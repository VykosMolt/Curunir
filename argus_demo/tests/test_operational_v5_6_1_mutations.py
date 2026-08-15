"""V5.6.1 §18 — twenty-six mutations, each of which must fail for its own reason.

A mutation suite is only worth its runtime if each case fails for the reason it
was written for.  A test that passes because the constructor happened to raise
``TypeError`` two lines earlier proves nothing, so every case below asserts on
the message as well as the refusal.

The list is the contract's, in the contract's order.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from curunir_operational.v5_6 import schema as S
from curunir_operational.v5_6_1 import custody as CU
from curunir_operational.v5_6_1 import gates as G
from curunir_operational.v5_6_1 import identity as ID
from curunir_operational.v5_6_1 import packets as PK
from curunir_operational.v5_6_1 import protocol as P
from curunir_operational.v5_6_1 import reference as R

pytestmark = pytest.mark.no_db

ROOT = Path(__file__).resolve().parents[1]
V56_ROOT = ROOT / "artifacts/curunir_dependency_consistent_reference_standard_v5_6_20260725"


def support_answer(**overrides):
    base = {
        "unit_id": "u", "unit_type": "SUPPORT", "addresses_proposition": True,
        "actor_alignment": "ALIGNED", "predicate_alignment": "ALIGNED",
        "object_alignment": "ALIGNED", "scope_alignment": "ALIGNED",
        "time_alignment": "ALIGNED", "polarity_alignment": "ALIGNED",
        "modality_alignment": "ALIGNED", "lifecycle_alignment": "ALIGNED",
        "attribution_alignment": "NOT_APPLICABLE",
        "support_completeness": "COMPLETE", "first_material_failure": None,
        "support_class": "FULL_SUPPORT", "required_qualifications": [],
        "wording_permission": "DIRECT_FACTUAL_PUBLICATION",
        "disposition": "PUBLISHED", "reasoning": "r" * 200,
    }
    base.update(overrides)
    return base


def extraction_answer(**overrides):
    base = {
        "unit_id": "u", "unit_type": "EXTRACTION",
        "candidate_semantic_validity": "VALID",
        "boundary_repair_requirement": "NO_REPAIR_REQUIRED",
        "exact_quotation_permission": "NO_EXACT_QUOTATION_REQUESTED",
        "paraphrase_permission": "PARAPHRASE_PERMITTED",
        "wording_as_written_permission": "PERMITTED_AS_WRITTEN",
        "mapping_satisfied": ["TEXT_LOCATABLE", "PROPOSITION_BOUNDARY_DEFENSIBLE"],
        "first_material_failure": None, "preferred_candidate_id": None,
        "reasoning": "r" * 200,
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# 1–4 — the typed extraction protocol
# ---------------------------------------------------------------------------

def test_m01_coerce_not_applicable_to_false():
    """NOT_APPLICABLE is not false; a bool coercion would erase the distinction."""
    assert P.coercion_would_lose_information("paraphrase_permission", "NOT_APPLICABLE")
    assert P.coercion_would_lose_information(
        "boundary_repair_requirement", "NOT_APPLICABLE")
    with pytest.raises(P.ProtocolViolation, match="not in the vocabulary"):
        P.assert_no_collapse("paraphrase_permission", False)
    audit = P.audit_no_ambiguous_fields([{
        "decision_id": "d", "paraphrase_permission": False,
        "candidate_semantic_validity": "VALID",
        "boundary_repair_requirement": "NO_REPAIR_REQUIRED",
        "exact_quotation_permission": "NO_EXACT_QUOTATION_REQUESTED",
        "wording_as_written_permission": "PERMITTED_AS_WRITTEN"}])
    assert audit["verdict"] == "FAIL"
    assert audit["ambiguous_wording_fields"] == 1


def test_m02_coerce_permitted_only_as_paraphrase_to_permitted_as_written():
    """The two are a declared prohibited collapse, and the menu enforces it."""
    assert ("wording_as_written_permission", "PERMITTED_ONLY_AS_PARAPHRASE",
            "NOT_PERMITTED") in P.PROHIBITED_COLLAPSES
    assert P.coercion_would_lose_information(
        "wording_as_written_permission", "PERMITTED_ONLY_AS_PARAPHRASE")
    # A span needing repair may not be offered PERMITTED_AS_WRITTEN at all.
    offered = PK.available_wording_as_written(
        "RECOVERABLE_WITH_BOUNDARY_REPAIR", "EXPAND_LEFT_CONTEXT",
        "PARAPHRASE_PERMITTED")
    assert "PERMITTED_AS_WRITTEN" not in offered
    problems = PK.validate_answer(extraction_answer(
        candidate_semantic_validity="RECOVERABLE_WITH_BOUNDARY_REPAIR",
        boundary_repair_requirement="EXPAND_LEFT_CONTEXT",
        wording_as_written_permission="PERMITTED_AS_WRITTEN"))
    assert any("not offered by this seat's own earlier answers" in p for p in problems)


def test_m03_exact_quotation_without_exact_value_mapping():
    problems = PK.validate_answer(extraction_answer(
        exact_quotation_permission="EXACT_QUOTATION_PERMITTED",
        mapping_satisfied=["TEXT_LOCATABLE"]))
    assert any("EXACT_VALUE_QUOTABLE" in p for p in problems)
    with pytest.raises(P.ProtocolViolation, match="EXACT_VALUE_QUOTABLE"):
        P.typed_decision(
            candidate_id="c", seat_id="REVIEWER_A_REPAIR",
            candidate_semantic_validity="VALID",
            boundary_repair_requirement="NO_REPAIR_REQUIRED",
            exact_quotation_permission="EXACT_QUOTATION_PERMITTED",
            paraphrase_permission="PARAPHRASE_PERMITTED",
            wording_as_written_permission="PERMITTED_AS_WRITTEN",
            mapping_satisfied=("TEXT_LOCATABLE",))


def test_m04_reject_faithful_paraphrase_solely_for_approximate_mapping():
    """An approximate mapping bars exact quotation; it does not bar a paraphrase.

    The two dimensions are independent, which is exactly what the V5.6 boolean
    could not express.
    """
    answer = extraction_answer(
        exact_quotation_permission="EXACT_QUOTATION_REQUIRES_STRONGER_MAPPING",
        paraphrase_permission="PARAPHRASE_PERMITTED",
        wording_as_written_permission="PERMITTED_ONLY_AS_PARAPHRASE")
    assert PK.validate_answer(answer) == []
    # And the collapse of the two exact-quotation values is prohibited, so a
    # coarse mapping can never be silently read as "not permitted".
    assert ("exact_quotation_permission",
            "EXACT_QUOTATION_REQUIRES_STRONGER_MAPPING",
            "EXACT_QUOTATION_NOT_PERMITTED") in P.PROHIBITED_COLLAPSES
    routed = R.material_dimensions([
        answer,
        extraction_answer(
            exact_quotation_permission="EXACT_QUOTATION_NOT_PERMITTED",
            paraphrase_permission="PARAPHRASE_PERMITTED",
            wording_as_written_permission="PERMITTED_ONLY_AS_PARAPHRASE"),
        answer])
    assert "exact_quotation_permission" in routed


# ---------------------------------------------------------------------------
# 5–9 — semantic identity
# ---------------------------------------------------------------------------

def test_m05_legacy_subject_as_semantic_actor():
    with pytest.raises(ID.IdentityViolation, match="may not determine a semantic role"):
        ID.SemanticIdentity(
            "i", "Importers who", None, None, None, None, None, None, None, None,
            None, (), "Importers who", True, "2026-07-25T00:00:00+00:00")
    audit = ID.audit_legacy_subject_authority([
        ID.build_identity(grammatical_subject="The European Commission")])
    assert audit["legacy_subject_authoritative_uses"] == 0


def test_m06_issuer_as_quoted_speaker_without_evidence():
    with pytest.raises(ID.IdentityViolation, match="requires the span that establishes"):
        ID.SemanticIdentity(
            "i", "The report", None, None, None, None,
            "v5-6-1-entity-commission", None, "v5-6-1-issuer-commission", None,
            None, (), "The report", False, "2026-07-25T00:00:00+00:00")


def test_m07_importers_who_is_not_an_actor():
    derivation = ID.derive_actor("Importers who", role="semantic_actor")
    assert derivation.outcome == "UNRESOLVED_NOT_REFERENTIAL"
    assert derivation.resolved_id is None
    assert not ID.is_referential("Importers who")
    for phrase in ("For transparency", "Immediately", "That right", "Unadjusted data",
                   "The person whose status", "As previously"):
        assert not ID.is_referential(phrase), phrase


def test_m08_metaclaim_with_unresolved_speaker():
    identity = ID.build_identity(grammatical_subject="Importers who")
    split = ID.split_fact_and_metaclaim_typed(
        identity=identity, predicate="stated", object_or_value="x")
    assert split["metaclaim"] is None
    assert "NAMED PARTY" in split["metaclaim_refused_reason"]


def test_m09_transfer_metaclaim_support_to_the_underlying_fact():
    """That a party said something is a different proposition from its truth."""
    identity = ID.build_identity(
        grammatical_subject="The European Commission",
        quoted_speaker_phrase="The European Commission",
        quoted_speaker_span="The European Commission said")
    split = ID.split_fact_and_metaclaim_typed(
        identity=identity, predicate="stated", object_or_value="emissions fell")
    assert split["metaclaim"]["assertion"] == "THE_EXTERNAL_STATEMENT_OCCURRED"
    assert split["fact"]["predicate"] == "stated"
    # Their canonical identities differ, so support for one cannot be recorded
    # against the other.
    fact_hash = ID.canonical_hash(
        {"record_type": "SUPPORT_UNIT", "proposition_id": "p-fact",
         "semantic_actor_id": split["fact"]["subject_id"]},
        family="semantic_identity_hash", record_type="SUPPORT_UNIT")
    meta_hash = ID.canonical_hash(
        {"record_type": "SUPPORT_UNIT", "proposition_id": "p-meta",
         "quoted_speaker_id": split["metaclaim"]["speaker_id"]},
        family="semantic_identity_hash", record_type="SUPPORT_UNIT")
    assert fact_hash != meta_hash


# ---------------------------------------------------------------------------
# 10–14 — canonical serialization
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("field,family", [
    ("translation_language", "evidence_content_hash"),
    ("is_translation", "evidence_content_hash"),
    ("translation_of_id", "semantic_identity_hash"),
    ("title_and_metadata_context", "evidence_content_hash"),
    ("target_role", "semantic_identity_hash"),
])
def test_m10_to_m14_identity_bearing_fields_are_hashed(field, family):
    """Each of these fell outside a V5.6 hash; each must move one now."""
    assert field in ID.IDENTITY_FIELDS[family]
    payload = {
        "record_type": "SUPPORT_UNIT", "schema_version": ID.CANONICAL_SCHEMA_VERSION,
        "proposition_id": "p", "evidence_bundle_id": "b",
        "translation_language": None, "is_translation": False,
        "translation_of_id": None, "title_and_metadata_context": "title",
        "target_role": "PRIMARY", "original_language": "en",
    }
    before = ID.canonical_hash(payload, family=family, record_type="SUPPORT_UNIT")
    mutated = dict(payload)
    mutated[field] = ("de" if field == "translation_language"
                      else True if field == "is_translation"
                      else "MUTATED")
    after = ID.canonical_hash(mutated, family=family, record_type="SUPPORT_UNIT")
    assert after != before, f"{field} is not covered by {family}"


def test_m10b_original_and_translation_never_share_a_manifestation():
    original = ID.manifestation_identity(
        source_id="doc", language="en", is_translation=False,
        translation_of_id=None, intellectual_work_id="work")
    translation = ID.manifestation_identity(
        source_id="doc", language="de", is_translation=True,
        translation_of_id="doc", intellectual_work_id="work")
    assert original["MANIFESTATION_IDENTITY"] != translation["MANIFESTATION_IDENTITY"]
    assert original["INTELLECTUAL_WORK_IDENTITY"] == \
        translation["INTELLECTUAL_WORK_IDENTITY"]


def test_hash_coverage_refuses_an_unexplained_exclusion():
    report = ID.hash_field_coverage("SUPPORT_UNIT", {
        "record_type": "SUPPORT_UNIT", "proposition_id": "p",
        "a_field_nobody_declared": 1})
    assert report["verdict"] == "FAIL"
    assert "a_field_nobody_declared" in report["unexplained_excluded_fields"]


# ---------------------------------------------------------------------------
# 15–17 — the dependency cone and the split
# ---------------------------------------------------------------------------

def test_m15_reuse_an_affected_record_without_re_adjudication():
    with pytest.raises(G.GateViolation, match="reused without re-adjudication"):
        G.assert_affected_records_readjudicated(
            affected_object_ids=["p-1", "p-2"],
            reference_records=[{"object_id": "p-1",
                                "lineage_disposition": "REUSED_DEPENDENCY_PROVEN"},
                               {"object_id": "p-2",
                                "lineage_disposition": "REGENERATED_AFFIRMED"}])


def test_m16_dependency_uncertain_record_labelled_unaffected():
    """A reuse claim without its complete proof is refused."""
    with pytest.raises(G.GateViolation, match="fewer than 6 proof parts"):
        G.assert_reuse_has_dependency_proof(
            [{"object_id": "p-1", "proof": ["a", "b", "c"]}])
    G.assert_reuse_has_dependency_proof(
        [{"object_id": "p-1", "proof": list("abcdef")}])


def test_m17_repartition_a_translation_away_from_its_original():
    """Partition follows the intellectual work, not the manifestation hash."""
    original = ID.manifestation_identity(
        source_id="doc", language="en", is_translation=False,
        translation_of_id=None, intellectual_work_id="work-1")
    translation = ID.manifestation_identity(
        source_id="doc", language="fr", is_translation=True,
        translation_of_id="doc", intellectual_work_id="work-1")
    # Distinct manifestations, one work — so any split anchored on the work id
    # keeps them together no matter how the manifestation hash moved.
    assert original["MANIFESTATION_IDENTITY"] != translation["MANIFESTATION_IDENTITY"]
    assert original["INTELLECTUAL_WORK_IDENTITY"] == \
        translation["INTELLECTUAL_WORK_IDENTITY"]
    partition = {original["INTELLECTUAL_WORK_IDENTITY"]: "REFERENCE_DEVELOPMENT"}
    assert partition[original["INTELLECTUAL_WORK_IDENTITY"]] == \
        partition[translation["INTELLECTUAL_WORK_IDENTITY"]]


# ---------------------------------------------------------------------------
# 18–20 — the panel
# ---------------------------------------------------------------------------

def test_m18_expose_v5_6_labels_to_repair_reviewers():
    for field in ("v5_6_reference_id", "primary_decisions", "final_decision",
                  "adjudication_outcome", "legacy_ambiguous_wording_field",
                  "exact_quotation_permitted", "partition", "defect_ids"):
        assert field in PK.FORBIDDEN_PACKET_KEYS, field
    audit = PK.audit_blinding([{"packet_id": "p", "v5_6_reference_id": "x"}])
    assert audit["verdict"] == "FAIL"
    audit = PK.audit_blinding([{"packet_id": "p"}],
                              paths=["16_primary/seats/development/a"])
    assert audit["verdict"] == "FAIL"
    assert audit["path_leaks"] >= 1


def test_m19_overwrite_a_primary_repair_decision(tmp_path):
    seat = CU.RepairSeat.open(
        seat_id="REVIEWER_A_REPAIR", seat_root=tmp_path / "a",
        run_salt="salt", session_id="s", assignment_order=["u0", "u1"])
    seat.append(dossier_id="u0", payload={"unit_id": "u0", "answer": "first"})
    # Rewriting the row on disk breaks the chain, and the seat refuses to build
    # on a file it did not write.
    # An honest overwrite attempt through the API is refused outright: a seat
    # writes its assignment in order and never revisits a dossier.
    with pytest.raises(CU.CustodyViolation, match="out of order|dossier"):
        seat.append(dossier_id="u0", payload={"unit_id": "u0", "answer": "again"})

    # Editing the row on disk instead leaves the chain head intact but breaks
    # the link, and both verification and sealing refuse it.  The point is that
    # a tampered seat can never be finalized, so its rows never reach the
    # reference: §8.6 gates the panel behind three intact chains.
    records = seat.seat_root / "records.jsonl"
    rows = [json.loads(line) for line in records.read_text().splitlines()]
    rows[0]["payload"] = {"unit_id": "u0", "answer": "overwritten"}
    records.write_text(json.dumps(rows[0]) + "\n")
    verification = CU.verify_chain(seat.seat_root)
    assert not verification["chain_intact"]
    assert verification["break_index"] == 0
    seat.append(dossier_id="u1", payload={"unit_id": "u1", "answer": "second"})
    with pytest.raises(CU.CustodyViolation, match="hash chain is broken"):
        seat.finalize()


def test_m20_delete_repair_seat_dissent():
    decisions = [support_answer(), support_answer(support_class="NOT_SUPPORTED",
                                                  wording_permission="NO_PUBLICATION_PERMITTED",
                                                  disposition="REJECTED_UNSUPPORTED",
                                                  addresses_proposition=False,
                                                  support_completeness="ABSENT",
                                                  first_material_failure="addresses_proposition"),
                 support_answer()]
    pattern = R.agreement_pattern(decisions)
    assert pattern == "MAJORITY_TWO_ONE"
    assert len(R.dissent(decisions, ["A", "B", "C"])) == 1
    with pytest.raises(R.ReferenceViolation, match="dissent is never deleted"):
        R.ReferenceRecord(
            "r", "o", "SUPPORT", "b", "V5_6_1_PROTOCOL_1", "h1", "h2", "h3",
            tuple(decisions), ("A", "B", "C"), pattern, (), (),
            "AFFIRM_MAJORITY", "because", (), decisions[0], None,
            "REFERENCE_DEVELOPMENT", "v5-6-reference-x", (), "REGENERATED_AFFIRMED",
            "h4", "2026-07-25T00:00:00+00:00")


def test_material_disagreement_must_be_routed():
    decisions = [support_answer(), support_answer(support_class="PARTIAL_SUPPORT",
                                                  disposition="PUBLISHED_WITH_QUALIFICATION"),
                 support_answer()]
    with pytest.raises(R.ReferenceViolation, match="was not routed to an adjudicator"):
        R.build_reference_record(
            unit={"packet_id": "p", "protocol_version": "V5_6_1_PROTOCOL_1",
                  "semantic_identity_hash": "h1", "evidence_content_hash": "h2",
                  "review_packet_hash": "h3", "evidence_bundle_id": "b"},
            lineage={"object_id": "o", "object_type": "SUPPORT",
                     "partition": "REFERENCE_DEVELOPMENT",
                     "v5_6_reference_id": "x", "defect_ids": []},
            decisions=decisions, seats=["A", "B", "C"])


def test_a_defect_may_not_carry_a_semantic_label():
    decisions = [support_answer(), support_answer(), support_answer()]
    record = R.build_reference_record(
        unit={"packet_id": "p", "protocol_version": "V5_6_1_PROTOCOL_1",
              "semantic_identity_hash": "h1", "evidence_content_hash": "h2",
              "review_packet_hash": "h3", "evidence_bundle_id": "b"},
        lineage={"object_id": "o", "object_type": "SUPPORT",
                 "partition": "REFERENCE_DEVELOPMENT", "v5_6_reference_id": "x",
                 "defect_ids": []},
        decisions=decisions, seats=["A", "B", "C"],
        adjudication={"outcome": "PACKET_CONSTRUCTION_DEFECT",
                      "reasoning": "the packet posed no answerable question"})
    assert record.final_decision is None
    assert record.lineage_disposition == "INVALIDATED_CORPUS_DEFECT"


# ---------------------------------------------------------------------------
# 21–23 — development, freeze, validation
# ---------------------------------------------------------------------------

def test_m21_select_a_variant_with_insufficient_evidence():
    verdict = G.sufficient_evidence_for_variant_selection(
        nondefective_units=8, discriminating_units=2,
        fold_sizes={"eur-lex": 3, "cert": 1}, family_shares={"eur-lex": 0.8},
        uncertainty_estimated=False)
    assert verdict["verdict"] == "INSUFFICIENT_EVIDENCE"
    assert len(verdict["reasons_against"]) >= 4
    ok = G.sufficient_evidence_for_variant_selection(
        nondefective_units=40, discriminating_units=15,
        fold_sizes={"a": 10, "b": 12, "c": 8},
        family_shares={"a": 0.33, "b": 0.4, "c": 0.27}, uncertainty_estimated=True)
    assert ok["verdict"] == "SUFFICIENT"


def test_m21b_a_singleton_fold_cannot_carry_a_stability_claim():
    verdict = G.sufficient_evidence_for_variant_selection(
        nondefective_units=40, discriminating_units=15,
        fold_sizes={"a": 30, "b": 1}, family_shares={"a": 0.75},
        uncertainty_estimated=True)
    assert verdict["verdict"] == "INSUFFICIENT_EVIDENCE"
    assert verdict["folds_below_minimum"] == {"b": 1}


def test_m22_open_validation_before_the_candidate_freeze(tmp_path):
    seal = G.ValidationSeal(seal_path=tmp_path / "seal.json",
                            candidate_freeze_path=tmp_path / "freeze.json")
    assert seal.sealed
    with pytest.raises(G.GateViolation, match="no post-reference candidate freeze"):
        seal.open(development_complete=True, validation_packet_hashes={})


def test_m22b_validation_opens_exactly_once(tmp_path):
    freeze = tmp_path / "freeze.json"
    freeze.write_text(json.dumps({"integrity_hash": "abc"}))
    seal = G.ValidationSeal(seal_path=tmp_path / "seal.json",
                            candidate_freeze_path=freeze)
    seal.open(development_complete=True, validation_packet_hashes={"u": "h"})
    assert seal.open_events == 1
    with pytest.raises(G.GateViolation, match="exactly one opening"):
        seal.open(development_complete=True, validation_packet_hashes={"u": "h"})


def test_m23_tune_after_validation(tmp_path):
    freeze = tmp_path / "freeze.json"
    freeze.write_text(json.dumps({"integrity_hash": "abc"}))
    seal = G.ValidationSeal(seal_path=tmp_path / "seal.json",
                            candidate_freeze_path=freeze)
    seal.open(development_complete=True, validation_packet_hashes={})
    G.assert_no_post_validation_change(seal=seal, candidate_freeze_hash_now="abc")
    with pytest.raises(G.GateViolation, match="tuning on validation"):
        G.assert_no_post_validation_change(seal=seal,
                                           candidate_freeze_hash_now="tuned")


# ---------------------------------------------------------------------------
# 24–26 — custody, prior artifacts, the kernel
# ---------------------------------------------------------------------------

def test_m24_accept_a_foreign_reviewer_write(tmp_path):
    seat = CU.RepairSeat.open(
        seat_id="REVIEWER_A_REPAIR", seat_root=tmp_path / "a", run_salt="salt",
        session_id="s", assignment_order=["u0"])
    with pytest.raises(CU.CustodyViolation, match="foreign seat id"):
        seat.append(dossier_id="u0", payload={"unit_id": "u0"},
                    seat_id="REVIEWER_B_REPAIR")
    with pytest.raises(CU.CustodyViolation, match="capability token invalid"):
        seat.append(dossier_id="u0", payload={"unit_id": "u0"},
                    capability_token="wrong")
    with pytest.raises(CU.CustodyViolation, match="process lease violated"):
        seat.append(dossier_id="u0", payload={"unit_id": "u0"},
                    process_id=os.getpid() + 1)
    with pytest.raises(CU.CustodyViolation, match="unknown V5.6.1 seat"):
        CU.RepairSeat.open(seat_id="REVIEWER_D_REPAIR", seat_root=tmp_path / "d",
                           run_salt="salt", session_id="s", assignment_order=["u0"])


def test_m24b_the_v5_4_namespace_is_not_widened():
    """Importing this module must not make V5.4's panel four seats wide."""
    from curunir_operational.v5_4 import custody as C4
    assert C4.SEAT_IDS == ("REVIEWER_A", "REVIEWER_B", "REVIEWER_C")


