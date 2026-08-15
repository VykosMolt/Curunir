"""V5.8.1 §8-§11 — PDF reading-order integrity.

A two-column page extracted line-by-line produces sentences the document never
contained: "the prison regime is tion is examined".  Three independent reviewer
seats caught one such unit and an adjudicator classified it a packet
construction defect.  Grammar cannot repair that, because there is nothing
ungrammatical about the result — the words are real, the order is invented.  So
layout is decided before syntax, from geometry rather than from prose.

Geometry comes from ``pdftotext -bbox-layout``, which reports every word with a
bounding box.  Columns are found as vertical gutters: bands of x with no word in
them, tall enough to be structural rather than an accident of one short line.
Reading order is then column-major, and the naive line-major extraction is
*compared against it* — when a page's visual lines routinely span two columns,
the flat extraction is interleaving them, and that is the defect, detected
rather than assumed.
"""

from __future__ import annotations

import os
import re
import subprocess
import tempfile
from collections.abc import Sequence
from dataclasses import dataclass, field
from xml.etree import ElementTree

from ..v5_1.models import Record, now_utc, sha256, stable_id

READING_ORDER_STATES: tuple[str, ...] = (
    "READING_ORDER_ESTABLISHED", "READING_ORDER_RECOVERABLE",
    "LAYOUT_RECONSTRUCTION_REQUIRED", "PACKET_CONSTRUCTION_DEFECT",
)

#: A gutter must be this fraction of page width to count as a column boundary.
MIN_GUTTER_WIDTH_RATIO = 0.020
#: and must run this fraction of the text band's height.
MIN_GUTTER_HEIGHT_RATIO = 0.55
#: Words whose vertical centres are within this fraction of page height belong
#: to one visual line.
LINE_TOLERANCE_RATIO = 0.006
#: Above this share of visual lines spanning more than one column, a line-major
#: extraction is interleaving the columns.
INTERLEAVING_LINE_FRACTION = 0.25
#: Below this overall confidence no semantic proposition packet may be built.
MIN_PACKET_CONFIDENCE = 0.70


@dataclass(frozen=True)
class WordBox:
    text: str
    xmin: float
    ymin: float
    xmax: float
    ymax: float

    @property
    def xcentre(self) -> float:
        return (self.xmin + self.xmax) / 2

    @property
    def ycentre(self) -> float:
        return (self.ymin + self.ymax) / 2


@dataclass(frozen=True)
class PageReadingOrder(Record):
    page_id: str
    page_number: int
    width: float
    height: float
    detected_column_count: int
    column_bounds: tuple[tuple[float, float], ...]
    column_assignments: tuple[int, ...]
    line_count: int
    cross_column_line_count: int
    column_assignment_confidence: float
    block_continuity_confidence: float
    line_merge_confidence: float
    header_state: str
    footer_state: str
    masthead_state: str
    reading_order_text: str
    flat_text: str
    state: str
    evidence: str


@dataclass(frozen=True)
class DocumentReadingOrderAssessment(Record):
    """§8 — what the layout is, and how confident that reading is."""

    assessment_id: str
    document_id: str
    manifestation_id: str
    pages: tuple[PageReadingOrder, ...]
    detected_column_count: int
    overall_layout_confidence: float
    cross_page_continuity_confidence: float
    state: str
    repair_actions: tuple[str, ...]
    source_lineage: str
    recorded_time: str

    def __post_init__(self) -> None:
        if self.state not in READING_ORDER_STATES:
            raise ValueError(f"unknown reading-order state {self.state!r}")

    @property
    def admits_propositions(self) -> bool:
        """§9 — whether a semantic packet may be built from this manifestation."""
        return (self.state in ("READING_ORDER_ESTABLISHED",
                               "READING_ORDER_RECOVERABLE")
                and self.overall_layout_confidence >= MIN_PACKET_CONFIDENCE)

    def ordered_text(self) -> str:
        return "\n".join(page.reading_order_text for page in self.pages)


# ===========================================================================
# Geometry
# ===========================================================================

