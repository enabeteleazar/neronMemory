"""Le vocabulaire de predicats doit pouvoir s'enrichir seul.

Avant, `normaliser` rejetait tout predicat absent de trois `set()` ecrits
dans le code. Constate en production le 04/09/2026 : le juge avait produit
`a_pour_animal_prefere`, le triplet a ete rejete et le fait perdu.

Le vocabulaire du code est desormais un AMORCAGE ; la table `predicates`
fait autorite et memory l'enrichit lui-meme. Ces tests verrouillent les deux
moities du contrat : ce qui doit etre adopte, et ce qui doit encore etre
refuse.
"""

from __future__ import annotations

import pytest

from memory.oblivia.normalisation import (
    normaliser,
    predicat_structurellement_valide,
)
from memory.oblivia.predicate_discovery import PredicateRegistry
from memory.oblivia.schemas import MemoryRecord
from memory.oblivia.sqlite_adapter import SQLiteMemoryAdapter


@pytest.fixture
def registre(adapter) -> PredicateRegistry:
    return PredicateRegistry(adapter)


def _triplet(predicate: str, subject: str = "utilisateur", object: str = "bleu") -> dict:
    return {"subject": subject, "predicate": predicate, "object": object}


class TestAmorcage:
    def test_starter_vocabulary_is_loaded_into_the_table(self, registre):
        noms = registre.noms()

        assert "prenom" in noms
        assert "a_pour_frere" in noms
        assert len(noms) >= 20

    def test_seeding_is_idempotent(self, adapter):
        first = PredicateRegistry(adapter)
        avant = len(first.noms())

        second = PredicateRegistry(adapter)

        assert len(second.noms()) == avant

    def test_starter_predicates_keep_their_kind(self, registre):
        assert registre.est_relation("a_pour_frere") is True
        assert registre.est_orientee("a_pour_mere") is True
        assert registre.est_orientee("a_pour_frere") is False   # symetrique
        assert registre.est_relation("prenom") is False         # attribut


class TestPredicatConnu:
    def test_a_known_predicate_is_accepted_unchanged(self, registre):
        retenus, rejets = normaliser([_triplet("prenom", object="Eleazar")], registre)

        assert rejets == []
        assert retenus[0]["predicate"] == "prenom"

    def test_using_a_predicate_counts_an_usage(self, registre, adapter):
        normaliser([_triplet("prenom", object="Eleazar")], registre)

        assert adapter.list_predicates()["prenom"]["usages"] >= 1


class TestNouveauPredicat:
    def test_an_unknown_but_valid_predicate_is_accepted(self, registre):
        retenus, rejets = normaliser([_triplet("couleur_preferee")], registre)

        assert rejets == [], f"rejete a tort : {rejets}"
        assert retenus[0]["predicate"] == "couleur_preferee"

    def test_an_accepted_predicate_is_recorded(self, registre, adapter):
        normaliser([_triplet("couleur_preferee")], registre)

        entree = adapter.list_predicates()["couleur_preferee"]
        assert entree["origine"] == "appris"
        assert entree["kind"] == "attribut"

    def test_the_example_that_introduced_it_is_kept(self, registre, adapter):
        """Pouvoir expliquer d'ou vient un predicat appris."""
        normaliser([_triplet("couleur_preferee")], registre)

        exemple = adapter.list_predicates()["couleur_preferee"]["exemple"]
        assert exemple and "couleur_preferee" in exemple

    def test_several_new_predicates_are_all_recorded(self, registre, adapter):
        retenus, rejets = normaliser(
            [
                _triplet("couleur_preferee", object="bleu"),
                _triplet("boisson_preferee", object="the"),
                _triplet("sport_pratique", object="escalade"),
            ],
            registre,
        )

        assert rejets == []
        assert len(retenus) == 3
        noms = set(adapter.list_predicates())
        assert {"couleur_preferee", "boisson_preferee", "sport_pratique"} <= noms

    def test_a_new_relation_gets_the_relation_kind(self, registre):
        """Le prefixe a_pour_ vaut relation : la forme canonique en depend."""
        normaliser(
            [{"subject": "Luc", "predicate": "a_pour_cousin", "object": "utilisateur"}],
            registre,
        )

        assert registre.est_relation("a_pour_cousin") is True

    def test_a_learned_relation_puts_the_user_as_subject(self, registre):
        """Verifie que le classement produit bien la forme canonique."""
        retenus, _ = normaliser(
            [{"subject": "Luc", "predicate": "a_pour_cousin", "object": "utilisateur"}],
            registre,
        )

        assert retenus[0]["subject"] == "utilisateur"
        assert retenus[0]["object"] == "Luc"


