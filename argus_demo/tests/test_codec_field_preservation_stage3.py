"""Fast contracts for the tiny_256 Stage-3 valid-open field-preservation path.

These tests never run long CUDA training.  They exercise the checkpoint loader
(student weights only, no teacher), the wrong-decoder guard, the open-path
boundary, the Stage-3 verdict logic, the artifact schema, and the no-symbolic
invariant for the valid-open field-preservation decoder.
"""

import inspect
import io
import json
import tokenize
from pathlib import Path
from types import SimpleNamespace

import pytest
import torch

from argus_capsules import codec_field_preservation as fpmod
from argus_capsules.codec_field_preservation import (
    FIELD_PRESERVATION_CHECKPOINT_TYPE,
    FIELD_PRESERVATION_MODULE_CLASS,
    FieldPreservationModule,
    field_preservation_loader_contract,
    field_preservation_stage3_verdict,
    load_field_preservation_decoder,
)
from argus_capsules.codec_stage2_heads import Stage2FieldHead
from argus_capsules.codec_text_to_slot import TextToSlotCapsule
from argus_capsules.run_codec_stage2_field_head_study import (
    STAGE3_FIELD_PRESERVATION_REQUIRED_ARTIFACTS,
    _aggregate_preservation_stage3,
    _load_stage2_smoke_head,
    _run_preservation_stage3_smoke,
    _write_preservation_stage3_artifacts,
    stage3_field_preservation_artifact_paths,
)
from argus_capsules.tiny_schema import FIELD_NAMES, get_tiny_schema_spec


@pytest.fixture(scope="session", autouse=True)
def _db_setup():
    yield


@pytest.fixture(autouse=True)
def clean_db():
    yield


SPEC = get_tiny_schema_spec("tiny_256")
GLOBAL = 2
LOCAL = 14
SLOT_COUNT = GLOBAL + LOCAL
INPUT_SIZE = 16
ASSIGNMENTS = [index % len(FIELD_NAMES) for index in range(LOCAL)]
DEVICE = torch.device("cpu")

_PRESERVATION_CONFIG = {
    "stage2_module_class": FIELD_PRESERVATION_MODULE_CLASS,
    "stage2_checkpoint_type": FIELD_PRESERVATION_CHECKPOINT_TYPE,
    "head": "value_multi_pointer_pooler",
    "loss": "date_feature_distillation",
    "input_mode": "recovered_slots",
    "candidate_top_k": 3,
    "multi_pointer_k": 1,
    "train_size": 256,
    "field_preservation_verdict": "VALUE_REPAIRED_WITH_DATE_PRESERVED",
}


def _build_module():
    return FieldPreservationModule(
        SPEC,
        input_size=INPUT_SIZE,
        slot_count=SLOT_COUNT,
        global_slot_count=GLOBAL,
        local_field_assignments=ASSIGNMENTS,
        separate_branch=False,
        candidate_top_k=3,
        multi_pointer_k=1,
    )


def _save_module(tmp_path: Path, name: str = "seed0.pt") -> Path:
    module = _build_module()
    path = tmp_path / name
    torch.save({k: v.cpu() for k, v in module.state_dict().items()}, path)
    return path


def _code_only(source: str) -> str:
    pieces = []
    for tok in tokenize.generate_tokens(io.StringIO(source).readline):
        if tok.type in (tokenize.STRING, tokenize.COMMENT):
            continue
        pieces.append(tok.string)
    return " ".join(pieces).lower()


# --------------------------------------------------------------------------- #
# 1. checkpoint loader.
# --------------------------------------------------------------------------- #


