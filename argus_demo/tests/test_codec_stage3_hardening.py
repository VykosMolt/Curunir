"""Fast contracts for the tiny_256 story-capsule v0 hardening pass.

These tests never run long CUDA training.  They exercise the clean-room package
boundary audit, the rule-based negative-control generation (no LLM), the
replication / negative-control / carrier-causality verdict logic, the v0 package
artifact schema and no-claims document, and the no-symbolic invariant.
"""

import inspect
import io
import json
import tokenize
from pathlib import Path

import pytest

from argus_capsules import codec_stage3_hardening as H
from argus_capsules.run_codec_stage2_field_head_study import (
    V0_HARDENING_REQUIRED_ARTIFACTS,
    V0_PACKAGE_REQUIRED_FILES,
    _aggregate_causality,
    _aggregate_strong_controls,
    _run_stage3_hardening,
    _write_cleanroom_package,
    _write_v0_package,
)
from argus_capsules.tiny_schema import FIELD_NAMES


@pytest.fixture(scope="session", autouse=True)
def _db_setup():
    yield


@pytest.fixture(autouse=True)
def clean_db():
    yield


def _code_only(source: str) -> str:
    pieces = []
    for tok in tokenize.generate_tokens(io.StringIO(source).readline):
        if tok.type in (tokenize.STRING, tokenize.COMMENT):
            continue
        pieces.append(tok.string)
    return " ".join(pieces).lower()


# --------------------------------------------------------------------------- #
# 1. clean-room package manifest excludes targets/debug/teacher/renderer.
# --------------------------------------------------------------------------- #


def test_cleanroom_package_manifest():
    # A clean package (only allowed capsule keys + decoder/tokenizer files).
    audit = H.cleanroom_boundary_audit(
        package_files=[
            "config.json",
            "decoder_weights.pt",
            "tokenizer.json",
            "schema.json",
            "open_contract.json",
            "capsules.jsonl",
            "cleanroom_package_manifest.json",
        ],
        capsule_keys=sorted(H.CLEANROOM_CAPSULE_ALLOWED_KEYS),
        open_used_inputs=["carrier_text", "secret", "tokenizer"],
        open_ran=True,
    )
    assert audit["TINY256_CLEANROOM_OPEN"] == "PASS"
    assert audit["leaked_capsule_keys"] == []
    assert audit["forbidden_files_found"] == []
    # the allowed capsule keys never include target/report/debug.
    for bad in ("fields", "target_json", "report", "case_id", "gold_slot_ids"):
        assert bad not in H.CLEANROOM_CAPSULE_ALLOWED_KEYS
        assert bad in H.CLEANROOM_FORBIDDEN_CAPSULE_KEYS


# --------------------------------------------------------------------------- #
# 2. clean-room open blocks forbidden inputs (target/teacher/renderer/encoder).
# --------------------------------------------------------------------------- #


def test_cleanroom_open_blocks_forbidden_inputs():
    leak = H.cleanroom_boundary_audit(
        package_files=["config.json", "capsules.jsonl"],
        capsule_keys=["carrier_text", "fields"],  # target fields leaked
        open_used_inputs=["carrier_text"],
        open_ran=True,
    )
    assert leak["TINY256_CLEANROOM_OPEN"] == "FAIL_TARGET_DEPENDENCY"

    teacher = H.cleanroom_boundary_audit(
        package_files=["config.json", "date_teacher_weights.pt"],
        capsule_keys=["carrier_text", "secret_text"],
        open_used_inputs=["carrier_text"],
        open_ran=True,
    )
    assert teacher["TINY256_CLEANROOM_OPEN"] == "FAIL_TEACHER_DEPENDENCY"

    renderer = H.cleanroom_boundary_audit(
        package_files=["config.json", "renderer_state.json"],
        capsule_keys=["carrier_text", "secret_text"],
        open_used_inputs=["carrier_text"],
        open_ran=True,
    )
    assert renderer["TINY256_CLEANROOM_OPEN"] == "FAIL_RENDERER_OR_ENCODER_DEPENDENCY"

    used_forbidden = H.cleanroom_boundary_audit(
        package_files=["config.json", "capsules.jsonl"],
        capsule_keys=["carrier_text", "secret_text"],
        open_used_inputs=["carrier_text", "date_teacher"],
        open_ran=True,
    )
    assert used_forbidden["TINY256_CLEANROOM_OPEN"] == "FAIL_OTHER_BOUNDARY_VIOLATION"

    not_run = H.cleanroom_boundary_audit(
        package_files=["config.json", "capsules.jsonl"],
        capsule_keys=["carrier_text", "secret_text"],
        open_used_inputs=["carrier_text"],
        open_ran=False,
    )
    assert not_run["TINY256_CLEANROOM_OPEN"] == "READY_NOT_RUN"


