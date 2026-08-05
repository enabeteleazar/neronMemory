from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timezone
import json
import logging
import re
from pathlib import Path
import time
from typing import Any, Literal

import os

import httpx
from fastapi import FastAPI, Query, Request
from pydantic import BaseModel, ConfigDict, Field, field_validator

from server.common.paths import NERON_SERVER_DIR, service_version
from server.common.service import create_service_app
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
from memory.oblivia.normalisation import VOCABULAIRE, normaliser
from memory.protocols import KnowledgeProvider, MemoryProvider


logger = logging.getLogger("memory.app")
logging.basicConfig(level=logging.INFO)
VERSION = service_version(__file__)
MEMORY_ROOT = NERON_SERVER_DIR / "memory"
SQLITE_PATH = MEMORY_ROOT / "neron_memory.db"
OBSIDIAN_PATH = MEMORY_ROOT / "obsidian"

NERON_LLM_URL = os.getenv("NERON_LLM_URL", "http://127.0.1.2:8765")
# serve.py exporte NERON_LLM_URL AVEC le suffixe /llm, mais le defaut
# ci-dessus ne l a pas : on normalise pour accepter les deux formes.
LLM_GENERATE_URL = NERON_LLM_URL.rstrip("/").removesuffix("/llm") + "/llm/generate"
NERON_API_KEY = os.getenv("NERON_API_KEY", "")
_observe_tasks: set[asyncio.Task] = set()
# Consigne v8, gagnante au banc du 03/08. La liste des predicats est
# construite depuis VOCABULAIRE : une seule source, jamais deux listes.
_OBSERVE_PROMPT_TEMPLATE = (
    "Extrais les informations durables de ce message.\n\n"
    "Beaucoup de messages ne contiennent AUCUNE information durable : ils "
    "parlent d une activite ponctuelle, d un projet du soir, d un rendez-vous. "
    "Dans ce cas la bonne reponse est une liste facts VIDE. C est une reponse "
    "correcte et attendue, pas un echec.\n\n"
    "Le predicat doit OBLIGATOIREMENT etre choisi dans cette liste :\n"
    + ", ".join(sorted(VOCABULAIRE)) + "\n\n"
    "Regles :\n"
    "- un objet JSON par information ATOMIQUE : le metier et la ville sont "
    "deux faits distincts\n"
    "- le sujet est utilisateur, ou le prenom de la personne concernee\n"
    "- l objet est une valeur courte, jamais une phrase\n"
    "- n invente rien qui ne soit pas ecrit dans le message\n\n"
    "Reponds en JSON, cle facts, liste d objets ayant les cles subject, "
    "predicate, object.\n\n"
    "Message : {text}"
)


# Voie rapide : un ordre explicite de memorisation vaut corroboration
# immediate. Le fait passe quand meme par le brouillon (tracabilite), mais
# avec assez de points pour etre promu dans la foulee.
_ORDRE_EXPLICITE = re.compile(
    r"\b(retiens|retenir|souviens[- ]toi|rappelle[- ]toi|memorise|"
    r"note que|n[' ]oublie pas)\b",
    re.IGNORECASE,
)


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


# Vocabulaire ferme de provenance. Toute valeur hors liste est rejetee par
# l'API : la provenance est posee par le code appelant, jamais deduite.
SourceProvenance = Literal["utilisateur", "agent", "externe", "systeme", "inconnu"]


class ObserveRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    text: str = Field(min_length=1)
    source: SourceProvenance = "inconnu"


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

    async def observe(self, text: str, source: str = "inconnu") -> dict[str, Any]:
        """Oblivia recoit un texte brut de conversation. Le texte est d'abord
        ecrit TEL QUEL et de maniere SYNCHRONE dans memory_records : aucun
        message ne doit pouvoir etre perdu, meme si le juge echoue ou
        n'extrait rien. Le jugement LLM part ensuite en tache de fond
        (cf. ADR-0001)."""
        brut = MemoryRecord(
            content=text,
            source=source,
            category="brut",
            metadata={"origine": "observe"},
        )
        await asyncio.to_thread(self.oblivia.sqlite.save_record, brut)
        logger.info("oblivia_observe_brut_saved id=%s source=%s", brut.id, source)
        explicite = source == "utilisateur" and bool(_ORDRE_EXPLICITE.search(text))
        if explicite:
            logger.info("oblivia_voie_rapide id=%s", brut.id)
        task = asyncio.create_task(
            self._observe_background(text, brut.id, explicite)
        )
        _observe_tasks.add(task)
        task.add_done_callback(_observe_tasks.discard)
        return {"observed": True, "status": "scheduled", "record_id": brut.id}

    async def _observe_background(
        self, text: str, origin_id: str | None = None, explicite: bool = False
    ) -> None:
        logger.debug("DEBUG_observe_background_started text=%r", text)
        prompt = _OBSERVE_PROMPT_TEMPLATE.format(text=text)
        headers = {"Authorization": f"Bearer {NERON_API_KEY}"} if NERON_API_KEY else {}
        try:
            async with httpx.AsyncClient(timeout=900.0) as client:
                response = await client.post(
                    LLM_GENERATE_URL,
                    json={
                        "task_type": "memory",
                        "json_mode": True,
                        "prompt": prompt,
                        "context": {},
                        "model_preference": "auto",
                    },
                    headers=headers,
                )
            response.raise_for_status()
            data = response.json()
            logger.debug("DEBUG_observe_llm_call_succeeded")
        except Exception:
            logger.warning("oblivia_observe_judge_failed", exc_info=True)
            return

        raw_text = str(data.get("result") or "").strip()
        logger.info("oblivia_observe_judge_result text=%r", raw_text)

        try:
            bruts = json.loads(raw_text).get("facts", [])
        except Exception:
            logger.warning("oblivia_observe_json_invalide", exc_info=True)
            return
        if not isinstance(bruts, list):
            logger.warning("oblivia_observe_facts_non_liste")
            return
        bruts = bruts[:50]

        retenus, rejets = normaliser(bruts)
        for triplet, motif in rejets:
            logger.info("oblivia_rejet motif=%r triplet=%r", motif, triplet)

        maintenant = datetime.now(timezone.utc).isoformat()
        cle = origin_id or maintenant
        points = 2.0 if explicite else 1.0
        for f in retenus:
            etat = await asyncio.to_thread(
                self.oblivia.sqlite.add_candidate,
                f["subject"], f["predicate"], f["object"], cle, points, maintenant,
            )
            logger.info(
                "oblivia_candidat %s | %s | %s -> %s point(s)%s",
                f["subject"], f["predicate"], f["object"], etat["points"],
                " (deja compte)" if etat["deja_compte"] else "",
            )
        promus = await asyncio.to_thread(
            self.oblivia.sqlite.promote_candidates, 2.0, maintenant
        )
        for p in promus:
            logger.info(
                "oblivia_promotion %s | %s | %s (%s points)",
                p["subject"], p["predicate"], p["object"], p["points"],
            )
        logger.info(
            "oblivia_observe_done retenus=%d rejetes=%d promus=%d",
            len(retenus), len(rejets), len(promus),
        )


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


@asynccontextmanager
async def _setup(app: FastAPI):
    app.state.memory_service = create_memory_service()
    app.state.knowledge_service = KnowledgeService(create_knowledge_provider())
    logger.info("Memory daemon started")
    try:
        yield
    finally:
        logger.info("Memory daemon stopped")


async def _health_details(request: Request) -> dict[str, Any]:
    status = await _service(request).status()
    return {"status": "healthy" if status["ok"] else "degraded"}


app = create_service_app(
    name="memory",
    title="NéronOS Memory",
    version=VERSION,
    capabilities=["memory", "sqlite", "obsidian", "context_storage"],
    setup=_setup,
    health=_health_details,
)


def _service(request: Request) -> MemoryService:
    return request.app.state.memory_service


def _knowledge(request: Request) -> KnowledgeService:
    return request.app.state.knowledge_service


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


@app.post("/memory/observe")
async def observe(request: Request, payload: ObserveRequest) -> dict[str, Any]:
    return await _service(request).observe(payload.text, payload.source)


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
