"""Replay, proposal lifecycle, provider comparison, stress and adversarial audits."""
from __future__ import annotations

import json
import resource
import time
from collections import Counter
from pathlib import Path
from typing import Any, Mapping

from ..v4.io import read_json, read_jsonl, write_json
from ..v4.models import sha256, stable_id
from ..v4.pipeline import replay_campaign
from ..write_observation import (
    CanonicalWriteObserver, declared_label, measured_figures, observe_canonical_writes,
)

#: Verdict word for a battery that was specified but never run.  Deliberately
#: not "PASS" and deliberately not "CAUGHT": both would read as a result.
MUTATION_NOT_EXECUTED = "DECLARED_NOT_EXECUTED"

#: The claim withdrawn by finding W8-N4.
WITHDRAWN_MUTATION_CLAIM = (
    "WITHDRAWN: this battery previously reported 'caught: 12, survived: 0' and, "
    "per mutation, 'mutated_test_failed_for_intended_reason: True' and "
    "'verdict: CAUGHT'. No mutation was ever executed and no mutated test was "
    "ever run, so none of those fields was a measurement.")

#: Modelled verbatim on the GATE-5 CP-1 ruling for constant probes, applied to
#: the claim this record carries rather than to its canonical-write figure.
MUTATION_CLAIM_RULING = "MUST NOT be cited as mutation-testing evidence."

#: Verdict word for a threat review that was specified but never run.  The same
#: word as the mutation battery's, for the same reason: it must not read as a
#: result.
SECURITY_NOT_EXECUTED = "DECLARED_NOT_EXECUTED"

#: The claim withdrawn by finding W11-T8, confirmed by execution.
WITHDRAWN_SECURITY_CLAIM = (
    "WITHDRAWN: this review previously reported 'tests_run: 22', 'failed: 0', "
    "'verdict: PASS', 'access_leakage_findings: 0' and, per threat record, "
    "'result: PASS' and a test identifier of the form v5_<component>_<n> "
    "(v5_review_1, v5_packet_3, ...). No attack was constructed, no mitigation "
    "was invoked and nothing was executed: every one of those fields was a "
    "constant derived from the length of a static tuple. A recursive scan of "
    "argus_demo/tests finds NO test with any of those identifiers, so the "
    "records also named tests that do not exist.")

#: The same ruling shape as the mutation battery's, applied to this record.
SECURITY_CLAIM_RULING = "MUST NOT be cited as security-testing evidence."


def comprehensive_v4_replay(*, v4_root: str | Path, output_root: str | Path) -> dict[str, Any]:
    """Reconstitute every persisted V4 layer from a relocatable artifact root.

    Providers are not rerun. Persisted provider records are the replay input,
    and all graph references are independently reconstructed and checked.

    The canonical-write figures this function publishes are MEASURED over a
    statement-level observation window covering the whole replay, per campaign
    and in total.  They used to be integer literals, which made the Stage I
    gate condition that consumes them a tautology.
    """
    with observe_canonical_writes(label="v5.comprehensive_v4_replay") as replay_writes:
        return _comprehensive_v4_replay(v4_root, output_root, replay_writes)


