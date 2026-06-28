"""Fast contracts for the tiny_256 value-binding repair pass.

These tests never run long CUDA training.  They exercise audit/rank math,
neural value-head shapes, hard-negative sampling, the value-repair verdict and
Stage-3 gate logic, the open-path boundary, and the artifact schema.
"""

import inspect
from pathlib import Path

import pytest
import torch

from argus_capsules.codec_value_binding import (
    VALUE_HEAD_VARIANTS,
    ValueBindingStage2Head,
    ValueScoringHead,
    build_value_cooccurrence,
    compute_value_error_audit,
    hard_negative_sampling_report,
    sample_value_hard_negatives,
    tiny256_value_binding_repair_gate,
    tiny256_value_binding_verdict,
    tiny256_value_repair_stage3_allowed,
    value_decode_mode,
    value_head_compatible,
    value_rank_diagnostics,
    _value_contrastive_loss,
)
from argus_capsules.run_codec_stage2_field_head_study import (
    VALUE_BINDING_REQUIRED_ARTIFACTS,
    value_binding_artifact_paths,
)
from argus_capsules.tiny_schema import FIELD_NAMES, get_tiny_schema_spec


@pytest.fixture(scope="session", autouse=True)
def _db_setup():
    # These pure-Python contracts never touch Postgres; shadow the root
    # conftest DB fixtures so the suite runs without a database.
    yield


@pytest.fixture(autouse=True)
def clean_db():
    yield


def _fields(subject, object_, value, *, date_value="2026-01-01"):
    return {
        "subject": subject,
        "predicate": "predicate_00",
        "object": object_,
        "date": date_value,
        "value": value,
        "confidence": "low",
        "source_type": "synthetic_note",
    }


def _build_value_head(head_variant, *, decode_mode="base", candidate_top_k=8):
    spec = get_tiny_schema_spec("tiny_256")
    assignments = [index % len(FIELD_NAMES) for index in range(10)]
    return ValueBindingStage2Head(
        spec,
        input_size=16,
        slot_count=12,
        global_slot_count=2,
        local_field_assignments=assignments,
        head_variant=head_variant,
        decode_mode=decode_mode,
        candidate_top_k=candidate_top_k,
    )


# --------------------------------------------------------------------------- #
# Test 1: value error audit metrics.
# --------------------------------------------------------------------------- #


def test_value_error_audit_metrics():
    spec = get_tiny_schema_spec("tiny_256")
    train_fields = [
        _fields("subject_00", "object_00", "value_00"),
        _fields("subject_01", "object_01", "value_01"),
        _fields("subject_00", "object_01", "value_02"),
    ]
    targets = [
        _fields("subject_00", "object_00", "value_00"),  # seen S/O/V
        _fields("subject_01", "object_02", "value_03"),  # novel
        _fields("subject_00", "object_01", "value_02"),  # seen S/O/V
        _fields("subject_02", "object_03", "value_05"),  # novel
    ]
    # Predictions: 2 correct value, 2 wrong value.
    predictions = [
        dict(targets[0]),
        {**targets[1], "value": "value_10"},
        dict(targets[2]),
        {**targets[3], "value": "value_11"},
    ]
    value_logits = torch.randn(len(targets), spec.field_sizes["value"])
    audit = compute_value_error_audit(
        value_logits=value_logits,
        predictions=predictions,
        targets=targets,
        train_fields=train_fields,
        spec=spec,
    )
    assert audit["value_accuracy"] == pytest.approx(0.5)
    assert audit["value_error_count"] == 2
    # Required breakdowns exist.
    for key in (
        "value_accuracy_by_value_class",
        "value_accuracy_by_subject_class",
        "value_accuracy_by_object_class",
        "value_accuracy_by_subject_object_pair",
        "value_accuracy_by_predicate_class",
        "value_accuracy_seen_subject_object_value",
        "value_accuracy_unseen_subject_object_value",
        "value_accuracy_when_subject_object_correct",
        "value_error_when_all_other_fields_correct",
        "combo_novelty",
    ):
        assert key in audit
    # The two seen-SOV rows were predicted correctly here.
    assert audit["value_accuracy_seen_subject_object_value"]["accuracy"] == pytest.approx(1.0)
    assert audit["value_accuracy_seen_subject_object_value"]["count"] == 2
    assert audit["value_accuracy_unseen_subject_object_value"]["accuracy"] == pytest.approx(0.0)
    assert audit["combo_novelty"]["subject_object_value_combo_seen_rate"] == pytest.approx(0.5)
    # Confusions recorded for the two wrong rows.
    assert len(audit["value_top_confusions"]) == 2
    assert len(audit["failure_examples"]) == 2


