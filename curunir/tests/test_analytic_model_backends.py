"""The model-proposal seam, end to end, with no network.

The response schema comes from the same table the review path enforces, a
provider may only cite identifiers it was shown, its failures are kept as
evidence, and nothing it emits becomes state until a person accepts it."""
from __future__ import annotations

import json

import pytest

from curunir_analytic.candidate_schema import (NOT_MODEL_PROPOSABLE, available_ids,
                                               candidate_schema, id_fields,
                                               proposable_kinds, refusal_reason,
                                               unresolvable_ids)
from curunir_analytic.model_backends import (BackendUnavailable, DeterministicBackend,
                                             ModelBackend, ProviderSpec,
                                             availability, build_backend)
from curunir_analytic.providers import AnalyticalAssist
from curunir_analytic.substrate import CANDIDATE_BINDING_KEYS, resolve_candidate

from analytic_support import make_analytic, seed_acme

pytestmark = pytest.mark.no_db


# The schema follows the binding table

def test_every_proposable_kind_has_a_schema_over_exactly_its_binding_keys():
    for kind in proposable_kinds():
        schema = candidate_schema(kind)
        assert tuple(schema["required"]) == CANDIDATE_BINDING_KEYS[kind], kind
        assert set(schema["properties"]) == set(CANDIDATE_BINDING_KEYS[kind]), kind
        assert schema["additionalProperties"] is False, kind


def test_a_new_binding_key_reaches_the_schema_without_editing_it():
    """A new binding key reaches the schema with no edit to the schema."""
    from curunir_analytic import candidate_schema as CS
    original = dict(CS.CANDIDATE_BINDING_KEYS)
    try:
        CS.CANDIDATE_BINDING_KEYS["analytic_theme"] = ("title", "supporting_claim_ids",
                                                       "newly_required_field")
        assert "newly_required_field" in candidate_schema("analytic_theme")["required"]
    finally:
        CS.CANDIDATE_BINDING_KEYS.clear()
        CS.CANDIDATE_BINDING_KEYS.update(original)


def test_closed_vocabularies_become_enums_from_the_contract():
    from curunir_analytic.contracts import INFLUENCE_KINDS, VARIANT_RELATIONS
    assert candidate_schema("narrative_variant")["properties"]["relation"]["enum"] \
        == list(VARIANT_RELATIONS)
    assert candidate_schema("influence_assertion")["properties"]["kind"]["enum"] \
        == list(INFLUENCE_KINDS)


def test_a_machine_computed_binding_is_refused_not_invited():
    assert "impact_path" in NOT_MODEL_PROPOSABLE
    assert "impact_path" not in proposable_kinds()
    assert refusal_reason("impact_path")
    with pytest.raises(ValueError, match="edge_chain"):
        candidate_schema("impact_path")
    with pytest.raises(ValueError, match="not model-proposable"):
        DeterministicBackend().infer("t", {}, "impact_path")


# A provider may only cite what it was shown

def test_available_ids_finds_identifiers_nested_anywhere():
    shown = available_ids({"a": {"b": [{"claim_id": "claim-1"},
                                       {"deep": {"object_id": "obj-2"}}]}})
    assert {"claim-1", "obj-2"} <= shown


def test_unresolvable_ids_flags_a_fabricated_citation():
    assert unresolvable_ids("analytic_theme",
                            {"title": "t", "supporting_claim_ids": ["claim-real"]},
                            frozenset({"claim-real"})) == ()
    bad = unresolvable_ids("analytic_theme",
                           {"title": "t", "supporting_claim_ids": ["claim-invented"]},
                           frozenset({"claim-real"}))
    assert bad == ("supporting_claim_ids=claim-invented",)


def test_id_fields_covers_the_non_obvious_stakeholder_claims_field():
    assert "claims" in id_fields("stakeholder_assessment")
    assert "entity_object_id" in id_fields("stakeholder_assessment")
    assert "role_in_context" not in id_fields("stakeholder_assessment")


class _Fabricating(ModelBackend):
    """A provider that cites an identifier it was never given."""
    def __init__(self):
        super().__init__(ProviderSpec(provider="deterministic"))

    def _invoke(self, system, payload, schema):
        return json.dumps({"title": "Plausible but unsupported",
                           "supporting_claim_ids": ["claim-does-not-exist"]})


