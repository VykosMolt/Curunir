"""D42H — bounded subject constituents and neighbouring negatives."""

from __future__ import annotations

import pytest

from curunir_operational.v5_8_1 import roles_v2 as V2

pytestmark = pytest.mark.no_db


def _subjects(text: str, language: str):
    tokens = V2._tokens(text)
    if language == "ru":
        candidates = V2._cyrillic_candidates(text, tokens)
    else:
        candidates = V2._arabic_candidates(text, tokens)
    return [candidate for candidate in candidates
            if candidate.role_type == "SUBJECT"]


def _texts(text: str, language: str):
    return {candidate.text for candidate in _subjects(text, language)}


def test_russian_postpassive_coordinate_keeps_every_conjunct():
    text = ("Под структурными факторами понимаются механизмы управления "
            "и рамки политики, а также иные условия")
    assert "механизмы управления и рамки политики" in _texts(text, "ru")


def test_russian_coordinated_predicates_are_skipped_before_the_patient():
    text = ("используются или создаются материалы, технологии или информация, "
            "которые доступны исследователю")
    assert "материалы, технологии или информация" in _texts(text, "ru")


def test_russian_uncoordinated_postverbal_object_is_not_widened_as_a_patient():
    text = "Комитет использует материалы проекта ежегодно."
    assert "материалы проекта ежегодно" not in _texts(text, "ru")


def test_russian_coordinated_prepositional_complements_are_not_a_patient():
    text = ("Они заключаются между Советом и Членами или между Советом "
            "и группами Членов.")
    assert not any(candidate.origin_rule == "RU_POSTVERBAL_COORDINATED_PATIENT"
                   for candidate in _subjects(text, "ru"))


def test_russian_negative_quantifier_is_one_preverbal_constituent():
    text = "При этом ни одна страна не должна иметь коэффициент выше нормы."
    assert "ни одна страна" in _texts(text, "ru")


def test_russian_negation_without_the_quantifier_does_not_trigger_the_rule():
    text = "Страна не должна иметь коэффициент выше нормы."
    assert not any(candidate.origin_rule == "RU_NEGATIVE_QUANTIFIER_NP"
                   for candidate in _subjects(text, "ru"))


def test_arabic_existential_delayed_subject_includes_its_construct_chain():
    text = "وهناك 4 أنماط من فيروس الأنفلونزا، وهي A وB وC وD."
    assert "4 أنماط من فيروس الأنفلونزا" in _texts(text, "ar")


def test_arabic_passive_patient_ends_before_the_adverbial():
    text = ("تُصنّف فيروسات الأنفلونزا من النمط A أيضاً إلى أنماط "
            "فرعية أخرى.")
    assert "فيروسات الأنفلونزا من النمط A" in _texts(text, "ar")


def test_arabic_subordinate_verb_can_have_an_indefinite_postverbal_subject():
    text = "يمكن أن تظهر عدوى لفيروسات أخرى تصيب الجهاز التنفسي (مثل الفيروس)"
    assert ("عدوى لفيروسات أخرى تصيب الجهاز التنفسي"
            in _texts(text, "ar"))


def test_arabic_active_prepositional_object_is_still_not_a_subject():
    text = "يبحث عن الحلول المتاحة في هذا المجال بصورة منتظمة"
    assert not any("الحلول" in value for value in _texts(text, "ar"))


def test_arabic_subordinated_active_prepositional_object_is_not_a_subject():
    text = "وله أن يطلب إلى أعضاء الأمم المتحدة تطبيق هذه التدابير"
    assert not any(candidate.origin_rule == "AR_SUBORDINATE_POSTVERBAL_NP"
                   for candidate in _subjects(text, "ar"))


def test_arabic_bare_active_clause_does_not_license_the_subordinate_rule():
    text = "تظهر عدوى جديدة في الشتاء"
    assert not any(candidate.origin_rule == "AR_SUBORDINATE_POSTVERBAL_NP"
                   for candidate in _subjects(text, "ar"))


@pytest.mark.parametrize(
    "language,text,expected",
    [
        ("ru", "используются материалы или технологии, которые проверены",
         "материалы или технологии"),
        ("ar", "وهناك 3 أنواع من اللقاح، وهي معروفة",
         "3 أنواع من اللقاح"),
    ],
)
def test_new_candidate_offsets_address_the_original_text(language, text, expected):
    candidate = next(value for value in _subjects(text, language)
                     if value.text == expected)
    assert candidate.span is not None
    assert text[candidate.span[0]:candidate.span[1]] == expected
