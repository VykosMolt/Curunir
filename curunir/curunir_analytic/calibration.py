"""Calibration scoring: pure functions over the replayed log, writing nothing.

A score uses the probability the record shows actually stood before resolution,
never one supplied at scoring time; a resolution carrying a different number
raises rather than flattering the author. Coverage is part of the answer, so
every scoreboard reports what it could not score alongside what it did.
"""
from __future__ import annotations

import math
from typing import Any, Iterable, Mapping

from curunir_operational.canonical import parse_time

from .store import AnalyticStore

SCORED_STATUSES = ("RESOLVED_TRUE", "RESOLVED_FALSE")


def standing_probability(versions: list[Mapping[str, Any]],
                         horizon_time: str = "") -> float:
    """The probability that stood on the question.

    The last version at or before the horizon when one is given — a number
    moved afterwards, with the outcome possibly visible, is not a forecast of
    it — else the last version before the resolving one. Ordered by version,
    since a coarse clock can tie timestamps. Raises when the resolution carries
    a probability the record never held.
    """
    terminal = [v for v in versions if v.get("status") in SCORED_STATUSES]
    if not terminal:
        raise ValueError(
            f"forecast {versions[0]['forecast_id'][:24]} carries no resolved "
            f"version: there is nothing to score")
    resolving = min(v.get("version", 1) for v in terminal)
    prior = [v for v in versions if v.get("version", 1) < resolving]
    if not prior:
        raise ValueError(
            f"forecast {versions[0]['forecast_id'][:24]} has no version "
            f"before its resolution: nothing was ever at stake")
    last_prior = max(prior, key=lambda v: v.get("version", 1))
    for v in terminal:
        if abs(v["probability"] - last_prior["probability"]) > 1e-9:
            raise ValueError(
                f"hindsight leakage on forecast "
                f"{versions[0]['forecast_id'][:24]}: the resolution carries "
                f"p={v['probability']:.2f} but the standing pre-resolution "
                f"probability was {last_prior['probability']:.2f}")
    if horizon_time:
        in_time = [v for v in prior
                   if v.get("recorded_time")
                   and parse_time(v["recorded_time"]) <= parse_time(horizon_time)]
        if in_time:
            return max(in_time, key=lambda v: v.get("version", 1))["probability"]
        # authored entirely after its horizon: score the opening number, the
        # only one not moved with the outcome in sight
        return min(prior, key=lambda v: v.get("version", 1))["probability"]
    return last_prior["probability"]


def post_horizon_update_count(versions: list[Mapping[str, Any]],
                              horizon_time: str) -> int:
    """How many probability moves landed after the horizon and before
    resolution, so a number that chased a visible outcome shows on its row."""
    terminal_versions = [v.get("version", 1) for v in versions
                         if v.get("status") in SCORED_STATUSES]
    resolving = min(terminal_versions) if terminal_versions else None
    count = 0
    for earlier, later in zip(versions, versions[1:]):
        if resolving is not None and later.get("version", 1) >= resolving:
            continue
        if abs(later["probability"] - earlier["probability"]) <= 1e-9:
            continue
        if later.get("recorded_time") \
                and parse_time(later["recorded_time"]) > parse_time(horizon_time):
            count += 1
    return count


def brier(probability: float, outcome_true: bool) -> float:
    return (probability - (1.0 if outcome_true else 0.0)) ** 2


def log_score(probability: float, outcome_true: bool) -> float:
    """Negative log likelihood, lower is better. Always finite: p is inside (0,1)."""
    p = probability if outcome_true else 1.0 - probability
    return -math.log(p)


def horizon_band(created_time: str, horizon_time: str) -> str:
    span = parse_time(horizon_time) - parse_time(created_time)
    days = span.total_seconds() / 86400.0
    if days <= 7:
        return "SHORT"
    if days <= 90:
        return "MEDIUM"
    return "LONG"


def _opening(versions: list[Mapping[str, Any]]) -> Mapping[str, Any]:
    """The first version, by the version field rather than by list position."""
    return min(versions, key=lambda v: v.get("version", 1))


