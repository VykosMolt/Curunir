"""Stakeholders and influence: contextual, temporal, evidence-bound.

An assessment is always relative to a context and a period, never a permanent
label. Positions change by supersession with both states retained, and open
identity ambiguity travels with the assessment instead of being collapsed.
Discovery derives only what the world model states — formal roles and
ownership from ACTIVE typed relations; inferred influence and interests enter
as judgments with their author on record.
"""
from __future__ import annotations

from typing import Any, Iterable, Mapping

from argus.source_intelligence.models import digest_id

from .basis import compute_basis
from .contracts import InfluenceAssertion, StakeholderAssessment, StakeholderPosition
from .store import AnalyticStore
from .substrate import (AnalyticContext, append_version, creation_authority,
                        ensure_transition, open_identity_caveats,
                        record_transition, require_accepted_candidate)

# relation type -> how the derived role reads
_ROLE_RELATIONS = {
    "HOLDS_ROLE": "holds a stated role at",
    "OWNS": "owns",
    "OPERATES": "operates",
    "PUBLISHED_BY": "is published by",
    "SUCCESSOR_OF": "is successor of",
}


def assessment_id_for(entity_object_id: str, context_kind: str, context_id: str) -> str:
    return digest_id("stake", entity_object_id, context_kind, context_id)


def _entity_label(store: AnalyticStore, object_id: str,
                  latest_objects: Mapping[str, Mapping[str, Any]] | None = None) -> str:
    if latest_objects is None:
        latest_objects = store.latest_by_id("object_version", "object_id")
    latest = latest_objects.get(object_id)
    if latest is None:
        return object_id[:24]
    return latest.get("attributes", {}).get("name") \
        or (latest.get("labels") or (object_id[:24],))[0]


def _position_claims(positions: Iterable[Mapping[str, Any] | StakeholderPosition]
                     ) -> tuple[str, ...]:
    claims: list[str] = []
    for position in positions:
        record = position.to_record() if isinstance(position, StakeholderPosition) else position
        claims.extend(record["claim_ids"])
    return tuple(dict.fromkeys(claims))


