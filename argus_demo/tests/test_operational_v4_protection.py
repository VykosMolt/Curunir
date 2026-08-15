from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

from curunir_operational.v4.kernel import PROTECTED_HASHES, inspect_frozen_kernel, static_zero_write_scan
from curunir_operational.v4.models import sha256

pytestmark = pytest.mark.no_db


def test_protected_hashes_canonical_table_count_and_zero_write_scan():
    root = Path(__file__).parents[1]
    inspection = inspect_frozen_kernel(root)
    assert inspection["hashes"] == PROTECTED_HASHES
    assert inspection["canonical_table_count"] == 16
    scan = static_zero_write_scan(root / "curunir_operational" / "v4")
    assert scan["verdict"] == "PASS"
    assert scan["canonical_write_attempts"] == scan["canonical_writes"] == 0


def test_v4_never_imports_protected_action_module_or_database_clients():
    root = Path(__file__).parents[1] / "curunir_operational" / "v4"
    forbidden = {"argus.actions", "psycopg", "psycopg2", "asyncpg"}
    found = []
    for path in root.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and (node.module or "") in forbidden:
                found.append((path.name, node.module))
            if isinstance(node, ast.Import):
                found.extend((path.name, alias.name) for alias in node.names if alias.name in forbidden)
    assert found == []


def test_v4_status_vocabulary_cannot_open_human_or_canonical_gate():
    root = Path(__file__).parents[1] / "curunir_operational" / "v4"
    production = "\n".join(path.read_text(encoding="utf-8") for path in root.glob("*.py"))
    assert 'status": "ADMITTED"' not in production
    assert 'status": "HUMAN_APPROVED"' not in production
    assert 'status": "CANONICAL_WRITE_COMPLETE"' not in production


def test_research_ledger_has_inherited_records_before_v4_append():
    root = Path(__file__).parents[1]
    lines = [line for line in (root / "research" / "ledger.jsonl").read_text().splitlines() if line.strip()]
    assert len(lines) >= 55
    assert all(json.loads(line) for line in lines[-5:])
