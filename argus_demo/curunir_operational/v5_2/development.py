"""Development replay over exposed V5.1 material (contract Section 16).

V5.1 cases are development and regression data for V5.2.  This module replays
the repaired production path over them and scores it against the V5.1 blind
reviewer consensus, which is now an exposed label set.

Nothing here encodes an answer: every label is read from a file at call time,
and no case identifier, source URL or entity appears in the code.  The module
is development tooling and is excluded from the production freeze scope.

Research shadow only.
"""
from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from ..v4.custody import normalize_source
from ..v4.models import NormalizedDocument, RetrievalRecord, SourceRecord
from ..v5_1.layout import analyze_layout
from . import semantics


def _rows(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def _record(cls, payload: Mapping[str, Any]):
    fields = {f for f in cls.__dataclass_fields__}
    kwargs = {}
    for name in fields:
        value = payload.get(name)
        if isinstance(value, list):
            value = tuple(tuple(item) if isinstance(item, list) else item
                          for item in value)
        kwargs[name] = value
    return cls(**kwargs)


def load_documents(custody_root: str | Path) -> dict[str, NormalizedDocument]:
    """Re-normalize every admitted source in one campaign custody root.

    Keyed by source_object_id, which is what candidate rows carry.
    """
    root = Path(custody_root)
    records = root / "records"
    sources = {row["source_object_id"]: row
               for row in _rows(records / "source_records.jsonl")}
    retrievals = {row["retrieval_id"]: row
                  for row in _rows(records / "retrieval_records.jsonl")}
    documents: dict[str, NormalizedDocument] = {}
    for source_id, row in sources.items():
        retrieval_ids = row.get("retrieval_ids") or []
        retrieval_row = next((retrievals[rid] for rid in retrieval_ids
                              if rid in retrievals), None)
        if retrieval_row is None:
            continue
        try:
            source = _record(SourceRecord, row)
            retrieval = _record(RetrievalRecord, retrieval_row)
            documents[source_id] = normalize_source(source, retrieval)
        except (ValueError, TypeError, KeyError, FileNotFoundError):
            continue
    return documents


@dataclass(frozen=True)
class ReplayCase:
    case_id: str
    candidate_id: str
    source_object_id: str
    expected_stage: str
    v5_1_stage: str


def replay_extraction(*, candidates: Sequence[Mapping[str, Any]],
                      documents: Mapping[str, NormalizedDocument],
                      cases: Iterable[ReplayCase],
                      media_types: Mapping[str, str] | None = None) -> dict[str, Any]:
    """Run the repaired resolver over exposed cases and score it.

    Returns per-case outcomes plus the aggregate the development gate reads.
    """
    media_types = media_types or {}
    by_id = {row["candidate_id"]: row for row in candidates}
    outcomes: list[dict[str, Any]] = []
    layouts: dict[str, Any] = {}
    for case in cases:
        row = by_id.get(case.candidate_id)
        document = documents.get(case.source_object_id)
        if row is None or document is None:
            outcomes.append({
                "case_id": case.case_id, "candidate_id": case.candidate_id,
                "expected": case.expected_stage, "v5_1": case.v5_1_stage,
                "v5_2": None, "status": "UNAVAILABLE"})
            continue
        if document.document_id not in layouts:
            try:
                layouts[document.document_id] = analyze_layout(
                    document.text, document.pages, media_types.get(
                        case.source_object_id, "text/html"), document.parser)
            except (ValueError, TypeError):
                layouts[document.document_id] = None
        candidate = _record(semantics.ExtractionCandidate, row)
        decision = semantics.resolve_candidate(
            candidate, document, layouts[document.document_id],
            language=document.language)
        outcomes.append({
            "case_id": case.case_id, "candidate_id": case.candidate_id,
            "expected": case.expected_stage, "v5_1": case.v5_1_stage,
            "v5_2": decision.stage, "level": decision.selected_level,
            "lifecycle_state": decision.lifecycle_state,
            "alternatives": decision.alternative_count,
            "warnings": list(decision.boundary_warnings),
            "status": "CORRECT" if decision.stage == case.expected_stage else "INCORRECT",
        })
    scored = [row for row in outcomes if row["status"] != "UNAVAILABLE"]
    v5_1_correct = sum(1 for row in scored if row["v5_1"] == row["expected"])
    v5_2_correct = sum(1 for row in scored if row["v5_2"] == row["expected"])
    total = max(1, len(scored))
    return {
        "cases_requested": len(outcomes),
        "cases_scored": len(scored),
        "cases_unavailable": len(outcomes) - len(scored),
        "v5_1_correct": v5_1_correct,
        "v5_2_correct": v5_2_correct,
        "v5_1_semantic_correctness": round(v5_1_correct / total, 4),
        "v5_2_semantic_correctness": round(v5_2_correct / total, 4),
        "confusion_v5_2": dict(Counter(
            f"{row['expected']}->{row['v5_2']}" for row in scored
            if row["status"] == "INCORRECT")),
        "outcomes": outcomes,
    }
