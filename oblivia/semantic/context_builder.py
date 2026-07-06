from __future__ import annotations


def build_context(items) -> str:
    return "\n".join(str(item) for item in items)
