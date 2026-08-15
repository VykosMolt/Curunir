"""World-model integration: observations → entities, relations, events, claims.

State lives in the shared operational records — ObjectVersion,
RelationshipVersion, ActivityRecord — appended to the same hash-chained store,
with EVIDENTIARY provenance descending through EvidenceRefs to manifestations
and anchored observations. The proposition ledger (SemanticClaim) versions
forward beside them, carrying independence arithmetic over dependence groups.

Discipline enforced here:
  * knowledge time is the append's recorded_time; valid time comes only from
    what the source states (or, for historical manifestations, from when the
    archived state was captured) — a 2008 capture discovered today updates
    2008, not today;
  * machine integration lands as epistemic_state EXTRACTED with review_state
    UNREVIEWED; acceptance is a separate recorded act;
  * identity is deterministic only within one identifier scheme; cross-scheme
    equivalence goes through the reversible association engine as a proposal,
    never a silent merge;
  * a re-run over the same evidence appends nothing (idempotent).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping
from urllib.parse import urlparse

from argus.source_intelligence.models import digest_id
from curunir_operational.access import Marking, marking_from_record
from curunir_operational.association import AssociationEngine
from curunir_operational.contracts import (ActivityRecord, EvidenceRef, ExternalRef,
                                           ObjectVersion, ProvenanceSummary, RelationshipVersion)

from .contracts import SemanticClaim
from .store import SemanticStore

INTEGRATOR_VERSION = "curunir-semantic-integrator-0.1"

# subject scheme → world-model object type
_SCHEME_TYPES = {
    "LEI": "ORGANISATION", "SEC_CIK": "ORGANISATION", "WIKIDATA_QID": "ORGANISATION",
    "OPENCORPORATES": "ORGANISATION", "ISIN": "PUBLIC_IDENTIFIER", "TICKER": "PUBLIC_IDENTIFIER",
    "URL": "WEB_DOMAIN", "DOMAIN": "WEB_DOMAIN", "FEED": "INTELLECTUAL_WORK",
    "PERSON": "PERSON",
}

_RELATION_PREDICATES = {"SUCCESSOR_OF", "OPERATES", "OWNS", "HOLDS_ROLE",
                        "PUBLISHED_BY", "HAS_IDENTIFIER", "MENTIONS"}


def normalize_url_key(url: str) -> str:
    parsed = urlparse(url if "://" in url else f"https://{url}")
    host = (parsed.hostname or "").removeprefix("www.")
    path = (parsed.path or "/").rstrip("/") or "/"
    return f"{host}{path}"


def origin_key(manifestation: Mapping[str, Any]) -> str:
    """The underlying origin family a manifestation's information comes from.

    Independence counting is per publisher, not per response: three GLEIF
    responses restating one record are one origin (GLEIF), and a live
    retrieval and an archived capture of the same site are one origin (the
    site). Distinct publishers stay distinct families — which still does not
    prove independence, only non-identity; the basis note carries that caveat.
    """
    def _site(url: str) -> str:
        host = urlparse(url if "://" in url else f"https://{url}").hostname or url
        return f"site:{(host or '').removeprefix('www.').split(':')[0]}"

    source = manifestation["source_id"]
    native = manifestation.get("native_id") or manifestation.get("request_url", "")
    if source == "wayback":
        # capture references are "<14-digit timestamp>/<original url>"; an
        # enumeration's identity is the plain target URL itself — both belong
        # to the archived site's family, never to web.archive.org
        timestamp, sep, original = native.partition("/")
        if sep and timestamp.isdigit() and len(timestamp) == 14 and original:
            return _site(original)
        if native:
            return _site(native)
    if source == "live-web":
        return _site(manifestation.get("final_url") or native)
    return f"publisher:{source}"


def dependence_group_for(manifestation: Mapping[str, Any]) -> str:
    return digest_id("evgroup", origin_key(manifestation))


def parse_subject(subject_ref: str) -> tuple[str, str]:
    scheme, sep, value = subject_ref.partition(":")
    if not sep:
        return "LABEL", subject_ref
    return scheme, value


def world_object_id(subject_ref: str) -> str:
    scheme, value = parse_subject(subject_ref)
    if scheme in ("URL", "DOMAIN"):
        value = normalize_url_key(value)
    return digest_id("wm", scheme, value)


def _first(values: Iterable[str | None]) -> str | None:
    for value in values:
        if value:
            return value
    return None


@dataclass
class IntegrationContext:
    store: SemanticStore
    actor: str
    marking: Marking
    now_fn: Any  # Callable[[], str]

    def __post_init__(self):
        self._manifestations = {r["manifestation_id"]: r
                                for r in self.store.records_of("fabric_manifestation")}
        self._activity_ids = {r["activity_id"] for r in self.store.records_of("activity")}

    def manifestation(self, manifestation_id: str) -> Mapping[str, Any]:
        return self._manifestations.get(manifestation_id, {})


def evidence_ref(ctx: IntegrationContext, observation: Mapping[str, Any]) -> EvidenceRef:
    manifestation = ctx.manifestation(observation["manifestation_id"])
    anchors = observation["anchors"]
    mapping_status = anchors[0]["mapping_status"] if anchors else "UNSPECIFIED"
    return EvidenceRef(
        source_object_id=observation["manifestation_id"],
        document_id=observation["document_id"],
        content_sha256=anchors[0]["content_sha256"] if anchors
        else manifestation.get("content_sha256", "0" * 64),
        assertion_id=observation["observation_id"],
        evidence_basis_id=digest_id("basis", observation["observation_id"]),
        identity_status="IDENTITY_UNKNOWN",
        authority_state="AUTHORITY_NOT_ASSESSED",
        independence_status="UNRESOLVED",
        claim_basis_status="DIRECT_FIELD" if anchors and anchors[0]["kind"] == "FIELD"
        else "DIRECT_SPAN" if anchors and anchors[0]["kind"] == "TEXT_SPAN" else "UNKNOWN_BASIS",
        review_state="UNREVIEWED",
        mapping_status=mapping_status,
        dependence_group_id=dependence_group_for(manifestation) if manifestation else None,
    )


def _provenance(ctx: IntegrationContext, observations: list[Mapping[str, Any]]) -> ProvenanceSummary:
    refs = tuple(evidence_ref(ctx, observation) for observation in observations)
    return ProvenanceSummary(
        mode="EVIDENTIARY",
        source_ids=tuple(sorted({o["source_id"] for o in observations})),
        ingestion_ids=tuple(sorted({o["manifestation_id"] for o in observations})),
        evidence=refs,
    )


def _valid_times(document: Mapping[str, Any],
                 observations: list[Mapping[str, Any]]) -> tuple[str | None, str | None, str]:
    """(valid_from, source_time, precision) under the bitemporal policy.

    source_time comes only from what the source states (or the archive
    capture time); it never defaults to retrieval time — knowledge time must
    not leak into the valid-time axis."""
    stated_valid = _first(o.get("valid_from") for o in observations)
    source_time = _first([_first(o.get("source_time") for o in observations),
                          document.get("source_time")])
    precision = next((o["time_precision"] for o in observations
                      if o["time_precision"] != "UNKNOWN"), "UNKNOWN")
    if document.get("temporal_status") == "HISTORICAL":
        # an archived state was true when captured, not when we learned of it
        valid_from = stated_valid or document.get("source_time")
        return valid_from, document.get("source_time"), precision if precision != "UNKNOWN" else "DAY"
    return stated_valid, source_time, precision


def integrate_document(ctx: IntegrationContext, document: Mapping[str, Any],
                       observations: list[Mapping[str, Any]] | None = None) -> dict[str, Any]:
    """Integrate one normalized document's observations into the world model."""
    store = ctx.store
    if observations is None:
        observations = store.observations_for_document(document["document_id"])
    result = {"objects": [], "relationships": [], "activities": [], "claims": [],
              "associations": []}
    by_subject: dict[str, list[Mapping[str, Any]]] = {}
    for observation in observations:
        by_subject.setdefault(observation["subject_ref"], []).append(observation)

    for subject_ref, subject_observations in sorted(by_subject.items()):
        attributes_obs = [o for o in subject_observations
                          if o["observation_type"] in ("ENTITY_ATTRIBUTE", "ENTITY_NAME",
                                                       "ENTITY_IDENTIFIER")]
        relation_obs = [o for o in subject_observations
                        if o["observation_type"] == "RELATION"
                        and o["attribute"] in _RELATION_PREDICATES]
        event_obs = [o for o in subject_observations
                     if o["observation_type"] in ("EVENT", "PUBLICATION")]
        claim_only_obs = [o for o in subject_observations
                          if o["observation_type"] in ("STATEMENT", "ROLE_SIGNAL")]

        object_id = None
        if attributes_obs:
            object_id = _integrate_entity(ctx, document, subject_ref, attributes_obs, result)
        elif relation_obs or event_obs or claim_only_obs:
            # subjects without attribute observations still exist as objects:
            # claims, relations and alerts must never reference an id that no
            # ObjectVersion carries
            anchor_observation = (relation_obs + event_obs + claim_only_obs)[0]
            object_id = _ensure_stub_object(ctx, subject_ref, document, anchor_observation)
        for observation in relation_obs:
            _integrate_relation(ctx, document, subject_ref, object_id, observation, result)
        for observation in event_obs:
            _integrate_event(ctx, document, subject_ref, object_id, observation, result)
        for observation in attributes_obs:
            if observation["attribute"] in ("legal_name", "entity_status",
                                            "registration_status", "jurisdiction"):
                _integrate_claim(ctx, document, subject_ref, object_id, observation, result)
        for observation in claim_only_obs:
            _integrate_claim(ctx, document, subject_ref, object_id, observation, result)
    return result


