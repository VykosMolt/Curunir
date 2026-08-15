import pytest

from curunir_operational.v5_8_1 import roles_v2 as V2

pytestmark = pytest.mark.no_db


def _build(text: str, language: str):
    return V2.build_lattice(text=text, language=language)[1]


def _carrying(analyses, relation_type: str):
    return [
        analysis
        for analysis in analyses
        if any(
            relation.relation_type == relation_type
            for relation in analysis.role_relations
        )
    ]


def test_m1_unique_closed_finite_anchor_is_typed_relation():
    analyses = _build(
        "Las personas con mayor riesgo son las embarazadas.", "es")
    related = _carrying(
        analyses, "LOCAL_SUBJECT_UNIQUE_CLOSED_FINITE_ANCHOR")
    assert related
    assert all(analysis.predicate_head.text == "son" for analysis in related)
    state, chosen, _ = V2.resolve_internal(analyses)
    assert state in {"UNIQUE_BINDING_ESTABLISHED", "AMBIGUOUS_BINDING"}
    assert chosen is not None
    assert chosen.predicate_head.text == "son"
    assert chosen.subject is not None


def test_m1_does_not_change_frozen_score_or_margin_constants():
    assert V2.SEPARATION_MARGIN == 0.12
    assert V2.REQUIRED_SLOT_MISSING_PENALTY == 0.35
    assert V2.UNSUPPORTED_SLOT_PENALTY == 0.15
    assert V2.SUPPORTED_OPTIONAL_SLOT_CREDIT == 0.03
    assert V2.MAX_OPTIONAL_CREDIT == 0.06


def test_m2_german_adjacent_lexical_finite_is_typed_relation():
    analyses = _build(
        "Die oder der Bundesbeauftragte leistet vor der "
        "Bundespräsidentin folgenden Eid.", "de")
    related = _carrying(
        analyses, "LOCAL_SUBJECT_GERMAN_LEXICAL_FINITE")
    assert related
    assert all(analysis.predicate_head.text == "leistet"
               for analysis in related)
    state, chosen, _ = V2.resolve_internal(analyses)
    assert state in {"UNIQUE_BINDING_ESTABLISHED", "AMBIGUOUS_BINDING"}
    assert chosen is not None
    assert chosen.predicate_head.text == "leistet"


def test_m2_rejects_participle_before_verb_final_auxiliary():
    analyses = _build(
        "Die Rücklagen, die umgewandelt werden sollen, müssen "
        "ausgewiesen sein.", "de")
    assert not _carrying(
        analyses, "LOCAL_SUBJECT_GERMAN_LEXICAL_FINITE")


def test_m3_required_to_frame_carries_deontic_relation():
    analyses = _build(
        "(1) The appellant is required to make a statement.", "en")
    related = _carrying(
        analyses, "LOCAL_SUBJECT_REQUIRED_TO_DEONTIC_FRAME")
    assert related
    assert all(analysis.predicate_head.text == "is" for analysis in related)
    _state, chosen, _ = V2.resolve_internal(analyses)
    assert chosen is not None
    assert chosen.predicate_head.text == "is"
    assert chosen.subject is not None


def test_m3_does_not_fire_for_other_passive_modal_frame():
    analyses = _build("It must be mitigated immediately.", "en")
    assert not _carrying(
        analyses, "LOCAL_SUBJECT_REQUIRED_TO_DEONTIC_FRAME")
