from __future__ import annotations

from pathlib import Path

from memory.semantic_memory import SemanticMemory

from .schemas import MemoryQuery, MemoryRecord, MemorySearchResult, MemoryStatus
from .sqlite_adapter import SQLiteMemoryAdapter
from memory.text_utils import normalize_text


class ObliviaMemoryManager:
    def __init__(
        self,
        sqlite_path: str | None = None,
        obsidian_path: str | None = None,
    ) -> None:
        self.sqlite = SQLiteMemoryAdapter(sqlite_path or "memory/neron_memory.db")
        self.obsidian_path = Path(obsidian_path or "server/memory/obsidian")
        self.obsidian_path.mkdir(parents=True, exist_ok=True)
        self.semantic = SemanticMemory(self.sqlite)

    def remember(self, record: MemoryRecord) -> MemoryRecord:
        facts, added = self.semantic.remember(record)
        record.metadata = dict(record.metadata or {})
        record.metadata["facts"] = [fact.model_dump(mode="json") for fact in facts]
        record.metadata["semantic_facts_added"] = added
        record.metadata["natural_response"] = self._natural_remember_response(record, facts)
        return self.sqlite.save_record(record)

    def recall(self, query: MemoryQuery) -> list[MemorySearchResult]:
        return [
            MemorySearchResult(backend="sqlite", record=record, score=1.0)
            for record in self.sqlite.search_records(query.query, limit=query.limit)
        ]

    def recall_knowledge(self, query: str, limit: int = 10) -> dict:
        return self.semantic.recall(query, limit=limit)

    def search(self, query: str, limit: int = 10) -> list[MemorySearchResult]:
        records = self.sqlite.search_records(query, limit=limit)
        if not records:
            needle = normalize_text(query)
            records = [
                record
                for record in self.sqlite.list_records(1000)
                if any(part in normalize_text(record.content) for part in needle.split())
            ][:limit]
        return [MemorySearchResult(backend="sqlite", record=record, score=1.0) for record in records]

    def forget(self, query: str) -> dict[str, int]:
        return {"forgotten": self.sqlite.forget(query)}

    def recent(self, limit: int = 10) -> list[MemoryRecord]:
        return self.sqlite.list_records(limit)

    def cleanup(self, days: int) -> dict[str, int]:
        del days
        return {"deleted": 0}

    def status(self) -> MemoryStatus:
        try:
            status = self.sqlite.status()
        except Exception as exc:
            return MemoryStatus(ok=False, records=0, facts=0, error=str(exc))
        return MemoryStatus(ok=True, records=status["records"], facts=status["facts"])

    def _natural_remember_response(self, record: MemoryRecord, facts) -> str:
        if not facts:
            return f"C’est mémorisé : {record.content}"
        fact = facts[0]
        if fact.metadata.get("retract") and fact.predicate == "lives_at":
            return f"C’est corrigé : {fact.object} est retiré de ton historique de résidence."
        if fact.predicate == "lives_at" and fact.metadata.get("historical"):
            return f"C’est noté : tu as habité à {fact.object}."
        if fact.predicate == "creator":
            return f"C’est noté : mon créateur est {fact.object}."
        if fact.predicate == "works_at":
            return f"C’est noté : tu travailles chez {fact.object}."
        return f"C’est noté : {record.content.rstrip(' .')}."
