"""Shared basis arithmetic: claims → evidence descent and dependence counts.

This is the one implementation of "what actually supports this analytical
object". It resolves supporting/contradicting claims to their observations,
manifestations, sources and origin families, so that fifty derivative
manifestations of one origin count as reach, never as independence, and so
that a degraded claim (DISPUTED/RETRACTED/STALE/...) is visible in the basis
rather than silently still counted as clean support.

`describe_descent` exposes the full programmatic lineage:
claim → observations → evidence anchors → manifestation → source.
"""
from __future__ import annotations

from typing import Any, Iterable, Mapping

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
    """When the observed source state was current — the same axis the claim
    integrator ranks on: capture time for archives, retrieval time for live."""
    if manifestation.get("temporal_status") == "HISTORICAL":
        return manifestation.get("archive_capture_time") \
            or manifestation.get("source_time") or ""
    return manifestation.get("retrieval_time") or ""


def compute_basis(store: AnalyticStore,
                  supporting_claim_ids: Iterable[str],
                  contradicting_claim_ids: Iterable[str] = (),
                  *, coverage_notes: tuple[str, ...] = (),
                  note: str = "") -> BasisSummary:
    """Resolve a claim set into its full basis summary.

    Unknown claim ids are ignored rather than fabricated; a basis computed
    over claims that no longer exist shrinks honestly.
    """
    requested_supporting = tuple(dict.fromkeys(supporting_claim_ids))
    requested_contradicting = tuple(dict.fromkeys(contradicting_claim_ids))
    claims = store.current_claims()
    # an id that resolves to no claim is not evidence: it is excluded from the
    # basis and reported as unresolved, so a phantom string can never satisfy
    # the "requires at least one supporting claim" invariant
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
        if store.claim_state(claim_id) in DEGRADED_CLAIM_STATES:
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
        earliest_time=min(times, default=""),
        latest_time=max(times, default=""),
        stated_valid_from=min(stated_from, default=""),
        stated_valid_to=max(stated_to, default=""),
        unresolved_claim_ids=unresolved,
        coverage_notes=coverage_notes,
        note=note or default_note,
    )


def basis_from_record(record: Mapping[str, Any]) -> BasisSummary:
    """Reconstruct a BasisSummary from its replayed dict form — the one
    round-trip implementation, so a field added to the record cannot be
    silently dropped by a per-engine copy."""
    data = {k: v for k, v in record.items() if k != "record_type"}
    for key in ("supporting_claim_ids", "contradicting_claim_ids", "origin_families",
                "languages", "unresolved_claim_ids", "coverage_notes"):
        data[key] = tuple(data.get(key, ()))
    return BasisSummary(**data)


def basis_changed_materially(before: Mapping[str, Any], after: BasisSummary) -> list[str]:
    """Which basis dimensions moved — drives typed theme/narrative transitions."""
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
    """Programmatic evidence lineage for one claim:
    claim → observations → anchors → manifestation → source."""
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