def _current_object(store: SemanticStore, object_id: str) -> Mapping[str, Any] | None:
    """Current version under the projection's rule — latest validity point,
    ties broken by version number — never raw log order, so a late-arriving
    older record cannot masquerade as current here."""
    from curunir_operational.canonical import parse_time
    versions = [v for v in store.records_of("object_version") if v["object_id"] == object_id]
    if not versions:
        return None
    def _valid_point(version: Mapping[str, Any]) -> str:
        return version.get("valid_from") or version.get("source_time") or version["recorded_time"]
    return max(versions, key=lambda v: (parse_time(_valid_point(v)), v["version"]))


def _integrate_entity(ctx: IntegrationContext, document: Mapping[str, Any], subject_ref: str,
                      observations: list[Mapping[str, Any]], result: dict) -> str:
    store = ctx.store
    scheme, value = parse_subject(subject_ref)
    object_id = world_object_id(subject_ref)
    object_type = _SCHEME_TYPES.get(scheme, "ORGANISATION")
    now = ctx.now_fn()

    attributes: dict[str, Any] = {}
    labels: set[str] = set()
    identifier_refs: list[tuple[str, str, Mapping[str, Any]]] = []
    for observation in observations:
        if observation["observation_type"] == "ENTITY_IDENTIFIER":
            identifier_refs.append((observation["attribute"], observation["value"], observation))
        elif observation["observation_type"] == "ENTITY_NAME":
            if observation["attribute"] in ("legal_name", "display_name", "feed_title"):
                attributes.setdefault("name", observation["value"])
            labels.add(observation["value"][:80])
        else:
            attributes[observation["attribute"]] = observation["value"]

    # idempotency is judged against the object's whole history by observation
    # identity — not against "current", whose valid point may postdate this
    # document (a historical record must neither re-append forever nor be
    # judged by a state it did not produce)
    versions = [v for v in store.records_of("object_version") if v["object_id"] == object_id]
    current = _current_object(store, object_id)
    if versions:
        seen_assertions = {evidence.get("assertion_id")
                          for version in versions
                          for evidence in version.get("provenance", {}).get("evidence", ())}
        if all(o["observation_id"] in seen_assertions for o in observations):
            result["objects"].append({"object_id": object_id, "unchanged": True})
            return object_id
    if current is not None:
        attributes = {**current.get("attributes", {}), **attributes}
        labels |= set(current.get("labels", ()))

    valid_from, source_time, precision = _valid_times(document, observations)
    external_refs = tuple(
        ExternalRef(system=scheme_name, external_id=identifier,
                    imported_version=document["document_id"],
                    ingestion_id=observation["manifestation_id"],
                    source_time=source_time, sync_status="SYNCHRONIZED")
        for scheme_name, identifier, observation in identifier_refs) or (
        ExternalRef(system=scheme, external_id=value,
                    imported_version=document["document_id"],
                    ingestion_id=document["manifestation_id"],
                    source_time=source_time, sync_status="UNKNOWN"),)
    version = ObjectVersion(
        object_id=object_id, version=store.next_object_version(object_id),
        object_type=object_type, lifecycle="ACTIVE",
        labels=tuple(sorted(labels))[:20], external_refs=external_refs,
        valid_from=valid_from, valid_to=None, source_time=source_time,
        time_precision=precision, recorded_time=now, geometry=None,
        attributes=attributes,
        quality={"review_state": "UNREVIEWED", "source_reliability": "UNKNOWN",
                 "identity_confidence": "UNKNOWN"},
        epistemic_state="EXTRACTED", marking=ctx.marking,
        provenance=_provenance(ctx, observations),
    )
    store.append("OBJECT_VERSION_APPENDED", version, recorded_time=now, actor=ctx.actor)
    result["objects"].append({"object_id": object_id, "version": version.version})

    # cross-scheme identifiers: reversible equivalence proposals, never merges.
    # The identifier agreement here is third-party ASSERTED (one source claims
    # another registry's identifier), so auto-acceptance is withheld and the
    # ambiguity is queued for review.
    engine = AssociationEngine(store)
    for scheme_name, identifier, observation in identifier_refs:
        if scheme_name == scheme and identifier == value:
            continue
        other_id = world_object_id(f"{scheme_name}:{identifier}")
        if other_id != object_id and _current_object(store, other_id) is not None:
            proposal = engine.evaluate(object_id, other_id, recorded_time=ctx.now_fn(),
                                       actor=ctx.actor, marking=ctx.marking,
                                       temporal_window_hours=24 * 3650,
                                       auto_accept=False)
            _queue_identity_ambiguity(ctx, proposal, observation)
            result["associations"].append({"proposal_id": proposal["proposal_id"],
                                           "outcome": proposal["outcome"],
                                           "left": object_id, "right": other_id})
    return object_id


