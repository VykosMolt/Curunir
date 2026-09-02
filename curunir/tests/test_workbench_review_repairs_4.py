"""One invariant covers the whole family of marking-derivation defects: after a
cleared actor runs a command against a restricted subject, nothing newly written
may be visible to an uncleared one. The rest of the module pins single cases."""
from __future__ import annotations

import json

import pytest

from curunir_operational.access import (AccessContext, Marking, can_view,
                                        most_restrictive)
from curunir_operational.contracts import AnalyticalProposal
from curunir_semantic.contracts import ReviewItem
from curunir_workbench import commands
from curunir_workbench.commands import CommandContext, CommandError
from curunir_workbench.projections import MissionProjection

from semantic_support import MARK
from workbench_support import (CTX_A, CTX_B, RESTRICTED_MARK, make_workbench,
                               seed_mission)

pytestmark = pytest.mark.no_db

# Registry metadata carries no marking and is visible to everyone.
_UNMARKED_OK = ("fabric_source_descriptor", "fabric_source_profile",
                "fabric_source_status")


@pytest.fixture()
def mission(tmp_path):
    pipeline, ctx = make_workbench(tmp_path)
    seeded = seed_mission(pipeline, ctx)
    cc = lambda context, marking=MARK: CommandContext(
        store=ctx.store, root=tmp_path, context=context, marking=marking,
        now_fn=ctx.now_fn)
    return ctx, seeded, cc


def _head_seq(store) -> int:
    events = store.events(None)
    return events[-1]["seq"] if events else 0


def _assert_no_leak(store, before_seq, ctx_b, label):
    for event in store.events(None):
        if event["seq"] <= before_seq:
            continue
        record = event["record"]
        if record.get("record_type") in _UNMARKED_OK and "marking" not in record:
            continue
        assert not can_view(record.get("marking"), ctx_b), (
            f"{label}: {event['event_type']} appended a "
            f"{record.get('record_type')} record VISIBLE to the uncleared "
            f"context — declassification by derivation")


def test_no_command_declassifies_a_special_subject(mission):
    """No command run against a restricted subject writes anything an uncleared
    context can see."""
    ctx, seeded, cc = mission
    A = cc(CTX_A)  # holds the SPECIAL compartment
    public_claim = seeded["status_claim"]["claim_id"]

    # ---- restricted subjects, made through the commands under test ----
    forecast = commands.author_forecast(
        A, question="Will the compartmented counterparty default by 2027?",
        outcome_semantics="TRUE iff default recorded",
        horizon_time="2027-08-16T12:00:00+00:00", probability=0.2,
        probability_basis="compartmented exposure analysis",
        proposition_refs=(("claim", public_claim),),
        resolution={"kind": "HUMAN_JUDGMENT", "criteria": "human settles"},
        domain="compartment", compartments=("SPECIAL",))
    hypothesis = commands.create_hypothesis(
        A, statement="The compartmented counterparty is a front",
        case_id="compartment", compartments=("SPECIAL",))

    # The rest of the restricted subjects go in directly, without a command.
    from curunir_analytic.impact import create_objective
    from curunir_analytic.substrate import AnalyticContext
    setup = AnalyticContext(store=ctx.store, actor="analyst-a",
                            marking=RESTRICTED_MARK, now_fn=ctx.now_fn)
    objective = create_objective(
        setup, mission_context="compartment",
        statement="Preserve the compartmented programme", priority="HIGH")
    review = ReviewItem(
        item_id="ri-secret-class", kind="CONTRADICTED", subject_kind="hypothesis",
        subject_id=hypothesis["hypothesis_id"],
        detail="compartmented contradiction", evidence_refs=(), status="OPEN",
        resolution_note="", recorded_time=ctx.now_fn(), marking=RESTRICTED_MARK,
        version=1)
    ctx.store.append("REVIEW_ITEM_RECORDED", review,
                     recorded_time=review.recorded_time, actor="analyst-a")
    proposal = AnalyticalProposal(
        proposal_id="prop-secret-class", inference_id="inf-secret",
        proposal_type="ASSESSMENT", content={"summary": "compartmented proposal"},
        status="PROPOSED", recorded_time=ctx.now_fn(), marking=RESTRICTED_MARK)
    ctx.store.append("ANALYTICAL_PROPOSAL_RECORDED", proposal,
                     recorded_time=proposal.recorded_time, actor="analyst-a")

    forecast_id = forecast["forecast_id"]
    hyp_id = hypothesis["hypothesis_id"]

    # ---- run each command and check nothing visible was written ----
    def run(label, fn):
        before = _head_seq(ctx.store)
        fn()
        _assert_no_leak(ctx.store, before, CTX_B, label)

    run("move_forecast", lambda: commands.move_forecast(
        A, forecast_id, expected_version=1, probability=0.4,
        probability_basis="new compartmented signal", change_reason="movement"))
    run("project_warning", lambda: commands.project_forecast_warning(
        A, forecast_id, objective_id=objective["objective_id"]))
    run("assess_hypothesis", lambda: commands.assess_hypothesis(
        A, hyp_id, expected_version=1, status="WEAKLY_SUPPORTED",
        rationale="compartmented basis"))
    run("link_hypothesis_claim", lambda: commands.link_hypothesis_claim(
        A, hyp_id, claim_id=public_claim, stance="supporting",
        rationale="a public claim links a compartmented hypothesis"))
    run("resolve_model_proposal", lambda: commands.resolve_model_proposal(
        A, "prop-secret-class", accept=False, note="reject compartmented proposal"))
    run("resolve_review_item", lambda: commands.resolve_review_item(
        A, "ri-secret-class", expected_version=1, status="DISMISSED",
        note="handled in compartment"))
    run("annotate", lambda: commands.annotate(
        A, target_kind="analytic_forecast", target_id=forecast_id,
        kind="NOTE", text="watch the compartmented horizon"))
    run("resolve_forecast", lambda: commands.resolve_forecast(
        A, forecast_id, outcome="VOID", rationale="ill-posed",
        evidence_refs=()))

    # The seed's own public warning stays visible; only the restricted one is gone.
    view_b = MissionProjection(ctx.store, CTX_B)
    assert view_b.get("analytic_forecast", forecast_id) is None
    assert view_b.get("hypothesis", hyp_id) is None
    assert not any(w["objective_id"] == objective["objective_id"]
                   for w in view_b.family("strategic_warning"))
    blob = json.dumps(view_b.overview()) + json.dumps(view_b.activity_feed(500))
    assert "compartmented" not in blob


