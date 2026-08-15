"""Semantic invariants, deterministic admissibility and grouped calibration.

Each test names the V5.3 failure it closes.  V5.3 learned a direct map
``features -> terminal reviewer class``, scored 0.8494 on development and
0.4348 on clean held-out evidence, and wrongly admitted seven spans.  The
tests below are organised around the three claims of the repair:

1. every one of the twenty-one invariants is an individually testable
   predicate over real document text (German and English), not a weight;
2. the terminal state is a pure function of the invariant vector, driven by
   an inspectable material-versus-recoverable table;
3. a high learned score cannot rescue any of the eleven forbidden overrides,
   because invariant-invalid candidates are removed from the ranking
   population before any weight is applied.

Test data only.  Nothing here is a production input.
"""
from __future__ import annotations

import dataclasses

import pytest

from conftest_v5_2_helpers import candidate_over
from curunir_operational.v5_3 import ranking as RK
from curunir_operational.v5_4 import calibration as CAL
from curunir_operational.v5_4 import invariants as INV

pytestmark = pytest.mark.no_db

SAT = INV.SATISFIED
VIO = INV.VIOLATED
UNR = INV.UNRESOLVED
NA = INV.NOT_APPLICABLE


# ---------------------------------------------------------------------------
# Fixtures over real-ish document text
# ---------------------------------------------------------------------------

def context(text, needle, **kwargs):
    """Build an invariant context over ``needle`` inside ``text``."""
    layout = kwargs.pop("layout", None)
    document, candidate = candidate_over(text, needle, **kwargs)
    return INV.build_context(candidate, document, layout)


def states(text, needle, **kwargs):
    return INV.compute_invariants(context(text, needle, **kwargs)).states


def assess(text, needle, **kwargs):
    layout = kwargs.pop("layout", None)
    document, candidate = candidate_over(text, needle, **kwargs)
    return INV.assess_candidate(candidate, document, layout)


def perturb(ctx, **interpretation_fields):
    """Replace fields on the selected interpretation of a real lattice.

    A handful of invariants guard against a boundary defect the V5.2 lattice
    scorer will not select on purpose — losing a lifecycle reading outright,
    or admitting a non-role-bearing boundary.  The guard still has to hold,
    so it is tested against a real document, a real candidate and a real
    interpretation record whose one relevant field is perturbed.
    """
    selected = dataclasses.replace(ctx.selected, **interpretation_fields)
    return dataclasses.replace(ctx, selected=selected, widened=True)


@dataclasses.dataclass(frozen=True)
class LayoutStub:
    """The minimal shape ``_quarantined_regions`` reads."""

    regions: tuple


CLEAN_EN = ("The Council adopted the regulation on 16 January 2026.\n")
CLEAN_DE = ("Das Bundeskriminalamt hat die Analyseplattform beschafft.\n")


def greedy_model():
    """A ranker that wants to accept absolutely everything.

    This is the adversary the V5.4 architecture has to survive: not a badly
    tuned model, but the worst model expressible in the V5.3 weight space.
    """
    model = RK.RankingModel()
    for name in RK.FEATURE_NAMES:
        model.weights["ACCEPTED_CANDIDATE"][name] = 1000.0
        for outcome in RK.OUTCOMES:
            if outcome != "ACCEPTED_CANDIDATE":
                model.weights[outcome][name] = -1000.0
    model.bias["ACCEPTED_CANDIDATE"] = 1000.0
    return model


# ---------------------------------------------------------------------------
# One focused test per invariant: a candidate that satisfies it, one that does not
# ---------------------------------------------------------------------------

def test_proposition_complete():
    satisfied = states(CLEAN_EN, CLEAN_EN.strip())
    assert satisfied["proposition_complete"] == SAT
    violated = states("A detailed description of the measures put in place.\n",
                      "A detailed description of the measures put in place.")
    assert violated["proposition_complete"] == VIO
    # Recoverable shape: the predicate lives in the enclosing block.
    unresolved = states(
        "Capabilities\nCentral case handling\nBiometric search\n"
        "The agency operates the register.\n",
        "Central case handling")
    assert unresolved["proposition_complete"] == UNR


def test_referential_subject_complete():
    satisfied = states(CLEAN_DE, CLEAN_DE.strip(), language="de")
    assert satisfied["referential_subject_complete"] == SAT
    # V5.1 accepted "Damit" as the subject of a claim.
    violated = states("Damit wird die Software bundesweit eingesetzt.\n",
                      "Damit wird die Software bundesweit eingesetzt.",
                      language="de")
    assert violated["referential_subject_complete"] == VIO
    unresolved = states(
        "Das Bundeskriminalamt beschaffte die Analyseplattform. "
        "Damit wird die Plattform bundesweit betrieben.\n",
        "Damit wird die Plattform bundesweit betrieben.", language="de")
    assert unresolved["referential_subject_complete"] == UNR


def test_predicate_complete():
    satisfied = states(CLEAN_EN, CLEAN_EN.strip())
    assert satisfied["predicate_complete"] == SAT
    violated = states("A detailed description of the measures put in place.\n",
                      "A detailed description of the measures put in place.")
    assert violated["predicate_complete"] == VIO
    # A predicate supplied by a table header is not in the recorded evidence.
    unresolved = states("Betreiber              Status\n"
                        "Landeskriminalamt      Pilotbetrieb\n",
                        "Landeskriminalamt      Pilotbetrieb", language="de")
    assert unresolved["predicate_complete"] == UNR


