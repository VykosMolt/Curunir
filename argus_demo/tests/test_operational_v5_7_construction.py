"""V5.7 §3–§7 — evidence construction, and the states it may produce.

Every defect tested here was found by a reviewer reading a packet, not by a
check. That is the point: each survived review by being invisible to the checks,
so each is now a mechanical property that can be verified without a model.
"""

from __future__ import annotations

import pytest

from curunir_operational.v5_7 import construction as CN
from curunir_operational.v5_7 import states as ST

pytestmark = pytest.mark.no_db


# ===========================================================================
# §3 — numeric and value boundaries
# ===========================================================================

@pytest.mark.parametrize("span,context,structure", [
    ("The cost is €15.", "The cost is €15.3 billion this year.", "currency"),
    ("GDP increased by 0.", "GDP increased by 0.4% this quarter.", "percentage"),
    ("Wind reached 30", "Wind reached 30% of generation.", "percentage"),
    ("The fund holds 1,2", "The fund holds 1,250 projects.", "thousands"),
    # With a unit attached the widest straddling structure is the measurement,
    # and that is the more accurate label: the value is `1,250 MW`, not `1,250`.
    ("Capacity is 1,2", "Capacity is 1,250 MW total.", "measurement"),
    ("The line runs 3", "The line runs 3–5 km underground.", "range"),
    ("A margin of 2", "A margin of 2:1 was recorded.", "ratio"),
    ("A tolerance of 1.2e", "A tolerance of 1.2e-4 applies.", "scientific"),
    ("Bis zum 30.", "Bis zum 30. April jedes Kalenderjahres.", "date_ordinal"),
    ("Applies under Article 12", "Applies under Article 12(3) of the Act.",
     "legal_article"),
    ("It received €4.", "It received €4.2 million in grants.", "currency"),
])
def test_a_span_cut_inside_a_value_is_detected(span, context, structure):
    report = CN.numeric_structure(span, context=context)
    assert not report["numeric_token_complete"], span
    assert report["truncated_structure"] == structure


@pytest.mark.parametrize("span,context", [
    ("Emissions fell in 2026", "Emissions fell in 2026 and again later."),
    ("Rail takes 80% of the fund.", "Rail takes 80% of the fund. More text."),
    ("It cost €7 billion.", "It cost €7 billion. Next sentence."),
    ("A sentence with no figures at all.", "A sentence with no figures at all. More."),
    ("approved by the Cttee.", "approved by the Cttee. It then met."),
])
def test_a_span_that_merely_ends_near_a_number_is_not_sheared(span, context):
    """Over-firing here would quarantine every sentence ending in a year."""
    assert CN.numeric_structure(span, context=context)["numeric_token_complete"]


@pytest.mark.parametrize("span,context,expected", [
    ("The cost is €15.", "The cost is €15.3 billion this year.",
     "The cost is €15.3 billion"),
    ("GDP increased by 0.", "GDP increased by 0.4% this quarter.",
     "GDP increased by 0.4%"),
    ("The line runs 3", "The line runs 3–5 km underground.", "The line runs 3–5 km"),
    ("Capacity is 1,2", "Capacity is 1,250 MW total.", "Capacity is 1,250 MW"),
])
def test_the_repair_completes_the_value_including_its_unit(span, context, expected):
    """`€15.3` is still not what the source says; the magnitude word is the value."""
    repair = CN.value_repair(span, context)
    assert repair["repairable"]
    assert repair["replacement_span"] == expected


def test_an_unrecoverable_shear_says_so_rather_than_guessing():
    repair = CN.value_repair("The cost is €15.", "an unrelated context entirely")
    assert repair["repairable"] is False
    assert repair["replacement_span"] is None


# ===========================================================================
# §4 — keyed context alignment
# ===========================================================================

def _candidate(**over):
    base = {"candidate_id": "c1", "source_id": "doc-1", "source_family_id": "fam",
            "intellectual_work_id": "work-1", "manifestation_id": "man-1",
            "page_or_section": "BODY", "language": "en",
            "raw_span": "the operative sentence"}
    base.update(over)
    return base


