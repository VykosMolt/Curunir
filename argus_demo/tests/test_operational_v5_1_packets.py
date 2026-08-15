from __future__ import annotations

import json

import pytest

from curunir_operational.v5.models import SURFACES
from curunir_operational.v5_1.models import canonical_json
from curunir_operational.v5_1.packets import (
    ReviewPacket, SealedBuilderEntry, build_packet, classify_context,
    freeze_corpus, leakage_scan, replace_defective, validate_packet,
)

pytestmark = pytest.mark.no_db

EXTRACTION, ORIGIN, DEPENDENCE, CLAIM, CONTRADICTION, FAITHFULNESS = SURFACES

SOURCES = {
    "src-es": {"text": "El organismo regional publicó el informe anual el 3 de mayo. "
                       "La cifra total fue de 41 toneladas."},
    "src-de": {"text": "Die Regionalbehörde veröffentlichte den Jahresbericht am 3. Mai. "
                       "Die Gesamtmenge betrug 41 Tonnen."},
    "src-en": {"text": "The regional body released its annual report in May, "
                       "citing a total of 41 tonnes."},
}

INSTRUCTIONS = (
    "Read every excerpt and the shipped metadata, then judge the stated relationship. "
    "Permitted decisions: CORRECT, INCORRECT, PARTIALLY_CORRECT, AMBIGUOUS, CANNOT_ADJUDICATE, "
    "and EPISTEMICALLY_UNRESOLVABLE. Choose EPISTEMICALLY_UNRESOLVABLE only when the packet itself "
    "demonstrates that the public evidence cannot settle the question. Choose CANNOT_ADJUDICATE "
    "when you cannot reach a verdict for any other reason."
)


def _excerpt(source_id, piece, language, **extra):
    start = SOURCES[source_id]["text"].index(piece)
    return {"source_id": source_id, "span_start": start, "span_end": start + len(piece),
            "original_text": piece, "language": language, **extra}


def _dependence_material(note="pair"):
    return {
        "excerpts": [
            _excerpt("src-es", "El organismo regional publicó el informe anual el 3 de mayo.", "es"),
            _excerpt("src-de", "Die Regionalbehörde veröffentlichte den Jahresbericht am 3. Mai.", "de"),
        ],
        "reviewer_instructions": INSTRUCTIONS,
        "source_metadata": {"src-es": {"publisher": "Boletín Regional"},
                            "src-de": {"publisher": "Regionalanzeiger"}},
        "publication_timing": {"src-es": "2026-05-03T08:00:00+00:00",
                               "src-de": "2026-05-03T11:30:00+00:00"},
        "dependence_evidence": [
            {"kind": "PUBLICATION_TIMING", "detail": "second publication appeared three hours later"},
            {"kind": "TRANSLATION_ALIGNMENT", "detail": f"sentence-aligned wording across the {note}"},
        ],
        "content_overlap_analysis": {"method": "normalized token overlap", "score": 0.62},
    }


def _dependence_packet(note="pair", stratum="cross_language_pair", reserve=False,
                       context="CONTEXT_SUFFICIENT", material=None):
    return build_packet(surface=DEPENDENCE, material=material or _dependence_material(note),
                        context_class=context, stratum=stratum, reserve=reserve)


def _faithfulness_material(include_basis=True):
    material = {
        "report_sentence": "The regional body reported a total of 41 tonnes in May.",
        "reviewer_instructions": INSTRUCTIONS,
        "excerpts": [_excerpt("src-en", "citing a total of 41 tonnes", "en")],
    }
    if include_basis:
        material["structured_basis"] = [{"basis_kind": "CLAIM", "source_id": "src-en",
                                         "statement": "total of 41 tonnes reported in May"}]
    return material


def _origin_material(include_roles=True):
    material = {
        "excerpts": [_excerpt("src-en", "The regional body released its annual report in May", "en")],
        "reviewer_instructions": INSTRUCTIONS,
        "origin_manifest": {"retrieval": "captured from the publisher domain",
                            "manifested_as": "html article"},
    }
    if include_roles:
        material["role_metadata"] = [{"kind": "MASTHEAD_OR_IMPRINT",
                                      "value": "imprint names the publishing body"}]
    return material


