"""Forecast lifecycle: authored probabilities over exact propositions, with
append-only update history, typed resolution, and honest absence handling.

What this engine refuses to do:
  * invent a probability — every number is authored (analyst, or a
    human-accepted model candidate) with its stated basis;
  * overwrite an update — probability movement is a new version carrying
    its reason and the evidence that moved it;
  * resolve FALSE from silence — "it did not happen" requires either claim
    evidence newer than the horizon or the resolution rule's declared
    coverage actually achieved; otherwise the forecast waits, visibly.

Machine resolution exists only for typed rules (CLAIM_PREDICATE,
EVENT_OCCURRED) and records itself as a SERVICE act over the evidence that
settled the question; everything else is a recorded human judgment.
"""
from __future__ import annotations

import re
from typing import Any, Iterable, Mapping

from argus.source_intelligence.models import digest_id
from curunir_operational.access import marking_from_record
from curunir_operational.canonical import parse_time
from curunir_semantic.contracts import ReviewItem
from curunir_semantic.worldmodel import world_object_id

from .basis import (basis_changed_materially, basis_from_record, compute_basis,
                    _manifestation_index, _observation_index, _state_time)
from .contracts import ForecastRecord, ResolutionRule
from .store import AnalyticStore
from .substrate import (AnalyticContext, append_version, ensure_transition,
                        record_transition, require_accepted_candidate)


def forecast_id_for(question: str, horizon_time: str) -> str:
    return digest_id("forecast", question.casefold().strip(), horizon_time)


def _authority_for(provenance_kind: str) -> str:
    return "ANALYST_ASSESSMENT" if provenance_kind == "ANALYST" \
        else "SUPPORTED_INFERENCE"


def create_forecast(ctx: AnalyticContext, *, question: str, outcome_semantics: str,
                    proposition_refs: tuple[tuple[str, str], ...],
                    horizon_time: str, resolution: ResolutionRule,
                    probability: float, probability_basis: str,
                    author: str, domain: str,
                    supporting_claim_ids: Iterable[str] = (),
                    contradicting_claim_ids: Iterable[str] = (),
                    assumption_ids: tuple[str, ...] = (),
                    indicator_ids: tuple[str, ...] = (),
                    provenance_kind: str = "ANALYST", inference_id: str = "",
                    proposal_id: str = "", caused_by: str = "") -> dict[str, Any]:
    """Create a forecast (idempotent by question+horizon). A re-call folds
    new evidence; a re-call with a DIFFERENT probability is refused — moving
    a probability is an explicit recorded update, never a silent side effect
    of re-creation."""
    store = ctx.store
    forecast_id = forecast_id_for(question, horizon_time)
    existing = store.current_forecasts().get(forecast_id)
    if existing is not None:
        ensure_transition(ctx, subject_kind="analytic_forecast",
                          subject_id=forecast_id, transition_type="CREATED",
                          detail=f"forecast created: {existing['question'][:120]!r} "
                                 f"p={existing['probability']:.2f} by "
                                 f"{existing['author']}",
                          caused_by=caused_by or forecast_id,
                          to_status=existing["status"])
        from .contracts import FORECAST_TERMINAL_STATUSES
        if existing["status"] in FORECAST_TERMINAL_STATUSES:
            # a settled forecast is history: re-creation completes the missing
            # transition above but folds NOTHING — the evidentiary basis of a
            # resolved judgment is never rewritten after the fact
            return existing
        if provenance_kind == "MODEL" and (not proposal_id or proposal_id
                                           != existing.get("proposal_id")):
            raise ValueError(
                "an existing forecast cannot be modified under model provenance "
                "with a different proposal: propose and accept a new candidate")
        if abs(existing["probability"] - probability) > 1e-9:
            raise ValueError(
                f"forecast {forecast_id[:24]} already stands at "
                f"p={existing['probability']:.2f}: moving it is an explicit "
                "update_probability act with its reason, never a re-creation")
        new_supporting = [c for c in supporting_claim_ids
                          if c not in existing["basis"]["supporting_claim_ids"]]
        new_contradicting = [c for c in contradicting_claim_ids
                             if c not in existing["basis"]["contradicting_claim_ids"]]
        if new_supporting or new_contradicting:
            basis = compute_basis(
                store,
                tuple(existing["basis"]["supporting_claim_ids"]) + tuple(new_supporting),
                tuple(existing["basis"]["contradicting_claim_ids"])
                + tuple(new_contradicting))
            folded = _reappend(ctx, existing, {"basis": basis},
                               change_reason=f"creation fold ({provenance_kind}): "
                                             f"+{len(new_supporting)}/+"
                                             f"{len(new_contradicting)} claims",
                               history_note="CREATION_FOLD")
            record_transition(ctx, subject_kind="analytic_forecast",
                              subject_id=forecast_id,
                              transition_type="EVIDENCE_UPDATED",
                              detail=f"creation fold: +{len(new_supporting)} "
                                     f"supporting, +{len(new_contradicting)} "
                                     f"contradicting",
                              caused_by=digest_id("fold", forecast_id,
                                                  *sorted(new_supporting
                                                          + new_contradicting)),
                              evidence_refs=tuple((new_supporting
                                                   + new_contradicting)[:5]))
            return folded
        return existing
    if provenance_kind == "MODEL":
        require_accepted_candidate(
            store, inference_id=inference_id, proposal_id=proposal_id,
            target_kind="analytic_forecast",
            materialized={"question": question, "probability": probability,
                          "horizon_time": horizon_time})
    record = ForecastRecord(
        forecast_id=forecast_id, version=1,
        question=question, outcome_semantics=outcome_semantics,
        proposition_refs=proposition_refs, horizon_time=horizon_time,
        resolution=resolution, probability=probability,
        probability_basis=probability_basis,
        basis=compute_basis(store, supporting_claim_ids, contradicting_claim_ids),
        assumption_ids=assumption_ids, indicator_ids=indicator_ids,
        author=author, domain=domain,
        status="OPEN", authority=_authority_for(provenance_kind),
        provenance_kind=provenance_kind, inference_id=inference_id,
        proposal_id=proposal_id,
        outcome="", resolved_time="", resolution_evidence_refs=(),
        resolver_id="", resolver_kind="",
        change_reason="", history=(f"CREATED:{provenance_kind}:{author}",),
        recorded_time=ctx.now_fn(), marking=ctx.marking)
    appended = append_version(ctx, record)
    ensure_transition(ctx, subject_kind="analytic_forecast", subject_id=forecast_id,
                      transition_type="CREATED",
                      detail=f"forecast created: {question[:120]!r} "
                             f"p={probability:.2f} by {author} "
                             f"({provenance_kind}); horizon {horizon_time[:19]}",
                      caused_by=caused_by or forecast_id,
                      evidence_refs=tuple(record.basis.supporting_claim_ids[:5]),
                      to_status="OPEN")
    return appended


