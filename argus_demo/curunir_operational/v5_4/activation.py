"""Section 9 — activating the dormant dependence and temporal classifiers.

On V5.3's clean evidence, dependence emitted ``NO_DEPENDENCE_FOUND`` for all 66
units and temporal emitted ``NO_CONFLICT`` for all 70.  Both scored well —
0.9394 and 1.0000 — and neither number meant anything, because a constant
function scores whatever the corpus's majority class is.

Two things were wrong.  The evidence carried almost no explicit relations, and
the classifiers only looked for implicit ones.  This module supplies the missing
half: detect the relations documents state *about themselves* — corrigendum to,
supersedes, translation of, syndicated from — and project one observed relation
consistently into the dependence and temporal vocabularies instead of letting
each module guess separately.

It also supplies the gate that stops the milestone from spending a reviewer
panel on another constant classifier.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from ..v5_1.models import Record, now_utc, stable_id

# ---------------------------------------------------------------------------
# Section 9.2 / 9.3 — the class vocabularies.  None may be narrowed.
# ---------------------------------------------------------------------------

DEPENDENCE_CLASSES: tuple[str, ...] = (
    "DERIVATIVE_CONFIRMED", "TRANSLATION_DERIVATIVE", "SYNDICATION_DERIVATIVE",
    "MIRROR_MANIFESTATION", "COMMON_EVIDENCE_BASIS_CONFIRMED", "PARTIAL_DEPENDENCE",
    "SHARED_DATA_INDEPENDENT_ANALYSIS", "INDEPENDENCE_SUPPORTED",
    "NO_DEPENDENCE_FOUND", "INDEPENDENCE_UNKNOWN", "DEPENDENCE_DISPUTED",
)

TEMPORAL_CLASSES: tuple[str, ...] = (
    "LOGICAL_CONTRADICTION", "TEMPORAL_UPDATE", "SCOPE_DIFFERENCE",
    "DEFINITION_DIFFERENCE", "SOURCE_DISAGREEMENT", "NUMERIC_DISAGREEMENT",
    "IDENTITY_DISAGREEMENT", "POLARITY_CONFLICT", "QUALIFICATION", "CORRECTION",
    "RETRACTION", "SUPERSESSION", "UNRESOLVED", "NO_CONFLICT",
)

# ---------------------------------------------------------------------------
# Section 9.4 — explicit relation evidence, and its single projection.
# ---------------------------------------------------------------------------

#: One observed relation projects simultaneously into source origin, dependence
#: and temporal.  Guessing the same relation independently in three modules is
#: how three vocabularies drift apart.
RELATION_PROJECTION: Mapping[str, dict[str, str]] = {
    "REVISES":          {"origin": "REVISION_OF", "dependence": "DERIVATIVE_CONFIRMED",
                         "temporal": "TEMPORAL_UPDATE"},
    "REPLACES":         {"origin": "REPLACEMENT_OF", "dependence": "DERIVATIVE_CONFIRMED",
                         "temporal": "SUPERSESSION"},
    "SUPERSEDES":       {"origin": "SUPERSESSION_OF", "dependence": "DERIVATIVE_CONFIRMED",
                         "temporal": "SUPERSESSION"},
    "UPDATES":          {"origin": "UPDATE_OF", "dependence": "DERIVATIVE_CONFIRMED",
                         "temporal": "TEMPORAL_UPDATE"},
    "AMENDS":           {"origin": "AMENDMENT_OF", "dependence": "PARTIAL_DEPENDENCE",
                         "temporal": "TEMPORAL_UPDATE"},
    "CORRECTS":         {"origin": "CORRECTION_OF", "dependence": "DERIVATIVE_CONFIRMED",
                         "temporal": "CORRECTION"},
    "CORRIGENDUM_TO":   {"origin": "CORRECTION_OF", "dependence": "DERIVATIVE_CONFIRMED",
                         "temporal": "CORRECTION"},
    "WITHDRAWS":        {"origin": "WITHDRAWAL_OF", "dependence": "DERIVATIVE_CONFIRMED",
                         "temporal": "RETRACTION"},
    "RETRACTS":         {"origin": "RETRACTION_OF", "dependence": "DERIVATIVE_CONFIRMED",
                         "temporal": "RETRACTION"},
    "CONSOLIDATES":     {"origin": "CONSOLIDATION_OF", "dependence": "DERIVATIVE_CONFIRMED",
                         "temporal": "SUPERSESSION"},
    "NEW_EDITION_OF":   {"origin": "EDITION_OF", "dependence": "DERIVATIVE_CONFIRMED",
                         "temporal": "TEMPORAL_UPDATE"},
    "TRANSLATION_OF":   {"origin": "TRANSLATION_OF", "dependence": "TRANSLATION_DERIVATIVE",
                         "temporal": "NO_CONFLICT"},
    "REPUBLISHED_FROM": {"origin": "REPUBLICATION_OF", "dependence": "MIRROR_MANIFESTATION",
                         "temporal": "NO_CONFLICT"},
    "SYNDICATED_FROM":  {"origin": "SYNDICATION_OF", "dependence": "SYNDICATION_DERIVATIVE",
                         "temporal": "NO_CONFLICT"},
    "ADAPTED_FROM":     {"origin": "ADAPTATION_OF", "dependence": "PARTIAL_DEPENDENCE",
                         "temporal": "NO_CONFLICT"},
    "BASED_ON":         {"origin": "BASED_ON", "dependence": "COMMON_EVIDENCE_BASIS_CONFIRMED",
                         "temporal": "NO_CONFLICT"},
}

#: An instrument identifier appearing right after a relation phrase names the
#: relation's *target*.  "amending Regulation (EU) 2016/679" is a relation to
#: 2016/679 and to nothing else.
#: Targets are named in the vocabulary of their own domain.  An EU instrument is
#: named by number; a retracted article is named by DOI.  An extractor that knows
#: only one of these cannot see the other's relation at all — which is why the
#: retraction notices in the corpus stated RETRACTS and governed nothing.
_RELATION_TARGET = re.compile(
    r"\((?:EU|EC|EEC|UE)\)\s*(?:No\s*|Nr\.?\s*|n[°o]\s*)?(\d{1,4}/\d{2,4})"
    r"|(?:doi:|https?://(?:dx\.)?doi\.org/)\s*(10\.\d{4,9}/[-._;()/:a-z0-9]+)"
    r"|\b(S\d{4}-\d{4}\(\d{2}\)\d{4,5}-\d)\b",
    re.IGNORECASE)


def _first_group(match: "re.Match[str]") -> str | None:
    for value in match.groups():
        if value:
            return value.rstrip(".,;)")
    return None


#: The other way a document names what it supersedes: a designator stated right
#: after the relation phrase, usually behind a colon — "Superseded-By: 649",
#: "Replaces: RFC 2119", "supersedes version 15.0.0".  _RELATION_TARGET knows
#: EU serials, DOIs and article ids and nothing else, so on every corpus outside
#: EU law the relation was detected with no target, governs_pair refused it for
#: having named nobody, and no explicit relation ever governed a pair.  The
#: designator must sit within a few tokens of the phrase; a number further away
#: is prose, not a target.
#: A designator is an uppercase series mark followed by a number, or a dotted
#: version.  A bare four-digit number in the 19xx/20xx range is a date — "last
#: updated 2026" is not a statement about another document — and a lowercase or
#: mixed-case prefix is a word, so "Apr 02" is not a target either.  Reading
#: those as targets would let governs_pair project a relation onto a pair the
#: document never named, which is the defect governs_pair exists to prevent.
_RELATION_DESIGNATOR = re.compile(
    # The relation word itself, then the label's "-By", then a colon or dash,
    # then the designator.  Matched against whitespace-collapsed text, because a
    # metadata table renders as "Superseded-By : \n\n 649" and any pattern
    # counting literal separator characters loses the target to the newlines.
    r"^[^\W\d_]+(?:[- ]?by)?\s*[:\-–—]?\s*(?:the\s+)?"
    r"(?:(?:version|edition|revision|release|no\.?|nr\.?|n[°o])\s*)?"
    r"(?!(?:19|20)\d{2}\b)"
    # A bare integer must be at least three digits.  One- and two-digit numbers
    # next to a relation phrase are overwhelmingly dates — "Last updated: Apr
    # 02" — and a date read as a target lets a relation govern a pair that was
    # never named.  Series marks and dotted versions carry their own evidence.
    # The series mark stays case-sensitive inside an otherwise case-insensitive
    # pattern: "PEP 262" is a designator, "Apr 02" is a date.
    r"((?-i:[A-Z]{2,6})[ -]?\d[\w.]*|\d+(?:\.\d+)+|\d{3,5})\b",
    re.IGNORECASE)


def _target_after(segment: str, start: int, window: int = 220) -> str | None:
    tail = segment[start:start + window]
    match = _RELATION_TARGET.search(tail)
    if match:
        return _first_group(match)
    designator = _RELATION_DESIGNATOR.match(" ".join(tail[:96].split()))
    return designator.group(1).rstrip(".,;)") if designator else None


#: Surface patterns, multilingual, that state a relation explicitly.
_RELATION_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("CORRIGENDUM_TO", re.compile(
        r"\b(corrigend\w+\s+(?:to|zu|au)|berichtigung\s+(?:zu|der|des)|rectificatif\s+au"
        r"|rettifica\s+del)\b", re.IGNORECASE)),
    ("RETRACTS", re.compile(
        r"\b(retract\w+|this\s+article\s+has\s+been\s+withdrawn|zurückgezogen"
        r"|zurueckgezogen|rétract\w+)\b", re.IGNORECASE)),
    ("WITHDRAWS", re.compile(
        r"\b(withdraw(?:s|n|al)\s+(?:of|the)|zurückgenommen|zurueckgenommen"
        r"|retrait\s+de)\b", re.IGNORECASE)),
    ("SUPERSEDES", re.compile(
        r"\b(supersed\w+|ersetzt\s+(?:die|den|das)|remplace\s+le|abrogat\w+)\b",
        re.IGNORECASE)),
    ("REPLACES", re.compile(
        # A metadata header states the relation as a label — "Replaces: 3107",
        # "Replaced-By: 649" — with no noun phrase after the verb.  Requiring
        # one of four nouns matched EU legislative prose and nothing else.
        r"\b(replaces?\s+(?:regulation|directive|decision|the)|ersetzt\s+durch"
        r"|replace[sd][- ]?by\s*:|replaces\s*:)",
        re.IGNORECASE)),
    ("CONSOLIDATES", re.compile(
        r"\b(consolidated\s+(?:text|version)|konsolidierte\s+fassung"
        r"|version\s+consolidée)\b", re.IGNORECASE)),
    ("AMENDS", re.compile(
        r"\b(amend(?:s|ing|ment\s+to)|zur\s+änderung|zur\s+aenderung"
        r"|modifiant|amending\s+regulation)\b", re.IGNORECASE)),
    ("CORRECTS", re.compile(
        r"\b(corrects?\s+(?:the|regulation)|correction\s+to|berichtigt)\b",
        re.IGNORECASE)),
    ("NEW_EDITION_OF", re.compile(
        r"\b((?:second|third|fourth|revised|new)\s+edition|neuauflage"
        r"|nouvelle\s+édition)\b", re.IGNORECASE)),
    ("TRANSLATION_OF", re.compile(
        r"\b(translation\s+of|übersetzung\s+(?:von|des|der)|uebersetzung"
        r"|traduction\s+de|unofficial\s+translation)\b", re.IGNORECASE)),
    ("SYNDICATED_FROM", re.compile(
        r"\b((?:reuters|associated\s+press|\bap\b|afp|dpa|pa\s+media)\s*[-–—|]"
        r"|reporting\s+by\s+\w+\s+for|first\s+published\s+by"
        r"|mit\s+material\s+(?:von|der))\b", re.IGNORECASE)),
    ("REPUBLISHED_FROM", re.compile(
        r"\b(republished\s+(?:from|with\s+permission)|originally\s+(?:published|appeared)"
        r"|erstveröffentlich\w*|zuerst\s+erschienen)\b", re.IGNORECASE)),
    ("UPDATES", re.compile(
        r"\b(updated?\s+(?:version|on|to\s+reflect)|aktualisierte\s+fassung"
        r"|mise\s+à\s+jour)\b", re.IGNORECASE)),
    ("REVISES", re.compile(
        r"\b(revis(?:es|ed|ion\s+of)|überarbeitete|ueberarbeitete|révis\w+)\b",
        re.IGNORECASE)),
    ("ADAPTED_FROM", re.compile(
        r"\b(adapted\s+from|nach\s+einer\s+vorlage|adapté\s+de)\b", re.IGNORECASE)),
    ("BASED_ON", re.compile(
        r"\b(based\s+on\s+(?:data|figures|the\s+report)|auf\s+(?:der\s+)?grundlage"
        r"|sur\s+la\s+base\s+de|according\s+to\s+(?:data|figures)\s+from)\b",
        re.IGNORECASE)),
)


@dataclass(frozen=True)
class RelationObservation(Record):
    """One explicitly stated relation, with the span that states it."""

    observation_id: str
    relation: str
    matched_text: str
    offset: int
    source_object_id: str
    recorded_time: str
    target_identifier: str | None = None

    def __post_init__(self) -> None:
        if self.relation not in RELATION_PROJECTION:
            raise ValueError(f"unknown relation: {self.relation}")

    @property
    def projection(self) -> dict[str, str]:
        return dict(RELATION_PROJECTION[self.relation])


def detect_relations(text: str, *, source_object_id: str = "",
                     window: int = 4000) -> tuple[RelationObservation, ...]:
    """Find explicitly stated relations in a document's head and tail.

    Self-describing relations live in title blocks, mastheads and footers, so
    the whole body is not searched: a mention of "corrigendum" in paragraph 40
    is a topic, not a claim about this document.
    """
    if not text:
        return ()
    head, tail = text[:window], text[-window:] if len(text) > window else ""
    tail_offset = max(0, len(text) - window)
    found: list[RelationObservation] = []
    seen: set[str] = set()
    for segment, base in ((head, 0), (tail, tail_offset)):
        if not segment:
            continue
        for relation, pattern in _RELATION_PATTERNS:
            match = pattern.search(segment)
            if match and relation not in seen:
                seen.add(relation)
                found.append(RelationObservation(
                    stable_id("v5-4-relation", source_object_id, relation,
                              str(base + match.start())),
                    relation, match.group(0)[:120], base + match.start(),
                    source_object_id, now_utc(),
                    _target_after(segment, match.start())))
    return tuple(found)


def project(relation: str) -> dict[str, str]:
    """The single consistent projection of one relation into three vocabularies."""
    if relation not in RELATION_PROJECTION:
        raise ValueError(f"unknown relation: {relation}")
    return dict(RELATION_PROJECTION[relation])


# ---------------------------------------------------------------------------
# Numeric and polarity disagreement — the temporal classes explicit relations
# do not reach.
# ---------------------------------------------------------------------------

_NUMBER = re.compile(r"(?<![\w.])(\d{1,3}(?:[ ,.]\d{3})*(?:[.,]\d+)?)\s*"
                     r"(%|percent|prozent|million|billion|milliarden|mrd|mio|bn|m\b|€|eur)?",
                     re.IGNORECASE)
_PRELIMINARY = re.compile(
    r"\b(preliminary|provisional|initial|first\s+estimate|vorläufig|vorlaeufig|"
    r"erste\s+schätzung|provisoire)\b", re.IGNORECASE)
_FINAL = re.compile(r"\b(final|definitive|confirmed|endgültig|endgueltig|définitif)\b",
                    re.IGNORECASE)
_NEGATION = re.compile(
    r"\b(not|no longer|never|denies|denied|rejects?|rejected|nicht|kein|keine|"
    r"bestreitet|dementiert)\b", re.IGNORECASE)
_SCOPE_WORDS = re.compile(
    r"\b(eu-wide|union-wide|nationwide|member states?|europe|worldwide|global|"
    r"region(?:al)?|single site|pilot|bundesweit|europaweit|weltweit)\b", re.IGNORECASE)
_DEFINITION = re.compile(
    r"\b(defined as|definition of|for the purposes of this|means, in relation to|"
    r"im sinne dieser|definiert als|au sens du présent)\b", re.IGNORECASE)
_ATTRIBUTION = re.compile(
    r"\b(according to|said|stated|reported by|laut|nach angaben von|selon)\b",
    re.IGNORECASE)

#: A named institutional or corporate actor.  Used to tell IDENTITY_DISAGREEMENT
#: (two sources naming different actors for one act) from SOURCE_DISAGREEMENT
#: (two attributed accounts that differ in content).
_NAMED_ACTOR = re.compile(
    r"\b((?:[A-ZÄÖÜ][\w-]*\s+(?:(?:of|for|and|de|der|des|von)\s+)?){1,4}"
    r"(?:Commission|Council|Parliament|Agency|Authority|Ministry|Board|Office|"
    r"Bank|Court|Committee|Centre|Center|Institute|Group|Company|Corporation|"
    r"Kommission|Beh\u00f6rde|Ministerium|Amt|Gericht))\b")

#: Mutually exclusive status assertions.  Two statements about one subject that
#: land in different buckets here cannot both hold.
_EXCLUSIVE_STATUS: Mapping[str, tuple[str, ...]] = {
    "OPERATING": ("operational", "in service", "in operation", "fully deployed",
                  "in betrieb", "im einsatz"),
    "NOT_OPERATING": ("never deployed", "not operational", "out of service",
                      "decommissioned", "abandoned", "cancelled", "suspended",
                      "nicht in betrieb", "eingestellt", "stillgelegt"),
    "COMPLETE": ("completed", "finalised", "finalized", "concluded", "delivered",
                 "abgeschlossen", "fertiggestellt"),
    "INCOMPLETE": ("ongoing", "under way", "underway", "not yet complete",
                   "pending", "delayed", "laufend", "verzögert", "verzoegert"),
}

#: An explicit statement that provenance is contested.
_DISPUTED_PROVENANCE = re.compile(
    r"\b(disputed?\s+(?:provenance|attribution|authorship|origin)|contested\s+"
    r"(?:attribution|authorship|provenance)|den(?:y|ies|ied)\s+(?:copying|"
    r"plagiaris\w+|reproducing|reusing)|alleged\s+(?:plagiaris\w+|copying)|"
    r"plagiaris\w+\s+"
    r"(?:allegation|claim)|without\s+(?:permission|attribution)|"
    r"bestreitet\s+die\s+urheberschaft|umstrittene\s+herkunft)\b", re.IGNORECASE)

#: A statement describing its own sourcing as independent and self-obtained.
_OWN_SOURCING = re.compile(
    r"\b(our own (?:analysis|investigation|reporting|measurements?|data)|"
    r"independently (?:obtained|verified|collected|gathered|measured)|"
    r"we (?:obtained|collected|measured|surveyed|interviewed)|"
    r"based on (?:our|its) own|eigene (?:erhebung|analyse|messung)|"
    r"selbst erhoben)\b", re.IGNORECASE)

#: A statement that qualifies rather than contradicts.
_QUALIFYING = re.compile(
    r"\b(to clarify|clarif\w+ that|it should be noted that|subject to|"
    r"provided that|only (?:where|if|in cases)|with the exception of|"
    r"does not (?:apply|extend) to|this does not mean|"
    r"zur klarstellung|vorbehaltlich|sofern nicht)\b", re.IGNORECASE)

#: A conflict that both sides leave open.
_UNRESOLVED_MARKER = re.compile(
    r"\b(remains? (?:unclear|under investigation|disputed|unresolved)|"
    r"investigation is (?:ongoing|continuing)|no (?:final|definitive) "
    r"(?:conclusion|determination)|yet to be (?:determined|established)|"
    r"pending (?:the )?(?:final|outcome)|noch (?:offen|ungeklärt)|"
    r"untersuchung (?:dauert an|läuft))\b", re.IGNORECASE)


def _status_bucket(text: str) -> str | None:
    folded = (text or "").casefold()
    for bucket, markers in _EXCLUSIVE_STATUS.items():
        if any(marker in folded for marker in markers):
            return bucket
    return None


_EXCLUSIVE_PAIRS = (("OPERATING", "NOT_OPERATING"), ("COMPLETE", "INCOMPLETE"))


def _mutually_exclusive(left: str | None, right: str | None) -> bool:
    if left is None or right is None or left == right:
        return False
    return any({left, right} == set(pair) for pair in _EXCLUSIVE_PAIRS)


def _actors(text: str) -> set[str]:
    return {m.group(1).strip().casefold() for m in _NAMED_ACTOR.finditer(text or "")}


def _numbers(text: str) -> set[str]:
    out = set()
    for match in _NUMBER.finditer(text or ""):
        value = match.group(1).replace(" ", "").replace(",", ".")
        out.add(value.rstrip("."))
    return out


def governs_pair(observation: "RelationObservation",
                 other_identifier: str | None) -> bool:
    """Does this relation actually hold between *this pair*?

    A relation is a statement about a specific target.  The GDPR consolidates its
    own earlier version; it says nothing about the Digital Services Act.  Reading
    "consolidated version" off document A and projecting it onto every pair
    containing A attributes a relation to documents that were never named, and it
    masks the content-level comparison the pair actually supports.

    When the relation names a target, it governs only pairs containing that
    target.  When it names none, it governs nothing and the pair falls through
    to the content branches rather than inheriting an unearned relation.
    """
    if observation.target_identifier is None:
        return False
    if other_identifier is None:
        return False
    left = observation.target_identifier.casefold().rstrip(".,;)")
    right = other_identifier.casefold().rstrip(".,;)")
    if left == right:
        return True
    # EU instruments are compared on their serial part so that "(EU) 2016/679"
    # matches an identifier recorded as "2016/679"; DOIs and article ids are
    # compared whole, because their tails are not serials.
    if "/" in left and "/" in right and not left.startswith("10."):
        return left.split("/")[-1] == right.split("/")[-1]
    return False


def definition_clauses(text: str, *, limit: int = 12) -> tuple[str, ...]:
    """The sentences in which a document defines terms for its own instrument.

    In an EU instrument the definitions article sits *after* the recitals, often
    tens of thousands of characters in.  Any comparison that reads a fixed head
    window will never see it, and will conclude the document defines nothing.
    Where the definitions sit is a layout fact, not a semantic one, so they are
    extracted by marker over the whole text instead.
    """
    out: list[str] = []
    for match in _DEFINITION.finditer(text or ""):
        start = max(0, match.start() - 120)
        out.append(" ".join((text[start:match.end() + 240]).split()))
        if len(out) >= limit:
            break
    return tuple(out)


def classify_temporal(left_text: str, right_text: str, *,
                      left_relations: Sequence[RelationObservation] = (),
                      right_relations: Sequence[RelationObservation] = (),
                      left_identifier: str | None = None,
                      right_identifier: str | None = None,
                      left_definitions: Sequence[str] = (),
                      right_definitions: Sequence[str] = (),
                      ) -> tuple[str, str]:
    """Classify a pair, governing relations first, then stated disagreements."""
    for observation, other in ((o, left_identifier) for o in right_relations):
        if not governs_pair(observation, other):
            continue
        projected = observation.projection["temporal"]
        if projected != "NO_CONFLICT":
            return projected, (f"explicit relation {observation.relation} targeting "
                               f"{observation.target_identifier}: "
                               f"{observation.matched_text!r}")
    for observation, other in ((o, right_identifier) for o in left_relations):
        if not governs_pair(observation, other):
            continue
        projected = observation.projection["temporal"]
        if projected != "NO_CONFLICT":
            return projected, (f"explicit relation {observation.relation} targeting "
                               f"{observation.target_identifier}: "
                               f"{observation.matched_text!r}")

    left_content, right_content = _content(left_text), _content(right_text)
    overlap = left_content & right_content
    union = left_content | right_content
    similarity = len(overlap) / len(union) if union else 0.0
    shared_topic = len(overlap) >= 3

    # Polarity conflict requires the negation to attach to *the same* assertion.
    # Testing whether the word "not" appears anywhere in two multi-page documents
    # is not a polarity test — it fires on almost every pair and manufactures
    # class diversity that is not there.
    left_neg = bool(_NEGATION.search(left_text))
    right_neg = bool(_NEGATION.search(right_text))
    if left_neg != right_neg and similarity >= 0.60:
        return "POLARITY_CONFLICT", (
            f"near-identical statements ({similarity:.0%} shared vocabulary) differing "
            "in negation")

    left_defs = tuple(left_definitions) or definition_clauses(left_text)
    right_defs = tuple(right_definitions) or definition_clauses(right_text)
    if shared_topic and left_defs and right_defs:
        left_terms = _content(" ".join(left_defs))
        right_terms = _content(" ".join(right_defs))
        shared_defined = left_terms & right_terms
        if shared_defined and " ".join(sorted(left_defs)) != " ".join(sorted(right_defs)):
            return "DEFINITION_DIFFERENCE", (
                f"both instruments define terms for their own purposes over shared "
                f"vocabulary {sorted(shared_defined)[:4]}, with different definition "
                "text")

    # LOGICAL_CONTRADICTION — two statements asserting states that cannot both
    # hold.  Distinct from POLARITY_CONFLICT, which is the explicit negation of
    # one assertion; here neither statement negates, they simply exclude.
    if shared_topic:
        left_status, right_status = _status_bucket(left_text), _status_bucket(right_text)
        if _mutually_exclusive(left_status, right_status):
            return "LOGICAL_CONTRADICTION", (
                f"one statement asserts {left_status} and the other "
                f"{right_status} for the same subject; these cannot both hold")

    # IDENTITY_DISAGREEMENT — the same act attributed to different actors.
    if shared_topic:
        left_actors, right_actors = _actors(left_text), _actors(right_text)
        if left_actors and right_actors and not (left_actors & right_actors):
            return "IDENTITY_DISAGREEMENT", (
                f"the same subject is attributed to {sorted(left_actors)[:2]} in one "
                f"account and {sorted(right_actors)[:2]} in the other")

    # QUALIFICATION — one statement narrows the other rather than contradicting
    # it.  High similarity plus a qualifying clause on exactly one side.
    if similarity >= 0.45:
        left_q = bool(_QUALIFYING.search(left_text))
        right_q = bool(_QUALIFYING.search(right_text))
        if left_q != right_q:
            return "QUALIFICATION", (
                f"near-identical statements ({similarity:.0%} shared vocabulary) of "
                "which one carries an explicit qualifying clause")

    left_numbers, right_numbers = _numbers(left_text), _numbers(right_text)
    if shared_topic and left_numbers and right_numbers and not (left_numbers & right_numbers):
        preliminary = _PRELIMINARY.search(left_text) or _PRELIMINARY.search(right_text)
        final = _FINAL.search(left_text) or _FINAL.search(right_text)
        if preliminary and final:
            return "TEMPORAL_UPDATE", ("a preliminary figure and a final figure for the "
                                       "same quantity")
        return "NUMERIC_DISAGREEMENT", (
            f"different figures for a shared subject: {sorted(left_numbers)[:3]} vs "
            f"{sorted(right_numbers)[:3]}")

    if shared_topic:
        left_scope = {m.group(0).casefold() for m in _SCOPE_WORDS.finditer(left_text)}
        right_scope = {m.group(0).casefold() for m in _SCOPE_WORDS.finditer(right_text)}
        if left_scope and right_scope and not (left_scope & right_scope):
            return "SCOPE_DIFFERENCE", (
                f"different scopes asserted: {sorted(left_scope)[:2]} vs "
                f"{sorted(right_scope)[:2]}")
        if _ATTRIBUTION.search(left_text) and _ATTRIBUTION.search(right_text):
            # UNRESOLVED outranks SOURCE_DISAGREEMENT when the sources say
            # themselves that the matter is still open: reporting it as a
            # settled disagreement would overstate what either side claims.
            if _UNRESOLVED_MARKER.search(left_text) or _UNRESOLVED_MARKER.search(right_text):
                return "UNRESOLVED", (
                    "two attributed accounts differ and at least one states the "
                    "matter is still open; neither has been withdrawn")
            return "SOURCE_DISAGREEMENT", (
                "two attributed accounts of the same subject")
        if _UNRESOLVED_MARKER.search(left_text) and _UNRESOLVED_MARKER.search(right_text):
            return "UNRESOLVED", (
                "both statements describe the matter as still under investigation")
    return "NO_CONFLICT", "no stated relation and no detected disagreement"


#: Observation types from ``v5_3/observations.py`` that already establish a
#: derivation relation.  Consuming them here is Section 9.4's rule applied
#: honestly: the observation layer detects the notice once, and dependence reads
#: that detection rather than running a second, narrower regex of its own.
_OBSERVED_RELATION: Mapping[str, str] = {
    "SYNDICATION_NOTICE": "SYNDICATION_DERIVATIVE",
    "TRANSLATION_NOTICE": "TRANSLATION_DERIVATIVE",
    "MIRROR_NOTICE": "MIRROR_MANIFESTATION",
    "REVISION_STATEMENT": "DERIVATIVE_CONFIRMED",
}


def classify_dependence(left_text: str, right_text: str, *,
                        left_relations: Sequence[RelationObservation] = (),
                        right_relations: Sequence[RelationObservation] = (),
                        shared_evidence_ids: Sequence[str] = (),
                        left_language: str | None = None,
                        right_language: str | None = None,
                        left_identifier: str | None = None,
                        right_identifier: str | None = None,
                        left_observations: Sequence[str] = (),
                        right_observations: Sequence[str] = (),
                        left_cited: Sequence[str] = (),
                        right_cited: Sequence[str] = (),
                        ) -> tuple[str, str]:
    """Classify dependence, explicit relations first.

    Official multilingual publication is the common translation case and it
    almost never says "translation of": the German and French versions of a
    regulation simply carry the same instrument number in another language.
    Requiring the phrase is why TRANSLATION_DERIVATIVE never fired.
    """
    if (left_language and right_language and left_language != right_language
            and left_identifier and right_identifier
            and left_identifier == right_identifier):
        return "TRANSLATION_DERIVATIVE", (
            f"same instrument {left_identifier} published in {left_language} and "
            f"{right_language}")

    for observation_type in tuple(right_observations) + tuple(left_observations):
        projected = _OBSERVED_RELATION.get(observation_type)
        if projected:
            return projected, (f"the observation layer captured a "
                               f"{observation_type} on this pair")

    for observation, other in tuple((o, left_identifier) for o in right_relations) + \
            tuple((o, right_identifier) for o in left_relations):
        if not governs_pair(observation, other):
            continue
        return observation.projection["dependence"], (
            f"explicit relation {observation.relation} targeting "
            f"{observation.target_identifier}: {observation.matched_text!r}")

    # Two documents citing the same primary instrument share an evidence basis
    # even when neither derives from the other.  This is the distinction between
    # COMMON_EVIDENCE_BASIS_CONFIRMED and DERIVATIVE_CONFIRMED, and it needs the
    # cited identifiers, not the body text.
    # DEPENDENCE_DISPUTED — provenance is explicitly contested by a party.
    if _DISPUTED_PROVENANCE.search(left_text) or _DISPUTED_PROVENANCE.search(right_text):
        return "DEPENDENCE_DISPUTED", (
            "the relationship between these documents is explicitly contested in "
            "the text; production may not resolve a dispute the sources are having")

    shared_citations = set(left_cited) & set(right_cited)
    overlap_terms = _content(left_text) & _content(right_text)
    left_terms = _content(left_text)
    ratio_now = len(overlap_terms) / len(left_terms) if left_terms else 0.0

    if shared_citations:
        # SHARED_DATA_INDEPENDENT_ANALYSIS vs COMMON_EVIDENCE_BASIS_CONFIRMED.
        # Both cite one primary source.  The distinction is whether the analysis
        # around it is the same work or two separate ones: low textual overlap
        # over a shared dataset is two teams analysing one release, which is a
        # materially different corroboration claim from two documents that
        # simply cite the same act.
        # The discriminator is semantic, not a text-overlap threshold:
        # "independent analysis" means BOTH sides analysed independently.  One
        # side describing its own work over a source the other merely cites is
        # a common evidence basis, not two independent analyses.
        if _OWN_SOURCING.search(left_text) and _OWN_SOURCING.search(right_text):
            return "SHARED_DATA_INDEPENDENT_ANALYSIS", (
                f"both cite {sorted(shared_citations)[:2]} and both describe their "
                f"own analysis of it; shared analytical vocabulary {ratio_now:.0%}")
        return "COMMON_EVIDENCE_BASIS_CONFIRMED", (
            f"both documents cite {sorted(shared_citations)[:3]} without deriving "
            "from one another")

    # INDEPENDENCE_SUPPORTED — positively established, not merely absent.  Both
    # documents describe their own sourcing, they cite nothing in common, and
    # they share little text.  "No dependence found" is the absence of evidence;
    # this is evidence of independence, and the two must not be conflated.
    if (_OWN_SOURCING.search(left_text) and _OWN_SOURCING.search(right_text)
            and not shared_citations and ratio_now <= 0.25):
        return "INDEPENDENCE_SUPPORTED", (
            "both documents describe their own independently obtained sourcing, "
            f"cite nothing in common, and share {ratio_now:.0%} of their vocabulary")
    if shared_evidence_ids:
        return "COMMON_EVIDENCE_BASIS_CONFIRMED", (
            f"{len(shared_evidence_ids)} shared evidence record(s)")
    overlap = _content(left_text) & _content(right_text)
    left_terms = _content(left_text)
    ratio = len(overlap) / len(left_terms) if left_terms else 0.0
    if ratio >= 0.85:
        return "MIRROR_MANIFESTATION", f"near-identical text ({ratio:.0%} shared terms)"
    if ratio >= 0.55:
        return "PARTIAL_DEPENDENCE", f"substantial shared text ({ratio:.0%})"
    if ratio <= 0.10:
        return "NO_DEPENDENCE_FOUND", f"little shared content ({ratio:.0%})"
    return "INDEPENDENCE_UNKNOWN", (
        f"shared content {ratio:.0%} is neither derivative nor clearly independent")


_STOP = frozenset("""a an the of to in on for by with at from and or as is are was were be
der die das den dem des ein eine und oder als ist sind war waren für von mit auf bei aus zu
""".split())


def _content(text: str) -> set[str]:
    return {w for w in re.findall(r"[\wÀ-ɏ'-]+", (text or "").casefold())
            if w not in _STOP and len(w) > 3}


# ---------------------------------------------------------------------------
# Statement-level pairing.
#
# CORRECTION, RETRACTION and SUPERSESSION are relations between *documents*: one
# instrument corrects another.  LOGICAL_CONTRADICTION, POLARITY_CONFLICT,
# IDENTITY_DISAGREEMENT, QUALIFICATION and NUMERIC_DISAGREEMENT are not — they
# are relations between two *statements*, which may sit anywhere inside two large
# documents.  Comparing document heads can never surface them: two 400 kB
# instruments share a head full of recitals and nothing else.
#
# This is the same category error as reading definitions from a head window, and
# it is why five temporal classes stayed dormant through two milestones of
# acquisition.  Acquiring more documents cannot fix a comparison performed at the
# wrong granularity.
# ---------------------------------------------------------------------------

_SENTENCE = re.compile(r"(?<=[.!?])\s+(?=[A-ZÄÖÜ])")

#: A statement is worth comparing when it carries something comparable: a
#: figure, a negation, an attribution, a qualification or a definition.
_COMPARABLE = (
    _NUMBER, _NEGATION, _ATTRIBUTION, _DEFINITION,
    re.compile(r"\b(may|might|could|subject to|provided that|unless|only if|"
               r"however|although|nevertheless|clarif\w+|correct\w+)\b", re.IGNORECASE),
)


@dataclass(frozen=True)
class Statement(Record):
    """One comparable assertion, with the document it came from."""

    statement_id: str
    source_object_id: str
    text: str
    offset: int
    subject_terms: tuple[str, ...]


def extract_statements(text: str, *, source_object_id: str = "",
                       limit: int = 400, min_chars: int = 40,
                       max_chars: int = 400) -> tuple[Statement, ...]:
    """Comparable statements from the whole document, not a head window."""
    out: list[Statement] = []
    offset = 0
    for sentence in _SENTENCE.split(text or ""):
        start, offset = offset, offset + len(sentence) + 1
        stripped = " ".join(sentence.split())
        if not (min_chars <= len(stripped) <= max_chars):
            continue
        if not any(pattern.search(stripped) for pattern in _COMPARABLE):
            continue
        terms = tuple(sorted(_content(stripped))[:12])
        if len(terms) < 3:
            continue
        out.append(Statement(
            stable_id("v5-4-statement", source_object_id, str(start)),
            source_object_id, stripped, start, terms))
        if len(out) >= limit:
            break
    return tuple(out)


def near_duplicate_statements(left: Sequence[Statement], right: Sequence[Statement],
                              *, min_similarity: float = 0.45, limit: int = 400
                              ) -> tuple[tuple[Statement, Statement], ...]:
    """Statement pairs that are near-restatements of one another.

    POLARITY_CONFLICT and QUALIFICATION are relations between two versions of
    *the same* assertion — one negated, or one qualified.  Pairing on a handful
    of shared terms almost never produces such a pair, so those two classes
    stayed dormant however much evidence was acquired.  They need a pairing key
    of their own: high vocabulary overlap.
    """
    pairs: list[tuple[Statement, Statement]] = []
    for statement in left:
        a = set(statement.subject_terms)
        if not a:
            continue
        for other in right:
            if other.statement_id == statement.statement_id:
                continue
            b = set(other.subject_terms)
            if not b:
                continue
            union = a | b
            if len(a & b) / len(union) < min_similarity:
                continue
            pairs.append((statement, other))
            if len(pairs) >= limit:
                return tuple(pairs)
    return tuple(pairs)


def pair_statements(left: Sequence[Statement], right: Sequence[Statement], *,
                    min_shared_terms: int = 4, limit: int = 4000
                    ) -> tuple[tuple[Statement, Statement], ...]:
    """Statements from two documents that are plausibly about the same thing.

    Subject overlap is the pairing key.  Two statements that share no vocabulary
    are not in a temporal relation; they are simply unrelated, and pairing them
    would manufacture disagreements out of noise.
    """
    index: dict[str, list[Statement]] = {}
    for statement in right:
        for term in statement.subject_terms:
            index.setdefault(term, []).append(statement)
    seen: set[tuple[str, str]] = set()
    pairs: list[tuple[Statement, Statement]] = []
    for statement in left:
        candidates: dict[str, int] = {}
        for term in statement.subject_terms:
            for other in index.get(term, ()):
                candidates[other.statement_id] = candidates.get(other.statement_id, 0) + 1
        for other in right:
            if candidates.get(other.statement_id, 0) < min_shared_terms:
                continue
            key = (statement.statement_id, other.statement_id)
            if key in seen:
                continue
            seen.add(key)
            pairs.append((statement, other))
            if len(pairs) >= limit:
                return tuple(pairs)
    return tuple(pairs)


# ---------------------------------------------------------------------------
# Section 9.1 / 15 — the production-output diversity gate.
# ---------------------------------------------------------------------------

#: Minimum distinct classes production must emit before a panel may launch.
#: These are non-degeneracy floors, not closure.
DIVERSITY_MINIMA: Mapping[str, tuple[int, int]] = {
    "dependence": (8, 11),
    "temporal": (10, 14),
    "claim_support": (9, 12),
    "source_roles": (10, 13),
    "report_dispositions": (4, 5),
}


def diversity_gate(counts_by_stream: Mapping[str, Mapping[str, int]]) -> dict[str, Any]:
    """Section 9.1 — refuse to spend a reviewer panel on a constant classifier.

    This inspects frozen production predictions only.  It never reads a reviewer
    label, because none exist when it runs: that is the whole point.  A stream
    that fails here has either an acquisition failure or a dormant classifier,
    and running three seats over it would buy another 1.0000 that means nothing.
    """
    streams: dict[str, Any] = {}
    for stream, (minimum, total) in DIVERSITY_MINIMA.items():
        counts = {k: v for k, v in (counts_by_stream.get(stream) or {}).items() if v}
        distinct = len(counts)
        emitted = sum(counts.values())
        largest = max(counts.values()) / emitted if emitted else 0.0
        streams[stream] = {
            "distinct_predicted_classes": distinct,
            "required_minimum": minimum,
            "classes_total": total,
            "predictions": emitted,
            "largest_class_share": round(largest, 4),
            "constant_output": distinct <= 1,
            "passes": distinct >= minimum,
        }
    failed = [name for name, row in streams.items() if not row["passes"]]
    constant = [name for name, row in streams.items() if row["constant_output"]]
    return {
        "recorded_time": now_utc(),
        "streams": streams,
        "failed_streams": failed,
        "constant_output_streams": constant,
        "panel_may_launch": not failed,
        "verdict": "PASS" if not failed else "PARTIAL",
        "rule": ("a reviewer panel is launched only when frozen production output is "
                 "non-degenerate on every stream; otherwise the milestone stops "
                 "before clean scoring and reports which streams are dormant"),
    }


def coverage_audit(*, predictions: Mapping[str, Sequence[str]],
                   vocabularies: Mapping[str, Sequence[str]]) -> dict[str, Any]:
    """Which classes of each vocabulary production actually exercised."""
    out: dict[str, Any] = {}
    for stream, vocabulary in vocabularies.items():
        emitted = set(predictions.get(stream) or ())
        covered = [c for c in vocabulary if c in emitted]
        missing = [c for c in vocabulary if c not in emitted]
        out[stream] = {"classes_total": len(vocabulary), "classes_exercised": len(covered),
                       "exercised": covered, "never_emitted": missing,
                       "recall": round(len(covered) / len(vocabulary), 4)
                       if vocabulary else None}
    return out
