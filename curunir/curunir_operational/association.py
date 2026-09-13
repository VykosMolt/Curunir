"""Conservative, reversible operational object association: nothing is ever
destructively merged.

AUTO_ASSOCIATE needs an exact cross-system identifier match with no
contradictions; anything ambiguous stays a proposal for review. Accepted
associations become SAME_AS versions that can be reversed later, so every merge,
split and reversal keeps full lineage.
"""
from __future__ import annotations

from typing import Any, Iterable, Mapping

from .access import Marking
from .canonical import digest_id, parse_time
from .contracts import AssociationProposal, AssociationResolution, ProvenanceSummary, RelationshipVersion
from .geometry import haversine_m
from .store import MissionDataStore

ENGINE_VERSION = "curunir-operational-association-v1"
DEFAULT_VOLATILE_FIELDS = ("position_note", "observed_at", "reported_speed_kmh")
IDENTIFIER_SUFFIXES = ("_id", "_ref", "_callsign")


def _identifier_attributes(version: Mapping[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in version.get("attributes", {}).items()
            if any(k.endswith(s) for s in IDENTIFIER_SUFFIXES) and v not in (None, "", "UNKNOWN")}


def _weakest_mapping_confidence(left: Mapping[str, Any], right: Mapping[str, Any]) -> Any:
    values = [v.get("quality", {}).get("mapping_confidence", "UNKNOWN") for v in (left, right)]
    if "UNKNOWN" in values:
        return "UNKNOWN"
    # Between a number and a label the number counts as weaker; labels compare as text.
    return min(values, key=lambda x: (isinstance(x, str), x))


def compute_features(left: Mapping[str, Any], right: Mapping[str, Any], *,
                     max_speed_kmh: float = 80.0, temporal_window_hours: float = 12.0,
                     volatile_fields: Iterable[str] = DEFAULT_VOLATILE_FIELDS,
                     negative_evidence: Iterable[str] = ()) -> dict[str, Any]:
    left_refs = {(r["system"], r["external_id"]) for r in left.get("external_refs", [])
                 if r.get("identity_bearing", True)}
    right_refs = {(r["system"], r["external_id"]) for r in right.get("external_refs", [])
                  if r.get("identity_bearing", True)}
    if left_refs & right_refs:
        identifier = "SYSTEM_MATCH"
    else:
        shared_systems = {s for s, _ in left_refs} & {s for s, _ in right_refs}
        left_ids = _identifier_attributes(left)
        right_ids = _identifier_attributes(right)
        shared_keys = set(left_ids) & set(right_ids)
        if shared_systems:
            # One identity-issuing system registered them as two things.
            identifier = "CONFLICT"
        elif shared_keys and all(left_ids[k] == right_ids[k] for k in shared_keys):
            identifier = "ATTRIBUTE_MATCH"
        elif shared_keys:
            identifier = "CONFLICT"
        else:
            identifier = "ABSENT"
    type_compatibility = "MATCH" if left.get("object_type") == right.get("object_type") else "MISMATCH"
    left_time = left.get("source_time") or left.get("valid_from")
    right_time = right.get("source_time") or right.get("valid_from")
    if left_time and right_time:
        gap_hours = abs((parse_time(right_time) - parse_time(left_time)).total_seconds()) / 3600
        temporal = "COMPATIBLE" if gap_hours <= temporal_window_hours else "INCOMPATIBLE"
    else:
        gap_hours = None
        temporal = "UNKNOWN"
    geospatial = "UNKNOWN"
    distance_m = None
    if left.get("geometry") and right.get("geometry") \
            and left["geometry"]["kind"] == right["geometry"]["kind"] == "POINT":
        distance_m = round(haversine_m(left["geometry"]["coordinates"], right["geometry"]["coordinates"]), 1)
        if gap_hours is None:
            geospatial = "UNKNOWN"
        else:
            budget_m = max_speed_kmh * 1000 * max(gap_hours, 0.25)
            geospatial = "COMPATIBLE" if distance_m <= budget_m else "IMPLAUSIBLE_SPEED"
    left_sources = set(left.get("provenance", {}).get("source_ids", []))
    right_sources = set(right.get("provenance", {}).get("source_ids", []))
    if left_sources and right_sources:
        source_relationship = "SAME_SOURCE" if left_sources & right_sources else "DIFFERENT_SOURCES"
    else:
        source_relationship = "UNKNOWN"
    volatile = set(volatile_fields)

    def _contradicts(a: Any, b: Any) -> bool:
        if a in (None, "UNKNOWN") or b in (None, "UNKNOWN") or a == b:
            return False
        if isinstance(a, (int, float)) and isinstance(b, (int, float)) and not isinstance(a, bool):
            # Numeric readings within 20% are treated as agreeing.
            return abs(a - b) > 0.2 * max(abs(a), abs(b))
        return True

    contradictions = sorted(
        k for k in set(left.get("attributes", {})) & set(right.get("attributes", {}))
        if k not in volatile and not any(k.endswith(s) for s in IDENTIFIER_SUFFIXES)
        and _contradicts(left["attributes"][k], right["attributes"][k]))
    return {
        "identifier_agreement": identifier, "type_compatibility": type_compatibility,
        "temporal_compatibility": temporal, "temporal_gap_hours": gap_hours,
        "geospatial_compatibility": geospatial, "distance_m": distance_m,
        "source_relationship": source_relationship, "contradictory_attributes": contradictions,
        "negative_evidence": sorted(negative_evidence),
        # An unassessed mapping is weaker than any scored one, so UNKNOWN wins.
        "mapping_confidence": _weakest_mapping_confidence(left, right),
    }


def decide(features: Mapping[str, Any]) -> tuple[str, tuple[str, ...]]:
    rationale: list[str] = []
    if features["type_compatibility"] == "MISMATCH":
        return "REJECT_ASSOCIATION", ("object types differ",)
    if features["negative_evidence"]:
        return "REJECT_ASSOCIATION", tuple(f"negative evidence: {e}" for e in features["negative_evidence"])
    if features["identifier_agreement"] == "CONFLICT":
        return "REJECT_ASSOCIATION", ("conflicting identifiers from a shared identifier space",)
    if features["geospatial_compatibility"] == "IMPLAUSIBLE_SPEED" and features["identifier_agreement"] != "SYSTEM_MATCH":
        return "REJECT_ASSOCIATION", (f"implied movement of {features['distance_m']} m exceeds plausible speed",)
    if features["contradictory_attributes"]:
        rationale.append(f"contradictory attributes: {features['contradictory_attributes']}")
        return "UNKNOWN", tuple(rationale)
    if features["identifier_agreement"] == "SYSTEM_MATCH" and features["temporal_compatibility"] != "INCOMPATIBLE":
        return "AUTO_ASSOCIATE", ("exact cross-system identifier agreement with no contradictions",)
    if features["identifier_agreement"] == "ATTRIBUTE_MATCH" \
            or (features["temporal_compatibility"] == "COMPATIBLE" and features["geospatial_compatibility"] == "COMPATIBLE"):
        return "PROPOSE_ASSOCIATION", ("compatible but not identifier-proven; requires review",)
    return "UNKNOWN", ("insufficient features for any confident outcome",)


class AssociationEngine:
    def __init__(self, store: MissionDataStore):
        self.store = store

    def _current_version(self, object_id: str) -> Mapping[str, Any]:
        versions = [v for v in self.store.records_of("object_version") if v["object_id"] == object_id]
        if not versions:
            raise ValueError(f"unknown object: {object_id}")
        return versions[-1]

    def evaluate(self, left_object_id: str, right_object_id: str, *, recorded_time: str, actor: str,
                 marking: Marking, negative_evidence: Iterable[str] = (),
                 max_speed_kmh: float = 80.0,
                 temporal_window_hours: float = 12.0,
                 auto_accept: bool = True) -> dict[str, Any]:
        """``auto_accept=False`` demotes AUTO_ASSOCIATE to a reviewable
        proposal: callers whose identifier agreement is true by construction
        (e.g. a third party *asserting* another registry's identifier) must
        not let that assertion accept itself."""
        left = self._current_version(left_object_id)
        right = self._current_version(right_object_id)
        features = compute_features(left, right, negative_evidence=negative_evidence, max_speed_kmh=max_speed_kmh,
                                    temporal_window_hours=temporal_window_hours)
        outcome, rationale = decide(features)
        if outcome == "AUTO_ASSOCIATE" and not auto_accept:
            outcome = "PROPOSE_ASSOCIATION"
            rationale = rationale + ("auto-acceptance withheld: identifier agreement is "
                                     "third-party asserted, not registry-issued to both",)
        proposal = AssociationProposal(
            proposal_id=digest_id("assoc", left_object_id, right_object_id, recorded_time),
            left_object_id=left_object_id, right_object_id=right_object_id,
            object_type=left["object_type"], features=dict(features), outcome=outcome,
            rationale=rationale, engine_version=ENGINE_VERSION, recorded_time=recorded_time, marking=marking,
        )
        event = self.store.append(
            "ASSOCIATION_PROPOSED", proposal,
            recorded_time=recorded_time, actor=actor)
        if outcome == "AUTO_ASSOCIATE":
            self._record_resolution(proposal.proposal_id, "ACCEPTED", actor_id=actor, actor_kind="SERVICE",
                                    rationale="automatic: " + "; ".join(rationale),
                                    recorded_time=recorded_time, marking=marking)
        elif outcome == "PROPOSE_ASSOCIATION":
            self._relationship(proposal, "POSSIBLY_SAME_AS", "PROPOSED", recorded_time, actor, marking,
                               rationale="; ".join(rationale))
        return event["record"]

    def _relationship(self, proposal: AssociationProposal | Mapping[str, Any], relation_type: str, status: str,
                      recorded_time: str, actor: str, marking: Marking, rationale: str = "") -> None:
        record = proposal.to_record() if isinstance(proposal, AssociationProposal) else proposal
        relationship_id = digest_id("rel", relation_type, record["left_object_id"], record["right_object_id"])
        version = RelationshipVersion(
            relationship_id=relationship_id, version=self.store.next_relationship_version(relationship_id),
            relation_type=relation_type, source_object_id=record["left_object_id"],
            target_object_id=record["right_object_id"], valid_from=None, valid_to=None,
            recorded_time=recorded_time, evidence_refs=(record["proposal_id"],), derivation="RULE",
            confidence="UNKNOWN", status=status, marking=marking,
            provenance=ProvenanceSummary(mode="OPERATIONAL"), rationale=rationale,
        )
        self.store.append("RELATIONSHIP_VERSION_APPENDED", version, recorded_time=recorded_time, actor=actor)

    def _find_proposal(self, proposal_id: str) -> Mapping[str, Any]:
        for record in self.store.records_of("association_proposal"):
            if record["proposal_id"] == proposal_id:
                return record
        raise ValueError(f"unknown association proposal: {proposal_id}")

    def _record_resolution(self, proposal_id: str, resolution: str, *, actor_id: str, actor_kind: str,
                           rationale: str, recorded_time: str, marking: Marking) -> dict[str, Any]:
        proposal = self._find_proposal(proposal_id)
        record = AssociationResolution(
            resolution_id=digest_id("assoc-res", proposal_id, resolution, recorded_time),
            proposal_id=proposal_id, resolution=resolution, actor_id=actor_id, actor_kind=actor_kind,
            rationale=rationale, recorded_time=recorded_time, marking=marking,
        )
        event = self.store.append(
            "ASSOCIATION_RESOLVED", record,
            recorded_time=recorded_time, actor=actor_id)
        if resolution == "ACCEPTED":
            self._relationship(proposal, "SAME_AS", "ACTIVE", recorded_time, actor_id, marking, rationale)
        elif resolution in ("REJECTED", "SPLIT", "REVERSED"):
            for relation_type in ("SAME_AS", "POSSIBLY_SAME_AS"):
                relationship_id = digest_id("rel", relation_type, proposal["left_object_id"], proposal["right_object_id"])
                existing = [r for r in self.store.records_of("relationship_version")
                            if r["relationship_id"] == relationship_id]
                if existing and existing[-1]["status"] in ("ACTIVE", "PROPOSED"):
                    retired = RelationshipVersion(
                        relationship_id=relationship_id, version=self.store.next_relationship_version(relationship_id),
                        relation_type=relation_type, source_object_id=proposal["left_object_id"],
                        target_object_id=proposal["right_object_id"], valid_from=None, valid_to=None,
                        recorded_time=recorded_time, evidence_refs=(record.resolution_id,), derivation="ANALYST"
                        if actor_kind == "HUMAN" else "RULE",
                        confidence="UNKNOWN", status="REJECTED" if resolution in ("REJECTED", "SPLIT") else "RETIRED",
                        marking=marking, provenance=ProvenanceSummary(mode="OPERATIONAL"), rationale=rationale,
                    )
                    self.store.append("RELATIONSHIP_VERSION_APPENDED", retired, recorded_time=recorded_time, actor=actor_id)
        return event["record"]

    def resolve(self, proposal_id: str, resolution: str, *, actor_id: str, actor_kind: str,
                rationale: str, recorded_time: str, marking: Marking) -> dict[str, Any]:
        return self._record_resolution(proposal_id, resolution, actor_id=actor_id, actor_kind=actor_kind,
                                       rationale=rationale, recorded_time=recorded_time, marking=marking)