def _corpus():
    packets, reserves, entries, answers = [], [], [], {}
    strata = ("cross_language_pair", "same_language_pair")
    for index in range(4):
        stratum = strata[index % 2]
        packet, entry = _dependence_packet(note=f"active {index}", stratum=stratum)
        packets.append(packet); entries.append(entry)
        answers[packet.packet_id] = ("TRANSLATION_DERIVATIVE" if stratum == strata[0]
                                     else "NO_DEPENDENCE_FOUND")
    for index, stratum in enumerate(strata):
        packet, entry = _dependence_packet(note=f"reserve {index}", stratum=stratum, reserve=True)
        reserves.append(packet); entries.append(entry)
        answers[packet.packet_id] = "COMMON_EVIDENCE_BASIS_CONFIRMED"
    return packets, reserves, entries, answers


def _by_name(results):
    return {name: (ok, detail) for name, ok, detail in results}


# --- reviewer payload / sealed builder manifest -----------------------------

def test_reviewer_payload_never_contains_stratum():
    packet, entry = _dependence_packet()
    payload = packet.to_record()
    assert "sampling_stratum" not in payload
    assert "stratum" not in canonical_json(payload)
    assert entry.packet_id == packet.packet_id
    assert entry.sampling_stratum == "cross_language_pair"
    assert entry.access_marking["releasability"] == ["REVIEW_ENGINE_ONLY"]


def test_material_with_builder_metadata_key_rejected():
    for key in ("sampling_stratum", "stratum", "difficulty", "expected_answer", "final_verdict"):
        material = _dependence_material()
        material[key] = "anything"
        with pytest.raises(ValueError, match="sealed builder or answer"):
            build_packet(surface=DEPENDENCE, material=material,
                         context_class="CONTEXT_SUFFICIENT", stratum="cross_language_pair")


def test_sealed_builder_entry_requires_engine_only_marking():
    with pytest.raises(ValueError, match="REVIEW_ENGINE_ONLY"):
        SealedBuilderEntry("entry", "packet", "cross_language_pair",
                           {"releasability": ["PUBLIC"]})
    with pytest.raises(ValueError, match="stratum"):
        SealedBuilderEntry("entry", "packet", "   ",
                           {"releasability": ["REVIEW_ENGINE_ONLY"]})


def test_packet_hash_rejects_material_replacement():
    packet, _entry = _dependence_packet()
    payload = packet.public_payload()
    payload["blinded_material"]["reviewer_instructions"] = INSTRUCTIONS + " One more sentence."
    with pytest.raises(ValueError, match="hash"):
        ReviewPacket(**payload)


# --- leakage scan -----------------------------------------------------------

def test_leakage_scan_catches_verbatim_answer_in_material():
    material = _dependence_material()
    material["dependence_evidence"][0]["detail"] = "the pair is TRANSLATION_DERIVATIVE work"
    packet, _entry = _dependence_packet(material=material)
    findings = leakage_scan(packet.public_payload(), "TRANSLATION_DERIVATIVE")
    assert any(f["kind"] == "ANSWER_VERBATIM" for f in findings)
    clean, _entry = _dependence_packet()
    assert leakage_scan(clean.public_payload(), "TRANSLATION_DERIVATIVE") == ()


def test_leakage_scan_label_menu_is_not_a_leak():
    packet, _entry = _dependence_packet()
    assert leakage_scan(packet.public_payload(), "CANNOT_ADJUDICATE") == ()
    material = _dependence_material()
    material["builder_note"] = "CANNOT_ADJUDICATE"
    single, _entry = _dependence_packet(material=material)
    findings = leakage_scan(single.public_payload(), "CANNOT_ADJUDICATE")
    assert [f["kind"] for f in findings] == ["ANSWER_VERBATIM"]


def test_leakage_scan_rejects_ordinal_packet_id():
    packet, _entry = _dependence_packet()
    payload = packet.public_payload()
    payload["packet_id"] = "v5-1-packet-0007"
    findings = leakage_scan(payload, None)
    assert any(f["kind"] == "ORDINAL_PACKET_ID" for f in findings)
    assert leakage_scan(packet.public_payload(), None) == ()


def test_leakage_scan_flags_denylisted_field_and_file_path():
    payload = {"packet_id": "v5-1-packet-" + "a" * 24, "difficulty": "hard",
               "material": {"note": "/home/builder/sealed/answers.json",
                            "url": "https://example.org/report.html"}}
    findings = leakage_scan(payload, None)
    kinds = {f["kind"] for f in findings}
    assert kinds == {"DENYLISTED_FIELD", "FILE_PATH"}
    path_findings = [f for f in findings if f["kind"] == "FILE_PATH"]
    assert len(path_findings) == 1 and path_findings[0]["path"].endswith(".note")


