"""Turn observations into world-model entities, relations, events and claims.

The rules this module keeps:

  * valid time comes from what the source says, or from when an archived state
    was captured — a 2008 capture found today updates 2008, not today;
  * machine output is recorded as EXTRACTED and UNREVIEWED; accepting it is a
    separate act;
  * identifiers from different schemes are matched by a reversible proposal,
    never a silent merge;
  * re-running over the same evidence appends nothing.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Mapping
from urllib.parse import urlparse

from argus.source_intelligence.models import digest_id
from curunir_operational.access import Marking, marking_from_record, most_restrictive
from curunir_operational.association import AssociationEngine
from curunir_operational.contracts import (ActivityRecord, EvidenceRef, ExternalRef,
                                           ObjectVersion, ProvenanceSummary, RelationshipVersion)

from curunir_operational.canonical import parse_time

from .contracts import ClaimStateRecord, ReviewItem, SemanticClaim
from .store import SemanticStore

INTEGRATOR_VERSION = "curunir-semantic-integrator-0.1"

# how a subject's identifier scheme maps to a world-model object type
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
    """Which publisher a manifestation's information really comes from.

    Independence is counted per publisher, not per response: three GLEIF
    responses restating one record are one origin, and a live fetch and an
    archived capture of the same site are one origin. Different publishers are
    only different, which is not proof that they are independent.
    """
    def _site(url: str) -> str:
        host = urlparse(url if "://" in url else f"https://{url}").hostname or url
        return f"site:{(host or '').removeprefix('www.').split(':')[0]}"

    source = manifestation["source_id"]
    native = manifestation.get("native_id") or manifestation.get("request_url", "")
    if source == "wayback":
        # A capture reference is "<14-digit timestamp>/<original url>" and an
        # enumeration is the plain URL. Both belong to the archived site.
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
    """Return (valid_from, source_time, precision) for a document.

    source_time comes only from what the source states, or from the archive
    capture time. It never falls back to retrieval time, which is when we
    learned something, not when it was true."""
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
            # A subject with no attribute observations still needs an object:
            # nothing may reference an id that no object version carries.
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
    """The object's current version: latest valid point, ties broken by version.

    Not log order, so a late-arriving older record cannot pose as current.
    """
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

    # Judge "already integrated" against the object's whole history by
    # observation id, not against the current version, whose valid point may be
    # later than this document's.
    versions = [v for v in store.records_of("object_version") if v["object_id"] == object_id]
    current = _current_object(store, object_id)
    if versions:
        seen_assertions = {evidence.get("assertion_id")
                          for version in versions
                          for evidence in version.get("provenance", {}).get("evidence", ())}
        if all(o["observation_id"] in seen_assertions for o in observations):
            result["objects"].append({"object_id": object_id, "unchanged": True})
            return object_id
    # The new version carries the previous attributes and labels forward, so it
    # must be at least as restricted as the version it copies them from.
    write_marking = ctx.marking
    if current is not None:
        attributes = {**current.get("attributes", {}), **attributes}
        labels |= set(current.get("labels", ()))
        if isinstance(current.get("marking"), dict):
            write_marking = most_restrictive([ctx.marking,
                                              marking_from_record(current["marking"])])

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
        epistemic_state="EXTRACTED", marking=write_marking,
        provenance=_provenance(ctx, observations),
    )
    store.append("OBJECT_VERSION_APPENDED", version, recorded_time=now, actor=ctx.actor)
    result["objects"].append({"object_id": object_id, "version": version.version})

    # Identifiers from another scheme become reversible equivalence proposals,
    # never merges: one source claiming another registry's identifier is
    # hearsay, so a human reviews it.
    engine = AssociationEngine(store)
    for scheme_name, identifier, observation in identifier_refs:
        if scheme_name == scheme and identifier == value:
            continue
        other_id = world_object_id(f"{scheme_name}:{identifier}")
        other = _current_object(store, other_id)
        if other_id != object_id and other is not None:
            # The proposal says two objects are the same, so it is at least as
            # restricted as either of them.
            pair_marking = most_restrictive(
                [write_marking]
                + ([marking_from_record(other["marking"])]
                   if isinstance(other.get("marking"), dict) else []))
            proposal = engine.evaluate(object_id, other_id, recorded_time=ctx.now_fn(),
                                       actor=ctx.actor, marking=pair_marking,
                                       temporal_window_hours=24 * 3650,
                                       auto_accept=False)
            _queue_identity_ambiguity(ctx, proposal, observation)
            result["associations"].append({"proposal_id": proposal["proposal_id"],
                                           "outcome": proposal["outcome"],
                                           "left": object_id, "right": other_id})
    return object_id


def _queue_identity_ambiguity(ctx: IntegrationContext, proposal: Mapping[str, Any],
                              observation: Mapping[str, Any] | None,
                              known_item_ids: set[str] | None = None) -> None:
    store = ctx.store
    item_id = digest_id("review-identity", proposal["left_object_id"],
                        proposal["right_object_id"])
    if known_item_ids is not None:
        if item_id in known_item_ids:
            return
        known_item_ids.add(item_id)
    elif any(r["item_id"] == item_id for r in store.records_of("review_item")):
        return
    now = ctx.now_fn()
    item = ReviewItem(
        item_id=item_id, kind="IDENTITY_AMBIGUITY", subject_kind="association_proposal",
        subject_id=proposal["proposal_id"],
        detail=f"possible equivalence {proposal['left_object_id'][:24]} ~ "
               f"{proposal['right_object_id'][:24]} ({proposal['outcome']}): "
               + "; ".join(proposal["rationale"])[:240],
        evidence_refs=(observation["observation_id"],) if observation else (),
        status="OPEN", resolution_note="", recorded_time=now,
        # The item names the equivalence, so it inherits the proposal's
        # marking rather than the context default.
        marking=marking_from_record(proposal["marking"])
        if isinstance(proposal.get("marking"), dict) else ctx.marking)
    store.append("REVIEW_ITEM_RECORDED", item, recorded_time=now, actor=ctx.actor)


def _ensure_stub_object(ctx: IntegrationContext, subject_ref: str,
                        document: Mapping[str, Any], observation: Mapping[str, Any]) -> str:
    """Create a bare object for a relation or event endpoint that has none."""
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
    """Record or advance the claim an observation bears on."""
    store = ctx.store
    subject_id = subject_object_id or world_object_id(subject_ref)
    claim_id = digest_id("claim", subject_id, observation["attribute"])
    versions = [c for c in store.records_of("semantic_claim") if c["claim_id"] == claim_id]
    seen_observations = {o for c in versions for o in c["observation_ids"]}
    if observation["observation_id"] in seen_observations:
        # The evidence is already recorded, but the standing bookkeeping that
        # should follow it may have been lost to an interruption. Finish it
        # here — only when this observation is the one that produced the latest
        # advance, so an older observation cannot re-stamp a human's judgment.
        if len(versions) >= 2 \
                and observation["observation_id"] in versions[-1]["observation_ids"] \
                and observation["observation_id"] \
                not in versions[-2]["observation_ids"] \
                and versions[-2]["object_or_value"] != versions[-1]["object_or_value"]:
            _ensure_claim_standing(ctx, claim_id, versions[-1]["version"],
                                   new_value=versions[-1]["object_or_value"],
                                   observation_id=observation["observation_id"])
        return

    all_observations = {o["observation_id"]: o
                        for o in store.records_of("semantic_observation")}

    def _state_time(observation_ids: Iterable[str]) -> str:
        """When the observed state was current at the source, not when we saw it.

        An archived manifestation speaks for its capture moment, a live one for
        its retrieval moment, so a 2008 capture fetched today cannot displace
        today's state."""
        times = []
        for oid in observation_ids:
            record = all_observations.get(oid)
            if record:
                manifestation = ctx.manifestation(record["manifestation_id"])
                if manifestation:
                    if manifestation.get("temporal_status") == "HISTORICAL":
                        times.append(manifestation.get("archive_capture_time")
                                     or manifestation.get("source_time") or "")
                    else:
                        times.append(manifestation["retrieval_time"])
        return max((t for t in times if t), key=parse_time, default="")

    current = versions[-1] if versions else None
    supporting = [observation["observation_id"]]
    if current is not None:
        new_manifestation = ctx.manifestation(observation["manifestation_id"])
        new_group = dependence_group_for(new_manifestation) if new_manifestation else ""
        if current["object_or_value"] == observation["value"]:
            if new_group and new_group in set(current["dependence_group_ids"]):
                return  # same value from the same publisher adds no basis
            supporting = sorted(set(current["observation_ids"]) | {observation["observation_id"]})
        else:
            # A different value replaces the current one only when the same
            # publisher has newer source state. A conflicting value from another
            # publisher is a contradiction to surface, not an update: newer is
            # not automatically truer across sources.
            new_time = _state_time([observation["observation_id"]])
            current_time = _state_time(current["observation_ids"])
            # Compare parsed times: capture and source times keep their own
            # offsets and would sort wrongly as raw strings.
            if not new_time or not current_time \
                    or parse_time(new_time) <= parse_time(current_time):
                # Unknown order counts as not newer. The observation stays in
                # the log as evidence but does not move the claim.
                return
            if new_group and new_group not in set(current["dependence_group_ids"]):
                _record_claim_conflict(ctx, current, observation, result)
                return
    groups = set()
    observation_records = []
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
    if current is not None and current["object_or_value"] != observation["value"]:
        _ensure_claim_standing(ctx, claim_id, claim.version,
                               new_value=observation["value"],
                               observation_id=observation["observation_id"])
    result["claims"].append({"claim_id": claim_id, "version": claim.version,
                             "value": observation["value"][:80]})


