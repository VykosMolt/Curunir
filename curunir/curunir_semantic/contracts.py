"""Typed records for the semantic plane's event log.

Frozen dataclasses that validate themselves at construction, are serialized
once and replayed as dicts. "The source says X" (an observation) and "our
current reading of X" (a claim plus its state) are separate record types.
"""
from __future__ import annotations

from dataclasses import dataclass

from curunir_operational.access import Marking
from curunir_operational.canonical import require_aware, require_aware_or_none, require_sha256
from curunir_operational.contracts import Record, TIME_PRECISIONS, _member
from curunir_operational.references import DynamicRef, Label, Ref, RefPairs, Refs

ANCHOR_KINDS = ("TEXT_SPAN", "FIELD", "RECORD", "DOCUMENT")
REPRESENTATIONS = ("ORIGINAL", "TRANSLATED")
DOCUMENT_FORMATS = ("HTML", "XML", "PLAIN_TEXT", "PDF", "JSON", "FEED")

OBSERVATION_TYPES = ("ENTITY_ATTRIBUTE", "ENTITY_IDENTIFIER", "ENTITY_NAME", "RELATION",
                     "EVENT", "ROLE_SIGNAL", "LIFECYCLE_SIGNAL", "PUBLICATION", "STATEMENT")

PRODUCER_KINDS = ("DETERMINISTIC_PARSER", "MODEL_PROVIDER")

CLAIM_STATES = ("CURRENT", "CORRECTED", "RETRACTED", "SUPERSEDED", "DISPUTED",
                "STALE", "SOURCE_WITHDRAWN")

CHANGE_CLASSES = ("NEW_PROPOSITION", "REMOVED_PROPOSITION", "VALUE_CHANGED",
                  "RELATION_ADDED", "RELATION_REMOVED", "ROLE_CHANGED",
                  "ENTITY_ATTRIBUTE_CHANGED", "EVENT_ADDED", "EVENT_UPDATED",
                  "SOURCE_CORRECTION", "SOURCE_RETRACTION", "HISTORICAL_STATE_DISCOVERED",
                  "SEMANTICALLY_UNCHANGED", "UNRESOLVED_CHANGE")

HYPOTHESIS_STATUSES = ("OPEN", "SUPPORTED", "WEAKLY_SUPPORTED", "DISPUTED",
                       "REJECTED", "UNRESOLVED", "SUPERSEDED")
HYPOTHESIS_SETTLED_SUPPORT_STATUSES = frozenset(
    ("OPEN", "SUPPORTED", "WEAKLY_SUPPORTED", "UNRESOLVED"))

DISCRIMINATOR_STATUSES = ("OPEN", "REQUESTED", "SATISFIED", "UNSATISFIABLE")

ROUTE_STATUSES = ("PROPOSED", "SELECTED", "EXECUTED", "FAILED", "HUMAN_REQUIRED", "DECLINED")

REVIEW_KINDS = ("CONTRADICTED", "STALE_BASIS", "SOURCE_CORRECTED", "SOURCE_RETRACTED",
                "MANIFESTATION_CHANGED", "IDENTITY_AMBIGUITY", "COVERAGE_GAP",
                "EXPECTED_NOT_OBSERVED", "PROCESSING_FAILED")

REVIEW_STATUSES = ("OPEN", "RESOLVED", "DISMISSED")


@dataclass(frozen=True)
class EvidenceAnchor(Record):
    """Where a piece of evidence sits: a manifestation plus a span or field.

    ``normalized_sha256`` names the payload the offsets or field path address —
    the normalized text for span and document anchors, the field table for field
    anchors. ``mapping_status`` says how that maps back to the original bytes.
    """
    RECORD_TYPE = "evidence_anchor"
    manifestation_id: Ref("fabric_manifestation")
    source_id: Ref("source")
    content_sha256: str
    kind: str
    normalized_sha256: str = ""
    start: int | None = None
    end: int | None = None
    field_path: str = ""
    exact_value: str = ""
    mapping_status: str = "UNSPECIFIED"
    representation: str = "ORIGINAL"
    translation_id: Label(str) = ""

    def __post_init__(self):
        _member(self.kind, ANCHOR_KINDS, "anchor kind")
        _member(self.representation, REPRESENTATIONS, "representation")
        require_sha256(self.content_sha256)
        if not self.manifestation_id:
            raise ValueError("an anchor requires its manifestation")
        if self.kind == "TEXT_SPAN":
            if self.start is None or self.end is None or self.end < self.start:
                raise ValueError("a text-span anchor requires valid offsets")
            if not self.normalized_sha256:
                raise ValueError("a text-span anchor requires the normalized text identity")
        if self.kind == "FIELD" and not self.field_path:
            raise ValueError("a field anchor requires its field path")
        if self.representation == "TRANSLATED" and not self.translation_id:
            raise ValueError("a translated anchor requires its translation identity")


