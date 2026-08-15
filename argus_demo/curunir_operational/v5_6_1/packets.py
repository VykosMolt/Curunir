"""V5.6.1 §5, §8, §9 — repaired reviewer packets and the answers they admit.

A V5.6 packet asked its extraction question through one boolean and carried the
proposition's grammatical ``subject`` as though it were an actor.  Both defects
are structural: they live in what the packet *can represent*, so no amount of
careful reviewing repairs them.  This module rebuilds the packet around the two
repairs, and around one rule that governs both:

    A reviewer may only be shown options that its own earlier answers permit.

That is why Stage E is computed from the seat's own B/C/D answer rather than
offered as a free choice, and why a construction defect cannot carry a
substantive wording dimension.  A contradiction that cannot be entered does not
have to be detected later.

The blinding list is stricter than V5.6's.  Three V5.6.1-specific fields are
added: the legacy wording boolean (it *is* a V5.6 answer), the defect
classification (it tells a reviewer what to look for), and the V5.6 reference
identifier (it points straight at the old decision).
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from ..v5_1.models import Record, now_utc, stable_id
from ..v5_6 import packets as _p6
from ..v5_6 import schema as S
from . import protocol as P
from .identity import (
    IdentityViolation, SemanticIdentity, canonical_hash, hash_field_coverage,
)

PROTOCOL_VERSION = "V5_6_1_PROTOCOL_1"
PACKET_QUESTION_VERSION = "V5_6_1_QUESTIONS_1"


class PacketViolation(RuntimeError):
    """A packet exposed something a blinded reviewer may not see."""


#: §5.3 — everything V5.6 forbade, plus what V5.6.1 adds.
FORBIDDEN_PACKET_KEYS: frozenset[str] = _p6.FORBIDDEN_PACKET_KEYS | frozenset({
    # The V5.6 boolean is a V5.6 answer.  §1.3 keeps it in lineage, not here.
    "legacy_ambiguous_wording_field", "LEGACY_AMBIGUOUS_WORDING_FIELD",
    "exact_quotation_permitted",
    # V5.6 panel output of any shape.
    "v5_6_reference_id", "v5_6_decision", "v5_6_final_decision",
    "v5_6_agreement_pattern", "agreement_pattern", "adjudication_outcome",
    "primary_decisions", "final_decision", "dissent",
    # Why this unit was regenerated, and which arm produced its candidate.
    "defect_ids", "defect_class", "classification", "dependency_classification",
    "extraction_variant", "variant", "variant_id",
    # Partition, under either spelling.
    "partition", "development_or_validation_partition",
})

#: Filename and directory fragments that would leak an answer or a defect class
#: even if the payload were clean (§5.3: *a packet named with an answer counts*).
FORBIDDEN_PATH_FRAGMENTS: tuple[str, ...] = (
    "development", "validation", "affected", "unaffected", "defect", "d1", "d2",
    "d3", "d4", "d5", "d6", "supported", "rejected", "variant", "majority",
    "dissent", "gold", "expected", "answer", "v5_6_label", "legacy",
)


def _scrub(payload: Any, path: str = "") -> list[str]:
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


# ---------------------------------------------------------------------------
# §5.5 — the typed extraction packet
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class RepairedExtractionPacket(Record):
    """Five independent questions, five disjoint vocabularies, one span."""

    packet_id: str
    unit_id: str
    unit_type: str
    candidate_id: str
    evidence_bundle_id: str
    protocol_version: str
    packet_question_version: str
    raw_span: str
    normalized_span: str
    expanded_context: str
    page_or_section: str
    original_language: str
    original_language_text: str
    translation_text: str | None
    translation_language: str | None
    is_translation: bool
    competing_candidates: tuple[Mapping[str, Any], ...]
    mapping_requirements: tuple[str, ...]
    dimensions: Mapping[str, tuple[str, ...]]
    questions: Mapping[str, str]
    semantic_identity_hash: str
    evidence_content_hash: str
    review_packet_hash: str
    hash_coverage: Mapping[str, Any]
    recorded_time: str

    def __post_init__(self) -> None:
        leaks = _scrub(self.to_record())
        if leaks:
            raise PacketViolation(
                f"packet {self.packet_id} exposes {leaks}; a blinded seat may "
                "not be shown a prior answer, a defect class or a partition")
        if set(self.dimensions) != set(P.DIMENSIONS):
            raise PacketViolation(
                "a repaired extraction packet must pose all five typed "
                f"dimensions; got {sorted(self.dimensions)}")
        for name, values in self.dimensions.items():
            if tuple(values) != tuple(P.DIMENSIONS[name]):
                raise PacketViolation(
                    f"{name} was offered a vocabulary other than its own")
            if len(values) < 2:
                raise PacketViolation(
                    f"{name} was collapsed to fewer than two values, which is "
                    "how a boolean is smuggled back in")


# ---------------------------------------------------------------------------
# §5.4 — the support packet, carrying typed roles instead of a subject string
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class RepairedSupportPacket(Record):
    """Roles are typed and independently derived; none stands in for another."""

    packet_id: str
    unit_id: str
    unit_type: str
    proposition_id: str
    claim_id: str
    evidence_bundle_id: str
    protocol_version: str
    packet_question_version: str
    report_sentence: str
    grammatical_subject_span: str
    semantic_actor_span: str | None
    semantic_actor_resolution: str
    attribution_source_span: str | None
    quoted_speaker_span: str | None
    institutional_issuer_id: str | None
    predicate: str
    object_or_value: str
    proposition_kind: str
    asserted_polarity: str
    asserted_modality: str
    asserted_lifecycle_state: str
    dependence_state: str
    evidence_text: str
    original_language: str
    original_language_text: str
    translation_text: str | None
    translation_language: str | None
    is_translation: bool
    counterevidence: tuple[str, ...]
    correction_context: tuple[str, ...]
    title_and_metadata_context: str
    support_question_order: tuple[str, ...]
    permitted_support_classes: tuple[str, ...]
    qualification_dimensions: tuple[str, ...]
    wording_permissions: tuple[str, ...]
    questions: tuple[str, ...]
    semantic_identity_hash: str
    evidence_content_hash: str
    review_packet_hash: str
    hash_coverage: Mapping[str, Any]
    recorded_time: str

    def __post_init__(self) -> None:
        leaks = _scrub(self.to_record())
        if leaks:
            raise PacketViolation(
                f"packet {self.packet_id} exposes {leaks}; a blinded seat may "
                "not be shown a prior answer, a defect class or a partition")
        # §5.4 — a role that did not resolve says so.  It never carries the
        # nearest organisation, and it never silently reuses the grammatical
        # subject, which is the whole of V56-D2.
        if self.semantic_actor_resolution == "RESOLVED" and not self.semantic_actor_span:
            raise PacketViolation("a resolved semantic actor must name a span")
        if self.semantic_actor_resolution != "RESOLVED" and self.semantic_actor_span:
            raise PacketViolation(
                "an unresolved semantic actor may not carry a span; that is the "
                "nearest-entity substitution §8.8 forbids")


# ---------------------------------------------------------------------------
# Builders
# ---------------------------------------------------------------------------

#: §9.5 — a packet carries presentation fields that are deliberately not
#: identity-bearing.  Each is excluded on purpose and says why, because an
#: unexplained exclusion is exactly what let V5.6 drop ``translation_language``
#: without anyone noticing.
PRESENTATION_EXCLUSIONS: Mapping[str, str] = {
    "packet_id": "derived FROM the packet hash; hashing it would be circular",
    "unit_id": "the root unit id, equal to packet_id by construction",
    "unit_type": "carried in record_type, which every family hashes",
    "questions": "the reviewer-facing question text; its version is hashed via "
                 "packet_question_version",
    "dimensions": "the offered vocabularies; their version is hashed via "
                  "protocol_version",
    "mapping_requirements": "as dimensions",
    "support_question_order": "as dimensions",
    "permitted_support_classes": "as dimensions",
    "qualification_dimensions": "as dimensions",
    "wording_permissions": "as dimensions",
    "packet_question_version": "hashed by review_packet_hash",
    "semantic_identity_hash": "the hash itself",
    "evidence_content_hash": "the hash itself",
    "review_packet_hash": "the hash itself",
    "hash_coverage": "the coverage manifest describing the hashes",
    "raw_span": "hashed as original_language_text in evidence_content_hash",
    "normalized_span": "a whitespace normalisation of raw_span, which is hashed",
    "expanded_context": "hashed as document_context in evidence_content_hash",
    "page_or_section": "hashed inside exact_mapping_information",
    "evidence_text": "hashed as document_context in evidence_content_hash",
    "report_sentence": "the claim under test; its proposition_id is hashed",
    "predicate": "hashed via proposition_id, which determines it",
    "object_or_value": "as predicate",
    "proposition_kind": "as predicate",
    "asserted_polarity": "as predicate",
    "asserted_modality": "as predicate",
    "asserted_lifecycle_state": "as predicate",
    "dependence_state": "hashed as source_dependence_state in evidence_content_hash",
    "claim_id": "hashed by semantic_identity_hash",
    "competing_candidates": "presentation of sibling candidate ids, each of which "
                            "has its own hashed identity",
    "semantic_actor_span": "the span of semantic_actor_id, which is hashed",
    "attribution_source_span": "the span of attribution_source_id, which is hashed",
    "quoted_speaker_span": "the span of quoted_speaker_id, which is hashed",
    "semantic_actor_resolution": "derived from whether semantic_actor_id is present",
    "institutional_issuer_id": "hashed by semantic_identity_hash when present",
    "counterevidence": "hashed by evidence_content_hash",
    "correction_context": "hashed as correction_or_supersession_context",
    "title_and_metadata_context": "hashed by evidence_content_hash",
}


def _hashes(*, record_type: str, identity_payload: Mapping[str, Any],
            evidence_payload: Mapping[str, Any],
            packet_payload: Mapping[str, Any]) -> dict[str, Any]:
    identity = canonical_hash(identity_payload, family="semantic_identity_hash",
                              record_type=record_type)
    evidence = canonical_hash(evidence_payload, family="evidence_content_hash",
                              record_type=record_type)
    full_packet_payload = {**packet_payload,
                           "semantic_identity_hash": identity,
                           "evidence_content_hash": evidence}
    packet = canonical_hash(full_packet_payload, family="review_packet_hash",
                            record_type=record_type)
    coverage = {}
    for family, payload in (("semantic_identity_hash", identity_payload),
                            ("evidence_content_hash", evidence_payload),
                            ("review_packet_hash", full_packet_payload)):
        report = hash_field_coverage(record_type, payload)
        coverage[family] = {
            "hashed_fields": report["by_family"][family]["included"],
            "unexplained_excluded_fields": report["unexplained_excluded_fields"],
        }
    return {"semantic_identity_hash": identity, "evidence_content_hash": evidence,
            "review_packet_hash": packet, "hash_coverage": coverage}


def repaired_extraction_packet(*, candidate: Mapping[str, Any],
                               evidence: Mapping[str, Any] | None = None,
                               intellectual_work_id: str,
                               competing: Sequence[Mapping[str, Any]] = (),
                               ) -> RepairedExtractionPacket:
    evidence = dict(evidence or {})
    language = str(candidate.get("language") or evidence.get("original_language") or "en")
    is_translation = bool(evidence.get("translation_language"))
    identity_payload = {
        "record_type": "EXTRACTION_UNIT", "schema_version": "V5_6_1_CANONICAL_1",
        "protocol_version": PROTOCOL_VERSION,
        "source_id": candidate.get("source_id"),
        "source_family_id": candidate.get("source_family_id"),
        "intellectual_work_id": intellectual_work_id,
        "candidate_id": candidate["candidate_id"],
        "evidence_bundle_id": evidence.get("evidence_bundle_id", ""),
        "language": language, "original_language": language,
        "is_translation": is_translation,
        "translation_of_id": evidence.get("translation_of_id"),
        "is_original_manifestation": not is_translation,
    }
    evidence_payload = {
        "record_type": "EXTRACTION_UNIT", "schema_version": "V5_6_1_CANONICAL_1",
        "raw_evidence_ids": evidence.get("raw_evidence_ids", []),
        "document_context": evidence.get("document_context", ""),
        # §5.2 / D4 — the field V5.6 left outside the hash.
        "title_and_metadata_context": evidence.get("title_and_metadata_context", ""),
        "original_language": language,
        "original_language_text": candidate.get("raw_span", ""),
        "translation_text": evidence.get("translation_text"),
        # §5.2 / D3 — the field that separates an original from its translation.
        "translation_language": evidence.get("translation_language"),
        "is_translation": is_translation,
        "exact_mapping_information": {"mapping": candidate.get("mapping"),
                                      "page_or_section": candidate.get("page_or_section")},
    }
    packet_payload = {
        "record_type": "EXTRACTION_PACKET", "schema_version": "V5_6_1_CANONICAL_1",
        "protocol_version": PROTOCOL_VERSION,
        "packet_question_version": PACKET_QUESTION_VERSION,
        "review_protocol_version": PROTOCOL_VERSION,
        "candidate_id": candidate["candidate_id"],
        "evidence_bundle_id": evidence.get("evidence_bundle_id", ""),
    }
    hashes = _hashes(record_type="EXTRACTION_PACKET",
                     identity_payload=identity_payload,
                     evidence_payload=evidence_payload,
                     packet_payload=packet_payload)
    packet_id = stable_id("v5-6-1-packet-a", candidate["candidate_id"],
                          hashes["review_packet_hash"])
    return RepairedExtractionPacket(
        packet_id, packet_id, "EXTRACTION", candidate["candidate_id"],
        evidence.get("evidence_bundle_id", ""), PROTOCOL_VERSION,
        PACKET_QUESTION_VERSION,
        candidate.get("raw_span", ""), candidate.get("normalized_span", ""),
        # The context a boundary question needs.  A candidate's own
        # ``expanded_context`` is empty throughout this corpus, which is why
        # V5.6 could not ask the boundary question at all; the bundle's
        # document context is the text actually surrounding the span.
        (candidate.get("expanded_context") or evidence.get("document_context") or ""),
        candidate.get("page_or_section", ""),
        language, candidate.get("raw_span", ""),
        evidence.get("translation_text"), evidence.get("translation_language"),
        is_translation,
        tuple(dict(c) for c in competing), tuple(S.MAPPING_REQUIREMENTS),
        {name: tuple(values) for name, values in P.DIMENSIONS.items()},
        dict(P.REVIEWER_QUESTIONS),
        hashes["semantic_identity_hash"], hashes["evidence_content_hash"],
        hashes["review_packet_hash"], hashes["hash_coverage"], now_utc())


#: §5.4 — the actor question adapts to whether an actor was derived.
#:
#: A question a reviewer cannot answer produces noise, not a label.  V5.6's
#: repair de-authorised the legacy subject, which is right, but a packet that
#: then asks *"does the semantic actor align"* with no actor derived is asking
#: about nothing.  So the question changes shape: with a canonical actor it is
#: asked against that actor; without one it is asked against the entity the
#: sentence is about, and the reviewer is told plainly that no canonical actor
#: could be derived rather than being handed a guessed one.
ACTOR_QUESTION_RESOLVED = (
    "Does the semantic actor align?  Judge against the derived semantic actor, "
    "not the grammatical subject span.")
ACTOR_QUESTION_UNRESOLVED = (
    "No canonical semantic actor could be derived: the proposition's subject "
    "position holds a grammatical or adverbial phrase rather than a referring "
    "expression.  Judge entity alignment against whatever entity the report "
    "sentence is actually about, and answer UNRESOLVED if the evidence does not "
    "settle which entity that is.  Do not supply a nearest organisation.")

SUPPORT_QUESTIONS: tuple[str, ...] = (
    "Answer in order.  Does the evidence address this proposition family at all?",
    ACTOR_QUESTION_RESOLVED,
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
    "Given all of the above, which publication disposition follows?  You will "
    "be offered only the dispositions your own answers permit.",
)


def support_questions(*, actor_resolved: bool) -> tuple[str, ...]:
    """The ten Stage-B/C/D/E questions, with the actor question in the shape
    this unit can actually answer."""
    return tuple(
        question if question is not ACTOR_QUESTION_RESOLVED
        else (ACTOR_QUESTION_RESOLVED if actor_resolved else ACTOR_QUESTION_UNRESOLVED)
        for question in SUPPORT_QUESTIONS)


def repaired_support_packet(*, proposition: Mapping[str, Any],
                            evidence: Mapping[str, Any],
                            identity: SemanticIdentity,
                            report_sentence: str,
                            intellectual_work_id: str) -> RepairedSupportPacket:
    resolution = "RESOLVED" if identity.actor_resolved else "UNRESOLVED"
    language = str(evidence.get("original_language") or "en")
    is_translation = bool(evidence.get("translation_language"))
    identity_payload = {
        "record_type": "SUPPORT_UNIT", "schema_version": "V5_6_1_CANONICAL_1",
        "protocol_version": PROTOCOL_VERSION,
        "intellectual_work_id": intellectual_work_id,
        "proposition_id": proposition["proposition_id"],
        "claim_id": proposition.get("claim_id"),
        "evidence_bundle_id": evidence["evidence_bundle_id"],
        "language": language, "original_language": language,
        "is_translation": is_translation,
        "translation_of_id": evidence.get("translation_of_id"),
        "is_original_manifestation": not is_translation,
        # §8 — typed roles enter identity; the grammatical position is carried
        # for provenance but is not the actor.
        "grammatical_subject_span": identity.grammatical_subject_span,
        "semantic_actor_id": identity.semantic_actor_id,
        "attribution_source_id": identity.attribution_source_id,
        "quoted_speaker_id": identity.quoted_speaker_id,
        "target_role": proposition.get("target_role"),
        "target_entity_id": proposition.get("target_entity_id"),
    }
    evidence_payload = {
        "record_type": "SUPPORT_UNIT", "schema_version": "V5_6_1_CANONICAL_1",
        "raw_evidence_ids": evidence.get("raw_evidence_ids", []),
        "normalized_observation_ids": evidence.get("normalized_observation_ids", []),
        "document_context": evidence.get("document_context", ""),
        "title_and_metadata_context": evidence.get("title_and_metadata_context", ""),
        "counterevidence": evidence.get("counterevidence", []),
        "correction_or_supersession_context":
            evidence.get("correction_or_supersession_context", []),
        "source_dependence_state": evidence.get("source_dependence_state"),
        "exact_mapping_information": evidence.get("exact_mapping_information", {}),
        "original_language": language,
        "original_language_text": evidence.get("original_language_text", ""),
        "translation_text": evidence.get("translation_text"),
        "translation_language": evidence.get("translation_language"),
        "is_translation": is_translation,
    }
    packet_payload = {
        "record_type": "SUPPORT_PACKET", "schema_version": "V5_6_1_CANONICAL_1",
        "protocol_version": PROTOCOL_VERSION,
        "packet_question_version": PACKET_QUESTION_VERSION,
        "review_protocol_version": PROTOCOL_VERSION,
        "proposition_id": proposition["proposition_id"],
        "evidence_bundle_id": evidence["evidence_bundle_id"],
    }
    hashes = _hashes(record_type="SUPPORT_PACKET",
                     identity_payload=identity_payload,
                     evidence_payload=evidence_payload,
                     packet_payload=packet_payload)
    packet_id = stable_id("v5-6-1-packet-b", proposition["proposition_id"],
                          hashes["review_packet_hash"])
    return RepairedSupportPacket(
        packet_id, packet_id, "SUPPORT", proposition["proposition_id"],
        proposition.get("claim_id", ""), evidence["evidence_bundle_id"],
        PROTOCOL_VERSION, PACKET_QUESTION_VERSION, report_sentence,
        identity.grammatical_subject_span,
        identity.semantic_actor_span, resolution,
        identity.attribution_source_span, identity.quoted_speaker_span,
        identity.institutional_issuer_id,
        proposition.get("predicate", ""), proposition.get("object_or_value", ""),
        proposition.get("kind", ""), proposition.get("polarity", ""),
        proposition.get("modality", ""), proposition.get("lifecycle_state", ""),
        evidence.get("source_dependence_state", ""),
        evidence.get("document_context", ""), language,
        evidence.get("original_language_text", ""),
        evidence.get("translation_text"), evidence.get("translation_language"),
        is_translation,
        tuple(evidence.get("counterevidence", []) or ()),
        tuple(evidence.get("correction_or_supersession_context", []) or ()),
        evidence.get("title_and_metadata_context", ""),
        tuple(S.SUPPORT_QUESTION_ORDER), tuple(S.SUPPORT_CLASSES),
        tuple(S.QUALIFICATION_DIMENSIONS), tuple(S.WORDING_PERMISSIONS),
        support_questions(actor_resolved=resolution == "RESOLVED"),
        hashes["semantic_identity_hash"], hashes["evidence_content_hash"],
        hashes["review_packet_hash"], hashes["hash_coverage"], now_utc())


# ---------------------------------------------------------------------------
# §8.2 — adaptive menus.  A seat is offered what its own answers permit.
# ---------------------------------------------------------------------------

available_qualifications = _p6.available_qualifications
available_wording_permissions = _p6.available_wording_permissions
available_dispositions = _p6.available_dispositions


def available_boundary_repairs(semantic_validity: str) -> tuple[str, ...]:
    """Stage A2 options, given the seat's own Stage A1 answer."""
    if semantic_validity == "CONSTRUCTION_DEFECT":
        return ("NOT_APPLICABLE",)
    if semantic_validity == "EPISTEMICALLY_UNRESOLVABLE":
        return ("EPISTEMICALLY_UNRESOLVABLE", "NOT_APPLICABLE")
    if semantic_validity == "RECOVERABLE_WITH_BOUNDARY_REPAIR":
        return tuple(v for v in P.BOUNDARY_REPAIR
                     if v not in ("NO_REPAIR_REQUIRED", "NOT_APPLICABLE"))
    if semantic_validity == "VALID":
        return ("NO_REPAIR_REQUIRED", "NOT_APPLICABLE")
    return P.BOUNDARY_REPAIR


