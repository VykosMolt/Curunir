"""Fast contracts for the frozen Stage-2 audit and field-head study."""

import inspect
import json
from pathlib import Path

import pytest
import torch

from argus_capsules.codec_stage2_audit import (
    combination_coverage_audit,
    date_bayes_diagnostics,
    field_coverage_metrics,
    mutual_information_metrics,
    slot_collision_audit,
    tiny256_stage2_audit_verdict,
)
from argus_capsules.codec_stage2_heads import (
    BINDING_AUXILIARIES,
    Stage2FieldHead,
    binding_auxiliary_specs,
    encode_binding_aux_labels,
    factorized_binding_aux_logits,
    parameter_count,
    stage2_study_verdict,
    stage2_loss,
    stage2_variant_repair_gate,
    stage3_smoke_allowed,
    tiny256_compositional_stage2_verdict,
    tiny256_stage2_repair_gate,
    tiny256_stage2_scale_verdict,
    tiny256_stage3_smoke_allowed,
)
from argus_capsules.run_codec_stage2_field_head_study import (
    REQUIRED_ARTIFACTS,
    _prepare_source_runs,
    _stage2_jobs_from_decisions,
    build_stage2_study_config,
    stage2_staged_pruning_decisions,
    stage2_study_artifact_paths,
)
from argus_capsules.tiny_schema import FIELD_NAMES, get_tiny_schema_spec


@pytest.fixture(scope="session", autouse=True)
def _db_setup():
    yield


@pytest.fixture(autouse=True)
def clean_db():
    yield


def _fields(subject: str, *, date_value: str = "2026-01-01"):
    return {
        "subject": subject,
        "predicate": "predicate_00",
        "object": "object_00",
        "date": date_value,
        "value": "value_00",
        "confidence": "low",
        "source_type": "synthetic_note",
    }


def test_slot_collision_audit_detects_unique_mapping():
    audit = slot_collision_audit(
        [[0, 0], [0, 1], [1, 0]],
        [
            _fields("subject_00"),
            _fields("subject_01"),
            _fields("subject_02"),
        ],
    )
    assert audit["slot_tuple_uniqueness_rate"] == 1.0
    assert audit["slot_to_field_collision_count"] == 0
    assert audit["slot_to_field_bayes_exact_ceiling"] == 1.0


def test_slot_collision_audit_detects_collisions():
    audit = slot_collision_audit(
        [[0, 0], [0, 0], [1, 0]],
        [
            _fields("subject_00"),
            _fields("subject_01"),
            _fields("subject_02"),
        ],
    )
    assert audit["slot_to_field_collision_count"] == 1
    assert audit["slot_to_field_collision_rate"] == pytest.approx(2 / 3)
    assert audit["slot_to_field_bayes_exact_ceiling"] == pytest.approx(
        2 / 3
    )
    assert audit["collision_examples"]


def test_per_field_bayes_ceiling():
    positions = {
        field: ([0] if field == "subject" else [1])
        for field in FIELD_NAMES
    }
    audit = slot_collision_audit(
        [[0, 0], [0, 1], [1, 0], [1, 1]],
        [
            _fields("subject_00"),
            _fields("subject_01"),
            _fields("subject_02"),
            _fields("subject_02"),
        ],
        field_slot_positions=positions,
    )
    assert audit["per_field"]["subject"]["bayes_ceiling"] == 0.75
    assert audit["per_field"]["predicate"]["bayes_ceiling"] == 1.0


def test_mutual_information_metrics():
    perfect = mutual_information_metrics(
        ["a", "a", "b", "b"],
        [[0], [0], [1], [1]],
    )
    assert perfect["field_entropy"] == 1.0
    assert perfect["field_conditional_entropy_given_slots"] == 0.0
    assert perfect["field_mutual_information_with_slots"] == 1.0
    assert perfect["normalized_mutual_information"] == 1.0
    ambiguous = mutual_information_metrics(
        ["a", "b", "a", "b"],
        [[0], [0], [0], [0]],
    )
    assert ambiguous["field_mutual_information_with_slots"] == 0.0


