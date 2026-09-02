"""The repository ledger, checked rather than read.

tests/ledger.json says what the suite collected when it was last accepted,
which product modules are entry points, and what was excised and where it is
archived. These tests hold the tree to it: a test module cannot shrink, vanish
or skip itself away unnoticed; a product module cannot survive with nothing
importing it; an excised path cannot come back and its archive tag cannot go
missing. Update the ledger on purpose with `python tools/ledger.py --update`.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
import ledger  # noqa: E402

pytestmark = pytest.mark.no_db

LEDGER = json.loads(ledger.LEDGER_PATH.read_text(encoding="utf-8"))
TEST_FILES = sorted(p.relative_to(ledger.PACKAGE_ROOT).as_posix()
                    for p in (ledger.PACKAGE_ROOT / "tests").rglob("test_*.py"))


@pytest.fixture(scope="module")
def collected():
    return ledger.collected_tests()


def test_every_test_module_is_in_the_ledger_and_collects_at_least_what_it_did(collected):
    recorded = LEDGER["tests_collected"]
    unrecorded = sorted(set(collected) - set(recorded))
    assert not unrecorded, f"new test modules; add them with tools/ledger.py --update: {unrecorded}"
    shrunk = {m: (recorded[m], collected.get(m, 0)) for m in recorded if collected.get(m, 0) < recorded[m]}
    assert not shrunk, f"modules collecting fewer tests than the ledger records (was, now): {shrunk}"


def test_no_test_module_skips_itself_at_module_level():
    hidden = {path: skips for path in TEST_FILES
              if (skips := ledger.module_level_skips(ledger.PACKAGE_ROOT / path))}
    assert not hidden, hidden


def test_every_product_module_has_an_importer_or_is_an_entry_point():
    users = ledger.importers()
    entry_points = set(LEDGER["entry_points"])
    orphans = sorted(name for name, importing in users.items() if not importing and name not in entry_points)
    assert not orphans, f"nothing imports these modules; excise them or record them as entry points: {orphans}"
    stale = sorted(name for name in entry_points if name not in users)
    assert not stale, f"ledger names entry points that no longer exist: {stale}"


def test_excised_paths_stay_gone_and_stay_recoverable():
    tags = set(subprocess.run(["git", "tag", "-l"], cwd=ledger.PACKAGE_ROOT,
                              capture_output=True, text=True, check=True).stdout.split())
    for entry in LEDGER["excised"]:
        for path in entry["paths"]:
            assert not (ledger.PACKAGE_ROOT / path).exists(), f"{path} was excised on {entry['date']} but is back"
        assert entry["archive_tag"] in tags, f"archive tag {entry['archive_tag']} is missing"
