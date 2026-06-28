"""Fast contracts for archive hard gating and codec replication."""

import inspect
from pathlib import Path

import pytest

from argus_capsules import (
    acceptance,
    archive_auth,
    codec_replication,
    codec_text_to_slot,
    run_codec_text_to_slot_bridge_repl,
)
from argus_capsules.acceptance import (
    calibrate_secret_validity_threshold,
    hard_secret_gate_acceptance,
)
from argus_capsules.codec_field_diagnostics import (
    compute_field_diagnostics,
)
from argus_capsules.codec_narrative import codec_narrative_metrics
from argus_capsules.codec_replication import (
    aggregate_seed_metrics,
    repaired_valid_open_seed_pass,
    repaired_valid_open_verdict,
    tiny256_gate_open,
    tiny64_train64_seed_pass,
    tiny64_train64_verdict,
    tiny64_replication_verdict,
    valid_open_beats_required_baselines,
)
from argus_capsules.codec_text_to_slot import (
    REQUIRED_STAGE3_NEGATIVE_KEYS,
    tiny256_bridge_verdict,
    tiny64_seed_pass,
)
from argus_capsules.control_experiments import (
    build_synthetic_examples,
)
from argus_capsules.run_archive_hardening import (
    ArchiveHardeningConfig,
    _wrong_secret_sweep_conditions,
    archive_strict_auth_verdict,
)
from argus_capsules.run_codec_text_to_slot_bridge_repl import (
    build_replication_config,
    build_replication_run_id,
    compute_value_diagnostics,
    replication_artifact_paths,
)
from argus_capsules.tiny_schema import get_tiny_schema_spec
from argus_capsules.tokenizer import SimpleTokenizer


@pytest.fixture(scope="session", autouse=True)
def _db_setup():
    yield


@pytest.fixture(autouse=True)
def clean_db():
    yield


def test_hard_secret_gate_acceptance_logic():
    high_auth_low_secret = hard_secret_gate_acceptance(
        story_auth_score=0.9,
        story_auth_threshold=0.5,
        secret_validity_score=0.1,
        secret_validity_threshold=0.5,
        validation_pass=True,
        expected_hash_pass=True,
    )
    assert not high_auth_low_secret.accepted
    low_auth_high_secret = hard_secret_gate_acceptance(
        story_auth_score=0.1,
        story_auth_threshold=0.5,
        secret_validity_score=0.9,
        secret_validity_threshold=0.5,
        validation_pass=True,
        expected_hash_pass=True,
    )
    assert not low_auth_high_secret.accepted
    accepted = hard_secret_gate_acceptance(
        story_auth_score=0.9,
        story_auth_threshold=0.5,
        secret_validity_score=0.9,
        secret_validity_threshold=0.5,
        validation_pass=True,
        expected_hash_pass=True,
    )
    assert accepted.accepted
    assert not hard_secret_gate_acceptance(
        story_auth_score=0.9,
        story_auth_threshold=0.5,
        secret_validity_score=0.9,
        secret_validity_threshold=0.5,
        validation_pass=False,
        expected_hash_pass=True,
    ).accepted


def test_wrong_secret_sweep_config_builds():
    config = ArchiveHardeningConfig(
        n_examples=128,
        hard_secret_gate=True,
        wrong_secret_sweep=32,
    )
    assert config.wrong_secret_sweep == 32
    assert "hard-secret-gate-sweep32" in config.run_id
    examples = build_synthetic_examples(4, 3)
    rows = _wrong_secret_sweep_conditions(
        example_index=0,
        attempt_count=config.wrong_secret_sweep,
        examples=examples,
        tokenizer=SimpleTokenizer(),
        checkpoint_config={
            "num_secrets": 32,
            "num_secret_tokens": 16,
        },
        seed=11,
    )
    assert len(rows) == 34
    assert sum(
        category
        in (
            "near_secret",
            "other_item_secret",
            "random_wrong_secret",
        )
        for category, _ in rows
    ) == 32
    categories = {category for category, _ in rows}
    assert categories == {
        "near_secret",
        "other_item_secret",
        "random_wrong_secret",
        "wrong_salt",
        "wrong_version",
    }


