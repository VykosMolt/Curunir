"""Evaluation provenance: four strictly separated layers (contract Sections 4-5).

V5.2's clean evaluation produced two numbers that looked like capability and
were not.  On Surfaces 3 and 5 the corpus builder wrote the sealed answer from
its own heuristic and the frozen classifiers were never invoked, so the panel
scored the builder.  The numbers were recorded INVALID, but nothing in the
architecture had prevented them.

This module makes that failure structural rather than procedural:

* ``RawEvidenceRecord`` — bytes and directly observable source properties.
  No system inference of any kind.
* ``NormalizedObservationRecord`` — a provenance-backed normalization of raw
  evidence, carrying its exact raw support, the transformation applied, and
  whether the observation is explicit in the source or inferred.  It is *not*
  a semantic role and may not name one.
* ``ProductionPredictionRecord`` — what frozen production decided.  It can
  only be constructed through ``record_prediction``, which resolves the
  producing callable to a file, hashes that file, and refuses any producer
  outside the registered production set.  An evaluator module cannot mint a
  prediction even by importing this one.
* ``ReviewerDecisionRecord`` — an independent judgement over a blinded
  dossier.

``ScoringComparisonRecord`` compares exactly one frozen production prediction
against exactly one frozen reviewer majority.  A unit with no genuine
production prediction scores ``INVALID_FOR_CAPABILITY_SCORING`` and can never
contribute to a capability figure.

Research shadow only.
"""
from __future__ import annotations

import hashlib
import inspect
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence

from ..v4.models import canonical_json, require_aware, require_hash
from ..v5_1.models import Record, now_utc, sha256, stable_id

# ---------------------------------------------------------------------------
# The production set: only these modules may produce a prediction
# ---------------------------------------------------------------------------

# Files whose decisions count as Curunír production output.  The evaluator
# modules (dossiers, corpus building, scoring, human packets) are deliberately
# absent: a prediction attributed to any of them is refused at construction.
PRODUCTION_MODULES: tuple[str, ...] = (
    "curunir_operational/v5_2/lifecycle.py",
    "curunir_operational/v5_2/semantics.py",
    "curunir_operational/v5_2/chrome.py",
    "curunir_operational/v5_2/source_origin.py",
    "curunir_operational/v5_2/dependence.py",
    "curunir_operational/v5_2/temporal.py",
    "curunir_operational/v5_2/claim_support.py",
    "curunir_operational/v5_2/reporting.py",
    "curunir_operational/v5_3/ranking.py",
    "curunir_operational/v5_3/observations.py",
    "curunir_operational/v5_3/roles.py",
    "curunir_operational/v5_3/revision.py",
    "curunir_operational/v5_3/predict.py",
)

# Modules that must never appear as the producer of a scored prediction.
EVALUATOR_MODULES: tuple[str, ...] = (
    "curunir_operational/v5_2/dossiers.py",
    "curunir_operational/v5_2/heldout.py",
    "curunir_operational/v5_2/human_packets.py",
    "curunir_operational/v5_3/corpus.py",
    "curunir_operational/v5_3/scoring.py",
)

SURFACES: tuple[str, ...] = (
    "SURFACE_1_SEMANTIC_EXTRACTION",
    "SURFACE_2_SOURCE_IDENTITY_AND_ORIGIN",
    "SURFACE_3_DEPENDENCE_AND_CORROBORATION",
    "SURFACE_4_CLAIM_SUPPORT",
    "SURFACE_5_TEMPORAL_RELATIONS",
    "SURFACE_6_REPORT_FAITHFULNESS",
)

OBSERVATION_MODES = ("EXPLICIT", "INFERRED")

# Section 7.4 — how strongly an observation supports anything at all.
EVIDENCE_STRENGTHS = ("EXPLICIT", "STRONGLY_IMPLIED", "WEAKLY_IMPLIED",
                      "CONFLICTING", "ABSENT")


class ProvenanceViolation(ValueError):
    """A layer boundary was crossed.  Never downgraded to a warning."""


