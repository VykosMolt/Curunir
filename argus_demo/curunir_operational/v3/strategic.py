"""ARGUS-adapted strategic warning workbench; no competing claim graph."""
from __future__ import annotations

import html
from typing import Any, Mapping

from ..argus_adapter import evidence_ref_from_bundle, validate_evidence_bundle
from ..canonical import sha256
from .models import (AccessMarkingV3, EvidenceHandoff, Indicator, OperationalImplicationProposal,
                     StrategicHypothesis, WarningAssessment)
from .node import DistributedNode


HYPOTHESIS_STATES = ("VIABLE", "REJECTED", "UNRESOLVED")


def adapt_argus_evidence(bundle: Mapping[str, Any]) -> dict[str, Any]:
    """Read-only adapter: preserve ARGUS identifiers and epistemic fields."""
    validate_evidence_bundle(bundle)
    return evidence_ref_from_bundle(bundle).to_record()


class StrategicWarningWorkbench:
    def __init__(self, node: DistributedNode):
        self.node = node

    def indicator(self, indicator: Indicator, *, actor_id: str, recorded_time: str,
                  nonce: str, mission_scope: str) -> str:
        if not indicator.argus_evidence_refs:
            raise ValueError("strategic indicator requires ARGUS evidence")
        event = self.node.append_action(
            actor_id=actor_id, action_type="ATTACH_PUBLIC_EVIDENCE", event_type="INDICATOR_RECORDED",
            payload={"record_type": "indicator", "subject_id": indicator.indicator_id, **indicator.to_record()},
            marking=AccessMarkingV3(**normalized_marking(indicator.access_marking)),
            recorded_time=recorded_time, nonce=nonce, object_ref=indicator.indicator_id,
            mission_scope=mission_scope)
        return event.event_id

    def source_dependence_group(self, group_id: str, members: tuple[str, ...], evidence_basis: str, *,
                                actor_id: str, recorded_time: str, nonce: str,
                                marking: AccessMarkingV3, mission_scope: str) -> str:
        event = self.node.append_action(
            actor_id=actor_id, action_type="ATTACH_PUBLIC_EVIDENCE",
            event_type="SOURCE_DEPENDENCE_GROUP_RECORDED",
            payload={"record_type": "source_dependence_group", "subject_id": group_id,
                     "group_id": group_id, "members": list(members), "evidence_basis": evidence_basis,
                     "independent_basis_count": 1,
                     "interpretation": "member count is not independent corroboration"},
            marking=marking, recorded_time=recorded_time, nonce=nonce,
            object_ref=group_id, mission_scope=mission_scope)
        return event.event_id

    def hypothesis(self, hypothesis: StrategicHypothesis, *, actor_id: str, recorded_time: str,
                   nonce: str, marking: AccessMarkingV3, mission_scope: str) -> str:
        if hypothesis.analyst_state not in HYPOTHESIS_STATES:
            raise ValueError(f"invalid strategic hypothesis state: {hypothesis.analyst_state}")
        event = self.node.append_action(
            actor_id=actor_id, action_type="ANNOTATION", event_type="STRATEGIC_HYPOTHESIS_RECORDED",
            payload={"record_type": "strategic_hypothesis", "subject_id": hypothesis.hypothesis_id,
                     **hypothesis.to_record()},
            marking=marking, recorded_time=recorded_time, nonce=nonce,
            object_ref=hypothesis.hypothesis_id, mission_scope=mission_scope)
        return event.event_id

    def assessment(self, assessment: WarningAssessment, *, actor_id: str, recorded_time: str,
                   nonce: str, marking: AccessMarkingV3, mission_scope: str) -> str:
        event = self.node.append_action(
            actor_id=actor_id, action_type="ANNOTATION", event_type="WARNING_ASSESSMENT_RECORDED",
            payload={"record_type": "warning_assessment", "subject_id": assessment.assessment_id,
                     **assessment.to_record()},
            marking=marking, recorded_time=recorded_time, nonce=nonce,
            object_ref=assessment.assessment_id, mission_scope=mission_scope)
        return event.event_id

    def implication(self, implication: OperationalImplicationProposal, *, actor_id: str,
                    recorded_time: str, nonce: str, marking: AccessMarkingV3,
                    mission_scope: str) -> str:
        if implication.direct_operational_mutation:
            raise ValueError("strategic implication cannot directly mutate operational state")
        event = self.node.append_action(
            actor_id=actor_id, action_type="PROPOSE_IMPLICATION",
            event_type="OPERATIONAL_IMPLICATION_PROPOSED",
            payload={"record_type": "operational_implication", "subject_id": implication.implication_id,
                     **implication.to_record(), "operational_state_mutated": False},
            marking=marking, recorded_time=recorded_time, nonce=nonce,
            object_ref=implication.implication_id, mission_scope=mission_scope)
        return event.event_id

    def evidence_handoff(self, handoff: EvidenceHandoff, *, actor_id: str, recorded_time: str,
                         nonce: str, marking: AccessMarkingV3, mission_scope: str,
                         parent_event_ids: tuple[str, ...]) -> str:
        event = self.node.append_action(
            actor_id=actor_id, action_type="HANDOFF", event_type="EVIDENCE_HANDOFF_RECORDED",
            payload={"record_type": "evidence_handoff", "subject_id": handoff.handoff_id,
                     **handoff.to_record(), "provenance_preserved": True},
            marking=marking, recorded_time=recorded_time, nonce=nonce,
            parent_event_ids=parent_event_ids, object_ref=handoff.handoff_id,
            mission_scope=mission_scope)
        return event.event_id


