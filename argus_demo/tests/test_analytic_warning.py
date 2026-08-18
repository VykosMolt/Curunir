"""Warning locks: the tier comes from the named rule and nowhere else,
weak evidence caps escalation, projection is idempotent and append-only,
and a settled forecast resolves its warning."""
from __future__ import annotations

import pytest

from curunir_analytic.contracts import ImpactEdge, ResolutionRule, WarningRecord
from curunir_analytic.forecasts import (create_forecast, try_machine_resolution,
                                        update_probability)
from curunir_analytic.impact import build_path, create_objective
from curunir_analytic.warning import (TIER_RULE_V1, derive_tier,
                                      evidence_confidence, probability_band,
                                      project_warning, refresh_warnings,
                                      time_pressure)
from curunir_semantic.worldmodel import world_object_id

from analytic_support import (GLEIF_ACME, GLEIF_ACME_SUSPENDED, MARK, T0,
                              make_analytic)
from semantic_support import plant_manifestation

pytestmark = pytest.mark.no_db

HORIZON = "2026-08-17T18:00:00+00:00"
ACME = "LEI:ACMELEI000000000001"


def _seed(pipeline, ctx):
    plant_manifestation(pipeline, source_id="gleif",
                        native_id="lei/ACMELEI000000000001",
                        body=GLEIF_ACME, media_type="application/json",
                        retrieval_time=T0)
    pipeline.process_new_evidence()
    return {c["predicate"]: c["claim_id"] for c in ctx.store.current_claims().values()
            if c["subject_ref"] == ACME}


def _objective(ctx, priority="HIGH"):
    return create_objective(
        ctx, mission_context="m1",
        statement="Maintain visibility of Acme's legal standing",
        priority=priority)


def _forecast(ctx, by_predicate, probability=0.35, expected="INACTIVE",
              horizon=HORIZON):
    rule = ResolutionRule(
        kind="CLAIM_PREDICATE",
        criteria=f"GLEIF entity_status for {ACME} reads {expected}",
        claim_subject_ref=ACME, claim_attribute="entity_status",
        expected_value=expected,
        absence_min_successful_sources=1,
        absence_required_source_ids=("gleif",))
    return create_forecast(
        ctx, question=f"Will {ACME} be suspended by {HORIZON}?",
        outcome_semantics="TRUE iff entity_status reads INACTIVE",
        proposition_refs=(("claim", by_predicate["entity_status"]),),
        horizon_time=horizon, resolution=rule, probability=probability,
        probability_basis="registry base rates", author="jan",
        domain="corporate-registry",
        supporting_claim_ids=[by_predicate["entity_status"]])


def _path(ctx, by_predicate, objective):
    acme_object = world_object_id(ACME)
    edge = ImpactEdge(edge_id="e1", from_kind="object", from_id=acme_object,
                      to_kind="mission_objective",
                      to_id=objective["objective_id"],
                      edge_kind="DEPENDENCY", effect_order="DIRECT",
                      authority="DERIVED", note="",
                      basis_ids=(by_predicate["entity_status"],),
                      assumption_ids=())
    return build_path(ctx, objective_id=objective["objective_id"],
                      summary="registry standing exposure", edges=(edge,))


# ---- the named rule --------------------------------------------------------


def test_tier_comes_from_the_table_with_recorded_adjustments():
    tier, notes = derive_tier("LIKELY", "CRITICAL", "NEAR", "STRONG")
    assert tier == "CRITICAL" and any("table[CRITICAL][LIKELY]" in n
                                      for n in notes)
    tier, notes = derive_tier("POSSIBLE", "HIGH", "IMMINENT", "STRONG")
    assert tier == "PRIORITY", "imminence bumps exactly one tier"
    assert any("IMMINENT" in n for n in notes)
    tier, notes = derive_tier("VERY_LIKELY", "CRITICAL", "NEAR", "WEAK")
    assert tier == "PRIORITY", \
        "an unsupported number does not drive CRITICAL alone"
    assert any("caps at PRIORITY" in n for n in notes)


