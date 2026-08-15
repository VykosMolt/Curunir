"""Contradiction, correction and supersession capability for V5.1 (Section 16).

Mechanism-level closures, all general (no campaign material):

- COMPATIBLE_UPDATE_OVERCLASSIFIED: ``TEMPORAL_UPDATE`` requires an actual
  value difference AND disjoint event times; identical propositions are
  ``NO_CONFLICT`` regardless of when they were published.
- Event-time vs publication-time confusion: ``TemporalFrame`` keeps the
  period a claim is about apart from when it was stated, and publication
  order can never license an update or a conflict class.  Divergent values
  with no event-time evidence are ``UNRESOLVED`` with an
  ``EPISTEMICALLY_UNRESOLVABLE`` demonstration, never silently classified.
- IDENTITY_UNRESOLVED_CONFLICT: unresolved subject identity blocks every
  forced conflict class (``IDENTITY_DISAGREEMENT`` with a recorded blocker).
- APPLICABILITY_SCHEMA_GAP: qualification, nested scope and definitional
  divergence are first-class relation outcomes, not flattened conflicts.
- SUPERSESSION_TOO_BROAD: correction/retraction/supersession exist only on
  explicit notice evidence; supersession carries explicit scope and a
  whole-document scope needs a document-level notice.
- Interpretation failures (uninterpretable units, dimension mismatches)
  emit ``SYSTEM_CAPABILITY_FAILURE``; they never degrade to a bare unknown.

Research shadow only.
"""
from __future__ import annotations

import calendar
import re
from dataclasses import dataclass
from fractions import Fraction
from typing import Iterable, Mapping

from ..v4.models import ClaimUnit, IDENTITY_OUTCOMES, require_aware
from .models import (
    CONTENT_RELATIONS, EVIDENCE_LIFECYCLE_STATES, MATERIAL_QUALIFICATION_MARKERS,
    MODALITIES, POLARITIES, RELATION_CLASSES, ROLE_EVIDENCE_KINDS,
    CapabilityOutcome, EvidenceRef, Record, capability_outcome, now_utc, stable_id,
)

# Scoping subsets of the shared vocabularies (never redefined locally).
NOTICE_RELATIONS = frozenset({"CORRECTS", "RETRACTS", "SUPERSEDES"})
SUPERSESSION_SCOPES = ("CLAIM_SCOPE", "SECTION_SCOPE", "WHOLE_DOCUMENT_SCOPE")
NOTICE_COVERAGE = ("CLAIM_LEVEL", "SECTION_LEVEL", "DOCUMENT_LEVEL")
TEMPORAL_BASES = ("SAME_EVENT_TIME", "OVERLAPPING_EVENT_TIME", "DIFFERENT_EVENT_TIME",
                  "EVENT_TIME_UNKNOWN", "NOTICE_GOVERNED", "NOT_COMPARED")
# Ordered view over the shared models vocabulary; drift fails loudly.
CLAIM_STATE_STATUSES = ("CURRENT", "CORRECTED", "RETRACTED", "SUPERSEDED")
if frozenset(CLAIM_STATE_STATUSES) != EVIDENCE_LIFECYCLE_STATES:
    raise RuntimeError("claim lifecycle order out of sync with models.EVIDENCE_LIFECYCLE_STATES")

_NOTICE_CLASSES = frozenset({"CORRECTION", "RETRACTION", "SUPERSESSION"})
_CONFLICT_CLASSES = frozenset({"LOGICAL_CONTRADICTION", "NUMERIC_DISAGREEMENT",
                               "POLARITY_CONFLICT"})
_NOTICE_STATUS = {"CORRECTION": "CORRECTED", "RETRACTION": "RETRACTED",
                  "SUPERSESSION": "SUPERSEDED"}

if not NOTICE_RELATIONS <= CONTENT_RELATIONS:
    raise ValueError("notice relations drifted from the shared content-relation vocabulary")
if "UNRESOLVED" not in MATERIAL_QUALIFICATION_MARKERS or "CONDITIONAL" not in MODALITIES:
    raise ValueError("required V5.1 vocabulary members are missing")


# ---------------------------------------------------------------------------
# temporal frames — event time vs publication time
# ---------------------------------------------------------------------------

_MIN_BOUND = "0000-00-00"
_MAX_BOUND = "9999-99-99"
_DATE_PATTERN = re.compile(r"^(\d{4})(?:-(\d{2}))?(?:-(\d{2}))?(?:[T ].*)?$")


