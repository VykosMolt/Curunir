"""V5.8.1 §9, §24 — focused cover for the D23, D24 and D27 repairs.

Each repair is tested against its own failure mode *and* against the failure
mode of over-correcting it, because all three are fixes that a careless version
would push too far: refusing every repeated string, refusing every identical
wording, refusing to report any number at all.
"""
from __future__ import annotations

import pytest

from curunir_operational.v5_8_1 import lineage as LI
from curunir_operational.v5_8_1 import regions as RG
from curunir_operational.v5_8_1 import sampling as SP

pytestmark = pytest.mark.no_db


# ===========================================================================
# D24 — structural candidate origin
# ===========================================================================
PAGE = """<html><body>
<nav class="navbar"><ul><li><a href="/a">Home</a></li><li><a href="/b">Treaties</a></li></ul></nav>
<div class="breadcrumb">Home &gt; Media centre &gt; Fact sheets &gt; Detail</div>
<div id="search"><label>When autocomplete results are available use up and down arrows to review and enter to select.</label></div>
<h1>Tuberculosis</h1>
<h2>Table of contents</h2><ul><li><a href="#1">Overview</a></li></ul>
<p>Tuberculosis is caused by bacteria and most often affects the lungs.</p>
<p>It spreads when people with the disease cough or sneeze.</p>
<table><tr><td>Africa</td><td>2.5 million cases were reported in 2023.</td></tr></table>
<figcaption>Figure 1 shows the estimated incidence by region.</figcaption>
<footer>&copy; World Health Organization 2026 - All rights reserved</footer>
<div class="lang-select"><a href="/fr">Fran&ccedil;ais</a></div>
<div class="pagination"><a href="/p2">Next page</a></div>
</body></html>"""


@pytest.fixture(scope="module")
def typed():
    regions = RG.segment(PAGE, source_id="s1", source_family_id="who.int")
    return {region.text: region for region in regions}


@pytest.mark.parametrize("needle,expected", [
    ("Home", "NAVIGATION_MENU"),
    ("Home > Media centre > Fact sheets > Detail", "BREADCRUMB"),
    ("When autocomplete results are available use up and down arrows to review "
     "and enter to select.", "SEARCH_CONTROL"),
    ("Tuberculosis", "HEADING_CONTEXT_ONLY"),
    ("Table of contents", "TABLE_OF_CONTENTS_ENTRY"),
    ("Overview", "GENERIC_LINK_LABEL"),
    ("Tuberculosis is caused by bacteria and most often affects the lungs.",
     "PRIMARY_PROPOSITION_CONTENT"),
    ("2.5 million cases were reported in 2023.", "TABLE_OR_STRUCTURED_PROPOSITION"),
    ("Figure 1 shows the estimated incidence by region.",
     "CAPTION_OR_FIGURE_PROPOSITION"),
    ("© World Health Organization 2026 - All rights reserved", "FOOTER_FURNITURE"),
    ("Français", "LANGUAGE_SELECTOR"),
    ("Next page", "PAGINATION_CONTROL"),
])
def test_each_region_is_typed_by_what_it_structurally_is(typed, needle, expected):
    assert needle in typed, sorted(typed)[:5]
    assert typed[needle].content_region_type == expected


def test_only_proposition_bearing_regions_are_admitted(typed):
    admitted = {text for text, region in typed.items()
                if RG.admit(region=region, span=text).admitted}
    assert "Tuberculosis is caused by bacteria and most often affects the lungs." in admitted
    assert "2.5 million cases were reported in 2023." in admitted
    assert "Figure 1 shows the estimated incidence by region." in admitted
    for furniture in ("Home", "Home > Media centre > Fact sheets > Detail",
                      "Table of contents", "Overview", "Next page", "Français"):
        assert furniture not in admitted


def test_a_pronoun_subject_is_not_a_reason_to_refuse(typed):
    span = "It spreads when people with the disease cough or sneeze."
    decision = RG.admit(region=typed[span], span=span)
    assert decision.admitted
    assert decision.referential_subject_state == "IMPLICIT_RESOLVABLE_IN_CONTEXT"


def test_a_legal_recital_with_no_finite_verb_is_still_content(typed):
    """The Spanish preamble form: gerund head, semicolon end, no subordinator."""
    region = typed["Tuberculosis is caused by bacteria and most often affects the lungs."]
    recital = ("Reafirmando su proposito de consolidar en este Continente, dentro "
               "del cuadro de las instituciones democraticas, un regimen de "
               "libertad personal y de justicia social;")
    assert RG.admit(region=region, span=recital).admitted


