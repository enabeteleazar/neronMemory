from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from memory.oblivia.schemas import KnowledgeFact


@dataclass
class GraphNode:
    id: str
    type: str
    label: str
    confidence: float
    timestamp: str


@dataclass
class GraphRelation:
    source: str
    target: str
    relation: str
    confidence: float
    origin_memory: str | None


class KnowledgeGraph:
    def __init__(self, adapter) -> None:
        self.adapter = adapter

    def add_facts(self, facts: Iterable[KnowledgeFact]) -> int:
        count = 0
        for fact in facts:
            count += int(self.adapter.add_fact(fact))
        return count

    def query(self, subject: str | None = None, relation: str | None = None) -> list[KnowledgeFact]:
        return self.adapter.list_facts(subject=subject, predicate=relation)
