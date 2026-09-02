"""Ordinary paths where a cleared analyst could push restricted state to an
uncleared one. A new record inherits the most restricted thing it cites; an
existing record refuses to start citing something above its own marking."""
from __future__ import annotations

import json

import pytest

from curunir_operational.access import Marking, marking_from_record
from curunir_operational.contracts import ExternalRef, ObjectVersion, ProvenanceSummary
from curunir_semantic.contracts import ReviewItem
from curunir_workbench import commands
from curunir_workbench.commands import CommandContext, CommandError
from curunir_workbench.projections import MissionProjection

from semantic_support import MARK
from workbench_support import (CTX_A, CTX_B, RESTRICTED_MARK, make_workbench,
                               seed_mission)

pytestmark = pytest.mark.no_db


@pytest.fixture()
def mission(tmp_path):
    pipeline, ctx = make_workbench(tmp_path)
    seeded = seed_mission(pipeline, ctx)
    cc = lambda context, marking=MARK: CommandContext(
        store=ctx.store, root=tmp_path, context=context, marking=marking,
        now_fn=ctx.now_fn)
    return pipeline, ctx, seeded, cc


def _special_forecast(cc, seeded):
    return commands.author_forecast(
        cc(CTX_A), question="compartmented q?", outcome_semantics="TRUE iff x",
        horizon_time="2027-08-16T12:00:00+00:00", probability=0.3,
        probability_basis="compartmented", domain="c",
        proposition_refs=(("claim", seeded["status_claim"]["claim_id"]),),
        resolution={"kind": "HUMAN_JUDGMENT", "criteria": "human"},
        compartments=("SPECIAL",))


def test_f1_identity_sweep_inherits_endpoint_markings(mission):
    """A proposed equivalence between a public and a restricted object, and the
    review item for it, are both restricted."""
    pipeline, ctx, seeded, cc = mission
    from curunir_semantic.worldmodel import propose_cross_scheme_associations
    ref = ExternalRef(system="LEI", external_id="SHAREDLEI0000000001",
                      imported_version="doc-x", ingestion_id="ing-x",
                      identity_bearing=True)
    now = ctx.now_fn
    for oid, marking in (("obj-public-side", MARK), ("obj-special-side", RESTRICTED_MARK)):
        ctx.store.append("OBJECT_VERSION_APPENDED", ObjectVersion(
            object_id=oid, version=1, object_type="ORGANISATION", lifecycle="ACTIVE",
            labels=(oid,), external_refs=(ref,), valid_from=None, valid_to=None,
            source_time=None, time_precision="UNKNOWN", recorded_time=now(),
            geometry=None, attributes={}, quality={}, epistemic_state="REPORTED",
            marking=marking, provenance=ProvenanceSummary(mode="OPERATIONAL")),
            recorded_time=now(), actor="t")
    # An ordinary public pipeline pass runs the sweep.
    propose_cross_scheme_associations(pipeline.context())
    view_b = MissionProjection(ctx.store, CTX_B)
    identity_items = [r for r in view_b.family("review_item")
                      if r["kind"] == "IDENTITY_AMBIGUITY"]
    for item in identity_items:
        assert "obj-special-side" not in json.dumps(item)
    blob = json.dumps(view_b.overview()) + json.dumps(view_b.family("review_item"))
    assert "obj-special-side" not in blob


def test_f2_author_forecast_inherits_reference_marking(mission):
    """A forecast citing a restricted assumption is restricted."""
    pipeline, ctx, seeded, cc = mission
    forecast = commands.author_forecast(
        cc(CTX_A), question="will the compartmented dependency hold?",
        outcome_semantics="TRUE iff x", horizon_time="2027-08-16T12:00:00+00:00",
        probability=0.3, probability_basis="from compartmented assumption",
        domain="c", proposition_refs=(("claim", seeded["status_claim"]["claim_id"]),),
        resolution={"kind": "HUMAN_JUDGMENT", "criteria": "human"},
        assumption_ids=(seeded["secret_assumption_id"],))
    view_b = MissionProjection(ctx.store, CTX_B)
    assert view_b.get("analytic_forecast", forecast["forecast_id"]) is None
    assert "compartmented dependency" not in json.dumps(view_b.family("analytic_forecast"))


