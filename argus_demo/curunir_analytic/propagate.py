"""Semantic change → analytical update, incrementally.

The semantic plane already interprets watch-detected byte changes into typed
SemanticChangeRecords with affected objects and claims. This module carries
those changes one layer up: through the reverse-dependency index it finds
exactly the analytical objects resting on the changed state, refreshes only
those, questions assumptions whose support degraded, refreshes touched
hypotheses through the existing engine, and raises evidence-bound analytical
alerts that explain what changed, which analytical object moved, and which
mission state is affected.

Everything is idempotent: refreshes append only on material change and
transitions are keyed by their cause, so re-running after a crash completes
the propagation instead of duplicating it.
"""
from __future__ import annotations

from typing import Any, Mapping

from argus.source_intelligence.models import digest_id
from curunir_operational.workflow import WorkflowEngine
from curunir_semantic.hypotheses import refresh_hypotheses_for_claims
from curunir_semantic.worldmodel import IntegrationContext

from .basis import DEGRADED_CLAIM_STATES
from .forecasts import refresh_forecast
from .impact import mark_objective_exposed, question_assumption, refresh_path
from .narratives import refresh_narrative
from .stakeholders import refresh_assessment
from .store import AnalyticStore
from .substrate import AnalyticContext, DependencyIndex, record_transition
from .themes import refresh_theme

# change classes that do not touch analytical state
_INERT_CLASSES = ("SEMANTICALLY_UNCHANGED",)

# analytical transitions worth an alert when they fire
_ALERTABLE = {
    ("analytic_theme", "CONTRADICTION_ADDED"), ("analytic_theme", "STALE"),
    ("analytic_theme", "WEAKENED"), ("analytic_theme", "SOURCE_DIVERSITY_CHANGED"),
    ("analytic_narrative", "CONTESTED"), ("analytic_narrative", "ORIGIN_REVISED"),
    ("stakeholder_assessment", "BASIS_DEGRADED"),
    ("stakeholder_assessment", "POSITION_CHANGED"),
    ("impact_path", "STALE"), ("impact_path", "ASSUMPTION_INVALIDATED"),
    ("mission_objective", "EXPOSED"),
    ("analytic_assumption", "QUESTIONED"), ("analytic_assumption", "INVALIDATED"),
    ("influence_assertion", "WEAKENED"),
    ("response_option", "EVIDENCE_DEGRADED"),
    ("historical_episode", "EVIDENCE_DEGRADED"),
    ("historical_analogue", "EVIDENCE_DEGRADED"),
    ("analytic_forecast", "BASIS_DEGRADED"),
    ("analytic_forecast", "RESOLVED_TRUE"), ("analytic_forecast", "RESOLVED_FALSE"),
    ("strategic_warning", "ESCALATED"),
}

_REFRESHERS = {
    "analytic_theme": refresh_theme,
    "analytic_narrative": refresh_narrative,
}


def _refresh_degraded_basis(ctx: AnalyticContext, kind: str, object_id: str,
                            claim_ids, transition_type: str) -> None:
    """Basis-degradation watch for the kinds without a richer refresher:
    when supporting evidence of an influence assertion, response option,
    episode or analogue leaves CURRENT, the fact is recorded as a typed
    transition (idempotent per degraded set), never silently ignored."""
    store = ctx.store
    record = store.current_analytics(kind).get(object_id)
    if record is None:
        return
    degraded = sorted(f"{claim_id}:{store.claim_state(claim_id)}"
                      for claim_id in claim_ids
                      if claim_id in store.current_claims()
                      and store.claim_state(claim_id) in DEGRADED_CLAIM_STATES)
    if not degraded:
        return
    record_transition(
        ctx, subject_kind=kind, subject_id=object_id,
        transition_type=transition_type,
        detail=f"supporting evidence degraded: {'; '.join(degraded)[:250]}",
        caused_by=digest_id("degraded", kind, object_id, *degraded),
        evidence_refs=tuple(entry.split(":")[0] for entry in degraded)[:5])


