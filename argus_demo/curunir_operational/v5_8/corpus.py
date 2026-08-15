"""V5.8 §8 — corpus normalization, as a production mechanism rather than a script.

Two of the five repairs that invalidated the V5.7 freeze were text-normalisation
bugs in a scratchpad corpus builder.  A mechanism that a clean run depends on
cannot live in a script that is edited after the freeze, so it lives here, is
hashed, and is frozen with everything else.

The rule both bugs violated is the same one: **normalise once**.  A span
extracted under one normalisation and matched against text under another cannot
be found in its own document, and the keyed context join then correctly refuses
it — which is what silently halved the V5.7 corpus.
"""

from __future__ import annotations

import html
import re
import unicodedata
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

#: Characters that ``str.splitlines()`` treats as line breaks but a JSONL reader
#: splitting on "\n" does not.  One of these inside a span truncates the record
#: when it is read back — the first V5.7 post-freeze bug.
JSONL_UNSAFE = re.compile(r"[  \r\x00-\x08\x0b\x0c\x0e-\x1f]")

_SCRIPT_STYLE = re.compile(r"<(script|style|noscript|template)[^>]*>.*?</\1>",
                           re.S | re.I)
#: Block-level markup whose boundary is a real semantic boundary.  Collapsing
#: these into spaces is how a heading merges into the paragraph beneath it.
_BLOCK = re.compile(r"</?(p|div|br|li|tr|td|th|h[1-6]|section|article|blockquote|"
                    r"figcaption|dt|dd)\b[^>]*>", re.I)
_MARKUP = re.compile(r"<[^>]+>")
_SPACES = re.compile(r"[^\S\n]+")
_BLANKS = re.compile(r"\n{2,}")


def normalise_text(raw: bytes | str) -> str:
    """The one normalisation.  Everything downstream uses this and only this.

    Block boundaries survive as newlines — §8.2 requires paragraph, heading,
    list-item, row and quotation boundaries to be preserved — while inline
    markup and runs of spaces collapse.
    """
    body = raw.decode("utf-8", "ignore") if isinstance(raw, bytes) else raw
    body = _SCRIPT_STYLE.sub(" ", body)
    body = _BLOCK.sub("\n", body)
    body = _MARKUP.sub(" ", body)
    body = html.unescape(body)
    body = JSONL_UNSAFE.sub(" ", body)
    body = unicodedata.normalize("NFC", body)
    body = _SPACES.sub(" ", body)
    return _BLANKS.sub("\n\n", body).strip()


def normalise_span(text: str) -> str:
    """A span, normalised the SAME way, so it can be found in its own document.

    A span never spans a block boundary, so its newlines become spaces — but
    that substitution happens here and is mirrored by :func:`locate`, rather
    than being applied on one side only.
    """
    return _SPACES.sub(" ", JSONL_UNSAFE.sub(
        " ", unicodedata.normalize("NFC", text or ""))).replace("\n", " ").strip()


def locate(span: str, document: str) -> int:
    """Where the span sits in its document, under one shared normalisation."""
    return normalise_span(document).find(normalise_span(span))


def local_context(span: str, document: str, window: int = 600) -> str:
    """The text actually surrounding the span, or an honest empty string."""
    flat = normalise_span(document)
    needle = normalise_span(span)
    index = flat.find(needle)
    if index < 0:
        return ""
    return flat[max(0, index - window): index + len(needle) + window]


#: §8.2 — structure worth keeping out of a proposition.
_CHROME = re.compile(
    r"(cookie|newsletter|subscribe|sign in|log ?in|privacy policy|skip to|"
    r"all rights reserved|follow us|share this|accept all|javascript|"
    r"terms of use|site ?map)", re.I)
_SENTENCE = re.compile(r"(?<=[.!?])\s+(?=[A-ZÄÖÜÉÈÀÁ0-9€$£])")

MIN_SENTENCE, MAX_SENTENCE = 60, 400
MIN_ALPHA_RATIO = 0.55


def sentences(document: str) -> list[str]:
    """Candidate propositions, taken from the normalised document.

    Block boundaries are sentence boundaries too: a heading and the paragraph
    under it are not one sentence merely because no full stop separates them.
    """
    out: list[str] = []
    for block in normalise_text(document).split("\n"):
        for part in _SENTENCE.split(block):
            part = normalise_span(part)
            if not (MIN_SENTENCE <= len(part) <= MAX_SENTENCE):
                continue
            if _CHROME.search(part) or " " not in part:
                continue
            if sum(c.isalpha() for c in part) / len(part) < MIN_ALPHA_RATIO:
                continue
            out.append(part)
    return out


#: §15 — what a captured page actually is.
PAGE_CLASSES: tuple[str, ...] = (
    "SUBSTANTIVE_EVIDENCE", "DISCOVERY_OR_NAVIGATION_ARTIFACT",
    "MANIFESTATION_METADATA", "ACCESS_FAILURE", "DUPLICATE",
)

_DISCOVERY_URL = re.compile(
    r"(/news/?$|/newsroom|/blog/?$|/press/?$|/actualites|/nyheter|/notizie|"
    r"/wiadomosci|/publicaties|/recent|/latest|/index|/hub/|/home/?$|/search|"
    r"/tag/|/category/|/feed|/rss|/archive/?$|/list/|/browse)", re.I)


def classify_page(*, url: str, text: str, sentence_count: int,
                  duplicate_of: str | None = None) -> str:
    """§15 — a listing page is not a substantive source because its URL differs."""
    if duplicate_of:
        return "DUPLICATE"
    if not text.strip():
        return "ACCESS_FAILURE"
    if _DISCOVERY_URL.search(url) and sentence_count < 5:
        return "DISCOVERY_OR_NAVIGATION_ARTIFACT"
    if sentence_count == 0:
        return "MANIFESTATION_METADATA"
    if _DISCOVERY_URL.search(url) and sentence_count < 12:
        # A listing page with some prose is still a listing page unless it
        # carries the material being adjudicated.
        return "DISCOVERY_OR_NAVIGATION_ARTIFACT"
    return "SUBSTANTIVE_EVIDENCE"


def audit_normalisation(pairs: Iterable[tuple[str, str]]) -> dict[str, Any]:
    """Every span must be findable in the document it was taken from."""
    pairs = list(pairs)
    unlocatable = [(s[:60], d[:60]) for s, d in pairs if locate(s, d) < 0]
    return {"pairs": len(pairs), "unlocatable_spans": len(unlocatable),
            "examples": unlocatable[:5],
            "rule": "one normalisation, applied to both sides of every match",
            "verdict": "PASS" if not unlocatable else "FAIL"}


__all__ = [
    "JSONL_UNSAFE", "normalise_text", "normalise_span", "locate", "local_context",
    "sentences", "PAGE_CLASSES", "classify_page", "audit_normalisation",
    "MIN_SENTENCE", "MAX_SENTENCE", "MIN_ALPHA_RATIO",
]
