"""Semantic change engine: manifestation difference → world-model difference.

The fabric's watch layer already answers "did the bytes change". This layer
answers what the change *means*: it diffs the observation sets of two
manifestations of one target, classifies each difference (value change,
proposition added/removed, relation/event added, correction, retraction,
historical state discovered, or semantically unchanged), identifies the
affected world-model objects and claims, updates claim lifecycle states, and
queues review items — without deleting or rewriting any prior state.

Chrome noise never becomes a semantic change here because the diff runs over
typed observations, not raw text: a page whose navigation changed but whose
extracted propositions are identical is SEMANTICALLY_UNCHANGED.
"""
from __future__ import annotations

import re
from typing import Any, Iterable, Mapping

from argus.source_intelligence.models import digest_id
from curunir_operational.access import inherited_marking

from .contracts import ClaimStateRecord, ReviewItem, SemanticChangeRecord
from .store import SemanticStore
from .worldmodel import IntegrationContext

# compact multilingual correction/retraction cues; a match is a signal for
# classification, never an auto-resolution
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
    """Pure classification of two observation sets into semantic differences."""
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
            # a correction that replaces a statement removes the old one
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
    from .worldmodel import world_object_id
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


def interpret_change(ctx: IntegrationContext, prior_manifestation_id: str,
                     current_manifestation_id: str, *, watch_id: str = "",
                     fabric_change_id: str = "", current_text: str = "",
                     max_records: int = 50) -> list[dict[str, Any]]:
    """Interpret the difference between two manifestations of one target.

    Both manifestations must already be normalized and extracted. Emits
    SemanticChangeRecords, updates claim lifecycle where the change class
    warrants it, and opens review items — idempotently.
    """
    store = ctx.store
    prior_observations = store.observations_for_manifestation(prior_manifestation_id) \
        if prior_manifestation_id else []
    current_observations = store.observations_for_manifestation(current_manifestation_id)

    current_manifestation = ctx.manifestation(current_manifestation_id)
    prior_manifestation = ctx.manifestation(prior_manifestation_id) \
        if prior_manifestation_id else {}
    # a change record copies the changed observation's literal value into
    # prior_value/current_value/detail; on the watch path it is written in the
    # pipeline's default (often PUBLIC) context, so it inherits the markings of
    # the manifestations it diffs — a change over a compartmented manifestation
    # can never surface its value in a record a lower context can read
    change_marking = inherited_marking(
        ctx.marking, [prior_manifestation.get("marking"),
                      current_manifestation.get("marking")])
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
        if current_manifestation.get("truncated"):
            # a byte-capped current manifestation is INCOMPLETE, so any diff that
            # concludes content was removed or changed (vs the prior full read)
            # may be a truncation artifact — the content could be in the unseen
            # tail. Reclassify every lifecycle-moving (loss/change) class to
            # UNRESOLVED_CHANGE, so a resource limit never becomes evidence of
            # deletion / retraction / staleness. Additions are trustworthy (a cut
            # can only hide content, never add it) and pass through unchanged.
            # (V6.7 §3, review finding F4.)
            for difference in differences:
                if difference["change_class"] in _STATE_FOR_CLASS:
                    difference["change_class"] = "UNRESOLVED_CHANGE"
                    difference["truncated_current"] = True

    existing_changes = {r["change_id"]: r for r in store.records_of("semantic_change")}
    existing_change_ids = set(existing_changes)
    emitted: list[dict[str, Any]] = []
    truncated = differences[max_records:]
    if truncated:
        # never silently lose differences: the remainder is recorded as one
        # explicit unresolved-change record naming what was not interpreted
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
            # the change record exists, but the propagation tail (claim
            # lifecycle + review item) may have been lost to an interruption
            # after the append — _propagate is idempotent, so completing it
            # here makes a re-run finish the flow instead of skipping it
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
            recorded_time=now, marking=change_marking,
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
    if difference["change_class"] == "UNRESOLVED_CHANGE" and difference.get("truncated_current"):
        base = (f"{current_obs['subject_ref']} {current_obs['attribute']}"
                if current_obs else
                f"{prior_obs['subject_ref']} {prior_obs['attribute']}") if (prior_obs or current_obs) else "difference"
        return (f"{base}: the current manifestation was byte-capped (truncated), "
                f"so this apparent removal/change is not interpreted as evidence "
                f"of deletion or staleness — a partial read is not the whole source")
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
    """When the manifestation's source state was current — capture time for
    archives, retrieval time for live fetches; "" when unknown."""
    if not manifestation:
        return ""
    if manifestation.get("temporal_status") == "HISTORICAL":
        return manifestation.get("archive_capture_time") \
            or manifestation.get("source_time") or ""
    return manifestation.get("retrieval_time") or ""