def test_object_or_value_complete():
    satisfied = states(CLEAN_EN, CLEAN_EN.strip())
    assert satisfied["object_or_value_complete"] == SAT
    violated = states("Hat diese Stoerung Auswirkungen gehabt?\n",
                      "Hat diese Stoerung Auswirkungen gehabt?")
    assert violated["object_or_value_complete"] == VIO
    unresolved = states("Abbildung 3: Verteilung der Standorte\n"
                        "Die Behoerde erfasst.\n",
                        "Die Behoerde erfasst.", language="de")
    assert unresolved["object_or_value_complete"] == UNR


def test_attribution_complete():
    satisfied = states('The minister said: "The system is already operational."\n',
                       '"The system is already operational."',
                       candidate_type="QUOTATION")
    assert satisfied["attribution_complete"] == SAT
    violated = states('Nothing precedes it here.\n'
                      '"The system is already operational."\n',
                      '"The system is already operational."',
                      candidate_type="QUOTATION")
    assert violated["attribution_complete"] == VIO


def test_polarity_preserved():
    satisfied = states(CLEAN_DE, CLEAN_DE.strip(), language="de")
    assert satisfied["polarity_preserved"] == SAT
    # The recorded span reads positive; the boundary the lattice selects reads
    # the negation the span was cut away from.
    violated = states("Die Landespolizei hat die Software nicht beschafft.\n",
                      "Die Landespolizei hat die Software", language="de")
    assert violated["polarity_preserved"] == VIO


def test_modality_preserved():
    satisfied = states(CLEAN_EN, CLEAN_EN.strip())
    assert satisfied["modality_preserved"] == SAT
    # The recorded span hedges with PLANNED; the wider boundary reads EXERCISED.
    violated = states(
        "The exercise showed that the agency plans to deploy the platform.\n",
        "the agency plans to deploy the platform")
    assert violated["modality_preserved"] == VIO


def test_lifecycle_state_preserved():
    satisfied = states(CLEAN_DE, CLEAN_DE.strip(), language="de")
    assert satisfied["lifecycle_state_preserved"] == SAT
    ctx = context("The registry operates in Hamburg since March 2025.\n",
                  "The registry operates in Hamburg since March 2025.")
    assert ctx.seed.lifecycle_state == "OPERATIONAL"
    lost = INV.compute_invariants(perturb(ctx, lifecycle_state="UNKNOWN"))
    assert lost.states["lifecycle_state_preserved"] == VIO


def test_temporal_scope_complete():
    satisfied = states("The pilot started on 16 January 2026 in Hamburg.\n",
                       "16 January 2026", candidate_type="DATE")
    assert satisfied["temporal_scope_complete"] == SAT
    violated = states("The pilot started in the spring of that cycle.\n",
                      "the spring", candidate_type="DATE")
    assert violated["temporal_scope_complete"] == VIO
    # An asserted calendar scope may not rest on an approximate mapping.
    unresolved = states("The pilot started on 3 March 2025 in Hamburg.\n",
                        "The pilot started on 3 March 2025 in Hamburg.",
                        mapping_precision="APPROXIMATE_PAGE")
    assert unresolved["temporal_scope_complete"] == UNR


def test_geographic_scope_complete():
    satisfied = states("The registry operates in Hamburg since March 2025.\n",
                       "The registry operates in Hamburg since March 2025.")
    assert satisfied["geographic_scope_complete"] == SAT
    violated = states("Bundesweit gilt die neue Regel.\n", "Bundesweit",
                      language="de", candidate_type="GEOGRAPHIC_SCOPE")
    assert violated["geographic_scope_complete"] == VIO


def test_unit_and_quantity_complete():
    satisfied = states("The centre delivers 250 MW of capacity.\n", "250 MW",
                       candidate_type="NUMERIC_VALUE")
    assert satisfied["unit_and_quantity_complete"] == SAT
    violated = states("The registry holds 4200 records.\n", "4200",
                      candidate_type="NUMERIC_VALUE")
    assert violated["unit_and_quantity_complete"] == VIO


def test_discourse_assertive():
    satisfied = states(CLEAN_EN, CLEAN_EN.strip())
    assert satisfied["discourse_assertive"] == SAT
    question = states("Hat diese Stoerung Auswirkungen gehabt?\n",
                      "Hat diese Stoerung Auswirkungen gehabt?")
    assert question["discourse_assertive"] == VIO
    citation_text = ("( 8 ) Decision No 768/2008/EC of the European Parliament "
                     "of 9 July 2008.\n")
    citation = states(citation_text, citation_text.strip())
    assert citation["discourse_assertive"] == VIO


def test_layout_mapping_defensible():
    satisfied = states(CLEAN_EN, CLEAN_EN.strip())
    assert satisfied["layout_mapping_defensible"] == SAT
    text = "The programme office published a note.\nWe use cookies on this site.\n"
    layout = LayoutStub(regions=((0, 40, "PAGE_HEADER", "QUARANTINED"),))
    violated = states(text, "The programme office published a note.", layout=layout)
    assert violated["layout_mapping_defensible"] == VIO
    unresolved = states("The centre delivers 250 MW of capacity.\n", "250 MW",
                        candidate_type="NUMERIC_VALUE",
                        mapping_precision="APPROXIMATE_PAGE")
    assert unresolved["layout_mapping_defensible"] == UNR


def test_context_dependency_resolved():
    satisfied = states(CLEAN_DE, CLEAN_DE.strip(), language="de")
    assert satisfied["context_dependency_resolved"] == SAT
    # Five of the eleven V5.2-era wrong admissions had exactly this shape.
    violated = states("Damit wird die Software bundesweit eingesetzt.\n",
                      "Damit wird die Software bundesweit eingesetzt.",
                      language="de")
    assert violated["context_dependency_resolved"] == VIO
    unresolved = states(
        "Das Bundeskriminalamt beschaffte die Analyseplattform. "
        "Damit wird die Plattform bundesweit betrieben.\n",
        "Damit wird die Plattform bundesweit betrieben.", language="de")
    assert unresolved["context_dependency_resolved"] == UNR


