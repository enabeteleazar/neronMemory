from __future__ import annotations

from memory.fact_extractor import FactExtractor
from memory.knowledge_graph import KnowledgeGraph
from memory.oblivia.schemas import KnowledgeFact, MemoryRecord
from memory.semantic_query import SemanticQueryEngine


class SemanticMemory:
    def __init__(self, adapter) -> None:
        self.adapter = adapter
        self.extractor = FactExtractor()
        self.graph = KnowledgeGraph(adapter)
        self.query_engine = SemanticQueryEngine(adapter)

    def remember(self, record: MemoryRecord) -> tuple[list[KnowledgeFact], int]:
        extracted = self.extractor.extract(record.content)
        facts = [
            KnowledgeFact(
                subject=fact.subject,
                predicate=fact.relation,
                object=fact.object,
                confidence=fact.confidence,
                origin_memory=record.id,
                metadata=fact.metadata,
            )
            for fact in extracted
        ]
        added = self.graph.add_facts(facts)
        return facts, added

    def recall(self, query: str, limit: int = 10) -> dict:
        return self.query_engine.answer(query, limit=limit)