def available_wording_as_written(semantic_validity: str, boundary_repair: str,
                                 paraphrase: str) -> tuple[str, ...]:
    """Stage A5 options, given A1, A2 and A4.

    This is the direct replacement for the V5.6 boolean, and the constraint that
    makes it more than a rename: a candidate whose boundary needs repair cannot
    be offered PERMITTED_AS_WRITTEN at all.
    """
    if semantic_validity == "CONSTRUCTION_DEFECT":
        return ("NOT_APPLICABLE",)
    options = list(P.WORDING_AS_WRITTEN)
    if boundary_repair not in ("NO_REPAIR_REQUIRED", "NOT_APPLICABLE"):
        options = [o for o in options if o != "PERMITTED_AS_WRITTEN"]
    if paraphrase == "PARAPHRASE_NOT_PERMITTED":
        options = [o for o in options if o != "PERMITTED_ONLY_AS_PARAPHRASE"]
    return tuple(options)


# ---------------------------------------------------------------------------
# Answer validation
# ---------------------------------------------------------------------------

REQUIRED_EXTRACTION_FIELDS: tuple[str, ...] = (
    "unit_id", "candidate_semantic_validity", "boundary_repair_requirement",
    "exact_quotation_permission", "paraphrase_permission",
    "wording_as_written_permission", "mapping_satisfied", "reasoning",
)

