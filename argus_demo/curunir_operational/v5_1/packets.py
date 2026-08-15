"""Packet-system closure for V5.1 (contract Section 18).

Closes the verified V5 failure modes from the milestone weakness inventory:
sampling-stratum label leakage into reviewer-visible payloads, packet context
insufficiency discovered only at review time, reserve insufficiency leaving a
defective packet unreplaceable, and packets whose material made external
adjudication impossible by construction.

Design rule: reviewer-visible payloads carry evidence only.  Builder metadata
(sampling strata) and sealed answers live in separate REVIEW_ENGINE_ONLY files
keyed by packet id.  A packet whose material cannot support its question type
is a PACKET_CONSTRUCTION_DEFECT and cannot freeze; genuine evidence
insufficiency requires an explicit demonstration and remains a valid packet
with an EPISTEMICALLY_UNRESOLVABLE ground truth.
"""
from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterator, Mapping, Sequence

from ..v4.models import require_aware, require_hash
from ..v5.models import SURFACES
from .models import (
    ADMISSION_STAGES, CAPABILITY_OUTCOMES, CONTENT_RELATIONS, CONTEXT_SUFFICIENCY,
    DEPENDENCE_SIGNAL_KINDS, DEPENDENCE_STATES, RELATION_CLASSES,
    REVIEW_DECISIONS, ROLE_EVIDENCE_KINDS, SOURCE_ROLES, SUPPORT_STATES,
    Record, canonical_json, now_utc, sha256, stable_id,
)

REVIEW_ENGINE_ONLY = "REVIEW_ENGINE_ONLY"
MINIMUM_RESERVE_RATIO = 0.20
# Deterministic builder timestamp: freezing must not depend on wall clock.
PACKET_BUILD_TIME = "2026-07-24T12:00:00+00:00"

REVIEWER_PAYLOAD_DENYLIST = frozenset({
    "sampling_stratum", "system_label", "answer", "expected", "stratum",
    "split", "difficulty",
})
_REVIEWER_TOKEN_DENYLIST = frozenset({"stratum", "split", "difficulty", "answer", "expected"})
_ANSWER_KEY_FIELDS = frozenset({
    "answer_key", "ground_truth", "correct_option", "expected_answer", "final_verdict",
})
_ANSWER_KEY_TOKENS = frozenset({"gold", "verdict", "adjudication", "truth"})

# Vocabularies whose full-menu enumeration in reviewer instructions is not a
# leak: singling out one label leaks, listing the closed menu does not.
# Closed label pair for report-faithfulness packets (Section 24.1 S6).
RENDERING_LABELS = ("RENDERED_FAITHFULLY", "RENDERING_REFUSED")

_LABEL_VOCABULARIES = (
    REVIEW_DECISIONS, DEPENDENCE_STATES, SUPPORT_STATES, RELATION_CLASSES,
    CAPABILITY_OUTCOMES, CONTENT_RELATIONS, SOURCE_ROLES, tuple(ADMISSION_STAGES),
    RENDERING_LABELS,
)

_EXTRACTION, _ORIGIN, _DEPENDENCE, _CLAIM, _CONTRADICTION, _FAITHFULNESS = SURFACES

# Per-surface sufficiency rules: the packet must contain the evidence its
# question type needs (Section 18.1); supporting keys downgrade to
# CONTEXT_PARTIALLY_SUFFICIENT rather than defect.
_SURFACE_REQUIRED = {
    _EXTRACTION: ("excerpts", "reviewer_instructions", "candidate"),
    _ORIGIN: ("excerpts", "reviewer_instructions", "role_metadata"),
    _DEPENDENCE: ("excerpts", "reviewer_instructions", "source_metadata",
                  "publication_timing", "dependence_evidence"),
    _CLAIM: ("excerpts", "reviewer_instructions", "claim"),
    _CONTRADICTION: ("excerpts", "reviewer_instructions", "publication_timing"),
    _FAITHFULNESS: ("report_sentence", "structured_basis", "reviewer_instructions"),
}
_SURFACE_SUPPORTING = {
    _EXTRACTION: ("surrounding_context",),
    _ORIGIN: ("origin_manifest",),
    _DEPENDENCE: ("content_overlap_analysis",),
    _CLAIM: ("qualifications",),
    _CONTRADICTION: ("relation_context",),
    _FAITHFULNESS: ("excerpts",),
}


def _normalize(value: Any) -> str:
    """Casefold, collapse whitespace, AND fold underscore/hyphen separators to
    spaces: 'HARD_NEGATIVE' and 'hard-negative' and 'hard negative' must all
    compare equal, or separator variants smuggle sealed values past the scan."""
    return " ".join(re.sub(r"[-_]+", " ", str(value)).split()).casefold()


def _tokens(key: str) -> frozenset[str]:
    return frozenset(t for t in re.split(r"[^a-z0-9]+", str(key).casefold()) if t)


