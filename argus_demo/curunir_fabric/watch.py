"""Persistent watch primitive: keep observing, durably.

A WatchDefinition binds an information need to one observed target (URL,
feed, query or native object) at a cadence. All watch state — definitions,
runs, change observations — is event-sourced in the FabricStore, so a
process restart loses nothing: ``due_watches`` replays the log to find what
should run next. Executing a watch reuses the same policy-gated executor and
custody path as discovery, so watch evidence joins the same lineage.

Change semantics follow the longitudinal vocabulary: byte-identical content
is no change; new/removed native records in feeds and enumerations are
NEW_OBJECT / DISAPPEARED_OBJECT; changed bytes for the same target are
CONTENT_CHANGED; retrieval failures are themselves observations.
"""
from __future__ import annotations

from datetime import datetime, timedelta

from argus.source_intelligence.models import digest_id

from .contracts import ChangeObservation, QuerySpec, WatchDefinition, WatchRun
from .executor import ExecutionContext, ExecutionResult, execute_single
from .store import FabricStore

MAX_CHANGES_PER_RUN = 25


def register_watch(store: FabricStore, definition: WatchDefinition, *, actor: str) -> WatchDefinition:
    store.append("FABRIC_WATCH_RECORDED", definition,
                 recorded_time=definition.created_time, actor=actor)
    return definition


def retire_watch(store: FabricStore, watch_record: dict, *, now: str, actor: str, marking) -> None:
    retired = WatchDefinition(
        watch_id=watch_record["watch_id"], need_id=watch_record["need_id"],
        target_kind=watch_record["target_kind"], target_ref=watch_record["target_ref"],
        source_id=watch_record["source_id"], operation=watch_record["operation"],
        query_value=watch_record["query_value"],
        cadence_seconds=watch_record["cadence_seconds"], active=False,
        blind_spots=tuple(watch_record["blind_spots"]),
        created_by=actor, created_time=now, marking=marking,
    )
    store.append("FABRIC_WATCH_RECORDED", retired, recorded_time=now, actor=actor)


