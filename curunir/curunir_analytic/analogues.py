"""Historical analogues: structural comparison, never a forecast.

An episode is evidence-bound structured history — actors, ordered events,
setting, mechanism, constraints and outcome, each carried by claims. Retrieval
compares a situation to episodes on explicit structural dimensions rather than
on text similarity, and every analogue exposes what matched, what did not, and
its transfer risks. The record has no forecast field, so similar structure
cannot become expected outcome.
"""
from __future__ import annotations

from typing import Any

from argus.source_intelligence.models import digest_id

from .contracts import AnalogueDimension, HistoricalAnalogue, HistoricalEpisode
from .store import AnalyticStore
from .substrate import (AnalyticContext, append_version, ensure_transition,
                        record_transition)


def record_episode(ctx: AnalyticContext, *, title: str, summary: str,
                   actor_object_ids: tuple[str, ...],
                   event_ids: tuple[str, ...],
                   institutional_setting: str, mechanism: str,
                   constraints: tuple[str, ...] = (),
                   outcome: str = "", outcome_claim_ids: tuple[str, ...] = (),
                   claim_ids: tuple[str, ...],
                   valid_from: str | None = None, valid_to: str | None = None,
                   caused_by: str = "") -> dict[str, Any]:
    """Record one evidence-bound historical episode, idempotent by title."""
    store = ctx.store
    episode_id = digest_id("episode", title)
    existing = store.current_analytics("historical_episode").get(episode_id)
    if existing is not None:
        ensure_transition(ctx, subject_kind="historical_episode",
                          subject_id=episode_id, transition_type="RECORDED",
                          detail=f"episode recorded: {existing['title'][:140]}",
                          caused_by=caused_by or episode_id)
        new_claims = tuple(c for c in claim_ids
                           if c not in existing["claim_ids"])
        if new_claims:
            merged = {k: v for k, v in existing.items() if k != "record_type"}
            merged.update(
                version=store.next_analytic_version("historical_episode", episode_id),
                claim_ids=tuple(existing["claim_ids"]) + new_claims,
                change_reason="creation over an existing episode folds the "
                              "new evidence",
                history=tuple(existing["history"]) + ("CREATION_FOLD",),
                recorded_time=ctx.now_fn(), marking=ctx.marking)
            for key in ("actor_object_ids", "event_ids", "constraints",
                        "outcome_claim_ids"):
                merged[key] = tuple(merged[key])
            appended = append_version(ctx, HistoricalEpisode(**merged))
            record_transition(ctx, subject_kind="historical_episode",
                              subject_id=episode_id,
                              transition_type="EVIDENCE_UPDATED",
                              detail=f"creation fold: +{len(new_claims)} claim(s)",
                              caused_by=digest_id("fold", episode_id,
                                                  *sorted(new_claims)),
                              evidence_refs=new_claims[:5])
            return appended
        return existing
    record = HistoricalEpisode(
        episode_id=episode_id, version=1, title=title, summary=summary,
        actor_object_ids=actor_object_ids, event_ids=event_ids,
        institutional_setting=institutional_setting, mechanism=mechanism,
        constraints=constraints, outcome=outcome,
        outcome_claim_ids=outcome_claim_ids, claim_ids=claim_ids,
        valid_from=valid_from, valid_to=valid_to, change_reason="",
        history=("RECORDED",), recorded_time=ctx.now_fn(), marking=ctx.marking)
    appended = append_version(ctx, record)
    ensure_transition(ctx, subject_kind="historical_episode", subject_id=episode_id,
                      transition_type="RECORDED",
                      detail=f"episode recorded: {title[:140]}",
                      caused_by=caused_by or episode_id,
                      evidence_refs=claim_ids[:8])
    return appended