# --------------------------------------------------------------------------- #
# 3. reduced-package open contract: allowed inputs are sufficient.
# --------------------------------------------------------------------------- #


def test_reduced_package_open_contract():
    # The clean-room open uses only allowed inputs.
    used = (
        "carrier_text",
        "secret",
        "salt",
        "version",
        "tokenizer",
        "frozen_stage1_text_to_slot_weights",
        "field_preservation_stage2_student_weights",
        "schema_hash_validation",
        "public_schema_definitions",
    )
    assert set(used) <= set(H.CLEANROOM_ALLOWED_INPUTS)
    assert set(used).isdisjoint(set(H.CLEANROOM_FORBIDDEN_INPUTS))
    # forbidden inputs include the seal-only objects.
    for bad in (
        "target_json",
        "original_report_json",
        "date_teacher",
        "teacher_logits",
        "renderer",
        "encoder",
        "remote_llm_api",
    ):
        assert bad in H.CLEANROOM_FORBIDDEN_INPUTS


# --------------------------------------------------------------------------- #
# 4. stronger negative controls generate deterministically (no remote LLM).
# --------------------------------------------------------------------------- #


def test_stronger_negative_control_generation():
    catalog = H.negative_control_catalog()
    # all required control names exist.
    for name in (
        "wrong_secret",
        "wrong_salt",
        "wrong_version",
        "near_secret_one_char_edit",
        "near_secret_two_char_edit",
        "random_story",
        "same_distribution_impostor_story",
        "same_theme_impostor_story",
        "same_plot_different_entities_story",
        "story_swap_between_capsules",
        "paragraph_shuffle",
        "sentence_shuffle",
        "paragraph_delete",
        "sentence_delete",
        "number_date_token_edit",
        "local_word_edit",
        "local_synonym_rewrite",
        "light_paraphrase",
        "heavy_paraphrase",
    ):
        assert name in catalog

    secret = "alpha-bravo-charlie-2026"
    e1 = H.near_secret_edit(secret, n_chars=1)
    e2 = H.near_secret_edit(secret, n_chars=2)
    assert e1 != secret and e2 != secret
    assert e1 == H.near_secret_edit(secret, n_chars=1)  # deterministic

    story = (
        "The first paragraph has two sentences. Here is the second one.\n\n"
        "A second paragraph mentions 2026 and a value.\n\n"
        "A third paragraph closes the tale quietly."
    )
    assert H.sentence_shuffle(story, seed=0) == H.sentence_shuffle(story, seed=0)
    assert H.sentence_delete(story, seed=1) != story
    assert "2027" in H.number_date_token_edit(story, seed=0)
    assert H.carrier_token_shuffle(story, seed=2) != story

    # No remote LLM anywhere in the hardening module.
    src = _code_only(inspect.getsource(H))
    for bad in ("openai", "anthropic", "requests.", "http://", "https://", "urllib", "socket"):
        assert bad not in src, bad


# --------------------------------------------------------------------------- #
# 5. negative-control verdict logic.
# --------------------------------------------------------------------------- #


