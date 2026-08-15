"""V5.6.1 §8–§9 — canonical semantic identity and serialization.

Two repairs live here.

**§8, the semantic actor model.**  V5.6 built attributed metaclaims by passing
V5.3's legacy ``subject`` field as the speaker.  That field holds whatever text
preceded the verb, so seven metaclaims were constructed with ``"Importers who"``,
``"For transparency"`` and ``"Immediately"`` in the slot where a named party must
be.  V5.4 had already found this field unusable as an actor and repaired it
*inside the claim-support classifier*; the corpus builder reintroduced the same
defect one layer up.  The repair is to stop treating a grammatical position as a
semantic role: a grammatical subject, a semantic actor, an attribution source, a
quoted speaker and an institutional issuer are five different things, and a
derivation that cannot resolve one returns ``unresolved`` rather than guessing.

**§9, canonical serialization.**  V5.6 hashed per-module with inconsistent field
coverage, and two identity-bearing fields fell outside — including the one that
keeps an original manifestation distinct from its translation.  One versioned
contract replaces the ad-hoc hashes, with a coverage manifest that must explain
every excluded field.
"""

from __future__ import annotations

import json
import re
import unicodedata
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from hashlib import sha256 as _sha256
from typing import Any

from ..v5_1.models import Record, now_utc, stable_id


class IdentityViolation(RuntimeError):
    """A semantic role or identity was derived from something that cannot bear it."""


# ===========================================================================
# §8 — semantic actor and attribution
# ===========================================================================

#: §8.1 — the five roles, which are not interchangeable.
ACTOR_ROLES: tuple[str, ...] = (
    "grammatical_subject", "semantic_actor", "attribution_source",
    "quoted_speaker", "institutional_issuer",
)

DERIVATION_OUTCOMES: tuple[str, ...] = (
    "RESOLVED", "UNRESOLVED_NOT_REFERENTIAL", "UNRESOLVED_ANAPHORIC",
    "UNRESOLVED_NO_CANDIDATE", "UNRESOLVED_TYPE_INCOMPATIBLE", "NOT_APPLICABLE",
)

#: §8.3 — a phrase in one of these shapes is a grammatical position, not an
#: actor.  Every example is drawn from the seven malformed V5.6 metaclaims or
#: from the V5.4 record of the same field.
_NON_REFERENTIAL = (
    # A relative-clause fragment.  "Importers who" ends on the pronoun; "The
    # person whose status" continues past it and is still a fragment, because
    # the clause never closes — the phrase describes a condition on an actor
    # rather than naming one.
    re.compile(r"\b(who|whose|which|that)\b", re.IGNORECASE),
    # adverbial or purpose opener: "For transparency", "Immediately"
    re.compile(r"^(as|for|under|following|after|before|during|within|through|by|"
               r"with|from|in|on|at|immediately|subsequently|previously|currently|"
               r"recently|thereafter|meanwhile|however|therefore|moreover|"
               r"additionally|finally|initially|overall|according)\b", re.IGNORECASE),
    # demonstrative or anaphor: "That right", "These security", "This process"
    re.compile(r"^(that|this|these|those|such|said|it|they|he|she|we)\b", re.IGNORECASE),
    # An abstraction, with or without adjectival modifiers.  "Unadjusted data"
    # and "The reporting obligation" are both headed by an abstraction, and the
    # modifier is what a noun-first pattern misses — which is how "Unadjusted
    # data" survived the first draft of this check.
    re.compile(r"^(?:the\s+|a\s+|an\s+)?(?:[\w-]+\s+){0,3}"
               r"(rights?|obligations?|provisions?|requirements?|measures?|"
               r"conditions?|periods?|deadlines?|procedures?|derogations?|"
               r"exemptions?|penalt(?:y|ies)|data|estimates?|figures?|"
               r"process(?:es)?|balances?|features?|statistics?|values?|"
               r"results?|totals?|shares?|rates?)\s*$", re.IGNORECASE),
    # conditional fragment: "If, following the exchanges"
    re.compile(r"^(if|where|when|whereas|unless|provided)\b", re.IGNORECASE),
    # sentence-fragment tell: ends on a preposition or conjunction
    re.compile(r"\b(of|to|in|on|for|and|or|with|by|from|as)\s*$", re.IGNORECASE),
)

