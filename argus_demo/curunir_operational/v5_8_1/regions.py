"""V5.8.1 §5 — D24: structural candidate origin, not a list of bad strings.

The V5.8.1 pilot put eighteen units of page furniture in front of reviewers:
autocomplete widget text, breadcrumbs, footers, menu labels, a database name
lifted from a navigation column.  Blacklisting those eighteen strings would
have fixed those eighteen strings.  The defect is that the corpus had no idea
where a sentence came from — every candidate was a substring of one flat text,
and a flat text cannot tell a menu from a paragraph.

So this module segments a document into regions *before* any sentence exists,
types each region by what it structurally is, and lets only proposition-bearing
regions reach semantic extraction.  The signals are structural and general:

  * the element ancestry the text sits in (nav, header, footer, aside, menu);
  * the fraction of the region's characters that live inside links;
  * whether the region repeats across other pages of the same source family;
  * whether it sits in a list of links or a table of content-bearing rows;
  * whether it is a heading, and whether the heading asserts anything;
  * whether it is assertive discourse with a meaning-bearing predicate.

None of those depend on the words being English, and none depend on having seen
the page before.  The repetition signal is the only corpus-level one, and it is
computed over declared source families rather than over the whole corpus, so a
sentence that legitimately recurs inside one work is not punished for it.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from html.parser import HTMLParser
from typing import Any

from ..v5_1.models import Record, now_utc, sha256, stable_id


class RegionViolation(RuntimeError):
    """A candidate reached semantic extraction without a typed origin."""


# ===========================================================================
# §5 — the origin vocabulary
# ===========================================================================

ORIGIN_CLASSES: tuple[str, ...] = (
    "PRIMARY_PROPOSITION_CONTENT", "TABLE_OR_STRUCTURED_PROPOSITION",
    "CAPTION_OR_FIGURE_PROPOSITION", "HEADING_CONTEXT_ONLY",
    "TABLE_OF_CONTENTS_ENTRY", "NAVIGATION_MENU", "BREADCRUMB",
    "HEADER_FURNITURE", "FOOTER_FURNITURE", "COOKIE_OR_CONSENT_TEXT",
    "LANGUAGE_SELECTOR", "SEARCH_CONTROL", "PAGINATION_CONTROL",
    "GENERIC_LINK_LABEL", "DOCUMENT_INDEX_ENTRY", "SITE_SLOGAN",
    "UNKNOWN_STRUCTURAL_REGION",
)

#: Only these may enter semantic extraction.  A heading is deliberately not
#: among them: a heading gives context to the propositions under it, and a
#: heading that does assert something reaches extraction as prose, through the
#: assertive-discourse test, not by being a heading.
PROPOSITION_BEARING: frozenset[str] = frozenset({
    "PRIMARY_PROPOSITION_CONTENT", "TABLE_OR_STRUCTURED_PROPOSITION",
    "CAPTION_OR_FIGURE_PROPOSITION",
})

#: Furniture proper: not evidence in any reading, and never repairable into it.
STRUCTURAL_FURNITURE: frozenset[str] = frozenset({
    "NAVIGATION_MENU", "BREADCRUMB", "HEADER_FURNITURE", "FOOTER_FURNITURE",
    "COOKIE_OR_CONSENT_TEXT", "LANGUAGE_SELECTOR", "SEARCH_CONTROL",
    "PAGINATION_CONTROL", "GENERIC_LINK_LABEL", "SITE_SLOGAN",
})

#: Real content that names a work rather than asserting about the world.  Kept
#: separate from furniture because a table-of-contents entry is a true fact
#: about the document, and losing that distinction is how "linked article
#: title" becomes "factual proposition asserted by this document".
REFERENTIAL_CONTENT: frozenset[str] = frozenset({
    "TABLE_OF_CONTENTS_ENTRY", "DOCUMENT_INDEX_ENTRY", "HEADING_CONTEXT_ONLY",
})

NAVIGATION_ELEMENTS: frozenset[str] = frozenset({
    "nav", "header", "footer", "aside", "menu", "menuitem", "form", "select",
    "option", "button", "search", "dialog",
})
BLOCK_ELEMENTS: frozenset[str] = frozenset({
    "p", "div", "li", "td", "th", "tr", "section", "article", "blockquote",
    "figcaption", "caption", "dd", "dt", "pre", "h1", "h2", "h3", "h4", "h5",
    "h6", "summary", "details", "main", "nav", "header", "footer", "aside",
    "label", "option", "button", "figure", "table",
})
HEADING_ELEMENTS: frozenset[str] = frozenset({"h1", "h2", "h3", "h4", "h5", "h6"})
CAPTION_ELEMENTS: frozenset[str] = frozenset({"figcaption", "caption"})
DROPPED_ELEMENTS: frozenset[str] = frozenset({
    "script", "style", "noscript", "template", "svg", "head", "iframe"})

#: Class and id substrings that name a region's role in the page.  These are
#: role names, not content: a site that calls its navigation "nav" is telling us
#: what the region is, in the same way the <nav> element does.
_ROLE_HINTS: tuple[tuple[str, str], ...] = (
    ("breadcrumb", "BREADCRUMB"), ("ariane", "BREADCRUMB"),
    ("cookie", "COOKIE_OR_CONSENT_TEXT"), ("consent", "COOKIE_OR_CONSENT_TEXT"),
    ("gdpr", "COOKIE_OR_CONSENT_TEXT"),
    ("lang-select", "LANGUAGE_SELECTOR"), ("language-select", "LANGUAGE_SELECTOR"),
    ("langnav", "LANGUAGE_SELECTOR"), ("locale", "LANGUAGE_SELECTOR"),
    ("pagination", "PAGINATION_CONTROL"), ("pager", "PAGINATION_CONTROL"),
    ("search", "SEARCH_CONTROL"), ("autocomplete", "SEARCH_CONTROL"),
    ("typeahead", "SEARCH_CONTROL"), ("combobox", "SEARCH_CONTROL"),
    ("tagline", "SITE_SLOGAN"), ("slogan", "SITE_SLOGAN"),
    ("toc", "TABLE_OF_CONTENTS_ENTRY"), ("table-of-contents", "TABLE_OF_CONTENTS_ENTRY"),
    ("inhaltsverzeichnis", "TABLE_OF_CONTENTS_ENTRY"),
    ("sommaire", "TABLE_OF_CONTENTS_ENTRY"),
    ("sitemap", "NAVIGATION_MENU"), ("menu", "NAVIGATION_MENU"),
    ("navbar", "NAVIGATION_MENU"), ("navigation", "NAVIGATION_MENU"),
    ("footer", "FOOTER_FURNITURE"), ("header", "HEADER_FURNITURE"),
    ("masthead", "HEADER_FURNITURE"), ("banner", "HEADER_FURNITURE"),
)

#: Headings that introduce a contents list.  Language-diverse because the
#: corpus is, and matched as a whole heading rather than as a substring of prose.
_CONTENTS_HEADING = re.compile(
    r"^\s*(table\s+of\s+contents|contents|index|inhalts(verzeichnis)?|"
    r"sommaire|table\s+des\s+mati[eè]res|[ií]ndice|indice|sumário|sumario|"
    r"содержание|оглавление|الفهرس|المحتويات)\s*[:.]?\s*$", re.IGNORECASE)

#: A control's own label, in the languages the corpus carries.  These are UI
#: verbs addressed to the reader, not assertions about the world.
_CONTROL_LABEL = re.compile(
    r"\b(skip to (main )?content|search|suchen|rechercher|buscar|поиск|بحث|"
    r"sign in|log ?in|register|subscribe|newsletter|share this|follow us|"
    r"accept (all )?cookies|manage (your )?preferences|change language|"
    r"select language|previous page|next page|back to top|print this page|"
    r"use up and down arrows|autocomplete results)\b", re.IGNORECASE)

_ALL_RIGHTS = re.compile(
    r"(all rights reserved|alle rechte vorbehalten|tous droits r[eé]serv[eé]s|"
    r"todos los derechos reservados|©\s*\d{4}|\(c\)\s*\d{4})", re.IGNORECASE)

_URL_LIKE = re.compile(r"https?://|www\.\w|\S+@\S+\.\w")
_BREADCRUMB_TRAIL = re.compile(r"(\s[>›»/|]\s|::)")

#: A meaning-bearing predicate.  Detected structurally: a finite verb in one of
#: the corpus languages, or — for languages this cannot reach — a clause-length
#: token sequence containing a copula or a verbal suffix pattern.  This is a
#: presence test, never a correctness test.
_FINITE_VERB = re.compile(
    r"\b(is|are|was|were|be|been|being|has|have|had|does|do|did|can|could|may|"
    r"might|shall|should|will|would|must|includes?|provides?|requires?|"
    r"states?|means?|applies|applied|covers?|contains?|consists?|remains?|"
    r"causes?|affects?|reports?|shows?|estimates?|kills?|spreads?|recovers?|"
    r"ist|sind|war|waren|wird|werden|wurde|wurden|hat|haben|hatte|kann|können|"
    r"muss|müssen|soll|sollen|gilt|gelten|dient|dienen|"
    r"est|sont|était|étaient|sera|seront|a|ont|avait|peut|peuvent|doit|"
    r"doivent|comprend|comprennent|s'applique|constitue|"
    r"es|son|era|eran|será|serán|tiene|tienen|puede|pueden|debe|deben|"
    r"incluye|incluyen|constituye|afecta|ataca|"
    r"является|являются|был|была|были|будет|имеет|имеют|может|могут|должен|"
    r"должны|составляет|необходимо|приходится)\b", re.IGNORECASE)
#: Arabic verbal/predicative markers.  Arabic sentences here are verbal or
#: nominal; both carry these particles or verb prefixes at clause head.
_ARABIC_PREDICATE = re.compile(
    r"(\bتشك[ّ]?ل|\bتعد\b|\bيعد\b|\bأعلن|\bتستدعي|\bيمكن|\bتوجد|\bلا يوجد|"
    r"\bهو\b|\bهي\b|\bكان\b|\bتشمل|\bيشمل|\bتؤدي|\bيؤدي)")

_SENTENCE_END = re.compile(r"[.!?。؟।]\s*$")

#: Words a title may carry in lower case without ceasing to be a title.
_TITLE_PARTICLES: frozenset[str] = frozenset({
    "of", "the", "and", "for", "on", "in", "to", "a", "an",
    "de", "la", "le", "les", "des", "du", "et", "pour", "sur",
    "und", "der", "die", "das", "den", "dem", "für", "zur", "zum",
    "y", "en", "para", "por", "el", "los", "las", "sobre",
})

#: How long a run of title-cased words may be before length alone says prose.
_MAX_TITLE_WORDS = 25


# ===========================================================================
# Segmentation
# ===========================================================================

@dataclass
class _Block:
    tag: str
    ancestry: tuple[str, ...]
    role_hints: tuple[str, ...]
    text_parts: list[str] = field(default_factory=list)
    link_chars: int = 0
    link_count: int = 0
    in_list: bool = False
    in_table: bool = False
    table_has_data_cells: bool = False
    order: int = 0

    @property
    def text(self) -> str:
        return " ".join(" ".join(self.text_parts).split())


class _Segmenter(HTMLParser):
    """Collects block-level regions with their ancestry and link density."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.blocks: list[_Block] = []
        #: (tag, hints) pairs.  Hints must be scoped to the element that
        #: declared them: a flat hint list makes the first <nav> on a page
        #: swallow every block after it, which is a worse defect than the one
        #: this module exists to fix.
        self._stack: list[tuple[str, tuple[str, ...]]] = []
        self._open: list[_Block] = []
        self._dropping = 0
        self._link_depth = 0
        self._order = 0
        self._table_cells = 0

    # -- helpers
    def _current(self) -> _Block | None:
        return self._open[-1] if self._open else None

    def _hints_from(self, attrs: Sequence[tuple[str, str | None]]) -> tuple[str, ...]:
        found: list[str] = []
        blob = " ".join(str(v or "") for k, v in attrs
                        if k in ("class", "id", "role", "aria-label", "data-testid"))
        folded = blob.casefold()
        for needle, origin in _ROLE_HINTS:
            if needle in folded and origin not in found:
                found.append(origin)
        return tuple(found)

    # -- parser interface
    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        if tag in DROPPED_ELEMENTS:
            self._dropping += 1
            return
        if self._dropping:
            return
        if tag == "a":
            self._link_depth += 1
            block = self._current()
            if block is not None:
                block.link_count += 1
            return
        hints = self._hints_from(attrs)
        ancestry = tuple(t for t, _ in self._stack)
        inherited = tuple(h for _, hs in self._stack for h in hs)
        if tag in BLOCK_ELEMENTS:
            self._order += 1
            block = _Block(tag=tag, ancestry=ancestry,
                           role_hints=inherited + hints,
                           in_list=any(t in ("li", "ul", "ol") for t in ancestry)
                                   or tag == "li",
                           in_table=any(t in ("table", "tr", "td", "th")
                                        for t in ancestry)
                                    or tag in ("td", "th", "tr", "table"),
                           order=self._order)
            self._open.append(block)
            self.blocks.append(block)
        self._stack.append((tag, hints))

    def handle_endtag(self, tag):
        tag = tag.lower()
        if tag in DROPPED_ELEMENTS:
            self._dropping = max(0, self._dropping - 1)
            return
        if self._dropping:
            return
        if tag == "a":
            self._link_depth = max(0, self._link_depth - 1)
            return
        if tag in BLOCK_ELEMENTS and self._open and self._open[-1].tag == tag:
            self._open.pop()
        for depth in range(len(self._stack) - 1, -1, -1):
            if self._stack[depth][0] == tag:
                del self._stack[depth:]
                break

    def handle_data(self, data):
        if self._dropping or not data.strip():
            return
        block = self._current()
        if block is None:
            self._order += 1
            block = _Block(tag="body", ancestry=(), role_hints=(), order=self._order)
            self._open.append(block)
            self.blocks.append(block)
        block.text_parts.append(data)
        if self._link_depth:
            block.link_chars += len(data.strip())