@dataclass(frozen=True)
class NormalizedDocumentRecord(Record):
    """One manifestation normalized into text and, if structured, a field table.

    Both payloads are content-addressed in the store; this record binds their
    hashes to the manifestation and parser so replay can recover them.
    """
    RECORD_TYPE = "semantic_document"
    ID_FIELD = "document_id"
    document_id: str
    manifestation_id: Ref("fabric_manifestation")
    source_id: Ref("source")
    retrieval_id: Label(str)
    native_id: Label(str)
    content_sha256: str
    normalized_sha256: str
    fields_sha256: str
    format: str
    content_class: str
    language: str
    title: str
    publisher: str
    source_time: str | None
    retrieval_time: str
    temporal_status: str  # LIVE or HISTORICAL, copied from the manifestation
    region_count: int
    field_count: int
    regions: tuple[tuple[str, int, int], ...]  # capped number of structural regions
    structural: Label(tuple[tuple[str, str], ...])  # (path, value)
    parser: str
    parser_version: str
    warnings: tuple[str, ...]
    recorded_time: str
    marking: Marking

    def __post_init__(self):
        _member(self.format, DOCUMENT_FORMATS, "document format")
        require_sha256(self.content_sha256)
        require_aware(self.recorded_time)
        require_aware(self.retrieval_time)
        require_aware_or_none(self.source_time)
        if not self.manifestation_id:
            raise ValueError("a normalized document requires its manifestation")
        if self.format == "JSON" and not self.fields_sha256:
            raise ValueError("structured records keep their fields addressable, not prose-only")


@dataclass(frozen=True)
class SemanticObservation(Record):
    """One "the source says X", pinned to evidence.

    Observations never change when our reading of them changes. Model-produced
    observations also carry the inference record that produced them.
    """
    RECORD_TYPE = "semantic_observation"
    ID_FIELD = "observation_id"
    observation_id: str
    document_id: Ref("semantic_document")
    manifestation_id: Ref("fabric_manifestation")
    source_id: Ref("source")
    observation_type: str
    subject_ref: Ref("*")      # as the source names it: "LEI:...", "QID:...", a url, a name
    attribute: str        # attribute name, or the predicate of a relation
    value: str
    object_ref: Ref("*")       # the other participant, for a relation or event
    valid_from: str | None
    valid_to: str | None
    source_time: str | None
    time_precision: str
    language: str
    representation: str
    anchors: tuple[EvidenceAnchor, ...]
    producer_kind: str
    producer_id: Label(str)
    producer_version: str
    inference_id: Ref("inference")
    recorded_time: str
    marking: Marking

    def __post_init__(self):
        _member(self.observation_type, OBSERVATION_TYPES, "observation type")
        _member(self.representation, REPRESENTATIONS, "representation")
        _member(self.producer_kind, PRODUCER_KINDS, "producer kind")
        _member(self.time_precision, TIME_PRECISIONS, "time precision")
        require_aware(self.recorded_time)
        for value in (self.valid_from, self.valid_to, self.source_time):
            require_aware_or_none(value)
        if not self.anchors:
            raise ValueError("an observation without evidence anchors is an "
                             "inference wearing an observation's name")
        if not self.subject_ref:
            raise ValueError("an observation requires its source-native subject")
        if self.producer_kind == "MODEL_PROVIDER" and not self.inference_id:
            raise ValueError("model-produced observations require their inference record")


