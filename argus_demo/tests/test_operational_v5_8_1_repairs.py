"""V5.8.1 §41 — mutation and regression cover for the four pre-panel repairs.

Each repair removed a way for a signal to be attributed to something it does not
govern.  The tests below assert the repaired behaviour *and* the boundary the
repair must not cross, because the failure mode of every one of these fixes is
the same: making a class reachable by making it reachable too easily.
"""
from __future__ import annotations

import pytest

from curunir_operational.v5_4 import activation as ACT
from curunir_operational.v5_4 import roles_binary as RB
from curunir_operational.v5_8 import surfaces as SF
from curunir_operational.v5_8 import topology as TP

pytestmark = pytest.mark.no_db


# ---------------------------------------------------------------------------
# V581-D19 — the role classifier reads the kind the emitter actually writes
# ---------------------------------------------------------------------------
def test_observation_type_is_read_as_the_kind():
    verdict = RB.predict_role(
        source_id="s1", entity_id="peps.python.org", target_role="HOSTED_BY",
        observations=[{"observation_id": "o1", "observation_type": "DOMAIN_HOST",
                       "observed_value": "peps.python.org"}])
    assert verdict.prediction == "ESTABLISHED"


def test_the_two_older_field_names_still_work():
    for field in ("kind", "observation_kind"):
        verdict = RB.predict_role(
            source_id="s1", entity_id="example.org", target_role="HOSTED_BY",
            observations=[{"observation_id": "o1", field: "DOMAIN_HOST",
                           "observed_value": "example.org"}])
        assert verdict.prediction == "ESTABLISHED", field


def test_an_observation_with_no_kind_still_establishes_nothing():
    verdict = RB.predict_role(
        source_id="s1", entity_id="example.org", target_role="HOSTED_BY",
        observations=[{"observation_id": "o1", "observed_value": "example.org"}])
    assert verdict.prediction != "ESTABLISHED"


def test_the_host_is_still_not_the_publisher():
    verdict = RB.predict_role(
        source_id="s1", entity_id="peps.python.org", target_role="PUBLISHED_BY",
        observations=[{"observation_id": "o1", "observation_type": "DOMAIN_HOST",
                       "observed_value": "peps.python.org"}])
    assert verdict.prediction != "ESTABLISHED"


def test_an_observation_naming_another_entity_is_not_evidence():
    verdict = RB.predict_role(
        source_id="s1", entity_id="Bundesministerium der Justiz",
        target_role="HOSTED_BY",
        observations=[{"observation_id": "o1", "observation_type": "DOMAIN_HOST",
                       "observed_value": "peps.python.org"}])
    assert verdict.prediction != "ESTABLISHED"


def test_prohibited_role_inferences_are_still_declared():
    assert ("HOSTED_BY", "PUBLISHED_BY") in RB.PROHIBITED_INFERENCES
    assert ("ISSUED_BY", "PUBLISHED_BY") in RB.PROHIBITED_INFERENCES


# ---------------------------------------------------------------------------
# V581-D21 — a relation's target, read from the document that states it
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("text,expected", [
    ("Superseded-By : \n\n 649 , 749", "649"),
    ("Replaces : \n\n 563", "563"),
    ("Superseded By 345", "345"),
    ("supersedes ISO 8601:2004", "ISO 8601"),
    ("Replaced-By: 600", "600"),
    ("revises PEP 288", "PEP 288"),
    ("supersedes version 15.0.0", "15.0.0"),
])
def test_a_stated_designator_is_read_as_the_target(text, expected):
    assert ACT._target_after(text, 0) == expected


@pytest.mark.parametrize("text", [
    "updated : Apr 02, 2025",          # a date, not a designator
    "supersedes the 2016 guidance",    # a year, not a designator
    "last updated 12",                 # two digits, overwhelmingly a date
    "supersedes the previous guidance",  # names nobody
])
def test_a_date_or_an_unnamed_target_is_not_a_target(text):
    assert ACT._target_after(text, 0) is None


def test_the_eu_serial_still_wins_over_the_designator_form():
    assert ACT._target_after("ersetzt die Verordnung (EU) 2016/679", 0) == "2016/679"


def test_a_relation_naming_nobody_governs_no_pair():
    observation = ACT.RelationObservation(
        "o1", "SUPERSEDES", "supersedes", 0, "doc", "2026-07-25T00:00:00+00:00",
        None)
    assert ACT.governs_pair(observation, "563") is False


