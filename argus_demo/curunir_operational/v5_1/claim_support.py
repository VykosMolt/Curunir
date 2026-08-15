"""Claim-support capability for V5.1 (contract Section 15).

Type-level closure of the V5 claim-support failure modes:
TEMPORAL_SCOPE_DROPPED (a time-bounded observation can never silently
fully support a temporally broader claim), POLARITY_FLATTENED (polarity
mismatch is always WRONG_POLARITY, never a silent flip), and
ATTRIBUTED_MODALITY_FLATTENED (attributed/reported evidence can never
support an unqualified factual claim).  One general modality ladder
closes vendor-claim-as-fact, plan-as-implementation and
exercise-as-deployment; retracted or superseded evidence is blocked and
never counted; absence-of-evidence statements support only the absence
statement itself, never the negation of the underlying fact.

Interpretation failure raises ValueError (or is recorded as
SYSTEM_CAPABILITY_FAILURE via ``guarded_assess_support``); it never
degrades to a silent unknown.

Research shadow only.  Composes frozen V4 records; never edits them.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Iterable, Mapping

from .models import (
    EVIDENCE_LIFECYCLE_STATES, MATERIAL_QUALIFICATION_MARKERS, MODALITIES,
    POLARITIES, REQUIRED_CLAIM_CLASSES, SUPPORT_STATES, CapabilityOutcome,
    Record, capability_outcome, now_utc, stable_id,
)


def _require_subset(subset: frozenset[str], vocabulary: frozenset[str] | tuple[str, ...],
                    label: str) -> frozenset[str]:
    missing = subset - frozenset(vocabulary)
    if missing:
        raise ValueError(f"{label} outside models vocabulary: {sorted(missing)}")
    return subset


# Support states that may ever be accepted for reporting; everything else
# is a refusal or a detected mismatch and is unacceptable by construction.
ACCEPTABLE_SUPPORT_STATES = _require_subset(frozenset({
    "FULL_SUPPORT", "QUALIFIED_SUPPORT", "PARTIAL_SUPPORT",
    "CONTEXT_DEPENDENT_SUPPORT",
}), SUPPORT_STATES, "acceptable support states")

# Source class whose statements are self-descriptions of the speaking
# vendor; their effective modality is capped at VENDOR_DESCRIBED so a
# vendor statement can never become unqualified fact.
VENDOR_SELF_DESCRIPTION = "VENDOR_SELF_DESCRIPTION"

# Alias of the shared lifecycle vocabulary (models.py is the single
# authority); RETRACTED/SUPERSEDED evidence is always blocked.
EVIDENCE_CORRECTION_STATES = EVIDENCE_LIFECYCLE_STATES
BLOCKED_CORRECTION_STATES = frozenset({"RETRACTED", "SUPERSEDED"})

# --- Modality ladders (Section 15) ------------------------------------
# Implementation ladder: VENDOR_DESCRIBED < PLANNED/PROPOSED/INTENDED
# < EXERCISED/PILOTED < DEPLOYED < OPERATIONAL.  A claim higher on the
# ladder than its evidence is WRONG_MODALITY; this single mechanism
# closes vendor->fact, plan->implementation and exercise->deployment.
_IMPLEMENTATION_RANK: Mapping[str, int] = {
    "VENDOR_DESCRIBED": 1, "PLANNED": 2, "PROPOSED": 2, "INTENDED": 2,
    "EXERCISED": 3, "PILOTED": 3, "DEPLOYED": 4, "OPERATIONAL": 5,
}
_IMPLEMENTATION_MAX = 6  # ASSERTED evidence states the content as direct fact

# Attribution ladder: what factual strength a claim demands, and what an
# evidence modality offers toward it.  REPORTED < ASSERTED: reported
# evidence never licenses an unqualified factual claim.
_ATTRIBUTION_DEMAND: Mapping[str, int] = {
    "ASSERTED": 3, "FINAL": 3, "REPORTED": 2, "ATTRIBUTED": 2,
    "PRELIMINARY": 2, "ALLEGED": 1,
}
_ATTRIBUTION_OFFER: Mapping[str, int] = {
    "ASSERTED": 3, "EXERCISED": 3, "PILOTED": 3, "DEPLOYED": 3,
    "OPERATIONAL": 3, "FINAL": 3, "REQUIRED_BY_LAW": 3, "ALLEGED": 1,
}
_ATTRIBUTION_OFFER_DEFAULT = 2  # prospective / attributed / contested modalities

# Evidence modalities whose support is only ever qualified support; the
# marker travels with the assessment so downstream rendering can never
# flatten it (closes ATTRIBUTED_MODALITY_FLATTENED at the record level).
_EVIDENCE_MODALITY_MARKER: Mapping[str, str] = {
    "PLANNED": "PLANNED", "PROPOSED": "PROPOSED", "INTENDED": "INTENDED",
    "REPORTED": "REPORTED", "ATTRIBUTED": "REPORTED", "ALLEGED": "REPORTED",
    "VENDOR_DESCRIBED": "VENDOR_DESCRIBED", "EXERCISED": "EXERCISED",
    "PILOTED": "PILOTED", "DISPUTED": "DISPUTED", "PRELIMINARY": "PRELIMINARY",
}
_require_subset(frozenset(_EVIDENCE_MODALITY_MARKER.values()),
                MATERIAL_QUALIFICATION_MARKERS, "modality markers")

_CONTEXTUAL_MODALITIES = frozenset({"CONDITIONAL", "PREDICTED"})

# --- General multilingual surface patterns (never case-specific) -------

def _patterns(*expressions: str) -> tuple[re.Pattern[str], ...]:
    return tuple(re.compile(item, re.IGNORECASE) for item in expressions)


_ABSENCE_PATTERNS = _patterns(
    r"\bno (?:public\s+)?(?:evidence|record|indication|trace)s?\b",
    r"\babsence of (?:evidence|record)s?\b", r"\bnot (?:documented|recorded)\b",
    r"\baucune? (?:preuve|trace|élément|document)s?\b",
    r"\bkeine?n? (?:hinweise?|belege?|nachweise?|aufzeichnungen)\b",
    r"\bsin (?:pruebas|indicios|registros)\b", r"\bno consta\b",
    r"\bnessuna (?:prova|traccia)\b", r"\bgeen bewijs\b",
)
_HEDGE_PATTERNS = _patterns(
    r"\bmay\s+(?:be|have|not)\b", r"\bmight\b", r"\bpossibl\w*", r"\bunclear\b", r"\buncertain\b",
    r"\bappears? to\b", r"\bmöglicherweise\b", r"\bunklar\b", r"\bpeut-être\b",
    r"\bincertain\w*", r"\bposiblemente\b", r"\bincierto\b", r"\bpodría\b",
)
_LEGAL_PATTERNS = _patterns(
    r"\b(?:law|statute|regulation|directive|ruling|court|tribunal|illegal|prohibit\w*)\b",
    r"\b(?:gesetz\w*|verordnung|gericht\w*|urteil|verboten)\b",
    r"\b(?:loi|règlement|tribunal|interdit\w*|décret)\b",
    r"\b(?:ley|reglamento|prohibido|sentencia)\b",
)
_DEPLOYMENT_PATTERNS = _patterns(
    r"\bdeploy\w*\b", r"\brolled? out\b", r"\bin (?:operation|production)\b",
    r"\boperational\b", r"\b(?:eingesetzt|im einsatz|in betrieb|ausgerollt)\b",
    r"\b(?:déployé\w*|en service|mis en (?:œuvre|oeuvre)|en production)\b",
    r"\b(?:desplegad\w+|en funcionamiento|en producción)\b",
)
_PROCUREMENT_PATTERNS = _patterns(
    r"\bprocure\w*\b", r"\btender\w*\b", r"\bcontract (?:award\w*|sign\w*)\b",
    r"\bpurchas\w*\b", r"\bacquisition of\b", r"\bframework agreement\b",
    r"\b(?:beschafft\w*|ausschreibung|vergabe|auftrag vergeben)\b",
    r"\b(?:marché public|appel d'offres|contrat attribué)\b",
    r"\b(?:licitación|adjudicación|contrato adjudicado)\b",
)
_OWNERSHIP_PATTERNS = _patterns(
    r"\bowns?\b", r"\bowned by\b", r"\bsubsidiary\b", r"\bparent company\b",
    r"\bgoverns?\b", r"\bcontrols?\b", r"\boperated by\b", r"\boversees\b",
    r"\b(?:gehört|tochtergesellschaft|kontrolliert|betrieben von)\b",
    r"\b(?:appartient|filiale|contrôle|exploité par)\b",
    r"\b(?:pertenece|filial|controla|operado por)\b",
)
_CAPABILITY_PATTERNS = _patterns(
    r"\bcan\b", r"\bcapab\w*\b", r"\bis able to\b", r"\benables?\b",
    r"\b(?:kann|fähig|ermöglicht)\b", r"\b(?:capacité|capable|permet)\b",
    r"\b(?:capaz|permite)\b",
)
_PROGRAMME_PATTERNS = _patterns(
    r"\bestablish\w*\b", r"\blaunch\w*\b", r"\bset up\b", r"\bcreated?\b",
    r"\binitiated\b", r"\bfounded\b", r"\bexists?\b",
    r"\b(?:eingerichtet|gegründet|ins leben gerufen|gestartet)\b",
    r"\b(?:lancé\w*|créé\w*|mis en place|établi\w*)\b",
    r"\b(?:establecid\w+|lanzad\w+|cread\w+|puesta? en marcha)\b",
)
_CAUSAL_PATTERNS = _patterns(
    r"\bcaused?\b", r"\bled to\b", r"\bresult\w* in\b", r"\bbecause of\b",
    r"\bdue to\b", r"\btriggered\b", r"\b(?:verursacht\w*|führte zu|aufgrund|infolge)\b",
    r"\b(?:a causé|entraîné|en raison de|provoqué)\b",
    r"\b(?:causó|provocó|debido a|dio lugar)\b",
)
_TIMING_PATTERNS = _patterns(
    r"\b(?:began|started|commenced|scheduled|postponed|delayed)\b",
    r"\b(?:begann|verschoben|seit \d{4}|bis \d{4})\b",
    r"\b(?:a commencé|reporté|depuis \d{4}|jusqu'à \d{4})\b",
    r"\b(?:comenzó|aplazado|desde \d{4}|hasta \d{4})\b",
)
_DATE_VALUE = re.compile(
    r"^\d{4}(?:-\d{2}){0,2}(?:\s*(?:to|–|—|/)\s*\d{4}(?:-\d{2}){0,2})?$")
_TEMPORAL_BOUND = re.compile(r"^(\d{4})(?:-(\d{2}))?(?:-(\d{2}))?(?:[T ].*)?$")


def _matches(text: str, patterns: tuple[re.Pattern[str], ...]) -> bool:
    return any(pattern.search(text) for pattern in patterns)


# --- Typed carriers (duck-typed mappings and V4 ClaimUnit also accepted)

@dataclass(frozen=True)
class SemanticClaim(Record):
    """A claim's semantic fields as this capability interprets them."""

    claim_id: str
    statement: str
    subject: str
    predicate: str
    object_or_value: str
    polarity: str
    modality: str
    temporal_scope: tuple[str | None, str | None] = (None, None)
    geographic_scope: tuple[str, ...] = ()
    entity_ids: tuple[str, ...] = ()
    attribution: str = ""
    epistemic_state: str = "EXTRACTED"
    qualifies_claim_id: str | None = None

    def __post_init__(self) -> None:
        if not self.claim_id.strip() or not self.subject.strip():
            raise ValueError("claim requires claim_id and subject")
        if not (self.statement.strip() or self.predicate.strip()):
            raise ValueError("claim semantics uninterpretable: no statement or predicate")
        if self.polarity not in POLARITIES:
            raise ValueError(f"uninterpretable claim polarity: {self.polarity!r}")
        if self.modality not in MODALITIES:
            raise ValueError(f"uninterpretable claim modality: {self.modality!r}")


