"""Typed records for the analytical layer's event log: frozen validated
dataclasses, serialized once and replayed as dicts.

The invariants enforced at construction are what makes the layer honest. An
object cannot exist without a supporting claim basis. Observed, derived,
inferred, model-proposed and analyst-entered assertions carry distinct
authority, and the combinations that would launder one into another are
refused. Model provenance requires its inference record. An impact edge must
carry typed semantics and evidence. An analogue carries transfer risks and has
no forecast field.
"""
from __future__ import annotations

from dataclasses import dataclass

from curunir_operational.access import Marking
from curunir_operational.canonical import require_aware, require_aware_or_none
from curunir_operational.contracts import Record, _member

# ---- shared vocabularies --------------------------------------------------

# Authority of an analytical assertion, strongest first. AUTHORITY_RANK reads
# the order, so uncertainty propagates by position.
AUTHORITY_LEVELS = ("OBSERVED", "DERIVED", "ANALYST_ASSESSMENT",
                    "SUPPORTED_INFERENCE", "MODEL_PROPOSAL", "CONTESTED", "UNRESOLVED")
AUTHORITY_RANK = {level: rank for rank, level in enumerate(AUTHORITY_LEVELS)}
# what MODEL provenance may stamp: never observation, derivation or human
# judgment
MODEL_AUTHORITIES = ("SUPPORTED_INFERENCE", "MODEL_PROPOSAL",
                     "CONTESTED", "UNRESOLVED")

PROVENANCE_KINDS = ("RULE", "MODEL", "ANALYST")

ANALYTIC_KINDS = ("analytic_theme", "analytic_narrative", "narrative_variant",
                  "propagation_edge", "stakeholder_assessment", "influence_assertion",
                  "mission_objective", "analytic_assumption", "impact_path",
                  "response_option", "historical_episode", "historical_analogue",
                  "analytic_forecast", "forecast_indicator", "strategic_warning")

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
    "analytic_forecast": ("CREATED", "PROBABILITY_UPDATED", "UPDATE_REQUIRED",
                          "INDICATOR_FIRED", "BASIS_DEGRADED", "HORIZON_PASSED",
                          "RESOLVED_TRUE", "RESOLVED_FALSE", "RESOLVED_VOID",
                          "WITHDRAWN", "EVIDENCE_UPDATED"),
    "forecast_indicator": ("ARMED", "FIRED", "COVERAGE_BLOCKED",
                           "EXPIRED_UNFIRED", "RETIRED"),
    "strategic_warning": ("RAISED", "ESCALATED", "DOWNGRADED", "COMPONENT_CHANGED",
                          "RESOLVED", "WITHDRAWN"),
}


def weakest_authority(levels) -> str:
    """The weakest authority in a set; an empty set has none."""
    chosen = None
    for level in levels:
        _member(level, AUTHORITY_LEVELS, "authority")
        if chosen is None or AUTHORITY_RANK[level] > AUTHORITY_RANK[chosen]:
            chosen = level
    if chosen is None:
        raise ValueError("an empty set has no authority to claim")
    return chosen