@dataclass(frozen=True)
class RegionRecord(Record):
    """One typed region of one document, and the evidence for its type."""

    region_id: str
    source_id: str
    source_family_id: str
    order: int
    content_region_type: str
    dom_or_layout_role: str
    navigation_ancestry: tuple[str, ...]
    link_density: float
    link_count: int
    repetition_across_pages: int
    heading_relation: str
    list_item_state: str
    table_of_contents_state: str
    content_provenance: str
    text: str
    deciding_signal: str
    recorded_time: str

    @property
    def proposition_bearing(self) -> bool:
        return self.content_region_type in PROPOSITION_BEARING


def _role_of(block: _Block) -> str:
    if block.tag in HEADING_ELEMENTS:
        return f"HEADING_{block.tag.upper()}"
    if block.tag in CAPTION_ELEMENTS:
        return "CAPTION"
    if block.tag in ("td", "th", "tr", "table"):
        return "TABLE_CELL"
    if block.tag == "li":
        return "LIST_ITEM"
    if block.tag in NAVIGATION_ELEMENTS:
        return block.tag.upper()
    return block.tag.upper()


def assertive_discourse(text: str) -> bool:
    """Does this text assert something, as opposed to labelling or commanding?"""
    stripped = text.strip()
    if not stripped or _CONTROL_LABEL.search(stripped):
        return False
    if stripped.endswith("?"):
        return False
    return predicate_present(stripped)


