"""Clean held-out corpus, reviewer isolation, and capability scoring
(contract Sections 24 and 25).

The main implementation context orchestrates by identifier and hash only:
packet payloads are built by code, frozen by ``packets.freeze_corpus``, and
sealed answers may be opened ONLY through ``consensus_and_score``, which
refuses to run before the review-completion manifest freezes.
"""
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any, Iterable, Mapping

from ..v4.io import read_json, read_jsonl, write_json
from ..v4.models import sha256, stable_id
from .campaign import _load_retrieval, _load_source
from ..v4.custody import normalize_source
from .models import ADMISSION_STAGES, CapabilityMetrics, REVIEW_DECISIONS, now_utc
from .packets import ReviewPacket, SealedBuilderEntry, build_packet, freeze_corpus

_ADMISSION_STAGE_MENU = tuple(ADMISSION_STAGES)

SURFACE_KEYS = ("SURFACE_1", "SURFACE_2", "SURFACE_3", "SURFACE_4", "SURFACE_5",
                "SURFACE_6")
_SURFACE_NAMES = {
    "SURFACE_1": "SURFACE_1_SPAN_GROUNDED_EXTRACTION_PRECISION",
    "SURFACE_2": "SURFACE_2_SOURCE_ORIGIN_ACCURACY",
    "SURFACE_3": "SURFACE_3_FALSE_CORROBORATION_ACCURACY",
    "SURFACE_4": "SURFACE_4_CLAIM_SUPPORT_ACCURACY",
    "SURFACE_5": "SURFACE_5_CONTRADICTION_CORRECTION_AND_RETRACTION_ACCURACY",
    "SURFACE_6": "SURFACE_6_REPORT_FAITHFULNESS",
}
DEFAULT_MINIMUMS = {"SURFACE_1": 80, "SURFACE_2": 60, "SURFACE_3": 60,
                    "SURFACE_4": 60, "SURFACE_5": 40, "SURFACE_6": 0}
_RESERVE_TARGET = 0.25
_UNRESOLVABLE_ANSWERS = frozenset({"INDEPENDENCE_UNKNOWN", "EPISTEMICALLY_UNRESOLVABLE",
                                   "UNRESOLVED"})


def _documents_for(custody_root: Path) -> dict[str, dict[str, Any]]:
    retrievals = {record["retrieval_id"]: _load_retrieval(record)
                  for record in read_jsonl(custody_root / "records" / "retrieval_records.jsonl")}
    lookup: dict[str, dict[str, Any]] = {}
    for record in read_jsonl(custody_root / "records" / "source_records.jsonl"):
        source_id = record["source_object_id"]
        if source_id in lookup:
            continue
        source = _load_source(record)
        try:
            document = normalize_source(source, retrievals[source.retrieval_ids[0]])
        except ValueError:
            continue
        # Reviewer-facing presentation text: control characters (pdftotext form
        # feeds and stray control bytes) are replaced ONE-FOR-ONE with spaces so
        # every span offset stays valid while packets render cleanly.  Custody
        # derivatives are never modified.
        presentation = "".join(
            " " if character < " " and character not in "\n\t" else character
            for character in document.text)
        lookup[source_id] = {"text": presentation, "language": source.language,
                             "publisher": source.publisher,
                             "source_class": source.source_class,
                             "publication_time": source.publication_time,
                             "title": source.title}
    return lookup


def _excerpt(lookup: Mapping[str, Any], source_id: str, needle: str | None = None,
             length: int = 480) -> dict[str, Any] | None:
    entry = lookup.get(source_id)
    if entry is None:
        return None
    text = entry["text"]
    if needle:
        position = text.find(needle)
        start = position if position >= 0 else 0
        end = min(len(text), start + max(len(needle), length))
    else:
        start, end = 0, min(len(text), length)
    return {"source_id": source_id, "span_start": start, "span_end": end,
            "original_text": text[start:end], "language": entry["language"] or "und"}


def _reserve_split(items: list[tuple[dict[str, Any], str, str]]
                   ) -> tuple[list[tuple[dict[str, Any], str, str]],
                              list[tuple[dict[str, Any], str, str]]]:
    ordered = sorted(items, key=lambda item: sha256(item[0]))
    reserve_count = max(1, int(len(ordered) * _RESERVE_TARGET)) if len(ordered) > 1 else 0
    return ordered[reserve_count:], ordered[:reserve_count]


