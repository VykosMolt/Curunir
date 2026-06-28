"""Scope-aware technical relation proposer.

These exercise the technical path added to `claim_relation_proposals` for the
predicates `benchmark_score`, `license_claim`, `weights_available`,
`model_released`. The proposer is read-only and deterministic; it must:

  * find duplicates / contradictions / supersessions when subject, object, scope
    and value make the relation defensible, and
  * REFUSE to mint contradictions across different benchmark settings, different
    benchmarks, different license components, or different model entities.

Like the other proposal tests these build temporary fixtures via `actions` and
run against the truncated test DB. Nothing here writes a relation except the two
apply-path tests, which use the audited apply function.
"""
from __future__ import annotations

import itertools

from argus import actions, claim_relation_proposals as crp

_seq = itertools.count()


# ---- fixture helpers --------------------------------------------------------

def _doc(doc_id: str, *, published_at: str | None = None, source_name: str | None = None) -> str:
    """A fresh document_version under a (possibly shared-by-name) source."""
    source_id = actions.create_source(source_name or f"src-{doc_id}", "publisher")["source_id"]
    res = actions.ingest_text_document(
        f"body of {doc_id} #{next(_seq)}", title=doc_id, source_id=source_id,
        published_at=published_at, metadata={"doc_id": doc_id, "manifest": {}},
    )
    return str(res["document_version_id"])


def _entity(entity_type: str, name: str) -> str:
    return str(actions.create_entity(entity_type, name)["entity_id"])


def _claim(dv: str, predicate: str, claim_type: str, claim_text: str, *,
           subject_entity: str | None = None, object_entity: str | None = None,
           value: dict | None = None, qualifiers: dict | None = None) -> str:
    """Create a technical claim whose subject/object entity caches are resolved
    from anchoring mentions (so the proposer sees real entity ids)."""
    span = actions.create_evidence_span(dv, exact_quote=f"q{next(_seq)}:{claim_text[:50]}")["evidence_span_id"]
    subj_mid = obj_mid = None
    if subject_entity:
        s = actions.create_evidence_span(dv, exact_quote=f"subj{next(_seq)}")["evidence_span_id"]
        subj_mid = actions.create_mention(dv, "subj", "model", evidence_span_id=s)["mention_id"]
        actions.assign_mention_to_entity(subj_mid, subject_entity)
    if object_entity:
        s = actions.create_evidence_span(dv, exact_quote=f"obj{next(_seq)}")["evidence_span_id"]
        obj_mid = actions.create_mention(dv, "obj", "benchmark", evidence_span_id=s)["mention_id"]
        actions.assign_mention_to_entity(obj_mid, object_entity)
    res = actions.create_claim(
        claim_text, claim_type, predicate, span,
        subject_mention_id=subj_mid, object_mention_id=obj_mid,
        value=value or {}, qualifiers=qualifiers or {},
    )
    return str(res["claim_id"])


def _rels(db_conn, relation: str) -> list[dict]:
    return [p for p in crp.propose_claim_relations(db_conn)["proposals"] if p["relation"] == relation]


def _between(db_conn, relation: str, a: str, b: str) -> list[dict]:
    """Direction-agnostic: proposals relating a and b (either order)."""
    return [p for p in _rels(db_conn, relation)
            if {p["src_claim_id"], p["dst_claim_id"]} == {a, b}]


def _directed(db_conn, relation: str, src: str, dst: str) -> list[dict]:
    """Direction-aware: proposals with exactly this src -> dst."""
    return [p for p in _rels(db_conn, relation)
            if p["src_claim_id"] == src and p["dst_claim_id"] == dst]


# ---- 1: benchmark duplicate positive ----------------------------------------

def test_benchmark_duplicate_same_setting_same_score(db_conn):
    model = _entity("model", "Llama 3.1 405B")
    mmlu = _entity("benchmark", "MMLU")
    val = {"score": 87.3, "unit": "percent", "metric": "accuracy", "setting": "5-shot"}
    q = {"setting": "5-shot", "benchmark": "MMLU"}
    a = _claim(_doc("card"), "benchmark_score", "benchmark_result", "card: 87.3 MMLU 5-shot",
               subject_entity=model, object_entity=mmlu, value=val, qualifiers=q)
    b = _claim(_doc("paper"), "benchmark_score", "benchmark_result", "paper: 87.3 MMLU 5-shot",
               subject_entity=model, object_entity=mmlu, value=val, qualifiers=q)
    dups = _between(db_conn, "duplicates", a, b)
    assert len(dups) == 1
    assert dups[0]["heuristic_name"] == "benchmark_score_duplicate_same_scope"
    assert dups[0]["triage"] == "clean"
    # and NOT a contradiction
    assert _between(db_conn, "contradicts", a, b) == []