def test_secret_threshold_calibration():
    separated = calibrate_secret_validity_threshold(
        [0.90, 0.92, 0.95, 0.97, 0.99],
        [0.01, 0.02],
        [0.03],
        [0.04],
    )
    assert separated["secret_validity_distributions_separable"]
    assert 0.04 < separated["secret_threshold"] < 0.90
    assert separated["secret_threshold_warning"] == ""

    overlap = calibrate_secret_validity_threshold(
        [0.40, 0.50, 0.60],
        [0.55],
        [0.20],
        [0.30],
    )
    assert not overlap["secret_validity_distributions_separable"]
    assert overlap["secret_threshold"] > 0.55
    assert overlap["secret_threshold_warning"]


def _archive_passing_metrics():
    metrics = {
        "positive_validation_pass_rate": 1.0,
        "positive_auth_accept_rate": 1.0,
        "light_paraphrase_any_valid_accept_rate": 0.0,
        "wrong_secret_sweep_any_valid_accept_rate": 0.0,
    }
    for control in (
        "heavy_paraphrase",
        "full_paraphrase",
        "one_paragraph_paraphrase",
        "local_synonym_edit",
        "near_edit_1_word",
        "near_edit_5_words",
        "same_theme_impostor",
        "same_plot_different_details",
        "shuffled_paragraphs",
        "deleted_paragraph",
        "inserted_paragraph",
        "story_a_secret_b",
        "wrong_secret",
        "wrong_salt",
        "wrong_version",
        "multi_carrier_impostor",
    ):
        metrics[f"{control}_any_valid_accept_rate"] = 0.0
    return metrics


def test_archive_n128_hard_gate_verdict_logic():
    metrics = _archive_passing_metrics()
    assert archive_strict_auth_verdict(128, metrics) == "PASS_N128"
    metrics["wrong_secret_sweep_any_valid_accept_rate"] = 0.001
    assert (
        archive_strict_auth_verdict(128, metrics)
        == "PARTIAL_ATTACK_SURFACE_N128"
    )
    metrics["positive_validation_pass_rate"] = 0.99
    assert (
        archive_strict_auth_verdict(128, metrics)
        == "FAIL_POSITIVES_REGRESSED_N128"
    )


def _passing_seed(value: float = 0.8):
    metrics = {
        "stage1_heldout_exact_sequence_accuracy": 0.8,
        "stage1_heldout_per_slot_accuracy_mean": 0.95,
        "stage1_slot_accuracy_drop_when_story_randomized": 0.8,
        "stage1_slot_accuracy_drop_when_story_swapped": 0.8,
        "stage2_text_to_slot_to_field_exact_tuple_accuracy": 0.6,
        "stage2_text_to_slot_to_field_average_field_accuracy": 0.9,
        "stage2_field_accuracy_drop_when_story_randomized": 0.7,
        "stage2_field_accuracy_drop_when_story_swapped": 0.7,
        "stage3_heldout_exact_tuple_accuracy": 0.6,
        "stage3_heldout_average_field_accuracy": value,
        "stage3_canonical_hash_match_rate": 0.6,
        "stage3_schema_validation_pass_rate": 0.6,
        "stage3_field_accuracy_drop_when_carrier_randomized": 0.5,
        "stage3_field_accuracy_drop_when_story_swapped": 0.5,
    }
    for key in REQUIRED_STAGE3_NEGATIVE_KEYS:
        metrics[key] = 0.0
    return metrics


def test_codec_replication_aggregate_metrics():
    aggregate = aggregate_seed_metrics(
        [_passing_seed(0.8), _passing_seed(1.0)]
    )
    assert aggregate["seed_count"] == 2
    assert aggregate["stage3_heldout_average_field_mean"] == 0.9
    assert aggregate["stage3_heldout_average_field_std"] == pytest.approx(
        0.1
    )
    assert aggregate["stage3_any_negative_accept_max"] == 0.0


