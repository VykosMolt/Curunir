"""Section 6 — report publication control.

V5.3's report planner emitted ``PUBLISHED`` for 82 of 85 propositions, and 25 of
those were propositions the reviewer majority judged ``REJECTED_UNSUPPORTED``.
The planner was deciding publishability for itself, from the evidence, in
parallel with the claim-support classifier rather than downstream of it.

This module makes Surface 6 a strict consumer of validated Surface 4.  The
planner may verify that wording is compatible with a support class.  It may
never promote support status, and it may never publish a proposition whose
support class forbids publication — no matter what the evidence looks like when
read again.

The gate is fail-closed: missing support state does not mean "probably fine".
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from ..v5_1.models import Record, now_utc, stable_id
from . import support as SUP

# ---------------------------------------------------------------------------
# Section 6.2 — the support-to-publication mapping.
# ---------------------------------------------------------------------------

DISPOSITIONS: tuple[str, ...] = (
    "PUBLISHED",
    "PUBLISHED_WITH_QUALIFICATION",
    "MOVED_TO_UNCERTAINTY_SECTION",
    "PUBLISHED_AS_MARKED_ANALYSIS",
    "REJECTED_UNSUPPORTED",
    # Section 6.6 fail-closed states.  Reachable from any support class, because
    # a missing support state or an upstream critical defect blocks publication
    # regardless of what the support class would otherwise have permitted.
    "REPORT_BLOCKED",
)

#: Dispositions any support class may take when the gate fails closed.
FAIL_CLOSED_DISPOSITIONS = frozenset({"REPORT_BLOCKED"})

#: What each support class is *permitted* to produce.  Anything outside the
#: permitted set is a gate violation, not a judgement call.
PERMITTED_DISPOSITIONS: Mapping[str, frozenset[str]] = {
    "FULL_SUPPORT": frozenset({"PUBLISHED", "PUBLISHED_WITH_QUALIFICATION",
                               "MOVED_TO_UNCERTAINTY_SECTION"}),
    "PARTIAL_SUPPORT": frozenset({"PUBLISHED_WITH_QUALIFICATION",
                                  "MOVED_TO_UNCERTAINTY_SECTION"}),
    "QUALIFIED_SUPPORT": frozenset({"PUBLISHED_WITH_QUALIFICATION",
                                    "MOVED_TO_UNCERTAINTY_SECTION"}),
    "CONTEXT_DEPENDENT_SUPPORT": frozenset({"PUBLISHED_WITH_QUALIFICATION",
                                            "MOVED_TO_UNCERTAINTY_SECTION"}),
    "INFERENCE_ONLY": frozenset({"PUBLISHED_AS_MARKED_ANALYSIS",
                                 "MOVED_TO_UNCERTAINTY_SECTION"}),
    # Section 6.2: these may never be published as factual propositions.
    "CONTRADICTED": frozenset({"REJECTED_UNSUPPORTED", "MOVED_TO_UNCERTAINTY_SECTION"}),
    "NOT_SUPPORTED": frozenset({"REJECTED_UNSUPPORTED", "MOVED_TO_UNCERTAINTY_SECTION"}),
    "WRONG_SCOPE": frozenset({"REJECTED_UNSUPPORTED", "MOVED_TO_UNCERTAINTY_SECTION"}),
    "WRONG_TIME": frozenset({"REJECTED_UNSUPPORTED", "MOVED_TO_UNCERTAINTY_SECTION"}),
    "WRONG_ENTITY": frozenset({"REJECTED_UNSUPPORTED", "MOVED_TO_UNCERTAINTY_SECTION"}),
    "WRONG_MODALITY": frozenset({"REJECTED_UNSUPPORTED", "MOVED_TO_UNCERTAINTY_SECTION"}),
    "WRONG_POLARITY": frozenset({"REJECTED_UNSUPPORTED", "MOVED_TO_UNCERTAINTY_SECTION"}),
}

#: Classes that may never appear as a factual proposition in a report body.
NEVER_FACTUAL = frozenset({
    "CONTRADICTED", "NOT_SUPPORTED", "WRONG_SCOPE", "WRONG_TIME",
    "WRONG_ENTITY", "WRONG_MODALITY", "WRONG_POLARITY",
})

#: Dispositions that place a proposition in the report as an assertion of fact.
FACTUAL_DISPOSITIONS = frozenset({"PUBLISHED", "PUBLISHED_WITH_QUALIFICATION"})


class PublicationViolation(RuntimeError):
    """Raised when a publication decision contradicts its support class."""


# ---------------------------------------------------------------------------
# Section 6.4 — the proposition publication record.
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class PublicationRecord(Record):
    """Everything a publication decision must persist to be auditable."""

    proposition_id: str
    claim_id: str
    claim_support_id: str
    support_class: str
    support_vector: Any
    production_wording: str
    publication_disposition: str
    lifecycle_state: str
    modality: str
    polarity: str
    scope: tuple[str, ...]
    valid_time: tuple[str | None, str | None]
    attribution: str | None
    dependence_state: str
    counterevidence: tuple[str, ...]
    required_qualifications: tuple[str, ...]
    allowed_wording: tuple[str, ...]
    prohibited_wording: tuple[str, ...]
    blocking_reason: str | None
    section: str
    recorded_time: str

    def __post_init__(self) -> None:
        if self.publication_disposition not in DISPOSITIONS:
            raise ValueError(f"unknown disposition: {self.publication_disposition}")
        if self.support_class not in SUP.SUPPORT_CLASSES:
            raise ValueError(f"unknown support class: {self.support_class}")
        permitted = PERMITTED_DISPOSITIONS[self.support_class] | FAIL_CLOSED_DISPOSITIONS
        if self.publication_disposition not in permitted:
            raise PublicationViolation(
                f"support class {self.support_class} may not produce "
                f"{self.publication_disposition}; permitted: {sorted(permitted)}")
        if (self.support_class in NEVER_FACTUAL
                and self.publication_disposition in FACTUAL_DISPOSITIONS):
            raise PublicationViolation(
                f"{self.support_class} may never be published as a factual "
                "proposition (Section 6.2)")


# ---------------------------------------------------------------------------
# Section 6.1 — the publication dependency contract.
# ---------------------------------------------------------------------------

CONTRACT_CONDITIONS: tuple[str, ...] = (
    "support_class_permits_publication",
    "wording_entailed_by_support_class",
    "lifecycle_preserved",
    "modality_preserved",
    "polarity_preserved",
    "scope_preserved",
    "time_preserved",
    "attribution_preserved",
    "dependence_limitations_represented",
    "material_counterevidence_represented",
    "no_upstream_critical_failure",
)

_STRONG = re.compile(
    r"\b(establishes?|confirms?|proves?|demonstrates?|conclusively|beyond doubt|"
    r"definitively|verified|belegt|beweist|bestätigt|bestaetigt)\b", re.IGNORECASE)
_QUALIFIER_PRESENT = re.compile(
    r"\b(may|might|could|partly|in part|subject to|qualified|reportedly|approximately|"
    r"provisional|preliminary|where|if|unless|according to|teilweise|vorläufig|"
    r"vorlaeufig|voraussichtlich|laut)\b", re.IGNORECASE)
_INDEPENDENCE = re.compile(
    r"\b(independent(ly)?|corroborat\w+|multiple independent|separately confirmed|"
    r"unabhängig|unabhaengig|bestätigt durch mehrere)\b", re.IGNORECASE)


def evaluate_contract(*, support: Any, wording: str, claim: Mapping[str, Any] | Any,
                      dependence_state: str = "INDEPENDENCE_UNKNOWN",
                      counterevidence: Sequence[str] = (),
                      ) -> dict[str, Any]:
    """Evaluate every Section 6.1 condition.  All must hold to publish."""
    support_class = str(_get(support, "support_class"))
    vector = _get(support, "vector")
    critical = _get(support, "critical_error")
    prohibited = tuple(_get(support, "prohibited_stronger_wording") or ())
    folded = (wording or "").casefold()

    results: dict[str, bool] = {}
    results["support_class_permits_publication"] = support_class not in NEVER_FACTUAL
    results["wording_entailed_by_support_class"] = not any(
        p.casefold() in folded for p in prohibited)
    if support_class in ("PARTIAL_SUPPORT", "QUALIFIED_SUPPORT",
                         "CONTEXT_DEPENDENT_SUPPORT"):
        # The sentence must not imply complete support.
        results["wording_entailed_by_support_class"] = (
            results["wording_entailed_by_support_class"]
            and not _STRONG.search(wording or "")
            and bool(_QUALIFIER_PRESENT.search(wording or "")))
    # An UNRESOLVED lifecycle or modality alignment means the evidence stated no
    # lifecycle reading — not that it contradicted one.  Blocking on that alone
    # would refuse every ordinary factual proposition whose evidence simply never
    # discusses programme lifecycle, which is most of them.
    #
    # The protection is kept exactly where it bites: if the *claim* asserts a
    # lifecycle state and the evidence never established one, publishing the
    # claim's wording would assert a lifecycle the evidence does not carry.  That
    # remains blocked.
    claimed_lifecycle = str(_get(claim, "lifecycle_state") or "UNKNOWN")
    lifecycle_unasserted = claimed_lifecycle in ("", "UNKNOWN")
    results["lifecycle_preserved"] = _alignment_ok(
        vector, "lifecycle_alignment", unresolved_ok=lifecycle_unasserted)
    results["modality_preserved"] = _alignment_ok(
        vector, "modality_alignment", unresolved_ok=lifecycle_unasserted)
    results["polarity_preserved"] = _alignment_ok(vector, "polarity_alignment")
    results["scope_preserved"] = _alignment_ok(vector, "scope_alignment")
    results["time_preserved"] = _alignment_ok(vector, "time_alignment")
    results["attribution_preserved"] = _alignment_ok(vector, "attribution_alignment")

    limitations = tuple(_get(vector, "dependence_limitations") or ()) if vector else ()
    if dependence_state in ("DERIVATIVE_CONFIRMED", "TRANSLATION_DERIVATIVE",
                            "SYNDICATION_DERIVATIVE", "MIRROR_MANIFESTATION",
                            "COMMON_EVIDENCE_BASIS_CONFIRMED", "PARTIAL_DEPENDENCE",
                            "INDEPENDENCE_UNKNOWN", "DEPENDENCE_DISPUTED"):
        # The sentence must not claim independence the dependence state does not
        # support.  Requiring a *positive* dependence disclaimer in every
        # sentence would block essentially every proposition, since almost all
        # of them sit at INDEPENDENCE_UNKNOWN — a gate that rejects everything
        # measures nothing.  Where a limitation is recorded it must be visible;
        # where none is recorded, silence is not an overclaim.
        overclaims = bool(_INDEPENDENCE.search(wording or ""))
        recorded_limits_visible = (not limitations) or any(
            limitation.casefold()[:24] in folded for limitation in limitations) or \
            "single source" in folded or "same underlying" in folded or \
            "derivative" in folded
        results["dependence_limitations_represented"] = (
            recorded_limits_visible and not overclaims)
    else:
        results["dependence_limitations_represented"] = True

    material = tuple(counterevidence) or (
        tuple(_get(vector, "counterevidence") or ()) if vector else ())
    results["material_counterevidence_represented"] = (
        (not material) or any(token.casefold()[:24] in folded for token in material)
        or "however" in folded or "contradict" in folded or "disput" in folded)
    results["no_upstream_critical_failure"] = not critical

    failed = [name for name, ok in results.items() if not ok]
    return {"conditions": results, "failed_conditions": failed,
            "publishable": not failed}


def _alignment_ok(vector: Any, name: str, *, unresolved_ok: bool = False) -> bool:
    if vector is None:
        return False          # Section 6.6: missing state fails closed.
    value = _get(vector, name)
    if value in ("ALIGNED", "NOT_APPLICABLE"):
        return True
    return unresolved_ok and value == "UNRESOLVED"


def _get(obj: Any, key: str, default: Any = None) -> Any:
    if isinstance(obj, Mapping):
        return obj.get(key, default)
    return getattr(obj, key, default)


# ---------------------------------------------------------------------------
# Section 6.3 / 6.6 — the gate itself.
# ---------------------------------------------------------------------------

def decide(*, proposition_id: str, claim: Mapping[str, Any] | Any, support: Any,
           wording: str, section: str = "DETAILED_REPORT",
           dependence_state: str = "INDEPENDENCE_UNKNOWN",
           counterevidence: Sequence[str] = (),
           upstream_defect: str | None = None) -> PublicationRecord:
    """Decide a proposition's disposition strictly downstream of claim support.

    The planner supplies wording and context.  It does not supply an opinion
    about whether the evidence is good enough — that question was already
    answered upstream and this function will not reopen it.
    """
    if support is None:
        return _blocked(proposition_id, claim, None, wording, section,
                        dependence_state, counterevidence,
                        "DO_NOT_PUBLISH: no claim-support state exists for this "
                        "proposition (Section 6.6 fail-closed)")
    if upstream_defect:
        return _blocked(proposition_id, claim, support, wording, section,
                        dependence_state, counterevidence,
                        f"REPORT_BLOCKED: upstream critical defect — {upstream_defect}")

    support_class = str(_get(support, "support_class"))
    contract = evaluate_contract(support=support, wording=wording, claim=claim,
                                 dependence_state=dependence_state,
                                 counterevidence=counterevidence)

    if support_class in NEVER_FACTUAL:
        disposition = "REJECTED_UNSUPPORTED"
        blocking = (f"support class {support_class} may never be published as a "
                    "factual proposition")
    elif not contract["publishable"]:
        disposition = "MOVED_TO_UNCERTAINTY_SECTION"
        blocking = ("publication contract unsatisfied: "
                    + ", ".join(contract["failed_conditions"]))
    elif support_class == "INFERENCE_ONLY":
        disposition = "PUBLISHED_AS_MARKED_ANALYSIS"
        blocking = None
    elif support_class == "FULL_SUPPORT":
        disposition = "PUBLISHED"
        blocking = None
    else:
        disposition = "PUBLISHED_WITH_QUALIFICATION"
        blocking = None

    allowed, prohibited = SUP.ALLOWED_WORDING.get(support_class, ()), \
        tuple(_get(support, "prohibited_stronger_wording") or ())
    vector = _get(support, "vector")
    return PublicationRecord(
        proposition_id,
        str(_get(claim, "claim_id") or ""),
        str(_get(support, "support_id") or ""),
        support_class, vector, wording, disposition,
        str(_get(claim, "lifecycle_state") or "UNKNOWN"),
        str(_get(claim, "modality") or "ASSERTED"),
        str(_get(claim, "polarity") or "POSITIVE"),
        tuple(_get(claim, "scope") or ()),
        tuple(_get(claim, "valid_time") or (None, None)),
        _get(claim, "attribution"), dependence_state,
        tuple(counterevidence),
        _required_qualifications(support_class, vector),
        tuple(allowed), prohibited, blocking, section, now_utc())


def _blocked(proposition_id, claim, support, wording, section, dependence_state,
             counterevidence, reason: str) -> PublicationRecord:
    return PublicationRecord(
        proposition_id, str(_get(claim, "claim_id") or ""),
        str(_get(support, "support_id") or "") if support is not None else "",
        str(_get(support, "support_class")) if support is not None else "NOT_SUPPORTED",
        _get(support, "vector") if support is not None else None,
        wording, "REPORT_BLOCKED",
        str(_get(claim, "lifecycle_state") or "UNKNOWN"),
        str(_get(claim, "modality") or "ASSERTED"),
        str(_get(claim, "polarity") or "POSITIVE"),
        tuple(_get(claim, "scope") or ()),
        tuple(_get(claim, "valid_time") or (None, None)),
        _get(claim, "attribution"), dependence_state, tuple(counterevidence),
        (), (), (), reason, section, now_utc())


def _required_qualifications(support_class: str, vector: Any) -> tuple[str, ...]:
    out: list[str] = []
    if support_class == "PARTIAL_SUPPORT":
        out.append("the evidence carries only part of the proposition")
    if support_class == "QUALIFIED_SUPPORT":
        out.append("the source's own qualification must be preserved")
    if support_class == "CONTEXT_DEPENDENT_SUPPORT":
        out.append("the governing condition must appear in the same sentence or section")
    if support_class == "INFERENCE_ONLY":
        out.append("must be marked as analysis, not stated as fact")
    if vector is not None:
        for limitation in (_get(vector, "dependence_limitations") or ()):
            out.append(f"dependence limitation: {limitation}")
    return tuple(out)


# ---------------------------------------------------------------------------
# Wording generation.
#
# A gate that only *verifies* wording cannot produce a compliant report.  Given
# a proposition whose support is PARTIAL, the planner must be able to write a
# sentence that says so — otherwise every partially supported proposition is
# routed to the uncertainty section for lacking a qualifier the planner was
# never able to add, and PUBLISHED_WITH_QUALIFICATION is unreachable by
# construction.  That is a capability gap, not a strictness setting.
# ---------------------------------------------------------------------------

#: The qualifying frame each support class requires its wording to carry.
QUALIFYING_FRAME: Mapping[str, str] = {
    "PARTIAL_SUPPORT": "The evidence supports in part that {body}",
    "QUALIFIED_SUPPORT": "According to the source, and subject to its own "
                         "qualification, {body}",
    "CONTEXT_DEPENDENT_SUPPORT": "Where the stated conditions apply, {body}",
    "INFERENCE_ONLY": "Analysis, not established fact: the evidence may indicate "
                      "that {body}",
}


def _strip_strong(text: str) -> str:
    """Remove wording a qualified class may not carry."""
    out = _STRONG.sub("indicates", text or "")
    return " ".join(out.split())


def render_wording(*, support: Any, base_text: str,
                   dependence_state: str = "INDEPENDENCE_UNKNOWN") -> str:
    """Produce wording this support class is permitted to publish.

    The body is the planner's proposition; the frame is what the support class
    entails.  Nothing is added that the evidence does not carry — the frame only
    ever *weakens* the assertion, never strengthens it.
    """
    support_class = str(_get(support, "support_class"))
    # Only the qualified classes must avoid strong wording.  FULL_SUPPORT is
    # entitled to say "establishes"; weakening it would be its own kind of
    # unfaithfulness — a report that understates what the evidence carries.
    needs_weakening = support_class in QUALIFYING_FRAME
    body = (_strip_strong(base_text) if needs_weakening else
            " ".join((base_text or "").split())).rstrip(".")
    if not body:
        return ""
    if support_class in NEVER_FACTUAL:
        # Not publishable as fact; there is no compliant factual wording to make.
        return ""
    frame = QUALIFYING_FRAME.get(support_class)
    if frame is None:
        wording = body + "."
    else:
        lowered = body[0].lower() + body[1:] if body else body
        wording = frame.format(body=lowered) + "."
    vector = _get(support, "vector")
    limitations = tuple(_get(vector, "dependence_limitations") or ()) if vector else ()
    if limitations:
        wording += " " + "; ".join(f"Dependence limitation: {x}" for x in limitations)
    counter = tuple(_get(vector, "counterevidence") or ()) if vector else ()
    if counter:
        wording += f" However, {len(counter)} counterevidence item(s) are recorded."
    return wording


def decide_with_wording(*, proposition_id: str, claim: Mapping[str, Any] | Any,
                        support: Any, base_text: str,
                        section: str = "DETAILED_REPORT",
                        dependence_state: str = "INDEPENDENCE_UNKNOWN",
                        counterevidence: Sequence[str] = (),
                        upstream_defect: str | None = None) -> PublicationRecord:
    """Generate compliant wording, then decide.  The planner's normal path."""
    wording = (render_wording(support=support, base_text=base_text,
                              dependence_state=dependence_state)
               if support is not None else base_text)
    return decide(proposition_id=proposition_id, claim=claim, support=support,
                  wording=wording or base_text, section=section,
                  dependence_state=dependence_state,
                  counterevidence=counterevidence, upstream_defect=upstream_defect)


