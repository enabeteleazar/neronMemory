from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from .schemas import KnowledgeFact, MemoryRecord, now_iso
from memory.text_utils import normalize_text


class SQLiteMemoryAdapter:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()
        self._migrate()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_schema(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS memory_records (
                    id TEXT PRIMARY KEY,
                    source TEXT NOT NULL,
                    category TEXT NOT NULL,
                    content TEXT NOT NULL,
                    metadata TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS knowledge_facts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    subject TEXT NOT NULL,
                    predicate TEXT NOT NULL,
                    object TEXT NOT NULL,
                    confidence REAL NOT NULL,
                    origin_memory TEXT,
                    metadata TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    valid_from TEXT,
                    valid_to TEXT,
                    is_current INTEGER NOT NULL DEFAULT 1,
                    retracted INTEGER NOT NULL DEFAULT 0,
                    retracted_at TEXT,
                    retraction_reason TEXT
                );
                -- Brouillon : triplets en attente de corroboration.
                -- Rien n'entre dans knowledge_facts avant d'avoir atteint
                -- le seuil de points (1 par message, 0.5 par relecture).
                CREATE TABLE IF NOT EXISTS fact_candidates (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    subject TEXT NOT NULL,
                    predicate TEXT NOT NULL,
                    object TEXT NOT NULL,
                    confidence REAL NOT NULL DEFAULT 1.0,
                    points REAL NOT NULL DEFAULT 0,
                    message_count INTEGER NOT NULL DEFAULT 0,
                    origin_memories TEXT NOT NULL DEFAULT '[]',
                    first_seen TEXT NOT NULL,
                    last_seen TEXT NOT NULL,
                    promoted_at TEXT,
                    promoted_fact_id INTEGER,
                    UNIQUE(subject, predicate, object)
                );
                -- Journal des relectures : une ligne par passe sur un message.
                CREATE TABLE IF NOT EXISTS reread_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    record_id TEXT NOT NULL,
                    passe INTEGER NOT NULL,
                    done_at TEXT NOT NULL,
                    UNIQUE(record_id, passe)
                );
                CREATE TABLE IF NOT EXISTS semantic_nodes (
                    id TEXT PRIMARY KEY,
                    type TEXT NOT NULL,
                    label TEXT NOT NULL,
                    confidence REAL NOT NULL,
                    timestamp TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS semantic_relations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    source TEXT NOT NULL,
                    target TEXT NOT NULL,
                    relation TEXT NOT NULL,
                    confidence REAL NOT NULL,
                    origin_memory TEXT,
                    timestamp TEXT NOT NULL
                );
                """
            )

    def _migrate(self) -> None:
        with self._connect() as conn:
            colonnes = {r[1] for r in conn.execute(
                "PRAGMA table_info(fact_candidates)"
            )}
            if "rejected_at" not in colonnes:
                conn.execute(
                    "ALTER TABLE fact_candidates ADD COLUMN rejected_at TEXT"
                )

    def reject_candidate(self, candidate_id: int, timestamp: str) -> bool:
        """Ecarte definitivement un candidat : il ne sera jamais promu."""
        with self._connect() as conn:
            cur = conn.execute(
                "UPDATE fact_candidates SET rejected_at=? "
                "WHERE id=? AND promoted_at IS NULL",
                (timestamp, candidate_id),
            )
            return cur.rowcount > 0

    def save_record(self, record: MemoryRecord) -> MemoryRecord:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO memory_records
                (id, source, category, content, metadata, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    record.id,
                    record.source,
                    record.category,
                    record.content,
                    json.dumps(record.metadata, ensure_ascii=False),
                    record.created_at,
                ),
            )
        return record

    def add_candidate(
        self,
        subject: str,
        predicate: str,
        obj: str,
        origin_key: str,
        points: float,
        timestamp: str,
    ) -> dict[str, Any]:
        """Ajoute ou renforce un triplet dans le brouillon.

        Un meme message ne peut JAMAIS compter deux fois : origin_key est
        conserve dans origin_memories, et rejoue sans aucun effet.
        """
        with self._connect() as conn:
            row = conn.execute(
                "SELECT id, points, origin_memories FROM fact_candidates "
                "WHERE subject=? AND predicate=? AND object=?",
                (subject, predicate, obj),
            ).fetchone()
            if row is None:
                conn.execute(
                    "INSERT INTO fact_candidates (subject, predicate, object, "
                    "points, message_count, origin_memories, first_seen, last_seen) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (subject, predicate, obj, points, 1,
                     json.dumps([origin_key]), timestamp, timestamp),
                )
                return {"points": points, "deja_compte": False}
            origines = json.loads(row["origin_memories"])
            if origin_key in origines:
                return {"points": row["points"], "deja_compte": True}
            origines.append(origin_key)
            # Un message relu N fois reste UN message : on compte les
            # identifiants de base, pas les cles (<id>#r1, <id>#r2...).
            messages = len({o.split("#", 1)[0] for o in origines})
            total = round(row["points"] + points, 2)
            conn.execute(
                "UPDATE fact_candidates SET points=?, message_count=?, "
                "origin_memories=?, last_seen=? WHERE id=?",
                (total, messages, json.dumps(origines), timestamp, row["id"]),
            )
            return {"points": total, "deja_compte": False}

    def promote_candidates(
        self, seuil: float, timestamp: str, candidate_id: int | None = None
    ) -> list[dict[str, Any]]:
        """Recopie au carnet de fiches les candidats ayant atteint le seuil.

        Une fiche deja promue (promoted_at renseigne) n est jamais recopiee.
        """
        base = ("SELECT id, subject, predicate, object, confidence, points, "
                "origin_memories FROM fact_candidates WHERE promoted_at IS NULL ")
        if candidate_id is None:
            requete, params = base + "AND rejected_at IS NULL AND points >= ?", (seuil,)
        else:
            requete, params = base + "AND id = ?", (candidate_id,)
        with self._connect() as conn:
            rows = conn.execute(requete, params).fetchall()

        promus: list[dict[str, Any]] = []
        for row in rows:
            origines = json.loads(row["origin_memories"])
            self.add_fact(KnowledgeFact(
                subject=row["subject"],
                predicate=row["predicate"],
                object=row["object"],
                confidence=row["confidence"],
                origin_memory=origines[0] if origines else None,
                metadata={"promu_depuis": "brouillon", "origines": origines,
                          "points": row["points"]},
                created_at=timestamp,
            ))
            fiche = self.current_fact(row["subject"], row["predicate"], row["object"])
            with self._connect() as conn:
                conn.execute(
                    "UPDATE fact_candidates SET promoted_at=?, promoted_fact_id=? "
                    "WHERE id=?",
                    (timestamp, fiche.id if fiche else None, row["id"]),
                )
            promus.append({"subject": row["subject"], "predicate": row["predicate"],
                           "object": row["object"], "points": row["points"]})
        return promus

    def records_to_reread(self, limit: int) -> list[dict[str, Any]]:
        """Messages bruts de l utilisateur, les moins relus d abord."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT r.id AS rid, r.content AS contenu, "
                "COALESCE((SELECT COUNT(*) FROM reread_log l "
                "          WHERE l.record_id = r.id), 0) AS passes "
                "FROM memory_records r "
                "WHERE r.category = 'brut' AND r.source = 'utilisateur' "
                "ORDER BY passes ASC, r.created_at ASC LIMIT ?",
                (limit,),
            ).fetchall()
        return [{"id": r["rid"], "content": r["contenu"], "passes": r["passes"]}
                for r in rows]

    def mark_reread(self, record_id: str, passe: int, timestamp: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO reread_log (record_id, passe, done_at) "
                "VALUES (?, ?, ?)",
                (record_id, passe, timestamp),
            )

    def list_candidates(self, limit: int = 200) -> list[dict[str, Any]]:
        """Contenu du brouillon, les plus corrobores d abord."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT id, subject, predicate, object, points, message_count, "
                "origin_memories, first_seen, last_seen, promoted_at "
                "FROM fact_candidates "
                "ORDER BY promoted_at IS NOT NULL, points DESC, last_seen DESC "
                "LIMIT ?",
                (limit,),
            ).fetchall()
        return [
            {
                "id": r["id"], "subject": r["subject"], "predicate": r["predicate"],
                "object": r["object"], "points": r["points"],
                "message_count": r["message_count"],
                "origines": json.loads(r["origin_memories"]),
                "first_seen": r["first_seen"], "last_seen": r["last_seen"],
                "promoted_at": r["promoted_at"],
            }
            for r in rows
        ]

    def add_fact(self, fact: KnowledgeFact) -> bool:
        if fact.metadata.get("retract"):
            return self.retract_fact(fact.subject, fact.predicate, fact.object)
        if fact.metadata.get("historical"):
            existing = [
                item for item in self.list_facts(subject=fact.subject, predicate=fact.predicate)
                if item.metadata.get("historical")
                and not item.retracted
                and normalize_text(item.object) == normalize_text(fact.object)
            ]
            if existing:
                return False
            fact.is_current = False
        if fact.predicate in {"lives_at", "works_at"} and not fact.metadata.get("historical"):
            current = self.current_fact(fact.subject, fact.predicate)
            if current and normalize_text(current.object) == normalize_text(fact.object):
                return False
            self._close_current(fact.subject, fact.predicate, fact.created_at)
        if fact.predicate in {
            "favorite_color",
            "name",
            "operating_system",
            "smartphone",
            "spouse",
            "vehicle",
        }:
            self._close_current(fact.subject, fact.predicate, fact.created_at)
        if fact.predicate == "likes":
            existing = self.current_fact(fact.subject, fact.predicate, fact.object)
            if existing:
                return False
        fact.valid_from = fact.valid_from or fact.created_at
        with self._connect() as conn:
            cur = conn.execute(
                """
                INSERT INTO knowledge_facts
                (subject, predicate, object, confidence, origin_memory, metadata,
                 created_at, valid_from, valid_to, is_current, retracted,
                 retracted_at, retraction_reason)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                self._fact_row(fact),
            )
            fact.id = int(cur.lastrowid)
            self._upsert_node(conn, fact.subject, "entity", fact.subject, fact.confidence, fact.created_at)
            self._upsert_node(conn, fact.object, "entity", fact.object, fact.confidence, fact.created_at)
            conn.execute(
                """
                INSERT INTO semantic_relations
                (source, target, relation, confidence, origin_memory, timestamp)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (fact.subject, fact.object, fact.predicate, fact.confidence, fact.origin_memory, fact.created_at),
            )
        return True

    def _fact_row(self, fact: KnowledgeFact) -> tuple[Any, ...]:
        return (
            fact.subject,
            fact.predicate,
            fact.object,
            fact.confidence,
            fact.origin_memory,
            json.dumps(fact.metadata, ensure_ascii=False),
            fact.created_at,
            fact.valid_from,
            fact.valid_to,
            int(fact.is_current),
            int(fact.retracted),
            fact.retracted_at,
            fact.retraction_reason,
        )

    def _upsert_node(self, conn: sqlite3.Connection, node_id: str, type_: str, label: str, confidence: float, timestamp: str) -> None:
        conn.execute(
            """
            INSERT INTO semantic_nodes (id, type, label, confidence, timestamp)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET confidence=excluded.confidence, timestamp=excluded.timestamp
            """,
            (node_id, type_, label, confidence, timestamp),
        )

    def _close_current(self, subject: str, predicate: str, timestamp: str) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE knowledge_facts
                SET is_current = 0, valid_to = ?
                WHERE subject = ? AND predicate = ? AND is_current = 1 AND retracted = 0
                """,
                (timestamp, subject, predicate),
            )

    def retract_fact(self, subject: str, predicate: str, obj: str) -> bool:
        current = now_iso()
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT id, object FROM knowledge_facts
                WHERE subject = ? AND predicate = ? AND retracted = 0
                """,
                (subject, predicate),
            ).fetchall()
            ids = [
                row["id"]
                for row in rows
                if normalize_text(row["object"]) == normalize_text(obj)
            ]
            for fact_id in ids:
                conn.execute(
                    """
                    UPDATE knowledge_facts
                    SET is_current = 0, retracted = 1, retracted_at = ?,
                        retraction_reason = 'user_denial'
                    WHERE id = ?
                    """,
                    (current, fact_id),
                )
            return bool(ids)

    def list_records(self, limit: int = 100) -> list[MemoryRecord]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM memory_records ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [self._record_from_row(row) for row in rows]

    def search_records(self, query: str, limit: int = 10) -> list[MemoryRecord]:
        needle = normalize_text(query)
        records = self.list_records(1000)
        matches = [record for record in records if needle in normalize_text(record.content)]
        return matches[:limit]

    def current_fact(self, subject: str, predicate: str, obj: str | None = None) -> KnowledgeFact | None:
        facts = self.list_facts(subject=subject, predicate=predicate, current_only=True)
        if obj is not None:
            facts = [fact for fact in facts if normalize_text(fact.object) == normalize_text(obj)]
        return facts[-1] if facts else None

    def list_lives_at(self) -> list[KnowledgeFact]:
        return self.list_facts(predicate="lives_at", include_retracted=True)

    def list_facts(
        self,
        *,
        subject: str | None = None,
        predicate: str | None = None,
        current_only: bool = False,
        include_retracted: bool = True,
        limit: int = 1000,
    ) -> list[KnowledgeFact]:
        clauses: list[str] = []
        params: list[Any] = []
        if subject:
            clauses.append("subject = ?")
            params.append(subject)
        if predicate:
            clauses.append("predicate = ?")
            params.append(predicate)
        if current_only:
            clauses.append("is_current = 1")
        if not include_retracted:
            clauses.append("retracted = 0")
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT * FROM knowledge_facts {where} ORDER BY id ASC LIMIT ?",
                (*params, limit),
            ).fetchall()
        return [self._fact_from_row(row) for row in rows]

    def forget(self, query: str) -> int:
        needle = normalize_text(query)
        current_only = False
        if "femme" in needle or "epouse" in needle:
            needle = "spouse"
            current_only = True
        count = 0
        with self._connect() as conn:
            rows = conn.execute("SELECT id, subject, predicate, object, is_current FROM knowledge_facts").fetchall()
            for row in rows:
                haystack = normalize_text(f"{row['subject']} {row['predicate']} {row['object']}")
                if current_only and not row["is_current"]:
                    continue
                if needle and any(part in haystack for part in needle.split()):
                    conn.execute("DELETE FROM knowledge_facts WHERE id = ?", (row["id"],))
                    count += 1
        return count

    def status(self) -> dict[str, int]:
        with self._connect() as conn:
            records = conn.execute("SELECT COUNT(*) FROM memory_records").fetchone()[0]
            facts = conn.execute("SELECT COUNT(*) FROM knowledge_facts").fetchone()[0]
        return {"records": records, "facts": facts}

    def _record_from_row(self, row: sqlite3.Row) -> MemoryRecord:
        return MemoryRecord(
            id=row["id"],
            source=row["source"],
            category=row["category"],
            content=row["content"],
            metadata=json.loads(row["metadata"] or "{}"),
            created_at=row["created_at"],
        )

    def _fact_from_row(self, row: sqlite3.Row) -> KnowledgeFact:
        return KnowledgeFact(
            id=row["id"],
            subject=row["subject"],
            predicate=row["predicate"],
            object=row["object"],
            confidence=row["confidence"],
            origin_memory=row["origin_memory"],
            metadata=json.loads(row["metadata"] or "{}"),
            created_at=row["created_at"],
            valid_from=row["valid_from"],
            valid_to=row["valid_to"],
            is_current=bool(row["is_current"]),
            retracted=bool(row["retracted"]),
            retracted_at=row["retracted_at"],
            retraction_reason=row["retraction_reason"],
        )
