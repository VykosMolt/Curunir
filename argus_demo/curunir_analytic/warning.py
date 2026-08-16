"""Strategic warning: a deterministic projection of (forecast × objective ×
impact) into a tier — never an independent classifier.

Every component is derived from typed state it names in its basis:
  * probability band — from the forecast's authored probability;
  * consequence — the threatened objective's recorded priority;
  * time pressure — distance from now to the forecast horizon;
  * evidence confidence — the forecast basis's independent origin families
    (reach is not independence; degraded claims cost a notch).

The tier comes from a NAMED rule (the contract refuses a warning without
one): a probability×consequence table, then two bounded adjustments that
are themselves part of the rule and recorded in the component basis —
imminence bumps one tier, and NONE/WEAK evidence caps at PRIORITY, because
an unsupported number must not by itself drive CRITICAL.

Projection is idempotent: re-projecting unchanged inputs appends nothing;
a component change is a new version with a transition naming what moved.
"""
from __future__ import annotations

from typing import Any, Mapping

from argus.source_intelligence.models import digest_id
from curunir_operational.canonical import parse_time

from .contracts import WARNING_TIERS, WarningRecord
from .store import AnalyticStore
from .substrate import AnalyticContext, append_version, record_transition

TIER_RULE_V1 = "curunir-warning-tier-v1"

# probability band → tier, per consequence (objective priority)
_TIER_TABLE = {
    "CRITICAL": {"REMOTE": "ATTENTION", "POSSIBLE": "PRIORITY",
                 "LIKELY": "CRITICAL", "VERY_LIKELY": "CRITICAL"},
    "HIGH": {"REMOTE": "ROUTINE", "POSSIBLE": "ATTENTION",
             "LIKELY": "PRIORITY", "VERY_LIKELY": "CRITICAL"},
    "MEDIUM": {"REMOTE": "ROUTINE", "POSSIBLE": "ATTENTION",
               "LIKELY": "ATTENTION", "VERY_LIKELY": "PRIORITY"},
    "LOW": {"REMOTE": "ROUTINE", "POSSIBLE": "ROUTINE",
            "LIKELY": "ATTENTION", "VERY_LIKELY": "ATTENTION"},
}


def probability_band(probability: float) -> str:
    if probability < 0.15:
        return "REMOTE"
    if probability < 0.45:
        return "POSSIBLE"
    if probability < 0.75:
        return "LIKELY"
    return "VERY_LIKELY"


def time_pressure(now: str, horizon_time: str) -> str:
    hours = (parse_time(horizon_time) - parse_time(now)).total_seconds() / 3600.0
    if hours <= 0:
        return "PASSED"
    if hours <= 72:
        return "IMMINENT"
    if hours <= 14 * 24:
        return "CLOSE"
    if hours <= 60 * 24:
        return "NEAR"
    return "DISTANT"


def evidence_confidence(basis: Mapping[str, Any]) -> str:
    families = len(basis.get("origin_families", ()))
    if families == 0:
        return "NONE"
    if basis.get("degraded_claim_count", 0):
        families -= 1  # a degraded basis does not count at full strength
    if families <= 0:
        return "NONE"
    if families == 1:
        return "WEAK"
    if families == 2:
        return "MODERATE"
    return "STRONG"


def derive_tier(band: str, consequence: str, pressure: str,
                confidence: str) -> tuple[str, tuple[str, ...]]:
    """The named rule, in the open: table lookup plus its two recorded
    adjustments. Returns (tier, rule-step notes)."""
    tier = _TIER_TABLE[consequence][band]
    notes = [f"table[{consequence}][{band}] -> {tier}"]
    if pressure == "IMMINENT" and tier != "CRITICAL":
        tier = WARNING_TIERS[WARNING_TIERS.index(tier) + 1]
        notes.append(f"IMMINENT horizon bumps one tier -> {tier}")
    if confidence in ("NONE", "WEAK") \
            and WARNING_TIERS.index(tier) > WARNING_TIERS.index("PRIORITY"):
        tier = "PRIORITY"
        notes.append(f"{confidence} evidence caps at PRIORITY: an unsupported "
                     f"number does not drive CRITICAL alone")
    return tier, tuple(notes)


def warning_id_for(forecast_id: str, objective_id: str) -> str:
    return digest_id("warning", forecast_id, objective_id)


