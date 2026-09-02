"""Indicators: registered observation patterns that move forecasts.

A PRESENCE indicator fires on matching evidence recorded after arming, never on
the evidence that made the question worth watching. An ABSENCE indicator fires
only at its deadline and only when the declared coverage was achieved;
insufficient coverage yields COVERAGE_BLOCKED and a collection requirement, so
silence never moves a number.

A firing does exactly what a human pre-authorized at arming: APPLY_PROBABILITY
executes their recorded conditional judgment, REVIEW_ONLY marks the forecast
UPDATE_REQUIRED and queues review.
"""
from __future__ import annotations

from typing import Any, Mapping

from argus.source_intelligence.models import digest_id
from curunir_operational.canonical import parse_time
from curunir_semantic.contracts import ReviewItem

from .basis import _manifestation_index, _state_time
from .contracts import IndicatorEffect, IndicatorRecord
from .forecasts import (update_probability, _absence_coverage_satisfied,
                        _close_coverage_gap)
from .store import AnalyticStore
from .substrate import (AnalyticContext, append_version, ensure_transition,
                        record_transition, require_accepted_candidate)


def indicator_id_for(description: str, forecast_ids: tuple[str, ...]) -> str:
    return digest_id("indicator", description.casefold().strip(),
                     *sorted(forecast_ids))


def arm_indicator(ctx: AnalyticContext, *, description: str,
                  forecast_ids: tuple[str, ...], kind: str, direction: str,
                  desired_observation_type: str = "",
                  desired_subject_ref: str = "", desired_attribute: str = "",
                  expected_value: str = "",
                  effect: IndicatorEffect | None = None,
                  deadline: str = "",
                  coverage_min_successful_sources: int = 1,
                  coverage_required_source_ids: tuple[str, ...] = (),
                  provenance_kind: str = "ANALYST", inference_id: str = "",
                  proposal_id: str = "", caused_by: str = "") -> dict[str, Any]:
    """Arm an indicator, idempotent by description and forecasts.

    The named forecasts must exist and be live; arming folds the indicator id
    into each forecast's list.
    """
    store = ctx.store
    indicator_id = indicator_id_for(description, forecast_ids)
    existing = store.current_indicators().get(indicator_id)
    if existing is not None:
        ensure_transition(ctx, subject_kind="forecast_indicator",
                          subject_id=indicator_id, transition_type="ARMED",
                          detail=f"indicator armed: {existing['description'][:140]}",
                          caused_by=caused_by or indicator_id,
                          to_status=existing["status"])
        supplied_effect = (effect or IndicatorEffect(mode="REVIEW_ONLY")).to_record()
        differs = [
            name for name, supplied, standing in (
                ("effect", {k: v for k, v in supplied_effect.items()
                            if k != "record_type"},
                 {k: v for k, v in existing["effect"].items()
                  if k != "record_type"}),
                ("kind", kind, existing["kind"]),
                ("direction", direction, existing["direction"]),
                ("deadline", deadline, existing["deadline"]),
                ("expected_value", expected_value, existing["expected_value"]),
                ("desired_subject_ref", desired_subject_ref,
                 existing["desired_subject_ref"]),
                ("desired_attribute", desired_attribute,
                 existing["desired_attribute"]),
                ("desired_observation_type", desired_observation_type,
                 existing["desired_observation_type"]),
                ("coverage_min_successful_sources",
                 coverage_min_successful_sources,
                 existing["coverage_min_successful_sources"]),
                ("coverage_required_source_ids",
                 tuple(coverage_required_source_ids),
                 tuple(existing["coverage_required_source_ids"])),
            ) if supplied != standing]
        if differs:
            # keeping a superseded authorization silently would break the
            # pre-authorization guarantee: changing one needs a new indicator
            raise ValueError(
                f"indicator {indicator_id[:24]} already stands with a "
                f"different {', '.join(differs)}: changing a pre-authorization "
                "is never a silent side effect of re-arming — retire it and "
                "arm a new indicator")
        _fold_into_forecasts(ctx, existing)
        return existing
    if provenance_kind == "MODEL":
        require_accepted_candidate(
            store, inference_id=inference_id, proposal_id=proposal_id,
            target_kind="forecast_indicator",
            materialized={"description": description, "kind": kind,
                          "forecast_ids": forecast_ids})
    from .contracts import FORECAST_TERMINAL_STATUSES
    forecasts = store.current_forecasts()
    for forecast_id in forecast_ids:
        forecast = forecasts.get(forecast_id)
        if forecast is None:
            raise ValueError(f"unknown forecast: {forecast_id}")
        if forecast["status"] in FORECAST_TERMINAL_STATUSES:
            raise ValueError(f"forecast {forecast_id[:24]} is settled: "
                             "indicators watch live questions")
    record = IndicatorRecord(
        indicator_id=indicator_id, version=1,
        forecast_ids=forecast_ids, description=description,
        kind=kind, direction=direction,
        desired_observation_type=desired_observation_type,
        desired_subject_ref=desired_subject_ref,
        desired_attribute=desired_attribute,
        expected_value=expected_value,
        effect=effect or IndicatorEffect(mode="REVIEW_ONLY"),
        deadline=deadline,
        coverage_min_successful_sources=coverage_min_successful_sources,
        coverage_required_source_ids=coverage_required_source_ids,
        status="ARMED", armed_time=ctx.now_fn(),
        fired_time="", fired_evidence_refs=(),
        provenance_kind=provenance_kind, inference_id=inference_id,
        proposal_id=proposal_id if provenance_kind == "MODEL" else "",
        change_reason="",
        history=(f"ARMED:{provenance_kind}",),
        recorded_time=ctx.now_fn(), marking=ctx.marking)
    appended = append_version(ctx, record)
    ensure_transition(ctx, subject_kind="forecast_indicator",
                      subject_id=indicator_id, transition_type="ARMED",
                      detail=f"{kind} indicator armed ({direction}, effect "
                             f"{record.effect.mode}): {description[:120]}",
                      caused_by=caused_by or indicator_id, to_status="ARMED")
    _fold_into_forecasts(ctx, appended)
    return appended