def _claim_newest_evidence_time(ctx: IntegrationContext,
                                claim: Mapping[str, Any]) -> str:
    """The newest source-state time among the claim's current observations."""
    store = ctx.store
    observations = {o["observation_id"]: o
                    for o in store.records_of("semantic_observation")}
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
    """Carry a classified change onto the claims it touches: lifecycle state
    plus a review item. Prior claim versions and states stay in the log.

    Re-entrant for crash recovery, but completion is GAP-DETECTION, not
    re-judgment: a change's staleness verdict about a claim stands only
    while the change is the newest information about it. A later change or
    a later claim version supersedes it — re-stamping an old change's
    verdict over a claim that has since moved on would mark correct current
    state STALE for a reason two versions old."""
    store = ctx.store
    mapping = _STATE_FOR_CLASS.get(change["change_class"])
    if mapping is None:
        return
    claim_state, review_kind = mapping
    now = ctx.now_fn()
    from curunir_operational.canonical import parse_time
    all_changes = store.records_of("semantic_change")
    change_position = next((i for i, c in enumerate(all_changes)
                            if c["change_id"] == change["change_id"]),
                           len(all_changes))
    change_marking = change.get("marking")
    for claim_id in change["affected_claim_ids"]:
        current = store.current_claims().get(claim_id)
        if current is None:
            continue
        # the claim-state and review-item are ABOUT this claim and repeat the
        # change detail (which may quote a restricted observation value): they
        # inherit the join of the context, the change, and the claim's own
        # marking, never a lower default
        record_marking = inherited_marking(
            ctx.marking, [change_marking, current.get("marking")])
        superseded_by_later_change = any(
            claim_id in later["affected_claim_ids"]
            and later["change_class"] in _STATE_FOR_CLASS
            for later in all_changes[change_position + 1:])
        superseded_by_later_version = parse_time(current["recorded_time"]) \
            > parse_time(change["recorded_time"])
        # the EVIDENCE axis, not just record order: when a backlog is
        # interpreted after integration already advanced the claim, the old
        # change's records postdate the claim version — but the claim's own
        # evidence is newer than the change's manifestation, and an old
        # source state must not stale a claim resting on newer source state
        superseded_by_newer_evidence = False
        change_manifestation = ctx.manifestation(change["current_manifestation_id"])
        change_time = _manifestation_state_time(change_manifestation)
        claim_time = _claim_newest_evidence_time(ctx, current)
        if change_time and claim_time \
                and parse_time(claim_time) > parse_time(change_time):
            superseded_by_newer_evidence = True
        lifecycle_applicable = not superseded_by_later_change \
            and not superseded_by_later_version \
            and not superseded_by_newer_evidence
        # Deliberate: when the integrator has already advanced the claim to
        # the changed value, the transition is expressed by the version bump
        # and the claim's standing is CURRENT — a stale/corrected marker would
        # misdescribe the current version. The lifecycle write below therefore
        # fires only when the claim still carries the pre-change value; the
        # review item always opens either way.
        if claim_state and lifecycle_applicable \
                and current["object_or_value"] != change["current_value"]:
            # a SERVICE actor may transition a claim's standing only OUT of
            # machine-bookkeeping states: RETRACTED, CORRECTED, DISPUTED and
            # SOURCE_WITHDRAWN carry adjudication weight, and completion
            # re-runs of this propagation must never re-stamp over a human's
            # later judgment
            if store.claim_state(claim_id) in ("CURRENT", "STALE", "SUPERSEDED") \
                    and store.claim_state(claim_id) != claim_state:
                state = ClaimStateRecord(
                    state_id=digest_id("clstate", claim_id, claim_state, change["change_id"]),
                    claim_id=claim_id, state=claim_state,
                    reason=change["detail"][:300],
                    caused_by=change["change_id"], superseded_by="",
                    actor_id=ctx.actor, actor_kind="SERVICE",
                    recorded_time=now, marking=record_marking)
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
                recorded_time=now, marking=record_marking)
            store.append("REVIEW_ITEM_RECORDED", item, recorded_time=now, actor=ctx.actor)


def explain_change(store: SemanticStore, change: Mapping[str, Any]) -> str:
    """Human-readable semantic alert body for one change record."""
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
