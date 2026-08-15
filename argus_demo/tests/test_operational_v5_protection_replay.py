from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from curunir_operational.v5.operations import comprehensive_v4_replay

pytestmark = pytest.mark.no_db

V4 = Path("artifacts/curunir_public_source_intelligence_and_kernel_admission_v4_20260722")


def _digest(path): return hashlib.sha256(path.read_bytes()).hexdigest()


def test_protected_hashes_and_tables():
    assert _digest(Path("schema.sql")) == "7af415d510de07ebf681f996320c938706049fe3aa3a57dbf5a3a62237a9da32"
    assert _digest(Path("argus/actions.py")) == "faa7d5b6c86386a4dd34109368efae20d917e0b8b440b6ca83a2e711d16cdd14"
    assert sum(line.lstrip().casefold().startswith("create table") for line in Path("schema.sql").read_text().splitlines()) == 16


def test_comprehensive_replay_has_zero_external_calls(tmp_path):
    result = comprehensive_v4_replay(v4_root=V4, output_root=tmp_path)
    assert result["verdict"] == "PASS" and result["copied_root_supported"]
    assert result["network_requests"] == result["provider_reinvocations"] == 0
    assert result["canonical_write_attempts"] == result["canonical_writes"] == 0
    assert all(x["all_layers_valid"] for x in result["campaigns"].values())


def test_human_gate_remains_closed():
    text = "\n".join(path.read_text(encoding="utf-8") for path in Path("curunir_operational/v5").glob("*.py"))
    assert "HUMAN_REVIEW_COMPLETE" not in text
    assert "HUMAN_VALIDATED_ACCURACY" not in text
    assert "INSTITUTIONALLY_APPROVED" not in text


def test_no_canonical_database_imports_or_sql():
    files = list(Path("curunir_operational/v5").glob("*.py"))
    text = "\n".join(path.read_text(encoding="utf-8") for path in files)
    assert "psycopg" not in text and "argus.actions" not in text
    assert "INSERT INTO" not in text and "DELETE FROM" not in text