def test_the_span_layer_catches_furniture_the_region_layer_would_have_passed(typed):
    """§5.5 — defence in depth is only real if the layers disagree sometimes."""
    prose = typed["Tuberculosis is caused by bacteria and most often affects the lungs."]
    for furniture in ("When autocomplete results are available use up and down "
                      "arrows to review and enter to select.",
                      "Copyright 2026 World Health Organization. All rights reserved.",
                      "Home > Media centre > Fact sheets > Detail > More"):
        assert not RG.admit(region=prose, span=furniture).admitted, furniture


def test_an_untyped_region_is_refused_rather_than_assumed_to_be_prose():
    region = RG.RegionRecord(
        "r", "s", "f", 0, "UNKNOWN_STRUCTURAL_REGION", "DIV", (), 0.0, 0, 0,
        "UNDER_HEADING", "NOT_A_LIST_ITEM", "NOT_CONTENTS", "HTML_BLOCK_ELEMENT",
        "Some text of uncertain provenance that reads like a sentence.", "x",
        "2026-07-26T00:00:00+00:00")
    assert not RG.admit(region=region, span=region.text).admitted


def test_repetition_across_pages_retypes_a_paragraph_as_furniture():
    region = RG.segment("<p>Follow our work on social media for updates.</p>",
                        source_id="s1", source_family_id="f")[0]
    assert region.content_region_type == "PRIMARY_PROPOSITION_CONTENT"
    retyped = RG.retype(region, repetition=7)
    assert retyped.content_region_type == "UNKNOWN_STRUCTURAL_REGION"
    assert "7 pages" in retyped.deciding_signal


def test_repetition_inside_one_document_is_not_repetition_across_pages():
    """Statutory language repeats legitimately; counting occurrences would
    delete it.  The index counts distinct pages, not occurrences."""
    index = RG.repetition_index([
        ("s1", "f", ["The Party shall notify the Secretariat."] * 9),
    ])
    assert set(index.values()) == {1}


def test_a_contents_heading_is_not_a_claim(typed):
    assert typed["Table of contents"].content_region_type == "TABLE_OF_CONTENTS_ENTRY"
    assert typed["Table of contents"].content_region_type in RG.REFERENTIAL_CONTENT


def test_a_data_row_survives_while_a_link_grid_does_not():
    grid = RG.segment(
        '<table><tr><td><a href="/1">Report 2021</a></td>'
        '<td><a href="/2">Report 2022</a></td></tr></table>',
        source_id="s", source_family_id="f")
    assert not any(r.proposition_bearing for r in grid if r.text)
    data = RG.segment("<table><tr><td>Deaths fell to 1.25 million in 2023.</td></tr></table>",
                      source_id="s", source_family_id="f")
    assert any(r.content_region_type == "TABLE_OR_STRUCTURED_PROPOSITION"
               for r in data)


def test_the_audit_reports_its_own_denominators():
    regions = RG.segment(PAGE, source_id="s1", source_family_id="who.int")
    decisions = [RG.admit(region=r, span=r.text) for r in regions]
    report = RG.audit(decisions, regions)
    assert report["regions"] == len(regions)
    assert report["admitted_from_non_proposition_origin"] == 0
    assert sum(report["regions_by_class"].values()) == len(regions)


# ===========================================================================
# D23 — evidentiary independence
# ===========================================================================
def _p(pid, *, work, manifestation, observation, text="The rate fell to 3.1%.",
       **extra):
    return {"proposition_id": pid, "intellectual_work_id": work,
            "manifestation_id": manifestation, "source_observation_id": observation,
            "text": text, **extra}


def test_the_claim_offered_as_its_own_evidence_is_a_construction_defect():
    claim = _p("c", work="w", manifestation="m", observation="o")
    result = LI.assess_eligibility(claim=claim, evidence_items=[claim])
    assert not result.eligible
    assert result.refusal_state == "SELF_EVIDENCE"
    assert result.terminal_state == "CONSTRUCTION_DEFECT"


def test_the_same_proposition_in_another_manifestation_of_one_work_is_self_evidence():
    claim = _p("c", work="w", manifestation="m1", observation="o1")
    evidence = _p("e", work="w", manifestation="m2", observation="o2")
    result = LI.assess_eligibility(claim=claim, evidence_items=[evidence])
    assert result.lineage[0].independent_origin_state == "SELF_EVIDENCE"
    assert not result.eligible


def test_identical_wording_from_a_different_work_is_ordinary_evidence():
    claim = _p("c", work="w1", manifestation="m1", observation="o1")
    evidence = _p("e", work="w2", manifestation="m2", observation="o2")
    result = LI.assess_eligibility(claim=claim, evidence_items=[evidence])
    assert result.eligible
    assert result.lineage[0].independent_origin_state == "INDEPENDENT_SOURCE_EVIDENCE"


