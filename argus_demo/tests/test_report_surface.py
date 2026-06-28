"""Tests for the synthetic report-surface carrier (REPORT_SURFACE_V0)."""

import inspect
import random
from pathlib import Path

import pytest

import argus_capsules.report_surface as rs
import argus_capsules.run_report_surface_v0 as rv
from argus_capsules.codec_text_to_slot import (
    NarrativeWordTokenizer,
    build_slot_span_masks,
)
from argus_capsules.report_surface import (
    CARRIER_STYLE,
    CodecReportConfig,
    CodecReportSurface,
    build_report_surface,
    mutate_report_surface,
    render_synthetic_report_surface,
    report_surface_quality_metrics,
    synthetic_safety_audit,
    validate_report_surface,
)


@pytest.fixture(scope="session", autouse=True)
def _db_setup():
    yield


@pytest.fixture(autouse=True)
def clean_db():
    yield


def _surface() -> CodecReportSurface:
    return build_report_surface(word_count_min=500, word_count_max=1200)


# 1 --------------------------------------------------------------------------
def test_report_surface_renderer_basic():
    surface = _surface()
    text = render_synthetic_report_surface(surface=surface, seed=3)
    word_count = len(text.split())
    assert 500 <= word_count <= 1200
    lowered = text.lower()
    for key in rs.REQUIRED_SECTION_KEYS:
        assert key in lowered, f"missing section {key}"
    # synthetic entities present (subject + object entity render verbatim)
    plan = rs.SyntheticReportPlan.from_slot_ids(
        surface, surface.random_slot_ids(random.Random(3)), seed=3
    )
    assert plan.subject_entity.lower() in lowered
    assert plan.object_entity.lower() in lowered
    # no hidden JSON / base64 / hex payload
    audit = synthetic_safety_audit(text)
    assert not audit["json_like_flag"]
    assert not audit["base64_like_flag"]
    assert not audit["hex_like_flag"]
    assert not audit["long_digit_run_flag"]


# 2 --------------------------------------------------------------------------
def test_report_surface_synthetic_safety():
    surface = _surface()
    clean = render_synthetic_report_surface(surface=surface, seed=1)
    assert validate_report_surface(clean).synthetic_safe
    assert synthetic_safety_audit(clean)["synthetic_safe"]

    real_agency = clean + "\n\nThis was filed with the CIA and FBI."
    assert not validate_report_surface(real_agency).synthetic_safe
    assert "cia" in validate_report_surface(real_agency).forbidden_terms

    classified = clean + "\n\nMarking: TOP SECRET // NOFORN."
    forbidden = validate_report_surface(classified).forbidden_terms
    assert "top secret" in forbidden

    person_org = clean.replace("Ember Holdings", "Acme Corporation")
    # fake classified label embedded
    fake_label = clean + "\n\nClassified by national security agency."
    assert not validate_report_surface(fake_label).synthetic_safe

    # acronym substrings must NOT false-positive (e.g. 'cia' in 'associated')
    benign = "The associated official social financial review was unclassifiable."
    assert synthetic_safety_audit(benign)["forbidden_terms"] == []


# 3 --------------------------------------------------------------------------
def test_report_surface_deterministic_seed():
    surface = _surface()
    a = render_synthetic_report_surface(surface=surface, seed=7)
    b = render_synthetic_report_surface(surface=surface, seed=7)
    assert a == b
    c = render_synthetic_report_surface(surface=surface, seed=8)
    assert a != c
    # span alignment is also deterministic and overlaps real word tokens
    ids = surface.random_slot_ids(random.Random(7))
    text, spans = surface.render_with_slot_spans(ids)
    assert len(spans) == surface.slot_count
    tok = NarrativeWordTokenizer.fit([text])
    masks = build_slot_span_masks(
        texts=[text], slot_spans=[spans], tokenizer=tok, max_words=4096
    )
    assert masks.shape[1] == surface.slot_count


# 4 --------------------------------------------------------------------------
def test_report_surface_quality_metrics():
    surface = _surface()
    text = render_synthetic_report_surface(surface=surface, seed=2)
    metrics = report_surface_quality_metrics(text)
    assert metrics["section_count"] == float(len(rs.REQUIRED_SECTION_KEYS))
    assert metrics["entity_consistency_score"] == 1.0
    assert metrics["timeline_consistency_score"] == 1.0
    assert 0.0 <= metrics["template_repetitiveness_score"] <= 0.20
    assert metrics["word_count"] >= 500
    assert metrics["synthetic_safety_flag"] == 0.0
    assert 0.0 <= metrics["plausibility_score"] <= 1.0


