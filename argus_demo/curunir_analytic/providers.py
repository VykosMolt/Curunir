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

from curunir_operational.access import (Marking, inherited_marking,
                                         marking_from_record)
from curunir_operational.canonical import sha256
from curunir_operational.contracts import InferenceRecord, ModelPackage

from .substrate import AnalyticContext, record_candidate

InferFn = Callable[[str, Mapping[str, Any]], Mapping[str, Any]]


def _input_markings(store, input_refs: tuple[str, ...]) -> list[Marking]:
    """The markings of the material objects an inference is built from —
    claims, world objects, observations, manifestations. The provider egress
    gate joins these so the effective input sensitivity is derived from the
    ACTUAL evidence supplied, never from the caller's context alone."""
    if not input_refs:
        return []
    claims = store.current_claims()
    objects: dict[str, dict] = {}
    for record in store.records_of("object_version"):
        known = objects.get(record["object_id"])
        if known is None or record.get("version", 1) >= known.get("version", 1):
            objects[record["object_id"]] = record
    out: list[Marking] = []
    seen: set[str] = set()
    for ref in input_refs:
        if not ref or ref in seen:
            continue
        seen.add(ref)
        record = claims.get(ref) or objects.get(ref)
        if record is None:
            for family, id_field in (("semantic_observation", "observation_id"),
                                     ("fabric_manifestation", "manifestation_id")):
                record = next((r for r in store.records_of(family)
                               if r.get(id_field) == ref), None)
                if record is not None:
                    break
        if record is not None and isinstance(record.get("marking"), dict):
            out.append(marking_from_record(record["marking"]))
    return out


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
    # the maximum information marking this provider is permitted to receive. A
    # PUBLIC external provider is given a PUBLIC-releasable ceiling; a trusted
    # local provider may be widened. None means UNGOVERNED — the fail-closed
    # default, which receives no evidence at all until a policy is declared.
    allowed_input_marking: Marking | None = None

    def available(self) -> bool:
        return self.infer_fn is not None and self.package is not None

    def _egress_refusal(self, effective_input: Marking) -> str | None:
        """Whether sending an input of this marking to the provider is refused,
        derived like the workbench reference-floor guard: the input may not
        raise the provider's declared ceiling. Returns the refusal reason, or
        None when egress is permitted."""
        ceiling = self.allowed_input_marking
        if ceiling is None:
            # ungoverned provider: the safe default is public-releasable only —
            # compartmented, org-locked (no releasability), or role-elevated
            # evidence is refused without the operator having to configure
            # anything, while ordinary public collection still flows
            if (effective_input.compartments
                    or effective_input.min_role != "OBSERVER"
                    or not effective_input.releasability):
                return ("provider declares no egress policy; only "
                        "public-releasable evidence may be sent to an "
                        "ungoverned provider")
            return None
        try:
            joined = inherited_marking(ceiling, [effective_input])
        except ValueError:
            return ("input marking is incompatible with the provider's egress "
                    "policy (cross-authority)")
        if joined.to_record() != ceiling.to_record():
            return ("input is more restricted than this provider is permitted "
                    "to receive")
        return None

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
        # EGRESS GATE — derive the effective input marking from the actual
        # material supplied and refuse BEFORE the provider call (before any
        # bytes leave the process) when the provider is not permitted to
        # receive it. SPECIAL evidence can never reach a PUBLIC provider merely
        # because code can call it.
        effective_input = inherited_marking(
            ctx.marking, _input_markings(ctx.store, input_refs))
        refusal = self._egress_refusal(effective_input)
        if refusal is not None:
            return {"status": "EGRESS_REFUSED", "detail": refusal,
                    "model_id": self.package.model_id,
                    "input_marking": effective_input.to_record()}
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
            # the inference record carries the sensitivity of what was actually
            # sent, not the caller's ambient context
            validation="INVALID" if errors else "VALID", marking=effective_input,
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
