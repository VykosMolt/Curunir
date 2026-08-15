from __future__ import annotations

from dataclasses import replace

import pytest

from curunir_operational.v4.analysis import (
    ai_candidate_from_located_text, deterministic_candidates, disagreements, entity_record,
    evidence_basis, make_candidate, propose_identity, repository_mention_candidates,
    retract_basis, source_origin_edge,
)
from curunir_operational.v4.models import (
    ClaimUnit, DerivativeMapping, InferenceRecord, NormalizedDocument, SourceRecord, sha256,
)
from curunir_operational.v4.reporting import REPORT_SECTIONS, sentence, validate_report

pytestmark = pytest.mark.no_db
STAMP = "2026-07-22T09:00:00+00:00"


def fixture():
    text = "European Commission announced PROGRAMME on 2026-07-22. A later correction changed the date."
    source_hash = sha256(b"raw"); derivative_hash = sha256(text); source_id = "source-1"
    mapping = DerivativeMapping("map-1", source_id, source_hash, derivative_hash, "immutable", 0, len(text),
                                "EXACT_CHARACTER")
    document = NormalizedDocument("doc-1", source_id, source_hash, derivative_hash, "TEST", "1", "en", text,
                                  (("BODY", 0, len(text)),), (), (mapping,), (), (), STAMP)
    source = SourceRecord(source_id, "CASE", ("retrieval-1",), source_hash, "/immutable", ("https://e/a",),
                          ("https://e/a",), "EU", "OFFICIAL", "Title", "en", STAMP, STAMP,
                          "CAPTURED", {"releasability": ["PUBLIC"]})
    return text, document, source


def test_deterministic_and_repository_extractors_have_exact_spans():
    text, document, source = fixture()
    candidates = deterministic_candidates("CASE", document, source)
    mentions = repository_mention_candidates("CASE", document, source)
    assert candidates and any(item.candidate_type == "CORRECTION_CANDIDATE" for item in candidates)
    assert mentions and all(document.text[item.span_start:item.span_end] == item.original_text for item in mentions)


def test_ai_candidate_without_locatable_span_is_rejected_and_review_is_secondary():
    _, document, source = fixture()
    with pytest.raises(ValueError, match="span"):
        ai_candidate_from_located_text(case_id="CASE", document=document, source=source, exact_text="hallucinated",
                                       candidate_type="CLAIM_CANDIDATE", normalized_value={})
    item = ai_candidate_from_located_text(case_id="CASE", document=document, source=source,
                                          exact_text="European Commission", candidate_type="ENTITY_MENTION_CANDIDATE",
                                          normalized_value={"entity": "European Commission"})
    assert item.review_state == "AI_SECONDARY_REVIEW" and item.mapping_precision == "EXACT_CHARACTER"


def test_candidate_disagreement_is_preserved():
    _, document, source = fixture()
    left = make_candidate(case_id="CASE", document=document, source=source, span_start=0, span_end=19,
                          candidate_type="ENTITY_MENTION_CANDIDATE", normalized_value={"polarity": "POSITIVE"},
                          provider="A", provider_version="1", review_state="UNREVIEWED")
    right = replace(left, candidate_id="candidate-other", candidate_type="CLAIM_CANDIDATE", provider="B")
    result = disagreements((left, right))
    assert len(result) == 1 and result[0].review_state == "HUMAN_REVIEW_REQUIRED"


def test_entity_resolution_is_reversible_and_name_match_does_not_accept():
    left = entity_record("PROGRAMME", "Arcadia", aliases=("ARCADIA",))
    right = entity_record("COMPANY", "ARCADIA", aliases=("Arcadia",))
    result = propose_identity(left, right, evidence_candidate_ids=(), factors={})
    assert result.outcome == "AMBIGUOUS" and result.reversible
    official_left = entity_record("PROGRAMME", "P", external_identifiers={"register": "123"})
    official_right = entity_record("PROGRAMME", "Programme", external_identifiers={"register": "123"})
    proposed = propose_identity(official_left, official_right, evidence_candidate_ids=("c",),
                                factors={"explicit_source_statement": True})
    assert proposed.outcome == "SAME_ENTITY_PROPOSED"


def test_source_origin_is_directional_translation_and_dependence_require_basis():
    edge = source_origin_edge(source_id="translation", target_id="original", relationship="TRANSLATED_FROM",
                              metadata_basis=("translation manifest",))
    assert edge.source_id == "translation" and edge.target_id == "original"
    with pytest.raises(ValueError, match="basis"):
        source_origin_edge(source_id="copy", target_id="original", relationship="DERIVED_FROM")
    with pytest.raises(ValueError, match="invalid"):
        source_origin_edge(source_id="same", target_id="same", relationship="MIRRORS")


