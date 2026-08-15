"""Coverage and failure accounting per information need.

Derived from recorded plans and executions against the registered source
population — never from optimism. The rule that matters:

    "we did not find it"  !=  "it does not exist"
    "we never looked"     !=  either of the above

Every registered source gets an explicit state for the need; unregistered
source families remain a visible gap, not an invisible one.
"""
from __future__ import annotations

from argus.source_intelligence.models import digest_id

from .contracts import CoverageAssessment
from .registry import RegistryView
from .store import FabricStore

_RELEVANT_OUTCOMES = ("EXECUTED_WITH_RESULTS", "EXECUTED_EMPTY", "SOURCE_FAILED",
                      "ACCESS_RESTRICTED", "POLICY_REFUSED", "RATE_DEFERRED", "NOT_ATTEMPTED")


def _need_time_before_coverage(need: dict, profile: dict) -> bool:
    """True when the need's window ends before the source's coverage begins."""
    need_end = need["time_bounds"][1]
    coverage_start = profile["time_coverage"][0]
    return bool(need_end and coverage_start and need_end < coverage_start)


def assess_coverage(store: FabricStore, registry: RegistryView, need_id: str, *,
                    now: str, actor: str, marking) -> list[CoverageAssessment]:
    needs = [r for r in store.records_of("fabric_information_need") if r["need_id"] == need_id]
    if not needs:
        raise ValueError(f"unknown information need: {need_id}")
    need = needs[-1]
    plan_ids = {r["plan_id"] for r in store.records_of("fabric_discovery_plan")
                if r["need_id"] == need_id}
    executions_by_source: dict[str, list[dict]] = {}
    for record in store.records_of("fabric_execution"):
        if record["plan_id"] in plan_ids:
            executions_by_source.setdefault(record["source_id"], []).append(record)

    assessments: list[CoverageAssessment] = []
    for descriptor in registry.current_descriptors():
        source_id = descriptor.source_id
        profile = registry.profile(source_id) or {}
        executions = executions_by_source.get(source_id, [])
        gaps: list[str] = []
        basis = tuple(r["execution_id"] for r in executions)
        if not executions:
            if profile and _need_time_before_coverage(need, profile):
                state = "NOT_AVAILABLE"
                gaps.append(f"need window ends before source coverage starts "
                            f"({profile['time_coverage'][0]})")
            elif not profile:
                state = "UNKNOWN"
                gaps.append("no capability profile registered")
            else:
                state = "NOT_SEARCHED"
                gaps.append("no query executed against this source for this need")
            basis = ()
        else:
            outcomes = {r["outcome"] for r in executions}
            executed = outcomes & {"EXECUTED_WITH_RESULTS", "EXECUTED_EMPTY"}
            refused = outcomes & {"ACCESS_RESTRICTED", "POLICY_REFUSED"}
            failed = outcomes & {"SOURCE_FAILED"}
            if executed and not failed and not refused:
                state = "COVERED"
            elif executed:
                state = "PARTIALLY_COVERED"
                if failed:
                    gaps.append("some queries failed at the source")
                if refused:
                    gaps.append("some queries were access-restricted or policy-refused")
            elif refused and not failed:
                state = "ACCESS_RESTRICTED"
                gaps.append("all attempts were access-restricted or policy-refused")
            elif failed:
                state = "SOURCE_FAILED"
                gaps.append("all attempts failed at the source")
            else:
                state = "UNKNOWN"
            empty_only = executed == {"EXECUTED_EMPTY"} and outcomes <= {"EXECUTED_EMPTY"}
            if empty_only:
                gaps.append("searched and returned no results; absence of results "
                            "is not evidence of nonexistence")
        gaps.extend(profile.get("known_gaps", ()))
        assessment = CoverageAssessment(
            coverage_id=digest_id("coverage", need_id, source_id, now),
            need_id=need_id, source_id=source_id, source_family=descriptor.source_type,
            state=state, basis_execution_ids=basis,
            temporal_coverage=tuple(profile.get("time_coverage", (None, None))),
            language_coverage=tuple(descriptor.languages),
            geographic_coverage=tuple(descriptor.jurisdictions),
            gaps=tuple(gaps), assessed_time=now, assessor="fabric-coverage-rules-0.1",
            marking=marking,
        )
        store.append("FABRIC_COVERAGE_ASSESSED", assessment, recorded_time=now, actor=actor)
        assessments.append(assessment)
    return assessments


def coverage_summary(store: FabricStore, need_id: str) -> dict[str, list[str]]:
    """Latest coverage state per source, grouped by state."""
    latest: dict[str, dict] = {}
    for record in store.records_of("fabric_coverage"):
        if record["need_id"] == need_id:
            latest[record["source_id"]] = record
    grouped: dict[str, list[str]] = {}
    for source_id, record in sorted(latest.items()):
        grouped.setdefault(record["state"], []).append(source_id)
    return grouped


def unsearched_families(store: FabricStore, registry: RegistryView, need_id: str) -> list[str]:
    summary = coverage_summary(store, need_id)
    unsearched = set(summary.get("NOT_SEARCHED", ())) | set(summary.get("UNKNOWN", ()))
    families = registry.families()
    return sorted(family for family, members in families.items()
                  if all(member in unsearched for member in members))
