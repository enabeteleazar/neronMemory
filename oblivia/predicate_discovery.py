"""Deterministic predicate discovery for clear personal possession facts."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

from .ontology import PREDICATES, PredicateDefinition
from .schemas import KnowledgeFact


@dataclass(frozen=True)
class PredicateCandidate:
    predicate: str
    cardinality: str
    lifecycle: str
    temporal: bool
    category: str
    labels: dict[str, str]
    confidence: float


@dataclass(frozen=True)
class DiscoveryDecision:
    predicate: str | None
    definition: PredicateDefinition | PredicateCandidate | None
    status: str
    requires_confirmation: bool = False


class PredicateDiscovery:
    """Map clear concepts and retain safe ontology candidates for review."""

    _nearby = {
        "telephone": "owns_device",
        "smartphone": "owns_device",
        "ordinateur": "owns_device",
        "appareil": "owns_device",
        "voiture": "owns_vehicle",
        "vehicule": "owns_vehicle",
        "objet": "owns_object",
        "achat": "purchased",
        "utilise": "uses_device",
    }

    def __init__(self) -> None:
        self.candidates: dict[str, PredicateCandidate] = {}

    def discover(self, concept: str) -> DiscoveryDecision:
        normalized = _normalize(concept)
        if normalized in PREDICATES:
            return DiscoveryDecision(
                normalized,
                PREDICATES[normalized],
                "known",
            )
        if normalized in self._nearby:
            predicate = self._nearby[normalized]
            return DiscoveryDecision(
                predicate,
                PREDICATES[predicate],
                "mapped",
            )
        candidate_name = re.sub(r"[^a-z0-9]+", "_", normalized).strip("_")
        if not candidate_name or len(candidate_name) < 3:
            return DiscoveryDecision(
                None,
                None,
                "ambiguous",
                requires_confirmation=True,
            )
        candidate = PredicateCandidate(
            predicate=candidate_name,
            cardinality="many",
            lifecycle="accumulate",
            temporal=True,
            category="possessions",
            labels={"fr": concept.strip()},
            confidence=0.7,
        )
        self.candidates.setdefault(candidate_name, candidate)
        return DiscoveryDecision(candidate_name, candidate, "candidate")

    def extract(
        self,
        text: str,
        *,
        source: str,
    ) -> list[KnowledgeFact]:
        value = text.strip()
        patterns = (
            (
                "replacement",
                re.compile(
                    r"^j['’]ai remplac[ée]\s+(?:(?:mon|ma|mes)\s+)?"
                    r"(?P<concept>.+?)\s+par\s+(?P<object>.+?)[.!?]*$",
                    re.IGNORECASE,
                ),
            ),
            (
                "named",
                re.compile(
                    r"^(?:(?:mon|ma|mes)\s+)?(?P<concept>.+?)\s+"
                    r"s['’]appelle\s+(?P<object>.+?)[.!?]*$",
                    re.IGNORECASE,
                ),
            ),
            (
                "attribute",
                re.compile(
                    r"^(?:mon|ma|mes)\s+(?P<concept>.+?)\s+"
                    r"(?:est|sont)\s+(?:maintenant\s+)?"
                    r"(?P<object>.+?)[.!?]*$",
                    re.IGNORECASE,
                ),
            ),
            (
                "purchase",
                re.compile(
                    r"^j['’]ai achet[ée]\s+(?P<object>.+?)[.!?]*$",
                    re.IGNORECASE,
                ),
            ),
            (
                "owns",
                re.compile(
                    r"^j['’]ai\s+(?P<object>.+?)[.!?]*$",
                    re.IGNORECASE,
                ),
            ),
            (
                "owns",
                re.compile(
                    r"^je poss[èe]de\s+(?P<object>.+?)[.!?]*$",
                    re.IGNORECASE,
                ),
            ),
            (
                "uses",
                re.compile(
                    r"^j['’]utilise\s+(?P<object>.+?)[.!?]*$",
                    re.IGNORECASE,
                ),
            ),
            (
                "phone",
                re.compile(
                    r"^mon t[ée]l[ée]phone est\s+(?P<object>.+?)[.!?]*$",
                    re.IGNORECASE,
                ),
            ),
            (
                "computer",
                re.compile(
                    r"^mon ordinateur est\s+(?P<object>.+?)[.!?]*$",
                    re.IGNORECASE,
                ),
            ),
        )
        for operation, pattern in patterns:
            match = pattern.match(value)
            if not match:
                continue
            raw_object = re.split(
                r",\s*(?:il|elle|c['’]est)\b",
                match.group("object"),
                maxsplit=1,
                flags=re.IGNORECASE,
            )[0]
            object_name = _clean_object(raw_object)
            if not object_name:
                return []
            concept = (
                match.groupdict().get("concept")
                if "concept" in match.groupdict()
                else None
            )
            return self._facts(
                object_name,
                operation=operation,
                source=source,
                raw_text=value,
                concept=concept,
            )
        return []

    def _facts(
        self,
        object_name: str,
        *,
        operation: str,
        source: str,
        raw_text: str,
        concept: str | None = None,
    ) -> list[KnowledgeFact]:
        slot = canonical_slot(concept or object_name)
        if operation == "named":
            return [
                _attribute_fact(
                    slot=f"{slot}:name",
                    value=object_name,
                    source=source,
                    raw_text=raw_text,
                    concept=concept or slot,
                )
            ]

        if operation == "attribute" and slot == "vehicle":
            if _looks_like_color(object_name):
                object_name = f"{_vehicle_kind(concept)} {object_name}"
            return [
                KnowledgeFact(
                    subject="user",
                    predicate="owns_vehicle",
                    object=object_name,
                    source=source,
                    raw_text=raw_text,
                    metadata={
                        "vehicle_slot": "vehicle",
                        "attribute_slot": slot,
                        "discovered_by": "predicate_discovery",
                    },
                )
            ]

        if operation in {"attribute", "replacement"} and slot in DEVICE_SLOTS:
            return [
                KnowledgeFact(
                    subject="user",
                    predicate="owns_device",
                    object=object_name,
                    source=source,
                    raw_text=raw_text,
                    metadata={
                        "device_slot": slot,
                        "discovered_by": "predicate_discovery",
                    },
                )
            ]

        if operation in {"attribute", "replacement"}:
            return [
                _attribute_fact(
                    slot=slot,
                    value=object_name,
                    source=source,
                    raw_text=raw_text,
                    concept=concept or slot,
                )
            ]

        device = _classify_device(object_name)
        inferred_slot = canonical_slot(object_name)
        if device is None and inferred_slot in DEVICE_SLOTS:
            device = {"slot": inferred_slot, "type": inferred_slot}
        if operation in {"phone", "computer", "uses"} or device:
            predicate = "uses_device" if operation == "uses" else "owns_device"
            device_slot = (
                operation
                if operation in {"phone", "computer"}
                else (device or {}).get("slot", "device")
            )
            metadata = {
                "device_slot": device_slot,
                "discovered_by": "predicate_discovery",
            }
        elif canonical_slot(object_name) == "vehicle":
            predicate = "owns_vehicle"
            metadata = {
                "vehicle_slot": "vehicle",
                "discovered_by": "predicate_discovery",
            }
        elif canonical_slot(object_name) in {"pet", "dog", "cat"}:
            predicate = "has_pet"
            metadata = {
                "pet_slot": canonical_slot(object_name),
                "discovered_by": "predicate_discovery",
            }
        else:
            predicate = "owns_object"
            metadata = {"discovered_by": "predicate_discovery"}

        facts = [
            KnowledgeFact(
                subject="user",
                predicate=predicate,
                object=object_name,
                source=source,
                raw_text=raw_text,
                metadata=metadata,
            )
        ]
        if operation == "purchase":
            facts.append(
                KnowledgeFact(
                    subject="user",
                    predicate="purchased",
                    object=object_name,
                    source=source,
                    raw_text=raw_text,
                    metadata={"acquisition": "purchase"},
                )
            )
            facts.append(
                KnowledgeFact(
                    subject=object_name,
                    predicate="acquired_by",
                    object="purchase",
                    source=source,
                    raw_text=raw_text,
                )
            )
        if device:
            facts.append(
                KnowledgeFact(
                    subject=object_name,
                    predicate="type",
                    object=device["type"],
                    source=source,
                    raw_text=raw_text,
                )
            )
            if device.get("brand"):
                facts.append(
                    KnowledgeFact(
                        subject=object_name,
                        predicate="brand",
                        object=device["brand"],
                        source=source,
                        raw_text=raw_text,
                    )
                )
        return facts


def _normalize(value: str) -> str:
    value = unicodedata.normalize("NFKD", value.casefold())
    value = "".join(
        char for char in value if not unicodedata.combining(char)
    )
    return " ".join(
        re.sub(r"[^a-z0-9 ]+", " ", value).split()
    )


def _clean_object(value: str) -> str:
    cleaned = value.strip().rstrip(".!?")
    cleaned = re.sub(
        r"^(?:l['’]|le |la |les |un |une )",
        "",
        cleaned,
        flags=re.IGNORECASE,
    )
    return cleaned.strip()


def _contains(value: str, tokens: tuple[str, ...]) -> bool:
    normalized = value.casefold()
    return any(token in normalized for token in tokens)


def _classify_device(value: str) -> dict[str, str] | None:
    normalized = _normalize(value)
    if any(token in normalized for token in ("iphone", "smartphone", "téléphone")):
        return {
            "slot": "phone",
            "type": "smartphone",
            **({"brand": "Apple"} if "iphone" in normalized else {}),
        }
    if any(token in normalized for token in ("ordinateur portable", "macbook", "laptop")):
        return {
            "slot": "laptop",
            "type": "computer",
            **({"brand": "Apple"} if "macbook" in normalized else {}),
        }
    if any(token in normalized for token in ("ordinateur fixe", "desktop", " pc", "pc ")):
        return {"slot": "desktop", "type": "computer"}
    if any(token in normalized for token in ("ipad", "tablette")):
        return {
            "slot": "tablet",
            "type": "tablet",
            **({"brand": "Apple"} if "ipad" in normalized else {}),
        }
    return None


SLOT_ALIASES: dict[str, tuple[str, ...]] = {
    "device": ("appareil",),
    "vehicle": ("vehicule", "camion", "voiture", "utilitaire"),
    "phone": ("telephone", "smartphone", "iphone"),
    "laptop": ("ordinateur portable", "portable", "laptop", "macbook"),
    "desktop": ("ordinateur fixe", "pc fixe", "desktop", "ordinateur", "pc"),
    "tv": ("television", "televiseur", "tv"),
    "watch": ("montre",),
    "headphones": ("casque",),
    "console": ("console",),
    "nas": ("nas",),
    "printer": ("imprimante",),
    "house": ("maison",),
    "roof": ("toit", "toiture"),
    "gate": ("portail",),
    "kitchen": ("cuisine",),
    "living_room": ("salon",),
    "bedroom": ("chambre",),
    "pet": ("animal", "animaux"),
    "dog": ("chien",),
    "cat": ("chat",),
    "internet_box": ("box internet", "routeur internet", "box"),
    "router": ("routeur",),
    "switch": ("switch", "commutateur"),
    "server": ("serveur",),
    "home_automation": ("solution domotique", "systeme domotique", "domotique", "home assistant"),
    "containers": ("systeme de conteneurs", "conteneurs", "docker"),
    "operating_system": ("systeme d exploitation", "os", "ubuntu"),
    "favorite_movie": ("film prefere",),
    "favorite_game": ("jeu prefere",),
    "favorite_color": ("couleur preferee",),
}

DEVICE_SLOTS = {
    "phone", "laptop", "desktop", "tv", "watch", "headphones",
    "console", "nas", "printer", "internet_box", "router", "switch",
    "server",
}


def canonical_slot(value: str) -> str:
    normalized = _normalize(value)
    for slot, aliases in SLOT_ALIASES.items():
        if normalized == slot or any(alias in normalized for alias in aliases):
            return slot
    return re.sub(r"[^a-z0-9]+", "_", normalized).strip("_") or "attribute"


def _attribute_fact(
    *,
    slot: str,
    value: str,
    source: str,
    raw_text: str,
    concept: str,
) -> KnowledgeFact:
    return KnowledgeFact(
        subject="user",
        predicate="personal_attribute",
        object=value,
        source=source,
        raw_text=raw_text,
        metadata={
            "attribute_slot": slot,
            "concept": concept.strip(),
            "discovered_by": "predicate_discovery",
        },
    )


def _looks_like_color(value: str) -> bool:
    return _normalize(value) in {
        "bleu", "bleue", "rouge", "vert", "verte", "noir", "noire",
        "blanc", "blanche", "gris", "grise", "jaune", "orange",
    }


def _vehicle_kind(value: str | None) -> str:
    normalized = _normalize(value or "")
    return next(
        (kind for kind in ("camion", "voiture", "utilitaire") if kind in normalized),
        "véhicule",
    )