# --------------------------------------------------------------------------- #
# Test 2: value rank diagnostics.
# --------------------------------------------------------------------------- #


def test_value_rank_diagnostics():
    # Row0 gold=2 sits at rank 1; row1 gold=3 sits at rank 1.
    logits = torch.tensor([[5.0, 1.0, 3.0, 0.0], [0.0, 1.0, 5.0, 3.0]])
    labels = torch.tensor([2, 3])
    diag = value_rank_diagnostics(logits, labels)
    assert diag["value_top1_accuracy"] == pytest.approx(0.0)
    assert diag["value_top2_accuracy"] == pytest.approx(1.0)
    assert diag["value_top3_accuracy"] == pytest.approx(1.0)
    assert diag["mean_gold_value_rank"] == pytest.approx(1.0)
    assert diag["median_gold_value_rank"] == pytest.approx(1.0)
    # Both wrong: margin_wrong = gold_logit - top1 = 3 - 5 = -2 for both.
    assert diag["value_margin_wrong_mean"] == pytest.approx(-2.0)

    # All correct case.
    perfect = value_rank_diagnostics(
        torch.tensor([[5.0, 1.0, 0.0], [0.0, 5.0, 1.0]]),
        torch.tensor([0, 1]),
    )
    assert perfect["value_top1_accuracy"] == pytest.approx(1.0)
    assert perfect["mean_gold_value_rank"] == pytest.approx(0.0)
    assert perfect["value_margin_correct_mean"] == pytest.approx(4.0)


# --------------------------------------------------------------------------- #
# Test 3: value candidate reranker shapes.
# --------------------------------------------------------------------------- #


def test_value_candidate_reranker_shapes():
    spec = get_tiny_schema_spec("tiny_256")
    value_size = spec.field_sizes["value"]
    slots = torch.randn(3, 12, 16)
    for top_k in (4, 8, 16):
        model = _build_value_head(
            "value_candidate_reranker",
            decode_mode="rerank",
            candidate_top_k=top_k,
        )
        outputs = model.training_outputs(slots)
        assert outputs["value_compat_logits"].shape == (3, value_size)
        assert outputs["combined_value_scores"].shape == (3, value_size)
        decoded = model(slots)
        assert decoded["value"].shape == (3, value_size)
        # Exactly top_k candidates are scored; the rest are masked to -inf.
        finite = torch.isfinite(decoded["value"]).sum(dim=1)
        assert torch.all(finite == top_k)
        for field in FIELD_NAMES:
            assert decoded[field].shape == (3, spec.field_sizes[field])


# --------------------------------------------------------------------------- #
# Test 4: subject->object->value scorer shapes.
# --------------------------------------------------------------------------- #