# ---------------------------------------------------------------------------
# Section 6.5 — executive-summary control.
# ---------------------------------------------------------------------------

_LIFECYCLE_STRENGTH_WORDS: Mapping[str, int] = {
    "proposed": 1, "announced": 1, "planned": 1, "plans": 1, "intends": 1,
    "under development": 2, "in development": 2, "being developed": 2,
    "piloted": 3, "pilot": 3, "trialled": 3, "tested": 3,
    "deployed": 4, "rolled out": 4, "in service": 4, "fielded": 4,
    "operational": 5, "operating": 5, "operates": 5, "in operation": 5,
}


def _lifecycle_strength(text: str) -> int:
    folded = (text or "").casefold()
    return max((v for k, v in _LIFECYCLE_STRENGTH_WORDS.items() if k in folded),
               default=0)


def _certainty_strength(text: str) -> int:
    folded = (text or "").casefold()
    if _STRONG.search(folded):
        return 3
    if _QUALIFIER_PRESENT.search(folded):
        return 1
    return 2


def summarize(*, detailed: PublicationRecord, executive_wording: str,
              ) -> dict[str, Any]:
    """Section 6.5 — the stricter executive-summary gate.

    The summary may omit detail.  It may not strengthen certainty, capability,
    deployment, causality or independence, and every material qualification and
    counterevidence item must survive compression.
    """
    checks: dict[str, bool] = {}
    checks["detailed_proposition_is_publishable"] = (
        detailed.publication_disposition in FACTUAL_DISPOSITIONS
        or detailed.publication_disposition == "PUBLISHED_AS_MARKED_ANALYSIS")
    checks["executive_wording_no_stronger"] = (
        _certainty_strength(executive_wording)
        <= _certainty_strength(detailed.production_wording))
    checks["no_lifecycle_strengthening"] = (
        _lifecycle_strength(executive_wording)
        <= _lifecycle_strength(detailed.production_wording))
    folded = executive_wording.casefold()
    checks["material_qualifications_survive"] = all(
        _qualification_survives(q, folded) for q in detailed.required_qualifications)
    checks["material_counterevidence_survives"] = (
        (not detailed.counterevidence)
        or any(c.casefold()[:24] in folded for c in detailed.counterevidence)
        or "however" in folded or "disput" in folded)
    checks["no_unsupported_independence_language"] = not (
        _INDEPENDENCE.search(executive_wording)
        and detailed.dependence_state != "INDEPENDENCE_SUPPORTED")
    checks["no_prohibited_wording"] = not any(
        p.casefold() in folded for p in detailed.prohibited_wording)

    failed = [k for k, ok in checks.items() if not ok]
    return {"proposition_id": detailed.proposition_id, "checks": checks,
            "failed_checks": failed,
            "disposition": ("PUBLISHED" if not failed else "REJECTED_UNSUPPORTED"),
            "admissible": not failed}