def build_heldout(*, campaigns: Iterable[Mapping[str, Any]], output_root: str | Path,
                  reviewer_prompts: Mapping[str, str],
                  minimums: Mapping[str, int] | None = None) -> dict[str, Any]:
    """Build and freeze the clean held-out corpus from campaign registers."""
    out = Path(output_root)
    limits = {**DEFAULT_MINIMUMS, **dict(minimums or {})}
    for key in SURFACE_KEYS:
        if key not in reviewer_prompts or len(reviewer_prompts[key].strip()) < 80:
            raise ValueError(f"substantive reviewer prompt required for {key}")

    staged: dict[str, list[tuple[dict[str, Any], str, str]]] = {
        key: [] for key in SURFACE_KEYS}
    source_lookup: dict[str, dict[str, Any]] = {}
    class_coverage: dict[str, set[str]] = {
        "candidate_types": set(), "roles": set(), "dependence_states": set(),
        "claim_classes": set(), "relation_classes": set(), "modalities": set()}

    for campaign in campaigns:
        analysis = Path(campaign["analysis_root"])
        custody = Path(campaign["custody_root"])
        report_root = Path(campaign["report_root"])
        lookup = _documents_for(custody)
        source_lookup.update(lookup)
        candidates = {record["candidate_id"]: record
                      for record in read_jsonl(analysis / "candidate_register.jsonl")}
        claims = {record["claim_id"]: record
                  for record in read_json(analysis / "claim_register.json")}

        for row in read_jsonl(analysis / "admission_register.jsonl"):
            candidate = candidates.get(row["candidate_id"])
            if candidate is None:
                continue
            excerpt = _excerpt(lookup, candidate["source_object_id"],
                               candidate["original_text"])
            if excerpt is None:
                continue
            class_coverage["candidate_types"].add(candidate["candidate_type"])
            material = {
                "excerpts": [excerpt],
                "surrounding_context": excerpt["original_text"],
                "candidate": {"span_text": candidate["original_text"],
                              "page_or_section": candidate["page_or_section"],
                              "mapping_precision": candidate["mapping_precision"],
                              "admission_options": "Admission labels: "
                              + ", ".join(_ADMISSION_STAGE_MENU) + "."},
                "reviewer_instructions": reviewer_prompts["SURFACE_1"],
            }
            answer = row["stage"]
            staged["SURFACE_1"].append((material, answer, "ADMISSION_CASE"))

        for row in read_jsonl(analysis / "role_register.jsonl"):
            for edge in row.get("role_edges", ()):
                excerpt = _excerpt(lookup, row.get("subject_id", ""))
                if excerpt is None:
                    continue
                class_coverage["roles"].add(edge["role"])
                evidence_rows = [{"kind": ref["evidence_kind"],
                                  "value": ref["detail"]}
                                 for ref in edge.get("evidence", ())]
                if not evidence_rows:
                    continue
                material = {
                    "excerpts": [excerpt],
                    "role_metadata": evidence_rows,
                    "role_subject": edge.get("agent_entity_id", ""),
                    "reviewer_instructions": reviewer_prompts["SURFACE_2"],
                }
                staged["SURFACE_2"].append((material, edge["role"], "ROLE_CASE"))

        for row in read_jsonl(analysis / "dependence_register.jsonl"):
            left_id, right_id = row["left_publication_id"], row["right_publication_id"]
            left_excerpt = _excerpt(lookup, left_id)
            right_excerpt = _excerpt(lookup, right_id)
            if left_excerpt is None or right_excerpt is None:
                continue
            class_coverage["dependence_states"].add(row["state"])
            explanation = row.get("explanation", {})
            material = {
                "excerpts": [left_excerpt, right_excerpt],
                "source_metadata": {
                    side: {"publisher": lookup[side]["publisher"],
                           "source_class": lookup[side]["source_class"],
                           "language": lookup[side]["language"]}
                    for side in (left_id, right_id)},
                "publication_timing": {
                    side: lookup[side]["publication_time"] or "UNSTATED"
                    for side in (left_id, right_id)},
                "dependence_evidence": sorted(
                    ({"kind": signal["kind"], "detail": signal["detail"]}
                     for signal in row.get("signals", ())),
                    key=lambda item: (item["kind"], item["detail"])),
                "content_overlap_analysis": sorted(explanation.get("positive_evidence", ())
                                                   ) or ["no positive signals recorded"],
                "reviewer_instructions": reviewer_prompts["SURFACE_3"],
            }
            if row["state"] in _UNRESOLVABLE_ANSWERS:
                demonstration = str(row.get("outcome", {}).get(
                    "evidence_insufficiency_demonstration") or "")
                if demonstration:
                    material["evidence_insufficiency_demonstration"] = demonstration
            staged["SURFACE_3"].append((material, row["state"], "PAIR_CASE"))

        for row in read_jsonl(analysis / "claim_support_register.jsonl"):
            claim = claims.get(row["claim_id"])
            if claim is None:
                continue
            candidate = candidates.get(claim["candidate_ids"][0], {})
            excerpt = _excerpt(lookup, candidate.get("source_object_id", ""),
                               claim["original_wording"])
            if excerpt is None:
                continue
            class_coverage["claim_classes"].add(row.get("claim_class", "UNCLASSIFIED"))
            class_coverage["modalities"].add(claim["modality"])
            material = {
                "excerpts": [excerpt],
                "claim": {"normalized_statement": claim["normalized_statement"],
                          "subject": claim["subject"], "predicate": claim["predicate"],
                          "object_or_value": claim["object_or_value"],
                          "polarity": claim["polarity"], "modality": claim["modality"],
                          "temporal_scope": claim["temporal_scope"]},
                "qualifications": [marker for marker in (claim["modality"],)
                                   if marker not in {"ASSERTED"}],
                "reviewer_instructions": reviewer_prompts["SURFACE_4"],
            }
            staged["SURFACE_4"].append((material, row["state"], "SUPPORT_CASE"))

        for row in read_jsonl(analysis / "relation_register.jsonl"):
            left = claims.get(row["left_claim_id"])
            right = claims.get(row["right_claim_id"])
            if left is None or right is None:
                continue
            left_candidate = candidates.get(left["candidate_ids"][0], {})
            right_candidate = candidates.get(right["candidate_ids"][0], {})
            left_excerpt = _excerpt(lookup, left_candidate.get("source_object_id", ""),
                                    left["original_wording"])
            right_excerpt = _excerpt(lookup, right_candidate.get("source_object_id", ""),
                                     right["original_wording"])
            if left_excerpt is None or right_excerpt is None:
                continue
            class_coverage["relation_classes"].add(row["relation"])
            material = {
                "excerpts": [left_excerpt, right_excerpt],
                "publication_timing": {
                    row["left_claim_id"]: str(row.get("left_frame", {}).get(
                        "publication_time") or "UNSTATED"),
                    row["right_claim_id"]: str(row.get("right_frame", {}).get(
                        "publication_time") or "UNSTATED")},
                "relation_context": {"left_statement": left["normalized_statement"],
                                     "right_statement": right["normalized_statement"]},
                "reviewer_instructions": reviewer_prompts["SURFACE_5"],
            }
            staged["SURFACE_5"].append((material, row["relation"], "RELATION_CASE"))

        for row in read_jsonl(report_root / "proposition_evidence_ledger.jsonl"):
            proposition = row["proposition"]
            supporting = [claims[claim_id] for claim_id in
                          proposition.get("supporting_claim_ids", ())
                          if claim_id in claims]
            basis_rows = [{
                "claim": claim["normalized_statement"], "polarity": claim["polarity"],
                "modality": claim["modality"], "temporal_scope": claim["temporal_scope"],
            } for claim in supporting] or [{"claim": "NO_MAPPED_CLAIM"}]
            first_candidate = (candidates.get(supporting[0]["candidate_ids"][0], {})
                               if supporting else {})
            excerpt = (_excerpt(lookup, first_candidate.get("source_object_id", ""))
                       if first_candidate else None)
            material = {
                "report_sentence": row.get("final_sentence")
                or proposition["text"],
                "structured_basis": basis_rows,
                "excerpts": [excerpt] if excerpt else [],
                "reviewer_instructions": reviewer_prompts["SURFACE_6"],
            }
            answer = ("RENDERED_FAITHFULLY"
                      if str(row["disposition"]).startswith("PUBLISHED")
                      else "RENDERING_REFUSED")
            staged["SURFACE_6"].append((material, answer, "PROPOSITION_CASE"))

    active_packets: list[ReviewPacket] = []
    reserve_packets: list[ReviewPacket] = []
    entries: list[SealedBuilderEntry] = []
    answers: dict[str, str] = {}
    shortfalls: dict[str, dict[str, int]] = {}
    construction_defects: list[dict[str, Any]] = []
    duplicate_materials = 0
    from .packets import _context_gap
    for key in SURFACE_KEYS:
        seen_materials: set[str] = set()
        filtered: list[tuple[dict[str, Any], str, str, str]] = []
        for material, answer, stratum in staged[key]:
            digest = sha256(material)
            if digest in seen_materials:
                duplicate_materials += 1
                continue
            seen_materials.add(digest)
            declared = _declared_context(key, material)
            if declared == "PACKET_CONSTRUCTION_DEFECT":
                missing_required, _supporting = _context_gap(
                    _SURFACE_NAMES[key], material)
                construction_defects.append({
                    "surface": key, "stratum": stratum,
                    "missing_required": list(missing_required)})
                continue
            filtered.append((material, answer, stratum, declared))
        if len(filtered) < 2:
            shortfalls[key] = {"required": max(limits[key], 2),
                               "available": len(filtered), "frozen": 0}
            continue
        active, reserve = _reserve_split(
            [(material, answer, stratum) for material, answer, stratum, _d in filtered])
        declared_by_digest = {sha256(material): declared
                              for material, _a, _s, declared in filtered}
        if len(active) < limits[key]:
            shortfalls[key] = {"required": limits[key], "available": len(active),
                               "frozen": len(active)}
        for bucket, flag in ((active, False), (reserve, True)):
            for material, answer, stratum in bucket:
                packet, entry = build_packet(
                    surface=_SURFACE_NAMES[key], material=material,
                    context_class=declared_by_digest[sha256(material)],
                    stratum=stratum, reserve=flag)
                if flag:
                    reserve_packets.append(packet)
                else:
                    active_packets.append(packet)
                entries.append(entry)
                answers[packet.packet_id] = answer

    manifest = freeze_corpus(active_packets, answers, reserve_packets, out,
                             builder_entries=entries, source_lookup=source_lookup)
    coverage = {name: sorted(values) for name, values in class_coverage.items()}
    summary = {
        "manifest": manifest,
        "shortfalls": shortfalls,
        "class_coverage": coverage,
        "minimums": limits,
        "identical_material_duplicates_dropped": duplicate_materials,
        "builder_construction_defects_excluded": construction_defects,
        "built_time": now_utc(),
    }
    write_json(out / "build_summary.json", summary)
    return summary


