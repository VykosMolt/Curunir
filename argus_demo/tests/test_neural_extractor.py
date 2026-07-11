from __future__ import annotations

import inspect
import json
import subprocess
import sys
from functools import lru_cache
from pathlib import Path

import pytest

from argus_neural.extractor_types import EntityCandidate, ExtractionResult, validate_extraction_result
from argus_neural.staging_adapter import to_staging_candidates, validate_staging_candidates
from argus_neural.neural_extractor import ArgusNeuralExtractorV0
from argus_neural.synthetic_dataset import dataset_quality, generate_dataset, write_dataset
from argus_neural.training import train_extractor
from argus_neural.universal_dataset import dataset_quality as universal_dataset_quality
from argus_neural.universal_dataset import generate_universal_dataset
from argus_neural.universal_evaluation import evaluate_deterministic_baseline
from argus_neural.universal_training import train_universal_extractor
from argus_neural.realistic_dataset import (
    generate_v2_realistic_dataset,
    validate_v2_row,
    v2_dataset_quality,
)
from argus_neural.realistic_evaluation import (
    PROTECTED_KERNEL_FILES,
    evaluate_by_split,
    scan_loaded_runtime_modules,
    scan_text_for_runtime_audit,
)
from argus_neural.universal_extractor import ArgusUniversalNeuralExtractorV1
import argus_neural.neural_extractor as runtime_module
import argus_neural.extractor_types as extractor_types_module
import argus_neural.features as features_module
import argus_neural.span_utils as span_utils_module
import argus_neural.staging_adapter as staging_module
import argus_neural.universal_extractor as universal_runtime_module

pytestmark = pytest.mark.no_db


def _tiny_extractor():
    dataset = generate_dataset(seed=3, n_train=24, n_dev=6, n_test=6)
    extractor, _ = train_extractor(dataset["train"], epochs=5, seed=3)
    return extractor, dataset


@lru_cache(maxsize=1)
def _tiny_universal_extractor():
    dataset = generate_universal_dataset(
        seed=13,
        n_train=80,
        n_dev=12,
        n_test=12,
        n_hard=10,
        n_no_keyword=10,
        n_keyword_bait=10,
        n_cross_format=10,
        n_abstention=10,
    )
    extractor, _ = train_universal_extractor(dataset["train"], epochs=4, seed=13)
    return extractor, dataset


@lru_cache(maxsize=1)
def _v2_dataset():
    return generate_v2_realistic_dataset()


@lru_cache(maxsize=1)
def _artifact_v2_extractor():
    return ArgusUniversalNeuralExtractorV1.load(
        "artifacts/universal_neural_extractor_v2_realistic_eval/model"
    )


@lru_cache(maxsize=1)
def _artifact_v1_extractor():
    return ArgusUniversalNeuralExtractorV1.load("artifacts/universal_neural_extractor_v1/model")


def test_candidate_span_validation_and_confidence_bounds():
    text = "Aster Data Group reviewed a note."
    good = ExtractionResult(
        entities=[
            EntityCandidate(
                text="Aster Data Group",
                entity_type="organization",
                start_char=0,
                end_char=16,
                sentence_index=0,
                source_id="s",
                confidence=0.8,
                extractor_name="test",
            )
        ],
        source_hash="",
    )
    validate_extraction_result(good, text)
    with pytest.raises(ValueError):
        EntityCandidate(
            text="bad",
            entity_type="organization",
            start_char=0,
            end_char=3,
            sentence_index=0,
            source_id="s",
            confidence=1.2,
            extractor_name="test",
        )


def test_synthetic_dataset_span_validity_and_special_subsets(tmp_path):
    dataset = generate_dataset(seed=5, n_train=8, n_dev=4, n_test=4)
    quality = write_dataset(dataset, tmp_path)
    assert quality["span_validity"] == 1.0
    assert quality["keyword_ablation_examples"] > 0
    assert quality["keyword_bait_negative_examples"] > 0
    assert quality["paraphrase_generalization_examples"] > 0
    assert quality["abstention_examples"] > 0
    assert (tmp_path / "keyword_ablation_test.jsonl").is_file()
    assert (tmp_path / "keyword_bait_negative_test.jsonl").is_file()
    assert (tmp_path / "paraphrase_generalization_test.jsonl").is_file()


