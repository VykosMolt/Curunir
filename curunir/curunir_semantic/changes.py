"""Work out what a change between two manifestations of a target means.

The watch layer answers "did the bytes change". This module diffs the two
manifestations' observations, classifies each difference, names the claims it
touches and queues review items, deleting nothing. Diffing observations rather
than raw text keeps page furniture out.
"""
from __future__ import annotations

import re
from typing import Any, Iterable, Mapping

from argus.source_intelligence.models import digest_id

from curunir_operational.canonical import parse_time

from .contracts import ClaimStateRecord, ReviewItem, SemanticChangeRecord
from .store import SemanticStore
from .worldmodel import IntegrationContext, world_object_id

# Correction and retraction wording in several languages. A match only steers
# the classification; it never resolves anything on its own.
_CORRECTION_CUES = re.compile(
    r"\b(correction|corrected|corrigendum|erratum|berichtigung|korrigiert|"
    r"rectificatif|corrigé|rettifica|исправлен\w*|поправка)\b", re.IGNORECASE)
_RETRACTION_CUES = re.compile(
    r"\b(retraction|retracted|withdrawn|widerrufen|zurückgezogen|retrait|"
    r"retiré|ritirato|отозван\w*)\b", re.IGNORECASE)


def _observation_key(observation: Mapping[str, Any]) -> tuple[str, str, str, str]:
    return (observation["subject_ref"], observation["observation_type"],
            observation["attribute"], observation["object_ref"])


def classify_pairwise(prior: list[Mapping[str, Any]],
                      current: list[Mapping[str, Any]],
                      current_text: str = "") -> list[dict[str, Any]]:
    """Classify the differences between two sets of observations."""
    prior_by_key = {_observation_key(o): o for o in prior}
    current_by_key = {_observation_key(o): o for o in current}
    differences: list[dict[str, Any]] = []

    correction = bool(_CORRECTION_CUES.search(current_text))
    retraction = bool(_RETRACTION_CUES.search(current_text))

    for key in sorted(current_by_key.keys() - prior_by_key.keys()):
        observation = current_by_key[key]
        change_class = {"RELATION": "RELATION_ADDED", "EVENT": "EVENT_ADDED",
                        "PUBLICATION": "EVENT_ADDED"}.get(
            observation["observation_type"], "NEW_PROPOSITION")
        if correction and change_class == "NEW_PROPOSITION":
            change_class = "SOURCE_CORRECTION"
        differences.append({"change_class": change_class, "key": key,
                            "prior": None, "current": observation})
    for key in sorted(prior_by_key.keys() - current_by_key.keys()):
        observation = prior_by_key[key]
        change_class = "RELATION_REMOVED" if observation["observation_type"] == "RELATION" \
            else "REMOVED_PROPOSITION"
        if retraction:
            change_class = "SOURCE_RETRACTION"
        elif correction and change_class == "REMOVED_PROPOSITION":
            # a correction replaces a statement, removing the old one
            change_class = "SOURCE_CORRECTION"
        differences.append({"change_class": change_class, "key": key,
                            "prior": observation, "current": None})
    for key in sorted(prior_by_key.keys() & current_by_key.keys()):
        before, after = prior_by_key[key], current_by_key[key]
        if before["value"] == after["value"]:
            continue
        if correction:
            change_class = "SOURCE_CORRECTION"
        elif after["observation_type"] in ("ENTITY_ATTRIBUTE", "ENTITY_NAME", "ENTITY_IDENTIFIER"):
            change_class = "ENTITY_ATTRIBUTE_CHANGED"
        elif after["observation_type"] == "ROLE_SIGNAL":
            change_class = "ROLE_CHANGED"
        elif after["observation_type"] in ("EVENT", "PUBLICATION"):
            change_class = "EVENT_UPDATED"
        else:
            change_class = "VALUE_CHANGED"
        differences.append({"change_class": change_class, "key": key,
                            "prior": before, "current": after})
    if not differences:
        differences.append({"change_class": "SEMANTICALLY_UNCHANGED", "key": None,
                            "prior": None, "current": None})
    return differences


def _affected(store: SemanticStore, observations: Iterable[Mapping[str, Any] | None]
              ) -> tuple[tuple[str, ...], tuple[str, ...]]:
    object_ids: set[str] = set()
    claim_ids: set[str] = set()
    for observation in observations:
        if observation is None:
            continue
        object_ids.add(world_object_id(observation["subject_ref"]))
        for claim in store.records_of("semantic_claim"):
            if observation["observation_id"] in claim["observation_ids"] or (
                    claim["subject_ref"] == observation["subject_ref"]
                    and claim["predicate"] == observation["attribute"]):
                claim_ids.add(claim["claim_id"])
    return tuple(sorted(object_ids)), tuple(sorted(claim_ids))