def _declared_context(surface_key: str, material: Mapping[str, Any]) -> str:
    from .packets import classify_context  # local import to reuse the classifier
    probe = {"packet_id": "probe", "surface": _SURFACE_NAMES[surface_key],
             "blinded_material": material}

    class _Probe:
        surface = _SURFACE_NAMES[surface_key]
        blinded_material = material
    return classify_context(_Probe())  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Reviewer assignment and completion freeze
# ---------------------------------------------------------------------------

def assign_reviewers(*, corpus_root: str | Path, reviewer_ids: Iterable[str],
                     output_root: str | Path) -> dict[str, Any]:
    corpus = Path(corpus_root)
    out = Path(output_root)
    out.mkdir(parents=True, exist_ok=True)
    reviewers = tuple(reviewer_ids)
    if len(reviewers) != 3 or len(set(reviewers)) != 3:
        raise ValueError("exactly three distinct reviewers are required")
    manifest = json.loads((corpus / "manifest.json").read_text(encoding="utf-8"))
    packet_ids = [record["packet_id"]
                  for record in read_jsonl(corpus / "frozen_packets.jsonl")]
    assignments = {}
    for reviewer in reviewers:
        order = sorted(packet_ids, key=lambda pid: sha256(f"{reviewer}|{pid}"))
        payload = {
            "reviewer": reviewer,
            "packet_file": str(corpus / "frozen_packets.jsonl"),
            "packet_order": order,
            "isolation": {
                "curunir_verdicts_provided": False,
                "other_reviewer_outputs_provided": False,
                "development_labels_provided": False,
                "defect_annotations_provided": False,
            },
        }
        path = out / f"assignment_{reviewer}.json"
        write_json(path, payload)
        assignments[reviewer] = {"path": str(path), "packets": len(order),
                                 "order_hash": sha256(order)}
    contract = {
        "corpus_manifest_hash": sha256(manifest),
        "reviewers": assignments,
        "isolation_contract": [
            "Reviewers receive only their assignment file and the frozen packet file.",
            "No Curunir verdict, reviewer output, development label, or defect "
            "annotation is provided.",
            "The implementation context does not read packet payloads or sealed "
            "answers until the review-completion manifest freezes.",
        ],
        "created_time": now_utc(),
    }
    write_json(out / "isolation_contract.json", contract)
    return contract


