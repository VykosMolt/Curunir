"""The governed model-proposal seam, end to end, with no network.

What must hold: the response schema is generated from the same table the human
review path enforces; a provider may only cite identifiers it was shown; a
provider failure is retained as evidence rather than becoming analytical
content; and nothing a provider emits becomes state without a human.
"""
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

from analytic_support import GLEIF_ACME, make_analytic
from semantic_support import T0, plant_manifestation

pytestmark = pytest.mark.no_db


# ---- schema is generated from the enforced table, not restated -------------

def test_every_proposable_kind_has_a_schema_over_exactly_its_binding_keys():
    for kind in proposable_kinds():
        schema = candidate_schema(kind)
        assert tuple(schema["required"]) == CANDIDATE_BINDING_KEYS[kind], kind
        assert set(schema["properties"]) == set(CANDIDATE_BINDING_KEYS[kind]), kind
        assert schema["additionalProperties"] is False, kind


def test_a_new_binding_key_reaches_the_schema_without_editing_it():
    """The drift lock: schema follows the table, so it cannot fall behind."""
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


# ---- a provider may only cite what it was shown ----------------------------

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


# ---- availability degrades honestly ---------------------------------------

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


# ---- the whole seam, over real world-model state ---------------------------

def _seed(tmp_path):
    pipeline, ctx = make_analytic(tmp_path)
    plant_manifestation(pipeline, source_id="gleif",
                        native_id="lei/ACMELEI000000000001", body=GLEIF_ACME,
                        media_type="application/json", retrieval_time=T0)
    pipeline.process_new_evidence()
    claim = next(c["claim_id"] for c in ctx.store.current_claims().values()
                 if c["predicate"] == "entity_status")
    return ctx, claim


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

    # the complete outcome is retained, and it cites the real claim
    inference = next(r for r in ctx.store.records_of("inference")
                     if r["inference_id"] == result["inference_id"])
    assert inference["validation"] == "VALID"
    assert inference["output"]["supporting_claim_ids"] == [claim]
    assert inference["model_id"] == backend.package().model_id

    # it is not analytical state
    proposal_id = result["proposal"]["proposal_id"]
    assert proposal_id not in ctx.store.current_themes()

    # only a human can accept
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
    # nothing was proposed
    assert not [p for p in ctx.store.records_of("analytical_proposal")]


def test_the_backend_receives_the_target_kind_the_legacy_callable_cannot(tmp_path):
    """Why `backend` exists at all: InferFn has no way to carry target_kind,
    and without it a provider cannot be constrained to the right schema."""
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
    """The marking gate must run before the backend is touched at all."""
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


# ---- the command layer cannot be used to read around the lattice -----------

def _command_context(ctx, root, context):
    from curunir_workbench.commands import CommandContext
    from semantic_support import MARK
    return CommandContext(store=ctx.store, root=root, context=context,
                          marking=MARK, now_fn=ctx.now_fn)


def test_a_proposal_request_cannot_show_a_provider_what_the_actor_cannot_see(
        tmp_path, monkeypatch):
    """A restricted record the requester cannot view is reported as unknown,
    and never reaches the provider payload."""
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

    # CTX_B holds no SPECIAL compartment: the record is simply unknown to it,
    # and the provider is never reached.
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
    """The shipped default is public-releasable-only: compartmented evidence
    does not leave the deployment just because a cleared analyst asked."""
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


# ---- the HTTP boundary -----------------------------------------------------

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
