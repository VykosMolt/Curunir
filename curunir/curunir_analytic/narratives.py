"""Narratives: which proposition is propagating, through which manifestations
and source families, in which variants.

Reach and independence are kept apart, so a widely propagated but evidentially
narrow narrative stays visible as one. Origin is never proven; the strongest
status is EARLIEST_OBSERVED_KNOWN.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping

from argus.source_intelligence.models import digest_id
from curunir_operational.canonical import parse_time
from curunir_semantic.worldmodel import dependence_group_for

from .basis import _manifestation_index, _observation_index, _state_time, compute_basis
from .contracts import NarrativeRecord, NarrativeVariant, PropagationEdge
from .store import AnalyticStore
from .substrate import (AnalyticContext, append_version, creation_authority,
                        ensure_transition, record_transition,
                        require_accepted_candidate)

_MIN_DERIVATIVE_WORDS = 6   # a verbatim-match derivation needs distinctive text
# A manifestation with no known state time sorts last, never first.
_UNDATED_LAST = datetime.max.replace(tzinfo=timezone.utc)

# How a variant's evidence bears on the narrative basis, by relation.
_SUPPORTING_VARIANT_RELATIONS = ("VERBATIM", "PARAPHRASE", "NARROWING",
                                 "BROADENING", "CERTAINTY_SHIFT", "FRAMING_SHIFT")
_CONTRADICTING_VARIANT_RELATIONS = ("COUNTER_NARRATIVE", "POLARITY_SHIFT")


def normalize_statement(text: str) -> str:
    """Matching key for text identity: casefold, collapse whitespace, strip
    surrounding punctuation."""
    collapsed = re.sub(r"\s+", " ", text.strip().casefold())
    return collapsed.strip(" .,;:!?\"'«»„“”")


def _claim_manifestations(store: AnalyticStore, claim_ids: Iterable[str]
                          ) -> list[tuple[str, str, str]]:
    """(manifestation id, origin family, state time) per manifestation carrying
    one of these claims' observations, earliest first."""
    claims = store.current_claims()
    observations = _observation_index(store)
    manifestations = _manifestation_index(store)
    seen: dict[str, tuple[str, str, str]] = {}
    for claim_id in claim_ids:
        claim = claims.get(claim_id)
        if claim is None:
            continue
        for observation_id in claim["observation_ids"]:
            observation = observations.get(observation_id)
            if observation is None:
                continue
            manifestation = manifestations.get(observation["manifestation_id"])
            if manifestation is None:
                continue
            seen[manifestation["manifestation_id"]] = (
                manifestation["manifestation_id"],
                dependence_group_for(manifestation),
                _state_time(manifestation))
    return sorted(seen.values(),
                  key=lambda entry: (parse_time(entry[2]) if entry[2]
                                     else _UNDATED_LAST, entry[0]))


def earliest_observed(store: AnalyticStore, claim_ids: Iterable[str]) -> tuple[str, str]:
    """The earliest retained manifestation carrying the proposition.

    Earliest observed, which is not a claim about where it originated.
    """
    entries = [e for e in _claim_manifestations(store, claim_ids) if e[2]]
    if not entries:
        return "", ""
    manifestation_id, _, state_time = entries[0]
    return manifestation_id, state_time


def narrative_id_for(normalized: str) -> str:
    return digest_id("narrative", normalized)