def test_negative_control_verdict_logic():
    def row(name, sev, acc):
        return {"name": name, "severity": sev, "accept_rate": acc}

    all_zero = [row("wrong_secret", "severe", 0.0), row("light_paraphrase", "weak", 0.0)]
    assert H.strong_negative_control_verdict(all_zero) == "PASS_ALL_ZERO"

    weak_nonzero = [row("wrong_secret", "severe", 0.0), row("light_paraphrase", "weak", 0.03)]
    assert H.strong_negative_control_verdict(weak_nonzero) == "PARTIAL_NONZERO_ACCEPT"

    severe_nonzero = [row("wrong_secret", "severe", 0.01), row("light_paraphrase", "weak", 0.0)]
    assert H.strong_negative_control_verdict(severe_nonzero) == "FAIL_SECURITY_CONTROLS"

    # A near-secret accept (core boundary all zero) is PARTIAL, not FAIL.
    near = [
        row("wrong_secret", "severe", 0.0),
        row("near_secret_one_char_edit", "near_secret", 0.10),
    ]
    assert H.strong_negative_control_verdict(near) == "PARTIAL_NONZERO_ACCEPT"
    assert H.negative_controls_all_severe_zero(near) is True

    assert H.strong_negative_control_verdict([]) == "FAIL_SECURITY_CONTROLS"
    # the PASS_AUTH_ZERO_FIELD_DECODE_NONZERO verdict is a valid v0 outcome.
    assert "PASS_AUTH_ZERO_FIELD_DECODE_NONZERO" in inspect.getsource(
        H.story_capsule_v0_verdict
    )


# --------------------------------------------------------------------------- #
# 6. carrier-causality verdict logic.
# --------------------------------------------------------------------------- #


def test_carrier_causality_verdict_logic():
    distributed = {
        "real_open_avg_field": 0.94,
        "carrier_randomized_drop": 0.85,
        "carrier_token_shuffle_drop": 0.80,
        "sentence_shuffle_drop": 0.60,
        "paragraph_shuffle_drop": 0.72,
        "story_swap_drop": 0.89,
        "same_distribution_impostor_drop": 0.83,
        "local_edit_drop": 0.05,
        "value_sentence_delete_drop": 0.10,
        "irrelevant_sentence_delete_drop": 0.02,
    }
    assert H.carrier_causality_verdict(distributed) == "PASS_DISTRIBUTED"

    strong = dict(distributed)
    strong.update(
        {"local_edit_drop": 0.30, "value_sentence_delete_drop": 0.35,
         "irrelevant_sentence_delete_drop": 0.25}
    )
    assert H.carrier_causality_verdict(strong) == "PASS_STRONG"

    weak = {
        "real_open_avg_field": 0.94,
        "carrier_randomized_drop": 0.25,
        "story_swap_drop": 0.22,
        "local_edit_drop": 0.02,
    }
    assert H.carrier_causality_verdict(weak) == "PARTIAL_WEAK_LOCALITY"

    flat = {
        "real_open_avg_field": 0.94,
        "carrier_randomized_drop": 0.05,
        "story_swap_drop": 0.03,
    }
    assert H.carrier_causality_verdict(flat) == "FAIL_STORY_NOT_CAUSAL"


# --------------------------------------------------------------------------- #
# 7. v0 package artifact schema.
# --------------------------------------------------------------------------- #