def strategic_view(projection: Mapping[str, Any]) -> dict[str, Any]:
    records = [event["payload"] for event in projection.get("union_records", ())]
    by_type: dict[str, list[dict[str, Any]]] = {}
    for record in records:
        by_type.setdefault(record.get("record_type", "unknown"), []).append(record)
    hypotheses = sorted(by_type.get("strategic_hypothesis", ()), key=lambda item: item["hypothesis_id"])
    indicators = sorted(by_type.get("indicator", ()), key=lambda item: item["indicator_id"])
    dependence = sorted(by_type.get("source_dependence_group", ()), key=lambda item: item["group_id"])
    implications = sorted(by_type.get("operational_implication", ()), key=lambda item: item["implication_id"])
    handoffs = sorted(by_type.get("evidence_handoff", ()), key=lambda item: item["handoff_id"])
    assessments = sorted(by_type.get("warning_assessment", ()), key=lambda item: item["assessment_id"])
    return {
        "workbench_id": "STRATEGIC_WARNING_AND_EVIDENCE_WORKBENCH_V3",
        "purpose": "Preserve public evidence, dependence, correction and competing-hypothesis uncertainty",
        "indicators": indicators, "hypotheses": hypotheses, "source_dependence_groups": dependence,
        "implications": implications, "evidence_handoffs": handoffs, "assessments": assessments,
        "corrections": [item for item in indicators if item.get("correction_state") == "CORRECTED"],
        "retractions": [item for item in indicators if item.get("correction_state") == "RETRACTED"],
        "refusals": sorted({refusal for assessment in assessments
                             for refusal in assessment.get("refusal_statements", ())}),
        "integrity_hash": sha256({"indicators": indicators, "hypotheses": hypotheses,
                                   "dependence": dependence, "implications": implications,
                                   "handoffs": handoffs}),
    }


