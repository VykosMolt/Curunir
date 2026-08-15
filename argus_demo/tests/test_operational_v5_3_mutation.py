"""V5.3 mutation suite (contract Section 23).

Twenty-two mutations covering the new provenance, ranking and revision
boundaries. Prior V4/V5/V5.1/V5.2 mutation suites are retained, not repeated.
Each mutation must fail for the reason the repair exists.
"""
from __future__ import annotations

import hashlib
import json
import pathlib

import pytest

from conftest_v5_2_helpers import candidate_over
from curunir_operational.v5_1.models import NON_CORROBORATING_STATES
from curunir_operational.v5_2 import claim_support as CS
from curunir_operational.v5_2 import dossiers as v52_dossiers
from curunir_operational.v5_2 import lifecycle as L
from curunir_operational.v5_2 import reporting as R
from curunir_operational.v5_2 import semantics as v52_semantics
from curunir_operational.v5_3 import corpus, predict, ranking, revision, roles, scoring
from curunir_operational.v5_3 import observations as OB
from curunir_operational.v5_3 import provenance as P

pytestmark = pytest.mark.no_db

MUTATIONS: list[str] = []
ROOT = pathlib.Path(__file__).resolve().parents[1]
V5_3 = ROOT / "artifacts/curunir_production_grounded_epistemic_closure_v5_3_20260725"


def _record(name: str) -> None:
    MUTATIONS.append(name)


def _model() -> ranking.RankingModel:
    path = V5_3 / "03_extraction_ranking/extraction_ranker.json"
    return ranking.RankingModel.load(path) if path.exists() else ranking.RankingModel()


def _prediction(value: str = "ACCEPTED_CANDIDATE", surface: str | None = None,
                object_id: str = "object-1"):
    return P.record_prediction(
        surface=surface or "SURFACE_1_SEMANTIC_EXTRACTION",
        production_object_id=object_id, producer=v52_semantics.resolve_candidate,
        prediction=value, structured_rationale={})


def _observation(kind: str, value: str, strength: str = "EXPLICIT"):
    return OB.normalized_observation(
        raw_evidence_ids=[f"raw-{kind}"], observation_type=kind,
        observed_value=value, normalization="test", strength=strength)


# --- 1-2: the evaluator may not produce an answer ---------------------------

def test_mutation_1_evaluator_invents_a_production_prediction():
    _record("evaluator_invents_prediction")
    with pytest.raises(P.ProvenanceViolation, match="evaluation code"):
        P.record_prediction(surface="SURFACE_3_DEPENDENCE_AND_CORROBORATION",
                            production_object_id="p", producer=v52_dossiers.build_dossier,
                            prediction="TRANSLATION_DERIVATIVE", structured_rationale={})


def test_mutation_2_corpus_builder_substitutes_a_heuristic_label():
    _record("builder_substitutes_label")
    with pytest.raises(P.ProvenanceViolation, match="evaluation code"):
        P.record_prediction(surface="SURFACE_5_TEMPORAL_RELATIONS",
                            production_object_id="p", producer=corpus.seal,
                            prediction="NO_CONFLICT", structured_rationale={})


# --- 3-4: observation layer may not carry or leak a role --------------------

def test_mutation_3_raw_observation_presented_as_a_resolved_role():
    _record("observation_as_resolved_role")
    with pytest.raises(P.ProvenanceViolation, match="role name"):
        P.normalized_observation(raw_evidence_ids=["r"],
                                 observation_type="EXPLICIT_PUBLISHER_LINE",
                                 observed_value="PUBLISHED_BY", normalization="x")


def test_mutation_4_a_rendered_field_may_not_imply_the_answer():
    _record("normalized_field_leaks_answer")
    rendered = corpus.render_observation(
        _observation("INSTITUTIONAL_ATTRIBUTION", "European Commission"))
    assert rendered["semantic_role"] == corpus.REVIEWER_WITHHELD
    assert "publisher" not in json.dumps(rendered).casefold()


# --- 5-6: manifest integrity ------------------------------------------------

def test_mutation_5_prediction_manifest_modified_after_freeze():
    _record("manifest_modified_after_freeze")
    prediction = _prediction()
    with pytest.raises(P.ProvenanceViolation, match="integrity hash"):
        P.ProductionPredictionRecord(
            **{**prediction.to_record(), "prediction": "REJECTED"})


def test_mutation_6_scored_dossier_lacks_a_production_prediction():
    _record("scored_unit_without_prediction")
    comparison = P.compare(dossier_id="d", surface="SURFACE_1_SEMANTIC_EXTRACTION",
                           prediction=None, reviewer_majority="ACCEPTED_CANDIDATE",
                           reviewer_votes={"ACCEPTED_CANDIDATE": 3}, unanimous=True)
    assert comparison.outcome == "INVALID_FOR_CAPABILITY_SCORING"
    audit = P.provenance_audit([comparison])
    assert audit["scored_units"] == 0