def test_claim_derived_evidence_is_refused():
    claim = _p("c", work="w1", manifestation="m1", observation="o1")
    evidence = _p("e", work="w2", manifestation="m2", observation="o2",
                  text="Something else entirely.", derivation_ids=["c"])
    result = LI.assess_eligibility(claim=claim, evidence_items=[evidence])
    assert result.refusal_state == "CLAIM_DERIVED_EVIDENCE"


def test_report_derived_evidence_is_refused():
    claim = _p("c", work="w1", manifestation="m1", observation="o1")
    evidence = _p("e", work="w2", manifestation="m2", observation="o2",
                  text="Other text.", origin_kind="REPORT_PROPOSITION",
                  report_proposition_lineage=["c"])
    result = LI.assess_eligibility(claim=claim, evidence_items=[evidence])
    assert result.refusal_state == "REPORT_DERIVED_EVIDENCE"


def test_a_generated_summary_is_refused():
    claim = _p("c", work="w1", manifestation="m1", observation="o1")
    evidence = _p("e", work="w2", manifestation="m2", observation="o2",
                  text="Other text.", origin_kind="GENERATED_SUMMARY")
    result = LI.assess_eligibility(claim=claim, evidence_items=[evidence])
    assert result.refusal_state == "GENERATED_SUMMARY_EVIDENCE"


def test_same_work_evidence_supports_meaning_but_never_corroborates():
    claim = _p("c", work="w", manifestation="m1", observation="o1",
               text="Der Satz lautet so.")
    evidence = _p("e", work="w", manifestation="m2", observation="o2",
                  text="The sentence reads thus.", translation_lineage=["m1"])
    result = LI.assess_eligibility(claim=claim, evidence_items=[evidence])
    assert result.eligible
    assert result.lineage[0].independent_origin_state == "SAME_INTELLECTUAL_WORK_EVIDENCE"
    weight = LI.corroboration_weight(result)
    assert weight["independent_witnesses"] == 0
    assert weight["independence_language_permitted"] is False


def test_duplicate_manifestations_do_not_multiply_support():
    claim = _p("c", work="w1", manifestation="m1", observation="o1")
    duplicates = [_p(f"e{i}", work="w2", manifestation="m2", observation="o2",
                     text="Other text.") for i in range(4)]
    result = LI.assess_eligibility(claim=claim, evidence_items=duplicates)
    weight = LI.corroboration_weight(result)
    assert weight["independent_witnesses"] <= 1


def test_two_distinct_works_do_permit_independence_language():
    claim = _p("c", work="w1", manifestation="m1", observation="o1")
    evidence = [_p("e1", work="w2", manifestation="m2", observation="o2",
                   text="Other."),
                _p("e2", work="w3", manifestation="m3", observation="o3",
                   text="Other still.")]
    result = LI.assess_eligibility(claim=claim, evidence_items=evidence)
    assert LI.corroboration_weight(result)["independence_language_permitted"] is True


def test_unresolvable_lineage_is_refused_rather_than_assumed_independent():
    claim = _p("c", work="w1", manifestation="m1", observation="o1")
    result = LI.assess_eligibility(
        claim=claim, evidence_items=[{"proposition_id": "e", "text": "Other."}])
    assert result.refusal_state == "LINEAGE_UNRESOLVED"


def test_an_empty_bundle_is_refused():
    claim = _p("c", work="w1", manifestation="m1", observation="o1")
    assert not LI.assess_eligibility(claim=claim, evidence_items=[]).eligible


def test_a_circular_item_is_refused_loudly_not_dropped_quietly():
    """Filtering the circular item out would leave the rest to answer a
    question that was never well posed, and hide the fault from the audit."""
    claim = _p("c", work="w1", manifestation="m1", observation="o1")
    good = _p("e", work="w2", manifestation="m2", observation="o2", text="Other.")
    result = LI.assess_eligibility(claim=claim, evidence_items=[claim, good])
    assert not result.eligible
    assert result.refusal_state == "SELF_EVIDENCE"


# ===========================================================================
# D27 — evaluation power
# ===========================================================================
def test_ten_units_cannot_adjudicate_the_threshold():
    plan = SP.build_plan("TEST")
    verdict = SP.surface_verdict(surface="SURFACE_1_EXTRACTION", correct=10,
                                 adjudicable=10, critical_failures=0, plan=plan)
    assert verdict["verdict"] == "NOT_ADJUDICABLE"
    assert verdict["point_estimate"] == 1.0


