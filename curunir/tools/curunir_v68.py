"""Curunir V6.8 terminal-validation harness.

This is qualification tooling over the accepted V6 planes.  It does not add
a product plane or an operator UI.  It prepares the frozen missions through
existing production stores/connectors, serves the existing workbench with a
small privacy-bounded audit middleware, packages retained mission evidence,
and verifies custody/replay/measurement conditions.
"""
from __future__ import annotations

import argparse
import contextlib
import fcntl
import hashlib
import json
import os
import re
import secrets
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import unicodedata
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

from starlette.requests import Request as StarletteRequest

from curunir_operational.canonical import canonical_line, digest_id, parse_json_strict, sha256


PACKAGE_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = PACKAGE_ROOT.parent
#: The external ARGUS kernel package.  Its *identity* (whole-tree hash and file
#: count) is frozen in CURUNIR_V6_8_REPOSITORY_TRUTH.json; its *location* is
#: the repository's kernel plane, overridable with CURUNIR_ARGUS_KERNEL.
KERNEL_PACKAGE = Path(os.environ.get("CURUNIR_ARGUS_KERNEL", REPO_ROOT / "kernel" / "argus"))
CONTRACT_PATH = PACKAGE_ROOT / "CURUNIR_V6_8_QUALIFICATION.json"
MISSIONS_PATH = PACKAGE_ROOT / "CURUNIR_V6_8_MISSIONS.json"
TRUTH_PATH = PACKAGE_ROOT / "CURUNIR_V6_8_REPOSITORY_TRUTH.json"
V7_LEDGER_PATH = PACKAGE_ROOT / "CURUNIR_V6_8_V7_FOLLOW_UP.json"
REPAIR_CONTRACT_PATH = PACKAGE_ROOT / "CURUNIR_V6_8_REPAIR_CONTRACT.json"
PROTOCOL_PATH = PACKAGE_ROOT / "CURUNIR_V6_8_PILOT_PROTOCOL.md"
FIXTURE_ROOT = PACKAGE_ROOT / "v68" / "fixtures"

MISSION_IDS = (
    "M1_CORPORATE_REGISTRY_CAPSTONE",
    "M2_REGULATORY_CORRECTION",
    "M3_RELIEF_COLLABORATION",
)
PRIMARY_ACTOR = "v68-primary-operator"
APPROVER_ACTOR = "v68-human-approver"
PUBLIC_ACTOR = "v68-public-observer"
PREPARATION_ACTOR = "v68-preparation-service"

# Existing workbench routes that expose operational objects, events, alerts,
# and recommendations.  These names are not MissionProjection families.
M3_OPERATIONAL_REVIEW_PATHS = ("/api/overview", "/api/activity")

# Remainder tokens allowed around an exact cited statement or asserted value.
# Any other leftover token means the sentence asserts extra, unbound content.
_BINDING_WRAPPER_TOKENS = frozenset({
    "a", "an", "and", "as", "at", "after", "affected", "before", "being",
    "be", "been", "by", "correction", "corrected", "current", "derivative",
    "earlier", "english", "equals", "equal", "evidence", "facility", "for",
    "from", "historical", "in", "is", "its", "it", "name", "named", "notice",
    "notional", "observation", "observed", "of", "on", "or", "previous",
    "public", "record", "recorded", "remain", "remained", "remains", "report",
    "reporting", "reports", "status", "superseded", "support", "supported",
    "that", "the", "these", "this", "those", "to", "v1", "v2", "value",
    "version", "was", "were", "with",
})

M1_MARKING_AUTHORITY = "apple-corporate-visibility"
M2_MARKING_AUTHORITY = "M2_REGULATORY_CORRECTION"
M3_MARKING_AUTHORITY = "M3_RELIEF_COLLABORATION"


class V68Error(RuntimeError):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _read_json(path: Path) -> Any:
    return parse_json_strict(path.read_bytes(), label=str(path))


def _write_json(path: Path, value: Any, *, mode: int = 0o644) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    body = _json_bytes(value)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(body)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def _json_bytes(value: Any) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False,
                       allow_nan=False) + "\n").encode("utf-8")


