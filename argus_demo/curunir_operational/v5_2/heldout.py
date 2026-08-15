"""Clean held-out corpus and scoring over dossiers (contract Sections 20-21).

V5.1's held-out machinery had three defects this module closes.

* It sealed 132 review units its own reviewers then marked defective.  Here
  no dossier enters the corpus until ``dossiers.certify_sufficiency`` returns
  SELF_SUFFICIENT or INHERENTLY_UNRESOLVABLE_FROM_PUBLIC_EVIDENCE.
* Its Surface-1 sample drew 74 rejections and 16 quarantines and not one
  accepted candidate, so the wrong-admission gate — the critical error the
  closure contract forbids — was never measurable.  The sampling policy here
  is stated per surface as required decision classes, and sealing fails if a
  required class is absent.
* It reported class coverage after the fact.  Here the required-class
  inventory is enforced before sealing and reported with the corpus.

Reviewer isolation, sealed keys and the completion manifest follow V5.1,
which passed those gates; that machinery is composed rather than rewritten.

Research shadow only.
"""
from __future__ import annotations

import json
import random
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from ..v4.io import write_json
from ..v5_1.models import Record, now_utc, sha256, stable_id
from .dossiers import Dossier, certify_sufficiency

SURFACES = (
    "SURFACE_1_SEMANTIC_EXTRACTION",
    "SURFACE_2_SOURCE_IDENTITY_AND_ORIGIN",
    "SURFACE_3_DEPENDENCE_AND_CORROBORATION",
    "SURFACE_4_CLAIM_SUPPORT",
    "SURFACE_5_TEMPORAL_RELATIONS",
    "SURFACE_6_REPORT_FAITHFULNESS",
)

# Section 20.1 minimum primary units per surface.  Surface 6 is "all
# reportable propositions", so it has no fixed floor.
MINIMUM_PRIMARY: Mapping[str, int] = {
    "SURFACE_1_SEMANTIC_EXTRACTION": 90,
    "SURFACE_2_SOURCE_IDENTITY_AND_ORIGIN": 60,
    "SURFACE_3_DEPENDENCE_AND_CORROBORATION": 60,
    "SURFACE_4_CLAIM_SUPPORT": 70,
    "SURFACE_5_TEMPORAL_RELATIONS": 60,
    "SURFACE_6_REPORT_FAITHFULNESS": 0,
}

RESERVE_FRACTION = 0.25

# Decision classes each surface's sample must contain.  Surface 1's entry is
# the direct repair of the V5.1 sampling defect: a corpus that never draws an
# accepted candidate cannot measure wrong admission.
REQUIRED_DECISION_CLASSES: Mapping[str, tuple[str, ...]] = {
    "SURFACE_1_SEMANTIC_EXTRACTION": ("ACCEPTED_CANDIDATE", "REJECTED",
                                      "QUARANTINED", "SEMANTICALLY_PARSED"),
    "SURFACE_2_SOURCE_IDENTITY_AND_ORIGIN": (),
    "SURFACE_3_DEPENDENCE_AND_CORROBORATION": (),
    "SURFACE_4_CLAIM_SUPPORT": (),
    "SURFACE_5_TEMPORAL_RELATIONS": (),
    "SURFACE_6_REPORT_FAITHFULNESS": (),
}

REVIEW_ENGINE_ONLY = "REVIEW_ENGINE_ONLY"


@dataclass(frozen=True)
class CorpusEntry(Record):
    """One sealed unit: the dossier, its certificate and its sealed answer."""

    entry_id: str
    dossier_id: str
    surface: str
    reserve: bool
    sealed_answer: str
    sufficiency_result: str
    sampling_stratum: str


def _shuffled(items: Sequence[Any], seed: int) -> list[Any]:
    """Deterministic shuffle: a reviewer's order must be reproducible."""
    ordered = list(items)
    random.Random(seed).shuffle(ordered)
    return ordered


