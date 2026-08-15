"""V5.6.1 §6, §8, §15 — custody for the repair panel.

The V5.4 custody implementation is the one that has been attacked, tested and
audited across three milestones, so this module does not re-implement it.  What
it does is give the repair panel its **own seat namespace**: the V5.6 panel sat
in ``REVIEWER_A/B/C`` and its outputs are immutable history, so a repair seat
that reused those identifiers would be a second writer against a name that
already means something.

``REVIEWER_A_REPAIR`` and its two peers are therefore distinct identities with
distinct roots, distinct leases and distinct capability tokens.  The dummy
preflight seats are distinct again, so a credential rehearsed against a throwaway
namespace can never authorise a write into the real panel (§6: *do not reuse
dummy credentials for the real panel*).

The namespace is installed for the duration of a delegated call and removed
afterwards, so importing this module cannot silently widen the V5.4 allowlist:
``REVIEWER_D`` remains unknown to both.
"""

from __future__ import annotations

import os
from collections.abc import Iterable, Mapping, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from ..v5_4 import custody as _c
from ..v5_4.custody import CustodyViolation, GENESIS_RECORD_HASH

#: §8 — the three fresh primary seats.  Fresh identity, not a re-lease of V5.6.
PANEL_SEATS: tuple[str, ...] = (
    "REVIEWER_A_REPAIR", "REVIEWER_B_REPAIR", "REVIEWER_C_REPAIR",
)

#: §6 — the dummy namespace used to rehearse custody before any real packet is
#: reviewed.  Deleted after its audit artifacts are preserved.
PREFLIGHT_SEATS: tuple[str, ...] = (
    "PREFLIGHT_REVIEWER_A", "PREFLIGHT_REVIEWER_B", "PREFLIGHT_REVIEWER_C",
)

V5_6_1_SEAT_IDS: tuple[str, ...] = PANEL_SEATS + PREFLIGHT_SEATS


@contextmanager
def seat_namespace(seat_ids: Sequence[str] = V5_6_1_SEAT_IDS):
    """Run a custody operation under the V5.6.1 seat allowlist.

    Scoped rather than permanent: the V5.4 panel is exactly three seats and a
    module-level widening would make that assertion false everywhere else.
    """
    original = _c.SEAT_IDS
    _c.SEAT_IDS = tuple(seat_ids)
    try:
        yield
    finally:
        _c.SEAT_IDS = original


class RepairSeat:
    """A custody handle over one repair-panel seat root.

    A thin proxy: every operation runs inside :func:`seat_namespace` and then
    delegates to the audited V5.4 implementation.  No custody rule is relaxed,
    reimplemented or bypassed here — the only thing this class changes is which
    seat identifiers are recognised.
    """

    def __init__(self, inner: _c.ReviewerSeat) -> None:
        self._inner = inner

    # -- construction -----------------------------------------------------

    @classmethod
    def open(cls, *, seat_id: str, seat_root: str | Path, run_salt: str,
             session_id: str, assignment_order: Sequence[str],
             process_id: int | None = None) -> "RepairSeat":
        _require_known(seat_id)
        with seat_namespace():
            return cls(_c.ReviewerSeat.open(
                seat_id=seat_id, seat_root=seat_root, run_salt=run_salt,
                session_id=session_id, assignment_order=assignment_order,
                process_id=process_id))

    @classmethod
    def resume(cls, *, seat_id: str, seat_root: str | Path, run_salt: str,
               session_id: str, assignment_order: Sequence[str],
               process_id: int | None = None) -> "RepairSeat":
        _require_known(seat_id)
        with seat_namespace():
            return cls(_c.ReviewerSeat.resume(
                seat_id=seat_id, seat_root=seat_root, run_salt=run_salt,
                session_id=session_id, assignment_order=assignment_order,
                process_id=process_id))

    @classmethod
    def open_or_resume(cls, *, seat_id: str, seat_root: str | Path, run_salt: str,
                       session_id: str, assignment_order: Sequence[str]
                       ) -> "RepairSeat":
        """§8.5 — a new provider process continues the same seat, or opens it.

        The pid is rebound by ``resume``; nothing else is.  Same seat, same
        token, same session, same assignment order, continuing from the last
        valid link.  Earlier records are never rewritten.
        """
        lease = Path(seat_root).expanduser().resolve() / _c.LEASE_FILENAME
        if lease.exists():
            return cls.resume(seat_id=seat_id, seat_root=seat_root,
                              run_salt=run_salt, session_id=session_id,
                              assignment_order=assignment_order)
        return cls.open(seat_id=seat_id, seat_root=seat_root, run_salt=run_salt,
                        session_id=session_id, assignment_order=assignment_order)

    # -- introspection ----------------------------------------------------

    @property
    def seat_id(self) -> str:
        return self._inner.seat_id

    @property
    def seat_root(self) -> Path:
        return self._inner.seat_root

    @property
    def head_hash(self) -> str:
        return self._inner.head_hash

    @property
    def rows(self) -> int:
        return self._inner.rows

    @property
    def finalized(self) -> bool:
        return self._inner.finalized

    @property
    def completed_dossiers(self) -> tuple[str, ...]:
        return self._inner.completed_dossiers

    @property
    def next_dossier_id(self) -> str | None:
        return self._inner.next_dossier_id

    @property
    def next_dossier_index(self) -> int:
        return self._inner.next_dossier_index

    @property
    def assignment_order(self) -> tuple[str, ...]:
        return self._inner.assignment_order

    @property
    def capability_token_hash(self) -> str:
        return self._inner.capability_token_hash

    # -- the write boundary ----------------------------------------------

    def append(self, **kwargs: Any):
        with seat_namespace():
            return self._inner.append(**kwargs)

    def finalize(self, **kwargs: Any) -> dict[str, Any]:
        with seat_namespace():
            return self._inner.finalize(**kwargs)


