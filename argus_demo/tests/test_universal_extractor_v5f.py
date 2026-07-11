"""V5F tests: orthographic window variants, boundary-adjacency features,
train-side supersedes corpus growth."""
from __future__ import annotations

import pytest

from argus_neural.public_doc_features_v5b import shape_features
from argus_neural.public_doc_label_feedback import rows_by_split
from argus_neural.span_utils import Span, enumerate_token_windows
from argus_neural.true_public_annotations_v5c import true_public_gold_docs_v5c


def _windows(text, max_tokens=4):
    return {s.as_tuple() for s in enumerate_token_windows(text, max_tokens=max_tokens)}


def _feats(text, lo, hi):
    return shape_features(text, Span(lo, hi), prefix="e")


@pytest.mark.no_db
def test_window_variant_dotted_abbreviation():
    text = "The fine hit Clearview AI Inc. in 2022."
    wins = _windows(text)
    lo = text.find("Clearview")
    assert (lo, text.find("Inc.") + 4) in wins   # period-inclusive variant
    assert (lo, text.find("Inc.") + 3) in wins   # plain token window still there


@pytest.mark.no_db
def test_window_variant_multi_dot_suffix():
    text = "Pioneer Hi-Bred Italia Sementi s.r.l. was fined."
    wins = _windows(text, max_tokens=8)
    assert (0, text.find("s.r.l.") + 6) in wins


@pytest.mark.no_db
def test_window_variant_possessive_strip():
    text = "Meta's claim about the license was disputed."
    wins = _windows(text)
    assert (0, 4) in wins        # "Meta" out of "Meta's"
    assert (0, 6) in wins        # the raw token window survives


@pytest.mark.no_db
def test_windows_unique():
    text = "Alpha Beta Inc. and Gamma's tools."
    spans = enumerate_token_windows(text, max_tokens=4)
    assert len(spans) == len({s.as_tuple() for s in spans})


@pytest.mark.no_db
def test_right_adjacent_corp_suffix_cue():
    text = "The Pioneer Hi-Bred Italia Sementi s.r.l. site."
    lo = text.find("Pioneer")
    hi = text.find("Sementi") + len("Sementi")
    feats = _feats(text, lo, hi)                 # truncated before the suffix
    assert "e:right_adjacent_corp_suffix" in feats
    full = _feats(text, lo, text.find("s.r.l.") + 6)
    assert "e:right_adjacent_corp_suffix" not in full
    assert "e:dotted_tail" in full


@pytest.mark.no_db
def test_next_char_dot_marks_truncated_abbreviation():
    text = "Sued by Clearview AI Inc. yesterday."
    lo = text.find("Clearview")
    truncated = _feats(text, lo, text.find("Inc.") + 3)
    assert "e:next_char_dot" in truncated
    complete = _feats(text, lo, text.find("Inc.") + 4)
    assert "e:next_char_dot" not in complete


@pytest.mark.no_db
def test_right_adjacent_titlecase_fires_for_digit_final_span():
    text = "We ran Llama 3.1 8B Instruct on the suite."
    lo = text.find("Llama")
    short = _feats(text, lo, text.find("8B") + 2)
    assert "e:right_adjacent_titlecase" in short  # "Instruct" continues the name
    full = _feats(text, lo, text.find("Instruct") + len("Instruct"))
    assert "e:right_adjacent_titlecase" not in full


@pytest.mark.no_db
def test_left_adjacent_titlecase_but_not_across_comma():
    text = "The Hellenic Data Protection Authority ruled. Consequently, Clearview appealed."
    lo = text.find("Data Protection Authority")
    dropped = _feats(text, lo, lo + len("Data Protection Authority"))
    assert "e:left_adjacent_titlecase" in dropped  # "Hellenic" was cut off
    clo = text.find("Clearview")
    clean = _feats(text, clo, clo + len("Clearview"))
    assert "e:left_adjacent_titlecase" not in clean  # comma breaks adjacency
    wide = _feats(text, text.find("Consequently"), clo + len("Clearview"))
    assert "e:internal_comma" in wide


@pytest.mark.no_db
def test_possessive_and_honorific_tail_cues():
    text = "Anya Proops KC cited Meta's letter."
    assert "e:possessive_tail" in _feats(text, text.find("Meta"), text.find("Meta's") + 6)
    assert "e:tail_short_allcaps" in _feats(text, 0, text.find("KC") + 2)


@pytest.mark.no_db
def test_cnil_2022_supersedes_added_convention_consistent():
    doc = next(d for d in true_public_gold_docs_v5c()
               if d["v5b_doc_key"] == "cnil_2022_clearview_pr")
    sups = [r for r in doc["relations"] if r["relation_type"] == "supersedes"]
    assert len(sups) == 1
    left = doc["claims"][sups[0]["left_claim_index"]]
    right = doc["claims"][sups[0]["right_claim_index"]]
    # later enforcement step (fine) supersedes the earlier formal notice
    assert left["value"] == "20 million euros"
    assert right["value"] == "formal notice"
    train_supersedes = sum(1 for r in rows_by_split()["train"]
                           for rel in r["relations"]
                           if rel["relation_type"] == "supersedes")
    assert train_supersedes >= 2
