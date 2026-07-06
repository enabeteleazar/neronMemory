from __future__ import annotations

import re
import unicodedata


def normalize_text(text: str) -> str:
    value = (text or "").casefold()
    value = unicodedata.normalize("NFKD", value)
    value = "".join(char for char in value if not unicodedata.combining(char))
    value = value.replace("’", "'").replace("-", " ")
    value = re.sub(r"[^a-z0-9' ]+", " ", value)
    return " ".join(value.split())


def clean_value(value: str) -> str:
    value = (value or "").strip().strip(" .?!:;,'\"")
    return re.sub(r"\s+", " ", value)
