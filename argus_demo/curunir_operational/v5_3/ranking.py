"""Structured ranking over candidate interpretations (contract Section 6).

V5.2 built the candidate lattice but decided with a hand-ordered cascade of
rejection rules.  On clean evidence that scored 0.5921 with 9 wrong accepted
spans.  The lattice was not the problem; the selection function over it was.

This module replaces the cascade with a transparent multiclass linear ranker:

* one shared feature vector per candidate, covering boundary completeness,
  boundary contamination, discourse role, layout evidence and semantic
  coherence (Section 6.2);
* per-outcome weights fitted from pairwise development preferences by an
  averaged perceptron — deterministic, inspectable, pure stdlib, no opaque
  dependency;
* hard invariants (Section 6.5) that veto an outcome regardless of score, so
  a learned weight can never license a span that loses polarity, loses
  material modality, strengthens lifecycle state, changes attribution, or is
  structural chrome.

The weights are data.  They are written to, and loaded from, a frozen JSON
file, so the ranker that ran in a clean evaluation is exactly the ranker whose
hash appears in the freeze manifest.

Research shadow only.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from ..v4.models import ExtractionCandidate, NormalizedDocument
from ..v5_1.models import Record, now_utc, sha256, stable_id
from ..v5_2 import chrome, lifecycle
from ..v5_2.semantics import (
    CandidateLattice, Interpretation, _normalize_type, _quarantined_regions,
    build_lattice, is_legal_citation, span_alignment,
)

OUTCOMES: tuple[str, ...] = (
    "ACCEPTED_CANDIDATE", "SEMANTICALLY_PARSED", "EVIDENCE_BOUND",
    "QUARANTINED", "REJECTED",
)

_EXACT_PRECISIONS = frozenset({"EXACT_BYTE", "EXACT_CHARACTER", "EXACT_PAGE_CHARACTER"})
_EXACT_SPAN_TYPES = frozenset({"QUOTATION", "NUMERIC_VALUE", "DATE"})
_ASSERTION_TYPES = frozenset({"CLAIM", "RELATION", "EVENT"})
_MATERIAL_TYPES = frozenset({"CLAIM", "RELATION", "EVENT", "QUOTATION",
                             "CORRECTION", "RETRACTION"})

_INTERROGATIVE = re.compile(r"\?\s*[\"'»›)\]]*$")

# Pronominal adverbs, discourse connectives and expletive constructions whose
# referent lies outside the span.  A span containing one of these does not say
# what it is about on its own, however well formed it looks.  Five of the
# eleven V5.2-era wrong admissions were exactly this shape.
_ANAPHORIC = re.compile(
    r"(?:^|(?<=[\s,;:]))(?:"
    r"dabei|damit|dadurch|daher|deshalb|darum|dorthin|dort|hierbei|hierzu|"
    r"somit|zudem|au[\u00dfs]erdem|ferner|denn\s|dies(?:e|er|es|em|en)?\s|"
    r"das\s+geht\s+aus|es\s+geht\s+(?:darum|um)|"
    r"thereby|thereto|thereof|therein|thus\s|hence\s|additionally|moreover|"
    r"furthermore|in\s+addition|as\s+such|"
    r"ainsi|de\s+ce\s+fait|par\s+ailleurs|en\s+outre|"
    r"por\s+ello|por\s+tanto|adem[\u00e1a]s|as[\u00ed i]\s+"
    r")", re.IGNORECASE)

# An explicit calendar date inside the span's asserted content.
_EXPLICIT_DATE = re.compile(
    r"\b(?:\d{1,2}\s+)?(?:january|february|march|april|may|june|july|august|"
    r"september|october|november|december|januar|februar|m[\u00e4a]rz|mai|juni|juli|"
    r"oktober|dezember|janvier|f[\u00e9e]vrier|mars|avril|juin|juillet|ao[\u00fbu]t|"
    r"septembre|octobre|novembre|d[\u00e9e]cembre|enero|febrero|marzo|abril|mayo|"
    r"junio|julio|agosto|septiembre|octubre|noviembre|diciembre)\b|"
    r"\b\d{1,2}[./]\d{1,2}[./]\d{2,4}\b|\b\d{4}-\d{2}-\d{2}\b",
    re.IGNORECASE)
_HEADLINE = re.compile(r"^[^.!?]{10,120}$")
_TABLE_ROW = re.compile(r"\S[ \t]{2,}\S")

# Modality values that carry material information a narrower span could lose.
_MATERIAL_MODALITIES = frozenset({
    "PLANNED", "PROPOSED", "INTENDED", "VENDOR_DESCRIBED", "EXERCISED",
    "PILOTED", "DEPLOYED", "OPERATIONAL", "PRELIMINARY", "DISPUTED",
    "PREDICTED", "CONDITIONAL", "RECOMMENDED", "REQUIRED_BY_LAW", "ALLEGED",
    "ATTRIBUTED", "REPORTED",
})


# ---------------------------------------------------------------------------
# Section 6.2 — features
# ---------------------------------------------------------------------------

FEATURE_NAMES: tuple[str, ...] = (
    # boundary completeness
    "has_subject", "has_predicate", "has_object", "has_attribution",
    "has_material_modality", "has_temporal_scope", "lifecycle_recoverable",
    "has_numeric_value", "has_unit_context", "finite_clause",
    "missing_role_count",
    # boundary contamination
    "starts_mid_sentence", "ends_mid_sentence", "truncated", "open_delimiter",
    "list_item_without_stem", "non_referential_subject",
    "attribution_from_context", "crosses_page", "widened_from_seed",
    # discourse role
    "is_chrome", "is_citation", "is_question", "is_headline_shape",
    "in_table_row", "has_caption_context", "has_heading_context",
    "correction_notice_nearby",
    # layout
    "layout_quarantined", "footnote_attached", "table_header_supplied",
    # coherence
    "seed_is_whole_sentence", "token_count", "alternatives_considered",
    "unresolved_deictic", "asserts_date_at_approximate_mapping",
)


def _norm_count(value: int, cap: int = 8) -> float:
    return min(value, cap) / cap


def extract_features(candidate: ExtractionCandidate, document: NormalizedDocument,
                     lattice: CandidateLattice, best: Interpretation | None,
                     layout_annotation: Any = None) -> dict[str, float]:
    """One shared feature vector for the whole decision."""
    text = candidate.original_text
    evidence = lattice.boundary_evidence
    narrow = next((i for i in lattice.interpretations if i.level == "NARROW"), best)
    starts_clean, ends_clean = span_alignment(
        document.text, candidate.span_start, candidate.span_end)
    warnings = set(best.warnings) if best else set()

    overlaps_layout = any(
        candidate.span_start < end and candidate.span_end > start
        for start, end, _kind in _quarantined_regions(layout_annotation))

    attribution_changed = bool(
        best and narrow and best.attribution and narrow.attribution
        and best.attribution != narrow.attribution)

    features = {
        "has_subject": float(bool(best and best.subject.strip())),
        "has_predicate": float(bool(best and best.predicate.strip())),
        "has_object": float(bool(best and best.object_or_value.strip())),
        "has_attribution": float(bool(best and best.attribution)),
        "has_material_modality": float(
            bool(best and best.modality in _MATERIAL_MODALITIES)),
        "has_temporal_scope": float(
            bool(best and best.temporal_scope != (None, None))),
        "lifecycle_recoverable": float(
            bool(best and best.lifecycle_state != "UNKNOWN")),
        "has_numeric_value": float(bool(best and best.numeric_values)),
        "has_unit_context": float(bool(evidence.table_header)),
        "finite_clause": float(bool(best and best.finite_clause)),
        "missing_role_count": _norm_count(len(best.missing_roles) if best else 3, 4),

        "starts_mid_sentence": float(not starts_clean),
        "ends_mid_sentence": float(not ends_clean),
        "truncated": float(bool(warnings & {"TRUNCATED_AT_ABBREVIATION",
                                            "TRUNCATED_MID_SENTENCE"})),
        "open_delimiter": float(bool(warnings & {"OPEN_PARENTHESIS", "OPEN_QUOTATION"})),
        "list_item_without_stem": float("LIST_ITEM_WITHOUT_STEM" in warnings
                                        or evidence.enumerated_context),
        "non_referential_subject": float("NON_REFERENTIAL_SUBJECT" in warnings),
        "attribution_from_context": float(
            "ATTRIBUTION_AFTER_QUOTATION" in warnings or attribution_changed),
        "crosses_page": float(evidence.continues_next_page),
        "widened_from_seed": float(bool(best and best.level != "NARROW")),

        "is_chrome": float(chrome.classify_chrome(text) is not None),
        "is_citation": float(is_legal_citation(text)),
        "is_question": float(bool(_INTERROGATIVE.search(text.strip()))),
        "is_headline_shape": float(bool(_HEADLINE.match(text.strip()))),
        "in_table_row": float(bool(_TABLE_ROW.search(text))),
        "has_caption_context": float(evidence.caption is not None),
        "has_heading_context": float(evidence.heading is not None),
        "correction_notice_nearby": float(evidence.correction_notice
                                          or evidence.retraction_notice),

        "layout_quarantined": float(overlaps_layout),
        "footnote_attached": float(evidence.footnote is not None),
        "table_header_supplied": float("PREDICATE_FROM_TABLE_HEADER" in warnings
                                       or "UNIT_FROM_TABLE_HEADER" in warnings),

        "seed_is_whole_sentence": float(starts_clean and ends_clean),
        "token_count": _norm_count(len(text.split()), 40),
        "alternatives_considered": _norm_count(len(lattice.interpretations), 4),
        "unresolved_deictic": float(bool(_ANAPHORIC.search(text))),
        "asserts_date_at_approximate_mapping": float(
            bool(best and best.temporal_scope != (None, None))
            and bool(_EXPLICIT_DATE.search(text))
            and candidate.mapping_precision not in _EXACT_PRECISIONS),
    }
    assert set(features) == set(FEATURE_NAMES)
    return features


# ---------------------------------------------------------------------------
# Section 6.5 — hard invariants
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Veto:
    outcome: str
    reason: str
    detail: str


def hard_vetoes(candidate: ExtractionCandidate, document: NormalizedDocument,
                lattice: CandidateLattice, best: Interpretation | None,
                features: Mapping[str, float],
                layout_annotation: Any = None) -> tuple[Veto, ...]:
    """Outcomes no learned weight may license.

    Every veto here corresponds to a Section 6.5 invariant or a Section 18
    zero-tolerance gate.  They are applied after scoring and before selection,
    so the ranker's preference is visible in the artifact even when overruled.
    """
    vetoes: list[Veto] = []
    ntype = _normalize_type(candidate.candidate_type)
    narrow = next((i for i in lattice.interpretations if i.level == "NARROW"), best)

    if features["is_chrome"] or features["layout_quarantined"]:
        for outcome in ("ACCEPTED_CANDIDATE", "SEMANTICALLY_PARSED", "EVIDENCE_BOUND"):
            vetoes.append(Veto(outcome, "STRUCTURAL_CHROME",
                               "site or archive furniture can never be admitted"))
    if features["is_question"] and ntype in _ASSERTION_TYPES:
        for outcome in ("ACCEPTED_CANDIDATE", "SEMANTICALLY_PARSED", "EVIDENCE_BOUND"):
            vetoes.append(Veto(outcome, "INTERROGATIVE",
                               "a question asserts nothing"))
    if features["is_citation"] and ntype in _ASSERTION_TYPES:
        for outcome in ("ACCEPTED_CANDIDATE", "SEMANTICALLY_PARSED", "EVIDENCE_BOUND"):
            vetoes.append(Veto(outcome, "BIBLIOGRAPHIC_CITATION",
                               "a reference to a legal act asserts nothing"))
    if features["non_referential_subject"]:
        vetoes.append(Veto("ACCEPTED_CANDIDATE", "NON_REFERENTIAL_FRAGMENT",
                           "the subject refers to nothing on its own"))
    if features["unresolved_deictic"]:
        vetoes.append(Veto("ACCEPTED_CANDIDATE", "UNRESOLVED_DEICTIC",
                           "the span carries a pronominal adverb or discourse "
                           "connective whose referent lies outside it"))
    if features["list_item_without_stem"]:
        vetoes.append(Veto("ACCEPTED_CANDIDATE", "PREDICATE_IN_ENCLOSING_STEM",
                           "the span is one item of an enumeration whose "
                           "predicate lives in the stem above it"))
    if features["asserts_date_at_approximate_mapping"]:
        vetoes.append(Veto("ACCEPTED_CANDIDATE", "TEMPORAL_MAPPING_PRECISION",
                           "an asserted calendar scope cannot rest on an "
                           "approximate span mapping"))
    if features["starts_mid_sentence"] or features["ends_mid_sentence"]:
        vetoes.append(Veto("ACCEPTED_CANDIDATE", "MIS_BOUNDARIED_SPAN",
                           "the recorded span is not a sentence-aligned unit"))
    if best is not None and best.missing_roles:
        vetoes.append(Veto("ACCEPTED_CANDIDATE", "INCOMPLETE_ROLES",
                           f"required roles absent: {list(best.missing_roles)}"))
    if ntype in _ASSERTION_TYPES and not (best and best.finite_clause):
        vetoes.append(Veto("ACCEPTED_CANDIDATE", "NO_PROPOSITIONAL_CONTENT",
                           "no boundary yields a finite clause"))

    # Modality and lifecycle may not be lost or strengthened by the chosen
    # boundary relative to the seed span the candidate actually records.
    if best is not None and narrow is not None and best is not narrow:
        if narrow.modality in _MATERIAL_MODALITIES and \
                best.modality not in _MATERIAL_MODALITIES:
            vetoes.append(Veto("ACCEPTED_CANDIDATE", "MODALITY_LOST",
                               f"the seed records {narrow.modality}; the chosen "
                               f"boundary reads {best.modality}"))
        if narrow.polarity != best.polarity:
            vetoes.append(Veto("ACCEPTED_CANDIDATE", "POLARITY_CHANGED",
                               "the chosen boundary flips the seed's polarity"))
        if narrow.lifecycle_state != "UNKNOWN" and best.lifecycle_state != "UNKNOWN" \
                and lifecycle.stronger_than(best.lifecycle_state, narrow.lifecycle_state):
            vetoes.append(Veto("ACCEPTED_CANDIDATE", "LIFECYCLE_STRENGTHENED",
                               f"the chosen boundary reads {best.lifecycle_state} "
                               f"where the seed reads {narrow.lifecycle_state}"))
        if narrow.attribution and best.attribution and \
                narrow.attribution != best.attribution:
            vetoes.append(Veto("ACCEPTED_CANDIDATE", "ATTRIBUTION_CHANGED",
                               "the chosen boundary reassigns the attribution"))

    if ntype in _EXACT_SPAN_TYPES and candidate.mapping_precision not in _EXACT_PRECISIONS:
        vetoes.append(Veto("ACCEPTED_CANDIDATE", "MAPPING_PRECISION_INSUFFICIENT",
                           f"{ntype} requires exact span mapping"))
    return tuple(vetoes)


# ---------------------------------------------------------------------------
# The ranker
# ---------------------------------------------------------------------------

RANKER_VERSION = "curunir-extraction-ranker-v5.3"


@dataclass
class RankingModel:
    """Multiclass linear ranker with per-outcome weights.

    Transparent by construction: the weights are a plain mapping the freeze
    manifest can hash and a reviewer can read.
    """

    weights: dict[str, dict[str, float]] = field(default_factory=dict)
    bias: dict[str, float] = field(default_factory=dict)
    version: str = RANKER_VERSION
    fitted_on: int = 0

    def __post_init__(self) -> None:
        for outcome in OUTCOMES:
            self.weights.setdefault(outcome, {name: 0.0 for name in FEATURE_NAMES})
            self.bias.setdefault(outcome, 0.0)

    def score(self, outcome: str, features: Mapping[str, float]) -> float:
        row = self.weights[outcome]
        return round(self.bias[outcome] +
                     sum(row[name] * float(features[name]) for name in FEATURE_NAMES), 6)

    def scores(self, features: Mapping[str, float]) -> dict[str, float]:
        return {outcome: self.score(outcome, features) for outcome in OUTCOMES}

    def fit(self, examples: Sequence[tuple[Mapping[str, float], str]], *,
            epochs: int = 12, learning_rate: float = 1.0) -> dict[str, Any]:
        """Averaged multiclass perceptron over development preferences.

        Deterministic: examples are consumed in the order given, ties break on
        the fixed OUTCOMES order, and no randomness is used anywhere.
        """
        totals = {o: {n: 0.0 for n in FEATURE_NAMES} for o in OUTCOMES}
        total_bias = {o: 0.0 for o in OUTCOMES}
        updates = 0
        mistakes_by_epoch: list[int] = []
        for _epoch in range(epochs):
            mistakes = 0
            for features, gold in examples:
                if gold not in self.weights:
                    raise ValueError(f"unknown outcome in development data: {gold}")
                scored = self.scores(features)
                best = max(OUTCOMES, key=lambda o: (scored[o], -OUTCOMES.index(o)))
                if best != gold:
                    mistakes += 1
                    for name in FEATURE_NAMES:
                        value = float(features[name]) * learning_rate
                        self.weights[gold][name] += value
                        self.weights[best][name] -= value
                    self.bias[gold] += learning_rate
                    self.bias[best] -= learning_rate
                updates += 1
                for outcome in OUTCOMES:
                    for name in FEATURE_NAMES:
                        totals[outcome][name] += self.weights[outcome][name]
                    total_bias[outcome] += self.bias[outcome]
            mistakes_by_epoch.append(mistakes)
        if updates:
            for outcome in OUTCOMES:
                for name in FEATURE_NAMES:
                    self.weights[outcome][name] = round(totals[outcome][name] / updates, 6)
                self.bias[outcome] = round(total_bias[outcome] / updates, 6)
        self.fitted_on = len(examples)
        return {"examples": len(examples), "epochs": epochs,
                "mistakes_by_epoch": mistakes_by_epoch,
                "final_mistake_rate": round(
                    mistakes_by_epoch[-1] / max(1, len(examples)), 4)}

    def to_record(self) -> dict[str, Any]:
        return {"version": self.version, "fitted_on": self.fitted_on,
                "feature_names": list(FEATURE_NAMES), "outcomes": list(OUTCOMES),
                "weights": self.weights, "bias": self.bias}

    def save(self, path: str | Path) -> str:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(self.to_record(), indent=1, sort_keys=True)
        target.write_text(payload)
        return sha256(payload)

    @classmethod
    def load(cls, path: str | Path) -> "RankingModel":
        payload = json.loads(Path(path).read_text())
        if list(payload["feature_names"]) != list(FEATURE_NAMES):
            raise ValueError("ranker feature set does not match this build")
        model = cls(weights=payload["weights"], bias=payload["bias"],
                    version=payload["version"], fitted_on=payload["fitted_on"])
        return model


@dataclass(frozen=True)
class RankedDecision(Record):
    """The production decision, with the whole ranking kept visible."""

    decision_id: str
    candidate_id: str
    document_id: str
    stage: str
    candidate_function: str
    selected_level: str | None
    selected_span: tuple[int, int] | None
    lifecycle_state: str
    evidence_act: str
    polarity: str | None
    modality: str | None
    temporal_scope: tuple[str | None, str | None]
    subject: str | None
    predicate: str | None
    object_or_value: str | None
    attribution: str | None
    missing_roles: tuple[str, ...]
    boundary_warnings: tuple[str, ...]
    alternative_count: int
    ranked_scores: Mapping[str, float]
    top_unvetoed: str
    vetoes: tuple[Mapping[str, str], ...]
    features: Mapping[str, float]
    rationale: str
    lattice_id: str
    ranker_version: str
    recorded_time: str

    def __post_init__(self) -> None:
        if self.stage not in OUTCOMES:
            raise ValueError(f"unknown stage: {self.stage}")
        if self.stage == "ACCEPTED_CANDIDATE" and self.missing_roles:
            raise ValueError("an accepted candidate may not miss a required role")


_CHROME_FUNCTION = {
    "ARCHIVAL_WRAPPER": "DOCUMENT_STRUCTURE",
    "COOKIE_NOTICE": "NAVIGATIONAL_METADATA",
    "NAVIGATION_CHROME": "NAVIGATIONAL_METADATA",
    "CONTACT_BOILERPLATE": "NAVIGATIONAL_METADATA",
    "LEGAL_BOILERPLATE": "DOCUMENT_STRUCTURE",
    "SOCIAL_SHARE": "NAVIGATIONAL_METADATA",
    "PRINT_OR_EXPORT": "NAVIGATIONAL_METADATA",
    "SUBSCRIPTION_PROMPT": "NAVIGATIONAL_METADATA",
}


def _function_for(stage: str, ntype: str, text: str) -> str:
    if stage == "QUARANTINED":
        found = chrome.classify_chrome(text)
        return _CHROME_FUNCTION.get(found or "", "DOCUMENT_STRUCTURE")
    if stage == "ACCEPTED_CANDIDATE":
        return "ANALYTICALLY_MATERIAL" if ntype in _MATERIAL_TYPES else "SUPPORTING_DETAIL"
    if stage == "REJECTED" and is_legal_citation(text):
        return "SUPPORTING_DETAIL"
    return "UNKNOWN_VALUE"


def rank_candidate(candidate: ExtractionCandidate, document: NormalizedDocument,
                   model: RankingModel, layout_annotation: Any = None, *,
                   language: str | None = None,
                   document_date: str | None = None) -> RankedDecision:
    """Decide one candidate by ranking outcomes over its lattice."""
    if candidate.document_id != document.document_id:
        raise ValueError("candidate/document provenance mismatch")
    if document.text[candidate.span_start:candidate.span_end] != candidate.original_text:
        raise ValueError("candidate span fabrication or derivative drift")

    lang = language or getattr(document, "language", None) or "en"
    ntype = _normalize_type(candidate.candidate_type)
    lattice = build_lattice(candidate, document, language=lang,
                            layout_annotation=layout_annotation,
                            document_date=document_date)
    best = next((item for item in lattice.interpretations
                 if item.interpretation_id == lattice.selected_interpretation_id), None)

    features = extract_features(candidate, document, lattice, best, layout_annotation)
    scores = model.scores(features)
    vetoes = hard_vetoes(candidate, document, lattice, best, features, layout_annotation)
    vetoed = {veto.outcome for veto in vetoes}

    order = sorted(OUTCOMES, key=lambda o: (-scores[o], OUTCOMES.index(o)))
    top_unvetoed = next((o for o in order if o not in vetoed), "REJECTED")
    stage = top_unvetoed

    reasons = sorted({veto.reason for veto in vetoes if veto.outcome == order[0]})
    if stage == order[0]:
        rationale = (f"ranker selected {stage} (score {scores[stage]}) over "
                     f"{len(OUTCOMES) - 1} alternatives")
    else:
        rationale = (f"ranker preferred {order[0]} (score {scores[order[0]]}) but "
                     f"hard invariants vetoed it ({reasons}); selected {stage}")

    return RankedDecision(
        stable_id("v5-3-ranked", candidate.candidate_id, stage),
        candidate.candidate_id, document.document_id, stage,
        _function_for(stage, ntype, candidate.original_text),
        best.level if best else None,
        (best.span_start, best.span_end) if best else None,
        best.lifecycle_state if best else "UNKNOWN",
        best.evidence_act if best else "ACTOR_INTENTION",
        best.polarity if best else None, best.modality if best else None,
        best.temporal_scope if best else (None, None),
        best.subject if best else None, best.predicate if best else None,
        best.object_or_value if best else None, best.attribution if best else None,
        best.missing_roles if best and stage != "ACCEPTED_CANDIDATE" else (),
        best.warnings if best else (), max(0, len(lattice.interpretations) - 1),
        scores, top_unvetoed,
        tuple({"outcome": v.outcome, "reason": v.reason, "detail": v.detail}
              for v in vetoes),
        features, rationale, lattice.lattice_id, model.version, now_utc())


# ---------------------------------------------------------------------------
# Section 6.4 — pairwise preference records
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class PreferenceRecord(Record):
    """Why one outcome is preferred over another on a development case."""

    preference_id: str
    candidate_id: str
    preferred: str
    over: str
    reason: str
    features: Mapping[str, float]


PREFERENCE_REASONS = (
    "COMPLETE_MODALITY", "CORRECT_ACTOR", "NO_ATTRIBUTION_CONTAMINATION",
    "VALID_TABLE_CONTEXT", "NO_CHROME", "CORRECT_LIFECYCLE_INTERPRETATION",
    "SENTENCE_ALIGNED", "RECOVERABLE_WITH_CONTEXT", "CANNOT_CARRY_TYPE",
    "STRUCTURAL_FURNITURE", "MAPPING_PRECISION",
)


def preference(candidate_id: str, preferred: str, over: str, reason: str,
               features: Mapping[str, float]) -> PreferenceRecord:
    if reason not in PREFERENCE_REASONS:
        raise ValueError(f"unknown preference reason: {reason}")
    return PreferenceRecord(
        stable_id("v5-3-preference", candidate_id, preferred, over),
        candidate_id, preferred, over, reason, dict(features))


def ranking_report(decisions: Iterable[RankedDecision | Mapping[str, Any]]
                   ) -> dict[str, Any]:
    """How often the learned score agreed with the invariants."""
    total = agreed = overruled = 0
    stages: dict[str, int] = {}
    veto_reasons: dict[str, int] = {}
    for item in decisions:
        get = (item.get if isinstance(item, Mapping)
               else lambda key, obj=item: getattr(obj, key, None))
        total += 1
        stage = str(get("stage"))
        stages[stage] = stages.get(stage, 0) + 1
        scores = dict(get("ranked_scores") or {})
        if scores:
            top = max(scores, key=lambda o: (scores[o], -OUTCOMES.index(o)))
            if top == stage:
                agreed += 1
            else:
                overruled += 1
        for veto in (get("vetoes") or ()):
            reason = veto.get("reason") if isinstance(veto, Mapping) else None
            if reason:
                veto_reasons[reason] = veto_reasons.get(reason, 0) + 1
    return {
        "decisions": total, "stage_counts": stages,
        "ranker_top_choice_taken": agreed,
        "ranker_top_choice_vetoed": overruled,
        "veto_reason_counts": veto_reasons,
        "invariant_override_rate": round(overruled / max(1, total), 4),
    }