def test_a_perfect_adequately_powered_sample_passes_hardened():
    plan = SP.build_plan("TEST")
    verdict = SP.surface_verdict(surface="SURFACE_1_EXTRACTION", correct=120,
                                 adjudicable=120, critical_failures=0, plan=plan)
    assert verdict["verdict"] == "PASS_HARDENED"
    assert verdict["interval_95"][0] >= plan.threshold


def test_a_point_estimate_at_the_threshold_is_not_a_hardened_pass():
    plan = SP.build_plan("TEST")
    verdict = SP.surface_verdict(surface="SURFACE_1_EXTRACTION", correct=114,
                                 adjudicable=120, critical_failures=0, plan=plan)
    assert verdict["verdict"] == "PASS_POINT_ESTIMATE_ONLY"


def test_a_critical_failure_defeats_any_interval():
    plan = SP.build_plan("TEST")
    verdict = SP.surface_verdict(surface="SURFACE_4_CLAIM_SUPPORT", correct=200,
                                 adjudicable=200, critical_failures=1, plan=plan)
    assert verdict["verdict"] == "FAIL"


def test_every_surface_declares_a_minimum_and_strata():
    plan = SP.build_plan("TEST")
    assert len(plan.surfaces) == 6
    for surface in plan.surfaces:
        assert surface.minimum_adjudicable_units >= 100
        assert surface.principal_units > surface.minimum_adjudicable_units
        assert surface.reserve_units > 0
        assert len(surface.strata) >= 6
        assert surface.confidence_method.startswith("Wilson")


def test_the_plan_prohibits_interim_inspection():
    assert SP.build_plan("TEST").interim_inspection_prohibited is True


def test_only_prospectively_permitted_causes_may_consume_reserve():
    audit = SP.audit_reserve_use([
        {"unit_id": "u1", "cause": "ACCESS_FAILURE"},
        {"unit_id": "u2", "cause": "ORDINARY_PRODUCTION_ERROR"},
    ])
    assert audit["verdict"] == "FAIL"
    assert audit["illegal_replacements"][0]["unit_id"] == "u2"


def test_an_ordinary_production_error_is_never_replaceable():
    for cause in SP.NON_REPLACEABLE_CAUSES:
        assert cause not in SP.REPLACEABLE_CAUSES


def test_the_required_sample_size_is_derived_not_asserted():
    needed = SP.units_required_for_precision(0.95, 0.05)
    assert SP.half_width(round(0.95 * needed), needed) <= 0.05
    assert SP.half_width(round(0.95 * (needed - 1)), needed - 1) > 0.05


# --- V5.8.1 defect D28: extraction fidelity ---------------------------------
#
# The Arabic World Health Assembly resolution PDFs extract with bidi control
# characters and letter-level corruption.  Production admitted 42 such spans and
# bound 13 of them ROLE_BINDING_ESTABLISHED — a wrong quotation carried forward
# with full confidence.  Every layer before this one asked what a span means;
# none asked whether the characters were the document's.

CORRUPT_ARABIC = (
    "‫وق دد اح دال دت عتم دا بنةرير لجن دة البرن دامج والميةاني دة‬")
CLEAN_ARABIC = "تعتمد جمعية الصحة العالمية برنامج العمل العام الرابع عشر للمنظمة"


def _region(text, klass="PRIMARY_PROPOSITION_CONTENT"):
    from curunir_operational.v5_8_1.regions import RegionRecord
    from curunir_operational.v5_1.models import now_utc
    return RegionRecord("region-1", "source-1", "family-1", 0, klass,
                        "PARAGRAPH", (), 0.0, 0, 0, "UNDER_HEADING",
                        "NOT_A_LIST_ITEM", "NOT_CONTENTS", "PDF_TEXT_OPERATORS",
                        text, "assertive prose", now_utc())


def test_extraction_artefacts_are_refused_before_any_semantic_test():
    from curunir_operational.v5_8_1 import regions as RG

    assert RG.extraction_fidelity(CORRUPT_ARABIC)
    decision = RG.admit(region=_region(CORRUPT_ARABIC), span=CORRUPT_ARABIC)
    assert not decision.admitted
    assert "extraction artefact" in decision.refusal_reason


def test_replacement_characters_are_an_extraction_artefact():
    from curunir_operational.v5_8_1 import regions as RG

    span = "The Committee ��� adopted the report on 12 March 2024."
    assert RG.extraction_fidelity(span)
    assert not RG.admit(region=_region(span), span=span).admitted


