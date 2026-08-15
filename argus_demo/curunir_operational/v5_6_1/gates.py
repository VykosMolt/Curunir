"""V5.6.1 §13, §15, §16 — the gates around development, freezing and validation.

Three rules that are easy to state and easy to break by accident:

* a variant is not selected when the development partition cannot distinguish it;
* candidates freeze before validation opens;
* validation opens exactly once, and nothing is tuned afterwards.

Each is enforced as a refusal rather than a note, because every one of them is a
rule whose violation looks like ordinary diligence at the moment it happens —
"let me just check the validation set", "let me re-run the failures".
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..v5_1.models import now_utc, sha256
from ..write_observation import MEASURED, MEASUREMENT_KEY, CanonicalWriteObserver


class GateViolation(RuntimeError):
    """A development, freeze or validation gate refused."""


# ===========================================================================
# §13.2 — sufficient evidence for variant selection
# ===========================================================================

#: A fold smaller than this cannot carry a stability claim.  V5.5 learned this
#: the expensive way: a singleton source-family fold produced a range of 1.0 and
#: made an unstable variant look perfectly stable.
MIN_FOLD_SIZE = 5

#: Below this many non-defective development units, the comparison is not
#: powered enough to separate three variants.
MIN_DEVELOPMENT_UNITS = 20

#: Units on which the variants actually differ.  If they all produce the same
#: candidate everywhere, there is nothing to select between.
MIN_DISCRIMINATING_UNITS = 10

MIN_MEANINGFUL_FOLDS = 2

#: No single source family may decide the outcome.
MAX_SINGLE_FAMILY_SHARE = 0.5


def sufficient_evidence_for_variant_selection(
        *, nondefective_units: int, discriminating_units: int,
        fold_sizes: Mapping[str, int], family_shares: Mapping[str, float],
        uncertainty_estimated: bool) -> dict[str, Any]:
    """§13.2 — may a variant be selected at all?

    Returns the decision and every reason against it.  A caller that finds
    ``sufficient=False`` records ``INSUFFICIENT_EVIDENCE`` and selects nothing;
    §13.2 is explicit that selecting anyway is not an option.
    """
    reasons: list[str] = []
    meaningful = {name: size for name, size in fold_sizes.items()
                  if size >= MIN_FOLD_SIZE}
    singletons = {name: size for name, size in fold_sizes.items() if size < MIN_FOLD_SIZE}
    if nondefective_units < MIN_DEVELOPMENT_UNITS:
        reasons.append(
            f"only {nondefective_units} non-defective development units; "
            f"{MIN_DEVELOPMENT_UNITS} required")
    if discriminating_units < MIN_DISCRIMINATING_UNITS:
        reasons.append(
            f"only {discriminating_units} units on which the variants differ; "
            f"{MIN_DISCRIMINATING_UNITS} required.  Variants that agree "
            "everywhere cannot be ranked by a comparison")
    if len(meaningful) < MIN_MEANINGFUL_FOLDS:
        reasons.append(
            f"only {len(meaningful)} source-family folds of at least "
            f"{MIN_FOLD_SIZE} units; {MIN_MEANINGFUL_FOLDS} required")
    if not uncertainty_estimated:
        reasons.append("no uncertainty estimate accompanies the point estimates")
    dominant = [name for name, share in family_shares.items()
                if share > MAX_SINGLE_FAMILY_SHARE]
    if dominant:
        reasons.append(
            f"source families {dominant} each account for more than "
            f"{MAX_SINGLE_FAMILY_SHARE:.0%} of the comparison; one family would "
            "decide the outcome")
    return {
        "sufficient": not reasons,
        "reasons_against": reasons,
        "meaningful_folds": meaningful,
        "folds_below_minimum": singletons,
        "no_singleton_fold_stability_claim": not singletons or bool(meaningful),
        "verdict": "SUFFICIENT" if not reasons else "INSUFFICIENT_EVIDENCE",
    }


# ===========================================================================
# §15–§16 — candidate freeze and the single validation opening
# ===========================================================================

@dataclass
class ValidationSeal:
    """Validation labels are sealed until a candidate freeze exists, then opened
    exactly once.

    The counter is the whole mechanism.  A second opening is not a warning; it
    is the difference between a held-out measurement and a tuning loop.
    """

    seal_path: Path
    candidate_freeze_path: Path

    def state(self) -> dict[str, Any]:
        if self.seal_path.exists():
            return json.loads(self.seal_path.read_text())
        return {"sealed": True, "open_events": 0, "opened_time": None,
                "candidate_freeze_hash": None}

    @property
    def sealed(self) -> bool:
        return bool(self.state()["sealed"])

    @property
    def open_events(self) -> int:
        return int(self.state()["open_events"])

    def assert_sealed(self) -> None:
        if not self.sealed:
            raise GateViolation(
                "validation is already open; the implementation context may not "
                "re-enter development with validation labels visible")

    def open(self, *, development_complete: bool,
             validation_packet_hashes: Mapping[str, str]) -> dict[str, Any]:
        """§16 — open validation, once, after the candidate freeze."""
        if not self.candidate_freeze_path.exists():
            raise GateViolation(
                "refusing to open validation: no post-reference candidate freeze "
                "exists.  §16 requires the freeze first, because a freeze written "
                "after a first look is not a freeze")
        if not development_complete:
            raise GateViolation(
                "refusing to open validation: development work is not complete")
        state = self.state()
        if state["open_events"] >= 1:
            raise GateViolation(
                f"validation has already been opened {state['open_events']} time(s); "
                "§16 permits exactly one opening and this would be the second")
        freeze = json.loads(self.candidate_freeze_path.read_text())
        payload = {
            "sealed": False,
            "open_events": state["open_events"] + 1,
            "opened_time": now_utc(),
            "candidate_freeze_hash": freeze.get("integrity_hash"),
            "validation_packet_hashes": dict(validation_packet_hashes),
            "rule": ("no tuning, no variant switching, no threshold change and no "
                     "re-run of errors only, after this point (§16)"),
        }
        self.seal_path.parent.mkdir(parents=True, exist_ok=True)
        self.seal_path.write_text(
            json.dumps(payload, indent=1, sort_keys=True, ensure_ascii=False) + "\n")
        return payload


def assert_no_post_validation_change(*, seal: ValidationSeal,
                                     candidate_freeze_hash_now: str) -> None:
    """§16 — the frozen candidate must be byte-identical after validation opened."""
    state = seal.state()
    if state["open_events"] == 0:
        return
    if state["candidate_freeze_hash"] != candidate_freeze_hash_now:
        raise GateViolation(
            "the candidate freeze changed after validation was opened; that is "
            "tuning on validation, whatever it is called")


# ===========================================================================
# Immutability of what came before
# ===========================================================================

def artifacts_unchanged(manifest: Mapping[str, str], root: Path) -> dict[str, Any]:
    """Every prior-milestone artifact still hashes to what it did."""
    import hashlib
    changed, missing = [], []
    for relative, expected in manifest.items():
        path = root / relative
        if not path.exists():
            missing.append(relative)
            continue
        actual = hashlib.sha256(path.read_bytes()).hexdigest()
        if actual != expected:
            changed.append({"path": relative, "expected": expected, "actual": actual})
    return {"checked": len(manifest), "changed": len(changed), "missing": len(missing),
            "examples": (changed + [{"path": m} for m in missing])[:10],
            "verdict": "PASS" if not changed and not missing else "FAIL"}


def assert_affected_records_readjudicated(
        *, affected_object_ids: Iterable[str],
        reference_records: Sequence[Mapping[str, Any]]) -> None:
    """§11.2 — an affected record may not be reused without fresh adjudication."""
    fresh = {record["object_id"] for record in reference_records
             if record.get("lineage_disposition") != "REUSED_DEPENDENCY_PROVEN"}
    stale = sorted(set(affected_object_ids) - fresh)
    if stale:
        raise GateViolation(
            f"{len(stale)} affected records were reused without re-adjudication: "
            f"{stale[:5]}")


def assert_reuse_has_dependency_proof(
        reuse_records: Sequence[Mapping[str, Any]], *, required_parts: int = 6
        ) -> None:
    """§11.2 — a reused record carries its complete dependency proof."""
    weak = [r.get("object_id") for r in reuse_records
            if len(r.get("proof") or r.get("reuse_proof") or ()) < required_parts]
    if weak:
        raise GateViolation(
            f"{len(weak)} reused records carry fewer than {required_parts} proof "
            f"parts: {weak[:5]}")


# ===========================================================================
# §17 — canonical zero-write, over the port this deployment actually uses
# ===========================================================================

class CanonicalWriteAttempt(RuntimeError):
    """Something tried to reach or write the canonical store."""


#: The port this deployment's canonical store listens on.
#:
#: Declared here rather than imported.  The operational package must not
#: reference canonical database machinery at all — a standing protection with
#: its own test, which scans for the reference as text and not merely as an
#: import — and reaching across that boundary for one integer would breach it
#: for the sake of a number that changes once a deployment.  The value mirrors
#: the deployment DSN; the test that pins the two together lives on the other
#: side of the boundary, where reading the DSN is allowed.
DECLARED_CANONICAL_PORT = 5544


def canonical_dsn_port() -> int:
    """The port the canonical ARGUS database is on, as declared here.

    Deliberately not read from the environment or from any connection string:
    doing either would put deployment-configuration handling inside a package
    that is forbidden to know about the canonical store.  A deployment that
    moves the store passes the new port to :func:`guarded_ports`, from a caller
    that is allowed to read it.
    """
    return DECLARED_CANONICAL_PORT

#: V4's ``ZeroWriteMonitor`` refuses 5432, 5433 and 6432 — the conventional
#: PostgreSQL ports.  This deployment's canonical store is on 5544, which is
#: outside that set, so a connection to the real canonical database would not
#: have been refused by port alone.  V4's module is protected and is not edited
#: here; this guard closes the gap for V5.6.1 by deriving the port from the DSN
#: rather than from a fixed list.
CONVENTIONAL_POSTGRES_PORTS = frozenset({5432, 5433, 6432})


def guarded_ports(extra: Iterable[int] = ()) -> frozenset[int]:
    """Every port a canonical-store connection may not be made to.

    ``extra`` exists for a deployment whose store has moved: the caller reads
    its own configuration and passes the port in, rather than this package
    reaching for it.
    """
    return (CONVENTIONAL_POSTGRES_PORTS | {canonical_dsn_port()}
            | {int(port) for port in extra})


#: WIRING STATUS -- finding W8-N1.  This guard is a LIBRARY CONTROL: nothing in
#: production constructs one.  Its only construction sites in the repository are
#: tests.  That is not a defect to be hidden by installing it somewhere
#: arbitrary -- a guard placed where nothing can trigger it is the same vacuity
#: this workstream is repairing -- but it must not be read as an armed
#: production control either.  So it is declared, and
#: ``tests/test_operational_zero_write_measurement.py`` fails if the declaration
#: and the repository ever disagree, in either direction.
CANONICAL_WRITE_GUARD_WIRING = "NOT_CONSTRUCTED_IN_PRODUCTION_LIBRARY_CONTROL_ONLY"


class CanonicalWriteGuard:
    """Counts and refuses every attempt to reach the canonical store.

    Two different quantities, kept apart on purpose:

    ``canonical_write_attempts``
        attempts this guard REFUSED.  Always was a real counter.
    ``canonical_writes``
        write statements that were actually issued to a guarded port and
        reached the canonical store's client library.  Until W10 this was an
        integer literal ``0`` -- unmeasured, and incapable of being anything
        else.  It is now MEASURED by a statement-level observer armed for the
        guard's lifetime, and is demonstrably escapable (R11): see
        ``tests/test_operational_zero_write_measurement.py``.
    """

    def __init__(self, extra_ports: Iterable[int] = ()) -> None:
        self.attempts: list[dict[str, Any]] = []
        self.ports = guarded_ports(extra_ports)
        self.observer = CanonicalWriteObserver(
            self.ports, label="v5_6_1.CanonicalWriteGuard").activate()

    @property
    def canonical_writes(self) -> int:
        """Canonical write statements observed since this guard was built."""
        return self.observer.canonical_writes

    def refuse_connect(self, address: Any) -> None:
        port = address[1] if isinstance(address, (tuple, list)) and len(address) > 1 else None
        if port in self.ports:
            self.attempts.append({"kind": "CONNECT", "port": port})
            raise CanonicalWriteAttempt(
                f"canonical database connection to port {port} refused")

    def refuse_statement(self, statement: Any) -> None:
        import re
        text = " ".join(str(item) for item in statement) if isinstance(
            statement, (list, tuple)) else str(statement)
        if re.search(r"\b(insert|update|delete|alter|drop|truncate)\b", text,
                     re.IGNORECASE):
            self.attempts.append({"kind": "STATEMENT", "statement": text[:120]})
            raise CanonicalWriteAttempt("canonical write statement refused")

    def report(self) -> dict[str, Any]:
        observed_writes = self.observer.canonical_writes
        return {"canonical_write_attempts": len(self.attempts),
                "canonical_writes": observed_writes,
                MEASUREMENT_KEY: MEASURED,
                "canonical_write_observation": self.observer.observation(),
                "guarded_ports": sorted(self.ports),
                "wiring": CANONICAL_WRITE_GUARD_WIRING,
                "attempts": self.attempts,
                "verdict": ("CANONICAL_WRITE_OBSERVED" if observed_writes
                            else "PASS" if not self.attempts else "REFUSED_AND_COUNTED")}


__all__ = [
    "GateViolation", "CanonicalWriteAttempt", "CanonicalWriteGuard",
    "CANONICAL_WRITE_GUARD_WIRING",
    "canonical_dsn_port", "guarded_ports", "CONVENTIONAL_POSTGRES_PORTS",
    "MIN_FOLD_SIZE", "MIN_DEVELOPMENT_UNITS",
    "MIN_DISCRIMINATING_UNITS", "MIN_MEANINGFUL_FOLDS", "MAX_SINGLE_FAMILY_SHARE",
    "sufficient_evidence_for_variant_selection", "ValidationSeal",
    "assert_no_post_validation_change", "artifacts_unchanged",
    "assert_affected_records_readjudicated", "assert_reuse_has_dependency_proof",
]
