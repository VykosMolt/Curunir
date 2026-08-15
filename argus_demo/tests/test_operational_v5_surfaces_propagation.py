from __future__ import annotations

import json
from pathlib import Path

import pytest

from curunir_operational.v5.adjudication import (
    build_frozen_corpus, build_reference_and_heldout, build_technical_annex,
    compare_and_repair, execute_blind_reviews, propagate_repairs, surface_metrics,
)
from curunir_operational.v5.operations import revalidate_kernel_proposals
from curunir_operational.v5.campaign import finalize_sanitized_model_panel
from curunir_operational.v5.epistemic_repairs import (
    build_corrected_packets, build_semantic_overlays, build_verification_packets, infer_temporal_scope,
    validate_epistemic_repairs,
)
from curunir_operational.v4.models import sha256

pytestmark = pytest.mark.no_db

V4 = Path("artifacts/curunir_public_source_intelligence_and_kernel_admission_v4_20260722")


def _run(tmp_path):
    corpus = tmp_path / "corpus"; review = tmp_path / "review"; errors = tmp_path / "errors"
    build_frozen_corpus(V4, corpus); execute_blind_reviews(corpus / "frozen_packets.jsonl", review)
    compare_and_repair(corpus, review, errors)
    return corpus, review, errors


def test_all_six_surfaces_execute(tmp_path):
    corpus, review, errors = _run(tmp_path)
    metrics = surface_metrics(corpus, review, errors, tmp_path / "metrics")
    assert len(metrics) == 6
    assert all(item["post_repair_verdict"] == "PASS_HARDENED" for item in metrics.values())


def test_no_dependence_found_is_not_independence(tmp_path):
    _, _, errors = _run(tmp_path)
    values = json.loads((errors / "error_records.json").read_text())
    assert any(x["failure_class"] == "UNKNOWN_TREATED_AS_INDEPENDENT" for x in values)


def test_translation_direction_requires_positive_evidence(tmp_path):
    _, _, errors = _run(tmp_path)
    values = json.loads((errors / "error_records.json").read_text())
    assert any(x["failure_class"] == "WRONG_EDGE_TYPE" for x in values)


def test_entity_only_claim_is_invalidated(tmp_path):
    _, _, errors = _run(tmp_path)
    values = json.loads((errors / "error_records.json").read_text())
    assert any(x["failure_class"] == "WRONG_TYPE" for x in values)


def test_correction_propagates_without_mutating_history(tmp_path):
    corpus, review, errors = _run(tmp_path)
    result = propagate_repairs(corpus, errors, V4, tmp_path / "propagation")
    impacts = json.loads((tmp_path / "propagation/dependency_impacts.json").read_text())
    assert result["historical_artifacts_preserved"] and result["corrected_reports"] == 2
    assert {"EXTRACTION_CANDIDATE", "SOURCE_ORIGIN_EDGE", "REPORT_SENTENCE",
            "MISSION_HANDOFF", "KERNEL_PROPOSAL"} <= {x["downstream_kind"] for x in impacts}
    assert all(x["old_version_preserved"] for x in impacts)


def test_report_redline_uses_precise_retrieval_language(tmp_path):
    corpus, review, errors = _run(tmp_path)
    propagate_repairs(corpus, errors, V4, tmp_path / "propagation")
    reports = list((tmp_path / "propagation").glob("*_corrected_report_v2.md"))
    texts = "\n".join(path.read_text() for path in reports)
    assert "43 public leads prompted 43 retrieval attempts" in texts
    assert "31 public leads prompted 33 retrieval attempts" in texts
    assert "independently retrieved" not in texts


def test_heldout_runs_after_reference_freeze(tmp_path):
    corpus, review, errors = _run(tmp_path)
    result = build_reference_and_heldout(corpus, review, tmp_path / "reference")
    heldout = json.loads((tmp_path / "reference/heldout_results.json").read_text())
    assert result["heldout_packets"] > 0 and heldout["production_repair_frozen_before_run"]
    assert heldout["human_accuracy"] == "NOT_MEASURED" and not heldout["post_heldout_tuning"]


