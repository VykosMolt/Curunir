"""The HTTP API: token auth, per-actor projections, command status codes, and no
hidden record leaking through a response."""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from curunir_workbench.auth import write_registry
from curunir_workbench.server import create_app

from semantic_support import clock
from workbench_support import make_workbench, seed_mission

pytestmark = pytest.mark.no_db


@pytest.fixture()
def client(tmp_path):
    pipeline, ctx = make_workbench(tmp_path)
    seeded = seed_mission(pipeline, ctx)
    actors = tmp_path / "actors.json"
    write_registry(actors, [
        {"token": "token-a", "actor_id": "analyst-a", "actor_kind": "HUMAN",
         "roles": ["ANALYST"], "compartments": ["SPECIAL"],
         "releasability": ["PUBLIC"], "organisation": "workbench-test"},
        {"token": "token-b", "actor_id": "analyst-b", "actor_kind": "HUMAN",
         "roles": ["ANALYST"], "releasability": ["PUBLIC"],
         "organisation": "workbench-test"},
    ])
    # The fixture clock runs ahead of real UTC, so the server needs a later one
    # or the store rejects the out-of-order writes.
    app = create_app(tmp_path, actors, now_fn=clock(start_minute=500))
    return TestClient(app), seeded


def _h(token):
    return {"Authorization": f"Bearer {token}"}


def test_auth_required(client):
    c, _ = client
    assert c.get("/api/overview").status_code == 401
    assert c.get("/api/overview", headers=_h("bogus")).status_code == 401
    ok = c.get("/api/session", headers=_h("token-a"))
    assert ok.status_code == 200 and ok.json()["actor_id"] == "analyst-a"


def test_overview_and_access_difference(client):
    c, seeded = client
    a = c.get("/api/overview", headers=_h("token-a")).json()
    b = c.get("/api/overview", headers=_h("token-b")).json()
    assert a["counts"]["entities"] == b["counts"]["entities"] + 1
    assert seeded["secret_object_id"] not in json.dumps(b)


def test_hidden_record_is_404_for_restricted_actor(client):
    c, seeded = client
    secret = seeded["secret_assumption_id"]
    ok = c.get(f"/api/record/analytic_assumption/{secret}", headers=_h("token-a"))
    assert ok.status_code == 200
    hidden = c.get(f"/api/record/analytic_assumption/{secret}", headers=_h("token-b"))
    assert hidden.status_code == 404
    missing = c.get("/api/record/analytic_assumption/nope", headers=_h("token-b"))
    assert missing.status_code == 404
    assert hidden.json() == missing.json()


def test_search_graph_do_not_leak_over_http(client):
    c, seeded = client
    hits = c.get("/api/search", params={"q": "Sensitive Partner"},
                 headers=_h("token-b")).json()
    assert hits["total"] == 0
    graph = c.get("/api/graph", headers=_h("token-b")).json()
    assert seeded["secret_object_id"] not in json.dumps(graph)


def test_provenance_descent_over_http(client):
    c, seeded = client
    warning_id = seeded["warning"]["warning_id"]
    chain = c.get(f"/api/provenance/descend/strategic_warning/{warning_id}",
                  headers=_h("token-a")).json()
    anchor = chain["claims"][0]["observations"][0]["anchors"][0]
    assert anchor["source"]["source_id"] == "gleif"


def test_annotation_conflict_and_attribution(client):
    c, seeded = client
    made = c.post("/api/commands/annotate", headers=_h("token-a"),
                  json={"target_kind": "analytic_forecast",
                        "target_id": seeded["forecast"]["forecast_id"],
                        "kind": "NOTE", "text": "note from A"})
    assert made.status_code == 200
    annotation = made.json()
    assert annotation["author"] == "analyst-a"
    first = c.post(f"/api/commands/annotations/{annotation['annotation_id']}/resolve",
                   headers=_h("token-a"),
                   json={"expected_version": 1, "status": "RESOLVED", "note": "ok"})
    assert first.status_code == 200
    stale = c.post(f"/api/commands/annotations/{annotation['annotation_id']}/resolve",
                   headers=_h("token-b"),
                   json={"expected_version": 1, "status": "RESOLVED", "note": "me too"})
    assert stale.status_code == 409


