"""Future institutional pilot package; contains no human results."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from ..v4.io import write_json
from ..v4.models import sha256


ROLES = (
    "SOURCE_REVIEWER", "DOMAIN_REVIEWER", "ANALYTIC_REVIEWER", "GOVERNANCE_REVIEWER",
    "SYSTEM_OPERATOR", "PILOT_COORDINATOR",
)


def build_pilot_package(output_root: str | Path) -> dict[str, Any]:
    out = Path(output_root); instructions = out / "reviewer_instructions"; instructions.mkdir(parents=True, exist_ok=True)
    definition = {
        "pilot_id": "curunir-v5-future-non-sensitive-institutional-pilot",
        "partner_status": "HUMAN_PARTNER_PENDING", "human_sessions": 0,
        "objectives": ["source acquisition fidelity", "extraction precision", "source-origin accuracy",
            "false-corroboration detection", "claim support", "contradiction handling", "report usefulness",
            "report faithfulness", "evidence navigation", "longitudinal update usefulness",
            "kernel-proposal governance"],
        "conditions": ["STATIC_BASELINE", "CURUNIR_RESEARCH_SHADOW"],
        "roles": list(ROLES), "answer_visibility": "HIDDEN_DURING_INDEPENDENT_LABELING",
        "packet_order": "RANDOMIZED_PER_REVIEWER", "expert_escalation": True,
        "canonical_write_authority": False, "system_of_record_authority": False,
    }
    packet_schema = {"required": ["packet_id", "surface", "evidence", "decision_options", "access_marking"],
        "forbidden": ["curunir_answer", "final_verdict", "other_reviewer_outputs", "personal_name"],
        "decision_states": ["CORRECT", "INCORRECT", "PARTIALLY_CORRECT", "INSUFFICIENT_INFORMATION",
            "AMBIGUOUS", "OUT_OF_SCOPE", "CANNOT_ADJUDICATE", "PACKET_DEFECT"]}
    metric_schema = {"metrics": ["correctness", "completion", "time", "evidence-trace correctness",
        "source-dependence recognition", "conflict recognition", "stale-data recognition",
        "observed-versus-inferred distinction", "inappropriate certainty", "confidence calibration",
        "report quality", "policy compliance", "workload", "trust", "usability issues"],
        "no_human_results": True}
    role_definitions = {role: {"role": role, "may_label": True,
        "may_view_other_decisions_before_submission": False, "may_canonical_write": False} for role in ROLES}
    write_json(out / "pilot_definition.json", definition)
    write_json(out / "packet_schema.json", packet_schema); write_json(out / "metric_schema.json", metric_schema)
    write_json(out / "reviewer_roles.json", role_definitions)
    protocol = """# Curunír non-sensitive institutional pilot protocol

Status: future pilot package; no partner or human result exists.

Reviewers receive randomized, answer-hidden packets and submit an independent decision, confidence,
elapsed time and optional comment. They may select cannot-adjudicate. Conflicts remain visible for a
separate adjudication phase. Model output and Curunír labels remain hidden until independent submission.
No participant or model can write canonical state.
"""
    (out / "pilot_protocol.md").write_text(protocol, encoding="utf-8")
    for role in ROLES:
        (instructions / f"{role.casefold()}.md").write_text(
            f"# {role}\n\nJudge only assigned non-sensitive evidence. Do not infer hidden system answers. "
            "Use cannot-adjudicate when evidence or expertise is insufficient.\n", encoding="utf-8")
    (out / "consent_template.md").write_text("""# Future pilot consent template

Participation is voluntary. The pilot records only an anonymized identifier, assigned role, packet,
decision, confidence, elapsed time and optional comment. It collects no desktop capture, hidden
keystrokes, private files, audio, video or unrelated personal information. No participant data exists yet.
""", encoding="utf-8")
    (out / "retention_policy.md").write_text("""# Retention policy

Retain pseudonymous decisions only for the approved pilot period and analysis window. Separate the
participant key from review data. Permit withdrawal where institutionally required. Export or delete at
pilot close under partner policy; immutable public-source evidence is governed separately.
""", encoding="utf-8")
    (out / "incident_procedure.md").write_text("""# Incident procedure

Stop affected sessions; preserve a minimal incident record; isolate exposed packets; notify the future
pilot coordinator and partner contact; assess access, privacy and answer-key leakage; invalidate affected
reviews; do not silently substitute packets; resume only after documented authorization.
""", encoding="utf-8")
    (out / "exit_and_export.md").write_text("""# Exit and export

Export open JSON/JSONL packets, decisions, hashes, adjudication history and aggregate metrics. Revoke
pilot identities, stop collection, return or delete partner data according to policy, and preserve no
canonical writes. A partner may terminate without admitting any proposal.
""", encoding="utf-8")
    privacy = {"collected": ["anonymized_reviewer_id", "assigned_role", "packet_id", "decision",
        "confidence", "elapsed_time", "optional_comment"], "prohibited": ["raw_desktop_activity",
        "hidden_keystrokes", "private_files", "audio", "video", "unrelated_personal_data"],
        "human_records_present": 0}
    write_json(out / "privacy_boundary.json", privacy)
    result = {"roles": len(ROLES), "blind_packet_schema": True, "metric_schema": True,
        "privacy_policy": True, "retention_policy": True, "incident_procedure": True,
        "exit_procedure": True, "human_participation": 0, "fake_human_results": 0,
        "status": "PILOT_READY_HUMAN_PARTNER_PENDING"}
    result["integrity_hash"] = sha256(result); write_json(out / "pilot_package_manifest.json", result)
    return result