def test_neural_extractor_runs_and_proposes_without_deterministic_proposals():
    extractor, dataset = _tiny_extractor()
    row = next(row for row in dataset["test"] if row["claims"])
    result = extractor.extract(row["source_text"], row["source_id"])
    validate_extraction_result(result, row["source_text"])
    assert result.diagnostics["deterministic_proposals_required"] is False
    assert result.diagnostics["runtime_candidate_source"] == "token_windows_and_sentence_pairs"
    assert result.entities
    assert result.claims


def test_abstain_no_unsupported_claim_for_noise_text():
    extractor, dataset = _tiny_extractor()
    row = dataset["keyword_bait_negative_test"][0]
    result = extractor.extract(row["source_text"], row["source_id"])
    validate_extraction_result(result, row["source_text"])
    assert not result.claims
    assert "no_claim_candidate_above_threshold" in result.abstentions


def test_model_save_load_smoke(tmp_path):
    extractor, dataset = _tiny_extractor()
    extractor.save(tmp_path)
    loaded = ArgusNeuralExtractorV0.load(tmp_path)
    row = dataset["test"][0]
    assert loaded.extract(row["source_text"], row["source_id"]).source_hash
    assert (tmp_path / "model.json").is_file()
    assert (tmp_path / "model_config.json").is_file()


def test_runtime_extraction_has_no_hardcoded_phrase_triggers():
    source = inspect.getsource(runtime_module)
    forbidden = ["reported", "confirmed", "because", "caused", "supports", "indicates", "_KW"]
    assert not [term for term in forbidden if term in source]


def test_no_internet_or_remote_llm_runtime_references():
    source = inspect.getsource(runtime_module)
    forbidden = ["requests.", "http://", "https://", "openai", "anthropic", "remote"]
    assert not [term for term in forbidden if term in source.lower()]


def test_neural_dataset_quality_verdict():
    dataset = generate_dataset(seed=9, n_train=8, n_dev=4, n_test=4)
    quality = dataset_quality(dataset)
    assert quality["verdict"] == "ARGUS_NEURAL_EXTRACTOR_DATASET = PASS_SYNTHETIC_V0"


def test_no_frozen_capsule_artifact_paths_in_neural_code():
    source = "\n".join(
        Path(path).read_text(encoding="utf-8")
        for path in [
            "argus_neural/neural_extractor.py",
            "argus_neural/synthetic_dataset.py",
            "argus_neural/run_neural_extractor_milestone.py",
        ]
    )
    assert "artifacts/private_capsules" not in source


def test_universal_extractor_accepts_source_text_without_source_type_and_metadata_is_provenance_only():
    extractor, dataset = _tiny_universal_extractor()
    row = next(row for row in dataset["test"] if row["claims"])
    first = extractor.extract_document(row["source_text"], source_id=row["source_id"], metadata={"batch": "a"})
    second = extractor.extract_document(row["source_text"], source_id=row["source_id"], metadata={"batch": "b"})
    validate_extraction_result(first, row["source_text"])
    validate_extraction_result(second, row["source_text"])
    assert first.source_metadata_echo == {"batch": "a"}
    assert second.source_metadata_echo == {"batch": "b"}
    first_payload = first.as_dict()
    second_payload = second.as_dict()
    first_payload["source_metadata_echo"] = {}
    second_payload["source_metadata_echo"] = {}
    assert first_payload == second_payload
    assert first.diagnostics["metadata_controls_extraction"] is False