@dataclass(frozen=True)
class EvidenceStatement(Record):
    """One evidence item's own semantic parse, with lifecycle state."""

    evidence_id: str
    statement: str
    subject: str
    predicate: str
    object_or_value: str
    polarity: str
    modality: str
    temporal_scope: tuple[str | None, str | None] = (None, None)
    geographic_scope: tuple[str, ...] = ()
    entity_ids: tuple[str, ...] = ()
    source_class: str = "PUBLIC_RECORD"
    correction_state: str = "CURRENT"
    active: bool = True
    absence_statement: bool = False

    def __post_init__(self) -> None:
        if not self.evidence_id.strip():
            raise ValueError("evidence requires evidence_id")
        if not (self.subject.strip() or self.entity_ids):
            raise ValueError("evidence entity uninterpretable: no subject or entity ids")
        if self.polarity not in POLARITIES:
            raise ValueError(f"uninterpretable evidence polarity: {self.polarity!r}")
        if self.modality not in MODALITIES:
            raise ValueError(f"uninterpretable evidence modality: {self.modality!r}")
        if self.correction_state not in EVIDENCE_CORRECTION_STATES:
            raise ValueError(f"uninterpretable correction state: {self.correction_state!r}")
        if not self.source_class.strip():
            raise ValueError("evidence requires a source class")


