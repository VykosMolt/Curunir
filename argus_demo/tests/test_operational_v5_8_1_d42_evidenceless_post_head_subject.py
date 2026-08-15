import pytest

from curunir_operational.v5_8_1 import roles_v2 as V2

pytestmark = pytest.mark.no_db


def _analyses(text: str, language: str):
    return V2.build_lattice(text=text, language=language)[1]


def _binding(analyses, subject_text: str, head_text: str):
    for analysis in analyses:
        subject = analysis.subject
        head = analysis.predicate_head
        if subject is None or head is None:
            continue
        if subject.text == subject_text and head.text == head_text:
            return analysis
    return None


def test_post_head_object_binding_is_an_unsupported_slot():
    analyses = _analyses(
        "Комитет назначает директора Регионального бюро.", "ru")
    binding = _binding(analyses, "директора", "назначает")
    assert binding is not None
    assert V2._subject_is_evidenceless_post_head_argument(binding)


def test_preverbal_unmarked_subject_is_untouched():
    """Emptiness alone must not fire: this subject carries no marker either."""
    analyses = _analyses(
        "Комитет назначает директора Регионального бюро.", "ru")
    binding = _binding(analyses, "Комитет", "назначает")
    assert binding is not None
    assert binding.subject.syntactic == ()
    assert not V2._subject_is_evidenceless_post_head_argument(binding)


def test_evidenced_subject_is_untouched_in_every_language():
    for text, language, subject_text, head_text in (
        ("Die Rücklagen müssen ausgewiesen sein.", "de",
         "Die Rücklagen", "müssen"),
        ("The appellant is required to make a statement.", "en",
         "The appellant", "is"),
        ("Las personas con mayor riesgo son las embarazadas.", "es",
         "Las personas con mayor riesgo", "son"),
    ):
        analyses = _analyses(text, language)
        binding = _binding(analyses, subject_text, head_text)
        assert binding is not None, text
        assert binding.subject.syntactic
        assert not V2._subject_is_evidenceless_post_head_argument(binding)


def test_infinitival_directive_member_selects_the_subjectless_reading():
    text = "(6) оказывать поддержку государствам-членам в осуществлении мер по"
    analyses = _analyses(text, "ru")
    binding = _binding(analyses, "государствам-членам", "оказывать")
    assert binding is not None
    assert V2._subject_is_evidenceless_post_head_argument(binding)
    _state, chosen, _reason = V2.resolve_internal(analyses)
    assert chosen is not None
    assert chosen.predicate_head is not None
    assert chosen.predicate_head.text == "оказывать"
    assert chosen.subject is None


def test_operative_verb_item_selects_the_subjectless_reading():
    text = "НАЗНАЧАЕТ д-ра Ханан Хассан Балхи директором Регионального бюро"
    analyses = _analyses(text, "ru")
    _state, chosen, _reason = V2.resolve_internal(analyses)
    assert chosen is not None
    assert chosen.predicate_head is not None
    assert chosen.predicate_head.text == "НАЗНАЧАЕТ"
    assert chosen.subject is None


def test_construct_needs_both_conjuncts():
    """Neither emptiness nor position alone is the claim."""
    analyses = _analyses(
        "Комитет назначает директора Регионального бюро.", "ru")
    empty_but_preceding = _binding(analyses, "Комитет", "назначает")
    empty_and_following = _binding(analyses, "директора", "назначает")
    assert empty_but_preceding.subject.syntactic == ()
    assert empty_and_following.subject.syntactic == ()
    assert (empty_but_preceding.subject.span[0]
            < empty_but_preceding.predicate_head.span[0])
    assert (empty_and_following.subject.span[0]
            >= empty_and_following.predicate_head.span[1])
    assert not V2._subject_is_evidenceless_post_head_argument(
        empty_but_preceding)
    assert V2._subject_is_evidenceless_post_head_argument(empty_and_following)


def test_headless_or_subjectless_analyses_never_fire():
    analyses = _analyses("Dies gilt nicht für § 3a.", "de")
    for analysis in analyses:
        if analysis.subject is None or analysis.predicate_head is None:
            assert not V2._subject_is_evidenceless_post_head_argument(analysis)


def test_m5_changes_no_frozen_constant():
    assert V2.SEPARATION_MARGIN == 0.12
    assert V2.REQUIRED_SLOT_MISSING_PENALTY == 0.35
    assert V2.UNSUPPORTED_SLOT_PENALTY == 0.15
    assert V2.SUPPORTED_OPTIONAL_SLOT_CREDIT == 0.03
    assert V2.MAX_OPTIONAL_CREDIT == 0.06
