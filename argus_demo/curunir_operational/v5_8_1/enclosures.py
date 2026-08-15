"""V5.8.1 — typed enclosures, and what it means for a span to cut one in half.

35 analyses reach selection whose role span opens a bracket or a quotation it
never closes: "(cardiacas,", "«Entro", "6 «The Madrid resolution on".  A span
that begins inside a parenthesis and ends outside it is not a constituent of
anything; it is a slice taken across a boundary.

The obvious test -- count the quote marks, require them even -- is the wrong one,
and the roadmap says so explicitly.  Parity fails in every direction that
matters here:

  * the French apostrophe is U+2019, the same character as a closing single
    quote, so "L'expression" has odd parity and is perfectly well formed;
  * an enumerator "g)" carries a closing parenthesis that never opened;
  * a citation may legitimately span regions, so an opener with no closer in
    THIS span is evidence of truncation, not of malformation;
  * OCR loses delimiters, and a lost delimiter must not convict the text that
    survived it.

So an enclosure here is a typed object with an opening event, a closing event
and a role, and the invariant is about IMPROPER INTERSECTION rather than counts:
a candidate is inadmissible when it crosses an established boundary while
neither containing the enclosure nor sitting inside it.  Where the boundary is
not established -- an unterminated opener, a lost delimiter -- nothing is
concluded.  A layer that cannot see a boundary must not reject on its account.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from ..v5_1.models import Record, now_utc, stable_id


class EnclosureError(ValueError):
    """An enclosure or role asserted outside its declared vocabulary."""


ENCLOSURE_TYPES: tuple[str, ...] = (
    "PARENTHESIS", "BRACKET", "BRACE",
    "GUILLEMET", "GERMAN_QUOTE", "CURLY_QUOTE", "STRAIGHT_QUOTE",
)

#: What the enclosed material is doing.  A finite-looking token inside a
#: METALINGUISTIC_TOKEN or a TITLE is not the matrix predicate, which is why the
#: role is carried rather than merely the extent.
QUOTED_CONTENT_ROLES: tuple[str, ...] = (
    "DIRECT_SPEECH", "TITLE", "TERM_MENTION", "DEFINITION", "CITATION",
    "EXAMPLE", "METALINGUISTIC_TOKEN", "UNKNOWN_QUOTED_CONTENT",
)

INTERSECTION_STATES: tuple[str, ...] = (
    "DISJOINT", "CONTAINED_WITHIN", "CONTAINS_WHOLE", "IMPROPER_INTERSECTION",
    "BOUNDARY_NOT_ESTABLISHED",
)

_PAIRS = {"(": (")", "PARENTHESIS"), "[": ("]", "BRACKET"),
          "{": ("}", "BRACE"), "«": ("»", "GUILLEMET"),
          "„": ("“", "GERMAN_QUOTE"), "“": ("”", "CURLY_QUOTE"),
          "‘": ("’", "CURLY_QUOTE")}
_SYMMETRIC = {'"': "STRAIGHT_QUOTE"}

#: An enumerator's parenthesis never opened.  "g)", "3)", "iv)" are list
#: markers; reading the ")" as an unmatched closer convicts every enumerated
#: provision in the corpus.
_ENUMERATOR_CLOSE = re.compile(r"^\s*(?:\(?[0-9]{1,3}|\(?[a-zA-Z]|\(?[ivxlIVXL]{1,5})\)")

#: U+2019 is the recommended apostrophe in French and English typography
#: ("L’expression", "aujourd’hui").  It is a closing quote only when a matching
#: opener precedes it.
_APOSTROPHE = "’"

_REPORTING = re.compile(
    r"(said|says|stated|declared|noted|reported|schwöre|erklärt|"
    r"déclare|dit|dijo|declaró|disse)\W*$", re.IGNORECASE)
_TERM_INTRODUCER = re.compile(
    r"(the word|the term|the expression|l[’']expression|le terme|"
    r"der begriff|das wort|el término|la expresión|il termine|"
    r"مصطلح|كلمة)\W*$", re.IGNORECASE)
_DEFINITION_FOLLOWS = re.compile(
    r"^\W*(means|designates|désigne|bedeutet|significa|significia|indica|"
    r"يعني|تعني)", re.IGNORECASE)
_EXAMPLE_INTRODUCER = re.compile(
    r"(for example|e\.?g\.?|such as|zum beispiel|z\.\s?b\.|par exemple|"
    r"por ejemplo|ad esempio|مثل)\W*$", re.IGNORECASE)
_CITATION_SHAPE = re.compile(
    r"^\W*(art|artikel|article|articles|abs|nr|no|para|paragraph|section|"
    r"§|resolution|doc|\d)", re.IGNORECASE)
_TITLE_INTRODUCER = re.compile(
    r"(resolution|convention|convenio|convenzione|report|informe|rapport|"
    r"agreement|treaty|decision|regulation|directive|charter|declaration|"
    r"beschluss|verordnung|richtlinie)\W*$", re.IGNORECASE)


@dataclass(frozen=True)
class Enclosure(Record):
    """One delimited region, its role, and how confident we are of its bounds."""

    enclosure_id: str
    region_id: str
    enclosure_type: str
    open_offset: int
    close_offset: int | None
    open_event: str
    close_event: str
    nesting_depth: int
    quoted_content_role: str
    source_convention: str
    continuation_relation: str = "NONE"
    region_ids: tuple[str, ...] = ()
    continuation_region_ids: tuple[str, ...] = ()
    ocr_uncertainty: bool = False
    asserting_component: str = "v5_8_1.enclosures"
    confidence: float = 0.0
    recorded_time: str = ""

    def __post_init__(self) -> None:
        if self.enclosure_type not in ENCLOSURE_TYPES:
            raise EnclosureError(f"unknown enclosure type {self.enclosure_type!r}")
        if self.quoted_content_role not in QUOTED_CONTENT_ROLES:
            raise EnclosureError(
                f"unknown quoted-content role {self.quoted_content_role!r}")

    @property
    def established(self) -> bool:
        """Are both boundaries known?  Nothing is concluded from one alone."""
        return self.close_offset is not None and not self.ocr_uncertainty


def _role_of(text: str, open_offset: int, close_offset: int | None) -> str:
    """What the enclosed material is doing, from its neighbourhood."""
    before = text[max(0, open_offset - 60):open_offset]
    inner = text[open_offset + 1:close_offset] if close_offset else ""
    after = text[close_offset + 1:close_offset + 40] if close_offset else ""

    if _TERM_INTRODUCER.search(before):
        # "The word "is" appears" -- one or two tokens named rather than used.
        return ("METALINGUISTIC_TOKEN" if len(inner.split()) <= 2
                else "TERM_MENTION")
    if _DEFINITION_FOLLOWS.match(after):
        return "DEFINITION"
    if _REPORTING.search(before):
        return "DIRECT_SPEECH"
    if _EXAMPLE_INTRODUCER.search(before):
        return "EXAMPLE"
    if _TITLE_INTRODUCER.search(before):
        return "TITLE"
    if _CITATION_SHAPE.match(inner):
        return "CITATION"
    return "UNKNOWN_QUOTED_CONTENT"


def build_enclosures(text: str, *, region_id: str = "") -> tuple[Enclosure, ...]:
    """Find the enclosures in a span, pairing openers with their closers.

    Two passes.  The first establishes which openers actually close, because an
    opener whose closer is missing tells us the extract is truncated or the
    delimiter was lost -- not that the text is malformed.  The second builds the
    typed records.
    """
    body = text or ""
    found: list[Enclosure] = []
    stack: list[tuple[str, str, int, int]] = []  # closer, type, offset, depth

    for offset, character in enumerate(body):
        if stack and character == stack[-1][0]:
            closer, kind, open_offset, depth = stack.pop()
            found.append(_make(body, region_id, kind, open_offset, offset, depth))
            continue
        if character in _SYMMETRIC:
            matching = [i for i, entry in enumerate(stack)
                        if entry[0] == character]
            if matching:
                closer, kind, open_offset, depth = stack.pop(matching[-1])
                found.append(_make(body, region_id, kind, open_offset, offset,
                                   depth))
            else:
                stack.append((character, _SYMMETRIC[character], offset,
                              len(stack)))
            continue
        if character == _APOSTROPHE and not any(
                entry[0] == _APOSTROPHE for entry in stack):
            # An apostrophe, not a closing quote: no opener is waiting for it.
            continue
        if character in _PAIRS:
            closer, kind = _PAIRS[character]
            stack.append((closer, kind, offset, len(stack)))

    # Openers that never closed.  Recorded, with the boundary NOT established.
    for closer, kind, open_offset, depth in stack:
        found.append(_make(body, region_id, kind, open_offset, None, depth,
                           ocr_uncertainty=True))

    return tuple(sorted(found, key=lambda e: (e.open_offset,
                                              e.close_offset or 10 ** 9)))


def _make(text: str, region_id: str, kind: str, open_offset: int,
          close_offset: int | None, depth: int,
          ocr_uncertainty: bool = False) -> Enclosure:
    return Enclosure(
        enclosure_id=stable_id("v5-8-1-enclosure", region_id, open_offset,
                               close_offset if close_offset is not None else -1),
        region_id=region_id,
        enclosure_type=kind,
        open_offset=open_offset,
        close_offset=close_offset,
        open_event=text[open_offset:open_offset + 1],
        close_event=(text[close_offset:close_offset + 1]
                     if close_offset is not None else ""),
        nesting_depth=depth,
        quoted_content_role=_role_of(text, open_offset, close_offset),
        source_convention=("GUILLEMET" if kind == "GUILLEMET" else
                           "GERMAN_LOW_HIGH" if kind == "GERMAN_QUOTE" else
                           "TYPOGRAPHIC" if kind == "CURLY_QUOTE" else
                           "ASCII" if kind == "STRAIGHT_QUOTE" else "BRACKETING"),
        continuation_relation=("POSSIBLE_CROSS_REGION_CONTINUATION"
                               if close_offset is None else "NONE"),
        region_ids=(region_id,) if region_id else (),
        ocr_uncertainty=ocr_uncertainty,
        confidence=0.4 if ocr_uncertainty else 0.9,
        recorded_time=now_utc(),
    )


def intersection_state(span: tuple[int, int] | None,
                       enclosure: Enclosure) -> str:
    """How a span sits relative to one enclosure."""
    if span is None:
        return "DISJOINT"
    if not enclosure.established:
        return "BOUNDARY_NOT_ESTABLISHED"
    start, stop = span
    open_offset = enclosure.open_offset
    close_offset = enclosure.close_offset
    if stop <= open_offset or start > close_offset:
        return "DISJOINT"
    if start > open_offset and stop <= close_offset:
        return "CONTAINED_WITHIN"
    if start <= open_offset and stop > close_offset:
        return "CONTAINS_WHOLE"
    return "IMPROPER_INTERSECTION"


def improper_intersections(span: tuple[int, int] | None,
                           enclosures: tuple[Enclosure, ...]
                           ) -> tuple[Enclosure, ...]:
    """Enclosures this span cuts across without containing or entering."""
    if span is None:
        return ()
    return tuple(e for e in enclosures
                 if intersection_state(span, e) == "IMPROPER_INTERSECTION")


def assess_span(span: tuple[int, int] | None, text: str, *,
                region_id: str = "",
                enclosures: tuple[Enclosure, ...] | None = None) -> dict[str, Any]:
    """Whether a role span crosses an established enclosure boundary."""
    found = (build_enclosures(text, region_id=region_id)
             if enclosures is None else enclosures)
    result: dict[str, Any] = {
        "enclosure_count": len(found),
        "enclosure_ids": tuple(e.enclosure_id for e in found),
        "improper_enclosure_ids": (),
        "improper_enclosure_types": (),
        "quoted_content_roles": (),
        "ocr_uncertain_enclosures": sum(1 for e in found if e.ocr_uncertainty),
        "verdict": "NO_ENCLOSURE_VIOLATION",
    }
    if span is None or not found:
        return result
    improper = improper_intersections(span, found)
    if not improper:
        return result
    result["improper_enclosure_ids"] = tuple(e.enclosure_id for e in improper)
    result["improper_enclosure_types"] = tuple(e.enclosure_type for e in improper)
    result["quoted_content_roles"] = tuple(e.quoted_content_role for e in improper)
    result["verdict"] = "IMPROPERLY_INTERSECTS_ENCLOSURE"
    return result


def encloses(offset: int, enclosures: tuple[Enclosure, ...]) -> tuple[Enclosure, ...]:
    """Established enclosures containing a character offset."""
    return tuple(e for e in enclosures
                 if e.established and e.open_offset < offset <= e.close_offset)


def suppressing_depth(text: str, enclosures: tuple[Enclosure, ...]) -> list[int]:
    """Per-character enclosure depth counting ESTABLISHED enclosures only.

    Clause segmentation consumes this so that a full stop inside a quotation
    does not open a proposition -- while an opener that never closes suppresses
    nothing, since it is evidence of truncation rather than of an enclosure
    covering all following text.
    """
    depth = [0] * (len(text) + 1)
    for enclosure in enclosures:
        if not enclosure.established:
            continue
        for offset in range(enclosure.open_offset, enclosure.close_offset + 1):
            depth[offset] += 1
    return depth


__all__ = [
    "ENCLOSURE_TYPES", "QUOTED_CONTENT_ROLES", "INTERSECTION_STATES",
    "EnclosureError", "Enclosure", "build_enclosures", "intersection_state",
    "improper_intersections", "assess_span", "encloses", "suppressing_depth",
]