def _fold_into_forecasts(ctx: AnalyticContext, indicator: Mapping[str, Any]) -> None:
    from .contracts import FORECAST_TERMINAL_STATUSES
    from .forecasts import _reappend as _forecast_reappend
    store = ctx.store
    for forecast_id in indicator["forecast_ids"]:
        forecast = store.current_forecasts().get(forecast_id)
        if forecast is None or indicator["indicator_id"] in forecast["indicator_ids"]:
            continue
        if forecast["status"] in FORECAST_TERMINAL_STATUSES:
            # a settled forecast's record is history and is not rewritten
            continue
        _forecast_reappend(ctx, forecast,
                           {"indicator_ids": tuple(forecast["indicator_ids"])
                            + (indicator["indicator_id"],)},
                           change_reason=f"indicator armed: "
                                         f"{indicator['indicator_id']}",
                           history_note=f"INDICATOR:{indicator['indicator_id'][:18]}")


def _reappend(ctx: AnalyticContext, indicator: Mapping[str, Any],
              updates: dict[str, Any], change_reason: str,
              history_note: str) -> dict[str, Any]:
    merged = {k: v for k, v in indicator.items() if k != "record_type"}
    merged.update(updates)
    merged["version"] = ctx.store.next_analytic_version(
        "forecast_indicator", indicator["indicator_id"])
    merged["change_reason"] = change_reason
    merged["history"] = tuple(indicator["history"]) + (history_note,)
    merged["recorded_time"] = ctx.now_fn()
    merged["marking"] = ctx.marking
    for key in ("forecast_ids", "coverage_required_source_ids",
                "fired_evidence_refs", "history"):
        merged[key] = tuple(merged[key])
    if isinstance(merged["effect"], Mapping):
        merged["effect"] = IndicatorEffect(
            **{k: v for k, v in merged["effect"].items() if k != "record_type"})
    record = IndicatorRecord(**merged)
    return append_version(ctx, record)


def _matches(indicator: Mapping[str, Any], observation: Mapping[str, Any]) -> bool:
    if indicator["desired_observation_type"] \
            and observation["observation_type"] != indicator["desired_observation_type"]:
        return False
    if indicator["desired_subject_ref"] \
            and observation["subject_ref"] != indicator["desired_subject_ref"]:
        return False
    if indicator["desired_attribute"] \
            and observation["attribute"] != indicator["desired_attribute"]:
        return False
    if indicator["expected_value"] \
            and observation["value"] != indicator["expected_value"]:
        return False
    return True


