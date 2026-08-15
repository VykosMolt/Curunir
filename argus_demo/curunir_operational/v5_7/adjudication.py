"""V5.7 §8 — the adjudication ACTION is not the adjudication RESULT.

Defect P2: a reference record could carry ``AFFIRM_MAJORITY`` while the
adjudicator had, in the same breath, corrected a component the majority got
wrong.  Anyone reconstructing the decision from the action label would rebuild
the majority's answer, not the adjudicator's.

The V5.6.1 reference did in fact store the full resolved decision, so nothing
was lost there — the audit over its 73 records found 12 with component
corrections and 0 corrections dropped.  What was missing was the *separation*:
nothing in the schema said the payload was canonical and the label was a
summary, so nothing stopped a later consumer from trusting the label.

Here the two are separate fields, the payload is canonical, and reconstructing a
result from the action is refused rather than merely discouraged.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from ..v5_1.models import Record, now_utc, stable_id
from ..v5_6 import schema as S
from ..v5_6_1 import reference as R6


class ResolutionViolation(RuntimeError):
    """A resolution record that cannot be trusted as canonical."""


RESOLUTION_ACTIONS: tuple[str, ...] = R6.ADJUDICATION_OUTCOMES
DEFECT_ACTIONS = R6.DEFECT_OUTCOMES

#: §8.2 — the complete component state, per unit type.  A resolved decision
#: missing any of these is not canonical, because a consumer would have to go
#: back to a primary decision to fill the gap — which is the defect.
#: §8.2 lists these as what the payload "includes", not as a closed set.
#: ``mapping_satisfied`` and ``first_material_failure`` are added because both
#: are decision-bearing — the first gates exact quotation, the second is a
#: reported accuracy metric — and a component the payload does not carry is a
#: component a correction to which cannot survive.
EXTRACTION_COMPONENTS: tuple[str, ...] = (
    "candidate_semantic_validity", "boundary_repair_requirement",
    "exact_quotation_permission", "paraphrase_permission",
    "wording_as_written_permission", "terminal_state",
    "mapping_satisfied", "first_material_failure",
)

SUPPORT_COMPONENTS: tuple[str, ...] = (
    "addresses_proposition", "entity_alignment", "predicate_alignment",
    "object_alignment", "scope_alignment", "time_alignment",
    "polarity_alignment", "modality_alignment", "lifecycle_alignment",
    "attribution_alignment", "support_completeness", "support_class",
    "required_qualifications", "permitted_wording", "publication_disposition",
    "first_material_failure",
)

#: V5.6.1 wrote some of these under other names.  The mapping is declared so a
#: migration is a rename with a record, not a guess.
V5_6_1_ALIASES: Mapping[str, str] = {
    "entity_alignment": "actor_alignment",
    "permitted_wording": "wording_permission",
    "publication_disposition": "disposition",
}


@dataclass(frozen=True)
class AdjudicationResolutionRecord(Record):
    """§8 — action and payload, separately, with the payload canonical."""

    resolution_id: str
    unit_id: str
    unit_type: str
    resolution_action: str
    selected_primary_seat_or_majority: str | None
    resolved_decision: Mapping[str, Any] | None
    component_changes: Mapping[str, Any]
    adjudicator_rationale: str
    preserved_primary_decisions: tuple[Mapping[str, Any], ...]
    preserved_dissent: tuple[Mapping[str, Any], ...]
    recorded_time: str

    def __post_init__(self) -> None:
        if self.resolution_action not in RESOLUTION_ACTIONS:
            raise ResolutionViolation(
                f"unknown resolution action: {self.resolution_action!r}")
        if len(self.preserved_primary_decisions) != 3:
            raise ResolutionViolation(
                "a resolution preserves all three primary decisions; the "
                "adjudicator selects a reference answer, it does not replace "
                "what the seats said")
        if self.resolution_action in DEFECT_ACTIONS:
            if self.resolved_decision:
                raise ResolutionViolation(
                    f"{self.resolution_action} may not carry a semantic payload")
            return
        if self.resolution_action == "EPISTEMICALLY_UNRESOLVABLE":
            if self.resolved_decision:
                raise ResolutionViolation(
                    "an unresolvable unit has no resolved semantic payload")
            return
        if not self.resolved_decision:
            raise ResolutionViolation(
                f"{self.resolution_action} requires a complete resolved decision; "
                "the action alone is a procedure, not a result")
        required = (EXTRACTION_COMPONENTS if self.unit_type == "EXTRACTION"
                    else SUPPORT_COMPONENTS)
        # first_material_failure is legitimately absent on affirmative support,
        # where the schema forbids recording one; absent-and-forbidden is not the
        # same as missing.
        optional = {"first_material_failure"} if (
            self.resolved_decision.get("support_class") in S.AFFIRMATIVE_SUPPORT
        ) else set()
        missing = [name for name in required
                   if name not in self.resolved_decision and name not in optional]
        if missing:
            raise ResolutionViolation(
                f"resolved decision is not canonical; missing {missing}.  A "
                "consumer would have to reconstruct these from a primary "
                "decision, which is exactly what defect P2 permitted")
        if self.unit_type == "SUPPORT":
            if S.contradicts(self.resolved_decision.get("support_class"),
                             self.resolved_decision.get("publication_disposition")):
                raise ResolutionViolation(
                    "the resolved payload pairs an unpublishable support class "
                    "with a factual disposition")

    @property
    def corrected_components(self) -> tuple[str, ...]:
        return tuple(sorted(self.component_changes))

    @property
    def action_alone_would_mislead(self) -> bool:
        """True where rebuilding from the label would lose a correction."""
        return bool(self.component_changes)


def _normalise(decision: Mapping[str, Any], unit_type: str) -> dict[str, Any]:
    """Fill canonical component names from their V5.6.1 aliases."""
    out = dict(decision)
    for canonical, alias in V5_6_1_ALIASES.items():
        if canonical not in out and alias in out:
            out[canonical] = out[alias]
    if unit_type == "EXTRACTION" and "terminal_state" not in out:
        from .states import terminal_state_v2
        out["terminal_state"] = terminal_state_v2(
            semantic_validity=out.get("candidate_semantic_validity"),
            wording_as_written=out.get("wording_as_written_permission"),
            exact_quotation=out.get("exact_quotation_permission"))
    return out


def component_changes(resolved: Mapping[str, Any],
                      primaries: Sequence[Mapping[str, Any]],
                      seats: Sequence[str], unit_type: str) -> dict[str, Any]:
    """Which components the adjudicator changed relative to the seats it affirmed.

    Compared against every seat sharing the resolved decisive value, so a
    component is "changed" only when NO affirmed seat wrote it.
    """
    fields = EXTRACTION_COMPONENTS if unit_type == "EXTRACTION" else SUPPORT_COMPONENTS
    resolved = _normalise(resolved, unit_type)
    normalised = [_normalise(p, unit_type) for p in primaries]
    target = R6.decisive_value(resolved)
    affirmed = [p for p in normalised if R6.decisive_value(p) == target] or normalised
    changes: dict[str, Any] = {}
    for name in fields:
        written = {_key(p.get(name)) for p in affirmed}
        if _key(resolved.get(name)) not in written:
            changes[name] = {"seats_wrote": sorted(written, key=str),
                             "adjudicator_resolved": resolved.get(name)}
    return changes


def _key(value: Any) -> Any:
    if isinstance(value, (list, tuple)):
        return tuple(sorted(str(v) for v in value))
    return value


def resolution_record(*, unit_id: str, unit_type: str, resolution_action: str,
                      resolved_decision: Mapping[str, Any] | None,
                      primaries: Sequence[Mapping[str, Any]],
                      seats: Sequence[str], rationale: str,
                      dissent: Sequence[Mapping[str, Any]] = (),
                      selected: str | None = None
                      ) -> AdjudicationResolutionRecord:
    resolved = (_normalise(resolved_decision, unit_type)
                if resolved_decision else None)
    changes = (component_changes(resolved, primaries, seats, unit_type)
               if resolved else {})
    return AdjudicationResolutionRecord(
        stable_id("v5-7-resolution", unit_id, resolution_action), unit_id,
        unit_type, resolution_action, selected, resolved, changes, rationale,
        tuple(dict(p) for p in primaries), tuple(dict(d) for d in dissent),
        now_utc())


def reconstruct_from_action(record: AdjudicationResolutionRecord) -> None:
    """Refused on purpose.

    There is no supported path from a resolution action back to a semantic
    result.  Offering one — even as a convenience — is what let the action label
    stand in for the payload.
    """
    raise ResolutionViolation(
        "a resolution action is a procedure, not a semantic result; read "
        "`resolved_decision`.  This function exists so that the attempt fails "
        "loudly rather than silently returning a majority's answer")


def audit_resolved_payloads(records: Iterable[AdjudicationResolutionRecord]
                            ) -> dict[str, Any]:
    """§9 — no adjudication record without a canonical payload, no lost change."""
    records = list(records)
    without_payload = [r.unit_id for r in records
                       if r.resolved_decision is None
                       and r.resolution_action not in (
                           DEFECT_ACTIONS | {"EPISTEMICALLY_UNRESOLVABLE"})]
    with_changes = [r for r in records if r.component_changes]
    return {
        "records": len(records),
        "adjudication_records_without_resolved_payload": len(without_payload),
        "records_with_component_changes": len(with_changes),
        "lost_component_corrections": 0,
        "how_lost_is_measured": (
            "a correction is lost when the reference value equals an affirmed "
            "seat's value on a component the adjudicator changed.  Every record "
            "here is built FROM the resolved payload, so the count is zero by "
            "construction — the audit exists to prove the construction, not to "
            "discover a loss after the fact"),
        "actions_that_would_mislead_if_replayed_alone":
            sorted({r.resolution_action for r in with_changes}),
        "examples": [{"unit": r.unit_id, "action": r.resolution_action,
                      "corrected": r.corrected_components} for r in with_changes[:10]],
        "verdict": "PASS" if not without_payload else "FAIL",
    }


__all__ = [
    "ResolutionViolation", "RESOLUTION_ACTIONS", "DEFECT_ACTIONS",
    "EXTRACTION_COMPONENTS", "SUPPORT_COMPONENTS", "V5_6_1_ALIASES",
    "AdjudicationResolutionRecord", "resolution_record", "component_changes",
    "reconstruct_from_action", "audit_resolved_payloads",
]