def create_assessment(ctx: AnalyticContext, *, entity_object_id: str,
                      context_kind: str, context_id: str, role_in_context: str,
                      positions: tuple[StakeholderPosition, ...] = (),
                      supporting_claim_ids: Iterable[str] = (),
                      provenance_kind: str = "ANALYST", inference_id: str = "",
                      proposal_id: str = "", caused_by: str = "") -> dict[str, Any]:
    """Create a stakeholder assessment, idempotent per entity and context."""
    store = ctx.store
    assessment_id = assessment_id_for(entity_object_id, context_kind, context_id)
    existing = store.current_stakeholder_assessments().get(assessment_id)
    if existing is not None:
        ensure_transition(ctx, subject_kind="stakeholder_assessment",
                          subject_id=assessment_id, transition_type="CREATED",
                          detail=f"{existing['entity_label']} assessed as stakeholder "
                                 f"in {context_kind}:{context_id[:24]}",
                          caused_by=caused_by or assessment_id,
                          to_status=existing["status"])
        # model provenance may only complete the materialization the human
        # accepted, never modify an object under another or absent proposal
        if provenance_kind == "MODEL" and (not proposal_id or proposal_id
                                       != existing.get("proposal_id")):
            raise ValueError(
                "an existing assessment cannot be modified under model "
                "provenance with a different proposal: propose and accept a "
                "new candidate")
        # an ambiguity opened since creation must not stay invisible, and the
        # caller's evidence and positions are folded in rather than discarded
        refreshed = refresh_assessment(ctx, assessment_id,
                                       caused_by=caused_by or assessment_id)
        known_position_ids = {_position_record(p)["position_id"]
                              for p in refreshed["positions"]}
        new_positions = [p for p in positions
                         if p.position_id not in known_position_ids]
        new_claims = [c for c in supporting_claim_ids
                      if c not in refreshed["basis"]["supporting_claim_ids"]]
        if provenance_kind == "MODEL" and (new_positions or new_claims):
            proposal = store.latest_by_id("analytical_proposal",
                                          "proposal_id").get(proposal_id, {})
            accepted_claims = set(proposal.get("content", {}).get("claims", ()))
            if new_positions or any(c not in accepted_claims for c in new_claims):
                raise ValueError(
                    "model fold on an assessment may only complete the accepted "
                    "candidate's own claims; positions and further evidence "
                    "require a new accepted candidate or an analyst act")
        for position in new_positions:
            refreshed = add_position(ctx, assessment_id, position,
                                     caused_by=caused_by or assessment_id,
                                     rationale=f"creation fold "
                                               f"({provenance_kind})")
        if new_claims:
            folded = compute_basis(
                store, tuple(refreshed["basis"]["supporting_claim_ids"])
                + tuple(new_claims),
                refreshed["basis"]["contradicting_claim_ids"])
            refreshed = _reappend(ctx, refreshed, {"basis": folded},
                                  change_reason=f"creation over an existing "
                                                f"assessment folds the new "
                                                f"evidence ({provenance_kind})",
                                  history_note="CREATION_FOLD")
            record_transition(ctx, subject_kind="stakeholder_assessment",
                              subject_id=assessment_id,
                              transition_type="EVIDENCE_UPDATED",
                              detail=f"creation fold ({provenance_kind}): "
                                     f"+{len(new_claims)} claim(s)",
                              caused_by=digest_id("fold", assessment_id,
                                                  *sorted(new_claims)),
                              evidence_refs=tuple(new_claims[:5]))
        return refreshed
    if provenance_kind == "MODEL" and positions:
        raise ValueError(
            "a model-materialized assessment carries no positions: positions "
            "are judgments entered by an analyst or through their own accepted "
            "candidates — creation is exactly as strict as the fold")
    authority = creation_authority(
        store, provenance_kind=provenance_kind, inference_id=inference_id,
        proposal_id=proposal_id, target_kind="stakeholder_assessment",
        materialized={"entity_object_id": entity_object_id,
                      "context_kind": context_kind,
                      "context_id": context_id,
                      "role_in_context": role_in_context,
                      "claims": tuple(supporting_claim_ids)})
    # Creation runs the same evidence guard as add_position, and each position
    # is checked against all the others, so ordering cannot slip a position
    # past a check that the reverse order refuses.
    for index, position in enumerate(positions):
        others = positions[:index] + positions[index + 1:]
        _check_public_position_evidence(
            store, {"entity_object_id": entity_object_id,
                    "positions": tuple(others)}, position)
    claims = tuple(dict.fromkeys(tuple(supporting_claim_ids)
                                 + _position_claims(positions)))
    basis = compute_basis(store, claims)
    caveats = open_identity_caveats(store, entity_object_id)
    record = StakeholderAssessment(
        assessment_id=assessment_id, version=1,
        entity_object_id=entity_object_id,
        entity_label=_entity_label(store, entity_object_id),
        context_kind=context_kind, context_id=context_id,
        role_in_context=role_in_context,
        positions=positions, influence_ids=(),
        identity_caveats=caveats, basis=basis,
        status="ACTIVE", authority=authority,
        provenance_kind=provenance_kind, inference_id=inference_id,
        proposal_id=proposal_id if provenance_kind == "MODEL" else "",
        change_reason="",
        history=(f"CREATED:{provenance_kind}",),
        recorded_time=ctx.now_fn(), marking=ctx.marking)
    appended = append_version(ctx, record)
    ensure_transition(ctx, subject_kind="stakeholder_assessment",
                      subject_id=assessment_id, transition_type="CREATED",
                      detail=f"{record.entity_label} assessed as stakeholder in "
                             f"{context_kind}:{context_id[:24]} ({role_in_context[:80]})"
                             + (f"; {len(caveats)} open identity caveat(s)" if caveats else ""),
                      caused_by=caused_by or assessment_id,
                      evidence_refs=claims[:10], to_status="ACTIVE")
    return appended


