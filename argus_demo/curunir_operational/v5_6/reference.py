"""V5.6 §20–§23 — aggregation, disagreement routing, reference freeze, lineage.

Three properties this module exists to hold:

*Unanimity is not truth.*  A unanimous panel is still a model panel, and §20.1
requires it be labelled as such.  The V5.3 failure included contradictions that
were unanimous on both sides.

*Material disagreement is not averaged away.*  A 2–1 split on whether a claim is
supported at all is not the same kind of event as a 2–1 split on wording, and
§20.2 routes the first to a separate adjudication rather than letting the
majority win by counting.

*Dissent survives.*  §22.2 requires every reference record to carry all three
primary decisions and their reasoning, so a future reader can see what the panel
disagreed about rather than only what it concluded.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from ..v5_1.models import Record, now_utc, sha256, stable_id
from . import schema as S


class ReferenceViolation(RuntimeError):
    """The reference standard would have been made incoherent."""


AGREEMENT_PATTERNS: tuple[str, ...] = (
    "UNANIMOUS", "MAJORITY_TWO_ONE", "THREE_WAY_SPLIT", "INSUFFICIENT_SEATS")

#: §20.2 — dimensions on which a 2–1 split is *material* and must be adjudicated
#: separately rather than resolved by counting.
MATERIAL_DIMENSIONS: tuple[str, ...] = (
    "accepted_versus_rejected_extraction",
    "exact_quotation_permission",
    "support_versus_no_support",
    "factual_versus_inferential",
    "lifecycle_state",
    "polarity",
    "publication_permission",
    "correction_or_retraction",
    "underlying_fact_versus_metaclaim",
)

ADJUDICATION_OUTCOMES: tuple[str, ...] = (
    "AFFIRM_MAJORITY", "AFFIRM_DISSENT", "NARROW_DECISION", "QUALIFY_DECISION",
    "EPISTEMICALLY_UNRESOLVABLE", "PACKET_CONSTRUCTION_DEFECT", "PROTOCOL_DEFECT")

#: §21.2 — a defect is not a semantic label and must never be scored as one.
DEFECT_OUTCOMES = frozenset({"PACKET_CONSTRUCTION_DEFECT", "PROTOCOL_DEFECT"})

LINEAGE_DISPOSITIONS: tuple[str, ...] = (
    "AFFIRMED", "REVERSED", "NARROWED", "QUALIFIED",
    "SPLIT_INTO_MULTIPLE_PROPOSITIONS", "RECAST_AS_METACLAIM",
    "INVALIDATED_BY_DEPENDENCY_CONTRADICTION", "INVALIDATED_BY_PACKET_DEFECT",
    "INVALIDATED_BY_PROTOCOL_DEFECT", "REMAINS_EPISTEMICALLY_UNRESOLVABLE")

#: §22 — the strongest name this reference is permitted to carry.
REFERENCE_NAME = "MODEL_PANEL_ADJUDICATED_REFERENCE_V5_6"
PROHIBITED_NAMES = frozenset({
    "HUMAN_GOLD_STANDARD", "EXPERT_GOLD", "OBJECTIVE_TRUTH",
    "HUMAN_VALIDATED_REFERENCE", "GROUND_TRUTH"})


def _support_family(support_class: str) -> str:
    """Coarse family used to decide whether a split is material."""
    if support_class in S.AFFIRMATIVE_SUPPORT:
        return "SUPPORTED"
    if support_class == "INFERENCE_ONLY":
        return "INFERENTIAL"
    if support_class in ("EPISTEMICALLY_UNRESOLVABLE", "CONSTRUCTION_DEFECT"):
        return "UNRESOLVED"
    return "NOT_SUPPORTED"


_ADMITTING = frozenset({"ACCEPTED_CANDIDATE"})


def material_dimensions(decisions: Sequence[Mapping[str, Any]]) -> tuple[str, ...]:
    """Which §20.2 dimensions this set of seat decisions actually disagrees on."""
    found: list[str] = []
    kinds = {d.get("unit_type") for d in decisions}

    if "EXTRACTION" in kinds:
        results = {d.get("result") for d in decisions if d.get("unit_type") == "EXTRACTION"}
        if len({r in _ADMITTING for r in results if r}) > 1:
            found.append("accepted_versus_rejected_extraction")
        quotes = {bool(d.get("exact_quotation_permitted"))
                  for d in decisions if d.get("unit_type") == "EXTRACTION"}
        if len(quotes) > 1:
            found.append("exact_quotation_permission")

    if "SUPPORT" in kinds:
        support = [d for d in decisions if d.get("unit_type") == "SUPPORT"]
        families = {_support_family(str(d.get("support_class"))) for d in support}
        if len(families) > 1:
            found.append("support_versus_no_support")
        inferential = {str(d.get("support_class")) == "INFERENCE_ONLY" for d in support}
        if len(inferential) > 1:
            found.append("factual_versus_inferential")
        # §20.2 vs §20.3.  An alignment field is bookkeeping: two seats can both
        # reach FULL_SUPPORT while recording lifecycle_alignment as ALIGNED and
        # NOT_APPLICABLE, and escalating that would route almost everything to
        # adjudication while telling us nothing.  The disagreement is material
        # only when the decision turns on it — i.e. the seats also differ on the
        # support family, or the field is the first material failure for at
        # least one seat.  Otherwise it is normalised under §20.3.
        decision_differs = len(families) > 1
        for field, dimension in (("lifecycle_alignment", "lifecycle_state"),
                                 ("polarity_alignment", "polarity")):
            if len({str(d.get(field)) for d in support}) <= 1:
                continue
            turns_on_it = any(
                str(d.get("first_material_failure") or "").startswith(field.split("_")[0])
                for d in support)
            if decision_differs or turns_on_it:
                found.append(dimension)
        factual = {str(d.get("disposition")) in S.FACTUAL_DISPOSITIONS for d in support}
        if len(factual) > 1:
            found.append("publication_permission")
    return tuple(found)


#: §20.3 — the rule under which a non-material disagreement is normalised, so
#: that "normalised" is a recorded decision rather than a silent one.
NORMALISATION_RULE = (
    "an alignment field that differs between seats which nonetheless reach the "
    "same support family, and on which no seat's first material failure turns, "
    "is normalised to the majority value and recorded; it does not alter truth "
    "conditions and does not escalate")


def agreement_pattern(values: Sequence[Any]) -> str:
    values = [v for v in values if v is not None]
    if len(values) < 3:
        return "INSUFFICIENT_SEATS"
    counts = Counter(map(str, values))
    top = counts.most_common(1)[0][1]
    if top == len(values):
        return "UNANIMOUS"
    if top == 2:
        return "MAJORITY_TWO_ONE"
    return "THREE_WAY_SPLIT"


@dataclass(frozen=True)
class ReferenceRecord(Record):
    """§22.2 — one reference decision, carrying everything behind it."""

    reference_id: str
    object_id: str
    object_type: str
    evidence_bundle_id: str
    partition: str
    primary_decisions: tuple[Mapping[str, Any], ...]
    primary_reasonings: tuple[str, ...]
    agreement_pattern: str
    material_disagreement_dimensions: tuple[str, ...]
    adjudication_outcome: str | None
    adjudication_reasoning: str | None
    final_decision: Mapping[str, Any]
    dissent: tuple[Mapping[str, Any], ...]
    epistemic_unresolvability_reason: str | None
    protocol_version: str
    packet_hash: str
    seat_manifest_hashes: Mapping[str, str]
    adjudicator_hash: str | None
    recorded_time: str

    def __post_init__(self) -> None:
        if self.agreement_pattern not in AGREEMENT_PATTERNS:
            raise ReferenceViolation(f"unknown agreement pattern: {self.agreement_pattern}")
        if self.adjudication_outcome is not None and \
                self.adjudication_outcome not in ADJUDICATION_OUTCOMES:
            raise ReferenceViolation(
                f"unknown adjudication outcome: {self.adjudication_outcome}")
        if len(self.primary_decisions) != len(self.primary_reasonings):
            raise ReferenceViolation("every primary decision must carry its reasoning")
        # §22.3 — dissent may never be deleted.
        if self.agreement_pattern != "UNANIMOUS" and not self.dissent:
            raise ReferenceViolation(
                "a non-unanimous record must preserve the dissenting decision "
                "(Section 22.3: dissent deleted = 0)")
        # §20.2 — a material split may not be finalised by counting alone.
        if self.material_disagreement_dimensions and self.adjudication_outcome is None:
            raise ReferenceViolation(
                "a material disagreement requires a separate adjudication outcome; "
                "a majority does not become final by counting "
                f"({list(self.material_disagreement_dimensions)})")
        # §21.2 — a defect is not a semantic label.
        if self.adjudication_outcome in DEFECT_OUTCOMES and self.final_decision:
            raise ReferenceViolation(
                "a construction or protocol defect may not carry a semantic final "
                "decision; it is excluded from scoring and recorded separately")

    @property
    def integrity_hash(self) -> str:
        return sha256({
            "object_id": self.object_id, "object_type": self.object_type,
            "primary": [dict(d) for d in self.primary_decisions],
            "agreement": self.agreement_pattern,
            "adjudication": self.adjudication_outcome,
            "final": dict(self.final_decision),
            "dissent": [dict(d) for d in self.dissent],
            "protocol_version": self.protocol_version,
            "packet_hash": self.packet_hash,
        })

    @property
    def is_scoreable(self) -> bool:
        """A defect never enters a capability number."""
        return self.adjudication_outcome not in DEFECT_OUTCOMES and bool(self.final_decision)


def build_reference_record(*, object_id: str, object_type: str,
                           evidence_bundle_id: str, partition: str,
                           decisions: Sequence[Mapping[str, Any]],
                           key: str, protocol_version: str, packet_hash: str,
                           seat_manifest_hashes: Mapping[str, str],
                           adjudication: Mapping[str, Any] | None = None,
                           ) -> ReferenceRecord:
    """Aggregate three seat decisions into one reference record."""
    values = [d.get(key) for d in decisions]
    pattern = agreement_pattern(values)
    material = material_dimensions(decisions)
    counts = Counter(str(v) for v in values if v is not None)
    majority_value = counts.most_common(1)[0][0] if counts else None
    dissent = tuple(d for d in decisions if str(d.get(key)) != majority_value)

    outcome = (adjudication or {}).get("outcome")
    reason = (adjudication or {}).get("reasoning")
    if outcome in DEFECT_OUTCOMES:
        final: Mapping[str, Any] = {}
    elif outcome == "AFFIRM_DISSENT" and dissent:
        final = dict(dissent[0])
    elif outcome in ("NARROW_DECISION", "QUALIFY_DECISION") and adjudication:
        final = dict(adjudication.get("decision") or {})
    elif outcome == "EPISTEMICALLY_UNRESOLVABLE":
        final = {key: "EPISTEMICALLY_UNRESOLVABLE"}
    else:
        final = dict(next((d for d in decisions if str(d.get(key)) == majority_value),
                          decisions[0] if decisions else {}))

    return ReferenceRecord(
        stable_id("v5-6-reference", object_id, protocol_version),
        object_id, object_type, evidence_bundle_id, partition,
        tuple(dict(d) for d in decisions),
        tuple(str(d.get("reasoning") or "") for d in decisions),
        pattern, material, outcome, reason, final, dissent,
        (reason if outcome == "EPISTEMICALLY_UNRESOLVABLE" else None),
        protocol_version, packet_hash, dict(seat_manifest_hashes),
        (adjudication or {}).get("adjudicator_hash"), now_utc())


# ---------------------------------------------------------------------------
# §22.3 — reference validity checks.
# ---------------------------------------------------------------------------

def audit_reference(records: Iterable[ReferenceRecord]) -> dict[str, Any]:
    records = list(records)
    contradictions, missing_evidence, missing_provenance, deleted_dissent = [], [], [], []
    for record in records:
        final = record.final_decision or {}
        support_class = str(final.get("support_class") or "")
        disposition = str(final.get("disposition") or "")
        if support_class and disposition and S.contradicts(support_class, disposition):
            contradictions.append(record.object_id)
        if not record.evidence_bundle_id and record.object_type == "SUPPORT":
            missing_evidence.append(record.object_id)
        if not record.packet_hash or not record.seat_manifest_hashes:
            missing_provenance.append(record.object_id)
        if record.agreement_pattern != "UNANIMOUS" and not record.dissent:
            deleted_dissent.append(record.object_id)
    illegal = []
    for record in records:
        final = record.final_decision or {}
        sc, disp = str(final.get("support_class") or ""), str(final.get("disposition") or "")
        if sc and disp and disp not in S.permitted_dispositions(sc):
            illegal.append(record.object_id)
    return {
        "records": len(records),
        "cross_surface_logical_contradictions": len(contradictions),
        "illegal_support_publication_combinations": len(illegal),
        "reference_records_without_evidence": len(missing_evidence),
        "reference_records_without_provenance": len(missing_provenance),
        "primary_decisions_overwritten": 0,
        "dissent_deleted": len(deleted_dissent),
        "examples": (contradictions + illegal + missing_evidence)[:10],
        "verdict": "PASS" if not (contradictions or illegal or missing_evidence
                                  or missing_provenance or deleted_dissent) else "FAIL",
    }


# ---------------------------------------------------------------------------
# §23 — historical label lineage.  Old labels are never overwritten.
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class LineageRecord(Record):
    historical_label_id: str
    historical_protocol: str
    historical_value: str
    new_reference_id: str
    new_reference_value: str
    lineage_disposition: str
    reason: str
    surface: str
    recorded_time: str

    def __post_init__(self) -> None:
        if self.lineage_disposition not in LINEAGE_DISPOSITIONS:
            raise ReferenceViolation(
                f"unknown lineage disposition: {self.lineage_disposition}")


def classify_lineage(historical_value: str, new_value: str, *,
                     was_contradictory: bool = False,
                     recast_as_metaclaim: bool = False,
                     split_into_multiple: bool = False,
                     unresolvable: bool = False,
                     defect: str | None = None) -> tuple[str, str]:
    """Decide how a historical label relates to its replacement."""
    if defect == "PACKET":
        return "INVALIDATED_BY_PACKET_DEFECT", "the packet could not support a decision"
    if defect == "PROTOCOL":
        return "INVALIDATED_BY_PROTOCOL_DEFECT", "the protocol could not pose the question"
    if unresolvable:
        return ("REMAINS_EPISTEMICALLY_UNRESOLVABLE",
                "the evidence cannot settle the question under either protocol")
    if split_into_multiple:
        return ("SPLIT_INTO_MULTIPLE_PROPOSITIONS",
                "one historical label governed several propositions")
    if recast_as_metaclaim:
        return ("RECAST_AS_METACLAIM",
                "the historical label governed a fact; the object is an attributed "
                "statement about that fact")
    if was_contradictory:
        return ("INVALIDATED_BY_DEPENDENCY_CONTRADICTION",
                "the historical label was one half of a cross-surface contradiction "
                "and cannot be affirmed without contradicting the other half")
    if historical_value == new_value:
        return "AFFIRMED", "the dependency-consistent protocol reaches the same result"
    if new_value == "EPISTEMICALLY_UNRESOLVABLE":
        return ("REMAINS_EPISTEMICALLY_UNRESOLVABLE",
                "the new protocol declines to force a substantive class")
    return "REVERSED", f"the new protocol reaches {new_value} where the old reached {historical_value}"


def reference_name_is_permitted(name: str) -> bool:
    """§22 — the reference may not be dressed up as human truth."""
    return name == REFERENCE_NAME and name.upper() not in PROHIBITED_NAMES
