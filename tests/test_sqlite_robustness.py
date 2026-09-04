"""Garanties de robustesse de la couche SQLite.

Trois defauts constates le 04/09/2026 sur la base reelle :

  - `journal_mode: delete` — une ecriture bloquait toutes les lectures, sur
    un service HTTP asynchrone pouvant traiter plusieurs requetes a la fois ;
  - 19 ouvertures de connexion pour 0 `close()` — `with sqlite3.connect(...)`
    gere la transaction, pas la fermeture ;
  - 0 index : `EXPLAIN QUERY PLAN` annoncait `SCAN` sur chaque recherche.

Ces tests verrouillent les corrections.
"""

from __future__ import annotations

import sqlite3
import threading

import pytest

from memory.oblivia.schemas import KnowledgeFact, MemoryRecord
from memory.oblivia.sqlite_adapter import SQLiteMemoryAdapter


class TestConnectionHandling:
    def test_connections_are_closed_after_use(self, adapter):
        """Une connexion laissee ouverte retient un descripteur de fichier."""
        leaked = []
        real_connect = sqlite3.connect

        def tracking_connect(*args, **kwargs):
            conn = real_connect(*args, **kwargs)
            leaked.append(conn)
            return conn

        with pytest.MonkeyPatch().context() as mp:
            mp.setattr(sqlite3, "connect", tracking_connect)
            adapter.save_record(MemoryRecord(content="un souvenir"))

        assert leaked, "le test doit avoir observe au moins une connexion"
        for conn in leaked:
            with pytest.raises(sqlite3.ProgrammingError):
                conn.execute("SELECT 1")

    def test_a_failing_operation_still_closes_its_connection(self, adapter):
        """Meme sur exception, la connexion doit etre liberee."""
        leaked = []
        real_connect = sqlite3.connect

        def tracking_connect(*args, **kwargs):
            conn = real_connect(*args, **kwargs)
            leaked.append(conn)
            return conn

        with pytest.MonkeyPatch().context() as mp:
            mp.setattr(sqlite3, "connect", tracking_connect)
            with pytest.raises(sqlite3.Error):
                with adapter._connect() as conn:
                    conn.execute("SELECT * FROM table_inexistante")

        for conn in leaked:
            with pytest.raises(sqlite3.ProgrammingError):
                conn.execute("SELECT 1")


class TestWalMode:
    def test_database_uses_write_ahead_logging(self, adapter):
        adapter.save_record(MemoryRecord(content="declenche une ecriture"))

        conn = sqlite3.connect(adapter.path)
        mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        conn.close()

        assert mode.lower() == "wal"

    def test_reading_while_writing_does_not_block(self, adapter):
        """Le benefice concret de WAL : une lecture pendant une ecriture."""
        adapter.save_record(MemoryRecord(content="donnee initiale"))
        erreurs: list[Exception] = []

        def lire():
            try:
                for _ in range(20):
                    adapter.list_records(limit=5)
            except Exception as exc:  # pragma: no cover - trace en cas d'echec
                erreurs.append(exc)

        lecteur = threading.Thread(target=lire)
        lecteur.start()
        for i in range(20):
            adapter.save_record(MemoryRecord(content=f"ecriture {i}"))
        lecteur.join(timeout=30)

        assert not erreurs, f"lecture concurrente en echec : {erreurs}"
        assert adapter.status()["records"] == 21


class TestIndexes:
    def test_expected_indexes_exist(self, adapter):
        conn = sqlite3.connect(adapter.path)
        names = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='index' AND sql IS NOT NULL"
            )
        }
        conn.close()

        assert "idx_records_created_at" in names
        assert "idx_facts_subject_predicate" in names

    def test_fact_lookup_uses_an_index_rather_than_a_full_scan(self, adapter):
        adapter.add_fact(KnowledgeFact(subject="user", predicate="prenom", object="Eleazar"))

        conn = sqlite3.connect(adapter.path)
        plan = " ".join(
            str(row[-1])
            for row in conn.execute(
                "EXPLAIN QUERY PLAN "
                "SELECT * FROM knowledge_facts WHERE subject=? AND predicate=?",
                ("user", "prenom"),
            )
        )
        conn.close()

        assert "SCAN" not in plan.upper(), f"balayage complet subsiste : {plan}"


class TestLockTimeout:
    def test_lock_timeout_is_longer_than_the_sqlite_default(self):
        """5 s (defaut) est trop court des que deux ecritures se croisent."""
        assert SQLiteMemoryAdapter.LOCK_TIMEOUT_SECONDS > 5.0