def _queue_identity_ambiguity(ctx: IntegrationContext, proposal: Mapping[str, Any],
                              observation: Mapping[str, Any] | None) -> None:
    from .contracts import ReviewItem
    store = ctx.store
    item_id = digest_id("review-identity", proposal["left_object_id"],
                        proposal["right_object_id"])
    if any(r["item_id"] == item_id for r in store.records_of("review_item")):
        return
    now = ctx.now_fn()
    item = ReviewItem(
        item_id=item_id, kind="IDENTITY_AMBIGUITY", subject_kind="association_proposal",
        subject_id=proposal["proposal_id"],
        detail=f"possible equivalence {proposal['left_object_id'][:24]} ~ "
               f"{proposal['right_object_id'][:24]} ({proposal['outcome']}): "
               + "; ".join(proposal["rationale"])[:240],
        evidence_refs=(observation["observation_id"],) if observation else (),
        status="OPEN", resolution_note="", recorded_time=now, marking=ctx.marking)
    store.append("REVIEW_ITEM_RECORDED", item, recorded_time=now, actor=ctx.actor)


def _ensure_stub_object(ctx: IntegrationContext, subject_ref: str,
                        document: Mapping[str, Any], observation: Mapping[str, Any]) -> str:
    """A relation/event endpoint must exist as an object, even minimally."""
    store = ctx.store
    object_id = world_object_id(subject_ref)
    if _current_object(store, object_id) is not None:
        return object_id
    scheme, value = parse_subject(subject_ref)
    now = ctx.now_fn()
    version = ObjectVersion(
        object_id=object_id, version=store.next_object_version(object_id),
        object_type=_SCHEME_TYPES.get(scheme, "ORGANISATION"), lifecycle="PROPOSED",
        labels=(), external_refs=(ExternalRef(
            system=scheme, external_id=value, imported_version=document["document_id"],
            ingestion_id=observation["manifestation_id"], source_time=None,
            sync_status="UNKNOWN"),),
        valid_from=None, valid_to=None, source_time=None, time_precision="UNKNOWN",
        recorded_time=now, geometry=None, attributes={},
        quality={"review_state": "UNREVIEWED", "identity_confidence": "UNKNOWN"},
        epistemic_state="REPORTED", marking=ctx.marking,
        provenance=_provenance(ctx, [observation]),
    )
    store.append("OBJECT_VERSION_APPENDED", version, recorded_time=now, actor=ctx.actor)
    return object_id