def _require_model_inference(provenance_kind: str, inference_id: str,
                             authority: str | None = None,
                             proposal_id: str | None = None) -> None:
    """Check that provenance and its identifiers agree.

    Model output needs its inference record and, where the record carries one,
    its accepted candidate; nothing else may carry a candidate at all.
    """
    _member(provenance_kind, PROVENANCE_KINDS, "provenance kind")
    if provenance_kind != "MODEL":
        if proposal_id:
            raise ValueError("only model-generated analytical state carries an "
                             "accepted candidate proposal: a human or rule act "
                             "spends none")
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
    """Evidence arithmetic for one analytical object, computed from its claims.

    ``manifestation_count`` is reach — how many retained artifacts state this —
    and ``origin_family_count`` is independence. Fifty derivatives of one origin
    are one family. ``basis.compute_basis`` is the one implementation.
    """
    RECORD_TYPE = "analytic_basis"
    supporting_claim_ids: tuple[str, ...]
    contradicting_claim_ids: tuple[str, ...]
    observation_count: int
    manifestation_count: int
    source_count: int
    origin_families: tuple[str, ...]
    degraded_claim_count: int  # supporting claims no longer CURRENT
    languages: tuple[str, ...]
    # when the evidence was seen, not when the world was that way
    earliest_time: str
    latest_time: str
    # what the sources themselves stated as the valid interval
    stated_valid_from: str = ""
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
    """One typed state change of one analytical object; the sequence is its
    history."""
    RECORD_TYPE = "analytic_transition"
    transition_id: str
    subject_kind: str
    subject_id: str
    transition_type: str
    detail: str
    caused_by: str  # the change, claim, observation, act or proposal behind it
    evidence_refs: tuple[str, ...]
    from_status: str
    to_status: str
    recorded_time: str
    marking: Marking

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
    """A recurring or emerging issue across propositions, entities and events.

    Membership, support, contradiction and source families are all inspectable,
    and a theme without a supporting claim cannot be constructed.
    """
    RECORD_TYPE = "analytic_theme"
    theme_id: str
    version: int
    title: str
    description: str
    status: str
    authority: str
    parent_theme_id: str
    lineage: tuple[tuple[str, str], ...]  # (lineage kind, theme id)
    basis: BasisSummary
    entity_ids: tuple[str, ...]
    event_ids: tuple[str, ...]
    relation_ids: tuple[str, ...]
    valid_from: str | None
    valid_to: str | None
    provenance_kind: str
    inference_id: str
    proposal_id: str
    change_reason: str
    history: tuple[str, ...]
    recorded_time: str
    marking: Marking

    def __post_init__(self):
        _member(self.status, THEME_STATUSES, "theme status")
        _member(self.authority, AUTHORITY_LEVELS, "authority")
        _require_model_inference(self.provenance_kind, self.inference_id,
                                 authority=self.authority,
                                 proposal_id=self.proposal_id)
        require_aware(self.recorded_time)
        require_aware_or_none(self.valid_from)
        require_aware_or_none(self.valid_to)
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
    """A proposition being propagated: what is said, in which variants, through
    which manifestations, with reach and independence kept apart.

    Origin is never proven; the strongest status is EARLIEST_OBSERVED_KNOWN.
    """
    RECORD_TYPE = "analytic_narrative"
    narrative_id: str
    version: int
    statement: str             # the proposition as the evidence carries it
    normalized_statement: str  # matching key for text identity
    status: str
    authority: str
    origin_status: str
    earliest_manifestation_id: str
    earliest_time: str
    variant_ids: tuple[str, ...]
    counter_narrative_ids: tuple[str, ...]
    basis: BasisSummary
    entity_ids: tuple[str, ...]
    provenance_kind: str
    inference_id: str
    proposal_id: str
    change_reason: str
    history: tuple[str, ...]
    recorded_time: str
    marking: Marking

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
    """One materially distinct form of a narrative.

    Framing, attribution and counter-narrative relations stay typed rather than
    flattening into "same topic". Only VERBATIM and UNRESOLVED_RELATION may
    carry non-inferential authority: calling two texts a paraphrase is a
    judgment and must say whose.
    """
    RECORD_TYPE = "narrative_variant"
    variant_id: str
    narrative_id: str
    relation: str
    statement: str
    claim_ids: tuple[str, ...]
    observation_ids: tuple[str, ...]
    manifestation_ids: tuple[str, ...]
    language: str
    authority: str
    mechanism: str
    provenance_kind: str
    inference_id: str
    recorded_time: str
    marking: Marking
    version: int = 1
    proposal_id: str = ""  # the accepted candidate this consumed

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
    """How one manifestation of a narrative relates to another.

    LIKELY_DERIVATIVE and INDEPENDENT_ADOPTION are inferences and must state a
    mechanism; SAME_ORIGIN_FAMILY must actually be one family.
    """
    RECORD_TYPE = "propagation_edge"
    edge_id: str
    narrative_id: str
    from_manifestation_id: str
    to_manifestation_id: str
    relation: str
    mechanism: str
    from_family: str
    to_family: str
    authority: str
    basis_observation_ids: tuple[str, ...]
    provenance_kind: str
    inference_id: str
    recorded_time: str
    marking: Marking
    version: int = 1
    proposal_id: str = ""  # the accepted candidate this consumed

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
    """One position, interest or role of a stakeholder in context.

    An inferred interest can never carry observed authority, and an observed
    public position can carry only the stance the source itself stated.
    """
    RECORD_TYPE = "stakeholder_position"
    position_id: str
    kind: str
    statement: str
    stance: str
    authority: str
    claim_ids: tuple[str, ...]
    relationship_ids: tuple[str, ...]
    valid_from: str | None
    valid_to: str | None
    superseded: bool
    note: str

    def __post_init__(self):
        _member(self.kind, POSITION_KINDS, "position kind")
        _member(self.stance, POSITION_STANCES, "position stance")
        _member(self.authority, AUTHORITY_LEVELS, "authority")
        require_aware_or_none(self.valid_from)
        require_aware_or_none(self.valid_to)
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
    """An entity's evidence-backed relationship to one context during a period,
    never a permanent label.

    Identity caveats keep open ambiguity visible instead of collapsing
    possibly-distinct entities.
    """
    RECORD_TYPE = "stakeholder_assessment"
    assessment_id: str
    version: int
    entity_object_id: str
    entity_label: str
    context_kind: str
    context_id: str
    role_in_context: str
    positions: tuple[StakeholderPosition, ...]
    influence_ids: tuple[str, ...]
    identity_caveats: tuple[str, ...]  # open identity-ambiguity review items
    basis: BasisSummary
    status: str
    authority: str
    provenance_kind: str
    inference_id: str
    proposal_id: str
    change_reason: str
    history: tuple[str, ...]
    recorded_time: str
    marking: Marking

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

    Observed authority requires evidence, LIKELY_INFLUENCES must state its
    mechanism, and association alone is INFLUENCE_UNRESOLVED.
    """
    RECORD_TYPE = "influence_assertion"
    influence_id: str
    version: int
    source_object_id: str
    target_object_id: str
    kind: str
    mechanism: str
    authority: str
    claim_ids: tuple[str, ...]
    relationship_ids: tuple[str, ...]
    valid_from: str | None
    valid_to: str | None
    status: str
    provenance_kind: str
    inference_id: str
    proposal_id: str
    change_reason: str
    history: tuple[str, ...]
    recorded_time: str
    marking: Marking

    def __post_init__(self):
        _member(self.kind, INFLUENCE_KINDS, "influence kind")
        _member(self.status, INFLUENCE_STATUSES, "influence status")
        _member(self.authority, AUTHORITY_LEVELS, "authority")
        _require_model_inference(self.provenance_kind, self.inference_id,
                                 authority=self.authority,
                                 proposal_id=self.proposal_id)
        require_aware(self.recorded_time)
        require_aware_or_none(self.valid_from)
        require_aware_or_none(self.valid_to)
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
    """What a mission is trying to maintain, track or understand, with its
    dependencies and assumptions explicit so impact propagation can land."""
    RECORD_TYPE = "mission_objective"
    objective_id: str
    version: int
    mission_context: str
    statement: str
    status: str
    priority: str
    time_horizon: str
    depends_on: tuple[tuple[str, str], ...]  # (kind, id)
    assumption_ids: tuple[str, ...]
    change_reason: str
    history: tuple[str, ...]
    recorded_time: str
    marking: Marking

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
    """An assumption an impact path or objective rests on.

    Contradicting evidence invalidates it as a new version, and everything that
    depended on it can then be found and marked.
    """
    RECORD_TYPE = "analytic_assumption"
    assumption_id: str
    version: int
    statement: str
    status: str
    supporting_claim_ids: tuple[str, ...]
    contradicting_claim_ids: tuple[str, ...]
    objective_ids: tuple[str, ...]
    caused_by: str
    change_reason: str
    history: tuple[str, ...]
    recorded_time: str
    marking: Marking

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
    """One typed step in an impact path.

    Adjacency is not causality: every non-assumption edge carries evidence, and
    an inference edge exposes its reasoning and cannot claim observation.
    """
    RECORD_TYPE = "impact_edge"
    edge_id: str
    from_kind: str
    from_id: str
    to_kind: str
    to_id: str
    edge_kind: str
    effect_order: str
    authority: str
    note: str
    basis_ids: tuple[str, ...]      # claim, relationship or activity ids
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
    """A connected typed path from a world change to a mission objective.

    Its authority is its weakest edge, checked here rather than trusted; direct
    and second-order effects stay distinguishable; assumptions are explicit.
    """
    RECORD_TYPE = "impact_path"
    path_id: str
    version: int
    objective_id: str
    summary: str
    edges: tuple[ImpactEdge, ...]
    status: str
    path_authority: str        # must equal the weakest edge authority
    uncertainty_note: str
    assumption_ids: tuple[str, ...]
    provenance_kind: str
    inference_id: str
    proposal_id: str
    change_reason: str
    history: tuple[str, ...]
    recorded_time: str
    marking: Marking

    def __post_init__(self):
        _member(self.status, IMPACT_STATUSES, "impact status")
        _member(self.path_authority, AUTHORITY_LEVELS, "authority")
        # the path's authority comes from its edges, so this constrains only
        # the inference and proposal identity
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
    """A candidate response to an impact path; only a recorded human act can
    move it to ACCEPTED."""
    RECORD_TYPE = "response_option"
    option_id: str
    version: int
    objective_id: str
    path_id: str
    description: str
    prerequisites: tuple[str, ...]
    tradeoffs: tuple[str, ...]
    claim_ids: tuple[str, ...]
    uncertainty_note: str
    status: str
    human_actor: str
    provenance_kind: str
    inference_id: str
    change_reason: str
    history: tuple[str, ...]
    recorded_time: str
    marking: Marking
    proposal_id: str = ""  # the accepted candidate this consumed

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
    """A historical episode as structure: actors, sequence, setting, mechanism,
    constraints and outcome, each carried by claims."""
    RECORD_TYPE = "historical_episode"
    episode_id: str
    version: int
    title: str
    summary: str
    actor_object_ids: tuple[str, ...]
    event_ids: tuple[str, ...]          # ordered activity ids
    institutional_setting: str
    mechanism: str
    constraints: tuple[str, ...]
    outcome: str
    outcome_claim_ids: tuple[str, ...]
    claim_ids: tuple[str, ...]          # the evidence behind the episode
    valid_from: str | None
    valid_to: str | None
    change_reason: str
    history: tuple[str, ...]
    recorded_time: str
    marking: Marking

    def __post_init__(self):
        require_aware(self.recorded_time)
        require_aware_or_none(self.valid_from)
        require_aware_or_none(self.valid_to)
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
    """One dimension on which an analogue matched or mismatched, with its
    evidence."""
    RECORD_TYPE = "analogue_dimension"
    dimension: str
    detail: str
    basis_ids: tuple[str, ...]

    def __post_init__(self):
        _member(self.dimension, ANALOGUE_DIMENSIONS, "analogue dimension")
        if not self.detail:
            raise ValueError("an analogue dimension requires its detail")


@dataclass(frozen=True)
class HistoricalAnalogue(Record):
    """A structural comparison between a situation and a historical episode.

    It exposes matched and mismatched dimensions and its transfer risks, and has
    no forecast field: similar structure never becomes expected outcome.
    """
    RECORD_TYPE = "historical_analogue"
    analogue_id: str
    version: int
    query_kind: str  # the situation under analysis: theme, path or hypothesis
    query_id: str
    episode_id: str
    matched: tuple[AnalogueDimension, ...]
    mismatched: tuple[AnalogueDimension, ...]
    transfer_risks: tuple[str, ...]
    retrieval_method: str
    authority: str
    status: str
    provenance_kind: str
    inference_id: str
    change_reason: str
    history: tuple[str, ...]
    recorded_time: str
    marking: Marking
    proposal_id: str = ""  # the accepted candidate this consumed

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


# ---- forecasting / strategic warning --------------------------------------

FORECAST_STATUSES = ("OPEN", "UPDATE_REQUIRED", "HORIZON_PASSED",
                     "RESOLVED_TRUE", "RESOLVED_FALSE", "RESOLVED_VOID",
                     "WITHDRAWN")
FORECAST_TERMINAL_STATUSES = ("RESOLVED_TRUE", "RESOLVED_FALSE",
                              "RESOLVED_VOID", "WITHDRAWN")
RESOLUTION_RULE_KINDS = ("CLAIM_PREDICATE", "EVENT_OCCURRED", "HUMAN_JUDGMENT")

INDICATOR_KINDS = ("PRESENCE", "ABSENCE")
INDICATOR_DIRECTIONS = ("SUPPORTS", "UNDERMINES")
INDICATOR_STATUSES = ("ARMED", "FIRED", "COVERAGE_BLOCKED", "EXPIRED_UNFIRED",
                      "RETIRED")
INDICATOR_EFFECT_MODES = ("REVIEW_ONLY", "APPLY_PROBABILITY")

WARNING_TIERS = ("ROUTINE", "ATTENTION", "PRIORITY", "CRITICAL")
WARNING_STATUSES = ("ACTIVE", "ESCALATED", "DOWNGRADED", "RESOLVED", "WITHDRAWN")
# Status is a control input, not display text. Declaring the settled set beside
# each vocabulary makes a new status fail closed until it is classified here,
# and gives report approval one table to read instead of private lists.
SETTLED_SUPPORT_STATUSES: dict[str, frozenset[str]] = {
    "analytic_forecast": frozenset(FORECAST_STATUSES)
    - frozenset(FORECAST_TERMINAL_STATUSES),
    "strategic_warning": frozenset(("ACTIVE", "ESCALATED", "DOWNGRADED")),
    "analytic_theme": frozenset(("EMERGING", "ACTIVE", "DECLINING")),
    "analytic_narrative": frozenset(("ACTIVE", "DORMANT")),
    "response_option": frozenset(("PROPOSED", "UNDER_REVIEW", "ACCEPTED")),
    "forecast_indicator": frozenset(("ARMED", "FIRED", "COVERAGE_BLOCKED")),
    "analytic_assumption": frozenset(("HELD",)),
    "impact_path": frozenset(("PROPOSED", "ASSESSED", "CHANGED")),
    "mission_objective": frozenset(("ACTIVE", "EXPOSED")),
    "stakeholder_assessment": frozenset(("ACTIVE",)),
    "influence_assertion": frozenset(("ACTIVE",)),
    "historical_analogue": frozenset(("PROPOSED", "REVIEWED")),
    "discriminator": frozenset(("SATISFIED",)),
}
OFFICIAL_STATUS_VOCABULARIES: dict[str, tuple[str, ...]] = {
    "analytic_forecast": FORECAST_STATUSES,
    "strategic_warning": WARNING_STATUSES,
    "analytic_theme": THEME_STATUSES,
    "analytic_narrative": NARRATIVE_STATUSES,
    "response_option": RESPONSE_STATUSES,
    "forecast_indicator": INDICATOR_STATUSES,
    "analytic_assumption": ASSUMPTION_STATUSES,
    "impact_path": IMPACT_STATUSES,
    "mission_objective": OBJECTIVE_STATUSES,
    "stakeholder_assessment": STAKEHOLDER_STATUSES,
    "influence_assertion": INFLUENCE_STATUSES,
    "historical_analogue": ANALOGUE_STATUSES,
    "discriminator": ("OPEN", "REQUESTED", "SATISFIED", "UNSATISFIABLE"),
}
PROBABILITY_BANDS = ("REMOTE", "POSSIBLE", "LIKELY", "VERY_LIKELY")
TIME_PRESSURES = ("DISTANT", "NEAR", "CLOSE", "IMMINENT", "PASSED")
EVIDENCE_CONFIDENCES = ("NONE", "WEAK", "MODERATE", "STRONG")


@dataclass(frozen=True)
class ResolutionRule(Record):
    """How a forecast resolves.

    Machine resolution exists only for typed, checkable rules. A FALSE by
    absence additionally requires the coverage the rule declares: "we did not
    see it" is not "it did not happen" unless someone looked.
    """
    RECORD_TYPE = "resolution_rule"
    kind: str
    criteria: str  # the resolution criterion, stated for a human
    # CLAIM_PREDICATE: the claim whose value settles the question
    claim_subject_ref: str = ""
    claim_attribute: str = ""
    expected_value: str = ""
    # EVENT_OCCURRED: an activity of this type on this subject settles TRUE
    event_activity_type: str = ""
    event_subject_ref: str = ""
    # coverage demanded before an absence may resolve FALSE
    absence_min_successful_sources: int = 1
    absence_required_source_ids: tuple[str, ...] = ()
    resolver_role: str = "ANALYST"

    def __post_init__(self):
        _member(self.kind, RESOLUTION_RULE_KINDS, "resolution rule kind")
        if not self.criteria:
            raise ValueError("a resolution rule requires its stated criteria")
        if self.kind == "CLAIM_PREDICATE" and not (
                self.claim_subject_ref and self.claim_attribute
                and self.expected_value):
            raise ValueError("a claim-predicate rule requires subject, attribute "
                             "and the value that settles TRUE")
        if self.kind == "EVENT_OCCURRED" and not (
                self.event_activity_type and self.event_subject_ref):
            raise ValueError("an event rule requires the activity type and subject")
        if self.absence_min_successful_sources < 1:
            raise ValueError("absence coverage requires at least one source")
        if self.kind != "HUMAN_JUDGMENT":
            # these kinds can resolve FALSE by absence, and coverage naming no
            # source would be satisfied by any unrelated search anywhere
            if not self.absence_required_source_ids:
                raise ValueError("a machine-resolvable rule must NAME the "
                                 "sources whose successful search constitutes "
                                 "absence coverage")
            if self.absence_min_successful_sources \
                    > len(self.absence_required_source_ids):
                raise ValueError("absence coverage cannot demand more sources "
                                 "than it names: the minimum counts only "
                                 "declared sources")


@dataclass(frozen=True)
class ForecastRecord(Record):
    """An exact proposition with explicit outcome semantics, a horizon, a typed
    resolution rule, a probability with its authored basis, and its history.

    A probability is authored, never observed or derived, and lies strictly
    inside (0,1). Moving it is a new version with its reason. A resolved
    forecast carries its resolution evidence and its resolver.
    """
    RECORD_TYPE = "analytic_forecast"
    forecast_id: str
    version: int
    question: str            # the exact proposition being forecast
    outcome_semantics: str   # what counts as TRUE
    proposition_refs: tuple[tuple[str, str], ...]  # (kind, id)
    horizon_time: str
    resolution: ResolutionRule
    probability: float
    probability_basis: str   # the rationale for this number
    basis: BasisSummary      # the claims bearing on the question
    assumption_ids: tuple[str, ...]
    indicator_ids: tuple[str, ...]
    author: str              # analyst or model id; calibration groups on it
    domain: str              # calibration grouping, e.g. "corporate-registry"
    status: str
    authority: str
    provenance_kind: str
    inference_id: str
    proposal_id: str
    outcome: str             # "" until resolved, then TRUE/FALSE/VOID
    resolved_time: str       # "" until resolved
    resolution_evidence_refs: tuple[str, ...]
    resolver_id: str
    resolver_kind: str
    change_reason: str
    history: tuple[str, ...]
    recorded_time: str
    marking: Marking

    def __post_init__(self):
        _member(self.status, FORECAST_STATUSES, "forecast status")
        _member(self.authority, AUTHORITY_LEVELS, "authority")
        require_aware(self.recorded_time)
        require_aware(self.horizon_time)
        if self.version < 1:
            raise ValueError("forecast versions start at 1")
        if not self.question or not self.outcome_semantics:
            raise ValueError("a forecast requires its exact question and outcome "
                             "semantics")
        if not self.proposition_refs:
            raise ValueError("a forecast must reference the world-model or "
                             "analytical state it is about")
        if not (0.0 < self.probability < 1.0):
            raise ValueError("a forecast probability lies strictly inside (0,1): "
                             "certainty is a resolution, not a forecast")
        if not self.probability_basis:
            raise ValueError("a probability without its authored basis is a "
                             "number, not a judgment")
        if self.provenance_kind not in ("ANALYST", "MODEL"):
            raise ValueError("a probability is authored, never machine-derived: "
                             "provenance is ANALYST or a gated MODEL candidate")
        if self.authority not in ("ANALYST_ASSESSMENT", "SUPPORTED_INFERENCE"):
            raise ValueError("a probability is never observed or derived")
        _require_model_inference(self.provenance_kind, self.inference_id,
                                 authority=self.authority,
                                 proposal_id=self.proposal_id)
        if self.version > 1 and not self.change_reason:
            raise ValueError("a forecast version beyond 1 requires the reason "
                             "it moved")
        if self.status in ("RESOLVED_TRUE", "RESOLVED_FALSE"):
            if not self.resolution_evidence_refs:
                raise ValueError("a TRUE/FALSE resolution requires the evidence "
                                 "that settled it")
            if not self.resolver_id or not self.resolved_time:
                raise ValueError("a resolution requires its resolver and time")
            if self.outcome not in ("TRUE", "FALSE"):
                raise ValueError("a resolved forecast states its outcome")
        if self.status == "RESOLVED_VOID" and not self.resolver_id:
            raise ValueError("voiding a forecast is a recorded act")
        if self.status not in FORECAST_TERMINAL_STATUSES and self.outcome:
            raise ValueError("an unresolved forecast carries no outcome")


@dataclass(frozen=True)
class IndicatorEffect(Record):
    """What an indicator firing is pre-authorized to do to its forecasts.

    APPLY_PROBABILITY is a human's own conditional judgment executing later, so
    it requires the authorizing human and a target inside (0,1).
    """
    RECORD_TYPE = "indicator_effect"
    mode: str
    target_probability: float | None = None
    rationale: str = ""
    authorized_by: str = ""
    authorized_kind: str = ""

    def __post_init__(self):
        _member(self.mode, INDICATOR_EFFECT_MODES, "indicator effect mode")
        if self.mode == "APPLY_PROBABILITY":
            if self.target_probability is None \
                    or not (0.0 < self.target_probability < 1.0):
                raise ValueError("a pre-authorized update requires a target "
                                 "probability inside (0,1)")
            if not self.authorized_by or self.authorized_kind != "HUMAN":
                raise ValueError("only a human can pre-authorize an automatic "
                                 "probability update; the machine executes the "
                                 "human's recorded conditional judgment")
            if not self.rationale:
                raise ValueError("a pre-authorized update states its rationale")


@dataclass(frozen=True)
class IndicatorRecord(Record):
    """An observation pattern that would move a forecast.

    PRESENCE fires on matching evidence. ABSENCE fires only once its deadline
    passes and the declared coverage was achieved: silence moves nothing when
    the sources that would show it were never searched.
    """
    RECORD_TYPE = "forecast_indicator"
    indicator_id: str
    version: int
    forecast_ids: tuple[str, ...]
    description: str
    kind: str
    direction: str
    desired_observation_type: str
    desired_subject_ref: str
    desired_attribute: str
    expected_value: str      # "" matches any new or changed observation
    effect: IndicatorEffect
    # ABSENCE only; empty for PRESENCE
    deadline: str
    coverage_min_successful_sources: int
    coverage_required_source_ids: tuple[str, ...]
    status: str
    armed_time: str
    fired_time: str
    fired_evidence_refs: tuple[str, ...]
    provenance_kind: str
    inference_id: str
    proposal_id: str
    change_reason: str
    history: tuple[str, ...]
    recorded_time: str
    marking: Marking

    def __post_init__(self):
        _member(self.kind, INDICATOR_KINDS, "indicator kind")
        _member(self.direction, INDICATOR_DIRECTIONS, "indicator direction")
        _member(self.status, INDICATOR_STATUSES, "indicator status")
        if self.desired_observation_type:
            # a typo would never match, leaving the indicator ARMED forever and
            # looking like honest waiting
            from curunir_semantic.contracts import OBSERVATION_TYPES
            _member(self.desired_observation_type, OBSERVATION_TYPES,
                    "observation type")
        require_aware(self.recorded_time)
        require_aware(self.armed_time)
        _require_model_inference(self.provenance_kind, self.inference_id,
                                 proposal_id=self.proposal_id)
        if self.version < 1:
            raise ValueError("indicator versions start at 1")
        if not self.forecast_ids:
            raise ValueError("an indicator exists to move forecasts: it names them")
        if not self.description:
            raise ValueError("an indicator states what it watches for")
        if self.kind == "PRESENCE" \
                and not (self.desired_subject_ref or self.desired_attribute):
            raise ValueError("a presence indicator must constrain its subject "
                             "or attribute: an unconstrained pattern would "
                             "fire on any observation about anything")
        if self.effect.mode == "APPLY_PROBABILITY" \
                and not self.desired_subject_ref:
            raise ValueError("an indicator that executes a probability move "
                             "must name its subject: an attribute-only pattern "
                             "would move the number on any entity's matching "
                             "observation")
        if self.kind == "ABSENCE":
            if not self.deadline:
                raise ValueError("an absence indicator requires its deadline")
            require_aware(self.deadline)
            if self.coverage_min_successful_sources < 1:
                raise ValueError("an absence indicator requires declared coverage: "
                                 "otherwise silence is unfalsifiable")
            if not self.coverage_required_source_ids:
                raise ValueError("an absence indicator must NAME the sources "
                                 "whose successful search constitutes coverage: "
                                 "an unnamed minimum is satisfied by unrelated "
                                 "noise")
            if self.coverage_min_successful_sources \
                    > len(self.coverage_required_source_ids):
                raise ValueError("absence coverage cannot demand more sources "
                                 "than it names")
        if self.status == "FIRED":
            if self.kind == "PRESENCE" and not self.fired_evidence_refs:
                raise ValueError("a fired presence indicator carries the evidence "
                                 "that fired it")
            if not self.fired_time:
                raise ValueError("a fired indicator records when")
        if self.version > 1 and not self.change_reason:
            raise ValueError("an indicator version beyond 1 requires its change "
                             "reason")


@dataclass(frozen=True)
class WarningRecord(Record):
    """A projection of one forecast onto the impact state it threatens, never an
    independent classifier.

    The tier must be what the named rule yields for the typed components, and
    every component carries its basis.
    """
    RECORD_TYPE = "strategic_warning"
    warning_id: str
    version: int
    mission_context: str
    objective_id: str
    forecast_id: str
    impact_path_ids: tuple[str, ...]
    probability_band: str
    consequence: str          # the threatened objective's priority
    time_pressure: str
    evidence_confidence: str
    tier: str
    tier_rule_id: str         # the named rule that produced the tier
    component_basis: tuple[tuple[str, str], ...]  # (component, why)
    status: str
    change_reason: str
    history: tuple[str, ...]
    recorded_time: str
    marking: Marking

    def __post_init__(self):
        _member(self.probability_band, PROBABILITY_BANDS, "probability band")
        _member(self.consequence, OBJECTIVE_PRIORITIES, "consequence")
        _member(self.time_pressure, TIME_PRESSURES, "time pressure")
        _member(self.evidence_confidence, EVIDENCE_CONFIDENCES,
                "evidence confidence")
        _member(self.tier, WARNING_TIERS, "warning tier")
        _member(self.status, WARNING_STATUSES, "warning status")
        require_aware(self.recorded_time)
        if self.version < 1:
            raise ValueError("warning versions start at 1")
        if not self.objective_id or not self.forecast_id:
            raise ValueError("a warning projects a forecast onto an objective: "
                             "it requires both")
        if not self.tier_rule_id:
            raise ValueError("a warning tier is produced by a named rule, "
                             "never asserted freely")
        # naming the rule is not enough: the record must satisfy it
        from .warning import TIER_RULE_V1, derive_tier
        if self.tier_rule_id != TIER_RULE_V1:
            raise ValueError(f"unknown warning tier rule "
                             f"{self.tier_rule_id!r}: only a rule the engine "
                             "implements can have produced a tier")
        expected_tier, _ = derive_tier(self.probability_band, self.consequence,
                                       self.time_pressure,
                                       self.evidence_confidence)
        if self.tier != expected_tier:
            raise ValueError(
                f"tier {self.tier} is not what {self.tier_rule_id} yields for "
                f"({self.probability_band}, {self.consequence}, "
                f"{self.time_pressure}, {self.evidence_confidence}): "
                f"the rule produces {expected_tier}")
        if not self.component_basis:
            raise ValueError("every warning component states its basis")
        if self.version > 1 and not self.change_reason:
            raise ValueError("a warning version beyond 1 requires its change reason")