_CONTENT_TRUNCATION_WARNING_PREFIXES = (
    "FIELDS_TRUNCATED", "FIELD_VALUES_TRUNCATED",
    "PDF_TEXT_DERIVATIVE_TRUNCATED")


def _current_read_truncated(store: SemanticStore,
                            manifestation: Mapping[str, Any],
                            manifestation_id: str) -> bool:
    """Whether the content itself was capped, not just its anchor map."""
    if manifestation.get("truncated"):
        return True
    document = next((record for record in store.records_of("semantic_document")
                     if record.get("manifestation_id") == manifestation_id), None)
    return bool(document and any(
        str(warning).startswith(_CONTENT_TRUNCATION_WARNING_PREFIXES)
        for warning in document.get("warnings", ())))


def interpret_change(ctx: IntegrationContext, prior_manifestation_id: str,
                     current_manifestation_id: str, *, watch_id: str = "",
                     fabric_change_id: str = "", current_text: str = "",
                     max_records: int = 50) -> list[dict[str, Any]]:
    """Interpret the difference between two manifestations of one target.

    Both must already be normalized and extracted. Records the differences,
    updates claim standing where the change warrants it, and opens review items.
    Safe to re-run.
    """
    store = ctx.store
    prior_observations = store.observations_for_manifestation(prior_manifestation_id) \
        if prior_manifestation_id else []
    current_observations = store.observations_for_manifestation(current_manifestation_id)

    current_manifestation = ctx.manifestation(current_manifestation_id)
    historical_discovery = (not prior_manifestation_id
                            and current_manifestation.get("temporal_status") == "HISTORICAL")

    if historical_discovery:
        differences = [{"change_class": "HISTORICAL_STATE_DISCOVERED", "key": None,
                        "prior": None, "current": observation}
                       for observation in current_observations] or \
                      [{"change_class": "HISTORICAL_STATE_DISCOVERED", "key": None,
                        "prior": None, "current": None}]
    else:
        differences = classify_pairwise(prior_observations, current_observations,
                                        current_text)
        if _current_read_truncated(store, current_manifestation,
                                   current_manifestation_id):
            # A truncated read cannot show that earlier content was removed or
            # changed. Additions still count: truncation can hide content but
            # cannot invent it.
            for difference in differences:
                if (difference["change_class"] in _STATE_FOR_CLASS
                        and difference.get("prior") is not None):
                    difference["change_class"] = "UNRESOLVED_CHANGE"
                    difference["truncated_current"] = True

    existing_changes = {r["change_id"]: r for r in store.records_of("semantic_change")}
    existing_change_ids = set(existing_changes)
    emitted: list[dict[str, Any]] = []
    truncated = differences[max_records:]
    if truncated:
        # Never drop differences silently: record the remainder as one
        # unresolved change naming what was not interpreted.
        differences = differences[:max_records] + [{
            "change_class": "UNRESOLVED_CHANGE", "key": None, "prior": None, "current": None,
            "truncated_count": len(truncated),
            "truncated_keys": [d["key"] for d in truncated[:20]]}]
    for difference in differences:
        now = ctx.now_fn()
        prior_obs, current_obs = difference["prior"], difference["current"]
        subject_ref = (current_obs or prior_obs or {}).get("subject_ref", "")
        attribute = (current_obs or prior_obs or {}).get("attribute", "")
        change_id = digest_id("semchange", prior_manifestation_id, current_manifestation_id,
                              difference["change_class"], subject_ref, attribute,
                              (current_obs or {}).get("observation_id", ""),
                              (prior_obs or {}).get("observation_id", ""))
        if change_id in existing_change_ids:
            # The change is recorded, but the claim standing and review item
            # that follow it may have been lost to an interruption. Finish
            # them; _propagate is safe to re-run.
            _propagate(ctx, existing_changes[change_id])
            continue
        affected_objects, affected_claims = _affected(store, (prior_obs, current_obs))
        record = SemanticChangeRecord(
            change_id=change_id,
            source_id=current_manifestation.get("source_id", ""),
            prior_manifestation_id=prior_manifestation_id,
            current_manifestation_id=current_manifestation_id,
            watch_id=watch_id, fabric_change_id=fabric_change_id,
            change_class=difference["change_class"],
            detail=_detail(difference), subject_ref=subject_ref, attribute=attribute,
            prior_value=(prior_obs or {}).get("value", "")[:500],
            current_value=(current_obs or {}).get("value", "")[:500],
            prior_observation_id=(prior_obs or {}).get("observation_id", ""),
            current_observation_id=(current_obs or {}).get("observation_id", ""),
            affected_object_ids=affected_objects,
            affected_claim_ids=affected_claims,
            recorded_time=now, marking=ctx.marking,
        )
        store.append("SEMANTIC_CHANGE_RECORDED", record, recorded_time=now, actor=ctx.actor)
        existing_change_ids.add(change_id)
        emitted.append(record.to_record())
        _propagate(ctx, record.to_record())
    return emitted


