"""Differential proof that the browser's canonical serializer
(curunir_workbench/static/js/canonical.js) produces byte-identical output to
the server's curunir_operational.canonical.canonical_line over an adversarial
corpus.

This is the load-bearing correctness lock for browser signing: the browser
signs canonical(payload) and the server verifies by re-canonicalizing the same
payload; a single divergent byte breaks every signature. Includes unicode,
control characters, quotes/backslashes, key-ordering, nesting and the exact
signed-payload shapes (challenge + approve action). Also asserts that values
whose JS/Python canonical form could diverge (floats, unsafe integers) are
REFUSED by the JS serializer rather than silently signed.
"""
from __future__ import annotations

import base64
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from curunir_operational.canonical import canonical_line

pytestmark = pytest.mark.no_db

HARNESS = Path(__file__).parent / "js" / "canonical_harness.mjs"
NODE = shutil.which("node")

# ---- corpus that MUST canonicalize identically in both stacks --------------
PARITY_CORPUS = [
    {},
    [],
    "",
    "plain",
    "with \"quotes\" and \\backslash\\",
    "tab\tnewline\nreturn\rform\ffeed\bbell",
    "control\x00\x01\x1f\x1e end",
    "unicode: café résumé Ω → ★ 中文 🇪🇺 👍🏽 é",  # combining accent, ZWJ-ish, flags
    "rtl ‮abc‬ mix",
    "slash/forward and     seps",
    True,
    False,
    None,
    0,
    1,
    -1,
    42,
    9007199254740991,   # Number.MAX_SAFE_INTEGER
    -9007199254740991,
    {"b": 1, "a": 2, "c": 3},                       # key sorting
    {"z": {"y": {"x": [3, 2, 1]}}},                 # nested, list order preserved
    {"k": ["s", True, None, 7, {"nested": "obj"}]},
    {"": "empty key", "unicode-key-ключ": "v"},
    # astral-plane keys: JS UTF-16 code-unit sort diverges from Python's
    # code-point sort unless the serializer compares code points (review F7)
    {"\U0001F600": 2, "￿": 1, "z": 3},
    {"\U0001F680": "a", "\U0001F600": "b", "m": "c"},
    {"actor_id": "analyst-a", "nonce": "abc123",
     "purpose": "curunir-authenticate"},            # exact challenge payload
    {"actor_id": "analyst-b", "actor_kind": "HUMAN",
     "action_type": "approve_report", "target_kind": "workbench_report",
     "target_id": "rep-1", "target_version_token": "workbench_report:rep-1@v2",
     "mission_id": "m-1", "nonce": "n-1", "timestamp": "2026-08-17T10:00:00+00:00",
     "command": {"note": "approved with dissent \"noted\"", "flag": True}},
]

# ---- values the JS serializer MUST refuse (divergence risk) -----------------
# NB: only cases that survive JSON transport AS a float/unsafe-int are useful
# here — a literal 1.0 is collapsed to integer 1 by JS JSON.parse before the
# serializer ever sees it (JS has no int/float distinction), so it is not a
# meaningful refusal vector. The real, transportable divergence risks are
# non-integer floats and integers beyond 2**53.
REFUSED_CORPUS = [
    1.5,                    # float
    1e-7,                   # Python '1e-07' vs JS '1e-7'
    9007199254740993,       # > 2**53: JS rounds to an unsafe integer -> refused
    "lone \ud800 surrogate",  # Python canonical_line RAISES; JS must refuse too (F-J1)
    {"k": "tail \udfff"},     # a lone surrogate nested in a value
    {"\ud800": 1},            # a lone surrogate in an object KEY (F-J1 residual)
    {"outer": {"k\udfff": "ok"}},  # a lone-surrogate key nested one level down
    -0.0,                     # negative zero: JS "0" vs Python "-0.0" — refuse (M8)
]


def _run_harness(corpus):
    inputs = Path(__file__).parent / "js" / "_parity_inputs.json"
    # ensure_ascii transport so the file is pure ASCII; Node's JSON.parse
    # restores the real Unicode before canonicalizing.
    inputs.write_text(json.dumps(corpus, ensure_ascii=True), encoding="utf-8")
    try:
        proc = subprocess.run([NODE, str(HARNESS), str(inputs)],
                              capture_output=True, text=True, timeout=30)
    finally:
        inputs.unlink(missing_ok=True)
    assert proc.returncode == 0, f"harness failed: {proc.stderr}"
    return proc.stdout.splitlines()


@pytest.mark.skipif(NODE is None, reason="node not available")
def test_js_canonical_matches_python_byte_for_byte():
    lines = _run_harness(PARITY_CORPUS)
    assert len(lines) == len(PARITY_CORPUS)
    for item, line in zip(PARITY_CORPUS, lines):
        assert line.startswith("OK "), f"JS refused {item!r}: {line}"
        js_bytes = base64.b64decode(line[3:])
        py_bytes = canonical_line(item).encode("utf-8")
        assert js_bytes == py_bytes, (
            f"canonical divergence for {item!r}:\n"
            f"  py={py_bytes!r}\n  js={js_bytes!r}")


@pytest.mark.skipif(NODE is None, reason="node not available")
def test_js_canonical_refuses_divergent_numbers():
    lines = _run_harness(REFUSED_CORPUS)
    assert len(lines) == len(REFUSED_CORPUS)
    for item, line in zip(REFUSED_CORPUS, lines):
        assert line.startswith("ERR "), (
            f"JS should refuse {item!r} (float/unsafe int) but produced {line}")
