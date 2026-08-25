"""Model backends for the governed analytical-proposal seam.

`providers.AnalyticalAssist` already owns everything that makes a model call
safe: it derives the effective marking from authoritative records, refuses
egress before invoking, invokes exactly once, retains the complete outcome as an
InferenceRecord (a failure included, as validation="INVALID"), converts a
successful response only into a PROPOSED candidate awaiting a human, and never
calls a provider again on replay. This module supplies the missing half — an
actual provider — and nothing else. It makes no authorization decision, writes
no record, and cannot promote anything.

Providers
---------
`anthropic` and `openai`, each through its official SDK, plus `deterministic`,
an offline backend that needs no network and no credential and is what the
tests use.

Credentials are deliberately *not* handled here. Each backend constructs a
zero-argument client and lets the vendor SDK run its own resolution chain, so
whatever that SDK supports, this supports — an API key, an auth token, an
OAuth profile written by `ant auth login` (the subscription-backed path), or
workload identity federation — and it keeps working when a vendor extends the
chain. Reading a key here would replace a maintained resolver with a worse one.
`base_url` is passed through for gateways, proxies and self-hosted endpoints.

The SDKs are optional. They are imported lazily inside `build_backend`, so a
checkout with neither installed behaves exactly as one with no provider
configured: `AnalyticalAssist.available()` is False and the deterministic path
and every analyst action remain fully available.
"""
from __future__ import annotations

import json
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Mapping

from curunir_operational.contracts import ModelPackage

from .providers import analytical_assist_package
from .candidate_schema import (available_ids, candidate_schema, refusal_reason,
                               unresolvable_ids)

PROVIDERS = ("anthropic", "openai", "deterministic")

DEFAULT_MODELS = {"anthropic": "claude-opus-5", "openai": "gpt-5"}

# The discipline the proposal must follow. Kind-independent on purpose: it is
# the cacheable prefix, and a prefix that changed per analytical kind would
# throw away the cache on every call. The per-kind constraint travels in the
# response schema instead.
SYSTEM_CONTRACT = """\
You propose a single analytical candidate for a human analyst to accept or \
reject. You are not deciding anything; a person reviews every field you emit.

Rules, in priority order:

1. Cite only identifiers that appear in the payload you were given. Never \
invent, complete, guess or reformat an identifier. If the payload does not \
contain an identifier you need, you cannot make this proposal.
2. Ground every claim you make in the payload. Do not import outside knowledge \
about the entities involved.
3. Prefer an honest narrow proposal to a confident broad one. If the evidence \
supports only a weak statement, propose the weak statement.
4. If the evidence does not support a proposal of the requested kind at all, \
emit the schema's required fields with the most defensible minimal content \
you can support, rather than inventing support that is not there.
5. Write for an analyst who will read the cited evidence themselves. No \
hedging boilerplate, no restatement of these instructions.
"""


class BackendUnavailable(RuntimeError):
    """The requested provider cannot be constructed in this environment."""


@dataclass(frozen=True)
class ProviderSpec:
    """What to call and how. Never what credential to use."""
    provider: str
    model: str = ""
    effort: str = "high"
    max_tokens: int = 16_000
    base_url: str | None = None
    timeout_s: float = 120.0
    extra: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.provider not in PROVIDERS:
            raise ValueError(f"unknown provider {self.provider!r}; "
                             f"expected one of {', '.join(PROVIDERS)}")
        if not self.model:
            object.__setattr__(self, "model", DEFAULT_MODELS.get(self.provider, "offline"))
        if self.max_tokens < 1:
            raise ValueError("max_tokens must be positive")


