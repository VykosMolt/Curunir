"""Relation classification under the full contract decision order (Section 13).

V5.1's ``contradiction.relate`` never ran the decision order the contract
requires.  Two branches short-circuited ahead of the scope, definition,
lifecycle and notice tests, and both of them overreached:

* ``IDENTITY_DISAGREEMENT`` fired whenever two claims carried textually
  different subjects, no identity resolution had been supplied and the values
  differed.  Because identity resolution is never run in bulk, that condition
  is the normal case: it produced 5254 of the 20 527 relations across the
  three V5.1 campaigns, and 8 of the 45 scored held-out cases, every one of
  which reviewers marked NO_CONFLICT.  Two claims about different entities do
  not conflict; ``IDENTITY_DISAGREEMENT`` describes something else entirely,
  namely two sources disagreeing about *which* entity a shared designator
  denotes, and that requires affirmative evidence.
* ``UNRESOLVED`` fired whenever values diverged and event time was unknown,
  before anything had checked whether the two claims share a proposition at
  all.  That produced 3561 population relations and 10 further held-out
  errors, again all NO_CONFLICT under review.

The consequence was a classifier that used 6 of its 14 classes over the whole
population (NO_CONFLICT 10950, IDENTITY_DISAGREEMENT 5254, UNRESOLVED 3561,
SCOPE_DIFFERENCE 756, TEMPORAL_UPDATE 5, LOGICAL_CONTRADICTION 1) and only 3
of 14 in the held-out corpus, scoring 25/45 (semantic correctness 0.5556).

This module composes over the frozen V5.1 records and runs the eight contract
steps in order — entity, proposition, definitions, valid intervals, scopes,
lifecycle dimension, correction/supersession evidence, preliminary-versus-final
— before any conflict class may be assigned.  The lifecycle step (Section 13.1
step 6) is new: it consumes the V5.2 lifecycle DAG so that a plan and an
implementation of the same object are read as one progression rather than as a
numeric or logical disagreement.  ``UNRESOLVED`` survives only as the last
resort, after every earlier test has been run and with a demonstration naming
what is missing.

Explicit editorial notices keep the dispositive position V5.1 gave them: the
notice branch is delegated unchanged to the frozen module, so CORRECTION,
RETRACTION and SUPERSESSION behave exactly as they did, including supersession
scope and the claim-version state machine.

Every rule is language-general; nothing here encodes a campaign, source or
fixture answer.

Research shadow only.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable, Mapping

from ..v4.models import ClaimUnit
from ..v5_1.contradiction import (  # noqa: F401  (re-exported for callers)
    ClaimVersionState, DefinitionEvidence, NoticeRecord, RelationAssessment,
    SubjectIdentity, TemporalFrame, _EVENT_BASIS, _assessment, _compare_values,
    _definition_applies, _norm, apply_notice, definition_evidence,
    initial_claim_state, notice_record, subject_identity, temporal_frame,
)
from ..v5_1.contradiction import relate as _v5_1_relate
from ..v5_1.models import (
    IDENTITY_EVIDENCE_KINDS, MODALITIES, POLARITIES, RELATION_CLASSES,
    EvidenceRef, Record, stable_id,
)
from .lifecycle import (
    MODALITY_TO_STATE, comparable, derive_lifecycle, entails, normalize_state,
)

# ---------------------------------------------------------------------------
# Section 13.1 — the decision order
# ---------------------------------------------------------------------------

# The order is normative: every step above a conflict class must be answered
# before that class may be assigned.  Step 7 is hoisted to the front of the
# implementation because an explicit editorial act is dispositive over the
# whole pair and needs none of the semantic tests below it (Section 13.4).
DECISION_ORDER: tuple[str, ...] = (
    "SAME_ENTITY",
    "SAME_PROPOSITION",
    "COMPATIBLE_DEFINITIONS",
    "OVERLAPPING_VALID_INTERVALS",
    "COMPATIBLE_SCOPES",
    "SAME_LIFECYCLE_DIMENSION",
    "CORRECTION_OR_SUPERSESSION_EVIDENCE",
    "PRELIMINARY_VERSUS_FINAL",
)

# Section 13.2 — what licenses each class.  A class with no documented trigger
# is a class nobody can audit, which is how V5.1 came to use six of fourteen.
RELATION_TRIGGERS: Mapping[str, str] = {
    "CORRECTION": (
        "an explicit CORRECTS notice with span evidence governs at least one "
        "claim of the pair"),
    "RETRACTION": (
        "an explicit RETRACTS notice with span evidence governs at least one "
        "claim of the pair"),
    "SUPERSESSION": (
        "an explicit SUPERSEDES notice with span evidence and a declared scope "
        "governs at least one claim of the pair"),
    "IDENTITY_DISAGREEMENT": (
        "affirmative evidence that two sources resolve one shared designator to "
        "different entities, or an identity assessment that returned a recorded "
        "conflict; never mere textual difference between subjects"),
    "NO_CONFLICT": (
        "the claims are about different entities, predicate different aspects, "
        "cover disjoint scopes, sit on entailed lifecycle states, or assert the "
        "same value for the same proposition"),
    "QUALIFICATION": (
        "one claim is conditional and does not assert the proposition outright, "
        "one restates the other's value for a narrower scope, or the pair stands "
        "in a preliminary-versus-final relationship"),
    "DEFINITION_DIFFERENCE": (
        "recorded definition evidence shows the claims define a shared term "
        "differently, so their values are not comparable"),
    "TEMPORAL_UPDATE": (
        "the same entity and predicate carry different values over disjoint "
        "event periods; both may be true of their own period"),
    "SCOPE_DIFFERENCE": (
        "the values differ across nested or partially overlapping geographic, "
        "organizational or temporal scopes, or across lifecycle dimensions that "
        "are on different branches of the lifecycle DAG"),
    "UNRESOLVED": (
        "every earlier test has been run and none separates the claims, but the "
        "event-time or identity evidence needed to adjudicate the divergence is "
        "absent, or the values cannot be interpreted at all"),
    "POLARITY_CONFLICT": (
        "the identical proposition is asserted with opposite polarity over an "
        "established shared event time"),
    "NUMERIC_DISAGREEMENT": (
        "unit-normalized measured quantities diverge for the same proposition, "
        "scope, lifecycle dimension and event time; the divergence could in "
        "principle be reconciled by measurement"),
    "SOURCE_DISAGREEMENT": (
        "two sources assert incompatible categorical values for the same "
        "proposition, scope, lifecycle dimension and overlapping validity, with "
        "no correction or supersession settling precedence"),
    "LOGICAL_CONTRADICTION": (
        "the values cannot both be true under any reading: one is an explicit "
        "negation of the other, or both are members of a declared mutually "
        "exclusive vocabulary, with same entity, predicate, scope, lifecycle "
        "dimension and overlapping validity"),
}
if frozenset(RELATION_TRIGGERS) != RELATION_CLASSES:
    raise RuntimeError("relation triggers drifted from models.RELATION_CLASSES")

_NOTICE_CLASSES = frozenset({"CORRECTION", "RETRACTION", "SUPERSESSION"})


# ---------------------------------------------------------------------------
# Affirmative identity-conflict evidence
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class IdentityDispute(Record):
    """Two sources resolve one designator to two different entities.

    This is the only thing ``IDENTITY_DISAGREEMENT`` may describe.  V5.1 used
    that class for the absence of an identity resolution, which is not a
    disagreement about identity but an absence of evidence about it; the
    honest reading of two differently-named subjects is that they are two
    entities, and two entities do not conflict.
    """

    dispute_id: str
    designator: str
    left_entity_id: str
    right_entity_id: str
    evidence: tuple[EvidenceRef, ...]

    def __post_init__(self) -> None:
        if not self.designator.strip():
            raise ValueError("an identity dispute requires the disputed designator")
        if not self.left_entity_id.strip() or not self.right_entity_id.strip():
            raise ValueError("an identity dispute requires both resolved entities")
        if _norm(self.left_entity_id) == _norm(self.right_entity_id):
            raise ValueError("an identity dispute requires two divergent resolutions")
        if not self.evidence:
            raise ValueError("an identity dispute requires affirmative identity evidence")
        for reference in self.evidence:
            if reference.evidence_kind not in IDENTITY_EVIDENCE_KINDS:
                raise ValueError(
                    f"identity evidence kind outside shared vocabulary: "
                    f"{reference.evidence_kind}")


def identity_dispute(*, designator: str, left_entity_id: str, right_entity_id: str,
                     evidence: tuple[EvidenceRef, ...]) -> IdentityDispute:
    return IdentityDispute(
        stable_id("identity-dispute", designator, left_entity_id, right_entity_id),
        designator, left_entity_id, right_entity_id, tuple(evidence))


def _dispute_applies(dispute: IdentityDispute, left_claim: ClaimUnit,
                     right_claim: ClaimUnit) -> bool:
    """The disputed designator must actually be the referent both claims use."""
    designator = _norm(dispute.designator)
    return all(designator in _norm(" ".join((claim.subject, claim.normalized_statement)))
               for claim in (left_claim, right_claim))


# Identity states this module reasons over.  They are deliberately not the V4
# identity vocabulary: what matters here is whether a conflict class may be
# assigned, and the two unresolved cases differ in exactly that respect.
_SAME_ENTITY = "SAME_ENTITY"
_ASSESSED_AMBIGUOUS = "ASSESSED_AMBIGUOUS"


# ---------------------------------------------------------------------------
# Mutual exclusivity — the only license for LOGICAL_CONTRADICTION
# ---------------------------------------------------------------------------

# Negation particles across the five supported languages, applied as one union
# table.  A value that is the other value with a negation particle removed is
# that value's explicit negation, which is the P / not-P form the contract
# reserves for LOGICAL_CONTRADICTION.  Field-level polarity is a separate
# class (POLARITY_CONFLICT) and is tested separately.
_NEGATION_PARTICLES = frozenset({
    "not", "no", "never", "without", "non",
    "nicht", "kein", "keine", "keinen", "keiner", "keinem", "keines", "nie",
    "niemals", "ohne",
    "ne", "pas", "aucun", "aucune", "jamais", "sans",
    "ningun", "ningún", "ninguna", "ninguno", "nunca", "sin",
    "nessun", "nessuna", "mai", "senza",
})

_TOKEN_SPLIT = re.compile(r"[\s\-‐-―]+")


def _strip_negation(value: str) -> tuple[str, bool]:
    tokens = [token for token in _TOKEN_SPLIT.split(_norm(value)) if token]
    kept = [token for token in tokens if token not in _NEGATION_PARTICLES]
    return " ".join(kept), len(kept) != len(tokens)


def _mutually_exclusive(left_value: str, right_value: str,
                        exclusive_value_sets: Iterable[Iterable[str]]) -> str | None:
    """Name the exclusivity mechanism, or None when the values merely differ.

    Two divergent values are not by themselves a contradiction: one source may
    be measuring differently, reporting a subset, or simply be wrong.  Only an
    affirmative exclusivity mechanism rules out every reading in which both
    hold.
    """
    left_stripped, left_negated = _strip_negation(left_value)
    right_stripped, right_negated = _strip_negation(right_value)
    if (left_stripped and left_stripped == right_stripped
            and left_negated != right_negated):
        return "one value is an explicit negation of the other"
    left_norm, right_norm = _norm(left_value), _norm(right_value)
    for group in exclusive_value_sets:
        members = {_norm(member) for member in group}
        if left_norm in members and right_norm in members:
            return "both values belong to one declared mutually exclusive vocabulary"
    return None


# ---------------------------------------------------------------------------
# Lifecycle dimension (Section 13.1 step 6)
# ---------------------------------------------------------------------------

def _lifecycle_state(claim: ClaimUnit, supplied: str | None, language: str) -> str:
    """Read the lifecycle dimension a claim sits on.

    An explicitly supplied state wins; otherwise the state is derived from the
    claim wording, and only when the wording carries no lifecycle cue at all
    does the historical flat modality field get consulted.
    """
    if supplied is not None:
        return normalize_state(supplied)
    reading = derive_lifecycle(claim.normalized_statement, language)
    if reading.state == "UNKNOWN":
        reading = derive_lifecycle(f"{claim.predicate} {claim.object_or_value}", language)
    if reading.state != "UNKNOWN":
        return reading.state
    return MODALITY_TO_STATE.get(claim.modality, "UNKNOWN")


# ---------------------------------------------------------------------------
# Scope relations (Section 13.1 step 5) — geographic and organizational
# ---------------------------------------------------------------------------

def _scope_relation(left: frozenset[str], right: frozenset[str]) -> str:
    """An unstated scope is the universal scope, so it nests anything."""
    if left == right:
        return "EQUAL"
    if left and right and not (left & right):
        return "DISJOINT"
    if not left or not right or left < right or right < left:
        return "NESTED"
    return "PARTIAL"


def _scope_set(values: Iterable[str]) -> frozenset[str]:
    return frozenset(_norm(item) for item in values if str(item).strip())


# ---------------------------------------------------------------------------
# Notice governance (Section 13.4) — delegated to the frozen module
# ---------------------------------------------------------------------------

def _notice_governs(left_claim: ClaimUnit, right_claim: ClaimUnit,
                    notices: tuple[NoticeRecord, ...],
                    claim_documents: Mapping[str, str] | None) -> bool:
    pair = {left_claim.claim_id, right_claim.claim_id}
    for notice in notices:
        if notice.scope_kind == "WHOLE_DOCUMENT_SCOPE":
            if claim_documents is None:
                raise ValueError("whole-document notice requires a claim-to-document mapping")
            if any(claim_documents.get(claim_id) == notice.target_document_id
                   for claim_id in pair):
                return True
        elif set(notice.target_claim_ids) & pair:
            return True
    return False


# ---------------------------------------------------------------------------
# relate
# ---------------------------------------------------------------------------

def relate(left_claim: ClaimUnit, right_claim: ClaimUnit,
           notices: Iterable[NoticeRecord] = (), frames: Iterable[TemporalFrame] = (), *,
           identity: SubjectIdentity | None = None,
           definitions: Iterable[DefinitionEvidence] = (),
           claim_documents: Mapping[str, str] | None = None,
           dispute: IdentityDispute | None = None,
           left_lifecycle: str | None = None,
           right_lifecycle: str | None = None,
           left_organizational_scope: Iterable[str] = (),
           right_organizational_scope: Iterable[str] = (),
           exclusive_value_sets: Iterable[Iterable[str]] = (),
           language: str = "en") -> RelationAssessment:
    """Classify the relation between two claims under the Section 13 order.

    Positional and keyword arguments up to ``claim_documents`` are those of
    ``v5_1.contradiction.relate`` and mean the same thing; the return value is
    a V5.1 ``RelationAssessment`` so downstream consumers are unaffected.

    The additional keywords carry the evidence the repaired order needs:
    ``dispute`` is the only license for ``IDENTITY_DISAGREEMENT``,
    ``left_lifecycle``/``right_lifecycle`` override the derived lifecycle
    dimension, the organizational scopes complete the Section 13.1 step 5 test
    that V5.1 ran on geography alone, and ``exclusive_value_sets`` lets a
    caller declare a vocabulary whose members cannot co-occur.
    """
    if left_claim.claim_id == right_claim.claim_id:
        raise ValueError("relation requires two distinct claims")
    for claim in (left_claim, right_claim):
        if claim.polarity not in POLARITIES:
            raise ValueError(f"claim polarity outside shared vocabulary: {claim.polarity}")
        if claim.modality not in MODALITIES:
            raise ValueError(f"claim modality outside shared vocabulary: {claim.modality}")

    notice_records = tuple(notices)
    frame_records = tuple(frames.values() if isinstance(frames, Mapping) else frames)

    # Step 7, hoisted (Section 13.4): an explicit editorial act settles
    # precedence for the whole pair and is decided by the frozen V5.1 path, so
    # notice handling, supersession scope and claim versioning are unchanged
    # by this repair rather than reimplemented beside it.
    if _notice_governs(left_claim, right_claim, notice_records, claim_documents):
        return _v5_1_relate(left_claim, right_claim, notice_records, frame_records,
                            claim_documents=claim_documents)

    frame_index: dict[str, TemporalFrame] = {}
    for frame in frame_records:
        if frame.claim_id in frame_index:
            raise ValueError("duplicate temporal frame for one claim")
        frame_index[frame.claim_id] = frame
    left_frame = frame_index.get(left_claim.claim_id) or temporal_frame(
        claim_id=left_claim.claim_id, event_time=left_claim.temporal_scope)
    right_frame = frame_index.get(right_claim.claim_id) or temporal_frame(
        claim_id=right_claim.claim_id, event_time=right_claim.temporal_scope)

    # --- step 1: same entity ------------------------------------------------
    left_subject, right_subject = _norm(left_claim.subject), _norm(right_claim.subject)
    identity_evidence: tuple[EvidenceRef, ...] = ()
    identity_blocker: str | None = None

    if dispute is not None:
        if not _dispute_applies(dispute, left_claim, right_claim):
            raise ValueError("identity dispute does not cover this claim pair")
        return _assessment(
            left_claim, right_claim, "IDENTITY_DISAGREEMENT",
            "two sources resolve the shared designator to different entities; the "
            "disagreement is about which entity is meant, not about the values",
            basis="NOT_COMPARED", evidence=dispute.evidence,
            identity_blocker=(
                f"the designator {dispute.designator!r} is resolved to "
                f"{dispute.left_entity_id} by one source and to "
                f"{dispute.right_entity_id} by the other"),
            outcome_state="RESOLVED_WITH_MATERIAL_QUALIFICATION",
            qualifications=("DISPUTED",))

    if left_subject == right_subject:
        identity_state = _SAME_ENTITY
    elif identity is None:
        # The V5.1 defect, closed: differently named subjects with no identity
        # resolution are two entities as far as the record shows, and claims
        # about two entities cannot conflict.  The absence of a resolution is
        # recorded as a qualification rather than promoted to a conflict class.
        return _assessment(
            left_claim, right_claim, "NO_CONFLICT",
            "the claims name different subjects and no identity resolution licenses "
            "reading them as one entity; claims about different entities share no "
            "proposition and cannot conflict",
            basis="NOT_COMPARED",
            outcome_state="RESOLVED_WITH_MATERIAL_QUALIFICATION",
            qualifications=("UNRESOLVED",))
    else:
        covered = ({_norm(identity.left_subject), _norm(identity.right_subject)} ==
                   {left_subject, right_subject})
        if not covered:
            raise ValueError("identity link does not cover this claim pair")
        identity_evidence = identity.evidence
        if identity.outcome == "SAME_ENTITY_ACCEPTED":
            identity_state = _SAME_ENTITY
        elif identity.outcome == "DIFFERENT_ENTITY":
            return _assessment(
                left_claim, right_claim, "NO_CONFLICT",
                "the subjects are resolved to different entities; the claims do not "
                "share a proposition and cannot conflict",
                basis="NOT_COMPARED", evidence=identity_evidence)
        elif identity.outcome == "AMBIGUOUS" and identity_evidence:
            # An assessment that ran and came back conflicting is a recorded
            # disagreement about identity, which is what the class describes.
            return _assessment(
                left_claim, right_claim, "IDENTITY_DISAGREEMENT",
                "the identity assessment over the two subjects returned a recorded "
                "conflict; the sources disagree about which entity is meant",
                basis="NOT_COMPARED", evidence=identity_evidence,
                identity_blocker=(
                    "identity evidence covering both subjects is internally "
                    "conflicting, so neither a merge nor a separation is licensed"),
                outcome_state="RESOLVED_WITH_MATERIAL_QUALIFICATION",
                qualifications=("DISPUTED",))
        else:
            identity_state = _ASSESSED_AMBIGUOUS
            identity_blocker = (
                f"subject identity between the claims was assessed and returned "
                f"{identity.outcome}; no conflict class may be forced before "
                "identity is resolved")

    # --- step 2: same proposition ------------------------------------------
    if _norm(left_claim.predicate) != _norm(right_claim.predicate):
        return _assessment(
            left_claim, right_claim, "NO_CONFLICT",
            "the claims predicate different aspects of the subject; there is no "
            "shared proposition to conflict", basis="NOT_COMPARED",
            evidence=identity_evidence)

    # A conditional claim does not assert its proposition outright, so there is
    # no shared assertion for the categorical claim to contradict.
    if (left_claim.modality == "CONDITIONAL") != (right_claim.modality == "CONDITIONAL"):
        return _assessment(
            left_claim, right_claim, "QUALIFICATION",
            "one claim is conditional and does not assert the fact outright; it "
            "qualifies the categorical claim without conflicting",
            basis="NOT_COMPARED", evidence=identity_evidence)

    value_status = _compare_values(left_claim.object_or_value, right_claim.object_or_value)
    if value_status in {"UNIT_UNINTERPRETED", "DIMENSION_MISMATCH"}:
        # Preserved from V5.1: an interpretation failure is a defect of this
        # software, never a bare unknown and never a silent NO_CONFLICT.
        return _assessment(
            left_claim, right_claim, "UNRESOLVED",
            "both values are numeric but their units or multipliers cannot be "
            "normalized to a common dimension; the evidence suffices for comparison "
            "yet the system cannot interpret it", basis="NOT_COMPARED",
            outcome_state="SYSTEM_CAPABILITY_FAILURE", failure_class="SEMANTIC_TYPE_ERROR")

    polarity_same = left_claim.polarity == right_claim.polarity
    compatible_values = polarity_same and value_status == "EQUAL"

    event_relation = left_frame.event_relation(right_frame)
    basis = _EVENT_BASIS[event_relation]

    # --- step 3: compatible definitions ------------------------------------
    # Ordered ahead of the value comparison on purpose: when a shared term is
    # defined differently the two values are not comparable at all, so an
    # apparent agreement between them is not evidence of agreement either.
    matching_definitions = [item for item in definitions
                            if _definition_applies(item, left_claim, right_claim)]
    if matching_definitions:
        evidence = tuple(ref for item in matching_definitions for ref in item.evidence)
        terms = ", ".join(sorted(_norm(item.term) for item in matching_definitions))
        return _assessment(
            left_claim, right_claim, "DEFINITION_DIFFERENCE",
            f"the claims use divergent recorded definitions of: {terms}; their values "
            "are not comparable and no contradiction is licensed",
            basis=basis, evidence=evidence + identity_evidence)

    # --- step 4: overlapping valid intervals --------------------------------
    if not compatible_values:
        if event_relation == "DISJOINT" and identity_state == _SAME_ENTITY:
            return _assessment(
                left_claim, right_claim, "TEMPORAL_UPDATE",
                "the same subject and predicate carry different values over disjoint "
                "event periods; both values may be true of their own periods",
                basis=basis, evidence=identity_evidence)
        if event_relation in {"LEFT_WITHIN_RIGHT", "RIGHT_WITHIN_LEFT"}:
            return _assessment(
                left_claim, right_claim, "SCOPE_DIFFERENCE",
                "the values differ across nested temporal scopes; each may be true of "
                "its own period", basis=basis, evidence=identity_evidence)

    # --- step 5: compatible scopes (geographic and organizational) ----------
    scopes = (
        ("geographic", _scope_set(left_claim.geographic_scope),
         _scope_set(right_claim.geographic_scope)),
        ("organizational", _scope_set(left_organizational_scope),
         _scope_set(right_organizational_scope)),
    )
    for dimension, left_scope, right_scope in scopes:
        relation = _scope_relation(left_scope, right_scope)
        if relation == "DISJOINT":
            return _assessment(
                left_claim, right_claim, "NO_CONFLICT",
                f"the claims cover disjoint {dimension} scopes and can both hold",
                basis="NOT_COMPARED", evidence=identity_evidence)
        if relation == "NESTED":
            if compatible_values:
                return _assessment(
                    left_claim, right_claim, "QUALIFICATION",
                    f"the narrower-{dimension}-scope claim restates the broader claim's "
                    "value for a sub-scope; it qualifies without conflicting",
                    basis="NOT_COMPARED", evidence=identity_evidence)
            return _assessment(
                left_claim, right_claim, "SCOPE_DIFFERENCE",
                f"the values differ across nested {dimension} scopes; each may be true "
                "of its own scope", basis="NOT_COMPARED", evidence=identity_evidence)
        if relation == "PARTIAL" and not compatible_values:
            return _assessment(
                left_claim, right_claim, "SCOPE_DIFFERENCE",
                f"the values differ across partially overlapping {dimension} scopes",
                basis="NOT_COMPARED", evidence=identity_evidence)

    if compatible_values:
        return _assessment(
            left_claim, right_claim, "NO_CONFLICT",
            "the claims assert the same value for the same proposition; a temporal "
            "update requires an actual value difference, publication recency alone "
            "changes nothing", basis=basis, evidence=identity_evidence)

    # --- step 6: same lifecycle dimension -----------------------------------
    # The addition this repair exists for.  A statement of intent and a
    # statement of delivery about one object are two points on one lifecycle,
    # not two competing values, and two lifecycle branches are two dimensions.
    left_state = _lifecycle_state(left_claim, left_lifecycle, language)
    right_state = _lifecycle_state(right_claim, right_lifecycle, language)
    if "UNKNOWN" not in {left_state, right_state} and left_state != right_state:
        if comparable(left_state, right_state):
            stronger, weaker = ((left_state, right_state)
                                if entails(left_state, right_state)
                                else (right_state, left_state))
            return _assessment(
                left_claim, right_claim, "NO_CONFLICT",
                f"the claims sit on one lifecycle progression: {stronger} presupposes "
                f"{weaker}, so the weaker claim is entailed by the stronger and does "
                "not compete with it", basis=basis, evidence=identity_evidence)
        return _assessment(
            left_claim, right_claim, "SCOPE_DIFFERENCE",
            f"the claims report different lifecycle dimensions ({left_state} and "
            f"{right_state}, which lie on different branches and neither of which "
            "presupposes the other); each may be true of its own dimension",
            basis=basis, evidence=identity_evidence)

    # --- step 8: preliminary versus final ------------------------------------
    if {left_claim.modality, right_claim.modality} == {"PRELIMINARY", "FINAL"}:
        return _assessment(
            left_claim, right_claim, "QUALIFICATION",
            "one claim states a preliminary value and the other the final one; a "
            "provisional figure qualifies the final figure rather than competing "
            "with it", basis=basis, evidence=identity_evidence,
            outcome_state="RESOLVED_WITH_MATERIAL_QUALIFICATION",
            qualifications=("PRELIMINARY",))

    # --- conflict classes (Section 13.2) ------------------------------------
    # Everything above has been answered; only now may a conflict be asserted.
    if identity_state == _ASSESSED_AMBIGUOUS:
        return _assessment(
            left_claim, right_claim, "UNRESOLVED",
            "the claims would conflict only under a same-entity reading, and the "
            "identity assessment could not establish one", basis="NOT_COMPARED",
            identity_blocker=identity_blocker, evidence=identity_evidence,
            outcome_state="EPISTEMICALLY_UNRESOLVABLE",
            demonstration=(
                "The claims share a predicate and an object dimension, and the "
                "definition, valid-interval, scope and lifecycle tests were run and "
                "none of them separates the claims. What is missing is subject "
                "identity: an assessment over the two subjects was performed and "
                "returned neither a licensed merge nor a supported separation, so no "
                "public identity evidence in the record settles whether one entity or "
                "two are being described. No correction, retraction or supersession "
                "notice governs the pair either."))

    if event_relation == "UNKNOWN":
        return _assessment(
            left_claim, right_claim, "UNRESOLVED",
            "every earlier test has been run and none separates the claims, but "
            "without event-time evidence a temporal update cannot be told apart from "
            "a disagreement", basis=basis, evidence=identity_evidence,
            outcome_state="EPISTEMICALLY_UNRESOLVABLE",
            demonstration=(
                "The claims share an entity, a predicate and an object dimension; the "
                "definition, scope, lifecycle and preliminary-versus-final tests were "
                "all run and none of them separates the claims. What is missing is "
                "event-time evidence: neither claim nor its temporal frame records the "
                "period its value is about, and publication order cannot substitute "
                "for event time. No correction, retraction or supersession notice "
                "governs the pair, so no editorial act settles precedence either."))

    if not polarity_same:
        return _assessment(
            left_claim, right_claim, "POLARITY_CONFLICT",
            "the claims assert opposite polarity for the same proposition, scope, "
            "lifecycle dimension and event time", basis=basis, evidence=identity_evidence)

    exclusivity = _mutually_exclusive(left_claim.object_or_value,
                                      right_claim.object_or_value, exclusive_value_sets)
    if exclusivity is not None:
        return _assessment(
            left_claim, right_claim, "LOGICAL_CONTRADICTION",
            f"the values cannot both be true under any reading: {exclusivity}; the "
            "claims share an entity, predicate, scope, lifecycle dimension and event "
            "time", basis=basis, evidence=identity_evidence)

    if value_status == "DIFFERENT_NUMERIC":
        return _assessment(
            left_claim, right_claim, "NUMERIC_DISAGREEMENT",
            "the unit-normalized measured quantities disagree for the same "
            "proposition, scope, lifecycle dimension and event time; the divergence "
            "could in principle be reconciled by measurement",
            basis=basis, evidence=identity_evidence)

    return _assessment(
        left_claim, right_claim, "SOURCE_DISAGREEMENT",
        "the sources assert incompatible categorical values for the same "
        "proposition, scope, lifecycle dimension and overlapping validity, and no "
        "correction or supersession settles precedence between them",
        basis=basis, evidence=identity_evidence)


# ---------------------------------------------------------------------------
# Class coverage (Section 13.3)
# ---------------------------------------------------------------------------

def relation_class_coverage(
        assessments: Iterable[RelationAssessment | Mapping[str, object] | str]
        ) -> dict[str, object]:
    """Report which relation classes a population exercised, and which not.

    V5.1 shipped a classifier that could reach six of fourteen classes over a
    20 527-relation population and three over the held-out corpus, and nothing
    measured that.  This is the measurement.
    """
    counts = {name: 0 for name in sorted(RELATION_CLASSES)}
    total = 0
    for item in assessments:
        if isinstance(item, str):
            relation = item
        elif isinstance(item, Mapping):
            relation = str(item.get("relation", ""))
        else:
            relation = str(getattr(item, "relation", ""))
        if relation not in RELATION_CLASSES:
            raise ValueError(f"unknown relation class: {relation!r}")
        counts[relation] += 1
        total += 1
    exercised = tuple(name for name in sorted(RELATION_CLASSES) if counts[name])
    missing = tuple(name for name in sorted(RELATION_CLASSES) if not counts[name])
    return {
        "assessments": total,
        "classes_required": len(RELATION_CLASSES),
        "classes_exercised": len(exercised),
        "exercised": exercised,
        "missing": missing,
        "coverage_complete": not missing,
        "counts": counts,
        "notice_classes_exercised": tuple(
            name for name in sorted(_NOTICE_CLASSES) if counts[name]),
        "triggers_for_missing": {name: RELATION_TRIGGERS[name] for name in missing},
    }
