"""Dependence classification repaired for V5.2 (contract Section 12).

V5.1's ``classify_dependence`` scored 32 of 42 exposed publication pairs
correct against the blind-reviewer majority (semantic correctness 0.7619) and
exercised only 4 of the 11 classes the dependence ontology defines.  The ten
errors concentrate in four mechanisms, and this module closes each of them:

* 4 pairs were called COMMON_EVIDENCE_BASIS_CONFIRMED where reviewers read
  TRANSLATION_DERIVATIVE.  Official multilingual issues of one text were
  reduced to a shared evidence basis because translation was only ever
  detected from a declared language pair or a hand-supplied alignment signal.
  Section 12.2 now detects translation from content correspondence: numbers,
  dates and proper nouns survive translation, so their ordered agreement plus
  near-identical paragraph structure across two languages is the signal.
* 3 pairs were called DERIVATIVE_CONFIRMED where reviewers read
  PARTIAL_DEPENDENCE.  Signal strength was aggregated over the whole
  publication, so confirmed reuse of one section asserted derivation of
  everything.  Section 12.3 separates the two questions: signal strength says
  how firmly reuse is established, an overlap ratio says how much of the
  derivative publication it accounts for, and only the ratio decides full
  against partial derivation.
* 1 pair was called DERIVATIVE_CONFIRMED where reviewers read
  MIRROR_MANIFESTATION: two manifestations of a single publication were
  reasoned about as two publications, because manifestation grouping was
  never consulted before derivation.  Section 12.1 now runs that grouping
  first, so a mirror can never be reported as derivation between publications.
* 1 pair was called INDEPENDENCE_UNKNOWN where reviewers read
  COMMON_EVIDENCE_BASIS_CONFIRMED: an identified artefact both publications
  rest on was graded WEAK and therefore fell below a strength test that was
  never appropriate for it — whether two publications cite the same named
  annex or dataset is directly checkable, not a matter of textual confidence.

What is deliberately *not* changed: independence remains a positive finding.
Absence of a discovered dependency stays NO_DEPENDENCE_FOUND or
INDEPENDENCE_UNKNOWN, INDEPENDENCE_SUPPORTED keeps the full V5.1 multi-signal
rule, and no state in ``NON_CORROBORATING_STATES`` may ever be counted as
corroboration.  The V5.1 corroboration and family arithmetic is imported and
reused rather than reimplemented, and the returned record type is unchanged,
so every V5.1 consumer keeps working.

All rules are language-general and deterministic; no ML, no campaign, source,
entity or fixture answer is encoded anywhere.

Research shadow only.
"""
from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, replace
from difflib import SequenceMatcher
from typing import Any, Iterable, Mapping

from ..v4.models import require_aware
# The V5.1 signal groupings are imported, never restated: a vocabulary drift
# in the frozen module must reach this one, not silently diverge from it.
from ..v5_1.dependence import (
    CORROBORATING_STATES, DISTINCT_ANALYSIS_KINDS, INDEPENDENCE_EVIDENCE_KINDS,
    SIGNAL_DIRECTIONS, DependenceAssessment, DependenceCapabilityError,
    DependenceSignal, PublicationRef, _COMMON_BASIS, _CONTENT_FINGERPRINT,
    _DEPENDENCE_KINDS, _DERIVATION_KINDS, _EXPLICIT_DERIVATION, _SYNDICATION,
    _TRANSLATION, _assessment, _descriptor, _independence_rule_met,
    _pair_subject, corroboration_arithmetic, dependence_signal,  # noqa: F401
    family_state,  # noqa: F401
)
from ..v5_1.models import (
    DEPENDENCE_STATES, NON_CORROBORATING_STATES, EvidenceRef, Record,
    capability_outcome, now_utc, sha256, stable_id,
)
from ..v5_1.source_identity import (
    DocumentManifestation, PublicationRecord, group_manifestations, is_mirror_pair,
)

# ---------------------------------------------------------------------------
# Section 12 — thresholds
# ---------------------------------------------------------------------------

# Fraction of the DERIVATIVE publication's substantive content that the source
# must account for before full derivation is asserted.  0.85 leaves headroom
# for the roughly one unit in seven that a full republication legitimately
# adds — headline, dateline, standfirst, local framing sentence — while any
# lower coverage means at least a sixth of the derivative is unaccounted for,
# which is material original content by any reviewer's reading.
FULL_DERIVATION_COVERAGE = 0.85

# Below this coverage a measured overlap is not by itself evidence that the
# two PUBLICATIONS stand in a dependence relation: a stray shared sentence is
# more parsimoniously explained by a common upstream basis, so the pair falls
# through to the common-basis and underdetermination tests instead.
MATERIAL_OVERLAP_FLOOR = 0.15

# Composite structural-correspondence score above which two publications in
# different languages are read as translations of one text.
TRANSLATION_ALIGNMENT_THRESHOLD = 0.75
# Above this the correspondence is strong enough to be recorded as a STRONG
# typed signal rather than a moderate one.
TRANSLATION_STRONG_SCORE = 0.85
# Every supported invariant component must clear this floor: a high composite
# must not be carried by paragraph counts alone.
TRANSLATION_COMPONENT_FLOOR = 0.60
# "Near-identical paragraph counts" is a necessary condition, not a weight.
TRANSLATION_STRUCTURE_FLOOR = 0.80
# Two texts with almost no numbers, dates or names align trivially; require a
# minimum of surviving invariant material before believing an alignment.
MIN_INVARIANT_SUPPORT = 4
# A coverage fraction over one or two units is noise, not a measurement.
MIN_COVERAGE_UNITS = 3
# V5.8.1 defect D26.  A translation replaces the wording; a mirror repeats it.
# When the substantive units on both sides of a declared language difference are
# the same strings, the language labels disagree with the text — and the text is
# the evidence.  Above this fraction of the smaller side, the pair is read as
# untranslated repetition and the declared-language routes to
# TRANSLATION_DERIVATIVE are refused.
UNTRANSLATED_REPETITION_FLOOR = 0.9
# One or two shared units is a shared dateline, not a repeated document.
MIN_REPETITION_UNITS = 2
# A substantive unit is a sentence carrying real content; short fragments
# (headings, datelines, captions) are page furniture and are not counted.
MIN_UNIT_TOKENS = 4
# Scripts written without spaces produce few tokens per sentence, so a
# character floor keeps the unit definition language-general.
MIN_UNIT_CHARACTERS = 24