def test_no_structural_chrome():
    satisfied = states(CLEAN_EN, CLEAN_EN.strip())
    assert satisfied["no_structural_chrome"] == SAT
    violated = states("We use cookies to improve your browsing experience.\n",
                      "We use cookies to improve your browsing experience.")
    assert violated["no_structural_chrome"] == VIO


def test_no_cross_column_contamination():
    # A column gap bound by a table header is a licensed structured reading.
    satisfied = states("Betreiber              Status\n"
                       "Landeskriminalamt      Pilotbetrieb\n",
                       "Landeskriminalamt      Pilotbetrieb", language="de")
    assert satisfied["no_cross_column_contamination"] == SAT
    violated = states(
        "Bavaria produced the report        Saxony rejected the proposal\n",
        "Bavaria produced the report        Saxony rejected the proposal")
    assert violated["no_cross_column_contamination"] == VIO
    widened = states("Ort            Status\n"
                     "Die Software wurde beschafft        Der Betrieb laeuft\n",
                     "Die Software wurde beschafft", language="de")
    assert widened["no_cross_column_contamination"] == VIO


def test_no_unrelated_proposition_contamination():
    satisfied = states("Die Landespolizei hat die Software nicht beschafft.\n",
                       "Die Landespolizei hat die Software", language="de")
    assert satisfied["no_unrelated_proposition_contamination"] == SAT
    unresolved = states("The agency will provide the following:\n"
                        "1. a national biometric register\n"
                        "2. a shared case management system\n",
                        "a national biometric register")
    assert unresolved["no_unrelated_proposition_contamination"] == UNR
    # A whole-sentence seed widened to a non-role-bearing boundary can only
    # have imported a neighbouring assertion.
    ctx = context(CLEAN_EN, CLEAN_EN.strip())
    violated = INV.compute_invariants(perturb(ctx, role_bearing=False)).states
    assert violated["no_unrelated_proposition_contamination"] == VIO


def test_no_attribution_shift():
    satisfied = states(CLEAN_EN, CLEAN_EN.strip())
    assert satisfied["no_attribution_shift"] in {SAT, NA}
    violated = states(
        "According to the report, the ministry said the platform is certified.\n",
        "the ministry said the platform is certified")
    assert violated["no_attribution_shift"] == VIO


def test_no_actor_shift():
    satisfied = states("Die Landespolizei hat die Software nicht beschafft.\n",
                       "Die Landespolizei hat die Software", language="de")
    assert satisfied["no_actor_shift"] == SAT
    violated = states(
        "Die Bundespolizei bestaetigte, dass das Landesamt die Software "
        "beschafft hat.\n",
        "das Landesamt die Software beschafft hat", language="de")
    assert violated["no_actor_shift"] == VIO


def test_no_scope_strengthening():
    satisfied = states("The registry operates in Hamburg since March 2025.\n",
                       "The registry operates in Hamburg since March 2025.")
    assert satisfied["no_scope_strengthening"] in {SAT, NA}
    violated = states(
        "The ministry in Berlin confirmed that the police in Hamburg use the "
        "platform.\n",
        "the police in Hamburg use the platform")
    assert violated["no_scope_strengthening"] == VIO


def test_no_lifecycle_strengthening():
    satisfied = states("The registry operates in Hamburg since March 2025.\n",
                       "The registry operates in Hamburg since March 2025.")
    assert satisfied["no_lifecycle_strengthening"] in {SAT, NA}
    # DEPLOYED in the recorded span read as OPERATIONAL by the wider boundary.
    violated = states(
        "The platform is in operation after the agency rolled out the new "
        "modules.\n",
        "the agency rolled out the new modules")
    assert violated["no_lifecycle_strengthening"] == VIO


# ---------------------------------------------------------------------------
# The vector and the material-versus-recoverable table
# ---------------------------------------------------------------------------

def test_the_vector_carries_all_twenty_one_invariants():
    vector = INV.compute_invariants(context(CLEAN_EN, CLEAN_EN.strip()))
    assert len(INV.INVARIANT_NAMES) == 21
    assert tuple(vector.states) == INV.INVARIANT_NAMES
    assert set(vector.findings) == set(INV.INVARIANT_NAMES)
    assert set(vector.states.values()) <= set(INV.INVARIANT_STATES)


def test_the_materiality_table_is_a_total_inspectable_partition():
    assert set(INV.MATERIALITY) == set(INV.INVARIANT_NAMES)
    assert set(INV.MATERIAL_INVARIANTS) | set(INV.RECOVERABLE_INVARIANTS) == \
        set(INV.INVARIANT_NAMES)
    assert not set(INV.MATERIAL_INVARIANTS) & set(INV.RECOVERABLE_INVARIANTS)
    assert tuple(INV.INVARIANT_PREDICATES) == INV.INVARIANT_NAMES


def clean_vector(**overrides):
    vector = {name: SAT for name in INV.INVARIANT_NAMES}
    vector.update(overrides)
    return vector


@pytest.mark.parametrize("invariant", INV.MATERIAL_INVARIANTS)
def test_a_material_violation_forces_rejection(invariant):
    state, rule = INV.derive_terminal_state(clean_vector(**{invariant: VIO}))
    assert state == "REJECTED"
    assert rule == "MATERIAL_INVARIANT_VIOLATED"