def create_narrative(ctx: AnalyticContext, *, statement: str,
                     supporting_claim_ids: Iterable[str],
                     contradicting_claim_ids: Iterable[str] = (),
                     entity_ids: tuple[str, ...] = (),
                     provenance_kind: str = "RULE", inference_id: str = "",
                     proposal_id: str = "", caused_by: str = "") -> dict[str, Any]:
    """Create a narrative, idempotent by normalized statement."""
    store = ctx.store
    normalized = normalize_statement(statement)
    narrative_id = narrative_id_for(normalized)
    existing = store.current_narratives().get(narrative_id)
    if existing is not None:
        ensure_transition(ctx, subject_kind="analytic_narrative",
                          subject_id=narrative_id, transition_type="CREATED",
                          detail=f"narrative created: {existing['statement'][:140]!r}",
                          caused_by=caused_by or narrative_id,
                          evidence_refs=tuple(
                              existing["basis"]["supporting_claim_ids"][:10]),
                          to_status=existing["status"])
        # A model may only complete the materialization the human accepted.
        if provenance_kind == "MODEL" and (not proposal_id or proposal_id
                                       != existing.get("proposal_id")):
            raise ValueError(
                "an existing narrative cannot be modified under model provenance "
                "with a different proposal: propose and accept a new candidate")
        # Fold in evidence the caller brought that the basis lacks.
        new_supporting = [c for c in supporting_claim_ids
                          if c not in existing["basis"]["supporting_claim_ids"]]
        new_contradicting = [c for c in contradicting_claim_ids
                             if c not in existing["basis"]["contradicting_claim_ids"]]
        if provenance_kind == "MODEL" and (new_supporting or new_contradicting):
            proposal = store.latest_by_id("analytical_proposal",
                                          "proposal_id").get(proposal_id, {})
            accepted_claims = set(proposal.get("content", {})
                                  .get("supporting_claim_ids", ()))
            beyond = [c for c in new_supporting + new_contradicting
                      if c not in accepted_claims]
            if beyond:
                raise ValueError(
                    f"model fold includes {len(beyond)} claim(s) beyond the "
                    "accepted candidate's content: the human accepted specific "
                    "evidence, not an open mandate")
        if new_supporting or new_contradicting:
            basis = compute_basis(
                store,
                tuple(existing["basis"]["supporting_claim_ids"]) + tuple(new_supporting),
                tuple(existing["basis"]["contradicting_claim_ids"])
                + tuple(new_contradicting))
            folded = _reappend(ctx, existing, {"basis": basis},
                               change_reason=f"creation over an existing narrative "
                                             f"folds the new evidence (provenance "
                                             f"{provenance_kind})",
                               history_note="CREATION_FOLD")
            record_transition(ctx, subject_kind="analytic_narrative",
                              subject_id=narrative_id,
                              transition_type="EVIDENCE_UPDATED",
                              detail=f"creation fold ({provenance_kind}): "
                                     f"+{len(new_supporting)} supporting, "
                                     f"+{len(new_contradicting)} contradicting",
                              caused_by=digest_id("fold", narrative_id,
                                                  *sorted(new_supporting
                                                          + new_contradicting)),
                              evidence_refs=tuple(new_supporting[:5]
                                                  + new_contradicting[:5]))
            return folded
        return existing
    authority = creation_authority(
        store, provenance_kind=provenance_kind, inference_id=inference_id,
        proposal_id=proposal_id, target_kind="analytic_narrative",
        materialized={"statement": statement,
                      "supporting_claim_ids": tuple(supporting_claim_ids)})
    basis = compute_basis(store, supporting_claim_ids, contradicting_claim_ids)
    earliest_manifestation, earliest_time = earliest_observed(
        store, basis.supporting_claim_ids)
    record = NarrativeRecord(
        narrative_id=narrative_id, version=1,
        statement=statement, normalized_statement=normalized,
        status="ACTIVE", authority=authority,
        origin_status="EARLIEST_OBSERVED_KNOWN" if earliest_manifestation
        else "ORIGIN_UNRESOLVED",
        earliest_manifestation_id=earliest_manifestation, earliest_time=earliest_time,
        variant_ids=(), counter_narrative_ids=(), basis=basis,
        entity_ids=entity_ids,
        provenance_kind=provenance_kind, inference_id=inference_id,
        proposal_id=proposal_id if provenance_kind == "MODEL" else "",
        change_reason="",
        history=(f"CREATED:{provenance_kind}",),
        recorded_time=ctx.now_fn(), marking=ctx.marking)
    appended = append_version(ctx, record)
    ensure_transition(ctx, subject_kind="analytic_narrative", subject_id=narrative_id,
                      transition_type="CREATED",
                      detail=f"narrative created: {statement[:140]!r} "
                             f"(reach {basis.manifestation_count}, "
                             f"{basis.origin_family_count} independent famil"
                             f"{'y' if basis.origin_family_count == 1 else 'ies'})",
                      caused_by=caused_by or narrative_id,
                      evidence_refs=tuple(basis.supporting_claim_ids[:10]),
                      to_status="ACTIVE")
    return appended


