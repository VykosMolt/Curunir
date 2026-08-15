"""V5.8.1 §4–§13, §18 — multilingual role binding.

The dangerous failure of a role binder is not silence, it is confidence: a
nearest-entity guess written into a subject slot reads downstream exactly like a
fact the source stated.  Most of what follows tests that the binder declines.
"""
from __future__ import annotations

import time

import pytest

from curunir_operational.v5_8_1 import roles as RB

pytestmark = pytest.mark.no_db


def _bind(text, language, **kwargs):
    return RB.bind(candidate_id="c", text=text, language=language, **kwargs)


# ===========================================================================
# §8 — Arabic
# ===========================================================================
def test_arabic_verbal_sentence_binds_a_postverbal_subject():
    text = "تتولى الأمانة العامة تنسيق الأعمال بين الدول الأعضاء في المنظمة."
    binding = _bind(text, "ar")
    assert binding.predicate_state == "EXPLICIT_FINITE_PREDICATE"
    assert binding.subject_state == "POSTVERBAL_SUBJECT"
    assert binding.text_of(text, binding.subject_span).startswith("ال")
    assert binding.role_binding_state == "ROLE_BINDING_ESTABLISHED"


def test_arabic_deontic_operator_keeps_its_complement():
    text = "يجب على الدول الأطراف أن تتخذ التدابير اللازمة لحماية هذه الحقوق."
    binding = _bind(text, "ar")
    assert binding.predicate_state == "DEONTIC_OPERATOR_WITH_COMPLEMENT"
    assert binding.predicate_head == "يجب"
    assert binding.predicate_complement, "the complement carries the action"


def test_arabic_zero_copula_nominal_clause_binds_a_subject():
    text = "مقاومة مضادات الميكروبات تهديد عالمي للصحة والتنمية في جميع البلدان."
    binding = _bind(text, "ar")
    assert binding.subject_state in ("ZERO_COPULA_NOMINAL_SUBJECT",
                                     "IMPLICIT_CONTEXT_BOUND_SUBJECT")
    assert binding.role_binding_state != "ROLE_BINDING_INVALID"


def test_the_arabic_definite_article_is_never_a_predicate_head():
    binding = _bind("المحتويات والفهرس", "ar")
    assert not binding.predicate_head.startswith("ال")


def test_arabic_first_noun_phrase_is_not_assumed_to_be_the_subject():
    """Verb-initial order means the leading token is the verb, not the subject."""
    text = "تتولى الأمانة العامة تنسيق الأعمال بين الدول الأعضاء في المنظمة."
    binding = _bind(text, "ar")
    assert binding.subject_span is not None
    assert binding.subject_span[0] > binding.predicate_span[0]


# ===========================================================================
# §9 — Russian
# ===========================================================================
def test_russian_modal_binds_subject_and_complement():
    text = "Оператор обязан обеспечить конфиденциальность персональных данных."
    binding = _bind(text, "ru")
    assert binding.predicate_state == "DEONTIC_OPERATOR_WITH_COMPLEMENT"
    assert binding.text_of(text, binding.subject_span) == "Оператор"


def test_russian_reflexive_passive_has_no_semantic_actor_without_evidence():
    text = "Запрещается разглашение сведений, составляющих государственную тайну."
    binding = _bind(text, "ru")
    assert binding.predicate_state == "PASSIVE_OR_IMPERSONAL_PREDICATE"
    assert binding.semantic_actor_state == "SEMANTIC_ACTOR_UNRESOLVED"
    assert binding.semantic_actor_span is None


def test_russian_short_form_participle_is_the_predicate():
    text = "Договор утверждён постановлением Правительства Российской Федерации."
    binding = _bind(text, "ru")
    assert binding.predicate_state == "PARTICIPIAL_PREDICATE"
    assert binding.text_of(text, binding.subject_span) == "Договор"


def test_the_russian_copula_is_not_a_reflexive_passive():
    """`являются` ends in -ся; reading it as reflexive turned every copular
    clause into a passive with a patient subject."""
    text = "Сердечно-сосудистые заболевания являются основной причиной смерти."
    binding = _bind(text, "ru")
    assert binding.predicate_state == "EXPLICIT_FINITE_PREDICATE"
    assert binding.subject_state == "EXPLICIT_SUBJECT"


def test_russian_does_not_assume_subject_before_predicate():
    binding = _bind("Запрещается разглашение сведений о персональных данных.", "ru")
    assert binding.subject_state != "EXPLICIT_SUBJECT"


