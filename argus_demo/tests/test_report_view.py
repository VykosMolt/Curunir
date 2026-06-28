"""Static case-export renderer (argus.report_view).

Pure function: it renders Markdown from an export bundle dict alone -- no DB, no
writes, no network. These tests use a small in-memory fixture bundle.
"""
from __future__ import annotations

from argus import report_view

# A minimal but representative export bundle: one of each item type, with a claim
# carrying evidence roles (origin + verification) and predication triage.
BUNDLE = {
    "export": {"case_id": "case-1"},
    "case": {"id": "case-1", "title": "Demo case", "description": "A short demo."},
    "items": [
        {"case_item": {"item_type": "document"},
         "document": {"document": {"title": "UT judgment", "document_type": "court_decision",
                                   "publisher": "ICO (host)",
                                   "metadata": {"doc_id": "uk-ico-2025-clearview-ut-judgment"}},
                      "versions": [{"raw_hash": "h"}]}},
        {"case_item": {"item_type": "entity"},
         "entity": {"entity_id": "e1", "current_version": {"canonical_name": "First-tier Tribunal",
                                                           "entity_type": "court"},
                    "assigned_mention_count": 2,
                    "surface_forms": [{"surface_text": "FTT", "count": 1},
                                      {"surface_text": "First-tier Tribunal", "count": 1}]}},
        {"case_item": {"item_type": "claim"},
         "claim": {"claim_id": "claim-abcd1234", "current_version": {
                       "predicate": "appeal_allowed", "claim_text": "FTT allowed the appeal.",
                       "verification_state": "human_verified", "value_jsonb": {},
                       "qualifiers_jsonb": {"date": "2023-10-17"}},
                   "subject": {"resolution_status": "resolved",
                               "resolved_entity": {"canonical_name": "First-tier Tribunal",
                                                   "entity_type": "court"}},
                   "object": {"resolution_status": "missing_mention"},
                   "evidence_roles": {
                       "origin": {"evidence_span_id": "s1", "quote": "the FTT allowed the appeal",
                                  "document_title": "UT judgment", "source_name": "Upper Tribunal",
                                  "doc_id_from_metadata": "uk-ico-2025-clearview-ut-judgment"},
                       "verification_events": [
                           {"evidence_span_id": "s2", "previous_state": "unverified",
                            "new_state": "human_verified", "quote": "the appeal is allowed",
                            "document_title": "FTT decision", "source_name": "First-tier Tribunal",
                            "doc_id_from_metadata": "uk-ftt-2023-clearview-decision"}],
                       "relation_events": []},
                   "predication": {"flags": ["reported_secondhand"], "triage_level": "review",
                                   "proposal_confidence": 0.28, "review_note": "secondhand recital"}}},
        {"case_item": {"item_type": "relation"},
         "relation": {"relation": {"relation": "supersedes", "src_claim_id": "claim-abcd1234",
                                   "dst_claim_id": "claim-efgh5678"}}},
        {"case_item": {"item_type": "note", "note": "A limitations note."}},
    ],
}


def test_render_is_pure_and_deterministic():
    a = report_view.render_case_export(BUNDLE)
    b = report_view.render_case_export(BUNDLE)
    assert a == b                      # deterministic
    assert isinstance(a, str) and a.endswith("\n")


def test_render_shows_case_and_counts():
    md = report_view.render_case_export(BUNDLE)
    assert "# Case report — Demo case" in md
    assert "A short demo." in md
    assert "| claim | 1 |" in md
    assert "| document | 1 |" in md


def test_render_shows_documents_and_entities():
    md = report_view.render_case_export(BUNDLE)
    assert "uk-ico-2025-clearview-ut-judgment" in md
    assert "First-tier Tribunal" in md
    assert "FTT×1" in md  # surface form with count


def test_render_shows_claim_evidence_roles_and_predication():
    md = report_view.render_case_export(BUNDLE)
    assert "`appeal_allowed`" in md
    assert "Origin evidence:" in md and "the FTT allowed the appeal" in md
    assert "Verification evidence:" in md and "the appeal is allowed" in md
    assert "First-tier Tribunal" in md  # verification source
    assert "Predication triage:" in md and "`review`" in md
    assert "reported_secondhand" in md
    assert "not a truth estimate" in md  # the confidence caveat is shown
    assert "human_verified" in md


def test_render_shows_relations_and_notes():
    md = report_view.render_case_export(BUNDLE)
    assert "supersedes" in md
    assert "A limitations note." in md


def test_render_with_validation_header():
    validation = {"valid": True, "summary": {"total_checks": 258, "failed_checks": 0, "gaps": 0}}
    md = report_view.render_case_export(BUNDLE, validation)
    assert "Validation:" in md and "checks=258" in md and "failed=0" in md


def test_render_handles_empty_bundle():
    md = report_view.render_case_export({"case": {"title": "Empty"}, "items": []})
    assert "# Case report — Empty" in md
    assert "_None._" in md  # claims section with nothing
