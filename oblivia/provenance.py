"""Provenance des informations : d'ou vient ce que la memoire croit savoir.

Constate le 07/09/2026 en production. La question « Reponds en un seul mot :
capitale de la France ? » a produit deux faits personnels :

    utilisateur | habite_a | Paris
    utilisateur | metier   | ingenieur

Rien dans le message ne les justifiait. Le juge (qwen3:1.7b) les a inventes,
et rien entre lui et le carnet ne distinguait « l'utilisateur l'a dit » de
« le modele l'a suppose ». Ces hypotheses valaient 1 point ; la relecture
nocturne en ajoutait 0.5 par passe ; le seuil de promotion etant 2.0, quatre
nuits suffisaient pour qu'une hallucination devienne un fait etabli.

Ce module donne un statut epistemique a chaque information. Il ne remplace
pas `MemoryRecord.source`, qui decrit le CANAL par lequel un message est
arrive (utilisateur, agent, externe...) : la provenance dit ce qu'on est en
droit de CROIRE, ce qui est une autre question.

Regle directrice : en l'absence de preuve, la memoire prefere ne rien savoir
plutot que de savoir faux.
"""

from __future__ import annotations

from typing import Final

# ── Vocabulaire ────────────────────────────────────────────────────────

#: L'utilisateur a explicitement demande de retenir (« Retiens que... »).
EXPLICIT_USER: Final = "explicit_user"
#: L'utilisateur a confirme une hypothese qu'on lui a soumise.
USER_CONFIRMED: Final = "user_confirmed"
#: Une source du systeme tenue pour fiable (pas un modele de langage).
TRUSTED_SOURCE: Final = "trusted_source"
#: Deduit d'une affirmation de l'utilisateur par une regle deterministe.
INFERRED: Final = "inferred"
#: Produit par un modele de langage. Aucune valeur de preuve en soi.
LLM_GENERATED: Final = "llm_generated"
#: Provenance inconnue.
UNKNOWN: Final = "unknown"
#: Anterieur a l'introduction de la provenance (migration du 08/09/2026).
LEGACY: Final = "legacy"

PROVENANCES: Final = frozenset({
    EXPLICIT_USER, USER_CONFIRMED, TRUSTED_SOURCE,
    INFERRED, LLM_GENERATED, UNKNOWN, LEGACY,
})

# ── Ce que chaque provenance autorise ──────────────────────────────────

#: Provenances qui valent preuve a elles seules : un seul message suffit.
#: L'utilisateur a parle, on le croit.
PREUVE_DIRECTE: Final = frozenset({EXPLICIT_USER, USER_CONFIRMED, TRUSTED_SOURCE})

#: Provenances qui ne sont que des hypotheses. Elles exigent une
#: corroboration par des messages DISTINCTS — jamais par le temps.
HYPOTHESE: Final = frozenset({INFERRED, LLM_GENERATED})

#: Provenances qui ne doivent JAMAIS etre promues automatiquement.
#: Seule une confirmation explicite de l'utilisateur peut les faire passer.
JAMAIS_AUTOMATIQUE: Final = frozenset({UNKNOWN, LEGACY})

#: Nombre de messages distincts exiges pour promouvoir une hypothese.
#: Deux messages differents disant la meme chose, ce n'est plus une
#: coincidence. Deux RELECTURES du meme message, si.
MESSAGES_DISTINCTS_REQUIS: Final = 2


def normaliser(provenance: str | None) -> str:
    """Ramene une provenance a une valeur connue ; UNKNOWN par defaut."""
    valeur = (provenance or "").strip().lower()
    return valeur if valeur in PROVENANCES else UNKNOWN


def peut_etre_promu(
    provenance: str | None,
    points: float,
    messages_distincts: int,
    seuil: float,
) -> tuple[bool, str]:
    """Cette information a-t-elle assez de preuve pour entrer au carnet ?

    Renvoie (verdict, motif). Le motif sert au journal : quand la memoire
    refuse de savoir quelque chose, on doit pouvoir dire pourquoi.

    Le score seul ne suffit plus. C'est le coeur du correctif : `points`
    melangeait la corroboration (des messages differents) et l'anciennete
    (des relectures du meme message). Seul `messages_distincts` mesure une
    vraie corroboration — la relecture ne le fait pas monter.
    """
    provenance = normaliser(provenance)

    if provenance in JAMAIS_AUTOMATIQUE:
        return False, f"provenance {provenance} : confirmation utilisateur requise"

    if provenance in PREUVE_DIRECTE:
        if points >= seuil:
            return True, f"provenance {provenance}"
        return False, f"provenance {provenance} mais {points} < {seuil} point(s)"

    # Hypothese : le score ne suffit pas, il faut plusieurs messages.
    if messages_distincts < MESSAGES_DISTINCTS_REQUIS:
        return False, (
            f"provenance {provenance} : {messages_distincts} message(s) distinct(s), "
            f"{MESSAGES_DISTINCTS_REQUIS} requis — le temps ne vaut pas preuve"
        )
    if points < seuil:
        return False, f"provenance {provenance} : {points} < {seuil} point(s)"
    return True, f"provenance {provenance} corroboree par {messages_distincts} messages"