def _sha256_bytes(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _mission_definition(mission_id: str) -> dict[str, Any]:
    definitions = _read_json(MISSIONS_PATH)["missions"]
    try:
        return next(item for item in definitions if item["mission_id"] == mission_id)
    except StopIteration as exc:
        raise V68Error(f"unknown frozen mission: {mission_id}") from exc


def _validate_frozen_authority_files() -> None:
    qualification = _read_json(CONTRACT_PATH)
    protocol = qualification.get("operator_protocol", {})
    repair = qualification.get("base_authority", {}).get("repair_contract", {})
    if protocol.get("path") != PROTOCOL_PATH.name \
            or protocol.get("sha256") != _sha256_file(PROTOCOL_PATH):
        raise V68Error("frozen pilot protocol identity mismatch")
    if repair.get("path") != REPAIR_CONTRACT_PATH.name \
            or repair.get("sha256") != _sha256_file(REPAIR_CONTRACT_PATH):
        raise V68Error("frozen repair-contract identity mismatch")


def _require_new_root(root: Path) -> None:
    if root.exists():
        raise V68Error(f"mission root already exists; refusing overwrite: {root}")
    root.mkdir(parents=True, mode=0o700)


def _marking(authority: str, *, restricted: bool = False):
    from curunir_operational.access import Marking
    return Marking(
        owning_authority=authority,
        compartments=("SPECIAL",) if restricted else (),
        releasability=("PUBLIC",),
        min_role="ANALYST" if restricted else "OBSERVER",
    )


class FixtureClock:
    """Deterministic monotonic knowledge time for notional mission setup."""

    def __init__(self, minute: int = 0):
        self.minute = minute

    def __call__(self) -> str:
        hour = 12 + self.minute // 60
        minute = self.minute % 60
        self.minute += 1
        return f"2026-08-21T{hour:02d}:{minute:02d}:00+00:00"


def _actor_entries() -> list[dict[str, Any]]:
    return [
        {
            "token": secrets.token_urlsafe(32),
            "actor_id": PRIMARY_ACTOR,
            "actor_kind": "HUMAN",
            "roles": ["ANALYST", "SUPERVISOR"],
            "compartments": ["SPECIAL"],
            "releasability": ["PUBLIC"],
            "organisation": "v68-pilot",
            "enabled": True,
        },
        {
            "token": secrets.token_urlsafe(32),
            "actor_id": APPROVER_ACTOR,
            "actor_kind": "HUMAN",
            "roles": ["ANALYST", "SUPERVISOR"],
            "compartments": ["SPECIAL"],
            "releasability": ["PUBLIC"],
            "organisation": "v68-pilot",
            "enabled": True,
        },
        {
            "token": secrets.token_urlsafe(32),
            "actor_id": PUBLIC_ACTOR,
            "actor_kind": "HUMAN",
            "roles": ["OBSERVER"],
            "compartments": [],
            "releasability": ["PUBLIC"],
            "organisation": "v68-pilot",
            "enabled": True,
        },
    ]


def _write_actors(root: Path) -> Path:
    from curunir_workbench.auth import write_registry
    actors_path = root / "actors.json"
    write_registry(actors_path, _actor_entries())
    return actors_path


def _current_authority() -> dict[str, str]:
    return {
        "contract": CONTRACT_PATH.name,
        "contract_sha256": _sha256_file(CONTRACT_PATH),
        "missions": MISSIONS_PATH.name,
        "missions_sha256": _sha256_file(MISSIONS_PATH),
        "repository_truth": TRUTH_PATH.name,
        "repository_truth_sha256": _sha256_file(TRUTH_PATH),
        "repair_contract": REPAIR_CONTRACT_PATH.name,
        "repair_contract_sha256": _sha256_file(REPAIR_CONTRACT_PATH),
        "pilot_protocol": PROTOCOL_PATH.name,
        "pilot_protocol_sha256": _sha256_file(PROTOCOL_PATH),
    }


def _accepted_authorities() -> list[dict[str, str]]:
    """The authority sets a campaign root may carry.

    The current byte identities of the five authority files come first.  A
    superseded set is accepted only if the frozen qualification contract
    ledgers it under ``authority_supersession`` with all five hashes: a
    revision of the authority files is a change-control event recorded in the
    contract, never an implicit pass."""
    current = _current_authority()
    accepted = [current]
    for entry in _read_json(CONTRACT_PATH).get("authority_supersession", ()):
        keys = ("contract_sha256", "missions_sha256", "repository_truth_sha256",
                "repair_contract_sha256", "pilot_protocol_sha256")
        if not all(isinstance(entry.get(key), str) and len(entry[key]) == 64 for key in keys):
            raise V68Error("authority_supersession entry is incomplete")
        accepted.append({**current, **{key: entry[key] for key in keys}})
    return accepted


def _copy_mission_authority(root: Path, mission_id: str) -> None:
    _validate_frozen_authority_files()
    _write_json(root / "mission_definition.json", _mission_definition(mission_id))
    ledger = _read_json(V7_LEDGER_PATH)
    ledger["mission_id"] = mission_id
    _write_json(root / "v7_follow_up.json", ledger)
    _write_json(root / "qualification_authority.json", {
        "contract": CONTRACT_PATH.name,
        "contract_sha256": _sha256_file(CONTRACT_PATH),
        "missions": MISSIONS_PATH.name,
        "missions_sha256": _sha256_file(MISSIONS_PATH),
        "repository_truth": TRUTH_PATH.name,
        "repository_truth_sha256": _sha256_file(TRUTH_PATH),
        "repair_contract": REPAIR_CONTRACT_PATH.name,
        "repair_contract_sha256": _sha256_file(REPAIR_CONTRACT_PATH),
        "pilot_protocol": PROTOCOL_PATH.name,
        "pilot_protocol_sha256": _sha256_file(PROTOCOL_PATH),
    })


def _repository_identity(*, require_clean: bool) -> dict[str, Any]:
    """Bind a prepared campaign to one exact, clean executable and kernel."""
    commit = subprocess.run(
        ["git", "-C", str(REPO_ROOT), "rev-parse", "HEAD"], check=True,
        capture_output=True, text=True).stdout.strip()
    status = subprocess.run(
        ["git", "-C", str(REPO_ROOT), "status", "--porcelain=v1",
         "--untracked-files=normal"], check=True,
        capture_output=True, text=True).stdout
    if require_clean and status:
        raise V68Error("V6.8 preparation requires a clean executable checkout")
    from tools.reconstruct_v67 import kernel_identity
    truth = _read_json(TRUTH_PATH)["external_kernel"]
    kernel = KERNEL_PACKAGE
    kernel_hash, kernel_count, _ = kernel_identity(kernel)
    if kernel_hash != truth["tree_sha256"] \
            or kernel_count != truth["python_file_count"]:
        raise V68Error("external kernel identity differs from the accepted V6.7 pin")
    return {
        "executable_sha": commit,
        "worktree_clean": not bool(status),
        "kernel_path": str(kernel),
        "kernel_tree_sha256": kernel_hash,
        "kernel_python_file_count": kernel_count,
    }


def _open_fixture_need(store, *, mission_id: str, question: str, marking, now: str):
    from curunir_fabric.mission_bridge import open_requirement_with_need
    return open_requirement_with_need(
        store,
        mission_context=mission_id,
        question=question,
        entities=(),
        languages=("en",),
        scripts=(),
        now=now,
        actor=PREPARATION_ACTOR,
        marking=marking,
    )


def _plant_fixture_manifestation(
    pipeline,
    *,
    need_id: str,
    source_id: str,
    native_id: str,
    body: bytes,
    media_type: str,
    retrieval_time: str,
    marking,
    prior_manifestation_id: str | None = None,
    derived_from: tuple[str, ...] = (),
    temporal_status: str = "LIVE",
    source_time: str | None = None,
    archive_capture_time: str | None = None,
) -> dict[str, Any]:
    """Admit a frozen scenario artifact with explicit fixture lineage.

    This is deterministic scenario setup, not a simulated network result.  The
    plan, manifestation, and execution all label that fact, while raw bytes use
    the same content-addressed custody and semantic pipeline as live evidence.
    """
    from curunir_fabric import ABSENCE_SEMANTICS
    from curunir_fabric.contracts import DiscoveryPlan, ExecutionRecord, ManifestationRecord, QuerySpec

    digest = _sha256_bytes(body)
    custody = Path(pipeline.custody_root) / "sha256" / digest[:2] / digest[2:4] / digest
    custody.parent.mkdir(parents=True, exist_ok=True)
    if custody.exists() and _sha256_file(custody) != digest:
        raise V68Error(f"custody collision for fixture {native_id}")
    if not custody.exists():
        custody.write_bytes(body)

    query_id = digest_id("v68-fixture-query", need_id, native_id, digest)
    plan_id = digest_id("v68-fixture-plan", query_id)
    execution_id = digest_id("v68-fixture-execution", query_id, retrieval_time)
    manifestation_id = digest_id(
        "manifestation", source_id, native_id, digest, retrieval_time)
    query = QuerySpec(
        query_id=query_id,
        family="NATIVE_OBJECT",
        value=native_id,
        language="",
        script="",
        operation="FETCH",
        source_id=source_id,
        time_bounds=(None, None),
        origin="RULE",
        origin_detail="FROZEN_NOTIONAL_FIXTURE",
        rationale="deterministic V6.8 mission setup; not a live retrieval",
        derived_from=derived_from,
    )
    plan = DiscoveryPlan(
        plan_id=plan_id,
        need_id=need_id,
        generation="INITIAL",
        queries=(query,),
        considered_source_ids=(source_id,),
        unmatched_query_ids=(),
        budget_max_requests=1,
        planner_version="curunir-v6.8-fixture-loader-v1",
        created_time=retrieval_time,
        marking=marking,
    )
    pipeline.store.append(
        "FABRIC_PLAN_RECORDED", plan,
        recorded_time=pipeline.now_fn(), actor=PREPARATION_ACTOR)
    relative = Path("custody") / "sha256" / digest[:2] / digest[2:4] / digest
    manifestation = ManifestationRecord(
        manifestation_id=manifestation_id,
        source_id=source_id,
        connector_id="v68-notional-fixture-v1",
        connector_version="1.0",
        native_id=native_id,
        request_url=native_id,
        final_url=native_id,
        content_sha256=digest,
        content_store_path=str(relative),
        media_type=media_type,
        temporal_status=temporal_status,
        source_time=source_time,
        archive_capture_time=archive_capture_time,
        retrieval_time=retrieval_time,
        http_status=None,
        redirects=(),
        etag="",
        last_modified="",
        truncated=False,
        retrieval_id=digest_id("v68-fixture-retrieval", manifestation_id),
        custody_ingestion_id=digest_id("v68-fixture-ingestion", digest),
        source_object_id=digest_id("v68-fixture-object", source_id, native_id),
        execution_id=execution_id,
        prior_manifestation_id=prior_manifestation_id,
        marking=marking,
    )
    pipeline.store.append(
        "FABRIC_MANIFESTATION_RECORDED", manifestation,
        recorded_time=pipeline.now_fn(), actor=PREPARATION_ACTOR)
    execution = ExecutionRecord(
        execution_id=execution_id,
        plan_id=plan_id,
        query_id=query_id,
        source_id=source_id,
        connector_id="v68-notional-fixture-v1",
        connector_version="1.0",
        operation="FETCH",
        outcome="EXECUTED_WITH_RESULTS",
        result_count=1,
        request_url=native_id,
        http_status=None,
        policy_decision="FROZEN_NOTIONAL_FIXTURE",
        error_class=None,
        error_detail="",
        manifestation_ids=(manifestation_id,),
        started_time=retrieval_time,
        completed_time=retrieval_time,
        absence_semantics=ABSENCE_SEMANTICS,
        marking=marking,
    )
    pipeline.store.append(
        "FABRIC_EXECUTION_RECORDED", execution,
        recorded_time=pipeline.now_fn(), actor=PREPARATION_ACTOR)
    return manifestation.to_record()


def _fixture_manifest(paths: Iterable[Path]) -> list[dict[str, Any]]:
    return [
        {
            "path": str(path.relative_to(PACKAGE_ROOT)),
            "bytes": path.stat().st_size,
            "sha256": _sha256_file(path),
            "classification": "NOTIONAL_PUBLIC_EVIDENCE",
        }
        for path in sorted(paths)
    ]


def _prepare_m2(root: Path) -> dict[str, Any]:
    from dataclasses import replace
    from curunir_fabric.catalog import seed_starter_catalog, starter_catalog
    from curunir_fabric.contracts import ChangeObservation
    from curunir_fabric.registry import register_source
    from curunir_semantic.contracts import ClaimStateRecord
    from curunir_semantic.pipeline import SemanticPipeline
    from curunir_workbench.store import WorkbenchStore

    clock = FixtureClock()
    marking = _marking(M2_MARKING_AUTHORITY)
    store = WorkbenchStore.create(root / "store", M2_MARKING_AUTHORITY, clock())
    seed_starter_catalog(store, recorded_time=clock(), actor=PREPARATION_ACTOR)
    catalog = starter_catalog(clock())
    base_api, base_web = catalog[5], catalog[4]
    for source_id, base, name, connector_id in (
        ("v68-notional-regulator-api", base_api,
         "Notional Regulator Structured Notice API", "v68-notional-api-fixture-v1"),
        ("v68-notional-regulator-web", base_web,
         "Notional Regulator Public Notice Site", "v68-notional-web-fixture-v1"),
    ):
        descriptor, profile = base
        descriptor = replace(
            descriptor, source_id=source_id, canonical_name=name,
            publisher_name="V6.8 Notional Regulator",
            base_urls=("https://regulator.notional.example",),
            jurisdictions=("NOTIONAL",), official_status="OFFICIAL",
            independence_notes=(
                "same notional publisher across API, English page, and Croatian "
                "derivative; channels are distinct source identities but not "
                "independent corroboration"),
            transaction_time=clock(), created_at=clock())
        profile = replace(
            profile, profile_id=digest_id("profile", source_id, "v68"),
            source_id=source_id, connector_id=connector_id,
            connector_version="1.0", supported_operations=("FETCH",),
            native_id_scheme="NOTIONAL_NOTICE_URL", created_time=clock())
        register_source(store, descriptor, profile, recorded_time=clock(),
                        actor=PREPARATION_ACTOR)
    pipeline = SemanticPipeline(
        store=store, custody_root=root / "custody",
        actor=PREPARATION_ACTOR, marking=marking, now_fn=clock)
    need = _open_fixture_need(
        store, mission_id="M2_REGULATORY_CORRECTION",
        question="Which version of notional notice VR-2026-41 is current?",
        marking=marking, now=clock())

    fixture_dir = FIXTURE_ROOT / "m2_regulatory"
    paths = [
        fixture_dir / "notice_v1.json",
        fixture_dir / "notice_v1.html",
        fixture_dir / "notice_v1_hr.html",
        fixture_dir / "notice_v2_correction.html",
    ]
    structured = _plant_fixture_manifestation(
        pipeline, need_id=need.need_id, source_id="v68-notional-regulator-api",
        native_id="https://regulator.notional.example/notices/VR-2026-41.v1.json",
        body=paths[0].read_bytes(), media_type="application/json",
        retrieval_time="2026-08-21T12:10:00+00:00", marking=marking)
    initial = _plant_fixture_manifestation(
        pipeline, need_id=need.need_id, source_id="v68-notional-regulator-web",
        native_id="https://regulator.notional.example/notices/VR-2026-41",
        body=paths[1].read_bytes(), media_type="text/html",
        retrieval_time="2026-08-21T12:11:00+00:00", marking=marking)
    translated = _plant_fixture_manifestation(
        pipeline, need_id=need.need_id, source_id="v68-notional-regulator-web",
        native_id="https://regulator.notional.example/hr/notices/VR-2026-41",
        body=paths[2].read_bytes(), media_type="text/html",
        retrieval_time="2026-08-21T12:12:00+00:00", marking=marking,
        temporal_status="HISTORICAL",
        archive_capture_time="2026-08-21T12:12:00+00:00",
        derived_from=(initial["manifestation_id"],))
    pipeline.process_manifestation(structured)
    pipeline.process_manifestation(initial)
    pipeline.process_manifestation(translated)

    affected_claims = [
        claim for claim in store.current_claims().values()
        if claim["predicate"] == "affected_facility"
        and claim["object_or_value"] == "Bridge N-4"
    ]
    if len(affected_claims) < 2:
        raise V68Error("M2 fixture failed to produce original and translated claims")
    corrected = _plant_fixture_manifestation(
        pipeline, need_id=need.need_id, source_id="v68-notional-regulator-web",
        native_id="https://regulator.notional.example/notices/VR-2026-41",
        body=paths[3].read_bytes(), media_type="text/html",
        retrieval_time="2026-08-21T12:30:00+00:00", marking=marking,
        prior_manifestation_id=initial["manifestation_id"])
    change = ChangeObservation(
        change_id=digest_id("v68-m2-change", initial["manifestation_id"], corrected["manifestation_id"]),
        watch_id="v68-m2-notice-watch",
        run_id="v68-m2-watch-run-2",
        change_type="CONTENT_CHANGED",
        detail="frozen notional notice v2 differs from v1",
        prior_ref=initial["manifestation_id"],
        current_ref=corrected["manifestation_id"],
        evidence_manifestation_ids=(corrected["manifestation_id"],),
        observed_time=clock(),
        marking=marking,
    )
    store.append(
        "FABRIC_CHANGE_OBSERVED", change,
        recorded_time=clock(), actor=PREPARATION_ACTOR)
    changes = pipeline.process_fabric_changes()
    observations = {item["observation_id"]: item
                    for item in store.records_of("semantic_observation")}
    translated_n4 = [
        claim for claim in store.current_claims().values()
        if claim.get("predicate") == "affected_facility"
        and claim.get("object_or_value") == "Bridge N-4"
        and any(observations.get(observation_id, {}).get("manifestation_id")
                == translated["manifestation_id"]
                for observation_id in claim.get("observation_ids", ()))
    ]
    current_n9 = [
        claim for claim in store.current_claims().values()
        if claim.get("predicate") == "affected_facility"
        and claim.get("object_or_value") == "Bridge N-9"
    ]
    facility_changes = [item for item in store.records_of("semantic_change")
                        if item.get("attribute") == "affected_facility"
                        and item.get("prior_value") == "Bridge N-4"
                        and item.get("current_value") == "Bridge N-9"]
    if len(translated_n4) != 1 or len(current_n9) != 1 or len(facility_changes) != 1:
        raise V68Error("M2 fixture failed to isolate dependent N-4 and current N-9")
    translation_state = ClaimStateRecord(
        state_id=digest_id("v68-m2-translation-state", translated_n4[0]["claim_id"]),
        claim_id=translated_n4[0]["claim_id"], state="SUPERSEDED",
        reason="dependent translation of English v1 is historical after correction v2",
        caused_by=facility_changes[0]["change_id"],
        superseded_by=current_n9[0]["claim_id"],
        actor_id=PREPARATION_ACTOR, actor_kind="SERVICE",
        recorded_time=clock(), marking=marking)
    store.append("SEMANTIC_CLAIM_STATE_RECORDED", translation_state,
                 recorded_time=translation_state.recorded_time,
                 actor=PREPARATION_ACTOR)

    from curunir_operational.missions import MissionWorkflow
    workflow = MissionWorkflow(store)
    requirement = workflow.open_requirement(
        mission_context="M2_REGULATORY_CORRECTION",
        question="Which facility is named by the current corrected notice?",
        affected_ids=(facility_changes[0]["change_id"],),
        priority="HIGH",
        rationale="the source correction invalidates the earlier settled reading",
        required_evidence_type="OPEN_SOURCE",
        owning_role="ANALYST",
        closure_criteria="human disposition cites the current notice and preserves the superseded version",
        due_time=None,
        recorded_time=clock(),
        marking=marking,
        actor=PREPARATION_ACTOR,
    )
    task = workflow.assign_task(
        assigned_role="ANALYST", assigned_actor=PRIMARY_ACTOR,
        task_type="REVIEW",
        affected_ids=(requirement["requirement_id"],),
        required_action="Review VR-2026-41 v1, translation, and correction v2",
        due_time=None, depends_on=(), recorded_time=clock(),
        marking=marking, actor=PREPARATION_ACTOR)
    return {
        "mode": "DETERMINISTIC_NOTIONAL_FIXTURE",
        "fixture_files": _fixture_manifest(paths),
        "manifestations": [
            structured["manifestation_id"], initial["manifestation_id"],
            translated["manifestation_id"], corrected["manifestation_id"],
        ],
        "dependent_translation_claim_id": translated_n4[0]["claim_id"],
        "current_facility_claim_id": current_n9[0]["claim_id"],
        "translation_state_id": translation_state.state_id,
        "semantic_change_outcomes": changes,
        "requirement_id": requirement["requirement_id"],
        "task_id": task["task_id"],
        "chain": store.verify_chain(),
    }


def _prepare_m3(root: Path) -> dict[str, Any]:
    from curunir_fabric.catalog import seed_starter_catalog
    from curunir_operational.access import AccessContext
    from curunir_operational.analytics import DeterministicRuleProvider
    from curunir_operational.contracts import SourceRecord
    from curunir_operational.missions import MissionWorkflow
    from curunir_operational.pipelines import PipelineExecutor, build_connector
    from curunir_operational.projection import Projection
    from curunir_operational.schema_registry import SchemaRegistry
    from curunir_operational.scenario import feeds
    from curunir_operational.scenario.config import MAPPINGS, PIPELINES, SCHEMAS, at
    from curunir_operational.workflow import WorkflowEngine
    from curunir_semantic.pipeline import SemanticPipeline
    from curunir_workbench.store import WorkbenchStore

    clock = FixtureClock()
    public = _marking(M3_MARKING_AUTHORITY)
    restricted = _marking(M3_MARKING_AUTHORITY, restricted=True)
    store = WorkbenchStore.create(root / "store", M3_MARKING_AUTHORITY, clock())
    seed_starter_catalog(store, recorded_time=clock(), actor=PREPARATION_ACTOR)
    registry = SchemaRegistry(store)
    for source_id, source_type, system, operator in (
        ("src-regsys", "SYSTEM", "CORRIDOR-REGISTRY", "Notional Registry Office"),
        ("src-logsys", "SYSTEM", "LOGSYS", "Notional Logistics Office"),
        ("src-moveplan", "SYSTEM", "MOVEPLAN", "Notional Planning Cell"),
        ("src-fieldnet", "SENSOR", "FIELDNET", "Notional Field Network"),
        ("src-civdef", "ORGANISATION", "CIVDEF-BULLETIN", "Notional Civil Office"),
    ):
        record = SourceRecord(
            source_id, source_type, system, f"{system.lower()}-01", operator,
            M3_MARKING_AUTHORITY, {"track_record": "UNKNOWN"}, public,
            "ACTIVE", "", clock())
        store.append("SOURCE_REGISTERED", record, recorded_time=clock(), actor=PREPARATION_ACTOR)
    for schema in SCHEMAS:
        registry.register_schema(schema, recorded_time=clock(), actor=PREPARATION_ACTOR)
    for mapping in MAPPINGS:
        registry.register_mapping(mapping, recorded_time=clock(), actor=PREPARATION_ACTOR)
    definitions = []
    connectors = {}
    for original in PIPELINES:
        definition = json.loads(json.dumps(original))
        definition["marking"] = public.to_record()
        definitions.append(definition)
        connectors[definition["connector_id"]] = build_connector(
            definition.get("connector_kind", "json"), definition["connector_id"],
            definition["schema_id"], definition.get("event_id_field"))
    executor = PipelineExecutor(store, registry, connectors)
    for definition in definitions:
        executor.register_pipeline(definition, recorded_time=clock(), actor=PREPARATION_ACTOR)

    ingestions: list[dict[str, Any]] = []
    fixture_payloads: list[dict[str, Any]] = []

    def ingest(pipeline_id: str, body: bytes, source_id: str, source_time: str,
               *, marking_override=None) -> dict[str, Any]:
        recorded = clock()
        result = executor.run(
            pipeline_id, body, source_id=source_id, source_time=source_time,
            received_time=recorded, recorded_time=recorded,
            actor=PREPARATION_ACTOR, marking_override=marking_override)
        ingestions.append(result)
        fixture_payloads.append({
            "pipeline_id": pipeline_id, "source_id": source_id,
            "bytes": len(body), "sha256": _sha256_bytes(body),
            "marking": "SPECIAL" if marking_override else "PUBLIC",
        })
        return result

    ingest("infrastructure-status", feeds.infrastructure_registry(), "src-regsys", at(-24))
    ingest("route-registry", feeds.route_registry(), "src-regsys", at(-24))
    ingest("logistics-stock", feeds.STOCK_INITIAL, "src-logsys", at(0))
    ingest("movement-plan", feeds.movement_plan(), "src-moveplan", at(0.75))
    ingest("civdef-bulletins", feeds.BULLETIN_DAMAGE, "src-civdef", at(24.5))
    ingest("field-observations", feeds.SENSOR_BRIDGE_OK, "src-fieldnet", at(25.4))
    ingest("field-observations", feeds.SENSOR_SUBSTATION_RESTRICTED,
           "src-fieldnet", at(25.6), marking_override=restricted)
    ingest("field-observations", feeds.SENSOR_ROUTE_OBSTRUCTION, "src-fieldnet", at(25.9))
    ingest("logistics-stock", feeds.STOCK_UPDATE, "src-logsys", at(24.0))
    ingest("civdef-bulletins", feeds.BULLETIN_CORRECTION, "src-civdef", at(26.9))

    service_context = AccessContext(
        "v68-m3-rules", PREPARATION_ACTOR, "SERVICE", ("ANALYST",),
        compartments=("SPECIAL",), releasability=("PUBLIC",),
        organisation=M3_MARKING_AUTHORITY)
    projection = Projection(store, snapshot_time=clock())
    proposals = DeterministicRuleProvider(store).run(
        projection, service_context, recorded_time=clock())
    workflow_engine = WorkflowEngine(store)
    materialized = []
    for proposal in proposals:
        if proposal["proposal_type"] in (
            "ALERT_CANDIDATE", "RELATIONSHIP_CANDIDATE",
            "STATE_CANDIDATE", "RECOMMENDATION_CANDIDATE",
        ):
            materialized.append(workflow_engine.materialize(
                proposal, actor_id=PREPARATION_ACTOR, recorded_time=clock()))

    pipeline = SemanticPipeline(
        store=store, custody_root=root / "custody",
        actor=PREPARATION_ACTOR, marking=public, now_fn=clock)
    need = _open_fixture_need(
        store, mission_id="M3_RELIEF_COLLABORATION",
        question="What route disposition is supported after the public and restricted updates?",
        marking=public, now=clock())
    public_body = (
        b"<html><body><p>Fixture Status: NOTIONAL PUBLIC EVIDENCE.</p>"
        b"<p>Route Status: R1 OBSTRUCTED.</p>"
        b"<p>Observed Time: 2026-03-02T13:54:00+00:00.</p></body></html>")
    restricted_body = (
        b"<html><body><p>Fixture Status: NOTIONAL COMPARTMENTED EVIDENCE.</p>"
        b"<p>Engineering Status: SUBSTATION TOVAN UNSTABLE.</p>"
        b"<p>Observed Time: 2026-03-02T13:36:00+00:00.</p></body></html>")
    public_evidence = _plant_fixture_manifestation(
        pipeline, need_id=need.need_id, source_id="live-web",
        native_id="https://relief.notional.example/public/route-r1-status",
        body=public_body, media_type="text/html", retrieval_time=clock(), marking=public)
    restricted_evidence = _plant_fixture_manifestation(
        pipeline, need_id=need.need_id, source_id="live-web",
        native_id="https://relief.notional.example/restricted/tovan-engineering",
        body=restricted_body, media_type="text/html", retrieval_time=clock(), marking=restricted)
    pipeline.process_manifestation(public_evidence)
    restricted_pipeline = SemanticPipeline(
        store=store, custody_root=root / "custody",
        actor=PREPARATION_ACTOR, marking=restricted, now_fn=clock)
    restricted_pipeline.process_manifestation(restricted_evidence)

    mission_workflow = MissionWorkflow(store)
    requirement = mission_workflow.open_requirement(
        mission_context="M3_RELIEF_COLLABORATION",
        question="What disposition should be recorded for relief movement RELIEF-101?",
        affected_ids=("route-R1", "infra-SUB-TOV"), priority="CRITICAL",
        rationale="public obstruction and compartmented engineering evidence require joint review",
        required_evidence_type="MULTI_SOURCE_OPERATIONAL",
        owning_role="ANALYST",
        closure_criteria="assigned human records an evidence-bound disposition and unresolved limits",
        due_time=None, recorded_time=clock(), marking=restricted,
        actor=PREPARATION_ACTOR)
    task = mission_workflow.assign_task(
        assigned_role="ANALYST", assigned_actor=PRIMARY_ACTOR,
        task_type="COORDINATION",
        affected_ids=(requirement["requirement_id"],),
        required_action="Review public route evidence and SPECIAL engineering evidence",
        due_time=None, depends_on=(), recorded_time=clock(),
        marking=restricted, actor=PREPARATION_ACTOR)
    return {
        "mode": "DETERMINISTIC_NOTIONAL_PRODUCTION_PIPELINES",
        "fixture_source": "curunir_operational.scenario.feeds",
        "fixture_source_sha256": _sha256_file(
            PACKAGE_ROOT / "curunir_operational" / "scenario" / "feeds.py"),
        "fixture_payloads": fixture_payloads,
        "ingestions": ingestions,
        "rule_proposals": len(proposals),
        "materialized_records": len(materialized),
        "semantic_manifestations": [
            public_evidence["manifestation_id"],
            restricted_evidence["manifestation_id"],
        ],
        "requirement_id": requirement["requirement_id"],
        "task_id": task["task_id"],
        "chain": store.verify_chain(),
    }


def _prepare_m1(root: Path) -> dict[str, Any]:
    """Prepare the real-public capstone substrate without claiming a pilot."""
    from curunir_analytic.contracts import ImpactEdge
    from curunir_analytic.demo import MISSION, SUBJECT_LEI, SUBJECT_NAME, phase_1
    from curunir_analytic.impact import build_path, create_objective, record_assumption
    from curunir_analytic.narratives import create_narrative
    from curunir_analytic.stakeholders import create_assessment
    from curunir_analytic.substrate import AnalyticContext
    from curunir_analytic.themes import create_theme, discover_theme_candidates
    from curunir_fabric.registry import load_registry
    from curunir_operational.access import Marking
    from curunir_semantic.collection import plan_collection_routes, requirement_for_discriminator
    from curunir_semantic.hypotheses import propose_discriminator
    from curunir_semantic.worldmodel import world_object_id
    from curunir_workbench.store import WorkbenchStore

    acquisition = phase_1(root)
    store = WorkbenchStore(root / "store")
    marking = Marking(owning_authority=M1_MARKING_AUTHORITY, releasability=("PUBLIC",))
    context = AnalyticContext(
        store=store, actor=PREPARATION_ACTOR, marking=marking, now_fn=_now)
    lei_ref = f"LEI:{SUBJECT_LEI}"
    claims = [claim for claim in store.current_claims().values()
              if claim["subject_ref"] == lei_ref]
    status_claim = next(
        (claim for claim in claims if claim["predicate"] == "registration_status"),
        None)
    if status_claim is None:
        raise V68Error(
            "capstone preparation lacks a current GLEIF registration claim; "
            "the live source failure is retained in the mission root")
    lei_object = world_object_id(lei_ref)
    candidates = discover_theme_candidates(store)
    candidate = next(
        (item for item in candidates if lei_object in item["entity_ids"]), None)
    if candidate is None:
        raise V68Error("capstone theme candidate was not derived from live evidence")
    theme = create_theme(
        context,
        title=f"{SUBJECT_NAME} public registry continuity",
        description="public registry and filing continuity for the capstone subject",
        supporting_claim_ids=candidate["claim_ids"],
        entity_ids=candidate["entity_ids"],
        event_ids=candidate["event_ids"],
        provenance_kind="RULE",
        caused_by=candidate["candidate_id"],
    )
    narrative = create_narrative(
        context,
        statement=status_claim["statement"],
        supporting_claim_ids=(status_claim["claim_id"],),
    )
    stakeholder = create_assessment(
        context,
        entity_object_id=lei_object,
        context_kind="THEME",
        context_id=theme["theme_id"],
        role_in_context="capstone subject",
        supporting_claim_ids=(status_claim["claim_id"],),
    )
    objective = create_objective(
        context, mission_context=MISSION,
        statement="Maintain an auditable current assessment of public registry continuity",
        priority="HIGH", depends_on=(("object", lei_object),))
    assumption = record_assumption(
        context,
        statement="The current GLEIF record is relevant to registry-continuity monitoring",
        supporting_claim_ids=(status_claim["claim_id"],),
        objective_ids=(objective["objective_id"],))
    edge = ImpactEdge(
        edge_id=digest_id("v68-capstone-edge", objective["objective_id"]),
        from_kind="object", from_id=lei_object,
        to_kind="mission_objective", to_id=objective["objective_id"],
        edge_kind="INFERENCE", effect_order="POTENTIAL",
        authority="SUPPORTED_INFERENCE",
        note="a registry-status change would require reassessment, not an automatic conclusion",
        basis_ids=(), assumption_ids=(assumption["assumption_id"],))
    path = build_path(
        context, objective_id=objective["objective_id"],
        summary="registry-continuity monitoring path", edges=(edge,),
        caused_by=status_claim["claim_id"])

    need = store.records_of("fabric_information_need")[-1]
    discriminator = propose_discriminator(
        store,
        question=f"Does a fresh GLEIF retrieval still report the current registration status for {SUBJECT_NAME}?",
        claim_ids=(status_claim["claim_id"],),
        desired_observation_type="ENTITY_ATTRIBUTE",
        desired_subject_ref=lei_ref,
        desired_attribute="registration_status",
        source_family_hints=("gleif",),
        independence_required=False,
        now=_now(), actor=PREPARATION_ACTOR, marking=marking)
    paired = requirement_for_discriminator(
        store, discriminator, mission_context=MISSION,
        need_id=need["need_id"], now=_now(), actor=PREPARATION_ACTOR,
        marking=marking)
    routes = plan_collection_routes(
        store, load_registry(store), paired["discriminator"],
        requirement_id=paired["requirement"]["requirement_id"],
        need_id=need["need_id"], now=_now(), actor=PREPARATION_ACTOR,
        marking=marking)
    if not any(route["automatable"] and route["score"] > 0 for route in routes):
        raise V68Error("capstone has no viable post-baseline collection route")
    from curunir_operational.missions import MissionWorkflow
    task = MissionWorkflow(store).assign_task(
        assigned_role="ANALYST", assigned_actor=PRIMARY_ACTOR,
        task_type="COLLECTION_FOLLOWUP",
        affected_ids=(paired["requirement"]["requirement_id"],),
        required_action="Review baseline evidence, launch a viable route, and reassess after arrival",
        due_time=None, depends_on=(), recorded_time=_now(),
        marking=marking, actor=PREPARATION_ACTOR)
    return {
        "mode": "LIVE_PUBLIC_PREPARATION",
        "acquisition": acquisition,
        "theme_id": theme["theme_id"],
        "narrative_id": narrative["narrative_id"],
        "stakeholder_assessment_id": stakeholder["assessment_id"],
        "objective_id": objective["objective_id"],
        "impact_path_id": path["path_id"],
        "discriminator_id": paired["discriminator"]["discriminator_id"],
        "requirement_id": paired["requirement"]["requirement_id"],
        "task_id": task["task_id"],
        "route_ids": [route["route_id"] for route in routes],
        "chain": store.verify_chain(),
    }


def prepare_mission(mission_id: str, root: Path) -> dict[str, Any]:
    if mission_id not in MISSION_IDS:
        raise V68Error(f"mission must be one of: {', '.join(MISSION_IDS)}")
    executable_identity = _repository_identity(require_clean=True)
    _require_new_root(root)
    try:
        _copy_mission_authority(root, mission_id)
        if mission_id == "M1_CORPORATE_REGISTRY_CAPSTONE":
            result = _prepare_m1(root)
        elif mission_id == "M2_REGULATORY_CORRECTION":
            result = _prepare_m2(root)
        else:
            result = _prepare_m3(root)
        actors_path = _write_actors(root)
        result = {
            "format": "curunir-v6.8-preparation-v1",
            "mission_id": mission_id,
            "status": "READY_FOR_GENUINE_HUMAN_PILOT",
            "prepared_at": _now(),
            "actors_path": str(actors_path),
            "human_actor_ids": [PRIMARY_ACTOR, APPROVER_ACTOR, PUBLIC_ACTOR],
            "human_participation_recorded": False,
            "executable_identity": executable_identity,
            "preparation": result,
        }
        _write_json(root / "preparation.json", result)
        from curunir_workbench.store import WorkbenchStore
        retained = WorkbenchStore(root / "store")
        manifestations = [{
            "manifestation_id": item["manifestation_id"],
            "source_id": item["source_id"],
            "native_id": item.get("native_id", ""),
            "content_sha256": item.get("content_sha256", ""),
            "retrieval_time": item.get("retrieval_time", ""),
            "temporal_status": item.get("temporal_status", ""),
        } for item in retained.records_of("fabric_manifestation")]
        fixture_entries = result["preparation"].get(
            "fixture_files", result["preparation"].get("fixture_payloads", []))
        _write_json(root / "input_manifest.json", {
            "format": "curunir-v6.8-input-manifest-v1",
            "mission_id": mission_id,
            "preparation_sha256": sha256(result),
            "source_ids": sorted(
                {item["source_id"] for item in manifestations}
                | {item["source_id"] for item in fixture_entries
                   if isinstance(item, dict) and item.get("source_id")}),
            "manifestations": manifestations,
            "fixture_entries": fixture_entries,
        })
        return result
    except BaseException as exc:
        _write_json(root / "preparation_failure.json", {
            "format": "curunir-v6.8-preparation-failure-v1",
            "mission_id": mission_id,
            "status": "NOT_READY",
            "failure_class": "E" if mission_id.startswith("M1_") else "B",
            "error_type": type(exc).__name__,
            "detail": str(exc),
            "recorded_at": _now(),
        })
        raise


# ---- privacy-bounded pilot instrumentation ---------------------------------

PILOT_LOG = "pilot_events.jsonl"
ROLE_ACTORS = {
    "PRIMARY_OPERATOR": PRIMARY_ACTOR,
    "APPROVER": APPROVER_ACTOR,
    "PUBLIC_ACCESS_CHECK": PUBLIC_ACTOR,
}


def _pilot_entry_hash(entry: Mapping[str, Any]) -> str:
    unsigned = {key: value for key, value in entry.items() if key != "entry_hash"}
    return sha256(unsigned)


def verify_pilot_log(path: Path) -> dict[str, Any]:
    """Verify the independent instrumentation chain.

    The log is deliberately outside the mission truth store: it measures how a
    human used the existing surface and never becomes source evidence.  It is
    nevertheless hash chained so a package cannot silently rewrite the pilot.
    """
    if not path.exists():
        return {"valid": False, "event_count": 0, "head_hash": "", "error": "missing pilot log"}
    previous = "0" * 64
    count = 0
    try:
        with path.open("rb") as handle:
            for raw in handle:
                if not raw.endswith(b"\n"):
                    raise V68Error("pilot log has an unterminated line")
                entry = parse_json_strict(raw, label=str(path))
                count += 1
                if entry.get("seq") != count:
                    raise V68Error(f"pilot log sequence mismatch at {count}")
                if entry.get("prev_hash") != previous:
                    raise V68Error(f"pilot log predecessor mismatch at {count}")
                if entry.get("entry_hash") != _pilot_entry_hash(entry):
                    raise V68Error(f"pilot log hash mismatch at {count}")
                previous = entry["entry_hash"]
    except (OSError, ValueError, V68Error) as exc:
        return {"valid": False, "event_count": count, "head_hash": previous,
                "error": str(exc)}
    return {"valid": True, "event_count": count, "head_hash": previous}


def _append_pilot_event(path: Path, event: Mapping[str, Any]) -> dict[str, Any]:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_APPEND, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        os.lseek(fd, 0, os.SEEK_SET)
        raw = b""
        while chunk := os.read(fd, 64 * 1024):
            raw += chunk
        previous = "0" * 64
        count = 0
        for line in raw.splitlines(keepends=True):
            if not line.endswith(b"\n"):
                raise V68Error("pilot log has an unterminated line")
            current = parse_json_strict(line, label=str(path))
            count += 1
            if current.get("seq") != count \
                    or current.get("prev_hash") != previous \
                    or current.get("entry_hash") != _pilot_entry_hash(current):
                raise V68Error(f"pilot log failed verification at event {count}")
            previous = current["entry_hash"]
        entry = {
            "format": "curunir-v6.8-pilot-event-v1",
            "seq": count + 1,
            "prev_hash": previous,
            **dict(event),
        }
        entry["entry_hash"] = _pilot_entry_hash(entry)
        line = (canonical_line(entry) + "\n").encode("utf-8")
        os.lseek(fd, 0, os.SEEK_END)
        view = memoryview(line)
        while view:
            written = os.write(fd, view)
            if written <= 0:
                raise OSError("short pilot-log append")
            view = view[written:]
        os.fsync(fd)
        return entry
    finally:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)


