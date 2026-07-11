"""V5D tests: generalizable relation pair features, masked-copy training,
featurizer dispatch, save/load roundtrip, runtime audit cleanliness."""
from __future__ import annotations

from pathlib import Path

import pytest

from argus_neural.realistic_evaluation import scan_text_for_runtime_audit
from argus_neural.span_utils import Span
from argus_neural.structural_segmenter import segment_document
from argus_neural.thread_graph import build_thread_graph
from argus_neural.universal_extractor_v3 import segment_for_span
from argus_neural.universal_extractor_v5b import load_v5b
from argus_neural.universal_extractor_v5d import (
    ArgusUniversalNeuralExtractorV5D,
    V5D_RELATION_THRESHOLD,
    _date_tuples,
    _numeric_values,
    _proper_anchor_set,
    load_v5d,
    mask_lexical_pair_features,
    relation_pair_features_v5d,
    save_v5d,
    train_relation_head_v5d,
    upgrade_v5b_to_v5d,
)

ROOT = Path(__file__).resolve().parents[1]
V5C_MODEL = (ROOT / "artifacts"
             / "universal_neural_extractor_v5c_exhaustive_label_expansion_endpoint_repair"
             / "model_v5c")

DOC = (
    "The supervisory authority found that Acme Corp processed data unlawfully.\n"
    "The supervisory authority fined Acme Corp 250 000 EUR on 12 May 2023.\n"
    "An earlier decision of 3 March 2021 set the penalty at 100 000 EUR.\n"
)


def _pair_feats(text: str, left: tuple[int, int], right: tuple[int, int],
                left_type: str = "incident", right_type: str = "incident"):
    segmentation = segment_document(text)
    graph = build_thread_graph(text, segmentation)
    ls, rs = Span(*left), Span(*right)
    return relation_pair_features_v5d(
        text, ls, rs, left_type, right_type,
        segment_for_span(segmentation, ls.start, ls.end),
        segment_for_span(segmentation, rs.start, rs.end),
        graph,
    )


@pytest.mark.no_db
def test_numeric_value_normalization_across_grouping_styles():
    assert _numeric_values("a fine of EUR 30.000") == {30000.0}
    assert _numeric_values("total of € 30 500 000") == {30500000.0}
    assert _numeric_values("fined 30,500,000 in total") == {30500000.0}
    assert _numeric_values("scores 82.34 on the benchmark") == {82.34}
    assert _numeric_values("1 234.56 units") == {1234.56}
    assert _numeric_values("no numerals here") == set()


@pytest.mark.no_db
def test_date_tuples_prose_and_iso():
    assert _date_tuples("decision of 17 October 2022") == {(2022, 10, 17)}
    assert _date_tuples("On May 3, 2021 and on 2023-04-13") == {
        (2021, 5, 3), (2023, 4, 13)}
    assert _date_tuples("in October generally") == set()


@pytest.mark.no_db
def test_proper_anchor_set_skips_claim_initial_and_stop_tokens():
    anchors = _proper_anchor_set("The Hellenic authority fined Acme Corp today")
    assert "acme" in anchors and "corp" in anchors and "hellenic" in anchors
    assert "the" not in anchors
    # claim-initial capitalization alone is not a name
    assert _proper_anchor_set("Data was processed unlawfully") == set()


@pytest.mark.no_db
def test_v5d_features_drop_memorization_surface():
    line1 = (0, DOC.index("\n"))
    line2 = (DOC.index("\n") + 1, DOC.index("\n", DOC.index("\n") + 1))
    feats = _pair_feats(DOC, line1, line2)
    assert not any(k.startswith("pair:diff=") for k in feats)
    assert not any(k.startswith("between:") for k in feats)
    assert not any(k.startswith(("left_claim:c3=", "right_claim:c3=",
                                 "left_claim:c4=", "right_claim:c4=")) for k in feats)
    assert "left_claim:w1=the" not in feats
    # contentful unigrams survive
    assert any(k.startswith("right_claim:w1=fined") for k in feats)


