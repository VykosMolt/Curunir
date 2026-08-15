"""Bounded V5 records and invariants.

These records deliberately keep model review, human review, current state and
historical state separate.  They are JSON-native so every operation can be
exported and replayed without a database or provider call.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Mapping

from ..v4.models import sha256, stable_id


SURFACES = (
    "SURFACE_1_SPAN_GROUNDED_EXTRACTION_PRECISION",
    "SURFACE_2_SOURCE_ORIGIN_ACCURACY",
    "SURFACE_3_FALSE_CORROBORATION_ACCURACY",
    "SURFACE_4_CLAIM_SUPPORT_ACCURACY",
    "SURFACE_5_CONTRADICTION_CORRECTION_AND_RETRACTION_ACCURACY",
    "SURFACE_6_REPORT_FAITHFULNESS",
)
SPLITS = frozenset({"REPAIR_DEVELOPMENT_SET", "REGRESSION_SET", "FINAL_HELD_OUT_SET",
                    "ADVERSARIAL_CHALLENGE_SET", "RESERVE_POOL"})
DECISIONS = frozenset({"CORRECT", "INCORRECT", "PARTIALLY_CORRECT", "INSUFFICIENT_INFORMATION",
                       "AMBIGUOUS", "OUT_OF_SCOPE", "CANNOT_ADJUDICATE", "PACKET_DEFECT"})
CONSENSUS = frozenset({"UNANIMOUS", "MAJORITY", "SPLIT", "DETERMINISTIC_OVERRIDE",
                       "ESCALATION_REQUIRED", "HUMAN_REVIEW_REQUIRED"})
REVIEWER_KINDS = frozenset({"DETERMINISTIC_VALIDATOR", "BLIND_MODEL_REVIEWER_A",
                            "BLIND_MODEL_REVIEWER_B", "BLIND_MODEL_REVIEWER_C",
                            "HUMAN_REVIEWER_SLOT"})
SEVERITIES = frozenset({"CRITICAL", "HIGH", "MEDIUM", "LOW", "INFORMATIONAL"})
PROPAGATION_STATES = frozenset({"UNAFFECTED", "REVALIDATION_REQUIRED", "INVALIDATED",
                                "SUPERSEDED", "REGENERATED", "BLOCKED",
                                "HUMAN_REVIEW_REQUIRED"})
DELTA_STATES = frozenset({"ADDED", "REMOVED", "MODIFIED", "UNCHANGED", "INVALIDATED",
                          "SUPERSEDED", "REQUIRES_REVIEW"})
FRESHNESS_STATES = frozenset({"CURRENT", "AGING", "STALE", "SUPERSEDED", "RETRACTED",
                              "UNAVAILABLE", "VALIDITY_UNKNOWN", "REVALIDATION_REQUIRED"})


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class EvaluationPacket:
    packet_id: str
    surface: str
    source_artifact_ids: tuple[str, ...]
    packet_version: int
    created_time: str
    sampling_stratum: str
    split: str
    access_marking: Mapping[str, Any]
    redactions: tuple[str, ...]
    answer_key_visibility: str
    assignment_state: str
    review_material: Mapping[str, Any]
    frozen_content_hash: str

    def __post_init__(self) -> None:
        if self.surface not in SURFACES or self.split not in SPLITS:
            raise ValueError("invalid surface or corpus split")
        if self.answer_key_visibility != "HIDDEN_FROM_INDEPENDENT_REVIEWERS":
            raise ValueError("blind packet must hide its answer key")
        forbidden = {"curunir_answer", "system_label", "expected_answer", "final_verdict",
                     "selected_because_wrong", "mutation_answer"}
        if forbidden.intersection(self.review_material):
            raise ValueError("blind packet leaks an answer or verdict")
        expected = sha256(self.public_payload(include_hash=False))
        if self.frozen_content_hash != expected:
            raise ValueError("packet frozen hash mismatch")

    def public_payload(self, *, include_hash: bool = True) -> dict[str, Any]:
        value = asdict(self)
        if not include_hash:
            value.pop("frozen_content_hash", None)
        return value


def freeze_packet(*, surface: str, source_artifact_ids: tuple[str, ...], version: int,
                  stratum: str, split: str, material: Mapping[str, Any], ordinal: int,
                  created_time: str = "2026-07-22T12:00:00+00:00") -> EvaluationPacket:
    packet_id = stable_id("v5-packet", surface, split, stratum, ordinal, source_artifact_ids)
    provisional = {
        "packet_id": packet_id, "surface": surface, "source_artifact_ids": source_artifact_ids,
        "packet_version": version, "created_time": created_time, "sampling_stratum": stratum,
        "split": split, "access_marking": {"releasability": ["PUBLIC"]}, "redactions": (),
        "answer_key_visibility": "HIDDEN_FROM_INDEPENDENT_REVIEWERS",
        "assignment_state": "FROZEN_UNASSIGNED", "review_material": dict(material),
    }
    return EvaluationPacket(**provisional, frozen_content_hash=sha256(provisional))


@dataclass(frozen=True)
class PacketAssignment:
    assignment_id: str
    packet_id: str
    reviewer_id: str
    reviewer_kind: str
    packet_order: int
    isolated_context_id: str
    assigned_time: str
    prompt_hash: str
    answer_key_visible: bool = False

    def __post_init__(self) -> None:
        if self.reviewer_kind not in REVIEWER_KINDS or self.answer_key_visible:
            raise ValueError("invalid or unblinded reviewer assignment")


@dataclass(frozen=True)
class AdjudicationDecision:
    decision_id: str
    packet_id: str
    reviewer_id: str
    reviewer_kind: str
    provider: str
    model_version: str
    prompt_hash: str
    decision: str
    independent_label: str
    rationale: str
    evidence: tuple[str, ...]
    confidence: float
    warnings: tuple[str, ...]
    created_time: str
    review_boundary: str = "AI_SECONDARY_ADJUDICATION"

    def __post_init__(self) -> None:
        if self.decision not in DECISIONS or not 0 <= self.confidence <= 1:
            raise ValueError("invalid adjudication decision")
        if self.reviewer_kind == "HUMAN_REVIEWER_SLOT":
            raise ValueError("empty human reviewer slots cannot emit decisions")
        if self.review_boundary not in {"AI_SECONDARY_ADJUDICATION", "DETERMINISTIC_VALIDATOR"}:
            raise ValueError("review boundary cannot claim human review")


@dataclass(frozen=True)
class Disagreement:
    disagreement_id: str
    packet_id: str
    reviewer_decision_ids: tuple[str, ...]
    independent_labels: tuple[str, ...]
    consensus_state: str
    resolution: str
    human_review_required: bool

    def __post_init__(self) -> None:
        if self.consensus_state not in CONSENSUS:
            raise ValueError("invalid consensus state")
        if self.consensus_state == "HUMAN_REVIEW_REQUIRED" and not self.human_review_required:
            raise ValueError("human escalation cannot be silently closed")


@dataclass(frozen=True)
class ErrorRecord:
    error_id: str
    surface: str
    packet_id: str
    taxonomy_class: str
    failure_class: str
    affected_source_ids: tuple[str, ...]
    affected_object_ids: tuple[str, ...]
    severity: str
    root_cause: str
    reproducible: bool
    repair_id: str | None
    final_state: str

    def __post_init__(self) -> None:
        if self.severity not in SEVERITIES:
            raise ValueError("invalid error severity")


@dataclass(frozen=True)
class RepairRecord:
    repair_id: str
    error_ids: tuple[str, ...]
    root_cause_component: str
    invariant: str
    production_change: str
    regression_test: str
    case_specific: bool
    repair_frozen_time: str
    status: str


@dataclass(frozen=True)
class DependencyImpact:
    impact_id: str
    upstream_object_id: str
    downstream_object_id: str
    downstream_kind: str
    state: str
    reason: str
    old_version_preserved: bool
    new_version_id: str | None

    def __post_init__(self) -> None:
        if self.state not in PROPAGATION_STATES or not self.old_version_preserved:
            raise ValueError("invalid destructive propagation")


@dataclass(frozen=True)
class StandingInvestigation:
    standing_id: str
    originating_case_id: str
    purpose: str
    scope: tuple[str, ...]
    exclusions: tuple[str, ...]
    entities: tuple[str, ...]
    source_classes: tuple[str, ...]
    languages: tuple[str, ...]
    watch_queries: tuple[str, ...]
    watched_urls: tuple[str, ...]
    recapture_policy: Mapping[str, Any]
    freshness_policy: Mapping[str, Any]
    materiality_policy: Mapping[str, Any]
    access_policy: str
    owning_node: str
    review_policy: str
    status: str
    version: int
    integrity_hash: str


@dataclass(frozen=True)
class SourceVersion:
    source_version_id: str
    source_object_id: str
    previous_version_id: str | None
    retrieval_id: str
    content_hash: str | None
    metadata_hash: str
    content_path: str | None
    access_decision: str
    change_state: str
    byte_changed: bool
    semantic_changed: bool
    valid_time: str | None
    recorded_time: str
    update_run_id: str
    immutable: bool = True

    def __post_init__(self) -> None:
        if not self.immutable:
            raise ValueError("source versions are immutable")


@dataclass(frozen=True)
class LongitudinalDelta:
    delta_id: str
    kind: str
    old_id: str | None
    new_id: str | None
    state: str
    reason: str
    supporting_evidence_ids: tuple[str, ...]
    materiality: str
    propagation_state: str
    review_state: str

    def __post_init__(self) -> None:
        if self.state not in DELTA_STATES or self.propagation_state not in PROPAGATION_STATES:
            raise ValueError("invalid longitudinal delta")


@dataclass(frozen=True)
class FreshnessAssessment:
    assessment_id: str
    object_id: str
    state: str
    reasons: tuple[str, ...]
    source_type: str
    claim_type: str
    assessed_at: str
    revalidation_required: bool

    def __post_init__(self) -> None:
        if self.state not in FRESHNESS_STATES:
            raise ValueError("invalid freshness state")


@dataclass(frozen=True)
class IntelligenceUpdateAlert:
    alert_id: str
    standing_id: str
    affected_object_ids: tuple[str, ...]
    old_state: str
    new_state: str
    supporting_source_version_ids: tuple[str, ...]
    source_origin_state: str
    uncertainty: str
    access_marking: Mapping[str, Any]
    recipient: str
    review_state: str
    materiality: str
    requested_action: str

    def __post_init__(self) -> None:
        if self.materiality == "NO_ALERT":
            raise ValueError("NO_ALERT changes must not instantiate alerts")