def test_annex_is_fully_mapped_and_honest_about_length(tmp_path):
    result = build_technical_annex(V4, tmp_path / "annex")
    assert result["mapped_sentences"] == result["sentences"]
    assert result["coverage"] == "100_PERCENT"
    if not result["target_met"]: assert result["limitation"]


def test_kernel_revalidation_blocks_every_proposal(tmp_path):
    result = revalidate_kernel_proposals(V4, tmp_path, tmp_path / "kernel")
    diff = json.loads((tmp_path / "kernel/kernel_shadow_diff.json").read_text())
    assert result["proposals_revalidated"] == 4 == result["blocked_operations"]
    assert all(item["blocked"] and not item["fixture_mutated"] for item in diff["operations"])
    assert result["canonical_writes"] == 0


def test_corrected_packets_add_real_context_without_replacing_v1(tmp_path):
    corpus, review, errors = _run(tmp_path)
    before = (corpus / "frozen_packets.jsonl").read_bytes()
    report = build_corrected_packets(V4, corpus, tmp_path / "packet_repairs")
    corrected = [json.loads(x) for x in (tmp_path / "packet_repairs/corrected_packets_v2.jsonl").read_text().splitlines()]
    span = [x for x in corrected if x["surface"].startswith("SURFACE_1")]
    span_with_context = [x for x in span if "context_mapping" in x["review_material"]]
    assert len(span) == 40
    assert report["span_context_packets"] == len(span_with_context) == 39
    assert any(x["review_material"]["surrounding_context"] != x["review_material"]["source_excerpt"]
               for x in span)
    assert len(corrected) == 177
    assert all(x["packet_version"] == 2 and "supersedes_packet_id" in x["review_material"] for x in corrected)
    leaked_strata = {"UPDATES", "SUPERSEDES", "INDEPENDENT", "SOURCE_DISAGREEMENT", "QUALIFICATION"}
    assert not leaked_strata.intersection(x["sampling_stratum"] for x in corrected)
    forbidden = {
        "SURFACE_2_SOURCE_ORIGIN_ACCURACY": {"asserted_direction", "direction_state"},
        "SURFACE_3_FALSE_CORROBORATION_ACCURACY": {"observed_origin_relationships", "source_family_id"},
        "SURFACE_4_CLAIM_SUPPORT_ACCURACY": {"normalized_claim", "geographic_scope", "modality", "polarity", "temporal_scope"},
        "SURFACE_5_CONTRADICTION_CORRECTION_AND_RETRACTION_ACCURACY": {"temporal_relationship", "scope_relationship"},
        "SURFACE_6_REPORT_FAITHFULNESS": {"sentence", "sentence_type"},
    }
    for packet in corrected:
        assert not forbidden.get(packet["surface"], set()).intersection(packet["review_material"])
        assert packet["review_material"].get("independent_task") or packet["surface"].startswith("SURFACE_1")
    assert (corpus / "frozen_packets.jsonl").read_bytes() == before