def _integrate_relation(ctx: IntegrationContext, document: Mapping[str, Any], subject_ref: str,
                        subject_object_id: str | None, observation: Mapping[str, Any],
                        result: dict) -> None:
    store = ctx.store
    subject_id = subject_object_id or _ensure_stub_object(ctx, subject_ref, document, observation)
    target_ref = observation["object_ref"] or observation["value"]
    target_id = _ensure_stub_object(ctx, target_ref, document, observation)
    relation_type = observation["attribute"]
    relationship_id = digest_id("wmrel", relation_type, subject_id, target_id)
    existing = [r for r in store.records_of("relationship_version")
                if r["relationship_id"] == relationship_id]
    if existing and existing[-1]["status"] == "ACTIVE" \
            and observation["observation_id"] in existing[-1]["evidence_refs"]:
        return
    valid_from, source_time, precision = _valid_times(document, [observation])
    now = ctx.now_fn()
    version = RelationshipVersion(
        relationship_id=relationship_id,
        version=store.next_relationship_version(relationship_id),
        relation_type=relation_type, source_object_id=subject_id, target_object_id=target_id,
        valid_from=valid_from, valid_to=None, recorded_time=now,
        evidence_refs=(observation["observation_id"],),
        derivation="EVIDENCE", confidence="UNKNOWN", status="ACTIVE",
        marking=ctx.marking, provenance=_provenance(ctx, [observation]),
        rationale=f"{observation['producer_id']} extraction from {document['document_id'][:24]}",
    )
    store.append("RELATIONSHIP_VERSION_APPENDED", version, recorded_time=now, actor=ctx.actor)
    result["relationships"].append({"relationship_id": relationship_id,
                                    "version": version.version})


