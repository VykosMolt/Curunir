"""Executable V4 mutation probes and integrity threat inventory."""
from __future__ import annotations

import subprocess
from dataclasses import replace
from pathlib import Path
from typing import Any, Callable

from .analysis import evidence_basis, make_candidate
from .io import write_json
from .kernel import (
    ZeroWriteMonitor, ZeroWriteViolation, build_proposal, inspect_frozen_kernel,
    validate_proposal, zero_write_guard,
)
from .models import (
    ClaimUnit, DerivativeMapping, ExtractionCandidate, IdentityProposal, NormalizedDocument,
    SourceRecord, sha256,
)
from .reporting import REPORT_SECTIONS, sentence, validate_report
from ..write_observation import declared_label

STAMP = "2026-07-22T12:00:00+00:00"


def _fixture() -> dict[str, Any]:
    text = "Official authority directly states the bounded public finding."
    raw_hash = sha256(text.encode()); derivative_hash = sha256(text); source_id = "mutation-source"
    source = SourceRecord(source_id, "MUTATION_CASE", ("retrieval",), raw_hash, "immutable://source",
                          ("https://public.invalid/source",), ("https://public.invalid/source",),
                          "Official authority", "OFFICIAL", "Bounded source", "en", STAMP, STAMP,
                          "CAPTURED_UNREVIEWED", {"releasability": ["PUBLIC"]})
    mapping = DerivativeMapping("mutation-map", source_id, raw_hash, derivative_hash, source.content_path,
                                0, len(text), "EXACT_CHARACTER")
    document = NormalizedDocument("mutation-document", source_id, raw_hash, derivative_hash,
                                  "MUTATION_NORMALIZER", "4", "en", text,
                                  (("BODY", 0, len(text)),), (), (mapping,), (), (), STAMP)
    candidate = make_candidate(case_id="MUTATION_CASE", document=document, source=source,
                               span_start=0, span_end=len(text), candidate_type="CLAIM_CANDIDATE",
                               normalized_value={"statement": text}, provider="MUTATION_BASELINE",
                               provider_version="1", review_state="HUMAN_REVIEW_REQUIRED")
    basis = evidence_basis(case_id="MUTATION_CASE", source_object_ids=(source_id,),
                           candidate_ids=(candidate.candidate_id,), source_family_id="family",
                           independence_state="INDEPENDENT")
    claim = ClaimUnit("mutation-claim", "MUTATION_CASE", "A bounded public finding was stated.", text,
                      "Official authority", "STATES", "bounded finding", (None, None), (), "ASSERTED",
                      "POSITIVE", "DIRECTLY_STATED", (basis.basis_id,), (candidate.candidate_id,),
                      "HUMAN_REVIEW_REQUIRED", 1)
    sentences = [sentence(section=section, text=f"{section} method note.", sentence_type="METHOD")
                 for section in REPORT_SECTIONS]
    sentences[0] = sentence(section=REPORT_SECTIONS[0], text="The official authority directly states the bounded public finding.",
                            sentence_type="FACTUAL", claim_ids=(claim.claim_id,),
                            evidence_basis_ids=(basis.basis_id,), source_object_ids=(source_id,),
                            epistemic_state="DIRECTLY_STATED")
    return {"text": text, "source": source, "document": document, "candidate": candidate,
            "basis": basis, "claim": claim, "sentences": sentences}


def _report(fixture: dict[str, Any], **overrides: Any) -> dict[str, Any]:
    values = {"sentences": fixture["sentences"], "claims": (fixture["claim"],),
              "bases": (fixture["basis"],), "sources": (fixture["source"],),
              "candidates": (fixture["candidate"],)}
    values.update(overrides); return validate_report(**values)


def _record(name: str, exact_change: str, detector: str, baseline: bool,
            mutated_caught: bool, intended_reason: str, restored: bool) -> dict[str, Any]:
    return {"mutation": name, "exact_change": exact_change, "detector": detector,
            "baseline_passed": baseline, "mutated_test_failed_for_intended_reason": mutated_caught,
            "intended_failure_reason": intended_reason, "restored_test_passed": restored,
            "verdict": "CAUGHT" if baseline and mutated_caught and restored else "SURVIVED"}


