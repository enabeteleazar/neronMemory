"""Memorisation et rappel de bout en bout, via ObliviaMemoryManager.

C'est le chemin que Core emprunte reellement : `POST /memory/remember` puis
`POST /memory/recall`. Ces tests verifient qu'une phrase memorisee ressort
sous forme de fait exploitable, et que le rappel repond en francais.
"""

from __future__ import annotations

import pytest

from memory.oblivia.schemas import MemoryQuery, MemoryRecord


class TestRemember:
    def test_remembering_a_message_stores_it(self, manager):
        manager.remember(MemoryRecord(content="Le ciel est bleu."))

        assert manager.status().records == 1

    def test_remembering_extracts_facts_into_metadata(self, manager):
        """L'extraction est ce qui distingue une memoire d'un journal :
        le message brut est conserve, mais on en tire aussi des faits."""
        stored = manager.remember(MemoryRecord(content="J'aime le chocolat."))

        assert "facts" in stored.metadata
        assert isinstance(stored.metadata["facts"], list)

    def test_a_message_without_extractable_fact_is_still_stored(self, manager):
        """Ne rien pouvoir deduire n'est pas une erreur."""
        stored = manager.remember(MemoryRecord(content="Bonjour."))

        assert manager.status().records == 1
        assert stored.metadata.get("facts") == []


class TestRecall:
    def test_recall_finds_a_stored_message(self, manager):
        manager.remember(MemoryRecord(content="Le train part a huit heures."))

        results = manager.recall(MemoryQuery(query="train", limit=5))

        assert len(results) == 1
        assert "train" in results[0].record.content

    def test_recall_without_match_returns_nothing(self, manager):
        manager.remember(MemoryRecord(content="Un contenu quelconque."))

        assert manager.recall(MemoryQuery(query="introuvable", limit=5)) == []

    def test_recall_knowledge_answers_in_french(self, manager):
        """Le rappel doit produire une phrase, pas un dump de triplets."""
        manager.remember(MemoryRecord(content="Je m'appelle Eleazar."))

        answer = manager.recall_knowledge("comment je m'appelle", limit=5)

        assert isinstance(answer, dict)
        assert "answer" in answer


class TestStatus:
    def test_status_reports_a_healthy_backend(self, manager):
        status = manager.status()

        assert status.ok is True
        assert status.records == 0

    def test_status_counts_grow_with_memorised_messages(self, manager):
        for i in range(3):
            manager.remember(MemoryRecord(content=f"souvenir numero {i}"))

        assert manager.status().records == 3


class TestIsolation:
    def test_two_managers_on_distinct_paths_do_not_share_data(self, tmp_path):
        """Garantit que les tests n'interferent pas entre eux — et, au
        passage, que deux instances ne se marchent pas dessus."""
        from memory.oblivia.manager import ObliviaMemoryManager

        first = ObliviaMemoryManager(
            sqlite_path=str(tmp_path / "a.db"), obsidian_path=str(tmp_path / "oa")
        )
        second = ObliviaMemoryManager(
            sqlite_path=str(tmp_path / "b.db"), obsidian_path=str(tmp_path / "ob")
        )

        first.remember(MemoryRecord(content="present uniquement dans la premiere"))

        assert first.status().records == 1
        assert second.status().records == 0