def _integrate_event(ctx: IntegrationContext, document: Mapping[str, Any], subject_ref: str,
                     subject_object_id: str | None, observation: Mapping[str, Any],
                     result: dict) -> None:
    store = ctx.store
    subject_id = subject_object_id or _ensure_stub_object(ctx, subject_ref, document, observation)
    activity_id = digest_id("wmevent", observation["attribute"], subject_id,
                            observation["object_ref"] or observation["value"],
                            observation.get("valid_from") or "")
    if activity_id in ctx._activity_ids:
        return
    valid_from, source_time, precision = _valid_times(document, [observation])
    now = ctx.now_fn()
    record = ActivityRecord(
        activity_id=activity_id, activity_type=observation["attribute"],
        epistemic_state="EXTRACTED", subject_ids=(subject_id,),
        description=observation["value"][:400],
        valid_from=valid_from, valid_to=None, source_time=source_time,
        recorded_time=now, evidence_refs=(observation["observation_id"],),
        marking=ctx.marking, provenance=_provenance(ctx, [observation]),
        participants=((subject_id, "SUBJECT"),) + (
            ((observation["object_ref"], "OBJECT"),) if observation["object_ref"] else ()),
        time_precision=precision,
    )
    store.append("ACTIVITY_RECORDED", record, recorded_time=now, actor=ctx.actor)
    ctx._activity_ids.add(activity_id)
    result["activities"].append({"activity_id": activity_id,
                                 "activity_type": observation["attribute"]})


