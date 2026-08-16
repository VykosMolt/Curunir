"""Integrated operator exercise over a REAL mission root, through the
workbench HTTP boundary only — no research scripts, no JSON patching.

    python -m curunir_workbench.demo_mission --root missions/apple_workbench_v66

Prerequisite: the root was populated by the live analytic demo phases
(curunir_analytic.demo phases 1/2/3/6 — real GLEIF/Wikidata/SEC/Wayback
acquisition). This driver then walks the operator loop:

  overview → warning/forecast inspection → provenance descent → hypothesis →
  identity-review disposition → fresh LIVE public-source collection launched
  from the workbench → state update visible → annotation → second restricted
  analyst → dossier (validation rejects an unsupported sentence → repair →
  approve) → restart → replay fingerprint.

Every step is an authenticated HTTP call; failures are reported, not painted
over. The restricted fixture record is deterministic test data, labeled as
such — the public mission itself carries no naturally secret evidence.
"""
from __future__ import annotations

import argparse
import json
import socket
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx
import uvicorn

from curunir_operational.access import Marking
from curunir_workbench.auth import write_registry
from curunir_workbench.server import create_app
from curunir_workbench.store import WorkbenchStore

JAN = {"token": "demo-token-jan", "actor_id": "jan", "actor_kind": "HUMAN",
       "roles": ["ANALYST", "SUPERVISOR"], "compartments": ["SPECIAL"],
       "releasability": ["PUBLIC"], "organisation": "curunir-demo"}
REVIEWER = {"token": "demo-token-reviewer", "actor_id": "reviewer",
            "actor_kind": "HUMAN", "roles": ["ANALYST", "SUPERVISOR"],
            "compartments": ["SPECIAL"], "releasability": ["PUBLIC"],
            "organisation": "curunir-demo"}