def _request_category(method: str, path: str) -> str:
    if path == "/v68/pilot/start":
        return "TASK_START"
    if path == "/v68/pilot/end":
        return "TASK_END"
    if path == "/v68/pilot/correction":
        return "OPERATOR_CORRECTION"
    if path.startswith("/api/evidence/"):
        return "EVIDENCE_OPENED"
    if path.startswith("/api/provenance/") or path.endswith("/descent"):
        return "PROVENANCE_REVIEWED"
    if "/routes/" in path and path.endswith("/launch"):
        return "COLLECTION_ACTION"
    if "/hypotheses" in path:
        return "HYPOTHESIS_ACTION"
    if "/forecasts" in path:
        return "FORECAST_ACTION"
    if "warning" in path:
        return "WARNING_REVIEWED"
    if "/reports" in path:
        return "REPORT_ACTION"
    if "/review" in path:
        return "REVIEW_ACTION"
    if method != "GET":
        return "OPERATOR_WRITE"
    return "OPERATOR_READ"


def _bounded_path(path: str) -> tuple[str, str]:
    """Remove record identifiers from retained instrumentation paths."""
    patterns = (
        r"^(/api/evidence/)([^/]+)$",
        r"^(/api/claims/)([^/]+)(/descent)$",
        r"^(/api/provenance/(?:descend|ascend)/[^/]+/)([^/]+)$",
        r"^(/api/reports/)([^/]+)(/.*)?$",
    )
    for pattern in patterns:
        match = re.match(pattern, path)
        if match:
            groups = match.groups()
            identifier = groups[1]
            suffix = groups[2] if len(groups) > 2 and groups[2] else ""
            return f"{groups[0]}{{id}}{suffix}", _sha256_bytes(identifier.encode())
    return path, ""


