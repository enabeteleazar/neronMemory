from __future__ import annotations

from memory.oblivia.text_utils import normalize_text


class EntityResolver:
    USER_ALIASES = {"je", "moi", "mon", "ma", "mes", "user", "utilisateur"}
    ASSISTANT_ALIASES = {"tu", "toi", "ton", "ta", "tes", "assistant", "neron", "néron"}

    def resolve(self, value: str) -> str:
        normalized = normalize_text(value)
        if normalized in self.USER_ALIASES:
            return "user"
        if normalized in self.ASSISTANT_ALIASES:
            return "assistant"
        return normalized or value
