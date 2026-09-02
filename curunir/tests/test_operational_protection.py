"""Guards on the kernel plane: the protected files still hash the same, and the
operational package reaches neither the canonical database nor the network."""
from __future__ import annotations

import hashlib
import os
import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.no_db

ROOT = Path(__file__).resolve().parent.parent  # the product root
# The kernel plane holds schema.sql and the pinned argus package.
# CURUNIR_ARGUS_KERNEL names the argus package, so a clean checkout can be
# checked against a kernel unpacked elsewhere.
KERNEL = Path(os.environ.get("CURUNIR_ARGUS_KERNEL", ROOT.parent / "kernel" / "argus")).resolve().parent

PROTECTED = {
    "schema.sql": "7af415d510de07ebf681f996320c938706049fe3aa3a57dbf5a3a62237a9da32",
    "argus/actions.py": "faa7d5b6c86386a4dd34109368efae20d917e0b8b440b6ca83a2e711d16cdd14",
}


def test_protected_hashes_unchanged():
    for relative, expected in PROTECTED.items():
        actual = hashlib.sha256((KERNEL / relative).read_bytes()).hexdigest()
        assert actual == expected, f"protected file changed: {relative}"


def test_canonical_table_count_unchanged():
    schema = (KERNEL / "schema.sql").read_text(encoding="utf-8")
    tables = re.findall(r"(?i)create table\s+(?:if not exists\s+)?([a-z_]+)", schema)
    assert len(tables) == 16


def test_operational_package_has_no_canonical_write_path():
    forbidden = ("psycopg", "argus.db", "argus.actions", "from argus import db", "DATABASE_URL")
    for path in (ROOT / "curunir_operational").rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        for token in forbidden:
            assert token not in text, f"{path.name} references canonical DB machinery: {token}"


def test_operational_package_has_no_network_calls():
    # Word boundaries, so a name like `evidence_requests` does not match the
    # `requests` library.
    forbidden = [r"\burllib\.request\b", r"\bhttp\.client\b", r"\bimport requests\b",
                 r"\brequests\.(get|post|put|delete|head|session|request)\b",
                 r"\bsocket\.connect\b", r"\burlopen\b", r"\bhttpx\b"]
    for path in (ROOT / "curunir_operational").rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        for pattern in forbidden:
            assert not re.search(pattern, text), f"{path.name} appears to reach the network: {pattern}"


def test_reuse_imports_are_confined_to_canonical_seam():
    """Reuse from other packages goes through canonical.py and nowhere else."""
    for path in (ROOT / "curunir_operational").rglob("*.py"):
        if path.name == "canonical.py":
            continue
        text = path.read_text(encoding="utf-8")
        assert "from argus." not in text and "import argus" not in text, \
            f"{path} imports argus directly; reuse must flow through canonical.py"