def test_universal_extractor_proposes_direct_spans_dates_and_staging_candidates_validate():
    extractor, dataset = _tiny_universal_extractor()
    row = next(row for row in dataset["test"] if row["claims"] and row["dates"])
    result = extractor.extract_document(row["source_text"], row["source_id"], row.get("source_metadata"))
    validate_extraction_result(result, row["source_text"])
    assert result.diagnostics["deterministic_proposals_required"] is False
    assert result.diagnostics["runtime_candidate_source"] == "token_windows_sentence_spans_sentence_pairs"
    assert result.entities
    assert result.claims
    assert result.dates
    for candidate in [*result.entities, *result.claims, *result.relations, *result.dates]:
        assert candidate.source_id == row["source_id"]
        assert candidate.source_hash == row["source_hash"]
        assert 0.0 <= candidate.confidence <= 1.0
    staged = to_staging_candidates(result)
    validation = validate_staging_candidates(staged, row["source_text"])
    assert staged
    assert validation["pass_rate"] == 1.0
    assert {item.review_status for item in staged} == {"pending"}
    assert {item.promotion_status for item in staged} == {"not_promoted"}


def test_universal_runtime_has_no_source_specific_dispatch_or_keyword_trigger_terms():
    source = "\n".join(
        inspect.getsource(module)
        for module in [
            universal_runtime_module,
            features_module,
            span_utils_module,
            extractor_types_module,
            staging_module,
        ]
    ).lower()
    source_specific = [
        "ollama",
        "llama",
        "clearview",
        "discord",
        "source_type",
        "document_style",
        "markdown memo",
        "report format",
    ]
    keyword_triggers = ["reported", "confirmed", "because", "caused", "indicates", "_kw", "_anchors"]
    assert not [term for term in source_specific if term in source]
    assert not [term for term in keyword_triggers if term in source]


def test_universal_dataset_v1_quality_and_required_subsets():
    _, dataset = _tiny_universal_extractor()
    quality = universal_dataset_quality(dataset)
    assert quality["span_validity"] == 1.0
    assert quality["source_hash_present"] is True
    assert quality["hidden_marker_hits"] == 0
    assert quality["no_keyword_examples"] > 0
    assert quality["keyword_bait_negative_examples"] > 0
    assert quality["cross_sentence_relation_examples"] > 0
    assert quality["cross_paragraph_relation_examples"] > 0
    assert quality["multiple_document_styles"] is True
    assert quality["verdict"] == "ARGUS_UNIVERSAL_EXTRACTOR_DATASET_V1 = PASS_BROAD_SYNTHETIC"
    for split in ["hard_test", "no_keyword_test", "keyword_bait_negative_test", "cross_format_test", "abstention_test"]:
        assert dataset[split]


def test_universal_abstention_noise_and_keyword_bait_do_not_emit_unsupported_claims():
    extractor, dataset = _tiny_universal_extractor()
    for split in ["keyword_bait_negative_test", "abstention_test"]:
        for row in dataset[split][:3]:
            result = extractor.extract_document(row["source_text"], row["source_id"], row.get("source_metadata"))
            validate_extraction_result(result, row["source_text"])
            assert not result.claims
            assert "no_claim_candidate_above_threshold" in result.abstentions


def test_deterministic_baseline_remains_available_for_comparison():
    _, dataset = _tiny_universal_extractor()
    metrics = evaluate_deterministic_baseline(dataset["test"][:3])
    assert metrics["documents"] == 3
    assert "entity" in metrics
    assert "claim" in metrics
    assert "relation" in metrics


def test_universal_artifacts_state_no_production_or_capsule_claims():
    no_claims = Path("artifacts/universal_neural_extractor_v1/no_claims.md")
    assert no_claims.is_file()
    text = no_claims.read_text(encoding="utf-8").lower()
    required = [
        "no production extraction claim",
        "no autonomous truth extraction claim",
        "no real-world validation claim",
        "no crypto/capsule/open-boundary claim",
        "no hidden-source claim",
        "no claim that all hardcoded argus paths are already removed from production",
        "synthetic/local diagnostic prototype only",
    ]
    assert all(item in text for item in required)
    v1_source = "\n".join(
        Path(path).read_text(encoding="utf-8")
        for path in [
            "argus_neural/universal_extractor.py",
            "argus_neural/universal_dataset.py",
            "argus_neural/run_universal_extractor_v1.py",
            "argus_neural/staging_adapter.py",
        ]
    )
    assert "artifacts/private_capsules" not in v1_source
    assert "argus_capsules" not in v1_source


