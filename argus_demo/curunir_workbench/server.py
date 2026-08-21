"""The workbench HTTP boundary: authorized projections and attributable
commands over one mission store.

Every request authenticates to an actor (bearer token → access context);
every projection is filtered server-side before serialization; every command
carries the authenticated actor into the canonical command layer. Unknown and
forbidden are both 404. Stale writes are 409 with the current version so the
client can rebase instead of losing work.

Run:
    uvicorn curunir_workbench.server:app --port 8100        (env-configured)
or programmatically:
    create_app(mission_root, actors_path)

NOTE: no `from __future__ import annotations` here — the request models are
defined inside create_app, and stringified annotations would make FastAPI
unable to resolve them (silently demoting body models to query params).
"""
import math
import os
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from curunir_operational.access import AccessContext

from . import commands
from .auth import ActorRegistry, AuthError
from .commands import CommandContext
from .errors import Conflict, NotFound
from .projections import MissionProjection
from .provenance import ascend, claim_descent, descend, evidence_view
from .reports import export_html, export_package, role_view, validate_report
from .search import search as search_projection
from .store import WorkbenchStore
from .views import (coverage_matrix, entity_dossier, entity_list, event_dossier,
                    event_list, graph, hypothesis_matrix, map_view, review_queue,
                    source_independence, timeline)

STATIC_DIR = Path(__file__).parent / "static"


def render_safe(value) -> str:
    """Make any error detail UTF-8 renderable without echoing raw bytes."""
    return str(value).encode("utf-8", "replace").decode("utf-8")


def _deep_render_safe(value):
    """Total conversion of validation errors to strict JSON response data."""
    if value is None or isinstance(value, (bool, int)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, str):
        return render_safe(value)
    if isinstance(value, (bytes, bytearray)):
        return bytes(value).decode("utf-8", "replace")
    if isinstance(value, dict):
        return {
            _deep_render_safe(key): _deep_render_safe(item)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_deep_render_safe(item) for item in value]
    return render_safe(value)


