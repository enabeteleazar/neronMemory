"""Un message, une chaine, une ecriture.

Mesure le 08/09/2026 en production. Le message

    « Retiens que mon velo est un Decathlon Riverside. »

a produit DEUX enregistrements bruts et TROIS faits :

    velo     = "un Decathlon Riverside"   (extraction deterministe)
    possede  = "Decathlon Riverside"      (juge LLM)
    habite_a = "Riverside"                (juge LLM)

Le Coeur envoyait chaque message a /memory/observe AVANT de router, puis la
route memory_provider appelait /memory/remember : les deux chaines
d'extraction travaillaient sur le meme message, chacune ecrivant sa propre
version. Le troisieme fait montre le cout de l'affaire — la marque du velo
promue au rang de domicile, et promue d'emblee parce que la voie « ordre
explicite » creditait la sortie du JUGE comme une parole de l'utilisateur.

Ces tests verrouillent le cote Memory du correctif. Le cote Coeur (ne plus
observer un message qui part deja en memorisation) est verrouille par
tests/test_core_single_memory_pipeline.py dans le depot parent.
"""

from __future__ import annotations

import pytest

from memory.oblivia import provenance as prov
from memory.oblivia.manager import ObliviaMemoryManager
from memory.oblivia.schemas import KnowledgeFact, MemoryRecord


@pytest.fixture
def memoire(tmp_path) -> ObliviaMemoryManager:
    return ObliviaMemoryManager(
        sqlite_path=str(tmp_path / "memory.db"),
        obsidian_path=str(tmp_path / "obsidian"),
    )


def _faits_actifs(memoire):
    return [f for f in memoire.sqlite.list_facts() if not f.retracted]


class TestMemorisationExplicite:
    """Test 1 : une seule ecriture canonique."""

    def test_an_explicit_order_writes_one_fact(self, memoire):
        memoire.remember(MemoryRecord(
            content="mon velo est un Decathlon Riverside",
            source="utilisateur",
        ))

        assert len(_faits_actifs(memoire)) == 1

    def test_that_fact_is_credited_to_the_user(self, memoire):
        memoire.remember(MemoryRecord(
            content="mon velo est un Decathlon Riverside",
            source="utilisateur",
        ))

        assert _faits_actifs(memoire)[0].provenance == prov.EXPLICIT_USER

    def test_no_second_representation_appears(self, memoire):
        """Ni `possede`, ni `habite_a` : une seule relation."""
        memoire.remember(MemoryRecord(
            content="mon velo est un Decathlon Riverside",
            source="utilisateur",
        ))

        predicats = {f.predicate for f in _faits_actifs(memoire)}
        assert len(predicats) == 1
        assert "habite_a" not in predicats


class TestInformationSpontanee:
    """Test 2 : extraction unique, aucun fait parasite."""

    def test_a_spontaneous_statement_is_extracted_once(self, memoire):
        memoire.remember(MemoryRecord(
            content="Mon collegue s'appelle Killian.", source="utilisateur",
        ))

        faits = _faits_actifs(memoire)
        assert len(faits) == 1
        assert faits[0].object == "Killian"

    def test_no_family_relation_is_invented(self, memoire):
        memoire.remember(MemoryRecord(
            content="Mon collegue s'appelle Killian.", source="utilisateur",
        ))

        for fait in _faits_actifs(memoire):
            assert fait.predicate != "a_pour_enfant"


class TestIdempotence:
    """Tests 4 et 5 : rejouer ne doit rien ajouter."""

    def test_the_same_message_twice_writes_one_fact(self, memoire):
        for _ in range(2):
            memoire.remember(MemoryRecord(
                content="mon velo est un Decathlon Riverside",
                source="utilisateur",
            ))

        assert len(_faits_actifs(memoire)) == 1

    def test_an_identical_fact_is_never_stored_twice(self, memoire):
        """`animal_prefere = le renard` figurait trois fois en production."""
        fait = KnowledgeFact(
            subject="utilisateur", predicate="animal_prefere",
            object="le renard", provenance=prov.EXPLICIT_USER,
        )

        assert memoire.sqlite.add_fact(fait) is True
        for _ in range(4):
            assert memoire.sqlite.add_fact(KnowledgeFact(
                subject="utilisateur", predicate="animal_prefere",
                object="le renard", provenance=prov.EXPLICIT_USER,
            )) is False

        assert len(_faits_actifs(memoire)) == 1

    def test_the_two_chains_on_one_message_still_yield_one_fact(self, memoire):
        """Test 4 : le double appel artificiel observe() + remember().

        Meme si les deux chaines etaient declenchees, l'idempotence et la
        regle de provenance empechent la seconde representation d'entrer.
        """
        memoire.remember(MemoryRecord(
            content="mon velo est un Decathlon Riverside",
            source="utilisateur",
        ))
        # Ce que le juge aurait produit sur le meme message.
        memoire.sqlite.add_candidate(
            "mon velo", "possede", "Decathlon Riverside", "msg1", 2.0,
            "2026-09-08T09:00:00+00:00", prov.LLM_GENERATED,
        )
        memoire.sqlite.promote_candidates(2.0, "2026-09-08T09:00:00+00:00")

        assert len(_faits_actifs(memoire)) == 1


class TestQuestionsNEcriventRien:
    """Test 3 : une question lit, elle n'ecrit pas."""

    @pytest.mark.parametrize(
        "question",
        [
            "Comment s'appelle mon collegue ?",
            "Quel velo est-ce que je possede ?",
            "Reponds en un seul mot : capitale de la France ?",
        ],
    )
    def test_a_question_creates_no_fact_and_no_draft(self, memoire, question):
        from memory.app import est_une_question

        assert est_une_question(question) is True
        assert _faits_actifs(memoire) == []
        assert memoire.sqlite.list_candidates() == []

    def test_a_question_still_reads_what_is_known(self, memoire):
        memoire.remember(MemoryRecord(
            content="Mon collegue s'appelle Killian.", source="utilisateur",
        ))

        reponse = memoire.recall_knowledge("Comment s'appelle mon collegue ?")

        assert "Killian" in str(reponse.get("answer") or "")
        assert len(_faits_actifs(memoire)) == 1


class TestLaVoieRapideNeCreditePlusLeJuge:
    """Un ordre explicite ne transforme pas une supposition en preuve."""

    def test_a_judge_triplet_stays_a_hypothesis(self, memoire):
        """Le cas `habite_a = Riverside`, promu d'emblee en production."""
        memoire.sqlite.add_candidate(
            "mon velo", "habite_a", "Riverside", "msg1", 2.0,
            "2026-09-08T09:00:00+00:00", prov.LLM_GENERATED,
        )

        promus = memoire.sqlite.promote_candidates(
            2.0, "2026-09-08T09:00:00+00:00"
        )

        assert promus == []
        assert _faits_actifs(memoire) == []


class TestNouveauPredicat:
    """Test 6 : le vocabulaire reste ouvert."""

    def test_an_unknown_predicate_is_still_adopted(self, memoire):
        memoire.remember(MemoryRecord(
            content="mon velo est un Decathlon Riverside",
            source="utilisateur",
        ))

        assert "velo" in memoire.predicates.noms()

    def test_the_written_predicate_belongs_to_the_registry(self, memoire):
        memoire.remember(MemoryRecord(
            content="Mon collegue s'appelle Killian.", source="utilisateur",
        ))

        for fait in _faits_actifs(memoire):
            assert fait.predicate in memoire.predicates.noms()