@dataclass(frozen=True)
class SemanticClaim(Record):
    """Our current reading of a set of observations.

    A claim gathers observations into one proposition, counts how many
    independent origins back it, and names the world-model versions it produced.
    Claims version forward: re-appending with version+1 supersedes on replay and
    every prior version stays in the log.
    """
    RECORD_TYPE = "semantic_claim"
    ID_FIELD = "claim_id"
    claim_id: str
    version: int
    statement: str
    subject_ref: Ref("*")
    subject_object_id: Ref("object_version")
    predicate: str
    object_or_value: str
    object_object_id: Ref("object_version")
    valid_from: str | None
    valid_to: str | None
    time_precision: str
    polarity: str  # AFFIRMED or NEGATED
    observation_ids: Refs("semantic_observation")
    dependence_group_ids: Label(tuple[str, ...])
    independent_basis_count: int
    basis_note: str
    world_refs: Refs("*")  # the world-model versions this claim produced
    epistemic_state: str
    review_state: str
    recorded_time: str
    marking: Marking

    def __post_init__(self):
        require_aware(self.recorded_time)
        require_aware_or_none(self.valid_from)
        require_aware_or_none(self.valid_to)
        _member(self.time_precision, TIME_PRECISIONS, "time precision")
        if self.polarity not in ("AFFIRMED", "NEGATED"):
            raise ValueError(f"invalid polarity: {self.polarity!r}")
        if not self.observation_ids:
            raise ValueError("a claim requires at least one supporting observation")
        if self.version < 1:
            raise ValueError("claim versions start at 1")
        if self.independent_basis_count < 0:
            raise ValueError("independent basis count cannot be negative")
        if self.independent_basis_count > len(self.observation_ids):
            raise ValueError("independent basis cannot exceed the observation count")


@dataclass(frozen=True)
class ClaimStateRecord(Record):
    """A claim's standing; the latest wins on replay and the history stays."""
    RECORD_TYPE = "semantic_claim_state"
    ID_FIELD = "state_id"
    state_id: str
    claim_id: Ref("semantic_claim")
    state: str
    reason: str
    caused_by: Ref("*")  # the change, notice or review item behind this state
    superseded_by: Ref("*")
    actor_id: Label(str)
    actor_kind: str
    recorded_time: str
    marking: Marking

    def __post_init__(self):
        _member(self.state, CLAIM_STATES, "claim state")
        require_aware(self.recorded_time)
        if self.state in ("CORRECTED", "RETRACTED", "SUPERSEDED", "STALE") and not self.reason:
            raise ValueError(f"claim state {self.state} requires a reason")


@dataclass(frozen=True)
class SemanticChangeRecord(Record):
    """What changed in meaning between two manifestations of one target."""
    RECORD_TYPE = "semantic_change"
    ID_FIELD = "change_id"
    change_id: str
    source_id: Ref("source")
    prior_manifestation_id: Ref("fabric_manifestation")
    current_manifestation_id: Ref("fabric_manifestation")
    watch_id: Ref("fabric_watch")
    fabric_change_id: Ref("fabric_change")
    change_class: str
    detail: str
    subject_ref: Ref("*")
    attribute: str
    prior_value: str
    current_value: str
    prior_observation_id: Ref("semantic_observation")
    current_observation_id: Ref("semantic_observation")
    affected_object_ids: Refs("object_version")
    affected_claim_ids: Refs("semantic_claim")
    recorded_time: str
    marking: Marking

    def __post_init__(self):
        _member(self.change_class, CHANGE_CLASSES, "semantic change class")
        require_aware(self.recorded_time)
        if not self.current_manifestation_id:
            raise ValueError("a semantic change requires the current manifestation")
        if self.change_class not in ("SEMANTICALLY_UNCHANGED", "HISTORICAL_STATE_DISCOVERED",
                                     "UNRESOLVED_CHANGE") \
                and not self.prior_manifestation_id:
            raise ValueError("a change against prior state requires the prior manifestation")


@dataclass(frozen=True)
class HypothesisRecord(Record):
    """One competing explanation and the evidence for and against it.

    Re-appending the same hypothesis supersedes on replay; ``history`` carries
    the readable trail. A hypothesis is never born supported — a new one starts
    UNRESOLVED.
    """
    RECORD_TYPE = "hypothesis"
    ID_FIELD = "hypothesis_id"
    hypothesis_id: str
    case_id: Label(str)
    statement: str
    status: str
    assumptions: tuple[str, ...]
    unknowns: tuple[str, ...]
    supporting_claim_ids: Refs("semantic_claim")
    contradicting_claim_ids: Refs("semantic_claim")
    unresolved_claim_ids: Refs("semantic_claim")
    independent_evidence_count: int
    source_dependence_summary: str
    discriminator_ids: Refs("discriminator")
    analyst_or_provider: str
    review_state: str
    history: tuple[str, ...]
    recorded_time: str
    marking: Marking
    version: int = 1  # a stale writer raises rather than shadowing an update

    def __post_init__(self):
        _member(self.status, HYPOTHESIS_STATUSES, "hypothesis status")
        if self.version < 1:
            raise ValueError("hypothesis versions start at 1")
        require_aware(self.recorded_time)
        if not self.statement:
            raise ValueError("a hypothesis requires a statement")
        if self.independent_evidence_count < 0:
            raise ValueError("independent evidence count cannot be negative")