# 5 --------------------------------------------------------------------------
@pytest.mark.parametrize(
    "mode",
    (
        "report_swap",
        "paragraph_shuffle",
        "sentence_shuffle",
        "section_delete",
        "evidence_section_delete",
        "irrelevant_paragraph_delete",
        "local_word_edit",
        "synonym_rewrite",
        "date_token_edit",
        "same_distribution_impostor",
        "same_theme_impostor",
    ),
)
def test_report_surface_mutations(mode):
    surface = _surface()
    original = render_synthetic_report_surface(surface=surface, seed=5)
    other = render_synthetic_report_surface(surface=surface, seed=6)
    changed = mutate_report_surface(
        original, mode, seed=11, surface=surface, other_text=other
    )
    assert isinstance(changed, str) and changed
    assert changed != original
    # mutations stay synthetic-safe (no leaked real markers / payloads)
    assert synthetic_safety_audit(changed)["synthetic_safe"]


# 6 --------------------------------------------------------------------------
def test_report_surface_carrier_style_dispatch():
    import argus_capsules.run_codec_stage2_field_head_study as study

    report = study._build_carrier_surface(
        CARRIER_STYLE,
        global_slot_count=9,
        local_slot_count=256,
        choices_per_slot=16,
        word_count_min=500,
        word_count_max=1200,
    )
    assert isinstance(report, CodecReportSurface)
    narrative = study._build_carrier_surface(
        "narrative",
        global_slot_count=9,
        local_slot_count=256,
        choices_per_slot=16,
        word_count_min=400,
        word_count_max=700,
    )
    assert type(narrative).__name__ == "CodecNarrativeSurface"
    # drop-in: identical slot geometry so the pipeline is unchanged
    assert report.slot_count == narrative.slot_count
    assert report.vocab_size == narrative.vocab_size
    with pytest.raises(ValueError):
        study._build_carrier_surface(
            "not_a_style",
            global_slot_count=9,
            local_slot_count=256,
            choices_per_slot=16,
            word_count_min=500,
            word_count_max=1200,
        )


# 7 --------------------------------------------------------------------------
def test_report_surface_open_boundary():
    """The valid-open contract must forbid renderer/plan/target/teacher access."""
    forbidden = set(rv.OPEN_CONTRACT["forbidden_open_inputs"])
    for needle in (
        "renderer",
        "report_plan",
        "target_fields",
        "debug_ids",
        "date_teacher",
        "teacher_logits",
        "encoder",
        "gold_slot_ids",
        "remote_llm_api",
    ):
        assert needle in forbidden, f"{needle} not forbidden at open"
    allowed = set(rv.OPEN_CONTRACT["allowed_open_inputs"])
    assert "carrier_text" in allowed
    assert "secret" in allowed
    # renderer/plan must not be in the allowed set
    assert allowed.isdisjoint({"renderer", "report_plan", "target_fields"})
    # the surface itself exposes no open/decode entry point
    surface_names = {name for name, _ in inspect.getmembers(CodecReportSurface)}
    assert not (
        {"open", "decode", "open_capsule", "recover_fields", "parse"} & surface_names
    )


# 8 --------------------------------------------------------------------------
def test_report_surface_no_symbolic_inverse():
    module_path = Path(rs.__file__)
    source = module_path.read_text(encoding="utf-8")
    forbidden_names = {
        "decode_report_to_case",
        "report_to_fields",
        "inverse_report",
        "carrier_to_report_symbolic",
        "report_lookup",
        "carrier_lookup",
        "inverse_grammar",
        "symbolic_parser",
    }
    fn_names = {
        name for name, obj in inspect.getmembers(rs) if inspect.isfunction(obj)
    }
    assert not (forbidden_names & fn_names)
    # no reversible payload decoders in the renderer module
    for needle in ("b64decode", "bytes.fromhex", "json.loads", "carrier_lookup"):
        assert needle not in source, f"found forbidden token: {needle}"