def create_app(mission_root: str | Path, actors_path: str | Path,
               now_fn=None) -> FastAPI:
    root = Path(mission_root)
    store = WorkbenchStore(root / "store")
    registry = ActorRegistry(actors_path)
    app = FastAPI(title="Curunír analyst workbench", version="1.0.0",
                  docs_url=None, redoc_url=None, openapi_url=None)
    app.state.root = root
    app.state.store = store
    app.state.registry = registry

    from fastapi.exceptions import RequestValidationError

    @app.exception_handler(RequestValidationError)
    async def validation_error(_request: Request, error: RequestValidationError):
        try:
            detail = _deep_render_safe(error.errors())
        except Exception:
            detail = "invalid request body"
        return JSONResponse(status_code=422, content={"detail": detail})

    # ---- auth ---------------------------------------------------------------

    def context(request: Request) -> AccessContext:
        header = request.headers.get("authorization", "")
        token = header.removeprefix("Bearer ").strip() if header.startswith("Bearer ") else None
        try:
            return registry.context_for(token)
        except AuthError as error:
            raise HTTPException(status_code=401, detail=str(error))

    def fresh_store() -> WorkbenchStore:
        # re-open per request: another process (scheduler, second server,
        # import) may have appended; the store's catch-up handles it
        return WorkbenchStore(root / "store")

    def projection(request: Request) -> MissionProjection:
        return MissionProjection(fresh_store(), context(request))

    def command_context(request: Request) -> CommandContext:
        kwargs = {"now_fn": now_fn} if now_fn is not None else {}
        return CommandContext(store=fresh_store(), root=root,
                              context=context(request),
                              marking=_default_marking(), **kwargs)

    def _default_marking():
        from curunir_operational.access import Marking
        meta = store.meta
        return Marking(owning_authority=meta.get("store_id", "curunir-workbench"),
                       releasability=("PUBLIC",))

    def run(request, fn, *args, **kwargs):
        """Translate command-layer failures into honest HTTP semantics, and
        scrub the RESPONSE through the caller's own projection — a write
        path never returns state its author could not read.

        Only the typed NotFound becomes 404 — a bare KeyError is a server
        bug and must surface as 500, never as an existence claim. Workflow
        authority refusals are 403, not 400."""
        from curunir_operational.missions import MissionWorkflowError
        from curunir_operational.workflow import WorkflowError
        def detail(error):
            return render_safe(error)

        try:
            result = fn(*args, **kwargs)
        except Conflict as error:
            raise HTTPException(status_code=409, detail=detail(error))
        except NotFound as error:
            raise HTTPException(status_code=404, detail=detail(error))
        except PermissionError as error:
            raise HTTPException(status_code=403, detail=detail(error))
        except (MissionWorkflowError, WorkflowError) as error:
            raise HTTPException(status_code=403, detail=detail(error))
        except ValueError as error:
            raise HTTPException(status_code=400, detail=detail(error))
        if isinstance(result, (dict, list)):
            return MissionProjection(fresh_store(), context(request)).redact(
                result if isinstance(result, dict) else {"items": result})
        return result

    def found(record):
        if record is None:
            raise HTTPException(status_code=404, detail="not found")
        return record

    # ---- session ------------------------------------------------------------

    @app.get("/api/session")
    def session(request: Request):
        ctx = context(request)
        return {"actor_id": ctx.actor_id, "actor_kind": ctx.actor_kind,
                "roles": list(ctx.roles), "organisation": ctx.organisation,
                "mission_id": store.meta.get("store_id", "curunir-workbench")}

    # ---- mission projections ------------------------------------------------

    @app.get("/api/overview")
    def overview(request: Request):
        return projection(request).overview()

    @app.get("/api/activity")
    def activity(request: Request, limit: int = Query(100, le=1000)):
        return {"feed": projection(request).activity_feed(limit=limit)}

    @app.get("/api/search")
    def search(request: Request, q: str, types: str = "", status: str = "",
               limit: int = Query(50, le=200)):
        return search_projection(projection(request), q,
                                 types=tuple(t for t in types.split(",") if t),
                                 status=status or None, limit=limit)

    @app.get("/api/family/{record_type}")
    def family(request: Request, record_type: str):
        try:
            return {"records": projection(request).family(record_type)}
        except KeyError as error:
            raise HTTPException(status_code=404, detail=str(error))

    @app.get("/api/record/{record_type}/{record_id}")
    def record(request: Request, record_type: str, record_id: str):
        p = projection(request)
        try:
            current = found(p.get(record_type, record_id))
        except KeyError as error:
            raise HTTPException(status_code=404, detail=str(error))
        return {"current": current,
                "versions": p.versions(record_type, record_id),
                "transitions": p.transitions(record_id),
                "annotations": p.annotations_for(record_id)}

    @app.get("/api/entities")
    def entities(request: Request):
        return {"entities": entity_list(projection(request))}

    @app.get("/api/entities/{object_id}")
    def entity(request: Request, object_id: str):
        return found(entity_dossier(projection(request), object_id))

    @app.get("/api/events")
    def events(request: Request):
        return {"events": event_list(projection(request))}

    @app.get("/api/events/{activity_id}")
    def event(request: Request, activity_id: str):
        return found(event_dossier(projection(request), activity_id))

    @app.get("/api/timeline")
    def get_timeline(request: Request, axis: str = "valid", kinds: str = "",
                     t_from: str = "", t_to: str = ""):
        try:
            return timeline(projection(request), axis=axis,
                            kinds=tuple(k for k in kinds.split(",") if k),
                            t_from=t_from or None, t_to=t_to or None)
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error))

    @app.get("/api/graph")
    def get_graph(request: Request, focus: str = "",
                  depth: int = Query(2, ge=0, le=6),
                  relation_types: str = ""):
        return graph(projection(request), focus=focus or None, depth=depth,
                     relation_types=tuple(t for t in relation_types.split(",") if t))

    @app.get("/api/map")
    def get_map(request: Request):
        return map_view(projection(request))

    @app.get("/api/evidence/{manifestation_id}")
    def evidence(request: Request, manifestation_id: str):
        return found(evidence_view(projection(request), manifestation_id))

    @app.get("/api/provenance/descend/{kind}/{record_id}")
    def provenance_descend(request: Request, kind: str, record_id: str):
        return found(descend(projection(request), kind, record_id))

    @app.get("/api/provenance/ascend/{kind}/{record_id}")
    def provenance_ascend(request: Request, kind: str, record_id: str):
        return found(ascend(projection(request), kind, record_id))

    @app.get("/api/claims/{claim_id}/descent")
    def claim_evidence(request: Request, claim_id: str):
        return found(claim_descent(projection(request), claim_id))

    @app.get("/api/independence")
    def independence(request: Request, claim_ids: str):
        ids = tuple(c for c in claim_ids.split(",") if c)
        if not ids:
            raise HTTPException(status_code=400, detail="claim_ids required")
        return source_independence(projection(request), claim_ids=ids)

    @app.get("/api/hypotheses/matrix")
    def matrix(request: Request, hypothesis_ids: str = ""):
        return hypothesis_matrix(
            projection(request),
            hypothesis_ids=tuple(h for h in hypothesis_ids.split(",") if h))

    @app.get("/api/coverage")
    def coverage(request: Request, need_id: str = ""):
        return coverage_matrix(projection(request), need_id=need_id or None)

    @app.get("/api/review")
    def review(request: Request):
        return review_queue(projection(request))

    # ---- reports ------------------------------------------------------------

    @app.get("/api/reports/{report_id}/validate")
    def report_validate(request: Request, report_id: str):
        p = projection(request)
        report = found(p.get("workbench_report", report_id))
        return validate_report(p, report)

    @app.get("/api/reports/{report_id}/role/{role}")
    def report_role(request: Request, report_id: str, role: str):
        p = projection(request)
        report = found(p.get("workbench_report", report_id))
        try:
            return role_view(p, report, role.upper())
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error))

    @app.get("/api/reports/{report_id}/export")
    def report_export(request: Request, report_id: str):
        p = projection(request)
        return found(export_package(p, p.store, report_id))

    @app.get("/api/reports/{report_id}/export.html")
    def report_export_html(request: Request, report_id: str):
        p = projection(request)
        report = found(p.get("workbench_report", report_id))
        return HTMLResponse(export_html(p, report))

    @app.get("/api/reports/{report_id}/dispositions")
    def report_dispositions(request: Request, report_id: str):
        p = projection(request)
        found(p.get("workbench_report", report_id))
        return {"dispositions": [p.redact(d) for d in
                                 p.store.report_dispositions(report_id)
                                 if p.get("workbench_report_disposition",
                                          d["disposition_id"]) is not None]}

    # ---- commands -----------------------------------------------------------

    class AnnotateBody(BaseModel):
        target_kind: str; target_id: str
        kind: str = "NOTE"; text: str
        reply_to: str = ""; anchor_ref: str = ""

    @app.post("/api/commands/annotate")
    def cmd_annotate(request: Request, body: AnnotateBody):
        return run(request, commands.annotate, command_context(request), **body.model_dump())

    class ResolveAnnotationBody(BaseModel):
        expected_version: int; status: str; note: str

    @app.post("/api/commands/annotations/{annotation_id}/resolve")
    def cmd_resolve_annotation(request: Request, annotation_id: str,
                               body: ResolveAnnotationBody):
        return run(request, commands.resolve_annotation, command_context(request),
                   annotation_id, **body.model_dump())

    class RequirementBody(BaseModel):
        question: str; priority: str = "MEDIUM"; mission_context: str
        rationale: str; affected_ids: list[str] = Field(default_factory=list)
        compartments: list[str] = Field(default_factory=list)

    @app.post("/api/commands/requirements")
    def cmd_requirement(request: Request, body: RequirementBody):
        data = body.model_dump()
        data["affected_ids"] = tuple(data["affected_ids"])
        data["compartments"] = tuple(data["compartments"])
        return run(request, commands.open_requirement, command_context(request), **data)

    class TaskBody(BaseModel):
        assigned_actor: str; task_type: str; required_action: str
        affected_ids: list[str] = Field(default_factory=list)
        due_time: str | None = None
        compartments: list[str] = Field(default_factory=list)

    @app.post("/api/commands/tasks")
    def cmd_task(request: Request, body: TaskBody):
        data = body.model_dump()
        data["affected_ids"] = tuple(data["affected_ids"])
        data["compartments"] = tuple(data["compartments"])
        return run(request, commands.assign_task, command_context(request), **data)

    class TransitionBody(BaseModel):
        subject_kind: str; subject_id: str; to_status: str
        evidence_refs: list[str] = Field(default_factory=list)
        note: str = ""

    @app.post("/api/commands/workflow/transition")
    def cmd_transition(request: Request, body: TransitionBody):
        data = body.model_dump()
        data["evidence_refs"] = tuple(data["evidence_refs"])
        return run(request, commands.transition_workflow, command_context(request), **data)

    class ReviewBody(BaseModel):
        expected_version: int; status: str; note: str

    @app.post("/api/commands/review/{item_id}/resolve")
    def cmd_review(request: Request, item_id: str, body: ReviewBody):
        return run(request, commands.resolve_review_item, command_context(request),
                   item_id, **body.model_dump())

    class ProposalBody(BaseModel):
        accept: bool; note: str = ""

    @app.post("/api/commands/proposals/{proposal_id}/resolve")
    def cmd_proposal(request: Request, proposal_id: str, body: ProposalBody):
        return run(request, commands.resolve_model_proposal, command_context(request),
                   proposal_id, **body.model_dump())

    class HypothesisBody(BaseModel):
        statement: str; case_id: str
        assumptions: list[str] = Field(default_factory=list)
        unknowns: list[str] = Field(default_factory=list)
        compartments: list[str] = Field(default_factory=list)

    @app.post("/api/commands/hypotheses")
    def cmd_hypothesis(request: Request, body: HypothesisBody):
        return run(request, commands.create_hypothesis, command_context(request),
                   statement=body.statement, case_id=body.case_id,
                   assumptions=tuple(body.assumptions),
                   unknowns=tuple(body.unknowns),
                   compartments=tuple(body.compartments))

    class AssessBody(BaseModel):
        expected_version: int; status: str; rationale: str

    @app.post("/api/commands/hypotheses/{hypothesis_id}/assess")
    def cmd_assess(request: Request, hypothesis_id: str, body: AssessBody):
        return run(request, commands.assess_hypothesis, command_context(request),
                   hypothesis_id, **body.model_dump())

    class ForecastBody(BaseModel):
        question: str; outcome_semantics: str; horizon_time: str
        probability: float; probability_basis: str
        proposition_refs: list[list[str]]
        resolution: dict[str, Any]
        domain: str
        assumption_ids: list[str] = Field(default_factory=list)
        compartments: list[str] = Field(default_factory=list)

    @app.post("/api/commands/forecasts")
    def cmd_forecast(request: Request, body: ForecastBody):
        return run(request, commands.author_forecast, command_context(request),
                   question=body.question, outcome_semantics=body.outcome_semantics,
                   horizon_time=body.horizon_time, probability=body.probability,
                   probability_basis=body.probability_basis,
                   proposition_refs=tuple((k, v) for k, v in body.proposition_refs),
                   resolution=body.resolution, domain=body.domain,
                   assumption_ids=tuple(body.assumption_ids),
                   compartments=tuple(body.compartments))

    class MoveForecastBody(BaseModel):
        expected_version: int; probability: float
        probability_basis: str; change_reason: str
        evidence_refs: list[str] = Field(default_factory=list)

    @app.post("/api/commands/forecasts/{forecast_id}/move")
    def cmd_move_forecast(request: Request, forecast_id: str, body: MoveForecastBody):
        data = body.model_dump()
        data["evidence_refs"] = tuple(data["evidence_refs"])
        return run(request, commands.move_forecast, command_context(request),
                   forecast_id, **data)

    class ResolveForecastBody(BaseModel):
        outcome: str; rationale: str
        evidence_refs: list[str] = Field(default_factory=list)

    @app.post("/api/commands/forecasts/{forecast_id}/resolve")
    def cmd_resolve_forecast(request: Request, forecast_id: str,
                             body: ResolveForecastBody):
        return run(request, commands.resolve_forecast, command_context(request),
                   forecast_id, outcome=body.outcome, rationale=body.rationale,
                   evidence_refs=tuple(body.evidence_refs))

    class LinkClaimBody(BaseModel):
        claim_id: str; stance: str; rationale: str

    @app.post("/api/commands/hypotheses/{hypothesis_id}/link")
    def cmd_link_claim(request: Request, hypothesis_id: str, body: LinkClaimBody):
        return run(request, commands.link_hypothesis_claim, command_context(request),
                   hypothesis_id, **body.model_dump())

    class ProjectWarningBody(BaseModel):
        objective_id: str

    @app.post("/api/commands/forecasts/{forecast_id}/project-warning")
    def cmd_project_warning(request: Request, forecast_id: str,
                            body: ProjectWarningBody):
        return run(request, commands.project_forecast_warning, command_context(request),
                   forecast_id, objective_id=body.objective_id)

    @app.post("/api/commands/routes/{route_id}/launch")
    def cmd_launch_route(request: Request, route_id: str):
        return run(request, commands.launch_route, command_context(request), route_id)

    class AssignRouteBody(BaseModel):
        assigned_actor: str

    @app.post("/api/commands/routes/{route_id}/assign")
    def cmd_assign_route(request: Request, route_id: str, body: AssignRouteBody):
        return run(request, commands.assign_route, command_context(request), route_id,
                   assigned_actor=body.assigned_actor)

    class WatchBody(BaseModel):
        need_id: str; target_kind: str; target_ref: str
        source_id: str; operation: str; query_value: str
        cadence_seconds: int
        blind_spots: list[str] = Field(default_factory=list)
        compartments: list[str] = Field(default_factory=list)

    @app.post("/api/commands/watches")
    def cmd_watch(request: Request, body: WatchBody):
        data = body.model_dump()
        data["blind_spots"] = tuple(data["blind_spots"])
        data["compartments"] = tuple(data["compartments"])
        return run(request, commands.create_watch, command_context(request), **data)

    class WatchActiveBody(BaseModel):
        active: bool
        expected_active: bool | None = None

    @app.post("/api/commands/watches/{watch_id}/active")
    def cmd_watch_active(request: Request, watch_id: str, body: WatchActiveBody):
        return run(request, commands.set_watch_active, command_context(request),
                   watch_id, active=body.active,
                   expected_active=body.expected_active)

    class SavedViewBody(BaseModel):
        title: str; view_kind: str
        definition: dict[str, Any] = Field(default_factory=dict)
        compartments: list[str] = Field(default_factory=list)

    @app.post("/api/commands/saved-views")
    def cmd_saved_view(request: Request, body: SavedViewBody):
        data = body.model_dump()
        data["compartments"] = tuple(data["compartments"])
        return run(request, commands.save_view, command_context(request), **data)

    class ReportCreateBody(BaseModel):
        title: str; question: str
        sections: list[dict[str, Any]] = Field(default_factory=list)
        compartments: list[str] = Field(default_factory=list)

    @app.post("/api/commands/reports")
    def cmd_report_create(request: Request, body: ReportCreateBody):
        data = body.model_dump()
        data["compartments"] = tuple(data["compartments"])
        return run(request, commands.create_report, command_context(request), **data)

    class ReportEditBody(BaseModel):
        expected_version: int
        sections: list[dict[str, Any]]
        title: str | None = None; question: str | None = None
        change_note: str = ""

    @app.post("/api/commands/reports/{report_id}/edit")
    def cmd_report_edit(request: Request, report_id: str, body: ReportEditBody):
        return run(request, commands.edit_report, command_context(request), report_id,
                   **body.model_dump())

    class ReportVersionBody(BaseModel):
        expected_version: int

    @app.post("/api/commands/reports/{report_id}/submit")
    def cmd_report_submit(request: Request, report_id: str, body: ReportVersionBody):
        return run(request, commands.submit_report, command_context(request), report_id,
                   expected_version=body.expected_version)

    class ReportApproveBody(BaseModel):
        expected_version: int; note: str = ""
        acknowledge_dissent: list[str] = Field(default_factory=list)

    @app.post("/api/commands/reports/{report_id}/approve")
    def cmd_report_approve(request: Request, report_id: str, body: ReportApproveBody):
        from .reports import ReportValidationError
        try:
            return run(request, commands.approve_report, command_context(request), report_id,
                       expected_version=body.expected_version, note=body.note,
                       acknowledge_dissent=tuple(body.acknowledge_dissent))
        except ReportValidationError as error:
            return JSONResponse(status_code=422,
                                content={"detail": "validation rejected approval",
                                         "findings": error.findings})

    class ReportRejectBody(BaseModel):
        expected_version: int; note: str
        return_for_revision: bool = False

    @app.post("/api/commands/reports/{report_id}/reject")
    def cmd_report_reject(request: Request, report_id: str, body: ReportRejectBody):
        return run(request, commands.reject_report, command_context(request), report_id,
                   **body.model_dump())

    # ---- cryptographic identity -------------------------------------------

    from datetime import datetime, timezone
    from curunir_identity import KeyRegistry, SessionManager, SignatureRejected
    from curunir_identity.crypto import normalize_public_key
    from curunir_identity.sessions import AuthError as IdentityAuthError
    from curunir_operational.access import marking_from_record
    from .signed_ops import SignedOperations

    identity_now = now_fn if now_fn is not None else \
        (lambda: datetime.now(timezone.utc).isoformat())
    app.state.sessions = SessionManager(now_fn=identity_now)

    def mission_id() -> str:
        return store.meta.get("store_id", "curunir-workbench")

    class EnrollBody(BaseModel):
        public_key_hex: str

    @app.post("/api/auth/enroll")
    def cmd_enroll(request: Request, body: EnrollBody):
        principal = context(request)
        try:
            public_key = normalize_public_key(body.public_key_hex)
            record = KeyRegistry(
                fresh_store(), marking=_default_marking(), now_fn=identity_now,
            ).enroll(
                actor_id=principal.actor_id,
                actor_kind=principal.actor_kind,
                public_key_hex=public_key,
            )
        except ValueError as error:
            raise HTTPException(status_code=409, detail=render_safe(error))
        return {
            "key_id": record["key_id"],
            "actor_id": record["actor_id"],
            "actor_kind": record["actor_kind"],
            "status": record["status"],
        }

    @app.get("/api/auth/time")
    def cmd_server_time(request: Request):
        context(request)
        return {"now": identity_now()}

    class ChallengeBody(BaseModel):
        actor_id: str

    @app.post("/api/auth/challenge")
    def cmd_challenge(request: Request, body: ChallengeBody):
        principal = context(request)
        if body.actor_id != principal.actor_id:
            raise HTTPException(
                status_code=403,
                detail="a bearer may issue challenges only for its own actor",
            )
        try:
            return app.state.sessions.issue_challenge(
                principal.actor_id, owner=principal.actor_id)
        except IdentityAuthError as error:
            raise HTTPException(status_code=401, detail=render_safe(error))

    class AuthenticateBody(BaseModel):
        actor_id: str
        nonce: str
        signature: str

    @app.post("/api/auth/authenticate")
    def cmd_authenticate(body: AuthenticateBody):
        try:
            session = app.state.sessions.authenticate(
                KeyRegistry(fresh_store()),
                actor_id=body.actor_id,
                nonce=body.nonce,
                signature_hex=body.signature,
            )
        except IdentityAuthError as error:
            raise HTTPException(status_code=401, detail=render_safe(error))
        return {
            "session_id": session.session_id,
            "actor_id": session.actor_id,
            "actor_kind": session.actor_kind,
            "expires_time": session.expires_time,
        }

    class SignedApproveBody(BaseModel):
        session_id: str
        payload: dict[str, Any]
        signature: str
        expected_version: int

    @app.post("/api/commands/reports/{report_id}/approve-signed")
    def cmd_report_approve_signed(report_id: str, body: SignedApproveBody):
        from .reports import ReportValidationError

        signed_command = body.payload.get("command")
        if not isinstance(signed_command, dict):
            raise HTTPException(
                status_code=400, detail="signed command must be an object")
        dissent = signed_command.get("acknowledge_dissent", []) or []
        if not isinstance(dissent, list) \
                or not all(isinstance(item, str) for item in dissent):
            raise HTTPException(
                status_code=400,
                detail="acknowledge_dissent must be a list of ids",
            )
        signed_store = fresh_store()
        current = signed_store.current_reports().get(report_id)
        if current is None:
            raise HTTPException(status_code=404, detail="unknown report")
        operations = SignedOperations(
            store=signed_store,
            root=root,
            registry=KeyRegistry(signed_store),
            sessions=app.state.sessions,
            authz=registry,
            now_fn=identity_now,
            mission_id=mission_id(),
        )
        try:
            result = operations.apply_signed(
                session_id=body.session_id,
                payload=body.payload,
                signature_hex=body.signature,
                action_type="approve_report",
                target_kind="workbench_report",
                target_id=report_id,
                current_version_token=(
                    f"workbench_report:{report_id}@v{current['version']}"
                ),
                target_marking=marking_from_record(current["marking"]),
                apply=lambda command_context: commands.approve_report(
                    command_context,
                    report_id,
                    # apply_signed already proved that the signed target token
                    # equals this exact raw current version.  The unsigned
                    # compatibility field in the HTTP body is not authority.
                    expected_version=current["version"],
                    note=str(signed_command.get("note", "")),
                    acknowledge_dissent=tuple(dissent),
                ),
            )
        except SignatureRejected as error:
            raise HTTPException(
                status_code=401,
                detail=render_safe(f"{error.status}: {error}"),
            )
        except ReportValidationError as error:
            return JSONResponse(
                status_code=422,
                content={
                    "detail": "validation rejected approval",
                    "findings": _deep_render_safe(error.findings),
                },
            )
        except Conflict as error:
            raise HTTPException(status_code=409, detail=render_safe(error))
        except PermissionError as error:
            raise HTTPException(status_code=403, detail=render_safe(error))
        except ValueError as error:
            raise HTTPException(status_code=400, detail=render_safe(error))
        actor_context = registry.context_for_actor(
            result["signed_action"]["actor_id"])
        return MissionProjection(fresh_store(), actor_context).redact(
            result["result"])

    # ---- UI -----------------------------------------------------------------

    if STATIC_DIR.exists():
        app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

        @app.get("/", response_class=HTMLResponse)
        def index():
            return FileResponse(STATIC_DIR / "index.html")

    return app


def app() -> FastAPI:
    """uvicorn entry: CURUNIR_MISSION_ROOT + CURUNIR_ACTORS point at the
    mission root and actor registry."""
    mission_root = os.environ.get("CURUNIR_MISSION_ROOT")
    actors = os.environ.get("CURUNIR_ACTORS")
    if not mission_root or not actors:
        raise RuntimeError("set CURUNIR_MISSION_ROOT and CURUNIR_ACTORS")
    return create_app(mission_root, actors)
