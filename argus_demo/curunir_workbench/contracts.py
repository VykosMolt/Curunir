"""Workbench-plane contracts: annotations, dissent, and the report/dossier
engine.

A report is not generated prose: it is a versioned structured projection of
the analytical record. Every sentence in an approvable output is SUPPORTED
(with resolvable proposition/evidence basis), EXPLICITLY_INFERENTIAL (with its
inference basis exposed), or UNRESOLVED (with the reason). Approval is a
recorded human disposition over one immutable report version; later revision
is a new version, never a rewrite of the approved history.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from curunir_operational.access import Marking
from curunir_operational.canonical import require_aware
from curunir_operational.contracts import Record, _member

ANNOTATION_KINDS = ("NOTE", "QUESTION", "DISSENT", "CORRECTION_SUGGESTION")
ANNOTATION_STATUSES = ("OPEN", "RESOLVED", "WITHDRAWN")

SENTENCE_STATUSES = ("SUPPORTED", "EXPLICITLY_INFERENTIAL", "UNRESOLVED")
TEMPORAL_SCOPES = ("", "CURRENT", "HISTORICAL")

# Suggested section kinds; the schema is extensible — any lowercase token is
# accepted so a mission can add sections without a contract change.
SECTION_KINDS = ("executive_summary", "mission_question", "key_judgments",
                 "current_situation", "evidence", "themes", "key_events",
                 "stakeholders", "narratives", "impact_exposure", "hypotheses",
                 "forecasts", "warnings", "information_gaps", "decision_options",
                 "unresolved_risks", "dissent", "collection_status", "appendix")

REPORT_STATUSES = ("DRAFT", "IN_REVIEW", "APPROVED", "APPROVED_WITH_DISSENT",
                   "REJECTED", "RETURNED_FOR_REVISION", "SUPERSEDED", "WITHDRAWN")
DISPOSITIONS = ("SUBMITTED", "APPROVED", "APPROVED_WITH_DISSENT", "REJECTED",
                "RETURNED_FOR_REVISION", "SUPERSEDED", "WITHDRAWN")
# dispositions only a recorded human act may append
HUMAN_ONLY_DISPOSITIONS = ("APPROVED", "APPROVED_WITH_DISSENT", "REJECTED",
                           "RETURNED_FOR_REVISION")

REPORT_ROLES = ("ANALYST", "EXECUTIVE", "SOURCE_LEGAL", "OPERATOR")

# targets an annotation may bind to (canonical record families — the browser
# cannot invent new target kinds)
ANNOTATION_TARGET_KINDS = (
    "object", "relationship", "semantic_claim", "semantic_observation",
    "evidence_anchor", "fabric_manifestation", "fabric_source_descriptor",
    "analytic_theme", "analytic_narrative", "narrative_variant",
    "stakeholder_assessment", "influence_assertion", "impact_path",
    "mission_objective", "analytic_assumption", "response_option",
    "hypothesis", "discriminator", "analytic_forecast", "forecast_indicator",
    "strategic_warning", "review_item", "information_requirement",
    "analyst_task", "collection_route", "fabric_watch", "workbench_report",
    "report_sentence", "alert", "recommendation", "decision",
)


@dataclass(frozen=True)
class AnnotationRecord(Record):
    """One analyst statement bound to a canonical mission object.

    Dissent is an annotation of kind DISSENT: it never overwrites the state it
    disagrees with, and resolving it requires a note. Replies reference the
    parent annotation; the discussion stays attached to the target object.
    """
    RECORD_TYPE = "workbench_annotation"
    annotation_id: str; target_kind: str; target_id: str
    author: str; kind: str; text: str
    status: str; resolution_note: str
    reply_to: str          # parent annotation_id, or ""
    anchor_ref: str        # optional finer anchor (sentence id, span ref), or ""
    recorded_time: str; marking: Marking
    version: int = 1  # strict next-version: a stale writer raises, never shadows

    def __post_init__(self):
        _member(self.kind, ANNOTATION_KINDS, "annotation kind")
        _member(self.status, ANNOTATION_STATUSES, "annotation status")
        _member(self.target_kind, ANNOTATION_TARGET_KINDS, "annotation target kind")
        if not self.text.strip():
            raise ValueError("annotation text must not be empty")
        if not self.author:
            raise ValueError("annotation requires an author")
        if self.version < 1:
            raise ValueError("annotation versions start at 1")
        if self.status != "OPEN" and not self.resolution_note:
            raise ValueError("resolving or withdrawing an annotation requires a note")
        require_aware(self.recorded_time)


@dataclass(frozen=True)
class ReportSentence(Record):
    """One atomic approvable statement with its epistemic status.

    SUPPORTED requires basis_refs that resolve to actual propositions/evidence;
    EXPLICITLY_INFERENTIAL requires an inference note (and exposes assumptions);
    UNRESOLVED requires the reason it remains unresolved. `asserts_independent`
    marks sentences that claim independent corroboration — validation checks
    the actual dependence arithmetic behind the basis.
    """
    RECORD_TYPE = "report_sentence"
    sentence_id: str; text: str; status: str
    basis_refs: tuple[str, ...] = ()        # claim/observation/forecast/anchor ids
    assumption_ids: tuple[str, ...] = ()
    inference_note: str = ""
    unresolved_reason: str = ""
    temporal_scope: str = ""                # "", CURRENT, HISTORICAL
    asserts_independent: bool = False

    def __post_init__(self):
        _member(self.status, SENTENCE_STATUSES, "sentence status")
        _member(self.temporal_scope, TEMPORAL_SCOPES, "temporal scope")
        if not self.text.strip():
            raise ValueError("sentence text must not be empty")
        if self.status == "EXPLICITLY_INFERENTIAL" and not self.inference_note.strip():
            raise ValueError("an inferential sentence must state its inference basis")
        if self.status == "UNRESOLVED" and not self.unresolved_reason.strip():
            raise ValueError("an unresolved sentence must state why it is unresolved")


@dataclass(frozen=True)
class ReportSection(Record):
    RECORD_TYPE = "report_section"
    section_id: str; kind: str; title: str
    sentences: tuple[ReportSentence, ...] = ()
    option_ids: tuple[str, ...] = ()   # analytic response_option ids (decision options)

    def __post_init__(self):
        if not self.kind or not self.kind.replace("_", "").isalnum() or self.kind != self.kind.lower():
            raise ValueError(f"section kind must be a lowercase token: {self.kind!r}")
        if not self.title.strip():
            raise ValueError("section title must not be empty")


@dataclass(frozen=True)
class ReportRecord(Record):
    """One version of a mission report/dossier.

    The record is the full structured content at this version; the event log
    keeps every prior version. `based_on_state_token` pins the projection
    state the draft was built against, so staleness is visible at review time.
    """
    RECORD_TYPE = "workbench_report"
    report_id: str; version: int
    title: str; question: str
    author: str
    sections: tuple[ReportSection, ...]
    status: str
    based_on_state_token: str
    recorded_time: str; marking: Marking
    change_note: str = ""

    def __post_init__(self):
        _member(self.status, REPORT_STATUSES, "report status")
        if self.version < 1:
            raise ValueError("report versions start at 1")
        if not self.title.strip():
            raise ValueError("report title must not be empty")
        if not self.author:
            raise ValueError("report requires an author")
        seen: set[str] = set()
        for section in self.sections:
            for sentence in section.sentences:
                if sentence.sentence_id in seen:
                    raise ValueError(f"duplicate sentence id: {sentence.sentence_id}")
                seen.add(sentence.sentence_id)
        require_aware(self.recorded_time)


@dataclass(frozen=True)
class ReportDisposition(Record):
    """A recorded, attributable act over one immutable report version.

    Approval carries the sha256 of the validation result it accepted and the
    projection state token at decision time — the approved output is
    replayable evidence, not a UI boolean.
    """
    RECORD_TYPE = "workbench_report_disposition"
    disposition_id: str; report_id: str; report_version: int
    disposition: str; actor_id: str; actor_kind: str
    note: str; validation_sha256: str; state_token: str
    dissent_annotation_ids: tuple[str, ...]
    recorded_time: str; marking: Marking

    def __post_init__(self):
        _member(self.disposition, DISPOSITIONS, "report disposition")
        _member(self.actor_kind, ("HUMAN", "SERVICE"), "actor kind")
        if self.disposition in HUMAN_ONLY_DISPOSITIONS and self.actor_kind != "HUMAN":
            raise ValueError(f"disposition {self.disposition} requires a human actor")
        if self.disposition == "APPROVED_WITH_DISSENT" and not self.dissent_annotation_ids:
            raise ValueError("APPROVED_WITH_DISSENT must reference the dissent it carries")
        if self.disposition in ("REJECTED", "RETURNED_FOR_REVISION") and not self.note.strip():
            raise ValueError(f"{self.disposition} requires a note")
        require_aware(self.recorded_time)


@dataclass(frozen=True)
class SavedViewRecord(Record):
    """A saved investigation layout: filters, focus and window definitions.

    Pure presentation state — never analytical truth. Kept in the store so a
    mission's working views survive restart/replay and stay attributable.
    """
    RECORD_TYPE = "workbench_saved_view"
    view_id: str; title: str; author: str
    view_kind: str          # which workbench surface this layout belongs to
    definition: dict = field(default_factory=dict)
    recorded_time: str = ""
    marking: Marking = None  # type: ignore[assignment]
    version: int = 1

    def __post_init__(self):
        if not self.title.strip():
            raise ValueError("saved view title must not be empty")
        if self.marking is None:
            raise ValueError("saved view requires a marking")
        require_aware(self.recorded_time)
