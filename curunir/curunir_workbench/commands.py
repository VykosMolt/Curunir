"""Every workbench mutation, as a named act with a named actor.

The browser names a command; the server resolves the actor, checks authority and
calls the same store functions the rest of Curunír uses. A stale write raises a
conflict instead of overwriting someone's work.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping

from argus.source_intelligence.models import digest_id
from curunir_analytic.contracts import ResolutionRule
from curunir_analytic.forecasts import (create_forecast, resolve_forecast_human,
                                        update_probability)
from curunir_analytic.candidate_schema import refusal_reason
from curunir_analytic.model_backends import assist_from_environment
from curunir_analytic.substrate import AnalyticContext, resolve_candidate
from curunir_analytic.warning import project_warning
from curunir_fabric.contracts import WatchDefinition
from curunir_fabric.registry import load_registry
from curunir_fabric.watch import register_watch
from curunir_operational.access import (AccessContext, Marking, ROLE_RANK, can_view,
                                        marking_from_record, most_restrictive)
from curunir_operational.canonical import validate_interchange
from curunir_operational.missions import MissionWorkflow
from curunir_operational.security import MaterialReference, resolve_reference_records
from curunir_operational.workflow import WorkflowEngine
from curunir_semantic.collection import assign_human_route, execute_route
from curunir_semantic.contracts import HYPOTHESIS_STATUSES, HypothesisRecord, ReviewItem
from curunir_semantic.hypotheses import link_claim, record_hypothesis
from curunir_semantic.pipeline import SemanticPipeline

from . import annotations as annotations_module
from . import reports as reports_module
from .contracts import SavedViewRecord
from .errors import Conflict, NotFound
from .projections import MissionProjection
from .store import WorkbenchStore


class CommandError(ValueError):
    pass


# Digest-shaped record ids ("prefix-hex"). evgroup- is a dependence group.
_ID_TOKEN_RE = re.compile(r"(?!evgroup-)[a-z][a-z-]{1,32}-[0-9a-f]{12,64}")
# Anything id-shaped in free text, tested for membership below.
_FREE_TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z0-9_-]{5,}")


def _validate_inbound(projection: MissionProjection, *,
                      refs: tuple = (), texts: tuple = ()) -> None:
    """Refuse a payload that cites anything the caller cannot open.

    Every reference must resolve in the caller's own view, and hidden and missing
    are refused alike. Free text is scanned for hidden and digest-shaped ids."""
    known = projection.visible_id_set()
    hidden = projection.hidden_ids
    bad: list[str] = []
    for ref in refs:
        value = str(ref)
        if not value:
            continue
        if value == "REDACTED" or value not in known:
            bad.append(value)
    for text in texts:
        text = str(text or "")
        if "REDACTED" in text:
            bad.append("REDACTED")
        for match in _FREE_TOKEN_RE.finditer(text):
            # A token after "/" or "." is part of a link or hostname.
            prev = text[match.start() - 1] if match.start() > 0 else " "
            if prev in "/.":
                continue
            base = match.group(0).split("@v")[0]
            if base in hidden:
                bad.append(base)
            elif _ID_TOKEN_RE.fullmatch(base) and base not in known:
                bad.append(base)
    if bad:
        raise CommandError(
            "payload references identifiers that do not resolve in your "
            f"view: {sorted(set(bad))[:4]} — cite records you can open, and "
            "never re-submit redacted placeholders")


def _soft_reject(projection: MissionProjection, values: tuple) -> None:
    """For fields naming things outside the store: they need not resolve, but a
    hidden id or a REDACTED echo is still refused."""
    hidden = projection.hidden_ids
    bad = [str(v) for v in values
           if v and (str(v) == "REDACTED" or str(v).split("@v")[0] in hidden)]
    if bad:
        raise CommandError(
            f"payload carries identifiers you cannot resolve: {sorted(set(bad))[:4]}")


def marked(ctx: "CommandContext", compartments: tuple[str, ...] = (), min_role: str = "") -> Marking:
    """The marking for a new record: the default, raised into compartments the
    actor actually holds and to a role floor the actor meets. Raises otherwise,
    so nobody writes into access they do not have."""
    if min_role and min_role not in ROLE_RANK:
        raise CommandError(f"unknown role floor: {min_role}")
    if not compartments and not min_role:
        return ctx.marking
    missing = set(compartments) - set(ctx.context.compartments)
    if missing:
        raise PermissionError("cannot write into compartments you do not hold")
    base = ctx.marking.to_record()
    floor = base["min_role"]
    if min_role and ROLE_RANK[min_role] > ROLE_RANK[floor]:
        if not any(ROLE_RANK.get(role, -1) >= ROLE_RANK[min_role] for role in ctx.context.roles):
            raise PermissionError("cannot write at a role floor you do not meet")
        floor = min_role
    return Marking(owning_authority=base["owning_authority"],
                   compartments=tuple(compartments) or tuple(base["compartments"]),
                   releasability=tuple(base["releasability"]),
                   min_role=floor, caveats=tuple(base["caveats"]))


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _record_marking(record: Mapping[str, Any]) -> Marking:
    """The marking anything derived from this record must carry; a derived
    record is never less restricted than its subject."""
    return marking_from_record(record["marking"]) \
        if isinstance(record.get("marking"), dict) else record["marking"]


def _reference_marking(ctx: "CommandContext", projection: MissionProjection, *,
                       refs: tuple, compartments: tuple[str, ...] = (), min_role: str = "") -> Marking:
    """The marking for a new record that cites existing state: the most restrictive
    of the actor's own and everything it names. The actor must be cleared for the
    result, so citing several records cannot land them above their own access."""
    markings = [marked(ctx, compartments, min_role)] + _ref_markings(projection, tuple(refs))
    result = most_restrictive(markings)
    if not can_view(result, ctx.context):
        raise PermissionError(
            "this record references state above your access; it cannot be "
            "written at a classification you cannot yourself view")
    return result


def _ref_markings(projection: MissionProjection, refs: tuple) -> list[Marking]:
    out: list[Marking] = []
    for ref in refs:
        if not ref:
            continue
        found = projection.marking_of(str(ref))
        if isinstance(found, dict):
            out.append(marking_from_record(found))
    return out


def _guard_reference_floor(projection: MissionProjection, subject_marking: Marking,
                           refs: tuple) -> None:
    """Refuse when a record that keeps its own marking cites something more
    restricted: a re-append does not re-classify, so the citation and the prose
    around it would sit in an under-classified record."""
    joined = most_restrictive([subject_marking] + _ref_markings(projection, refs))
    if joined.to_record() != subject_marking.to_record():
        raise CommandError(
            "this record cites state more restricted than its own "
            "classification; cite records it can carry, or create the record "
            "at that classification")


def _definition_refs(definition: Mapping[str, Any]) -> tuple:
    """Every string anywhere in a saved-view definition; any of them may name a
    record whose marking the view has to inherit."""
    out: list[str] = []

    def walk(value):
        if isinstance(value, str):
            out.append(value)
        elif isinstance(value, Mapping):
            for v in value.values():
                walk(v)
        elif isinstance(value, (list, tuple)):
            for v in value:
                walk(v)

    walk(definition)
    return tuple(out)


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

    def analytic(self, marking: Marking | None = None) -> AnalyticContext:
        # Everything written here carries `marking`, companion records included.
        # Pass the subject's marking for an existing record; the actor's default
        # is only for new ones.
        return AnalyticContext(store=self.store, actor=self.actor,
                               marking=marking or self.marking, now_fn=self.now_fn)

    def pipeline(self, marking: Marking | None = None) -> SemanticPipeline:
        return SemanticPipeline(store=self.store, custody_root=self.root / "custody",
                                actor=self.actor, marking=marking or self.marking,
                                now_fn=self.now_fn)

    def require_visible_marking(self) -> None:
        """An actor may only write records it could read itself."""
        if not can_view(self.marking, self.context):
            raise PermissionError("actor cannot write records outside its own access")


# Annotations

def annotate(ctx: CommandContext, *, target_kind: str, target_id: str,
             kind: str, text: str, reply_to: str = "",
             anchor_ref: str = "") -> dict:
    ctx.require_visible_marking()
    projection = ctx.projection()
    _validate_inbound(projection, refs=(anchor_ref, reply_to), texts=(text,))
    # At least as restricted as the record it targets or anchors to.
    subject_markings = []
    tgt = annotations_module.target_marking(projection, target_kind, target_id)
    if isinstance(tgt, dict):
        subject_markings.append(marking_from_record(tgt))
    if anchor_ref:
        anchor = projection.marking_of(anchor_ref)
        if isinstance(anchor, dict):
            subject_markings.append(marking_from_record(anchor))
    marking = most_restrictive(subject_markings) if subject_markings else ctx.marking
    if not can_view(marking, ctx.context):
        raise PermissionError("cannot annotate outside your own access")
    return annotations_module.create_annotation(
        ctx.store, projection, actor=ctx.actor, marking=marking,
        now=ctx.now_fn(), target_kind=target_kind, target_id=target_id,
        kind=kind, text=text, reply_to=reply_to, anchor_ref=anchor_ref)


def resolve_annotation(ctx: CommandContext, annotation_id: str, *,
                       expected_version: int, status: str, note: str) -> dict:
    projection = ctx.projection()
    if projection.get("workbench_annotation", annotation_id) is None:
        raise NotFound(f"unknown annotation: {annotation_id}")
    _validate_inbound(projection, texts=(note,))
    try:
        return annotations_module.resolve_annotation(
            ctx.store, annotation_id, actor=ctx.actor, marking=ctx.marking,
            now=ctx.now_fn(), expected_version=expected_version,
            status=status, note=note)
    except annotations_module.AnnotationConflict as error:
        raise Conflict(str(error)) from error


# Mission workflow: requirements and tasks

def open_requirement(ctx: CommandContext, *, question: str, priority: str,
                     mission_context: str, rationale: str,
                     required_evidence_type: str = "OPEN_SOURCE",
                     owning_role: str = "ANALYST", closure_criteria: str = "",
                     due_time: str | None = None,
                     affected_ids: tuple[str, ...] = (),
                     compartments: tuple[str, ...] = ()) -> dict:
    projection = ctx.projection()
    _validate_inbound(projection, refs=affected_ids,
                      texts=(question, rationale, mission_context, closure_criteria))
    # At least as restricted as the records it names.
    write_marking = _reference_marking(ctx, projection, refs=affected_ids,
                                       compartments=compartments)
    requirement_id = digest_id("req", question, mission_context)
    existing = ctx.store.latest_by_id(
        "information_requirement", "requirement_id").get(requirement_id)
    if existing is not None and not can_view(existing.get("marking"), ctx.context):
        # The question exists outside the caller's access. Folding would
        # declassify it, returning it would disclose it: refuse silently.
        raise PermissionError("cannot open this requirement in your context")
    if existing is not None:
        # Widening keeps the existing marking, so nothing above it may come in.
        _guard_reference_floor(projection, marking_from_record(existing["marking"]),
                               tuple(affected_ids))
    workflow = MissionWorkflow(ctx.store)
    result = workflow.open_requirement(
        mission_context=mission_context, question=question,
        affected_ids=affected_ids, priority=priority, rationale=rationale,
        required_evidence_type=required_evidence_type, owning_role=owning_role,
        closure_criteria=closure_criteria or "answered with cited evidence",
        due_time=due_time, recorded_time=ctx.now_fn(),
        marking=marking_from_record(existing["marking"]) if existing is not None
        else write_marking, actor=ctx.actor)
    return result


def assign_task(ctx: CommandContext, *, assigned_actor: str, task_type: str,
                required_action: str, affected_ids: tuple[str, ...] = (),
                assigned_role: str = "ANALYST", due_time: str | None = None,
                depends_on: tuple[str, ...] = (),
                compartments: tuple[str, ...] = ()) -> dict:
    projection = ctx.projection()
    _validate_inbound(projection, refs=affected_ids + depends_on,
                      texts=(required_action,))
    write_marking = _reference_marking(ctx, projection,
                                       refs=affected_ids + depends_on,
                                       compartments=compartments)
    workflow = MissionWorkflow(ctx.store)
    return workflow.assign_task(
        assigned_role=assigned_role, assigned_actor=assigned_actor,
        task_type=task_type, affected_ids=affected_ids,
        required_action=required_action, due_time=due_time,
        depends_on=depends_on, recorded_time=ctx.now_fn(),
        marking=write_marking, actor=ctx.actor)


def transition_workflow(ctx: CommandContext, *, subject_kind: str, subject_id: str,
                        to_status: str, evidence_refs: tuple[str, ...] = (),
                        note: str = "") -> dict:
    view_key = {"requirement": "information_requirements",
                "analyst_task": "analyst_tasks",
                "evidence_request": "evidence_requests"}.get(subject_kind)
    if view_key is None:
        raise CommandError(f"unknown workflow subject kind: {subject_kind}")
    id_field = {"requirement": "requirement_id", "analyst_task": "task_id",
                "evidence_request": "request_id"}[subject_kind]
    projection = ctx.projection()
    visible = projection.base_view[view_key]
    subject = next((r for r in visible if r[id_field] == subject_id), None)
    if subject is None:
        raise NotFound(f"unknown {subject_kind}: {subject_id}")
    # Evidence that resolves; invented or unseen references do not count.
    _validate_inbound(projection, refs=evidence_refs, texts=(note,))
    # The transition keeps its subject's marking, so evidence must fit under it.
    _guard_reference_floor(projection, _record_marking(subject), tuple(evidence_refs))
    workflow = MissionWorkflow(ctx.store)
    return workflow.transition(
        subject_kind, subject_id, to_status, actor_id=ctx.actor,
        actor_kind=ctx.actor_kind, evidence_refs=evidence_refs, note=note,
        recorded_time=ctx.now_fn(), marking=_record_marking(subject))


# Review

def resolve_review_item(ctx: CommandContext, item_id: str, *,
                        expected_version: int, status: str, note: str) -> dict:
    """Close a review item. DISMISSED keeps the item and its note in the log:
    dismissing a contradiction never erases it."""
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
    _validate_inbound(projection, texts=(note,))
    raw = ctx.store.latest_by_id("review_item", "item_id")[item_id]
    record = ReviewItem(
        item_id=item_id, kind=raw["kind"], subject_kind=raw["subject_kind"],
        subject_id=raw["subject_id"], detail=raw["detail"],
        evidence_refs=tuple(raw["evidence_refs"]), status=status,
        resolution_note=note, recorded_time=ctx.now_fn(),
        # Closing never re-marks an item.
        marking=marking_from_record(raw["marking"]),
        version=raw["version"] + 1)
    try:
        event = ctx.store.append(
            "REVIEW_ITEM_RECORDED", record,
            recorded_time=record.recorded_time, actor=ctx.actor)
    except ValueError as error:
        raise Conflict(str(error)) from error
    return event["record"]


def resolve_model_proposal(ctx: CommandContext, proposal_id: str, *,
                           accept: bool, note: str = "") -> dict:
    latest = ctx.store.latest_by_id("analytical_proposal", "proposal_id").get(proposal_id)
    if latest is None or not can_view(latest.get("marking"), ctx.context):
        raise NotFound(f"unknown analytical proposal: {proposal_id}")
    # Resolution and companions carry the proposal's marking.
    return resolve_candidate(ctx.analytic(_record_marking(latest)), proposal_id,
                             accept=accept, actor_id=ctx.actor,
                             actor_kind=ctx.actor_kind, note=note)


def request_model_proposal(ctx: CommandContext, *, task: str, target_kind: str,
                           input_refs: tuple[str, ...]) -> dict:
    """Ask the configured provider for one analytical candidate. It only ever sees
    records this actor could already read, and the answer comes back as a
    proposal only a human can accept.
    """
    assist = assist_from_environment()
    if assist is None or not assist.available():
        return {"status": "ANALYTICAL_PROVIDER_UNAVAILABLE",
                "detail": ("no analytical provider is configured; deterministic "
                           "candidates and analyst acts remain fully available")}
    if not input_refs:
        raise ValueError("a proposal request must cite the evidence it rests on")
    reason = refusal_reason(target_kind)
    if reason is not None:
        raise ValueError(f"{target_kind} cannot be model-proposed: {reason}")

    records: list[Mapping[str, Any]] = []
    markings: list[Any] = []
    for ref in dict.fromkeys(input_refs):
        resolved = resolve_reference_records(ctx.store, (MaterialReference("*", ref),))
        visible = [r for r in resolved if can_view(r.get("marking"), ctx.context)]
        if not visible:
            raise NotFound(f"unknown record: {ref}")
        for record in visible:
            records.append(record)
            markings.append(_record_marking(record))
    subject = most_restrictive(markings) if markings else ctx.marking
    return assist.propose(ctx.analytic(subject), task=task, target_kind=target_kind,
                          inputs={"records": records}, input_refs=tuple(input_refs))


def decide_recommendation(ctx: CommandContext, recommendation_id: str, *,
                          state: str, rationale: str) -> dict:
    # A recommendation the actor cannot see is unknown.
    recommendation = ctx.store.latest_by_id(
        "recommendation", "recommendation_id").get(recommendation_id)
    if recommendation is None or not can_view(recommendation.get("marking"), ctx.context):
        raise NotFound(f"unknown recommendation: {recommendation_id}")
    _validate_inbound(ctx.projection(), texts=(rationale,))
    engine = WorkflowEngine(ctx.store)
    return engine.decide(recommendation_id, context=ctx.context, state=state,
                         rationale=rationale, recorded_time=ctx.now_fn(),
                         marking=_record_marking(recommendation))


# Hypotheses

def create_hypothesis(ctx: CommandContext, *, statement: str, case_id: str,
                      assumptions: tuple[str, ...] = (),
                      unknowns: tuple[str, ...] = (),
                      compartments: tuple[str, ...] = ()) -> dict:
    _validate_inbound(ctx.projection(),
                      texts=(statement, case_id) + assumptions + unknowns)
    return record_hypothesis(ctx.store, statement=statement, case_id=case_id,
                             assumptions=assumptions, unknowns=unknowns,
                             analyst_or_provider=ctx.actor, now=ctx.now_fn(),
                             actor=ctx.actor, marking=marked(ctx, compartments))


def assess_hypothesis(ctx: CommandContext, hypothesis_id: str, *,
                      expected_version: int, status: str, rationale: str) -> dict:
    """Record an analyst's assessment as a new version of the hypothesis.
    An analyst who disagrees files dissent instead of overwriting this."""
    if ctx.actor_kind != "HUMAN":
        raise PermissionError("hypothesis assessment is a human act")
    if status not in HYPOTHESIS_STATUSES:
        raise CommandError(f"invalid hypothesis status: {status}")
    if not rationale.strip():
        raise CommandError("an assessment requires its rationale")
    projection = ctx.projection()
    if projection.get("hypothesis", hypothesis_id) is None:
        raise NotFound(f"unknown hypothesis: {hypothesis_id}")
    _validate_inbound(projection, texts=(rationale,))
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
        event = ctx.store.append(
            "HYPOTHESIS_RECORDED", record,
            recorded_time=record.recorded_time, actor=ctx.actor)
    except ValueError as error:
        raise Conflict(str(error)) from error
    return event["record"]


# Forecasts

def author_forecast(ctx: CommandContext, *, question: str, outcome_semantics: str,
                    horizon_time: str, probability: float, probability_basis: str,
                    proposition_refs: tuple[tuple[str, str], ...],
                    resolution: Mapping[str, Any], domain: str,
                    assumption_ids: tuple[str, ...] = (),
                    compartments: tuple[str, ...] = ()) -> dict:
    """A probability a person states; the workbench derives none and
    recalculates none."""
    if ctx.actor_kind != "HUMAN":
        raise PermissionError("a forecast probability is authored by a human "
                              "(or enters as a gated model candidate, not here)")
    projection = ctx.projection()
    forecast_refs = assumption_ids + tuple(r[1] for r in proposition_refs)
    _validate_inbound(projection, refs=forecast_refs,
                      texts=(question, outcome_semantics, probability_basis))
    # At least as restricted as what it cites.
    forecast_marking = _reference_marking(ctx, projection, refs=forecast_refs,
                                          compartments=compartments)
    rule = resolution if isinstance(resolution, ResolutionRule) else ResolutionRule(
        **{k: (tuple(v) if isinstance(v, list) else v)
           for k, v in resolution.items() if k != "record_type"})
    analytic_ctx = ctx.analytic()
    analytic_ctx.marking = forecast_marking
    return create_forecast(analytic_ctx, question=question,
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
    projection = ctx.projection()
    _validate_inbound(projection, refs=evidence_refs,
                      texts=(probability_basis, change_reason))
    current = projection.get("analytic_forecast", forecast_id)
    if current is None:
        raise NotFound(f"unknown forecast: {forecast_id}")
    if current["version"] != expected_version:
        raise Conflict(f"forecast is at version {current['version']}, "
                       f"you saw {expected_version}")
    # The movement keeps the forecast's marking, so evidence must fit under it.
    forecast_marking = _record_marking(ctx.store.current_forecasts()[forecast_id])
    _guard_reference_floor(projection, forecast_marking, tuple(evidence_refs))
    try:
        return update_probability(ctx.analytic(forecast_marking), forecast_id,
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
    projection = ctx.projection()
    if projection.get("analytic_forecast", forecast_id) is None:
        raise NotFound(f"unknown forecast: {forecast_id}")
    _validate_inbound(projection, refs=evidence_refs, texts=(rationale,))
    forecast_marking = _record_marking(ctx.store.current_forecasts()[forecast_id])
    _guard_reference_floor(projection, forecast_marking, tuple(evidence_refs))
    return resolve_forecast_human(ctx.analytic(forecast_marking), forecast_id,
                                  outcome=outcome, evidence_refs=evidence_refs,
                                  note=rationale, actor_id=ctx.actor,
                                  actor_kind=ctx.actor_kind)


def link_hypothesis_claim(ctx: CommandContext, hypothesis_id: str, *,
                          claim_id: str, stance: str, rationale: str) -> dict:
    projection = ctx.projection()
    if projection.get("hypothesis", hypothesis_id) is None:
        raise NotFound(f"unknown hypothesis: {hypothesis_id}")
    if projection.get("semantic_claim", claim_id) is None:
        raise NotFound(f"unknown claim: {claim_id}")
    _validate_inbound(projection, texts=(rationale,))
    # The hypothesis keeps its marking, so a claim above it would leave the
    # claim id and its rationale under-classified.
    hyp_marking = _record_marking(ctx.store.current_hypotheses()[hypothesis_id])
    _guard_reference_floor(projection, hyp_marking, (claim_id,))
    return link_claim(ctx.store, hypothesis_id, claim_id, stance,
                      rationale=rationale, now=ctx.now_fn(), actor=ctx.actor,
                      marking=hyp_marking)


def project_forecast_warning(ctx: CommandContext, forecast_id: str, *,
                             objective_id: str) -> dict:
    """Project a forecast onto an objective; the tier comes from the named rule,
    never from the caller."""
    projection = ctx.projection()
    if projection.get("analytic_forecast", forecast_id) is None:
        raise NotFound(f"unknown forecast: {forecast_id}")
    if projection.get("mission_objective", objective_id) is None:
        raise NotFound(f"unknown objective: {objective_id}")
    # A warning reveals the forecast's probability and tier, so it takes the
    # more restrictive of the two markings.
    forecast_marking = _record_marking(ctx.store.current_forecasts()[forecast_id])
    objective_marking = _record_marking(ctx.store.current_objectives()[objective_id])
    warning_marking = most_restrictive([forecast_marking, objective_marking])
    if not can_view(warning_marking, ctx.context):
        # Two readable records can combine into one the actor cannot open.
        raise PermissionError(
            "the warning would be classified above your own access; you "
            "cannot project this forecast onto this objective")
    return project_warning(ctx.analytic(warning_marking), forecast_id=forecast_id,
                           objective_id=objective_id)


# Collection

def launch_route(ctx: CommandContext, route_id: str, *,
                 transports: Mapping[str, Any] | None = None) -> dict:
    """Run one collection route through the fabric: acquire, take custody,
    understand, and refresh the hypotheses and analytics that follow."""
    projection = ctx.projection()
    if projection.get("collection_route", route_id) is None:
        raise NotFound(f"unknown collection route: {route_id}")
    route = ctx.store.latest_by_id("collection_route", "route_id")[route_id]
    # Everything this launch writes carries the route's marking, so open
    # evidence under a restricted tasking is over-classified rather than under.
    pipeline = ctx.pipeline(_record_marking(route))
    registry = load_registry(ctx.store)
    return execute_route(pipeline, registry, route, transports=transports)


def assign_route(ctx: CommandContext, route_id: str, *, assigned_actor: str) -> dict:
    projection = ctx.projection()
    if projection.get("collection_route", route_id) is None:
        raise NotFound(f"unknown collection route: {route_id}")
    route = ctx.store.latest_by_id("collection_route", "route_id")[route_id]
    return assign_human_route(ctx.store, route, assigned_actor=assigned_actor,
                              now=ctx.now_fn(), actor=ctx.actor,
                              marking=_record_marking(route))


# Watches

def create_watch(ctx: CommandContext, *, need_id: str, target_kind: str,
                 target_ref: str, source_id: str, operation: str,
                 query_value: str, cadence_seconds: int,
                 blind_spots: tuple[str, ...] = (),
                 compartments: tuple[str, ...] = ()) -> dict:
    # need_id, target_ref and query_value usually name things outside the store
    # (an LEI, a URL, a phrase), so they need not resolve.
    projection = ctx.projection()
    _soft_reject(projection, (need_id, target_ref, query_value))
    _validate_inbound(projection, texts=tuple(blind_spots))
    # If one does name a record here, the watch inherits its marking.
    write_marking = _reference_marking(ctx, projection,
                                       refs=(need_id, target_ref, query_value),
                                       compartments=compartments)
    now = ctx.now_fn()
    watch_id = digest_id("watch", need_id, source_id, operation, query_value)
    raw = ctx.store.latest_by_id("fabric_watch", "watch_id").get(watch_id)
    if raw is not None:
        if not can_view(raw.get("marking"), ctx.context):
            # It exists outside the caller's access; refuse without confirming.
            raise PermissionError("cannot create this watch in your context")
        raise Conflict(f"watch {watch_id} already exists; change its state "
                       "through pause/resume, never by re-creation")
    definition = WatchDefinition(
        watch_id=watch_id,
        need_id=need_id, target_kind=target_kind, target_ref=target_ref,
        source_id=source_id, operation=operation, query_value=query_value,
        cadence_seconds=cadence_seconds, active=True, blind_spots=blind_spots,
        created_by=ctx.actor, created_time=now, marking=write_marking)
    register_watch(ctx.store, definition, actor=ctx.actor)
    return definition.to_record()


def set_watch_active(ctx: CommandContext, watch_id: str, *, active: bool,
                     expected_active: bool | None = None) -> dict:
    """Pause or resume a watch by re-appending its definition, which is what the
    scheduler reads. The caller names the state it believes it is changing from,
    so a concurrent change becomes a conflict rather than a silent overwrite."""
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
              definition: Mapping[str, Any],
              compartments: tuple[str, ...] = ()) -> dict:
    """Save a layout of filters and focus; presentation only, never truth."""
    ctx.require_visible_marking()
    validate_interchange(definition)
    projection = ctx.projection()
    _validate_inbound(projection,
                      texts=(title, json.dumps(definition, default=str)))
    now = ctx.now_fn()
    view_id = digest_id("savedview", ctx.actor, view_kind, title)
    existing = ctx.store.latest_by_id("workbench_saved_view", "view_id").get(view_id)
    # At least as restricted as the records its definition names.
    definition_refs = _definition_refs(definition)
    if existing is not None:
        # A re-save keeps the original marking, so nothing above it may come in.
        view_marking = marking_from_record(existing["marking"])
        _guard_reference_floor(projection, view_marking, definition_refs)
    else:
        view_marking = _reference_marking(ctx, projection, refs=definition_refs,
                                          compartments=compartments)
    record = SavedViewRecord(
        view_id=view_id, title=title, author=ctx.actor, view_kind=view_kind,
        definition=dict(definition), recorded_time=now, marking=view_marking,
        version=(existing["version"] + 1) if existing else 1)
    try:
        event = ctx.store.append(
            "WORKBENCH_SAVED_VIEW_RECORDED", record,
            recorded_time=now, actor=ctx.actor)
    except ValueError as error:
        raise Conflict(str(error)) from error
    return event["record"]


# Reports

def _section_refs(sections: list[Mapping[str, Any]]) -> tuple:
    refs: list[str] = []
    for section in sections:
        for sentence in section.get("sentences", ()):
            refs += list(sentence.get("basis_refs", ()))
            refs += list(sentence.get("assumption_ids", ()))
        refs += list(section.get("option_ids", ()))
    return tuple(refs)


def _validate_sections(projection: MissionProjection,
                       sections: list[Mapping[str, Any]]) -> None:
    texts: list[str] = []
    for section in sections:
        texts.append(str(section.get("title", "")))
        for sentence in section.get("sentences", ()):
            texts += [str(sentence.get("text", "")),
                      str(sentence.get("inference_note", "")),
                      str(sentence.get("unresolved_reason", ""))]
    _validate_inbound(projection, refs=_section_refs(sections), texts=tuple(texts))


def _validate_section_shapes(sections: Any) -> None:
    """Check the shape of incoming sections before building records from them."""
    if not isinstance(sections, list):
        raise ValueError("sections must be a list")
    for section in sections:
        if not isinstance(section, Mapping):
            raise ValueError("each section must be an object")
        for field in ("kind", "title", "section_id"):
            if not isinstance(section.get(field, ""), str):
                raise ValueError(f"section {field} must be a string")
        if not isinstance(section.get("sentences", []), list):
            raise ValueError("section sentences must be a list")
        option_ids = section.get("option_ids", [])
        if not isinstance(option_ids, list) \
                or not all(isinstance(item, str) for item in option_ids):
            raise ValueError("section option_ids must be a list of ids")
        for sentence in section.get("sentences", []):
            if not isinstance(sentence, Mapping):
                raise ValueError("each sentence must be an object")
            for field in (
                "text", "status", "inference_note", "unresolved_reason",
                "temporal_scope", "sentence_id",
            ):
                if not isinstance(sentence.get(field, ""), str):
                    raise ValueError(f"sentence {field} must be a string")
            for field in ("basis_refs", "assumption_ids"):
                refs = sentence.get(field, [])
                if not isinstance(refs, list) \
                        or not all(isinstance(item, str) for item in refs):
                    raise ValueError(f"sentence {field} must be a list of ids")


def create_report(ctx: CommandContext, *, title: str, question: str,
                  sections: list[Mapping[str, Any]],
                  compartments: tuple[str, ...] = (), min_role: str = "") -> dict:
    _validate_section_shapes(sections)
    projection = ctx.projection()
    _validate_inbound(projection, texts=(title, question))
    _validate_sections(projection, sections)
    # Created at the floor it will need, since an edit never raises it.
    write_marking = _reference_marking(ctx, projection,
                                       refs=_section_refs(sections),
                                       compartments=compartments, min_role=min_role)
    return reports_module.create_report(
        ctx.store, actor=ctx.actor, marking=write_marking,
        now=ctx.now_fn(), title=title, question=question, sections=sections,
        state_token=projection.state_token)


def edit_report(ctx: CommandContext, report_id: str, *, expected_version: int,
                sections: list[Mapping[str, Any]], title: str | None = None,
                question: str | None = None, change_note: str = "") -> dict:
    _validate_section_shapes(sections)
    projection = ctx.projection()
    current = projection.get("workbench_report", report_id)
    if current is None:
        raise NotFound(f"unknown report: {report_id}")
    _validate_inbound(projection, texts=tuple(t for t in (title, question, change_note) if t))
    _validate_sections(projection, sections)
    # An edit keeps the report's marking, so a reference above it would leave
    # the surrounding prose under-classified.
    report_marking = _record_marking(current)
    ref_marking = _reference_marking(ctx, projection, refs=_section_refs(sections))
    if most_restrictive([report_marking, ref_marking]).to_record() != report_marking.to_record():
        raise CommandError(
            "the edit cites state more restricted than this report; create a "
            "report at that classification instead of adding it here")
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
    _validate_inbound(projection, texts=(note,))
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
    _validate_inbound(projection, texts=(note,))
    try:
        return reports_module.reject_report(
            ctx.store, report_id, actor=ctx.actor, actor_kind=ctx.actor_kind,
            marking=ctx.marking, now=ctx.now_fn(),
            expected_version=expected_version, note=note,
            return_for_revision=return_for_revision,
            state_token=projection.state_token)
    except reports_module.ReportConflict as error:
        raise Conflict(str(error)) from error
