"""server/memory/knowledge/obsidian_provider.py

Implémentation réelle du KnowledgeProvider pour un vault Obsidian (un
répertoire de fichiers Markdown). Remplace oblivia/obsidian_adapter.py,
qui était un stub vide (__init__ ne faisant rien, jamais branché nulle
part) — le vault existe pourtant déjà sur disque
(server/memory/obsidian/identity/NERON.md) et n'était jusqu'ici jamais lu.

Recherche par mots-clés simple, dans le même esprit que
SQLiteMemoryAdapter.search_records : pas d'embeddings, pas de dépendance
ML — cohérent avec le reste du service. À remplacer par une recherche
vectorielle si le besoin s'en fait sentir (le protocole KnowledgeProvider
n'a pas besoin de changer pour ça, seule cette classe serait réécrite).
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

from memory.knowledge.schemas import KnowledgeDocument, KnowledgeDocumentMeta
from memory.text_utils import normalize_text

logger = logging.getLogger("memory.knowledge.obsidian")

_SNIPPET_RADIUS = 80


class ObsidianKnowledgeProvider:
    """KnowledgeProvider pour un vault Obsidian.

    Satisfait memory.protocols.KnowledgeProvider par typage structurel
    (aucun héritage requis).
    """

    def __init__(self, vault_path: str | Path) -> None:
        self.vault_path = Path(vault_path)
        self.vault_path.mkdir(parents=True, exist_ok=True)

    def _iter_files(self) -> Iterator[Path]:
        return self.vault_path.rglob("*.md")

    def _read(self, path: Path) -> str | None:
        try:
            return path.read_text(encoding="utf-8")
        except OSError as exc:
            logger.warning("obsidian | unreadable file=%s error=%s", path, exc)
            return None

    def _title_of(self, path: Path, content: str) -> str:
        for line in content.splitlines():
            stripped = line.strip()
            if stripped.startswith("#"):
                return stripped.lstrip("#").strip() or path.stem
        return path.stem

    def _snippet(self, content: str, terms: list[str]) -> str:
        """Extrait un passage autour du premier terme trouvé.

        Recherche insensible à la casse mais PAS aux accents (contrairement
        à normalize_text utilisé pour le score) : chercher la position d'un
        match dans une version normalisée puis découper la chaîne
        originale à ces indices serait incorrect, car la normalisation
        peut changer la longueur de la chaîne (accents, ponctuation).
        Limitation acceptée pour un v1 : un document accentué différemment
        du terme recherché retombe sur l'extrait par défaut ci-dessous,
        jamais sur un texte tronqué au mauvais endroit.
        """
        for term in terms:
            match = re.search(re.escape(term), content, re.IGNORECASE)
            if match:
                start = max(0, match.start() - _SNIPPET_RADIUS)
                end = min(len(content), match.end() + _SNIPPET_RADIUS)
                return content[start:end].strip().replace("\n", " ")
        return content[:160].strip().replace("\n", " ")

    def query(self, text: str, limit: int = 10) -> list[KnowledgeDocument]:
        terms = [t for t in normalize_text(text).split() if t]
        if not terms:
            return []

        scored: list[tuple[int, KnowledgeDocument]] = []
        for path in self._iter_files():
            content = self._read(path)
            if content is None:
                continue
            haystack = normalize_text(content)
            hits = sum(haystack.count(term) for term in terms)
            if hits == 0:
                continue
            doc = KnowledgeDocument(
                path=str(path.relative_to(self.vault_path)),
                title=self._title_of(path, content),
                snippet=self._snippet(content, terms),
                score=float(hits),
            )
            scored.append((hits, doc))

        scored.sort(key=lambda pair: pair[0], reverse=True)
        return [doc for _, doc in scored[:limit]]

    def list_documents(self) -> list[KnowledgeDocumentMeta]:
        docs: list[KnowledgeDocumentMeta] = []
        for path in self._iter_files():
            content = self._read(path)
            if content is None:
                continue
            try:
                modified_at = datetime.fromtimestamp(
                    path.stat().st_mtime, tz=timezone.utc
                ).isoformat()
            except OSError:
                modified_at = ""
            docs.append(
                KnowledgeDocumentMeta(
                    path=str(path.relative_to(self.vault_path)),
                    title=self._title_of(path, content),
                    modified_at=modified_at,
                )
            )
        return docs

    def status(self) -> dict[str, object]:
        try:
            count = sum(1 for _ in self._iter_files())
        except OSError as exc:
            return {"ok": False, "documents": 0, "error": str(exc)}
        return {"ok": True, "documents": count}