def _query_structure(store: AnalyticStore, query_kind: str,
                     query_id: str) -> dict[str, Any] | None:
    """The situation's structure: its entities, event types and claims."""
    record = store.current_analytics(query_kind).get(query_id) \
        if query_kind in ("analytic_theme", "impact_path") else None
    hypothesis = store.current_hypotheses().get(query_id) \
        if query_kind == "hypothesis" else None
    if record is None and hypothesis is None:
        return None
    activities = {a["activity_id"]: a for a in store.records_of("activity")}
    if query_kind == "analytic_theme":
        entity_ids = set(record["entity_ids"])
        event_types = [activities[e]["activity_type"] for e in record["event_ids"]
                       if e in activities]
        claim_ids = tuple(record["basis"]["supporting_claim_ids"])
    elif query_kind == "impact_path":
        entity_ids = {e["from_id"] for e in record["edges"] if e["from_kind"] == "object"} \
            | {e["to_id"] for e in record["edges"] if e["to_kind"] == "object"}
        event_types = [activities[e["from_id"]]["activity_type"]
                       for e in record["edges"]
                       if e["from_kind"] == "activity" and e["from_id"] in activities]
        claim_ids = tuple(b for e in record["edges"] for b in e["basis_ids"])
    else:
        entity_ids = set()
        event_types = []
        claim_ids = tuple(hypothesis["supporting_claim_ids"])
    # entities named by the situation's claims count toward its actor structure
    claims = store.current_claims()
    for claim_id in claim_ids:
        claim = claims.get(claim_id)
        if claim:
            entity_ids.add(claim["subject_object_id"])
            if claim["object_object_id"]:
                entity_ids.add(claim["object_object_id"])
    return {"entity_ids": entity_ids, "event_types": event_types,
            "claim_ids": claim_ids}


