"""The V6.8 pilot protocol, driven through the shipped UI and nothing else.

Each journey is one mission of `CURUNIR_V6_8_PILOT_PROTOCOL.md` performed by
Chromium against a real instrumented server: session control, the mission
brief, the analytical work, the dossier, and the signed approval all happen in
the browser. No action here goes through urllib or a store function; the store
and the pilot log are read only to check what the browser caused.
"""
from __future__ import annotations

import json
import threading
import time
from pathlib import Path

import pytest

try:
    from playwright.sync_api import expect, sync_playwright
    HAVE_PLAYWRIGHT = True
except ImportError:
    HAVE_PLAYWRIGHT = False

from curunir_identity.replay import verify_all
from curunir_workbench.auth import ActorRegistry, write_registry
from curunir_workbench.projections import MissionProjection
from curunir_workbench.server import create_app
from curunir_workbench.store import WorkbenchStore
from curunir_workbench.views import review_queue

from tools import curunir_v68 as v68

from semantic_support import clock
from workbench_support import free_port, make_workbench, seed_mission

pytestmark = [pytest.mark.no_db,
              pytest.mark.skipif(not HAVE_PLAYWRIGHT, reason="playwright not installed")]

HUMAN_ACTORS = set(v68.ROLE_ACTORS.values())


# ---- mission roots and servers ----------------------------------------------

def _prepare_root(mission_id: str, root: Path) -> dict:
    """Build a deterministic mission root in process.

    This mirrors `prepare_mission` minus its clean-worktree requirement, which
    a test run cannot satisfy and which proves nothing about the UI.
    """
    root.mkdir(parents=True)
    v68._copy_mission_authority(root, mission_id)
    prepared = (v68._prepare_m2 if mission_id == "M2_REGULATORY_CORRECTION"
                else v68._prepare_m3)(root)
    actors_path = v68._write_actors(root)
    v68._write_json(root / "preparation.json", {
        "format": "curunir-v6.8-preparation-v1",
        "mission_id": mission_id,
        "status": "READY_FOR_GENUINE_HUMAN_PILOT",
        "prepared_at": v68._now(),
        "actors_path": str(actors_path),
        "human_actor_ids": [v68.PRIMARY_ACTOR, v68.APPROVER_ACTOR, v68.PUBLIC_ACTOR],
        "human_participation_recorded": False,
        "preparation": prepared,
    })
    return prepared


def _serve(app):
    import uvicorn
    port = free_port()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port,
                                           log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(200):
        if server.started:
            break
        time.sleep(0.05)
    return f"http://127.0.0.1:{port}", server, thread


def _token(root: Path, actor_id: str) -> str:
    actors = json.loads((root / "actors.json").read_text(encoding="utf-8"))["actors"]
    return next(item["token"] for item in actors if item["actor_id"] == actor_id)


def _projection(root: Path, actor_id: str) -> MissionProjection:
    """A fresh server-side view, for assertions only."""
    return MissionProjection(WorkbenchStore(root / "store"),
                             ActorRegistry(root / "actors.json").context_for_actor(actor_id))


# ---- browser helpers ---------------------------------------------------------

def _login(page, base, token, role="", note=""):
    """Sign in through the form; on a pilot server, with a role, the session
    starts before the first authenticated read."""
    page.goto(base)
    page.fill("#token", token)
    if role:
        expect(page.locator("#pilot-login")).to_be_visible()
        page.select_option("#pilot-role", role)
        page.fill("#pilot-note", note)
    page.click("#login-form button[type=submit]")
    page.wait_for_selector("#shell:not(.hidden)")
    if role:
        expect(page.locator("main h1")).to_contain_text("Pilot session")
        expect(page.locator("main")).to_contain_text("yours")


def _end_session(page, base, role, note):
    page.goto(f"{base}/#/pilot")
    expect(page.locator("main h1")).to_contain_text("Pilot session")
    page.locator("select[aria-label='participant role']").select_option(role)
    page.locator("input[aria-label='session note']").fill(note)
    page.get_by_role("button", name="End session").click()
    page.wait_for_selector("#login:not(.hidden)")