def _get(item: Any, name: str, default: Any = None) -> Any:
    if isinstance(item, Mapping):
        return item.get(name, default)
    return getattr(item, name, default)


def _text(item: Any, name: str) -> str:
    value = _get(item, name, "")
    return str(value).strip() if value is not None else ""


def _vocab(value: str, vocabulary: frozenset[str], label: str) -> str:
    if value not in vocabulary:
        raise ValueError(f"uninterpretable {label}: {value!r}")
    return value


def _temporal(item: Any) -> tuple[str | None, str | None]:
    scope = _get(item, "temporal_scope", None)
    if scope is None:
        scope = _get(item, "temporal", (None, None))
    scope = tuple(scope or (None, None))
    if len(scope) != 2:
        raise ValueError("temporal scope must be a (start, end) pair")
    return scope  # type: ignore[return-value]


def _bound(value: str | None, *, end: bool) -> tuple[int, int, int] | None:
    if value in (None, ""):
        return None
    match = _TEMPORAL_BOUND.match(str(value).strip())
    if not match:
        raise ValueError(f"temporal bound uninterpretable: {value!r}")
    month = int(match.group(2)) if match.group(2) else (12 if end else 1)
    day = int(match.group(3)) if match.group(3) else (31 if end else 1)
    return (int(match.group(1)), month, day)