@pytest.mark.no_db
def test_v5d_abstract_pair_features_present():
    line1 = (0, DOC.index("\n"))
    line2 = (DOC.index("\n") + 1, DOC.index("\n", DOC.index("\n") + 1))
    feats = _pair_feats(DOC, line1, line2)
    assert feats.get("pair:type_pair=incident|incident") == 1.0
    assert any(k.startswith("pair:anchor_shared=") for k in feats)
    # finding (no numerals) -> sanction (amount): one-sided numeric shape
    assert "pair:num_only_right" in feats
    assert "pair:date_one_sided" in feats


@pytest.mark.no_db
def test_v5d_date_ordering_direction():
    line2 = (DOC.index("\n") + 1, DOC.index("\n", DOC.index("\n") + 1))
    line3_start = DOC.index("An earlier")
    line3 = (line3_start, len(DOC) - 1)
    feats = _pair_feats(DOC, line2, line3, "incident", "incident")
    assert "pair:date_left_later" in feats
    reverse = _pair_feats(DOC, line3, line2, "incident", "incident")
    assert "pair:date_right_later" in reverse


@pytest.mark.no_db
def test_mask_strips_lexical_but_keeps_abstract():
    line1 = (0, DOC.index("\n"))
    line2 = (DOC.index("\n") + 1, DOC.index("\n", DOC.index("\n") + 1))
    feats = _pair_feats(DOC, line1, line2)
    masked = mask_lexical_pair_features(feats)
    assert not any(k.startswith(("left_claim:", "right_claim:", "between:"))
                   for k in masked)
    assert "pair:type_pair=incident|incident" in masked
    assert any(k.startswith("pair:anchor_") for k in masked)


def _tiny_rows():
    return [{
        "example_id": "v5d-tiny-1",
        "source_id": "v5d-tiny-1",
        "source_text": DOC,
        "entities": [], "dates": [],
        "claims": [
            {"start_char": 0, "end_char": DOC.index("\n"),
             "evidence_start_char": 0, "evidence_end_char": DOC.index("\n"),
             "claim_type": "incident"},
            {"start_char": DOC.index("\n") + 1,
             "end_char": DOC.index("\n", DOC.index("\n") + 1),
             "evidence_start_char": DOC.index("\n") + 1,
             "evidence_end_char": DOC.index("\n", DOC.index("\n") + 1),
             "claim_type": "incident"},
        ],
        "relations": [{"left_claim_index": 0, "right_claim_index": 1,
                       "relation_type": "supports"}],
    }]


@pytest.mark.no_db
def test_relation_head_trains_with_masked_copies_and_upgrade_roundtrip(tmp_path):
    rows = _tiny_rows()
    model, diag = train_relation_head_v5d(rows, epochs=4, seed=7)
    assert diag["masked_copy_per_sample"] is True
    assert diag["relation_training_samples_v5d"] == 4  # 2 ordered pairs x 2 copies
    assert set(model.labels) == {"none", "supports"}

    base = load_v5b(V5C_MODEL)
    v5d = upgrade_v5b_to_v5d(base, model)
    assert isinstance(v5d, ArgusUniversalNeuralExtractorV5D)
    assert v5d.config.relation_threshold == V5D_RELATION_THRESHOLD
    assert v5d.relation_featurizer is relation_pair_features_v5d
    assert v5d.boundary_weights == base.boundary_weights

    save_v5d(v5d, tmp_path / "model_v5d")
    loaded = load_v5d(tmp_path / "model_v5d")
    assert isinstance(loaded, ArgusUniversalNeuralExtractorV5D)
    assert loaded.config.relation_threshold == V5D_RELATION_THRESHOLD
    assert loaded.relation_model.labels == v5d.relation_model.labels
    result = loaded.extract_document(DOC, "v5d-roundtrip")
    assert result.source_hash


@pytest.mark.no_db
def test_endpoint_eval_dispatches_extractor_featurizer():
    import inspect

    from argus_neural import relation_endpoint_eval_v5c

    source = inspect.getsource(relation_endpoint_eval_v5c.relation_endpoint_eval)
    assert 'getattr(extractor, "relation_featurizer"' in source


@pytest.mark.no_db
def test_v5d_runtime_file_passes_source_and_lexicon_audit():
    text = (ROOT / "argus_neural" / "universal_extractor_v5d.py").read_text(encoding="utf-8")
    audit = scan_text_for_runtime_audit({"argus_neural/universal_extractor_v5d.py": text})
    assert audit["verdict"].endswith("PASS_NO_SOURCE_SPECIFIC_PATHS"), audit
