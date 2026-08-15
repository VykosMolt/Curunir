"""Kernel proposal revalidation for V5.1 (contract Section 28).

Every V4/V5 kernel admission proposal is re-checked against the repaired
system state.  A proposal whose upstream dependency (claim, identity,
dependence, source, or report state) was invalidated or retracted can
never re-emerge as UNCHANGED_VALID_SHADOW: the mapping refuses at the
record level, not merely by convention.  Supersession propagates through
proposal-to-proposal dependencies; unknown or uninterpretable repaired
states fail closed to human review, with interpretation failure recorded
as SYSTEM_CAPABILITY_FAILURE via ``models.capability_outcome`` — never a
silent skip.

New shadow proposals built from campaign claim registers run through the
frozen V4 admission path (``validate_proposal`` + ``shadow_dry_run``)
under an active ``ZeroWriteMonitor``; every one ends
HUMAN_REVIEW_REQUIRED / VALIDATED_SHADOW_PROPOSAL_ONLY and a monitor
verdict other than PASS raises.

Composes frozen v4/kernel.py and follows the v5/operations.py
``revalidate_kernel_proposals`` patterns without editing either.
Research shadow only.  Nothing here claims human validation.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

from ..v4.io import read_json, read_jsonl, write_json
from ..write_observation import declared_label
from ..v4.kernel import (
    ZeroWriteMonitor, ZeroWriteViolation, build_proposal, inspect_frozen_kernel,
    shadow_dry_run, validate_proposal, zero_write_guard,
)
from .models import (
    EVIDENCE_LIFECYCLE_STATES, CapabilityOutcome, Record, capability_outcome,
    now_utc, sha256, stable_id,
)

# Repository root that carries the frozen kernel surfaces (schema.sql and
# argus/actions.py); inspection is read-only and deterministic.
_REPO_ROOT = Path(__file__).resolve().parents[2]

PROVIDER = "CURUNIR_V5_1_KERNEL_REGRESSION"
_CREATOR_ACTOR = "CURUNIR_SHADOW_PROPOSAL_BUILDER"
_REVIEWER_ACTOR = "CURUNIR_SHADOW_PROPOSAL_REVIEWER"
_VALIDATOR_NODE = "KERNEL_REVIEW_NODE"

# ---------------------------------------------------------------------------
# Section 28 vocabularies (proposal-revalidation specific; evidence
# lifecycle itself stays owned by models.EVIDENCE_LIFECYCLE_STATES).
# ---------------------------------------------------------------------------

REVALIDATION_DISPOSITIONS = frozenset({
    "REVALIDATION_REQUIRED", "INVALIDATED", "SUPERSEDED",
    "HUMAN_REVIEW_REQUIRED", "UNCHANGED_VALID_SHADOW",
})

# Repaired-system states a dependency lookup may report: the shared
# evidence lifecycle plus explicit validity/invalidity/unknown markers.
REPAIRED_DEPENDENCY_STATES = frozenset(EVIDENCE_LIFECYCLE_STATES) | frozenset({
    "VALID", "REVISED", "INVALIDATED", "UNKNOWN",
})

# States that destroy the upstream evidence: they can never yield an
# unchanged-valid disposition (the Section 28 hard guard).
INVALIDATING_STATES = frozenset({"INVALIDATED", "RETRACTED"})

# States recorded on disposition records; UNINTERPRETABLE marks a lookup
# whose answer could not be understood (always a capability failure).
_RECORDED_STATES = REPAIRED_DEPENDENCY_STATES | {"UNINTERPRETABLE"}

_VALID_STATES = frozenset({"VALID", "CURRENT"})

DEPENDENCY_KINDS = frozenset({
    "CLAIM", "EVIDENCE_BASIS", "SOURCE_OBJECT", "IDENTITY", "DEPENDENCE",
    "REPORT", "PROPOSAL_DEPENDENCY",
})

# Proposal-record fields that carry dependency references, with the kind
# each reference is checked as.  General mechanism: any V4/V5 proposal
# artifact exposing these fields is revalidated the same way.
_DEPENDENCY_FIELDS: tuple[tuple[str, str], ...] = (
    ("claim_ids", "CLAIM"),
    ("evidence_basis_ids", "EVIDENCE_BASIS"),
    ("source_object_ids", "SOURCE_OBJECT"),
    ("identity_proposal_ids", "IDENTITY"),
    ("dependence_relation_ids", "DEPENDENCE"),
    ("report_sentence_ids", "REPORT"),
    ("dependencies", "PROPOSAL_DEPENDENCY"),
)

_STATE_TO_DISPOSITION: Mapping[str, str] = {
    "VALID": "UNCHANGED_VALID_SHADOW", "CURRENT": "UNCHANGED_VALID_SHADOW",
    "UNKNOWN": "HUMAN_REVIEW_REQUIRED", "UNINTERPRETABLE": "HUMAN_REVIEW_REQUIRED",
    "CORRECTED": "REVALIDATION_REQUIRED", "REVISED": "REVALIDATION_REQUIRED",
    "SUPERSEDED": "SUPERSEDED",
    "RETRACTED": "INVALIDATED", "INVALIDATED": "INVALIDATED",
}

_DISPOSITION_SEVERITY: Mapping[str, int] = {
    "UNCHANGED_VALID_SHADOW": 0, "HUMAN_REVIEW_REQUIRED": 1,
    "REVALIDATION_REQUIRED": 2, "SUPERSEDED": 3, "INVALIDATED": 4,
}
_SEVERITY_TO_DISPOSITION = {value: key for key, value in _DISPOSITION_SEVERITY.items()}

# How a proposal-level disposition is recorded as a dependency state on
# the proposals that depend on it (supersession propagation).
_DISPOSITION_TO_STATE: Mapping[str, str] = {
    "UNCHANGED_VALID_SHADOW": "VALID", "HUMAN_REVIEW_REQUIRED": "UNKNOWN",
    "REVALIDATION_REQUIRED": "REVISED", "SUPERSEDED": "SUPERSEDED",
    "INVALIDATED": "INVALIDATED",
}

_KIND_SUBJECT: Mapping[str, str] = {
    "CLAIM": "CLAIM_SUPPORT_RELATION", "EVIDENCE_BASIS": "CLAIM_SUPPORT_RELATION",
    "SOURCE_OBJECT": "SOURCE_ORIGIN_EDGE", "IDENTITY": "ENTITY_IDENTITY",
    "DEPENDENCE": "SOURCE_DEPENDENCE_RELATION", "REPORT": "REPORT_PROPOSITION",
    "PROPOSAL_DEPENDENCY": "CLAIM_SUPPORT_RELATION",
}
_KIND_FAILURE_CLASS: Mapping[str, str] = {
    "IDENTITY": "IDENTITY_EVIDENCE_UNINTERPRETED",
    "DEPENDENCE": "DEPENDENCE_EVIDENCE_UNINTERPRETED",
    "PROPOSAL_DEPENDENCY": "DEPENDENCE_EVIDENCE_UNINTERPRETED",
}

# Validation outcomes the frozen V4 gate may legally produce for a shadow
# proposal; anything else means a proposal escaped the human-review gate.
_BLOCKED_VALIDATION_STATUSES = frozenset({
    "HUMAN_REVIEW_REQUIRED", "APPROVAL_BLOCKED", "EVIDENCE_INCOMPLETE",
    "IDENTITY_UNRESOLVED", "CONTRADICTION_UNRESOLVED",
})

_SHADOW_STATUSES = _BLOCKED_VALIDATION_STATUSES | {"VALIDATED_SHADOW_PROPOSAL_ONLY"}


# ---------------------------------------------------------------------------
# Records
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class DependencyState(Record):
    """One upstream dependency of a proposal, with its repaired state."""

    kind: str
    dependency_id: str
    state: str

    def __post_init__(self) -> None:
        if self.kind not in DEPENDENCY_KINDS:
            raise ValueError(f"unknown dependency kind: {self.kind}")
        if not str(self.dependency_id).strip():
            raise ValueError("dependency reference requires an identifier")
        if self.state not in _RECORDED_STATES:
            raise ValueError(f"unknown repaired dependency state: {self.state}")


@dataclass(frozen=True)
class ProposalRevalidation(Record):
    """Per-proposal Section 28 disposition with the triggering dependency.

    Hard guard: an invalidated or retracted upstream dependency can NEVER
    coexist with UNCHANGED_VALID_SHADOW; construction raises.
    """

    revalidation_id: str
    proposal_id: str
    artifact_path: str
    disposition: str
    dependency_states: tuple[DependencyState, ...]
    triggering_dependency: DependencyState | None
    human_review_required: bool
    capability_failure: bool
    recorded_time: str

    def __post_init__(self) -> None:
        if self.disposition not in REVALIDATION_DISPOSITIONS:
            raise ValueError(f"unknown revalidation disposition: {self.disposition}")
        if not self.human_review_required:
            raise ValueError("V4 policy is preserved: every proposal keeps its "
                             "genuine-human-review requirement")
        invalidated = [item for item in self.dependency_states
                       if item.state in INVALIDATING_STATES]
        if invalidated and self.disposition == "UNCHANGED_VALID_SHADOW":
            raise ValueError(
                "invalidated/retracted upstream dependency can never emerge "
                f"UNCHANGED_VALID_SHADOW (dependency {invalidated[0].dependency_id})")
        if self.disposition == "UNCHANGED_VALID_SHADOW":
            if self.capability_failure:
                raise ValueError("capability failure cannot yield UNCHANGED_VALID_SHADOW")
            if self.triggering_dependency is not None:
                raise ValueError("UNCHANGED_VALID_SHADOW records no triggering dependency")
            if any(item.state not in _VALID_STATES for item in self.dependency_states):
                raise ValueError("UNCHANGED_VALID_SHADOW requires every dependency valid")
        elif self.triggering_dependency is None and self.dependency_states:
            raise ValueError("non-valid disposition must record its triggering dependency")
        if (self.triggering_dependency is not None
                and self.triggering_dependency not in self.dependency_states):
            raise ValueError("triggering dependency must be one of the checked dependencies")


@dataclass(frozen=True)
class ShadowProposalRecord(Record):
    """One campaign-derived concept proposal after the frozen V4 gate.

    Shadow proposals are never admitted: review state is always
    HUMAN_REVIEW_REQUIRED, the operation is always blocked, and any
    attempt to mark a canonical write raises.
    """

    shadow_id: str
    proposal_id: str
    claim_id: str
    case_id: str
    target_table_or_action: str
    validation_status: str
    review_state: str
    shadow_status: str
    blocked: bool
    canonical_write: bool

    def __post_init__(self) -> None:
        if self.review_state != "HUMAN_REVIEW_REQUIRED":
            raise ValueError("shadow proposals always remain HUMAN_REVIEW_REQUIRED")
        if self.validation_status not in _BLOCKED_VALIDATION_STATUSES:
            raise ValueError(f"shadow proposal escaped the human-review gate: "
                             f"{self.validation_status}")
        if self.shadow_status not in _SHADOW_STATUSES:
            raise ValueError(f"forbidden shadow status: {self.shadow_status}")
        if not self.blocked:
            raise ValueError("shadow proposal operations are always blocked")
        if self.canonical_write:
            raise ValueError("shadow proposals are never admitted to canonical state")


# ---------------------------------------------------------------------------
# Artifact discovery (tolerant of absent roots; never silently lossy)
# ---------------------------------------------------------------------------

def _sorted_rglob(root: Path, patterns: Iterable[str]) -> list[Path]:
    found: set[Path] = set()
    for pattern in patterns:
        found.update(path for path in root.rglob(pattern) if path.is_file())
    return sorted(found, key=lambda path: path.relative_to(root).as_posix())


def _proposal_items(path: Path) -> list[Mapping[str, Any]]:
    if path.suffix == ".jsonl":
        payload: Any = read_jsonl(path)
    else:
        payload = read_json(path)
    if isinstance(payload, Mapping) and isinstance(payload.get("proposals"), list):
        payload = payload["proposals"]
    if isinstance(payload, Mapping):
        payload = [payload]
    if not isinstance(payload, list):
        raise ValueError("proposal artifact is neither a list nor a proposal object")
    items: list[Mapping[str, Any]] = []
    for item in payload:
        if not isinstance(item, Mapping) or not str(item.get("proposal_id", "")).strip():
            raise ValueError("proposal record lacks a proposal_id")
        items.append(item)
    return items


def _load_proposal_artifacts(label: str, root: Path,
                             ) -> tuple[list[tuple[str, str, Mapping[str, Any]]],
                                        list[dict[str, str]]]:
    """Discover proposal artifacts under one root; absent roots yield nothing."""
    entries: list[tuple[str, str, Mapping[str, Any]]] = []
    failures: list[dict[str, str]] = []
    if not root.is_dir():
        return entries, failures
    for path in _sorted_rglob(root, ("*proposal*.json", "*proposal*.jsonl")):
        relative = f"{label}/{path.relative_to(root).as_posix()}"
        try:
            for item in _proposal_items(path):
                entries.append((label, relative, item))
        except (ValueError, json.JSONDecodeError, UnicodeDecodeError) as error:
            failures.append({"artifact_path": relative, "detail": str(error)[:300]})
    return entries, failures


def _string_tuple(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, (list, tuple)):
        return tuple(str(item) for item in value)
    return (str(value),)


# ---------------------------------------------------------------------------
# revalidate_prior_proposals — Section 28 core
# ---------------------------------------------------------------------------

def _lookup_state(repaired_signal_fn: Callable[[str, str], str], kind: str,
                  dependency_id: str, proposal_id: str,
                  outcomes: list[CapabilityOutcome]) -> str:
    """Consult the repaired system; uninterpretable answers fail closed."""
    detail = ""
    try:
        state = str(repaired_signal_fn(kind, dependency_id))
        if state in REPAIRED_DEPENDENCY_STATES:
            return state
        detail = f"repaired-state lookup returned unknown state {state!r}"
    except Exception as error:  # noqa: BLE001 - recorded, never swallowed
        detail = f"repaired-state lookup failed: {error}"
    outcomes.append(capability_outcome(
        subject_kind=_KIND_SUBJECT[kind],
        subject_id=f"{proposal_id}:{kind}:{dependency_id}",
        outcome="SYSTEM_CAPABILITY_FAILURE",
        rationale=f"{detail} for {kind} dependency {dependency_id} of proposal "
                  f"{proposal_id}; disposition fails closed to human review",
        capability_failure_class=_KIND_FAILURE_CLASS.get(kind, "SEMANTIC_TYPE_ERROR")))
    return "UNINTERPRETABLE"


def revalidate_prior_proposals(v4_root: str | Path, v5_root: str | Path,
                               repaired_signal_fn: Callable[[str, str], str],
                               output_path: str | Path) -> dict[str, Any]:
    """Re-check every persisted V4/V5 proposal against the repaired system.

    ``repaired_signal_fn(kind, dependency_id)`` must return one of
    ``REPAIRED_DEPENDENCY_STATES`` for the claim/identity/dependence/
    source/report states of the repaired system.  Any other answer is a
    SYSTEM_CAPABILITY_FAILURE and fails closed to human review.
    """
    roots = (("v4", Path(v4_root)), ("v5", Path(v5_root)))
    entries: list[tuple[str, str, Mapping[str, Any]]] = []
    failures: list[dict[str, str]] = []
    for label, root in roots:
        found, bad = _load_proposal_artifacts(label, root)
        entries.extend(found)
        failures.extend(bad)

    outcomes: list[CapabilityOutcome] = []
    for failure in failures:
        outcomes.append(capability_outcome(
            subject_kind="CLAIM_SUPPORT_RELATION",
            subject_id=failure["artifact_path"],
            outcome="SYSTEM_CAPABILITY_FAILURE",
            rationale=f"proposal artifact {failure['artifact_path']} is present but "
                      f"uninterpretable ({failure['detail']}); it is counted, never "
                      "silently skipped",
            capability_failure_class="PARSER_INCAPABILITY"))

    seen: dict[str, tuple[str, Mapping[str, Any]]] = {}
    duplicates: list[str] = []
    for _label, relative, item in entries:
        proposal_id = str(item["proposal_id"])
        if proposal_id in seen:
            duplicates.append(proposal_id)
        else:
            seen[proposal_id] = (relative, item)
    batch_ids = frozenset(seen)

    external: dict[str, list[DependencyState]] = {}
    in_batch: dict[str, list[str]] = {}
    failed_lookup: dict[str, bool] = {}
    for proposal_id in sorted(seen):
        _relative, item = seen[proposal_id]
        deps: list[DependencyState] = []
        internal: list[str] = []
        before = len(outcomes)
        for field_name, kind in _DEPENDENCY_FIELDS:
            for reference in _string_tuple(item.get(field_name)):
                if kind == "PROPOSAL_DEPENDENCY" and reference in batch_ids:
                    internal.append(reference)
                    continue
                state = _lookup_state(repaired_signal_fn, kind, reference,
                                      proposal_id, outcomes)
                deps.append(DependencyState(kind, reference, state))
        external[proposal_id] = deps
        in_batch[proposal_id] = sorted(dict.fromkeys(internal))
        failed_lookup[proposal_id] = len(outcomes) > before

    # Base severity from the repaired-state lookups.
    severity: dict[str, int] = {}
    trigger: dict[str, DependencyState | None] = {}
    for proposal_id in sorted(seen):
        best, chosen = 0, None
        for dep in external[proposal_id]:
            rank = _DISPOSITION_SEVERITY[_STATE_TO_DISPOSITION[dep.state]]
            if rank > best:
                best, chosen = rank, dep
        if not external[proposal_id] and not in_batch[proposal_id]:
            best = max(best, _DISPOSITION_SEVERITY["HUMAN_REVIEW_REQUIRED"])
        severity[proposal_id] = best
        trigger[proposal_id] = chosen

    # Supersession/invalidation propagates through proposal dependencies
    # until a fixpoint (severity only ever increases, so this terminates).
    changed = True
    while changed:
        changed = False
        for proposal_id in sorted(seen):
            for upstream in in_batch[proposal_id]:
                rank = severity[upstream]
                if rank > severity[proposal_id]:
                    severity[proposal_id] = rank
                    trigger[proposal_id] = DependencyState(
                        "PROPOSAL_DEPENDENCY", upstream,
                        _DISPOSITION_TO_STATE[_SEVERITY_TO_DISPOSITION[rank]])
                    changed = True

    records: list[ProposalRevalidation] = []
    for proposal_id in sorted(seen):
        relative, _item = seen[proposal_id]
        disposition = _SEVERITY_TO_DISPOSITION[severity[proposal_id]]
        deps = tuple(external[proposal_id]) + tuple(
            DependencyState("PROPOSAL_DEPENDENCY", upstream,
                            _DISPOSITION_TO_STATE[_SEVERITY_TO_DISPOSITION[severity[upstream]]])
            for upstream in in_batch[proposal_id])
        records.append(ProposalRevalidation(
            stable_id("proposal-revalidation", proposal_id, disposition,
                      [dep.to_record() for dep in deps]),
            proposal_id, relative, disposition, deps, trigger[proposal_id],
            True, failed_lookup[proposal_id], now_utc()))

    counts = {name: 0 for name in sorted(REVALIDATION_DISPOSITIONS)}
    for record in records:
        counts[record.disposition] += 1

    stable = {
        "artifacts_discovered": len({relative for _l, relative, _i in entries} |
                                    {f["artifact_path"] for f in failures}),
        "proposals_examined": len(records),
        "duplicate_proposal_ids": sorted(dict.fromkeys(duplicates)),
        "disposition_counts": counts,
        "dispositions": [_without(record.to_record(), "recorded_time")
                         for record in records],
        "uninterpretable_artifacts": failures,
        "capability_outcomes": [_without(item.to_record(), "recorded_time")
                                for item in outcomes],
        "empty": not records and not failures,
        "canonical_write_attempts": 0, "canonical_writes": 0,
        **declared_label("v5_1/kernel_regression.py::revalidate_prior_proposals"),
    }
    report = dict(stable)
    report["integrity_hash"] = sha256(stable)
    report["artifact_roots"] = {label: str(root) for label, root in roots}
    report["generated_time"] = now_utc()
    report["verdict"] = ("PASS_EMPTY" if stable["empty"] else
                         "PASS_WITH_CAPABILITY_FAILURES" if outcomes else "PASS")
    write_json(output_path, report, refuse_existing=True)
    return report


def _without(record: Mapping[str, Any], *keys: str) -> dict[str, Any]:
    return {key: value for key, value in record.items() if key not in keys}


# ---------------------------------------------------------------------------
# shadow_proposals_from_campaign — concept proposals through the frozen gate
# ---------------------------------------------------------------------------

def _load_claim_registers(root: Path) -> tuple[list[Mapping[str, Any]], list[str],
                                               list[dict[str, str]]]:
    claims: list[Mapping[str, Any]] = []
    registers: list[str] = []
    failures: list[dict[str, str]] = []
    if not root.is_dir():
        return claims, registers, failures
    for path in _sorted_rglob(root, ("claim_graph.json", "*claim_register*.json",
                                     "*claim_register*.jsonl")):
        relative = path.relative_to(root).as_posix()
        registers.append(relative)
        try:
            payload: Any = read_jsonl(path) if path.suffix == ".jsonl" else read_json(path)
            if isinstance(payload, Mapping) and isinstance(payload.get("claims"), list):
                payload = payload["claims"]
            if not isinstance(payload, list):
                raise ValueError("claim register is neither a list nor a claim graph")
            for item in payload:
                if (not isinstance(item, Mapping)
                        or not str(item.get("claim_id", "")).strip()
                        or not str(item.get("case_id", "")).strip()):
                    raise ValueError("claim record lacks claim_id/case_id")
                claims.append(item)
        except (ValueError, json.JSONDecodeError, UnicodeDecodeError) as error:
            failures.append({"artifact_path": relative, "detail": str(error)[:300]})
    return claims, registers, failures


def _load_evidence_bases(root: Path) -> dict[str, Mapping[str, Any]]:
    bases: dict[str, Mapping[str, Any]] = {}
    if not root.is_dir():
        return bases
    for path in _sorted_rglob(root, ("evidence_basis_register.json",
                                     "*evidence_basis*.jsonl")):
        payload: Any = read_jsonl(path) if path.suffix == ".jsonl" else read_json(path)
        if isinstance(payload, list):
            for item in payload:
                if isinstance(item, Mapping) and str(item.get("basis_id", "")).strip():
                    bases.setdefault(str(item["basis_id"]), item)
    return bases


def _concept_target(inspection: Mapping[str, Any]) -> str:
    tables = sorted(inspection.get("tables", {}))
    if not tables:
        raise ValueError("frozen inspection exposes no tables for concept proposals")
    claim_tables = [name for name in tables if "claim" in name.casefold()]
    return min(claim_tables, key=lambda name: (len(name), name)) if claim_tables else tables[0]


def _proposal_from_claim(claim: Mapping[str, Any], target: str,
                         bases: Mapping[str, Mapping[str, Any]]) -> Any:
    claim_id = str(claim["claim_id"])
    epistemic = str(claim.get("epistemic_state", "UNKNOWN"))
    basis_ids = tuple(sorted(_string_tuple(claim.get("evidence_basis_ids"))))
    source_ids = set(_string_tuple(claim.get("source_object_ids")))
    for basis_id in basis_ids:
        source_ids.update(_string_tuple(bases.get(basis_id, {}).get("source_object_ids")))
    field_values = {"claim_id": claim_id}
    for name in ("normalized_statement", "subject", "predicate", "object_or_value",
                 "modality", "polarity", "epistemic_state"):
        if str(claim.get(name, "")).strip():
            field_values[name] = str(claim[name])
    temporal = _string_tuple(claim.get("temporal_scope")) or (None, None)
    temporal_scope = (temporal[0] if len(temporal) > 0 else None,
                      temporal[1] if len(temporal) > 1 else None)
    return build_proposal(
        case_id=str(claim["case_id"]),
        proposed_concept=str(claim.get("normalized_statement")
                             or claim.get("predicate") or claim_id),
        target_table_or_action=target,
        proposed_field_values=field_values,
        source_object_ids=tuple(sorted(source_ids)),
        claim_ids=(claim_id,),
        evidence_basis_ids=basis_ids,
        source_independence_state=str(claim.get("independence_state")
                                      or "INDEPENDENCE_UNKNOWN"),
        identity_state=str(claim.get("identity_state") or "SAME_ENTITY_PROPOSED"),
        contradiction_state=("UNRESOLVED" if epistemic in {"CONTRADICTED", "DISPUTED"}
                             else "NONE"),
        correction_retraction_state=("RETRACTED" if epistemic == "RETRACTED"
                                     else str(claim.get("correction_state") or "CURRENT")),
        mapping_precision=str(claim.get("mapping_precision") or "APPROXIMATE_SECTION"),
        dependencies=tuple(sorted(_string_tuple(claim.get("dependencies")))),
        provider=PROVIDER, creator_actor_id=_CREATOR_ACTOR,
        access_marking=dict(claim.get("access_marking")
                            or {"releasability": ["RESEARCH_SHADOW_ONLY"]}),
        temporal_scope=temporal_scope)


def shadow_proposals_from_campaign(campaign_analysis_root: str | Path,
                                   output_path: str | Path, *,
                                   repository_root: str | Path | None = None,
                                   ) -> dict[str, Any]:
    """Build concept-only shadow proposals from campaign claim registers.

    Every proposal runs through the frozen V4 gate under an active
    ``ZeroWriteMonitor``; all end HUMAN_REVIEW_REQUIRED /
    VALIDATED_SHADOW_PROPOSAL_ONLY, and a monitor verdict other than
    PASS raises ``ZeroWriteViolation``.
    """
    root = Path(campaign_analysis_root)
    repo = Path(repository_root) if repository_root is not None else _REPO_ROOT
    claims, registers, failures = _load_claim_registers(root)
    bases = _load_evidence_bases(root)

    outcomes: list[CapabilityOutcome] = []
    for failure in failures:
        outcomes.append(capability_outcome(
            subject_kind="CLAIM_SUPPORT_RELATION",
            subject_id=failure["artifact_path"],
            outcome="SYSTEM_CAPABILITY_FAILURE",
            rationale=f"claim register {failure['artifact_path']} is present but "
                      f"uninterpretable ({failure['detail']}); no shadow proposal is "
                      "built from evidence the system cannot interpret",
            capability_failure_class="PARSER_INCAPABILITY"))

    inspection = inspect_frozen_kernel(repo)
    monitor = ZeroWriteMonitor(repo)
    target = _concept_target(inspection)

    ordered = sorted(claims, key=lambda item: str(item["claim_id"]))
    proposals = [_proposal_from_claim(claim, target, bases) for claim in ordered]
    known_claims = {str(item["claim_id"]) for item in ordered}
    known_bases = set(bases)
    known_sources: set[str] = set()
    for basis in bases.values():
        known_sources.update(_string_tuple(basis.get("source_object_ids")))
    for claim in ordered:
        known_sources.update(_string_tuple(claim.get("source_object_ids")))
    known_dependencies = known_claims | known_bases | known_sources

    with zero_write_guard(monitor):
        validations = [validate_proposal(
            proposal, inspection, validator_node_id=_VALIDATOR_NODE,
            reviewer_actor_id=_REVIEWER_ACTOR, known_claim_ids=known_claims,
            known_basis_ids=known_bases, known_source_ids=known_sources,
            known_dependencies=known_dependencies) for proposal in proposals]
        diff = shadow_dry_run(proposals, validations, {}, inspection)

    attestation = monitor.verify()
    if attestation["verdict"] != "PASS":
        raise ZeroWriteViolation(
            f"zero-write monitor verdict {attestation['verdict']}: shadow proposal "
            "construction is forbidden from touching canonical state")
    if any(not operation.blocked for operation in diff.proposed_operations):
        raise ZeroWriteViolation("unblocked shadow operation in dry run")

    shadow_records: list[ShadowProposalRecord] = []
    for claim, proposal, validation in zip(ordered, proposals, validations):
        status = validation.resulting_status
        if status not in _BLOCKED_VALIDATION_STATUSES:
            raise ValueError(f"shadow proposal escaped the human-review gate: {status}")
        shadow_records.append(ShadowProposalRecord(
            stable_id("shadow-proposal", proposal.proposal_id, status),
            proposal.proposal_id, str(claim["claim_id"]), str(claim["case_id"]),
            target, status, "HUMAN_REVIEW_REQUIRED",
            "VALIDATED_SHADOW_PROPOSAL_ONLY" if status == "HUMAN_REVIEW_REQUIRED"
            else status, True, False))
    shadow_records.sort(key=lambda record: record.proposal_id)

    stable = {
        "registers": registers,
        "claims_examined": len(ordered),
        "proposals_built": len(proposals),
        "shadow_proposals": [record.to_record() for record in shadow_records],
        "shadow_diff_hash": diff.integrity_hash,
        "blocked_operations": len(diff.blocked_operation_ids),
        "capability_outcomes": [_without(item.to_record(), "recorded_time")
                                for item in outcomes],
        "empty": not shadow_records and not failures,
        "admitted": 0,
        "zero_write": {
            "canonical_write_attempts": attestation["canonical_write_attempts"],
            "canonical_writes": attestation["canonical_writes"],
            "protected_unchanged": attestation["protected_unchanged"],
            "verdict": attestation["verdict"],
        },
    }
    report = dict(stable)
    report["integrity_hash"] = sha256(stable)
    report["campaign_analysis_root"] = str(root)
    report["proposal_records"] = [proposal.to_record() for proposal in proposals]
    report["validations"] = [_without(validation.to_record(), "validated_time")
                             for validation in validations]
    report["generated_time"] = now_utc()
    report["verdict"] = ("PASS_EMPTY" if stable["empty"] else
                         "PASS_WITH_CAPABILITY_FAILURES" if outcomes else "PASS")
    write_json(output_path, report, refuse_existing=True)
    return report


# ---------------------------------------------------------------------------
# regression_report — Section 28 rollup
# ---------------------------------------------------------------------------

def _as_mapping(value: Mapping[str, Any] | str | Path) -> Mapping[str, Any]:
    if isinstance(value, Mapping):
        return value
    payload = read_json(value)
    if not isinstance(payload, Mapping):
        raise ValueError("regression input artifact is not a report object")
    return payload


def regression_report(revalidation: Mapping[str, Any] | str | Path,
                      shadow: Mapping[str, Any] | str | Path | None,
                      output_path: str | Path) -> dict[str, Any]:
    """Counts by disposition, zero-write attestation, deterministic hash."""
    reval = _as_mapping(revalidation)
    counts = {name: 0 for name in sorted(REVALIDATION_DISPOSITIONS)}
    for record in reval.get("dispositions", ()):
        disposition = str(record.get("disposition", ""))
        if disposition not in REVALIDATION_DISPOSITIONS:
            raise ValueError(f"unknown disposition in revalidation input: {disposition}")
        counts[disposition] += 1
    declared = reval.get("disposition_counts")
    if declared is not None and dict(declared) != counts:
        raise ValueError("revalidation disposition counts are inconsistent with records")

    shadow_payload = _as_mapping(shadow) if shadow is not None else None
    shadow_zero_write = (dict(shadow_payload.get("zero_write", {}))
                         if shadow_payload is not None else None)
    if shadow_payload is not None:
        verdict = str(shadow_zero_write.get("verdict", "MISSING"))
        if verdict != "PASS":
            raise ZeroWriteViolation(
                f"shadow attestation verdict {verdict}: regression report refuses "
                "to attest a non-zero-write shadow run")
        if shadow_payload.get("admitted", 0) != 0:
            raise ValueError("shadow report claims admitted proposals")

    capability_failures = (len(reval.get("capability_outcomes", ())) +
                           (len(shadow_payload.get("capability_outcomes", ()))
                            if shadow_payload is not None else 0))
    stable = {
        "disposition_counts": counts,
        "proposals_examined": int(reval.get("proposals_examined", 0)),
        "shadow_proposals_built": (int(shadow_payload.get("proposals_built", 0))
                                   if shadow_payload is not None else 0),
        "capability_failures": capability_failures,
        "zero_write_attestation": {
            "revalidation": {"canonical_write_attempts": 0, "canonical_writes": 0,
                             **declared_label("v5_1/kernel_regression.py::regression_report")},
            "shadow": shadow_zero_write if shadow_zero_write is not None else "NOT_RUN",
        },
        "component_hashes": {
            "revalidation": str(reval.get("integrity_hash", "")),
            "shadow": (str(shadow_payload.get("integrity_hash", ""))
                       if shadow_payload is not None else None),
        },
        "human_review_required": True,
    }
    report = dict(stable)
    report["integrity_hash"] = sha256(stable)
    report["generated_time"] = now_utc()
    report["verdict"] = "PASS_WITH_CAPABILITY_FAILURES" if capability_failures else "PASS"
    write_json(output_path, report, refuse_existing=True)
    return report


def kernel_regression(**spec: Any) -> dict[str, Any]:
    """CLI wrapper (name fixed by cli.FORWARDED_COMMANDS)."""
    states = dict(spec.get("dependency_states") or {})
    default_state = spec.get("default_dependency_state", "UNCHANGED")

    def repaired_signal(kind: str, dependency_id: str) -> str:
        return states.get(dependency_id, states.get(kind, default_state))

    revalidation = revalidate_prior_proposals(
        spec["v4_root"], spec["v5_root"], repaired_signal,
        spec["revalidation_output"])
    shadow = None
    if spec.get("campaign_analysis_root"):
        shadow = shadow_proposals_from_campaign(
            spec["campaign_analysis_root"], spec["shadow_output"],
            repository_root=spec.get("repository_root"))
    return regression_report(revalidation, shadow, spec["report_output"])