def predicate_present(text: str) -> bool:
    """A meaning-bearing predicate, tested for presence only."""
    if _FINITE_VERB.search(text) or _ARABIC_PREDICATE.search(text):
        return True
    # Languages the finite-verb list does not reach still show a predicate in a
    # clause of this length with sentence punctuation; a bare noun phrase does
    # not.  Deliberately permissive, because the origin type already carries
    # most of the weight and a false positive here still faces extraction.
    return bool(_SENTENCE_END.search(text) and len(text.split()) >= 8)


#: A subordinating or relative clause marker, in the corpus languages.  Its
#: presence means the span has clause structure even where the finite-verb list
#: does not reach the verb — participial and gerundive legal recitals, above all.
_CLAUSE_MARKER = re.compile(
    r"\b(that|which|who|where|when|because|whereas|dass|daß|welche[rs]?|"
    r"weil|da|indem|que|qui|dont|parce que|lorsque|quien|porque|cuando|"
    r"который|которая|которые|что|поскольку|الذي|التي|أن|لأن)\b", re.IGNORECASE)


#: Text-direction controls and the replacement character.  A document does not
#: contain these; an extractor emits them when it could not render the source.
#: On the Arabic governing-body PDFs they travel with letter-level corruption —
#: "الإدارة" arriving as "الدارة" — so a span carrying one is not the document's
#: wording and must not be quoted, bound or published as if it were.
_EXTRACTION_ARTEFACT = re.compile("[‪-‮⁦-⁩�]")