# 9 --------------------------------------------------------------------------
def test_report_surface_verdict_logic():
    assert rv.tiny64_report_surface_verdict({"ran": False}) == "NOT_RUN"
    assert (
        rv.tiny64_report_surface_verdict(
            {
                "ran": True,
                "valid_open_ran": True,
                "valid_open_average_field": 0.82,
                "valid_open_exact_tuple": 0.2,
            }
        )
        == "PASS_VALID_OPEN"
    )
    assert (
        rv.tiny64_report_surface_verdict(
            {"ran": True, "average_field": 0.5, "exact_tuple": 0.0}
        )
        == "PARTIAL_FIELD_SIGNAL"
    )
    assert (
        rv.tiny64_report_surface_verdict(
            {"ran": True, "average_field": 0.1, "exact_tuple": 0.0}
        )
        == "FAIL_NO_SIGNAL"
    )

    gate = {
        "average_field": 0.9,
        "exact_tuple": 0.5,
        "value": 0.75,
        "date": 0.9,
        "subject": 0.9,
        "object": 0.9,
        "gap": 0.1,
    }
    assert (
        rv.tiny256_stage2_report_surface_verdict(gate)
        == "PASS_REPORT_SURFACE_STAGE2"
    )
    partial = dict(gate, average_field=0.7, exact_tuple=0.2, value=0.5)
    assert (
        rv.tiny256_stage2_report_surface_verdict(partial)
        == "PARTIAL_REPORT_SURFACE_SIGNAL"
    )
    fail = {"average_field": 0.2, "exact_tuple": 0.0}
    assert (
        rv.tiny256_stage2_report_surface_verdict(fail)
        == "FAIL_REPORT_SURFACE_STAGE2"
    )

    assert (
        rv.tiny256_stage3_report_surface_verdict({}, stage2_metrics=fail)
        == "NOT_RUN_STAGE2_GATE"
    )
    stage3_pass = {
        "valid_open_average_field": 0.8,
        "exact_tuple": 0.1,
        "schema_hash_exact": True,
        "negatives_core_zero": True,
        "clean_open_boundary": True,
        "carrier_causality_nontrivial": True,
    }
    assert (
        rv.tiny256_stage3_report_surface_verdict(stage3_pass, stage2_metrics=gate)
        == "PASS_REPORT_SURFACE_WEAK_SMOKE"
    )

    assert (
        rv.report_surface_negative_controls_verdict(
            {"negatives_core_zero": True, "near_secret_accept_max": 0.0}
        )
        == "PASS_CORE_ZERO"
    )
    assert (
        rv.report_surface_negative_controls_verdict(
            {"negatives_core_zero": True, "near_secret_accept_max": 0.2}
        )
        == "PARTIAL_NEAR_SECRET_OR_WEAK_ACCEPT"
    )
    assert (
        rv.report_surface_negative_controls_verdict({"negatives_core_zero": False})
        == "FAIL_CORE_SECURITY"
    )

    assert (
        rv.report_surface_carrier_causality_verdict(
            {"global_disruption_drop": 0.5, "local_edit_drop": 0.4}
        )
        == "PASS_STRONG"
    )
    assert (
        rv.report_surface_carrier_causality_verdict(
            {"global_disruption_drop": 0.2, "local_edit_drop": 0.05}
        )
        == "PASS_DISTRIBUTED"
    )
    assert (
        rv.report_surface_carrier_causality_verdict({"carrier_ignored": True})
        == "FAIL_NOT_CAUSAL"
    )


# 10 -------------------------------------------------------------------------
def test_report_surface_artifact_schema(tmp_path):
    config = CodecReportConfig(word_count_min=500, word_count_max=1200)
    verdicts = {
        "ARGUS_CAPSULE_REPORT_SURFACE_TINY64": "PARTIAL_FIELD_SIGNAL",
        "ARGUS_CAPSULE_REPORT_SURFACE_TINY256_STAGE2": "PARTIAL_REPORT_SURFACE_SIGNAL",
        "ARGUS_CAPSULE_REPORT_SURFACE_TINY256_STAGE3": "NOT_RUN_STAGE2_GATE",
        "REPORT_SURFACE_NEGATIVE_CONTROLS": "PASS_CORE_ZERO",
        "REPORT_SURFACE_CARRIER_CAUSALITY": "PASS_DISTRIBUTED",
        "REPORT_SURFACE_V0": "RENDERER_READY_SIGNAL_PARTIAL",
    }
    rv.write_artifact_bundle(
        tmp_path,
        config=config,
        sample_count=4,
        tiny64={"verdict": "PARTIAL_FIELD_SIGNAL", "average_field": 0.5, "exact_tuple": 0.0},
        tiny256_stage2={
            "verdict": "PARTIAL_REPORT_SURFACE_SIGNAL",
            "average_field": 0.7,
            "exact_tuple": 0.2,
            "value": 0.5,
            "date": 0.8,
            "subject": 0.8,
            "object": 0.8,
            "gap": 0.2,
            "seed_rows": [{"seed": 0, "average_field": 0.7, "exact_tuple": 0.2}],
        },
        controls={"rows": [{"control": "wrong_secret", "accept_rate": 0.0, "average_field": 0.0, "interpretation": "core_zero"}]},
        causality={"rows": [{"mutation": "report_swap", "field_drop": 0.5, "interpretation": "global_disruption"}]},
        verdicts=verdicts,
    )
    for name in rv.required_artifacts(stage3_run=False, neural_run=True):
        assert (tmp_path / name).exists(), f"missing artifact {name}"
    # final verdicts round-trip
    import json

    written = json.loads((tmp_path / "final_verdicts.json").read_text())
    assert written["REPORT_SURFACE_V0"] == "RENDERER_READY_SIGNAL_PARTIAL"
    # no-claims content present
    no_claims = (tmp_path / "no_claims.md").read_text().lower()
    assert "no cryptographic-security claim" in no_claims
    assert "synthetic report surface only" in no_claims
