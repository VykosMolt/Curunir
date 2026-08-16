"""Browser E2E: real Chromium against a real workbench server over a real
seeded WorkbenchStore. Operator journeys: sign-in, COP, warning → evidence
descent, hypothesis assessment, annotation, dossier flow, access contexts."""
from __future__ import annotations

import socket
import threading

import pytest

try:
    from playwright.sync_api import expect, sync_playwright
    HAVE_PLAYWRIGHT = True
except ImportError:
    HAVE_PLAYWRIGHT = False

from curunir_workbench.auth import write_registry
from curunir_workbench.server import create_app

from semantic_support import clock
from workbench_support import make_workbench, seed_mission

pytestmark = [pytest.mark.no_db,
              pytest.mark.skipif(not HAVE_PLAYWRIGHT, reason="playwright not installed")]


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def mission_server(tmp_path_factory):
    import uvicorn
    tmp_path = tmp_path_factory.mktemp("wb-e2e")
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
    app = create_app(tmp_path, actors, now_fn=clock(start_minute=600))
    port = _free_port()
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    import time
    for _ in range(100):
        if server.started:
            break
        time.sleep(0.05)
    yield f"http://127.0.0.1:{port}", seeded
    server.should_exit = True
    thread.join(timeout=5)


@pytest.fixture(scope="module")
def browser_ctx():
    with sync_playwright() as p:
        browser = p.chromium.launch()
        yield browser
        browser.close()


def _login(page, base, token):
    page.goto(base)
    page.fill("#token", token)
    page.click("#login-form button[type=submit]")
    page.wait_for_selector("#shell:not(.hidden)")


def test_login_and_overview(browser_ctx, mission_server):
    base, seeded = mission_server
    page = browser_ctx.new_page()
    _login(page, base, "token-a")
    expect(page.locator("#actor-badge")).to_contain_text("analyst-a")
    expect(page.locator("main h1")).to_contain_text("Common operating picture")
    expect(page.locator(".cop")).to_contain_text("Active warnings")
    expect(page.locator(".cop")).to_contain_text("Preserve supply-chain visibility")
    page.close()


def test_warning_descends_to_source_evidence(browser_ctx, mission_server):
    base, seeded = mission_server
    page = browser_ctx.new_page()
    _login(page, base, "token-a")
    page.goto(f"{base}/#/warnings/{seeded['warning']['warning_id']}")
    expect(page.locator("main h1")).to_contain_text("Warning")
    expect(page.locator("main")).to_contain_text("Why this warning exists")
    expect(page.locator("main")).to_contain_text("Provenance descent")
    expect(page.locator("main")).to_contain_text("EXACT ANCHOR")
    expect(page.locator("main")).to_contain_text("source gleif")
    # pivot into the manifestation (evidence viewer)
    page.locator("main a", has_text="manifestation").first.click()
    expect(page.locator("main h1")).to_contain_text("Evidence:")
    expect(page.locator("main")).to_contain_text("content sha256")
    page.close()


def test_hypothesis_matrix_and_assessment(browser_ctx, mission_server):
    base, seeded = mission_server
    page = browser_ctx.new_page()
    _login(page, base, "token-a")
    page.goto(f"{base}/#/hypotheses")
    expect(page.locator("main")).to_contain_text("Evidence matrix")
    expect(page.locator(".matrix")).to_contain_text("+")
    page.goto(f"{base}/#/hypotheses/{seeded['hypothesis']['hypothesis_id']}")
    page.select_option("main select", "WEAKLY_SUPPORTED")
    page.fill("main textarea >> nth=0", "single-origin registry basis, via browser")
    page.get_by_role("button", name="Record assessment").click()
    expect(page.locator("main")).to_contain_text("WEAKLY_SUPPORTED")
    expect(page.locator("main")).to_contain_text("ANALYST_ASSESSED:analyst-a")
    page.close()


def test_annotation_from_browser(browser_ctx, mission_server):
    base, seeded = mission_server
    page = browser_ctx.new_page()
    _login(page, base, "token-a")
    page.goto(f"{base}/#/forecasts/{seeded['forecast']['forecast_id']}")
    expect(page.locator("main")).to_contain_text("Authored probability history")
    annotate = page.locator(".card", has_text="Annotate").last
    annotate.locator("textarea").fill("browser annotation on the forecast")
    annotate.get_by_role("button", name="Add").click()
    expect(page.locator("main")).to_contain_text("browser annotation on the forecast")
    expect(page.locator("main")).to_contain_text("analyst-a")
    page.close()


def test_dossier_flow_in_browser(browser_ctx, mission_server):
    base, seeded = mission_server
    page = browser_ctx.new_page()
    _login(page, base, "token-a")
    page.goto(f"{base}/#/reports")
    page.fill("input[placeholder='report title…']", "Browser dossier")
    page.fill("input[placeholder='mission question…']", "Is Acme viable?")
    page.get_by_role("button", name="Create draft").click()
    page.wait_for_url("**/#/reports/*")
    expect(page.locator("main h1")).to_contain_text("Browser dossier")
    # edit: add a supported sentence bound to the real claim
    page.get_by_role("button", name="Edit").click()
    page.get_by_role("button", name="+ sentence").click()
    sentence = page.locator(".sentence").first
    sentence.locator("textarea").fill("Acme Industri AS holds an ISSUED GLEIF registration.")
    sentence.locator("input[placeholder^='basis refs']").fill(
        seeded["status_claim"]["claim_id"])
    page.get_by_role("button", name="Save as v2").click()
    expect(page.locator("main")).to_contain_text("validation: no blocking findings")
    page.get_by_role("button", name="Submit for review").click()
    expect(page.locator("main h1")).to_contain_text("IN_REVIEW")
    report_url = page.url
    page.close()
    # separation of duties: a second analyst performs the approval
    reviewer = browser_ctx.new_page()
    _login(reviewer, base, "token-b")
    reviewer.goto(report_url)
    reviewer.get_by_role("button", name="Approve (validated, human act)").click()
    expect(reviewer.locator("main h1")).to_contain_text("APPROVED")
    expect(reviewer.locator("main")).to_contain_text("Dispositions")
    reviewer.close()


def test_restricted_analyst_sees_filtered_mission(browser_ctx, mission_server):
    base, seeded = mission_server
    page = browser_ctx.new_page()
    _login(page, base, "token-b")
    expect(page.locator("#actor-badge")).to_contain_text("analyst-b")
    # search for the compartmented object yields nothing
    page.fill("#quick-search", "Sensitive Partner")
    page.press("#quick-search", "Enter")
    expect(page.locator("main")).to_contain_text("0 results")
    # entity list lacks the hidden object
    page.goto(f"{base}/#/entities")
    expect(page.locator("main")).not_to_contain_text("Sensitive Partner")
    content = page.content()
    assert seeded["secret_object_id"] not in content
    page.close()


def test_timeline_axes_in_browser(browser_ctx, mission_server):
    base, _ = mission_server
    page = browser_ctx.new_page()
    _login(page, base, "token-a")
    page.goto(f"{base}/#/timeline")
    expect(page.locator("main")).to_contain_text("KNOWLEDGE TIME")
    page.get_by_role("button", name="VALID TIME (world)").click()
    expect(page.locator("main")).to_contain_text("what was happening in the world")
    page.close()
