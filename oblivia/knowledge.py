from __future__ import annotations

import re
import unicodedata

from .schemas import KnowledgeFact
from .predicate_discovery import PredicateDiscovery
from .timeline import project_unique_timeline


def normalize(value: str) -> str:
    text = unicodedata.normalize("NFKD", value.casefold())
    text = "".join(char for char in text if not unicodedata.combining(char))
    text = text.replace("’", "'")
    return " ".join(re.sub(r"[^a-z0-9' ]+", " ", text).split())


class KnowledgeExtractor:
    """Deterministic foundation for explicit user facts."""

    _memory_directive = re.compile(
        r"^(?:"
        r"retiens\s+que|"
        r"m[ée]morise\s+que|"
        r"souviens[- ]toi\s+que|"
        r"note\s+que|"
        r"garde\s+en\s+m[ée]moire\s+que"
        r")\s+",
        re.IGNORECASE,
    )

    _patterns = (
        (
            re.compile(
                r"^je suis n[ée] le\s+(.+?)[.!?]*$",
                re.IGNORECASE,
            ),
            lambda match: ("user", "birth_date", match.group(1)),
        ),
        (
            re.compile(r"^je suis\s+(.+?)[.!?]*$", re.IGNORECASE),
            lambda match: ("user", "is", match.group(1)),
        ),
        (
            re.compile(r"^je m['’]appelle\s+(.+?)[.!?]*$", re.IGNORECASE),
            lambda match: ("user", "name", match.group(1)),
        ),
        (
            re.compile(
                r"^ma femme s['’]appelle\s+(.+?)[.!?]*$",
                re.IGNORECASE,
            ),
            lambda match: ("user", "spouse", match.group(1)),
        ),
        (
            re.compile(
                r"^mon fils s['’]appelle\s+(.+?)[.!?]*$",
                re.IGNORECASE,
            ),
            lambda match: ("user.son", "name", match.group(1)),
        ),
        (
            re.compile(
                r"^j['’]habite (?:maintenant )?[àa]\s+(.+?)[.!?]*$",
                re.IGNORECASE,
            ),
            lambda match: ("user", "lives_at", match.group(1)),
        ),
        (
            re.compile(r"^j['’]aime\s+(.+?)[.!?]*$", re.IGNORECASE),
            lambda match: ("user", "likes", match.group(1)),
        ),
        (
            re.compile(
                r"^je travaille (?:maintenant )?chez\s+(.+?)[.!?]*$",
                re.IGNORECASE,
            ),
            lambda match: ("user", "works_at", match.group(1)),
        ),
    )

    def __init__(self) -> None:
        self.predicate_discovery = PredicateDiscovery()

    def extract(self, text: str, *, source: str = "user") -> list[KnowledgeFact]:
        value = self._memory_directive.sub("", text.strip()).strip()
        temporal = self._extract_temporal_lives_at(value, source=source)
        if temporal:
            return temporal
        correction = self._extract_correction(value, source=source)
        if correction:
            return correction
        preference = self._extract_preference_change(value, source=source)
        if preference:
            return preference
        incremental_child = re.match(
            r"^j['’]ai aussi un enfant,\s*(?P<name>.+?)[.!?]*$",
            value,
            re.IGNORECASE,
        )
        if incremental_child:
            name = incremental_child.group("name").strip().rstrip(".!?")
            metadata = {"collection": "children", "position": 10_000}
            return [
                KnowledgeFact(
                    subject="user",
                    predicate="has_child",
                    object=name,
                    source=source,
                    raw_text=value,
                    metadata=metadata,
                ),
                KnowledgeFact(
                    subject=name,
                    predicate="relation_to_user",
                    object="child",
                    source=source,
                    raw_text=value,
                    metadata=metadata,
                ),
            ]
        collection = self._extract_children(value, source=source)
        if collection:
            return collection
        for pattern, builder in self._patterns:
            match = pattern.match(value)
            if match:
                subject, predicate, object_value = builder(match)
                return [
                    KnowledgeFact(
                        subject=subject,
                        predicate=predicate,
                        object=object_value.strip().rstrip(".!?"),
                        source=source,
                        raw_text=value,
                    )
                ]
        return self.predicate_discovery.extract(value, source=source)

    @staticmethod
    def _extract_correction(
        value: str,
        *,
        source: str,
    ) -> list[KnowledgeFact]:
        name = re.match(
            r"^en fait je m['’]appelle\s+(?P<value>.+?)"
            r"(?:,\s*pas\s+.+)?[.!?]*$",
            value,
            re.IGNORECASE,
        )
        if name:
            return [
                KnowledgeFact(
                    subject="user",
                    predicate="name",
                    object=name.group("value").strip().rstrip(".!?"),
                    source=source,
                    raw_text=value,
                )
            ]
        return []

    @staticmethod
    def _extract_preference_change(
        value: str,
        *,
        source: str,
    ) -> list[KnowledgeFact]:
        retraction = re.match(
            r"^je n['’]aime plus\s+(?P<value>.+?)[.!?]*$",
            value,
            re.IGNORECASE,
        )
        if retraction:
            return [
                KnowledgeFact(
                    subject="user",
                    predicate="likes",
                    object=retraction.group("value").strip().rstrip(".!?"),
                    source=source,
                    raw_text=value,
                    metadata={
                        "temporal_operation": "retraction",
                        "retraction_reason": "preference_withdrawn",
                    },
                )
            ]
        reactivation = re.match(
            r"^en fait j['’]aime [àa] nouveau\s+"
            r"(?P<value>.+?)[.!?]*$",
            value,
            re.IGNORECASE,
        )
        if reactivation:
            return [
                KnowledgeFact(
                    subject="user",
                    predicate="likes",
                    object=reactivation.group("value").strip().rstrip(".!?"),
                    source=source,
                    raw_text=value,
                )
            ]
        return []

    @staticmethod
    def _extract_temporal_lives_at(
        value: str,
        *,
        source: str,
    ) -> list[KnowledgeFact]:
        retraction = re.match(
            r"^(?:ce n['’]est pas vrai,\s*)?"
            r"(?:je n['’]ai jamais habit[ée] [àa]|"
            r"je n['’]habitais pas [àa])\s+"
            r"(?P<place>.+?)[.!?]*$",
            value,
            re.IGNORECASE,
        )
        if retraction:
            return [
                KnowledgeFact(
                    subject="user",
                    predicate="lives_at",
                    object=retraction.group("place").strip().rstrip(".!?"),
                    source=source,
                    raw_text=value,
                    metadata={
                        "temporal_operation": "retraction",
                        "retraction_reason": "user_denial",
                    },
                )
            ]

        years_ago = re.match(
            r"^j['’]ai habit[ée] [àa]\s+(?P<place>.+?)\s+"
            r"il y a\s+(?P<years>\d+)\s+ans?[.!?]*$",
            value,
            re.IGNORECASE,
        )
        past = re.match(
            r"^(?:j['’]habitais [àa]\s+(?P<place_a>.+?)\s+avant|"
            r"avant,\s*j['’]habitais [àa]\s+(?P<place_b>.+?))[.!?]*$",
            value,
            re.IGNORECASE,
        )
        if not years_ago and not past:
            return []

        if years_ago:
            place = years_ago.group("place")
            metadata = {
                "temporal_operation": "historical_assertion",
                "date_approximate": True,
                "years_ago": int(years_ago.group("years")),
            }
        else:
            place = past.group("place_a") or past.group("place_b")
            metadata = {
                "temporal_operation": "historical_assertion",
                "date_approximate": True,
                "relative_period": "before",
            }
        return [
            KnowledgeFact(
                subject="user",
                predicate="lives_at",
                object=place.strip().rstrip(".!?"),
                source=source,
                raw_text=value,
                metadata=metadata,
            )
        ]

    @staticmethod
    def _extract_children(
        value: str,
        *,
        source: str,
    ) -> list[KnowledgeFact]:
        count_match = re.match(
            r"^j['’]ai\s+"
            r"(?P<count>\d+|un|une|deux|trois|quatre|cinq|six|sept|huit|neuf|dix)"
            r"\s+enfants?\s*:\s*(?P<names>.+?)[.!?]*$",
            value,
            re.IGNORECASE,
        )
        names_match = re.match(
            r"^mes enfants (?:s['’]appellent|sont)\s+"
            r"(?P<names>.+?)[.!?]*$",
            value,
            re.IGNORECASE,
        )
        match = count_match or names_match
        if not match:
            return []

        names = [
            name.strip()
            for name in re.split(r"\s*,\s*|\s+et\s+", match.group("names"))
            if name.strip()
        ]
        if not names:
            return []

        facts: list[KnowledgeFact] = []
        for index, name in enumerate(names):
            metadata = {"collection": "children", "position": index}
            facts.extend(
                (
                    KnowledgeFact(
                        subject="user",
                        predicate="has_child",
                        object=name,
                        source=source,
                        raw_text=value,
                        metadata=metadata,
                    ),
                    KnowledgeFact(
                        subject=name,
                        predicate="relation_to_user",
                        object="child",
                        source=source,
                        raw_text=value,
                        metadata=metadata,
                    ),
                )
            )

        if count_match:
            number_words = {
                "un": 1,
                "une": 1,
                "deux": 2,
                "trois": 3,
                "quatre": 4,
                "cinq": 5,
                "six": 6,
                "sept": 7,
                "huit": 8,
                "neuf": 9,
                "dix": 10,
            }
            raw_count = count_match.group("count").casefold()
            count = int(raw_count) if raw_count.isdigit() else number_words[raw_count]
            facts.append(
                KnowledgeFact(
                    subject="user",
                    predicate="children_count",
                    object=str(count),
                    source=source,
                    raw_text=value,
                )
            )
        return facts


