"""Model-assisted analytical proposals through the attributed inference plane.

No provider is hard-coded into the analytical core. When a model is
configured, every invocation is recorded as an operational InferenceRecord
(model identity, hashed inputs/outputs, parameters) and its output enters the
system only as an ANALYTICAL_OBJECT_CANDIDATE proposal awaiting a human
resolution through `substrate.resolve_candidate`.

When no provider is configured, the capability is honestly UNAVAILABLE:
deterministic candidates and analyst acts still work, and nothing pretends
to be semantic judgment. Replay never re-invokes a provider — historical
inferences are replayed from their retained records like all other state.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Mapping

from curunir_operational.canonical import sha256
from curunir_operational.contracts import InferenceRecord, ModelPackage

from .substrate import AnalyticContext, record_candidate

InferFn = Callable[[str, Mapping[str, Any]], Mapping[str, Any]]


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
    """The one gate between an inference provider and analytical candidates.

    ``infer_fn(task, payload) -> output`` is the injected provider call; when
    it is None the assist is unavailable and says so in a typed result
    instead of raising or silently returning nothing."""
    package: ModelPackage | None = None
    infer_fn: InferFn | None = None
    provider_actor: str = "analytic-assist"

    def available(self) -> bool:
        return self.infer_fn is not None and self.package is not None

    def status(self) -> dict[str, Any]:
        if self.available():
            return {"status": "AVAILABLE", "model_id": self.package.model_id,
                    "provider": self.package.provider}
        return {"status": "ANALYTICAL_PROVIDER_UNAVAILABLE",
                "detail": "no inference provider configured; deterministic "
                          "candidates and analyst acts remain fully available"}

    def _ensure_registered(self, ctx: AnalyticContext) -> None:
        registered = [m for m in ctx.store.records_of("model_package")
                      if m["model_id"] == self.package.model_id]
        if not registered:
            ctx.store.append("MODEL_REGISTERED", self.package,
                             recorded_time=ctx.now_fn(), actor=self.provider_actor)

    def propose(self, ctx: AnalyticContext, *, task: str, target_kind: str,
                inputs: Mapping[str, Any],
                input_refs: tuple[str, ...]) -> dict[str, Any]:
        """Invoke the provider on typed inputs; retain the full inference
        record; land the output as a PROPOSED candidate. Returns a typed
        failure when unavailable or when the provider errors."""
        if not self.available():
            return self.status()
        self._ensure_registered(ctx)
        started = ctx.now_fn()
        try:
            output = dict(self.infer_fn(task, inputs))
            errors: tuple[str, ...] = ()
        except Exception as error:
            output = {}
            errors = (f"{type(error).__name__}: {str(error)[:300]}",)
        inference = InferenceRecord(
            inference_id=f"inf-{sha256({'m': self.package.model_id, 't': task, 'i': dict(inputs), 's': started})[:20]}",
            model_id=self.package.model_id, model_version=self.package.version,
            input_refs=input_refs, input_hash=sha256(dict(inputs)),
            output=output, output_hash=sha256(output),
            started=started, completed=ctx.now_fn(),
            parameters={"task": task}, errors=errors,
            validation="INVALID" if errors else "VALID", marking=ctx.marking,
            downstream_use=("analytical_object_candidate",))
        ctx.store.append("INFERENCE_RECORDED", inference,
                         recorded_time=inference.completed, actor=self.provider_actor)
        if errors:
            return {"status": "PROVIDER_ERROR", "inference_id": inference.inference_id,
                    "errors": list(errors)}
        try:
            proposal = record_candidate(ctx, target_kind=target_kind,
                                        content=output,
                                        inference_id=inference.inference_id)
        except ValueError as error:
            # a model omitting its kind's binding fields is a malformed
            # candidate: a typed failure with the inference retained, matching
            # the PROVIDER_ERROR path rather than raising out of the gate
            return {"status": "MALFORMED_CANDIDATE",
                    "inference_id": inference.inference_id,
                    "errors": [str(error)[:300]]}
        return {"status": "PROPOSED", "inference_id": inference.inference_id,
                "proposal": proposal}