def retrieve_analogues(ctx: AnalyticContext, *, query_kind: str, query_id: str,
                       min_matches: int = 1) -> list[dict[str, Any]]:
    """Retrieve structural analogues for a situation from the episode corpus.

    Each analogue records its per-dimension matches with their evidence, its
    mismatches, and the transfer risks those imply. Returns a typed failure when
    the situation has no structure to compare.
    """
    store = ctx.store
    structure = _query_structure(store, query_kind, query_id)
    if structure is None:
        return [{"status": "UNKNOWN_QUERY", "query_kind": query_kind,
                 "query_id": query_id}]
    activities = {a["activity_id"]: a for a in store.records_of("activity")}
    retrieved = []
    for episode in sorted(store.current_analytics("historical_episode").values(),
                          key=lambda e: e["episode_id"]):
        matched: list[AnalogueDimension] = []
        mismatched: list[AnalogueDimension] = []

        shared_actors = structure["entity_ids"] & set(episode["actor_object_ids"])
        if shared_actors:
            matched.append(AnalogueDimension(
                dimension="ACTOR_CONFIGURATION",
                detail=f"{len(shared_actors)} shared actor(s): "
                       f"{', '.join(sorted(shared_actors))[:120]}",
                basis_ids=tuple(sorted(shared_actors))))
        else:
            mismatched.append(AnalogueDimension(
                dimension="ACTOR_CONFIGURATION",
                detail="no shared actors between the situation and the episode",
                basis_ids=()))

        episode_event_types = [activities[e]["activity_type"]
                               for e in episode["event_ids"] if e in activities]
        shared_types = set(structure["event_types"]) & set(episode_event_types)
        if shared_types:
            matched.append(AnalogueDimension(
                dimension="EVENT_TYPE",
                detail=f"shared event types: {', '.join(sorted(shared_types))}",
                basis_ids=tuple(e for e in episode["event_ids"]
                                if activities.get(e, {}).get("activity_type")
                                in shared_types)[:8]))
        elif structure["event_types"] or episode_event_types:
            mismatched.append(AnalogueDimension(
                dimension="EVENT_TYPE",
                detail=f"situation events {sorted(set(structure['event_types']))} vs "
                       f"episode events {sorted(set(episode_event_types))}",
                basis_ids=()))

        if len(shared_types) >= 2:
            # order only means something over the event types both share
            situation_seq = [t for t in structure["event_types"] if t in shared_types]
            episode_seq = [t for t in episode_event_types if t in shared_types]
            if situation_seq == episode_seq:
                matched.append(AnalogueDimension(
                    dimension="TEMPORAL_SEQUENCE",
                    detail=f"same ordered sequence: {' → '.join(situation_seq)[:140]}",
                    basis_ids=tuple(episode["event_ids"][:8])))
            else:
                mismatched.append(AnalogueDimension(
                    dimension="TEMPORAL_SEQUENCE",
                    detail="shared event types occur in a different order",
                    basis_ids=()))

        if episode["institutional_setting"]:
            mismatched.append(AnalogueDimension(
                dimension="INSTITUTIONAL_SETTING",
                detail=f"episode setting {episode['institutional_setting'][:100]!r} "
                       f"is declared for the episode only; the situation's setting "
                       f"has not been established as equivalent",
                basis_ids=()))

        if len(matched) < min_matches:
            continue
        transfer_risks = tuple(
            f"{dim.dimension} differs: {dim.detail[:120]}" for dim in mismatched
        ) + ("structural similarity is not outcome prediction; the episode's "
             "outcome must not be read as a forecast for the situation",)
        analogue_id = digest_id("analogue", query_kind, query_id,
                                episode["episode_id"])
        existing = store.current_analytics("historical_analogue").get(analogue_id)
        if existing is not None:
            retrieved.append(existing)
            continue
        record = HistoricalAnalogue(
            analogue_id=analogue_id, version=1,
            query_kind=query_kind, query_id=query_id,
            episode_id=episode["episode_id"],
            matched=tuple(matched), mismatched=tuple(mismatched),
            transfer_risks=transfer_risks,
            retrieval_method="deterministic structural comparison over shared "
                             "actors, event-type structure and sequence",
            authority="SUPPORTED_INFERENCE", status="PROPOSED",
            provenance_kind="RULE", inference_id="", change_reason="",
            history=("RETRIEVED",), recorded_time=ctx.now_fn(),
            marking=ctx.marking)
        appended = append_version(ctx, record)
        ensure_transition(ctx, subject_kind="historical_analogue",
                          subject_id=analogue_id, transition_type="RETRIEVED",
                          detail=f"analogue retrieved for {query_kind}:{query_id[:18]} "
                                 f"← episode {episode['episode_id']} "
                                 f"({len(matched)} matched, {len(mismatched)} "
                                 f"mismatched dimension(s))",
                          caused_by=f"retrieve:{query_kind}:{query_id[:18]}",
                          evidence_refs=(episode["episode_id"],))
        retrieved.append(appended)
    retrieved.sort(key=lambda a: (-len(a.get("matched", ())), a.get("analogue_id", "")))
    return retrieved


def explain_analogue(store: AnalyticStore, analogue_id: str) -> dict[str, Any]:
    analogue = store.current_analytics("historical_analogue").get(analogue_id)
    if analogue is None:
        return {"analogue_id": analogue_id, "status": "UNKNOWN_ANALOGUE"}
    episode = store.current_analytics("historical_episode").get(
        analogue["episode_id"], {})
    claims = store.current_claims()
    return {
        "analogue_id": analogue_id,
        "situation": f"{analogue['query_kind']}:{analogue['query_id']}",
        "episode": episode.get("title", analogue["episode_id"]),
        "episode_outcome": episode.get("outcome", ""),
        "outcome_caveat": "the outcome is the episode's recorded history, not a "
                          "forecast for the situation",
        "matched": [{"dimension": d["dimension"], "detail": d["detail"],
                     "evidence": list(d["basis_ids"])} for d in analogue["matched"]],
        "mismatched": [{"dimension": d["dimension"], "detail": d["detail"]}
                       for d in analogue["mismatched"]],
        "transfer_risks": list(analogue["transfer_risks"]),
        "retrieval_method": analogue["retrieval_method"],
        "authority": analogue["authority"],
        "status": analogue["status"],
        "episode_evidence": [
            claims.get(claim_id, {}).get("statement", claim_id)
            for claim_id in episode.get("claim_ids", ())[:8]],
    }
