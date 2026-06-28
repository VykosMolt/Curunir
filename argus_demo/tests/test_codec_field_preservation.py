"""Fast contracts for the tiny_256 field-preservation pass.

These tests never run long CUDA training.  They exercise the date-regression
audit math, the date-preservation losses (logit/feature distillation,
anti-regression), the frozen-date-head contract, the separate value branch,
the preservation verdict + gate logic, the optional train512 / Stage-3 gates,
the no-symbolic-correction invariant, the valid-open boundary, and the artifact
schema.
"""

import inspect
import json
from pathlib import Path

import pytest
import torch

from argus_capsules.codec_field_preservation import (
    DISTRIBUTED_VALUE_READER_REFERENCE,
    PRESERVATION_HEAD_VARIANTS,
    PRESERVATION_LOSS_VARIANTS,
    VALUE_BINDING_REFERENCE,
    DateTeacher,
    FieldPreservationModule,
    _anti_regression_date_loss,
    _date_feature_distillation,
    _date_logit_distillation,
    date_field_diagnostics,
    date_logit_diagnostics,
    date_regression_audit,
    field_preservation_loss,
    preservation_head_compatible,
    preservation_spec,
    tiny256_coverage_plus_preservation_verdict,
    tiny256_field_preservation_gate,
    tiny256_field_preservation_stage3_allowed,
    tiny256_field_preservation_verdict,
    train_field_preservation_head,
)
from argus_capsules.run_codec_stage2_field_head_study import (
    FIELD_PRESERVATION_REQUIRED_ARTIFACTS,
    _preservation_mode_active,
    _preservation_pairs,
    _run_preservation_stage3_smoke,
    _write_date_regression_artifacts,
    build_stage2_study_config,
    field_preservation_artifact_paths,
)
from argus_capsules.tiny_schema import FIELD_NAMES, get_tiny_schema_spec


@pytest.fixture(scope="session", autouse=True)
def _db_setup():
    # Pure-Python contracts; shadow the root DB fixtures so no Postgres needed.
    yield


@pytest.fixture(autouse=True)
def clean_db():
    yield


SPEC = get_tiny_schema_spec("tiny_256")
GLOBAL = 2
LOCAL = 14  # value-assigned local indices i%7==4 -> {4, 11} => group size 2
SLOT_COUNT = GLOBAL + LOCAL
INPUT_SIZE = 16
ASSIGNMENTS = [index % len(FIELD_NAMES) for index in range(LOCAL)]
DEVICE = torch.device("cpu")


def _fields(subject, object_, value, *, date_value="2026-01-01", predicate="predicate_00"):
    return {
        "subject": subject,
        "predicate": predicate,
        "object": object_,
        "date": date_value,
        "value": value,
        "confidence": "low",
        "source_type": "synthetic_note",
    }


def _make_fields(n):
    return [
        _fields(
            f"subject_{i % 64:02d}",
            f"object_{(i * 3) % 64:02d}",
            f"value_{i % 32:02d}",
            date_value=SPEC.dates[i % 64],
            predicate=f"predicate_{i % 16:02d}",
        )
        for i in range(n)
    ]


def _build_module(separate_branch=False):
    return FieldPreservationModule(
        SPEC,
        input_size=INPUT_SIZE,
        slot_count=SLOT_COUNT,
        global_slot_count=GLOBAL,
        local_field_assignments=ASSIGNMENTS,
        separate_branch=separate_branch,
        candidate_top_k=2,
        multi_pointer_k=1,
    )


def _seed_metric(value, date, *, subject=0.97, object_=0.99, avg=0.94, exact=0.63,
                 train_avg=0.99, gap=0.06):
    return {
        "value_accuracy": value,
        "date_accuracy": date,
        "subject_accuracy": subject,
        "object_accuracy": object_,
        "heldout_average_field_accuracy": avg,
        "heldout_exact_tuple_accuracy": exact,
        "train_average_field_accuracy": train_avg,
        "train_heldout_gap": gap,
    }


# --------------------------------------------------------------------------- #
# 1. date regression audit metrics.
# --------------------------------------------------------------------------- #


