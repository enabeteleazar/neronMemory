"""Vocabulaire vivant des predicats.

Le vocabulaire de `normalisation.py` est un point de DEPART, pas une limite.
Ce registre le charge en base au premier demarrage, puis fait autorite : un
predicat inconnu mais structurellement valide y est adopte, persiste, et
disponible pour les messages suivants — y compris apres redemarrage.

Ce qui a motive ce changement, mesure le 04/09/2026 en production : le juge
avait produit `a_pour_animal_prefere` la ou le vocabulaire connaissait
`animal_prefere`. Le triplet etait rejete et le fait perdu. Une memoire qui
doit apprendre ne peut pas dependre d'une liste ecrite a la main.

Trois issues possibles pour un predicat, dans cet ordre :

    connu            -> utilise tel quel
    variante d'un connu -> ramene au predicat existant (evite la
                        proliferation de synonymes : a_pour_animal_prefere
                        et animal_prefere doivent designer la meme chose)
    inconnu valide   -> adopte, enregistre, utilisable aussitot
    inconnu invalide -> rejete, avec le motif
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from .normalisation import (
    ATTRIBUTS,
    ORIENTEES,
    SYMETRIQUES,
    classer_predicat,
    predicat_structurellement_valide,
)

_RELATION_KINDS = {"relation_symetrique", "relation_orientee"}


def predicate_from_label(label: str) -> str:
    """Transforme un libelle libre en predicat candidat."""
    return "_".join((label or "").strip().lower().split())


def _now() -> str:
    return datetime.now(UTC).isoformat()


class PredicateRegistry:
    """Vocabulaire persistant, adosse a l'adaptateur SQLite.

    Le cache memoire evite une lecture SQL par triplet ; il est reconstruit
    a l'adoption d'un predicat, jamais invalide autrement. Deux instances
    pointant la meme base peuvent donc diverger le temps d'une requete —
    sans consequence : la table reste la reference et la cle primaire
    tranche les adoptions concurrentes.
    """

    def __init__(self, adapter) -> None:
        self.adapter = adapter
        self._cache: dict[str, dict[str, Any]] | None = None
        self._amorcer()

    # ── vocabulaire ────────────────────────────────────────────────────

    def _amorcer(self) -> None:
        """Injecte le vocabulaire de depart si la table est vide.

        Idempotent : `add_predicate` ignore les doublons, donc un
        redemarrage ne recree rien et n'ecrase aucun predicat appris.
        """
        if self.adapter.list_predicates():
            return
        maintenant = _now()
        for nom in sorted(SYMETRIQUES | ORIENTEES | ATTRIBUTS):
            self.adapter.add_predicate(
                nom, classer_predicat(nom), "amorce", maintenant
            )
        self._cache = None

    @property
    def vocabulaire(self) -> dict[str, dict[str, Any]]:
        if self._cache is None:
            self._cache = self.adapter.list_predicates()
        return self._cache

    def noms(self) -> set[str]:
        return set(self.vocabulaire)

    def est_relation(self, predicat: str) -> bool:
        entree = self.vocabulaire.get(predicat)
        return bool(entree) and entree["kind"] in _RELATION_KINDS

    def est_orientee(self, predicat: str) -> bool:
        entree = self.vocabulaire.get(predicat)
        return bool(entree) and entree["kind"] == "relation_orientee"

    # ── resolution ─────────────────────────────────────────────────────

    def _variante_connue(self, predicat: str) -> str | None:
        """Ramene un predicat a un existant dont il n'est qu'une variante.

        Sans ce rapprochement, le vocabulaire se peuplerait de doublons
        semantiques que rien ne relierait : le juge ne produit pas deux fois
        la meme forme pour la meme idee.
        """
        connus = self.noms()

        if predicat.startswith("a_pour_"):
            nu = predicat[len("a_pour_"):]
            if nu in connus:
                return nu
        else:
            prefixe = f"a_pour_{predicat}"
            if prefixe in connus:
                return prefixe

        if predicat.endswith("s") and predicat[:-1] in connus:
            return predicat[:-1]
        if f"{predicat}s" in connus:
            return f"{predicat}s"

        return None

    def resoudre(
        self, predicat: str, exemple: dict | None = None
    ) -> tuple[str | None, str]:
        """Renvoie (predicat retenu, motif de rejet si None)."""
        if predicat in self.vocabulaire:
            self.adapter.touch_predicate(predicat, _now())
            return predicat, ""

        variante = self._variante_connue(predicat)
        if variante is not None:
            self.adapter.touch_predicate(variante, _now())
            return variante, ""

        valide, motif = predicat_structurellement_valide(predicat)
        if not valide:
            return None, motif

        return self.adopter(predicat, exemple), ""

    def adopter(self, predicat: str, exemple: dict | None = None) -> str:
        """Enregistre un nouveau predicat et le rend disponible aussitot."""
        maintenant = _now()
        trace = None
        if exemple:
            trace = (
                f"{exemple.get('subject')} | {predicat} | {exemple.get('object')}"
            )
        self.adapter.add_predicate(
            predicat, classer_predicat(predicat), "appris", maintenant, trace
        )
        self.adapter.touch_predicate(predicat, maintenant)
        self._cache = None
        return predicat
