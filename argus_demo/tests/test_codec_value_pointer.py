"""Fast contracts for the tiny_256 value-as-pointer / value-localization pass.

These tests never run long CUDA training.  They exercise the value-localization
audit math, the neural pointer-head shapes, the pointer losses, the
gold-pointer oracle (diagnostic-only) contract, the no-symbolic-lookup
invariant, the pointer-repair verdict + Stage-3 gate logic, the open-path
boundary, and the artifact schema.
"""

import inspect
from pathlib import Path

import pytest
import torch

from argus_capsules.codec_value_pointer import (
    POINTER_HEAD_VARIANTS,
    POINTER_LOSS_VARIANTS,
    VALUE_POINTER_PREVIOUS_BEST,
    GoldPointerOracleClassifier,
    SingleSlotValueProbe,
    ValuePointerModule,
    compute_value_localization_audit,
    diagnostic_gold_value_locations,
    pointer_head_compatible,
    pointer_loss_needs_negatives,
    select_value_candidate_slots,
    tiny256_value_pointer_repair_gate,
    tiny256_value_pointer_stage3_allowed,
    tiny256_value_pointer_verdict,
    train_single_slot_value_probe,
    value_localization_verdict,
    value_pointer_loss,
    value_pointer_oracle_verdict,
)
from argus_capsules.codec_stage2_heads import _field_labels, predict_fields
from argus_capsules.run_codec_stage2_field_head_study import (
    VALUE_POINTER_REQUIRED_ARTIFACTS,
    value_pointer_artifact_paths,
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
LOCAL = 14  # value-assigned local indices i%7==4 -> {4, 11} => G=2
SLOT_COUNT = GLOBAL + LOCAL
INPUT_SIZE = 16
ASSIGNMENTS = [index % len(FIELD_NAMES) for index in range(LOCAL)]


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


def _build_pointer(head, *, candidate_top_k=3, multi_pointer_k=1):
    return ValuePointerModule(
        SPEC,
        input_size=INPUT_SIZE,
        slot_count=SLOT_COUNT,
        global_slot_count=GLOBAL,
        local_field_assignments=ASSIGNMENTS,
        head_variant=head,
        candidate_top_k=candidate_top_k,
        multi_pointer_k=multi_pointer_k,
    )


# --------------------------------------------------------------------------- #
# Test 1: value localization audit metrics.
# --------------------------------------------------------------------------- #


def test_value_localization_audit_metrics():
    torch.manual_seed(0)
    train_fields = _make_fields(32)
    heldout_fields = _make_fields(16)
    train_emb = torch.randn(32, SLOT_COUNT, INPUT_SIZE)
    heldout_emb = torch.randn(16, SLOT_COUNT, INPUT_SIZE)
    value_size = SPEC.field_sizes["value"]
    train_vl = _field_labels(train_fields, SPEC, torch.device("cpu"))["value"]
    probe = train_single_slot_value_probe(
        train_embeddings=train_emb,
        train_value_labels=train_vl,
        global_slot_count=GLOBAL,
        local_field_assignments=ASSIGNMENTS,
        value_size=value_size,
        epochs=2,
        seed=0,
        device=torch.device("cpu"),
    )
    # Diagnostic locations carry both gold (label-using) and label-free saliency.
    loc = diagnostic_gold_value_locations(
        probe=probe,
        embeddings=heldout_emb,
        value_labels=_field_labels(heldout_fields, SPEC, torch.device("cpu"))["value"],
        global_slot_count=GLOBAL,
        local_field_assignments=ASSIGNMENTS,
        device=torch.device("cpu"),
    )
    group = loc["candidate_count"]
    assert group == 2  # {local 4, local 11}
    assert loc["gold_value_location"].shape == (16,)
    assert torch.all(loc["gold_value_location"] < group)
    assert loc["saliency_attention"].shape == (16, group)
    # Attention rows are a distribution over candidate slots.
    assert torch.allclose(
        loc["saliency_attention"].sum(dim=1), torch.ones(16), atol=1e-5
    )

    predictions = [dict(f) for f in heldout_fields]
    predictions[0]["value"] = "value_31"  # one forced value error
    audit = compute_value_localization_audit(
        probe=probe,
        train_embeddings=train_emb,
        heldout_embeddings=heldout_emb,
        train_fields=train_fields,
        heldout_fields=heldout_fields,
        heldout_predictions=predictions,
        slot_confidence=torch.rand(16, SLOT_COUNT, 1),
        global_slot_count=GLOBAL,
        local_field_assignments=ASSIGNMENTS,
        spec=SPEC,
        device=torch.device("cpu"),
    )["audit"]
    for key in (
        "value_location_entropy",
        "value_location_oracle_accuracy",
        "value_location_top1_accuracy_if_known",
        "value_location_top3_accuracy_if_known",
        "best_single_position_accuracy",
        "mean_pool_probe_accuracy",
        "per_candidate_slot_accuracy",
        "attention_mass_on_gold_value_location",
        "attention_mass_on_predicted_value_location",
        "attention_mass_on_wrong_value_location",
        "slot_confidence_at_gold_value_location",
        "value_error_when_location_low_confidence",
        "value_error_when_location_high_entropy",
        "location_concentration_by_field",
        "VALUE_LOCALIZATION_AUDIT",
    ):
        assert key in audit, key
    assert 0.0 <= audit["value_location_oracle_accuracy"] <= 1.0
    assert 0.0 <= audit["mean_pool_probe_accuracy"] <= 1.0
    assert len(audit["per_candidate_slot_accuracy"]) == group
    assert audit["slot_confidence_at_gold_value_location"] is not None
    assert audit["VALUE_LOCALIZATION_AUDIT"] in {
        "LOCALIZED_VALUE_SIGNAL",
        "DISTRIBUTED_VALUE_SIGNAL",
        "WEAK_OR_NO_VALUE_LOCALIZATION",
        "MIXED_LOCAL_AND_DISTRIBUTED_SIGNAL",
    }


def test_value_localization_verdict_branches():
    def _audit(oracle, mean_pool, best_pos, norm_entropy):
        return {
            "value_location_oracle_accuracy": oracle,
            "best_single_position_accuracy": best_pos,
            "mean_pool_probe_accuracy": mean_pool,
            "value_location_entropy": {"normalized_mean": norm_entropy},
        }

    assert (
        value_localization_verdict(_audit(0.3, 0.4, 0.3, 0.5))
        == "WEAK_OR_NO_VALUE_LOCALIZATION"
    )
    assert (
        value_localization_verdict(_audit(0.88, 0.70, 0.6, 0.5))
        == "LOCALIZED_VALUE_SIGNAL"
    )
    assert (
        value_localization_verdict(_audit(0.62, 0.66, 0.5, 0.90))
        == "DISTRIBUTED_VALUE_SIGNAL"
    )


# --------------------------------------------------------------------------- #
# Test 2: pointer head produces a distribution over slot positions.
# --------------------------------------------------------------------------- #


def test_value_pointer_head_shapes():
    slots = torch.randn(5, SLOT_COUNT, INPUT_SIZE)
    model = _build_pointer("value_pointer_head").eval()
    outputs = model.training_outputs(slots)
    group = model.candidate_group_size
    assert group == 2
    assert outputs["pointer_logits"].shape == (5, group)
    assert outputs["pointer_dist"].shape == (5, group)
    assert torch.allclose(
        outputs["pointer_dist"].sum(dim=1), torch.ones(5), atol=1e-5
    )
    # The diagnostic pointer-only head reads value from the base value logits.
    decoded = model(slots)
    assert torch.allclose(decoded["value"], outputs["base_value_logits"])


# --------------------------------------------------------------------------- #
# Test 3: pointer-pooled rep feeds the value classifier.
# --------------------------------------------------------------------------- #


def test_value_pointer_plus_classifier_shapes():
    slots = torch.randn(4, SLOT_COUNT, INPUT_SIZE)
    value_size = SPEC.field_sizes["value"]
    model = _build_pointer("value_pointer_plus_classifier")
    outputs = model.training_outputs(slots)
    assert outputs["soft_pooled"].shape == (4, model.slot_dim)
    assert outputs["pooled_value_scores"].shape == (4, value_size)
    decoded = model(slots)
    assert decoded["value"].shape == (4, value_size)
    # Value scores differ from the raw base value logits (the pooled rep is used).
    assert not torch.allclose(decoded["value"], outputs["base_value_logits"])
    for field in FIELD_NAMES:
        assert decoded[field].shape == (4, SPEC.field_sizes[field])


# --------------------------------------------------------------------------- #
# Test 4: conditioned classifier consumes context + S/O/P/date + pooled.
# --------------------------------------------------------------------------- #


def test_value_pointer_conditioned_classifier_shapes():
    slots = torch.randn(3, SLOT_COUNT, INPUT_SIZE)
    value_size = SPEC.field_sizes["value"]
    model = _build_pointer("value_pointer_conditioned_classifier")
    outputs = model.training_outputs(slots)
    assert outputs["conditioned_value_scores"].shape == (3, value_size)
    decoded = model(slots)
    assert decoded["value"].shape == (3, value_size)
    # The conditioned query consumes the value field-query state, S/O/P/date
    # states, global context and the pointer-pooled value evidence.
    src = inspect.getsource(ValuePointerModule.training_outputs)
    assert "conditioned_value_query" in src
    assert "soft_pooled" in src


# --------------------------------------------------------------------------- #
# Test 5: multi-pointer pooler with k = 1, 2, 4.
# --------------------------------------------------------------------------- #


def test_value_multi_pointer_pooler_shapes():
    slots = torch.randn(4, SLOT_COUNT, INPUT_SIZE)
    value_size = SPEC.field_sizes["value"]
    for k in (1, 2, 4):
        model = _build_pointer("value_multi_pointer_pooler", multi_pointer_k=k)
        outputs = model.training_outputs(slots)
        assert outputs["multi_pooled"].shape == (4, model.slot_dim)
        assert outputs["multi_value_scores"].shape == (4, value_size)
        decoded = model(slots)
        assert decoded["value"].shape == (4, value_size)


# --------------------------------------------------------------------------- #
# Test 6: candidate verifier scores top-3 / top-5 candidates.
# --------------------------------------------------------------------------- #


def test_value_pointer_candidate_verifier_shapes():
    slots = torch.randn(4, SLOT_COUNT, INPUT_SIZE)
    value_size = SPEC.field_sizes["value"]
    for top_k in (3, 5):
        model = _build_pointer(
            "value_pointer_candidate_verifier", candidate_top_k=top_k
        )
        outputs = model.training_outputs(slots)
        assert outputs["verifier_value_scores"].shape == (4, value_size)
        decoded = model(slots)
        # Exactly top_k candidate values are scored; the rest masked to -inf.
        finite = torch.isfinite(decoded["value"]).sum(dim=1)
        assert torch.all(finite == top_k)


# --------------------------------------------------------------------------- #
# Test 7: pointer losses are finite.
# --------------------------------------------------------------------------- #


def test_pointer_losses():
    torch.manual_seed(0)
    slots = torch.randn(8, SLOT_COUNT, INPUT_SIZE)
    fields = _make_fields(8)
    labels = _field_labels(fields, SPEC, torch.device("cpu"))
    for head in POINTER_HEAD_VARIANTS:
        for loss in POINTER_LOSS_VARIANTS:
            if not pointer_head_compatible(head, loss):
                continue
            model = _build_pointer(head, candidate_top_k=3, multi_pointer_k=2)
            outputs = model.training_outputs(slots)
            gold_location = torch.randint(
                0, model.candidate_group_size, (8,)
            )
            negatives = (
                torch.randint(0, SPEC.field_sizes["value"], (8, 8))
                if pointer_loss_needs_negatives(loss)
                else None
            )
            result = value_pointer_loss(
                loss_variant=loss,
                outputs=outputs,
                labels=labels,
                gold_location=gold_location,
                model=model,
                spec=SPEC,
                binding_weight=0.5,
                pointer_aux_weight=0.5,
                value_loss_weight=1.0,
                entropy_regularization=0.05,
                candidate_top_k=3,
                negatives=negatives,
            )
            for key in ("total", "field_binding", "pointer_aux", "value"):
                assert torch.isfinite(result[key]).all(), (head, loss, key)
            # The aux-only loss carries no value term.
            if loss == "pointer_aux_only":
                assert float(result["value"]) == 0.0


# --------------------------------------------------------------------------- #
# Test 8: gold-pointer oracle is diagnostic-only.
# --------------------------------------------------------------------------- #


def test_gold_pointer_oracle_is_diagnostic_only():
    assert GoldPointerOracleClassifier.diagnostic_only is True
    model = GoldPointerOracleClassifier(
        SPEC,
        input_size=INPUT_SIZE,
        slot_count=SLOT_COUNT,
        global_slot_count=GLOBAL,
        local_field_assignments=ASSIGNMENTS,
    )
    # The oracle forward *requires* a gold_location argument -- it cannot run
    # without being handed the diagnostic location.
    params = list(inspect.signature(GoldPointerOracleClassifier.forward).parameters)
    assert params == ["self", "slots", "gold_location"]
    slots = torch.randn(4, SLOT_COUNT, INPUT_SIZE)
    gold_location = torch.zeros(4, dtype=torch.long)
    out = model(slots, gold_location)
    assert out["value"].shape == (4, SPEC.field_sizes["value"])
    # The learned pointer module's value decode never takes a location argument.
    learned_params = list(
        inspect.signature(ValuePointerModule.forward).parameters
    )
    assert learned_params == ["self", "slots"]


def test_pointer_oracle_verdict_branches():
    assert (
        value_pointer_oracle_verdict(
            oracle_value_accuracy=0.60, learned_pointer_value_accuracy=0.55
        )
        == "LOW_CEILING_REPRESENTATION_BOTTLENECK"
    )
    assert (
        value_pointer_oracle_verdict(
            oracle_value_accuracy=0.90, learned_pointer_value_accuracy=0.70
        )
        == "HIGH_CEILING_POINTER_SELECTION_BOTTLENECK"
    )
    assert (
        value_pointer_oracle_verdict(
            oracle_value_accuracy=0.86, learned_pointer_value_accuracy=0.84
        )
        == "POINTER_METHOD_NEAR_ORACLE"
    )


# --------------------------------------------------------------------------- #
# Test 9: no symbolic slot-to-value lookup in the decode path.
# --------------------------------------------------------------------------- #


def test_no_symbolic_slot_to_value_lookup():
    decode_sources = "\n".join(
        inspect.getsource(fn)
        for fn in (
            ValuePointerModule.forward,
            ValuePointerModule._decoded_value,
            ValuePointerModule.training_outputs,
            ValuePointerModule._pointer_logits,
            ValuePointerModule._soft_pool,
            ValuePointerModule._multi_pool,
            ValuePointerModule._compat,
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
        "value_table",
        "slot_to_value",
        "base64",
        "b64decode",
    )
    for term in forbidden:
        assert term not in decode_sources, term
    # The value scorer is a learned bilinear form over learned value embeddings.
    compat_src = inspect.getsource(ValuePointerModule._compat)
    assert "value_embedding" in compat_src
    # The pointer is a learned attention (einsum of a learned query over slots),
    # never a deterministic slot-index rule.
    ptr_src = inspect.getsource(ValuePointerModule._pointer_logits)
    assert "pointer_query" in ptr_src
    assert "einsum" in ptr_src
    # Decode entrypoints consume only slot embeddings -- no labels/locations.
    assert list(inspect.signature(ValuePointerModule.forward).parameters) == [
        "self",
        "slots",
    ]


# --------------------------------------------------------------------------- #
# Test 10: pointer-repair verdict logic.
# --------------------------------------------------------------------------- #


def _seed_row(
    value,
    *,
    subject=0.96,
    object_=0.96,
    date=0.96,
    avg=0.94,
    exact=0.63,
    gap=0.06,
    top1=None,
    top3=0.96,
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
        "value_top1_accuracy": value if top1 is None else top1,
        "value_top3_accuracy": top3,
        "train_average_field_accuracy": train_avg,
    }


def test_pointer_repair_verdict_logic():
    # REPAIRED: clears every gate threshold over 3 seeds.
    repaired = [_seed_row(0.80) for _ in range(3)]
    assert tiny256_value_pointer_repair_gate(repaired)
    assert tiny256_value_pointer_verdict(repaired) == "VALUE_POINTER_REPAIRED"

    # PARTIAL: value materially up (+0.03) but mean below the 0.78 gate.
    partial = [_seed_row(0.77, avg=0.945, exact=0.65) for _ in range(3)]
    assert not tiny256_value_pointer_repair_gate(partial)
    assert tiny256_value_pointer_verdict(partial) == "PARTIAL_POINTER_SIGNAL"

    # REGRESSES: value up but object collapses below 0.90.
    regress = [_seed_row(0.80, object_=0.85) for _ in range(3)]
    assert (
        tiny256_value_pointer_verdict(regress)
        == "POINTER_REPAIR_REGRESSES_OTHER_FIELDS"
    )

    # SELECTION BOTTLENECK: oracle high, learned value low, no improvement.
    bottleneck = [_seed_row(0.72) for _ in range(3)]
    assert (
        tiny256_value_pointer_verdict(bottleneck, oracle_value_accuracy=0.90)
        == "POINTER_SELECTION_BOTTLENECK"
    )

    # OVERFIT: train high, heldout value flat, oracle not high.
    overfit = [_seed_row(0.72, train_avg=0.99, avg=0.93) for _ in range(3)]
    assert (
        tiny256_value_pointer_verdict(overfit) == "OVERFIT_POINTER_BINDING"
    )

    # NO GAIN: nothing improved, no overfit, no oracle bottleneck.
    flat = [_seed_row(0.72, train_avg=0.95, avg=0.93, exact=0.64, top3=0.94)
            for _ in range(3)]
    assert tiny256_value_pointer_verdict(flat) == "NO_POINTER_GAIN"


# --------------------------------------------------------------------------- #
# Test 11: Stage-3 smoke gated by pointer repair.
# --------------------------------------------------------------------------- #


def test_stage3_smoke_gated_by_pointer_repair():
    assert tiny256_value_pointer_stage3_allowed("VALUE_POINTER_REPAIRED")
    for verdict in (
        "PARTIAL_POINTER_SIGNAL",
        "POINTER_REPAIR_REGRESSES_OTHER_FIELDS",
        "POINTER_SELECTION_BOTTLENECK",
        "OVERFIT_POINTER_BINDING",
        "NO_POINTER_GAIN",
    ):
        assert not tiny256_value_pointer_stage3_allowed(verdict)


# --------------------------------------------------------------------------- #
# Test 12: no valid-open boundary violation (pointer).
# --------------------------------------------------------------------------- #


def test_no_valid_open_boundary_violation_pointer():
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

    # The value-pointer decode path (what a Stage-3 smoke would wrap) consumes
    # only recovered slot embeddings -- never encoder/renderer/LLM/gold state.
    decode = "\n".join(
        inspect.getsource(fn)
        for fn in (
            ValuePointerModule.forward,
            ValuePointerModule._decoded_value,
            ValuePointerModule.training_outputs,
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
        "gold_location",
        "gold_value_location",
        "base64",
        "b64decode",
    ):
        assert term not in decode, term


# --------------------------------------------------------------------------- #
# Test 13: artifact schema for the value-pointer pass.
# --------------------------------------------------------------------------- #


def test_artifact_schema_tiny256_value_pointer(tmp_path):
    paths = value_pointer_artifact_paths(Path(tmp_path))
    assert set(paths) == set(VALUE_POINTER_REQUIRED_ARTIFACTS)
    expected = {
        "config.json",
        "localization_audit/value_localization_audit.json",
        "localization_audit/value_localization_summary.md",
        "localization_audit/value_location_examples.jsonl",
        "localization_audit/value_location_confidence.csv",
        "localization_audit/value_location_attention.csv",
        "localization_audit/value_location_error_breakdown.csv",
        "stage2_pointer_metrics.json",
        "stage2_pointer_variant_table.csv",
        "stage2_pointer_seed_table.csv",
        "pointer_diagnostics.json",
        "pointer_diagnostics.csv",
        "value_diagnostics.json",
        "value_rank_diagnostics.csv",
        "value_pointer_failure_cases.jsonl",
        "value_pointer_confusion_examples.jsonl",
        "gold_pointer_oracle_metrics.json",
        "gold_pointer_oracle_summary.md",
        "best_variant_config.json",
        "best_variant_checkpoint",
    }
    assert expected <= set(paths)

    real = Path("artifacts/private_capsules/tiny256_value_pointer")
    if (real / "stage2_pointer_metrics.json").exists():
        for name in (
            "stage2_pointer_metrics.json",
            "localization_audit/value_localization_audit.json",
            "gold_pointer_oracle_metrics.json",
            "value_diagnostics.json",
            "best_variant_config.json",
        ):
            assert (real / name).exists(), name


def test_pointer_head_loss_compatibility_matrix():
    # The diagnostic pointer-only head carries only the aux-only loss.
    assert pointer_head_compatible("value_pointer_head", "pointer_aux_only")
    assert not pointer_head_compatible(
        "value_pointer_head", "pointer_aux_plus_value_ce"
    )
    # Value-repair heads never carry the aux-only loss.
    assert not pointer_head_compatible(
        "value_pointer_plus_classifier", "pointer_aux_only"
    )
    # Head-specific losses bind to their head.
    assert pointer_head_compatible(
        "value_pointer_conditioned_classifier", "pointer_conditioned_value_ce"
    )
    assert not pointer_head_compatible(
        "value_pointer_plus_classifier", "pointer_conditioned_value_ce"
    )
    assert pointer_head_compatible(
        "value_pointer_candidate_verifier", "pointer_candidate_verifier_loss"
    )
    assert set(POINTER_HEAD_VARIANTS) == {
        "value_pointer_head",
        "value_pointer_plus_classifier",
        "value_pointer_conditioned_classifier",
        "value_multi_pointer_pooler",
        "value_pointer_candidate_verifier",
    }
    assert VALUE_POINTER_PREVIOUS_BEST["value_accuracy"] == pytest.approx(0.7266)
