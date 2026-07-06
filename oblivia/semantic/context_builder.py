from ..obsidian_adapter import ObsidianMemoryAdapter
from memory.oblivia.semantic.semantic_search import ObsidianSemanticSearch


class ObsidianContextBuilder:
    def __init__(self, vault_path: str):
        self.memory = ObsidianMemory(vault_path)
        self.semantic = ObsidianSemanticSearch(vault_path)

    def build_context(self, query: str, limit: int = 3) -> str:
        semantic_results = self.semantic.search(query, limit=limit)

        if semantic_results:
            parts = ["Contexte sémantique extrait de la mémoire Obsidian :"]

            for result in semantic_results[:limit]:
                parts.append(
                    f"\n---\n"
                    f"Note: {result['title']}\n"
                    f"Chemin: {result['path']}\n"
                    f"Score: {result['score']}\n"
                    f"Extrait:\n{result['preview']}"
                )

            return "\n".join(parts)

        results = self.memory.search(query, limit=limit)

        if not results:
            words = [
                w for w in query.lower().replace("'", " ").split()
                if len(w) >= 5
                and w not in {
                    "explique", "comment", "pourquoi", "donne", "faire",
                    "avec", "dans", "pour", "neron", "néron"
                }
            ]

            seen = set()
            results = []

            for word in words:
                for item in self.memory.search(word, limit=limit):
                    if item["file"] not in seen:
                        results.append(item)
                        seen.add(item["file"])

                    if len(results) >= limit:
                        break

                if len(results) >= limit:
                    break

        if not results:
            return ""

        parts = ["Contexte texte extrait de la mémoire Obsidian :"]

        for result in results[:limit]:
            parts.append(
                f"\n---\n"
                f"Note: {result['title']}\n"
                f"Chemin: {result['file']}\n"
                f"Extrait:\n{result['preview']}"
            )

        return "\n".join(parts)