def test_system_outputs_are_revealed_only_after_three_complete_blind_files(tmp_path):
    artifact = tmp_path / "artifact"; corpus = artifact / "03_corpus"
    build_frozen_corpus(V4, corpus)
    repair = artifact / "10_errors_and_repairs/packet_repairs"
    build_corrected_packets(V4, corpus, repair)
    build_semantic_overlays(V4, artifact / "10_errors_and_repairs/semantic_overlays")
    packets = [json.loads(x) for x in (repair / "corrected_packets_v2.jsonl").read_text().splitlines()]
    input_hash = sha256((repair / "corrected_packets_v2.jsonl").read_bytes())
    reviews = tmp_path / "reviews"; reviews.mkdir()
    for suffix, reviewer in zip("abc", ("BLIND_MODEL_REVIEWER_A", "BLIND_MODEL_REVIEWER_B",
                                        "BLIND_MODEL_REVIEWER_C")):
        rows = [{"reviewer": reviewer, "packet_id": packet["packet_id"],
                 "packet_frozen_content_hash": packet["frozen_content_hash"],
                 "surface": packet["surface"], "independent_label": "ABSTAIN_TEST",
                 "confidence": 0.0, "decision": "CANNOT_ADJUDICATE", "rationale": "test",
                 "system_answer_seen": False, "other_reviews_seen": False,
                 "input_sha256": input_hash} for packet in packets]
        (reviews / f"blind_labels_reviewer_{suffix}.jsonl").write_text(
            "".join(json.dumps(row, sort_keys=True) + "\n" for row in rows))
    result = build_verification_packets(V4, artifact, reviews, tmp_path / "verification")
    assert result["verification_packet_count"] == 177
    assert all(x["records"] == 177 for x in result["reviewers_frozen"])
    verification_path = tmp_path / "verification/verification_packets_v3.jsonl"
    verification = [json.loads(x) for x in verification_path.read_text().splitlines()]
    verification_hash = sha256(verification_path.read_bytes())
    for suffix, reviewer in zip("abc", ("BLIND_MODEL_REVIEWER_A", "BLIND_MODEL_REVIEWER_B",
                                        "BLIND_MODEL_REVIEWER_C")):
        rows = [{"reviewer": reviewer, "verification_packet_id": packet["packet_id"],
                 "decision": "CORRECT", "severity": "INFORMATIONAL", "rationale": "test",
                 "other_reviews_seen": False, "human_review": False,
                 "verification_input_sha256": verification_hash} for packet in verification]
        (tmp_path / f"verification/comparison_reviewer_{suffix}.jsonl").write_text(
            "".join(json.dumps(row, sort_keys=True) + "\n" for row in rows))
    final_dir = tmp_path / "verification/post_comparison_repair/final_round"; final_dir.mkdir(parents=True)
    final_packet = {"packet_id": "final-test-packet", "blind_packet_id": packets[0]["packet_id"]}
    final_path = final_dir / "final_repair_packets_v5.jsonl"
    final_path.write_text(json.dumps(final_packet, sort_keys=True) + "\n")
    final_hash = sha256(final_path.read_bytes())
    for suffix, reviewer in zip("abc", ("BLIND_MODEL_REVIEWER_A", "BLIND_MODEL_REVIEWER_B",
                                        "BLIND_MODEL_REVIEWER_C")):
        row = {"reviewer": reviewer, "final_repair_packet_id": "final-test-packet",
               "decision": "CORRECT", "severity": "INFORMATIONAL", "other_reviews_seen": False,
               "human_review": False, "final_repair_input_sha256": final_hash}
        (final_dir / f"final_comparison_reviewer_{suffix}.jsonl").write_text(json.dumps(row) + "\n")
    gate_dir = artifact / "14_stage_1_gate"; gate_dir.mkdir(parents=True)
    (gate_dir / "stage_1_gate.json").write_text(json.dumps({"conditions": {
        "existing": True, "final_sanitized_model_panel_complete": False}}))
    (gate_dir / "stage_1_summary.json").write_text(json.dumps({"stage_1_gate": {}, "verdict": "BLOCKED"}))
    panel = finalize_sanitized_model_panel(artifact, tmp_path / "verification")
    assert panel["comparison_records"] == 531 and panel["verdict"] == "AI_SECONDARY_ADJUDICATION_COMPLETE"


def test_semantic_overlays_remove_independence_and_broad_supersession(tmp_path):
    report = build_semantic_overlays(V4, tmp_path / "overlays")
    bases = json.loads((tmp_path / "overlays/corrected_evidence_basis_register_v2.json").read_text())
    relations = json.loads((tmp_path / "overlays/corrected_relation_register_v2.json").read_text())
    assert report["legacy_independence_removed"]
    assert not any(x["independence_state"] == "INDEPENDENT" for x in bases)
    assert not any(x["relation_type"] == "SUPERSESSION" and
                   "PRECEDES_FINAL" in x["temporal_relationship"] for x in relations)