def _reappend(ctx: AnalyticContext, assessment: Mapping[str, Any],
              updates: dict[str, Any], change_reason: str,
              history_note: str) -> dict[str, Any]:
    merged = {k: v for k, v in assessment.items() if k != "record_type"}
    merged.update(updates)
    merged["version"] = ctx.store.next_analytic_version(
        "stakeholder_assessment", assessment["assessment_id"])
    merged["change_reason"] = change_reason
    merged["history"] = tuple(assessment["history"]) + (history_note,)
    merged["recorded_time"] = ctx.now_fn()
    merged["marking"] = ctx.marking
    for key in ("influence_ids", "identity_caveats", "history"):
        merged[key] = tuple(merged[key])
    positions = []
    for position in merged["positions"]:
        if isinstance(position, StakeholderPosition):
            positions.append(position)
        else:
            data = {k: v for k, v in position.items() if k != "record_type"}
            for key in ("claim_ids", "relationship_ids"):
                data[key] = tuple(data[key])
            positions.append(StakeholderPosition(**data))
    merged["positions"] = tuple(positions)
    if isinstance(merged["basis"], Mapping):
        from .basis import basis_from_record
        merged["basis"] = basis_from_record(merged["basis"])
    record = StakeholderAssessment(**merged)
    return append_version(ctx, record)


def _position_record(position: Mapping[str, Any] | StakeholderPosition) -> Mapping[str, Any]:
    return position.to_record() if isinstance(position, StakeholderPosition) else position


# relation types that convey publication control, and which end must be the
# entity for it to count
_ATTRIBUTION_RELATIONS = {
    "OPERATES": "source",       # entity OPERATES the site/channel
    "OWNS": "source",           # entity OWNS the outlet
    "PUBLISHED_BY": "target",   # the work is PUBLISHED_BY the entity
}


def _attribution_linked(store: AnalyticStore, entity: str
                        ) -> tuple[set[str], set[str]]:
    """(linked object ids, linked hosts) the entity has publication control over.

    Only the latest ACTIVE version of a publication-control relation counts, and
    only in the controlling direction. A host counts only through a site-root
    reference: one deep page on a shared host does not make every utterance
    there the entity's own.
    """
    from curunir_semantic.worldmodel import normalize_url_key
    linked: set[str] = {entity}
    for relation in store.latest_by_id("relationship_version",
                                       "relationship_id").values():
        if relation["status"] != "ACTIVE":
            continue
        controlling_end = _ATTRIBUTION_RELATIONS.get(relation["relation_type"])
        if controlling_end == "source" and relation["source_object_id"] == entity:
            linked.add(relation["target_object_id"])
        elif controlling_end == "target" and relation["target_object_id"] == entity:
            linked.add(relation["source_object_id"])
    linked_hosts: set[str] = set()
    for object_id, version in store.latest_by_id("object_version",
                                                 "object_id").items():
        if object_id not in linked or object_id == entity:
            continue
        for ref in version.get("external_refs", ()):
            if ref.get("system") == "DOMAIN":
                linked_hosts.add(normalize_url_key(ref["external_id"]).split("/")[0])
            elif ref.get("system") == "URL":
                key = normalize_url_key(ref["external_id"])
                host, _, path = key.partition("/")
                if path in ("", "/"):
                    linked_hosts.add(host)
    return linked, linked_hosts


