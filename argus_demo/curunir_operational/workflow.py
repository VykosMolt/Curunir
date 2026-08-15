"""Alert → analyst review → recommendation → human decision workflow.

Boundary rules enforced here: provider (SERVICE) actors can propose and the
workflow can materialize, but only a HUMAN actor with sufficient role — and
never the recommending provider — can record a decision; decisions bind to a
frozen, recomputable evidence snapshot hash over version-pinned references;
no path in this module executes any external action.
"""
from __future__ import annotations

from typing import Any, Mapping

from .access import AccessContext, Marking, ROLE_RANK, marking_from_record
from .canonical import digest_id, sha256
from .contracts import (Alert, AlertTransition, AnalystAction, DecisionRecord, EvidenceRef, ObjectVersion,
                        ProvenanceSummary, Recommendation, RelationshipVersion)
from .geometry import geometry_from_record
from .store import MissionDataStore


class WorkflowError(ValueError):
    pass


def _provenance_from(record: Mapping[str, Any]) -> ProvenanceSummary:
    return ProvenanceSummary(
        mode=record.get("mode", "OPERATIONAL"), source_ids=tuple(record.get("source_ids", ())),
        ingestion_ids=tuple(record.get("ingestion_ids", ())),
        transformation_ids=tuple(record.get("transformation_ids", ())),
        evidence=tuple(EvidenceRef(**{k: v for k, v in e.items() if k != "record_type"})
                       for e in record.get("evidence", ())),
    )


def pin_evidence_refs(store: MissionDataStore, refs: Mapping[str, Any] | list | tuple) -> tuple[str, ...]:
    """Pin plain object ids to their current version id so the snapshot is frozen."""
    versions: dict[str, int] = {}
    for record in store.records_of("object_version"):
        versions[record["object_id"]] = record["version"]
    pinned = []
    for ref in refs:
        if isinstance(ref, str) and "@v" not in ref and ref in versions:
            pinned.append(f"{ref}@v{versions[ref]}")
        else:
            pinned.append(str(ref))
    return tuple(pinned)


def evidence_snapshot_hash(store: MissionDataStore, refs: tuple[str, ...]) -> str:
    objects = {f"{r['object_id']}@v{r['version']}": r for r in store.records_of("object_version")}
    alerts = {r["alert_id"]: r for r in store.records_of("alert")}
    snapshot = {}
    for ref in refs:
        snapshot[ref] = objects.get(ref) or alerts.get(ref) or {"unresolved_reference": ref}
    return sha256(snapshot)


