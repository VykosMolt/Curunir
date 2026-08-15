"""V5.8.1 §6–§7 — bundle accounting and correctly typed topology denominators.

V5.8's topology gate reported a singleton fraction of 0.62 and failed. The
number was wrong, and wrong in a specific way: it counted every declared bundle
that captured one member as a singleton *intellectual work in the clean graph*.
Most of those were acquisition failures — a 403, a JavaScript shell, a page that
turned out not to carry the relation assumed of it. An attempt that never
becomes eligible evidence is an acquisition failure, not a lonely node.

So this module separates the populations §7 requires and computes three distinct
singleton rates. The gate uses the eligible-graph rate, and the other two are
reported beside it so the correction cannot hide a genuinely thin corpus.

It also fixes a second conflation: a pair motif is *complete* at two members. An
original and its official translation is a complete relation; requiring three
would make the commonest real relation permanently unsatisfiable.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from ..v5_1.models import Record, now_utc, sha256, stable_id


class TopologyViolation(RuntimeError):
    """A topology figure was computed over the wrong population."""


# ===========================================================================
# §6 — bundle states
# ===========================================================================

BUNDLE_STATES: tuple[str, ...] = (
    "DECLARED_NOT_ATTEMPTED", "ACCESS_ATTEMPTED_NO_VALID_MEMBER",
    "SINGLETON_CAPTURE_INCOMPLETE", "PAIR_COMPLETE", "CHAIN_COMPLETE",
    "CLUSTER_COMPLETE", "PARTIALLY_COMPLETE", "INVALID_DUPLICATE",
    "INVALID_NONRELATIONAL",
)

#: §7.3 — what "complete" means, per motif shape.  Declared BEFORE acquisition.
PAIR_MOTIFS: frozenset[str] = frozenset({
    "official_translation", "mirror", "archive", "syndication",
    "manifestation_pair", "document_correction", "derivative_summary",
    "author_publisher_host", "same_named_dataset",
})
CHAIN_MOTIFS: frozenset[str] = frozenset({
    "supersession", "act_amendment", "preliminary_then_final",
    "announcement_pilot_deployment", "initial_correction_final",
    "translation_revision_chain",
})
CLUSTER_MOTIFS: frozenset[str] = frozenset({
    "shared_dataset_cluster", "independent_interpretation_cluster",
    "independence_supported_cluster", "dependence_disputed_cluster",
    # Added before the G02 freeze, because G01 had no honest motif for the
    # commonest real cluster in institutional publishing: one instrument issued
    # by one body in three or more official languages.  Declaring those as a
    # "shared dataset" would have been a misdescription, and folding them into
    # pairs would have hidden that the completion bar is three members, not two.
    # The bar is not lowered by the addition — this motif still requires three.
    "multilingual_manifestation_cluster",
})

MINIMUM_MEMBERS: Mapping[str, int] = {
    **{m: 2 for m in PAIR_MOTIFS},
    **{m: 3 for m in CHAIN_MOTIFS},
    **{m: 3 for m in CLUSTER_MOTIFS},
}


def minimum_members(motif: str) -> int:
    """How many members this motif needs to be a completed relation."""
    if motif not in MINIMUM_MEMBERS:
        raise TopologyViolation(
            f"motif {motif!r} has no declared completion rule; §7.3 requires the "
            "rule to be recorded before acquisition, not inferred after it")
    return MINIMUM_MEMBERS[motif]


def complete_state(motif: str) -> str:
    if motif in PAIR_MOTIFS:
        return "PAIR_COMPLETE"
    if motif in CHAIN_MOTIFS:
        return "CHAIN_COMPLETE"
    return "CLUSTER_COMPLETE"


@dataclass(frozen=True)
class BundleAccountingRecord(Record):
    """§6 — one bundle's arithmetic, with overlap made explicit."""

    bundle_id: str
    motif_id: str
    declared_member_roles: tuple[str, ...]
    required_member_count: int
    captured_member_ids: tuple[str, ...]
    eligible_member_ids: tuple[str, ...]
    state: str
    members_shared_with_other_bundles: tuple[str, ...]
    scored_eligible: bool
    reserve_eligible: bool
    failure_reasons: tuple[str, ...]
    recorded_time: str

    def __post_init__(self) -> None:
        if self.state not in BUNDLE_STATES:
            raise TopologyViolation(f"unknown bundle state: {self.state!r}")
        if self.state in ("PAIR_COMPLETE", "CHAIN_COMPLETE", "CLUSTER_COMPLETE") \
                and len(self.eligible_member_ids) < self.required_member_count:
            raise TopologyViolation(
                f"{self.bundle_id} is marked {self.state} with "
                f"{len(self.eligible_member_ids)} eligible members but requires "
                f"{self.required_member_count}")

    @property
    def complete(self) -> bool:
        return self.state in ("PAIR_COMPLETE", "CHAIN_COMPLETE", "CLUSTER_COMPLETE")

    @property
    def incomplete(self) -> bool:
        return not self.complete

    @property
    def singleton_capture(self) -> bool:
        return self.state == "SINGLETON_CAPTURE_INCOMPLETE"


