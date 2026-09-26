from __future__ import annotations

import logging

from memory.fact_extractor import FactExtractor
from memory.knowledge_graph import KnowledgeGraph
from memory.oblivia.provenance import EXPLICIT_USER, UNKNOWN
from memory.oblivia.schemas import KnowledgeFact, MemoryRecord
from memory.semantic_query import SemanticQueryEngine

logger = logging.getLogger("memory.semantic")


class SemanticMemory:
    def __init__(self, adapter, registre=None) -> None:
        self.adapter = adapter
        self.extractor = FactExtractor()
        self.graph = KnowledgeGraph(adapter)
        self.query_engine = SemanticQueryEngine(adapter)
        # Registre des predicats, partage avec la voie du juge. Sans lui,
        # cette chaine inventait son propre vocabulaire dans son coin : les
        # predicats `velo` et `collegue` sont ainsi apparus dans le carnet
        # sans jamais figurer au registre, en parallele des `possede` et
        # `prenom` produits par le juge sur le meme message.
        self.registre = registre

    def remember(self, record: MemoryRecord) -> tuple[list[KnowledgeFact], int]:
        """Extraction deterministe, ecrite directement au carnet.

        Cette voie ne passe pas par le brouillon, et c'est voulu : elle ne
        se declenche que sur une affirmation de l'utilisateur, et elle
        n'utilise aucun modele de langage — c'est une lecture par regles de
        ce qui a ete ecrit. La preuve est le message lui-meme.

        La provenance depend donc de l'emetteur : ce que l'utilisateur
        affirme vaut preuve, ce qui vient d'ailleurs ne vaut rien tant que
        personne ne l'a confirme.
        """
        provenance = EXPLICIT_USER if record.source == "utilisateur" else UNKNOWN
        extracted = self.extractor.extract(record.content)
        facts = []
        for fact in extracted:
            predicat = self._canoniser(fact.relation, fact)
            if predicat is None:
                continue
            facts.append(KnowledgeFact(
                subject=fact.subject,
                predicate=predicat,
                object=fact.object,
                confidence=fact.confidence,
                origin_memory=record.id,
                provenance=provenance,
                metadata=fact.metadata,
            ))
        added = self.graph.add_facts(facts)
        return facts, added

    def _canoniser(self, predicat: str, exemple) -> str | None:
        """Ramene un predicat a la forme retenue par le registre.

        Sans registre (usage direct de SemanticMemory dans certains tests),
        le predicat passe tel quel : le comportement d'origine est conserve.
        """
        if self.registre is None:
            return predicat
        retenu, motif = self.registre.resoudre(
            predicat,
            exemple={"subject": exemple.subject, "object": exemple.object},
        )
        if retenu is None:
            logger.info("semantic_predicat_rejete predicat=%r motif=%r",
                        predicat, motif)
        return retenu

    def recall(self, query: str, limit: int = 10) -> dict:
        return self.query_engine.answer(query, limit=limit)
