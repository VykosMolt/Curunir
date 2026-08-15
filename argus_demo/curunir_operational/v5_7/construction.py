"""V5.7 §3–§6 — repairing how evidence is constructed, before anything reads it.

Four defects the V5.6.1 and Gate A panels found by reading packets, not by
running checks:

* **§3** spans cut inside a value.  ``is €15.`` where the source says €15.3
  billion does not truncate the figure, it *replaces* it — fifteen euros.  Nine
  such spans reached a reference panel.
* **§4** context joined by list position.  Eight packets carried the neighbouring
  unit's context; two seats spotted it as "rotated by one item".
* **§5** a claim used as its own evidence.  Three bundles echoed the target
  sentence back as its support.
* **§6** original text, translation and gloss concatenated unlabelled.  Three
  spans of German and Spanish carried ``original_language: "en"``.

Each is repaired here as a mechanical property of construction, checkable
without a model and without a reviewer, because each one survived review by
being invisible to the checks rather than by being subtle.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from ..v5_1.models import Record, now_utc, sha256, stable_id


class ConstructionViolation(RuntimeError):
    """Evidence was built in a way that changes what it says."""


# ===========================================================================
# §3 — numeric and value-boundary integrity
# ===========================================================================

#: A value whose meaning lives across the punctuation.  Each pattern matches a
#: COMPLETE structure; the repair detects a span that ends part-way through one.
_COMPLETE_STRUCTURES: tuple[tuple[str, re.Pattern[str]], ...] = (
    # Order matters at equal width: the first pattern reaching the widest end
    # wins, so the more specific structure is listed first.  `1,250` is a
    # thousands group, not one-point-two-five, and labelling it `decimal` would
    # misdescribe the value in the repair record.
    ("thousands", re.compile(r"\d{1,3}(?:,\d{3})+(?:\.\d+)?")),
    ("decimal", re.compile(r"\d+[.,]\d+")),
    ("percentage", re.compile(r"\d+(?:[.,]\d+)?\s*%")),
    ("currency", re.compile(r"[€$£¥]\s?\d+(?:[.,]\d+)?(?:\s*(?:million|billion|"
                            r"trillion|Mio\.?|Mrd\.?|thousand))?", re.IGNORECASE)),
    ("range", re.compile(r"\d+(?:[.,]\d+)?\s*[–—-]\s*\d+(?:[.,]\d+)?")),
    ("ratio", re.compile(r"\d+\s*:\s*\d+")),
    ("scientific", re.compile(r"\d+(?:\.\d+)?e[+-]?\d+", re.IGNORECASE)),
    ("version", re.compile(r"\d+\.\d+(?:\.\d+)*")),
    ("date_ordinal", re.compile(r"\d{1,2}\.\s*(?:Januar|Februar|März|April|Mai|"
                                r"Juni|Juli|August|September|Oktober|November|"
                                r"Dezember|January|February|March|May|June|July|"
                                r"August|October|December)", re.IGNORECASE)),
    ("date_numeric", re.compile(r"\d{1,2}[./]\d{1,2}[./]\d{2,4}")),
    ("measurement", re.compile(r"\d+(?:[.,]\d+)?\s*(?:km|m|cm|mm|kg|g|t|MW|GW|kWh|"
                               r"MWh|TWh|ha|km²|m²|°C|bn|k)\b", re.IGNORECASE)),
    ("legal_article", re.compile(r"Article\s+\d+(?:\(\d+\))?", re.IGNORECASE)),
    ("reference_code", re.compile(r"[A-Z]{1,4}/\d+/\d+")),
)

#: A span ending in one of these is cut mid-structure: a digit followed by a
#: separator with nothing after it, or a bare currency/opening token.
_TRUNCATED_TAIL = re.compile(
    r"(?:"
    r"\d[.,]"                       # 15.  |  0,
    r"|[€$£¥]\s?\d*[.,]?"           # €  |  €15.
    r"|\d+\s*[–—-]"                 # 3–
    r"|\d+\s*:"                     # 2:
    r"|\d{1,2}\.\s*"                # 30.   (German ordinal date)
    r"|\bArticle\s+\d+\($"          # Article 12(
    r")\s*$")

#: Structure fields — mechanical, not learned (§3.2).
STRUCTURE_FIELDS: tuple[str, ...] = (
    "numeric_token_complete", "decimal_complete", "percentage_complete",
    "currency_complete", "range_complete", "unit_complete",
    "table_cell_mapping_complete",
)


def _normalise(text: str) -> str:
    return unicodedata.normalize("NFC", text or "")


def numeric_structure(span: str, *, context: str = "",
                     table_cell_mapped: bool | None = None) -> dict[str, Any]:
    """§3.2 — the mechanical structure of a span's values.

    ``*_complete`` answers one question: does every value structure that STARTS
    inside this span also END inside it?  A span carrying no values is complete
    by vacuity, which is correct — there is nothing to shear.
    """
    span = _normalise(span)
    context_n = _normalise(context)
    kinds_present = {name for name, pattern in _COMPLETE_STRUCTURES
                     if pattern.search(span)}

    # Two detectors, because neither alone is sufficient.
    #
    # The structural one is decisive where context is held: if a COMPLETE value
    # structure in the surrounding text straddles the span's end, the span cut
    # through a value.  This catches `reached 30` → `30%`, which no
    # trailing-punctuation pattern can see, and it never fires on a span that
    # merely ends in a number.
    #
    # The tail pattern is the fallback for a span with no context, where a
    # dangling separator is the only evidence available.
    truncated_kind = None
    index = context_n.find(span) if context_n and span else -1
    if index >= 0:
        cut = index + len(span)
        widest_end = -1
        for name, pattern in _COMPLETE_STRUCTURES:
            for match in pattern.finditer(context_n):
                if match.start() < cut < match.end() and match.end() > widest_end:
                    widest_end, truncated_kind = match.end(), name
    if truncated_kind is None and _TRUNCATED_TAIL.search(span):
        truncated_kind = "numeric"
    tail = _TRUNCATED_TAIL.search(span)

    complete = truncated_kind is None
    return {
        "numeric_token_complete": complete,
        "decimal_complete": complete or truncated_kind != "decimal",
        "percentage_complete": complete or truncated_kind != "percentage",
        "currency_complete": complete or truncated_kind != "currency",
        "range_complete": complete or truncated_kind != "range",
        "unit_complete": complete or truncated_kind != "measurement",
        "table_cell_mapping_complete": (True if table_cell_mapped is None
                                        else bool(table_cell_mapped)),
        "truncated_structure": truncated_kind,
        "structures_present": sorted(kinds_present),
        "trailing_fragment": tail.group(0) if tail else None,
    }


def value_repair(span: str, context: str) -> dict[str, Any] | None:
    """§3.3 — the bounded repair that completes a sheared value, if one exists."""
    structure = numeric_structure(span, context=context)
    if structure["numeric_token_complete"]:
        return None
    span_n, context_n = _normalise(span), _normalise(context)
    index = context_n.find(span_n)
    if index < 0:
        return {"repair_direction": "EXPAND_RIGHT_CONTEXT", "replacement_span": None,
                "complete_value": None, "complete_unit": None,
                "required_context": "the span is not located in its own context",
                "repairable": False,
                "reason": "the value is sheared and the completion is not "
                          "available in the bounded context supplied"}
    after = context_n[index + len(span_n):]
    # Extend to the end of the structure the span cut through — the WIDEST such
    # structure, not the first to match.  `€15.` is cut inside a decimal and
    # inside a currency amount at once; completing only the decimal yields
    # `€15.3`, which is still not what the source says.  The magnitude word is
    # part of the value.
    cut = index + len(span_n)
    end = None
    for name, pattern in _COMPLETE_STRUCTURES:
        for match in pattern.finditer(context_n):
            if match.start() < cut <= match.end():
                end = max(end or 0, match.end())
    extended = context_n[index:end] if end else None
    if extended is not None:
        # A value's unit is part of the value.  `3–5` completes the range but
        # still omits the `km` that says what was measured.
        unit = re.match(r"\s*(?:million|billion|trillion|thousand|%|km²|m²|km|m|"
                        r"cm|mm|kg|g|t|MW|GW|kWh|MWh|TWh|ha|°C)\b",
                        context_n[end:], re.IGNORECASE)
        if unit:
            extended = context_n[index:end + unit.end()]
    if extended is None:
        word = re.match(r"\S+", after)
        extended = span_n + (word.group(0) if word else "")
    return {
        "repair_direction": "EXPAND_RIGHT_CONTEXT",
        "replacement_span": extended,
        "required_context": after[:60],
        "complete_value": extended[len(span_n):].strip() or None,
        "complete_unit": (re.search(r"(million|billion|trillion|%|km|MW|GWh)",
                                    extended, re.IGNORECASE) or [None])
                         and (re.search(r"(million|billion|trillion|%|km|MW|GWh)",
                                        extended, re.IGNORECASE).group(0)
                              if re.search(r"(million|billion|trillion|%|km|MW|GWh)",
                                           extended, re.IGNORECASE) else None),
        "repairable": True,
        "structure": structure["truncated_structure"],
        "reason": f"the span ends inside a {structure['truncated_structure']} "
                  f"structure; the completion is present in bounded context",
    }


def audit_numeric_boundaries(units: Iterable[Mapping[str, Any]], *,
                             span_field: str = "raw_span",
                             context_field: str = "expanded_context"
                             ) -> dict[str, Any]:
    units = list(units)
    sheared, repairable = [], 0
    for unit in units:
        structure = numeric_structure(unit.get(span_field, ""),
                                      context=unit.get(context_field, ""))
        if not structure["numeric_token_complete"]:
            repair = value_repair(unit.get(span_field, ""), unit.get(context_field, ""))
            repairable += bool(repair and repair["repairable"])
            sheared.append({"unit": unit.get("packet_id") or unit.get("candidate_id"),
                            "structure": structure["truncated_structure"],
                            "fragment": structure["trailing_fragment"],
                            "repairable": bool(repair and repair["repairable"])})
    return {"units": len(units), "sheared_spans": len(sheared),
            "repairable_in_bounded_context": repairable,
            "examples": sheared[:10],
            "verdict": "PASS" if not sheared else "DEFECTS_PRESENT"}


# ===========================================================================
# §4 — keyed context alignment
# ===========================================================================

#: §4.1 — the keys a context join must agree on.  Position is not among them.
JOIN_KEYS: tuple[str, ...] = (
    "source_id", "source_family_id", "intellectual_work_id", "manifestation_id",
    "candidate_id", "page_or_section", "context_relation", "language",
)

TABLE_JOIN_KEYS: tuple[str, ...] = (
    "table_or_figure_id", "row_or_cell_identity", "caption_or_heading_identity",
)

CONTEXT_RELATIONS: tuple[str, ...] = (
    "LOCAL_SURROUNDING_TEXT", "SAME_DOCUMENT_ELSEWHERE",
    "LINKED_CORROBORATING_DOCUMENT", "LINKED_CONTRADICTING_DOCUMENT",
    "DOCUMENT_METADATA_ONLY", "NO_CONTEXT_HELD",
)

#: Only this relation may be presented as the candidate's own local context.
LOCAL_RELATION = "LOCAL_SURROUNDING_TEXT"


@dataclass(frozen=True)
class ContextJoin(Record):
    """§4.2 — one candidate-to-context join, with its keys checked."""

    join_id: str
    candidate_id: str
    context_relation: str
    candidate_keys: Mapping[str, Any]
    context_keys: Mapping[str, Any]
    span_contained_in_context: bool
    mismatched_keys: tuple[str, ...]
    recorded_time: str

    def __post_init__(self) -> None:
        if self.context_relation not in CONTEXT_RELATIONS:
            raise ConstructionViolation(
                f"unknown context relation: {self.context_relation!r}")
        if self.context_relation == LOCAL_RELATION:
            if self.mismatched_keys:
                raise ConstructionViolation(
                    f"context presented as the candidate's own local text "
                    f"disagrees on {list(self.mismatched_keys)}; context from "
                    "elsewhere may appear only as explicitly linked evidence")
            if not self.span_contained_in_context:
                raise ConstructionViolation(
                    "local surrounding text must contain the span it surrounds; "
                    "this is the check that a presence test cannot make")

    @property
    def is_local(self) -> bool:
        return self.context_relation == LOCAL_RELATION


def join_context(*, candidate: Mapping[str, Any], context: Mapping[str, Any],
                 context_relation: str = LOCAL_RELATION,
                 span_field: str = "raw_span",
                 text_field: str = "text") -> ContextJoin:
    """§4 — join on keys, never on position, and verify containment."""
    required = list(JOIN_KEYS)
    if candidate.get("table_or_figure_id") or context.get("table_or_figure_id"):
        required += list(TABLE_JOIN_KEYS)
    mismatched = tuple(
        key for key in required
        if key not in ("candidate_id", "context_relation")
        and candidate.get(key) is not None and context.get(key) is not None
        and candidate.get(key) != context.get(key))
    span = _normalise(candidate.get(span_field, ""))
    text = _normalise(context.get(text_field, ""))
    return ContextJoin(
        stable_id("v5-7-join", candidate.get("candidate_id", ""),
                  context.get("manifestation_id", "")),
        candidate.get("candidate_id", ""), context_relation,
        {k: candidate.get(k) for k in required},
        {k: context.get(k) for k in required},
        bool(span) and span in text, mismatched, now_utc())


def audit_context_alignment(joins: Iterable[ContextJoin]) -> dict[str, Any]:
    joins = list(joins)
    foreign_local = [j.candidate_id for j in joins
                     if j.is_local and (j.mismatched_keys or
                                        not j.span_contained_in_context)]
    return {
        "joins": len(joins),
        "local_joins": sum(1 for j in joins if j.is_local),
        "foreign_context_presented_as_local": len(foreign_local),
        "keyed_matches": sum(1 for j in joins if not j.mismatched_keys),
        "span_contained": sum(1 for j in joins if j.span_contained_in_context),
        "examples": foreign_local[:10],
        "verdict": "PASS" if not foreign_local else "FAIL",
    }


def rotate(sequence: Sequence[Any], offset: int = 1) -> list[Any]:
    """The attack §4.3 requires to be permanently testable."""
    if not sequence:
        return []
    offset %= len(sequence)
    return list(sequence[offset:]) + list(sequence[:offset])


# ===========================================================================
# §5 — claim echo
# ===========================================================================

ECHO_KINDS: tuple[str, ...] = (
    "TARGET_CLAIM_TEXT", "PRODUCTION_NORMALIZED_CLAIM", "REPORT_SENTENCE",
    "GENERATED_SUMMARY", "EVALUATION_PROMPT", "REVIEWER_QUESTION",
    "CLAIM_COPIED_INTO_EVIDENCE", "CLAIM_DERIVED_OBSERVATION_WITHOUT_PROVENANCE",
)

#: §5.2 — what makes an identical string a legitimate source quotation rather
#: than an echo: the direction of derivation is recorded and runs source→claim.
REQUIRED_SOURCE_PROVENANCE: tuple[str, ...] = (
    "raw_source_text", "source_location", "capture_hash", "observation_id",
)


def _shingles(text: str, size: int = 7) -> set[str]:
    words = re.findall(r"\w+", _normalise(text).casefold())
    if len(words) < size:
        return {" ".join(words)} if words else set()
    return {" ".join(words[i:i + size]) for i in range(len(words) - size + 1)}


def near_duplicate(left: str, right: str, threshold: float = 0.8) -> float:
    a, b = _shingles(left), _shingles(right)
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def detect_claim_echo(*, claim_text: str, evidence_spans: Sequence[Mapping[str, Any]],
                      threshold: float = 0.8) -> dict[str, Any]:
    """§5.3 — circular provenance, distinguished from legitimate quotation."""
    echoes, legitimate = [], []
    for span in evidence_spans:
        text = span.get("text", "")
        overlap = near_duplicate(claim_text, text, threshold)
        exact = _normalise(claim_text).strip() == _normalise(text).strip()
        if not exact and overlap < threshold:
            continue
        missing = [f for f in REQUIRED_SOURCE_PROVENANCE if not span.get(f)]
        row = {"span_id": span.get("span_id"), "overlap": round(overlap, 3),
               "exact": exact, "missing_provenance": missing,
               "derivation": span.get("derivation_direction")}
        if missing or span.get("derivation_direction") != "SOURCE_TO_CLAIM":
            row["echo_kind"] = span.get("echo_kind") or "CLAIM_COPIED_INTO_EVIDENCE"
            echoes.append(row)
        else:
            # The source genuinely says it, and we can show it said it first.
            legitimate.append(row)
    return {
        "evidence_spans": len(evidence_spans),
        "claim_echoes": len(echoes),
        "legitimate_source_quotations": len(legitimate),
        "echo_detail": echoes,
        "rule": ("a matching string is an echo only when the direction of "
                 "derivation is missing or runs claim→evidence.  A source that "
                 "genuinely contains the sentence, with capture provenance, is "
                 "evidence — rejecting it would be the opposite error"),
        "verdict": "PASS" if not echoes else "FAIL",
    }


# ===========================================================================
# §6 — language boundaries
# ===========================================================================

REPRESENTATION_KINDS: tuple[str, ...] = (
    "ORIGINAL_LANGUAGE_SOURCE", "OFFICIAL_TRANSLATION", "MODEL_TRANSLATION",
    "REVIEWER_TRANSLATION", "NORMALIZED_GLOSS",
)

#: §6.2 — which representation may be authoritative for which dimension.
#: A gloss is never authoritative for anything quotable.
AUTHORITY_DIMENSIONS: tuple[str, ...] = (
    "exact_quotation", "numeric_value", "entity_name", "attribution",
    "semantic_proposition", "legal_wording",
)

NEVER_AUTHORITATIVE_FOR = {
    "NORMALIZED_GLOSS": ("exact_quotation", "legal_wording"),
    "MODEL_TRANSLATION": ("exact_quotation", "legal_wording"),
}

_SCRIPT_HINTS: Mapping[str, re.Pattern[str]] = {
    "de": re.compile(r"\b(der|die|das|und|nicht|sollte|Verordnung|Kommission|"
                     r"jedes|nach|Veröffentlichung|gemäß|über)\b"),
    "es": re.compile(r"\b(el|la|los|las|energías|alcanzaron|electricidad|"
                     r"superando|primera|generada|según)\b"),
    "fr": re.compile(r"\b(le|la|les|des|selon|règlement|Commission|"
                     r"conformément|publication)\b"),
    "it": re.compile(r"\b(il|lo|gli|della|regolamento|Commissione|secondo)\b"),
}


def detect_language(text: str) -> str | None:
    """A cheap, honest script/function-word check — not a classifier."""
    text = _normalise(text)
    scores = {code: len(pattern.findall(text)) for code, pattern in _SCRIPT_HINTS.items()}
    best = max(scores, key=scores.get) if scores else None
    return best if best and scores[best] >= 2 else None


@dataclass(frozen=True)
class LanguageRepresentation(Record):
    """§6.1 — one labelled representation of one span."""

    representation_id: str
    kind: str
    source_language: str
    target_language: str | None
    translator_or_provider: str | None
    original_span_id: str
    translated_span_id: str | None
    official_status: str
    text: str
    integrity_hash: str
    recorded_time: str

    def __post_init__(self) -> None:
        if self.kind not in REPRESENTATION_KINDS:
            raise ConstructionViolation(f"unknown representation kind: {self.kind!r}")
        if self.kind != "ORIGINAL_LANGUAGE_SOURCE":
            if not self.target_language:
                raise ConstructionViolation(
                    f"{self.kind} must record the language it renders into")
            if not self.translator_or_provider:
                raise ConstructionViolation(
                    f"{self.kind} must record who or what produced it")
            if not self.translated_span_id:
                raise ConstructionViolation(
                    f"{self.kind} must record the span it produced")

    def authoritative_for(self, dimension: str) -> bool:
        if dimension not in AUTHORITY_DIMENSIONS:
            raise ConstructionViolation(f"unknown authority dimension: {dimension!r}")
        return dimension not in NEVER_AUTHORITATIVE_FOR.get(self.kind, ())


def representation(*, kind: str, source_language: str, text: str,
                   original_span_id: str, target_language: str | None = None,
                   translator_or_provider: str | None = None,
                   translated_span_id: str | None = None,
                   official_status: str = "UNOFFICIAL") -> LanguageRepresentation:
    return LanguageRepresentation(
        stable_id("v5-7-representation", original_span_id, kind), kind,
        source_language, target_language, translator_or_provider,
        original_span_id, translated_span_id, official_status, text,
        sha256({"kind": kind, "text": _normalise(text)}), now_utc())


def audit_language_boundaries(units: Iterable[Mapping[str, Any]], *,
                              text_field: str = "raw_span",
                              declared_field: str = "original_language"
                              ) -> dict[str, Any]:
    """§6.4 — declared language against the text actually present."""
    units, mistagged, mixed = list(units), [], []
    for unit in units:
        text = unit.get(text_field) or unit.get("original_language_text") or ""
        declared = unit.get(declared_field)
        detected = detect_language(text)
        if detected and declared and detected != declared:
            mistagged.append({"unit": unit.get("packet_id") or unit.get("candidate_id"),
                              "declared": declared, "detected": detected,
                              "text": text[:70]})
        # An unlabelled mixture: two different language signatures in one span
        # with no representation breakdown recorded.
        signatures = [code for code, pattern in _SCRIPT_HINTS.items()
                      if len(pattern.findall(_normalise(text))) >= 2]
        if len(signatures) > 1 and not unit.get("representations"):
            mixed.append({"unit": unit.get("packet_id"), "languages": signatures})
    return {
        "units": len(units),
        "mistagged_language": len(mistagged),
        "unlabelled_mixed_language_spans": len(mixed),
        "examples": (mistagged + mixed)[:10],
        "verdict": "PASS" if not mistagged and not mixed else "FAIL",
    }


__all__ = [
    "ConstructionViolation", "STRUCTURE_FIELDS", "numeric_structure",
    "value_repair", "audit_numeric_boundaries", "JOIN_KEYS", "TABLE_JOIN_KEYS",
    "CONTEXT_RELATIONS", "LOCAL_RELATION", "ContextJoin", "join_context",
    "audit_context_alignment", "rotate", "ECHO_KINDS",
    "REQUIRED_SOURCE_PROVENANCE", "near_duplicate", "detect_claim_echo",
    "REPRESENTATION_KINDS", "AUTHORITY_DIMENSIONS", "detect_language",
    "LanguageRepresentation", "representation", "audit_language_boundaries",
]


# ===========================================================================
# §3/§7 — the other half of a defective boundary: an unbound reference
# ===========================================================================
#
# Every recoverable case in the reference fails one dimension,
# ``PROPOSITION_BOUNDARY_DEFENSIBLE``, in one of two mechanically distinct ways.
# One is a value sheared at an edge, handled above.  The other is a reference
# the span does not bind: `Rail projects will receive 80% of the €7 billion`
# names no €7 billion, `The application must be submitted` names no
# application, `Immediately secured the compromised secret` names nobody.
#
# Each is checkable without judgement: find the referring expression, look for
# its antecedent inside the span, and if it is absent but present in the
# preceding context, a bounded left expansion binds it.

#: Openers that cannot begin a self-contained proposition.
_UNBOUND_OPENERS = (
    re.compile(r"^(and|or|but|nor|yet)\b", re.IGNORECASE),
    re.compile(r"^\[\d+\]"),                       # [34] [35] footnote chrome
    re.compile(r"^(in such a case|such a case|that|this|these|those|it|they)\b",
               re.IGNORECASE),
    re.compile(r"^\d"),                            # `4% in the EU`, `2024 for rail`
    re.compile(r"^(immediately|subsequently|thereafter|meanwhile|respectively)\b",
               re.IGNORECASE),
)

#: A definite or demonstrative reference: `the X`, `that X`, `such a X`.  The
#: head is the FIRST token after the determiner — taking the last would grab the
#: verb out of "the application must be submitted" and test the wrong word.  A
#: currency symbol may open the head, because "the €7 billion" refers to a sum.
_DEFINITE_REFERENCE = re.compile(
    r"\b(?:the|that|this|these|those|such an?|said)\s+"
    r"((?:[€$£¥]\s?)?[\w-]+)", re.IGNORECASE)

#: Heads that are generic enough that a definite article implies no antecedent.
_GENERIC_HEADS = frozenset({
    "european", "commission", "council", "parliament", "union", "regulation",
    "directive", "member", "states", "case", "same", "following", "first",
    "second", "third", "total", "number", "end", "time", "year", "world",
})


def unbound_reference(span: str, context: str = "") -> dict[str, Any]:
    """Does this span rely on something it does not itself introduce?

    Returns the first unbound reference found and whether the preceding context
    would bind it.  ``bindable`` is what separates a recoverable boundary from a
    genuinely incomplete proposition: if the antecedent is not in reach either,
    expanding the span does not help.
    """
    span_n, context_n = _normalise(span), _normalise(context)
    opener = next((p.pattern for p in _UNBOUND_OPENERS if p.match(span_n.strip())), None)

    preceding = ""
    index = context_n.find(span_n)
    if index > 0:
        preceding = context_n[:index]

    unbound = None
    for match in _DEFINITE_REFERENCE.finditer(span_n):
        head = match.group(1).strip().casefold()
        if not head or head in _GENERIC_HEADS:
            continue
        # A reference to a VALUE ("the €7 billion") is unbound unless that value
        # appears earlier in the span; a reference to a NOUN needs a noun head
        # long enough to be one.
        is_value = bool(re.match(r"[€$£¥]?\s?\d", head))
        if not is_value and len(head) < 4:
            continue
        # A word boundary before `€` never matches, so a value head is looked
        # for as a plain substring and a noun head with word boundaries.
        needle = (re.escape(head) if is_value
                  else rf"\b{re.escape(head)}\b")
        # Bound if the head is introduced earlier in the span itself.
        before_here = span_n[:match.start()]
        if re.search(needle, before_here, re.IGNORECASE):
            continue
        # Unbound within the span.  Is it bindable from the left?
        if preceding and re.search(needle, preceding, re.IGNORECASE):
            unbound = {"phrase": match.group(0), "head": head, "bindable": True}
            break
        if unbound is None:
            unbound = {"phrase": match.group(0), "head": head, "bindable": False}

    defective = bool(opener) or bool(unbound)
    bindable = bool(preceding) and (bool(opener) or bool(unbound and unbound["bindable"]))
    return {
        "boundary_defensible": not defective,
        "unbound_opener": opener,
        "unbound_reference": unbound,
        "bindable_by_left_expansion": bindable,
        "preceding_context_available": bool(preceding),
    }
