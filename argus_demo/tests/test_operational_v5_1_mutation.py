"""V5.1 required-mutation battery tests (contract Section 30)."""
from __future__ import annotations

import json

import pytest

from curunir_operational.v5_1.mutation import run_required_mutations

pytestmark = pytest.mark.no_db


@pytest.fixture(scope="module")
def battery(tmp_path_factory):
    out = tmp_path_factory.mktemp("mutations")
    return out, run_required_mutations(out / "mutations.json")


def test_all_28_mutations_caught(battery):
    _out, report = battery
    rows = report["mutations"]
    assert len(rows) == 28
    assert all(row["caught"] for row in rows), [
        row["mutation_id"] for row in rows if not row["caught"]]
    assert report["verdict"] == "PASS"
    assert report["caught"] == 28


def test_mutation_records_carry_reason_and_mechanism(battery):
    _out, report = battery
    for row in report["mutations"]:
        assert row["description"].strip()
        assert row["expected_failure_reason"].strip()
        assert row["catch_mechanism"].strip()
        assert not row["catch_mechanism"].startswith("UNEXPECTED_")


def test_spot_mutations_attack_real_guards(battery):
    _out, report = battery
    by_id = {row["mutation_id"]: row for row in report["mutations"]}
    # 13: NO_DEPENDENCE_FOUND -> independence must be a constructor guard.
    assert "INDEPENDENCE" in by_id[13]["catch_mechanism"].upper() or \
           "independence" in by_id[13]["catch_mechanism"]
    # 19: held-out label exposure caught by the leakage scan.
    assert "leak" in by_id[19]["catch_mechanism"].casefold()
    # 24: canonical write attempt caught by the zero-write machinery.
    assert "write" in by_id[24]["catch_mechanism"].casefold()
    # 25: replay network use caught by the network guard.
    assert "network" in by_id[25]["catch_mechanism"].casefold() or \
           "replay" in by_id[25]["catch_mechanism"].casefold()


def test_report_written_and_verdict_fails_closed(battery):
    out, report = battery
    written = json.loads((out / "mutations.json").read_text())
    assert written["verdict"] == report["verdict"] == "PASS"
    assert {row["mutation_id"] for row in written["mutations"]} == set(range(1, 29))


def test_battery_is_repeatable(tmp_path):
    report = run_required_mutations(tmp_path / "again.json")
    assert report["verdict"] == "PASS"
    assert report["caught"] == 28
