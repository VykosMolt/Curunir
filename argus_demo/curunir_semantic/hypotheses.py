"""Competing hypotheses wired to the live world model.

Follows the V4 hypothesis discipline — a hypothesis is never born supported,
assessment history accumulates, dependence-aware evidence counting — and adds
the two things V4 lacked: event-sourced state beside the world model it
assesses, and typed discriminating observations that bridge unresolved
uncertainty back into collection.

Claim linkage is an explicit recorded act (analyst or rule); refresh derives
status from the linked claims' current versions, lifecycle states and
dependence groups without deleting any prior assessment.
"""
from __future__ import annotations

from typing import Any, Mapping

from argus.source_intelligence.models import digest_id
from curunir_operational.access import Marking

from .contracts import DiscriminatingObservation, HypothesisRecord
from .store import SemanticStore
from .worldmodel import IntegrationContext


def record_hypothesis(store: SemanticStore, *, statement: str, case_id: str,
                      assumptions: tuple[str, ...] = (), unknowns: tuple[str, ...] = (),
                      analyst_or_provider: str, now: str, actor: str,
                      marking: Marking) -> dict[str, Any]:
    """A new hypothesis starts UNRESOLVED — never born supported. A repeat
    call folds new assumptions/unknowns rather than discarding them: the
    stated assumption set is recorded epistemic state."""
    hypothesis_id = digest_id("hyp", case_id, statement)
    existing = store.current_hypotheses().get(hypothesis_id)
    if existing is not None:
        merged_assumptions = tuple(dict.fromkeys(
            tuple(existing["assumptions"]) + tuple(assumptions)))
        merged_unknowns = tuple(dict.fromkeys(
            tuple(existing["unknowns"]) + tuple(unknowns)))
        if merged_assumptions == tuple(existing["assumptions"]) \
                and merged_unknowns == tuple(existing["unknowns"]):
            return existing
        return _reappend(store, existing,
                         {"assumptions": merged_assumptions,
                          "unknowns": merged_unknowns},
                         f"ASSUMPTIONS_FOLDED:{analyst_or_provider[:40]}",
                         now=now, actor=actor, marking=marking)
    record = HypothesisRecord(
        hypothesis_id=hypothesis_id, case_id=case_id, statement=statement,
        status="UNRESOLVED", assumptions=assumptions, unknowns=unknowns,
        supporting_claim_ids=(), contradicting_claim_ids=(), unresolved_claim_ids=(),
        independent_evidence_count=0,
        source_dependence_summary="no evidence linked yet",
        discriminator_ids=(), analyst_or_provider=analyst_or_provider,
        review_state="HUMAN_REVIEW_PENDING",
        history=("CREATED_WITHOUT_FORCED_WINNER",),
        recorded_time=now, marking=marking)
    store.append("HYPOTHESIS_RECORDED", record, recorded_time=now, actor=actor)
    return record.to_record()


def _reappend(store: SemanticStore, hypothesis: Mapping[str, Any], updates: dict[str, Any],
              history_note: str, *, now: str, actor: str, marking: Marking) -> dict[str, Any]:
    merged = {**{k: v for k, v in hypothesis.items() if k != "record_type"}, **updates}
    merged["history"] = tuple(hypothesis["history"]) + (history_note,)
    merged["recorded_time"] = now
    merged["marking"] = marking
    merged["version"] = store.next_family_version("hypothesis", "hypothesis_id",
                                                  hypothesis["hypothesis_id"])
    for key in ("assumptions", "unknowns", "supporting_claim_ids", "contradicting_claim_ids",
                "unresolved_claim_ids", "discriminator_ids"):
        merged[key] = tuple(merged[key])
    record = HypothesisRecord(**merged)
    store.append("HYPOTHESIS_RECORDED", record, recorded_time=now, actor=actor)
    return record.to_record()


