"""Direct exercise of capability families whose mapped tests never called
their named implementations (C11 handoff: F01 lawful acquisition, F14
sovereignty exit test).

The F03 production-prediction cases and the V4 security-review guard were
removed with the research-campaign tree in the V6.9 excision; they live on at
tag ``archive/curunir-campaign-tree-v5x``.

Each test calls the named product function itself, with both a positive and a
negative case where the capability is a decision, so it cannot pass by
refusing (or allowing) everything.
"""
from __future__ import annotations

import pytest

from curunir_operational.access import AccessContext, Marking

pytestmark = pytest.mark.no_db

NOW = "2026-08-15T12:00:00+00:00"


# ---------------------------------------------------------------------------
# F01 — lawful acquisition: classify_access / acquisition_eligible / connectors
# ---------------------------------------------------------------------------

def _descriptor(access_class: str):
    from argus.source_intelligence.models import SourceDescriptor
    return SourceDescriptor(
        source_id=f"src-{access_class.casefold()}", canonical_name="n", publisher_name="p",
        source_type="OTHER_PUBLIC_SOURCE", source_subtype="", base_urls=("https://example.org",),
        jurisdictions=(), languages=(), coverage_domains=(), access_class=access_class,
        access_methods=("HTTP_GET",), authentication_requirement="NONE", known_identifiers=(),
        update_cadence="UNKNOWN", archive_availability="UNKNOWN", official_status="UNOFFICIAL",
        authority_candidate="NOT_ASSESSED", capabilities=(), independence_notes="",
        terms_metadata="", robots_metadata="", rate_metadata="", review_state="TEST",
        valid_time=(None, None), transaction_time=NOW, created_at=NOW)


@pytest.mark.parametrize("access_class,expected_decision,eligible", [
    ("PUBLIC_ORDINARY_WEB", "ELIGIBLE_PUBLIC_ACQUISITION", True),
    ("PUBLIC_API", "ELIGIBLE_PUBLIC_API", True),
    ("PUBLIC_BULK_EXPORT", "ELIGIBLE_PUBLIC_EXPORT", True),
    ("PUBLIC_REGISTRY", "ELIGIBLE_PUBLIC_ACQUISITION", True),
    ("RESTRICTED", "NOT_PUBLIC", False),
    ("PRIVATE", "NOT_PUBLIC", False),
    ("DARK_WEB", "OUT_OF_SCOPE_DARK_WEB", False),
    ("UNKNOWN_ACCESS_CLASS", "UNKNOWN", False),
])
def test_f01_each_access_class_gets_its_decision_not_a_downgrade(
        access_class, expected_decision, eligible):
    from argus.source_intelligence.policy import acquisition_eligible, classify_access
    decision = classify_access(_descriptor(access_class), "https://example.org/x", now=NOW)
    assert decision.decision == expected_decision
    assert acquisition_eligible(decision) is eligible


def test_f01_ineligible_is_refused_not_silently_downgraded():
    from argus.source_intelligence.policy import acquisition_eligible, classify_access
    decision = classify_access(_descriptor("RESTRICTED"), "https://example.org/x", now=NOW)
    # the refusal names its reason and is not converted to any ELIGIBLE_* value
    assert not acquisition_eligible(decision)
    assert "PRIVATE_OR_RESTRICTED_ACCESS" in decision.reason_codes
    assert not decision.decision.startswith("ELIGIBLE")


def test_f01_bypass_and_technical_barriers_are_distinct_refusals():
    from argus.source_intelligence.policy import acquisition_eligible, classify_access
    bypass = classify_access(_descriptor("PUBLIC_ORDINARY_WEB"), "https://example.org/x",
                             bypass_required=True, now=NOW)
    assert bypass.decision == "PROHIBITED_BYPASS_REQUIRED"
    assert not acquisition_eligible(bypass)
    blocked = classify_access(_descriptor("PUBLIC_ORDINARY_WEB"), "https://example.org/x",
                              technical_barriers=("CAPTCHA",), now=NOW)
    assert blocked.decision == "TEMPORARILY_BLOCKED_BY_TECHNICAL_CONTROL"
    assert not acquisition_eligible(blocked)
    assert "NO_CIRCUMVENTION" in blocked.reason_codes


def test_f01_connectors_shape_leads_without_granting_acquisition():
    from argus.source_intelligence.connectors import NEWS_CONNECTOR
    from argus.source_intelligence.models import Seed
    seed = Seed(seed_id="seed-1", seed_type="ENTITY", seed_value="Example Corp",
                seed_identifiers=(), jurisdiction_hints=(), time_window=(None, None),
                language_hints=(), source_constraints=(), created_by="t",
                created_at=NOW, review_state="TEST")
    rows = [{"url": "https://news.example.org/article", "title": "Example Corp fined",
             "snippet": "…", "source_id": "news-1"}]
    leads = NEWS_CONNECTOR.discover(seed, rows, now=NOW)
    assert leads, "a lead-only connector must still shape supplied rows"
    for lead in leads:
        assert "REQUIRES_ACQUISITION" in lead.relevance_rationale
        assert lead.acquisition_state == "NOT_ACQUIRED"
    assert NEWS_CONNECTOR.policy.absence_semantics.startswith("ABSENCE_IS_UNKNOWN")
    assert NEWS_CONNECTOR.policy.prohibited_inference  # inference limits are declared, not implied


# ---------------------------------------------------------------------------
# F14 — sovereignty.run_exit_test
# ---------------------------------------------------------------------------

def test_f14_exit_test_passes_on_faithful_export_and_fails_on_tamper(tmp_path):
    import json
    from curunir_operational.sovereignty import run_exit_test
    from operational_support import make_store, t
    from curunir_operational.contracts import ObjectVersion, ProvenanceSummary

    store = make_store(tmp_path)
    marking = Marking(owning_authority="test", releasability=("PUBLIC",))
    version = ObjectVersion(
        object_id="obj-1", version=1, object_type="INFRASTRUCTURE", lifecycle="ACTIVE",
        labels=("bridge",), external_refs=(), valid_from=t(0), valid_to=None,
        source_time=t(0), time_precision="EXACT", recorded_time=t(0), geometry=None,
        attributes={"status": "OPEN"}, quality={}, epistemic_state="OBSERVED",
        marking=marking, provenance=ProvenanceSummary(mode="OPERATIONAL"),
        correction_of=None, correction_reason=None, supersedes_version=None)
    store.append("OBJECT_VERSION_APPENDED", version, recorded_time=t(0), actor="t")
    context = AccessContext(context_id="ctx", actor_id="a", actor_kind="HUMAN",
                            roles=("SUPERVISOR",), compartments=(), releasability=("PUBLIC",),
                            organisation="org")
    result = run_exit_test(store, tmp_path / "export", tmp_path / "fresh", context,
                           snapshot_time=t(1))
    assert result["passed"] is True
    assert all(result["checks"].values())

    # a tampered export must fail the exit test, not pass by construction
    events = (tmp_path / "export" / "events.jsonl").read_text().splitlines()
    tampered = events[-1].replace('"OPEN"', '"CLOSED"')
    (tmp_path / "export" / "events.jsonl").write_text("\n".join(events[:-1] + [tampered]) + "\n")
    from curunir_operational.store import MissionDataStore, StoreError
    with pytest.raises(StoreError, match="tampered"):
        MissionDataStore.import_from(tmp_path / "export", tmp_path / "fresh2")
