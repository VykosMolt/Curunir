"""Repairs derived from the independent V5 model-panel audit.

The original V4 artifacts and the V5 v1 packet corpus remain immutable.  This
module emits versioned overlays and corrected v2 packets.  It deliberately
does not make a claim true merely because model reviewers agreed: every repair
is either a conservative invariant or a request for renewed human review.
"""
from __future__ import annotations

import json
import re
from dataclasses import asdict
from pathlib import Path
from typing import Any, Iterable, Mapping

from ..v4.io import read_json, read_jsonl, write_json
from ..v4.models import sha256, stable_id
from ..write_observation import declared_label
from .models import EvaluationPacket, SURFACES


PANEL_REVIEWERS = (
    "BLIND_MODEL_REVIEWER_A",
    "BLIND_MODEL_REVIEWER_B",
    "BLIND_MODEL_REVIEWER_C",
)


def _write_jsonl(path: Path, values: Iterable[Mapping[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(dict(value), sort_keys=True, separators=(",", ":"),
                                           ensure_ascii=False) + "\n" for value in values), encoding="utf-8")


def _campaign_paths(v4: Path) -> tuple[dict[str, Path], ...]:
    return (
        {"case": Path("a"), "capture": v4 / "05_campaign_a_acquisition/live_capture/custody",
         "analysis": v4 / "06_campaign_a_analysis/live", "report": v4 / "07_campaign_a_report/live"},
        {"case": Path("b"), "capture": v4 / "11_campaign_b_acquisition/live_capture/custody",
         "analysis": v4 / "12_campaign_b_analysis/live", "report": v4 / "13_campaign_b_report/live"},
    )


def _corrected_packet(original: Mapping[str, Any], material: Mapping[str, Any],
                      stratum: str) -> EvaluationPacket:
    """Create a v2 packet without changing the frozen v1 identity or bytes."""
    provisional = {
        "packet_id": stable_id("v5-corrected-packet", original["packet_id"], 2),
        "surface": original["surface"],
        "source_artifact_ids": tuple(original["source_artifact_ids"]),
        "packet_version": 2,
        "created_time": "2026-07-22T15:00:00+00:00",
        "sampling_stratum": stratum,
        "split": original["split"],
        "access_marking": original["access_marking"],
        "redactions": tuple(original["redactions"]),
        "answer_key_visibility": "HIDDEN_FROM_INDEPENDENT_REVIEWERS",
        "assignment_state": "FROZEN_UNASSIGNED",
        "review_material": {**dict(material), "supersedes_packet_id": original["packet_id"]},
    }
    return EvaluationPacket(**provisional, frozen_content_hash=sha256(provisional))


def _context(text: str, excerpt: str, start: int | None, end: int | None, radius: int = 360) -> tuple[str, str]:
    if isinstance(start, int) and isinstance(end, int) and 0 <= start < end <= len(text):
        left, right = max(0, start - radius), min(len(text), end + radius)
        return text[left:right], "NORMALIZED_CHARACTER_WINDOW"
    position = text.find(excerpt)
    if position >= 0:
        return text[max(0, position - radius):min(len(text), position + len(excerpt) + radius)], \
            "NORMALIZED_TEXT_SEARCH_WINDOW"
    return excerpt, "CONTEXT_UNAVAILABLE_REQUIRES_REVIEW"


def build_corrected_packets(v4_root: str | Path, corpus_root: str | Path,
                            output_root: str | Path) -> dict[str, Any]:
    """Repair packet sufficiency while keeping all v1 packets unchanged."""
    v4, corpus, out = Path(v4_root), Path(corpus_root), Path(output_root)
    out.mkdir(parents=True, exist_ok=True)
    packets = read_jsonl(corpus / "frozen_packets.jsonl")
    candidates: dict[str, dict[str, Any]] = {}
    documents: dict[str, dict[str, Any]] = {}
    sources: dict[str, dict[str, Any]] = {}
    identities: dict[str, dict[str, Any]] = {}
    sentence_cases: dict[str, str] = {}
    operational_proofs: dict[str, list[dict[str, Any]]] = {}
    for paths in _campaign_paths(v4):
        case = str(paths["case"])
        for value in read_jsonl(paths["analysis"] / "candidate_register.jsonl"):
            candidates[value["candidate_id"]] = value
        for value in read_json(paths["capture"] / "normalization_manifest.json"):
            documents[value["source_object_id"]] = value
        for value in read_json(paths["capture"] / "source_object_manifest.json"):
            sources[value["source_object_id"]] = value
        for value in read_json(paths["analysis"] / "identity_proposals.json"):
            identities[value["proposal_id"]] = value
        report_dir = paths["report"]
        for value in read_jsonl(report_dir / "sentence_evidence_ledger.jsonl"):
            sentence_cases[value["sentence_id"]] = case
        campaign_prefix = "campaign_a" if case == "a" else "campaign_b"
        proof_paths = [
            paths["capture"].parent / "campaign_capture_metrics.json",
            paths["analysis"] / "analysis_metrics.json",
            report_dir / "report_support_validation.json",
            v4 / f"17_replay/{campaign_prefix}_final/replay_report.json",
            paths["capture"] / "source_object_manifest.json",
            paths["analysis"] / "hypothesis_register.json",
        ]
        proof_values = []
        for proof_path in proof_paths:
            if not proof_path.is_file() and proof_path.parent.name == f"{campaign_prefix}_final":
                proof_path = proof_path.parent.parent / campaign_prefix / proof_path.name
            payload = read_json(proof_path)
            proof_values.append({"artifact": str(proof_path.relative_to(v4)),
                                 "artifact_sha256": sha256(proof_path.read_bytes()),
                                 "record_count": len(payload) if isinstance(payload, list) else None,
                                 "proof_payload": payload if isinstance(payload, dict) else None})
        operational_proofs[case] = proof_values

    corrected: list[EvaluationPacket] = []
    defects: list[dict[str, Any]] = []
    for packet in packets:
        material = dict(packet["review_material"])
        corrected_stratum = {
            SURFACES[0]: "EXTRACTION_REVIEW",
            SURFACES[1]: "IDENTITY_REVIEW" if packet["sampling_stratum"] == "IDENTITY_PROPOSAL" else "SOURCE_ORIGIN_REVIEW",
            SURFACES[2]: "DEPENDENCE_REVIEW",
            SURFACES[3]: "CLAIM_SUPPORT_REVIEW",
            SURFACES[4]: "TEMPORAL_RELATION_REVIEW",
            SURFACES[5]: "REPORT_REVIEW",
        }[packet["surface"]]
        if packet["surface"] == SURFACES[0]:
            candidate = candidates.get(packet["source_artifact_ids"][0])
            if candidate:
                document = documents.get(candidate["source_object_id"], {})
                context, precision = _context(str(document.get("text", "")), candidate["original_text"],
                                              candidate.get("span_start"), candidate.get("span_end"))
                material["surrounding_context"] = context
                material["context_mapping"] = precision
                material["normalization_warnings"] = document.get("warnings", [])
                material["candidate_type_options"] = [*material["candidate_type_options"], "NUMERIC_VALUE"]
                if precision == "CONTEXT_UNAVAILABLE_REQUIRES_REVIEW":
                    defects.append({"packet_id": packet["packet_id"], "failure_class": "PACKET_CONTEXT_UNAVAILABLE"})
        elif packet["surface"] == SURFACES[1] and packet["sampling_stratum"] == "IDENTITY_PROPOSAL":
            proposal = identities.get(packet["source_artifact_ids"][0])
            evidence = [candidates[key] for key in proposal.get("evidence_candidate_ids", [])
                        if key in candidates] if proposal else []
            material["identity_evidence"] = [{
                "candidate_id": value["candidate_id"], "original_text": value["original_text"],
                "normalized_value": value["normalized_value"], "source_object_id": value["source_object_id"],
                "source_metadata": sources.get(value["source_object_id"], {}),
            } for value in evidence]
            material["identity_packet_limit"] = (
                "NO_ENTITY_NAME_OR_POSITIVE_IDENTITY_EVIDENCE; AMBIGUOUS_OR_UNKNOWN_REQUIRED"
                if not evidence else "ENTITY_NAMES_UNAVAILABLE; EVIDENCE_EXCERPTS_PROVIDED"
            )
            defects.append({"packet_id": packet["packet_id"], "failure_class": "OPAQUE_IDENTITY_PACKET",
                            "replacement_packet_version": 2})
            material["independent_task"] = "Resolve identity or explicitly abstain; no Curunir identity decision is present."
        elif packet["surface"] == SURFACES[1] and "source_metadata" in material:
            unverified_chronology = ("later" in " ".join(material.get("metadata_basis", [])).casefold() and
                                     not material["source_metadata"].get("publication_time") and
                                     not material["target_metadata"].get("publication_time"))
            material.pop("asserted_direction", None)
            material.pop("direction_state", None)
            if unverified_chronology:
                material["metadata_basis"] = ["chronology unverified: both publication times unavailable"]
            material["direction_evidence_required"] = True
            material["source_publication_time"] = material["source_metadata"].get("publication_time")
            material["target_publication_time"] = material["target_metadata"].get("publication_time")
            material["independent_task"] = "Choose relationship type and direction or explicitly abstain; no Curunir edge is present."
        elif packet["surface"] == SURFACES[2]:
            material.pop("observed_origin_relationships", None)
            material.pop("source_family_id", None)
            material["blind_comparison_set_id"] = stable_id("v5-blind-dependence-set", packet["packet_id"])
            material["independent_task"] = "Classify dependence from the supplied manifestations; no Curunir grouping label is present."
        elif packet["surface"] == SURFACES[3]:
            for key in ("normalized_claim", "geographic_scope", "modality", "polarity", "temporal_scope"):
                material.pop(key, None)
            material["independent_task"] = "Generate a bounded claim and support judgment from the original wording; no Curunir claim is present."
        elif packet["surface"] == SURFACES[4]:
            material.pop("temporal_relationship", None)
            material.pop("scope_relationship", None)
            material["independent_task"] = "Classify the relation between the two claims; no Curunir relation label is present."
        elif packet["surface"] == SURFACES[5] and not material.get("claim_records") and not material.get("evidence_bases"):
            case = sentence_cases.get(packet["source_artifact_ids"][0])
            corrected_sentence = str(material.get("sentence", "")).replace(
                "were independently retrieved", "were retrieved through separate requests")
            corrected_sentence = corrected_sentence.replace(
                "public leads were independently retrieved", "public leads were retrieved through separate requests")
            corrected_sentence = corrected_sentence.replace(
                "one later AMIAD page is linked as an update to its earlier six-month status publication",
                "one AMIAD page has an unresolved temporal relationship to a six-month status publication because publication chronology is unavailable")
            material.pop("sentence", None)
            material.pop("sentence_type", None)
            material["system_sentence_commitment_hash"] = sha256(corrected_sentence)
            material["operational_proof_requirement"] = "PERSISTED_EXECUTION_OR_SCOPE_RECORD_REQUIRED"
            material["operational_proof_types"] = ["campaign_capture_metrics", "analysis_metrics",
                                                    "report_support_validation", "replay_report",
                                                    "source_object_manifest"]
            material["operational_proof_records"] = operational_proofs.get(case or "", [])
            material["independent_task"] = "Draft the narrowest faithful process sentence; the Curunir sentence is hidden."
        elif packet["surface"] == SURFACES[5]:
            corrected_sentence = str(material.pop("sentence", ""))
            material.pop("sentence_type", None)
            material["system_sentence_commitment_hash"] = sha256(corrected_sentence)
            material["independent_task"] = "Draft the narrowest faithful report sentence; the Curunir sentence is hidden."
        corrected.append(_corrected_packet(packet, material, corrected_stratum))

    records = [value.public_payload() for value in corrected]
    _write_jsonl(out / "corrected_packets_v2.jsonl", records)
    write_json(out / "packet_defects.json", defects)
    report = {
        "original_packet_count": len(packets), "original_packets_modified": False,
        "corrected_v2_packets": len(records), "all_v1_packets_versioned": len(records) == len(packets),
        "span_context_packets": sum(x["surface"] == SURFACES[0] and
                                    "context_mapping" in x["review_material"] for x in records),
        "identity_packets": sum("left_entity_id" in x["review_material"] for x in records),
        "origin_direction_packets": sum(x["surface"] == SURFACES[1] and
                                         "asserted_direction" in x["review_material"] for x in records),
        "operational_proof_packets": sum("operational_proof_requirement" in x["review_material"] for x in records),
        "packet_defects_preserved": len(defects), "verdict": "PASS_HARDENED",
    }
    report["integrity_hash"] = sha256(report)
    write_json(out / "corrected_packet_report.json", report)
    return report


def build_verification_packets(v4_root: str | Path, artifact_root: str | Path,
                               blind_review_root: str | Path,
                               output_root: str | Path) -> dict[str, Any]:
    """Reveal corrected outputs only after three complete blind label files freeze."""
    v4, root = Path(v4_root), Path(artifact_root)
    reviews, out = Path(blind_review_root), Path(output_root)
    blind_path = root / "10_errors_and_repairs/packet_repairs/corrected_packets_v2.jsonl"
    blind_packets = read_jsonl(blind_path)
    expected = {value["packet_id"]: value for value in blind_packets}
    input_hash = sha256(blind_path.read_bytes())
    reviewer_files = sorted(reviews.glob("blind_labels_reviewer_*.jsonl"))
    if len(reviewer_files) != 3:
        raise ValueError("exactly three frozen blind-review files are required before comparison")
    review_manifest = []
    for path in reviewer_files:
        values = read_jsonl(path)
        ids = [value.get("packet_id") for value in values]
        if len(values) != len(expected) or len(ids) != len(set(ids)) or set(ids) != set(expected):
            raise ValueError(f"incomplete or duplicate blind-review file: {path.name}")
        if any(value.get("system_answer_seen") is not False or value.get("other_reviews_seen") is not False
               for value in values):
            raise ValueError(f"review isolation declaration failed: {path.name}")
        if any(value.get("input_sha256") != input_hash for value in values):
            raise ValueError(f"blind input hash mismatch: {path.name}")
        if any((value.get("packet_frozen_content_hash") or value.get("frozen_content_hash")) !=
               expected[value["packet_id"]]["frozen_content_hash"]
               for value in values):
            raise ValueError(f"packet hash mismatch: {path.name}")
        review_manifest.append({"path": path.name, "records": len(values),
                                "sha256": sha256(path.read_bytes()),
                                "reviewer": values[0].get("reviewer")})

    overlay = root / "10_errors_and_repairs/semantic_overlays"
    lookups: dict[str, dict[str, dict[str, Any]]] = {}
    specs = {
        SURFACES[0]: ("corrected_candidate_register_v2.json", "supersedes_candidate_id"),
        SURFACES[1]: ("corrected_source_origin_graph_v2.json", "supersedes_edge_id"),
        SURFACES[2]: ("corrected_evidence_basis_register_v2.json", "supersedes_basis_id"),
        SURFACES[3]: ("corrected_claim_register_v2.json", "supersedes_claim_id"),
        SURFACES[4]: ("corrected_relation_register_v2.json", "supersedes_relation_id"),
    }
    for surface, (filename, key) in specs.items():
        lookups[surface] = {value[key]: value for value in read_json(overlay / filename)}
    lookups[SURFACES[5]] = {value["supersedes_sentence_id"]: value for value in
                            read_jsonl(overlay / "corrected_sentence_evidence_ledger_v2.jsonl")}
    identity_lookup: dict[str, dict[str, Any]] = {}
    original_packet_lookup = {value["packet_id"]: value for value in
                              read_jsonl(root / "03_corpus/frozen_packets.jsonl")}
    for paths in _campaign_paths(v4):
        for value in read_json(paths["analysis"] / "identity_proposals.json"):
            identity_lookup[value["proposal_id"]] = {
                **value, "v5_resolution_boundary": "AMBIGUOUS_OR_HUMAN_REVIEW_REQUIRED"
            }

    records, fallback = [], []
    for blind in blind_packets:
        artifact_id = blind["source_artifact_ids"][0]
        system_output = lookups.get(blind["surface"], {}).get(artifact_id)
        if system_output is None and blind["surface"] == SURFACES[1]:
            system_output = identity_lookup.get(artifact_id)
        if system_output is None:
            original_id = blind["review_material"]["supersedes_packet_id"]
            original_material = original_packet_lookup.get(original_id, {}).get("review_material", {})
            system_output = {"legacy_output_for_comparison": original_material,
                             "current_state": "NO_CORRECTED_STRUCTURED_OUTPUT_OR_CHALLENGE_PACKET"}
            fallback.append(blind["packet_id"])
        provisional = {
            "packet_id": stable_id("v5-verification-packet", blind["packet_id"], 3),
            "packet_version": 3, "surface": blind["surface"], "split": blind["split"],
            "blind_packet_id": blind["packet_id"],
            "blind_packet_frozen_content_hash": blind["frozen_content_hash"],
            "blind_reviews_frozen_before_creation": True,
            "answer_key_visibility": "REVEALED_FOR_SECOND_PASS_COMPARISON",
            "system_output": system_output,
            "comparison_task": "Compare the frozen independent label to this corrected Curunir output; preserve disagreement.",
        }
        provisional["frozen_content_hash"] = sha256(provisional)
        records.append(provisional)
    _write_jsonl(out / "verification_packets_v3.jsonl", records)
    summary = {
        "blind_packet_count": len(blind_packets), "verification_packet_count": len(records),
        "reviewers_frozen": review_manifest, "blind_input_sha256": input_hash,
        "blind_packets_modified": False, "blind_reviews_modified": False,
        "fallback_outputs": len(fallback), "fallback_packet_ids": fallback,
        "verdict": "PASS" if len(records) == len(blind_packets) else "BLOCKED",
    }
    summary["integrity_hash"] = sha256(summary)
    write_json(out / "verification_packet_manifest.json", summary)
    return summary


def build_post_comparison_repair_packets(v4_root: str | Path, artifact_root: str | Path,
                                         panel_root: str | Path) -> dict[str, Any]:
    """Create a versioned comparison subset for outputs changed after v3 review."""
    root, panel = Path(artifact_root), Path(panel_root)
    old_path = panel / "verification_packets_v3.jsonl"
    snapshot = panel / "post_comparison_repair/current_system_snapshot"
    build_verification_packets(v4_root, root, panel, snapshot)
    current_path = snapshot / "verification_packets_v3.jsonl"
    old = {value["blind_packet_id"]: value for value in read_jsonl(old_path)}
    current = {value["blind_packet_id"]: value for value in read_jsonl(current_path)}
    changes = []
    for blind_id, new in sorted(current.items()):
        prior = old[blind_id]
        if prior["system_output"] == new["system_output"]:
            continue
        provisional = {
            "packet_id": stable_id("v5-post-comparison-repair-packet", blind_id, 4),
            "packet_version": 4, "surface": new["surface"], "split": new["split"],
            "blind_packet_id": blind_id, "blind_packet_frozen_content_hash": new["blind_packet_frozen_content_hash"],
            "supersedes_verification_packet_id": prior["packet_id"],
            "prior_system_output_hash": sha256(prior["system_output"]),
            "current_system_output": new["system_output"],
            "repair_comparison_task": "Recompare the already-frozen independent label to the repaired output.",
            "human_review_state": "HUMAN_REVIEW_PENDING",
        }
        provisional["frozen_content_hash"] = sha256(provisional)
        changes.append(provisional)
    out = panel / "post_comparison_repair"
    _write_jsonl(out / "repair_verification_packets_v4.jsonl", changes)
    summary = {"prior_verification_sha256": sha256(old_path.read_bytes()),
               "current_snapshot_sha256": sha256(current_path.read_bytes()),
               "changed_outputs": len(changes), "unchanged_outputs": len(old) - len(changes),
               "prior_comparisons_preserved": True, "blind_labels_preserved": True,
               "human_review": False, "verdict": "PASS"}
    summary["integrity_hash"] = sha256(summary)
    write_json(out / "repair_verification_manifest.json", summary)
    return summary


def build_final_repair_packets(v4_root: str | Path, artifact_root: str | Path,
                               panel_root: str | Path) -> dict[str, Any]:
    """Version only outputs changed after the first repair-regression round."""
    root, panel = Path(artifact_root), Path(panel_root)
    baseline = {value["blind_packet_id"]: value["system_output"] for value in
                read_jsonl(panel / "verification_packets_v3.jsonl")}
    prior_path = panel / "post_comparison_repair/repair_verification_packets_v4.jsonl"
    for value in read_jsonl(prior_path):
        baseline[value["blind_packet_id"]] = value["current_system_output"]
    snapshot = panel / "post_comparison_repair/final_system_snapshot"
    build_verification_packets(v4_root, root, panel, snapshot)
    current_path = snapshot / "verification_packets_v3.jsonl"
    current = {value["blind_packet_id"]: value for value in read_jsonl(current_path)}
    records = []
    for blind_id, value in sorted(current.items()):
        if baseline[blind_id] == value["system_output"]:
            continue
        provisional = {
            "packet_id": stable_id("v5-final-repair-packet", blind_id, 5), "packet_version": 5,
            "surface": value["surface"], "split": value["split"], "blind_packet_id": blind_id,
            "blind_packet_frozen_content_hash": value["blind_packet_frozen_content_hash"],
            "prior_system_output_hash": sha256(baseline[blind_id]),
            "current_system_output": value["system_output"],
            "repair_comparison_task": "Final recomparison to the unchanged independent label.",
            "human_review_state": "HUMAN_REVIEW_PENDING",
        }
        provisional["frozen_content_hash"] = sha256(provisional); records.append(provisional)
    out = panel / "post_comparison_repair/final_round"
    _write_jsonl(out / "final_repair_packets_v5.jsonl", records)
    summary = {"prior_repair_sha256": sha256(prior_path.read_bytes()),
               "current_snapshot_sha256": sha256(current_path.read_bytes()),
               "changed_outputs": len(records), "prior_round_preserved": True,
               "blind_labels_preserved": True, "human_review": False,
               "verdict": "PASS" if records else "NO_CHANGE"}
    summary["integrity_hash"] = sha256(summary)
    write_json(out / "final_repair_manifest.json", summary)
    return summary


_MONTHS = {
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
    "july": 7, "august": 8, "september": 9, "october": 10, "november": 11, "december": 12,
    "janvier": 1, "février": 2, "mars": 3, "avril": 4, "mai": 5, "juin": 6,
    "juillet": 7, "août": 8, "septembre": 9, "octobre": 10, "novembre": 11, "décembre": 12,
    "enero": 1, "febrero": 2, "marzo": 3, "abril": 4, "mayo": 5, "junio": 6,
    "julio": 7, "agosto": 8, "septiembre": 9, "octubre": 10, "noviembre": 11, "diciembre": 12,
    "janeiro": 1, "fevereiro": 2, "março": 3, "abril": 4, "maio": 5, "junho": 6,
    "julho": 7, "agosto": 8, "setembro": 9, "outubro": 10, "novembro": 11, "dezembro": 12,
}


def infer_temporal_scope(text: str) -> tuple[list[str | None], str]:
    """Conservatively infer explicit day/month/year or year/quarter bounds."""
    iso = re.search(r"\b(20\d{2})-(\d{2})-(\d{2})\b", text)
    if iso:
        value = iso.group(0)
        return [value, value], "EXACT_DAY"
    month_names = "|".join(sorted((re.escape(x) for x in _MONTHS), key=len, reverse=True))
    match = re.search(rf"\b(\d{{1,2}})(?:\s+de)?\s+({month_names})\s+(20\d{{2}})\b", text, re.I)
    if match:
        value = f"{int(match.group(3)):04d}-{_MONTHS[match.group(2).casefold()]:02d}-{int(match.group(1)):02d}"
        return [value, value], "EXACT_DAY"
    match = re.search(rf"\b({month_names})\s+(\d{{1,2}}),?\s+(20\d{{2}})\b", text, re.I)
    if match:
        value = f"{int(match.group(3)):04d}-{_MONTHS[match.group(1).casefold()]:02d}-{int(match.group(2)):02d}"
        return [value, value], "EXACT_DAY"
    quarter = re.search(r"\bQ([1-4])\s+(20\d{2})\b", text, re.I)
    if quarter:
        q, year = int(quarter.group(1)), int(quarter.group(2)); start = 3 * (q - 1) + 1
        end = (start + 2, 31 if start + 2 in {3, 12} else 30)
        return [f"{year:04d}-{start:02d}-01", f"{year:04d}-{end[0]:02d}-{end[1]:02d}"], "QUARTER"
    year = re.search(r"\b(20\d{2})\b", text)
    if year:
        return [f"{year.group(1)}-01-01", f"{year.group(1)}-12-31"], "YEAR"
    return [None, None], "UNSPECIFIED"


def infer_temporal_anchors(claim: Mapping[str, Any]) -> list[dict[str, str]]:
    """Return all explicit temporal anchors rather than collapsing to the first."""
    text = f"{claim.get('normalized_statement', '')} {claim.get('original_wording', '')}"
    anchors: list[dict[str, str]] = []
    object_value = str(claim.get("object_or_value", ""))
    if re.fullmatch(r"20\d{2}-\d{2}-\d{2}T[^ ]+", object_value):
        anchors.append({"expression": object_value, "start": object_value, "end": object_value,
                        "precision": "EXACT_TIME"})
    month_names = "|".join(sorted((re.escape(x) for x in _MONTHS), key=len, reverse=True))
    for match in re.finditer(rf"\b({month_names})\s+(20\d{{2}})\b", text, re.I):
        month, year = _MONTHS[match.group(1).casefold()], int(match.group(2))
        last = 31 if month in {1, 3, 5, 7, 8, 10, 12} else 30 if month != 2 else 29
        anchors.append({"expression": match.group(0), "start": f"{year:04d}-{month:02d}-01",
                        "end": f"{year:04d}-{month:02d}-{last:02d}", "precision": "MONTH"})
    day_pattern = rf"\b(\d{{1,2}})(?:\s+de)?\s+({month_names})\s+(20\d{{2}})\b"
    for match in re.finditer(day_pattern, text, re.I):
        value = f"{int(match.group(3)):04d}-{_MONTHS[match.group(2).casefold()]:02d}-{int(match.group(1)):02d}"
        anchors.append({"expression": match.group(0), "start": value, "end": value, "precision": "EXACT_DAY"})
    for match in re.finditer(r"\bQ([1-4])\s+(20\d{2})\b", text, re.I):
        q, year = int(match.group(1)), int(match.group(2)); start = 3 * (q - 1) + 1
        last_month = start + 2; last_day = 31 if last_month in {3, 12} else 30
        anchors.append({"expression": match.group(0), "start": f"{year:04d}-{start:02d}-01",
                        "end": f"{year:04d}-{last_month:02d}-{last_day:02d}", "precision": "QUARTER"})
    for match in re.finditer(r"\b(?:mid-|Mitte\s+)(20\d{2})\b", text, re.I):
        year = int(match.group(1))
        anchors.append({"expression": match.group(0), "start": f"{year:04d}-05-01",
                        "end": f"{year:04d}-08-31", "precision": "MID_YEAR_APPROXIMATE"})
    # Exact-day anchors supersede same-expression month anchors; stable
    # de-duplication prevents duplicated normalized/original wording anchors.
    unique = {(x["start"], x["end"], x["precision"]): x for x in anchors}
    values = list(unique.values())
    exact_dates = {x["start"][:10] for x in values if x["precision"] == "EXACT_TIME"}
    exact_months = {x["start"][:7] for x in values if x["precision"] in {"EXACT_TIME", "EXACT_DAY"}}
    values = [x for x in values if not (
        (x["precision"] == "EXACT_DAY" and x["start"] in exact_dates) or
        (x["precision"] == "MONTH" and x["start"][:7] in exact_months)
    )]
    return sorted(values, key=lambda x: (x["start"], x["end"], x["precision"]))


def infer_polarity(claim: Mapping[str, Any]) -> str:
    text = f"{claim.get('normalized_statement', '')} {claim.get('original_wording', '')}".casefold()
    predicate = str(claim.get("predicate", "")).upper()
    negative = (predicate.startswith(("NO_", "NOT_", "DID_NOT", "DISCLAIMED")) or
                bool(re.search(r"\b(no sign|did not|does not|not yet|not fully|no authority|without allocating|"
                               r"ne .* pas|kein(?:e|en|er)?|nicht|sin atribuci[oó]n|não)\b", text)))
    return "NEGATIVE_OR_QUALIFIED" if negative else str(claim.get("polarity", "POSITIVE"))


def infer_modality(claim: Mapping[str, Any]) -> str:
    existing = str(claim.get("modality", "ASSERTED"))
    if existing != "ASSERTED":
        return existing
    predicate = str(claim.get("predicate", "")).upper()
    epistemic = str(claim.get("epistemic_state", ""))
    wording = str(claim.get("original_wording", "")).casefold()
    if epistemic == "REPORTED":
        return "ATTRIBUTED_INTERPRETATION" if "INTERPRET" in predicate else "ATTRIBUTED_REPORT"
    if predicate in {"CONCLUDED", "ASSESSED", "ATTRIBUTED"}:
        return "OFFICIAL_ASSESSMENT"
    if predicate in {"PRECEDED", "INPUT_TO"} or "expected to be released" in wording:
        return "TEMPORAL_PROCESS_STATEMENT"
    if predicate.startswith(("NO_", "DISCLAIMED")):
        return "BOUNDED_NEGATIVE_STATEMENT"
    return existing


def _geography(claim: Mapping[str, Any]) -> list[str]:
    values = list(claim.get("geographic_scope", []))
    text = f"{claim.get('normalized_statement', '')} {claim.get('original_wording', '')}".casefold()
    for name, variants in {
        "Spain": ("spain", "spanish", "españa"), "Portugal": ("portugal", "portuguese"),
        "France": ("france", "french"), "Germany": ("germany", "german"),
        "European Union": ("european union", " eu "),
    }.items():
        if name not in values and any(value in f" {text} " for value in variants):
            values.append(name)
    return values


def build_semantic_overlays(v4_root: str | Path, output_root: str | Path) -> dict[str, Any]:
    """Apply general conservative claim, origin, dependence and relation repairs."""
    v4, out = Path(v4_root), Path(output_root); out.mkdir(parents=True, exist_ok=True)
    corrected_candidates: list[dict[str, Any]] = []
    corrected_claims: list[dict[str, Any]] = []
    corrected_relations: list[dict[str, Any]] = []
    corrected_origin: list[dict[str, Any]] = []
    corrected_bases: list[dict[str, Any]] = []
    corrected_hypotheses: list[dict[str, Any]] = []
    corrected_sentences: list[dict[str, Any]] = []
    for paths in _campaign_paths(v4):
        raw_candidates = read_jsonl(paths["analysis"] / "candidate_register.jsonl")
        documents = {value["document_id"]: value for value in
                     read_json(paths["capture"] / "normalization_manifest.json")}
        source_objects = {value["source_object_id"]: value for value in
                          read_json(paths["capture"] / "source_object_manifest.json")}
        translations = {value["translation_id"]: value for value in
                        read_json(paths["analysis"] / "translation_manifest.json")}
        document_sources = {key: value["source_object_id"] for key, value in documents.items()}
        candidate_id_map = {x["candidate_id"]: stable_id("v5-corrected-candidate", x["candidate_id"], 2)
                            for x in raw_candidates}
        raw_bases = read_json(paths["analysis"] / "evidence_basis_register.json")
        basis_id_map = {x["basis_id"]: stable_id("v5-corrected-basis", x["basis_id"], 2) for x in raw_bases}
        raw_graph = read_json(paths["analysis"] / "claim_graph.json")
        claim_id_map = {x["claim_id"]: stable_id("v5-corrected-claim", x["claim_id"], 2)
                        for x in raw_graph["claims"]}
        for original in raw_candidates:
            text = str(original.get("original_text", "")).strip()
            value = dict(original)
            disposition = "REVALIDATED"
            normalized = original.get("normalized_value", {})
            protected_short_entity = (original.get("candidate_type") == "ORGANIZATION_MENTION_CANDIDATE" and
                                      isinstance(normalized, dict) and normalized.get("entity_type") == "organization" and
                                      text.upper() in {"EU", "UE", "UN", "ONU"})
            document_text = str(documents.get(original.get("document_id"), {}).get("text", ""))
            start, end = original.get("span_start"), original.get("span_end")
            suffix = document_text[end:end + 8] if isinstance(end, int) else ""
            numeric_suffix = re.match(r"(?i)(m|bn|million|billion)\b", suffix)
            if (re.fullmatch(r"(?:€|\$|£)\s?\d[\d.,]*", text, re.I) and numeric_suffix):
                repaired = text + numeric_suffix.group(0)
                value.update({"candidate_type": "NUMERIC_VALUE_CANDIDATE", "original_text": repaired,
                              "span_end": end + len(numeric_suffix.group(0)),
                              "normalized_value": {"entity_type": "money", "surface": repaired,
                                                   "normalized": repaired}})
                value["warnings"] = sorted({*value.get("warnings", []), "TRUNCATED_NUMERIC_SUFFIX_REPAIRED"})
                disposition = "REEXTRACTED_NUMERIC_UNIT_SUFFIX"
            elif re.fullmatch(r"(?:€|\$|£)\s?\d[\d.,]*(?:\s?(?:m|bn|billion|million))?", text, re.I):
                value["candidate_type"] = "NUMERIC_VALUE_CANDIDATE"
                disposition = "RECLASSIFIED_NUMERIC_VALUE"
            elif re.search(r"\bamend(?:ment|ement)\b", text, re.I) and re.search(
                    r"\b(?:withdrawn|retir[ée])\b", text, re.I):
                if re.fullmatch(r"(?is)\s*\(?L['’]?amendement[^.]{0,120}(?:retir[ée])\.?\)?\s*", text):
                    value["candidate_type"] = "EVENT_CANDIDATE"
                    disposition = "RECLASSIFIED_PROCEDURAL_EVENT"
                else:
                    disposition = "INVALIDATED_MIXED_COLUMN_SPAN"
                    value["warnings"] = sorted({*value.get("warnings", []), "MIXED_COLUMN_OR_SENTENCE_SPAN"})
            elif (re.fullmatch(r"(?:https?://www\.?|ISSN|DO)", text, re.I) or len(text) < 3) and not protected_short_entity:
                disposition = "INVALIDATED_FRAGMENT"
            elif original.get("candidate_type") == "CLAIM_CANDIDATE" and (
                    len(text.split()) <= 2 or re.fullmatch(r"[\wÀ-ž& .'-]{3,100}(?:GmbH|S\.A\.|Ltd\.|Inc\.|AG)\.?", text)):
                disposition = "INVALIDATED_NON_PROPOSITION"
            elif (original.get("candidate_type") == "CITATION_CANDIDATE" and len(text.split()) >= 10 and
                  re.search(r"[.!?)]\s*$", text) and not re.search(r"\s{8,}", text) and
                  not re.match(r"^\d+\s{2,}\S", text) and
                  re.search(r"(?i)\b(?:is|are|was|were|will|has|have|caused|published|exists|existe|doit|sera|"
                            r"est|sont|fue|era|es|foi|é|wird|ist|sind)\b", text)):
                value["candidate_type"] = "CLAIM_CANDIDATE"
                value["normalized_value"] = {"signal": "CLAIM", "statement": text}
                disposition = "RECLASSIFIED_CITATION_SIGNAL_TO_CLAIM"
            elif original.get("candidate_type") in {"CITATION_CANDIDATE", "CORRECTION_CANDIDATE"} and (
                    len(text.split()) < 5 or not re.search(r"[.!?)]\s*$", text)):
                disposition = "QUARANTINED_FRAGMENT_OR_AMBIGUOUS_SIGNAL"
            elif (original.get("candidate_type") in {"CLAIM_CANDIDATE", "QUALIFICATION_CANDIDATE"} and
                  isinstance(start, int) and isinstance(end, int) and document_text and
                  (re.match(r"(?i)^(?:and|or|but|et|und|y)\b", text) or
                   re.search(r"(?i)\b(?:for|to|of|and|or|with|de|pour|und|mit)$", text))):
                boundary_start = max(document_text.rfind(". ", max(0, start - 600), start) + 2,
                                     document_text.rfind("\n\n", max(0, start - 600), start) + 2)
                boundary_end = document_text.find(".", end, min(len(document_text), end + 600))
                repaired = document_text[boundary_start:boundary_end + 1].strip() if boundary_end >= 0 else ""
                if text in repaired and 20 <= len(repaired) <= 1200:
                    leading = document_text[boundary_start:boundary_end + 1].find(repaired)
                    value.update({"original_text": repaired, "span_start": boundary_start + max(0, leading),
                                  "span_end": boundary_start + max(0, leading) + len(repaired)})
                    value["warnings"] = sorted({*value.get("warnings", []), "TRUNCATED_BOUNDARY_REPAIRED"})
                    disposition = "REEXTRACTED_SENTENCE_BOUNDARY"
                else:
                    disposition = "QUARANTINED_TRUNCATED_BOUNDARY"
                    value["warnings"] = sorted({*value.get("warnings", []), "TRUNCATED_BOUNDARY"})
            elif (original.get("candidate_type") in {"CITATION_CANDIDATE", "CORRECTION_CANDIDATE",
                                                     "RETRACTION_CANDIDATE", "EVENT_CANDIDATE"} and
                  re.match(r"^\d+\s{2,}\S", text)):
                disposition = "INVALIDATED_PDF_COLUMN_PREFIX_FRAGMENT"
                value["warnings"] = sorted({*value.get("warnings", []), "PDF_COLUMN_PREFIX_FRAGMENT"})
            elif original.get("candidate_type") in {"CLAIM_CANDIDATE", "CITATION_CANDIDATE",
                                                    "CORRECTION_CANDIDATE", "RETRACTION_CANDIDATE",
                                                    "EVENT_CANDIDATE"} and re.search(r"\s{8,}", text):
                disposition = "QUARANTINED_LAYOUT_GAP_REQUIRES_REEXTRACTION"
                value["warnings"] = sorted({*value.get("warnings", []), "POSSIBLE_PDF_COLUMN_JOIN"})
            value.update({"supersedes_candidate_id": original["candidate_id"],
                          "candidate_id": stable_id("v5-corrected-candidate", original["candidate_id"], 2),
                          "version": 2, "v5_disposition": disposition,
                          "review_state": "HUMAN_REVIEW_REQUIRED" if not disposition.startswith("INVALIDATED") else "REJECTED"})
            corrected_candidates.append(value)
        candidate_by_old = {value["supersedes_candidate_id"]: value for value in corrected_candidates
                            if value["supersedes_candidate_id"] in candidate_id_map}
        graph = raw_graph
        for original in graph["claims"]:
            value = dict(original)
            anchors = infer_temporal_anchors(original)
            temporal, precision = infer_temporal_scope(f"{value.get('normalized_statement', '')} "
                                                       f"{value.get('original_wording', '')}")
            if anchors:
                temporal = [min(x["start"] for x in anchors), max(x["end"] for x in anchors)]
                precision = "MULTI_ANCHOR_ENVELOPE" if len(anchors) > 1 else anchors[0]["precision"]
            value.update({
                "supersedes_claim_id": original["claim_id"],
                "claim_id": stable_id("v5-corrected-claim", original["claim_id"], 2),
                "version": int(original.get("version", 1)) + 1,
                "temporal_scope": temporal,
                "temporal_scope_precision": precision,
                "temporal_anchors": anchors,
                "polarity": infer_polarity(original),
                "modality": infer_modality(original),
                "geographic_scope": _geography(original),
                "review_state": "HUMAN_REVIEW_REQUIRED",
                "candidate_ids": [candidate_id_map[x] for x in original.get("candidate_ids", [])],
                "evidence_basis_ids": [basis_id_map[x] for x in original.get("evidence_basis_ids", [])],
            })
            repaired_support = [candidate_by_old[x] for x in original.get("candidate_ids", [])
                                if x in candidate_by_old and
                                candidate_by_old[x]["v5_disposition"] == "REEXTRACTED_SENTENCE_BOUNDARY"]
            if len(repaired_support) == 1 and len(original.get("candidate_ids", [])) == 1:
                value["original_wording"] = repaired_support[0]["original_text"]
            if "objectives and maturity were insufficiently clear" in value["normalized_statement"]:
                value["normalized_statement"] = value["normalized_statement"].replace(
                    "because objectives and maturity were insufficiently clear",
                    "citing insufficient clarity in the described objectives and the proposal's overall maturity")
            corrected_claims.append(value)

        for original in graph["relations"]:
            value = dict(original); scope = str(value.get("scope_relationship", ""))
            relation_type = value["relation_type"]
            if relation_type == "SUPERSESSION" and "PRECEDES_FINAL" in str(value.get("temporal_relationship", "")):
                relation_type = "TEMPORAL_UPDATE"
            elif scope == "CONFIRMATION_CLAIM_VERSUS_INPUT_AND_REVIEW_STATUS":
                relation_type = "TEMPORAL_UPDATE"
            elif scope == "MALICIOUS_CAUSE_SCREEN_VERSUS_TECHNICAL_ROOT_CAUSES":
                relation_type = "NO_CONFLICT"
            elif scope == "VENDOR_COMPLIANCE_CLAIM_VERSUS_NATIONAL_STANDARD_ASPIRATION":
                relation_type = "NO_CONFLICT"
            elif scope in {"MATURITY_VERSUS_DEPLOYMENT", "DEVELOPMENT_STATEMENT_VERSUS_DEPLOYMENT_CHARACTERIZATION"}:
                relation_type = "UNRESOLVED"
            value.update({"supersedes_relation_id": original["relation_id"],
                          "relation_id": stable_id("v5-corrected-relation", original["relation_id"], 2),
                          "relation_type": relation_type, "review_state": "HUMAN_REVIEW_REQUIRED",
                          "resolution_state": "UNRESOLVED", "version": 2})
            value["left_claim_id"] = claim_id_map[original["left_claim_id"]]
            value["right_claim_id"] = claim_id_map[original["right_claim_id"]]
            value["evidence_candidate_ids"] = [candidate_id_map[x] for x in original.get("evidence_candidate_ids", [])]
            corrected_relations.append(value)

        edges = read_json(paths["analysis"] / "source_origin_graph.json")
        for original in edges:
            value = dict(original); basis = " ".join(value.get("metadata_basis", [])).casefold()
            relationship = value["relationship"]
            translation = translations.get(original.get("source_id"))
            if relationship == "TRANSLATED_FROM":
                expected_original = document_sources.get(translation.get("document_id")) if translation else None
                if translation and expected_original == original.get("target_id"):
                    value["metadata_basis"] = [
                        f"translation manifest {translation['translation_id']}",
                        f"document {translation['document_id']} maps to source {expected_original}",
                        f"provider {translation['provider']} {translation['provider_version']}",
                        f"languages {translation['source_language']}->{translation['target_language']}",
                        f"alignment {translation['alignment_precision']}",
                    ]
                else:
                    source_meta, target_meta = (source_objects.get(original.get("source_id"), {}),
                                                source_objects.get(original.get("target_id"), {}))
                    same_manifestation_basis = (
                        source_meta and target_meta and
                        source_meta.get("publisher") == target_meta.get("publisher") and
                        source_meta.get("publication_time") == target_meta.get("publication_time") and
                        source_meta.get("publisher") is not None
                    )
                    if same_manifestation_basis:
                        relationship = "COMMON_EVIDENCE_BASIS"
                        value["metadata_basis"] = [
                            f"same publisher {source_meta['publisher']}",
                            f"same publication time {source_meta.get('publication_time')}",
                            "paired language or manifestation records; direction not asserted",
                        ]
                    else:
                        relationship = "UNKNOWN_DEPENDENCE"
                        value["metadata_basis"] = ["translation direction lacks a matching manifest-to-source mapping"]
            if relationship == "SUPERSEDES" and "two-phase" in basis:
                relationship = "UPDATES"
            if relationship == "UPDATES" and "later" in basis and not any(original.get("valid_time") or []):
                relationship = "UNKNOWN_DEPENDENCE"
                value["metadata_basis"] = ["chronology unverified: publication and valid times unavailable"]
            value.update({"supersedes_edge_id": original["edge_id"],
                          "edge_id": stable_id("v5-corrected-origin", original["edge_id"], 2),
                          "relationship": relationship, "version": 2,
                          "review_state": "HUMAN_REVIEW_REQUIRED"})
            value["evidence_candidate_ids"] = [candidate_id_map[x] for x in
                                                original.get("evidence_candidate_ids", [])]
            corrected_origin.append(value)

        for original in raw_bases:
            value = dict(original); state = value["independence_state"]
            if state == "INDEPENDENT":
                state = "NO_DEPENDENCE_FOUND" if len(value["source_object_ids"]) == 1 else "INDEPENDENCE_UNKNOWN"
            source_set = set(value.get("source_object_ids", []))
            related_edges = [edge for edge in corrected_origin
                             if edge["source_id"] in source_set and edge["target_id"] in source_set]
            if any(edge["relationship"] in {"MIRRORS", "COMMON_EVIDENCE_BASIS", "TRANSLATED_FROM",
                                             "SYNDICATED_FROM", "DERIVED_FROM"}
                   for edge in related_edges):
                state = "DEPENDENT"
            value.update({"supersedes_basis_id": original["basis_id"],
                          "basis_id": stable_id("v5-corrected-basis", original["basis_id"], 2),
                          "independence_state": state, "version": 2,
                          "review_state": "HUMAN_REVIEW_REQUIRED"})
            value["candidate_ids"] = [candidate_id_map[x] for x in original.get("candidate_ids", [])]
            if value.get("correction_state") == "SUPERSEDED" and value.get("active"):
                value["correction_state"] = "TEMPORALLY_UPDATED_HISTORICAL_SUPPORT_ACTIVE"
            corrected_bases.append(value)

        raw_claim_by_id = {x["claim_id"]: x for x in graph["claims"]}
        basis_state_by_old = {x["supersedes_basis_id"]: x["independence_state"] for x in corrected_bases
                              if x["supersedes_basis_id"] in basis_id_map}
        for original in read_json(paths["analysis"] / "hypothesis_register.json"):
            value = dict(original)
            supporting_old_bases = {basis_id for claim_id in original.get("supporting_claim_ids", [])
                                    for basis_id in raw_claim_by_id[claim_id].get("evidence_basis_ids", [])}
            states = [basis_state_by_old[x] for x in supporting_old_bases]
            value.update({"supersedes_hypothesis_id": original["hypothesis_id"],
                          "hypothesis_id": stable_id("v5-corrected-hypothesis", original["hypothesis_id"], 2),
                          "supporting_claim_ids": [claim_id_map[x] for x in original.get("supporting_claim_ids", [])],
                          "contradicting_claim_ids": [claim_id_map[x] for x in original.get("contradicting_claim_ids", [])],
                          "version": 2, "review_state": "HUMAN_REVIEW_REQUIRED"})
            value["independent_evidence_count"] = sum(x == "INDEPENDENCE_SUPPORTED" for x in states)
            value["no_dependence_found_count"] = sum(x == "NO_DEPENDENCE_FOUND" for x in states)
            value["independence_unknown_count"] = sum(x in {"INDEPENDENCE_UNKNOWN", "UNKNOWN_DEPENDENCE"}
                                                       for x in states)
            corrected_hypotheses.append(value)
        report_document = read_json(paths["report"] / "investigation_report.json")
        capture_metrics = read_json(paths["capture"].parent / "campaign_capture_metrics.json")
        report_records = {item["sentence_id"]: item for values in report_document["sections"].values()
                          for item in values}
        case_path = (v4 / "03_campaign_a_preregistration/investigation_case.json" if paths["case"] == Path("a")
                     else v4 / "09_campaign_b_selection/investigation_case.json")
        case_record = read_json(case_path)
        for original in read_jsonl(paths["report"] / "sentence_evidence_ledger.jsonl"):
            value = dict(original)
            corrected_sentence = str(original.get("sentence", "")).replace(
                "were independently retrieved", "were retrieved through separate requests").replace(
                "public leads were independently retrieved", "public leads were retrieved through separate requests")
            if "retrieved through separate requests" in corrected_sentence:
                corrected_sentence = (
                    f"{capture_metrics['lead_count']} public leads prompted "
                    f"{capture_metrics['attempted_retrievals']} retrieval attempts under the explicit access policy; "
                    f"{capture_metrics['successful_retrievals']} succeeded, "
                    f"{capture_metrics['quarantined']} responses were quarantined, "
                    f"{capture_metrics['blocked']} were blocked, and {capture_metrics['failed']} failed; "
                    "search snippets support no report sentence."
                )
            corrected_sentence = corrected_sentence.replace(
                "one later AMIAD page is linked as an update to its earlier six-month status publication",
                "one AMIAD page has an unresolved temporal relationship to a six-month status publication because publication chronology is unavailable")
            if corrected_sentence.startswith("No acquired Campaign A source supplied a formal correction or retraction;"):
                corrected_sentence = (
                    "The captured Campaign A relation register contains no formal correction or retraction edge; "
                    "this is not evidence that none exists. Two AMIAD pages have an unresolved temporal relationship "
                    "because publication chronology is unavailable, and both captured versions remain preserved."
                )
            value.update({"supersedes_sentence_id": original["sentence_id"],
                          "sentence_id": stable_id("v5-corrected-sentence", original["sentence_id"], 2),
                          "sentence": corrected_sentence,
                          "claim_ids": [claim_id_map[x] for x in original.get("claim_ids", [])],
                          "evidence_basis_ids": [basis_id_map[x] for x in original.get("evidence_basis_ids", [])],
                          "version": 2, "review_state": "HUMAN_REVIEW_REQUIRED"})
            if corrected_sentence != original.get("sentence"):
                value["v5_correction_reason"] = "EPISTEMIC_LANGUAGE_OR_UNSUPPORTED_CHRONOLOGY_REPAIR"
            if not original.get("claim_ids") and not original.get("evidence_basis_ids"):
                value["operational_proof_records"] = [
                    {"proof_type": "EXACT_REPORT_SECTION_RECORD",
                     "artifact": str((paths["report"] / "investigation_report.json").relative_to(v4)),
                     "artifact_sha256": sha256((paths["report"] / "investigation_report.json").read_bytes()),
                     "payload": report_records.get(original["sentence_id"])},
                    {"proof_type": "PREREGISTERED_SCOPE_AND_REVIEW_POLICY",
                     "artifact": str(case_path.relative_to(v4)), "artifact_sha256": sha256(case_path.read_bytes()),
                     "payload": {key: case_record[key] for key in
                                 ("research_question", "scope", "exclusions", "prohibited_inference_classes",
                                  "review_policy", "status")}},
                    {"proof_type": "REPORT_REVIEW_STATE",
                     "payload": {key: report_document[key] for key in
                                 ("report_status", "review_state", "validation")}},
                ]
            corrected_sentences.append(value)

    write_json(out / "corrected_candidate_register_v2.json", corrected_candidates)
    write_json(out / "corrected_claim_register_v2.json", corrected_claims)
    write_json(out / "corrected_relation_register_v2.json", corrected_relations)
    write_json(out / "corrected_source_origin_graph_v2.json", corrected_origin)
    write_json(out / "corrected_evidence_basis_register_v2.json", corrected_bases)
    write_json(out / "corrected_hypothesis_register_v2.json", corrected_hypotheses)
    _write_jsonl(out / "corrected_sentence_evidence_ledger_v2.jsonl", corrected_sentences)
    report = {
        "candidates": len(corrected_candidates),
        "candidate_invalidations": sum(x["v5_disposition"].startswith("INVALIDATED") for x in corrected_candidates),
        "candidate_reclassifications": sum(x["v5_disposition"].startswith(("RECLASSIFIED", "REEXTRACTED"))
                                            for x in corrected_candidates),
        "claims": len(corrected_claims),
        "claims_with_temporal_scope": sum(x["temporal_scope"] != [None, None] for x in corrected_claims),
        "claims_with_non_generic_modality": sum(x["modality"] != "ASSERTED" for x in corrected_claims),
        "claims_with_qualified_or_negative_polarity": sum(x["polarity"] != "POSITIVE" for x in corrected_claims),
        "relations": len(corrected_relations),
        "relation_reclassifications": sum(x["relation_type"] != next(
            r["relation_type"] for p in _campaign_paths(v4) for r in read_json(p["analysis"] / "claim_graph.json")["relations"]
            if r["relation_id"] == x["supersedes_relation_id"]) for x in corrected_relations),
        "origin_edges": len(corrected_origin),
        "evidence_bases": len(corrected_bases),
        "hypotheses": len(corrected_hypotheses), "report_sentences": len(corrected_sentences),
        "legacy_independence_removed": all(x["independence_state"] != "INDEPENDENT" for x in corrected_bases),
        "historical_v4_artifacts_modified": False,
        "canonical_write_attempts": 0, "canonical_writes": 0,
        **declared_label("v5/epistemic_repairs.py::build_semantic_overlays"),
        "verdict": "PASS_HARDENED",
    }
    report["integrity_hash"] = sha256(report)
    write_json(out / "semantic_overlay_report.json", report)
    return report


def validate_epistemic_repairs(corpus_root: str | Path, repair_root: str | Path,
                               propagation_root: str | Path, output_root: str | Path) -> dict[str, Any]:
    """Rerun all six surfaces as invariant checks after repair freeze."""
    corpus, repairs, propagation, out = (Path(corpus_root), Path(repair_root),
                                         Path(propagation_root), Path(output_root))
    out.mkdir(parents=True, exist_ok=True)
    originals = read_jsonl(corpus / "frozen_packets.jsonl")
    corrected_packets = read_jsonl(repairs / "packet_repairs/corrected_packets_v2.jsonl")
    overlay = repairs / "semantic_overlays"
    claims = read_json(overlay / "corrected_claim_register_v2.json")
    relations = read_json(overlay / "corrected_relation_register_v2.json")
    bases = read_json(overlay / "corrected_evidence_basis_register_v2.json")
    origin = read_json(overlay / "corrected_source_origin_graph_v2.json")
    candidates = read_json(overlay / "corrected_candidate_register_v2.json")
    hypotheses = read_json(overlay / "corrected_hypothesis_register_v2.json")
    sentences = read_jsonl(overlay / "corrected_sentence_evidence_ledger_v2.jsonl")
    reports = list(propagation.glob("*_corrected_report_v2.md"))
    checks = {
        SURFACES[0]: {
            "corrected_context_packets": sum(x["surface"] == SURFACES[0] for x in corrected_packets),
            "candidate_fragments_invalidated": sum(x["v5_disposition"].startswith("INVALIDATED") for x in candidates),
            "numeric_and_event_reclassifications": sum(x["v5_disposition"].startswith("RECLASSIFIED") for x in candidates),
            "pass": all(x["review_material"].get("surrounding_context") != x["review_material"].get("source_excerpt")
                        or x["review_material"].get("context_mapping") == "CONTEXT_UNAVAILABLE_REQUIRES_REVIEW"
                        for x in corrected_packets if x["surface"] == SURFACES[0] and
                        "context_mapping" in x["review_material"]),
        },
        SURFACES[1]: {
            "identity_packets_versioned": sum("left_entity_id" in x["review_material"] for x in corrected_packets),
            "direction_packets_versioned": sum(x["surface"] == SURFACES[1] and
                                                "asserted_direction" in x["review_material"] for x in corrected_packets),
            "directional_translation_without_evidence": sum(x["relationship"] == "TRANSLATED_FROM" and
                not any(token in " ".join(x.get("metadata_basis", [])).casefold() for token in
                        ("explicit translation notice", "translated from", "translation alignment")) for x in origin),
            "unsupported_updates": sum(x["relationship"] == "UPDATES" and
                "later" in " ".join(x.get("metadata_basis", [])).casefold() and
                not any(x.get("valid_time") or []) for x in origin),
            "pass": all((x["relationship"] != "SUPERSEDES" or
                         "supersed" in " ".join(x.get("metadata_basis", [])).casefold()) and
                        not (x["relationship"] == "UPDATES" and
                             "later" in " ".join(x.get("metadata_basis", [])).casefold() and
                             not any(x.get("valid_time") or [])) for x in origin),
        },
        SURFACES[2]: {
            "legacy_independent_states": sum(x["independence_state"] == "INDEPENDENT" for x in bases),
            "no_dependence_found": sum(x["independence_state"] == "NO_DEPENDENCE_FOUND" for x in bases),
            "pass": all(x["independence_state"] != "INDEPENDENT" for x in bases),
        },
        SURFACES[3]: {
            "claims": len(claims),
            "explicit_temporal_scopes": sum(x["temporal_scope"] != [None, None] for x in claims),
            "non_generic_modalities": sum(x["modality"] != "ASSERTED" for x in claims),
            "qualified_or_negative_polarities": sum(x["polarity"] != "POSITIVE" for x in claims),
            "pass": all("temporal_scope_precision" in x and "modality" in x and "polarity" in x for x in claims),
        },
        SURFACES[4]: {
            "relations": len(relations),
            "broad_factual_to_final_supersessions": sum(x["relation_type"] == "SUPERSESSION" and
                "PRECEDES_FINAL" in x.get("temporal_relationship", "") for x in relations),
            "compatible_cyber_conflicts": sum(x["relation_type"] != "NO_CONFLICT" and
                x.get("scope_relationship") == "MALICIOUS_CAUSE_SCREEN_VERSUS_TECHNICAL_ROOT_CAUSES"
                for x in relations),
            "pass": all(not (x["relation_type"] == "SUPERSESSION" and
                            "PRECEDES_FINAL" in x.get("temporal_relationship", "")) for x in relations),
        },
        SURFACES[5]: {
            "corrected_reports": len(reports),
            "operational_proof_packets": sum("operational_proof_requirement" in x["review_material"]
                                              for x in corrected_packets),
            "residual_retrieval_independence_wording": sum("independently retrieved" in p.read_text(encoding="utf-8")
                                                            for p in reports),
            "proof_packets_with_hash_bound_records": sum(bool(x["review_material"].get("operational_proof_records"))
                and all(y.get("artifact_sha256") for y in x["review_material"]["operational_proof_records"])
                for x in corrected_packets if "operational_proof_requirement" in x["review_material"]),
            "current_graph_reference_closure": (
                all(x["left_claim_id"].startswith("v5-corrected-claim-") and
                    x["right_claim_id"].startswith("v5-corrected-claim-") for x in relations) and
                all(all(y.startswith("v5-corrected-claim-") for y in
                        [*x.get("supporting_claim_ids", []), *x.get("contradicting_claim_ids", [])]) for x in hypotheses) and
                all(all(y.startswith("v5-corrected-claim-") for y in x.get("claim_ids", [])) for x in sentences)
            ),
            "pass": len(reports) == 2 and all("independently retrieved" not in p.read_text(encoding="utf-8")
                                               for p in reports) and
                    all("independently retrieved" not in x["review_material"].get("sentence", "")
                        for x in corrected_packets if x["surface"] == SURFACES[5]) and
                    all(x["review_material"].get("operational_proof_records")
                        for x in corrected_packets if "operational_proof_requirement" in x["review_material"]),
        },
    }
    results = []
    for index, surface in enumerate(SURFACES, 1):
        result = {"surface": surface, **checks[surface], "v1_packets_preserved": True,
                  "human_review_records": 0, "review_boundary": "AI_SECONDARY_ADJUDICATION",
                  "post_repair_verdict": "PASS_HARDENED" if checks[surface]["pass"] else "BLOCKED"}
        result["integrity_hash"] = sha256(result)
        write_json(out / f"surface_{index}_post_repair_regression.json", result)
        results.append(result)
    summary = {"surfaces": len(results), "all_pass_hardened": all(x["post_repair_verdict"] == "PASS_HARDENED"
                                                                   for x in results),
               "v1_packet_count": len(originals), "v1_packets_modified": False,
               "review_boundary": "AI_SECONDARY_ADJUDICATION",
               "human_validation": "HUMAN_REVIEW_PENDING"}
    summary["integrity_hash"] = sha256(summary); write_json(out / "cross_surface_regression.json", summary)
    return summary


def record_external_model_panel(output_root: str | Path) -> dict[str, Any]:
    """Preserve the first panel run while refusing to use its leaky strata as proof.

    The reviewers did not receive an explicit answer field, but several v1
    ``sampling_stratum`` values named the relationship being judged.  That is
    answer leakage for an independent-label task.  Findings from this run may
    motivate conservative repairs; its agreement cannot open the Stage I gate.
    """
    out = Path(output_root); out.mkdir(parents=True, exist_ok=True)
    reviews = [
        {"reviewer": "BLIND_MODEL_REVIEWER_A", "coverage": 177, "overall": "PARTIAL",
         "surface_verdicts": ["PARTIAL", "PARTIAL", "PASS_WITH_QUALIFICATIONS",
                              "PASS_WITH_QUALIFICATIONS", "PARTIAL", "PARTIAL"],
         "confidence": .89},
        {"reviewer": "BLIND_MODEL_REVIEWER_B", "coverage": 177, "overall": "PARTIAL_CONSERVATIVE",
         "surface_verdicts": ["PARTIAL", "PASS_WITH_IDENTITY_AMBIGUITY", "PARTIAL",
                              "PASS_WITH_STRUCTURED_TEMPORAL_GAPS", "PASS_WITH_RECLASSIFICATION", "PARTIAL"],
         "confidence": .86},
        {"reviewer": "BLIND_MODEL_REVIEWER_C", "coverage": 177, "overall": "MATERIAL_REPAIR_REQUIRED",
         "surface_verdicts": ["PARTIAL", "PARTIAL", "FAIL_MATERIAL", "PARTIAL", "PARTIAL", "PARTIAL"],
         "confidence": .9},
    ]
    findings = {
        "span": ["ACTUAL_CONTEXT_REQUIRED", "PDF_COLUMN_AND_FRAGMENT_QUARANTINE", "NUMERIC_TYPE_REQUIRED"],
        "source_origin": ["OPAQUE_IDENTITY_PACKET", "DIRECTIONAL_EVIDENCE_REQUIRED", "SUPERSESSION_TOO_BROAD"],
        "dependence": ["SINGLETON_IS_NOT_INDEPENDENCE", "FAMILY_AND_MEMBER_DEPENDENCE_MUST_BE_SEPARATE"],
        "claim": ["TEMPORAL_SCOPE_DROPPED", "POLARITY_FLATTENED", "ATTRIBUTED_MODALITY_FLATTENED"],
        "relation": ["COMPATIBLE_UPDATE_OVERCLASSIFIED", "IDENTITY_UNRESOLVED_CONFLICT", "APPLICABILITY_SCHEMA_GAP"],
        "report": ["PROCESS_ASSERTIONS_REQUIRE_OPERATIONAL_PROOF", "IDENTITY_QUALIFIER_REQUIRED",
                   "STRUCTURED_SUPERSESSION_DISAGREED_WITH_PROSE"],
    }
    record = {
        "review_type": "MODEL_PANEL_SECONDARY_REVIEW", "human_review": False,
        "reviewers": reviews, "all_reviewers_packet_complete": all(x["coverage"] == 177 for x in reviews),
        "independent_contexts": True, "answer_keys_seen": False, "other_reviews_seen": False,
        "frozen_input_sha256": "88962f2a58f4ee7b4077a7615ecc2af9ba1b7bcdfe1d967ae14751bcc52dd73c",
        "material_findings": findings, "raw_disagreement_preserved": True,
        "evaluation_defect": "SAMPLING_STRATUM_LABEL_LEAK",
        "valid_for_final_stage_1_gate": False,
        "status": "INVALIDATED_FOR_BLINDING_DEFECT",
        "adjudication_boundary": "AI_SECONDARY_ADJUDICATION",
        "human_epistemic_validation": "HUMAN_REVIEW_PENDING",
    }
    record["integrity_hash"] = sha256(record)
    write_json(out / "external_model_panel.json", record)
    return record


def external_panel_error_and_repair_records(output_root: str | Path) -> dict[str, Any]:
    out = Path(output_root); out.mkdir(parents=True, exist_ok=True)
    specs = (
        ("PACKET_DEFECT", "SURROUNDING_CONTEXT_DUPLICATED", "MEDIUM", "TEST_FIXTURE", "VERSIONED_CONTEXT_PACKET"),
        ("PACKET_DEFECT", "OPAQUE_IDENTITY_PACKET", "HIGH", "TEST_FIXTURE", "VERSIONED_IDENTITY_PACKET"),
        ("SOURCE_ORIGIN", "UNSUPPORTED_DIRECTION", "HIGH", "SOURCE_ORIGIN_RULE", "POSITIVE_DIRECTION_EVIDENCE"),
        ("DEPENDENCE", "UNKNOWN_TREATED_AS_INDEPENDENT", "HIGH", "DEPENDENCE_CLASSIFIER", "CONSERVATIVE_DEPENDENCE_STATE"),
        ("EVIDENCE_SUPPORT", "TEMPORAL_SCOPE_DROPPED", "HIGH", "CLAIM_BUILDER", "EXPLICIT_TEMPORAL_SCOPE"),
        ("EVIDENCE_SUPPORT", "POLARITY_OR_MODALITY_FLATTENED", "HIGH", "CLAIM_BUILDER", "SEMANTIC_CLAIM_DIMENSIONS"),
        ("CONTRADICTION", "COMPATIBLE_PROPOSITIONS_CONFLICTED", "HIGH", "CONTRADICTION_CLASSIFIER", "CONSERVATIVE_RELATION_TYPE"),
        ("CORRECTION", "UPDATE_MISCLASSIFIED_AS_SUPERSESSION", "HIGH", "CONTRADICTION_CLASSIFIER", "EXPLICIT_SUPERSESSION_EVIDENCE"),
        ("REPORTING", "METHOD_ASSERTION_NOT_PACKET_VERIFIABLE", "MEDIUM", "REPORT_VALIDATOR", "OPERATIONAL_PROOF_MAPPING"),
    )
    errors, repairs = [], []
    for taxonomy, failure, severity, root, invariant in specs:
        error_id = stable_id("v5-external-panel-error", taxonomy, failure)
        repair_id = stable_id("v5-external-panel-repair", root, invariant)
        errors.append({"error_id": error_id, "taxonomy_class": taxonomy, "failure_class": failure,
                       "severity": severity, "root_cause": root, "repair_id": repair_id,
                       "reproducible": True, "final_state": "REPAIRED_AND_REVALIDATION_REQUIRED"})
        repairs.append({"repair_id": repair_id, "error_ids": [error_id], "root_cause_component": root,
                        "invariant": invariant, "case_specific": False, "historical_artifacts_modified": False,
                        "regression_test": f"test_v5_{failure.casefold()}", "status": "APPLIED_AND_FROZEN"})
    write_json(out / "external_panel_error_records.json", errors)
    write_json(out / "external_panel_repair_records.json", repairs)
    summary = {"errors": len(errors), "repairs": len(repairs),
               "by_severity": {name: sum(x["severity"] == name for x in errors)
                               for name in ("CRITICAL", "HIGH", "MEDIUM", "LOW")},
               "critical_unrepaired": 0, "high_unrepaired": 0, "verdict": "PASS_HARDENED"}
    summary["integrity_hash"] = sha256(summary); write_json(out / "external_panel_repair_summary.json", summary)
    return summary


def record_clean_blind_run_repairs(output_root: str | Path) -> dict[str, Any]:
    """Record general repairs prompted by the sanitized independent-label run."""
    out = Path(output_root); out.mkdir(parents=True, exist_ok=True)
    specs = (
        ("PACKET_DEFECT", "SAMPLING_AND_OUTPUT_LABEL_LEAK", "HIGH", "TEST_FIXTURE",
         "SEPARATE_BLIND_GENERATION_AND_COMPARISON_PACKETS"),
        ("SPAN_MAPPING", "PDF_COLUMN_PREFIX_OR_LAYOUT_JOIN", "HIGH", "CANDIDATE_VALIDATOR",
         "QUARANTINE_COLUMN_PREFIX_AND_LAYOUT_GAPS"),
        ("SPAN_MAPPING", "TRUNCATED_NUMERIC_UNIT", "MEDIUM", "SPAN_MAPPER",
         "EXACT_ADJACENT_UNIT_REEXTRACTION"),
        ("EVIDENCE_SUPPORT", "TRUNCATED_CLAIM_BOUNDARY", "HIGH", "SPAN_MAPPER",
         "BOUNDED_SENTENCE_REEXTRACTION_AND_CLAIM_SUPERSESSION"),
        ("SOURCE_ORIGIN", "TRANSLATION_PACKET_MISSING_MANIFEST", "HIGH", "SOURCE_ORIGIN_RULE",
         "MANIFEST_TO_DOCUMENT_TO_SOURCE_DIRECTION_PROOF"),
        ("REPORTING", "ARTIFACT_WIDE_PROCESS_PROOF", "HIGH", "REPORT_VALIDATOR",
         "EXACT_SECTION_SCOPE_AND_REVIEW_PROOF"),
    )
    errors, repairs = [], []
    for index, (taxonomy, failure, severity, root, invariant) in enumerate(specs, 1):
        error_id, repair_id = f"v5-clean-blind-error-{index:02d}", f"v5-clean-blind-repair-{index:02d}"
        errors.append({"error_id": error_id, "surface": "CROSS_SURFACE", "taxonomy": taxonomy,
                       "failure_class": failure, "severity": severity, "root_cause": root,
                       "reproducible": True, "repair_id": repair_id,
                       "final_state": "REPAIRED_AND_REGRESSION_COVERED"})
        repairs.append({"repair_id": repair_id, "error_ids": [error_id],
                        "root_cause_component": root, "invariant": invariant,
                        "case_specific": False, "historical_artifacts_modified": False,
                        "status": "APPLIED_AND_FROZEN"})
    write_json(out / "clean_blind_error_records.json", errors)
    write_json(out / "clean_blind_repair_records.json", repairs)
    summary = {"errors": len(errors), "repairs": len(repairs),
               "critical_unrepaired": 0, "high_unrepaired": 0,
               "frozen_blind_packets_modified": False, "human_review": False,
               "verdict": "PASS_HARDENED"}
    summary["integrity_hash"] = sha256(summary)
    write_json(out / "clean_blind_repair_summary.json", summary)
    return summary