def _geo(item: Any) -> frozenset[str]:
    return frozenset(str(part).casefold().strip()
                     for part in (_get(item, "geographic_scope", ()) or ()) if str(part).strip())


def _entities(item: Any) -> frozenset[str]:
    return frozenset(str(part).casefold().strip()
                     for part in (_get(item, "entity_ids", ()) or ()) if str(part).strip())


def _norm_subject(item: Any) -> str:
    return " ".join(_text(item, "subject").casefold().split())


def _is_absence_statement(item: Any) -> bool:
    if bool(_get(item, "absence_statement", False)):
        return True
    combined = f"{_text(item, 'statement')} {_text(item, 'predicate')}"
    return _matches(combined, _ABSENCE_PATTERNS)


# --- Claim classification ----------------------------------------------

def classify_claim(claim_like: Any) -> str:
    """Deterministic ClaimClass tag from the claim's semantic fields.

    Rules test modality, polarity, numeric/date content and multilingual
    predicate patterns in a fixed priority order; unknown vocabulary
    raises rather than defaulting.
    """
    polarity = _vocab(_text(claim_like, "polarity") or "POSITIVE", POLARITIES, "claim polarity")
    modality = _vocab(_text(claim_like, "modality") or "ASSERTED", MODALITIES, "claim modality")
    statement, predicate = _text(claim_like, "statement"), _text(claim_like, "predicate")
    object_or_value = _text(claim_like, "object_or_value")
    if not statement and not predicate:
        raise ValueError("claim semantics uninterpretable: no statement or predicate")
    content = f"{statement} {predicate}"

    if _text(claim_like, "epistemic_state") == "INFERRED" or bool(_get(claim_like, "inferred", False)):
        return "INFERENCE"
    if _text(claim_like, "qualifies_claim_id"):
        return "QUALIFICATION"
    if _is_absence_statement(claim_like):
        return "ABSENCE_OF_EVIDENCE_STATEMENT"
    if modality in {"PREDICTED", "CONDITIONAL", "DISPUTED"} or _matches(content, _HEDGE_PATTERNS):
        return "UNCERTAINTY"
    if polarity == "NEGATIVE":
        return "NEGATIVE_CLAIM"
    if modality in {"REPORTED", "ATTRIBUTED", "ALLEGED"} or _text(claim_like, "attribution"):
        return "ATTRIBUTED_REPORT"
    if modality == "REQUIRED_BY_LAW" or _matches(content, _LEGAL_PATTERNS):
        return "LEGAL_OR_REGULATORY_CLAIM"
    if modality in {"DEPLOYED", "OPERATIONAL", "EXERCISED", "PILOTED"} or _matches(content, _DEPLOYMENT_PATTERNS):
        return "DEPLOYMENT_CLAIM"
    if _matches(content, _PROCUREMENT_PATTERNS):
        return "PROCUREMENT_CLAIM"
    if _matches(content, _OWNERSHIP_PATTERNS):
        return "OWNERSHIP_OR_GOVERNANCE_CLAIM"
    if modality == "VENDOR_DESCRIBED" or _matches(content, _CAPABILITY_PATTERNS):
        return "CAPABILITY_CLAIM"
    if _matches(content, _PROGRAMME_PATTERNS):
        return "PROGRAMME_EXISTENCE_CLAIM"
    if _matches(content, _CAUSAL_PATTERNS):
        return "CAUSAL_CLAIM"
    if (object_or_value and _DATE_VALUE.match(object_or_value)) or _matches(content, _TIMING_PATTERNS):
        return "TIMELINE_CLAIM"
    if re.search(r"\d", object_or_value):
        return "NUMERIC_CLAIM"
    return "DIRECT_FACTUAL_ASSERTION"