# Common-basis kinds that name a specific identified artefact.  Whether two
# publications rest on the same annex, dataset or outbound source is directly
# checkable; strength grades our reading of a text, not the identity of a
# document, so these confirm a common basis at any strength (Section 12.2).
IDENTIFIED_ARTIFACT_BASIS_KINDS = frozenset({
    "COMMON_OFFICIAL_ANNEX", "COMMON_PRIMARY_DATASET", "COMMON_SOURCE_LINK"})

OVERLAP_BASES = frozenset({
    "LEXICAL_UNIT_COVERAGE", "TRANSLATION_INVARIANT_COVERAGE", "CALLER_SUPPLIED_COVERAGE"})

# A cross-language coverage measure cannot license a paragraph-overlap signal:
# the sentence surface does not survive translation, so only these bases may
# be recorded as content-fingerprint evidence.
_FINGERPRINT_SYNTHESIS_BASES = frozenset({"LEXICAL_UNIT_COVERAGE", "CALLER_SUPPLIED_COVERAGE"})

# States that may add independent corroboration, after the non-corroborating
# discipline is applied (Section 12.4).
CORROBORATION_ELIGIBLE_STATES = CORROBORATING_STATES - NON_CORROBORATING_STATES

# ---------------------------------------------------------------------------
# Section 12 — the trigger table
#
# The V5.1 clean run exercised 4 of 11 classes, which means most of the
# ontology was never tested.  A class nobody can reach is a claim, not a
# capability, so every class states the condition that reaches it here.
# ---------------------------------------------------------------------------

DEPENDENCE_TRIGGERS: Mapping[str, str] = {
    "MIRROR_MANIFESTATION": (
        "manifestation grouping resolves both references to one publication: a shared "
        "publication record, a manifestation group holding a capture from each side, an "
        "identical manifestation content hash, or identical publication content "
        "fingerprints; checked before any derivation reasoning"),
    "TRANSLATION_DERIVATIVE": (
        "an explicit translation notice, the same intellectual work or document series "
        "issued in two languages, or structural content correspondence across two "
        "languages, with the derivative's content fully accounted for by the source"),
    "SYNDICATION_DERIVATIVE": (
        "strong wire-service or press-release fingerprint evidence, with the derivative's "
        "content fully accounted for by the source"),
    "DERIVATIVE_CONFIRMED": (
        "strong explicit reuse, citation or attribution evidence, or strong content "
        "fingerprint evidence, or two concordant moderate derivation kinds, with measured "
        f"coverage at or above {FULL_DERIVATION_COVERAGE:.2f} or no coverage measurement "
        "available"),
    "PARTIAL_DEPENDENCE": (
        "confirmed derivation whose measured coverage of the derivative publication falls "
        f"below {FULL_DERIVATION_COVERAGE:.2f}, or confirmed derivation coexisting with "
        "affirmative original-content evidence"),
    "COMMON_EVIDENCE_BASIS_CONFIRMED": (
        "an identified shared artefact (annex, dataset, source link) at any strength, or a "
        "strong or moderate inferential common-basis signal, or two concordant weak "
        "common-basis kinds, with no affirmative distinct-analysis evidence"),
    "SHARED_DATA_INDEPENDENT_ANALYSIS": (
        "a confirmed common evidence basis together with affirmative distinct-analysis "
        "evidence of at least moderate strength"),
    "INDEPENDENCE_SUPPORTED": (
        "at least two un-negated, un-contested typed independence signals of distinct "
        "kinds including one strong, and no un-negated dependence-kind signal; absence of "
        "discovered dependence never reaches this state"),
    "NO_DEPENDENCE_FOUND": (
        "no un-negated dependence signal was discovered, or independence evidence stayed "
        "below the multi-signal rule; records only the absence of a discovered dependency"),
    "INDEPENDENCE_UNKNOWN": (
        "un-negated dependence signals were discovered that neither confirm derivation nor "
        "a common basis and cannot be affirmatively negated, with an evidence-insufficiency "
        "demonstration"),
    "DEPENDENCE_DISPUTED": (
        "at least one material dependence signal is contested and the contest is unresolved"),
}

if frozenset(DEPENDENCE_TRIGGERS) != DEPENDENCE_STATES:
    raise RuntimeError("dependence trigger table no longer covers the models state vocabulary")
if CORROBORATING_STATES & NON_CORROBORATING_STATES:
    raise RuntimeError("a corroborating state may never appear in the non-corroborating set")
if IDENTIFIED_ARTIFACT_BASIS_KINDS - _COMMON_BASIS:
    raise RuntimeError("identified-artefact kinds must stay inside the common-basis grouping")


def dependence_class_coverage(assessments: Iterable[DependenceAssessment]) -> dict[str, Any]:
    """Report which dependence classes a run actually exercised.

    The milestone requires all 11 classes live, so the report names the
    missing ones together with the condition that would reach them.
    """
    counts = Counter(item.state for item in assessments)
    exercised = tuple(sorted(counts))
    missing = tuple(sorted(DEPENDENCE_STATES - set(counts)))
    return {
        "classes_required": len(DEPENDENCE_STATES),
        "classes_exercised": len(exercised),
        "exercised_classes": exercised,
        "missing_classes": missing,
        "coverage_complete": not missing,
        "per_class_counts": {state: counts.get(state, 0) for state in sorted(DEPENDENCE_STATES)},
        "missing_class_triggers": {state: DEPENDENCE_TRIGGERS[state] for state in missing},
    }


# ---------------------------------------------------------------------------
# Section 12.2 — language-general content profiles
#
# Everything below is deterministic text arithmetic over stdlib regular
# expressions and difflib.  No model, no lexicon, no language list.
# ---------------------------------------------------------------------------