def _add_sentence(page, index, *, text, status, cite_id=None, unresolved_reason="",
                  inference_note="", temporal_scope="", extra_refs=()):
    """Author one report sentence in the editor, citing claims through the
    picker; other basis (a manifestation id read off its evidence page) goes
    into the raw refs field the way an operator would type it."""
    page.get_by_role("button", name="+ sentence").click()
    sentence = page.locator(".sentence").nth(index)
    sentence.locator("textarea").fill(text)
    sentence.locator("select").first.select_option(status)
    refs = sentence.locator("input[placeholder^='basis refs']")
    if cite_id:
        picker = sentence.locator(".picker", has=page.get_by_role("button", name="cite claim"))
        picker.locator("select").select_option(cite_id)
        picker.get_by_role("button", name="cite claim").click()
        expect(refs).to_have_value(cite_id)
    if extra_refs:
        refs.fill(",".join([*([cite_id] if cite_id else []), *extra_refs]))
    if temporal_scope:
        sentence.locator("select").nth(1).select_option(temporal_scope)
    if inference_note:
        sentence.locator("input[placeholder^='inference note']").fill(inference_note)
    if unresolved_reason:
        sentence.locator("input[placeholder^='unresolved reason']").fill(unresolved_reason)
    return sentence


def _pilot_log(root: Path) -> list[dict]:
    verification = v68.verify_pilot_log(root / v68.PILOT_LOG)
    assert verification["valid"], verification
    return v68._pilot_events(root / v68.PILOT_LOG)


def _protocol_reads(events, actor, category=None, path=None):
    return [item for item in events if item.get("event_kind") == "HTTP_ACTION"
            and item.get("actor_id") == actor and item.get("http_status") == 200
            and (category is None or item.get("category") == category)
            and (path is None or item.get("path") == path)]


def _assert_protocol_inspections(events, root, *, fixtures):
    """The reads the mission gates require, made inside the session by the
    primary operator: exact evidence, the source registry, provenance descent;
    for the fixture missions every fixture manifestation was opened."""
    primary = v68.PRIMARY_ACTOR
    assert _protocol_reads(events, primary, category="EVIDENCE_OPENED")
    assert _protocol_reads(events, primary, path="/api/family/fabric_source_descriptor")
    assert _protocol_reads(events, primary, category="PROVENANCE_REVIEWED") \
        or _protocol_reads(events, v68.APPROVER_ACTOR, category="PROVENANCE_REVIEWED")
    if fixtures:
        opened = {item["resource_id_sha256"] for item in _protocol_reads(events, primary, category="EVIDENCE_OPENED")}
        store = WorkbenchStore(root / "store")
        for item in store.records_of("fabric_manifestation"):
            if item.get("connector_id") == "v68-notional-fixture-v1":
                assert v68._sha256_bytes(item["manifestation_id"].encode()) in opened, item["manifestation_id"]


def _assert_ui_authored_mutations(events):
    """Every write a human made in these journeys came from the shipped UI."""
    mutations = [item for item in events
                 if item.get("event_kind") == "HTTP_ACTION"
                 and item.get("actor_id") in HUMAN_ACTORS
                 and item.get("method") != "GET"]
    assert mutations, "the journey performed no mutation at all"
    assert {item.get("client") for item in mutations} == {"workbench-ui"}, \
        sorted({(item.get("path"), item.get("client")) for item in mutations})
    outside = [item for item in v68._session_analysis(events)["findings"]
               if item["code"] == "HUMAN_HTTP_OUTSIDE_SESSION"]
    assert not outside, outside


@pytest.fixture(scope="module")
def browser():
    with sync_playwright() as p:
        instance = p.chromium.launch()
        yield instance
        instance.close()


# ---- M2: regulatory correction ----------------------------------------------

@pytest.fixture(scope="module")
def m2(tmp_path_factory):
    root = tmp_path_factory.mktemp("v68-m2") / "M2_REGULATORY_CORRECTION"
    prepared = _prepare_root("M2_REGULATORY_CORRECTION", root)
    base, server, thread = _serve(v68.create_instrumented_app(root))
    yield {"base": base, "root": root, "prepared": prepared}
    server.should_exit = True
    thread.join(timeout=5)