def test_clean_arabic_is_not_refused_as_an_extraction_artefact():
    """Neighbouring negative: the guard is about artefacts, not about Arabic."""
    from curunir_operational.v5_8_1 import regions as RG

    assert RG.extraction_fidelity(CLEAN_ARABIC) is None
    assert RG.admit(region=_region(CLEAN_ARABIC), span=CLEAN_ARABIC).admitted


def test_clean_latin_prose_is_not_refused_as_an_extraction_artefact():
    from curunir_operational.v5_8_1 import regions as RG

    span = "The Health Assembly adopted the fourteenth general programme of work."
    assert RG.extraction_fidelity(span) is None
    assert RG.admit(region=_region(span), span=span).admitted


def test_an_extraction_artefact_in_furniture_is_still_refused():
    from curunir_operational.v5_8_1 import regions as RG

    decision = RG.admit(region=_region(CORRUPT_ARABIC, "NAVIGATION_MENU"),
                        span=CORRUPT_ARABIC)
    assert not decision.admitted


# --- V5.8.1 defect D29: the role binder was called without its context -------
#
# roles.py resolves an anaphoric subject against a bounded left context and
# declines when the context does not decide it.  Both invariant call sites
# passed no context, so every anaphor resolved ANTECEDENT_ABSENT and the
# anaphora path could not fire in the pipeline at all.

def test_left_context_helper_is_bounded_by_the_binders_own_window():
    from curunir_operational.v5_4 import invariants as INV
    from curunir_operational.v5_8_1 import roles as RB

    class _Doc:
        text = "x" * 5000 + "TAIL"

    class _Cand:
        span_start = 5000

    class _Ctx:
        document = _Doc()
        candidate = _Cand()

    context = INV._left_context(_Ctx())
    assert len(context) == RB.ANTECEDENT_CONTEXT_CHARS
    assert context == "x" * RB.ANTECEDENT_CONTEXT_CHARS


def test_left_context_at_the_start_of_a_document_is_not_negative_indexed():
    from curunir_operational.v5_4 import invariants as INV

    class _Doc:
        text = "The Commission adopted the decision."

    class _Cand:
        span_start = 4

    class _Ctx:
        document = _Doc()
        candidate = _Cand()

    assert INV._left_context(_Ctx()) == "The "


def test_both_invariant_call_sites_pass_left_context():
    """A wiring regression is invisible in behaviour tests, so assert the call."""
    import inspect
    from curunir_operational.v5_4 import invariants as INV

    source = inspect.getsource(INV)
    # D32 moved both call sites to roles_v2; the D29 property is unchanged --
    # whichever binder production calls, it must pass the bounded left context.
    binds = [line for line in source.splitlines()
             if "_roles_v2.bind(" in line or "_roles.bind(" in line]
    assert len(binds) == 2, binds
    # Each bind( call must be followed, within its argument list, by the context.
    for name in ("referential_subject_complete", "predicate_complete"):
        body = inspect.getsource(getattr(INV, name))
        assert ("_roles_v2.bind(" in body or "_roles.bind(" in body), name
        assert "left_context=_left_context(ctx)" in body, name


def test_an_anaphoric_subject_now_reaches_the_antecedent_path():
    """Without context the resolver cannot run at all; with it, it decides."""
    from curunir_operational.v5_8_1 import roles as RB

    span = "These provisions shall apply from 1 January 2027."
    context = ("The European Data Protection Board adopted the present "
               "guidelines at its plenary session on 12 March 2024. ")
    without = RB.bind(candidate_id="c1", text=span, language="en")
    assert without.subject_source == "NO_ANTECEDENT_IN_BOUNDED_CONTEXT"

    with_context = RB.bind(candidate_id="c1", text=span, language="en",
                           left_context=context)
    # The resolver now runs.  On this context it declines, which is the correct
    # answer for several equally good capitalised candidates -- the point is
    # that it reached a judgement instead of a vacuous absence.
    assert with_context.subject_source != "NO_ANTECEDENT_IN_BOUNDED_CONTEXT"
    assert with_context.subject_source in ("AMBIGUOUS_ANTECEDENT",) or \
        with_context.subject_source.startswith("BOUNDED_ANTECEDENT:")


def test_a_single_dominant_antecedent_is_resolved_not_refused():
    """Neighbouring positive: the decline is about ambiguity, not about context."""
    from curunir_operational.v5_8_1 import roles as RB

    result = RB.resolve_antecedent(
        anaphor="These provisions",
        left_context="the European Data Protection Board issued guidance. ")
    assert result.state in ("ANTECEDENT_ESTABLISHED", "ANTECEDENT_RECOVERABLE")
    assert result.span is not None
