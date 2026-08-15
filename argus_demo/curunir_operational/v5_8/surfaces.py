"""V5.8 §7, §21 — one production entry point per surface, and nothing else.

Four of the five repairs that invalidated the V5.7 freeze were the same mistake:
a prediction script called an API that did not exist, the call failed, the
surface emitted nothing, and the run continued.  A guessed name fails loudly
here instead, and every prediction records the module and code hash that
produced it.

The evaluator may score what these return.  It may not author it.
"""

from __future__ import annotations

import hashlib
import inspect
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from ..v5_1.models import SOURCE_ROLES, now_utc, sha256, stable_id
from ..v5_4 import activation as ACT
from ..v5_4 import publication as PUB
from ..v5_4 import roles_binary as RB
from ..v5_4 import support as SUP
from ..v5_7 import extraction as EX
from ..v5_7 import states as ST

_ROOT = Path(__file__).resolve().parents[2]


class SurfaceFailure(RuntimeError):
    """A production surface could not answer, and said so instead of returning None."""


def _hash(relative: str) -> str:
    return hashlib.sha256((_ROOT / relative).read_bytes()).hexdigest()


#: The real entry point for each surface, resolved from the module by name.  A
#: name that does not resolve raises at import, not at prediction time.
SURFACES: Mapping[str, Mapping[str, Any]] = {
    "SURFACE_1_EXTRACTION": {
        "module": "curunir_operational/v5_7/extraction.py",
        "callable": EX.decide, "name": "decide",
        "vocabulary": ST.TERMINAL_STATES_V2},
    "SURFACE_2_SOURCE_ROLES": {
        "module": "curunir_operational/v5_4/roles_binary.py",
        "callable": RB.predict_role, "name": "predict_role",
        "vocabulary": tuple(SOURCE_ROLES)},
    "SURFACE_3_DEPENDENCE": {
        "module": "curunir_operational/v5_4/activation.py",
        "callable": ACT.classify_dependence, "name": "classify_dependence",
        "vocabulary": tuple(ACT.DEPENDENCE_CLASSES)},
    "SURFACE_4_CLAIM_SUPPORT": {
        "module": "curunir_operational/v5_4/support.py",
        "callable": SUP.assess, "name": "assess", "vocabulary": ()},
    "SURFACE_5_TEMPORAL": {
        "module": "curunir_operational/v5_4/activation.py",
        "callable": ACT.classify_temporal, "name": "classify_temporal",
        "vocabulary": tuple(ACT.TEMPORAL_CLASSES)},
    "SURFACE_6_PUBLICATION": {
        "module": "curunir_operational/v5_4/publication.py",
        "callable": PUB.decide, "name": "decide", "vocabulary": ()},
}

for _name, _spec in SURFACES.items():
    if not callable(_spec["callable"]):
        raise SurfaceFailure(f"{_name} entry point does not resolve")


def interface_manifest() -> dict[str, Any]:
    return {
        "surfaces": {
            name: {"module": spec["module"], "callable": spec["name"],
                   "signature": str(inspect.signature(spec["callable"])),
                   "code_hash": _hash(spec["module"]),
                   "vocabulary": list(spec["vocabulary"])}
            for name, spec in SURFACES.items()},
        "guessed_production_apis": 0,
        "recorded_time": now_utc(),
    }


def _record(surface: str, unit_id: str, prediction: Any, payload: Mapping[str, Any],
            inputs: Sequence[str], rationale: Mapping[str, Any]) -> dict[str, Any]:
    spec = SURFACES[surface]
    row = {
        "prediction_id": stable_id("v5-8-prediction", unit_id, surface),
        "surface": surface, "unit_id": unit_id,
        "production_module": spec["module"], "callable": spec["name"],
        "production_code_hash": _hash(spec["module"]),
        "input_evidence_ids": list(inputs),
        "prediction": prediction,
        "resolved_component_payload": dict(payload),
        "structured_rationale": dict(rationale),
        "authored_by": "PRODUCTION_MODULE",
        "recorded_time": now_utc(),
    }
    row["prediction_hash"] = sha256({k: v for k, v in row.items()
                                     if k != "recorded_time"})
    return row


