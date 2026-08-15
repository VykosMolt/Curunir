"""D42I — narrow predicate constructions and complete negative controls."""

from __future__ import annotations

import pytest

from curunir_operational.v5_8_1 import roles_v2 as V2

pytestmark = pytest.mark.no_db


def _latin(text: str, left_context: str = ""):
    return V2._latin_candidates(
        text, V2._tokens(text), left_context=left_context)


def _arabic(text: str, governed: bool):
    return V2._arabic_candidates(
        text, V2._tokens(text), governed_list_item=governed)


def _origin(candidates, origin):
    return [candidate for candidate in candidates
            if candidate.origin_rule == origin]


def test_balanced_quoted_definition_emits_exact_subject_and_head():
    text = ("3 L’expression anglaise « emergency, critical and operative care "
            "(ECO-) system » désigne ici les services de soins")
    candidates = _latin(text)
    subject = _origin(candidates, "LAT_QUOTED_METALINGUISTIC_SUBJECT")
    head = _origin(candidates, "LAT_QUOTED_DEFINITION_FINITE")
    assert [(value.span, value.text) for value in subject] == [
        ((2, 80),
         "L’expression anglaise « emergency, critical and operative care "
         "(ECO-) system »")
    ]
    assert [(value.span, value.text) for value in head] == [
        ((81, 88), "désigne")
    ]


def test_quoted_definition_generalizes_to_a_different_finite_lexeme():
    text = "La notion « unité clinique » signifie un service intégré"
    assert [value.text for value in _origin(
        _latin(text), "LAT_QUOTED_DEFINITION_FINITE")] == ["signifie"]


@pytest.mark.parametrize(
    "text",
    [
        "« Pseudonymisierung » die Verarbeitung personenbezogener Daten",
        "Le rapport cite « unité clinique » dans son annexe",
        "3 L’expression « unité clinique désigne un service",
    ],
)
def test_quote_fragments_do_not_trigger_finite_definition_rule(text):
    assert not _origin(_latin(text), "LAT_QUOTED_DEFINITION_FINITE")


def test_initial_all_caps_operative_requires_its_infinitive_frame():
    text = ("AUTORISE le Directeur général à établir pour Mme Saima Wazed "
            "un contrat")
    head = _origin(_latin(text), "LAT_INITIAL_ALL_CAPS_OPERATIVE")
    assert [(value.span, value.text) for value in head] == [
        ((0, 8), "AUTORISE")
    ]


def test_initial_all_caps_operative_generalizes_without_a_verb_inventory():
    text = "HABILITE le secrétaire à signer le protocole"
    assert [value.text for value in _origin(
        _latin(text), "LAT_INITIAL_ALL_CAPS_OPERATIVE")] == ["HABILITE"]


@pytest.mark.parametrize(
    "text",
    [
        "DOCUMENTS DE REFERENCE POUR LA SESSION",
        "VISTO il regolamento del Parlamento europeo",
        "AUTORISE le Directeur général pour une période de quatre ans",
    ],
)
def test_all_caps_titles_and_incomplete_frames_are_controls(text):
    assert not _origin(_latin(text), "LAT_INITIAL_ALL_CAPS_OPERATIVE")


def test_typed_arabic_list_action_excludes_the_connective_from_the_head():
    text = "واستعراض عملية استحداث أدوات جديدة؛"
    head = _origin(
        _arabic(text, governed=True), "AR_GOVERNED_LIST_ACTION_NOMINAL")
    assert [(value.span, value.text) for value in head] == [
        ((1, 8), "استعراض")
    ]


def test_arabic_istifal_action_generalizes_to_a_different_root():
    text = "واستحداث آلية جديدة؛"
    assert [value.text for value in _origin(
        _arabic(text, governed=True),
        "AR_GOVERNED_LIST_ACTION_NOMINAL")] == ["استحداث"]


@pytest.mark.parametrize(
    "text,governed",
    [
        ("واستعراض عملية استحداث أدوات جديدة؛", False),
        ("واستعرض المجلس الأدوات الجديدة؛", True),
        ("واستعمال الأدوات الجديدة؛", False),
    ],
)
def test_arabic_action_requires_typing_and_the_exact_masdar_shape(text, governed):
    assert not _origin(
        _arabic(text, governed), "AR_GOVERNED_LIST_ACTION_NOMINAL")


def test_enumerated_to_infinitive_uses_bounded_enacting_context():
    text = "(1) to use the programme as the strategic basis"
    head = _origin(
        _latin(text, "3. REQUESTS the Director-General:"),
        "LAT_GOVERNED_TO_INFINITIVE",
    )
    assert [(value.span, value.text) for value in head] == [
        ((4, 10), "to use")
    ]


def test_enumerated_to_infinitive_generalizes_to_another_base_verb():
    text = "(4) to review the report"
    assert [value.text for value in _origin(
        _latin(text, "The Assembly DECIDES:"),
        "LAT_GOVERNED_TO_INFINITIVE")] == ["to review"]


@pytest.mark.parametrize(
    "text,left",
    [
        ("(1) to the Committee for review", "The Assembly REQUESTS:"),
        ("to use the programme", "The Assembly REQUESTS:"),
        ("(1) to use the programme", "Background and context"),
        ("(1) the Committee reviews the programme", "The Assembly REQUESTS:"),
    ],
)
def test_to_infinitive_needs_all_three_structural_cues(text, left):
    assert not _origin(
        _latin(text, left), "LAT_GOVERNED_TO_INFINITIVE")


@pytest.mark.parametrize(
    "text,language,left,governed",
    [
        ("La notion « unité clinique » signifie un service", "fr", "", False),
        ("HABILITE le secrétaire à signer le protocole", "fr", "", False),
        ("واستحداث آلية جديدة؛", "ar", "", True),
        ("(4) to review the report", "en", "The Assembly DECIDES:", False),
    ],
)
def test_new_predication_evidence_is_not_marked_as_no_predication(
        text, language, left, governed):
    script = V2.script_of(text)
    tokens = V2._tokens(text)
    candidates = (
        _arabic(text, governed)
        if language == "ar"
        else _latin(text, left)
    )
    transported = V2._transport_constructional_predication(
        V2.non_proposition_evidence(text, tokens, script), candidates)
    assert "NO_VERBAL_OR_PREDICATIVE_EVIDENCE" not in transported