def _ensure_claim_standing(ctx: IntegrationContext, claim_id: str, version: int, *,
                           new_value: str, observation_id: str) -> None:
    """Settle a claim's standing after new evidence changed its value.

    The machine may only clear its own bookkeeping states (STALE, SUPERSEDED),
    and only when they predate the advance. RETRACTED, CORRECTED, DISPUTED and
    SOURCE_WITHDRAWN are human judgments: the machine queues them for review
    instead of reverting them. Safe to re-run.
    """
    store = ctx.store
    latest_version = next((c for c in reversed(store.records_of("semantic_claim"))
                           if c["claim_id"] == claim_id), None)
    state_record = store.claim_states().get(claim_id)
    if latest_version is not None and state_record is not None \
            and parse_time(state_record["recorded_time"]) \
            >= parse_time(latest_version["recorded_time"]):
        # The standing is as new as the advance, so there is nothing to
        # complete. A tie fails safe toward not resetting.
        return
    prior_state = store.claim_state(claim_id)
    # The new record names the claim's prior standing, so it inherits that
    # state record's marking — not the claim's current, possibly lower one.
    standing_marking = most_restrictive([ctx.marking, marking_from_record(state_record["marking"])]) \
        if state_record and isinstance(state_record.get("marking"), dict) else ctx.marking
    if prior_state in ("STALE", "SUPERSEDED"):
        now = ctx.now_fn()
        reset = ClaimStateRecord(
            state_id=digest_id("clstate", claim_id, "CURRENT", now),
            claim_id=claim_id, state="CURRENT",
            reason=f"superseded by fresh evidence at version {version}",
            caused_by=observation_id,
            superseded_by=f"{claim_id}@v{version}",
            actor_id=ctx.actor, actor_kind="SERVICE",
            recorded_time=now, marking=standing_marking)
        store.append("SEMANTIC_CLAIM_STATE_RECORDED", reset,
                     recorded_time=reset.recorded_time, actor=ctx.actor)
    elif prior_state != "CURRENT":
        item_id = digest_id("review-standing", claim_id, str(version))
        if not any(r["item_id"] == item_id for r in store.records_of("review_item")):
            item = ReviewItem(
                item_id=item_id, kind="MANIFESTATION_CHANGED",
                subject_kind="semantic_claim", subject_id=claim_id,
                detail=f"fresh evidence advanced the claim to version "
                       f"{version} ({new_value[:120]!r}) while its "
                       f"{prior_state} standing is unadjudicated; the state was "
                       f"not machine-reset and needs review",
                evidence_refs=(observation_id,),
                status="OPEN", resolution_note="",
                recorded_time=ctx.now_fn(), marking=standing_marking)
            store.append("REVIEW_ITEM_RECORDED", item,
                         recorded_time=item.recorded_time, actor=ctx.actor)