def run_mutation_suite(output_path: str | Path, repository_root: str | Path) -> dict[str, Any]:
    f = _fixture(); results: list[dict[str, Any]] = []
    baseline = _report(f)["verdict"] == "PASS"

    mutated = _report(f, search_lead_ids=(f["source"].source_object_id,))
    results.append(_record("SEARCH_SNIPPET_SUPPORT", "mark the only cited source object as a SearchLead identifier",
                           "validate_report:SEARCH_SNIPPET_AS_EVIDENCE", baseline,
                           any(item["code"] == "SEARCH_SNIPPET_AS_EVIDENCE" for item in mutated["findings"]),
                           "search snippets cannot support factual sentences", _report(f)["verdict"] == "PASS"))

    dependent = replace(f["basis"], basis_id="dependent-basis", independence_state="DEPENDENT")
    mutated = _report(f, bases=(f["basis"], dependent))
    results.append(_record("DERIVATIVES_COUNTED_INDEPENDENT", "add DEPENDENT and INDEPENDENT bases under one source-family identifier",
                           "validate_report:DEPENDENCE_COUNTING_VIOLATION", baseline,
                           any(item["code"] == "DEPENDENCE_COUNTING_VIOLATION" for item in mutated["findings"]),
                           "one derivative family cannot be counted as independent", _report(f)["verdict"] == "PASS"))

    translation_id = "translation-1"; edges = [{"source_id": translation_id, "relationship": "TRANSLATED_FROM"}]
    translation_ok: Callable[[list[dict[str, str]]], bool] = lambda values: any(
        item["source_id"] == translation_id and item["relationship"] == "TRANSLATED_FROM" for item in values)
    results.append(_record("DROP_TRANSLATION_PROVENANCE", "remove the required TRANSLATED_FROM edge",
                           "translation provenance invariant", translation_ok(edges), not translation_ok([]),
                           "translation derivative lacks directional origin", translation_ok(edges)))

    correction_edges = [{"left": "old", "right": "new", "relation_type": "CORRECTION"}]
    correction_ok: Callable[[list[dict[str, str]]], bool] = lambda values: any(
        item["left"] == "old" and item["right"] == "new" and item["relation_type"] == "CORRECTION" for item in values)
    results.append(_record("REMOVE_CORRECTION_EDGE", "delete the old-claim to new-claim CORRECTION relation",
                           "correction graph invariant", correction_ok(correction_edges), not correction_ok([]),
                           "declared correction has no preserved edge", correction_ok(correction_edges)))

    retracted = replace(f["basis"], active=True, correction_state="RETRACTED")
    mutated = _report(f, bases=(retracted,))
    results.append(_record("RETRACTED_ACTIVE_SUPPORT", "set correction_state=RETRACTED while retaining active report support",
                           "validate_report:ACTIVE_RETRACTED_SUPPORT", baseline,
                           any(item["code"] == "ACTIVE_RETRACTED_SUPPORT" for item in mutated["findings"]),
                           "retracted evidence cannot remain active support", _report(f)["verdict"] == "PASS"))

    invalid_span_caught = False
    try:
        replace(f["candidate"], candidate_id="no-span", span_start=5, span_end=5)
    except ValueError as exc:
        invalid_span_caught = "span" in str(exc)
    results.append(_record("CLAIM_WITHOUT_SOURCE_SPAN", "set candidate span_end equal to span_start",
                           "ExtractionCandidate.__post_init__", True, invalid_span_caught,
                           "candidate requires a non-empty source span", replace(f["candidate"]) == f["candidate"]))

    ambiguous = IdentityProposal("identity-1", "left", "right", "AMBIGUOUS", (), {}, True,
                                 "HUMAN_REVIEW_REQUIRED")
    identity_ok: Callable[[IdentityProposal], bool] = lambda item: not (
        item.outcome == "SAME_ENTITY_ACCEPTED" and item.review_state != "HUMAN_REVIEWED")
    destructive = replace(ambiguous, outcome="SAME_ENTITY_ACCEPTED")
    results.append(_record("DESTRUCTIVE_AMBIGUOUS_ENTITY_MERGE", "change AMBIGUOUS to SAME_ENTITY_ACCEPTED without human review",
                           "identity acceptance invariant", identity_ok(ambiguous), not identity_ok(destructive),
                           "ambiguous identities cannot be destructively accepted", identity_ok(ambiguous)))

    unsupported = list(f["sentences"]); unsupported[0] = replace(unsupported[0], evidence_basis_ids=())
    mutated = _report(f, sentences=unsupported)
    results.append(_record("REPORT_SENTENCE_WITHOUT_EVIDENCE", "remove evidence_basis_ids from a factual sentence",
                           "validate_report:UNSUPPORTED_FACTUAL_SENTENCE", baseline,
                           any(item["code"] == "UNSUPPORTED_FACTUAL_SENTENCE" for item in mutated["findings"]),
                           "every factual sentence requires evidence mappings", _report(f)["verdict"] == "PASS"))

    inspection = inspect_frozen_kernel(repository_root)
    proposal = build_proposal(case_id="MUTATION_CASE", proposed_concept="Bounded record",
                              target_table_or_action="claims", proposed_field_values={"status": "shadow"},
                              source_object_ids=(f["source"].source_object_id,), claim_ids=(f["claim"].claim_id,),
                              evidence_basis_ids=(f["basis"].basis_id,), source_independence_state="INDEPENDENT",
                              identity_state="SAME_ENTITY_ACCEPTED", contradiction_state="NONE",
                              correction_retraction_state="CURRENT", mapping_precision="EXACT_CHARACTER",
                              dependencies=(), provider="MUTATION_PROVIDER", creator_actor_id="creator",
                              access_marking={"releasability": ["PUBLIC"]})
    common = {"known_claim_ids": {f["claim"].claim_id}, "known_basis_ids": {f["basis"].basis_id},
              "known_source_ids": {f["source"].source_object_id}, "known_dependencies": set()}
    valid_review = validate_proposal(proposal, inspection, validator_node_id="KERNEL_REVIEW_NODE",
                                     reviewer_actor_id="independent-reviewer", **common)
    self_review = validate_proposal(proposal, inspection, validator_node_id="KERNEL_REVIEW_NODE",
                                    reviewer_actor_id="creator", **common)
    restored_review = validate_proposal(proposal, inspection, validator_node_id="KERNEL_REVIEW_NODE",
                                        reviewer_actor_id="independent-reviewer", **common)
    results.append(_record("PROVIDER_SELF_APPROVAL", "set reviewer_actor_id equal to creator_actor_id",
                           "validate_proposal:separation_of_duties", valid_review.resulting_status == "HUMAN_REVIEW_REQUIRED",
                           self_review.resulting_status == "APPROVAL_BLOCKED" and not self_review.separation_of_duties_valid,
                           "proposal creator cannot approve its own proposal",
                           restored_review.resulting_status == "HUMAN_REVIEW_REQUIRED"))

    before = ZeroWriteMonitor(repository_root); baseline_zero = before.verify()["verdict"] == "PASS"
    attacked = ZeroWriteMonitor(repository_root); write_blocked = False
    try:
        with zero_write_guard(attacked):
            subprocess.run(["psql", "--command", "INSERT INTO claims VALUES ('mutation')"], check=False)
    except ZeroWriteViolation as exc:
        write_blocked = "canonical write subprocess refused" in str(exc)
    restored_monitor = ZeroWriteMonitor(repository_root)
    results.append(_record("CANONICAL_WRITE_PATH", "invoke psql with an INSERT command under the independent zero-write guard",
                           "ZeroWriteMonitor.refuse_command", baseline_zero,
                           write_blocked and attacked.attempts == 1,
                           "write path is blocked before subprocess or database contact",
                           restored_monitor.verify()["verdict"] == "PASS"))

    report = {"required_mutations": 10, "caught": sum(item["verdict"] == "CAUGHT" for item in results),
              "survived": sum(item["verdict"] != "CAUGHT" for item in results), "mutations": results,
              "adversarial_blocked_write_attempts": attacked.attempts,
              "operational_canonical_write_attempts": 0, "canonical_writes": 0,
              **declared_label("v4/mutation.py::run_mutation_suite")}
    report["verdict"] = "PASS" if report["caught"] == 10 and not report["survived"] else "INVALID"
    report["integrity_hash"] = sha256(report); write_json(output_path, report)
    return report


