"""D25H — the exposed harness must reach production the way production does.

These tests guard the defect campaign 341 repaired: the exposed evaluation was
calling `roles_v2.bind()` with no `structural_context`, so governing-clause
transport was never exercised and every recoverability metric was measuring an
unwired harness rather than the frozen system.

They assert the wiring, not a score.  A future edit that quietly drops the
structural context, or that reaches the binder by some path other than
`structure.build_structural_context`, fails here.
"""

import hashlib
import json
import pathlib
import sys

import pytest

from curunir_operational.v5_8_1 import regions as RG
from curunir_operational.v5_8_1 import roles_v2 as V2
from curunir_operational.v5_8_1 import structure as ST

pytestmark = pytest.mark.no_db

ROOT = (pathlib.Path(__file__).resolve().parents[1]
        / "artifacts" / "curunir_autonomous_completion_v5_8_1_20260725")
HARNESS = ROOT / "341_d25h_exposed_structural_context_wiring"
POPULATION = ROOT / "307_d42g_structural_region_truth" / "exposed_population.jsonl"

pytestmark = [pytest.mark.no_db,
              pytest.mark.skipif(not HARNESS.exists(),
                                 reason="D25H harness artifacts absent")]


def _rows(name):
    path = HARNESS / name
    return [json.loads(line) for line in path.read_text().splitlines() if line]


def test_every_unit_receives_a_structural_context():
    rows = _rows("wiring_rows.jsonl")
    assert len(rows) == 120
    unsupplied = [r["unit_id"] for r in rows
                  if not r["new"]["structural_context_supplied"]]
    assert unsupplied == [], unsupplied


def test_the_old_harness_supplied_none():
    """The defect this campaign exists to fix, asserted rather than described."""
    rows = _rows("wiring_rows.jsonl")
    assert all(not r["old"]["structural_context_supplied"] for r in rows)
    assert {r["old"]["governing_state"] for r in rows} == {
        "GOVERNING_CLAUSE_NOT_SUPPLIED"}


def test_wiring_changes_governing_state_for_the_whole_cohort():
    rows = _rows("wiring_rows.jsonl")
    assert {r["new"]["governing_state"] for r in rows} <= {
        "GOVERNING_CLAUSE_UNIQUE", "GOVERNING_CLAUSE_ABSENT",
        "GOVERNING_CLAUSE_AMBIGUOUS"}
    assert "GOVERNING_CLAUSE_NOT_SUPPLIED" not in {
        r["new"]["governing_state"] for r in rows}


def test_harness_matches_a_direct_production_invocation():
    report = json.loads((HARNESS / "parity_report.json").read_text())
    assert report["units_in_disagreement"] == 0, report[
        "field_disagreement_frequency"]
    assert report["parity"] == "NEW_EXPOSED_HARNESS == FROZEN_PRODUCTION_EXECUTION"
    assert report["cohort"] == 120


def test_denominator_is_never_reduced():
    """Unwirable units are evaluated, not dropped."""
    report = json.loads((HARNESS / "wiring_report.json").read_text())
    assert report["units"] == 120
    assert sum(report["wiring"].values()) == 120


def test_a_normalised_match_is_recorded_distinctly():
    """A near match must never be reported as an exact one."""
    report = json.loads((HARNESS / "wiring_report.json").read_text())
    assert set(report["wiring"]) <= {
        "WIRED", "NORMALISED", "REGION_NOT_REPRODUCED",
        "REGION_MATCH_AMBIGUOUS", "NO_REGIONS_REPRODUCED"}


def test_context_reaches_the_binder_through_the_production_constructor():
    """A hand-built context would prove nothing; this uses the real path."""
    sys.path.insert(0, str(HARNESS))
    import wire  # noqa: PLC0415

    unit = next(
        json.loads(line) for line in POPULATION.read_text().splitlines()
        if line and json.loads(line)["container"] == "HTML")
    documents = wire.Documents(wire.content_store())
    context, wiring, _state = wire.context_for(unit, documents)
    assert wiring in ("WIRED", "NORMALISED")
    assert isinstance(context, ST.StructuralClauseContext)
    assert context.current_region_id
    record = wire.bind_wired(unit, context)
    assert isinstance(record, V2.RoleBindingV2Record)
    assert record.candidate_id == unit["unit_id"]


def test_regions_are_reproduced_from_immutable_source_bytes():
    """The harness may not invent regions; it re-segments the stored source."""
    sys.path.insert(0, str(HARNESS))
    import wire  # noqa: PLC0415

    store = wire.content_store()
    unit = next(
        json.loads(line) for line in POPULATION.read_text().splitlines()
        if line and json.loads(line)["container"] == "HTML")
    assert unit["content_hash"] in store
    raw = store[unit["content_hash"]].read_bytes()
    assert hashlib.sha256(raw).hexdigest() == unit["content_hash"]
    produced = RG.segment(
        raw, source_id=unit["source_id"],
        source_family_id=unit["source_family_id"],
        container=unit["container"])
    assert any(
        hashlib.sha256(region.text.encode()).hexdigest()
        == unit["region_text_hash"] for region in produced)


# --- frozen-vocabulary conformance -----------------------------------------

def test_governing_clause_source_stays_in_the_frozen_vocabulary():
    """A resolved governor must be reported by KIND, never by region id.

    This field once emitted the governing region's identifier whenever one was
    resolved.  Nothing caught it, because with the harness unwired no governor
    was ever resolved and the expression never reached that branch.  Wiring the
    harness reached it for four units immediately.
    """
    import json as _json
    import pathlib as _pathlib
    import sys as _sys

    _sys.path.insert(0, str(HARNESS))
    import wire  # noqa: PLC0415

    schema_path = (ROOT / "55_d25_role_binding_reference" / "freeze"
                   / "schema.py")
    import importlib.util as _util  # noqa: PLC0415
    spec = _util.spec_from_file_location("frozen_schema", schema_path)
    schema = _util.module_from_spec(spec)
    spec.loader.exec_module(schema)
    allowed = set(schema.ROLE_SOURCES) | {"NONE"}

    units = [_json.loads(line)
             for line in POPULATION.read_text().splitlines() if line]
    documents = wire.Documents(wire.content_store())
    offenders = []
    resolved_governors = 0
    for unit in units:
        context, _wiring, _state = wire.context_for(unit, documents)
        record = (wire.bind_wired(unit, context) if context is not None
                  else wire.bind_unwired(unit))
        if context is not None and context.governing_resolution()[1]:
            resolved_governors += 1
        if record.governing_clause_source not in allowed:
            offenders.append((unit["unit_id"], record.governing_clause_source))
    assert resolved_governors > 0, (
        "the test is vacuous unless some governor resolves")
    assert offenders == [], offenders