def link_claim(store: SemanticStore, hypothesis_id: str, claim_id: str, stance: str, *,
               rationale: str, now: str, actor: str, marking: Marking) -> dict[str, Any]:
    """Record that a claim bears on a hypothesis (supporting/contradicting/unresolved)."""
    if stance not in ("supporting", "contradicting", "unresolved"):
        raise ValueError(f"invalid stance: {stance}")
    hypothesis = store.current_hypotheses().get(hypothesis_id)
    if hypothesis is None:
        raise ValueError(f"unknown hypothesis: {hypothesis_id}")
    if claim_id not in store.current_claims():
        raise ValueError(f"unknown claim: {claim_id}")
    key = f"{stance}_claim_ids"
    if claim_id in hypothesis[key]:
        return hypothesis
    updates = {key: tuple(hypothesis[key]) + (claim_id,)}
    for other in ("supporting_claim_ids", "contradicting_claim_ids", "unresolved_claim_ids"):
        if other != key:
            updates[other] = tuple(c for c in hypothesis[other] if c != claim_id)
    return _reappend(store, hypothesis, updates,
                     f"LINKED_{stance.upper()}:{claim_id[:18]}:{rationale[:80]}",
                     now=now, actor=actor, marking=marking)


def refresh_hypothesis(ctx: IntegrationContext, hypothesis_id: str) -> dict[str, Any]:
    """Re-derive status from the linked claims' live state. History preserved."""
    store = ctx.store
    hypothesis = store.current_hypotheses().get(hypothesis_id)
    if hypothesis is None:
        raise ValueError(f"unknown hypothesis: {hypothesis_id}")
    claims = store.current_claims()

    def live(claim_ids: tuple[str, ...]) -> list[Mapping[str, Any]]:
        result = []
        for claim_id in claim_ids:
            claim = claims.get(claim_id)
            if claim is None:
                continue
            state = store.claim_state(claim_id)
            result.append({"claim": claim, "state": state})
        return result

    supporting = live(tuple(hypothesis["supporting_claim_ids"]))
    contradicting = live(tuple(hypothesis["contradicting_claim_ids"]))
    active_support = [s for s in supporting if s["state"] == "CURRENT"]
    degraded_support = [s for s in supporting
                        if s["state"] in ("DISPUTED", "RETRACTED", "CORRECTED", "STALE",
                                          "SUPERSEDED", "SOURCE_WITHDRAWN")]
    active_contradiction = [c for c in contradicting if c["state"] == "CURRENT"]

    groups: set[str] = set()
    for entry in active_support:
        groups.update(entry["claim"]["dependence_group_ids"])
    independent = len(groups)

    if active_contradiction and active_support:
        status = "DISPUTED"
    elif active_contradiction:
        status = "REJECTED" if len(active_contradiction) >= 2 else "DISPUTED"
    elif independent >= 2:
        status = "SUPPORTED"
    elif independent == 1:
        status = "WEAKLY_SUPPORTED"
    else:
        status = "UNRESOLVED"

    summary = (f"{independent} independent origin famil{'y' if independent == 1 else 'ies'} "
               f"across {len(active_support)} active supporting claim(s); "
               f"{len(degraded_support)} degraded; {len(active_contradiction)} active "
               f"contradiction(s). Dependent manifestations share one family and are "
               f"never counted twice.")
    if status == hypothesis["status"] and independent == hypothesis["independent_evidence_count"] \
            and summary == hypothesis["source_dependence_summary"]:
        return hypothesis
    refreshed = _reappend(
        store, hypothesis,
        {"status": status, "independent_evidence_count": independent,
         "source_dependence_summary": summary,
         "unresolved_claim_ids": tuple(sorted(
             set(hypothesis["unresolved_claim_ids"])
             | {s["claim"]["claim_id"] for s in degraded_support}))},
        f"REFRESHED:{hypothesis['status']}->{status}:independent={independent}",
        now=ctx.now_fn(), actor=ctx.actor, marking=ctx.marking)
    if degraded_support:
        _queue_stale_basis(ctx, refreshed, degraded_support)
    return refreshed