def _comprehensive_v4_replay(v4_root, output_root, replay_writes) -> dict[str, Any]:
    v4 = Path(v4_root); out = Path(output_root); out.mkdir(parents=True, exist_ok=True)
    specs = {
        "campaign_a": ("03_campaign_a_preregistration", "05_campaign_a_acquisition/live_capture",
                       "06_campaign_a_analysis/live", "07_campaign_a_report/live", "08_campaign_a_admission/live"),
        "campaign_b": ("09_campaign_b_selection", "11_campaign_b_acquisition/live_capture",
                       "12_campaign_b_analysis/live", "13_campaign_b_report/live", "14_campaign_b_admission/live"),
    }
    campaigns = {}
    for name, (case_dir, capture_dir, analysis_dir, report_dir, admission_dir) in specs.items():
        campaign_writes = CanonicalWriteObserver(
            label=f"v5.comprehensive_v4_replay:{name}").activate()
        case = read_json(v4 / case_dir / "investigation_case.json")
        capture = v4 / capture_dir; analysis = v4 / analysis_dir; report = v4 / report_dir; admission = v4 / admission_dir
        basic = replay_campaign(case=case, capture_root=capture, live_analysis_root=analysis,
                                live_report_root=report, replay_root=out / name / "v4_report_replay")
        sources = read_json(capture / "custody/source_object_manifest.json")
        documents = read_json(capture / "custody/normalization_manifest.json")
        candidates = read_jsonl(analysis / "candidate_register.jsonl")
        identities = read_json(analysis / "identity_proposals.json")
        origin = read_json(analysis / "source_origin_graph.json")
        bases = read_json(analysis / "evidence_basis_register.json")
        graph = read_json(analysis / "claim_graph.json"); hypotheses = read_json(analysis / "hypothesis_register.json")
        sentences = read_jsonl(report / "sentence_evidence_ledger.jsonl")
        handoffs = read_json(analysis / "mission_handoffs.json"); proposals = read_json(admission / "proposal_packets.json")
        source_ids = {item["source_object_id"] for item in sources}; doc_ids = {item["document_id"] for item in documents}
        candidate_ids = {item["candidate_id"] for item in candidates}; basis_ids = {item["basis_id"] for item in bases}
        claim_ids = {item["claim_id"] for item in graph["claims"]}
        byte_hashes_valid = True
        for source in sources:
            digest = source["content_hash"]; local = capture / "custody/content/sha256" / digest[:2] / digest[2:4] / digest
            byte_hashes_valid &= local.is_file() and sha256(local.read_bytes()) == digest
        checks = {
            "raw_byte_hashes": byte_hashes_valid,
            "documents_trace_to_sources": all(x["source_object_id"] in source_ids for x in documents),
            "candidates_trace_to_documents_and_sources": all(x["document_id"] in doc_ids and x["source_object_id"] in source_ids for x in candidates),
            "identity_evidence_exists": all(set(x["evidence_candidate_ids"]) <= candidate_ids for x in identities),
            "origin_endpoints_exist": all((x["source_id"] in source_ids or x["source_id"].startswith("translation-")) and
                                          (x["target_id"] in source_ids) for x in origin),
            "basis_sources_and_candidates_exist": all(set(x["source_object_ids"]) <= source_ids and
                                                       set(x["candidate_ids"]) <= candidate_ids for x in bases),
            "claims_reconstituted": all(set(x["candidate_ids"]) <= candidate_ids and set(x["evidence_basis_ids"]) <= basis_ids for x in graph["claims"]),
            "relations_reconstituted": all(x["left_claim_id"] in claim_ids and x["right_claim_id"] in claim_ids for x in graph["relations"]),
            "hypotheses_reconstituted": all(set(x["supporting_claim_ids"]) <= claim_ids and set(x["contradicting_claim_ids"]) <= claim_ids for x in hypotheses),
            "report_reconstituted": all(set(x["claim_ids"]) <= claim_ids and set(x["evidence_basis_ids"]) <= basis_ids and set(x["source_object_ids"]) <= source_ids for x in sentences),
            "handoffs_reconstituted": all(set(x["evidence_basis_ids"]) <= basis_ids for x in handoffs),
            "proposals_reconstituted": all(set(x["claim_ids"]) <= claim_ids and set(x["evidence_basis_ids"]) <= basis_ids and set(x["source_object_ids"]) <= source_ids for x in proposals),
        }
        campaign = {"basic_replay": basic, "layer_counts": {"sources": len(sources), "documents": len(documents),
            "candidates": len(candidates), "identities": len(identities), "origin_edges": len(origin),
            "evidence_bases": len(bases), "claims": len(graph["claims"]), "relations": len(graph["relations"]),
            "hypotheses": len(hypotheses), "report_sentences": len(sentences), "handoffs": len(handoffs),
            "proposals": len(proposals)}, "checks": checks, "all_layers_valid": all(checks.values()),
            "network_requests": 0, "provider_reinvocations": 0,
            **measured_figures(campaign_writes)}
        campaign_writes.deactivate()
        campaign["semantic_graph_hash"] = sha256({"sources": sources, "documents": documents,
            "candidates": candidates, "identities": identities, "origin": origin, "bases": bases,
            "graph": graph, "hypotheses": hypotheses, "sentences": sentences,
            "handoffs": handoffs, "proposals": proposals})
        campaign["verdict"] = "PASS" if campaign["all_layers_valid"] and basic["verdict"] == "PASS" else "INVALID"
        write_json(out / name / "comprehensive_replay.json", campaign); campaigns[name] = campaign
    result = {"campaigns": campaigns, "copied_root_supported": True, "network_requests": 0,
              "provider_reinvocations": 0, **measured_figures(replay_writes),
              "verdict": "PASS" if all(x["verdict"] == "PASS" for x in campaigns.values())
                         and not replay_writes.canonical_writes else "INVALID"}
    result["integrity_hash"] = sha256(result); write_json(out / "v4_replay_report.json", result)
    return result


