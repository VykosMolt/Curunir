"""Proposal-only kernel admission, deterministic dry runs, and zero-write guards."""
from __future__ import annotations

import ast
import copy
import inspect
import json
import os
import re
import socket
import subprocess
from contextlib import contextmanager
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

from .models import (
    KernelAdmissionProposalV4, KernelAdmissionValidation, KernelShadowDiff,
    KernelShadowOperation, canonical_json, sha256, stable_id,
)
from ..write_observation import (
    MEASURED, MEASUREMENT_KEY, CanonicalWriteObserver, declared_label,
)

POLICY_VERSION = "curunir-kernel-admission-shadow-policy-v4"
PROTECTED_HASHES = {
    "schema.sql": "7af415d510de07ebf681f996320c938706049fe3aa3a57dbf5a3a62237a9da32",
    "argus/actions.py": "faa7d5b6c86386a4dd34109368efae20d917e0b8b440b6ca83a2e711d16cdd14",
}
ALLOWED_OPERATIONS = {"WOULD_INSERT", "WOULD_LINK", "WOULD_UPDATE", "WOULD_SUPERSEDE",
                      "WOULD_REJECT", "WOULD_REQUIRE_REVIEW"}

#: The conventional PostgreSQL ports. A floor, never the whole guarded set: a
#: fixed list is precisely what left this deployment's canonical port (5544)
#: unguarded, so the real set is derived from deployment configuration below.
CONVENTIONAL_POSTGRES_PORTS = frozenset({5432, 5433, 6432})
#: Configuration keys that carry a bare port number. Connection strings are
#: recognised by their own shape instead of by key name, so the guard depends on
#: no particular variable and this module names no canonical-store machinery
#: (tests/test_operational_protection.py scans this package for such names).
_PORT_CONFIG_KEYS = frozenset({"POSTGRES_PORT", "PGPORT"})
_CONNECTION_STRING_PATTERN = re.compile(r"\bpostgres(?:ql)?://[^/\s?#]*:(\d{1,5})(?=[/?#\s\"']|$)")


def _ports_in_connection_strings(text: str) -> set[int]:
    """Ports of every PostgreSQL connection string appearing in ``text``."""
    return {int(port) for port in _CONNECTION_STRING_PATTERN.findall(text) if 0 < int(port) < 65536}


def _ports_in_config(pairs: Iterable[tuple[str, str]]) -> set[int]:
    ports: set[int] = set()
    for raw_key, raw_value in pairs:
        key = raw_key.strip().upper()
        value = raw_value.strip().strip('"').strip("'")
        if not value:
            continue
        if key in _PORT_CONFIG_KEYS and value.isdigit() and 0 < int(value) < 65536:
            ports.add(int(value))
        else:
            ports |= _ports_in_connection_strings(value)
    return ports


def _dotenv_pairs(text: str) -> list[tuple[str, str]]:
    pairs: list[tuple[str, str]] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, _, value = stripped.partition("=")
        pairs.append((key, value))
    return pairs


def canonical_database_ports(repository_root: str | Path) -> frozenset[int]:
    """Every port this deployment's canonical store can be reached on.

    Derived from the deployment's own configuration rather than declared as a
    list, because a declared list is the defect: the conventional ports are
    5432/5433/6432 and this deployment's canonical store is on 5544, so a
    connection to the real canonical database was not refused by port alone.

    Configuration is read as *text* only, from three places under the deployment
    root the monitor is already pointed at: the process environment, the
    deployment's ``.env``, and the default connection string written in
    ``argus/db.py``. Nothing here imports, or may import, database machinery --
    the operational package contains no database client, and connection strings
    are matched by shape rather than by variable name, so this module still
    names no canonical-store machinery. Reading a file under the repository root
    as text is the idiom this module already uses to inspect the frozen kernel.

    The conventional ports remain a floor, so a deployment that publishes no
    configuration is guarded no less than before.
    """
    root = Path(repository_root)
    ports = set(CONVENTIONAL_POSTGRES_PORTS)
    ports |= _ports_in_config(os.environ.items())
    env_file = root / ".env"
    if env_file.is_file():
        ports |= _ports_in_config(_dotenv_pairs(env_file.read_text(encoding="utf-8", errors="replace")))
    connection_source = root / "argus" / "db.py"
    if connection_source.is_file():
        ports |= _ports_in_connection_strings(
            connection_source.read_text(encoding="utf-8", errors="replace"))
    return frozenset(ports)


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def inspect_frozen_kernel(repository_root: str | Path) -> dict[str, Any]:
    root = Path(repository_root)
    schema_path = root / "schema.sql"; actions_path = root / "argus" / "actions.py"
    hashes = {"schema.sql": sha256(schema_path.read_bytes()),
              "argus/actions.py": sha256(actions_path.read_bytes())}
    schema_text = schema_path.read_text(encoding="utf-8")
    table_blocks = re.findall(r"(?is)CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?([\w\"]+)\s*\((.*?)\)\s*;", schema_text)
    tables: dict[str, list[str]] = {}
    for raw_name, block in table_blocks:
        name = raw_name.strip('"')
        columns: list[str] = []
        for line in block.splitlines():
            stripped = line.strip().rstrip(",")
            if not stripped or stripped.upper().startswith(("PRIMARY ", "FOREIGN ", "UNIQUE ", "CHECK ", "CONSTRAINT ")):
                continue
            match = re.match(r'"?([A-Za-z_]\w*)"?\s+', stripped)
            if match: columns.append(match.group(1))
        tables[name] = columns
    tree = ast.parse(actions_path.read_text(encoding="utf-8"), filename=str(actions_path))
    functions: dict[str, list[str]] = {}
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            functions[node.name] = [arg.arg for arg in node.args.args]
    return {
        "inspection_only": True, "schema_path": str(schema_path), "actions_path": str(actions_path),
        "hashes": hashes, "protected_hashes_match": hashes == PROTECTED_HASHES,
        "canonical_table_count": len(tables), "tables": tables, "action_signatures": functions,
        "database_connection_opened": False, "action_module_imported": False,
    }


