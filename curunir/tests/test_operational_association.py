"""Association: cautious outcomes, human-resolved proposals, and merges that can
be reversed without losing anything."""
from __future__ import annotations

import pytest

from curunir_operational.association import AssociationEngine, compute_features, decide
from curunir_operational.contracts import ExternalRef, ObjectVersion, ProvenanceSummary
from curunir_operational.geometry import Geometry
from curunir_operational.projection import Projection

from operational_support import BASE_MARKING, T0, make_store, t

pytestmark = pytest.mark.no_db

PROV_A = ProvenanceSummary(mode="OPERATIONAL", source_ids=("src-a",))
PROV_B = ProvenanceSummary(mode="OPERATIONAL", source_ids=("src-b",))


def movement(object_id, version=1, *, refs=(), lon=-30.0, lat=45.0, hours=0.0, attributes=None,
             prov=PROV_A, object_type="MOVEMENT"):
    return ObjectVersion(
        object_id=object_id, version=version, object_type=object_type, lifecycle="ACTIVE",
        labels=(object_id,), external_refs=tuple(ExternalRef(*r) for r in refs),
        valid_from=t(hours), valid_to=None, source_time=t(hours), time_precision="HOUR",
        recorded_time=t(hours), geometry=Geometry("POINT", (lon, lat)),
        attributes=attributes or {}, quality={}, epistemic_state="REPORTED",
        marking=BASE_MARKING, provenance=prov,
    )


def seed(store, *versions):
    for version in versions:
        store.append("OBJECT_VERSION_APPENDED", version, recorded_time=version.recorded_time, actor="fixture")


def test_explicit_identifier_match_auto_associates(tmp_path):
    store = make_store(tmp_path)
    seed(store,
         movement("mv-a", refs=[("REGISTRY", "CV-100", "1", "ing-1", None, "SYNCHRONIZED")]),
         movement("mv-b", hours=1.0, prov=PROV_B, refs=[("REGISTRY", "CV-100", "2", "ing-2", None, "SYNCHRONIZED")]))
    engine = AssociationEngine(store)
    proposal = engine.evaluate("mv-a", "mv-b", recorded_time=t(2), actor="assoc-engine", marking=BASE_MARKING)
    assert proposal["outcome"] == "AUTO_ASSOCIATE"
    relationships = store.records_of("relationship_version")
    assert any(r["relation_type"] == "SAME_AS" and r["status"] == "ACTIVE" for r in relationships)
    resolutions = store.records_of("association_resolution")
    assert resolutions and resolutions[0]["actor_kind"] == "SERVICE"


def test_ambiguous_proximity_stays_proposal(tmp_path):
    store = make_store(tmp_path)
    seed(store, movement("mv-a", lon=-30.00, lat=45.00),
         movement("mv-b", hours=1.0, lon=-30.05, lat=45.01, prov=PROV_B))
    engine = AssociationEngine(store)
    proposal = engine.evaluate("mv-a", "mv-b", recorded_time=t(2), actor="assoc-engine", marking=BASE_MARKING)
    assert proposal["outcome"] == "PROPOSE_ASSOCIATION"
    current = store.records_of("relationship_version")[-1]
    assert current["relation_type"] == "POSSIBLY_SAME_AS" and current["status"] == "PROPOSED"


def test_contradiction_blocks_and_stays_unknown():
    left = movement("mv-a", attributes={"cargo": "fuel"}).to_record()
    right = movement("mv-b", attributes={"cargo": "medical"}, prov=PROV_B).to_record()
    features = compute_features(left, right)
    outcome, rationale = decide(features)
    assert features["contradictory_attributes"] == ["cargo"]
    assert outcome == "UNKNOWN"


def test_rejections():
    truck = movement("mv-a").to_record()
    depot = movement("dep-x", object_type="INFRASTRUCTURE", prov=PROV_B).to_record()
    assert decide(compute_features(truck, depot))[0] == "REJECT_ASSOCIATION"
    same_system_conflict = compute_features(
        movement("mv-a", refs=[("LOGSYS", "CV-1", "1", "i", None, "SYNCHRONIZED")]).to_record(),
        movement("mv-b", refs=[("LOGSYS", "CV-2", "1", "i", None, "SYNCHRONIZED")], prov=PROV_B).to_record())
    assert decide(same_system_conflict)[0] == "REJECT_ASSOCIATION"
    negative = compute_features(truck, movement("mv-b", prov=PROV_B).to_record(),
                                negative_evidence=("both groups observed simultaneously at distinct checkpoints",))
    assert decide(negative)[0] == "REJECT_ASSOCIATION"
    teleport = compute_features(movement("mv-a", lon=-30.0).to_record(),
                                movement("mv-b", lon=-25.0, hours=0.5, prov=PROV_B).to_record())
    assert decide(teleport)[0] == "REJECT_ASSOCIATION"  # about 390 km in half an hour


def test_accept_then_reverse_is_non_destructive(tmp_path):
    store = make_store(tmp_path)
    seed(store, movement("mv-a", lon=-30.00), movement("mv-b", hours=1.0, lon=-30.05, prov=PROV_B))
    engine = AssociationEngine(store)
    proposal = engine.evaluate("mv-a", "mv-b", recorded_time=t(2), actor="assoc-engine", marking=BASE_MARKING)
    engine.resolve(proposal["proposal_id"], "ACCEPTED", actor_id="analyst-vale", actor_kind="HUMAN",
                   rationale="checkpoint log confirms single convoy", recorded_time=t(3), marking=BASE_MARKING)
    projection = Projection(store)
    assert projection.cluster_of["mv-a"] == projection.cluster_of["mv-b"]
    engine.resolve(proposal["proposal_id"], "REVERSED", actor_id="analyst-vale", actor_kind="HUMAN",
                   rationale="second convoy confirmed by later checkpoint", recorded_time=t(4), marking=BASE_MARKING)
    projection = Projection(store)
    assert projection.cluster_of["mv-a"] != projection.cluster_of["mv-b"]
    # Both versions and both relationships are still on the log.
    assert {v["object_id"] for v in store.records_of("object_version")} == {"mv-a", "mv-b"}
    same_as = [r for r in store.records_of("relationship_version") if r["relation_type"] == "SAME_AS"]
    assert [r["status"] for r in same_as] == ["ACTIVE", "RETIRED"]


def test_split_resolution_rejects_proposed_link(tmp_path):
    store = make_store(tmp_path)
    seed(store, movement("mv-a", lon=-30.00), movement("mv-b", hours=1.0, lon=-30.05, prov=PROV_B))
    engine = AssociationEngine(store)
    proposal = engine.evaluate("mv-a", "mv-b", recorded_time=t(2), actor="assoc-engine", marking=BASE_MARKING)
    engine.resolve(proposal["proposal_id"], "SPLIT", actor_id="analyst-vale", actor_kind="HUMAN",
                   rationale="distinct plate groups on checkpoint imagery", recorded_time=t(3), marking=BASE_MARKING)
    possibly = [r for r in store.records_of("relationship_version") if r["relation_type"] == "POSSIBLY_SAME_AS"]
    assert [r["status"] for r in possibly] == ["PROPOSED", "REJECTED"]
