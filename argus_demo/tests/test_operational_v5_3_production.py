"""Extraction ranking, source-role and revision-relation tests (Sections 6-8).

Each test names the V5.2 failure it closes.
"""
from __future__ import annotations

import pathlib

import pytest

from conftest_v5_2_helpers import candidate_over
from curunir_operational.v5_3 import observations as OB
from curunir_operational.v5_3 import ranking as RK
from curunir_operational.v5_3 import revision as RV
from curunir_operational.v5_3 import roles as RO

pytestmark = pytest.mark.no_db

RANKER = (pathlib.Path(__file__).resolve().parents[1] /
          "artifacts/curunir_production_grounded_epistemic_closure_v5_3_20260725"
          "/03_extraction_ranking/extraction_ranker.json")


def _model() -> RK.RankingModel:
    if RANKER.exists():
        return RK.RankingModel.load(RANKER)
    return RK.RankingModel()


# ---------------------------------------------------------------------------
# Section 6 — extraction ranking
# ---------------------------------------------------------------------------

def test_the_ranker_is_transparent_data():
    model = _model()
    record = model.to_record()
    assert set(record["outcomes"]) == set(RK.OUTCOMES)
    assert len(record["feature_names"]) == len(RK.FEATURE_NAMES)
    for outcome in RK.OUTCOMES:
        assert set(record["weights"][outcome]) == set(RK.FEATURE_NAMES)


def test_fitting_is_deterministic():
    examples = [({name: 1.0 if name == "is_chrome" else 0.0
                  for name in RK.FEATURE_NAMES}, "QUARANTINED"),
                ({name: 1.0 if name == "finite_clause" else 0.0
                  for name in RK.FEATURE_NAMES}, "ACCEPTED_CANDIDATE")]
    first, second = RK.RankingModel(), RK.RankingModel()
    first.fit(examples, epochs=4)
    second.fit(examples, epochs=4)
    assert first.weights == second.weights
    assert first.bias == second.bias


def test_every_decision_records_its_whole_ranking():
    document, candidate = candidate_over(
        "The Council adopted the regulation on 16 January 2026.",
        "The Council adopted the regulation on 16 January 2026.")
    decision = RK.rank_candidate(candidate, document, _model())
    assert set(decision.ranked_scores) == set(RK.OUTCOMES)
    assert decision.top_unvetoed in RK.OUTCOMES
    assert set(decision.features) == set(RK.FEATURE_NAMES)


@pytest.mark.parametrize("text,reason", [
    ("We use cookies to improve your browsing experience.", "STRUCTURAL_CHROME"),
    ("Hat diese Stoerung Auswirkungen gehabt?", "INTERROGATIVE"),
    ("( 8 ) Decision No 768/2008/EC of the European Parliament of 9 July 2008.",
     "BIBLIOGRAPHIC_CITATION"),
])
def test_hard_invariants_veto_acceptance(text, reason):
    document, candidate = candidate_over(text, text)
    decision = RK.rank_candidate(candidate, document, _model())
    assert decision.stage != "ACCEPTED_CANDIDATE"
    assert reason in {v["reason"] for v in decision.vetoes}


def test_an_unresolved_deictic_may_not_be_accepted():
    # Five of the eleven V5.2-era wrong admissions had this shape: a
    # well-formed sentence whose referent lies outside the span.
    text = ("Die Bahn modernisiert den Bahnhof. Dabei stehen insbesondere die "
            "barrierefreie Modernisierung sowie eine attraktivere Gestaltung im "
            "Mittelpunkt.")
    needle = ("Dabei stehen insbesondere die barrierefreie Modernisierung sowie "
              "eine attraktivere Gestaltung im Mittelpunkt.")
    document, candidate = candidate_over(text, needle, language="de")
    decision = RK.rank_candidate(candidate, document, _model(), language="de")
    assert decision.stage != "ACCEPTED_CANDIDATE"
    assert "UNRESOLVED_DEICTIC" in {v["reason"] for v in decision.vetoes}