_WORD_TOKEN = re.compile(r"\w+", re.UNICODE)
_LETTER_TOKEN = re.compile(r"[^\W\d_]{2,}", re.UNICODE)
_NUMBER = re.compile(r"\d+(?:[.,\u00a0\s]\d{3})*(?:[.,]\d+)?")
_ISO_DATE = re.compile(r"\b(\d{4})-(\d{1,2})-(\d{1,2})\b")
_NUMERIC_DATE = re.compile(r"\b(\d{1,2})[./](\d{1,2})[./](\d{2,4})\b")
_YEAR = re.compile(r"\b(?:1[5-9]\d{2}|2\d{3})\b")
_PARAGRAPH_SPLIT = re.compile(r"\n\s*\n+")
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?;:。！？])\s+|\n+")


def _normalize_number(token: str) -> str:
    """Locale-neutral numeric key.

    A separator followed by exactly three digits is a group separator in every
    locale that uses one; anything else is the decimal mark.  The residual
    ambiguity (a bare thousands group) resolves identically on both sides of a
    comparison, which is all the alignment measure needs.
    """
    digits = re.sub(r"[\s\u00a0]", "", token)
    tail = re.search(r"[.,](\d+)$", digits)
    if tail is not None and len(tail.group(1)) != 3:
        head = re.sub(r"[.,]", "", digits[: tail.start()])
        return f"{head}.{tail.group(1)}"
    return re.sub(r"[.,]", "", digits)


def _date_key(components: tuple[str, ...]) -> str:
    """Order-insensitive date key.

    Day-first and month-first orderings are genuinely ambiguous across
    locales, so the components are sorted: the same calendar date written
    either way yields one key, and no ordering convention is assumed.
    """
    values = sorted(int(part) for part in components if part)
    return "d:" + "-".join(str(value) for value in values)


def _extract_dates(text: str) -> tuple[tuple[str, ...], str]:
    """Return the ordered date keys plus the text with date spans blanked.

    Dates are masked before numbers are read so a year is never counted twice,
    once as a date and once as a bare quantity.
    """
    keys: list[str] = []

    def take(match: re.Match[str]) -> str:
        keys.append(_date_key(match.groups() or (match.group(),)))
        return " " * (match.end() - match.start())

    masked = _ISO_DATE.sub(take, text)
    masked = _NUMERIC_DATE.sub(take, masked)
    masked = _YEAR.sub(take, masked)
    return tuple(keys), masked


def _proper_nouns(text: str) -> tuple[str, ...]:
    """Ordered capitalized tokens.

    Deliberately naive: languages differ in what they capitalize, so this is
    a candidate set, not a classifier.  Cross-language comparison restricts it
    to the vocabulary present on both sides, which is what leaves the names.
    In scripts without case the sequence is empty and the component simply
    carries no weight.
    """
    return tuple(match.group() for match in _LETTER_TOKEN.finditer(text)
                 if len(match.group()) >= 3 and match.group()[0].isupper())


def _substantive_units(text: str) -> tuple[str, ...]:
    """Hashed keys of the sentences that carry content.

    Units are hashed so a derived record never carries captured source text,
    and normalized (case, punctuation, whitespace) so that reformatting during
    republication does not hide reuse.
    """
    units: list[str] = []
    for chunk in _SENTENCE_SPLIT.split(text):
        normalized = " ".join(_WORD_TOKEN.findall(chunk.casefold()))
        if not normalized:
            continue
        if len(normalized.split()) >= MIN_UNIT_TOKENS or len(normalized) >= MIN_UNIT_CHARACTERS:
            units.append(sha256(normalized)[:16])
    return tuple(units)


@dataclass(frozen=True)
class ContentProfile(Record):
    """Structural fingerprint of one publication's text (Section 12.2)."""

    profile_id: str
    publication_id: str
    language: str
    paragraph_count: int
    substantive_units: tuple[str, ...]
    numeric_sequence: tuple[str, ...]
    date_sequence: tuple[str, ...]
    proper_noun_sequence: tuple[str, ...]
    recorded_time: str

    def __post_init__(self) -> None:
        require_aware(self.recorded_time)
        if not self.publication_id.strip() or not self.language.strip():
            raise ValueError("content profile requires publication and language identity")
        if self.paragraph_count < 0:
            raise ValueError("negative paragraph count")

    @property
    def invariant_support(self) -> int:
        return len(self.numeric_sequence) + len(self.date_sequence)


def content_profile(*, publication_id: str, language: str, text: str) -> ContentProfile:
    dates, masked = _extract_dates(text)
    numbers = tuple(_normalize_number(match.group()) for match in _NUMBER.finditer(masked))
    paragraphs = tuple(part for part in _PARAGRAPH_SPLIT.split(text) if part.strip())
    return ContentProfile(
        stable_id("content-profile", publication_id, sha256(text)), publication_id, language,
        len(paragraphs), _substantive_units(text), numbers, dates, _proper_nouns(text),
        now_utc())


# ---------------------------------------------------------------------------
# Section 12.2 — translation alignment from content correspondence
# ---------------------------------------------------------------------------

_ALIGNMENT_WEIGHTS: Mapping[str, float] = {
    "numeric_sequence": 0.35,
    "proper_noun_sequence": 0.30,
    "paragraph_structure": 0.20,
    "date_sequence": 0.15,
}
_INVARIANT_COMPONENTS = ("numeric_sequence", "date_sequence", "proper_noun_sequence")


@dataclass(frozen=True)
class TranslationAlignment(Record):
    """Deterministic structural correspondence between two publications."""

    alignment_id: str
    left_publication_id: str
    right_publication_id: str
    score: float
    components: Mapping[str, float]
    supported_components: tuple[str, ...]
    aligned: bool
    rationale: str
    recorded_time: str

    def __post_init__(self) -> None:
        require_aware(self.recorded_time)
        if not 0.0 <= self.score <= 1.0:
            raise ValueError("alignment score out of bounds")
        for name, value in self.components.items():
            if not 0.0 <= float(value) <= 1.0:
                raise ValueError(f"alignment component out of bounds: {name}")
        if self.aligned and self.score < TRANSLATION_ALIGNMENT_THRESHOLD:
            raise ValueError("an aligned pair must clear the alignment threshold")
        if not self.rationale.strip():
            raise ValueError("alignment requires a rationale")