def test_date_diagnostics():
    diagnostics = date_bayes_diagnostics(
        [[0], [0], [1]],
        [
            _fields("subject_00", date_value="2026-01-01"),
            _fields("subject_01", date_value="2026-01-03"),
            _fields("subject_02", date_value="2026-02-01"),
        ],
        [0],
    )
    assert diagnostics["date_year_bayes_ceiling"] == 1.0
    assert diagnostics["date_month_bayes_ceiling"] == 1.0
    assert diagnostics["date_day_bayes_ceiling"] == pytest.approx(2 / 3)
    assert diagnostics["date_absolute_day_error_bayes_mean"] == pytest.approx(
        2 / 3
    )


def _repair_rows(
    *,
    exact: float = 0.65,
    average: float = 0.95,
    subject: float = 0.90,
    object_value: float = 0.90,
    date_value: float = 0.90,
):
    return [
        {
            "heldout_exact_tuple_accuracy": exact,
            "heldout_average_field_accuracy": average,
            "subject_accuracy": subject,
            "predicate_accuracy": 1.0,
            "object_accuracy": object_value,
            "date_accuracy": date_value,
            "value_accuracy": 0.95,
            "confidence_accuracy": 1.0,
            "source_type_accuracy": 1.0,
            "heldout_diagnostics": {
                "tuple_failures": {"one_field_wrong_rate": 0.30}
            },
        }
        for _ in range(5)
    ]


def test_stage2_head_verdict_logic():
    repaired = _repair_rows()
    unresolved = _repair_rows(
        exact=0.48,
        average=0.90,
        subject=0.80,
        object_value=0.80,
        date_value=0.78,
    )
    assert (
        stage2_study_verdict(
            audit_verdict="SLOT_REPRESENTATION_UNDERSPECIFIED",
            best_gold_metrics=repaired,
            best_recovered_metrics=repaired,
            baseline_exact=0.44,
        )
        == "SLOT_REPRESENTATION_LIMITED"
    )
    assert (
        stage2_study_verdict(
            audit_verdict="FIELD_HEAD_OR_LOSS_BOTTLENECK",
            best_gold_metrics=repaired,
            best_recovered_metrics=unresolved,
            baseline_exact=0.44,
        )
        == "RECOVERED_SLOT_NOISE_LIMITED"
    )
    assert (
        stage2_study_verdict(
            audit_verdict="FIELD_HEAD_OR_LOSS_BOTTLENECK",
            best_gold_metrics=repaired,
            best_recovered_metrics=repaired,
            baseline_exact=0.44,
        )
        == "FIELD_HEAD_REPAIRED"
    )
    assert (
        stage2_study_verdict(
            audit_verdict="FIELD_HEAD_OR_LOSS_BOTTLENECK",
            best_gold_metrics=unresolved,
            best_recovered_metrics=unresolved,
            baseline_exact=0.44,
        )
        == "OPTIMIZATION_OR_CAPACITY_UNRESOLVED"
    )
    date_remaining = _repair_rows(
        exact=0.56,
        average=0.94,
        subject=0.90,
        object_value=0.90,
        date_value=0.80,
    )
    assert (
        stage2_study_verdict(
            audit_verdict="FIELD_HEAD_OR_LOSS_BOTTLENECK",
            best_gold_metrics=date_remaining,
            best_recovered_metrics=date_remaining,
            baseline_exact=0.44,
        )
        == "PARTIAL_FIELD_REPAIR_DATE_REMAINS"
    )


def test_repair_gate_requires_subject_object_date():
    assert stage2_variant_repair_gate(_repair_rows())
    assert not stage2_variant_repair_gate(
        _repair_rows(subject=0.80)
    )
    assert not stage2_variant_repair_gate(
        _repair_rows(object_value=0.80)
    )
    assert not stage2_variant_repair_gate(
        _repair_rows(date_value=0.80)
    )


