"""V5.8.1 §3, §4.3 — multilingual finite-clause recognition.

Positive fixtures come in pairs with a neighbouring negative from the same
language and register, because the failure mode of this repair is not missing a
clause: it is admitting a noun phrase because the language is unfamiliar.
"""
from __future__ import annotations

import time

import pytest

from curunir_operational.v5_8_1 import clauses as CL

pytestmark = pytest.mark.no_db

ESTABLISHED = "FINITE_CLAUSE_ESTABLISHED"
RECOVERABLE = "FINITE_CLAUSE_RECOVERABLE"
UNRESOLVED = "FINITE_CLAUSE_UNRESOLVED"
NOT_A_CLAUSE = "NOT_A_FINITE_CLAUSE"


# ===========================================================================
# Arabic
# ===========================================================================
ARABIC_PROPOSITIONS = [
    "يجب على الدول الأطراف أن تتخذ التدابير اللازمة لحماية هذه الحقوق.",
    "لا يجوز حرمان أي شخص من حريته إلا وفقاً للقانون.",
    "يُعتبر هذا الاتفاق نافذاً من تاريخ التصديق عليه من جميع الأطراف.",
    "تتولى الأمانة العامة تنسيق الأعمال بين الدول الأعضاء في المنظمة.",
    "يُعمل به من تاريخ نشره في الجريدة الرسمية للدولة الطرف.",
    "يُحظر نشر أي معلومات سرية دون إذن كتابي من الجهة المختصة.",
    "يلتزم كل طرف بتقديم تقرير سنوي إلى الأمانة العامة للمنظمة.",
    "أعلنت المنظمة أن مقاومة مضادات الميكروبات من التهديدات العالمية الرئيسية.",
    "مقاومة مضادات الميكروبات تهديد عالمي للصحة والتنمية في جميع البلدان.",
    "تسري أحكام هذه الاتفاقية على جميع الأشخاص الخاضعين لولاية الدولة.",
]
ARABIC_NON_PROPOSITIONS = [
    "الفهرس",
    "منظمة الصحة العالمية",
    "المحتويات",
    "الصفحة الرئيسية",
]


@pytest.mark.parametrize("text", ARABIC_PROPOSITIONS)
def test_arabic_legal_propositions_are_recognised(text):
    evidence = CL.detect(text, language="ar")
    assert evidence.state in (ESTABLISHED, RECOVERABLE), evidence.reason
    assert evidence.language_or_script == "ARABIC"


@pytest.mark.parametrize("text", ARABIC_NON_PROPOSITIONS)
def test_arabic_headings_are_not_clauses(text):
    assert CL.detect(text, language="ar").state in (NOT_A_CLAUSE, UNRESOLVED)


def test_arabic_recognition_meets_the_declared_rate():
    hits = sum(1 for text in ARABIC_PROPOSITIONS
               if CL.detect(text, language="ar").state == ESTABLISHED)
    assert hits / len(ARABIC_PROPOSITIONS) >= 0.95


def test_arabic_zero_copula_nominal_predication_is_predication():
    """Arabic present-tense predication has no copula at all."""
    evidence = CL.detect("مقاومة مضادات الميكروبات تهديد عالمي للصحة والتنمية.",
                         language="ar")
    assert evidence.state == ESTABLISHED


# ===========================================================================
# Russian
# ===========================================================================
RUSSIAN_PROPOSITIONS = [
    "Настоящий Федеральный закон должен применяться ко всем участникам отношений.",
    "Запрещается разглашение сведений, составляющих государственную тайну.",
    "Порядок подлежит утверждению уполномоченным федеральным органом власти.",
    "Договор утверждён постановлением Правительства Российской Федерации.",
    "Оператор обязан обеспечить конфиденциальность персональных данных граждан.",
    "Сердечно-сосудистые заболевания являются основной причиной смерти в мире.",
    "Настоящий закон обязателен для исполнения на всей территории страны.",
    "Нельзя ограничивать права граждан иначе как на основании федерального закона.",
    "Решение принято единогласно всеми членами постоянного комитета организации.",
    "Регламент устанавливается уполномоченным органом исполнительной власти.",
]
RUSSIAN_NON_PROPOSITIONS = [
    "Содержание",
    "Российская Федерация",
    "Оглавление",
    "Главная страница",
]