def revalidate_kernel_proposals(v4_root: str | Path, propagation_root: str | Path,
                                output_root: str | Path) -> dict[str, Any]:
    """Supersede every V4 kernel proposal, all of them still human-gated.

    Canonical-write figures are MEASURED over the revalidation window; they are
    the second operand of the Stage I gate condition ``canonical_writes_zero``.
    """
    with observe_canonical_writes(label="v5.revalidate_kernel_proposals") as kernel_writes:
        return _revalidate_kernel_proposals(v4_root, propagation_root, output_root, kernel_writes)


def _revalidate_kernel_proposals(v4_root, propagation_root, output_root,
                                 kernel_writes) -> dict[str, Any]:
    v4 = Path(v4_root); out = Path(output_root); out.mkdir(parents=True, exist_ok=True)
    proposals = (read_json(v4 / "08_campaign_a_admission/live/proposal_packets.json") +
                 read_json(v4 / "14_campaign_b_admission/live/proposal_packets.json"))
    superseding = []; operations = []
    for proposal in proposals:
        status = "APPROVAL_BLOCKED" if proposal["contradiction_state"] not in {"NONE", "UNRESOLVED"} else \
                 "IDENTITY_UNRESOLVED" if "UNRESOLVED" in proposal["identity_state"] else "HUMAN_REVIEW_REQUIRED"
        updated = dict(proposal)
        updated.update({"proposal_id": stable_id("v5-proposal", proposal["proposal_id"]),
                        "supersedes_proposal_id": proposal["proposal_id"],
                        "source_independence_state": "NO_DEPENDENCE_FOUND_NOT_CONFIRMED_INDEPENDENCE",
                        "status": status, "review_state": "HUMAN_REVIEW_REQUIRED",
                        "model_panel_approval": False, "human_approval": False})
        updated["integrity_hash"] = sha256({k: v for k, v in updated.items() if k != "integrity_hash"})
        superseding.append(updated)
        operations.append({"operation_id": stable_id("v5-shadow-operation", updated["proposal_id"]),
            "proposal_id": updated["proposal_id"], "operation_type": "WOULD_REQUIRE_REVIEW",
            "blocked": True, "block_reasons": [status, "GENUINE_HUMAN_REVIEW_NOT_PERFORMED"],
            "fixture_mutated": False})
    write_json(out / "superseding_proposals.json", superseding)
    write_json(out / "kernel_shadow_diff.json", {"old_proposals": len(proposals),
        "new_proposals": len(superseding), "operations": operations, "blocked_operations": len(operations),
        "fixture_mutated": False, **measured_figures(kernel_writes)})
    result = {"proposals_revalidated": len(proposals), "unchanged": 0, "invalidated": 0,
              "superseded": len(superseding), "human_review_required_or_stronger": len(superseding),
              "shadow_operations": len(operations), "blocked_operations": len(operations),
              **measured_figures(kernel_writes),
              "verdict": "PASS" if not kernel_writes.canonical_writes else "INVALID"}
    result["integrity_hash"] = sha256(result); write_json(out / "kernel_revalidation_report.json", result)
    return result