def _check_public_position_evidence(store: AnalyticStore,
                                    assessment: Mapping[str, Any],
                                    position: StakeholderPosition) -> None:
    """Check a position's evidence.

    An OBSERVED public position needs a statement attributable to the entity,
    and no position may be minted by relabelling an inferred interest's claims.
    """
    # every cited id must resolve in the log, or "requires evidence" would be
    # satisfied with nothing behind it
    claims = store.current_claims()
    known_relationships = {r["relationship_id"]
                           for r in store.records_of("relationship_version")}
    phantom_claims = [c for c in position.claim_ids if c not in claims]
    phantom_relations = [r for r in position.relationship_ids
                         if r not in known_relationships]
    if phantom_claims or phantom_relations:
        raise ValueError(
            f"position {position.position_id[:18]} cites evidence ids that "
            f"resolve to nothing in the log "
            f"({(phantom_claims + phantom_relations)[0][:40]!r}): phantom ids "
            "are not evidence")
    if position.kind != "PUBLIC_POSITION":
        return
    resolved = [claims[c] for c in position.claim_ids if c in claims]
    if not resolved:
        raise ValueError("a public position requires claims that resolve in the log")
    interest_claims: set[str] = set()
    for other in assessment["positions"]:
        other = _position_record(other)
        if other["kind"] == "INFERRED_INTEREST":
            interest_claims.update(other["claim_ids"])
    if position.authority == "OBSERVED":
        from curunir_semantic.worldmodel import normalize_url_key, parse_subject
        entity = assessment["entity_object_id"]
        linked, linked_hosts = _attribution_linked(store, entity)

        def _attributable(claim: Mapping[str, Any]) -> bool:
            if claim["subject_object_id"] in linked:
                return True
            scheme, value = parse_subject(claim["subject_ref"])
            if scheme in ("URL", "DOMAIN"):
                return normalize_url_key(value).split("/")[0] in linked_hosts
            return False

        # at least one attributable claim outside every inferred interest's
        # basis, so filler cannot mint an observed public position
        if not any(_attributable(claim)
                   and claim["claim_id"] not in interest_claims
                   for claim in resolved):
            raise ValueError(
                "an OBSERVED public position requires at least one attributable "
                "claim (subject is the assessed entity, or a site the entity "
                "controls per the world model) beyond any inferred interest's "
                "own basis; otherwise the attribution is itself a judgment — "
                "record it as ANALYST_ASSESSMENT, or collect a primary statement")
    else:
        if interest_claims and set(position.claim_ids) <= interest_claims:
            raise ValueError(
                "a public position cannot be manufactured from an inferred "
                "interest's own claim set: the evidence only ever supported the "
                "inference — collect a primary statement instead")


def add_position(ctx: AnalyticContext, assessment_id: str,
                 position: StakeholderPosition, *, caused_by: str,
                 rationale: str = "") -> dict[str, Any]:
    """Add a position as a new assessment version; existing positions stay."""
    store = ctx.store
    assessment = store.current_stakeholder_assessments().get(assessment_id)
    if assessment is None:
        raise ValueError(f"unknown assessment: {assessment_id}")
    _check_public_position_evidence(store, assessment, position)
    if any((p["position_id"] if isinstance(p, Mapping) else p.position_id)
           == position.position_id for p in assessment["positions"]):
        return assessment
    positions = tuple(assessment["positions"]) + (position,)
    claims = tuple(dict.fromkeys(
        tuple(assessment["basis"]["supporting_claim_ids"]) + tuple(position.claim_ids)))
    updated = _reappend(ctx, assessment,
                        {"positions": positions, "basis": compute_basis(store, claims)},
                        change_reason=rationale or f"position added: {position.kind}",
                        history_note=f"POSITION:{position.kind}")
    transition_type = "INTEREST_INFERRED" if position.kind == "INFERRED_INTEREST" \
        else "POSITION_ADDED"
    record_transition(ctx, subject_kind="stakeholder_assessment",
                      subject_id=assessment_id, transition_type=transition_type,
                      detail=f"{position.kind} ({position.authority}): "
                             f"{position.statement[:160]}",
                      caused_by=caused_by,
                      evidence_refs=tuple(position.claim_ids)[:5]
                      or tuple(position.relationship_ids)[:5])
    return updated