@dataclass(frozen=True)
class DiscriminatingObservation(Record):
    """The observation that would best tell the competing hypotheses apart."""
    RECORD_TYPE = "discriminator"
    ID_FIELD = "discriminator_id"
    discriminator_id: str
    question: str
    hypothesis_ids: Refs("hypothesis")
    claim_ids: Refs("semantic_claim")
    desired_observation_type: str
    desired_subject_ref: Ref("*")
    desired_attribute: str
    source_family_hints: tuple[str, ...]
    independence_required: bool
    # The origin families backing the question when it was asked. Evidence
    # collected to answer it is checked against this snapshot, so it cannot
    # disqualify itself by joining the basis first.
    basis_groups_at_pose: tuple[str, ...]
    requirement_id: Ref("information_requirement")
    status: str
    recorded_time: str
    marking: Marking
    # What was uncertain enough to raise this question. Referring to it by id
    # lets this record inherit its marking without quoting restricted text.
    source_refs: RefPairs() = ()
    version: int = 1  # a stale writer raises rather than shadowing an update

    def __post_init__(self):
        _member(self.status, DISCRIMINATOR_STATUSES, "discriminator status")
        if self.version < 1:
            raise ValueError("discriminator versions start at 1")
        _member(self.desired_observation_type, OBSERVATION_TYPES, "observation type")
        require_aware(self.recorded_time)
        if not self.question:
            raise ValueError("a discriminating observation requires its question")
        if not self.hypothesis_ids and not self.claim_ids:
            raise ValueError("a discriminator must reference what it discriminates")


@dataclass(frozen=True)
class CollectionRoute(Record):
    """One candidate collection action, ranked and explained."""
    RECORD_TYPE = "collection_route"
    ID_FIELD = "route_id"
    route_id: str
    requirement_id: Ref("information_requirement")
    discriminator_id: Ref("discriminator")
    source_id: Ref("source")
    operation: str
    query_value: str
    automatable: bool
    human_reason: str
    factors: tuple[tuple[str, float], ...]
    score: float
    rank: int
    explanation: str
    status: str
    execution_id: Ref("fabric_execution")
    task_id: Ref("analyst_task")
    recorded_time: str
    marking: Marking
    version: int = 1  # a stale writer raises rather than shadowing an update

    def __post_init__(self):
        _member(self.status, ROUTE_STATUSES, "route status")
        if self.version < 1:
            raise ValueError("route versions start at 1")
        require_aware(self.recorded_time)
        if not self.explanation:
            raise ValueError("a collection route must explain its ranking")
        if not self.automatable and self.status not in ("HUMAN_REQUIRED", "DECLINED") \
                and self.status in ("EXECUTED",):
            raise ValueError("a non-automatable route cannot be marked executed by the machine")


@dataclass(frozen=True)
class ReviewItem(Record):
    """An entry in the human review queue; the latest state wins."""
    RECORD_TYPE = "review_item"
    ID_FIELD = "item_id"
    item_id: str
    kind: str
    subject_kind: str
    subject_id: DynamicRef("subject_kind")
    detail: str
    evidence_refs: Refs("*")
    status: str
    resolution_note: str
    recorded_time: str
    marking: Marking
    version: int = 1  # a stale writer raises rather than shadowing an update

    def __post_init__(self):
        _member(self.kind, REVIEW_KINDS, "review kind")
        if self.version < 1:
            raise ValueError("review item versions start at 1")
        _member(self.status, REVIEW_STATUSES, "review status")
        require_aware(self.recorded_time)
        if self.status != "OPEN" and not self.resolution_note:
            raise ValueError("resolving or dismissing a review item requires a note")
