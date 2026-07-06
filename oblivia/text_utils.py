from __future__ import annotations

import unicodedata


def normalize_text(text: str) -> str:
    normalized = unicodedata.normalize("NFKD", text or "")
    without_accents = "".join(
        char for char in normalized
        if not unicodedata.combining(char)
    )

    return " ".join(without_accents.casefold().split())