def _reappend(ctx: AnalyticContext, forecast: Mapping[str, Any],
              updates: dict[str, Any], change_reason: str,
              history_note: str) -> dict[str, Any]:
    merged = {k: v for k, v in forecast.items() if k != "record_type"}
    merged.update(updates)
    merged["version"] = ctx.store.next_analytic_version(
        "analytic_forecast", forecast["forecast_id"])
    merged["change_reason"] = change_reason
    merged["history"] = tuple(forecast["history"]) + (history_note,)
    merged["recorded_time"] = ctx.now_fn()
    # a re-append NEVER re-classifies: the forecast keeps its own marking
    merged["marking"] = marking_from_record(forecast["marking"]) \
        if isinstance(forecast.get("marking"), dict) else forecast["marking"]
    for key in ("assumption_ids", "indicator_ids", "resolution_evidence_refs",
                "history"):
        merged[key] = tuple(merged[key])
    merged["proposition_refs"] = tuple(tuple(p) for p in merged["proposition_refs"])
    if isinstance(merged["basis"], Mapping):
        merged["basis"] = basis_from_record(merged["basis"])
    if isinstance(merged["resolution"], Mapping):
        rule = {k: v for k, v in merged["resolution"].items() if k != "record_type"}
        rule["absence_required_source_ids"] = tuple(
            rule.get("absence_required_source_ids", ()))
        merged["resolution"] = ResolutionRule(**rule)
    record = ForecastRecord(**merged)
    return append_version(ctx, record)


def _require_open(forecast: Mapping[str, Any]) -> None:
    from .contracts import FORECAST_TERMINAL_STATUSES
    if forecast["status"] in FORECAST_TERMINAL_STATUSES:
        raise ValueError(f"forecast {forecast['forecast_id'][:24]} is "
                         f"{forecast['status']}: a settled forecast is history, "
                         "not a live judgment")