def linking_impact_paths(store: AnalyticStore, forecast: Mapping[str, Any],
                         objective_id: str) -> tuple[str, ...]:
    """Impact paths of the objective whose edge evidence overlaps the
    forecast's claims or propositions — the typed reason THIS forecast
    threatens THIS objective."""
    forecast_refs = set(forecast["basis"]["supporting_claim_ids"]) \
        | set(forecast["basis"]["contradicting_claim_ids"]) \
        | {ref for _, ref in forecast["proposition_refs"]}
    linked = []
    for path in store.paths_for_objective(objective_id):
        basis_ids = {b for edge in path["edges"] for b in edge["basis_ids"]}
        endpoint_ids = {edge["from_id"] for edge in path["edges"]} \
            | {edge["to_id"] for edge in path["edges"]}
        if forecast_refs & (basis_ids | endpoint_ids):
            linked.append(path["path_id"])
    return tuple(sorted(linked))


def _components(store: AnalyticStore, forecast: Mapping[str, Any],
                objective: Mapping[str, Any], now: str) -> dict[str, Any]:
    band = probability_band(forecast["probability"])
    consequence = objective["priority"]
    pressure = time_pressure(now, forecast["horizon_time"])
    confidence = evidence_confidence(forecast["basis"])
    tier, rule_notes = derive_tier(band, consequence, pressure, confidence)
    families = tuple(forecast["basis"].get("origin_families", ()))
    component_basis = (
        ("probability_band", f"p={forecast['probability']:.2f} authored by "
                             f"{forecast['author']} ({forecast['provenance_kind']})"),
        ("consequence", f"objective {objective['objective_id'][:24]} priority "
                        f"{consequence}"),
        ("time_pressure", f"horizon {forecast['horizon_time'][:19]} vs "
                          f"now {now[:19]}"),
        ("evidence_confidence", f"{len(families)} origin famil"
                                f"{'y' if len(families) == 1 else 'ies'}, "
                                f"{forecast['basis'].get('degraded_claim_count', 0)}"
                                f" degraded claim(s)"),
    ) + tuple(("tier_rule", note) for note in rule_notes)
    return {"probability_band": band, "consequence": consequence,
            "time_pressure": pressure, "evidence_confidence": confidence,
            "tier": tier, "component_basis": component_basis}


# component_basis is deliberately NOT in the change-detection keys: its
# "why" strings embed the sampled clock, so including it would append a new
# version on every projection pass. The trade: while no COMPONENT value
# moves, the stored basis text describes the state at the last component
# change, not the latest look — the components themselves are always
# re-derived and compared live.
_COMPONENT_KEYS = ("probability_band", "consequence", "time_pressure",
                   "evidence_confidence", "tier", "impact_path_ids", "status")