def test_a_fabricated_identifier_is_refused_at_the_backend():
    with pytest.raises(ValueError, match="identifiers it was not shown"):
        _Fabricating().infer("t", {"claims": [{"claim_id": "claim-real"}]},
                             "analytic_theme")


class _NotJson(ModelBackend):
    def __init__(self):
        super().__init__(ProviderSpec(provider="deterministic"))

    def _invoke(self, system, payload, schema):
        return "I'm afraid I can't do that."


def test_a_non_json_response_is_an_error_not_content():
    with pytest.raises(ValueError, match="not JSON"):
        _NotJson().infer("t", {}, "analytic_theme")


# Availability degrades honestly

def test_missing_sdks_are_reported_not_faked():
    report = availability()["providers"]
    assert report["deterministic"]["status"] == "AVAILABLE"
    for vendor in ("anthropic", "openai"):
        assert report[vendor]["status"] in ("UNAVAILABLE", "CLIENT_CONSTRUCTED")
        if report[vendor]["status"] == "UNAVAILABLE":
            assert "not installed" in report[vendor]["detail"] \
                or "unavailable" in report[vendor]["detail"]


def test_an_unknown_provider_is_refused_at_construction():
    with pytest.raises(ValueError, match="unknown provider"):
        ProviderSpec(provider="something-else")


def test_a_spec_defaults_to_the_current_model_per_vendor():
    assert ProviderSpec(provider="anthropic").model == "claude-opus-5"
    assert ProviderSpec(provider="openai").model


def test_an_uninstalled_sdk_raises_backend_unavailable_not_import_error():
    pytest.importorskip
    try:
        import anthropic  # noqa: F401
    except ImportError:
        with pytest.raises(BackendUnavailable, match="not installed"):
            build_backend(ProviderSpec(provider="anthropic"))


# The whole seam, over real state

def _seed(tmp_path):
    pipeline, ctx = make_analytic(tmp_path)
    return ctx, seed_acme(pipeline, ctx)["entity_status"]


def test_a_backend_proposal_becomes_a_candidate_only_a_human_can_accept(tmp_path):
    ctx, claim = _seed(tmp_path)
    backend = DeterministicBackend()
    assist = AnalyticalAssist(package=backend.package(), backend=backend)
    assert assist.available()
    assert assist.status()["status"] == "AVAILABLE"

    result = assist.propose(ctx, task="label the registry cluster",
                            target_kind="analytic_theme",
                            inputs={"claims": [claim]}, input_refs=(claim,))
    assert result["status"] == "PROPOSED"

    inference = next(r for r in ctx.store.records_of("inference")
                     if r["inference_id"] == result["inference_id"])
    assert inference["validation"] == "VALID"
    assert inference["output"]["supporting_claim_ids"] == [claim]
    assert inference["model_id"] == backend.package().model_id

    # A proposal is not analytical state yet.
    proposal_id = result["proposal"]["proposal_id"]
    assert proposal_id not in ctx.store.current_themes()

    with pytest.raises(ValueError):
        resolve_candidate(ctx, proposal_id, accept=True,
                          actor_id="svc", actor_kind="SERVICE")
    resolved = resolve_candidate(ctx, proposal_id, accept=True,
                                 actor_id="jan", actor_kind="HUMAN")
    assert resolved["content"]["supporting_claim_ids"] == [claim]


def test_a_backend_failure_is_retained_as_an_invalid_inference(tmp_path):
    ctx, claim = _seed(tmp_path)

    class _Failing(ModelBackend):
        def __init__(self):
            super().__init__(ProviderSpec(provider="deterministic"))

        def _invoke(self, system, payload, schema):
            raise RuntimeError("provider exploded")

    backend = _Failing()
    assist = AnalyticalAssist(package=backend.package(), backend=backend)
    result = assist.propose(ctx, task="t", target_kind="analytic_theme",
                            inputs={"claims": [claim]}, input_refs=(claim,))
    assert result["status"] == "PROVIDER_ERROR"
    inference = next(r for r in ctx.store.records_of("inference")
                     if r["inference_id"] == result["inference_id"])
    assert inference["validation"] == "INVALID"
    assert inference["output"] == {}
    assert any("provider exploded" in e for e in inference["errors"])
    assert not [p for p in ctx.store.records_of("analytical_proposal")]