def update_probability(ctx: AnalyticContext, forecast_id: str, *,
                       probability: float, reason: str,
                       evidence_refs: tuple[str, ...] = (),
                       actor_id: str, actor_kind: str,
                       indicator_id: str = "",
                       provenance_kind: str = "ANALYST",
                       inference_id: str = "",
                       proposal_id: str = "") -> dict[str, Any]:
    """Move a forecast's probability — as a new version stating why.

    Three legitimate movers, all on record:
      * a HUMAN act (analyst judgment);
      * a fired indicator executing a HUMAN pre-authorized conditional
        update (the engine passes the indicator id; the pre-authorization
        was validated at arming time);
      * an accepted MODEL candidate through the full gate.
    A SERVICE actor without a pre-authorized indicator cannot move a number.
    """
    store = ctx.store
    forecast = store.current_forecasts().get(forecast_id)
    if forecast is None:
        raise ValueError(f"unknown forecast: {forecast_id}")
    _require_open(forecast)
    if not reason:
        raise ValueError("a probability update states its reason")
    if indicator_id and (actor_kind == "HUMAN" or provenance_kind == "MODEL"):
        # an indicator id belongs to exactly one path: the SERVICE-executed
        # pre-authorization. On any other path it would skip every indicator
        # check yet still burn the firing's consumption marker — silently
        # defeating the analyst's own recorded conditional judgment
        raise ValueError("an indicator id accompanies only the SERVICE "
                         "execution of its pre-authorization: a human or "
                         "model update stands on its own authority")
    if provenance_kind == "MODEL" and proposal_id \
            and forecast.get("proposal_id") == proposal_id \
            and abs(forecast["probability"] - probability) <= 1e-9:
        # crash-recovery completion of THIS update: the version already
        # landed (it carries this very proposal), so the consumption scan
        # would refuse a fresh spend — complete the possibly-missing
        # transition instead of leaving a version without its history
        record_transition(ctx, subject_kind="analytic_forecast",
                          subject_id=forecast_id,
                          transition_type="PROBABILITY_UPDATED",
                          detail=f"p → {probability:.2f} by accepted model "
                                 f"candidate: {reason[:180]}",
                          caused_by=digest_id("pupdate", forecast_id,
                                              str(forecast["version"])),
                          evidence_refs=evidence_refs[:5],
                          to_status=forecast["status"])
        return forecast
    if provenance_kind == "MODEL":
        require_accepted_candidate(
            store, inference_id=inference_id, proposal_id=proposal_id,
            target_kind="analytic_forecast",
            materialized={"question": forecast["question"],
                          "probability": probability,
                          "horizon_time": forecast["horizon_time"]})
        authority = "SUPPORTED_INFERENCE"
    elif actor_kind == "HUMAN":
        authority = "ANALYST_ASSESSMENT"
    elif indicator_id:
        indicator = store.current_indicators().get(indicator_id)
        if indicator is None or indicator["status"] != "FIRED":
            raise ValueError("an automatic update requires the FIRED indicator "
                             "that pre-authorizes it")
        if forecast_id not in indicator["forecast_ids"]:
            raise ValueError(
                f"indicator {indicator_id[:24]} does not name forecast "
                f"{forecast_id[:24]}: a pre-authorization covers exactly the "
                "forecasts the human armed it for, nothing else")
        effect = indicator["effect"]
        if effect["mode"] != "APPLY_PROBABILITY" \
                or abs(effect["target_probability"] - probability) > 1e-9:
            raise ValueError("the applied probability must be exactly what the "
                             "human pre-authorized on the indicator")
        # a firing is ONE act. Consumption is the INDICATOR_FIRED marker on
        # this forecast, not the current value of the number — a value check
        # would let a stale firing overwrite every later human judgment that
        # moved the probability away from the target
        marker = digest_id("fire", indicator_id, forecast_id)
        if any(t["transition_type"] == "INDICATOR_FIRED"
               and t["caused_by"] == marker
               for t in store.transitions_for(forecast_id)):
            raise ValueError(
                f"indicator {indicator_id[:24]}'s firing was already executed "
                f"on forecast {forecast_id[:24]}: a pre-authorization is one "
                "act, not a standing power over the forecast")
        # The numeric target is the human's pre-authorized public effect.  The
        # standing indicator can contain more-restricted rationale and the
        # firing evidence can contain more-restricted values.  Cite those
        # objects on derived transitions, but never copy their prose into the
        # lower forecast version.
        reason = (f"pre-authorized by {effect['authorized_by']} via indicator "
                  f"{indicator_id} firing")
        evidence_refs = tuple(dict.fromkeys((indicator_id, *evidence_refs)))
        if abs(forecast["probability"] - probability) <= 1e-9:
            # the number already stands at the target: consume the firing
            # (marker below) without a redundant version
            record_transition(ctx, subject_kind="analytic_forecast",
                              subject_id=forecast_id,
                              transition_type="INDICATOR_FIRED",
                              detail="pre-authorized target already standing; "
                                     "firing consumed without movement",
                              caused_by=marker, evidence_refs=evidence_refs[:5])
            return store.current_forecasts()[forecast_id]
        authority = "ANALYST_ASSESSMENT"  # the human's recorded conditional act
    else:
        raise ValueError("a probability moves only by human act, human "
                         "pre-authorization, or an accepted model candidate")
    if not (0.0 < probability < 1.0):
        raise ValueError("a forecast probability lies strictly inside (0,1)")
    old = forecast["probability"]
    # a SERVICE-executed pre-authorization moves the NUMBER, never the review
    # standing: UPDATE_REQUIRED was raised for a human and only a human (or a
    # human-accepted candidate) clears it. And no update un-passes a passed
    # horizon — HORIZON_PASSED is a fact about the clock, not a review flag
    if indicator_id and actor_kind != "HUMAN" and provenance_kind != "MODEL":
        new_status = forecast["status"]
    elif forecast["status"] == "UPDATE_REQUIRED":
        new_status = "OPEN"
    else:
        new_status = forecast["status"]
    updated = _reappend(ctx, forecast,
                        {"probability": probability,
                         "probability_basis": reason,
                         "authority": authority,
                         "provenance_kind": provenance_kind
                         if provenance_kind == "MODEL" else "ANALYST",
                         # a non-MODEL version does not wear a model trail:
                         # the acceptance it consumed stays consumed (the
                         # scan covers all versions), and the record's stated
                         # provenance matches its identifiers
                         "inference_id": inference_id
                         if provenance_kind == "MODEL" else "",
                         "proposal_id": proposal_id
                         if provenance_kind == "MODEL" else "",
                         "status": new_status},
                        change_reason=f"p {old:.2f} → {probability:.2f}: "
                                      f"{reason[:200]}",
                        history_note=f"P:{old:.2f}->{probability:.2f}")
    record_transition(ctx, subject_kind="analytic_forecast", subject_id=forecast_id,
                      transition_type="PROBABILITY_UPDATED",
                      detail=f"p {old:.2f} → {probability:.2f} by "
                             f"{actor_id or provenance_kind}"
                             f"{' via indicator ' + indicator_id[:18] if indicator_id else ''}"
                             f": {reason[:180]}",
                      caused_by=digest_id("pupdate", forecast_id, str(updated["version"])),
                      evidence_refs=evidence_refs[:5],
                      from_status=forecast["status"], to_status=new_status)
    if indicator_id:
        # consume the firing on this forecast the moment its effect lands —
        # never leave a window in which the same firing is redeemable again
        record_transition(ctx, subject_kind="analytic_forecast",
                          subject_id=forecast_id,
                          transition_type="INDICATOR_FIRED",
                          detail=f"pre-authorized update executed: p → "
                                 f"{probability:.2f}",
                          caused_by=digest_id("fire", indicator_id, forecast_id),
                          evidence_refs=evidence_refs[:5])
    return updated


