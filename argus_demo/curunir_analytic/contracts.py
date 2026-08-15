"""Typed records for the analytical intelligence layer's event log.

Same discipline as the operational, fabric and semantic contracts: frozen
validated dataclasses, serialized once, replayed as dicts. The invariants
enforced at construction are the layer's epistemic law:

  * an analytical object cannot exist without a supporting claim basis — a
    model-generated title with no evidence is a proposal, never state;
  * observed, deterministically derived, inferred, model-proposed and
    analyst-entered assertions carry distinct authority values and the
    combinations that would launder one into another are rejected
    (an inferred interest can never be OBSERVED; a verbatim-match edge can
    never claim more than derivation; a LIKELY_INFLUENCES relation can never
    present itself as observed fact);
  * model provenance requires the inference record identity;
  * impact edges must carry typed semantics and evidence — decorative
    "A → B, 0.8" edges cannot be constructed;
  * analogues carry transfer risks structurally and have no forecast field.
"""
from __future__ import annotations

from dataclasses import dataclass

from curunir_operational.access import Marking
from curunir_operational.canonical import require_aware, require_aware_or_none
from curunir_operational.contracts import Record, _member

# ---- shared vocabularies --------------------------------------------------

# Epistemic authority of an analytical assertion. Ordered strongest-to-weakest
# for uncertainty propagation (see AUTHORITY_RANK): directly observed evidence
# outranks deterministic derivation; an accountable human judgment outranks a
# machine inference, which outranks an unaccepted model proposal; CONTESTED
# and UNRESOLVED are weaker than all — machine output never outranks recorded
# human judgment.
AUTHORITY_LEVELS = ("OBSERVED", "DERIVED", "ANALYST_ASSESSMENT",
                    "SUPPORTED_INFERENCE", "MODEL_PROPOSAL", "CONTESTED", "UNRESOLVED")
AUTHORITY_RANK = {level: rank for rank, level in enumerate(AUTHORITY_LEVELS)}
INFERENTIAL_AUTHORITIES = ("ANALYST_ASSESSMENT", "SUPPORTED_INFERENCE",
                           "MODEL_PROPOSAL", "CONTESTED", "UNRESOLVED")
# what MODEL provenance may ever stamp: never observation/derivation, and
# never ANALYST_ASSESSMENT — machine output cannot wear human judgment
MODEL_AUTHORITIES = ("SUPPORTED_INFERENCE", "MODEL_PROPOSAL",
                     "CONTESTED", "UNRESOLVED")

PROVENANCE_KINDS = ("RULE", "MODEL", "ANALYST")

ANALYTIC_KINDS = ("analytic_theme", "analytic_narrative", "narrative_variant",
                  "propagation_edge", "stakeholder_assessment", "influence_assertion",
                  "mission_objective", "analytic_assumption", "impact_path",
                  "response_option", "historical_episode", "historical_analogue")

THEME_STATUSES = ("EMERGING", "ACTIVE", "CONTESTED", "DECLINING", "STALE",
                  "RESOLVED", "MERGED", "SPLIT")
THEME_LINEAGE_KINDS = ("MERGED_FROM", "SPLIT_FROM", "MERGED_INTO", "SPLIT_INTO")

NARRATIVE_STATUSES = ("ACTIVE", "DORMANT", "CONTESTED", "RESOLVED", "SUPERSEDED")
ORIGIN_STATUSES = ("EARLIEST_OBSERVED_KNOWN", "ORIGIN_UNRESOLVED")

VARIANT_RELATIONS = ("VERBATIM", "PARAPHRASE", "NARROWING", "BROADENING",
                     "FRAMING_SHIFT", "ATTRIBUTION_SHIFT", "CAUSALITY_SHIFT",
                     "CERTAINTY_SHIFT", "POLARITY_SHIFT", "COUNTER_NARRATIVE",
                     "UNRESOLVED_RELATION")

PROPAGATION_RELATIONS = ("SAME_ORIGIN_FAMILY", "LIKELY_DERIVATIVE",
                         "INDEPENDENT_ADOPTION", "UNRESOLVED")

POSITION_KINDS = ("PUBLIC_POSITION", "INFERRED_INTEREST", "FORMAL_ROLE",
                  "CAPABILITY", "DEPENDENCY")
POSITION_STANCES = ("SUPPORTS", "OPPOSES", "NEUTRAL", "MIXED", "UNRESOLVED")

STAKEHOLDER_CONTEXT_KINDS = ("MISSION", "THEME", "ISSUE", "OBJECTIVE", "EVENT")
STAKEHOLDER_STATUSES = ("ACTIVE", "SUPERSEDED", "WITHDRAWN")

INFLUENCE_KINDS = ("FORMAL_AUTHORITY_OVER", "FUNDS", "OWNS", "APPOINTS",
                   "ADVISES", "MEMBER_OF", "PUBLICLY_ENDORSES", "PUBLICLY_OPPOSES",
                   "INFORMATION_CHANNEL_TO", "INTERMEDIARY_BETWEEN",
                   "LIKELY_INFLUENCES", "INFLUENCE_UNRESOLVED")
