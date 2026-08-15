from __future__ import annotations

import os
from pathlib import Path

import pytest

from curunir_operational.v4.stress import run_corpus_stress

pytestmark = pytest.mark.no_db


@pytest.mark.skipif(os.environ.get("CURUNIR_RUN_V4_STRESS") != "1", reason="explicit bounded stress opt-in")
def test_explicit_v4_500_document_corpus_stress(tmp_path):
    root = Path(__file__).parents[1]
    result = run_corpus_stress(tmp_path / "stress", root)
    assert result["verdict"] == "PASS"
    assert result["documents"] >= 500 and result["language_labels"] >= 5
    assert result["extraction_candidates"] >= 10_000 and result["claims"] >= 3000
    assert result["source_origin_edges"] >= 1000
    assert result["contradiction_or_qualification_edges"] >= 500
    assert result["kernel_admission_proposals"] >= 100
    assert result["canonical_writes"] == result["network_requests_during_replay"] == 0
