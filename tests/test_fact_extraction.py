"""Extraction de faits et normalisation — la logique metier de memory.

Ces regles decident de ce que Neron retient d'une phrase. Une regression ici
ne fait pas planter le service : elle lui fait retenir des choses fausses,
ou ne rien retenir du tout. C'est precisement le genre de defaut qu'aucun
test ne couvrait.
"""

from __future__ import annotations

import pytest

from memory.fact_extractor import FactExtractor
from memory.oblivia.normalisation import normaliser, plat


@pytest.fixture
def extractor() -> FactExtractor:
    return FactExtractor()


class TestExtraction:
    def test_a_stated_preference_becomes_a_fact(self, extractor):
        facts = extractor.extract("J'aime le chocolat.")

        assert facts
        assert facts[0].subject == "user"
        assert "chocolat" in facts[0].object.lower()

    def test_a_neutral_sentence_yields_no_fact(self, extractor):
        """Ne rien deduire est un resultat valide — pas une erreur."""
        assert extractor.extract("Bonjour.") == []

    def test_extraction_is_stable_across_calls(self, extractor):
        """Meme entree, meme sortie : sans cela, la memoire derive."""
        first = extractor.extract("J'aime le chocolat.")
        second = extractor.extract("J'aime le chocolat.")

        assert [(f.subject, f.relation, f.object) for f in first] == \
               [(f.subject, f.relation, f.object) for f in second]

    def test_empty_input_is_handled(self, extractor):
        assert extractor.extract("") == []

    def test_a_denial_marks_the_fact_for_retraction(self, extractor):
        """« je n'ai jamais habite a X » doit RETIRER, pas ajouter."""
        facts = extractor.extract("je n'ai jamais habite a Lyon")

        assert facts
        assert facts[0].metadata.get("retract") is True


class TestNormalisation:
    def test_plat_removes_accents_and_case(self):
        assert plat("Éléazar") == plat("eleazar")

    # Nota : `normaliser` attend des cles ANGLAISES (subject/predicate/object)
    # alors que le vocabulaire des predicats est en francais (prenom,
    # a_pour_frere...). Un triplet passe en francais est silencieusement
    # rejete pour « champ vide » — piege verifie ci-dessous.

    def test_a_valid_triplet_is_kept(self):
        retenus, rejets = normaliser(
            [{"subject": "utilisateur", "predicate": "prenom", "object": "Eleazar"}]
        )

        assert len(retenus) == 1
        assert rejets == []

    def test_french_keys_are_rejected_as_empty_fields(self):
        """Documente le piege : seules les cles anglaises sont lues."""
        retenus, rejets = normaliser(
            [{"sujet": "utilisateur", "predicat": "prenom", "objet": "Eleazar"}]
        )

        assert retenus == []
        assert rejets and "champ vide" in rejets[0][1]

    def test_duplicates_are_collapsed(self):
        """Repeter une information ne doit pas la dupliquer en memoire."""
        triplet = {"subject": "utilisateur", "predicate": "prenom", "object": "Eleazar"}

        retenus, _ = normaliser([triplet, dict(triplet)])

        assert len(retenus) == 1

    def test_user_is_always_the_subject_of_a_relation(self):
        """Forme canonique : sans elle, le meme lien serait stocke tantot
        dans un sens tantot dans l'autre, et les requetes deviendraient
        peu fiables."""
        retenus, _ = normaliser(
            [{"subject": "Julien", "predicate": "a_pour_frere", "object": "utilisateur"}]
        )

        assert len(retenus) == 1
        assert plat(retenus[0]["subject"]) == "utilisateur"

    def test_a_triplet_whose_subject_equals_its_object_is_rejected(self):
        retenus, rejets = normaliser(
            [{"subject": "utilisateur", "predicate": "prenom", "object": "utilisateur"}]
        )

        assert retenus == []
        assert rejets

    def test_an_unknown_predicate_is_rejected_with_a_reason(self):
        """Le vocabulaire est ferme : un predicat inconnu doit etre ecarte,
        et le motif conserve pour pouvoir l'expliquer."""
        retenus, rejets = normaliser(
            [{"subject": "utilisateur", "predicate": "predicat_inexistant", "object": "x"}]
        )

        assert retenus == []
        assert len(rejets) == 1
        assert rejets[0][1]

    def test_empty_input_yields_empty_output(self):
        assert normaliser([]) == ([], [])