# --- Support assessment -------------------------------------------------

@dataclass(frozen=True)
class SupportAssessment(Record):
    """One adjudicated claim-support relation.

    Wrong-* states, contradiction, inference-only and not-supported can
    never be accepted for reporting; blocked (retracted/superseded)
    evidence can never appear among the supporting items.
    """

    assessment_id: str
    claim_id: str
    claim_class: str
    state: str
    evidence_ids: tuple[str, ...]
    supporting_evidence_ids: tuple[str, ...]
    blocked_evidence_ids: tuple[str, ...]
    retracted_support_blocked: bool
    required_qualifications: tuple[str, ...]
    modality_violations: tuple[tuple[str, str, str], ...]
    per_item_states: tuple[tuple[str, str], ...]
    accepted_for_reporting: bool
    rationale: str
    recorded_time: str

    def __post_init__(self) -> None:
        if self.state not in SUPPORT_STATES:
            raise ValueError(f"unknown support state: {self.state}")
        if self.claim_class not in REQUIRED_CLAIM_CLASSES:
            raise ValueError(f"unknown claim class: {self.claim_class}")
        if not self.claim_id.strip() or not self.rationale.strip():
            raise ValueError("assessment requires claim id and rationale")
        known = set(self.evidence_ids)
        if not set(self.supporting_evidence_ids) <= known or not set(self.blocked_evidence_ids) <= known:
            raise ValueError("assessment references evidence outside its input set")
        if set(self.supporting_evidence_ids) & set(self.blocked_evidence_ids):
            raise ValueError("retracted or superseded evidence can never be counted as support")
        if self.retracted_support_blocked != bool(self.blocked_evidence_ids):
            raise ValueError("retracted_support_blocked marker must match blocked evidence")
        invalid = set(self.required_qualifications) - MATERIAL_QUALIFICATION_MARKERS
        if invalid:
            raise ValueError(f"qualifications outside marker vocabulary: {sorted(invalid)}")
        if self.state == "QUALIFIED_SUPPORT" and not self.required_qualifications:
            raise ValueError("qualified support requires its material qualification markers")
        if self.state in ACCEPTABLE_SUPPORT_STATES and not self.supporting_evidence_ids:
            raise ValueError("support state requires supporting evidence")
        if self.accepted_for_reporting and self.state not in ACCEPTABLE_SUPPORT_STATES:
            raise ValueError(f"{self.state} can never be accepted for reporting")
        for evidence_id, state in self.per_item_states:
            if state not in SUPPORT_STATES or evidence_id not in known:
                raise ValueError("per-item states must use known evidence and support states")
        for evidence_id, claim_modality, evidence_modality in self.modality_violations:
            if (evidence_id not in known or claim_modality not in MODALITIES
                    or evidence_modality not in MODALITIES):
                raise ValueError("modality violation record is uninterpretable")


def _effective_evidence_modality(item: Any) -> str:
    modality = _vocab(_text(item, "modality") or "ASSERTED", MODALITIES, "evidence modality")
    if _text(item, "source_class") == VENDOR_SELF_DESCRIPTION:
        return "VENDOR_DESCRIBED"
    return modality


def _modality_violation(claim_class: str, claim_modality: str, evidence_modality: str) -> bool:
    if claim_modality == evidence_modality:
        return False
    claim_rank = _IMPLEMENTATION_RANK.get(claim_modality)
    evidence_rank = _IMPLEMENTATION_RANK.get(evidence_modality)
    demand = claim_rank
    # A deployment claim stated as bare fact demands deployment-grade evidence.
    if demand is None and claim_class == "DEPLOYMENT_CLAIM" \
            and _ATTRIBUTION_DEMAND.get(claim_modality, 0) >= 3:
        demand = _IMPLEMENTATION_RANK["DEPLOYED"]
    if demand is not None:
        if evidence_modality == "ASSERTED":
            offer = _IMPLEMENTATION_MAX
        elif evidence_rank is not None:
            offer = evidence_rank
        else:
            offer = 0
        if offer < demand:
            return True
        if claim_rank is not None and evidence_rank is not None:
            return False
    return _ATTRIBUTION_DEMAND.get(claim_modality, 0) > \
        _ATTRIBUTION_OFFER.get(evidence_modality, _ATTRIBUTION_OFFER_DEFAULT)