def supersede_position(ctx: AnalyticContext, assessment_id: str, position_id: str,
                       replacement: StakeholderPosition, *, caused_by: str,
                       rationale: str) -> dict[str, Any]:
    """Replace a position: the old one is closed and the new one appended.

    "A supported X" is never overwritten by "A opposes X" — both remain,
    bounded in time.
    """
    store = ctx.store
    assessment = store.current_stakeholder_assessments().get(assessment_id)
    if assessment is None:
        raise ValueError(f"unknown assessment: {assessment_id}")
    _check_public_position_evidence(store, assessment, replacement)
    now = ctx.now_fn()
    positions: list[StakeholderPosition] = []
    found = False
    for position in assessment["positions"]:
        data = position if isinstance(position, Mapping) else position.to_record()
        data = {k: v for k, v in data.items() if k != "record_type"}
        for key in ("claim_ids", "relationship_ids"):
            data[key] = tuple(data[key])
        if data["position_id"] == position_id:
            found = True
            data["superseded"] = True
            data["valid_to"] = data["valid_to"] or now
        positions.append(StakeholderPosition(**data))
    if not found:
        raise ValueError(f"unknown position: {position_id}")
    positions.append(replacement)
    claims = tuple(dict.fromkeys(
        tuple(assessment["basis"]["supporting_claim_ids"])
        + tuple(replacement.claim_ids)))
    updated = _reappend(ctx, assessment,
                        {"positions": tuple(positions),
                         "basis": compute_basis(store, claims)},
                        change_reason=rationale,
                        history_note=f"POSITION_CHANGED:{position_id[:18]}")
    record_transition(ctx, subject_kind="stakeholder_assessment",
                      subject_id=assessment_id, transition_type="POSITION_CHANGED",
                      detail=f"position superseded: {rationale[:200]}",
                      caused_by=caused_by,
                      evidence_refs=tuple(replacement.claim_ids)[:5])
    return updated


def refresh_assessment(ctx: AnalyticContext, assessment_id: str, *,
                       caused_by: str) -> dict[str, Any]:
    """Re-derive an assessment's basis and identity caveats from live state."""
    store = ctx.store
    assessment = store.current_stakeholder_assessments().get(assessment_id)
    if assessment is None:
        raise ValueError(f"unknown assessment: {assessment_id}")
    if assessment["status"] != "ACTIVE":
        return assessment
    basis = compute_basis(store, assessment["basis"]["supporting_claim_ids"],
                          assessment["basis"]["contradicting_claim_ids"])
    caveats = open_identity_caveats(store, assessment["entity_object_id"])
    from .basis import basis_changed_materially
    changes = basis_changed_materially(assessment["basis"], basis)
    caveats_changed = set(caveats) != set(assessment["identity_caveats"])
    if not changes and not caveats_changed:
        return assessment
    updated = _reappend(ctx, assessment,
                        {"basis": basis, "identity_caveats": caveats},
                        change_reason=f"refresh after {caused_by[:60]}",
                        history_note="REFRESHED")
    if "degradation" in changes:
        record_transition(ctx, subject_kind="stakeholder_assessment",
                          subject_id=assessment_id, transition_type="BASIS_DEGRADED",
                          detail=f"{basis.degraded_claim_count} of "
                                 f"{len(basis.supporting_claim_ids)} basis claims no "
                                 f"longer CURRENT",
                          caused_by=caused_by)
    if caveats_changed:
        record_transition(ctx, subject_kind="stakeholder_assessment",
                          subject_id=assessment_id,
                          transition_type="IDENTITY_CAVEAT_CHANGED",
                          detail=f"open identity ambiguities now: {len(caveats)}",
                          caused_by=caused_by, evidence_refs=caveats[:5])
    return updated