def create_instrumented_app(root: Path):
    """Wrap the shipped workbench; no routes or UI in the product are replaced."""
    from fastapi import HTTPException
    from curunir_workbench.server import create_app
    from curunir_workbench.store import WorkbenchStore

    preparation = _read_json(root / "preparation.json")
    if preparation.get("status") != "READY_FOR_GENUINE_HUMAN_PILOT":
        raise V68Error("mission has not passed preparation")
    actors_path = root / "actors.json"
    app = create_app(root, actors_path)
    log_path = root / PILOT_LOG

    def principal(request: StarletteRequest):
        header = request.headers.get("authorization", "")
        token = header.removeprefix("Bearer ").strip() \
            if header.startswith("Bearer ") else None
        try:
            return app.state.registry.context_for(token)
        except Exception as exc:
            raise HTTPException(status_code=401, detail="authentication required") from exc

    async def control_event(request: StarletteRequest, event_kind: str):
        actor = principal(request)
        if actor.actor_kind != "HUMAN":
            raise HTTPException(status_code=403, detail="pilot participation requires a human actor")
        try:
            body = await request.json()
        except Exception:
            body = {}
        role = body.get("participant_role", "") if isinstance(body, dict) else ""
        if role not in ("PRIMARY_OPERATOR", "APPROVER", "PUBLIC_ACCESS_CHECK"):
            raise HTTPException(status_code=400, detail="invalid participant_role")
        if ROLE_ACTORS[role] != actor.actor_id:
            raise HTTPException(status_code=403,
                                detail="participant role does not match actor identity")
        session_state = _session_analysis(_pilot_events(log_path))
        key_open = (actor.actor_id, role) in session_state["open_sessions"]
        if event_kind == "SESSION_STARTED" and key_open:
            raise HTTPException(status_code=409, detail="session is already open")
        if event_kind == "SESSION_ENDED" and not key_open:
            raise HTTPException(status_code=409, detail="no matching open session")
        note = body.get("note", "") if isinstance(body, dict) else ""
        if not isinstance(note, str) or len(note) > 500:
            raise HTTPException(status_code=400, detail="note must be at most 500 characters")
        entry = _append_pilot_event(log_path, {
            "event_kind": event_kind,
            "event_time": _now(),
            "mission_id": preparation["mission_id"],
            "actor_id": actor.actor_id,
            "actor_kind": actor.actor_kind,
            "participant_role": role,
            "note": note,
        })
        return {"recorded": True, "event_seq": entry["seq"],
                "entry_hash": entry["entry_hash"]}

    @app.post("/v68/pilot/start")
    async def pilot_start(request: StarletteRequest):
        return await control_event(request, "SESSION_STARTED")

    @app.post("/v68/pilot/end")
    async def pilot_end(request: StarletteRequest):
        return await control_event(request, "SESSION_ENDED")

    @app.post("/v68/pilot/correction")
    async def pilot_correction(request: StarletteRequest):
        return await control_event(request, "OPERATOR_CORRECTION_RECORDED")

    @app.get("/v68/pilot/status")
    def pilot_status(request: StarletteRequest):
        principal(request)
        return verify_pilot_log(log_path)

    @app.get("/v68/pilot/brief")
    def pilot_brief(request: StarletteRequest):
        """Return only the caller-visible ids needed to use existing commands."""
        from curunir_workbench.projections import MissionProjection
        actor = principal(request)
        projection = MissionProjection(
            WorkbenchStore(root / "store"), actor)
        claims = projection.family("semantic_claim")
        objectives = projection.family("mission_objective")
        return {
            "mission": _mission_definition(preparation["mission_id"]),
            "actor": {"actor_id": actor.actor_id, "actor_kind": actor.actor_kind},
            "evidence": [{
                "manifestation_id": item["manifestation_id"],
                "source_id": item["source_id"],
                "url": item.get("final_url", ""),
                "retrieval_time": item.get("retrieval_time", ""),
            } for item in projection.family("fabric_manifestation")],
            "claims": [{
                "claim_id": item["claim_id"],
                "statement": item["statement"],
                "epistemic_state": item.get("epistemic_state", ""),
            } for item in claims],
            "objectives": [{"objective_id": item["objective_id"],
                            "statement": item["statement"]}
                           for item in objectives],
            "hypotheses": projection.family("hypothesis"),
            "forecasts": projection.family("analytic_forecast"),
            "collection_routes": projection.family("collection_route"),
            "operator_gate_notes": {
                key: value for key, value in {
                    "SUPPORTED_SENTENCE_BINDING": (
                        "A SUPPORTED sentence must be the cited statement or "
                        "asserted value with only licensed connective wrapping; "
                        "additional prose fails faithfulness."
                    ),
                    "M2_SOURCE_CORRECTED_REVIEWS": (
                        "Resolve OPEN SOURCE_CORRECTED review items via "
                        "POST /api/commands/review/{item_id}/resolve before "
                        "citing current N-9 as SUPPORTED. Status RESOLVED, "
                        "note required."
                    ) if preparation["mission_id"] == "M2_REGULATORY_CORRECTION" else "",
                    "M3_OPERATIONAL_REVIEW_PATHS": (
                        "Inspect operational objects, events, alerts, and "
                        "recommendations through existing GET /api/overview "
                        "and GET /api/activity; those names are not "
                        "/api/family record types."
                    ) if preparation["mission_id"] == "M3_RELIEF_COLLABORATION" else "",
                }.items() if value
            },
            "report_sentence_statuses": [
                "SUPPORTED", "EXPLICITLY_INFERENTIAL", "UNRESOLVED"],
            "forecast_command_shape": {
                "question": "operator-authored falsifiable question",
                "outcome_semantics": "exact event whose occurrence counts",
                "horizon_time": "aware ISO-8601 timestamp",
                "probability": "number from 0 to 1",
                "probability_basis": "why this authored probability is justified",
                "proposition_refs": [["claim", "claim-id-from-this-brief"]],
                "resolution": {"kind": "HUMAN_JUDGMENT",
                               "criteria": "exact resolution criterion"},
                "domain": "CORPORATE_REGISTRY",
            },
        }

    @app.middleware("http")
    async def pilot_measurement(request: StarletteRequest, call_next):
        path = request.url.path
        measured = path.startswith("/api/") or path.startswith("/v68/")
        actor_id = "UNAUTHENTICATED"
        actor_kind = "UNKNOWN"
        if measured:
            try:
                actor = principal(request)
                actor_id, actor_kind = actor.actor_id, actor.actor_kind
            except HTTPException:
                pass
        before = WorkbenchStore(root / "store").verify_chain() if measured else None
        started = time.perf_counter()
        status = 500
        try:
            if actor_id == PUBLIC_ACTOR and request.method != "GET" \
                    and path not in ("/v68/pilot/start", "/v68/pilot/end"):
                from starlette.responses import JSONResponse
                response = JSONResponse(
                    status_code=403,
                    content={"detail": "public access-check actor is read-only"})
            else:
                response = await call_next(request)
            status = response.status_code
            return response
        finally:
            if measured:
                after = WorkbenchStore(root / "store").verify_chain()
                retained_path, resource_id_sha256 = _bounded_path(path)
                _append_pilot_event(log_path, {
                    "event_kind": "HTTP_ACTION",
                    "event_time": _now(),
                    "mission_id": preparation["mission_id"],
                    "actor_id": actor_id,
                    "actor_kind": actor_kind,
                    "method": request.method,
                    "path": retained_path,
                    "resource_id_sha256": resource_id_sha256,
                    "category": _request_category(request.method, path),
                    "http_status": status,
                    "duration_ms": round((time.perf_counter() - started) * 1000, 3),
                    "store_seq_before": before["event_count"],
                    "store_seq_after": after["event_count"],
                    "store_head_before": before["head_hash"],
                    "store_head_after": after["head_hash"],
                })

    return app


# ---- evidence, measurement, and replay -------------------------------------

def _pilot_events(path: Path) -> list[dict[str, Any]]:
    verification = verify_pilot_log(path)
    if not verification["valid"]:
        return []
    return [parse_json_strict(line, label=str(path))
            for line in path.read_bytes().splitlines()]


def _access_contexts(root: Path, mission_id: str) -> list[tuple[str, Any]]:
    from curunir_workbench.auth import ActorRegistry
    registry = ActorRegistry(root / "actors.json")
    names = [PRIMARY_ACTOR]
    if mission_id in ("M1_CORPORATE_REGISTRY_CAPSTONE", "M3_RELIEF_COLLABORATION"):
        names.append(APPROVER_ACTOR)
    if mission_id == "M3_RELIEF_COLLABORATION":
        names.append(PUBLIC_ACTOR)
    return [(name, registry.context_for_actor(name)) for name in names]


def _semantic_projection(store, context) -> dict[str, Any]:
    from curunir_workbench.projections import ALL_FAMILIES, MissionProjection
    projection = MissionProjection(store, context)
    overview = projection.overview()
    overview["meta"].pop("context_id", None)
    return {
        "overview": overview,
        "operational_view": projection.base_view,
        "families": {
            family: projection.family(family)
            for family in sorted(ALL_FAMILIES)
        },
    }


def _fingerprints(store, root: Path, mission_id: str) -> dict[str, str]:
    return {
        actor_id: sha256(_semantic_projection(store, context))
        for actor_id, context in _access_contexts(root, mission_id)
    }


def _approved_reports(store) -> list[dict[str, Any]]:
    return [report for report in store.current_reports().values()
            if report.get("status") in ("APPROVED", "APPROVED_WITH_DISSENT")]


def _report_sentence_counts(reports: Iterable[Mapping[str, Any]]) -> tuple[int, int]:
    required = complete = 0
    for report in reports:
        for section in report.get("sections", ()):
            for sentence in section.get("sentences", ()):
                required += 1
                status = sentence.get("status")
                if status == "SUPPORTED" and sentence.get("basis_refs"):
                    complete += 1
                elif status == "EXPLICITLY_INFERENTIAL" \
                        and sentence.get("basis_refs") \
                        and sentence.get("inference_note"):
                    complete += 1
                elif status == "UNRESOLVED" and sentence.get("unresolved_reason"):
                    complete += 1
    return complete, required


def _normal_text(value: Any) -> str:
    folded = unicodedata.normalize("NFKC", str(value or "")).casefold()
    cleaned = re.sub(r"[^\w\s-]+", " ", folded)
    return " ".join(cleaned.split())


def _licensed_wrapper_around(text: str, value: str) -> bool:
    """True when text is exactly value, or value plus licensed connectives."""
    if len(value) < 3 or value not in text:
        return False
    start = 0
    while True:
        index = text.find(value, start)
        if index < 0:
            return False
        remainder = f"{text[:index]} {text[index + len(value):]}"
        tokens = remainder.split()
        if all(token in _BINDING_WRAPPER_TOKENS for token in tokens):
            return True
        start = index + 1


def _supported_sentence_binding(projection, sentence: Mapping[str, Any]) -> dict[str, Any]:
    """Bind settled prose to cited observational content without extra claims.

    Citation shape is not enough.  The normalized sentence must be the cited
    statement or asserted value, optionally wrapped only in licensed
    connectives.  Additional content, including a true value appended to
    unrelated prose, fails closed.
    """
    text = _normal_text(sentence.get("text"))
    matches = []
    for ref in sentence.get("basis_refs", ()):
        claim = projection.get("semantic_claim", ref)
        if claim is not None:
            candidates = (claim.get("statement", ""), claim.get("object_or_value", ""))
            if any(_licensed_wrapper_around(text, _normal_text(value))
                   for value in candidates):
                matches.append({"basis_ref": ref, "family": "semantic_claim"})
            continue
        observation = projection.get("semantic_observation", ref)
        if observation is not None:
            if _licensed_wrapper_around(text, _normal_text(observation.get("value", ""))):
                matches.append({"basis_ref": ref, "family": "semantic_observation"})
            continue
        manifestation = projection.get("fabric_manifestation", ref)
        if manifestation is not None:
            values = [_normal_text(item.get("value", ""))
                      for item in projection.family("semantic_observation")
                      if item.get("manifestation_id") == ref]
            if any(_licensed_wrapper_around(text, value) for value in values):
                matches.append({"basis_ref": ref, "family": "fabric_manifestation"})
    return {"content_bound": bool(matches), "matches": matches}