@pytest.mark.parametrize("invariant", INV.RECOVERABLE_INVARIANTS)
def test_a_recoverable_violation_never_rejects(invariant):
    state, _rule = INV.derive_terminal_state(clean_vector(**{invariant: VIO}))
    assert state != "REJECTED"
    assert state in {"SEMANTICALLY_PARSED", "EVIDENCE_BOUND"}


@pytest.mark.parametrize("invariant", INV.INVARIANT_NAMES)
def test_an_unresolved_invariant_routes_to_quarantine(invariant):
    state, rule = INV.derive_terminal_state(clean_vector(**{invariant: UNR}))
    assert state == "QUARANTINED"
    assert rule == "INVARIANT_UNRESOLVED_FROM_AVAILABLE_CONTEXT"


def test_all_satisfied_accepts():
    state, rule = INV.derive_terminal_state(clean_vector())
    assert state == "ACCEPTED_CANDIDATE"
    assert rule == "ALL_MATERIAL_INVARIANTS_SATISFIED"


def test_not_applicable_is_vacuous_satisfaction():
    vector = {name: NA for name in INV.INVARIANT_NAMES}
    assert INV.derive_terminal_state(vector)[0] == "ACCEPTED_CANDIDATE"


def test_a_lost_context_dependency_is_semantically_parsed_not_rejected():
    state, rule = INV.derive_terminal_state(
        clean_vector(context_dependency_resolved=VIO))
    assert state == "SEMANTICALLY_PARSED"
    assert rule == "SEMANTIC_STRUCTURE_NOT_EVIDENCE_BOUND"


@pytest.mark.parametrize("invariant", INV.COMPLETION_INVARIANTS)
def test_a_completion_defect_is_evidence_bound(invariant):
    state, rule = INV.derive_terminal_state(clean_vector(**{invariant: VIO}))
    assert state == "EVIDENCE_BOUND"
    assert rule == "NON_CRITICAL_COMPLETION_REQUIRED"


def test_a_material_violation_outranks_an_unresolved_invariant():
    state, _rule = INV.derive_terminal_state(
        clean_vector(no_structural_chrome=VIO, predicate_complete=UNR))
    assert state == "REJECTED"


def test_derivation_is_pure_and_carries_no_learned_weight():
    vector = clean_vector(object_or_value_complete=VIO)
    assert INV.derive_terminal_state(vector) == INV.derive_terminal_state(vector)
    assert INV.derive_terminal_state(dict(vector))[0] == "EVIDENCE_BOUND"


def test_an_incomplete_vector_cannot_produce_a_state():
    partial = {name: SAT for name in INV.INVARIANT_NAMES[:5]}
    with pytest.raises(ValueError):
        INV.derive_terminal_state(partial)


def test_the_terminal_vocabulary_matches_the_v5_3_outcomes():
    assert set(INV.TERMINAL_STATES) == set(RK.OUTCOMES)


# ---------------------------------------------------------------------------
# The core V5.3 regression: no learned score may rescue a forbidden override
# ---------------------------------------------------------------------------

OVERRIDE_FIXTURES = {
    "LOST_POLARITY": (
        "Die Landespolizei hat die Software nicht beschafft.\n",
        "Die Landespolizei hat die Software", {"language": "de"}),
    "LOST_MATERIAL_MODALITY": (
        "The exercise showed that the agency plans to deploy the platform.\n",
        "the agency plans to deploy the platform", {}),
    "LIFECYCLE_STRENGTHENING": (
        "The platform is in operation after the agency rolled out the new "
        "modules.\n",
        "the agency rolled out the new modules", {}),
    "ATTRIBUTION_SHIFT": (
        "According to the report, the ministry said the platform is certified.\n",
        "the ministry said the platform is certified", {}),
    "ACTOR_SHIFT": (
        "Die Bundespolizei bestaetigte, dass das Landesamt die Software "
        "beschafft hat.\n",
        "das Landesamt die Software beschafft hat", {"language": "de"}),
    "STRUCTURAL_CHROME": (
        "We use cookies to improve your browsing experience.\n",
        "We use cookies to improve your browsing experience.", {}),
    "NON_REFERENTIAL_FRAGMENT": (
        "Damit wird die Software bundesweit eingesetzt.\n",
        "Damit wird die Software bundesweit eingesetzt.", {"language": "de"}),
    "CROSS_COLUMN_CONTAMINATION": (
        "Bavaria produced the report        Saxony rejected the proposal\n",
        "Bavaria produced the report        Saxony rejected the proposal", {}),
    "MISSING_PREDICATE": (
        "A detailed description of the measures put in place.\n",
        "A detailed description of the measures put in place.", {}),
    "MISSING_REQUIRED_SUBJECT": (
        "Damit wird die Software bundesweit eingesetzt.\n",
        "Damit wird die Software bundesweit eingesetzt.", {"language": "de"}),
    "UNRESOLVED_MATERIAL_TEMPORAL_SCOPE": (
        "The pilot started on 3 March 2025 in Hamburg.\n",
        "The pilot started on 3 March 2025 in Hamburg.",
        {"mapping_precision": "APPROXIMATE_PAGE"}),
}


def test_every_forbidden_override_has_a_fixture():
    assert set(OVERRIDE_FIXTURES) == set(INV.FORBIDDEN_OVERRIDES)
    assert len(INV.FORBIDDEN_OVERRIDES) == 11


