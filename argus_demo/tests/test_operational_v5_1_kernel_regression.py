"""V5.1 kernel-proposal regression tests (contract Section 28)."""
from __future__ import annotations

import json

import pytest

from curunir_operational.v5_1.kernel_regression import (
    DependencyState, ProposalRevalidation, regression_report,
    revalidate_prior_proposals, shadow_proposals_from_campaign,
)

from test_operational_v5_1_campaign import (  # noqa: F401  (fixture reuse)
    _definition, _fake_acquire, _leads, campaign_run,
)

pytestmark = pytest.mark.no_db

_TIME = "2026-07-24T12:00:00+00:00"


def _proposal(index: int, claim: str) -> dict:
    return {
        "proposal_id": f"fixture-proposal-{index:03d}",
        "case_id": "case-fixture", "proposed_concept": f"concept-{index}",
        "claim_ids": [claim], "evidence_basis_ids": [f"basis-{index}"],
        "source_object_ids": [f"source-{index}"], "dependencies": [],
        "status": "HUMAN_REVIEW_REQUIRED",
    }


def _roots(tmp_path):
    v4 = tmp_path / "v4_artifacts"
    v5 = tmp_path / "v5_artifacts"
    (v4 / "admission").mkdir(parents=True)
    (v5 / "kernel").mkdir(parents=True)
    (v4 / "admission" / "proposal_register.json").write_text(json.dumps(
        [_proposal(1, "claim-retracted"), _proposal(2, "claim-current")]),
        encoding="utf-8")
    (v5 / "kernel" / "superseding_proposals.json").write_text(json.dumps(
        [_proposal(3, "claim-superseded")]), encoding="utf-8")
    return v4, v5


def _signal(kind: str, dependency_id: str) -> str:
    if "retracted" in dependency_id:
        return "RETRACTED"
    if "superseded" in dependency_id:
        return "SUPERSEDED"
    return "CURRENT"


def test_upstream_invalidation_forces_disposition(tmp_path):
    v4, v5 = _roots(tmp_path)
    report = revalidate_prior_proposals(v4, v5, _signal, tmp_path / "reval.json")
    by_id = {row["proposal_id"]: row for row in report["dispositions"]}
    assert by_id["fixture-proposal-001"]["disposition"] == "INVALIDATED"
    assert by_id["fixture-proposal-003"]["disposition"] == "SUPERSEDED"
    assert by_id["fixture-proposal-002"]["disposition"] in {
        "UNCHANGED_VALID_SHADOW", "HUMAN_REVIEW_REQUIRED"}


def test_record_guard_refuses_valid_shadow_with_invalidated_dependency():
    dependency = DependencyState("CLAIM", "claim-retracted", "RETRACTED")
    with pytest.raises(ValueError, match="never emerge"):
        ProposalRevalidation(
            "reval-guard-1", "proposal-x", "artifact://x",
            "UNCHANGED_VALID_SHADOW", (dependency,), None, True, False, _TIME)


def test_human_review_requirement_is_preserved():
    with pytest.raises(ValueError, match="human"):
        ProposalRevalidation(
            "reval-guard-2", "proposal-y", "artifact://y",
            "HUMAN_REVIEW_REQUIRED",
            (DependencyState("CLAIM", "claim-1", "CURRENT"),),
            DependencyState("CLAIM", "claim-1", "CURRENT"), False, False, _TIME)


def test_uninterpretable_states_fail_closed(tmp_path):
    v4, v5 = _roots(tmp_path)
    report = revalidate_prior_proposals(
        v4, v5, lambda kind, dep: "NOT_A_REAL_STATE", tmp_path / "reval2.json")
    for row in report["dispositions"]:
        assert row["disposition"] == "HUMAN_REVIEW_REQUIRED"
    assert report["capability_outcomes"], "uninterpretable lookups must be recorded"
    assert all(item["outcome"] == "SYSTEM_CAPABILITY_FAILURE"
               for item in report["capability_outcomes"])


def test_absent_roots_tolerated(tmp_path):
    report = revalidate_prior_proposals(
        tmp_path / "missing_a", tmp_path / "missing_b", _signal,
        tmp_path / "reval3.json")
    assert report["proposals_examined"] == 0
    assert report["empty"] is True


def test_shadow_proposals_never_admitted(campaign_run, tmp_path):
    _d, _custody, analysis, _acq, _metrics, _tmp = campaign_run
    report = shadow_proposals_from_campaign(
        analysis, tmp_path / "shadow.json",
        repository_root="/home/moloch/Saulot/argus_demo")
    assert report["zero_write"]["verdict"] == "PASS"
    assert report["zero_write"]["canonical_write_attempts"] == 0
    assert report["zero_write"]["canonical_writes"] == 0
    assert report["admitted"] == 0
    for record in report["shadow_proposals"]:
        assert record["review_state"] == "HUMAN_REVIEW_REQUIRED"
        assert record["blocked"] is True
        assert record["canonical_write"] is False
    assert report["proposals_built"] >= 1


def test_regression_report_roundtrip(campaign_run, tmp_path):
    v4, v5 = _roots(tmp_path)
    _d, _custody, analysis, _acq, _metrics, _tmp = campaign_run
    revalidation = revalidate_prior_proposals(v4, v5, _signal, tmp_path / "r.json")
    shadow = shadow_proposals_from_campaign(
        analysis, tmp_path / "s.json",
        repository_root="/home/moloch/Saulot/argus_demo")
    report = regression_report(revalidation, shadow, tmp_path / "final.json")
    attestation = report["zero_write_attestation"]
    assert attestation["revalidation"]["canonical_writes"] == 0
    assert attestation["shadow"]["canonical_writes"] == 0
    assert report["integrity_hash"]
    assert report["human_review_required"] is True
    counts = report["disposition_counts"]
    assert sum(counts.get(k, 0) for k in ("INVALIDATED", "SUPERSEDED")) >= 2
