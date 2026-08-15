"""Raw evidence capture and normalization (contract Section 7.1-7.2).

V5.2 scored 0.125 on source identity, and all three reviewers independently
reported the same cause: the dossier carried one populated field,
``issuing_institution``, copied straight from a custody column. Reviewers read
a populated publisher-shaped field as role evidence; the production resolver
refused to. Neither was working from what the document actually says.

This module reads the document instead. It emits raw evidence records pinned
to offsets in the custody bytes, and normalizes them into observations that
say what was found and where — never what role that implies. The role
decision belongs to ``roles.py``, one layer up.

Nothing here encodes a campaign, source or fixture answer.

Research shadow only.
"""
from __future__ import annotations

import re
from typing import Any, Iterable, Mapping, Sequence
from urllib.parse import urlparse

from ..v4.models import NormalizedDocument
from .provenance import (
    NormalizedObservationRecord, RawEvidenceRecord, normalized_observation,
    raw_evidence,
)

# How much of a document counts as its front matter and its tail.  Imprints,
# mastheads and publication lines live at one end or the other.
_HEAD = 2500
_TAIL = 2500

_PATTERNS: tuple[tuple[str, str, str], ...] = (
    # (observation_type, regex, capture description)
    ("EXPLICIT_PUBLISHER_LINE",
     r"(?:published\s+by|publisher|herausgegeben\s+von|herausgeber|"
     r"[ée]diteur|publi[ée]\s+par|publicado\s+por|editado\s+por|"
     r"pubblicato\s+da)\s*[:\-]?\s*([^\n|]{3,120})", "publisher line"),
    ("EXPLICIT_ISSUER_LINE",
     r"(?:issued\s+by|issuing\s+authority|erlassen\s+von|"
     r"[ée]mis\s+par|emitido\s+por|emesso\s+da)\s*[:\-]?\s*([^\n|]{3,120})",
     "issuer line"),
    ("EXPLICIT_AUTHOR_LINE",
     r"(?:^|\n)\s*(?:by|von|par|por|di)\s+([A-ZÀ-Ý][\w.À-ÿ'-]+"
     r"(?:\s+[A-ZÀ-Ý][\w.À-ÿ'-]+){0,3})\s*(?:\n|$)", "byline"),
    ("EXPLICIT_EDITOR_LINE",
     r"(?:edited\s+by|editor|redaktion|r[ée]dacteur|editado\s+por)\s*[:\-]?\s*"
     r"([^\n|]{3,120})", "editor line"),
    ("EXPLICIT_SUBMISSION_LINE",
     r"(?:submitted\s+by|uploaded\s+by|deposited\s+by|eingereicht\s+von|"
     r"soumis\s+par|presentado\s+por)\s*[:\-]?\s*([^\n|]{3,120})",
     "submission line"),
    ("COPYRIGHT_HOLDER",
     r"(?:©|\(c\)|copyright)\s*(?:\d{4}\s*[-–]?\s*\d{0,4})?\s*"
     r"([^\n|]{3,100})", "copyright line"),
    ("TRANSLATION_NOTICE",
     r"(?:translated\s+by|translation\s+of|[üu]bersetzt\s+von|[üu]bersetzung\s+von|"
     r"traduit\s+par|traduction\s+de|traducido\s+por|tradotto\s+da)"
     r"\s*[:\-]?\s*([^\n|]{0,120})", "translation notice"),
    ("SYNDICATION_NOTICE",
     r"(?:syndicated\s+(?:by|from)|via\s+(?:reuters|afp|dpa|ap|efe|ansa|pa\s+media)|"
     r"wire\s+service)\s*[:\-]?\s*([^\n|]{0,120})", "syndication notice"),
    ("MIRROR_NOTICE",
     r"(?:mirror(?:ed)?\s+(?:of|from|site)|spiegelserver|miroir\s+de|"
     r"espejo\s+de)\s*[:\-]?\s*([^\n|]{0,120})", "mirror notice"),
    ("ARCHIVE_PROVIDER",
     r"(?:the\s+wayback\s+machine|archived\s+by|archive-it|internet\s+archive|"
     r"archiviert\s+von|archiv[ée]\s+par)\s*[:\-]?\s*([^\n|]{0,120})",
     "archive banner"),
    ("COMMISSIONING_NOTICE",
     r"(?:commissioned\s+by|prepared\s+for|study\s+for|im\s+auftrag\s+von|"
     r"command[ée]\s+par|encargado\s+por)\s*[:\-]?\s*([^\n|]{3,120})",
     "commissioning statement"),
    ("OPERATING_ENTITY_NOTICE",
     r"(?:operated\s+by|managed\s+by|betrieben\s+von|exploit[ée]\s+par|"
     r"gestionado\s+por)\s*[:\-]?\s*([^\n|]{3,120})", "operating notice"),
    ("OWNERSHIP_RECORD",
     r"(?:owned\s+by|a\s+(?:subsidiary|division)\s+of|eigent[üu]mer|"
     r"propri[ée]t[ée]\s+de|propiedad\s+de)\s*[:\-]?\s*([^\n|]{3,120})",
     "ownership record"),
    ("DOCUMENT_SERIES_OWNER",
     r"(?:official\s+journal|amtsblatt|journal\s+officiel|diario\s+oficial|"
     r"gazzetta\s+ufficiale)\s*(?:of|der|de|du|dell[ao])?\s*([^\n|]{0,90})",
     "document series"),
    ("REVISION_STATEMENT",
     r"((?:this\s+\w+\s+)?(?:revises|replaces|supersedes|amends|corrects|"
     r"withdraws|retracts|consolidates)\s+[^\n.]{3,160})", "revision statement"),
    ("DOCUMENT_IDENTIFIER",
     r"\b((?:SIB|AD|CELEX|ELI|COM|SWD|JOIN|C|L)\s?(?:No\.?\s?)?[\d./–-]{3,24})\b",
     "document identifier"),
    ("PUBLICATION_DATE",
     r"(?:published|issued|adopted|of)\s+(?:on\s+)?(\d{1,2}\s+\w+\s+(?:19|20)\d\d)",
     "publication date"),
    ("CONTACT_BLOCK",
     r"((?:for\s+more\s+information,?\s+contact|press\s+office|"
     r"pressestelle|contact\s+presse)[^\n]{0,120})", "contact block"),
)