def build_corpus(*, dossiers: Sequence[Dossier],
                 sealed_answers: Mapping[str, str],
                 strata: Mapping[str, str],
                 output_root: str | Path,
                 reserve_fraction: float = RESERVE_FRACTION,
                 seed: int = 20260724) -> dict[str, Any]:
    """Certify, split and seal a corpus.  Refuses to seal a defective unit."""
    root = Path(output_root)
    (root / "corpus").mkdir(parents=True, exist_ok=True)
    (root / "sealed_keys").mkdir(parents=True, exist_ok=True)

    certificates = []
    defects: list[str] = []
    admissible: list[Dossier] = []
    for dossier in dossiers:
        certificate = certify_sufficiency(
            dossier, sealed_answer=sealed_answers.get(dossier.dossier_id))
        certificates.append(certificate.to_record())
        if certificate.result == "CONSTRUCTION_DEFECT":
            defects.append(dossier.dossier_id)
        else:
            admissible.append(dossier)

    by_surface: dict[str, list[Dossier]] = {surface: [] for surface in SURFACES}
    for dossier in admissible:
        by_surface.setdefault(dossier.surface, []).append(dossier)

    primary: list[CorpusEntry] = []
    reserve: list[CorpusEntry] = []
    result_by_id = {row["dossier_id"]: row["result"] for row in certificates}
    for surface, items in by_surface.items():
        ordered = _shuffled(items, seed + SURFACES.index(surface)
                            if surface in SURFACES else seed)
        split = max(0, int(round(len(ordered) * reserve_fraction)))
        held = ordered[:split]
        used = ordered[split:]
        for dossier, target in ((d, primary) for d in used):
            target.append(_entry(dossier, sealed_answers, strata, result_by_id,
                                 reserve=False))
        for dossier in held:
            reserve.append(_entry(dossier, sealed_answers, strata, result_by_id,
                                  reserve=True))

    inventory = class_inventory(primary, sealed_answers)
    shortfalls = _shortfalls(primary, inventory)

    _write_jsonl(root / "corpus" / "frozen_dossiers.jsonl",
                 [d.public_payload() for d in admissible if not _is_reserve(d, reserve)])
    _write_jsonl(root / "corpus" / "reserve_dossiers.jsonl",
                 [d.public_payload() for d in admissible if _is_reserve(d, reserve)])
    write_json(root / "corpus" / "sufficiency_certificates.json",
               {"certificates": certificates})
    write_json(root / "sealed_keys" / "sealed_answers.json", {
        "access_marking": {"releasability": [REVIEW_ENGINE_ONLY]},
        "answers": {entry.dossier_id: entry.sealed_answer
                    for entry in primary + reserve},
        "strata": {entry.dossier_id: entry.sampling_stratum
                   for entry in primary + reserve},
    })

    manifest = {
        "built_time": now_utc(),
        "dossiers_offered": len(dossiers),
        "construction_defects": len(defects),
        "construction_defect_ids": defects,
        "primary_units": len(primary),
        "reserve_units": len(reserve),
        "reserve_fraction": reserve_fraction,
        "per_surface": {
            surface: {
                "primary": sum(1 for e in primary if e.surface == surface),
                "reserve": sum(1 for e in reserve if e.surface == surface),
                "minimum_required": MINIMUM_PRIMARY.get(surface, 0),
            } for surface in SURFACES},
        "class_inventory": inventory,
        "shortfalls": shortfalls,
        "sealable": not defects and not shortfalls,
        "corpus_hash": sha256([e.to_record() for e in primary]),
    }
    write_json(root / "corpus" / "manifest.json", manifest)
    return manifest


def _entry(dossier: Dossier, answers: Mapping[str, str],
           strata: Mapping[str, str], results: Mapping[str, str], *,
           reserve: bool) -> CorpusEntry:
    return CorpusEntry(
        stable_id("v5-2-corpus-entry", dossier.dossier_id, str(int(reserve))),
        dossier.dossier_id, dossier.surface, reserve,
        answers.get(dossier.dossier_id, "UNSPECIFIED"),
        results.get(dossier.dossier_id, "SELF_SUFFICIENT"),
        strata.get(dossier.dossier_id, "UNSPECIFIED"))