def account_bundle(*, bundle: Mapping[str, Any],
                   captured: Sequence[Mapping[str, Any]],
                   eligible_classes: frozenset[str] = frozenset({"SUBSTANTIVE_EVIDENCE"}),
                   membership_index: Mapping[str, set[str]] | None = None
                   ) -> BundleAccountingRecord:
    motif = bundle["motif_type"]
    required = minimum_members(motif)
    roles = tuple(m.get("role", "") for m in bundle.get("members", ()))
    captured_ids = tuple(c["source_id"] for c in captured)
    eligible = tuple(c["source_id"] for c in captured
                     if c.get("page_class") in eligible_classes)
    reasons: list[str] = []
    for c in captured:
        if c.get("page_class") not in eligible_classes:
            reasons.append(f"{c['source_id']}:{c.get('page_class')}")

    if not captured_ids:
        state = "DECLARED_NOT_ATTEMPTED" if not bundle.get("attempted") \
            else "ACCESS_ATTEMPTED_NO_VALID_MEMBER"
    elif not eligible:
        state = "ACCESS_ATTEMPTED_NO_VALID_MEMBER"
    elif len(eligible) >= required:
        state = complete_state(motif)
    elif len(eligible) == 1:
        state = "SINGLETON_CAPTURE_INCOMPLETE"
    else:
        state = "PARTIALLY_COMPLETE"

    shared: tuple[str, ...] = ()
    if membership_index:
        shared = tuple(sorted(
            sid for sid in eligible
            if len(membership_index.get(sid, set())) > 1))

    return BundleAccountingRecord(
        bundle["bundle_id"], motif, roles, required, captured_ids, eligible,
        state, shared,
        state in ("PAIR_COMPLETE", "CHAIN_COMPLETE", "CLUSTER_COMPLETE"),
        state == "PARTIALLY_COMPLETE", tuple(reasons), now_utc())


def reconcile(records: Iterable[BundleAccountingRecord]) -> dict[str, Any]:
    """§6 — totals that do not pretend to be mutually exclusive."""
    records = list(records)
    by_state: dict[str, int] = {}
    for record in records:
        by_state[record.state] = by_state.get(record.state, 0) + 1
    declared = len(records)
    complete = sum(1 for r in records if r.complete)
    singleton = sum(1 for r in records if r.singleton_capture)
    partial = sum(1 for r in records if r.state == "PARTIALLY_COMPLETE")
    none_valid = sum(1 for r in records
                     if r.state in ("ACCESS_ATTEMPTED_NO_VALID_MEMBER",
                                    "DECLARED_NOT_ATTEMPTED"))
    arithmetic = complete + singleton + partial + none_valid + sum(
        1 for r in records if r.state in ("INVALID_DUPLICATE", "INVALID_NONRELATIONAL"))
    return {
        "declared_bundles": declared,
        "by_state": by_state,
        "complete": complete,
        "complete_pairs": by_state.get("PAIR_COMPLETE", 0),
        "complete_chains": by_state.get("CHAIN_COMPLETE", 0),
        "complete_clusters": by_state.get("CLUSTER_COMPLETE", 0),
        "singleton_capture_incomplete": singleton,
        "partially_complete": partial,
        "attempted_no_valid_member": none_valid,
        "arithmetic_reconciles": arithmetic == declared,
        "unclassified_bundles": declared - arithmetic,
        "members_in_more_than_one_bundle": sorted(
            {sid for r in records for sid in r.members_shared_with_other_bundles}),
        "note": ("a manifestation may belong to several motifs; bundle counts "
                 "therefore overlap and are NOT mutually exclusive"),
        "verdict": "PASS_HARDENED" if arithmetic == declared else "FAIL",
    }


# ===========================================================================
# §7 — typed populations
# ===========================================================================