def refresh_forecast(ctx: AnalyticContext, forecast_id: str, *,
                     caused_by: str) -> dict[str, Any]:
    """Re-derive a forecast's standing from live state: degraded basis marks
    it UPDATE_REQUIRED (the number is stale, visibly — the machine never
    moves it); a passed horizon triggers typed resolution or waits, coverage
    permitting."""
    store = ctx.store
    forecast = store.current_forecasts().get(forecast_id)
    if forecast is None:
        raise ValueError(f"unknown forecast: {forecast_id}")
    from .contracts import FORECAST_TERMINAL_STATUSES
    if forecast["status"] in FORECAST_TERMINAL_STATUSES:
        return forecast
    basis = compute_basis(store, forecast["basis"]["supporting_claim_ids"],
                          forecast["basis"]["contradicting_claim_ids"])
    changes = basis_changed_materially(forecast["basis"], basis)
    updates: dict[str, Any] = {}
    if changes:
        updates["basis"] = basis
    degradation = "degradation" in changes \
        or "contradiction" in changes
    if degradation and forecast["status"] == "OPEN":
        updates["status"] = "UPDATE_REQUIRED"
    horizon_passed = parse_time(ctx.now_fn()) > parse_time(forecast["horizon_time"])
    if horizon_passed and forecast["status"] in ("OPEN", "UPDATE_REQUIRED"):
        updates["status"] = "HORIZON_PASSED"
    if not updates:
        if horizon_passed:
            return try_machine_resolution(ctx, forecast_id)
        return forecast
    updated = _reappend(ctx, forecast, updates,
                        change_reason=f"refresh after {caused_by[:60]}: "
                                      f"{', '.join(changes) or 'horizon'}",
                        history_note=f"REFRESH:{forecast['status']}->"
                                     f"{updates.get('status', forecast['status'])}")
    if degradation:
        finding = digest_id("fdeg", forecast_id,
                            *sorted(basis.contradicting_claim_ids),
                            str(basis.degraded_claim_count))
        record_transition(ctx, subject_kind="analytic_forecast",
                          subject_id=forecast_id, transition_type="BASIS_DEGRADED",
                          detail=f"{basis.degraded_claim_count} supporting claim(s) "
                                 f"degraded, {len(basis.contradicting_claim_ids)} "
                                 f"contradiction(s): the probability is an "
                                 f"unreviewed number over changed evidence",
                          caused_by=finding,
                          evidence_refs=tuple(basis.contradicting_claim_ids[:5]),
                          from_status=forecast["status"],
                          to_status=updates.get("status", forecast["status"]))
    if updates.get("status") == "HORIZON_PASSED":
        record_transition(ctx, subject_kind="analytic_forecast",
                          subject_id=forecast_id, transition_type="HORIZON_PASSED",
                          detail=f"horizon {forecast['horizon_time'][:19]} passed; "
                                 f"resolution pending",
                          caused_by=digest_id("horizon", forecast_id),
                          from_status=forecast["status"], to_status="HORIZON_PASSED")
        return try_machine_resolution(ctx, forecast_id)
    return updated