def test_m2_journey_through_the_ui(browser, m2):
    base, root, prepared = m2["base"], m2["root"], m2["prepared"]
    cleared = _projection(root, v68.PRIMARY_ACTOR)
    n9 = cleared.get("semantic_claim", prepared["current_facility_claim_id"])
    # The English v1 reading of N-4 survives only as history: the claim now
    # reads N-9, so the historical sentence cites the v1 manifestation itself.
    english_v1 = prepared["manifestations"][1]
    corrections = [item for item in review_queue(_projection(root, v68.PRIMARY_ACTOR))["items"]
                   if item["status"] == "OPEN" and item["kind"] == "SOURCE_CORRECTED"]
    assert corrections, "the M2 fixture should open SOURCE_CORRECTED review items"

    operator_ctx = browser.new_context()
    page = operator_ctx.new_page()
    _login(page, base, _token(root, v68.PRIMARY_ACTOR), "PRIMARY_OPERATOR", "begin frozen mission")
    expect(page.locator("#nav-links")).to_contain_text("Pilot session")

    # 1. the session opens before any work, and the brief is on the same page.
    expect(page.locator("main")).to_contain_text("Mission brief")
    expect(page.locator("main")).to_contain_text("M2_SOURCE_CORRECTED_REVIEWS")
    expect(page.locator("main")).to_contain_text("Bridge N-9")

    # 2. all four frozen manifestations, listed and each one opened, then the
    #    source registry and the interpreted change from N-4 to N-9.
    page.goto(f"{base}/#/evidence")
    expect(page.locator("main h1")).to_contain_text("Evidence")
    expect(page.locator("main tbody tr")).to_have_count(4)
    for index in range(4):
        page.goto(f"{base}/#/evidence")
        page.locator("main tbody tr").nth(index).locator("a").first.click()
        expect(page.locator("main h1")).to_contain_text("Evidence:")
    page.goto(f"{base}/#/sources")
    expect(page.locator("main h1")).to_contain_text("Sources")
    page.goto(f"{base}/#/timeline")
    expect(page.locator("main")).to_contain_text("SOURCE_CORRECTION")
    expect(page.locator("main")).to_contain_text("affected_facility")

    # 3. two genuinely competing hypotheses.
    page.goto(f"{base}/#/investigation")
    expect(page.locator("main h1")).to_contain_text("Investigation")
    for statement in (
            "The corrected notice v2 makes Bridge N-9 the current affected facility",
            "The English v1 reading of Bridge N-4 remains operative despite v2"):
        page.locator("input[placeholder='hypothesis statement…']").fill(statement)
        page.locator("input[placeholder='case id…']").fill("m2-facility")
        page.get_by_role("button", name="Record", exact=True).click()
        expect(page.locator("main")).to_contain_text(statement[:48])

    # 4. the human rework event.
    page.goto(f"{base}/#/pilot")
    page.locator("input[aria-label='operator correction note']").fill(
        "revised initial N-4 reading after exact v2 correction review")
    page.get_by_role("button", name="Record operator correction").click()
    expect(page.locator("main")).to_contain_text("operator correction recorded")

    # 5. every OPEN SOURCE_CORRECTED item is disposed of, with a note, inline.
    page.goto(f"{base}/#/review")
    expect(page.locator("main h1")).to_contain_text("Review queue")
    open_cards = page.locator(".card").filter(
        has=page.locator("input[aria-label='disposition note']"))
    expect(open_cards).to_have_count(len(corrections))
    for remaining in range(len(corrections), 0, -1):
        card = open_cards.first
        card.locator("input[aria-label='disposition note']").fill(
            "correction v2 inspected against exact evidence; current reading is N-9")
        card.get_by_role("button", name="Resolve", exact=True).click()
        expect(open_cards).to_have_count(remaining - 1)
    assert not [item for item in review_queue(_projection(root, v68.PRIMARY_ACTOR))["items"]
                if item["status"] == "OPEN" and item["kind"] == "SOURCE_CORRECTED"]

    # 6. reassess a hypothesis now that the correction is disposed of.
    page.goto(f"{base}/#/hypotheses")
    expect(page.locator("main h1")).to_contain_text("Hypotheses")
    page.locator("main tbody tr").first.locator("a").first.click()
    expect(page.locator("main h1")).to_contain_text("Hypothesis")
    page.locator("main select").first.select_option("SUPPORTED")
    page.locator("main textarea").first.fill(
        "correction v2 settles the affected facility as N-9")
    page.get_by_role("button", name="Record assessment").click()
    expect(page.locator("main")).to_contain_text(f"ANALYST_ASSESSED:{v68.PRIMARY_ACTOR}")

    # 7. the dossier: current N-9 is the cited statement itself, N-4 is
    #    carried as historical from English v1, an inference is marked as one,
    #    and the unknown stays unresolved.
    page.goto(f"{base}/#/reports")
    page.locator("input[placeholder='report title…']").fill("V6.8 M2 correction disposition")
    page.locator("input[placeholder='mission question…']").fill(
        "Which facility does the current corrected notice name?")
    page.get_by_role("button", name="Create draft").click()
    page.wait_for_url("**/#/reports/*")
    report_url = page.url
    page.get_by_role("button", name="Edit").click()
    expect(page.locator("main")).to_contain_text("Edit (a save is a new version")
    _add_sentence(page, 0, text=n9["statement"], status="SUPPORTED",
                  cite_id=n9["claim_id"], temporal_scope="CURRENT")
    _add_sentence(page, 1, text="The affected facility was Bridge N-4.", status="SUPPORTED",
                  temporal_scope="HISTORICAL", extra_refs=[english_v1])
    _add_sentence(page, 2, status="EXPLICITLY_INFERENTIAL",
                  text="The Croatian page restates English v1 and adds no independent basis for N-4.",
                  inference_note="dependent derivative of English v1; not corroboration after v2")
    _add_sentence(page, 3, status="UNRESOLVED",
                  text="Whether any source outside this bundle names another facility "
                       "is not established.",
                  unresolved_reason="nothing in the frozen bundle addresses sources outside it")
    page.get_by_role("button", name="Save as v2").click()
    expect(page.locator("main")).to_contain_text("validation: no blocking findings")
    page.get_by_role("button", name="Submit for review").click()
    expect(page.locator("main h1")).to_contain_text("IN_REVIEW")

    # 8. end the primary session and leave the browser profile behind.
    _end_session(page, base, "PRIMARY_OPERATOR", "mission work complete")
    page.wait_for_selector("#login:not(.hidden)")
    operator_ctx.close()

    # 9. the distinct approver, in a separate browser profile, signs the approval.
    approver_ctx = browser.new_context()
    approver = approver_ctx.new_page()
    _login(approver, base, _token(root, v68.APPROVER_ACTOR), "APPROVER", "independent review")
    approver.goto(report_url)
    expect(approver.locator("main h1")).to_contain_text("IN_REVIEW")
    approver.locator("input[aria-label='disposition note']").fill(
        "cited evidence inspected independently")
    approver.get_by_role("button", name="Approve (validated, human act)").click()
    expect(approver.locator("main h1")).to_contain_text("APPROVED")
    _end_session(approver, base, "APPROVER", "approval recorded")
    approver_ctx.close()

    # ---- what the harness can see afterwards ----
    events = _pilot_log(root)
    pairs = {(item["event_kind"], item.get("actor_id"), item.get("participant_role"))
             for item in events}
    for kind in ("SESSION_STARTED", "SESSION_ENDED"):
        assert (kind, v68.PRIMARY_ACTOR, "PRIMARY_OPERATOR") in pairs
        assert (kind, v68.APPROVER_ACTOR, "APPROVER") in pairs
    assert any(item["event_kind"] == "OPERATOR_CORRECTION_RECORDED" for item in events)
    _assert_ui_authored_mutations(events)
    _assert_protocol_inspections(events, root, fixtures=True)

    store = WorkbenchStore(root / "store")
    signed = [item for item in store.records_of("signed_action")
              if item["action_type"] == "approve_report"]
    assert len(signed) == 1, signed
    assert signed[0]["actor_id"] == v68.APPROVER_ACTOR
    replay = verify_all(store)
    assert replay["all_genuine"], replay


