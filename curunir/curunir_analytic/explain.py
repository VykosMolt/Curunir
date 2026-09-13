"""Structured explanations for any analytical object.

Every explanation answers the same eight questions: WHAT, WHY, what stands
AGAINST it, SOURCE_BASIS, TEMPORAL span, INFERENCES, UNCERTAINTY and
MISSION_EFFECT. `render_text` sits on top of the structured form.
"""
from __future__ import annotations

from typing import Any, Mapping

from .store import ANALYTIC_ID_FIELDS, AnalyticStore
from .substrate import DependencyIndex, open_identity_caveats


def _claims_text(store: AnalyticStore, claim_ids) -> list[dict[str, str]]:
    claims = store.current_claims()
    states = store.claim_states()
    result = []
    for claim_id in claim_ids:
        claim = claims.get(claim_id)
        result.append({
            "claim_id": claim_id,
            "statement": claim["statement"] if claim else "(claim not found)",
            # an unknown id is not a claim, so it must not inherit CURRENT
            "state": states.get(claim_id, {}).get("state", "CURRENT") if claim
            else "UNRESOLVED_CLAIM",
        })
    return result


def _mission_effect(store: AnalyticStore, kind: str, object_id: str, *,
                    index: DependencyIndex | None = None) -> dict[str, Any]:
    # the index replays every current analytical view, so a caller explaining
    # several objects should build one and pass it in
    if index is None:
        index = DependencyIndex(store)
    dependents = sorted(index.by_analytic.get((kind, object_id), set()))
    requirements = [r for r in store.records_of("information_requirement")
                    if object_id in r["affected_ids"]]
    hypotheses = []
    record = store.current_analytics(kind).get(object_id, {})
    basis = record.get("basis") or {}
    basis_claims = set(basis.get("supporting_claim_ids", ()))
    if basis_claims:
        for hypothesis in store.current_hypotheses().values():
            touching = basis_claims & (set(hypothesis["supporting_claim_ids"])
                                       | set(hypothesis["contradicting_claim_ids"]))
            if touching:
                hypotheses.append({"hypothesis_id": hypothesis["hypothesis_id"],
                                   "statement": hypothesis["statement"][:140],
                                   "status": hypothesis["status"]})
    return {"dependent_analytics": [f"{k}:{i}" for k, i in dependents],
            "requirements": [r["requirement_id"] for r in requirements],
            "hypotheses": hypotheses}