def _subject_match_values(subject_refs: tuple[str, ...]) -> set[str]:
    values = set()
    for ref in subject_refs:
        if not ref:
            continue
        values.add(ref.casefold())
        values.add(ref.split(":", 1)[-1].casefold())
    return values


def _execution_touches_subject(execution: Mapping[str, Any],
                               subject_values: set[str],
                               query_values: Mapping[str, str]) -> bool:
    """Was this execution ABOUT the subject? A successful search of the right
    source about a different entity is still noise. Checked against the
    persisted query value (via the discovery plan) and the request URL; an
    execution attributable to neither fails safe — it does not count.

    The URL check is DELIMITED and length-gated: this gate stands between
    silence and RESOLVED_FALSE, so a short or generic subject value must not
    attribute the whole world's searches to the question. Short values can
    only bind through the exact plan-query join."""
    query_value = query_values.get(execution.get("query_id", ""), "")
    if query_value and query_value.casefold() in subject_values:
        return True
    url = (execution.get("request_url") or "").casefold()
    if not url:
        return False
    for value in subject_values:
        if not value or len(value) < 6:
            continue
        # \w lookarounds, not an ASCII class: a non-ASCII letter is part of
        # a token, never a boundary ('oslobank' must not match 'oslobankø')
        if re.search(rf"(?<!\w){re.escape(value)}(?!\w)", url):
            return True
    return False


def _absence_coverage_satisfied(store: AnalyticStore, rule: Mapping[str, Any],
                                since: str,
                                subject_refs: tuple[str, ...] = ()
                                ) -> tuple[bool, tuple[str, ...], str]:
    """Was the declared coverage actually achieved since `since`? Checkable
    fact: successful fabric executions of the required sources, ABOUT the
    question's subject when one is declared. Returns (satisfied, evidence
    execution ids, explanation)."""
    subject_values = _subject_match_values(subject_refs)
    query_values: dict[str, str] = {}
    if subject_values:
        for plan in store.records_of("fabric_discovery_plan"):
            for query in plan.get("queries", ()):
                query_values[query["query_id"]] = query.get("value", "")
    successes: dict[str, list[str]] = {}
    unattributed: dict[str, int] = {}
    for execution in store.records_of("fabric_execution"):
        if execution["outcome"] not in ("EXECUTED_WITH_RESULTS", "EXECUTED_EMPTY"):
            continue
        if not execution.get("completed_time") \
                or parse_time(execution["completed_time"]) < parse_time(since):
            continue
        if subject_values and not _execution_touches_subject(
                execution, subject_values, query_values):
            unattributed[execution["source_id"]] = \
                unattributed.get(execution["source_id"], 0) + 1
            continue
        successes.setdefault(execution["source_id"], []).append(
            execution["execution_id"])
    required = tuple(rule.get("absence_required_source_ids", ()))
    if not required:
        # legacy/hand-built rule without declared sources: coverage is
        # unfalsifiable, so it is never satisfied — silence stays silence
        return False, (), ("no declared coverage sources: absence cannot be "
                           "established without naming where to look")
    missing_required = [s for s in required if s not in successes]
    if missing_required:
        # "never looked" and "looked, could not attribute" are different
        # facts and imply different remedies — say which one holds
        parts = []
        for source_id in missing_required:
            filtered = unattributed.get(source_id, 0)
            if filtered:
                parts.append(f"{source_id} searched {filtered} time(s) but no "
                             f"execution attributable to the subject")
            else:
                parts.append(f"{source_id} never successfully searched")
        return False, (), (f"coverage unmet since {since[:19]}: "
                           + "; ".join(parts))
    # only DECLARED sources count toward the minimum: a successful search of
    # an unrelated source is noise, not coverage of this question
    covered = [s for s in required if s in successes]
    minimum = rule.get("absence_min_successful_sources", 1)
    if len(covered) < minimum:
        return False, (), (f"only {len(covered)} of the declared source(s) "
                           f"successfully searched since {since[:19]}; "
                           f"{minimum} required")
    evidence = tuple(successes[s][0] for s in covered)[:8]
    return True, evidence, (f"{len(covered)} declared source(s) successfully "
                            f"searched since {since[:19]}: {', '.join(covered)}")