# ---- 2: benchmark different setting negative --------------------------------

def test_benchmark_different_setting_no_relation(db_conn):
    model = _entity("model", "Llama 3.1 405B")
    mmlu = _entity("benchmark", "MMLU")
    a = _claim(_doc("p", published_at="2024-07-23"), "benchmark_score", "benchmark_result",
               "87.3 MMLU 5-shot", subject_entity=model, object_entity=mmlu,
               value={"score": 87.3, "metric": "accuracy", "setting": "5-shot"},
               qualifiers={"setting": "5-shot"})
    b = _claim(_doc("p2", published_at="2024-07-23"), "benchmark_score", "benchmark_result",
               "88.6 MMLU 0-shot CoT", subject_entity=model, object_entity=mmlu,
               value={"score": 88.6, "metric": "accuracy", "setting": "0-shot, CoT"},
               qualifiers={"setting": "0-shot, CoT"})
    assert _between(db_conn, "contradicts", a, b) == []
    assert _between(db_conn, "duplicates", a, b) == []
    assert _between(db_conn, "supersedes", a, b) == []


# ---- 3: benchmark incompatible same-scope positive --------------------------

def test_benchmark_same_setting_incompatible_score_contradicts(db_conn):
    model = _entity("model", "Llama 3.1 405B")
    mmlu = _entity("benchmark", "MMLU")
    q = {"setting": "5-shot", "benchmark": "MMLU"}
    a = _claim(_doc("vendor"), "benchmark_score", "benchmark_result", "87.3 MMLU 5-shot",
               subject_entity=model, object_entity=mmlu,
               value={"score": 87.3, "metric": "accuracy", "setting": "5-shot"}, qualifiers=q)
    b = _claim(_doc("indep"), "benchmark_score", "benchmark_result", "72.0 MMLU 5-shot",
               subject_entity=model, object_entity=mmlu,
               value={"score": 72.0, "metric": "accuracy", "setting": "5-shot"}, qualifiers=q)
    cons = _between(db_conn, "contradicts", a, b)
    assert len(cons) == 1
    assert cons[0]["heuristic_name"] == "benchmark_score_contradiction_same_scope"
    assert cons[0]["scope"]["value_a"] != cons[0]["scope"]["value_b"]
    assert _between(db_conn, "duplicates", a, b) == []


# ---- 4: different benchmark negative (MMLU vs MMLU-Pro) ----------------------

def test_benchmark_different_benchmark_no_contradiction(db_conn):
    model = _entity("model", "Llama 3.1 405B")
    mmlu = _entity("benchmark", "MMLU")
    mmlu_pro = _entity("benchmark", "MMLU-Pro")
    a = _claim(_doc("a"), "benchmark_score", "benchmark_result", "87.3 MMLU 5-shot",
               subject_entity=model, object_entity=mmlu,
               value={"score": 87.3, "metric": "accuracy", "setting": "5-shot"},
               qualifiers={"setting": "5-shot"})
    b = _claim(_doc("b"), "benchmark_score", "benchmark_result", "73.3 MMLU-Pro 5-shot",
               subject_entity=model, object_entity=mmlu_pro,
               value={"score": 73.3, "metric": "accuracy", "setting": "5-shot"},
               qualifiers={"setting": "5-shot"})
    assert _between(db_conn, "contradicts", a, b) == []
    assert _between(db_conn, "duplicates", a, b) == []


# ---- 5: license contradiction positive --------------------------------------