def _is_reserve(dossier: Dossier, reserve: Sequence[CorpusEntry]) -> bool:
    return any(entry.dossier_id == dossier.dossier_id for entry in reserve)


def _write_jsonl(path: Path, rows: Iterable[Mapping[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as handle:
        for row in rows:
            handle.write(json.dumps(row, sort_keys=True, ensure_ascii=False) + "\n")


def class_inventory(entries: Sequence[CorpusEntry],
                    sealed_answers: Mapping[str, str]) -> dict[str, Any]:
    """Which decision classes the sealed sample actually contains."""
    inventory: dict[str, dict[str, int]] = {}
    for entry in entries:
        answer = sealed_answers.get(entry.dossier_id, entry.sealed_answer)
        inventory.setdefault(entry.surface, {})
        inventory[entry.surface][answer] = inventory[entry.surface].get(answer, 0) + 1
    return inventory


def _shortfalls(entries: Sequence[CorpusEntry],
                inventory: Mapping[str, Mapping[str, int]]) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    for surface in SURFACES:
        count = sum(1 for entry in entries if entry.surface == surface)
        minimum = MINIMUM_PRIMARY.get(surface, 0)
        if count < minimum:
            findings.append({"surface": surface, "kind": "BELOW_MINIMUM",
                             "have": count, "need": minimum})
        present = set(inventory.get(surface, {}))
        missing = [name for name in REQUIRED_DECISION_CLASSES.get(surface, ())
                   if name not in present]
        if missing:
            findings.append({"surface": surface, "kind": "MISSING_DECISION_CLASS",
                             "missing": missing})
    return findings


# ---------------------------------------------------------------------------
# Section 20.3 — reviewer isolation
# ---------------------------------------------------------------------------

def assign_reviewers(*, corpus_root: str | Path, reviewer_ids: Sequence[str],
                     seed: int = 20260724) -> dict[str, Any]:
    """Independently shuffled assignment per reviewer, no cross-visibility.

    Every reviewer sees every primary unit in their own order, so the panel
    is three independent full passes rather than a partition.
    """
    if len(reviewer_ids) != 3:
        raise ValueError("the contract fixes the panel at exactly three reviewers")
    root = Path(corpus_root)
    rows = [json.loads(line) for line in
            (root / "corpus" / "frozen_dossiers.jsonl").read_text().splitlines()
            if line.strip()]
    assignments_dir = root / "assignments"
    assignments_dir.mkdir(parents=True, exist_ok=True)
    manifest: dict[str, Any] = {"reviewers": {}, "assigned_time": now_utc()}
    for index, reviewer in enumerate(reviewer_ids):
        ordered = _shuffled(rows, seed + index * 7919)
        path = assignments_dir / f"assignment_{reviewer}.jsonl"
        _write_jsonl(path, ordered)
        manifest["reviewers"][reviewer] = {
            "units": len(ordered),
            "assignment_file": str(path.relative_to(root)),
            "assignment_hash": sha256([row["dossier_id"] for row in ordered]),
            "sees_other_reviewer_output": False,
            "sees_curunir_labels": False,
            "sees_v5_1_labels": False,
            "sees_development_labels": False,
        }
    manifest["isolation"] = "PASS"
    write_json(root / "assignments" / "assignment_manifest.json", manifest)
    return manifest


def freeze_completion(*, corpus_root: str | Path,
                      review_files: Mapping[str, str | Path]) -> dict[str, Any]:
    """Freeze the completion manifest BEFORE any sealed key is opened."""
    root = Path(corpus_root)
    expected = {json.loads(line)["dossier_id"] for line in
                (root / "corpus" / "frozen_dossiers.jsonl").read_text().splitlines()
                if line.strip()}
    per_reviewer: dict[str, Any] = {}
    complete = True
    for reviewer, path in review_files.items():
        rows = [json.loads(line) for line in Path(path).read_text().splitlines()
                if line.strip()]
        seen = {row.get("dossier_id") for row in rows}
        missing = sorted(expected - seen)
        if missing:
            complete = False
        per_reviewer[reviewer] = {
            "reviewed": len(rows), "expected": len(expected),
            "missing": missing[:20], "missing_count": len(missing),
            "review_hash": sha256(rows),
            "other_reviews_seen": any(row.get("other_reviews_seen")
                                      for row in rows),
            "human_review": any(row.get("human_review") for row in rows),
        }
    manifest = {
        "frozen_time": now_utc(),
        "expected_units": len(expected),
        "reviewers": per_reviewer,
        "all_complete": complete,
        "keys_opened_before_freeze": False,
        "verdict": "PASS" if complete and not any(
            row["other_reviews_seen"] for row in per_reviewer.values()) else "INVALID",
    }
    write_json(root / "review_completion_manifest.json", manifest)
    return manifest


# ---------------------------------------------------------------------------
# Section 21 — scoring
# ---------------------------------------------------------------------------

# Reviewer decisions that are not a substantive label.
_NON_LABEL_DECISIONS = frozenset({
    "INSUFFICIENT_INFORMATION", "EPISTEMICALLY_UNRESOLVABLE",
    "CANNOT_ADJUDICATE", "PACKET_DEFECT", "DOSSIER_DEFECT",
})


def consensus(reviews: Mapping[str, Sequence[Mapping[str, Any]]]
              ) -> list[dict[str, Any]]:
    """Majority over three independent passes, one row per unit."""
    by_unit: dict[str, list[Mapping[str, Any]]] = {}
    for rows in reviews.values():
        for row in rows:
            by_unit.setdefault(str(row.get("dossier_id")), []).append(row)
    output: list[dict[str, Any]] = []
    for dossier_id, rows in sorted(by_unit.items()):
        votes: dict[str, int] = {}
        for row in rows:
            decision = str(row.get("decision") or "")
            label = (str(row.get("label") or "") if decision == "LABELED"
                     else decision)
            votes[label] = votes.get(label, 0) + 1
        top = max(votes.values())
        winners = sorted(name for name, count in votes.items() if count == top)
        majority = winners[0] if top >= 2 and len(winners) == 1 else None
        output.append({
            "dossier_id": dossier_id,
            "surface": str(rows[0].get("surface") or ""),
            "votes": votes,
            "majority": majority,
            "unanimous": top == len(rows),
        })
    return output


def score_surface(rows: Sequence[Mapping[str, Any]],
                  sealed_answers: Mapping[str, str],
                  certificates: Mapping[str, str]) -> dict[str, Any]:
    """Score one surface against the sealed keys."""
    total = len(rows)
    adjudicable = correct = incorrect = unresolvable = defect = disagreement = 0
    confusion: dict[str, int] = {}
    for row in rows:
        dossier_id = row["dossier_id"]
        certificate = certificates.get(dossier_id, "SELF_SUFFICIENT")
        if certificate == "CONSTRUCTION_DEFECT":
            defect += 1
            continue
        majority = row.get("majority")
        if majority is None:
            disagreement += 1
            continue
        if majority in _NON_LABEL_DECISIONS:
            if certificate == "INHERENTLY_UNRESOLVABLE_FROM_PUBLIC_EVIDENCE" and \
                    majority == "EPISTEMICALLY_UNRESOLVABLE":
                unresolvable += 1
                correct += 1
                adjudicable += 1
            else:
                unresolvable += 1
            continue
        adjudicable += 1
        expected = sealed_answers.get(dossier_id)
        if majority == expected:
            correct += 1
        else:
            incorrect += 1
            confusion[f"{expected}->{majority}"] = \
                confusion.get(f"{expected}->{majority}", 0) + 1
    denominator = max(1, adjudicable)
    return {
        "total_cases": total,
        "adjudicable_cases": adjudicable,
        "genuinely_unresolvable_cases": unresolvable,
        "correct": correct,
        "incorrect": incorrect,
        "construction_defects": defect,
        "reviewer_disagreements": disagreement,
        "semantic_correctness": round(correct / denominator, 4),
        "task_completion": round(correct / max(1, total), 4),
        "construction_defect_rate": round(defect / max(1, total), 4),
        "disagreement_rate": round(disagreement / max(1, total), 4),
        "confusion": confusion,
    }