class ModelBackend(ABC):
    """A provider that can answer one analytical-candidate request."""

    def __init__(self, spec: ProviderSpec) -> None:
        self.spec = spec

    @abstractmethod
    def _invoke(self, system: str, payload: str, schema: Mapping[str, Any]) -> str:
        """Return the provider's raw response text. Must be JSON."""

    def package(self) -> ModelPackage:
        """The identity recorded by MODEL_REGISTERED — what actually ran.

        Built by the existing `analytical_assist_package`, so the declared
        limitations, approved uses and prohibited uses stay defined in one
        place for every provider.
        """
        return analytical_assist_package(
            self.spec.provider, f"{self.spec.provider}:{self.spec.model}",
            self.spec.model)

    def infer(self, task: str, inputs: Mapping[str, Any],
              target_kind: str) -> Mapping[str, Any]:
        """Propose one candidate of `target_kind`, or raise.

        Raising is a supported outcome: `AnalyticalAssist.propose` retains the
        failure as an INVALID inference rather than letting a bad response
        become plausible analytical content.
        """
        reason = refusal_reason(target_kind)
        if reason is not None:
            raise ValueError(f"{target_kind} is not model-proposable: {reason}")
        schema = candidate_schema(target_kind)
        payload = json.dumps({"task": task, "target_kind": target_kind,
                              "evidence": inputs}, sort_keys=True, ensure_ascii=False)
        raw = self._invoke(SYSTEM_CONTRACT, payload, schema)
        try:
            content = json.loads(raw)
        except (TypeError, ValueError) as error:
            raise ValueError(f"provider response was not JSON: {error}") from None
        if not isinstance(content, dict):
            raise ValueError("provider response must be a JSON object")
        # A candidate may only cite what it was shown. Enforced here so a
        # fabricated identifier is a provider error retained as an INVALID
        # inference, not a proposal a human has to catch by eye.
        dangling = unresolvable_ids(target_kind, content, available_ids(inputs))
        if dangling:
            raise ValueError("provider cited identifiers it was not shown: "
                             + ", ".join(dangling))
        return content


class DeterministicBackend(ModelBackend):
    """An offline backend: no network, no credential, no vendor SDK.

    It does not pretend to reason. It fills each binding field from the request
    in a fixed, inspectable way, so the seam, the schema, the id check, the
    proposal record and the human resolution path can all be exercised end to
    end — in tests and in a demo — without spending anything or leaving the
    machine. Its proposals are labelled as such.
    """

    def __init__(self, spec: ProviderSpec | None = None) -> None:
        super().__init__(spec or ProviderSpec(provider="deterministic"))

    def _invoke(self, system: str, payload: str, schema: Mapping[str, Any]) -> str:
        request = json.loads(payload)
        shown = sorted(available_ids(request["evidence"]))
        out: dict[str, Any] = {}
        for name, shape in schema["properties"].items():
            if shape.get("type") == "array":
                out[name] = shown[:1] or ["(no identifier was supplied)"]
            elif shape.get("type") == "number":
                out[name] = 0.5
            elif shape.get("enum"):
                out[name] = shape["enum"][-1]      # the most conservative member
            elif name in ("horizon_time",):
                out[name] = "2026-12-31T23:59:59+00:00"
            else:
                out[name] = f"deterministic offline proposal for {request['task']}"
        return json.dumps(out)


class AnthropicBackend(ModelBackend):
    """Claude through the official `anthropic` SDK."""

    def __init__(self, spec: ProviderSpec, client: Any) -> None:
        super().__init__(spec)
        self._client = client

    def _invoke(self, system: str, payload: str, schema: Mapping[str, Any]) -> str:
        import anthropic
        try:
            response = self._client.messages.create(
                model=self.spec.model,
                max_tokens=self.spec.max_tokens,
                # Kind-independent prefix, cached: the volatile evidence sits
                # after it in `messages`, so the cache survives across calls.
                system=[{"type": "text", "text": system,
                         "cache_control": {"type": "ephemeral"}}],
                thinking={"type": "adaptive"},
                output_config={"effort": self.spec.effort,
                               "format": {"type": "json_schema", "schema": dict(schema)}},
                messages=[{"role": "user", "content": payload}],
                **dict(self.spec.extra))
        except anthropic.NotFoundError as error:
            raise ValueError(f"model {self.spec.model!r} not available: {error}") from None
        except anthropic.RateLimitError as error:
            raise ValueError(f"provider rate limited: {error}") from None
        except anthropic.APIStatusError as error:
            raise ValueError(f"provider returned {error.status_code}: {error}") from None
        except anthropic.APIConnectionError as error:
            raise ValueError(f"provider unreachable: {error}") from None
        if getattr(response, "stop_reason", None) == "refusal":
            detail = getattr(response, "stop_details", None)
            raise ValueError("provider declined the request"
                             + (f" ({detail.category})" if detail else ""))
        return next(block.text for block in response.content if block.type == "text")


