"""Analytical uncertainty turned into collection, through the existing active-
collection machinery rather than a parallel planner.

The patterns recognized here: a single-origin theme wants an independent source
family; an unresolved narrative origin wants earlier manifestations; an inferred
interest without a public stand wants a primary statement; a weak impact edge
wants discriminating evidence; a coverage-blocked or single-family forecast
wants the declared sources searched; an absence indicator wants coverage where
the thing would appear. Each becomes a discriminator plus a mission information
requirement, which the existing planner ranks.
"""
from __future__ import annotations

from typing import Any, Mapping

from curunir_semantic.collection import requirement_for_discriminator
from curunir_semantic.hypotheses import propose_discriminator

from .store import AnalyticStore
from .substrate import AnalyticContext


def _claim(store: AnalyticStore, claim_id: str) -> Mapping[str, Any] | None:
    return store.current_claims().get(claim_id)


def _need(kind: str, subject_id: str, question: str, *, claim_ids: tuple[str, ...],
          desired_type: str, desired_subject: str, desired_attribute: str,
          independence_required: bool) -> dict[str, Any]:
    return {"source_kind": kind, "source_id": subject_id, "question": question,
            "claim_ids": claim_ids, "desired_observation_type": desired_type,
            "desired_subject_ref": desired_subject,
            "desired_attribute": desired_attribute,
            "independence_required": independence_required}


