from pathlib import Path
from datetime import datetime
import json


class ObsidianIndexer:
    def __init__(self, vault_path: str):
        self.vault = Path(vault_path)
        self.index_path = self.vault / "index.json"

    def build_index(self) -> dict:
        notes = []

        for file in self.vault.rglob("*.md"):
            try:
                text = file.read_text(encoding="utf-8")
            except Exception:
                continue

            relative = str(file.relative_to(self.vault))

            notes.append({
                "path": relative,
                "title": file.stem,
                "folder": file.parent.name,
                "size": len(text),
                "preview": text[:300],
                "updated_at": datetime.fromtimestamp(file.stat().st_mtime).isoformat(timespec="seconds"),
            })

        data = {
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "count": len(notes),
            "notes": notes,
        }

        self.index_path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8"
        )

        return data
