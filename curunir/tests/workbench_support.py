"""Shared workbench fixtures: one seeded mission, and two access contexts that
differ only in whether they hold the SPECIAL compartment."""
from __future__ import annotations

import socket
from pathlib import Path

from curunir_analytic.contracts import ImpactEdge, ResolutionRule
from curunir_analytic.forecasts import create_forecast
from curunir_analytic.impact import build_path, create_objective, record_assumption
from curunir_analytic.substrate import AnalyticContext
from curunir_analytic.warning import project_warning
from curunir_fabric.catalog import seed_starter_catalog
from curunir_operational.access import AccessContext, Marking
from curunir_operational.contracts import ObjectVersion, ProvenanceSummary
from curunir_operational.missions import MissionWorkflow
from curunir_semantic.hypotheses import link_claim, record_hypothesis
from curunir_semantic.pipeline import SemanticPipeline
from curunir_workbench.store import WorkbenchStore

from analytic_support import GLEIF_ACME, plant_page, statement_page
from semantic_support import MARK, T0, clock, plant_manifestation

RESTRICTED_MARK = Marking(owning_authority="semantic-test",
                          compartments=("SPECIAL",), releasability=("PUBLIC",))

CTX_A = AccessContext("ctx-a", "analyst-a", "HUMAN", ("ANALYST",),
                      compartments=("SPECIAL",), releasability=("PUBLIC",))
CTX_B = AccessContext("ctx-b", "analyst-b", "HUMAN", ("ANALYST",),
                      releasability=("PUBLIC",))

ACME = "LEI:ACMELEI000000000001"
HORIZON = "2026-08-30T18:00:00+00:00"


def free_port() -> int:
    """A port the operating system says is free right now."""
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def make_workbench(tmp_path: Path, *, seeded: bool = True, start_minute: int = 1
                   ) -> tuple[SemanticPipeline, AnalyticContext]:
    root = tmp_path / "store"
    if (root / "store_meta.json").exists():
        store = WorkbenchStore(root)
    else:
        store = WorkbenchStore.create(root, "workbench-test", T0)
        if seeded:
            seed_starter_catalog(store, recorded_time=T0, actor="t")
    now_fn = clock(start_minute)
    pipeline = SemanticPipeline(store=store, custody_root=tmp_path / "custody",
                                actor="t", marking=MARK, now_fn=now_fn)
    ctx = AnalyticContext(store=store, actor="t", marking=MARK, now_fn=now_fn)
    return pipeline, ctx


