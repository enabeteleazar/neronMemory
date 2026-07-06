from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, Field

MemorySource = Literal["sqlite", "obsidian", "memory_manager"]

MemoryCategory = Literal[
    "self",
    "project",
    "agent",
    "goal",
    "decision",
    "lesson",
    "runtime",
    "unknown",
]


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


class MemoryRecord(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    source: MemorySource = "memory_manager"
    category: MemoryCategory = "unknown"
    content: str
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: str = Field(default_factory=now_utc)
    updated_at: str = Field(default_factory=now_utc)


class KnowledgeFact(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    subject: str = Field(min_length=1)
    predicate: str = Field(min_length=1)
    object: str = Field(min_length=1)
    fact_type: Literal["semantic", "episodic", "procedural"] = "semantic"
    source: str = "user"
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    raw_text: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: str = Field(default_factory=now_utc)
    updated_at: str = Field(default_factory=now_utc)


class TemporalLivesAtFact(KnowledgeFact):
    """Temporal model reserved exclusively for the ``lives_at`` spike."""

    predicate: Literal["lives_at"] = "lives_at"
    valid_from: str
    valid_to: str | None = None
    is_current: bool = True
    retracted: bool = False
    retracted_at: str | None = None
    retraction_reason: str | None = None


class LifecycleKnowledgeFact(KnowledgeFact):
    valid_from: str
    valid_to: str | None = None
    is_current: bool = True
    retracted: bool = False
    retracted_at: str | None = None
    retraction_reason: str | None = None
    lifecycle: Literal[
        "immutable",
        "replace",
        "accumulate",
        "preference",
        "event",
    ]
    conflict: bool = False
    conflict_reason: str | None = None


class MemoryQuery(BaseModel):
    query: str
    category: MemoryCategory | None = None
    limit: int = 10


class MemorySearchResult(BaseModel):
    record: MemoryRecord
    score: float = 1.0
    backend: str


class MemoryStatus(BaseModel):
    ok: bool
    sqlite: dict[str, Any]
    obsidian: dict[str, Any]
