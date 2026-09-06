"""Validation deterministe des triplets sortis du juge LLM.

Le modele extrait, le code decide. Tout ce qui est mecaniquement corrigeable
l'est ici plutot que dans la consigne : sur un petit modele, chaque regle
ajoutee a la consigne coute un fait, alors qu'elle coute une milliseconde ici.
"""
from __future__ import annotations

import re
from typing import TYPE_CHECKING
import unicodedata

if TYPE_CHECKING:  # predicate_discovery importe ce module : garde anti-cycle.
    from .predicate_discovery import PredicateRegistry

# Relations symetriques : les deux sens sont vrais.
SYMETRIQUES = {"a_pour_frere", "a_pour_soeur"}

# Relations orientees. La forme canonique (utilisateur toujours sujet) leve
# l'ambiguite de direction, y compris sur parent/enfant.
ORIENTEES = {
    "a_pour_pere", "a_pour_mere", "a_pour_fils", "a_pour_fille",
    "a_pour_conjoint", "a_pour_epoux", "a_pour_epouse",
    "a_pour_parent", "a_pour_enfant",
}

ATTRIBUTS = {
    "prenom", "age", "habite_a", "travaille_a", "metier",
    "aime", "n_aime_pas", "film_prefere", "animal_prefere", "possede", "autre",
}

# VOCABULAIRE est le vocabulaire d'AMORCAGE, pas une liste fermee. Il sert
# a peupler la table `predicates` au premier demarrage ; ensuite c'est cette
# table qui fait autorite, et memory l'enrichit lui-meme (cf.
# oblivia/predicate_discovery.py).
VOCABULAIRE = SYMETRIQUES | ORIENTEES | ATTRIBUTS
RELATIONS = SYMETRIQUES | ORIENTEES

# ── Validation structurelle d'un predicat ──────────────────────────────
# Remplace l'appartenance a une liste fermee. Un predicat inconnu n'est plus
# rejete d'office : il doit en revanche ressembler a un predicat, et pas a
# une phrase que le modele aurait recopiee. Ces bornes viennent des sorties
# reellement observees du juge.
_PREDICAT_MIN = 2
_PREDICAT_MAX = 40
_PREDICAT_MAX_SEGMENTS = 4
_PREDICAT_FORME = re.compile(r"^[a-z][a-z0-9]*(_[a-z0-9]+)*$")


def predicat_structurellement_valide(predicat: str) -> tuple[bool, str]:
    """Un predicat inconnu peut-il etre adopte ? Renvoie (verdict, motif).

    Ne dit RIEN de la pertinence semantique — seulement de la forme. C'est
    le garde-fou qui remplace la liste fermee : sans lui, une phrase entiere
    renvoyee par le modele deviendrait un predicat.
    """
    if not predicat:
        return False, "predicat vide"
    if len(predicat) < _PREDICAT_MIN:
        return False, f"predicat trop court : {predicat}"
    if len(predicat) > _PREDICAT_MAX:
        return False, f"predicat trop long ({len(predicat)} caracteres)"
    if not _PREDICAT_FORME.match(predicat):
        return False, f"forme invalide : {predicat}"
    if predicat.count("_") + 1 > _PREDICAT_MAX_SEGMENTS:
        return False, f"predicat trop segmente : {predicat}"
    return True, ""


def classer_predicat(predicat: str) -> str:
    """Attribut ou relation ? Determine la forme canonique appliquee ensuite.

    Le prefixe `a_pour_` designe une relation entre deux personnes dans tout
    le vocabulaire d'amorcage (a_pour_frere, a_pour_mere...). Un predicat
    appris qui le reprend est traite comme une relation ORIENTEE : c'est le
    choix prudent, la symetrie ne peut pas se deviner de la forme et une
    fausse symetrie enregistrerait un lien dans les deux sens.
    Tout le reste est un attribut — un attribut ne declenche ni inversion
    ni miroir, donc une erreur de classement y est sans consequence.
    """
    if predicat in SYMETRIQUES:
        return "relation_symetrique"
    if predicat in ORIENTEES:
        return "relation_orientee"
    if predicat in ATTRIBUTS:
        return "attribut"
    return "relation_orientee" if predicat.startswith("a_pour_") else "attribut"

# Correspondances observees en conditions reelles.
SYNONYMES = {
    "deteste": "n_aime_pas",
    "profession": "metier",
    "vit_a": "habite_a",
    "est": "autre",
    "a_pour_femme": "a_pour_epouse",
    "a_pour_mari": "a_pour_epoux",
}

ALIAS_UTILISATEUR = {
    "j", "je", "moi", "me", "mon", "ma", "mes",
    "user", "l_utilisateur", "utilisateur", "moi_meme",
}


def plat(texte: str) -> str:
    sans = unicodedata.normalize("NFD", str(texte).strip().lower())
    return "".join(c for c in sans if unicodedata.category(c) != "Mn")