def _refresh_one(ctx: AnalyticContext, kind: str, object_id: str,
                 caused_by: str) -> Mapping[str, Any] | None:
    store = ctx.store
    if kind in _REFRESHERS:
        return _REFRESHERS[kind](ctx, object_id, caused_by=caused_by)
    if kind == "stakeholder_assessment":
        return refresh_assessment(ctx, object_id, caused_by=caused_by)
    if kind == "analytic_forecast":
        return refresh_forecast(ctx, object_id, caused_by=caused_by)
    if kind == "impact_path":
        return refresh_path(ctx, object_id, caused_by=caused_by)
    if kind == "influence_assertion":
        record = store.current_analytics(kind).get(object_id)
        if record is not None:
            _refresh_degraded_basis(ctx, kind, object_id, record["claim_ids"],
                                    "WEAKENED")
        return record
    if kind == "response_option":
        record = store.current_analytics(kind).get(object_id)
        if record is not None:
            _refresh_degraded_basis(ctx, kind, object_id, record["claim_ids"],
                                    "EVIDENCE_DEGRADED")
        return record
    if kind == "historical_episode":
        record = store.current_analytics(kind).get(object_id)
        if record is not None:
            _refresh_degraded_basis(
                ctx, kind, object_id,
                tuple(record["claim_ids"]) + tuple(record["outcome_claim_ids"]),
                "EVIDENCE_DEGRADED")
        return record
    if kind == "historical_analogue":
        record = store.current_analytics(kind).get(object_id)
        if record is not None:
            episode = store.current_analytics("historical_episode").get(
                record["episode_id"], {})
            _refresh_degraded_basis(
                ctx, kind, object_id,
                tuple(episode.get("claim_ids", ()))
                + tuple(episode.get("outcome_claim_ids", ())),
                "EVIDENCE_DEGRADED")
        return record
    return None