def _post_arming_matches(indicator: Mapping[str, Any],
                         observations: list[Mapping[str, Any]],
                         manifestations: Mapping[str, Mapping[str, Any]]
                         ) -> list[Mapping[str, Any]]:
    """Matching observations about the world after arming.

    Both the recording time and the source state time must be later than the
    arming: an archive backfill is history arriving late, and firing on it
    would contradict the claims. Missing state time refuses the firing, and
    both bounds are strict, so a same-instant retrieval stays invisible — the
    safe direction, since a false firing would move a number.
    """
    armed = parse_time(indicator["armed_time"])
    matching = []
    for observation in observations:
        if parse_time(observation["recorded_time"]) <= armed:
            continue
        if not _matches(indicator, observation):
            continue
        manifestation = manifestations.get(observation["manifestation_id"])
        state_time = _state_time(manifestation) if manifestation else ""
        if not state_time or parse_time(state_time) <= armed:
            continue
        matching.append(observation)
    return matching


def _defeat_matches(indicator: Mapping[str, Any],
                    observations: list[Mapping[str, Any]],
                    manifestations: Mapping[str, Mapping[str, Any]]
                    ) -> list[Mapping[str, Any]]:
    """Observations defeating an ABSENCE indicator.

    The watched thing holding at any point up to the deadline defeats it,
    including before arming: the post-arming bound belongs to PRESENCE, and
    applying it here would let the indicator assert "we never saw it" about a
    state that already held. Missing state time falls back to recorded time,
    which can only add a defeat and so never invents a firing.
    """
    deadline = parse_time(indicator["deadline"])
    defeating = []
    for observation in observations:
        if not _matches(indicator, observation):
            continue
        manifestation = manifestations.get(observation["manifestation_id"])
        state_time = (_state_time(manifestation) if manifestation else "") \
            or observation["recorded_time"]
        if parse_time(state_time) <= deadline:
            defeating.append(observation)
    return defeating