# --- Section 18.1 validators ------------------------------------------------

def test_validate_packet_all_checks_pass_on_sufficient_dependence_packet():
    packet, _entry = _dependence_packet()
    results = validate_packet(packet, SOURCES, sealed_answer="TRANSLATION_DERIVATIVE")
    assert len(results) == 14
    assert [name for name, ok, _detail in results if not ok] == []


def test_validate_packet_flags_unknown_source_and_span_divergence():
    material = _dependence_material()
    material["excerpts"][0]["source_id"] = "src-missing"
    material["excerpts"][1]["span_end"] += 3
    material["source_metadata"]["src-missing"] = material["source_metadata"].pop("src-es")
    material["publication_timing"]["src-missing"] = material["publication_timing"].pop("src-es")
    packet, _entry = _dependence_packet(material=material)
    checks = _by_name(validate_packet(packet, SOURCES))
    assert not checks["SOURCE_EXISTENCE"][0]
    assert not checks["MAPPING_VERIFICATION"][0]


def test_dependence_packet_without_both_side_excerpts_fails():
    material = _dependence_material()
    material["excerpts"] = [material["excerpts"][0]]
    packet, _entry = _dependence_packet(material=material)
    checks = _by_name(validate_packet(packet, SOURCES))
    assert not checks["DEPENDENCE_EVIDENCE_SUFFICIENCY"][0]
    assert "both-side" in checks["DEPENDENCE_EVIDENCE_SUFFICIENCY"][1]
    assert not checks["CONTEXT_SUFFICIENCY"][0]
    assert classify_context(packet) == "PACKET_CONSTRUCTION_DEFECT"


def test_faithfulness_packet_without_structured_basis_fails():
    packet, _entry = build_packet(surface=FAITHFULNESS,
                                  material=_faithfulness_material(include_basis=False),
                                  context_class="CONTEXT_SUFFICIENT", stratum="sentence_basis")
    checks = _by_name(validate_packet(packet, SOURCES))
    assert not checks["CONTEXT_SUFFICIENCY"][0]
    assert classify_context(packet) == "PACKET_CONSTRUCTION_DEFECT"
    complete, _entry = build_packet(surface=FAITHFULNESS, material=_faithfulness_material(),
                                    context_class="CONTEXT_SUFFICIENT", stratum="sentence_basis")
    assert classify_context(complete) == "CONTEXT_SUFFICIENT"


def test_origin_packet_without_role_metadata_fails():
    packet, _entry = build_packet(surface=ORIGIN, material=_origin_material(include_roles=False),
                                  context_class="CONTEXT_SUFFICIENT", stratum="role_attribution")
    checks = _by_name(validate_packet(packet, SOURCES))
    assert not checks["ROLE_SPECIFIC_CONTEXT_VALIDATION"][0]
    assert not checks["CONTEXT_SUFFICIENCY"][0]
    complete, _entry = build_packet(surface=ORIGIN, material=_origin_material(),
                                    context_class="CONTEXT_SUFFICIENT", stratum="role_attribution")
    assert _by_name(validate_packet(complete, SOURCES))["ROLE_SPECIFIC_CONTEXT_VALIDATION"][0]


def test_reviewer_instructions_must_permit_abstention():
    material = _dependence_material()
    material["reviewer_instructions"] = (
        "Read both excerpts and decide whether the stated relationship holds. "
        "Permitted decisions: CORRECT, INCORRECT, PARTIALLY_CORRECT, and AMBIGUOUS only.")
    packet, _entry = _dependence_packet(material=material)
    checks = _by_name(validate_packet(packet, SOURCES))
    assert not checks["REVIEWER_INSTRUCTION_VALIDATION"][0]
    assert "EPISTEMICALLY_UNRESOLVABLE" in checks["REVIEWER_INSTRUCTION_VALIDATION"][1]
    assert "CANNOT_ADJUDICATE" in checks["REVIEWER_INSTRUCTION_VALIDATION"][1]