@pytest.mark.skipif(not V56_ROOT.exists(), reason="V5.6 artifacts absent")
def test_m25_alter_a_v5_6_artifact():
    import hashlib
    manifest_path = (ROOT / "artifacts/curunir_reference_standard_repair_v5_6_1_20260725"
                     / "00_baseline/v5_6_artifact_manifest.json")
    manifest = json.loads(manifest_path.read_text())
    hashes, paths = manifest.get("hashes") or {}, manifest.get("paths") or {}
    if not hashes or not paths:
        pytest.skip("baseline manifest carries no per-file hashes")
    # `paths` are relative to the V5.6 artifact root, not the repository root.
    hashes = {paths[name]: digest for name, digest in hashes.items() if name in paths}
    report = G.artifacts_unchanged(hashes, V56_ROOT)
    assert report["verdict"] == "PASS", report["examples"]
    tampered = dict(hashes)
    first = next(iter(tampered))
    tampered[first] = "0" * 64
    assert G.artifacts_unchanged(tampered, V56_ROOT)["verdict"] == "FAIL"


def test_m26_attempt_a_canonical_write():
    from curunir_operational.v4 import kernel as K
    monitor = K.ZeroWriteMonitor(ROOT)
    with pytest.raises(K.ZeroWriteViolation, match="connection refused"):
        monitor.refuse_connect(("127.0.0.1", 5432))
    with pytest.raises(K.ZeroWriteViolation, match="subprocess refused"):
        monitor.refuse_command(["psql", "-c", "INSERT INTO cases VALUES (1)"])
    assert monitor.verify()["canonical_writes"] == 0


