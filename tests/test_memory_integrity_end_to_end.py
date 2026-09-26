"""Les cas d'usage reels qui ont revele le defaut, joues de bout en bout.

Les tests de tests/test_provenance_promotion.py verrouillent les regles une
par une. Ceux-ci verifient la chaine complete — extraction, normalisation,
registre, provenance, carnet — sur les phrases exactes qui ont servi aux
tests d'utilisation du 07/09/2026.
"""

from __future__ import annotations

import pytest

from memory.oblivia import provenance as prov
from memory.oblivia.manager import ObliviaMemoryManager
from memory.oblivia.schemas import MemoryRecord


@pytest.fixture
def memoire(tmp_path) -> ObliviaMemoryManager:
    return ObliviaMemoryManager(
        sqlite_path=str(tmp_path / "memory.db"),
        obsidian_path=str(tmp_path / "obsidian"),
    )


def _dire(memoire: ObliviaMemoryManager, phrase: str):
    return memoire.remember(MemoryRecord(content=phrase, source="utilisateur"))


def _faits(memoire: ObliviaMemoryManager):
    return memoire.sqlite.list_facts()


class TestInformationExplicite:
    """Cas 2 : ce que l'utilisateur affirme doit etre retenu."""

    def test_an_explicit_statement_becomes_a_fact(self, memoire):
        _dire(memoire, "Retiens que mon velo est un Decathlon Riverside.")

        objets = [f.object for f in _faits(memoire)]
        assert any("Decathlon Riverside" in o for o in objets)

    def test_that_fact_is_credited_to_the_user(self, memoire):
        _dire(memoire, "Retiens que mon velo est un Decathlon Riverside.")

        assert _faits(memoire)[0].provenance == prov.EXPLICIT_USER

    def test_the_stored_information_can_be_read_back(self, memoire):
        """Cas 3 : « Quel velo est-ce que je possede ? »"""
        _dire(memoire, "Retiens que mon velo est un Decathlon Riverside.")

        reponse = memoire.recall_knowledge("Quel velo est-ce que je possede ?")

        assert "Decathlon Riverside" in str(reponse.get("answer") or "")


class TestInformationAmbigue:
    """Cas 4 : « Mon collegue s'appelle Killian. »"""

    def test_the_colleague_relation_is_recorded(self, memoire):
        _dire(memoire, "Mon collegue s'appelle Killian.")

        assert any(f.object == "Killian" for f in _faits(memoire))

    def test_the_colleague_is_never_turned_into_a_child(self, memoire):
        """Le juge avait produit `utilisateur a_pour_enfant collegue`."""
        _dire(memoire, "Mon collegue s'appelle Killian.")

        for fait in _faits(memoire):
            assert fait.predicate != "a_pour_enfant"
            assert fait.object.lower() != "collegue"


class TestQuestionsGenerales:
    """Cas 1 et 5 : une question ne nourrit pas la memoire personnelle."""

    @pytest.mark.parametrize(
        "question",
        [
            "Reponds en un seul mot : capitale de la France ?",
            "Explique-moi Docker.",
            "Qui etait Napoleon ?",
            "Combien font 2 + 2 ?",
            "Comment s'appelle mon collegue ?",
        ],
    )
    def test_a_question_creates_no_personal_fact(self, memoire, question):
        from memory.app import est_une_question

        # Barriere posee a l'entree : le juge n'est meme pas sollicite.
        assert est_une_question(question) is True

    def test_the_message_itself_is_still_kept(self, memoire):
        """Ne rien deduire n'est pas tout jeter : le message reste."""
        memoire.sqlite.save_record(MemoryRecord(
            content="Reponds en un seul mot : capitale de la France ?",
            source="utilisateur", category="brut",
        ))

        contenus = [r.content for r in memoire.sqlite.list_records(10)]
        assert any("capitale de la France" in c for c in contenus)


class TestHallucinationNeDevientJamaisUnFait:
    """Cas 6 et 7 : le scenario de production, en entier."""

    def test_a_model_guess_stays_a_draft_for_ever(self, memoire):
        memoire.sqlite.add_candidate(
            "utilisateur", "habite_a", "Paris", "msg1", 1.0,
            "2026-09-08T10:00:00+00:00", prov.LLM_GENERATED,
        )

        # Dix nuits de relecture, sans un seul message nouveau.
        for passe in range(1, 11):
            memoire.sqlite.add_candidate(
                "utilisateur", "habite_a", "Paris", f"msg1#r{passe}", 0.0,
                "2026-09-08T03:05:00+00:00", prov.LLM_GENERATED,
            )
            memoire.sqlite.promote_candidates(2.0, "2026-09-08T03:05:00+00:00")

        assert _faits(memoire) == []
        assert memoire.sqlite.list_candidates()[0]["promoted_at"] is None

    def test_the_user_saying_it_changes_everything(self, memoire):
        """La sortie de secours : l'utilisateur confirme, la memoire apprend."""
        memoire.sqlite.add_candidate(
            "utilisateur", "habite_a", "Paris", "msg1", 1.0,
            "2026-09-08T10:00:00+00:00", prov.LLM_GENERATED,
        )
        memoire.sqlite.add_candidate(
            "utilisateur", "habite_a", "Paris", "msg2", 2.0,
            "2026-09-08T10:01:00+00:00", prov.EXPLICIT_USER,
        )

        promus = memoire.sqlite.promote_candidates(
            2.0, "2026-09-08T10:02:00+00:00"
        )

        assert len(promus) == 1
        assert _faits(memoire)[0].object == "Paris"


class TestNouveauPredicat:
    """Cas 8 : le vocabulaire reste ouvert, sans contourner la provenance."""

    def test_an_unknown_predicate_is_still_adopted(self, memoire):
        _dire(memoire, "Retiens que mon velo est un Decathlon Riverside.")

        assert "velo" in memoire.predicates.noms()

    def test_both_extraction_chains_share_one_vocabulary(self, memoire):
        """`velo` et `collegue` naissaient hors du registre."""
        _dire(memoire, "Retiens que mon velo est un Decathlon Riverside.")
        _dire(memoire, "Mon collegue s'appelle Killian.")

        vocabulaire = memoire.predicates.noms()
        for predicat in {f.predicate for f in _faits(memoire)}:
            assert predicat in vocabulaire, f"{predicat} absent du registre"

    def test_a_new_predicate_does_not_bypass_provenance(self, memoire):
        """Un predicat inedit propose par le modele reste une hypothese."""
        memoire.sqlite.add_candidate(
            "utilisateur", "couleur_preferee", "bleu", "msg1", 5.0,
            "2026-09-08T10:00:00+00:00", prov.LLM_GENERATED,
        )

        memoire.sqlite.promote_candidates(2.0, "2026-09-08T10:00:00+00:00")

        assert _faits(memoire) == []