def test_license_open_source_polarity_contradicts(db_conn):
    model = _entity("model", "Llama 3.1 405B")
    osource = _entity("concept", "Open Source")
    a = _claim(_doc("meta", published_at="2024-07-23"), "license_claim", "license_assertion",
               "Llama 3.1 is open source", subject_entity=model, object_entity=osource,
               value={"claim": "open_source", "open_source": True})
    b = _claim(_doc("osi", published_at="2025-02-18"), "license_claim", "license_assertion",
               "Llama 3.1 is not open source", subject_entity=model, object_entity=osource,
               value={"claim": "open_source", "open_source": False})
    cons = _between(db_conn, "contradicts", a, b)
    assert len(cons) == 1
    assert cons[0]["heuristic_name"] == "license_claim_contradiction_same_scope"
    assert cons[0]["triage"] == "review"


# ---- 6: license scope negative (code vs weights component) -------------------

def test_license_different_component_no_contradiction(db_conn):
    model = _entity("model", "Llama 3.1 405B")
    osource = _entity("concept", "Open Source")
    a = _claim(_doc("code"), "license_claim", "license_assertion", "code is open",
               subject_entity=model, object_entity=osource,
               value={"open_source": True}, qualifiers={"component": "code"})
    b = _claim(_doc("weights"), "license_claim", "license_assertion", "weights not open",
               subject_entity=model, object_entity=osource,
               value={"open_source": False}, qualifiers={"component": "weights"})
    assert _between(db_conn, "contradicts", a, b) == []
    # control: same component DOES contradict
    c = _claim(_doc("weights2"), "license_claim", "license_assertion", "weights are open",
               subject_entity=model, object_entity=osource,
               value={"open_source": True}, qualifiers={"component": "weights"})
    assert len(_between(db_conn, "contradicts", b, c)) == 1


# ---- 7: weights availability contradiction positive -------------------------

def test_weights_available_contradicts(db_conn):
    model = _entity("model", "Llama 3.1 405B")
    a = _claim(_doc("hf"), "weights_available", "availability_assertion", "weights available",
               subject_entity=model, value={"available": True, "form": "open-weights"})
    b = _claim(_doc("api"), "weights_available", "availability_assertion", "api only",
               subject_entity=model, value={"available": False, "form": "api-only"})
    cons = _between(db_conn, "contradicts", a, b)
    assert len(cons) == 1
    assert cons[0]["heuristic_name"] == "weights_available_contradiction_same_scope"


# ---- 8: model version negative (Llama 2 vs Llama 3.1) -----------------------

def test_different_model_version_no_contradiction(db_conn):
    llama2 = _entity("model", "Llama 2")
    llama31 = _entity("model", "Llama 3.1 405B")
    osource = _entity("concept", "Open Source")
    a = _claim(_doc("osi2"), "license_claim", "license_assertion", "Llama 2 not open source",
               subject_entity=llama2, object_entity=osource, value={"open_source": False})
    b = _claim(_doc("meta"), "license_claim", "license_assertion", "Llama 3.1 open source",
               subject_entity=llama31, object_entity=osource, value={"open_source": True})
    # different subject entities => NOT a contradiction (no family-level merge)
    assert _between(db_conn, "contradicts", a, b) == []


# ---- 9: supersession positive (same assessor, later date, family continuity) -

def test_supersession_same_source_later_date(db_conn):
    llama2 = _entity("model", "Llama 2")
    llama31 = _entity("model", "Llama 3.1 405B")
    osource = _entity("concept", "Open Source")
    osi = "Open Source Initiative"
    earlier = _claim(_doc("osi-2023", published_at="2023-07-20", source_name=osi),
                     "license_claim", "license_assertion", "Llama 2 is not open source",
                     subject_entity=llama2, object_entity=osource,
                     value={"open_source": False}, qualifiers={"date": "2023-07-20"})
    later = _claim(_doc("osi-2025", published_at="2025-02-18", source_name=osi),
                   "license_claim", "license_assertion",
                   "Llama 3.x is still not Open Source", subject_entity=llama31,
                   object_entity=osource, value={"open_source": False},
                   qualifiers={"date": "2025-02-18"})
    sups = _directed(db_conn, "supersedes", later, earlier)
    assert len(sups) == 1
    assert sups[0]["heuristic_name"] == "technical_updated_assessment_supersedes"
    # directional: the earlier claim does not supersede the later one
    assert _directed(db_conn, "supersedes", earlier, later) == []


# ---- 10: supersession negative (different source, no continuity language) ----