INFLUENCE_STATUSES = ("ACTIVE", "SUPERSEDED", "RETIRED")

OBJECTIVE_STATUSES = ("ACTIVE", "EXPOSED", "ACHIEVED", "ABANDONED", "SUPERSEDED")
OBJECTIVE_PRIORITIES = ("LOW", "MEDIUM", "HIGH", "CRITICAL")

ASSUMPTION_STATUSES = ("HELD", "UNCERTAIN", "INVALIDATED", "SUPERSEDED")

IMPACT_EDGE_KINDS = ("EVENT_EFFECT", "DEPENDENCY", "TYPED_RELATION",
                     "ASSUMPTION_LINK", "INFERENCE")
EDGE_ENDPOINT_KINDS = ("object", "activity", "claim", "relationship") + ANALYTIC_KINDS
EFFECT_ORDERS = ("DIRECT", "SECOND_ORDER", "POTENTIAL", "UNRESOLVED")
IMPACT_STATUSES = ("PROPOSED", "ASSESSED", "CHANGED", "STALE", "INVALIDATED", "RESOLVED")

RESPONSE_STATUSES = ("PROPOSED", "UNDER_REVIEW", "ACCEPTED", "REJECTED", "WITHDRAWN")

ANALOGUE_DIMENSIONS = ("ACTOR_CONFIGURATION", "EVENT_TYPE", "INSTITUTIONAL_SETTING",
                       "CAUSAL_MECHANISM", "TEMPORAL_SEQUENCE", "CONSTRAINTS", "OUTCOME")
ANALOGUE_STATUSES = ("PROPOSED", "REVIEWED", "REJECTED")

# typed failure statuses engines return instead of empty successes
ANALYTIC_FAILURES = ("ANALYTICAL_PROVIDER_UNAVAILABLE", "INSUFFICIENT_EVIDENCE",
                     "IDENTITY_AMBIGUITY", "DEPENDENCE_UNRESOLVED",
                     "COVERAGE_INSUFFICIENT", "TEMPORAL_SCOPE_UNRESOLVED",
                     "NARRATIVE_ORIGIN_UNRESOLVED", "IMPACT_PATH_UNRESOLVED")

TRANSITION_TYPES = {
    "analytic_theme": ("CREATED", "STRENGTHENED", "WEAKENED", "CONTRADICTION_ADDED",
                       "SOURCE_DIVERSITY_CHANGED", "SUBTHEME_EMERGED",
                       "MEMBERSHIP_CHANGED", "EVIDENCE_UPDATED", "SPLIT_PROPOSED",
                       "MERGE_PROPOSED", "MERGED", "SPLIT", "STALE", "RESOLVED",
                       "REVIVED"),
    "analytic_narrative": ("CREATED", "VARIANT_ADDED", "PROPAGATION_OBSERVED",
                           "INDEPENDENT_ADOPTION_OBSERVED", "COUNTER_NARRATIVE_LINKED",
                           "ORIGIN_REVISED", "STRENGTHENED", "WEAKENED", "DORMANT",
                           "REEMERGED", "CONTESTED", "RESOLVED", "EVIDENCE_UPDATED"),
    "narrative_variant": ("RECORDED", "REVISED"),
    "propagation_edge": ("RECORDED", "REVISED"),
    "stakeholder_assessment": ("CREATED", "POSITION_ADDED", "POSITION_CHANGED",
                               "ROLE_CHANGED", "INTEREST_INFERRED",
                               "ALIGNMENT_CHANGED", "BASIS_DEGRADED",
                               "IDENTITY_CAVEAT_CHANGED", "SUPERSEDED",
                               "EVIDENCE_UPDATED"),
    "influence_assertion": ("ASSERTED", "STRENGTHENED", "WEAKENED",
                            "SUPERSEDED", "RETIRED"),
    "mission_objective": ("CREATED", "EXPOSED", "STATUS_CHANGED", "DEPENDENCY_CHANGED"),
    "analytic_assumption": ("HELD", "QUESTIONED", "INVALIDATED", "RESTORED",
                            "EVIDENCE_UPDATED"),
    "impact_path": ("CONSTRUCTED", "EDGE_CHANGED", "ASSUMPTION_INVALIDATED",
                    "UNCERTAINTY_CHANGED", "STALE", "INVALIDATED", "RESOLVED"),
    "response_option": ("PROPOSED", "REVIEWED", "ACCEPTED", "REJECTED", "WITHDRAWN",
                        "EVIDENCE_DEGRADED"),
    "historical_episode": ("RECORDED", "REVISED", "EVIDENCE_UPDATED",
                           "EVIDENCE_DEGRADED"),
    "historical_analogue": ("RETRIEVED", "REVISED", "REJECTED", "EVIDENCE_DEGRADED"),
}


def weakest_authority(levels) -> str:
    """The weakest authority in a set — uncertainty propagates, never washes
    out: a chain is only as strong as its weakest link. Fails closed: an
    empty set has no authority to claim."""
    chosen = None
    for level in levels:
        _member(level, AUTHORITY_LEVELS, "authority")
        if chosen is None or AUTHORITY_RANK[level] > AUTHORITY_RANK[chosen]:
            chosen = level
    if chosen is None:
        raise ValueError("an empty set has no authority; nothing observed nothing")
    return chosen


