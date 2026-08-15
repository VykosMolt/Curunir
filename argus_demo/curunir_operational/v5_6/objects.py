"""V5.6 §7–§8 — the canonical adjudication object model.

The V5.3 panel adjudicated claim support and report publication as independent
questions over objects that were only loosely the same thing.  Two consequences
followed, and both are fatal to a reference standard:

  * A report sentence and the claim beneath it were reconnected after the fact by
    sentence matching, so a label could govern something other than the object it
    was recorded against.
  * "Vendor X states that System Y is operational" and "System Y is operational"
    were treated as one proposition.  Evidence establishing the first says nothing
    about the second, so a reviewer who found the metaclaim supported and the
    underlying fact unsupported had no way to record both.

This module fixes the identity layer.  Every surface refers to the same
``proposition_id``, ``claim_id`` and ``evidence_bundle_id``; a factual claim and
an attributed metaclaim about it are *separate propositions with separate IDs and
separate support decisions*; and the report layer decomposes a sentence into the
propositions it actually asserts.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from ..v5_1.models import Record, now_utc, sha256, stable_id


class IdentityViolation(RuntimeError):
    """An object was constructed in a way that breaks canonical identity."""


# ---------------------------------------------------------------------------
# §8 — the kinds of thing a proposition can be.
# ---------------------------------------------------------------------------

PROPOSITION_KINDS: tuple[str, ...] = (
    "UNDERLYING_FACT_CLAIM",
    "ATTRIBUTED_METACLAIM",
    "ANALYTIC_INFERENCE",
    "HYPOTHESIS",
    "UNCERTAINTY_STATEMENT",
    "CORRECTION_STATEMENT",
)

#: Kinds that assert something about the world directly.  A metaclaim does not:
#: it asserts that someone said something.
DIRECT_FACTUAL_KINDS = frozenset({"UNDERLYING_FACT_CLAIM", "CORRECTION_STATEMENT"})

#: What an attributed metaclaim asserts.  Section 8 requires every
#: attribution-preserving sentence to declare which of these it means.
METACLAIM_ASSERTIONS: tuple[str, ...] = (
    "THE_EXTERNAL_STATEMENT_OCCURRED",
    "THE_EXTERNAL_STATEMENT_IS_TRUE",
)


@dataclass(frozen=True)
class EvidenceBundleRecord(Record):
    """§7.3 — the immutable evidence every seat sees, identically."""

    evidence_bundle_id: str
    raw_evidence_ids: tuple[str, ...]
    normalized_observation_ids: tuple[str, ...]
    document_context: str
    title_and_metadata_context: str
    counterevidence: tuple[str, ...]
    correction_or_supersession_context: tuple[str, ...]
    source_dependence_state: str
    exact_mapping_information: Mapping[str, Any]
    original_language: str
    original_language_text: str
    translation_text: str | None
    translation_language: str | None
    recorded_time: str

    def __post_init__(self) -> None:
        if not self.raw_evidence_ids:
            raise IdentityViolation("an evidence bundle requires at least one raw record")
        if self.translation_text is not None and self.translation_language is None:
            raise IdentityViolation(
                "a translation must declare its language; original and translation "
                "are shown separately and may never be merged")

    @property
    def integrity_hash(self) -> str:
        return sha256({
            "raw": sorted(self.raw_evidence_ids),
            "observations": sorted(self.normalized_observation_ids),
            "document_context": self.document_context,
            "counterevidence": sorted(self.counterevidence),
            "correction": sorted(self.correction_or_supersession_context),
            "dependence": self.source_dependence_state,
            "mapping": dict(self.exact_mapping_information),
            "original_language": self.original_language,
            "original_language_text": self.original_language_text,
            "translation_text": self.translation_text,
            # Both of these are shown to every seat, so both must be covered.
            # translation_language especially: it is the field that keeps an
            # original and its translation distinct, and omitting it let a
            # translation be re-labelled without changing the hash.
            "title_and_metadata_context": self.title_and_metadata_context,
            "translation_language": self.translation_language,
        })


def evidence_bundle(*, raw_evidence_ids: Iterable[str],
                    normalized_observation_ids: Iterable[str] = (),
                    document_context: str = "",
                    title_and_metadata_context: str = "",
                    counterevidence: Iterable[str] = (),
                    correction_or_supersession_context: Iterable[str] = (),
                    source_dependence_state: str = "INDEPENDENCE_UNKNOWN",
                    exact_mapping_information: Mapping[str, Any] | None = None,
                    original_language: str = "en",
                    original_language_text: str = "",
                    translation_text: str | None = None,
                    translation_language: str | None = None) -> EvidenceBundleRecord:
    raw = tuple(raw_evidence_ids)
    return EvidenceBundleRecord(
        stable_id("v5-6-bundle", "|".join(sorted(raw)), original_language_text[:200]),
        raw, tuple(normalized_observation_ids), document_context,
        title_and_metadata_context, tuple(counterevidence),
        tuple(correction_or_supersession_context), source_dependence_state,
        dict(exact_mapping_information or {}), original_language,
        original_language_text, translation_text, translation_language, now_utc())


@dataclass(frozen=True)
class CanonicalPropositionRecord(Record):
    """§7.1 — one proposition, one identity, shared across every surface."""

    proposition_id: str
    claim_id: str
    investigation_id: str
    kind: str
    source_family_ids: tuple[str, ...]
    evidence_bundle_id: str
    raw_evidence_ids: tuple[str, ...]
    normalized_observation_ids: tuple[str, ...]
    subject: str
    predicate: str
    object_or_value: str
    attribution: str | None
    polarity: str
    modality: str
    lifecycle_state: str
    event_time: str | None
    valid_interval: tuple[str | None, str | None]
    geographic_scope: tuple[str, ...]
    institutional_scope: tuple[str, ...]
    dependence_state: str
    counterevidence_ids: tuple[str, ...]
    report_location_ids: tuple[str, ...]
    historical_label_ids: tuple[str, ...]
    # §8 — a metaclaim points at the fact it is about.
    underlying_proposition_id: str | None
    metaclaim_assertion: str | None
    recorded_time: str

    def __post_init__(self) -> None:
        if self.kind not in PROPOSITION_KINDS:
            raise IdentityViolation(f"unknown proposition kind: {self.kind}")
        if self.kind == "ATTRIBUTED_METACLAIM":
            if not self.attribution:
                raise IdentityViolation(
                    "an attributed metaclaim must name who made the statement")
            if self.metaclaim_assertion not in METACLAIM_ASSERTIONS:
                raise IdentityViolation(
                    "an attributed metaclaim must declare whether it asserts that the "
                    "external statement OCCURRED or that it is TRUE (Section 8)")
        else:
            if self.metaclaim_assertion is not None:
                raise IdentityViolation(
                    f"{self.kind} is not a metaclaim and may not carry a "
                    "metaclaim assertion")
            if self.underlying_proposition_id is not None:
                raise IdentityViolation(
                    f"{self.kind} is not a metaclaim and may not point at an "
                    "underlying proposition")
        if not self.evidence_bundle_id:
            raise IdentityViolation("a proposition requires an evidence bundle")

    @property
    def is_metaclaim(self) -> bool:
        return self.kind == "ATTRIBUTED_METACLAIM"


def canonical_proposition(*, claim_id: str, investigation_id: str, subject: str,
                          predicate: str, object_or_value: str,
                          evidence_bundle_id: str,
                          kind: str = "UNDERLYING_FACT_CLAIM",
                          source_family_ids: Iterable[str] = (),
                          raw_evidence_ids: Iterable[str] = (),
                          normalized_observation_ids: Iterable[str] = (),
                          attribution: str | None = None,
                          polarity: str = "POSITIVE", modality: str = "ASSERTED",
                          lifecycle_state: str = "UNKNOWN",
                          event_time: str | None = None,
                          valid_interval: tuple[str | None, str | None] = (None, None),
                          geographic_scope: Iterable[str] = (),
                          institutional_scope: Iterable[str] = (),
                          dependence_state: str = "INDEPENDENCE_UNKNOWN",
                          counterevidence_ids: Iterable[str] = (),
                          report_location_ids: Iterable[str] = (),
                          historical_label_ids: Iterable[str] = (),
                          underlying_proposition_id: str | None = None,
                          metaclaim_assertion: str | None = None,
                          ) -> CanonicalPropositionRecord:
    return CanonicalPropositionRecord(
        stable_id("v5-6-proposition", claim_id, kind, subject, predicate,
                  object_or_value, attribution or "", evidence_bundle_id),
        claim_id, investigation_id, kind, tuple(source_family_ids),
        evidence_bundle_id, tuple(raw_evidence_ids),
        tuple(normalized_observation_ids), subject, predicate, object_or_value,
        attribution, polarity, modality, lifecycle_state, event_time,
        tuple(valid_interval), tuple(geographic_scope), tuple(institutional_scope),
        dependence_state, tuple(counterevidence_ids), tuple(report_location_ids),
        tuple(historical_label_ids), underlying_proposition_id, metaclaim_assertion,
        now_utc())


def split_fact_and_metaclaim(*, claim_id: str, investigation_id: str,
                             speaker: str, subject: str, predicate: str,
                             object_or_value: str, evidence_bundle_id: str,
                             **shared: Any
                             ) -> tuple[CanonicalPropositionRecord,
                                        CanonicalPropositionRecord]:
    """§8 — build the underlying fact and the metaclaim about it, as two objects.

    "Vendor X states that System Y is operational" and "System Y is operational"
    are different propositions.  Evidence that Vendor X made the statement fully
    supports the first and says nothing about the second.  Returning them as a
    pair makes it impossible to record one label for both.
    """
    fact = canonical_proposition(
        claim_id=claim_id, investigation_id=investigation_id, subject=subject,
        predicate=predicate, object_or_value=object_or_value,
        evidence_bundle_id=evidence_bundle_id, kind="UNDERLYING_FACT_CLAIM",
        **shared)
    meta = canonical_proposition(
        claim_id=claim_id, investigation_id=investigation_id, subject=speaker,
        predicate="states that", object_or_value=f"{subject} {predicate} {object_or_value}",
        evidence_bundle_id=evidence_bundle_id, kind="ATTRIBUTED_METACLAIM",
        attribution=speaker, underlying_proposition_id=fact.proposition_id,
        metaclaim_assertion="THE_EXTERNAL_STATEMENT_OCCURRED", **shared)
    return fact, meta


@dataclass(frozen=True)
class CanonicalExtractionCandidateRecord(Record):
    """§7.2 — one extraction candidate, with its competing alternatives."""

    candidate_id: str
    source_id: str
    source_family_id: str
    raw_span: str
    normalized_span: str
    mapping: str
    page_or_section: str
    candidate_boundary: str
    expanded_context: str
    semantic_structure: Mapping[str, Any]
    material_quantities: tuple[str, ...]
    attribution: str | None
    modality: str
    polarity: str
    temporal_scope: tuple[str | None, str | None]
    lifecycle_state: str
    competing_candidate_ids: tuple[str, ...]
    historical_label_ids: tuple[str, ...]
    recorded_time: str

    def __post_init__(self) -> None:
        if not self.raw_span.strip():
            raise IdentityViolation("an extraction candidate requires a raw span")
        if self.candidate_id in self.competing_candidate_ids:
            raise IdentityViolation("a candidate may not compete with itself")


def canonical_candidate(*, source_id: str, source_family_id: str, raw_span: str,
                        normalized_span: str | None = None, mapping: str = "",
                        page_or_section: str = "", candidate_boundary: str = "SENTENCE",
                        expanded_context: str = "",
                        semantic_structure: Mapping[str, Any] | None = None,
                        material_quantities: Iterable[str] = (),
                        attribution: str | None = None, modality: str = "ASSERTED",
                        polarity: str = "POSITIVE",
                        temporal_scope: tuple[str | None, str | None] = (None, None),
                        lifecycle_state: str = "UNKNOWN",
                        competing_candidate_ids: Iterable[str] = (),
                        historical_label_ids: Iterable[str] = (),
                        ) -> CanonicalExtractionCandidateRecord:
    return CanonicalExtractionCandidateRecord(
        stable_id("v5-6-candidate", source_id, raw_span[:200], candidate_boundary),
        source_id, source_family_id, raw_span, normalized_span or raw_span, mapping,
        page_or_section, candidate_boundary, expanded_context,
        dict(semantic_structure or {}), tuple(material_quantities), attribution,
        modality, polarity, tuple(temporal_scope), lifecycle_state,
        tuple(competing_candidate_ids), tuple(historical_label_ids), now_utc())


@dataclass(frozen=True)
class ReportPropositionRecord(Record):
    """§7.4 — one factual proposition asserted by one report sentence.

    A sentence may assert several.  Each record references exactly one canonical
    proposition, so a label recorded here governs a known object rather than a
    string that has to be matched back later.
    """

    report_proposition_id: str
    report_sentence_id: str
    sentence_text: str
    proposition_id: str
    claim_id: str
    evidence_bundle_id: str
    section: str
    asserts_underlying_fact: bool
    recorded_time: str

    def __post_init__(self) -> None:
        if not self.proposition_id:
            raise IdentityViolation(
                "a report proposition must reference exactly one canonical "
                "proposition; fuzzy sentence matching is not permitted (Section 7.1)")


def decompose_sentence(*, report_sentence_id: str, sentence_text: str,
                       propositions: Sequence[CanonicalPropositionRecord],
                       section: str = "DETAILED_REPORT"
                       ) -> tuple[ReportPropositionRecord, ...]:
    """Decompose one report sentence into the propositions it asserts."""
    out = []
    for proposition in propositions:
        out.append(ReportPropositionRecord(
            stable_id("v5-6-reportprop", report_sentence_id, proposition.proposition_id),
            report_sentence_id, sentence_text, proposition.proposition_id,
            proposition.claim_id, proposition.evidence_bundle_id, section,
            proposition.kind in DIRECT_FACTUAL_KINDS, now_utc()))
    return tuple(out)


# ---------------------------------------------------------------------------
# Identity audits — §34 closure conditions 2 and 8.
# ---------------------------------------------------------------------------

def audit_shared_identity(propositions: Iterable[CanonicalPropositionRecord],
                          report_propositions: Iterable[ReportPropositionRecord],
                          ) -> dict[str, Any]:
    """Every overlapping claim and report proposition uses one canonical ID."""
    by_id = {p.proposition_id: p for p in propositions}
    # Materialise both sides: these are iterables, and consuming one of them
    # twice silently yields nothing the second time.
    report_propositions = list(report_propositions)
    orphans, bundle_mismatch, claim_mismatch = [], [], []
    for record in report_propositions:
        proposition = by_id.get(record.proposition_id)
        if proposition is None:
            orphans.append(record.report_proposition_id)
            continue
        if proposition.evidence_bundle_id != record.evidence_bundle_id:
            bundle_mismatch.append(record.report_proposition_id)
        if proposition.claim_id != record.claim_id:
            claim_mismatch.append(record.report_proposition_id)
    return {
        "propositions": len(by_id),
        "report_propositions": len(report_propositions),
        "orphan_report_propositions": len(orphans),
        "evidence_bundle_mismatches": len(bundle_mismatch),
        "claim_id_mismatches": len(claim_mismatch),
        "examples": (orphans + bundle_mismatch + claim_mismatch)[:10],
        "verdict": "PASS" if not (orphans or bundle_mismatch or claim_mismatch) else "FAIL",
    }


def audit_fact_metaclaim_separation(propositions: Iterable[CanonicalPropositionRecord]
                                    ) -> dict[str, Any]:
    """No metaclaim shares an identity with the fact it is about."""
    propositions = list(propositions)
    by_id = {p.proposition_id: p for p in propositions}
    metaclaims = [p for p in propositions if p.is_metaclaim]
    dangling = [m.proposition_id for m in metaclaims
                if m.underlying_proposition_id
                and m.underlying_proposition_id not in by_id]
    collisions = [m.proposition_id for m in metaclaims
                  if m.underlying_proposition_id == m.proposition_id]
    unattributed = [m.proposition_id for m in metaclaims if not m.attribution]
    return {
        "propositions": len(propositions),
        "metaclaims": len(metaclaims),
        "underlying_facts_present": sum(
            1 for m in metaclaims
            if m.underlying_proposition_id and m.underlying_proposition_id in by_id),
        "dangling_underlying_references": len(dangling),
        "identity_collisions": len(collisions),
        "unattributed_metaclaims": len(unattributed),
        "verdict": "PASS" if not (dangling or collisions or unattributed) else "FAIL",
    }


def deduplicate(propositions: Iterable[CanonicalPropositionRecord]
                ) -> tuple[CanonicalPropositionRecord, ...]:
    """§16.1 — deduplicate by canonical identity, never by sentence text."""
    seen: dict[str, CanonicalPropositionRecord] = {}
    for proposition in propositions:
        seen.setdefault(proposition.proposition_id, proposition)
    return tuple(seen.values())