def _ordered_ratio(left: tuple[str, ...], right: tuple[str, ...]) -> float:
    """Order-sensitive similarity of two token sequences.

    Order matters: independent reports of one event share values, a
    translation shares them in the same sequence.
    """
    return SequenceMatcher(None, left, right, autojunk=False).ratio()


def _shared_name_ratio(left: tuple[str, ...], right: tuple[str, ...]) -> tuple[float, int]:
    """Compare only the names present on both sides, in order.

    Restricting to the shared vocabulary removes the language-specific
    capitalization noise (some languages capitalize every noun) and leaves the
    material that actually survives translation.
    """
    left_keys = tuple(token.casefold() for token in left)
    right_keys = tuple(token.casefold() for token in right)
    shared = set(left_keys) & set(right_keys)
    if len(shared) < 2:
        return 0.0, len(shared)
    return (_ordered_ratio(tuple(k for k in left_keys if k in shared),
                           tuple(k for k in right_keys if k in shared)), len(shared))


def untranslated_repetition(left: ContentProfile | None,
                            right: ContentProfile | None) -> tuple[bool, str]:
    """Whether a declared language difference is contradicted by identical text.

    V5.8.1 defect D26: two identical English strings carried by the Arabic and
    Spanish manifestations of one page were classified TRANSLATION_DERIVATIVE on
    the strength of the declared language pair alone.  Nothing was translated —
    the same sentence was republished under two language labels.  Substantive
    units are hashes of normalized sentences, so their coincidence is a direct
    test of that, and it is language-general.
    """
    if left is None or right is None:
        return False, ""
    if left.language.casefold() == right.language.casefold():
        return False, ""
    left_units = Counter(left.substantive_units)
    right_units = Counter(right.substantive_units)
    smaller = min(sum(left_units.values()), sum(right_units.values()))
    if smaller < MIN_REPETITION_UNITS:
        return False, ""
    shared = sum((left_units & right_units).values())
    if shared / smaller < UNTRANSLATED_REPETITION_FLOOR:
        return False, ""
    return True, (
        f"{shared} of {smaller} substantive units are the same strings across a "
        f"declared {left.language}/{right.language} difference: the text was "
        "repeated, not translated")


def translation_alignment(left: ContentProfile, right: ContentProfile) -> TranslationAlignment:
    """Score structural correspondence across a language pair (Section 12.2)."""
    components: dict[str, float] = {}
    supported: list[str] = []
    for name, left_seq, right_seq in (
            ("numeric_sequence", left.numeric_sequence, right.numeric_sequence),
            ("date_sequence", left.date_sequence, right.date_sequence)):
        if left_seq or right_seq:
            components[name] = _ordered_ratio(left_seq, right_seq)
            supported.append(name)
    name_ratio, shared_names = _shared_name_ratio(
        left.proper_noun_sequence, right.proper_noun_sequence)
    if shared_names >= 2:
        components["proper_noun_sequence"] = name_ratio
        supported.append("proper_noun_sequence")
    widest = max(left.paragraph_count, right.paragraph_count)
    drift = abs(left.paragraph_count - right.paragraph_count)
    structure = (1.0 - drift / widest) if widest else 0.0
    components["paragraph_structure"] = structure
    supported.append("paragraph_structure")

    weight = sum(_ALIGNMENT_WEIGHTS[name] for name in supported)
    score = sum(_ALIGNMENT_WEIGHTS[name] * components[name] for name in supported) / weight

    invariants = min(left.invariant_support, right.invariant_support)
    languages_differ = left.language.casefold() != right.language.casefold()
    invariant_support = tuple(name for name in supported if name in _INVARIANT_COMPONENTS)
    # Conjunctive gate: the composite alone is not enough.  Two independent
    # reports of one event can share values; a translation shares them in
    # order, in a document of the same shape, in another language.
    repeated, repetition_detail = untranslated_repetition(left, right)
    if not languages_differ:
        aligned, rationale = False, "the two publications declare the same language"
    elif repeated:
        # D26.  Structural correspondence is trivially perfect between a text
        # and itself, so the composite must not be allowed to license a
        # translation reading here.
        aligned, rationale = False, repetition_detail
    elif not invariant_support:
        aligned, rationale = False, "no translation-invariant material to compare"
    elif invariants < MIN_INVARIANT_SUPPORT:
        aligned, rationale = False, "too little invariant material to believe an alignment"
    elif structure < TRANSLATION_STRUCTURE_FLOOR:
        aligned, rationale = False, "paragraph structures are not near-identical"
    elif any(components[name] < TRANSLATION_COMPONENT_FLOOR for name in invariant_support):
        aligned, rationale = False, "an invariant component contradicts the alignment"
    elif score < TRANSLATION_ALIGNMENT_THRESHOLD:
        aligned, rationale = False, "structural correspondence stays below the alignment threshold"
    else:
        aligned, rationale = True, (
            "ordered numeric, date and proper-noun correspondence across two languages in "
            "documents of the same shape")
    return TranslationAlignment(
        stable_id("translation-alignment", left.publication_id, right.publication_id),
        left.publication_id, right.publication_id, round(score, 4),
        {name: round(value, 4) for name, value in sorted(components.items())},
        tuple(sorted(supported)), aligned, rationale, now_utc())


# ---------------------------------------------------------------------------
# Section 12.3 — overlap coverage
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class OverlapMeasurement(Record):
    """How much of the derivative publication the source accounts for."""

    measurement_id: str
    derivative_publication_id: str
    source_publication_id: str
    basis: str
    covered_units: int
    total_units: int
    coverage_ratio: float
    recorded_time: str

    def __post_init__(self) -> None:
        require_aware(self.recorded_time)
        if self.basis not in OVERLAP_BASES:
            raise ValueError(f"unknown overlap basis: {self.basis}")
        if self.derivative_publication_id == self.source_publication_id:
            raise ValueError("overlap requires two distinct publications")
        if self.total_units <= 0 or not 0 <= self.covered_units <= self.total_units:
            raise ValueError("overlap counts must be a covered fraction of a positive total")
        # The ratio is recorded rounded to four decimals so the audit trail is
        # readable; it must still be the ratio it claims to be.
        if abs(self.coverage_ratio - self.covered_units / self.total_units) > 5e-5:
            raise ValueError("coverage ratio must equal its counted units")


