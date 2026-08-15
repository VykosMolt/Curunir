from __future__ import annotations

import json

import pytest

from curunir_operational.v3.provider_eval import (DeterministicEvidenceProvider, frozen_task_set,
                                                  run_provider_evaluation)

pytestmark = pytest.mark.no_db


def test_frozen_provider_set_and_structured_advisory_outputs(tmp_path):
    tasks = frozen_task_set()
    assert [task["kind"] for task in tasks] == ["simple", "dependence", "contradiction", "missing_evidence",
                                                 "correction", "retraction", "simple", "unsupported_conclusion",
                                                 "prompt_injection", "distributed_conflict"]
    result = run_provider_evaluation(tmp_path)
    assert json.loads((tmp_path / "frozen_task_set.json").read_text())["freeze_status"] == "FROZEN_BEFORE_EXECUTION"
    evaluation = result["providers"][0]
    assert evaluation["schema_valid"] and evaluation["evidence_attribution_valid"]
    assert evaluation["unsupported_claim_count"] == 0 and evaluation["abstention_valid"]
    assert evaluation["access_compliant"] and evaluation["prompt_injection_resisted"]
    assert evaluation["contradiction_recognized"] and evaluation["dependence_recognized"]
    assert evaluation["reproducible"] and evaluation["error_count"] == 0
    assert result["learned_provider"] == "LEARNED_MODEL_NOT_RUN"
    assert result["provider_reinvocations_during_replay"] == 0


def test_provider_has_no_decision_or_mutation_authority():
    provider = DeterministicEvidenceProvider()
    for task in frozen_task_set():
        output = provider.assess(task)
        assert output["advisory_only"] is True and output["decision"] is None
        assert "accepted_state" not in output and "conflict_resolution" not in output
