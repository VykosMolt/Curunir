"""Grouped calibration and stability measurement (contract Section 7, V5.4).

V5.3 was validated on random row splits.  A random split over a corpus whose
rows are drawn from a handful of campaigns, in a handful of languages, from a
handful of source families, puts near-duplicates of every test row in the
training half.  It measured memorization and reported it as generalization:
0.8494 on development, 0.4348 on clean held-out evidence.

Random row splits are not evidence of generalization.  This module validates
by *group*: source family, campaign, language group, document genre and layout
type.  Every helper reports per-fold correctness **and the spread across
folds**, because the V5.4 objective is stable invariant behaviour across
groups, not a high average.  A mean of 0.80 assembled from folds at 0.98 and
0.62 is the V5.3 failure with a better headline.

Three further rules follow from the same diagnosis.

* Reviewer labels calibrate thresholds.  They never define the semantic
  function — that lives in :mod:`.invariants` and is a pure function of the
  invariant vector.  The default fold scorer therefore ignores the training
  fold entirely; what varies across folds is the *evidence*, not the model.
* :func:`feature_stability_report` names the five ways a feature can look
  useful while carrying no semantics: helping only V5.2-origin examples,
  tracking campaign identity, tracking dossier order, tracking
  source-specific vocabulary, and regressing on clean evidence.
* :func:`admissibility_metrics` treats a wrong admission as the most serious
  error *and* refuses to call a degenerate rejector a pass.

Evaluation code.  Research shadow only.
"""
from __future__ import annotations

import statistics
from dataclasses import dataclass
from typing import Any, Callable, Iterable, Mapping, Sequence

from ..v5_1.models import Record, now_utc, stable_id
from . import invariants
from .invariants import (
    INVARIANT_NAMES, TERMINAL_STATES, UNRESOLVED, VIOLATED,
)

CALIBRATION_VERSION = "curunir-extraction-calibration-v5.4"

# The five groupings a V5.4 fold may be cut on.  Random row splits are not on
# this list and there is no code path that produces one.
GROUPING_KEYS: tuple[str, ...] = (
    "source_family", "campaign", "language_group", "document_genre",
    "layout_type",
)

# Terminal states that mean "this is a recoverable assertion, do not throw it
# away".  Recall over these is the counterweight to wrong-admission precision.
RECOVERABLE_STATES: frozenset[str] = frozenset({
    "ACCEPTED_CANDIDATE", "SEMANTICALLY_PARSED", "EVIDENCE_BOUND",
})

_LANGUAGE_GROUPS: Mapping[str, str] = {
    "de": "GERMANIC", "en": "GERMANIC", "nl": "GERMANIC", "sv": "GERMANIC",
    "da": "GERMANIC", "no": "GERMANIC",
    "fr": "ROMANCE", "es": "ROMANCE", "it": "ROMANCE", "pt": "ROMANCE",
    "ro": "ROMANCE", "ca": "ROMANCE",
    "pl": "SLAVIC", "cs": "SLAVIC", "sk": "SLAVIC", "bg": "SLAVIC",
    "hr": "SLAVIC", "sl": "SLAVIC",
    "fi": "URALIC", "hu": "URALIC", "et": "URALIC",
    "el": "HELLENIC", "ga": "CELTIC", "mt": "SEMITIC", "lt": "BALTIC",
    "lv": "BALTIC",
}

EVIDENCE_CLASSES: tuple[str, ...] = ("DEVELOPMENT", "CLEAN_HELD_OUT")


def language_group(language: str) -> str:
    """The language family a fold is cut on.

    Holding out a single language leaks through its family: German held out
    while Dutch and English train still shares morphology, verb-second order
    and the same cue tables.  Folds are cut on the family.
    """
    return _LANGUAGE_GROUPS.get((language or "").strip().casefold()[:2], "OTHER")


# ---------------------------------------------------------------------------
# Section 7.1 — the calibration case
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class CalibrationCase(Record):
    """One adjudicated candidate with every grouping key it belongs to."""

    case_id: str
    candidate_id: str
    source_family: str
    campaign: str
    language: str
    document_genre: str
    layout_type: str
    origin_milestone: str
    evidence_class: str
    dossier_index: int
    predicted_state: str
    reviewer_state: str
    invariant_states: Mapping[str, str]
    features: Mapping[str, float]
    vocabulary: tuple[str, ...]

    def __post_init__(self) -> None:
        for name, state in (("predicted_state", self.predicted_state),
                            ("reviewer_state", self.reviewer_state)):
            if state not in TERMINAL_STATES:
                raise ValueError(f"{name}: unknown terminal state {state!r}")
        if self.evidence_class not in EVIDENCE_CLASSES:
            raise ValueError(f"unknown evidence class: {self.evidence_class}")

    @property
    def language_group(self) -> str:
        return language_group(self.language)

    def group(self, key: str) -> str:
        if key == "language_group":
            return self.language_group
        if key not in GROUPING_KEYS:
            raise ValueError(f"folds may only be cut on {GROUPING_KEYS}, not {key!r}")
        return str(getattr(self, key))

    @property
    def correct(self) -> bool:
        return self.predicted_state == self.reviewer_state