def explain_object(store: AnalyticStore, kind: str, object_id: str, *,
                   index: DependencyIndex | None = None) -> dict[str, Any]:
    """The eight-section structured explanation for any analytical object."""
    if kind not in ANALYTIC_ID_FIELDS:
        raise ValueError(f"unknown analytical kind: {kind}")
    record = store.current_analytics(kind).get(object_id)
    if record is None:
        return {"kind": kind, "id": object_id, "status": "UNKNOWN_OBJECT"}
    basis = record.get("basis") or {"supporting_claim_ids": (),
                                    "contradicting_claim_ids": (),
                                    "manifestation_count": 0, "source_count": 0,
                                    "origin_families": (), "note": "",
                                    "earliest_time": "", "latest_time": "",
                                    "degraded_claim_count": 0,
                                    "coverage_notes": ()}
    what = record.get("title") or record.get("statement") \
        or record.get("summary") or record.get("description") \
        or record.get("question") \
        or f"{kind} {object_id[:24]}"
    if kind == "analytic_forecast":
        what = (f"p={record['probability']:.2f} that: {record['question']} "
                f"(horizon {record['horizon_time'][:19]})")
    if kind == "strategic_warning":
        what = (f"{record['tier']} warning ({record['tier_rule_id']}): forecast "
                f"{record['forecast_id'][:24]} threatens objective "
                f"{record['objective_id'][:24]}")
    transitions = store.transitions_for(object_id)
    inferential = []
    if kind == "impact_path":
        for edge in record["edges"]:
            if edge["authority"] not in ("OBSERVED", "DERIVED"):
                inferential.append(f"{edge['from_id'][:18]} → {edge['to_id'][:18]}: "
                                   f"{edge['authority']} ({edge['note'][:100]})")
    if kind == "stakeholder_assessment":
        for position in record["positions"]:
            if position["kind"] == "INFERRED_INTEREST":
                inferential.append(f"inferred interest ({position['authority']}): "
                                   f"{position['statement'][:120]}")
    if record.get("authority") in ("SUPPORTED_INFERENCE", "MODEL_PROPOSAL",
                                   "ANALYST_ASSESSMENT"):
        inferential.append(f"the object itself is {record['authority']}, "
                           f"not a direct observation")
    if kind == "analytic_forecast":
        inferential.append(f"the probability was authored by {record['author']} "
                           f"({record['provenance_kind']}): "
                           f"{record['probability_basis'][:160]}")
    if kind == "strategic_warning":
        for component, why in record["component_basis"]:
            inferential.append(f"{component}: {why[:140]}")
    uncertainty = []
    if record.get("uncertainty_note"):
        uncertainty.append(record["uncertainty_note"])
    if kind == "analytic_forecast" and record.get("status") == "UPDATE_REQUIRED":
        uncertainty.append("UPDATE_REQUIRED: the probability is an unreviewed "
                           "number over changed evidence")
    if kind == "strategic_warning" \
            and record.get("evidence_confidence") in ("NONE", "WEAK"):
        uncertainty.append(f"warning evidence confidence is "
                           f"{record['evidence_confidence']}: the tier rule caps "
                           f"escalation accordingly")
    if basis.get("degraded_claim_count"):
        uncertainty.append(f"{basis['degraded_claim_count']} supporting claim(s) "
                           f"no longer CURRENT")
    if basis.get("unresolved_claim_ids"):
        uncertainty.append(f"{len(basis['unresolved_claim_ids'])} referenced claim "
                           f"id(s) resolve to no known claim and carry no weight")
    # read live from the review queue: an ambiguity opened after the last
    # version must still show
    if record.get("entity_object_id"):
        live_caveats = open_identity_caveats(store, record["entity_object_id"])
        if live_caveats:
            uncertainty.append(f"{len(live_caveats)} open identity ambiguity "
                               f"item(s) touch the assessed entity")
    elif record.get("identity_caveats"):
        uncertainty.append(f"{len(record['identity_caveats'])} open identity "
                           f"ambiguity item(s)")
    family_count = len(basis.get("origin_families", ()))
    if record.get("basis") is not None and family_count == 0:
        # only for kinds with a claim basis: a path descends through its
        # edges and assumptions instead
        uncertainty.insert(0, "NO EVIDENTIARY BASIS: no supporting claim resolves "
                              "to retained evidence — this object asserts nothing "
                              "the log can back")
    elif family_count == 1:
        uncertainty.append("all support descends from one origin family")
    for note in basis.get("coverage_notes", ()):
        uncertainty.append(f"coverage: {note}")
    # coverage gaps raised against discriminators over this object's claims
    basis_claims = set(basis.get("supporting_claim_ids", ()))
    if basis_claims:
        discriminator_ids = {d["discriminator_id"]
                             for d in store.records_of("discriminator")
                             if basis_claims & set(d["claim_ids"])}
        for item in store.open_review_items():
            if item["kind"] in ("COVERAGE_GAP", "EXPECTED_NOT_OBSERVED") \
                    and item["subject_id"] in discriminator_ids:
                uncertainty.append(f"{item['kind']}: {item['detail'][:140]}")
    return {
        "kind": kind, "id": object_id,
        "WHAT": {"assertion": what, "status": record.get("status", ""),
                 "authority": record.get("authority")
                 or record.get("path_authority", "")},
        "WHY": _claims_text(store, basis.get("supporting_claim_ids", ())),
        "AGAINST": _claims_text(store, basis.get("contradicting_claim_ids", ())),
        "SOURCE_BASIS": {
            "manifestation_reach": basis.get("manifestation_count", 0),
            "sources": basis.get("source_count", 0),
            "independent_origin_families": len(basis.get("origin_families", ())),
            "caveat": basis.get("note", ""),
        },
        "TEMPORAL": {
            "valid_from": record.get("valid_from") or basis.get("earliest_time", ""),
            "valid_to": record.get("valid_to"),
            "evidence_span": (basis.get("earliest_time", ""),
                              basis.get("latest_time", "")),
            "versions": len(store.analytic_versions(kind, object_id)),
            "changes": [{"type": t["transition_type"], "at": t["recorded_time"],
                         "detail": t["detail"][:160]} for t in transitions],
        },
        "INFERENCES": inferential,
        "UNCERTAINTY": uncertainty,
        "MISSION_EFFECT": _mission_effect(store, kind, object_id, index=index),
    }


def render_text(explanation: Mapping[str, Any]) -> str:
    """Render the structured explanation as lines of text."""
    if explanation.get("status") == "UNKNOWN_OBJECT":
        return f"unknown analytical object: {explanation['kind']}:{explanation['id']}"
    lines = [f"{explanation['kind']} {explanation['id'][:24]}",
             f"WHAT: {explanation['WHAT']['assertion']} "
             f"[{explanation['WHAT']['status']}; {explanation['WHAT']['authority']}]"]
    for claim in explanation["WHY"][:8]:
        lines.append(f"WHY: [{claim['state']}] {claim['statement'][:140]}")
    for claim in explanation["AGAINST"][:5]:
        lines.append(f"AGAINST: [{claim['state']}] {claim['statement'][:140]}")
    source = explanation["SOURCE_BASIS"]
    lines.append(f"SOURCE BASIS: {source['manifestation_reach']} manifestation(s), "
                 f"{source['independent_origin_families']} independent origin "
                 f"famil{'y' if source['independent_origin_families'] == 1 else 'ies'}")
    temporal = explanation["TEMPORAL"]
    lines.append(f"TEMPORAL: evidence {temporal['evidence_span'][0][:19]} → "
                 f"{temporal['evidence_span'][1][:19]}; "
                 f"{temporal['versions']} version(s), "
                 f"{len(temporal['changes'])} transition(s)")
    for inference in explanation["INFERENCES"][:5]:
        lines.append(f"INFERENCE: {inference}")
    for item in explanation["UNCERTAINTY"][:5]:
        lines.append(f"UNCERTAINTY: {item}")
    effect = explanation["MISSION_EFFECT"]
    if effect["dependent_analytics"] or effect["hypotheses"] or effect["requirements"]:
        lines.append(f"MISSION EFFECT: {len(effect['dependent_analytics'])} dependent "
                     f"object(s), {len(effect['hypotheses'])} hypothesis(es), "
                     f"{len(effect['requirements'])} requirement(s)")
    return "\n".join(lines)