def freeze_reviews(*, review_files: Mapping[str, str | Path],
                   assignment_root: str | Path, output_path: str | Path,
                   ) -> dict[str, Any]:
    assignment_dir = Path(assignment_root)
    completion: dict[str, Any] = {}
    for reviewer, path in sorted(review_files.items()):
        assignment = read_json(assignment_dir / f"assignment_{reviewer}.json")
        expected = list(assignment["packet_order"])
        records = read_jsonl(Path(path))
        seen = [record.get("packet_id") for record in records]
        if sorted(seen) != sorted(expected):
            raise ValueError(f"reviewer {reviewer} output incomplete or excess")
        if len(set(seen)) != len(seen):
            raise ValueError(f"reviewer {reviewer} duplicated packets")
        for record in records:
            decision = record.get("decision")
            if decision not in REVIEW_DECISIONS and decision != "LABELED":
                raise ValueError(f"unknown decision from {reviewer}: {decision}")
            if decision == "LABELED" and not str(record.get("label", "")).strip():
                raise ValueError(f"LABELED record without a label from {reviewer}")
            if record.get("other_reviews_seen") is not False:
                raise ValueError(f"isolation violation in {reviewer} output")
            if record.get("human_review") is not False:
                raise ValueError(f"human-review misrepresentation in {reviewer} output")
        completion[reviewer] = {
            "path": str(path),
            "records": len(records),
            "sha256": sha256(Path(path).read_bytes()),
        }
    manifest = {
        "review_completion": completion,
        "all_reviewers_complete": True,
        "frozen_time": now_utc(),
    }
    manifest["integrity_hash"] = sha256(
        {key: value for key, value in manifest.items() if key != "frozen_time"})
    write_json(Path(output_path), manifest)
    return manifest