def _walk(value: Any, path: str = "$") -> Iterator[tuple[str, str, str]]:
    """Yield ("key", path, key_name) and ("str", path, string_value) leaves."""
    if isinstance(value, Mapping):
        for key, item in value.items():
            yield ("key", f"{path}.{key}", str(key))
            yield from _walk(item, f"{path}.{key}")
    elif isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            yield from _walk(item, f"{path}[{index}]")
    elif isinstance(value, str):
        yield ("str", path, value)


def _reviewer_key_flagged(key: str) -> bool:
    tokens = _tokens(key)
    return (key.casefold() in REVIEWER_PAYLOAD_DENYLIST
            or bool(tokens & _REVIEWER_TOKEN_DENYLIST)
            or {"system", "label"} <= tokens)


def _answer_key_flagged(key: str) -> bool:
    return key.casefold() in _ANSWER_KEY_FIELDS or bool(_tokens(key) & _ANSWER_KEY_TOKENS)


def _denylisted_keys(value: Any) -> tuple[tuple[str, str], ...]:
    return tuple((path, key) for kind, path, key in _walk(value)
                 if kind == "key" and _reviewer_key_flagged(key))


def _answer_key_hits(value: Any) -> tuple[tuple[str, str], ...]:
    return tuple((path, key) for kind, path, key in _walk(value)
                 if kind == "key" and _answer_key_flagged(key))


def _menu_segments(text: str) -> tuple[str, ...]:
    return tuple(segment for segment in re.split(r"[.\n;]+", text) if segment.strip())


# Review-history phrasing singles out the answer no matter how many labels
# pad the sentence — it is never an instruction shape.
_HISTORY_PATTERN = re.compile(
    r"(?:prior|previous|earlier|other|past)\s+\w*\s*"
    r"(?:review\w*|panel\w*|rater\w*|adjudicat\w*)"
    r"|(?:review\w*|panel\w*|rater\w*)\s+(?:found|judged|rated|labell?ed|decided|concluded)")

# Definitional instruction shape: a defining TAIL is required — a bare
# directive lead ("choose X") is an instruction to pick the answer, never a
# definition, and is always a leak.
_DEFINITIONAL_TAILS = (" means", " when", " only when", " if", " applies", " indicates")

# Connectors and lead-in words permitted inside a bare label menu.
_MENU_ALLOWED_WORDS = frozenset({
    "labels", "label", "options", "option", "available", "choices",
    "select", "decisions", "decision", "values", "possible", "answers",
    "permitted", "allowed", "and", "or", "are", "is",
})


def _whole_token(needle: str, haystack: str) -> bool:
    return re.search(rf"(?<![a-z0-9]){re.escape(needle)}(?![a-z0-9])", haystack) is not None


def _exempt_segment(segment: str, vocabulary: tuple[str, ...], norm_answer: str) -> bool:
    """A segment containing the sealed answer is exempt only when it is an
    instruction shape — a bare label menu or a definitional sentence — and
    never review-history prose.  Labels count as WHOLE tokens only (CORRECT
    inside INCORRECT is not a menu entry), and a menu may contain nothing but
    labels, connectors, and lead-in words."""
    norm_segment = _normalize(segment)
    if _HISTORY_PATTERN.search(norm_segment):
        return False
    # A short lead ending in a colon ("Permitted decisions:", "Labels:") is
    # part of the menu shape; a long pre-colon clause is ordinary prose.
    lead, colon, rest = norm_segment.partition(":")
    menu_text = rest if colon and len(lead.split()) <= 4 else norm_segment
    present = [_normalize(label) for label in vocabulary
               if _whole_token(_normalize(label), menu_text)]
    if len(present) >= 3 and norm_answer in menu_text:
        stripped = menu_text
        for label in sorted(present, key=len, reverse=True):
            stripped = re.sub(rf"(?<![a-z0-9]){re.escape(label)}(?![a-z0-9])", " ", stripped)
        residual = [token for token in re.findall(r"[a-zà-ÿ]{2,}", stripped)
                    if token not in _MENU_ALLOWED_WORDS]
        if not residual:
            return True
    position = norm_segment.find(norm_answer)
    after = norm_segment[position + len(norm_answer):]
    return any(after.startswith(tail) for tail in _DEFINITIONAL_TAILS)


def _label_menu_exempt(text: str, sealed_answer: str) -> bool:
    """Exempt only when every occurrence of the sealed answer sits inside an
    instruction-shaped segment (Section 18.4: appending a menu to an
    answer-revealing sentence is not an exemption)."""
    norm_answer = _normalize(sealed_answer)
    for vocabulary in _LABEL_VOCABULARIES:
        if any(_normalize(label) == norm_answer for label in vocabulary):
            with_answer = [segment for segment in _menu_segments(text)
                           if norm_answer in _normalize(segment)]
            if with_answer and all(_exempt_segment(segment, vocabulary, norm_answer)
                                   for segment in with_answer):
                return True
    return False


