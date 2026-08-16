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
                                        marking_from_record, most_restrictive)
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


import re as _re

# digest-shaped record ids ("prefix-hex"); evgroup- names dependence
# families, which are arithmetic groupings rather than records
_ID_TOKEN_RE = _re.compile(r"(?!evgroup-)[a-z][a-z-]{1,32}-[0-9a-f]{12,64}")
# a broad tokenizer for free text: any id-shaped run we then test for membership
_FREE_TOKEN_RE = _re.compile(r"[A-Za-z][A-Za-z0-9_-]{5,}")


def _validate_inbound(projection: MissionProjection, *,
                      refs: tuple = (), texts: tuple = ()) -> None:
    """Uniform inbound validation.

    Reference fields are structured id lists: every one must resolve in the
    CALLER's own view — hidden and nonexistent are rejected identically, so
    the refusal carries no existence signal, no hidden id can be laundered
    into a record, and a client echoing REDACTED is refused rather than
    corrupting the record's real reference.

    Free text is scanned for (a) any of the caller's OWN hidden ids
    (shape-independent, from the exact hidden set — a probe or a paste of
    restricted state carries these) and (b) digest-shaped tokens that do not
    resolve. RESIDUAL, stated honestly: a NON-digest, nonexistent id-shaped
    token in prose is not rejected (we cannot distinguish it from an ordinary
    hyphenated word), while a non-digest HIDDEN id is. For an id whose form
    an analyst can guess (a mnemonic object id), that asymmetry is a narrow
    free-text existence oracle. Production ids are digest-shaped, where the
    two branches are symmetric; the residual is bounded to guessable-mnemonic
    ids and is a V6.6 limitation, not a structured-reference one (reference
    FIELDS are airtight: hidden and nonexistent are rejected identically)."""
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
            # a token whose preceding char is '/' or '.' is a URL path or
            # dotted-host segment (reg.example/lookup/lei-0123…), not a record
            # id — analysts paste registry/archive links into notes and prose
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
    """For loose / external-identifier fields: don't require resolution, but
    never let a hidden id or a REDACTED echo be laundered into the record."""
    hidden = projection.hidden_ids
    bad = [str(v) for v in values
           if v and (str(v) == "REDACTED" or str(v).split("@v")[0] in hidden)]
    if bad:
        raise CommandError(
            f"payload carries identifiers you cannot resolve: {sorted(set(bad))[:4]}")


def marked(ctx: "CommandContext", compartments: tuple[str, ...] = ()) -> Marking:
    """The write marking for a NEW record: the server default, optionally
    raised into compartments the actor actually holds. This function itself
    enforces the floor — it raises if the actor does not hold every declared
    compartment, so a record can never be written into access the author
    lacks."""
    if not compartments:
        return ctx.marking
    missing = set(compartments) - set(ctx.context.compartments)
    if missing:
        raise PermissionError("cannot write into compartments you do not hold")
    base = ctx.marking.to_record()
    return Marking(owning_authority=base["owning_authority"],
                   compartments=tuple(compartments),
                   releasability=tuple(base["releasability"]),
                   min_role=base["min_role"], caveats=tuple(base["caveats"]))


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _record_marking(record: Mapping[str, Any]) -> Marking:
    """The marking a record derived from `record` must inherit — a derived
    record is never less restricted than the subject it is about."""
    return marking_from_record(record["marking"]) \
        if isinstance(record.get("marking"), dict) else record["marking"]


def _reference_marking(ctx: "CommandContext", projection: MissionProjection, *,
                       refs: tuple, compartments: tuple[str, ...] = ()) -> Marking:
    """The write marking for a NEW record that CITES existing state: the
    high-water-mark of the actor's declared marking and every reference it
    carries. A record is never less restricted than anything it is about —
    a report/requirement/task/view whose prose or fields reference a
    compartmented subject is itself compartmented. The actor must be cleared
    for the result (`marked` already enforces the compartment floor; this
    also refuses a join above the actor's own access)."""
    markings = [marked(ctx, compartments)]
    for ref in refs:
        if not ref:
            continue
        found = projection.marking_of(str(ref))
        if isinstance(found, dict):
            markings.append(marking_from_record(found))
    result = most_restrictive(markings)
    if not can_view(result, ctx.context):
        raise PermissionError(
            "this record references state above your access; it cannot be "
            "written at a classification you cannot yourself view")
    return result