def overlap_measurement(*, derivative_publication_id: str, source_publication_id: str,
                        covered_units: int, total_units: int,
                        basis: str = "CALLER_SUPPLIED_COVERAGE") -> OverlapMeasurement:
    return OverlapMeasurement(
        stable_id("overlap", derivative_publication_id, source_publication_id, basis),
        derivative_publication_id, source_publication_id, basis, covered_units, total_units,
        round(covered_units / total_units, 4) if total_units else 0.0, now_utc())


def measure_overlap(derivative: ContentProfile,
                    source: ContentProfile) -> OverlapMeasurement | None:
    """Fraction of the derivative's substantive content the source accounts for.

    Within one language the unit is the normalized sentence.  Across languages
    the sentence surface does not survive, so the unit is the translation
    invariant — numbers and dates.  Proper nouns are excluded from the
    coverage measure (though not from alignment) because capitalization
    conventions are language-specific, which would make the fraction
    incomparable between the two directions.  ``None`` means the pair carries
    too little material to measure, never that coverage is zero.
    """
    if derivative.language.casefold() == source.language.casefold():
        basis = "LEXICAL_UNIT_COVERAGE"
        left_items, right_items = derivative.substantive_units, source.substantive_units
    else:
        basis = "TRANSLATION_INVARIANT_COVERAGE"
        left_items = derivative.numeric_sequence + derivative.date_sequence
        right_items = source.numeric_sequence + source.date_sequence
    if len(left_items) < MIN_COVERAGE_UNITS:
        return None
    covered = sum((Counter(left_items) & Counter(right_items)).values())
    return OverlapMeasurement(
        stable_id("overlap", derivative.publication_id, source.publication_id, basis),
        derivative.publication_id, source.publication_id, basis, covered, len(left_items),
        round(covered / len(left_items), 4), now_utc())


# ---------------------------------------------------------------------------
# Section 12.1 — manifestation grouping
# ---------------------------------------------------------------------------

def _mirror_basis(left_pub: PublicationRef, right_pub: PublicationRef,
                  left_manifestations: tuple[DocumentManifestation, ...],
                  right_manifestations: tuple[DocumentManifestation, ...],
                  left_record: PublicationRecord | None,
                  right_record: PublicationRecord | None) -> tuple[str, ...]:
    """Every way the pair can turn out to be one publication seen twice."""
    basis: list[str] = []
    fingerprint = left_pub.content_fingerprint
    if fingerprint and fingerprint == right_pub.content_fingerprint:
        basis.append(f"IDENTICAL_CONTENT_FINGERPRINT:{fingerprint}")
    if left_record is not None and right_record is not None:
        if left_record.publication_id == right_record.publication_id:
            basis.append(f"SINGLE_PUBLICATION_RECORD:{left_record.publication_id}")
    if left_manifestations and right_manifestations:
        left_ids = {item.manifestation_id for item in left_manifestations}
        right_ids = {item.manifestation_id for item in right_manifestations}
        for publication_id, group in sorted(
                group_manifestations(left_manifestations + right_manifestations).items()):
            if any(is_mirror_pair(one, other) for one in group for other in group
                   if one.manifestation_id in left_ids and other.manifestation_id in right_ids):
                basis.append(f"SHARED_PUBLICATION_MANIFESTATION_GROUP:{publication_id}")
        shared_hashes = ({item.content_hash for item in left_manifestations}
                         & {item.content_hash for item in right_manifestations})
        for content_hash in sorted(shared_hashes):
            basis.append(f"IDENTICAL_MANIFESTATION_CONTENT_HASH:{content_hash}")
    return tuple(dict.fromkeys(basis))


# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------

_SIDE_DIRECTION = {"LEFT": "LEFT_FROM_RIGHT", "RIGHT": "RIGHT_FROM_LEFT"}

# Translation bases that rest on a declared fact rather than on a measured
# correspondence, and therefore record the signal at full strength.
_DECLARED_TRANSLATION_BASIS = frozenset({
    "EXPLICIT_TRANSLATION_NOTICE", "SAME_INTELLECTUAL_WORK_DIFFERENT_LANGUAGE",
    "SAME_DOCUMENT_SERIES_DIFFERENT_LANGUAGE"})


def _unique(signals: Iterable[DependenceSignal]) -> tuple[DependenceSignal, ...]:
    seen: set[str] = set()
    ordered: list[DependenceSignal] = []
    for item in signals:
        if item.signal_id not in seen:
            seen.add(item.signal_id)
            ordered.append(item)
    return tuple(ordered)


def _negatives(signals: Iterable[DependenceSignal]) -> tuple[str, ...]:
    return tuple(_descriptor(item) for item in signals if item.negated)


def _assess(left: PublicationRef, right: PublicationRef, state: str,
            all_signals: tuple[DependenceSignal, ...], *,
            audit_dimensions: Mapping[str, float] | None = None,
            **fields: Any) -> DependenceAssessment:
    """Construct the V5.1 assessment, then attach the V5.2 audit dimensions.

    The record type is deliberately unchanged so V5.1 consumers keep working;
    the ratio and threshold that drove the decision ride in the explanation
    (Section 12.3 requires the decision to be auditable, not merely correct).
    """
    base = _assessment(left, right, state, all_signals, **fields)
    if not audit_dimensions:
        return base
    explanation = replace(base.explanation, confidence_dimensions={
        **base.explanation.confidence_dimensions, **audit_dimensions})
    return replace(base, explanation=explanation)


def _derivative_side(signals: Iterable[DependenceSignal]) -> str | None:
    """Which publication the discovered derivation evidence points at."""
    directions = {item.direction for item in signals
                  if item.kind in _DERIVATION_KINDS and item.direction != "UNDIRECTED"}
    if directions == {"LEFT_FROM_RIGHT"}:
        return "LEFT"
    if directions == {"RIGHT_FROM_LEFT"}:
        return "RIGHT"
    return None


