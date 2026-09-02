"""One store per process, projections cached by chain head.

The server opens the log once, catches up on the tail before every request,
reuses a viewer's projection while the head is unchanged, and sees an append
made by another store instance on the next request. Concurrent readers and
writers leave a valid chain.
"""
from __future__ import annotations

import threading

import pytest
from fastapi.testclient import TestClient

import curunir_operational.store as store_module
import curunir_workbench.server as server_module
from curunir_workbench.auth import write_registry
from curunir_workbench.store import WorkbenchStore

from semantic_support import clock
from workbench_support import make_workbench, seed_mission

pytestmark = pytest.mark.no_db


@pytest.fixture()
def mission(tmp_path):
    pipeline, ctx = make_workbench(tmp_path)
    seeded = seed_mission(pipeline, ctx)
    actors = tmp_path / "actors.json"
    write_registry(actors, [
        {"token": "tok-a", "actor_id": "analyst-a", "actor_kind": "HUMAN", "roles": ["ANALYST"],
         "compartments": ["SPECIAL"], "releasability": ["PUBLIC"], "organisation": "wb"},
        {"token": "tok-b", "actor_id": "analyst-b", "actor_kind": "HUMAN", "roles": ["ANALYST"],
         "releasability": ["PUBLIC"], "organisation": "wb"},
    ])
    return tmp_path, actors, seeded


def _client(tmp_path, actors):
    return TestClient(server_module.create_app(tmp_path, actors, now_fn=clock(start_minute=500)))


def _h(token):
    return {"Authorization": f"Bearer {token}"}


def test_requests_do_not_reread_the_log(mission, monkeypatch):
    tmp_path, actors, _ = mission
    client = _client(tmp_path, actors)
    calls = []
    original = store_module._read_regular
    monkeypatch.setattr(store_module, "_read_regular", lambda *a, **k: calls.append(a) or original(*a, **k))
    for _ in range(5):
        assert client.get("/api/overview", headers=_h("tok-a")).status_code == 200
        assert client.get("/api/family/semantic_claim", headers=_h("tok-b")).status_code == 200
    assert not [c for c in calls if str(c[0]).endswith("events.jsonl")], "a request re-read the whole log"


def test_a_projection_is_reused_until_the_head_moves(mission, monkeypatch):
    tmp_path, actors, seeded = mission
    built = []
    real = server_module.MissionProjection

    class Counting(real):
        def __init__(self, *a, **k):
            built.append(1)
            super().__init__(*a, **k)

    monkeypatch.setattr(server_module, "MissionProjection", Counting)
    client = _client(tmp_path, actors)
    for _ in range(4):
        assert client.get("/api/overview", headers=_h("tok-a")).status_code == 200
    assert len(built) == 1
    assert client.get("/api/overview", headers=_h("tok-b")).status_code == 200
    assert len(built) == 2, "a different viewer never shares a projection"
    response = client.post("/api/commands/annotate", headers=_h("tok-a"), json={
        "target_kind": "analytic_forecast", "target_id": seeded["forecast"]["forecast_id"],
        "kind": "NOTE", "text": "moves the head"})
    assert response.status_code == 200, response.text
    # The append moved the head, so filtering the reply already rebuilt A's
    # projection; the next read reuses that one.
    assert len(built) == 3
    assert client.get("/api/overview", headers=_h("tok-a")).status_code == 200
    assert len(built) == 3


def test_an_append_by_another_store_instance_is_seen_on_the_next_request(mission):
    tmp_path, actors, seeded = mission
    client = _client(tmp_path, actors)
    assert client.get("/api/overview", headers=_h("tok-a")).status_code == 200
    other = TestClient(server_module.create_app(tmp_path, actors, now_fn=clock(start_minute=600)))
    response = other.post("/api/commands/annotate", headers=_h("tok-a"), json={
        "target_kind": "analytic_forecast", "target_id": seeded["forecast"]["forecast_id"],
        "kind": "NOTE", "text": "written through a second process"})
    assert response.status_code == 200, response.text
    seen = client.get("/api/family/workbench_annotation", headers=_h("tok-a")).json()["records"]
    assert any(r["text"] == "written through a second process" for r in seen)


def test_concurrent_readers_and_writers_keep_the_chain_valid(mission):
    tmp_path, actors, seeded = mission
    client = _client(tmp_path, actors)
    failures = []

    def reader():
        for _ in range(15):
            r = client.get("/api/overview", headers=_h("tok-b"))
            if r.status_code != 200:
                failures.append(("read", r.status_code, r.text[:200]))

    def writer(n):
        for i in range(8):
            r = client.post("/api/commands/annotate", headers=_h("tok-a"), json={
                "target_kind": "analytic_forecast", "target_id": seeded["forecast"]["forecast_id"],
                "kind": "NOTE", "text": f"writer {n} note {i}"})
            if r.status_code != 200:
                failures.append(("write", r.status_code, r.text[:200]))

    threads = [threading.Thread(target=reader) for _ in range(3)] + [threading.Thread(target=writer, args=(n,)) for n in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert not failures, failures
    chain = WorkbenchStore(tmp_path / "store").verify_chain()
    assert chain["valid"]
    notes = [r for r in WorkbenchStore(tmp_path / "store").records_of("workbench_annotation") if r["text"].startswith("writer ")]
    assert len(notes) == 16