def test_the_backend_receives_the_target_kind_the_legacy_callable_cannot(tmp_path):
    """The backend is told which kind it is proposing, so the schema can bind it."""
    ctx, claim = _seed(tmp_path)
    seen = {}

    class _Recording(DeterministicBackend):
        def infer(self, task, inputs, target_kind):
            seen["kind"] = target_kind
            return super().infer(task, inputs, target_kind)

    backend = _Recording()
    assist = AnalyticalAssist(package=backend.package(), backend=backend)
    assist.propose(ctx, task="t", target_kind="analytic_narrative",
                   inputs={"claims": [claim]}, input_refs=(claim,))
    assert seen["kind"] == "analytic_narrative"


def test_the_legacy_infer_fn_path_is_unchanged(tmp_path):
    ctx, claim = _seed(tmp_path)
    from curunir_analytic.providers import analytical_assist_package
    assist = AnalyticalAssist(
        package=analytical_assist_package("legacy", "stub", "1.0"),
        infer_fn=lambda task, payload: {"title": "legacy",
                                        "supporting_claim_ids": list(payload["claims"])})
    result = assist.propose(ctx, task="t", target_kind="analytic_theme",
                            inputs={"claims": [claim]}, input_refs=(claim,))
    assert result["status"] == "PROPOSED"


def test_egress_refusal_still_precedes_any_provider_call(tmp_path):
    """The marking gate runs before the provider is called at all."""
    ctx, claim = _seed(tmp_path)

    class _MustNotRun(ModelBackend):
        def __init__(self):
            super().__init__(ProviderSpec(provider="deterministic"))

        def _invoke(self, system, payload, schema):
            raise AssertionError("the provider was called despite egress refusal")

    backend = _MustNotRun()
    assist = AnalyticalAssist(package=backend.package(), backend=backend)
    result = assist.propose(ctx, task="t", target_kind="analytic_theme",
                            inputs={"claims": ["claim-not-a-record"]},
                            input_refs=("claim-not-a-record",))
    assert result["status"] == "EGRESS_REFUSED"


# The command layer cannot read around the lattice

def _command_context(ctx, root, context):
    from curunir_workbench.commands import CommandContext
    from semantic_support import MARK
    return CommandContext(store=ctx.store, root=root, context=context,
                          marking=MARK, now_fn=ctx.now_fn)


def test_a_proposal_request_cannot_show_a_provider_what_the_actor_cannot_see(
        tmp_path, monkeypatch):
    """A record the requester cannot view is unknown to them and never sent out."""
    from curunir_workbench import commands
    from curunir_workbench.errors import NotFound
    from workbench_support import CTX_A, CTX_B, make_workbench, seed_mission

    monkeypatch.setenv("CURUNIR_MODEL_PROVIDER", "deterministic")
    pipeline, ctx = make_workbench(tmp_path)
    seeded = seed_mission(pipeline, ctx)
    secret = seeded["secret_assumption_id"]

    seen: list = []

    class _Recording(DeterministicBackend):
        def infer(self, task, inputs, target_kind):
            seen.append(inputs)
            return super().infer(task, inputs, target_kind)

    backend = _Recording()
    monkeypatch.setattr(
        commands, "assist_from_environment",
        lambda: AnalyticalAssist(package=backend.package(), backend=backend))

    # CTX_B is not in the compartment, so the record is simply unknown to it.
    with pytest.raises(NotFound):
        commands.request_model_proposal(
            _command_context(ctx, tmp_path, CTX_B),
            task="summarise", target_kind="analytic_theme", input_refs=(secret,))
    assert seen == [], "the provider was shown a record the requester cannot view"

    # CTX_A does hold it, so the same request resolves.
    result = commands.request_model_proposal(
        _command_context(ctx, tmp_path, CTX_A),
        task="summarise", target_kind="analytic_theme", input_refs=(secret,))
    assert result["status"] in ("PROPOSED", "EGRESS_REFUSED")
    if result["status"] == "PROPOSED":
        assert seen, "the authorized request never reached the provider"


