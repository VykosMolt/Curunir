"""Harness comparing providers on identical access-filtered projections.

It scores structured-output validity, determinism, latency, abstention,
unsupported claims, evidence citation and reproducibility. Providers stay
proposals-only: the harness materializes nothing.
"""
from __future__ import annotations

import time
from typing import Any, Callable, Mapping

from .access import AccessContext, can_view
from .analytics import DeterministicRuleProvider, MockAssessmentProvider
from .canonical import sha256
from .contracts import PROPOSAL_TYPES
from .projection import Projection
from .store import MissionDataStore

HARNESS_VERSION = "curunir-operational-provider-eval-v1"


def _proposal_structurally_valid(proposal: Mapping[str, Any]) -> bool:
    return (proposal.get("proposal_type") in PROPOSAL_TYPES
            and isinstance(proposal.get("content"), dict)
            and bool(proposal.get("inference_id"))
            and proposal.get("status") == "PROPOSED")


def _visible_object_ids(projection: Projection, context: AccessContext) -> set[str]:
    return {oid for oid, entry in projection.objects.items()
            if can_view(entry["current"].get("marking"), context)}


def _referenced_object_ids(proposal: Mapping[str, Any]) -> set[str]:
    content = proposal.get("content", {})
    refs: set[str] = set()
    for key in ("affected_ids", "evidence_refs"):
        refs.update(str(v).split("@v")[0] for v in content.get(key, []))
    for key in ("left", "right", "object_id", "target", "hazard"):
        if content.get(key):
            refs.add(str(content[key]))
    return {r for r in refs if not r.startswith(("evgroup", "rule-", "basis"))}


def evaluate_provider(name: str, run: Callable[[Projection, AccessContext, str], list[dict[str, Any]]],
                      projection: Projection, empty_projection: Projection,
                      context: AccessContext, low_context: AccessContext,
                      restricted_ids: set[str], *, times: list[str]) -> dict[str, Any]:
    started = time.monotonic()
    first = run(projection, context, times[0])
    latency_ms = round((time.monotonic() - started) * 1000, 2)
    second = run(projection, context, times[1])
    strip = lambda ps: sorted(sha256({k: p[k] for k in ("proposal_type", "content")}) for p in ps)
    deterministic = strip(first) == strip(second)
    visible = _visible_object_ids(projection, context)
    unsupported = [p["proposal_id"] for p in first
                   if not _referenced_object_ids(p) <= visible]
    abstained = run(empty_projection, context, times[2])
    low_run = run(projection, low_context, times[3])
    low_leaks = [p["proposal_id"] for p in low_run
                 if _referenced_object_ids(p) & restricted_ids]
    cited = [p for p in first if p["proposal_type"] in ("ALERT_CANDIDATE", "RECOMMENDATION_CANDIDATE")]
    citations_ok = all(p["content"].get("evidence_refs") for p in cited)
    return {
        "provider": name, "harness_version": HARNESS_VERSION,
        "proposals": len(first),
        "structured_output_valid": all(_proposal_structurally_valid(p) for p in first),
        "deterministic_content": deterministic,
        "latency_ms_first_run": latency_ms,
        "abstention_on_empty_projection": len(abstained) == 0,
        "unsupported_claims": unsupported,
        "unsupported_claim_count": len(unsupported),
        "evidence_citation_on_alerts_and_recommendations": citations_ok,
        "access_compliance_low_context_leaks": low_leaks,
        "access_compliant": not low_leaks,
        "reproducibility_hash": sha256(strip(first)),
        "notes": "outputs remain proposals; no accepted-state mutation, no decision authority, "
                 "no requirement closure — enforced by the workflow layer, exercised in tests",
    }


def run_provider_comparison(make_eval_store: Callable[[str], MissionDataStore],
                            projection: Projection, empty_projection: Projection,
                            context: AccessContext, low_context: AccessContext, restricted_ids: set[str],
                            *, times: list[str]) -> dict[str, Any]:
    """Each provider is evaluated against its own fresh copy of the store, so
    evaluation never touches operational state."""
    rules_store = make_eval_store("rules")

    def rules_runner(proj, ctx, when):
        return DeterministicRuleProvider(rules_store).run(proj, ctx, recorded_time=when)

    mock_store = make_eval_store("mock")

    def mock_runner(proj, ctx, when):
        target = next((oid for oid in sorted(proj.objects)
                       if proj.objects[oid]["current"]["object_type"] == "INFRASTRUCTURE"
                       and can_view(proj.objects[oid]["current"].get("marking"), ctx)), None)
        if target is None:
            return []
        result = MockAssessmentProvider(mock_store).run(proj, ctx, target, recorded_time=when)
        return [result] if result else []

    results = [
        evaluate_provider("curunir-deterministic-rules@1.0", rules_runner, projection, empty_projection,
                          context, low_context, restricted_ids, times=times),
        evaluate_provider("mock-damage-assessment@1.0", mock_runner, projection, empty_projection,
                          context, low_context, restricted_ids, times=times),
    ]
    return {
        "harness_version": HARNESS_VERSION,
        "evaluated": results,
        "learned_model_comparison": "NOT_RUN",
        "learned_model_reason": "no safe additional local provider available without dependency or environment "
                                "disruption (Transformers/PyTorch/CUDA untouched; Ouro environment untouched; "
                                "no remote API permitted)",
        "authority_boundary": "providers cannot write accepted state, decide, close requirements, or receive "
                              "data their context cannot view",
    }