def _context(**over):
    base = {"source_id": "doc-1", "source_family_id": "fam",
            "intellectual_work_id": "work-1", "manifestation_id": "man-1",
            "page_or_section": "BODY", "language": "en",
            "text": "before. the operative sentence. after."}
    base.update(over)
    return base


def test_a_keyed_local_join_succeeds():
    join = CN.join_context(candidate=_candidate(), context=_context())
    assert join.is_local
    assert join.span_contained_in_context
    assert join.mismatched_keys == ()


def test_local_context_must_contain_its_own_span():
    """The check a presence test cannot make — and the V5.6.1 defect exactly."""
    with pytest.raises(CN.ConstructionViolation, match="must contain the span"):
        CN.join_context(candidate=_candidate(),
                        context=_context(text="an entirely different passage."))


def test_context_from_another_document_may_not_be_local():
    with pytest.raises(CN.ConstructionViolation, match="disagrees on"):
        CN.join_context(candidate=_candidate(),
                        context=_context(source_id="doc-2", manifestation_id="man-2"))


def test_context_from_another_document_may_be_linked_evidence():
    join = CN.join_context(
        candidate=_candidate(), context=_context(source_id="doc-2"),
        context_relation="LINKED_CORROBORATING_DOCUMENT")
    assert not join.is_local


@pytest.mark.parametrize("offset", [1, -1, 2])
def test_the_rotation_attack_is_refused(offset):
    candidates = [_candidate(candidate_id=f"c{i}", raw_span=f"sentence {i}")
                  for i in range(4)]
    contexts = [_context(text=f"before. sentence {i}. after.") for i in range(4)]
    rotated = CN.rotate(contexts, offset)
    refused = 0
    for candidate, context in zip(candidates, rotated):
        try:
            CN.join_context(candidate=candidate, context=context)
        except CN.ConstructionViolation:
            refused += 1
    assert refused == len(candidates)


def test_a_table_join_requires_cell_identity():
    with pytest.raises(CN.ConstructionViolation):
        CN.join_context(
            candidate=_candidate(table_or_figure_id="t1",
                                 row_or_cell_identity="r1"),
            context=_context(table_or_figure_id="t1",
                             row_or_cell_identity="r2"))


# ===========================================================================
# §5 — claim echo
# ===========================================================================

CLAIM = "The Commission adopted the amending regulation on 30 April 2026."


def test_a_claim_copied_into_evidence_is_an_echo():
    report = CN.detect_claim_echo(
        claim_text=CLAIM,
        evidence_spans=[{"span_id": "e1", "text": CLAIM,
                         "derivation_direction": None}])
    assert report["claim_echoes"] == 1
    assert report["verdict"] == "FAIL"


def test_a_genuine_source_quotation_with_provenance_is_evidence():
    """Rejecting this would be the opposite error: sources do say things."""
    report = CN.detect_claim_echo(
        claim_text=CLAIM,
        evidence_spans=[{"span_id": "e1", "text": CLAIM,
                         "raw_source_text": CLAIM, "source_location": "OJ L 1/1",
                         "capture_hash": "a" * 64, "observation_id": "obs-1",
                         "derivation_direction": "SOURCE_TO_CLAIM"}])
    assert report["claim_echoes"] == 0
    assert report["legitimate_source_quotations"] == 1


def test_a_near_duplicate_without_provenance_is_an_echo():
    near = "The Commission adopted the amending regulation on 30 April 2026 today."
    report = CN.detect_claim_echo(
        claim_text=CLAIM,
        evidence_spans=[{"span_id": "e1", "text": near,
                         "derivation_direction": None}])
    assert report["claim_echoes"] == 1


def test_unrelated_evidence_is_not_an_echo():
    report = CN.detect_claim_echo(
        claim_text=CLAIM,
        evidence_spans=[{"span_id": "e1", "text": "Entirely different subject matter.",
                         "derivation_direction": None}])
    assert report["claim_echoes"] == 0