def render_strategic_html(view: Mapping[str, Any], *, title: str = "Strategic warning and evidence") -> str:
    def rows(items: list[Mapping[str, Any]], columns: tuple[tuple[str, str], ...]) -> str:
        return "".join("<tr>" + "".join(f"<td>{html.escape(str(item.get(key, 'UNKNOWN')))}</td>" for _, key in columns) +
                       "</tr>" for item in items)

    def table(caption: str, items: list[Mapping[str, Any]], columns: tuple[tuple[str, str], ...]) -> str:
        heads = "".join(f'<th scope="col">{html.escape(label)}</th>' for label, _ in columns)
        body = rows(items, columns) or f'<tr><td colspan="{len(columns)}">None visible in this access context</td></tr>'
        return f'<section><h2>{html.escape(caption)}</h2><table><caption>{html.escape(caption)} — visible records only</caption><thead><tr>{heads}</tr></thead><tbody>{body}</tbody></table></section>'

    hypothesis_table = table("Competing hypotheses", list(view["hypotheses"]), (
        ("Hypothesis", "hypothesis_id"), ("Statement", "statement"), ("State", "analyst_state"),
        ("Support", "supporting_indicators"), ("Contradiction", "contradicting_indicators"),
        ("Review", "review_state")))
    indicator_table = table("Indicators and evidence", list(view["indicators"]), (
        ("Indicator", "indicator_id"), ("Development", "statement"),
        ("Dependence group", "source_dependence_group"), ("Correction state", "correction_state"),
        ("ARGUS evidence", "argus_evidence_refs")))
    dependence_table = table("Source dependence", list(view["source_dependence_groups"]), (
        ("Group", "group_id"), ("Members", "members"), ("Evidence basis", "evidence_basis"),
        ("Independent bases", "independent_basis_count"), ("Interpretation", "interpretation")))
    implication_table = table("Operational implications — proposals, not facts", list(view["implications"]), (
        ("Implication", "implication_id"), ("Hypothesis", "source_hypothesis_id"),
        ("Proposal", "proposed_consequence"), ("Status", "status"),
        ("Mutated operational state", "operational_state_mutated")))
    handoff_table = table("Evidence handoffs", list(view["evidence_handoffs"]), (
        ("Handoff", "handoff_id"), ("Strategic subject", "strategic_subject_id"),
        ("Receiving workbench", "receiving_workbench"), ("Requirement", "information_requirement_id"),
        ("Review", "review_state"), ("Result", "unresolved_state")))
    refusals = "".join(f"<li>{html.escape(item)}</li>" for item in view["refusals"]) or "<li>None recorded</li>"
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>{html.escape(title)}</title>
<style>
body{{font-family:system-ui,sans-serif;margin:1rem auto;max-width:1180px;padding:0 .8rem;background:#f5f6f4;color:#17201c}}
.banner{{border:2px solid #2c4a3d;padding:.6rem;background:#e7eee9;font-weight:700}}
.cards{{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:.7rem;margin:1rem 0}}
.card{{border:1px solid #8a9b91;background:white;padding:.7rem}}table{{border-collapse:collapse;width:100%;background:white}}
th,td{{border:1px solid #9ba7a0;padding:.35rem;text-align:left;vertical-align:top;font-size:.84rem}}
caption{{text-align:left;font-weight:600;padding:.25rem}}th{{background:#e6ece8}}
:focus{{outline:3px solid #c56700;outline-offset:2px}}@media(max-width:800px){{.cards{{grid-template-columns:1fr}}section{{overflow-x:auto}}}}
</style></head><body><main>
<div class="banner">RESEARCH SHADOW · SYNTHETIC STRATEGIC SCENARIO · IMPLICATION IS NOT FACT</div>
<h1>{html.escape(title)}</h1><p>{html.escape(view["purpose"])}</p>
<div class="cards"><div class="card"><b>Indicators</b><br>{len(view["indicators"])}</div>
<div class="card"><b>Competing hypotheses</b><br>{len(view["hypotheses"])}</div>
<div class="card"><b>Open evidence handoffs</b><br>{sum(1 for item in view["evidence_handoffs"] if item.get("unresolved_state") != "RESOLVED")}</div></div>
{hypothesis_table}{indicator_table}{dependence_table}{implication_table}{handoff_table}
<section><h2>What the system refuses to conclude</h2><ul>{refusals}</ul></section>
<p><small>Integrity: {html.escape(view["integrity_hash"])}</small></p>
</main></body></html>"""


def normalized_marking(record: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "owning_authority": record["owning_authority"],
        "compartments": tuple(record.get("compartments", ())),
        "releasability": tuple(record.get("releasability", ())),
        "mission_scopes": tuple(record.get("mission_scopes", ())),
        "min_role": record.get("min_role", "OBSERVER"),
        "originator_controls": tuple(record.get("originator_controls", ())),
        "sanitized": bool(record.get("sanitized", False)),
    }