@pytest.mark.parametrize("text", RUSSIAN_PROPOSITIONS)
def test_russian_legal_propositions_are_recognised(text):
    evidence = CL.detect(text, language="ru")
    assert evidence.state in (ESTABLISHED, RECOVERABLE), evidence.reason
    assert evidence.language_or_script == "CYRILLIC"


@pytest.mark.parametrize("text", RUSSIAN_NON_PROPOSITIONS)
def test_russian_headings_are_not_clauses(text):
    assert CL.detect(text, language="ru").state in (NOT_A_CLAUSE, UNRESOLVED)


def test_russian_recognition_meets_the_declared_rate():
    hits = sum(1 for text in RUSSIAN_PROPOSITIONS
               if CL.detect(text, language="ru").state == ESTABLISHED)
    assert hits / len(RUSSIAN_PROPOSITIONS) >= 0.95


def test_russian_short_form_participle_is_the_predicate():
    evidence = CL.detect("Договор утверждён постановлением Правительства.",
                         language="ru")
    assert evidence.short_form_participial_predication
    assert evidence.state == ESTABLISHED


def test_russian_zero_copula_is_predication():
    evidence = CL.detect("Настоящий закон обязателен для исполнения на территории.",
                         language="ru")
    assert evidence.state == ESTABLISHED


@pytest.mark.parametrize("head", [
    "признавая", "отмечая", "учитывая", "ссылаясь",
])
def test_russian_recital_converb_keeps_finiteness_and_semantics_separate(head):
    text = f"{head}, что государства продолжают сотрудничество,"
    evidence = CL.detect(text, language="ru")
    assert evidence.state == RECOVERABLE
    assert evidence.dependent_nonfinite_predication
    assert evidence.semantic_predication_status == \
        "SEMANTIC_PROPOSITION_ESTABLISHED"
    assert evidence.predicate_realization_mode == \
        "DEPENDENT_NONFINITE_PREDICATION/CONVERB"
    assert evidence.local_predicate_span == (0, len(head))
    assert evidence.local_predicate_text == head


@pytest.mark.parametrize("text", [
    "включая оборудование и материалы;",
    "несмотря на достигнутые результаты;",
    "помимо использования защитных средств;",
])
def test_grammaticalized_converb_shaped_controls_do_not_become_predicates(text):
    evidence = CL.detect(text, language="ru")
    assert not evidence.dependent_nonfinite_predication
    assert evidence.predicate_realization_mode != \
        "DEPENDENT_NONFINITE_PREDICATION/CONVERB"
    assert evidence.local_predicate_span is None
    assert not evidence.local_predicate_text


def test_russian_finite_clause_is_not_relabelled_as_dependent_nonfinite():
    evidence = CL.detect(
        "Они заключаются между сторонами и подлежат ратификации государствами.",
        language="ru")
    assert evidence.state == ESTABLISHED
    assert evidence.semantic_predication_status == \
        "SEMANTIC_PROPOSITION_ESTABLISHED"
    assert evidence.predicate_realization_mode == \
        "EXISTING_FINITE_OR_OTHER_PREDICATION"
    assert not evidence.dependent_nonfinite_predication


# ===========================================================================
# Legal recitals and non-finite predication
# ===========================================================================
RECITALS = [
    ("es", "Reafirmando su proposito de consolidar en este Continente un regimen "
           "de libertad personal y de justicia social;"),
    ("fr", "Considerant que la protection des droits de l'homme exige un regime "
           "de droit democratique;"),
    ("en", "Whereas the Contracting Parties have agreed to establish a common "
           "framework for cooperation;"),
    ("de", "In Erwaegung der Notwendigkeit eines gemeinsamen Rahmens fuer die "
           "Zusammenarbeit der Vertragsstaaten;"),
    ("en", "Having regard to the Treaty on the Functioning of the Union and to "
           "the opinion of the Committee;"),
]


@pytest.mark.parametrize("language,text", RECITALS)
def test_legal_recitals_are_established_or_recoverable(language, text):
    evidence = CL.detect(text, language=language)
    assert evidence.state in (ESTABLISHED, RECOVERABLE), evidence.reason