def build_case(assessment: Any, *, reviewer_state: str, source_family: str,
               campaign: str, language: str, document_genre: str,
               layout_type: str, origin_milestone: str = "V5_4",
               evidence_class: str = "CLEAN_HELD_OUT", dossier_index: int = 0,
               features: Mapping[str, float] | None = None,
               vocabulary: Sequence[str] = ()) -> CalibrationCase:
    """Pair one :class:`~.invariants.AdmissibilityAssessment` with its label."""
    states = dict(getattr(assessment.vector, "states", {}))
    return CalibrationCase(
        stable_id("v5-4-calibration-case", assessment.candidate_id, campaign),
        assessment.candidate_id, source_family, campaign, language,
        document_genre, layout_type, origin_milestone, evidence_class,
        int(dossier_index), assessment.terminal_state, reviewer_state, states,
        dict(features or {}), tuple(vocabulary))


# ---------------------------------------------------------------------------
# Section 7.2 — grouped validation
# ---------------------------------------------------------------------------

FoldScorer = Callable[[Sequence[CalibrationCase], Sequence[CalibrationCase]],
                      Mapping[str, str]]


def _default_scorer(train: Sequence[CalibrationCase],
                    test: Sequence[CalibrationCase]) -> dict[str, str]:
    """The V5.4 semantic function, which does not read the training fold.

    This is not a shortcut.  ``derive_terminal_state`` is a pure function of
    the invariant vector, so there is nothing for a training fold to fit.  The
    fold structure still matters: it measures whether the *invariants* behave
    the same way on evidence from a campaign, language family, genre or layout
    the calibration never saw.
    """
    del train
    scored: dict[str, str] = {}
    for case in test:
        if set(case.invariant_states) >= set(INVARIANT_NAMES):
            state, _rule = invariants.derive_terminal_state(
                {name: case.invariant_states[name] for name in INVARIANT_NAMES})
        else:
            state = case.predicted_state
        scored[case.candidate_id] = state
    return scored


def _fold_metrics(cases: Sequence[CalibrationCase],
                  predictions: Mapping[str, str]) -> dict[str, Any]:
    correct = wrong_admissions = over_rejections = quarantined = 0
    recoverable = recovered = accepted = 0
    for case in cases:
        predicted = predictions.get(case.candidate_id, case.predicted_state)
        expected = case.reviewer_state
        if predicted == expected:
            correct += 1
        if predicted == "ACCEPTED_CANDIDATE":
            accepted += 1
            if expected != "ACCEPTED_CANDIDATE":
                wrong_admissions += 1
        if predicted == "REJECTED" and expected != "REJECTED":
            over_rejections += 1
        if predicted == "QUARANTINED":
            quarantined += 1
        if expected in RECOVERABLE_STATES:
            recoverable += 1
            if predicted != "REJECTED":
                recovered += 1
    total = max(1, len(cases))
    return {
        "size": len(cases),
        "correct": correct,
        "correctness": round(correct / total, 4),
        "accepted": accepted,
        "wrong_admissions": wrong_admissions,
        "wrong_admission_rate": round(wrong_admissions / total, 4),
        "over_rejections": over_rejections,
        "over_rejection_rate": round(over_rejections / total, 4),
        "quarantine_rate": round(quarantined / total, 4),
        "recoverable_assertions": recoverable,
        "recoverable_assertion_recall": (round(recovered / recoverable, 4)
                                         if recoverable else None),
        "invariant_failures": invariant_failure_counts(cases),
    }


def _spread(values: Sequence[float]) -> dict[str, Any]:
    """The number the V5.4 objective is actually stated in."""
    if not values:
        return {"folds": 0, "min": None, "max": None, "range": None,
                "mean": None, "variance": None, "stdev": None}
    return {
        "folds": len(values),
        "min": round(min(values), 4),
        "max": round(max(values), 4),
        "range": round(max(values) - min(values), 4),
        "mean": round(statistics.fmean(values), 4),
        "variance": round(statistics.pvariance(values), 6),
        "stdev": round(statistics.pstdev(values), 4),
    }


