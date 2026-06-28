"""Patch 1 + export contract: a `document` case item resolves to the logical
document and its versions, and a case export bundle is provenance-complete
(documents, versions, claims, evidence quotes, events, relations, resolved
subject/object mentions/entities)."""
from __future__ import annotations

import json
from pathlib import Path

from argus import actions

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def _build_case(out_path: Path) -> dict:
    text = (FIXTURES / "doc_a.txt").read_text(encoding="utf-8")
    ing = actions.ingest_text_document(text, title="Decision 2024-17")
    doc_id, dv = ing["document_id"], ing["document_version_id"]

    needle = "The Authority imposed an administrative fine of €4,500,000 on Northwind Analytics Ltd."
    start = text.find(needle)
    span = actions.create_evidence_span(dv, start_char=start, end_char=start + len(needle))["evidence_span_id"]

    subj = actions.create_mention(dv, "Example Data Protection Authority", "organization",
                                  evidence_span_id=span)["mention_id"]
    obj = actions.create_mention(dv, "Northwind Analytics Ltd", "organization",
                                 evidence_span_id=span)["mention_id"]
    ent = actions.create_entity("organization", "Northwind Analytics Ltd")["entity_id"]
    actions.assign_mention_to_entity(obj, ent)

    c1 = actions.create_claim("EDPA fined Northwind €4.5M.", "enforcement", "issued_fine", span,
                              subject_mention_id=subj, object_mention_id=obj)["claim_id"]
    c2 = actions.create_claim("EDPA reduced the fine.", "enforcement", "amended_fine", span,
                              object_mention_id=obj)["claim_id"]
    rel = actions.create_claim_relation(c2, c1, "supersedes", evidence_span_id=span)["relation_id"]
    actions.set_claim_verification_state(c1, "disputed", reason="superseded")

    case_id = actions.create_case("Northwind case")["case_id"]
    actions.add_case_item(case_id, "document", item_id=doc_id, note="logical document")
    actions.add_case_item(case_id, "claim", item_id=c1)
    actions.add_case_item(case_id, "claim", item_id=c2)
    actions.add_case_item(case_id, "entity", item_id=ent)
    actions.add_case_item(case_id, "relation", item_id=rel)
    actions.add_case_item(case_id, "note", note="freeform note")

    actions.export_case(case_id, out_path)
    return json.loads(out_path.read_text(encoding="utf-8"))


def _by_type(bundle: dict) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    for item in bundle["items"]:
        out.setdefault(item["case_item"]["item_type"], []).append(item)
    return out


def test_export_document_item_contains_document_and_versions(tmp_path):
    bundle = _build_case(tmp_path / "case.json")
    docs = _by_type(bundle)["document"]
    assert docs, "no document case items in export"
    expanded = docs[0]["document"]
    assert expanded["document"] is not None
    assert expanded["document"]["title"] == "Decision 2024-17"
    assert len(expanded["versions"]) >= 1
    assert expanded["versions"][0]["raw_hash"]


def test_export_bundle_is_provenance_complete(tmp_path):
    bundle = _build_case(tmp_path / "case.json")
    assert bundle["case"]["title"] == "Northwind case"
    assert "generated_at" in bundle["export"]
    by_type = _by_type(bundle)

    # claims expanded with evidence quote, events, and a resolved object entity
    claims = [it["claim"] for it in by_type["claim"]]
    assert claims
    assert any(c["evidence_span"]["exact_quote"] for c in claims)
    assert any(c["events"] for c in claims)
    assert any(c["object"] and c["object"]["resolved_entity"] for c in claims)
    # the disputed claim carries its incoming supersedes relation
    assert any(c["relations"]["incoming"] or c["relations"]["outgoing"] for c in claims)

    # relation + entity items expand
    rel_row = by_type["relation"][0]["relation"]["relation"]
    assert rel_row["relation"] == "supersedes"
    ent_item = by_type["entity"][0]["entity"]
    assert ent_item["current_version"]["canonical_name"] == "Northwind Analytics Ltd"