def _event_bound(value: str | None, *, end: bool) -> str:
    """Normalize a time value to a comparable date bound (date granularity)."""
    if value is None:
        return _MAX_BOUND if end else _MIN_BOUND
    match = _DATE_PATTERN.match(str(value).strip())
    if not match:
        raise ValueError(f"unparseable time value: {value!r}")
    year = int(match.group(1))
    if match.group(2) is None:
        return f"{year:04d}-12-31" if end else f"{year:04d}-01-01"
    month = int(match.group(2))
    if not 1 <= month <= 12:
        raise ValueError(f"month out of range: {value!r}")
    last = calendar.monthrange(year, month)[1]
    if match.group(3) is None:
        day = last if end else 1
    else:
        day = int(match.group(3))
        if not 1 <= day <= last:
            raise ValueError(f"day out of range: {value!r}")
    return f"{year:04d}-{month:02d}-{day:02d}"


@dataclass(frozen=True)
class TemporalFrame(Record):
    """Event time (the period a claim is about) kept apart from publication
    time (when it was stated).  Confusing the two was a verified V5 failure
    mode; classification logic below only ever consumes event time."""

    frame_id: str
    claim_id: str
    event_time: tuple[str | None, str | None]
    publication_time: str | None

    def __post_init__(self) -> None:
        start = _event_bound(self.event_time[0], end=False)
        end = _event_bound(self.event_time[1], end=True)
        if self.event_time[0] is not None and self.event_time[1] is not None and start > end:
            raise ValueError("event interval is inverted")
        if self.publication_time is not None:
            _event_bound(self.publication_time, end=False)

    @property
    def has_event_time(self) -> bool:
        return self.event_time[0] is not None or self.event_time[1] is not None

    def _bounds(self) -> tuple[str, str]:
        return (_event_bound(self.event_time[0], end=False),
                _event_bound(self.event_time[1], end=True))

    def event_relation(self, other: "TemporalFrame") -> str:
        if not self.has_event_time or not other.has_event_time:
            return "UNKNOWN"
        a0, a1 = self._bounds()
        b0, b1 = other._bounds()
        if (a0, a1) == (b0, b1):
            return "EQUAL"
        if a1 < b0 or b1 < a0:
            return "DISJOINT"
        if b0 <= a0 and a1 <= b1:
            return "LEFT_WITHIN_RIGHT"
        if a0 <= b0 and b1 <= a1:
            return "RIGHT_WITHIN_LEFT"
        return "OVERLAPPING"

    def same_event_time(self, other: "TemporalFrame") -> bool:
        return self.event_relation(other) == "EQUAL"

    def publication_order(self, other: "TemporalFrame") -> str:
        """Reporting helper only: publication order cannot distinguish an
        update from a contradiction and never feeds classification."""
        if self.publication_time is None or other.publication_time is None:
            return "UNKNOWN"
        a = _event_bound(self.publication_time, end=False)
        b = _event_bound(other.publication_time, end=False)
        return "EQUAL" if a == b else ("EARLIER" if a < b else "LATER")


def temporal_frame(*, claim_id: str, event_time: tuple[str | None, str | None] = (None, None),
                   publication_time: str | None = None) -> TemporalFrame:
    return TemporalFrame(stable_id("temporal-frame", claim_id, event_time, publication_time),
                         claim_id, tuple(event_time), publication_time)


# ---------------------------------------------------------------------------
# notices, identity links, definition evidence
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class NoticeRecord(Record):
    """An explicit editorial notice; the only license for CORRECTION,
    RETRACTION or SUPERSESSION (Section 13-style evidence contract)."""

    notice_id: str
    content_relation: str
    issuing_document_id: str
    target_claim_ids: tuple[str, ...]
    target_document_id: str | None
    replacement_claim_id: str | None
    scope_kind: str
    notice_span_coverage: str
    evidence: tuple[EvidenceRef, ...]
    recorded_time: str

    def __post_init__(self) -> None:
        require_aware(self.recorded_time)
        if self.content_relation not in NOTICE_RELATIONS:
            raise ValueError(f"unknown notice relation: {self.content_relation}")
        if self.scope_kind not in SUPERSESSION_SCOPES:
            raise ValueError(f"unknown notice scope: {self.scope_kind}")
        if self.notice_span_coverage not in NOTICE_COVERAGE:
            raise ValueError(f"unknown notice coverage: {self.notice_span_coverage}")
        if not self.issuing_document_id.strip():
            raise ValueError("notice requires its issuing document")
        if not self.evidence:
            raise ValueError("a notice is only admissible with explicit notice evidence")
        for reference in self.evidence:
            if reference.evidence_kind not in ROLE_EVIDENCE_KINDS:
                raise ValueError(f"notice evidence kind outside shared vocabulary: {reference.evidence_kind}")
        if self.scope_kind == "WHOLE_DOCUMENT_SCOPE":
            if not self.target_document_id:
                raise ValueError("whole-document scope requires the governed document")
            if self.notice_span_coverage != "DOCUMENT_LEVEL":
                raise ValueError("marking a whole source requires whole-document notice evidence")
        elif not self.target_claim_ids:
            raise ValueError("claim/section scope requires explicit governed claims")