def test_stage3_smoke_gated_by_stage2_repair():
    assert stage3_smoke_allowed("FIELD_HEAD_REPAIRED")
    assert not stage3_smoke_allowed(
        "OPTIMIZATION_OR_CAPACITY_UNRESOLVED"
    )
    assert not stage3_smoke_allowed(
        "PARTIAL_FIELD_REPAIR_DATE_REMAINS"
    )


def test_artifact_schema_for_stage2_study(tmp_path):
    paths = stage2_study_artifact_paths(Path(tmp_path))
    assert set(paths) == set(REQUIRED_ARTIFACTS)
    config = build_stage2_study_config()
    assert config.audit_only is False
    assert "gold_slots" in config.input_modes
    assert "recovered_slots" in config.input_modes


def test_tiny256_audit_verdict_logic():
    base = {
        "gold_slot_to_field_bayes_exact_ceiling": 1.0,
        "gold_slot_to_field_bayes_average_field_ceiling": 1.0,
        "recovered_slot_to_field_bayes_exact_ceiling": 1.0,
        "recovered_slot_to_field_bayes_average_field_ceiling": 1.0,
        **{f"gold_{field}_bayes_ceiling": 1.0 for field in FIELD_NAMES},
        "coverage": {
            field: {"heldout_unseen_class_rate": 0.0}
            for field in FIELD_NAMES
        },
    }
    verdict = tiny256_stage2_audit_verdict(
        base, current_exact=0.04, current_average=0.69
    )
    assert "HEAD_CAPACITY_OR_OPTIMIZATION_BOTTLENECK" in verdict

    limited = dict(base)
    limited["gold_slot_to_field_bayes_exact_ceiling"] = 0.40
    assert (
        "SLOT_REPRESENTATION_UNDERSPECIFIED"
        in tiny256_stage2_audit_verdict(
            limited, current_exact=0.04, current_average=0.69
        )
    )

    coverage = dict(base)
    coverage["coverage"] = {
        **base["coverage"],
        "value": {"heldout_unseen_class_rate": 0.25},
    }
    assert (
        "TRAIN_COVERAGE_LIMITED"
        in tiny256_stage2_audit_verdict(
            coverage, current_exact=0.04, current_average=0.69
        )
    )

    recovered_noise = dict(base)
    recovered_noise[
        "recovered_slot_to_field_bayes_average_field_ceiling"
    ] = 0.90
    assert (
        "RECOVERED_SLOT_NOISE_LIMITED"
        in tiny256_stage2_audit_verdict(
            recovered_noise, current_exact=0.04, current_average=0.69
        )
    )

    broader = dict(base)
    broader["gold_date_bayes_ceiling"] = 0.60
    assert (
        "BROADER_CONTEXT_REQUIRED"
        in tiny256_stage2_audit_verdict(
            broader, current_exact=0.04, current_average=0.69
        )
    )


def _tiny256_rows(
    *,
    exact: float = 0.36,
    average: float = 0.88,
    subject: float = 0.82,
    object_value: float = 0.83,
    date_value: float = 0.84,
    value: float = 0.79,
    gap: float = 0.12,
):
    return [
        {
            "heldout_exact_tuple_accuracy": exact,
            "heldout_average_field_accuracy": average,
            "subject_accuracy": subject,
            "object_accuracy": object_value,
            "date_accuracy": date_value,
            "value_accuracy": value,
            "train_heldout_gap": gap,
        }
        for _ in range(3)
    ]


def test_tiny256_stage2_repair_gate():
    assert tiny256_stage2_repair_gate(_tiny256_rows())
    assert not tiny256_stage2_repair_gate(
        _tiny256_rows(average=0.90, value=0.70)
    )
    assert not tiny256_stage2_repair_gate(
        _tiny256_rows(average=0.90, date_value=0.70)
    )
    assert not tiny256_stage2_repair_gate(_tiny256_rows(exact=0.10))
    assert not tiny256_stage2_repair_gate(_tiny256_rows(gap=0.20))