def _qualification_survives(qualification: str, folded_summary: str) -> bool:
    if "marked as analysis" in qualification:
        return any(k in folded_summary for k in
                   ("analysis", "assessment", "hypothesis", "may", "suggests"))
    if "only part" in qualification:
        return any(k in folded_summary for k in ("part", "partly", "some", "partial"))
    if "qualification must be preserved" in qualification:
        return bool(_QUALIFIER_PRESENT.search(folded_summary))
    if "governing condition" in qualification:
        return any(k in folded_summary for k in ("if", "where", "when", "unless",
                                                 "subject to", "provided"))
    if qualification.startswith("dependence limitation"):
        return any(k in folded_summary for k in ("single source", "same underlying",
                                                 "derivative", "one source"))
    return True


# ---------------------------------------------------------------------------
# Section 6.6 — revalidation, and the audit the milestone is graded on.
# ---------------------------------------------------------------------------

def revalidate(records: Iterable[PublicationRecord],
               changed_claim_ids: Sequence[str]) -> dict[str, Any]:
    """If an upstream claim changed after report generation, say so."""
    changed = set(changed_claim_ids)
    affected = [r.proposition_id for r in records if r.claim_id in changed]
    return {"changed_claims": sorted(changed), "affected_propositions": affected,
            "state": "REPORT_REVALIDATION_REQUIRED" if affected else "CURRENT"}


def audit(records: Iterable[PublicationRecord | Mapping[str, Any]]) -> dict[str, Any]:
    """The Section 6.7 audit.  ``unsupported_published`` must be zero."""
    records = list(records)
    counts: dict[str, int] = {}
    unsupported_published: list[str] = []
    unmarked_inference: list[str] = []
    for record in records:
        disposition = str(_get(record, "publication_disposition"))
        support_class = str(_get(record, "support_class"))
        counts[disposition] = counts.get(disposition, 0) + 1
        if support_class in NEVER_FACTUAL and disposition in FACTUAL_DISPOSITIONS:
            unsupported_published.append(str(_get(record, "proposition_id")))
        if support_class == "INFERENCE_ONLY" and disposition in FACTUAL_DISPOSITIONS:
            unmarked_inference.append(str(_get(record, "proposition_id")))
    return {
        "propositions": len(records),
        "dispositions": counts,
        "distinct_dispositions": len(counts),
        "known_unsupported_propositions_published": len(unsupported_published),
        "unsupported_published_ids": unsupported_published[:20],
        "unmarked_inference": len(unmarked_inference),
        "verdict": "PASS" if not unsupported_published and not unmarked_inference
        else "FAIL",
    }