def _require_model_inference(provenance_kind: str, inference_id: str,
                             authority: str | None = None,
                             proposal_id: str | None = None) -> None:
    """MODEL provenance requires the inference identity, can never carry
    observed/derived authority, and — for records that carry a proposal_id —
    requires the accepted-candidate identity, so a model output cannot enter
    typed state without its acceptance trail."""
    _member(provenance_kind, PROVENANCE_KINDS, "provenance kind")
    if provenance_kind != "MODEL":
        return
    if not inference_id:
        raise ValueError("model-generated analytical state requires its inference record")
    if authority is not None and authority not in MODEL_AUTHORITIES:
        raise ValueError("model-generated analytical state can never carry "
                         "observed, derived or analyst authority")
    if proposal_id is not None and not proposal_id:
        raise ValueError("model-generated analytical state requires its accepted "
                         "candidate proposal")


# ---- shared basis ---------------------------------------------------------


@dataclass(frozen=True)
class BasisSummary(Record):
    """Evidence arithmetic for one analytical object, computed from claims.

    The two counts that must never be conflated are ``manifestation_count``
    (propagation reach: how many retained artifacts state this) and
    ``origin_family_count`` (independence: how many distinct origin families
    those artifacts descend from). Fifty derivatives of one origin are one
    family. ``compute_basis`` in ``basis.py`` is the one implementation.
    """
    RECORD_TYPE = "analytic_basis"
    supporting_claim_ids: tuple[str, ...]
    contradicting_claim_ids: tuple[str, ...]
    observation_count: int
    manifestation_count: int
    source_count: int
    origin_families: tuple[str, ...]
    degraded_claim_count: int  # supporting claims whose lifecycle is no longer CURRENT
    languages: tuple[str, ...]
    earliest_time: str  # earliest source-STATE time in the basis ("" if unknown);
    latest_time: str    # a knowledge-side span — never the world's valid time
    # defaults keep pre-extension replayed bases valid
    stated_valid_from: str = ""  # earliest source-STATED valid time ("" if none)
    stated_valid_to: str = ""
    unresolved_claim_ids: tuple[str, ...] = ()  # ids resolving to no known claim
    coverage_notes: tuple[str, ...] = ()
    note: str = ""

    @property
    def origin_family_count(self) -> int:
        return len(self.origin_families)

    @property
    def contradiction_count(self) -> int:
        return len(self.contradicting_claim_ids)

    def __post_init__(self):
        for count in (self.observation_count, self.manifestation_count,
                      self.source_count, self.degraded_claim_count):
            if count < 0:
                raise ValueError("basis counts cannot be negative")
        if len(self.origin_families) > self.manifestation_count:
            raise ValueError("origin families cannot exceed manifestations: "
                             "independence is bounded by reach")
        if self.degraded_claim_count > len(self.supporting_claim_ids):
            raise ValueError("degraded claims cannot exceed supporting claims")


# ---- transitions ----------------------------------------------------------


@dataclass(frozen=True)
class AnalyticalTransition(Record):
    """One typed state change of one analytical object. Never overwritten;
    the sequence of transitions is the object's analytical history."""
    RECORD_TYPE = "analytic_transition"
    transition_id: str; subject_kind: str; subject_id: str
    transition_type: str; detail: str
    caused_by: str  # change_id / claim_id / observation_id / action id / proposal id
    evidence_refs: tuple[str, ...]
    from_status: str; to_status: str
    recorded_time: str; marking: Marking

    def __post_init__(self):
        _member(self.subject_kind, ANALYTIC_KINDS, "analytic kind")
        allowed = TRANSITION_TYPES[self.subject_kind]
        _member(self.transition_type, allowed, f"{self.subject_kind} transition")
        require_aware(self.recorded_time)
        if not self.detail:
            raise ValueError("an analytical transition must explain itself")


# ---- themes ---------------------------------------------------------------


@dataclass(frozen=True)
class ThemeRecord(Record):
    """An evidence-backed recurring or emerging issue across propositions,
    entities and events over time. Not a topic-model label: membership,
    support, contradiction and family basis are all inspectable, and a theme
    without at least one supporting claim cannot be constructed."""
    RECORD_TYPE = "analytic_theme"
    theme_id: str; version: int; title: str; description: str
    status: str; authority: str
    parent_theme_id: str  # non-empty for a subtheme
    lineage: tuple[tuple[str, str], ...]  # (MERGED_FROM/SPLIT_FROM/..., theme_id)
    basis: BasisSummary
    entity_ids: tuple[str, ...]; event_ids: tuple[str, ...]
    relation_ids: tuple[str, ...]
    valid_from: str | None; valid_to: str | None
    provenance_kind: str; inference_id: str; proposal_id: str
    change_reason: str
    history: tuple[str, ...]
    recorded_time: str; marking: Marking

    def __post_init__(self):
        _member(self.status, THEME_STATUSES, "theme status")
        _member(self.authority, AUTHORITY_LEVELS, "authority")
        _require_model_inference(self.provenance_kind, self.inference_id,
                                 authority=self.authority,
                                 proposal_id=self.proposal_id)
        require_aware(self.recorded_time)
        require_aware_or_none(self.valid_from); require_aware_or_none(self.valid_to)
        if self.version < 1:
            raise ValueError("theme versions start at 1")
        if not self.title:
            raise ValueError("a theme requires a title")
        if not self.basis.supporting_claim_ids:
            raise ValueError("a theme cannot exist as an unsupported title: "
                             "it requires at least one supporting claim")
        if self.version > 1 and not self.change_reason:
            raise ValueError("a theme version beyond 1 requires its change reason")
        for kind, other in self.lineage:
            _member(kind, THEME_LINEAGE_KINDS, "theme lineage kind")
            if not other:
                raise ValueError("theme lineage requires the related theme id")


