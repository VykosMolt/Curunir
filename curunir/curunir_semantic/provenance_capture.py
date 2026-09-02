"""Read what a document says about its own origin, and where it says it.

Publisher lines, bylines, copyright and translation notices, official-journal
identifiers, and the head and foot of the text, each pinned to an offset in the
custody bytes.

An observation says what the document states, never what role that implies:
PUBLISHED_BY would be a role, and only a person may decide one.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping, Sequence
from urllib.parse import urlparse

# ---- private hashing helpers; nothing here is persisted ------------------

def _canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)

def _sha256(value: bytes | str | Mapping[str, Any] | list[Any]) -> str:
    if isinstance(value, bytes):
        raw = value
    elif isinstance(value, str):
        raw = value.encode("utf-8")
    else:
        raw = _canonical_json(value).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()

def _stable_id(prefix: str, *parts: object) -> str:
    return f"{prefix}-{_sha256('|'.join(str(part) for part in parts))[:24]}"

def _now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()

def require_aware(value: str) -> None:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("timestamp must be timezone-aware")

def require_hash(value: str) -> None:
    if not re.fullmatch(r"[0-9a-f]{64}", value):
        raise ValueError("invalid SHA-256")


# ---- the document being read ---------------------------------------------

class Record:
    def to_record(self) -> dict[str, Any]:
        return asdict(self)

MAPPING_PRECISIONS = frozenset({
    "EXACT_BYTE", "EXACT_CHARACTER", "EXACT_PAGE_CHARACTER", "APPROXIMATE_PAGE",
    "APPROXIMATE_SECTION", "UNMAPPED",
})

@dataclass(frozen=True)
class DerivativeMapping(Record):
    mapping_id: str
    source_object_id: str
    source_hash: str
    derivative_hash: str
    source_locator: str
    derivative_start: int
    derivative_end: int
    precision: str

    def __post_init__(self) -> None:
        require_hash(self.source_hash)
        require_hash(self.derivative_hash)
        if self.precision not in MAPPING_PRECISIONS:
            raise ValueError("invalid mapping precision")

@dataclass(frozen=True)
class NormalizedDocument(Record):
    document_id: str
    source_object_id: str
    source_hash: str
    derivative_hash: str
    parser: str
    parser_version: str
    language: str
    text: str
    sections: tuple[tuple[str, int, int], ...]
    pages: tuple[tuple[int, int, int], ...]
    mappings: tuple[DerivativeMapping, ...]
    warnings: tuple[str, ...]
    omitted_content: tuple[str, ...]
    generated_time: str

    def __post_init__(self) -> None:
        require_hash(self.source_hash)
        require_hash(self.derivative_hash)
        require_aware(self.generated_time)
        if self.text and not self.mappings:
            raise ValueError("normalized text requires source mapping")


# ---- raw evidence --------------------------------------------------------

class ProvenanceViolation(ValueError):
    """A boundary between evidence, observation and role was crossed."""

OBSERVATION_MODES = ("EXPLICIT", "INFERRED")

EVIDENCE_STRENGTHS = ("EXPLICIT", "STRONGLY_IMPLIED", "WEAKLY_IMPLIED",
                      "CONFLICTING", "ABSENT")

@dataclass(frozen=True)
class RawEvidenceRecord(Record):
    """Bytes and what is directly visible in them. Nothing inferred."""

    evidence_id: str
    source_object_id: str
    document_id: str | None
    locator: Mapping[str, Any]
    raw_text: str
    media_type: str
    content_hash: str
    observed_time: str

    def __post_init__(self) -> None:
        require_aware(self.observed_time)
        require_hash(self.content_hash)
        if not self.source_object_id.strip():
            raise ProvenanceViolation("raw evidence requires a custody source object")
        if not self.locator:
            raise ProvenanceViolation("raw evidence requires a locator into the source")

def raw_evidence(*, source_object_id: str, raw_text: str, locator: Mapping[str, Any],
                 content_hash: str, document_id: str | None = None,
                 media_type: str = "text/plain") -> RawEvidenceRecord:
    return RawEvidenceRecord(
        _stable_id("v5-3-raw", source_object_id, _canonical_json(dict(locator)),
                  _sha256(raw_text)),
        source_object_id, document_id, dict(locator), raw_text, media_type,
        content_hash, _now_utc())


# ---- normalized observations ---------------------------------------------

OBSERVATION_TYPES: tuple[str, ...] = (
    "EXPLICIT_AUTHOR_LINE", "EXPLICIT_EDITOR_LINE", "EXPLICIT_ISSUER_LINE",
    "EXPLICIT_PUBLISHER_LINE", "EXPLICIT_SUBMISSION_LINE",
    "INSTITUTIONAL_ATTRIBUTION", "TITLE_PAGE_INSTITUTION",
    "DOMAIN_HOST", "REDIRECT_CHAIN", "DOCUMENT_SERIES_OWNER",
    "COPYRIGHT_HOLDER", "ARCHIVE_PROVIDER", "MIRROR_NOTICE",
    "TRANSLATION_NOTICE", "SYNDICATION_NOTICE", "COMMISSIONING_NOTICE",
    "OPERATING_ENTITY_NOTICE", "OWNERSHIP_RECORD",
    "PUBLICATION_DATE", "REVISION_STATEMENT", "DOCUMENT_IDENTIFIER",
    "HEADER_TEXT", "FOOTER_TEXT", "TABLE_HEADER", "CAPTION",
    "QUOTED_SENTENCE", "NUMERIC_VALUE", "CONTACT_BLOCK",
)

_ROLE_WORDS = frozenset({
    "AUTHORED_BY", "EDITED_BY", "SUBMITTED_BY", "ISSUED_BY", "PUBLISHED_BY",
    "HOSTED_BY", "MIRRORED_BY", "ARCHIVED_BY", "TRANSLATED_BY",
    "SYNDICATED_BY", "COMMISSIONED_BY", "OWNED_BY", "OPERATED_BY",
})


# No observation type may name a role. Raised rather than asserted, because
# `python -O` strips assertions and this check has to hold.
for _name in OBSERVATION_TYPES:
    if _name in _ROLE_WORDS:
        raise ProvenanceViolation(f"observation type {_name} names a role")

@dataclass(frozen=True)
class NormalizedObservationRecord(Record):
    """What the source says, normalized, with the raw text that supports it.

    Carries no role. ``semantic_role_supplied`` exists so a renderer can show
    that no role reached the reader.
    """

    observation_id: str
    raw_evidence_ids: tuple[str, ...]
    observation_type: str
    observed_value: str
    normalization: str
    mode: str
    strength: str
    provenance: Mapping[str, Any]
    rationale: str
    semantic_role_supplied: bool
    recorded_time: str

    def __post_init__(self) -> None:
        if self.observation_type not in OBSERVATION_TYPES:
            raise ProvenanceViolation(
                f"unknown observation type: {self.observation_type}")
        if self.mode not in OBSERVATION_MODES:
            raise ProvenanceViolation(f"unknown observation mode: {self.mode}")
        if self.strength not in EVIDENCE_STRENGTHS:
            raise ProvenanceViolation(f"unknown evidence strength: {self.strength}")
        if not self.raw_evidence_ids:
            raise ProvenanceViolation(
                "a normalized observation requires its raw support; an "
                "observation without raw evidence is an inference wearing a "
                "normalization's name")
        if self.semantic_role_supplied:
            raise ProvenanceViolation(
                "a normalized observation may not carry a semantic role; that "
                "is a layer-3 production decision")
        if self.observed_value.upper() in _ROLE_WORDS:
            raise ProvenanceViolation(
                "observed value is a role name, not an observation")
        require_aware(self.recorded_time)

def normalized_observation(*, raw_evidence_ids: Iterable[str],
                           observation_type: str, observed_value: str,
                           normalization: str, mode: str = "EXPLICIT",
                           strength: str = "EXPLICIT",
                           provenance: Mapping[str, Any] | None = None,
                           rationale: str = "") -> NormalizedObservationRecord:
    ids = tuple(raw_evidence_ids)
    return NormalizedObservationRecord(
        _stable_id("v5-3-observation", observation_type, observed_value, "|".join(ids)),
        ids, observation_type, observed_value, normalization, mode, strength,
        dict(provenance or {}), rationale or f"{observation_type} read from source",
        False, _now_utc())


# ---- capture -------------------------------------------------------------

_HEAD = 2500

_TAIL = 2500

_PATTERNS: tuple[tuple[str, str, str], ...] = (
    # (observation type, pattern, what the match is called)
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

    # --- from the text ---------------------------------------------------
    for name, pattern, label in _COMPILED:
        for match in pattern.finditer(window):
            captured = match.group(1) if match.groups() else match.group(0)
            add(name, captured,
                {"kind": "TEXT_WINDOW", "start": match.start(), "end": match.end(),
                 "label": label},
                match.group(0), f"whitespace-normalized {label}")
            break  # one observation per type, the first one found

    # --- from the document's shape ----------------------------------------
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

    # --- from how it was fetched ------------------------------------------
    if final_url:
        host = urlparse(final_url).netloc
        if host:
            add("DOMAIN_HOST", host, {"kind": "FINAL_URL", "url": final_url},
                final_url, "netloc of the final URL")
    if redirects:
        add("REDIRECT_CHAIN", " -> ".join(str(item) for item in redirects)[:200],
            {"kind": "REDIRECTS", "count": len(redirects)},
            str(redirects)[:400], "ordered redirect chain")

    # When the front matter has no publisher or issuer line, an institution
    # named there is the best it offers. Recorded as an attribution, not a role.
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