def test_date_regression_audit_metrics():
    torch.manual_seed(0)
    targets = _make_fields(24)
    # Teacher (previous best): all dates correct.
    teacher_pred = [dict(f) for f in targets]
    # Distributed reader: regress 6 dates (near-miss shifts) and flip some value.
    reader_pred = [dict(f) for f in targets]
    for i in range(6):
        # shift date by one calendar day (near miss); value made correct here.
        reader_pred[i]["date"] = SPEC.dates[(i + 1) % 64]
    reader_vc = [i < 6 for i in range(24)]  # value correct exactly where date wrong
    teach_vc = [True] * 24
    date_size = SPEC.field_sizes["date"]
    distributed = {
        0: {
            "predictions": reader_pred,
            "date_logits": torch.randn(24, date_size),
            "value_correct": reader_vc,
        }
    }
    teacher = {
        0: {
            "predictions": teacher_pred,
            "date_logits": torch.randn(24, date_size),
            "value_correct": teach_vc,
        }
    }
    audit = date_regression_audit(
        distributed_by_seed=distributed,
        teacher_by_seed=teacher,
        heldout_fields_by_seed={0: targets},
        probe_recovery_by_seed={0: 0.95},
    )
    assert audit["DATE_REGRESSION_AUDIT"] in {
        "DATE_HEAD_INTERFERENCE",
        "DATE_REPRESENTATION_INTERFERENCE",
        "SEED_LOCALIZED_DATE_COLLAPSE",
        "VALUE_DATE_TRADEOFF",
        "DATE_REGRESSION_UNCLEAR",
    }
    # Teacher beats reader on date.
    assert audit["previous_best_date_accuracy_mean"] > audit[
        "distributed_reader_date_accuracy_mean"
    ]
    # KL + margins are finite.
    assert audit["date_kl_from_previous_best"] >= 0.0
    assert audit["date_logit_margin_distributed_reader"] == pytest.approx(
        audit["date_logit_margin_distributed_reader"]
    )
    # Date/value interaction recorded; date errors concentrate where value right.
    assert audit["date_error_when_value_correct"] > audit[
        "date_error_when_value_wrong"
    ]
    # Regression examples: previous best correct, reader wrong.
    assert len(audit["regression_examples"]) == 6
    assert audit["date_confusion_top_errors"]
    # Date errors concentrate where value is correct and the frozen probe does
    # not recover to the teacher's level => value/date tradeoff.
    assert audit["DATE_REGRESSION_AUDIT"] == "VALUE_DATE_TRADEOFF"

    # If the frozen date features still recover date near the teacher level, the
    # verdict flips to head interference (the head drifted, not the rep).
    head_audit = date_regression_audit(
        distributed_by_seed=distributed,
        teacher_by_seed=teacher,
        heldout_fields_by_seed={0: targets},
        probe_recovery_by_seed={0: 0.99},
    )
    assert head_audit["DATE_REGRESSION_AUDIT"] == "DATE_HEAD_INTERFERENCE"

    # The per-field diagnostic also decomposes calendar components + day error.
    diag = date_field_diagnostics(reader_pred, targets, value_correct=reader_vc)
    assert 0.0 <= diag["date_accuracy"] <= 1.0
    assert diag["p90_abs_day_error"] >= diag["p50_abs_day_error"]
    assert "date_accuracy_by_class" in diag
    assert diag["year_accuracy"] == pytest.approx(1.0)  # all 2026


# --------------------------------------------------------------------------- #
# 2. date logit distillation loss.
# --------------------------------------------------------------------------- #


def test_date_logit_distillation_loss():
    torch.manual_seed(1)
    student = torch.randn(10, SPEC.field_sizes["date"], requires_grad=True)
    teacher = torch.randn(10, SPEC.field_sizes["date"])
    loss = _date_logit_distillation(student, teacher)
    assert torch.isfinite(loss)
    assert loss.item() >= 0.0
    loss.backward()
    assert student.grad is not None and torch.isfinite(student.grad).all()
    # KL of a distribution from itself is ~0.
    same = _date_logit_distillation(teacher, teacher)
    assert same.item() == pytest.approx(0.0, abs=1e-5)

    diag = date_logit_diagnostics(student.detach(), teacher)
    assert torch.isfinite(torch.tensor(diag["date_kl_from_previous_best"]))
    assert "date_logit_margin_mean" in diag


# --------------------------------------------------------------------------- #
# 3. date feature distillation loss.
# --------------------------------------------------------------------------- #


