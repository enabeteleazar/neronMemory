from __future__ import annotations

from .schemas import (
    MemoryQuery,
    MemoryRecord,
    MemorySearchResult,
    MemoryStatus,
)
from .sqlite_adapter import SQLiteMemoryAdapter
from .obsidian_adapter import ObsidianMemoryAdapter
from .semantic.semantic_search import ObsidianSemanticSearch
from .text_utils import normalize_text
from .knowledge import KnowledgeExtractor, natural_answer
from .reasoner import MemoryReasoner
from core.modules.self_model.integration import emit_memory_event


class ObliviaMemoryManager:
    def __init__(
        self,
        sqlite_path: str | None = None,
        obsidian_path: str | None = None,
    ):
        self.sqlite = (
            SQLiteMemoryAdapter(sqlite_path)
            if sqlite_path
            else SQLiteMemoryAdapter()
        )
        self.obsidian = (
            ObsidianMemoryAdapter(obsidian_path)
            if obsidian_path
            else ObsidianMemoryAdapter()
        )
        self.semantic = ObsidianSemanticSearch(str(self.obsidian.vault))
        self.knowledge = KnowledgeExtractor()
        self.reasoner = MemoryReasoner(self.sqlite)

    def remember(self, record: MemoryRecord) -> MemoryRecord:
        facts = self.knowledge.extract(
            record.content,
            source=str(record.metadata.get("source") or "user"),
        )
        saved_facts = [self.sqlite.upsert_fact(fact) for fact in facts]
        if saved_facts:
            record.metadata["facts"] = [
                fact.model_dump(mode="json") for fact in saved_facts
            ]
        temporal_noop = bool(saved_facts) and all(
            fact.metadata.get("temporal_noop") for fact in saved_facts
        )
        if saved_facts:
            operation = saved_facts[0].metadata.get("temporal_operation")
            place = saved_facts[0].object
            predicate = saved_facts[0].predicate
            if operation == "historical_assertion" and predicate == "lives_at":
                record.metadata["natural_response"] = (
                    f"C’est noté : tu as habité à {place}."
                )
            elif operation == "retraction" and predicate == "lives_at":
                record.metadata["natural_response"] = (
                    f"C’est corrigé : {place} est retiré de ton "
                    "historique de résidence."
                )
            elif operation == "retraction" and predicate == "likes":
                record.metadata["natural_response"] = (
                    f"C’est noté : tu n’aimes plus {place}."
                )
            elif saved_facts[0].conflict:
                record.metadata["natural_response"] = (
                    "Cette information entre en conflit avec une "
                    "connaissance immutable existante ; elle est conservée "
                    "pour audit sans remplacer la valeur actuelle."
                )
        if not temporal_noop:
            self.sqlite.add(record)

        if record.category in {
            "self",
            "project",
            "decision",
            "lesson",
            "agent",
        }:
            self.obsidian.add(record)

        emit_memory_event(
            "memory.remembered",
            {
                "operation": "remember",
                "provider": "oblivia",
                "status": "completed",
                "record_id": record.id,
            },
        )
        return record

    def recall_knowledge(self, question: str, limit: int = 10) -> dict:
        reasoned = self.reasoner.answer(question)
        if reasoned is not None:
            return reasoned
        facts = self.sqlite.search_facts(question, limit=limit)
        return {
            "answer": natural_answer(question, facts),
            "facts": [fact.model_dump(mode="json") for fact in facts],
        }

    def forget(self, query: str) -> dict[str, int]:
        result = {"forgotten": self.sqlite.forget_facts(query)}
        emit_memory_event(
            "memory.deleted",
            {
                "operation": "forget",
                "provider": "oblivia",
                "status": "completed",
                "record_id": None,
            },
        )
        return result

    def recent(self, limit: int = 10) -> list[MemorySearchResult]:
        return [
            MemorySearchResult(
                record=MemoryRecord(
                    id=row[0],
                    source=row[1],
                    category=row[2],
                    content=row[3],
                ),
                backend="sqlite",
                score=1.0,
            )
            for row in self.sqlite.recent(limit)
        ]

    def cleanup(self, days: int) -> dict[str, int]:
        return {"deleted": self.sqlite.cleanup(days)}

    def search(self, query: str, limit: int = 10):
        results = []
        normalized_query = normalize_text(query)

        for row in self.sqlite.search(normalized_query, limit):
            record = MemoryRecord(
                id=row[0],
                source=row[1],
                category=row[2],
                content=row[3],
            )

            results.append(
                MemorySearchResult(
                    record=record,
                    backend="sqlite",
                    score=1.0,
                )
            )

        remaining = max(0, limit - len(results))

        if remaining > 0:
            for item in self.obsidian.search(normalized_query, remaining):
                record = MemoryRecord(
                    source="obsidian",
                    category="project",
                    content=item["content"],
                    metadata={"path": item["path"]},
                )

                results.append(
                    MemorySearchResult(
                        record=record,
                        backend="obsidian",
                        score=item["score"],
                    )
                )

        existing_paths = {
            item.record.metadata.get("path")
            for item in results
            if isinstance(item.record.metadata, dict)
        }

        try:
            for item in self.semantic.search(normalized_query, limit=limit):
                path = item.get("path")
                if path in existing_paths:
                    continue

                record = MemoryRecord(
                    source="obsidian",
                    category="project",
                    content=item.get("preview", ""),
                    metadata={
                        "path": path,
                        "title": item.get("title"),
                        "folder": item.get("folder"),
                        "search": "semantic",
                    },
                )

                results.append(
                    MemorySearchResult(
                        record=record,
                        backend="obsidian_semantic",
                        score=float(item.get("score") or 0.0),
                    )
                )
        except Exception:
            pass

        sorted_results = sorted(
            results,
            key=lambda item: item.score,
            reverse=True,
        )
        emit_memory_event(
            "memory.search",
            {
                "operation": "search",
                "provider": "oblivia",
                "status": "completed",
                "record_id": None,
            },
        )
        return sorted_results

    def recall(self, query: MemoryQuery):
        return self.search(query.query, query.limit)

    def status(self):
        return MemoryStatus(
            ok=True,
            sqlite=self.sqlite.status(),
            obsidian=self.obsidian.status(),
        )
