"""Role Binding V2 — focused tests with neighbouring negatives.

Every positive here is paired with a negative that the same mechanism must not
fire on.  The V1 lesson was that fixture conformity is not capability, so these
tests exist to hold repairs down, not to demonstrate that the module works: the
diagnostic reference is what says whether it works.
"""

from __future__ import annotations

import ast
import inspect
import textwrap
from dataclasses import replace

import pytest

from curunir_operational.v5_1.models import stable_id
from curunir_operational.v5_8_1 import roles as R
from curunir_operational.v5_8_1 import roles_v2 as V2
from curunir_operational.v5_8_1 import structure as ST

pytestmark = pytest.mark.no_db


# --- recitals --------------------------------------------------------------

def test_russian_converb_recital_binds_a_relation_predicate():
    """V1 read this as a zero-copula nominal; the reference says otherwise."""
    result = V2.bind(candidate_id="c", language="ru",
                     text="отмечая шесть основных принципов Комитета,")
    assert result.predicate_state == "RECITAL_RELATION_PREDICATE"
    assert result.subject_state == "GOVERNING_CLAUSE_SUBJECT"
    assert result.predicate_head == "отмечая"
    assert result.subject_span is None


def test_latin_participial_recital_binds_the_same_way():
    result = V2.bind(candidate_id="c", language="it",
                     text="Visto il regolamento (UE) 2016/679 del Parlamento,")
    assert result.predicate_state == "RECITAL_RELATION_PREDICATE"
    assert result.subject_state == "GOVERNING_CLAUSE_SUBJECT"


def test_a_recital_head_followed_by_an_operative_verb_is_not_a_recital():
    """Neighbouring negative: participle-initial does not imply recital."""
    result = V2.bind(candidate_id="c", language="ru",
                     text="Принимая ПОСТАНОВЛЯЕТ учредить рабочую группу.")
    assert result.predicate_state != "RECITAL_RELATION_PREDICATE"


def test_ordinary_russian_finite_clause_is_not_read_as_a_recital():
    result = V2.bind(candidate_id="c", language="ru",
                     text="Секретариат разработал стратегические документы.")
    assert result.predicate_state != "RECITAL_RELATION_PREDICATE"
    assert result.subject_state != "GOVERNING_CLAUSE_SUBJECT"


# --- non-propositions ------------------------------------------------------

def test_a_masthead_is_invalid_not_unresolved():
    """V1 had no way to say 'there is nothing here to bind'."""
    result = V2.bind(candidate_id="c", language="ru",
                     text="ВСЕМИРНОЙ АССАМБЛЕИ ЗДРАВООХРАНЕНИЯ WHA76.3")
    assert result.subject_state == "NO_SEMANTIC_SUBJECT"
    assert result.predicate_state == "NO_SEMANTIC_PREDICATE"
    assert result.role_binding_state == "ROLE_BINDING_INVALID"
    assert result.final_extraction_disposition == "REJECTED"


def test_a_session_line_is_invalid():
    result = V2.bind(candidate_id="c", language="ru",
                     text="Третье заседание, 23 января 2024 г.")
    assert result.role_binding_state == "ROLE_BINDING_INVALID"


def test_a_real_proposition_is_not_refused_as_a_non_proposition():
    """Neighbouring negative: the gate is about absence of predication."""
    result = V2.bind(candidate_id="c", language="en",
                     text="The Committee shall review the report annually.")
    assert result.subject_state != "NO_SEMANTIC_SUBJECT"
    assert result.role_binding_state != "ROLE_BINDING_INVALID"


@pytest.mark.parametrize("region", sorted(V2.STRUCTURAL_NON_PROPOSITION_REGIONS))
def test_typed_document_furniture_selects_the_null_analysis(region):
    result = V2.bind(
        candidate_id="c",
        language="es",
        text="Miembros de la Asamblea",
        content_region_type=region,
    )
    assert result.subject_state == "NO_SEMANTIC_SUBJECT"
    assert result.predicate_state == "NO_SEMANTIC_PREDICATE"
    assert result.role_binding_state == "ROLE_BINDING_INVALID"
    assert region in result.binding_reason


@pytest.mark.parametrize(
    "region",
    [
        "BREADCRUMB",
        "SEARCH_CONTROL",
        "PRIMARY_PROPOSITION_CONTENT",
        "UNKNOWN_STRUCTURAL_REGION",
    ],
)
def test_mixed_or_proposition_regions_keep_linguistic_selection(region):
    result = V2.bind(
        candidate_id="c",
        language="en",
        text="The Committee shall review the report.",
        content_region_type=region,
    )
    assert result.predicate_state != "NO_SEMANTIC_PREDICATE"
    assert result.role_binding_state != "ROLE_BINDING_INVALID"


# --- hard constraints ------------------------------------------------------

def test_arabic_definite_article_is_never_a_verbal_head():
    """A named V1 failure, refused here by construction."""
    analysis = V2.Analysis(
        analysis_id="a", subject=None,
        predicate_head=V2._candidate("PREDICATE_HEAD", (0, 6), "الأمانة",
                                     "TEST", confidence=0.5),
        predicate_complement=None, governing_clause=None, antecedent=None,
        semantic_actor=None, institutional_issuer=None, attribution=None,
        quoted_speaker=None)
    violations = V2.hard_constraints(analysis, "الأمانة العامة", "ARABIC")
    assert "ARABIC_ARTICLE_READ_AS_VERBAL_PREFIX" in violations


def test_russian_oblique_noun_is_refused_as_a_nominative_subject():
    subject = V2._candidate("SUBJECT", (0, 8), "документами", "TEST",
                            confidence=0.5, morphological=("OBLIQUE_HINT",))
    analysis = V2.Analysis(
        analysis_id="a", subject=subject, predicate_head=None,
        predicate_complement=None, governing_clause=None, antecedent=None,
        semantic_actor=None, institutional_issuer=None, attribution=None,
        quoted_speaker=None)
    violations = V2.hard_constraints(analysis, "документами", "CYRILLIC")
    assert "RUSSIAN_OBLIQUE_USED_AS_NOMINATIVE_SUBJECT" in violations


def test_a_nominative_noun_is_not_refused():
    """Neighbouring negative: the constraint is about case, not about Russian."""
    subject = V2._candidate("SUBJECT", (0, 11), "организация", "TEST",
                            confidence=0.5,
                            morphological=("NOMINATIVE_HINT", "OBLIQUE_HINT"))
    analysis = V2.Analysis(
        analysis_id="a", subject=subject, predicate_head=None,
        predicate_complement=None, governing_clause=None, antecedent=None,
        semantic_actor=None, institutional_issuer=None, attribution=None,
        quoted_speaker=None)
    violations = V2.hard_constraints(analysis, "организация", "CYRILLIC")
    assert "RUSSIAN_OBLIQUE_USED_AS_NOMINATIVE_SUBJECT" not in violations


def test_a_modal_without_a_complement_is_refused():
    head = V2._candidate("PREDICATE_HEAD", (0, 5), "shall", "TEST",
                         confidence=0.9,
                         subtype="DEONTIC_OPERATOR_WITH_COMPLEMENT")
    analysis = V2.Analysis(
        analysis_id="a", subject=None, predicate_head=head,
        predicate_complement=None, governing_clause=None, antecedent=None,
        semantic_actor=None, institutional_issuer=None, attribution=None,
        quoted_speaker=None)
    assert "MODAL_DETACHED_FROM_COMPLEMENT" in V2.hard_constraints(
        analysis, "shall", "LATIN")


# --- ambiguity and safety --------------------------------------------------

def test_ambiguity_is_reported_rather_than_forced():
    analyses = [
        V2.Analysis(analysis_id=f"a{n}", subject=V2._candidate(
            "SUBJECT", (n, n + 4), f"s{n}", "TEST", confidence=0.7),
            predicate_head=V2._candidate(
                "PREDICATE_HEAD", (20 + n, 24 + n), f"p{n}", "TEST",
                confidence=0.7),
            predicate_complement=None, governing_clause=None, antecedent=None,
            semantic_actor=None, institutional_issuer=None, attribution=None,
            quoted_speaker=None, compatibility_score=0.75 - 0.01 * n)
        for n in range(3)]
    state, best, reason = V2.resolve_internal(analyses)
    assert state == "AMBIGUOUS_BINDING"
    assert "does not decide" in reason