def test_date_feature_distillation_loss():
    torch.manual_seed(2)
    student = torch.randn(10, 48, requires_grad=True)
    teacher = torch.randn(10, 48)
    loss = _date_feature_distillation(student, teacher)
    assert torch.isfinite(loss)
    assert 0.0 <= loss.item() <= 2.0
    loss.backward()
    assert student.grad is not None
    # cosine distance of a vector from itself is ~0.
    zero = _date_feature_distillation(teacher, teacher)
    assert zero.item() == pytest.approx(0.0, abs=1e-5)


# --------------------------------------------------------------------------- #
# 4. frozen date head contract.
# --------------------------------------------------------------------------- #


def test_date_head_frozen_contract():
    torch.manual_seed(3)
    model = _build_module()
    before = {name: p.detach().clone() for name, p in model.date_head_parameters()}
    value_param_before = model.value_embedding.weight.detach().clone()
    assert preservation_spec("date_head_frozen")["freeze_date_head"] is True

    fields = _make_fields(32)
    held = _make_fields(16)
    result = train_field_preservation_head(
        model=model,
        loss_variant="date_head_frozen",
        train_embeddings=torch.randn(32, SLOT_COUNT, INPUT_SIZE),
        train_fields=fields,
        heldout_embeddings=torch.randn(16, SLOT_COUNT, INPUT_SIZE),
        heldout_fields=held,
        gold_location_train=torch.zeros(32, dtype=torch.long),
        gold_location_heldout=torch.zeros(16, dtype=torch.long),
        epochs=6,
        batch_size=16,
        seed=0,
        device=DEVICE,
        teacher=None,
    )
    assert result["freeze_date_head"] is True
    assert result["frozen_date_parameters"]
    # Date head parameters are byte-identical after training.
    after = dict(model.date_head_parameters())
    for name, original in before.items():
        assert torch.equal(after[name], original), name
    # But the value branch did train.
    assert not torch.equal(model.value_embedding.weight.detach(), value_param_before)


# --------------------------------------------------------------------------- #
# 5. anti-regression loss.
# --------------------------------------------------------------------------- #


def test_anti_regression_loss():
    torch.manual_seed(4)
    date_size = SPEC.field_sizes["date"]
    n = 8
    labels = torch.arange(n) % date_size
    # Student predicts the wrong class everywhere.
    logits = torch.full((n, date_size), -5.0)
    for i in range(n):
        logits[i, (labels[i] + 1) % date_size] = 5.0
    teacher_correct = torch.ones(n, dtype=torch.bool)
    loss = _anti_regression_date_loss(logits, labels, teacher_correct)
    assert loss.item() > 0.0
    # No teacher-correct examples => zero penalty.
    none_correct = torch.zeros(n, dtype=torch.bool)
    assert _anti_regression_date_loss(logits, labels, none_correct).item() == 0.0
    # Student already correct => zero penalty even if teacher correct.
    correct_logits = torch.full((n, date_size), -5.0)
    for i in range(n):
        correct_logits[i, labels[i]] = 5.0
    assert _anti_regression_date_loss(
        correct_logits, labels, teacher_correct
    ).item() == 0.0


# --------------------------------------------------------------------------- #
# 6. separate value branch shapes + gradient isolation.
# --------------------------------------------------------------------------- #


def test_separate_value_branch_shapes():
    torch.manual_seed(5)
    emb = torch.randn(6, SLOT_COUNT, INPUT_SIZE)

    separate = _build_module(separate_branch=True)
    out = separate.training_outputs(emb)
    assert out["multi_value_scores"].shape == (6, SPEC.field_sizes["value"])
    assert out["base_logits"]["date"].shape == (6, SPEC.field_sizes["date"])
    # A pure value-contrastive signal must not reach the shared base/date head.
    out["multi_value_scores"].sum().backward()
    base_grad = sum(
        p.grad.abs().sum().item()
        for _, p in separate.base.named_parameters()
        if p.grad is not None
    )
    assert base_grad == 0.0
    assert separate.pointer_query[0].weight.grad is not None

    # The shared-base topology *does* let value gradients into the base.
    shared = _build_module(separate_branch=False)
    shared.training_outputs(emb)["multi_value_scores"].sum().backward()
    shared_base_grad = sum(
        p.grad.abs().sum().item()
        for _, p in shared.base.named_parameters()
        if p.grad is not None
    )
    assert shared_base_grad > 0.0