def _reappend(ctx: AnalyticContext, narrative: Mapping[str, Any],
              updates: dict[str, Any], change_reason: str,
              history_note: str) -> dict[str, Any]:
    merged = {k: v for k, v in narrative.items() if k != "record_type"}
    merged.update(updates)
    merged["version"] = ctx.store.next_analytic_version(
        "analytic_narrative", narrative["narrative_id"])
    merged["change_reason"] = change_reason
    merged["history"] = tuple(narrative["history"]) + (history_note,)
    merged["recorded_time"] = ctx.now_fn()
    merged["marking"] = ctx.marking
    for key in ("variant_ids", "counter_narrative_ids", "entity_ids", "history"):
        merged[key] = tuple(merged[key])
    if isinstance(merged["basis"], Mapping):
        from .basis import basis_from_record
        merged["basis"] = basis_from_record(merged["basis"])
    record = NarrativeRecord(**merged)
    return append_version(ctx, record)


def add_variant(ctx: AnalyticContext, narrative_id: str, *, relation: str,
                statement: str, claim_ids: tuple[str, ...] = (),
                observation_ids: tuple[str, ...] = (),
                manifestation_ids: tuple[str, ...],
                language: str = "", authority: str, mechanism: str = "",
                provenance_kind: str, inference_id: str = "",
                proposal_id: str = "", caused_by: str = "") -> dict[str, Any]:
    """Record one materially distinct variant, folding its claims into the
    narrative basis so reach and independence stay complete."""
    store = ctx.store
    narrative = store.current_narratives().get(narrative_id)
    if narrative is None:
        raise ValueError(f"unknown narrative: {narrative_id}")
    # VERBATIM means text identity with the narrative's own proposition; a
    # false label would fold unrelated evidence in.
    if relation == "VERBATIM" \
            and normalize_statement(statement) != narrative["normalized_statement"]:
        raise ValueError(
            "a VERBATIM variant must state the narrative's own proposition "
            "verbatim; a materially different text is a judgment-bearing "
            "relation (PARAPHRASE/…), not text identity")
    # Every cited id must resolve: a phantom id is not evidence.
    known_claims = store.current_claims()
    known_observations = _observation_index(store)
    known_manifestations = _manifestation_index(store)
    for label, ids, known in (("claim", claim_ids, known_claims),
                              ("observation", observation_ids, known_observations),
                              ("manifestation", manifestation_ids,
                               known_manifestations)):
        phantom = [i for i in ids if i not in known]
        if phantom:
            raise ValueError(f"variant cites {label} id {phantom[0][:40]!r} that "
                             "resolves to nothing in the log: phantom ids are "
                             "not evidence")
    variant_id = digest_id("narvar", narrative_id, normalize_statement(statement), relation)
    # Always construct, so the contract invariants run on the reconcile path too.
    record = NarrativeVariant(
        variant_id=variant_id, narrative_id=narrative_id, relation=relation,
        statement=statement, claim_ids=claim_ids, observation_ids=observation_ids,
        manifestation_ids=manifestation_ids, language=language,
        authority=authority, mechanism=mechanism,
        provenance_kind=provenance_kind, inference_id=inference_id,
        recorded_time=ctx.now_fn(), marking=ctx.marking,
        proposal_id=proposal_id if provenance_kind == "MODEL" else "")
    stored = store.current_analytics("narrative_variant").get(variant_id)
    if stored is None:
        if provenance_kind == "MODEL":
            require_accepted_candidate(
                store, inference_id=inference_id, proposal_id=proposal_id,
                target_kind="narrative_variant",
                materialized={"statement": statement, "relation": relation,
                              "claim_ids": tuple(claim_ids)})
        append_version(ctx, record)
        stored = record.to_record()
    else:
        # A completion continues its own accepted materialization; the
        # consumption check is not re-entered.
        if provenance_kind == "MODEL" and (not proposal_id or proposal_id
                                           != stored.get("proposal_id")):
            raise ValueError(
                "an existing variant cannot be reconciled under model "
                "provenance with a different proposal")
        # A re-call reconciles the same variant; a revision is a new version.
        def _plain_value(value):
            return list(value) if isinstance(value, (list, tuple)) else value

        for field_name in ("claim_ids", "observation_ids", "manifestation_ids",
                           "authority", "mechanism", "language"):
            if _plain_value(stored[field_name]) \
                    != _plain_value(getattr(record, field_name)):
                raise ValueError(
                    f"variant {variant_id[:24]} already exists with different "
                    f"{field_name}: a re-call reconciles the same content; a "
                    "revision is an explicit new version")
    # The relation decides which side of the basis the claims join: reframing
    # supports, a counter or polarity flip contradicts, a shift joins neither.
    # The fold reads the stored variant, so a re-call cannot smuggle claims in.
    stored_claims = tuple(stored["claim_ids"])
    if relation in _SUPPORTING_VARIANT_RELATIONS:
        add_supporting, add_contradicting = stored_claims, ()
    elif relation in _CONTRADICTING_VARIANT_RELATIONS:
        add_supporting, add_contradicting = (), stored_claims
    else:
        add_supporting, add_contradicting = (), ()
    supporting = tuple(dict.fromkeys(
        tuple(narrative["basis"]["supporting_claim_ids"]) + tuple(add_supporting)))
    contradicting = tuple(dict.fromkeys(
        tuple(narrative["basis"]["contradicting_claim_ids"]) + tuple(add_contradicting)))
    basis = compute_basis(store, supporting, contradicting)
    # Complete version, membership and transition even if the variant landed
    # in an earlier attempt.
    updated = narrative
    if variant_id not in narrative["variant_ids"] \
            or set(supporting) != set(narrative["basis"]["supporting_claim_ids"]) \
            or set(contradicting) != set(narrative["basis"]["contradicting_claim_ids"]):
        variant_ids = tuple(narrative["variant_ids"]) \
            + ((variant_id,) if variant_id not in narrative["variant_ids"] else ())
        updated = _reappend(ctx, narrative,
                            {"variant_ids": variant_ids, "basis": basis},
                            change_reason=f"variant added ({relation}): {statement[:120]}",
                            history_note=f"VARIANT:{relation}")
    record_transition(ctx, subject_kind="analytic_narrative", subject_id=narrative_id,
                      transition_type="VARIANT_ADDED",
                      detail=f"{relation} variant: {statement[:160]!r} ({authority})",
                      # Derived, not caller-chosen: one variant, one transition.
                      caused_by=digest_id("variant-added", variant_id),
                      evidence_refs=tuple(claim_ids)[:5] or tuple(observation_ids)[:5])
    return updated