def test_a_source_quotation_missing_capture_provenance_is_an_echo():
    report = CN.detect_claim_echo(
        claim_text=CLAIM,
        evidence_spans=[{"span_id": "e1", "text": CLAIM,
                         "raw_source_text": CLAIM,
                         "derivation_direction": "SOURCE_TO_CLAIM"}])
    assert report["claim_echoes"] == 1
    assert "capture_hash" in report["echo_detail"][0]["missing_provenance"]


# ===========================================================================
# §6 — language boundaries
# ===========================================================================

def test_a_translation_must_record_who_produced_it():
    with pytest.raises(CN.ConstructionViolation, match="who or what produced it"):
        CN.representation(kind="MODEL_TRANSLATION", source_language="de",
                          target_language="en", text="rendered",
                          original_span_id="s1", translated_span_id="s2")


def test_a_gloss_is_never_authoritative_for_exact_quotation():
    gloss = CN.representation(
        kind="NORMALIZED_GLOSS", source_language="de", target_language="en",
        translator_or_provider="normaliser", text="an English gloss",
        original_span_id="s1", translated_span_id="s2")
    assert not gloss.authoritative_for("exact_quotation")
    assert not gloss.authoritative_for("legal_wording")
    assert gloss.authoritative_for("semantic_proposition")


def test_an_official_translation_may_carry_legal_wording():
    official = CN.representation(
        kind="OFFICIAL_TRANSLATION", source_language="de", target_language="en",
        translator_or_provider="Publications Office", text="the official text",
        original_span_id="s1", translated_span_id="s2",
        official_status="OFFICIAL")
    assert official.authoritative_for("legal_wording")


def test_the_original_needs_no_translator():
    original = CN.representation(
        kind="ORIGINAL_LANGUAGE_SOURCE", source_language="de",
        text="Die Kommission bewertet jedes Jahr.", original_span_id="s1")
    assert original.authoritative_for("exact_quotation")


@pytest.mark.parametrize("text,expected", [
    ("Die vorliegende Verordnung sollte am dritten Tag nach ihrer "
     "Veröffentlichung in Kraft treten.", "de"),
    ("Las energías eólica y solar alcanzaron el 30% de la electricidad "
     "generada, superando por primera vez.", "es"),
    ("The Commission adopted the regulation.", None),
])
def test_language_detection_is_honest_about_what_it_can_tell(text, expected):
    assert CN.detect_language(text) == expected


def test_a_span_declared_english_but_written_in_german_is_flagged():
    report = CN.audit_language_boundaries([{
        "packet_id": "p1", "original_language": "en",
        "raw_span": "Die vorliegende Verordnung sollte am dritten Tag nach "
                    "ihrer Veröffentlichung in Kraft treten."}])
    assert report["mistagged_language"] == 1
    assert report["verdict"] == "FAIL"


# ===========================================================================
# §7 — the recoverable state in the pipeline
# ===========================================================================

def test_a_recoverable_span_may_not_be_admitted_as_written():
    state = ST.pipeline_state(
        unit_id="u", extraction_state="EXTRACTION_RECOVERABLE_WITH_BOUNDARY_REPAIR",
        boundary_repair_plan={"repair_direction": "EXPAND_RIGHT_CONTEXT"})
    assert not state.reached_support
    assert state.is_upstream_failure


def test_a_repair_plan_names_a_real_direction():
    with pytest.raises(ST.PrecedenceViolation, match="unknown repair direction"):
        ST.boundary_repair_plan(unit_id="u", repair_direction="GUESS",
                                required_context="ctx", reason="because")


def test_a_repair_plan_must_say_why_the_repair_works():
    with pytest.raises(ST.PrecedenceViolation, match="must say why"):
        ST.boundary_repair_plan(unit_id="u",
                                repair_direction="EXPAND_RIGHT_CONTEXT",
                                required_context="ctx", reason="   ")


def test_the_repaired_candidate_takes_a_new_identity():
    """§7.3 — a malformed candidate is never mutated into its repair."""
    plan = ST.boundary_repair_plan(
        unit_id="candidate-1", repair_direction="EXPAND_RIGHT_CONTEXT",
        required_context="3 billion this year",
        reason="the span ends inside a currency amount")
    assert plan.unit_id == "candidate-1"
    assert plan.plan_id != "candidate-1"