RESTRICTED_ANALYST = {"token": "demo-token-b", "actor_id": "analyst-b",
                      "actor_kind": "HUMAN", "roles": ["ANALYST"],
                      "releasability": ["PUBLIC"], "organisation": "curunir-demo"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _ensure_restricted_fixture(root: Path) -> str:
    """One SPECIAL-compartment record (deterministic fixture, labeled) so the
    two-analyst access demonstration has restricted material to protect."""
    from curunir_analytic.impact import record_assumption
    from curunir_analytic.substrate import AnalyticContext
    store = WorkbenchStore(root / "store")
    marking = Marking(owning_authority="curunir-analytic-demo",
                      compartments=("SPECIAL",), releasability=("PUBLIC",))
    ctx = AnalyticContext(store=store, actor="jan", marking=marking, now_fn=_now)
    record = record_assumption(
        ctx, statement="[DETERMINISTIC ACCESS-TEST FIXTURE] Compartmented "
                       "counterparty dependency assumption for SPECIAL holders",
        supporting_claim_ids=(), objective_ids=())
    return record["assumption_id"]


class Step:
    def __init__(self):
        self.results: list[tuple[str, str]] = []

    def ok(self, name: str, detail: str = ""):
        self.results.append((name, f"OK {detail}".strip()))
        print(f"  [OK]   {name}" + (f" — {detail}" if detail else ""))

    def fail(self, name: str, detail: str):
        self.results.append((name, f"FAIL {detail}"))
        print(f"  [FAIL] {name} — {detail}")


def run_exercise(root: Path, base: str, step: Step) -> dict:
    jan = httpx.Client(base_url=base, headers={"Authorization": f"Bearer {JAN['token']}"},
                       timeout=180)
    reviewer = httpx.Client(base_url=base,
                            headers={"Authorization": f"Bearer {REVIEWER['token']}"},
                            timeout=60)
    b = httpx.Client(base_url=base,
                     headers={"Authorization": f"Bearer {RESTRICTED_ANALYST['token']}"},
                     timeout=60)

    # 1-2: mission opens with a coherent picture
    overview = jan.get("/api/overview").json()
    counts_before = overview["counts"]
    step.ok("overview", f"claims={counts_before['claims']} evidence={counts_before['manifestations']} "
            f"forecasts_resolved={counts_before['forecasts_resolved']} review_open={counts_before['review_open']}")

    # 3-5: warning → forecast → evidence descent
    warnings = jan.get("/api/family/strategic_warning").json()["records"]
    warning = warnings[0]
    chain = jan.get(f"/api/provenance/descend/strategic_warning/{warning['warning_id']}").json()
    anchor = chain["claims"][0]["observations"][0]["anchors"][0]
    manifestation_id = anchor["manifestation"]["manifestation_id"]
    step.ok("warning descent", f"tier={warning['tier']} rule={warning['tier_rule_id']} → "
            f"claim → observation → {anchor['anchor'].get('field_path') or 'span'} → "
            f"{manifestation_id[:24]} → source={anchor['source']['source_id']}")

    evidence = jan.get(f"/api/evidence/{manifestation_id}").json()
    has_payload = evidence["payload"] and not evidence["payload"].get("unavailable")
    step.ok("evidence viewer", f"payload_bytes={evidence['payload'].get('bytes')} "
            f"anchors={len(evidence['anchors'])} verified_custody={bool(has_payload)}")

    # 6: hypothesis over the real claims, compared with evidence
    claims = {c["predicate"]: c for c in jan.get("/api/family/semantic_claim").json()["records"]
              if c["subject_ref"].startswith("LEI:")}
    hyp = jan.post("/api/commands/hypotheses", json={
        "statement": "Apple Inc. maintains active GLEIF registry standing",
        "case_id": "apple-corporate-visibility"}).json()
    jan.post(f"/api/commands/hypotheses/{hyp['hypothesis_id']}/link", json={
        "claim_id": claims["registration_status"]["claim_id"], "stance": "supporting",
        "rationale": "registry status ISSUED directly supports"}).raise_for_status()
    matrix = jan.get("/api/hypotheses/matrix").json()
    step.ok("hypothesis matrix", f"hypotheses={len(matrix['hypotheses'])} rows={len(matrix['rows'])}")

    # 7-8: information gap + EIV routes with explanations
    routes = jan.get("/api/family/collection_route").json()["records"]
    proposed = [r for r in routes if r["status"] == "PROPOSED" and r["automatable"]]
    step.ok("EIV routes", f"{len(routes)} routes, {len(proposed)} proposed; "
            f"top explanation: {proposed[0]['explanation'][:90] if proposed else 'n/a'}")

    # 9-12: fresh LIVE public-source acquisition from the workbench
    counts_mid = jan.get("/api/overview").json()["counts"]
    launched = None
    if proposed:
        route = sorted(proposed, key=lambda r: r["rank"])[0]
        response = jan.post(f"/api/commands/routes/{route['route_id']}/launch")
        if response.status_code == 200:
            launched = response.json()
            counts_after = jan.get("/api/overview").json()["counts"]
            step.ok("live collection from workbench",
                    f"route {route['source_id']}/{route['operation']} → "
                    f"evidence {counts_mid['manifestations']}→{counts_after['manifestations']}, "
                    f"claims {counts_mid['claims']}→{counts_after['claims']}")
        else:
            step.fail("live collection from workbench",
                      f"{response.status_code}: {response.text[:200]}")
    else:
        executed = [r for r in routes if r["status"] == "EXECUTED" and r["execution_id"]]
        if executed and all(
                jan.get(f"/api/record/fabric_execution/{r['execution_id']}").status_code == 200
                for r in executed):
            step.ok("live collection from workbench",
                    f"all {len(executed)} routes already executed in a prior run; "
                    "each execution record resolves")
        else:
            step.fail("live collection from workbench", "no proposed automatable route")

    # forecast authored in the workbench + warning projected by the named rule
    horizon = "2027-08-16T12:00:00+00:00"
    objective = jan.get("/api/family/mission_objective").json()["records"][0]
    forecast = jan.post("/api/commands/forecasts", json={
        "question": f"Will GLEIF registration_status for Apple Inc. read LAPSED by {horizon}?",
        "outcome_semantics": "TRUE iff GLEIF registration_status equals 'LAPSED' at or before the horizon",
        "horizon_time": horizon, "probability": 0.05,
        "probability_basis": "registry stable across 12 years of retained evidence; base rate very low",
        "proposition_refs": [["claim", claims["registration_status"]["claim_id"]]],
        "resolution": {"kind": "CLAIM_PREDICATE",
                       "criteria": "GLEIF registration_status reads LAPSED",
                       "claim_subject_ref": claims["registration_status"]["subject_ref"],
                       "claim_attribute": "registration_status",
                       "expected_value": "LAPSED",
                       "absence_min_successful_sources": 1,
                       "absence_required_source_ids": ["gleif"]},
        "domain": "corporate-registry"}).json()
    projected = jan.post(f"/api/commands/forecasts/{forecast['forecast_id']}/project-warning",
                         json={"objective_id": objective["objective_id"]}).json()
    step.ok("authored forecast + projected warning",
            f"p={forecast['probability']} → warning tier={projected['tier']} "
            f"(rule {projected['tier_rule_id']})")

    # 13: evidence-bound annotation
    jan.post("/api/commands/annotate", json={
        "target_kind": "fabric_manifestation", "target_id": manifestation_id,
        "kind": "NOTE",
        "text": "GLEIF registry record anchors the standing theme; recheck at horizon"
    }).raise_for_status()
    step.ok("annotation", "bound to the GLEIF manifestation")

    # 14: second analyst sees the mission by their permissions
    fixture_id = _ensure_restricted_fixture(root)
    seen_by_jan = jan.get(f"/api/record/analytic_assumption/{fixture_id}")
    seen_by_b = b.get(f"/api/record/analytic_assumption/{fixture_id}")
    search_b = b.get("/api/search", params={"q": "ACCESS-TEST FIXTURE"}).json()
    blob_b = json.dumps(b.get("/api/overview").json()) + json.dumps(
        b.get("/api/family/analytic_assumption").json())
    leak_free = (seen_by_jan.status_code == 200 and seen_by_b.status_code == 404
                 and search_b["total"] == 0 and fixture_id not in blob_b)
    (step.ok if leak_free else step.fail)(
        "access-aware projections",
        f"jan={seen_by_jan.status_code} analyst-b={seen_by_b.status_code} "
        f"b_search_hits={search_b['total']} id_in_b_payloads={fixture_id in blob_b}")

    # 15: review disposition (identity ambiguity stays a recorded human act)
    review = jan.get("/api/review").json()
    open_semantic = [i for i in review["items"]
                     if i["status"] == "OPEN" and i["queue"] == "SEMANTIC"]
    if open_semantic:
        item = open_semantic[0]
        jan.post(f"/api/commands/review/{item['id']}/resolve", json={
            "expected_version": item["version"], "status": "DISMISSED",
            "note": "reviewed: wayback-era site identity differs from current entity; "
                    "kept distinct deliberately"}).raise_for_status()
        step.ok("review disposition", f"{item['kind']} dismissed with recorded note")
    else:
        dismissed = [i for i in review["items"]
                     if i["queue"] == "SEMANTIC" and i["status"] != "OPEN"
                     and i["resolution_note"]]
        if dismissed:
            step.ok("review disposition",
                    f"{len(dismissed)} item(s) already dispositioned with recorded "
                    "notes in a prior run")
        else:
            step.fail("review disposition", "no open semantic review item")

    # 16-19: dossier with sentence-level status; validation rejects, repair, approve
    status_claim = claims["registration_status"]
    bad_sections = [{"kind": "key_judgments", "title": "Key judgments", "sentences": [
        {"text": "Apple Inc. holds an ISSUED GLEIF registration.",
         "status": "SUPPORTED", "basis_refs": [status_claim["claim_id"]]},
        {"text": f"The probability of registry lapse within a year is authored at "
                 f"{forecast['probability']:.2f}.",
         "status": "EXPLICITLY_INFERENTIAL",
         "basis_refs": [forecast["forecast_id"]],
         "inference_note": "authored forecast, not an observation"},
        {"text": "Apple's supplier network is under acute registry risk.",
         "status": "SUPPORTED", "basis_refs": []},  # deliberately unsupported
    ]}]
    report = jan.post("/api/commands/reports", json={
        "title": "Apple registry standing dossier",
        "question": "Does Apple Inc. maintain active corporate registry standing?",
        "sections": bad_sections}).json()
    submitted = jan.post(f"/api/commands/reports/{report['report_id']}/submit",
                         json={"expected_version": report["version"]}).json()
    rejected = reviewer.post(f"/api/commands/reports/{report['report_id']}/approve",
                             json={"expected_version": submitted["version"]})
    if rejected.status_code == 422:
        codes = {f["code"] for f in rejected.json()["findings"]}
        step.ok("validation rejects unsupported sentence", f"findings={sorted(codes)}")
    else:
        step.fail("validation rejects unsupported sentence",
                  f"expected 422, got {rejected.status_code}")
    good_sections = [dict(bad_sections[0])]
    good_sections[0]["sentences"] = bad_sections[0]["sentences"][:2] + [
        {"text": "Supplier-network exposure to a registry lapse remains unassessed.",
         "status": "UNRESOLVED",
         "unresolved_reason": "no supplier dependency evidence collected yet"}]
    edited = jan.post(f"/api/commands/reports/{report['report_id']}/edit", json={
        "expected_version": submitted["version"], "sections": good_sections,
        "change_note": "removed unsupported supplier claim; recorded the gap honestly"}).json()
    resubmitted = jan.post(f"/api/commands/reports/{report['report_id']}/submit",
                           json={"expected_version": edited["version"]}).json()
    approved = reviewer.post(f"/api/commands/reports/{report['report_id']}/approve",
                             json={"expected_version": resubmitted["version"]}).json()
    dispositions = jan.get(f"/api/reports/{report['report_id']}/dispositions").json()
    last = dispositions["dispositions"][-1]
    step.ok("dossier approved", f"status={approved['status']} v{approved['version']} "
            f"by {last['actor_id']} validation_sha={last['validation_sha256'][:12]}")
    roles_view = jan.get(f"/api/reports/{report['report_id']}/role/EXECUTIVE").json()
    step.ok("role projections", f"executive uncertainty mix={roles_view['uncertainty_note']}")

    for client in (jan, reviewer, b):
        client.close()
    return {"report_id": report["report_id"], "forecast_id": forecast["forecast_id"],
            "warning_id": projected["warning_id"], "fixture_id": fixture_id,
            "manifestation_id": manifestation_id}


def _serve(root: Path, actors: Path, port: int) -> tuple[uvicorn.Server, threading.Thread]:
    app = create_app(root, actors)
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port,
                                           log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    while not server.started:
        time.sleep(0.05)
    return server, thread


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True)
    args = parser.parse_args(argv)
    root = Path(args.root)
    actors = root / "actors.json"
    write_registry(actors, [JAN, REVIEWER, RESTRICTED_ANALYST])
    step = Step()

    port = _free_port()
    print(f"== operator exercise against http://127.0.0.1:{port} ==")
    server, thread = _serve(root, actors, port)
    context = run_exercise(root, f"http://127.0.0.1:{port}", step)

    # 21-22: restart the backend process, reopen the mission
    server.should_exit = True
    thread.join(timeout=10)
    port2 = _free_port()
    server2, thread2 = _serve(root, actors, port2)
    with httpx.Client(base_url=f"http://127.0.0.1:{port2}",
                      headers={"Authorization": f"Bearer {JAN['token']}"},
                      timeout=60) as jan:
        report = jan.get(f"/api/record/workbench_report/{context['report_id']}").json()
        forecast = jan.get(f"/api/record/analytic_forecast/{context['forecast_id']}").json()
        intact = report["current"]["status"] in ("APPROVED", "APPROVED_WITH_DISSENT") \
            and forecast["current"]["probability"] == 0.05
        (step.ok if intact else step.fail)(
            "restart", f"report={report['current']['status']} "
            f"forecast_p={forecast['current']['probability']}")
    server2.should_exit = True
    thread2.join(timeout=10)

    # 23: export/import/replay reconstructs the same authorized history
    import shutil
    from curunir_workbench.projections import MissionProjection
    from curunir_operational.access import AccessContext
    store = WorkbenchStore(root / "store")
    export_dir = root / "open_export"
    if export_dir.exists():
        shutil.rmtree(export_dir)
    store.export_to(export_dir)
    replay_root = root / "replayed"
    if replay_root.exists():
        shutil.rmtree(replay_root)
    WorkbenchStore.import_from(export_dir, replay_root / "store")
    shutil.copytree(root / "custody", replay_root / "custody")
    ctx_jan = AccessContext("wb-jan", "jan", "HUMAN", ("ANALYST", "SUPERVISOR"),
                            compartments=("SPECIAL",), releasability=("PUBLIC",))
    ctx_b = AccessContext("wb-b", "analyst-b", "HUMAN", ("ANALYST",),
                          releasability=("PUBLIC",))
    same = True
    for ctx in (ctx_jan, ctx_b):
        original = MissionProjection(store, ctx).overview()
        replayed = MissionProjection(WorkbenchStore(replay_root / "store"), ctx).overview()
        original["meta"].pop("context_id"); replayed["meta"].pop("context_id")
        if json.dumps(original, sort_keys=True, default=str) \
                != json.dumps(replayed, sort_keys=True, default=str):
            same = False
    (step.ok if same else step.fail)("export/import/replay",
                                     "authorized projections identical for both contexts")

    print("\n== RESULT ==")
    failures = [r for r in step.results if r[1].startswith("FAIL")]
    print(f"{len(step.results) - len(failures)}/{len(step.results)} steps OK")
    for name, result in failures:
        print(f"  FAILED: {name}: {result}")
    return 1 if failures else 0


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


if __name__ == "__main__":
    raise SystemExit(main())