def _require_known(seat_id: str) -> None:
    if seat_id not in V5_6_1_SEAT_IDS:
        raise CustodyViolation(
            f"unknown V5.6.1 seat: {seat_id!r}; the repair panel is "
            f"{list(PANEL_SEATS)} and the rehearsal namespace is "
            f"{list(PREFLIGHT_SEATS)}")


def derive_repair_token(*, seat_id: str, run_salt: str) -> str:
    _require_known(seat_id)
    with seat_namespace():
        return _c.derive_capability_token(seat_id=seat_id, run_salt=run_salt)


def verify_chain(seat_root: str | Path) -> dict[str, Any]:
    with seat_namespace():
        return _c.verify_chain(seat_root)


def audit_panel(seat_roots: Iterable[str | Path]) -> dict[str, Any]:
    with seat_namespace():
        return _c.audit_panel(seat_roots)


def seat_quality_gate(seat_root: str | Path, **kwargs: Any) -> dict[str, Any]:
    with seat_namespace():
        return _c.seat_quality_gate(seat_root, **kwargs)


def open_repair_panel(*, panel_root: str | Path, run_salt: str, session_id: str,
                      assignments: Mapping[str, Sequence[str]]
                      ) -> dict[str, RepairSeat]:
    """Open all three repair seats under disjoint per-seat roots."""
    missing = set(PANEL_SEATS) - set(assignments)
    if missing:
        raise CustodyViolation(
            f"the repair panel is three seats; missing {sorted(missing)}")
    unknown = set(assignments) - set(PANEL_SEATS)
    if unknown:
        raise CustodyViolation(f"unknown repair seats: {sorted(unknown)}")
    root = Path(panel_root).expanduser().resolve()
    roots = {seat: root / seat.lower() for seat in PANEL_SEATS}
    with seat_namespace():
        _c.assert_disjoint_seat_roots(roots.values())
    return {
        seat: RepairSeat.open(
            seat_id=seat, seat_root=roots[seat], run_salt=run_salt,
            session_id=session_id, assignment_order=assignments[seat])
        for seat in PANEL_SEATS
    }


def salt_path(panel_root: str | Path, seat_id: str) -> Path:
    """Where one seat's run salt lives.

    Deliberately outside every seat root: a salt inside a seat root would be a
    foreign file in a sealed directory, and a salt shared between seats would
    let one seat derive another's capability token.
    """
    _require_known(seat_id)
    return Path(panel_root).expanduser().resolve() / "salts" / f"{seat_id.lower()}.salt"


def write_salt(panel_root: str | Path, seat_id: str, salt: str) -> Path:
    path = salt_path(panel_root, seat_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(salt)
    return path


def read_salt(panel_root: str | Path, seat_id: str) -> str:
    return salt_path(panel_root, seat_id).read_text(encoding="utf-8").strip()


__all__ = [
    "PANEL_SEATS", "PREFLIGHT_SEATS", "V5_6_1_SEAT_IDS", "RepairSeat",
    "CustodyViolation", "GENESIS_RECORD_HASH", "seat_namespace",
    "derive_repair_token", "verify_chain", "audit_panel", "seat_quality_gate",
    "open_repair_panel", "salt_path", "write_salt", "read_salt",
]