def notice_record(*, content_relation: str, issuing_document_id: str,
                  evidence: tuple[EvidenceRef, ...], target_claim_ids: tuple[str, ...] = (),
                  target_document_id: str | None = None, replacement_claim_id: str | None = None,
                  scope_kind: str = "CLAIM_SCOPE",
                  notice_span_coverage: str = "CLAIM_LEVEL") -> NoticeRecord:
    return NoticeRecord(
        stable_id("notice", content_relation, issuing_document_id,
                  sorted(target_claim_ids), target_document_id or "", scope_kind),
        content_relation, issuing_document_id, tuple(target_claim_ids), target_document_id,
        replacement_claim_id, scope_kind, notice_span_coverage, tuple(evidence), now_utc())


@dataclass(frozen=True)
class SubjectIdentity(Record):
    """Identity resolution between the two claims' subjects (V4 outcomes)."""

    identity_id: str
    left_subject: str
    right_subject: str
    outcome: str
    evidence: tuple[EvidenceRef, ...]

    def __post_init__(self) -> None:
        if self.outcome not in IDENTITY_OUTCOMES:
            raise ValueError(f"unknown identity outcome: {self.outcome}")
        if not self.left_subject.strip() or not self.right_subject.strip():
            raise ValueError("identity link requires both subjects")
        if self.outcome in {"SAME_ENTITY_ACCEPTED", "DIFFERENT_ENTITY"} and not self.evidence:
            raise ValueError("a resolved identity requires identity evidence")


def subject_identity(*, left_subject: str, right_subject: str, outcome: str,
                     evidence: tuple[EvidenceRef, ...] = ()) -> SubjectIdentity:
    return SubjectIdentity(stable_id("subject-identity", left_subject, right_subject, outcome),
                           left_subject, right_subject, outcome, tuple(evidence))


@dataclass(frozen=True)
class DefinitionEvidence(Record):
    """Recorded evidence that the two claims define a shared term differently."""

    definition_id: str
    term: str
    left_definition: str
    right_definition: str
    evidence: tuple[EvidenceRef, ...]

    def __post_init__(self) -> None:
        if not self.term.strip():
            raise ValueError("definition evidence requires the diverging term")
        if _norm(self.left_definition) == _norm(self.right_definition):
            raise ValueError("definition evidence requires divergent definitions")
        if not self.evidence:
            raise ValueError("definitional divergence requires evidence")


def definition_evidence(*, term: str, left_definition: str, right_definition: str,
                        evidence: tuple[EvidenceRef, ...]) -> DefinitionEvidence:
    return DefinitionEvidence(stable_id("definition-evidence", term, left_definition, right_definition),
                              term, left_definition, right_definition, tuple(evidence))


# ---------------------------------------------------------------------------
# value comparison with unit normalization
# ---------------------------------------------------------------------------

_NUMBER_PATTERN = re.compile(r"^\s*([+-]?\d[\d\s.,]*)\s*(.*)$")  # \s covers nbsp/narrow-nbsp

_MULTIPLIERS: dict[str, int] = {
    "tausend": 10 ** 3, "thousand": 10 ** 3, "mille": 10 ** 3, "tsd": 10 ** 3,
    "million": 10 ** 6, "millions": 10 ** 6, "millionen": 10 ** 6, "mio": 10 ** 6,
    "milliarde": 10 ** 9, "milliarden": 10 ** 9, "milliard": 10 ** 9,
    "milliards": 10 ** 9, "mrd": 10 ** 9,
}
# 'billion' is 1e9 in English but 1e12 in German/French; with no language tag
# it is uninterpretable and must fail loudly, never be guessed.
_AMBIGUOUS_MULTIPLIERS = frozenset({"billion", "billions", "billionen"})

