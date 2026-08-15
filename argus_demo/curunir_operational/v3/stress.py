"""Bounded 50,000-event, three-store distributed stress execution."""
from __future__ import annotations

import json
import resource
import time
from pathlib import Path
from typing import Any

from ..canonical import canonical_line, sha256
from . import SCHEMA_VERSION
from .models import (AccessMarkingV3, ActorIdentity, NodeIdentity, RevocationRecord, SyncRequest)
from .node import DistributedNode
from .scenario import FULL_ACCESS, MISSION, NODE_IDS, PARTNER_ACCESS, TIMES, _actors, _node_identities
from .sync import (SyncProtocolError, acknowledge_receipt, build_bundle, import_bundle, make_request,
                   verify_bundle, write_bundle)


STRESS_SCHEMA_V2 = "curunir-distributed-event-v3.1"


def _write(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(canonical_line(value) + "\n", encoding="utf-8")


def _actors_contracts() -> tuple[ActorIdentity, ...]:
    return tuple(ActorIdentity(**{**record, "roles": tuple(record["roles"]),
                                   "compartments": tuple(record["compartments"]),
                                   "mission_scopes": tuple(record["mission_scopes"]),
                                   "authority_scopes": tuple(record["authority_scopes"])})
                 for record in _actors())


def _node_contracts() -> dict[str, NodeIdentity]:
    return {key: NodeIdentity(**{**record, "sharing_scopes": tuple(record["sharing_scopes"]),
                                 "supported_protocol_versions": tuple(record["supported_protocol_versions"])})
            for key, record in _node_identities().items()}


def _mark(authority: str, index: int) -> AccessMarkingV3:
    if index % 100 == 0:
        compartment = "STRESS_RESTRICTED"
        return AccessMarkingV3(authority, compartments=(compartment,), releasability=(f"{authority}_ONLY",),
                               mission_scopes=(MISSION,), min_role="AUDITOR",
                               originator_controls=(f"AUTHORITY:{authority}",))
    if index % 7 == 0:
        return AccessMarkingV3(authority, releasability=("MISSION_PARTNERS",),
                               mission_scopes=(MISSION, "STRESS_SCOPE_B"))
    return AccessMarkingV3(authority, releasability=("MISSION_PARTNERS",), mission_scopes=(MISSION,))


def _sync(source: DistributedNode, destination: DistributedNode, root: Path, label: str, *,
          max_events: int = 100_000, tamper_probe: bool = False) -> tuple[dict[str, Any], Any, Any]:
    request = make_request(destination, source.node_id, PARTNER_ACCESS, requested_scope=(MISSION,),
                           supported_schemas=(SCHEMA_VERSION, STRESS_SCHEMA_V2), max_events=max_events)
    start = time.perf_counter(); bundle = build_bundle(source, request, creation_time=TIMES[9]); generation = time.perf_counter() - start
    bundle_path = root / "bundles" / f"{label}.json"; write_bundle(bundle, bundle_path)
    start = time.perf_counter(); verify_bundle(destination, bundle, at_time=TIMES[9]); verification = time.perf_counter() - start
    tamper_state = "NOT_ATTEMPTED"
    if tamper_probe and bundle.events:
        from dataclasses import replace
        events = list(bundle.events); events[0] = {**events[0], "payload": {"tampered": True}}
        tampered = replace(bundle, events=tuple(events))
        tamper_state = import_bundle(destination, tampered, admitted_time=TIMES[9]).verification_state
    start = time.perf_counter(); receipt = import_bundle(destination, bundle, admitted_time=TIMES[9]); importing = time.perf_counter() - start
    acknowledge_receipt(source, bundle, receipt)
    return ({"label": label, "bundle_id": bundle.bundle_id, "bundle_bytes": bundle_path.stat().st_size,
             "event_count_privileged": len(bundle.events), "generation_seconds": generation,
             "verification_seconds": verification, "import_seconds": importing,
             "receipt": receipt.verification_state, "tamper_probe": tamper_state}, bundle, receipt)


def run_stress(root: str | Path, *, total_events: int = 50_000) -> dict[str, Any]:
    if total_events < 50_000:
        raise ValueError("V3 stress target may not be silently reduced below 50,000")
    root = Path(root)
    if root.exists() and any(root.iterdir()):
        raise ValueError(f"stress root must be empty: {root}")
    root.mkdir(parents=True, exist_ok=True)
    identities, actors = _node_contracts(), _actors_contracts()
    probe_identity = NodeIdentity("SCHEMA_PROBE_NODE", "SCHEMA_PROBE_AUTHORITY", "COMPATIBILITY_PROBE",
                                  "TRUSTED_TEST", (MISSION,))
    keys = {subject: f"stress-test-key::{subject}"
            for subject in [*NODE_IDS, probe_identity.node_id, *(actor.actor_id for actor in actors)]}
    nodes: dict[str, DistributedNode] = {}
    for node_id in NODE_IDS:
        nodes[node_id] = DistributedNode.create(
            root / "stores" / node_id.lower(), identities[node_id], actors, keys,
            {**{key: value for key, value in identities.items() if key != node_id},
             probe_identity.node_id: probe_identity},
            schema_versions=(SCHEMA_VERSION, STRESS_SCHEMA_V2))
    actors_by_node = {"LOGISTICS_NODE": "logistics-analyst-v3",
                      "CIVIL_PROTECTION_NODE": "civil-engineer-v3",
                      "STRATEGIC_EVIDENCE_NODE": "strategic-analyst-v3"}
    authorities = {key: identity.owning_authority for key, identity in identities.items()}
    ingest_seconds: dict[str, float] = {}
    sync_metrics: list[dict[str, Any]] = []
    # Small synchronized baseline proves reordered delivery without making a
    # 16k-event reversed bundle quadratic.
    for node_id, node in nodes.items():
        specs = []
        for index in range(2):
            specs.append({"actor_id": actors_by_node[node_id], "action_type": "ANNOTATION",
                          "event_type": "STRESS_BASELINE", "payload": {"record_type": "observation",
                          "subject_id": f"baseline-{node_id}-{index}", "value": index},
                          "marking": _mark(authorities[node_id], index + 1), "recorded_time": TIMES[1],
                          "nonce": f"baseline-{node_id}-{index}", "mission_scope": MISSION})
        node.append_batch(specs)
    for source_id in NODE_IDS:
        for destination_id in NODE_IDS:
            if source_id != destination_id:
                metric, _, _ = _sync(nodes[source_id], nodes[destination_id], root,
                                     f"baseline_{source_id.lower()}_to_{destination_id.lower()}")
                sync_metrics.append(metric)
    # Restart after baseline, then operate offline/partitioned.
    nodes = {node_id: DistributedNode(node.root) for node_id, node in nodes.items()}
    targets = {"LOGISTICS_NODE": total_events // 3 + (1 if total_events % 3 else 0),
               "CIVIL_PROTECTION_NODE": total_events // 3 + (1 if total_events % 3 > 1 else 0),
               "STRATEGIC_EVIDENCE_NODE": total_events // 3}
    # Correct integer distribution for all remainders.
    while sum(targets.values()) < total_events:
        targets[NODE_IDS[sum(targets.values()) % 3]] += 1
    for node_id, node in nodes.items():
        remaining = targets[node_id] - 2
        specs = []
        for index in range(remaining):
            global_index = index + 2
            if index < 400:
                payload = {"record_type": "planning_assumption", "subject_id": f"ambiguous-{index:04d}",
                           "value": f"{node_id}-VALUE", "evidence_snapshot": [f"basis-{node_id}-{index}"]}
            else:
                restricted = global_index % 100 == 0
                payload = {"record_type": "observation",
                           "subject_id": (f"restricted-{node_id}-{global_index}" if restricted
                                          else f"observation-{node_id}-{global_index}"),
                           "value": global_index, "access_scope": "B" if global_index % 7 == 0 else "A"}
            specs.append({"actor_id": actors_by_node[node_id], "action_type": "ANNOTATION",
                          "event_type": "STRESS_EVENT", "payload": payload,
                          "marking": _mark(authorities[node_id], global_index), "recorded_time": TIMES[4],
                          "nonce": f"stress-{node_id}-{global_index}", "mission_scope": MISSION,
                          "schema_version": STRESS_SCHEMA_V2 if global_index % 25 == 0 else SCHEMA_VERSION})
        start = time.perf_counter(); node.append_batch(specs); ingest_seconds[node_id] = time.perf_counter() - start
    assert sum(sum(1 for event in node.events() if event.originating_node_id == node.node_id)
               for node in nodes.values()) == total_events
    # Validly signed incompatible-schema probe. It is auxiliary and is not one
    # of the three primary stress stores.
    probe = DistributedNode.create(
        root / "schema_probe_store", probe_identity, actors, keys,
        {key: value for key, value in identities.items()}, schema_versions=(SCHEMA_VERSION,))
    probe_request = SyncRequest(probe_identity.node_id, {"LOGISTICS_NODE": "GENESIS"}, (MISSION,),
                                PARTNER_ACCESS, (SCHEMA_VERSION, STRESS_SCHEMA_V2),
                                {"max_events": 100, "max_bytes": 10_000_000})
    probe_bundle = build_bundle(nodes["LOGISTICS_NODE"], probe_request, creation_time=TIMES[8])
    schema_receipt = import_bundle(probe, probe_bundle, admitted_time=TIMES[8])
    # Rejoin: one delayed/partial path, tamper first, all others ordinary.
    for source_id in NODE_IDS:
        for destination_id in NODE_IDS:
            if source_id == destination_id:
                continue
            if source_id == "STRATEGIC_EVIDENCE_NODE" and destination_id == "LOGISTICS_NODE":
                part = 0
                while True:
                    part += 1
                    metric, bundle, receipt = _sync(nodes[source_id], nodes[destination_id], root,
                        f"rejoin_partial_{part}_{source_id.lower()}_to_{destination_id.lower()}",
                        max_events=5000, tamper_probe=part == 1)
                    sync_metrics.append(metric)
                    if len(bundle.events) < 5000:
                        break
            else:
                metric, bundle, receipt = _sync(nodes[source_id], nodes[destination_id], root,
                                                f"rejoin_{source_id.lower()}_to_{destination_id.lower()}")
                sync_metrics.append(metric)
                if source_id == "LOGISTICS_NODE" and destination_id == "CIVIL_PROTECTION_NODE":
                    duplicate = import_bundle(nodes[destination_id], bundle, admitted_time=TIMES[9])
                    sync_metrics.append({"label": "duplicate_delivery", "receipt": duplicate.verification_state,
                                         "bundle_bytes": 0, "event_count_privileged": 0,
                                         "generation_seconds": 0, "verification_seconds": 0,
                                         "import_seconds": 0, "tamper_probe": "NOT_ATTEMPTED"})
    # Source-side stale base rejection.
    stale_request = SyncRequest("CIVIL_PROTECTION_NODE", {"LOGISTICS_NODE": "stale-opaque-token"}, (MISSION,),
                                PARTNER_ACCESS, (SCHEMA_VERSION, STRESS_SCHEMA_V2),
                                {"max_events": 1000, "max_bytes": 1000000})
    stale_base = "NOT_CAUGHT"
    try:
        build_bundle(nodes["LOGISTICS_NODE"], stale_request, creation_time=TIMES[10])
    except SyncProtocolError as exc:
        stale_base = exc.code
    missing_request = SyncRequest("CIVIL_PROTECTION_NODE", {"LOGISTICS_NODE": "GENESIS"}, (MISSION,),
                                  PARTNER_ACCESS, (SCHEMA_VERSION, STRESS_SCHEMA_V2),
                                  {"max_events": 1000, "max_bytes": 1000000})
    missing_base = "NOT_CAUGHT"
    try:
        build_bundle(nodes["LOGISTICS_NODE"], missing_request, creation_time=TIMES[10])
    except SyncProtocolError as exc:
        missing_base = exc.code
    # Bitemporal query and conflict-aware projection.
    start = time.perf_counter(); temporal_view = nodes["LOGISTICS_NODE"].projection(
        PARTNER_ACCESS, valid_at=TIMES[4], known_at=TIMES[9]); bitemporal_seconds = time.perf_counter() - start
    start = time.perf_counter(); full_projection = nodes["LOGISTICS_NODE"].projection(PARTNER_ACCESS)
    projection_seconds = time.perf_counter() - start
    conflict_count = len(full_projection["conflicts"])
    semantic_hashes = {node_id: node.semantic_projection_hash(PARTNER_ACCESS) for node_id, node in nodes.items()}
    # Export/replay one fully converged 50k-event store; scenario coverage
    # already replays all three smaller principal stores.
    start = time.perf_counter(); export_manifest = nodes["LOGISTICS_NODE"].export_open(root / "export_logistics")
    export_seconds = time.perf_counter() - start
    start = time.perf_counter(); replay = DistributedNode.import_open(root / "export_logistics", root / "replay_logistics")
    replay_seconds = time.perf_counter() - start
    replay_equal = replay.semantic_projection_hash(PARTNER_ACCESS) == semantic_hashes["LOGISTICS_NODE"]
    # Revoked node cannot synchronize after history/convergence proof.
    nodes["LOGISTICS_NODE"].revoke(RevocationRecord(
        "stress-revoke-strategic", "NODE", "STRATEGIC_EVIDENCE_NODE", TIMES[11], TIMES[11],
        "JOINT_COORDINATION_AUTHORITY", "stress revocation probe"))
    request = make_request(nodes["LOGISTICS_NODE"], "STRATEGIC_EVIDENCE_NODE", PARTNER_ACCESS,
                           requested_scope=(MISSION,), supported_schemas=(SCHEMA_VERSION, STRESS_SCHEMA_V2))
    revoked_bundle = build_bundle(nodes["STRATEGIC_EVIDENCE_NODE"], request, creation_time=TIMES[10])
    revoked_receipt = import_bundle(nodes["LOGISTICS_NODE"], revoked_bundle, admitted_time=TIMES[12])
    # Known leakage strings must not occur in partner bundles.
    leakage_failures = 0
    for path in (root / "bundles").glob("*.json"):
        text = path.read_text(encoding="utf-8")
        if "restricted-" in text or "STRESS_RESTRICTED" in text or "_ONLY" in text:
            leakage_failures += 1
    store_sizes = {node_id: sum(path.stat().st_size for path in node.root.rglob("*") if path.is_file())
                   for node_id, node in nodes.items()}
    metrics = {
        "target_events": total_events, "completed_origin_events": total_events,
        "primary_node_stores": 3, "synchronization_rounds": len(sync_metrics),
        "concurrent_or_causally_ambiguous_updates": 1200,
        "access_scopes": ["MISSION_PARTNERS", "STRESS_SCOPE_B", "ORIGINATOR_RESTRICTED"],
        "schema_versions": [SCHEMA_VERSION, STRESS_SCHEMA_V2],
        "ingestion_seconds": ingest_seconds, "projection_seconds": projection_seconds,
        "bundle_generation_seconds": sum(item["generation_seconds"] for item in sync_metrics),
        "bundle_verification_seconds": sum(item["verification_seconds"] for item in sync_metrics),
        "bundle_import_seconds": sum(item["import_seconds"] for item in sync_metrics),
        "conflict_detection_count": conflict_count, "bitemporal_query_seconds": bitemporal_seconds,
        "export_seconds": export_seconds, "replay_seconds": replay_seconds,
        "store_bytes": store_sizes,
        "bundle_bytes": sum(item["bundle_bytes"] for item in sync_metrics),
        "peak_rss_kib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
        "duplicate_delivery": any(item["receipt"] == "DUPLICATE" for item in sync_metrics),
        "delay_and_partial_bundle": any("partial" in item["label"] for item in sync_metrics),
        "tampering_detected": any(item["tamper_probe"] == "TAMPERED" for item in sync_metrics),
        "stale_base": stale_base, "missing_base": missing_base,
        "restart": True, "offline_partition": True, "rejoin": True,
        "schema_mismatch_receipt": schema_receipt.verification_state,
        "schema_mismatch_quarantines_privileged": len(schema_receipt.quarantines),
        "revoked_node_receipt": revoked_receipt.verification_state,
        "access_leakage_failures": leakage_failures,
        "authorized_convergence": len(set(semantic_hashes.values())) == 1,
        "semantic_hashes": semantic_hashes, "replay_equal": replay_equal,
        "provider_reinvocations": export_manifest["provider_reinvocations"],
        "integrity_and_access_checks_disabled": False,
        "sync_metrics": sync_metrics,
        "temporal_visible_summary": temporal_view["visible_summary"],
        "status": "PASS" if (total_events >= 50_000 and conflict_count >= 400 and replay_equal and
                              len(set(semantic_hashes.values())) == 1 and leakage_failures == 0) else "PARTIAL",
    }
    _write(root / "stress_metrics.json", metrics)
    return metrics