# ---- M3: relief collaboration and access control ----------------------------

@pytest.fixture(scope="module")
def m3(tmp_path_factory):
    root = tmp_path_factory.mktemp("v68-m3") / "M3_RELIEF_COLLABORATION"
    prepared = _prepare_root("M3_RELIEF_COLLABORATION", root)
    base, server, thread = _serve(v68.create_instrumented_app(root))
    yield {"base": base, "root": root, "prepared": prepared}
    server.should_exit = True
    thread.join(timeout=5)


def _restricted_ids(root: Path) -> list[str]:
    """The SPECIAL engineering records, read from the store, never from the UI."""
    cleared = _projection(root, v68.PRIMARY_ACTOR)
    public = _projection(root, v68.PUBLIC_ACTOR)
    public_ids = {item["manifestation_id"] for item in public.family("fabric_manifestation")}
    public_ids |= {item["claim_id"] for item in public.family("semantic_claim")}
    hidden = [item["manifestation_id"] for item in cleared.family("fabric_manifestation")
              if item["manifestation_id"] not in public_ids]
    hidden += [item["claim_id"] for item in cleared.family("semantic_claim")
               if item["claim_id"] not in public_ids
               and "engineering_status" in item["statement"]]
    assert hidden
    return hidden


def _assert_absent(content: str, identifier: str):
    """Neither the identifier nor a truncation of it may appear.

    The digest is what identifies the record; the family prefix ("claim-") is
    shared by everything and is not what leaks, so the prefixes checked here are
    prefixes of the digest.
    """
    digest = identifier.split("-")[-1]
    for form in (identifier, digest, digest[:8], digest[-6:]):
        assert form not in content, f"{form!r} (from {identifier}) leaked into the page"


