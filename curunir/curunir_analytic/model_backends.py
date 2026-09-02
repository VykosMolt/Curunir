"""The providers behind the analytical-proposal seam: `anthropic`, `openai`,
and `deterministic`, an offline backend needing no network or credential.

This module supplies a provider and nothing else — `providers.AnalyticalAssist`
owns the egress refusal, the retained inference record and the human gate. It
makes no authorization decision, writes no record, and cannot promote anything.

Credentials are not handled here: each backend constructs a zero-argument
client and lets the vendor SDK run its own resolution chain, so whatever that
chain supports, this supports. `base_url` is passed through for gateways and
self-hosted endpoints. Both SDKs are optional and imported lazily, so a checkout
with neither behaves as one with no provider configured.
"""
from __future__ import annotations

import json
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Mapping

from curunir_operational.contracts import ModelPackage

from .providers import analytical_assist_package
from .candidate_schema import (available_ids, candidate_schema, id_fields,
                               refusal_reason, unresolvable_ids)

PROVIDERS = ("anthropic", "openai", "deterministic")

DEFAULT_MODELS = {"anthropic": "claude-opus-5", "openai": "gpt-5"}

# The discipline the proposal must follow. Kind-independent so it stays a
# cacheable prefix; the per-kind constraint travels in the response schema.
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
    """What to call and how. Never which credential to use."""
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
        """The provider's raw response text, which must be JSON."""

    def package(self) -> ModelPackage:
        """The identity of what actually ran, as recorded by MODEL_REGISTERED."""
        return analytical_assist_package(
            self.spec.provider, f"{self.spec.provider}:{self.spec.model}",
            self.spec.model)

    def infer(self, task: str, inputs: Mapping[str, Any],
              target_kind: str) -> Mapping[str, Any]:
        """Propose one candidate of `target_kind`, or raise.

        Raising is a supported outcome: the caller retains the failure as an
        INVALID inference rather than letting a bad response become content.
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
        # a fabricated identifier becomes a provider error retained as an
        # INVALID inference, not a proposal a human has to catch by eye
        dangling = unresolvable_ids(target_kind, content, available_ids(inputs))
        if dangling:
            raise ValueError("provider cited identifiers it was not shown: "
                             + ", ".join(dangling))
        return content


# The member of each closed vocabulary that commits to least, so an offline
# candidate stays materializable: ABSENCE would additionally demand a deadline
# and named coverage sources, a non-VERBATIM variant relation a mechanism, and
# LIKELY_INFLUENCES a mechanism too.
_SAFEST_ENUM_MEMBER = {
    ("narrative_variant", "relation"): "UNRESOLVED_RELATION",
    ("stakeholder_assessment", "context_kind"): "MISSION",
    ("influence_assertion", "kind"): "INFLUENCE_UNRESOLVED",
    ("forecast_indicator", "kind"): "PRESENCE",
}

_NO_IDENTIFIER = "(no identifier was supplied)"


class DeterministicBackend(ModelBackend):
    """An offline backend: no network, no credential, no vendor SDK.

    It does not reason. It fills each binding field from the request in a fixed,
    inspectable way, so the seam, the schema, the id check, the proposal record
    and the human resolution path can be exercised end to end. Its proposals say
    so in their text.
    """

    def __init__(self, spec: ProviderSpec | None = None) -> None:
        super().__init__(spec or ProviderSpec(provider="deterministic"))

    def _invoke(self, system: str, payload: str, schema: Mapping[str, Any]) -> str:
        request = json.loads(payload)
        target_kind = request["target_kind"]
        evidence = request["evidence"] if isinstance(request["evidence"], Mapping) \
            else {}
        shown = sorted(available_ids(request["evidence"]))
        identifier_fields = set(id_fields(target_kind))
        out: dict[str, Any] = {}
        for name, shape in schema["properties"].items():
            # filled by field name, not by JSON type: prose in a scalar id
            # field would be a citation the provider was never shown
            if name in identifier_fields:
                out[name] = _fill_identifier(name, shape, evidence, shown)
            elif shape.get("type") == "number":
                out[name] = 0.5
            elif shape.get("enum"):
                member = _SAFEST_ENUM_MEMBER.get((target_kind, name))
                out[name] = member if member in shape["enum"] else shape["enum"][0]
            elif name in ("horizon_time",):
                out[name] = "2026-12-31T23:59:59+00:00"
            else:
                out[name] = f"deterministic offline proposal for {request['task']}"
        return json.dumps(out)


def _fill_identifier(name: str, shape: Mapping[str, Any],
                     evidence: Mapping[str, Any], shown: list[str]) -> Any:
    """The identifier this field asks for, taken from the payload where it
    supplies one."""
    supplied = evidence.get(name)
    if shape.get("type") == "array":
        if isinstance(supplied, (list, tuple)):
            picked = [v for v in supplied if isinstance(v, str) and v]
            if picked:
                return picked
        if isinstance(supplied, str) and supplied:
            return [supplied]
        return shown[:1] or [_NO_IDENTIFIER]
    if isinstance(supplied, str) and supplied:
        return supplied
    if isinstance(supplied, (list, tuple)):
        for value in supplied:
            if isinstance(value, str) and value:
                return value
    return shown[0] if shown else _NO_IDENTIFIER


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
                # cached prefix: the volatile evidence sits after it in
                # `messages`, so the cache survives across calls
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
    """GPT through the official `openai` SDK, using structured outputs."""

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
    """Construct a backend, or raise BackendUnavailable with the reason."""
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
    """What this environment could run, without calling anything."""
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

    Read from ``CURUNIR_MODEL_PROVIDER`` (unset means no provider, which is a
    safe state), ``CURUNIR_MODEL_ID``, ``CURUNIR_MODEL_EFFORT``,
    ``CURUNIR_MODEL_BASE_URL`` and ``CURUNIR_MODEL_MAX_TOKENS``, so switching
    provider is a deployment decision.

    The egress ceiling stays out of the environment: widening what may leave the
    deployment is a code and review change.
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
