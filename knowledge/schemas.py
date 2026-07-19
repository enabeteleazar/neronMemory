"""server/memory/knowledge/schemas.py

Schémas de données pour les Knowledge Providers. Volontairement séparés de
oblivia/schemas.py (MemoryRecord, etc.) — un document de connaissance n'est
pas un souvenir, cf. server/memory/protocols.py pour la distinction.
"""
from __future__ import annotations

from dataclasses import dataclass

from memory.dumpable import Dumpable


@dataclass
class KnowledgeDocument(Dumpable):
    """Un résultat de recherche dans une base de connaissances."""

    path: str          # chemin relatif dans le vault, ex. "identity/NERON.md"
    title: str          # premier titre markdown trouvé, ou nom de fichier
    snippet: str        # extrait autour du terme trouvé
    score: float = 1.0  # nombre d'occurrences des termes recherchés


@dataclass
class KnowledgeDocumentMeta(Dumpable):
    """Métadonnées d'un document, sans contenu — pour lister le vault."""

    path: str
    title: str
    modified_at: str