class WorkflowEngine:
    def __init__(self, store: MissionDataStore):
        self.store = store

    # ---- alerts -------------------------------------------------------------

    def _alert_status(self, alert_id: str) -> str | None:
        status = None
        for record in self.store.records_of("alert"):
            if record["alert_id"] == alert_id:
                status = record["status"]
        for record in self.store.records_of("alert_transition"):
            if record["alert_id"] == alert_id:
                status = record["to_status"]
        return status

    def raise_alert(self, content: Mapping[str, Any], *, marking: Marking, recorded_time: str,
                    actor: str) -> tuple[str, bool]:
        existing = self.store.find_alert_by_dedup(content["dedup_key"])
        if existing is not None:
            current = self._alert_status(existing) or "OPEN"
            transition = AlertTransition(
                transition_id=digest_id("altr", existing, recorded_time),
                alert_id=existing, from_status=current, to_status=current,
                actor_id=actor, actor_kind="SERVICE",
                note=f"retriggered: {content['trigger']}", recorded_time=recorded_time, marking=marking,
            )
            self.store.append("ALERT_TRANSITIONED", transition, recorded_time=recorded_time, actor=actor)
            return existing, False
        alert = Alert(
            alert_id=digest_id("alert", content["dedup_key"]),
            rule_id=content["rule_id"], rule_version=content["rule_version"], trigger=content["trigger"],
            affected_ids=tuple(content["affected_ids"]), evidence_refs=pin_evidence_refs(self.store, content["evidence_refs"]),
            quality_note=content.get("quality_note", ""), severity=content["severity"],
            severity_rationale=content["severity_rationale"], dedup_key=content["dedup_key"],
            expiry_condition=content.get("expiry_condition", ""), recorded_time=recorded_time, marking=marking,
        )
        self.store.append("ALERT_RAISED", alert, recorded_time=recorded_time, actor=actor)
        return alert.alert_id, True

    def transition_alert(self, alert_id: str, to_status: str, *, context: AccessContext, note: str,
                         recorded_time: str, marking: Marking) -> dict[str, Any]:
        current = self._alert_status(alert_id)
        if current is None:
            raise WorkflowError(f"unknown alert: {alert_id}")
        if to_status == "ACKNOWLEDGED" and context.actor_kind != "HUMAN":
            raise WorkflowError("only a human can acknowledge an alert")
        transition = AlertTransition(
            transition_id=digest_id("altr", alert_id, to_status, recorded_time),
            alert_id=alert_id, from_status=current, to_status=to_status,
            actor_id=context.actor_id, actor_kind=context.actor_kind, note=note,
            recorded_time=recorded_time, marking=marking,
        )
        self.store.append("ALERT_TRANSITIONED", transition, recorded_time=recorded_time, actor=context.actor_id)
        return transition.to_record()

    # ---- proposal materialization ------------------------------------------

    def materialize(self, proposal: Mapping[str, Any], *, actor_id: str, recorded_time: str) -> dict[str, Any]:
        inference = next((r for r in self.store.records_of("inference")
                          if r["inference_id"] == proposal["inference_id"]), None)
        if inference is not None and inference["validation"] != "VALID":
            raise WorkflowError(f"invalid provider output cannot be materialized: {proposal['inference_id']}")
        marking = marking_from_record(proposal["marking"])
        kind = proposal["proposal_type"]
        content = proposal["content"]
        result: dict[str, Any] = {"proposal_id": proposal["proposal_id"], "proposal_type": kind}
        if kind == "ALERT_CANDIDATE":
            alert_id, created = self.raise_alert(content, marking=marking, recorded_time=recorded_time, actor=actor_id)
            result.update(alert_id=alert_id, created=created)
        elif kind == "RELATIONSHIP_CANDIDATE":
            relationship_id = digest_id("rel", content["relation_type"], content["left"], content["right"])
            version = RelationshipVersion(
                relationship_id=relationship_id,
                version=self.store.next_relationship_version(relationship_id),
                relation_type=content["relation_type"], source_object_id=content["left"],
                target_object_id=content["right"], valid_from=None, valid_to=None,
                recorded_time=recorded_time, evidence_refs=(proposal["proposal_id"],),
                derivation="RULE", confidence="UNKNOWN", status="ACTIVE", marking=marking,
                provenance=ProvenanceSummary(mode="OPERATIONAL"), rationale=content.get("rationale", ""),
            )
            self.store.append("RELATIONSHIP_VERSION_APPENDED", version, recorded_time=recorded_time, actor=actor_id)
            result.update(relationship_id=relationship_id, version=version.version)
        elif kind == "STATE_CANDIDATE":
            versions = [r for r in self.store.records_of("object_version") if r["object_id"] == content["object_id"]]
            if not versions:
                raise WorkflowError(f"unknown object for state proposal: {content['object_id']}")
            current = versions[-1]
            if current["epistemic_state"] == content["epistemic_state"]:
                result.update(object_id=content["object_id"], unchanged=True)
                return result
            quality = dict(current.get("quality", {}))
            quality["information_credibility"] = "CONFLICTING_REPORTS"
            updated = ObjectVersion(
                object_id=current["object_id"], version=self.store.next_object_version(current["object_id"]),
                object_type=current["object_type"], lifecycle=current["lifecycle"],
                labels=tuple(current.get("labels", ())),
                external_refs=(), valid_from=current.get("valid_from"), valid_to=current.get("valid_to"),
                source_time=current.get("source_time"), time_precision=current.get("time_precision", "UNKNOWN"),
                recorded_time=recorded_time, geometry=geometry_from_record(current.get("geometry")),
                attributes={**current.get("attributes", {}), "dispute_note": content["reason"]},
                quality=quality, epistemic_state=content["epistemic_state"],
                marking=marking_from_record(current["marking"]), provenance=_provenance_from(current.get("provenance", {})),
            )
            self.store.append("OBJECT_VERSION_APPENDED", updated, recorded_time=recorded_time, actor=actor_id)
            result.update(object_id=current["object_id"], version=updated.version)
        elif kind == "RECOMMENDATION_CANDIDATE":
            inference = next((r for r in self.store.records_of("inference")
                              if r["inference_id"] == proposal["inference_id"]), None)
            provider_id = inference["model_id"] if inference else "UNKNOWN_PROVIDER"
            record = self.recommend(content, provider_id=provider_id, marking=marking,
                                    recorded_time=recorded_time, actor=actor_id)
            result.update(recommendation_id=record["recommendation_id"], created=record["created"])
        else:  # ASSESSMENT / ASSOCIATION_CANDIDATE stay proposals in V1
            result.update(materialized=False)
        return result

    # ---- recommendations and decisions -------------------------------------

    def recommend(self, content: Mapping[str, Any], *, provider_id: str, marking: Marking,
                  recorded_time: str, actor: str, alert_ids: tuple[str, ...] = ()) -> dict[str, Any]:
        recommendation_id = digest_id("rec", content["dedup_key"])
        for record in self.store.records_of("recommendation"):
            if record["recommendation_id"] == recommendation_id:
                return {**record, "created": False}
        refs = pin_evidence_refs(self.store, content["evidence_refs"])
        recommendation = Recommendation(
            recommendation_id=recommendation_id, alert_ids=tuple(alert_ids),
            action_kind=content["action_kind"], proposed_action=content["proposed_action"],
            rationale=content["rationale"], assumptions=tuple(content.get("assumptions", ())),
            alternatives=tuple(content.get("alternatives", ())), evidence_refs=refs,
            evidence_snapshot_hash=evidence_snapshot_hash(self.store, refs),
            uncertainty=content.get("uncertainty", ""), expected_benefit=content.get("expected_benefit", ""),
            potential_risk=content.get("potential_risk", ""), expiry=content.get("expiry"),
            required_role=content.get("required_role", "ANALYST"), provider_id=provider_id,
            recorded_time=recorded_time, marking=marking,
        )
        self.store.append("RECOMMENDATION_RECORDED", recommendation, recorded_time=recorded_time, actor=actor)
        return {**recommendation.to_record(), "created": True}

    def analyst_action(self, *, context: AccessContext, kind: str, subject_kind: str, subject_id: str,
                       note: str, recorded_time: str, marking: Marking) -> dict[str, Any]:
        if context.actor_kind != "HUMAN":
            raise WorkflowError("analyst actions require a human actor")
        action = AnalystAction(
            action_id=digest_id("act", context.actor_id, kind, subject_id, recorded_time),
            actor_id=context.actor_id, actor_kind=context.actor_kind, actor_roles=tuple(context.roles),
            kind=kind, subject_kind=subject_kind, subject_id=subject_id, note=note,
            recorded_time=recorded_time, marking=marking,
        )
        self.store.append("ANALYST_ACTION_RECORDED", action, recorded_time=recorded_time, actor=context.actor_id)
        return action.to_record()

    def enact_decision_effect(self, decision_id: str, *, object_id: str, attributes_patch: Mapping[str, Any],
                              rationale: str, recorded_time: str, actor: str,
                              epistemic_state: str = "INFERRED") -> dict[str, Any]:
        """Recorded cross-workbench consequence of a human decision: a new
        object version whose attributes carry the decision basis. Never called
        by providers; only enacts ACCEPTED or MODIFIED decisions; the original
        decision and its evidence snapshot stay inspectable."""
        decision = next((d for d in self.store.records_of("decision") if d["decision_id"] == decision_id), None)
        if decision is None:
            raise WorkflowError(f"unknown decision: {decision_id}")
        if decision["state"] not in ("ACCEPTED", "MODIFIED"):
            raise WorkflowError(f"decision {decision_id} is {decision['state']}; only accepted or modified "
                                "decisions can produce effects")
        versions = [r for r in self.store.records_of("object_version") if r["object_id"] == object_id]
        if not versions:
            raise WorkflowError(f"unknown object for decision effect: {object_id}")
        current = versions[-1]
        updated = ObjectVersion(
            object_id=object_id, version=self.store.next_object_version(object_id),
            object_type=current["object_type"], lifecycle=current["lifecycle"],
            labels=tuple(current.get("labels", ())), external_refs=(),
            valid_from=recorded_time, valid_to=current.get("valid_to"),
            source_time=current.get("source_time"), time_precision=current.get("time_precision", "UNKNOWN"),
            recorded_time=recorded_time, geometry=geometry_from_record(current.get("geometry")),
            attributes={**current.get("attributes", {}), **attributes_patch,
                        "decision_basis": decision_id, "effect_note": rationale},
            quality=dict(current.get("quality", {})), epistemic_state=epistemic_state,
            marking=marking_from_record(current["marking"]),
            provenance=_provenance_from(current.get("provenance", {})),
        )
        self.store.append("OBJECT_VERSION_APPENDED", updated, recorded_time=recorded_time, actor=actor)
        return updated.to_record()

    def decide(self, recommendation_id: str, *, context: AccessContext, state: str, rationale: str,
               recorded_time: str, marking: Marking, modification: str = "") -> dict[str, Any]:
        recommendation = None
        for record in self.store.records_of("recommendation"):
            if record["recommendation_id"] == recommendation_id:
                recommendation = record
        if recommendation is None:
            raise WorkflowError(f"unknown recommendation: {recommendation_id}")
        if context.actor_kind != "HUMAN":
            raise WorkflowError("providers and services cannot record human decisions")
        if context.actor_id == recommendation["provider_id"]:
            raise WorkflowError("the recommending provider cannot record the decision")
        required = recommendation["required_role"]
        if context.max_role_rank < ROLE_RANK[required]:
            raise WorkflowError(f"decision requires role {required}")
        recomputed = evidence_snapshot_hash(self.store, tuple(recommendation["evidence_refs"]))
        if recomputed != recommendation["evidence_snapshot_hash"]:
            raise WorkflowError("evidence snapshot hash mismatch: snapshot is not stable")
        role = max(context.roles, key=lambda r: ROLE_RANK[r])
        decision = DecisionRecord(
            decision_id=digest_id("dec", recommendation_id, context.actor_id, recorded_time),
            recommendation_id=recommendation_id, actor_id=context.actor_id, actor_role=role,
            state=state, modification=modification, rationale=rationale,
            evidence_snapshot_hash=recommendation["evidence_snapshot_hash"],
            recorded_time=recorded_time, marking=marking,
        )
        self.store.append("DECISION_RECORDED", decision, recorded_time=recorded_time, actor=context.actor_id)
        return decision.to_record()