def _coverage(left_profile: ContentProfile | None, right_profile: ContentProfile | None,
              side: str | None) -> OverlapMeasurement | None:
    if left_profile is None or right_profile is None:
        return None
    if side == "LEFT":
        return measure_overlap(left_profile, right_profile)
    if side == "RIGHT":
        return measure_overlap(right_profile, left_profile)
    # Direction undeclared: the publication better accounted for by the other
    # is the derivation candidate, so the higher coverage governs.  Reading it
    # the other way would let an unknown direction manufacture partiality.
    measured = tuple(item for item in (measure_overlap(left_profile, right_profile),
                                       measure_overlap(right_profile, left_profile))
                     if item is not None)
    return max(measured, key=lambda item: item.coverage_ratio) if measured else None


def _translation_basis(left_pub: PublicationRef, right_pub: PublicationRef,
                       firm: tuple[DependenceSignal, ...], *,
                       left_record: PublicationRecord | None,
                       right_record: PublicationRecord | None,
                       left_document_series: str | None, right_document_series: str | None,
                       alignment: TranslationAlignment | None,
                       translation_notice: bool,
                       untranslated: bool = False) -> tuple[str, ...]:
    """Declared and undeclared routes to reading a pair as a translation.

    ``untranslated`` records that the two texts are the same strings.  It
    withdraws the routes that rest on the declared language pair alone — D26 —
    while leaving an explicit translation notice in place, because a notice is a
    positive declaration whose conflict with the text is a different finding
    from a metadata inference that was never evidence in the first place.
    """
    basis: list[str] = []
    languages_differ = left_pub.language.casefold() != right_pub.language.casefold()
    if translation_notice:
        basis.append("EXPLICIT_TRANSLATION_NOTICE")
    if any(item.kind in _TRANSLATION and item.strength == "STRONG" for item in firm):
        basis.append("DECLARED_TRANSLATION_ALIGNMENT_SIGNAL")
    if (not untranslated and left_record is not None and right_record is not None
            and left_record.work_id == right_record.work_id
            and left_record.language.casefold() != right_record.language.casefold()):
        basis.append("SAME_INTELLECTUAL_WORK_DIFFERENT_LANGUAGE")
    if (not untranslated and left_document_series and right_document_series
            and left_document_series == right_document_series and languages_differ):
        basis.append("SAME_DOCUMENT_SERIES_DIFFERENT_LANGUAGE")
    if alignment is not None and alignment.aligned:
        basis.append("STRUCTURAL_CONTENT_CORRESPONDENCE")
    return tuple(dict.fromkeys(basis))


def _synthetic(kind: str, *, strength: str, detail: str, publication_id: str,
               evidence_kind: str, direction: str = "UNDIRECTED") -> DependenceSignal:
    """Type a measured fact as a signal so it is adjudicable like any other.

    A measurement that only reached a rationale string could not be negated,
    contested or reviewed; typing it keeps the whole basis of the decision on
    the assessment record.
    """
    if direction not in SIGNAL_DIRECTIONS:
        raise ValueError(f"unknown signal direction: {direction}")
    return dependence_signal(
        kind=kind, strength=strength, direction=direction, detail=detail,
        evidence_refs=(EvidenceRef(evidence_kind, publication_id, None, detail),))


def _require_matching_profile(profile: ContentProfile | None,
                              publication: PublicationRef) -> None:
    if profile is not None and profile.publication_id != publication.publication_id:
        raise ValueError("content profile does not belong to the publication it was supplied for")


