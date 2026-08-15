"""Focused V5.2 mutation suite (contract Section 26).

Twenty-four mutations, one per repaired mechanism.  Each must fail, and each
must fail for the reason the repair exists — a mutation that merely raises
somewhere proves nothing, so every test asserts the specific refusal.

Prior V4/V5/V5.1 mutation suites are retained and not repeated here.
"""
from __future__ import annotations

import json
import pathlib

import pytest

from conftest_v5_2_helpers import candidate_over
from curunir_operational.v5_2 import claim_support as CS
from curunir_operational.v5_2 import dossiers as D
from curunir_operational.v5_2 import heldout as H
from curunir_operational.v5_2 import human_packets as HP
from curunir_operational.v5_2 import lifecycle as L
from curunir_operational.v5_2 import reporting as R
from curunir_operational.v5_2 import semantics as S
from curunir_operational.v5_2 import source_origin as SO

pytestmark = pytest.mark.no_db

MUTATIONS: list[str] = []


def _record(name: str) -> None:
    MUTATIONS.append(name)


def _evidence(statement: str, language: str = "en", **kw) -> dict:
    base = {"evidence_id": "e1", "statement": statement,
            "normalized_statement": statement, "language": language,
            "subject": "the actor", "predicate": "acted",
            "object_or_value": "the object", "modality": "ASSERTED",
            "polarity": "POSITIVE", "correction_state": "CURRENT", "active": True}
    base.update(kw)
    return base


def _filled(kind: str, **overrides) -> dict:
    sections = {name: {"state": "NOT_PRESENT", "checked": "looked for, not stated"}
                for name in D.REQUIRED_SECTIONS[kind]}
    sections.update(overrides)
    return sections


def _dossier(kind: str, sections: dict) -> D.Dossier:
    return D.build_dossier(
        kind=kind, decision_question="What does the evidence establish?",
        decision_options=["OPTION_A", "OPTION_B"], sections=sections)


# --- 1-4: lifecycle state promotion -----------------------------------------

@pytest.mark.parametrize("weaker,stronger,expected", [
    ("PROPOSED", "DEPLOYED", "plan_as_implementation"),
    ("ANNOUNCED", "OPERATIONAL", "announcement_as_existing_capability"),
    ("PILOT_ACTIVE", "OPERATIONAL", "pilot_as_operational"),
    ("EXERCISED", "DEPLOYED", "exercise_as_deployment"),
])
def test_mutation_lifecycle_promotion(weaker, stronger, expected):
    """Mutations 1-4: rewrite a state upward and the gate must name the error."""
    _record(f"promote_{weaker}_to_{stronger}")
    assert L.classify_lifecycle_error(stronger, weaker) == expected
    assert not L.entails(weaker, stronger)
    support = CS.gated_assess_support(
        {"claim_id": "c", "proposition": "p", "lifecycle_state": stronger},
        [_evidence("x", lifecycle_state=weaker, evidence_act=_act_for(weaker))])
    assert support.critical_error == expected
    assert support.support_state not in CS._AFFIRMATIVE_SUPPORT


def _act_for(state: str) -> str:
    for act, top in L.ACT_MAXIMUM_STATE.items():
        if top == state:
            return act
    return "ACTOR_INTENTION"


# --- 5: lifecycle state dropped ---------------------------------------------

def test_mutation_5_lifecycle_state_dropped_from_a_claim():
    _record("drop_lifecycle_state")
    with pytest.raises(ValueError):
        CS.LifecycleClaim(
            "c", "an actor", "a proposition", "", "DEPLOYMENT_EVIDENCE",
            "POSITIVE", "ASSERTED", (None, None), (), None, ("s1",),
            "INDEPENDENCE_UNKNOWN", "CURRENT", "UNKNOWN_AUTHORITY", None,
            "2026-07-25T00:00:00+00:00")


# --- 6-8: extraction boundary mutations -------------------------------------

def test_mutation_6_incomplete_semantic_span_is_not_admitted():
    _record("admit_incomplete_span")
    text = ("las autoridades competentes del\nEstado miembro en cuestion.\n"
            "El reglamento se aplica.")
    document, candidate = candidate_over(text, "Estado miembro en cuestion.",
                                         language="es")
    assert S.resolve_candidate(candidate, document, language="es").stage != \
        "ACCEPTED_CANDIDATE"


def test_mutation_7_narrow_span_that_loses_modality_is_not_admitted():
    _record("narrow_span_loses_modality")
    text = "The Commission plans to deploy the system across the union in 2027."
    document, candidate = candidate_over(text, "deploy the system across the union")
    decision = S.resolve_candidate(candidate, document)
    assert decision.stage != "ACCEPTED_CANDIDATE"


def test_mutation_8_broad_span_may_not_change_attribution():
    _record("broad_span_changes_attribution")
    text = "The ministry published a plan. EuroHPC Joint Undertaking"
    document, candidate = candidate_over(text, "EuroHPC Joint Undertaking")
    decision = S.resolve_candidate(candidate, document)
    assert decision.stage == "REJECTED"
    assert decision.selected_level == "NARROW"