def analytic_collection_needs(store: AnalyticStore) -> list[dict[str, Any]]:
    """Scan analytical state for the recognized uncertainty patterns, recording
    nothing."""
    needs = []

    for theme in store.current_themes().values():
        if theme["status"] not in ("EMERGING", "ACTIVE", "CONTESTED"):
            continue
        if len(theme["basis"]["origin_families"]) != 1:
            continue
        anchor_claim = _claim(store, theme["basis"]["supporting_claim_ids"][0])
        if anchor_claim is None:
            continue
        needs.append(_need(
            "analytic_theme", theme["theme_id"],
            f"Does any independent source family corroborate the theme "
            f"analytic_theme:{theme['theme_id']}? All current support descends "
            "from one origin family.",
            claim_ids=tuple(theme["basis"]["supporting_claim_ids"]),
            desired_type="ENTITY_ATTRIBUTE"
            if anchor_claim["predicate"] in ("entity_status", "legal_name",
                                             "registration_status", "jurisdiction")
            else "STATEMENT",
            desired_subject=anchor_claim["subject_ref"],
            desired_attribute=anchor_claim["predicate"],
            independence_required=True))

    for narrative in store.current_narratives().values():
        if narrative["status"] not in ("ACTIVE", "CONTESTED"):
            continue
        single_family = len(narrative["basis"]["origin_families"]) == 1
        origin_unresolved = narrative["origin_status"] == "ORIGIN_UNRESOLVED"
        if not single_family and not origin_unresolved:
            continue
        anchor_claim = _claim(store, narrative["basis"]["supporting_claim_ids"][0])
        if anchor_claim is None:
            continue
        question = (f"What is the earliest historical manifestation bearing "
                    f"on analytic_narrative:{narrative['narrative_id']}?"
                    + (" Independent corroboration is also missing: all support "
                       "descends from one origin family." if single_family else ""))
        needs.append(_need(
            "analytic_narrative", narrative["narrative_id"], question,
            claim_ids=tuple(narrative["basis"]["supporting_claim_ids"]),
            desired_type="STATEMENT",
            desired_subject=anchor_claim["subject_ref"],
            desired_attribute=anchor_claim["predicate"],
            independence_required=single_family))

    for assessment in store.current_stakeholder_assessments().values():
        if assessment["status"] != "ACTIVE":
            continue
        has_interest = any(p["kind"] == "INFERRED_INTEREST" and not p["superseded"]
                           for p in assessment["positions"])
        has_public = any(p["kind"] == "PUBLIC_POSITION" and not p["superseded"]
                         for p in assessment["positions"])
        if not has_interest or has_public:
            continue
        interest = next(p for p in assessment["positions"]
                        if p["kind"] == "INFERRED_INTEREST" and not p["superseded"])
        subject_ref = ""
        if interest["claim_ids"]:
            based_on = _claim(store, interest["claim_ids"][0])
            subject_ref = based_on["subject_ref"] if based_on else ""
        needs.append(_need(
            "stakeholder_assessment", assessment["assessment_id"],
            f"Is there a primary public statement bearing on "
            f"stakeholder_assessment:{assessment['assessment_id']}? The "
            "interest is inferred; no explicit position is in evidence.",
            claim_ids=tuple(interest["claim_ids"]),
            desired_type="STATEMENT", desired_subject=subject_ref,
            desired_attribute="", independence_required=False))

    for path in store.current_impact_paths().values():
        if path["status"] not in ("ASSESSED", "CHANGED"):
            continue
        weak_edges = [e for e in path["edges"]
                      if e["authority"] not in ("OBSERVED", "DERIVED")]
        if not weak_edges:
            continue
        edge = weak_edges[0]
        anchor_claim = None
        for basis_id in edge["basis_ids"]:
            anchor_claim = _claim(store, basis_id)
            if anchor_claim:
                break
        if anchor_claim is None:
            for claim_id in path["assumption_ids"]:
                assumption = store.current_assumptions().get(claim_id)
                if assumption and assumption["supporting_claim_ids"]:
                    anchor_claim = _claim(store, assumption["supporting_claim_ids"][0])
                    if anchor_claim:
                        break
        if anchor_claim is None:
            continue
        needs.append(_need(
            "impact_path", path["path_id"],
            f"What evidence would discriminate the uncertain link recorded "
            f"by impact_path:{path['path_id']}?",
            claim_ids=(anchor_claim["claim_id"],),
            desired_type="ENTITY_ATTRIBUTE"
            if anchor_claim["predicate"] in ("entity_status", "legal_name",
                                             "registration_status", "jurisdiction")
            else "STATEMENT",
            desired_subject=anchor_claim["subject_ref"],
            desired_attribute=anchor_claim["predicate"],
            independence_required=True))

    open_gaps = [item for item in store.open_review_items()
                 if item["kind"] == "COVERAGE_GAP"]

    for forecast in store.current_forecasts().values():
        if forecast["status"] not in ("OPEN", "UPDATE_REQUIRED",
                                      "HORIZON_PASSED"):
            continue
        rule = forecast["resolution"]
        coverage_blocked = any(item["subject_id"] == forecast["forecast_id"]
                               for item in open_gaps)
        single_family = len(forecast["basis"]["origin_families"]) <= 1
        if not coverage_blocked and not single_family:
            continue
        anchor_claim = None
        for claim_id in forecast["basis"]["supporting_claim_ids"]:
            anchor_claim = _claim(store, claim_id)
            if anchor_claim:
                break
        if rule["kind"] == "CLAIM_PREDICATE":
            desired_subject = rule["claim_subject_ref"]
            desired_attribute = rule["claim_attribute"]
            desired_type = "ENTITY_ATTRIBUTE"
        elif anchor_claim is not None:
            desired_subject = anchor_claim["subject_ref"]
            desired_attribute = anchor_claim["predicate"]
            desired_type = "STATEMENT"
        else:
            # nothing typed for the discriminator to bind to
            continue
        if coverage_blocked:
            question = (f"Resolution of analytic_forecast:"
                        f"{forecast['forecast_id']} is coverage-blocked: its "
                        f"declared sources ({', '.join(rule['absence_required_source_ids'])}) "
                        "have not been successfully searched "
                        "since the horizon. Silence means nothing until they are.")
        else:
            question = (f"Does any independent source family bear on the "
                        f"analytic_forecast:{forecast['forecast_id']}? Its "
                        f"entire basis descends from "
                        f"{'one origin family' if forecast['basis']['origin_families'] else 'no evidence at all'}.")
        needs.append(_need(
            "analytic_forecast", forecast["forecast_id"], question,
            claim_ids=tuple(forecast["basis"]["supporting_claim_ids"]),
            desired_type=desired_type, desired_subject=desired_subject,
            desired_attribute=desired_attribute,
            independence_required=not coverage_blocked))

    from .contracts import FORECAST_TERMINAL_STATUSES
    forecasts = store.current_forecasts()
    for indicator in store.current_indicators().values():
        blocked = indicator["status"] == "COVERAGE_BLOCKED"
        watching_absence = indicator["status"] == "ARMED" \
            and indicator["kind"] == "ABSENCE"
        if not blocked and not watching_absence:
            continue
        if all(forecasts.get(fid) is None
               or forecasts[fid]["status"] in FORECAST_TERMINAL_STATUSES
               for fid in indicator["forecast_ids"]):
            continue  # a settled question does not drive collection
        watched_claims = tuple(dict.fromkeys(
            claim_id
            for forecast_id in indicator["forecast_ids"]
            for claim_id in forecasts
            .get(forecast_id, {"basis": {"supporting_claim_ids": ()}})
            ["basis"]["supporting_claim_ids"]))
        if not watched_claims:
            continue  # nothing typed to bind the discriminator to
        needs.append(_need(
            "forecast_indicator", indicator["indicator_id"],
            (f"The forecast_indicator:{indicator['indicator_id']} "
             + ("passed its deadline without its declared coverage"
                if blocked else
                "needs its declared coverage searched before its deadline")
             + ": absence only means something where someone looked."),
            claim_ids=watched_claims,
            desired_type=indicator["desired_observation_type"]
            or "ENTITY_ATTRIBUTE",
            desired_subject=indicator["desired_subject_ref"],
            desired_attribute=indicator["desired_attribute"],
            independence_required=False))

    return needs


def open_analytic_requirements(ctx: AnalyticContext, *, mission_context: str,
                               needs: list[dict[str, Any]] | None = None
                               ) -> list[dict[str, Any]]:
    """Open a discriminator and a mission requirement for each need."""
    store = ctx.store
    if needs is None:
        needs = analytic_collection_needs(store)
    opened = []
    for need in needs:
        discriminator = propose_discriminator(
            store, question=need["question"],
            claim_ids=tuple(need["claim_ids"]),
            source_refs=((need["source_kind"], need["source_id"]),),
            desired_observation_type=need["desired_observation_type"],
            desired_subject_ref=need["desired_subject_ref"],
            desired_attribute=need["desired_attribute"],
            independence_required=need["independence_required"],
            now=ctx.now_fn(), actor=ctx.actor, marking=ctx.marking)
        outcome = requirement_for_discriminator(
            store, discriminator, mission_context=mission_context,
            now=ctx.now_fn(), actor=ctx.actor, marking=ctx.marking)
        opened.append({"need": need,
                       "discriminator": outcome["discriminator"],
                       "requirement": outcome["requirement"]})
    return opened
