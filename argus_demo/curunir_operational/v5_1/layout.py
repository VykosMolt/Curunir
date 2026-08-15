"""Document layout hardening for V5.1 (contract Section 12.3).

Pure-function layout annotation over normalized text plus page offsets — the
shape produced by v4 custody ``normalize_source``.  Closes the V5 mechanism
PDF_COLUMN_AND_FRAGMENT_QUARANTINE: page furniture (repeated headers/footers,
page numbers, decoration), column-fragment suspects, hyphenation breaks and
cross-page sentences are classified so layout noise can never silently become
an extraction candidate.  The text is never mutated; hyphenation repairs are
exposed as spans over the original offsets (immutable derivative discipline).

When evidence is sufficient but interpretation fails (empty text layer,
unreliable reading order) the module emits SYSTEM_CAPABILITY_FAILURE via
``models.capability_outcome`` instead of degrading to an unknown state.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from ..v4.models import require_aware, require_hash
from .models import (
    ADMISSION_STAGES, CANDIDATE_FUNCTIONS, CapabilityOutcome, Record,
    capability_outcome, now_utc, sha256, stable_id,
)

LAYOUT_ANNOTATOR_VERSION = "curunir-layout-annotator-v5.1"

# Region vocabulary is layout-specific; models.py holds no layout ontology.
LAYOUT_REGION_CLASSES = frozenset({
    "BODY_TEXT", "REPEATED_HEADER", "REPEATED_FOOTER", "PAGE_NUMBER",
    "TABLE_LIKE", "CAPTION_LIKE", "FOOTNOTE_LIKE", "COLUMN_FRAGMENT_SUSPECT",
    "DECORATIVE",
})

READING_ORDER_CONFIDENCE = ("EXACT", "HIGH", "UNCERTAIN")

_SHORT_LINE_LIMIT = 45
_FRAGMENT_RUN_MINIMUM = 6
_FRAGMENT_DENSITY_LIMIT = 0.15
_REPEAT_MINIMUM_PAGES = 3
_BOUNDARY_LINE_COUNT = 2
_FOOTNOTE_TAIL_LINES = 4
_PAGE_NUMBER_MAXIMUM_LENGTH = 24
_EXACT_ORDER_MEDIA = frozenset({"text/plain", "application/json"})

_ROMAN_NUMERAL = r"(?=[MDCLXVI])M{0,4}(?:CM|CD|D?C{0,3})(?:XC|XL|L?X{0,3})(?:IX|IV|V?I{0,3})"
_PAGE_WORD = r"(?:page|seite|p[áa]gina|strona|str|стр|blz|s|p)"
_PAGE_LINKER = r"(?:of|de|von|di|sur|van|z|из|/)"
_PAGE_NUMBER_PATTERNS = tuple(re.compile(pattern, re.IGNORECASE) for pattern in (
    r"[-–—\s.]*\d{1,4}[-–—\s.]*",
    _ROMAN_NUMERAL,
    rf"{_PAGE_WORD}\.?\s*\d{{1,4}}(?:\s*{_PAGE_LINKER}\s*\d{{1,4}})?\s*\.?",
    r"\d{1,4}\s*/\s*\d{1,4}",
))
_DECORATIVE_LINE = re.compile(r"[\W_]{3,}")
_TABLE_GAP = re.compile(r"\S[ \t]{3,}(?=\S)")
_CAPTION_LINE = re.compile(
    r"(?:abbildung|abb|tabelle|tableau|tabela|tabla|table|tab|figure|figura|fig"
    r"|rysunek|rys|obr)\.?\s*\d+\b.*", re.IGNORECASE)
_FOOTNOTE_LEAD = re.compile(r"(?:\*|†|‡|\d{1,3}[.)]|\[\d{1,3}\])\s+\S.*")
_HYPHEN_BREAK = re.compile(r"([^\W\d_]{2,})-(?:\n\f?|\f)([^\W\d_]{2,})")
_TERMINAL_PUNCTUATION = ".!?…"
_CLAUSE_PUNCTUATION = ".!?…:;"
_TRAILING_CLOSERS = "\"'”’»«)]"
_CONTINUATION_LEADS = ",;:)]»&–—-"
_FURNITURE = frozenset({
    "PAGE_NUMBER", "REPEATED_HEADER", "REPEATED_FOOTER", "DECORATIVE", "FOOTNOTE_LIKE",
})


@dataclass(frozen=True)
class LayoutAnnotation(Record):
    """Classified layout regions over one immutable normalized text."""

    annotation_id: str
    text_hash: str
    media_type: str
    parser: str
    annotator_version: str
    regions: tuple[tuple[int, int, str], ...]
    reading_order_confidence: str
    repair_spans: tuple[tuple[int, str], ...]
    cross_page_joins: tuple[tuple[int, int], ...]
    fragment_fraction: float
    page_count: int
    warnings: tuple[str, ...]
    generated_time: str

    def __post_init__(self) -> None:
        require_hash(self.text_hash); require_aware(self.generated_time)
        if self.reading_order_confidence not in READING_ORDER_CONFIDENCE:
            raise ValueError(f"unknown reading-order confidence: {self.reading_order_confidence}")
        if not 0.0 <= self.fragment_fraction <= 1.0:
            raise ValueError("fragment fraction must lie in [0, 1]")
        if self.page_count < 0:
            raise ValueError("page count cannot be negative")
        cursor = 0
        for start, end, region_class in self.regions:
            if region_class not in LAYOUT_REGION_CLASSES:
                raise ValueError(f"unknown layout region class: {region_class}")
            if start < cursor or end <= start:
                raise ValueError("layout regions must be ordered, non-overlapping and non-empty")
            cursor = end
        previous_position = -1
        for position, joined_word in self.repair_spans:
            if position <= previous_position:
                raise ValueError("hyphenation repairs must be ordered by position")
            if not joined_word or not joined_word.isalpha():
                raise ValueError("hyphenation repair must join a single alphabetic word")
            previous_position = position
        for left_page, right_page in self.cross_page_joins:
            if left_page >= right_page:
                raise ValueError("cross-page join must run forward across pages")
        # Excess fragment density is an interpretation failure and may never
        # present itself with a confident reading order.
        if self.fragment_fraction > _FRAGMENT_DENSITY_LIMIT and self.reading_order_confidence != "UNCERTAIN":
            raise ValueError("excess column-fragment density requires UNCERTAIN reading order")


@dataclass(frozen=True)
class QuarantineDecision(Record):
    decision_id: str
    region_class: str
    admission_stage: str
    candidate_function: str
    pending_context_expansion: bool
    rationale: str

    def __post_init__(self) -> None:
        if self.region_class not in LAYOUT_REGION_CLASSES:
            raise ValueError(f"unknown layout region class: {self.region_class}")
        if self.admission_stage not in ADMISSION_STAGES:
            raise ValueError(f"unknown admission stage: {self.admission_stage}")
        if self.candidate_function not in CANDIDATE_FUNCTIONS:
            raise ValueError(f"unknown candidate function: {self.candidate_function}")
        if not self.rationale.strip():
            raise ValueError("quarantine decision requires a rationale")
        if self.admission_stage == "QUARANTINED" and self.candidate_function == "ANALYTICALLY_MATERIAL":
            raise ValueError("quarantined layout regions cannot be analytically material")
        if self.pending_context_expansion != (self.region_class == "COLUMN_FRAGMENT_SUSPECT"):
            raise ValueError("context expansion is pending exactly for column-fragment suspects")


@dataclass(frozen=True)
class OcrDisclosure(Record):
    """Honest OCR_REQUIRED labeling for an empty text layer (Section 12.3).

    Actual OCR is out of scope; the disclosure therefore always carries a
    SYSTEM_CAPABILITY_FAILURE with class PARSER_INCAPABILITY.
    """

    disclosure_id: str
    document_id: str
    source_object_id: str
    parser: str
    marker: str
    capability_failure: CapabilityOutcome
    recorded_time: str

    def __post_init__(self) -> None:
        require_aware(self.recorded_time)
        if self.marker != "OCR_REQUIRED":
            raise ValueError("OCR disclosure marker must be OCR_REQUIRED")
        if (self.capability_failure.outcome != "SYSTEM_CAPABILITY_FAILURE"
                or self.capability_failure.capability_failure_class != "PARSER_INCAPABILITY"):
            raise ValueError("OCR disclosure must carry a PARSER_INCAPABILITY system failure")


def _line_spans(text: str) -> list[tuple[int, int, str]]:
    # Form feeds are page separators in pdftotext-shaped text and must break
    # lines exactly like newlines; both separators are one character wide.
    lines: list[tuple[int, int, str]] = []
    cursor = 0
    for raw in re.split(r"[\n\f]", text):
        stripped = raw.strip()
        left = cursor + (len(raw) - len(raw.lstrip()))
        lines.append((left, left + len(stripped), stripped))
        cursor += len(raw) + 1
    return lines


def _normalized_key(stripped: str) -> str:
    return re.sub(r"\s+", " ", stripped).casefold()


def _is_page_number_line(stripped: str) -> bool:
    if not stripped or len(stripped) > _PAGE_NUMBER_MAXIMUM_LENGTH:
        return False
    return any(pattern.fullmatch(stripped) for pattern in _PAGE_NUMBER_PATTERNS)


def _ends_clause_open(stripped: str, terminal: str) -> bool:
    trimmed = stripped.rstrip(_TRAILING_CLOSERS)
    return not trimmed or trimmed[-1] not in terminal


def _continuation_start(stripped: str) -> bool:
    head = stripped[0]
    return head.islower() or head.isdigit() or head in _CONTINUATION_LEADS


def _first_alpha_lower(stripped: str) -> bool:
    for character in stripped:
        if character.isalpha():
            return character.islower()
    return False


def _boundary_split(indices: list[int]) -> tuple[list[int], list[int]]:
    # Head and tail candidates never overlap, even on one/two-line pages.
    half = (len(indices) + 1) // 2
    head = indices[:min(_BOUNDARY_LINE_COUNT, half)]
    tail = indices[max(len(indices) - _BOUNDARY_LINE_COUNT, half):]
    return head, tail


def _validated_pages(text: str, pages: tuple) -> tuple[tuple[int, int, int], ...]:
    previous_end = 0
    previous_number = 0
    for entry in pages:
        if len(entry) != 3 or not all(isinstance(value, int) for value in entry):
            raise ValueError("page offsets must be (page_number, start, end) integer triples")
        number, start, end = entry
        if number <= previous_number:
            raise ValueError("page numbers must strictly increase")
        if start < previous_end or end < start or end > len(text):
            raise ValueError("page offsets must be ordered, non-overlapping and within text bounds")
        previous_number, previous_end = number, end
    if pages:
        return tuple((number, start, end) for number, start, end in pages)
    return ((1, 0, len(text)),) if text.strip() else ()


def _page_index_for(position: int, pages: tuple[tuple[int, int, int], ...]) -> tuple[int, int]:
    best_index = 0
    best_distance: int | None = None
    for index, (_, start, end) in enumerate(pages):
        if start <= position < end:
            return index, 0
        distance = start - position if position < start else position - end + 1
        if best_distance is None or distance < best_distance:
            best_index, best_distance = index, distance
    return best_index, best_distance or 0


def analyze_layout(text: str, pages: tuple, media_type: str, parser: str) -> LayoutAnnotation:
    if not isinstance(text, str):
        raise ValueError("layout analysis requires normalized text")
    if not str(media_type).strip() or not str(parser).strip():
        raise ValueError("layout analysis requires media type and parser identity")
    effective_pages = _validated_pages(text, tuple(pages))
    text_hash = sha256(text)
    annotation_id = stable_id("layout-annotation", text_hash, media_type, parser)
    # Zero-width characters survive str.strip(); a layer of only such
    # characters is still an empty text layer, not body text.
    visible = re.sub("[\\u200b\\u200c\\u200d\\u2060\\ufeff]", "", text)
    if not visible.strip():
        return LayoutAnnotation(
            annotation_id, text_hash, str(media_type), str(parser), LAYOUT_ANNOTATOR_VERSION,
            (), "UNCERTAIN", (), (), 0.0, len(effective_pages), ("EMPTY_TEXT_LAYER",), now_utc())

    warnings: list[str] = []
    lines = _line_spans(text)
    content = [index for index, (_, _, stripped) in enumerate(lines) if stripped]
    page_lines: list[list[int]] = [[] for _ in effective_pages]
    gap_attached = False
    for index in content:
        page_index, distance = _page_index_for(lines[index][0], effective_pages)
        if distance:
            gap_attached = True
        page_lines[page_index].append(index)
    if gap_attached:
        warnings.append("PAGE_MAP_GAP_LINE_ATTACHED_TO_NEAREST_PAGE")

    classes: dict[int, str] = {}

    for indices in page_lines:
        head, tail = _boundary_split(indices)
        for index in head + tail:
            if index not in classes and _is_page_number_line(lines[index][2]):
                classes[index] = "PAGE_NUMBER"

    page_count = len(effective_pages)
    share_threshold = max(2, (3 * page_count + 4) // 5)  # ceil(60%) without float ceil

    def _mark_repeats(pick_head: bool, region_class: str) -> None:
        occurrences: dict[str, list[int]] = {}
        pages_seen: dict[str, set[int]] = {}
        for page_index, indices in enumerate(page_lines):
            head, tail = _boundary_split(indices)
            for index in (head if pick_head else tail):
                if classes.get(index) == "PAGE_NUMBER":
                    continue
                key = _normalized_key(lines[index][2])
                occurrences.setdefault(key, []).append(index)
                pages_seen.setdefault(key, set()).add(page_index)
        for key, seen in pages_seen.items():
            count = len(seen)
            if count >= _REPEAT_MINIMUM_PAGES or (page_count >= 2 and count >= share_threshold):
                for index in occurrences[key]:
                    classes.setdefault(index, region_class)

    _mark_repeats(True, "REPEATED_HEADER")
    _mark_repeats(False, "REPEATED_FOOTER")

    for index in content:
        if index in classes:
            continue
        stripped = lines[index][2]
        if len(stripped) >= 3 and _DECORATIVE_LINE.fullmatch(stripped):
            classes[index] = "DECORATIVE"
        elif _CAPTION_LINE.fullmatch(stripped):
            classes[index] = "CAPTION_LIKE"
        elif len(_TABLE_GAP.findall(stripped)) >= 2 or stripped.count("\t") >= 2:
            classes[index] = "TABLE_LIKE"

    for indices in page_lines:
        for index in indices[-_FOOTNOTE_TAIL_LINES:]:
            if index not in classes and _FOOTNOTE_LEAD.fullmatch(lines[index][2]):
                classes[index] = "FOOTNOTE_LIKE"

    def _pair_suspect(left: int, right: int) -> bool:
        left_text, right_text = lines[left][2], lines[right][2]
        if len(left_text) >= _SHORT_LINE_LIMIT or len(right_text) >= _SHORT_LINE_LIMIT:
            return False
        return _ends_clause_open(left_text, _CLAUSE_PUNCTUATION) and _continuation_start(right_text)

    fragment_lines: set[int] = set()
    for indices in page_lines:
        eligible = [index for index in indices if index not in classes]
        run: list[int] = []
        for index in eligible:
            if run and index == run[-1] + 1 and _pair_suspect(run[-1], index):
                run.append(index)
                continue
            if len(run) >= _FRAGMENT_RUN_MINIMUM:
                fragment_lines.update(run)
            run = [index]
        if len(run) >= _FRAGMENT_RUN_MINIMUM:
            fragment_lines.update(run)
    for index in fragment_lines:
        classes[index] = "COLUMN_FRAGMENT_SUSPECT"

    fragment_characters = sum(lines[index][1] - lines[index][0] for index in fragment_lines)
    fragment_fraction = round(fragment_characters / max(1, len(text)), 6)
    if fragment_fraction > _FRAGMENT_DENSITY_LIMIT:
        confidence = "UNCERTAIN"
        warnings.extend(("COLUMN_FRAGMENT_DENSITY_EXCEEDS_LIMIT", "READING_ORDER_UNCERTAIN"))
    elif fragment_lines or str(media_type) not in _EXACT_ORDER_MEDIA:
        confidence = "HIGH"
    else:
        confidence = "EXACT"

    joins: list[tuple[int, int]] = []
    for left_index in range(len(effective_pages) - 1):
        tail = next((index for index in reversed(page_lines[left_index])
                     if classes.get(index, "BODY_TEXT") not in _FURNITURE), None)
        head = next((index for index in page_lines[left_index + 1]
                     if classes.get(index, "BODY_TEXT") not in _FURNITURE), None)
        if tail is None or head is None:
            continue
        if _ends_clause_open(lines[tail][2], _TERMINAL_PUNCTUATION) and _first_alpha_lower(lines[head][2]):
            joins.append((effective_pages[left_index][0], effective_pages[left_index + 1][0]))

    repairs = tuple((match.start(1), match.group(1) + match.group(2))
                    for match in _HYPHEN_BREAK.finditer(text) if match.group(2)[0].islower())

    regions: list[tuple[int, int, str]] = []
    for index in content:
        left, right, _ = lines[index]
        region_class = classes.get(index, "BODY_TEXT")
        if regions and regions[-1][2] == region_class:
            regions[-1] = (regions[-1][0], right, region_class)
        else:
            regions.append((left, right, region_class))

    return LayoutAnnotation(
        annotation_id, text_hash, str(media_type), str(parser), LAYOUT_ANNOTATOR_VERSION,
        tuple(regions), confidence, repairs, tuple(joins), fragment_fraction,
        page_count, tuple(dict.fromkeys(warnings)), now_utc())


def quarantine_decision(region_class: str) -> QuarantineDecision:
    if region_class not in LAYOUT_REGION_CLASSES:
        raise ValueError(f"unknown layout region class: {region_class}")
    if region_class in {"PAGE_NUMBER", "REPEATED_HEADER", "REPEATED_FOOTER"}:
        stage, function, pending = "QUARANTINED", "NAVIGATIONAL_METADATA", False
        rationale = "page furniture is navigational metadata, never evidential content"
    elif region_class == "DECORATIVE":
        stage, function, pending = "QUARANTINED", "DOCUMENT_STRUCTURE", False
        rationale = "decorative structure carries no propositional content"
    elif region_class == "COLUMN_FRAGMENT_SUSPECT":
        stage, function, pending = "QUARANTINED", "UNKNOWN_VALUE", True
        rationale = "layout-joined column fragments are quarantined pending context expansion"
    elif region_class in {"TABLE_LIKE", "CAPTION_LIKE", "FOOTNOTE_LIKE"}:
        stage, function, pending = "STRUCTURALLY_VALID", "SUPPORTING_DETAIL", False
        rationale = "structured detail admissible with layout-aware handling"
    else:
        stage, function, pending = "STRUCTURALLY_VALID", "ANALYTICALLY_MATERIAL", False
        rationale = "body text is admissible for candidate extraction"
    return QuarantineDecision(
        stable_id("layout-quarantine", region_class, stage, function),
        region_class, stage, function, pending, rationale)


def ocr_disclosure(record) -> OcrDisclosure:
    """Explicit OCR_REQUIRED marker for a normalization record without text."""
    if record.text.strip():
        raise ValueError("OCR disclosure only applies to an empty text layer")
    failure = capability_outcome(
        subject_kind="EXTRACTION_CANDIDATE", subject_id=record.document_id,
        outcome="SYSTEM_CAPABILITY_FAILURE",
        rationale=("source bytes are in custody but the parser produced no text layer; "
                   "OCR is required and unavailable, so extraction over this document is "
                   "a system capability failure, not evidence insufficiency"),
        capability_failure_class="PARSER_INCAPABILITY")
    return OcrDisclosure(
        stable_id("ocr-disclosure", record.document_id, record.source_object_id),
        record.document_id, record.source_object_id, record.parser,
        "OCR_REQUIRED", failure, now_utc())


def reading_order_failure(annotation: LayoutAnnotation, *, subject_id: str) -> CapabilityOutcome | None:
    """Mandatory capability failure for unreliable annotations.

    Covers both failure modes so a pipeline needs exactly one call: an empty
    text layer (OCR required, PARSER_INCAPABILITY) and unreliable reading
    order over present text (READING_ORDER_LOSS).
    """
    if "EMPTY_TEXT_LAYER" in annotation.warnings:
        return capability_outcome(
            subject_kind="EXTRACTION_CANDIDATE", subject_id=subject_id,
            outcome="SYSTEM_CAPABILITY_FAILURE",
            rationale=("source bytes are in custody but the parser produced no text layer; "
                       "OCR is required and unavailable, so extraction over this document "
                       "is a system capability failure, not evidence insufficiency"),
            capability_failure_class="PARSER_INCAPABILITY")
    if annotation.reading_order_confidence != "UNCERTAIN":
        return None
    return capability_outcome(
        subject_kind="EXTRACTION_CANDIDATE", subject_id=subject_id,
        outcome="SYSTEM_CAPABILITY_FAILURE",
        rationale=(f"text layer present (fragment fraction {annotation.fragment_fraction}) but the "
                   "reconstructed reading order is unreliable; this is an interpretation failure "
                   "of the system, not insufficiency of the underlying evidence"),
        capability_failure_class="READING_ORDER_LOSS")