def test_m26c_both_guards_cover_this_deployments_port():
    """Formerly a recorded gap; now a pinned repair.

    V4's monitor used to refuse only the conventional PostgreSQL ports, so a
    connection to this deployment's canonical store (5544) was not refused by
    port alone -- GATE-11 finding F-02.  V4 now derives its guarded set from the
    deployment's configuration, and V5.6.1's guard derives the port from the
    declared DSN.  This test pins both layers so neither can drift back.
    """
    from curunir_operational.v4 import kernel as K
    monitor = K.ZeroWriteMonitor(ROOT)
    canonical_port = G.canonical_dsn_port()
    assert canonical_port not in K.CONVENTIONAL_POSTGRES_PORTS
    assert canonical_port in monitor.guarded_ports
    with pytest.raises(K.ZeroWriteViolation, match="connection refused"):
        monitor.refuse_connect(("127.0.0.1", canonical_port))
    assert monitor.verify()["canonical_write_attempts"] == 1
    assert monitor.verify()["canonical_writes"] == 0

    guard = G.CanonicalWriteGuard()
    assert canonical_port in G.guarded_ports()
    with pytest.raises(G.CanonicalWriteAttempt, match=f"port {canonical_port}"):
        guard.refuse_connect(("127.0.0.1", canonical_port))
    with pytest.raises(G.CanonicalWriteAttempt, match="write statement refused"):
        guard.refuse_statement("INSERT INTO cases VALUES (1)")
    assert guard.report()["canonical_writes"] == 0
    assert guard.report()["canonical_write_attempts"] == 2


def test_m26b_no_v5_6_1_module_writes_to_the_kernel():
    """A static scan of the V5.6.1 modules for canonical write surfaces."""
    forbidden = ("INSERT INTO", "UPDATE ", "DELETE FROM", "psycopg", "execute(")
    offenders = []
    for path in (ROOT / "curunir_operational/v5_6_1").glob("*.py"):
        body = path.read_text()
        for marker in forbidden:
            if marker in body:
                offenders.append((path.name, marker))
    assert offenders == []