def project_warning(ctx: AnalyticContext, *, forecast_id: str,
                    objective_id: str, caused_by: str = "") -> dict[str, Any]:
    """Project one forecast onto one objective. First call raises the
    warning; later calls re-derive and append ONLY on change, with the
    transition naming what moved. A terminal forecast resolves the warning."""
    store = ctx.store
    forecast = store.current_forecasts().get(forecast_id)
    if forecast is None:
        raise ValueError(f"unknown forecast: {forecast_id}")
    objective = store.current_objectives().get(objective_id)
    if objective is None:
        raise ValueError(f"unknown objective: {objective_id}")
    warning_id = warning_id_for(forecast_id, objective_id)
    existing = store.current_warnings().get(warning_id)
    from .contracts import FORECAST_TERMINAL_STATUSES
    now = ctx.now_fn()
    derived = _components(store, forecast, objective, now)
    derived["impact_path_ids"] = linking_impact_paths(store, forecast,
                                                      objective_id)
    if forecast["status"] in FORECAST_TERMINAL_STATUSES:
        derived["status"] = "RESOLVED" if existing is not None else None
        if existing is None:
            raise ValueError(
                f"forecast {forecast_id[:24]} is already settled "
                f"({forecast['status']}): a warning about a resolved question "
                "cannot be raised, only resolved")
    else:
        derived["status"] = existing["status"] if existing is not None \
            and existing["status"] != "RESOLVED" else "ACTIVE"

    if existing is None:
        record = WarningRecord(
            warning_id=warning_id, version=1,
            mission_context=objective["mission_context"],
            objective_id=objective_id, forecast_id=forecast_id,
            impact_path_ids=derived["impact_path_ids"],
            probability_band=derived["probability_band"],
            consequence=derived["consequence"],
            time_pressure=derived["time_pressure"],
            evidence_confidence=derived["evidence_confidence"],
            tier=derived["tier"], tier_rule_id=TIER_RULE_V1,
            component_basis=derived["component_basis"],
            status="ACTIVE", change_reason="",
            history=(f"RAISED:{derived['tier']}",),
            recorded_time=now, marking=ctx.marking)
        appended = append_version(ctx, record)
        record_transition(ctx, subject_kind="strategic_warning",
                          subject_id=warning_id, transition_type="RAISED",
                          detail=f"{derived['tier']} warning: "
                                 f"{forecast['question'][:100]!r} threatens "
                                 f"objective {objective['statement'][:80]!r}",
                          caused_by=caused_by or warning_id,
                          evidence_refs=(forecast_id,),
                          to_status="ACTIVE")
        return appended

    unchanged = all(tuple(existing[key]) == tuple(derived[key])
                    if isinstance(existing[key], (list, tuple))
                    else existing[key] == derived[key]
                    for key in _COMPONENT_KEYS if derived[key] is not None) \
        and not (forecast["status"] in FORECAST_TERMINAL_STATUSES
                 and existing["status"] != "RESOLVED")
    if unchanged:
        return existing

    old_rank = WARNING_TIERS.index(existing["tier"])
    new_rank = WARNING_TIERS.index(derived["tier"])
    if forecast["status"] in FORECAST_TERMINAL_STATUSES:
        status, transition = "RESOLVED", "RESOLVED"
        reason = f"forecast settled {forecast['status']}"
    elif new_rank > old_rank:
        status, transition = "ESCALATED", "ESCALATED"
        reason = f"tier {existing['tier']} -> {derived['tier']}"
    elif new_rank < old_rank:
        status, transition = "DOWNGRADED", "DOWNGRADED"
        reason = f"tier {existing['tier']} -> {derived['tier']}"
    else:
        status, transition = existing["status"], "COMPONENT_CHANGED"
        moved = [key for key in _COMPONENT_KEYS
                 if key not in ("status",) and derived[key] is not None
                 and (tuple(existing[key]) != tuple(derived[key])
                      if isinstance(existing[key], (list, tuple))
                      else existing[key] != derived[key])]
        reason = f"components moved: {', '.join(moved)}"
    merged = {k: v for k, v in existing.items() if k != "record_type"}
    merged.update({key: derived[key] for key in
                   ("probability_band", "consequence", "time_pressure",
                    "evidence_confidence", "tier", "impact_path_ids",
                    "component_basis")})
    merged["status"] = status
    merged["version"] = store.next_analytic_version("strategic_warning",
                                                    warning_id)
    merged["change_reason"] = reason
    merged["history"] = tuple(existing["history"]) + (f"{transition}:"
                                                      f"{derived['tier']}",)
    merged["recorded_time"] = now
    merged["marking"] = ctx.marking
    merged["impact_path_ids"] = tuple(merged["impact_path_ids"])
    merged["component_basis"] = tuple(tuple(pair)
                                      for pair in merged["component_basis"])
    appended = append_version(ctx, WarningRecord(**merged))
    record_transition(ctx, subject_kind="strategic_warning",
                      subject_id=warning_id, transition_type=transition,
                      detail=f"{reason}; rule {TIER_RULE_V1}",
                      caused_by=digest_id("warnmove", warning_id,
                                          str(merged["version"])),
                      evidence_refs=(forecast_id,),
                      from_status=existing["status"], to_status=status)
    return appended


def refresh_warnings(ctx: AnalyticContext, *, caused_by: str = "") -> list[dict]:
    """Re-project every current warning against current state. Pure
    re-derivation: unchanged warnings append nothing."""
    outcomes = []
    for warning in sorted(ctx.store.current_warnings().values(),
                          key=lambda w: w["warning_id"]):
        if warning["status"] in ("RESOLVED", "WITHDRAWN"):
            continue
        outcomes.append(project_warning(
            ctx, forecast_id=warning["forecast_id"],
            objective_id=warning["objective_id"],
            caused_by=caused_by or f"refresh:{warning['warning_id'][:18]}"))
    return outcomes
