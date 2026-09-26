#!/usr/bin/env python3
"""Retrograde en brouillons les faits anterieurs a la provenance.

Contexte. Jusqu'au 08/09/2026, `knowledge_facts` ne portait aucune trace de
ce qui justifiait un fait. Les faits deja en base peuvent donc aussi bien
venir d'une phrase de l'utilisateur que d'une invention du juge promue par
la relecture nocturne — rien ne permet de les distinguer apres coup.

Ces faits sont marques `legacy` par la migration de schema. Ce script les
ramene au brouillon : ils redeviennent des hypotheses, consultables et
confirmables, mais ils ne sont plus presentes comme des verites.

Aucune donnee n'est perdue. Les lignes d'origine sont d'abord recopiees
telles quelles dans `knowledge_facts_avant_provenance`, ce qui rend
l'operation reversible.

    python3 retrograder_faits_sans_provenance.py <base.db> [--appliquer]

Sans --appliquer, le script se contente de decrire ce qu'il ferait.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import UTC, datetime

TABLE_SAUVEGARDE = "knowledge_facts_avant_provenance"


def _maintenant() -> str:
    return datetime.now(UTC).isoformat()


def retrograder(chemin: str, appliquer: bool) -> int:
    conn = sqlite3.connect(chemin)
    conn.row_factory = sqlite3.Row

    colonnes = {r[1] for r in conn.execute("PRAGMA table_info(knowledge_facts)")}
    if "provenance" not in colonnes:
        print("  la colonne provenance n'existe pas encore : demarrer le "
              "service memory une fois pour appliquer la migration de schema.")
        return 1

    faits = conn.execute(
        "SELECT * FROM knowledge_facts WHERE provenance = 'legacy' "
        "AND retracted = 0"
    ).fetchall()

    print(f"  {len(faits)} fait(s) sans provenance exploitable")
    for f in faits[:10]:
        print(f"    {f['subject']} | {f['predicate']} | {f['object']}")
    if len(faits) > 10:
        print(f"    ... et {len(faits) - 10} autre(s)")

    if not faits:
        return 0
    if not appliquer:
        print("\n  simulation seule — relancer avec --appliquer")
        return 0

    horodatage = _maintenant()
    conn.execute(
        f"CREATE TABLE IF NOT EXISTS {TABLE_SAUVEGARDE} AS "
        "SELECT * FROM knowledge_facts WHERE 0"
    )

    retrogrades = 0
    for f in faits:
        conn.execute(
            f"INSERT INTO {TABLE_SAUVEGARDE} SELECT * FROM knowledge_facts "
            "WHERE id = ?",
            (f["id"],),
        )
        # Le brouillon garde la trace de l'origine : sans points, la fiche
        # ne peut pas etre promue automatiquement — c'est bien l'intention.
        conn.execute(
            "INSERT OR IGNORE INTO fact_candidates "
            "(subject, predicate, object, confidence, points, message_count, "
            " origin_memories, first_seen, last_seen, provenance) "
            "VALUES (?, ?, ?, ?, 0, 0, ?, ?, ?, 'legacy')",
            (f["subject"], f["predicate"], f["object"], f["confidence"],
             json.dumps([f"legacy#{f['id']}"]),
             f["created_at"] or horodatage, horodatage),
        )
        conn.execute("DELETE FROM knowledge_facts WHERE id = ?", (f["id"],))
        retrogrades += 1

    conn.commit()
    restants = conn.execute("SELECT COUNT(*) FROM knowledge_facts").fetchone()[0]
    brouillons = conn.execute("SELECT COUNT(*) FROM fact_candidates").fetchone()[0]
    conn.close()

    print(f"\n  {retrogrades} fait(s) retrograde(s) en brouillon")
    print(f"  sauvegarde integrale dans {TABLE_SAUVEGARDE}")
    print(f"  carnet : {restants} fait(s) | brouillon : {brouillons} fiche(s)")
    return 0


def main() -> int:
    parseur = argparse.ArgumentParser(description=__doc__)
    parseur.add_argument("base", help="chemin de neron_memory.db")
    parseur.add_argument("--appliquer", action="store_true",
                         help="ecrire reellement (sinon simulation)")
    args = parseur.parse_args()
    return retrograder(args.base, args.appliquer)


if __name__ == "__main__":
    sys.exit(main())