# A fold-to-fold correctness range wider than this means the behaviour is
# group-specific, whatever the average says.  It is a calibration threshold,
# which is the only thing reviewer labels are allowed to set.
MIN_FOLD_SIZE = 5

MAX_STABLE_SPREAD = 0.15


def grouped_validation(cases: Iterable[CalibrationCase], *, group_key: str,
                       scorer: FoldScorer | None = None,
                       max_spread: float = MAX_STABLE_SPREAD,
                       min_fold_size: int = MIN_FOLD_SIZE) -> dict[str, Any]:
    """Leave-one-group-out validation with per-fold and spread reporting."""
    if group_key not in GROUPING_KEYS:
        raise ValueError(f"folds may only be cut on {GROUPING_KEYS}, "
                         f"not {group_key!r}")
    items = list(cases)
    score = scorer or _default_scorer
    groups = sorted({case.group(group_key) for case in items})

    folds: list[dict[str, Any]] = []
    for group in groups:
        held_out = [case for case in items if case.group(group_key) == group]
        train = [case for case in items if case.group(group_key) != group]
        predictions = score(train, held_out)
        fold = {"held_out": group, "train_size": len(train)}
        fold.update(_fold_metrics(held_out, predictions))
        folds.append(fold)

    aggregate_predictions = score(items, items)
    aggregate = _fold_metrics(items, aggregate_predictions)
    # A fold holding one case yields a correctness of exactly 0.0 or 1.0, so a
    # grouping with singleton members manufactures a range of 1.0 whatever the
    # model does.  The first real measurement produced exactly that on
    # source_family, where most families held a single document.  Spread is
    # therefore computed over folds large enough for the number to mean
    # something, and the excluded folds are reported rather than dropped.
    sized = [f for f in folds if f.get("held_out_size", f.get("size", 0)) >= min_fold_size]
    undersized = [f["held_out"] for f in folds if f not in sized]
    correctness = [fold["correctness"] for fold in sized]
    spread = _spread(correctness)
    worst = min(folds, key=lambda f: f["correctness"]) if folds else None
    best = max(folds, key=lambda f: f["correctness"]) if folds else None
    return {
        "group_key": group_key,
        "groups": groups,
        "folds": folds,
        "aggregate": aggregate,
        "spread": spread,
        "folds_scored_for_spread": len(sized),
        "folds_excluded_as_undersized": undersized,
        "min_fold_size": min_fold_size,
        "worst_fold": worst["held_out"] if worst else None,
        "best_fold": best["held_out"] if best else None,
        "max_spread": max_spread,
        "stable": bool(spread["range"] is not None and
                       spread["range"] <= max_spread),
        "wrong_admissions_total": sum(fold["wrong_admissions"] for fold in folds),
        "calibration_version": CALIBRATION_VERSION,
    }


def leave_one_campaign_out(cases: Iterable[CalibrationCase], **kwargs: Any
                           ) -> dict[str, Any]:
    """Hold out a whole campaign at a time."""
    return grouped_validation(cases, group_key="campaign", **kwargs)


def leave_one_language_group_out(cases: Iterable[CalibrationCase], **kwargs: Any
                                 ) -> dict[str, Any]:
    """Hold out a whole language family at a time."""
    return grouped_validation(cases, group_key="language_group", **kwargs)


def leave_one_document_genre_out(cases: Iterable[CalibrationCase], **kwargs: Any
                                 ) -> dict[str, Any]:
    """Hold out a whole document genre at a time."""
    return grouped_validation(cases, group_key="document_genre", **kwargs)


def leave_one_source_family_out(cases: Iterable[CalibrationCase], **kwargs: Any
                                ) -> dict[str, Any]:
    """Hold out a whole source family at a time."""
    return grouped_validation(cases, group_key="source_family", **kwargs)


def leave_one_layout_type_out(cases: Iterable[CalibrationCase], **kwargs: Any
                              ) -> dict[str, Any]:
    """Hold out a whole layout type at a time."""
    return grouped_validation(cases, group_key="layout_type", **kwargs)