def link_influence(ctx: AnalyticContext, assessment_id: str, influence_id: str,
                   *, caused_by: str = "") -> dict[str, Any]:
    store = ctx.store
    assessment = store.current_stakeholder_assessments().get(assessment_id)
    if assessment is None:
        raise ValueError(f"unknown assessment: {assessment_id}")
    if influence_id in assessment["influence_ids"]:
        return assessment
    return _reappend(ctx, assessment,
                     {"influence_ids": tuple(assessment["influence_ids"]) + (influence_id,)},
                     change_reason=f"influence linked: {influence_id[:24]}",
                     history_note=f"INFLUENCE:{influence_id[:18]}")


# ---- influence ------------------------------------------------------------


def assert_influence(ctx: AnalyticContext, *, source_object_id: str,
                     target_object_id: str, kind: str, mechanism: str,
                     authority: str, claim_ids: tuple[str, ...] = (),
                     relationship_ids: tuple[str, ...] = (),
                     valid_from: str | None = None,
                     provenance_kind: str = "ANALYST", inference_id: str = "",
                     proposal_id: str = "", caused_by: str = "") -> dict[str, Any]:
    """Assert one typed influence relation, idempotent per pair and kind."""
    store = ctx.store
    influence_id = digest_id("infl", source_object_id, target_object_id, kind)
    existing = store.current_influence_assertions().get(influence_id)
    if existing is not None and existing["status"] == "ACTIVE":
        # a MODEL re-call may only complete its own accepted materialization;
        # the gate is not re-entered, or recovery would be impossible
        if provenance_kind == "MODEL" and (not proposal_id or proposal_id
                                           != existing.get("proposal_id")):
            raise ValueError(
                "an existing influence assertion cannot be modified under model "
                "provenance with a different proposal")
        ensure_transition(ctx, subject_kind="influence_assertion",
                          subject_id=influence_id, transition_type="ASSERTED",
                          detail=f"{existing['kind']} ({existing['authority']}): "
                                 f"{existing['mechanism'][:180]}",
                          caused_by=caused_by or influence_id, to_status="ACTIVE")
        return existing
    if provenance_kind == "MODEL":
        require_accepted_candidate(
            store, inference_id=inference_id, proposal_id=proposal_id,
            target_kind="influence_assertion",
            materialized={"source_object_id": source_object_id,
                          "target_object_id": target_object_id, "kind": kind})
    version = store.next_analytic_version("influence_assertion", influence_id)
    prior_history = tuple(existing["history"]) if existing is not None else ()
    record = InfluenceAssertion(
        influence_id=influence_id, version=version,
        source_object_id=source_object_id, target_object_id=target_object_id,
        kind=kind, mechanism=mechanism, authority=authority,
        claim_ids=claim_ids, relationship_ids=relationship_ids,
        valid_from=valid_from, valid_to=None, status="ACTIVE",
        provenance_kind=provenance_kind, inference_id=inference_id,
        proposal_id=proposal_id if provenance_kind == "MODEL" else "",
        change_reason="reasserted" if version > 1 else "",
        history=prior_history + ((f"ASSERTED:{provenance_kind}",) if version == 1
                                 else (f"REASSERTED:v{version}",)),
        recorded_time=ctx.now_fn(), marking=ctx.marking)
    appended = append_version(ctx, record)
    if version == 1:
        ensure_transition(ctx, subject_kind="influence_assertion",
                          subject_id=influence_id, transition_type="ASSERTED",
                          detail=f"{kind} ({authority}): {mechanism[:180]}",
                          caused_by=caused_by or influence_id,
                          evidence_refs=claim_ids[:5] or relationship_ids[:5],
                          to_status="ACTIVE")
    else:
        # a re-assertion after supersession is its own transition, not deduped
        # against the original
        record_transition(ctx, subject_kind="influence_assertion",
                          subject_id=influence_id, transition_type="ASSERTED",
                          detail=f"reasserted at v{version}: {kind} ({authority}): "
                                 f"{mechanism[:160]}",
                          caused_by=f"reassert:{influence_id}:v{version}",
                          evidence_refs=claim_ids[:5] or relationship_ids[:5],
                          from_status=existing["status"] if existing else "",
                          to_status="ACTIVE")
    return appended