@pytest.mark.parametrize("override", sorted(OVERRIDE_FIXTURES))
def test_a_high_learned_score_cannot_rescue_a_forbidden_override(override):
    text, needle, kwargs = OVERRIDE_FIXTURES[override]
    document, candidate = candidate_over(text, needle, **kwargs)
    model = greedy_model()

    # The model, left to itself, wants to accept this span outright.
    ctx = INV.build_context(candidate, document)
    features = RK.extract_features(candidate, document, ctx.lattice, ctx.selected)
    scored = model.scores(features)
    assert max(scored, key=lambda outcome: scored[outcome]) == "ACCEPTED_CANDIDATE"

    assessment = INV.assess_candidate(candidate, document)
    assert override in assessment.forbidden_overrides
    assert assessment.invariant_valid is False
    assert assessment.terminal_state != "ACCEPTED_CANDIDATE"

    ranking = INV.residual_rank([candidate], document, model)
    assert ranking.scored_candidate_ids == ()
    assert ranking.excluded_candidate_ids == (candidate.candidate_id,)
    assert override in ranking.excluded[0]["forbidden_overrides"]


def test_the_scoring_helper_itself_refuses_an_invalid_assessment():
    text, needle, kwargs = OVERRIDE_FIXTURES["STRUCTURAL_CHROME"]
    document, candidate = candidate_over(text, needle, **kwargs)
    ctx = INV.build_context(candidate, document)
    assessment = INV.assess_candidate(candidate, document)
    with pytest.raises(ValueError, match="learned score may never be applied"):
        INV._residual_scores([assessment], {candidate.candidate_id: ctx},
                             greedy_model())


def test_residual_rank_scores_only_invariant_valid_survivors():
    text = (CLEAN_EN +
            "We use cookies to improve your browsing experience.\n"
            "Did the outage have any effect on the register?\n")
    document, clean = candidate_over(text, CLEAN_EN.strip())
    _doc, chrome = candidate_over(
        text, "We use cookies to improve your browsing experience.")
    _doc2, question = candidate_over(
        text, "Did the outage have any effect on the register?")
    ranking = INV.residual_rank([clean, chrome, question], document, greedy_model())

    assert ranking.considered == 3
    assert clean.candidate_id in ranking.scored_candidate_ids
    assert set(ranking.excluded_candidate_ids) == {chrome.candidate_id,
                                                  question.candidate_id}
    assert not (set(ranking.scored_candidate_ids) &
                set(ranking.excluded_candidate_ids))
    scored_states = {row["candidate_id"]: row["terminal_state"]
                     for row in ranking.ordered}
    assert scored_states[clean.candidate_id] == "ACCEPTED_CANDIDATE"


def test_residual_rank_uses_the_v5_3_model_only_to_order_survivors():
    text = (CLEAN_EN + CLEAN_DE +
            "The registry operates in Hamburg since March 2025.\n")
    document, first = candidate_over(text, CLEAN_EN.strip())
    _d, second = candidate_over(text, CLEAN_DE.strip())
    _e, third = candidate_over(
        text, "The registry operates in Hamburg since March 2025.")
    candidates = [first, second, third]

    flat = INV.residual_rank(candidates, document, RK.RankingModel())
    tilted = RK.RankingModel()
    tilted.weights["ACCEPTED_CANDIDATE"]["has_temporal_scope"] = 5.0
    leaning = INV.residual_rank(candidates, document, tilted)

    assert set(flat.scored_candidate_ids) == set(leaning.scored_candidate_ids)
    # The model may reorder survivors; it may not change any terminal state.
    assert {row["candidate_id"]: row["terminal_state"] for row in flat.ordered} == \
        {row["candidate_id"]: row["terminal_state"] for row in leaning.ordered}
    assert leaning.ranker_role.startswith("rank among invariant-valid")


def test_a_learned_score_never_appears_in_the_assessment():
    assessment = assess(CLEAN_EN, CLEAN_EN.strip())
    record = assessment.to_record()
    assert "score" not in record
    assert "weights" not in record
    assert assessment.derivation_rule in INV.DERIVATION_RULES


# ---------------------------------------------------------------------------
# End-to-end assessments
# ---------------------------------------------------------------------------

def test_a_clean_german_claim_is_accepted():
    assessment = assess(CLEAN_DE, CLEAN_DE.strip(), language="de")
    assert assessment.terminal_state == "ACCEPTED_CANDIDATE"
    assert assessment.invariant_valid is True
    assert assessment.material_violations == ()
    assert assessment.unresolved == ()


def test_assessment_is_deterministic_over_the_same_evidence():
    first = assess(CLEAN_EN, CLEAN_EN.strip())
    second = assess(CLEAN_EN, CLEAN_EN.strip())
    assert first.vector.states == second.vector.states
    assert first.terminal_state == second.terminal_state
    assert first.assessment_id == second.assessment_id


def test_assessment_report_summarises_a_population():
    text = CLEAN_EN + "We use cookies to improve your browsing experience.\n"
    document, clean = candidate_over(text, CLEAN_EN.strip())
    _d, chrome = candidate_over(
        text, "We use cookies to improve your browsing experience.")
    report = INV.assessment_report([INV.assess_candidate(clean, document),
                                    INV.assess_candidate(chrome, document)])
    assert report["assessments"] == 2
    assert report["terminal_state_counts"]["ACCEPTED_CANDIDATE"] == 1
    assert report["terminal_state_counts"]["REJECTED"] == 1
    assert report["forbidden_override_counts"]["STRUCTURAL_CHROME"] == 1
    assert report["invariant_failure_distribution"]["no_structural_chrome"][VIO] == 1