# Builder-side vocabulary that must never surface in reviewer-visible VALUES,
# regardless of what key carries it (Section 18.4 indirect leakage).  Compound
# phrases only: quoted source excerpts may legitimately contain bare words
# like "difficulty" or "stratum", but never builder jargon compounds.
_VALUE_LEAK_PHRASES = (
    "sampling stratum", "sampling_stratum", "sampling strata",
    "difficulty tier", "difficulty level", "difficulty stratum",
    "reserve pool", "reserve_pool", "heldout", "held out set", "held-out set",
    "held out split", "held-out split", "answer key", "answer_key",
    "system label", "system_label", "expected answer", "expected label",
    "gold label", "repair development set", "regression set", "challenge set",
)
_VALUE_LEAK_PATTERN = re.compile(
    "|".join(rf"(?<![a-z0-9]){re.escape(_normalize(phrase))}(?:s|es)?(?![a-z0-9])"
             for phrase in dict.fromkeys(_normalize(p) for p in _VALUE_LEAK_PHRASES)))


def _value_leaks(text: str) -> tuple[str, ...]:
    return tuple(dict.fromkeys(_VALUE_LEAK_PATTERN.findall(_normalize(text))))


_ABSOLUTE_PATH = re.compile(r"(?:^|[\s\"'=(])/(?:[\w.\-]+/)+[\w.\-]+")
_RELATIVE_FILE = re.compile(r"[\w.\-]+/[\w.\-]+\.(?:json|jsonl|txt|md|py|csv|yaml|html)\b")


def _path_leak(text: str) -> bool:
    if text.startswith(("http://", "https://")):
        return False
    return bool(_ABSOLUTE_PATH.search(text) or ":\\" in text or _RELATIVE_FILE.search(text))


def _id_findings(packet_id: str) -> tuple[dict[str, str], ...]:
    tail = re.split(r"[-_]", packet_id)[-1] if packet_id else ""
    if tail.isdigit():
        return ({"kind": "ORDINAL_PACKET_ID", "path": "$.packet_id",
                 "detail": f"packet id ends in ordinal counter '{tail}'; ids must be content-derived"},)
    if not re.fullmatch(r"[0-9a-f]{24}", tail):
        return ({"kind": "NON_CONTENT_DERIVED_ID", "path": "$.packet_id",
                 "detail": "packet id tail is not a content-derived digest"},)
    return ()


@dataclass(frozen=True)
class ReviewPacket(Record):
    """Reviewer-visible packet.  Carries no sampling stratum by construction:
    the internal stratum lives only in the sealed builder manifest."""

    packet_id: str
    surface: str
    packet_version: int
    created_time: str
    blinded_material: Mapping[str, Any]
    access_marking: Mapping[str, Any]
    reserve: bool
    context_class: str
    frozen_content_hash: str

    def __post_init__(self) -> None:
        if self.surface not in SURFACES:
            raise ValueError(f"unknown packet surface: {self.surface}")
        if self.context_class not in CONTEXT_SUFFICIENCY:
            raise ValueError(f"unknown context sufficiency class: {self.context_class}")
        require_aware(self.created_time); require_hash(self.frozen_content_hash)
        if self.packet_version < 1:
            raise ValueError("packet version starts at 1")
        if not self.blinded_material:
            raise ValueError("packet requires blinded material")
        hits = _denylisted_keys(self.blinded_material) + _answer_key_hits(self.blinded_material)
        if hits:
            leaked = sorted({key for _path, key in hits})
            raise ValueError(f"reviewer-visible payload carries sealed builder or answer metadata: {leaked}")
        if self.frozen_content_hash != sha256(self.public_payload(include_hash=False)):
            raise ValueError("packet frozen hash mismatch")

    def public_payload(self, *, include_hash: bool = True) -> dict[str, Any]:
        value = asdict(self)
        if not include_hash:
            value.pop("frozen_content_hash", None)
        return value


@dataclass(frozen=True)
class SealedBuilderEntry(Record):
    """Builder-side stratum record keyed by packet id; never reviewer-visible."""

    entry_id: str
    packet_id: str
    sampling_stratum: str
    access_marking: Mapping[str, Any]

    def __post_init__(self) -> None:
        if not self.sampling_stratum.strip():
            raise ValueError("sealed builder entry requires a stratum")
        if tuple(self.access_marking.get("releasability") or ()) != (REVIEW_ENGINE_ONLY,):
            raise ValueError("sealed builder entries must be marked REVIEW_ENGINE_ONLY")