def test_subject_object_to_value_scorer_shapes():
    spec = get_tiny_schema_spec("tiny_256")
    value_size = spec.field_sizes["value"]
    slots = torch.randn(4, 12, 16)
    model = _build_value_head(
        "subject_object_to_value_scorer", decode_mode="scorer"
    )
    outputs = model.training_outputs(slots)
    compat = outputs["value_compat_logits"]
    assert compat.shape == (4, value_size)
    # Positive scores and sampled negative scores both gather cleanly.
    value_labels = torch.tensor([0, 1, 2, 3])
    negatives = torch.tensor(
        [[1, 2, 3], [0, 2, 3], [0, 1, 3], [0, 1, 2]]
    )
    positive = compat.gather(1, value_labels.unsqueeze(1))
    negative = compat.gather(1, negatives)
    assert positive.shape == (4, 1)
    assert negative.shape == (4, 3)
    loss = _value_contrastive_loss(
        combined_scores=outputs["combined_value_scores"],
        value_labels=value_labels,
        negatives=negatives,
    )
    assert torch.isfinite(loss)

    # The standalone scoring head also produces per-candidate compatibility.
    head = ValueScoringHead(
        head_variant="subject_object_to_value_scorer",
        state_dim=8,
        context_dim=4,
        value_size=value_size,
    )
    states = {field: torch.randn(2, 8) for field in FIELD_NAMES}
    scores = head.compatibility(states, torch.randn(2, 4))
    assert scores.shape == (2, value_size)


# --------------------------------------------------------------------------- #
# Test 5: hard negative sampling.
# --------------------------------------------------------------------------- #


def test_hard_negative_sampling():
    spec = get_tiny_schema_spec("tiny_256")
    value_size = spec.field_sizes["value"]
    train_fields = [
        _fields("subject_00", "object_00", "value_00"),
        _fields("subject_00", "object_00", "value_05"),
        _fields("subject_01", "object_01", "value_09"),
        _fields("subject_00", "object_02", "value_05"),
    ]
    cooccurrence = build_value_cooccurrence(train_fields, spec)
    assert cooccurrence["subject_to_values"][0] == {0, 5}
    assert cooccurrence["object_to_values"][0] == {0, 5}

    value_labels = torch.tensor([0, 9])
    subject_labels = torch.tensor([0, 1])
    object_labels = torch.tensor([0, 1])
    base_value_logits = torch.zeros(2, value_size)
    base_value_logits[0, 7] = 10.0  # model's top wrong value for row 0
    base_value_logits[0, 0] = 5.0
    generator = torch.Generator(device="cpu").manual_seed(0)
    negatives = sample_value_hard_negatives(
        value_labels=value_labels,
        subject_labels=subject_labels,
        object_labels=object_labels,
        base_value_logits=base_value_logits,
        cooccurrence=cooccurrence,
        value_size=value_size,
        count=8,
        generator=generator,
    )
    assert negatives.shape == (2, 8)
    # Gold never appears as a negative.
    for row in range(2):
        assert int(value_labels[row]) not in negatives[row].tolist()
    # The model's top wrong value is selected as a hard negative.
    assert 7 in negatives[0].tolist()
    # A same-subject co-occurring value (value_05 -> index 5) is reachable.
    assert 5 in negatives[0].tolist()

    # Count is capped at value_size - 1 (cannot exceed available negatives).
    capped = sample_value_hard_negatives(
        value_labels=value_labels,
        subject_labels=subject_labels,
        object_labels=object_labels,
        base_value_logits=base_value_logits,
        cooccurrence=cooccurrence,
        value_size=value_size,
        count=value_size + 50,
        generator=generator,
    )
    assert capped.shape == (2, value_size - 1)

    report = hard_negative_sampling_report()
    assert report["excludes_target"] is True
    assert report["used_in_decode_path"] is False
    assert "top_wrong_values_from_current_model" in report["priority_order"]
    assert "same_train_frequency_values" in report["priority_order"]
    assert "values_seen_with_same_subject_in_train" in report["priority_order"]
    assert "values_seen_with_same_object_in_train" in report["priority_order"]
    assert "random_values" in report["priority_order"]


# --------------------------------------------------------------------------- #
# Test 6: no symbolic lookup for value in the decode path.
# --------------------------------------------------------------------------- #