def _entity_mismatch(claim_like: Any, item: Any) -> bool:
    claim_entities, evidence_entities = _entities(claim_like), _entities(item)
    if claim_entities and evidence_entities:
        return not (claim_entities & evidence_entities)
    claim_subject, evidence_subject = _norm_subject(claim_like), _norm_subject(item)
    if not claim_subject or not evidence_subject:
        raise ValueError("entity comparison uninterpretable: subject missing")
    return not (claim_subject == evidence_subject
                or claim_subject in evidence_subject or evidence_subject in claim_subject)


def _assess_item(claim_like: Any, claim_class: str, claim_modality: str,
                 item: Any) -> tuple[str, frozenset[str], tuple[str, str] | None, tuple[str, ...]]:
    """Returns (state, markers, modality_violation_modalities, notes)."""
    notes: list[str] = []
    if _entity_mismatch(claim_like, item):
        return "WRONG_ENTITY", frozenset(), None, ("ENTITY_MISMATCH",)

    claim_start, claim_end = _temporal(claim_like)
    evidence_start, evidence_end = _temporal(item)
    cs, ce = _bound(claim_start, end=False), _bound(claim_end, end=True)
    es, ee = _bound(evidence_start, end=False), _bound(evidence_end, end=True)
    if (cs and ee and cs > ee) or (es and ce and es > ce):
        return "WRONG_TIME", frozenset(), None, ("TEMPORAL_SCOPE_DISJOINT",)

    claim_polarity = _vocab(_text(claim_like, "polarity") or "POSITIVE", POLARITIES, "claim polarity")
    evidence_polarity = _vocab(_text(item, "polarity") or "POSITIVE", POLARITIES, "evidence polarity")
    if claim_polarity != evidence_polarity:
        return "WRONG_POLARITY", frozenset(), None, ("POLARITY_MISMATCH_NEVER_FLIPPED",)

    evidence_is_absence = _is_absence_statement(item)
    if evidence_is_absence and claim_class != "ABSENCE_OF_EVIDENCE_STATEMENT":
        # Absence of evidence is never proof of the underlying negative.
        return "INFERENCE_ONLY", frozenset(), None, ("ABSENCE_IS_NOT_NEGATIVE_PROOF",)

    effective_modality = _effective_evidence_modality(item)
    if _modality_violation(claim_class, claim_modality, effective_modality):
        return ("WRONG_MODALITY", frozenset(), (claim_modality, effective_modality),
                ("MODALITY_LADDER_VIOLATION",))

    partial = False
    claim_geo, evidence_geo = _geo(claim_like), _geo(item)
    if claim_geo and evidence_geo:
        covered = claim_geo & evidence_geo
        if not covered or len(covered) * 2 < len(claim_geo):
            # Material scope test: minority coverage is not partial support.
            return "WRONG_SCOPE", frozenset(), None, ("GEOGRAPHIC_SCOPE_MISMATCH",)
        if claim_geo - evidence_geo:
            partial = True
            notes.append("GEOGRAPHIC_SCOPE_PARTIAL")
    if (es and (cs is None or cs < es)) or (ee and (ce is None or ce > ee)):
        # Claim temporally broader than the evidence: never full support.
        partial = True
        notes.append("TEMPORAL_SCOPE_NARROWER_THAN_CLAIM")

    markers: set[str] = set()
    if claim_class == "ABSENCE_OF_EVIDENCE_STATEMENT":
        if not evidence_is_absence:
            return "NOT_SUPPORTED", frozenset(), None, ("ABSENCE_CLAIM_NEEDS_ABSENCE_STATEMENT",)
        markers.add("UNRESOLVED")
        notes.append("SUPPORTED_ONLY_AS_ABSENCE_STATEMENT")
    if _text(item, "correction_state") == "CORRECTED":
        markers.add("CORRECTED")
    marker = _EVIDENCE_MODALITY_MARKER.get(effective_modality)
    if marker:
        markers.add(marker)

    if effective_modality in _CONTEXTUAL_MODALITIES:
        return "CONTEXT_DEPENDENT_SUPPORT", frozenset(markers), None, tuple(notes) or ("CONTEXT_BOUND",)
    if partial:
        return "PARTIAL_SUPPORT", frozenset(markers), None, tuple(notes)
    if markers:
        return "QUALIFIED_SUPPORT", frozenset(markers), None, tuple(notes) or ("MATERIALLY_QUALIFIED",)
    return "FULL_SUPPORT", frozenset(), None, tuple(notes) or ("ALL_DIMENSIONS_MATCH",)


