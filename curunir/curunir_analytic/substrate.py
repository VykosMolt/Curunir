"""Machinery every analytical engine shares: the context, typed transitions,
reverse dependencies, and the model-proposal acceptance boundary.

`record_candidate` / `resolve_candidate` are the one path from model output to
analytical state: a candidate stays PROPOSED until a human act accepts it.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Iterable, Mapping

from argus.source_intelligence.models import digest_id
from curunir_operational.access import Marking, marking_from_record
from curunir_operational.contracts import AnalystAction, AnalyticalProposal

from .contracts import AnalyticalTransition
from .store import ANALYTIC_ID_FIELDS, AnalyticStore


@dataclass
class AnalyticContext:
    """One store, one actor, one clock, shared by every engine."""
    store: AnalyticStore
    actor: str
    marking: Marking
    now_fn: Callable[[], str]


def record_transition(ctx: AnalyticContext, *, subject_kind: str, subject_id: str,
                      transition_type: str, detail: str, caused_by: str,
                      evidence_refs: tuple[str, ...] = (),
                      from_status: str = "", to_status: str = "") -> dict[str, Any] | None:
    """Append one typed transition, once per cause, so re-running propagation
    after a crash completes history instead of duplicating it.
    """
    transition_id = digest_id("antrans", subject_kind, subject_id,
                              transition_type, caused_by)
    if ctx.store.has_transition(transition_id):
        return None
    now = ctx.now_fn()
    record = AnalyticalTransition(
        transition_id=transition_id, subject_kind=subject_kind, subject_id=subject_id,
        transition_type=transition_type, detail=detail[:500], caused_by=caused_by,
        evidence_refs=evidence_refs, from_status=from_status, to_status=to_status,
        recorded_time=now, marking=ctx.marking)
    event = ctx.store.append("ANALYTIC_TRANSITION_RECORDED", record,
                             recorded_time=now, actor=ctx.actor)
    return event["record"]


def ensure_transition(ctx: AnalyticContext, *, subject_kind: str, subject_id: str,
                      transition_type: str, detail: str, caused_by: str,
                      evidence_refs: tuple[str, ...] = (),
                      to_status: str = "") -> dict[str, Any] | None:
    """Record a transition only if the subject has none of this type yet.

    A version and its CREATED transition are separate log events, so a crash
    between them leaves a gap this fills without adding a second one.
    """
    if any(t["transition_type"] == transition_type
           for t in ctx.store.transitions_for(subject_id)):
        return None
    return record_transition(ctx, subject_kind=subject_kind, subject_id=subject_id,
                             transition_type=transition_type, detail=detail,
                             caused_by=caused_by, evidence_refs=evidence_refs,
                             to_status=to_status)


def append_version(ctx: AnalyticContext, record) -> dict[str, Any]:
    """Append one analytical object version."""
    event_type, _ = ANALYTIC_ID_FIELDS[record.RECORD_TYPE]
    now = record.recorded_time
    event = ctx.store.append(event_type, record, recorded_time=now, actor=ctx.actor)
    return event["record"]


# Reverse dependencies


class DependencyIndex:
    """Which analytical objects depend on which semantic state: semantic identity
    -> (kind, id), built by replaying the current analytical records.
    """

    def __init__(self, store: AnalyticStore):
        self.by_claim: dict[str, set[tuple[str, str]]] = {}
        self.by_object: dict[str, set[tuple[str, str]]] = {}
        self.by_relationship: dict[str, set[tuple[str, str]]] = {}
        self.by_activity: dict[str, set[tuple[str, str]]] = {}
        self.by_assumption: dict[str, set[tuple[str, str]]] = {}
        self.by_analytic: dict[tuple[str, str], set[tuple[str, str]]] = {}
        self._build(store)

    def _add(self, index: dict, key: str, ref: tuple[str, str]) -> None:
        if key:
            index.setdefault(key, set()).add(ref)

    def _add_basis(self, basis: Mapping[str, Any], ref: tuple[str, str]) -> None:
        for claim_id in tuple(basis.get("supporting_claim_ids", ())) \
                + tuple(basis.get("contradicting_claim_ids", ())):
            self._add(self.by_claim, claim_id, ref)

    def _build(self, store: AnalyticStore) -> None:
        for theme in store.current_themes().values():
            ref = ("analytic_theme", theme["theme_id"])
            self._add_basis(theme["basis"], ref)
            for object_id in theme["entity_ids"]:
                self._add(self.by_object, object_id, ref)
            for activity_id in theme["event_ids"]:
                self._add(self.by_activity, activity_id, ref)
            for relationship_id in theme["relation_ids"]:
                self._add(self.by_relationship, relationship_id, ref)
            if theme["parent_theme_id"]:
                self._add(self.by_analytic,
                          ("analytic_theme", theme["parent_theme_id"]), ref)
        for narrative in store.current_narratives().values():
            ref = ("analytic_narrative", narrative["narrative_id"])
            self._add_basis(narrative["basis"], ref)
            for object_id in narrative["entity_ids"]:
                self._add(self.by_object, object_id, ref)
        for variant in store.current_analytics("narrative_variant").values():
            ref = ("analytic_narrative", variant["narrative_id"])
            for claim_id in variant["claim_ids"]:
                self._add(self.by_claim, claim_id, ref)
        for narrative in store.current_narratives().values():
            ref = ("analytic_narrative", narrative["narrative_id"])
            for counter_id in narrative["counter_narrative_ids"]:
                self._add(self.by_analytic, ("analytic_narrative", counter_id), ref)
        for assessment in store.current_stakeholder_assessments().values():
            ref = ("stakeholder_assessment", assessment["assessment_id"])
            self._add_basis(assessment["basis"], ref)
            self._add(self.by_object, assessment["entity_object_id"], ref)
            for position in assessment["positions"]:
                for claim_id in position["claim_ids"]:
                    self._add(self.by_claim, claim_id, ref)
                for relationship_id in position["relationship_ids"]:
                    self._add(self.by_relationship, relationship_id, ref)
            for influence_id in assessment["influence_ids"]:
                self._add(self.by_analytic,
                          ("influence_assertion", influence_id), ref)
            if assessment["context_kind"] == "THEME":
                self._add(self.by_analytic,
                          ("analytic_theme", assessment["context_id"]), ref)
            elif assessment["context_kind"] == "OBJECTIVE":
                self._add(self.by_analytic,
                          ("mission_objective", assessment["context_id"]), ref)
            elif assessment["context_kind"] == "EVENT":
                self._add(self.by_activity, assessment["context_id"], ref)
            # MISSION and ISSUE contexts are free labels, not addressable state.
        for influence in store.current_influence_assertions().values():
            ref = ("influence_assertion", influence["influence_id"])
            for claim_id in influence["claim_ids"]:
                self._add(self.by_claim, claim_id, ref)
            for relationship_id in influence["relationship_ids"]:
                self._add(self.by_relationship, relationship_id, ref)
            self._add(self.by_object, influence["source_object_id"], ref)
            self._add(self.by_object, influence["target_object_id"], ref)
        for assumption in store.current_assumptions().values():
            ref = ("analytic_assumption", assumption["assumption_id"])
            for claim_id in tuple(assumption["supporting_claim_ids"]) \
                    + tuple(assumption["contradicting_claim_ids"]):
                self._add(self.by_claim, claim_id, ref)
            # The assumption names the objectives resting on it.
            for objective_id in assumption["objective_ids"]:
                self._add(self.by_assumption, assumption["assumption_id"],
                          ("mission_objective", objective_id))
        for path in store.current_impact_paths().values():
            ref = ("impact_path", path["path_id"])
            for edge in path["edges"]:
                for basis_id in edge["basis_ids"]:
                    # A basis id may be a claim, relationship or activity;
                    # lookups are exact, so index all three.
                    self._add(self.by_claim, basis_id, ref)
                    self._add(self.by_relationship, basis_id, ref)
                    self._add(self.by_activity, basis_id, ref)
                for kind, endpoint in ((edge["from_kind"], edge["from_id"]),
                                       (edge["to_kind"], edge["to_id"])):
                    if kind == "object":
                        self._add(self.by_object, endpoint, ref)
                    elif kind == "activity":
                        self._add(self.by_activity, endpoint, ref)
                    elif kind == "claim":
                        self._add(self.by_claim, endpoint, ref)
                    elif kind == "relationship":
                        self._add(self.by_relationship, endpoint, ref)
                    elif kind in ANALYTIC_ID_FIELDS:
                        self._add(self.by_analytic, (kind, endpoint), ref)
            for assumption_id in path["assumption_ids"]:
                self._add(self.by_assumption, assumption_id, ref)
            self._add(self.by_analytic,
                      ("mission_objective", path["objective_id"]), ref)
        for objective in store.current_objectives().values():
            ref = ("mission_objective", objective["objective_id"])
            for kind, dep in objective["depends_on"]:
                if kind == "object":
                    self._add(self.by_object, dep, ref)
                elif kind == "relationship":
                    self._add(self.by_relationship, dep, ref)
                elif kind == "activity":
                    self._add(self.by_activity, dep, ref)
                elif kind == "claim":
                    self._add(self.by_claim, dep, ref)
                elif kind in ANALYTIC_ID_FIELDS:
                    self._add(self.by_analytic, (kind, dep), ref)
                else:
                    # Invisible to propagation is worse than raising.
                    raise ValueError(f"objective {objective['objective_id'][:24]} "
                                     f"declares a dependency of unknown kind "
                                     f"{kind!r}; it cannot be tracked")
            for assumption_id in objective["assumption_ids"]:
                self._add(self.by_assumption, assumption_id, ref)
        for option in store.current_analytics("response_option").values():
            ref = ("response_option", option["option_id"])
            for claim_id in option["claim_ids"]:
                self._add(self.by_claim, claim_id, ref)
            self._add(self.by_analytic, ("impact_path", option["path_id"]), ref)
            self._add(self.by_analytic,
                      ("mission_objective", option["objective_id"]), ref)
        for episode in store.current_analytics("historical_episode").values():
            ref = ("historical_episode", episode["episode_id"])
            for claim_id in tuple(episode["claim_ids"]) + tuple(episode["outcome_claim_ids"]):
                self._add(self.by_claim, claim_id, ref)
            for actor_id in episode["actor_object_ids"]:
                self._add(self.by_object, actor_id, ref)
            for event_id in episode["event_ids"]:
                self._add(self.by_activity, event_id, ref)
        for analogue in store.current_analytics("historical_analogue").values():
            ref = ("historical_analogue", analogue["analogue_id"])
            self._add(self.by_analytic, ("historical_episode", analogue["episode_id"]), ref)
            if analogue["query_kind"] in ANALYTIC_ID_FIELDS:
                self._add(self.by_analytic,
                          (analogue["query_kind"], analogue["query_id"]), ref)
        for forecast in store.current_analytics("analytic_forecast").values():
            ref = ("analytic_forecast", forecast["forecast_id"])
            self._add_basis(forecast["basis"], ref)
            for kind, dep in forecast["proposition_refs"]:
                if kind == "object":
                    self._add(self.by_object, dep, ref)
                elif kind == "relationship":
                    self._add(self.by_relationship, dep, ref)
                elif kind == "activity":
                    self._add(self.by_activity, dep, ref)
                elif kind == "claim":
                    self._add(self.by_claim, dep, ref)
                elif kind == "hypothesis":
                    self._add(self.by_analytic, ("hypothesis", dep), ref)
                elif kind in ANALYTIC_ID_FIELDS:
                    self._add(self.by_analytic, (kind, dep), ref)
                else:
                    raise ValueError(
                        f"forecast {forecast['forecast_id'][:24]} references a "
                        f"proposition of unknown kind {kind!r}; it cannot be "
                        "tracked")
            for assumption_id in forecast["assumption_ids"]:
                self._add(self.by_assumption, assumption_id, ref)
            for indicator_id in forecast["indicator_ids"]:
                self._add(self.by_analytic,
                          ("forecast_indicator", indicator_id), ref)
        for indicator in store.current_analytics("forecast_indicator").values():
            # The indicator may name the forecast first, so link from here too.
            for forecast_id in indicator["forecast_ids"]:
                self._add(self.by_analytic,
                          ("forecast_indicator", indicator["indicator_id"]),
                          ("analytic_forecast", forecast_id))
        for warning in store.current_analytics("strategic_warning").values():
            ref = ("strategic_warning", warning["warning_id"])
            self._add(self.by_analytic,
                      ("analytic_forecast", warning["forecast_id"]), ref)
            self._add(self.by_analytic,
                      ("mission_objective", warning["objective_id"]), ref)
            for path_id in warning["impact_path_ids"]:
                self._add(self.by_analytic, ("impact_path", path_id), ref)

    def affected_by(self, *, claim_ids: Iterable[str] = (),
                    object_ids: Iterable[str] = (),
                    relationship_ids: Iterable[str] = (),
                    activity_ids: Iterable[str] = (),
                    assumption_ids: Iterable[str] = (),
                    transitive: bool = True) -> set[tuple[str, str]]:
        """Every analytical object resting on the given semantic state,
        following analytic-on-analytic dependence to a fixpoint."""
        affected: set[tuple[str, str]] = set()
        for index, keys in ((self.by_claim, claim_ids), (self.by_object, object_ids),
                            (self.by_relationship, relationship_ids),
                            (self.by_activity, activity_ids),
                            (self.by_assumption, assumption_ids)):
            for key in keys:
                affected |= index.get(key, set())
        if transitive:
            frontier = set(affected)
            while frontier:
                next_frontier: set[tuple[str, str]] = set()
                for ref in frontier:
                    next_frontier |= self.by_analytic.get(ref, set()) - affected
                affected |= next_frontier
                frontier = next_frontier
        return affected


# Model-proposal boundary


def _normalized_value(value: Any) -> Any:
    if isinstance(value, (list, tuple)):
        return tuple(sorted(str(v) for v in value))
    return value


def require_accepted_candidate(store: AnalyticStore, *, inference_id: str,
                               proposal_id: str, target_kind: str | None = None,
                               materialized: Mapping[str, Any] | None = None
                               ) -> Mapping[str, Any]:
    """Check that this materialization is the one a human accepted, and return the
    proposal: the target kind must match, shared fields must be equal, and the
    acceptance must not already be spent.
    """
    if not inference_id or not proposal_id:
        raise ValueError("model-provenance analytical state requires both the "
                         "inference record and the accepted candidate proposal")
    inference = next((r for r in store.records_of("inference")
                      if r["inference_id"] == inference_id), None)
    if inference is None:
        raise ValueError(f"inference record not found in the log: {inference_id}")
    proposal = store.latest_by_id("analytical_proposal", "proposal_id").get(proposal_id)
    if proposal is None:
        raise ValueError(f"candidate proposal not found in the log: {proposal_id}")
    if proposal["status"] != "ACCEPTED":
        raise ValueError(f"candidate proposal {proposal_id} is {proposal['status']}: "
                         "only a human-accepted candidate can become analytical state")
    if proposal["inference_id"] != inference_id:
        raise ValueError("the candidate proposal does not carry this inference record")
    # An acceptance for an operational proposal authorizes nothing here.
    if proposal.get("proposal_type") != "ANALYTICAL_OBJECT_CANDIDATE":
        raise ValueError(f"proposal {proposal_id[:24]} is a "
                         f"{proposal.get('proposal_type')!r}, not an analytical "
                         "object candidate: its acceptance authorizes nothing here")
    content = proposal.get("content", {})
    if target_kind is None:
        raise ValueError("materialization must declare its target kind")
    if content.get("target_kind") != target_kind:
        raise ValueError(f"the accepted candidate targets "
                         f"{content.get('target_kind')!r}, not {target_kind!r}: "
                         "an acceptance is not transferable across kinds")
    # Bind over the kind's declared fields plus any shared keys, never over
    # whatever the model chose to emit.
    binding_keys = CANDIDATE_BINDING_KEYS.get(target_kind, ())
    materialized = dict(materialized or {})
    for key in binding_keys:
        if key not in materialized:
            raise ValueError(f"materialization of {target_kind} must bind {key!r}")
        if key not in content:
            # Only reachable past record_candidate; fail typed, not KeyError.
            raise ValueError(f"accepted candidate is malformed: it lacks its "
                             f"kind's binding field {key!r}")
        if _normalized_value(content[key]) != _normalized_value(materialized[key]):
            raise ValueError(
                f"materialized {key!r} differs from what the human accepted: "
                "the acceptance covers the candidate's content, nothing else")
    for key, value in materialized.items():
        if key in content and _normalized_value(content[key]) \
                != _normalized_value(value):
            raise ValueError(
                f"materialized {key!r} differs from what the human accepted: "
                "the acceptance covers the candidate's content, nothing else")
    # Consumption is a fact about the log: a spend stays spent even once a
    # later version drops the proposal id.
    spent = store.materialization_of(proposal_id)
    if spent is not None:
        kind, object_id = spent
        raise ValueError(
            f"candidate proposal {proposal_id[:24]} was already "
            f"materialized as {kind} {object_id[:24]}: an "
            "acceptance is consumed by one materialization, not reused")
    return proposal


def creation_authority(store: AnalyticStore, *, provenance_kind: str,
                       inference_id: str = "", proposal_id: str = "",
                       target_kind: str | None = None,
                       materialized: Mapping[str, Any] | None = None) -> str:
    """The authority this provenance may stamp at creation. Model output enters
    only through an accepted candidate and lands as SUPPORTED_INFERENCE.
    """
    if provenance_kind == "ANALYST":
        return "ANALYST_ASSESSMENT"
    if provenance_kind == "RULE":
        return "DERIVED"
    require_accepted_candidate(store, inference_id=inference_id,
                               proposal_id=proposal_id, target_kind=target_kind,
                               materialized=materialized)
    return "SUPPORTED_INFERENCE"


# Per kind, the fields a candidate must declare and a materialization must
# match, so model-chosen key names cannot defeat the binding.
CANDIDATE_BINDING_KEYS = {
    "analytic_theme": ("title", "supporting_claim_ids"),
    "analytic_narrative": ("statement", "supporting_claim_ids"),
    "narrative_variant": ("statement", "relation", "claim_ids"),
    "propagation_edge": ("narrative_id", "from_manifestation_id",
                         "to_manifestation_id"),
    "stakeholder_assessment": ("entity_object_id", "context_kind", "context_id",
                               "role_in_context", "claims"),
    "influence_assertion": ("source_object_id", "target_object_id", "kind"),
    # edge_chain digests each edge's content in order; binding by edge id
    # would commit the acceptance to labels.
    "impact_path": ("objective_id", "summary", "edge_chain"),
    "response_option": ("objective_id", "path_id", "description"),
    "historical_analogue": ("query_id", "episode_id"),
    "analytic_forecast": ("question", "probability", "horizon_time"),
    "forecast_indicator": ("description", "kind", "forecast_ids"),
    # strategic_warning is a machine projection, never model-proposed.
}


def record_candidate(ctx: AnalyticContext, *, target_kind: str,
                     content: Mapping[str, Any], inference_id: str) -> dict[str, Any]:
    """Record a model-proposed candidate as a PROPOSED proposal, not as state. One
    missing its binding fields is refused, so the human sees exactly what would
    be materialized.
    """
    if target_kind not in ANALYTIC_ID_FIELDS:
        raise ValueError(f"unknown analytical kind: {target_kind}")
    if target_kind not in CANDIDATE_BINDING_KEYS:
        raise ValueError(f"{target_kind} cannot be model-proposed")
    missing = [key for key in CANDIDATE_BINDING_KEYS[target_kind]
               if key not in content]
    if missing:
        raise ValueError(f"a {target_kind} candidate must declare its binding "
                         f"fields {CANDIDATE_BINDING_KEYS[target_kind]}; "
                         f"missing: {missing}")
    if not inference_id:
        raise ValueError("a model candidate requires its inference record")
    now = ctx.now_fn()
    proposal = AnalyticalProposal(
        proposal_id=digest_id("anprop", target_kind, inference_id,
                              str(sorted(content.items()))[:2000]),
        inference_id=inference_id, proposal_type="ANALYTICAL_OBJECT_CANDIDATE",
        # The engine-declared kind wins, so a provider's own "target_kind"
        # cannot re-aim the candidate.
        content={**content, "target_kind": target_kind},
        status="PROPOSED", recorded_time=now, marking=ctx.marking)
    event = ctx.store.append("ANALYTICAL_PROPOSAL_RECORDED", proposal,
                             recorded_time=now, actor=ctx.actor)
    return event["record"]


def resolve_candidate(ctx: AnalyticContext, proposal_id: str, *, accept: bool,
                      actor_id: str, actor_kind: str, actor_roles: tuple[str, ...] = ("ANALYST",),
                      note: str = "") -> dict[str, Any]:
    """Accept or reject a model candidate; only a human can accept. Acceptance is a
    recorded action plus a new version, and returns the content to materialize.
    """
    latest = ctx.store.latest_by_id("analytical_proposal", "proposal_id").get(proposal_id)
    if latest is None:
        raise ValueError(f"unknown analytical proposal: {proposal_id}")
    if latest["status"] != "PROPOSED":
        raise ValueError(f"proposal {proposal_id} is already {latest['status']}")
    if accept and actor_kind != "HUMAN":
        raise ValueError("only a human can accept a model-proposed analytical object")
    now = ctx.now_fn()
    # An act about a hidden record is hidden, and a re-append never
    # re-classifies, so both take the proposal's marking.
    proposal_marking = marking_from_record(latest["marking"]) \
        if isinstance(latest.get("marking"), dict) else latest["marking"]
    action = AnalystAction(
        action_id=digest_id("anact", proposal_id, "ACCEPT" if accept else "REJECT", now),
        actor_id=actor_id, actor_kind=actor_kind, actor_roles=actor_roles,
        kind="ACCEPT" if accept else "REJECT",
        subject_kind="analytical_proposal", subject_id=proposal_id,
        note=note, recorded_time=now, marking=proposal_marking)
    ctx.store.append("ANALYST_ACTION_RECORDED", action, recorded_time=now, actor=actor_id)
    resolved = AnalyticalProposal(
        proposal_id=proposal_id, inference_id=latest["inference_id"],
        proposal_type=latest["proposal_type"], content=dict(latest["content"]),
        status="ACCEPTED" if accept else "REJECTED",
        recorded_time=now, marking=proposal_marking)
    event = ctx.store.append("ANALYTICAL_PROPOSAL_RECORDED", resolved,
                             recorded_time=resolved.recorded_time, actor=actor_id)
    return event["record"]


def open_identity_caveats(store: AnalyticStore, entity_object_id: str) -> tuple[str, ...]:
    """Open identity-ambiguity review items touching this entity, so an
    unresolved identity stays visible on the assessments that use it."""
    proposals = {p["proposal_id"]: p for p in store.records_of("association_proposal")}
    caveats = []
    for item in store.open_review_items():
        if item["kind"] != "IDENTITY_AMBIGUITY":
            continue
        proposal = proposals.get(item["subject_id"])
        if proposal and entity_object_id in (proposal["left_object_id"],
                                             proposal["right_object_id"]):
            caveats.append(item["item_id"])
    return tuple(sorted(caveats))
