"""Source-role resolution over normalized observations (Section 7.3-7.6).

V5.2 required one exact marker per role in one dossier section, so a document
whose front matter plainly identifies its issuing institution resolved to
NO_ROLE_ESTABLISHED unless the words "issued by" appeared. This module
combines observations instead: several weaker observations can jointly
establish a role, one decisive observation can establish it alone, and three
inferences stay refused however much evidence accumulates around them.

Failures are reported by component (Section 7.6) so a Surface-2 number can
never again hide whether the capture, the normalization or the resolver was
at fault.

Research shadow only.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Sequence

from ..v5_1.models import Record, SOURCE_ROLES, now_utc, stable_id
from ..v5_2.source_origin import PROHIBITED_INFERENCES
from .provenance import EVIDENCE_STRENGTHS, NormalizedObservationRecord

ROLE_STATES = ("RESOLVED", "UNRESOLVED_EVIDENCE_ABSENT",
               "UNRESOLVED_EVIDENCE_CONFLICTS", "REFUSED_PROHIBITED_INFERENCE")

# Section 7.6 — where a Surface-2 failure actually happened.
FAILURE_COMPONENTS = ("RAW_EVIDENCE_CAPTURE_FAILURE", "NORMALIZATION_FAILURE",
                      "PRODUCTION_ROLE_FAILURE", "DOSSIER_RENDERING_FAILURE",
                      "REVIEWER_DISAGREEMENT")

_STRENGTH_WEIGHT: Mapping[str, float] = {
    "EXPLICIT": 1.0, "STRONGLY_IMPLIED": 0.6, "WEAKLY_IMPLIED": 0.3,
    "CONFLICTING": 0.0, "ABSENT": 0.0,
}

# Observations that decide a role on their own, and observations that only
# contribute.  A role is established when one decisive observation exists, or
# when supporting weight reaches the threshold.
DECISIVE: Mapping[str, tuple[str, ...]] = {
    "PUBLISHED_BY": ("EXPLICIT_PUBLISHER_LINE",),
    "ISSUED_BY": ("EXPLICIT_ISSUER_LINE",),
    "AUTHORED_BY": ("EXPLICIT_AUTHOR_LINE",),
    "EDITED_BY": ("EXPLICIT_EDITOR_LINE",),
    "SUBMITTED_BY": ("EXPLICIT_SUBMISSION_LINE",),
    "TRANSLATED_BY": ("TRANSLATION_NOTICE",),
    "SYNDICATED_BY": ("SYNDICATION_NOTICE",),
    "MIRRORED_BY": ("MIRROR_NOTICE",),
    "ARCHIVED_BY": ("ARCHIVE_PROVIDER",),
    "COMMISSIONED_BY": ("COMMISSIONING_NOTICE",),
    "OPERATED_BY": ("OPERATING_ENTITY_NOTICE",),
    "OWNED_BY": ("OWNERSHIP_RECORD",),
    "HOSTED_BY": ("DOMAIN_HOST",),
}
assert set(DECISIVE) == set(SOURCE_ROLES)

SUPPORTING: Mapping[str, tuple[str, ...]] = {
    # An official document series plus a front-matter institution is how an
    # official act identifies its issuer without ever writing "issued by".
    "ISSUED_BY": ("DOCUMENT_SERIES_OWNER", "INSTITUTIONAL_ATTRIBUTION",
                  "TITLE_PAGE_INSTITUTION", "DOCUMENT_IDENTIFIER"),
    "PUBLISHED_BY": ("DOCUMENT_SERIES_OWNER", "COPYRIGHT_HOLDER",
                     "TITLE_PAGE_INSTITUTION"),
    "OWNED_BY": ("COPYRIGHT_HOLDER",),
    "ARCHIVED_BY": ("REDIRECT_CHAIN",),
    "HOSTED_BY": ("REDIRECT_CHAIN",),
    "AUTHORED_BY": (), "EDITED_BY": (), "SUBMITTED_BY": (),
    "TRANSLATED_BY": (), "SYNDICATED_BY": (), "MIRRORED_BY": (),
    "COMMISSIONED_BY": (), "OPERATED_BY": (),
}

# Weight at which supporting observations jointly establish a role.
SUPPORT_THRESHOLD = 1.0


@dataclass(frozen=True)
class RoleResolution(Record):
    resolution_id: str
    subject_id: str
    role: str
    agent_value: str | None
    state: str
    evidence_strength: str
    decisive_observation_ids: tuple[str, ...]
    supporting_observation_ids: tuple[str, ...]
    support_weight: float
    refused_inference: str | None
    rationale: str
    recorded_time: str

    def __post_init__(self) -> None:
        if self.role not in SOURCE_ROLES:
            raise ValueError(f"unknown source role: {self.role}")
        if self.state not in ROLE_STATES:
            raise ValueError(f"unknown role state: {self.state}")
        if self.evidence_strength not in EVIDENCE_STRENGTHS:
            raise ValueError(f"unknown evidence strength: {self.evidence_strength}")
        if self.state == "RESOLVED":
            if not self.agent_value:
                raise ValueError("a resolved role names the agent that holds it")
            if not (self.decisive_observation_ids or self.supporting_observation_ids):
                raise ValueError("a resolved role requires observations")
        if self.state == "REFUSED_PROHIBITED_INFERENCE" and \
                self.refused_inference not in PROHIBITED_INFERENCES:
            raise ValueError("a refusal must name the prohibited inference")


def _resolution(subject_id: str, role: str, state: str, rationale: str, *,
                agent: str | None = None, strength: str = "ABSENT",
                decisive: Iterable[str] = (), supporting: Iterable[str] = (),
                weight: float = 0.0, refused: str | None = None) -> RoleResolution:
    return RoleResolution(
        stable_id("v5-3-role", subject_id, role, state), subject_id, role, agent,
        state, strength, tuple(decisive), tuple(supporting), round(weight, 3),
        refused, rationale, now_utc())


def resolve_role(observations: Sequence[NormalizedObservationRecord], role: str, *,
                 subject_id: str) -> RoleResolution:
    """Decide one role by combining observations.

    A decisive observation settles it. Otherwise supporting observations
    accumulate weight, and the role is established only if that weight reaches
    the threshold — which one weakly-implied observation never does on its own.
    """
    if role not in DECISIVE:
        raise ValueError(f"unknown source role: {role}")
    grouped: dict[str, list[NormalizedObservationRecord]] = {}
    for item in observations:
        grouped.setdefault(item.observation_type, []).append(item)

    decisive = [item for kind in DECISIVE[role] for item in grouped.get(kind, ())]
    supporting = [item for kind in SUPPORTING.get(role, ())
                  for item in grouped.get(kind, ())]

    # Section 7.3 refusals, checked before any positive assignment.
    if role == "PUBLISHED_BY" and not decisive and grouped.get("DOMAIN_HOST"):
        editorial = any(grouped.get(kind) for kind in
                        ("DOCUMENT_SERIES_OWNER", "COPYRIGHT_HOLDER",
                         "TITLE_PAGE_INSTITUTION"))
        if not editorial:
            return _resolution(
                subject_id, role, "REFUSED_PROHIBITED_INFERENCE",
                PROHIBITED_INFERENCES["HOST_AS_PUBLISHER"] +
                "; the only transport evidence is the domain",
                refused="HOST_AS_PUBLISHER")
    if role == "AUTHORED_BY" and not decisive and grouped.get("EXPLICIT_SUBMISSION_LINE"):
        return _resolution(
            subject_id, role, "REFUSED_PROHIBITED_INFERENCE",
            PROHIBITED_INFERENCES["UPLOADER_AS_AUTHOR"], refused="UPLOADER_AS_AUTHOR")
    if role in ("OWNED_BY", "OPERATED_BY") and not decisive:
        vendorish = [item for item in grouped.get("INSTITUTIONAL_ATTRIBUTION", ())
                     if any(word in item.observed_value.casefold()
                            for word in ("supplier", "vendor", "consortium",
                                         "contractor", "partner"))]
        if vendorish:
            return _resolution(
                subject_id, role, "REFUSED_PROHIBITED_INFERENCE",
                PROHIBITED_INFERENCES["VENDOR_AS_PROGRAMME_OWNER"],
                refused="VENDOR_AS_PROGRAMME_OWNER")

    if decisive:
        top = max(decisive, key=lambda item: _STRENGTH_WEIGHT[item.strength])
        conflicting = {item.observed_value for item in decisive}
        if len(conflicting) > 1:
            return _resolution(
                subject_id, role, "UNRESOLVED_EVIDENCE_CONFLICTS",
                f"{len(conflicting)} different agents are named by decisive "
                f"{role} observations", strength="CONFLICTING",
                decisive=[item.observation_id for item in decisive])
        return _resolution(
            subject_id, role, "RESOLVED",
            f"{role} is established by a decisive "
            f"{top.observation_type} reading {top.observed_value!r}",
            agent=top.observed_value, strength=top.strength,
            decisive=[item.observation_id for item in decisive],
            weight=_STRENGTH_WEIGHT[top.strength])

    if not supporting:
        return _resolution(
            subject_id, role, "UNRESOLVED_EVIDENCE_ABSENT",
            f"no observation of any kind that could decide {role} was captured "
            f"from this source (decisive kinds: {list(DECISIVE[role])})")

    weight = sum(_STRENGTH_WEIGHT[item.strength] for item in supporting)
    if weight < SUPPORT_THRESHOLD:
        return _resolution(
            subject_id, role, "UNRESOLVED_EVIDENCE_ABSENT",
            f"supporting observations for {role} reach only {weight:.2f} of the "
            f"{SUPPORT_THRESHOLD} required; the evidence is present and does not "
            "establish the role", strength="WEAKLY_IMPLIED",
            supporting=[item.observation_id for item in supporting], weight=weight)

    # An institution names an agent; a document series names a venue.  When
    # both are present the institution is what holds the role.
    _AGENT_PREFERENCE = ("INSTITUTIONAL_ATTRIBUTION", "TITLE_PAGE_INSTITUTION",
                         "COPYRIGHT_HOLDER", "DOCUMENT_SERIES_OWNER")
    named: list[NormalizedObservationRecord] = []
    for kind in _AGENT_PREFERENCE:
        named = [item for item in supporting if item.observation_type == kind]
        if named:
            break
    if not named:
        return _resolution(
            subject_id, role, "UNRESOLVED_EVIDENCE_ABSENT",
            f"supporting weight for {role} is sufficient but no observation names "
            "an agent to bind it to", strength="WEAKLY_IMPLIED",
            supporting=[item.observation_id for item in supporting], weight=weight)
    agent = max(named, key=lambda item: _STRENGTH_WEIGHT[item.strength])
    return _resolution(
        subject_id, role, "RESOLVED",
        f"{role} is established by combined supporting observations "
        f"({', '.join(sorted({i.observation_type for i in supporting}))}) "
        f"reaching weight {weight:.2f}", agent=agent.observed_value,
        strength="STRONGLY_IMPLIED",
        supporting=[item.observation_id for item in supporting], weight=weight)


def resolve_all(observations: Sequence[NormalizedObservationRecord], *,
                subject_id: str) -> tuple[RoleResolution, ...]:
    return tuple(resolve_role(observations, role, subject_id=subject_id)
                 for role in sorted(SOURCE_ROLES))


def component_diagnostics(resolutions: Iterable[RoleResolution | Mapping[str, Any]],
                          observation_count: int) -> dict[str, Any]:
    """Section 7.6 — separate capture, normalization and resolver failures."""
    counts = {name: 0 for name in FAILURE_COMPONENTS}
    resolved = refused = conflicting = absent = 0
    for item in resolutions:
        get = (item.get if isinstance(item, Mapping)
               else lambda key, obj=item: getattr(obj, key, None))
        state = str(get("state"))
        if state == "RESOLVED":
            resolved += 1
        elif state == "REFUSED_PROHIBITED_INFERENCE":
            refused += 1
        elif state == "UNRESOLVED_EVIDENCE_CONFLICTS":
            conflicting += 1
            counts["PRODUCTION_ROLE_FAILURE"] += 1
        else:
            absent += 1
            # No observation at all means capture failed; observations present
            # but insufficient means the resolver declined on real evidence.
            if not (get("supporting_observation_ids") or
                    get("decisive_observation_ids")):
                counts["RAW_EVIDENCE_CAPTURE_FAILURE"] += 1
    return {
        "roles_examined": sum(1 for _ in resolutions) if isinstance(resolutions, list)
        else resolved + refused + conflicting + absent,
        "observations_available": observation_count,
        "resolved": resolved, "refused_prohibited": refused,
        "conflicting": conflicting, "unresolved_absent": absent,
        "failure_components": counts,
    }