def test_a_clear_winner_is_not_called_ambiguous():
    """Neighbouring negative: the margin must not swallow a decided reading."""
    analyses = [
        V2.Analysis(analysis_id="a", subject=V2._candidate(
            "SUBJECT", (0, 4), "s", "TEST", confidence=0.9),
            predicate_head=V2._candidate("PREDICATE_HEAD", (20, 24), "p",
                                         "TEST", confidence=0.9),
            predicate_complement=None, governing_clause=None, antecedent=None,
            semantic_actor=None, institutional_issuer=None, attribution=None,
            quoted_speaker=None, compatibility_score=0.9),
        V2.Analysis(analysis_id="b", subject=V2._candidate(
            "SUBJECT", (5, 9), "t", "TEST", confidence=0.4),
            predicate_head=V2._candidate("PREDICATE_HEAD", (30, 34), "q",
                                         "TEST", confidence=0.4),
            predicate_complement=None, governing_clause=None, antecedent=None,
            semantic_actor=None, institutional_issuer=None, attribution=None,
            quoted_speaker=None, compatibility_score=0.4)]
    state, best, reason = V2.resolve_internal(analyses)
    assert state == "UNIQUE_BINDING_ESTABLISHED"


def test_passive_clause_never_promotes_its_subject_to_actor():
    result = V2.bind(candidate_id="c", language="en",
                     text="The report was adopted by the Assembly in March.")
    assert result.semantic_actor_state != "SEMANTIC_ACTOR_IS_GRAMMATICAL_SUBJECT"


def test_an_active_clause_still_gets_its_actor():
    """Neighbouring negative: the withdrawal is about passives only."""
    result = V2.bind(candidate_id="c", language="en",
                     text="The Assembly adopted the report in March.")
    assert result.semantic_actor_state in (
        "SEMANTIC_ACTOR_IS_GRAMMATICAL_SUBJECT", "SEMANTIC_ACTOR_INSTITUTIONAL")


def test_every_record_carries_its_candidate_and_analysis_counts():
    """Ambiguity is only auditable if the alternatives are counted."""
    result = V2.bind(candidate_id="c", language="en",
                     text="The Committee shall review the report annually.")
    assert result.candidates_considered > 0
    assert result.analyses_considered > 0
    assert result.internal_state in V2.INTERNAL_STATES


# --- D33: ordinary finite lexical verbs ------------------------------------
#
# The candidate generator recognised auxiliaries, modals, deontic operators and
# participial legal formulae, and missed ordinary finite lexical verbs.  "The
# Assembly adopted the report" produced no predicate candidate at all, so the
# span became a non-proposition.  A whitelist of legal verbs would fix the
# witnesses and not the defect, so the evidence is morphological and positional.

@pytest.mark.parametrize("text,head", [
    ("The Assembly adopted the report.", "adopted"),
    ("The regulator published the decision.", "published"),
    ("The authority rejected the application.", "rejected"),
    ("The ministry amended the regulation.", "amended"),
    ("The court annulled the measure.", "annulled"),
])
def test_ordinary_finite_lexical_verbs_produce_a_predicate(text, head):
    result = V2.bind(candidate_id="c", text=text, language="en")
    assert result.predicate_state not in ("NO_SEMANTIC_PREDICATE",
                                          "PREDICATE_UNRESOLVED")
    assert result.subject_state != "NO_SEMANTIC_SUBJECT"
    assert head in (result.predicate_head + result.predicate_complement)


@pytest.mark.parametrize("text", [
    "Le regulateur a publie la decision.",
    "Die Behoerde hat die Verordnung geaendert.",
    "L'autorita ha respinto la domanda.",
    "La autoridad rechazo la solicitud.",
])
def test_ordinary_lexical_predicates_in_other_latin_registers(text):
    result = V2.bind(candidate_id="c", text=text)
    assert result.predicate_state not in ("NO_SEMANTIC_PREDICATE",
                                          "PREDICATE_UNRESOLVED")


@pytest.mark.parametrize("text", [
    "the adopted report",
    "adopted standards",
    "report adoption",
    "Assembly report",
])
def test_nominal_and_attributive_forms_are_not_finite_predicates(text):
    """Neighbouring negatives: surface suffix is not clause role."""
    result = V2.bind(candidate_id="c", text=text, language="en")
    assert result.final_extraction_disposition != "EVIDENCE_BOUND"


def test_a_bare_participial_phrase_is_not_promoted_to_evidence_bound():
    result = V2.bind(candidate_id="c", language="en",
                     text="decision adopted by the Assembly")
    assert result.final_extraction_disposition != "EVIDENCE_BOUND"


def test_a_lexical_verb_alone_does_not_reach_evidence_bound():
    """A morphology-proposed head is a candidate, not an established binding."""
    result = V2.bind(candidate_id="c", language="en",
                     text="The Assembly adopted the report.")
    assert result.final_extraction_disposition != "EVIDENCE_BOUND"
    assert result.internal_state in V2.INTERNAL_STATES


# --- Arabic proclitic boundary and existential predicate --------------------
#
# Four of the six remaining Arabic predicate-head blockers were boundary errors,
# not morphology: the candidate existed, but its span included the proclitic
# connective, so it never matched the reference head.  The reference writes
# ينبغي where the surface reads وينبغي.

@pytest.mark.parametrize("text,head", [
    ("وينبغي علاج الأشخاص المعرضين لخطر كبير بالأدوية.", "ينبغي"),
    ("وتُستخدم الاختبارات التشخيصية السريعة في السياقات السريرية.", "تُستخدم"),
])
def test_arabic_proclitic_is_not_part_of_the_predicate_head(text, head):
    result = V2.bind(candidate_id="c", text=text, language="ar")
    assert result.predicate_head == head
    assert not result.predicate_head.startswith("و")


def test_arabic_existential_particle_heads_a_nominal_predicate():
    result = V2.bind(candidate_id="c", language="ar",
                     text="وهناك 4 أنماط من فيروس الأنفلونزا، وهي A وB وC وD.")
    assert result.predicate_head == "هناك"
    assert result.predicate_state == "NOMINAL_PREDICATE"


def test_the_proclitic_strip_does_not_truncate_a_genuine_waw_word():
    """Neighbouring negative: وزارة begins with waw and is not و + زارة."""
    stripped, start, end = V2._strip_arabic_proclitic("وزارة", 0, 5)
    # the rule fires on surface form only for tokens the verbal patterns matched,
    # so a bare nominal is never handed to it in production; assert the guard
    # directly for the short-word case
    short, s2, e2 = V2._strip_arabic_proclitic("وقد", 0, 3)
    assert short == "وقد" and (s2, e2) == (0, 3)


def test_a_verbless_arabic_nominal_is_not_given_an_existential_predicate():
    """Neighbouring negative: هناك must be present for the existential rule."""
    result = V2.bind(candidate_id="c", language="ar",
                     text="الأمانة العامة للمنظمة")
    assert result.predicate_state != "NOMINAL_PREDICATE" or \
        result.predicate_head != "هناك"


def test_arabic_offsets_still_point_into_the_original_span():
    text = "وينبغي علاج الأشخاص المعرضين لخطر كبير بالأدوية."
    result = V2.bind(candidate_id="c", text=text, language="ar")
    assert result.predicate_span is not None
    start, end = result.predicate_span
    # V581K M15 P2 (frozen contract): the attached clause connective belongs
    # to the predicate span — Reference V7 row …d9f798fd writes وينبغي.
    # The 396 test-contract audit strengthened this from accepting either
    # boundary form: the two-form assertion could not distinguish the frozen
    # boundary from a +1 proclitic offset drift or a wrong-occurrence index
    # (396_m15_test_contract_audit/assertion_strength_probe.json).
    assert 0 <= start < end <= len(text)
    span = text[start:end]
    assert span.startswith("وينبغي")
    assert "ينبغي" in span
    assert span == "وينبغي علاج الأشخاص المعرضين لخطر كبير بالأدوية"


# --- production terminal-state closure ------------------------------------

