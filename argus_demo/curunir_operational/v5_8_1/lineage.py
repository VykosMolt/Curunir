"""V5.8.1 §6 — D23: evidentiary independence, which is not textual difference.

The pilot assessed a claim FULL_SUPPORT on evidence that was a byte-identical
copy of the claim, then published it.  The tempting fix — "identical text means
unsupported" — is wrong, and wrong in a way that would cost more than the defect
does.  Two independent bodies reporting the same figure in the same words is the
strongest evidence there is.  A translation of a statute carries the statute's
meaning exactly.  Identity of wording is not the problem.

The problem is circular provenance: evidence whose existence derives from the
claim it is offered to support.  That is a question about lineage, not about
characters, so this module models lineage and answers three separate questions
that the pilot had collapsed into one:

  1. Is this evidence *the claim itself*, or derived from it?          (circular)
  2. Is it a different observation of the *same intellectual work*?    (not independent)
  3. Is it an independently captured source?                          (corroborating)

Only (1) is a construction defect.  (2) may carry semantic truth and may never
be counted as corroboration.  (3) is evaluated normally, identical wording or not.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from ..v5_1.models import Record, now_utc, stable_id


class LineageViolation(RuntimeError):
    """Claim support was asked to run on evidence it may not use."""


# ===========================================================================
# §6.1 — the origin states
# ===========================================================================

EVIDENCE_ORIGIN_STATES: tuple[str, ...] = (
    "INDEPENDENT_SOURCE_EVIDENCE", "SAME_INTELLECTUAL_WORK_EVIDENCE",
    "DUPLICATE_MANIFESTATION_EVIDENCE", "CLAIM_DERIVED_EVIDENCE",
    "REPORT_DERIVED_EVIDENCE", "GENERATED_SUMMARY_EVIDENCE", "SELF_EVIDENCE",
    "LINEAGE_UNRESOLVED",
)

#: Origins that are an upstream construction failure.  A claim-support class is
#: not an answer to any of these — the question was never well posed.
CIRCULAR_ORIGINS: frozenset[str] = frozenset({
    "SELF_EVIDENCE", "CLAIM_DERIVED_EVIDENCE", "REPORT_DERIVED_EVIDENCE",
    "GENERATED_SUMMARY_EVIDENCE",
})

#: Origins that may bear on semantic truth but never add corroborative weight.
NON_CORROBORATING_ORIGINS: frozenset[str] = frozenset({
    "SAME_INTELLECTUAL_WORK_EVIDENCE", "DUPLICATE_MANIFESTATION_EVIDENCE",
})

#: The only origin that counts as an independent corroborating observation.
CORROBORATING_ORIGINS: frozenset[str] = frozenset({"INDEPENDENT_SOURCE_EVIDENCE"})


@dataclass(frozen=True)
class EvidenceLineageRecord(Record):
    """Where one piece of evidence came from, relative to one claim."""

    lineage_id: str
    claim_id: str
    claim_origin: str
    claim_derivation_ids: tuple[str, ...]
    evidence_id: str
    source_observation_id: str
    source_manifestation_id: str
    source_intellectual_work_id: str
    evidence_derivation_ids: tuple[str, ...]
    normalization_lineage: tuple[str, ...]
    report_proposition_lineage: tuple[str, ...]
    translation_lineage: tuple[str, ...]
    capture_timestamp: str
    independent_origin_state: str
    deciding_signal: str
    recorded_time: str

    @property
    def circular(self) -> bool:
        return self.independent_origin_state in CIRCULAR_ORIGINS

    @property
    def corroborating(self) -> bool:
        return self.independent_origin_state in CORROBORATING_ORIGINS


def _normalised(text: Any) -> str:
    """Whitespace- and case-folded proposition text, for identity only."""
    return " ".join(str(text or "").split()).casefold()


def _ids(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return (value,)
    return tuple(str(item) for item in value)


def classify_origin(*, claim: Mapping[str, Any], evidence: Mapping[str, Any]
                    ) -> tuple[str, str]:
    """Decide one evidence item's origin state relative to one claim.

    The order matters and is the whole architecture: circularity is checked
    before identity of work, and identity of work before anything about wording,
    because a claim used as its own evidence is a defect whether or not the two
    happen to sit in the same publication.
    """
    claim_id = str(claim.get("proposition_id") or claim.get("candidate_id") or "")
    evidence_id = str(evidence.get("proposition_id") or evidence.get("candidate_id") or "")

    # 1. The same object, offered to support itself.
    if evidence_id and evidence_id == claim_id:
        return "SELF_EVIDENCE", "the evidence is the claim object"

    # 2. Evidence that exists because the claim does.
    evidence_derivation = set(_ids(evidence.get("derivation_ids"))) | \
        set(_ids(evidence.get("derived_from")))
    if claim_id and claim_id in evidence_derivation:
        return "CLAIM_DERIVED_EVIDENCE", (
            "the evidence records the claim as its own antecedent")
    if evidence.get("origin_kind") in ("REPORT_PROPOSITION", "REPORT_SENTENCE"):
        report_lineage = set(_ids(evidence.get("report_proposition_lineage")))
        if not claim_id or claim_id in report_lineage or not report_lineage:
            return "REPORT_DERIVED_EVIDENCE", (
                "the evidence is a report proposition, which is downstream of "
                "claims rather than upstream of them")
    if evidence.get("origin_kind") in ("GENERATED_SUMMARY", "MODEL_SUMMARY",
                                       "SYNTHETIC"):
        return "GENERATED_SUMMARY_EVIDENCE", (
            "the evidence was generated rather than observed")

    # 3. A normalisation of the claim's own span is the claim wearing a hat.
    claim_normalisation = set(_ids(claim.get("normalization_lineage"))) | {claim_id}
    evidence_normalisation = set(_ids(evidence.get("normalization_lineage"))) | \
        {evidence_id}
    if claim_normalisation & evidence_normalisation - {""}:
        return "CLAIM_DERIVED_EVIDENCE", (
            "claim and evidence share a normalisation ancestor, so the evidence "
            "is a rendering of the claim rather than an observation of the world")

    # 4. The same proposition, inside the same work, offered to support itself.
    #    Two manifestations of one fact sheet carry the same sentence; quoting
    #    the English page to support the Arabic page is not a second observation
    #    of the world, it is the same statement re-rendered.  Across *different*
    #    works the identical wording is real corroboration and is left alone —
    #    that distinction is the whole point of checking the work identity here
    #    rather than checking the characters anywhere.
    claim_text = _normalised(claim.get("text") or claim.get("normalized_statement")
                             or claim.get("raw_span"))
    evidence_text = _normalised(evidence.get("text")
                                or evidence.get("normalized_statement")
                                or evidence.get("raw_span"))
    claim_work_early = str(claim.get("intellectual_work_id") or "")
    evidence_work_early = str(evidence.get("intellectual_work_id") or "")
    if (claim_text and claim_text == evidence_text
            and claim_work_early and claim_work_early == evidence_work_early):
        return "SELF_EVIDENCE", (
            "claim and evidence are the same proposition within one intellectual "
            "work; the wording is not a second observation")

    # 5. Same observation captured once: a duplicate, not a second witness.
    claim_observation = str(claim.get("source_observation_id") or "")
    evidence_observation = str(evidence.get("source_observation_id") or "")
    if claim_observation and claim_observation == evidence_observation:
        return "DUPLICATE_MANIFESTATION_EVIDENCE", (
            "claim and evidence are the same observation")

    claim_manifestation = str(claim.get("manifestation_id") or "")
    evidence_manifestation = str(evidence.get("manifestation_id") or "")
    claim_work = str(claim.get("intellectual_work_id") or "")
    evidence_work = str(evidence.get("intellectual_work_id") or "")

    if claim_manifestation and claim_manifestation == evidence_manifestation:
        return "DUPLICATE_MANIFESTATION_EVIDENCE", (
            "claim and evidence are the same manifestation of the same work")

    # 6. Same work, different manifestation — a translation, a mirror, a reprint.
    #    It can settle what the work says.  It cannot corroborate that the work
    #    is right, because there is only one work.
    if claim_work and claim_work == evidence_work:
        translation = set(_ids(evidence.get("translation_lineage")))
        signal = ("the evidence is a translation of the same work"
                  if translation else
                  "claim and evidence are manifestations of one intellectual work")
        return "SAME_INTELLECTUAL_WORK_EVIDENCE", signal

    # 7. Nothing observed about where it came from.
    if not evidence_observation and not evidence_work and not evidence_manifestation:
        return "LINEAGE_UNRESOLVED", (
            "no observation, manifestation or work identity is recorded for the "
            "evidence, so its independence cannot be asserted")

    return "INDEPENDENT_SOURCE_EVIDENCE", (
        "distinct observation of a distinct intellectual work")


def build(*, claim: Mapping[str, Any], evidence: Mapping[str, Any]
          ) -> EvidenceLineageRecord:
    state, signal = classify_origin(claim=claim, evidence=evidence)
    claim_id = str(claim.get("proposition_id") or claim.get("candidate_id") or "")
    evidence_id = str(evidence.get("proposition_id") or evidence.get("candidate_id") or "")
    return EvidenceLineageRecord(
        stable_id("v5-8-1-lineage", claim_id, evidence_id),
        claim_id, str(claim.get("origin_kind") or "OBSERVED_SOURCE_SPAN"),
        _ids(claim.get("derivation_ids")), evidence_id,
        str(evidence.get("source_observation_id") or ""),
        str(evidence.get("manifestation_id") or ""),
        str(evidence.get("intellectual_work_id") or ""),
        _ids(evidence.get("derivation_ids")),
        _ids(evidence.get("normalization_lineage")),
        _ids(evidence.get("report_proposition_lineage")),
        _ids(evidence.get("translation_lineage")),
        str(evidence.get("capture_timestamp") or evidence.get("captured_time") or ""),
        state, signal, now_utc())


# ===========================================================================
# §6.3 — upstream eligibility
# ===========================================================================

@dataclass(frozen=True)
class SupportEligibility(Record):
    """Whether claim support may run at all on this bundle."""

    eligibility_id: str
    claim_id: str
    eligible: bool
    refusal_state: str | None
    refusal_reason: str | None
    lineage: tuple[EvidenceLineageRecord, ...]
    independent_evidence_ids: tuple[str, ...]
    non_corroborating_evidence_ids: tuple[str, ...]
    corroboration_count: int
    recorded_time: str

    @property
    def terminal_state(self) -> str:
        return "VALID" if self.eligible else "CONSTRUCTION_DEFECT"


def assess_eligibility(*, claim: Mapping[str, Any],
                       evidence_items: Sequence[Mapping[str, Any]]
                       ) -> SupportEligibility:
    """§6.3 — claim support runs only on non-circular, grounded evidence.

    A circular item is not filtered out quietly.  Silently dropping it would
    leave the remaining evidence to answer a question that was posed wrongly,
    and would hide the construction fault from every downstream audit.
    """
    claim_id = str(claim.get("proposition_id") or claim.get("candidate_id") or "")
    lineage = tuple(build(claim=claim, evidence=item) for item in evidence_items)
    circular = [record for record in lineage if record.circular]
    #: Witnesses are counted by *work*, not by item.  Four spans lifted from one
    #: page are one observation of one work; counting them as four is precisely
    #: the arithmetic that lets a single source read as a consensus.
    independent = tuple(record.source_intellectual_work_id or record.evidence_id
                        for record in lineage if record.corroborating)
    non_corroborating = tuple(record.evidence_id for record in lineage
                              if record.independent_origin_state
                              in NON_CORROBORATING_ORIGINS)

    if not lineage:
        return SupportEligibility(
            stable_id("v5-8-1-eligibility", claim_id), claim_id, False,
            "NO_EVIDENCE_OFFERED", "no evidence item was supplied", lineage,
            (), (), 0, now_utc())
    if circular:
        first = circular[0]
        return SupportEligibility(
            stable_id("v5-8-1-eligibility", claim_id), claim_id, False,
            first.independent_origin_state,
            f"{first.independent_origin_state}: {first.deciding_signal}",
            lineage, independent, non_corroborating, 0, now_utc())
    unresolved = [record for record in lineage
                  if record.independent_origin_state == "LINEAGE_UNRESOLVED"]
    if unresolved and not independent and not non_corroborating:
        return SupportEligibility(
            stable_id("v5-8-1-eligibility", claim_id), claim_id, False,
            "LINEAGE_UNRESOLVED",
            "no evidence item has a resolvable origin; independence cannot be "
            "asserted and support may not be inferred from an unknown source",
            lineage, independent, non_corroborating, 0, now_utc())
    return SupportEligibility(
        stable_id("v5-8-1-eligibility", claim_id), claim_id, True, None, None,
        lineage, independent, non_corroborating, len(set(independent)), now_utc())


def corroboration_weight(eligibility: SupportEligibility) -> dict[str, Any]:
    """§6.2 — how many independent witnesses there actually are.

    Duplicate manifestations contribute zero.  Same-work manifestations
    contribute zero.  This is the number that may appear behind any language
    about independent corroboration.
    """
    return {
        "independent_witnesses": eligibility.corroboration_count,
        "independent_works": list(dict.fromkeys(eligibility.independent_evidence_ids)),
        "same_work_or_duplicate_items": len(eligibility.non_corroborating_evidence_ids),
        "independence_language_permitted": eligibility.corroboration_count >= 2,
        "reason": ("only one distinct intellectual work is present; independent "
                   "corroboration may not be asserted"
                   if eligibility.corroboration_count < 2 else
                   f"{eligibility.corroboration_count} distinct works observed "
                   "independently"),
    }


def audit(eligibilities: Iterable[SupportEligibility]) -> dict[str, Any]:
    """§6.5 — what the guard refused, and on what grounds."""
    rows = list(eligibilities)
    states: dict[str, int] = {}
    for row in rows:
        for record in row.lineage:
            states[record.independent_origin_state] = states.get(
                record.independent_origin_state, 0) + 1
    refused = [row for row in rows if not row.eligible]
    return {
        "claims_assessed": len(rows),
        "eligible": len(rows) - len(refused),
        "refused": len(refused),
        "refusal_states": {state: sum(1 for row in refused
                                      if row.refusal_state == state)
                           for state in sorted({row.refusal_state for row in refused
                                                if row.refusal_state})},
        "evidence_origin_states": dict(sorted(states.items())),
        "claims_with_independent_corroboration": sum(
            1 for row in rows if row.corroboration_count >= 2),
        "claims_whose_only_evidence_is_the_same_work": sum(
            1 for row in rows if row.eligible and row.corroboration_count == 0),
    }


__all__ = [
    "LineageViolation", "EVIDENCE_ORIGIN_STATES", "CIRCULAR_ORIGINS",
    "NON_CORROBORATING_ORIGINS", "CORROBORATING_ORIGINS",
    "EvidenceLineageRecord", "SupportEligibility", "classify_origin", "build",
    "assess_eligibility", "corroboration_weight", "audit",
]
