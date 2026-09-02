"""The browser and the server canonicalize to the same bytes, and the browser
refuses every value where they would differ."""
from __future__ import annotations

import base64
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from curunir_operational.canonical import canonical_line

pytestmark = pytest.mark.no_db
NODE = shutil.which("node")
HARNESS = Path(__file__).parents[1] / "js" / "canonical_harness.mjs"

PARITY = [
    {}, [], "", True, False, None, 0, -1, 9007199254740991,
    "quotes \" slash \\ controls\n\t Unicode café Ω 中文 👍🏽",
    {"b": 1, "a": 2, "nested": ["x", None, {"z": True}]},
    {"\U0001f600": 2, "\uffff": 1, "z": 3},
    {
        "purpose": "curunir-authenticate",
        "actor_id": "analyst-a",
        "nonce": "n-1",
    },
    {
        "actor_id": "analyst-b",
        "actor_kind": "HUMAN",
        "action_type": "approve_report",
        "target_kind": "workbench_report",
        "target_id": "report-1",
        "target_version_token": "workbench_report:report-1@v2",
        "mission_id": "mission-1",
        "nonce": "n-2",
        "timestamp": "2026-08-21T10:00:00+00:00",
        "command": {"note": "sound", "acknowledge_dissent": []},
    },
]

REFUSED = [
    1.5,
    1e-7,
    9007199254740993,
    -0.0,
    "lone \ud800 surrogate",
    {"key": "tail \udfff"},
    {"\ud800": 1},
]


def _run(tmp_path, values):
    input_path = tmp_path / "canonical-inputs.json"
    input_path.write_text(
        json.dumps(values, ensure_ascii=True), encoding="utf-8")
    result = subprocess.run(
        [NODE, str(HARNESS), str(input_path)],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    return result.stdout.splitlines()


@pytest.mark.skipif(NODE is None, reason="node not available")
def test_browser_and_server_canonical_bytes_are_identical(tmp_path):
    output = _run(tmp_path, PARITY)
    assert len(output) == len(PARITY)
    for value, line in zip(PARITY, output):
        assert line.startswith("OK "), (value, line)
        assert base64.b64decode(line[3:]) == canonical_line(value).encode("utf-8")


@pytest.mark.skipif(NODE is None, reason="node not available")
def test_browser_refuses_values_with_divergent_server_bytes(tmp_path):
    output = _run(tmp_path, REFUSED)
    assert all(line.startswith("ERR ") for line in output)