REQUIRED_SUPPORT_FIELDS: tuple[str, ...] = (
    "unit_id", "addresses_proposition", "actor_alignment", "predicate_alignment",
    "object_alignment", "scope_alignment", "time_alignment", "polarity_alignment",
    "modality_alignment", "lifecycle_alignment", "attribution_alignment",
    "support_completeness", "support_class", "required_qualifications",
    "wording_permission", "disposition", "reasoning",
)

#: §8.3 — enough reasoning to adjudicate a disagreement later, no essays.
MIN_REASONING_CHARS = 120


def validate_extraction_answer(answer: Mapping[str, Any]) -> list[str]:
    """Every reason this Stage-A answer is not admissible, or an empty list."""
    problems: list[str] = []
    for field in REQUIRED_EXTRACTION_FIELDS:
        if field not in answer:
            problems.append(f"missing required field: {field}")
    if problems:
        return problems
    for dimension in P.DIMENSIONS:
        value = answer[dimension]
        if isinstance(value, bool):
            problems.append(f"{dimension} was submitted as a boolean")
            continue
        if value not in P.DIMENSIONS[dimension]:
            problems.append(f"{dimension}={value!r} is outside its vocabulary")
    if problems:
        return problems

    validity = answer["candidate_semantic_validity"]
    if answer["boundary_repair_requirement"] not in available_boundary_repairs(validity):
        problems.append(
            f"boundary_repair_requirement={answer['boundary_repair_requirement']!r} "
            f"is not offered when candidate_semantic_validity={validity!r}")
    permitted_wording = available_wording_as_written(
        validity, answer["boundary_repair_requirement"], answer["paraphrase_permission"])
    if answer["wording_as_written_permission"] not in permitted_wording:
        problems.append(
            f"wording_as_written_permission={answer['wording_as_written_permission']!r} "
            "is not offered by this seat's own earlier answers")
    if answer["exact_quotation_permission"] == "EXACT_QUOTATION_PERMITTED" and \
            "EXACT_VALUE_QUOTABLE" not in tuple(answer.get("mapping_satisfied") or ()):
        problems.append(
            "exact quotation permitted without an EXACT_VALUE_QUOTABLE mapping")
    for value in tuple(answer.get("mapping_satisfied") or ()):
        if value not in S.MAPPING_REQUIREMENTS:
            problems.append(f"unknown mapping requirement: {value!r}")
    if len(str(answer.get("reasoning") or "").strip()) < MIN_REASONING_CHARS:
        problems.append(
            f"reasoning is shorter than {MIN_REASONING_CHARS} characters; §8.3 "
            "requires enough reasoning to adjudicate a disagreement")
    return problems


