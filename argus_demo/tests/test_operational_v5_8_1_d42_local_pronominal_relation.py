"""D42J — local overt-pronominal subject relations and invariants."""

from __future__ import annotations

from dataclasses import replace

import pytest

from curunir_operational.v5_8_1 import admissibility as AD
from curunir_operational.v5_8_1 import roles_v2 as V2

pytestmark = pytest.mark.no_db

RELATION = "LOCAL_OVERT_PRONOMINAL_SUBJECT_OF"


def _build(text: str, language: str):
    candidates, analyses, _signals, _script = V2.build_lattice(
        text=text, language=language
    )
    return candidates, analyses


def _relation_analyses(text: str, language: str):
    _candidates, analyses = _build(text, language)
    return [
        analysis
        for analysis in analyses
        if any(
            relation.relation_type == RELATION
            for relation in analysis.role_relations
        )
    ]


def _pairs(text: str, language: str):
    return {
        (analysis.subject.text, analysis.predicate_head.text)
        for analysis in _relation_analyses(text, language)
    }


def test_german_maximal_coordination_excludes_truncated_pronoun():
    text = (
        "Sie oder er muss über die für die Erfüllung der Aufgaben "
        "erforderliche Qualifikation verfügen."
    )
    pairs = _pairs(text, "de")
    assert ("Sie oder er", "muss") in pairs
    assert ("Sie", "muss") not in pairs


@pytest.mark.parametrize(
    "language,text,subject,head",
    [
        (
            "de",
            "Sie soll in einem angemessenen Verhältnis zu den Aufgaben stehen.",
            "Sie",
            "soll",
        ),
        (
            "de",
            "sie hierzu aus rechtlichen Gründen nicht in der Lage ist;",
            "sie",
            "ist",
        ),
        ("de", "Dies gilt nicht für § 3a.", "Dies", "gilt"),
        (
            "ru",
            (
                "предотвратимой смертности, что особенно ярко проявилось "
                "в ходе пандемии COVID-19,"
            ),
            "что",
            "проявилось",
        ),
        (
            "ru",
            (
                "Они заключаются между Советом Безопасности и Членами "
                "Организации."
            ),
            "Они",
            "заключаются",
        ),
        (
            "fr",
            "Il convie tout Membre des Nations Unies à s'associer à lui.",
            "Il",
            "convie",
        ),
    ],
)
def test_closed_pronoun_is_related_to_its_local_finite_head(
        language, text, subject, head):
    assert (subject, head) in _pairs(text, language)


def test_english_passive_modal_preserves_both_typed_heads():
    pairs = _pairs(
        "It must be mitigated pursuant to section 49 (1).", "en"
    )
    assert ("It", "must") in pairs
    assert ("It", "must be mitigated") in pairs


@pytest.mark.parametrize(
    "language,text",
    [
        (
            "it",
            (
                "Il Ministro dell'economia e delle finanze è autorizzato "
                "ad apportare le variazioni."
            ),
        ),
        (
            "ru",
            (
                "используются или создаются материалы, технологии или "
                "информация, которые, помимо использования"
            ),
        ),
        (
            "ru",
            (
                "отмечая также, что Секретариат ВОЗ разработал "
                "стратегические и оперативные"
            ),
        ),
        ("en", "The board invited it to participate.",),
    ],
)
def test_articles_cross_clause_forms_complementizers_and_objects_are_negative(
        language, text):
    assert not _relation_analyses(text, language)


def test_relation_attaches_only_to_the_exact_subject_head_pair():
    text = "Sie soll begründet werden."
    _candidates, analyses = _build(text, "de")
    for analysis in analyses:
        for relation in analysis.role_relations:
            if relation.relation_type != RELATION:
                continue
            assert analysis.subject is not None
            assert analysis.predicate_head is not None
            assert (
                relation.subject_candidate_id
                == analysis.subject.candidate_id
            )
            assert (
                relation.predicate_head_candidate_id
                == analysis.predicate_head.candidate_id
            )
            assert relation.evidence_span == (
                analysis.subject.span[0],
                analysis.predicate_head.span[1],
            )


def test_relation_precedence_keeps_null_alternative_admissible():
    text = "Sie soll begründet werden."
    _candidates, analyses = _build(text, "de")
    admitted, assessments = AD.admissible_analyses(
        analyses, text, unit_id="d42j-relation-precedence"
    )
    relation_analysis = next(
        analysis
        for analysis in admitted
        if any(
            relation.relation_type == RELATION
            for relation in analysis.role_relations
        )
    )
    same_head_null = next(
        analysis
        for analysis in admitted
        if analysis.subject is None
        and analysis.predicate_head is not None
        and analysis.predicate_head.span
        == relation_analysis.predicate_head.span
    )
    state, chosen, _reason = V2.resolve_internal(admitted)
    assessment_by_object = {
        id(analysis): assessment
        for analysis, assessment in zip(analyses, assessments)
    }
    assert assessment_by_object[id(relation_analysis)].competes
    assert assessment_by_object[id(same_head_null)].competes
    assert chosen is relation_analysis
    assert state == "UNIQUE_BINDING_ESTABLISHED"


def test_two_material_relation_bearers_preserve_ambiguity():
    text = "It must be mitigated pursuant to section 49 (1)."
    _candidates, analyses = _build(text, "en")
    admitted, _assessments = AD.admissible_analyses(
        analyses, text, unit_id="d42j-material-ambiguity"
    )
    state, chosen, _reason = V2.resolve_internal(admitted)
    assert chosen is not None
    assert any(
        relation.relation_type == RELATION
        for relation in chosen.role_relations
    )
    assert state == "AMBIGUOUS_BINDING"


def test_local_relation_does_not_manufacture_an_external_antecedent():
    analysis = _relation_analyses("Sie soll begründet werden.", "de")[0]
    assert analysis.antecedent is None
    assert analysis.shared_subject_source == "NONE"


def test_candidate_inventory_is_deterministic_and_relation_free():
    text = "Sie soll begründet werden."
    first_candidates, _first_analyses = _build(text, "de")
    second_candidates, _second_analyses = _build(text, "de")
    assert first_candidates == second_candidates
    assert all(not hasattr(candidate, "role_relations")
               for candidate in first_candidates)


def test_relation_metadata_does_not_change_admissibility_decisions():
    text = "Sie soll begründet werden."
    _candidates, analyses = _build(text, "de")
    _admitted, with_relations = AD.admissible_analyses(
        analyses, text, unit_id="d42j-admissibility-with"
    )
    stripped = [
        replace(analysis, role_relations=()) for analysis in analyses
    ]
    _stripped_admitted, without_relations = AD.admissible_analyses(
        stripped, text, unit_id="d42j-admissibility-without"
    )

    def projection(assessment):
        return (
            assessment.verdict,
            assessment.competes,
            assessment.primary_violation,
            assessment.secondary_violations,
            assessment.clause_pairing_verdict,
            assessment.cross_clause_licence_type,
        )

    assert [projection(value) for value in with_relations] == [
        projection(value) for value in without_relations
    ]
