"""Theme engine: independence-driven status, preserved membership history,
contested state, lineage-preserving merge/split, deterministic discovery."""
from __future__ import annotations

import pytest

from curunir_analytic.contracts import ThemeRecord
from curunir_analytic.themes import (apply_merge, apply_split, create_theme,
                                     discover_theme_candidates, explain_theme,
                                     propose_merge, refresh_theme, resolve_theme,
                                     update_membership)
from curunir_semantic.contracts import ClaimStateRecord

from analytic_support import (GLEIF_ACME, MARK, T0, make_analytic, plant_page,
                              statement_page)
from semantic_support import plant_manifestation

pytestmark = pytest.mark.no_db

GLEIF_OTHER = GLEIF_ACME.replace(b"ACMELEI000000000001", b"OTHERLEI00000000002") \
    .replace(b"Acme Industri AS", b"Borg Verft AS")


def _seed_acme(pipeline, ctx):
    plant_manifestation(pipeline, source_id="gleif", native_id="lei/ACMELEI000000000001",
                        body=GLEIF_ACME, media_type="application/json", retrieval_time=T0)
    pipeline.process_new_evidence()
    claims = ctx.store.current_claims()
    return {c["predicate"]: c["claim_id"] for c in claims.values()
            if c["subject_ref"] == "LEI:ACMELEI000000000001"}


def test_theme_cannot_exist_unsupported(tmp_path):
    _, ctx = make_analytic(tmp_path, seeded=False)
    with pytest.raises(ValueError, match="unsupported title"):
        create_theme(ctx, title="Corporate instability", supporting_claim_ids=())


def test_one_origin_family_stays_emerging_until_independent_family(tmp_path):
    pipeline, ctx = make_analytic(tmp_path)
    by_predicate = _seed_acme(pipeline, ctx)
    # a second derivative retrieval of the same registry record adds reach,
    # not independence
    plant_manifestation(pipeline, source_id="gleif",
                        native_id="lei/ACMELEI000000000001?refresh",
                        body=GLEIF_ACME, media_type="application/json",
                        retrieval_time="2026-08-17T14:00:00+00:00")
    pipeline.process_new_evidence()
    theme = create_theme(ctx, title="Acme registry standing",
                         supporting_claim_ids=[by_predicate["entity_status"],
                                               by_predicate["legal_name"]],
                         provenance_kind="RULE")
    assert theme["status"] == "EMERGING"
    assert len(theme["basis"]["origin_families"]) == 1

    # an independent origin family (a different site) promotes the theme
    plant_page(pipeline, url="https://acme-industri.example.no/about",
               body=statement_page("Acme Industri AS remains active in Oslo"),
               retrieval_time="2026-08-17T15:00:00+00:00")
    pipeline.process_new_evidence()
    page_claim = next(c["claim_id"] for c in ctx.store.current_claims().values()
                      if c["subject_ref"].startswith("URL:"))
    updated = update_membership(ctx, theme["theme_id"],
                                add_supporting=[page_claim],
                                caused_by="test", rationale="independent site statement")
    assert updated["status"] == "ACTIVE"
    assert len(updated["basis"]["origin_families"]) == 2
    assert updated["version"] == 2
    # prior membership is preserved as version 1 in the log
    versions = ctx.store.analytic_versions("analytic_theme", theme["theme_id"])
    assert len(versions) == 2
    assert page_claim not in versions[0]["basis"]["supporting_claim_ids"]
    assert page_claim in versions[1]["basis"]["supporting_claim_ids"]


def test_contradiction_makes_theme_contested_without_deleting_support(tmp_path):
    pipeline, ctx = make_analytic(tmp_path)
    by_predicate = _seed_acme(pipeline, ctx)
    plant_page(pipeline, url="https://registerwatch.example.org/acme",
               body=statement_page("Acme Industri AS has ceased operations"),
               retrieval_time="2026-08-17T15:00:00+00:00")
    pipeline.process_new_evidence()
    contra = next(c["claim_id"] for c in ctx.store.current_claims().values()
                  if c["subject_ref"].startswith("URL:"))
    theme = create_theme(ctx, title="Acme registry standing",
                         supporting_claim_ids=[by_predicate["entity_status"]],
                         provenance_kind="RULE")
    updated = update_membership(ctx, theme["theme_id"], add_contradicting=[contra],
                                caused_by="test", rationale="minority interpretation")
    assert updated["status"] == "CONTESTED"
    assert by_predicate["entity_status"] in updated["basis"]["supporting_claim_ids"]
    kinds = {t["transition_type"] for t in ctx.store.transitions_for(theme["theme_id"])}
    assert "CONTRADICTION_ADDED" in kinds


def test_degraded_basis_weakens_then_stales_theme(tmp_path):
    pipeline, ctx = make_analytic(tmp_path)
    by_predicate = _seed_acme(pipeline, ctx)
    theme = create_theme(ctx, title="Acme registry standing",
                         supporting_claim_ids=[by_predicate["entity_status"]],
                         provenance_kind="RULE")
    state = ClaimStateRecord(
        state_id="st-1", claim_id=by_predicate["entity_status"], state="STALE",
        reason="source manifestation changed", caused_by="chg-1", superseded_by="",
        actor_id="t", actor_kind="SERVICE",
        recorded_time=ctx.now_fn(), marking=MARK)
    ctx.store.append("SEMANTIC_CLAIM_STATE_RECORDED", state,
                     recorded_time=state.recorded_time, actor="t")
    refreshed = refresh_theme(ctx, theme["theme_id"], caused_by="chg-1")
    assert refreshed["status"] == "STALE"
    assert refreshed["basis"]["degraded_claim_count"] == 1
    kinds = {t["transition_type"] for t in ctx.store.transitions_for(theme["theme_id"])}
    assert "STALE" in kinds and "WEAKENED" in kinds
    # refresh with no further change appends nothing
    again = refresh_theme(ctx, theme["theme_id"], caused_by="chg-1")
    assert again["version"] == refreshed["version"]


