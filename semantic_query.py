from __future__ import annotations

import re

from memory.text_utils import normalize_text
from memory.oblivia.normalisation import ALIAS_UTILISATEUR, SYNONYMES, plat


# Le vocabulaire ferme du juge memoire est en francais, le moteur de lecture
# historique interroge des predicats anglais. Un nom logique couvre donc les
# DEUX orthographes : les anciennes fiches restent lisibles, les nouvelles le
# deviennent, et aucun site d appel ne change.
EQUIVALENTS = {
    "name": {"name", "prenom"},
    "lives_at": {"lives_at", "habite_a"},
    "works_at": {"works_at", "travaille_a"},
    "spouse": {"spouse", "a_pour_conjoint"},
    "has_child": {"has_child", "a_pour_enfant"},
    "likes": {"likes", "aime"},
    "owns": {"owns", "possede"},
}

SUJETS_UTILISATEUR = {"user", "utilisateur"}

# Mots qui ne portent aucune information de recherche.
VIDES = {
    "qui", "que", "quoi", "quel", "quelle", "quels", "quelles", "ou", "quand",
    "comment", "combien", "est", "es", "sont", "suis", "ai", "as", "a", "le",
    "la", "les", "un", "une", "des", "du", "de", "d", "en", "et", "sais", "sait",
    "tu", "il", "elle", "s", "appelle", "dis", "moi", "c", "ce", "cette",
}

# Rendu d un triplet en francais : premiere forme quand le sujet est
# l utilisateur, seconde pour un tiers.
RENDU = {
    "a_pour_frere": ("Ton frère s'appelle {o}.", "{s} a pour frère {o}."),
    "a_pour_soeur": ("Ta sœur s'appelle {o}.", "{s} a pour sœur {o}."),
    "a_pour_pere": ("Ton père s'appelle {o}.", "{s} a pour père {o}."),
    "a_pour_mere": ("Ta mère s'appelle {o}.", "{s} a pour mère {o}."),
    "a_pour_fils": ("Ton fils s'appelle {o}.", "{s} a pour fils {o}."),
    "a_pour_fille": ("Ta fille s'appelle {o}.", "{s} a pour fille {o}."),
    "a_pour_conjoint": ("Ton conjoint s'appelle {o}.", "{s} a pour conjoint {o}."),
    "a_pour_epoux": ("Ton époux s'appelle {o}.", "{s} a pour époux {o}."),
    "a_pour_epouse": ("Ton épouse s'appelle {o}.", "{s} a pour épouse {o}."),
    "a_pour_parent": ("Ton parent s'appelle {o}.", "{s} a pour parent {o}."),
    "a_pour_enfant": ("Ton enfant s'appelle {o}.", "{s} a pour enfant {o}."),
    "prenom": ("Tu t'appelles {o}.", "{s} s'appelle {o}."),
    "age": ("Tu as {o}.", "{s} a {o}."),
    "habite_a": ("Tu habites à {o}.", "{s} habite à {o}."),
    "travaille_a": ("Tu travailles à {o}.", "{s} travaille à {o}."),
    "metier": ("Tu es {o}.", "{s} est {o}."),
    "aime": ("Tu aimes {o}.", "{s} aime {o}."),
    "n_aime_pas": ("Tu n'aimes pas {o}.", "{s} n'aime pas {o}."),
    "film_prefere": ("Ton film préféré est {o}.", "Le film préféré de {s} est {o}."),
    "animal_prefere": ("Ton animal préféré est {o}.", "L'animal préféré de {s} est {o}."),
    "possede": ("Tu possèdes {o}.", "{s} possède {o}."),
    "autre": ("{o}.", "{s} : {o}."),
}


def _mots(texte: str) -> list[str]:
    return [m for m in re.split(r"[^a-z0-9]+", plat(texte)) if m]


