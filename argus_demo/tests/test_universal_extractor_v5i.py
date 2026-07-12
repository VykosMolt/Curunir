"""V5I tests: claim-type grammatical-shape features and operating point."""
from __future__ import annotations

import pytest

from argus_neural.universal_extractor_v3 import _claim_type_shape_features


def _f(sent):
    return _claim_type_shape_features(sent)


@pytest.mark.no_db
def test_temporal_lead_fires_on_date_led_decision_sentences():
    f = _f("On 13 April 2023, the restricted committee considered the matter closed.")
    assert "ctype:temporal_lead" in f
    assert "ctype:has_date_shape" in f
    f2 = _f("In a decision of 17 October 2022, a fine was imposed.")
    assert "ctype:has_date_shape" in f2


@pytest.mark.no_db
def test_present_descriptive_sentences_get_copula_cue_not_temporal():
    f = _f("The company is a US technology firm which collects photographs.")
    assert "ctype:present_copula_lead" in f
    assert "ctype:temporal_lead" not in f
    assert f.get("ctype:past_shape=0") == 1.0


@pytest.mark.no_db
def test_past_shape_density_counts_participles():
    f = _f("The authority imposed a fine and ordered the data deleted.")
    assert "ctype:past_shape=3" in f


@pytest.mark.no_db
def test_modal_and_amount_cues():
    f = _f("The operator must pay 250 000 EUR within two months.")
    assert "ctype:modal" in f
    assert "ctype:amountish" in f


@pytest.mark.no_db
def test_features_flow_into_claim_span_features():
    from argus_neural.span_utils import Span
    from argus_neural.structural_segmenter import segment_document
    from argus_neural.universal_extractor_v3 import (
        claim_span_features,
        segment_for_span,
    )
    text = "On 5 May 2024, the board fined Acme Corp 100 000 EUR.\n"
    seg = segment_for_span(segment_document(text), 0, len(text) - 1)
    feats = claim_span_features(text, Span(0, len(text) - 1), seg, "line")
    assert "ctype:temporal_lead" in feats
    assert "ctype:amountish" in feats


@pytest.mark.no_db
def test_v5i_operating_point_declared():
    from argus_neural.run_v5i_claim_type_repair import (
        SYNTH_BEFORE,
        V5H_BASE,
        V5I_CLAIM_THRESHOLD,
    )
    assert V5I_CLAIM_THRESHOLD == 0.46
    assert V5H_BASE["claim"] == 0.4519
    assert SYNTH_BEFORE["claim_f1"] == 0.8944