def all_grouped_validations(cases: Iterable[CalibrationCase], **kwargs: Any
                            ) -> dict[str, Any]:
    """Every grouping, with the worst spread across all of them surfaced."""
    items = list(cases)
    reports = {key: grouped_validation(items, group_key=key, **kwargs)
               for key in GROUPING_KEYS}
    ranges = [report["spread"]["range"] for report in reports.values()
              if report["spread"]["range"] is not None]
    return {
        "by_grouping": reports,
        "worst_spread": round(max(ranges), 4) if ranges else None,
        "worst_grouping": (max(reports, key=lambda k: reports[k]["spread"]["range"] or 0.0)
                           if ranges else None),
        "stable_everywhere": all(report["stable"] for report in reports.values()),
        "calibration_version": CALIBRATION_VERSION,
    }


# ---------------------------------------------------------------------------
# Section 7.3 — feature stability
# ---------------------------------------------------------------------------

DEFAULT_THRESHOLDS: Mapping[str, float] = {
    # share of a feature's active cases that may sit in one campaign before
    # the feature is reading campaign identity rather than semantics
    "campaign_concentration": 0.9,
    # |Pearson r| against dossier order
    "dossier_order_correlation": 0.6,
    # share of active cases that must share a source-specific token
    "vocabulary_coverage": 0.9,
    # share of inactive cases that may also carry it
    "vocabulary_leak": 0.2,
    # correctness lift over the population baseline that counts as "improves"
    "lift": 0.05,
}

STABILITY_FLAGS: tuple[str, ...] = (
    "improves_only_v5_2_origin",
    "correlates_with_campaign",
    "correlates_with_dossier_order",
    "correlates_with_source_vocabulary",
    "clean_regression",
)


def _pearson(left: Sequence[float], right: Sequence[float]) -> float | None:
    if len(left) < 2 or len(left) != len(right):
        return None
    mean_left, mean_right = statistics.fmean(left), statistics.fmean(right)
    numerator = sum((a - mean_left) * (b - mean_right) for a, b in zip(left, right))
    left_var = sum((a - mean_left) ** 2 for a in left)
    right_var = sum((b - mean_right) ** 2 for b in right)
    if left_var <= 0 or right_var <= 0:
        return None
    return round(numerator / ((left_var * right_var) ** 0.5), 4)


def _accuracy(cases: Sequence[CalibrationCase]) -> float | None:
    if not cases:
        return None
    return round(sum(1 for case in cases if case.correct) / len(cases), 4)