def test_component_derivations_are_typed():
    assert probability_band(0.05) == "REMOTE"
    assert probability_band(0.62) == "LIKELY"
    assert probability_band(0.80) == "VERY_LIKELY"
    assert time_pressure("2026-08-17T12:00:00+00:00",
                         "2026-08-17T11:00:00+00:00") == "PASSED"
    assert time_pressure("2026-08-17T12:00:00+00:00",
                         "2026-08-18T12:00:00+00:00") == "IMMINENT"
    assert time_pressure("2026-08-17T12:00:00+00:00",
                         "2026-09-10T12:00:00+00:00") == "NEAR"
    assert evidence_confidence({"origin_families": (),
                                "degraded_claim_count": 0}) == "NONE"
    assert evidence_confidence({"origin_families": ("gleif",),
                                "degraded_claim_count": 0}) == "WEAK"
    assert evidence_confidence({"origin_families": ("a", "b"),
                                "degraded_claim_count": 1}) == "WEAK", \
        "a degraded basis does not count at full strength"
    assert evidence_confidence({"origin_families": ("a", "b", "c"),
                                "degraded_claim_count": 0}) == "STRONG"


def test_a_free_tier_is_unconstructible():
    with pytest.raises(ValueError, match="named rule"):
        WarningRecord(
            warning_id="w1", version=1, mission_context="m1",
            objective_id="o1", forecast_id="f1", impact_path_ids=(),
            probability_band="LIKELY", consequence="HIGH",
            time_pressure="NEAR", evidence_confidence="MODERATE",
            tier="CRITICAL", tier_rule_id="",
            component_basis=(("probability_band", "b"),),
            status="ACTIVE", change_reason="", history=(),
            recorded_time=T0, marking=MARK)


# ---- projection over the store --------------------------------------------


def test_projection_binds_forecast_objective_and_paths(tmp_path):
    pipeline, ctx = make_analytic(tmp_path)
    by_predicate = _seed(pipeline, ctx)
    objective = _objective(ctx, priority="HIGH")
    path = _path(ctx, by_predicate, objective)
    forecast = _forecast(ctx, by_predicate, probability=0.62)
    warning = project_warning(ctx, forecast_id=forecast["forecast_id"],
                              objective_id=objective["objective_id"])
    assert warning["tier"] == "PRIORITY"  # table[HIGH][LIKELY]
    assert warning["tier_rule_id"] == TIER_RULE_V1
    assert warning["impact_path_ids"] == [path["path_id"]] \
        or tuple(warning["impact_path_ids"]) == (path["path_id"],), \
        "the typed reason this forecast threatens this objective"
    assert warning["evidence_confidence"] == "WEAK"  # one origin family
    basis_components = {component for component, _ in warning["component_basis"]}
    assert {"probability_band", "consequence", "time_pressure",
            "evidence_confidence", "tier_rule"} <= basis_components


def test_reprojection_is_append_only_and_names_what_moved(tmp_path):
    pipeline, ctx = make_analytic(tmp_path)
    by_predicate = _seed(pipeline, ctx)
    objective = _objective(ctx, priority="HIGH")
    _path(ctx, by_predicate, objective)
    # a NEAR horizon: no imminence bump muddying the escalation
    forecast = _forecast(ctx, by_predicate, probability=0.35,
                         horizon="2026-09-10T12:00:00+00:00")
    warning = project_warning(ctx, forecast_id=forecast["forecast_id"],
                              objective_id=objective["objective_id"])
    assert warning["tier"] == "ATTENTION"  # table[HIGH][POSSIBLE], NEAR, no bump
    before = ctx.store.head()["event_count"]
    unchanged = refresh_warnings(ctx)
    assert ctx.store.head()["event_count"] == before, \
        "unchanged inputs append nothing"
    assert unchanged[0]["version"] == warning["version"]
    update_probability(ctx, forecast["forecast_id"], probability=0.62,
                       reason="filing trouble", actor_id="jan",
                       actor_kind="HUMAN")
    escalated = refresh_warnings(ctx)[0]
    assert escalated["tier"] == "PRIORITY"
    assert escalated["status"] == "ESCALATED"
    versions = ctx.store.analytic_versions("strategic_warning",
                                           warning["warning_id"])
    assert [v["tier"] for v in versions] == ["ATTENTION", "PRIORITY"], \
        "the old tier is history, never overwritten"
    kinds = [t["transition_type"]
             for t in ctx.store.transitions_for(warning["warning_id"])]
    assert kinds == ["RAISED", "ESCALATED"]