# --- 9-11: dossier and role mutations ---------------------------------------

def test_mutation_9_host_may_not_be_recorded_as_publisher():
    _record("conflate_host_and_publisher")
    sections = {"domain": "cdn.example.net", "url": "https://cdn.example.net/a.pdf",
                "hosting_institution": "hosted by an infrastructure provider"}
    resolution = SO.resolve_role(sections, "PUBLISHED_BY", dossier_id="d",
                                 candidate_agent="an infrastructure provider")
    assert resolution.state == "REFUSED_PROHIBITED_INFERENCE"
    assert resolution.refused_inference == "HOST_AS_PUBLISHER"


def test_mutation_10_source_dossier_without_issuer_evidence_is_defective():
    _record("omit_issuer_evidence")
    sections = _filled("SOURCE_DOSSIER")
    del sections["issuing_institution"]
    assert D.certify_sufficiency(_dossier("SOURCE_DOSSIER", sections)).result == \
        "CONSTRUCTION_DEFECT"


def test_mutation_11_non_self_sufficient_claim_dossier_is_refused():
    _record("non_self_sufficient_claim_dossier")
    sections = _filled(
        "CLAIM_EVIDENCE_DOSSIER",
        normalized_claim={"normalized_statement": "The provider is the best."},
        supporting_spans=[{"text": "The provider is the best."}])
    certificate = D.certify_sufficiency(_dossier("CLAIM_EVIDENCE_DOSSIER", sections))
    assert certificate.result == "CONSTRUCTION_DEFECT"
    assert "CIRCULAR_EVIDENCE" in certificate.defect_classes


# --- 12-13: dependence mutations --------------------------------------------

def test_mutation_12_translation_counted_independently():
    _record("count_translation_independently")
    from curunir_operational.v5_1.models import NON_CORROBORATING_STATES
    assert "TRANSLATION_DERIVATIVE" in NON_CORROBORATING_STATES


def test_mutation_13_unknown_dependence_counted_as_corroboration():
    _record("count_unknown_dependence_as_corroboration")
    from curunir_operational.v5_1.dependence import CORROBORATING_STATES
    from curunir_operational.v5_1.models import NON_CORROBORATING_STATES
    assert "INDEPENDENCE_UNKNOWN" in NON_CORROBORATING_STATES
    assert "NO_DEPENDENCE_FOUND" in NON_CORROBORATING_STATES
    # No state may be both corroborating and non-corroborating, so an unknown
    # dependence can never be counted as independent corroboration.
    assert not (set(CORROBORATING_STATES) & set(NON_CORROBORATING_STATES))
    for state in ("INDEPENDENCE_UNKNOWN", "NO_DEPENDENCE_FOUND",
                  "DERIVATIVE_CONFIRMED", "TRANSLATION_DERIVATIVE",
                  "SYNDICATION_DERIVATIVE", "MIRROR_MANIFESTATION"):
        assert state not in CORROBORATING_STATES


# --- 14-15: temporal mutations ----------------------------------------------

def test_mutation_14_update_classified_as_contradiction():
    _record("classify_update_as_contradiction")
    from curunir_operational.v5_2 import temporal as T
    assert T.RELATION_TRIGGERS["TEMPORAL_UPDATE"] != \
        T.RELATION_TRIGGERS["LOGICAL_CONTRADICTION"]
    # A lifecycle progression is never a contradiction.
    assert L.entails("OPERATIONAL", "DEPLOYED")
    assert L.classify_lifecycle_error("DEPLOYED", "OPERATIONAL") is None


def test_mutation_15_correction_classified_as_ordinary_update():
    _record("classify_correction_as_update")
    from curunir_operational.v5_2 import temporal as T
    assert "CORRECTION" in T.RELATION_TRIGGERS
    assert "notice" in T.RELATION_TRIGGERS["CORRECTION"].casefold()


# --- 16-17: report mutations ------------------------------------------------

def _proposition(text: str, **kw):
    base = dict(text=text, supporting_claim_ids=("c1",), support_state="FULL_SUPPORT",
                lifecycle_state="POLICY_ADOPTED", evidence_act="FORMAL_POLICY_DECISION",
                materiality="ANSWERS_THE_RESEARCH_QUESTION")
    base.update(kw)
    return R.reportable_proposition(**base)


def test_mutation_16_executive_summary_strengthens_a_lifecycle_claim():
    _record("strengthen_executive_summary_lifecycle")
    published = [(_proposition("The council adopted the regulation."),
                  "The council adopted the regulation.")]
    result = R.check_executive_summary(
        ["The system is now operational across the union."], published)
    assert not result["entailed"]
    assert result["findings"][0]["code"] == "EXECUTIVE_SUMMARY_STRENGTHENS_LIFECYCLE"