def verify_evidence_faithfulness(root: Path) -> dict[str, Any]:
    from curunir_identity import verify_all
    from curunir_operational.access import can_view
    from curunir_operational.security import PRIMARY_ID_FIELDS
    from curunir_workbench.auth import ActorRegistry
    from curunir_workbench.projections import MissionProjection
    from curunir_workbench.provenance import evidence_view
    from curunir_workbench.reports import validate_report
    from curunir_workbench.store import WorkbenchStore

    preparation = _read_json(root / "preparation.json")
    mission_id = preparation["mission_id"]
    store = WorkbenchStore(root / "store")
    registry = ActorRegistry(root / "actors.json")
    full_context = registry.context_for_actor(APPROVER_ACTOR)
    projection = MissionProjection(store, full_context)
    findings: list[dict[str, Any]] = []

    def finding(code: str, detail: str, **extra: Any) -> None:
        findings.append({"code": code, "detail": detail, **extra})

    chain = store.verify_chain()
    if not chain["valid"]:
        finding("STORE_CHAIN_INVALID", str(chain))

    manifestations = store.records_of("fabric_manifestation")
    by_manifestation = {item["manifestation_id"]: item for item in manifestations}
    custody_verified = 0
    for item in manifestations:
        digest = item.get("content_sha256", "")
        canonical = root / "custody" / "sha256" / digest[:2] / digest[2:4] / digest
        store_copy = store.payload_dir / digest
        present: list[tuple[str, Path]] = []
        if store_copy.exists() or store_copy.is_symlink():
            present.append(("store", store_copy))
        if canonical.exists() or canonical.is_symlink():
            present.append(("custody", canonical))
        invalid = [label for label, path in present
                   if not path.is_file() or path.is_symlink()
                   or _sha256_file(path) != digest]
        if invalid:
            finding("CUSTODY_COPY_HASH_MISMATCH",
                    "every present custody copy must match the content identity",
                    manifestation_id=item["manifestation_id"], copies=invalid)
        elif not present:
            finding("CUSTODY_HASH_UNAVAILABLE",
                    "no exact hash-verified payload is available",
                    manifestation_id=item["manifestation_id"], sha256=digest)
        else:
            custody_verified += 1
        prior = item.get("prior_manifestation_id")
        if prior:
            prior_item = by_manifestation.get(prior)
            if prior_item is None:
                finding("PRIOR_MANIFESTATION_MISSING", "version predecessor is absent",
                        manifestation_id=item["manifestation_id"], prior=prior)
            elif item.get("retrieval_time", "") <= prior_item.get("retrieval_time", ""):
                finding("TEMPORAL_ORDER_INVALID", "successor does not follow predecessor",
                        manifestation_id=item["manifestation_id"], prior=prior)

    anchor_count = 0
    for observation in store.records_of("semantic_observation"):
        manifestation_id = observation.get("manifestation_id", "")
        if manifestation_id not in by_manifestation:
            finding("OBSERVATION_MANIFESTATION_MISSING", "observation basis is absent",
                    observation_id=observation["observation_id"])
            continue
        view = evidence_view(projection, manifestation_id)
        if view is None or view.get("payload", {}).get("unavailable"):
            finding("EVIDENCE_VIEW_UNAVAILABLE", "exact payload cannot be drilled down",
                    manifestation_id=manifestation_id)
            continue
        for anchor in observation.get("anchors", ()):
            anchor_count += 1
            if anchor.get("manifestation_id") != manifestation_id:
                finding("ANCHOR_IDENTITY_MISMATCH", "anchor names another manifestation",
                        observation_id=observation["observation_id"])
            normalized = anchor.get("normalized_sha256")
            if normalized and not any(
                    payload.get("sha256") == normalized and not payload.get("unavailable")
                    for payload in view.get("normalized_payloads", ())):
                finding("NORMALIZED_ANCHOR_UNAVAILABLE",
                        "anchor's normalized payload cannot be hash verified",
                        observation_id=observation["observation_id"], sha256=normalized)

    reports = _approved_reports(store)
    report_results = []
    content_bound_supported = 0
    non_supported_complete = 0
    for report in reports:
        validation = validate_report(projection, projection.redact(report))
        blocking = [item for item in validation.get("findings", ())
                    if item.get("blocking", True)]
        report_results.append({
            "report_id": report["report_id"],
            "version": report["version"],
            "status": report["status"],
            "validation": validation,
        })
        for item in blocking:
            finding("REPORT_VALIDATION_BLOCKING", item.get("detail", ""),
                    report_id=report["report_id"], report_finding=item)
        sentence_bindings = []
        for section in report.get("sections", ()):
            for sentence in section.get("sentences", ()):
                if sentence.get("status") == "SUPPORTED":
                    binding = _supported_sentence_binding(projection, sentence)
                    sentence_bindings.append({"sentence_id": sentence["sentence_id"], **binding})
                    if binding["content_bound"]:
                        content_bound_supported += 1
                    else:
                        finding("SUPPORTED_TEXT_NOT_CONTENT_BOUND",
                                "settled prose is not the cited statement or asserted "
                                "value with only licensed connective wrapping",
                                report_id=report["report_id"],
                                sentence_id=sentence["sentence_id"])
                elif sentence.get("status") == "EXPLICITLY_INFERENTIAL" \
                        and sentence.get("basis_refs") and sentence.get("inference_note"):
                    non_supported_complete += 1
                elif sentence.get("status") == "UNRESOLVED" \
                        and sentence.get("unresolved_reason"):
                    non_supported_complete += 1
        report_results[-1]["supported_sentence_bindings"] = sentence_bindings
    if not reports:
        finding("FINAL_ARTIFACT_ABSENT", "no approved human report exists")

    m2_semantics = {"applicable": mission_id == "M2_REGULATORY_CORRECTION",
                    "current_n9_content_bound": False,
                    "historical_n4_present": False,
                    "dependent_translation_misused": False}
    if mission_id == "M2_REGULATORY_CORRECTION" and reports:
        prepared = preparation.get("preparation", {})
        current_n9 = prepared.get("current_facility_claim_id", "")
        translation_n4 = prepared.get("dependent_translation_claim_id", "")
        initial_manifestations = prepared.get("manifestations", ())
        english_v1 = initial_manifestations[1] if len(initial_manifestations) >= 2 else ""
        for report in reports:
            for section in report.get("sections", ()):
                for sentence in section.get("sentences", ()):
                    refs = set(sentence.get("basis_refs", ()))
                    normalized = _normal_text(sentence.get("text"))
                    binding = _supported_sentence_binding(projection, sentence)
                    if current_n9 in refs and "bridge n-9" in normalized \
                            and sentence.get("status") == "SUPPORTED" \
                            and binding["content_bound"]:
                        m2_semantics["current_n9_content_bound"] = True
                    if english_v1 in refs and "bridge n-4" in normalized \
                            and sentence.get("status") == "SUPPORTED" \
                            and sentence.get("temporal_scope") == "HISTORICAL" \
                            and binding["content_bound"]:
                        m2_semantics["historical_n4_present"] = True
                    if translation_n4 in refs and (
                            sentence.get("temporal_scope") != "HISTORICAL"
                            or sentence.get("asserts_independent")):
                        m2_semantics["dependent_translation_misused"] = True
        if not m2_semantics["current_n9_content_bound"]:
            finding("M2_CURRENT_N9_NOT_ESTABLISHED",
                    "approved report lacks content-bound current N-9 support")
        if not m2_semantics["historical_n4_present"]:
            finding("M2_HISTORICAL_N4_NOT_ESTABLISHED",
                    "approved report lacks content-bound historical N-4 support from English v1")
        if m2_semantics["dependent_translation_misused"]:
            finding("M2_DEPENDENT_TRANSLATION_MISUSED",
                    "dependent Croatian v1 is presented as current or independent support")

    restricted_leak = {"applicable": mission_id == "M3_RELIEF_COLLABORATION",
                       "leaked_ids": []}
    if mission_id == "M3_RELIEF_COLLABORATION":
        public_context = registry.context_for_actor(PUBLIC_ACTOR)
        public_projection = MissionProjection(store, public_context)
        restricted_ids: set[str] = set()
        for event in store.events():
            record = event["record"]
            id_field = PRIMARY_ID_FIELDS.get(record.get("record_type", ""))
            if id_field and isinstance(record.get(id_field), str) \
                    and record[id_field] in public_projection.hidden_ids:
                restricted_ids.add(record[id_field])
        public_raw = [canonical_line(event["record"]) for event in store.events()
                      if can_view(event["record"].get("marking"), public_context)]
        public_text = "\n".join(public_raw)
        public_tokens = set(re.findall(r"[A-Za-z][A-Za-z0-9_-]{5,}", public_text))
        leaks = []
        for token in sorted(public_tokens):
            if "-" not in token:
                continue
            matches = [identifier for identifier in restricted_ids
                       if identifier.startswith(token)]
            if matches:
                leaks.append({
                    "public_token": token,
                    "restricted_id_sha256": sorted(
                        _sha256_bytes(identifier.encode()) for identifier in matches),
                })
        restricted_leak["leaked_ids"] = leaks
        if leaks:
            finding("RESTRICTED_IDENTIFIER_LEAK",
                    "full or truncated restricted identifiers occur in raw public-marked state",
                    leak_count=len(leaks))

    signatures = verify_all(store)
    if not signatures["all_genuine"]:
        finding("SIGNED_ACTION_INVALID", "one or more retained signatures fail replay",
                signature_verification=signatures)
    if reports and not signatures.get("verdicts"):
        finding("SIGNED_ACTION_ABSENT",
                "approved report exists without any replay-verifiable signed action")

    _, required = _report_sentence_counts(reports)
    complete = content_bound_supported + non_supported_complete
    return {
        "format": "curunir-v6.8-evidence-faithfulness-v2",
        "mission_id": mission_id,
        "status": "PASS" if not findings else "FAIL",
        "store_chain": chain,
        "manifestations_total": len(manifestations),
        "manifestations_custody_verified": custody_verified,
        "anchors_checked": anchor_count,
        "approved_report_results": report_results,
        "lineage_complete_conclusions": complete,
        "lineage_required_conclusions": required,
        "restricted_projection_check": restricted_leak,
        "m2_report_semantics": m2_semantics,
        "signed_actions": signatures,
        "findings": findings,
        "verification_store_head": chain.get("head_hash", ""),
    }


def _measurement(root: Path, faithfulness: Mapping[str, Any], replay: Mapping[str, Any]) -> dict[str, Any]:
    from curunir_workbench.store import WorkbenchStore
    store = WorkbenchStore(root / "store")
    events = _pilot_events(root / PILOT_LOG)
    session_analysis = _session_analysis(events)
    http = session_analysis["bounded_http"] if session_analysis["valid"] else []
    elapsed = sum(item["elapsed_seconds"] for item in session_analysis["intervals"])

    evidence_hashes = {item.get("resource_id_sha256", "")
                       for item in http if item.get("category") == "EVIDENCE_OPENED"
                       and item.get("http_status") == 200
                       and item.get("resource_id_sha256")}
    evidence_ids = {record["manifestation_id"]
                    for record in store.records_of("fabric_manifestation")
                    if _sha256_bytes(record["manifestation_id"].encode()) in evidence_hashes}
    source_ids = {record["source_id"] for record in store.records_of("fabric_manifestation")
                  if record["manifestation_id"] in evidence_ids}
    human_ids = {PRIMARY_ACTOR, APPROVER_ACTOR, PUBLIC_ACTOR}
    human_events = [event for event in store.events() if event.get("actor") in human_ids]
    hypothesis_revisions = sum(
        1 for event in human_events
        if event["record"].get("record_type") == "hypothesis"
        and event["record"].get("version", 1) > 1)
    forecast_revisions = sum(
        1 for event in human_events
        if event["record"].get("record_type") == "analytic_forecast"
        and event["record"].get("version", 1) > 1)
    forecasts: dict[str, list[float]] = {}
    for event in human_events:
        record = event["record"]
        if record.get("record_type") == "analytic_forecast":
            forecasts.setdefault(record["forecast_id"], []).append(record["probability"])
    confidence = [
        {"forecast_id": key, "before": values[0], "after": values[-1],
         "revisions": max(0, len(values) - 1)}
        for key, values in sorted(forecasts.items())
    ]
    reports = _approved_reports(store)
    measured = {
        "mission_status": "COMPLETED" if reports else "NOT_COMPLETED",
        "operator_elapsed_seconds": round(elapsed, 3),
        "system_processing_seconds": round(sum(
            item.get("duration_ms", 0.0) for item in http) / 1000, 3),
        "evidence_items_inspected": len(evidence_ids),
        "sources_used": len(source_ids),
        "collection_actions": sum(1 for item in http
                                  if item.get("category") == "COLLECTION_ACTION"
                                  and item.get("http_status") < 400),
        "alerts_or_warnings_reviewed": len({item.get("path") for item in http
                                            if item.get("category") == "WARNING_REVIEWED"
                                            and item.get("http_status") == 200}),
        "hypothesis_revisions": hypothesis_revisions,
        "forecast_revisions": forecast_revisions,
        "operator_corrections": sum(1 for item in events
                                    if item.get("event_kind") == "OPERATOR_CORRECTION_RECORDED"),
        "system_refusals_or_errors": sum(1 for item in http
                                         if item.get("http_status", 0) >= 400),
        "evidence_lineage_completeness": {
            "complete": faithfulness["lineage_complete_conclusions"],
            "required": faithfulness["lineage_required_conclusions"],
        },
        "replay_success": replay.get("status") == "PASS",
        "final_artifact_completion": len(reports),
        "confidence_before_after_when_authored": confidence,
    }
    return {
        "format": "curunir-v6.8-measurement-v1",
        "mission_id": _read_json(root / "preparation.json")["mission_id"],
        "measured_value": measured,
        "interpretation": {
            "timing_and_counts": "descriptive; no validated performance threshold",
            "evidence_items_inspected": (
                "successful evidence-detail retrievals inside a valid session; "
                "an interaction proxy, not proof of human cognition"),
            "integrity": "lineage, replay, final artifact, and required human transitions are binary gates",
        },
        "criterion": {
            "descriptive_metrics": "REPORT_ONLY",
            "evidence_lineage_completeness": "complete equals required",
            "replay_success": True,
            "final_artifact_completion": "at least one approved report",
        },
    }


@contextlib.contextmanager
def _offline_replay_guard():
    """Deny replay-path egress.  This is not a kernel sandbox.

    Enforced boundaries: socket connect/connect_ex/create_connection,
    subprocess spawn helpers, and AnalyticalAssist.propose.  A caller that
    already holds a connected transport, or that reaches the kernel by
    other means, is outside this guard.
    """
    from unittest import mock
    from curunir_analytic.providers import AnalyticalAssist

    counters = {
        "network_attempts_blocked": 0, "network_calls_completed": 0,
        "model_attempts_blocked": 0, "model_calls_completed": 0,
    }

    def deny_network(*_args, **_kwargs):
        counters["network_attempts_blocked"] += 1
        raise V68Error("network access is denied during V6.8 replay")

    def deny_model(*_args, **_kwargs):
        counters["model_attempts_blocked"] += 1
        raise V68Error("model-provider access is denied during V6.8 replay")

    with mock.patch.object(socket.socket, "connect", deny_network), \
            mock.patch.object(socket.socket, "connect_ex", deny_network), \
            mock.patch.object(socket, "create_connection", deny_network), \
            mock.patch.object(subprocess, "Popen", deny_network), \
            mock.patch.object(subprocess, "run", deny_network), \
            mock.patch.object(subprocess, "call", deny_network), \
            mock.patch.object(subprocess, "check_call", deny_network), \
            mock.patch.object(subprocess, "check_output", deny_network), \
            mock.patch.object(AnalyticalAssist, "propose", deny_model):
        yield counters


def replay_mission(root: Path, destination: Path) -> dict[str, Any]:
    from curunir_identity import verify_all
    from curunir_workbench.store import WorkbenchStore

    if destination.exists() or destination.is_symlink():
        raise V68Error(f"replay destination already exists: {destination}")
    destination.mkdir(parents=True, mode=0o700)
    mission_id = _read_json(root / "preparation.json")["mission_id"]
    with _offline_replay_guard() as isolation:
        original = WorkbenchStore(root / "store")
        export_dir = destination / "store_export"
        export_manifest = original.export_to(export_dir)
        replay_root = destination / "reconstruction"
        replay_root.mkdir(mode=0o700)
        replayed = WorkbenchStore.import_from(export_dir, replay_root / "store")
        if (root / "custody").is_dir():
            shutil.copytree(root / "custody", replay_root / "custody")
        else:
            (replay_root / "custody").mkdir(mode=0o700)

        original_chain = original.verify_chain()
        replay_chain = replayed.verify_chain()
        original_fingerprints = _fingerprints(original, root, mission_id)
        replay_fingerprints = _fingerprints(replayed, root, mission_id)
        signature_result = verify_all(replayed)
    status = "PASS" if (
        original_chain["valid"] and replay_chain["valid"]
        and original_chain["event_count"] == replay_chain["event_count"]
        and original_chain["head_hash"] == replay_chain["head_hash"]
        and original_fingerprints == replay_fingerprints
        and signature_result["all_genuine"]
    ) else "FAIL"
    return {
        "format": "curunir-v6.8-replay-report-v2",
        "mission_id": mission_id,
        "status": status,
        "network_used": isolation["network_calls_completed"] > 0,
        "model_provider_used": isolation["model_calls_completed"] > 0,
        "external_call_isolation": {
            "enforced": True,
            "network_connect_boundaries": [
                "socket.socket.connect", "socket.socket.connect_ex",
                "socket.create_connection"],
            "subprocess_boundaries": [
                "subprocess.Popen", "subprocess.run", "subprocess.call",
                "subprocess.check_call", "subprocess.check_output"],
            "model_provider_boundary": "curunir_analytic.providers.AnalyticalAssist.propose",
            "sandbox": False,
            **isolation,
        },
        "export_manifest": export_manifest,
        "original_chain": original_chain,
        "replay_chain": replay_chain,
        "semantic_identity_definition": (
            "canonical JSON SHA-256 of the authorized operational view, all "
            "V6 family projections, and overview per frozen actor context"
        ),
        "original_projection_fingerprints": original_fingerprints,
        "replay_projection_fingerprints": replay_fingerprints,
        "signed_action_verification": signature_result,
        "verification_store_head": original_chain.get("head_hash", ""),
    }