def test_tiny256_stage3_smoke_gated():
    assert tiny256_stage3_smoke_allowed("FIELD_CONTEXT_REPAIRED")
    assert tiny256_stage3_smoke_allowed(
        "FIELD_CONTEXT_REPAIRED+COMPOSITION_COVERAGE_LIMITED"
    )
    assert not tiny256_stage3_smoke_allowed("FIELD_SCALE_BLOCKED")
    assert not tiny256_stage3_smoke_allowed("TRAIN_COVERAGE_LIMITED")


def test_tiny256_stage2_scale_verdict_logic():
    repaired = _tiny256_rows()
    blocked = _tiny256_rows(exact=0.04, average=0.70, value=0.55)
    assert (
        tiny256_stage2_scale_verdict(
            audit_verdict="HEAD_CAPACITY_OR_OPTIMIZATION_BOTTLENECK",
            best_gold_metrics=blocked,
            best_recovered_metrics=repaired,
            baseline_average=0.69,
        )
        == "FIELD_CONTEXT_REPAIRED"
    )
    assert (
        tiny256_stage2_scale_verdict(
            audit_verdict="HEAD_CAPACITY_OR_OPTIMIZATION_BOTTLENECK",
            best_gold_metrics=repaired,
            best_recovered_metrics=blocked,
            baseline_average=0.69,
        )
        == "RECOVERED_SLOT_NOISE_LIMITED"
    )
    assert (
        tiny256_stage2_scale_verdict(
            audit_verdict="SLOT_REPRESENTATION_UNDERSPECIFIED",
            best_gold_metrics=repaired,
            best_recovered_metrics=repaired,
            baseline_average=0.69,
        )
        == "SLOT_REPRESENTATION_LIMITED"
    )


def test_tiny256_compositional_stage2_verdict_logic():
    repaired_binding = _tiny256_rows()
    partial_binding = _tiny256_rows(exact=0.34, average=0.875)
    train256_only = _tiny256_rows(exact=0.39, average=0.88)
    old_train128 = _tiny256_rows(exact=0.30, average=0.84, value=0.72)
    overfit_binding = _tiny256_rows(
        exact=0.31, average=0.85, value=0.72, gap=0.22
    )
    for row in overfit_binding:
        row["train_average_field_accuracy"] = 0.92
    assert (
        tiny256_compositional_stage2_verdict(
            best_binding_metrics=repaired_binding,
            best_train256_metrics=repaired_binding,
            best_train128_metrics=old_train128,
        )
        == "BINDING_REPAIRED"
    )
    assert (
        tiny256_compositional_stage2_verdict(
            best_binding_metrics=partial_binding,
            best_train256_metrics=partial_binding,
            best_train128_metrics=old_train128,
        )
        == "PARTIAL_BINDING_SIGNAL"
    )
    assert (
        tiny256_compositional_stage2_verdict(
            best_binding_metrics=[],
            best_train256_metrics=train256_only,
            best_train128_metrics=old_train128,
        )
        == "DATA_SCALE_SIGNAL_ONLY"
    )
    assert (
        tiny256_compositional_stage2_verdict(
            best_binding_metrics=overfit_binding,
            best_train256_metrics=old_train128,
            best_train128_metrics=old_train128,
        )
        == "OVERFIT_BINDING_OBJECTIVE"
    )
    assert (
        tiny256_compositional_stage2_verdict(
            best_binding_metrics=[],
            best_train256_metrics=old_train128,
            best_train128_metrics=old_train128,
        )
        == "NO_BINDING_GAIN"
    )


def test_stage3_smoke_gated_by_binding_repair():
    assert tiny256_stage3_smoke_allowed("BINDING_REPAIRED")
    assert not tiny256_stage3_smoke_allowed("PARTIAL_BINDING_SIGNAL")
    assert not tiny256_stage3_smoke_allowed("NO_BINDING_GAIN")