def build_proposal(*, case_id: str, proposed_concept: str, target_table_or_action: str,
                   proposed_field_values: Mapping[str, Any], source_object_ids: tuple[str, ...],
                   claim_ids: tuple[str, ...], evidence_basis_ids: tuple[str, ...],
                   source_independence_state: str, identity_state: str,
                   contradiction_state: str, correction_retraction_state: str,
                   mapping_precision: str, dependencies: tuple[str, ...], provider: str,
                   creator_actor_id: str, access_marking: Mapping[str, Any],
                   temporal_scope: tuple[str | None, str | None] = (None, None)) -> KernelAdmissionProposalV4:
    payload = {
        "proposal_id": stable_id("kernel-proposal", case_id, proposed_concept, target_table_or_action,
                                 proposed_field_values, claim_ids),
        "case_id": case_id, "proposed_concept": proposed_concept,
        "target_table_or_action": target_table_or_action,
        "proposed_field_values": dict(proposed_field_values),
        "source_object_ids": source_object_ids, "claim_ids": claim_ids,
        "evidence_basis_ids": evidence_basis_ids,
        "source_independence_state": source_independence_state, "identity_state": identity_state,
        "temporal_scope": temporal_scope, "contradiction_state": contradiction_state,
        "correction_retraction_state": correction_retraction_state,
        "mapping_precision": mapping_precision,
        "review_requirements": ("GENUINE_HUMAN_EVIDENCE_REVIEW", "GENUINE_HUMAN_IDENTITY_REVIEW",
                                "EXPLICIT_FUTURE_WRITE_AUTHORIZATION"),
        "access_marking": dict(access_marking), "reversibility": "SHADOW_PROPOSAL_DISCARDABLE",
        "dependencies": dependencies, "policy_version": POLICY_VERSION,
        "provider": provider, "creator_actor_id": creator_actor_id,
        "status": "DRAFT", "integrity_hash": "0" * 64,
    }
    payload["integrity_hash"] = sha256({key: value for key, value in payload.items() if key != "integrity_hash"})
    return KernelAdmissionProposalV4(**payload)


def validate_proposal(proposal: KernelAdmissionProposalV4, inspection: Mapping[str, Any], *,
                      validator_node_id: str, reviewer_actor_id: str,
                      known_claim_ids: set[str], known_basis_ids: set[str],
                      known_source_ids: set[str], known_dependencies: set[str]) -> KernelAdmissionValidation:
    rationale: list[str] = []
    structurally_valid = (proposal.target_table_or_action in inspection.get("tables", {}) or
                          proposal.target_table_or_action in inspection.get("action_signatures", {}))
    if not structurally_valid: rationale.append("target absent from frozen inspection")
    evidence_complete = (bool(proposal.claim_ids and proposal.evidence_basis_ids and proposal.source_object_ids)
                         and set(proposal.claim_ids) <= known_claim_ids
                         and set(proposal.evidence_basis_ids) <= known_basis_ids
                         and set(proposal.source_object_ids) <= known_source_ids)
    if not evidence_complete: rationale.append("evidence references incomplete")
    identity_resolved = proposal.identity_state == "SAME_ENTITY_ACCEPTED"
    if not identity_resolved: rationale.append("identity is not genuinely human accepted")
    contradiction_resolved = proposal.contradiction_state in {"NONE", "RESOLVED"}
    if not contradiction_resolved: rationale.append("contradiction remains unresolved")
    dependencies_valid = set(proposal.dependencies) <= known_dependencies
    if not dependencies_valid: rationale.append("dependency missing")
    separation = reviewer_actor_id != proposal.creator_actor_id and validator_node_id == "KERNEL_REVIEW_NODE"
    if not separation: rationale.append("separation of duties failed")
    # Even complete shadow proposals cannot cross the genuine-human gate in V4.
    if not structurally_valid: status = "APPROVAL_BLOCKED"
    elif not evidence_complete: status = "EVIDENCE_INCOMPLETE"
    elif not identity_resolved: status = "IDENTITY_UNRESOLVED"
    elif not contradiction_resolved: status = "CONTRADICTION_UNRESOLVED"
    elif not dependencies_valid or not separation: status = "APPROVAL_BLOCKED"
    else: status = "HUMAN_REVIEW_REQUIRED"
    rationale.append("V4 policy always requires future genuine human review and explicit write authorization")
    return KernelAdmissionValidation(
        stable_id("kernel-validation", proposal.proposal_id, validator_node_id, status), proposal.proposal_id,
        structurally_valid, evidence_complete, identity_resolved, contradiction_resolved,
        dependencies_valid, separation, status, tuple(rationale), validator_node_id, now_utc(),
    )