def extraction_fidelity(text: str) -> str | None:
    """§5.5 layer three: whether the span is what the manifestation says.

    V5.8.1 defect D28.  Every earlier layer asks what a span means; none asked
    whether the characters are the source's at all.  A mis-decoded span reached
    role binding and thirteen of them were bound ROLE_BINDING_ESTABLISHED, which
    is a wrong quotation presented with full confidence.
    """
    if _EXTRACTION_ARTEFACT.search(text):
        return ("extraction artefact: the span carries text-direction controls "
                "or replacement characters, so its wording is the extractor's, "
                "not the manifestation's")
    return None


def span_furniture_signal(text: str) -> str | None:
    """§5.5 layer two: furniture signals carried by the span itself.

    Independent of how the region was typed, so the two layers can disagree —
    which is the only thing that makes defence in depth more than a slogan.
    """
    stripped = text.strip()
    if _CONTROL_LABEL.search(stripped):
        return "control label addressed to the reader"
    if _ALL_RIGHTS.search(stripped):
        return "rights or copyright notice"
    if _BREADCRUMB_TRAIL.search(stripped) and not _SENTENCE_END.search(stripped):
        return "separator-delimited trail with no assertion"
    if _looks_like_a_title(stripped):
        return "title-cased run with no predicate"
    return None


