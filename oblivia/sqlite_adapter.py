from __future__ import annotations

import json
import logging
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from .provenance import (
    HYPOTHESE,
    JAMAIS_AUTOMATIQUE,
    LLM_GENERATED,
    PREUVE_DIRECTE,
    USER_CONFIRMED,
)
from .provenance import normaliser as normaliser_provenance
from .provenance import peut_etre_promu
from .schemas import KnowledgeFact, MemoryRecord, now_iso
from memory.text_utils import normalize_text

logger = logging.getLogger("memory.oblivia.sqlite")

# Ordre de force des provenances. Sert quand un meme triplet revient par
# une voie plus sure : l'utilisateur confirme ce que le modele supposait,
# la fiche doit gagner en credit, jamais en perdre.
_FORCE = {p: 3 for p in PREUVE_DIRECTE} | {p: 2 for p in HYPOTHESE} | {
    p: 1 for p in JAMAIS_AUTOMATIQUE
}


def _meilleure_provenance(actuelle: str | None, nouvelle: str | None) -> str:
    a, b = normaliser_provenance(actuelle), normaliser_provenance(nouvelle)
    return a if _FORCE.get(a, 0) >= _FORCE.get(b, 0) else b


class SQLiteMemoryAdapter:
    # Attente maximale sur un verrou avant `database is locked`. Le defaut de
    # sqlite3 est 5 s : trop court des que deux requetes HTTP ecrivent en meme
    # temps, et l'erreur remonte alors jusqu'a l'appelant.
    LOCK_TIMEOUT_SECONDS = 30.0

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()
        self._migrate()
        self._create_indexes()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        """Connexion transactionnelle, FERMEE a la sortie.

        `with sqlite3.connect(...) as conn` gere la transaction (commit ou
        rollback) mais NE FERME PAS la connexion — piege classique de
        l'API sqlite3. Les 19 sites d'appel de cette classe s'en remettaient
        donc au ramasse-miettes pour liberer les descripteurs. Ce
        gestionnaire de contexte conserve la semantique transactionnelle et
        ajoute la fermeture.

        WAL : en mode `delete` (defaut), une ecriture bloque toutes les
        lectures. memory est un service HTTP asynchrone qui peut traiter
        plusieurs requetes a la fois ; WAL permet aux lectures de continuer
        pendant une ecriture.
        """
        conn = sqlite3.connect(self.path, timeout=self.LOCK_TIMEOUT_SECONDS)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA foreign_keys=ON")
            with conn:
                yield conn
        finally:
            conn.close()

    def _create_indexes(self) -> None:
        """Index sur les colonnes reellement interrogees.

        Sans eux, `EXPLAIN QUERY PLAN` annonce `SCAN memory_records` et
        `SCAN knowledge_facts` sur chaque recherche : invisible a 300
        enregistrements, bloquant a 10 000.
        """
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE INDEX IF NOT EXISTS idx_records_created_at
                    ON memory_records(created_at DESC);
                CREATE INDEX IF NOT EXISTS idx_facts_subject_predicate
                    ON knowledge_facts(subject, predicate);
                CREATE INDEX IF NOT EXISTS idx_facts_current
                    ON knowledge_facts(is_current);
                CREATE INDEX IF NOT EXISTS idx_candidates_points
                    ON fact_candidates(points DESC);
                """
            )

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
                -- Vocabulaire des predicats. Le code ne porte plus qu'un
                -- vocabulaire d'AMORCAGE : ce qui fait autorite a l'execution
                -- est cette table, que memory enrichit lui-meme quand il
                -- rencontre un predicat inconnu mais structurellement valide.
                --   kind    : attribut | relation_symetrique | relation_orientee
                --             (determine la forme canonique et la detection
                --              des paires contradictoires)
                --   origine : amorce | appris
                CREATE TABLE IF NOT EXISTS predicates (
                    name TEXT PRIMARY KEY,
                    kind TEXT NOT NULL,
                    origine TEXT NOT NULL,
                    usages INTEGER NOT NULL DEFAULT 0,
                    first_seen TEXT NOT NULL,
                    last_seen TEXT NOT NULL,
                    exemple TEXT
                );
                """
            )

    def _migrate(self) -> None:
        """Migrations additives : aucune donnee existante n'est touchee."""
        with self._connect() as conn:
            colonnes = {r[1] for r in conn.execute(
                "PRAGMA table_info(fact_candidates)"
            )}
            if "rejected_at" not in colonnes:
                conn.execute(
                    "ALTER TABLE fact_candidates ADD COLUMN rejected_at TEXT"
                )
            # Provenance : d'ou vient l'information, donc ce qu'on est en
            # droit d'en croire (cf. oblivia/provenance.py). Un brouillon
            # sans provenance connue est une hypothese de modele : c'est le
            # cas le plus defavorable, donc le defaut le plus sur.
            if "provenance" not in colonnes:
                conn.execute(
                    "ALTER TABLE fact_candidates ADD COLUMN provenance TEXT "
                    "NOT NULL DEFAULT 'llm_generated'"
                )

            colonnes_faits = {r[1] for r in conn.execute(
                "PRAGMA table_info(knowledge_facts)"
            )}
            if "provenance" not in colonnes_faits:
                # Les faits anterieurs a cette migration n'ont aucune preuve
                # rattachable : 'legacy' les rend identifiables sans les
                # detruire ni leur preter une fiabilite qu'ils n'ont pas.
                conn.execute(
                    "ALTER TABLE knowledge_facts ADD COLUMN provenance TEXT "
                    "NOT NULL DEFAULT 'legacy'"
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
        provenance: str = LLM_GENERATED,
    ) -> dict[str, Any]:
        """Ajoute ou renforce un triplet dans le brouillon.

        Un meme message ne peut JAMAIS compter deux fois : origin_key est
        conserve dans origin_memories, et rejoue sans aucun effet.

        `provenance` dit ce qu'on est en droit de croire de ce triplet
        (cf. oblivia/provenance.py). Quand un triplet deja connu revient
        avec une provenance plus forte — l'utilisateur confirme ce que le
        modele avait suppose — la meilleure des deux est conservee.
        """
        provenance = normaliser_provenance(provenance)
        with self._connect() as conn:
            row = conn.execute(
                "SELECT id, points, origin_memories, provenance FROM fact_candidates "
                "WHERE subject=? AND predicate=? AND object=?",
                (subject, predicate, obj),
            ).fetchone()
            if row is None:
                conn.execute(
                    "INSERT INTO fact_candidates (subject, predicate, object, "
                    "points, message_count, origin_memories, first_seen, "
                    "last_seen, provenance) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (subject, predicate, obj, points, 1,
                     json.dumps([origin_key]), timestamp, timestamp, provenance),
                )
                return {"points": points, "deja_compte": False,
                        "provenance": provenance}
            origines = json.loads(row["origin_memories"])
            retenue = _meilleure_provenance(row["provenance"], provenance)
            if origin_key in origines:
                if retenue != row["provenance"]:
                    conn.execute(
                        "UPDATE fact_candidates SET provenance=? WHERE id=?",
                        (retenue, row["id"]),
                    )
                return {"points": row["points"], "deja_compte": True,
                        "provenance": retenue}
            origines.append(origin_key)
            # Un message relu N fois reste UN message : on compte les
            # identifiants de base, pas les cles (<id>#r1, <id>#r2...).
            messages = len({o.split("#", 1)[0] for o in origines})
            total = round(row["points"] + points, 2)
            conn.execute(
                "UPDATE fact_candidates SET points=?, message_count=?, "
                "origin_memories=?, last_seen=?, provenance=? WHERE id=?",
                (total, messages, json.dumps(origines), timestamp,
                 retenue, row["id"]),
            )
            return {"points": total, "deja_compte": False,
                    "provenance": retenue}

    def promote_candidates(
        self, seuil: float, timestamp: str, candidate_id: int | None = None
    ) -> list[dict[str, Any]]:
        """Recopie au carnet de fiches les candidats ayant atteint le seuil.

        Une fiche deja promue (promoted_at renseigne) n est jamais recopiee.
        """
        base = ("SELECT id, subject, predicate, object, confidence, points, "
                "message_count, provenance, origin_memories "
                "FROM fact_candidates WHERE promoted_at IS NULL ")
        if candidate_id is None:
            # Le filtre par points reste, mais il ne decide plus seul : la
            # provenance tranche ensuite, candidat par candidat.
            requete, params = base + "AND rejected_at IS NULL AND points >= ?", (seuil,)
        else:
            # Promotion nominative : l'utilisateur valide une fiche precise
            # depuis l'interface. C'est une confirmation explicite, elle
            # court-circuite legitimement l'examen de provenance.
            requete, params = base + "AND id = ?", (candidate_id,)
        with self._connect() as conn:
            rows = conn.execute(requete, params).fetchall()

        promus: list[dict[str, Any]] = []
        for row in rows:
            if candidate_id is None:
                autorise, motif = peut_etre_promu(
                    row["provenance"], row["points"],
                    row["message_count"], seuil,
                )
                if not autorise:
                    logger.info(
                        "oblivia_promotion_refusee %s | %s | %s -> %s",
                        row["subject"], row["predicate"], row["object"], motif,
                    )
                    continue
            provenance_promue = (
                USER_CONFIRMED if candidate_id is not None
                else normaliser_provenance(row["provenance"])
            )
            origines = json.loads(row["origin_memories"])
            self.add_fact(KnowledgeFact(
                subject=row["subject"],
                predicate=row["predicate"],
                object=row["object"],
                confidence=row["confidence"],
                origin_memory=origines[0] if origines else None,
                metadata={"promu_depuis": "brouillon", "origines": origines,
                          "points": row["points"],
                          "messages_distincts": row["message_count"]},
                created_at=timestamp,
                provenance=provenance_promue,
            ))
            fiche = self.current_fact(row["subject"], row["predicate"], row["object"])
            with self._connect() as conn:
                conn.execute(
                    "UPDATE fact_candidates SET promoted_at=?, promoted_fact_id=? "
                    "WHERE id=?",
                    (timestamp, fiche.id if fiche else None, row["id"]),
                )
            promus.append({"subject": row["subject"], "predicate": row["predicate"],
                           "object": row["object"], "points": row["points"],
                           "provenance": provenance_promue})
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
                "origin_memories, first_seen, last_seen, promoted_at, provenance "
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
                "provenance": r["provenance"],
            }
            for r in rows
        ]

    def reread_summary(self) -> dict[str, Any]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS n, MAX(done_at) AS dernier FROM reread_log"
            ).fetchone()
        return {"passes_total": row["n"], "derniere_passe": row["dernier"]}

    def get_record(self, record_id: str) -> dict[str, Any] | None:
        """Message brut d origine. Accepte aussi une cle de relecture <id>#rN."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT id, source, category, content, created_at "
                "FROM memory_records WHERE id=?",
                (record_id.split("#", 1)[0],),
            ).fetchone()
        if row is None:
            return None
        return {"id": row["id"], "source": row["source"],
                "category": row["category"], "content": row["content"],
                "created_at": row["created_at"]}

    def add_fact(self, fact: KnowledgeFact) -> bool:
        if fact.metadata.get("retract"):
            return self.retract_fact(fact.subject, fact.predicate, fact.object)

        # Idempotence generale. La deduplication n'existait que pour une
        # poignee de predicats nommes (lives_at, works_at, likes...) : tout
        # le reste s'empilait. La base de production comptait ainsi trois
        # fois `animal_prefere = le renard` et deux fois `film_prefere =
        # Interstellar`. Reecrire un fait deja connu ne doit rien changer.
        if not fact.metadata.get("historical"):
            deja = self.current_fact(fact.subject, fact.predicate, fact.object)
            if deja is not None and not deja.retracted:
                return False

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
                 retracted_at, retraction_reason, provenance)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
            normaliser_provenance(fact.provenance),
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

    # ── Vocabulaire des predicats ──────────────────────────────────────
    # memory apprend ses propres predicats : le vocabulaire du code n'est
    # qu'un point de depart, cette table est ce qui fait autorite ensuite.

    def list_predicates(self) -> dict[str, dict[str, Any]]:
        """Vocabulaire courant, indexe par nom."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT name, kind, origine, usages, first_seen, last_seen, exemple "
                "FROM predicates"
            ).fetchall()
        return {row["name"]: dict(row) for row in rows}

    def add_predicate(
        self,
        name: str,
        kind: str,
        origine: str,
        timestamp: str,
        exemple: str | None = None,
    ) -> bool:
        """Enregistre un predicat. Renvoie False s'il existait deja.

        `INSERT OR IGNORE` plutot qu'un SELECT puis INSERT : deux messages
        traites en parallele peuvent decouvrir le meme predicat au meme
        instant, et la contrainte de cle primaire doit trancher, pas nous.
        """
        with self._connect() as conn:
            cur = conn.execute(
                "INSERT OR IGNORE INTO predicates "
                "(name, kind, origine, usages, first_seen, last_seen, exemple) "
                "VALUES (?, ?, ?, 0, ?, ?, ?)",
                (name, kind, origine, timestamp, timestamp, exemple),
            )
            return cur.rowcount > 0

    def touch_predicate(self, name: str, timestamp: str) -> None:
        """Compte un emploi. Sert a distinguer un predicat vivant d'un
        predicat apparu une fois puis jamais revu."""
        with self._connect() as conn:
            conn.execute(
                "UPDATE predicates SET usages = usages + 1, last_seen = ? "
                "WHERE name = ?",
                (timestamp, name),
            )

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
            provenance=(row["provenance"] if "provenance" in row.keys()
                        else "legacy"),
            metadata=json.loads(row["metadata"] or "{}"),
            created_at=row["created_at"],
            valid_from=row["valid_from"],
            valid_to=row["valid_to"],
            is_current=bool(row["is_current"]),
            retracted=bool(row["retracted"]),
            retracted_at=row["retracted_at"],
            retraction_reason=row["retraction_reason"],
        )