def test_a_list_item_without_its_stem_may_not_be_accepted():
    text = "ANNEX XI\n2.\nA description of the testing measures applied.\n3.\nAnother item."
    document, candidate = candidate_over(
        text, "A description of the testing measures applied.")
    decision = RK.rank_candidate(candidate, document, _model())
    assert decision.stage != "ACCEPTED_CANDIDATE"


def test_a_well_formed_sentence_is_still_accepted():
    text = ("The European Commission and the Bank Group have joined forces to "
            "support the initiative.")
    document, candidate = candidate_over(text, text)
    assert RK.rank_candidate(candidate, document, _model()).stage == \
        "ACCEPTED_CANDIDATE"


def test_a_veto_is_recorded_even_when_the_ranker_disagreed():
    document, candidate = candidate_over(
        "We use cookies to improve your browsing experience.",
        "We use cookies to improve your browsing experience.")
    decision = RK.rank_candidate(candidate, document, _model())
    assert decision.vetoes
    assert "vetoed" in decision.rationale or decision.stage == decision.top_unvetoed


def test_ranking_report_separates_agreement_from_override():
    document, candidate = candidate_over(
        "The Council adopted the regulation on 16 January 2026.",
        "The Council adopted the regulation on 16 January 2026.")
    decision = RK.rank_candidate(candidate, document, _model())
    report = RK.ranking_report([decision])
    assert report["decisions"] == 1
    assert report["ranker_top_choice_taken"] + report["ranker_top_choice_vetoed"] == 1


# ---------------------------------------------------------------------------
# Section 7 — source evidence and roles
# ---------------------------------------------------------------------------

def _observation(kind: str, value: str, strength: str = "EXPLICIT",
                 mode: str = "EXPLICIT"):
    return OB.normalized_observation(
        raw_evidence_ids=[f"raw-{kind}"], observation_type=kind,
        observed_value=value, normalization="test", mode=mode, strength=strength)


def test_combined_observations_can_establish_a_role_without_a_magic_marker():
    # V5.2 required the literal words "issued by"; an official act identifies
    # its issuer through its series and its front matter instead.
    observations = [
        _observation("DOCUMENT_SERIES_OWNER", "Official Journal of the European Union"),
        _observation("INSTITUTIONAL_ATTRIBUTION", "Council of the European Union",
                     strength="STRONGLY_IMPLIED", mode="INFERRED"),
    ]
    resolution = RO.resolve_role(observations, "ISSUED_BY", subject_id="s1")
    assert resolution.state == "RESOLVED"
    assert resolution.agent_value == "Council of the European Union"
    assert resolution.support_weight >= RO.SUPPORT_THRESHOLD


def test_one_weak_observation_does_not_establish_a_role():
    observations = [_observation("DOCUMENT_IDENTIFIER", "SIB 2022-02R4",
                                 strength="WEAKLY_IMPLIED")]
    assert RO.resolve_role(observations, "ISSUED_BY", subject_id="s1").state == \
        "UNRESOLVED_EVIDENCE_ABSENT"


def test_a_decisive_observation_settles_a_role_alone():
    observations = [_observation("EXPLICIT_PUBLISHER_LINE", "Publications Office")]
    resolution = RO.resolve_role(observations, "PUBLISHED_BY", subject_id="s1")
    assert resolution.state == "RESOLVED"
    assert resolution.decisive_observation_ids


def test_conflicting_decisive_observations_do_not_force_a_role():
    observations = [_observation("EXPLICIT_PUBLISHER_LINE", "Publisher One"),
                    _observation("EXPLICIT_PUBLISHER_LINE", "Publisher Two")]
    assert RO.resolve_role(observations, "PUBLISHED_BY", subject_id="s1").state == \
        "UNRESOLVED_EVIDENCE_CONFLICTS"


