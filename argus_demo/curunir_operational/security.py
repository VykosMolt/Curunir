"""Typed information-flow policy for the integrated Curunír record graph.

The policy is deliberately data-oriented: domain packages keep their public
APIs, while one registry says which fields carry material in-store references.
Both marking admission and authoritative control walks consume this registry.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Mapping

from .access import Marking, inherited_marking, marking_from_record


# Record identity is explicit.  Actor, model, rule, connector, inference, and
# proposal identifiers are intentionally absent: they are authority/provenance
# labels, not material state whose contents are embedded by every mention.
PRIMARY_ID_FIELDS: dict[str, str] = {
    "source": "source_id",
    "ingestion": "ingestion_id",
    "object_version": "object_id",
    "relationship_version": "relationship_id",
    "activity": "activity_id",
    "alert": "alert_id",
    "information_requirement": "requirement_id",
    "evidence_request": "request_id",
    "analyst_task": "task_id",
    "fabric_execution": "execution_id",
    "fabric_manifestation": "manifestation_id",
    "fabric_change": "change_id",
    "semantic_document": "document_id",
    "semantic_observation": "observation_id",
    "semantic_claim": "claim_id",
    "semantic_claim_state": "state_id",
    "semantic_change": "change_id",
    "hypothesis": "hypothesis_id",
    "discriminator": "discriminator_id",
    "collection_route": "route_id",
    "review_item": "item_id",
    "analytic_theme": "theme_id",
    "analytic_narrative": "narrative_id",
    "narrative_variant": "variant_id",
    "propagation_edge": "edge_id",
    "stakeholder_assessment": "assessment_id",
    "influence_assertion": "influence_id",
    "mission_objective": "objective_id",
    "analytic_assumption": "assumption_id",
    "impact_path": "path_id",
    "response_option": "option_id",
    "historical_episode": "episode_id",
    "historical_analogue": "analogue_id",
    "analytic_forecast": "forecast_id",
    "forecast_indicator": "indicator_id",
    "strategic_warning": "warning_id",
    "analytic_transition": "transition_id",
    "workbench_annotation": "annotation_id",
    "workbench_report": "report_id",
    "actor_key": "key_id",
    "signed_action": "action_id",
}


KIND_ALIASES = {
    "claim": "semantic_claim",
    "observation": "semantic_observation",
    "manifestation": "fabric_manifestation",
    "document": "semantic_document",
    "change": "semantic_change",
    "theme": "analytic_theme",
    "narrative": "analytic_narrative",
    "forecast": "analytic_forecast",
    "indicator": "forecast_indicator",
    "warning": "strategic_warning",
    "objective": "mission_objective",
    "assumption": "analytic_assumption",
    "path": "impact_path",
    "option": "response_option",
    "episode": "historical_episode",
    "analogue": "historical_analogue",
    "relationship": "relationship_version",
    "object": "object_version",
    "requirement": "information_requirement",
}


# A field can name more than one compatible family where the historic schemas
# reused a generic name.  Resolution includes every matching current record.
FIELD_KINDS: dict[str, tuple[str, ...]] = {
    "claim_id": ("semantic_claim",),
    "claim_ids": ("semantic_claim",),
    "supporting_claim_ids": ("semantic_claim",),
    "contradicting_claim_ids": ("semantic_claim",),
    "unresolved_claim_ids": ("semantic_claim",),
    "outcome_claim_ids": ("semantic_claim",),
    "affected_claim_ids": ("semantic_claim",),
    "observation_id": ("semantic_observation",),
    "observation_ids": ("semantic_observation",),
    "basis_observation_ids": ("semantic_observation",),
    "prior_observation_id": ("semantic_observation",),
    "current_observation_id": ("semantic_observation",),
    "document_id": ("semantic_document",),
    "manifestation_id": ("fabric_manifestation",),
    "manifestation_ids": ("fabric_manifestation",),
    "evidence_manifestation_ids": ("fabric_manifestation",),
    "prior_manifestation_id": ("fabric_manifestation",),
    "current_manifestation_id": ("fabric_manifestation",),
    "hypothesis_id": ("hypothesis",),
    "hypothesis_ids": ("hypothesis",),
    "discriminator_id": ("discriminator",),
    "discriminator_ids": ("discriminator",),
    "requirement_id": ("information_requirement",),
    "task_id": ("analyst_task",),
    "execution_id": ("fabric_execution",),
    "basis_execution_ids": ("fabric_execution",),
    "activity_id": ("activity",),
    "theme_id": ("analytic_theme",),
    "parent_theme_id": ("analytic_theme",),
    "narrative_id": ("analytic_narrative",),
    "variant_ids": ("narrative_variant",),
    "counter_narrative_ids": ("analytic_narrative",),
    "assessment_id": ("stakeholder_assessment",),
    "influence_id": ("influence_assertion",),
    "influence_ids": ("influence_assertion",),
    "objective_id": ("mission_objective",),
    "objective_ids": ("mission_objective",),
    "assumption_id": ("analytic_assumption",),
    "assumption_ids": ("analytic_assumption",),
    "path_id": ("impact_path",),
    "impact_path_ids": ("impact_path",),
    "option_id": ("response_option",),
    "option_ids": ("response_option",),
    "episode_id": ("historical_episode",),
    "forecast_id": ("analytic_forecast",),
    "forecast_ids": ("analytic_forecast",),
    "indicator_id": ("forecast_indicator",),
    "indicator_ids": ("forecast_indicator",),
    "warning_id": ("strategic_warning",),
    "relationship_id": ("relationship_version",),
    "relationship_ids": ("relationship_version",),
    "source_object_id": ("object_version",),
    "target_object_id": ("object_version",),
    "entity_object_id": ("object_version",),
    "subject_object_id": ("object_version",),
    "object_object_id": ("object_version",),
    "actor_object_ids": ("object_version",),
    "affected_object_ids": ("object_version",),
    "event_ids": ("object_version",),
    "dissent_annotation_ids": ("workbench_annotation",),
    "supersedes_key_id": ("actor_key",),
}

TYPED_PAIR_FIELDS = frozenset({"basis_refs", "proposition_refs", "depends_on"})
ANY_REFERENCE_FIELDS = frozenset({
    "affected_ids", "basis_ids", "caused_by", "evidence_refs",
    "input_refs", "desired_subject_ref", "from_id", "to_id",
})
PER_RECORD_EXCLUSIONS = {
    # A forecast listing its watchers does not embed their restricted state.
    ("analytic_forecast", "indicator_ids"),
}


@dataclass(frozen=True, order=True)
class MaterialReference:
    kind: str
    record_id: str


def _strings(value: Any) -> Iterable[str]:
    if isinstance(value, str):
        if value:
            yield value
    elif isinstance(value, (list, tuple, set, frozenset)):
        for item in value:
            if isinstance(item, str) and item:
                yield item


def material_references(record: Mapping[str, Any]) -> tuple[MaterialReference, ...]:
    """Return all typed material dependencies, including nested carriers."""
    record_type = str(record.get("record_type", ""))
    found: set[MaterialReference] = set()

    def walk(value: Any) -> None:
        if isinstance(value, Mapping):
            for key, item in value.items():
                if key in {"marking", "record_type", PRIMARY_ID_FIELDS.get(record_type)}:
                    continue
                if (record_type, key) in PER_RECORD_EXCLUSIONS:
                    continue
                if key in TYPED_PAIR_FIELDS:
                    for pair in item or ():
                        if isinstance(pair, (list, tuple)) and len(pair) == 2 \
                                and isinstance(pair[0], str) and isinstance(pair[1], str):
                            found.add(MaterialReference(
                                KIND_ALIASES.get(pair[0], pair[0]), pair[1]))
                        elif isinstance(pair, Mapping):
                            kind = pair.get("kind")
                            ref = pair.get("ref") or pair.get("id")
                            if isinstance(kind, str) and isinstance(ref, str) and ref:
                                found.add(MaterialReference(KIND_ALIASES.get(kind, kind), ref))
                    continue
                kinds = FIELD_KINDS.get(key)
                if kinds:
                    for reference in _strings(item):
                        for kind in kinds:
                            found.add(MaterialReference(kind, reference))
                elif key in ANY_REFERENCE_FIELDS:
                    for reference in _strings(item):
                        found.add(MaterialReference("*", reference))
                walk(item)
        elif isinstance(value, (list, tuple)):
            for item in value:
                walk(item)

    walk(record)
    return tuple(sorted(found))


def _latest(store: Any, record_type: str, id_field: str, record_id: str) -> dict | None:
    matches = [record for record in store.records_of(record_type)
               if record.get(id_field) == record_id]
    if not matches:
        return None
    return max(matches, key=lambda record: record.get("version", 1))


def resolve_reference_records(
    store: Any,
    references: Iterable[MaterialReference],
) -> tuple[dict, ...]:
    """Resolve references against current raw state, never a projection."""
    resolved: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for reference in references:
        kinds = PRIMARY_ID_FIELDS if reference.kind == "*" else {
            KIND_ALIASES.get(reference.kind, reference.kind):
            PRIMARY_ID_FIELDS.get(KIND_ALIASES.get(reference.kind, reference.kind), "")
        }
        for kind, id_field in kinds.items():
            if not id_field:
                continue
            candidate_ids = [reference.record_id]
            if kind == "object_version":
                try:
                    from curunir_semantic.worldmodel import world_object_id
                    mapped = world_object_id(reference.record_id)
                    if mapped and mapped not in candidate_ids:
                        candidate_ids.append(mapped)
                except (ImportError, TypeError, ValueError):
                    pass
            for candidate_id in candidate_ids:
                record = _latest(store, kind, id_field, candidate_id)
                key = (kind, candidate_id)
                if record is not None and key not in seen:
                    resolved.append(record)
                    seen.add(key)
    return tuple(resolved)


def resolve_reference_markings(
    store: Any,
    references: Iterable[MaterialReference | str],
) -> list[Mapping[str, Any]]:
    normalized = [
        reference if isinstance(reference, MaterialReference)
        else MaterialReference("*", reference)
        for reference in references if reference
    ]
    return [record["marking"] for record in resolve_reference_records(store, normalized)
            if isinstance(record.get("marking"), Mapping)]


def previous_version(store: Any, record: Mapping[str, Any]) -> dict | None:
    record_type = str(record.get("record_type", ""))
    id_field = PRIMARY_ID_FIELDS.get(record_type)
    if not id_field or not isinstance(record.get(id_field), str):
        return None
    return _latest(store, record_type, id_field, record[id_field])


def admit_marking(store: Any, record: Mapping[str, Any]) -> dict[str, Any]:
    """Return a copy whose marking covers prior state and material refs."""
    data = dict(record)
    raw_marking = data.get("marking")
    if not isinstance(raw_marking, Mapping):
        return data
    base = marking_from_record(raw_marking)
    markings: list[Marking | Mapping[str, Any] | None] = []
    prior = previous_version(store, data)
    if prior is not None:
        markings.append(prior.get("marking"))
    markings.extend(resolve_reference_markings(store, material_references(data)))
    data["marking"] = inherited_marking(base, markings).to_record()
    return data
