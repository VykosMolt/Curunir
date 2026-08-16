"""The workbench command layer: every mutation is an attributable act routed
through the canonical plane functions.

The browser cannot mutate truth: it names a command, the server resolves the
actor from authentication, validates authority, and calls the same store
functions the rest of Curunír uses. Human-only guards (task closure, model
candidate acceptance, forecast authorship, report approval) live in the
canonical layers and are surfaced — never bypassed — here. Stale writes
raise conflicts; nothing silently overwrites another analyst's work.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping

from argus.source_intelligence.models import digest_id
from curunir_analytic.contracts import ResolutionRule
from curunir_analytic.forecasts import (create_forecast, resolve_forecast_human,
                                        update_probability)
from curunir_analytic.substrate import AnalyticContext, resolve_candidate
from curunir_fabric.executor import ExecutionContext, RateGate
from curunir_fabric.registry import load_registry
from curunir_fabric.watch import register_watch, retire_watch
from curunir_operational.access import (AccessContext, Marking, can_view,
                                        marking_from_record)
from curunir_operational.missions import MissionWorkflow
from curunir_operational.workflow import WorkflowEngine
from curunir_semantic.collection import assign_human_route, execute_route
from curunir_semantic.contracts import ReviewItem
from curunir_semantic.hypotheses import record_hypothesis
from curunir_semantic.pipeline import SemanticPipeline

from argus.source_intelligence.custody import SourceCustodyStore

from . import annotations as annotations_module
from . import reports as reports_module
from .projections import MissionProjection
from .store import WorkbenchStore


from .errors import Conflict, NotFound


class CommandError(ValueError):
    pass


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class CommandContext:
    """One authenticated actor operating one mission root."""
    store: WorkbenchStore
    root: Path
    context: AccessContext
    marking: Marking
    now_fn: Callable[[], str] = _utc_now

    @property
    def actor(self) -> str:
        return self.context.actor_id

    @property
    def actor_kind(self) -> str:
        return self.context.actor_kind

    def projection(self) -> MissionProjection:
        return MissionProjection(self.store, self.context)

    def analytic(self) -> AnalyticContext:
        return AnalyticContext(store=self.store, actor=self.actor,
                               marking=self.marking, now_fn=self.now_fn)

    def pipeline(self) -> SemanticPipeline:
        return SemanticPipeline(store=self.store, custody_root=self.root / "custody",
                                actor=self.actor, marking=self.marking,
                                now_fn=self.now_fn)

    def require_visible_marking(self) -> None:
        """An actor may only author records it could itself view — a command
        can never create state above its own access."""
        if not can_view(self.marking, self.context):
            raise PermissionError("actor cannot write records outside its own access")


# ---- annotations -------------------------------------------------------------

def annotate(ctx: CommandContext, *, target_kind: str, target_id: str,
             kind: str, text: str, reply_to: str = "",
             anchor_ref: str = "") -> dict:
    ctx.require_visible_marking()
    return annotations_module.create_annotation(
        ctx.store, ctx.projection(), actor=ctx.actor, marking=ctx.marking,
        now=ctx.now_fn(), target_kind=target_kind, target_id=target_id,
        kind=kind, text=text, reply_to=reply_to, anchor_ref=anchor_ref)


def resolve_annotation(ctx: CommandContext, annotation_id: str, *,
                       expected_version: int, status: str, note: str) -> dict:
    if ctx.projection().get("workbench_annotation", annotation_id) is None:
        raise NotFound(f"unknown annotation: {annotation_id}")
    try:
        return annotations_module.resolve_annotation(
            ctx.store, annotation_id, actor=ctx.actor, marking=ctx.marking,
            now=ctx.now_fn(), expected_version=expected_version,
            status=status, note=note)
    except annotations_module.AnnotationConflict as error:
        raise Conflict(str(error)) from error


# ---- mission workflow (requirements / tasks) --------------------------------

def open_requirement(ctx: CommandContext, *, question: str, priority: str,
                     mission_context: str, rationale: str,
                     required_evidence_type: str = "OPEN_SOURCE",
                     owning_role: str = "ANALYST", closure_criteria: str = "",
                     due_time: str | None = None,
                     affected_ids: tuple[str, ...] = ()) -> dict:
    workflow = MissionWorkflow(ctx.store)
    return workflow.open_requirement(
        mission_context=mission_context, question=question,
        affected_ids=affected_ids, priority=priority, rationale=rationale,
        required_evidence_type=required_evidence_type, owning_role=owning_role,
        closure_criteria=closure_criteria or "answered with cited evidence",
        due_time=due_time, recorded_time=ctx.now_fn(),
        marking=ctx.marking, actor=ctx.actor)


def assign_task(ctx: CommandContext, *, assigned_actor: str, task_type: str,
                required_action: str, affected_ids: tuple[str, ...] = (),
                assigned_role: str = "ANALYST", due_time: str | None = None,
                depends_on: tuple[str, ...] = ()) -> dict:
    workflow = MissionWorkflow(ctx.store)
    return workflow.assign_task(
        assigned_role=assigned_role, assigned_actor=assigned_actor,
        task_type=task_type, affected_ids=affected_ids,
        required_action=required_action, due_time=due_time,
        depends_on=depends_on, recorded_time=ctx.now_fn(),
        marking=ctx.marking, actor=ctx.actor)


def transition_workflow(ctx: CommandContext, *, subject_kind: str, subject_id: str,
                        to_status: str, evidence_refs: tuple[str, ...] = (),
                        note: str = "") -> dict:
    workflow = MissionWorkflow(ctx.store)
    return workflow.transition(
        subject_kind, subject_id, to_status, actor_id=ctx.actor,
        actor_kind=ctx.actor_kind, evidence_refs=evidence_refs, note=note,
        recorded_time=ctx.now_fn(), marking=ctx.marking)


# ---- review ------------------------------------------------------------------

def resolve_review_item(ctx: CommandContext, item_id: str, *,
                        expected_version: int, status: str, note: str) -> dict:
    """Disposition a contradiction/stale-basis/processing review item.
    DISMISSED keeps the item and its note in history — dismissal never erases
    the contradiction."""
    if ctx.actor_kind != "HUMAN":
        raise PermissionError("review disposition is a human act")
    if status not in ("RESOLVED", "DISMISSED"):
        raise CommandError(f"invalid review disposition: {status}")
    projection = ctx.projection()
    current = projection.get("review_item", item_id)
    if current is None:
        raise NotFound(f"unknown review item: {item_id}")
    if current["status"] != "OPEN":
        raise CommandError(f"review item is already {current['status']}")
    if current["version"] != expected_version:
        raise Conflict(f"review item is at version {current['version']}, "
                       f"you saw {expected_version}")
    raw = ctx.store.latest_by_id("review_item", "item_id")[item_id]
    record = ReviewItem(
        item_id=item_id, kind=raw["kind"], subject_kind=raw["subject_kind"],
        subject_id=raw["subject_id"], detail=raw["detail"],
        evidence_refs=tuple(raw["evidence_refs"]), status=status,
        resolution_note=note, recorded_time=ctx.now_fn(),
        # a disposition never re-classifies the item it dispositions
        marking=marking_from_record(raw["marking"]),
        version=raw["version"] + 1)
    try:
        ctx.store.append("REVIEW_ITEM_RECORDED", record,
                         recorded_time=record.recorded_time, actor=ctx.actor)
    except ValueError as error:
        raise Conflict(str(error)) from error
    return record.to_record()


def resolve_model_proposal(ctx: CommandContext, proposal_id: str, *,
                           accept: bool, note: str = "") -> dict:
    latest = ctx.store.latest_by_id("analytical_proposal", "proposal_id").get(proposal_id)
    if latest is None or not can_view(latest.get("marking"), ctx.context):
        raise NotFound(f"unknown analytical proposal: {proposal_id}")
    return resolve_candidate(ctx.analytic(), proposal_id, accept=accept,
                             actor_id=ctx.actor, actor_kind=ctx.actor_kind,
                             note=note)


def decide_recommendation(ctx: CommandContext, recommendation_id: str, *,
                          state: str, rationale: str) -> dict:
    engine = WorkflowEngine(ctx.store)
    return engine.decide(recommendation_id, context=ctx.context, state=state,
                         rationale=rationale, recorded_time=ctx.now_fn(),
                         marking=ctx.marking)


# ---- hypotheses --------------------------------------------------------------

def create_hypothesis(ctx: CommandContext, *, statement: str, case_id: str,
                      assumptions: tuple[str, ...] = (),
                      unknowns: tuple[str, ...] = ()) -> dict:
    return record_hypothesis(ctx.store, statement=statement, case_id=case_id,
                             assumptions=assumptions, unknowns=unknowns,
                             analyst_or_provider=ctx.actor, now=ctx.now_fn(),
                             actor=ctx.actor, marking=ctx.marking)


def assess_hypothesis(ctx: CommandContext, hypothesis_id: str, *,
                      expected_version: int, status: str, rationale: str) -> dict:
    """A recorded analyst assessment: re-appends the hypothesis with the
    assessed status and an attributable history entry. Disagreement from
    another analyst is dissent (an annotation), never an overwrite."""
    if ctx.actor_kind != "HUMAN":
        raise PermissionError("hypothesis assessment is a human act")
    from curunir_semantic.contracts import HYPOTHESIS_STATUSES, HypothesisRecord
    if status not in HYPOTHESIS_STATUSES:
        raise CommandError(f"invalid hypothesis status: {status}")
    if not rationale.strip():
        raise CommandError("an assessment requires its rationale")
    if ctx.projection().get("hypothesis", hypothesis_id) is None:
        raise NotFound(f"unknown hypothesis: {hypothesis_id}")
    raw = ctx.store.current_hypotheses()[hypothesis_id]
    if raw["version"] != expected_version:
        raise Conflict(f"hypothesis is at version {raw['version']}, "
                       f"you saw {expected_version}")
    record = HypothesisRecord(**{
        **{k: v for k, v in raw.items() if k != "record_type"},
        "assumptions": tuple(raw["assumptions"]),
        "unknowns": tuple(raw["unknowns"]),
        "supporting_claim_ids": tuple(raw["supporting_claim_ids"]),
        "contradicting_claim_ids": tuple(raw["contradicting_claim_ids"]),
        "unresolved_claim_ids": tuple(raw["unresolved_claim_ids"]),
        "discriminator_ids": tuple(raw["discriminator_ids"]),
        "status": status,
        "review_state": "HUMAN_ASSESSED",
        "history": tuple(raw["history"]) + (
            f"ANALYST_ASSESSED:{ctx.actor}:{status}:{rationale[:200]}",),
        "version": raw["version"] + 1,
        "recorded_time": ctx.now_fn(),
        "marking": marking_from_record(raw["marking"])})
    try:
        ctx.store.append("HYPOTHESIS_RECORDED", record,
                         recorded_time=record.recorded_time, actor=ctx.actor)
    except ValueError as error:
        raise Conflict(str(error)) from error
    return record.to_record()


# ---- forecasts ---------------------------------------------------------------

def author_forecast(ctx: CommandContext, *, question: str, outcome_semantics: str,
                    horizon_time: str, probability: float, probability_basis: str,
                    proposition_refs: tuple[tuple[str, str], ...],
                    resolution: Mapping[str, Any], domain: str,
                    assumption_ids: tuple[str, ...] = ()) -> dict:
    """An authored probability: the workbench offers no machine-derived
    numbers and no recalculation. Provenance is the human analyst."""
    if ctx.actor_kind != "HUMAN":
        raise PermissionError("a forecast probability is authored by a human "
                              "(or enters as a gated model candidate, not here)")
    rule = resolution if isinstance(resolution, ResolutionRule) else ResolutionRule(
        **{k: (tuple(v) if isinstance(v, list) else v)
           for k, v in resolution.items() if k != "record_type"})
    return create_forecast(ctx.analytic(), question=question,
                           outcome_semantics=outcome_semantics,
                           proposition_refs=proposition_refs,
                           horizon_time=horizon_time, resolution=rule,
                           probability=probability,
                           probability_basis=probability_basis,
                           assumption_ids=assumption_ids, author=ctx.actor,
                           domain=domain, provenance_kind="ANALYST")


def move_forecast(ctx: CommandContext, forecast_id: str, *, expected_version: int,
                  probability: float, probability_basis: str, change_reason: str,
                  evidence_refs: tuple[str, ...] = ()) -> dict:
    if ctx.actor_kind != "HUMAN":
        raise PermissionError("a probability movement is authored by a human")
    current = ctx.projection().get("analytic_forecast", forecast_id)
    if current is None:
        raise NotFound(f"unknown forecast: {forecast_id}")
    if current["version"] != expected_version:
        raise Conflict(f"forecast is at version {current['version']}, "
                       f"you saw {expected_version}")
    try:
        return update_probability(ctx.analytic(), forecast_id,
                                  probability=probability,
                                  reason=f"{change_reason} — {probability_basis}".strip(" —"),
                                  evidence_refs=evidence_refs,
                                  actor_id=ctx.actor, actor_kind=ctx.actor_kind,
                                  provenance_kind="ANALYST")
    except ValueError as error:
        if "version" in str(error):
            raise Conflict(str(error)) from error
        raise


def resolve_forecast(ctx: CommandContext, forecast_id: str, *, outcome: str,
                     rationale: str, evidence_refs: tuple[str, ...]) -> dict:
    if ctx.actor_kind != "HUMAN":
        raise PermissionError("human forecast resolution requires a human actor")
    if ctx.projection().get("analytic_forecast", forecast_id) is None:
        raise NotFound(f"unknown forecast: {forecast_id}")
    return resolve_forecast_human(ctx.analytic(), forecast_id, outcome=outcome,
                                  evidence_refs=evidence_refs, note=rationale,
                                  actor_id=ctx.actor, actor_kind=ctx.actor_kind)


def link_hypothesis_claim(ctx: CommandContext, hypothesis_id: str, *,
                          claim_id: str, stance: str, rationale: str) -> dict:
    from curunir_semantic.hypotheses import link_claim
    projection = ctx.projection()
    if projection.get("hypothesis", hypothesis_id) is None:
        raise NotFound(f"unknown hypothesis: {hypothesis_id}")
    if projection.get("semantic_claim", claim_id) is None:
        raise NotFound(f"unknown claim: {claim_id}")
    return link_claim(ctx.store, hypothesis_id, claim_id, stance,
                      rationale=rationale, now=ctx.now_fn(), actor=ctx.actor,
                      marking=ctx.marking)


def project_forecast_warning(ctx: CommandContext, forecast_id: str, *,
                             objective_id: str) -> dict:
    """Project a forecast onto an objective through the canonical warning
    engine — the tier comes from the named rule, never from the caller."""
    from curunir_analytic.warning import project_warning
    projection = ctx.projection()
    if projection.get("analytic_forecast", forecast_id) is None:
        raise NotFound(f"unknown forecast: {forecast_id}")
    if projection.get("mission_objective", objective_id) is None:
        raise NotFound(f"unknown objective: {objective_id}")
    return project_warning(ctx.analytic(), forecast_id=forecast_id,
                           objective_id=objective_id)


# ---- collection --------------------------------------------------------------

def launch_route(ctx: CommandContext, route_id: str, *,
                 transports: Mapping[str, Any] | None = None) -> dict:
    """Execute one automatable EIV-ranked collection route through the OSINT
    fabric — acquisition, custody, semantic understanding, hypothesis and
    analytic refresh — exactly the canonical path, no research script."""
    projection = ctx.projection()
    if projection.get("collection_route", route_id) is None:
        raise NotFound(f"unknown collection route: {route_id}")
    route = ctx.store.latest_by_id("collection_route", "route_id")[route_id]
    pipeline = ctx.pipeline()
    registry = load_registry(ctx.store)
    return execute_route(pipeline, registry, route, transports=transports)


def assign_route(ctx: CommandContext, route_id: str, *, assigned_actor: str) -> dict:
    projection = ctx.projection()
    if projection.get("collection_route", route_id) is None:
        raise NotFound(f"unknown collection route: {route_id}")
    route = ctx.store.latest_by_id("collection_route", "route_id")[route_id]
    return assign_human_route(ctx.store, route, assigned_actor=assigned_actor,
                              now=ctx.now_fn(), actor=ctx.actor,
                              marking=ctx.marking)


# ---- watches -----------------------------------------------------------------

def create_watch(ctx: CommandContext, *, need_id: str, target_kind: str,
                 target_ref: str, source_id: str, operation: str,
                 query_value: str, cadence_seconds: int,
                 blind_spots: tuple[str, ...] = ()) -> dict:
    from curunir_fabric.contracts import WatchDefinition
    now = ctx.now_fn()
    watch_id = digest_id("watch", need_id, source_id, operation, query_value)
    if ctx.store.latest_by_id("fabric_watch", "watch_id").get(watch_id) is not None:
        raise Conflict(f"watch {watch_id} already exists; change its state "
                       "through pause/resume, never by re-creation")
    definition = WatchDefinition(
        watch_id=watch_id,
        need_id=need_id, target_kind=target_kind, target_ref=target_ref,
        source_id=source_id, operation=operation, query_value=query_value,
        cadence_seconds=cadence_seconds, active=True, blind_spots=blind_spots,
        created_by=ctx.actor, created_time=now, marking=ctx.marking)
    register_watch(ctx.store, definition, actor=ctx.actor)
    return definition.to_record()


def set_watch_active(ctx: CommandContext, watch_id: str, *, active: bool,
                     expected_active: bool | None = None) -> dict:
    """Pause/resume by re-appending the definition; the scheduler (fabric
    tick) reads the latest state — the workbench never bypasses it. The
    caller states the state it believes it is changing FROM; a concurrent
    change surfaces as a conflict instead of silent last-write-wins.
    Authorship, creation time and marking are preserved — the act itself is
    attributed through the event log."""
    from curunir_fabric.contracts import WatchDefinition
    projection = ctx.projection()
    if projection.get("fabric_watch", watch_id) is None:
        raise NotFound(f"unknown watch: {watch_id}")
    raw = ctx.store.latest_by_id("fabric_watch", "watch_id")[watch_id]
    if expected_active is not None and bool(raw["active"]) != expected_active:
        raise Conflict(f"watch is currently "
                       f"{'ACTIVE' if raw['active'] else 'PAUSED'}; "
                       "your view is stale")
    if bool(raw["active"]) == active:
        return raw
    definition = WatchDefinition(**{
        **{k: v for k, v in raw.items() if k != "record_type"},
        "blind_spots": tuple(raw["blind_spots"]),
        "active": active,
        "marking": marking_from_record(raw["marking"])})
    register_watch(ctx.store, definition, actor=ctx.actor)
    return definition.to_record()


def save_view(ctx: CommandContext, *, title: str, view_kind: str,
              definition: Mapping[str, Any]) -> dict:
    """Persist an investigation layout (filters/focus/window). Presentation
    state only — never analytical truth."""
    from .contracts import SavedViewRecord
    ctx.require_visible_marking()
    now = ctx.now_fn()
    view_id = digest_id("savedview", ctx.actor, view_kind, title)
    existing = ctx.store.latest_by_id("workbench_saved_view", "view_id").get(view_id)
    record = SavedViewRecord(
        view_id=view_id, title=title, author=ctx.actor, view_kind=view_kind,
        definition=dict(definition), recorded_time=now, marking=ctx.marking,
        version=(existing["version"] + 1) if existing else 1)
    try:
        ctx.store.append("WORKBENCH_SAVED_VIEW_RECORDED", record,
                         recorded_time=now, actor=ctx.actor)
    except ValueError as error:
        raise Conflict(str(error)) from error
    return record.to_record()


# ---- reports -----------------------------------------------------------------

def create_report(ctx: CommandContext, *, title: str, question: str,
                  sections: list[Mapping[str, Any]]) -> dict:
    projection = ctx.projection()
    return reports_module.create_report(
        ctx.store, actor=ctx.actor, marking=ctx.marking, now=ctx.now_fn(),
        title=title, question=question, sections=sections,
        state_token=projection.state_token)


def edit_report(ctx: CommandContext, report_id: str, *, expected_version: int,
                sections: list[Mapping[str, Any]], title: str | None = None,
                question: str | None = None, change_note: str = "") -> dict:
    projection = ctx.projection()
    if projection.get("workbench_report", report_id) is None:
        raise NotFound(f"unknown report: {report_id}")
    try:
        return reports_module.edit_report(
            ctx.store, report_id, actor=ctx.actor, actor_kind=ctx.actor_kind,
            marking=ctx.marking,
            now=ctx.now_fn(), expected_version=expected_version,
            sections=sections, title=title, question=question,
            state_token=projection.state_token, change_note=change_note)
    except reports_module.ReportConflict as error:
        raise Conflict(str(error)) from error


def submit_report(ctx: CommandContext, report_id: str, *, expected_version: int) -> dict:
    projection = ctx.projection()
    if projection.get("workbench_report", report_id) is None:
        raise NotFound(f"unknown report: {report_id}")
    try:
        return reports_module.submit_report(
            ctx.store, report_id, actor=ctx.actor, actor_kind=ctx.actor_kind,
            marking=ctx.marking,
            now=ctx.now_fn(), expected_version=expected_version,
            state_token=projection.state_token)
    except reports_module.ReportConflict as error:
        raise Conflict(str(error)) from error


def approve_report(ctx: CommandContext, report_id: str, *, expected_version: int,
                   note: str = "", acknowledge_dissent: tuple[str, ...] = ()) -> dict:
    projection = ctx.projection()
    if projection.get("workbench_report", report_id) is None:
        raise NotFound(f"unknown report: {report_id}")
    try:
        return reports_module.approve_report(
            ctx.store, projection, report_id, actor=ctx.actor,
            actor_kind=ctx.actor_kind, marking=ctx.marking, now=ctx.now_fn(),
            expected_version=expected_version, note=note,
            acknowledge_dissent=acknowledge_dissent)
    except reports_module.ReportConflict as error:
        raise Conflict(str(error)) from error


def reject_report(ctx: CommandContext, report_id: str, *, expected_version: int,
                  note: str, return_for_revision: bool = False) -> dict:
    projection = ctx.projection()
    if projection.get("workbench_report", report_id) is None:
        raise NotFound(f"unknown report: {report_id}")
    try:
        return reports_module.reject_report(
            ctx.store, report_id, actor=ctx.actor, actor_kind=ctx.actor_kind,
            marking=ctx.marking, now=ctx.now_fn(),
            expected_version=expected_version, note=note,
            return_for_revision=return_for_revision,
            state_token=projection.state_token)
    except reports_module.ReportConflict as error:
        raise Conflict(str(error)) from error