def link_counter_narrative(ctx: AnalyticContext, narrative_id: str,
                           counter_narrative_id: str, *, caused_by: str = "") -> None:
    """Link two narratives as counter-narratives; both point at each other and
    neither is demoted by the link."""
    store = ctx.store
    for this_id, other_id in ((narrative_id, counter_narrative_id),
                              (counter_narrative_id, narrative_id)):
        narrative = store.current_narratives().get(this_id)
        if narrative is None:
            raise ValueError(f"unknown narrative: {this_id}")
        if other_id in narrative["counter_narrative_ids"]:
            continue
        _reappend(ctx, narrative,
                  {"counter_narrative_ids":
                   tuple(narrative["counter_narrative_ids"]) + (other_id,)},
                  change_reason=f"counter-narrative linked: {other_id[:24]}",
                  history_note=f"COUNTER:{other_id[:18]}")
        record_transition(ctx, subject_kind="analytic_narrative", subject_id=this_id,
                          transition_type="COUNTER_NARRATIVE_LINKED",
                          detail=f"counter-narrative {other_id[:24]} linked",
                          caused_by=caused_by or f"counter:{narrative_id[:12]}",
                          evidence_refs=(other_id,))


def derive_propagation(ctx: AnalyticContext, narrative_id: str) -> list[dict[str, Any]]:
    """Derive propagation edges among the manifestations carrying a narrative.

    One origin family is SAME_ORIGIN_FAMILY; across families a long verbatim
    match in temporal order is LIKELY_DERIVATIVE; anything less is UNRESOLVED.
    """
    store = ctx.store
    narrative = store.current_narratives().get(narrative_id)
    if narrative is None:
        raise ValueError(f"unknown narrative: {narrative_id}")
    variant_claims: list[str] = []
    for variant in store.variants_for_narrative(narrative_id):
        variant_claims.extend(variant["claim_ids"])
    all_claims = tuple(narrative["basis"]["supporting_claim_ids"]) + tuple(variant_claims)
    entries = _claim_manifestations(store, all_claims)
    # A verbatim match compares the two manifestations' own propositions, not
    # the narrative's headline.
    claims = store.current_claims()
    observations = _observation_index(store)
    stated: dict[str, set[str]] = {}
    for claim_id in all_claims:
        claim = claims.get(claim_id)
        if claim is None:
            continue
        normalized_value = normalize_statement(claim["object_or_value"])
        if len(normalized_value.split()) < _MIN_DERIVATIVE_WORDS:
            continue
        for observation_id in claim["observation_ids"]:
            observation = observations.get(observation_id)
            if observation is not None:
                stated.setdefault(observation["manifestation_id"],
                                  set()).add(normalized_value)
    existing = store.current_analytics("propagation_edge")
    emitted = []
    for index, (from_id, from_family, from_time) in enumerate(entries):
        for to_id, to_family, to_time in entries[index + 1:]:
            shared = stated.get(from_id, set()) & stated.get(to_id, set())
            if from_family == to_family:
                relation, authority, mechanism = (
                    "SAME_ORIGIN_FAMILY", "DERIVED",
                    "one origin family per the dependence engine; chronology within "
                    "a family is republication, not corroboration")
            elif shared and from_time and to_time \
                    and parse_time(from_time) < parse_time(to_time):
                relation, authority = "LIKELY_DERIVATIVE", "SUPPORTED_INFERENCE"
                matched = sorted(shared)[0]
                mechanism = (
                    f"both manifestations state the proposition "
                    f"{matched[:100]!r} verbatim, across distinct origin families, "
                    f"with temporal order ({from_time[:19]} before {to_time[:19]}); "
                    "an unseen common origin remains possible — this edge asserts "
                    "likelihood, not proof")
            else:
                relation, authority, mechanism = (
                    "UNRESOLVED", "UNRESOLVED",
                    "distinct origin families without a shared verbatim proposition "
                    "of distinctive length and decidable temporal order")
            edge_id = digest_id("propedge", narrative_id, from_id, to_id)
            if edge_id in existing:
                continue
            record = PropagationEdge(
                edge_id=edge_id, narrative_id=narrative_id,
                from_manifestation_id=from_id, to_manifestation_id=to_id,
                relation=relation, mechanism=mechanism,
                from_family=from_family, to_family=to_family,
                authority=authority, basis_observation_ids=(),
                provenance_kind="RULE", inference_id="",
                recorded_time=ctx.now_fn(), marking=ctx.marking)
            emitted.append(append_version(ctx, record))
            record_transition(ctx, subject_kind="analytic_narrative",
                              subject_id=narrative_id,
                              transition_type="PROPAGATION_OBSERVED",
                              detail=f"{relation}: {from_id[:18]} → {to_id[:18]} "
                                     f"({from_family[:20]} → {to_family[:20]})",
                              caused_by=edge_id, evidence_refs=(from_id, to_id))
    return emitted