# ===========================================================================
# §5.1 — five roles, five answers
# ===========================================================================
def test_a_passive_patient_is_never_promoted_to_actor():
    text = "The report was published by the European Commission in March."
    binding = _bind(text, "en")
    assert binding.subject_state == "PASSIVE_PATIENT_SUBJECT"
    assert binding.semantic_actor_span != binding.subject_span
    assert "Commission" in binding.text_of(text, binding.semantic_actor_span)


def test_a_passive_with_no_agent_leaves_the_actor_unresolved():
    binding = _bind("The regulation was adopted in March.", "en")
    assert binding.semantic_actor_state == "SEMANTIC_ACTOR_UNRESOLVED"
    assert binding.semantic_actor_span is None


def test_an_institutional_subject_is_marked_as_such():
    binding = _bind("The European Commission publishes the report each year.", "en")
    assert binding.semantic_actor_state in ("SEMANTIC_ACTOR_INSTITUTIONAL",
                                            "SEMANTIC_ACTOR_IS_GRAMMATICAL_SUBJECT")


def test_attribution_is_separate_from_the_grammatical_subject():
    text = "According to the World Health Organization, cases fell in 2024."
    binding = _bind(text, "en")
    assert binding.attribution_state == "ATTRIBUTION_EXPLICIT"
    assert binding.attribution_span is not None


def test_a_quoted_passage_with_no_named_speaker_stays_unresolved():
    binding = _bind('The text reads "the Party shall notify the depositary".', "en")
    assert binding.attribution_state in ("QUOTED_SPEAKER_UNRESOLVED",
                                         "ATTRIBUTION_ABSENT")


# ===========================================================================
# §12 — bounded anaphora
# ===========================================================================
def test_an_ambiguous_antecedent_stays_ambiguous():
    result = RB.resolve_antecedent(anaphor="It", left_context=(
        "The European Commission published a report. "
        "The European Parliament published a report."))
    assert result.state == "ANTECEDENT_AMBIGUOUS"
    assert result.span is None


def test_no_antecedent_is_not_the_nearest_entity():
    result = RB.resolve_antecedent(anaphor="It", left_context="")
    assert result.state == "ANTECEDENT_ABSENT"
    assert result.span is None


def test_a_single_dominant_antecedent_resolves():
    result = RB.resolve_antecedent(anaphor="It", left_context=(
        "The World Health Organization issued guidance in March."))
    assert result.state in ("ANTECEDENT_ESTABLISHED", "ANTECEDENT_RECOVERABLE")
    assert result.span is not None


def test_an_unresolvable_anaphor_leaves_the_subject_unresolved():
    binding = _bind("It spreads when people with the disease cough.", "en",
                    left_context="")
    assert binding.subject_state == "SUBJECT_UNRESOLVED"
    assert binding.repair_requirement is None


def test_the_antecedent_window_is_bounded():
    assert RB.ANTECEDENT_CONTEXT_CHARS <= 2000


# ===========================================================================
# §11 — inherited roles carry lineage
# ===========================================================================
def test_a_shared_predicate_records_where_it_came_from():
    binding = _bind("or limiting its spread among the population", "en",
                    shared_predicate_source="lead-in-1")
    assert binding.predicate_state == "SHARED_COORDINATE_PREDICATE"
    assert binding.predicate_source == "SHARED_FROM:lead-in-1"
    assert "lead-in-1" in binding.required_context_ids


def test_a_governing_clause_is_recorded_not_copied():
    binding = _bind("bearing in mind the need for cooperation", "en",
                    governing_clause_id="enactment-3")
    assert binding.governing_clause_id == "enactment-3"
    assert "enactment-3" in binding.required_context_ids
    assert binding.role_binding_state in ("ROLE_BINDING_RECOVERABLE",
                                          "ROLE_BINDING_PARTIAL",
                                          "ROLE_BINDING_UNRESOLVED")


# ===========================================================================
# §18 — illegal states are unrepresentable
# ===========================================================================
def _record(**overrides):
    base = dict(
        binding_id="b", candidate_id="c", clause_state="FINITE_CLAUSE_ESTABLISHED",
        subject_state="EXPLICIT_SUBJECT", subject_span=(0, 3),
        subject_source="RECORDED_SPAN", subject_confidence=0.9,
        predicate_state="EXPLICIT_FINITE_PREDICATE", predicate_span=(4, 6),
        predicate_head="is", predicate_complement="", predicate_source="RECORDED_SPAN",
        predicate_confidence=0.9, governing_clause_id=None,
        shared_predicate_source=None, shared_subject_source=None,
        semantic_actor_state="SEMANTIC_ACTOR_IS_GRAMMATICAL_SUBJECT",
        semantic_actor_span=(0, 3), attribution_state="ATTRIBUTION_ABSENT",
        attribution_span=None, role_binding_state="ROLE_BINDING_ESTABLISHED",
        repair_requirement=None, required_context_ids=(), binding_confidence=0.9,
        binding_reason="", language_or_script="LATIN",
        recorded_time="2026-07-26T00:00:00+00:00")
    base.update(overrides)
    return RB.ClauseRoleBinding(**base)