def test_claim_repair_preserves_negative_and_explicit_time(tmp_path):
    build_semantic_overlays(V4, tmp_path / "overlays")
    claims = json.loads((tmp_path / "overlays/corrected_claim_register_v2.json").read_text())
    blackout = next(x for x in claims if "12:33 CEST" in x["normalized_statement"])
    cyber = next(x for x in claims if "no sign of a cyber attack" in x["normalized_statement"])
    assert blackout["temporal_scope"] == ["2025-04-28T12:33:00+02:00", "2025-04-28T12:33:00+02:00"]
    assert cyber["polarity"] == "NEGATIVE_OR_QUALIFIED"
    assert infer_temporal_scope("expected in Q1 2026") == (["2026-01-01", "2026-03-31"], "QUARTER")


def test_short_organizations_and_multidate_claims_survive_repair(tmp_path):
    build_semantic_overlays(V4, tmp_path / "overlays")
    candidates = json.loads((tmp_path / "overlays/corrected_candidate_register_v2.json").read_text())
    claims = json.loads((tmp_path / "overlays/corrected_claim_register_v2.json").read_text())
    short_orgs = [x for x in candidates if x["original_text"] in {"EU", "UE", "UN"} and
                  x["candidate_type"] == "ORGANIZATION_MENTION_CANDIDATE"]
    assert short_orgs and not any(x["v5_disposition"].startswith("INVALIDATED") for x in short_orgs)
    uranos = next(x for x in claims if "Uranos AI procurement" in x["normalized_statement"])
    assert uranos["temporal_scope"][0].startswith("2025-12")
    assert uranos["temporal_scope"][1].startswith("2027-08")
    assert len(uranos["temporal_anchors"]) >= 2
    ai_tokens = [x for x in candidates if x["original_text"] in {"AI", "IA"} and
                 x["candidate_type"] == "ORGANIZATION_MENTION_CANDIDATE"]
    assert ai_tokens and all(x["v5_disposition"].startswith("INVALIDATED") for x in ai_tokens)
    layout_signals = [x for x in candidates if x["candidate_type"] in
        {"CITATION_CANDIDATE", "CORRECTION_CANDIDATE"} and "        " in x["original_text"]]
    assert layout_signals and all(x["v5_disposition"].startswith("QUARANTINED") or
                                  x["v5_disposition"].startswith("INVALIDATED") for x in layout_signals)
    numeric = next(x for x in candidates if x["supersedes_candidate_id"] == "candidate-3296bbccc4f7c3becec155b0")
    assert numeric["original_text"] == "€5M" and numeric["v5_disposition"] == "REEXTRACTED_NUMERIC_UNIT_SUFFIX"
    column_prefix = next(x for x in candidates if x["supersedes_candidate_id"] == "candidate-841b0fd02ee46bd54f60b68a")
    assert column_prefix["v5_disposition"] == "INVALIDATED_PDF_COLUMN_PREFIX_FRAGMENT"
    sentence_citations = {"candidate-468616ad8363dd71c667043a", "candidate-40d308070fa8163a3ce4e0c9",
                          "candidate-95f9379ab147b5b47fed927d", "candidate-a3610f87a7e3f22b4fc17b75"}
    repaired = [x for x in candidates if x["supersedes_candidate_id"] in sentence_citations]
    assert len(repaired) == 4 and all(x["candidate_type"] == "CLAIM_CANDIDATE" for x in repaired)


