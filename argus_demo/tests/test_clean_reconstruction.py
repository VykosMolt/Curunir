"""V6.7 §7 — clean-checkout reconstruction.

Proves the supported Curunír product reconstructs from TRACKED repository state
plus its explicitly-documented external prerequisites (the inherited `argus`
kernel snapshot + the pinned PyPI deps), and NOT from development-machine
accidents — unstaged source, home-directory files, or the working tree.

The test exports the tracked tree with `git archive` (so only committed files
exist), confirms the intended external boundary (the `argus` kernel is NOT in
the tracked tree), mounts the kernel snapshot as the documented dependency, and
runs a real reconstruction (import → create store → export → import → replay)
in that isolated tree via a subprocess whose import path is the isolated tree
alone. See CURUNIR_RECONSTRUCTION.md.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.no_db

REPO_PKG = Path(__file__).resolve().parent.parent          # …/argus_demo
HAVE_GIT = shutil.which("git") is not None


def _git_root() -> Path | None:
    try:
        out = subprocess.run(["git", "-C", str(REPO_PKG), "rev-parse", "--show-toplevel"],
                             capture_output=True, text=True, timeout=15)
        return Path(out.stdout.strip()) if out.returncode == 0 else None
    except Exception:
        return None


RECON_SCRIPT = r'''
import os, sys, tempfile
PKG = sys.argv[1]
# import path is the ISOLATED tree only — never the working tree
sys.path.insert(0, PKG)
import curunir_workbench, curunir_operational.canonical  # noqa
# provenance: the product code MUST come from the reconstructed tree
assert os.path.abspath(curunir_workbench.__file__).startswith(PKG), \
    f"curunir_workbench resolved outside the reconstructed tree: {curunir_workbench.__file__}"
# and the argus kernel must resolve to the mounted snapshot inside the tree
import argus.prospective.freezing as fz
assert os.path.abspath(fz.__file__).startswith(PKG), \
    f"argus kernel resolved outside the reconstructed tree: {fz.__file__}"

from curunir_workbench.store import WorkbenchStore
from curunir_operational.contracts import Record  # noqa

root = tempfile.mkdtemp()
T0 = "2026-08-17T12:00:00+00:00"
store = WorkbenchStore.create(os.path.join(root, "store"), "recon-mission", T0)
# a minimal real append through the tracked contract + kernel canonicalization
from curunir_analytic.impact import create_objective
from curunir_analytic.substrate import AnalyticContext
from curunir_operational.access import Marking
ctx = AnalyticContext(store=store, actor="t",
                      marking=Marking(owning_authority="recon", releasability=("PUBLIC",)),
                      now_fn=lambda: T0)
create_objective(ctx, mission_context="recon", statement="reconstructed objective")
backup = os.path.join(root, "backup")
store.export_to(backup)
restored = WorkbenchStore.import_from(backup, os.path.join(root, "restored"))
assert restored.verify_chain()["valid"]
assert any(o["statement"] == "reconstructed objective"
           for o in restored.current_objectives().values())
print("RECONSTRUCTION_OK")
'''


@pytest.mark.skipif(not HAVE_GIT, reason="git not available")
def test_product_reconstructs_from_tracked_state_plus_documented_kernel(tmp_path):
    root = _git_root()
    if root is None:
        pytest.skip("not a git repository")
    # 1. export ONLY tracked files (git archive emits committed content)
    archive = tmp_path / "tracked.tar"
    rc = subprocess.run(["git", "-C", str(root), "archive", "-o", str(archive), "HEAD"],
                        capture_output=True, text=True, timeout=120)
    assert rc.returncode == 0, rc.stderr
    recon = tmp_path / "recon"
    recon.mkdir()
    subprocess.run(["tar", "-xf", str(archive), "-C", str(recon)], check=True, timeout=120)
    # the package dir inside the archive (mirror the working-tree layout)
    rel = REPO_PKG.relative_to(root)
    recon_pkg = recon / rel

    # 2. confirm the intended external boundary: the argus kernel is NOT tracked
    assert (recon_pkg / "curunir_workbench").is_dir(), "tracked product missing from checkout"
    assert not (recon_pkg / "argus").exists(), \
        "argus kernel unexpectedly tracked — the external boundary is not what we document"

    # 3. mount the documented external prerequisites (kernel snapshot). Copying
    #    the working-tree kernel here stands in for fetching the versioned
    #    snapshot; the point is that it is a NAMED, external, mountable asset.
    if not (REPO_PKG / "argus").is_dir():
        pytest.skip("argus kernel snapshot not present to mount")
    shutil.copytree(REPO_PKG / "argus", recon_pkg / "argus")

    # 4. run the reconstruction in the isolated tree, with a clean import path
    #    (PYTHONPATH empty; the script inserts ONLY the reconstructed pkg dir).
    env = dict(os.environ)
    env.pop("PYTHONPATH", None)
    script = recon / "recon_run.py"
    script.write_text(RECON_SCRIPT)
    proc = subprocess.run([sys.executable, str(script), str(recon_pkg)],
                          capture_output=True, text=True, timeout=180,
                          cwd=str(recon))          # cwd is the isolated tree, not the repo
    assert proc.returncode == 0, f"reconstruction failed:\nSTDOUT{proc.stdout}\nSTDERR{proc.stderr}"
    assert "RECONSTRUCTION_OK" in proc.stdout, proc.stdout