def validate_support_answer(answer: Mapping[str, Any]) -> list[str]:
    """Every reason this Stage-B/C/D/E answer is not admissible."""
    problems: list[str] = []
    for field in REQUIRED_SUPPORT_FIELDS:
        if field not in answer:
            problems.append(f"missing required field: {field}")
    if problems:
        return problems

    support_class = answer["support_class"]
    if support_class not in S.SUPPORT_CLASSES:
        return [f"unknown support class: {support_class!r}"]
    for field in ("actor_alignment", "predicate_alignment", "object_alignment",
                  "scope_alignment", "time_alignment", "polarity_alignment",
                  "modality_alignment", "lifecycle_alignment",
                  "attribution_alignment"):
        if answer[field] not in S.ALIGNMENT_VALUES:
            problems.append(f"{field}={answer[field]!r} is outside {S.ALIGNMENT_VALUES}")
    if not isinstance(answer["addresses_proposition"], bool):
        problems.append("addresses_proposition must be a genuine boolean question")
    # §5.4 discipline, carried from V5.4: a specific mismatch presupposes that
    # the evidence engages the same proposition family.
    if support_class in S.SPECIFIC_MISMATCH and not answer["addresses_proposition"]:
        problems.append(
            f"{support_class} is a specific mismatch and requires the evidence to "
            "address the same proposition family; unaddressed evidence is "
            "NOT_SUPPORTED")
    for qualification in tuple(answer.get("required_qualifications") or ()):
        if qualification not in available_qualifications(support_class):
            problems.append(f"qualification {qualification!r} is not offered for "
                            f"{support_class}")
    if answer["wording_permission"] not in available_wording_permissions(support_class):
        problems.append(
            f"wording_permission={answer['wording_permission']!r} is not offered "
            f"for {support_class}")
    else:
        offered = available_dispositions(
            support_class, wording_permission=answer["wording_permission"],
            required_context_present=bool(answer.get("required_context_present", True)))
        if answer["disposition"] not in offered:
            problems.append(
                f"disposition={answer['disposition']!r} is not derivable from this "
                f"seat's own B/C/D answers; offered: {list(offered)}")
    if S.contradicts(support_class, answer["disposition"]):
        problems.append(
            f"{support_class} with {answer['disposition']} is one of the "
            "combinations §14.2 makes unrepresentable")
    if len(str(answer.get("reasoning") or "").strip()) < MIN_REASONING_CHARS:
        problems.append(
            f"reasoning is shorter than {MIN_REASONING_CHARS} characters; §8.3 "
            "requires enough reasoning to adjudicate a disagreement")
    return problems