def test_an_accepted_assessment_may_not_carry_a_defect():
    vector = INV.compute_invariants(context(CLEAN_EN, CLEAN_EN.strip()))
    with pytest.raises(ValueError):
        INV.AdmissibilityAssessment(
            "id", "cand", "doc", "ACCEPTED_CANDIDATE",
            "ALL_MATERIAL_INVARIANTS_SATISFIED", vector,
            ("no_structural_chrome",), (), (), (), True, "NARROW", (0, 1),
            "why", "lattice", INV.INVARIANT_VERSION,
            "2026-07-25T00:00:00+00:00")


# ---------------------------------------------------------------------------
# Grouped calibration
# ---------------------------------------------------------------------------

def calibration_case(candidate_id, *, campaign="ALPHA", language="de",
                     genre="PRESS_RELEASE", layout="SINGLE_COLUMN",
                     family="MINISTRY", origin="V5_4",
                     evidence_class="CLEAN_HELD_OUT", index=0,
                     predicted="ACCEPTED_CANDIDATE",
                     reviewer="ACCEPTED_CANDIDATE", features=None,
                     vocabulary=()):
    return CAL.CalibrationCase(
        f"case-{candidate_id}", candidate_id, family, campaign, language,
        genre, layout, origin, evidence_class, index, predicted, reviewer,
        vector_for(predicted), dict(features or {}), tuple(vocabulary))


def vector_for(state):
    """An invariant vector the derivation maps onto ``state``."""
    mapping = {
        "ACCEPTED_CANDIDATE": {},
        "REJECTED": {"no_structural_chrome": VIO},
        "QUARANTINED": {"predicate_complete": UNR},
        "SEMANTICALLY_PARSED": {"context_dependency_resolved": VIO},
        "EVIDENCE_BOUND": {"object_or_value_complete": VIO},
    }
    return clean_vector(**mapping[state])


def test_vector_for_helper_agrees_with_the_derivation():
    for state in INV.TERMINAL_STATES:
        assert INV.derive_terminal_state(vector_for(state))[0] == state


def test_leave_one_campaign_out_reports_per_fold_spread():
    cases = (
        [calibration_case(f"a{i}", campaign="ALPHA") for i in range(4)] +
        [calibration_case(f"b{i}", campaign="BETA",
                          predicted="REJECTED" if i < 2 else "ACCEPTED_CANDIDATE",
                          reviewer="ACCEPTED_CANDIDATE") for i in range(4)])
    report = CAL.leave_one_campaign_out(cases, min_fold_size=1)
    assert report["group_key"] == "campaign"
    assert [fold["held_out"] for fold in report["folds"]] == ["ALPHA", "BETA"]
    assert report["folds"][0]["correctness"] == 1.0
    assert report["folds"][1]["correctness"] == 0.5
    assert report["spread"]["range"] == 0.5
    assert report["spread"]["stdev"] > 0.0
    assert report["stable"] is False
    assert report["worst_fold"] == "BETA"


def test_leave_one_language_group_out_cuts_folds_on_families():
    assert CAL.language_group("de") == CAL.language_group("en") == "GERMANIC"
    assert CAL.language_group("fr") == "ROMANCE"
    cases = ([calibration_case(f"g{i}", language="de") for i in range(3)] +
             [calibration_case(f"r{i}", language="fr",
                               predicted="REJECTED",
                               reviewer="ACCEPTED_CANDIDATE") for i in range(3)])
    report = CAL.leave_one_language_group_out(cases, min_fold_size=1)
    assert report["groups"] == ["GERMANIC", "ROMANCE"]
    assert report["spread"]["folds"] == 2
    assert report["spread"]["range"] == 1.0
    assert report["stable"] is False


def test_leave_one_document_genre_out_reports_per_fold_spread():
    cases = ([calibration_case(f"p{i}", genre="PRESS_RELEASE") for i in range(3)] +
             [calibration_case(f"t{i}", genre="TENDER_NOTICE") for i in range(3)])
    report = CAL.leave_one_document_genre_out(cases, min_fold_size=1)
    assert [fold["held_out"] for fold in report["folds"]] == \
        ["PRESS_RELEASE", "TENDER_NOTICE"]
    assert all(fold["size"] == 3 and fold["train_size"] == 3
               for fold in report["folds"])
    assert report["spread"]["range"] == 0.0
    assert report["stable"] is True


def test_a_fold_may_not_be_cut_on_anything_but_a_group():
    with pytest.raises(ValueError):
        CAL.grouped_validation([calibration_case("x")], group_key="candidate_id")
    with pytest.raises(ValueError):
        CAL.grouped_validation([calibration_case("x")], group_key="random")


def test_all_grouped_validations_surfaces_the_worst_grouping():
    cases = (
        [calibration_case(f"a{i}", campaign="ALPHA", layout="SINGLE_COLUMN")
         for i in range(3)] +
        [calibration_case(f"b{i}", campaign="BETA", layout="TWO_COLUMN",
                          predicted="REJECTED", reviewer="ACCEPTED_CANDIDATE")
         for i in range(3)])
    report = CAL.all_grouped_validations(cases, min_fold_size=1)
    assert set(report["by_grouping"]) == set(CAL.GROUPING_KEYS)
    assert report["worst_spread"] == 1.0
    assert report["stable_everywhere"] is False


def test_a_fold_report_carries_wrong_admissions_and_recall():
    cases = [calibration_case("w0", predicted="ACCEPTED_CANDIDATE",
                              reviewer="REJECTED"),
             calibration_case("w1", predicted="REJECTED",
                              reviewer="EVIDENCE_BOUND"),
             calibration_case("w2")]
    report = CAL.leave_one_campaign_out(cases, min_fold_size=1)
    fold = report["folds"][0]
    assert fold["wrong_admissions"] == 1
    assert fold["over_rejections"] == 1
    assert fold["recoverable_assertions"] == 2
    assert fold["recoverable_assertion_recall"] == 0.5
    assert report["wrong_admissions_total"] == 1


