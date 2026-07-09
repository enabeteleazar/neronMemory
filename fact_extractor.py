from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from memory.text_utils import clean_value, normalize_text


@dataclass
class ExtractedFact:
    subject: str
    relation: str
    object: str
    confidence: float = 1.0
    metadata: dict[str, Any] = field(default_factory=dict)


class FactExtractor:
    """Rule-based French fact extractor for high-confidence personal memory."""

    def extract(self, text: str) -> list[ExtractedFact]:
        original = clean_value(self._strip_directive(text))
        value = normalize_text(original)
        facts: list[ExtractedFact] = []

        denial = re.match(r".*(?:jamais habite a|n'habitais pas a)\s+(.+)$", value)
        if denial:
            return [ExtractedFact("user", "lives_at", self._title_from(original, denial.group(1)), metadata={"retract": True})]

        no_longer_likes = re.match(r"je n'aime plus\s+(.+)$", value)
        if no_longer_likes:
            return [ExtractedFact("user", "likes", clean_value(no_longer_likes.group(1)), metadata={"retract": True})]

        if re.match(r"je suis ton createur$", value):
            return [ExtractedFact("assistant", "creator", "Eléazar Nabet", 0.98)]

        children = re.match(r"(?:j'ai (?:[0-9]+|un|une|deux|trois|quatre|cinq|six|sept|huit|neuf|dix) enfants?|mes enfants (?:s'appellent|sont))\s*:?\s*(.+)$", value)
        if children:
            names = [clean_value(part.title()) for part in re.split(r",|\set\s", self._tail_original(original)) if clean_value(part)]
            if names:
                for name in names:
                    facts.append(ExtractedFact("user", "has_child", name))
                    facts.append(ExtractedFact(name, "relation_to_user", "child"))
                facts.append(ExtractedFact("user", "children_count", str(len(names))))
                return facts

        patterns: tuple[tuple[str, str, str, dict[str, Any]], ...] = (
            (r"je m'appelle\s+(.+)$", "user", "name", {}),
            (r"je suis\s+(.+)$", "user", "is", {}),
            (r"ma femme s'appelle\s+(.+)$", "user", "spouse", {}),
            (r"mon fils s'appelle\s+(.+)$", "user.son", "name", {}),
            (r"j'habite(?: maintenant)? a\s+(.+)$", "user", "lives_at", {}),
            (r"j'ai habite a\s+(.+?)\s+il y a\s+([0-9]+)\s+ans?$", "user", "lives_at", {"historical": True}),
            (r"(?:j'habitais a|avant j'habitais a)\s+(.+?)(?:\s+avant)?$", "user", "lives_at", {"historical": True}),
            (r"je travaille(?: maintenant)? chez\s+(.+)$", "user", "works_at", {}),
            (r"j'aime\s+(.+)$", "user", "likes", {}),
            (r"ma couleur preferee est\s+(.+)$", "user", "favorite_color", {}),
            (r"mon chien s'appelle\s+(.+)$", "user.dog", "name", {}),
        )
        for pattern, subject, relation, metadata in patterns:
            match = re.match(pattern, value)
            if match:
                obj = self._object_for_relation(original, relation, match.group(1))
                meta = dict(metadata)
                if relation == "lives_at" and "il y a" in value:
                    years = re.search(r"il y a\s+([0-9]+)", value)
                    if years:
                        meta.update({"date_approximate": True, "years_ago": int(years.group(1))})
                facts.append(ExtractedFact(subject, relation, obj, metadata=meta))
                return facts

        replacement = re.match(r"j'ai remplace\s+(.+?)\s+par\s+(.+)$", value)
        if replacement:
            item = re.sub(r"^(?:mon|ma|mes)\s+", "", replacement.group(1)).strip()
            return [ExtractedFact("user", self._object_relation(item), self._title_from(original, replacement.group(2)))]

        owned = re.match(r"j'ai (?:un|une)\s+(.+)$", value)
        if owned:
            obj = clean_value(owned.group(1))
            if "chien" in obj:
                return [ExtractedFact("user", "owns", obj)]
            return [ExtractedFact("user", "vehicle", obj)]

        possessive = re.match(r"(mon|ma|mes)\s+(.+?)\s+(?:est|sont)(?: maintenant)?\s+(.+)$", value)
        if possessive:
            item = clean_value(possessive.group(2))
            obj = self._original_after_state_verb(original)
            if item == "camion":
                return [
                    ExtractedFact("user", "vehicle", f"camion {obj}", metadata={"label": item}),
                    ExtractedFact("user", "camion", obj, metadata={"label": item}),
                ]
            return [ExtractedFact("user", self._object_relation(item), obj, metadata={"label": item})]

        return facts

    def _strip_directive(self, text: str) -> str:
        return re.sub(r"(?i)^(?:retiens|m[ée]morise|souviens-toi|note)\s+que\s+", "", text or "").strip()

    def _tail_original(self, text: str) -> str:
        return re.split(r":|s'appellent|sont", text, maxsplit=1)[-1]

    def _title_from(self, original: str, normalized_tail: str) -> str:
        tail = clean_value(normalized_tail)
        if not tail:
            return tail
        words = tail.split()
        for size in range(len(words), 0, -1):
            needle = " ".join(words[:size])
            pattern = re.compile(re.escape(needle).replace("\\ ", r"[\s-]+"), re.IGNORECASE)
            match = pattern.search(original)
            if match:
                return clean_value(match.group(0))
        return tail

    def _original_after_state_verb(self, original: str) -> str:
        match = re.search(r"\s(?:est|sont)(?:\s+maintenant)?\s+(.+)$", original, re.IGNORECASE)
        return clean_value(match.group(1)) if match else original

    def _object_for_relation(self, original: str, relation: str, fallback: str) -> str:
        phrase_by_relation = {
            "name": r"m[’']appelle\s+(.+)$",
            "spouse": r"femme s[’']appelle\s+(.+)$",
            "lives_at": r"(?:habite(?: maintenant)?|habité|habite)\s+[àa]\s+(.+?)(?:\s+il y a\s+[0-9]+\s+ans?)?$",
            "works_at": r"travaille(?: maintenant)? chez\s+(.+)$",
            "likes": r"J[’']aime\s+(.+)$",
            "favorite_color": r"couleur préférée est\s+(?:le |la |l’|l')?(.+)$",
            "is": r"Je suis\s+(.+)$",
        }
        pattern = phrase_by_relation.get(relation)
        if pattern:
            match = re.search(pattern, original, re.IGNORECASE)
            if match:
                return clean_value(match.group(1))
        return self._title_from(original, fallback)

    def _object_relation(self, item: str) -> str:
        item = clean_value(item)
        aliases = {
            "telephone": "smartphone",
            "smartphone": "smartphone",
            "camion": "vehicle",
            "vehicule": "vehicle",
            "couleur preferee": "favorite_color",
            "systeme de conteneurs": "container_system",
            "systeme d'exploitation": "operating_system",
        }
        return aliases.get(item, item.replace(" ", "_"))
