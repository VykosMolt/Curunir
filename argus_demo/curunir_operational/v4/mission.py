"""Evidence handoff and serialized separation between research and kernel review."""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from .io import append_jsonl, read_json, write_json
from .models import MissionEvidenceHandoff, canonical_json, sha256, stable_id
from ..write_observation import declared_label

PACKET_PROTOCOL = "curunir-v4-kernel-review-packet-v1"


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def build_handoff(*, case_id: str, claim_or_hypothesis_ids: tuple[str, ...],
                  evidence_basis_ids: tuple[str, ...], source_dependence_summary: str,
                  correction_retraction_state: str, uncertainty: str, proposed_implication: str,
                  recipient: str, action_kind: str, access_marking: Mapping[str, Any],
                  expires_at: str) -> MissionEvidenceHandoff:
    payload = {
        "handoff_id": stable_id("mission-handoff", case_id, claim_or_hypothesis_ids, recipient, action_kind),
        "case_id": case_id, "claim_or_hypothesis_ids": claim_or_hypothesis_ids,
        "evidence_basis_ids": evidence_basis_ids, "source_dependence_summary": source_dependence_summary,
        "correction_retraction_state": correction_retraction_state, "uncertainty": uncertainty,
        "proposed_implication": proposed_implication, "recipient": recipient, "action_kind": action_kind,
        "access_marking": dict(access_marking), "review_state": "HUMAN_REVIEW_PENDING",
        "expires_at": expires_at, "integrity_hash": "0" * 64, "direct_operational_mutation": False,
    }
    payload["integrity_hash"] = sha256({key: value for key, value in payload.items() if key != "integrity_hash"})
    return MissionEvidenceHandoff(**payload)


def initialize_review_nodes(root: str | Path) -> dict[str, Any]:
    base = Path(root); manifests = {}
    specifications = {
        "STRATEGIC_EVIDENCE_NODE": {"authority": "CURUNIR_RESEARCH_SHADOW", "role": "PUBLIC_SOURCE_ANALYSIS",
                                     "browse": True, "acquire": True},
        "KERNEL_REVIEW_NODE": {"authority": "CURUNIR_KERNEL_REVIEW_SHADOW", "role": "PROPOSAL_VALIDATION",
                               "browse": False, "acquire": False},
    }
    for node_id, values in specifications.items():
        node_root = base / node_id; node_root.mkdir(parents=True, exist_ok=True)
        manifest = {"node_id": node_id, **values, "protocol": PACKET_PROTOCOL,
                    "independent_store_path": str(node_root.resolve()), "status": "ACTIVE",
                    "canonical_write_authority": False, "human_review_authority": False}
        manifest["integrity_hash"] = sha256(manifest)
        write_json(node_root / "node_manifest.json", manifest)
        for name in ("outbound_packets.jsonl", "inbound_packets.jsonl", "receipts.jsonl"):
            (node_root / name).touch(exist_ok=True)
        manifests[node_id] = manifest
    return manifests


def serialize_review_packet(*, strategic_node_root: str | Path, destination_node_id: str,
                            case_id: str, handoffs: list[Mapping[str, Any]],
                            proposals: list[Mapping[str, Any]], access_context: Mapping[str, Any],
                            output_path: str | Path) -> dict[str, Any]:
    if destination_node_id != "KERNEL_REVIEW_NODE":
        raise ValueError("kernel packet destination is fixed")
    # Access filtering precedes serialization. Restricted payload details are replaced by non-counting declarations.
    requester_releasability = set(access_context.get("releasability", ()))
    def visible(item: Mapping[str, Any]) -> bool:
        record_releasability = set(item.get("access_marking", {}).get("releasability", ()))
        return "PUBLIC" in record_releasability or bool(record_releasability & requester_releasability)
    visible_handoffs = [dict(item) for item in handoffs if visible(item)]
    visible_proposals = [dict(item) for item in proposals if visible(item)]
    body = {"protocol": PACKET_PROTOCOL, "source_node": "STRATEGIC_EVIDENCE_NODE",
            "destination_node": destination_node_id, "case_id": case_id,
            "handoffs": visible_handoffs, "proposals": visible_proposals,
            "omissions_declaration": "Policy may omit records or attributes; no hidden counts or identifiers are disclosed.",
            "access_context_hash": sha256(access_context), "created_time": now_utc()}
    packet = {**body, "packet_id": stable_id("review-packet", case_id, sha256(body)),
              "payload_hash": sha256(body)}
    packet["integrity_hash"] = sha256(packet)
    write_json(output_path, packet)
    append_jsonl(Path(strategic_node_root) / "outbound_packets.jsonl", (packet,))
    return packet


def receive_review_packet(*, kernel_node_root: str | Path, packet_path: str | Path,
                          receipt_path: str | Path) -> dict[str, Any]:
    root = Path(kernel_node_root); manifest = read_json(root / "node_manifest.json")
    if manifest.get("node_id") != "KERNEL_REVIEW_NODE" or manifest.get("browse") or manifest.get("acquire"):
        raise ValueError("kernel review node separation invalid")
    packet = read_json(packet_path); received_hash = packet.pop("integrity_hash", None)
    valid = received_hash == sha256(packet) and packet.get("payload_hash") == sha256({
        key: value for key, value in packet.items() if key not in {"packet_id", "payload_hash"}
    })
    packet["integrity_hash"] = received_hash
    if packet.get("destination_node") != "KERNEL_REVIEW_NODE": valid = False
    append_jsonl(root / "inbound_packets.jsonl", (packet,))
    receipt = {"packet_id": packet.get("packet_id"), "receiver": "KERNEL_REVIEW_NODE",
               "verification_state": "VALID" if valid else "TAMPERED", "pid": os.getpid(),
               "received_time": now_utc(), "browsing_attempts": 0, "acquisition_attempts": 0,
               "canonical_write_attempts": 0, "canonical_writes": 0,
               **declared_label("v4/mission.py::receive_review_packet"),
               "review_state": "HUMAN_REVIEW_PENDING"}
    receipt["integrity_hash"] = sha256(receipt)
    write_json(receipt_path, receipt); append_jsonl(root / "receipts.jsonl", (receipt,))
    if not valid: raise ValueError("review packet integrity invalid")
    return receipt