def _detail(difference: Mapping[str, Any]) -> str:
    prior_obs, current_obs = difference["prior"], difference["current"]
    if difference["change_class"] == "SEMANTICALLY_UNCHANGED":
        return "no extracted proposition differs between the two manifestations"
    if difference["change_class"] == "UNRESOLVED_CHANGE" and difference.get("truncated_count"):
        return (f"{difference['truncated_count']} further differences were not "
                f"individually interpreted (record cap); first keys: "
                f"{difference['truncated_keys']}")
    if difference["change_class"] == "UNRESOLVED_CHANGE" \
            and difference.get("truncated_current"):
        observation = current_obs or prior_obs or {}
        return (f"{observation.get('subject_ref', '')} "
                f"{observation.get('attribute', '')}: current content was "
                "truncated, so apparent removal/change is not deletion or "
                "staleness evidence")
    if prior_obs and current_obs:
        return (f"{current_obs['subject_ref']} {current_obs['attribute']}: "
                f"{prior_obs['value'][:120]!r} → {current_obs['value'][:120]!r}")
    if current_obs:
        return (f"{current_obs['subject_ref']} {current_obs['attribute']} = "
                f"{current_obs['value'][:150]!r}")
    return (f"{prior_obs['subject_ref']} {prior_obs['attribute']} no longer stated "
            f"(was {prior_obs['value'][:120]!r}); absence from the current "
            f"manifestation is not deletion evidence by itself")


_STATE_FOR_CLASS = {
    "SOURCE_CORRECTION": ("CORRECTED", "SOURCE_CORRECTED"),
    "SOURCE_RETRACTION": ("RETRACTED", "SOURCE_RETRACTED"),
    "ENTITY_ATTRIBUTE_CHANGED": ("SUPERSEDED", "MANIFESTATION_CHANGED"),
    "VALUE_CHANGED": ("STALE", "MANIFESTATION_CHANGED"),
    "ROLE_CHANGED": ("STALE", "MANIFESTATION_CHANGED"),
    "REMOVED_PROPOSITION": (None, "MANIFESTATION_CHANGED"),
    "RELATION_REMOVED": (None, "MANIFESTATION_CHANGED"),
}


def _manifestation_state_time(manifestation: Mapping[str, Any]) -> str:
    """When this state was current at the source, or "" if unknown."""
    if not manifestation:
        return ""
    if manifestation.get("temporal_status") == "HISTORICAL":
        return manifestation.get("archive_capture_time") \
            or manifestation.get("source_time") or ""
    return manifestation.get("retrieval_time") or ""


def _claim_newest_evidence_time(ctx: IntegrationContext, claim: Mapping[str, Any],
                                observations: Mapping[str, Any] | None = None) -> str:
    """The newest source-state time among the claim's observations."""
    if observations is None:
        observations = {o["observation_id"]: o
                        for o in ctx.store.records_of("semantic_observation")}
    times = []
    for observation_id in claim["observation_ids"]:
        observation = observations.get(observation_id)
        if observation is None:
            continue
        state_time = _manifestation_state_time(
            ctx.manifestation(observation["manifestation_id"]))
        if state_time:
            times.append(state_time)
    return max(times, default="")