_COMPILED = tuple((name, re.compile(pattern, re.IGNORECASE | re.MULTILINE), label)
                  for name, pattern, label in _PATTERNS)


def _clean(value: str) -> str:
    return " ".join((value or "").split()).strip(" .,;:|-–—")


def capture(document: NormalizedDocument, *, source_object_id: str,
            content_hash: str, final_url: str | None = None,
            redirects: Sequence[Any] = (), media_type: str = "text/html",
            title: str | None = None
            ) -> tuple[tuple[RawEvidenceRecord, ...],
                       tuple[NormalizedObservationRecord, ...]]:
    """Read a document into raw evidence and normalized observations."""
    text = document.text or ""
    window = text[:_HEAD] + ("\n" + text[-_TAIL:] if len(text) > _HEAD + _TAIL else "")
    raws: list[RawEvidenceRecord] = []
    observations: list[NormalizedObservationRecord] = []

    def add(observation_type: str, value: str, locator: Mapping[str, Any],
            raw_text: str, normalization: str, mode: str = "EXPLICIT",
            strength: str = "EXPLICIT") -> None:
        cleaned = _clean(value)
        if not cleaned:
            return
        evidence = raw_evidence(
            source_object_id=source_object_id, raw_text=raw_text[:400],
            locator=dict(locator), content_hash=content_hash,
            document_id=document.document_id, media_type=media_type)
        raws.append(evidence)
        observations.append(normalized_observation(
            raw_evidence_ids=[evidence.evidence_id],
            observation_type=observation_type, observed_value=cleaned[:200],
            normalization=normalization, mode=mode, strength=strength,
            provenance={"document_id": document.document_id,
                        "parser": document.parser, "locator": dict(locator)}))

    # --- text-derived observations -------------------------------------
    for name, pattern, label in _COMPILED:
        for match in pattern.finditer(window):
            captured = match.group(1) if match.groups() else match.group(0)
            add(name, captured,
                {"kind": "TEXT_WINDOW", "start": match.start(), "end": match.end(),
                 "label": label},
                match.group(0), f"whitespace-normalized {label}")
            break  # one observation per type per document; the first is the head

    # --- structural observations ---------------------------------------
    lines = [line for line in text[:_HEAD].split("\n") if line.strip()]
    if lines:
        add("HEADER_TEXT", lines[0], {"kind": "FIRST_LINE"}, lines[0],
            "first non-empty line of the normalized text")
    tail_lines = [line for line in text[-_TAIL:].split("\n") if line.strip()]
    if tail_lines:
        add("FOOTER_TEXT", tail_lines[-1], {"kind": "LAST_LINE"}, tail_lines[-1],
            "last non-empty line of the normalized text")
    if title:
        add("TITLE_PAGE_INSTITUTION", title, {"kind": "CUSTODY_TITLE"}, title,
            "title recorded at capture time", strength="STRONGLY_IMPLIED")

    # --- transport observations ----------------------------------------
    if final_url:
        host = urlparse(final_url).netloc
        if host:
            add("DOMAIN_HOST", host, {"kind": "FINAL_URL", "url": final_url},
                final_url, "netloc of the final URL")
    if redirects:
        add("REDIRECT_CHAIN", " -> ".join(str(item) for item in redirects)[:200],
            {"kind": "REDIRECTS", "count": len(redirects)},
            str(redirects)[:400], "ordered redirect chain")

    # An institutional attribution is the strongest thing the front matter can
    # offer when no explicit publisher or issuer line exists.  It is recorded
    # as an attribution, not as a role.
    institutional = re.search(
        r"\b((?:European\s+\w+|Commission|Council|Parliament|Agency|Authority|"
        r"Ministry|Ministerium|Minist[èe]re|Bundesamt|Office|Directorate[- ]General|"
        r"Secretariat|Joint\s+Undertaking)[\w\s,'’-]{0,80})", window)
    if institutional:
        add("INSTITUTIONAL_ATTRIBUTION", institutional.group(1),
            {"kind": "FRONT_MATTER", "start": institutional.start()},
            institutional.group(0), "institutional name in front matter",
            mode="INFERRED", strength="STRONGLY_IMPLIED")

    return tuple(raws), tuple(observations)


def observations_by_type(observations: Iterable[NormalizedObservationRecord]
                         ) -> dict[str, list[NormalizedObservationRecord]]:
    grouped: dict[str, list[NormalizedObservationRecord]] = {}
    for item in observations:
        grouped.setdefault(item.observation_type, []).append(item)
    return grouped


def capture_report(observations: Sequence[NormalizedObservationRecord]
                   ) -> dict[str, Any]:
    """Section 7.6 — raw-evidence capture accuracy, reported on its own."""
    by_type: dict[str, int] = {}
    by_strength: dict[str, int] = {}
    explicit = 0
    for item in observations:
        by_type[item.observation_type] = by_type.get(item.observation_type, 0) + 1
        by_strength[item.strength] = by_strength.get(item.strength, 0) + 1
        explicit += item.mode == "EXPLICIT"
    return {
        "observations": len(observations),
        "explicit_observations": explicit,
        "inferred_observations": len(observations) - explicit,
        "by_observation_type": dict(sorted(by_type.items())),
        "by_strength": dict(sorted(by_strength.items())),
        "role_bearing_observations": 0,
    }
