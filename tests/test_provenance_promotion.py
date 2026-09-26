"""Le temps ne vaut pas preuve.

Constate en production le 07/09/2026. La question « Reponds en un seul mot :
capitale de la France ? » a produit deux faits personnels inventes :

    utilisateur | habite_a | Paris      (1.0 point)
    utilisateur | metier   | ingenieur  (1.0 point)

et « Comment s'appelle mon collegue ? » a produit :

    utilisateur | a_pour_enfant | collegue

Ces hypotheses n'etaient pas condamnees a rester des hypotheses : la
relecture nocturne versait 0.5 point par passe et le seuil de promotion
etait 2.0. Deux nuits suffisaient pour qu'une invention du modele devienne
un fait etabli, sans que l'utilisateur ait rien confirme.

Ces tests verrouillent les trois barrieres posees contre ce scenario :
une question ne produit rien, une relecture ne rapporte rien, et une
hypothese exige des messages DISTINCTS pour etre promue.
"""

from __future__ import annotations

import pytest

from memory.app import est_une_question
from memory.oblivia import provenance as prov
from memory.oblivia.schemas import KnowledgeFact


def _candidat(adapter, sujet, predicat, objet, cle, points, provenance):
    return adapter.add_candidate(
        sujet, predicat, objet, cle, points, "2026-09-08T10:00:00+00:00",
        provenance,
    )


class TestQuestionsNeProduisentRien:
    """Premiere barriere : ne pas soumettre une question au juge."""

    @pytest.mark.parametrize(
        "texte",
        [
            "Reponds en un seul mot : capitale de la France ?",
            "Explique-moi Docker.",
            "Qui etait Napoleon ?",
            "Quelle est la capitale de l'Allemagne ?",
            "Combien font 2 + 2 ?",
            "Comment s'appelle mon collegue ?",
            "Ou est-ce que j'habite ?",
        ],
    )
    def test_a_question_is_recognised(self, texte):
        assert est_une_question(texte) is True

    @pytest.mark.parametrize(
        "texte",
        [
            "Mon collegue s'appelle Killian.",
            "Retiens que mon velo est un Decathlon Riverside.",
            "J'habite a Saron-sur-Aube.",
            "Je travaille chez Constructel.",
        ],
    )
    def test_a_statement_is_not_a_question(self, texte):
        assert est_une_question(texte) is False

    def test_an_empty_message_is_not_a_question(self):
        assert est_une_question("") is False
        assert est_une_question("   ") is False


class TestProvenanceDecide:
    """Deuxieme barriere : le score ne decide plus seul."""

    def test_an_explicit_user_statement_is_proof_enough(self):
        autorise, _ = prov.peut_etre_promu(prov.EXPLICIT_USER, 2.0, 1, 2.0)
        assert autorise is True

    def test_a_model_hypothesis_needs_distinct_messages(self):
        """Le cas exact du 07/09 : 1 message, un score gonfle par le temps."""
        autorise, motif = prov.peut_etre_promu(prov.LLM_GENERATED, 5.0, 1, 2.0)

        assert autorise is False
        assert "le temps ne vaut pas preuve" in motif

    def test_a_model_hypothesis_corroborated_twice_may_pass(self):
        autorise, _ = prov.peut_etre_promu(prov.LLM_GENERATED, 2.0, 2, 2.0)
        assert autorise is True

    @pytest.mark.parametrize("origine", [prov.UNKNOWN, prov.LEGACY])
    def test_unknown_provenance_is_never_promoted_automatically(self, origine):
        autorise, motif = prov.peut_etre_promu(origine, 99.0, 99, 2.0)

        assert autorise is False
        assert "confirmation utilisateur" in motif

    def test_an_unlisted_provenance_falls_back_to_unknown(self):
        assert prov.normaliser("n_importe_quoi") == prov.UNKNOWN
        assert prov.normaliser(None) == prov.UNKNOWN