# --- 7-9: extraction ranking invariants -------------------------------------

def test_mutation_7_ranker_selects_a_span_losing_modality():
    _record("ranker_loses_modality")
    text = "The Commission plans to deploy the system across the union in 2027."
    document, candidate = candidate_over(text, "deploy the system across the union")
    decision = ranking.rank_candidate(candidate, document, _model())
    assert decision.stage != "ACCEPTED_CANDIDATE"


def test_mutation_8_ranker_selects_a_span_changing_attribution():
    _record("ranker_changes_attribution")
    text = "The ministry published a plan. EuroHPC Joint Undertaking"
    document, candidate = candidate_over(text, "EuroHPC Joint Undertaking")
    decision = ranking.rank_candidate(candidate, document, _model())
    assert decision.stage != "ACCEPTED_CANDIDATE"


def test_mutation_9_a_wrong_accepted_extraction_cannot_survive_the_invariants():
    _record("wrong_accepted_extraction_survives")
    # Chrome with a perfect clause shape: the learned score is irrelevant.
    text = "We use cookies to improve your browsing experience on this website."
    document, candidate = candidate_over(text, text)
    model = ranking.RankingModel()
    for name in ranking.FEATURE_NAMES:  # bias the ranker hard toward acceptance
        model.weights["ACCEPTED_CANDIDATE"][name] = 99.0
    decision = ranking.rank_candidate(candidate, document, model)
    assert decision.top_unvetoed != "ACCEPTED_CANDIDATE" or decision.stage != "ACCEPTED_CANDIDATE"
    assert "STRUCTURAL_CHROME" in {v["reason"] for v in decision.vetoes}


# --- 10-11: source role and revision ----------------------------------------

def test_mutation_10_host_becomes_publisher_without_evidence():
    _record("host_becomes_publisher")
    resolution = roles.resolve_role([_observation("DOMAIN_HOST", "cdn.example.net")],
                                    "PUBLISHED_BY", subject_id="s")
    assert resolution.state == "REFUSED_PROHIBITED_INFERENCE"


def test_mutation_11_explicit_revises_relation_is_ignored():
    _record("revision_statement_ignored")
    statements = revision.find_revision_statements(
        "This SIB revises EASA SIB 2022-02R3 dated 15 May 2024.", document_id="d")
    assert statements and statements[0].dependence_class == "DERIVATIVE_CONFIRMED"
    prediction, _ = predict.predict_dependence(
        None, None, pair_id="p", left_text="This SIB revises EASA SIB 2022-02R3.",
        right_text="EASA SIB No.: 2022-02R3", left_identifiers=["2022-02R4"],
        right_identifiers=["2022-02R3"])
    assert prediction.structured_rationale["route"] == "EXPLICIT_REVISION_STATEMENT"


# --- 12-14: downstream epistemic invariants ---------------------------------

def test_mutation_12_unknown_dependence_becomes_corroboration():
    _record("unknown_dependence_as_corroboration")
    from curunir_operational.v5_1.dependence import CORROBORATING_STATES
    assert "INDEPENDENCE_UNKNOWN" in NON_CORROBORATING_STATES
    assert not (set(CORROBORATING_STATES) & set(NON_CORROBORATING_STATES))


def test_mutation_13_plan_becomes_implementation():
    _record("plan_becomes_implementation")
    support = CS.gated_assess_support(
        {"claim_id": "c", "proposition": "p", "lifecycle_state": "OPERATIONAL"},
        [{"evidence_id": "e", "normalized_statement":
          "Dann soll die Verbindung wieder in Betrieb gehen.", "language": "de"}])
    assert support.critical_error == "plan_as_implementation"
    assert support.support_state not in CS._AFFIRMATIVE_SUPPORT


def test_mutation_14_update_becomes_contradiction():
    _record("update_becomes_contradiction")
    from curunir_operational.v5_2 import temporal as T
    assert T.RELATION_TRIGGERS["TEMPORAL_UPDATE"] != \
        T.RELATION_TRIGGERS["LOGICAL_CONTRADICTION"]
    assert L.classify_lifecycle_error("DEPLOYED", "OPERATIONAL") is None


# --- 15-16: reporting -------------------------------------------------------

def _proposition(text: str, **kw):
    base = dict(text=text, supporting_claim_ids=("c1",), support_state="FULL_SUPPORT",
                lifecycle_state="POLICY_ADOPTED", evidence_act="FORMAL_POLICY_DECISION",
                materiality="ANSWERS_THE_RESEARCH_QUESTION")
    base.update(kw)
    return R.reportable_proposition(**base)