def _claim_answer_entry_time(store: AnalyticStore, claim_id: str,
                             at_time: str = "") -> str:
    """When this answer entered the record.

    The earliest recorded time of the trailing run of versions sharing the
    value standing at `at_time` — the resolution instant, never the value
    standing now, so a replayed scoreboard does not change when the claim moves
    on afterwards.
    """
    versions = sorted((r for r in store.records_of("semantic_claim")
                       if r["claim_id"] == claim_id),
                      key=lambda v: v.get("version", 1))
    if not versions:
        return ""
    if at_time:
        standing = [v for v in versions
                    if v.get("recorded_time")
                    and parse_time(v["recorded_time"]) <= parse_time(at_time)]
        versions = standing or versions[:1]
    anchor_value = versions[-1].get("object_or_value")
    entry = versions[-1].get("recorded_time", "")
    for version in reversed(versions):
        if version.get("object_or_value") != anchor_value:
            break
        entry = version.get("recorded_time", entry)
    return entry


def _evidence_recorded_time(store: AnalyticStore, ref: str,
                            at_time: str = "") -> str:
    """When the referenced resolution evidence entered the record.

    An unknown ref returns "": only provably-prior evidence excludes a row.
    """
    if store.current_claims().get(ref) is not None:
        return _claim_answer_entry_time(store, ref, at_time)
    for observation in store.records_of("semantic_observation"):
        if observation["observation_id"] == ref:
            return observation.get("recorded_time", "")
    for execution in store.records_of("fabric_execution"):
        if execution["execution_id"] == ref:
            return execution.get("completed_time") \
                or execution.get("started_time", "")
    for activity in store.records_of("activity"):
        if activity["activity_id"] == ref:
            return activity.get("recorded_time", "")
    for manifestation in store.records_of("fabric_manifestation"):
        if manifestation["manifestation_id"] == ref:
            return manifestation.get("retrieval_time", "")
    for document in store.records_of("semantic_document"):
        if document.get("document_id") == ref:
            return document.get("recorded_time", "")
    for relationship in store.records_of("relationship_version"):
        if relationship.get("relationship_id") == ref:
            return relationship.get("recorded_time", "")
    return ""


def resolved_by_prior_evidence(store: AnalyticStore,
                               current: Mapping[str, Any],
                               versions: list[Mapping[str, Any]]) -> bool:
    """Was the answer already on the record when the number was authored?

    True when every resolution-evidence record whose time is known predates the
    first version. Scope is RESOLVED_TRUE rows and RESOLVED_FALSE rows settled
    before the horizon: a FALSE settled at or after it was decided by the window
    closing, and flagging those would drop honest negative forecasts.
    """
    status = current.get("status")
    if status == "RESOLVED_FALSE":
        resolved = current.get("resolved_time", "")
        horizon = current.get("horizon_time", "")
        if not (resolved and horizon
                and parse_time(resolved) < parse_time(horizon)):
            return False
    elif status != "RESOLVED_TRUE":
        return False
    refs = tuple(current.get("resolution_evidence_refs", ()))
    if not refs:
        return False
    authored = _opening(versions).get("recorded_time", "")
    if not authored:
        return False
    known = [t for t in (_evidence_recorded_time(
        store, ref, at_time=current.get("resolved_time", ""))
        for ref in refs) if t]
    return bool(known) and all(parse_time(t) < parse_time(authored)
                               for t in known)


def scored_forecasts(store: AnalyticStore) -> list[dict[str, Any]]:
    """One row per resolved forecast: standing probability, outcome, both
    scores, and the grouping keys."""
    rows = []
    for forecast_id, current in sorted(store.current_forecasts().items()):
        if current["status"] not in SCORED_STATUSES:
            continue
        versions = store.analytic_versions("analytic_forecast", forecast_id)
        probability = standing_probability(versions, current["horizon_time"])
        outcome_true = current["status"] == "RESOLVED_TRUE"
        rows.append({
            "forecast_id": forecast_id,
            "question": current["question"],
            # a first version postdating the horizon was written with the
            # answer available: the row shows but never feeds an aggregate
            "authored_after_horizon": bool(
                _opening(versions).get("recorded_time"))
            and parse_time(_opening(versions)["recorded_time"])
            > parse_time(current["horizon_time"]),
            "resolved_by_prior_evidence": resolved_by_prior_evidence(
                store, current, versions),
            "probability": probability,
            "outcome": "TRUE" if outcome_true else "FALSE",
            "brier": brier(probability, outcome_true),
            "log_score": log_score(probability, outcome_true),
            "author": current["author"],
            "domain": current["domain"],
            "horizon_band": horizon_band(_opening(versions)["recorded_time"],
                                         current["horizon_time"]),
            "resolver_kind": current["resolver_kind"],
            "update_count": sum(
                1 for earlier, later in zip(versions, versions[1:])
                if abs(later["probability"] - earlier["probability"]) > 1e-9),
            "post_horizon_updates": post_horizon_update_count(
                versions, current["horizon_time"]),
            "resolved_time": current["resolved_time"],
        })
    return rows


