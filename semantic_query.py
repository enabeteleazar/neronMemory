from __future__ import annotations

from memory.text_utils import normalize_text


class SemanticQueryEngine:
    def __init__(self, adapter) -> None:
        self.adapter = adapter

    def answer(self, query: str, limit: int = 10) -> dict:
        q = normalize_text(query).replace("'", " ")
        facts = self.adapter.list_facts(include_retracted=False, limit=1000)
        answer = self._answer(q, facts)
        selected = self._selected_facts(q, facts) if answer else []
        return {"answer": answer, "facts": [fact.model_dump(mode="json") for fact in selected[:limit]]}

    def _selected_facts(self, q: str, facts):
        if "createur" in q:
            return [f for f in facts if f.subject == "assistant" and f.predicate == "creator"]
        if "chien" in q:
            return [f for f in facts if "dog" in f.subject or f.predicate == "owns"]
        if "couleur preferee" in q:
            return [f for f in facts if f.predicate == "favorite_color"]
        if "femme" in q or "epouse" in q:
            return [f for f in facts if f.predicate == "spouse" and f.is_current]
        if "travaille" in q or "travail" in q:
            return [f for f in facts if f.predicate == "works_at"]
        if "habite" in q or "vecu" in q:
            return [f for f in facts if f.predicate == "lives_at"]
        return facts

    def _answer(self, q: str, facts) -> str | None:
        current = lambda p: [f for f in facts if f.predicate == p and f.is_current and not f.retracted]
        all_valid = lambda p: [f for f in facts if f.predicate == p and not f.retracted]

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
        by_pred = lambda p: [f for f in facts if f.predicate == p and f.is_current and not f.retracted]
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