def test_a_restricted_input_is_refused_egress_under_the_default_ceiling(
        tmp_path, monkeypatch):
    """The default ceiling is public-only: hidden evidence never leaves, even
    when a cleared analyst asks."""
    from curunir_workbench import commands
    from workbench_support import CTX_A, make_workbench, seed_mission

    pipeline, ctx = make_workbench(tmp_path)
    seeded = seed_mission(pipeline, ctx)

    class _MustNotRun(DeterministicBackend):
        def infer(self, task, inputs, target_kind):
            raise AssertionError("compartmented evidence reached the provider")

    backend = _MustNotRun()
    monkeypatch.setattr(
        commands, "assist_from_environment",
        lambda: AnalyticalAssist(package=backend.package(), backend=backend))
    result = commands.request_model_proposal(
        _command_context(ctx, tmp_path, CTX_A), task="summarise",
        target_kind="analytic_theme",
        input_refs=(seeded["secret_assumption_id"],))
    assert result["status"] == "EGRESS_REFUSED"


def test_no_configured_provider_is_an_honest_status_not_an_error(tmp_path,
                                                                 monkeypatch):
    from curunir_workbench import commands
    from workbench_support import CTX_A, make_workbench, seed_mission

    pipeline, ctx = make_workbench(tmp_path)
    seeded = seed_mission(pipeline, ctx)
    monkeypatch.setattr(commands, "assist_from_environment", lambda: None)
    result = commands.request_model_proposal(
        _command_context(ctx, tmp_path, CTX_A), task="t",
        target_kind="analytic_theme",
        input_refs=(seeded["status_claim"]["claim_id"],))
    assert result["status"] == "ANALYTICAL_PROVIDER_UNAVAILABLE"


def test_a_request_must_cite_evidence(tmp_path, monkeypatch):
    from curunir_workbench import commands
    from workbench_support import CTX_A, make_workbench

    pipeline, ctx = make_workbench(tmp_path)
    monkeypatch.setattr(
        commands, "assist_from_environment",
        lambda: AnalyticalAssist(package=DeterministicBackend().package(),
                                 backend=DeterministicBackend()))
    with pytest.raises(ValueError, match="cite the evidence"):
        commands.request_model_proposal(
            _command_context(ctx, tmp_path, CTX_A), task="t",
            target_kind="analytic_theme", input_refs=())


# The HTTP boundary

