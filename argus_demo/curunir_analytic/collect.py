"""Analytics should drive collection: analytical uncertainty becomes
discriminating observations and information requirements through the
EXISTING active-collection machinery — no parallel planner.

The uncertainty patterns this module recognizes:

  theme important but single-origin      → seek an independent source family
  narrative origin unresolved            → seek earlier/historical manifestations
  inferred interest without public stand → seek a primary statement
  impact path resting on weak edges      → seek evidence discriminating the edge
  forecast single-family or coverage-blocked → seek independent corroboration,
                                           or search exactly the declared sources
  absence indicator needing its coverage → look where the thing would appear,
                                           before (or after) its deadline

Each becomes a typed DiscriminatingObservation (idempotent) plus a mission
information requirement; the existing EIV planner ranks routes with the
coverage and dependence arithmetic it already has.
"""
from __future__ import annotations

from typing import Any, Mapping

from curunir_operational.access import Marking
from curunir_semantic.collection import requirement_for_discriminator
from curunir_semantic.hypotheses import propose_discriminator
from curunir_semantic.worldmodel import parse_subject

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
    """Scan current analytical state for the recognized uncertainty patterns.
    Pure derivation — records nothing."""
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
            f"{theme['theme_id'][:18]}? All current support descends from one "
            f"origin family.",
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
        question = (f"What is the earliest historical manifestation of the "
                    f"proposition {narrative['narrative_id'][:18]}? Earliest "
                    f"currently observed: "
                    f"{narrative['earliest_time'][:19] or 'unknown'}."
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
            f"Is there a primary public statement bearing on assessment "
            f"{assessment['assessment_id'][:18]}? The interest is inferred; "
            f"no explicit position is in evidence.",
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
            f"What evidence would discriminate the uncertain link "
            f"{edge['from_id'][:18]} → {edge['to_id'][:18]} "
            f"({edge['authority']}) in the exposure of the objective "
            f"{path['path_id'][:18]}?",
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
            # a non-claim-predicate forecast with no resolvable supporting
            # claim gives the discriminator nothing typed to bind to — the
            # skip is deliberate and mirrors the indicator loop below
            continue
        if coverage_blocked:
            question = (f"Resolution of the forecast "
                        f"{forecast['forecast_id'][:18]} is coverage-blocked: "
                        f"the sources its rule declares "
                        f"({', '.join(rule['absence_required_source_ids'])}) "
                        f"have not been successfully searched since the "
                        f"horizon. Silence means nothing until they are.")
        else:
            question = (f"Does any independent source family bear on the "
                        f"forecast {forecast['forecast_id'][:18]}? Its entire "
                        f"basis descends from "
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
            continue  # a dead question does not drive collection
        required = ", ".join(indicator["coverage_required_source_ids"]) \
            or f"{indicator['coverage_min_successful_sources']} source(s)"
        watched_claims = tuple(dict.fromkeys(
            claim_id
            for forecast_id in indicator["forecast_ids"]
            for claim_id in store.current_forecasts()
            .get(forecast_id, {"basis": {"supporting_claim_ids": ()}})
            ["basis"]["supporting_claim_ids"]))
        if not watched_claims:
            continue  # nothing typed to bind the discriminator to
        needs.append(_need(
            "forecast_indicator", indicator["indicator_id"],
            # cite by id; a PUBLIC collection pass must not embed a
            # SPECIAL indicator's description (A4 / R25A-1 sibling)
            (f"The absence indicator {indicator['indicator_id'][:18]} "
             + ("passed its deadline without its declared coverage"
                if blocked else
                f"needs its declared coverage ({required}) searched before "
                f"its deadline {indicator['deadline'][:19]}")
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
    """Turn analytical uncertainty into discriminators + mission information
    requirements through the existing machinery. Idempotent end to end."""
    store = ctx.store
    if needs is None:
        needs = analytic_collection_needs(store)
    opened = []
    from curunir_operational.access import inherited_marking
    from .substrate import claim_markings
    for need in needs:
        # floor persist on the claims the need rests on so a PUBLIC
        # collection pass cannot write a SPECIAL subject_ref / attribute
        # into a discriminator CTX_B can read (R27A-2)
        from .substrate import resolve_reference_markings
        marking = inherited_marking(
            ctx.marking,
            [*claim_markings(store, need["claim_ids"]),
             *resolve_reference_markings(
                 store, (need.get("source_id"),
                         need.get("desired_subject_ref")))])
        discriminator = propose_discriminator(
            store, question=need["question"],
            claim_ids=tuple(need["claim_ids"]),
            desired_observation_type=need["desired_observation_type"],
            desired_subject_ref=need["desired_subject_ref"],
            desired_attribute=need["desired_attribute"],
            independence_required=need["independence_required"],
            now=ctx.now_fn(), actor=ctx.actor, marking=marking)
        outcome = requirement_for_discriminator(
            store, discriminator, mission_context=mission_context,
            now=ctx.now_fn(), actor=ctx.actor, marking=marking)
        opened.append({"need": need,
                       "discriminator": outcome["discriminator"],
                       "requirement": outcome["requirement"]})
    return opened
