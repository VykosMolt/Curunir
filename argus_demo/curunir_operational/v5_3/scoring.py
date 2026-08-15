"""Scoring against frozen production predictions (contract Sections 5.4, 18).

Evaluation code, on the refused-producer list.  Every figure it emits comes
from ``provenance.compare``, which cannot produce a CORRECT or INCORRECT
outcome without a frozen production prediction, so a V5.2-style builder-label
result is unreachable from here.

Scoring runs only after the completion manifest is frozen; the entry point
refuses otherwise.

Research shadow only.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from ..v4.io import write_json
from ..v5_1.models import now_utc, sha256
from .provenance import (
    NON_LABEL_DECISIONS, ProductionPredictionRecord, ScoringComparisonRecord,
    compare, load_prediction_manifest, provenance_audit,
)


def consensus(reviews: Mapping[str, Sequence[Mapping[str, Any]]]
              ) -> list[dict[str, Any]]:
    """Majority over three independent passes."""
    by_unit: dict[str, list[Mapping[str, Any]]] = {}
    for rows in reviews.values():
        for row in rows:
            by_unit.setdefault(str(row.get("dossier_id")), []).append(row)
    output = []
    for dossier_id, rows in sorted(by_unit.items()):
        votes: dict[str, int] = {}
        for row in rows:
            decision = str(row.get("decision") or "")
            label = str(row.get("label") or "") if decision == "LABELED" else decision
            votes[label] = votes.get(label, 0) + 1
        top = max(votes.values())
        winners = sorted(name for name, count in votes.items() if count == top)
        output.append({
            "dossier_id": dossier_id,
            "surface": str(rows[0].get("surface") or ""),
            "votes": votes,
            "majority": winners[0] if top >= 2 and len(winners) == 1 else None,
            "unanimous": top == len(rows),
        })
    return output


def freeze_completion(*, corpus_root: str | Path,
                      review_files: Mapping[str, str | Path]) -> dict[str, Any]:
    """Freeze completion BEFORE any prediction is compared to any label."""
    root = Path(corpus_root)
    expected = {json.loads(line)["dossier_id"] for line in
                (root / "corpus" / "frozen_dossiers.jsonl").read_text().splitlines()
                if line.strip()}
    per_reviewer: dict[str, Any] = {}
    complete = True
    isolation = True
    for reviewer, path in review_files.items():
        rows = [json.loads(line) for line in Path(path).read_text().splitlines()
                if line.strip()]
        seen = {row.get("dossier_id") for row in rows}
        missing = sorted(expected - seen)
        complete &= not missing
        broke = any(row.get("other_reviews_seen") for row in rows)
        isolation &= not broke
        per_reviewer[reviewer] = {
            "reviewed": len(rows), "expected": len(expected),
            "missing_count": len(missing), "missing": missing[:20],
            "review_hash": sha256(rows), "other_reviews_seen": broke,
            "human_review": any(row.get("human_review") for row in rows),
        }
    manifest = {
        "frozen_time": now_utc(), "expected_units": len(expected),
        "reviewers": per_reviewer, "all_complete": complete,
        "reviewer_isolation_intact": isolation,
        "frozen_before_any_comparison": True,
        "verdict": "PASS" if complete and isolation else "INVALID",
    }
    write_json(root / "review_completion_manifest.json", manifest)
    return manifest


def score(*, corpus_root: str | Path, prediction_manifest: str | Path,
          review_files: Mapping[str, str | Path],
          completion_manifest: Mapping[str, Any],
          output_root: str | Path) -> dict[str, Any]:
    """Compare frozen predictions to frozen reviewer majorities."""
    if completion_manifest.get("verdict") != "PASS":
        raise ValueError("scoring may not run before a passing completion manifest")

    root, out = Path(corpus_root), Path(output_root)
    out.mkdir(parents=True, exist_ok=True)
    predictions = load_prediction_manifest(prediction_manifest)
    mapping = json.loads(
        (root / "sealed_keys" / "dossier_to_production_object.json").read_text())["mapping"]
    certificates = {c["dossier_id"]: c["result"] for c in json.loads(
        (root / "corpus" / "sufficiency_certificates.json").read_text())["certificates"]}
    surface_of = {json.loads(line)["dossier_id"]: json.loads(line)["surface"]
                  for line in (root / "corpus" / "frozen_dossiers.jsonl").read_text().splitlines()
                  if line.strip()}

    reviews = {name: [json.loads(line) for line in Path(path).read_text().splitlines()
                      if line.strip()]
               for name, path in review_files.items()}
    rows = consensus(reviews)
    for row in rows:
        row["surface"] = surface_of.get(row["dossier_id"], row["surface"])
    write_json(out / "consensus_rows.json", rows)

    comparisons: list[ScoringComparisonRecord] = []
    for row in rows:
        dossier_id = row["dossier_id"]
        object_id = mapping.get(dossier_id)
        record = predictions.get(object_id) if object_id else None
        certificate = certificates.get(dossier_id, "SELF_SUFFICIENT")
        comparisons.append(compare(
            dossier_id=dossier_id, surface=row["surface"], prediction=record,
            reviewer_majority=row["majority"], reviewer_votes=row["votes"],
            unanimous=row["unanimous"],
            construction_defect=certificate == "CONSTRUCTION_DEFECT",
            unresolvable_certified=(
                certificate == "INHERENTLY_UNRESOLVABLE_FROM_PUBLIC_EVIDENCE")))

    with (out / "scoring_comparisons.jsonl").open("w") as handle:
        for item in comparisons:
            handle.write(json.dumps(item.to_record(), sort_keys=True,
                                    ensure_ascii=False) + "\n")

    by_surface: dict[str, list[ScoringComparisonRecord]] = {}
    for item in comparisons:
        by_surface.setdefault(item.surface, []).append(item)

    reports: dict[str, Any] = {}
    for surface, items in sorted(by_surface.items()):
        total = len(items)
        correct = sum(1 for i in items if i.outcome == "CORRECT")
        incorrect = sum(1 for i in items if i.outcome == "INCORRECT")
        unresolvable = sum(1 for i in items if i.outcome == "GENUINELY_UNRESOLVABLE")
        disagreement = sum(1 for i in items if i.outcome == "REVIEWER_DISAGREEMENT")
        defect = sum(1 for i in items if i.outcome == "CONSTRUCTION_DEFECT")
        invalid = sum(1 for i in items
                      if i.outcome == "INVALID_FOR_CAPABILITY_SCORING")
        adjudicable = correct + incorrect
        confusion: dict[str, int] = {}
        for item in items:
            if item.outcome == "INCORRECT":
                key = f"{item.production_prediction}->{item.reviewer_majority}"
                confusion[key] = confusion.get(key, 0) + 1
        reports[surface] = {
            "total_cases": total, "adjudicable_cases": adjudicable,
            "genuinely_unresolvable_cases": unresolvable,
            "correct": correct, "incorrect": incorrect,
            "construction_defects": defect,
            "reviewer_disagreements": disagreement,
            "invalid_for_capability_scoring": invalid,
            "semantic_correctness": round(correct / max(1, adjudicable), 4),
            "task_completion": round(correct / max(1, total), 4),
            "confusion": confusion,
            "producer_modules": sorted({i.producer_module for i in items
                                        if i.producer_module}),
        }
        gates = {
            "semantic_correctness_at_least_95":
                reports[surface]["semantic_correctness"] >= 0.95,
            "task_completion_at_least_95":
                reports[surface]["task_completion"] >= 0.95,
            "no_construction_defects": defect == 0,
            "every_unit_production_grounded": invalid == 0,
        }
        reports[surface]["gates"] = gates
        reports[surface]["verdict"] = "PASS" if all(gates.values()) else "PARTIAL"

    audit = provenance_audit(comparisons)
    summary = {
        "scored_time": now_utc(),
        "completion_manifest_frozen_before_comparison": True,
        "prediction_manifest": str(prediction_manifest),
        "reviewers": len(review_files),
        "units": len(rows),
        "surface_reports": reports,
        "provenance_audit": audit,
        "unanimity_rate": round(
            sum(1 for r in rows if r["unanimous"]) / max(1, len(rows)), 4),
    }
    write_json(out / "heldout_scores.json", summary)
    return summary


def critical_errors(comparisons: Iterable[ScoringComparisonRecord | Mapping[str, Any]],
                    predictions: Mapping[str, ProductionPredictionRecord]
                    ) -> dict[str, int]:
    """Section 18 zero-tolerance counters, read off the scored comparisons."""
    counts = {
        "surface_1_wrong_accepted_span": 0,
        "surface_1_structural_chrome_accepted": 0,
        "surface_2_host_publisher_conflation": 0,
        "surface_3_dependence_counted_independent": 0,
        "surface_4_plan_as_implementation": 0,
        "surface_5_update_classified_contradiction": 0,
        "surface_5_contradiction_classified_update": 0,
        "surface_6_unsupported_factual_proposition": 0,
    }
    non_corroborating = {"DERIVATIVE_CONFIRMED", "TRANSLATION_DERIVATIVE",
                         "SYNDICATION_DERIVATIVE", "MIRROR_MANIFESTATION",
                         "INDEPENDENCE_UNKNOWN", "NO_DEPENDENCE_FOUND"}
    for item in comparisons:
        get = (item.get if isinstance(item, Mapping)
               else lambda key, obj=item: getattr(obj, key, None))
        if get("outcome") != "INCORRECT":
            continue
        surface = str(get("surface"))
        predicted = str(get("production_prediction"))
        actual = str(get("reviewer_majority"))
        if surface.startswith("SURFACE_1") and predicted == "ACCEPTED_CANDIDATE":
            counts["surface_1_wrong_accepted_span"] += 1
            if actual == "QUARANTINED":
                counts["surface_1_structural_chrome_accepted"] += 1
        if surface.startswith("SURFACE_2") and predicted == "PUBLISHED_BY" \
                and actual == "HOSTED_BY":
            counts["surface_2_host_publisher_conflation"] += 1
        if surface.startswith("SURFACE_3") and actual in non_corroborating \
                and predicted in ("INDEPENDENCE_SUPPORTED",
                                  "SHARED_DATA_INDEPENDENT_ANALYSIS"):
            counts["surface_3_dependence_counted_independent"] += 1
        if surface.startswith("SURFACE_5"):
            if predicted == "LOGICAL_CONTRADICTION" and actual == "TEMPORAL_UPDATE":
                counts["surface_5_update_classified_contradiction"] += 1
            if predicted == "TEMPORAL_UPDATE" and actual == "LOGICAL_CONTRADICTION":
                counts["surface_5_contradiction_classified_update"] += 1
        if surface.startswith("SURFACE_6") and predicted in (
                "PUBLISHED", "PUBLISHED_WITH_QUALIFICATION") and \
                actual == "REJECTED_UNSUPPORTED":
            counts["surface_6_unsupported_factual_proposition"] += 1
    for record in predictions.values():
        if record.surface == "SURFACE_4_CLAIM_SUPPORT":
            error = (record.structured_rationale or {}).get("critical_error")
            if error == "plan_as_implementation" and record.prediction in (
                    "FULL_SUPPORT", "PARTIAL_SUPPORT", "QUALIFIED_SUPPORT",
                    "CONTEXT_DEPENDENT_SUPPORT"):
                counts["surface_4_plan_as_implementation"] += 1
    return counts
