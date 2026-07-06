"""Deterministic reasoning over Oblivia's structured user facts."""

from __future__ import annotations

import re
import unicodedata
from typing import TYPE_CHECKING

from .ontology import PREDICATES
from .predicate_discovery import DEVICE_SLOTS, canonical_slot
from .schemas import LifecycleKnowledgeFact
from .timeline import project_unique_timeline

if TYPE_CHECKING:
    from .sqlite_adapter import SQLiteMemoryAdapter


def _normalize(value: str) -> str:
    text = unicodedata.normalize("NFKD", value.casefold())
    text = "".join(char for char in text if not unicodedata.combining(char))
    return " ".join(re.sub(r"[^a-z0-9 ]+", " ", text).split())


def _join_french(values: list[str]) -> str:
    if len(values) < 2:
        return values[0] if values else ""
    return ", ".join(values[:-1]) + f" et {values[-1]}"


def _timeline_key(fact: LifecycleKnowledgeFact) -> tuple[str, str]:
    return (fact.valid_from, fact.valid_to or "")


class MemoryReasoner:
    """Answer aggregate and semantic-alias questions without an LLM."""

    _profile_questions = {
        "qui suis je",
        "parle moi de moi",
        "presente moi",
        "que sais tu de moi",
        "fais un resume de ce que tu sais sur moi",
        "tu me connais bien",
        "est ce que tu te souviens de moi",
        "as tu appris des choses sur moi",
        "qu est ce que tu retiens principalement de moi",
        "qu est ce qui me caracterise",
        "que pourrais tu raconter sur moi a quelqu un",
        "si tu devais me presenter que dirais tu",
    }
    _spouse_questions = {"qui est ma femme", "qui est mon epouse"}
    _works_history_questions = {"ou ai je travaille"}
    _likes_current_questions = {
        "qu est ce que j aime",
        "j aime quoi",
    }
    _likes_history_questions = {"qu est ce que j aimais avant"}
    _audit_questions = {
        "montre moi tout ce que tu sais",
        "montre toute ma memoire",
        "quels souvenirs possedes tu",
    }
    _count_questions = {"combien de souvenirs as tu sur moi"}
    _predicate_questions = {"quels predicats connais tu sur moi"}
    _category_questions = {"quels types d informations connais tu"}
    _stale_questions = {
        "quels anciens souvenirs possedes tu",
        "quelles informations ne sont plus actuelles",
        "quelles informations sont obsoletes",
        "y a t il des donnees obsoletes",
    }
    _retracted_questions = {"y a t il des souvenirs retractes"}
    _conflict_questions = {
        "ai je des informations contradictoires",
        "as tu detecte des conflits",
        "as tu des informations douteuses",
    }
    _family_questions = {
        "qui fait partie de ma famille",
        "qui partage ma vie",
        "qui fait partie de mon foyer",
        "qui est lie a moi",
    }
    _dependant_questions = {"qui depend de moi"}
    _household_questions = {"qui habite avec moi"}
    _moving_questions = {
        "si je demenage qui demenage probablement avec moi",
    }
    _latest_questions = {
        "quelle est la derniere chose importante que tu as apprise sur moi",
    }
    _phone_questions = {
        "quel est mon telephone",
        "quel telephone j utilise",
        "quel smartphone ai je",
        "tu te souviens de mon telephone",
    }
    _device_questions = {"quels appareils je possede"}
    _purchase_questions = {"qu est ce que j ai achete"}

    def __init__(self, store: SQLiteMemoryAdapter) -> None:
        self.store = store

    def answer(self, question: str) -> dict | None:
        query = _normalize(question)
        facts = self.store.list_facts(limit=1_000)

        if query in self._profile_questions:
            return self._result(self._profile(facts), self._active_user(facts))
        if query in self._spouse_questions:
            relevant = self._current(facts, "spouse")
            if not relevant:
                relevant = [
                    fact
                    for fact in facts
                    if fact.subject == "user.spouse"
                    and fact.predicate == "name"
                    and fact.is_current
                    and not fact.retracted
                    and not fact.conflict
                ]
            answer = (
                f"Ta femme s'appelle {relevant[-1].object}."
                if relevant
                else "Je ne connais pas le nom de ta femme."
            )
            return self._result(answer, relevant)
        if query in self._works_history_questions:
            relevant = self._timeline(facts, "works_at")
            answer = (
                "Tu as travaillé chez "
                + ", puis chez ".join(fact.object for fact in relevant)
                + "."
                if relevant
                else "Je ne connais aucun de tes anciens employeurs."
            )
            return self._result(answer, relevant)
        if query in self._likes_current_questions:
            relevant = self._current(facts, "likes")
            answer = (
                f"Tu aimes {_join_french([fact.object for fact in relevant])}."
                if relevant
                else "Je ne connais aucune de tes préférences actuelles."
            )
            return self._result(answer, relevant)
        if query in self._likes_history_questions:
            relevant = sorted(
                (
                    fact
                    for fact in facts
                    if fact.subject == "user"
                    and fact.predicate == "likes"
                    and (fact.retracted or not fact.is_current)
                    and not fact.conflict
                ),
                key=_timeline_key,
            )
            answer = (
                "Avant, tu aimais "
                + _join_french([fact.object for fact in relevant])
                + "."
                if relevant
                else "Je ne connais aucune de tes anciennes préférences."
            )
            return self._result(answer, relevant)
        personal = self._personal_facts(facts)
        if query in self._audit_questions:
            return self._result(self._audit(personal), personal)
        if query in self._count_questions:
            return self._result(
                f"Je connais {len(personal)} "
                f"fait{'s' if len(personal) != 1 else ''} structuré"
                f"{'s' if len(personal) != 1 else ''} sur toi.",
                personal,
            )
        if query in self._predicate_questions:
            predicates = sorted({fact.predicate for fact in personal})
            answer = (
                "Je connais ces prédicats sur toi : "
                + ", ".join(predicates)
                + "."
                if predicates
                else "Je ne connais encore aucun prédicat sur toi."
            )
            return self._result(answer, personal)
        if query in self._category_questions:
            categories = sorted(
                {
                    PREDICATES[fact.predicate].category
                    for fact in personal
                    if fact.predicate in PREDICATES
                }
            )
            answer = (
                "Je connais des informations de type : "
                + ", ".join(categories)
                + "."
                if categories
                else "Je ne connais encore aucun type d’information sur toi."
            )
            return self._result(answer, personal)
        if query in self._stale_questions:
            stale = sorted(
                (
                    fact
                    for fact in personal
                    if not fact.is_current
                    and not fact.retracted
                    and not fact.conflict
                ),
                key=_timeline_key,
            )
            answer = (
                "Informations qui ne sont plus actuelles : "
                + self._describe(stale)
                + "."
                if stale
                else "Je ne connais aucune information obsolète sur toi."
            )
            return self._result(answer, stale)
        if query in self._retracted_questions:
            retracted = [fact for fact in personal if fact.retracted]
            answer = (
                "Souvenirs rétractés : " + self._describe(retracted) + "."
                if retracted
                else "Je n’ai aucun souvenir rétracté sur toi."
            )
            return self._result(answer, retracted)
        if query in self._conflict_questions:
            conflicts = [fact for fact in personal if fact.conflict]
            answer = (
                "J’ai détecté des informations contradictoires : "
                + self._describe(conflicts)
                + "."
                if conflicts
                else "Je n’ai détecté aucun conflit dans tes informations."
            )
            return self._result(answer, conflicts)
        if query in self._family_questions:
            return self._result(*self._family(facts))
        if query in self._dependant_questions:
            children = self._current(facts, "has_child")
            answer = (
                f"Je sais que tu as {self._number(len(children))} enfants : "
                f"{_join_french([fact.object for fact in children])}, "
                "mais je ne sais pas s’ils dépendent administrativement "
                "ou financièrement de toi."
                if children
                else "Je ne sais pas qui dépend de toi."
            )
            return self._result(answer, children)
        if query in self._household_questions:
            return self._result(
                "Je ne sais pas précisément qui habite avec toi. "
                "Je connais ta famille, mais tu ne m’as pas dit "
                "explicitement qui vit dans ton foyer.",
                self._family_facts(facts),
            )
        if query in self._moving_questions:
            return self._result(
                "Probablement les personnes de ton foyer, mais je ne sais "
                "pas encore qui vit officiellement avec toi.",
                self._family_facts(facts),
            )
        if query in self._latest_questions:
            relevant = [
                fact
                for fact in personal
                if fact.is_current
                and not fact.retracted
                and not fact.conflict
                and fact.predicate != "relation_to_user"
            ]
            latest = max(relevant, key=lambda fact: fact.updated_at) if relevant else None
            answer = (
                "La dernière information personnelle importante que j’ai "
                f"apprise est : {self._describe([latest])}."
                if latest
                else "Je n’ai encore appris aucune information personnelle "
                "importante sur toi."
            )
            return self._result(answer, [latest] if latest else [])
        if query in self._phone_questions:
            devices = [
                fact
                for fact in facts
                if fact.subject == "user"
                and fact.predicate in {"owns_device", "uses_device"}
                and fact.metadata.get("device_slot") == "phone"
                and fact.is_current
                and not fact.retracted
                and not fact.conflict
            ]
            preferred = next(
                (
                    fact
                    for fact in reversed(devices)
                    if fact.predicate == "uses_device"
                ),
                devices[-1] if devices else None,
            )
            answer = (
                f"Ton téléphone est un {preferred.object}."
                if preferred
                else "Je ne connais pas ton téléphone actuel."
            )
            return self._result(answer, [preferred] if preferred else [])
        if query in self._device_questions:
            devices = self._current(facts, "owns_device")
            answer = (
                f"Tu possèdes {_join_french([f'un {item.object}' for item in devices])}."
                if devices
                else "Je ne connais aucun appareil que tu possèdes."
            )
            return self._result(answer, devices)
        if query in self._purchase_questions:
            purchases = self._current(facts, "purchased")
            answer = (
                f"Tu as acheté {_join_french([f'un {item.object}' for item in purchases])}."
                if purchases
                else "Je ne connais encore aucun de tes achats."
            )
            return self._result(answer, purchases)
        personal_query = self._personal_query(query)
        if personal_query is not None:
            slot, mode = personal_query
            relevant = self._slot_facts(facts, slot)
            if relevant:
                current = relevant[-1]
                if mode == "color":
                    color = self._color(current.object)
                    answer = (
                        f"Ton {self._slot_label(slot)} est {color}."
                        if color
                        else f"Je sais que ton {self._slot_label(slot)} est "
                        f"{current.object}, sans couleur plus précise."
                    )
                elif mode == "name":
                    answer = (
                        f"Ton {self._slot_label(slot)} s'appelle "
                        f"{current.object}."
                    )
                elif mode == "where":
                    answer = (
                        f"Ton {self._slot_label(slot)} est {current.object}."
                    )
                else:
                    answer = (
                        f"Ton {self._slot_label(slot)} est {current.object}."
                    )
                return self._result(answer, [current])
        return None

    @staticmethod
    def _personal_query(query: str) -> tuple[str, str] | None:
        patterns = (
            ("color", r"^de quelle couleur est (?:mon|ma|mes)\s+(.+)$"),
            ("where", r"^ou est (?:mon|ma|mes)\s+(.+)$"),
            ("name", r"^comment s appelle (?:mon|ma|mes)\s+(.+)$"),
            ("value", r"^quel(?:le)? est (?:mon|ma|mes)\s+(.+)$"),
            (
                "value",
                r"^quel(?:le)? ((?:systeme|solution|appareil).+?) "
                r"(?:que )?j utilise$",
            ),
        )
        for mode, pattern in patterns:
            match = re.match(pattern, query)
            if not match:
                continue
            concept = match.group(1)
            slot = canonical_slot(concept)
            return (f"{slot}:name" if mode == "name" else slot, mode)
        return None

    @staticmethod
    def _slot_facts(
        facts: list[LifecycleKnowledgeFact],
        slot: str,
    ) -> list[LifecycleKnowledgeFact]:
        base_slot = slot.removesuffix(":name")
        relevant = [
            fact
            for fact in facts
            if fact.subject == "user"
            and fact.is_current
            and not fact.retracted
            and not fact.conflict
            and (
                (
                    fact.predicate == "personal_attribute"
                    and fact.metadata.get("attribute_slot") == slot
                )
                or (
                    base_slot == "vehicle"
                    and fact.predicate == "owns_vehicle"
                )
                or (
                    (base_slot in DEVICE_SLOTS or base_slot == "device")
                    and fact.predicate in {"owns_device", "uses_device"}
                    and (
                        base_slot == "device"
                        or fact.metadata.get("device_slot") == base_slot
                    )
                )
                or (
                    base_slot in {"pet", "dog", "cat"}
                    and fact.predicate == "has_pet"
                    and (
                        base_slot == "pet"
                        or fact.metadata.get("pet_slot") == base_slot
                    )
                )
            )
        ]
        return sorted(relevant, key=lambda fact: fact.updated_at)

    @staticmethod
    def _color(value: str) -> str | None:
        normalized = _normalize(value)
        colors = (
            "bleu", "bleue", "rouge", "vert", "verte", "noir", "noire",
            "blanc", "blanche", "gris", "grise", "jaune", "orange",
        )
        return next((color for color in colors if color in normalized.split()), None)

    @staticmethod
    def _slot_label(slot: str) -> str:
        labels = {
            "vehicle": "véhicule",
            "phone": "téléphone",
            "laptop": "ordinateur portable",
            "desktop": "ordinateur",
            "internet_box": "box internet",
            "home_automation": "solution domotique",
            "containers": "système de conteneurs",
            "operating_system": "système d’exploitation",
            "favorite_movie": "film préféré",
            "favorite_game": "jeu préféré",
            "favorite_color": "couleur préférée",
            "pet": "animal",
        }
        clean = slot.removesuffix(":name")
        return labels.get(clean, clean.replace("_", " "))

    @staticmethod
    def _result(
        answer: str,
        facts: list[LifecycleKnowledgeFact],
    ) -> dict:
        return {
            "answer": answer,
            "facts": [fact.model_dump(mode="json") for fact in facts],
            "reasoner": "deterministic_user_memory",
        }

    @staticmethod
    def _active_user(
        facts: list[LifecycleKnowledgeFact],
    ) -> list[LifecycleKnowledgeFact]:
        return [
            fact
            for fact in facts
            if (
                fact.subject == "user"
                or fact.predicate == "relation_to_user"
            )
            and fact.is_current
            and not fact.retracted
            and not fact.conflict
        ]

    @staticmethod
    def _personal_facts(
        facts: list[LifecycleKnowledgeFact],
    ) -> list[LifecycleKnowledgeFact]:
        return [
            fact
            for fact in facts
            if (
                fact.subject == "user"
                or fact.subject == "user.spouse"
                or fact.predicate == "relation_to_user"
            )
        ]

    @staticmethod
    def _describe(facts: list[LifecycleKnowledgeFact]) -> str:
        labels = {
            "name": "nom",
            "lives_at": "résidence",
            "works_at": "emploi",
            "spouse": "épouse",
            "has_child": "enfant",
            "likes": "préférence",
            "birth_date": "date de naissance",
            "is": "identité",
            "children_count": "nombre d’enfants",
            "relation_to_user": "relation",
        }
        return "; ".join(
            f"{labels.get(fact.predicate, fact.predicate)} : {fact.object}"
            for fact in facts
        )

    def _audit(self, facts: list[LifecycleKnowledgeFact]) -> str:
        if not facts:
            return "Je ne connais encore aucun fait personnel sur toi."
        return "Mémoire personnelle : " + self._describe(facts) + "."

    @staticmethod
    def _number(value: int) -> str:
        return {
            0: "zéro",
            1: "un",
            2: "deux",
            3: "trois",
            4: "quatre",
            5: "cinq",
        }.get(value, str(value))

    def _family_facts(
        self,
        facts: list[LifecycleKnowledgeFact],
    ) -> list[LifecycleKnowledgeFact]:
        return [
            *self._current(facts, "spouse"),
            *self._current(facts, "has_child"),
        ]

    def _family(
        self,
        facts: list[LifecycleKnowledgeFact],
    ) -> tuple[str, list[LifecycleKnowledgeFact]]:
        family = self._family_facts(facts)
        spouse = [fact.object for fact in family if fact.predicate == "spouse"]
        children = [
            fact.object for fact in family if fact.predicate == "has_child"
        ]
        parts = []
        if spouse:
            parts.append(f"ta femme {spouse[-1]}")
        if children:
            parts.append(f"tes enfants {_join_french(children)}")
        return (
            (
                "Les personnes de ta famille que je connais sont "
                + _join_french(parts)
                + "."
            )
            if parts
            else "Je ne connais encore personne de ta famille.",
            family,
        )

    @staticmethod
    def _current(
        facts: list[LifecycleKnowledgeFact],
        predicate: str,
    ) -> list[LifecycleKnowledgeFact]:
        return sorted(
            (
                fact
                for fact in facts
                if fact.subject == "user"
                and fact.predicate == predicate
                and fact.is_current
                and not fact.retracted
                and not fact.conflict
            ),
            key=lambda fact: fact.created_at,
        )

    @staticmethod
    def _timeline(
        facts: list[LifecycleKnowledgeFact],
        predicate: str,
    ) -> list[LifecycleKnowledgeFact]:
        timeline = sorted(
            (
                fact
                for fact in facts
                if fact.subject == "user"
                and fact.predicate == predicate
                and not fact.retracted
                and not fact.conflict
            ),
            key=_timeline_key,
        )
        return project_unique_timeline(timeline)

    def _profile(self, facts: list[LifecycleKnowledgeFact]) -> str:
        sentences: list[str] = []

        def current(predicate: str) -> LifecycleKnowledgeFact | None:
            values = self._current(facts, predicate)
            return values[-1] if values else None

        name = current("name")
        residence = current("lives_at")
        employer = current("works_at")
        spouse = current("spouse")
        children = self._current(facts, "has_child")
        likes = self._current(facts, "likes")

        if name:
            sentences.append(f"Tu t'appelles {name.object}.")
        if residence:
            sentences.append(
                f"Tu habites actuellement à {residence.object}."
            )
        if employer:
            sentences.append(f"Tu travailles chez {employer.object}.")
        if spouse:
            sentences.append(f"Ta femme s'appelle {spouse.object}.")
        if children:
            count = self._number(len(children))
            sentences.append(
                f"Tu as {count} enfant{'s' if len(children) != 1 else ''} : "
                f"{_join_french([fact.object for fact in children])}."
            )
        if likes:
            sentences.append(
                f"Tu aimes {_join_french([fact.object for fact in likes])}."
            )
        return (
            " ".join(sentences)
            if sentences
            else "Je connais encore peu d’informations sur toi."
        )