def _crafted_v0_inputs():
    rep_rows = [
        {"seed": 0, "exact_tuple": 0.73, "average_field": 0.95, "value": 0.84, "date": 0.98,
         "canonical_hash_match": 0.73, "schema_validation": 0.73, "subject": 0.95,
         "predicate": 0.93, "object": 0.97, "confidence": 0.98, "source_type": 0.98},
        {"seed": 2, "exact_tuple": 0.66, "average_field": 0.93, "value": 0.82, "date": 0.91,
         "canonical_hash_match": 0.66, "schema_validation": 0.66, "subject": 0.96,
         "predicate": 0.92, "object": 0.97, "confidence": 0.98, "source_type": 0.98},
    ]
    replication = H.aggregate_replication(rep_rows)
    strong_rows = [
        {"name": "wrong_secret", "severity": "severe", "accept_rate": 0.0,
         "hash_match_rate": 0.0, "schema_pass_rate": 0.0, "avg_field_vs_target": 0.1,
         "exact_tuple_rate": 0.0, "field_drop": 0.84, "failure_mode": "rejection"},
    ]
    causality = {
        "real_open_avg_field": 0.94,
        "carrier_randomized_avg_field": 0.09, "carrier_randomized_drop": 0.85,
        "story_swap_avg_field": 0.05, "story_swap_drop": 0.89,
        "local_edit_avg_field": 0.89, "local_edit_drop": 0.05,
    }
    exact_diag = {"TINY256_EXACT_LIMITER": "VALUE_FIELD",
                  "most_common_wrong_field": "value", "one_field_wrong_rate": 0.8,
                  "multi_field_wrong": 5, "hash_failures_one_field_rate": 0.7,
                  "value_failed_top3_rate": 0.6, "verifier_plausible_for_value": True}
    verdicts = {
        "ARGUS_CAPSULE_CODEC_TINY256_STAGE3_SMOKE": "PASS_WEAK_SMOKE",
        "TINY256_STAGE3_REPLICATION": "PASS_STABLE_3SEED",
        "TINY256_CARRIER_CAUSALITY": "PASS_DISTRIBUTED",
        "negatives_all_zero": True,
    }
    return verdicts, replication, rep_rows, strong_rows, causality, exact_diag


def test_v0_package_artifact_schema(tmp_path):
    for name in (
        "README_V0.md", "verdicts.json", "metrics_summary.json",
        "replication_summary.csv", "negative_control_summary.csv",
        "carrier_causality_summary.csv", "cleanroom_boundary_audit.json",
        "open_contract.json", "model_manifest.json", "artifact_manifest.json",
        "known_limitations.md", "reproduction_commands.sh", "no_claims.md",
    ):
        assert name in V0_PACKAGE_REQUIRED_FILES
    for name in (
        "config.json", "hardening_summary.md", "replication_metrics.json",
        "cleanroom_boundary_audit.json", "strong_negative_control_table.csv",
        "carrier_causality_table.csv", "exact_limiter_diagnostics.json",
        "final_verdicts.json",
    ):
        assert name in V0_HARDENING_REQUIRED_ARTIFACTS

    verdicts, replication, rep_rows, strong_rows, causality, exact_diag = (
        _crafted_v0_inputs()
    )
    _write_v0_package(
        v0_dir=tmp_path,
        verdicts=verdicts,
        replication=replication,
        rep_rows=rep_rows,
        strong_rows=strong_rows,
        causality=causality,
        cleanroom_audit={"TINY256_CLEANROOM_OPEN": "PASS"},
        open_contract={"auth_threshold": 0.5, "secret_threshold": 0.5},
        best_config={"head": "value_multi_pointer_pooler", "loss": "date_feature_distillation",
                     "stage2_module_class": "FieldPreservationModule"},
        exact_diag=exact_diag,
    )
    for name in V0_PACKAGE_REQUIRED_FILES:
        assert (tmp_path / name).exists(), name


# --------------------------------------------------------------------------- #
# 8. no symbolic inverse in the v0 path.
# --------------------------------------------------------------------------- #


def test_no_symbolic_inverse_in_v0_path():
    code = _code_only(inspect.getsource(H))
    code += _code_only(inspect.getsource(_run_stage3_hardening))
    code += _code_only(inspect.getsource(_write_cleanroom_package))
    forbidden = (
        "lookup_table",
        "nearest_train",
        "nearest_neighbor",
        "inverse_grammar",
        "symbolic_parse",
        "base64",
        "reversible_template",
        "correct_date",
        "fix_date",
        "date_correction",
        "openai",
        "anthropic",
    )
    for token in forbidden:
        assert token not in code, token


# --------------------------------------------------------------------------- #
# 9. replication verdict logic.
# --------------------------------------------------------------------------- #