class TestRelectureNeProuveRien:
    """Troisieme barriere : relire n'est pas apprendre."""

    def test_rereads_do_not_raise_the_distinct_message_count(self, adapter):
        """Le compteur de messages distincts ignore les cles #rN."""
        _candidat(adapter, "utilisateur", "habite_a", "Paris", "msg1", 1.0,
                  prov.LLM_GENERATED)
        for passe in range(1, 6):
            _candidat(adapter, "utilisateur", "habite_a", "Paris",
                      f"msg1#r{passe}", 0.0, prov.LLM_GENERATED)

        fiche = adapter.list_candidates()[0]
        assert fiche["message_count"] == 1

    def test_a_hallucination_survives_five_rereads_without_being_promoted(
        self, adapter
    ):
        """Le scenario de production, joue en entier."""
        _candidat(adapter, "utilisateur", "habite_a", "Paris", "msg1", 1.0,
                  prov.LLM_GENERATED)
        for passe in range(1, 6):
            _candidat(adapter, "utilisateur", "habite_a", "Paris",
                      f"msg1#r{passe}", 0.0, prov.LLM_GENERATED)
            adapter.promote_candidates(2.0, "2026-09-08T03:05:00+00:00")

        assert adapter.list_facts() == []

    def test_even_a_score_above_the_threshold_is_not_enough(self, adapter):
        """Ceinture et bretelles : meme si des points arrivaient malgre tout."""
        _candidat(adapter, "utilisateur", "habite_a", "Paris", "msg1", 1.0,
                  prov.LLM_GENERATED)
        for passe in range(1, 6):
            _candidat(adapter, "utilisateur", "habite_a", "Paris",
                      f"msg1#r{passe}", 0.5, prov.LLM_GENERATED)

        fiche = adapter.list_candidates()[0]
        assert fiche["points"] >= 2.0          # le seuil est atteint
        assert fiche["message_count"] == 1     # mais rien ne le corrobore

        adapter.promote_candidates(2.0, "2026-09-08T03:05:00+00:00")
        assert adapter.list_facts() == []


class TestPromotionLegitime:
    """La memoire doit continuer d'apprendre ce qui est prouve."""

    def test_an_explicit_order_is_promoted_at_once(self, adapter):
        _candidat(adapter, "utilisateur", "possede", "Decathlon Riverside",
                  "msg1", 2.0, prov.EXPLICIT_USER)

        promus = adapter.promote_candidates(2.0, "2026-09-08T10:00:00+00:00")

        assert len(promus) == 1
        assert promus[0]["object"] == "Decathlon Riverside"
        assert adapter.list_facts()[0].provenance == prov.EXPLICIT_USER

    def test_two_distinct_messages_corroborate_a_hypothesis(self, adapter):
        _candidat(adapter, "utilisateur", "habite_a", "Troyes", "msg1", 1.0,
                  prov.LLM_GENERATED)
        _candidat(adapter, "utilisateur", "habite_a", "Troyes", "msg2", 1.0,
                  prov.LLM_GENERATED)

        promus = adapter.promote_candidates(2.0, "2026-09-08T10:00:00+00:00")

        assert len(promus) == 1
        assert adapter.list_facts()[0].object == "Troyes"

    def test_the_user_can_confirm_a_draft_by_hand(self, adapter):
        """Promotion nominative : l'utilisateur valide une fiche precise."""
        _candidat(adapter, "utilisateur", "habite_a", "Paris", "msg1", 1.0,
                  prov.LLM_GENERATED)
        fiche = adapter.list_candidates()[0]

        promus = adapter.promote_candidates(
            2.0, "2026-09-08T10:00:00+00:00", candidate_id=fiche["id"]
        )

        assert len(promus) == 1
        assert adapter.list_facts()[0].provenance == prov.USER_CONFIRMED

    def test_a_user_confirmation_upgrades_an_earlier_guess(self, adapter):
        """L'utilisateur confirme ce que le modele avait suppose."""
        _candidat(adapter, "utilisateur", "habite_a", "Troyes", "msg1", 1.0,
                  prov.LLM_GENERATED)
        etat = _candidat(adapter, "utilisateur", "habite_a", "Troyes", "msg2",
                         1.0, prov.EXPLICIT_USER)

        assert etat["provenance"] == prov.EXPLICIT_USER

    def test_provenance_never_degrades(self, adapter):
        _candidat(adapter, "utilisateur", "habite_a", "Troyes", "msg1", 2.0,
                  prov.EXPLICIT_USER)
        etat = _candidat(adapter, "utilisateur", "habite_a", "Troyes", "msg2",
                         1.0, prov.LLM_GENERATED)

        assert etat["provenance"] == prov.EXPLICIT_USER


class TestPersistance:
    def test_a_fact_keeps_its_provenance_across_a_reload(self, adapter, db_path):
        from memory.oblivia.sqlite_adapter import SQLiteMemoryAdapter

        adapter.add_fact(KnowledgeFact(
            subject="utilisateur", predicate="prenom", object="Eleazar",
            provenance=prov.EXPLICIT_USER,
        ))

        relu = SQLiteMemoryAdapter(db_path).list_facts()[0]
        assert relu.provenance == prov.EXPLICIT_USER

    def test_a_fact_written_without_provenance_is_not_trusted(self, adapter):
        """Le defaut doit etre le cas le plus defavorable, jamais l'inverse."""
        adapter.add_fact(KnowledgeFact(
            subject="utilisateur", predicate="prenom", object="Eleazar",
        ))

        assert adapter.list_facts()[0].provenance == prov.UNKNOWN