def test_codec_replication_aggregate_ignores_unrun_stage3():
    reached_stage3 = _passing_seed(0.85)
    stopped_at_stage2 = {
        key: value
        for key, value in reached_stage3.items()
        if not key.startswith("stage3_")
    }
    aggregate = aggregate_seed_metrics(
        [reached_stage3, stopped_at_stage2]
    )
    assert aggregate["stage3_heldout_average_field_mean"] == 0.85
    assert aggregate["stage3_heldout_average_field_run_count"] == 1
    assert aggregate["stage3_negative_controls_run_count"] == 1


def test_codec_replication_marks_unrun_negatives_null():
    aggregate = aggregate_seed_metrics([{}])
    assert aggregate["stage3_any_negative_accept_max"] is None
    assert aggregate["stage3_any_negative_accept_mean"] is None
    assert aggregate["stage3_negative_controls_run_count"] == 0


def test_codec_tiny64_replication_verdict_logic():
    assert tiny64_replication_verdict(4, 5) == "PASS_STABLE"
    assert (
        tiny64_replication_verdict(2, 5)
        == "PARTIAL_SEED_SENSITIVE"
    )
    assert tiny64_replication_verdict(1, 5) == "FAIL_NOT_STABLE"
    assert (
        tiny64_replication_verdict(3, 3)
        == "PASS_STABLE_PRELIMINARY"
    )


def test_tiny64_train64_verdict_logic():
    assert tiny64_train64_verdict(5, 5) == "PASS_STABLE_5OF5"
    assert tiny64_train64_verdict(4, 5) == "PASS_STABLE_4OF5"
    assert (
        tiny64_train64_verdict(3, 5)
        == "PARTIAL_SEED_SENSITIVE"
    )
    assert (
        tiny64_train64_verdict(2, 5)
        == "PARTIAL_SEED_SENSITIVE"
    )
    assert tiny64_train64_verdict(1, 5) == "FAIL_NOT_STABLE"
    assert tiny64_train64_verdict(0, 5) == "FAIL_NOT_STABLE"


def test_tiny256_gated_by_tiny64():
    assert not tiny256_gate_open("PARTIAL_SEED_SENSITIVE")
    assert not tiny256_gate_open("FAIL_NOT_STABLE")
    assert tiny256_gate_open("PASS_STABLE_4OF5")
    assert tiny256_gate_open("PASS_STABLE_5OF5")


def test_tiny256_gated_by_repaired_tiny64():
    assert not tiny256_gate_open("PARTIAL_SEED_SENSITIVE")
    assert not tiny256_gate_open("FAIL_SECURITY_CONTROLS")
    assert tiny256_gate_open("PASS_STABLE_4OF5")
    assert tiny256_gate_open("PASS_STABLE_5OF5")


def _passing_train64_seed():
    metrics = _passing_seed(0.90)
    metrics.update(
        {
            "stage1_heldout_per_slot_accuracy_mean": 0.999,
            "stage2_text_to_slot_to_field_exact_tuple_accuracy": 0.60,
            "stage2_text_to_slot_to_field_average_field_accuracy": 0.90,
            "majority_class_baseline": 0.12,
            "random_field_baseline": 0.12,
            "story_ignored_decoder_baseline": 0.12,
            "secret_only_decoder_baseline": 0.22,
        }
    )
    return metrics


def _passing_repaired_seed():
    metrics = _passing_train64_seed()
    metrics.update(
        {
            "stage2_text_to_slot_to_field_exact_tuple_accuracy": 0.80,
            "stage2_text_to_slot_to_field_average_field_accuracy": 0.96,
            "stage2_subject_accuracy": 0.95,
            "stage2_object_accuracy": 0.95,
            "stage2_date_accuracy": 0.95,
            "stage3_heldout_exact_tuple_accuracy": 0.80,
            "stage3_heldout_average_field_accuracy": 0.92,
            "stage3_canonical_hash_match_rate": 0.80,
            "stage3_schema_validation_pass_rate": 0.80,
        }
    )
    return metrics