def build_packet(*, surface: str, material: Mapping[str, Any], context_class: str,
                 stratum: str, reserve: bool = False, version: int = 1,
                 access_marking: Mapping[str, Any] | None = None,
                 created_time: str = PACKET_BUILD_TIME) -> tuple[ReviewPacket, SealedBuilderEntry]:
    if not str(stratum).strip():
        raise ValueError("builder stratum is required (sealed, never reviewer-visible)")
    # Canonical deep copy: deterministic form, no shared mutable state.
    material = json.loads(canonical_json(dict(material)))
    packet_id = stable_id("v5-1-packet", surface, version, int(bool(reserve)),
                          sha256(material))
    provisional = {
        "packet_id": packet_id, "surface": surface, "packet_version": version,
        "created_time": created_time, "blinded_material": material,
        "access_marking": dict(access_marking or {"releasability": ["PUBLIC"]}),
        "reserve": bool(reserve), "context_class": context_class,
    }
    packet = ReviewPacket(**provisional, frozen_content_hash=sha256(provisional))
    entry = SealedBuilderEntry(
        stable_id("v5-1-stratum", packet_id, stratum), packet_id, str(stratum),
        {"releasability": [REVIEW_ENGINE_ONLY]})
    # Builder-side leak gate: the stratum value and builder vocabulary must
    # not reach reviewer-visible values under ANY key name.
    value_findings = tuple(
        finding for finding in leakage_scan(packet.public_payload(), None,
                                            sealed_stratum=str(stratum))
        if finding["kind"] in {"BUILDER_VOCABULARY_IN_VALUE", "STRATUM_VALUE_IN_PAYLOAD"})
    if value_findings:
        raise ValueError(
            "reviewer-visible payload leaks builder metadata in values: "
            + "; ".join(finding["detail"] for finding in value_findings))
    return packet, entry


def leakage_scan(packet_payload: Mapping[str, Any], sealed_answer: str | None,
                 sealed_stratum: str | None = None) -> tuple[dict[str, str], ...]:
    """Section 18.1 leakage audit over a reviewer-visible payload.

    Scans keys AND string values: builder metadata renamed under an innocuous
    key, or embedded inside value text, is still a leak.
    """
    findings: list[dict[str, str]] = []
    norm_stratum = _normalize(sealed_stratum) if sealed_stratum else ""
    for kind, path, value in _walk(packet_payload):
        if kind == "key" and _reviewer_key_flagged(value):
            findings.append({"kind": "DENYLISTED_FIELD", "path": path,
                             "detail": f"field name '{value}' is builder/answer metadata"})
        elif kind == "str":
            # Verbatim source excerpts are pinned to immutable custody by the
            # SPAN_EXISTENCE validator, so a label word occurring inside them
            # is certifiably source-authored coincidence, not builder leakage.
            in_pinned_excerpt = bool(re.search(
                r"excerpts\[\d+\]\.original_text$", path))
            if sealed_answer and not in_pinned_excerpt:
                norm_answer = _normalize(sealed_answer)
                if norm_answer and norm_answer in _normalize(value) \
                        and not _label_menu_exempt(value, sealed_answer):
                    findings.append({"kind": "ANSWER_VERBATIM", "path": path,
                                     "detail": "payload text contains the sealed answer verbatim"})
            for phrase in _value_leaks(value):
                findings.append({"kind": "BUILDER_VOCABULARY_IN_VALUE", "path": path,
                                 "detail": f"payload text contains builder vocabulary '{phrase}'"})
            if norm_stratum and norm_stratum in _normalize(value):
                findings.append({"kind": "STRATUM_VALUE_IN_PAYLOAD", "path": path,
                                 "detail": "payload text contains the sealed sampling stratum"})
            if _path_leak(value):
                findings.append({"kind": "FILE_PATH", "path": path,
                                 "detail": "payload text contains a file-system path"})
    if isinstance(packet_payload, Mapping) and "packet_id" in packet_payload:
        findings.extend(_id_findings(str(packet_payload["packet_id"])))
    return tuple(findings)


def _material_empty(value: Any) -> bool:
    return value is None or value == "" or value == [] or value == {} or value == ()


def _mapping_excerpts(material: Mapping[str, Any]) -> tuple[Mapping[str, Any], ...]:
    return tuple(e for e in (material.get("excerpts") or ()) if isinstance(e, Mapping))


def _context_gap(surface: str, material: Mapping[str, Any]) -> tuple[tuple[str, ...], tuple[str, ...]]:
    missing_required = [key for key in _SURFACE_REQUIRED[surface]
                        if _material_empty(material.get(key))]
    missing_supporting = [key for key in _SURFACE_SUPPORTING[surface]
                          if _material_empty(material.get(key))]
    excerpts = _mapping_excerpts(material)
    if surface == _DEPENDENCE:
        sides = sorted({str(e.get("source_id")) for e in excerpts if e.get("source_id") is not None})
        if len(sides) < 2:
            missing_required.append("both_side_excerpts")
        for side in sides:
            if side not in (material.get("source_metadata") or {}):
                missing_required.append(f"source_metadata[{side}]")
            if side not in (material.get("publication_timing") or {}):
                missing_required.append(f"publication_timing[{side}]")
    if surface == _CONTRADICTION and len(excerpts) < 2:
        missing_required.append("both_statement_excerpts")
    if surface == _FAITHFULNESS:
        basis = material.get("structured_basis")
        if not _material_empty(basis):
            rows = basis if isinstance(basis, (list, tuple)) else ()
            if not rows or not all(isinstance(row, Mapping) and row for row in rows):
                missing_required.append("structured_basis_rows")
    return tuple(missing_required), tuple(missing_supporting)


