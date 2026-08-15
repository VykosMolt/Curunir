"""Evidence-bound report rendering and structural support validation."""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Iterable, Mapping

from .io import append_jsonl, write_json
from .models import (
    ClaimUnit, EvidenceBasis, ExtractionCandidate, InferenceRecord, SentenceEvidence, SourceRecord,
    canonical_json, sha256, stable_id,
)

REPORT_SECTIONS = (
    "EXECUTIVE_ASSESSMENT", "INVESTIGATION_QUESTION_AND_SCOPE", "METHOD_AND_LIMITS",
    "KEY_FINDINGS", "TIMELINE", "ENTITIES_AND_PROGRAMMES",
    "EVIDENCE_BASIS_AND_SOURCE_DEPENDENCE_ANALYSIS",
    "CONTRADICTIONS_CORRECTIONS_AND_RETRACTIONS", "COMPETING_HYPOTHESES",
    "OPERATIONAL_OR_STRATEGIC_IMPLICATIONS", "UNKNOWNS_AND_EVIDENCE_GAPS",
    "WHAT_CURUNIR_REFUSES_TO_CONCLUDE", "SOURCE_REGISTER", "REPRODUCIBILITY_AND_INTEGRITY",
    "REVIEW_STATUS",
)
FACT_TYPES = {"FACTUAL", "INFERENCE"}


def sentence(*, section: str, text: str, sentence_type: str,
             claim_ids: tuple[str, ...] = (), evidence_basis_ids: tuple[str, ...] = (),
             source_object_ids: tuple[str, ...] = (), epistemic_state: str = "UNKNOWN",
             inference_id: str | None = None) -> SentenceEvidence:
    if section not in REPORT_SECTIONS:
        raise ValueError("unknown required report section")
    if sentence_type not in {"FACTUAL", "INFERENCE", "METHOD", "LIMITATION", "REFUSAL", "REVIEW_STATUS"}:
        raise ValueError("invalid report sentence type")
    return SentenceEvidence(stable_id("report-sentence", section, text), section, text, sentence_type,
                            claim_ids, evidence_basis_ids, source_object_ids, epistemic_state, inference_id)