def _propagate(ctx: IntegrationContext, change: Mapping[str, Any]) -> None:
    """Carry a classified change onto the claims it touches.

    Each affected claim gets a standing update where the change warrants one,
    plus a review item. Prior versions and states stay in the log.

    Safe to re-run, but a re-run only fills gaps: a change's verdict about a
    claim holds only while it is the newest information about that claim. A
    later change or claim version wins, so an old change cannot mark correct
    current state stale.
    """
    store = ctx.store
    mapping = _STATE_FOR_CLASS.get(change["change_class"])
    if mapping is None:
        return
    claim_state, review_kind = mapping
    now = ctx.now_fn()
    all_changes = store.records_of("semantic_change")
    change_position = next((i for i, c in enumerate(all_changes)
                            if c["change_id"] == change["change_id"]),
                           len(all_changes))
    # Read once for the whole loop: every affected claim is visited once, and
    # the appends below only touch the claim being visited.
    current_claims = store.current_claims()
    claim_states = store.claim_states()
    all_observations = {o["observation_id"]: o
                        for o in store.records_of("semantic_observation")}
    change_manifestation = ctx.manifestation(change["current_manifestation_id"])
    change_time = _manifestation_state_time(change_manifestation)
    for claim_id in change["affected_claim_ids"]:
        current = current_claims.get(claim_id)
        if current is None:
            continue
        superseded_by_later_change = any(
            claim_id in later["affected_claim_ids"]
            and later["change_class"] in _STATE_FOR_CLASS
            for later in all_changes[change_position + 1:])
        superseded_by_later_version = parse_time(current["recorded_time"]) \
            > parse_time(change["recorded_time"])
        # Compare the evidence, not just record order: a backlog interpreted
        # after the claim already moved on looks newer by record order while
        # resting on older source state.
        superseded_by_newer_evidence = False
        claim_time = _claim_newest_evidence_time(ctx, current, all_observations)
        if change_time and claim_time \
                and parse_time(claim_time) > parse_time(change_time):
            superseded_by_newer_evidence = True
        lifecycle_applicable = not superseded_by_later_change \
            and not superseded_by_later_version \
            and not superseded_by_newer_evidence
        # If the claim already carries the changed value, the version bump has
        # expressed the change and its standing is CURRENT; a stale marker would
        # misdescribe it. The review item opens either way.
        if claim_state and lifecycle_applicable \
                and current["object_or_value"] != change["current_value"]:
            state_record = claim_states.get(claim_id)
            standing = state_record["state"] if state_record else "CURRENT"
            # The machine may only move a claim out of its own bookkeeping
            # states. RETRACTED, CORRECTED, DISPUTED and SOURCE_WITHDRAWN are
            # human judgments and a re-run must never stamp over them.
            if standing in ("CURRENT", "STALE", "SUPERSEDED") \
                    and standing != claim_state:
                state = ClaimStateRecord(
                    state_id=digest_id("clstate", claim_id, claim_state, change["change_id"]),
                    claim_id=claim_id, state=claim_state,
                    reason=change["detail"][:300],
                    caused_by=change["change_id"], superseded_by="",
                    actor_id=ctx.actor, actor_kind="SERVICE",
                    recorded_time=now, marking=ctx.marking)
                store.append("SEMANTIC_CLAIM_STATE_RECORDED", state,
                             recorded_time=now, actor=ctx.actor)
        item_id = digest_id("review", claim_id, change["change_id"])
        if not any(r["item_id"] == item_id for r in store.records_of("review_item")):
            item = ReviewItem(
                item_id=item_id, kind=review_kind, subject_kind="semantic_claim",
                subject_id=claim_id, detail=change["detail"][:400],
                evidence_refs=tuple(x for x in (change["prior_observation_id"],
                                                change["current_observation_id"]) if x),
                status="OPEN", resolution_note="",
                recorded_time=now, marking=ctx.marking)
            store.append("REVIEW_ITEM_RECORDED", item, recorded_time=now, actor=ctx.actor)


def explain_change(store: SemanticStore, change: Mapping[str, Any]) -> str:
    """The alert body a person reads for one change."""
    lines = [f"{change['change_class']}: {change['detail']}"]
    lines.append(f"source: {change['source_id']}; manifestations "
                 f"{(change['prior_manifestation_id'] or '(none)')[:20]} → "
                 f"{change['current_manifestation_id'][:20]}")
    if change["affected_object_ids"]:
        lines.append(f"affected objects: {', '.join(change['affected_object_ids'][:5])}")
    for claim_id in change["affected_claim_ids"][:5]:
        claim = store.current_claims().get(claim_id)
        if claim:
            lines.append(f"affected proposition [{store.claim_state(claim_id)}]: "
                         f"{claim['statement'][:140]}")
    item_ids = {digest_id("review", claim_id, change["change_id"])
                for claim_id in change["affected_claim_ids"]}
    open_items = [r for r in store.open_review_items() if r["item_id"] in item_ids]
    if open_items:
        lines.append(f"review queue: {len(open_items)} open item(s)")
    return "\n".join(lines)