def try_machine_resolution(ctx: AnalyticContext, forecast_id: str) -> dict[str, Any]:
    """Attempt typed machine resolution. TRUE may resolve early on matching
    evidence; FALSE only after the horizon, and only when the evidence is
    fresher than the horizon or the rule's declared coverage was achieved —
    otherwise the forecast waits at HORIZON_PASSED with a COVERAGE_GAP on
    record."""
    store = ctx.store
    forecast = store.current_forecasts().get(forecast_id)
    if forecast is None:
        raise ValueError(f"unknown forecast: {forecast_id}")
    from .contracts import FORECAST_TERMINAL_STATUSES
    if forecast["status"] in FORECAST_TERMINAL_STATUSES:
        return forecast
    rule = forecast["resolution"]
    if rule["kind"] == "HUMAN_JUDGMENT":
        return forecast
    if any(r["item_id"] == digest_id("review-latematch", forecast_id)
           for r in store.open_review_items()):
        # the record itself declared this question machine-undecidable and
        # handed it to a human: while that item is open, NO machine verdict
        # — TRUE or FALSE — may settle it (a later flip-back must not let
        # the machine assert what the claim read at a horizon it never saw)
        return forecast
    horizon_passed = parse_time(ctx.now_fn()) > parse_time(forecast["horizon_time"])

    if rule["kind"] == "CLAIM_PREDICATE":
        claim_id = digest_id("claim", world_object_id(rule["claim_subject_ref"]),
                             rule["claim_attribute"])
        claim = store.current_claims().get(claim_id)
        current = claim is not None and store.claim_state(claim_id) == "CURRENT"
        matches = current and claim["object_or_value"] == rule["expected_value"]
        evidence_time = _claim_evidence_time(ctx, claim) if current else ""
        if matches and not horizon_passed:
            return _resolve(ctx, forecast, "TRUE", (claim_id,),
                            f"claim {rule['claim_subject_ref']} "
                            f"{rule['claim_attribute']} = "
                            f"{rule['expected_value']!r} (machine, per rule)")
        if matches and horizon_passed:
            # "by the horizon" is part of the question: a late flip only
            # proves TRUE if the evidence state predates the horizon
            if evidence_time and parse_time(evidence_time) \
                    <= parse_time(forecast["horizon_time"]):
                return _resolve(ctx, forecast, "TRUE", (claim_id,),
                                f"claim {rule['claim_subject_ref']} "
                                f"{rule['claim_attribute']} = "
                                f"{rule['expected_value']!r} with evidence "
                                f"state {evidence_time[:19]} at or before the "
                                f"horizon (machine, per rule)")
            # the value stands NOW but the evidence postdates the horizon:
            # whether it held AT the horizon is not machine-decidable —
            # neither TRUE (too late) nor FALSE (it may well have flipped in
            # time). Route to a human, visibly, and wait.
            item_id = digest_id("review-latematch", forecast["forecast_id"])
            if not any(r["item_id"] == item_id
                       for r in store.records_of("review_item")):
                item = ReviewItem(
                    item_id=item_id, kind="EXPECTED_NOT_OBSERVED",
                    subject_kind="analytic_forecast",
                    subject_id=forecast["forecast_id"],
                    detail=f"the expected value "
                           f"{rule['expected_value'][:60]!r} stands in "
                           f"post-horizon evidence ({evidence_time[:19]}); "
                           f"whether it held by the horizon "
                           f"{forecast['horizon_time'][:19]} needs a human "
                           f"judgment",
                    evidence_refs=(claim_id,),
                    status="OPEN", resolution_note="",
                    recorded_time=ctx.now_fn(), marking=ctx.marking)
                store.append("REVIEW_ITEM_RECORDED", item,
                             recorded_time=item.recorded_time, actor=ctx.actor)
            return forecast
        if horizon_passed and current and not matches:
            if evidence_time and parse_time(evidence_time) \
                    >= parse_time(forecast["horizon_time"]):
                return _resolve(ctx, forecast, "FALSE", (claim_id,),
                                f"at horizon the claim reads "
                                f"{claim['object_or_value'][:80]!r}, not "
                                f"{rule['expected_value']!r}; evidence state "
                                f"{evidence_time[:19]} postdates the horizon")
        if horizon_passed:
            return _false_by_absence_or_wait(ctx, forecast, rule)
        return forecast

    # EVENT_OCCURRED
    subject_object = world_object_id(rule["event_subject_ref"])
    for activity in store.records_of("activity"):
        if activity["activity_type"] != rule["event_activity_type"]:
            continue
        if subject_object not in activity["subject_ids"]:
            continue
        occurred = activity.get("valid_from") or activity.get("source_time")
        if occurred and parse_time(occurred) \
                <= parse_time(forecast["horizon_time"]):
            return _resolve(ctx, forecast, "TRUE", (activity["activity_id"],),
                            f"event {rule['event_activity_type']} on "
                            f"{rule['event_subject_ref']} occurred "
                            f"{occurred[:19]} (machine, per rule)")
    if horizon_passed:
        return _false_by_absence_or_wait(ctx, forecast, rule)
    return forecast


