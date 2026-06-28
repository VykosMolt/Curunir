"""Technical-domain claim proposers (Task 2 domain-transfer slice).

These exercise the benchmark_score / license_claim / weights_available / model_released
predicates added for the contested-technical corpus, plus a relation negative control
(same benchmark, different setting is NOT a contradiction). Read-only proposal
generation; claims are applied only through the existing audited actions.
"""
from __future__ import annotations

from argus import actions, claim_proposals

_ENTITY_TYPES = {"model", "benchmark", "license", "organization", "company"}


def _doc(text: str, mentions: list[tuple[str, str]], doc_id: str) -> str:
    sid = actions.create_source("Tech Source", "vendor")["source_id"]
    dv = str(actions.ingest_text_document(
        text, title="Tech Doc", source_id=sid,
        metadata={"doc_id": doc_id, "manifest": {}})["document_version_id"])
    cache: dict[tuple[str, str], str] = {}
    cursor: dict[str, int] = {}
    for needle, mtype in mentions:
        start = text.index(needle, cursor.get(needle, 0))
        cursor[needle] = start + len(needle)  # next identical needle anchors later
        span = actions.create_evidence_span(
            dv, start_char=start, end_char=start + len(needle))["evidence_span_id"]
        mid = actions.create_mention(dv, needle, mtype, evidence_span_id=span)["mention_id"]
        if mtype in _ENTITY_TYPES:
            key = (needle, mtype)
            if key not in cache:
                cache[key] = actions.create_entity(mtype, needle)["entity_id"]
            actions.assign_mention_to_entity(mid, cache[key])
    return dv


def _by_pred(db_conn, predicate: str) -> list[dict]:
    return [p for p in claim_proposals.propose_claims(db_conn)["proposals"]
            if p["predicate"] == predicate]


# ---- benchmark_score -------------------------------------------------------

def test_benchmark_score_clean(db_conn):
    _doc("Llama 3.1 405B scores 87.3 on MMLU (5-shot).",
         [("Llama 3.1 405B", "model"), ("MMLU", "benchmark")], "bs-clean")
    bs = _by_pred(db_conn, "benchmark_score")
    assert bs and bs[0]["value"]["score"] == 87.3
    assert bs[0]["value"]["setting"] == "5-shot"
    assert bs[0]["object_surface"] == "MMLU"
    assert bs[0]["triage_level"] == "clean"
    assert bs[0]["predication_flags"] == []


def test_benchmark_score_setting_ambiguous_flagged(db_conn):
    # a score with no stated split/shots is not comparable -> flagged, not suppressed
    _doc("Llama 3.1 405B scores 87.3 on MMLU.",
         [("Llama 3.1 405B", "model"), ("MMLU", "benchmark")], "bs-amb")
    bs = _by_pred(db_conn, "benchmark_score")
    assert bs and bs[0]["value"]["score"] == 87.3
    assert "metric_setting_ambiguous" in bs[0]["predication_flags"]
    assert bs[0]["triage_level"] == "review"


# ---- license_claim ---------------------------------------------------------

def test_license_claim_community_license_clean(db_conn):
    _doc("Use of Llama 3.1 405B is governed by the Llama 3.1 Community License.",
         [("Llama 3.1 405B", "model"), ("Llama 3.1 Community License", "license")], "lic-clean")
    lc = _by_pred(db_conn, "license_claim")
    assert lc and lc[0]["value"].get("license") == "Llama 3.1 Community License"
    assert "license_scope_ambiguous" not in lc[0]["predication_flags"]


def test_license_claim_open_source_scope_ambiguous(db_conn):
    _doc("Llama 3.1 405B is the first frontier-level open source AI model.",
         [("Llama 3.1 405B", "model"), ("open source", "license")], "lic-os")
    lc = _by_pred(db_conn, "license_claim")
    assert lc and lc[0]["value"]["open_source"] is True
    assert "license_scope_ambiguous" in lc[0]["predication_flags"]
    assert lc[0]["triage_level"] in ("review", "high_review")


def test_license_claim_negated_is_false_and_likely_negative(db_conn):
    _doc("Llama 3.1 405B is still not Open Source.",
         [("Llama 3.1 405B", "model"), ("Open Source", "license")], "lic-neg")
    lc = _by_pred(db_conn, "license_claim")
    assert lc and lc[0]["value"]["open_source"] is False
    assert "negation_near_trigger" in lc[0]["predication_flags"]
    assert lc[0]["triage_level"] == "likely_negative"


# ---- weights_available -----------------------------------------------------

def test_weights_available_true(db_conn):
    _doc("The Llama 3.1 405B model weights are available to download.",
         [("Llama 3.1 405B", "model")], "w-true")
    wa = _by_pred(db_conn, "weights_available")
    assert wa and wa[0]["value"]["available"] is True


def test_weights_not_released_is_false(db_conn):
    _doc("The Llama 3.1 405B weights are not released.",
         [("Llama 3.1 405B", "model")], "w-false")
    wa = _by_pred(db_conn, "weights_available")
    assert wa and wa[0]["value"]["available"] is False


# ---- relation negative control ---------------------------------------------

def test_different_setting_is_not_contradiction(db_conn):
    # same model + same benchmark, two DIFFERENT settings -> distinct scopes. The
    # conservative relation proposer must NOT manufacture a contradiction between them.
    _doc("Llama 3.1 405B scores 87.3 on MMLU (5-shot). "
         "Llama 3.1 405B scores 88.6 on MMLU (0-shot, CoT).",
         [("Llama 3.1 405B", "model"), ("MMLU", "benchmark"),
          ("Llama 3.1 405B", "model"), ("MMLU", "benchmark")], "negctrl")
    bs = _by_pred(db_conn, "benchmark_score")
    settings = sorted(p["value"].get("setting") for p in bs)
    assert settings == ["0-shot, CoT", "5-shot"]          # two distinct scopes
    # apply both, then run the relation proposer: it must not pair them as contradicts
    from argus import claim_relation_proposals
    claim_proposals.apply_claim_proposals([dict(p, accepted=True) for p in bs])
    rels = claim_relation_proposals.propose_claim_relations(db_conn)["proposals"]
    assert not any(r.get("relation") == "contradicts" for r in rels)