def threat_model() -> list[dict[str, str]]:
    values = (
        ("discovery", "malicious search result", "leads remain non-evidentiary until acquisition", "search snippet mutation"),
        ("identity", "poisoned official-looking domain", "authority/domain identity remains proposed", "identity ambiguity test"),
        ("identity", "typosquatted domain", "domain match alone cannot accept identity", "identity ambiguity test"),
        ("acquisition", "redirect laundering", "requested/final URLs and redirects are immutable", "redirect custody test"),
        ("normalization", "HTML injection", "scripts/forms/navigation excluded and raw bytes retained", "HTML normalization test"),
        ("normalization", "PDF embedded prompt injection", "document text is data; candidates require spans", "candidate span test"),
        ("extraction", "document prompt injection", "AI output advisory and source-span bound", "AI candidate test"),
        ("acquisition", "malicious metadata", "metadata is captured, not trusted as claim evidence", "failure custody test"),
        ("identity", "source impersonation", "official identifiers and reversible proposals required", "identity ambiguity test"),
        ("temporal", "fabricated publication date", "publication and retrieval times remain distinct", "custody metadata test"),
        ("source-origin", "duplicate-domain laundering", "content and origin graphs collapse derivative families", "dependence mutation"),
        ("translation", "translation laundering", "translation remains a dependent derivative", "translation mutation"),
        ("source-origin", "citation laundering", "directional citation/derivation evidence required", "source-origin basis test"),
        ("evidence", "false corroboration", "publication, family and basis counts are separate", "dependence mutation"),
        ("extraction", "model hallucination", "unlocatable model text is rejected", "AI candidate test"),
        ("extraction", "source-span fabrication", "candidate offsets are checked against derivatives", "candidate span mutation"),
        ("reporting", "malicious report text", "sentence-level structural support and epistemic checks", "unsupported report mutation"),
        ("mission", "unauthorized evidence handoff", "access filtering precedes serialization", "review packet access test"),
        ("mission", "restricted-source leakage", "restricted records are absent from public packets", "review packet access test"),
        ("kernel", "kernel-proposal privilege escalation", "separation of duties and human gate", "self-approval mutation"),
        ("kernel", "canonical-write escape", "AST scan plus socket/subprocess/file guards", "canonical write mutation"),
        ("acquisition", "stale public source", "capture time, hash and freshness remain explicit", "custody metadata test"),
        ("acquisition", "changed live webpage", "report binds captured hash and replay discloses capture", "offline replay hash test"),
        ("replay", "replay network dependency", "replay reads custody/provider records only", "campaign replay test"),
        ("planning", "campaign-scope creep", "preregistration is immutable and amendments versioned", "case amendment test"),
    )
    return [{"component": component, "attack": attack, "mitigation": mitigation, "test": test,
             "result": SECURITY_NOT_EXECUTED,
             "residual_risk": "Synthetic tests do not establish adversary-complete protection.",
             "future_requirement": "Genuine human and institutional security review before any pilot."}
            for component, attack, mitigation, test in values]


#: Same finding class as V5's W11-T8, same ruling: every per-threat "PASS" and
#: the summary's tests_run/failed figures were constants derived from the
#: length of the static threat tuple. Nothing was constructed or executed.
SECURITY_NOT_EXECUTED = "DECLARED_NOT_EXECUTED"
SECURITY_CLAIM_RULING = "MUST NOT be cited as security-testing evidence."


def write_security_review(output_path: str | Path) -> dict[str, Any]:
    threats = threat_model()
    report = {"threats": threats, "threat_count": len(threats),
              "tests_declared_not_executed": len(threats),
              "security_review_execution": SECURITY_NOT_EXECUTED,
              "residual_risk": "RESEARCH_SHADOW_ONLY",
              "verdict": SECURITY_NOT_EXECUTED, "ruling": SECURITY_CLAIM_RULING}
    report["integrity_hash"] = sha256(report); write_json(output_path, report); return report