def test_m3_journey_through_the_ui(browser, m3):
    base, root = m3["base"], m3["root"]
    cleared = _projection(root, v68.PRIMARY_ACTOR)
    # The SUPPORTED sentence cites the compartmented engineering claim, so the
    # report is created at the SPECIAL floor with the ANALYST role floor first.
    engineering = next(item for item in cleared.family("semantic_claim")
                       if "engineering_status" in item["statement"])
    hidden_ids = _restricted_ids(root)

    operator_ctx = browser.new_context()
    page = operator_ctx.new_page()
    _login(page, base, _token(root, v68.PRIMARY_ACTOR), "PRIMARY_OPERATOR", "begin relief mission")

    # 1. the operational picture: objects, alerts, recommendations.
    page.goto(f"{base}/#/overview")
    expect(page.locator("main h1")).to_contain_text("Common operating picture")
    expect(page.locator(".cop")).to_contain_text("Open alerts")
    expect(page.locator(".cop")).not_to_contain_text("no open alerts")
    page.goto(f"{base}/#/activity")
    expect(page.locator("main h1")).to_contain_text("Mission activity")
    expect(page.locator("main")).to_contain_text("OBJECT_VERSION_APPENDED")
    expect(page.locator("main")).to_contain_text("alert")
    expect(page.locator("main")).to_contain_text("recommendation")

    # 2. advance the assigned task.
    page.goto(f"{base}/#/tasks")
    expect(page.locator("main h1")).to_contain_text("Tasks")
    page.get_by_role("button", name="Start", exact=True).click()
    expect(page.locator("main")).to_contain_text("IN_PROGRESS")
    page.locator("input[aria-label='task note']").fill("public route evidence and the SPECIAL assessment reviewed")
    page.get_by_role("button", name="Complete").click()
    expect(page.locator("main")).to_contain_text("no open tasks")

    # 3. both evidence payloads, including the SPECIAL one, then an
    #    attributable annotation on the one left open.
    page.goto(f"{base}/#/evidence")
    expect(page.locator("main tbody tr")).to_have_count(2)
    payloads = []
    for index in (1, 0):
        page.goto(f"{base}/#/evidence")
        page.locator("main tbody tr").nth(index).locator("a").first.click()
        expect(page.locator("main h1")).to_contain_text("Evidence:")
        payloads.append(page.content())
    assert any("SUBSTATION TOVAN UNSTABLE" in item for item in payloads), \
        "the cleared operator should be able to read the SPECIAL engineering payload"
    page.goto(f"{base}/#/claims/{engineering['claim_id']}")
    expect(page.locator("main h1")).to_contain_text("Claim")
    page.goto(f"{base}/#/sources")
    expect(page.locator("main h1")).to_contain_text("Sources")
    page.goto(f"{base}/#/evidence")
    page.locator("main tbody tr").first.locator("a").first.click()
    expect(page.locator("main h1")).to_contain_text("Evidence:")
    annotate = page.locator(".card", has_text="Annotate").last
    annotate.locator("textarea").fill("route status reviewed against the captured bytes")
    annotate.get_by_role("button", name="Add").click()
    expect(page.locator("main")).to_contain_text("route status reviewed against the captured bytes")

    # 4. the dossier is created at the SPECIAL floor, before it cites anything.
    page.goto(f"{base}/#/reports")
    page.locator("input[placeholder='report title…']").fill("V6.8 M3 relief-routing disposition")
    page.locator("input[placeholder='mission question…']").fill(
        "What disposition is supported for RELIEF-101?")
    page.locator("select[aria-label='compartments']").select_option("SPECIAL")
    page.locator("select[aria-label='role floor']").select_option("ANALYST")
    page.get_by_role("button", name="Create draft").click()
    page.wait_for_url("**/#/reports/*")
    report_url = page.url
    page.get_by_role("button", name="Edit").click()
    _add_sentence(page, 0, text=engineering["statement"], status="SUPPORTED",
                  cite_id=engineering["claim_id"])
    _add_sentence(page, 1, status="UNRESOLVED",
                  text="Whether the substation can carry relief traffic after repair "
                       "is not established.",
                  unresolved_reason="no post-repair engineering evidence exists in this mission")
    page.get_by_role("button", name="Save as v2").click()
    expect(page.locator("main")).to_contain_text("validation: no blocking findings")
    page.get_by_role("button", name="Submit for review").click()
    expect(page.locator("main h1")).to_contain_text("IN_REVIEW")
    _end_session(page, base, "PRIMARY_OPERATOR", "primary work complete")
    page.wait_for_selector("#login:not(.hidden)")
    operator_ctx.close()

    # 5. the second cleared human dissents, then approves carrying that dissent.
    approver_ctx = browser.new_context()
    approver = approver_ctx.new_page()
    _login(approver, base, _token(root, v68.APPROVER_ACTOR), "APPROVER", "independent compartmented review")
    approver.goto(report_url)
    expect(approver.locator("main h1")).to_contain_text("IN_REVIEW")
    dissent_box = approver.locator(".card", has_text="Annotate").last
    dissent_box.locator("select[aria-label='annotation kind']").select_option("DISSENT")
    dissent_box.locator("textarea").fill(
        "the unresolved limitation understates the routing risk")
    dissent_box.get_by_role("button", name="Add").click()
    expect(approver.locator("main")).to_contain_text("open dissent annotation")
    approver.locator("input[aria-label='approve with open dissent']").check()
    approver.locator("input[aria-label='disposition note']").fill(
        "approved with the dissent carried visibly")
    approver.get_by_role("button", name="Approve (validated, human act)").click()
    expect(approver.locator("main h1")).to_contain_text("APPROVED_WITH_DISSENT")
    _end_session(approver, base, "APPROVER", "approval recorded")
    approver_ctx.close()

    # 6. the public access check: nothing compartmented is reachable or nameable.
    public_ctx = browser.new_context()
    observer = public_ctx.new_page()
    _login(observer, base, _token(root, v68.PUBLIC_ACTOR), "PUBLIC_ACCESS_CHECK", "public access check")
    hidden_ids = [*hidden_ids, report_url.rsplit("/", 1)[-1]]
    seen = []
    for route, heading in (("/overview", "Common operating picture"), ("/evidence", "Evidence"),
                           ("/graph", "Relationship graph"), ("/reports", "Reports")):
        observer.goto(f"{base}/#{route}")
        expect(observer.locator("main h1")).to_contain_text(heading)
        seen.append(observer.content())
    observer.fill("#quick-search", "SUBSTATION TOVAN UNSTABLE")
    observer.press("#quick-search", "Enter")
    expect(observer.locator("main")).to_contain_text("0 results")
    seen.append(observer.content())
    for content in seen:
        for identifier in hidden_ids:
            _assert_absent(content, identifier)

    # A mutation from the read-only actor is refused, visibly.
    observer.goto(f"{base}/#/evidence")
    observer.locator("main tbody tr").first.locator("a").first.click()
    expect(observer.locator("main h1")).to_contain_text("Evidence:")
    public_annotate = observer.locator(".card", has_text="Annotate").last
    public_annotate.locator("textarea").fill("an observer should not be able to write this")
    public_annotate.get_by_role("button", name="Add").click()
    expect(observer.locator("main")).to_contain_text("read-only")
    _end_session(observer, base, "PUBLIC_ACCESS_CHECK", "public access check complete")
    public_ctx.close()

    # ---- what the harness can see afterwards ----
    events = _pilot_log(root)
    pairs = {(item["event_kind"], item.get("actor_id"), item.get("participant_role"))
             for item in events}
    for kind in ("SESSION_STARTED", "SESSION_ENDED"):
        assert (kind, v68.PRIMARY_ACTOR, "PRIMARY_OPERATOR") in pairs
        assert (kind, v68.APPROVER_ACTOR, "APPROVER") in pairs
        assert (kind, v68.PUBLIC_ACTOR, "PUBLIC_ACCESS_CHECK") in pairs
    _assert_ui_authored_mutations(events)
    _assert_protocol_inspections(events, root, fixtures=False)
    assert any(item.get("event_kind") == "HTTP_ACTION"
               and item.get("actor_id") == v68.PUBLIC_ACTOR
               and item.get("http_status") == 403 for item in events)

    store = WorkbenchStore(root / "store")
    report_id = report_url.rsplit("/", 1)[-1]
    assert store.current_reports()[report_id]["status"] == "APPROVED_WITH_DISSENT"
    assert verify_all(store)["all_genuine"]