def test_corrected_graph_uses_v2_references_and_active_update_state(tmp_path):
    build_semantic_overlays(V4, tmp_path / "overlays")
    relations = json.loads((tmp_path / "overlays/corrected_relation_register_v2.json").read_text())
    bases = json.loads((tmp_path / "overlays/corrected_evidence_basis_register_v2.json").read_text())
    assert all(x["left_claim_id"].startswith("v5-corrected-claim-") and
               x["right_claim_id"].startswith("v5-corrected-claim-") for x in relations)
    factual = next(x for x in bases if x["source_family_id"] == "ENTSOE_FACTUAL_REPORT_FAMILY")
    assert factual["active"] and factual["correction_state"] == "TEMPORALLY_UPDATED_HISTORICAL_SUPPORT_ACTIVE"
    origin = json.loads((tmp_path / "overlays/corrected_source_origin_graph_v2.json").read_text())
    asserted_later = next(x for x in origin if x["relationship"] == "UNKNOWN_DEPENDENCE" and
                          "chronology unverified" in " ".join(x["metadata_basis"]))
    assert asserted_later["relationship"] == "UNKNOWN_DEPENDENCE"
    assert all(x.startswith("v5-corrected-candidate-") for x in asserted_later["evidence_candidate_ids"])
    hypotheses = json.loads((tmp_path / "overlays/corrected_hypothesis_register_v2.json").read_text())
    assert hypotheses and all(x["independent_evidence_count"] == 0 for x in hypotheses)
    translation_edges = [x for x in origin if x["relationship"] == "TRANSLATED_FROM"]
    assert len(translation_edges) == 4
    assert all(any("translation manifest" in basis for basis in x["metadata_basis"])
               for x in translation_edges)
    paired = {x["source_family_id"]: x["independence_state"] for x in bases}
    assert paired["ASSEMBLY_SITTING_20260505"] == "DEPENDENT"
    assert paired["RED_ELECTRICA_JUNE_REPORT"] == "DEPENDENT"


def test_truncated_claim_span_and_process_proofs_are_repaired(tmp_path):
    build_semantic_overlays(V4, tmp_path / "overlays")
    candidates = json.loads((tmp_path / "overlays/corrected_candidate_register_v2.json").read_text())
    claims = json.loads((tmp_path / "overlays/corrected_claim_register_v2.json").read_text())
    candidate = next(x for x in candidates if x["supersedes_candidate_id"] == "candidate-1ba352abd177cda4b08d3615")
    claim = next(x for x in claims if x["supersedes_claim_id"] == "claim-72731d8a8b0c26938c0a20ec")
    assert candidate["v5_disposition"] == "REEXTRACTED_SENTENCE_BOUNDARY"
    assert candidate["original_text"].startswith("The scope of this investigation")
    assert candidate["original_text"].endswith("non-compliance with legal requirements.")
    assert claim["original_wording"] == candidate["original_text"]
    sentences = [json.loads(x) for x in (tmp_path / "overlays/corrected_sentence_evidence_ledger_v2.jsonl").read_text().splitlines()]
    process = [x for x in sentences if not x["claim_ids"] and not x["evidence_basis_ids"]]
    assert process and all(len(x["operational_proof_records"]) == 3 for x in process)
    assert all(x["operational_proof_records"][0]["payload"]["sentence_id"] == x["supersedes_sentence_id"]
               for x in process)
    rendered = " ".join(x["sentence"] for x in sentences)
    assert "independently retrieved" not in rendered
    assert "linked as an update" not in rendered
    assert "unresolved temporal relationship" in rendered


def test_unverified_update_is_removed_from_packet_and_report(tmp_path):
    corpus, _, errors = _run(tmp_path)
    build_corrected_packets(V4, corpus, errors / "packet_repairs")
    packets = [json.loads(x) for x in (errors / "packet_repairs/corrected_packets_v2.jsonl").read_text().splitlines()]
    amiads = [x for x in packets if x["surface"].startswith("SURFACE_2") and
              "chronology unverified" in " ".join(x["review_material"].get("metadata_basis", []))]
    assert amiads and all("asserted_direction" not in x["review_material"] for x in amiads)
    propagate_repairs(corpus, errors, V4, tmp_path / "propagation")
    report = (tmp_path / "propagation/campaign_a_corrected_report_v2.md").read_text()
    assert "unresolved temporal relationship" in report and "linked as an update" not in report
    assert "this is not evidence that none exists" in report


def test_all_surfaces_rerun_after_external_repairs(tmp_path):
    corpus, review, errors = _run(tmp_path)
    build_corrected_packets(V4, corpus, errors / "packet_repairs")
    build_semantic_overlays(V4, errors / "semantic_overlays")
    propagation = tmp_path / "propagation"
    propagate_repairs(corpus, errors, V4, propagation)
    result = validate_epistemic_repairs(corpus, errors, propagation, tmp_path / "post_repair")
    assert result["surfaces"] == 6 and result["all_pass_hardened"]
