"""server/memory/dumpable.py

Base commune pour tous les schémas de données du service memory (Memory
Providers, Storage Providers, Knowledge Providers). Extrait de
oblivia/schemas.py pour rester une feuille sans dépendance : si
knowledge/schemas.py importait Dumpable depuis oblivia/schemas.py, le
package oblivia devrait s'initialiser en entier (donc manager.py, donc
protocols.py, donc memory.knowledge.schemas) avant même que
knowledge/schemas.py ait fini de se charger — cycle d'import garanti.

Ce module ne doit JAMAIS importer quoi que ce soit d'oblivia/, de
knowledge/, ou de protocols.py — c'est ce qui garantit qu'aucun des trois
ne peut créer de cycle en passant par ici.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass
class Dumpable:
    def model_dump(self, mode: str | None = None) -> dict[str, Any]:
        del mode
        return asdict(self)