def test_field_preservation_checkpoint_loader(tmp_path):
    checkpoint = _save_module(tmp_path)
    model, contract, stripped = load_field_preservation_decoder(
        spec=SPEC,
        config=_PRESERVATION_CONFIG,
        checkpoint_path=checkpoint,
        input_size=INPUT_SIZE,
        slot_count=SLOT_COUNT,
        global_slot_count=GLOBAL,
        local_field_assignments=ASSIGNMENTS,
        device=DEVICE,
    )
    assert isinstance(model, FieldPreservationModule)
    assert stripped == []  # a clean student checkpoint has no teacher tensors
    assert contract["inference_uses_student_weights_only"] is True
    assert contract["teacher_required_at_inference"] is False

    # Incompatible base-head checkpoint must fail loudly.
    base = Stage2FieldHead(
        SPEC,
        input_size=INPUT_SIZE,
        slot_count=SLOT_COUNT,
        global_slot_count=GLOBAL,
        local_field_assignments=ASSIGNMENTS,
        variant="field_query_cross_attention_lite_plus_binding",
    )
    base_ckpt = tmp_path / "base.pt"
    torch.save(base.state_dict(), base_ckpt)
    with pytest.raises(RuntimeError):
        load_field_preservation_decoder(
            spec=SPEC,
            config=_PRESERVATION_CONFIG,
            checkpoint_path=base_ckpt,
            input_size=INPUT_SIZE,
            slot_count=SLOT_COUNT,
            global_slot_count=GLOBAL,
            local_field_assignments=ASSIGNMENTS,
            device=DEVICE,
        )

    # Missing module-class config must be rejected.
    with pytest.raises(ValueError):
        field_preservation_loader_contract(
            {"head": "value_multi_pointer_pooler", "loss": "date_feature_distillation"}
        )
    # Missing checkpoint file.
    with pytest.raises(FileNotFoundError):
        load_field_preservation_decoder(
            spec=SPEC,
            config=_PRESERVATION_CONFIG,
            checkpoint_path=tmp_path / "does_not_exist.pt",
            input_size=INPUT_SIZE,
            slot_count=SLOT_COUNT,
            global_slot_count=GLOBAL,
            local_field_assignments=ASSIGNMENTS,
            device=DEVICE,
        )


# --------------------------------------------------------------------------- #
# 2. inference needs no teacher.
# --------------------------------------------------------------------------- #


def test_field_preservation_inference_no_teacher(tmp_path):
    checkpoint = _save_module(tmp_path)
    # Strip-marker: even if a checkpoint carried teacher tensors, they are
    # removed and never required.
    state = torch.load(checkpoint, map_location="cpu", weights_only=False)
    state["date_teacher.weight"] = torch.zeros(2, 2)  # bogus teacher tensor
    torch.save(state, checkpoint)
    model, contract, stripped = load_field_preservation_decoder(
        spec=SPEC,
        config=_PRESERVATION_CONFIG,
        checkpoint_path=checkpoint,
        input_size=INPUT_SIZE,
        slot_count=SLOT_COUNT,
        global_slot_count=GLOBAL,
        local_field_assignments=ASSIGNMENTS,
        device=DEVICE,
    )
    assert "date_teacher.weight" in stripped  # teacher tensor was stripped
    # Inference runs with carrier-derived slot embeddings only — no teacher.
    out = model(torch.randn(4, SLOT_COUNT, INPUT_SIZE))
    assert set(out) == set(FIELD_NAMES)
    assert out["value"].shape == (4, SPEC.field_sizes["value"])
    assert not hasattr(model, "teacher")
    assert not hasattr(model, "date_teacher")


# --------------------------------------------------------------------------- #
# 3. Stage-3 dispatch actually loads the FieldPreservationModule decoder.
# --------------------------------------------------------------------------- #