def _record_claim_conflict(ctx: IntegrationContext, current_claim: Mapping[str, Any],
                           observation: Mapping[str, Any], result: dict) -> None:
    """Mark a claim DISPUTED and queue it when another publisher disagrees.

    Nothing is resolved automatically and no history is lost.
    """
    store = ctx.store
    now = ctx.now_fn()
    claim_id = current_claim["claim_id"]
    reason = (f"independent source {observation['source_id']} reports "
              f"{observation['value'][:120]!r} against current "
              f"{current_claim['object_or_value'][:120]!r}")
    # Both records quote the claim's value, so they are at least as restricted
    # as the claim itself.
    conflict_marking = most_restrictive([ctx.marking, marking_from_record(current_claim["marking"])]) \
        if isinstance(current_claim.get("marking"), dict) else ctx.marking
    if store.claim_state(claim_id) != "DISPUTED":
        state = ClaimStateRecord(
            state_id=digest_id("clstate", claim_id, "DISPUTED", now),
            claim_id=claim_id, state="DISPUTED", reason=reason,
            caused_by=observation["observation_id"], superseded_by="",
            actor_id=ctx.actor, actor_kind="SERVICE",
            recorded_time=now, marking=conflict_marking)
        store.append("SEMANTIC_CLAIM_STATE_RECORDED", state, recorded_time=now, actor=ctx.actor)
    item_id = digest_id("review", claim_id, observation["observation_id"])
    if not any(r["item_id"] == item_id for r in store.records_of("review_item")):
        item = ReviewItem(
            item_id=item_id, kind="CONTRADICTED", subject_kind="semantic_claim",
            subject_id=claim_id, detail=reason,
            evidence_refs=(observation["observation_id"],) + tuple(current_claim["observation_ids"][:5]),
            status="OPEN", resolution_note="", recorded_time=now, marking=conflict_marking)
        store.append("REVIEW_ITEM_RECORDED", item, recorded_time=now, actor=ctx.actor)
    result.setdefault("conflicts", []).append({"claim_id": claim_id,
                                               "observation_id": observation["observation_id"]})