_SUPPORT_PRECEDENCE = ("FULL_SUPPORT", "QUALIFIED_SUPPORT",
                       "CONTEXT_DEPENDENT_SUPPORT", "PARTIAL_SUPPORT")
_FAILURE_PRECEDENCE = ("WRONG_POLARITY", "WRONG_MODALITY", "WRONG_TIME",
                       "WRONG_ENTITY", "WRONG_SCOPE", "INFERENCE_ONLY", "NOT_SUPPORTED")


def assess_support(claim_like: Any, evidence_items: Iterable[Any]) -> SupportAssessment:
    """Adjudicate one claim against its offered evidence items.

    The propositional pairing of claim and evidence is the caller's
    input; this capability adjudicates the epistemic dimensions (entity,
    time, polarity, modality ladder, scope, lifecycle, absence).
    """
    claim_class = classify_claim(claim_like)
    claim_id = _text(claim_like, "claim_id")
    if not claim_id:
        raise ValueError("claim requires claim_id")
    claim_modality = _vocab(_text(claim_like, "modality") or "ASSERTED", MODALITIES, "claim modality")

    evidence_ids: list[str] = []
    blocked: list[str] = []
    per_item: list[tuple[str, str]] = []
    violations: list[tuple[str, str, str]] = []
    outcomes: dict[str, list[tuple[str, frozenset[str]]]] = {}
    notes: list[str] = []
    for item in evidence_items:
        evidence_id = _text(item, "evidence_id") or _text(item, "candidate_id")
        if not evidence_id:
            raise ValueError("evidence item requires an identifier")
        evidence_ids.append(evidence_id)
        correction_state = _vocab(_text(item, "correction_state") or "CURRENT",
                                  EVIDENCE_CORRECTION_STATES, "correction state")
        active = bool(_get(item, "active", True))
        if correction_state in BLOCKED_CORRECTION_STATES or not active:
            blocked.append(evidence_id)
            per_item.append((evidence_id, "NOT_SUPPORTED"))
            notes.append(f"{evidence_id}:RETRACTED_SUPPORT_BLOCKED")
            continue
        state, markers, violation, item_notes = _assess_item(
            claim_like, claim_class, claim_modality, item)
        per_item.append((evidence_id, state))
        if violation is not None:
            violations.append((evidence_id, violation[0], violation[1]))
        outcomes.setdefault(state, []).append((evidence_id, markers))
        notes.extend(f"{evidence_id}:{note}" for note in item_notes)

    supporting: tuple[str, ...] = ()
    best_support = next((state for state in _SUPPORT_PRECEDENCE if state in outcomes), None)
    if best_support is not None:
        supporting = tuple(evidence_id for evidence_id, _ in outcomes[best_support])
        if "WRONG_POLARITY" in outcomes:
            state = "CONTRADICTED"
            notes.append("SUPPORT_AND_OPPOSITE_POLARITY_EVIDENCE_COEXIST")
        else:
            state = best_support
    elif outcomes:
        state = next(state for state in _FAILURE_PRECEDENCE if state in outcomes)
    else:
        state = "NOT_SUPPORTED"
        notes.append("NO_ACTIVE_EVIDENCE")

    qualifications: tuple[str, ...] = ()
    if best_support is not None and state == best_support:
        merged: set[str] = set()
        for _, markers in outcomes[best_support]:
            merged |= markers
        qualifications = tuple(sorted(merged))

    return SupportAssessment(
        stable_id("support-assessment", claim_id, sorted(evidence_ids), state),
        claim_id, claim_class, state, tuple(evidence_ids), supporting, tuple(blocked),
        bool(blocked), qualifications, tuple(violations), tuple(per_item),
        state in ACCEPTABLE_SUPPORT_STATES,
        "; ".join([f"state={state}", f"class={claim_class}",
                   f"items={len(evidence_ids)}", *notes]) or "no evidence offered",
        now_utc())