# ---------------------------------------------------------------------------
# Feature stability
# ---------------------------------------------------------------------------

def test_feature_stability_flags_campaign_identity():
    cases = ([calibration_case(f"a{i}", campaign="ALPHA",
                               features={"marker": 1.0}) for i in range(5)] +
             [calibration_case(f"b{i}", campaign="BETA",
                               features={"marker": 0.0}) for i in range(5)])
    report = CAL.feature_stability_report(cases)
    entry = report["features"]["marker"]
    assert entry["flags"]["correlates_with_campaign"] is True
    assert entry["evidence"]["campaign_concentration"] == 1.0
    assert "marker" in report["flagged"]


def test_feature_stability_flags_dossier_ordering():
    cases = [calibration_case(f"o{i}", index=i, features={"position": i / 9.0})
             for i in range(10)]
    report = CAL.feature_stability_report(cases)
    entry = report["features"]["position"]
    assert entry["flags"]["correlates_with_dossier_order"] is True
    assert entry["evidence"]["dossier_order_correlation"] == 1.0


def test_feature_stability_flags_improvement_only_on_v5_2_origin():
    cases = (
        [calibration_case(f"o{i}", origin="V5_2", campaign=f"C{i % 3}",
                          features={"legacy": 1.0}) for i in range(5)] +
        [calibration_case(f"n{i}", origin="V5_4", campaign=f"C{i % 3}",
                          features={"legacy": 1.0}, predicted="REJECTED",
                          reviewer="ACCEPTED_CANDIDATE") for i in range(5)] +
        [calibration_case(f"c{i}", origin="V5_4", campaign=f"C{i % 3}",
                          features={"legacy": 0.0}) for i in range(5)] +
        [calibration_case(f"w{i}", origin="V5_4", campaign=f"C{i % 3}",
                          features={"legacy": 0.0}, predicted="REJECTED",
                          reviewer="ACCEPTED_CANDIDATE") for i in range(5)])
    report = CAL.feature_stability_report(cases)
    entry = report["features"]["legacy"]
    assert entry["evidence"]["population_accuracy"] == 0.5
    assert entry["evidence"]["v5_2_origin_accuracy"] == 1.0
    assert entry["evidence"]["other_origin_accuracy"] == 0.0
    assert entry["flags"]["improves_only_v5_2_origin"] is True


def test_feature_stability_flags_clean_regression():
    cases = (
        [calibration_case(f"da{i}", evidence_class="DEVELOPMENT",
                          campaign=f"C{i % 2}", features={"brittle": 1.0})
         for i in range(4)] +
        [calibration_case(f"di{i}", evidence_class="DEVELOPMENT",
                          campaign=f"C{i % 2}", features={"brittle": 0.0},
                          predicted="REJECTED" if i < 2 else "ACCEPTED_CANDIDATE",
                          reviewer="ACCEPTED_CANDIDATE") for i in range(4)] +
        [calibration_case(f"ca{i}", evidence_class="CLEAN_HELD_OUT",
                          campaign=f"C{i % 2}", features={"brittle": 1.0},
                          predicted="REJECTED", reviewer="ACCEPTED_CANDIDATE")
         for i in range(4)] +
        [calibration_case(f"ci{i}", evidence_class="CLEAN_HELD_OUT",
                          campaign=f"C{i % 2}", features={"brittle": 0.0})
         for i in range(4)])
    report = CAL.feature_stability_report(cases)
    entry = report["features"]["brittle"]
    assert entry["evidence"]["development_accuracy"] == 1.0
    assert entry["evidence"]["clean_accuracy"] == 0.0
    assert entry["flags"]["clean_regression"] is True
    assert report["unstable_feature_count"] >= 1


def test_feature_stability_flags_source_specific_vocabulary():
    cases = (
        [calibration_case(f"s{i}", family="BUNDESPRESSEAMT",
                          campaign=f"C{i % 3}",
                          features={"house_style": 1.0},
                          vocabulary=("pressemitteilung", "bundesregierung"))
         for i in range(5)] +
        [calibration_case(f"o{i}", family="COMMISSION", campaign=f"C{i % 3}",
                          features={"house_style": 0.0},
                          vocabulary=("communication", "commission"))
         for i in range(5)])
    report = CAL.feature_stability_report(cases)
    entry = report["features"]["house_style"]
    assert entry["flags"]["correlates_with_source_vocabulary"] is True
    marker = entry["evidence"]["source_vocabulary_marker"]
    assert marker["source_family"] == "BUNDESPRESSEAMT"
    assert marker["coverage"] == 1.0


def test_feature_stability_report_is_json_serializable():
    import json
    cases = [calibration_case(f"j{i}", index=i, features={"f": float(i % 2)},
                              vocabulary=("token",)) for i in range(4)]
    report = CAL.feature_stability_report(cases)
    payload = json.loads(json.dumps(report))
    assert payload["cases"] == 4
    assert set(payload["flag_vocabulary"]) == set(CAL.STABILITY_FLAGS)


def test_a_stable_feature_is_not_flagged():
    cases = ([calibration_case(f"s{i}", campaign="ALPHA", index=i,
                               features={"finite_clause": 1.0},
                               vocabulary=("shared",)) for i in range(4)] +
             [calibration_case(f"t{i}", campaign="BETA", index=i,
                               features={"finite_clause": 1.0},
                               vocabulary=("shared",)) for i in range(4)])
    report = CAL.feature_stability_report(cases)
    assert report["features"]["finite_clause"]["flags"] == {
        flag: False for flag in CAL.STABILITY_FLAGS}
    assert report["flagged"] == []