#: A named party looks like one: a proper-noun sequence, an institution word, or
#: an explicitly typed entity.  This is a necessary condition, never sufficient.
_NAMED_PARTY = re.compile(
    r"\b([A-ZÄÖÜ][\w.&-]*(?:\s+(?:of|for|and|de|der|des|van|von)\s+)?"
    r"(?:\s+[A-ZÄÖÜ][\w.&-]*)*)\b")
_INSTITUTION_WORD = re.compile(
    r"\b(Commission|Council|Parliament|Agency|Authority|Ministry|Board|Office|"
    r"Bank|Court|Committee|Centre|Center|Institute|Organisation|Organization|"
    r"Company|Corporation|Ltd|GmbH|Inc|Association|Federation|Union|Service|"
    r"Department|Directorate|Kommission|Behörde|Ministerium|Amt|Gericht)\b")


@dataclass(frozen=True)
class ActorDerivation(Record):
    """§8.7 — one derivation attempt, with its provenance at every step."""

    derivation_id: str
    role: str
    raw_phrase: str
    outcome: str
    resolved_id: str | None
    resolved_span: str | None
    steps: tuple[tuple[str, str], ...]
    recorded_time: str

    def __post_init__(self) -> None:
        if self.role not in ACTOR_ROLES:
            raise IdentityViolation(f"unknown actor role: {self.role}")
        if self.outcome not in DERIVATION_OUTCOMES:
            raise IdentityViolation(f"unknown derivation outcome: {self.outcome}")
        if self.outcome == "RESOLVED" and not self.resolved_id:
            raise IdentityViolation("a resolved derivation must name what it resolved to")
        if self.outcome != "RESOLVED" and self.resolved_id:
            raise IdentityViolation(
                "an unresolved derivation may not carry a resolved identity; that is "
                "the substitution §8.8 forbids")


def is_referential(phrase: str) -> bool:
    """§8.3 — could this phrase name an actor at all?"""
    text = " ".join((phrase or "").split())
    if len(text) < 2:
        return False
    for pattern in _NON_REFERENTIAL:
        if pattern.search(text):
            return False
    return bool(_NAMED_PARTY.search(text) or _INSTITUTION_WORD.search(text))


def derive_actor(raw_phrase: str, *, role: str = "semantic_actor",
                 predicate: str | None = None,
                 local_context: str | None = None) -> ActorDerivation:
    """§8.7 — the typed derivation pipeline, recording provenance at each step.

    §8.8 is the important part: when resolution fails the result is
    ``unresolved``.  It is never the first capitalised phrase, the document
    issuer, the source host, the legacy subject, or the nearest organisation.
    """
    text = " ".join((raw_phrase or "").split())
    steps: list[tuple[str, str]] = [("raw_grammatical_phrase", text[:120])]

    if not text:
        steps.append(("discourse_role", "empty phrase"))
        return _unresolved(role, text, "UNRESOLVED_NO_CANDIDATE", steps)

    steps.append(("discourse_role_classification",
                  "grammatical subject position" if role == "grammatical_subject"
                  else f"candidate for {role}"))

    if not is_referential(text):
        steps.append(("referentiality_check",
                      "phrase is a grammatical position, not a referring expression"))
        anaphoric = bool(re.match(r"^(that|this|these|those|such|it|they)\b", text,
                                  re.IGNORECASE))
        return _unresolved(
            role, text,
            "UNRESOLVED_ANAPHORIC" if anaphoric else "UNRESOLVED_NOT_REFERENTIAL",
            steps)
    steps.append(("referentiality_check", "phrase can refer"))

    match = _NAMED_PARTY.search(text)
    candidate = (match.group(1).strip() if match else text)
    steps.append(("candidate_entity_linking", candidate[:120]))

    if predicate and role == "semantic_actor":
        # §8.7 predicate-role compatibility: a document cannot operate a system.
        if re.match(r"^(the\s+)?(report|study|analysis|document|regulation|"
                    r"directive|notice|article)\b", candidate, re.IGNORECASE) and \
                re.search(r"\b(operat|deploy|procur|build|construct)", predicate,
                          re.IGNORECASE):
            steps.append(("predicate_role_compatibility",
                          "a document cannot be the actor of an operational predicate"))
            return _unresolved(role, text, "UNRESOLVED_TYPE_INCOMPATIBLE", steps)
        steps.append(("predicate_role_compatibility", "compatible"))

    if local_context and re.match(r"^(that|this|these|those|it|they)\b", text,
                                  re.IGNORECASE):
        steps.append(("local_anaphora_resolution", "attempted, bounded to local context"))

    steps.append(("resolution", "resolved"))
    return ActorDerivation(
        stable_id("v5-6-1-actor", role, candidate), role, text, "RESOLVED",
        stable_id("v5-6-1-entity", candidate.casefold()), candidate, tuple(steps),
        now_utc())