def test_v2_realistic_dataset_span_validity_hidden_markers_and_quality():
    dataset = _v2_dataset()
    quality = v2_dataset_quality(dataset, v1_dataset_dir="artifacts/universal_neural_extractor_v1/dataset")
    assert quality["span_validity"] == 1.0
    assert quality["source_hash_present"] is True
    assert quality["hidden_marker_hits"] == 0
    assert quality["v1_exact_source_text_overlap_count"] == 0
    assert quality["exact_duplicate_source_text_count"] == 0
    assert quality["verdict"] == "ARGUS_UNIVERSAL_EXTRACTOR_V2_DATASET = PASS_REALISTIC_PUBLIC_SAFE_FIXTURES"
    for rows in dataset.values():
        for row in rows:
            validate_v2_row(row)
            assert "GOLD_LABEL" not in row["source_text"]
            lowered = row["source_text"].lower()
            assert "capsule" not in lowered
            assert "crypto" not in lowered
            assert "open-boundary" not in lowered
            assert "production" not in lowered


def test_v2_dataset_metadata_and_style_labels_do_not_affect_runtime_logic():
    extractor = _artifact_v2_extractor()
    row = next(row for row in _v2_dataset()["realistic_test"] if row["claims"])
    first = extractor.extract_document(row["source_text"], row["source_id"], {"document_style": "markdown_mixed"})
    second = extractor.extract_document(row["source_text"], row["source_id"], {"document_style": "table_text"})
    validate_extraction_result(first, row["source_text"])
    validate_extraction_result(second, row["source_text"])
    first_payload = first.as_dict()
    second_payload = second.as_dict()
    first_payload["source_metadata_echo"] = {}
    second_payload["source_metadata_echo"] = {}
    assert first_payload == second_payload
    assert first.diagnostics["metadata_controls_extraction"] is False


def test_v1_on_v2_evaluation_runs():
    dataset = _v2_dataset()
    extractor = _artifact_v1_extractor()
    metrics = evaluate_by_split(extractor, {"realistic_test": dataset["realistic_test"][:3]})
    assert metrics["realistic_test"]["documents"] == 3
    assert "claim" in metrics["realistic_test"]
    assert metrics["realistic_test"]["valid_span_rate"] == 1.0


def test_v2_evaluation_runs_and_stageable_candidates_validate():
    dataset = _v2_dataset()
    extractor = _artifact_v2_extractor()
    metrics = evaluate_by_split(extractor, {"realistic_test": dataset["realistic_test"][:3]})
    assert metrics["realistic_test"]["documents"] == 3
    assert metrics["realistic_test"]["valid_span_rate"] == 1.0
    row = next(row for row in dataset["realistic_test"] if row["claims"])
    result = extractor.extract_document(row["source_text"], row["source_id"], row.get("source_metadata"))
    staged = to_staging_candidates(result)
    validation = validate_staging_candidates(staged, row["source_text"])
    assert validation["pass_rate"] == 1.0
    assert all(item.review_status == "pending" for item in staged)
    assert all(item.promotion_status == "not_promoted" for item in staged)


def test_v2_source_path_audit_passes_and_catches_forbidden_dispatch():
    audit = scan_loaded_runtime_modules()
    assert audit["verdict"] == "ARGUS_UNIVERSAL_EXTRACTOR_V2_SOURCE_PATH_AUDIT = PASS_NO_SOURCE_SPECIFIC_PATHS"
    bad = scan_text_for_runtime_audit({
        "bad_runtime.py": "if document_style == 'markdown':\n    extract_from_named_platform()\n# reported trigger\n"
    })
    assert bad["source_specific_hits"]
    assert bad["keyword_trigger_hits"]
    assert bad["verdict"] == "ARGUS_UNIVERSAL_EXTRACTOR_V2_SOURCE_PATH_AUDIT = FAIL_SOURCE_SPECIFIC_EXTRACTION"