# ---- narratives -----------------------------------------------------------


@dataclass(frozen=True)
class NarrativeRecord(Record):
    """A proposition/frame family being propagated: what is said, in which
    variants, through which manifestations, with propagation reach and source
    independence kept as separate axes. Origin is never 'proven': the
    strongest representable origin status is EARLIEST_OBSERVED_KNOWN."""
    RECORD_TYPE = "analytic_narrative"
    narrative_id: str; version: int
    statement: str            # the core proposition, as carried by evidence
    normalized_statement: str  # deterministic matching key
    status: str; authority: str
    origin_status: str
    earliest_manifestation_id: str; earliest_time: str
    variant_ids: tuple[str, ...]
    counter_narrative_ids: tuple[str, ...]
    basis: BasisSummary
    entity_ids: tuple[str, ...]
    provenance_kind: str; inference_id: str; proposal_id: str
    change_reason: str
    history: tuple[str, ...]
    recorded_time: str; marking: Marking

    @property
    def propagation_reach(self) -> int:
        return self.basis.manifestation_count

    @property
    def independent_origin_count(self) -> int:
        return self.basis.origin_family_count

    def __post_init__(self):
        _member(self.status, NARRATIVE_STATUSES, "narrative status")
        _member(self.authority, AUTHORITY_LEVELS, "authority")
        _member(self.origin_status, ORIGIN_STATUSES, "origin status")
        _require_model_inference(self.provenance_kind, self.inference_id,
                                 authority=self.authority,
                                 proposal_id=self.proposal_id)
        require_aware(self.recorded_time)
        if self.version < 1:
            raise ValueError("narrative versions start at 1")
        if not self.statement:
            raise ValueError("a narrative requires its core proposition")
        if not self.basis.supporting_claim_ids:
            raise ValueError("a narrative requires at least one supporting claim")
        if self.origin_status == "EARLIEST_OBSERVED_KNOWN" and not self.earliest_manifestation_id:
            raise ValueError("EARLIEST_OBSERVED_KNOWN requires the earliest manifestation")
        if self.version > 1 and not self.change_reason:
            raise ValueError("a narrative version beyond 1 requires its change reason")


@dataclass(frozen=True)
class NarrativeVariant(Record):
    """One materially distinct form of a narrative. Variant relations are not
    flattened into 'same topic': a framing shift, an attribution shift and a
    counter-narrative stay typed. Only VERBATIM (deterministic text identity)
    and UNRESOLVED_RELATION may carry non-inferential authority — calling two
    different texts a paraphrase is a judgment and must say whose."""
    RECORD_TYPE = "narrative_variant"
    variant_id: str; narrative_id: str
    relation: str; statement: str
    claim_ids: tuple[str, ...]; observation_ids: tuple[str, ...]
    manifestation_ids: tuple[str, ...]
    language: str
    authority: str; mechanism: str
    provenance_kind: str; inference_id: str
    recorded_time: str; marking: Marking
    version: int = 1  # revision is a new version, never a silent overwrite
    proposal_id: str = ""  # the accepted candidate this consumed, if MODEL

    def __post_init__(self):
        _member(self.relation, VARIANT_RELATIONS, "variant relation")
        if self.version < 1:
            raise ValueError("variant versions start at 1")
        _member(self.authority, AUTHORITY_LEVELS, "authority")
        _require_model_inference(self.provenance_kind, self.inference_id,
                                 authority=self.authority,
                                 proposal_id=self.proposal_id)
        require_aware(self.recorded_time)
        if not self.statement:
            raise ValueError("a variant requires its statement")
        if not self.claim_ids and not self.observation_ids:
            raise ValueError("a variant requires supporting claims or observations")
        if not self.manifestation_ids:
            raise ValueError("a variant requires the manifestations that carry it")
        if self.relation == "VERBATIM" and self.authority not in ("OBSERVED", "DERIVED"):
            raise ValueError("a verbatim variant is a deterministic text identity, "
                             "not an inference")
        if self.relation not in ("VERBATIM", "UNRESOLVED_RELATION") \
                and self.authority in ("OBSERVED", "DERIVED"):
            raise ValueError(f"classifying a variant as {self.relation} is a judgment: "
                             "it cannot claim observed/derived authority")
        if self.relation not in ("VERBATIM", "UNRESOLVED_RELATION") and not self.mechanism:
            raise ValueError("a non-verbatim variant relation must explain its basis")