_UNITS: dict[str, tuple[str, Fraction]] = {}
for _tokens, _dimension, _factor in (
    (("mm",), "LENGTH", Fraction(1, 1000)),
    (("cm",), "LENGTH", Fraction(1, 100)),
    (("m", "meter", "metre", "meters", "metres"), "LENGTH", Fraction(1)),
    (("km", "kilometer", "kilometre", "kilometers", "kilometres"), "LENGTH", Fraction(1000)),
    (("g", "gram", "grams", "gramm", "gramme", "grammes"), "MASS", Fraction(1)),
    (("kg", "kilogram", "kilograms", "kilogramm", "kilogramme", "kilogrammes"), "MASS", Fraction(1000)),
    (("t", "ton", "tons", "tonne", "tonnes", "tonnen"), "MASS", Fraction(10 ** 6)),
    (("eur", "euro", "euros", "€"), "CURRENCY:EUR", Fraction(1)),
    (("usd", "dollar", "dollars", "$"), "CURRENCY:USD", Fraction(1)),
    (("%", "percent", "prozent", "pourcent"), "RATIO", Fraction(1)),
):
    for _token in _tokens:
        _UNITS[_token] = (_dimension, _factor)


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", str(text).strip().casefold())


def _decimal_value(raw: str) -> Fraction | None:
    """Deterministic separator rule: a single trailing 3-digit group is
    grouping, a 1-2 digit tail is a decimal; mixed separators use the
    rightmost as decimal.  Documented constraint, never a guess per-case."""
    sign = 1
    if raw and raw[0] in "+-":
        sign = -1 if raw[0] == "-" else 1
        raw = raw[1:]
    if not raw or raw[-1] in ".,":
        return None
    dots, commas = raw.count("."), raw.count(",")
    if dots and commas:
        decimal = "." if raw.rfind(".") > raw.rfind(",") else ","
        grouping = "," if decimal == "." else "."
        raw = raw.replace(grouping, "").replace(decimal, ".")
        if raw.count(".") > 1:
            return None
    elif dots or commas:
        separator = "." if dots else ","
        parts = raw.split(separator)
        if all(len(part) == 3 for part in parts[1:]) and parts[0] and len(parts[0]) <= 3:
            raw = "".join(parts)
        elif len(parts) == 2 and 1 <= len(parts[1]) <= 2:
            raw = f"{parts[0]}.{parts[1]}"
        else:
            return None
    if not re.fullmatch(r"\d+(\.\d+)?", raw):
        return None
    return sign * Fraction(raw)


def _parse_measure(text: str) -> tuple[Fraction, str | None, str] | None:
    """Returns (value, ambiguous_multiplier_token, unit_token) or None."""
    match = _NUMBER_PATTERN.match(str(text).strip())
    if not match:
        return None
    value = _decimal_value(re.sub(r"\s", "", match.group(1)))
    if value is None:
        return None
    rest = match.group(2).strip().casefold().replace("pour cent", "pourcent")
    tokens = [token for token in re.split(r"\s+", rest) if token] if rest else []
    ambiguous: str | None = None
    if tokens:
        head = tokens[0].strip(".")
        if head in _MULTIPLIERS:
            value *= _MULTIPLIERS[head]
            tokens = tokens[1:]
        elif head in _AMBIGUOUS_MULTIPLIERS:
            ambiguous = head
            tokens = tokens[1:]
    unit = " ".join(tokens).strip(" .")
    return value, ambiguous, unit


def _compare_values(left_text: str, right_text: str) -> str:
    if _norm(left_text) == _norm(right_text):
        return "EQUAL"
    left = _parse_measure(left_text)
    right = _parse_measure(right_text)
    if left is None or right is None:
        return "DIFFERENT_TEXT"
    left_value, left_ambiguous, left_unit = left
    right_value, right_ambiguous, right_unit = right
    if (left_ambiguous or right_ambiguous) and left_ambiguous != right_ambiguous:
        return "UNIT_UNINTERPRETED"
    if left_unit == right_unit:
        # Identical tokens are commensurable even when unrecognized; an
        # identical ambiguous multiplier cancels out of the comparison.
        return "EQUAL" if left_value == right_value else "DIFFERENT_NUMERIC"
    left_resolved = _UNITS.get(left_unit) if left_unit else ("COUNT", Fraction(1))
    right_resolved = _UNITS.get(right_unit) if right_unit else ("COUNT", Fraction(1))
    if left_resolved is None or right_resolved is None:
        return "UNIT_UNINTERPRETED"
    if left_resolved[0] != right_resolved[0]:
        return "DIMENSION_MISMATCH"
    if left_value * left_resolved[1] == right_value * right_resolved[1]:
        return "EQUAL"
    return "DIFFERENT_NUMERIC"