def test_translation_separation_rejects_merged_fields():
    material = _dependence_material()
    material["excerpts"][1]["translation"] = "The regional authority published the annual report on May 3."
    material["excerpts"][1]["translation_language"] = "en"
    packet, _entry = _dependence_packet(material=material)
    assert _by_name(validate_packet(packet, SOURCES))["TRANSLATION_SEPARATION"][0]

    merged = _dependence_material()
    translation = "The regional authority published the annual report on May 3."
    merged["excerpts"][1]["original_text"] += " " + translation
    merged["excerpts"][1]["translation"] = translation
    merged["excerpts"][1]["translation_language"] = "en"
    packet, _entry = _dependence_packet(material=merged)
    checks = _by_name(validate_packet(packet, SOURCES))
    assert not checks["TRANSLATION_SEPARATION"][0]
    assert "merged" in checks["TRANSLATION_SEPARATION"][1]


# --- context classification -------------------------------------------------

def test_insufficient_by_nature_requires_demonstration():
    material = _dependence_material()
    del material["publication_timing"]["src-de"]
    defect, _entry = _dependence_packet(material=material, context="PACKET_CONSTRUCTION_DEFECT")
    assert classify_context(defect) == "PACKET_CONSTRUCTION_DEFECT"

    by_nature_material = _dependence_material()
    del by_nature_material["publication_timing"]["src-de"]
    by_nature_material["evidence_insufficiency_demonstration"] = (
        "Neither manifestation carries a dated imprint, archive capture, or any other "
        "recoverable publication timestamp in the public record, so publication order "
        "cannot be established from available public evidence.")
    packet, _entry = _dependence_packet(material=by_nature_material,
                                        context="CONTEXT_INSUFFICIENT_BY_NATURE")
    assert classify_context(packet) == "CONTEXT_INSUFFICIENT_BY_NATURE"
    assert _by_name(validate_packet(packet, SOURCES))["CONTEXT_SUFFICIENCY"][0]


def test_partially_sufficient_when_supporting_material_missing():
    material = _dependence_material()
    del material["content_overlap_analysis"]
    packet, _entry = _dependence_packet(material=material, context="CONTEXT_PARTIALLY_SUFFICIENT")
    assert classify_context(packet) == "CONTEXT_PARTIALLY_SUFFICIENT"


# --- freeze -----------------------------------------------------------------

def test_freeze_blocks_leaky_packet(tmp_path):
    # An answer surfacing in a builder-authored field blocks the freeze; the
    # same phrase inside a custody-pinned excerpt is certifiably
    # source-authored coincidence (SPAN_EXISTENCE pins excerpts) and does not.
    packets, reserves, entries, answers = _corpus()
    leaky, entry = _dependence_packet(
        material={**_dependence_material("leaky"),
                  "builder_note": "the right call here is TRANSLATION_DERIVATIVE"},
        stratum="leaky_pair")
    packets.append(leaky)
    entries.append(entry)
    answers[leaky.packet_id] = "TRANSLATION_DERIVATIVE"
    with pytest.raises(ValueError, match="leakage"):
        freeze_corpus(packets, answers, reserves, tmp_path / "corpus", builder_entries=entries)


def test_answer_phrase_inside_pinned_excerpt_is_exempt(tmp_path):
    packets, reserves, entries, answers = _corpus()
    answers[packets[0].packet_id] = "informe anual"
    manifest = freeze_corpus(packets, answers, reserves, tmp_path / "corpus",
                             builder_entries=entries)
    assert manifest["frozen"] is True


def test_freeze_blocks_construction_defect_packet(tmp_path):
    packets, reserves, entries, answers = _corpus()
    material = _dependence_material("defective")
    del material["publication_timing"]["src-de"]
    defective, entry = _dependence_packet(material=material)  # declared CONTEXT_SUFFICIENT
    packets.append(defective); entries.append(entry)
    answers[defective.packet_id] = "TRANSLATION_DERIVATIVE"
    with pytest.raises(ValueError, match="construction defect"):
        freeze_corpus(packets, answers, reserves, tmp_path / "corpus", builder_entries=entries)


def test_freeze_enforces_surface_reserve_ratio(tmp_path):
    packets, reserves, entries, answers = _corpus()
    with pytest.raises(ValueError, match="reserve"):
        freeze_corpus(packets, answers, [], tmp_path / "empty", builder_entries=entries)
    manifest = freeze_corpus(packets, answers, reserves, tmp_path / "corpus",
                             builder_entries=entries)
    assert manifest["frozen"]
    assert manifest["reserve_ratio_by_surface"][DEPENDENCE] >= 0.20