def test_forecast_move_guarded_and_versioned(client):
    c, seeded = client
    forecast_id = seeded["forecast"]["forecast_id"]
    moved = c.post(f"/api/commands/forecasts/{forecast_id}/move",
                   headers=_h("token-a"),
                   json={"expected_version": 1, "probability": 0.57,
                         "probability_basis": "registry drift",
                         "change_reason": "new evidence"})
    assert moved.status_code == 200 and moved.json()["probability"] == 0.57
    stale = c.post(f"/api/commands/forecasts/{forecast_id}/move",
                   headers=_h("token-b"),
                   json={"expected_version": 1, "probability": 0.6,
                         "probability_basis": "x", "change_reason": "stale"})
    assert stale.status_code == 409
    record = c.get(f"/api/record/analytic_forecast/{forecast_id}",
                   headers=_h("token-b")).json()
    assert [v["probability"] for v in record["versions"]] == [0.35, 0.57]


def test_report_flow_with_validation_rejection(client):
    c, seeded = client
    claim_id = seeded["status_claim"]["claim_id"]
    created = c.post("/api/commands/reports", headers=_h("token-a"),
                     json={"title": "Acme dossier", "question": "Viable?",
                           "sections": [{"kind": "key_judgments", "title": "KJ",
                                         "sentences": [
                                             {"text": "Acme holds an ISSUED registration.",
                                              "status": "SUPPORTED",
                                              "basis_refs": [claim_id]},
                                             {"text": "Acme has hidden debts.",
                                              "status": "SUPPORTED",
                                              "basis_refs": []}]}]})
    assert created.status_code == 200
    report = created.json()
    submitted = c.post(f"/api/commands/reports/{report['report_id']}/submit",
                       headers=_h("token-a"), json={"expected_version": 1})
    assert submitted.status_code == 200
    self_approve = c.post(f"/api/commands/reports/{report['report_id']}/approve",
                          headers=_h("token-a"),
                          json={"expected_version": submitted.json()["version"]})
    assert self_approve.status_code == 403
    rejected = c.post(f"/api/commands/reports/{report['report_id']}/approve",
                      headers=_h("token-b"),
                      json={"expected_version": submitted.json()["version"]})
    assert rejected.status_code == 422
    assert any(f["code"] == "NO_EVIDENCE_BASIS"
               for f in rejected.json()["findings"])
    # Drop the unsupported sentence and run the flow again.
    edited = c.post(f"/api/commands/reports/{report['report_id']}/edit",
                    headers=_h("token-a"),
                    json={"expected_version": submitted.json()["version"],
                          "sections": [{"kind": "key_judgments", "title": "KJ",
                                        "sentences": [
                                            {"text": "Acme holds an ISSUED registration.",
                                             "status": "SUPPORTED",
                                             "basis_refs": [claim_id]}]}]})
    assert edited.status_code == 200
    resubmitted = c.post(f"/api/commands/reports/{report['report_id']}/submit",
                         headers=_h("token-a"),
                         json={"expected_version": edited.json()["version"]})
    approved = c.post(f"/api/commands/reports/{report['report_id']}/approve",
                      headers=_h("token-b"),
                      json={"expected_version": resubmitted.json()["version"]})
    assert approved.status_code == 200
    assert approved.json()["status"] == "APPROVED"
    dispositions = c.get(f"/api/reports/{report['report_id']}/dispositions",
                         headers=_h("token-a")).json()["dispositions"]
    assert dispositions[-1]["actor_id"] == "analyst-b"
    html = c.get(f"/api/reports/{report['report_id']}/export.html",
                 headers=_h("token-a"))
    assert "[SUPPORTED]" in html.text


def test_workflow_transition_guard_over_http(client):
    c, seeded = client
    task_id = seeded["task"]["task_id"]
    hijack = c.post("/api/commands/workflow/transition", headers=_h("token-a"),
                    json={"subject_kind": "analyst_task", "subject_id": task_id,
                          "to_status": "DONE", "note": "not mine"})
    assert hijack.status_code == 403
    legit = c.post("/api/commands/workflow/transition", headers=_h("token-b"),
                   json={"subject_kind": "analyst_task", "subject_id": task_id,
                         "to_status": "IN_PROGRESS"})
    assert legit.status_code == 200