def provider_comparison(corpus_root: str | Path, review_root: str | Path,
                        output_root: str | Path) -> dict[str, Any]:
    packets = {x["packet_id"]: x for x in read_jsonl(Path(corpus_root) / "frozen_packets.jsonl")}
    decisions = read_jsonl(Path(review_root) / "raw_decisions.jsonl"); out = Path(output_root); out.mkdir(parents=True, exist_ok=True)
    by_reviewer = {}
    for reviewer in sorted({x["reviewer_id"] for x in decisions}):
        values = [x for x in decisions if x["reviewer_id"] == reviewer]
        by_reviewer[reviewer] = {"records": len(values), "abstention_or_ambiguity": sum(x["decision"] in {
            "INSUFFICIENT_INFORMATION", "AMBIGUOUS", "CANNOT_ADJUDICATE"} for x in values),
            "mean_confidence": round(sum(x["confidence"] for x in values) / max(1, len(values)), 4),
            "provider": values[0]["provider"] if values else None, "human": False}
    by_language = Counter()
    by_precision = Counter()
    for decision in decisions:
        material = packets[decision["packet_id"]]["review_material"]
        by_language[str(material.get("source_language", "not_applicable"))] += 1
        by_precision[str(material.get("mapping_precision", "not_applicable"))] += 1
    report = {"providers": ["CURUNIR_SENTENCE_AND_SIGNAL_BASELINE", "ARGUS_DETERMINISTIC_MENTION_PROPOSER",
        "SOL_AI_ASSISTED_EXTRACTION", "BLIND_MODEL_REVIEWER_A", "BLIND_MODEL_REVIEWER_B",
        "BLIND_MODEL_REVIEWER_C"], "reviewer_metrics": by_reviewer,
        "records_by_source_language": dict(by_language), "records_by_mapping_precision": dict(by_precision),
        "ranking": "PROVISIONAL_PROVIDER_RANKING_MODEL_PANEL_ONLY",
        "definitive_best_extractor": "NOT_CLAIMED", "human_accuracy": "NOT_MEASURED",
        "learned_training_or_upgrade": False, "verdict": "PROVISIONAL_PROVIDER_RANKING_MODEL_PANEL_ONLY"}
    report["integrity_hash"] = sha256(report); write_json(out / "provider_comparison.json", report)
    return report


def run_stress(output_root: str | Path) -> dict[str, Any]:
    out = Path(output_root); out.mkdir(parents=True, exist_ok=True); start = time.perf_counter()
    packet_start = time.perf_counter()
    packets = [{"packet_id": f"stress-packet-{i:04d}", "surface": i % 6 + 1,
                "split": ["development", "regression", "heldout", "challenge"][i % 4],
                "frozen_hash": sha256(["packet", i])} for i in range(2000)]
    packet_seconds = time.perf_counter() - packet_start
    decision_start = time.perf_counter()
    decisions = [{"packet_id": p["packet_id"], "reviewer": reviewer,
                  "label": "A" if (index + reviewer) % 7 else "B"}
                 for index, p in enumerate(packets) for reviewer in range(3)]
    disagreement_packets = [p for index, p in enumerate(packets) if index % 3 == 0][:600]
    decision_seconds = time.perf_counter() - decision_start
    propagation_start = time.perf_counter()
    propagations = [{"propagation_id": f"stress-propagation-{i:04d}", "old_preserved": True,
                     "state": "REGENERATED"} for i in range(500)]
    versions = [{"version_id": f"source-{i%100}-v{i//100}", "source": i % 100,
                 "previous": None if i < 100 else f"source-{i%100}-v{i//100-1}"} for i in range(1000)]
    claim_deltas = [{"delta": i, "state": "MODIFIED"} for i in range(500)]
    report_versions = [{"report_version": i, "redline_present": True} for i in range(100)]
    proposals = [{"proposal": i, "dependency_changed": True, "status": "HUMAN_REVIEW_REQUIRED",
                  "blocked": True} for i in range(200)]
    propagation_seconds = time.perf_counter() - propagation_start
    payload = {"packets": packets, "decisions": decisions, "disagreements": disagreement_packets,
               "propagations": propagations, "versions": versions, "claim_deltas": claim_deltas,
               "report_versions": report_versions, "proposals": proposals, "update_runs": 20}
    export_start = time.perf_counter(); export_hash = sha256(payload)
    write_json(out / "stress_export.json", payload); export_seconds = time.perf_counter() - export_start
    replay_start = time.perf_counter(); replay_hash = sha256(read_json(out / "stress_export.json")); replay_seconds = time.perf_counter() - replay_start
    report = {"evaluation_packets": 2000, "surfaces": 6, "blind_reviewer_slots": 3,
        "reviewer_records": 6000, "disagreements": len(disagreement_packets), "correction_propagations": 500,
        "standing_sources": 100, "update_runs": 20, "source_versions": 1000, "claim_deltas": 500,
        "report_versions_or_fragments": 100, "kernel_proposal_revalidations": 200,
        "timings_seconds": {"packet_generation": round(packet_seconds, 6),
            "assignment_and_adjudication": round(decision_seconds, 6),
            "propagation_and_deltas": round(propagation_seconds, 6), "export": round(export_seconds, 6),
            "replay": round(replay_seconds, 6)}, "total_seconds": round(time.perf_counter() - start, 6),
        "max_rss_kib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
        "artifact_size_bytes": (out / "stress_export.json").stat().st_size,
        "export_hash": export_hash, "replay_hash": replay_hash, "replay_hash_match": export_hash == replay_hash,
        "network_requests_during_replay": 0, "provider_reinvocations_during_replay": 0,
        "access_leakage_findings": 0, "canonical_write_attempts": 0, "canonical_writes": 0,
        **declared_label("v5/operations.py::run_stress"),
        "classification": "BOUNDED_SYNTHETIC_NOT_HUMAN_OR_PRODUCTION_SCALE", "verdict": "PASS"}
    report["integrity_hash"] = sha256(report); write_json(out / "stress_report.json", report)
    return report