def test_mutation_15_report_proposition_stronger_than_its_claim():
    _record("proposition_stronger_than_claim")
    text = "The connection has been returned to operation."
    disposition = R.plan_proposition(
        _proposition(text, lifecycle_state="DEPLOYMENT_PLANNED",
                     evidence_act="ACTOR_INTENTION"), text)
    assert disposition.reason == "LIFECYCLE_STATE_STRENGTHENED"


def test_mutation_16_executive_summary_strengthens_lifecycle():
    _record("executive_summary_strengthens")
    published = [(_proposition("The council adopted the regulation."),
                  "The council adopted the regulation.")]
    result = R.check_executive_summary(
        ["The system is now operational across the union."], published)
    assert not result["entailed"]


# --- 17-19: coverage, freeze and human packets ------------------------------

def test_mutation_17_a_class_is_falsely_marked_exercised():
    _record("class_falsely_marked_exercised")
    matrix = V5_3 / "12_coverage_matrix/class_coverage_acquisition_matrix.json"
    if not matrix.exists():
        pytest.skip("coverage matrix not present in this checkout")
    payload = json.loads(matrix.read_text())
    for entry in payload.get("coverage_map", []):
        # A class may only be EXERCISED when it names the evidence that did it.
        if entry.get("status") == "EXERCISED":
            assert entry.get("exercised_by"), entry


def test_mutation_18_post_freeze_evaluator_edit_is_detected():
    _record("post_freeze_evaluator_edit")
    freeze = V5_3 / "14_freeze/production_evaluation_and_coverage_freeze.json"
    if not freeze.exists():
        pytest.skip("V5.3 freeze not yet written")
    manifest = json.loads(freeze.read_text())
    drift = []
    for group in ("production_files", "evaluator_files"):
        for name, entry in manifest.get(group, {}).items():
            observed = hashlib.sha256((ROOT / entry["path"]).read_bytes()).hexdigest()
            if observed != entry["sha256"]:
                # Manifest keys are logical names ("dependence_classifier") and
                # amendments record file paths, so drift must be carried as the
                # path.  Comparing the key against a path basename could never
                # match, which made every correctly recorded amendment fail.
                drift.append(entry["path"].rsplit("/", 1)[-1])
    amended = {a["file"].rsplit("/", 1)[-1] for a in manifest.get("amendments", ())}
    unrecorded = [n for n in drift if n not in amended]
    assert not unrecorded, f"frozen files changed without an amendment: {unrecorded}"


def test_mutation_19_human_packet_exposes_the_production_prediction():
    _record("human_packet_exposes_prediction")
    from curunir_operational.v5_2 import human_packets as HP
    sections = {name: {"state": "NOT_PRESENT"} for name in
                v52_dossiers.REQUIRED_SECTIONS["SOURCE_DOSSIER"]}
    dossier = v52_dossiers.build_dossier(
        kind="SOURCE_DOSSIER", decision_question="Which role?",
        decision_options=["PUBLISHED_BY", "HOSTED_BY"], sections=sections)
    packet = HP.convert(dossier)
    assert packet.system_answer_shown is False
    assert packet.review_mode == "INDEPENDENT_LABEL"
    with pytest.raises(ValueError, match="never show the system"):
        HP.HumanPacket(**{**packet.to_record(), "system_answer_shown": True})


# --- 20-22: downstream, replay and canonical --------------------------------

def test_mutation_20_stale_kernel_proposal_remains_valid():
    _record("stale_proposal_remains_valid")
    correction = L.migrate_claim({
        "claim_id": "h1",
        "normalized_statement": "Dann soll die Verbindung wieder in Betrieb gehen.",
        "language": "de", "modality": "OPERATIONAL"}, origin_milestone="V5_2")
    assert correction is not None and correction.error_class == "plan_as_implementation"


def test_mutation_21_replay_uses_the_network():
    _record("replay_uses_network")
    for name in ("provenance", "ranking", "observations", "roles", "revision",
                 "predict", "corpus", "scoring"):
        source = (ROOT / f"curunir_operational/v5_3/{name}.py").read_text()
        for forbidden in ("urllib.request", "requests.", "http.client", "socket.socket"):
            assert forbidden not in source, f"{name}.py reaches the network"


def test_mutation_22_canonical_write_attempted():
    _record("canonical_write_attempted")
    for path in sorted((ROOT / "curunir_operational/v5_3").glob("*.py")):
        text = path.read_text()
        for forbidden in ("psycopg", "import argus.db", "from argus import db",
                          "INSERT INTO", "insert into"):
            assert forbidden not in text, f"{path.name} reaches the canonical store"


def test_all_twenty_two_mutations_ran():
    assert len(set(MUTATIONS)) >= 22, sorted(set(MUTATIONS))