# --------------------------------------------------------------------------- #
# 7. preservation verdict logic (all six verdicts reachable).
# --------------------------------------------------------------------------- #


def test_preservation_verdict_logic():
    repaired = [_seed_metric(0.79, 0.91), _seed_metric(0.80, 0.92), _seed_metric(0.78, 0.90)]
    assert tiny256_field_preservation_verdict(repaired) == "VALUE_REPAIRED_WITH_DATE_PRESERVED"
    assert tiny256_field_preservation_gate(repaired) is True

    regress = [_seed_metric(0.80, 0.86), _seed_metric(0.81, 0.87), _seed_metric(0.79, 0.88)]
    assert tiny256_field_preservation_verdict(regress) == "VALUE_REPAIR_STILL_REGRESSES_DATE"

    date_lost = [_seed_metric(0.70, 0.92), _seed_metric(0.71, 0.93), _seed_metric(0.69, 0.91)]
    assert tiny256_field_preservation_verdict(date_lost) == "DATE_REPAIRED_VALUE_LOST"

    partial = [
        _seed_metric(0.79, 0.905, exact=0.66),
        _seed_metric(0.74, 0.88, exact=0.60),
        _seed_metric(0.80, 0.91, exact=0.66),
    ]
    assert tiny256_field_preservation_verdict(partial) == "PARTIAL_FIELD_PRESERVATION_SIGNAL"

    overfit = [
        _seed_metric(0.74, 0.885, avg=0.88, train_avg=0.99, gap=0.11, exact=0.60),
        _seed_metric(0.73, 0.875, avg=0.88, train_avg=0.99, gap=0.11, exact=0.60),
        _seed_metric(0.72, 0.87, avg=0.88, train_avg=0.99, gap=0.11, exact=0.60),
    ]
    assert tiny256_field_preservation_verdict(overfit) == "OVERFIT_FIELD_PRESERVATION"

    no_gain = [
        _seed_metric(0.70, 0.86, avg=0.93, exact=0.58),
        _seed_metric(0.71, 0.85, avg=0.93, exact=0.58),
        _seed_metric(0.69, 0.87, avg=0.93, exact=0.58),
    ]
    assert tiny256_field_preservation_verdict(no_gain) == "NO_FIELD_PRESERVATION_GAIN"

    # Fewer than three seeds never passes the gate.
    assert tiny256_field_preservation_gate(repaired[:2]) is False


# --------------------------------------------------------------------------- #
# 8. optional train512 gate.
# --------------------------------------------------------------------------- #


def test_train512_optional_gate():
    repaired = [_seed_metric(0.79, 0.91), _seed_metric(0.80, 0.92), _seed_metric(0.78, 0.90)]
    # No train512 metrics => NOT_RUN (the coverage check is gated).
    assert (
        tiny256_coverage_plus_preservation_verdict(
            train256_seed_metrics=repaired, train512_seed_metrics=[]
        )
        == "NOT_RUN_STAGE2_GATE"
    )
    # Gate-passing train512 => IMPROVES_TO_GATE.
    assert (
        tiny256_coverage_plus_preservation_verdict(
            train256_seed_metrics=repaired, train512_seed_metrics=repaired
        )
        == "IMPROVES_TO_GATE"
    )
    # Modest value/exact gain that misses the gate.
    base = [_seed_metric(0.70, 0.88, exact=0.60)]
    better = [
        _seed_metric(0.74, 0.88, exact=0.63),
        _seed_metric(0.74, 0.88, exact=0.63),
        _seed_metric(0.74, 0.88, exact=0.63),
    ]
    assert (
        tiny256_coverage_plus_preservation_verdict(
            train256_seed_metrics=base, train512_seed_metrics=better
        )
        == "MODEST_VALUE_GAIN"
    )


# --------------------------------------------------------------------------- #
# 9. Stage-3 smoke gated by field preservation.
# --------------------------------------------------------------------------- #