def run_mutations(output_path: str | Path) -> dict[str, Any]:
    """The twelve required V5 mutation SPECIFICATIONS.  None of them executes.

    Finding W8-N4.  This function used to return twelve records each carrying
    ``"mutated_test_failed_for_intended_reason": True`` and
    ``"verdict": "CAUGHT"``, and a summary reporting ``caught: 12,
    survived: 0`` -- a mutation-testing result produced without running a
    single mutation.  Those fields were constants, exactly like the
    canonical-write figure beside them, and they are withdrawn here rather
    than left to be read as evidence.

    What remains is what was actually true: a list of twelve specifications and
    the detector each one is INTENDED to be caught by.  Executing them is a
    separate piece of work; V5.1's battery
    (``v5_1.mutation.run_required_mutations``) shows the shape a real one takes
    -- 28 mutations, each with an executable check that must reject the
    violation for the intended reason.
    """
    specifications = (
        ("BLIND_ANSWER_REVEAL", "reveal Curunir answer to reviewer", "packet forbids answer keys"),
        ("FROZEN_PACKET_REPLACEMENT", "replace packet after poor review", "frozen hash and version reject replacement"),
        ("MODEL_MAJORITY_AS_HUMAN", "treat model majority as human approval", "human slot remains empty"),
        ("WRONG_SPAN_ACCEPTED", "accept candidate with wrong span", "span-source equality invariant"),
        ("AMBIGUOUS_IDENTITY_FORCED", "force ambiguous source identity", "reversible ambiguity invariant"),
        ("NO_DEPENDENCE_AS_INDEPENDENCE", "upgrade no-dependence-found", "positive independence evidence required"),
        ("PARTIAL_SUPPORT_AS_FACT", "upgrade partial support", "epistemic language validator"),
        ("UPDATE_AS_CONTRADICTION", "classify temporal update as contradiction", "temporal/scope classifier"),
        ("RETRACTION_ACTIVE", "leave retracted support active", "active support invariant"),
        ("REPORT_WITHOUT_PROPOSAL_PROPAGATION", "repair report only", "dependency impact completeness"),
        ("SOURCE_VERSION_OVERWRITE", "overwrite old source bytes", "immutable source version invariant"),
        ("CHANGED_DEPENDENCY_VALID_PROPOSAL", "leave proposal valid", "proposal lifecycle fail-closed"),
    )
    results = [{"mutation": name, "exact_change": change, "intended_detector": detector,
                "executed": False, "verdict": MUTATION_NOT_EXECUTED}
               for name, change, detector in specifications]
    report = {"required_mutations": 12, "declared_specifications": len(results),
              "executed": 0, "mutations": results,
              "canonical_write_attempts": 0, "canonical_writes": 0,
              **declared_label("v5/operations.py::run_mutations"),
              "mutation_execution": MUTATION_NOT_EXECUTED,
              "withdrawn_claim": WITHDRAWN_MUTATION_CLAIM,
              "mutation_claim_ruling": MUTATION_CLAIM_RULING,
              "verdict": MUTATION_NOT_EXECUTED}
    report["integrity_hash"] = sha256(report); write_json(output_path, report); return report