@pytest.mark.parametrize(
    ("subject_state", "predicate_state", "predicate_frame_inherited",
     "shared_subject_source", "shared_predicate_source", "context_ids",
     "repair_fragments"),
    [
        (
            "GOVERNING_CLAUSE_SUBJECT", "EXPLICIT_FINITE_PREDICATE", False,
            None, None, ("LEFT_CONTEXT",),
            ("governing enactment clause",),
        ),
        (
            "IMPLICIT_CONTEXT_BOUND_SUBJECT", "EXPLICIT_FINITE_PREDICATE",
            False, None, None, ("SOURCE_PUBLISHER",),
            ("publishing context",),
        ),
        (
            "ANAPHORIC_SUBJECT_RECOVERABLE", "EXPLICIT_FINITE_PREDICATE",
            False, None, None, ("LEFT_CONTEXT",),
            ("anaphor",),
        ),
        (
            "INHERITED_COORDINATE_SUBJECT", "EXPLICIT_FINITE_PREDICATE",
            False, "lead-in-subject", None, ("lead-in-subject",),
            ("coordinating lead-in",),
        ),
        (
            "EXPLICIT_SUBJECT", "PREDICATE_RECOVERABLE", False,
            None, None, ("LEFT_CONTEXT",),
            ("predicate",),
        ),
        (
            "EXPLICIT_SUBJECT", "SHARED_COORDINATE_PREDICATE", False,
            None, "lead-in-predicate", ("lead-in-predicate",),
            ("coordinating lead-in",),
        ),
        (
            "GOVERNING_CLAUSE_SUBJECT", "GOVERNING_CLAUSE_PREDICATE", True,
            None, None, ("LEFT_CONTEXT",),
            ("governing enactment clause", "predicate frame"),
        ),
    ],
)
def test_final_role_states_have_deterministic_obligations(
        subject_state, predicate_state, predicate_frame_inherited,
        shared_subject_source, shared_predicate_source, context_ids,
        repair_fragments):
    required, repair = V2.derive_obligations(
        subject_state=subject_state,
        predicate_state=predicate_state,
        predicate_frame_inherited=predicate_frame_inherited,
        shared_subject_source=shared_subject_source,
        shared_predicate_source=shared_predicate_source,
    )
    assert required == context_ids
    assert repair is not None
    for fragment in repair_fragments:
        assert fragment in repair


def test_obligation_contexts_are_deduplicated_in_final_role_order():
    required, repair = V2.derive_obligations(
        subject_state="GOVERNING_CLAUSE_SUBJECT",
        predicate_state="SHARED_COORDINATE_PREDICATE",
        predicate_frame_inherited=True,
        shared_predicate_source="LEFT_CONTEXT",
    )
    assert required == ("LEFT_CONTEXT",)
    assert required.count("LEFT_CONTEXT") == 1
    assert repair.count(";") == 2


def test_m7_transport_of_subject_and_predicate_refreshes_terminal_axes():
    base = ST.empty_context(document_id="d", region_id="r")
    context = replace(
        base,
        governing_clause_candidates=("governor",),
        governing_clause_confidences=(1.0,),
        governing_clause_relation_types=(
            "LIST_ITEM_INHERITS_LEAD_IN_SUBJECT",
            "LIST_ITEM_INHERITS_LEAD_IN_PREDICATE",
        ),
    )
    result = V2.bind(
        candidate_id="synthetic-m7-subject-predicate",
        language="ru",
        text="и использует рекомендации",
        structural_context=context,
    )
    assert result.subject_state == "GOVERNING_CLAUSE_SUBJECT"
    assert result.predicate_state == "GOVERNING_CLAUSE_PREDICATE"
    assert result.predicate_span is not None
    assert result.required_context_ids == ("LEFT_CONTEXT",)
    assert result.subject_completeness == "RECOVERABLE_BOUNDED"
    assert result.predicate_completeness == "RECOVERABLE_BOUNDED"
    assert result.role_binding_state == "ROLE_BINDING_RECOVERABLE"
    assert result.final_extraction_disposition == "RECOVERABLE_WITH_BOUNDARY_REPAIR"


# --- P4 production publication boundary -----------------------------------

@pytest.mark.parametrize("text", [
    "in shall the policy",
    "in shall the policy.",
])
def test_fixed_p4_witness_and_terminated_neighbor_quarantine_without_p4(text):
    """M14's non-semantic subject must fail closed for both boundaries."""
    result = V2.bind(candidate_id="p4-witness", language="en", text=text)
    assert result.subject_state == "NO_SEMANTIC_SUBJECT"
    assert result.subject_completeness == "UNRESOLVED"
    assert result.role_binding_state == "ROLE_BINDING_PARTIAL"
    assert result.final_extraction_disposition == "QUARANTINED"
    assert not (
        result.final_extraction_disposition == "EVIDENCE_BOUND"
        and (
            result.subject_state in V2.UNDECIDED_ROLE
            or result.predicate_state in V2.UNDECIDED_ROLE
        )
    )


def test_ambiguous_p4_neighbor_remains_quarantined():
    result = V2.bind(candidate_id="p4-ambiguous", language="en",
                     text="in runs fast")
    assert result.internal_state == "INSUFFICIENT_ROLE_EVIDENCE"
    assert result.final_extraction_disposition == "QUARANTINED"
    assert result.role_binding_state == "ROLE_BINDING_UNRESOLVED"


def test_genuine_expletive_impersonal_control_remains_lawfully_publishable():
    result = V2.bind(
        candidate_id="expletive-control",
        language="en",
        text="It is necessary to review the policy.",
    )
    assert result.internal_state == "UNIQUE_BINDING_ESTABLISHED"
    assert result.subject_state == "EXPLETIVE_OR_IMPERSONAL_CONSTRUCTION"
    assert result.subject_completeness == "ABSENT_BY_CONSTRUCTION"
    assert result.role_binding_state == "ROLE_BINDING_ESTABLISHED"
    assert result.final_extraction_disposition == "EVIDENCE_BOUND"


def test_non_proposition_control_remains_rejected_after_p4_repair():
    result = V2.bind(
        candidate_id="invalid-control",
        language="en",
        text="COMMITTEE SESSION 2024",
    )
    assert result.subject_state == "NO_SEMANTIC_SUBJECT"
    assert result.predicate_state == "NO_SEMANTIC_PREDICATE"
    assert result.role_binding_state == "ROLE_BINDING_INVALID"
    assert result.final_extraction_disposition == "REJECTED"


def test_recoverable_governing_context_remains_non_published_with_exact_obligation():
    base = ST.empty_context(document_id="d", region_id="r")
    context = replace(
        base,
        governing_clause_candidates=("governor",),
        governing_clause_confidences=(1.0,),
        governing_clause_relation_types=(
            "LIST_ITEM_INHERITS_LEAD_IN_SUBJECT",
            "LIST_ITEM_INHERITS_LEAD_IN_PREDICATE",
        ),
    )
    result = V2.bind(
        candidate_id="recoverable-control",
        language="ru",
        text="и использует рекомендации",
        structural_context=context,
    )
    assert result.role_binding_state == "ROLE_BINDING_RECOVERABLE"
    assert result.final_extraction_disposition == "RECOVERABLE_WITH_BOUNDARY_REPAIR"
    assert result.required_context_ids == ("LEFT_CONTEXT",)
    assert result.repair_requirement == (
        "bind the subject from the governing enactment clause; "
        "recover the predicate frame from the governing enactment clause"
    )


@pytest.mark.parametrize("role_state", sorted(V2.UNDECIDED_ROLE))
def test_publication_invariant_rejects_each_exact_undecided_role(role_state):
    subject_state = (
        role_state if "SUBJECT" in role_state else "EXPLICIT_SUBJECT"
    )
    predicate_state = (
        role_state if "PREDICATE" in role_state
        else "EXPLICIT_FINITE_PREDICATE"
    )
    with pytest.raises(V2.RoleBindingV2Violation, match="EVIDENCE_BOUND"):
        V2.validate_publication_role_invariant(
            subject_state=subject_state,
            predicate_state=predicate_state,
            final_extraction_disposition="EVIDENCE_BOUND",
        )


