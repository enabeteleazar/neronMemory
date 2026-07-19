from __future__ import annotations


def predicate_from_label(label: str) -> str:
    return "_".join((label or "").strip().lower().split())