def test_settled_forecast_resolves_its_warning(tmp_path):
    pipeline, ctx = make_analytic(tmp_path)
    by_predicate = _seed(pipeline, ctx)
    objective = _objective(ctx)
    _path(ctx, by_predicate, objective)
    forecast = _forecast(ctx, by_predicate, probability=0.62)
    warning = project_warning(ctx, forecast_id=forecast["forecast_id"],
                              objective_id=objective["objective_id"])
    plant_manifestation(pipeline, source_id="gleif",
                        native_id="lei/ACMELEI000000000001",
                        body=GLEIF_ACME_SUSPENDED, media_type="application/json",
                        retrieval_time=ctx.now_fn())
    pipeline.process_new_evidence()
    assert try_machine_resolution(
        ctx, forecast["forecast_id"])["status"] == "RESOLVED_TRUE"
    resolved = refresh_warnings(ctx)[0]
    assert resolved["status"] == "RESOLVED"
    # a further refresh is a no-op: resolved warnings are done
    before = ctx.store.head()["event_count"]
    refresh_warnings(ctx)
    assert ctx.store.head()["event_count"] == before


def test_warning_cannot_be_raised_about_a_settled_question(tmp_path):
    pipeline, ctx = make_analytic(tmp_path)
    by_predicate = _seed(pipeline, ctx)
    objective = _objective(ctx)
    forecast = _forecast(ctx, by_predicate, probability=0.62)
    plant_manifestation(pipeline, source_id="gleif",
                        native_id="lei/ACMELEI000000000001",
                        body=GLEIF_ACME_SUSPENDED, media_type="application/json",
                        retrieval_time=ctx.now_fn())
    pipeline.process_new_evidence()
    try_machine_resolution(ctx, forecast["forecast_id"])
    with pytest.raises(ValueError, match="settled"):
        project_warning(ctx, forecast_id=forecast["forecast_id"],
                        objective_id=objective["objective_id"])


def test_warning_floors_marking_on_referenced_forecast(tmp_path):
    # review B-1: a warning EMBEDS the forecast's state (its tier/band come from
    # the forecast's probability), so its marking must be floored on the
    # forecast's (and objective's) live marking — else a warning projected or
    # RE-projected over a restricted forecast under-classifies it.
    from curunir_analytic.substrate import AnalyticContext
    from curunir_operational.access import AccessContext, can_view, marking_from_record
    from workbench_support import RESTRICTED_MARK
    pipeline, ctx = make_analytic(tmp_path)
    claims = _seed(pipeline, ctx)
    rctx = AnalyticContext(store=ctx.store, actor="analyst-a",
                           marking=RESTRICTED_MARK, now_fn=ctx.now_fn)   # SPECIAL compartment
    obj = _objective(rctx)
    fc = _forecast(rctx, claims)
    warn = project_warning(ctx, forecast_id=fc["forecast_id"],           # projected on the PUBLIC pass
                           objective_id=obj["objective_id"])
    assert "SPECIAL" in marking_from_record(warn["marking"]).compartments  # floored, not PUBLIC
    uncleared = AccessContext("c", "d", "HUMAN", ("ANALYST",), releasability=("PUBLIC",))
    assert can_view(warn["marking"], uncleared) is False                 # invisible to the uncleared


