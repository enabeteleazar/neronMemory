from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from memory.dumpable import Dumpable


def now_iso() -> str:
    return datetime.now(UTC).isoformat()


@dataclass
class MemoryRecord(Dumpable):
    content: str
    source: str = "memory_manager"
    category: str = "unknown"
    metadata: dict[str, Any] = field(default_factory=dict)
    id: str = field(default_factory=lambda: str(uuid4()))
    created_at: str = field(default_factory=now_iso)


@dataclass
class MemoryQuery(Dumpable):
    query: str
    category: str | None = None
    limit: int = 10


@dataclass
class KnowledgeFact(Dumpable):
    subject: str
    predicate: str
    object: str
    confidence: float = 1.0
    origin_memory: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    id: int | None = None
    created_at: str = field(default_factory=now_iso)
    valid_from: str | None = None
    valid_to: str | None = None
    is_current: bool = True
    retracted: bool = False
    retracted_at: str | None = None
    retraction_reason: str | None = None


@dataclass
class MemorySearchResult(Dumpable):
    backend: str
    record: MemoryRecord
    score: float = 1.0


@dataclass
class MemoryStatus(Dumpable):
    ok: bool
    records: int
    facts: int = 0
    error: str | None = None