def _unresolved(role, text, outcome, steps):
    return ActorDerivation(
        stable_id("v5-6-1-actor", role, text or "empty"), role, text, outcome,
        None, None, tuple(steps), now_utc())


@dataclass(frozen=True)
class SemanticIdentity(Record):
    """§8.1 — the typed roles for one proposition, none standing in for another."""

    identity_id: str
    grammatical_subject_span: str
    semantic_actor_id: str | None
    semantic_actor_span: str | None
    attribution_source_id: str | None
    attribution_source_span: str | None
    quoted_speaker_id: str | None
    quoted_speaker_span: str | None
    institutional_issuer_id: str | None
    document_author_id: str | None
    document_host_id: str | None
    derivations: tuple[Mapping[str, Any], ...]
    legacy_subject_span: str | None
    legacy_subject_authoritative: bool
    recorded_time: str

    def __post_init__(self) -> None:
        # §8, closure condition 6: the legacy field may be carried but is never
        # authoritative.
        if self.legacy_subject_authoritative:
            raise IdentityViolation(
                "the legacy `subject` field may not determine a semantic role; it "
                "holds a grammatical position and V5.6 built seven malformed "
                "metaclaims from it")
        # §8.5 — a quoted speaker requires an explicit quotation relation.
        if self.quoted_speaker_id and not self.quoted_speaker_span:
            raise IdentityViolation(
                "a quoted speaker requires the span that establishes the quotation")

    @property
    def actor_resolved(self) -> bool:
        return self.semantic_actor_id is not None


def build_identity(*, grammatical_subject: str, predicate: str | None = None,
                   attribution_phrase: str | None = None,
                   quoted_speaker_phrase: str | None = None,
                   quoted_speaker_span: str | None = None,
                   institutional_issuer_id: str | None = None,
                   document_author_id: str | None = None,
                   document_host_id: str | None = None,
                   local_context: str | None = None) -> SemanticIdentity:
    """Derive every role independently; never let one substitute for another."""
    derivations = []
    actor = derive_actor(grammatical_subject, role="semantic_actor",
                         predicate=predicate, local_context=local_context)
    derivations.append(actor.to_record())

    attribution = None
    if attribution_phrase:
        attribution = derive_actor(attribution_phrase, role="attribution_source")
        derivations.append(attribution.to_record())

    speaker = None
    if quoted_speaker_phrase:
        speaker = derive_actor(quoted_speaker_phrase, role="quoted_speaker")
        derivations.append(speaker.to_record())

    return SemanticIdentity(
        stable_id("v5-6-1-identity", grammatical_subject or "", predicate or ""),
        grammatical_subject,
        actor.resolved_id, actor.resolved_span,
        (attribution.resolved_id if attribution else None),
        (attribution.resolved_span if attribution else None),
        (speaker.resolved_id if speaker else None),
        (quoted_speaker_span if speaker and speaker.resolved_id else None),
        institutional_issuer_id, document_author_id, document_host_id,
        tuple(derivations), grammatical_subject, False, now_utc())


def split_fact_and_metaclaim_typed(*, identity: SemanticIdentity,
                                   predicate: str, object_or_value: str,
                                   **reserved: Any) -> dict[str, Any]:
    """§8.9 — the splitter, consuming canonical typed identity only.

    Repairs V56-D6 as well: reserved keys now raise a typed error naming the
    reason instead of a bare TypeError from a duplicate keyword.
    """
    forbidden = {"attribution", "kind", "underlying_proposition_id",
                 "metaclaim_assertion", "subject"} & set(reserved)
    if forbidden:
        raise IdentityViolation(
            f"{sorted(forbidden)} are derived from the canonical identity and may "
            "not be supplied by the caller; the metaclaim's attribution is the "
            "resolved quoted speaker, not an argument")
    # §8.9 — a metaclaim requires a resolved speaker.  Without one there is no
    # named party, and therefore no proposition to test.
    speaker = identity.quoted_speaker_id or identity.attribution_source_id
    if not speaker:
        return {
            "fact": {"subject_id": identity.semantic_actor_id,
                     "subject_span": identity.semantic_actor_span,
                     "predicate": predicate, "object_or_value": object_or_value},
            "metaclaim": None,
            "metaclaim_refused_reason": (
                "no quoted speaker or attribution source resolved; a metaclaim "
                "asserts that a NAMED PARTY made a statement and there is none"),
        }
    return {
        "fact": {"subject_id": identity.semantic_actor_id,
                 "subject_span": identity.semantic_actor_span,
                 "predicate": predicate, "object_or_value": object_or_value},
        "metaclaim": {"speaker_id": speaker,
                      "speaker_span": (identity.quoted_speaker_span
                                       or identity.attribution_source_span),
                      "assertion": "THE_EXTERNAL_STATEMENT_OCCURRED"},
        "metaclaim_refused_reason": None,
    }