def validate_answer(answer: Mapping[str, Any]) -> list[str]:
    unit_type = answer.get("unit_type")
    if unit_type == "EXTRACTION":
        return validate_extraction_answer(answer)
    if unit_type == "SUPPORT":
        return validate_support_answer(answer)
    return [f"unknown unit_type: {unit_type!r}"]


# ---------------------------------------------------------------------------
# §5.3 / §5.6 — audits
# ---------------------------------------------------------------------------

def audit_blinding(packets: Iterable[Any], *,
                   paths: Iterable[str] = ()) -> dict[str, Any]:
    """§5.3 — no packet, and no packet's *name*, may carry an answer."""
    leaks, count = [], 0
    for packet in packets:
        count += 1
        payload = packet.to_record() if hasattr(packet, "to_record") else dict(packet)
        for field in _scrub(payload):
            leaks.append({"packet": str(payload.get("packet_id")),
                          "kind": "PAYLOAD_FIELD", "detail": field})
        blob = " ".join(str(v) for v in payload.values()).casefold()
        for marker in ("v5-6-reference-", "reviewer_a\"", "adjudicat"):
            if marker in blob:
                leaks.append({"packet": str(payload.get("packet_id")),
                              "kind": "EMBEDDED_MARKER", "detail": marker})
    path_leaks = []
    for path in paths:
        lowered = str(path).casefold()
        for fragment in FORBIDDEN_PATH_FRAGMENTS:
            if re.search(rf"(^|[^a-z0-9]){re.escape(fragment)}([^a-z0-9]|$)", lowered):
                path_leaks.append({"path": str(path), "fragment": fragment})
    return {
        "packets": count,
        "payload_leaks": len(leaks),
        "path_leaks": len(path_leaks),
        "v5_6_primary_labels_exposed": 0 if not leaks else len(leaks),
        "examples": (leaks + path_leaks)[:10],
        "forbidden_keys": sorted(FORBIDDEN_PACKET_KEYS),
        "forbidden_path_fragments": list(FORBIDDEN_PATH_FRAGMENTS),
        "verdict": "PASS" if not leaks and not path_leaks else "FAIL",
    }


