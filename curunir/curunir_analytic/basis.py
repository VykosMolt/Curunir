"""What actually supports an analytical object: claims resolved to their
observations, manifestations, sources and origin families.

Fifty derivative manifestations of one origin count as reach, never as
independence, and a degraded claim stays visible in the basis instead of
counting as clean support. `describe_descent` walks the same lineage one claim
at a time.
"""
from __future__ import annotations

from typing import Any, Iterable, Mapping

from curunir_operational.canonical import parse_time
from curunir_semantic.worldmodel import dependence_group_for

from .contracts import BasisSummary
from .store import AnalyticStore

DEGRADED_CLAIM_STATES = ("DISPUTED", "RETRACTED", "CORRECTED", "STALE",
                         "SUPERSEDED", "SOURCE_WITHDRAWN")


def _observation_index(store: AnalyticStore) -> dict[str, Mapping[str, Any]]:
    return {r["observation_id"]: r for r in store.records_of("semantic_observation")}


def _manifestation_index(store: AnalyticStore) -> dict[str, Mapping[str, Any]]:
    return {r["manifestation_id"]: r for r in store.records_of("fabric_manifestation")}


def _state_time(manifestation: Mapping[str, Any]) -> str:
    """When the observed source state was current: capture time for an
    archive, retrieval time for a live fetch."""
    if manifestation.get("temporal_status") == "HISTORICAL":
        return manifestation.get("archive_capture_time") \
            or manifestation.get("source_time") or ""
    return manifestation.get("retrieval_time") or ""


def compute_basis(store: AnalyticStore,
                  supporting_claim_ids: Iterable[str],
                  contradicting_claim_ids: Iterable[str] = (),
                  *, coverage_notes: tuple[str, ...] = (),
                  note: str = "") -> BasisSummary:
    """Resolve a claim set into its basis summary."""
    requested_supporting = tuple(dict.fromkeys(supporting_claim_ids))
    requested_contradicting = tuple(dict.fromkeys(contradicting_claim_ids))
    claims = store.current_claims()
    states = store.claim_states()
    # An id resolving to no claim is reported as unresolved and carries no
    # weight, so a phantom string cannot satisfy "requires a supporting claim".
    supporting = tuple(c for c in requested_supporting if c in claims)
    contradicting = tuple(c for c in requested_contradicting if c in claims)
    unresolved = tuple(c for c in requested_supporting + requested_contradicting
                       if c not in claims)
    observations = _observation_index(store)
    manifestations = _manifestation_index(store)

    observation_ids: set[str] = set()
    manifestation_ids: set[str] = set()
    source_ids: set[str] = set()
    families: set[str] = set()
    languages: set[str] = set()
    times: list[str] = []
    stated_from: list[str] = []
    stated_to: list[str] = []
    degraded = 0

    for claim_id in supporting:
        claim = claims.get(claim_id)
        if claim is None:
            continue
        if claim.get("valid_from"):
            stated_from.append(claim["valid_from"])
        if claim.get("valid_to"):
            stated_to.append(claim["valid_to"])
        if states.get(claim_id, {}).get("state", "CURRENT") in DEGRADED_CLAIM_STATES:
            degraded += 1
        for observation_id in claim["observation_ids"]:
            observation = observations.get(observation_id)
            if observation is None:
                continue
            observation_ids.add(observation_id)
            manifestation = manifestations.get(observation["manifestation_id"])
            if manifestation is None:
                continue
            manifestation_ids.add(manifestation["manifestation_id"])
            source_ids.add(manifestation["source_id"])
            families.add(dependence_group_for(manifestation))
            if observation.get("language"):
                languages.add(observation["language"])
            state_time = _state_time(manifestation)
            if state_time:
                times.append(state_time)

    default_note = (f"{len(families)} independent origin famil"
                    f"{'y' if len(families) == 1 else 'ies'} across "
                    f"{len(manifestation_ids)} manifestation(s); dependent "
                    f"manifestations share one family and never count twice; "
                    f"distinct families are non-identical, not proven independent")
    if unresolved:
        default_note += (f"; {len(unresolved)} claim id(s) resolve to no known "
                         f"claim and carry no evidentiary weight")
    return BasisSummary(
        supporting_claim_ids=supporting,
        contradicting_claim_ids=contradicting,
        observation_count=len(observation_ids),
        manifestation_count=len(manifestation_ids),
        source_count=len(source_ids),
        origin_families=tuple(sorted(families)),
        degraded_claim_count=degraded,
        languages=tuple(sorted(languages)),
        # compared as instants: an offset-bearing timestamp sorts wrongly as text
        earliest_time=min(times, key=parse_time, default=""),
        latest_time=max(times, key=parse_time, default=""),
        stated_valid_from=min(stated_from, key=parse_time, default=""),
        stated_valid_to=max(stated_to, key=parse_time, default=""),
        unresolved_claim_ids=unresolved,
        coverage_notes=coverage_notes,
        note=note or default_note,
    )