def test_no_derived_record_underclassifies_anything_it_references(tmp_path):
    # CLASS-LEVEL property (review A1/A2/A3, the CORRECT invariant): after the
    # SHIPPED background pass at a lower marking, NO analytic_transition or
    # review_item may be viewable by an actor who cannot view SOMETHING THE RECORD
    # REFERENCES — its subject OR any object in evidence_refs (a transition quotes
    # a non-subject indicator's description; a review quotes a claim). The earlier
    # version of this test asserted "record >= its own subject", the invariant the
    # code already enforced, and so could never catch the non-subject leaks the
    # confirmatory review found — this asserts "record >= EVERYTHING it references".
    import importlib
    from curunir_analytic.substrate import AnalyticContext
    from curunir_analytic.store import ANALYTIC_ID_FIELDS
    from curunir_analytic.indicators import arm_indicator, check_indicators
    from curunir_operational.access import AccessContext, can_view
    from workbench_support import RESTRICTED_MARK
    ind_mod = importlib.import_module("test_analytic_indicators")
    pipeline, ctx = make_analytic(tmp_path)                       # PUBLIC background ctx
    claims = _seed(pipeline, ctx)
    rctx = AnalyticContext(store=ctx.store, actor="analyst-a",
                           marking=RESTRICTED_MARK, now_fn=ctx.now_fn)
    obj = _objective(rctx); fc = _forecast(rctx, claims)         # SPECIAL objective + forecast
    project_warning(ctx, forecast_id=fc["forecast_id"], objective_id=obj["objective_id"])
    # arm a SPECIAL indicator on the (SPECIAL) forecast and FIRE it on the PUBLIC pass
    arm_indicator(rctx, description="OPERATION MOONLIGHT covert delisting watch",
                  forecast_ids=(fc["forecast_id"],), kind="PRESENCE", direction="SUPPORTS",
                  desired_observation_type="ENTITY_ATTRIBUTE", desired_subject_ref=ACME,
                  desired_attribute="entity_status", expected_value="INACTIVE",
                  effect=ind_mod._presence_effect())
    ind_mod._flip_to_suspended(pipeline, ctx)
    check_indicators(ctx)                                        # shipped background pass, PUBLIC
    project_warning(ctx, forecast_id=fc["forecast_id"], objective_id=obj["objective_id"])

    uncleared = AccessContext("c", "d", "HUMAN", ("ANALYST",), releasability=("PUBLIC",))
    # marking of EVERY object, by id (analytic + claims + observations —
    # the resolver hole R25A-2 hid observations from this test)
    mark_of = {}
    for kind, (_, id_field) in ANALYTIC_ID_FIELDS.items():
        for rec in ctx.store.current_analytics(kind).values():
            mark_of[rec[id_field]] = rec["marking"]
    for cid, c in ctx.store.current_claims().items():
        mark_of[cid] = c["marking"]
    for rec in ctx.store.records_of("semantic_observation"):
        mark_of[rec["observation_id"]] = rec["marking"]

    def _referenced_restricted(record):
        refs = [record["subject_id"], *record.get("evidence_refs", ())]
        return [r for r in refs if r in mark_of and not can_view(mark_of[r], uncleared)]

    leaks = []
    for rec in (*ctx.store.records_of("analytic_transition"), *ctx.store.records_of("review_item")):
        restricted = _referenced_restricted(rec)
        if restricted and can_view(rec["marking"], uncleared):
            leaks.append((rec.get("transition_type") or rec.get("kind"),
                          rec.get("detail", "")[:50], restricted))
    assert leaks == [], f"derived records under-classifying a REFERENCED object: {leaks}"


def test_apply_probability_does_not_embed_indicator_rationale(tmp_path):
    # R25A-1: APPLY_PROBABILITY wrote the SPECIAL indicator's rationale into
    # the PUBLIC forecast version (change_reason / probability_basis). A4
    # stopped embedding description in _fold; the fire-side sibling must
    # cite the indicator by id, not its compartmented rationale.
    from curunir_analytic.contracts import IndicatorEffect
    from curunir_analytic.indicators import arm_indicator, check_indicators
    from curunir_analytic.substrate import AnalyticContext
    from curunir_operational.access import AccessContext, can_view, marking_from_record
    from workbench_support import RESTRICTED_MARK
    SECRET = "COMPARTMENTED_RATIONALE_MOONLIGHT_XYZ"
    pipeline, ctx = make_analytic(tmp_path)
    claims = _seed(pipeline, ctx)
    fc = _forecast(ctx, claims)                                 # PUBLIC forecast
    assert "SPECIAL" not in marking_from_record(fc["marking"]).compartments
    rctx = AnalyticContext(store=ctx.store, actor="analyst-a",
                           marking=RESTRICTED_MARK, now_fn=ctx.now_fn)
    arm_indicator(rctx, description="OPERATION MOONLIGHT covert delisting watch",
                  forecast_ids=(fc["forecast_id"],), kind="PRESENCE",
                  direction="SUPPORTS",
                  desired_observation_type="ENTITY_ATTRIBUTE",
                  desired_subject_ref=ACME, desired_attribute="entity_status",
                  expected_value="INACTIVE",
                  effect=IndicatorEffect(mode="APPLY_PROBABILITY",
                                         target_probability=0.62,
                                         rationale=SECRET, authorized_by="jan",
                                         authorized_kind="HUMAN"))
    from test_analytic_indicators import _flip_to_suspended
    _flip_to_suspended(pipeline, ctx)
    check_indicators(ctx)
    moved = ctx.store.current_forecasts()[fc["forecast_id"]]
    uncleared = AccessContext("c", "d", "HUMAN", ("ANALYST",),
                              releasability=("PUBLIC",))
    assert moved["probability"] == 0.62
    assert SECRET not in (moved.get("change_reason") or "")
    assert SECRET not in (moved.get("probability_basis") or "")
    # the version itself stays PUBLIC (A4: association is not embed); the
    # PROBABILITY_UPDATED transition cites the indicator and is floored
    assert can_view(moved["marking"], uncleared) is True
    assert "SPECIAL" not in marking_from_record(moved["marking"]).compartments
    leaked_transitions = [
        t for t in ctx.store.transitions_for(fc["forecast_id"])
        if SECRET in (t.get("detail") or "") and can_view(t["marking"], uncleared)
    ]
    assert leaked_transitions == []