# ---------------------------------------------------------------------------
# Section 25 — scoring
# ---------------------------------------------------------------------------

def consensus_and_score(*, corpus_root: str | Path, completion_manifest: str | Path,
                        review_files: Mapping[str, str | Path],
                        output_root: str | Path,
                        campaign_support_audits: Iterable[Mapping[str, Any]] = (),
                        freeze_verification: Mapping[str, Any] | None = None,
                        ) -> dict[str, Any]:
    """Open sealed answers ONLY after the completion manifest verifies."""
    corpus = Path(corpus_root)
    out = Path(output_root)
    out.mkdir(parents=True, exist_ok=True)
    manifest_path = Path(completion_manifest)
    if not manifest_path.is_file():
        raise ValueError("scoring requires the frozen review-completion manifest")
    completion = read_json(manifest_path)
    if not completion.get("all_reviewers_complete"):
        raise ValueError("review panel incomplete; scoring refused")
    for reviewer, entry in completion["review_completion"].items():
        current = sha256(Path(entry["path"]).read_bytes())
        if current != entry["sha256"]:
            raise ValueError(f"reviewer output changed after completion freeze: {reviewer}")
    if freeze_verification is not None and freeze_verification.get("verdict") != "PASS":
        raise ValueError("production freeze verification failed; held-out run INVALID")

    sealed = json.loads((corpus / "sealed_answers.json").read_text(encoding="utf-8"))["answers"]
    packet_surface = {record["packet_id"]: record["surface"]
                      for record in read_jsonl(corpus / "frozen_packets.jsonl")}
    decisions: dict[str, list[str]] = {}
    for reviewer, path in sorted(review_files.items()):
        for record in read_jsonl(Path(path)):
            vote = (str(record.get("label", "")).strip()
                    if record.get("decision") == "LABELED" else record["decision"])
            decisions.setdefault(record["packet_id"], []).append(vote)

    per_surface: dict[str, Counter] = {name: Counter() for name in _SURFACE_NAMES.values()}
    consensus_rows = []
    for packet_id, surface in sorted(packet_surface.items()):
        votes = decisions.get(packet_id, [])
        counts = Counter(votes)
        majority, majority_count = (counts.most_common(1)[0]
                                    if counts else ("NO_VOTES", 0))
        answer = sealed[packet_id]
        surface_counter = per_surface[surface]
        surface_counter["total"] += 1
        outcome = "PARTIAL"
        if majority_count * 2 <= len(votes):
            outcome = "DISAGREEMENT"
            surface_counter["disagreements"] += 1
        elif majority == "PACKET_DEFECT":
            outcome = "PACKET_DEFECT"
            surface_counter["packet_defects"] += 1
        elif majority == "EPISTEMICALLY_UNRESOLVABLE":
            if answer in _UNRESOLVABLE_ANSWERS:
                outcome = "CORRECTLY_UNRESOLVABLE"
                surface_counter["correctly_unresolvable"] += 1
            else:
                outcome = "INCORRECT"
                surface_counter["incorrect"] += 1
        elif majority in {"INSUFFICIENT_INFORMATION", "CANNOT_ADJUDICATE", "AMBIGUOUS"}:
            outcome = "UNADJUDICATED"
            surface_counter["unadjudicated"] += 1
        elif majority == answer:
            outcome = "CORRECT"
            surface_counter["correct"] += 1
        elif majority == "PARTIALLY_CORRECT":
            outcome = "PARTIAL"
            surface_counter["partial"] += 1
        else:
            outcome = "INCORRECT"
            surface_counter["incorrect"] += 1
        consensus_rows.append({"packet_id": packet_id, "surface": surface,
                               "votes": dict(counts), "majority": majority,
                               "outcome": outcome})

    critical: list[str] = []
    for audit in campaign_support_audits:
        for counter_name, value in (audit.get("zero_tolerance") or audit).items():
            if isinstance(value, int) and value > 0 and counter_name != "assessments":
                critical.append(f"{counter_name}={value}")

    surface_reports = {}
    for surface, counter in per_surface.items():
        total = counter["total"]
        if not total:
            continue
        packet_defects = counter["packet_defects"]
        adjudicable = total - packet_defects - counter["correctly_unresolvable"]
        metrics = CapabilityMetrics(
            total_required_cases=total,
            adjudicable_cases=max(0, adjudicable),
            epistemically_unresolvable_cases=counter["correctly_unresolvable"],
            correctly_identified_unresolvable_cases=counter["correctly_unresolvable"],
            system_capability_failures=0,
            correctly_resolved_cases=counter["correct"],
            qualified_correct_cases=0,
            unsupported_rejections=0,
            correctly_rejected_unsupported_cases=0,
            partial_results=counter["partial"] + counter["unadjudicated"],
            incorrect_results=counter["incorrect"],
            packet_defects=packet_defects,
            reviewer_disagreements=counter["disagreements"],
        )
        report = metrics.as_report()
        defect_rate = packet_defects / total
        gates = {
            "semantic_correctness_at_least_95": report["semantic_correctness"] >= 0.95,
            "task_completion_at_least_95": report["task_completion_rate"] >= 0.95,
            "packet_defect_rate_below_2": defect_rate < 0.02,
            "no_critical_failures": not critical,
        }
        verdict = "PASS_HARDENED" if all(gates.values()) else "PARTIAL"
        surface_reports[surface] = {"metrics": report, "gates": gates,
                                    "verdict": verdict,
                                    "packet_defect_rate": round(defect_rate, 4)}

    result = {
        "surface_reports": surface_reports,
        "critical_failures": sorted(set(critical)),
        "completion_manifest_hash": completion.get("integrity_hash"),
        "scored_time": now_utc(),
    }
    write_json(out / "consensus_rows.json", consensus_rows)
    write_json(out / "heldout_scores.json", result)
    return result


# ---------------------------------------------------------------------------
# CLI wrappers (names fixed by cli.FORWARDED_COMMANDS)
# ---------------------------------------------------------------------------

def score_heldout(**spec: Any) -> dict[str, Any]:
    return consensus_and_score(**spec)