def _parse(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def last_run(store: FabricStore, watch_id: str) -> dict | None:
    found = None
    for record in store.records_of("fabric_watch_run"):
        if record["watch_id"] == watch_id:
            found = record
    return found


def due_watches(store: FabricStore, *, now: str) -> list[dict]:
    """Active watches whose next observation time has arrived — pure replay."""
    current = store.latest_by_id("fabric_watch", "watch_id")
    due = []
    for watch in current.values():
        if not watch["active"]:
            continue
        previous = last_run(store, watch["watch_id"])
        next_due = previous["next_due_time"] if previous else watch["created_time"]
        if _parse(next_due) <= _parse(now):
            due.append(watch)
    return sorted(due, key=lambda item: item["watch_id"])


def _watch_query(watch: dict) -> QuerySpec:
    return QuerySpec(
        query_id=digest_id("watch-query", watch["watch_id"]),
        family="FEED_POLL" if watch["target_kind"] == "FEED" else "NATIVE_OBJECT",
        value=watch["query_value"] or watch["target_ref"],
        language="", script="", operation=watch["operation"], source_id=watch["source_id"],
        time_bounds=(None, None), origin="RULE", origin_detail="fabric-watch",
        rationale=f"scheduled observation for watch {watch['watch_id'][:16]}",
        derived_from=(watch["watch_id"],),
    )


def _diff_changes(watch: dict, run_id: str, previous: dict | None,
                  outcome: ExecutionResult, *, now: str, marking) -> list[ChangeObservation]:
    changes: list[ChangeObservation] = []
    evidence = tuple(m.manifestation_id for m in outcome.manifestations)
    manifestation_id = evidence[0] if evidence else ""

    def add(change_type: str, detail: str, prior_ref: str, current_ref: str) -> None:
        changes.append(ChangeObservation(
            change_id=digest_id("change", watch["watch_id"], run_id, change_type,
                                prior_ref, current_ref),
            watch_id=watch["watch_id"], run_id=run_id, change_type=change_type,
            detail=detail, prior_ref=prior_ref, current_ref=current_ref,
            evidence_manifestation_ids=evidence, observed_time=now, marking=marking,
        ))

    if outcome.execution.outcome in ("SOURCE_FAILED", "ACCESS_RESTRICTED", "POLICY_REFUSED"):
        prior_ref = previous["manifestation_id"] if previous else ""
        change = ChangeObservation(
            change_id=digest_id("change", watch["watch_id"], run_id, "RETRIEVAL_FAILURE"),
            watch_id=watch["watch_id"], run_id=run_id, change_type="RETRIEVAL_FAILURE",
            detail=outcome.execution.error_detail or outcome.execution.outcome,
            prior_ref=prior_ref, current_ref="", evidence_manifestation_ids=(),
            observed_time=now, marking=marking,
        )
        return [change]

    if previous is None:
        return []  # first observation is the baseline, not a change

    observed_sha = outcome.response.body_sha256() if outcome.response else ""
    previous_sha = previous["observed_content_sha256"]
    previous_records = {native_id: source_time
                        for native_id, source_time in previous["observed_records"]}
    current_records = {result.native_id: (result.source_time or "")
                       for result in outcome.results}

    if watch["target_kind"] in ("URL", "NATIVE_OBJECT"):
        if previous_sha and observed_sha and observed_sha != previous_sha:
            add("CONTENT_CHANGED",
                f"content hash changed from {previous_sha[:12]} to {observed_sha[:12]}",
                previous["manifestation_id"], manifestation_id)
        return changes

    # FEED / QUERY targets: diff the native record sets
    new_ids = [i for i in current_records if i not in previous_records]
    gone_ids = [i for i in previous_records if i not in current_records]
    historical = watch["operation"].startswith("HISTORICAL_")
    for native_id in new_ids[:MAX_CHANGES_PER_RUN]:
        add("NEW_HISTORICAL_MANIFESTATION" if historical else "NEW_OBJECT",
            f"new record {native_id}", "", native_id)
    for native_id in gone_ids[:MAX_CHANGES_PER_RUN]:
        add("DISAPPEARED_OBJECT",
            f"record {native_id} no longer returned (window shift or removal; "
            "disappearance from a feed is not deletion evidence)",
            native_id, "")
    for native_id, source_time in current_records.items():
        prior_time = previous_records.get(native_id)
        if prior_time is not None and source_time and prior_time and source_time != prior_time:
            add("CHANGED_SOURCE_RECORD",
                f"record {native_id} source time moved {prior_time} → {source_time}",
                native_id, native_id)
    return changes


def run_watch(ctx: ExecutionContext, watch: dict, *, scheduled_time: str) -> WatchRun:
    started = ctx.now_fn()
    previous = last_run(ctx.store, watch["watch_id"])
    outcome = execute_single(ctx, query=_watch_query(watch), source_id=watch["source_id"],
                             plan_id="")
    completed = ctx.now_fn()
    run_id = digest_id("watch-run", watch["watch_id"], started)
    changes = _diff_changes(watch, run_id, previous, outcome, now=completed, marking=ctx.marking)
    next_due = (_parse(completed) + timedelta(seconds=watch["cadence_seconds"])).isoformat()
    observed_sha = outcome.response.body_sha256() if outcome.response and outcome.response.raw_body else ""
    run = WatchRun(
        run_id=run_id, watch_id=watch["watch_id"], scheduled_time=scheduled_time,
        started_time=started, completed_time=completed,
        outcome=outcome.execution.outcome,
        execution_id=outcome.execution.execution_id,
        observed_content_sha256=observed_sha,
        observed_records=tuple((r.native_id, r.source_time or "") for r in outcome.results),
        manifestation_id=outcome.manifestations[0].manifestation_id if outcome.manifestations else "",
        change_observation_ids=tuple(change.change_id for change in changes),
        next_due_time=next_due, marking=ctx.marking,
    )
    ctx.store.append("FABRIC_WATCH_RUN_RECORDED", run, recorded_time=completed, actor=ctx.actor)
    for change in changes:
        ctx.store.append("FABRIC_CHANGE_OBSERVED", change, recorded_time=completed, actor=ctx.actor)
    return run


def tick(ctx: ExecutionContext, *, now: str | None = None) -> list[WatchRun]:
    """Run every due watch once. Safe to call from cron, a loop, or the CLI;
    all state needed to continue after a restart is already in the store."""
    moment = now or ctx.now_fn()
    runs = []
    for watch in due_watches(ctx.store, now=moment):
        runs.append(run_watch(ctx, watch, scheduled_time=moment))
    return runs