def _clause_shaped(text: str) -> bool:
    """Clause-length content inside a region already typed proposition-bearing.

    Deliberately does not require a finite verb or a subordinator.  A legal
    recital ("Reafirmando su propósito de consolidar en este Continente …;")
    has neither, ends in a semicolon, and is unambiguously the operative content
    of the instrument.  Demanding a verb form the pattern happens to know is how
    a structural filter turns into an over-rejection defect.
    """
    return len(text.split()) >= 8 and span_furniture_signal(text) is None


def sentence_completeness(text: str) -> str:
    stripped = text.strip()
    if not stripped:
        return "EMPTY"
    if _SENTENCE_END.search(stripped) and stripped[0].isupper() or \
            _SENTENCE_END.search(stripped) and not stripped[0].isalpha():
        return "COMPLETE"
    if _SENTENCE_END.search(stripped):
        return "COMPLETE_LOWERCASE_OPENING"
    return "TRUNCATED_OR_FRAGMENT"


def _looks_like_a_title(text: str) -> bool:
    """A run of title-cased words with no sentence punctuation.

    Written as a linear scan rather than a regex.  The regex form —
    ``(?:[A-Z][\\w'-]*(?:\\s+(?:of|the|…)\\b)?\\s*){3,}`` — nests a quantified
    group inside a quantified group, and on a long non-matching paragraph the
    engine explores exponentially many splits.  It ran for over an hour against
    the development corpus before this replaced it.  Any pattern that can be a
    scan should be a scan.
    """
    stripped = text.strip()
    if not stripped or _SENTENCE_END.search(stripped):
        return False
    words = stripped.split()
    if not 3 <= len(words) <= _MAX_TITLE_WORDS:
        return False
    for word in words:
        if word.casefold() in _TITLE_PARTICLES:
            continue
        head = word.lstrip("(\"'«¡¿")
        if not head:
            return False
        if not (head[0].isupper() or head[0].isdigit()):
            return False
    return True


def classify_region(*, block: _Block, repetition: int,
                    after_contents_heading: bool) -> tuple[str, str]:
    """Type one region, returning the class and the signal that decided it."""
    text = block.text
    ancestry = set(block.ancestry) | {block.tag}
    hints = block.role_hints
    density = block.link_chars / max(len(text), 1)

    # 1. Declared role wins: a region that says it is a breadcrumb is one.
    for hint in hints:
        if hint in STRUCTURAL_FURNITURE or hint == "TABLE_OF_CONTENTS_ENTRY":
            return hint, f"declared structural role {hint.lower()}"

    # 2. Structural ancestry: nav/header/footer/aside/form are not prose.
    if ancestry & {"nav", "menu", "menuitem"}:
        return "NAVIGATION_MENU", "sits inside a navigation element"
    if ancestry & {"form", "select", "option", "button", "search", "label"}:
        return "SEARCH_CONTROL", "sits inside an interactive control"
    if "header" in ancestry:
        return "HEADER_FURNITURE", "sits inside the page header"
    if "footer" in ancestry:
        return "FOOTER_FURNITURE", "sits inside the page footer"
    if "aside" in ancestry:
        return "NAVIGATION_MENU", "sits inside an aside"

    # 3. Its own words say what it is.
    if _CONTROL_LABEL.search(text):
        return "SEARCH_CONTROL", "reads as a control label addressed to the reader"
    if _ALL_RIGHTS.search(text) and len(text.split()) < 40:
        return "FOOTER_FURNITURE", "copyright or rights notice"
    if _BREADCRUMB_TRAIL.search(text) and len(text.split()) < 30 \
            and not _SENTENCE_END.search(text):
        return "BREADCRUMB", "separator-delimited trail with no assertion"

    # 4. Repetition across pages of the same family: site furniture, whatever
    #    element it happens to sit in.
    if repetition >= 3 and len(text.split()) < 60:
        return "UNKNOWN_STRUCTURAL_REGION", (
            f"identical text on {repetition} pages of this source family")

    # 5. Link density: a paragraph is mostly prose; a menu is mostly links.
    if density >= 0.65 and block.link_count >= 2:
        return "NAVIGATION_MENU", f"link density {density:.2f} over {block.link_count} links"
    if density >= 0.9 and block.link_count == 1 and len(text.split()) < 25:
        return "GENERIC_LINK_LABEL", "the region is a single link and nothing else"

    # 6. Headings and contents.
    if block.tag in HEADING_ELEMENTS:
        if _CONTENTS_HEADING.match(text):
            return "TABLE_OF_CONTENTS_ENTRY", "heading introduces a contents list"
        return "HEADING_CONTEXT_ONLY", "heading element"
    if after_contents_heading and (block.in_list or density > 0.3):
        return "TABLE_OF_CONTENTS_ENTRY", "list entry under a contents heading"

    # 7. Tables: a data row is structured content, a link table is navigation.
    if block.in_table:
        if block.link_count and density >= 0.5:
            return "NAVIGATION_MENU", "table row is a link grid"
        if assertive_discourse(text) or re.search(r"\d", text) or _clause_shaped(text):
            return "TABLE_OR_STRUCTURED_PROPOSITION", "table cell carrying content"
        # An index entry is short by nature.  A thirty-word cell with no link
        # and no furniture signal is the document's own prose, laid out in a
        # table — which is how treaty preambles are published.
        return "DOCUMENT_INDEX_ENTRY", "table cell with no assertion"

    if block.tag in CAPTION_ELEMENTS:
        return "CAPTION_OR_FIGURE_PROPOSITION", "caption element"

    # 8. List items: an index entry names a work, a prose bullet asserts.
    if block.in_list and not (assertive_discourse(text) or _clause_shaped(text)):
        if _looks_like_a_title(text) or density > 0.2:
            return "DOCUMENT_INDEX_ENTRY", "list item naming a work rather than asserting"
        return "UNKNOWN_STRUCTURAL_REGION", "list item with no assertion"

    # 9. Prose.
    if assertive_discourse(text):
        return "PRIMARY_PROPOSITION_CONTENT", "assertive prose with a predicate"
    if _clause_shaped(text):
        return "PRIMARY_PROPOSITION_CONTENT", (
            "clause-length prose with no furniture signal; the predicate is in "
            "a form the finite-verb list does not reach")
    if _looks_like_a_title(text):
        return "DOCUMENT_INDEX_ENTRY", "title-cased run with no predicate"
    if len(text.split()) < 5:
        return "GENERIC_LINK_LABEL" if block.link_count else "UNKNOWN_STRUCTURAL_REGION", \
            "too short to carry a proposition"
    return "UNKNOWN_STRUCTURAL_REGION", "no assertive predicate and no declared role"


