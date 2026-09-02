"""No operational surface names a record the viewer cannot see.

Two scenes: the Vessia scenario, and a small scene in which the rules read a
compartmented observation alongside open ones. For every access context the
test computes the record ids that context can never see and scans every
rendered surface (projection, changes, sitrep in all three forms, explain for
every visible record, the PACE bundle, the COP view and HTML, and the
proposals) for any of them. Markings are joined structurally at append time and
derived from inputs in the rule provider; this is the end-to-end check that
both hold. The structural join at append time is the load-bearing control:
the compartmented scene carries an analyst action that names the restricted
observation under an open marking, which only admission keeps hidden. The
provider-derived marking is a second, independent floor; on its own it is
never the last line, because every proposal also inherits through its
inference record and the inference through its input refs.
"""
from __future__ import annotations

import json
import re

import pytest

from curunir_operational.access import can_view
from curunir_operational.analytics import DeterministicRuleProvider, MockAssessmentProvider
from curunir_operational.explain import explain, explain_markdown
from curunir_operational.projection import Projection
from curunir_operational.scenario.config import CONTEXTS, STALENESS_HOURS, WORKSHOP, at
from curunir_operational.scenario.runner import OPERATIONAL_CONTEXT, run_scenario
from curunir_operational.security import PRIMARY_ID_FIELDS
from curunir_operational.sitrep import build_situation_report, render_markdown, render_text
from curunir_operational.sovereignty import build_pace_bundle
from curunir_operational.store import MissionDataStore
from curunir_operational.workbench import WorkbenchRenderer, render_cop_html, validate_workshop_definition
from curunir_operational.workflow import WorkflowEngine

from operational_support import BASE_MARKING, HIGH_CONTEXT, LOW_CONTEXT, SERVICE_CONTEXT, build_scene, t

pytestmark = pytest.mark.no_db

MATERIALIZED = ("ALERT_CANDIDATE", "RELATIONSHIP_CANDIDATE", "STATE_CANDIDATE", "RECOMMENDATION_CANDIDATE")


@pytest.fixture(scope="module")
def vessia(tmp_path_factory):
    base = tmp_path_factory.mktemp("vessia-surfaces")
    run_scenario(base / "store", base / "out")
    store = MissionDataStore(base / "store")
    projection = Projection(store, snapshot_time=at(33.0), staleness_hours=STALENESS_HOURS)
    return base, store, projection, CONTEXTS


@pytest.fixture(scope="module")
def compartmented(tmp_path_factory):
    """Rules run with full access over a scene whose first observation is compartmented."""
    base = tmp_path_factory.mktemp("compartmented-surfaces")
    store, projection = build_scene(base, restrict_first_observation=True)
    workflow = WorkflowEngine(store)
    for proposal in DeterministicRuleProvider(store).run(projection, HIGH_CONTEXT, recorded_time=t(27.0)):
        if proposal["proposal_type"] in MATERIALIZED:
            workflow.materialize(proposal, actor_id="workflow-policy", recorded_time=t(27.1))
    MockAssessmentProvider(store).run(projection, HIGH_CONTEXT, "infra-BR-7", recorded_time=t(27.2))
    # An open-marked note about the restricted observation: only admission can keep it hidden.
    workflow.analyst_action(context=HIGH_CONTEXT, kind="ANNOTATE", subject_kind="object", subject_id="obs-sensor-1",
                            note="sensor reads nominal; treat as suspect", recorded_time=t(27.3), marking=BASE_MARKING)
    projection = Projection(store, snapshot_time=t(28.0), staleness_hours={"RESOURCE_STOCK": 24.0})
    contexts = {"high": HIGH_CONTEXT, "low": LOW_CONTEXT, "service": SERVICE_CONTEXT}
    return base, store, projection, contexts


def _visibility(store, context):
    """Ids the context can see through some record, and ids it can never see."""
    visible, hidden = set(), set()
    for event in store.events():
        record = event["record"]
        field = PRIMARY_ID_FIELDS.get(record["record_type"])
        record_id = record.get(field) if field else None
        if not record_id or "marking" not in record:
            continue
        (visible if can_view(record["marking"], context) else hidden).add(record_id)
    return visible, hidden - visible


def _surfaces(base, store, projection, name, context, visible, hidden):
    yield "projection", json.dumps(projection.view(context))
    yield "changes", json.dumps(projection.changes_since(0, context, store))
    report = build_situation_report(store, projection, context, operational_context=OPERATIONAL_CONTEXT)
    yield "sitrep", json.dumps(report)
    yield "sitrep.md", render_markdown(report)
    yield "sitrep.txt", render_text(report)
    for record_id in sorted(visible):
        explanation = explain(projection, record_id, context)
        yield f"explain:{record_id}", json.dumps(explanation) + explain_markdown(explanation)
    for record_id in sorted(hidden):
        # Asking about a hidden record echoes the asked-for id and nothing else.
        explanation = {k: v for k, v in explain(projection, record_id, context).items() if k != "record_id"}
        yield f"explain-hidden:{record_id}", json.dumps(explanation)
    bundle = base / f"pace-{name}"
    build_pace_bundle(store, projection, context, bundle, operational_context=OPERATIONAL_CONTEXT)
    for path in sorted(bundle.rglob("*")):
        if path.is_file():
            yield f"pace:{path.name}", path.read_text(encoding="utf-8", errors="replace")
    view = WorkbenchRenderer(projection).render(validate_workshop_definition(WORKSHOP), context)
    yield "cop", json.dumps(view)
    yield "cop.html", render_cop_html(view, title="COP")
    for proposal in store.records_of("analytical_proposal"):
        if can_view(proposal["marking"], context):
            yield f"proposal:{proposal['proposal_id']}", json.dumps(proposal["content"])


def _pattern(ids):
    # An id counts wherever letters or digits do not run straight into it.
    return re.compile(r"(?<![A-Za-z0-9])(?:%s)(?![A-Za-z0-9])" % "|".join(map(re.escape, sorted(ids, key=len, reverse=True))))


@pytest.mark.parametrize("scene", ("vessia", "compartmented"))
def test_no_surface_names_a_record_the_low_context_cannot_see(request, scene):
    base, store, projection, contexts = request.getfixturevalue(scene)
    context = contexts["low"]
    visible, hidden = _visibility(store, context)
    assert hidden, "the low context must have something kept from it"
    pattern = _pattern(hidden)
    leaks = {surface: sorted(set(pattern.findall(text)))
             for surface, text in _surfaces(base, store, projection, "low", context, visible, hidden)
             if pattern.search(text)}
    assert not leaks, leaks


@pytest.mark.parametrize("scene", ("vessia", "compartmented"))
def test_cleared_contexts_have_nothing_hidden_and_render_every_surface(request, scene):
    base, store, projection, contexts = request.getfixturevalue(scene)
    for name, context in contexts.items():
        if name == "low":
            continue
        visible, hidden = _visibility(store, context)
        assert not hidden, (name, sorted(hidden))
        assert visible
        for _ in _surfaces(base, store, projection, name, context, visible, hidden):
            pass