class TestPredicatInvalide:
    """La validation n'est pas supprimee, elle change de nature."""

    @pytest.mark.parametrize(
        "predicat",
        [
            "a",                                   # trop court
            "predicat_beaucoup_trop_long_pour_etre_un_predicat_credible",
            "trop_de_segments_dans_ce_predicat",   # > 4 segments
        ],
    )
    def test_structurally_invalid_predicates_are_still_rejected(self, registre, predicat):
        retenus, rejets = normaliser([_triplet(predicat)], registre)

        assert retenus == []
        assert len(rejets) == 1

    def test_a_whole_sentence_is_not_adopted_as_a_predicate(self, registre, adapter):
        """Le garde-fou principal : sans lui, le modele peut faire entrer
        n'importe quelle phrase dans le vocabulaire."""
        phrase = "l utilisateur aime beaucoup le cafe le matin"

        retenus, rejets = normaliser([_triplet(phrase)], registre)

        assert retenus == []
        assert rejets
        assert not any("aime_beaucoup" in n for n in adapter.list_predicates())

    def test_rejection_states_a_reason(self, registre):
        _, rejets = normaliser([_triplet("a")], registre)

        assert rejets[0][1]


class TestVariantes:
    """Ne pas peupler le vocabulaire de doublons semantiques."""

    def test_a_pour_prefix_is_mapped_to_the_existing_attribute(self, registre, adapter):
        """Le cas reel du 04/09 : a_pour_animal_prefere -> animal_prefere."""
        retenus, rejets = normaliser(
            [_triplet("a_pour_animal_prefere", object="pangolin")], registre
        )

        assert rejets == []
        assert retenus[0]["predicate"] == "animal_prefere"
        assert "a_pour_animal_prefere" not in adapter.list_predicates()

    def test_a_plural_is_mapped_to_the_known_singular(self, registre, adapter):
        retenus, _ = normaliser([_triplet("prenoms", object="Eleazar")], registre)

        assert retenus[0]["predicate"] == "prenom"
        assert "prenoms" not in adapter.list_predicates()

    def test_the_same_new_predicate_twice_is_recorded_once(self, registre, adapter):
        normaliser([_triplet("couleur_preferee", object="bleu")], registre)
        normaliser([_triplet("couleur_preferee", object="vert")], registre)

        entrees = [n for n in adapter.list_predicates() if n == "couleur_preferee"]
        assert len(entrees) == 1
        assert adapter.list_predicates()["couleur_preferee"]["usages"] >= 2


class TestPersistance:
    def test_a_learned_predicate_survives_a_restart(self, db_path):
        """Le critere central : redemarrer ne doit rien desapprendre."""
        premier = PredicateRegistry(SQLiteMemoryAdapter(db_path))
        normaliser([_triplet("couleur_preferee")], premier)

        # Nouvel adaptateur et nouveau registre : ce que ferait un redemarrage.
        second = PredicateRegistry(SQLiteMemoryAdapter(db_path))

        assert "couleur_preferee" in second.noms()

    def test_a_learned_predicate_is_reusable_after_restart(self, db_path):
        premier = PredicateRegistry(SQLiteMemoryAdapter(db_path))
        normaliser([_triplet("couleur_preferee", object="bleu")], premier)

        second = PredicateRegistry(SQLiteMemoryAdapter(db_path))
        retenus, rejets = normaliser(
            [_triplet("couleur_preferee", object="vert")], second
        )

        assert rejets == []
        assert retenus[0]["predicate"] == "couleur_preferee"

    def test_restart_does_not_downgrade_a_learned_predicate_to_seed(self, db_path):
        premier = PredicateRegistry(SQLiteMemoryAdapter(db_path))
        normaliser([_triplet("couleur_preferee")], premier)

        adapter = SQLiteMemoryAdapter(db_path)
        PredicateRegistry(adapter)

        assert adapter.list_predicates()["couleur_preferee"]["origine"] == "appris"


class TestCompatibilite:
    def test_normaliser_without_registry_keeps_the_closed_vocabulary(self):
        """Appelee sans registre, la fonction garde son ancien comportement —
        les tests de normalisation existants s'en servent."""
        retenus, rejets = normaliser([_triplet("couleur_preferee")])

        assert retenus == []
        assert "hors vocabulaire" in rejets[0][1]

    def test_existing_memory_data_is_untouched_by_seeding(self, adapter):
        """L'arrivee du vocabulaire ne doit toucher ni faits ni messages."""
        adapter.save_record(MemoryRecord(content="souvenir anterieur"))
        avant = adapter.status()

        PredicateRegistry(adapter)

        assert adapter.status() == avant

    def test_structural_validation_is_reusable_on_its_own(self):
        assert predicat_structurellement_valide("couleur_preferee")[0] is True
        assert predicat_structurellement_valide("Couleur Preferee")[0] is False
        assert predicat_structurellement_valide("")[0] is False