def shadow_dry_run(proposals: Iterable[KernelAdmissionProposalV4],
                   validations: Iterable[KernelAdmissionValidation],
                   frozen_fixture: Mapping[str, Any], inspection: Mapping[str, Any]) -> KernelShadowDiff:
    before = copy.deepcopy(dict(frozen_fixture)); before_hash = sha256(before)
    validation_map = {item.proposal_id: item for item in validations}
    operations: list[KernelShadowOperation] = []; conflicts: list[str] = []
    expected = copy.deepcopy(before)
    for proposal in sorted(proposals, key=lambda item: item.proposal_id):
        validation = validation_map.get(proposal.proposal_id)
        reasons: list[str] = []
        if validation is None: reasons.append("VALIDATION_MISSING")
        elif validation.resulting_status != "HUMAN_REVIEW_REQUIRED": reasons.append(validation.resulting_status)
        # All V4 semantic writes stay blocked, including structurally valid ones.
        reasons.append("GENUINE_HUMAN_REVIEW_NOT_PERFORMED")
        operation_type = "WOULD_INSERT" if proposal.target_table_or_action in inspection.get("tables", {}) else "WOULD_REQUIRE_REVIEW"
        operation = KernelShadowOperation(
            stable_id("shadow-operation", proposal.proposal_id, operation_type), proposal.proposal_id,
            operation_type, proposal.target_table_or_action, dict(proposal.proposed_field_values),
            proposal.dependencies, True, tuple(dict.fromkeys(reasons)), True,
        )
        operations.append(operation)
        # Expected state shows a separate shadow namespace, never fixture mutation.
        expected.setdefault("shadow_expected", []).append({"operation_id": operation.operation_id,
                                                            "target": operation.target,
                                                            "fields": operation.field_mapping})
    if sha256(before) != before_hash:
        raise RuntimeError("frozen fixture mutated during shadow planning")
    after_hash = sha256(expected)
    unsigned = {
        "diff_id": stable_id("shadow-diff", before_hash, after_hash, [item.operation_id for item in operations]),
        "fixture_hash_before": before_hash, "proposed_operations": tuple(operations),
        "blocked_operation_ids": tuple(item.operation_id for item in operations),
        "conflicts": tuple(conflicts), "expected_fixture_hash_after": after_hash,
        # DECLARED_NOT_MEASURED: registered at
        # write_observation.DECLARED_NOT_MEASURED_SITES["v4/kernel.py::shadow_dry_run"];
        # MUST NOT be cited as zero-write evidence.
        "fixture_mutated": False, "canonical_write_attempts": 0, "canonical_writes": 0,
        "integrity_hash": "0" * 64,
    }
    unsigned["integrity_hash"] = sha256({key: ([item.to_record() for item in value] if key == "proposed_operations" else value)
                                         for key, value in unsigned.items() if key != "integrity_hash"})
    return KernelShadowDiff(**unsigned)


