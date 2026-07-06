import hashlib
import numpy as np


class LocalEmbedder:
    def __init__(self, model_name: str = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"):
        self.model_name = model_name
        self.model = None
        self.dimension = 384

        try:
            from sentence_transformers import SentenceTransformer
            self.model = SentenceTransformer(model_name)
        except Exception:
            self.model = None

    def embed(self, text: str) -> list[float]:
        text = text.strip()

        if self.model:
            vector = self.model.encode(text, normalize_embeddings=True)
            return vector.astype(float).tolist()

        return self._fallback_embedding(text)

    def _fallback_embedding(self, text: str) -> list[float]:
        vector = np.zeros(self.dimension, dtype=float)

        words = text.lower().split()

        for word in words:
            h = int(hashlib.sha256(word.encode("utf-8")).hexdigest(), 16)
            index = h % self.dimension
            vector[index] += 1.0

        norm = np.linalg.norm(vector)

        if norm > 0:
            vector = vector / norm

        return vector.tolist()
