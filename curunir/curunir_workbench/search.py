"""Typed cross-object search over one authorized projection.

Results preserve object type and say what matched. Search only ever sees the
access-filtered projection, so hidden objects can neither hit nor be counted.
"""
from __future__ import annotations

from typing import Any, Iterable

from .projections import MissionProjection

# family → (id field, searchable text fields, label field)
_SEARCH_FAMILIES: dict[str, tuple[str, tuple[str, ...], str]] = {
    "semantic_claim": ("claim_id", ("statement", "predicate", "subject_ref", "object_or_value"), "statement"),
    "hypothesis": ("hypothesis_id", ("statement",), "statement"),
    "analytic_theme": ("theme_id", ("title", "summary"), "title"),
    "analytic_narrative": ("narrative_id", ("title", "core_proposition"), "title"),
    "stakeholder_assessment": ("assessment_id", ("stakeholder_object_id", "context_ref"), "stakeholder_object_id"),
    "mission_objective": ("objective_id", ("title", "statement"), "title"),
    "impact_path": ("path_id", ("summary",), "summary"),
    "analytic_forecast": ("forecast_id", ("question", "outcome_semantics", "domain", "author"), "question"),
    "forecast_indicator": ("indicator_id", ("description",), "description"),
    "strategic_warning": ("warning_id", ("mission_context", "tier"), "mission_context"),
    "review_item": ("item_id", ("detail", "kind"), "detail"),
    "collection_route": ("route_id", ("explanation", "query_value", "source_id"), "explanation"),
    "fabric_watch": ("watch_id", ("target_ref", "query_value"), "target_ref"),
    "fabric_source_descriptor": ("source_id", ("title", "family", "jurisdiction"), "title"),
    "fabric_manifestation": ("manifestation_id", ("final_url", "native_id", "source_id"), "final_url"),
    "semantic_observation": ("observation_id", ("attribute", "value", "subject_ref"), "value"),
    "workbench_annotation": ("annotation_id", ("text", "author"), "text"),
    "workbench_report": ("report_id", ("title", "question"), "title"),
    "analytic_assumption": ("assumption_id", ("statement",), "statement"),
    "response_option": ("option_id", ("description",), "description"),
}


def _match(record: dict, fields: tuple[str, ...], terms: list[str]) -> list[str]:
    """Return the fields where every term appears (case-insensitive)."""
    matched = []
    for field in fields:
        value = str(record.get(field, "") or "").casefold()
        if value and all(term in value for term in terms):
            matched.append(field)
    return matched


def search(projection: MissionProjection, query: str, *,
           types: Iterable[str] = (), status: str | None = None,
           limit: int = 50) -> dict[str, Any]:
    terms = [t.casefold() for t in query.split() if t.strip()]
    wanted = set(types) if types else None
    results: list[dict] = []

    # world-model objects and events come from the operational base view
    if wanted is None or "object" in wanted:
        for record in projection.visible_objects():
            haystacks = {"labels": " ".join(record.get("labels", ())),
                         "object_type": record["object_type"],
                         "object_id": record["object_id"]}
            matched = [k for k, v in haystacks.items()
                       if terms and all(t in v.casefold() for t in terms)]
            if matched:
                results.append({"type": "object", "id": record["object_id"],
                                "label": (record.get("labels") or [record["object_id"]])[0],
                                "status": record.get("lifecycle", ""),
                                "epistemic_state": record["epistemic_state"],
                                "matched_fields": matched})
    if wanted is None or "event" in wanted:
        for record in projection.base_view["activities"]:
            haystacks = {"description": record.get("description", ""),
                         "activity_type": record["activity_type"]}
            matched = [k for k, v in haystacks.items()
                       if terms and all(t in str(v).casefold() for t in terms)]
            if matched:
                results.append({"type": "event", "id": record["activity_id"],
                                "label": record.get("description", record["activity_type"])[:120],
                                "status": "", "epistemic_state": record["epistemic_state"],
                                "matched_fields": matched})

    for record_type, (id_field, fields, label_field) in _SEARCH_FAMILIES.items():
        if wanted is not None and record_type not in wanted:
            continue
        for record in projection.family(record_type):
            if status and record.get("status") != status:
                continue
            matched = _match(record, fields, terms) if terms else []
            if not matched:
                continue
            results.append({"type": record_type, "id": record[id_field],
                            "label": str(record.get(label_field, record[id_field]))[:160],
                            "status": str(record.get("status", "")),
                            "epistemic_state": record.get("epistemic_state",
                                                          record.get("authority", "")),
                            "matched_fields": matched})
    results.sort(key=lambda r: (r["type"], r["id"]))
    return {"query": query, "total": len(results), "results": results[:limit]}