def test_no_symbolic_lookup_for_value():
    # The decode (open) path must not consult training co-occurrence tables,
    # nearest-training retrieval, or any triple-keyed lookup.
    decode_sources = "\n".join(
        inspect.getsource(fn)
        for fn in (
            ValueBindingStage2Head.forward,
            ValueBindingStage2Head._decoded_value_scores,
            ValueBindingStage2Head._base_states_and_logits,
            ValueScoringHead.compatibility,
            ValueScoringHead._query_input,
        )
    )
    forbidden = (
        "cooccurrence",
        "subject_to_values",
        "object_to_values",
        "build_value_cooccurrence",
        "train_fields",
        "nearest",
        "lookup",
    )
    for term in forbidden:
        assert term not in decode_sources, term
    # Candidate scoring is a learned bilinear form over value embeddings.
    compat_source = inspect.getsource(ValueScoringHead.compatibility)
    assert "value_embedding" in compat_source
    assert "query_encoder" in compat_source
    # The decode entrypoint consumes only slot embeddings (no labels/targets).
    params = list(inspect.signature(ValueBindingStage2Head.forward).parameters)
    assert params == ["self", "slots"]


# --------------------------------------------------------------------------- #
# Test 7: value repair verdict logic.
# --------------------------------------------------------------------------- #


def _seed_row(
    value,
    *,
    subject=0.96,
    object_=0.92,
    date=0.95,
    avg=0.94,
    exact=0.62,
    gap=0.06,
    top3=0.95,
    top1=None,
    train_avg=0.97,
):
    return {
        "value_accuracy": value,
        "subject_accuracy": subject,
        "object_accuracy": object_,
        "date_accuracy": date,
        "heldout_average_field_accuracy": avg,
        "heldout_exact_tuple_accuracy": exact,
        "train_heldout_gap": gap,
        "value_top3_accuracy": top3,
        "value_top1_accuracy": value if top1 is None else top1,
        "train_average_field_accuracy": train_avg,
    }


def test_value_repair_verdict_logic():
    repaired = [_seed_row(0.80), _seed_row(0.79), _seed_row(0.81)]
    assert tiny256_value_binding_repair_gate(repaired)
    assert tiny256_value_binding_verdict(repaired) == "VALUE_REPAIRED"

    partial = [_seed_row(0.77), _seed_row(0.76), _seed_row(0.77)]
    assert not tiny256_value_binding_repair_gate(partial)
    assert (
        tiny256_value_binding_verdict(partial) == "PARTIAL_VALUE_BINDING_SIGNAL"
    )

    regress = [
        _seed_row(0.80, subject=0.85),
        _seed_row(0.80, subject=0.86),
        _seed_row(0.80, subject=0.84),
    ]
    assert (
        tiny256_value_binding_verdict(regress)
        == "VALUE_REPAIR_REGRESSES_OTHER_FIELDS"
    )

    overfit = [
        _seed_row(0.71, train_avg=0.99, avg=0.90, top3=0.88),
        _seed_row(0.71, train_avg=0.99, avg=0.90, top3=0.88),
        _seed_row(0.71, train_avg=0.99, avg=0.90, top3=0.88),
    ]
    assert tiny256_value_binding_verdict(overfit) == "OVERFIT_VALUE_BINDING"

    no_gain = [
        _seed_row(0.71, top3=0.80, exact=0.60, train_avg=0.93),
        _seed_row(0.71, top3=0.80, exact=0.60, train_avg=0.93),
        _seed_row(0.70, top3=0.80, exact=0.60, train_avg=0.93),
    ]
    assert tiny256_value_binding_verdict(no_gain) == "NO_VALUE_GAIN"


# --------------------------------------------------------------------------- #
# Test 8: Stage-3 smoke gated by value repair.
# --------------------------------------------------------------------------- #


def test_stage3_smoke_gated_by_value_repair():
    assert tiny256_value_repair_stage3_allowed("VALUE_REPAIRED")
    for verdict in (
        "PARTIAL_VALUE_BINDING_SIGNAL",
        "VALUE_REPAIR_REGRESSES_OTHER_FIELDS",
        "OVERFIT_VALUE_BINDING",
        "NO_VALUE_GAIN",
    ):
        assert not tiny256_value_repair_stage3_allowed(verdict)