def classify_context(packet: ReviewPacket) -> str:
    """CONTEXT_INSUFFICIENT_BY_NATURE needs a substantive demonstration that the
    underlying public evidence is what is missing; otherwise a gap in required
    material is a builder failure, never a silent unknown."""
    missing_required, missing_supporting = _context_gap(packet.surface, packet.blinded_material)
    if missing_required:
        demonstration = str(packet.blinded_material.get("evidence_insufficiency_demonstration") or "")
        if len(demonstration.strip()) >= 40:
            return "CONTEXT_INSUFFICIENT_BY_NATURE"
        return "PACKET_CONSTRUCTION_DEFECT"
    if missing_supporting:
        return "CONTEXT_PARTIALLY_SUFFICIENT"
    return "CONTEXT_SUFFICIENT"


def _source_text(source_lookup: Mapping[str, Any], source_id: Any) -> str | None:
    entry = source_lookup.get(source_id) if source_id is not None else None
    if entry is None:
        return None
    if isinstance(entry, str):
        return entry
    if isinstance(entry, Mapping) and isinstance(entry.get("text"), str):
        return entry["text"]
    return None


def validate_packet(packet: ReviewPacket, source_lookup: Mapping[str, Any], *,
                    sealed_answer: str | None = None) -> tuple[tuple[str, bool, str], ...]:
    """Run all Section 18.1 named validators; returns (check, passed, detail)."""
    material = packet.blinded_material
    raw_excerpts = material.get("excerpts") or ()
    excerpts = _mapping_excerpts(material)
    checks: list[tuple[str, bool, str]] = []

    problems: list[str] = []
    if not isinstance(material, Mapping) or not material:
        problems.append("blinded material must be a non-empty mapping")
    for index, excerpt in enumerate(raw_excerpts):
        if not isinstance(excerpt, Mapping):
            problems.append(f"excerpt {index} is not a mapping")
            continue
        for field_name in ("source_id", "span_start", "span_end", "original_text", "language"):
            if field_name not in excerpt:
                problems.append(f"excerpt {index} missing {field_name}")
    checks.append(("SCHEMA_VALIDATION", not problems, "; ".join(problems) or "well-formed"))

    missing_sources = sorted({str(e.get("source_id")) for e in excerpts
                              if e.get("source_id") not in source_lookup})
    checks.append(("SOURCE_EXISTENCE", not missing_sources,
                   "; ".join(f"unknown source {s}" for s in missing_sources) or "all sources resolve"))

    problems = []
    for index, excerpt in enumerate(excerpts):
        text = _source_text(source_lookup, excerpt.get("source_id"))
        start, end = excerpt.get("span_start"), excerpt.get("span_end")
        if text is None:
            problems.append(f"excerpt {index}: source unresolvable")
        elif not isinstance(start, int) or not isinstance(end, int) or not 0 <= start < end <= len(text):
            problems.append(f"excerpt {index}: span [{start},{end}) does not resolve in document")
    checks.append(("SPAN_EXISTENCE", not problems, "; ".join(problems) or "all spans resolve"))

    derived = classify_context(packet)
    context_ok = derived != "PACKET_CONSTRUCTION_DEFECT" and derived == packet.context_class
    checks.append(("CONTEXT_SUFFICIENCY", context_ok,
                   f"derived {derived}; declared {packet.context_class}"))

    problems = []
    for index, excerpt in enumerate(excerpts):
        text = _source_text(source_lookup, excerpt.get("source_id"))
        start, end = excerpt.get("span_start"), excerpt.get("span_end")
        if text is None or not isinstance(start, int) or not isinstance(end, int) \
                or not 0 <= start < end <= len(text):
            problems.append(f"excerpt {index}: unverifiable mapping")
        elif text[start:end] != excerpt.get("original_text"):
            problems.append(f"excerpt {index}: excerpt text diverges from document span")
    checks.append(("MAPPING_VERIFICATION", not problems, "; ".join(problems) or "excerpts match document text"))

    findings = leakage_scan(packet.public_payload(), sealed_answer)
    checks.append(("LABEL_LEAKAGE_SCAN", not findings,
                   "; ".join(f"{f['kind']} at {f['path']}" for f in findings) or "no leakage findings"))

    hits = _answer_key_hits(packet.public_payload())
    checks.append(("ANSWER_KEY_METADATA_SCAN", not hits,
                   "; ".join(f"answer-key field '{key}' at {path}" for path, key in hits)
                   or "no answer-key metadata"))

    instructions = str(material.get("reviewer_instructions") or "")
    problems = []
    if len(instructions.split()) < 15 or "." not in instructions:
        problems.append("instructions must be plain-language sentences")
    if sum(1 for label in REVIEW_DECISIONS if label in instructions) < 3:
        problems.append("instructions must define the decision labels")
    for required_label in ("EPISTEMICALLY_UNRESOLVABLE", "CANNOT_ADJUDICATE"):
        if required_label not in instructions:
            problems.append(f"instructions must permit {required_label}")
    checks.append(("REVIEWER_INSTRUCTION_VALIDATION", not problems,
                   "; ".join(problems) or "instructions define labels and permit abstention"))

    problems = []
    releasability = packet.access_marking.get("releasability")
    if not isinstance(releasability, (list, tuple)) or not releasability:
        problems.append("access marking requires a releasability list")
    elif REVIEW_ENGINE_ONLY in releasability:
        problems.append("reviewer packet cannot carry the sealed engine marking")
    checks.append(("ACCESS_MARKING_VALIDATION", not problems, "; ".join(problems) or "marking valid"))

    problems = []
    try:
        canonical_json(packet.public_payload())
    except (TypeError, ValueError) as error:
        problems.append(f"payload not renderable: {error}")
    for kind, path, value in _walk(material):
        if kind == "str" and any(ch < " " and ch not in "\n\t" for ch in value):
            problems.append(f"control characters at {path}")
            break
    checks.append(("RENDERING_VALIDATION", not problems, "; ".join(problems) or "payload renders"))

    problems = []
    for index, excerpt in enumerate(excerpts):
        if not str(excerpt.get("original_text") or "").strip():
            problems.append(f"excerpt {index}: missing original-language text")
        if not str(excerpt.get("language") or "").strip():
            problems.append(f"excerpt {index}: missing language tag")
    checks.append(("ORIGINAL_LANGUAGE_PRESENCE", not problems,
                   "; ".join(problems) or "original language present"))

    problems = []
    for index, excerpt in enumerate(excerpts):
        merged_keys = [key for key in excerpt if _tokens(key) & {"merged", "combined"}]
        if merged_keys:
            problems.append(f"excerpt {index}: merged text fields {sorted(merged_keys)}")
        translation = excerpt.get("translation")
        if translation is None:
            continue
        original_norm = _normalize(excerpt.get("original_text") or "")
        translation_norm = _normalize(translation)
        if not str(excerpt.get("translation_language") or "").strip():
            problems.append(f"excerpt {index}: translation without language tag")
        if not translation_norm or translation_norm == original_norm:
            problems.append(f"excerpt {index}: translation duplicates original")
        elif translation_norm in original_norm or original_norm in translation_norm:
            problems.append(f"excerpt {index}: original and translation are merged, not separate fields")
    checks.append(("TRANSLATION_SEPARATION", not problems,
                   "; ".join(problems) or "original and translation kept separate"))

    if packet.surface == _ORIGIN:
        problems = []
        rows = material.get("role_metadata") or ()
        if not rows:
            problems.append("origin packet requires role metadata rows")
        for index, row in enumerate(rows):
            if not isinstance(row, Mapping) or row.get("kind") not in ROLE_EVIDENCE_KINDS:
                problems.append(f"role metadata {index}: kind must come from the role-evidence vocabulary")
            elif not str(row.get("value") or "").strip():
                problems.append(f"role metadata {index}: empty value")
        checks.append(("ROLE_SPECIFIC_CONTEXT_VALIDATION", not problems,
                       "; ".join(problems) or "role context present"))
    else:
        checks.append(("ROLE_SPECIFIC_CONTEXT_VALIDATION", True, "not applicable to this surface"))

    if packet.surface == _DEPENDENCE:
        problems = []
        rows = material.get("dependence_evidence") or ()
        if not rows:
            problems.append("dependence packet requires its evidence dossier")
        for index, row in enumerate(rows):
            if not isinstance(row, Mapping) or row.get("kind") not in DEPENDENCE_SIGNAL_KINDS:
                problems.append(f"dependence evidence {index}: kind must come from the signal vocabulary")
            elif not str(row.get("detail") or "").strip():
                problems.append(f"dependence evidence {index}: empty detail")
        sides = sorted({str(e.get("source_id")) for e in excerpts if e.get("source_id") is not None})
        if len(sides) < 2:
            problems.append("dependence packet requires both-side excerpts")
        for side in sides:
            if side not in (material.get("publication_timing") or {}):
                problems.append(f"missing publication timing for {side}")
            if side not in (material.get("source_metadata") or {}):
                problems.append(f"missing source metadata for {side}")
        checks.append(("DEPENDENCE_EVIDENCE_SUFFICIENCY", not problems,
                       "; ".join(problems) or "dependence dossier complete"))
    else:
        checks.append(("DEPENDENCE_EVIDENCE_SUFFICIENCY", True, "not applicable to this surface"))

    return tuple(checks)