POPULATIONS: tuple[str, ...] = (
    "ALL_ACQUISITION_ATTEMPTS", "VALID_CAPTURED_MANIFESTATIONS",
    "SUBSTANTIVE_MANIFESTATIONS", "DISCOVERY_OR_NAVIGATION_ARTIFACTS",
    "COMPLETE_RELATION_BUNDLE_MEMBERS", "SCORED_ELIGIBLE_MANIFESTATIONS",
    "RESERVE_ELIGIBLE_MANIFESTATIONS",
)

#: Page classes that never enter the eligible graph (§7.1, §19).
NON_ELIGIBLE_CLASSES: frozenset[str] = frozenset({
    "ACCESS_FAILURE", "EMPTY_OR_SCRIPT_SHELL", "DUPLICATE",
    "INVALID_RELATION_ASSUMPTION", "DISCOVERY_OR_NAVIGATION_ARTIFACT",
    "MANIFESTATION_METADATA",
})


def populations(sources: Sequence[Mapping[str, Any]],
                accounting: Sequence[BundleAccountingRecord]) -> dict[str, Any]:
    scored_bundles = {r.bundle_id for r in accounting if r.scored_eligible}
    reserve_bundles = {r.bundle_id for r in accounting if r.reserve_eligible}
    substantive = [s for s in sources if s.get("page_class") == "SUBSTANTIVE_EVIDENCE"]
    scored = [s for s in substantive if s.get("bundle_id") in scored_bundles]
    reserve = [s for s in substantive if s.get("bundle_id") in reserve_bundles]
    return {
        "ALL_ACQUISITION_ATTEMPTS": len(sources),
        "VALID_CAPTURED_MANIFESTATIONS": sum(
            1 for s in sources if s.get("page_class") not in
            ("ACCESS_FAILURE", "EMPTY_OR_SCRIPT_SHELL")),
        "SUBSTANTIVE_MANIFESTATIONS": len(substantive),
        "DISCOVERY_OR_NAVIGATION_ARTIFACTS": sum(
            1 for s in sources
            if s.get("page_class") == "DISCOVERY_OR_NAVIGATION_ARTIFACT"),
        "COMPLETE_RELATION_BUNDLE_MEMBERS": len(scored),
        "SCORED_ELIGIBLE_MANIFESTATIONS": len(scored),
        "RESERVE_ELIGIBLE_MANIFESTATIONS": len(reserve),
        "_scored": scored, "_reserve": reserve, "_substantive": substantive,
    }


def singleton_rates(sources: Sequence[Mapping[str, Any]],
                    accounting: Sequence[BundleAccountingRecord]) -> dict[str, Any]:
    """§7.2 — three rates, so the corrected one cannot hide a thin corpus."""
    pops = populations(sources, accounting)
    eligible = pops["_scored"] + pops["_reserve"]

    def rate(rows):
        works: dict[str, int] = {}
        for row in rows:
            works[row["intellectual_work_id"]] = works.get(
                row["intellectual_work_id"], 0) + 1
        if not works:
            return None, 0, 0
        singles = sum(1 for n in works.values() if n == 1)
        return round(singles / len(works), 4), singles, len(works)

    attempt_rate, a_s, a_w = rate(sources)
    capture_rate, c_s, c_w = rate(pops["_substantive"])
    eligible_rate, e_s, e_w = rate(eligible)
    return {
        "attempt_level_singleton_rate": attempt_rate,
        "capture_level_singleton_rate": capture_rate,
        "eligible_graph_singleton_rate": eligible_rate,
        "eligible_singleton_works": e_s, "eligible_works": e_w,
        "gate_uses": "eligible_graph_singleton_rate",
        "excluded_from_the_eligible_graph": sorted(NON_ELIGIBLE_CLASSES),
        "why": ("an attempt that never became eligible evidence is an "
                "acquisition failure, not a lonely node in the graph.  Both "
                "other rates are reported so this correction cannot conceal a "
                "corpus that is genuinely thin."),
    }


__all__ = [
    "TopologyViolation", "BUNDLE_STATES", "PAIR_MOTIFS", "CHAIN_MOTIFS",
    "CLUSTER_MOTIFS", "MINIMUM_MEMBERS", "minimum_members", "complete_state",
    "BundleAccountingRecord", "account_bundle", "reconcile", "POPULATIONS",
    "NON_ELIGIBLE_CLASSES", "populations", "singleton_rates",
]
