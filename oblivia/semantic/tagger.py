import re
import unicodedata


def normalize(text: str) -> str:
    text = text.lower()
    text = unicodedata.normalize("NFD", text)
    return "".join(c for c in text if unicodedata.category(c) != "Mn")


TAG_RULES = {
    "neron": ["neron", "néron"],
    "obsidian": ["obsidian", "vault", "markdown"],
    "ai": ["ia", "intelligence artificielle", "llm", "model", "modèle"],
    "agent": ["agent", "autonome", "assistant"],
    "code": ["code", "python", "script", "git", "commit"],
    "homeassistant": ["home assistant", "ha", "domotique", "lumiere", "lumière"],
    "architecture": ["architecture", "module", "core", "pipeline", "router"],
    "memory": ["memoire", "mémoire", "souvenir", "stocke", "stockage"],
    "task": ["tache", "tâche", "todo", "a faire", "à faire"],
    "security": ["securite", "sécurité", "audit", "vulnerabilite", "vulnérabilité"],
}


def extract_tags(text: str) -> list[str]:
    normalized = normalize(text)
    tags = []

    for tag, keywords in TAG_RULES.items():
        for keyword in keywords:
            if normalize(keyword) in normalized:
                tags.append(tag)
                break

    return sorted(set(tags))


def classify_folder(text: str) -> str:
    normalized = normalize(text)

    if any(k in normalized for k in ["bug", "erreur", "traceback", "exception"]):
        return "Bugs"

    if any(k in normalized for k in ["architecture", "module", "pipeline", "router", "core"]):
        return "architecture"

    if any(k in normalized for k in ["tache", "todo", "a faire", "à faire"]):
        return "Tasks"

    if any(k in normalized for k in ["recherche", "veille", "documentation", "doc"]):
        return "Research"

    if any(k in normalized for k in ["projet", "roadmap", "version", "v2", "v3"]):
        return "Projects"

    return "ideas"