def classify_dependence(left_pub: PublicationRef, right_pub: PublicationRef,
                        signals: Iterable[DependenceSignal] = (), *,
                        left_manifestations: Iterable[DocumentManifestation] = (),
                        right_manifestations: Iterable[DocumentManifestation] = (),
                        left_publication_record: PublicationRecord | None = None,
                        right_publication_record: PublicationRecord | None = None,
                        left_profile: ContentProfile | None = None,
                        right_profile: ContentProfile | None = None,
                        left_document_series: str | None = None,
                        right_document_series: str | None = None,
                        translation_notice: bool = False,
                        overlap: OverlapMeasurement | None = None) -> DependenceAssessment:
    """Classify one publication pair into the Section 12 dependence ontology.

    Returns the V5.1 assessment record unchanged in type.  The optional
    keywords carry the evidence V5.1 had no way to consult: manifestation
    grouping, publication and series identity, content profiles, and a
    pre-computed content overlap.
    """
    if left_pub.publication_id == right_pub.publication_id:
        raise ValueError("a publication cannot be assessed against itself; "
                         "a singleton can never certify independence")
    _require_matching_profile(left_profile, left_pub)
    _require_matching_profile(right_profile, right_pub)
    if overlap is not None:
        pair = {left_pub.publication_id, right_pub.publication_id}
        if {overlap.derivative_publication_id, overlap.source_publication_id} != pair:
            raise ValueError("overlap measurement does not describe this publication pair")
    all_signals = tuple(signals)
    negative = _negatives(all_signals)

    # --- Section 12.1: manifestation grouping precedes derivation reasoning.
    # Two manifestations of one publication are not two publications, so the
    # derivation question does not arise for them at all.
    mirror = _mirror_basis(
        left_pub, right_pub, tuple(left_manifestations), tuple(right_manifestations),
        left_publication_record, right_publication_record)
    if mirror:
        return _assess(
            left_pub, right_pub, "MIRROR_MANIFESTATION", all_signals,
            rationale="manifestation grouping resolves the pair to two manifestations of "
                      "one publication, so no derivation holds between publications",
            positive=mirror, negative=negative)

    firm = tuple(item for item in all_signals if not item.negated and not item.contested)

    # Strong derivation evidence in both directions cannot be interpreted:
    # sufficient evidence, failed interpretation -> capability failure, never
    # a silent unknown (V5.1 rule, preserved).
    strong_directions = {item.direction for item in firm
                         if item.kind in _DERIVATION_KINDS and item.strength == "STRONG"
                         and item.direction != "UNDIRECTED"}
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

    contested = tuple(item for item in all_signals if not item.negated and item.contested)
    if contested:
        return _assess(
            left_pub, right_pub, "DEPENDENCE_DISPUTED", all_signals,
            rationale="material dependence signals are contested and unresolved",
            licensing=contested, unresolved=tuple(_descriptor(item) for item in contested),
            negative=negative,
            alternatives=("CONTESTED_SIGNAL_GENUINE", "CONTESTED_SIGNAL_REFUTED"))

    # --- Section 12.2: translation from content correspondence, not only from
    # declared metadata.
    alignment = (translation_alignment(left_profile, right_profile)
                 if left_profile is not None and right_profile is not None else None)
    untranslated, _ = untranslated_repetition(left_profile, right_profile)
    basis = _translation_basis(
        left_pub, right_pub, firm, left_record=left_publication_record,
        right_record=right_publication_record, left_document_series=left_document_series,
        right_document_series=right_document_series, alignment=alignment,
        translation_notice=translation_notice, untranslated=untranslated)
    side = _derivative_side(firm)
    if basis and not any(item.kind in _TRANSLATION for item in firm):
        declared = bool(_DECLARED_TRANSLATION_BASIS & set(basis))
        score = alignment.score if alignment is not None else 0.0
        synthetic = _synthetic(
            "TRANSLATION_ALIGNMENT",
            strength="STRONG" if declared or score >= TRANSLATION_STRONG_SCORE else "MODERATE",
            detail=f"language pair {left_pub.language}/{right_pub.language}; "
                   f"alignment score {score:.2f}; basis {', '.join(basis)}",
            publication_id=left_pub.publication_id, evidence_kind="TRANSLATION_ALIGNMENT",
            direction=_SIDE_DIRECTION.get(side or "", "UNDIRECTED"))
        all_signals += (synthetic,)
        firm += (synthetic,)

    # --- Section 12.3: coverage of the derivative publication.
    measurement = overlap if overlap is not None else _coverage(left_profile, right_profile, side)
    if (measurement is not None and measurement.basis in _FINGERPRINT_SYNTHESIS_BASES
            and measurement.coverage_ratio >= MATERIAL_OVERLAP_FLOOR
            and not any(item.kind in _CONTENT_FINGERPRINT for item in firm)):
        # Strength records how firmly reuse is established, never how much of
        # the publication it covers: identical substantive sentences at this
        # scale are not independently producible.  Coverage is a separate
        # question, answered below.  Conflating the two is V5.1 defect (b).
        synthetic = _synthetic(
            "NORMALIZED_PARAGRAPH_OVERLAP", strength="STRONG",
            detail=f"{measurement.basis.lower()} accounts for {measurement.covered_units} of "
                   f"{measurement.total_units} substantive units of the derivative publication",
            publication_id=measurement.derivative_publication_id,
            evidence_kind="NORMALIZED_PARAGRAPH_OVERLAP",
            direction=_SIDE_DIRECTION.get(side or "", "UNDIRECTED"))
        all_signals += (synthetic,)
        firm += (synthetic,)

    audit: dict[str, float] = {}
    coverage_evidence: tuple[str, ...] = ()
    if measurement is not None:
        audit = {"overlap_coverage_ratio_uncalibrated": measurement.coverage_ratio,
                 "overlap_full_derivation_threshold": FULL_DERIVATION_COVERAGE}
        coverage_evidence = (
            f"OVERLAP_BASIS:{measurement.basis}",
            f"OVERLAP_UNITS:{measurement.covered_units}/{measurement.total_units}",
            f"OVERLAP_COVERAGE_RATIO:{measurement.coverage_ratio:.4f}",
            f"FULL_DERIVATION_COVERAGE_THRESHOLD:{FULL_DERIVATION_COVERAGE:.2f}")
    if alignment is not None:
        audit["translation_alignment_score_uncalibrated"] = alignment.score

    dependence_active = tuple(item for item in firm if item.kind in _DEPENDENCE_KINDS)
    independence_active = tuple(item for item in firm if item.kind in INDEPENDENCE_EVIDENCE_KINDS)
    derivation_active = tuple(item for item in dependence_active if item.kind in _DERIVATION_KINDS)

    def strong_of(kinds: frozenset[str]) -> tuple[DependenceSignal, ...]:
        return tuple(item for item in derivation_active
                     if item.kind in kinds and item.strength == "STRONG")

    translation = tuple(item for item in derivation_active
                        if item.kind in _TRANSLATION) if basis else ()
    syndication = strong_of(_SYNDICATION)
    explicit = strong_of(_EXPLICIT_DERIVATION)
    fingerprint = strong_of(_CONTENT_FINGERPRINT)
    combined = tuple(item for item in derivation_active
                     if item.strength in {"STRONG", "MODERATE"})
    combination_confirmed = combined if len({item.kind for item in combined}) >= 2 else ()
    licensing = translation or syndication or explicit or fingerprint or combination_confirmed

    if licensing:
        residual_evidence = tuple(_descriptor(item) for item in dependence_active
                                  if item not in licensing)
        original_content = tuple(item for item in independence_active
                                 if item.strength in {"STRONG", "MODERATE"})
        ratio = measurement.coverage_ratio if measurement is not None else None
        partial_by_coverage = ratio is not None and ratio < FULL_DERIVATION_COVERAGE
        if original_content or partial_by_coverage:
            if not original_content:
                # The uncovered remainder is itself affirmative evidence of
                # content the source does not account for; typing it keeps the
                # V5.1 rule that partial dependence never collapses to either
                # pole while letting a measurement, not a hand-fed signal,
                # establish the original pole.
                uncovered = measurement.total_units - measurement.covered_units
                remainder = _synthetic(
                    "DISTINCT_REPORTING_DETAIL",
                    strength="STRONG" if ratio is not None and ratio <= 0.5 else "MODERATE",
                    detail=f"{uncovered} of {measurement.total_units} substantive units of the "
                           "derivative publication are not accounted for by the source "
                           f"({measurement.basis.lower()})",
                    publication_id=measurement.derivative_publication_id,
                    evidence_kind="NORMALIZED_PARAGRAPH_OVERLAP")
                all_signals += (remainder,)
                original_content = (remainder,)
            return _assess(
                left_pub, right_pub, "PARTIAL_DEPENDENCE", all_signals,
                rationale="confirmed derivation accounts for part of the derivative "
                          "publication only; the pair is neither fully derivative nor "
                          "independent",
                licensing=tuple(licensing) + original_content,
                positive=tuple(_descriptor(item) for item in
                               tuple(licensing) + original_content) + coverage_evidence,
                negative=negative, unresolved=residual_evidence, audit_dimensions=audit)
        if translation:
            state, rationale = "TRANSLATION_DERIVATIVE", (
                "translation evidence (" + ", ".join(basis) + ") confirms one publication as "
                "a translation derivative of the other")
        elif syndication:
            state, rationale = "SYNDICATION_DERIVATIVE", (
                "wire or syndication evidence confirms syndicated derivative content")
        else:
            state, rationale = "DERIVATIVE_CONFIRMED", (
                "explicit reuse or content-fingerprint evidence confirms derivation")
        return _assess(
            left_pub, right_pub, state, all_signals, rationale=rationale,
            licensing=tuple(licensing),
            positive=tuple(_descriptor(item) for item in licensing) + coverage_evidence,
            negative=negative, unresolved=residual_evidence, audit_dimensions=audit)

    # --- Section 12.2: a common evidence basis is confirmed by an identified
    # artefact at any strength, by a graded inferential signal, or by two
    # concordant weak kinds.  V5.1 required a graded signal for everything and
    # therefore lost identified shared artefacts into INDEPENDENCE_UNKNOWN.
    basis_active = tuple(item for item in dependence_active if item.kind in _COMMON_BASIS)
    identified = tuple(item for item in basis_active
                       if item.kind in IDENTIFIED_ARTIFACT_BASIS_KINDS)
    graded = tuple(item for item in basis_active if item.strength in {"STRONG", "MODERATE"})
    concordant = basis_active if len({item.kind for item in basis_active}) >= 2 else ()
    common_confirmed = _unique(identified + graded + concordant)
    if common_confirmed:
        residual_evidence = tuple(_descriptor(item) for item in dependence_active
                                  if item not in common_confirmed)
        distinct_analysis = tuple(item for item in independence_active
                                  if item.kind in DISTINCT_ANALYSIS_KINDS
                                  and item.strength != "WEAK")
        if distinct_analysis:
            return _assess(
                left_pub, right_pub, "SHARED_DATA_INDEPENDENT_ANALYSIS", all_signals,
                rationale="publications share an evidence basis but carry affirmative "
                          "distinct-analysis evidence",
                licensing=common_confirmed + distinct_analysis,
                positive=tuple(_descriptor(item)
                               for item in common_confirmed + distinct_analysis),
                negative=negative, unresolved=residual_evidence, affirmative=True,
                audit_dimensions=audit)
        return _assess(
            left_pub, right_pub, "COMMON_EVIDENCE_BASIS_CONFIRMED", all_signals,
            rationale="publications rest on a common evidence basis without "
                      "distinct-analysis evidence",
            licensing=common_confirmed,
            positive=tuple(_descriptor(item) for item in common_confirmed),
            negative=negative, unresolved=residual_evidence,
            alternatives=("UNDISCLOSED_DIRECT_DERIVATION_BETWEEN_THE_PUBLICATIONS",
                          "INDEPENDENT_ANALYSIS_OF_THE_SHARED_BASIS_NOT_YET_EVIDENCED"),
            audit_dimensions=audit)

    if dependence_active:
        kinds = sorted({item.kind for item in dependence_active})
        demonstration = (
            "Dependence between the two publications was searched but remains underdetermined: "
            f"the signals {', '.join(kinds)} are individually insufficient to confirm derivation "
            "and cannot be affirmatively negated from the available public record, because an "
            "undisclosed common upstream source (for example an unpublished briefing or an "
            "embargoed release) would produce the same observable pattern.")
        return _assess(
            left_pub, right_pub, "INDEPENDENCE_UNKNOWN", all_signals,
            rationale="discovered signals neither confirm dependence nor support independence",
            unresolved=tuple(_descriptor(item)
                             for item in dependence_active + independence_active),
            negative=negative,
            alternatives=("UNDISCLOSED_COMMON_UPSTREAM_SOURCE",
                          "DIRECT_DERIVATION_BELOW_DETECTION_THRESHOLD",
                          "GENUINE_INDEPENDENCE"),
            demonstration=demonstration, audit_dimensions=audit)

    # --- Section 12.4: independence stays a positive finding.  The V5.1
    # multi-signal rule is applied unchanged, over caller-supplied evidence
    # only; nothing this module synthesizes can reach it.
    if independence_active:
        if _independence_rule_met(independence_active):
            return _assess(
                left_pub, right_pub, "INDEPENDENCE_SUPPORTED", all_signals,
                rationale="multiple affirmative independence signals with no un-negated "
                          "dependence signal support independence",
                licensing=independence_active,
                positive=tuple(_descriptor(item) for item in independence_active),
                negative=negative,
                alternatives=("DEPENDENCE_OUTSIDE_THE_SEARCHED_PUBLIC_RECORD",),
                affirmative=True)
        return _assess(
            left_pub, right_pub, "NO_DEPENDENCE_FOUND", all_signals,
            rationale="independence evidence is below the multi-signal threshold; only the "
                      "absence of discovered dependence remains",
            unresolved=tuple(_descriptor(item) for item in independence_active),
            negative=negative,
            alternatives=("UNDISCOVERED_DEPENDENCE",
                          "GENUINE_INDEPENDENCE_WITHOUT_SURVIVING_EVIDENCE"),
            only_absence=True)

    return _assess(
        left_pub, right_pub, "NO_DEPENDENCE_FOUND", all_signals,
        rationale="no un-negated dependence signal was discovered; this is not a finding of "
                  "independence",
        negative=negative,
        alternatives=("UNDISCOVERED_DEPENDENCE",
                      "GENUINE_INDEPENDENCE_WITHOUT_SURVIVING_EVIDENCE"),
        only_absence=True)