def test_measurement_counts_mutations_by_client(tmp_path):
    """The package summary counts mutations by the client each request claimed.

    Asserted on a hand-built log so the counts are exact; the journeys assert
    the same field on their own logs. A log written before the header existed
    carries no such key, so an already-frozen package still re-derives to its
    packaged bytes.
    """
    root = tmp_path / "measured"
    WorkbenchStore.create(root / "store", "measurement-test", "2026-08-21T12:00:00+00:00")
    v68._write_json(root / "preparation.json", {"mission_id": "M2_REGULATORY_CORRECTION"})
    log = root / v68.PILOT_LOG

    def http(seq_minute, method, path, client):
        v68._append_pilot_event(log, {
            "event_kind": "HTTP_ACTION", "event_time": f"2026-08-21T12:{seq_minute:02d}:00+00:00",
            "mission_id": "M2_REGULATORY_CORRECTION", "actor_id": v68.PRIMARY_ACTOR,
            "actor_kind": "HUMAN", "method": method, "path": path,
            "resource_id_sha256": "", "category": "OPERATOR_WRITE", "client": client,
            "http_status": 200, "duration_ms": 1.0, "store_seq_before": 1,
            "store_seq_after": 1, "store_head_before": "", "store_head_after": ""})

    def session(kind, minute):
        v68._append_pilot_event(log, {
            "event_kind": kind, "event_time": f"2026-08-21T12:{minute:02d}:00+00:00",
            "mission_id": "M2_REGULATORY_CORRECTION", "actor_id": v68.PRIMARY_ACTOR,
            "actor_kind": "HUMAN", "participant_role": "PRIMARY_OPERATOR", "note": ""})

    session("SESSION_STARTED", 0)
    http(1, "POST", "/api/commands/hypotheses", "workbench-ui")
    http(2, "POST", "/api/commands/reports", "workbench-ui")
    http(3, "POST", "/api/commands/annotate", "other")
    http(4, "GET", "/api/overview", "workbench-ui")
    session("SESSION_ENDED", 5)

    measurement = v68._measurement(
        root,
        {"lineage_complete_conclusions": 0, "lineage_required_conclusions": 0},
        {"status": "PASS"})
    assert measurement["measured_value"]["mutating_requests_by_client"] == {
        "other": 1, "workbench-ui": 2}

    # A log written before the header existed carries no client field, and
    # its measurement carries no such key: an already-frozen package still
    # re-derives to its packaged bytes.
    older = tmp_path / "older"
    WorkbenchStore.create(older / "store", "measurement-test", "2026-08-21T12:00:00+00:00")
    v68._write_json(older / "preparation.json", {"mission_id": "M2_REGULATORY_CORRECTION"})
    log = older / v68.PILOT_LOG
    session("SESSION_STARTED", 0)
    event = {"event_kind": "HTTP_ACTION", "event_time": "2026-08-21T12:01:00+00:00",
             "mission_id": "M2_REGULATORY_CORRECTION", "actor_id": v68.PRIMARY_ACTOR,
             "actor_kind": "HUMAN", "method": "POST", "path": "/api/commands/reports",
             "resource_id_sha256": "", "category": "REPORT_ACTION", "http_status": 200,
             "duration_ms": 1.0, "store_seq_before": 1, "store_seq_after": 1,
             "store_head_before": "", "store_head_after": ""}
    v68._append_pilot_event(log, event)
    session("SESSION_ENDED", 2)
    older_measurement = v68._measurement(
        older, {"lineage_complete_conclusions": 0, "lineage_required_conclusions": 0}, {"status": "PASS"})
    assert "mutating_requests_by_client" not in older_measurement["measured_value"]
    assert "mutating_requests_by_client" not in older_measurement["interpretation"]