# --------------------------------------------------------------------------- #
# Test 9: no valid-open boundary violation.
# --------------------------------------------------------------------------- #


def test_no_valid_open_boundary_violation():
    # The shared valid-open entrypoint must not import the encoder, renderer,
    # LLM, story plan, slot IDs/spans, or original report JSON.
    from argus_capsules.codec_text_to_slot import TextToSlotCapsule

    open_source = inspect.getsource(TextToSlotCapsule.open_artifact)
    for term in (
        "TinyNarrativeEncoder",
        "render_with_slot_spans",
        "gold_slot_ids",
        "slot_spans",
        "story_plan",
        "original_report",
        "target_json",
    ):
        assert term not in open_source, term

    # The value head, which is what Stage-3 smoke wraps, only consumes slot
    # embeddings recovered from carrier text -- never report/renderer/LLM state.
    value_decode = "\n".join(
        inspect.getsource(fn)
        for fn in (
            ValueBindingStage2Head.forward,
            ValueBindingStage2Head._decoded_value_scores,
        )
    )
    for term in (
        "TinyNarrativeEncoder",
        "render_with_slot_spans",
        "original_report",
        "target_json",
        "story_plan",
        "gold_slot_ids",
        "slot_spans",
        "base64",
        "b64decode",
    ):
        assert term not in value_decode, term


# --------------------------------------------------------------------------- #
# Test 10: artifact schema for the value-binding pass.
# --------------------------------------------------------------------------- #


def test_artifact_schema_tiny256_value_binding(tmp_path):
    paths = value_binding_artifact_paths(Path(tmp_path))
    assert set(paths) == set(VALUE_BINDING_REQUIRED_ARTIFACTS)
    # The audit, value diagnostics, and gated smoke artifacts are all named.
    expected = {
        "value_error_audit/value_error_audit.json",
        "value_error_audit/value_rank_diagnostics.csv",
        "value_error_audit/value_combo_novelty.csv",
        "value_error_audit/failure_examples.jsonl",
        "stage2_value_binding_metrics.json",
        "stage2_value_binding_variant_table.csv",
        "stage2_value_binding_seed_table.csv",
        "value_diagnostics.json",
        "value_rank_diagnostics.csv",
        "best_variant_config.json",
        "best_variant_checkpoint",
        "stage3_smoke_metrics.json",
    }
    assert expected <= set(paths)

    # When the real pass has produced artifacts, the key files exist on disk.
    real = Path("artifacts/private_capsules/tiny256_value_binding")
    if (real / "stage2_value_binding_metrics.json").exists():
        for name in (
            "stage2_value_binding_metrics.json",
            "value_diagnostics.json",
            "value_rank_diagnostics.csv",
            "best_variant_config.json",
            "stage3_smoke_metrics.json",
        ):
            assert (real / name).exists(), name


def test_value_head_loss_compatibility_matrix():
    # Decode mode is implied by the loss; scorer/rerank losses require a value
    # scoring head, the base CE/focal/baseline losses require the base head.
    assert value_decode_mode("value_candidate_rerank_loss") == "rerank"
    assert value_decode_mode("full_context_value_contrastive") == "scorer"
    assert value_decode_mode("value_upweighted_ce") == "base"
    assert value_head_compatible(
        "value_candidate_reranker", "value_candidate_rerank_loss"
    )
    assert value_head_compatible(
        "baseline_value_base", "value_upweighted_ce"
    )
    assert not value_head_compatible(
        "baseline_value_base", "full_context_value_contrastive"
    )
    assert set(VALUE_HEAD_VARIANTS) == {
        "baseline_value_base",
        "value_candidate_reranker",
        "subject_object_to_value_scorer",
        "object_context_to_value_scorer",
        "full_context_value_scorer",
    }