def _repo_relative(path: str | Path) -> str:
    text = str(path).replace("\\", "/")
    marker = "curunir_operational/"
    index = text.find(marker)
    return text[index:] if index >= 0 else text


def _file_hash(path: str | Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


# ---------------------------------------------------------------------------
# Layer 1 — raw evidence
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class RawEvidenceRecord(Record):
    """Bytes and directly observable properties.  No inference."""

    evidence_id: str
    source_object_id: str
    document_id: str | None
    locator: Mapping[str, Any]
    raw_text: str
    media_type: str
    content_hash: str
    observed_time: str

    def __post_init__(self) -> None:
        require_aware(self.observed_time)
        require_hash(self.content_hash)
        if not self.source_object_id.strip():
            raise ProvenanceViolation("raw evidence requires a custody source object")
        if not self.locator:
            raise ProvenanceViolation("raw evidence requires a locator into the source")


def raw_evidence(*, source_object_id: str, raw_text: str, locator: Mapping[str, Any],
                 content_hash: str, document_id: str | None = None,
                 media_type: str = "text/plain") -> RawEvidenceRecord:
    return RawEvidenceRecord(
        stable_id("v5-3-raw", source_object_id, canonical_json(dict(locator)),
                  sha256(raw_text)),
        source_object_id, document_id, dict(locator), raw_text, media_type,
        content_hash, now_utc())


# ---------------------------------------------------------------------------
# Layer 2 — normalized observation
# ---------------------------------------------------------------------------

# Vocabulary an observation may use.  Deliberately disjoint from the source
# role vocabulary: an observation records what the document says, never what
# role that implies.  ``PUBLISHED_BY`` is a role; ``EXPLICIT_PUBLISHER_LINE``
# is an observation that a publisher line exists and what it reads.
OBSERVATION_TYPES: tuple[str, ...] = (
    "EXPLICIT_AUTHOR_LINE", "EXPLICIT_EDITOR_LINE", "EXPLICIT_ISSUER_LINE",
    "EXPLICIT_PUBLISHER_LINE", "EXPLICIT_SUBMISSION_LINE",
    "INSTITUTIONAL_ATTRIBUTION", "TITLE_PAGE_INSTITUTION",
    "DOMAIN_HOST", "REDIRECT_CHAIN", "DOCUMENT_SERIES_OWNER",
    "COPYRIGHT_HOLDER", "ARCHIVE_PROVIDER", "MIRROR_NOTICE",
    "TRANSLATION_NOTICE", "SYNDICATION_NOTICE", "COMMISSIONING_NOTICE",
    "OPERATING_ENTITY_NOTICE", "OWNERSHIP_RECORD",
    "PUBLICATION_DATE", "REVISION_STATEMENT", "DOCUMENT_IDENTIFIER",
    "HEADER_TEXT", "FOOTER_TEXT", "TABLE_HEADER", "CAPTION",
    "QUOTED_SENTENCE", "NUMERIC_VALUE", "CONTACT_BLOCK",
)

# Any observation type naming a source role would collapse layers 2 and 3.
_ROLE_WORDS = frozenset({
    "AUTHORED_BY", "EDITED_BY", "SUBMITTED_BY", "ISSUED_BY", "PUBLISHED_BY",
    "HOSTED_BY", "MIRRORED_BY", "ARCHIVED_BY", "TRANSLATED_BY",
    "SYNDICATED_BY", "COMMISSIONED_BY", "OWNED_BY", "OPERATED_BY",
})
for _name in OBSERVATION_TYPES:
    assert _name not in _ROLE_WORDS, f"observation type {_name} names a role"


@dataclass(frozen=True)
class NormalizedObservationRecord(Record):
    """What the source says, normalized, with its exact raw support.

    Carries no semantic role.  ``semantic_role_supplied`` exists so a dossier
    renderer can assert, in the artifact, that no role reached the reviewer.
    """

    observation_id: str
    raw_evidence_ids: tuple[str, ...]
    observation_type: str
    observed_value: str
    normalization: str
    mode: str
    strength: str
    provenance: Mapping[str, Any]
    rationale: str
    semantic_role_supplied: bool
    recorded_time: str

    def __post_init__(self) -> None:
        if self.observation_type not in OBSERVATION_TYPES:
            raise ProvenanceViolation(
                f"unknown observation type: {self.observation_type}")
        if self.mode not in OBSERVATION_MODES:
            raise ProvenanceViolation(f"unknown observation mode: {self.mode}")
        if self.strength not in EVIDENCE_STRENGTHS:
            raise ProvenanceViolation(f"unknown evidence strength: {self.strength}")
        if not self.raw_evidence_ids:
            raise ProvenanceViolation(
                "a normalized observation requires its raw support; an "
                "observation without raw evidence is an inference wearing a "
                "normalization's name")
        if self.semantic_role_supplied:
            raise ProvenanceViolation(
                "a normalized observation may not carry a semantic role; that "
                "is a layer-3 production decision")
        if self.observed_value.upper() in _ROLE_WORDS:
            raise ProvenanceViolation(
                "observed value is a role name, not an observation")
        require_aware(self.recorded_time)


def normalized_observation(*, raw_evidence_ids: Iterable[str],
                           observation_type: str, observed_value: str,
                           normalization: str, mode: str = "EXPLICIT",
                           strength: str = "EXPLICIT",
                           provenance: Mapping[str, Any] | None = None,
                           rationale: str = "") -> NormalizedObservationRecord:
    ids = tuple(raw_evidence_ids)
    return NormalizedObservationRecord(
        stable_id("v5-3-observation", observation_type, observed_value, "|".join(ids)),
        ids, observation_type, observed_value, normalization, mode, strength,
        dict(provenance or {}), rationale or f"{observation_type} read from source",
        False, now_utc())


# ---------------------------------------------------------------------------
# Layer 3 — production prediction
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ProductionPredictionRecord(Record):
    """What frozen production decided, bound to the code that decided it."""

    prediction_id: str
    surface: str
    production_object_id: str
    producer_module: str
    producer_function: str
    production_code_sha256: str
    input_evidence_ids: tuple[str, ...]
    input_observation_ids: tuple[str, ...]
    prediction: str
    structured_rationale: Mapping[str, Any]
    predicted_time: str
    integrity_hash: str

    def __post_init__(self) -> None:
        if self.surface not in SURFACES:
            raise ProvenanceViolation(f"unknown surface: {self.surface}")
        if not self.prediction.strip():
            raise ProvenanceViolation("a prediction must have a value")
        if self.producer_module in EVALUATOR_MODULES:
            raise ProvenanceViolation(
                f"{self.producer_module} is evaluation code and may never "
                "produce a scored prediction")
        if self.producer_module not in PRODUCTION_MODULES:
            raise ProvenanceViolation(
                f"{self.producer_module} is not a registered production module")
        require_hash(self.production_code_sha256)
        require_aware(self.predicted_time)
        if self.integrity_hash != self._compute_integrity():
            raise ProvenanceViolation("prediction integrity hash mismatch")

    def _compute_integrity(self) -> str:
        return sha256({
            "surface": self.surface,
            "production_object_id": self.production_object_id,
            "producer_module": self.producer_module,
            "producer_function": self.producer_function,
            "production_code_sha256": self.production_code_sha256,
            "input_evidence_ids": list(self.input_evidence_ids),
            "input_observation_ids": list(self.input_observation_ids),
            "prediction": self.prediction,
            "structured_rationale": json.loads(
                canonical_json(dict(self.structured_rationale))),
        })


def record_prediction(*, surface: str, production_object_id: str,
                      producer: Callable[..., Any], prediction: str,
                      structured_rationale: Mapping[str, Any],
                      input_evidence_ids: Iterable[str] = (),
                      input_observation_ids: Iterable[str] = ()
                      ) -> ProductionPredictionRecord:
    """Mint a prediction, binding it to the file that produced it.

    The producer is resolved to a source file and that file is hashed, so a
    prediction cannot be attributed to production code that did not run and
    cannot survive a later edit of that code undetected.
    """
    try:
        source_file = inspect.getsourcefile(producer) or ""
    except TypeError as exc:
        raise ProvenanceViolation(f"producer is not resolvable to code: {exc}") from None
    module = _repo_relative(source_file)
    if not module:
        raise ProvenanceViolation("producer has no resolvable module path")
    evidence = tuple(input_evidence_ids)
    observations = tuple(input_observation_ids)
    rationale = json.loads(canonical_json(dict(structured_rationale)))
    body = {
        "surface": surface, "production_object_id": production_object_id,
        "producer_module": module,
        "producer_function": getattr(producer, "__qualname__", str(producer)),
        "production_code_sha256": _file_hash(source_file),
        "input_evidence_ids": list(evidence),
        "input_observation_ids": list(observations),
        "prediction": prediction, "structured_rationale": rationale,
    }
    return ProductionPredictionRecord(
        stable_id("v5-3-prediction", surface, production_object_id, prediction),
        surface, production_object_id, module, body["producer_function"],
        body["production_code_sha256"], evidence, observations, prediction,
        rationale, now_utc(), sha256(body))


def write_prediction_manifest(predictions: Sequence[ProductionPredictionRecord],
                              path: str | Path) -> dict[str, Any]:
    """Freeze the prediction manifest before any dossier is reviewed."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("w") as handle:
        for record in predictions:
            handle.write(json.dumps(record.to_record(), sort_keys=True,
                                    ensure_ascii=False) + "\n")
    by_surface: dict[str, int] = {}
    modules: dict[str, str] = {}
    for record in predictions:
        by_surface[record.surface] = by_surface.get(record.surface, 0) + 1
        modules[record.producer_module] = record.production_code_sha256
    return {
        "manifest_path": str(target),
        "predictions": len(predictions),
        "per_surface": by_surface,
        "producer_modules": modules,
        "evaluator_produced_predictions": 0,
        "manifest_hash": sha256([r.integrity_hash for r in predictions]),
        "frozen_time": now_utc(),
    }


def load_prediction_manifest(path: str | Path) -> dict[str, ProductionPredictionRecord]:
    records: dict[str, ProductionPredictionRecord] = {}
    for line in Path(path).read_text().splitlines():
        if not line.strip():
            continue
        payload = json.loads(line)
        payload["input_evidence_ids"] = tuple(payload["input_evidence_ids"])
        payload["input_observation_ids"] = tuple(payload["input_observation_ids"])
        record = ProductionPredictionRecord(**payload)
        records[record.production_object_id] = record
    return records


# ---------------------------------------------------------------------------
# Layer 4 — reviewer decision
# ---------------------------------------------------------------------------

NON_LABEL_DECISIONS = ("INSUFFICIENT_INFORMATION", "EPISTEMICALLY_UNRESOLVABLE",
                       "CANNOT_ADJUDICATE", "DOSSIER_DEFECT")


@dataclass(frozen=True)
class ReviewerDecisionRecord(Record):
    decision_id: str
    dossier_id: str
    surface: str
    reviewer: str
    decision: str
    label: str | None
    reasoning: str
    confidence: str
    other_reviews_seen: bool
    human_review: bool

    def __post_init__(self) -> None:
        if self.other_reviews_seen:
            raise ProvenanceViolation("reviewer isolation was broken")
        if self.human_review:
            raise ProvenanceViolation(
                "no human study is run in this milestone; a human_review flag "
                "on a model decision would misreport the panel")
        if self.decision == "LABELED" and not self.label:
            raise ProvenanceViolation("a labelled decision requires its label")


# ---------------------------------------------------------------------------
# Scoring comparison — the only place a capability figure may come from
# ---------------------------------------------------------------------------

SCORING_OUTCOMES = ("CORRECT", "INCORRECT", "GENUINELY_UNRESOLVABLE",
                    "REVIEWER_DISAGREEMENT", "CONSTRUCTION_DEFECT",
                    "INVALID_FOR_CAPABILITY_SCORING")


@dataclass(frozen=True)
class ScoringComparisonRecord(Record):
    """One frozen production prediction against one frozen reviewer majority."""

    comparison_id: str
    dossier_id: str
    surface: str
    prediction_id: str | None
    production_prediction: str | None
    producer_module: str | None
    reviewer_majority: str | None
    reviewer_votes: Mapping[str, int]
    unanimous: bool
    outcome: str
    detail: str

    def __post_init__(self) -> None:
        if self.outcome not in SCORING_OUTCOMES:
            raise ProvenanceViolation(f"unknown scoring outcome: {self.outcome}")
        if self.outcome in ("CORRECT", "INCORRECT") and not self.prediction_id:
            raise ProvenanceViolation(
                "a capability outcome requires a frozen production prediction; "
                "this is exactly the V5.2 defect")
        if self.producer_module in EVALUATOR_MODULES:
            raise ProvenanceViolation(
                "a scored comparison may never cite evaluation code as producer")


def compare(*, dossier_id: str, surface: str,
            prediction: ProductionPredictionRecord | None,
            reviewer_majority: str | None, reviewer_votes: Mapping[str, int],
            unanimous: bool, construction_defect: bool = False,
            unresolvable_certified: bool = False) -> ScoringComparisonRecord:
    """Score one unit.  Refuses to invent a capability figure."""
    base = dict(
        comparison_id=stable_id("v5-3-comparison", dossier_id, surface),
        dossier_id=dossier_id, surface=surface,
        prediction_id=prediction.prediction_id if prediction else None,
        production_prediction=prediction.prediction if prediction else None,
        producer_module=prediction.producer_module if prediction else None,
        reviewer_majority=reviewer_majority, reviewer_votes=dict(reviewer_votes),
        unanimous=unanimous)
    if construction_defect:
        return ScoringComparisonRecord(**base, outcome="CONSTRUCTION_DEFECT",
                                       detail="dossier was defective; excluded")
    if prediction is None:
        return ScoringComparisonRecord(
            **base, outcome="INVALID_FOR_CAPABILITY_SCORING",
            detail=("no frozen production prediction exists for this unit, so "
                    "no capability figure may be derived from it"))
    if reviewer_majority is None:
        return ScoringComparisonRecord(**base, outcome="REVIEWER_DISAGREEMENT",
                                       detail="no majority among three reviewers")
    if reviewer_majority in NON_LABEL_DECISIONS:
        if reviewer_majority == "EPISTEMICALLY_UNRESOLVABLE" and unresolvable_certified:
            return ScoringComparisonRecord(
                **base, outcome="GENUINELY_UNRESOLVABLE",
                detail="reviewers and the sufficiency validator agree the public "
                       "evidence cannot settle this")
        return ScoringComparisonRecord(
            **base, outcome="INCORRECT",
            detail=f"reviewers returned {reviewer_majority} on a dossier certified "
                   "self-sufficient")
    outcome = "CORRECT" if reviewer_majority == prediction.prediction else "INCORRECT"
    return ScoringComparisonRecord(
        **base, outcome=outcome,
        detail=f"production {prediction.prediction} vs reviewers {reviewer_majority}")


def provenance_audit(comparisons: Iterable[ScoringComparisonRecord | Mapping[str, Any]]
                     ) -> dict[str, Any]:
    """Did any scored answer come from the corpus builder or the evaluator?

    The closure gate requires zero.
    """
    from_evaluator = 0
    without_prediction = 0
    scored = 0
    modules: dict[str, int] = {}
    for item in comparisons:
        get = (item.get if isinstance(item, Mapping)
               else lambda key, obj=item: getattr(obj, key, None))
        outcome = str(get("outcome"))
        module = get("producer_module")
        if outcome in ("CORRECT", "INCORRECT"):
            scored += 1
            if not get("prediction_id"):
                without_prediction += 1
            if module in EVALUATOR_MODULES:
                from_evaluator += 1
        if module:
            modules[str(module)] = modules.get(str(module), 0) + 1
    return {
        "scored_units": scored,
        "scored_answers_from_evaluator": from_evaluator,
        "scored_units_without_production_prediction": without_prediction,
        "producer_module_counts": modules,
        "clean": from_evaluator == 0 and without_prediction == 0,
    }