def _session_analysis(events: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    """Pair ordered, non-overlapping sessions and bind HTTP work to them."""
    open_sessions: dict[tuple[str, str], dict[str, Any]] = {}
    intervals: list[dict[str, Any]] = []
    findings: list[dict[str, Any]] = []
    ordered = list(events)
    for event in ordered:
        kind = event.get("event_kind")
        if kind not in ("SESSION_STARTED", "SESSION_ENDED"):
            continue
        actor = event.get("actor_id", "")
        role = event.get("participant_role", "")
        key = (actor, role)
        try:
            when = datetime.fromisoformat(event["event_time"])
        except (KeyError, TypeError, ValueError):
            findings.append({"code": "SESSION_TIME_INVALID", "seq": event.get("seq")})
            continue
        if ROLE_ACTORS.get(role) != actor:
            findings.append({"code": "SESSION_ROLE_ACTOR_MISMATCH",
                             "seq": event.get("seq"), "actor_id": actor, "role": role})
            continue
        if kind == "SESSION_STARTED":
            if key in open_sessions:
                findings.append({"code": "SESSION_NESTED_START", "seq": event.get("seq")})
            else:
                open_sessions[key] = {"seq": event.get("seq", 0), "time": when}
        elif key not in open_sessions:
            findings.append({"code": "SESSION_END_WITHOUT_START", "seq": event.get("seq")})
        else:
            start = open_sessions.pop(key)
            if when < start["time"]:
                findings.append({"code": "SESSION_TIME_REVERSED", "seq": event.get("seq")})
            else:
                intervals.append({
                    "actor_id": actor, "participant_role": role,
                    "start_seq": start["seq"], "end_seq": event.get("seq", 0),
                    "start_time": start["time"].isoformat(), "end_time": when.isoformat(),
                    "elapsed_seconds": (when - start["time"]).total_seconds(),
                })
    for actor, role in sorted(open_sessions):
        findings.append({"code": "SESSION_START_WITHOUT_END",
                         "actor_id": actor, "role": role})
    completed_roles: dict[str, set[str]] = {}
    for interval in intervals:
        completed_roles.setdefault(interval["actor_id"], set()).add(
            interval["participant_role"])
    bounded_http = []
    unbounded_human_http = []
    for event in ordered:
        if event.get("event_kind") != "HTTP_ACTION":
            continue
        bounded = any(
            interval["actor_id"] == event.get("actor_id")
            and interval["start_seq"] < event.get("seq", 0) < interval["end_seq"]
            for interval in intervals)
        if bounded:
            bounded_http.append(event)
        elif event.get("actor_id") in set(ROLE_ACTORS.values()) \
                and event.get("category") not in ("TASK_END",) \
                and event.get("http_status", 500) < 400:
            unbounded_human_http.append(event)
    if unbounded_human_http:
        findings.append({"code": "HUMAN_HTTP_OUTSIDE_SESSION",
                         "event_seqs": [item.get("seq") for item in unbounded_human_http]})
    return {
        "valid": not findings,
        "intervals": intervals,
        "completed_roles": completed_roles,
        "open_sessions": open_sessions,
        "bounded_http": bounded_http,
        "unbounded_human_http": unbounded_human_http,
        "findings": findings,
    }


def _session_bound_store_events(store_events: Iterable[Mapping[str, Any]],
                                bounded_http: Iterable[Mapping[str, Any]]) \
        -> list[dict[str, Any]]:
    """Return store events attributable to successful in-session requests."""
    events = list(store_events)
    attributable: dict[int, dict[str, Any]] = {}
    for action in bounded_http:
        if action.get("http_status", 500) >= 400:
            continue
        actor = action.get("actor_id")
        before = action.get("store_seq_before", 0)
        after = action.get("store_seq_after", 0)
        if not isinstance(before, int) or not isinstance(after, int) or after <= before:
            continue
        for event in events:
            if before < event.get("seq", 0) <= after and event.get("actor") == actor:
                attributable[event["seq"]] = event
    return [attributable[key] for key in sorted(attributable)]


def assess_mission(root: Path, faithfulness: Mapping[str, Any],
                   replay: Mapping[str, Any]) -> dict[str, Any]:
    from curunir_identity import GENUINE
    from curunir_workbench.store import WorkbenchStore

    _validate_frozen_authority_files()
    mission_id = _read_json(root / "preparation.json")["mission_id"]
    preparation = _read_json(root / "preparation.json")
    store = WorkbenchStore(root / "store")
    log_result = verify_pilot_log(root / PILOT_LOG)
    pilot = _pilot_events(root / PILOT_LOG)
    sessions = _session_analysis(pilot)
    roles = sessions["completed_roles"]
    bounded_http = sessions["bounded_http"] if sessions["valid"] else []
    reports = _approved_reports(store)
    store_events = store.events()
    human_ids = {PRIMARY_ACTOR, APPROVER_ACTOR, PUBLIC_ACTOR}
    human_events = _session_bound_store_events(store_events, bounded_http)
    findings: list[dict[str, Any]] = []
    coverage: dict[str, dict[str, Any]] = {}

    def require(condition: bool, code: str, detail: str, *, capability: str = "") -> None:
        if not condition:
            findings.append({"code": code, "detail": detail})
        if capability:
            coverage[capability] = {
                "mission": mission_id,
                "exact_exercised_path": detail,
                "artifact": "mission_result.json",
                "measured_result": bool(condition),
                "status": "EXERCISED" if condition else "NOT_ESTABLISHED",
            }

    require(log_result["valid"], "PILOT_LOG_INVALID", str(log_result))
    require(sessions["valid"], "PILOT_SESSION_INTERVAL_INVALID",
            str(sessions["findings"]))
    require("PRIMARY_OPERATOR" in roles.get(PRIMARY_ACTOR, set()),
            "PRIMARY_SESSION_INCOMPLETE", "primary operator start/end pair is absent")
    require(any(event["actor"] == PRIMARY_ACTOR for event in human_events),
            "PRIMARY_STORE_ACTION_ABSENT", "no attributable primary-human store mutation")
    require(bool(reports), "APPROVED_FINAL_ARTIFACT_ABSENT",
            "no approved evidence-bound report exists")
    if reports:
        require("APPROVER" in roles.get(APPROVER_ACTOR, set()),
                "APPROVER_SESSION_INCOMPLETE",
                "distinct human approver start/end pair is absent")
        approved_ids = {report["report_id"] for report in reports}
        require(any(
            disposition.get("report_id") in approved_ids
            and disposition.get("actor_id") == APPROVER_ACTOR
            and disposition.get("actor_kind") == "HUMAN"
            and disposition.get("disposition") in ("APPROVED", "APPROVED_WITH_DISSENT")
            for disposition in store.records_of("workbench_report_disposition")
        ), "DISTINCT_HUMAN_APPROVAL_ABSENT",
            "approved output lacks the designated distinct human approver")
    require(faithfulness.get("status") == "PASS", "EVIDENCE_FAITHFULNESS_FAILED",
            "focused evidence and marking verifier must pass")
    require(replay.get("status") == "PASS", "REPLAY_FAILED",
            "clean offline export/import replay must reproduce semantic state",
            capability="export import and replay")
    v7_ledger = _read_json(root / "v7_follow_up.json")
    v7_fields = {"observed_need", "triggering_mission_or_workflow",
                 "why_outside_v6", "candidate_capability_class"}
    require(v7_ledger.get("mission_id") == mission_id
            and all(isinstance(entry, dict)
                    and v7_fields.issubset(entry)
                    and all(isinstance(entry[field], str) and entry[field].strip()
                            for field in v7_fields)
                    for entry in v7_ledger.get("entries", ())),
            "V7_LEDGER_INVALID",
            "V7 follow-up entries must remain explicit and separate from V6.8")
    require(any(item.get("category") == "EVIDENCE_OPENED"
                and item.get("http_status") == 200 for item in bounded_http),
            "NO_EVIDENCE_INSPECTION", "operator did not open exact evidence")

    identity = preparation.get("executable_identity", {})
    current_identity = _repository_identity(require_clean=True)
    require(identity == current_identity, "PREPARATION_EXECUTABLE_IDENTITY_MISMATCH",
            "campaign was not prepared by this exact clean executable and pinned kernel")
    authority = _read_json(root / "qualification_authority.json")
    require(authority in _accepted_authorities(), "QUALIFICATION_AUTHORITY_MISMATCH",
            "campaign authority hashes match neither the frozen repaired contract "
            "nor a superseded authority set ledgered in it")

    record_types = {event["record"].get("record_type") for event in store_events}
    primary_brief = any(item.get("actor_id") == PRIMARY_ACTOR
                        and item.get("path") == "/v68/pilot/brief"
                        and item.get("http_status") == 200 for item in bounded_http)
    require("information_requirement" in record_types and "analyst_task" in record_types
            and primary_brief,
            "MISSION_WORKFLOW_ABSENT", "requirement and task must be retained",
            capability="mission and objectives")
    source_review = any(item.get("actor_id") == PRIMARY_ACTOR
                        and item.get("path") == "/api/family/fabric_source_descriptor"
                        and item.get("http_status") == 200 for item in bounded_http)
    require(bool(store.records_of("fabric_source_descriptor")) and source_review,
            "SOURCE_REGISTRY_ABSENT", "registered source descriptors must be retained",
            capability="source registry and policy")
    evidence_review = any(item.get("actor_id") == PRIMARY_ACTOR
                          and item.get("category") == "EVIDENCE_OPENED"
                          and item.get("http_status") == 200 for item in bounded_http)
    provenance_review = any(item.get("actor_id") in (PRIMARY_ACTOR, APPROVER_ACTOR)
                            and item.get("category") == "PROVENANCE_REVIEWED"
                            and item.get("http_status") == 200 for item in bounded_http)
    require(faithfulness.get("status") == "PASS" and provenance_review,
            "EVIDENCE_DRILLDOWN_NOT_EXERCISED",
            "human must inspect provenance descent and the focused verifier must pass",
            capability="evidence drilldown")
    require(bool(store.records_of("fabric_manifestation")) and evidence_review
            and provenance_review,
            "ACQUISITION_ABSENT", "manifestation and custody path must be exercised",
            capability="acquisition and custody")
    require(bool(store.records_of("semantic_observation")) and bool(store.records_of("semantic_claim")),
            "SEMANTIC_STATE_ABSENT", "observation and claim records must be retained")

    signed = faithfulness.get("signed_actions", {})
    genuine_action_ids = {item["action_id"] for item in signed.get("verdicts", ())
                          if item.get("verdict") == GENUINE}
    approved_ids = {report["report_id"] for report in reports}
    genuine_approvals = [item for item in store.records_of("signed_action")
                         if item.get("action_type") == "approve_report"
                         and item.get("actor_id") == APPROVER_ACTOR
                         and item.get("target_id") in approved_ids
                         and item.get("action_id") in genuine_action_ids]
    require(bool(genuine_approvals), "SIGNED_DISPOSITION_ABSENT",
            "every mission requires the distinct approver's genuine signed approval",
            capability="signed disposition")

    def reviewed_family(family: str, actors: tuple[str, ...] = (PRIMARY_ACTOR,)) -> bool:
        return any(item.get("actor_id") in actors
                   and item.get("path") == f"/api/family/{family}"
                   and item.get("http_status") == 200 for item in bounded_http)

    def reviewed_path(path: str, actors: tuple[str, ...] = (PRIMARY_ACTOR,)) -> bool:
        return any(item.get("actor_id") in actors
                   and item.get("path") == path
                   and item.get("method", "GET") == "GET"
                   and item.get("http_status") == 200 for item in bounded_http)

    if mission_id == "M1_CORPORATE_REGISTRY_CAPSTONE":
        live = [item for item in store.records_of("fabric_manifestation")
                if item.get("connector_id") != "v68-notional-fixture-v1"
                and item.get("http_status") in range(200, 300)]
        require(bool(live), "NO_SUCCESSFUL_LIVE_ACQUISITION",
                "at least one non-fixture HTTP acquisition must be preserved",
                capability="live public web evidence")
        human_manifestation_events = [event for event in human_events
                                      if event["actor"] == PRIMARY_ACTOR
                                      and event["record"].get("record_type") == "fabric_manifestation"]
        require(bool(human_manifestation_events), "NO_POST_COLLECTION_EVIDENCE",
                "primary operator collection did not append a new manifestation")
        update_seq = min((event["seq"] for event in human_manifestation_events), default=10**18)
        hypothesis_events = [event for event in human_events
                             if event["actor"] == PRIMARY_ACTOR
                             and event["record"].get("record_type") == "hypothesis"]
        competing = len({event["record"]["hypothesis_id"]
                         for event in hypothesis_events}) >= 2
        require(competing,
                "COMPETING_HYPOTHESES_ABSENT", "operator must author at least two hypotheses",
                )
        post_hypothesis = any(
            event["seq"] > update_seq and event["record"].get("version", 1) > 1
            for event in hypothesis_events)
        require(post_hypothesis,
                "POST_UPDATE_HYPOTHESIS_REVISION_ABSENT",
                "a hypothesis must be reassessed after new evidence")
        collection_actions = [item for item in bounded_http
                              if item.get("actor_id") == PRIMARY_ACTOR
                              and item.get("category") == "COLLECTION_ACTION"
                              and item.get("http_status", 500) < 400]
        require(competing and post_hypothesis and bool(collection_actions)
                and bool(human_manifestation_events),
                "HYPOTHESIS_COLLECTION_LOOP_INCOMPLETE",
                "human hypotheses, active collection, new evidence, and reassessment must form one loop",
                capability="hypotheses and active collection")
        timeline_review = any(item.get("actor_id") == PRIMARY_ACTOR
                              and item.get("path") == "/api/timeline"
                              and item.get("http_status") == 200
                              for item in bounded_http)
        require(bool(human_manifestation_events) and post_hypothesis and timeline_review,
                "TEMPORAL_UPDATE_NOT_EXERCISED",
                "operator must inspect temporal state and revise after the new evidence arrival",
                capability="semantic world state and temporal change")
        forecasts = [event for event in human_events
                     if event["actor"] == PRIMARY_ACTOR
                     and event["record"].get("record_type") == "analytic_forecast"]
        pre_forecast = any(event["seq"] < update_seq
                           and event["record"].get("version") == 1
                           for event in forecasts)
        require(pre_forecast,
                "PRE_COLLECTION_FORECAST_ABSENT",
                "a human-authored forecast must precede the collection update")
        post_forecast = any(event["seq"] > update_seq
                            and event["record"].get("version", 1) > 1
                            for event in forecasts)
        require(post_forecast,
                "POST_UPDATE_FORECAST_REVISION_ABSENT",
                "a new forecast version must record the post-evidence judgment")
        analytical_context_review = all(reviewed_family(family) for family in (
            "analytic_theme", "analytic_narrative", "stakeholder_assessment", "impact_path"))
        require(pre_forecast and post_forecast and analytical_context_review,
                "ANALYTICAL_CONTEXT_FORECAST_INCOMPLETE",
                "operator must inspect analytic context and author pre/post forecasts",
                capability="analytical context and forecasting")
        human_warning = any(event["actor"] == PRIMARY_ACTOR
                    and event["record"].get("record_type") == "strategic_warning"
                    for event in human_events)
        require(human_warning and bool(reports),
                "WARNING_PROJECTION_ABSENT", "operator must project a named-rule warning",
                capability="alerts warnings and decisions")
        for family in ("analytic_theme", "analytic_narrative",
                       "stakeholder_assessment", "impact_path"):
            require(bool(store.records_of(family)) and reviewed_family(family),
                    f"{family.upper()}_NOT_REVIEWED",
                    f"operator must review retained {family} context")

    elif mission_id == "M2_REGULATORY_CORRECTION":
        fixtures = preparation["preparation"].get("fixture_files", ())
        fixture_ok = len(fixtures) == 4 and all(
            (PACKAGE_ROOT / item["path"]).is_file()
            and _sha256_file(PACKAGE_ROOT / item["path"]) == item["sha256"]
            for item in fixtures)
        opened_hashes = {item.get("resource_id_sha256") for item in bounded_http
                         if item.get("actor_id") == PRIMARY_ACTOR
                         and item.get("category") == "EVIDENCE_OPENED"
                         and item.get("http_status") == 200}
        fixture_opened = all(_sha256_bytes(item["manifestation_id"].encode()) in opened_hashes
                             for item in store.records_of("fabric_manifestation")
                             if item.get("connector_id") == "v68-notional-fixture-v1")
        require(fixture_ok, "FIXTURE_MANIFEST_MISMATCH",
                "all four frozen fixture bytes must retain their recorded hashes")
        require(fixture_opened, "FIXTURE_EVIDENCE_NOT_REVIEWED",
                "primary operator must review all four frozen fixture manifestations",
                capability="structured and unstructured evidence")
        fixture_manifestations = [
            item for item in store.records_of("fabric_manifestation")
            if item.get("connector_id") == "v68-notional-fixture-v1"]
        require(len({item["source_id"] for item in fixture_manifestations}) == 2,
                "SOURCE_IDENTITY_BREADTH_ABSENT",
                "structured and web channels must retain two source identities",
                capability="multiple source identities and dependence")
        affected_claims = [claim for claim in store.current_claims().values()
                           if claim.get("predicate") == "affected_facility"
                           and claim.get("object_or_value") in ("Bridge N-4", "Bridge N-9")]
        shared_groups = set.intersection(*(
            set(claim.get("dependence_group_ids", ())) for claim in affected_claims
        )) if len(affected_claims) >= 2 else set()
        require(bool(shared_groups)
                and all(claim.get("independent_basis_count") == 1
                        for claim in affected_claims),
                "DEPENDENT_DERIVATIVE_MISCLASSIFIED",
                "same-publisher API/web/translation evidence must not inflate independence")
        changes = store.records_of("semantic_change")
        require(any(item.get("attribute") == "affected_facility"
                    and item.get("current_value") == "Bridge N-9"
                    and item.get("change_class") == "SOURCE_CORRECTION"
                    for item in changes),
                "CORRECTION_NOT_PROPAGATED",
                "Bridge N-4 to Bridge N-9 source correction must be explicit")
        translated_id = preparation["preparation"].get("dependent_translation_claim_id")
        current_id = preparation["preparation"].get("current_facility_claim_id")
        claim_states = {item["claim_id"]: item for item in store.records_of("semantic_claim_state")}
        translation_state = claim_states.get(translated_id, {})
        queries = [query for plan in store.records_of("fabric_discovery_plan")
                   for query in plan.get("queries", ())]
        translated_manifestation = preparation["preparation"].get("manifestations", ["", "", ""])[2]
        translated_executions = [item for item in store.records_of("fabric_execution")
                                 if translated_manifestation in item.get("manifestation_ids", ())]
        translated_query_ids = {item["query_id"] for item in translated_executions}
        explicit_derivation = any(
            query.get("query_id") in translated_query_ids
            and preparation["preparation"]["manifestations"][1] in query.get("derived_from", ())
            for query in queries)
        m2_semantics = faithfulness.get("m2_report_semantics", {})
        derivative_state_valid = (
            translation_state.get("state") == "SUPERSEDED"
            and translation_state.get("superseded_by") == current_id
            and explicit_derivation
        )
        require(derivative_state_valid,
                "M2_DERIVATIVE_STATE_INVALID",
                "dependent v1 translation must remain historical and superseded by current N-9")
        require(m2_semantics.get("current_n9_content_bound")
                and m2_semantics.get("historical_n4_present")
                and not m2_semantics.get("dependent_translation_misused"),
                "M2_REPORT_CORRECTION_SEMANTICS_INCOMPLETE",
                "approved report must distinguish current N-9 from historical dependent N-4",
                capability="semantic world state and temporal change")
        hypotheses = {event["record"]["hypothesis_id"] for event in human_events
                      if event["actor"] == PRIMARY_ACTOR
                      and event["record"].get("record_type") == "hypothesis"}
        require(len(hypotheses) >= 2, "COMPETING_HYPOTHESES_ABSENT",
                "operator must record at least two correction hypotheses")
        require(any(event.get("event_kind") == "OPERATOR_CORRECTION_RECORDED"
                    and event.get("actor_id") == PRIMARY_ACTOR
                    and any(interval["actor_id"] == PRIMARY_ACTOR
                            and interval["start_seq"] < event.get("seq", 0) < interval["end_seq"]
                            for interval in sessions["intervals"])
                    for event in pilot),
                "OPERATOR_CORRECTION_ABSENT",
                "operator must explicitly record revision of the initial reading")

    else:
        require("PUBLIC_ACCESS_CHECK" in roles.get(PUBLIC_ACTOR, set()),
                "PUBLIC_ACCESS_SESSION_INCOMPLETE",
                "public-only access-check start/end pair is absent")
        operational_review = all(reviewed_path(path)
                                 for path in M3_OPERATIONAL_REVIEW_PATHS)
        require(len(preparation["preparation"].get("ingestions", ())) >= 10
                and operational_review,
                "HETEROGENEOUS_INGESTION_INCOMPLETE",
                "ten frozen ingestions must be retained and reviewed through "
                "existing overview and activity routes")
        mutation_actors = {event["actor"] for event in human_events}
        require({PRIMARY_ACTOR, APPROVER_ACTOR}.issubset(mutation_actors),
                "COLLABORATION_NOT_ESTABLISHED",
                "both cleared humans must append attributable mission state",
                capability="collaboration and access control")
        require(not any(event["actor"] == PUBLIC_ACTOR for event in store_events),
                "PUBLIC_ACTOR_MUTATED_STORE",
                "read-only public access-check actor must not append mission state")
        public_paths = {item.get("path") for item in bounded_http
                        if item.get("actor_id") == PUBLIC_ACTOR
                        and item.get("method") == "GET"
                        and item.get("http_status") == 200}
        require({"/api/overview", "/api/search", "/api/graph"}.issubset(public_paths),
                "PUBLIC_ACCESS_CHECK_INCOMPLETE",
                "read-only public session must inspect overview, search, and graph")
        annotations = [item for item in store.current_annotations().values()
                       if item.get("kind") == "DISSENT"]
        require(bool(annotations), "DISSENT_ABSENT",
                "a retained dissent annotation is required",
                capability="annotations and dissent")
        warning_reviewed = reviewed_path("/api/overview") \
            or reviewed_family("strategic_warning")
        require((bool(store.records_of("recommendation"))
                 or bool(store.records_of("strategic_warning")))
                and warning_reviewed and bool(reports),
                "WARNING_OR_RECOMMENDATION_ABSENT",
                "mission must retain a warning or recommendation",
                capability="alerts warnings and decisions")
        require(not faithfulness.get("restricted_projection_check", {}).get("leaked_ids"),
                "PUBLIC_PROJECTION_LEAK",
                "public projection must contain no compartmented identifier")
    mission_pass = not findings
    if not mission_pass:
        for row in coverage.values():
            if row["status"] == "EXERCISED":
                row["status"] = "NOT_ESTABLISHED"
                row["measured_result"] = False
                row["exact_exercised_path"] += "; mission did not pass"

    return {
        "format": "curunir-v6.8-mission-result-v2",
        "mission_id": mission_id,
        "status": "PASS" if not findings else "NOT_ACHIEVED",
        "manual_waiver": False,
        "human_participation_recorded": bool(human_events) and sessions["valid"],
        "human_participation_claim_boundary": (
            "Attributable HUMAN registry identities acted inside ordered sessions; "
            "software does not prove physical presence or distinct biological persons."),
        "store_chain": store.verify_chain(),
        "pilot_log": log_result,
        "approved_report_ids": [report["report_id"] for report in reports],
        "coverage": coverage,
        "findings": findings,
        "assessment_store_head": store.verify_chain().get("head_hash", ""),
    }


def _tree_manifest(root: Path, *, exclude: set[str] | None = None) -> list[dict[str, Any]]:
    excluded = exclude or set()
    entries = []
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        relative = str(path.relative_to(root))
        if relative in excluded:
            continue
        entries.append({"path": relative, "bytes": path.stat().st_size,
                        "sha256": _sha256_file(path)})
    return entries


def _audit_state(root: Path) -> dict[str, Any]:
    """Project the package index from the one authoritative retained store."""
    from curunir_operational.security import PRIMARY_ID_FIELDS
    from curunir_workbench.auth import ActorRegistry
    from curunir_workbench.projections import MissionProjection
    from curunir_workbench.store import WorkbenchStore
    from curunir_workbench.views import timeline

    store = WorkbenchStore(root / "store")
    context = ActorRegistry(root / "actors.json").context_for_actor(APPROVER_ACTOR)
    projection = MissionProjection(store, context)

    def histories(family: str, id_field: str) -> list[dict[str, Any]]:
        return [{"id": record[id_field], "versions": projection.versions(
            family, record[id_field])} for record in projection.family(family)]

    human_ids = {PRIMARY_ACTOR, APPROVER_ACTOR, PUBLIC_ACTOR}
    operator_actions = []
    for event in store.events():
        if event["actor"] not in human_ids:
            continue
        record = event["record"]
        id_field = PRIMARY_ID_FIELDS.get(record.get("record_type", ""))
        operator_actions.append({
            "seq": event["seq"], "recorded_time": event["recorded_time"],
            "actor": event["actor"], "event_type": event["event_type"],
            "record_type": record.get("record_type", ""),
            "record_id": record.get(id_field, "") if id_field else "",
            "version": record.get("version"), "status": record.get("status", ""),
        })
    return {
        "format": "curunir-v6.8-audit-state-v1",
        "mission_id": _read_json(root / "preparation.json")["mission_id"],
        "state_token": projection.state_token,
        "store_chain": store.verify_chain(),
        "timeline": timeline(projection, axis="knowledge"),
        "system_state_transitions": projection.family("analytic_transition"),
        "hypothesis_history": histories("hypothesis", "hypothesis_id"),
        "forecast_history": histories("analytic_forecast", "forecast_id"),
        "collection_history": {
            "routes": projection.family("collection_route"),
            "executions": projection.family("fabric_execution"),
        },
        "alerts_and_warnings": {
            "alerts": projection.base_view["alerts"],
            "warnings": projection.family("strategic_warning"),
        },
        "operator_actions": operator_actions,
        "final_outputs": {
            "reports": projection.family("workbench_report"),
            "report_history": histories("workbench_report", "report_id"),
            "dispositions": projection.family("workbench_report_disposition"),
            "signed_actions": store.records_of("signed_action"),
        },
    }


def finalize_mission(root: Path) -> dict[str, Any]:
    """Freeze one append-only mission package after the genuine pilot.

    A failed assessment is still preserved as data, but cannot become a pass.
    The credential registry is intentionally excluded from the package.
    """
    from curunir_workbench.store import WorkbenchStore

    artifacts = root / "artifacts"
    if artifacts.exists() or artifacts.is_symlink():
        raise V68Error(f"refusing to overwrite retained artifacts: {artifacts}")
    stage = Path(tempfile.mkdtemp(prefix=".v68-artifacts.", dir=root))
    try:
        replay = replay_mission(root, stage / "replay_package")
        faithfulness = verify_evidence_faithfulness(root)
        result = assess_mission(root, faithfulness, replay)
        measurement = _measurement(root, faithfulness, replay)
        _write_json(stage / "replay_report.json", replay)
        _write_json(stage / "evidence_faithfulness.json", faithfulness)
        _write_json(stage / "measurement.json", measurement)
        _write_json(stage / "mission_result.json", result)
        _write_json(stage / "audit_state.json", _audit_state(root))
        for filename in (
            "mission_definition.json", "qualification_authority.json",
            "preparation.json", "input_manifest.json", "v7_follow_up.json", PILOT_LOG,
        ):
            source = root / filename
            if source.is_file():
                shutil.copy2(source, stage / filename)

        store = WorkbenchStore(root / "store")
        package = {
            "format": "curunir-v6.8-mission-package-v1",
            "mission_id": result["mission_id"],
            "status": result["status"],
            "manual_waiver": False,
            "store_event_count": result["store_chain"]["event_count"],
            "store_head_hash": result["store_chain"]["head_hash"],
            "approved_reports": [
                {
                    "report_id": report["report_id"],
                    "version": report["version"],
                    "status": report["status"],
                    "record_sha256": sha256(report),
                }
                for report in _approved_reports(store)
            ],
            "actors_registry_packaged": False,
            "actors_registry_exclusion_reason": "contains bearer credentials",
            "created_at": _now(),
        }
        _write_json(stage / "mission_package.json", package)
        manifest = {
            "format": "curunir-v6.8-artifact-manifest-v1",
            "mission_id": result["mission_id"],
            "entries": _tree_manifest(stage, exclude={"artifact_manifest.json"}),
        }
        _write_json(stage / "artifact_manifest.json", manifest)
        os.replace(stage, artifacts)
        return {**package, "artifacts": str(artifacts),
                "artifact_manifest_sha256": _sha256_file(artifacts / "artifact_manifest.json")}
    except BaseException:
        shutil.rmtree(stage, ignore_errors=True)
        raise


def verify_package(root: Path) -> dict[str, Any]:
    artifacts = root / "artifacts"
    findings = []
    if not artifacts.is_dir():
        return {"status": "FAIL", "findings": [{"code": "PACKAGE_MISSING"}]}
    manifest = _read_json(artifacts / "artifact_manifest.json")
    expected = {item["path"]: item for item in manifest.get("entries", ())}
    actual = {item["path"]: item for item in
              _tree_manifest(artifacts, exclude={"artifact_manifest.json"})}
    if expected != actual:
        findings.append({"code": "ARTIFACT_MANIFEST_MISMATCH"})
    package = _read_json(artifacts / "mission_package.json")
    preparation = _read_json(root / "preparation.json")
    if package.get("format") != "curunir-v6.8-mission-package-v1" \
            or package.get("mission_id") != preparation.get("mission_id"):
        findings.append({"code": "PACKAGE_IDENTITY_MISMATCH"})
    if package.get("manual_waiver") is not False \
            or (artifacts / "actors.json").exists() \
            or package.get("actors_registry_packaged") is not False:
        findings.append({"code": "PACKAGE_CREDENTIAL_OR_WAIVER_VIOLATION"})
    from curunir_workbench.store import WorkbenchStore
    chain = WorkbenchStore(root / "store").verify_chain()
    if chain["event_count"] != package.get("store_event_count") \
            or chain["head_hash"] != package.get("store_head_hash"):
        findings.append({"code": "MISSION_MUTATED_AFTER_PACKAGE"})
    result = _read_json(artifacts / "mission_result.json")
    if result.get("status") != package.get("status") \
            or result.get("manual_waiver") is not False:
        findings.append({"code": "PACKAGE_STATUS_MISMATCH"})
    return {"status": "PASS" if not findings else "FAIL",
            "mission_id": package.get("mission_id"), "findings": findings,
            "artifact_manifest_sha256": _sha256_file(artifacts / "artifact_manifest.json")}


def _campaign_coverage(mission_results: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for result in mission_results:
        rows.extend({"capability": capability, **data}
                    for capability, data in sorted(result.get("coverage", {}).items()))
    return rows


def _focused_v68_validation() -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="curunir-v68-focused-") as scratch:
        junit = Path(scratch) / "focused.xml"
        environment = dict(os.environ)
        kernel = KERNEL_PACKAGE
        inherited = environment.get("PYTHONPATH", "")
        environment["PYTHONPATH"] = os.pathsep.join(
            item for item in (str(PACKAGE_ROOT), str(kernel.parent), inherited) if item)
        completed = subprocess.run(
            [sys.executable, "-m", "pytest", "-q", "-c", "pytest.ini",
             "--disable-warnings", f"--junitxml={junit}",
             "tests/test_curunir_v68.py"],
            cwd=PACKAGE_ROOT, env=environment,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        if not junit.is_file():
            return {"status": "FAIL", "exit_code": completed.returncode,
                    "output": completed.stdout[-4000:]}
        suite = ET.parse(junit).getroot().find("testsuite")
        if suite is None:
            return {"status": "FAIL", "exit_code": completed.returncode,
                    "output": "JUnit has no testsuite"}
        result = {
            "tests": int(suite.attrib["tests"]),
            "failures": int(suite.attrib["failures"]),
            "errors": int(suite.attrib["errors"]),
            "skipped": int(suite.attrib["skipped"]),
            "exit_code": completed.returncode,
            "junit_sha256": _sha256_file(junit),
        }
        result["passed"] = result["tests"] - result["failures"] \
            - result["errors"] - result["skipped"]
        result["status"] = "PASS" if completed.returncode == 0 \
            and result["tests"] > 0 and result["skipped"] == 0 else "FAIL"
        if result["status"] != "PASS":
            result["output"] = completed.stdout[-4000:]
        return result


def _v67_report_findings(v67: Mapping[str, Any], executable_sha: str) \
        -> list[dict[str, Any]]:
    from tools.validate_v67 import (
        MAXIMUM_FOCUSED_SKIPS, MAXIMUM_FULL_SKIPS_WITHOUT_POSTGRES,
        MAXIMUM_PRODUCT_SKIPS, MINIMUM_FOCUSED_TESTS, MINIMUM_FULL_TESTS,
        MINIMUM_PRODUCT_TESTS, PRODUCT_BASELINE_DESELECT,
    )
    findings: list[dict[str, Any]] = []

    def add(code: str, detail: Any = "") -> None:
        findings.append({"code": code, "detail": detail})

    if v67.get("format") != "curunir-v6.7-terminal-validation-v1":
        add("V67_REPORT_FORMAT_INVALID", v67.get("format"))
    top_status = v67.get("status")
    if top_status not in ("PASS", "PASSED", "PASS_WITH_ACCEPTED_BASELINE_RESIDUALS"):
        add("V67_REGRESSION_GATE_FAILED", top_status)
    if v67.get("commit") != executable_sha:
        add("VALIDATED_EXECUTABLE_IDENTITY_MISMATCH", v67.get("commit"))

    truth_kernel = _read_json(TRUTH_PATH)["external_kernel"]
    clean = v67.get("clean_reconstruction", {})
    if clean.get("status") != "RECONSTRUCTION_OK" \
            or clean.get("commit") != executable_sha \
            or clean.get("smoke", {}).get("status") != "RECONSTRUCTION_OK":
        add("CLEAN_RECONSTRUCTION_FAILED", clean)
    if clean.get("kernel_tree_sha256") != truth_kernel["tree_sha256"] \
            or clean.get("kernel_python_file_count") != truth_kernel["python_file_count"]:
        add("V67_KERNEL_IDENTITY_MISMATCH", {
            "sha256": clean.get("kernel_tree_sha256"),
            "files": clean.get("kernel_python_file_count")})

    focused = v67.get("focused_v67", {})
    if focused.get("status") != "PASSED" \
            or focused.get("tests", 0) < MINIMUM_FOCUSED_TESTS \
            or focused.get("failed") != 0 or focused.get("errors") != 0 \
            or focused.get("skipped", 10**9) > MAXIMUM_FOCUSED_SKIPS:
        add("V67_FOCUSED_RESULT_INVALID", focused)
    product = v67.get("curunir_product_planes", {})
    if product.get("status") != "PASSED" \
            or product.get("tests", 0) < MINIMUM_PRODUCT_TESTS \
            or product.get("failed") != 0 or product.get("errors") != 0 \
            or product.get("skipped", 10**9) > MAXIMUM_PRODUCT_SKIPS:
        add("V67_PRODUCT_RESULT_INVALID", product)

    baseline = _read_json(PACKAGE_ROOT / "CURUNIR_V6_7_BASELINE_NONPASSING.json")
    if clean.get("requirements_sha256") != baseline["harness"]["requirements_sha256"]:
        add("V67_RECONSTRUCTION_REQUIREMENTS_MISMATCH",
            clean.get("requirements_sha256"))

    def arithmetic_valid(result: Mapping[str, Any]) -> bool:
        fields = ("tests", "passed", "skipped", "failed", "errors")
        return all(type(result.get(field)) is int and result[field] >= 0 for field in fields) \
            and result["tests"] == sum(result[field]
                                       for field in ("passed", "skipped", "failed", "errors"))

    if not arithmetic_valid(focused):
        add("V67_FOCUSED_ARITHMETIC_INVALID")
    if not arithmetic_valid(product):
        add("V67_PRODUCT_ARITHMETIC_INVALID")
    if product.get("deselected_accepted_baseline_nodes") != list(PRODUCT_BASELINE_DESELECT):
        add("V67_PRODUCT_DESELECTION_INVALID")
    full = v67.get("full_repository", {})
    if not arithmetic_valid(full):
        add("V67_FULL_ARITHMETIC_INVALID")
    current = full.get("nonpassing_nodeids", [])
    outcomes = full.get("nonpassing_outcomes", {})
    if not isinstance(current, list) or not all(isinstance(item, str) for item in current):
        current = []
        add("V67_NONPASSING_SET_INVALID")
    current_set = set(current)
    allowed = set(baseline["allowed_nonpassing_nodeids"])
    new_nodes = sorted(current_set - allowed)
    if new_nodes:
        add("V67_NEW_NONPASSING_NODES", new_nodes)
    expected_errors = set(baseline.get("allowed_error_nodeids", ()))
    outcome_changes = sorted(
        node for node, outcome in outcomes.items()
        if node in current_set
        and outcome != ("error" if node in expected_errors else "failure")) \
        if isinstance(outcomes, dict) else ["nonpassing_outcomes is not an object"]
    if not isinstance(outcomes, dict) or set(outcomes) != current_set:
        add("V67_NONPASSING_OUTCOME_SET_INVALID")
    elif full.get("failed") != sum(value == "failure" for value in outcomes.values()) \
            or full.get("errors") != sum(value == "error" for value in outcomes.values()):
        add("V67_NONPASSING_OUTCOME_ARITHMETIC_INVALID")
    if outcome_changes:
        add("V67_OUTCOME_KIND_CHANGES", outcome_changes)
    expected_set_hash = _sha256_bytes(("\n".join(sorted(current_set)) + "\n").encode())
    if full.get("nonpassing_node_set_sha256") != expected_set_hash:
        add("V67_NONPASSING_SET_HASH_MISMATCH")
    if full.get("rewrite_only_nonpassing") != [] \
            or full.get("outcome_kind_changes") != []:
        add("V67_REPORTED_REGRESSION_FIELDS_NONEMPTY")
    if full.get("accepted_baseline_nonpassing_fixed") != len(allowed - current_set):
        add("V67_ACCEPTED_BASELINE_FIXED_COUNT_INVALID")
    if full.get("tests", 0) < MINIMUM_FULL_TESTS \
            or full.get("skipped", 10**9) > MAXIMUM_FULL_SKIPS_WITHOUT_POSTGRES:
        add("V67_FULL_COLLECTION_BOUNDS_FAILED", {
            "tests": full.get("tests"), "skipped": full.get("skipped")})
    expected_full_status = "PASSED" if not current_set \
        else "PASS_WITH_ACCEPTED_BASELINE_RESIDUALS"
    normalized_top = "PASSED" if top_status in ("PASS", "PASSED") else top_status
    if full.get("status") != expected_full_status or normalized_top != expected_full_status:
        add("V67_STATUS_RESIDUAL_MISMATCH", {
            "top": top_status, "full": full.get("status"),
            "expected": expected_full_status})
    return findings


def validate_campaign(campaign_root: Path, v67_report_path: Path,
                      output: Path | None = None) -> dict[str, Any]:
    mission_results = []
    package_results = []
    findings = []
    for mission_id in MISSION_IDS:
        root = campaign_root / mission_id
        try:
            package_check = verify_package(root)
            package_results.append(package_check)
            if package_check["status"] != "PASS":
                findings.append({"code": "MISSION_PACKAGE_INVALID", "mission_id": mission_id,
                                 "detail": package_check})
                continue
            with tempfile.TemporaryDirectory(prefix=f"curunir-v68-terminal-{mission_id}-") as scratch:
                replay = replay_mission(root, Path(scratch) / "replay")
                faithfulness = verify_evidence_faithfulness(root)
                result = assess_mission(root, faithfulness, replay)
                measurement = _measurement(root, faithfulness, replay)
                audit = _audit_state(root)
            derived = {
                "evidence_faithfulness.json": faithfulness,
                "replay_report.json": replay,
                "mission_result.json": result,
                "measurement.json": measurement,
                "audit_state.json": audit,
            }
            mismatches = sorted(
                name for name, value in derived.items()
                if (root / "artifacts" / name).read_bytes() != _json_bytes(value))
            package_results[-1]["live_rederivation"] = {
                "status": "PASS" if not mismatches else "FAIL",
                "byte_mismatches": mismatches,
                "store_head_hash": result["store_chain"]["head_hash"],
            }
            if mismatches:
                findings.append({"code": "PACKAGED_DERIVATION_MISMATCH",
                                 "mission_id": mission_id, "artifacts": mismatches})
            mission_results.append(result)
            if result.get("status") != "PASS" or result.get("manual_waiver") is not False:
                findings.append({"code": "MISSION_NOT_ACHIEVED", "mission_id": mission_id,
                                 "detail": result.get("findings", ())})
        except Exception as exc:
            failure = {"status": "FAIL",
                       "findings": [{"code": "LIVE_REDERIVATION_FAILED",
                                     "detail": f"{type(exc).__name__}: {exc}"}]}
            if package_results and package_results[-1].get("mission_id") == mission_id:
                package_results[-1]["live_rederivation"] = failure
            else:
                package_results.append({"mission_id": mission_id, **failure})
            findings.append({"code": "MISSION_LIVE_REDERIVATION_FAILED",
                             "mission_id": mission_id,
                             "detail": f"{type(exc).__name__}: {exc}"})

    focused_v68 = _focused_v68_validation()
    if focused_v68["status"] != "PASS":
        findings.append({"code": "FOCUSED_V68_FAILED", "detail": focused_v68})

    v67 = _read_json(v67_report_path)
    executable_sha = subprocess.run(
        ["git", "-C", str(REPO_ROOT), "rev-parse", "HEAD"], check=True,
        capture_output=True, text=True).stdout.strip()
    clean = v67.get("clean_reconstruction", {})
    findings.extend(_v67_report_findings(v67, executable_sha))

    coverage = _campaign_coverage(mission_results)
    required = set(_read_json(CONTRACT_PATH)["required_coverage"])
    established = {row["capability"] for row in coverage
                   if row.get("status") == "EXERCISED"
                   and row.get("measured_result") is True}
    missing_coverage = sorted(required - established)
    if missing_coverage:
        findings.append({"code": "REQUIRED_COVERAGE_NOT_ESTABLISHED",
                         "missing": missing_coverage})

    if findings:
        status = "NOT_ACHIEVED"
    elif v67.get("status") == "PASS_WITH_ACCEPTED_BASELINE_RESIDUALS":
        status = "PASS_WITH_ACCEPTED_BASELINE_RESIDUALS"
    else:
        status = "PASS"
    terminal = {
        "format": "curunir-v6.8-terminal-report-v1",
        "status": status,
        "accepted_v6_7_base_sha": "5f0eb4d65986262f36d1d4356c65c3232928b60a",
        "validated_v6_8_executable_sha": executable_sha,
        "qualification_contract_sha256": _sha256_file(CONTRACT_PATH),
        "v7_follow_up_ledger_sha256": _sha256_file(V7_LEDGER_PATH),
        "mission_packages": package_results,
        "coverage_matrix": coverage,
        "required_coverage_missing": missing_coverage,
        "focused_v6_8": focused_v68,
        "v6_7_validation_report": {
            "path": str(v67_report_path), "sha256": _sha256_file(v67_report_path),
            "status": v67.get("status"), "full_repository": v67.get("full_repository"),
            "clean_reconstruction": clean,
        },
        "findings": findings,
        "validated_at": _now(),
    }
    if output is not None:
        if output.exists() or output.is_symlink():
            raise V68Error(f"refusing to overwrite terminal report: {output}")
        _write_json(output, terminal)
    return terminal


def _print_json(value: Any) -> None:
    print(json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False,
                     allow_nan=False))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    prepare = sub.add_parser("prepare", help="prepare one frozen mission")
    prepare.add_argument("--mission", required=True, choices=MISSION_IDS)
    prepare.add_argument("--root", required=True, type=Path)

    prepare_all = sub.add_parser("prepare-all", help="prepare all mission roots")
    prepare_all.add_argument("--campaign-root", required=True, type=Path)

    serve = sub.add_parser("serve", help="serve existing workbench with pilot instrumentation")
    serve.add_argument("--root", required=True, type=Path)
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, required=True)

    check = sub.add_parser("check", help="assess a mission without freezing artifacts")
    check.add_argument("--root", required=True, type=Path)

    finalize = sub.add_parser("finalize", help="freeze canonical package after human pilot")
    finalize.add_argument("--root", required=True, type=Path)

    package = sub.add_parser("verify-package", help="verify retained package and no later mutation")
    package.add_argument("--root", required=True, type=Path)

    terminal = sub.add_parser("terminal", help="validate the three-package V6.8 campaign")
    terminal.add_argument("--campaign-root", required=True, type=Path)
    terminal.add_argument("--v67-report", required=True, type=Path)
    terminal.add_argument("--output", type=Path)

    args = parser.parse_args(argv)
    if args.command == "prepare":
        _print_json(prepare_mission(args.mission, args.root))
        return 0
    if args.command == "prepare-all":
        if args.campaign_root.exists() or args.campaign_root.is_symlink():
            raise V68Error(f"campaign root already exists: {args.campaign_root}")
        args.campaign_root.mkdir(parents=True, mode=0o700)
        results = [prepare_mission(mission, args.campaign_root / mission)
                   for mission in MISSION_IDS]
        _print_json({"campaign_root": str(args.campaign_root), "missions": results})
        return 0
    if args.command == "serve":
        import uvicorn
        uvicorn.run(create_instrumented_app(args.root), host=args.host, port=args.port,
                    log_level="info")
        return 0
    if args.command == "check":
        temporary = Path(tempfile.mkdtemp(prefix="curunir-v68-check-"))
        try:
            replay = replay_mission(args.root, temporary / "replay")
            faithfulness = verify_evidence_faithfulness(args.root)
            result = assess_mission(args.root, faithfulness, replay)
            _print_json({"mission_result": result, "faithfulness": faithfulness,
                         "replay": replay, "measurement": _measurement(
                             args.root, faithfulness, replay)})
            return 0 if result["status"] == "PASS" else 2
        finally:
            shutil.rmtree(temporary, ignore_errors=True)
    if args.command == "finalize":
        result = finalize_mission(args.root)
        _print_json(result)
        return 0 if result["status"] == "PASS" else 2
    if args.command == "verify-package":
        result = verify_package(args.root)
        _print_json(result)
        return 0 if result["status"] == "PASS" else 2
    result = validate_campaign(args.campaign_root, args.v67_report, args.output)
    _print_json(result)
    return 0 if result["status"] != "NOT_ACHIEVED" else 2


if __name__ == "__main__":
    raise SystemExit(main())