def _index_hash(rows: Sequence[Mapping[str, Any]]) -> str:
    return sha256([[row["packet_id"], row["frozen_content_hash"]] for row in rows])


def freeze_corpus(packets: Sequence[ReviewPacket], sealed_answers: Mapping[str, str],
                  reserves: Sequence[ReviewPacket], output_root: Path | str, *,
                  builder_entries: Sequence[SealedBuilderEntry],
                  source_lookup: Mapping[str, Any] | None = None) -> dict[str, Any]:
    packets, reserves = tuple(packets), tuple(reserves)
    output_root = Path(output_root)
    strata: dict[str, str] = {}
    for entry in builder_entries:
        if not isinstance(entry, SealedBuilderEntry):
            raise ValueError("builder manifest accepts only sealed builder entries")
        if strata.get(entry.packet_id, entry.sampling_stratum) != entry.sampling_stratum:
            raise ValueError(f"conflicting sealed strata for {entry.packet_id}")
        strata[entry.packet_id] = entry.sampling_stratum

    everything = packets + reserves
    identifiers = [p.packet_id for p in everything]
    if len(set(identifiers)) != len(identifiers):
        raise ValueError("duplicate packet ids in freeze")
    for packet in packets:
        if packet.reserve:
            raise ValueError(f"reserve-flagged packet in active set: {packet.packet_id}")
    for packet in reserves:
        if not packet.reserve:
            raise ValueError(f"active-flagged packet in reserve pool: {packet.packet_id}")

    for packet in everything:
        if packet.packet_id not in sealed_answers:
            raise ValueError(f"missing sealed answer for {packet.packet_id}")
        if packet.packet_id not in strata:
            raise ValueError(f"missing sealed stratum for {packet.packet_id}")
        findings = leakage_scan(packet.public_payload(), sealed_answers[packet.packet_id])
        if findings:
            raise ValueError(
                f"leakage violations block freeze for {packet.packet_id}: "
                + "; ".join(f"{f['kind']} at {f['path']}" for f in findings))
        derived = classify_context(packet)
        if derived == "PACKET_CONSTRUCTION_DEFECT":
            raise ValueError(f"packet construction defect blocks freeze (context): {packet.packet_id}")
        if derived != packet.context_class:
            raise ValueError(
                f"misdeclared context class for {packet.packet_id}: derived {derived}, "
                f"declared {packet.context_class}")
        if source_lookup is not None:
            failed = [check for check in validate_packet(
                packet, source_lookup, sealed_answer=sealed_answers[packet.packet_id])
                if not check[1]]
            if failed:
                raise ValueError(
                    f"pre-freeze validation failed for {packet.packet_id}: "
                    + "; ".join(f"{name}: {detail}" for name, _ok, detail in failed))

    active_by_surface: dict[str, int] = {}
    reserve_by_surface: dict[str, int] = {}
    active_by_stratum: dict[tuple[str, str], int] = {}
    reserve_by_stratum: dict[tuple[str, str], int] = {}
    for packet in packets:
        active_by_surface[packet.surface] = active_by_surface.get(packet.surface, 0) + 1
        key = (packet.surface, strata[packet.packet_id])
        active_by_stratum[key] = active_by_stratum.get(key, 0) + 1
    for packet in reserves:
        reserve_by_surface[packet.surface] = reserve_by_surface.get(packet.surface, 0) + 1
        key = (packet.surface, strata[packet.packet_id])
        reserve_by_stratum[key] = reserve_by_stratum.get(key, 0) + 1
    for surface, active_count in active_by_surface.items():
        ratio = reserve_by_surface.get(surface, 0) / active_count
        if ratio + 1e-9 < MINIMUM_RESERVE_RATIO:
            raise ValueError(f"insufficient reserve pool for surface {surface}: ratio {ratio:.2f}")
    for (surface, stratum), active_count in active_by_stratum.items():
        ratio = reserve_by_stratum.get((surface, stratum), 0) / active_count
        if ratio + 1e-9 < MINIMUM_RESERVE_RATIO:
            raise ValueError(
                f"insufficient reserve pool for stratum '{stratum}' on {surface}: ratio {ratio:.2f}")

    output_root.mkdir(parents=True, exist_ok=True)
    if (output_root / "frozen_packets.jsonl").exists():
        raise ValueError("freeze target already holds a frozen corpus")

    sorted_active = sorted((p.to_record() for p in packets), key=lambda row: row["packet_id"])
    sorted_reserve = sorted((p.to_record() for p in reserves), key=lambda row: row["packet_id"])
    (output_root / "frozen_packets.jsonl").write_text(
        "".join(canonical_json(row) + "\n" for row in sorted_active), encoding="utf-8")
    (output_root / "reserve_packets.jsonl").write_text(
        "".join(canonical_json(row) + "\n" for row in sorted_reserve), encoding="utf-8")

    answers_used = {pid: sealed_answers[pid] for pid in sorted(identifiers)}
    strata_used = {pid: strata[pid] for pid in sorted(identifiers)}
    (output_root / "sealed_answers.json").write_text(canonical_json(
        {"access_marking": {"releasability": [REVIEW_ENGINE_ONLY]}, "answers": answers_used}),
        encoding="utf-8")
    (output_root / "sealed_builder_manifest.json").write_text(canonical_json(
        {"access_marking": {"releasability": [REVIEW_ENGINE_ONLY]}, "strata": strata_used}),
        encoding="utf-8")

    context_counts: dict[str, int] = {}
    for packet in packets:
        context_counts[packet.context_class] = context_counts.get(packet.context_class, 0) + 1
    manifest = {
        "milestone": "CURUNIR_EPISTEMIC_CAPABILITY_CLOSURE_AND_CLEAN_GENERALIZATION_V5_1",
        "contract_section": 18,
        "frozen": True,
        "output_root": str(output_root),
        "packet_count": len(packets),
        "reserve_count": len(reserves),
        "surface_counts": dict(sorted(active_by_surface.items())),
        "reserve_counts_by_surface": dict(sorted(reserve_by_surface.items())),
        "reserve_ratio_by_surface": {
            surface: round(reserve_by_surface.get(surface, 0) / count, 4)
            for surface, count in sorted(active_by_surface.items())},
        "minimum_reserve_ratio": MINIMUM_RESERVE_RATIO,
        "stratum_reserve_check": "ENFORCED_PER_SURFACE_AND_STRATUM",
        "context_class_counts": dict(sorted(context_counts.items())),
        "packet_index_hash": _index_hash(sorted_active),
        "reserve_index_hash": _index_hash(sorted_reserve),
        "sealed_answers_hash": sha256(answers_used),
        "builder_manifest_hash": sha256(strata_used),
        "files": {"frozen_packets": "frozen_packets.jsonl",
                  "reserve_packets": "reserve_packets.jsonl",
                  "sealed_answers": "sealed_answers.json",
                  "sealed_builder_manifest": "sealed_builder_manifest.json",
                  "replacements": "replacements.jsonl"},
    }
    (output_root / "manifest.json").write_text(canonical_json(manifest), encoding="utf-8")
    return manifest


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def replace_defective(manifest: Mapping[str, Any], packet_id: str, reason: str) -> dict[str, Any]:
    """Promote a pre-frozen matching-surface-and-stratum reserve.  Append-only
    log; the frozen files are never rewritten (original preserved)."""
    if len(str(reason).strip()) < 10:
        raise ValueError("defect replacement requires a substantive reason")
    root = Path(manifest["output_root"])
    actives = _read_jsonl(root / "frozen_packets.jsonl")
    reserves = _read_jsonl(root / "reserve_packets.jsonl")
    sealed = json.loads((root / "sealed_builder_manifest.json").read_text(encoding="utf-8"))
    strata = sealed["strata"]
    if _index_hash(actives) != manifest["packet_index_hash"] \
            or _index_hash(reserves) != manifest["reserve_index_hash"] \
            or sha256(strata) != manifest["builder_manifest_hash"]:
        raise ValueError("post-freeze modification detected; replacement refused")
    by_id = {row["packet_id"]: row for row in actives}
    if packet_id not in by_id:
        raise ValueError("only frozen active packets can be replaced")
    log_path = root / "replacements.jsonl"
    log = _read_jsonl(log_path)
    if any(row["defective_packet_id"] == packet_id for row in log):
        raise ValueError(f"packet already replaced; the log is append-only: {packet_id}")
    promoted_before = {row["promoted_reserve_packet_id"] for row in log}
    surface, stratum = by_id[packet_id]["surface"], strata[packet_id]
    eligible = sorted(row["packet_id"] for row in reserves
                      if row["surface"] == surface
                      and strata.get(row["packet_id"]) == stratum
                      and row["packet_id"] not in promoted_before)
    if not eligible:
        raise ValueError(
            "no eligible pre-frozen reserve for this surface and stratum; "
            "replacement explicitly blocked rather than silently degraded")
    record = {
        "replacement_id": stable_id("v5-1-replacement", packet_id, eligible[0]),
        "defective_packet_id": packet_id,
        "promoted_reserve_packet_id": eligible[0],
        "surface": surface,
        "reason": str(reason).strip(),
        "matching_stratum_verified": True,
        "original_preserved": True,
        "recorded_time": now_utc(),
    }
    with open(log_path, "a", encoding="utf-8") as handle:
        handle.write(canonical_json(record) + "\n")
    return record