# ---------------------------------------------------------------------------
# relation assessment
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class RelationAssessment(Record):
    assessment_id: str
    left_claim_id: str
    right_claim_id: str
    relation: str
    rationale: str
    temporal_basis: str
    notice_ids: tuple[str, ...]
    governed_claim_ids: tuple[str, ...]
    identity_blocker: str | None
    supersession_scope_kind: str | None
    supersession_target_ids: tuple[str, ...]
    evidence: tuple[EvidenceRef, ...]
    outcome: CapabilityOutcome
    recorded_time: str

    def __post_init__(self) -> None:
        require_aware(self.recorded_time)
        if self.relation not in RELATION_CLASSES:
            raise ValueError(f"unknown relation class: {self.relation}")
        if self.left_claim_id == self.right_claim_id:
            raise ValueError("relation requires two distinct claims")
        if not self.rationale.strip():
            raise ValueError("relation assessment requires a rationale")
        if self.temporal_basis not in TEMPORAL_BASES:
            raise ValueError(f"unknown temporal basis: {self.temporal_basis}")
        if self.outcome.subject_kind != "CONTRADICTION_UPDATE_RELATION":
            raise ValueError("relation outcome must adjudicate the contradiction/update subject kind")
        if self.relation in _NOTICE_CLASSES and not (self.notice_ids and self.governed_claim_ids):
            raise ValueError("correction/retraction/supersession require notice evidence and governed claims")
        if self.relation == "SUPERSESSION":
            if self.supersession_scope_kind not in SUPERSESSION_SCOPES or not self.supersession_target_ids:
                raise ValueError("supersession requires explicit scope and superseded targets")
        elif self.supersession_scope_kind is not None or self.supersession_target_ids:
            raise ValueError("supersession scope is only valid on a supersession relation")
        if self.relation == "TEMPORAL_UPDATE" and self.temporal_basis != "DIFFERENT_EVENT_TIME":
            raise ValueError("temporal update requires distinct event times, not publication order")
        if self.relation in _CONFLICT_CLASSES and self.temporal_basis not in {
                "SAME_EVENT_TIME", "OVERLAPPING_EVENT_TIME"}:
            raise ValueError("conflict classes require an established shared event time")
        if self.relation == "IDENTITY_DISAGREEMENT" and not (self.identity_blocker or "").strip():
            raise ValueError("identity disagreement requires the recorded identity blocker")
        if self.relation == "UNRESOLVED" and not (
                self.identity_blocker or
                self.outcome.outcome in {"EPISTEMICALLY_UNRESOLVABLE", "SYSTEM_CAPABILITY_FAILURE"}):
            raise ValueError("UNRESOLVED requires an insufficiency demonstration, "
                             "a capability failure, or an identity blocker")


# ---------------------------------------------------------------------------
# versioned claim states — history is never destroyed
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ClaimVersionState(Record):
    state_id: str
    claim_id: str
    status: str
    version: int
    governing_notice_ids: tuple[str, ...]
    history: tuple[str, ...]
    recorded_time: str

    def __post_init__(self) -> None:
        require_aware(self.recorded_time)
        if self.status not in CLAIM_STATE_STATUSES:
            raise ValueError(f"unknown claim state status: {self.status}")
        if self.version < 1:
            raise ValueError("claim state version must be positive")
        if len(self.history) != self.version - 1:
            raise ValueError("claim state history may never be truncated or destroyed")
        if len(set(self.history)) != len(self.history):
            raise ValueError("claim state history contains duplicates")


def initial_claim_state(claim_id: str) -> ClaimVersionState:
    if not claim_id.strip():
        raise ValueError("claim state requires a claim")
    return ClaimVersionState(stable_id("claim-state", claim_id, 1), claim_id,
                             "CURRENT", 1, (), (), now_utc())


def apply_notice(claim_state: ClaimVersionState, relation: RelationAssessment) -> ClaimVersionState:
    """Apply a notice-backed relation, retaining the prior state in history."""
    if relation.relation not in _NOTICE_STATUS:
        raise ValueError("only notice-backed relations can version a claim state")
    if claim_state.claim_id not in relation.governed_claim_ids:
        raise ValueError("notice scope does not govern this claim")
    if claim_state.status == "RETRACTED":
        raise ValueError("a retracted claim state is terminal")
    return ClaimVersionState(
        stable_id("claim-state", claim_state.claim_id, claim_state.version + 1,
                  relation.assessment_id),
        claim_state.claim_id, _NOTICE_STATUS[relation.relation], claim_state.version + 1,
        tuple(dict.fromkeys(claim_state.governing_notice_ids + relation.notice_ids)),
        claim_state.history + (claim_state.state_id,), now_utc())


