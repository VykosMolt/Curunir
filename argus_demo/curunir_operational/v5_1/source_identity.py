"""Source identity and origin capability for V5.1 (contract Section 13).

Type-level closure of the V5 source-origin failure modes: directional
content relations require direction-licensing evidence or are downgraded
with an explicit record; hosting evidence never yields publication; the
conflation-sensitive entity-class pairs never merge without official
identifiers or explicit statements; identity is resolved explicitly,
never silently.

Research shadow only.  Composes frozen V4 records; never edits them.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Mapping

from ..v4.models import EntityRecord, require_aware, require_hash
from .models import (
    CONTENT_RELATIONS, DEPENDENCE_SIGNAL_KINDS, ENTITY_CLASSES,
    IDENTITY_EVIDENCE_KINDS, MANIFESTATION_KINDS, ROLE_EVIDENCE_KINDS,
    SOURCE_ROLES, CapabilityOutcome, EvidenceRef, Record, capability_outcome,
    now_utc, stable_id,
)


def _require_subset(subset: frozenset[str], vocabulary: frozenset[str], label: str) -> frozenset[str]:
    missing = subset - vocabulary
    if missing:
        raise ValueError(f"{label} outside models vocabulary: {sorted(missing)}")
    return subset


# Policy subsets of the models vocabularies (checked, never redefined).
DIRECTIONAL_CONTENT_RELATIONS = _require_subset(frozenset({
    "TRANSLATED_FROM", "DERIVED_FROM", "SYNDICATED_FROM",
    "SUPERSEDES", "UPDATES", "CORRECTS", "RETRACTS",
}), CONTENT_RELATIONS, "directional relations")

DOWNGRADE_RELATIONS = _require_subset(
    frozenset({"COMMON_EVIDENCE_BASIS", "UNKNOWN_DEPENDENCE"}),
    CONTENT_RELATIONS, "downgrade relations")

CONTENT_EDGE_EVIDENCE_KINDS = ROLE_EVIDENCE_KINDS | DEPENDENCE_SIGNAL_KINDS

# Evidence kinds that license asserting direction, per directional relation.
# PUBLICATION_TIMING licenses only UPDATES (ordering is constitutive there);
# supersession, correction and retraction require an explicit notice.
DIRECTION_LICENSING_EVIDENCE: Mapping[str, frozenset[str]] = {
    "TRANSLATED_FROM": frozenset({"TRANSLATION_NOTICE"}),
    "SYNDICATED_FROM": frozenset({"SYNDICATION_NOTICE", "WIRE_SERVICE_INDICATION"}),
    "DERIVED_FROM": frozenset({"EXPLICIT_CITATION", "EXPLICIT_TEXT_SPAN",
                               "EXPLICIT_ATTRIBUTION", "EXPLICIT_REUSE_STATEMENT"}),
    "SUPERSEDES": frozenset({"EXPLICIT_TEXT_SPAN", "EXPLICIT_METADATA", "EXPLICIT_CITATION"}),
    "UPDATES": frozenset({"EXPLICIT_TEXT_SPAN", "EXPLICIT_METADATA",
                          "EXPLICIT_CITATION", "PUBLICATION_TIMING"}),
    "CORRECTS": frozenset({"EXPLICIT_TEXT_SPAN", "EXPLICIT_METADATA"}),
    "RETRACTS": frozenset({"EXPLICIT_TEXT_SPAN", "EXPLICIT_METADATA"}),
}
for _relation, _kinds in DIRECTION_LICENSING_EVIDENCE.items():
    _require_subset(_kinds, CONTENT_EDGE_EVIDENCE_KINDS, f"licensing kinds for {_relation}")
if frozenset(DIRECTION_LICENSING_EVIDENCE) != DIRECTIONAL_CONTENT_RELATIONS:
    raise ValueError("every directional relation must declare its licensing evidence")

COMMON_BASIS_EVIDENCE_KINDS = _require_subset(frozenset({
    "COMMON_SOURCE_LINK", "IDENTICAL_QUOTATION", "NORMALIZED_PARAGRAPH_OVERLAP",
    "SHARED_TABLE_OR_GRAPHIC", "SHARED_UNIQUE_ERROR", "COMMON_QUOTED_INDIVIDUAL",
    "COMMON_ANONYMOUS_ATTRIBUTION", "COMMON_PRIMARY_DATASET",
    "COMMON_OFFICIAL_ANNEX", "TRANSLATION_ALIGNMENT", "PRESS_RELEASE_FINGERPRINT",
}), DEPENDENCE_SIGNAL_KINDS, "common-basis kinds")

HOSTING_ONLY_EVIDENCE_KINDS = _require_subset(
    frozenset({"DOMAIN_OWNERSHIP_RECORD"}), ROLE_EVIDENCE_KINDS, "hosting kinds")

NAME_EVIDENCE_KINDS = _require_subset(
    frozenset({"LEGAL_NAME", "LANGUAGE_ALIAS", "HISTORICAL_NAME"}),
    IDENTITY_EVIDENCE_KINDS, "name-similarity kinds")

MERGE_LICENSING_EVIDENCE_KINDS = _require_subset(
    frozenset({"OFFICIAL_IDENTIFIER", "EXPLICIT_INSTITUTIONAL_ATTRIBUTION"}),
    IDENTITY_EVIDENCE_KINDS, "merge-licensing kinds")

CONFLATION_GUARDED_CLASS_PAIRS = frozenset({
    frozenset({"PROGRAMME", "VENDOR"}), frozenset({"PROGRAMME", "PLATFORM"}),
    frozenset({"PRODUCT", "ORGANIZATION"}), frozenset({"PROCUREMENT_VEHICLE", "PROGRAMME"}),
})
_require_subset(frozenset().union(*CONFLATION_GUARDED_CLASS_PAIRS),
                ENTITY_CLASSES, "conflation-guarded classes")

IDENTITY_ASSESSMENT_OUTCOMES = frozenset({
    "SAME_ENTITY_SUPPORTED", "DIFFERENT_ENTITY_SUPPORTED",
    "AMBIGUOUS_IDENTITY", "EPISTEMICALLY_UNRESOLVABLE",
})

# Explicit metadata field -> (role, role evidence kind).
ROLE_METADATA_FIELDS: Mapping[str, tuple[str, str]] = {
    "publisher": ("PUBLISHED_BY", "EXPLICIT_METADATA"),
    "author": ("AUTHORED_BY", "BYLINE"),
    "submitter": ("SUBMITTED_BY", "SUBMISSION_RECORD"),
    "issuer": ("ISSUED_BY", "DOCUMENT_COVER"),
    "host_domain": ("HOSTED_BY", "DOMAIN_OWNERSHIP_RECORD"),
    "archive_banner": ("ARCHIVED_BY", "ARCHIVE_BANNER"),
    "translation_notice": ("TRANSLATED_BY", "TRANSLATION_NOTICE"),
    "syndication_notice": ("SYNDICATED_BY", "SYNDICATION_NOTICE"),
    "owner": ("OWNED_BY", "OFFICIAL_REGISTER"),
    "operator": ("OPERATED_BY", "OPERATING_NOTICE"),
}
_require_subset(frozenset(role for role, _ in ROLE_METADATA_FIELDS.values()),
                SOURCE_ROLES, "metadata roles")
_require_subset(frozenset(kind for _, kind in ROLE_METADATA_FIELDS.values()),
                ROLE_EVIDENCE_KINDS, "metadata evidence kinds")


def _validate_evidence(evidence: tuple[EvidenceRef, ...], vocabulary: frozenset[str],
                       label: str) -> None:
    for ref in evidence:
        if ref.evidence_kind not in vocabulary:
            raise ValueError(f"{label} kind outside models vocabulary: {ref.evidence_kind}")


def _validate_valid_time(valid_time: tuple[str | None, str | None]) -> None:
    if len(valid_time) != 2:
        raise ValueError("temporal validity must be a start/end pair")
    for bound in valid_time:
        if bound is not None:
            require_aware(bound)


def _validate_confidence(dimensions: Mapping[str, float]) -> None:
    for name, value in dimensions.items():
        if not isinstance(value, (int, float)) or not 0.0 <= float(value) <= 1.0:
            raise ValueError(f"confidence dimension out of range: {name}")


# ---------------------------------------------------------------------------
# Manifestation model
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class IntellectualWork(Record):
    work_id: str
    kind: str
    title: str
    original_language: str
    creator_entity_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.kind != "INTELLECTUAL_WORK" or self.kind not in MANIFESTATION_KINDS:
            raise ValueError("intellectual work has fixed manifestation kind")
        if not self.title.strip() or not self.original_language.strip():
            raise ValueError("intellectual work requires title and original language")


@dataclass(frozen=True)
class PublicationRecord(Record):
    """Venue + time + language identity of one act of publication."""

    publication_id: str
    kind: str
    work_id: str
    venue_entity_id: str
    language: str
    publication_time: str | None

    def __post_init__(self) -> None:
        if self.kind != "PUBLICATION_RECORD" or self.kind not in MANIFESTATION_KINDS:
            raise ValueError("publication record has fixed manifestation kind")
        if not (self.work_id.strip() and self.venue_entity_id.strip() and self.language.strip()):
            raise ValueError("publication requires work, venue and language identity")
        if self.publication_time is not None:
            require_aware(self.publication_time)


@dataclass(frozen=True)
class DocumentManifestation(Record):
    """Content-hash-addressed capture of one publication."""

    manifestation_id: str
    kind: str
    publication_id: str
    content_hash: str
    media_type: str
    capture_locator: str | None
    captured_time: str

    def __post_init__(self) -> None:
        if self.kind != "DOCUMENT_MANIFESTATION" or self.kind not in MANIFESTATION_KINDS:
            raise ValueError("document manifestation has fixed manifestation kind")
        if not self.publication_id.strip() or not self.media_type.strip():
            raise ValueError("manifestation requires publication and media identity")
        require_hash(self.content_hash)
        require_aware(self.captured_time)


def intellectual_work(*, title: str, original_language: str,
                      creator_entity_ids: tuple[str, ...] = ()) -> IntellectualWork:
    return IntellectualWork(stable_id("intellectual-work", title, original_language),
                            "INTELLECTUAL_WORK", title, original_language,
                            tuple(creator_entity_ids))


def publication_record(*, work_id: str, venue_entity_id: str, language: str,
                       publication_time: str | None = None) -> PublicationRecord:
    return PublicationRecord(
        stable_id("publication", work_id, venue_entity_id, language, publication_time),
        "PUBLICATION_RECORD", work_id, venue_entity_id, language, publication_time)


def document_manifestation(*, publication_id: str, content_hash: str, media_type: str,
                           capture_locator: str | None = None) -> DocumentManifestation:
    return DocumentManifestation(
        stable_id("manifestation", publication_id, content_hash), "DOCUMENT_MANIFESTATION",
        publication_id, content_hash, media_type, capture_locator, now_utc())


def group_manifestations(manifestations: tuple[DocumentManifestation, ...] | list[DocumentManifestation],
                         ) -> dict[str, tuple[DocumentManifestation, ...]]:
    """Mirrors are manifestations of one publication: same work + publication."""
    groups: dict[str, tuple[DocumentManifestation, ...]] = {}
    for item in sorted(manifestations, key=lambda m: (m.publication_id, m.manifestation_id)):
        groups[item.publication_id] = groups.get(item.publication_id, ()) + (item,)
    return groups


def is_mirror_pair(left: DocumentManifestation, right: DocumentManifestation) -> bool:
    return (left.publication_id == right.publication_id
            and left.manifestation_id != right.manifestation_id)


# ---------------------------------------------------------------------------
# Role edges (agent roles; never mixed with content relations)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class RoleEdge(Record):
    edge_id: str
    role: str
    subject_id: str
    agent_entity_id: str
    evidence: tuple[EvidenceRef, ...]
    valid_time: tuple[str | None, str | None]
    confidence_dimensions: Mapping[str, float]
    alternative_interpretation: str | None
    recorded_time: str

    def __post_init__(self) -> None:
        require_aware(self.recorded_time)
        _validate_valid_time(self.valid_time)
        _validate_confidence(self.confidence_dimensions)
        if self.role not in SOURCE_ROLES:
            raise ValueError(f"unknown source role: {self.role}")
        if not self.subject_id.strip() or not self.agent_entity_id.strip():
            raise ValueError("role edge requires subject and agent identifiers")
        if not self.evidence:
            raise ValueError("role edge requires at least one evidence reference")
        _validate_evidence(self.evidence, ROLE_EVIDENCE_KINDS, "role evidence")
        kinds = {ref.evidence_kind for ref in self.evidence}
        if self.role == "PUBLISHED_BY" and kinds <= HOSTING_ONLY_EVIDENCE_KINDS:
            raise ValueError(
                "domain hosting evidence alone never supports PUBLISHED_BY; assert HOSTED_BY")
        if self.alternative_interpretation is not None and not self.alternative_interpretation.strip():
            raise ValueError("alternative interpretation must be substantive or None")


def role_edge(*, role: str, subject_id: str, agent_entity_id: str,
              evidence: tuple[EvidenceRef, ...],
              valid_time: tuple[str | None, str | None] = (None, None),
              confidence_dimensions: Mapping[str, float] | None = None,
              alternative_interpretation: str | None = None) -> RoleEdge:
    return RoleEdge(stable_id("role-edge", role, subject_id, agent_entity_id),
                    role, subject_id, agent_entity_id, tuple(evidence), valid_time,
                    dict(confidence_dimensions or {"role_uncalibrated": 0.6}),
                    alternative_interpretation, now_utc())


# ---------------------------------------------------------------------------
# Content edges with the DIRECTIONAL_EVIDENCE contract
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class EdgeDowngrade(Record):
    """Explicit record that a directional relation was refused for lack of
    direction-licensing evidence and the weakest supported relation granted."""

    downgrade_id: str
    requested_relation: str
    granted_relation: str
    missing_evidence_kinds: tuple[str, ...]
    rationale: str
    recorded_time: str

    def __post_init__(self) -> None:
        require_aware(self.recorded_time)
        if self.requested_relation not in DIRECTIONAL_CONTENT_RELATIONS:
            raise ValueError("downgrade applies only to directional relations")
        if self.granted_relation not in DOWNGRADE_RELATIONS:
            raise ValueError("downgrade must grant the weakest supported relation")
        if not self.missing_evidence_kinds or not self.rationale.strip():
            raise ValueError("downgrade requires missing evidence kinds and rationale")


@dataclass(frozen=True)
class ContentEdge(Record):
    edge_id: str
    relation: str
    source_id: str
    target_id: str
    evidence: tuple[EvidenceRef, ...]
    scope: str
    valid_time: tuple[str | None, str | None]
    confidence_dimensions: Mapping[str, float]
    downgrade: EdgeDowngrade | None
    recorded_time: str

    def __post_init__(self) -> None:
        require_aware(self.recorded_time)
        _validate_valid_time(self.valid_time)
        _validate_confidence(self.confidence_dimensions)
        if self.relation not in CONTENT_RELATIONS:
            raise ValueError(f"unknown content relation: {self.relation}")
        if not self.source_id.strip() or not self.target_id.strip() or self.source_id == self.target_id:
            raise ValueError("content edge requires two distinct subjects")
        _validate_evidence(self.evidence, CONTENT_EDGE_EVIDENCE_KINDS, "content evidence")
        kinds = {ref.evidence_kind for ref in self.evidence}
        if self.relation in DIRECTIONAL_CONTENT_RELATIONS:
            if not kinds & DIRECTION_LICENSING_EVIDENCE[self.relation]:
                raise ValueError(
                    f"{self.relation} requires direction-licensing evidence; "
                    "content_edge() downgrades instead")
        if self.relation == "SUPERSEDES" and (not self.scope.strip() or self.scope == "UNSPECIFIED"):
            raise ValueError("supersession requires an explicit scope qualifier")
        if self.relation != "UNKNOWN_DEPENDENCE" and not self.evidence:
            raise ValueError("content edge requires evidence unless dependence is unknown")
        if self.downgrade is not None and self.relation not in DOWNGRADE_RELATIONS:
            raise ValueError("downgrade record only accompanies a downgraded relation")


def content_edge(*, relation: str, source_id: str, target_id: str,
                 evidence: tuple[EvidenceRef, ...] = (), scope: str = "UNSPECIFIED",
                 valid_time: tuple[str | None, str | None] = (None, None),
                 confidence_dimensions: Mapping[str, float] | None = None) -> ContentEdge:
    """Directional relations without licensing evidence are refused and
    downgraded to the weakest supported relation, never silently granted."""
    if relation not in CONTENT_RELATIONS:
        raise ValueError(f"unknown content relation: {relation}")
    supplied = tuple(evidence)
    kinds = {ref.evidence_kind for ref in supplied}
    granted, downgrade = relation, None
    if relation in DIRECTIONAL_CONTENT_RELATIONS and not kinds & DIRECTION_LICENSING_EVIDENCE[relation]:
        granted = ("COMMON_EVIDENCE_BASIS" if kinds & COMMON_BASIS_EVIDENCE_KINDS
                   else "UNKNOWN_DEPENDENCE")
        downgrade = EdgeDowngrade(
            stable_id("edge-downgrade", relation, granted, source_id, target_id),
            relation, granted, tuple(sorted(DIRECTION_LICENSING_EVIDENCE[relation])),
            f"no evidence kind licenses the direction asserted by {relation}; "
            "weakest supported relation granted", now_utc())
    return ContentEdge(stable_id("content-edge", granted, source_id, target_id),
                       granted, source_id, target_id, supplied, scope, valid_time,
                       dict(confidence_dimensions or {"relation_uncalibrated": 0.5}),
                       downgrade, now_utc())


def translation_publication(work: IntellectualWork, source_publication: PublicationRecord, *,
                            venue_entity_id: str, language: str,
                            publication_time: str | None = None,
                            evidence: tuple[EvidenceRef, ...] = (),
                            ) -> tuple[PublicationRecord, ContentEdge]:
    """Translation: same intellectual work, NEW publication record, dependent.

    Without a translation notice the dependence edge is downgraded, never
    granted as directional TRANSLATED_FROM.
    """
    if source_publication.work_id != work.work_id:
        raise ValueError("translation must stay within one intellectual work")
    publication = publication_record(work_id=work.work_id, venue_entity_id=venue_entity_id,
                                     language=language, publication_time=publication_time)
    if publication.publication_id == source_publication.publication_id:
        raise ValueError("translation requires a new publication identity")
    edge = content_edge(relation="TRANSLATED_FROM", source_id=publication.publication_id,
                        target_id=source_publication.publication_id, evidence=tuple(evidence))
    return publication, edge


# ---------------------------------------------------------------------------
# Identity resolution (never a silent merge)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class IdentityAssessment(Record):
    assessment_id: str
    left_entity_id: str
    left_entity_class: str
    right_entity_id: str
    right_entity_class: str
    outcome: str
    evidence: tuple[EvidenceRef, ...]
    rationale: str
    evidence_insufficiency_demonstration: str | None
    recorded_time: str

    def __post_init__(self) -> None:
        require_aware(self.recorded_time)
        if self.left_entity_id == self.right_entity_id:
            raise ValueError("identity assessment requires two distinct entity records")
        for entity_class in (self.left_entity_class, self.right_entity_class):
            if entity_class not in ENTITY_CLASSES:
                raise ValueError(f"unknown entity class: {entity_class}")
        if self.outcome not in IDENTITY_ASSESSMENT_OUTCOMES:
            raise ValueError(f"unknown identity outcome: {self.outcome}")
        if not self.rationale.strip():
            raise ValueError("identity assessment requires a rationale")
        _validate_evidence(self.evidence, IDENTITY_EVIDENCE_KINDS, "identity evidence")
        kinds = {ref.evidence_kind for ref in self.evidence}
        if self.outcome == "SAME_ENTITY_SUPPORTED":
            if not self.evidence:
                raise ValueError("supported merge requires evidence")
            if kinds <= NAME_EVIDENCE_KINDS:
                raise ValueError("name similarity alone can never support an entity merge")
            if not kinds & MERGE_LICENSING_EVIDENCE_KINDS:
                if frozenset({self.left_entity_class, self.right_entity_class}) in CONFLATION_GUARDED_CLASS_PAIRS:
                    raise ValueError(
                        "class-conflict pair merge requires official-identifier "
                        "or explicit-statement evidence")
                raise ValueError(
                    "entity merge requires official-identifier or explicit-statement evidence")
        if self.outcome == "DIFFERENT_ENTITY_SUPPORTED" and not self.evidence:
            raise ValueError("supported difference requires evidence")
        if self.outcome == "EPISTEMICALLY_UNRESOLVABLE":
            demonstration = (self.evidence_insufficiency_demonstration or "").strip()
            if len(demonstration) < 40:
                raise ValueError(
                    "EPISTEMICALLY_UNRESOLVABLE requires a substantive "
                    "evidence-insufficiency demonstration")


def assess_identity(left: EntityRecord, right: EntityRecord, *,
                    evidence: tuple[EvidenceRef, ...] = (),
                    unresolvable_demonstration: str | None = None) -> IdentityAssessment:
    if left.entity_id == right.entity_id:
        raise ValueError("identity assessment requires two distinct entity records")
    supplied = tuple(evidence)
    _validate_evidence(supplied, IDENTITY_EVIDENCE_KINDS, "identity evidence")
    shared = sorted(set(left.external_identifiers) & set(right.external_identifiers))
    matches = tuple(s for s in shared if left.external_identifiers[s] == right.external_identifiers[s])
    conflicts = tuple(s for s in shared if left.external_identifiers[s] != right.external_identifiers[s])
    kinds = {ref.evidence_kind for ref in supplied}
    combined = supplied
    if conflicts and not matches:
        combined = supplied + tuple(
            EvidenceRef("OFFICIAL_IDENTIFIER", left.entity_id, None,
                        f"identifier scheme '{scheme}' holds different values on the two records")
            for scheme in conflicts)
        outcome = "DIFFERENT_ENTITY_SUPPORTED"
        rationale = "official identifiers conflict between the records"
    elif conflicts:
        outcome = "AMBIGUOUS_IDENTITY"
        rationale = "official identifier evidence is internally conflicting"
    else:
        if matches:
            combined = supplied + tuple(
                EvidenceRef("OFFICIAL_IDENTIFIER", left.entity_id, None,
                            f"identifier scheme '{scheme}' matches on both records")
                for scheme in matches)
        if matches or kinds & MERGE_LICENSING_EVIDENCE_KINDS:
            outcome = "SAME_ENTITY_SUPPORTED"
            rationale = "merge licensed by official-identifier or explicit-statement evidence"
        elif not combined and unresolvable_demonstration:
            outcome = "EPISTEMICALLY_UNRESOLVABLE"
            rationale = "identity evidence is insufficient and demonstrably unrecoverable"
        else:
            outcome = "AMBIGUOUS_IDENTITY"
            rationale = ("no merge-licensing evidence present; name or structural "
                         "similarity alone never merges")
    return IdentityAssessment(
        stable_id("identity-assessment", left.entity_id, right.entity_id, outcome),
        left.entity_id, left.entity_class, right.entity_id, right.entity_class,
        outcome, combined, rationale,
        unresolvable_demonstration if outcome == "EPISTEMICALLY_UNRESOLVABLE" else None,
        now_utc())


# ---------------------------------------------------------------------------
# Metadata-driven role resolution
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class SourceRoleResolution(Record):
    resolution_id: str
    subject_id: str
    role_edges: tuple[RoleEdge, ...]
    outcomes: tuple[CapabilityOutcome, ...]
    interpreted_fields: tuple[str, ...]
    uninterpreted_fields: tuple[str, ...]
    absent_fields: tuple[str, ...]
    recorded_time: str

    def __post_init__(self) -> None:
        require_aware(self.recorded_time)
        if self.uninterpreted_fields and not any(
                item.outcome == "SYSTEM_CAPABILITY_FAILURE" for item in self.outcomes):
            raise ValueError("uninterpreted metadata must be recorded as capability failure")
        if not self.role_edges and not self.outcomes:
            raise ValueError("role resolution must never silently resolve to nothing")


def resolve_source_roles(source_metadata: Mapping[str, object],
                         spans: Mapping[str, str] | None = None, *,
                         subject_id: str, source_object_id: str) -> SourceRoleResolution:
    """Map explicit metadata fields to role edges.

    Present-but-uninterpretable metadata is a SYSTEM_CAPABILITY_FAILURE
    (ROLE_METADATA_UNINTERPRETED); genuinely absent metadata is
    EPISTEMICALLY_UNRESOLVABLE with a demonstration naming the absent fields.
    """
    span_map = dict(spans or {})
    edges: list[RoleEdge] = []
    outcomes: list[CapabilityOutcome] = []
    interpreted: list[str] = []
    uninterpreted: list[str] = []
    for field_name in sorted(source_metadata):
        value = source_metadata[field_name]
        if field_name not in ROLE_METADATA_FIELDS:
            uninterpreted.append(field_name)
            outcomes.append(capability_outcome(
                subject_kind="SOURCE_ROLE", subject_id=f"{subject_id}#{field_name}",
                outcome="SYSTEM_CAPABILITY_FAILURE",
                rationale=f"metadata field '{field_name}' is present but the resolver "
                          "has no role interpretation for it",
                capability_failure_class="ROLE_METADATA_UNINTERPRETED"))
            continue
        if not isinstance(value, str) or not re.search(r"\w", value):
            uninterpreted.append(field_name)
            outcomes.append(capability_outcome(
                subject_kind="SOURCE_ROLE", subject_id=f"{subject_id}#{field_name}",
                outcome="SYSTEM_CAPABILITY_FAILURE",
                rationale=f"metadata field '{field_name}' is present but cannot be "
                          "interpreted as an agent reference",
                capability_failure_class="ROLE_METADATA_UNINTERPRETED"))
            continue
        role, evidence_kind = ROLE_METADATA_FIELDS[field_name]
        detail = span_map.get(field_name) or f"{field_name}: {value.strip()}"
        edges.append(role_edge(
            role=role, subject_id=subject_id, agent_entity_id=value.strip(),
            evidence=(EvidenceRef(evidence_kind, source_object_id, None, detail),)))
        interpreted.append(field_name)
    absent = tuple(sorted(frozenset(ROLE_METADATA_FIELDS) - frozenset(source_metadata)))
    if not edges and not uninterpreted:
        outcomes.append(capability_outcome(
            subject_kind="SOURCE_ROLE", subject_id=subject_id,
            outcome="EPISTEMICALLY_UNRESOLVABLE",
            rationale="role metadata is genuinely absent from the captured record",
            evidence_insufficiency_demonstration=(
                "no role metadata fields are present on the captured record; absent "
                "fields: " + ", ".join(absent) + "; agent roles cannot be established "
                "from the available public capture alone")))
    return SourceRoleResolution(
        stable_id("source-role-resolution", subject_id, sorted(source_metadata)),
        subject_id, tuple(edges), tuple(outcomes), tuple(interpreted),
        tuple(uninterpreted), absent, now_utc())