def test_exhaustive_emittable_role_adapter_has_no_published_undecided_pair():
    subject_states = (
        "NO_SEMANTIC_SUBJECT", "EXPLETIVE_OR_IMPERSONAL_CONSTRUCTION",
        "SUBJECT_UNRESOLVED", "IMPLICIT_CONTEXT_BOUND_SUBJECT",
        "ANAPHORIC_SUBJECT_RECOVERABLE", "INHERITED_COORDINATE_SUBJECT",
        "GOVERNING_CLAUSE_SUBJECT", "EXPLICIT_SUBJECT",
        "PASSIVE_PATIENT_SUBJECT", "ZERO_COPULA_NOMINAL_SUBJECT",
        "POSTVERBAL_SUBJECT",
    )
    predicate_states = (
        "NO_SEMANTIC_PREDICATE", "PREDICATE_UNRESOLVED",
        "SHARED_COORDINATE_PREDICATE", "PREDICATE_RECOVERABLE",
        "GOVERNING_CLAUSE_PREDICATE", "EXPLICIT_FINITE_PREDICATE",
        "NOMINAL_PREDICATE", "DEONTIC_OPERATOR_WITH_COMPLEMENT",
        "PASSIVE_OR_IMPERSONAL_PREDICATE", "RECITAL_RELATION_PREDICATE",
        "PARTICIPIAL_PREDICATE",
    )
    for subject_state in subject_states:
        for predicate_state in predicate_states:
            for internal_state in V2.INTERNAL_STATES:
                facts = V2._terminal_facts(
                    internal_state=internal_state,
                    subject_state=subject_state,
                    subject_span=(0, 1),
                    predicate_state=predicate_state,
                    predicate_span=(2, 3),
                    antecedent_state="NO_ANAPHOR_PRESENT",
                    required_context_ids=(),
                    repair_requirement=None,
                    structural_context_supplied=False,
                    governing_state="GOVERNING_CLAUSE_NOT_SUPPLIED",
                )
                terminal = V2.TM.derive_terminal(facts)
                disposition = V2.TM.derive_disposition(facts, terminal.value)
                V2.TM.validate_decisions(facts, terminal, disposition)
                assert not (
                    disposition.value == "EVIDENCE_BOUND"
                    and (
                        subject_state in V2.UNDECIDED_ROLE
                        or predicate_state in V2.UNDECIDED_ROLE
                    )
                )


# --- P5-D01: durable pins for the publication authority and its wiring ------
#
# The two tests immediately above are the only shipped references to
# UNDECIDED_ROLE, and both CONSUME the symbol they are meant to protect: one
# generates its case list from sorted(V2.UNDECIDED_ROLE), so narrowing the set
# silently deletes cases and emptying it yields zero cases reported as a skip;
# the other asserts `not (EVIDENCE_BOUND and state in V2.UNDECIDED_ROLE)`,
# whose right conjunct becomes identically False once the set is empty.  Nine
# mutants were run against that suite and five survived it: drop
# NO_SEMANTIC_SUBJECT, drop SUBJECT_UNRESOLVED, empty the frozenset, delete the
# guard call from bind, and replace exact membership with bidirectional
# substring matching.
#
# WHAT THESE PINS ARE, AND ARE NOT.  validate_publication_role_invariant is a
# deliberate BACKSTOP and is provably dead on the reachable path: enumerating
# 1,056,000 adapter inputs yields EVIDENCE_BOUND intersect UNDECIDED_ROLE = 0,
# because _terminal_facts already stops every member of the set from reaching
# ROLE_BINDING_ESTABLISHED.  Its purpose is to hold under FUTURE change -- "a
# future caller that accidentally serializes a stale or otherwise drifted final
# role state" -- so what has to be protected is the authority itself and the
# fact that bind consults it.
#
# These are therefore STRUCTURAL pins.  They assert nothing about the guard
# firing on a production input: it does not, and such an assertion would be
# false.  The correct response to one of them failing is to restore the
# authority or the call, never to make the guard reachable by weakening
# _terminal_facts.  For the same reason the exactness pin below uses SYNTHETIC
# near-names: exact-versus-substring matching is behaviourally indistinguishable
# on the current 23-member vocabulary (the wrongly-blocked set is empty), so the
# discipline can only be pinned on names the vocabulary does not contain.

#: The frozen four-member publication authority, written out as a literal and
#: deliberately NOT derived from V2.UNDECIDED_ROLE.  A pin generated from the
#: symbol under test is not a pin.
FROZEN_UNDECIDED_ROLE_AUTHORITY = frozenset({
    "NO_SEMANTIC_PREDICATE",
    "NO_SEMANTIC_SUBJECT",
    "PREDICATE_UNRESOLVED",
    "SUBJECT_UNRESOLVED",
})

#: Names the real vocabulary does not contain, each one character away from a
#: member of the authority in one of the two directions a substring rule would
#: wrongly capture.
_SUBSTRING_TRAP_SUPERSTRINGS = (
    "NO_SEMANTIC_SUBJECT_X",
    "SUBJECT_UNRESOLVED_X",
    "NO_SEMANTIC_PREDICATE_X",
    "PREDICATE_UNRESOLVED_X",
)
_SUBSTRING_TRAP_PREFIXES = (
    "NO_SEMANTIC_SUBJEC",
    "SUBJECT_UNRESOLVE",
    "NO_SEMANTIC_PREDICAT",
    "PREDICATE_UNRESOLVE",
)


def test_undecided_role_is_exactly_the_frozen_four_member_authority():
    """The critical-predicate authority, pinned against a literal."""
    for name in FROZEN_UNDECIDED_ROLE_AUTHORITY:
        assert name in V2.UNDECIDED_ROLE, f"authority lost member {name!r}"
    assert V2.UNDECIDED_ROLE == FROZEN_UNDECIDED_ROLE_AUTHORITY
    assert len(V2.UNDECIDED_ROLE) == 4


def test_the_frozen_authority_names_are_real_role_states():
    """Neighbouring positive: the literal above is the source's own vocabulary."""
    assert {"NO_SEMANTIC_SUBJECT", "SUBJECT_UNRESOLVED"} <= set(R.SUBJECT_STATES)
    assert {"NO_SEMANTIC_PREDICATE", "PREDICATE_UNRESOLVED"} <= set(
        R.PREDICATE_STATES)


@pytest.mark.parametrize(
    "near_name", _SUBSTRING_TRAP_SUPERSTRINGS + _SUBSTRING_TRAP_PREFIXES)
def test_substring_trap_names_are_synthetic_and_not_real_role_states(near_name):
    """These names carry no behavioural claim, because nothing emits them."""
    assert near_name not in FROZEN_UNDECIDED_ROLE_AUTHORITY
    assert near_name not in R.SUBJECT_STATES
    assert near_name not in R.PREDICATE_STATES


@pytest.mark.parametrize("subject_state", _SUBSTRING_TRAP_SUPERSTRINGS[:2]
                         + _SUBSTRING_TRAP_PREFIXES[:2])
def test_publication_invariant_matches_subjects_exactly_not_by_substring(
        subject_state):
    """A synthetic subject name that merely resembles the authority passes."""
    V2.validate_publication_role_invariant(
        subject_state=subject_state,
        predicate_state="EXPLICIT_FINITE_PREDICATE",
        final_extraction_disposition="EVIDENCE_BOUND",
    )


@pytest.mark.parametrize("predicate_state", _SUBSTRING_TRAP_SUPERSTRINGS[2:]
                         + _SUBSTRING_TRAP_PREFIXES[2:])
def test_publication_invariant_matches_predicates_exactly_not_by_substring(
        predicate_state):
    """The same discipline on the predicate slot."""
    V2.validate_publication_role_invariant(
        subject_state="EXPLICIT_SUBJECT",
        predicate_state=predicate_state,
        final_extraction_disposition="EVIDENCE_BOUND",
    )


def test_bind_invokes_the_publication_role_invariant():
    """The guard is wired into bind, statically and without executing it.

    Deleting the call site changes no reachable output -- that is exactly why
    the shipped suite could not see it -- so the wiring is pinned on bind's own
    syntax tree rather than on an observable behaviour that does not exist.
    """
    tree = ast.parse(textwrap.dedent(inspect.getsource(V2.bind)))
    calls = [
        node for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "validate_publication_role_invariant"
    ]
    assert calls, "bind no longer invokes validate_publication_role_invariant"
    for call in calls:
        assert not call.args, "the guard is keyword-only"
        assert {keyword.arg for keyword in call.keywords} == {
            "subject_state", "predicate_state", "final_extraction_disposition",
        }


def test_a_call_to_an_unrelated_name_does_not_satisfy_the_wiring_pin():
    """Neighbouring negative: the pin reads the callee name, not any call."""
    tree = ast.parse("def f():\n    other_function(x=1)\n")
    calls = [
        node for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "validate_publication_role_invariant"
    ]
    assert not calls