def _definition_refs(definition: Mapping[str, Any]) -> tuple:
    """Every string value nested in a saved-view definition — candidate
    object/record references whose marking the view must inherit."""
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
        # every record written through this context — the primary record AND
        # its companions (transitions, review items, warnings) — is stamped
        # with `marking`. When operating on an existing subject the caller
        # passes the SUBJECT's marking so a derived record can never be less
        # restricted than the state it is about; `self.marking` (the actor's
        # PUBLIC default) is used only for genuinely new top-level records.
        return AnalyticContext(store=self.store, actor=self.actor,
                               marking=marking or self.marking, now_fn=self.now_fn)

    def pipeline(self, marking: Marking | None = None) -> SemanticPipeline:
        return SemanticPipeline(store=self.store, custody_root=self.root / "custody",
                                actor=self.actor, marking=marking or self.marking,
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
    projection = ctx.projection()
    _validate_inbound(projection, refs=(anchor_ref, reply_to), texts=(text,))
    # the annotation inherits the most restrictive of its TARGET's and its
    # ANCHOR's markings: discussion of compartmented state is compartmented,
    # through EITHER reference field, never PUBLIC-by-default
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


# ---- mission workflow (requirements / tasks) --------------------------------

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
    # a requirement citing compartmented affected state is itself compartmented
    write_marking = _reference_marking(ctx, projection, refs=affected_ids,
                                       compartments=compartments)
    requirement_id = digest_id("req", question, mission_context)
    existing = None
    for record in ctx.store.records_of("information_requirement"):
        if record["requirement_id"] == requirement_id:
            existing = record
    if existing is not None and not can_view(existing.get("marking"), ctx.context):
        # a requirement with this exact question already exists outside the
        # caller's access: refuse without echoing anything about it. (The
        # caller authored the colliding question themselves; refusing is the
        # minimal disclosure — folding would declassify, returning it would
        # disclose.)
        raise PermissionError("cannot open this requirement in your context")
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
        # invisible and nonexistent are the same refusal
        raise NotFound(f"unknown {subject_kind}: {subject_id}")
    # "answering requires evidence" must mean evidence that RESOLVES:
    # a transition cannot be closed on fabricated or invisible references
    _validate_inbound(projection, refs=evidence_refs, texts=(note,))
    workflow = MissionWorkflow(ctx.store)
    # the transition inherits its subject's marking — it can never be less
    # restricted than the requirement/task it moves
    return workflow.transition(
        subject_kind, subject_id, to_status, actor_id=ctx.actor,
        actor_kind=ctx.actor_kind, evidence_refs=evidence_refs, note=note,
        recorded_time=ctx.now_fn(), marking=_record_marking(subject))


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
    _validate_inbound(projection, texts=(note,))
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
    # the resolution and every companion record inherit the proposal's marking
    return resolve_candidate(ctx.analytic(_record_marking(latest)), proposal_id,
                             accept=accept, actor_id=ctx.actor,
                             actor_kind=ctx.actor_kind, note=note)


def decide_recommendation(ctx: CommandContext, recommendation_id: str, *,
                          state: str, rationale: str) -> dict:
    # visibility gate: a recommendation the actor cannot see is neither
    # decidable nor an existence oracle (unknown and forbidden are identical)
    recommendation = None
    for record in ctx.store.records_of("recommendation"):
        if record["recommendation_id"] == recommendation_id:
            recommendation = record
    if recommendation is None or not can_view(recommendation.get("marking"), ctx.context):
        raise NotFound(f"unknown recommendation: {recommendation_id}")
    _validate_inbound(ctx.projection(), texts=(rationale,))
    engine = WorkflowEngine(ctx.store)
    # the decision inherits the recommendation's marking
    return engine.decide(recommendation_id, context=ctx.context, state=state,
                         rationale=rationale, recorded_time=ctx.now_fn(),
                         marking=_record_marking(recommendation))


# ---- hypotheses --------------------------------------------------------------

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
                    assumption_ids: tuple[str, ...] = (),
                    compartments: tuple[str, ...] = ()) -> dict:
    """An authored probability: the workbench offers no machine-derived
    numbers and no recalculation. Provenance is the human analyst."""
    if ctx.actor_kind != "HUMAN":
        raise PermissionError("a forecast probability is authored by a human "
                              "(or enters as a gated model candidate, not here)")
    projection = ctx.projection()
    _validate_inbound(projection,
                      refs=assumption_ids + tuple(r[1] for r in proposition_refs),
                      texts=(question, outcome_semantics, probability_basis))
    forecast_marking = marked(ctx, compartments)
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
    # the movement and its recorded transition inherit the forecast's marking
    forecast_marking = _record_marking(ctx.store.current_forecasts()[forecast_id])
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
    return resolve_forecast_human(ctx.analytic(forecast_marking), forecast_id,
                                  outcome=outcome, evidence_refs=evidence_refs,
                                  note=rationale, actor_id=ctx.actor,
                                  actor_kind=ctx.actor_kind)