@dataclass(frozen=True)
class PropagationEdge(Record):
    """How one manifestation of a narrative relates to another. Propagation
    reach and independent adoption are different analytical facts; this edge
    keeps them apart. LIKELY_DERIVATIVE and INDEPENDENT_ADOPTION are
    inferences and must carry a mechanism; SAME_ORIGIN_FAMILY is derived
    from the dependence engine and must actually be one family."""
    RECORD_TYPE = "propagation_edge"
    edge_id: str; narrative_id: str
    from_manifestation_id: str; to_manifestation_id: str
    relation: str; mechanism: str
    from_family: str; to_family: str
    authority: str
    basis_observation_ids: tuple[str, ...]
    provenance_kind: str; inference_id: str
    recorded_time: str; marking: Marking
    version: int = 1  # a revised judgment is a new version, never a silent overwrite
    proposal_id: str = ""  # the accepted candidate this consumed, if MODEL

    def __post_init__(self):
        _member(self.relation, PROPAGATION_RELATIONS, "propagation relation")
        _member(self.authority, AUTHORITY_LEVELS, "authority")
        if self.version < 1:
            raise ValueError("edge versions start at 1")
        _require_model_inference(self.provenance_kind, self.inference_id,
                                 authority=self.authority,
                                 proposal_id=self.proposal_id)
        require_aware(self.recorded_time)
        if not self.from_manifestation_id or not self.to_manifestation_id:
            raise ValueError("a propagation edge requires both manifestations")
        if self.relation == "SAME_ORIGIN_FAMILY":
            if self.from_family != self.to_family or not self.from_family:
                raise ValueError("SAME_ORIGIN_FAMILY requires one shared origin family")
        if self.relation in ("LIKELY_DERIVATIVE", "INDEPENDENT_ADOPTION"):
            if self.authority in ("OBSERVED",):
                raise ValueError(f"{self.relation} is an inference over the evidence, "
                                 "never a direct observation")
            if not self.mechanism:
                raise ValueError(f"{self.relation} must state its mechanism")
            if self.from_family == self.to_family:
                raise ValueError(f"{self.relation} concerns distinct origin families; "
                                 "one family is SAME_ORIGIN_FAMILY")


# ---- stakeholders / influence --------------------------------------------


@dataclass(frozen=True)
class StakeholderPosition(Record):
    """One position/interest/role of a stakeholder in context. The category
    error this record makes impossible: an INFERRED_INTEREST can never carry
    OBSERVED authority, and an OBSERVED public position can carry only the
    stance the source itself stated — a machine labelling stance onto quoted
    text is interpretation and must say so."""
    RECORD_TYPE = "stakeholder_position"
    position_id: str; kind: str; statement: str; stance: str
    authority: str
    claim_ids: tuple[str, ...]; relationship_ids: tuple[str, ...]
    valid_from: str | None; valid_to: str | None
    superseded: bool; note: str

    def __post_init__(self):
        _member(self.kind, POSITION_KINDS, "position kind")
        _member(self.stance, POSITION_STANCES, "position stance")
        _member(self.authority, AUTHORITY_LEVELS, "authority")
        require_aware_or_none(self.valid_from); require_aware_or_none(self.valid_to)
        if not self.statement:
            raise ValueError("a position requires its statement")
        if self.kind == "PUBLIC_POSITION" and not self.claim_ids:
            raise ValueError("a public position requires the claims that carry it")
        if self.kind == "INFERRED_INTEREST" and self.authority in ("OBSERVED", "DERIVED"):
            raise ValueError("an inferred interest is an inference: it can never "
                             "present itself as observed or derived fact")
        if self.kind == "FORMAL_ROLE" and not self.claim_ids and not self.relationship_ids:
            raise ValueError("a formal role requires evidence claims or relations")
        if self.authority == "OBSERVED" and self.stance != "UNRESOLVED" \
                and self.kind == "PUBLIC_POSITION" and not self.note:
            raise ValueError("an OBSERVED stance label requires a note citing the "
                             "source-stated stance; otherwise record stance UNRESOLVED "
                             "and let interpretation carry its own authority")


