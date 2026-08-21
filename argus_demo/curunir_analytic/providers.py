"""Governed model-assisted analytical proposals.

This module is the sole analytical-provider boundary. It derives the effective
sensitivity from authoritative input records, refuses disallowed egress before
invocation, records every attempted invocation, and admits a successful
response only as a human-review candidate. Replay consumes the retained
inference record and never calls a provider again.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Mapping

from curunir_operational.access import Marking, inherited_marking
from curunir_operational.canonical import sha256, validate_interchange
from curunir_operational.contracts import InferenceRecord, ModelPackage
from curunir_operational.security import (MaterialReference,
                                           resolve_reference_records)

from .substrate import AnalyticContext, record_candidate

InferFn = Callable[[str, Mapping[str, Any]], Mapping[str, Any]]


def _clean_text(value: object) -> str:
    """Produce bounded, valid Unicode for an external diagnostic."""
    return str(value)[:300].encode("utf-8", "replace").decode("utf-8")


def _provider_json(value: Any, *, _depth: int = 0) -> Any:
    """Normalize a provider response into the Curunir interchange domain.

    JSON containers and finite scalar values retain their meaning. Strings are
    repaired only for malformed Unicode. Unsupported Python objects,
    non-string keys, non-finite numbers, and excessive nesting are rejected;
    they must not be silently converted into a plausible analytical result.
    """
    if _depth > 256:
        raise ValueError("provider response exceeds maximum nesting depth")
    if isinstance(value, str):
        return value.encode("utf-8", "replace").decode("utf-8")
    if value is None or isinstance(value, (bool, int, float)):
        validate_interchange(value)
        return value
    if isinstance(value, Mapping):
        result: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise ValueError("provider response object keys must be strings")
            clean_key = _provider_json(key, _depth=_depth + 1)
            if clean_key in result:
                raise ValueError("provider response keys collide after Unicode repair")
            result[clean_key] = _provider_json(item, _depth=_depth + 1)
        return result
    if isinstance(value, (list, tuple)):
        return [_provider_json(item, _depth=_depth + 1) for item in value]
    raise ValueError(f"unsupported provider response type: {type(value).__name__}")


def _payload_strings(value: Any):
    if isinstance(value, str):
        yield value
    elif isinstance(value, Mapping):
        for item in value.values():
            yield from _payload_strings(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            yield from _payload_strings(item)


def _effective_input_marking(
    ctx: AnalyticContext,
    inputs: Mapping[str, Any],
    input_refs: tuple[str, ...],
) -> tuple[Marking, tuple[str, ...], tuple[str, ...]]:
    """Resolve declared and payload-carried record ids against raw state."""
    markings: list[Mapping[str, Any]] = []
    missing: list[str] = []
    resolved_refs: list[str] = []
    candidates = list(dict.fromkeys((*input_refs, *_payload_strings(inputs))))
    for record_id in candidates:
        records = resolve_reference_records(
            ctx.store, (MaterialReference("*", record_id),))
        marked = [record["marking"] for record in records
                  if isinstance(record.get("marking"), Mapping)]
        if not marked and record_id in input_refs:
            missing.append(record_id)
        elif marked:
            markings.extend(marked)
            resolved_refs.append(record_id)
    return (inherited_marking(ctx.marking, markings), tuple(missing),
            tuple(resolved_refs))


def analytical_assist_package(provider: str, model_id: str, version: str) -> ModelPackage:
    return ModelPackage(
        model_id=model_id, version=version, provider=provider,
        task="analytical-object-candidate-proposal",
        input_schema_id="curunir-analytic-candidate-input",
        output_schema_id="curunir-analytic-candidate-output",
        training_data="external provider; not trained on mission data",
        evaluation_summary="not independently evaluated in this deployment",
        limitations=("candidates require human acceptance",
                     "no calibrated confidence",
                     "must not be treated as evidence"),
        approved_uses=("proposing analytical object candidates over "
                       "evidence-bound world-model state",),
        prohibited_uses=("closing requirements", "accepting its own proposals",
                         "asserting evidence-free analytical state"),
        latency_profile="provider-dependent", hardware="external",
        licence="provider terms", accreditation_state="UNACCREDITED",
    )


@dataclass
class AnalyticalAssist:
    """The one governed gate between an inference provider and candidates."""

    package: ModelPackage | None = None
    infer_fn: InferFn | None = None
    provider_actor: str = "analytic-assist"
    allowed_input_marking: Marking | None = None

    def available(self) -> bool:
        return self.infer_fn is not None and self.package is not None

    def status(self) -> dict[str, Any]:
        if self.available():
            return {"status": "AVAILABLE", "model_id": self.package.model_id,
                    "provider": self.package.provider}
        return {"status": "ANALYTICAL_PROVIDER_UNAVAILABLE",
                "detail": "no inference provider configured; deterministic "
                          "candidates and analyst acts remain fully available"}

    def _egress_refusal(self, effective: Marking) -> str | None:
        ceiling = self.allowed_input_marking
        if ceiling is None:
            # The legacy unconfigured mode remains public-releasable only.
            if (effective.compartments or effective.min_role != "OBSERVER"
                    or "PUBLIC" not in effective.releasability):
                return ("provider declares no egress policy; only "
                        "public-releasable evidence may be sent")
            return None
        try:
            joined = inherited_marking(ceiling, [effective])
        except ValueError:
            return "input marking is incompatible with provider policy"
        if joined.to_record() != ceiling.to_record():
            return "input is more restricted than this provider may receive"
        return None

    def _ensure_registered(self, ctx: AnalyticContext) -> None:
        registered = [m for m in ctx.store.records_of("model_package")
                      if m["model_id"] == self.package.model_id]
        if not registered:
            ctx.store.append("MODEL_REGISTERED", self.package,
                             recorded_time=ctx.now_fn(), actor=self.provider_actor)

    def propose(self, ctx: AnalyticContext, *, task: str, target_kind: str,
                inputs: Mapping[str, Any],
                input_refs: tuple[str, ...]) -> dict[str, Any]:
        """Invoke once after egress admission and retain the complete outcome."""
        if not self.available():
            return self.status()
        try:
            validate_interchange(task)
            clean_inputs = dict(inputs)
            validate_interchange(clean_inputs)
            validate_interchange(list(input_refs))
        except (TypeError, ValueError) as error:
            return {"status": "INVALID_PROVIDER_INPUT", "errors": [_clean_text(error)]}

        effective, missing, effective_refs = _effective_input_marking(
            ctx, clean_inputs, input_refs)
        if missing:
            return {"status": "EGRESS_REFUSED",
                    "detail": "input references do not resolve to marked authoritative records",
                    "missing_input_refs": list(missing),
                    "model_id": self.package.model_id}
        refusal = self._egress_refusal(effective)
        if refusal is not None:
            return {"status": "EGRESS_REFUSED", "detail": refusal,
                    "model_id": self.package.model_id,
                    "input_marking": effective.to_record()}

        self._ensure_registered(ctx)
        started = ctx.now_fn()
        try:
            raw_output = self.infer_fn(task, clean_inputs)
            if not isinstance(raw_output, Mapping):
                raise ValueError("provider response must be an object")
            output = _provider_json(raw_output)
            validate_interchange(output)
            errors: tuple[str, ...] = ()
        except MemoryError:
            raise
        except Exception as error:  # provider/data boundary, retained below
            output = {}
            errors = (f"{type(error).__name__}: {_clean_text(error)}",)

        completed = ctx.now_fn()
        inference = InferenceRecord(
            inference_id=f"inf-{sha256({'m': self.package.model_id, 't': task, 'i': clean_inputs, 's': started})[:20]}",
            model_id=self.package.model_id, model_version=self.package.version,
            input_refs=effective_refs, input_hash=sha256(clean_inputs),
            output=output, output_hash=sha256(output),
            started=started, completed=completed,
            parameters={"task": task}, errors=errors,
            validation="INVALID" if errors else "VALID", marking=effective,
            downstream_use=("analytical_object_candidate",))
        ctx.store.append("INFERENCE_RECORDED", inference,
                         recorded_time=completed, actor=self.provider_actor)
        if errors:
            return {"status": "PROVIDER_ERROR", "inference_id": inference.inference_id,
                    "errors": list(errors)}
        try:
            proposal = record_candidate(ctx, target_kind=target_kind,
                                        content=output,
                                        inference_id=inference.inference_id)
        except ValueError as error:
            return {"status": "MALFORMED_CANDIDATE",
                    "inference_id": inference.inference_id,
                    "errors": [_clean_text(error)]}
        return {"status": "PROPOSED", "inference_id": inference.inference_id,
                "proposal": proposal}