def _queue_stale_basis(ctx: IntegrationContext, hypothesis: Mapping[str, Any],
                       degraded_support: list[Mapping[str, Any]]) -> None:
    """A hypothesis whose supporting basis degraded queues for review."""
    from .contracts import ReviewItem
    store = ctx.store
    degraded_ids = tuple(sorted(s["claim"]["claim_id"] for s in degraded_support))
    item_id = digest_id("review-stale", hypothesis["hypothesis_id"], *degraded_ids)
    if any(r["item_id"] == item_id for r in store.records_of("review_item")):
        return
    now = ctx.now_fn()
    states = ", ".join(f"{s['claim']['claim_id'][:16]}={s['state']}" for s in degraded_support)
    item = ReviewItem(
        item_id=item_id, kind="STALE_BASIS", subject_kind="hypothesis",
        subject_id=hypothesis["hypothesis_id"],
        detail=f"supporting basis degraded ({states}); status now {hypothesis['status']}",
        evidence_refs=degraded_ids, status="OPEN", resolution_note="",
        recorded_time=now, marking=ctx.marking)
    store.append("REVIEW_ITEM_RECORDED", item, recorded_time=now, actor=ctx.actor)


def existing_basis_groups(store: SemanticStore, discriminator: Mapping[str, Any]) -> set[str]:
    """Origin families already supporting what the discriminator questions."""
    groups: set[str] = set()
    claims = store.current_claims()
    for claim_id in discriminator["claim_ids"]:
        claim = claims.get(claim_id)
        if claim:
            groups.update(claim["dependence_group_ids"])
    for hypothesis_id in discriminator["hypothesis_ids"]:
        hypothesis = store.current_hypotheses().get(hypothesis_id)
        if hypothesis:
            for claim_id in hypothesis["supporting_claim_ids"]:
                claim = claims.get(claim_id)
                if claim:
                    groups.update(claim["dependence_group_ids"])
    return groups


def refresh_hypotheses_for_claims(ctx: IntegrationContext,
                                  claim_ids: set[str]) -> list[dict[str, Any]]:
    """Propagate claim-state changes into every hypothesis touching them."""
    refreshed = []
    for hypothesis_id, hypothesis in ctx.store.current_hypotheses().items():
        touched = claim_ids & (set(hypothesis["supporting_claim_ids"])
                               | set(hypothesis["contradicting_claim_ids"])
                               | set(hypothesis["unresolved_claim_ids"]))
        if touched:
            refreshed.append(refresh_hypothesis(ctx, hypothesis_id))
    return refreshed


def propose_discriminator(store: SemanticStore, *, question: str,
                          hypothesis_ids: tuple[str, ...] = (),
                          claim_ids: tuple[str, ...] = (),
                          desired_observation_type: str, desired_subject_ref: str,
                          desired_attribute: str,
                          source_family_hints: tuple[str, ...] = (),
                          independence_required: bool = False,
                          now: str, actor: str, marking: Marking) -> dict[str, Any]:
    """What observation would most help distinguish the alternatives.

    Idempotent by (question, subject, attribute) — but the exists path FOLDS
    the caller's epistemics instead of discarding them: new hypothesis/claim
    links and hints merge in, and an independence requirement can only ever
    be raised, never silently downgraded by a cached weaker discriminator
    (the downgrade would let same-family evidence outrank and satisfy the
    very question it cannot answer). The hypothesis-link loop runs on both
    paths, so an interrupted first call completes on re-run.
    """
    discriminator_id = digest_id("disc", question, desired_subject_ref, desired_attribute)
    existing = store.latest_by_id("discriminator", "discriminator_id").get(discriminator_id)
    if existing is not None:
        merged_hypotheses = tuple(dict.fromkeys(
            tuple(existing["hypothesis_ids"]) + tuple(hypothesis_ids)))
        merged_claims = tuple(dict.fromkeys(
            tuple(existing["claim_ids"]) + tuple(claim_ids)))
        merged_hints = tuple(dict.fromkeys(
            tuple(existing["source_family_hints"]) + tuple(source_family_hints)))
        raised_independence = independence_required \
            and not existing["independence_required"]
        updates: dict = {}
        if merged_hypotheses != tuple(existing["hypothesis_ids"]):
            updates["hypothesis_ids"] = merged_hypotheses
        if merged_claims != tuple(existing["claim_ids"]):
            updates["claim_ids"] = merged_claims
        if merged_hints != tuple(existing["source_family_hints"]):
            updates["source_family_hints"] = merged_hints
        if raised_independence:
            updates["independence_required"] = True
            # the pose snapshot is taken NOW, when independence is first
            # required, over the merged linkage
            updates["basis_groups_at_pose"] = tuple(sorted(existing_basis_groups(
                store, {"claim_ids": merged_claims,
                        "hypothesis_ids": merged_hypotheses})))
        record = update_discriminator(store, existing, updates, now=now,
                                      actor=actor, marking=marking) \
            if updates else existing
    else:
        basis_snapshot = tuple(sorted(existing_basis_groups(
            store, {"claim_ids": claim_ids, "hypothesis_ids": hypothesis_ids}))) \
            if independence_required else ()
        new_record = DiscriminatingObservation(
            discriminator_id=discriminator_id, question=question,
            hypothesis_ids=hypothesis_ids, claim_ids=claim_ids,
            desired_observation_type=desired_observation_type,
            desired_subject_ref=desired_subject_ref, desired_attribute=desired_attribute,
            source_family_hints=source_family_hints,
            independence_required=independence_required,
            basis_groups_at_pose=basis_snapshot,
            requirement_id="", status="OPEN", recorded_time=now, marking=marking)
        store.append("DISCRIMINATOR_RECORDED", new_record, recorded_time=now, actor=actor)
        record = new_record.to_record()
    # the link loop runs on BOTH paths: a crash between the discriminator
    # append and the linking, or a later call bringing a new hypothesis,
    # completes here idempotently
    for hypothesis_id in record["hypothesis_ids"]:
        hypothesis = store.current_hypotheses().get(hypothesis_id)
        if hypothesis and discriminator_id not in hypothesis["discriminator_ids"]:
            _reappend(store, hypothesis,
                      {"discriminator_ids": tuple(hypothesis["discriminator_ids"]) + (discriminator_id,)},
                      f"DISCRIMINATOR_PROPOSED:{discriminator_id[:18]}",
                      now=now, actor=actor, marking=marking)
    return record