def segment(raw: bytes | str, *, source_id: str, source_family_id: str,
            repetition_index: Mapping[str, int] | None = None,
            container: str = "HTML") -> list[RegionRecord]:
    """Split one document into typed regions."""
    repetition_index = repetition_index or {}
    if container == "PDF":
        return _segment_flat(raw, source_id=source_id,
                             source_family_id=source_family_id,
                             repetition_index=repetition_index,
                             provenance="PDF_TEXT_OPERATORS")
    text = raw.decode("utf-8", "ignore") if isinstance(raw, bytes) else raw
    parser = _Segmenter()
    try:
        parser.feed(text)
        parser.close()
    except Exception:  # noqa: BLE001 — a malformed page still yields its blocks
        pass
    if not parser.blocks:
        return _segment_flat(text, source_id=source_id,
                             source_family_id=source_family_id,
                             repetition_index=repetition_index,
                             provenance="UNPARSEABLE_MARKUP")

    records: list[RegionRecord] = []
    contents_open = False
    for block in parser.blocks:
        body = block.text
        if not body:
            continue
        if block.tag in HEADING_ELEMENTS:
            contents_open = bool(_CONTENTS_HEADING.match(body))
        repetition = repetition_index.get(_fingerprint(source_family_id, body), 0)
        klass, signal = classify_region(block=block, repetition=repetition,
                                        after_contents_heading=contents_open)
        records.append(RegionRecord(
            stable_id("v5-8-1-region", source_id, str(block.order)),
            source_id, source_family_id, block.order, klass, _role_of(block),
            tuple(block.ancestry[-6:]),
            round(block.link_chars / max(len(body), 1), 4), block.link_count,
            repetition,
            "IS_HEADING" if block.tag in HEADING_ELEMENTS else "UNDER_HEADING",
            "LIST_ITEM" if block.in_list else "NOT_A_LIST_ITEM",
            "CONTENTS_REGION" if contents_open else "NOT_CONTENTS",
            "HTML_BLOCK_ELEMENT", body, signal, now_utc()))
    return records