def test_negative_accept_blocks_seed_pass():
    metrics = _passing_train64_seed()
    assert tiny64_train64_seed_pass(metrics)
    metrics["stage3_wrong_version_any_valid_accept_rate"] = 0.01
    assert not tiny64_train64_seed_pass(metrics)


def test_repaired_valid_open_verdict_logic():
    passing = [_passing_repaired_seed() for _ in range(5)]
    assert repaired_valid_open_seed_pass(passing[0])
    assert repaired_valid_open_verdict(passing) == "PASS_STABLE_5OF5"
    one_fail = [dict(row) for row in passing]
    one_fail[0]["stage3_heldout_exact_tuple_accuracy"] = 0.10
    assert repaired_valid_open_verdict(one_fail) == "PASS_STABLE_4OF5"
    three_fail = [dict(row) for row in passing]
    for row in three_fail[:3]:
        row["stage3_heldout_exact_tuple_accuracy"] = 0.10
    assert repaired_valid_open_verdict(three_fail) == "PARTIAL_SEED_SENSITIVE"
    four_fail = [dict(row) for row in passing]
    for row in four_fail[:4]:
        row["stage3_heldout_exact_tuple_accuracy"] = 0.10
    assert repaired_valid_open_verdict(four_fail) == "FAIL_NOT_STABLE"
    bad_negative = [dict(row) for row in passing]
    bad_negative[0]["stage3_wrong_secret_any_valid_accept_rate"] = 0.01
    assert repaired_valid_open_verdict(bad_negative) == "FAIL_SECURITY_CONTROLS"


def test_stage3_valid_open_requires_hash_schema():
    metrics = _passing_repaired_seed()
    assert repaired_valid_open_seed_pass(metrics)
    metrics["stage3_canonical_hash_match_rate"] = 0.0
    assert not repaired_valid_open_seed_pass(metrics)
    metrics = _passing_repaired_seed()
    metrics["stage3_schema_validation_pass_rate"] = 0.0
    assert not repaired_valid_open_seed_pass(metrics)


def test_valid_open_beats_required_baselines():
    metrics = _passing_train64_seed()
    assert valid_open_beats_required_baselines(metrics)
    metrics["secret_only_decoder_baseline"] = 0.91
    assert not valid_open_beats_required_baselines(metrics)
    assert not tiny64_train64_seed_pass(metrics)


def test_repaired_head_cli_flags():
    config = build_replication_config(
        stage2_input_mode="recovered_slots_plus_confidence",
        stage2_head="date_decomposed",
        stage2_loss="date_decomposed",
    )
    assert config.stage2_input_mode == "recovered_slots_plus_confidence"
    assert config.stage2_head == "date_decomposed"
    assert config.stage2_loss == "date_decomposed"


def test_field_diagnostics_computation():
    spec = get_tiny_schema_spec("tiny_64")
    targets = [
        {
            "subject": "subject_00",
            "predicate": "predicate_00",
            "object": "object_00",
            "date": "2026-01-01",
            "value": "value_00",
            "confidence": "low",
            "source_type": "synthetic_note",
        },
        {
            "subject": "subject_01",
            "predicate": "predicate_01",
            "object": "object_01",
            "date": "2026-01-02",
            "value": "value_01",
            "confidence": "medium",
            "source_type": "synthetic_diary",
        },
        {
            "subject": "subject_02",
            "predicate": "predicate_02",
            "object": "object_02",
            "date": "2026-01-03",
            "value": "value_02",
            "confidence": "high",
            "source_type": "synthetic_log",
        },
    ]
    predictions = [
        dict(targets[0]),
        {**targets[1], "subject": "subject_00"},
        {
            **targets[2],
            "object": "object_01",
            "date": "2026-01-05",
        },
    ]
    diagnostics = compute_field_diagnostics(
        targets, predictions, spec
    )
    assert diagnostics["per_field"]["subject"][
        "field_accuracy"
    ] == pytest.approx(2 / 3)
    assert diagnostics["per_field"]["subject"][
        "top_confusions"
    ][0]["predicted"] == "subject_00"
    failures = diagnostics["tuple_failures"]
    assert failures["one_field_wrong_rate"] == pytest.approx(1 / 3)
    assert failures["two_fields_wrong_rate"] == pytest.approx(1 / 3)
    assert failures["three_plus_fields_wrong_rate"] == 0.0