def supersede_influence(ctx: AnalyticContext, influence_id: str, *, reason: str,
                        caused_by: str, valid_to: str | None = None) -> dict[str, Any]:
    """Close an influence relation as a new version; the history stays."""
    store = ctx.store
    existing = store.current_influence_assertions().get(influence_id)
    if existing is None:
        raise ValueError(f"unknown influence assertion: {influence_id}")
    if existing["status"] != "ACTIVE":
        # already closed: complete a missing transition, append nothing
        record_transition(ctx, subject_kind="influence_assertion",
                          subject_id=influence_id, transition_type="SUPERSEDED",
                          detail=reason[:300], caused_by=caused_by,
                          from_status="ACTIVE", to_status=existing["status"])
        return existing
    merged = {k: v for k, v in existing.items() if k != "record_type"}
    merged.update(
        version=store.next_analytic_version("influence_assertion", influence_id),
        status="SUPERSEDED", valid_to=valid_to or ctx.now_fn(),
        change_reason=reason,
        history=tuple(existing["history"]) + (f"SUPERSEDED:{reason[:60]}",),
        recorded_time=ctx.now_fn(), marking=ctx.marking)
    for key in ("claim_ids", "relationship_ids"):
        merged[key] = tuple(merged[key])
    record = InfluenceAssertion(**merged)
    appended = append_version(ctx, record)
    record_transition(ctx, subject_kind="influence_assertion", subject_id=influence_id,
                      transition_type="SUPERSEDED", detail=reason[:300],
                      caused_by=caused_by,
                      from_status="ACTIVE", to_status="SUPERSEDED")
    return appended


def refresh_identity_caveats(ctx: AnalyticContext, *, caused_by: str
                             ) -> list[dict[str, Any]]:
    """Bring every active assessment's identity caveats up to date.

    Ambiguity opens at world-model integration and produces no semantic change,
    so nothing else would notice it.
    """
    store = ctx.store
    refreshed = []
    for assessment in list(store.current_stakeholder_assessments().values()):
        if assessment["status"] != "ACTIVE":
            continue
        caveats = open_identity_caveats(store, assessment["entity_object_id"])
        if set(caveats) == set(assessment["identity_caveats"]):
            continue
        updated = _reappend(ctx, assessment, {"identity_caveats": caveats},
                            change_reason=f"identity review queue changed "
                                          f"({len(caveats)} open)",
                            history_note="IDENTITY_CAVEATS")
        record_transition(ctx, subject_kind="stakeholder_assessment",
                          subject_id=assessment["assessment_id"],
                          transition_type="IDENTITY_CAVEAT_CHANGED",
                          detail=f"open identity ambiguities now: {len(caveats)}",
                          caused_by=f"{caused_by}:{assessment['assessment_id'][:12]}",
                          evidence_refs=caveats[:5])
        refreshed.append(updated)
    return refreshed


# ---- deterministic discovery ----------------------------------------------