def calibration_buckets(rows: Iterable[Mapping[str, Any]],
                        bucket_count: int = 10) -> list[dict[str, Any]]:
    """Reliability table: rows bucketed by stated probability with the observed
    frequency in each. Empty buckets are reported, not dropped."""
    buckets = [{"low": i / bucket_count, "high": (i + 1) / bucket_count,
                "count": 0, "probability_sum": 0.0, "true_count": 0}
               for i in range(bucket_count)]
    for row in rows:
        index = min(int(row["probability"] * bucket_count), bucket_count - 1)
        bucket = buckets[index]
        bucket["count"] += 1
        bucket["probability_sum"] += row["probability"]
        bucket["true_count"] += 1 if row["outcome"] == "TRUE" else 0
    out = []
    for bucket in buckets:
        n = bucket["count"]
        out.append({
            "range": f"[{bucket['low']:.1f},{bucket['high']:.1f})",
            "count": n,
            "mean_probability": bucket["probability_sum"] / n if n else None,
            "observed_rate": bucket["true_count"] / n if n else None,
        })
    return out


def expected_calibration_error(rows: list[Mapping[str, Any]],
                               bucket_count: int = 10) -> float | None:
    if not rows:
        return None
    total = len(rows)
    error = 0.0
    for bucket in calibration_buckets(rows, bucket_count):
        if not bucket["count"]:
            continue
        error += (bucket["count"] / total) \
            * abs(bucket["mean_probability"] - bucket["observed_rate"])
    return error


def _summarize(rows: list[Mapping[str, Any]]) -> dict[str, Any]:
    n = len(rows)
    return {
        "count": n,
        "brier_mean": sum(r["brier"] for r in rows) / n if n else None,
        "log_score_mean": sum(r["log_score"] for r in rows) / n if n else None,
        "base_rate": sum(1 for r in rows if r["outcome"] == "TRUE") / n
        if n else None,
    }


def _grouped(rows: list[Mapping[str, Any]], key: str) -> dict[str, dict]:
    groups: dict[str, list] = {}
    for row in rows:
        groups.setdefault(row[key], []).append(row)
    return {value: _summarize(members)
            for value, members in sorted(groups.items())}


def scoreboard(store: AnalyticStore) -> dict[str, Any]:
    """The whole calibration picture: scores, buckets, ECE, breakdowns, and
    everything that could not be scored."""
    rows = scored_forecasts(store)
    # a forecast written with the answer in hand is a statement about a visible
    # outcome: the row stays, but it feeds no mean, bucket, ECE or reputation
    honest = [r for r in rows if not r["authored_after_horizon"]
              and not r["resolved_by_prior_evidence"]]
    forecasts = store.current_forecasts()
    by_status: dict[str, int] = {}
    for forecast in forecasts.values():
        by_status[forecast["status"]] = by_status.get(forecast["status"], 0) + 1
    return {
        "overall": _summarize(honest),
        "buckets": calibration_buckets(honest),
        "expected_calibration_error": expected_calibration_error(honest),
        "by_author": _grouped(honest, "author"),
        "by_domain": _grouped(honest, "domain"),
        "by_horizon_band": _grouped(honest, "horizon_band"),
        "coverage": {
            "total_forecasts": len(forecasts),
            "scored": len(honest),
            "authored_after_horizon": sum(
                1 for r in rows if r["authored_after_horizon"]),
            "resolved_by_prior_evidence": sum(
                1 for r in rows if r["resolved_by_prior_evidence"]
                and not r["authored_after_horizon"]),
            "unscored_by_status": {status: count
                                   for status, count in sorted(by_status.items())
                                   if status not in SCORED_STATUSES},
        },
        "rows": rows,
    }