def link_hypothesis_claim(ctx: CommandContext, hypothesis_id: str, *,
                          claim_id: str, stance: str, rationale: str) -> dict:
    from curunir_semantic.hypotheses import link_claim
    projection = ctx.projection()
    if projection.get("hypothesis", hypothesis_id) is None:
        raise NotFound(f"unknown hypothesis: {hypothesis_id}")
    if projection.get("semantic_claim", claim_id) is None:
        raise NotFound(f"unknown claim: {claim_id}")
    _validate_inbound(projection, texts=(rationale,))
    # the hypothesis re-append and any review item it queues inherit the
    # hypothesis's own marking
    hyp_marking = _record_marking(ctx.store.current_hypotheses()[hypothesis_id])
    return link_claim(ctx.store, hypothesis_id, claim_id, stance,
                      rationale=rationale, now=ctx.now_fn(), actor=ctx.actor,
                      marking=hyp_marking)


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
    # a warning discloses its forecast's probability/band/tier — it inherits
    # the most restrictive of the forecast's and objective's markings so it
    # can never be less protected than either
    forecast_marking = _record_marking(ctx.store.current_forecasts()[forecast_id])
    objective_marking = _record_marking(ctx.store.current_objectives()[objective_id])
    warning_marking = most_restrictive([forecast_marking, objective_marking])
    if not can_view(warning_marking, ctx.context):
        # the join of a forecast and objective the actor can each view may be
        # above the actor's own access (split releasability); refuse rather
        # than strand an un-viewable warning
        raise PermissionError(
            "the warning would be classified above your own access; you "
            "cannot project this forecast onto this objective")
    return project_warning(ctx.analytic(warning_marking), forecast_id=forecast_id,
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
    # every record this launch writes — the route re-append, the discriminator
    # update, review items, AND the acquired evidence — inherits the route's
    # marking. A compartmented tasking never lands PUBLIC records; open-source
    # evidence acquired in service of it is over-classified to the route's
    # marking (the safe direction), never under-classified.
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


# ---- watches -----------------------------------------------------------------

def create_watch(ctx: CommandContext, *, need_id: str, target_kind: str,
                 target_ref: str, source_id: str, operation: str,
                 query_value: str, cadence_seconds: int,
                 blind_spots: tuple[str, ...] = (),
                 compartments: tuple[str, ...] = ()) -> dict:
    from curunir_fabric.contracts import WatchDefinition
    # need_id is a loose association (a fabric need id or absent); target_ref
    # and query_value are EXTERNAL identifiers (an LEI, a URL, a phrase), not
    # internal record ids — none is strict-validated. Only the free-text
    # blind-spot notes and REDACTED/hidden leakage are checked.
    _soft_reject(ctx.projection(), (need_id, target_ref, query_value))
    _validate_inbound(ctx.projection(), texts=tuple(blind_spots))
    now = ctx.now_fn()
    watch_id = digest_id("watch", need_id, source_id, operation, query_value)
    raw = ctx.store.latest_by_id("fabric_watch", "watch_id").get(watch_id)
    if raw is not None:
        if not can_view(raw.get("marking"), ctx.context):
            # collision with a watch outside the caller's access: refuse
            # without echoing the id or confirming what exists
            raise PermissionError("cannot create this watch in your context")
        raise Conflict(f"watch {watch_id} already exists; change its state "
                       "through pause/resume, never by re-creation")
    definition = WatchDefinition(
        watch_id=watch_id,
        need_id=need_id, target_kind=target_kind, target_ref=target_ref,
        source_id=source_id, operation=operation, query_value=query_value,
        cadence_seconds=cadence_seconds, active=True, blind_spots=blind_spots,
        created_by=ctx.actor, created_time=now, marking=marked(ctx, compartments))
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
              definition: Mapping[str, Any],
              compartments: tuple[str, ...] = ()) -> dict:
    """Persist an investigation layout (filters/focus/window). Presentation
    state only — never analytical truth."""
    import json as _json
    from .contracts import SavedViewRecord
    ctx.require_visible_marking()
    projection = ctx.projection()
    _validate_inbound(projection,
                      texts=(title, _json.dumps(definition, default=str)))
    now = ctx.now_fn()
    view_id = digest_id("savedview", ctx.actor, view_kind, title)
    existing = ctx.store.latest_by_id("workbench_saved_view", "view_id").get(view_id)
    # a saved view whose focus/filters name compartmented objects is
    # compartmented (its definition embeds those ids)
    write_marking = _reference_marking(ctx, projection,
                                       refs=_definition_refs(definition),
                                       compartments=compartments)
    record = SavedViewRecord(
        view_id=view_id, title=title, author=ctx.actor, view_kind=view_kind,
        definition=dict(definition), recorded_time=now,
        marking=marking_from_record(existing["marking"]) if existing
        else write_marking,
        version=(existing["version"] + 1) if existing else 1)
    try:
        ctx.store.append("WORKBENCH_SAVED_VIEW_RECORDED", record,
                         recorded_time=now, actor=ctx.actor)
    except ValueError as error:
        raise Conflict(str(error)) from error
    return record.to_record()


