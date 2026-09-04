"""Persistance : ce que Neron memorise doit se relire a l'identique.

Ces tests couvrent la couche la plus critique de memory — celle ou une
regression fait perdre des donnees sans bruit. Aucun n'existait avant.
"""

from __future__ import annotations

import sqlite3

import pytest

from memory.oblivia.schemas import KnowledgeFact, MemoryRecord


class TestRecords:
    def test_saved_record_is_read_back_identically(self, adapter):
        record = MemoryRecord(content="Le chat dort.", source="test", category="fait")

        adapter.save_record(record)
        reread = adapter.get_record(record.id)

        assert reread is not None
        assert reread["content"] == "Le chat dort."
        assert reread["source"] == "test"

    def test_metadata_survives_a_round_trip(self, adapter):
        """Les metadonnees transitent en JSON : c'est la ou l'on perd des
        donnees silencieusement si la serialisation derape.

        Elles sont restituees par `list_records` (MemoryRecord complet), pas
        par `get_record`, qui ne rend que le message brut d'origine.
        """
        record = MemoryRecord(
            content="Avec metadonnees.",
            metadata={"facts": [{"a": 1}], "nested": {"x": ["y", 2]}},
        )

        adapter.save_record(record)
        reread = adapter.list_records(limit=1)[0]

        assert reread.metadata["nested"] == {"x": ["y", 2]}
        assert reread.metadata["facts"] == [{"a": 1}]

    def test_get_record_returns_the_raw_message_only(self, adapter):
        """Contrat explicite de `get_record` : le message d'origine, sans les
        metadonnees ajoutees ensuite par l'extraction de faits."""
        record = MemoryRecord(content="Message brut.", metadata={"facts": [{"a": 1}]})
        adapter.save_record(record)

        reread = adapter.get_record(record.id)

        assert reread["content"] == "Message brut."
        assert "metadata" not in reread

    def test_unknown_record_returns_none_rather_than_raising(self, adapter):
        assert adapter.get_record("identifiant-inexistant") is None

    def test_records_are_listed_most_recent_first(self, adapter):
        for content in ("premier", "deuxieme", "troisieme"):
            adapter.save_record(MemoryRecord(content=content))

        listed = adapter.list_records(limit=10)

        assert len(listed) == 3
        assert listed[0].content == "troisieme"

    def test_search_finds_a_record_by_its_content(self, adapter):
        adapter.save_record(MemoryRecord(content="J'aime le cafe noir."))
        adapter.save_record(MemoryRecord(content="Le train part a huit heures."))

        found = adapter.search_records("cafe", limit=10)

        assert len(found) == 1
        assert "cafe" in found[0].content

    def test_search_without_match_returns_empty_list(self, adapter):
        adapter.save_record(MemoryRecord(content="Un contenu quelconque."))

        assert adapter.search_records("introuvable", limit=10) == []


class TestFacts:
    def test_added_fact_is_listed(self, adapter):
        adapter.add_fact(KnowledgeFact(subject="user", predicate="likes", object="le cafe"))

        facts = adapter.list_facts(limit=10)

        assert any(f.object == "le cafe" for f in facts)

    def test_current_fact_is_retrieved_by_subject_and_predicate(self, adapter):
        adapter.add_fact(KnowledgeFact(subject="user", predicate="prenom", object="Eleazar"))

        fact = adapter.current_fact("user", "prenom")

        assert fact is not None
        assert fact.object == "Eleazar"

    def test_retracted_fact_is_no_longer_current(self, adapter):
        adapter.add_fact(KnowledgeFact(subject="user", predicate="habite_a", object="Paris"))

        retracted = adapter.retract_fact("user", "habite_a", "Paris")

        assert retracted is True
        assert adapter.current_fact("user", "habite_a") is None

    def test_retracting_an_absent_fact_reports_failure(self, adapter):
        assert adapter.retract_fact("user", "inconnu", "rien") is False


class TestForget:
    """`forget` porte sur les FAITS de connaissance, pas sur les messages.

    Les messages bruts restent : ils sont la trace de ce qui a ete dit. Ce
    que Neron « oublie », c'est ce qu'il en avait deduit.
    """

    def test_forget_removes_the_matching_fact(self, adapter):
        adapter.add_fact(KnowledgeFact(subject="user", predicate="likes", object="le cafe"))
        adapter.add_fact(KnowledgeFact(subject="user", predicate="habite_a", object="Reims"))

        removed = adapter.forget("cafe")

        assert removed == 1
        assert adapter.current_fact("user", "likes") is None
        assert adapter.current_fact("user", "habite_a") is not None

    def test_forget_leaves_the_original_messages_intact(self, adapter):
        adapter.save_record(MemoryRecord(content="J'aime le cafe."))
        adapter.add_fact(KnowledgeFact(subject="user", predicate="likes", object="le cafe"))

        adapter.forget("cafe")

        assert len(adapter.list_records(limit=10)) == 1

    def test_forget_without_match_removes_nothing(self, adapter):
        adapter.add_fact(KnowledgeFact(subject="user", predicate="likes", object="le the"))

        assert adapter.forget("introuvable") == 0
        assert adapter.current_fact("user", "likes") is not None


class TestStatus:
    def test_status_counts_records_and_facts(self, adapter):
        adapter.save_record(MemoryRecord(content="un souvenir"))
        adapter.add_fact(KnowledgeFact(subject="user", predicate="likes", object="x"))

        status = adapter.status()

        assert status["records"] == 1
        assert status["facts"] == 1

    def test_status_on_an_empty_database(self, adapter):
        status = adapter.status()

        assert status["records"] == 0
        assert status["facts"] == 0


class TestSchema:
    def test_schema_is_created_on_a_fresh_database(self, db_path):
        from memory.oblivia.sqlite_adapter import SQLiteMemoryAdapter

        SQLiteMemoryAdapter(db_path)

        conn = sqlite3.connect(db_path)
        tables = {row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )}
        conn.close()

        assert {"memory_records", "knowledge_facts", "fact_candidates"} <= tables

    def test_reopening_an_existing_database_preserves_data(self, db_path):
        """Le redemarrage du service ne doit rien perdre."""
        from memory.oblivia.sqlite_adapter import SQLiteMemoryAdapter

        first = SQLiteMemoryAdapter(db_path)
        first.save_record(MemoryRecord(content="doit survivre au redemarrage"))

        second = SQLiteMemoryAdapter(db_path)

        assert second.status()["records"] == 1
