"""Record shapes for annotations, dissent and reports.

A report is a versioned structure, not generated prose: every sentence in it is
SUPPORTED, EXPLICITLY_INFERENTIAL or UNRESOLVED, and says on what. Approving one
is a recorded human act over a fixed version; a revision is a new version.
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

# Suggested section kinds. Any lowercase token is accepted, so a mission can
# add sections without changing this file.
SECTION_KINDS = ("executive_summary", "mission_question", "key_judgments",
                 "current_situation", "evidence", "themes", "key_events",
                 "stakeholders", "narratives", "impact_exposure", "hypotheses",
                 "forecasts", "warnings", "information_gaps", "decision_options",
                 "unresolved_risks", "dissent", "collection_status", "appendix")

REPORT_STATUSES = ("DRAFT", "IN_REVIEW", "APPROVED", "APPROVED_WITH_DISSENT",
                   "REJECTED", "RETURNED_FOR_REVISION", "SUPERSEDED", "WITHDRAWN")
DISPOSITIONS = ("SUBMITTED", "APPROVED", "APPROVED_WITH_DISSENT", "REJECTED",
                "RETURNED_FOR_REVISION", "SUPERSEDED", "WITHDRAWN")
# Dispositions only a human may record.
HUMAN_ONLY_DISPOSITIONS = ("APPROVED", "APPROVED_WITH_DISSENT", "REJECTED",
                           "RETURNED_FOR_REVISION")

REPORT_ROLES = ("ANALYST", "EXECUTIVE", "SOURCE_LEGAL", "OPERATOR")

# What an annotation may bind to. The browser cannot invent new target kinds.
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
    """One analyst statement bound to a mission record.

    Dissent is an annotation of kind DISSENT: it never overwrites what it
    disagrees with, and resolving it takes a note. A reply names its parent, so
    the whole discussion stays attached to the target.
    """
    RECORD_TYPE = "workbench_annotation"
    annotation_id: str
    target_kind: str
    target_id: str
    author: str
    kind: str
    text: str
    status: str
    resolution_note: str
    reply_to: str          # parent annotation_id, or ""
    anchor_ref: str        # a finer anchor such as a sentence id, or ""
    recorded_time: str
    marking: Marking
    version: int = 1  # a stale writer raises rather than overwriting

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
    """One statement a reader can approve, with the status that qualifies it.

    SUPPORTED needs a basis that resolves; EXPLICITLY_INFERENTIAL needs the
    inference written out; UNRESOLVED needs the reason. A sentence that sets
    asserts_independent is claiming independent corroboration, and validation
    checks that claim against the sources behind its basis.
    """
    RECORD_TYPE = "report_sentence"
    sentence_id: str
    text: str
    status: str
    basis_refs: tuple[str, ...] = ()        # claim, observation, forecast or anchor ids
    assumption_ids: tuple[str, ...] = ()
    inference_note: str = ""
    unresolved_reason: str = ""
    temporal_scope: str = ""                # "", CURRENT or HISTORICAL
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
    section_id: str
    kind: str
    title: str
    sentences: tuple[ReportSentence, ...] = ()
    option_ids: tuple[str, ...] = ()   # response_option ids, for a decision section

    def __post_init__(self):
        if not self.kind or not self.kind.replace("_", "").isalnum() or self.kind != self.kind.lower():
            raise ValueError(f"section kind must be a lowercase token: {self.kind!r}")
        if not self.title.strip():
            raise ValueError("section title must not be empty")


@dataclass(frozen=True)
class ReportRecord(Record):
    """One version of a mission report.

    The record holds the whole report at this version; the log keeps the earlier
    ones. based_on_state_token pins the mission state the draft was written
    against, so a reviewer can see that it has moved on.
    """
    RECORD_TYPE = "workbench_report"
    report_id: str
    version: int
    title: str
    question: str
    author: str
    sections: tuple[ReportSection, ...]
    status: str
    based_on_state_token: str
    recorded_time: str
    marking: Marking
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
    """One named act on one fixed report version.

    An approval records the hash of the validation result it accepted and the
    mission state at the time, so what was approved can be checked again later.
    """
    RECORD_TYPE = "workbench_report_disposition"
    disposition_id: str
    report_id: str
    report_version: int
    disposition: str
    actor_id: str
    actor_kind: str
    note: str
    validation_sha256: str
    state_token: str
    dissent_annotation_ids: tuple[str, ...]
    recorded_time: str
    marking: Marking

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
    """A saved layout: filters, focus and windows.

    This is presentation only, never analytical truth. It lives in the store so
    a mission's working views survive a restart and stay attributable.
    """
    RECORD_TYPE = "workbench_saved_view"
    view_id: str
    title: str
    author: str
    view_kind: str          # which workbench screen the layout belongs to
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
