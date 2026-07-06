"""User-facing timeline projections that never mutate audit facts."""

from __future__ import annotations

import re
import unicodedata
from typing import TypeVar

from .schemas import KnowledgeFact

FactT = TypeVar("FactT", bound=KnowledgeFact)


def _normalize(value: str) -> str:
    text = unicodedata.normalize("NFKD", value.casefold())
    text = "".join(char for char in text if not unicodedata.combining(char))
    return " ".join(re.sub(r"[^a-z0-9 ]+", " ", text).split())


def project_unique_timeline(facts: list[FactT]) -> list[FactT]:
    """Keep the first chronological occurrence of each normalized value.

    Callers sort on ``valid_from``/``valid_to`` before this projection. The
    input facts and SQLite audit rows are never modified.
    """
    projected: list[FactT] = []
    seen: set[str] = set()
    for fact in facts:
        value = _normalize(fact.object)
        if value in seen:
            continue
        seen.add(value)
        projected.append(fact)
    return projected
