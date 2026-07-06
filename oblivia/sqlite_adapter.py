from __future__ import annotations

import re
import sqlite3
from ast import literal_eval
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .ontology import get_predicate
from .schemas import (
    KnowledgeFact,
    LifecycleKnowledgeFact,
    MemoryRecord,
    TemporalLivesAtFact,
    now_utc,
)
from .text_utils import normalize_text

DEFAULT_DB = "/etc/neronOS/server/memory/neron_memory.db"

_FACT_COLUMNS = """
id, subject, predicate, object, fact_type, source, confidence, raw_text,
metadata, created_at, updated_at, valid_from, valid_to, is_current,
retracted, retracted_at, retraction_reason, lifecycle, conflict,
conflict_reason
"""


def _metadata(value: str) -> dict:
    try:
        decoded = literal_eval(value) if value else {}
    except (SyntaxError, ValueError):
        return {}
    return decoded if isinstance(decoded, dict) else {}


def _timeline_key(fact: LifecycleKnowledgeFact) -> tuple[int, object]:
    years_ago = fact.metadata.get("years_ago")
    if isinstance(years_ago, int):
        return (0, -years_ago)
    if fact.metadata.get("relative_period") == "before":
        return (0, 0)
    return (1, fact.valid_from)


class SQLiteMemoryAdapter:
    """SQLite knowledge store whose write behavior comes from the ontology."""

    def __init__(self, db_path: str = DEFAULT_DB):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _connect(self):
        return sqlite3.connect(self.db_path)

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.execute("""
            CREATE TABLE IF NOT EXISTS memory_records(
                id TEXT PRIMARY KEY,
                source TEXT,
                category TEXT,
                content TEXT,
                normalized_content TEXT,
                metadata TEXT,
                created_at TEXT,
                updated_at TEXT
            )
            """)
            conn.execute("""
            CREATE TABLE IF NOT EXISTS knowledge_facts(
                id TEXT PRIMARY KEY,
                subject TEXT NOT NULL,
                predicate TEXT NOT NULL,
                object TEXT NOT NULL,
                normalized_object TEXT NOT NULL,
                fact_type TEXT NOT NULL,
                source TEXT NOT NULL,
                confidence REAL NOT NULL,
                raw_text TEXT NOT NULL,
                metadata TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """)
            self._migrate_memory_records(conn)
            self._migrate_knowledge_schema(conn)
            self._migrate_legacy_spouse_facts(conn)
            self._ensure_lives_at_compatibility_table(conn)
            self._migrate_lives_at_compatibility_data(conn)
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_facts_relation "
                "ON knowledge_facts(subject, predicate, is_current, retracted)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_facts_timeline "
                "ON knowledge_facts(subject, predicate, valid_from, valid_to)"
            )
            conn.commit()

    @staticmethod
    def _migrate_memory_records(conn) -> None:
        columns = {
            row[1] for row in conn.execute("PRAGMA table_info(memory_records)")
        }
        if "normalized_content" not in columns:
            conn.execute(
                "ALTER TABLE memory_records ADD COLUMN normalized_content TEXT"
            )
        rows = conn.execute(
            """
            SELECT id, content FROM memory_records
            WHERE normalized_content IS NULL OR normalized_content = ''
            """
        ).fetchall()
        for record_id, content in rows:
            conn.execute(
                "UPDATE memory_records SET normalized_content = ? WHERE id = ?",
                (normalize_text(content), record_id),
            )

    @staticmethod
    def _migrate_knowledge_schema(conn) -> None:
        columns = {
            row[1] for row in conn.execute("PRAGMA table_info(knowledge_facts)")
        }
        additions = {
            "valid_from": "TEXT",
            "valid_to": "TEXT",
            "is_current": "INTEGER NOT NULL DEFAULT 1",
            "retracted": "INTEGER NOT NULL DEFAULT 0",
            "retracted_at": "TEXT",
            "retraction_reason": "TEXT",
            "lifecycle": "TEXT",
            "conflict": "INTEGER NOT NULL DEFAULT 0",
            "conflict_reason": "TEXT",
        }
        for name, definition in additions.items():
            if name not in columns:
                conn.execute(
                    f"ALTER TABLE knowledge_facts ADD COLUMN {name} {definition}"
                )
        conn.execute(
            """
            UPDATE knowledge_facts
            SET valid_from = COALESCE(valid_from, created_at)
            """
        )
        predicates = conn.execute(
            "SELECT DISTINCT predicate FROM knowledge_facts"
        ).fetchall()
        for (predicate,) in predicates:
            try:
                lifecycle = get_predicate(predicate).lifecycle
            except ValueError:
                lifecycle = "accumulate"
            conn.execute(
                """
                UPDATE knowledge_facts SET lifecycle = ?
                WHERE predicate = ? AND (lifecycle IS NULL OR lifecycle = '')
                """,
                (lifecycle, predicate),
            )

    @staticmethod
    def _ensure_lives_at_compatibility_table(conn) -> None:
        # Retained as an additive, read-only migration source for Phase 2.2/2.3
        # databases. New writes use knowledge_facts.
        conn.execute("""
        CREATE TABLE IF NOT EXISTS lives_at_facts(
            id TEXT PRIMARY KEY,
            subject TEXT NOT NULL,
            object TEXT NOT NULL,
            normalized_object TEXT NOT NULL,
            source TEXT NOT NULL,
            confidence REAL NOT NULL,
            raw_text TEXT NOT NULL,
            metadata TEXT NOT NULL,
            valid_from TEXT NOT NULL,
            valid_to TEXT,
            is_current INTEGER NOT NULL DEFAULT 0,
            retracted INTEGER NOT NULL DEFAULT 0,
            retracted_at TEXT,
            retraction_reason TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """)
        columns = {
            row[1] for row in conn.execute("PRAGMA table_info(lives_at_facts)")
        }
        for name, definition in {
            "retracted": "INTEGER NOT NULL DEFAULT 0",
            "retracted_at": "TEXT",
            "retraction_reason": "TEXT",
        }.items():
            if name not in columns:
                conn.execute(
                    f"ALTER TABLE lives_at_facts ADD COLUMN {name} {definition}"
                )

    @staticmethod
    def _migrate_legacy_spouse_facts(conn) -> None:
        rows = conn.execute(
            f"""
            SELECT {_FACT_COLUMNS}
            FROM knowledge_facts
            WHERE subject = 'user.spouse' AND predicate = 'name'
            """
        ).fetchall()
        for row in rows:
            conn.execute(
                """
                INSERT OR IGNORE INTO knowledge_facts(
                    id, subject, predicate, object, normalized_object,
                    fact_type, source, confidence, raw_text, metadata,
                    created_at, updated_at, valid_from, valid_to, is_current,
                    retracted, retracted_at, retraction_reason, lifecycle,
                    conflict, conflict_reason
                ) VALUES (?, 'user', 'spouse', ?, ?, ?, ?, ?, ?, ?, ?, ?,
                          ?, ?, ?, ?, ?, ?, 'replace', ?, ?)
                """,
                (
                    f"{row[0]}-spouse",
                    row[3],
                    normalize_text(row[3]),
                    row[4],
                    row[5],
                    row[6],
                    row[7],
                    row[8],
                    row[9],
                    row[10],
                    row[11],
                    row[12],
                    row[13],
                    row[14],
                    row[15],
                    row[16],
                    row[18],
                    row[19],
                ),
            )

    @staticmethod
    def _migrate_lives_at_compatibility_data(conn) -> None:
        rows = conn.execute(
            """
            SELECT id, subject, object, normalized_object, source, confidence,
                   raw_text, metadata, valid_from, valid_to, is_current,
                   retracted, retracted_at, retraction_reason,
                   created_at, updated_at
            FROM lives_at_facts
            """
        ).fetchall()
        for row in rows:
            conn.execute(
                """
                INSERT OR IGNORE INTO knowledge_facts(
                    id, subject, predicate, object, normalized_object,
                    fact_type, source, confidence, raw_text, metadata,
                    created_at, updated_at, valid_from, valid_to, is_current,
                    retracted, retracted_at, retraction_reason, lifecycle,
                    conflict, conflict_reason
                ) VALUES (?, ?, 'lives_at', ?, ?, 'semantic', ?, ?, ?, ?, ?,
                          ?, ?, ?, ?, ?, ?, ?, 'replace', 0, NULL)
                """,
                (
                    row[0], row[1], row[2], row[3], row[4], row[5], row[6],
                    row[7], row[14], row[15], row[8], row[9], row[10],
                    row[11], row[12], row[13],
                ),
            )

    def add(self, record: MemoryRecord) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO memory_records(
                    id, source, category, content, normalized_content,
                    metadata, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.id,
                    record.source,
                    record.category,
                    record.content,
                    normalize_text(record.content),
                    str(record.metadata),
                    record.created_at,
                    record.updated_at,
                ),
            )
            conn.commit()

    def search(self, query: str, limit: int = 10):
        normalized_query = normalize_text(query)
        with self._connect() as conn:
            return conn.execute(
                """
                SELECT id, source, category, content
                FROM memory_records
                WHERE normalized_content LIKE ?
                LIMIT ?
                """,
                (f"%{normalized_query}%", limit),
            ).fetchall()

    def recent(self, limit: int = 10):
        with self._connect() as conn:
            return conn.execute(
                """
                SELECT id, source, category, content
                FROM memory_records ORDER BY updated_at DESC LIMIT ?
                """,
                (limit,),
            ).fetchall()

    def cleanup(self, days: int) -> int:
        threshold = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        with self._connect() as conn:
            cursor = conn.execute(
                """
                DELETE FROM memory_records
                WHERE category IN ('runtime', 'unknown') AND updated_at < ?
                """,
                (threshold,),
            )
            conn.commit()
            return cursor.rowcount

    def upsert_fact(self, fact: KnowledgeFact) -> LifecycleKnowledgeFact:
        definition = get_predicate(fact.predicate)
        lifecycle = definition.lifecycle
        operation = str(fact.metadata.get("temporal_operation") or "assert")
        if lifecycle == "event":
            raise NotImplementedError("event lifecycle is declaration-only")
        if operation == "retraction":
            return self._retract(fact, lifecycle)
        if operation == "historical_assertion":
            existing = self._same_object(
                self._facts_for(fact.subject, fact.predicate),
                fact.object,
            )
            historical = next(
                (
                    item for item in existing
                    if not item.is_current and not item.retracted
                ),
                None,
            )
            if historical:
                return self._noop(historical)
            return self._insert_fact(fact, lifecycle, is_current=False)
        if lifecycle == "immutable":
            return self._upsert_immutable(fact, lifecycle)
        if lifecycle == "replace":
            return self._upsert_replace(fact, lifecycle)
        if lifecycle == "accumulate":
            return self._upsert_accumulate(
                fact,
                lifecycle,
                replacement_scope_key=definition.replacement_scope_key,
            )
        if lifecycle == "preference":
            return self._upsert_preference(fact, lifecycle)
        raise ValueError(f"unsupported lifecycle: {lifecycle}")

    def _upsert_immutable(
        self,
        fact: KnowledgeFact,
        lifecycle: str,
    ) -> LifecycleKnowledgeFact:
        existing = self._facts_for(fact.subject, fact.predicate)
        same = self._same_object(existing, fact.object)
        if same:
            return self._noop(same[0])
        if existing:
            duplicate_conflict = next(
                (
                    item for item in existing
                    if item.conflict
                    and normalize_text(item.object) == normalize_text(fact.object)
                ),
                None,
            )
            if duplicate_conflict:
                return self._noop(duplicate_conflict)
            return self._insert_fact(
                fact,
                lifecycle,
                is_current=False,
                conflict=True,
                conflict_reason="immutable_value_conflict",
            )
        return self._insert_fact(fact, lifecycle)

    def _upsert_replace(
        self,
        fact: KnowledgeFact,
        lifecycle: str,
    ) -> LifecycleKnowledgeFact:
        current = self._facts_for(
            fact.subject,
            fact.predicate,
            current=True,
            include_retracted=False,
        )
        if current and current[0].object == fact.object:
            return self._noop(current[0])
        transition_at = now_utc()
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE knowledge_facts
                SET is_current = 0, valid_to = ?, updated_at = ?
                WHERE subject = ? AND predicate = ?
                  AND is_current = 1 AND retracted = 0 AND conflict = 0
                """,
                (
                    transition_at,
                    transition_at,
                    fact.subject,
                    fact.predicate,
                ),
            )
            conn.commit()
        return self._insert_fact(
            fact,
            lifecycle,
            timestamp=transition_at,
        )

    def _upsert_accumulate(
        self,
        fact: KnowledgeFact,
        lifecycle: str,
        *,
        replacement_scope_key: str | None = None,
    ) -> LifecycleKnowledgeFact:
        existing = self._facts_for(fact.subject, fact.predicate)
        same = self._same_object(
            existing,
            fact.object,
        )
        active = next(
            (item for item in same if not item.retracted),
            None,
        )
        if active:
            return self._noop(active)
        if replacement_scope_key:
            scope = fact.metadata.get(replacement_scope_key)
            scoped_current = [
                item
                for item in existing
                if scope is not None
                and item.metadata.get(replacement_scope_key) == scope
                and item.is_current
                and not item.retracted
            ]
            if scoped_current:
                transition_at = now_utc()
                with self._connect() as conn:
                    conn.executemany(
                        """
                        UPDATE knowledge_facts
                        SET is_current = 0, valid_to = ?, updated_at = ?
                        WHERE id = ?
                        """,
                        [
                            (transition_at, transition_at, item.id)
                            for item in scoped_current
                        ],
                    )
                    conn.commit()
                return self._insert_fact(
                    fact,
                    lifecycle,
                    timestamp=transition_at,
                )
        return self._insert_fact(fact, lifecycle)

    def _upsert_preference(
        self,
        fact: KnowledgeFact,
        lifecycle: str,
    ) -> LifecycleKnowledgeFact:
        same = self._same_object(
            self._facts_for(fact.subject, fact.predicate),
            fact.object,
        )
        active = next(
            (
                item for item in same
                if item.is_current and not item.retracted
            ),
            None,
        )
        if active:
            return self._noop(active)
        retracted = next((item for item in same if item.retracted), None)
        if retracted:
            reactivated_at = now_utc()
            with self._connect() as conn:
                conn.execute(
                    """
                    UPDATE knowledge_facts
                    SET valid_from = ?, valid_to = NULL, is_current = 1,
                        retracted = 0, updated_at = ?
                    WHERE id = ?
                    """,
                    (reactivated_at, reactivated_at, retracted.id),
                )
                conn.commit()
            retracted.valid_from = reactivated_at
            retracted.valid_to = None
            retracted.is_current = True
            retracted.retracted = False
            retracted.updated_at = reactivated_at
            retracted.metadata = {
                **retracted.metadata,
                "reactivated": True,
            }
            return retracted
        return self._insert_fact(fact, lifecycle)

    def _retract(
        self,
        fact: KnowledgeFact,
        lifecycle: str,
    ) -> LifecycleKnowledgeFact:
        same = self._same_object(
            self._facts_for(fact.subject, fact.predicate),
            fact.object,
        )
        active = [item for item in same if not item.retracted]
        if not active:
            if same:
                return self._noop(same[0])
            denial = self._insert_fact(
                fact,
                lifecycle,
                is_current=False,
                retracted=True,
                retraction_reason=(
                    str(fact.metadata.get("retraction_reason"))
                    if fact.metadata.get("retraction_reason")
                    else "user_denial"
                ),
            )
            denial.valid_to = denial.valid_from
            with self._connect() as conn:
                conn.execute(
                    "UPDATE knowledge_facts SET valid_to = ? WHERE id = ?",
                    (denial.valid_to, denial.id),
                )
                conn.commit()
            return denial

        retracted_at = now_utc()
        reason = (
            str(fact.metadata.get("retraction_reason"))
            if fact.metadata.get("retraction_reason")
            else "user_denial"
        )
        with self._connect() as conn:
            conn.executemany(
                """
                UPDATE knowledge_facts
                SET valid_to = CASE
                        WHEN valid_to IS NULL THEN ? ELSE valid_to
                    END,
                    is_current = 0, retracted = 1, retracted_at = ?,
                    retraction_reason = ?, updated_at = ?
                WHERE id = ?
                """,
                [
                    (
                        retracted_at,
                        retracted_at,
                        reason,
                        retracted_at,
                        item.id,
                    )
                    for item in active
                ],
            )
            conn.commit()
        result = active[0]
        result.valid_to = result.valid_to or retracted_at
        result.is_current = False
        result.retracted = True
        result.retracted_at = retracted_at
        result.retraction_reason = reason
        result.updated_at = retracted_at
        result.metadata = {**result.metadata, **fact.metadata}
        return result

    def _insert_fact(
        self,
        fact: KnowledgeFact,
        lifecycle: str,
        *,
        timestamp: str | None = None,
        is_current: bool = True,
        retracted: bool = False,
        retraction_reason: str | None = None,
        conflict: bool = False,
        conflict_reason: str | None = None,
    ) -> LifecycleKnowledgeFact:
        processed_at = timestamp or now_utc()
        stored = LifecycleKnowledgeFact(
            **fact.model_dump(
                exclude={"created_at", "updated_at"},
            ),
            valid_from=processed_at,
            is_current=is_current,
            retracted=retracted,
            retracted_at=processed_at if retracted else None,
            retraction_reason=retraction_reason,
            lifecycle=lifecycle,
            conflict=conflict,
            conflict_reason=conflict_reason,
            created_at=processed_at,
            updated_at=processed_at,
        )
        with self._connect() as conn:
            conn.execute(
                f"""
                INSERT INTO knowledge_facts(
                    id, subject, predicate, object, normalized_object,
                    fact_type, source, confidence, raw_text, metadata,
                    created_at, updated_at, valid_from, valid_to, is_current,
                    retracted, retracted_at, retraction_reason, lifecycle,
                    conflict, conflict_reason
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                          ?, ?, ?, ?, ?)
                """,
                (
                    stored.id,
                    stored.subject,
                    stored.predicate,
                    stored.object,
                    normalize_text(stored.object),
                    stored.fact_type,
                    stored.source,
                    stored.confidence,
                    stored.raw_text,
                    str(stored.metadata),
                    stored.created_at,
                    stored.updated_at,
                    stored.valid_from,
                    stored.valid_to,
                    int(stored.is_current),
                    int(stored.retracted),
                    stored.retracted_at,
                    stored.retraction_reason,
                    stored.lifecycle,
                    int(stored.conflict),
                    stored.conflict_reason,
                ),
            )
            conn.commit()
        return stored

    @staticmethod
    def _same_object(
        facts: list[LifecycleKnowledgeFact],
        value: str,
    ) -> list[LifecycleKnowledgeFact]:
        normalized = normalize_text(value)
        return [
            item
            for item in facts
            if normalize_text(item.object) == normalized
        ]

    @staticmethod
    def _noop(fact: LifecycleKnowledgeFact) -> LifecycleKnowledgeFact:
        copy = fact.model_copy(deep=True)
        copy.metadata = {**copy.metadata, "temporal_noop": True}
        return copy

    @staticmethod
    def _row_fact(row) -> LifecycleKnowledgeFact:
        return LifecycleKnowledgeFact(
            id=row[0],
            subject=row[1],
            predicate=row[2],
            object=row[3],
            fact_type=row[4],
            source=row[5],
            confidence=row[6],
            raw_text=row[7],
            metadata=_metadata(row[8]),
            created_at=row[9],
            updated_at=row[10],
            valid_from=row[11],
            valid_to=row[12],
            is_current=bool(row[13]),
            retracted=bool(row[14]),
            retracted_at=row[15],
            retraction_reason=row[16],
            lifecycle=row[17],
            conflict=bool(row[18]),
            conflict_reason=row[19],
        )

    def _facts_for(
        self,
        subject: str,
        predicate: str,
        *,
        current: bool | None = None,
        include_retracted: bool = True,
        include_conflicts: bool = True,
        limit: int = 200,
    ) -> list[LifecycleKnowledgeFact]:
        conditions = ["subject = ?", "predicate = ?"]
        parameters: list[Any] = [subject, predicate]
        if current is not None:
            conditions.append("is_current = ?")
            parameters.append(int(current))
        if not include_retracted:
            conditions.append("retracted = 0")
        if not include_conflicts:
            conditions.append("conflict = 0")
        parameters.append(limit)
        with self._connect() as conn:
            rows = conn.execute(
                f"""
                SELECT {_FACT_COLUMNS}
                FROM knowledge_facts
                WHERE {' AND '.join(conditions)}
                ORDER BY valid_from ASC, valid_to ASC
                LIMIT ?
                """,
                parameters,
            ).fetchall()
        return [self._row_fact(row) for row in rows]

    def list_facts(
        self,
        limit: int = 100,
        *,
        include_retracted: bool = True,
        include_conflicts: bool = True,
    ) -> list[LifecycleKnowledgeFact]:
        conditions = []
        if not include_retracted:
            conditions.append("retracted = 0")
        if not include_conflicts:
            conditions.append("conflict = 0")
        where = "WHERE " + " AND ".join(conditions) if conditions else ""
        with self._connect() as conn:
            rows = conn.execute(
                f"""
                SELECT {_FACT_COLUMNS}
                FROM knowledge_facts {where}
                ORDER BY valid_from ASC, valid_to ASC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [self._row_fact(row) for row in rows]

    def list_lives_at(
        self,
        *,
        subject: str = "user",
        current: bool | None = None,
        include_retracted: bool = True,
        limit: int = 100,
    ) -> list[TemporalLivesAtFact]:
        facts = self._facts_for(
            subject,
            "lives_at",
            current=current,
            include_retracted=include_retracted,
            limit=limit,
        )
        return [
            TemporalLivesAtFact(
                **fact.model_dump(
                    exclude={"lifecycle", "conflict", "conflict_reason"},
                )
            )
            for fact in facts
        ]

    def search_facts(
        self,
        query: str,
        limit: int = 10,
    ) -> list[LifecycleKnowledgeFact]:
        normalized = normalize_text(query)
        canonical = " ".join(
            re.sub(
                r"[^a-z0-9 ]+",
                " ",
                normalized.replace("’", " ").replace("'", " "),
            ).split()
        )
        route = self._recall_route(canonical)
        if route:
            subject, predicate, mode = route
            facts = self._facts_for(
                subject,
                predicate,
                current=True if mode == "current" else None,
                include_retracted=False,
                include_conflicts=False,
                limit=200,
            )
            if mode == "previous":
                facts = [item for item in facts if not item.is_current]
                facts = sorted(facts, key=_timeline_key, reverse=True)
            elif mode == "history":
                facts = sorted(facts, key=_timeline_key)
            return facts[:limit]

        inverse = re.fullmatch(r"qui est (.+)", canonical)
        facts = self.list_facts(
            limit=200,
            include_retracted=False,
            include_conflicts=False,
        )
        if inverse:
            entity = inverse.group(1)
            return [
                item
                for item in facts
                if normalize_text(item.subject) == entity
                and item.predicate == "relation_to_user"
                and item.is_current
            ][:limit]
        tokens = {
            token
            for token in canonical.split()
            if len(token) > 2
            and token not in {
                "qui", "que", "quoi", "comment", "est", "sais",
                "sur", "dans", "memoire",
            }
        }
        return [
            item
            for item in facts
            if item.is_current
            and (
                not tokens
                or tokens
                & set(
                    normalize_text(
                        f"{item.subject} {item.predicate} "
                        f"{item.object} {item.raw_text}"
                    ).split()
                )
            )
        ][:limit]

    @staticmethod
    def _recall_route(
        query: str,
    ) -> tuple[str, str, str] | None:
        routes = (
            (("papa",), "user", "is", "current"),
            (
                ("ma femme avant", "s appelait ma femme avant"),
                "user", "spouse", "previous",
            ),
            (("ma femme",), "user", "spouse", "current"),
            (("aime boire", "j aime quoi"), "user", "likes", "current"),
            (("comment je m appelle",), "user", "name", "current"),
            (("comment je m appelais avant",), "user", "name", "previous"),
            (("mon fils",), "user.son", "name", "current"),
            (("ou est ce que j habite",), "user", "lives_at", "current"),
            (
                ("ou est ce que j habitais avant", "ou habitais je avant"),
                "user", "lives_at", "previous",
            ),
            (
                ("ou ai je vecu", "dans quelles villes ai je vecu"),
                "user", "lives_at", "history",
            ),
            (("ou est ce que je travaille",), "user", "works_at", "current"),
            (("ou travaillais je avant",), "user", "works_at", "previous"),
            (("combien ai je d enfants",), "user", "children_count", "current"),
            (("mes enfants",), "user", "has_child", "current"),
        )
        for aliases, subject, predicate, mode in routes:
            if any(alias in query for alias in aliases):
                return subject, predicate, mode
        return None

    def forget_facts(self, query: str) -> int:
        matches = self.search_facts(query, limit=200)
        active = [item for item in matches if not item.retracted]
        if not active:
            return 0
        retracted_at = now_utc()
        with self._connect() as conn:
            conn.executemany(
                """
                UPDATE knowledge_facts
                SET is_current = 0, retracted = 1, retracted_at = ?,
                    retraction_reason = 'explicit_forget',
                    valid_to = COALESCE(valid_to, ?), updated_at = ?
                WHERE id = ?
                """,
                [
                    (retracted_at, retracted_at, retracted_at, item.id)
                    for item in active
                ],
            )
            conn.commit()
        return len(active)

    def status(self):
        with self._connect() as conn:
            record_count = conn.execute(
                "SELECT COUNT(*) FROM memory_records"
            ).fetchone()[0]
            fact_count = conn.execute(
                "SELECT COUNT(*) FROM knowledge_facts"
            ).fetchone()[0]
            retracted_count = conn.execute(
                "SELECT COUNT(*) FROM knowledge_facts WHERE retracted = 1"
            ).fetchone()[0]
            conflict_count = conn.execute(
                "SELECT COUNT(*) FROM knowledge_facts WHERE conflict = 1"
            ).fetchone()[0]
            legacy_lives_count = conn.execute(
                "SELECT COUNT(*) FROM lives_at_facts"
            ).fetchone()[0]
        return {
            "backend": "sqlite",
            "path": str(self.db_path),
            "records": record_count,
            "facts": fact_count,
            "retracted_facts": retracted_count,
            "conflicts": conflict_count,
            "legacy_lives_at_facts": legacy_lives_count,
        }