def _integrate_claim(ctx: IntegrationContext, document: Mapping[str, Any], subject_ref: str,
                     subject_object_id: str | None, observation: Mapping[str, Any],
                     result: dict) -> None:
    """Version the proposition ledger for claim-worthy observations."""
    store = ctx.store
    subject_id = subject_object_id or world_object_id(subject_ref)
    claim_id = digest_id("claim", subject_id, observation["attribute"])
    versions = [c for c in store.records_of("semantic_claim") if c["claim_id"] == claim_id]
    seen_observations = {o for c in versions for o in c["observation_ids"]}
    if observation["observation_id"] in seen_observations:
        return  # this evidence is already accounted in the claim's history

    def _state_time(observation_ids: Iterable[str]) -> str:
        """When the observed source state was current — never knowledge time.

        A HISTORICAL manifestation speaks for its capture moment; a live
        retrieval speaks for its retrieval moment. Ranking on this axis keeps
        a 2008 capture retrieved today from displacing today's state."""
        times = []
        for oid in observation_ids:
            record = next((o for o in store.records_of("semantic_observation")
                           if o["observation_id"] == oid), None)
            if record:
                manifestation = ctx.manifestation(record["manifestation_id"])
                if manifestation:
                    if manifestation.get("temporal_status") == "HISTORICAL":
                        times.append(manifestation.get("archive_capture_time")
                                     or manifestation.get("source_time") or "")
                    else:
                        times.append(manifestation["retrieval_time"])
        return max((t for t in times if t), default="")

    current = versions[-1] if versions else None
    supporting = [observation["observation_id"]]
    if current is not None:
        new_manifestation = ctx.manifestation(observation["manifestation_id"])
        new_group = dependence_group_for(new_manifestation) if new_manifestation else ""
        if current["object_or_value"] == observation["value"]:
            if new_group and new_group in set(current["dependence_group_ids"]):
                return  # same value, same origin family: churn, not new basis
            supporting = sorted(set(current["observation_ids"]) | {observation["observation_id"]})
        else:
            # a different value supersedes only when it reflects strictly
            # newer SOURCE STATE from within the claim's own origin family
            # (the source updated itself). Older or historical states never
            # displace current state, and a conflicting value from an
            # independent origin is a contradiction to surface, not a
            # supersession — newer is not automatically truer across sources.
            new_time = _state_time([observation["observation_id"]])
            current_time = _state_time(current["observation_ids"])
            if not new_time or not current_time or new_time <= current_time:
                # unknown ordering is treated as not-newer: the observation
                # stays recorded as evidence but does not displace the claim
                return
            if new_group and new_group not in set(current["dependence_group_ids"]):
                _record_claim_conflict(ctx, current, observation, result)
                return
    groups = set()
    observation_records = []
    all_observations = {o["observation_id"]: o
                        for o in store.records_of("semantic_observation")}
    for observation_id in supporting:
        record = all_observations.get(observation_id)
        if record is not None:
            observation_records.append(record)
            manifestation = ctx.manifestation(record["manifestation_id"])
            if manifestation:
                groups.add(dependence_group_for(manifestation))
    now = ctx.now_fn()
    valid_from, source_time, precision = _valid_times(document, [observation])
    subject_version = _current_object(store, subject_id)
    claim = SemanticClaim(
        claim_id=claim_id, version=store.next_claim_version(claim_id),
        statement=f"{subject_ref} {observation['attribute']} = {observation['value'][:200]}",
        subject_ref=subject_ref, subject_object_id=subject_id,
        predicate=observation["attribute"], object_or_value=observation["value"][:500],
        object_object_id=world_object_id(observation["object_ref"]) if observation["object_ref"] else "",
        valid_from=valid_from, valid_to=None, time_precision=precision,
        polarity="AFFIRMED",
        observation_ids=tuple(supporting),
        dependence_group_ids=tuple(sorted(groups)),
        independent_basis_count=min(len(groups), len(supporting)),
        basis_note="independent basis counts distinct origin families; unresolved "
                   "cross-family dependence is not assumed independent",
        world_refs=(f"{subject_id}@v{subject_version['version']}",) if subject_version else (),
        epistemic_state="EXTRACTED", review_state="UNREVIEWED",
        recorded_time=now, marking=ctx.marking,
    )
    store.append("SEMANTIC_CLAIM_RECORDED", claim, recorded_time=now, actor=ctx.actor)
    # a lifecycle state describes the CURRENT version's standing — but only
    # machine-bookkeeping states (STALE, SUPERSEDED) may be machine-reset when
    # fresh evidence advances the claim. RETRACTED, CORRECTED, DISPUTED and
    # SOURCE_WITHDRAWN carry adjudication weight: a SERVICE actor never
    # reverts them; the tension is queued for review instead.
    if current is not None and current["object_or_value"] != observation["value"]:
        prior_state = store.claim_state(claim_id)
        from .contracts import ClaimStateRecord, ReviewItem
        if prior_state in ("STALE", "SUPERSEDED"):
            reset = ClaimStateRecord(
                state_id=digest_id("clstate", claim_id, "CURRENT", now),
                claim_id=claim_id, state="CURRENT",
                reason=f"superseded by fresh evidence at version {claim.version}",
                caused_by=observation["observation_id"],
                superseded_by=f"{claim_id}@v{claim.version}",
                actor_id=ctx.actor, actor_kind="SERVICE",
                recorded_time=ctx.now_fn(), marking=ctx.marking)
            store.append("SEMANTIC_CLAIM_STATE_RECORDED", reset,
                         recorded_time=reset.recorded_time, actor=ctx.actor)
        elif prior_state != "CURRENT":
            item_id = digest_id("review-standing", claim_id, str(claim.version))
            if not any(r["item_id"] == item_id for r in store.records_of("review_item")):
                item = ReviewItem(
                    item_id=item_id, kind="MANIFESTATION_CHANGED",
                    subject_kind="semantic_claim", subject_id=claim_id,
                    detail=f"fresh evidence advanced the claim to version "
                           f"{claim.version} ({observation['value'][:120]!r}) while its "
                           f"{prior_state} standing is unadjudicated; the state was "
                           f"not machine-reset and needs review",
                    evidence_refs=(observation["observation_id"],),
                    status="OPEN", resolution_note="",
                    recorded_time=ctx.now_fn(), marking=ctx.marking)
                store.append("REVIEW_ITEM_RECORDED", item,
                             recorded_time=item.recorded_time, actor=ctx.actor)
    result["claims"].append({"claim_id": claim_id, "version": claim.version,
                             "value": observation["value"][:80]})