# ---------------------------------------------------------------------------
# relate
# ---------------------------------------------------------------------------

_EVENT_BASIS = {"EQUAL": "SAME_EVENT_TIME", "DISJOINT": "DIFFERENT_EVENT_TIME",
                "LEFT_WITHIN_RIGHT": "OVERLAPPING_EVENT_TIME",
                "RIGHT_WITHIN_LEFT": "OVERLAPPING_EVENT_TIME",
                "OVERLAPPING": "OVERLAPPING_EVENT_TIME", "UNKNOWN": "EVENT_TIME_UNKNOWN"}


def _definition_applies(definition: DefinitionEvidence, left_claim: ClaimUnit,
                        right_claim: ClaimUnit) -> bool:
    term = _norm(definition.term)
    for claim in (left_claim, right_claim):
        text = _norm(" ".join((claim.normalized_statement, claim.predicate, claim.object_or_value)))
        if term not in text:
            return False
    return True


def _assessment(left_claim: ClaimUnit, right_claim: ClaimUnit, relation: str, rationale: str, *,
                basis: str, notice_ids: tuple[str, ...] = (), governed: tuple[str, ...] = (),
                identity_blocker: str | None = None, scope_kind: str | None = None,
                scope_targets: tuple[str, ...] = (), evidence: tuple[EvidenceRef, ...] = (),
                outcome_state: str = "RESOLVED_CORRECTLY",
                qualifications: tuple[str, ...] = (), demonstration: str | None = None,
                failure_class: str | None = None) -> RelationAssessment:
    outcome = capability_outcome(
        subject_kind="CONTRADICTION_UPDATE_RELATION",
        subject_id=stable_id("claim-pair", left_claim.claim_id, right_claim.claim_id),
        outcome=outcome_state, rationale=rationale, material_qualifications=qualifications,
        evidence_insufficiency_demonstration=demonstration, capability_failure_class=failure_class)
    return RelationAssessment(
        stable_id("relation-assessment", left_claim.claim_id, right_claim.claim_id, relation),
        left_claim.claim_id, right_claim.claim_id, relation, rationale, basis,
        tuple(notice_ids), tuple(governed), identity_blocker, scope_kind,
        tuple(scope_targets), tuple(evidence), outcome, now_utc())


