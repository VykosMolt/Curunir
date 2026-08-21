"""The committed V6.7 tree reconstructs only with its exact external kernel."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.no_db

PACKAGE_ROOT = Path(__file__).resolve().parents[2]


def test_terminal_validator_refuses_failure_error_kind_changes():
    from tools.validate_v67 import _outcome_kind_changes

    baseline = {
        "allowed_error_nodeids": ["suite::error-node"],
    }
    assert _outcome_kind_changes(
        baseline,
        {"suite::error-node": "error", "suite::failure-node": "failure"},
    ) == []
    assert _outcome_kind_changes(
        baseline,
        {"suite::error-node": "failure", "suite::failure-node": "error"},
    ) == ["suite::error-node", "suite::failure-node"]


def test_clean_checkout_reconstruction_uses_committed_bytes_and_pinned_kernel(tmp_path):
    kernel = Path(os.environ.get("CURUNIR_ARGUS_KERNEL", PACKAGE_ROOT / "argus"))
    assert kernel.is_dir(), "the explicit external kernel input is unavailable"
    output = tmp_path / "reconstructed"
    process = subprocess.run(
        [
            sys.executable,
            str(PACKAGE_ROOT / "tools" / "reconstruct_v67.py"),
            "--kernel",
            str(kernel),
            "--output",
            str(output),
            "--ref",
            "HEAD",
        ],
        cwd=PACKAGE_ROOT,
        capture_output=True,
        text=True,
        timeout=240,
    )
    assert process.returncode == 0, process.stderr
    result = json.loads(process.stdout)
    assert result["status"] == "RECONSTRUCTION_OK"
    assert result["smoke"]["status"] == "RECONSTRUCTION_OK"
    assert (output / "argus_demo" / "curunir_workbench").is_dir()
    assert (output / "argus_demo" / "argus" / "prospective" / "freezing.py").is_file()


def test_reconstruction_refuses_a_different_kernel_before_creating_output(tmp_path):
    wrong_kernel = tmp_path / "wrong-argus"
    wrong_kernel.mkdir()
    (wrong_kernel / "__init__.py").write_text("# wrong snapshot\n", encoding="utf-8")
    output = tmp_path / "must-not-exist"
    process = subprocess.run(
        [
            sys.executable,
            str(PACKAGE_ROOT / "tools" / "reconstruct_v67.py"),
            "--kernel",
            str(wrong_kernel),
            "--output",
            str(output),
        ],
        cwd=PACKAGE_ROOT,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert process.returncode == 1
    assert "kernel identity mismatch" in process.stderr
    assert not output.exists()