def test_stage3_smoke_gated_by_field_preservation(tmp_path):
    assert tiny256_field_preservation_stage3_allowed(
        "VALUE_REPAIRED_WITH_DATE_PRESERVED"
    )
    for verdict in (
        "VALUE_REPAIR_STILL_REGRESSES_DATE",
        "PARTIAL_FIELD_PRESERVATION_SIGNAL",
        "DATE_REPAIRED_VALUE_LOST",
        "NO_FIELD_PRESERVATION_GAIN",
    ):
        assert not tiny256_field_preservation_stage3_allowed(verdict)

    args = build_stage2_study_config(schema="tiny_256")
    # A non-passing Stage-2 verdict gates the valid-open Stage-3 run.
    preservation_dir = tmp_path / "preservation"
    preservation_dir.mkdir()
    (preservation_dir / "best_variant_config.json").write_text(
        json.dumps(
            {
                "head": "value_multi_pointer_pooler",
                "loss": "date_feature_distillation",
                "stage2_module_class": "FieldPreservationModule",
                "stage2_checkpoint_type": "field_preservation",
                "field_preservation_verdict": "VALUE_REPAIR_STILL_REGRESSES_DATE",
                "train_size": 256,
            }
        ),
        encoding="utf-8",
    )
    stage3_dir = tmp_path / "stage3"
    metrics = _run_preservation_stage3_smoke(
        args=args,
        caches={},
        spec=SPEC,
        study=None,
        preservation_dir=preservation_dir,
        stage3_dir=stage3_dir,
        device=torch.device("cpu"),
    )
    assert metrics["ARGUS_CAPSULE_CODEC_TINY256_STAGE3_SMOKE"] == "NOT_RUN_STAGE2_GATE"
    assert metrics["stage3_smoke_ran"] is False
    assert (stage3_dir / "stage3_smoke_metrics.json").exists()

    # Missing artifacts => integration-incomplete, never a fake pass.
    empty = tmp_path / "empty"
    empty.mkdir()
    missing = _run_preservation_stage3_smoke(
        args=args,
        caches={},
        spec=SPEC,
        study=None,
        preservation_dir=empty,
        stage3_dir=tmp_path / "stage3b",
        device=torch.device("cpu"),
    )
    assert (
        missing["ARGUS_CAPSULE_CODEC_TINY256_STAGE3_SMOKE"]
        == "NOT_RUN_INTEGRATION_INCOMPLETE"
    )


# --------------------------------------------------------------------------- #
# 10. no symbolic date or value correction.
# --------------------------------------------------------------------------- #


def _code_only(source: str) -> str:
    """Reconstruct a source string with all string literals and comments
    blanked out, so a token scan inspects executable code only (the module
    docstring legitimately *describes* the symbolic methods it avoids)."""
    import io
    import tokenize

    pieces = []
    tokens = tokenize.generate_tokens(io.StringIO(source).readline)
    for tok in tokens:
        if tok.type in (tokenize.STRING, tokenize.COMMENT):
            continue
        pieces.append(tok.string)
    return " ".join(pieces)


def test_no_symbolic_date_or_value_correction():
    import argus_capsules.codec_field_preservation as module

    code = _code_only(inspect.getsource(module)).lower()
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
        "retrieval",
    )
    for token in forbidden:
        assert token not in code, token
    # The value/date decisions are argmax over learned logits, not a decode map.
    train_src = inspect.getsource(train_field_preservation_head)
    assert "predict_fields(" in train_src
    # fromisoformat is used only for the day-error *diagnostic*, never to map a
    # prediction back to a corrected date.
    assert "fromisoformat" in inspect.getsource(module._parse_iso)
    assert "fromisoformat" not in train_src


# --------------------------------------------------------------------------- #
# 11. valid-open boundary not violated by the preservation pass.
# --------------------------------------------------------------------------- #


def test_no_valid_open_boundary_violation_field_preservation():
    # The Stage-3 valid-open path is gated on the repair verdict and records an
    # explicit open-boundary audit.  (Detailed open-path checks live in
    # test_codec_field_preservation_stage3.)
    smoke_src = inspect.getsource(_run_preservation_stage3_smoke)
    assert "NOT_RUN_STAGE2_GATE" in smoke_src
    assert "tiny256_field_preservation_stage3_allowed" in smoke_src
    # The date teacher (previous-best logits/features) is a *training-time*
    # signal only: its methods are no-grad and it is never referenced from a
    # valid-open path.  Confirm it exposes only date logits/features.
    teacher_methods = {
        name for name, _ in inspect.getmembers(DateTeacher, inspect.isfunction)
    }
    assert teacher_methods <= {
        "__init__",
        "date_logits",
        "date_state",
        "date_predictions",
    }
    # Teacher signals are consumed inside training only; the inference module
    # (FieldPreservationModule.forward) never references a teacher.
    loss_src = inspect.getsource(field_preservation_loss)
    assert "teacher_date_logits" in loss_src  # training-time argument
    forward_src = inspect.getsource(FieldPreservationModule.forward)
    assert "teacher" not in forward_src.lower()


