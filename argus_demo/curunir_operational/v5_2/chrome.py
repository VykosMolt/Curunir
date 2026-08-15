"""Wrapper, chrome and boilerplate classification (contract Sections 9.4, 15).

V5.1 captured many sources through web archives.  The archive's own banner
("N captures", "The Wayback Machine - https://...", "About this capture"),
cookie notices, navigation menus and press-office contact blocks all entered
the normalized text layer, were never classified, and reached both the
extraction admission boundary and the report planner.  Six of the twelve
V5.1 report-faithfulness failures were exactly this text published as factual
propositions.

The module classifies chrome regions structurally, in five languages, and is
consumed by the semantic resolver (which quarantines rather than rejects) and
by the report planner (which refuses to publish chrome as a proposition).

Nothing here encodes a campaign, source or fixture answer: every pattern is a
generic web or document furniture pattern.

Research shadow only.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable

from ..v5_1.models import Record, stable_id

CHROME_CLASSES = (
    "ARCHIVAL_WRAPPER",     # web-archive capture banner and timeline
    "COOKIE_NOTICE",        # consent banners
    "NAVIGATION_CHROME",    # skip links, menus, "read more", breadcrumbs
    "CONTACT_BOILERPLATE",  # press-office / newsroom contact blocks
    "LEGAL_BOILERPLATE",    # copyright, disclaimer, terms footers
    "SOCIAL_SHARE",         # share/follow widgets
    "PRINT_OR_EXPORT",      # "print as PDF", "download", "export"
    "SUBSCRIPTION_PROMPT",  # newsletter / paywall prompts
)

_PATTERNS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("ARCHIVAL_WRAPPER", (
        r"\bthe\s+wayback\s+machine\b",
        r"\bweb\.archive\.org/web/\d{8,}",
        r"^\s*\d{1,4}\s+captures?\b",
        r"\babout\s+this\s+capture\b",
        r"\bcollected\s+by\b",
        r"\bcollection:\s",
        r"\barchive-it\b",
        r"\bsaved?\s+page\s+now\b",
        r"\bthis\s+snapshot\s+was\s+(?:taken|captured)\b",
        r"\barchived\s+on\s+\d",
    )),
    ("COOKIE_NOTICE", (
        r"\bwe\s+use\s+cookies\b", r"\bthis\s+(?:site|website)\s+uses\s+cookies\b",
        r"\baccept\s+all\s+cookies\b", r"\bcookies?\s+(?:policy|settings|preferences)\b",
        r"\bwir\s+verwenden\s+cookies\b", r"\bdiese\s+website\s+verwendet\s+cookies\b",
        r"\bcookies?\s+zulassen\b",
        r"\bnous\s+utilisons\s+des\s+cookies\b", r"\bce\s+site\s+utilise\s+des\s+cookies\b",
        r"\butilizamos\s+cookies\b", r"\beste\s+sitio\s+utiliza\s+cookies\b",
        r"\butilizziamo\s+i\s+cookie\b",
    )),
    ("NAVIGATION_CHROME", (
        r"^\s*skip\s+to\s+(?:main\s+)?content\b", r"\bskip\s+to\s+main\s+content\b",
        r"^\s*read\s+more\b", r"\bread\s+more\s+(?:information\s+)?on\b",
        r"\bsee\s+also\b\s*$", r"^\s*back\s+to\s+top\b",
        r"^\s*(?:home|menu|search|sitemap)\s*$",
        r"\byou\s+are\s+here\b", r"\bbreadcrumb\b",
        r"^\s*zum\s+(?:haupt)?inhalt\b", r"^\s*mehr\s+(?:dazu|erfahren|informationen)\b",
        r"^\s*weitere\s+informationen\s+(?:finden\s+sie|auf)\b",
        r"^\s*aller\s+au\s+contenu\b", r"^\s*en\s+savoir\s+plus\b",
        r"^\s*ir\s+al\s+contenido\b", r"^\s*m[áa]s\s+informaci[óo]n\s+en\b",
        r"^\s*last\s+update\b", r"^\s*letzte\s+aktualisierung\b",
        # Imperative calls to action pointing at another page or a setting.
        # They are site navigation written as sentences, which is how they
        # survived the V5.1 admission boundary and reached the corpus.
        r"\bvisit\s+(?:our|the)\s+[\w\s-]{0,40}\bpage\b",
        r"\bgo\s+to\s+(?:the\s+)?[\w\s-]{0,30}\b(?:page|site|website)\b",
        r"\bcheck\s+the\s+[\w\s-]{0,40}\b(?:events?|page|site|website|calendar)\b",
        r"\bstay\s+up\s+to\s+date\b", r"\bfind\s+out\s+more\b",
        r"\bfor\s+more\s+information\s+(?:and|see|visit|go)\b",
        r"\bchange\s+your\s+settings\b",
        r"\bgehen\s+sie\s+zur?\s+[\w\s-]{0,30}\b(?:seite|quellenseite|website)\b",
        r"\bum\s+die\s+originalfassung\s+zu\s+lesen\b",
        r"\bweitere\s+informationen\s+(?:finden|erhalten)\s+sie\b",
        r"\bconsultez\s+(?:notre|la)\s+[\w\s-]{0,30}\bpage\b",
        r"\bpour\s+en\s+savoir\s+plus\b",
        r"\bvisite\s+(?:nuestra|la)\s+[\w\s-]{0,30}\bp[áa]gina\b",
        r"\bpara\s+m[áa]s\s+informaci[óo]n,?\s+(?:visite|consulte)\b",
    )),
    ("CONTACT_BOILERPLATE", (
        r"\bif\s+you\s+are\s+not\s+a\s+journalist\b",
        r"\bpress\s+(?:office|enquiries|contact)\b",
        r"\bfor\s+media\s+enquiries\b",
        r"\bwenn\s+sie\s+kein\s+journalist\s+sind\b",
        r"\bpressestelle\b", r"\bfür\s+presseanfragen\b",
        r"\babteilung\s+für\s+öffentlichkeitsarbeit\b",
        r"\bcontact\s+presse\b", r"\bservice\s+de\s+presse\b",
        r"\boficina\s+de\s+prensa\b", r"\bcontacto\s+de\s+prensa\b",
        r"\bdrehgenehmigung\b",
        r"\bfor\s+more\s+information,?\s+contact\b",
        r"\bcontact\s+the\s+[\w\s-]{0,50}\bsection\s+at\b",
        r"\b[\w.-]+\s*\[at\]\s*[\w.-]+",
        r"\bjoined\s+[\w\s]{2,40}\s+in\s+(?:19|20)\d\d\s+as\s+a\b",
        r"\bcutting\s+her\s+teeth\b", r"\bcutting\s+his\s+teeth\b",
        r"\bis\s+a\s+(?:senior\s+)?(?:reporter|correspondent|editor)\s+(?:at|for)\b",
    )),
    ("LEGAL_BOILERPLATE", (
        r"^\s*©\s*\d{4}", r"\ball\s+rights\s+reserved\b",
        r"\bterms\s+(?:of\s+use|and\s+conditions)\b", r"\bprivacy\s+(?:policy|notice)\b",
        r"\blegal\s+notice\b", r"\bimpressum\b", r"\bdatenschutzerklärung\b",
        r"\bmentions\s+l[ée]gales\b", r"\baviso\s+legal\b",
        r"\bthis\s+document\s+is\s+an\s+excerpt\s+from\s+the\s+\w+\s+website\b",
    )),
    ("SOCIAL_SHARE", (
        r"^\s*share\s+(?:this|on)\b", r"^\s*follow\s+us\b",
        r"^\s*(?:facebook|twitter|linkedin|mastodon|bluesky)\s*$",
        r"^\s*teilen\b", r"^\s*partager\b", r"^\s*compartir\b",
    )),
    ("PRINT_OR_EXPORT", (
        r"^\s*print\s+as\s+pdf\b", r"^\s*download\s+(?:pdf|the\s+report)\b",
        r"^\s*als\s+pdf\s+drucken\b", r"^\s*imprimer\b", r"^\s*imprimir\b",
        r"^\s*export\s+to\b",
    )),
    ("SUBSCRIPTION_PROMPT", (
        r"\bsubscribe\s+to\s+(?:our\s+)?newsletter\b", r"\bsign\s+up\s+for\s+updates\b",
        r"\bnewsletter\s+abonnieren\b", r"\bs['’]abonner\b", r"\bsuscr[íi]base\b",
    )),
)

_COMPILED: tuple[tuple[str, tuple[re.Pattern[str], ...]], ...] = tuple(
    (name, tuple(re.compile(p, re.IGNORECASE | re.MULTILINE) for p in patterns))
    for name, patterns in _PATTERNS)
assert {name for name, _ in _PATTERNS} == set(CHROME_CLASSES)


@dataclass(frozen=True)
class ChromeFinding(Record):
    finding_id: str
    chrome_class: str
    span_start: int
    span_end: int
    matched: str


def classify_chrome(text: str) -> str | None:
    """The chrome class a span belongs to, or None for genuine body text."""
    body = text or ""
    for name, patterns in _COMPILED:
        for pattern in patterns:
            if pattern.search(body):
                return name
    return None


def is_chrome(text: str) -> bool:
    return classify_chrome(text) is not None


def find_chrome(text: str) -> tuple[ChromeFinding, ...]:
    """Every chrome region in a document, as offsets over the given text."""
    body = text or ""
    findings: list[ChromeFinding] = []
    for name, patterns in _COMPILED:
        for pattern in patterns:
            for match in pattern.finditer(body):
                start, end = match.span()
                line_start = body.rfind("\n", 0, start) + 1
                line_end = body.find("\n", end)
                line_end = len(body) if line_end < 0 else line_end
                findings.append(ChromeFinding(
                    stable_id("chrome", name, str(line_start), str(line_end)),
                    name, line_start, line_end, match.group(0)[:120]))
    merged: list[ChromeFinding] = []
    for finding in sorted(findings, key=lambda f: (f.span_start, f.span_end)):
        if merged and finding.span_start <= merged[-1].span_end and \
                finding.chrome_class == merged[-1].chrome_class:
            continue
        merged.append(finding)
    return tuple(merged)


def chrome_regions(text: str) -> tuple[tuple[int, int, str], ...]:
    """Chrome as ``(start, end, kind)`` triples for the admission path."""
    return tuple((f.span_start, f.span_end, f.chrome_class) for f in find_chrome(text))


def strip_chrome(text: str) -> str:
    """The document with chrome lines blanked, offsets preserved."""
    body = text or ""
    buffer = list(body)
    for finding in find_chrome(body):
        for index in range(finding.span_start, min(finding.span_end, len(buffer))):
            if buffer[index] != "\n":
                buffer[index] = " "
    return "".join(buffer)


def any_chrome(texts: Iterable[str]) -> bool:
    return any(is_chrome(item) for item in texts)
