"""Measure what a workbench request costs as the event log grows.

Builds a synthetic mission store of N object versions, then times a store
open, a mission projection, and the overview through the real HTTP app,
with and without an append between requests.

    python tools/bench_store.py --events 5000 --requests 50
"""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
import time
from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PACKAGE_ROOT))

from curunir_operational.access import Marking  # noqa: E402
from curunir_operational.contracts import ObjectVersion, ProvenanceSummary  # noqa: E402
from curunir_workbench.auth import write_registry  # noqa: E402
from curunir_workbench.projections import MissionProjection  # noqa: E402
from curunir_workbench.server import create_app  # noqa: E402
from curunir_workbench.store import WorkbenchStore  # noqa: E402

OPEN = Marking(owning_authority="bench", releasability=("PUBLIC",))
RESTRICTED = Marking(owning_authority="bench", compartments=("SPECIAL",), releasability=("PUBLIC",))
PROVENANCE = ProvenanceSummary(mode="OPERATIONAL", source_ids=("src-bench",), ingestion_ids=("ing-bench",))


def stamp(hours: float) -> str:
    whole, frac = divmod(hours, 1)
    return f"2026-03-{1 + int(whole) // 24:02d}T{int(whole) % 24:02d}:{int(frac * 60):02d}:00+00:00"


def version(i: int, objects: int) -> ObjectVersion:
    object_id = f"obj-{i % objects}"
    return ObjectVersion(object_id=object_id, version=i // objects + 1, object_type="INFRASTRUCTURE",
                         lifecycle="ACTIVE", labels=(object_id,), external_refs=(), valid_from=stamp(i / 100),
                         valid_to=None, source_time=stamp(i / 100), time_precision="HOUR", recorded_time=stamp(i / 100),
                         geometry=None, attributes={"n": i}, quality={}, epistemic_state="REPORTED",
                         marking=RESTRICTED if i % 7 == 0 else OPEN, provenance=PROVENANCE)


def build(root: Path, events: int) -> WorkbenchStore:
    store = WorkbenchStore.create(root / "store", "bench", stamp(0))
    for i in range(events):
        store.append("OBJECT_VERSION_APPENDED", version(i, max(events // 10, 1)), recorded_time=stamp(i / 100), actor="bench")
    write_registry(root / "actors.json", [
        {"token": "bench-token", "actor_id": "bench-analyst", "actor_kind": "HUMAN", "roles": ["ANALYST"],
         "compartments": ["SPECIAL"], "releasability": ["PUBLIC"], "organisation": "bench"}])
    return store


def timed(label: str, fn, repeat: int = 1) -> float:
    started = time.perf_counter()
    for _ in range(repeat):
        fn()
    elapsed = (time.perf_counter() - started) / repeat * 1000
    print(json.dumps({"measure": label, "ms": round(elapsed, 2)}))
    return elapsed


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--events", type=int, default=5000)
    parser.add_argument("--requests", type=int, default=50)
    args = parser.parse_args()
    from fastapi.testclient import TestClient
    with tempfile.TemporaryDirectory(prefix="curunir-bench-") as scratch:
        root = Path(scratch)
        store = build(root, args.events)
        print(json.dumps({"events": args.events, "head": store.head()["head_hash"][:12]}))
        from curunir_workbench.auth import ActorRegistry
        actor = ActorRegistry(root / "actors.json").context_for("bench-token")
        timed("open store", lambda: WorkbenchStore(root / "store"), 3)
        timed("open + mission projection", lambda: MissionProjection(WorkbenchStore(root / "store"), actor), 3)
        client = TestClient(create_app(root, root / "actors.json"))
        headers = {"Authorization": "Bearer bench-token"}

        def overview():
            response = client.get("/api/overview", headers=headers)
            response.raise_for_status()

        timed("overview request, store unchanged", overview, args.requests)

        counter = [args.events]

        def overview_with_appends():
            if counter[0] % 10 == 0:
                writer = WorkbenchStore(root / "store")
                writer.append("OBJECT_VERSION_APPENDED", version(counter[0], max(args.events // 10, 1)),
                              recorded_time=stamp(counter[0] / 100), actor="bench")
            counter[0] += 1
            overview()

        timed("overview request, an append every 10th request", overview_with_appends, args.requests)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