def test_most_restrictive_never_downgrades():
    """A joined marking is viewable only by someone who could view every input, and
    two org-locked authorities have no safe join at all."""
    ctx_a = AccessContext("c", "u", "HUMAN", ("ANALYST",),
                          compartments=("X",), releasability=("PUBLIC",))
    a = Marking("mission", ("X",), ("PUBLIC",))
    b = Marking("mission", (), ("PUBLIC",))
    join = most_restrictive([a, b])
    for probe in (ctx_a, AccessContext("c2", "u2", "HUMAN", ("ANALYST",),
                                        releasability=("PUBLIC",))):
        if can_view(join, probe):
            assert can_view(a, probe) and can_view(b, probe)
    # Two different owning authorities have no single marking that covers both.
    with pytest.raises(ValueError):
        most_restrictive([Marking("auth-a", (), ()), Marking("auth-b", (), ())])
    both = most_restrictive([Marking("m", ("X",), ("PUBLIC",)),
                             Marking("m", ("Y",), ("PUBLIC",))])
    assert set(both.compartments) == {"X", "Y"}
    assert not can_view(both, ctx_a)  # ctx_a does not hold Y


def test_decide_recommendation_gated_and_marked(mission):
    """A recommendation the actor cannot see cannot be decided, and the refusal does
    not reveal that it exists."""
    ctx, seeded, cc = mission
    from curunir_operational.workflow import WorkflowEngine
    engine = WorkflowEngine(ctx.store)
    rec = engine.recommend(
        {"dedup_key": "compartmented-rec", "action_kind": "INFORMATION_REQUEST",
         "proposed_action": "collect more", "rationale": "compartmented gap",
         "evidence_refs": (seeded["status_claim"]["claim_id"],),
         "required_role": "ANALYST"},
        provider_id="model-x", recorded_time=ctx.now_fn(),
        actor="model-x", marking=RESTRICTED_MARK)
    from curunir_workbench.errors import NotFound
    with pytest.raises(NotFound):
        commands.decide_recommendation(cc(CTX_B), rec["recommendation_id"],
                                       state="ACCEPTED", rationale="probe")


def test_discriminator_reappend_preserves_marking(mission):
    """Updating a discriminator keeps its existing marking."""
    ctx, seeded, cc = mission
    from curunir_semantic.contracts import DiscriminatingObservation
    from curunir_semantic.hypotheses import update_discriminator
    disc = DiscriminatingObservation(
        discriminator_id="disc-secret", question="COMPARTMENTED probe?",
        hypothesis_ids=(), claim_ids=(seeded["status_claim"]["claim_id"],), desired_observation_type="ENTITY_ATTRIBUTE",
        desired_subject_ref="LEI:X", desired_attribute="status",
        source_family_hints=(), independence_required=False,
        basis_groups_at_pose=(), requirement_id="", status="OPEN",
        recorded_time=ctx.now_fn(), marking=RESTRICTED_MARK)
    ctx.store.append("DISCRIMINATOR_RECORDED", disc,
                     recorded_time=disc.recorded_time, actor="analyst-a")
    current = ctx.store.latest_by_id("discriminator", "discriminator_id")["disc-secret"]
    # The caller passes a public marking, which must not win.
    update_discriminator(ctx.store, current, {"status": "SATISFIED"},
                         now=ctx.now_fn(), actor="analyst-a", marking=MARK)
    after = ctx.store.latest_by_id("discriminator", "discriminator_id")["disc-secret"]
    assert after["marking"]["compartments"] == ["SPECIAL"]