def check_indicators(ctx: AnalyticContext) -> list[dict[str, Any]]:
    """One pass over the live indicators.

    PRESENCE fires on post-arming matches. ABSENCE fires past its deadline only
    when the declared coverage was achieved, and the watched thing occurring by
    then retires it instead. A FIRED indicator is revisited only to complete an
    interrupted effect, and every step is idempotent.
    """
    store = ctx.store
    outcomes = []
    observations = store.records_of("semantic_observation")
    manifestations = _manifestation_index(store)
    for indicator in sorted(store.current_indicators().values(),
                            key=lambda i: i["indicator_id"]):
        if indicator["status"] == "FIRED":
            # the FIRED version may have landed while its effect did not;
            # complete both
            appended = _apply_effects(ctx, indicator)
            _close_coverage_gap(ctx, "indicator", indicator["indicator_id"],
                                "coverage was achieved and the indicator fired")
            if appended:
                outcomes.append(store.current_indicators()
                                [indicator["indicator_id"]])
            continue
        if indicator["status"] not in ("ARMED", "COVERAGE_BLOCKED"):
            continue
        from .contracts import FORECAST_TERMINAL_STATUSES
        forecasts = store.current_forecasts()
        if all(forecasts.get(fid) is None
               or forecasts[fid]["status"] in FORECAST_TERMINAL_STATUSES
               for fid in indicator["forecast_ids"]):
            # every question it watches is settled, so it can never act again:
            # expire it rather than keep asking collection for a dead question
            expired = _reappend(ctx, indicator,
                                {"status": "EXPIRED_UNFIRED"},
                                change_reason="all watched forecasts settled "
                                              "before the indicator acted",
                                history_note="EXPIRED_UNFIRED")
            record_transition(ctx, subject_kind="forecast_indicator",
                              subject_id=indicator["indicator_id"],
                              transition_type="EXPIRED_UNFIRED",
                              detail="all watched forecasts are settled; the "
                                     "indicator expires unfired",
                              caused_by=digest_id("expire",
                                                  indicator["indicator_id"]),
                              from_status=indicator["status"],
                              to_status="EXPIRED_UNFIRED")
            _close_coverage_gap(ctx, "indicator", indicator["indicator_id"],
                                "the watched questions settled; the coverage "
                                "question is moot")
            outcomes.append(expired)
            continue
        if indicator["kind"] == "PRESENCE":
            if indicator["status"] != "ARMED":
                continue
            matching = _post_arming_matches(indicator, observations,
                                            manifestations)
            if not matching:
                continue
            outcomes.append(_fire(ctx, indicator,
                                  evidence=tuple(o["observation_id"]
                                                 for o in matching[:5]),
                                  why=f"matching observation(s): "
                                      f"{matching[0]['value'][:100]!r}"))
        else:  # ABSENCE
            if indicator["status"] == "ARMED" \
                    and parse_time(ctx.now_fn()) \
                    <= parse_time(indicator["deadline"]):
                continue
            # the watched thing holding at any point by the deadline, arming
            # included, defeats the absence
            observed = _defeat_matches(indicator, observations, manifestations)
            if observed:
                retired = _reappend(ctx, indicator,
                                    {"status": "RETIRED"},
                                    change_reason="the watched observation "
                                                  "occurred; absence defeated",
                                    history_note="RETIRED:observed")
                record_transition(ctx, subject_kind="forecast_indicator",
                                  subject_id=indicator["indicator_id"],
                                  transition_type="RETIRED",
                                  detail="the watched observation occurred before "
                                         "the deadline; the absence hypothesis is "
                                         "defeated, not fired",
                                  caused_by=digest_id("retire",
                                                      indicator["indicator_id"]),
                                  evidence_refs=(observed[0]["observation_id"],),
                                  from_status=indicator["status"],
                                  to_status="RETIRED")
                _close_coverage_gap(ctx, "indicator", indicator["indicator_id"],
                                    "the watched observation occurred: the "
                                    "absence question is settled by defeat")
                outcomes.append(retired)
                continue
            # coverage is measured from the deadline: a search that ran earlier
            # says nothing about the window it did not see
            satisfied, coverage_evidence, explanation = _absence_coverage_satisfied(
                store,
                {"absence_min_successful_sources":
                 indicator["coverage_min_successful_sources"],
                 "absence_required_source_ids":
                 tuple(indicator["coverage_required_source_ids"])},
                indicator["deadline"],
                subject_refs=(indicator["desired_subject_ref"],))
            if satisfied:
                outcomes.append(_fire(ctx, indicator, evidence=coverage_evidence,
                                      why=f"deadline passed with nothing observed "
                                          f"and coverage achieved: {explanation}"))
            elif indicator["status"] == "ARMED":
                blocked = _reappend(ctx, indicator,
                                    {"status": "COVERAGE_BLOCKED"},
                                    change_reason=f"deadline passed but coverage "
                                                  f"insufficient: {explanation}",
                                    history_note="COVERAGE_BLOCKED")
                record_transition(ctx, subject_kind="forecast_indicator",
                                  subject_id=indicator["indicator_id"],
                                  transition_type="COVERAGE_BLOCKED",
                                  detail=f"silence moves nothing: {explanation}",
                                  caused_by=digest_id("covblock",
                                                      indicator["indicator_id"]),
                                  from_status="ARMED",
                                  to_status="COVERAGE_BLOCKED")
                item_id = digest_id("review-coverage", "indicator",
                                    indicator["indicator_id"])
                if not any(r["item_id"] == item_id
                           for r in store.records_of("review_item")):
                    item = ReviewItem(
                        item_id=item_id, kind="COVERAGE_GAP",
                        subject_kind="forecast_indicator",
                        subject_id=indicator["indicator_id"],
                        detail=f"absence indicator at deadline without its "
                               f"declared coverage: {explanation}. Collection "
                               f"is needed before silence means anything.",
                        evidence_refs=(indicator["indicator_id"],),
                        status="OPEN", resolution_note="",
                        recorded_time=ctx.now_fn(), marking=ctx.marking)
                    store.append("REVIEW_ITEM_RECORDED", item,
                                 recorded_time=item.recorded_time, actor=ctx.actor)
                outcomes.append(blocked)
            # already blocked and still unsatisfied: nothing to append
    return outcomes