def guarded_assess_support(claim_like: Any, evidence_items: Iterable[Any],
                           ) -> tuple[SupportAssessment | None, CapabilityOutcome | None]:
    """assess_support, with interpretation failure recorded, never swallowed."""
    try:
        return assess_support(claim_like, tuple(evidence_items)), None
    except ValueError as error:
        text = str(error).casefold()
        if "modality" in text:
            failure_class = "MODALITY_LOSS"
        elif "polarity" in text:
            failure_class = "POLARITY_LOSS"
        elif "temporal" in text:
            failure_class = "TEMPORAL_SCOPE_LOSS"
        else:
            failure_class = "SEMANTIC_TYPE_ERROR"
        outcome = capability_outcome(
            subject_kind="CLAIM_SUPPORT_RELATION",
            subject_id=_text(claim_like, "claim_id") or "UNIDENTIFIED_CLAIM",
            outcome="SYSTEM_CAPABILITY_FAILURE",
            rationale=f"claim-support interpretation failed: {error}",
            capability_failure_class=failure_class)
        return None, outcome


# --- Section 15.1 audit -------------------------------------------------

_ZERO_TOLERANCE_COUNTERS = (
    "unsupported_accepted", "wrong_scope_accepted", "wrong_time_accepted",
    "wrong_modality_accepted", "wrong_polarity_accepted", "inference_as_fact",
    "vendor_as_fact", "plan_as_implementation", "exercise_as_deployment",
    "retracted_support_used",
)


def audit_support_set(assessments: Iterable[SupportAssessment | Mapping[str, Any]],
                      ) -> dict[str, Any]:
    """Per-class coverage plus the Section 15.1 zero-tolerance counters.

    Accepts live assessments and serialized records alike, so foreign or
    replayed records that violate the acceptance contract are counted
    rather than trusted.  Uninterpretable records raise.
    """
    coverage = {name: 0 for name in REQUIRED_CLAIM_CLASSES}
    state_counts: dict[str, int] = {}
    counters = {name: 0 for name in _ZERO_TOLERANCE_COUNTERS}
    total = 0
    for item in assessments:
        record = dict(item) if isinstance(item, Mapping) else item.to_record()
        total += 1
        state = _vocab(str(record.get("state", "")), SUPPORT_STATES, "support state")
        claim_class = str(record.get("claim_class", ""))
        if claim_class not in REQUIRED_CLAIM_CLASSES:
            raise ValueError(f"unknown claim class: {claim_class!r}")
        if state == "QUALIFIED_SUPPORT" and not tuple(record.get("required_qualifications", ())):
            raise ValueError("qualified support without material qualification markers")
        coverage[claim_class] += 1
        state_counts[state] = state_counts.get(state, 0) + 1

        accepted = bool(record.get("accepted_for_reporting", False))
        supporting = {str(value) for value in record.get("supporting_evidence_ids", ())}
        blocked = {str(value) for value in record.get("blocked_evidence_ids", ())}
        violations = tuple(tuple(entry) for entry in record.get("modality_violations", ()))

        if blocked & supporting:
            counters["retracted_support_used"] += 1
        if accepted:
            if state in {"NOT_SUPPORTED", "CONTRADICTED", "WRONG_ENTITY"}:
                counters["unsupported_accepted"] += 1
            if state == "WRONG_SCOPE":
                counters["wrong_scope_accepted"] += 1
            if state == "WRONG_TIME":
                counters["wrong_time_accepted"] += 1
            if state == "WRONG_MODALITY":
                counters["wrong_modality_accepted"] += 1
            if state == "WRONG_POLARITY":
                counters["wrong_polarity_accepted"] += 1
            if state == "INFERENCE_ONLY":
                counters["inference_as_fact"] += 1
            for _, claim_modality, evidence_modality in violations:
                if evidence_modality == "VENDOR_DESCRIBED":
                    counters["vendor_as_fact"] += 1
                if evidence_modality in {"PLANNED", "PROPOSED", "INTENDED"}:
                    counters["plan_as_implementation"] += 1
                if evidence_modality in {"EXERCISED", "PILOTED"} and (
                        claim_modality in {"DEPLOYED", "OPERATIONAL"}
                        or claim_class == "DEPLOYMENT_CLAIM"):
                    counters["exercise_as_deployment"] += 1

    zero_total = sum(counters.values())
    return {
        "total_assessments": total,
        "class_coverage": coverage,
        "missing_classes": tuple(name for name in REQUIRED_CLAIM_CLASSES if not coverage[name]),
        "state_counts": dict(sorted(state_counts.items())),
        "zero_tolerance": counters,
        "zero_tolerance_total": zero_total,
        "gate_clean": zero_total == 0,
    }
