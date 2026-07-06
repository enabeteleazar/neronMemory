from __future__ import annotations


class LocalEmbedder:
    model_name = "local"
    model = None

    def embed(self, text: str) -> list[float]:
        return [1.0 if text else 0.0]