def test_annotation_anchor_marking_inherited(mission):
    """Anchoring a note to a restricted record hides the note, even on a public
    target."""
    ctx, seeded, cc = mission
    note = commands.annotate(
        cc(CTX_A), target_kind="analytic_forecast",
        target_id=seeded["forecast"]["forecast_id"], kind="NOTE",
        text="anchored discussion",
        anchor_ref=seeded["secret_assumption_id"])
    assert MissionProjection(ctx.store, CTX_B).get(
        "workbench_annotation", note["annotation_id"]) is None


def test_own_visible_content_not_false_rejected(mission):
    """Citing an id from your own report is not mistaken for a leak."""
    ctx, seeded, cc = mission
    report = commands.create_report(
        cc(CTX_A), title="own content", question="?",
        sections=[{"kind": "key_judgments", "title": "KJ", "sentences": [
            {"text": "Acme holds an ISSUED registration.", "status": "SUPPORTED",
             "basis_refs": [seeded["status_claim"]["claim_id"]]}]}])
    sid = report["sections"][0]["sentences"][0]["sentence_id"]
    made = commands.annotate(cc(CTX_A), target_kind="workbench_report",
                             target_id=report["report_id"], kind="NOTE",
                             text=f"see {sid} for the key judgment")
    assert made["annotation_id"]


def test_truncated_hidden_id_is_scrubbed(mission):
    """A truncated hidden id is scrubbed like the full one."""
    ctx, seeded, cc = mission
    secret = seeded["secret_object_id"]
    item = ReviewItem(
        item_id="ri-trunc", kind="STALE_BASIS", subject_kind="object",
        subject_id=seeded["status_claim"]["claim_id"],
        detail=f"basis {secret[:16]} degraded", evidence_refs=(),
        status="OPEN", resolution_note="", recorded_time=ctx.now_fn(),
        marking=MARK, version=1)
    ctx.store.append("REVIEW_ITEM_RECORDED", item,
                     recorded_time=item.recorded_time, actor="analyst-a")
    record_b = MissionProjection(ctx.store, CTX_B).get("review_item", "ri-trunc")
    assert secret[:16] not in json.dumps(record_b)


def test_cluster_partial_hiding_is_surfaced(mission):
    """The entity list says when a cluster has members the reader cannot see."""
    ctx, seeded, cc = mission
    from curunir_operational.contracts import (ObjectVersion, ProvenanceSummary,
                                               RelationshipVersion)
    now = ctx.now_fn
    def obj(oid, marking):
        return ObjectVersion(
            object_id=oid, version=1, object_type="ORGANISATION",
            lifecycle="ACTIVE", labels=(oid,), external_refs=(), valid_from=None,
            valid_to=None, source_time=None, time_precision="UNKNOWN",
            recorded_time=now(), geometry=None, attributes={}, quality={},
            epistemic_state="REPORTED", marking=marking,
            provenance=ProvenanceSummary(mode="OPERATIONAL"))
    ctx.store.append("OBJECT_VERSION_APPENDED", obj("zzz-pub", MARK),
                     recorded_time=now(), actor="t")
    ctx.store.append("OBJECT_VERSION_APPENDED", obj("aaa-sec", RESTRICTED_MARK),
                     recorded_time=now(), actor="t")
    ctx.store.append("RELATIONSHIP_VERSION_APPENDED", RelationshipVersion(
        relationship_id="rel-pc", version=1, relation_type="SAME_AS",
        source_object_id="zzz-pub", target_object_id="aaa-sec", valid_from=None,
        valid_to=None, recorded_time=now(), evidence_refs=(), derivation="ANALYST",
        confidence=0.9, status="ACTIVE", marking=RESTRICTED_MARK,
        provenance=ProvenanceSummary(mode="OPERATIONAL")),
        recorded_time=now(), actor="t")
    from curunir_workbench.views import entity_list
    rows = {r["object_id"]: r for r in entity_list(MissionProjection(ctx.store, CTX_B))}
    assert rows["zzz-pub"]["cluster_partially_hidden"] is True
    assert rows["zzz-pub"]["cluster_id"] == "zzz-pub"