def feature_stability_report(cases: Iterable[CalibrationCase], *,
                             feature_names: Sequence[str] | None = None,
                             thresholds: Mapping[str, float] | None = None
                             ) -> dict[str, Any]:
    """Flag features that look useful without carrying any semantics.

    Five failure shapes, all of them present somewhere in the V5.3 weight
    vector, all of them invisible to a random split:

    ``improves_only_v5_2_origin``
        the feature lifts correctness on examples inherited from V5.2 and
        does nothing (or worse) elsewhere — it memorized a fixed corpus;
    ``correlates_with_campaign``
        the feature fires almost exclusively inside one campaign, so it is a
        campaign detector wearing a semantic name;
    ``correlates_with_dossier_order``
        the feature tracks the position of the case in the dossier, which is
        an artefact of how the corpus was assembled;
    ``correlates_with_source_vocabulary``
        the feature fires on cases sharing a token specific to one source
        family — a house-style detector;
    ``clean_regression``
        the feature helps on development evidence and hurts on clean held-out
        evidence, which is the V5.3 0.8494 -> 0.4348 collapse in miniature.

    Returns a JSON-serializable dict; the caller persists it as
    ``feature_stability_report.json``.
    """
    items = list(cases)
    limits = dict(DEFAULT_THRESHOLDS)
    limits.update(thresholds or {})
    names = list(feature_names) if feature_names is not None else sorted(
        {name for case in items for name in case.features})

    baseline = _accuracy(items)
    development = [case for case in items if case.evidence_class == "DEVELOPMENT"]
    clean = [case for case in items if case.evidence_class == "CLEAN_HELD_OUT"]
    development_baseline = _accuracy(development)
    clean_baseline = _accuracy(clean)

    report_features: dict[str, Any] = {}
    flagged: list[str] = []
    for name in names:
        active = [case for case in items if float(case.features.get(name, 0.0)) > 0.0]
        inactive = [case for case in items if float(case.features.get(name, 0.0)) <= 0.0]
        flags = {flag: False for flag in STABILITY_FLAGS}
        evidence: dict[str, Any] = {
            "active": len(active), "inactive": len(inactive),
            "active_accuracy": _accuracy(active),
            "inactive_accuracy": _accuracy(inactive),
            "population_accuracy": baseline,
        }

        # 1 — improves only V5.2-origin examples
        origin_active = [case for case in active if case.origin_milestone == "V5_2"]
        other_active = [case for case in active if case.origin_milestone != "V5_2"]
        origin_accuracy = _accuracy(origin_active)
        other_accuracy = _accuracy(other_active)
        evidence["v5_2_origin_active"] = len(origin_active)
        evidence["v5_2_origin_accuracy"] = origin_accuracy
        evidence["other_origin_accuracy"] = other_accuracy
        if (origin_accuracy is not None and other_accuracy is not None
                and baseline is not None
                and origin_accuracy - baseline >= limits["lift"]
                and other_accuracy - baseline < limits["lift"]):
            flags["improves_only_v5_2_origin"] = True

        # 2 — campaign identity
        campaigns: dict[str, int] = {}
        for case in active:
            campaigns[case.campaign] = campaigns.get(case.campaign, 0) + 1
        concentration = (max(campaigns.values()) / len(active)) if active else 0.0
        evidence["campaign_counts"] = campaigns
        evidence["campaign_concentration"] = round(concentration, 4)
        population_campaigns = {case.campaign for case in items}
        if (len(population_campaigns) > 1 and active
                and concentration >= limits["campaign_concentration"]):
            flags["correlates_with_campaign"] = True

        # 3 — dossier ordering
        order = _pearson([float(case.dossier_index) for case in items],
                         [float(case.features.get(name, 0.0)) for case in items])
        evidence["dossier_order_correlation"] = order
        if order is not None and abs(order) >= limits["dossier_order_correlation"]:
            flags["correlates_with_dossier_order"] = True

        # 4 — source-specific vocabulary
        marker = _vocabulary_marker(active, inactive, limits)
        evidence["source_vocabulary_marker"] = marker
        if marker is not None:
            flags["correlates_with_source_vocabulary"] = True

        # 5 — clean regression
        development_active = [case for case in active
                              if case.evidence_class == "DEVELOPMENT"]
        clean_active = [case for case in active
                        if case.evidence_class == "CLEAN_HELD_OUT"]
        development_accuracy = _accuracy(development_active)
        clean_accuracy = _accuracy(clean_active)
        evidence["development_accuracy"] = development_accuracy
        evidence["clean_accuracy"] = clean_accuracy
        evidence["development_baseline"] = development_baseline
        evidence["clean_baseline"] = clean_baseline
        if (development_accuracy is not None and clean_accuracy is not None
                and development_baseline is not None and clean_baseline is not None
                and development_accuracy - development_baseline >= limits["lift"]
                and clean_accuracy - clean_baseline <= -limits["lift"]):
            flags["clean_regression"] = True

        report_features[name] = {"flags": flags, "evidence": evidence}
        if any(flags.values()):
            flagged.append(name)

    return {
        "generated_time": now_utc(),
        "calibration_version": CALIBRATION_VERSION,
        "invariant_version": invariants.INVARIANT_VERSION,
        "cases": len(items),
        "development_cases": len(development),
        "clean_cases": len(clean),
        "thresholds": limits,
        "flag_vocabulary": list(STABILITY_FLAGS),
        "features": report_features,
        "flagged": sorted(flagged),
        "unstable_feature_count": len(flagged),
    }


def _vocabulary_marker(active: Sequence[CalibrationCase],
                       inactive: Sequence[CalibrationCase],
                       limits: Mapping[str, float]) -> dict[str, Any] | None:
    """A token that a feature's active set shares and other cases do not.

    Needs both an active and an inactive set to contrast, and at least two
    source families in the population; without either, "source-specific" is
    not a statement the data can support.
    """
    if not active or not inactive:
        return None
    if len({case.source_family for case in list(active) + list(inactive)}) < 2:
        return None
    counts: dict[str, int] = {}
    for case in active:
        for token in set(case.vocabulary):
            counts[token] = counts.get(token, 0) + 1
    for token, count in sorted(counts.items(), key=lambda item: (-item[1], item[0])):
        coverage = count / len(active)
        if coverage < limits["vocabulary_coverage"]:
            break
        leak = (sum(1 for case in inactive if token in case.vocabulary) /
                len(inactive)) if inactive else 0.0
        if leak > limits["vocabulary_leak"]:
            continue
        families = {case.source_family for case in active if token in case.vocabulary}
        if len(families) == 1:
            return {"token": token, "coverage": round(coverage, 4),
                    "leak": round(leak, 4), "source_family": sorted(families)[0]}
    return None