def test_stage3_uses_field_preservation_decoder(tmp_path):
    checkpoint = _save_module(tmp_path)
    cache = {
        "representations": {
            "recovered_slots": (
                torch.randn(8, SLOT_COUNT, INPUT_SIZE),
                torch.randn(4, SLOT_COUNT, INPUT_SIZE),
            )
        },
        "config": SimpleNamespace(
            global_slot_count=GLOBAL, local_slot_count=LOCAL
        ),
    }
    model = _load_stage2_smoke_head(
        cache=cache,
        spec=SPEC,
        best_config=_PRESERVATION_CONFIG,
        checkpoint_path=checkpoint,
        device=DEVICE,
    )
    assert isinstance(model, FieldPreservationModule)

    # The wrong-decoder guard lives in _stage3_smoke_seed and surfaces as a
    # FAIL_WRONG_DECODER verdict; confirm the verdict mapping is wired.
    assert (
        field_preservation_stage3_verdict(
            integration_complete=True,
            ran=True,
            open_boundary_clean=True,
            decoder_is_field_preservation=False,
            negatives_all_zero=True,
            average_field=0.9,
            exact_tuple=0.5,
            hash_tracks_exact=True,
            carrier_drops_causal=True,
            beats_baselines=True,
        )
        == "FAIL_WRONG_DECODER"
    )
    # The dispatch wires expect_stage2_class so a base head cannot pass silently.
    runner = __import__(
        "argus_capsules.run_codec_stage2_field_head_study",
        fromlist=["_stage3_smoke_seed", "_build_trained_stage3_capsule"],
    )
    assert "expect_stage2_class" in inspect.getsource(runner._stage3_smoke_seed)
    # The wrong-decoder guard lives in the shared capsule builder.
    builder_src = inspect.getsource(runner._build_trained_stage3_capsule)
    assert "expect_stage2_class" in builder_src
    assert "wrong decoder" in builder_src.lower()


# --------------------------------------------------------------------------- #
# 4. open path cannot access targets / teacher.
# --------------------------------------------------------------------------- #


def test_open_path_no_target_access_field_preservation():
    # The capsule open API takes only the artifact (carrier text) + secret
    # conditioning — never targets, report JSON, gold slots, or a teacher.
    params = set(inspect.signature(TextToSlotCapsule.predict_fields).parameters)
    assert params <= {"self", "artifact", "conditioning", "force_authentic"}
    for forbidden in ("target", "report", "gold", "teacher", "label"):
        assert not any(forbidden in p for p in params)

    contract = field_preservation_loader_contract(_PRESERVATION_CONFIG)
    assert contract["teacher_required_at_inference"] is False
    assert contract["distillation_required_at_inference"] is False

    # The Stage-3 runner records an explicit open-boundary audit and never reads
    # target fields for prediction.
    runner_src = inspect.getsource(_write_preservation_stage3_artifacts)
    assert "open_boundary_audit.json" in runner_src
    assert "open_path_inputs_forbidden_and_absent" in runner_src
    for forbidden in (
        "original_report_json",
        "target_fields",
        "gold_slot_ids",
        "date_teacher",
        "teacher_logits",
    ):
        assert forbidden in runner_src


# --------------------------------------------------------------------------- #
# 5. Stage-3 verdict logic (all codes reachable).
# --------------------------------------------------------------------------- #


def test_stage3_verdict_logic_field_preservation():
    def v(**kw):
        base = dict(
            integration_complete=True,
            ran=True,
            open_boundary_clean=True,
            decoder_is_field_preservation=True,
            negatives_all_zero=True,
            average_field=0.85,
            exact_tuple=0.3,
            hash_tracks_exact=True,
            carrier_drops_causal=True,
            beats_baselines=True,
        )
        base.update(kw)
        return field_preservation_stage3_verdict(**base)

    assert v() == "PASS_WEAK_SMOKE"
    assert v(integration_complete=False) == "NOT_RUN_INTEGRATION_INCOMPLETE"
    assert v(ran=False) == "READY_NOT_RUN"
    assert v(open_boundary_clean=False) == "FAIL_OPEN_BOUNDARY"
    assert v(decoder_is_field_preservation=False) == "FAIL_WRONG_DECODER"
    assert v(negatives_all_zero=False) == "FAIL_SECURITY_CONTROLS"
    assert (
        v(average_field=0.6, exact_tuple=0.05, beats_baselines=True)
        == "PARTIAL_FIELD_SMOKE"
    )
    assert (
        v(
            average_field=0.5,
            exact_tuple=0.0,
            carrier_drops_causal=False,
            beats_baselines=False,
        )
        == "FAIL_NO_VALID_OPEN_SIGNAL"
    )
    # Security/boundary failures dominate quality.
    assert v(negatives_all_zero=False, average_field=0.99) == "FAIL_SECURITY_CONTROLS"


# --------------------------------------------------------------------------- #
# 6. artifact schema.
# --------------------------------------------------------------------------- #


