"""Indicators: registered observation patterns that move forecasts.

PRESENCE indicators fire on matching evidence recorded after arming —
never on evidence that existed when the indicator was armed (what made the
question worth watching is not its answer), and never on anything but real
observations with anchors.

ABSENCE indicators fire only at their deadline AND only when the declared
coverage was actually achieved: "we did not see it" is a finding about the
world only when the places it would appear were genuinely looked at.
Insufficient coverage yields COVERAGE_BLOCKED plus a collection requirement
through the existing machinery — silence never moves a number.

A fired indicator's effect is exactly what a human pre-authorized at arming
time (validated in the contract): APPLY_PROBABILITY executes the analyst's
own recorded conditional judgment; REVIEW_ONLY marks the forecast
UPDATE_REQUIRED and queues review. The machine adds nothing of its own.
"""
from __future__ import annotations

from typing import Any, Iterable, Mapping

from argus.source_intelligence.models import digest_id
from curunir_operational.canonical import parse_time
from curunir_semantic.contracts import ReviewItem

from .basis import _manifestation_index, _state_time
from .contracts import IndicatorEffect, IndicatorRecord
from .forecasts import (update_probability, _absence_coverage_satisfied,
                        _close_coverage_gap)
from .store import AnalyticStore
from .substrate import (AnalyticContext, append_version, ensure_transition,
                        marked_for_subject, record_transition, require_accepted_candidate)


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
    """Arm an indicator (idempotent by description+forecasts). The named
    forecasts must exist and be live; arming folds the indicator id into
    each forecast's indicator list."""
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
        existing_effect = {k: v for k, v in dict(existing["effect"]).items()}
        differs = [
            name for name, supplied, standing in (
                ("effect", {k: v for k, v in supplied_effect.items()
                            if k != "record_type"},
                 {k: v for k, v in existing_effect.items()
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
            # in a subsystem whose thesis is "the machine executes exactly
            # what the human pre-authorized", silently keeping a superseded
            # authorization is the worst failure mode: changing what an
            # indicator does is an explicit act on a NEW indicator
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
        proposal_id=proposal_id, change_reason="",
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
            # a settled forecast's record is history: even a crash-recovered
            # arming fold does not rewrite it
            continue
        _forecast_reappend(ctx, forecast,
                           {"indicator_ids": tuple(forecast["indicator_ids"])
                            + (indicator["indicator_id"],)},
                           # REFERENCE the indicator by id (scrubbed for uncleared
                           # viewers), never embed its compartmented DESCRIPTION —
                           # a PUBLIC forecast must not carry a SPECIAL indicator's
                           # text verbatim, and flooring the whole forecast on the
                           # indicator would over-classify it (review A4)
                           change_reason=f"indicator armed: "
                                         f"{indicator['indicator_id'][:18]}",
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
    """Matching observations that are ABOUT the post-arming world: both the
    knowledge axis (recorded after arming) and the evidence axis (the source
    state the observation captures was current after arming) must be later
    than the arming. An archival backfill about the pre-arming world is
    history arriving late — the world model correctly refuses it as a claim
    update, and an indicator firing on it would put the forecast plane in
    contradiction with the claims. Missing state time fails safe: no firing.
    Both bounds are strict, so under a clock no finer than the source's
    timestamps a same-instant retrieval stays invisible — the safe direction
    (a missed firing surfaces through the forecast plane's other refreshers,
    a false firing would move a number)."""
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
    """Observations defeating an ABSENCE indicator: the watched thing held at
    ANY point up to the deadline — including before arming. The post-arming
    lower bound is a PRESENCE law (don't fire on the world that prompted the
    watch); applied to a defeat test it fails open, letting an indicator
    assert 'we never saw it' about a state that already held when it was
    armed. Missing state time falls back to recorded time — the error
    direction (an extra defeat) refuses a firing, never invents one."""
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
    """One pass over live indicators.

    PRESENCE (ARMED) fires on post-arming matching observations — on the
    evidence axis, never on archival backfill about the pre-arming world.
    ABSENCE (ARMED past its deadline, or COVERAGE_BLOCKED once coverage
    later arrives) fires only when the declared coverage was achieved; the
    watched thing occurring by the deadline defeats it (RETIRED). FIRED
    indicators are revisited only to COMPLETE an interrupted effect —
    every completion step is idempotent, so a finished indicator appends
    nothing."""
    store = ctx.store
    outcomes = []
    observations = store.records_of("semantic_observation")
    manifestations = _manifestation_index(store)
    for indicator in sorted(store.current_indicators().values(),
                            key=lambda i: i["indicator_id"]):
        if indicator["status"] == "FIRED":
            # crash-recovery: the FIRED version may have landed while the
            # pre-authorized effect (or the gap closure) did not; complete
            # both, idempotently
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
            # every question this indicator watches is settled: it can never
            # legitimately act again — expire it instead of rescanning it
            # forever and asking collection to cover a dead question
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
            # the watched thing having held at ANY point by the deadline —
            # pre-arming state included — defeats the absence
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
            # coverage is measured from the DEADLINE: "nothing had appeared
            # by the deadline" is only knowable from searches that saw the
            # state at or after that moment — a search hours earlier says
            # nothing about the window it did not see
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
                        recorded_time=ctx.now_fn(),
                        # floor on the indicator this item is ABOUT (its detail
                        # quotes the indicator's compartmented text) — R23B-1
                        marking=marked_for_subject(ctx, "forecast_indicator",
                                                   indicator["indicator_id"]))
                    store.append("REVIEW_ITEM_RECORDED", item,
                                 recorded_time=item.recorded_time, actor=ctx.actor)
                outcomes.append(blocked)
            # already COVERAGE_BLOCKED and still unsatisfied: quiet no-op
    return outcomes


def _fire(ctx: AnalyticContext, indicator: Mapping[str, Any], *,
          evidence: tuple[str, ...], why: str) -> dict[str, Any]:
    store = ctx.store
    fired = _reappend(ctx, indicator,
                      {"status": "FIRED", "fired_time": ctx.now_fn(),
                       "fired_evidence_refs": evidence},
                      change_reason=why[:280], history_note="FIRED")
    record_transition(ctx, subject_kind="forecast_indicator",
                      subject_id=indicator["indicator_id"],
                      transition_type="FIRED",
                      detail=f"{indicator['direction']}: {why[:200]}",
                      caused_by=digest_id("fire", indicator["indicator_id"]),
                      evidence_refs=evidence[:5],
                      from_status=indicator["status"], to_status="FIRED")
    _apply_effects(ctx, fired)
    _close_coverage_gap(ctx, "indicator", indicator["indicator_id"],
                        "coverage was achieved and the indicator fired")
    return store.current_indicators()[indicator["indicator_id"]]


def _effect_executed(store: AnalyticStore, indicator: Mapping[str, Any],
                     forecast_id: str, target: float) -> bool:
    """Did the pre-authorized update actually land? Checked against the
    forecast's VERSION HISTORY at/after the firing, not its current number —
    a human legitimately moving the probability afterwards must not trick a
    recovery pass into re-stomping their judgment."""
    fired_time = indicator.get("fired_time", "")
    for version in store.analytic_versions("analytic_forecast", forecast_id):
        if abs(version["probability"] - target) > 1e-9:
            continue
        if not fired_time or parse_time(version["recorded_time"]) \
                >= parse_time(fired_time):
            return True
    return False


def _apply_effects(ctx: AnalyticContext, indicator: Mapping[str, Any]) -> int:
    """Execute (or crash-complete) a FIRED indicator's pre-authorized effect
    on every named live forecast. Every step is idempotent — the transition
    is keyed by cause, the probability update no-ops at target, the review
    item is existence-gated — so a completed firing appends nothing.
    Returns the number of events appended."""
    store = ctx.store
    before = store.head()["event_count"]
    effect = indicator["effect"]
    evidence = tuple(indicator["fired_evidence_refs"])
    why = indicator.get("change_reason", "")
    from .contracts import FORECAST_TERMINAL_STATUSES
    for forecast_id in indicator["forecast_ids"]:
        forecast = store.current_forecasts().get(forecast_id)
        if forecast is None or forecast["status"] in FORECAST_TERMINAL_STATUSES:
            continue
        # the INDICATOR_FIRED transition is the COMPLETION marker and lands
        # LAST: once it exists this firing's effect ran exactly once, and a
        # recovery pass must not re-run it (it would re-flag a forecast a
        # human has since reviewed, or re-stomp a moved number)
        marker = digest_id("fire", indicator["indicator_id"], forecast_id)
        if any(t["transition_type"] == "INDICATOR_FIRED"
               and t["caused_by"] == marker
               for t in store.transitions_for(forecast_id)):
            continue
        if effect["mode"] == "APPLY_PROBABILITY":
            if not _effect_executed(store, indicator, forecast_id,
                                    effect["target_probability"]):
                # executing the human's recorded conditional judgment, verbatim
                update_probability(
                    ctx, forecast_id,
                    probability=effect["target_probability"],
                    reason=f"pre-authorized by {effect['authorized_by']} on "
                           f"indicator firing: {effect['rationale'][:140]}",
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
                    detail=f"indicator {indicator['description'][:120]!r} fired "
                           f"({indicator['direction']}); the probability needs "
                           f"human review",
                    evidence_refs=evidence[:5],
                    status="OPEN", resolution_note="",
                    recorded_time=ctx.now_fn(),
                    # floor on the forecast (subject) AND the indicator whose
                    # compartmented description this detail quotes verbatim (R23B-1)
                    marking=marked_for_subject(ctx, "analytic_forecast", forecast_id,
                                               reference_markings=[indicator.get("marking")]))
                store.append("REVIEW_ITEM_RECORDED", item,
                             recorded_time=item.recorded_time, actor=ctx.actor)
        record_transition(ctx, subject_kind="analytic_forecast",
                          subject_id=forecast_id,
                          transition_type="INDICATOR_FIRED",
                          detail=f"indicator {indicator['description'][:100]!r} "
                                 f"fired ({indicator['direction']}): {why[:120]}",
                          caused_by=marker,
                          # cite the indicator this detail quotes verbatim, so the
                          # transition floors on the (compartmented) indicator (A1)
                          evidence_refs=(indicator["indicator_id"], *evidence[:4]))
    return store.head()["event_count"] - before