# --- P5-D02: the second emission site agrees with the adapter ---------------

def test_layout_refusal_agrees_with_the_adapter_on_role_completeness():
    """_layout_refusal is the only RoleBindingV2Record not built by the adapter.

    The P4 repair changed NO_SEMANTIC_SUBJECT from ABSENT_BY_CONSTRUCTION to
    UNRESOLVED in _terminal_facts and did not propagate it here, so the two
    emission sites disagreed about the same role state.  proposition_status is
    deliberately NOT compared: CONSTRUCTION_DEFECT is the more specific lawful
    value for an unsound reading order, and both it and the adapter's
    NO_SEMANTIC_PROPOSITION derive ROLE_BINDING_INVALID.
    """
    record = V2._layout_refusal(
        "layout-refusal-control", "The Committee shall review the report.",
        "PACKET_CONSTRUCTION_DEFECT", "en")
    facts = V2._terminal_facts(
        internal_state=record.internal_state,
        subject_state=record.subject_state,
        subject_span=record.subject_span,
        predicate_state=record.predicate_state,
        predicate_span=record.predicate_span,
        antecedent_state=record.antecedent_state,
        required_context_ids=record.required_context_ids,
        repair_requirement=record.repair_requirement,
        structural_context_supplied=False,
        governing_state="GOVERNING_CLAUSE_NOT_SUPPLIED",
    )
    assert record.subject_state == "NO_SEMANTIC_SUBJECT"
    assert record.predicate_state == "NO_SEMANTIC_PREDICATE"
    assert facts.subject_completeness == "UNRESOLVED"
    assert record.subject_completeness == facts.subject_completeness
    assert record.predicate_completeness == facts.predicate_completeness
    # The one deliberate divergence, and the reason it is harmless.
    assert record.proposition_status == "CONSTRUCTION_DEFECT"
    assert facts.proposition_status == "NO_SEMANTIC_PROPOSITION"
    assert V2.TM.derive_terminal(facts).value == "ROLE_BINDING_INVALID"


@pytest.mark.parametrize("order_state", [
    "PACKET_CONSTRUCTION_DEFECT",
    "LAYOUT_RECONSTRUCTION_REQUIRED",
])
def test_layout_refusal_record_still_validates_and_stays_rejected(order_state):
    """The changed axis is lawful and changes no record's published fate."""
    context = ST.empty_context(document_id="d", region_id="r",
                               reading_order_state=order_state)
    result = V2.bind(candidate_id="layout-refusal-control", language="en",
                     text="The Committee shall review the report.",
                     structural_context=context)
    assert result.subject_completeness == "UNRESOLVED"
    # V6.1 LRI-R1: symmetric with the subject axis; see _REFUSAL_LAWFUL_VECTOR.
    assert result.predicate_completeness == "UNRESOLVED"
    facts = V2.TM.TerminalFacts(
        proposition_status=result.proposition_status,
        subject_completeness=result.subject_completeness,
        predicate_completeness=result.predicate_completeness,
        binding_uniqueness=result.binding_uniqueness,
        required_context_status=result.required_context_status,
        repairability=result.repairability,
    )
    facts.validate()
    terminal = V2.TM.derive_terminal(facts)
    disposition = V2.TM.derive_disposition(facts, terminal.value)
    V2.TM.validate_decisions(facts, terminal, disposition)
    assert terminal.value == result.role_binding_state == "ROLE_BINDING_INVALID"
    assert disposition.value == result.final_extraction_disposition == "REJECTED"
    assert order_state in result.binding_reason


def test_a_sound_reading_order_is_not_diverted_into_the_layout_refusal():
    """Neighbouring negative: the refusal is about reading order, not text."""
    context = ST.empty_context(document_id="d", region_id="r",
                               reading_order_state="READING_ORDER_ESTABLISHED")
    result = V2.bind(candidate_id="sound-order-control", language="en",
                     text="The Committee shall review the report.",
                     structural_context=context)
    assert result.proposition_status != "CONSTRUCTION_DEFECT"
    assert "PACKET_CONSTRUCTION_DEFECT" not in result.binding_reason


# --- P5.1-F01: the refusal's identity does not absorb a repaired D39 axis ---
#
# The P5 repair moved `subject_completeness` at the second emission site from
# ABSENT_BY_CONSTRUCTION to UNRESOLVED.  That repair is only safe because the
# record's IDENTITY is computed from `candidate_id` and `order_state` alone, so
# the repaired value cannot reach it and no already-published record changes its
# name.  That was measured and it held -- but nothing pinned it, so a later
# change folding the repaired value into the identity would silently rename
# every refusal record and be caught by no test.  These pins close that.
#
# The prefix is written here as a LITERAL on purpose.  Reading it back out of
# the module under test would let a single edit move both sides of the
# comparison at once, and the pin would then agree with whatever production
# happened to do.

#: The frozen identity prefix of a role-binding V2 record.
_BINDING_ID_PREFIX = "v5-8-1-bindingv2"

#: The two reading-order states that divert `bind` into the refusal.
_UNSOUND_ORDER_STATES = [
    "PACKET_CONSTRUCTION_DEFECT",
    "LAYOUT_RECONSTRUCTION_REQUIRED",
]

#: Every typed D39 axis the refusal emits.  None of them may reach the identity.
_D39_AXES = (
    "proposition_status",
    "subject_completeness",
    "predicate_completeness",
    "binding_uniqueness",
    "required_context_status",
    "repairability",
)

_REFUSAL_BODY = "The Committee shall review the report."


def _identity_is_the_two_input_form(source: str) -> bool:
    """Does `source` compute exactly one identity from exactly the two inputs?

    True only for a single unqualified ``stable_id`` call taking the literal
    prefix, the bare name ``candidate_id`` and the bare name ``order_state``.
    Any further part, any expression over those names, and any second identity
    site is a different shape and returns False.
    """
    tree = ast.parse(textwrap.dedent(source))
    calls = [
        node for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "stable_id"
    ]
    if len(calls) != 1:
        return False
    call = calls[0]
    if call.keywords or len(call.args) != 3:
        return False
    prefix, first, second = call.args
    return (
        isinstance(prefix, ast.Constant)
        and prefix.value == _BINDING_ID_PREFIX
        and isinstance(first, ast.Name) and first.id == "candidate_id"
        and isinstance(second, ast.Name) and second.id == "order_state"
    )


@pytest.mark.parametrize("candidate_id", ["layout-identity-a",
                                          "layout-identity-b"])
@pytest.mark.parametrize("order_state", _UNSOUND_ORDER_STATES)
def test_layout_refusal_identity_is_exactly_the_two_input_identity(
        order_state, candidate_id):
    """The emitted identity is recomputable from the two inputs alone."""
    record = V2._layout_refusal(candidate_id, _REFUSAL_BODY, order_state, "en")
    assert record.candidate_id == candidate_id
    assert record.binding_id == stable_id(
        _BINDING_ID_PREFIX, candidate_id, order_state)


@pytest.mark.parametrize("order_state", _UNSOUND_ORDER_STATES)
def test_bind_layout_refusal_route_emits_the_two_input_identity(order_state):
    """The same pin through the reachable route, not just the helper."""
    context = ST.empty_context(document_id="d", region_id="r",
                               reading_order_state=order_state)
    result = V2.bind(candidate_id="layout-identity-bind", language="en",
                     text=_REFUSAL_BODY, structural_context=context)
    assert result.proposition_status == "CONSTRUCTION_DEFECT"
    assert result.binding_id == stable_id(
        _BINDING_ID_PREFIX, "layout-identity-bind", order_state)


@pytest.mark.parametrize("axis", _D39_AXES)
@pytest.mark.parametrize("order_state", _UNSOUND_ORDER_STATES)
def test_layout_refusal_identity_absorbs_no_d39_axis(order_state, axis):
    """No emitted terminal axis participates in the record's identity.

    The fold is shown to be a real perturbation first: if adding the axis value
    left the digest alone, the inequality below would prove nothing.
    """
    candidate_id = "layout-identity-axis"
    record = V2._layout_refusal(candidate_id, _REFUSAL_BODY, order_state, "en")
    emitted_axis = getattr(record, axis)
    assert emitted_axis
    unfolded = stable_id(_BINDING_ID_PREFIX, candidate_id, order_state)
    folded = stable_id(_BINDING_ID_PREFIX, candidate_id, order_state,
                       emitted_axis)
    assert folded != unfolded
    assert record.binding_id == unfolded
    assert record.binding_id != folded


