"""The source registry: what each source can do, answered from the replayed log."""
from __future__ import annotations

import pytest

from argus.source_intelligence.models import SourceDescriptor
from curunir_fabric.catalog import starter_catalog, seed_starter_catalog
from curunir_fabric.contracts import CapabilityProfile
from curunir_fabric.registry import load_registry, record_source_status, register_source
from curunir_fabric.store import FabricStore

pytestmark = pytest.mark.no_db

NOW = "2026-08-15T12:00:00+00:00"
LATER = "2026-08-15T13:00:00+00:00"


@pytest.fixture()
def store(tmp_path):
    store = FabricStore.create(tmp_path / "store", "registry-test", NOW)
    seed_starter_catalog(store, recorded_time=NOW, actor="t")
    return store


def test_catalog_registers_all_starter_sources(store):
    view = load_registry(store)
    ids = {d.source_id for d in view.current_descriptors()}
    assert ids == {"wikidata", "gleif", "sec-edgar", "wayback", "live-web", "federal-register-feed"}
    for source_id in ids:
        assert view.profile(source_id) is not None, source_id


def test_historical_question_finds_archive_capable_sources(store):
    view = load_registry(store)
    historical = {d.source_id for d in view.capable_sources(historical=True)}
    assert "wayback" in historical
    assert "sec-edgar" in historical
    assert "live-web" not in historical
    assert "gleif" not in historical  # a registry of the present only


def test_operation_and_capability_queries(store):
    view = load_registry(store)
    searchers = {d.source_id for d in view.capable_sources(operation="SEARCH")}
    assert searchers == {"wikidata", "gleif", "sec-edgar"}
    filers = {d.source_id for d in view.capable_sources(capability="FILING_LOOKUP")}
    assert filers == {"sec-edgar"}


def test_monitorable_sources_respect_latency_ceiling(store):
    view = load_registry(store)
    daily = {d.source_id for d in view.monitorable_sources(max_latency="DAILY")}
    assert "federal-register-feed" in daily
    assert "wayback" not in daily  # IRREGULAR is slower than daily
    hourly = {d.source_id for d in view.monitorable_sources(max_latency="HOURLY")}
    assert "federal-register-feed" not in hourly


def test_families_group_by_source_type(store):
    families = load_registry(store).families()
    assert families["PUBLIC_ARCHIVE"] == ["wayback"]
    assert families["CORPORATE_REGISTRY"] == ["gleif"]


def test_status_events_replay_as_last_success_failure(store):
    record_source_status(store, source_id="gleif", connector_id="gleif-lei-v1", kind="SUCCESS",
                         operation="SEARCH", detail="EXECUTED_WITH_RESULTS",
                         observed_time=NOW, actor="t")
    record_source_status(store, source_id="gleif", connector_id="gleif-lei-v1", kind="FAILURE",
                         operation="LOOKUP", detail="HTTP_500", observed_time=LATER, actor="t")
    view = load_registry(store)
    assert view.last_status("gleif", "SUCCESS")["observed_time"] == NOW
    assert view.last_status("gleif", "FAILURE")["detail"] == "HTTP_500"
    assert view.last_status("wikidata", "FAILURE") is None


def test_registry_state_survives_reopen(store):
    root = store.root
    reopened = FabricStore(root)
    view = load_registry(reopened)
    assert len(view.current_descriptors()) == 6
    assert view.profile("wayback")["historical_depth"] == "DEEP_ARCHIVE"


def test_descriptor_updates_are_bitemporal_not_overwrites(store):
    view = load_registry(store)
    original = view.descriptor("gleif")
    updated = SourceDescriptor(**{
        **{f: getattr(original, f) for f in original.__dataclass_fields__},
        "transaction_time": LATER, "created_at": LATER,
        "rate_metadata": "observed throttling at 60 rpm",
    })
    profile_record = view.profile("gleif")
    profile = CapabilityProfile(**{
        **{k: v for k, v in profile_record.items() if k != "record_type"},
        "supported_operations": tuple(profile_record["supported_operations"]),
        "time_coverage": tuple(profile_record["time_coverage"]),
        "available_fields": tuple(profile_record["available_fields"]),
        "known_biases": tuple(profile_record["known_biases"]),
        "known_gaps": tuple(profile_record["known_gaps"]),
        "created_time": LATER,
    })
    register_source(store, updated, profile, recorded_time=LATER, actor="t")
    view = load_registry(store)
    assert view.descriptor("gleif").rate_metadata == "observed throttling at 60 rpm"
    assert len(view.registry.versions("gleif")) == 2  # the old version remains


def test_profile_vocabulary_is_validated():
    catalog = starter_catalog(NOW)
    with pytest.raises(ValueError):
        CapabilityProfile(**{
            **{f: getattr(catalog[0][1], f) for f in catalog[0][1].__dataclass_fields__},
            "supported_operations": ("TELEPATHY",),
        })
