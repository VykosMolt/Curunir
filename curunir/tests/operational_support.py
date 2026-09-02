"""Shared fixtures for curunir_operational tests (no DB, no network)."""
from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone

from curunir_operational.access import AccessContext, Marking
from curunir_operational.store import MissionDataStore

T0 = "2026-03-01T06:00:00+00:00"

BASE_MARKING = Marking(owning_authority="CIVDEF-AUTH", releasability=("CORRIDOR-OPS",))
RESTRICTED_MARKING = Marking(owning_authority="CIVDEF-AUTH", compartments=("SENSITIVE-INFRA",),
                             releasability=("CORRIDOR-OPS",), min_role="ANALYST")

HIGH_CONTEXT = AccessContext("ctx-high", "analyst-vale", "HUMAN", ("ANALYST", "SUPERVISOR"),
                             ("SENSITIVE-INFRA",), ("CORRIDOR-OPS",), "CIVDEF-AUTH")
LOW_CONTEXT = AccessContext("ctx-low", "observer-brenn", "HUMAN", ("OBSERVER",),
                            (), ("CORRIDOR-OPS",), "PARTNER-RELIEF-ORG")
SERVICE_CONTEXT = AccessContext("ctx-svc", "rule-engine", "SERVICE", ("ANALYST",),
                                ("SENSITIVE-INFRA",), ("CORRIDOR-OPS",), "CIVDEF-AUTH")


def t(hours: float) -> str:
    base = datetime.fromisoformat(T0)
    return (base + timedelta(hours=hours)).astimezone(timezone.utc).isoformat()


def make_store(tmp_path, name: str = "store", store_id: str = "test-store") -> MissionDataStore:
    return MissionDataStore.create(tmp_path / name, store_id, T0)


def fake_sha(seed: str) -> str:
    return hashlib.sha256(seed.encode()).hexdigest()