def test_freeze_enforces_stratum_reserve_ratio(tmp_path):
    packets, reserves, entries, answers = _corpus()
    stratum_of = {e.packet_id: e.sampling_stratum for e in entries}
    cross_only = [r for r in reserves if stratum_of[r.packet_id] == "cross_language_pair"]
    with pytest.raises(ValueError, match="stratum"):
        freeze_corpus(packets, answers, cross_only, tmp_path / "corpus", builder_entries=entries)


def test_freeze_writes_sealed_answers_separately_engine_only(tmp_path):
    packets, reserves, entries, answers = _corpus()
    root = tmp_path / "corpus"
    freeze_corpus(packets, answers, reserves, root, builder_entries=entries,
                  source_lookup=SOURCES)
    frozen_text = (root / "frozen_packets.jsonl").read_text() + (root / "reserve_packets.jsonl").read_text()
    for label in ("TRANSLATION_DERIVATIVE", "NO_DEPENDENCE_FOUND", "COMMON_EVIDENCE_BASIS_CONFIRMED"):
        assert label not in frozen_text
    assert "stratum" not in frozen_text
    sealed = json.loads((root / "sealed_answers.json").read_text())
    assert sealed["access_marking"]["releasability"] == ["REVIEW_ENGINE_ONLY"]
    assert set(sealed["answers"]) == {p.packet_id for p in packets + reserves}
    builder = json.loads((root / "sealed_builder_manifest.json").read_text())
    assert builder["access_marking"]["releasability"] == ["REVIEW_ENGINE_ONLY"]
    assert set(builder["strata"].values()) == {"cross_language_pair", "same_language_pair"}


def test_freeze_hash_determinism(tmp_path):
    first = _corpus()
    second = _corpus()
    manifest_a = freeze_corpus(first[0], first[3], first[1], tmp_path / "a", builder_entries=first[2])
    manifest_b = freeze_corpus(second[0], second[3], second[1], tmp_path / "b", builder_entries=second[2])
    manifest_a.pop("output_root"); manifest_b.pop("output_root")
    assert manifest_a == manifest_b
    assert (tmp_path / "a/frozen_packets.jsonl").read_bytes() == (tmp_path / "b/frozen_packets.jsonl").read_bytes()


# --- defect replacement -----------------------------------------------------

def test_replace_defective_promotes_matching_reserve_append_only(tmp_path):
    packets, reserves, entries, answers = _corpus()
    root = tmp_path / "corpus"
    manifest = freeze_corpus(packets, answers, reserves, root, builder_entries=entries)
    stratum_of = {e.packet_id: e.sampling_stratum for e in entries}
    target = packets[0]
    frozen_before = (root / "frozen_packets.jsonl").read_text()
    record = replace_defective(manifest, target.packet_id,
                               "span offsets fail to resolve against the referenced document")
    assert record["original_preserved"] and record["matching_stratum_verified"]
    assert record["surface"] == DEPENDENCE
    promoted = record["promoted_reserve_packet_id"]
    assert promoted in {r.packet_id for r in reserves}
    assert stratum_of[promoted] == stratum_of[target.packet_id]
    assert (root / "frozen_packets.jsonl").read_text() == frozen_before
    log_lines = (root / "replacements.jsonl").read_text().splitlines()
    assert len(log_lines) == 1
    assert json.loads(log_lines[0])["defective_packet_id"] == target.packet_id


def test_replace_defective_blocks_double_replacement_and_exhausted_reserve(tmp_path):
    packets, reserves, entries, answers = _corpus()
    root = tmp_path / "corpus"
    manifest = freeze_corpus(packets, answers, reserves, root, builder_entries=entries)
    reason = "context does not contain the evidence its question type requires"
    replace_defective(manifest, packets[0].packet_id, reason)
    with pytest.raises(ValueError, match="already replaced"):
        replace_defective(manifest, packets[0].packet_id, reason)
    # packets[2] shares the cross_language_pair stratum whose only reserve is consumed.
    with pytest.raises(ValueError, match="reserve"):
        replace_defective(manifest, packets[2].packet_id, reason)
    with pytest.raises(ValueError, match="frozen active"):
        replace_defective(manifest, "v5-1-packet-" + "b" * 24, reason)
    with pytest.raises(ValueError, match="reason"):
        replace_defective(manifest, packets[1].packet_id, "  ")
