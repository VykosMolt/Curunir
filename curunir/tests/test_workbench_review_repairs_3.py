"""Regression pins: re-appends keep their marking, an annotation inherits its
target's, inbound references are all checked the same way, and dissent rides
along into validation and exports."""
from __future__ import annotations

import json

import pytest

from curunir_operational.access import AccessContext
from curunir_workbench import commands
from curunir_workbench.commands import CommandContext, CommandError
from curunir_workbench.projections import MissionProjection
from curunir_workbench.reports import export_html, export_package, validate_report

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
    return ctx, seeded, cc


def test_forecast_move_never_declassifies(mission):
    """Moving a restricted forecast keeps it restricted."""
    ctx, seeded, cc = mission
    secret_forecast = commands.author_forecast(
        cc(CTX_A), question="Will the compartmented partner default by 2027?",
        outcome_semantics="TRUE iff default recorded",
        horizon_time="2027-08-16T12:00:00+00:00", probability=0.2,
        probability_basis="compartmented exposure analysis",
        proposition_refs=(("claim", seeded["status_claim"]["claim_id"]),),
        resolution={"kind": "HUMAN_JUDGMENT", "criteria": "human settles"},
        domain="compartment", compartments=("SPECIAL",))
    forecast_id = secret_forecast["forecast_id"]
    assert MissionProjection(ctx.store, CTX_B).get("analytic_forecast", forecast_id) is None
    moved = commands.move_forecast(cc(CTX_A), forecast_id, expected_version=1,
                                   probability=0.4,
                                   probability_basis="new compartmented signal",
                                   change_reason="movement")
    assert moved["probability"] == 0.4
    view_b = MissionProjection(ctx.store, CTX_B)
    assert view_b.get("analytic_forecast", forecast_id) is None
    assert forecast_id not in json.dumps(view_b.family("analytic_forecast"))


def test_hypothesis_link_never_declassifies(mission):
    """Linking a claim to a restricted hypothesis keeps it restricted."""
    ctx, seeded, cc = mission
    secret_hypothesis = commands.create_hypothesis(
        cc(CTX_A), statement="The compartmented partner is a front",
        case_id="compartment", compartments=("SPECIAL",))
    hid = secret_hypothesis["hypothesis_id"]
    assert MissionProjection(ctx.store, CTX_B).get("hypothesis", hid) is None
    commands.link_hypothesis_claim(cc(CTX_A), hid,
                                   claim_id=seeded["status_claim"]["claim_id"],
                                   stance="supporting", rationale="registry basis")
    assert MissionProjection(ctx.store, CTX_B).get("hypothesis", hid) is None


def test_annotation_inherits_target_marking(mission):
    """A note about a hidden record is hidden too."""
    ctx, seeded, cc = mission
    note = commands.annotate(cc(CTX_A), target_kind="analytic_assumption",
                             target_id=seeded["secret_assumption_id"],
                             kind="DISSENT",
                             text="the partner dependency is overstated")
    view_b = MissionProjection(ctx.store, CTX_B)
    assert view_b.get("workbench_annotation", note["annotation_id"]) is None
    assert "overstated" not in json.dumps(view_b.family("workbench_annotation"))
    # A public target still gives a public annotation.
    public = commands.annotate(cc(CTX_A), target_kind="analytic_forecast",
                               target_id=seeded["forecast"]["forecast_id"],
                               kind="NOTE", text="public note")
    assert view_b.store is ctx.store
    assert MissionProjection(ctx.store, CTX_B).get(
        "workbench_annotation", public["annotation_id"]) is not None


def test_author_declared_compartments_enforced(mission):
    """Compartments on creates are validated against the actor's holdings."""
    ctx, seeded, cc = mission
    with pytest.raises(PermissionError):
        commands.create_hypothesis(cc(CTX_B), statement="sneaky",
                                   case_id="x", compartments=("SPECIAL",))


def test_inbound_refs_uniformly_validated(mission):
    """A hidden id, a made-up id and an echoed REDACTED marker in a payload all get
    the same refusal, so a caller cannot tell them apart."""
    ctx, seeded, cc = mission
    hidden_id = seeded["secret_assumption_id"]
    fake_id = "assumption-00000000000000000000"
    for probe in (hidden_id, fake_id):
        with pytest.raises(CommandError):
            commands.annotate(cc(CTX_B), target_kind="analytic_forecast",
                              target_id=seeded["forecast"]["forecast_id"],
                              kind="NOTE", text=f"is {probe} involved?")
    with pytest.raises(CommandError):
        commands.create_report(cc(CTX_B), title="probe", question="?",
                               sections=[{"kind": "key_judgments", "title": "KJ",
                                          "sentences": [{"text": "x.",
                                                         "status": "SUPPORTED",
                                                         "basis_refs": ["REDACTED"]}]}])
    with pytest.raises(CommandError):
        commands.transition_workflow(
            cc(CTX_A), subject_kind="requirement",
            subject_id=seeded["requirement"]["requirement_id"],
            to_status="ANSWERED",
            evidence_refs=("observation-feedfacefeedfacefeed",))