def propagate_semantic_changes(ctx: AnalyticContext, *,
                               raise_alerts: bool = True) -> list[dict[str, Any]]:
    """Carry every recorded semantic change into the analytical layer.

    Changes are propagated per affected object over the object's FULL set of
    pending changes: the refreshers diff against live state, so attributing
    the accumulated movement to whichever change happened to sit first in the
    log would leave later, materially different changes permanently inert and
    falsify causality. Each refresh is caused by the change set as a whole,
    and every change in the set is named in the transition detail.

    Safe to call repeatedly: already-propagated change sets fall through as
    no-ops because every downstream write is idempotent by cause.
    """
    store: AnalyticStore = ctx.store
    index = DependencyIndex(store)
    assumptions = store.current_assumptions()

    changes = [c for c in store.records_of("semantic_change")
               if c["change_class"] not in _INERT_CLASSES]
    by_object: dict[tuple[str, str], list[Mapping[str, Any]]] = {}
    for change in changes:
        for ref in index.affected_by(claim_ids=change["affected_claim_ids"],
                                     object_ids=change["affected_object_ids"]):
            by_object.setdefault(ref, []).append(change)

    outcomes = []
    all_touched_claims: set[str] = set()
    for (kind, object_id), object_changes in sorted(by_object.items()):
        change_ids = sorted(c["change_id"] for c in object_changes)
        changeset = digest_id("changeset", *change_ids)
        latest = object_changes[-1]
        classes = sorted({c["change_class"] for c in object_changes})
        summary = (f"{len(object_changes)} semantic change(s) "
                   f"[{', '.join(classes)}]: "
                   + "; ".join(c["change_id"][:18] for c in object_changes))
        touched_claims = {claim_id for c in object_changes
                          for claim_id in c["affected_claim_ids"]}
        all_touched_claims |= touched_claims
        transitions_before = len(store.records_of("analytic_transition"))
        if kind == "analytic_assumption":
            assumption = assumptions.get(object_id)
            if assumption and assumption["status"] == "HELD":
                # caused per triggering CHANGE, not per changeset: a later
                # unrelated change must not re-fire the same questioning
                for change in object_changes:
                    relevant = [
                        claim_id for claim_id in assumption["supporting_claim_ids"]
                        if claim_id in change["affected_claim_ids"]
                        and (store.claim_state(claim_id) in DEGRADED_CLAIM_STATES
                             or change["change_class"] in ("SOURCE_RETRACTION",
                                                           "SOURCE_CORRECTION",
                                                           "VALUE_CHANGED",
                                                           "ENTITY_ATTRIBUTE_CHANGED"))]
                    if relevant:
                        question_assumption(
                            ctx, object_id, caused_by=change["change_id"],
                            reason=f"{change['change_class']}: "
                                   f"{change['detail'][:200]}")
                        break
        elif kind == "mission_objective":
            for change in object_changes:
                mark_objective_exposed(
                    ctx, object_id,
                    detail=f"declared dependency affected by "
                           f"{change['change_class']}: {change['detail'][:160]}",
                    caused_by=change["change_id"])
        else:
            _refresh_one(ctx, kind, object_id, caused_by=changeset)
        new_transitions = store.records_of("analytic_transition")[transitions_before:]
        alerts = _raise_analytic_alerts(ctx, latest, new_transitions,
                                        change_count=len(object_changes),
                                        index=index) \
            if raise_alerts else []
        if new_transitions:
            outcomes.append({
                "changeset": changeset,
                "change_ids": change_ids,
                "change_classes": classes,
                "affected_object": (kind, object_id),
                "transitions": [(t["subject_kind"], t["transition_type"])
                                for t in new_transitions],
                "alerts": alerts,
            })
    # hypotheses refresh over the union of touched claims, reported when a
    # new hypothesis version actually landed
    hypothesis_records_before = len(store.records_of("hypothesis"))
    if all_touched_claims:
        integration = IntegrationContext(store=store, actor=ctx.actor,
                                         marking=ctx.marking, now_fn=ctx.now_fn)
        refresh_hypotheses_for_claims(integration, all_touched_claims)
    hypotheses = store.records_of("hypothesis")[hypothesis_records_before:]
    if hypotheses:
        outcomes.append({
            "changeset": "hypotheses",
            "hypotheses_refreshed": sorted({h["hypothesis_id"] for h in hypotheses}),
        })
    # identity ambiguity opens without a semantic change; keep assessments'
    # caveats in step with the live review queue on every propagation pass
    from .stakeholders import refresh_identity_caveats
    caveats = refresh_identity_caveats(ctx, caused_by="propagate")
    if caveats:
        outcomes.append({
            "changeset": "identity-caveats",
            "assessments_refreshed": [a["assessment_id"] for a in caveats],
        })
    # the forecast plane runs a full pass every propagation: new evidence can
    # fire indicators (which execute pre-authorized effects), and any moved
    # probability, basis or clock re-derives the standing warnings. Every step
    # is idempotent, so a quiet pass appends nothing.
    from .indicators import check_indicators
    from .warning import refresh_warnings
    transitions_before = len(store.records_of("analytic_transition"))
    check_indicators(ctx)
    for forecast in sorted(store.current_forecasts().values(),
                           key=lambda f: f["forecast_id"]):
        # HORIZON_PASSED is included: a coverage-blocked forecast is WAITING
        # for executions that produce no semantic change, so only this pass
        # can notice the coverage arriving and complete the resolution
        if forecast["status"] in ("OPEN", "UPDATE_REQUIRED", "HORIZON_PASSED"):
            refresh_forecast(ctx, forecast["forecast_id"],
                             caused_by="propagate")
    refresh_warnings(ctx)
    plane_transitions = store.records_of("analytic_transition")[transitions_before:]
    if plane_transitions:
        outcomes.append({
            "changeset": "forecast-plane",
            "transitions": [(t["subject_kind"], t["transition_type"])
                            for t in plane_transitions],
            "alerts": _raise_forecast_plane_alerts(ctx, plane_transitions)
            if raise_alerts else [],
        })
    return outcomes