def _segment_flat(raw: bytes | str, *, source_id: str, source_family_id: str,
                  repetition_index: Mapping[str, int], provenance: str
                  ) -> list[RegionRecord]:
    """A container with no element structure still gets typed regions.

    Without markup the only structural signals left are repetition across the
    family and whether the paragraph asserts anything, so those are the only
    ones claimed.  Nothing is typed PRIMARY on the strength of "we could not
    see any structure".
    """
    text = raw.decode("utf-8", "ignore") if isinstance(raw, bytes) else raw
    records: list[RegionRecord] = []
    for order, chunk in enumerate(p for p in re.split(r"\n{1,}", text) if p.strip()):
        body = " ".join(chunk.split())
        repetition = repetition_index.get(_fingerprint(source_family_id, body), 0)
        if repetition >= 3 and len(body.split()) < 60:
            klass, signal = "UNKNOWN_STRUCTURAL_REGION", (
                f"identical text on {repetition} pages of this source family")
        elif _CONTROL_LABEL.search(body):
            klass, signal = "SEARCH_CONTROL", "control label"
        elif _ALL_RIGHTS.search(body) and len(body.split()) < 40:
            klass, signal = "FOOTER_FURNITURE", "rights notice"
        elif _BREADCRUMB_TRAIL.search(body) and not _SENTENCE_END.search(body) \
                and len(body.split()) < 30:
            klass, signal = "BREADCRUMB", "separator-delimited trail"
        elif assertive_discourse(body):
            klass, signal = "PRIMARY_PROPOSITION_CONTENT", "assertive prose with a predicate"
        elif _looks_like_a_title(body):
            klass, signal = "DOCUMENT_INDEX_ENTRY", "title-cased run with no predicate"
        else:
            klass, signal = "UNKNOWN_STRUCTURAL_REGION", "no assertive predicate"
        records.append(RegionRecord(
            stable_id("v5-8-1-region", source_id, str(order)), source_id,
            source_family_id, order, klass, "FLAT_PARAGRAPH", (), 0.0, 0,
            repetition, "UNDER_HEADING", "NOT_A_LIST_ITEM", "NOT_CONTENTS",
            provenance, body, signal, now_utc()))
    return records


def fingerprint(source_family_id: str, text: str) -> str:
    """The key under which one block of text is counted across a family."""
    return sha256([source_family_id, " ".join(text.split()).casefold()[:160]])


_fingerprint = fingerprint


def retype(region: RegionRecord, *, repetition: int) -> RegionRecord:
    """Re-decide one region's class once its cross-page repetition is known.

    Repetition is a corpus-level signal, so it cannot be known during the first
    parse of the first document.  Retyping from the already-parsed region avoids
    parsing every document twice, and — more importantly — guarantees the second
    pass sees exactly the blocks the first pass saw.
    """
    if repetition == region.repetition_across_pages:
        return region
    if repetition >= 3 and len(region.text.split()) < 60 \
            and region.content_region_type in PROPOSITION_BEARING:
        return RegionRecord(
            region.region_id, region.source_id, region.source_family_id,
            region.order, "UNKNOWN_STRUCTURAL_REGION", region.dom_or_layout_role,
            region.navigation_ancestry, region.link_density, region.link_count,
            repetition, region.heading_relation, region.list_item_state,
            region.table_of_contents_state, region.content_provenance,
            region.text,
            f"identical text on {repetition} pages of this source family",
            region.recorded_time)
    return RegionRecord(
        region.region_id, region.source_id, region.source_family_id, region.order,
        region.content_region_type, region.dom_or_layout_role,
        region.navigation_ancestry, region.link_density, region.link_count,
        repetition, region.heading_relation, region.list_item_state,
        region.table_of_contents_state, region.content_provenance, region.text,
        region.deciding_signal, region.recorded_time)


def repetition_index(documents: Iterable[tuple[str, str, Iterable[str]]]
                     ) -> dict[str, int]:
    """How many distinct pages of one source family carry each block of text.

    Counted per family and per *page*, not per occurrence: a sentence repeated
    three times inside one document is one document's worth of evidence, and
    punishing it would delete legitimately repeated statutory language.
    """
    seen: dict[str, set[str]] = {}
    for source_id, family, blocks in documents:
        for body in blocks:
            key = _fingerprint(family, body)
            seen.setdefault(key, set()).add(source_id)
    return {key: len(pages) for key, pages in seen.items()}


# ===========================================================================
# §5.1 — admission
# ===========================================================================

@dataclass(frozen=True)
class AdmissionDecision(Record):
    """Whether one span from one region may reach semantic extraction."""

    decision_id: str
    region_id: str
    admitted: bool
    content_region_type: str
    refusal_reason: str | None
    referential_subject_state: str
    assertive_discourse_state: str
    predicate_presence: str
    sentence_completeness: str
    recorded_time: str


_DEFINITE_OPENER = re.compile(
    r"^\s*(this|that|these|those|it|they|he|she|such|the (?:former|latter)|"
    r"dies(?:e|er|es)?|jen(?:e|er|es)|er|sie|es|"
    r"ce|cet|cette|ces|il|elle|ils|elles|celui|celle|"
    r"este|esta|estos|estas|ello|ella|dicho|dicha|"
    r"это|эти|этот|эта|он|она|они)\b", re.IGNORECASE)