def natural_answer(question: str, facts: list[KnowledgeFact]) -> str | None:
    query = normalize(question)

    past_residence = (
        "ou est ce que j'habitais avant" in query
        or "ou est ce que j habitais avant" in query
        or "ou habitais je avant" in query
    )
    residence_history = (
        "ou ai je vecu" in query
        or "dans quelles villes ai je vecu" in query
    )
    if past_residence:
        previous = sorted(
            (
                item
                for item in facts
                if item.predicate == "lives_at"
                and not bool(getattr(item, "is_current", True))
            ),
            key=lambda item: (
                getattr(item, "valid_to", None)
                or getattr(item, "valid_from", "")
            ),
            reverse=True,
        )
        if previous:
            return f"Avant, tu habitais à {previous[0].object}."
        return "Je ne connais pas de lieu où tu habitais avant."

    if residence_history:
        timeline = sorted(
            (item for item in facts if item.predicate == "lives_at"),
            key=_lives_at_order_key,
        )
        timeline = project_unique_timeline(timeline)
        if timeline:
            return "Tu as vécu " + _join_timeline(
                [item.object for item in timeline]
            ) + "."
        return "Je ne connais aucun lieu où tu as vécu."

    current_residence = (
        "ou est ce que j'habite" in query
        or "ou est ce que j habite" in query
    )
    if current_residence:
        current = next(
            (
                item
                for item in facts
                if item.predicate == "lives_at"
                and bool(getattr(item, "is_current", False))
                and not bool(getattr(item, "retracted", False))
            ),
            None,
        )
        if current:
            return f"Tu habites à {current.object}."
        return "Je ne connais pas ton lieu de résidence actuel."

    if not facts:
        if (
            "comment je m'appelais avant" in query
            or "comment je m appelais avant" in query
        ):
            return "Je ne connais pas de prénom antérieur."
        if "ou travaillais je avant" in query:
            return "Je ne connais pas d’employeur antérieur."
        if "comment s appelait ma femme avant" in query:
            return "Je ne connais pas de conjointe antérieure."
        return None

    children = sorted(
        (
            item
            for item in facts
            if item.subject == "user" and item.predicate == "has_child"
        ),
        key=lambda item: int(item.metadata.get("position", 0)),
    )
    if "combien ai je d'enfants" in query or "combien ai je d enfants" in query:
        count_fact = next(
            (
                item
                for item in facts
                if item.subject == "user"
                and item.predicate == "children_count"
            ),
            None,
        )
        count = int(count_fact.object) if count_fact else len(children)
        number_words = {
            0: "zéro",
            1: "un",
            2: "deux",
            3: "trois",
            4: "quatre",
            5: "cinq",
            6: "six",
            7: "sept",
            8: "huit",
            9: "neuf",
            10: "dix",
        }
        rendered = number_words.get(count, str(count))
        return f"Tu as {rendered} enfant{'s' if count != 1 else ''}."

    if (
        "comment s'appellent mes enfants" in query
        or "comment s appellent mes enfants" in query
        or "qui sont mes enfants" in query
    ) and children:
        names = _join_french([item.object for item in children])
        if "qui sont" in query:
            return f"Tes enfants sont {names}."
        return f"Tes enfants s'appellent {names}."

    inverse = next(
        (
            item
            for item in facts
            if item.predicate == "relation_to_user"
            and item.object == "child"
            and query in {f"qui est {normalize(item.subject)}"}
        ),
        None,
    )
    if inverse:
        return f"{inverse.subject} est l'un de tes enfants."

    if "qui est papa" in query:
        fact = next(
            (
                item
                for item in facts
                if item.subject == "user"
                and item.predicate == "is"
                and normalize(item.object) == "papa"
            ),
            None,
        )
        if fact:
            return "Papa, c'est toi."

    if (
        "comment s'appelle ma femme" in query
        or "comment s appelle ma femme" in query
    ):
        fact = next(
            (
                item
                for item in facts
                if item.subject == "user" and item.predicate == "spouse"
            ),
            None,
        )
        if fact:
            return f"Ta femme s'appelle {fact.object}."

    if "comment s appelait ma femme avant" in query:
        fact = next(
            (
                item
                for item in facts
                if item.subject == "user"
                and item.predicate == "spouse"
                and not bool(getattr(item, "is_current", True))
            ),
            None,
        )
        if fact:
            return f"Avant, ta femme s'appelait {fact.object}."

    if (
        "comment je m'appelle" in query
        or "comment je m appelle" in query
    ):
        fact = next(
            (
                item
                for item in facts
                if item.subject == "user" and item.predicate == "name"
            ),
            None,
        )
        if fact:
            return f"Tu t'appelles {fact.object}."

    if (
        "comment je m'appelais avant" in query
        or "comment je m appelais avant" in query
    ):
        fact = next(
            (
                item
                for item in facts
                if item.subject == "user"
                and item.predicate == "name"
                and not bool(getattr(item, "is_current", True))
            ),
            None,
        )
        if fact:
            return f"Avant, tu t'appelais {fact.object}."

    if "qui est mon fils" in query:
        fact = next(
            (
                item
                for item in facts
                if item.subject == "user.son" and item.predicate == "name"
            ),
            None,
        )
        if fact:
            return f"Ton fils s'appelle {fact.object}."

    if "ou est ce que je travaille" in query:
        fact = next(
            (
                item
                for item in facts
                if item.subject == "user" and item.predicate == "works_at"
            ),
            None,
        )
        if fact:
            return f"Tu travailles chez {fact.object}."

    if "ou travaillais je avant" in query:
        fact = next(
            (
                item
                for item in facts
                if item.subject == "user"
                and item.predicate == "works_at"
                and not bool(getattr(item, "is_current", True))
            ),
            None,
        )
        if fact:
            return f"Avant, tu travaillais chez {fact.object}."

    if "j'aime quoi" in query or "j aime quoi" in query:
        preference_facts = sorted(
            (
                item
                for item in facts
                if item.subject == "user"
                and item.predicate == "likes"
                and bool(getattr(item, "is_current", True))
                and not bool(getattr(item, "retracted", False))
            ),
            key=lambda item: item.created_at,
        )
        preferences = [
            item.object
            for item in preference_facts
        ]
        if preferences:
            return f"Tu aimes {_join_french(preferences)}."
        return "Je ne connais aucune de tes préférences actuelles."

    if any(
        value in query
        for value in (
            "qu'est ce que j'aime boire",
            "que j'aime boire",
            "qu est ce que j aime boire",
            "que j aime boire",
        )
    ):
        fact = next(
            (
                item
                for item in facts
                if item.subject == "user" and item.predicate == "likes"
            ),
            None,
        )
        if fact:
            return f"Tu apprécies {fact.object}."

    return None


def _join_french(values: list[str]) -> str:
    if len(values) < 2:
        return values[0] if values else ""
    return ", ".join(values[:-1]) + f" et {values[-1]}"


def _join_timeline(values: list[str]) -> str:
    if len(values) < 2:
        return f"à {values[0]}" if values else ""
    return ", puis à ".join(
        [f"à {values[0]}", *values[1:]]
    )


def _lives_at_order_key(item: KnowledgeFact) -> tuple[int, object]:
    years_ago = item.metadata.get("years_ago")
    if isinstance(years_ago, int):
        return (0, -years_ago)
    if item.metadata.get("relative_period") == "before":
        return (0, 0)
    return (1, getattr(item, "valid_from", item.created_at))
