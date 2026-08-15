"""Report faithfulness capability — atomic proposition planning (contract Section 17).

Closes the V5 surface-6 failure modes at mechanism level: overstated certainty,
process assertions without operational proof, identity qualifiers dropped from
prose, prose/structure supersession divergence, independence-language leaks,
executive-summary overcompression, and material omission.

Composes the frozen v4 reporting primitives (``SentenceEvidence`` /
``validate_report``) by adding a proposition layer above them; no v4 code is
edited.  All vocabularies come from ``v5_1.models`` — the single authority.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Sequence

from ..v4.models import EPISTEMIC_STATES
from .models import (
    CAPABILITY_FAILURE_CLASSES, DEPENDENCE_STATES, INDEPENDENCE_LANGUAGE_PATTERNS,
    MATERIAL_QUALIFICATION_MARKERS, MODALITIES, PROPOSITION_DISPOSITIONS,
    RELATION_CLASSES, CapabilityOutcome, Record, capability_outcome, stable_id,
)

PROPOSITION_KINDS = frozenset({"FACTUAL", "INFERENCE", "METHOD", "LIMITATION"})

# Ordered report-language strength ladder (weakest to strongest).  This is a
# rendering-strength scale, not an epistemic ontology; the epistemic vocabulary
# stays in v4/v5_1 models.  The Section 17.4 strongest-claim check needs a
# total order, and CORROBORATED is only reachable with affirmative independence.
CERTAINTY_LEVELS = ("UNRESOLVED", "REPORTED_ONLY", "QUALIFIED", "SUPPORTED", "CORROBORATED")
_CERTAINTY_RANK = {level: index for index, level in enumerate(CERTAINTY_LEVELS)}

_CERTAINTY_FROM_EVIDENCE = {
    "DIRECTLY_STATED": "SUPPORTED", "SUPPORTED": "SUPPORTED",
    "PARTIALLY_SUPPORTED": "QUALIFIED", "QUALIFIED": "QUALIFIED", "INFERRED": "QUALIFIED",
    "REPORTED": "REPORTED_ONLY", "EXTRACTED": "REPORTED_ONLY", "PREDICTED": "REPORTED_ONLY",
    "CONTRADICTED": "UNRESOLVED", "DISPUTED": "UNRESOLVED", "RETRACTED": "UNRESOLVED",
    "UNKNOWN": "UNRESOLVED",
}
if set(_CERTAINTY_FROM_EVIDENCE) != set(EPISTEMIC_STATES):
    raise AssertionError("certainty map out of sync with v4 epistemic states")

# Most-severe-first: aggregation always returns the most conservative state.
_DEPENDENCE_SEVERITY = (
    "DEPENDENCE_DISPUTED", "DERIVATIVE_CONFIRMED", "TRANSLATION_DERIVATIVE",
    "SYNDICATION_DERIVATIVE", "MIRROR_MANIFESTATION", "COMMON_EVIDENCE_BASIS_CONFIRMED",
    "PARTIAL_DEPENDENCE", "INDEPENDENCE_UNKNOWN", "SHARED_DATA_INDEPENDENT_ANALYSIS",
    "NO_DEPENDENCE_FOUND", "INDEPENDENCE_SUPPORTED",
)
if frozenset(_DEPENDENCE_SEVERITY) != DEPENDENCE_STATES:
    raise AssertionError("dependence severity order out of sync with models.DEPENDENCE_STATES")

VIOLATION_CODES = frozenset({
    "INDEPENDENCE_LANGUAGE_WITHOUT_SUPPORT", "MATERIAL_QUALIFICATION_OMITTED",
    "PROCESS_ASSERTION_WITHOUT_OPERATIONAL_PROOF", "STRUCTURED_PROSE_DIVERGENCE",
    "IDENTITY_QUALIFIER_OMITTED", "EXEC_CERTAINTY_ABOVE_BODY",
    "EXEC_PROPOSITION_WITHOUT_BODY_SUPPORT", "EXEC_OMITS_DECISIVE_COUNTEREVIDENCE",
    "EXEC_OVERCOMPRESSION", "EXEC_MISSING_UNRESOLVED_HYPOTHESES",
    "EXEC_MISSING_DEPENDENCE_LIMITATION", "EXEC_MISSING_TEMPORAL_BOUNDARY",
    "EXEC_MISSING_REFUSAL_STATEMENT",
})

INDEPENDENCE_SUBSTITUTES = (
    "reported in multiple publications",
    "no dependence was identified among the reviewed sources",
    "source independence remains unresolved",
)

# Multilingual lexical realizations for every material-qualification marker.
QUALIFICATION_LEXICON: Mapping[str, tuple[str, ...]] = {
    "PLANNED": ("planned", "geplant", "prévu", "prévue", "previsto", "prevista", "pianificato"),
    "PROPOSED": ("proposed", "vorgeschlagen", "proposé", "proposée", "propuesto", "propuesta"),
    "INTENDED": ("intended", "beabsichtigt", "envisagé", "envisagée", "pretendido", "destinado"),
    "REPORTED": ("reported", "reportedly", "berichtet", "berichten zufolge", "rapporté",
                 "selon des informations", "según informes", "reportado", "informó"),
    "VENDOR_DESCRIBED": ("vendor-described", "according to the vendor", "vendor states",
                         "laut hersteller", "nach herstellerangaben", "selon le fabricant",
                         "según el fabricante"),
    "EXERCISED": ("exercised", "exercise", "übung", "exercice", "ejercicio"),
    "PILOTED": ("piloted", "pilot", "pilotprojekt", "pilotphase", "pilote", "piloto"),
    "DEPLOYED": ("deployed", "eingesetzt", "im einsatz", "déployé", "déployée",
                 "desplegado", "desplegada"),
    "OPERATIONAL": ("operational", "operativ", "im betrieb", "opérationnel", "opérationnelle",
                    "operativo", "operativa"),
    "DISPUTED": ("disputed", "contested", "umstritten", "contesté", "contestée",
                 "disputado", "cuestionado"),
    "PRELIMINARY": ("preliminary", "vorläufig", "préliminaire", "preliminar"),
    "FINAL": ("final", "endgültig", "définitif", "définitive", "definitivo", "definitiva"),
    "CORRECTED": ("corrected", "correction", "korrigiert", "korrektur", "corrigé", "corrigée",
                  "rectificatif", "corregido", "corrección"),
    "SUPERSEDED": ("superseded", "replaced", "abgelöst", "ersetzt", "remplacé", "remplacée",
                   "reemplazado", "sustituido"),
    "UNRESOLVED": ("unresolved", "ungeklärt", "offen geblieben", "non résolu", "non résolue",
                   "sin resolver", "no resuelto"),
}
if set(QUALIFICATION_LEXICON) != set(MATERIAL_QUALIFICATION_MARKERS):
    raise AssertionError("qualification lexicon out of sync with models markers")

# Process-property assertion families (keyed on assertion type, never on a
# specific sentence): each detected family requires an operational-proof
# artifact reference.
PROCESS_ASSERTION_PATTERNS: Mapping[str, tuple[str, ...]] = {
    "REPLAY_REPRODUCIBILITY": (
        r"\breplay(?:ed|able)?\b", r"\breproduc(?:ed|es|ible|ibility)\b",
        r"\bbyte-?identical\b", r"\bdeterministic(?:ally)?\b",
    ),
    "NETWORK_ISOLATION": (
        r"\bzero[- ]network\b", r"\bno\s+network\b", r"\bwithout\s+network\s+access\b",
        r"\bair-?gapped\b", r"\boffline\s+replay\b",
    ),
    "COMPLETE_COVERAGE": (
        r"\bcomplete\s+coverage\b", r"\bfull\s+coverage\b", r"\b100\s*(?:%|percent)\b",
        r"\ball\s+\w+\s+(?:were|are)\s+(?:mapped|verified|checked)\b",
        r"\bevery\s+\w+\s+(?:was|is)\s+(?:mapped|verified|checked)\b",
    ),
    "INTEGRITY_VERIFICATION": (
        r"\bhash-?(?:locked|verified)\b", r"\bintegrity\s+(?:was\s+|is\s+)?verified\b",
        r"\bchecksums?\s+match(?:ed)?\b",
    ),
    "REVIEW_ISOLATION": (
        r"\bblind(?:ed)?\s+review\b", r"\bisolated\s+reviewers?\b",
        r"\banswer\s+keys?\s+(?:were\s+)?sealed\b",
    ),
}

_PROSE_RELATION_LEXICON: Mapping[str, tuple[str, ...]] = {
    "SUPERSESSION": ("supersedes", "superseded", "supersede", "replaced by", "replaces",
                     "abgelöst", "ersetzt", "remplacé", "remplace", "sustituido", "reemplazado"),
    "CORRECTION": ("corrected", "correction", "corrects", "korrigiert", "korrektur",
                   "corrigé", "rectificatif", "corregido", "corrección"),
    "RETRACTION": ("retracted", "retraction", "withdrawn", "zurückgezogen", "rétracté",
                   "retirado", "retractado"),
    "TEMPORAL_UPDATE": ("updated", "update to", "aktualisiert", "mis à jour", "actualizado"),
}

_REFUSAL_LEXICON = (
    "refuses to conclude", "refuse to conclude", "does not conclude", "cannot conclude",
    "declines to conclude", "no conclusion is drawn", "keine schlussfolgerung",
    "se refuse à conclure", "aucune conclusion", "no se concluye", "sin conclusión",
)

_INDEPENDENCE_REGEXES = tuple(re.compile(pattern) for pattern in INDEPENDENCE_LANGUAGE_PATTERNS)
_ATTRIBUTION = re.compile(r"(?:according to|laut|selon|según)\s+([^,;.]+)", re.IGNORECASE)
_CLAUSE_BOUNDARY = re.compile(
    r";\s+|,\s+(?:and|but|while|whereas|und|aber|et|mais|y|pero)\s+|\s+(?:while|whereas)\s+")
_STOPWORDS = frozenset({
    "the", "and", "was", "were", "for", "with", "that", "this", "are", "has", "have",
    "had", "its", "der", "die", "das", "und", "les", "des", "une", "una", "los", "las",
    "del", "dans", "pour", "est", "ist",
})

_DECISIVE_COUNTEREVIDENCE_STATES = frozenset({
    "CORRECTION", "RETRACTION", "SUPERSESSION", "LOGICAL_CONTRADICTION", "POLARITY_CONFLICT",
})

OMISSION_REASON_TO_DISPOSITION: Mapping[str, str] = {
    "EVIDENCE_INSUFFICIENT": "OMITTED_EVIDENCE_INSUFFICIENT",
    "NOT_MATERIAL": "OMITTED_NOT_MATERIAL",
    "ACCESS_RESTRICTED": "OMITTED_ACCESS_RESTRICTED",
    "UNSUPPORTED": "REFUSED_UNSUPPORTED",
    "RENDER_FAILURE": "SYSTEM_CAPABILITY_FAILURE",
    "PLANNING_FAILURE": "SYSTEM_CAPABILITY_FAILURE",
}


def certainty_for_evidence_state(evidence_state: str) -> str:
    if evidence_state not in EPISTEMIC_STATES:
        raise ValueError(f"unknown evidence state: {evidence_state}")
    return _CERTAINTY_FROM_EVIDENCE[evidence_state]


def aggregate_dependence(states: Iterable[str]) -> str:
    """Conservative aggregation: the most severe state present wins; empty is unknown."""
    values = frozenset(states)
    unknown = values - DEPENDENCE_STATES
    if unknown:
        raise ValueError(f"unknown dependence states: {sorted(unknown)}")
    if not values:
        return "INDEPENDENCE_UNKNOWN"
    for state in _DEPENDENCE_SEVERITY:
        if state in values:
            return state
    raise AssertionError("unreachable")


@dataclass(frozen=True)
class AtomicProposition(Record):
    proposition_id: str
    text: str
    kind: str
    attribution: str
    temporal_scope: tuple[str | None, str | None]
    geographic_scope: tuple[str, ...]
    modality: str
    certainty: str
    evidence_state: str
    dependence_summary: str
    contradiction_state: str
    qualifications: tuple[str, ...]
    supporting_claim_ids: tuple[str, ...]
    supporting_span_ids: tuple[str, ...]
    supporting_basis_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        if not self.text.strip():
            raise ValueError("proposition requires text")
        if self.kind not in PROPOSITION_KINDS:
            raise ValueError(f"unknown proposition kind: {self.kind}")
        if self.modality not in MODALITIES:
            raise ValueError(f"unknown modality: {self.modality}")
        if self.certainty not in _CERTAINTY_RANK:
            raise ValueError(f"unknown certainty level: {self.certainty}")
        if self.evidence_state not in EPISTEMIC_STATES:
            raise ValueError(f"unknown evidence state: {self.evidence_state}")
        if self.dependence_summary not in DEPENDENCE_STATES:
            raise ValueError(f"unknown dependence summary: {self.dependence_summary}")
        if self.contradiction_state not in RELATION_CLASSES:
            raise ValueError(f"unknown contradiction state: {self.contradiction_state}")
        unknown = set(self.qualifications) - MATERIAL_QUALIFICATION_MARKERS
        if unknown:
            raise ValueError(f"unknown qualification markers: {sorted(unknown)}")
        if self.kind == "FACTUAL" and (not self.supporting_claim_ids or not self.supporting_span_ids):
            raise ValueError("factual proposition requires mapped claim and span support")
        # False-corroboration closure: corroborated wording needs affirmative independence.
        if self.certainty == "CORROBORATED" and self.dependence_summary != "INDEPENDENCE_SUPPORTED":
            raise ValueError("CORROBORATED certainty requires INDEPENDENCE_SUPPORTED dependence")
        # Overstated-certainty closure: defeated evidence cannot carry strength.
        if self.evidence_state in {"CONTRADICTED", "RETRACTED"} and self.certainty != "UNRESOLVED":
            raise ValueError("contradicted or retracted evidence forces UNRESOLVED certainty")


def atomic_proposition(*, text: str, kind: str, modality: str, certainty: str,
                       evidence_state: str, attribution: str = "",
                       temporal_scope: tuple[str | None, str | None] = (None, None),
                       geographic_scope: tuple[str, ...] = (),
                       dependence_summary: str = "INDEPENDENCE_UNKNOWN",
                       contradiction_state: str = "NO_CONFLICT",
                       qualifications: tuple[str, ...] = (),
                       supporting_claim_ids: tuple[str, ...] = (),
                       supporting_span_ids: tuple[str, ...] = (),
                       supporting_basis_ids: tuple[str, ...] = ()) -> AtomicProposition:
    return AtomicProposition(
        stable_id("proposition", kind, text, attribution, supporting_claim_ids),
        text, kind, attribution, temporal_scope, geographic_scope, modality, certainty,
        evidence_state, dependence_summary, contradiction_state, qualifications,
        supporting_claim_ids, supporting_span_ids, supporting_basis_ids)


@dataclass(frozen=True)
class FaithfulnessViolation(Record):
    violation_id: str
    code: str
    subject: str
    detail: str
    matched_text: str
    suggested_substitutes: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.code not in VIOLATION_CODES:
            raise ValueError(f"unknown violation code: {self.code}")
        if not self.subject.strip() or not self.detail.strip():
            raise ValueError("violation requires subject and detail")


def _violation(code: str, subject: str, detail: str, matched: str = "",
               substitutes: tuple[str, ...] = ()) -> FaithfulnessViolation:
    return FaithfulnessViolation(stable_id("faithfulness-violation", code, subject, matched, detail),
                                 code, subject, detail, matched, substitutes)


@dataclass(frozen=True)
class SentenceDecomposition(Record):
    decomposition_id: str
    sentence_text: str
    status: str
    propositions: tuple[AtomicProposition, ...]
    unsupported_segments: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.status not in {"DECOMPOSED", "REQUIRES_SPLIT"}:
            raise ValueError(f"unknown decomposition status: {self.status}")
        if self.status == "DECOMPOSED" and (not self.propositions or self.unsupported_segments):
            raise ValueError("DECOMPOSED requires propositions and no unsupported segments")
        # No partial acceptance: a bundled sentence must be split before planning.
        if self.status == "REQUIRES_SPLIT" and (self.propositions or not self.unsupported_segments):
            raise ValueError("REQUIRES_SPLIT carries only the unsupported segments")


def _claim_field(claim: Any, name: str, default: Any = None) -> Any:
    if isinstance(claim, Mapping):
        return claim.get(name, default)
    return getattr(claim, name, default)


def _tokens(text: str) -> frozenset[str]:
    return frozenset(token for token in re.findall(r"[\w-]+", text.casefold())
                     if len(token) >= 3 and token not in _STOPWORDS)


def _extract_attribution(segment: str) -> str:
    match = _ATTRIBUTION.search(segment)
    return match.group(1).strip() if match else ""


_CONTRADICTION_FROM_EVIDENCE = {
    "CONTRADICTED": "LOGICAL_CONTRADICTION", "RETRACTED": "RETRACTION",
    "DISPUTED": "SOURCE_DISAGREEMENT",
}

# Worst-wins ranking over EPISTEMIC_STATES: counterevidence carried by ANY
# supporting claim governs the proposition, independent of claim order.
_EVIDENCE_SEVERITY = (
    "RETRACTED", "CONTRADICTED", "DISPUTED", "UNKNOWN", "PREDICTED", "INFERRED",
    "QUALIFIED", "PARTIALLY_SUPPORTED", "EXTRACTED", "REPORTED", "SUPPORTED",
    "DIRECTLY_STATED",
)

# Weakest-wins ranking over MODALITIES: a proposition supported by claims of
# mixed strength may only be presented at the weakest supported strength.
_MODALITY_WEAKNESS = (
    "ALLEGED", "VENDOR_DESCRIBED", "PROPOSED", "INTENDED", "PLANNED",
    "RECOMMENDED", "CONDITIONAL", "PREDICTED", "DISPUTED", "PRELIMINARY",
    "EXERCISED", "PILOTED", "REPORTED", "ATTRIBUTED", "REQUIRED_BY_LAW",
    "DEPLOYED", "OPERATIONAL", "FINAL", "ASSERTED",
)


if frozenset(_EVIDENCE_SEVERITY) != EPISTEMIC_STATES:
    raise AssertionError("evidence severity order out of sync with EPISTEMIC_STATES")
if frozenset(_MODALITY_WEAKNESS) != MODALITIES:
    raise AssertionError("modality weakness order out of sync with MODALITIES")


def _aggregate_evidence_state(states: Iterable[str]) -> str:
    values = frozenset(states)
    unknown = values - frozenset(_EVIDENCE_SEVERITY)
    if unknown:
        raise ValueError(f"unknown epistemic states cannot aggregate silently: {sorted(unknown)}")
    for state in _EVIDENCE_SEVERITY:
        if state in values:
            return state
    return "REPORTED"


def _aggregate_modality(modalities: Iterable[str]) -> str:
    values = frozenset(modalities)
    unknown = values - frozenset(_MODALITY_WEAKNESS)
    if unknown:
        raise ValueError(f"unknown modalities cannot aggregate silently: {sorted(unknown)}")
    for modality in _MODALITY_WEAKNESS:
        if modality in values:
            return modality
    return "REPORTED"


def _aggregate_temporal(scopes: Iterable[tuple[Any, Any]]) -> tuple[str | None, str | None]:
    pairs = [tuple(scope) for scope in scopes]
    if not pairs:
        return (None, None)
    if any(start is None or end is None for start, end in pairs):
        return (None, None)
    return (min(start for start, _ in pairs), max(end for _, end in pairs))


def _proposition_from_segment(segment: str, claims: Sequence[Any]) -> AtomicProposition:
    evidence_state = _aggregate_evidence_state(
        _claim_field(claim, "epistemic_state", "REPORTED") for claim in claims)
    modality = _aggregate_modality(
        _claim_field(claim, "modality", "REPORTED") for claim in claims)
    geographic = tuple(sorted({place for claim in claims
                               for place in _claim_field(claim, "geographic_scope", ())}))
    qualifications = tuple(sorted({
        value for value in (_claim_field(claim, "modality", "") for claim in claims)
        if value in MATERIAL_QUALIFICATION_MARKERS}))
    return atomic_proposition(
        text=segment, kind="FACTUAL", modality=modality,
        certainty=certainty_for_evidence_state(evidence_state),
        evidence_state=evidence_state, attribution=_extract_attribution(segment),
        temporal_scope=_aggregate_temporal(
            _claim_field(claim, "temporal_scope", (None, None)) for claim in claims),
        geographic_scope=geographic,
        contradiction_state=_CONTRADICTION_FROM_EVIDENCE.get(evidence_state, "NO_CONFLICT"),
        qualifications=qualifications,
        supporting_claim_ids=tuple(sorted(
            _claim_field(claim, "claim_id") for claim in claims)),
        supporting_span_ids=tuple(sorted({span for claim in claims
                                          for span in _claim_field(claim, "candidate_ids", ())})),
        supporting_basis_ids=tuple(sorted({basis for claim in claims
                                           for basis in _claim_field(claim, "evidence_basis_ids", ())})))


def decompose_sentence(sentence_text: str, mapped_claims: Sequence[Any]) -> SentenceDecomposition:
    """Split a report sentence into atomic propositions with per-proposition support.

    A sentence bundling materially independent clauses where any clause lacks
    its own mapped claim support yields a ``REQUIRES_SPLIT`` result instead of
    a silent partial decomposition (Section 17.1).
    """
    if not sentence_text.strip():
        raise ValueError("sentence text required")
    claims = tuple(mapped_claims)
    if not claims:
        raise ValueError("decomposition requires mapped claim support")
    segments = tuple(part.strip().rstrip(".") for part in _CLAUSE_BOUNDARY.split(sentence_text)
                     if part and part.strip())
    if len(segments) == 1:
        proposition = _proposition_from_segment(segments[0], claims)
        return SentenceDecomposition(stable_id("decomposition", sentence_text),
                                     sentence_text, "DECOMPOSED", (proposition,), ())
    assigned: dict[int, list[Any]] = {index: [] for index in range(len(segments))}
    segment_tokens = [_tokens(segment) for segment in segments]
    for claim in claims:
        statement = _claim_field(claim, "normalized_statement", "") or ""
        claim_tokens = _tokens(statement)
        overlaps = [len(claim_tokens & tokens) for tokens in segment_tokens]
        best = max(overlaps)
        threshold = 2 if len(claim_tokens) >= 2 else 1
        if best >= threshold:
            assigned[overlaps.index(best)].append(claim)
    unsupported = tuple(segments[index] for index in range(len(segments)) if not assigned[index])
    if unsupported:
        return SentenceDecomposition(stable_id("decomposition", sentence_text),
                                     sentence_text, "REQUIRES_SPLIT", (), unsupported)
    propositions = tuple(_proposition_from_segment(segments[index], tuple(assigned[index]))
                         for index in range(len(segments)))
    return SentenceDecomposition(stable_id("decomposition", sentence_text),
                                 sentence_text, "DECOMPOSED", propositions, ())


def _matches_independence(text: str) -> str | None:
    lowered = text.casefold()
    for regex in _INDEPENDENCE_REGEXES:
        match = regex.search(lowered)
        if match:
            return match.group(0)
    return None


def _independence_substitute(states: frozenset[str]) -> str:
    if not states or states & {"INDEPENDENCE_UNKNOWN", "DEPENDENCE_DISPUTED"}:
        return "source independence remains unresolved"
    if states & {"DERIVATIVE_CONFIRMED", "TRANSLATION_DERIVATIVE", "SYNDICATION_DERIVATIVE",
                 "MIRROR_MANIFESTATION", "COMMON_EVIDENCE_BASIS_CONFIRMED", "PARTIAL_DEPENDENCE"}:
        return "reported in multiple publications"
    return "no dependence was identified among the reviewed sources"


def independence_language_guard(sentence: str, dependence_states: Iterable[str],
                                ) -> tuple[FaithfulnessViolation, ...]:
    """Independence wording requires affirmative INDEPENDENCE_SUPPORTED (Section 17.3).

    Suggestions are deterministic substitutions offered to the planner, never
    silent rewrites of the sentence.
    """
    states = frozenset(dependence_states)
    unknown = states - DEPENDENCE_STATES
    if unknown:
        raise ValueError(f"unknown dependence states: {sorted(unknown)}")
    lowered = sentence.casefold()
    # One independent pair among derivative pairs never licenses global
    # independence wording: the conservative aggregate must itself be
    # INDEPENDENCE_SUPPORTED (mirrors the CORROBORATED constructor guard).
    if states and aggregate_dependence(states) == "INDEPENDENCE_SUPPORTED":
        return ()
    violations = []
    for regex in _INDEPENDENCE_REGEXES:
        match = regex.search(lowered)
        if match:
            violations.append(_violation(
                "INDEPENDENCE_LANGUAGE_WITHOUT_SUPPORT", sentence,
                "independence wording used without affirmative INDEPENDENCE_SUPPORTED "
                "in the mapped dependence states",
                match.group(0), (_independence_substitute(states),)))
    return tuple(violations)


def _realized(realization: str, lowered_sentence: str) -> bool:
    return re.search(r"(?<!\w)" + re.escape(realization) + r"(?!\w)", lowered_sentence) is not None


def qualification_preservation_check(proposition: AtomicProposition, rendered_sentence: str,
                                     ) -> tuple[FaithfulnessViolation, ...]:
    """Every material qualification marker must be lexically realized in the sentence."""
    if not rendered_sentence.strip():
        raise ValueError("rendered sentence required")
    lowered = rendered_sentence.casefold()
    violations = []
    for marker in proposition.qualifications:
        realizations = QUALIFICATION_LEXICON[marker]
        if not any(_realized(item, lowered) for item in realizations):
            violations.append(_violation(
                "MATERIAL_QUALIFICATION_OMITTED", proposition.proposition_id,
                f"material qualification {marker} is not lexically realized in the rendered sentence",
                marker, realizations[:3]))
    return tuple(violations)


def process_assertion_guard(sentence: str, operational_proof_refs: Mapping[str, Iterable[str]],
                            ) -> tuple[FaithfulnessViolation, ...]:
    """Process-property assertions require an operational-proof artifact reference.

    Keyed on assertion type: any sentence matching a process-assertion family
    must cite at least one proof artifact id for that family.
    """
    unknown = set(operational_proof_refs) - set(PROCESS_ASSERTION_PATTERNS)
    if unknown:
        raise ValueError(f"unknown process assertion types: {sorted(unknown)}")
    lowered = sentence.casefold()
    violations = []
    for assertion_type, patterns in PROCESS_ASSERTION_PATTERNS.items():
        matched = next((match.group(0) for pattern in patterns
                        if (match := re.search(pattern, lowered))), None)
        if matched is None:
            continue
        proofs = tuple(ref for ref in operational_proof_refs.get(assertion_type, ()) if str(ref).strip())
        if not proofs:
            violations.append(_violation(
                "PROCESS_ASSERTION_WITHOUT_OPERATIONAL_PROOF", sentence,
                f"process assertion of type {assertion_type} requires an operational-proof "
                "artifact reference", matched))
    return tuple(violations)


def structured_prose_consistency(proposition: AtomicProposition,
                                 structured_states: Mapping[str, Any],
                                 ) -> tuple[FaithfulnessViolation, ...]:
    """Prose supersession/correction narrative must match the structured relation state;
    identity qualifiers carried by the structured layer must appear in prose."""
    relation_state = structured_states.get("relation_state")
    if relation_state is None:
        raise ValueError("structured relation state required")
    if relation_state not in RELATION_CLASSES:
        raise ValueError(f"unknown relation state: {relation_state}")
    lowered = proposition.text.casefold()
    narrated = {relation for relation, terms in _PROSE_RELATION_LEXICON.items()
                if any(_realized(term, lowered) for term in terms)}
    violations = []
    if proposition.contradiction_state != relation_state:
        violations.append(_violation(
            "STRUCTURED_PROSE_DIVERGENCE", proposition.proposition_id,
            f"proposition carries contradiction state {proposition.contradiction_state} "
            f"but the structured relation state is {relation_state}"))
    if relation_state in _PROSE_RELATION_LEXICON:
        if relation_state not in narrated:
            violations.append(_violation(
                "STRUCTURED_PROSE_DIVERGENCE", proposition.proposition_id,
                f"structured {relation_state} state is not narrated in the prose"))
        for extra in sorted(narrated - {relation_state}):
            violations.append(_violation(
                "STRUCTURED_PROSE_DIVERGENCE", proposition.proposition_id,
                f"prose narrates {extra} but the structured relation state is {relation_state}"))
    else:
        for extra in sorted(narrated):
            violations.append(_violation(
                "STRUCTURED_PROSE_DIVERGENCE", proposition.proposition_id,
                f"prose narrates {extra} but the structured relation state is {relation_state}"))
    for qualifier in tuple(structured_states.get("identity_qualifiers", ())):
        if qualifier.casefold() not in lowered:
            violations.append(_violation(
                "IDENTITY_QUALIFIER_OMITTED", proposition.proposition_id,
                "identity qualifier carried by the structured layer is missing from prose",
                qualifier, (qualifier,)))
    return tuple(violations)


def _has_unresolved(proposition: AtomicProposition) -> bool:
    return (proposition.certainty == "UNRESOLVED" or proposition.contradiction_state == "UNRESOLVED"
            or "UNRESOLVED" in proposition.qualifications)


def validate_executive_summary(exec_props: Sequence[AtomicProposition],
                               body_props: Sequence[AtomicProposition],
                               ) -> tuple[FaithfulnessViolation, ...]:
    """Section 17.4 executive-summary checks against the report body."""
    exec_values = tuple(exec_props)
    body_values = tuple(body_props)
    if not body_values:
        raise ValueError("executive summary validation requires body propositions")
    violations = []
    for item in exec_values:
        if item.kind not in {"FACTUAL", "INFERENCE"} or not item.supporting_claim_ids:
            continue
        shared = [body for body in body_values
                  if set(body.supporting_claim_ids) & set(item.supporting_claim_ids)]
        if not shared:
            violations.append(_violation(
                "EXEC_PROPOSITION_WITHOUT_BODY_SUPPORT", item.proposition_id,
                "executive proposition cites claims that no body proposition carries"))
            continue
        body_rank = max(_CERTAINTY_RANK[body.certainty] for body in shared)
        if _CERTAINTY_RANK[item.certainty] > body_rank:
            violations.append(_violation(
                "EXEC_CERTAINTY_ABOVE_BODY", item.proposition_id,
                f"executive certainty {item.certainty} exceeds the strongest body "
                f"certainty {CERTAINTY_LEVELS[body_rank]} for the shared claims"))
        body_qualifications = {marker for body in shared for marker in body.qualifications}
        dropped = body_qualifications - set(item.qualifications)
        if dropped:
            violations.append(_violation(
                "EXEC_OVERCOMPRESSION", item.proposition_id,
                f"executive proposition drops material qualifications {sorted(dropped)} "
                "carried by its body support"))
    body_decisive = {body.contradiction_state for body in body_values
                     if body.contradiction_state in _DECISIVE_COUNTEREVIDENCE_STATES}
    exec_states = {item.contradiction_state for item in exec_values}
    for missing in sorted(body_decisive - exec_states):
        violations.append(_violation(
            "EXEC_OMITS_DECISIVE_COUNTEREVIDENCE", "executive-summary",
            f"body carries decisive counterevidence state {missing} absent from the executive summary"))
    if any(_has_unresolved(body) for body in body_values) and \
            not any(_has_unresolved(item) for item in exec_values):
        violations.append(_violation(
            "EXEC_MISSING_UNRESOLVED_HYPOTHESES", "executive-summary",
            "body carries unresolved material but the executive summary presents none"))
    if any(body.dependence_summary != "INDEPENDENCE_SUPPORTED" for body in body_values) and \
            not any(item.kind == "LIMITATION" and item.dependence_summary != "INDEPENDENCE_SUPPORTED"
                    for item in exec_values):
        violations.append(_violation(
            "EXEC_MISSING_DEPENDENCE_LIMITATION", "executive-summary",
            "source dependence is not fully independence-supported and the executive summary "
            "carries no dependence limitation"))
    if any(body.temporal_scope != (None, None) for body in body_values) and \
            not any(item.temporal_scope != (None, None) for item in exec_values):
        violations.append(_violation(
            "EXEC_MISSING_TEMPORAL_BOUNDARY", "executive-summary",
            "body findings are temporally scoped but the executive summary states no temporal boundary"))
    if not any(item.kind == "LIMITATION" and
               any(term in item.text.casefold() for term in _REFUSAL_LEXICON)
               for item in exec_values):
        violations.append(_violation(
            "EXEC_MISSING_REFUSAL_STATEMENT", "executive-summary",
            "executive summary carries no refusal statement for what is not concluded"))
    return tuple(violations)


@dataclass(frozen=True)
class PropositionEvidencePackage(Record):
    """Section 17.6 per-proposition evidence package."""

    package_id: str
    proposition: AtomicProposition
    final_sentence: str | None
    supporting_claim_ids: tuple[str, ...]
    supporting_span_ids: tuple[str, ...]
    attribution: str
    temporal_scope: tuple[str | None, str | None]
    geographic_scope: tuple[str, ...]
    modality: str
    dependence_summary: str
    counterevidence_ids: tuple[str, ...]
    qualifications: tuple[str, ...]
    epistemic_state: str
    allowed_wording: tuple[str, ...]
    disallowed_stronger_wording: tuple[str, ...]
    omission_reason: str | None
    disposition: str
    capability_failure_class: str | None

    def __post_init__(self) -> None:
        if self.disposition not in PROPOSITION_DISPOSITIONS:
            raise ValueError(f"unknown proposition disposition: {self.disposition}")
        if self.modality not in MODALITIES:
            raise ValueError(f"unknown modality: {self.modality}")
        if self.dependence_summary not in DEPENDENCE_STATES:
            raise ValueError(f"unknown dependence summary: {self.dependence_summary}")
        if self.epistemic_state not in EPISTEMIC_STATES:
            raise ValueError(f"unknown epistemic state: {self.epistemic_state}")
        if set(self.qualifications) - MATERIAL_QUALIFICATION_MARKERS:
            raise ValueError("unknown qualification markers")
        published = self.disposition in {"PUBLISHED_FAITHFULLY", "PUBLISHED_WITH_QUALIFICATION"}
        if published:
            if not (self.final_sentence or "").strip():
                raise ValueError("published proposition requires its final sentence")
            if self.omission_reason is not None:
                raise ValueError("published proposition cannot carry an omission reason")
            if self.disposition == "PUBLISHED_FAITHFULLY" and self.proposition.qualifications:
                raise ValueError("qualified proposition must publish as PUBLISHED_WITH_QUALIFICATION")
            if self.disposition == "PUBLISHED_WITH_QUALIFICATION" and not self.qualifications:
                raise ValueError("PUBLISHED_WITH_QUALIFICATION requires the preserved qualifications")
        else:
            if self.final_sentence is not None:
                raise ValueError("unpublished proposition cannot carry a final sentence")
            if not (self.omission_reason or "").strip():
                raise ValueError("unpublished proposition requires its omission reason")
        if self.disposition == "SYSTEM_CAPABILITY_FAILURE":
            if self.capability_failure_class not in CAPABILITY_FAILURE_CLASSES:
                raise ValueError("SYSTEM_CAPABILITY_FAILURE disposition requires a defect class")
        elif self.capability_failure_class is not None:
            raise ValueError("defect class is only valid on SYSTEM_CAPABILITY_FAILURE")
        if set(self.allowed_wording) & set(self.disallowed_stronger_wording):
            raise ValueError("allowed and disallowed wording must be disjoint")
        if self.dependence_summary != "INDEPENDENCE_SUPPORTED":
            for wording in self.allowed_wording:
                if _matches_independence(wording):
                    raise ValueError("allowed wording uses independence language without "
                                     "INDEPENDENCE_SUPPORTED dependence")


def proposition_evidence_package(*, proposition: AtomicProposition, disposition: str,
                                 final_sentence: str | None = None,
                                 counterevidence_ids: tuple[str, ...] = (),
                                 allowed_wording: tuple[str, ...] = (),
                                 disallowed_stronger_wording: tuple[str, ...] = (),
                                 omission_reason: str | None = None,
                                 capability_failure_class: str | None = None,
                                 ) -> PropositionEvidencePackage:
    return PropositionEvidencePackage(
        stable_id("proposition-package", proposition.proposition_id, disposition),
        proposition, final_sentence, proposition.supporting_claim_ids,
        proposition.supporting_span_ids, proposition.attribution, proposition.temporal_scope,
        proposition.geographic_scope, proposition.modality, proposition.dependence_summary,
        counterevidence_ids, proposition.qualifications, proposition.evidence_state,
        allowed_wording, disallowed_stronger_wording, omission_reason, disposition,
        capability_failure_class)


@dataclass(frozen=True)
class OmissionAuditResult(Record):
    audit_id: str
    proposition_count: int
    dispositions: Mapping[str, str]
    capability_failures: tuple[CapabilityOutcome, ...]
    published_count: int
    omitted_count: int

    def __post_init__(self) -> None:
        if len(self.dispositions) != self.proposition_count:
            raise ValueError("audit must assign a disposition to every proposition")
        invalid = set(self.dispositions.values()) - PROPOSITION_DISPOSITIONS
        if invalid:
            raise ValueError(f"unknown dispositions: {sorted(invalid)}")
        failure_count = sum(value == "SYSTEM_CAPABILITY_FAILURE"
                            for value in self.dispositions.values())
        if failure_count != len(self.capability_failures):
            raise ValueError("every capability-failure disposition requires its recorded outcome")


def material_omission_audit(all_propositions: Iterable[AtomicProposition],
                            published_ids: Iterable[str],
                            omission_reasons: Mapping[str, str]) -> OmissionAuditResult:
    """Section 17.5 audit: every reportable proposition gets an explicit disposition.

    An omission caused by rendering/planning failure — or a proposition with no
    recorded disposition at all — is a SYSTEM_CAPABILITY_FAILURE with class
    PROPOSITION_RENDER_FAILURE, never a silent gap.
    """
    propositions = tuple(all_propositions)
    identifiers = [item.proposition_id for item in propositions]
    identifier_set = set(identifiers)
    if len(identifier_set) != len(identifiers):
        raise ValueError("duplicate proposition identifiers")
    published = set(published_ids)
    if published - identifier_set:
        raise ValueError("published ids reference unknown propositions")
    reasons = dict(omission_reasons)
    if set(reasons) - identifier_set:
        raise ValueError("omission reasons reference unknown propositions")
    invalid = set(reasons.values()) - set(OMISSION_REASON_TO_DISPOSITION)
    if invalid:
        raise ValueError(f"invalid omission reasons: {sorted(invalid)} "
                         "(valid categories cover evidence, relevance, access-marking, "
                         "unsupported refusal, and declared render/planning failure)")
    if published & set(reasons):
        raise ValueError("published proposition cannot carry an omission reason")
    dispositions: dict[str, str] = {}
    failures: list[CapabilityOutcome] = []
    for item in propositions:
        identifier = item.proposition_id
        if identifier in published:
            dispositions[identifier] = ("PUBLISHED_WITH_QUALIFICATION" if item.qualifications
                                        else "PUBLISHED_FAITHFULLY")
        elif identifier in reasons:
            category = reasons[identifier]
            disposition = OMISSION_REASON_TO_DISPOSITION[category]
            dispositions[identifier] = disposition
            if disposition == "SYSTEM_CAPABILITY_FAILURE":
                failures.append(capability_outcome(
                    subject_kind="REPORT_PROPOSITION", subject_id=identifier,
                    outcome="SYSTEM_CAPABILITY_FAILURE",
                    rationale=f"proposition omitted because a declared {category} "
                              "prevented faithful rendering",
                    capability_failure_class="PROPOSITION_RENDER_FAILURE"))
        else:
            dispositions[identifier] = "SYSTEM_CAPABILITY_FAILURE"
            failures.append(capability_outcome(
                subject_kind="REPORT_PROPOSITION", subject_id=identifier,
                outcome="SYSTEM_CAPABILITY_FAILURE",
                rationale="proposition reached publication planning with no recorded "
                          "disposition; the planner silently dropped it",
                capability_failure_class="PROPOSITION_RENDER_FAILURE"))
    return OmissionAuditResult(
        stable_id("omission-audit", tuple(sorted(dispositions.items()))),
        len(propositions), dispositions, tuple(failures),
        sum(value.startswith("PUBLISHED") for value in dispositions.values()),
        sum(value.startswith("OMITTED") for value in dispositions.values()))