def test_no_supersession_when_unrelated_sources(db_conn):
    llama2 = _entity("model", "Llama 2")
    llama31 = _entity("model", "Llama 3.1 405B")
    osource = _entity("concept", "Open Source")
    earlier = _claim(_doc("blog-a", published_at="2023-07-20", source_name="Some Blog"),
                     "license_claim", "license_assertion", "Llama 2 is open",
                     subject_entity=llama2, object_entity=osource, value={"open_source": True})
    later = _claim(_doc("blog-b", published_at="2025-02-18", source_name="Other Blog"),
                   "license_claim", "license_assertion", "Llama 3.1 is open",
                   subject_entity=llama31, object_entity=osource, value={"open_source": True})
    # different source orgs AND no correction/update wording => no supersession
    assert _between(db_conn, "supersedes", later, earlier) == []


# ---- 11: the live Llama slice reproduced end-to-end -------------------------

def _llama_slice() -> dict[str, str]:
    """Mirror the curated technical slice: the Llama 3.1 family is one entity,
    Llama 2 is separate, 'Open Source' and 'MMLU' are shared objects."""
    llama31 = _entity("model", "Llama 3.1 405B")
    llama2 = _entity("model", "Llama 2")
    mmlu = _entity("benchmark", "MMLU")
    osource = _entity("concept", "Open Source")
    osi = "Open Source Initiative"
    ids = {}
    ids["bench_card"] = _claim(_doc("tech-llama31-405b-model-card", published_at="2024-07-23"),
        "benchmark_score", "benchmark_result", "Llama 3.1 405B scores 87.3 on MMLU (5-shot).",
        subject_entity=llama31, object_entity=mmlu,
        value={"score": 87.3, "unit": "percent", "metric": "accuracy", "setting": "5-shot"},
        qualifiers={"setting": "5-shot", "benchmark": "MMLU"})
    ids["bench_paper"] = _claim(_doc("tech-llama3-herd-paper", published_at="2024-07-23"),
        "benchmark_score", "benchmark_result", "Llama 3 scores 87.3 on MMLU (5-shot).",
        subject_entity=llama31, object_entity=mmlu,
        value={"score": 87.3, "unit": "percent", "metric": "accuracy", "setting": "5-shot"},
        qualifiers={"setting": "5-shot", "benchmark": "MMLU"})
    ids["bench_cot"] = _claim(_doc("tech-llama3-herd-paper", published_at="2024-07-23"),
        "benchmark_score", "benchmark_result", "Llama 3.1 405B scores 88.6 on MMLU (0-shot, CoT).",
        subject_entity=llama31, object_entity=mmlu,
        value={"score": 88.6, "unit": "percent", "metric": "accuracy", "setting": "0-shot, CoT"},
        qualifiers={"setting": "0-shot, CoT", "benchmark": "MMLU"})
    ids["meta_open"] = _claim(_doc("tech-zuckerberg-open-source-letter", published_at="2024-07-23",
        source_name="Meta (Mark Zuckerberg)"), "license_claim", "license_assertion",
        "Llama 3.1 is described as open source.", subject_entity=llama31, object_entity=osource,
        value={"claim": "open_source", "open_source": True}, qualifiers={"date": "July 23, 2024"})
    ids["osi_2025"] = _claim(_doc("tech-osi-llama-not-open-source-2025", published_at="2025-02-18",
        source_name="Open Source Initiative"), "license_claim", "license_assertion",
        "Llama 3.1 405B is not open source (OSI).", subject_entity=llama31, object_entity=osource,
        value={"claim": "open_source", "open_source": False}, qualifiers={"date": "2025-02-18"})
    ids["osi_2023"] = _claim(_doc("tech-osi-llama2-not-open-source-2023", published_at="2023-07-20",
        source_name="Open Source Initiative"), "license_claim", "license_assertion",
        "Llama 2 is not open source (OSI).", subject_entity=llama2, object_entity=osource,
        value={"claim": "open_source", "open_source": False}, qualifiers={"date": "2023-07-20"})
    return ids


