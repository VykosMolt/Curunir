"""Sovereignty manifest, open export exit test, and the degraded-mode PACE
bundle.

Exit test: the complete synthetic mission state exports into documented open
formats (JSONL event log + content-addressed payloads + JSON manifests) and
re-imports into a fresh store without losing identifiers, history, provenance,
access markings, quality, relationships or decisions.

PACE bundle: a deterministic, integrity-hashed, access-filtered directory that
is inspectable without the application. Standard sha256 only — no classified
cryptography and no claim of secure cross-domain transport. Restricted content
is omitted under a fixed policy statement; its existence and counts are never
represented.
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from typing import Any, Mapping

from . import ENGAGEMENT_BOUNDARY, PACKAGE_VERSION
from .access import AccessContext
from .canonical import CONTRACT_VERSION, canonical_line, sha256
from .projection import Projection, projection_hash
from .sitrep import build_situation_report, render_text
from .store import MissionDataStore

OMISSION_POLICY = ("This bundle contains only content releasable to the stated access context. "
                   "Content outside that releasability, if any exists, is omitted; neither its "
                   "existence, its count, nor its labels are represented in this bundle.")


def build_sovereignty_manifest(store: MissionDataStore) -> dict[str, Any]:
    model_packages = store.records_of("model_package")
    owning_authorities = sorted({record["marking"]["owning_authority"]
                                 for record in store.records_of("object_version")}) or ["(no objects yet)"]
    return {
        "manifest_type": "CURUNIR_OPERATIONAL_SOVEREIGNTY_MANIFEST",
        "package_version": PACKAGE_VERSION, "contract_version": CONTRACT_VERSION,
        "engagement_boundary": ENGAGEMENT_BOUNDARY,
        "data_ownership": {
            "operational_objects_relations_workflow": {
                "owner": f"owning authorities recorded on markings: {', '.join(owning_authorities)}",
                "storage": "append-only JSONL event log + content-addressed payload files on local disk",
                "format": "documented open JSON (curunir-operational-open-export-v1)",
                "exportable": True, "vendor_specific_transformation_required": False,
                "full_lineage_and_history_exportable": True,
            },
            "raw_payloads": {"owner": "originating systems of record", "storage": "content-addressed files",
                             "format": "original bytes, sha256-addressed", "exportable": True,
                             "vendor_specific_transformation_required": False,
                             "full_lineage_and_history_exportable": True},
            "argus_evidence": {"owner": "ARGUS Source Intelligence plane", "storage": "referenced by id + hash, "
                               "never copied out of its custody store", "exportable": "references only",
                               "vendor_specific_transformation_required": False,
                               "full_lineage_and_history_exportable": "via ARGUS, not duplicated here"},
        },
        "model_ownership": {
            "internal_models": [{"model_id": m["model_id"], "version": m["version"], "provider": m["provider"],
                                 "accreditation_state": m["accreditation_state"], "licence": m["licence"],
                                 "integrity_hash": m["integrity_hash"]} for m in model_packages],
            "external_models": [], "prompts_templates_policies": "all internal, stored as registered definitions",
            "training_data_and_labels": "none (no learned model in V1)",
            "restrictions": "V1 accreditation states are SYNTHETIC_EVALUATION_ONLY or UNACCREDITED by contract",
        },
        "dependencies": [
            {"dependency": "Python standard library", "version": sys.version.split()[0], "licence": "PSF",
             "purpose": "entire operational runtime", "replacement": "any conforming Python",
             "data_access": "local files only", "network_required": False, "criticality": "required"},
            {"dependency": "argus.prospective.freezing + argus.source_intelligence.models (helpers)",
             "version": "in-repo", "licence": "in-repo", "purpose": "canonical hashing, id and timestamp helpers",
             "replacement": "~40 lines of stdlib; confined to curunir_operational.canonical",
             "data_access": "none", "network_required": False, "criticality": "trivially replaceable"},
            {"dependency": "pytest", "version": "dev-only", "licence": "MIT", "purpose": "tests",
             "replacement": "any test runner", "data_access": "none", "network_required": False,
             "criticality": "development only"},
        ],
        "lock_in_assessment": {
            "third_party_platform": "NO_MATERIAL_THIRD_PARTY_PLATFORM_LOCK_IN — stdlib-only runtime, open "
                                    "JSONL/JSON formats, no vendor service in any path",
            "internal_schema_dependence": "INTERNAL_SCHEMA_DEPENDENCE_REMAINS — consumers depend on the "
                                          "curunir-operational contract vocabulary (record types, enums, "
                                          "provenance shapes); replacing it requires a mapping exercise",
            "semantic_migration_risk": "SEMANTIC_MIGRATION_RISK_REMAINS — epistemic states, quality dimensions "
                                       "and marking semantics carry meaning a naive field-level migration would "
                                       "lose; migration needs semantic review, not just format conversion",
            "ui_replacement_cost": "low: views are plain JSON; COP is a generated document",
            "storage_replacement_cost": "low-moderate: replay the exported event log through any implementation "
                                        "honouring the append contract",
            "provider_replacement_cost": "low: providers are register-invoke-propose contracts",
        },
        "provider_replacement": {
            "model_provider": "implement the provider contract (register ModelPackage, write InferenceRecords, "
                              "emit AnalyticalProposals); nothing downstream binds to a provider identity",
            "storage": "MissionDataStore is one class over JSONL + files; replay the exported event log through "
                       "any implementation honouring append(event_type, record, recorded_time, actor)",
            "ui": "COP and reports are generated from the projection view dict; any renderer over that JSON works",
            "mission_object_export": "curunir operational export / import (open JSONL)",
            "history_replay": "import event log into fresh store; projections rebuild deterministically",
        },
        "exit_test": "see exit_test result artifact (run_exit_test)",
    }


def run_exit_test(store: MissionDataStore, export_dir: str | Path, fresh_root: str | Path,
                  full_context: AccessContext, *, snapshot_time: str,
                  staleness_hours: Mapping[str, float] | None = None) -> dict[str, Any]:
    manifest = store.export_to(export_dir)
    imported = MissionDataStore.import_from(export_dir, fresh_root)
    original_projection = Projection(store, snapshot_time=snapshot_time, staleness_hours=staleness_hours)
    imported_projection = Projection(imported, snapshot_time=snapshot_time, staleness_hours=staleness_hours)
    original_view = original_projection.view(full_context)
    imported_view = imported_projection.view(full_context)
    checks = {
        "event_count_equal": store.head()["event_count"] == imported.head()["event_count"],
        "head_hash_equal": store.head()["head_hash"] == imported.head()["head_hash"],
        "chain_valid": imported.verify_chain()["valid"],
        "projection_hash_equal": projection_hash(original_view) == projection_hash(imported_view),
        "object_ids_preserved": sorted(original_projection.objects) == sorted(imported_projection.objects),
        "history_preserved": all(
            [v["version"] for v in original_projection.objects[o]["versions"]]
            == [v["version"] for v in imported_projection.objects[o]["versions"]]
            for o in original_projection.objects),
        "markings_preserved": all(
            original_projection.objects[o]["current"]["marking"] == imported_projection.objects[o]["current"]["marking"]
            for o in original_projection.objects),
        "provenance_preserved": all(
            original_projection.objects[o]["current"]["provenance"] == imported_projection.objects[o]["current"]["provenance"]
            for o in original_projection.objects),
        "decisions_preserved": original_projection.decisions == imported_projection.decisions,
        "relationships_preserved": sorted(original_projection.relationships) == sorted(imported_projection.relationships),
    }
    return {"passed": all(checks.values()), "checks": checks,
            "export_manifest": manifest,
            "original_head": store.head(), "imported_head": imported.head()}


# ---- PACE bundle ------------------------------------------------------------

def build_pace_bundle(store: MissionDataStore, projection: Projection, context: AccessContext,
                      out_dir: str | Path, *, operational_context: str, since_seq: int = 0) -> dict[str, Any]:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    view = projection.view(context)
    changes = projection.changes_since(since_seq, context, store)
    report = build_situation_report(store, projection, context, operational_context=operational_context,
                                    since_seq=since_seq)
    schema_versions = [{"schema_id": s["schema_id"], "version": s["version"],
                        "definition_sha256": s["definition_sha256"]}
                       for s in store.records_of("schema_definition")]
    members = {
        "projection.json": canonical_line(view) + "\n",
        "changes.json": canonical_line(changes) + "\n",
        "alerts.json": canonical_line(view["alerts"]) + "\n",
        "decisions.json": canonical_line(view["decisions"]) + "\n",
        "provenance_refs.json": canonical_line(report["report"]["provenance_references"]) + "\n",
        "sitrep.txt": render_text(report),
    }
    member_hashes = {}
    from .store import _export_write_bytes
    for name, content in members.items():
        _export_write_bytes(out_dir / name, content.encode("utf-8"))
        member_hashes[name] = hashlib.sha256(content.encode("utf-8")).hexdigest()
    manifest = {
        "bundle_type": "OperationalPACEBundle", "bundle_format": "curunir-operational-pace-v1",
        "store_id": store.meta["store_id"], "snapshot_time": view["meta"]["snapshot_time"],
        "state_token": view["meta"]["state_token"], "base_state_token": store.state_token_at(since_seq),
        "access_context_id": context.context_id, "omission_policy": OMISSION_POLICY,
        "schema_versions": schema_versions, "report_integrity_hash": report["integrity_hash"],
        "members": member_hashes, "combined_sha256": sha256(member_hashes),
        "boundary_note": "integrity by standard sha256 only; no secure cross-domain transport is claimed",
    }
    from .store import _export_write_bytes as _write_manifest
    _write_manifest(out_dir / "bundle_manifest.json",
                    (canonical_line(manifest) + "\n").encode("utf-8"))
    return manifest


def verify_pace_bundle(bundle_dir: str | Path) -> dict[str, Any]:
    bundle_dir = Path(bundle_dir)
    try:
        manifest = json.loads((bundle_dir / "bundle_manifest.json").read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {"valid": False, "reason": "bundle_manifest.json missing"}
    failures = []
    for name, expected in manifest["members"].items():
        path = bundle_dir / name
        if not path.exists():
            failures.append(f"{name}: missing")
            continue
        actual = hashlib.sha256(path.read_bytes()).hexdigest()
        if actual != expected:
            failures.append(f"{name}: hash mismatch")
    if sha256(manifest["members"]) != manifest["combined_sha256"]:
        failures.append("combined hash mismatch")
    return {"valid": not failures, "failures": failures, "bundle_format": manifest.get("bundle_format"),
            "state_token": manifest.get("state_token"), "access_context_id": manifest.get("access_context_id")}


def import_pace_bundle(bundle_dir: str | Path) -> dict[str, Any]:
    """Load a verified bundle as a NON-AUTHORITATIVE briefing snapshot. A PACE
    bundle is a filtered projection, not the event log; full-fidelity import
    goes through the open export (run_exit_test path)."""
    verification = verify_pace_bundle(bundle_dir)
    if not verification["valid"]:
        raise ValueError(f"PACE bundle failed verification: {verification['failures']}")
    bundle_dir = Path(bundle_dir)
    return {
        "non_authoritative_import": True,
        "manifest": json.loads((bundle_dir / "bundle_manifest.json").read_text(encoding="utf-8")),
        "projection": json.loads((bundle_dir / "projection.json").read_text(encoding="utf-8")),
        "changes": json.loads((bundle_dir / "changes.json").read_text(encoding="utf-8")),
        "sitrep_text": (bundle_dir / "sitrep.txt").read_text(encoding="utf-8"),
    }
