"""Isolated product smoke run used by reconstruct_v67.py."""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path


def _under(path: str, root: Path) -> bool:
    try:
        Path(path).resolve().relative_to(root)
        return True
    except ValueError:
        return False


def main() -> int:
    if len(sys.argv) != 2:
        raise SystemExit("usage: reconstruction_smoke.py PACKAGE_ROOT")
    package_root = Path(sys.argv[1]).resolve()
    sys.path.insert(0, str(package_root))

    import argus.prospective.freezing as kernel_freezing
    import curunir_workbench
    from curunir_analytic.impact import create_objective
    from curunir_analytic.substrate import AnalyticContext
    from curunir_operational.access import Marking
    from curunir_workbench.store import WorkbenchStore

    if not _under(curunir_workbench.__file__, package_root):
        raise RuntimeError("Curunir imported outside the reconstructed tree")
    if not _under(kernel_freezing.__file__, package_root):
        raise RuntimeError("argus imported outside the reconstructed tree")

    timestamp = "2026-08-21T12:00:00+00:00"
    with tempfile.TemporaryDirectory(prefix="curunir-v67-recon-smoke-") as scratch:
        root = Path(scratch)
        store = WorkbenchStore.create(root / "store", "reconstruction", timestamp)
        context = AnalyticContext(
            store=store,
            actor="reconstruction",
            marking=Marking(owning_authority="reconstruction", releasability=("PUBLIC",)),
            now_fn=lambda: timestamp,
        )
        create_objective(
            context,
            mission_context="reconstruction",
            statement="reconstructed objective",
        )
        payload_digest = store.put_payload(b"reconstruction payload")
        backup = root / "backup"
        manifest = store.export_to(backup)
        restored = WorkbenchStore.import_from(backup, root / "restored")
        if not restored.verify_chain()["valid"]:
            raise RuntimeError("restored chain is invalid")
        if restored.get_payload(payload_digest) != b"reconstruction payload":
            raise RuntimeError("restored payload differs")
        if not any(
            item["statement"] == "reconstructed objective"
            for item in restored.current_objectives().values()
        ):
            raise RuntimeError("restored projection is incomplete")
        print(json.dumps({
            "status": "RECONSTRUCTION_OK",
            "event_count": manifest["event_count"],
            "head_hash": manifest["head_hash"],
            "payload_sha256": payload_digest,
        }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