def test_stage3_replication_verdict_logic():
    def mk(seed, ex, av, val=0.84, dt=0.95):
        return {"seed": seed, "exact_tuple": ex, "average_field": av, "value": val,
                "date": dt, "canonical_hash_match": ex, "schema_validation": ex,
                "subject": 0.95, "predicate": 0.93, "object": 0.97,
                "confidence": 0.98, "source_type": 0.98}

    stable = [mk(0, 0.73, 0.95), mk(1, 0.55, 0.90), mk(2, 0.66, 0.93)]
    assert H.stage3_replication_verdict(
        stable, negatives_all_zero=True, open_boundary_clean=True
    ) == "PASS_STABLE_3SEED"

    soft = [mk(0, 0.73, 0.95), mk(1, 0.10, 0.82), mk(2, 0.66, 0.93)]
    assert H.stage3_replication_verdict(
        soft, negatives_all_zero=True, open_boundary_clean=True
    ) == "PASS_BEST2_ONLY_SEED1_SOFT"

    sensitive = [mk(0, 0.30, 0.70), mk(1, 0.10, 0.62), mk(2, 0.40, 0.74)]
    assert H.stage3_replication_verdict(
        sensitive, negatives_all_zero=True, open_boundary_clean=True
    ) == "PARTIAL_SEED_SENSITIVE"

    # security / boundary failure dominates.
    assert H.stage3_replication_verdict(
        stable, negatives_all_zero=False, open_boundary_clean=True
    ) == "FAIL_NOT_REPLICATED"
    assert H.stage3_replication_verdict(
        stable, negatives_all_zero=True, open_boundary_clean=False
    ) == "FAIL_NOT_REPLICATED"


# --------------------------------------------------------------------------- #
# 10. no-claims document present + complete.
# --------------------------------------------------------------------------- #


def test_no_claims_document_present(tmp_path):
    verdicts, replication, rep_rows, strong_rows, causality, exact_diag = (
        _crafted_v0_inputs()
    )
    _write_v0_package(
        v0_dir=tmp_path,
        verdicts=verdicts,
        replication=replication,
        rep_rows=rep_rows,
        strong_rows=strong_rows,
        causality=causality,
        cleanroom_audit={"TINY256_CLEANROOM_OPEN": "PASS"},
        open_contract={},
        best_config={"head": "value_multi_pointer_pooler", "loss": "date_feature_distillation"},
        exact_diag=exact_diag,
    )
    text = (tmp_path / "no_claims.md").read_text(encoding="utf-8").lower()
    assert "no cryptographic-security claim" in text
    assert "no full argus-codec claim" in text
    assert "no operational deception claim" in text
    assert "no real intelligence data claim" in text
    assert "no public release recommendation" in text
    readme = (tmp_path / "README_V0.md").read_text(encoding="utf-8").lower()
    assert "synthetic" in readme
    assert "not a full argus codec" in readme
    assert "not cryptographic security" in readme


# --------------------------------------------------------------------------- #
# aggregation sanity.
# --------------------------------------------------------------------------- #


def test_aggregate_controls_and_causality():
    per_seed = {
        0: {
            "control_rows": [
                {"name": "wrong_secret", "severity": "severe", "accept_rate": 0.0,
                 "hash_match_rate": 0.0, "schema_pass_rate": 0.0,
                 "avg_field_vs_target": 0.1, "exact_tuple_rate": 0.0},
            ],
            "causality_avg": {"carrier_randomized": 0.09, "local_edit": 0.89},
        },
        2: {
            "control_rows": [
                {"name": "wrong_secret", "severity": "severe", "accept_rate": 0.0,
                 "hash_match_rate": 0.0, "schema_pass_rate": 0.0,
                 "avg_field_vs_target": 0.12, "exact_tuple_rate": 0.0},
            ],
            "causality_avg": {"carrier_randomized": 0.11, "local_edit": 0.87},
        },
    }
    rows = _aggregate_strong_controls(per_seed, real_avg_mean=0.94)
    assert rows[0]["accept_rate"] == 0.0
    assert rows[0]["failure_mode"] == "rejection"
    assert H.negative_controls_all_severe_zero(rows) is True
    caus = _aggregate_causality(per_seed, real_avg_mean=0.94)
    assert caus["carrier_randomized_drop"] == pytest.approx(0.94 - 0.10, abs=1e-6)
    assert "story_ignored_decoder_avg_field" in caus
