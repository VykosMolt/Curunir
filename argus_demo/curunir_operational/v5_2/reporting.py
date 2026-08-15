"""Proposition-first report planning (contract Section 15).

V5.1 scored 110 of 124 on report faithfulness — the strongest surface, and
still short of closure.  All twelve errors were the same direction: Curunír
recorded RENDERED_FAITHFULLY where blind reviewers said the sentence should
have been refused.  The twelve split into six mechanisms:

* site and archive furniture promoted to factual propositions ("We use
  cookies to improve your browsing experience.", a press-office contact
  block, a Wayback capture banner);
* PDF line fragments promoted to propositions ("proyectos con valor
  anadido de la UE.");
* a legal recital — interpretive guidance about how a rule should be read —
  rendered with modality ASSERTED as a statement of fact;
* a temporal scope asserted that the supporting excerpt does not carry;
* a lifecycle term stronger than its evidence ("soll wieder in Betrieb
  gehen" rendered "(status: operational)");
* no materiality gate at all, so any admitted claim became reportable.

The repair is structural rather than a longer denylist: a proposition is
built from a validated claim, must pass a publishability contract before it
can be rendered, and every omission carries a recorded reason.  Generation
difficulty is not a reason, so the planner cannot quietly drop what it finds
hard.

Research shadow only.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Sequence

from ..v5_1.models import (
    INDEPENDENCE_LANGUAGE_PATTERNS, MATERIAL_QUALIFICATION_MARKERS,
    MODALITIES, NON_CORROBORATING_STATES, POLARITIES, Record, now_utc,
    stable_id,
)
from ..v5_1.report_planning import (
    AtomicProposition, FaithfulnessViolation, independence_language_guard,
)
from . import chrome, lifecycle
from .semantics import is_legal_citation, split_roles

# Section 15.2 — the complete disposition vocabulary.  Every proposition the
# planner sees gets exactly one; there is no fall-through.
PROPOSITION_DISPOSITIONS = (
    "PUBLISHED",
    "PUBLISHED_WITH_QUALIFICATION",
    "MOVED_TO_UNCERTAINTY_SECTION",
    "REJECTED_UNSUPPORTED",
    "OMITTED_AS_IMMATERIAL",
)

# Reasons an omission may carry.  Generation difficulty is deliberately not
# among them: a planner that cannot render a supported proposition has a
# capability failure, not an editorial judgement.
OMISSION_REASONS = (
    "NOT_MATERIAL_TO_THE_QUESTION", "DUPLICATE_OF_PUBLISHED_PROPOSITION",
    "SOURCE_ACCESS_RESTRICTED", "SUPERSEDED_BY_LATER_PROPOSITION",
)

REFUSAL_REASONS = (
    "SITE_OR_ARCHIVE_FURNITURE", "NOT_A_COMPLETE_PROPOSITION",
    "NORMATIVE_TEXT_RENDERED_AS_FACT", "BIBLIOGRAPHIC_CITATION",
    "TEMPORAL_SCOPE_UNSUPPORTED", "LIFECYCLE_STATE_STRENGTHENED",
    "NO_SUPPORTING_CLAIM", "UNSUPPORTED_INDEPENDENCE_LANGUAGE",
    "UNMARKED_INFERENCE", "MATERIAL_QUALIFICATION_OMITTED",
    "MATERIAL_COUNTEREVIDENCE_OMITTED", "CORRECTION_OR_RETRACTION_OMITTED",
)

# Legal recitals, preambles and interpretive guidance state how a rule is to
# be read.  They are normative, not descriptive, and V5.1 rendered one as a
# statement of fact about AI systems.
_NORMATIVE_MARKERS = (
    r"\bshould\s+be\s+(?:interpreted|understood|read|regarded|considered)\b",
    r"\bshould\s+(?:not\s+)?(?:apply|cover|include|be\s+possible)\b",
    r"\bis\s+(?:therefore\s+)?(?:necessary|appropriate|desirable)\s+to\b",
    r"\bwhereas\b", r"\bhaving\s+regard\s+to\b",
    r"\bin\s+order\s+to\s+ensure\s+that\b",
    r"\bsollte\s+(?:so\s+)?(?:ausgelegt|verstanden|betrachtet)\s+werden\b",
    r"\bes\s+ist\s+(?:daher\s+)?(?:erforderlich|angemessen)\b",
    r"\bin\s+erw[äa]gung\s+nachstehender\s+gr[üu]nde\b",
    r"\bdevrait\s+[êe]tre\s+(?:interpr[ée]t[ée]|compris|consid[ée]r[ée])\b",
    r"\bconsid[ée]rant\s+(?:ce\s+)?qu",
    r"\bdeber[íi]a\s+(?:interpretarse|entenderse|considerarse)\b",
    r"\bconsiderando\s+(?:lo\s+siguiente|que)\b",
)
_NORMATIVE = tuple(re.compile(pattern, re.IGNORECASE) for pattern in _NORMATIVE_MARKERS)

# Inference markers a faithful report must show explicitly.
_INFERENCE_MARKERS = (
    r"\bsuggests?\s+that\b", r"\bimplies\s+that\b", r"\bindicates?\s+that\b",
    r"\bappears?\s+to\b", r"\blikely\b", r"\bprobably\b", r"\bwe\s+infer\b",
    r"\bpoints?\s+to\b", r"\bdeutet\s+darauf\s+hin\b", r"\bd[üu]rfte\b",
    r"\bsemble\s+", r"\bprobablement\b", r"\bsugiere\s+que\b",
    r"\bprobablemente\b",
)
_INFERENCE = tuple(re.compile(pattern, re.IGNORECASE) for pattern in _INFERENCE_MARKERS)
_INFERENCE_MARKED = re.compile(
    r"\b(?:inference|assessment|our\s+reading|analytic(?:al)?\s+judg[e]?ment|"
    r"schlussfolgerung|einsch[äa]tzung|inf[ée]rence|appr[ée]ciation|"
    r"inferencia|valoraci[óo]n)\b", re.IGNORECASE)

_MINIMUM_PROPOSITION_TOKENS = 4

MATERIALITY_CLASSES = (
    "ANSWERS_THE_RESEARCH_QUESTION", "ESTABLISHES_A_LIFECYCLE_STATE",
    "ESTABLISHES_AN_INSTITUTIONAL_ROLE", "ESTABLISHES_A_DEPENDENCE_RELATION",
    "ESTABLISHES_A_TEMPORAL_RELATION", "QUALIFIES_A_PUBLISHED_PROPOSITION",
    "RECORDS_COUNTEREVIDENCE", "BACKGROUND_ONLY",
)


@dataclass(frozen=True)
class ReportableProposition(Record):
    """One candidate proposition with everything Section 15.1 requires."""

    proposition_id: str
    text: str
    supporting_claim_ids: tuple[str, ...]
    support_state: str
    lifecycle_state: str
    evidence_act: str
    attribution: str | None
    modality: str
    polarity: str
    temporal_scope: tuple[str | None, str | None]
    scope: tuple[str, ...]
    dependence_states: tuple[str, ...]
    counterevidence_ids: tuple[str, ...]
    qualifications: tuple[str, ...]
    correction_state: str
    materiality: str
    language: str
    recorded_time: str

    def __post_init__(self) -> None:
        lifecycle.normalize_state(self.lifecycle_state)
        if self.modality not in MODALITIES:
            raise ValueError(f"unknown modality: {self.modality}")
        if self.polarity not in POLARITIES:
            raise ValueError(f"unknown polarity: {self.polarity}")
        if self.materiality not in MATERIALITY_CLASSES:
            raise ValueError(f"unknown materiality class: {self.materiality}")
        invalid = set(self.qualifications) - MATERIAL_QUALIFICATION_MARKERS
        if invalid:
            raise ValueError(f"qualifications outside marker vocabulary: {sorted(invalid)}")

    @property
    def allowed_lifecycle_wording(self) -> tuple[str, ...]:
        state = lifecycle.normalize_state(self.lifecycle_state)
        if state == "UNKNOWN":
            return ()
        return tuple(sorted(lifecycle.PRESUPPOSED[state] | {state}))

    @property
    def prohibited_lifecycle_wording(self) -> tuple[str, ...]:
        allowed = set(self.allowed_lifecycle_wording)
        return tuple(state for state in lifecycle.LIFECYCLE_STATES
                     if state not in allowed and state != "UNKNOWN")


def reportable_proposition(*, text: str, supporting_claim_ids: Iterable[str],
                           support_state: str, lifecycle_state: str,
                           evidence_act: str, materiality: str,
                           modality: str = "ASSERTED", polarity: str = "POSITIVE",
                           attribution: str | None = None,
                           temporal_scope: tuple[str | None, str | None] = (None, None),
                           scope: Iterable[str] = (),
                           dependence_states: Iterable[str] = (),
                           counterevidence_ids: Iterable[str] = (),
                           qualifications: Iterable[str] = (),
                           correction_state: str = "CURRENT",
                           language: str = "en") -> ReportableProposition:
    claims = tuple(supporting_claim_ids)
    return ReportableProposition(
        stable_id("v5-2-proposition", text, "|".join(claims)),
        " ".join(text.split()), claims, support_state,
        lifecycle.normalize_state(lifecycle_state), evidence_act.strip().upper(),
        attribution, modality, polarity, tuple(temporal_scope), tuple(scope),
        tuple(dependence_states), tuple(counterevidence_ids),
        tuple(qualifications), correction_state, materiality, language, now_utc())


# ---------------------------------------------------------------------------
# Section 15.2 — the publishability contract
# ---------------------------------------------------------------------------

_ACCEPTABLE_SUPPORT = frozenset({
    "FULL_SUPPORT", "PARTIAL_SUPPORT", "QUALIFIED_SUPPORT",
    "CONTEXT_DEPENDENT_SUPPORT",
})


@dataclass(frozen=True)
class Disposition(Record):
    """What the planner did with one proposition, and why."""

    disposition_id: str
    proposition_id: str
    disposition: str
    rendered_sentence: str | None
    reason: str | None
    violations: tuple[RefusalRecord, ...]
    rationale: str
    recorded_time: str

    def __post_init__(self) -> None:
        if self.disposition not in PROPOSITION_DISPOSITIONS:
            raise ValueError(f"unknown disposition: {self.disposition}")
        if self.disposition == "OMITTED_AS_IMMATERIAL":
            if self.reason not in OMISSION_REASONS:
                raise ValueError(
                    "every omission carries a recorded reason from the omission "
                    "vocabulary; generation difficulty is not one of them")
        if self.disposition == "REJECTED_UNSUPPORTED" and self.reason not in REFUSAL_REASONS:
            raise ValueError("a refusal must name why the sentence was refused")
        if self.disposition.startswith("PUBLISHED") and not self.rendered_sentence:
            raise ValueError("a published proposition requires its rendered sentence")
        if self.disposition == "PUBLISHED_WITH_QUALIFICATION" and not self.rendered_sentence:
            raise ValueError("a qualified publication requires its rendered sentence")


@dataclass(frozen=True)
class RefusalRecord(Record):
    """Why one rendering was refused.

    V5.1's ``FaithfulnessViolation`` carries a closed code vocabulary that
    predates the V5.2 refusal reasons, and the frozen module may not be
    edited.  V5.1 violations forwarded from its guards are preserved as
    detail text so no evidence is lost.
    """

    refusal_id: str
    reason: str
    subject: str
    detail: str
    forwarded_v5_1_codes: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.reason not in REFUSAL_REASONS:
            raise ValueError(f"unknown refusal reason: {self.reason}")
        if not self.subject.strip() or not self.detail.strip():
            raise ValueError("a refusal requires a subject and a detail")


def _violation(code: str, subject: str, detail: str,
               forwarded: tuple[FaithfulnessViolation, ...] = ()) -> RefusalRecord:
    return RefusalRecord(
        stable_id("v5-2-refusal", code, subject), code, subject, detail,
        tuple(item.code for item in forwarded))


def _is_complete_proposition(text: str, language: str) -> bool:
    body = " ".join((text or "").split())
    if len(body.split()) < _MINIMUM_PROPOSITION_TOKENS:
        return False
    first = next((ch for ch in body if ch.isalpha()), "")
    if first and first.islower():
        return False
    return split_roles(body, language).finite_clause


def is_normative(text: str) -> bool:
    """Does the sentence state how a rule should be read rather than a fact?"""
    return any(pattern.search(text or "") for pattern in _NORMATIVE)


def unmarked_inference(text: str) -> bool:
    """Does the sentence infer without saying that it is inferring?"""
    body = text or ""
    if not any(pattern.search(body) for pattern in _INFERENCE):
        return False
    return not _INFERENCE_MARKED.search(body)


def temporal_scope_supported(proposition: ReportableProposition,
                             supporting_text: Iterable[str]) -> bool:
    """Is an asserted temporal scope actually carried by the evidence?

    V5.1 published a proposition scoped to January 2024 whose supporting
    excerpt named no January 2024 anything.  A scope the evidence does not
    state is a fabricated qualification, not a compression.
    """
    start, end = proposition.temporal_scope
    if start is None and end is None:
        return True
    body = " ".join(supporting_text)
    for bound in (start, end):
        if not bound:
            continue
        year = str(bound)[:4]
        if year and year not in body:
            return False
    return True


def plan_proposition(proposition: ReportableProposition, rendered_sentence: str, *,
                     supporting_text: Sequence[str] = (),
                     dependence_states: Iterable[str] | None = None,
                     material_counterevidence: bool = False,
                     correction_pending: bool = False) -> Disposition:
    """Decide the disposition of one proposition against its rendering.

    The order is deliberate: what the sentence *is* comes before what it
    says.  Furniture, fragments and citations are refused before any
    support reasoning, because V5.1's failures were dominated by sentences
    that should never have entered the proposition ledger at all.
    """
    text = " ".join((rendered_sentence or "").split())
    forwarded: list[FaithfulnessViolation] = []

    def refuse(reason: str, detail: str) -> Disposition:
        return Disposition(
            stable_id("disposition", proposition.proposition_id, reason),
            proposition.proposition_id, "REJECTED_UNSUPPORTED", None, reason,
            (_violation(reason, proposition.proposition_id, detail,
                        tuple(forwarded)),),
            detail, now_utc())

    chrome_class = chrome.classify_chrome(text) or chrome.classify_chrome(proposition.text)
    if chrome_class:
        return refuse("SITE_OR_ARCHIVE_FURNITURE",
                      f"the sentence is {chrome_class}: site or archive furniture "
                      "carried into the text layer, not an assertion the source "
                      "makes about the world")

    if is_legal_citation(text):
        return refuse("BIBLIOGRAPHIC_CITATION",
                      "the sentence is a reference to a legal act and asserts "
                      "nothing about the world")

    if not _is_complete_proposition(text, proposition.language):
        return refuse("NOT_A_COMPLETE_PROPOSITION",
                      "the sentence is a fragment: it carries no finite clause or "
                      "begins mid-sentence, so it states no proposition")

    if is_normative(text) and proposition.modality not in {"RECOMMENDED",
                                                           "REQUIRED_BY_LAW",
                                                           "CONDITIONAL"}:
        return refuse("NORMATIVE_TEXT_RENDERED_AS_FACT",
                      "the sentence states how a rule should be read; rendering "
                      "interpretive guidance with an assertive modality presents "
                      "a norm as a fact")

    if not proposition.supporting_claim_ids:
        return refuse("NO_SUPPORTING_CLAIM",
                      "no validated claim underwrites the sentence")

    if proposition.support_state not in _ACCEPTABLE_SUPPORT:
        return refuse("NO_SUPPORTING_CLAIM",
                      f"the supporting claim is {proposition.support_state}, which "
                      "may never be published as a factual proposition")

    wording = lifecycle.check_report_wording(
        text, proposition.lifecycle_state, language=proposition.language)
    if not wording.permitted:
        return refuse("LIFECYCLE_STATE_STRENGTHENED", wording.detail)

    if not temporal_scope_supported(proposition, supporting_text):
        return refuse("TEMPORAL_SCOPE_UNSUPPORTED",
                      f"the proposition asserts the scope {proposition.temporal_scope} "
                      "which its supporting evidence does not carry")

    if unmarked_inference(text):
        return refuse("UNMARKED_INFERENCE",
                      "the sentence draws an inference without marking it as one")

    states = tuple(dependence_states if dependence_states is not None
                   else proposition.dependence_states)
    guard = independence_language_guard(text, states)
    if guard:
        forwarded.extend(guard)
        return refuse("UNSUPPORTED_INDEPENDENCE_LANGUAGE",
                      "the sentence claims independent corroboration that the "
                      f"dependence graph does not support: {sorted(set(states))}")

    if correction_pending and proposition.correction_state == "CURRENT":
        return refuse("CORRECTION_OR_RETRACTION_OMITTED",
                      "a correction or retraction governs the supporting evidence "
                      "and the sentence does not carry it")

    if material_counterevidence and not proposition.counterevidence_ids:
        return refuse("MATERIAL_COUNTEREVIDENCE_OMITTED",
                      "material counterevidence exists and the proposition records "
                      "none")

    lowered = text.casefold()
    missing = tuple(marker for marker in proposition.qualifications
                    if marker.casefold().replace("_", " ") not in lowered)
    if missing:
        return refuse("MATERIAL_QUALIFICATION_OMITTED",
                      f"the rendering drops the material qualifications {list(missing)}")

    if proposition.materiality == "BACKGROUND_ONLY":
        return Disposition(
            stable_id("disposition", proposition.proposition_id, "OMITTED"),
            proposition.proposition_id, "OMITTED_AS_IMMATERIAL", None,
            "NOT_MATERIAL_TO_THE_QUESTION", (),
            "supported and faithful, but it does not bear on the research "
            "question", now_utc())

    if proposition.support_state in {"PARTIAL_SUPPORT", "CONTEXT_DEPENDENT_SUPPORT"}:
        return Disposition(
            stable_id("disposition", proposition.proposition_id, "UNCERTAIN"),
            proposition.proposition_id, "MOVED_TO_UNCERTAINTY_SECTION", text, None,
            (), f"support is {proposition.support_state}; the proposition belongs "
            "in the uncertainty section rather than the findings", now_utc())

    disposition = ("PUBLISHED_WITH_QUALIFICATION" if proposition.qualifications
                   else "PUBLISHED")
    return Disposition(
        stable_id("disposition", proposition.proposition_id, disposition),
        proposition.proposition_id, disposition, text, None, (),
        "every publishability condition is met", now_utc())


# ---------------------------------------------------------------------------
# Section 15.3 — executive-summary entailment
# ---------------------------------------------------------------------------

def check_executive_summary(summary_sentences: Sequence[str],
                            published: Sequence[tuple[ReportableProposition, str]]
                            ) -> dict[str, Any]:
    """The summary may compress the detailed report.  It may not strengthen it.

    Two strengthenings are checked because both reached the V5.1 corpus: a
    lifecycle term stronger than any published proposition establishes, and
    independence language no published proposition earns.
    """
    strongest: dict[str, str] = {}
    for proposition, _sentence in published:
        state = lifecycle.normalize_state(proposition.lifecycle_state)
        if state == "UNKNOWN":
            continue
        current = strongest.get("state")
        if current is None or lifecycle.stronger_than(state, current):
            strongest["state"] = state
    ceiling = strongest.get("state", "UNKNOWN")
    all_dependence = tuple(state for proposition, _ in published
                           for state in proposition.dependence_states)

    findings: list[dict[str, str]] = []
    for sentence in summary_sentences:
        reading = lifecycle.derive_lifecycle(sentence)
        annotated = lifecycle.status_annotation(sentence)
        claimed = annotated or reading.state
        if claimed != "UNKNOWN" and (
                ceiling == "UNKNOWN" or not lifecycle.entails(ceiling, claimed)):
            findings.append({
                "code": "EXECUTIVE_SUMMARY_STRENGTHENS_LIFECYCLE",
                "sentence": sentence[:200],
                "detail": (f"the summary asserts {claimed}; the strongest state any "
                           f"published proposition establishes is {ceiling}")})
        for violation in independence_language_guard(sentence, all_dependence):
            findings.append({
                "code": "EXECUTIVE_SUMMARY_UNSUPPORTED_INDEPENDENCE",
                "sentence": sentence[:200], "detail": violation.detail})
        if unmarked_inference(sentence):
            findings.append({
                "code": "EXECUTIVE_SUMMARY_UNMARKED_INFERENCE",
                "sentence": sentence[:200],
                "detail": "the summary infers without marking the inference"})
    return {
        "summary_sentences": len(summary_sentences),
        "published_propositions": len(published),
        "lifecycle_ceiling": ceiling,
        "findings": findings,
        "materially_misleading_propositions": len(findings),
        "entailed": not findings,
    }


# ---------------------------------------------------------------------------
# Section 15.4 — final conditions
# ---------------------------------------------------------------------------

REQUIRED_ZERO_CONDITIONS = (
    "unsupported_factual_propositions", "unmarked_inferences",
    "material_qualification_omissions", "material_correction_omissions",
    "material_retraction_omissions", "unsupported_independence_claims",
    "materially_misleading_executive_summary_propositions",
)

_REASON_TO_CONDITION: Mapping[str, str] = {
    "NO_SUPPORTING_CLAIM": "unsupported_factual_propositions",
    "SITE_OR_ARCHIVE_FURNITURE": "unsupported_factual_propositions",
    "NOT_A_COMPLETE_PROPOSITION": "unsupported_factual_propositions",
    "BIBLIOGRAPHIC_CITATION": "unsupported_factual_propositions",
    "NORMATIVE_TEXT_RENDERED_AS_FACT": "unsupported_factual_propositions",
    "TEMPORAL_SCOPE_UNSUPPORTED": "unsupported_factual_propositions",
    "LIFECYCLE_STATE_STRENGTHENED": "unsupported_factual_propositions",
    "UNMARKED_INFERENCE": "unmarked_inferences",
    "MATERIAL_QUALIFICATION_OMITTED": "material_qualification_omissions",
    "CORRECTION_OR_RETRACTION_OMITTED": "material_correction_omissions",
    "MATERIAL_COUNTEREVIDENCE_OMITTED": "material_qualification_omissions",
    "UNSUPPORTED_INDEPENDENCE_LANGUAGE": "unsupported_independence_claims",
}


def report_audit(dispositions: Sequence[Disposition],
                 executive_summary: Mapping[str, Any] | None = None
                 ) -> dict[str, Any]:
    """Count the conditions Section 15.4 requires to be zero in the report.

    A refusal is the planner working, not a failure: the counts below measure
    what was *published*, so a refused sentence contributes zero.  The refusal
    reasons are reported separately so the planner's behaviour stays visible.
    """
    counts = {name: 0 for name in REQUIRED_ZERO_CONDITIONS}
    refusals: dict[str, int] = {}
    published = qualified = uncertain = omitted = refused = 0
    for item in dispositions:
        if item.disposition == "PUBLISHED":
            published += 1
        elif item.disposition == "PUBLISHED_WITH_QUALIFICATION":
            qualified += 1
        elif item.disposition == "MOVED_TO_UNCERTAINTY_SECTION":
            uncertain += 1
        elif item.disposition == "OMITTED_AS_IMMATERIAL":
            omitted += 1
        else:
            refused += 1
            refusals[str(item.reason)] = refusals.get(str(item.reason), 0) + 1
    if executive_summary:
        counts["materially_misleading_executive_summary_propositions"] = int(
            executive_summary.get("materially_misleading_propositions", 0))
    total = published + qualified + uncertain + omitted + refused
    return {
        "propositions_seen": total,
        "published": published,
        "published_with_qualification": qualified,
        "moved_to_uncertainty_section": uncertain,
        "omitted_as_immaterial": omitted,
        "rejected_unsupported": refused,
        "refusal_reasons": refusals,
        "all_propositions_dispositioned": total == len(dispositions),
        "critical_counts": counts,
        "all_required_counts_zero": all(value == 0 for value in counts.values()),
    }
