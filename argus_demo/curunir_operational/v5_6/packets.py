"""V5.6 §15 — dependency-aware reviewer packets.

Two properties matter, and they pull in opposite directions.

*Blinding*: the packet may show what was observed and where, and nothing about
what Curunír concluded.  A production support class, an old majority label or a
confidence score in the packet turns adjudication into agreement measurement.

*Adaptive availability*: later options must be constrained by earlier answers, so
that an illegal combination cannot be submitted.  This is what stops the V5.3
failure at the form rather than at the audit — a reviewer who has answered
``NOT_SUPPORTED`` is never offered ``PUBLISHED``.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from ..v5_1.models import Record, now_utc, sha256, stable_id
from . import schema as S


class PacketViolation(RuntimeError):
    """A packet exposed something a blinded adjudicator may not see."""


#: §15.3 — fields whose presence means the packet is answering its own question.
FORBIDDEN_PACKET_KEYS: frozenset[str] = frozenset({
    "production_support_class", "production_extraction_state", "prediction",
    "expected_publication_state", "old_majority_label", "historical_label",
    "historical_label_ids", "conflict_category", "confidence", "confidence_score",
    "production_confidence", "reviewer_majority", "previous_reviewer_result",
    "support_class_predicted", "terminal_state_predicted", "partition",
})

#: What a packet *may* expose about an observation.
PERMITTED_OBSERVATION_KEYS: frozenset[str] = frozenset({
    "observed_text", "observation_type", "source_location", "normalization_provenance",
})


def _scrub(payload: Any, path: str = "") -> list[str]:
    """Every forbidden key found anywhere in a packet, with its path."""
    found: list[str] = []
    if isinstance(payload, Mapping):
        for key, value in payload.items():
            here = f"{path}.{key}" if path else str(key)
            if str(key) in FORBIDDEN_PACKET_KEYS:
                found.append(here)
            found.extend(_scrub(value, here))
    elif isinstance(payload, (list, tuple)):
        for index, value in enumerate(payload):
            found.extend(_scrub(value, f"{path}[{index}]"))
    return found


@dataclass(frozen=True)
class ExtractionPacket(Record):
    """§10 — one Stage-A packet for one extraction candidate."""

    packet_id: str
    candidate_id: str
    protocol_version: str
    raw_span: str
    expanded_context: str
    page_or_section: str
    observations: tuple[Mapping[str, Any], ...]
    competing_candidates: tuple[Mapping[str, str], ...]
    original_language: str
    original_language_text: str
    translation_text: str | None
    invariant_dimensions: tuple[str, ...]
    invariant_labels: tuple[str, ...]
    mapping_requirements: tuple[str, ...]
    permitted_results: tuple[str, ...]
    questions: tuple[str, ...]
    recorded_time: str

    def __post_init__(self) -> None:
        leaks = _scrub(self.to_record())
        if leaks:
            raise PacketViolation(f"packet exposes forbidden fields: {leaks[:5]}")
        if tuple(self.invariant_dimensions) != S.INVARIANT_DIMENSIONS:
            raise PacketViolation("an extraction packet must offer all 21 invariants")

    @property
    def identity(self) -> str:
        return sha256({"candidate_id": self.candidate_id, "raw_span": self.raw_span,
                       "context": self.expanded_context,
                       "protocol_version": self.protocol_version})


def extraction_packet(*, candidate: Mapping[str, Any], evidence: Mapping[str, Any] | None = None,
                      competing: Sequence[Mapping[str, str]] = (),
                      protocol_version: str = "V5_6_PROTOCOL_1") -> ExtractionPacket:
    evidence = evidence or {}
    observations = tuple(
        {k: v for k, v in obs.items() if k in PERMITTED_OBSERVATION_KEYS}
        for obs in (evidence.get("observations") or ()))
    return ExtractionPacket(
        stable_id("v5-6-packet-a", str(candidate.get("candidate_id")), protocol_version),
        str(candidate.get("candidate_id")), protocol_version,
        str(candidate.get("raw_span") or ""),
        str(candidate.get("expanded_context") or evidence.get("document_context") or ""),
        str(candidate.get("page_or_section") or ""),
        observations,
        tuple({"candidate_id": str(c.get("candidate_id")),
               "raw_span": str(c.get("raw_span") or "")} for c in competing),
        str(evidence.get("original_language") or "en"),
        str(evidence.get("original_language_text") or ""),
        evidence.get("translation_text"),
        S.INVARIANT_DIMENSIONS, S.INVARIANT_LABELS, S.MAPPING_REQUIREMENTS,
        S.EXTRACTION_RESULTS,
        ("For each invariant dimension, is it SATISFIED, VIOLATED, "
         "UNRESOLVED_FROM_AVAILABLE_EVIDENCE, NOT_APPLICABLE or CONSTRUCTION_DEFECT?",
         "Which mapping requirements does the recorded span satisfy?",
         "What is the first material invariant failure, if any?",
         "Would bounded context expansion repair it?",
         "Which competing candidate, if any, do you prefer?",
         "Is exact quotation permitted, or paraphrase only?",
         "What is the resulting extraction state?"),
        now_utc())


@dataclass(frozen=True)
class SupportPacket(Record):
    """§11–§14 — one staged packet for one canonical proposition.

    The Stage-E options are *not* listed here as a fixed menu.  They are
    computed from the Stage-B answer the reviewer gives, by
    ``available_dispositions``.  A menu that always listed PUBLISHED would
    reintroduce the failure this protocol exists to remove.
    """

    packet_id: str
    proposition_id: str
    claim_id: str
    evidence_bundle_id: str
    protocol_version: str
    proposition_kind: str
    subject: str
    predicate: str
    object_or_value: str
    attribution: str | None
    asserted_lifecycle_state: str
    asserted_polarity: str
    asserted_modality: str
    evidence_text: str
    counterevidence: tuple[str, ...]
    correction_context: tuple[str, ...]
    dependence_state: str
    original_language: str
    original_language_text: str
    translation_text: str | None
    report_sentence: str | None
    underlying_proposition_summary: str | None
    support_question_order: tuple[str, ...]
    permitted_support_classes: tuple[str, ...]
    qualification_dimensions: tuple[str, ...]
    wording_permissions: tuple[str, ...]
    questions: tuple[str, ...]
    recorded_time: str

    def __post_init__(self) -> None:
        leaks = _scrub(self.to_record())
        if leaks:
            raise PacketViolation(f"packet exposes forbidden fields: {leaks[:5]}")
        if tuple(self.support_question_order) != S.SUPPORT_QUESTION_ORDER:
            raise PacketViolation("the staged question order is fixed by Section 11.1")


def support_packet(*, proposition: Mapping[str, Any], evidence: Mapping[str, Any],
                   report_sentence: str | None = None,
                   underlying_summary: str | None = None,
                   protocol_version: str = "V5_6_PROTOCOL_1") -> SupportPacket:
    kind = str(proposition.get("kind") or "UNDERLYING_FACT_CLAIM")
    questions = [
        "Answer in order. Does the evidence address this proposition family at all?",
        "Does the subject or entity align?",
        "Does the predicate align?",
        "Does the object or value align?",
        "Does scope align?  Does time align?  Does polarity align?",
        "Do modality and lifecycle align?  Is attribution preserved?",
        "Is support complete, partial, qualified, context-dependent, contradicted "
        "or inferential?",
        "Given that support decision, which qualifications may NOT be removed "
        "without changing truth conditions?",
        "Given those qualifications, what is the strongest factually permitted "
        "wording, and what wording is prohibited?",
        "Given all of the above, what publication disposition follows?",
    ]
    if kind == "ATTRIBUTED_METACLAIM":
        questions.insert(0,
            "This proposition is an ATTRIBUTED METACLAIM: it asserts that a named "
            "party made a statement, NOT that the statement is true. Evidence that "
            "the party said it fully supports THIS proposition. Do not import "
            "evidence about the underlying fact.")
    return SupportPacket(
        stable_id("v5-6-packet-b", str(proposition.get("proposition_id")), protocol_version),
        str(proposition.get("proposition_id")), str(proposition.get("claim_id")),
        str(proposition.get("evidence_bundle_id")), protocol_version, kind,
        str(proposition.get("subject") or ""), str(proposition.get("predicate") or ""),
        str(proposition.get("object_or_value") or ""), proposition.get("attribution"),
        str(proposition.get("lifecycle_state") or "UNKNOWN"),
        str(proposition.get("polarity") or "POSITIVE"),
        str(proposition.get("modality") or "ASSERTED"),
        str(evidence.get("document_context") or ""),
        tuple(evidence.get("counterevidence") or ()),
        tuple(evidence.get("correction_or_supersession_context") or ()),
        str(evidence.get("source_dependence_state") or "INDEPENDENCE_UNKNOWN"),
        str(evidence.get("original_language") or "en"),
        str(evidence.get("original_language_text") or ""),
        evidence.get("translation_text"), report_sentence, underlying_summary,
        S.SUPPORT_QUESTION_ORDER, S.SUPPORT_CLASSES, S.QUALIFICATION_DIMENSIONS,
        S.WORDING_PERMISSIONS, tuple(questions), now_utc())


# ---------------------------------------------------------------------------
# §15.2 — adaptive availability.  The options a later stage may offer are a
# function of the answers already given.
# ---------------------------------------------------------------------------

def available_qualifications(support_class: str) -> tuple[str, ...]:
    """Stage C options, given the Stage B answer."""
    options = list(S.QUALIFICATION_DIMENSIONS)
    if support_class == "INFERENCE_ONLY":
        # Inference marking is not optional; offering "no material qualification"
        # would let an inference be published as bare fact.
        options = [o for o in options if o != "NO_MATERIAL_QUALIFICATION"]
    return tuple(options)


def available_wording_permissions(support_class: str) -> tuple[str, ...]:
    """Stage D options, given the Stage B answer."""
    if support_class in S.SPECIFIC_MISMATCH | {"NOT_SUPPORTED", "CONTRADICTED"}:
        return ("ATTRIBUTED_METACLAIM_ONLY", "INFERENCE_OR_UNCERTAINTY_ONLY",
                "NO_PUBLICATION_PERMITTED")
    if support_class == "INFERENCE_ONLY":
        return ("INFERENCE_OR_UNCERTAINTY_ONLY", "NO_PUBLICATION_PERMITTED")
    if support_class in ("EPISTEMICALLY_UNRESOLVABLE", "CONSTRUCTION_DEFECT"):
        return ("NO_PUBLICATION_PERMITTED",)
    return S.WORDING_PERMISSIONS


def available_dispositions(support_class: str, *, wording_permission: str,
                           required_context_present: bool = True) -> tuple[str, ...]:
    """Stage E options, given Stages B and D.

    This is the whole protocol in one function: a reviewer who answered
    NOT_SUPPORTED is never shown PUBLISHED, so the V5.3 contradiction cannot be
    entered rather than being rejected afterwards.
    """
    permitted = set(S.permitted_dispositions(
        support_class, required_context_present=required_context_present))
    if wording_permission == "NO_PUBLICATION_PERMITTED":
        permitted &= {"REJECTED_UNSUPPORTED", "MOVED_TO_UNCERTAINTY_SECTION",
                      "OMITTED_AS_IMMATERIAL", "REPORT_REVALIDATION_REQUIRED"}
    if wording_permission == "INFERENCE_OR_UNCERTAINTY_ONLY":
        permitted &= {"MOVED_TO_UNCERTAINTY_SECTION", "OMITTED_AS_IMMATERIAL",
                      "REJECTED_UNSUPPORTED"}
    return tuple(sorted(permitted))


def audit_blinding(packets: Iterable[Any]) -> dict[str, Any]:
    """§15.3 — no packet may carry an answer-shaped field."""
    leaks = []
    count = 0
    for packet in packets:
        count += 1
        payload = packet.to_record() if hasattr(packet, "to_record") else packet
        for path in _scrub(payload):
            leaks.append({"packet": str(payload.get("packet_id")), "field": path})
    return {"packets": count, "leaks": len(leaks), "examples": leaks[:10],
            "forbidden_keys": sorted(FORBIDDEN_PACKET_KEYS),
            "verdict": "PASS" if not leaks else "FAIL"}