def word_boxes(pdf_bytes: bytes) -> list[tuple[float, float, list[WordBox]]]:
    """Pages of words with bounding boxes, via pdftotext -bbox-layout."""
    in_fd, in_name = tempfile.mkstemp(prefix="curunir-layout-", suffix=".pdf")
    out_fd, out_name = tempfile.mkstemp(prefix="curunir-layout-", suffix=".xml")
    os.close(out_fd)
    try:
        with os.fdopen(in_fd, "wb") as handle:
            handle.write(pdf_bytes)
        result = subprocess.run(
            ["pdftotext", "-bbox-layout", in_name, out_name],
            capture_output=True, text=True, timeout=180, check=False)
        if result.returncode:
            raise ValueError(f"PDF_LAYOUT_PARSE_FAILED:{result.stderr[:200]}")
        raw = open(out_name, encoding="utf-8", errors="replace").read()
    finally:
        for name in (in_name, out_name):
            try:
                os.unlink(name)
            except FileNotFoundError:
                pass

    raw = re.sub(r'\sxmlns="[^"]+"', "", raw, count=1)
    try:
        root = ElementTree.fromstring(raw)
    except ElementTree.ParseError as error:
        raise ValueError(f"PDF_LAYOUT_XML_INVALID:{error}") from None

    pages: list[tuple[float, float, list[WordBox]]] = []
    for page in root.iter("page"):
        width = float(page.get("width", 0) or 0)
        height = float(page.get("height", 0) or 0)
        words: list[WordBox] = []
        for word in page.iter("word"):
            text = (word.text or "").strip()
            if not text:
                continue
            words.append(WordBox(
                text, float(word.get("xMin", 0)), float(word.get("yMin", 0)),
                float(word.get("xMax", 0)), float(word.get("yMax", 0))))
        pages.append((width, height, words))
    return pages


def detect_columns(words: Sequence[WordBox], width: float, height: float
                   ) -> tuple[list[tuple[float, float]], float]:
    """Vertical gutters wide and tall enough to be structural."""
    if not words or width <= 0:
        return [(0.0, width)], 0.0
    body = [w for w in words
            if 0.08 * height <= w.ycentre <= 0.94 * height]
    if len(body) < 20:
        body = list(words)
    top = min(w.ymin for w in body)
    bottom = max(w.ymax for w in body)
    band = max(bottom - top, 1.0)

    bins = 200
    occupied = [0.0] * bins

    def index(x: float) -> int:
        return min(bins - 1, max(0, int(x / width * bins)))

    for word in body:
        for slot in range(index(word.xmin), index(word.xmax) + 1):
            occupied[slot] = max(occupied[slot], 1.0)

    # A gutter is empty in x; require it to be tall too, which is checked by
    # confirming no word crosses it anywhere in the band.
    gutters: list[tuple[int, int]] = []
    run_start = None
    for slot in range(bins):
        if occupied[slot] == 0.0:
            run_start = slot if run_start is None else run_start
        elif run_start is not None:
            gutters.append((run_start, slot - 1))
            run_start = None
    if run_start is not None:
        gutters.append((run_start, bins - 1))

    minimum_slots = max(1, int(MIN_GUTTER_WIDTH_RATIO * bins))
    interior = [(a, b) for a, b in gutters
                if b - a + 1 >= minimum_slots and a > 2 and b < bins - 3]
    if not interior:
        return [(0.0, width)], 0.0

    tall: list[tuple[int, int]] = []
    for a, b in interior:
        left = a / bins * width
        right = (b + 1) / bins * width
        crossing = [w for w in body if w.xmin < right and w.xmax > left]
        if crossing:
            continue
        covered = [w for w in body
                   if w.xmax <= left or w.xmin >= right]
        if not covered:
            continue
        span = max(w.ymax for w in covered) - min(w.ymin for w in covered)
        if span / band >= MIN_GUTTER_HEIGHT_RATIO:
            tall.append((a, b))
    if not tall:
        return [(0.0, width)], 0.0

    bounds: list[tuple[float, float]] = []
    cursor = 0.0
    for a, b in tall:
        bounds.append((cursor, a / bins * width))
        cursor = (b + 1) / bins * width
    bounds.append((cursor, width))
    widest_gutter = max((b - a + 1) / bins for a, b in tall)
    return bounds, round(min(1.0, widest_gutter / MIN_GUTTER_WIDTH_RATIO / 8), 4)


def _visual_lines(words: Sequence[WordBox], height: float) -> list[list[WordBox]]:
    tolerance = max(LINE_TOLERANCE_RATIO * height, 1.0)
    lines: list[list[WordBox]] = []
    for word in sorted(words, key=lambda w: (w.ycentre, w.xmin)):
        for line in lines:
            if abs(line[0].ycentre - word.ycentre) <= tolerance:
                line.append(word)
                break
        else:
            lines.append([word])
    return lines