def test_established_with_an_unresolved_subject_is_refused():
    with pytest.raises(RB.RoleBindingViolation):
        _record(subject_state="SUBJECT_UNRESOLVED", subject_span=None)


def test_established_with_an_unbound_predicate_is_refused():
    with pytest.raises(RB.RoleBindingViolation):
        _record(predicate_state="PREDICATE_UNRESOLVED", predicate_span=None)


def test_no_semantic_subject_with_a_subject_span_is_refused():
    with pytest.raises(RB.RoleBindingViolation):
        _record(subject_state="NO_SEMANTIC_SUBJECT",
                role_binding_state="ROLE_BINDING_PARTIAL")


def test_a_recoverable_subject_without_a_repair_requirement_is_refused():
    with pytest.raises(RB.RoleBindingViolation):
        _record(subject_state="ANAPHORIC_SUBJECT_RECOVERABLE",
                role_binding_state="ROLE_BINDING_RECOVERABLE",
                repair_requirement=None)


def test_reusing_the_patient_as_the_actor_is_refused():
    with pytest.raises(RB.RoleBindingViolation):
        _record(subject_state="PASSIVE_PATIENT_SUBJECT",
                role_binding_state="ROLE_BINDING_PARTIAL",
                semantic_actor_span=(0, 3))


# ===========================================================================
# §7 — state semantics
# ===========================================================================
def test_invalid_means_contradicted_not_merely_unrecognised():
    """Two independent layers: one having evidence the other lacks must not be
    vetoed into INVALID."""
    binding = _bind("It spreads when people with the disease cough.", "en")
    assert binding.predicate_state == "PREDICATE_RECOVERABLE"
    assert binding.role_binding_state != "ROLE_BINDING_INVALID"


def test_a_bare_nominal_is_invalid():
    binding = _bind("Table of contents", "en")
    assert binding.role_binding_state == "ROLE_BINDING_INVALID"


def test_every_state_is_in_its_declared_vocabulary():
    for language, text in (("ar", "يجب على الدول أن تتخذ التدابير اللازمة."),
                           ("ru", "Оператор обязан обеспечить конфиденциальность."),
                           ("en", "The Party shall notify the depositary."),
                           ("en", "Table of contents")):
        binding = _bind(text, language)
        assert binding.subject_state in RB.SUBJECT_STATES
        assert binding.predicate_state in RB.PREDICATE_STATES
        assert binding.role_binding_state in RB.ROLE_BINDING_STATES


# ===========================================================================
# §13 — boundedness
# ===========================================================================
def test_binding_is_bounded_in_time():
    for language, unit in (
            ("ru", "Настоящий Федеральный закон регулирует отношения участников "),
            ("ar", "يجب على الدول الأطراف أن تتخذ التدابير اللازمة لحماية "),
            ("en", "The Contracting Party shall notify the depositary promptly ")):
        long_text = unit * 300
        started = time.time()
        for _ in range(20):
            RB.bind(candidate_id="c", text=long_text, language=language)
        assert time.time() - started < 2.0, language


def test_spans_are_source_offsets_never_invented_text():
    text = "Оператор обязан обеспечить конфиденциальность персональных данных."
    binding = _bind(text, "ru")
    for span in (binding.subject_span, binding.predicate_span):
        if span:
            assert 0 <= span[0] < span[1] <= len(text)
            assert text[span[0]:span[1]] in text


def test_the_audit_reports_its_own_failure_modes():
    bindings = [_bind(t, l) for l, t in (
        ("ar", "يجب على الدول الأطراف أن تتخذ التدابير اللازمة."),
        ("ru", "Договор утверждён постановлением Правительства."),
        ("en", "The report was published by the European Commission."))]
    report = RB.audit(bindings)
    assert report["bindings"] == 3
    assert report["established_without_a_bound_predicate"] == 0
    assert report["passive_patient_reused_as_actor"] == 0
    assert report["recoverable_without_repair_requirement"] == 0