def test_live_technical_slice_reproduced(db_conn):
    ids = _llama_slice()
    # 1) MMLU 87.3 5-shot duplicate across model card + technical report
    assert len(_between(db_conn, "duplicates", ids["bench_card"], ids["bench_paper"])) == 1
    # 2) Meta vs OSI open-source contradiction
    assert len(_between(db_conn, "contradicts", ids["meta_open"], ids["osi_2025"])) == 1
    # 3) OSI 2025 supersedes OSI 2023 (same assessor, later date, llama family)
    assert len(_directed(db_conn, "supersedes", ids["osi_2025"], ids["osi_2023"])) == 1
    # 4) refuses 87.3 5-shot vs 88.6 0-shot CoT (different setting) -- ANY relation
    payload = crp.propose_claim_relations(db_conn)
    cot_pairs = [p for p in payload["proposals"]
                 if {p["src_claim_id"], p["dst_claim_id"]} == {ids["bench_card"], ids["bench_cot"]}]
    assert cot_pairs == []
    # negative control: no Llama 2 vs Llama 3.1 open-source contradiction
    assert _between(db_conn, "contradicts", ids["meta_open"], ids["osi_2023"]) == []
    # exactly three technical relations in total, one of each type
    s = payload["summary"]
    assert s["technical_relation_proposals"] == 3
    assert s["technical_proposals_by_relation"] == {"duplicates": 1, "contradicts": 1, "supersedes": 1}


# ---- 12: apply path is idempotent regardless of curated direction -----------

def test_symmetric_apply_is_direction_idempotent(db_conn):
    model = _entity("model", "Llama 3.1 405B")
    osource = _entity("concept", "Open Source")
    a = _claim(_doc("meta", published_at="2024-07-23"), "license_claim", "license_assertion",
               "open source", subject_entity=model, object_entity=osource,
               value={"open_source": True})
    b = _claim(_doc("osi", published_at="2025-02-18"), "license_claim", "license_assertion",
               "not open source", subject_entity=model, object_entity=osource,
               value={"open_source": False})
    con = _rels(db_conn, "contradicts")[0]
    first = crp.apply_claim_relation_proposals([dict(con, accepted=True)])
    assert first["claim_relations_created"] == 1
    # re-apply the SAME edge with src/dst swapped -> recognised as already present
    swapped = dict(con, accepted=True, src_claim_id=con["dst_claim_id"],
                   dst_claim_id=con["src_claim_id"])
    second = crp.apply_claim_relation_proposals([swapped])
    assert second["claim_relations_created"] == 0
    assert second["summary"]["duplicate_existing"] == 1
    with db_conn.cursor() as cur:
        cur.execute("select count(*) as n from claim_relations where tx_to is null")
        assert cur.fetchone()["n"] == 1


# ---- 13: enforcement relations unaffected by the technical gate -------------

def test_enforcement_predicates_still_proposed(db_conn):
    """The legal heuristics must be untouched: a same-regulator reduced_fine still
    supersedes the earlier fine even with the technical gate in place."""
    cnil = actions.create_entity("regulator", "CNIL")["entity_id"]
    dv22 = _doc("fr-cnil-2022", published_at="2022-10-17")
    dv23 = _doc("fr-cnil-2023", published_at="2023-04-13")
    s22 = actions.create_evidence_span(dv22, exact_quote="cnil fine 2022")["evidence_span_id"]
    sm = actions.create_mention(dv22, "CNIL", "organization", evidence_span_id=s22)["mention_id"]
    actions.assign_mention_to_entity(sm, cnil)
    dst = actions.create_claim("French SA fined the company.", "enforcement_action", "issued_fine",
                               s22, subject_mention_id=sm,
                               value={"amount": 20000000, "currency": "EUR"})["claim_id"]
    s23 = actions.create_evidence_span(dv23, exact_quote="cnil reduced 2023")["evidence_span_id"]
    src = actions.create_claim("The fine was reduced to 5200000 EUR.", "enforcement_action",
                               "reduced_fine", s23,
                               value={"amount": 5200000, "currency": "EUR"})["claim_id"]
    sup = [p for p in _rels(db_conn, "supersedes")
           if p["src_claim_id"] == str(src) and p["dst_claim_id"] == str(dst)]
    assert len(sup) == 1
    assert sup[0]["heuristic_name"] == "same_chain_modification_supersedes"


# =====================================================================
# Hardening pass: deeper benchmark / license / weights / release / supersession
# coverage (cross-source vs same-source-correction, openness, temporal, ambiguity).
# =====================================================================

def _bench(dv, score, *, setting="5-shot", metric="accuracy", model=None, bench=None, text=None):
    return _claim(dv, "benchmark_score", "benchmark_result",
                  text or f"{score} on bench {setting}", subject_entity=model, object_entity=bench,
                  value={"score": score, "unit": "percent", "metric": metric, "setting": setting},
                  qualifiers={"setting": setting})