def _rendre(fait) -> str:
    vers_utilisateur, vers_tiers = RENDU.get(
        fait.predicate, ("{s} {p} {o}.", "{s} {p} {o}.")
    )
    gabarit = (vers_utilisateur if plat(fait.subject) in SUJETS_UTILISATEUR
               else vers_tiers)
    return gabarit.format(s=fait.subject, o=fait.object,
                          p=fait.predicate.replace("_", " "))


def equiv(nom: str) -> set[str]:
    return EQUIVALENTS.get(nom, {nom})


class SemanticQueryEngine:
    def __init__(self, adapter) -> None:
        self.adapter = adapter

    def answer(self, query: str, limit: int = 10) -> dict:
        q = normalize_text(query).replace("'", " ")
        facts = self.adapter.list_facts(include_retracted=False, limit=1000)
        answer = self._answer(q, facts)
        selected = self._selected_facts(q, facts) if answer else []
        if not answer:
            # Aucune formulation ecrite a la main ne correspond : on cherche.
            answer, selected = self._recherche(q, facts)
        return {"answer": answer, "facts": [fact.model_dump(mode="json") for fact in selected[:limit]]}

    def _recherche(self, q: str, facts) -> tuple[str | None, list]:
        """Repli quand aucune formulation connue ne correspond.

        On confronte les mots de la question au sujet, au predicat et a
        l objet de chaque fiche. Si rien ne sort, on cherche dans le texte
        brut des messages : mieux vaut rendre la phrase d origine qu un
        silence.
        """
        mots = [m for m in _mots(q) if m not in VIDES]
        if not mots:
            return None, []
        vise_utilisateur = any(m in ALIAS_UTILISATEUR for m in _mots(q))

        notes: list[tuple[int, object]] = []
        for f in facts:
            if f.retracted:
                continue
            pred = set(_mots(f.predicate))
            for mot, cible in SYNONYMES.items():
                if cible == f.predicate:
                    pred |= set(_mots(mot))
            entites = set(_mots(f.subject)) | set(_mots(f.object))
            note = sum(2 for m in mots if m in pred)
            note += sum(2 for m in mots if m in entites)
            if note and vise_utilisateur and plat(f.subject) in SUJETS_UTILISATEUR:
                note += 1
            if note:
                notes.append((note, f))

        if notes:
            notes.sort(key=lambda n: -n[0])
            retenus = [f for _, f in notes[:3]]
            return " ".join(_rendre(f) for f in retenus), [f for _, f in notes]

        try:
            records = self.adapter.search_records(q, limit=3)
        except Exception:
            records = []
        if records:
            extraits = " ".join(f"« {r.content} »" for r in records[:2])
            return (
                "Je n'ai pas de fiche là-dessus, mais tu m'as dit : "
                + extraits
            ), []
        return None, []

    def _selected_facts(self, q: str, facts):
        if "createur" in q:
            return [f for f in facts if f.subject == "assistant" and f.predicate == "creator"]
        if "chien" in q:
            return [f for f in facts if "dog" in f.subject or f.predicate == "owns"]
        if "couleur preferee" in q:
            return [f for f in facts if f.predicate == "favorite_color"]
        if "femme" in q or "epouse" in q:
            return [f for f in facts if f.predicate in equiv("spouse") and f.is_current]
        if "travaille" in q or "travail" in q:
            return [f for f in facts if f.predicate in equiv("works_at")]
        if "habite" in q or "vecu" in q:
            return [f for f in facts if f.predicate in equiv("lives_at")]
        return facts

    def _answer(self, q: str, facts) -> str | None:
        current = lambda p: [f for f in facts if f.predicate in equiv(p) and f.is_current and not f.retracted]
        all_valid = lambda p: [f for f in facts if f.predicate in equiv(p) and not f.retracted]

        if q in {"qui suis je", "parle moi de moi", "presente moi", "que sais tu de moi", "fais un resume de ce que tu sais sur moi"}:
            return self._profile(facts)
        if "createur" in q:
            fact = self._last(current("creator"))
            return f"Mon créateur est {fact.object}." if fact else None
        if "comment s appelle mon chien" in q:
            fact = self._last([f for f in facts if f.subject == "user.dog" and f.predicate == "name"])
            return f"Ton chien s'appelle {fact.object}." if fact else None
        if "couleur preferee" in q:
            fact = self._last(current("favorite_color"))
            return f"Ta couleur préférée est {fact.object}." if fact else None
        if "comment s appelle ma femme" in q or "qui est ma femme" in q or "qui est mon epouse" in q:
            fact = self._last(current("spouse"))
            return f"Ta femme s'appelle {fact.object}." if fact else None
        if "comment je m appelle" in q:
            fact = self._last(current("name"))
            return f"Tu t'appelles {fact.object}." if fact else None
        if "qui est mon fils" in q:
            fact = self._last([f for f in facts if f.subject == "user.son" and f.predicate == "name" and not f.retracted])
            return f"Ton fils s'appelle {fact.object}." if fact else None
        if q == "qui est papa":
            fact = self._last([f for f in facts if f.predicate == "is" and f.object == "papa"])
            return "Papa, c'est toi." if fact else None
        if "combien ai je d enfants" in q:
            fact = self._last(current("children_count"))
            return f"Tu as {self._number_word(fact.object)} enfants." if fact else None
        if "comment s appellent mes enfants" in q:
            names = [f.object for f in current("has_child")]
            return f"Tes enfants s'appellent {self._join(names)}." if names else None
        if "qui sont mes enfants" in q:
            names = [f.object for f in current("has_child")]
            return f"Tes enfants sont {self._join(names)}." if names else None
        if q.startswith("qui est "):
            name = query_name = q.removeprefix("qui est ").strip()
            for fact in current("has_child"):
                if normalize_text(fact.object) == name:
                    return f"{fact.object} est l'un de tes enfants."
        if "ou est ce que j habite" in q or "ou j habite" in q:
            fact = self._last(current("lives_at"))
            return f"Tu habites à {fact.object}." if fact else "Je ne connais pas ton lieu de résidence actuel."
        if "habitais avant" in q or "habitais je avant" in q:
            history = all_valid("lives_at")
            previous = [f for f in history if not f.is_current]
            return f"Avant, tu habitais à {previous[-1].object}." if previous else "Je ne connais pas de lieu où tu habitais avant."
        if "ou ai je vecu" in q or "dans quelles villes ai je vecu" in q:
            values = self._unique([f.object for f in self._timeline(all_valid("lives_at"))])
            return f"Tu as vécu à {self._join(values, then='à')}." if values else "Je ne connais aucun lieu où tu as vécu."
        if "ou est ce que je travaille" in q or "ou je travaille" in q or "quel est mon travail" in q:
            fact = self._last(current("works_at"))
            return f"Tu travailles chez {fact.object}." if fact else None
        if "travaillais je avant" in q:
            previous = [f for f in all_valid("works_at") if not f.is_current]
            return f"Avant, tu travaillais chez {previous[-1].object}." if previous else None
        if "ou ai je travaille" in q:
            values = self._unique([f.object for f in self._timeline(all_valid("works_at"))])
            return f"Tu as travaillé chez {self._join(values, then='chez')}." if values else None
        if "qu est ce que j aimais avant" in q:
            values = [f.object for f in self.adapter.list_facts(predicate="likes") if f.retracted]
            return f"Avant, tu aimais {self._join(values)}." if values else None
        if "qu est ce que j aime boire" in q:
            fact = self._last(current("likes"))
            return f"Tu apprécies {fact.object}." if fact else None
        if "qu est ce que j aime" in q or "j aime quoi" in q:
            values = [f.object for f in current("likes")]
            return f"Tu aimes {self._join(values)}." if values else None
        return self._generic_answer(q, facts)

    def _generic_answer(self, q: str, facts) -> str | None:
        relation = None
        # Note : "comment s appelle mon/ma X" ajouté le 19 juillet 2026,
        # symétrique du repli "X s'appelle Y" ajouté côté FactExtractor le
        # même jour — sans lui, un fait extrait via ce nouveau repli
        # d'extraction (ex. "voiture" -> "Nébula") n'avait aucune façon
        # d'être retrouvé au recall : "Comment s'appelle ma voiture ?" ne
        # matchait aucun préfixe existant ici.
        for prefix in ("quel est mon ", "quelle est ma ", "quelle est mon ", "quel est ma ", "ou est ma ", "ou est mon ", "de quelle couleur est mon ", "de quelle couleur est ma ", "comment s appelle mon ", "comment s appelle ma "):
            if q.startswith(prefix):
                relation = q.removeprefix(prefix).strip().replace(" ", "_")
                break
        if "systeme de conteneurs" in q:
            relation = "container_system"
        if "systeme d exploitation" in q:
            relation = "operating_system"
        if relation:
            aliases = {"vehicule": "vehicle", "telephone": "smartphone", "tv": "tv", "animal": "owns"}
            relation = aliases.get(relation, relation)
            fact = self._last([f for f in facts if f.predicate == relation and f.is_current and not f.retracted])
            if fact:
                return f"{fact.object}."
        return None

    def _profile(self, facts) -> str:
        parts: list[str] = []
        # Le profil ne parle que de l utilisateur : sans ce filtre, un fait sur
        # un tiers ("Julien travaille a Bordeaux") serait raconte comme le sien.
        by_pred = lambda p: [
            f for f in facts
            if f.predicate in equiv(p) and f.is_current and not f.retracted
            and normalize_text(f.subject) in SUJETS_UTILISATEUR
        ]
        if name := self._last(by_pred("name")):
            parts.append(f"Tu t'appelles {name.object}.")
        if home := self._last(by_pred("lives_at")):
            parts.append(f"Tu habites actuellement à {home.object}.")
        if work := self._last(by_pred("works_at")):
            parts.append(f"Tu travailles chez {work.object}.")
        if spouse := self._last(by_pred("spouse")):
            parts.append(f"Ta femme s'appelle {spouse.object}.")
        children = [f.object for f in by_pred("has_child")]
        if children:
            parts.append(f"Tu as {self._number_word(str(len(children)))} enfants : {self._join(children)}.")
        likes = [f.object for f in by_pred("likes")]
        if likes:
            parts.append(f"Tu aimes {self._join(likes)}.")
        aversions = [f.object for f in by_pred("n_aime_pas")]
        if aversions:
            parts.append(f"Tu n'aimes pas {self._join(aversions)}.")
        freres = [f.object for f in by_pred("a_pour_frere")]
        if freres:
            parts.append(
                f"Ton frère s'appelle {self._join(freres)}." if len(freres) == 1
                else f"Tes frères s'appellent {self._join(freres)}."
            )
        soeurs = [f.object for f in by_pred("a_pour_soeur")]
        if soeurs:
            parts.append(
                f"Ta sœur s'appelle {self._join(soeurs)}." if len(soeurs) == 1
                else f"Tes sœurs s'appellent {self._join(soeurs)}."
            )
        return " ".join(parts) if parts else "Je connais encore peu d’informations sur toi."

    def _last(self, values):
        return values[-1] if values else None

    def _unique(self, values: list[str]) -> list[str]:
        seen: set[str] = set()
        result: list[str] = []
        for value in values:
            key = normalize_text(value)
            if key not in seen:
                seen.add(key)
                result.append(value)
        return result

    def _timeline(self, facts):
        return sorted(facts, key=lambda fact: (fact.is_current, fact.id or 0))

    def _join(self, values: list[str], then: str | None = None) -> str:
        values = [v for v in values if v]
        if len(values) <= 1:
            return values[0] if values else ""
        sep = f", puis {then} " if then else " et "
        return ", ".join(values[:-1]) + sep + values[-1]

    def _number_word(self, value: str) -> str:
        return {"1": "un", "2": "deux", "3": "trois"}.get(value, value)