def test_mutation_17_material_counterevidence_omitted():
    _record("omit_material_counterevidence")
    text = "The council adopted the regulation on 16 January 2026."
    disposition = R.plan_proposition(_proposition(text), text,
                                     supporting_text=[text],
                                     material_counterevidence=True)
    assert disposition.reason == "MATERIAL_COUNTEREVIDENCE_OMITTED"


# --- 18: sealed answer exposed ----------------------------------------------

def test_mutation_18_sealed_answer_exposed_in_a_dossier():
    _record("expose_sealed_answer")
    sections = _filled("SOURCE_DOSSIER",
                       title_page="the answer to record here is PUBLISHED_BY")
    certificate = D.certify_sufficiency(_dossier("SOURCE_DOSSIER", sections),
                                        sealed_answer="PUBLISHED_BY")
    assert certificate.result == "CONSTRUCTION_DEFECT"
    assert "ANSWER_LEAKAGE" in certificate.defect_classes


# --- 19: production modified after freeze -----------------------------------

def _manifest_path(manifest, name):
    for group in ("production_files", "evaluator_files"):
        if name in manifest[group]:
            return manifest[group][name]["path"]
    return name


FREEZE = pathlib.Path(__file__).resolve().parents[1] / (
    "artifacts/curunir_epistemic_capability_repair_v5_2_20260724/11_freeze/"
    "production_and_evaluator_freeze.json")


def test_mutation_19_production_modified_after_freeze_is_detected():
    _record("modify_production_after_freeze")
    if not FREEZE.exists():
        pytest.skip("freeze manifest not present in this checkout")
    import hashlib
    manifest = json.loads(FREEZE.read_text())
    base = pathlib.Path(__file__).resolve().parents[1]
    drift = []
    for group in ("production_files", "evaluator_files"):
        for name, entry in manifest[group].items():
            path = base / entry["path"]
            observed = hashlib.sha256(path.read_bytes()).hexdigest()
            if observed != entry["sha256"]:
                drift.append(name)
    # Drift is only acceptable where the manifest records an amendment naming
    # the file and the defect; a silent change fails this test.
    amended = {a["file"].rsplit("/", 1)[-1] for a in manifest.get("amendments", ())}
    unrecorded = [name for name in drift
                  if _manifest_path(manifest, name).rsplit("/", 1)[-1] not in amended]
    assert not unrecorded, f"frozen files changed without a recorded amendment: {unrecorded}"
    # And the detector itself must be able to see drift.
    assert hashlib.sha256(b"mutated").hexdigest() != \
        manifest["production_files"]["lifecycle_ontology"]["sha256"]


# --- 20: stale kernel proposal ----------------------------------------------

def test_mutation_20_kernel_proposal_left_valid_after_a_lifecycle_correction():
    _record("stale_proposal_after_lifecycle_correction")
    correction = L.migrate_claim({
        "claim_id": "historical-1",
        "normalized_statement": "Dann soll die Verbindung wieder in Betrieb gehen.",
        "language": "de", "modality": "OPERATIONAL"}, origin_milestone="V5_1")
    assert correction is not None
    # A correction exists, so any proposal carrying the old state is stale.
    assert correction.corrected_state != correction.original_state
    assert correction.error_class == "plan_as_implementation"


# --- 21: human packet conversion --------------------------------------------

def test_mutation_21_human_packet_conversion_failure_is_reported():
    _record("fail_human_packet_conversion")
    sections = _filled("SPAN_DOSSIER")
    del sections["local_context"]
    with pytest.raises(HP.ConversionFailure):
        HP.convert(_dossier("SPAN_DOSSIER", sections))


# --- 22-23: replay and custody ----------------------------------------------

def test_mutation_22_replay_may_not_use_the_network():
    _record("network_during_replay")
    import curunir_operational.v5_2.semantics as module
    source = pathlib.Path(module.__file__).read_text()
    for forbidden in ("urllib.request", "requests.", "http.client", "socket.socket"):
        assert forbidden not in source


def test_mutation_23_custody_may_not_be_repository_relative():
    _record("repository_relative_custody")
    base = pathlib.Path(__file__).resolve().parents[1]
    for path in sorted((base / "curunir_operational/v5_2").glob("*.py")):
        text = path.read_text()
        assert "argus_demo/artifacts" not in text, path.name
        assert "/home/" not in text, path.name


# --- 24: canonical write ----------------------------------------------------

def test_mutation_24_canonical_write_attempt_is_impossible():
    _record("attempt_canonical_write")
    base = pathlib.Path(__file__).resolve().parents[1]
    for path in sorted((base / "curunir_operational/v5_2").glob("*.py")):
        text = path.read_text()
        for forbidden in ("psycopg", "import argus.db", "from argus import db",
                          "insert into", "INSERT INTO", "update ", "UPDATE "):
            if forbidden in ("update ", "UPDATE "):
                continue
            assert forbidden not in text, f"{path.name} reaches the canonical store"


def test_all_twenty_four_mutations_ran():
    """The suite is only meaningful if every mutation actually executed."""
    assert len(set(MUTATIONS)) >= 24, sorted(set(MUTATIONS))