def test_a_relation_governs_only_the_pair_it_names():
    observation = ACT.RelationObservation(
        "o1", "SUPERSEDES", "Superseded-By: 649", 0, "doc",
        "2026-07-25T00:00:00+00:00", "649")
    assert ACT.governs_pair(observation, "649") is True
    assert ACT.governs_pair(observation, "563") is False


def test_the_label_form_of_replaces_is_in_the_vocabulary():
    found = ACT.detect_relations("PEP 563 - Postponed Evaluation\nReplaces : 3107\n",
                                 source_object_id="doc")
    assert any(o.relation in ("REPLACES", "SUPERSEDES") for o in found)


def test_supersession_reaches_the_temporal_classifier():
    left = "PEP 3107 - Function Annotations. This document describes annotations."
    right = ("PEP 563 - Postponed Evaluation of Annotations\nReplaces : 3107\n"
             "This document describes postponed evaluation.")
    klass, _ = ACT.classify_temporal(
        left, right,
        right_relations=ACT.detect_relations(right, source_object_id="r"),
        left_identifier="3107", right_identifier="563")
    assert klass in ACT.TEMPORAL_CLASSES
    assert klass != "NO_CONFLICT"


# ---------------------------------------------------------------------------
# V581-D20 — the adapter supplies what production consults
# ---------------------------------------------------------------------------
def _side(**kwargs):
    base = {"proposition_id": "p", "text": "text", "language": "en",
            "source_id": "s", "instrument_id": "w"}
    base.update(kwargs)
    return base


def test_the_work_identifier_reaches_dependence_not_the_fetch_identifier():
    row = SF.predict_dependence(
        left=_side(proposition_id="p1", source_id="s1", language="de",
                   text="Bundesdatenschutzgesetz", instrument_id="work-1"),
        right=_side(proposition_id="p2", source_id="s2", language="en",
                    text="Federal Data Protection Act", instrument_id="work-1"))
    assert row["prediction"] == "TRANSLATION_DERIVATIVE"
    assert row["structured_rationale"]["left_instrument"] == "work-1"


def test_two_different_works_are_not_a_translation_pair():
    row = SF.predict_dependence(
        left=_side(proposition_id="p1", language="de", instrument_id="work-1"),
        right=_side(proposition_id="p2", language="en", instrument_id="work-2"))
    assert row["prediction"] != "TRANSLATION_DERIVATIVE"


def test_the_citable_identifier_reaches_the_temporal_classifier():
    row = SF.predict_temporal(
        left=_side(proposition_id="p1", text="PEP 3107 annotations",
                   citable_identifier="3107", relation_context="PEP 3107"),
        right=_side(proposition_id="p2", text="PEP 563 annotations",
                    citable_identifier="563",
                    relation_context="PEP 563\nReplaces : 3107\n"))
    assert row["prediction"] != "NO_CONFLICT"


def test_a_surface_that_cannot_answer_raises_instead_of_returning_none(monkeypatch):
    """The V5.7 Surface 2 and Surface 6 defect: None for every unit, reported as
    a successful run.  Substituting a mute production callable must raise."""
    class Mute:
        prediction = None

    spec = dict(SF.SURFACES["SURFACE_2_SOURCE_ROLES"])
    spec["callable"] = lambda **kwargs: Mute()
    monkeypatch.setitem(SF.SURFACES, "SURFACE_2_SOURCE_ROLES", spec)
    with pytest.raises(SF.SurfaceFailure):
        SF.predict_role(source_id="s", entity_id="e", target_role="HOSTED_BY",
                        observations=[])


def test_every_surface_entry_point_still_resolves():
    manifest = SF.interface_manifest()
    assert manifest["guessed_production_apis"] == 0
    assert len(manifest["surfaces"]) == 6
    for spec in manifest["surfaces"].values():
        assert spec["code_hash"] and spec["callable"]


# ---------------------------------------------------------------------------
# Topology — the added motif did not lower the bar
# ---------------------------------------------------------------------------
def test_a_multilingual_cluster_still_needs_three_members():
    assert TP.minimum_members("multilingual_manifestation_cluster") == 3


def test_every_cluster_motif_needs_three_and_every_pair_motif_two():
    for motif in TP.CLUSTER_MOTIFS:
        assert TP.minimum_members(motif) == 3
    for motif in TP.PAIR_MOTIFS:
        assert TP.minimum_members(motif) == 2


def test_an_undeclared_motif_is_refused_rather_than_guessed():
    with pytest.raises(TP.TopologyViolation):
        TP.minimum_members("motif_invented_after_acquisition")