# --------------------------------------------------------------------------- #
# 12. artifact schema.
# --------------------------------------------------------------------------- #


def test_artifact_schema_tiny256_field_preservation(tmp_path):
    required = set(FIELD_PRESERVATION_REQUIRED_ARTIFACTS)
    for name in (
        "config.json",
        "date_regression_audit/date_regression_audit.json",
        "date_regression_audit/date_confusion_matrix.csv",
        "date_regression_audit/date_by_class.csv",
        "date_regression_audit/date_value_interaction.csv",
        "date_regression_audit/date_regression_examples.jsonl",
        "stage2_field_preservation_metrics.json",
        "stage2_field_preservation_variant_table.csv",
        "stage2_field_preservation_seed_table.csv",
        "stage2_field_preservation_summary.md",
        "field_diagnostics.json",
        "date_diagnostics.json",
        "value_diagnostics.json",
        "preservation_diagnostics.json",
        "failure_cases.jsonl",
        "baseline_table.csv",
        "best_variant_config.json",
        "best_variant_checkpoint",
    ):
        assert name in required, name

    paths = field_preservation_artifact_paths(tmp_path)
    assert set(paths) == required

    # The audit writer materializes the six Track-A files.
    audit_dir = tmp_path / "date_regression_audit"
    audit = {
        "DATE_REGRESSION_AUDIT": "DATE_HEAD_INTERFERENCE",
        "distributed_reader_date_accuracy_mean": 0.88,
        "previous_best_date_accuracy_mean": 0.958,
        "date_regression_vs_previous_best": 0.078,
        "date_seed_spread": 0.03,
        "date_seed_min": 0.86,
        "date_seed_max": 0.89,
        "date_error_when_value_correct": 0.2,
        "date_error_when_value_wrong": 0.1,
        "value_accuracy_when_date_correct": 0.78,
        "value_accuracy_when_date_wrong": 0.7,
        "year_accuracy": 1.0,
        "month_accuracy": 0.95,
        "day_accuracy": 0.9,
        "p50_abs_day_error": 1.0,
        "p90_abs_day_error": 4.0,
        "near_miss_rate": 0.5,
        "date_kl_from_previous_best": 0.12,
        "date_logit_margin_distributed_reader": 1.1,
        "date_logit_margin_previous_best": 1.4,
        "frozen_feature_date_probe_recovery": 0.95,
        "per_seed_interaction": {
            "0": {
                "date_accuracy": 0.88,
                "date_error_when_value_correct": 0.2,
                "date_error_when_value_wrong": 0.1,
            }
        },
        "date_by_class_regression": {"2026-02-01": {"seeds_regressed": 1}},
        "date_confusion_top_errors": [
            {"gold": "2026-02-01", "predicted": "2026-02-02", "count": 3}
        ],
        "regression_examples": [{"seed": 0, "gold_date": "2026-02-01"}],
    }
    _write_date_regression_artifacts(audit_dir, audit)
    for name in (
        "date_regression_audit.json",
        "date_regression_summary.md",
        "date_confusion_matrix.csv",
        "date_by_class.csv",
        "date_value_interaction.csv",
        "date_regression_examples.jsonl",
    ):
        assert (audit_dir / name).exists(), name


# --------------------------------------------------------------------------- #
# Registry / pairing sanity.
# --------------------------------------------------------------------------- #


def test_preservation_pairs_and_mode_detection():
    args = build_stage2_study_config(
        schema="tiny_256",
        heads=list(PRESERVATION_HEAD_VARIANTS),
        loss_variants=list(PRESERVATION_LOSS_VARIANTS),
    )
    assert _preservation_mode_active(args)
    pairs = _preservation_pairs(args)
    assert ("value_multi_pointer_pooler", "baseline_distributed_value_reader") in pairs
    assert ("separate_value_branch", "baseline_distributed_value_reader") in pairs
    # The separate-only losses require the separate branch.
    assert not preservation_head_compatible(
        "value_multi_pointer_pooler", "separate_value_branch_plus_date_distillation"
    )
    assert preservation_head_compatible(
        "separate_value_branch", "separate_value_branch_plus_date_distillation"
    )