def _predicat(brut: str) -> str:
    p = plat(brut).replace(" ", "_").replace("-", "_")
    p = p.replace("a_for_", "a_pour_")          # derive anglais observee 3 fois
    while "__" in p:
        p = p.replace("__", "_")
    return SYNONYMES.get(p, p)


def _entite(brut: str) -> str:
    e = str(brut).strip()
    return "utilisateur" if plat(e).replace(" ", "_") in ALIAS_UTILISATEUR else e


def normaliser(
    faits: list[dict],
    registre: PredicateRegistry | None = None,
) -> tuple[list[dict], list[tuple[dict, str]]]:
    """Renvoie (triplets retenus, [(triplet rejete, motif)]).

    `registre` : un PredicateRegistry (cf. oblivia/predicate_discovery.py).
    Fourni, il fait autorite sur le vocabulaire et ADOPTE les predicats
    inconnus mais structurellement valides. Absent, la fonction retombe sur
    le vocabulaire d'amorcage du module — c'est ce qui permet de l'utiliser
    sans base, notamment dans les tests unitaires de normalisation.

    Avant, un predicat hors liste etait rejete sans appel. Le juge produisait
    par exemple `a_pour_animal_prefere` la ou le vocabulaire connaissait
    `animal_prefere`, et le fait etait perdu. Une memoire qui doit apprendre
    ne peut pas avoir un vocabulaire fige a l'ecriture du code.
    """
    retenus: list[dict] = []
    rejets: list[tuple[dict, str]] = []
    vus: set[tuple[str, str, str]] = set()
    orientations: dict[str, bool] = {}

    for brut in faits:
        if not isinstance(brut, dict):
            rejets.append(({"brut": brut}, "objet JSON attendu"))
            continue

        sujet = _entite(brut.get("subject", ""))
        objet = _entite(brut.get("object", ""))
        predicat = _predicat(brut.get("predicate", ""))

        if not sujet or not objet or not predicat:
            rejets.append((brut, "champ vide"))
            continue
        if plat(sujet) == plat(objet):
            rejets.append((brut, "sujet identique a l objet"))
            continue
        if registre is not None:
            # Le registre tranche : connu, variante d'un connu, ou adoptable.
            predicat, motif = registre.resoudre(predicat, exemple=brut)
            if predicat is None:
                rejets.append((brut, motif))
                continue
            relation = registre.est_relation(predicat)
            orientee = registre.est_orientee(predicat)
        else:
            if predicat not in VOCABULAIRE:
                rejets.append((brut, f"predicat hors vocabulaire : {predicat}"))
                continue
            relation = predicat in RELATIONS
            orientee = predicat in ORIENTEES

        # Forme canonique : quand l utilisateur est implique dans une relation,
        # il est TOUJOURS le sujet. Sans cela le meme lien serait stocke tantot
        # dans un sens tantot dans l autre, et les requetes seraient peu fiables.
        if relation and plat(objet) == "utilisateur":
            sujet, objet = objet, sujet
        orientations[predicat] = orientee

        cle = (plat(sujet), predicat, plat(objet))
        if cle in vus:
            rejets.append((brut, "doublon"))
            continue
        vus.add(cle)
        retenus.append({"subject": sujet, "predicate": predicat, "object": objet})

    # Filet : paire contradictoire restante sur une relation orientee.
    final, index = [], {(plat(f["subject"]), f["predicate"], plat(f["object"])) for f in retenus}
    for f in retenus:
        miroir = (plat(f["object"]), f["predicate"], plat(f["subject"]))
        if orientations.get(f["predicate"], False) and miroir in index:
            rejets.append((f, "paire contradictoire sur une relation orientee"))
            continue
        final.append(f)
    return final, rejets


if __name__ == "__main__":
    # Sorties REELLES du balayage du 03/08, defauts compris.
    cas = [
        {"subject": "Sophie", "predicate": "a_for_soeur", "object": "utilisateur"},
        {"subject": "j", "predicate": "aime", "object": "le cafe"},
        {"subject": "Lucas", "predicate": "a_for_enfant", "object": "Lucas"},
        {"subject": "Emma", "predicate": "a_for_conjoint", "object": "utilisateur"},
        {"subject": "Pierre", "predicate": "a_pour_parent", "object": "utilisateur"},
        {"subject": "utilisateur", "predicate": "a_pour_parent", "object": "Pierre"},
        {"subject": "utilisateur", "predicate": "possede", "object": "Tesla Model 3"},
        {"subject": "Julien", "predicate": "metier", "object": "kiné"},
        {"subject": "Julien", "predicate": "a_pour_frere", "object": "utilisateur"},
    ]
    retenus, rejets = normaliser(cas)
    print("RETENUS")
    for f in retenus:
        print(f"  {f['subject']} | {f['predicate']} | {f['object']}")
    print("REJETES")
    for f, motif in rejets:
        print(f"  {f.get('subject')} | {f.get('predicate')} | {f.get('object')}  →  {motif}")