@dataclass(frozen=True)
class StakeholderAssessment(Record):
    """A stakeholder is an entity with an evidence-backed relationship to a
    specific context (mission/theme/issue/objective/event) during a period —
    never a permanent global label. Identity caveats carried here keep open
    IDENTITY_AMBIGUITY review items visible instead of silently collapsing
    possibly-distinct entities."""
    RECORD_TYPE = "stakeholder_assessment"
    assessment_id: str; version: int
    entity_object_id: str; entity_label: str
    context_kind: str; context_id: str
    role_in_context: str
    positions: tuple[StakeholderPosition, ...]
    influence_ids: tuple[str, ...]
    identity_caveats: tuple[str, ...]  # open IDENTITY_AMBIGUITY review item ids
    basis: BasisSummary
    status: str; authority: str
    provenance_kind: str; inference_id: str; proposal_id: str
    change_reason: str
    history: tuple[str, ...]
    recorded_time: str; marking: Marking

    def __post_init__(self):
        _member(self.context_kind, STAKEHOLDER_CONTEXT_KINDS, "stakeholder context kind")
        _member(self.status, STAKEHOLDER_STATUSES, "stakeholder status")
        _member(self.authority, AUTHORITY_LEVELS, "authority")
        _require_model_inference(self.provenance_kind, self.inference_id,
                                 authority=self.authority,
                                 proposal_id=self.proposal_id)
        require_aware(self.recorded_time)
        if self.version < 1:
            raise ValueError("assessment versions start at 1")
        if not self.entity_object_id:
            raise ValueError("a stakeholder assessment requires its entity")
        if not self.context_id:
            raise ValueError("stakeholderhood is contextual: an assessment requires "
                             "the mission/theme/issue it is relative to")
        if not self.positions and not self.basis.supporting_claim_ids:
            raise ValueError("a stakeholder assessment requires positions or a claim basis")
        if self.version > 1 and not self.change_reason:
            raise ValueError("an assessment version beyond 1 requires its change reason")


@dataclass(frozen=True)
class InfluenceAssertion(Record):
    """A typed, evidence-bound influence relation between two entities.
    Decorative influence edges cannot be built: OBSERVED authority requires
    evidence, LIKELY_INFLUENCES must state its mechanism and cannot claim
    observation, and association alone is INFLUENCE_UNRESOLVED."""
    RECORD_TYPE = "influence_assertion"
    influence_id: str; version: int
    source_object_id: str; target_object_id: str
    kind: str; mechanism: str
    authority: str
    claim_ids: tuple[str, ...]; relationship_ids: tuple[str, ...]
    valid_from: str | None; valid_to: str | None
    status: str
    provenance_kind: str; inference_id: str; proposal_id: str
    change_reason: str
    history: tuple[str, ...]
    recorded_time: str; marking: Marking

    def __post_init__(self):
        _member(self.kind, INFLUENCE_KINDS, "influence kind")
        _member(self.status, INFLUENCE_STATUSES, "influence status")
        _member(self.authority, AUTHORITY_LEVELS, "authority")
        _require_model_inference(self.provenance_kind, self.inference_id,
                                 authority=self.authority,
                                 proposal_id=self.proposal_id)
        require_aware(self.recorded_time)
        require_aware_or_none(self.valid_from); require_aware_or_none(self.valid_to)
        if self.version < 1:
            raise ValueError("influence versions start at 1")
        if self.source_object_id == self.target_object_id:
            raise ValueError("influence requires two distinct entities")
        if self.kind == "LIKELY_INFLUENCES":
            if self.authority in ("OBSERVED", "DERIVED"):
                raise ValueError("LIKELY_INFLUENCES is an inference: it cannot claim "
                                 "observed or derived authority")
            if not self.mechanism:
                raise ValueError("LIKELY_INFLUENCES must state its mechanism")
        if self.authority in ("OBSERVED", "DERIVED") \
                and not self.claim_ids and not self.relationship_ids:
            raise ValueError("observed/derived influence requires evidence claims "
                             "or world-model relations")
        if self.version > 1 and not self.change_reason:
            raise ValueError("an influence version beyond 1 requires its change reason")


# ---- impact / exposure ----------------------------------------------------


@dataclass(frozen=True)
class MissionObjective(Record):
    """What a mission is trying to maintain, track, assess or understand —
    machine-readable, with explicit dependencies and assumptions, so impact
    propagation has something typed to land on."""
    RECORD_TYPE = "mission_objective"
    objective_id: str; version: int
    mission_context: str; statement: str
    status: str; priority: str; time_horizon: str
    depends_on: tuple[tuple[str, str], ...]  # (kind, id): world/analytical refs
    assumption_ids: tuple[str, ...]
    change_reason: str
    history: tuple[str, ...]
    recorded_time: str; marking: Marking

    def __post_init__(self):
        _member(self.status, OBJECTIVE_STATUSES, "objective status")
        _member(self.priority, OBJECTIVE_PRIORITIES, "objective priority")
        require_aware(self.recorded_time)
        if self.version < 1:
            raise ValueError("objective versions start at 1")
        if not self.statement or not self.mission_context:
            raise ValueError("an objective requires a statement and its mission")
        for kind, ref in self.depends_on:
            if not kind or not ref:
                raise ValueError("objective dependencies require kind and id")
            if kind not in ("object", "relationship", "activity", "claim") \
                    and kind not in ANALYTIC_KINDS:
                raise ValueError(f"objective dependency kind {kind!r} is not "
                                 "trackable state")
        if self.version > 1 and not self.change_reason:
            raise ValueError("an objective version beyond 1 requires its change reason")