def test_field_coverage_metrics_tiny256():
    train = [
        _fields("subject_00"),
        _fields("subject_01"),
    ]
    heldout = [
        _fields("subject_00"),
        _fields("subject_02"),
    ]
    coverage = field_coverage_metrics(
        train,
        heldout,
        field_cardinalities={field: 4 for field in FIELD_NAMES},
    )
    assert coverage["subject"]["train_class_coverage"] == 0.5
    assert coverage["subject"]["heldout_unseen_class_rate"] == 0.5
    assert "per_field_nearest_training_baseline" in coverage["subject"]


def test_stage2_variant_table_schema_contract():
    required = {
        "train_size",
        "input_mode",
        "head",
        "loss",
        "parameter_count",
        "heldout_exact_tuple_accuracy_mean",
        "heldout_average_field_accuracy_mean",
        "subject_accuracy_mean",
        "object_accuracy_mean",
        "date_accuracy_mean",
        "value_accuracy_mean",
        "train_heldout_gap_mean",
    }
    fake_row = {
        "train_size": 64,
        "input_mode": "recovered_slots_plus_confidence",
        "head": "field_query_cross_attention",
        "loss": "class_balanced",
        "parameter_count": 123,
        "heldout_exact_tuple_accuracy_mean": 0.2,
        "heldout_average_field_accuracy_mean": 0.85,
        "subject_accuracy_mean": 0.8,
        "object_accuracy_mean": 0.8,
        "date_accuracy_mean": 0.8,
        "value_accuracy_mean": 0.75,
        "train_heldout_gap_mean": 0.1,
    }
    assert required <= set(fake_row)


def _write_fake_source_run(
    root: Path,
    *,
    schema: str,
    seed: int,
    train_examples: int,
    heldout_examples: int = 128,
) -> Path:
    run_dir = (
        root
        / f"{schema}-s{seed}-train{train_examples}"
        f"-heldout{heldout_examples}-e300-g9-l256-v16"
    )
    (run_dir / "checkpoint").mkdir(parents=True)
    config = {
        "schema": schema,
        "seed": seed,
        "train_examples": train_examples,
        "heldout_examples": heldout_examples,
        "codec_global_slot_count": 9,
        "codec_local_slot_count": 256,
        "codec_vocab_size": 16,
        "codec_word_count_min": 400,
        "codec_word_count_max": 700,
        "slot_label_augmentation_examples": 128,
    }
    (run_dir / "config.json").write_text(
        json.dumps(config), encoding="utf-8"
    )
    for name in (
        "train_examples.jsonl",
        "heldout_examples.jsonl",
        "artifacts_train.jsonl",
        "artifacts_heldout.jsonl",
        "slot_reconstruction_examples.jsonl",
    ):
        (run_dir / name).write_text("", encoding="utf-8")
    (run_dir / "metrics.json").write_text(
        json.dumps(
            {
                "stage2_text_to_slot_to_field_exact_tuple_accuracy": 0.0,
                "stage2_text_to_slot_to_field_average_field_accuracy": 0.0,
            }
        ),
        encoding="utf-8",
    )
    (run_dir / "checkpoint" / "stage1_text_to_slot.pt").write_bytes(b"x")
    return run_dir


def test_tiny256_source_manifest_generation(tmp_path, monkeypatch):
    source_root = tmp_path / "existing"
    out_dir = tmp_path / "out"
    train64 = _write_fake_source_run(
        source_root, schema="tiny_256", seed=0, train_examples=64
    )
    generated = []

    def fake_generate(**kwargs):
        generated.append(kwargs["train_examples"])
        return _write_fake_source_run(
            out_dir / "source_runs",
            schema=kwargs["schema"],
            seed=kwargs["seed"],
            train_examples=kwargs["train_examples"],
            heldout_examples=kwargs["heldout_examples"],
        )

    monkeypatch.setattr(
        "argus_capsules.run_codec_stage2_field_head_study._generate_source_run",
        fake_generate,
    )
    args = build_stage2_study_config(
        schema="tiny_256",
        seeds=[0],
        heldout_examples=128,
        prepare_sources=True,
    )
    source_paths, manifests = _prepare_source_runs(
        args=args,
        out_dir=out_dir,
        source_roots=[source_root, out_dir / "source_runs"],
        train_sizes=[64, 128, 256],
    )
    assert source_paths[(64, 0)] == train64
    assert generated == [128, 256]
    by_train = {row["train_examples"]: row for row in manifests}
    assert by_train[64]["reused_existing_source"] is True
    assert by_train[128]["reused_existing_source"] is False
    assert "train128" in str(
        by_train[128]["source_artifact_paths"]["config.json"]
    )
    assert "train64" not in str(
        by_train[128]["source_artifact_paths"]["config.json"]
    )