def update_discriminator(store: SemanticStore, discriminator: Mapping[str, Any],
                         updates: dict[str, Any], *, now: str, actor: str,
                         marking: Marking) -> dict[str, Any]:
    merged = {**{k: v for k, v in discriminator.items() if k != "record_type"}, **updates}
    for key in ("hypothesis_ids", "claim_ids", "source_family_hints", "basis_groups_at_pose"):
        merged[key] = tuple(merged.get(key, ()))
    merged["recorded_time"] = now
    merged["marking"] = marking
    merged["version"] = store.next_family_version(
        "discriminator", "discriminator_id", discriminator["discriminator_id"])
    record = DiscriminatingObservation(**merged)
    store.append("DISCRIMINATOR_RECORDED", record, recorded_time=now, actor=actor)
    return record.to_record()


def discriminator_satisfied_by(store: SemanticStore,
                               discriminator: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Observations answering a discriminator — only evidence recorded after
    the discriminator was posed counts; pre-existing observations are what
    made the question worth asking, not its answer. When independence was
    required, evidence from an origin family already in the basis does not
    satisfy: the ranking's judgment and the satisfaction judgment agree."""
    from .worldmodel import dependence_group_for
    first_posed = min((r["recorded_time"] for r in store.records_of("discriminator")
                       if r["discriminator_id"] == discriminator["discriminator_id"]),
                      default=discriminator["recorded_time"])
    if not discriminator["independence_required"]:
        basis_groups = set()
    else:
        # the snapshot taken when the question was posed: evidence acquired to
        # answer it cannot filter itself out by being integrated first
        basis_groups = set(discriminator.get("basis_groups_at_pose") or ()) \
            or existing_basis_groups(store, discriminator)
    manifestations = {m["manifestation_id"]: m
                      for m in store.records_of("fabric_manifestation")}
    matches = []
    for observation in store.records_of("semantic_observation"):
        if observation["recorded_time"] <= first_posed:
            continue
        if observation["observation_type"] != discriminator["desired_observation_type"]:
            continue
        if discriminator["desired_subject_ref"] and \
                observation["subject_ref"] != discriminator["desired_subject_ref"]:
            continue
        if discriminator["desired_attribute"] and \
                observation["attribute"] != discriminator["desired_attribute"]:
            continue
        if basis_groups:
            manifestation = manifestations.get(observation["manifestation_id"])
            if manifestation is None or dependence_group_for(manifestation) in basis_groups:
                continue
        matches.append(observation)
    return matches