def seed_mission(pipeline: SemanticPipeline, ctx: AnalyticContext) -> dict:
    """Build the same mission every time: registry and web evidence through the real
    pipeline, a hypothesis, an objective and impact path, a forecast and its
    warning, an open requirement and task, and one restricted object and
    assumption."""
    store = ctx.store
    plant_manifestation(pipeline, source_id="gleif",
                        native_id="lei/ACMELEI000000000001", body=GLEIF_ACME,
                        media_type="application/json", retrieval_time=T0)
    plant_page(pipeline, url="https://acme.example/about",
               body=statement_page("Acme Industri AS supplies structural steel"),
               retrieval_time=T0)
    pipeline.process_new_evidence()
    claims = {c["predicate"]: c for c in store.current_claims().values()
              if c["subject_ref"] == ACME}
    status_claim = claims["entity_status"]

    hypothesis = record_hypothesis(
        store, statement="Acme Industri AS remains an active Norwegian entity",
        case_id="acme-viability", analyst_or_provider="analyst-a",
        now=ctx.now_fn(), actor="analyst-a", marking=MARK)
    link_claim(store, hypothesis["hypothesis_id"], status_claim["claim_id"],
               "supporting", rationale="registry status ISSUED supports viability",
               now=ctx.now_fn(), actor="analyst-a", marking=MARK)

    objective = create_objective(
        ctx, mission_context="acme-mission",
        statement="Preserve supply-chain visibility on Acme", priority="HIGH")
    assumption = record_assumption(
        ctx, statement="GLEIF registration status reflects operating status",
        supporting_claim_ids=(status_claim["claim_id"],),
        objective_ids=(objective["objective_id"],))
    acme_object_id = status_claim["subject_object_id"]
    edges = (
        ImpactEdge(edge_id="edge-lapse", from_kind="object", from_id=acme_object_id,
                   to_kind="object", to_id="obj-kyc-standing",
                   edge_kind="DEPENDENCY", effect_order="DIRECT",
                   authority="DERIVED", note="",
                   basis_ids=(status_claim["claim_id"],),
                   assumption_ids=(assumption["assumption_id"],)),
        ImpactEdge(edge_id="edge-objective", from_kind="object",
                   from_id="obj-kyc-standing", to_kind="mission_objective",
                   to_id=objective["objective_id"], edge_kind="INFERENCE",
                   effect_order="SECOND_ORDER", authority="SUPPORTED_INFERENCE",
                   note="loss of KYC standing degrades the visibility objective",
                   basis_ids=(), assumption_ids=()),
    )
    path = build_path(
        ctx, objective_id=objective["objective_id"],
        summary="Registry lapse would break KYC standing for Acme contracts",
        edges=edges)

    rule = ResolutionRule(
        kind="CLAIM_PREDICATE",
        criteria=f"GLEIF entity_status for {ACME} reads INACTIVE",
        claim_subject_ref=ACME, claim_attribute="entity_status",
        expected_value="INACTIVE", absence_min_successful_sources=1,
        absence_required_source_ids=("gleif",))
    forecast = create_forecast(
        ctx, question=f"Will {ACME} entity_status read INACTIVE by {HORIZON}?",
        outcome_semantics="TRUE iff GLEIF entity_status equals 'INACTIVE' at "
                          "or before the horizon",
        proposition_refs=(("claim", status_claim["claim_id"]),),
        horizon_time=HORIZON, resolution=rule, probability=0.35,
        probability_basis="registry stable for years; base rate low",
        supporting_claim_ids=(status_claim["claim_id"],),
        assumption_ids=(assumption["assumption_id"],),
        author="analyst-a", domain="corporate-registry")
    warning = project_warning(
        ctx, forecast_id=forecast["forecast_id"],
        objective_id=objective["objective_id"])

    workflow = MissionWorkflow(store)
    requirement = workflow.open_requirement(
        mission_context="acme-mission",
        question="Is Acme's GLEIF registration still ISSUED?",
        affected_ids=(status_claim["claim_id"],), priority="HIGH",
        rationale="forecast horizon approaching", required_evidence_type="OPEN_SOURCE",
        owning_role="ANALYST", closure_criteria="registry evidence newer than horizon",
        due_time=None, recorded_time=ctx.now_fn(), marking=MARK, actor="analyst-a")
    task = workflow.assign_task(
        assigned_role="ANALYST", assigned_actor="analyst-b",
        task_type="COLLECTION_FOLLOWUP",
        affected_ids=(requirement["requirement_id"],),
        required_action="Recheck GLEIF registration status for Acme",
        due_time=None, depends_on=(), recorded_time=ctx.now_fn(),
        marking=MARK, actor="analyst-a")

    # ---- restricted state ----
    now = ctx.now_fn()
    secret_object = ObjectVersion(
        object_id="obj-secret-partner", version=1, object_type="ORGANISATION",
        lifecycle="ACTIVE", labels=("Sensitive Partner AS",), external_refs=(),
        valid_from=None, valid_to=None, source_time=None, time_precision="UNKNOWN",
        recorded_time=now, geometry=None,
        attributes={"note": "compartmented counterparty"},
        quality={"review_state": "UNREVIEWED"}, epistemic_state="REPORTED",
        marking=RESTRICTED_MARK,
        provenance=ProvenanceSummary(mode="OPERATIONAL"))
    store.append("OBJECT_VERSION_APPENDED", secret_object, recorded_time=now, actor="analyst-a")
    secret_assumption = record_assumption(
        AnalyticContext(store=store, actor="analyst-a", marking=RESTRICTED_MARK,
                        now_fn=ctx.now_fn),
        statement="Sensitive Partner AS depends on Acme deliveries",
        supporting_claim_ids=(), objective_ids=())

    return {"claims": claims, "status_claim": status_claim,
            "hypothesis": store.current_hypotheses()[hypothesis["hypothesis_id"]],
            "objective": objective, "assumption": assumption, "path": path,
            "forecast": forecast, "warning": warning,
            "requirement": requirement, "task": task,
            "secret_object_id": secret_object.object_id,
            "secret_assumption_id": secret_assumption["assumption_id"]}