def _raise_forecast_plane_alerts(ctx: AnalyticContext,
                                 transitions: list[Mapping[str, Any]]) -> list[str]:
    """Alerts for forecast-plane movement not attributable to a single
    semantic change: warning escalations and degraded forecast bases.
    Dedup-keyed by transition, so a re-run raises nothing twice."""
    engine = WorkflowEngine(ctx.store)
    raised = []
    for transition in transitions:
        key = (transition["subject_kind"], transition["transition_type"])
        if key not in _ALERTABLE:
            continue
        alert_id, created = engine.raise_alert({
            "rule_id": "forecast-plane", "rule_version": "0.1",
            "trigger": (f"{transition['transition_type']} on "
                        f"{transition['subject_kind']} "
                        f"{transition['subject_id'][:20]}: "
                        f"{transition['detail'][:400]}"),
            "affected_ids": (transition["subject_id"],),
            "evidence_refs": tuple(transition.get("evidence_refs", ()))
            or (transition["transition_id"],),
            "severity": "HIGH"
            if transition["transition_type"] == "ESCALATED" else "WARNING",
            "severity_rationale": f"forecast-plane "
                                  f"{transition['transition_type']}",
            "dedup_key": digest_id("analert", transition["transition_id"]),
        }, marking=ctx.marking, recorded_time=ctx.now_fn(), actor=ctx.actor)
        if created:
            raised.append(alert_id)
    return raised


def _raise_analytic_alerts(ctx: AnalyticContext, change: Mapping[str, Any],
                           transitions: list[Mapping[str, Any]], *,
                           change_count: int = 1,
                           index: DependencyIndex | None = None) -> list[str]:
    """Analytical alerts explain the full chain: the evidence change, the
    analytical object it moved, and the mission state affected."""
    store = ctx.store
    engine = WorkflowEngine(store)
    if index is None:
        index = DependencyIndex(store)
    raised = []
    batch_note = "" if change_count == 1 else \
        f" (latest of {change_count} changes in this set)"
    for transition in transitions:
        key = (transition["subject_kind"], transition["transition_type"])
        if key not in _ALERTABLE:
            continue
        dependents = index.by_analytic.get(
            (transition["subject_kind"], transition["subject_id"]), set())
        mission_effect = ", ".join(f"{kind}:{ref[:18]}"
                                   for kind, ref in sorted(dependents)) or "none recorded"
        body = (f"ANALYTICAL {transition['transition_type']} on "
                f"{transition['subject_kind']} {transition['subject_id'][:20]}\n"
                f"what changed: {change['change_class']}: "
                f"{change['detail'][:200]}{batch_note}\n"
                f"analytical effect: {transition['detail'][:250]}\n"
                f"dependent analytical state: {mission_effect}\n"
                f"evidence: prior {change['prior_observation_id'][:18] or '(none)'} → "
                f"current {change['current_observation_id'][:18] or '(none)'}")
        evidence = tuple(x for x in (change["prior_observation_id"],
                                     change["current_observation_id"],
                                     change["current_manifestation_id"]) if x) \
            or (change["change_id"],)
        alert_id, created = engine.raise_alert({
            "rule_id": "analytic-change", "rule_version": "0.1",
            "trigger": body[:900],
            "affected_ids": (transition["subject_id"],),
            "evidence_refs": evidence,
            "severity": "HIGH" if transition["transition_type"] in
            ("ASSUMPTION_INVALIDATED", "EXPOSED", "INVALIDATED") else "WARNING",
            "severity_rationale": f"analytical {transition['transition_type']} caused "
                                  f"by semantic {change['change_class']}",
            "dedup_key": digest_id("analert", transition["transition_id"]),
        }, marking=ctx.marking, recorded_time=ctx.now_fn(), actor=ctx.actor)
        if created:
            raised.append(alert_id)
    return raised
