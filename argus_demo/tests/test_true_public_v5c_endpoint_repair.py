"""V5C tests: annotation validity, negative-sampling semantics, endpoint evals,
HTML converter source-agnosticism, boundary-ranker serialization."""
from __future__ import annotations

import inspect
from functools import lru_cache

import pytest

from argus_neural.html_text import html_to_text
from argus_neural.public_doc_label_feedback import (
    HeldoutLeakageError,
    heldout_hashes,
    rows_by_split,
    training_rows_with_heldout_guard,
)
from argus_neural.segmentation_dataset import validate_v3_row
from argus_neural.structural_segmenter import segment_document
from argus_neural.universal_extractor_v3 import structural_candidate_spans

V5C_MODEL = ("artifacts/universal_neural_extractor_v5c_exhaustive_label_expansion_endpoint_repair"
             "/model_v5c")


@lru_cache(maxsize=1)
def _splits():
    return rows_by_split()


@pytest.mark.no_db
def test_true_public_annotations_valid_and_reachable():
    for split, rows in _splits().items():
        for row in rows:
            validate_v3_row(row)
            assert row["annotation_completeness"] in {"partial", "exhaustive"}
            cands = {(s.start, s.end) for s, _, _ in
                     structural_candidate_spans(row["source_text"],
                                                segment_document(row["source_text"]))}
            for claim in row["claims"]:
                assert (claim["start_char"], claim["end_char"]) in cands


@pytest.mark.no_db
def test_split_has_no_source_hash_leakage():
    splits = _splits()
    hashes = {s: {r["source_hash"] for r in rows} for s, rows in splits.items()}
    assert not (hashes["train"] & hashes["dev"])
    assert not (hashes["train"] & hashes["heldout"])
    assert not (hashes["dev"] & hashes["heldout"])


@pytest.mark.no_db
def test_heldout_guard_still_refuses_heldout():
    splits = _splits()
    ok = splits["train"] + splits["dev"]
    assert training_rows_with_heldout_guard(ok) == ok
    with pytest.raises(HeldoutLeakageError):
        training_rows_with_heldout_guard(ok + splits["heldout"][:1])


@pytest.mark.no_db
def test_exhaustive_vs_partial_completeness_present():
    completeness = {r["v5b_doc_key"]: r["annotation_completeness"]
                    for rows in _splits().values() for r in rows}
    exhaustive = [k for k, v in completeness.items() if v == "exhaustive"]
    partial = [k for k, v in completeness.items() if v == "partial"]
    assert len(exhaustive) >= 5
    assert len(partial) >= 1  # partial docs must still exist (unlabeled=unknown)


@pytest.mark.no_db
def test_partial_docs_not_used_as_negatives_in_training():
    """The trainer must skip unlabeled candidates on partial docs."""
    from argus_neural.universal_training_v3 import train_universal_extractor_v3

    src = inspect.getsource(train_universal_extractor_v3)
    assert "annotation_completeness" in src
    assert "partial" in src
    # a partial doc with unlabeled claim-like sentences must not inflate the
    # 'none' training count vs the same doc marked exhaustive
    splits = _splits()
    partial_doc = next(r for rows in splits.values() for r in rows
                       if r["annotation_completeness"] == "partial" and r["claims"])
    exhaustive_variant = dict(partial_doc)
    exhaustive_variant["annotation_completeness"] = "exhaustive"
    _, diag_partial = train_universal_extractor_v3([partial_doc], epochs=1, seed=1)
    _, diag_exhaustive = train_universal_extractor_v3([exhaustive_variant], epochs=1, seed=1)
    assert diag_exhaustive["claim_training_samples"] > diag_partial["claim_training_samples"]


@pytest.mark.no_db
def test_html_converter_is_source_agnostic():
    src = inspect.getsource(html_to_text)
    module_src = inspect.getsource(__import__("argus_neural.html_text", fromlist=["x"]))
    for term in ("edpb", "cnil", "garante", "clearview", "llama", "europa.eu",
                 "verisure", "pioneer", "hdpa", "document_style", "source_type"):
        assert term not in module_src.lower()
    a = html_to_text("<html><body><nav>Menu</nav><p>Fine of EUR 20 million.</p>"
                     "<script>x=1</script></body></html>")
    assert "Fine of EUR 20 million." in a
    assert "Menu" not in a and "x=1" not in a


@pytest.mark.no_db
def test_entity_and_relation_endpoint_evals_run():
    from argus_neural.entity_boundary_eval_v5c import entity_boundary_eval
    from argus_neural.relation_endpoint_eval_v5c import relation_endpoint_eval
    from argus_neural.claim_endpoint_eval_v5c import claim_endpoint_eval
    from argus_neural.universal_extractor_v5b import load_v5b

    model = load_v5b(V5C_MODEL)
    rows = _splits()["dev"]
    ent = entity_boundary_eval(model, rows)
    assert "exact_span" in ent and "relaxed_iou50" in ent and "boundary" in ent
    clm = claim_endpoint_eval(model, rows)
    assert "false_negatives_from_segmentation" in clm
    rel = relation_endpoint_eval(model, rows)
    assert "gold_endpoint" in rel and "predicted_endpoint" in rel
    assert rel["blocker"].startswith("RELATION_BLOCKER")


@pytest.mark.no_db
def test_boundary_ranker_serialization_roundtrip(tmp_path):
    from argus_neural.universal_extractor_v5b import load_v5b, save_v5b

    model = load_v5b(V5C_MODEL)
    assert model.boundary_weights  # V5C model has a ranker
    save_v5b(model, tmp_path / "m")
    reloaded = load_v5b(tmp_path / "m")
    assert reloaded.boundary_weights == model.boundary_weights


@pytest.mark.no_db
def test_old_model_without_ranker_falls_back():
    """Backward compat: a V3 model (no boundary_model key) still loads/runs."""
    from argus_neural.universal_extractor_v5b import load_v5b

    model = load_v5b("artifacts/universal_neural_extractor_v3_segmentation_threading/model")
    assert model.boundary_weights is None
    row = _splits()["dev"][0]
    result = model.extract_document(row["source_text"], row["source_id"])
    assert result.diagnostics["metadata_controls_extraction"] is False