def _record_claim_conflict(ctx: IntegrationContext, current_claim: Mapping[str, Any],
                           observation: Mapping[str, Any], result: dict) -> None:
    """An independent origin disagrees with the current proposition: mark the
    claim DISPUTED and queue it for review with both bases visible. History
    is preserved; nothing is auto-resolved."""
    from .contracts import ClaimStateRecord, ReviewItem
    store = ctx.store
    now = ctx.now_fn()
    claim_id = current_claim["claim_id"]
    reason = (f"independent source {observation['source_id']} reports "
              f"{observation['value'][:120]!r} against current "
              f"{current_claim['object_or_value'][:120]!r}")
    if store.claim_state(claim_id) != "DISPUTED":
        state = ClaimStateRecord(
            state_id=digest_id("clstate", claim_id, "DISPUTED", now),
            claim_id=claim_id, state="DISPUTED", reason=reason,
            caused_by=observation["observation_id"], superseded_by="",
            actor_id=ctx.actor, actor_kind="SERVICE",
            recorded_time=now, marking=ctx.marking)
        store.append("SEMANTIC_CLAIM_STATE_RECORDED", state, recorded_time=now, actor=ctx.actor)
    item_id = digest_id("review", claim_id, observation["observation_id"])
    if not any(r["item_id"] == item_id for r in store.records_of("review_item")):
        item = ReviewItem(
            item_id=item_id, kind="CONTRADICTED", subject_kind="semantic_claim",
            subject_id=claim_id, detail=reason,
            evidence_refs=(observation["observation_id"],) + tuple(current_claim["observation_ids"][:5]),
            status="OPEN", resolution_note="", recorded_time=now, marking=ctx.marking)
        store.append("REVIEW_ITEM_RECORDED", item, recorded_time=now, actor=ctx.actor)
    result.setdefault("conflicts", []).append({"claim_id": claim_id,
                                               "observation_id": observation["observation_id"]})


def propose_cross_scheme_associations(ctx: IntegrationContext) -> list[dict[str, Any]]:
    """Order-independent equivalence sweep: two distinct objects sharing an
    identity-bearing external identifier become a reversible association
    proposal (never a merge). Pairs already proposed are not re-proposed."""
    store = ctx.store
    latest: dict[str, Mapping[str, Any]] = {}
    for version in store.records_of("object_version"):
        latest[version["object_id"]] = version
    by_identifier: dict[tuple[str, str], set[str]] = {}
    for object_id, version in latest.items():
        for ref in version.get("external_refs", ()):
            if ref.get("identity_bearing", True):
                by_identifier.setdefault((ref["system"], ref["external_id"]), set()).add(object_id)
    proposed_pairs = {tuple(sorted((p["left_object_id"], p["right_object_id"])))
                      for p in store.records_of("association_proposal")}
    engine = AssociationEngine(store)
    proposals = []
    for members in by_identifier.values():
        ordered = sorted(members)
        for index, left in enumerate(ordered):
            for right in ordered[index + 1:]:
                pair = (left, right)
                if pair in proposed_pairs:
                    continue
                proposal = engine.evaluate(left, right, recorded_time=ctx.now_fn(),
                                           actor=ctx.actor, marking=ctx.marking,
                                           temporal_window_hours=24 * 3650,
                                           auto_accept=False)
                _queue_identity_ambiguity(ctx, proposal, None)
                proposed_pairs.add(pair)
                proposals.append(proposal)
    return proposals


def integrate_all(ctx: IntegrationContext) -> dict[str, int]:
    """Integrate every normalized document in the store, idempotently."""
    totals = {"objects": 0, "relationships": 0, "activities": 0, "claims": 0,
              "associations": 0}
    for document in ctx.store.records_of("semantic_document"):
        outcome = integrate_document(ctx, document)
        for key in totals:
            totals[key] += len(outcome[key])
    totals["associations"] += len(propose_cross_scheme_associations(ctx))
    return totals
