"""Validation deterministe des triplets sortis du juge LLM.

Le modele extrait, le code decide. Tout ce qui est mecaniquement corrigeable
l'est ici plutot que dans la consigne : sur un petit modele, chaque regle
ajoutee a la consigne coute un fait, alors qu'elle coute une milliseconde ici.
"""
from __future__ import annotations

import unicodedata

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

VOCABULAIRE = SYMETRIQUES | ORIENTEES | ATTRIBUTS
RELATIONS = SYMETRIQUES | ORIENTEES

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


def normaliser(faits: list[dict]) -> tuple[list[dict], list[tuple[dict, str]]]:
    """Renvoie (triplets retenus, [(triplet rejete, motif)])."""
    retenus: list[dict] = []
    rejets: list[tuple[dict, str]] = []
    vus: set[tuple[str, str, str]] = set()

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
        if predicat not in VOCABULAIRE:
            rejets.append((brut, f"predicat hors vocabulaire : {predicat}"))
            continue

        # Forme canonique : quand l utilisateur est implique dans une relation,
        # il est TOUJOURS le sujet. Sans cela le meme lien serait stocke tantot
        # dans un sens tantot dans l autre, et les requetes seraient peu fiables.
        if predicat in RELATIONS and plat(objet) == "utilisateur":
            sujet, objet = objet, sujet

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
        if f["predicate"] in ORIENTEES and miroir in index:
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