def test_merge_is_human_only_and_preserves_lineage(tmp_path):
    pipeline, ctx = make_analytic(tmp_path)
    by_predicate = _seed_acme(pipeline, ctx)
    left = create_theme(ctx, title="Acme standing",
                        supporting_claim_ids=[by_predicate["entity_status"]],
                        provenance_kind="RULE")
    right = create_theme(ctx, title="Acme naming",
                         supporting_claim_ids=[by_predicate["legal_name"]],
                         provenance_kind="RULE")
    propose_merge(ctx, left["theme_id"], right["theme_id"], rationale="same issue")
    with pytest.raises(ValueError, match="analyst act"):
        apply_merge(ctx, left["theme_id"], right["theme_id"],
                    actor_id="svc", actor_kind="SERVICE", rationale="x")
    survivor = apply_merge(ctx, left["theme_id"], right["theme_id"],
                           actor_id="jan", actor_kind="HUMAN",
                           rationale="one registry-standing issue")
    assert ("MERGED_FROM", right["theme_id"]) in [tuple(p) for p in survivor["lineage"]]
    absorbed = ctx.store.current_themes()[right["theme_id"]]
    assert absorbed["status"] == "MERGED"
    assert ("MERGED_INTO", left["theme_id"]) in [tuple(p) for p in absorbed["lineage"]]
    assert by_predicate["legal_name"] in survivor["basis"]["supporting_claim_ids"]
    # the absorbed theme's history is intact
    assert len(ctx.store.analytic_versions("analytic_theme", right["theme_id"])) == 2


def test_split_preserves_lineage_both_ways(tmp_path):
    pipeline, ctx = make_analytic(tmp_path)
    by_predicate = _seed_acme(pipeline, ctx)
    broad = create_theme(ctx, title="Acme affairs",
                         supporting_claim_ids=list(by_predicate.values()),
                         provenance_kind="RULE")
    parts = apply_split(
        ctx, broad["theme_id"],
        [("Acme legal standing", (by_predicate["entity_status"],)),
         ("Acme identity", (by_predicate["legal_name"],))],
        actor_id="jan", actor_kind="HUMAN", rationale="two distinct issues")
    original = ctx.store.current_themes()[broad["theme_id"]]
    assert original["status"] == "SPLIT"
    split_into = {other for kind, other in
                  (tuple(p) for p in original["lineage"]) if kind == "SPLIT_INTO"}
    assert split_into == {p["theme_id"] for p in parts}
    for part in parts:
        assert ("SPLIT_FROM", broad["theme_id"]) in [tuple(p) for p in part["lineage"]]


def test_resolution_is_human_only(tmp_path):
    pipeline, ctx = make_analytic(tmp_path)
    by_predicate = _seed_acme(pipeline, ctx)
    theme = create_theme(ctx, title="Acme standing",
                         supporting_claim_ids=[by_predicate["entity_status"]],
                         provenance_kind="RULE")
    with pytest.raises(ValueError, match="analyst act"):
        resolve_theme(ctx, theme["theme_id"], actor_id="svc", actor_kind="SERVICE",
                      note="done")
    resolved = resolve_theme(ctx, theme["theme_id"], actor_id="jan",
                             actor_kind="HUMAN", note="issue closed")
    assert resolved["status"] == "RESOLVED"
    # machine refresh does not reopen a resolved theme
    assert refresh_theme(ctx, theme["theme_id"], caused_by="x")["status"] == "RESOLVED"


def test_discovery_clusters_by_world_structure(tmp_path):
    pipeline, ctx = make_analytic(tmp_path)
    _seed_acme(pipeline, ctx)
    plant_manifestation(pipeline, source_id="gleif", native_id="lei/OTHERLEI00000000002",
                        body=GLEIF_OTHER, media_type="application/json",
                        retrieval_time="2026-08-17T14:00:00+00:00")
    pipeline.process_new_evidence()
    candidates = discover_theme_candidates(ctx.store)
    titles = [c["title"] for c in candidates]
    assert len(candidates) == 2, titles
    assert any("Acme Industri AS" in t for t in titles)
    assert any("Borg Verft AS" in t for t in titles)
    for candidate in candidates:
        assert candidate["claim_ids"]
        assert "deterministic" in candidate["method"]
    # discovery is stable: same input, same candidates
    assert candidates == discover_theme_candidates(ctx.store)


def test_explain_theme_descends_to_anchors(tmp_path):
    pipeline, ctx = make_analytic(tmp_path)
    by_predicate = _seed_acme(pipeline, ctx)
    theme = create_theme(ctx, title="Acme standing",
                         supporting_claim_ids=[by_predicate["entity_status"]],
                         provenance_kind="RULE")
    explanation = explain_theme(ctx.store, theme["theme_id"])
    assert explanation["why"]
    assert explanation["source_basis"]["independent_origin_families"] == 1
    assert "never count twice" in explanation["source_basis"]["caveat"]
    descent = explanation["descent"][0]["descent"][0]
    assert descent["anchors"][0]["kind"] == "FIELD"
    assert descent["source_id"] == "gleif"