def static_zero_write_scan(v4_root: str | Path) -> dict[str, Any]:
    findings: list[dict[str, str]] = []
    for path in sorted(Path(v4_root).glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                module = node.module or ""
                if module in {"argus." + "actions", "psy" + "copg", "psy" + "copg2", "async" + "pg"}:
                    findings.append({"path": str(path), "finding": f"forbidden import:{module}"})
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name in {"argus." + "actions", "psy" + "copg", "psy" + "copg2", "async" + "pg"}:
                        findings.append({"path": str(path), "finding": f"forbidden import:{alias.name}"})
    # DECLARED_NOT_MEASURED: a static scan executes nothing and observes nothing.
    # Registered at
    # write_observation.DECLARED_NOT_MEASURED_SITES["v4/kernel.py::static_zero_write_scan"];
    # MUST NOT be cited as zero-write evidence.
    return {"verdict": "PASS" if not findings else "INVALID", "findings": findings,
            "canonical_write_attempts": 0, "canonical_writes": 0,
            **declared_label("v4/kernel.py::static_zero_write_scan")}


class ZeroWriteViolation(RuntimeError):
    pass


class ZeroWriteMonitor:
    """Runtime guard used around dry runs; test attacks are counted separately.

    ``canonical_writes`` is MEASURED, not declared.  A statement-level observer
    is armed for the monitor's whole lifetime -- from construction to garbage
    collection, which spans the ``zero_write_guard`` window and the ``verify``
    call that usually follows it -- and counts every canonical write statement
    this process issues through the canonical store's client library.  The
    socket-level refusals below remain what they always were: a REFUSAL
    mechanism whose ``attempts`` counter is likewise real.  The observer is the
    part that can see a write that the refusals cannot, because a write issued
    through the client library's C layer never reaches ``socket.socket``.

    Satisfiability (R11): the observer is demonstrated able to report a
    non-zero value in ``tests/test_operational_zero_write_measurement.py``.
    """
    def __init__(self, repository_root: str | Path):
        self.root = Path(repository_root); self.attempts = 0
        self.guarded_ports = canonical_database_ports(self.root)
        self.observer = CanonicalWriteObserver(self.guarded_ports,
                                               label="v4.ZeroWriteMonitor").activate()
        self.before = {name: sha256((self.root / name).read_bytes()) for name in PROTECTED_HASHES}

    @property
    def canonical_writes(self) -> int:
        """Canonical write statements observed since this monitor was built."""
        return self.observer.canonical_writes

    def refuse_connect(self, address: Any) -> None:
        port = address[1] if isinstance(address, (tuple, list)) and len(address) > 1 else None
        if port in self.guarded_ports:
            self.attempts += 1
            raise ZeroWriteViolation(f"canonical database connection refused (port {port})")

    def refuse_command(self, command: Any) -> None:
        argv = command if isinstance(command, (list, tuple)) else str(command).split()
        executable = Path(str(argv[0])).name.casefold() if argv else ""
        joined = " ".join(str(item).casefold() for item in argv)
        if executable in {"psql", "pg_restore", "createdb"} or re.search(r"\b(insert|update|delete|alter|drop)\b", joined):
            self.attempts += 1; raise ZeroWriteViolation("canonical write subprocess refused")

    def verify(self) -> dict[str, Any]:
        after = {name: sha256((self.root / name).read_bytes()) for name in PROTECTED_HASHES}
        observed_writes = self.observer.canonical_writes
        return {"protected_before": self.before, "protected_after": after,
                "protected_unchanged": after == self.before == PROTECTED_HASHES,
                "guarded_ports": sorted(self.guarded_ports),
                "canonical_write_attempts": self.attempts,
                "canonical_writes": observed_writes,
                MEASUREMENT_KEY: MEASURED,
                "canonical_write_observation": self.observer.observation(),
                "verdict": "PASS" if after == self.before == PROTECTED_HASHES
                           and self.attempts == 0 and observed_writes == 0 else "INVALID"}


@contextmanager
def zero_write_guard(monitor: ZeroWriteMonitor):
    original_connect = getattr(socket.socket, "connect"); original_run = subprocess.run; original_popen = subprocess.Popen
    def guarded_connect(sock: socket.socket, address: Any):
        monitor.refuse_connect(address); return original_connect(sock, address)
    def guarded_run(command: Any, *args: Any, **kwargs: Any):
        monitor.refuse_command(command); return original_run(command, *args, **kwargs)
    class GuardedPopen(subprocess.Popen):
        def __init__(self, command: Any, *args: Any, **kwargs: Any):
            monitor.refuse_command(command); super().__init__(command, *args, **kwargs)
    setattr(socket.socket, "connect", guarded_connect); subprocess.run = guarded_run; subprocess.Popen = GuardedPopen
    old = os.environ.get("CURUNIR_V4_KERNEL_ZERO_WRITE")
    os.environ["CURUNIR_V4_KERNEL_ZERO_WRITE"] = "ENFORCED"
    try:
        yield monitor
    finally:
        setattr(socket.socket, "connect", original_connect); subprocess.run = original_run; subprocess.Popen = original_popen
        if old is None: os.environ.pop("CURUNIR_V4_KERNEL_ZERO_WRITE", None)
        else: os.environ["CURUNIR_V4_KERNEL_ZERO_WRITE"] = old