def _fire(ctx: AnalyticContext, indicator: Mapping[str, Any], *,
          evidence: tuple[str, ...], why: str) -> dict[str, Any]:
    store = ctx.store
    # the evidence may be more restricted than the indicator, so persist its
    # references and never a quoted observation value
    fired = _reappend(ctx, indicator,
                      {"status": "FIRED", "fired_time": ctx.now_fn(),
                       "fired_evidence_refs": evidence},
                      change_reason="indicator fired from recorded evidence",
                      history_note="FIRED")
    record_transition(ctx, subject_kind="forecast_indicator",
                      subject_id=indicator["indicator_id"],
                      transition_type="FIRED",
                      detail=f"{indicator['direction']}: indicator fired from "
                             "recorded evidence",
                      caused_by=digest_id("fire", indicator["indicator_id"]),
                      evidence_refs=evidence[:5],
                      from_status=indicator["status"], to_status="FIRED")
    _apply_effects(ctx, fired)
    _close_coverage_gap(ctx, "indicator", indicator["indicator_id"],
                        "coverage was achieved and the indicator fired")
    return store.current_indicators()[indicator["indicator_id"]]


def _effect_executed(store: AnalyticStore, indicator: Mapping[str, Any],
                     forecast_id: str, target: float) -> bool:
    """Did the pre-authorized update land?

    Checked against the version history at or after the firing, not the current
    number, so a human moving the probability afterwards is not overwritten.
    """
    fired_time = indicator.get("fired_time", "")
    for version in store.analytic_versions("analytic_forecast", forecast_id):
        if abs(version["probability"] - target) > 1e-9:
            continue
        if not fired_time or parse_time(version["recorded_time"]) \
                >= parse_time(fired_time):
            return True
    return False


def _apply_effects(ctx: AnalyticContext, indicator: Mapping[str, Any]) -> int:
    """Execute, or complete, a FIRED indicator's effect on every live forecast
    it names. Returns how many events were appended; a completed firing appends
    nothing."""
    store = ctx.store
    before = store.head()["event_count"]
    effect = indicator["effect"]
    evidence = tuple(indicator["fired_evidence_refs"])
    material_refs = tuple(dict.fromkeys(
        (indicator["indicator_id"], *evidence)))
    from .contracts import FORECAST_TERMINAL_STATUSES
    for forecast_id in indicator["forecast_ids"]:
        forecast = store.current_forecasts().get(forecast_id)
        if forecast is None or forecast["status"] in FORECAST_TERMINAL_STATUSES:
            continue
        # the INDICATOR_FIRED transition lands last and marks completion, so
        # a later pass must not re-run an effect that already ran
        marker = digest_id("fire", indicator["indicator_id"], forecast_id)
        if any(t["transition_type"] == "INDICATOR_FIRED"
               and t["caused_by"] == marker
               for t in store.transitions_for(forecast_id)):
            continue
        if effect["mode"] == "APPLY_PROBABILITY":
            if not _effect_executed(store, indicator, forecast_id,
                                    effect["target_probability"]):
                # execute the human's recorded conditional judgment as written
                update_probability(
                    ctx, forecast_id,
                    probability=effect["target_probability"],
                    reason=f"pre-authorized indicator "
                           f"{indicator['indicator_id']} fired",
                    evidence_refs=evidence,
                    actor_id=effect["authorized_by"], actor_kind="SERVICE",
                    indicator_id=indicator["indicator_id"])
        else:
            from .forecasts import _reappend as _forecast_reappend
            if forecast["status"] == "OPEN":
                _forecast_reappend(ctx, forecast,
                                   {"status": "UPDATE_REQUIRED"},
                                   change_reason=f"indicator fired "
                                                 f"({indicator['direction']}): "
                                                 f"review required",
                                   history_note="UPDATE_REQUIRED:indicator")
            item_id = digest_id("review-indicator", indicator["indicator_id"],
                                forecast_id)
            if not any(r["item_id"] == item_id
                       for r in store.records_of("review_item")):
                item = ReviewItem(
                    item_id=item_id, kind="STALE_BASIS",
                    subject_kind="analytic_forecast", subject_id=forecast_id,
                    detail=f"indicator {indicator['indicator_id']} fired; the "
                           "probability needs human review",
                    evidence_refs=material_refs[:5],
                    status="OPEN", resolution_note="",
                    recorded_time=ctx.now_fn(), marking=ctx.marking)
                store.append("REVIEW_ITEM_RECORDED", item,
                             recorded_time=item.recorded_time, actor=ctx.actor)
        record_transition(ctx, subject_kind="analytic_forecast",
                          subject_id=forecast_id,
                          transition_type="INDICATOR_FIRED",
                          detail=f"indicator {indicator['indicator_id']} fired "
                                 f"({indicator['direction']})",
                          caused_by=marker,
                          evidence_refs=material_refs[:5])
    return store.head()["event_count"] - before