def predict_extraction(*, candidate: Mapping[str, Any], context: str,
                       invariant_state: str | None = None) -> dict[str, Any]:
    decision = SURFACES["SURFACE_1_EXTRACTION"]["callable"](
        candidate=candidate, context=context,
        declared_language=candidate.get("language"),
        invariant_state=invariant_state)
    return _record("SURFACE_1_EXTRACTION", candidate["candidate_id"],
                   decision.terminal_state,
                   {"terminal_state": decision.terminal_state,
                    "extraction_state": decision.extraction_state,
                    "exact_quotation_permission": decision.exact_quotation_permission,
                    "paraphrase_permission": decision.paraphrase_permission,
                    "wording_as_written_permission": decision.wording_as_written_permission,
                    "boundary_repair_plan": decision.boundary_repair_plan},
                   [candidate["candidate_id"]],
                   {"deciding_gate": decision.deciding_gate,
                    "mechanical_findings": decision.mechanical_findings,
                    "reason": decision.reason})


def predict_role(*, source_id: str, entity_id: str, target_role: str,
                 observations: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    verdict = SURFACES["SURFACE_2_SOURCE_ROLES"]["callable"](
        source_id=source_id, entity_id=entity_id, target_role=target_role,
        observations=observations)
    # The record names its answer `prediction` and its strength
    # `evidence_strength`.  Reading `verdict`/`strength` returned None for every
    # unit while reporting success — the same defect as V5.7's Surface 6.
    value = getattr(verdict, "prediction", None)
    if value is None:
        raise SurfaceFailure(
            f"source-role prediction for {target_role} returned no value")
    return _record("SURFACE_2_SOURCE_ROLES", f"{source_id}:{target_role}",
                   value,
                   {"target_role": target_role, "prediction": value,
                    "evidence_strength": getattr(verdict, "evidence_strength", None),
                    "decisive_observations": list(
                        getattr(verdict, "decisive_observations", ()) or ()),
                    "contradictory_observations": list(
                        getattr(verdict, "contradictory_observations", ()) or ())},
                   [o.get("observation_id", "") for o in observations],
                   {"inference_chain": list(
                       getattr(verdict, "inference_chain", ()) or ())})


def _relations(side: Mapping[str, Any]) -> tuple[Any, ...]:
    """The relations the document states about itself, read from its own text.

    Both relation classifiers take these and consult them before anything else.
    The adapter never supplied them, so every class reachable only through an
    explicit relation — supersession, correction, retraction, derivative — was
    unreachable no matter what the corpus contained.  Production was never
    asked the question it was built to answer.
    """
    text = str(side.get("relation_context") or side.get("text") or "")
    return ACT.detect_relations(text, source_object_id=str(side.get("instrument_id")
                                                           or side.get("source_id") or ""))


def _instrument(side: Mapping[str, Any]) -> str | None:
    """The identifier of the *work*, not of the fetch.

    classify_dependence documents left_identifier/right_identifier as the
    instrument being published; it recognises official multilingual publication
    by the two sides carrying the same instrument identifier.  The adapter
    passed the per-URL source id, which differs by construction for every pair,
    so TRANSLATION_DERIVATIVE could never fire on a corpus built entirely of
    official translations.
    """
    return side.get("instrument_id") or side.get("intellectual_work_id")


def predict_dependence(*, left: Mapping[str, Any], right: Mapping[str, Any],
                       shared_evidence_ids: Sequence[str] = ()) -> dict[str, Any]:
    klass, reason = SURFACES["SURFACE_3_DEPENDENCE"]["callable"](
        left["text"], right["text"],
        left_relations=_relations(left), right_relations=_relations(right),
        shared_evidence_ids=list(shared_evidence_ids),
        left_language=left.get("language"), right_language=right.get("language"),
        left_identifier=_instrument(left), right_identifier=_instrument(right))
    return _record("SURFACE_3_DEPENDENCE",
                   f"{left['proposition_id']}|{right['proposition_id']}", klass,
                   {"dependence_class": klass},
                   [left["proposition_id"], right["proposition_id"]],
                   {"reason": reason,
                    "left_instrument": _instrument(left),
                    "right_instrument": _instrument(right)})


def predict_support(*, claim: Mapping[str, Any],
                    evidence: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    assessment = SURFACES["SURFACE_4_CLAIM_SUPPORT"]["callable"](claim, list(evidence))
    allowed = tuple(getattr(assessment, "allowed_report_wording", ()) or ())
    payload = {"support_class": getattr(assessment, "support_class", None),
               "first_material_failure": getattr(assessment, "first_failing_stage", None),
               "allowed_report_wording": list(allowed),
               "permitted_wording": allowed[0] if allowed else None,
               "lifecycle_verdict": getattr(assessment, "lifecycle_verdict", None),
               "critical_error": getattr(assessment, "critical_error", None)}
    vector = getattr(assessment, "vector", None)
    if vector is not None:
        for field in ("addresses_proposition", "entity_alignment", "predicate_alignment",
                      "object_alignment", "scope_alignment", "time_alignment",
                      "polarity_alignment", "modality_alignment",
                      "lifecycle_alignment", "attribution_alignment",
                      "support_completeness"):
            payload[field] = getattr(vector, field, None)
    return _record("SURFACE_4_CLAIM_SUPPORT", claim["proposition_id"],
                   payload["support_class"], payload,
                   [claim["proposition_id"]] + [e.get("proposition_id", "")
                                                for e in evidence],
                   {"assessment": type(assessment).__name__}), assessment


def _citable(side: Mapping[str, Any]) -> str | None:
    """The identifier by which *other documents cite this one*.

    governs_pair compares the target named in a relation statement against the
    other side's identifier, so the identifier has to be the one that appears in
    text — "484", "2016/679", "15.0.0".  An internal work id can never match a
    target read out of a document, which is why no explicit relation has ever
    governed a pair in this programme's measurements.
    """
    return side.get("citable_identifier") or _instrument(side)


def predict_temporal(*, left: Mapping[str, Any], right: Mapping[str, Any]
                     ) -> dict[str, Any]:
    klass, reason = SURFACES["SURFACE_5_TEMPORAL"]["callable"](
        left["text"], right["text"],
        left_relations=_relations(left), right_relations=_relations(right),
        left_identifier=_citable(left), right_identifier=_citable(right))
    return _record("SURFACE_5_TEMPORAL",
                   f"{left['proposition_id']}|{right['proposition_id']}", klass,
                   {"temporal_class": klass},
                   [left["proposition_id"], right["proposition_id"]],
                   {"reason": reason})


def predict_publication(*, claim: Mapping[str, Any], assessment: Any,
                        section: str = "DETAILED_REPORT") -> dict[str, Any]:
    # The support record names its permitted wordings as `allowed_report_wording`;
    # reading a field that does not exist is how V5.7's Surface 6 returned None
    # for every unit while reporting success.
    allowed = tuple(getattr(assessment, "allowed_report_wording", ()) or ())
    wording = allowed[0] if allowed else "NO_PUBLICATION_PERMITTED"
    contract = SURFACES["SURFACE_6_PUBLICATION"]["callable"](
        proposition_id=claim["proposition_id"], claim=claim, support=assessment,
        wording=wording, section=section)
    disposition = getattr(contract, "publication_disposition", None)
    if disposition is None:
        raise SurfaceFailure(
            "publication returned no disposition; a surface that answers None "
            "while reporting success is the V5.7 Surface 6 defect")
    return _record("SURFACE_6_PUBLICATION", claim["proposition_id"], disposition,
                   {"publication_disposition": disposition,
                    "permitted_wording": wording,
                    "required_qualifications": list(
                        getattr(contract, "required_qualifications", ()) or ()),
                    "blocking_reason": getattr(contract, "blocking_reason", None),
                    "support_class": getattr(assessment, "support_class", None)},
                   [claim["proposition_id"]],
                   {"blocking_reason": getattr(contract, "blocking_reason", None),
                    "derived_downstream_of_support": True})


__all__ = ["SurfaceFailure", "SURFACES", "interface_manifest", "predict_extraction",
           "predict_role", "predict_dependence", "predict_support",
           "predict_temporal", "predict_publication"]