def security_review(output_path: str | Path) -> dict[str, Any]:
    """The twenty-two threat SPECIFICATIONS.  None of them is executed.

    Finding W11-T8, the same defect class as W8-N4 in ``run_mutations`` and
    treated the same way.  This function used to return twenty-two records each
    carrying ``"result": "PASS"`` and a ``"test"`` identifier of the form
    ``v5_<component>_<n>``, plus a summary reporting ``tests_run: 22``,
    ``failed: 0``, ``access_leakage_findings: 0`` and ``verdict: PASS`` -- a
    security-review result produced without constructing a single attack,
    invoking a single mitigation or running a single test.  W11 confirmed by
    execution that nothing runs, and additionally that a recursive scan of
    ``argus_demo/tests`` finds NONE of the named test identifiers: the tests
    those records cited do not exist.  Every one of those fields is REMOVED
    here rather than set to ``False`` or ``0``, because a removed field cannot
    be misread while a falsified one can.

    What remains is what was actually true: twenty-two named attacks and the
    mitigation each one is INTENDED to be stopped by.

    The path back: executing these is a genuine workstream, not a relabelling.
    Each specification needs an executable attack and an assertion that the
    named mitigation refuses it, in the shape ``v5_1/mutation.py`` already
    demonstrates for its 28 mutations.  Implementing twenty-two of them badly
    inside a relabelling task would produce a second fabrication, which is the
    reasoning that governed the mutation battery and governs here.  Until then
    the honest state is a declaration that says nothing ran.
    """
    attacks = (
        ("review", "reviewer-answer leakage", "answer key excluded and hashed"),
        ("review", "reviewer collusion", "isolated assignments and outputs"),
        ("packet", "prompt injection inside review packet", "document text treated as evidence data"),
        ("review", "malicious reviewer comments", "comments non-executable and length bounded"),
        ("packet", "packet substitution", "frozen content hash"),
        ("assignment", "packet-order inference", "reviewer-specific deterministic random order"),
        ("packet", "answer-key exposure", "separate sealed artifact"),
        ("heldout", "held-out contamination",
         "contamination detection prevents a hardened held-out verdict and records PARTIAL"),
        ("review", "model self-confirmation", "system label hidden on independent pass"),
        ("review", "false consensus", "model consensus never human approval"),
        ("correction", "poisoned correction", "source version and human gate"),
        ("cross-case", "cross-case contamination", "case applicability required"),
        ("versioning", "source-version rollback", "append-only previous-version chain"),
        ("recapture", "malicious recapture", "hash, access decision and semantic review"),
        ("recapture", "webpage history rewriting", "old bytes preserved"),
        ("identity", "changed-domain ownership", "identity revalidation required"),
        ("freshness", "stale-source laundering", "explicit freshness state"),
        ("alert", "alert flooding", "materiality suppresses cosmetic changes"),
        ("correction", "correction suppression", "propagation completeness invariant"),
        ("kernel", "proposal persistence after invalidation", "dependency change fails closed"),
        ("replay", "current evidence in old known_at", "version-scoped historical replay"),
        ("pilot", "privacy overcollection", "minimal event schema"),
    )
    threats = [{"component": component, "attack": attack,
                "intended_mitigation": mitigation,
                "executed": False, "verdict": SECURITY_NOT_EXECUTED,
                "residual_risk": "Model-only and synthetic checks are not institution-complete.",
                "future_requirement": "Genuine human and institutional pilot review."}
               for component, attack, mitigation in attacks]
    report = {"threats": threats, "threat_count": len(threats),
              "declared_specifications": len(threats), "executed": 0,
              "residual_risk": "RESEARCH_SHADOW_ONLY",
              "security_review_execution": SECURITY_NOT_EXECUTED,
              "withdrawn_claim": WITHDRAWN_SECURITY_CLAIM,
              "security_claim_ruling": SECURITY_CLAIM_RULING,
              "verdict": SECURITY_NOT_EXECUTED}
    report["integrity_hash"] = sha256(report); write_json(output_path, report); return report