#: §5.6 — every mutation that must move a hash or fail validation.
HASH_MUTATIONS: tuple[tuple[str, str, Any], ...] = (
    ("translation_language", "evidence_content_hash", "de"),
    ("is_translation", "evidence_content_hash", True),
    ("translation_of_id", "semantic_identity_hash", "v5-6-1-source-other"),
    ("title_and_metadata_context", "evidence_content_hash", "MUTATED TITLE CONTEXT"),
    ("semantic_actor_id", "semantic_identity_hash", "v5-6-1-entity-mutated"),
    ("attribution_source_id", "semantic_identity_hash", "v5-6-1-entity-mutated"),
    ("quoted_speaker_id", "semantic_identity_hash", "v5-6-1-entity-mutated"),
    ("target_role", "semantic_identity_hash", "MUTATED_ROLE"),
    ("proposition_id", "semantic_identity_hash", "v5-6-1-proposition-mutated"),
    ("protocol_version", "semantic_identity_hash", "V5_6_1_PROTOCOL_MUTATED"),
)


def hash_sensitivity(payload: Mapping[str, Any], *, record_type: str
                     ) -> list[dict[str, Any]]:
    """Mutate one identity-bearing field at a time; the hash must move."""
    rows = []
    for field, family, mutated in HASH_MUTATIONS:
        base = canonical_hash(payload, family=family, record_type=record_type)
        candidate = dict(payload)
        if field not in candidate:
            rows.append({"field": field, "family": family,
                         "outcome": "FIELD_ABSENT_FROM_THIS_RECORD_TYPE",
                         "detected": True})
            continue
        if candidate[field] == mutated:
            mutated = f"{mutated}-alt" if isinstance(mutated, str) else not mutated
        candidate[field] = mutated
        after = canonical_hash(candidate, family=family, record_type=record_type)
        rows.append({"field": field, "family": family,
                     "outcome": "HASH_CHANGED" if after != base else "HASH_UNCHANGED",
                     "detected": after != base,
                     "before": base[:16], "after": after[:16]})
    return rows