# ---- reports -----------------------------------------------------------------

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


def create_report(ctx: CommandContext, *, title: str, question: str,
                  sections: list[Mapping[str, Any]],
                  compartments: tuple[str, ...] = ()) -> dict:
    projection = ctx.projection()
    _validate_inbound(projection, texts=(title, question))
    _validate_sections(projection, sections)
    # a report that cites compartmented basis is itself compartmented
    write_marking = _reference_marking(ctx, projection,
                                       refs=_section_refs(sections),
                                       compartments=compartments)
    return reports_module.create_report(
        ctx.store, actor=ctx.actor, marking=write_marking,
        now=ctx.now_fn(), title=title, question=question, sections=sections,
        state_token=projection.state_token)


def edit_report(ctx: CommandContext, report_id: str, *, expected_version: int,
                sections: list[Mapping[str, Any]], title: str | None = None,
                question: str | None = None, change_note: str = "") -> dict:
    projection = ctx.projection()
    current = projection.get("workbench_report", report_id)
    if current is None:
        raise NotFound(f"unknown report: {report_id}")
    _validate_inbound(projection, texts=tuple(t for t in (title, question, change_note) if t))
    _validate_sections(projection, sections)
    # an edit re-append preserves the report's own marking (never re-classifies),
    # so it must not introduce a reference MORE restricted than the report can
    # hold — that prose would sit in an under-classified record. Refuse it.
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
