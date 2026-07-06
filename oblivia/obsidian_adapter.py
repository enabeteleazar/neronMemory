from __future__ import annotations

from pathlib import Path

from .schemas import MemoryRecord
from .text_utils import normalize_text

DEFAULT_VAULT = "/etc/neronOS/server/memory/obsidian"


class ObsidianMemoryAdapter:
    def __init__(self, vault_path: str = DEFAULT_VAULT):
        self.vault = Path(vault_path)
        self._init_vault()

    def _init_vault(self):
        folders = [
            "identity",
            "decisions",
            "roadmap",
            "agents",
            "lessons",
            "journal",
        ]

        for folder in folders:
            (self.vault / folder).mkdir(parents=True, exist_ok=True)

    def add(self, record: MemoryRecord):
        folder = self._folder_for_category(record.category)
        target_dir = self.vault / folder
        target_dir.mkdir(parents=True, exist_ok=True)

        target = target_dir / f"{record.id}.md"

        target.write_text(
            self._record_to_markdown(record),
            encoding="utf-8",
        )

        return target

    def search(self, query: str, limit: int = 10):
        results = []
        needle = normalize_text(query)

        if not needle:
            return results

        for md in self.vault.rglob("*.md"):
            try:
                content = md.read_text(encoding="utf-8")
            except Exception:
                continue

            haystack = normalize_text(content)
            if needle not in haystack:
                continue

            results.append(
                {
                    "path": str(md),
                    "content": content[:2000],
                    "score": self._score(content, needle),
                }
            )

            if len(results) >= limit:
                break

        return sorted(results, key=lambda item: item["score"], reverse=True)

    def status(self):
        files = len(list(self.vault.rglob("*.md")))

        return {
            "backend": "obsidian",
            "vault": str(self.vault),
            "files": files,
        }

    def _folder_for_category(self, category: str) -> str:
        mapping = {
            "self": "identity",
            "project": "roadmap",
            "decision": "decisions",
            "lesson": "lessons",
            "agent": "agents",
        }

        return mapping.get(category, "journal")

    def _record_to_markdown(self, record: MemoryRecord) -> str:
        return f"""# {record.category}

{record.content}

---

id: {record.id}
source: {record.source}
category: {record.category}
created_at: {record.created_at}
updated_at: {record.updated_at}
metadata: {record.metadata}
"""

    def _score(self, content: str, query: str) -> float:
        text = normalize_text(content)
        needle = normalize_text(query)
        count = text.count(needle)

        if count <= 0:
            return 0.0

        return min(1.0, 0.2 + (count * 0.2))