class OpenAIBackend(ModelBackend):
    """GPT through the official `openai` SDK, using Chat Completions
    structured outputs (`response_format` with a strict JSON schema)."""

    def __init__(self, spec: ProviderSpec, client: Any) -> None:
        super().__init__(spec)
        self._client = client

    def _invoke(self, system: str, payload: str, schema: Mapping[str, Any]) -> str:
        import openai
        try:
            response = self._client.chat.completions.create(
                model=self.spec.model,
                max_completion_tokens=self.spec.max_tokens,
                messages=[{"role": "system", "content": system},
                          {"role": "user", "content": payload}],
                response_format={"type": "json_schema",
                                 "json_schema": {"name": "analytical_candidate",
                                                 "schema": dict(schema),
                                                 "strict": True}},
                **dict(self.spec.extra))
        except openai.NotFoundError as error:
            raise ValueError(f"model {self.spec.model!r} not available: {error}") from None
        except openai.RateLimitError as error:
            raise ValueError(f"provider rate limited: {error}") from None
        except openai.APIStatusError as error:
            raise ValueError(f"provider returned {error.status_code}: {error}") from None
        except openai.APIConnectionError as error:
            raise ValueError(f"provider unreachable: {error}") from None
        choice = response.choices[0]
        if getattr(choice.message, "refusal", None):
            raise ValueError(f"provider declined the request: {choice.message.refusal}")
        return choice.message.content or ""


def build_backend(spec: ProviderSpec) -> ModelBackend:
    """Construct a backend, or raise BackendUnavailable with the exact reason.

    The vendor client is constructed with no credential argument on purpose;
    see the module docstring.
    """
    if spec.provider == "deterministic":
        return DeterministicBackend(spec)
    options: dict[str, Any] = {"timeout": spec.timeout_s}
    if spec.base_url:
        options["base_url"] = spec.base_url
    if spec.provider == "anthropic":
        try:
            import anthropic
        except ImportError:
            raise BackendUnavailable(
                "the `anthropic` package is not installed; it is an optional "
                "dependency of this seam") from None
        try:
            return AnthropicBackend(spec, anthropic.Anthropic(**options))
        except Exception as error:
            raise BackendUnavailable(f"anthropic client unavailable: {error}") from None
    try:
        import openai
    except ImportError:
        raise BackendUnavailable(
            "the `openai` package is not installed; it is an optional "
            "dependency of this seam") from None
    try:
        return OpenAIBackend(spec, openai.OpenAI(**options))
    except Exception as error:
        raise BackendUnavailable(f"openai client unavailable: {error}") from None


def availability() -> dict[str, Any]:
    """What this environment could actually run, without calling anything."""
    report: dict[str, Any] = {"providers": {}}
    for name in PROVIDERS:
        if name == "deterministic":
            report["providers"][name] = {"status": "AVAILABLE",
                                         "detail": "offline; no credential required"}
            continue
        try:
            build_backend(ProviderSpec(provider=name))
        except BackendUnavailable as error:
            report["providers"][name] = {"status": "UNAVAILABLE", "detail": str(error)}
        else:
            report["providers"][name] = {
                "status": "CLIENT_CONSTRUCTED",
                "detail": ("the SDK resolved a credential; whether it is valid is "
                           "only known at call time")}
    return report


def assist_from_environment(env: Mapping[str, str] | None = None):
    """Build a configured `AnalyticalAssist`, or None if none is configured.

    Read from the environment so switching provider is a deployment decision,
    not a code change:

    * ``CURUNIR_MODEL_PROVIDER`` — anthropic | openai | deterministic. Unset
      means no provider, which is a supported and safe state.
    * ``CURUNIR_MODEL_ID`` — defaults to the vendor default above.
    * ``CURUNIR_MODEL_EFFORT`` — low | medium | high | xhigh | max.
    * ``CURUNIR_MODEL_BASE_URL`` — for a gateway, proxy or self-hosted endpoint.
    * ``CURUNIR_MODEL_MAX_TOKENS``.

    The egress ceiling is deliberately **not** environment-configurable.
    `allowed_input_marking` stays None, which is the existing
    public-releasable-only mode: an environment variable that widened what may
    leave the deployment would be the wrong shape of control for that decision.
    Widening it is a code and review change.
    """
    import os
    env = os.environ if env is None else env
    provider = (env.get("CURUNIR_MODEL_PROVIDER") or "").strip().casefold()
    if not provider:
        return None
    from .providers import AnalyticalAssist
    spec = ProviderSpec(
        provider=provider,
        model=(env.get("CURUNIR_MODEL_ID") or "").strip(),
        effort=(env.get("CURUNIR_MODEL_EFFORT") or "high").strip(),
        max_tokens=int(env.get("CURUNIR_MODEL_MAX_TOKENS") or 16_000),
        base_url=(env.get("CURUNIR_MODEL_BASE_URL") or "").strip() or None)
    backend = build_backend(spec)
    return AnalyticalAssist(package=backend.package(), backend=backend)