def test_fire_does_not_embed_observation_value(tmp_path):
    # R25A-2: _reference_markings must resolve observations, and _fire must
    # not embed a SPECIAL observation's value in a PUBLIC indicator's
    # FIRED transition / version.
    import hashlib
    from argus.source_intelligence.models import digest_id
    from curunir_analytic.contracts import IndicatorEffect
    from curunir_analytic.indicators import arm_indicator, check_indicators
    from curunir_analytic.substrate import _reference_markings
    from curunir_fabric.contracts import ManifestationRecord
    from curunir_operational.access import AccessContext, can_view
    from curunir_semantic.contracts import EvidenceAnchor, SemanticObservation
    from workbench_support import RESTRICTED_MARK
    SECRET = "COVERT_SANCTION_CODE_MOONLIGHT"
    pipeline, ctx = make_analytic(tmp_path)
    claims = _seed(pipeline, ctx)
    fc = _forecast(ctx, claims)
    arm_indicator(ctx, description="any entity_status observation on ACME",
                  forecast_ids=(fc["forecast_id"],), kind="PRESENCE",
                  direction="SUPPORTS",
                  desired_observation_type="ENTITY_ATTRIBUTE",
                  desired_subject_ref=ACME, desired_attribute="entity_status",
                  expected_value="",
                  effect=IndicatorEffect(mode="REVIEW_ONLY"))
    now = ctx.now_fn()
    body = b'{"secret": true}'
    digest = hashlib.sha256(body).hexdigest()
    path = tmp_path / "custody" / digest
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(body)
    manifestation = ManifestationRecord(
        manifestation_id=digest_id("manifestation", "gleif", "secret", digest, now),
        source_id="gleif", connector_id="gleif-test", connector_version="1.0",
        native_id="lei/ACMELEI000000000001-secret",
        request_url="lei/secret", final_url="lei/secret",
        content_sha256=digest, content_store_path=str(path),
        media_type="application/json", temporal_status="LIVE", source_time=None,
        archive_capture_time=None, retrieval_time=now, http_status=200,
        redirects=(), etag="", last_modified="", truncated=False,
        retrieval_id=digest_id("retrieval", digest, now),
        custody_ingestion_id=digest_id("ingestion", digest),
        source_object_id=digest_id("source-object", "gleif", digest),
        execution_id=digest_id("execution", "gleif", "secret", now),
        prior_manifestation_id=None, marking=RESTRICTED_MARK)
    pipeline.store.append("FABRIC_MANIFESTATION_RECORDED", manifestation,
                          recorded_time=now, actor="t")
    observation = SemanticObservation(
        observation_id=digest_id("obs", "secret", now),
        document_id=digest_id("doc", "secret"),
        manifestation_id=manifestation.manifestation_id,
        source_id="gleif", observation_type="ENTITY_ATTRIBUTE",
        subject_ref=ACME, attribute="entity_status", value=SECRET,
        object_ref="", valid_from=None, valid_to=None, source_time=None,
        time_precision="UNKNOWN", language="en", representation="ORIGINAL",
        anchors=(EvidenceAnchor(
            manifestation_id=manifestation.manifestation_id, source_id="gleif",
            content_sha256=digest, kind="FIELD",
            field_path="data.attributes.entity.status", exact_value=SECRET),),
        producer_kind="DETERMINISTIC_PARSER", producer_id="test",
        producer_version="1.0", inference_id="", recorded_time=now,
        marking=RESTRICTED_MARK)
    pipeline.store.append("SEMANTIC_OBSERVATION_RECORDED", observation,
                          recorded_time=now, actor="t")
    assert _reference_markings(ctx, (observation.observation_id,)), \
        "resolver must resolve a cited observation"
    check_indicators(ctx)
    uncleared = AccessContext("c", "d", "HUMAN", ("ANALYST",),
                              releasability=("PUBLIC",))
    leaks = []
    for rec in (*ctx.store.records_of("analytic_transition"),
                *ctx.store.records_of("forecast_indicator")):
        text = " ".join(str(rec.get(k, "")) for k in
                        ("detail", "change_reason", "probability_basis"))
        if SECRET in text and can_view(rec.get("marking"), uncleared):
            leaks.append((rec.get("transition_type") or rec.get("status"),
                          rec.get("detail") or rec.get("change_reason")))
    assert leaks == [], f"SPECIAL observation value leaked into PUBLIC record: {leaks}"