def validate_report(*, sentences: Iterable[SentenceEvidence], claims: Iterable[ClaimUnit],
                    bases: Iterable[EvidenceBasis], sources: Iterable[SourceRecord],
                    candidates: Iterable[ExtractionCandidate] = (),
                    inferences: Iterable[InferenceRecord] = (), search_lead_ids: Iterable[str] = (),
                    failed_source_ids: Iterable[str] = (), restricted_source_ids: Iterable[str] = (),
                    requester_access: str = "PUBLIC") -> dict[str, Any]:
    values = tuple(sentences); claim_map = {item.claim_id: item for item in claims}
    basis_map = {item.basis_id: item for item in bases}; source_map = {item.source_object_id: item for item in sources}
    inference_map = {item.inference_id: item for item in inferences}; candidate_map = {item.candidate_id: item for item in candidates}
    lead_ids = set(search_lead_ids)
    failed = set(failed_source_ids); restricted = set(restricted_source_ids)
    findings: list[dict[str, Any]] = []
    if set(REPORT_SECTIONS) - {item.section for item in values}:
        findings.append({"code": "MISSING_REQUIRED_SECTIONS", "sections": sorted(set(REPORT_SECTIONS) - {item.section for item in values})})
    factual_count = sum(item.sentence_type == "FACTUAL" for item in values)
    inference_count = sum(item.sentence_type == "INFERENCE" for item in values)
    mapped_count = 0
    for item in values:
        if item.sentence_type == "FACTUAL":
            if not item.claim_ids or not item.evidence_basis_ids or not item.source_object_ids:
                findings.append({"code": "UNSUPPORTED_FACTUAL_SENTENCE", "sentence_id": item.sentence_id}); continue
            if not set(item.claim_ids) <= claim_map.keys():
                findings.append({"code": "UNKNOWN_CLAIM", "sentence_id": item.sentence_id}); continue
            if not set(item.evidence_basis_ids) <= basis_map.keys():
                findings.append({"code": "UNKNOWN_EVIDENCE_BASIS", "sentence_id": item.sentence_id}); continue
            if not set(item.source_object_ids) <= source_map.keys():
                findings.append({"code": "MISSING_SOURCE_OBJECT", "sentence_id": item.sentence_id}); continue
            if set(item.source_object_ids) & lead_ids:
                findings.append({"code": "SEARCH_SNIPPET_AS_EVIDENCE", "sentence_id": item.sentence_id}); continue
            if set(item.source_object_ids) & failed:
                findings.append({"code": "ACQUISITION_FAILURE_AS_EVIDENCE", "sentence_id": item.sentence_id}); continue
            if requester_access == "PUBLIC" and set(item.source_object_ids) & restricted:
                findings.append({"code": "RESTRICTED_EVIDENCE_LEAK", "sentence_id": item.sentence_id}); continue
            selected = [basis_map[value] for value in item.evidence_basis_ids]
            if any(not basis.active or basis.correction_state == "RETRACTED" for basis in selected):
                findings.append({"code": "ACTIVE_RETRACTED_SUPPORT", "sentence_id": item.sentence_id}); continue
            if any(not claim_map[value].candidate_ids for value in item.claim_ids):
                findings.append({"code": "CLAIM_WITHOUT_SPAN", "sentence_id": item.sentence_id}); continue
            referenced_candidates = {candidate_id for value in item.claim_ids
                                     for candidate_id in claim_map[value].candidate_ids}
            if candidate_map and not referenced_candidates <= candidate_map.keys():
                findings.append({"code": "UNKNOWN_SUPPORT_CANDIDATE", "sentence_id": item.sentence_id}); continue
            if candidate_map and any(not candidate_map[value].mapping_precision or
                                     candidate_map[value].mapping_precision == "UNMAPPED" or
                                     candidate_map[value].span_end <= candidate_map[value].span_start
                                     for value in referenced_candidates):
                findings.append({"code": "CLAIM_WITHOUT_MAPPED_SOURCE_SPAN", "sentence_id": item.sentence_id}); continue
            if candidate_map and not {candidate_map[value].source_object_id for value in referenced_candidates} <= set(item.source_object_ids):
                findings.append({"code": "SUPPORT_SPAN_SOURCE_NOT_CITED", "sentence_id": item.sentence_id}); continue
            if _epistemic_overreach(item.sentence, item.epistemic_state):
                findings.append({"code": "EPISTEMIC_LANGUAGE_OVERREACH", "sentence_id": item.sentence_id}); continue
            mapped_count += 1
        elif item.sentence_type == "INFERENCE":
            if not item.inference_id or item.inference_id not in inference_map:
                findings.append({"code": "UNMAPPED_INFERENCE", "sentence_id": item.sentence_id})
            elif not inference_map[item.inference_id].premise_claim_ids:
                findings.append({"code": "INFERENCE_WITHOUT_PREMISES", "sentence_id": item.sentence_id})
        elif item.claim_ids or item.evidence_basis_ids or item.source_object_ids:
            findings.append({"code": "NONFACTUAL_SENTENCE_CARRIES_HIDDEN_EVIDENCE", "sentence_id": item.sentence_id})
    # No evidence basis marked dependent can be counted as an independent family.
    family_independence: dict[str, set[str]] = {}
    for basis in basis_map.values():
        family_independence.setdefault(basis.source_family_id, set()).add(basis.independence_state)
    mixed = sorted(key for key, states in family_independence.items() if "INDEPENDENT" in states and "DEPENDENT" in states)
    if mixed:
        findings.append({"code": "DEPENDENCE_COUNTING_VIOLATION", "families": mixed})
    coverage = 100.0 if factual_count == mapped_count else (100.0 * mapped_count / max(1, factual_count))
    return {
        "verdict": "PASS" if not findings else "INVALID",
        "report_evidence_coverage": "100_PERCENT_FOR_FACTUAL_SENTENCES" if coverage == 100 else f"{coverage:.2f}_PERCENT",
        "factual_sentences": factual_count, "evidence_mapped_factual_sentences": mapped_count,
        "inference_sentences": inference_count, "unsupported_sentences": factual_count - mapped_count,
        "active_retracted_support_findings": sum(item["code"] == "ACTIVE_RETRACTED_SUPPORT" for item in findings),
        "dependence_counting_violations": sum(item["code"] == "DEPENDENCE_COUNTING_VIOLATION" for item in findings),
        "search_snippet_support_count": sum(item["code"] == "SEARCH_SNIPPET_AS_EVIDENCE" for item in findings),
        "findings": findings,
    }