def test_sentence_ids_are_report_scoped(mission):
    """The same sentence in two reports gets two ids, so dissent on one leaves the
    other approvable."""
    ctx, seeded, cc = mission
    claim_id = seeded["status_claim"]["claim_id"]
    sections = [{"kind": "key_judgments", "title": "KJ", "sentences": [
        {"text": "Acme holds an ISSUED registration.", "status": "SUPPORTED",
         "basis_refs": [claim_id]}]}]
    one = commands.create_report(cc(CTX_A), title="report one", question="?",
                                 sections=sections)
    two = commands.create_report(cc(CTX_A), title="report two", question="?",
                                 sections=sections)
    sid_one = one["sections"][0]["sentences"][0]["sentence_id"]
    sid_two = two["sections"][0]["sentences"][0]["sentence_id"]
    assert sid_one != sid_two
    commands.annotate(cc(CTX_B), target_kind="report_sentence",
                      target_id=sid_one, kind="DISSENT", text="disagree on one")
    submitted = commands.submit_report(cc(CTX_A), two["report_id"],
                                       expected_version=1)
    approved = commands.approve_report(cc(CTX_B), two["report_id"],
                                       expected_version=submitted["version"])
    assert approved["status"] == "APPROVED"


def test_validation_exposes_dissent_and_exports_carry_it(mission):
    """Open dissent shows up in validation and in both export formats."""
    ctx, seeded, cc = mission
    claim_id = seeded["status_claim"]["claim_id"]
    report = commands.create_report(cc(CTX_A), title="dissent export", question="?",
                                    sections=[{"kind": "key_judgments", "title": "KJ",
                                               "sentences": [
                                                   {"text": "Acme holds an ISSUED registration.",
                                                    "status": "SUPPORTED",
                                                    "basis_refs": [claim_id]}]}])
    sid = report["sections"][0]["sentences"][0]["sentence_id"]
    commands.annotate(cc(CTX_B), target_kind="report_sentence", target_id=sid,
                      kind="DISSENT", text="single-origin basis")
    projection = MissionProjection(ctx.store, CTX_A)
    validation = validate_report(projection, projection.get("workbench_report",
                                                            report["report_id"]))
    assert len(validation["open_dissent"]) == 1
    package = export_package(projection, ctx.store, report["report_id"])
    assert len(package["dissent"]) == 1
    html = export_html(projection, projection.get("workbench_report",
                                                  report["report_id"]))
    assert "single-origin basis" in html and "Dissent" in html


def test_cluster_never_merges_under_redaction(mission):
    """When a cluster's anchor is hidden, the visible members keep separate
    identities instead of collapsing into one redacted cluster."""
    ctx, seeded, cc = mission
    from curunir_operational.contracts import (ObjectVersion, ProvenanceSummary,
                                               RelationshipVersion)
    now = ctx.now_fn
    def obj(oid, marking):
        return ObjectVersion(
            object_id=oid, version=1, object_type="ORGANISATION",
            lifecycle="ACTIVE", labels=(oid,), external_refs=(),
            valid_from=None, valid_to=None, source_time=None,
            time_precision="UNKNOWN", recorded_time=now(), geometry=None,
            attributes={}, quality={}, epistemic_state="REPORTED",
            marking=marking, provenance=ProvenanceSummary(mode="OPERATIONAL"))
    for oid, marking in (("zzz-public-1", MARK), ("zzz-public-2", MARK),
                         ("aaa-secret-1", RESTRICTED_MARK),
                         ("aaa-secret-2", RESTRICTED_MARK)):
        ctx.store.append("OBJECT_VERSION_APPENDED", obj(oid, marking),
                         recorded_time=now(), actor="t")
    def same_as(rid, a, b):
        return RelationshipVersion(
            relationship_id=rid, version=1, relation_type="SAME_AS",
            source_object_id=a, target_object_id=b, valid_from=None,
            valid_to=None, recorded_time=now(), evidence_refs=(),
            derivation="ANALYST", confidence=0.9, status="ACTIVE",
            marking=RESTRICTED_MARK, provenance=ProvenanceSummary(mode="OPERATIONAL"))
    ctx.store.append("RELATIONSHIP_VERSION_APPENDED",
                     same_as("rel-c1", "zzz-public-1", "aaa-secret-1"),
                     recorded_time=now(), actor="t")
    ctx.store.append("RELATIONSHIP_VERSION_APPENDED",
                     same_as("rel-c2", "zzz-public-2", "aaa-secret-2"),
                     recorded_time=now(), actor="t")
    view_b = MissionProjection(ctx.store, CTX_B)
    clusters = {o["object_id"]: o.get("cluster_id")
                for o in view_b.base_view["objects"]
                if o["object_id"].startswith("zzz-")}
    assert clusters["zzz-public-1"] != "REDACTED"
    assert clusters["zzz-public-1"] != clusters["zzz-public-2"] \
        or clusters["zzz-public-1"] in ("zzz-public-1", "zzz-public-2")
    assert {clusters["zzz-public-1"], clusters["zzz-public-2"]} \
        == {"zzz-public-1", "zzz-public-2"}