def relate(left_claim: ClaimUnit, right_claim: ClaimUnit,
           notices: Iterable[NoticeRecord] = (), frames: Iterable[TemporalFrame] = (), *,
           identity: SubjectIdentity | None = None,
           definitions: Iterable[DefinitionEvidence] = (),
           claim_documents: Mapping[str, str] | None = None) -> RelationAssessment:
    if left_claim.claim_id == right_claim.claim_id:
        raise ValueError("relation requires two distinct claims")
    for claim in (left_claim, right_claim):
        if claim.polarity not in POLARITIES:
            raise ValueError(f"claim polarity outside shared vocabulary: {claim.polarity}")
        if claim.modality not in MODALITIES:
            raise ValueError(f"claim modality outside shared vocabulary: {claim.modality}")

    if isinstance(frames, Mapping):
        frames = frames.values()
    frame_index: dict[str, TemporalFrame] = {}
    for frame in frames:
        if frame.claim_id in frame_index:
            raise ValueError("duplicate temporal frame for one claim")
        frame_index[frame.claim_id] = frame
    left_frame = frame_index.get(left_claim.claim_id) or temporal_frame(
        claim_id=left_claim.claim_id, event_time=left_claim.temporal_scope)
    right_frame = frame_index.get(right_claim.claim_id) or temporal_frame(
        claim_id=right_claim.claim_id, event_time=right_claim.temporal_scope)

    pair = {left_claim.claim_id, right_claim.claim_id}

    # 1. Explicit notices govern first (evidence-backed editorial acts).
    applicable: list[tuple[NoticeRecord, tuple[str, ...]]] = []
    for notice in notices:
        if notice.scope_kind == "WHOLE_DOCUMENT_SCOPE":
            if claim_documents is None:
                raise ValueError("whole-document notice requires a claim-to-document mapping")
            governed = tuple(sorted(
                cid for cid in pair if claim_documents.get(cid) == notice.target_document_id))
        else:
            governed = tuple(sorted(set(notice.target_claim_ids) & pair))
        if governed:
            applicable.append((notice, governed))
    if applicable:
        priority = {"RETRACTS": 0, "CORRECTS": 1, "SUPERSEDES": 2}
        notice, governed = sorted(
            applicable, key=lambda item: (priority[item[0].content_relation], item[0].notice_id))[0]
        relation = {"RETRACTS": "RETRACTION", "CORRECTS": "CORRECTION",
                    "SUPERSEDES": "SUPERSESSION"}[notice.content_relation]
        scope_kind = notice.scope_kind if relation == "SUPERSESSION" else None
        scope_targets: tuple[str, ...] = ()
        if relation == "SUPERSESSION":
            scope_targets = (governed if notice.scope_kind == "WHOLE_DOCUMENT_SCOPE"
                             else tuple(sorted(notice.target_claim_ids)))
        return _assessment(
            left_claim, right_claim, relation,
            f"an explicit {notice.content_relation} notice with span evidence governs "
            f"the claim pair; scope {notice.scope_kind}",
            basis="NOTICE_GOVERNED", notice_ids=(notice.notice_id,), governed=governed,
            scope_kind=scope_kind, scope_targets=scope_targets, evidence=notice.evidence)

    # 2. Identity gate: an unresolved subject identity blocks forced classes.
    same_subject = _norm(left_claim.subject) == _norm(right_claim.subject)
    identity_blocker: str | None = None
    identity_evidence: tuple[EvidenceRef, ...] = ()
    if not same_subject:
        if identity is not None:
            covered = ({_norm(identity.left_subject), _norm(identity.right_subject)} ==
                       {_norm(left_claim.subject), _norm(right_claim.subject)})
            if not covered:
                raise ValueError("identity link does not cover this claim pair")
            identity_evidence = identity.evidence
            if identity.outcome == "SAME_ENTITY_ACCEPTED":
                same_subject = True
            elif identity.outcome == "DIFFERENT_ENTITY":
                return _assessment(
                    left_claim, right_claim, "NO_CONFLICT",
                    "the subjects are resolved to different entities; the claims do not "
                    "share a proposition and cannot conflict",
                    basis="NOT_COMPARED", evidence=identity_evidence)
            else:
                identity_blocker = (f"subject identity between the claims is {identity.outcome}; "
                                    "no conflict class may be forced before identity resolution")
        else:
            identity_blocker = ("subject identity unresolved: the claims name different subjects "
                                "and no identity resolution was supplied")
    if identity_blocker:
        compatible = (_norm(left_claim.predicate) != _norm(right_claim.predicate) or
                      (left_claim.polarity == right_claim.polarity and
                       _compare_values(left_claim.object_or_value, right_claim.object_or_value) == "EQUAL"))
        if compatible:
            return _assessment(
                left_claim, right_claim, "NO_CONFLICT",
                "the claims are compatible under every admissible identity resolution",
                basis="NOT_COMPARED", evidence=identity_evidence)
        return _assessment(
            left_claim, right_claim, "IDENTITY_DISAGREEMENT",
            "the claims would conflict only if their subjects are the same entity, "
            "and that identity is unresolved; the conflict class is withheld",
            basis="NOT_COMPARED", identity_blocker=identity_blocker,
            evidence=identity_evidence,
            outcome_state="RESOLVED_WITH_MATERIAL_QUALIFICATION",
            qualifications=("UNRESOLVED",))

    # 3. Different predicates assert different aspects: no shared proposition.
    if _norm(left_claim.predicate) != _norm(right_claim.predicate):
        return _assessment(
            left_claim, right_claim, "NO_CONFLICT",
            "the claims predicate different aspects of the subject; there is no shared "
            "proposition to conflict", basis="NOT_COMPARED")

    # 4. A conditional claim qualifies a categorical one without conflict.
    if (left_claim.modality == "CONDITIONAL") != (right_claim.modality == "CONDITIONAL"):
        return _assessment(
            left_claim, right_claim, "QUALIFICATION",
            "one claim is conditional and does not assert the fact outright; it qualifies "
            "the categorical claim without conflicting", basis="NOT_COMPARED")

    left_geo = frozenset(_norm(item) for item in left_claim.geographic_scope)
    right_geo = frozenset(_norm(item) for item in right_claim.geographic_scope)
    if left_geo and right_geo and not (left_geo & right_geo):
        return _assessment(
            left_claim, right_claim, "NO_CONFLICT",
            "the claims cover disjoint geographic scopes and can both hold",
            basis="NOT_COMPARED")

    value_status = _compare_values(left_claim.object_or_value, right_claim.object_or_value)
    if value_status in {"UNIT_UNINTERPRETED", "DIMENSION_MISMATCH"}:
        return _assessment(
            left_claim, right_claim, "UNRESOLVED",
            "both values are numeric but their units or multipliers cannot be normalized "
            "to a common dimension; the evidence suffices for comparison yet the system "
            "cannot interpret it", basis="NOT_COMPARED",
            outcome_state="SYSTEM_CAPABILITY_FAILURE", failure_class="SEMANTIC_TYPE_ERROR")

    polarity_same = left_claim.polarity == right_claim.polarity
    compatible_values = polarity_same and value_status == "EQUAL"

    # 5. Nested or partially overlapping geographic scope is a scope
    #    relation, never flattened into contradiction or update.
    if left_geo != right_geo:
        nested = (bool(left_geo) != bool(right_geo) or left_geo < right_geo or right_geo < left_geo)
        if nested:
            if compatible_values:
                return _assessment(
                    left_claim, right_claim, "QUALIFICATION",
                    "the narrower-scope claim restates the broader claim's value for a "
                    "sub-scope; it qualifies without conflicting", basis="NOT_COMPARED")
            return _assessment(
                left_claim, right_claim, "SCOPE_DIFFERENCE",
                "the values differ across nested geographic scopes; each may be true of "
                "its own scope", basis="NOT_COMPARED")
        if compatible_values:
            return _assessment(
                left_claim, right_claim, "NO_CONFLICT",
                "partially overlapping scopes carry the same value", basis="NOT_COMPARED")
        return _assessment(
            left_claim, right_claim, "SCOPE_DIFFERENCE",
            "the values differ across partially overlapping geographic scopes",
            basis="NOT_COMPARED")

    event_relation = left_frame.event_relation(right_frame)
    basis = _EVENT_BASIS[event_relation]

    # 6. Compatible propositions are NO_CONFLICT; an update additionally
    #    requires an actual value difference (never overclassified).
    if compatible_values:
        return _assessment(
            left_claim, right_claim, "NO_CONFLICT",
            "the claims assert the same value for the same proposition; a temporal update "
            "requires an actual value difference, publication recency alone changes nothing",
            basis=basis)

    # 7. Recorded definitional divergence explains divergent values and is
    #    surfaced, never hidden behind a contradiction class.
    matching_definitions = [item for item in definitions
                            if _definition_applies(item, left_claim, right_claim)]
    if matching_definitions:
        evidence = tuple(ref for item in matching_definitions for ref in item.evidence)
        terms = ", ".join(sorted(_norm(item.term) for item in matching_definitions))
        return _assessment(
            left_claim, right_claim, "DEFINITION_DIFFERENCE",
            f"the claims use divergent recorded definitions of: {terms}; their values are "
            "not comparable and no contradiction is licensed", basis=basis, evidence=evidence)

    if event_relation == "UNKNOWN":
        return _assessment(
            left_claim, right_claim, "UNRESOLVED",
            "divergent values without event-time evidence cannot be adjudicated",
            basis=basis, outcome_state="EPISTEMICALLY_UNRESOLVABLE",
            demonstration=(
                "The claims assert divergent values for the same subject and predicate, but "
                "neither claim nor its temporal frame carries event-time evidence for the "
                "period each value is about. Without event time, a temporal update cannot be "
                "distinguished from a contradiction; publication order cannot substitute for "
                "event time, and no correction or supersession notice is present."))

    if event_relation == "DISJOINT":
        return _assessment(
            left_claim, right_claim, "TEMPORAL_UPDATE",
            "the same subject and predicate carry different values over disjoint event "
            "periods; both values may be true of their own periods", basis=basis)

    if event_relation in {"LEFT_WITHIN_RIGHT", "RIGHT_WITHIN_LEFT"}:
        return _assessment(
            left_claim, right_claim, "SCOPE_DIFFERENCE",
            "the values differ across nested temporal scopes; each may be true of its own "
            "period", basis=basis)

    if not polarity_same:
        return _assessment(
            left_claim, right_claim, "POLARITY_CONFLICT",
            "the claims assert opposite polarity for the same proposition, scope and event "
            "time", basis=basis)

    if value_status == "DIFFERENT_NUMERIC":
        return _assessment(
            left_claim, right_claim, "NUMERIC_DISAGREEMENT",
            "the unit-normalized numeric values disagree for the same proposition and event "
            "time", basis=basis)

    return _assessment(
        left_claim, right_claim, "LOGICAL_CONTRADICTION",
        "the claims assert incompatible values for the same subject, predicate, scope and "
        "event time", basis=basis)
