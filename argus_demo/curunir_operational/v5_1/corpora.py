"""Repair-development, regression, and repair-verification corpora (Section 19).

All three corpora draw ONLY on pre-freeze V4/V5 material.  The repair-
development corpus is label-open; the regression corpus protects previously
correct behavior; the verification corpus checks known failure classes and is
never described as clean generalization evidence.
"""
from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Any, Iterable, Mapping

from ..v4.io import read_json, read_jsonl, write_json
from .models import now_utc, sha256, stable_id

CORPUS_KINDS = (
    "V5_1_REPAIR_DEVELOPMENT_CORPUS",
    "V5_1_REGRESSION_CORPUS",
    "V5_1_REPAIR_VERIFICATION_CORPUS",
)


def _write_jsonl(path: Path, records: Iterable[Mapping[str, Any]]) -> None:
    import json
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(
        json.dumps(dict(record), sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n"
        for record in records), encoding="utf-8")


def partition_v5_material(*, corpus_root: str | Path, consensus_path: str | Path,
                          error_records_paths: Iterable[str | Path],
                          output_root: str | Path,
                          corrected_packets_path: str | Path | None = None) -> dict[str, Any]:
    """Partition the frozen V5 corpus into the three Section 19 corpora.

    Membership is decided ONLY by consensus decision class and error linkage,
    never by packet content — no case-specific selection.  Consensus rows are
    keyed by corrected (v2) packet ids; pass ``corrected_packets_path`` so the
    rows resolve, with the frozen v1 corpus as fallback.
    """
    corpus = Path(corpus_root)
    out = Path(output_root)
    packets = {item["packet_id"]: item for item in read_jsonl(corpus / "frozen_packets.jsonl")}
    if corrected_packets_path is not None:
        for item in read_jsonl(Path(corrected_packets_path)):
            packets[item["packet_id"]] = item
    consensus = read_json(Path(consensus_path))
    errors: list[dict[str, Any]] = []
    for path in error_records_paths:
        errors.extend(read_json(Path(path)))
    error_packet_ids = {item.get("packet_id") for item in errors if item.get("packet_id")}

    development: list[dict[str, Any]] = []
    regression: list[dict[str, Any]] = []
    verification: list[dict[str, Any]] = []
    unmatched_consensus = 0

    for row in consensus:
        blind_id = row.get("blind_packet_id")
        counts = row.get("decision_counts", {})
        packet = packets.get(blind_id)
        membership_reason = None
        if packet is None:
            unmatched_consensus += 1
            continue
        has_defect = counts.get("PACKET_DEFECT", 0) > 0
        has_incorrect = counts.get("INCORRECT", 0) > 0
        has_partial = counts.get("PARTIALLY_CORRECT", 0) > 0
        unanimous_correct = counts == {"CORRECT": 3}
        record = {
            "packet_id": blind_id, "surface": row.get("surface"),
            "decision_counts": counts, "consensus_state": row.get("consensus_state"),
            "linked_error_records": sorted(
                item.get("error_id", "") for item in errors
                if item.get("packet_id") == blind_id),
            "packet": packet,
        }
        if has_defect or has_incorrect or has_partial or blind_id in error_packet_ids:
            membership_reason = ("PACKET_DEFECT" if has_defect else
                                 "INCORRECT" if has_incorrect else
                                 "PARTIAL" if has_partial else "CONFIRMED_ERROR_LINK")
            development.append({**record, "membership_reason": membership_reason})
            verification.append({
                **record,
                "membership_reason": membership_reason,
                "verification_question": (
                    "Does the repaired general mechanism now produce a correct, "
                    "complete, or correctly-unresolvable result for this case?"),
            })
        if unanimous_correct:
            regression.append({**record, "membership_reason": "PREVIOUSLY_CORRECT"})

    out.mkdir(parents=True, exist_ok=True)
    development_path = out / "11_repair_development/repair_development_corpus.jsonl"
    regression_path = out / "12_regression/regression_corpus.jsonl"
    verification_path = out / "13_repair_verification/repair_verification_corpus.jsonl"
    _write_jsonl(development_path, development)
    _write_jsonl(regression_path, regression)
    _write_jsonl(verification_path, verification)

    manifest = {
        "created_time": now_utc(),
        "labels_visible_in_development": True,
        "clean_generalization_evidence": False,
        "source_material": "PRE_FREEZE_V4_V5_ONLY",
        "counts": {
            "V5_1_REPAIR_DEVELOPMENT_CORPUS": len(development),
            "V5_1_REGRESSION_CORPUS": len(regression),
            "V5_1_REPAIR_VERIFICATION_CORPUS": len(verification),
        },
        "development_by_reason": dict(Counter(
            item["membership_reason"] for item in development)),
        "development_by_surface": dict(Counter(
            item["surface"] for item in development)),
        "regression_by_surface": dict(Counter(
            item["surface"] for item in regression)),
        "unmatched_consensus_rows": unmatched_consensus,
        "hashes": {
            "repair_development_corpus": sha256(development),
            "regression_corpus": sha256(regression),
            "repair_verification_corpus": sha256(verification),
        },
    }
    manifest["integrity_hash"] = sha256(manifest)
    write_json(out / "11_repair_development/corpora_manifest.json", manifest)
    return manifest