def test_recital_recognition_meets_the_declared_rate():
    hits = sum(1 for language, text in RECITALS
               if CL.detect(text, language=language).state
               in (ESTABLISHED, RECOVERABLE))
    assert hits / len(RECITALS) >= 0.95


def test_a_recital_is_not_silently_accepted_as_a_standalone_proposition():
    """RECOVERABLE means the governing predicate is elsewhere.  Collapsing it
    into ESTABLISHED would admit a dependent clause as a whole assertion."""
    evidence = CL.detect(
        "Whereas the Contracting Parties have agreed to establish a framework;",
        language="en")
    assert evidence.bounded_governing_clause


# ===========================================================================
# Neighbouring negatives — §4.3
# ===========================================================================
LATIN_NEGATIVES = [
    ("en", "Table of contents"),
    ("en", "World Health Organization Global Tuberculosis Report"),
    ("en", "Next page"),
    ("en", "A detailed description of the measures put in place."),
    ("es", "Tratados Multilaterales Departamento de Derecho Internacional"),
    ("fr", "Base de donnees mondiale sur les marques"),
    ("de", "Bundesministerium der Justiz und fuer Verbraucherschutz"),
]


@pytest.mark.parametrize("language,text", LATIN_NEGATIVES)
def test_latin_non_propositions_are_refused(language, text):
    assert CL.detect(text, language=language).state == NOT_A_CLAUSE


def test_a_covered_language_treats_silence_as_evidence():
    """For en/de/fr/es the role splitter is competent, so no predication found
    means no predicate.  For a language it does not model, silence is only
    silence — and the two must not be reported as the same fact."""
    noun_phrase = "A detailed description of the measures put in place."
    assert CL.detect(noun_phrase, language="en").state == NOT_A_CLAUSE
    unmodelled = CL.detect("Ekki er heimilt ad birta upplysingar an leyfis "
                           "fra thar til baerum yfirvoldum landsins",
                           language="is").state
    assert unmodelled in (UNRESOLVED, NOT_A_CLAUSE)


# ===========================================================================
# Boundedness — §3.6
# ===========================================================================
def test_detection_is_bounded_in_time():
    """The D24 title-case regex ran for an hour on real input.  Nothing here
    nests a quantifier inside a quantifier, and this asserts it."""
    for language, unit in (("ru", "Настоящий Федеральный закон регулирует отношения "),
                           ("ar", "يجب على الدول الأطراف أن تتخذ التدابير اللازمة "),
                           ("en", "the quick brown fox jumps over the lazy dog and ")):
        long_text = unit * 400
        started = time.time()
        for _ in range(20):
            CL.detect(long_text, language=language)
        assert time.time() - started < 2.0, language


def test_the_scan_window_is_capped():
    assert CL.MAX_SCAN_CHARS <= 8000
    assert CL.GOVERNING_CONTEXT_CHARS <= 2000


def test_no_state_is_established_without_a_named_dimension():
    report = CL.audit([CL.detect(text, language="ar")
                       for text in ARABIC_PROPOSITIONS]
                      + [CL.detect(text, language="ru")
                         for text in RUSSIAN_PROPOSITIONS])
    assert report["established_without_any_dimension"] == 0
    assert report["spans"] == len(ARABIC_PROPOSITIONS) + len(RUSSIAN_PROPOSITIONS)


def test_the_detector_never_removes_a_clause_the_lexicon_found():
    evidence = CL.detect("Something the splitter parsed.", language="en",
                         base_finite_clause=True)
    assert evidence.state == ESTABLISHED


def test_script_detection_does_not_need_a_language_tag():
    assert CL.script_of("يجب على الدول الأطراف") == "ARABIC"
    assert CL.script_of("Настоящий закон") == "CYRILLIC"
    assert CL.script_of("The present Act") == "LATIN"
    assert CL.script_of("") == "UNKNOWN"


def test_every_state_is_in_the_declared_vocabulary():
    for language, text in (("ar", ARABIC_PROPOSITIONS[0]),
                           ("ru", RUSSIAN_PROPOSITIONS[0]),
                           ("en", "Table of contents"),
                           ("en", RECITALS[2][1])):
        assert CL.detect(text, language=language).state in CL.CLAUSE_STATES