def audit_legacy_subject_authority(identities: Iterable[SemanticIdentity]
                                   ) -> dict[str, Any]:
    """§29 closure condition 6: legacy subject authoritative uses must be zero."""
    identities = list(identities)
    offenders = [i.identity_id for i in identities if i.legacy_subject_authoritative]
    unresolved = [i.identity_id for i in identities if not i.actor_resolved]
    return {
        "identities": len(identities),
        "legacy_subject_authoritative_uses": len(offenders),
        "actors_unresolved": len(unresolved),
        "actors_resolved": len(identities) - len(unresolved),
        "note": ("an unresolved actor is a correct outcome, not a failure: §8.8 "
                 "forbids substituting a nearest organisation for a missing one"),
        "verdict": "PASS" if not offenders else "FAIL",
    }


# ===========================================================================
# §9 — canonical serialization
# ===========================================================================

CANONICAL_SCHEMA_VERSION = "V5_6_1_CANONICAL_1"

#: §9.4 — separate hash families.  One hash may not stand for several concepts.
HASH_FAMILIES: tuple[str, ...] = (
    "semantic_identity_hash", "evidence_content_hash", "review_packet_hash",
    "review_decision_hash", "reference_record_hash",
)

#: §9.2 — identity-bearing fields, by hash family.  A field listed here and
#: present on a record MUST be covered; §9.5 requires every exclusion to carry a
#: rationale.
IDENTITY_FIELDS: Mapping[str, tuple[str, ...]] = {
    "semantic_identity_hash": (
        "record_type", "schema_version", "protocol_version",
        "source_id", "source_family_id", "intellectual_work_id",
        "publication_record_id", "manifestation_id",
        "language", "original_language", "is_translation", "translation_of_id",
        "translation_direction", "is_original_manifestation",
        "document_version", "revision_of_id", "supersedes_id", "corrects_id",
        "withdraws_id",
        "proposition_id", "claim_id", "candidate_id", "evidence_bundle_id",
        "grammatical_subject_span", "semantic_actor_id", "attribution_source_id",
        "quoted_speaker_id", "target_role", "target_entity_id", "target_pair_ids"),
    "evidence_content_hash": (
        "record_type", "schema_version", "raw_evidence_ids",
        "normalized_observation_ids", "document_context",
        "title_and_metadata_context", "counterevidence",
        "correction_or_supersession_context", "source_dependence_state",
        "exact_mapping_information", "original_language", "original_language_text",
        "translation_text", "translation_language", "is_translation"),
    "review_packet_hash": (
        "record_type", "schema_version", "protocol_version",
        "packet_question_version", "review_protocol_version",
        "proposition_id", "candidate_id", "evidence_bundle_id",
        "semantic_identity_hash", "evidence_content_hash"),
    "review_decision_hash": (
        "record_type", "schema_version", "protocol_version", "seat_id",
        "review_packet_hash", "candidate_semantic_validity",
        "boundary_repair_requirement", "exact_quotation_permission",
        "paraphrase_permission", "wording_as_written_permission",
        "support_class", "disposition"),
    "reference_record_hash": (
        "record_type", "schema_version", "protocol_version", "object_id",
        "semantic_identity_hash", "evidence_content_hash",
        "review_decision_hashes", "agreement_pattern", "adjudication_outcome",
        "final_decision", "dissent",
        "development_or_validation_partition"),
}