def assert_independent_adoption(ctx: AnalyticContext, narrative_id: str, *,
                                from_manifestation_id: str, to_manifestation_id: str,
                                mechanism: str, actor_id: str, actor_kind: str,
                                inference_id: str = "",
                                proposal_id: str = "") -> dict[str, Any]:
    """Assert that a family reached the proposition on its own evidence: a
    judgment, so it needs an analyst act or an accepted model candidate.
    """
    store = ctx.store
    edge_id = digest_id("propedge", narrative_id, from_manifestation_id,
                        to_manifestation_id)
    existing_edge = store.current_analytics("propagation_edge").get(edge_id)
    if existing_edge is not None and existing_edge["relation"] == "INDEPENDENT_ADOPTION":
        # Completing an earlier attempt: the consumption check would see this
        # very edge, so it is not re-entered.
        if actor_kind != "HUMAN" and (not proposal_id or proposal_id
                                      != existing_edge.get("proposal_id")):
            raise ValueError(
                "an existing adoption edge cannot be re-asserted under model "
                "provenance with a different proposal")
        record_transition(ctx, subject_kind="analytic_narrative",
                          subject_id=narrative_id,
                          transition_type="INDEPENDENT_ADOPTION_OBSERVED",
                          detail=f"independent adoption asserted by "
                                 f"{actor_id or 'model'}: {mechanism[:160]}",
                          caused_by=edge_id,
                          evidence_refs=(from_manifestation_id,
                                         to_manifestation_id))
        return existing_edge
    if actor_kind != "HUMAN":
        require_accepted_candidate(
            store, inference_id=inference_id, proposal_id=proposal_id,
            target_kind="propagation_edge",
            materialized={"narrative_id": narrative_id,
                          "from_manifestation_id": from_manifestation_id,
                          "to_manifestation_id": to_manifestation_id})
    manifestations = _manifestation_index(store)
    for manifestation_id in (from_manifestation_id, to_manifestation_id):
        if manifestation_id not in manifestations:
            raise ValueError(f"manifestation {manifestation_id[:40]!r} resolves to "
                             "nothing in the log: an adoption judgment needs both "
                             "retained manifestations")
    from_family = dependence_group_for(manifestations[from_manifestation_id])
    to_family = dependence_group_for(manifestations[to_manifestation_id])
    version = store.next_analytic_version("propagation_edge", edge_id)
    record = PropagationEdge(
        edge_id=edge_id, narrative_id=narrative_id,
        from_manifestation_id=from_manifestation_id,
        to_manifestation_id=to_manifestation_id,
        relation="INDEPENDENT_ADOPTION", mechanism=mechanism,
        from_family=from_family, to_family=to_family,
        authority="ANALYST_ASSESSMENT" if actor_kind == "HUMAN"
        else "SUPPORTED_INFERENCE",
        basis_observation_ids=(),
        provenance_kind="ANALYST" if actor_kind == "HUMAN" else "MODEL",
        inference_id=inference_id, version=version,
        recorded_time=ctx.now_fn(), marking=ctx.marking,
        # A human act consumes no candidate, so it never spends a proposal id.
        proposal_id=proposal_id if actor_kind != "HUMAN" else "")
    appended = append_version(ctx, record)
    if existing_edge is not None:
        record_transition(
            ctx, subject_kind="propagation_edge", subject_id=edge_id,
            transition_type="REVISED",
            detail=f"edge relation revised {existing_edge['relation']} → "
                   f"INDEPENDENT_ADOPTION by "
                   f"{actor_id if actor_kind == 'HUMAN' else 'accepted model candidate'}"
                   f": {mechanism[:160]}",
            caused_by=f"revise:{edge_id}:v{version}",
            evidence_refs=(from_manifestation_id, to_manifestation_id))
    record_transition(ctx, subject_kind="analytic_narrative", subject_id=narrative_id,
                      transition_type="INDEPENDENT_ADOPTION_OBSERVED",
                      detail=f"independent adoption asserted by "
                             f"{actor_id or 'model'}: {mechanism[:160]}",
                      caused_by=record.edge_id,
                      evidence_refs=(from_manifestation_id, to_manifestation_id))
    return appended


