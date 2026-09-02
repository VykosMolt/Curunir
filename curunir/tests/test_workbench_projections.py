"""Mission projection: cross-plane composition, access filtering before
serialization, redaction of hidden references, provenance descent."""
from __future__ import annotations

import json

import pytest

from curunir_workbench.projections import MissionProjection
from curunir_workbench.provenance import ascend, claim_descent, descend, evidence_view
from curunir_workbench.search import search
from curunir_workbench.views import (coverage_matrix, entity_dossier, entity_list,
                                     event_list, graph, hypothesis_matrix,
                                     map_view, review_queue,
                                     source_independence, timeline)

from workbench_support import CTX_A, CTX_B, make_workbench, seed_mission

pytestmark = pytest.mark.no_db


@pytest.fixture()
def mission(tmp_path):
    pipeline, ctx = make_workbench(tmp_path)
    seeded = seed_mission(pipeline, ctx)
    return pipeline, ctx, seeded


def test_overview_composes_every_plane(mission):
    _, ctx, seeded = mission
    view = MissionProjection(ctx.store, CTX_A).overview()
    counts = view["counts"]
    assert counts["claims"] > 0
    assert counts["forecasts_open"] == 1
    assert counts["warnings_active"] >= 1
    assert counts["hypotheses"] == 1
    assert counts["requirements_open"] >= 1
    assert counts["tasks_open"] >= 1
    assert view["active_warnings"][0]["forecast_id"] == seeded["forecast"]["forecast_id"]
    assert view["open_forecasts"][0]["probability"] == 0.35


def test_restricted_records_invisible_to_b(mission):
    _, ctx, seeded = mission
    view_a = MissionProjection(ctx.store, CTX_A)
    view_b = MissionProjection(ctx.store, CTX_B)
    secret = seeded["secret_object_id"]
    assert secret in view_a.visible_object_ids()
    assert secret not in view_b.visible_object_ids()
    assert view_a.get("analytic_assumption", seeded["secret_assumption_id"]) is not None
    assert view_b.get("analytic_assumption", seeded["secret_assumption_id"]) is None
    # counts computed from the filtered set only
    assert view_a.overview()["counts"]["entities"] \
        == view_b.overview()["counts"]["entities"] + 1
    # the whole serialized payload of B never contains the hidden id
    blob = json.dumps(view_b.overview()) + json.dumps(view_b.family("analytic_assumption"))
    assert secret not in blob
    assert seeded["secret_assumption_id"] not in blob


def test_search_does_not_leak_hidden_objects(mission):
    _, ctx, _ = mission
    hit_a = search(MissionProjection(ctx.store, CTX_A), "Sensitive Partner")
    hit_b = search(MissionProjection(ctx.store, CTX_B), "Sensitive Partner")
    assert hit_a["total"] >= 1
    assert hit_b["total"] == 0


def test_graph_does_not_leak_hidden_nodes(mission):
    _, ctx, seeded = mission
    graph_b = graph(MissionProjection(ctx.store, CTX_B))
    node_ids = {n["object_id"] for n in graph_b["nodes"]}
    assert seeded["secret_object_id"] not in node_ids
    blob = json.dumps(graph_b)
    assert seeded["secret_object_id"] not in blob


def test_activity_feed_is_attributable_and_filtered(mission):
    _, ctx, seeded = mission
    feed_a = MissionProjection(ctx.store, CTX_A).activity_feed(limit=500)
    feed_b = MissionProjection(ctx.store, CTX_B).activity_feed(limit=500)
    assert any(e["actor"] == "analyst-a" for e in feed_a)
    blob = json.dumps(feed_b)
    assert seeded["secret_object_id"] not in blob
    assert len(feed_a) > len(feed_b)


def test_entity_dossier_distinguishes_history_from_current(mission):
    _, ctx, seeded = mission
    projection = MissionProjection(ctx.store, CTX_A)
    entities = entity_list(projection)
    acme = next(e for e in entities if "Acme" in " ".join(map(str, e["labels"])))
    dossier = entity_dossier(projection, acme["object_id"])
    assert dossier["current"]["object_id"] == acme["object_id"]
    assert len(dossier["history"]) == dossier["current"]["history_count"]
    assert dossier["claims"], "entity dossier must expose its claims"
    assert dossier["forecasts"], "forecast about the entity's claim must appear"