#: §9.5 — fields deliberately excluded, each with a reason.  An unexplained
#: exclusion is prohibited.
EXCLUDED_FIELDS: Mapping[str, str] = {
    "recorded_time": "wall-clock time of construction; not identity-bearing and "
                     "would make every hash unreproducible",
    "reasoning": "free text; its decision is hashed, and hashing prose would make "
                 "an identical decision look different for a reworded sentence",
    "derivations": "provenance trace of how an identity was reached; the identity "
                   "itself is hashed",
    "steps": "same as derivations",
    "primary_reasonings": "as reasoning",
    "adjudication_reasoning": "as reasoning",
}


def _normalise(value: Any) -> Any:
    """§9.1 — deterministic, type-aware, Unicode-normalised, null-vs-absent explicit."""
    if value is None:
        return {"__null__": True}
    if isinstance(value, str):
        return unicodedata.normalize("NFC", value)
    if isinstance(value, bool):
        return {"__bool__": value}
    if isinstance(value, (int, float)):
        return {"__num__": repr(value)}
    if isinstance(value, Mapping):
        return {unicodedata.normalize("NFC", str(k)): _normalise(v)
                for k, v in sorted(value.items(), key=lambda kv: str(kv[0]))}
    if isinstance(value, (list, tuple)):
        # Ordered by default.  A caller wanting set semantics sorts first and
        # says so; silently sorting here would erase a real ordering.
        return [_normalise(v) for v in value]
    return unicodedata.normalize("NFC", str(value))


def canonical_hash(payload: Mapping[str, Any], *, family: str,
                   record_type: str) -> str:
    """§9 — one versioned, deterministic serialization for every hash family."""
    if family not in HASH_FAMILIES:
        raise IdentityViolation(f"unknown hash family: {family}")
    fields = IDENTITY_FIELDS[family]
    covered = {name: _normalise(payload.get(name)) for name in fields
               if name in payload}
    body = {"__family__": family, "__record_type__": record_type,
            "__schema_version__": CANONICAL_SCHEMA_VERSION, "fields": covered}
    return _sha256(json.dumps(body, sort_keys=True, ensure_ascii=False,
                              separators=(",", ":")).encode("utf-8")).hexdigest()


def hash_field_coverage(record_type: str, payload: Mapping[str, Any]
                        ) -> dict[str, Any]:
    """§9.5 — the coverage manifest.  No unexplained excluded field is permitted."""
    present = set(payload)
    rows, unexplained = {}, []
    for family, fields in IDENTITY_FIELDS.items():
        included = sorted(present & set(fields))
        rows[family] = {"included": included,
                        "declared_but_absent": sorted(set(fields) - present)}
    all_covered = {f for fields in IDENTITY_FIELDS.values() for f in fields}
    for name in sorted(present - all_covered):
        if name not in EXCLUDED_FIELDS:
            unexplained.append(name)
    return {
        "record_type": record_type,
        "schema_version": CANONICAL_SCHEMA_VERSION,
        "by_family": rows,
        "explained_exclusions": {k: v for k, v in EXCLUDED_FIELDS.items()
                                 if k in present},
        "unexplained_excluded_fields": unexplained,
        "verdict": "PASS" if not unexplained else "FAIL",
    }


def manifestation_identity(*, source_id: str, language: str, is_translation: bool,
                           translation_of_id: str | None,
                           intellectual_work_id: str) -> dict[str, str]:
    """§9.3 — four distinct identities that must never collapse into one.

    An original and its translation share an intellectual work and must not
    share a manifestation identity.  V5.6's bundle hash omitted
    ``translation_language``, which is precisely the field that separates them.
    """
    payload = {"record_type": "MANIFESTATION", "source_id": source_id,
               "language": language, "is_translation": is_translation,
               "translation_of_id": translation_of_id,
               "intellectual_work_id": intellectual_work_id,
               "is_original_manifestation": not is_translation,
               "schema_version": CANONICAL_SCHEMA_VERSION}
    return {
        "INTELLECTUAL_WORK_IDENTITY": intellectual_work_id,
        "MANIFESTATION_IDENTITY": canonical_hash(
            payload, family="semantic_identity_hash", record_type="MANIFESTATION"),
        "TRANSLATION_RELATIONSHIP": ("TRANSLATION_OF" if is_translation
                                     else "ORIGINAL_MANIFESTATION"),
        "PUBLICATION_IDENTITY": stable_id("v5-6-1-publication", source_id, language),
    }