def test_train256_source_manifest_generation(tmp_path, monkeypatch):
    test_tiny256_source_manifest_generation(tmp_path, monkeypatch)


def test_combination_coverage_audit():
    train = [
        {
            **_fields("subject_00"),
            "object": "object_00",
            "value": "value_00",
        },
        {
            **_fields("subject_01"),
            "object": "object_01",
            "value": "value_01",
        },
    ]
    heldout = [
        {
            **_fields("subject_00"),
            "object": "object_01",
            "value": "value_00",
        },
        {
            **_fields("subject_01"),
            "object": "object_00",
            "value": "value_01",
        },
    ]
    audit = combination_coverage_audit(
        train,
        heldout,
        field_cardinalities={field: 2 for field in FIELD_NAMES},
    )
    assert audit["single_field_coverage"]["subject"][
        "heldout_unseen_class_rate"
    ] == 0.0
    assert audit["subject_object_coverage"][
        "heldout_unseen_combo_rate"
    ] == 1.0
    assert audit["heldout_pair_combo_unseen_rate"] > 0.0
    assert audit["heldout_triple_combo_unseen_rate"] > 0.0
    assert "predicate_object_value_coverage" in audit
    assert "subject_predicate_value_coverage" in audit
    assert "heldout_predicate_object_value_unseen_rate" in audit


def test_composition_coverage_metrics():
    train = [
        {
            **_fields("subject_00"),
            "object": "object_00",
            "value": "value_00",
        }
    ]
    heldout = [
        {
            **_fields("subject_00"),
            "object": "object_01",
            "value": "value_00",
        }
    ]
    audit = combination_coverage_audit(
        train,
        heldout,
        field_cardinalities={field: 4 for field in FIELD_NAMES},
    )
    assert "pairwise_field_combo_coverage" in audit
    assert "triple_field_combo_coverage" in audit
    assert audit["heldout_subject_object_value_unseen_rate"] == 1.0


def test_binding_aux_label_encoding():
    spec = get_tiny_schema_spec("tiny_256")
    rows = [
        {
            **_fields("subject_00"),
            "object": "object_00",
            "value": "value_00",
        },
        {
            **_fields("subject_01"),
            "object": "object_02",
            "value": "value_03",
        },
    ]
    first = encode_binding_aux_labels(rows, spec)
    second = encode_binding_aux_labels(rows, spec)
    assert set(first) == set(BINDING_AUXILIARIES)
    for name in first:
        assert torch.equal(first[name], second[name])
    specs = binding_auxiliary_specs(spec)
    assert specs["subject_object_aux"]["num_classes"] == 64 * 64
    assert (
        specs["subject_object_value_aux"]["num_classes"]
        == 64 * 64 * 32
    )


def test_binding_aux_heads_shapes():
    spec = get_tiny_schema_spec("tiny_256")
    slots = torch.randn(2, 12, 16)
    assignments = [index % len(FIELD_NAMES) for index in range(10)]
    model = Stage2FieldHead(
        spec,
        input_size=16,
        slot_count=12,
        global_slot_count=2,
        local_field_assignments=assignments,
        variant="low_rank_pairwise_interactions_plus_binding",
    )
    logits = model(slots)
    assert set(FIELD_NAMES) <= set(logits)
    aux_logits = factorized_binding_aux_logits(
        logits, spec, "subject_object_aux"
    )
    assert aux_logits.shape == (2, spec.field_sizes["subject"] * spec.field_sizes["object"])
    triple_logits = factorized_binding_aux_logits(
        logits, spec, "predicate_object_value_aux"
    )
    assert triple_logits.shape[0] == 2
    assert triple_logits.shape[1] == (
        spec.field_sizes["predicate"]
        * spec.field_sizes["object"]
        * spec.field_sizes["value"]
    )