def test_timeline_axes_differ(mission):
    _, ctx, _ = mission
    projection = MissionProjection(ctx.store, CTX_A)
    valid = timeline(projection, axis="valid")
    knowledge = timeline(projection, axis="knowledge")
    assert valid["axis"] == "valid" and knowledge["axis"] == "knowledge"
    # knowledge axis includes forecast versions (no valid time), valid axis not
    assert any(e["kind"] == "forecast" for e in knowledge["entries"])
    assert not any(e["kind"] == "forecast" for e in valid["entries"])
    with pytest.raises(ValueError):
        timeline(projection, axis="wallclock")


def test_full_descent_warning_to_source(mission):
    _, ctx, seeded = mission
    projection = MissionProjection(ctx.store, CTX_A)
    chain = descend(projection, "strategic_warning", seeded["warning"]["warning_id"])
    kinds = [n["kind"] for n in chain["chain"]]
    assert kinds[0] == "strategic_warning"
    assert "analytic_forecast" in kinds
    assert "mission_objective" in kinds
    assert chain["claims"], "descent must reach claims"
    first = chain["claims"][0]
    observation = first["observations"][0]
    anchor = observation["anchors"][0]
    assert anchor["manifestation"]["manifestation_id"]
    assert anchor["source"]["source_id"] == "gleif"
    assert anchor["anchor"]["field_path"] or anchor["anchor"]["start"] is not None


def test_ascent_source_to_dependents(mission):
    _, ctx, seeded = mission
    projection = MissionProjection(ctx.store, CTX_A)
    up = ascend(projection, "fabric_source_descriptor", "gleif")
    assert up["claims"]
    kinds = {d["kind"] for d in up["dependents"]}
    assert "analytic_forecast" in kinds
    assert "strategic_warning" in kinds
    assert "hypothesis" in kinds


def test_evidence_view_exposes_exact_anchors_and_payload(mission):
    _, ctx, seeded = mission
    projection = MissionProjection(ctx.store, CTX_A)
    descent = claim_descent(projection, seeded["status_claim"]["claim_id"])
    manifestation_id = descent["observations"][0]["anchors"][0]["manifestation"]["manifestation_id"]
    view = evidence_view(projection, manifestation_id)
    assert view["anchors"], "anchors must be exposed"
    anchor = view["anchors"][0]
    assert anchor["exact_value"] or anchor["field_path"] or anchor["start"] is not None
    assert view["claims"]
    assert view["source"]["source_id"] == "gleif"


def test_hypothesis_matrix_projected_from_links(mission):
    _, ctx, seeded = mission
    projection = MissionProjection(ctx.store, CTX_A)
    matrix = hypothesis_matrix(projection)
    assert matrix["hypotheses"][0]["hypothesis_id"] == seeded["hypothesis"]["hypothesis_id"]
    row = next(r for r in matrix["rows"]
               if r["claim_id"] == seeded["status_claim"]["claim_id"])
    assert row["cells"][seeded["hypothesis"]["hypothesis_id"]] == "SUPPORTS"


def test_source_independence_arithmetic(mission):
    _, ctx, seeded = mission
    projection = MissionProjection(ctx.store, CTX_A)
    view = source_independence(projection,
                               claim_ids=(seeded["status_claim"]["claim_id"],))
    assert view["unique_sources"] == ["gleif"]
    assert view["manifestation_count"] >= 1
    assert view["claims"][0]["independent_basis_count"] >= 1


def test_review_queue_unifies_planes(mission):
    _, ctx, _ = mission
    projection = MissionProjection(ctx.store, CTX_A)
    queue = review_queue(projection)
    assert isinstance(queue["items"], list)
    assert queue["open_count"] == sum(1 for i in queue["items"] if i["status"] == "OPEN")


def test_map_lists_unlocated_instead_of_geocoding(mission):
    _, ctx, _ = mission
    view = map_view(MissionProjection(ctx.store, CTX_A))
    for feature in view["feature_collection"]["features"]:
        assert feature["geometry"]["coordinates"], "no fabricated coordinates"


def test_search_explains_matches_and_preserves_types(mission):
    _, ctx, _ = mission
    projection = MissionProjection(ctx.store, CTX_A)
    hits = search(projection, "Acme")
    assert hits["total"] > 0
    types = {h["type"] for h in hits["results"]}
    assert "object" in types
    assert all(h["matched_fields"] for h in hits["results"])
    only_claims = search(projection, "Acme", types=("semantic_claim",))
    assert {h["type"] for h in only_claims["results"]} <= {"semantic_claim"}