def test_f3_link_special_claim_to_public_hypothesis_refused(mission):
    """A public hypothesis cannot be linked to a restricted claim."""
    pipeline, ctx, seeded, cc = mission
    from curunir_semantic.contracts import SemanticClaim
    claim = SemanticClaim(
        claim_id="claim-special-1", version=1, statement="compartmented claim",
        subject_ref="LEI:X", subject_object_id="obj-x", predicate="p",
        object_or_value="v", object_object_id="", valid_from=None, valid_to=None,
        time_precision="UNKNOWN", polarity="AFFIRMED", observation_ids=("obs-x",),
        dependence_group_ids=(), independent_basis_count=0, basis_note="",
        world_refs=(), epistemic_state="EXTRACTED", review_state="UNREVIEWED",
        recorded_time=ctx.now_fn(), marking=RESTRICTED_MARK)
    ctx.store.append("SEMANTIC_CLAIM_RECORDED", claim, recorded_time=claim.recorded_time,
                     actor="t")
    public_hyp = commands.create_hypothesis(cc(CTX_A), statement="public h", case_id="p")
    with pytest.raises(CommandError):
        commands.link_hypothesis_claim(cc(CTX_A), public_hyp["hypothesis_id"],
                                       claim_id="claim-special-1", stance="supporting",
                                       rationale="cites compartmented claim")


def test_f4_saved_view_resave_refuses_more_restricted_ref(mission):
    """Re-saving a public view with a restricted reference is refused."""
    pipeline, ctx, seeded, cc = mission
    commands.save_view(cc(CTX_A), title="My graph", view_kind="graph",
                       definition={"focus": seeded["status_claim"]["subject_object_id"]})
    with pytest.raises(CommandError):
        commands.save_view(cc(CTX_A), title="My graph", view_kind="graph",
                           definition={"focus": seeded["secret_assumption_id"]})


def test_f5_requirement_fold_refuses_more_restricted_ref(mission):
    """Folding a restricted id into a public requirement is refused."""
    pipeline, ctx, seeded, cc = mission
    commands.open_requirement(cc(CTX_A), question="dup q?", priority="MEDIUM",
                              mission_context="m", rationale="r")
    with pytest.raises(CommandError):
        commands.open_requirement(cc(CTX_A), question="dup q?", priority="HIGH",
                                  mission_context="m", rationale="escalate",
                                  affected_ids=(seeded["secret_assumption_id"],))


def test_f6_watch_on_compartmented_target_is_special(mission):
    """A watch aimed at a restricted object is restricted."""
    pipeline, ctx, seeded, cc = mission
    watch = commands.create_watch(
        cc(CTX_A), need_id="n", target_kind="NATIVE_OBJECT",
        target_ref=seeded["secret_object_id"], source_id="gleif",
        operation="LOOKUP", query_value="x", cadence_seconds=3600)
    view_b = MissionProjection(ctx.store, CTX_B)
    assert view_b.get("fabric_watch", watch["watch_id"]) is None
    assert seeded["secret_object_id"] not in json.dumps(view_b.family("fabric_watch"))


def test_f7_transition_and_move_refuse_more_restricted_evidence(mission):
    """A transition or forecast move on a public subject cannot cite restricted
    evidence."""
    pipeline, ctx, seeded, cc = mission
    from curunir_operational.missions import MissionWorkflow
    req = MissionWorkflow(ctx.store).open_requirement(
        mission_context="m", question="pub q?", affected_ids=(), priority="MEDIUM",
        rationale="r", required_evidence_type="OPEN_SOURCE", owning_role="ANALYST",
        closure_criteria="c", due_time=None, recorded_time=ctx.now_fn(),
        marking=MARK, actor="analyst-a")
    with pytest.raises(CommandError):
        commands.transition_workflow(
            cc(CTX_A), subject_kind="requirement",
            subject_id=req["requirement_id"], to_status="EVIDENCE_PENDING",
            evidence_refs=(seeded["secret_assumption_id"],), note="cites special")
    with pytest.raises(CommandError):
        commands.move_forecast(
            cc(CTX_A), seeded["forecast"]["forecast_id"], expected_version=1,
            probability=0.5, probability_basis="b", change_reason="r",
            evidence_refs=(seeded["secret_assumption_id"],))