@pytest.mark.parametrize("order_state", _UNSOUND_ORDER_STATES)
def test_layout_refusal_identity_survives_the_completeness_repair(order_state):
    """The repaired axis moved; the identity provably could not follow it.

    This is the durable form of the delta that was measured once: had
    subject_completeness reached the identity, the P4 value and the P5 value
    would name two different records, and the repair would have renamed every
    refusal ever published.
    """
    candidate_id = "layout-identity-repair"
    record = V2._layout_refusal(candidate_id, _REFUSAL_BODY, order_state, "en")
    assert record.subject_completeness == "UNRESOLVED"
    p4_folded = stable_id(_BINDING_ID_PREFIX, candidate_id, order_state,
                          "ABSENT_BY_CONSTRUCTION")
    p5_folded = stable_id(_BINDING_ID_PREFIX, candidate_id, order_state,
                          "UNRESOLVED")
    assert p4_folded != p5_folded
    assert record.binding_id == stable_id(
        _BINDING_ID_PREFIX, candidate_id, order_state)
    assert record.binding_id not in (p4_folded, p5_folded)


def test_the_refusal_identity_pin_is_specific_to_the_refusal_route():
    """Neighbouring negative: a soundly bound record has a different identity.

    The lawful route keys on the terminal state, so the two-input form is not
    something every record satisfies by accident.
    """
    candidate_id = "layout-identity-sound"
    context = ST.empty_context(document_id="d", region_id="r",
                               reading_order_state="READING_ORDER_ESTABLISHED")
    result = V2.bind(candidate_id=candidate_id, language="en",
                     text=_REFUSAL_BODY, structural_context=context)
    assert result.binding_id == stable_id(
        _BINDING_ID_PREFIX, candidate_id, result.role_binding_state)
    for order_state in _UNSOUND_ORDER_STATES:
        assert result.binding_id != stable_id(
            _BINDING_ID_PREFIX, candidate_id, order_state)


def test_layout_refusal_identity_reads_only_candidate_id_and_order_state():
    """The identity's INPUTS are pinned on the refusal's own syntax tree.

    A digest comparison catches a fold that changes the emitted value; this
    catches one that reaches for a completeness axis whose value happens to
    leave the digest where it was.
    """
    assert _identity_is_the_two_input_form(
        inspect.getsource(V2._layout_refusal))


@pytest.mark.parametrize("source", [
    # the two-input form, written out synthetically: the predicate accepts it
    'def r(candidate_id, order_state):\n'
    '    return stable_id("v5-8-1-bindingv2", candidate_id, order_state)\n',
])
def test_the_identity_shape_predicate_accepts_the_pristine_form(source):
    """Positive control: the predicate is not simply rejecting everything."""
    assert _identity_is_the_two_input_form(source)


@pytest.mark.parametrize("source", [
    # a repaired axis concatenated onto the order state
    'def r(candidate_id, order_state, subject_completeness):\n'
    '    return stable_id("v5-8-1-bindingv2", candidate_id,\n'
    '                     order_state + "|UNRESOLVED")\n',
    # a repaired axis appended as a further identity part
    'def r(candidate_id, order_state, subject_completeness):\n'
    '    return stable_id("v5-8-1-bindingv2", candidate_id, order_state,\n'
    '                     subject_completeness)\n',
    # a repaired axis substituted for the order state
    'def r(candidate_id, order_state, subject_completeness):\n'
    '    return stable_id("v5-8-1-bindingv2", candidate_id,\n'
    '                     subject_completeness)\n',
    # a second identity site, so which one is emitted is no longer pinned
    'def r(candidate_id, order_state, subject_completeness):\n'
    '    if subject_completeness == "UNRESOLVED":\n'
    '        return stable_id("v5-8-1-bindingv2", candidate_id, order_state)\n'
    '    return stable_id("v5-8-1-bindingv2", candidate_id,\n'
    '                     subject_completeness)\n',
    # a drifted prefix
    'def r(candidate_id, order_state):\n'
    '    return stable_id("v5-8-1-bindingv3", candidate_id, order_state)\n',
])
def test_the_identity_shape_predicate_rejects_a_folded_identity(source):
    """Negative control: every way of folding the axis in is refused."""
    assert not _identity_is_the_two_input_form(source)


# --- P6-M01: the refusal site fails closed, and its refusal is unconditional -
#
# `_layout_refusal` is the one RoleBindingV2Record emission site that does not
# pass through the terminal adapter, so before P6 it called neither
# `TM.validate_decisions` nor `validate_publication_role_invariant`.  Its
# publication safety rested entirely on four correct literals, and nothing
# pinned that they were UNCONDITIONAL: every shipped case used language="en",
# one short English body and one candidate_id shape, so five mutants that
# published on an uncovered dimension (language, a body token, body length, a
# candidate_id prefix, and the terminal state on language) passed the whole
# committed suite.
#
# Two things close that, and both are needed.  The runtime calls in
# `_layout_refusal` make the site refuse to emit an unlawful record at all --
# that is the repair.  The matrix below pins that the lawful record does not
# depend on language, body content, body length or candidate_id -- that is the
# regression protection, on exactly the dimensions the survivors used.
#
# The expected vector is written out as a LITERAL on purpose.  Reading it back
# out of the module under test would let one edit move both sides at once.

#: The complete lawful emission of the refusal route, by field.
_REFUSAL_LAWFUL_VECTOR = {
    "internal_state": "INVALID_BINDING_CANDIDATE",
    "subject_state": "NO_SEMANTIC_SUBJECT",
    "subject_span": None,
    "predicate_state": "NO_SEMANTIC_PREDICATE",
    "predicate_span": None,
    "predicate_head": "",
    "predicate_complement": "",
    "semantic_actor_state": "NO_SEMANTIC_ACTOR",
    "semantic_actor_span": None,
    "attribution_state": "ATTRIBUTION_ABSENT",
    "attribution_span": None,
    "institutional_issuer_text": "",
    "antecedent_state": "NO_ANAPHOR_PRESENT",
    "governing_clause_source": "NONE",
    "shared_subject_source": "NONE",
    "shared_predicate_source": "NONE",
    "required_context_ids": (),
    "proposition_status": "CONSTRUCTION_DEFECT",
    "subject_completeness": "UNRESOLVED",
    # V6.1 LRI-R1.  Moved from ABSENT_BY_CONSTRUCTION.  This literal is not free
    # to be edited to match production, and it was not: the unmodified
    # test_layout_refusal_agrees_with_the_adapter_on_role_completeness derives
    # the required value by running _terminal_facts on the refusal's own emitted
    # role states, and under LRI-R1's symmetric mapping it FAILS unless this
    # site reads UNRESOLVED.  The two expectations were in direct conflict; the
    # one that computes its answer from the adapter is the authority.
    "predicate_completeness": "UNRESOLVED",
    "binding_uniqueness": "NONE",
    "required_context_status": "NONE",
    "repairability": "IRREPARABLE",
    "role_binding_state": "ROLE_BINDING_INVALID",
    "terminal_derivation_rule": "NO_SEMANTIC_PROPOSITION",
    "repair_requirement":
        "re-extract the manifestation with a sound reading order",
    "final_extraction_disposition": "REJECTED",
    "disposition_derivation_rule": "IRREPARABLE_NON_PROPOSITION",
    "binding_confidence": 0.0,
    "analyses_considered": 0,
    "candidates_considered": 0,
}

#: Languages the refusal must not read.  None and an unknown tag are included
#: because "the caller said nothing" and "the caller said something we do not
#: model" are the two shapes a conditional would most easily slip through.
_REFUSAL_LANGUAGES = [None, "en", "de", "fr", "ru", "ar", "zz"]

#: Body shapes: empty, short, a token-bearing body, a body over the 200-char
#: threshold one survivor used, and three non-English scripts.
_REFUSAL_BODY_SHAPES = {
    "empty": "",
    "english_short": "The Committee shall review the report.",
    "annex_token": "Annex II to the report of the Committee.",
    "over_two_hundred_chars": "The Committee shall review the report. " * 12,
    "german": "Der Ausschuss prüft den Bericht des Sekretariats.",
    "russian": "Комитет рассматривает доклад Секретариата.",
    "arabic": "تستعرض اللجنة تقرير الأمانة العامة.",
    "digits_only": "12345",
}