def test_value_diagnostics():
    base = {
        "subject": "subject_00",
        "predicate": "predicate_00",
        "object": "object_00",
        "date": "2026-01-01",
        "confidence": "low",
        "source_type": "synthetic_note",
    }
    rows = [
        {
            "target_fields": {**base, "value": "value_00"},
            "predicted_fields": {**base, "value": "value_01"},
        },
        {
            "target_fields": {
                **base,
                "subject": "subject_01",
                "object": "object_02",
                "value": "value_00",
            },
            "predicted_fields": {
                **base,
                "subject": "subject_01",
                "object": "object_02",
                "value": "value_02",
            },
        },
        {
            "target_fields": {**base, "value": "value_03"},
            "predicted_fields": {**base, "value": "value_03"},
        },
    ]
    diagnostics = compute_value_diagnostics(rows)
    assert diagnostics["value_accuracy"] == pytest.approx(1 / 3)
    assert diagnostics["value_error_when_all_other_fields_correct_count"] == 2
    assert diagnostics["value_top_confusions"][0]["target"] == "value_00"
    assert diagnostics["value_confusion_by_subject"][0]["count"] >= 1


def test_date_near_miss_metrics():
    spec = get_tiny_schema_spec("tiny_64")
    base = {
        "subject": "subject_00",
        "predicate": "predicate_00",
        "object": "object_00",
        "value": "value_00",
        "confidence": "low",
        "source_type": "synthetic_note",
    }
    targets = [
        {**base, "date": "2026-01-01"},
        {**base, "date": "2026-01-10"},
        {**base, "date": "2026-01-20"},
    ]
    predictions = [
        {**base, "date": "2026-01-01"},
        {**base, "date": "2026-01-11"},
        {**base, "date": "2026-01-28"},
    ]
    date_metrics = compute_field_diagnostics(
        targets, predictions, spec
    )["date"]
    assert date_metrics["date_exact_accuracy"] == pytest.approx(1 / 3)
    assert date_metrics["date_year_accuracy"] == 1.0
    assert date_metrics["date_month_accuracy"] == 1.0
    assert date_metrics["date_day_accuracy"] == pytest.approx(1 / 3)
    assert date_metrics["date_absolute_day_error_mean"] == 3.0
    assert date_metrics["date_absolute_day_error_median"] == 1.0
    assert date_metrics["date_absolute_day_error_p90"] == 8.0
    assert date_metrics["date_near_miss_within_1_day"] == pytest.approx(
        2 / 3
    )
    assert date_metrics["date_near_miss_within_7_days"] == pytest.approx(
        2 / 3
    )


def test_forbidden_terms_in_carrier():
    metrics = codec_narrative_metrics(
        "A fox crossed the valley. The hidden message remained."
    )
    assert metrics["codec_forbidden_term_rate"] == 1.0
    assert not metrics["codec_narrative_coherence_pass"]