def assess_page(page_number: int, width: float, height: float,
                words: Sequence[WordBox], document_id: str) -> PageReadingOrder:
    bounds, gutter_confidence = detect_columns(words, width, height)
    column_count = len(bounds)

    def column_of(word: WordBox) -> int:
        for position, (left, right) in enumerate(bounds):
            if left <= word.xcentre < right:
                return position
        return column_count - 1

    assignments = tuple(column_of(word) for word in words)
    lines = _visual_lines(words, height)
    cross = sum(1 for line in lines
                if len({column_of(word) for word in line}) > 1)
    line_fraction = cross / max(len(lines), 1)

    # Column-major reading order: each column top-to-bottom, columns left-to-right.
    ordered: list[str] = []
    for position in range(column_count):
        column_words = [w for w in words if column_of(w) == position]
        for line in _visual_lines(column_words, height):
            ordered.append(" ".join(w.text for w in
                                    sorted(line, key=lambda w: w.xmin)))
    flat = "\n".join(" ".join(w.text for w in sorted(line, key=lambda w: w.xmin))
                     for line in lines)

    header = "HEADER_PRESENT" if any(w.ycentre < 0.07 * height for w in words) \
        else "NO_HEADER"
    footer = "FOOTER_PRESENT" if any(w.ycentre > 0.94 * height for w in words) \
        else "NO_FOOTER"
    masthead = "MASTHEAD_CANDIDATE" if page_number == 1 and any(
        w.ycentre < 0.25 * height and w.text.isupper() and len(w.text) > 3
        for w in words) else "NO_MASTHEAD"

    if column_count == 1:
        state = "READING_ORDER_ESTABLISHED"
        evidence = "no structural gutter; single-column reading order"
        assignment_confidence = 0.95
    elif line_fraction >= INTERLEAVING_LINE_FRACTION:
        # The naive line-major extraction merges the columns.  Column-major
        # reconstruction is available, so this is recoverable — but the flat
        # text must never be used.
        state = "READING_ORDER_RECOVERABLE"
        evidence = (f"{column_count} columns; {cross} of {len(lines)} visual "
                    "lines span more than one column, so line-major extraction "
                    "interleaves them")
        assignment_confidence = round(max(0.5, 1.0 - line_fraction / 2), 4)
    else:
        state = "READING_ORDER_ESTABLISHED"
        evidence = (f"{column_count} columns; lines do not cross them")
        assignment_confidence = 0.9

    return PageReadingOrder(
        stable_id("v5-8-1-page", document_id, str(page_number)), page_number,
        width, height, column_count, tuple(bounds), assignments, len(lines),
        cross, assignment_confidence,
        round(1.0 - line_fraction, 4), round(1.0 - line_fraction, 4),
        header, footer, masthead, "\n".join(ordered), flat, state, evidence)


def assess(pdf_bytes: bytes, *, document_id: str = "",
           manifestation_id: str = "") -> DocumentReadingOrderAssessment:
    """§8 — assess one manifestation's reading order from its geometry."""
    try:
        pages_raw = word_boxes(pdf_bytes)
    except ValueError as error:
        return DocumentReadingOrderAssessment(
            stable_id("v5-8-1-layout", document_id), document_id,
            manifestation_id, (), 0, 0.0, 0.0, "PACKET_CONSTRUCTION_DEFECT",
            (f"layout could not be read: {error}",),
            sha256([document_id]), now_utc())

    pages = tuple(assess_page(number, width, height, words, document_id)
                  for number, (width, height, words)
                  in enumerate(pages_raw, start=1) if words)
    if not pages:
        return DocumentReadingOrderAssessment(
            stable_id("v5-8-1-layout", document_id), document_id,
            manifestation_id, (), 0, 0.0, 0.0, "PACKET_CONSTRUCTION_DEFECT",
            ("no words with geometry; the manifestation may be scanned",),
            sha256([document_id]), now_utc())

    confidence = round(sum(p.column_assignment_confidence for p in pages)
                       / len(pages), 4)
    columns = max(p.detected_column_count for p in pages)
    repairs: list[str] = []
    if any(p.state == "READING_ORDER_RECOVERABLE" for p in pages):
        repairs.append("COLUMN_MAJOR_RECONSTRUCTION_APPLIED")
    continuity = round(sum(p.block_continuity_confidence for p in pages)
                       / len(pages), 4)

    if confidence < MIN_PACKET_CONFIDENCE:
        state = "LAYOUT_RECONSTRUCTION_REQUIRED"
    elif repairs:
        state = "READING_ORDER_RECOVERABLE"
    else:
        state = "READING_ORDER_ESTABLISHED"

    return DocumentReadingOrderAssessment(
        stable_id("v5-8-1-layout", document_id), document_id, manifestation_id,
        pages, columns, confidence, continuity, state, tuple(repairs),
        sha256([document_id, str(len(pages))]), now_utc())


__all__ = [
    "READING_ORDER_STATES", "MIN_PACKET_CONFIDENCE",
    "INTERLEAVING_LINE_FRACTION", "WordBox", "PageReadingOrder",
    "DocumentReadingOrderAssessment", "word_boxes", "detect_columns",
    "assess_page", "assess",
]