def _claim_evidence_time(ctx: AnalyticContext, claim: Mapping[str, Any]) -> str:
    observations = _observation_index(ctx.store)
    manifestations = _manifestation_index(ctx.store)
    times = []
    for observation_id in claim["observation_ids"]:
        observation = observations.get(observation_id)
        if observation is None:
            continue
        manifestation = manifestations.get(observation["manifestation_id"], {})
        state_time = _state_time(manifestation) if manifestation else ""
        if state_time:
            times.append(state_time)
    return max(times, default="")


def _false_by_absence_or_wait(ctx: AnalyticContext, forecast: Mapping[str, Any],
                              rule: Mapping[str, Any]) -> dict[str, Any]:
    store = ctx.store
    satisfied, evidence, explanation = _absence_coverage_satisfied(
        store, rule, forecast["horizon_time"],
        subject_refs=(rule.get("claim_subject_ref", ""),
                      rule.get("event_subject_ref", "")))
    if satisfied:
        return _resolve(ctx, forecast, "FALSE", evidence,
                        f"nothing satisfying the rule was observed by the "
                        f"horizon, and coverage was achieved: {explanation}")
    # coverage insufficient: silence is not FALSE — the gap is durable state
    item_id = digest_id("review-coverage", "forecast", forecast["forecast_id"])
    if not any(r["item_id"] == item_id for r in store.records_of("review_item")):
        item = ReviewItem(
            item_id=item_id, kind="COVERAGE_GAP", subject_kind="analytic_forecast",
            subject_id=forecast["forecast_id"],
            detail=f"horizon passed but resolution is coverage-blocked: "
                   f"{explanation}. Absence of evidence is not FALSE until the "
                   f"declared coverage is achieved.",
            evidence_refs=(forecast["forecast_id"],),
            status="OPEN", resolution_note="",
            recorded_time=ctx.now_fn(), marking=ctx.marking)
        store.append("REVIEW_ITEM_RECORDED", item, recorded_time=item.recorded_time,
                     actor=ctx.actor)
    return store.current_forecasts()[forecast["forecast_id"]]


def _close_review_item(ctx: AnalyticContext, item_id: str, note: str) -> None:
    """Resolve one open review item — an item that outlives the question it
    was raised about is noise in the queue. Idempotent: already-resolved or
    absent items are left alone."""
    store = ctx.store
    open_item = next((r for r in store.open_review_items()
                      if r["item_id"] == item_id), None)
    if open_item is None:
        return
    resolved = ReviewItem(
        item_id=item_id, kind=open_item["kind"],
        subject_kind=open_item["subject_kind"],
        subject_id=open_item["subject_id"],
        detail=open_item["detail"],
        evidence_refs=tuple(open_item["evidence_refs"]),
        status="RESOLVED", resolution_note=note[:280],
        version=store.next_family_version("review_item", "item_id", item_id),
        recorded_time=ctx.now_fn(),
        # a re-append NEVER re-classifies: the review item keeps its marking
        marking=marking_from_record(open_item["marking"])
        if isinstance(open_item.get("marking"), dict) else open_item["marking"])
    store.append("REVIEW_ITEM_RECORDED", resolved,
                 recorded_time=resolved.recorded_time, actor=ctx.actor)


def _close_coverage_gap(ctx: AnalyticContext, subject_label: str,
                        subject_id: str, note: str) -> None:
    _close_review_item(
        ctx, digest_id("review-coverage", subject_label, subject_id), note)
    if subject_label == "forecast":
        # the late-match ambiguity item is settled by the same acts that
        # settle the forecast
        _close_review_item(
            ctx, digest_id("review-latematch", subject_id), note)