def audit_hash_coverage(payload: Mapping[str, Any], *, record_type: str
                        ) -> dict[str, Any]:
    return hash_field_coverage(record_type, payload)


def unexplained_presentation_fields(packet: Mapping[str, Any]) -> list[str]:
    """§9.5 for a packet: which of its fields does nobody explain?

    A packet legitimately carries fields that are not identity-bearing.  What is
    not legitimate is carrying one that has never been classified, because that
    is the state ``translation_language`` was in when V5.6 dropped it.
    """
    from .identity import EXCLUDED_FIELDS, IDENTITY_FIELDS
    covered = {name for fields in IDENTITY_FIELDS.values() for name in fields}
    explained = covered | set(EXCLUDED_FIELDS) | set(PRESENTATION_EXCLUSIONS)
    return sorted(set(packet) - explained)


__all__ = [
    "PROTOCOL_VERSION", "PACKET_QUESTION_VERSION", "PacketViolation",
    "FORBIDDEN_PACKET_KEYS", "FORBIDDEN_PATH_FRAGMENTS",
    "RepairedExtractionPacket", "RepairedSupportPacket",
    "repaired_extraction_packet", "repaired_support_packet", "SUPPORT_QUESTIONS",
    "available_qualifications", "available_wording_permissions",
    "available_dispositions", "available_boundary_repairs",
    "available_wording_as_written", "validate_answer", "validate_extraction_answer",
    "validate_support_answer", "audit_blinding", "hash_sensitivity",
    "audit_hash_coverage", "HASH_MUTATIONS", "MIN_REASONING_CHARS",
]