def propose_cross_scheme_associations(ctx: IntegrationContext) -> list[dict[str, Any]]:
    """Propose an equivalence wherever two objects share an identifier.

    The result does not depend on the order objects were seen in, proposals are
    reversible and never merges, and a pair is proposed only once.
    """
    store = ctx.store
    latest: dict[str, Mapping[str, Any]] = {}
    for version in store.records_of("object_version"):
        latest[version["object_id"]] = version
    by_identifier: dict[tuple[str, str], set[str]] = {}
    for object_id, version in latest.items():
        for ref in version.get("external_refs", ()):
            if ref.get("identity_bearing", True):
                by_identifier.setdefault((ref["system"], ref["external_id"]), set()).add(object_id)
    recorded_proposals = {tuple(sorted((p["left_object_id"], p["right_object_id"]))): p
                          for p in store.records_of("association_proposal")}
    proposed_pairs = set(recorded_proposals)
    # A proposal whose review item was lost to an interruption would sit
    # unseen forever, so re-queue every recorded proposal.
    known_item_ids = {r["item_id"] for r in store.records_of("review_item")}
    for proposal in recorded_proposals.values():
        _queue_identity_ambiguity(ctx, proposal, None, known_item_ids)
    engine = AssociationEngine(store)
    proposals = []
    for members in by_identifier.values():
        ordered = sorted(members)
        for index, left in enumerate(ordered):
            for right in ordered[index + 1:]:
                pair = (left, right)
                if pair in proposed_pairs:
                    continue
                # The proposal says two objects are the same, so it is at
                # least as restricted as either of them.
                pair_marking = most_restrictive(
                    [ctx.marking]
                    + [marking_from_record(latest[o]["marking"])
                       for o in (left, right)
                       if isinstance(latest[o].get("marking"), dict)])
                proposal = engine.evaluate(left, right, recorded_time=ctx.now_fn(),
                                           actor=ctx.actor, marking=pair_marking,
                                           temporal_window_hours=24 * 3650,
                                           auto_accept=False)
                _queue_identity_ambiguity(ctx, proposal, None)
                proposed_pairs.add(pair)
                proposals.append(proposal)
    return proposals


def integrate_all(ctx: IntegrationContext) -> dict[str, int]:
    """Integrate every normalized document in the store; safe to re-run."""
    totals = {"objects": 0, "relationships": 0, "activities": 0, "claims": 0,
              "associations": 0}
    for document in ctx.store.records_of("semantic_document"):
        outcome = integrate_document(ctx, document)
        for key in totals:
            totals[key] += len(outcome[key])
    totals["associations"] += len(propose_cross_scheme_associations(ctx))
    return totals