def _epistemic_overreach(text: str, state: str) -> bool:
    lowered = text.casefold()
    strong = bool(re.search(r"\b(proven|conclusively|certainly|validated intelligence|fully operational)\b", lowered))
    return strong and state not in {"SUPPORTED", "DIRECTLY_STATED"}


def render_report(*, case: Mapping[str, Any], sentences: Iterable[SentenceEvidence],
                  sources: Iterable[SourceRecord], claims: Iterable[ClaimUnit],
                  bases: Iterable[EvidenceBasis], hypotheses: Iterable[Mapping[str, Any]],
                  contradictions: Iterable[Mapping[str, Any]], validation: Mapping[str, Any],
                  output_dir: str | Path) -> dict[str, str]:
    output = Path(output_dir); output.mkdir(parents=True, exist_ok=True)
    sentence_values = tuple(sentences); source_values = tuple(sources); claim_values = tuple(claims)
    basis_values = tuple(bases); hypothesis_values = tuple(dict(item) for item in hypotheses)
    contradiction_values = tuple(dict(item) for item in contradictions)
    by_section: dict[str, list[SentenceEvidence]] = {key: [] for key in REPORT_SECTIONS}
    for item in sentence_values: by_section[item.section].append(item)
    lines = [f"# {case['title']}", "", "RESEARCH SHADOW — HUMAN REVIEW PENDING", ""]
    text_lines = [str(case["title"]), "RESEARCH SHADOW — HUMAN REVIEW PENDING", ""]
    for section in REPORT_SECTIONS:
        title = section.replace("_", " ").title()
        lines.extend((f"## {title}", "")); text_lines.extend((title.upper(), ""))
        for item in by_section[section]:
            lines.append(item.sentence); text_lines.append(item.sentence)
        lines.append(""); text_lines.append("")
    markdown = "\n".join(lines).rstrip() + "\n"; plain = "\n".join(text_lines).rstrip() + "\n"
    report = {
        "case_id": case["case_id"], "case_integrity_hash": case["integrity_hash"],
        "report_status": "REVIEW_PENDING", "review_state": "HUMAN_REVIEW_PENDING",
        "sections": {section: [item.to_record() for item in by_section[section]] for section in REPORT_SECTIONS},
        "validation": dict(validation), "markdown_sha256": sha256(markdown), "text_sha256": sha256(plain),
        "source_register_sha256": sha256([item.to_record() for item in source_values]),
        "claim_register_sha256": sha256([item.to_record() for item in claim_values]),
    }
    write_json(output / "investigation_report.json", report)
    (output / "investigation_report.md").write_text(markdown, encoding="utf-8")
    (output / "investigation_report.txt").write_text(plain, encoding="utf-8")
    append_jsonl(output / "sentence_evidence_ledger.jsonl", (item.to_record() for item in sentence_values))
    write_json(output / "source_register.json", [item.to_record() for item in source_values])
    write_json(output / "claim_register.json", [item.to_record() for item in claim_values])
    write_json(output / "hypothesis_register.json", list(hypothesis_values))
    write_json(output / "contradiction_register.json", list(contradiction_values))
    write_json(output / "evidence_basis_register.json", [item.to_record() for item in basis_values])
    return {
        "report_json": sha256(canonical_json(report)), "report_markdown": sha256(markdown),
        "report_text": sha256(plain), "sentence_ledger": sha256([item.to_record() for item in sentence_values]),
    }