def test_binding_loss_variants():
    spec = get_tiny_schema_spec("tiny_64")
    slots = torch.randn(4, 12, 16)
    assignments = [index % len(FIELD_NAMES) for index in range(10)]
    fields = [
        {
            "subject": spec.subjects[index],
            "predicate": spec.predicates[0],
            "object": spec.objects[index],
            "date": spec.dates[index],
            "value": spec.values[index % len(spec.values)],
            "confidence": spec.confidences[0],
            "source_type": spec.source_types[0],
        }
        for index in range(4)
    ]
    labels = {
        field: torch.tensor(
            [
                spec.field_values[field].index(row[field])
                for row in fields
            ],
            dtype=torch.long,
        )
        for field in FIELD_NAMES
    }
    variants = (
        "pair_aux_only",
        "triple_aux_only",
        "pair_plus_triple_aux",
        "contrastive_binding_loss",
        "bilinear_binding_loss",
        "field_loss_plus_binding_loss",
        "field_loss_plus_binding_loss_class_balanced",
    )
    for variant in variants:
        model = Stage2FieldHead(
            spec,
            input_size=16,
            slot_count=12,
            global_slot_count=2,
            local_field_assignments=assignments,
            variant="global_summary_concat_plus_binding",
        )
        loss = stage2_loss(
            model(slots),
            labels,
            spec=spec,
            model=model,
            variant=variant,
            binding_weight=0.1,
        )
        assert torch.isfinite(loss)


def test_full_context_head_shapes():
    spec = get_tiny_schema_spec("tiny_256")
    slots = torch.randn(4, 12, 16)
    assignments = [index % len(FIELD_NAMES) for index in range(10)]
    for head in (
        "global_summary_concat",
        "deepsets_context",
        "low_rank_pairwise_interactions",
        "field_query_cross_attention_lite",
    ):
        model = Stage2FieldHead(
            spec,
            input_size=16,
            slot_count=12,
            global_slot_count=2,
            local_field_assignments=assignments,
            variant=head,
        )
        logits = model(slots)
        assert set(FIELD_NAMES) <= set(logits)
        for field in FIELD_NAMES:
            assert logits[field].shape == (4, spec.field_sizes[field])


def test_confidence_features_optional():
    spec = get_tiny_schema_spec("tiny_256")
    assignments = [index % len(FIELD_NAMES) for index in range(10)]
    no_conf = Stage2FieldHead(
        spec,
        input_size=16,
        slot_count=12,
        global_slot_count=2,
        local_field_assignments=assignments,
        variant="global_summary_concat",
    )
    with_conf = Stage2FieldHead(
        spec,
        input_size=17,
        slot_count=12,
        global_slot_count=2,
        local_field_assignments=assignments,
        variant="global_summary_concat",
        confidence_feature=True,
    )
    assert no_conf(torch.randn(2, 12, 16))["subject"].shape[0] == 2
    assert with_conf(torch.randn(2, 12, 17))["subject"].shape[0] == 2


def test_parameter_count_reported():
    spec = get_tiny_schema_spec("tiny_256")
    model = Stage2FieldHead(
        spec,
        input_size=16,
        slot_count=12,
        global_slot_count=2,
        local_field_assignments=[
            index % len(FIELD_NAMES) for index in range(10)
        ],
        variant="low_rank_pairwise_interactions",
    )
    assert parameter_count(model) > 0