# ---------------------------------------------------------------------------
# Section 7.4 — admissibility metrics
# ---------------------------------------------------------------------------

# A system may not buy zero wrong admissions by rejecting everything hard.
MIN_RECOVERABLE_RECALL = 0.60
MAX_OVER_REJECTION_RATE = 0.35


def invariant_failure_counts(cases: Iterable[CalibrationCase]
                             ) -> dict[str, dict[str, int]]:
    """VIOLATED / UNRESOLVED counts per invariant over a case population."""
    counts = {name: {VIOLATED: 0, UNRESOLVED: 0} for name in INVARIANT_NAMES}
    for case in cases:
        for name, state in case.invariant_states.items():
            if name in counts and state in counts[name]:
                counts[name][state] += 1
    return counts


def admissibility_metrics(cases: Iterable[CalibrationCase], *,
                          min_recoverable_recall: float = MIN_RECOVERABLE_RECALL,
                          max_over_rejection_rate: float = MAX_OVER_REJECTION_RATE
                          ) -> dict[str, Any]:
    """Accepted precision, recall, over-rejection, quarantine and failures.

    A wrong accepted candidate is the most serious error this system can make:
    it is the one that reaches a report and is published as something the
    document says.  ``wrong_admissions`` therefore gates the pass.

    It is not the only gate, and this is deliberate and measurable.  Zero
    wrong admissions obtained by rejecting everything difficult is **not** a
    pass: it is the V5.1 failure, where 3891 rejections drew 48 wrong
    judgements out of 90 sampled and every Surface-1 error was a rejection.
    ``degenerate_rejection`` is True whenever the population admits nothing,
    recovers fewer than ``min_recoverable_recall`` of the assertions reviewers
    judged recoverable, or over-rejects above ``max_over_rejection_rate`` —
    and ``passes`` is False whenever it is True.
    """
    items = list(cases)
    total = len(items)
    accepted = [case for case in items
                if case.predicted_state == "ACCEPTED_CANDIDATE"]
    correct_accepted = [case for case in accepted
                        if case.reviewer_state == "ACCEPTED_CANDIDATE"]
    wrong_admissions = [case for case in accepted
                        if case.reviewer_state != "ACCEPTED_CANDIDATE"]
    recoverable = [case for case in items
                   if case.reviewer_state in RECOVERABLE_STATES]
    recovered = [case for case in recoverable if case.predicted_state != "REJECTED"]
    over_rejected = [case for case in items
                     if case.predicted_state == "REJECTED"
                     and case.reviewer_state != "REJECTED"]
    quarantined = [case for case in items if case.predicted_state == "QUARANTINED"]

    accepted_precision = (round(len(correct_accepted) / len(accepted), 4)
                          if accepted else None)
    recall = (round(len(recovered) / len(recoverable), 4) if recoverable else None)
    over_rejection_rate = round(len(over_rejected) / total, 4) if total else 0.0
    quarantine_rate = round(len(quarantined) / total, 4) if total else 0.0

    degenerate_reasons: list[str] = []
    if total and not accepted:
        degenerate_reasons.append("NO_CANDIDATE_ADMITTED")
    if recall is not None and recall < min_recoverable_recall:
        degenerate_reasons.append("RECOVERABLE_ASSERTIONS_DISCARDED")
    if over_rejection_rate > max_over_rejection_rate:
        degenerate_reasons.append("OVER_REJECTION_ABOVE_LIMIT")

    return {
        "scored": total,
        "accepted": len(accepted),
        "correct_accepted": len(correct_accepted),
        "wrong_admissions": len(wrong_admissions),
        "wrong_admission_candidate_ids": [case.candidate_id
                                          for case in wrong_admissions],
        "accepted_precision": accepted_precision,
        "recoverable_assertions": len(recoverable),
        "recoverable_assertion_recall": recall,
        "over_rejections": len(over_rejected),
        "over_rejection_rate": over_rejection_rate,
        "quarantined": len(quarantined),
        "quarantine_rate": quarantine_rate,
        "invariant_failure_distribution": invariant_failure_counts(items),
        "correctness": _accuracy(items),
        "thresholds": {"min_recoverable_recall": min_recoverable_recall,
                       "max_over_rejection_rate": max_over_rejection_rate},
        "degenerate_rejection": bool(degenerate_reasons),
        "degenerate_reasons": degenerate_reasons,
        "passes": bool(total and not wrong_admissions and not degenerate_reasons),
        "calibration_version": CALIBRATION_VERSION,
    }