def test_v2_staging_cli_validates_candidates(tmp_path):
    dataset = _v2_dataset()
    row = next(row for row in dataset["realistic_test"] if row["claims"])
    input_path = tmp_path / "input.txt"
    out_path = tmp_path / "staging.jsonl"
    input_path.write_text(row["source_text"], encoding="utf-8")
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "argus_neural.run_universal_staging_cli",
            str(input_path),
            "--model-dir",
            "artifacts/universal_neural_extractor_v2_realistic_eval/model",
            "--out",
            str(out_path),
            "--source-id",
            row["source_id"],
        ],
        cwd=Path.cwd(),
        check=True,
        capture_output=True,
        text=True,
    )
    payload = json.loads(completed.stdout)
    assert payload["validation"]["pass_rate"] == 1.0
    assert out_path.is_file()
    rows = [json.loads(line) for line in out_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert rows
    assert {item["review_status"] for item in rows} == {"pending"}
    assert {item["promotion_status"] for item in rows} == {"not_promoted"}


def test_v2_negative_abstention_docs_do_not_create_fake_claims_or_relations():
    extractor = _artifact_v2_extractor()
    for split in ["negative_abstention_test", "relation_bait_test"]:
        for row in _v2_dataset()[split][:4]:
            result = extractor.extract_document(row["source_text"], row["source_id"], row.get("source_metadata"))
            validate_extraction_result(result, row["source_text"])
            assert not result.claims
            assert not result.relations
            assert "no_claim_candidate_above_threshold" in result.abstentions


def test_v2_artifacts_do_not_touch_capsules_or_protected_kernel_paths():
    v2_root = Path("artifacts/universal_neural_extractor_v2_realistic_eval")
    assert v2_root.is_dir()
    assert "private_capsules" not in str(v2_root)
    for path in v2_root.rglob("*"):
        assert "artifacts/private_capsules" not in str(path)
    for protected in PROTECTED_KERNEL_FILES:
        assert not (v2_root / protected).exists()
    v2_source = "\n".join(
        Path(path).read_text(encoding="utf-8")
        for path in [
            "argus_neural/realistic_dataset.py",
            "argus_neural/realistic_evaluation.py",
            "argus_neural/run_universal_extractor_v2_realistic_eval.py",
        ]
    )
    assert "artifacts/private_capsules" not in v2_source
    assert "argus_capsules" not in v2_source


# ---------------------------------------------------------------------------
# V3 segmentation/threading milestone tests
# ---------------------------------------------------------------------------

from argus_neural.structural_segmenter import SEGMENT_TYPES, segment_document
from argus_neural.thread_graph import EDGE_TYPES, build_thread_graph
from argus_neural.segmentation_dataset import (
    generate_v3_segmentation_dataset,
    v3_dataset_quality,
    validate_v3_row,
)
from argus_neural.universal_extractor_v3 import (
    ArgusUniversalNeuralExtractorV3,
    structural_candidate_spans,
)
import argus_neural.structural_segmenter as segmenter_module
import argus_neural.thread_graph as thread_graph_module
import argus_neural.universal_extractor_v3 as universal_v3_module

V3_STRESS_DOC = """# Title

Intro paragraph line one
still the same paragraph.

- bullet one with value 42
  continuation of bullet one.
- [ ] task item

1. first numbered
2) second numbered

```
code line = 1
```

> quoted note says 17 cards.
>> nested quote.

Ari Lane: the count moved to 12.
Status: open
key = value

09:15 system check ok
unit | item | note
"""


@lru_cache(maxsize=1)
def _v3_dataset():
    return generate_v3_segmentation_dataset()


@lru_cache(maxsize=1)
def _artifact_v3_extractor():
    return ArgusUniversalNeuralExtractorV3.load(
        "artifacts/universal_neural_extractor_v3_segmentation_threading/model"
    )


def test_segmenter_preserves_spans_loses_no_text_and_does_not_overlap():
    result = segment_document(V3_STRESS_DOC)
    diag = result.diagnostics
    assert diag["coverage_exact"] is True
    assert diag["text_loss_free"] is True
    assert diag["overlap_char_errors"] == 0
    assert diag["span_surface_errors"] == 0
    for seg in result.segments:
        assert V3_STRESS_DOC[seg.start_char:seg.end_char] == seg.text
        assert seg.segment_type in SEGMENT_TYPES


def test_segmenter_recognizes_structural_families():
    result = segment_document(V3_STRESS_DOC)
    types = {seg.segment_type for seg in result.segments}
    assert {"heading", "paragraph", "bullet", "numbered_item", "code_block",
            "quote", "speaker_turn", "key_value_row", "log_line", "table_row",
            "continuation"} <= types
    quotes = [seg for seg in result.segments if seg.is_quote]
    assert any(seg.nesting_level == 2 for seg in quotes)
    code = [seg for seg in result.segments if seg.is_code_block]
    assert len(code) == 1 and code[0].text.startswith("```")


def test_segmenter_keeps_prose_adjacent_to_code_fences_extractable():
    text = "```\nsample = 1\n```\nThe desk logged 14 crates today."
    result = segment_document(text)
    spans = {(s.start_char, s.end_char): s.segment_type for s in result.segments}
    prose = [t for (start, end), t in spans.items() if "crates" in text[start:end]]
    assert prose and prose[0] != "code_block"


def test_segmenter_links_continuation_lines_to_parents():
    result = segment_document("- first pass total 71 bins\n  counted at the dock\n")
    cont = [seg for seg in result.segments if seg.is_continuation]
    assert cont and cont[0].parent_segment_id is not None


def test_thread_graph_edges_are_source_agnostic_and_span_valid():
    row = _v3_dataset()["contradiction_thread_test"][0]
    text = row["source_text"]
    graph = build_thread_graph(text, segment_document(text))
    assert graph.edges
    for edge in graph.edges:
        assert edge.edge_type in EDGE_TYPES
        start, end = edge.evidence_span
        assert 0 <= start <= end <= len(text)
    assert graph.diagnostics["emits_final_relations"] is False
    lowered = (inspect.getsource(thread_graph_module) + inspect.getsource(segmenter_module)).lower()
    for term in ["ollama", "clearview", "discord", "slack", "source_type", "document_style"]:
        assert term not in lowered


def test_v3_runtime_has_no_source_specific_dispatch_or_keyword_triggers():
    source = "\n".join(
        inspect.getsource(module)
        for module in [universal_v3_module, segmenter_module, thread_graph_module]
    ).lower()
    for term in ["ollama", "llama", "clearview", "discord", "source_type",
                 "document_style", "_kw", "_anchors"]:
        assert term not in source


def test_v3_dataset_quality_and_candidate_reachability():
    dataset = _v3_dataset()
    quality = v3_dataset_quality(dataset, v2_dataset=_v2_dataset())
    assert quality["span_validity"] == 1.0
    assert quality["hidden_marker_hits"] == 0
    assert quality["v2_exact_source_text_overlap_count"] == 0
    assert quality["exact_duplicate_source_text_count"] == 0
    assert quality["candidate_reachability"]["claim_reachability"] == 1.0
    assert quality["cross_segment_relation_examples"] > 0
    assert quality["verdict"].endswith("PASS_SEGMENTATION_STRESS_FIXTURES")
    for rows in dataset.values():
        for row in rows:
            validate_v3_row(row)


def test_v3_structural_candidates_cover_v2_markdown_and_thread_claims():
    dataset = _v2_dataset()
    for split in ["markdown_mixed_test", "threaded_notes_test"]:
        for row in dataset[split]:
            text = row["source_text"]
            keys = {
                (span.start, span.end)
                for span, _, _ in structural_candidate_spans(text, segment_document(text))
            }
            for claim in row["claims"]:
                assert (claim["start_char"], claim["end_char"]) in keys


def test_v2_on_v3_baseline_runs():
    extractor = _artifact_v2_extractor()
    rows = _v3_dataset()["markdown_thread_test"][:2]
    metrics = evaluate_by_split(extractor, {"markdown_thread_test": rows})
    assert metrics["markdown_thread_test"]["documents"] == 2
    assert metrics["markdown_thread_test"]["valid_span_rate"] == 1.0


def test_v3_extractor_runs_on_structural_docs_and_metadata_is_echo_only():
    extractor = _artifact_v3_extractor()
    row = next(r for r in _v3_dataset()["markdown_thread_test"] if r["claims"])
    first = extractor.extract_document(row["source_text"], row["source_id"], {"note": "a"})
    second = extractor.extract_document(row["source_text"], row["source_id"], {"note": "b"})
    validate_extraction_result(first, row["source_text"])
    first_payload = first.as_dict()
    second_payload = second.as_dict()
    first_payload["source_metadata_echo"] = {}
    second_payload["source_metadata_echo"] = {}
    assert first_payload == second_payload
    assert first.diagnostics["metadata_controls_extraction"] is False
    assert first.diagnostics["deterministic_proposals_required"] is False
    assert first.claims
    for claim in first.claims:
        assert claim.structure and claim.structure["segment_id"]


def test_v3_structure_negative_docs_do_not_emit_fake_claims():
    extractor = _artifact_v3_extractor()
    for row in _v3_dataset()["structure_negative_test"][:6]:
        result = extractor.extract_document(row["source_text"], row["source_id"], row.get("source_metadata"))
        validate_extraction_result(result, row["source_text"])
        assert not result.claims
        assert not result.relations


def test_v3_staging_candidates_validate_and_stay_pending():
    extractor = _artifact_v3_extractor()
    row = next(r for r in _v3_dataset()["quote_reply_test"] if r["claims"])
    result = extractor.extract_document(row["source_text"], row["source_id"], row.get("source_metadata"))
    staged = to_staging_candidates(result)
    validation = validate_staging_candidates(staged, row["source_text"])
    assert staged
    assert validation["pass_rate"] == 1.0
    assert {item.review_status for item in staged} == {"pending"}
    assert {item.promotion_status for item in staged} == {"not_promoted"}


def test_v3_staging_cli_works_on_structural_doc(tmp_path):
    row = _v3_dataset()["markdown_thread_test"][0]
    input_path = tmp_path / "structural.txt"
    out_path = tmp_path / "staging.jsonl"
    input_path.write_text(row["source_text"], encoding="utf-8")
    completed = subprocess.run(
        [
            sys.executable, "-m", "argus_neural.run_universal_staging_cli",
            str(input_path),
            "--model-dir", "artifacts/universal_neural_extractor_v3_segmentation_threading/model",
            "--out", str(out_path),
            "--source-id", row["source_id"],
        ],
        cwd=Path.cwd(), check=True, capture_output=True, text=True,
    )
    payload = json.loads(completed.stdout)
    assert payload["validation"]["pass_rate"] == 1.0
    rows = [json.loads(line) for line in out_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert rows
    assert {item["review_status"] for item in rows} == {"pending"}


def test_v3_artifacts_do_not_touch_capsules_or_protected_kernel():
    v3_root = Path("artifacts/universal_neural_extractor_v3_segmentation_threading")
    assert v3_root.is_dir()
    for protected in PROTECTED_KERNEL_FILES:
        assert not (v3_root / protected).exists()
    v3_source = "\n".join(
        Path(path).read_text(encoding="utf-8")
        for path in [
            "argus_neural/structural_segmenter.py",
            "argus_neural/thread_graph.py",
            "argus_neural/universal_extractor_v3.py",
            "argus_neural/universal_training_v3.py",
            "argus_neural/segmentation_dataset.py",
            "argus_neural/run_universal_extractor_v3_segmentation_eval.py",
        ]
    )
    assert "artifacts/private_capsules" not in v3_source
    assert "argus_capsules" not in v3_source