def discover_stakeholders(ctx: AnalyticContext, *, context_kind: str, context_id: str,
                          relevant_object_ids: Iterable[str],
                          caused_by: str = "") -> list[dict[str, Any]]:
    """Derive stakeholder assessments from what the world model states.

    Entities holding ACTIVE typed relations to the context's objects become
    stakeholders with formal-role positions backed by those relations. Nothing
    is inferred: interests, stances and informal influence need a judgment.
    """
    store = ctx.store
    relevant = set(relevant_object_ids)
    latest_objects = store.latest_by_id("object_version", "object_id")
    created = []
    for relation in sorted(store.latest_by_id("relationship_version",
                                              "relationship_id").values(),
                           key=lambda r: r["relationship_id"]):
        if relation["status"] != "ACTIVE" \
                or relation["relation_type"] not in _ROLE_RELATIONS:
            continue
        source, target = relation["source_object_id"], relation["target_object_id"]
        for stakeholder_id, other_id in ((source, target), (target, source)):
            if other_id not in relevant or stakeholder_id in relevant:
                continue
            wording = _ROLE_RELATIONS[relation["relation_type"]]
            if stakeholder_id == target:
                wording = f"is subject of {relation['relation_type']} from"
            position = StakeholderPosition(
                position_id=digest_id("pos", relation["relationship_id"],
                                      stakeholder_id),
                kind="FORMAL_ROLE",
                statement=f"{_entity_label(store, stakeholder_id, latest_objects)} "
                          f"{wording} "
                          f"{_entity_label(store, other_id, latest_objects)}",
                stance="UNRESOLVED", authority="OBSERVED",
                claim_ids=(), relationship_ids=(relation["relationship_id"],),
                valid_from=relation.get("valid_from"), valid_to=None,
                superseded=False, note="derived from world-model relation")
            assessment = create_assessment(
                ctx, entity_object_id=stakeholder_id,
                context_kind=context_kind, context_id=context_id,
                role_in_context=f"{relation['relation_type']} party",
                positions=(position,), provenance_kind="RULE",
                caused_by=caused_by or relation["relationship_id"])
            created.append(assessment)
            if relation["relation_type"] == "OWNS" and stakeholder_id == source:
                influence = assert_influence(
                    ctx, source_object_id=source, target_object_id=target,
                    kind="OWNS",
                    mechanism="ownership relation stated by evidence in the world model",
                    authority="OBSERVED",
                    relationship_ids=(relation["relationship_id"],),
                    provenance_kind="RULE",
                    caused_by=caused_by or relation["relationship_id"])
                link_influence(ctx, assessment["assessment_id"],
                               influence["influence_id"],
                               caused_by=relation["relationship_id"])
    return created


def explain_assessment(store: AnalyticStore, assessment_id: str) -> dict[str, Any]:
    assessment = store.current_stakeholder_assessments().get(assessment_id)
    if assessment is None:
        return {"assessment_id": assessment_id, "status": "UNKNOWN_ASSESSMENT"}
    influences = store.current_influence_assertions()
    positions = []
    for position in assessment["positions"]:
        positions.append({
            "kind": position["kind"], "statement": position["statement"],
            "stance": position["stance"], "authority": position["authority"],
            "current": not position["superseded"],
            "valid": (position["valid_from"], position["valid_to"]),
            "evidence_claims": list(position["claim_ids"]),
            "evidence_relations": list(position["relationship_ids"]),
        })
    return {
        "assessment_id": assessment_id,
        "entity": {"object_id": assessment["entity_object_id"],
                   "label": assessment["entity_label"]},
        "context": f"{assessment['context_kind']}:{assessment['context_id']}",
        "role": assessment["role_in_context"],
        "status": assessment["status"],
        "positions": positions,
        "influence": [
            {"kind": influences[i]["kind"], "mechanism": influences[i]["mechanism"],
             "authority": influences[i]["authority"], "status": influences[i]["status"]}
            for i in assessment["influence_ids"] if i in influences],
        # live from the review queue, so an ambiguity opened after the last
        # version still shows
        "identity_caveats": list(open_identity_caveats(
            store, assessment["entity_object_id"])),
        "source_basis": {
            "manifestations": assessment["basis"]["manifestation_count"],
            "independent_origin_families": len(assessment["basis"]["origin_families"]),
            "caveat": assessment["basis"]["note"],
        },
        "history": list(assessment["history"]),
        "transitions": [
            {"type": t["transition_type"], "detail": t["detail"], "at": t["recorded_time"]}
            for t in store.transitions_for(assessment_id)],
    }