def refresh_narrative(ctx: AnalyticContext, narrative_id: str, *,
                      caused_by: str) -> dict[str, Any]:
    """Re-derive a narrative from live claim state, revising earliest-observed
    when a newly preserved manifestation is older."""
    store = ctx.store
    narrative = store.current_narratives().get(narrative_id)
    if narrative is None:
        raise ValueError(f"unknown narrative: {narrative_id}")
    if narrative["status"] in ("RESOLVED", "SUPERSEDED"):
        return narrative
    basis = compute_basis(store, narrative["basis"]["supporting_claim_ids"],
                          narrative["basis"]["contradicting_claim_ids"])
    earliest_manifestation, earliest_time = earliest_observed(
        store, basis.supporting_claim_ids)
    updates: dict[str, Any] = {}
    from .basis import basis_changed_materially
    changes = basis_changed_materially(narrative["basis"], basis)
    if changes:
        updates["basis"] = basis
    origin_revised = (earliest_manifestation
                      and earliest_manifestation != narrative["earliest_manifestation_id"])
    if origin_revised:
        updates.update(earliest_manifestation_id=earliest_manifestation,
                       earliest_time=earliest_time,
                       origin_status="EARLIEST_OBSERVED_KNOWN")
    active_support = len(basis.supporting_claim_ids) - basis.degraded_claim_count
    status = narrative["status"]
    if basis.contradicting_claim_ids:
        status = "CONTESTED"
    elif active_support <= 0:
        status = "DORMANT"
    elif narrative["status"] in ("DORMANT", "CONTESTED"):
        status = "ACTIVE"
    if status != narrative["status"]:
        updates["status"] = status
    if not updates:
        return narrative
    updated = _reappend(ctx, narrative, updates,
                        change_reason=f"refresh after {caused_by[:60]}",
                        history_note=f"REFRESHED:{narrative['status']}->{status}")
    if origin_revised:
        record_transition(
            ctx, subject_kind="analytic_narrative", subject_id=narrative_id,
            transition_type="ORIGIN_REVISED",
            detail=f"earliest observed manifestation revised to "
                   f"{earliest_manifestation[:24]} ({earliest_time[:19]}); origin "
                   f"remains earliest-OBSERVED, not proven origin",
            caused_by=caused_by, evidence_refs=(earliest_manifestation,))
    if status != narrative["status"]:
        transition_type = {"CONTESTED": "CONTESTED", "DORMANT": "DORMANT",
                           "ACTIVE": "REEMERGED"}[status]
        record_transition(ctx, subject_kind="analytic_narrative",
                          subject_id=narrative_id, transition_type=transition_type,
                          detail=f"status {narrative['status']} → {status}",
                          caused_by=caused_by,
                          from_status=narrative["status"], to_status=status)
    return updated