def _full_seed_metrics(seed: int) -> dict:
    row = {
        "seed": seed,
        "stage3_heldout_exact_tuple_accuracy": 0.30,
        "stage3_heldout_average_field_accuracy": 0.85,
        "stage3_canonical_hash_match_rate": 0.30,
        "stage3_schema_validation_pass_rate": 0.30,
        "stage3_any_negative_accept_max": 0.0,
        "stage3_smoke_seed_pass": True,
        "stage3_field_accuracy_drop_when_carrier_randomized": 0.40,
        "stage3_field_accuracy_drop_when_story_swapped": 0.35,
        "stage3_field_accuracy_drop_when_paragraphs_shuffled": 0.10,
        "stage3_field_accuracy_drop_when_local_slots_changed": 0.20,
        "stage3_field_accuracy_drop_when_global_slots_changed": 0.15,
        "stage3_field_accuracy_drop_when_relevant_spans_deleted": 0.25,
        "stage3_field_accuracy_drop_when_irrelevant_spans_deleted": 0.05,
    }
    for key in (
        "wrong_secret",
        "wrong_salt",
        "wrong_version",
        "random_story",
        "same_distribution_impostor",
        "tamper",
        "story_swap",
        "story_swap_original_hash",
    ):
        row[f"stage3_{key}_any_valid_accept_rate"] = 0.0
    for field in FIELD_NAMES:
        row[f"stage3_{field}_accuracy"] = 0.86
    return row


def test_stage3_artifact_schema_field_preservation(tmp_path):
    required = set(STAGE3_FIELD_PRESERVATION_REQUIRED_ARTIFACTS)
    for name in (
        "config.json",
        "integration_summary.md",
        "loader_contract.json",
        "open_boundary_audit.json",
        "stage3_smoke_metrics.json",
        "stage3_seed_table.csv",
        "stage3_field_table.csv",
        "negative_control_table.csv",
        "carrier_causality_table.csv",
        "baseline_table.csv",
        "final_verdicts.json",
    ):
        assert name in required, name
    assert set(stage3_field_preservation_artifact_paths(tmp_path)) == required

    per_seed = [_full_seed_metrics(0), _full_seed_metrics(2)]
    contract = field_preservation_loader_contract(_PRESERVATION_CONFIG)
    aggregate, verdict = _aggregate_preservation_stage3(
        per_seed=per_seed,
        seeds=[0, 2],
        best_config=_PRESERVATION_CONFIG,
        contract=contract,
        decoder_ok=True,
    )
    assert verdict == "PASS_WEAK_SMOKE"
    assert aggregate["negatives_all_zero"] is True
    args = SimpleNamespace(
        heldout_examples=128, epochs=300, positive_only_epochs=100,
        negative_ramp_epochs=100, stage3_smoke_seed_count=2,
    )
    _write_preservation_stage3_artifacts(
        stage3_dir=tmp_path,
        preservation_dir=tmp_path / "src",
        args=args,
        best_config=_PRESERVATION_CONFIG,
        contract=contract,
        aggregate=aggregate,
        per_seed=per_seed,
        records=[{"dataset": "heldout", "case_id": "c0",
                  "target_fields": {"value": "value_00"},
                  "predicted_fields": {"value": "value_01"}}],
        seeds=[0, 2],
        device=DEVICE,
    )
    for name in required:
        assert (tmp_path / name).exists(), name
    boundary = json.loads((tmp_path / "open_boundary_audit.json").read_text())
    assert boundary["open_boundary_clean"] is True
    assert "target_fields" in boundary["open_path_inputs_forbidden_and_absent"]


# --------------------------------------------------------------------------- #
# 7. no symbolic inverse in the Stage-3 field-preservation path.
# --------------------------------------------------------------------------- #


def test_no_symbolic_inverse_field_preservation_stage3():
    code = _code_only(inspect.getsource(_run_preservation_stage3_smoke))
    code += _code_only(inspect.getsource(_write_preservation_stage3_artifacts))
    code += _code_only(inspect.getsource(load_field_preservation_decoder))
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
