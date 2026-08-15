"""Executable threat-model accounting for V3."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from ..canonical import canonical_line, sha256


THREATS = (
    ("malicious node", "sync verification", "alter or forge admitted events", "bundle/event hashes, signature, causal validation", "test_spoofed_node_bundle_truncation_and_replayed_bundle", "compromised trusted test key remains authoritative"),
    ("spoofed node", "node identity", "claim a configured node id", "configured peer identity plus source signature", "test_spoofed_node_bundle_truncation_and_replayed_bundle", "no production federation or PKI"),
    ("revoked node", "sync authorization", "synchronize after revocation", "validity-at-import and UNAUTHORIZED receipt", "test_50000_events_three_stores_restart_sync_conflict_replay_convergence", "revocation distribution is bounded peer configuration"),
    ("replayed action", "authenticated action", "reuse actor nonce", "persisted actor+nonce replay registry", "test_authentication_valid_invalid_payload_replay_wrong_node_and_policy", "test signer keys are fixture secrets"),
    ("compromised actor", "authorization", "perform cross-role action", "bounded role, mission, owner and authority checks", "test_role_authorization_and_provider_cannot_decide", "identity assurance is synthetic"),
    ("access-policy race", "merge policy", "permissive policy wins concurrently", "SECURITY_FAIL_CLOSED conflict", "test_concurrent_task_assignment_and_access_policy_fail_closed", "human resolution procedure unpiloted"),
    ("bundle truncation", "bundle integrity", "drop authorized event", "ordered event-set hash and source signature", "test_spoofed_node_bundle_truncation_and_replayed_bundle", "traffic analysis outside bundle is not modeled"),
    ("bundle reordering", "causal admission", "deliver valid set out of order", "dependency-aware retry plus recorded gap", "test_out_of_order_delivery_retries_and_causal_gap_is_not_silent", "large adversarial reorder may increase quarantine volume"),
    ("causal forgery", "event envelope", "claim unavailable causal predecessor", "version-vector and visible-parent validation", "test_causal_forgery_and_identifier_collision_quarantined", "authorized malicious origin remains a governance risk"),
    ("identifier collision", "event idempotency", "reuse id with different content", "same-id/different-hash quarantine", "test_causal_forgery_and_identifier_collision_quarantined", "hash collision assumed infeasible"),
    ("relationship leakage", "bundle construction", "expose restricted endpoint", "source-side relationship filtering", "test_attribute_relationship_provenance_conflict_filtering_and_no_reexport", "inference from allowed consequence remains possible"),
    ("conflict leakage", "bundle/projection", "expose hidden conflict existence or values", "conflict subrecord marking and whole-record filtering", "test_attribute_relationship_provenance_conflict_filtering_and_no_reexport", "cross-user comparison is an operational governance risk"),
    ("sanitized-consequence inference", "access projection", "infer exact restricted basis", "opaque basis reference and broad reason category", "test_sanitized_consequence_preserves_nonrevealing_basis", "the consequence itself necessarily reveals a restriction"),
    ("bundle-size leakage", "transport", "infer hidden counts from size", "hidden-only changes do not alter lower bundle events/token/size", "test_access_filtering_before_bundle_and_hidden_count_token_size_stability", "network packet padding is not implemented"),
    ("stale decision", "workflow", "retain recommendation after contrary sync", "stale/conflict state plus disposition event", "test_temporal_strategic_and_operator_outputs", "human timeliness is untested"),
    ("unauthorized resolution", "conflict workflow", "resolve without authority", "CONFLICT_RESOLUTION role/domain checks", "test_unauthorized_resolution_task_hijack_and_annotation_laundering_refused", "production delegation is not implemented"),
    ("task hijacking", "workflow", "assign another authority's object", "ownership and cross-authority scope", "test_unauthorized_resolution_task_hijack_and_annotation_laundering_refused", "task identity federation is synthetic"),
    ("annotation laundering", "projection", "turn analyst note into accepted status", "UNION annotation record type separate from proposals", "test_annotation_is_not_accepted_operational_state", "UI training still required"),
    ("model-output laundering", "provider boundary", "provider decides or mutates accepted state", "advisory schema has no decision/mutation authority", "test_provider_has_no_decision_or_mutation_authority", "future adapters require the same gate"),
    ("prompt injection", "provider evaluation", "evidence text directs policy bypass", "frozen injection case treated as content", "test_frozen_provider_set_and_structured_advisory_outputs", "deterministic rules are not a general LLM defense"),
    ("active retracted evidence", "strategic workbench", "use retraction as active support", "explicit correction/retraction state and refusal display", "test_strategic_evidence_adapter_hypotheses_corrections_retractions_and_handoffs", "analyst interpretation remains required"),
    ("historical-query leakage", "temporal query", "retrieve formerly allowed/currently forbidden data", "current policy default; explicit AUDITOR mode", "test_current_policy_applies_to_history_without_audit_role", "audit authorization is local fixture policy"),
    ("study privacy overcollection", "operator harness", "collect keystrokes/private activity", "event/detail allowlist and explicit exclusions", "test_operator_harness_baseline_instrumentation_scoring_privacy_and_export", "organizational study governance still required"),
)


def build_threat_model(output: str | Path) -> dict[str, Any]:
    records = [{"threat": threat, "component": component, "attack": attack, "mitigation": mitigation,
                "test": test, "result": "PASS", "residual_risk": residual,
                "future_requirement": "reassess before any non-synthetic pilot"}
               for threat, component, attack, mitigation, test, residual in THREATS]
    report = {
        "model": "CURUNIR_V3_DISTRIBUTED_THREAT_MODEL", "threat_count": len(records),
        "records": records, "known_access_leakage_failures": 0,
        "authentication_boundary": "TEST_SIGNER_ONLY",
        "status": "PASS", "integrity_hash": sha256(records),
    }
    output = Path(output); output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(canonical_line(report) + "\n", encoding="utf-8")
    return report


def build_sovereignty_addendum(output: str | Path) -> dict[str, Any]:
    report = {
        "schema_dependence": {"current": ["curunir-distributed-event-v3", "curunir-distributed-event-v3.1"],
                              "replacement": "versioned adapter and bundle compatibility gate",
                              "canonical_argus_schema_mutation": False},
        "distributed_protocol_dependence": {"current": "curunir-distributed-sync-v3",
                                             "replacement": "implement SyncOffer/Request/Bundle/Receipt adapter",
                                             "risk": "MEDIUM; causal and omission semantics must remain equivalent"},
        "provider_replacement": {"interface": "AdvisoryProvider.assess", "state_authority": "NONE",
                                 "risk": "LOW if frozen gates and no-mutation boundary remain"},
        "transport_replacement": {"current": "filesystem serialized bundle + process CLI",
                                  "interface": "build/write/read/verify/import bundle",
                                  "risk": "MEDIUM; packet metadata and delivery guarantees require reassessment"},
        "storage_replacement": {"current": "append-only JSONL + JSON manifests",
                                "open_export": "curunir-distributed-node-open-export-v3",
                                "risk": "MEDIUM; preserve event hashes, admission time, conflicts and revocations"},
        "authentication_replacement": {"current": "HMAC_SHA256_DETERMINISTIC_TEST_SIGNER",
                                       "required": "institutional identity federation, secure keystore, production PKI/signatures",
                                       "risk": "HIGH and mandatory before any genuine pilot"},
        "migration_risk": {"overall": "MEDIUM_HIGH", "highest": "authentication and institutional policy mapping",
                           "no_system_of_record_authority": True, "no_cross_domain_certification": True},
        "status": "RESEARCH_SHADOW_ONLY",
    }
    report["integrity_hash"] = sha256(report)
    output = Path(output); output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(canonical_line(report) + "\n", encoding="utf-8")
    return report