def _resolve(ctx: AnalyticContext, forecast: Mapping[str, Any], outcome: str,
             evidence_refs: tuple[str, ...], reason: str) -> dict[str, Any]:
    status = "RESOLVED_TRUE" if outcome == "TRUE" else "RESOLVED_FALSE"
    now = ctx.now_fn()
    updated = _reappend(ctx, forecast,
                        {"status": status, "outcome": outcome,
                         "resolved_time": now,
                         "resolution_evidence_refs": evidence_refs,
                         "resolver_id": ctx.actor, "resolver_kind": "SERVICE"},
                        change_reason=reason[:280],
                        history_note=f"{status}:machine")
    record_transition(ctx, subject_kind="analytic_forecast",
                      subject_id=forecast["forecast_id"],
                      transition_type=status,
                      detail=f"{reason[:220]} (final p was "
                             f"{forecast['probability']:.2f})",
                      caused_by=digest_id("resolve", forecast["forecast_id"]),
                      evidence_refs=evidence_refs[:5],
                      from_status=forecast["status"], to_status=status)
    _close_coverage_gap(ctx, "forecast", forecast["forecast_id"],
                        f"forecast resolved {status}: {reason[:180]}")
    return updated


def resolve_forecast_human(ctx: AnalyticContext, forecast_id: str, *,
                           outcome: str, evidence_refs: tuple[str, ...],
                           note: str, actor_id: str,
                           actor_kind: str) -> dict[str, Any]:
    """A human settles the question — TRUE/FALSE with evidence, or VOID with
    a stated reason (ill-posed, superseded, unresolvable)."""
    if actor_kind != "HUMAN":
        raise ValueError("human resolution requires a human")
    if outcome not in ("TRUE", "FALSE", "VOID"):
        raise ValueError(f"invalid outcome: {outcome}")
    store = ctx.store
    forecast = store.current_forecasts().get(forecast_id)
    if forecast is None:
        raise ValueError(f"unknown forecast: {forecast_id}")
    _require_open(forecast)
    if outcome in ("TRUE", "FALSE") and not evidence_refs:
        raise ValueError("a TRUE/FALSE resolution requires evidence")
    if not note:
        raise ValueError("a resolution states its reasoning")
    status = {"TRUE": "RESOLVED_TRUE", "FALSE": "RESOLVED_FALSE",
              "VOID": "RESOLVED_VOID"}[outcome]
    now = ctx.now_fn()
    updated = _reappend(ctx, forecast,
                        {"status": status,
                         "outcome": outcome if outcome != "VOID" else "",
                         "resolved_time": now,
                         "resolution_evidence_refs": evidence_refs,
                         "resolver_id": actor_id, "resolver_kind": "HUMAN"},
                        change_reason=f"resolved {outcome} by {actor_id}: "
                                      f"{note[:200]}",
                        history_note=f"{status}:{actor_id}")
    record_transition(ctx, subject_kind="analytic_forecast", subject_id=forecast_id,
                      transition_type=status,
                      detail=f"{note[:220]} (final p was "
                             f"{forecast['probability']:.2f})",
                      caused_by=f"analyst:{actor_id}:resolve",
                      evidence_refs=evidence_refs[:5],
                      from_status=forecast["status"], to_status=status)
    _close_coverage_gap(ctx, "forecast", forecast_id,
                        f"forecast resolved {status} by {actor_id}")
    return updated


def withdraw_forecast(ctx: AnalyticContext, forecast_id: str, *, note: str,
                      actor_id: str, actor_kind: str) -> dict[str, Any]:
    if actor_kind != "HUMAN":
        raise ValueError("withdrawing a forecast is a human act")
    store = ctx.store
    forecast = store.current_forecasts().get(forecast_id)
    if forecast is None:
        raise ValueError(f"unknown forecast: {forecast_id}")
    _require_open(forecast)
    updated = _reappend(ctx, forecast,
                        {"status": "WITHDRAWN", "resolver_id": actor_id,
                         "resolver_kind": "HUMAN", "resolved_time": ctx.now_fn()},
                        change_reason=f"withdrawn by {actor_id}: {note[:200]}",
                        history_note=f"WITHDRAWN:{actor_id}")
    record_transition(ctx, subject_kind="analytic_forecast", subject_id=forecast_id,
                      transition_type="WITHDRAWN", detail=note[:280],
                      caused_by=f"analyst:{actor_id}:withdraw",
                      from_status=forecast["status"], to_status="WITHDRAWN")
    _close_coverage_gap(ctx, "forecast", forecast_id,
                        f"forecast withdrawn by {actor_id}: the question is moot")
    return updated