def test_retraction_removes_active_support_without_deleting_basis():
    basis = evidence_basis(case_id="CASE", source_object_ids=("source-1",), candidate_ids=("candidate-1",),
                           source_family_id="family-1", independence_state="INDEPENDENT")
    retracted = retract_basis(basis)
    assert retracted.basis_id == basis.basis_id and not retracted.active and retracted.correction_state == "RETRACTED"


def report_fixture():
    _, document, source = fixture()
    candidate = make_candidate(case_id="CASE", document=document, source=source, span_start=0, span_end=55,
                               candidate_type="CLAIM_CANDIDATE", normalized_value={"statement": document.text[:55]},
                               provider="TEST", provider_version="1", review_state="HUMAN_REVIEW_REQUIRED")
    basis = evidence_basis(case_id="CASE", source_object_ids=(source.source_object_id,),
                           candidate_ids=(candidate.candidate_id,), source_family_id="family-1",
                           independence_state="INDEPENDENT")
    claim = ClaimUnit("claim-1", "CASE", "An announcement was published.", candidate.original_text,
                      "Programme", "ANNOUNCED", "public programme", (None, None), ("EU",), "ASSERTED",
                      "POSITIVE", "DIRECTLY_STATED", (basis.basis_id,), (candidate.candidate_id,),
                      "HUMAN_REVIEW_REQUIRED", 1)
    sentences = [sentence(section=section, text=f"{section.replace('_', ' ').title()} scope note.",
                          sentence_type="METHOD") for section in REPORT_SECTIONS]
    sentences[0] = sentence(section=REPORT_SECTIONS[0], text="The captured source directly states that an announcement was published.",
                            sentence_type="FACTUAL", claim_ids=(claim.claim_id,), evidence_basis_ids=(basis.basis_id,),
                            source_object_ids=(source.source_object_id,), epistemic_state="DIRECTLY_STATED")
    return sentences, claim, basis, source


def test_report_requires_all_factual_sentence_evidence_and_passes_complete_fixture():
    sentences, claim, basis, source = report_fixture()
    result = validate_report(sentences=sentences, claims=(claim,), bases=(basis,), sources=(source,))
    assert result["verdict"] == "PASS"
    assert result["report_evidence_coverage"] == "100_PERCENT_FOR_FACTUAL_SENTENCES"
    bad = list(sentences); bad[0] = replace(bad[0], evidence_basis_ids=())
    assert validate_report(sentences=bad, claims=(claim,), bases=(basis,), sources=(source,))["verdict"] == "INVALID"


def test_search_snippet_failed_restricted_and_retracted_support_rejected():
    sentences, claim, basis, source = report_fixture()
    assert validate_report(sentences=sentences, claims=(claim,), bases=(basis,), sources=(source,),
                           search_lead_ids=(source.source_object_id,))["search_snippet_support_count"] == 1
    assert validate_report(sentences=sentences, claims=(claim,), bases=(basis,), sources=(source,),
                           failed_source_ids=(source.source_object_id,))["verdict"] == "INVALID"
    assert validate_report(sentences=sentences, claims=(claim,), bases=(basis,), sources=(source,),
                           restricted_source_ids=(source.source_object_id,))["verdict"] == "INVALID"
    result = validate_report(sentences=sentences, claims=(claim,), bases=(retract_basis(basis),), sources=(source,))
    assert result["active_retracted_support_findings"] == 1


def test_inference_requires_persisted_premises():
    sentences, claim, basis, source = report_fixture()
    inference = InferenceRecord("inference-1", "A bounded implication", (claim.claim_id,), (basis.basis_id,),
                                "RULE", ("assumption",), ("alternative",), "uncertain", ("no deployment claim",))
    sentences[-1] = sentence(section=REPORT_SECTIONS[-1], text="This may imply a bounded future requirement.",
                             sentence_type="INFERENCE", claim_ids=(claim.claim_id,),
                             evidence_basis_ids=(basis.basis_id,), source_object_ids=(source.source_object_id,),
                             epistemic_state="INFERRED", inference_id=inference.inference_id)
    assert validate_report(sentences=sentences, claims=(claim,), bases=(basis,), sources=(source,),
                           inferences=(inference,))["verdict"] == "PASS"
    assert validate_report(sentences=sentences, claims=(claim,), bases=(basis,), sources=(source,))["verdict"] == "INVALID"
