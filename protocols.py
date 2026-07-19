"""server/memory/protocols.py

Contrats formels de l'architecture Memory API, conformes au schéma cible :

    Memory API
        │
        ├── Memory Providers   (Oblivia aujourd'hui, Mem0 ou autre demain)
        │       └── Storage Providers   (SQLite aujourd'hui, Postgres demain)
        │
        └── Knowledge Providers   (Obsidian aujourd'hui, Markdown/Git Wiki demain)

Ces classes sont des `typing.Protocol` : le typage est STRUCTUREL, pas
nominal — une classe n'a besoin d'hériter de rien pour satisfaire un
protocole, il lui suffit d'implémenter les bonnes méthodes avec la bonne
signature. C'est ce qui permet de remplacer un provider par un autre sans
toucher au code qui le consomme.

`@runtime_checkable` permet en plus un `isinstance(obj, Protocol)` — utilisé
comme garde-fou à la construction de chaque provider (cf. oblivia/manager.py,
knowledge/obsidian_provider.py, app.py) pour détecter immédiatement un
provider mal implémenté plutôt que de le découvrir au premier appel raté en
production.

Important : `isinstance` sur un Protocol runtime_checkable ne vérifie que la
PRÉSENCE des méthodes, pas leur signature exacte (types des paramètres,
type de retour). Ce n'est pas un remplacement pour des tests, seulement un
filet de sécurité supplémentaire.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

# Imports uniquement pour l'analyse statique (mypy/IDE) — jamais exécutés
# à l'exécution grâce à `from __future__ import annotations` (PEP 563),
# qui garde toutes les annotations sous forme de chaînes. C'est ce qui
# permet à protocols.py de ne dépendre d'AUCUN package (oblivia, knowledge)
# au runtime : deux tentatives d'import réel ont provoqué un cycle selon
# l'ordre d'import (oblivia/__init__.py importe manager.py de façon eager
# dès qu'on touche un sous-module quelconque d'oblivia, y compris juste
# oblivia.schemas) — ce garde TYPE_CHECKING élimine le problème à la racine,
# quel que soit l'ordre dans lequel les modules sont importés ailleurs.
if TYPE_CHECKING:
    from memory.knowledge.schemas import KnowledgeDocument, KnowledgeDocumentMeta
    from memory.oblivia.schemas import (
        KnowledgeFact,
        MemoryQuery,
        MemoryRecord,
        MemorySearchResult,
        MemoryStatus,
    )


# ── Storage Providers ──────────────────────────────────────────────────────
# Persistance brute des souvenirs et des faits. Aucune logique métier ici
# (pas de dédoublonnage, pas d'extraction sémantique) — uniquement lire /
# écrire / chercher dans un backend physique.

@runtime_checkable
class StorageProvider(Protocol):
    """Satisfait aujourd'hui par oblivia.sqlite_adapter.SQLiteMemoryAdapter."""

    def save_record(self, record: MemoryRecord) -> MemoryRecord: ...

    def list_records(self, limit: int = 100) -> list[MemoryRecord]: ...

    def search_records(self, query: str, limit: int = 10) -> list[MemoryRecord]: ...

    def add_fact(self, fact: KnowledgeFact) -> bool: ...

    def list_facts(
        self,
        *,
        subject: str | None = None,
        predicate: str | None = None,
        current_only: bool = False,
        include_retracted: bool = True,
        limit: int = 1000,
    ) -> list[KnowledgeFact]: ...

    def forget(self, query: str) -> int: ...

    def status(self) -> dict[str, int]: ...


# ── Memory Providers ────────────────────────────────────────────────────────
# Moteur mémoire — logique métier au-dessus d'un StorageProvider injecté :
# dédoublonnage, extraction de faits, gestion des types de mémoire.

@runtime_checkable
class MemoryProvider(Protocol):
    """Satisfait aujourd'hui par oblivia.manager.ObliviaMemoryManager.

    Ce contrat correspond exactement aux méthodes que server/memory/app.py
    appelle sur un provider mémoire — pas plus. Un futur provider (Mem0,
    etc.) n'a besoin d'implémenter que ces cinq méthodes pour être
    utilisable en remplacement d'Oblivia.
    """

    def remember(self, record: MemoryRecord) -> MemoryRecord: ...

    def recall(self, query: MemoryQuery) -> list[MemorySearchResult]: ...

    def recall_knowledge(self, query: str, limit: int = 10) -> dict[str, Any]: ...

    def forget(self, query: str) -> dict[str, int]: ...

    def status(self) -> MemoryStatus: ...


# ── Knowledge Providers ──────────────────────────────────────────────────────
# Bases de connaissances consultables — PAS des souvenirs personnels.
# Distinction volontaire : Néron peut lire un document Obsidian sans le
# considérer comme quelque chose que l'utilisateur lui a confié à retenir.
# Cf. server/memory/knowledge/schemas.py pour KnowledgeDocument.

@runtime_checkable
class KnowledgeProvider(Protocol):
    """Satisfait aujourd'hui par knowledge.obsidian_provider.ObsidianKnowledgeProvider."""

    def query(self, text: str, limit: int = 10) -> list[KnowledgeDocument]: ...

    def list_documents(self) -> list[KnowledgeDocumentMeta]: ...

    def status(self) -> dict[str, Any]: ...