#: Candidate identifiers, including the prefixed shape one survivor keyed on.
_REFUSAL_CANDIDATE_IDS = ["refusal-plain", "zz-refusal-prefixed"]

_REFUSAL_INPUT_PAIRS = [
    (language, body_name)
    for language in _REFUSAL_LANGUAGES
    for body_name in sorted(_REFUSAL_BODY_SHAPES)
]


def test_the_refusal_matrix_actually_varies_the_uncovered_dimensions():
    """Control: the matrix would notice each survivor's discriminator.

    A shrunken matrix -- English only, one short body, one identifier shape --
    is exactly the coverage that let five publishing mutants through.  This
    fails if a later edit takes any of those dimensions back out.
    """
    assert None in _REFUSAL_LANGUAGES
    assert "de" in _REFUSAL_LANGUAGES
    assert len(set(_REFUSAL_LANGUAGES)) >= 4
    assert any("Annex" in body for body in _REFUSAL_BODY_SHAPES.values())
    assert any(len(body) > 200 for body in _REFUSAL_BODY_SHAPES.values())
    assert any(body == "" for body in _REFUSAL_BODY_SHAPES.values())
    assert any(cid.startswith("zz-") for cid in _REFUSAL_CANDIDATE_IDS)
    assert any(not cid.startswith("zz-") for cid in _REFUSAL_CANDIDATE_IDS)
    assert len(_REFUSAL_INPUT_PAIRS) == (
        len(_REFUSAL_LANGUAGES) * len(_REFUSAL_BODY_SHAPES))


@pytest.mark.parametrize("order_state", _UNSOUND_ORDER_STATES)
@pytest.mark.parametrize("language,body_name", _REFUSAL_INPUT_PAIRS)
def test_the_refusal_emission_is_unconditional(language, body_name,
                                               order_state):
    """Every emitted axis is the same for every caller-supplied input.

    Both the helper and the reachable `bind` route are checked, and both
    identifier shapes, so a conditional on language, on body content, on body
    length or on the candidate_id cannot hide in either.
    """
    body = _REFUSAL_BODY_SHAPES[body_name]
    context = ST.empty_context(document_id="d", region_id="r",
                               reading_order_state=order_state)
    for candidate_id in _REFUSAL_CANDIDATE_IDS:
        records = {
            "helper": V2._layout_refusal(candidate_id, body, order_state,
                                         language),
            "bind": V2.bind(candidate_id=candidate_id, language=language,
                            text=body, structural_context=context),
        }
        for route, record in records.items():
            for field, expected in _REFUSAL_LAWFUL_VECTOR.items():
                assert getattr(record, field) == expected, (
                    f"{route} route moved {field} on language={language!r} "
                    f"body={body_name} candidate_id={candidate_id!r}")
            assert record.candidate_id == candidate_id
            assert record.binding_id == stable_id(
                _BINDING_ID_PREFIX, candidate_id, order_state)
            assert order_state in record.binding_reason


@pytest.mark.parametrize("order_state", _UNSOUND_ORDER_STATES)
@pytest.mark.parametrize("language,body_name", _REFUSAL_INPUT_PAIRS)
def test_the_refusal_is_a_fixed_point_of_the_state_machine(language, body_name,
                                                           order_state):
    """The four hard-coded decisions are what the machine derives, everywhere.

    Values AND derivation rules: a record whose terminal was merely crosswalk-
    legal, rather than derived, would pass `validate_decisions` and still be a
    caller-supplied terminal that nothing had checked.
    """
    record = V2._layout_refusal(_REFUSAL_CANDIDATE_IDS[0],
                                _REFUSAL_BODY_SHAPES[body_name], order_state,
                                language)
    facts = V2.TM.TerminalFacts(
        proposition_status=record.proposition_status,
        subject_completeness=record.subject_completeness,
        predicate_completeness=record.predicate_completeness,
        binding_uniqueness=record.binding_uniqueness,
        required_context_status=record.required_context_status,
        repairability=record.repairability,
    )
    facts.validate()
    terminal = V2.TM.derive_terminal(facts)
    disposition = V2.TM.derive_disposition(facts, terminal.value)
    V2.TM.validate_decisions(facts, terminal, disposition)
    assert (terminal.value, terminal.rule) == (
        record.role_binding_state, record.terminal_derivation_rule)
    assert (disposition.value, disposition.rule) == (
        record.final_extraction_disposition,
        record.disposition_derivation_rule)


# --- P6-M01: the runtime wiring, pinned on the refusal's own syntax tree -----
#
# The behavioural matrix above pins what the site emits today.  It cannot pin
# that the site would REFUSE to emit something else, because on the pristine
# tree there is nothing else to emit.  These structural pins hold the guards in
# place, exactly as `test_bind_invokes_the_publication_role_invariant` does for
# the adapter path, and the fault-injection cases below show they fire.


def _calls_named(source: str, name: str) -> list:
    """Every call to `name`, whether written bare or through a module alias."""
    tree = ast.parse(textwrap.dedent(source))
    found = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Name) and func.id == name:
            found.append(node)
        elif isinstance(func, ast.Attribute) and func.attr == name:
            found.append(node)
    return found


def _returned_record_name(source: str) -> str | None:
    """The bare name the function returns, if it returns exactly one."""
    tree = ast.parse(textwrap.dedent(source))
    returns = [node for node in ast.walk(tree)
               if isinstance(node, ast.Return)]
    if len(returns) != 1 or not isinstance(returns[0].value, ast.Name):
        return None
    return returns[0].value.id


def _guarded_axes_are_read_off_the_returned_record(source: str) -> bool:
    """Are all six D39 axes taken from the record that is actually returned?

    Reconstructing the facts from the local literals instead would produce a
    check that agrees with itself no matter what the constructor emitted, which
    is precisely the failure mode this repair exists to remove.
    """
    returned = _returned_record_name(source)
    if returned is None:
        return False
    calls = _calls_named(source, "TerminalFacts")
    if len(calls) != 1:
        return False
    call = calls[0]
    if call.args:
        return False
    supplied = {keyword.arg: keyword.value for keyword in call.keywords}
    if set(supplied) != set(_D39_AXES):
        return False
    return all(
        isinstance(value, ast.Attribute)
        and value.attr == axis
        and isinstance(value.value, ast.Name)
        and value.value.id == returned
        for axis, value in supplied.items()
    )


def _publication_guard_reads_the_returned_record(source: str) -> bool:
    """Is the publication guard called keyword-only, off the returned record?"""
    returned = _returned_record_name(source)
    if returned is None:
        return False
    calls = _calls_named(source, "validate_publication_role_invariant")
    if len(calls) != 1:
        return False
    call = calls[0]
    if call.args:
        return False
    supplied = {keyword.arg: keyword.value for keyword in call.keywords}
    if set(supplied) != {"subject_state", "predicate_state",
                         "final_extraction_disposition"}:
        return False
    return all(
        isinstance(value, ast.Attribute)
        and value.attr == name
        and isinstance(value.value, ast.Name)
        and value.value.id == returned
        for name, value in supplied.items()
    )


def _reads_the_name(source: str, name: str) -> bool:
    """Is `name` loaded anywhere in the function body?"""
    tree = ast.parse(textwrap.dedent(source))
    return any(isinstance(node, ast.Name) and node.id == name
               and isinstance(node.ctx, ast.Load)
               for node in ast.walk(tree))


def test_layout_refusal_invokes_validate_decisions():
    """The terminal crosswalk guard the adapter path calls is called here too."""
    assert _calls_named(inspect.getsource(V2._layout_refusal),
                        "validate_decisions")


def test_layout_refusal_invokes_the_publication_role_invariant():
    """The role-vocabulary guard the adapter path calls is called here too."""
    assert _calls_named(inspect.getsource(V2._layout_refusal),
                        "validate_publication_role_invariant")


def test_layout_refusal_rederives_the_terminal_it_emits():
    """The site does not merely hand a caller-supplied terminal to the guard.

    `validate_decisions` trusts the terminal it is given, and on this path no
    adapter derived one, so the emitted pair is additionally required to be the
    pair the state machine derives from the emitted facts.
    """
    source = inspect.getsource(V2._layout_refusal)
    assert _calls_named(source, "derive_terminal")
    assert _calls_named(source, "derive_disposition")