@dataclass(frozen=True)
class AssumptionRecord(Record):
    """An explicit assumption an impact path or objective rests on. When new
    evidence contradicts it, the assumption is INVALIDATED as a new version —
    and everything that depended on it can be found and marked."""
    RECORD_TYPE = "analytic_assumption"
    assumption_id: str; version: int
    statement: str; status: str
    supporting_claim_ids: tuple[str, ...]
    contradicting_claim_ids: tuple[str, ...]
    objective_ids: tuple[str, ...]
    caused_by: str; change_reason: str
    history: tuple[str, ...]
    recorded_time: str; marking: Marking

    def __post_init__(self):
        _member(self.status, ASSUMPTION_STATUSES, "assumption status")
        require_aware(self.recorded_time)
        if self.version < 1:
            raise ValueError("assumption versions start at 1")
        if not self.statement:
            raise ValueError("an assumption requires its statement")
        if self.status == "INVALIDATED" and not self.caused_by:
            raise ValueError("invalidating an assumption requires what caused it")
        if self.version > 1 and not self.change_reason:
            raise ValueError("an assumption version beyond 1 requires its change reason")


@dataclass(frozen=True)
class ImpactEdge(Record):
    """One typed step in an impact path. Graph adjacency is not causality:
    an edge must carry typed semantics, and every non-assumption edge must
    carry evidence — a bare 'A relates to B' cannot be constructed. An
    INFERENCE edge must expose its reasoning and cannot claim observation."""
    RECORD_TYPE = "impact_edge"
    edge_id: str
    from_kind: str; from_id: str
    to_kind: str; to_id: str
    edge_kind: str; effect_order: str
    authority: str; note: str
    basis_ids: tuple[str, ...]      # claim / relationship / activity ids
    assumption_ids: tuple[str, ...]

    def __post_init__(self):
        _member(self.edge_kind, IMPACT_EDGE_KINDS, "impact edge kind")
        _member(self.effect_order, EFFECT_ORDERS, "effect order")
        _member(self.authority, AUTHORITY_LEVELS, "authority")
        _member(self.from_kind, EDGE_ENDPOINT_KINDS, "edge endpoint kind")
        _member(self.to_kind, EDGE_ENDPOINT_KINDS, "edge endpoint kind")
        if not self.from_id or not self.to_id:
            raise ValueError("an impact edge requires both endpoints")
        if self.from_kind == self.to_kind and self.from_id == self.to_id:
            raise ValueError("an impact edge cannot be a self-loop: a node does "
                             "not impact itself along an edge")
        if self.edge_kind == "INFERENCE":
            if self.authority in ("OBSERVED", "DERIVED"):
                raise ValueError("an inference edge cannot claim observed/derived "
                                 "authority")
            if not self.note:
                raise ValueError("an inference edge must expose its reasoning")
        elif self.edge_kind == "ASSUMPTION_LINK":
            if not self.assumption_ids:
                raise ValueError("an assumption link requires its assumptions")
        elif not self.basis_ids:
            raise ValueError(f"a {self.edge_kind} edge requires evidence basis ids: "
                             "adjacency without evidence is not impact")


@dataclass(frozen=True)
class ImpactPath(Record):
    """A connected, typed, inspectable path from a world change to a mission
    objective. The path's authority is its weakest edge (validated, not
    trusted), direct and second-order effects stay distinguishable, and the
    assumptions the path rests on are explicit."""
    RECORD_TYPE = "impact_path"
    path_id: str; version: int
    objective_id: str; summary: str
    edges: tuple[ImpactEdge, ...]
    status: str
    path_authority: str        # must equal the weakest edge authority
    uncertainty_note: str
    assumption_ids: tuple[str, ...]
    provenance_kind: str; inference_id: str; proposal_id: str
    change_reason: str
    history: tuple[str, ...]
    recorded_time: str; marking: Marking

    def __post_init__(self):
        _member(self.status, IMPACT_STATUSES, "impact status")
        _member(self.path_authority, AUTHORITY_LEVELS, "authority")
        # the path's authority is derived from its edges, so MODEL provenance
        # here constrains only inference/proposal identity, not the level
        _require_model_inference(self.provenance_kind, self.inference_id,
                                 proposal_id=self.proposal_id)
        require_aware(self.recorded_time)
        if self.version < 1:
            raise ValueError("impact path versions start at 1")
        if not self.edges:
            raise ValueError("an impact path requires at least one edge")
        if not self.objective_id:
            raise ValueError("an impact path requires the objective it concerns")
        for previous, following in zip(self.edges, self.edges[1:]):
            if previous.to_id != following.from_id:
                raise ValueError("impact path edges must connect: "
                                 f"{previous.to_id[:24]} does not lead to "
                                 f"{following.from_id[:24]}")
        expected = weakest_authority(edge.authority for edge in self.edges)
        if self.path_authority != expected:
            raise ValueError(f"path authority must be the weakest edge authority "
                             f"({expected}), got {self.path_authority}: uncertainty "
                             "propagates, it does not wash out")
        weak = [e for e in self.edges
                if e.authority not in ("OBSERVED", "DERIVED") or e.assumption_ids]
        if weak and not self.uncertainty_note:
            raise ValueError("a path with inferential or assumption-bearing edges "
                             "must state its uncertainty")
        if self.version > 1 and not self.change_reason:
            raise ValueError("an impact path version beyond 1 requires its change reason")