def test_artifact_schema_contains_field_diagnostics(tmp_path):
    paths = replication_artifact_paths(Path(tmp_path))
    assert set(paths) == {
        "baseline_table",
        "field_diagnostics_csv",
        "field_diagnostics_json",
        "failure_analysis",
        "narrative_quality",
        "value_diagnostics",
    }
    config = build_replication_config(
        schema="tiny_64",
        train_examples=64,
        heldout_examples=128,
        seeds=[0, 1, 2, 3, 4],
    )
    assert build_replication_run_id(config) == (
        "tiny_64-train64-seeds0-1-2-3-4-heldout128-e300"
    )


def test_artifact_schema_for_repaired_valid_open(tmp_path):
    paths = replication_artifact_paths(Path(tmp_path))
    for name in (
        "field_diagnostics_csv",
        "field_diagnostics_json",
        "value_diagnostics",
        "baseline_table",
        "narrative_quality",
        "failure_analysis",
    ):
        assert name in paths


def _tiny256_metrics():
    metrics = {
        "schema": "tiny_256",
        "stage1_heldout_per_slot_accuracy_mean": 0.80,
        "stage1_slot_accuracy_drop_when_story_randomized": 0.4,
        "stage1_slot_accuracy_drop_when_story_swapped": 0.4,
        "stage2_text_to_slot_to_field_average_field_accuracy": 0.80,
        "stage2_field_accuracy_drop_when_story_randomized": 0.3,
        "stage2_field_accuracy_drop_when_story_swapped": 0.3,
        "stage3_heldout_exact_tuple_accuracy": 0.10,
        "stage3_heldout_average_field_accuracy": 0.75,
        "stage3_field_accuracy_drop_when_carrier_randomized": 0.3,
        "stage3_field_accuracy_drop_when_story_swapped": 0.3,
        "nearest_training_baseline_exact_field_tuple_accuracy": 0.0,
        "nearest_training_baseline_average_field_accuracy": 0.5,
        "story_ignored_baseline_average_field_accuracy": 0.15,
        "secret_only_decoder_baseline_average_field_accuracy": 0.2,
    }
    for key in REQUIRED_STAGE3_NEGATIVE_KEYS:
        metrics[key] = 0.0
    return metrics


def test_codec_tiny256_verdict_logic():
    metrics = _tiny256_metrics()
    assert tiny256_bridge_verdict(metrics) == "PASS_WEAK_SCALE_SIGNAL"
    metrics["stage3_heldout_exact_tuple_accuracy"] = 0.0
    assert tiny256_bridge_verdict(metrics) == "PARTIAL_FIELD_SIGNAL"
    metrics = _tiny256_metrics()
    metrics["stage1_heldout_per_slot_accuracy_mean"] = 0.2
    assert tiny256_bridge_verdict(metrics) == "FAIL_TEXT_TO_SLOT_SCALE"
    metrics = _tiny256_metrics()
    metrics["stage3_tamper_any_valid_accept_rate"] = 0.01
    assert tiny256_bridge_verdict(metrics) == "FAIL_SECURITY_CONTROLS"


def test_negative_accept_nonzero_blocks_pass():
    metrics = _passing_seed()
    assert tiny64_seed_pass(metrics)
    metrics["stage3_wrong_secret_any_valid_accept_rate"] = 0.001
    assert not tiny64_seed_pass(metrics)


def test_replication_config_builds():
    config = build_replication_config(
        schema="tiny_64", seeds=[0, 1, 2, 3, 4]
    )
    assert config.seeds == [0, 1, 2, 3, 4]
    assert config.run_stage3_valid_open


def test_no_symbolic_inverse():
    source = (
        inspect.getsource(acceptance)
        + inspect.getsource(archive_auth)
        + inspect.getsource(codec_replication)
        + inspect.getsource(codec_text_to_slot)
        + inspect.getsource(run_codec_text_to_slot_bridge_repl)
    ).lower()
    forbidden = (
        "story_to_" + "report",
        "decode_story_" + "to_case",
        "inverse_" + "story",
        "base64." + "b64decode",
        "bytes." + "fromhex",
        "carrier_" + "lookup",
    )
    for value in forbidden:
        assert value not in source