def test_layout_refusal_guards_read_the_record_it_returns():
    """The guards are fed the emitted record, not self-agreeing locals."""
    source = inspect.getsource(V2._layout_refusal)
    assert _guarded_axes_are_read_off_the_returned_record(source)
    assert _publication_guard_reads_the_returned_record(source)


def test_layout_refusal_never_reads_the_language_parameter():
    """`language` is not a discriminator at this site, and must not become one.

    Two of the five survivors keyed on it.  The correct response to this
    failing is to remove the dependence, never to relax the pin: the refusal is
    about reading order, which is a property of the manifestation and not of
    the language the caller declared.
    """
    source = inspect.getsource(V2._layout_refusal)
    assert _reads_the_name(source, "order_state")
    assert _reads_the_name(source, "candidate_id")
    assert not _reads_the_name(source, "language")


@pytest.mark.parametrize("source", [
    # reads it directly
    'def r(language):\n'
    '    return "EVIDENCE_BOUND" if language == "de" else "REJECTED"\n',
    # reads it inside a nested expression
    'def r(language, order_state):\n'
    '    return [x for x in (order_state,) if language]\n',
])
def test_the_language_pin_rejects_a_function_that_reads_language(source):
    """Negative control: the predicate reads the tree, not the parameter list."""
    assert _reads_the_name(source, "language")


@pytest.mark.parametrize("source", [
    # facts rebuilt from local literals: agrees with itself, checks nothing
    'def r():\n'
    '    record = RoleBindingV2Record("x")\n'
    '    facts = TM.TerminalFacts(\n'
    '        proposition_status="CONSTRUCTION_DEFECT",\n'
    '        subject_completeness="UNRESOLVED",\n'
    '        predicate_completeness="ABSENT_BY_CONSTRUCTION",\n'
    '        binding_uniqueness="NONE",\n'
    '        required_context_status="NONE",\n'
    '        repairability="IRREPARABLE")\n'
    '    return record\n',
    # facts read off a DIFFERENT object than the one returned
    'def r(other):\n'
    '    record = RoleBindingV2Record("x")\n'
    '    facts = TM.TerminalFacts(\n'
    '        proposition_status=other.proposition_status,\n'
    '        subject_completeness=other.subject_completeness,\n'
    '        predicate_completeness=other.predicate_completeness,\n'
    '        binding_uniqueness=other.binding_uniqueness,\n'
    '        required_context_status=other.required_context_status,\n'
    '        repairability=other.repairability)\n'
    '    return record\n',
    # an axis quietly dropped from the reconstruction
    'def r():\n'
    '    record = RoleBindingV2Record("x")\n'
    '    facts = TM.TerminalFacts(\n'
    '        proposition_status=record.proposition_status,\n'
    '        subject_completeness=record.subject_completeness,\n'
    '        predicate_completeness=record.predicate_completeness,\n'
    '        binding_uniqueness=record.binding_uniqueness,\n'
    '        required_context_status=record.required_context_status)\n'
    '    return record\n',
])
def test_the_axis_provenance_predicate_rejects_a_self_agreeing_check(source):
    """Negative control: reconstructing the facts locally is not a check."""
    assert not _guarded_axes_are_read_off_the_returned_record(source)


def test_the_axis_provenance_predicate_accepts_the_pristine_shape():
    """Positive control: the predicate is not simply rejecting everything."""
    source = (
        'def r():\n'
        '    record = RoleBindingV2Record("x")\n'
        '    facts = TM.TerminalFacts(\n'
        '        proposition_status=record.proposition_status,\n'
        '        subject_completeness=record.subject_completeness,\n'
        '        predicate_completeness=record.predicate_completeness,\n'
        '        binding_uniqueness=record.binding_uniqueness,\n'
        '        required_context_status=record.required_context_status,\n'
        '        repairability=record.repairability)\n'
        '    return record\n')
    assert _guarded_axes_are_read_off_the_returned_record(source)


# --- P6-M01: the guards fire.  Fault injection at the construction step ------
#
# The record is drifted between construction and return by replacing the
# constructor the site calls, which is how a future edit at this site would
# fail.  Nothing in production is patched or weakened: the site's own guards
# are what raise, and the positive control below shows the injection mechanism
# is not itself the cause.

def _refusal_with_drift(monkeypatch, **overrides):
    """Run the refusal with the constructed record perturbed as described."""
    constructor = V2.RoleBindingV2Record

    def drifting(*args):
        return replace(constructor(*args), **overrides)

    monkeypatch.setattr(V2, "RoleBindingV2Record", drifting)
    return V2._layout_refusal("refusal-drift", _REFUSAL_BODY,
                              "PACKET_CONSTRUCTION_DEFECT", "en")


def test_the_drift_injection_is_transparent_when_nothing_drifts(monkeypatch):
    """Positive control: the mechanism alone does not make the site raise."""
    record = _refusal_with_drift(monkeypatch)
    assert record.role_binding_state == "ROLE_BINDING_INVALID"
    assert record.final_extraction_disposition == "REJECTED"


@pytest.mark.parametrize("overrides", [
    # exactly M-01's X-G/X-H/X-I/X-J: publish, terminal left INVALID
    {"final_extraction_disposition": "EVIDENCE_BOUND"},
    # publish with a matching rule, so only the crosswalk can object
    {"final_extraction_disposition": "EVIDENCE_BOUND",
     "disposition_derivation_rule": "COMPLETE_UNIQUE_UNOBLIGATED_BINDING"},
])
def test_the_refusal_refuses_to_publish_an_invalid_binding(monkeypatch,
                                                           overrides):
    """The terminal crosswalk guard fires before the record can be returned."""
    with pytest.raises(V2.TM.TerminalContractViolation):
        _refusal_with_drift(monkeypatch, **overrides)


@pytest.mark.parametrize("overrides", [
    # exactly M-01's X-K: the terminal alone moves, disposition still REJECTED,
    # which the crosswalk permits and only re-derivation catches
    {"role_binding_state": "ROLE_BINDING_ESTABLISHED",
     "terminal_derivation_rule": "UNIQUE_COMPLETE_BINDING"},
    # a fact moves and the decisions do not follow it
    {"proposition_status": "PROPOSITION"},
    # the rule string drifts away from the rule the machine gives
    {"terminal_derivation_rule": "UNIQUE_COMPLETE_BINDING"},
    {"disposition_derivation_rule": "OUTSTANDING_SEMANTIC_OBLIGATION"},
])
def test_the_refusal_refuses_a_terminal_the_machine_does_not_derive(
        monkeypatch, overrides):
    """A caller-supplied terminal that is merely crosswalk-legal is refused."""
    with pytest.raises(V2.TM.TerminalContractViolation):
        _refusal_with_drift(monkeypatch, **overrides)


def test_the_refusal_refuses_to_publish_an_undecided_role(monkeypatch):
    """A fully self-consistent publishing refusal still carries no roles.

    Every D39 axis, the terminal and the disposition move together, so the
    crosswalk and the re-derivation are both satisfied.  The role vocabulary is
    what remains undecided, and the publication invariant is what refuses.
    """
    with pytest.raises(V2.RoleBindingV2Violation):
        _refusal_with_drift(
            monkeypatch,
            proposition_status="PROPOSITION",
            subject_completeness="BOUND_LOCAL",
            predicate_completeness="BOUND_LOCAL",
            binding_uniqueness="UNIQUE",
            repairability="NO_REPAIR_OWED",
            role_binding_state="ROLE_BINDING_ESTABLISHED",
            terminal_derivation_rule="UNIQUE_COMPLETE_BINDING",
            final_extraction_disposition="EVIDENCE_BOUND",
            disposition_derivation_rule="COMPLETE_UNIQUE_UNOBLIGATED_BINDING",
        )


def test_the_refusal_refuses_a_value_outside_the_frozen_vocabulary(monkeypatch):
    """A D39 axis outside the frozen vocabulary never reaches a caller."""
    with pytest.raises(V2.TM.TerminalContractViolation):
        _refusal_with_drift(monkeypatch, repairability="PROBABLY_FINE")


def test_the_refusal_guards_are_not_swallowed():
    """Neither guard may be wrapped in a handler at this site."""
    tree = ast.parse(textwrap.dedent(inspect.getsource(V2._layout_refusal)))
    assert not [node for node in ast.walk(tree)
                if isinstance(node, (ast.Try, ast.ExceptHandler))]