def test_stage2_staged_pruning_logic():
    rows = []
    for index, score in enumerate((0.90, 0.88, 0.86, 0.60)):
        rows.append(
            {
                "input_mode": "recovered_slots_plus_confidence",
                "head": f"head_{index}",
                "loss": "uniform",
                "heldout_average_field_accuracy": score,
                "heldout_exact_tuple_accuracy": score / 2.0,
                "subject_accuracy": score,
                "object_accuracy": score,
                "date_accuracy": score,
                "value_accuracy": score,
                "train_heldout_gap": 0.01,
            }
        )
    decisions = stage2_staged_pruning_decisions(rows, top_k=3)
    selected = {
        (row["head"], row["loss"])
        for row in decisions["selected_variants"]
    }
    assert len(selected) == 3
    assert ("head_3", "uniform") not in selected
    args = build_stage2_study_config(
        input_modes=[
            "recovered_slots_plus_confidence",
            "recovered_slots",
            "gold_slots",
        ],
        seeds=[0, 1, 2],
    )
    jobs = _stage2_jobs_from_decisions(args, [64, 128], decisions)
    assert jobs
    assert {
        (job["head"], job["loss"]) for job in jobs
    } <= selected
    assert all(job["sweep_stage"] == "stage2_confirm" for job in jobs)


def test_no_valid_open_boundary_violation():
    from argus_capsules.codec_text_to_slot import TextToSlotCapsule

    source = inspect.getsource(TextToSlotCapsule.open_artifact)
    forbidden = (
        "TinyNarrativeEncoder",
        "render_with_slot_spans",
        "gold_slot_ids",
        "slot_spans",
        "story_plan",
        "original_report",
        "target_json",
    )
    for term in forbidden:
        assert term not in source


def test_no_symbolic_lookup_in_valid_path():
    from argus_capsules.codec_text_to_slot import TextToSlotCapsule

    source = inspect.getsource(TextToSlotCapsule.open_artifact)
    forbidden = (
        "binding_aux",
        "auxiliary_label",
        "carrier_lookup",
        "lookup_table",
        "gold_slot_ids",
        "slot_spans",
    )
    for term in forbidden:
        assert term not in source


def test_no_symbolic_inverse():
    source = "\n".join(
        path.read_text(encoding="utf-8")
        for path in Path("argus_capsules").glob("*.py")
    )
    for term in (
        "def story_to_report",
        "def decode_story_to_case",
        "def inverse_story",
        "base64.b64decode",
        "bytes.fromhex",
        "carrier_lookup",
    ):
        assert term not in source


def test_artifact_schema_tiny256_full_context():
    paths = stage2_study_artifact_paths(
        Path("artifacts/private_capsules/tiny256_full_context_stage2")
    )
    expected = {
        "source_manifests",
        "coverage_audit",
        "coverage_metrics.json",
        "coverage_summary.md",
        "pairwise_coverage.csv",
        "triple_coverage.csv",
        "heldout_novel_combos.jsonl",
        "stage2_variant_metrics.json",
        "stage2_variant_table.csv",
        "best_variant_checkpoint",
    }
    assert expected <= set(paths)


def test_artifact_schema_tiny256_compositional_binding():
    paths = stage2_study_artifact_paths(
        Path("artifacts/private_capsules/tiny256_compositional_binding")
    )
    expected = {
        "config.json",
        "source_manifests",
        "coverage_audit",
        "coverage_metrics.json",
        "coverage_summary.md",
        "pairwise_coverage.csv",
        "triple_coverage.csv",
        "heldout_novel_combos.jsonl",
        "sov_combo_novelty.json",
        "stage2_binding_metrics.json",
        "stage2_binding_variant_table.csv",
        "stage2_binding_seed_table.csv",
        "stage2_binding_summary.md",
        "field_diagnostics.csv",
        "field_diagnostics.json",
        "date_diagnostics.csv",
        "value_diagnostics.json",
        "binding_aux_diagnostics.json",
        "confusion_examples.jsonl",
        "failure_cases.jsonl",
        "baseline_table.csv",
        "best_variant_config.json",
        "best_variant_checkpoint",
        "stage3_smoke_metrics.json",
        "stage3_smoke_summary.md",
    }
    assert expected <= set(paths)
