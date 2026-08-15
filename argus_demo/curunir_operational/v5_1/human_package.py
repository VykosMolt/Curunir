"""Human-review package (contract Section 27).

Converts held-out review packets into packets a person with no knowledge of
this system can answer: plain-language question and instructions, every
permitted answer defined inside the packet, original-language text kept
separate from any translation, source information exposed, and the system's
own answers hidden for the independent-label pass.

Nothing in this module produces or claims a human result.  Every artifact it
writes states HUMAN_REVIEW_PENDING, and the study-design validator refuses
any field that pretends a human outcome already exists (Section 27.4).

Composes the frozen packet register format written by ``v5_1.packets`` and the
Section 9 outcome model from ``v5_1.models``; sufficient-but-uninterpretable
material becomes an explicit SYSTEM_CAPABILITY_FAILURE record, never a silent
skip.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator, Mapping

from ..v4.io import append_jsonl, read_json, read_jsonl, write_json
from ..v4.models import require_aware
from ..v5.models import SURFACES
from .models import (
    REVIEW_DECISIONS, Record, canonical_json, capability_outcome, now_utc,
    sha256, stable_id,
)

MILESTONE = "CURUNIR_EPISTEMIC_CAPABILITY_CLOSURE_AND_CLEAN_GENERALIZATION_V5_1"
HUMAN_REVIEW_STATE = "HUMAN_REVIEW_PENDING"
# Deterministic packet timestamp: package content hashes must not depend on
# wall clock (summary/manifest recording times still come from now_utc()).
HUMAN_PACKAGE_BUILD_TIME = "2026-07-24T12:00:00+00:00"

# Section 27.2 — the four review modes.
REVIEW_MODES = (
    "INDEPENDENT_LABEL",
    "CURUNIR_OUTPUT_VERIFICATION",
    "DISAGREEMENT_ADJUDICATION",
    "DOMAIN_EXPERT_ESCALATION",
)

# Internal vocabulary that must never appear in the prose a human reviewer
# reads.  Label tokens (ALL_CAPS) are masked before the scan: naming a
# permitted answer such as EPISTEMICALLY_UNRESOLVABLE is required, describing
# it in jargon is not.
JARGON_DENYLIST = (
    "stratum", "strata", "sampling", "adjudication boundary", "adjudication",
    "adjudicable", "epistemic surface", "epistemic", "epistemically",
    "blinded", "blinding", "held out", "heldout", "reserve pool",
    "answer key", "sealed answer", "sealed", "leakage", "corpus", "freeze",
    "frozen", "consensus", "capability outcome", "capability failure",
    "manifestation", "provenance", "ontology", "vocabulary",
)

_ALL_CAPS_TOKEN = re.compile(r"\b[A-Z][A-Z0-9_]{2,}\b")


def _lint_normalize(text: str) -> str:
    masked = _ALL_CAPS_TOKEN.sub(" ", str(text))
    return " ".join(masked.casefold().replace("-", " ").replace("_", " ").split())


_JARGON_PATTERNS = tuple(
    (phrase, re.compile(rf"(?<![a-z0-9]){re.escape(_lint_normalize(phrase))}(?![a-z0-9])"))
    for phrase in JARGON_DENYLIST)


def plain_language_lint(text: str) -> tuple[str, ...]:
    """Return every denylisted jargon phrase found in reviewer-facing prose.

    ALL_CAPS answer labels are masked first: the closed answer menu is part of
    the packet contract, the surrounding prose must stay plain."""
    normalized = _lint_normalize(text)
    return tuple(phrase for phrase, pattern in _JARGON_PATTERNS
                 if pattern.search(normalized))


# Plain-language question per surface.  General mechanisms only: the question
# describes the judgement type, never a case.
SURFACE_QUESTIONS: Mapping[str, str] = {
    SURFACES[0]: ("Look at the highlighted passage. Does it really say what the record "
                  "next to it claims it says, at exactly the quoted position in the document?"),
    SURFACES[1]: ("Who wrote, published, issued, or provided this document? Judge whether "
                  "each stated role matches the evidence shown in this packet."),
    SURFACES[2]: ("Do these documents report the same information on their own, or does one "
                  "copy, translate, quote, or otherwise rely on the other?"),
    SURFACES[3]: ("Does the quoted evidence actually back up the statement being checked, "
                  "about the same subject, at the same time, and in the same sense?"),
    SURFACES[4]: ("Compare the two statements. Do they genuinely disagree, or is one an "
                  "update, a correction, or a withdrawal of the other?"),
    SURFACES[5]: ("Read the report sentence and the evidence behind it. Does the sentence "
                  "faithfully reflect that evidence, without adding, dropping, or "
                  "overstating anything important?"),
}

BASE_INSTRUCTIONS = (
    "Read everything in this packet before answering: the question, every quoted "
    "passage in its original language, any translation supplied beside it, and the "
    "information about where each document came from. Then choose exactly one of the "
    "listed answers; every answer choice is defined in this packet in plain words. "
    "If the packet shows that the publicly available record itself cannot settle the "
    "question, choose EPISTEMICALLY_UNRESOLVABLE. If you cannot reach a judgement for "
    "any other reason, choose CANNOT_ADJUDICATE. Both of these choices are always "
    "acceptable; never guess. Do not use outside knowledge and do not search for more "
    "material; judge only what is inside this packet."
)

# Every permitted answer, defined in plain words.  Keys are exactly the
# REVIEW_DECISIONS vocabulary from v5_1.models — no private label set.
LABEL_DEFINITIONS: Mapping[str, str] = {
    "CORRECT": ("The statement or relationship being checked is right, given the "
                "material in this packet, with nothing important missing or changed."),
    "INCORRECT": ("The statement or relationship being checked is wrong, given the "
                  "material in this packet."),
    "PARTIALLY_CORRECT": ("Part of what is being checked is right, but another "
                          "important part is wrong or missing."),
    "INSUFFICIENT_INFORMATION": ("This packet does not contain enough material for "
                                 "you to decide either way."),
    "AMBIGUOUS": ("The material can honestly be read in more than one way, and "
                  "different readings lead to different answers."),
    "CANNOT_ADJUDICATE": ("You cannot reach a judgement for some other reason; "
                          "choosing this is always better than guessing."),
    "PACKET_DEFECT": ("Something in this packet is broken, for example a quoted "
                      "passage that does not match its document, so the question "
                      "cannot be answered as asked."),
    "EPISTEMICALLY_UNRESOLVABLE": ("The packet shows that the publicly available "
                                   "record itself cannot settle this question, no "
                                   "matter who reviews it."),
}

# Section 9 subject kind for each surface's task unit.
_SURFACE_SUBJECT_KIND: Mapping[str, str] = {
    SURFACES[0]: "EXTRACTION_CANDIDATE",
    SURFACES[1]: "SOURCE_ROLE",
    SURFACES[2]: "SOURCE_DEPENDENCE_RELATION",
    SURFACES[3]: "CLAIM_SUPPORT_RELATION",
    SURFACES[4]: "CONTRADICTION_UPDATE_RELATION",
    SURFACES[5]: "REPORT_PROPOSITION",
}

_ANSWER_KEY_TOKENS = frozenset({"answer", "expected", "verdict", "gold", "truth"})
_EXEMPT_FIELD_NAMES = frozenset({"answers_hidden"})


def _key_tokens(key: str) -> frozenset[str]:
    return frozenset(t for t in re.split(r"[^a-z0-9]+", str(key).casefold()) if t)


def _walk(value: Any, path: str = "$") -> Iterator[tuple[str, str, str]]:
    if isinstance(value, Mapping):
        for key, item in value.items():
            yield ("key", f"{path}.{key}", str(key))
            yield from _walk(item, f"{path}.{key}")
    elif isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            yield from _walk(item, f"{path}[{index}]")
    elif isinstance(value, str):
        yield ("str", path, value)


def _answer_field_hits(value: Any) -> tuple[str, ...]:
    return tuple(path for kind, path, name in _walk(value)
                 if kind == "key" and name not in _EXEMPT_FIELD_NAMES
                 and _key_tokens(name) & _ANSWER_KEY_TOKENS)


def _normalize_value(value: Any) -> str:
    return " ".join(str(value).split()).casefold()


def _answer_value_hits(value: Any, sealed_answer: str) -> tuple[str, ...]:
    needle = _normalize_value(sealed_answer)
    if not needle:
        return ()
    return tuple(path for kind, path, text in _walk(value)
                 if kind == "str" and needle in _normalize_value(text))


class ConversionFailure(ValueError):
    """A packet whose material exists but cannot be turned into a reviewable
    human packet.  Always surfaced as SYSTEM_CAPABILITY_FAILURE, never skipped."""

    def __init__(self, failure_class: str, message: str) -> None:
        super().__init__(message)
        self.failure_class = failure_class


@dataclass(frozen=True)
class HumanReviewPacket(Record):
    """One packet a human reviewer answers without system knowledge."""

    human_packet_id: str
    source_packet_id: str
    surface: str
    mode: str
    question: str
    instructions: str
    label_definitions: Mapping[str, str]
    materials: Mapping[str, Any]
    source_metadata: Mapping[str, Any]
    answers_hidden: bool
    created_time: str

    def __post_init__(self) -> None:
        if self.surface not in SURFACES:
            raise ValueError(f"unknown packet surface: {self.surface}")
        if self.mode not in REVIEW_MODES:
            raise ValueError(f"unknown review mode: {self.mode}")
        if self.mode == "INDEPENDENT_LABEL" and not self.answers_hidden:
            raise ValueError("INDEPENDENT_LABEL packets must hide the system's answers")
        require_aware(self.created_time)
        if not str(self.question).strip():
            raise ValueError("human packet requires a question")
        if len(self.instructions.split()) < 30 or "." not in self.instructions:
            raise ValueError("instructions must be substantive plain-language sentences")
        for required in ("EPISTEMICALLY_UNRESOLVABLE", "CANNOT_ADJUDICATE"):
            if required not in self.instructions:
                raise ValueError(f"instructions must permit {required}")
        if set(self.label_definitions) != set(REVIEW_DECISIONS):
            raise ValueError("every permitted answer label must be defined in the packet, "
                             "and only permitted labels may appear")
        for label, definition in self.label_definitions.items():
            if len(str(definition).split()) < 5:
                raise ValueError(f"label {label} requires a substantive plain-language definition")
        prose = " ".join((self.question, self.instructions, *self.label_definitions.values()))
        findings = plain_language_lint(prose)
        if findings:
            raise ValueError(f"reviewer-facing prose contains internal jargon: {sorted(findings)}")
        if not isinstance(self.materials, Mapping) or not self.materials:
            raise ValueError("human packet requires reviewable material")
        if not isinstance(self.source_metadata, Mapping) or not self.source_metadata:
            raise ValueError("human packet must expose source metadata")
        hits = _answer_field_hits({"materials": self.materials,
                                   "source_metadata": self.source_metadata})
        if hits:
            raise ValueError(f"human packet carries answer-like fields: {sorted(hits)}")
        self._check_translation_separation()

    def _check_translation_separation(self) -> None:
        for index, excerpt in enumerate(self.materials.get("excerpts") or ()):
            if not isinstance(excerpt, Mapping):
                raise ValueError(f"excerpt {index} is not a mapping")
            original = str(excerpt.get("original_text") or "")
            if not original.strip() or not str(excerpt.get("language") or "").strip():
                raise ValueError(f"excerpt {index} requires original-language text and a language tag")
            translation = excerpt.get("translation")
            if translation is None:
                continue
            if not str(excerpt.get("translation_language") or "").strip():
                raise ValueError(f"excerpt {index}: translation requires its own language tag")
            original_norm = _normalize_value(original)
            translation_norm = _normalize_value(translation)
            if not translation_norm or translation_norm == original_norm \
                    or translation_norm in original_norm or original_norm in translation_norm:
                raise ValueError(f"excerpt {index}: translation must be a separate field, "
                                 "not merged with or duplicating the original text")


def _derive_source_metadata(material: Mapping[str, Any]) -> dict[str, Any]:
    metadata: dict[str, Any] = {}
    declared = material.get("source_metadata")
    if isinstance(declared, Mapping):
        metadata.update({str(key): value for key, value in declared.items()})
    for excerpt in material.get("excerpts") or ():
        if isinstance(excerpt, Mapping) and excerpt.get("source_id") is not None:
            metadata.setdefault(str(excerpt["source_id"]),
                                {"language": str(excerpt.get("language") or "")})
    for row in material.get("structured_basis") or ():
        if isinstance(row, Mapping) and row.get("source_id") is not None:
            metadata.setdefault(str(row["source_id"]),
                                {"listed_in_evidence_table": True})
    return metadata


def convert_packet(row: Mapping[str, Any], *,
                   sealed_answer: str | None = None) -> HumanReviewPacket:
    """Convert one frozen register row into an INDEPENDENT_LABEL human packet.

    A corrupted register (unknown surface, missing id) is a hard ValueError.
    Material that exists but cannot be made reviewable raises
    ``ConversionFailure`` for the caller to record as SYSTEM_CAPABILITY_FAILURE.
    """
    surface = row.get("surface")
    if surface not in SURFACES:
        raise ValueError(f"unknown surface in review register: {surface!r}")
    packet_id = str(row.get("packet_id") or "")
    if not packet_id:
        raise ValueError("register row missing its packet id")
    material = row.get("blinded_material")
    if not isinstance(material, Mapping) or not material:
        raise ConversionFailure("CONTEXT_INSUFFICIENT_BY_CONSTRUCTION",
                                f"packet {packet_id} carries no reviewable material")
    materials = {key: value for key, value in material.items()
                 if key != "reviewer_instructions"}
    source_metadata = _derive_source_metadata(material)
    if not source_metadata:
        raise ConversionFailure(
            "CONTEXT_INSUFFICIENT_BY_CONSTRUCTION",
            f"packet {packet_id}: no source-level information is interpretable from its "
            "material, so source metadata cannot be exposed to the reviewer")
    try:
        packet = HumanReviewPacket(
            human_packet_id=stable_id("v5-1-human-packet", "INDEPENDENT_LABEL", packet_id),
            source_packet_id=packet_id,
            surface=surface,
            mode="INDEPENDENT_LABEL",
            question=SURFACE_QUESTIONS[surface],
            instructions=BASE_INSTRUCTIONS,
            label_definitions=dict(sorted(LABEL_DEFINITIONS.items())),
            materials=json.loads(canonical_json(materials)),
            source_metadata=json.loads(canonical_json(source_metadata)),
            answers_hidden=True,
            created_time=HUMAN_PACKAGE_BUILD_TIME,
        )
    except ConversionFailure:
        raise
    except ValueError as error:
        raise ConversionFailure("OTHER_SOFTWARE_DEFECT",
                                f"packet {packet_id}: {error}") from error
    if sealed_answer:
        hits = _answer_value_hits(packet.to_record(), sealed_answer)
        if hits:
            raise ConversionFailure(
                "OTHER_SOFTWARE_DEFECT",
                f"packet {packet_id}: the system's own answer would be visible to the "
                f"reviewer at {sorted(hits)}; packet refused")
    return packet


def _load_register(root: Path, packets_file: str = "frozen_packets.jsonl") \
        -> tuple[list[dict[str, Any]], dict[str, str]]:
    rows_path = root / packets_file
    rows = read_jsonl(rows_path) if rows_path.is_file() else []
    answers: dict[str, str] = {}
    sealed_path = root / "sealed_answers.json"
    if sealed_path.is_file():
        sealed = read_json(sealed_path)
        if isinstance(sealed, Mapping) and isinstance(sealed.get("answers"), Mapping):
            answers = {str(key): str(value) for key, value in sealed["answers"].items()}
    return rows, answers


def build_human_packets(heldout_manifest_path: str | Path,
                        campaign_roots: Mapping[str, str | Path],
                        output_root: str | Path, minimum: int = 100) -> dict[str, Any]:
    """Convert the held-out register (plus campaign registers) into
    INDEPENDENT_LABEL human-review packets across all six surfaces.

    Writes ``human_packets.jsonl`` (append-only, refuses an existing package),
    an explicit shortfall record when fewer than ``minimum`` packets or any
    surface is missing, and one SYSTEM_CAPABILITY_FAILURE record per packet
    that could not be converted — never a silent skip.
    """
    if int(minimum) < 1:
        raise ValueError("minimum packet count must be at least 1")
    manifest_path = Path(heldout_manifest_path)
    output = Path(output_root)
    packets_path = output / "human_packets.jsonl"
    if packets_path.exists():
        raise ValueError("output root already holds a human-review package; append-only")

    manifest = read_json(manifest_path)
    if not isinstance(manifest, Mapping):
        raise ValueError("held-out manifest must be a JSON object")
    frozen_name = str((manifest.get("files") or {}).get("frozen_packets",
                                                        "frozen_packets.jsonl"))
    heldout_rows, heldout_answers = _load_register(manifest_path.parent, frozen_name)
    if not heldout_rows:
        raise ValueError("held-out register contains no packets")

    ordered: list[tuple[str, dict[str, Any], str | None]] = []
    seen: set[str] = set()
    duplicates = 0
    registers = [("HELD_OUT", heldout_rows, heldout_answers)]
    for label, root in sorted((campaign_roots or {}).items()):
        rows, answers = _load_register(Path(root))
        registers.append((str(label), rows, answers))
    for label, rows, answers in registers:
        for row in rows:
            packet_id = str(row.get("packet_id") or "")
            if packet_id and packet_id in seen:
                duplicates += 1
                continue
            if packet_id:
                seen.add(packet_id)
            ordered.append((label, row, answers.get(packet_id)))

    converted: list[HumanReviewPacket] = []
    failures: list[dict[str, Any]] = []
    for label, row, answer in ordered:
        try:
            converted.append(convert_packet(row, sealed_answer=answer))
        except ConversionFailure as failure:
            outcome = capability_outcome(
                subject_kind=_SURFACE_SUBJECT_KIND[row["surface"]],
                subject_id=str(row.get("packet_id")),
                outcome="SYSTEM_CAPABILITY_FAILURE",
                rationale=str(failure),
                capability_failure_class=failure.failure_class)
            failures.append({**outcome.to_record(), "register": label})

    converted.sort(key=lambda packet: (packet.surface, packet.human_packet_id))
    output.mkdir(parents=True, exist_ok=True)
    append_jsonl(packets_path, (packet.to_record() for packet in converted))
    if failures:
        append_jsonl(output / "capability_failures.jsonl", failures)

    counts: dict[str, int] = {}
    for packet in converted:
        counts[packet.surface] = counts.get(packet.surface, 0) + 1
    missing_surfaces = [surface for surface in SURFACES if not counts.get(surface)]

    shortfall: dict[str, Any] | None = None
    reasons: list[str] = []
    if len(converted) < int(minimum):
        reasons.append("PACKET_COUNT_SHORTFALL")
    if missing_surfaces:
        reasons.append("SURFACE_COVERAGE_SHORTFALL")
    if reasons:
        shortfall = {
            "explicit": True,
            "reasons": reasons,
            "required_minimum": int(minimum),
            "actual_packet_count": len(converted),
            "missing_surfaces": missing_surfaces,
            "recorded_time": now_utc(),
        }
        write_json(output / "shortfall_record.json", shortfall, refuse_existing=True)

    summary = {
        "milestone": MILESTONE,
        "contract_section": 27,
        "mode": "INDEPENDENT_LABEL",
        "curunir_answers_hidden": True,
        "sealed_answers_copied": False,
        "human_review_state": HUMAN_REVIEW_STATE,
        "packet_count": len(converted),
        "minimum_required": int(minimum),
        "counts_by_surface": dict(sorted(counts.items())),
        "missing_surfaces": missing_surfaces,
        "capability_failures": len(failures),
        "duplicate_source_packets_skipped": duplicates,
        "shortfall": shortfall,
        "packet_index_hash": sha256(
            [[packet.human_packet_id, sha256(packet.to_record())] for packet in converted]),
        "created_time": now_utc(),
    }
    write_json(output / "build_summary.json", summary, refuse_existing=True)
    return summary


_BASE_PACKET_FIELDS = ["question", "instructions", "label_definitions",
                       "materials", "source_metadata"]


def review_modes(output_path: str | Path) -> dict[str, Any]:
    """Section 27.2 — the four review modes with per-mode packet field contracts."""
    document = {
        "milestone": MILESTONE,
        "contract_section": "27.2",
        "human_review_state": HUMAN_REVIEW_STATE,
        "modes": {
            "INDEPENDENT_LABEL": {
                "purpose": ("The reviewer answers the packet question from the packet "
                            "material alone, without ever seeing the system's answer."),
                "system_answer_visibility": "HIDDEN",
                "system_answer_release": "NEVER_DURING_REVIEW",
                "required_packet_fields": list(_BASE_PACKET_FIELDS),
                "forbidden_packet_fields": ["system_answer", "curunir_answer",
                                            "expected_answer", "prior_reviews"],
            },
            "CURUNIR_OUTPUT_VERIFICATION": {
                "purpose": ("The reviewer first records an independent answer, and only "
                            "after that record exists is the system's answer revealed "
                            "for comparison."),
                "system_answer_visibility": "REVEALED_AFTER_INDEPENDENT_PASS",
                "system_answer_release": "AFTER_INDEPENDENT_PASS_RECORDED",
                "required_packet_fields": list(_BASE_PACKET_FIELDS) + [
                    "independent_pass_record_reference",
                    "system_answer_sealed_until_release"],
                "forbidden_packet_fields": ["system_answer_before_independent_pass"],
            },
            "DISAGREEMENT_ADJUDICATION": {
                "purpose": ("A third reviewer settles a case where the first two "
                            "reviewers gave different answers, seeing both prior "
                            "reviews and the system's answer."),
                "system_answer_visibility": "REVEALED_WITH_PRIOR_REVIEWS",
                "system_answer_release": "AT_ASSIGNMENT",
                "required_packet_fields": list(_BASE_PACKET_FIELDS) + [
                    "first_review_record", "second_review_record",
                    "disagreement_description"],
                "forbidden_packet_fields": [],
            },
            "DOMAIN_EXPERT_ESCALATION": {
                "purpose": ("A reviewer with subject-matter knowledge answers a case "
                            "the general reviewers marked as needing expertise, with "
                            "the full review history visible."),
                "system_answer_visibility": "REVEALED_WITH_FULL_REVIEW_HISTORY",
                "system_answer_release": "AT_ASSIGNMENT",
                "required_packet_fields": list(_BASE_PACKET_FIELDS) + [
                    "escalation_reason", "domain_of_expertise",
                    "full_review_history"],
                "forbidden_packet_fields": [],
            },
        },
    }
    if tuple(document["modes"]) != REVIEW_MODES:
        raise ValueError("mode document must define exactly the four Section 27.2 modes")
    write_json(Path(output_path), document, refuse_existing=True)
    return document


# Keys that would claim a human result before any human review happened.
_RESULT_KEY_TOKENS = frozenset({"results", "result", "accuracy", "kappa"})
_AGREEMENT_MODIFIER_TOKENS = frozenset({"measured", "observed", "score", "rate", "value"})


def _result_key_hits(value: Any) -> tuple[str, ...]:
    hits: list[str] = []
    for kind, path, name in _walk(value):
        if kind != "key":
            continue
        # Frozen surface identifiers legitimately contain result-like words
        # (e.g. ...ACCURACY); the detector targets field names, not the
        # closed surface vocabulary.
        if name in SURFACES:
            continue
        tokens = _key_tokens(name)
        if tokens & _RESULT_KEY_TOKENS:
            hits.append(path)
        elif "agreement" in tokens and tokens & _AGREEMENT_MODIFIER_TOKENS:
            hits.append(path)
    return tuple(hits)


def validate_study_design(design: Mapping[str, Any]) -> bool:
    """Refuse any design that carries result fields (Section 27.4: no fake
    human results) or that drops the minimal-design requirements."""
    hits = _result_key_hits(design)
    if hits:
        raise ValueError(
            f"study design must not carry human-result fields before any human "
            f"review has occurred: {sorted(hits)}")
    reviewers = design.get("reviewers")
    if not isinstance(reviewers, Mapping) \
            or reviewers.get("general_reviewers_per_packet") != 2:
        raise ValueError("Section 27.4 requires exactly two general reviewers per packet")
    if reviewers.get("domain_reviewer_where_required") != 1:
        raise ValueError("Section 27.4 requires one domain reviewer where required")
    if design.get("double_review") is not True:
        raise ValueError("Section 27.4 requires double review")
    if not design.get("adjudication_path"):
        raise ValueError("Section 27.4 requires an explicit path for settling disagreements")
    if not design.get("closure_criteria"):
        raise ValueError("Section 27.5 requires future closure criteria")
    return True


def study_design(output_path: str | Path) -> dict[str, Any]:
    """Section 27.4 minimal study design plus Section 27.5 future closure
    criteria.  Contains zero result fields by construction and by validation."""
    design = {
        "milestone": MILESTONE,
        "contract_section": "27.4",
        "design_only": True,
        "human_review_state": HUMAN_REVIEW_STATE,
        "reviewers": {
            "general_reviewers_per_packet": 2,
            "domain_reviewer_where_required": 1,
            "domain_reviewer_trigger": ("a general reviewer marks the packet as needing "
                                        "subject-matter knowledge"),
        },
        "double_review": True,
        "adjudication_path": [
            "every packet is answered independently by two general reviewers",
            "the two answers are compared only after both are recorded",
            ("if the two answers differ, a third reviewer settles the case in "
             "DISAGREEMENT_ADJUDICATION mode"),
            ("if any reviewer marks the packet as needing subject-matter knowledge, "
             "it moves to DOMAIN_EXPERT_ESCALATION mode"),
        ],
        "closure_criteria": {
            "section": "27.5",
            "all_packets_double_reviewed": "required before any closure claim",
            "every_disagreement_settled_or_recorded_unresolvable":
                "required before any closure claim",
            "future_measurement_plan": ("agreement and correctness will be computed only "
                                        "after human review has actually occurred"),
            "no_closure_before_human_review": True,
        },
    }
    validate_study_design(design)
    write_json(Path(output_path), design, refuse_existing=True)
    return design


def package_manifest(output_root: str | Path) -> dict[str, Any]:
    """Hash every file in the package, verify no file claims a human result,
    and state HUMAN_REVIEW_PENDING.  Refuses to overwrite an existing manifest."""
    root = Path(output_root)
    if not root.is_dir():
        raise ValueError("package root does not exist")
    manifest_path = root / "package_manifest.json"
    if manifest_path.exists():
        raise ValueError("package manifest already written; the package is append-only")
    files = sorted(path for path in root.rglob("*")
                   if path.is_file() and path != manifest_path)
    if not files:
        raise ValueError("empty package cannot be manifested")
    entries: dict[str, str] = {}
    for path in files:
        relative = path.relative_to(root).as_posix()
        entries[relative] = sha256(path.read_bytes())
        if path.suffix == ".json":
            parsed: list[Any] = [json.loads(path.read_text(encoding="utf-8"))]
        elif path.suffix == ".jsonl":
            parsed = [json.loads(line)
                      for line in path.read_text(encoding="utf-8").splitlines() if line]
        else:
            parsed = []
        for value in parsed:
            hits = _result_key_hits(value)
            if hits:
                raise ValueError(
                    f"package file {relative} carries human-result fields before any "
                    f"human review has occurred: {sorted(hits)}")
    manifest = {
        "milestone": MILESTONE,
        "contract_section": 27,
        "human_review_state": HUMAN_REVIEW_STATE,
        "human_results_claimed": False,
        "no_human_result_files_verified": True,
        "file_count": len(entries),
        "files": entries,
        "package_hash": sha256(entries),
        "created_time": now_utc(),
    }
    write_json(manifest_path, manifest, refuse_existing=True)
    return manifest


def human_package(**spec: Any) -> dict[str, Any]:
    """CLI wrapper (name fixed by cli.FORWARDED_COMMANDS)."""
    packets = build_human_packets(
        spec["heldout_manifest_path"], spec.get("campaign_roots") or {},
        spec["output_root"], minimum=int(spec.get("minimum", 100)))
    modes = review_modes(Path(spec["output_root"]) / "review_modes.json")
    design = study_design(Path(spec["output_root"]) / "study_design.json")
    manifest = package_manifest(spec["output_root"])
    return {"packets": {key: packets[key] for key in sorted(packets)
                        if key not in {"packets"}},
            "modes": sorted(modes.get("modes", modes)),
            "design_validated": validate_study_design(design),
            "manifest_hash": manifest.get("integrity_hash")}