def test_a_host_is_still_not_a_publisher():
    observations = [_observation("DOMAIN_HOST", "cdn.example.net")]
    resolution = RO.resolve_role(observations, "PUBLISHED_BY", subject_id="s1")
    assert resolution.state == "REFUSED_PROHIBITED_INFERENCE"
    assert resolution.refused_inference == "HOST_AS_PUBLISHER"
    assert RO.resolve_role(observations, "HOSTED_BY", subject_id="s1").state == \
        "RESOLVED"


def test_an_uploader_is_still_not_an_author():
    observations = [_observation("EXPLICIT_SUBMISSION_LINE", "submitted by J. Doe")]
    assert RO.resolve_role(observations, "AUTHORED_BY", subject_id="s1"
                           ).refused_inference == "UPLOADER_AS_AUTHOR"


def test_every_role_has_a_decisive_observation_kind():
    from curunir_operational.v5_1.models import SOURCE_ROLES
    assert set(RO.DECISIVE) == set(SOURCE_ROLES)


def test_component_diagnostics_separate_capture_from_resolver():
    resolutions = list(RO.resolve_all([], subject_id="s1"))
    report = RO.component_diagnostics(resolutions, observation_count=0)
    assert report["failure_components"]["RAW_EVIDENCE_CAPTURE_FAILURE"] == 13
    assert report["resolved"] == 0


def test_observation_capture_reports_no_role_bearing_observations():
    observations = [_observation("DOMAIN_HOST", "example.org")]
    assert OB.capture_report(observations)["role_bearing_observations"] == 0


# ---------------------------------------------------------------------------
# Section 8 — explicit revision relations
# ---------------------------------------------------------------------------

def test_the_v5_2_revision_miss_is_closed():
    statements = RV.find_revision_statements(
        "SIB No.: 2022-02R4\nThis SIB revises EASA SIB 2022-02R3 dated 15 May 2024.",
        document_id="doc-1")
    assert statements
    assert statements[0].relation == "REVISES"
    assert statements[0].dependence_class == "DERIVATIVE_CONFIRMED"
    assert statements[0].temporal_relation == "SUPERSESSION"


def test_a_pair_relation_is_found_from_either_side():
    statement = RV.relate_documents(
        "This SIB revises EASA SIB 2022-02R3 dated 15 May 2024.",
        "EASA SIB No.: 2022-02R3", left_id="a", right_id="b",
        left_identifiers=["2022-02R4"], right_identifiers=["2022-02R3"])
    assert statement is not None
    assert statement.dependence_class == "DERIVATIVE_CONFIRMED"


@pytest.mark.parametrize("text,language,expected", [
    ("Berichtigung zu Verordnung (EU) 2023/1115", "de", "CORRIGENDUM_TO"),
    ("Rectificatif au reglement (UE) 2023/1115", "fr", "CORRIGENDUM_TO"),
    ("Correccion de errores del Reglamento (UE) 2023/1115", "es", "CORRIGENDUM_TO"),
    ("Regulation amending Regulation (EU) 2021/1173", "en", "AMENDS"),
    ("This decision replaces Decision 2019/1234", "en", "REPLACES"),
])
def test_revision_language_is_multilingual(text, language, expected):
    statements = RV.find_revision_statements(text, document_id="d", language=language)
    assert expected in {s.relation for s in statements}


def test_every_revision_relation_projects_into_the_ontologies():
    for relation in RV.REVISION_RELATIONS:
        projection = RV.RELATION_PROJECTION[relation]
        assert projection["content_relation"]
        assert projection["dependence"]
        assert projection["temporal"]


def test_a_revision_statement_must_name_its_target():
    with pytest.raises(ValueError, match="name what it revises"):
        RV.RevisionStatement("s", "REVISES", "doc", "", "matched", 0, 1, "en",
                             "SUPERSEDES", "DERIVATIVE_CONFIRMED", "SUPERSESSION",
                             "2026-07-25T00:00:00+00:00")