def _http(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from curunir_workbench.auth import write_registry
    from curunir_workbench.server import create_app
    from workbench_support import make_workbench, seed_mission

    pipeline, ctx = make_workbench(tmp_path)
    seeded = seed_mission(pipeline, ctx)
    actors = tmp_path / "actors.json"
    write_registry(actors, [
        {"token": "tok-a", "actor_id": "analyst-a", "actor_kind": "HUMAN",
         "roles": ["ANALYST"], "compartments": [], "releasability": ["PUBLIC"],
         "organisation": "semantic-test", "enabled": True}])
    monkeypatch.setenv("CURUNIR_MODEL_PROVIDER", "deterministic")
    client = TestClient(create_app(tmp_path, actors, now_fn=ctx.now_fn))
    return client, seeded, {"Authorization": "Bearer tok-a"}


def test_model_status_endpoint_reports_the_environment(tmp_path, monkeypatch):
    client, _, auth = _http(tmp_path, monkeypatch)
    assert client.get("/api/model/status").status_code == 401
    body = client.get("/api/model/status", headers=auth).json()
    assert body["configured"]["providers"]["deterministic"]["status"] == "AVAILABLE"


def test_proposal_endpoint_returns_a_candidate_needing_a_human(tmp_path, monkeypatch):
    client, seeded, auth = _http(tmp_path, monkeypatch)
    claim = seeded["status_claim"]["claim_id"]
    response = client.post("/api/commands/proposals", headers=auth, json={
        "task": "label the registry cluster", "target_kind": "analytic_theme",
        "input_refs": [claim]})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "PROPOSED"
    assert body["proposal"]["status"] == "PROPOSED"


def test_proposal_endpoint_refuses_a_machine_computed_kind(tmp_path, monkeypatch):
    client, seeded, auth = _http(tmp_path, monkeypatch)
    response = client.post("/api/commands/proposals", headers=auth, json={
        "task": "t", "target_kind": "impact_path",
        "input_refs": [seeded["status_claim"]["claim_id"]]})
    assert response.status_code == 400
    assert "edge_chain" in response.json()["detail"]


def test_proposal_endpoint_reports_an_unseeable_ref_as_unknown(tmp_path, monkeypatch):
    client, seeded, auth = _http(tmp_path, monkeypatch)
    response = client.post("/api/commands/proposals", headers=auth, json={
        "task": "t", "target_kind": "analytic_theme",
        "input_refs": [seeded["secret_assumption_id"]]})
    assert response.status_code == 404


# Every proposable kind works offline, end to end

def _kind_inputs(ctx, seeded):
    """Real ids for every binding field, taken from the seeded mission."""
    from curunir_analytic.analogues import record_episode
    from curunir_analytic.narratives import create_narrative
    from curunir_analytic.themes import create_theme
    store = ctx.store
    claims = [c["claim_id"] for c in seeded["claims"].values()]
    objects = [r["object_id"] for r in store.records_of("object_version")
               if not r["marking"].get("compartments")]
    manifestations = [r["manifestation_id"] for r in store.records_of("fabric_manifestation")]
    theme = create_theme(ctx, title="registry standing", supporting_claim_ids=claims[:1])
    narrative = create_narrative(ctx, statement="Acme is a going concern", supporting_claim_ids=claims[:1])
    episode = record_episode(ctx, title="the 2019 registry lapse", summary="a lapse that was cured",
                             actor_object_ids=tuple(objects[:1]), event_ids=(), institutional_setting="registry",
                             mechanism="lapsed renewal", claim_ids=tuple(claims[:1]))
    return {
        "analytic_theme": {"supporting_claim_ids": claims},
        "analytic_narrative": {"supporting_claim_ids": claims},
        "narrative_variant": {"claim_ids": claims[:1]},
        "propagation_edge": {"narrative_id": narrative["narrative_id"],
                             "from_manifestation_id": manifestations[0], "to_manifestation_id": manifestations[1]},
        "stakeholder_assessment": {"entity_object_id": objects[0], "context_id": "mission", "claims": claims[:1]},
        "influence_assertion": {"source_object_id": objects[0], "target_object_id": objects[1]},
        "response_option": {"objective_id": seeded["objective"]["objective_id"], "path_id": seeded["path"]["path_id"]},
        "historical_analogue": {"query_id": theme["theme_id"], "episode_id": episode["episode_id"]},
        "analytic_forecast": {"claims": claims[:1]},
        "forecast_indicator": {"forecast_ids": [seeded["forecast"]["forecast_id"]]},
    }


def test_the_offline_backend_proposes_every_kind_a_human_can_then_accept(tmp_path):
    """One candidate per proposable kind: emitted, schema-valid, citing only
    what it was shown, recorded, and accepted. A kind the offline seam cannot
    complete is a defect, not a skip."""
    from workbench_support import make_workbench, seed_mission
    pipeline, ctx = make_workbench(tmp_path)
    seeded = seed_mission(pipeline, ctx)
    backend = DeterministicBackend()
    assist = AnalyticalAssist(package=backend.package(), backend=backend)
    inputs = _kind_inputs(ctx, seeded)
    assert set(inputs) == set(proposable_kinds())
    failures = {}
    for kind in proposable_kinds():
        result = assist.propose(ctx, task=f"propose {kind}", target_kind=kind,
                                inputs=inputs[kind], input_refs=())
        if result.get("status") != "PROPOSED":
            failures[kind] = result
            continue
        content = result["proposal"]["content"]
        schema = candidate_schema(kind)
        for field, shape in schema["properties"].items():
            value = content.get(field)
            if shape.get("enum"):
                assert value in shape["enum"], (kind, field, value)
            elif shape.get("type") == "number":
                assert 0 < value < 1, (kind, field, value)
            elif shape.get("type") == "array":
                assert value and all(isinstance(v, str) and v for v in value), (kind, field, value)
            else:
                assert isinstance(value, str) and value, (kind, field, value)
        assert not unresolvable_ids(kind, content, available_ids(inputs[kind])), kind
        resolved = resolve_candidate(ctx, result["proposal"]["proposal_id"], accept=True,
                                     actor_id="jan", actor_kind="HUMAN")
        assert resolved["content"]["target_kind"] == kind
    assert not failures, failures
