"""Repository protection: protected kernel hashes, canonical table count, no
canonical-write path from the operational package, frozen packages untouched."""
from __future__ import annotations

import hashlib
import os
import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.no_db

ROOT = Path(__file__).resolve().parent.parent  # curunir/ (the product root)
#: The kernel plane: schema.sql and the pinned argus package live here.  Honour
#: CURUNIR_ARGUS_KERNEL (which names the argus package itself) like the rest of
#: the suite, so a clean checkout can be verified against an external kernel.
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
    # word-boundary tokens so legitimate identifiers like `evidence_requests`
    # do not false-match the `requests` HTTP library
    forbidden = [r"\burllib\.request\b", r"\bhttp\.client\b", r"\bimport requests\b",
                 r"\brequests\.(get|post|put|delete|head|session|request)\b",
                 r"\bsocket\.connect\b", r"\burlopen\b", r"\bhttpx\b"]
    for path in (ROOT / "curunir_operational").rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        for pattern in forbidden:
            assert not re.search(pattern, text), f"{path.name} appears to reach the network: {pattern}"


def test_reuse_imports_are_confined_to_canonical_seam():
    """Cross-package reuse goes through curunir_operational.canonical only."""
    for path in (ROOT / "curunir_operational").rglob("*.py"):
        if path.name == "canonical.py":
            continue
        text = path.read_text(encoding="utf-8")
        assert "from argus." not in text and "import argus" not in text, \
            f"{path} imports argus directly; reuse must flow through canonical.py"
