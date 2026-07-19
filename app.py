from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timezone
import logging
from pathlib import Path
import time
from typing import Any

from fastapi import FastAPI, Query, Request
from pydantic import BaseModel, ConfigDict, Field, field_validator

from common.paths import NERON_SERVER_DIR
from server.common.registry.client import RegistryClient
from memory.knowledge import (
    KnowledgeDocument,
    KnowledgeDocumentMeta,
    ObsidianKnowledgeProvider,
)
from memory.oblivia import (
    MemoryQuery,
    MemoryRecord,
)
from memory.oblivia.manager import ObliviaMemoryManager
from memory.protocols import KnowledgeProvider, MemoryProvider


logger = logging.getLogger("memory.app")
VERSION = "0.1.0"
MEMORY_ROOT = NERON_SERVER_DIR / "memory"
SQLITE_PATH = MEMORY_ROOT / "neron_memory.db"
OBSIDIAN_PATH = MEMORY_ROOT / "obsidian"


class RememberRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    content: str = Field(min_length=1)
    category: str = "unknown"
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("content")
    @classmethod
    def reject_blank_content(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("content must not be blank")
        return value


class RecallRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    query: str = Field(min_length=1)
    limit: int = Field(default=10, ge=1, le=100)


class ForgetRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    query: str = Field(min_length=1)


class MemoryService:
    """HTTP facade over the single Oblivia source of truth."""

    def __init__(self, sqlite_path: Path, obsidian_path: Path) -> None:
        self.oblivia = ObliviaMemoryManager(
            sqlite_path=str(sqlite_path),
            obsidian_path=str(obsidian_path),
        )
        assert isinstance(self.oblivia, MemoryProvider), (
            "ObliviaMemoryManager ne satisfait plus le protocole MemoryProvider "
            "(cf. server/memory/protocols.py) — vérifier les méthodes requises."
        )

    async def remember(self, request: RememberRequest) -> dict[str, Any]:
        record = MemoryRecord(
            content=request.content,
            category=request.category,
            metadata=request.metadata,
        )
        result = await asyncio.to_thread(self.oblivia.remember, record)
        return result.model_dump(mode="json")

    async def search(self, query: str, limit: int) -> list[dict[str, Any]]:
        results = await asyncio.to_thread(
            self.oblivia.recall, MemoryQuery(query=query, limit=limit)
        )
        return [result.model_dump(mode="json") for result in results]

    async def status(self) -> dict[str, Any]:
        result = await asyncio.to_thread(self.oblivia.status)
        return result.model_dump(mode="json")

    async def recall_knowledge(self, query: str, limit: int) -> dict[str, Any]:
        return await asyncio.to_thread(self.oblivia.recall_knowledge, query, limit)

    async def forget(self, query: str) -> dict[str, Any]:
        return await asyncio.to_thread(self.oblivia.forget, query)


def create_memory_service() -> MemoryService:
    return MemoryService(SQLITE_PATH, OBSIDIAN_PATH)


class KnowledgeService:
    """HTTP facade over a KnowledgeProvider (Obsidian aujourd'hui).

    Volontairement une classe distincte de MemoryService, jamais fusionnée :
    un document consulté n'est pas un souvenir personnel (cf.
    server/memory/protocols.py).
    """

    def __init__(self, provider: KnowledgeProvider) -> None:
        self.provider = provider

    async def query(self, text: str, limit: int) -> list[dict[str, Any]]:
        docs: list[KnowledgeDocument] = await asyncio.to_thread(
            self.provider.query, text, limit
        )
        return [doc.model_dump(mode="json") for doc in docs]

    async def list_documents(self) -> list[dict[str, Any]]:
        docs: list[KnowledgeDocumentMeta] = await asyncio.to_thread(
            self.provider.list_documents
        )
        return [doc.model_dump(mode="json") for doc in docs]

    async def status(self) -> dict[str, Any]:
        return await asyncio.to_thread(self.provider.status)


def create_knowledge_provider() -> KnowledgeProvider:
    provider = ObsidianKnowledgeProvider(OBSIDIAN_PATH)
    assert isinstance(provider, KnowledgeProvider), (
        "ObsidianKnowledgeProvider ne satisfait plus le protocole "
        "KnowledgeProvider (cf. server/memory/protocols.py)."
    )
    return provider


def create_registry_client() -> RegistryClient:
    return RegistryClient(
        service_name="memory",
        version=VERSION,
        # Défauts alignés sur neron.server.yaml (nodes.memory). Peuvent être
        # surchargés par NERON_SERVICE_HOST/PORT (cf. service_from_env) —
        # mais ne doivent pas en dépendre pour être corrects par eux-mêmes.
        host="127.0.1.4",
        port=8040,
        capabilities=["memory", "sqlite", "obsidian", "context_storage"],
        metadata={},
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.started_at = time.monotonic()
    app.state.memory_service = create_memory_service()
    app.state.knowledge_service = KnowledgeService(create_knowledge_provider())
    registry_client = create_registry_client()
    app.state.registry_client = registry_client
    await registry_client.start()
    logger.info("Memory daemon started on port 8040")
    try:
        yield
    finally:
        await registry_client.stop()
        logger.info("Memory daemon stopped")


app = FastAPI(
    title="NéronOS Memory",
    version=VERSION,
    lifespan=lifespan,
)


def _service(request: Request) -> MemoryService:
    return request.app.state.memory_service


def _knowledge(request: Request) -> KnowledgeService:
    return request.app.state.knowledge_service


@app.get("/health")
async def health(request: Request) -> dict[str, str]:
    status = await _service(request).status()
    return {
        "service": "memory",
        "status": "healthy" if status["ok"] else "degraded",
    }


@app.get("/status")
async def service_status(request: Request) -> dict[str, Any]:
    started_at = getattr(request.app.state, "started_at", time.monotonic())
    return {
        "service": "memory",
        "status": "running",
        "uptime": round(max(0.0, time.monotonic() - started_at), 3),
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "backends": await _service(request).status(),
    }


@app.post("/memory/remember")
async def remember(request: Request, payload: RememberRequest) -> dict[str, Any]:
    return {"memory": await _service(request).remember(payload)}


@app.post("/memory/recall")
async def recall(request: Request, payload: RecallRequest) -> dict[str, Any]:
    service = _service(request)
    results = await service.search(payload.query, payload.limit)
    knowledge = await service.recall_knowledge(payload.query, payload.limit)
    return {
        "count": len(results),
        "results": results,
        **knowledge,
    }


@app.post("/memory/forget")
async def forget(request: Request, payload: ForgetRequest) -> dict[str, Any]:
    return await _service(request).forget(payload.query)


@app.get("/memory/search")
async def search(
    request: Request,
    q: str = Query(min_length=1),
    limit: int = Query(default=10, ge=1, le=100),
) -> dict[str, Any]:
    results = await _service(request).search(q, limit)
    return {"count": len(results), "results": results}


# ── Knowledge Providers — distinct des souvenirs personnels ─────────────────
# Cf. server/memory/protocols.py : un document Obsidian consulté n'est pas
# un souvenir. Endpoints séparés de /memory/* volontairement, pour ne pas
# recréer le mélange qu'on cherche justement à éviter.

@app.get("/knowledge/health")
async def knowledge_health(request: Request) -> dict[str, Any]:
    status = await _knowledge(request).status()
    return {
        "service": "knowledge",
        "status": "healthy" if status.get("ok") else "degraded",
        **status,
    }


@app.get("/knowledge/query")
async def knowledge_query(
    request: Request,
    q: str = Query(min_length=1),
    limit: int = Query(default=10, ge=1, le=50),
) -> dict[str, Any]:
    results = await _knowledge(request).query(q, limit)
    return {"count": len(results), "results": results}


@app.get("/knowledge/documents")
async def knowledge_documents(request: Request) -> dict[str, Any]:
    docs = await _knowledge(request).list_documents()
    return {"count": len(docs), "documents": docs}
