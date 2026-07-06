from __future__ import annotations

import hashlib
import re
from typing import List


class SemanticChunker:
    def __init__(
        self,
        min_size: int = 200,
        max_size: int = 1200,
    ):
        self.min_size = min_size
        self.max_size = max_size

    def chunk_markdown(
        self,
        content: str,
        source: str = "",
    ) -> List[dict]:

        sections = self._split_sections(content)

        chunks = []

        for idx, section in enumerate(sections):
            cleaned = section.strip()

            if not cleaned:
                continue

            if len(cleaned) < self.min_size:
                continue

            if len(cleaned) > self.max_size:
                sub_chunks = self._split_large_chunk(cleaned)

                for sub_idx, sub in enumerate(sub_chunks):
                    chunks.append(
                        self._build_chunk(
                            sub,
                            source,
                            f"{idx}_{sub_idx}",
                        )
                    )

            else:
                chunks.append(
                    self._build_chunk(
                        cleaned,
                        source,
                        str(idx),
                    )
                )

        return chunks

    def _split_sections(self, content: str) -> List[str]:
        pattern = r"(?=^#{1,6}\s)"
        sections = re.split(pattern, content, flags=re.MULTILINE)

        if not sections:
            return [content]

        return sections

    def _split_large_chunk(self, text: str) -> List[str]:
        paragraphs = text.split("\n\n")

        chunks = []
        current = ""

        for paragraph in paragraphs:
            if len(current) + len(paragraph) < self.max_size:
                current += "\n\n" + paragraph
            else:
                chunks.append(current.strip())
                current = paragraph

        if current.strip():
            chunks.append(current.strip())

        return chunks

    def _build_chunk(
        self,
        content: str,
        source: str,
        chunk_id: str,
    ) -> dict:

        sha = hashlib.sha256(
            content.encode("utf-8")
        ).hexdigest()

        return {
            "id": f"{source}:{chunk_id}",
            "source": source,
            "content": content,
            "hash": sha,
            "size": len(content),
        }
