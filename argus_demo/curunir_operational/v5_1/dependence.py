"""Dependence and corroboration capability for V5.1 (contract Section 14).

Closes the verified V5 failure modes: UNKNOWN_TREATED_AS_INDEPENDENT,
SINGLETON_IS_NOT_INDEPENDENCE, FAMILY_AND_MEMBER_DEPENDENCE_MUST_BE_SEPARATE,
partial-dependence collapse, and shared-data-vs-shared-analysis confusion.

Governing invariant: absence of a discovered dependency is never confirmed
independence.  Independence is a positive finding requiring affirmative
evidence; genuine underdetermination is EPISTEMICALLY_UNRESOLVABLE with a
demonstration; uninterpretable evidence raises SYSTEM_CAPABILITY_FAILURE.

Research shadow only.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Mapping

from ..v4.models import Record, require_aware, stable_id
from .models import (
    DEPENDENCE_SIGNAL_KINDS, DEPENDENCE_STATES, NON_CORROBORATING_STATES,
    CapabilityOutcome, EvidenceRef, capability_outcome, now_utc,
)

SIGNAL_DIRECTIONS = frozenset({"LEFT_FROM_RIGHT", "RIGHT_FROM_LEFT", "UNDIRECTED"})
SIGNAL_STRENGTHS = frozenset({"STRONG", "MODERATE", "WEAK"})

# The only two states that may ever add independent corroboration (14.6).
CORROBORATING_STATES = frozenset({"INDEPENDENCE_SUPPORTED", "SHARED_DATA_INDEPENDENT_ANALYSIS"})

# Functional groupings over models.DEPENDENCE_SIGNAL_KINDS; no new kinds.
_EXPLICIT_DERIVATION = frozenset({
    "EXPLICIT_CITATION", "EXPLICIT_ATTRIBUTION", "EXPLICIT_REUSE_STATEMENT"})
_TRANSLATION = frozenset({"TRANSLATION_ALIGNMENT"})
_SYNDICATION = frozenset({"WIRE_SERVICE_INDICATION", "PRESS_RELEASE_FINGERPRINT"})
_CONTENT_FINGERPRINT = frozenset({
    "NORMALIZED_PARAGRAPH_OVERLAP", "SHARED_UNIQUE_ERROR", "SHARED_TABLE_OR_GRAPHIC"})
_COMMON_BASIS = frozenset({
    "COMMON_OFFICIAL_ANNEX", "COMMON_PRIMARY_DATASET", "COMMON_SOURCE_LINK",
    "COMMON_QUOTED_INDIVIDUAL", "IDENTICAL_QUOTATION"})
_UNDERDETERMINED = frozenset({
    "COMMON_ANONYMOUS_ATTRIBUTION", "PUBLICATION_TIMING", "DOCUMENT_METADATA"})
INDEPENDENCE_EVIDENCE_KINDS = frozenset({
    "DISTINCT_AUTHORSHIP_EVIDENCE", "DISTINCT_REPORTING_DETAIL",
    "ON_THE_RECORD_ORIGINAL_INTERVIEW"})
DISTINCT_ANALYSIS_KINDS = frozenset({
    "DISTINCT_REPORTING_DETAIL", "DISTINCT_AUTHORSHIP_EVIDENCE"})

_DERIVATION_KINDS = _EXPLICIT_DERIVATION | _TRANSLATION | _SYNDICATION | _CONTENT_FINGERPRINT
_DEPENDENCE_KINDS = _DERIVATION_KINDS | _COMMON_BASIS | _UNDERDETERMINED
_DERIVATIVE_STATES = frozenset({
    "DERIVATIVE_CONFIRMED", "TRANSLATION_DERIVATIVE", "SYNDICATION_DERIVATIVE",
    "MIRROR_MANIFESTATION"})
_STRENGTH_WEIGHT = {"STRONG": 0.9, "MODERATE": 0.6, "WEAK": 0.3}

# A vocabulary drift in models.py must fail loudly, never leave signals
# silently uninterpreted.
if _DEPENDENCE_KINDS | INDEPENDENCE_EVIDENCE_KINDS != DEPENDENCE_SIGNAL_KINDS:
    raise RuntimeError("dependence signal grouping no longer covers the models vocabulary")

LEGACY_INDEPENDENCE_STATES = frozenset({"INDEPENDENT", "DEPENDENT", "UNKNOWN_DEPENDENCE"})


class DependenceCapabilityError(ValueError):
    """Evidence was sufficient but uninterpretable; carries the failure record."""

    def __init__(self, message: str, outcome: CapabilityOutcome) -> None:
        super().__init__(message)
        self.outcome = outcome


@dataclass(frozen=True)
class PublicationRef(Record):
    publication_id: str
    source_family_id: str
    publisher: str
    language: str
    content_fingerprint: str | None = None

    def __post_init__(self) -> None:
        if not self.publication_id.strip() or not self.source_family_id.strip():
            raise ValueError("publication reference requires publication and family identifiers")


@dataclass(frozen=True)
class DependenceSignal(Record):
    signal_id: str
    kind: str
    direction: str
    strength: str
    detail: str
    evidence_refs: tuple[EvidenceRef, ...]
    negated: bool = False
    negation_basis: str = ""
    contested: bool = False
    contest_basis: str = ""

    def __post_init__(self) -> None:
        if self.kind not in DEPENDENCE_SIGNAL_KINDS:
            raise ValueError(f"unknown dependence signal kind: {self.kind}")
        if self.direction not in SIGNAL_DIRECTIONS:
            raise ValueError(f"unknown signal direction: {self.direction}")
        if self.strength not in SIGNAL_STRENGTHS:
            raise ValueError(f"unknown signal strength: {self.strength}")
        if not self.detail.strip():
            raise ValueError("dependence signal requires detail")
        if not self.evidence_refs:
            raise ValueError("dependence signal requires evidence references")
        if self.negated and not self.negation_basis.strip():
            raise ValueError("negated signal requires the negation basis")
        if self.contested and not self.contest_basis.strip():
            raise ValueError("contested signal requires the contest basis")
        if self.negated and self.contested:
            raise ValueError("a signal cannot be simultaneously negated and contested")
        if self.kind in INDEPENDENCE_EVIDENCE_KINDS and self.direction != "UNDIRECTED":
            raise ValueError("independence evidence cannot carry a derivation direction")


def dependence_signal(*, kind: str, strength: str, detail: str,
                      evidence_refs: Iterable[EvidenceRef], direction: str = "UNDIRECTED",
                      negated: bool = False, negation_basis: str = "",
                      contested: bool = False, contest_basis: str = "") -> DependenceSignal:
    return DependenceSignal(
        stable_id("dependence-signal", kind, direction, strength, detail),
        kind, direction, strength, detail, tuple(evidence_refs),
        negated, negation_basis, contested, contest_basis)


@dataclass(frozen=True)
class DependenceExplanation(Record):
    """Section 14.3 explanation record attached to every assessment."""

    positive_evidence: tuple[str, ...]
    negative_evidence: tuple[str, ...]
    unresolved_signals: tuple[str, ...]
    alternative_explanations: tuple[str, ...]
    confidence_dimensions: Mapping[str, float]
    independence_affirmatively_supported: bool
    only_absence_of_discovered_dependence: bool

    def __post_init__(self) -> None:
        if self.independence_affirmatively_supported and self.only_absence_of_discovered_dependence:
            raise ValueError("affirmative independence support contradicts absence-only support")
        if self.independence_affirmatively_supported and not self.positive_evidence:
            raise ValueError("affirmative independence support requires positive evidence")
        for key, value in self.confidence_dimensions.items():
            if not 0.0 <= float(value) <= 1.0:
                raise ValueError(f"confidence dimension out of bounds: {key}")


@dataclass(frozen=True)
class DependenceAssessment(Record):
    assessment_id: str
    left_publication_id: str
    right_publication_id: str
    state: str
    signals: tuple[DependenceSignal, ...]
    explanation: DependenceExplanation
    outcome: CapabilityOutcome
    recorded_time: str

    def __post_init__(self) -> None:
        require_aware(self.recorded_time)
        if self.state not in DEPENDENCE_STATES:
            raise ValueError(f"unknown dependence state: {self.state}")
        if self.left_publication_id == self.right_publication_id:
            raise ValueError("dependence assessment requires two distinct publications")
        exp = self.explanation
        active = tuple(s for s in self.signals if not s.negated and not s.contested)
        if self.state == "INDEPENDENCE_SUPPORTED":
            if exp.only_absence_of_discovered_dependence:
                raise ValueError(
                    "absence of discovered dependence can never support INDEPENDENCE_SUPPORTED")
            if not exp.independence_affirmatively_supported:
                raise ValueError("INDEPENDENCE_SUPPORTED requires affirmative positive evidence")
            # Free-text explanation never substitutes for typed signals; the
            # constructor enforces the SAME Section 14.2 multi-signal rule the
            # classifier applies (>=2 signals, >=2 kinds, >=1 STRONG).
            if not _independence_rule_met(self.signals):
                raise ValueError(
                    "INDEPENDENCE_SUPPORTED requires the full multi-signal rule: "
                    "at least two un-negated, un-contested typed independence "
                    "signals of distinct kinds including one STRONG")
            # ANY surviving dependence-kind signal — including underdetermined
            # timing/metadata and contested-but-unresolved citations — blocks
            # affirmative independence; those belong to UNKNOWN or DISPUTED.
            if any(s.kind in _DEPENDENCE_KINDS and not s.negated for s in self.signals):
                raise ValueError(
                    "INDEPENDENCE_SUPPORTED cannot coexist with un-negated "
                    "dependence-kind signals (contested ones included)")
        if self.state == "SHARED_DATA_INDEPENDENT_ANALYSIS":
            if not exp.independence_affirmatively_supported:
                raise ValueError(
                    "SHARED_DATA_INDEPENDENT_ANALYSIS requires affirmative distinct-analysis evidence")
            if not any(s.kind in DISTINCT_ANALYSIS_KINDS for s in active):
                raise ValueError(
                    "SHARED_DATA_INDEPENDENT_ANALYSIS requires typed distinct-analysis signals")
            if not any(s.kind in _COMMON_BASIS | {"SHARED_TABLE_OR_GRAPHIC"} for s in active):
                raise ValueError(
                    "SHARED_DATA_INDEPENDENT_ANALYSIS requires a typed shared-data signal")
        if self.state == "NO_DEPENDENCE_FOUND":
            if not exp.only_absence_of_discovered_dependence:
                raise ValueError("NO_DEPENDENCE_FOUND records only the absence of discovered dependence")
            if exp.independence_affirmatively_supported or exp.positive_evidence:
                raise ValueError("NO_DEPENDENCE_FOUND cannot carry positive independence evidence")
        if self.state in _DERIVATIVE_STATES | {"COMMON_EVIDENCE_BASIS_CONFIRMED"}:
            if exp.independence_affirmatively_supported:
                raise ValueError("a derivative or common-basis pair cannot be affirmatively independent")
            if not exp.positive_evidence:
                raise ValueError(f"{self.state} requires positive evidence")
        if self.state == "PARTIAL_DEPENDENCE":
            active = tuple(s for s in self.signals if not s.negated)
            has_derived = any(s.kind in _DEPENDENCE_KINDS for s in active)
            has_original = any(s.kind in INDEPENDENCE_EVIDENCE_KINDS for s in active)
            if not (has_derived and has_original):
                raise ValueError(
                    "PARTIAL_DEPENDENCE requires both derived-content and original-content "
                    "evidence; it may not be collapsed to either pole")
        if self.state == "INDEPENDENCE_UNKNOWN":
            if self.outcome.outcome != "EPISTEMICALLY_UNRESOLVABLE":
                raise ValueError(
                    "INDEPENDENCE_UNKNOWN requires an EPISTEMICALLY_UNRESOLVABLE outcome "
                    "with an evidence-insufficiency demonstration")
            if not exp.unresolved_signals:
                raise ValueError("INDEPENDENCE_UNKNOWN requires the unresolved signals it rests on")
        elif self.outcome.outcome == "EPISTEMICALLY_UNRESOLVABLE":
            raise ValueError("only INDEPENDENCE_UNKNOWN may carry an EPISTEMICALLY_UNRESOLVABLE outcome")
        if self.state == "DEPENDENCE_DISPUTED" and not exp.unresolved_signals:
            raise ValueError("DEPENDENCE_DISPUTED requires the contested signals")
        if self.outcome.outcome == "SYSTEM_CAPABILITY_FAILURE":
            raise ValueError("capability failures are raised, never recorded as a dependence state")


def _descriptor(signal: DependenceSignal) -> str:
    return f"{signal.kind}[{signal.strength}]:{signal.signal_id}"


def _pair_subject(left: PublicationRef, right: PublicationRef) -> str:
    return stable_id("dependence-pair", *sorted((left.publication_id, right.publication_id)))


def _confidence(all_signals: tuple[DependenceSignal, ...],
                licensing: tuple[DependenceSignal, ...]) -> dict[str, float]:
    firm = tuple(s for s in all_signals if not s.negated and not s.contested)
    return {
        "signal_strength_uncalibrated": max(
            (_STRENGTH_WEIGHT[s.strength] for s in licensing), default=0.0),
        "signal_agreement_uncalibrated": (
            len(firm) / len(all_signals)) if all_signals else 1.0,
    }


_MATERIAL_QUALIFICATIONS = {
    "PARTIAL_DEPENDENCE": ("MIXED_ORIGINAL_AND_DERIVED_CONTENT",),
    "DEPENDENCE_DISPUTED": ("DISPUTED",),
    "NO_DEPENDENCE_FOUND": ("ONLY_ABSENCE_OF_DISCOVERED_DEPENDENCE",),
}


def _assessment(left: PublicationRef, right: PublicationRef, state: str,
                all_signals: tuple[DependenceSignal, ...], *, rationale: str,
                licensing: tuple[DependenceSignal, ...] = (),
                positive: tuple[str, ...] = (), negative: tuple[str, ...] = (),
                unresolved: tuple[str, ...] = (), alternatives: tuple[str, ...] = (),
                affirmative: bool = False, only_absence: bool = False,
                demonstration: str | None = None) -> DependenceAssessment:
    if state == "INDEPENDENCE_UNKNOWN":
        outcome = capability_outcome(
            subject_kind="SOURCE_DEPENDENCE_RELATION", subject_id=_pair_subject(left, right),
            outcome="EPISTEMICALLY_UNRESOLVABLE", rationale=rationale,
            evidence_insufficiency_demonstration=demonstration)
    elif state in _MATERIAL_QUALIFICATIONS:
        outcome = capability_outcome(
            subject_kind="SOURCE_DEPENDENCE_RELATION", subject_id=_pair_subject(left, right),
            outcome="RESOLVED_WITH_MATERIAL_QUALIFICATION", rationale=rationale,
            material_qualifications=_MATERIAL_QUALIFICATIONS[state])
    else:
        outcome = capability_outcome(
            subject_kind="SOURCE_DEPENDENCE_RELATION", subject_id=_pair_subject(left, right),
            outcome="RESOLVED_CORRECTLY", rationale=rationale)
    explanation = DependenceExplanation(
        positive, negative, unresolved, alternatives,
        _confidence(all_signals, licensing), affirmative, only_absence)
    return DependenceAssessment(
        stable_id("dependence-assessment", left.publication_id, right.publication_id, state,
                  sorted(s.signal_id for s in all_signals)),
        left.publication_id, right.publication_id, state, all_signals, explanation,
        outcome, now_utc())


def _independence_rule_met(signals: Iterable[DependenceSignal]) -> bool:
    """Section 14.2 multi-signal rule: a single (or weak-only) signal never suffices."""
    active = tuple(s for s in signals
                   if not s.negated and not s.contested and s.kind in INDEPENDENCE_EVIDENCE_KINDS)
    kinds = {s.kind for s in active}
    return len(active) >= 2 and len(kinds) >= 2 and any(s.strength == "STRONG" for s in active)


def classify_dependence(left_pub: PublicationRef, right_pub: PublicationRef,
                        signals: Iterable[DependenceSignal]) -> DependenceAssessment:
    if left_pub.publication_id == right_pub.publication_id:
        raise ValueError("a publication cannot be assessed against itself; "
                         "a singleton can never certify independence")
    all_signals = tuple(signals)
    negated = tuple(s for s in all_signals if s.negated)
    active = tuple(s for s in all_signals if not s.negated)
    contested = tuple(s for s in active if s.contested)
    firm = tuple(s for s in active if not s.contested)
    negative = tuple(_descriptor(s) for s in negated)

    # Identical manifestation dominates every textual signal.
    if left_pub.content_fingerprint and left_pub.content_fingerprint == right_pub.content_fingerprint:
        return _assessment(
            left_pub, right_pub, "MIRROR_MANIFESTATION", all_signals,
            rationale="identical content fingerprints identify the same manifestation",
            positive=(f"IDENTICAL_CONTENT_FINGERPRINT:{left_pub.content_fingerprint}",),
            negative=negative)

    # Strong derivation evidence in both directions cannot be interpreted:
    # sufficient evidence, failed interpretation -> capability failure, never
    # a silent unknown.
    strong_directions = {s.direction for s in firm
                         if s.kind in _DERIVATION_KINDS and s.strength == "STRONG"
                         and s.direction != "UNDIRECTED"}
    if {"LEFT_FROM_RIGHT", "RIGHT_FROM_LEFT"} <= strong_directions:
        failure = capability_outcome(
            subject_kind="SOURCE_DEPENDENCE_RELATION",
            subject_id=_pair_subject(left_pub, right_pub),
            outcome="SYSTEM_CAPABILITY_FAILURE",
            rationale="strong un-negated derivation evidence points in both directions; "
                      "the signal combination cannot be interpreted",
            capability_failure_class="DEPENDENCE_EVIDENCE_UNINTERPRETED")
        raise DependenceCapabilityError(
            "uninterpretable opposed strong derivation directions", failure)

    if contested:
        return _assessment(
            left_pub, right_pub, "DEPENDENCE_DISPUTED", all_signals,
            rationale="material dependence signals are contested and unresolved",
            licensing=contested, unresolved=tuple(_descriptor(s) for s in contested),
            negative=negative,
            alternatives=("CONTESTED_SIGNAL_GENUINE", "CONTESTED_SIGNAL_REFUTED"))

    dependence_active = tuple(s for s in firm if s.kind in _DEPENDENCE_KINDS)
    independence_active = tuple(s for s in firm if s.kind in INDEPENDENCE_EVIDENCE_KINDS)
    derivation_active = tuple(s for s in dependence_active if s.kind in _DERIVATION_KINDS)

    def strong_of(kinds: frozenset[str]) -> tuple[DependenceSignal, ...]:
        return tuple(s for s in derivation_active if s.kind in kinds and s.strength == "STRONG")

    translation = strong_of(_TRANSLATION)
    syndication = strong_of(_SYNDICATION)
    explicit = strong_of(_EXPLICIT_DERIVATION)
    fingerprint = strong_of(_CONTENT_FINGERPRINT)
    combined = tuple(s for s in derivation_active if s.strength in {"STRONG", "MODERATE"})
    combination_confirmed = combined if len({s.kind for s in combined}) >= 2 else ()
    licensing_derivation = translation or syndication or explicit or fingerprint or combination_confirmed
    original_content = tuple(s for s in independence_active if s.strength in {"STRONG", "MODERATE"})

    if licensing_derivation:
        if original_content:
            return _assessment(
                left_pub, right_pub, "PARTIAL_DEPENDENCE", all_signals,
                rationale="confirmed derivation coexists with affirmative original content; "
                          "the pair is neither fully derivative nor independent",
                licensing=tuple(licensing_derivation) + original_content,
                positive=tuple(_descriptor(s) for s in tuple(licensing_derivation) + original_content),
                negative=negative,
                unresolved=tuple(_descriptor(s) for s in dependence_active
                                 if s not in licensing_derivation))
        if translation:
            state, rationale = "TRANSLATION_DERIVATIVE", (
                "strong translation alignment confirms one publication as a translation derivative")
        elif syndication:
            state, rationale = "SYNDICATION_DERIVATIVE", (
                "wire or syndication evidence confirms syndicated derivative content")
        else:
            state, rationale = "DERIVATIVE_CONFIRMED", (
                "explicit reuse or content-fingerprint evidence confirms derivation")
        return _assessment(
            left_pub, right_pub, state, all_signals, rationale=rationale,
            licensing=tuple(licensing_derivation),
            positive=tuple(_descriptor(s) for s in licensing_derivation), negative=negative,
            unresolved=tuple(_descriptor(s) for s in dependence_active
                             if s not in licensing_derivation))

    common_confirmed = tuple(s for s in dependence_active
                             if s.kind in _COMMON_BASIS and s.strength in {"STRONG", "MODERATE"})
    if common_confirmed:
        residual = tuple(_descriptor(s) for s in dependence_active if s not in common_confirmed)
        distinct_analysis = tuple(s for s in independence_active
                                  if s.kind in DISTINCT_ANALYSIS_KINDS and s.strength != "WEAK")
        if distinct_analysis:
            return _assessment(
                left_pub, right_pub, "SHARED_DATA_INDEPENDENT_ANALYSIS", all_signals,
                rationale="publications share an evidence basis but carry affirmative "
                          "distinct-analysis evidence",
                licensing=common_confirmed + distinct_analysis,
                positive=tuple(_descriptor(s) for s in common_confirmed + distinct_analysis),
                negative=negative, unresolved=residual, affirmative=True)
        return _assessment(
            left_pub, right_pub, "COMMON_EVIDENCE_BASIS_CONFIRMED", all_signals,
            rationale="publications rest on a common evidence basis without distinct-analysis "
                      "evidence",
            licensing=common_confirmed,
            positive=tuple(_descriptor(s) for s in common_confirmed),
            negative=negative, unresolved=residual,
            alternatives=("UNDISCLOSED_DIRECT_DERIVATION_BETWEEN_THE_PUBLICATIONS",
                          "INDEPENDENT_ANALYSIS_OF_THE_SHARED_BASIS_NOT_YET_EVIDENCED"))

    if dependence_active:
        kinds = sorted({s.kind for s in dependence_active})
        demonstration = (
            "Dependence between the two publications was searched but remains underdetermined: "
            f"the signals {', '.join(kinds)} are individually insufficient to confirm derivation "
            "and cannot be affirmatively negated from the available public record, because an "
            "undisclosed common upstream source (for example an unpublished briefing or an "
            "embargoed release) would produce the same observable pattern.")
        return _assessment(
            left_pub, right_pub, "INDEPENDENCE_UNKNOWN", all_signals,
            rationale="discovered signals neither confirm dependence nor support independence",
            unresolved=tuple(_descriptor(s) for s in dependence_active + independence_active),
            negative=negative,
            alternatives=("UNDISCLOSED_COMMON_UPSTREAM_SOURCE",
                          "DIRECT_DERIVATION_BELOW_DETECTION_THRESHOLD",
                          "GENUINE_INDEPENDENCE"),
            demonstration=demonstration)

    if independence_active:
        if _independence_rule_met(independence_active):
            return _assessment(
                left_pub, right_pub, "INDEPENDENCE_SUPPORTED", all_signals,
                rationale="multiple affirmative independence signals with no un-negated "
                          "dependence signal support independence",
                licensing=independence_active,
                positive=tuple(_descriptor(s) for s in independence_active),
                negative=negative,
                alternatives=("DEPENDENCE_OUTSIDE_THE_SEARCHED_PUBLIC_RECORD",),
                affirmative=True)
        return _assessment(
            left_pub, right_pub, "NO_DEPENDENCE_FOUND", all_signals,
            rationale="independence evidence is below the multi-signal threshold; only the "
                      "absence of discovered dependence remains",
            unresolved=tuple(_descriptor(s) for s in independence_active),
            negative=negative,
            alternatives=("UNDISCOVERED_DEPENDENCE",
                          "GENUINE_INDEPENDENCE_WITHOUT_SURVIVING_EVIDENCE"),
            only_absence=True)

    return _assessment(
        left_pub, right_pub, "NO_DEPENDENCE_FOUND", all_signals,
        rationale="no un-negated dependence signal was discovered; this is not a finding of "
                  "independence",
        negative=negative,
        alternatives=("UNDISCOVERED_DEPENDENCE",
                      "GENUINE_INDEPENDENCE_WITHOUT_SURVIVING_EVIDENCE"),
        only_absence=True)


@dataclass(frozen=True)
class ClaimFamilySummary(Record):
    """Section 14.6 corroboration arithmetic for one claim family."""

    summary_id: str
    claim_family_id: str
    publication_count: int
    manifestation_count: int
    source_family_count: int
    evidence_basis_count: int
    confirmed_derivative_count: int
    partial_dependence_count: int
    independence_supported_count: int
    no_dependence_found_count: int
    independence_unknown_count: int
    independent_corroboration_count: int
    corroborating_pair_ids: tuple[str, ...]
    recorded_time: str

    def __post_init__(self) -> None:
        require_aware(self.recorded_time)
        for name in ("publication_count", "manifestation_count", "source_family_count",
                     "evidence_basis_count", "confirmed_derivative_count",
                     "partial_dependence_count", "independence_supported_count",
                     "no_dependence_found_count", "independence_unknown_count",
                     "independent_corroboration_count"):
            if getattr(self, name) < 0:
                raise ValueError(f"negative counter: {name}")
        if self.independent_corroboration_count != len(self.corroborating_pair_ids):
            raise ValueError("corroboration count must equal its per-pair records")
        if self.publication_count <= 1 and self.independent_corroboration_count:
            raise ValueError("a singleton claim family can never have independent corroboration")
        if self.independent_corroboration_count > self.publication_count * (self.publication_count - 1) // 2:
            raise ValueError("corroboration count exceeds the possible publication pairs")


def corroboration_arithmetic(*, claim_family_id: str,
                             publications: Iterable[PublicationRef],
                             assessments: Iterable[DependenceAssessment],
                             evidence_basis_ids: Iterable[str] = ()) -> ClaimFamilySummary:
    pubs = tuple(publications)
    ids = tuple(p.publication_id for p in pubs)
    if len(set(ids)) != len(ids):
        raise ValueError("duplicate publication in claim family")
    known = set(ids)
    values = tuple(assessments)
    seen_pairs: set[frozenset[str]] = set()
    for item in values:
        pair = frozenset((item.left_publication_id, item.right_publication_id))
        if not pair <= known:
            raise ValueError("assessment references a publication outside the claim family")
        if pair in seen_pairs:
            raise ValueError("conflicting duplicate assessment for one publication pair")
        seen_pairs.add(pair)
    corroborating = tuple(sorted(
        item.assessment_id for item in values
        if item.state in CORROBORATING_STATES and item.state not in NON_CORROBORATING_STATES))
    return ClaimFamilySummary(
        stable_id("claim-family-summary", claim_family_id, sorted(ids)),
        claim_family_id,
        len(pubs),
        len({p.content_fingerprint or p.publication_id for p in pubs}),
        len({p.source_family_id for p in pubs}),
        len(tuple(dict.fromkeys(evidence_basis_ids))),
        sum(item.state in _DERIVATIVE_STATES for item in values),
        sum(item.state == "PARTIAL_DEPENDENCE" for item in values),
        sum(item.state == "INDEPENDENCE_SUPPORTED" for item in values),
        sum(item.state == "NO_DEPENDENCE_FOUND" for item in values),
        sum(item.state == "INDEPENDENCE_UNKNOWN" for item in values),
        len(corroborating), corroborating, now_utc())


@dataclass(frozen=True)
class FamilyDependenceRecord(Record):
    """Family-level dependence computed separately from member-pair states."""

    record_id: str
    family_id: str
    member_publication_ids: tuple[str, ...]
    family_state: str
    pair_states: tuple[tuple[str, str, str], ...]
    rationale: str
    recorded_time: str

    def __post_init__(self) -> None:
        require_aware(self.recorded_time)
        if self.family_state not in DEPENDENCE_STATES:
            raise ValueError(f"unknown family dependence state: {self.family_state}")
        if not self.member_publication_ids:
            raise ValueError("family requires at least one member")
        if len(set(self.member_publication_ids)) != len(self.member_publication_ids):
            raise ValueError("duplicate family member")
        members = set(self.member_publication_ids)
        expected = {frozenset((a, b)) for a in members for b in members if a < b}
        provided = {frozenset((left, right)) for left, right, _ in self.pair_states}
        if provided != expected:
            raise ValueError(
                "family state requires a per-pair dependence record for every member pair")
        if len(members) == 1 and self.family_state == "INDEPENDENCE_SUPPORTED":
            raise ValueError("a singleton family can never be independence-supported")
        if self.family_state in CORROBORATING_STATES:
            if len(members) < 2 or not all(
                    state in CORROBORATING_STATES for _, _, state in self.pair_states):
                raise ValueError(
                    "family independence requires every member pair affirmatively independent")


_FAMILY_DEPENDENCE_POLE = _DERIVATIVE_STATES | {"COMMON_EVIDENCE_BASIS_CONFIRMED"}


def family_state(*, family_id: str, member_publication_ids: Iterable[str],
                 pair_assessments: Iterable[DependenceAssessment]) -> FamilyDependenceRecord:
    members = tuple(dict.fromkeys(member_publication_ids))
    values = tuple(pair_assessments)
    pair_states = tuple(sorted(
        (min(a.left_publication_id, a.right_publication_id),
         max(a.left_publication_id, a.right_publication_id), a.state) for a in values))
    states = [state for _, _, state in pair_states]
    if len(members) <= 1:
        family, rationale = "NO_DEPENDENCE_FOUND", (
            "singleton family: no member pair exists, so independence can never be established")
    elif "DEPENDENCE_DISPUTED" in states:
        family, rationale = "DEPENDENCE_DISPUTED", "at least one member pair is disputed"
    elif "INDEPENDENCE_UNKNOWN" in states:
        family, rationale = "INDEPENDENCE_UNKNOWN", (
            "at least one member pair is unresolved, blocking any family independence claim")
    elif "PARTIAL_DEPENDENCE" in states:
        family, rationale = "PARTIAL_DEPENDENCE", "at least one member pair is partially dependent"
    else:
        has_dependence = any(state in _FAMILY_DEPENDENCE_POLE for state in states)
        has_independence = any(state in CORROBORATING_STATES for state in states)
        has_none_found = any(state == "NO_DEPENDENCE_FOUND" for state in states)
        if has_dependence and has_independence:
            family, rationale = "PARTIAL_DEPENDENCE", (
                "member pairs mix confirmed dependence with affirmative independence")
        elif has_dependence:
            uniform = set(states) - {"NO_DEPENDENCE_FOUND"}
            if len(uniform) == 1:
                family = uniform.pop()
            elif uniform & _DERIVATIVE_STATES:
                family = "DERIVATIVE_CONFIRMED"
            else:
                family = "COMMON_EVIDENCE_BASIS_CONFIRMED"
            rationale = "resolved member pairs show internal dependence"
        elif has_independence and has_none_found:
            family, rationale = "NO_DEPENDENCE_FOUND", (
                "some member pairs carry only absence of discovered dependence; family "
                "independence is not affirmed")
        elif has_independence:
            family = ("SHARED_DATA_INDEPENDENT_ANALYSIS"
                      if "SHARED_DATA_INDEPENDENT_ANALYSIS" in states else "INDEPENDENCE_SUPPORTED")
            rationale = "every member pair is affirmatively independent"
        else:
            family, rationale = "NO_DEPENDENCE_FOUND", (
                "no member pair carries a discovered dependence; independence is not affirmed")
    return FamilyDependenceRecord(
        stable_id("family-dependence", family_id, sorted(members), family),
        family_id, members, family, pair_states, rationale, now_utc())


@dataclass(frozen=True)
class LegacyBasisBridge(Record):
    """Conservative V4 evidence-basis bridge (frozen V5 repair invariant, generalized)."""

    bridge_id: str
    basis_id: str
    legacy_state: str
    mapped_state: str
    positive_evidence_signal_ids: tuple[str, ...]
    rationale: str
    recorded_time: str

    def __post_init__(self) -> None:
        require_aware(self.recorded_time)
        if self.legacy_state not in LEGACY_INDEPENDENCE_STATES:
            raise ValueError(f"unknown legacy independence state: {self.legacy_state}")
        if self.mapped_state not in DEPENDENCE_STATES:
            raise ValueError(f"unknown mapped dependence state: {self.mapped_state}")
        if self.mapped_state == "INDEPENDENCE_SUPPORTED" and not self.positive_evidence_signal_ids:
            raise ValueError(
                "legacy INDEPENDENT cannot become INDEPENDENCE_SUPPORTED without positive evidence")


def from_legacy_basis(evidence_basis_record: Any, *,
                      positive_independence_signals: Iterable[DependenceSignal] = ()) -> LegacyBasisBridge:
    if isinstance(evidence_basis_record, Mapping):
        basis_id = evidence_basis_record["basis_id"]
        legacy = evidence_basis_record["independence_state"]
    else:
        basis_id = evidence_basis_record.basis_id
        legacy = evidence_basis_record.independence_state
    if legacy not in LEGACY_INDEPENDENCE_STATES:
        raise ValueError(f"unknown legacy independence state: {legacy}")
    signals = tuple(positive_independence_signals)
    for item in signals:
        if item.kind not in INDEPENDENCE_EVIDENCE_KINDS or item.negated or item.contested:
            raise ValueError("legacy bridge accepts only un-negated positive independence evidence")
    if legacy == "DEPENDENT":
        mapped = "DERIVATIVE_CONFIRMED"
        rationale = "legacy DEPENDENT keeps its dependence finding"
    elif legacy == "UNKNOWN_DEPENDENCE":
        mapped = "INDEPENDENCE_UNKNOWN"
        rationale = "legacy unknown dependence remains unresolved; it is never treated as independent"
    elif _independence_rule_met(signals):
        mapped = "INDEPENDENCE_SUPPORTED"
        rationale = "legacy INDEPENDENT upgraded only because affirmative multi-signal evidence was provided"
    else:
        mapped = "NO_DEPENDENCE_FOUND"
        rationale = ("legacy INDEPENDENT downgraded: no detected dependency is not confirmed "
                     "independence")
    return LegacyBasisBridge(
        stable_id("legacy-basis-bridge", basis_id, legacy, mapped), basis_id, legacy, mapped,
        tuple(item.signal_id for item in signals), rationale, now_utc())