def explain_narrative(store: AnalyticStore, narrative_id: str) -> dict[str, Any]:
    """Reach against independence, variants, and earliest-observed with its caveat."""
    narrative = store.current_narratives().get(narrative_id)
    if narrative is None:
        return {"narrative_id": narrative_id, "status": "UNKNOWN_NARRATIVE"}
    basis = narrative["basis"]
    claims = store.current_claims()
    edges = store.propagation_for_narrative(narrative_id)
    by_relation: dict[str, int] = {}
    for edge in edges:
        by_relation[edge["relation"]] = by_relation.get(edge["relation"], 0) + 1
    return {
        "narrative_id": narrative_id,
        "statement": narrative["statement"],
        "status": narrative["status"],
        "authority": narrative["authority"],
        "propagation_reach": basis["manifestation_count"],
        "source_independence": len(basis["origin_families"]),
        "reach_vs_independence": (
            f"{basis['manifestation_count']} manifestation(s) across "
            f"{len(basis['origin_families'])} independent origin famil"
            f"{'y' if len(basis['origin_families']) == 1 else 'ies'}: "
            + ("widely propagated but evidentially narrow"
               if basis["manifestation_count"] > 2 * len(basis["origin_families"])
               else "reach and independence are proportionate")),
        "origin": {
            "status": narrative["origin_status"],
            "earliest_observed_manifestation": narrative["earliest_manifestation_id"],
            "earliest_observed_time": narrative["earliest_time"],
            "caveat": "earliest OBSERVED by Curunír; not a claim about true origin",
        },
        "variants": [
            {"relation": v["relation"], "statement": v["statement"][:160],
             "language": v["language"], "authority": v["authority"],
             "mechanism": v["mechanism"][:160]}
            for v in store.variants_for_narrative(narrative_id)],
        "counter_narratives": list(narrative["counter_narrative_ids"]),
        "propagation_edges": by_relation,
        "languages": list(basis["languages"]),
        "evidence_span": (basis["earliest_time"], basis["latest_time"]),
        "why": [claims.get(claim_id, {}).get("statement", claim_id)
                for claim_id in basis["supporting_claim_ids"]],
        "against": [claims.get(claim_id, {}).get("statement", claim_id)
                    for claim_id in basis["contradicting_claim_ids"]],
        "history": list(narrative["history"]),
    }