# ---------------------------------------------------------------------------
# Admissibility metrics
# ---------------------------------------------------------------------------

def test_metrics_report_precision_recall_over_rejection_and_quarantine():
    cases = [
        calibration_case("m0"),                                    # right accept
        calibration_case("m1", predicted="ACCEPTED_CANDIDATE",
                         reviewer="REJECTED"),                     # wrong admission
        calibration_case("m2", predicted="QUARANTINED",
                         reviewer="QUARANTINED"),
        calibration_case("m3", predicted="REJECTED",
                         reviewer="EVIDENCE_BOUND"),                # over-rejection
        calibration_case("m4", predicted="EVIDENCE_BOUND",
                         reviewer="EVIDENCE_BOUND"),
    ]
    metrics = CAL.admissibility_metrics(cases)
    assert metrics["scored"] == 5
    assert metrics["accepted_precision"] == 0.5
    assert metrics["wrong_admissions"] == 1
    assert metrics["wrong_admission_candidate_ids"] == ["m1"]
    assert metrics["recoverable_assertions"] == 3
    assert metrics["recoverable_assertion_recall"] == round(2 / 3, 4)
    assert metrics["over_rejection_rate"] == 0.2
    assert metrics["quarantine_rate"] == 0.2
    assert metrics["passes"] is False


def test_zero_wrong_admissions_by_rejecting_everything_is_not_a_pass():
    cases = [calibration_case(f"r{i}", predicted="REJECTED",
                              reviewer="ACCEPTED_CANDIDATE") for i in range(6)]
    metrics = CAL.admissibility_metrics(cases)
    assert metrics["wrong_admissions"] == 0
    assert metrics["degenerate_rejection"] is True
    assert set(metrics["degenerate_reasons"]) == {
        "NO_CANDIDATE_ADMITTED", "RECOVERABLE_ASSERTIONS_DISCARDED",
        "OVER_REJECTION_ABOVE_LIMIT"}
    assert metrics["passes"] is False


def test_a_clean_population_passes():
    cases = ([calibration_case(f"g{i}") for i in range(6)] +
             [calibration_case(f"q{i}", predicted="QUARANTINED",
                               reviewer="QUARANTINED") for i in range(2)])
    metrics = CAL.admissibility_metrics(cases)
    assert metrics["wrong_admissions"] == 0
    assert metrics["degenerate_rejection"] is False
    assert metrics["passes"] is True


def test_metrics_carry_the_invariant_failure_distribution():
    cases = [calibration_case("f0", predicted="REJECTED", reviewer="REJECTED"),
             calibration_case("f1", predicted="QUARANTINED",
                              reviewer="QUARANTINED")]
    metrics = CAL.admissibility_metrics(cases)
    distribution = metrics["invariant_failure_distribution"]
    assert set(distribution) == set(INV.INVARIANT_NAMES)
    assert distribution["no_structural_chrome"][VIO] == 1
    assert distribution["predicate_complete"][UNR] == 1


def test_calibration_cases_reject_an_unknown_terminal_state():
    with pytest.raises(ValueError):
        calibration_case("bad", predicted="ACCEPTED_CANDIDATE",
                         reviewer="MAYBE_ACCEPTED")


def test_build_case_pairs_an_assessment_with_a_reviewer_label():
    assessment = assess(CLEAN_DE, CLEAN_DE.strip(), language="de")
    case = CAL.build_case(assessment, reviewer_state="ACCEPTED_CANDIDATE",
                          source_family="BKA", campaign="DE_2026",
                          language="de", document_genre="PRESS_RELEASE",
                          layout_type="SINGLE_COLUMN")
    assert case.correct is True
    assert case.language_group == "GERMANIC"
    assert set(case.invariant_states) == set(INV.INVARIANT_NAMES)


def test_singleton_folds_are_excluded_from_the_spread_statistic():
    """A one-case fold scores 0.0 or 1.0 by construction.

    The first real measurement produced a source-family range of exactly 1.0
    because most families held a single document; that number said nothing
    about stability and dominated the statistic computed over it.
    """
    from curunir_operational.v5_4 import calibration as C
    cases = [
        calibration_case("a", campaign="BIG", predicted="ACCEPTED_CANDIDATE",
              reviewer="ACCEPTED_CANDIDATE"),
        calibration_case("b", campaign="BIG", predicted="ACCEPTED_CANDIDATE",
              reviewer="ACCEPTED_CANDIDATE"),
        calibration_case("c", campaign="BIG", predicted="ACCEPTED_CANDIDATE",
              reviewer="REJECTED"),
        calibration_case("d", campaign="BIG", predicted="REJECTED", reviewer="REJECTED"),
        calibration_case("e", campaign="BIG", predicted="REJECTED", reviewer="REJECTED"),
        calibration_case("f", campaign="SINGLETON", predicted="REJECTED",
              reviewer="ACCEPTED_CANDIDATE"),
    ]
    lenient = C.grouped_validation(cases, group_key="campaign", min_fold_size=1)
    strict = C.grouped_validation(cases, group_key="campaign", min_fold_size=5)
    assert lenient["folds_scored_for_spread"] == 2
    assert strict["folds_scored_for_spread"] == 1
    assert "SINGLETON" in strict["folds_excluded_as_undersized"]
    assert strict["spread"]["range"] == 0.0