# ---- H1: cross-source same-setting numeric contradiction (independent eval) --

def test_cross_source_same_setting_contradicts(db_conn):
    model = _entity("model", "Model X")
    y = _entity("benchmark", "BenchY")
    a = _bench(_doc("vendor-blog", published_at="2025-01-01", source_name="Vendor"), 90.2,
               model=model, bench=y)
    b = _bench(_doc("independent-eval", published_at="2025-03-01", source_name="Independent Lab"), 84.0,
               model=model, bench=y)
    cons = _between(db_conn, "contradicts", a, b)
    assert len(cons) == 1
    assert cons[0]["heuristic_name"] == "benchmark_score_contradiction_same_scope"
    # different sources => a genuine disagreement, not a same-author correction
    assert _between(db_conn, "supersedes", a, b) == []


# ---- H2: same-source numeric revision is a CORRECTION (supersedes, not contradict)

def test_same_source_benchmark_correction_supersedes_not_contradicts(db_conn):
    model = _entity("model", "Model X")
    y = _entity("benchmark", "BenchY")
    early = _bench(_doc("card-v1", published_at="2025-01-01", source_name="Vendor"), 90.2,
                   model=model, bench=y)
    later = _bench(_doc("card-v2", published_at="2025-06-01", source_name="Vendor"), 88.0,
                   model=model, bench=y)
    # no contradiction (same author revising its own number)
    assert _between(db_conn, "contradicts", early, later) == []
    # later supersedes earlier, marked as a same-artifact correction, clean triage
    sup = _directed(db_conn, "supersedes", later, early)
    assert len(sup) == 1
    assert sup[0]["triage"] == "clean"
    assert sup[0]["scope"]["value_changed"] is True
    assert "correction" in sup[0]["reason"]
    # never the reverse direction
    assert _directed(db_conn, "supersedes", early, later) == []


# ---- H3: metric mismatch (accuracy vs pass@1) is not comparable -------------

def test_benchmark_metric_mismatch_no_contradiction(db_conn):
    model = _entity("model", "Model X")
    y = _entity("benchmark", "BenchY")
    a = _bench(_doc("a"), 87.3, metric="accuracy", model=model, bench=y)
    b = _bench(_doc("b"), 72.0, metric="pass@1", model=model, bench=y)
    assert _between(db_conn, "contradicts", a, b) == []
    assert _between(db_conn, "duplicates", a, b) == []


# ---- H4: ambiguous setting (no shots/split) -> no clean conflict ------------

def test_benchmark_ambiguous_vs_explicit_setting_no_relation(db_conn):
    model = _entity("model", "Model X")
    y = _entity("benchmark", "BenchY")
    # one score has NO setting at all, the other is explicitly 5-shot -> different keys
    a = _claim(_doc("vague"), "benchmark_score", "benchmark_result", "scores 90 on BenchY",
               subject_entity=model, object_entity=y, value={"score": 90.0, "metric": "accuracy"})
    b = _bench(_doc("pinned"), 84.0, setting="5-shot", model=model, bench=y)
    assert _between(db_conn, "contradicts", a, b) == []
    assert _between(db_conn, "duplicates", a, b) == []


def test_benchmark_both_ambiguous_emits_high_review_only(db_conn):
    model = _entity("model", "Model X")
    y = _entity("benchmark", "BenchY")
    a = _claim(_doc("vague-a", source_name="A"), "benchmark_score", "benchmark_result",
               "scores 90 on BenchY", subject_entity=model, object_entity=y,
               value={"score": 90.0, "metric": "accuracy"})
    b = _claim(_doc("vague-b", source_name="B"), "benchmark_score", "benchmark_result",
               "scores 84 on BenchY", subject_entity=model, object_entity=y,
               value={"score": 84.0, "metric": "accuracy"})
    cons = _between(db_conn, "contradicts", a, b)
    assert len(cons) == 1
    assert cons[0]["triage"] == "high_review"
    assert cons[0]["match_keys"].get("flag") == "metric_setting_ambiguous"


# ---- H5: named-license openness mismatch (category-derived) -> high_review ---