# ---- M1 analogue: the plain workbench, no pilot routes ----------------------

@pytest.fixture(scope="module")
def demo(tmp_path_factory):
    tmp_path = tmp_path_factory.mktemp("v68-m1-analogue")
    pipeline, ctx = make_workbench(tmp_path)
    seeded = seed_mission(pipeline, ctx)
    actors = tmp_path / "actors.json"
    write_registry(actors, [
        {"token": "tok-primary", "actor_id": "analyst-a", "actor_kind": "HUMAN",
         "roles": ["ANALYST"], "compartments": ["SPECIAL"],
         "releasability": ["PUBLIC"], "organisation": "v68-analogue"},
        {"token": "tok-approver", "actor_id": "analyst-b", "actor_kind": "HUMAN",
         "roles": ["ANALYST"], "releasability": ["PUBLIC"],
         "organisation": "v68-analogue"},
    ])
    app = create_app(tmp_path, actors, now_fn=clock(start_minute=600))
    base, server, thread = _serve(app)
    yield {"base": base, "root": tmp_path, "seeded": seeded}
    server.should_exit = True
    thread.join(timeout=5)


def test_m1_analogue_authors_forecast_warning_and_dossier_in_the_ui(browser, demo):
    base, seeded = demo["base"], demo["seeded"]
    claim = seeded["status_claim"]
    objective_id = seeded["objective"]["objective_id"]

    author_ctx = browser.new_context()
    page = author_ctx.new_page()
    _login(page, base, "tok-primary")
    # No pilot routes here, so the pilot group must not appear at all.
    expect(page.locator("#nav-links")).not_to_contain_text("PILOT")
    expect(page.locator("#nav-links")).not_to_contain_text("Pilot session")

    # 1. author a falsifiable forecast, citing a claim chosen from the picker.
    page.goto(f"{base}/#/forecasts")
    expect(page.locator("main h1")).to_contain_text("Forecasts")
    card = page.locator(".card", has_text="Author forecast (human judgment)")
    card.locator("input[placeholder='falsifiable question…']").fill(
        "Will a later preserved GLEIF retrieval report a different registration status?")
    card.locator("input[placeholder^='exactly what occurrence']").fill(
        "TRUE only if a later hash-preserved GLEIF manifestation yields a different "
        "registration status for the same LEI.")
    card.get_by_label("horizon time").fill(
        "2026-09-20T23:59:59+00:00")
    card.locator("input[type=number]").first.fill("0.12")
    card.locator("input[placeholder^='why this authored probability']").fill(
        "Operator judgment from the preserved baseline; not an observed frequency.")
    card.locator("select[aria-label='proposition refs']").select_option(claim["claim_id"])
    card.locator("input[placeholder^='exact resolution criterion']").fill(
        "Resolve against the later preserved GLEIF status claim at or after the horizon.")
    card.locator("input[placeholder='domain…']").fill("CORPORATE_REGISTRY")
    card.get_by_role("button", name="Author forecast").click()
    page.wait_for_url("**/#/forecasts/*")
    expect(page.locator("main h1")).to_contain_text("Forecast")
    expect(page.locator("main")).to_contain_text("0.12")

    # 2. move the probability: an authored act with its own reason.
    move = page.locator(".card", has_text="Move probability")
    move.locator("input[type=number]").fill("0.2")
    move.locator("input[placeholder^='authored basis']").fill(
        "the preserved baseline is unchanged but coverage thinned")
    move.locator("input[placeholder^='why it moved']").fill("coverage reassessed")
    move.get_by_role("button", name="Record movement").click()
    expect(page.locator("main")).to_contain_text("0.20")

    # 3. project the warning onto the seeded objective, chosen from the select.
    project = page.locator(".card", has_text="Project warning onto an objective")
    project.locator("select").select_option(objective_id)
    project.get_by_role("button", name="Project").click()
    page.wait_for_url("**/#/warnings/*")
    expect(page.locator("main h1")).to_contain_text("Warning")
    expect(page.locator("main")).to_contain_text("Why this warning exists")

    # 4. a dossier whose supported sentence cites the claim through the picker.
    page.goto(f"{base}/#/reports")
    page.locator("input[placeholder='report title…']").fill("V6.8 M1 analogue dossier")
    page.locator("input[placeholder='mission question…']").fill("Is Acme still registered?")
    page.get_by_role("button", name="Create draft").click()
    page.wait_for_url("**/#/reports/*")
    report_url = page.url
    page.get_by_role("button", name="Edit").click()
    _add_sentence(page, 0, text=claim["statement"], status="SUPPORTED",
                  cite_id=claim["claim_id"])
    page.get_by_role("button", name="Save as v2").click()
    expect(page.locator("main")).to_contain_text("validation: no blocking findings")
    page.get_by_role("button", name="Submit for review").click()
    expect(page.locator("main h1")).to_contain_text("IN_REVIEW")
    author_ctx.close()

    # 5. a different analyst, in a separate profile, approves.
    approver_ctx = browser.new_context()
    approver = approver_ctx.new_page()
    _login(approver, base, "tok-approver")
    approver.goto(report_url)
    expect(approver.locator("main h1")).to_contain_text("IN_REVIEW")
    approver.locator("input[aria-label='disposition note']").fill("basis inspected")
    approver.get_by_role("button", name="Approve (validated, human act)").click()
    expect(approver.locator("main h1")).to_contain_text("APPROVED")
    approver_ctx.close()