def basis_from_record(record: Mapping[str, Any]) -> BasisSummary:
    """Rebuild a BasisSummary from its replayed dict form.

    The one round-trip, so a new field cannot be dropped by a per-engine copy.
    """
    data = {k: v for k, v in record.items() if k != "record_type"}
    for key in ("supporting_claim_ids", "contradicting_claim_ids", "origin_families",
                "languages", "unresolved_claim_ids", "coverage_notes"):
        data[key] = tuple(data.get(key, ()))
    return BasisSummary(**data)


def basis_changed_materially(before: Mapping[str, Any], after: BasisSummary) -> list[str]:
    """Which basis dimensions moved, as the names the transitions use."""
    changes = []
    if set(before.get("supporting_claim_ids", ())) != set(after.supporting_claim_ids):
        changes.append("membership")
    if set(before.get("contradicting_claim_ids", ())) != set(after.contradicting_claim_ids):
        changes.append("contradiction")
    if set(before.get("origin_families", ())) != set(after.origin_families):
        changes.append("source_diversity")
    if before.get("degraded_claim_count", 0) != after.degraded_claim_count:
        changes.append("degradation")
    if before.get("observation_count", 0) != after.observation_count \
            or before.get("latest_time", "") != after.latest_time:
        changes.append("evidence")
    return changes


def describe_descent(store: AnalyticStore, claim_id: str) -> dict[str, Any]:
    """Evidence lineage of one claim: observations, anchors, manifestation, source."""
    claim = store.current_claims().get(claim_id)
    if claim is None:
        return {"claim_id": claim_id, "status": "UNKNOWN_CLAIM"}
    observations = _observation_index(store)
    manifestations = _manifestation_index(store)
    descent = []
    for observation_id in claim["observation_ids"]:
        observation = observations.get(observation_id)
        if observation is None:
            descent.append({"observation_id": observation_id, "status": "MISSING"})
            continue
        manifestation = manifestations.get(observation["manifestation_id"], {})
        descent.append({
            "observation_id": observation_id,
            "statement": f"{observation['subject_ref']} {observation['attribute']} "
                         f"= {observation['value'][:120]}",
            "anchors": [
                {"kind": anchor["kind"], "field_path": anchor.get("field_path", ""),
                 "start": anchor.get("start"), "end": anchor.get("end"),
                 "exact_value": anchor.get("exact_value", "")[:120],
                 "mapping_status": anchor.get("mapping_status", ""),
                 "content_sha256": anchor["content_sha256"]}
                for anchor in observation["anchors"]],
            "manifestation_id": observation["manifestation_id"],
            "source_id": observation["source_id"],
            "origin_family": dependence_group_for(manifestation) if manifestation else "",
            "temporal_status": manifestation.get("temporal_status", ""),
            "state_time": _state_time(manifestation) if manifestation else "",
        })
    return {"claim_id": claim_id,
            "statement": claim["statement"],
            "claim_state": store.claim_state(claim_id),
            "independent_basis_count": claim["independent_basis_count"],
            "descent": descent}