def admit(*, region: RegionRecord, span: str) -> AdmissionDecision:
    """§5.1 — the admission test, run before any semantic extraction."""
    assertive = assertive_discourse(span)
    predicate = predicate_present(span)
    completeness = sentence_completeness(span)
    if _DEFINITE_OPENER.match(span):
        subject_state = "IMPLICIT_RESOLVABLE_IN_CONTEXT"
    elif re.match(r"^\s*[a-zà-öø-ÿ]", span) and not span.strip().startswith("("):
        subject_state = "OPENS_MID_CLAUSE"
    else:
        subject_state = "EXPLICIT_OR_WELL_FORMED"

    reason: str | None = None
    if extraction_fidelity(span):
        # Runs before every semantic test: whether the span asserts anything is
        # not a question worth asking about characters the document never had.
        reason = extraction_fidelity(span)
    elif region.content_region_type in STRUCTURAL_FURNITURE:
        reason = (f"origin {region.content_region_type}: structural furniture is "
                  "not a proposition in any reading")
    elif region.content_region_type in REFERENTIAL_CONTENT:
        reason = (f"origin {region.content_region_type}: names a work or gives "
                  "context; it does not assert about the world")
    elif region.content_region_type == "UNKNOWN_STRUCTURAL_REGION":
        reason = ("origin could not be typed; an untyped region is refused "
                  "rather than assumed to be prose")
    elif not region.proposition_bearing:
        reason = f"origin {region.content_region_type} is not proposition-bearing"
    elif span_furniture_signal(span):
        # §5.5 layer two.  It runs unconditionally, not only when the span fails
        # the discourse test: a rights notice is a grammatical sentence, so a
        # check reached only by non-assertive spans would never see one.
        reason = f"span-level furniture signal: {span_furniture_signal(span)}"
    elif not (assertive or _clause_shaped(span)):
        # Inside a region already typed as proposition-bearing, the span test
        # exists to catch furniture that slipped past the region test — not to
        # re-adjudicate grammar.  Held to the stricter reading it refused legal
        # recitals whose only verbs are non-finite ("Reafirmando su propósito de
        # consolidar…"), which is the over-rejection D25 is about, introduced
        # one layer earlier.
        reason = "no assertive predicate and no clause structure"

    # A well-formed proposition whose subject is a pronoun resolvable from its
    # own context is admitted.  §5.1 is explicit that a named noun phrase is
    # not required, and refusing these was half of the D25 over-rejection.
    return AdmissionDecision(
        stable_id("v5-8-1-admission", region.region_id, span[:80]),
        region.region_id, reason is None, region.content_region_type, reason,
        subject_state, "ASSERTIVE" if assertive else "NON_ASSERTIVE",
        "PRESENT" if predicate else "ABSENT", completeness, now_utc())


def audit(decisions: Sequence[AdmissionDecision],
          regions: Sequence[RegionRecord]) -> dict[str, Any]:
    """§5.4 — what the filter did, over the whole population."""
    by_class: dict[str, int] = {}
    for region in regions:
        by_class[region.content_region_type] = by_class.get(
            region.content_region_type, 0) + 1
    admitted = [d for d in decisions if d.admitted]
    return {
        "regions": len(regions),
        "regions_by_class": dict(sorted(by_class.items())),
        "proposition_bearing_regions": sum(1 for r in regions if r.proposition_bearing),
        "structural_furniture_regions": sum(
            1 for r in regions if r.content_region_type in STRUCTURAL_FURNITURE),
        "referential_content_regions": sum(
            1 for r in regions if r.content_region_type in REFERENTIAL_CONTENT),
        "unknown_structural_regions": sum(
            1 for r in regions if r.content_region_type == "UNKNOWN_STRUCTURAL_REGION"),
        "candidates_considered": len(decisions),
        "candidates_admitted": len(admitted),
        "candidates_refused": len(decisions) - len(admitted),
        "refusal_reasons": {reason: sum(1 for d in decisions
                                        if d.refusal_reason == reason)
                            for reason in sorted({d.refusal_reason
                                                  for d in decisions
                                                  if d.refusal_reason})},
        "admitted_from_non_proposition_origin": sum(
            1 for d in admitted if d.content_region_type not in PROPOSITION_BEARING),
    }


__all__ = [
    "RegionViolation", "ORIGIN_CLASSES", "PROPOSITION_BEARING",
    "STRUCTURAL_FURNITURE", "REFERENTIAL_CONTENT", "RegionRecord",
    "AdmissionDecision", "segment", "classify_region", "admit", "audit",
    "repetition_index", "retype", "fingerprint", "assertive_discourse",
    "predicate_present", "extraction_fidelity", "span_furniture_signal",
    "sentence_completeness",
]