def test_named_license_openness_mismatch_contradicts_high_review(db_conn):
    model = _entity("model", "Model X")
    a = _claim(_doc("repo-license", source_name="Repo"), "license_claim", "license_assertion",
               "Apache-2.0", subject_entity=model,
               value={"license": "Apache-2.0", "category": "permissive"},
               qualifiers={"component": "weights"})
    b = _claim(_doc("press", source_name="Press"), "license_claim", "license_assertion",
               "non-commercial custom license", subject_entity=model,
               value={"license": "custom", "category": "non-commercial"},
               qualifiers={"component": "weights"})
    cons = _between(db_conn, "contradicts", a, b)
    assert len(cons) == 1
    assert cons[0]["triage"] == "high_review"
    assert cons[0]["match_keys"]["openness_basis"] == ["category", "category"]


# ---- H6: open weights vs open source are different predicates -> no edge -----

def test_open_weights_vs_open_source_no_contradiction(db_conn):
    model = _entity("model", "Model X")
    osource = _entity("concept", "Open Source")
    w = _claim(_doc("hf"), "weights_available", "availability_assertion", "weights public",
               subject_entity=model, value={"available": True, "form": "open-weights"})
    lic = _claim(_doc("osi"), "license_claim", "license_assertion", "not open source",
                 subject_entity=model, object_entity=osource, value={"open_source": False})
    # different predicates are never compared for contradiction
    assert _between(db_conn, "contradicts", w, lic) == []


# ---- H7: release contradiction at the same temporal scope -------------------

def test_release_contradiction_same_date(db_conn):
    lab = _entity("lab", "Lab Z")
    model = _entity("model", "Model X")
    a = _claim(_doc("blog", published_at="2025-01-01", source_name="Lab Blog"),
               "model_released", "release_assertion", "released", subject_entity=lab,
               object_entity=model, value={"released": True})
    b = _claim(_doc("tracker", published_at="2025-01-01", source_name="Tracker"),
               "model_released", "release_assertion", "not released", subject_entity=lab,
               object_entity=model, value={"released": False})
    assert len(_between(db_conn, "contradicts", a, b)) == 1


# ---- H8: release/availability flip ACROSS dates is a state change, not conflict

def test_release_flip_across_dates_no_contradiction(db_conn):
    lab = _entity("lab", "Lab Z")
    model = _entity("model", "Model X")
    early = _claim(_doc("preview", published_at="2025-01-01", source_name="Lab Blog"),
                   "model_released", "release_assertion", "not yet released", subject_entity=lab,
                   object_entity=model, value={"released": False})
    later = _claim(_doc("ga", published_at="2025-06-01", source_name="Lab Blog"),
                   "model_released", "release_assertion", "now released", subject_entity=lab,
                   object_entity=model, value={"released": True})
    # availability changing over time is NOT a contradiction
    assert _between(db_conn, "contradicts", early, later) == []


def test_weights_flip_across_dates_no_contradiction(db_conn):
    model = _entity("model", "Model X")
    early = _claim(_doc("gated", published_at="2025-01-01", source_name="Host"),
                   "weights_available", "availability_assertion", "gated",
                   subject_entity=model, value={"available": False, "form": "api-only"})
    later = _claim(_doc("open", published_at="2025-06-01", source_name="Host"),
                   "weights_available", "availability_assertion", "now open",
                   subject_entity=model, value={"available": True, "form": "open-weights"})
    assert _between(db_conn, "contradicts", early, later) == []


# ---- H9: same-artifact LICENSE correction (same source, later date) supersedes

def test_same_source_license_change_supersedes(db_conn):
    model = _entity("model", "Model X")
    osource = _entity("concept", "Open Source")
    early = _claim(_doc("v1", published_at="2024-01-01", source_name="Vendor"),
                   "license_claim", "license_assertion", "open source",
                   subject_entity=model, object_entity=osource, value={"open_source": True})
    later = _claim(_doc("v2", published_at="2025-01-01", source_name="Vendor"),
                   "license_claim", "license_assertion", "license updated to restricted",
                   subject_entity=model, object_entity=osource, value={"open_source": False})
    # same author changing its own license over time = supersedes, not contradiction
    assert _between(db_conn, "contradicts", early, later) == []
    sup = _directed(db_conn, "supersedes", later, early)
    assert len(sup) == 1
    assert sup[0]["scope"]["value_changed"] is True