@dataclass(frozen=True)
class ResponseOption(Record):
    """A candidate response to an impact path — decision support, never
    autonomous command: only a recorded human act can move it to ACCEPTED."""
    RECORD_TYPE = "response_option"
    option_id: str; version: int
    objective_id: str; path_id: str
    description: str
    prerequisites: tuple[str, ...]; tradeoffs: tuple[str, ...]
    claim_ids: tuple[str, ...]
    uncertainty_note: str
    status: str; human_actor: str
    provenance_kind: str; inference_id: str
    change_reason: str
    history: tuple[str, ...]
    recorded_time: str; marking: Marking
    proposal_id: str = ""  # the accepted candidate this consumed, if MODEL

    def __post_init__(self):
        _member(self.status, RESPONSE_STATUSES, "response status")
        _require_model_inference(self.provenance_kind, self.inference_id,
                                 proposal_id=self.proposal_id)
        require_aware(self.recorded_time)
        if self.version < 1:
            raise ValueError("response option versions start at 1")
        if not self.description:
            raise ValueError("a response option requires a description")
        if self.status == "ACCEPTED" and not self.human_actor:
            raise ValueError("only a recorded human act can accept a response option")


# ---- historical analogues -------------------------------------------------


@dataclass(frozen=True)
class HistoricalEpisode(Record):
    """A structured, evidence-bound historical episode: actors, sequence,
    institutional setting, mechanism, constraints and outcome — each carried
    by claims, never fabricated narrative."""
    RECORD_TYPE = "historical_episode"
    episode_id: str; version: int
    title: str; summary: str
    actor_object_ids: tuple[str, ...]
    event_ids: tuple[str, ...]          # ordered activity ids
    institutional_setting: str
    mechanism: str
    constraints: tuple[str, ...]
    outcome: str
    outcome_claim_ids: tuple[str, ...]
    claim_ids: tuple[str, ...]          # evidence basis for the episode facts
    valid_from: str | None; valid_to: str | None
    change_reason: str
    history: tuple[str, ...]
    recorded_time: str; marking: Marking

    def __post_init__(self):
        require_aware(self.recorded_time)
        require_aware_or_none(self.valid_from); require_aware_or_none(self.valid_to)
        if self.version < 1:
            raise ValueError("episode versions start at 1")
        if not self.title:
            raise ValueError("an episode requires a title")
        if not self.claim_ids:
            raise ValueError("a historical episode requires evidence claims: "
                             "history is not fabricated")
        if self.outcome and not self.outcome_claim_ids:
            raise ValueError("a stated outcome requires the claims that carry it")
        if self.version > 1 and not self.change_reason:
            raise ValueError("an episode version beyond 1 requires its change reason")


@dataclass(frozen=True)
class AnalogueDimension(Record):
    """One explicit structural dimension on which an analogue matched or
    mismatched, with the evidence for the comparison."""
    RECORD_TYPE = "analogue_dimension"
    dimension: str; detail: str
    basis_ids: tuple[str, ...]

    def __post_init__(self):
        _member(self.dimension, ANALOGUE_DIMENSIONS, "analogue dimension")
        if not self.detail:
            raise ValueError("an analogue dimension requires its detail")


@dataclass(frozen=True)
class HistoricalAnalogue(Record):
    """A structural comparison between a current situation and a historical
    episode. It exposes matched AND mismatched dimensions and transfer risks,
    and it has no forecast field: similar structure never silently becomes
    expected outcome."""
    RECORD_TYPE = "historical_analogue"
    analogue_id: str; version: int
    query_kind: str; query_id: str  # theme / impact_path / hypothesis under analysis
    episode_id: str
    matched: tuple[AnalogueDimension, ...]
    mismatched: tuple[AnalogueDimension, ...]
    transfer_risks: tuple[str, ...]
    retrieval_method: str
    authority: str; status: str
    provenance_kind: str; inference_id: str
    change_reason: str
    history: tuple[str, ...]
    recorded_time: str; marking: Marking
    proposal_id: str = ""  # the accepted candidate this consumed, if MODEL

    def __post_init__(self):
        _member(self.status, ANALOGUE_STATUSES, "analogue status")
        _member(self.authority, AUTHORITY_LEVELS, "authority")
        _require_model_inference(self.provenance_kind, self.inference_id,
                                 authority=self.authority,
                                 proposal_id=self.proposal_id)
        require_aware(self.recorded_time)
        if self.version < 1:
            raise ValueError("analogue versions start at 1")
        if not self.matched:
            raise ValueError("an analogue requires at least one matched dimension: "
                             "retrieval must be structural, not vibes")
        if not self.transfer_risks:
            raise ValueError("an analogue requires transfer-risk caveats: no analogue "
                             "transfers cleanly")
        if not self.retrieval_method:
            raise ValueError("an analogue must state how it was retrieved")
        if self.authority == "OBSERVED":
            raise ValueError("an analogy is never a direct observation")
        if self.version > 1 and not self.change_reason:
            raise ValueError("an analogue version beyond 1 requires its change reason")
