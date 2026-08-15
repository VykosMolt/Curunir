"""Semantic invariants and deterministic admissibility (contract Section 6, V5.4).

V5.3 learned a *direct* map ``features -> terminal reviewer-style class``.  It
reached 0.8494 on development, where the labels were previously exposed
reviewer majorities, and fell to 0.4348 on clean held-out evidence — below the
0.5921 of the V5.2 hand-cascade it replaced.  Seven spans were wrongly
admitted.  The diagnosis is not "too few features" or "too little data": a
36-weight perceptron fitted against a five-way terminal class learns the
reviewer panel's idiosyncrasies, because that is the only signal in the label.
Extraction validity was never modelled at all.

This module changes the architecture rather than the hyper-parameters::

    features -> SEMANTIC INVARIANT VECTOR -> deterministic admissibility state

Reviewer labels may calibrate a *threshold* (see :mod:`.calibration`).  They
may not define the semantic function.  The map from invariant vector to
terminal state is :func:`derive_terminal_state`: a pure function with no
learned weights, no thresholds and no data dependence, so it is inspectable,
diffable and testable in isolation from any corpus.

The learned score survives, demoted to its only defensible job: ranking among
alternatives that are *already* invariant-valid — sentence versus expanded
context, two complete candidate propositions, two defensible attribution
boundaries, two layout-consistent interpretations.  :func:`residual_rank`
filters to invariant-valid candidates *before* any scoring call, and the
scoring helper refuses a non-valid assessment outright, so the eleven
forbidden overrides are impossible by construction rather than by convention.

Every predicate reuses the V5.2 detectors (chrome, citation, finite verb,
subject head, non-referential subject, unit hint, antecedent recovery) and the
V5.3 deictic/anaphoric and explicit-date patterns.  Nothing here re-implements
a detector that already exists, and nothing here encodes a campaign, source or
fixture answer.

Research shadow only.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Sequence

from ..v4.models import ExtractionCandidate, NormalizedDocument
from ..v5_1.extraction import _normalize_type, _quarantined_regions
from ..v5_1.models import Record, now_utc, stable_id
from ..v5_2 import chrome, lifecycle
from ..v5_2.semantics import (
    REQUIRED_ROLES, BoundaryEvidence, CandidateLattice, Interpretation,
    _antecedent, _non_referential, _segment_cached, _UNIT_HINT, build_lattice,
    is_legal_citation, span_alignment,
)
from ..v5_3 import ranking
# V5.8.1 D25.  Imported lazily by name rather than by symbol so the
# dependency direction stays visible: v5_4 asks v5_8_1 for predication
# evidence, it does not inherit its vocabulary.
from ..v5_8_1 import clauses as _clauses
from ..v5_8_1 import roles as _roles
from ..v5_8_1 import roles_v2 as _roles_v2
from ..v5_3.ranking import (
    _ANAPHORIC, _EXACT_PRECISIONS, _EXACT_SPAN_TYPES, _EXPLICIT_DATE,
    _INTERROGATIVE, _MATERIAL_MODALITIES, _TABLE_ROW, RankingModel,
)

INVARIANT_VERSION = "curunir-extraction-invariants-v5.4"

# ---------------------------------------------------------------------------
# Section 6.1 — the four states an invariant may take
# ---------------------------------------------------------------------------

SATISFIED = "SATISFIED"
VIOLATED = "VIOLATED"
UNRESOLVED = "UNRESOLVED_FROM_AVAILABLE_CONTEXT"
NOT_APPLICABLE = "NOT_APPLICABLE"

INVARIANT_STATES: tuple[str, ...] = (SATISFIED, VIOLATED, UNRESOLVED, NOT_APPLICABLE)

# NOT_APPLICABLE is *vacuous satisfaction*: the invariant does not bind for
# this candidate type or this lattice shape, so it can neither block nor
# license admission.  It is kept distinct from SATISFIED so a reviewer reading
# a vector can tell "checked and clean" from "did not apply".
ADMITTING_STATES: frozenset[str] = frozenset({SATISFIED, NOT_APPLICABLE})


# ---------------------------------------------------------------------------
# Section 6.2 — the twenty-one invariants
# ---------------------------------------------------------------------------

INVARIANT_NAMES: tuple[str, ...] = (
    # completeness of the proposition the recorded span asserts
    "proposition_complete",
    "referential_subject_complete",
    "predicate_complete",
    "object_or_value_complete",
    "attribution_complete",
    # preservation of what the recorded span says
    "polarity_preserved",
    "modality_preserved",
    "lifecycle_state_preserved",
    "temporal_scope_complete",
    "geographic_scope_complete",
    "unit_and_quantity_complete",
    # the span as a unit of evidence
    "discourse_assertive",
    "layout_mapping_defensible",
    "context_dependency_resolved",
    "no_structural_chrome",
    # what the chosen boundary must not have imported or changed
    "no_cross_column_contamination",
    "no_unrelated_proposition_contamination",
    "no_attribution_shift",
    "no_actor_shift",
    "no_scope_strengthening",
    "no_lifecycle_strengthening",
)

MATERIAL = "MATERIAL"
RECOVERABLE = "RECOVERABLE"

# The inspectable table the whole terminal-state derivation runs on.
#
#   MATERIAL     — a VIOLATION forces REJECTED.  These are the invariants
#                  whose failure means the recorded span is the wrong unit of
#                  evidence or says something the document does not: no
#                  amount of further context repairs it, and no score may
#                  rescue it.
#   RECOVERABLE  — a VIOLATION degrades the terminal state (to EVIDENCE_BOUND
#                  or SEMANTICALLY_PARSED) rather than rejecting.  These are
#                  qualification or completion steps, not falsity.
#
# For *either* kind, UNRESOLVED_FROM_AVAILABLE_CONTEXT routes to QUARANTINED:
# the necessary context exists but the span alone does not fix the reading.
MATERIALITY: Mapping[str, str] = {
    "proposition_complete": MATERIAL,
    "referential_subject_complete": MATERIAL,
    "predicate_complete": MATERIAL,
    "object_or_value_complete": RECOVERABLE,
    "attribution_complete": RECOVERABLE,
    "polarity_preserved": MATERIAL,
    "modality_preserved": MATERIAL,
    "lifecycle_state_preserved": MATERIAL,
    "temporal_scope_complete": MATERIAL,
    "geographic_scope_complete": RECOVERABLE,
    "unit_and_quantity_complete": RECOVERABLE,
    "discourse_assertive": MATERIAL,
    "layout_mapping_defensible": MATERIAL,
    "context_dependency_resolved": RECOVERABLE,
    "no_structural_chrome": MATERIAL,
    "no_cross_column_contamination": MATERIAL,
    "no_unrelated_proposition_contamination": MATERIAL,
    "no_attribution_shift": MATERIAL,
    "no_actor_shift": MATERIAL,
    "no_scope_strengthening": MATERIAL,
    "no_lifecycle_strengthening": MATERIAL,
}
assert set(MATERIALITY) == set(INVARIANT_NAMES)
assert set(MATERIALITY.values()) == {MATERIAL, RECOVERABLE}

MATERIAL_INVARIANTS: tuple[str, ...] = tuple(
    name for name in INVARIANT_NAMES if MATERIALITY[name] == MATERIAL)
RECOVERABLE_INVARIANTS: tuple[str, ...] = tuple(
    name for name in INVARIANT_NAMES if MATERIALITY[name] == RECOVERABLE)

# The recoverable invariants whose violation is a *completion* step over an
# otherwise evidence-bound span.  ``context_dependency_resolved`` is the one
# recoverable invariant that is not: a span whose referent lies outside it is
# semantically parsed but not bound to any evidence the reviewer can check,
# which is exactly the shape of five of the V5.2-era wrong admissions.
COMPLETION_INVARIANTS: tuple[str, ...] = (
    "object_or_value_complete", "attribution_complete",
    "geographic_scope_complete", "unit_and_quantity_complete",
)
assert set(COMPLETION_INVARIANTS) | {"context_dependency_resolved"} == \
    set(RECOVERABLE_INVARIANTS)


# ---------------------------------------------------------------------------
# Section 6.3 — terminal states
# ---------------------------------------------------------------------------

TERMINAL_STATES: tuple[str, ...] = (
    "ACCEPTED_CANDIDATE", "SEMANTICALLY_PARSED", "EVIDENCE_BOUND",
    "QUARANTINED", "REJECTED",
)
# The V5.3 ranker's outcome vocabulary is the same set, so a V5.4 assessment
# can be compared against a V5.3 decision without translation.  Asserted at
# import so the two layers cannot silently drift apart.
assert set(TERMINAL_STATES) == set(ranking.OUTCOMES)

DERIVATION_RULES: tuple[str, ...] = (
    "MATERIAL_INVARIANT_VIOLATED",
    "INVARIANT_UNRESOLVED_FROM_AVAILABLE_CONTEXT",
    "SEMANTIC_STRUCTURE_NOT_EVIDENCE_BOUND",
    "NON_CRITICAL_COMPLETION_REQUIRED",
    "ALL_MATERIAL_INVARIANTS_SATISFIED",
)


# ---------------------------------------------------------------------------
# Section 6.5 — what a learned score may never override
# ---------------------------------------------------------------------------

# name -> (invariant, states of that invariant that disqualify the candidate
# from ever being scored).  This is the direct regression barrier against the
# V5.3 failure: each entry is a condition under which the candidate is removed
# from the ranking population, so no weight vector can reach it.
FORBIDDEN_OVERRIDES: Mapping[str, tuple[str, frozenset[str]]] = {
    "LOST_POLARITY": ("polarity_preserved", frozenset({VIOLATED})),
    "LOST_MATERIAL_MODALITY": ("modality_preserved", frozenset({VIOLATED})),
    "LIFECYCLE_STRENGTHENING": ("no_lifecycle_strengthening", frozenset({VIOLATED})),
    "ATTRIBUTION_SHIFT": ("no_attribution_shift", frozenset({VIOLATED})),
    "ACTOR_SHIFT": ("no_actor_shift", frozenset({VIOLATED})),
    "STRUCTURAL_CHROME": ("no_structural_chrome", frozenset({VIOLATED})),
    "NON_REFERENTIAL_FRAGMENT": ("referential_subject_complete", frozenset({VIOLATED})),
    "CROSS_COLUMN_CONTAMINATION": ("no_cross_column_contamination", frozenset({VIOLATED})),
    "MISSING_PREDICATE": ("predicate_complete", frozenset({VIOLATED})),
    "MISSING_REQUIRED_SUBJECT": ("referential_subject_complete", frozenset({VIOLATED})),
    "UNRESOLVED_MATERIAL_TEMPORAL_SCOPE": ("temporal_scope_complete",
                                           frozenset({UNRESOLVED, VIOLATED})),
}
assert all(invariant in INVARIANT_NAMES
           for invariant, _states in FORBIDDEN_OVERRIDES.values())


# ---------------------------------------------------------------------------
# The evaluation context every predicate reads
# ---------------------------------------------------------------------------

_PROPOSITIONAL_TYPES = frozenset({"CLAIM", "RELATION", "EVENT",
                                  "CORRECTION", "RETRACTION"})
_ATTRIBUTED_MODALITIES = frozenset({"REPORTED", "ATTRIBUTED", "ALLEGED"})

# Modalities that hedge or distance the assertion.  Losing one over-claims;
# gaining one under-claims.  Only the first direction is a violation, because
# only the first direction can make the system say more than the document.
_HEDGING_MODALITIES = frozenset({
    "PLANNED", "PROPOSED", "INTENDED", "PREDICTED", "CONDITIONAL",
    "RECOMMENDED", "PRELIMINARY", "DISPUTED", "ALLEGED", "VENDOR_DESCRIBED",
    "ATTRIBUTED", "REPORTED",
})
assert _HEDGING_MODALITIES <= _MATERIAL_MODALITIES | {"FINAL"}

_NAVIGATIONAL_KINDS = ("HEADER", "FOOTER", "NAV", "MENU", "PAGE_NUMBER",
                       "PAGINATION", "SIDEBAR", "ADVERT", "WATERMARK")


@dataclass(frozen=True)
class InvariantContext:
    """Everything the twenty-one predicates are allowed to look at.

    Built once per candidate so each predicate stays a small pure function of
    a single argument, which is what makes them individually testable.
    """

    candidate: ExtractionCandidate
    document: NormalizedDocument
    lattice: CandidateLattice
    evidence: BoundaryEvidence
    layout_annotation: Any
    selected: Interpretation | None
    seed: Interpretation | None
    block: Interpretation | None
    language: str
    candidate_type: str
    seed_text: str
    selected_text: str
    starts_clean: bool
    ends_clean: bool
    widened: bool
    layout_overlap: tuple[tuple[int, int, str], ...]
    sentences: tuple[tuple[int, int], ...]
    #: V5.8.1 D32.  regions.py knows which enacting clause governs a recital and
    #: which lead-in a list item hangs off.  Before this field existed the
    #: knowledge was computed and then dropped here, and the role binder had to
    #: guess it from a five-word window -- which the diagnostic reference showed
    #: is not possible.  None means no structure was supplied, and the binder is
    #: told that rather than left to infer it.
    structural_context: Any = None

    @property
    def selected_warnings(self) -> frozenset[str]:
        return frozenset(self.selected.warnings) if self.selected else frozenset()

    @property
    def required_roles(self) -> tuple[str, ...]:
        return tuple(REQUIRED_ROLES.get(self.candidate_type, ()))


def build_context(candidate: ExtractionCandidate, document: NormalizedDocument,
                  layout_annotation: Any = None, *, language: str | None = None,
                  document_date: str | None = None,
                  structural_context: Any = None) -> InvariantContext:
    """Resolve one candidate's lattice and freeze the predicate inputs."""
    if candidate.document_id != document.document_id:
        raise ValueError("candidate/document provenance mismatch")
    if document.text[candidate.span_start:candidate.span_end] != candidate.original_text:
        raise ValueError("candidate span fabrication or derivative drift")

    lang = language or getattr(document, "language", None) or "en"
    ntype = _normalize_type(candidate.candidate_type)
    lattice = build_lattice(candidate, document, language=lang,
                            layout_annotation=layout_annotation,
                            document_date=document_date)
    selected = next((item for item in lattice.interpretations
                     if item.interpretation_id == lattice.selected_interpretation_id),
                    None)
    seed = next((item for item in lattice.interpretations if item.level == "NARROW"),
                None)
    block = next((item for item in lattice.interpretations if item.level == "BLOCK"),
                 None)
    starts_clean, ends_clean = span_alignment(
        document.text, candidate.span_start, candidate.span_end)
    overlap = tuple(
        (start, end, kind) for start, end, kind in _quarantined_regions(layout_annotation)
        if candidate.span_start < end and candidate.span_end > start)
    widened = bool(selected is not None and
                   (selected.span_start, selected.span_end) !=
                   (candidate.span_start, candidate.span_end))
    return InvariantContext(
        candidate=candidate, document=document, lattice=lattice,
        evidence=lattice.boundary_evidence, layout_annotation=layout_annotation,
        selected=selected, seed=seed,
        block=block, language=lang, candidate_type=ntype,
        seed_text=candidate.original_text,
        selected_text=(document.text[selected.span_start:selected.span_end]
                       if selected else ""),
        starts_clean=starts_clean, ends_clean=ends_clean, widened=widened,
        layout_overlap=overlap, sentences=_segment_cached(document.text),
        structural_context=structural_context)


@dataclass(frozen=True)
class InvariantFinding:
    """One invariant's state and the one-line reason it holds."""

    invariant: str
    state: str
    detail: str

    def __post_init__(self) -> None:
        if self.state not in INVARIANT_STATES:
            raise ValueError(f"unknown invariant state: {self.state}")


def _finding(state: str, detail: str) -> tuple[str, str]:
    return (state, detail)


def _norm(value: str | None) -> str:
    return " ".join((value or "").split()).casefold().strip(" ,;:.\"'«»")


def _left_context(ctx: InvariantContext) -> str:
    """The document text immediately before the candidate span.

    V5.8.1 defect D29.  The typed role binder resolves an anaphoric subject
    against a bounded left context and declines when the context does not decide
    it.  Both call sites passed no context at all, so every anaphor resolved to
    ANTECEDENT_ABSENT and every clause opening with a pronoun became
    SUBJECT_UNRESOLVED — the binder's anaphora path could not fire in the
    pipeline even though it is tested and correct in isolation.  The context is
    bounded by the binder's own declared window, not widened to rescue a unit.
    """
    start = ctx.candidate.span_start
    return ctx.document.text[max(0, start - _roles.ANTECEDENT_CONTEXT_CHARS):start]


def _enclosing_sentence_text(ctx: InvariantContext) -> str:
    """The document sentence(s) the recorded span sits inside.

    A quotation names its speaker outside the quotation marks by construction,
    so "outside the recorded span" is the wrong test for it; "outside the
    sentence that contains it" is the right one.
    """
    start = ctx.candidate.span_start
    end = ctx.candidate.span_end
    covering = [span for span in ctx.sentences if span[1] > start and span[0] < end]
    if not covering:
        return ctx.seed_text
    return ctx.document.text[covering[0][0]:covering[-1][1]]


# ---------------------------------------------------------------------------
# The twenty-one predicates
# ---------------------------------------------------------------------------

def proposition_complete(ctx: InvariantContext) -> tuple[str, str]:
    """Does any boundary yield a finite clause that asserts something?

    A noun phrase is not a claim.  V5.1 rejected 2836 spans for exactly this
    reason and blind reviewers judged many of those rejections wrong, so the
    "the predicate lives in the enclosing block" case routes to UNRESOLVED
    rather than to VIOLATED.
    """
    if ctx.candidate_type not in _PROPOSITIONAL_TYPES:
        return _finding(NOT_APPLICABLE,
                        f"{ctx.candidate_type} does not assert a proposition")
    if ctx.selected is not None and ctx.selected.finite_clause:
        # A finite clause is necessary but not sufficient.  A propositional span
        # that does not begin and end at sentence boundaries is a fragment of an
        # assertion, not an assertion — it may state a finite clause and still
        # trail off mid-sentence.
        #
        # This requirement used to live in layout_mapping_defensible, where it
        # produced UNRESOLVED and quarantined a third of the corpus.  That was
        # the wrong invariant (locatability is not completeness) but the wrong
        # *verdict* too: moving it here without moving it anywhere turned those
        # quarantines into wrong admissions, 17 -> 26. It belongs here, where a
        # violation is material and yields REJECTED rather than QUARANTINED.
        if not (ctx.starts_clean and ctx.ends_clean):
            # V5.8.1 D25.  When this rule was written the vocabulary had no
            # state for "the proposition is recoverable and the boundary is
            # not", so the only way to stop a fragment being admitted was to
            # call it invalid.  V5.7 added RECOVERABLE_WITH_BOUNDARY_REPAIR and
            # the extraction pipeline now derives it from an UNRESOLVED
            # completeness finding plus a bounded repair plan.  Keeping VIOLATED
            # here rejected 237 of 900 development spans that carried a whole
            # finite clause and were cut only at the edges — over-rejection, not
            # safety.  A span with no propositional content at all still fails
            # materially, below.
            return _finding(UNRESOLVED,
                            f"finite clause at the {ctx.selected.level} boundary, "
                            "but the span does not begin and end at sentence "
                            "boundaries: the assertion is present and the "
                            "boundary is repairable from bounded context")
        return _finding(SATISFIED,
                        f"finite clause at the {ctx.selected.level} boundary")
    if ctx.block is not None and ctx.block.finite_clause:
        return _finding(UNRESOLVED,
                        "no finite clause in the recorded span; the enclosing "
                        "block carries one, so the predicate lives in the list "
                        "stem or heading above it")

    # V5.8.1 D25.  The lattice's finite-clause test is the V5.2 role splitter,
    # whose lexicon covers en, de, fr and es.  On a corpus containing Arabic and
    # Russian it did not misclassify those propositions — it could not see that
    # they were propositions, and 39 of 40 Arabic development spans were
    # rejected here for "no propositional content".  Romance legal recitals
    # failed identically because their predicate is non-finite.  The typed
    # detector adds evidence in dimensions the lexicon has no entry for; it can
    # never remove a clause the lexicon already found, and it resolves to
    # UNRESOLVED rather than inventing a predicate.
    evidence = _clauses.detect(
        ctx.selected_text or ctx.seed_text or ctx.candidate.raw_span,
        language=ctx.language,
        base_finite_clause=bool(ctx.selected and ctx.selected.finite_clause))
    if evidence.state == "FINITE_CLAUSE_ESTABLISHED":
        if not (ctx.starts_clean and ctx.ends_clean):
            return _finding(UNRESOLVED,
                            f"predication established by "
                            f"{', '.join(evidence.establishing_dimensions)} in "
                            f"{evidence.language_or_script}, but the span does "
                            "not begin and end at sentence boundaries")
        return _finding(SATISFIED,
                        f"predication established by "
                        f"{', '.join(evidence.establishing_dimensions)} in "
                        f"{evidence.language_or_script}")
    if evidence.state in ("FINITE_CLAUSE_RECOVERABLE", "FINITE_CLAUSE_UNRESOLVED"):
        return _finding(UNRESOLVED, f"{evidence.state}: {evidence.reason}")
    return _finding(VIOLATED,
                    "no boundary in the lattice yields propositional content, "
                    f"and no predication dimension is present: {evidence.reason}")


def referential_subject_complete(ctx: InvariantContext) -> tuple[str, str]:
    """Is the subject present and does it refer to something on its own?

    Covers both forbidden overrides MISSING_REQUIRED_SUBJECT and
    NON_REFERENTIAL_FRAGMENT: V5.1 accepted "Damit" as the subject of a claim.
    """
    if "subject" not in ctx.required_roles:
        return _finding(NOT_APPLICABLE,
                        f"{ctx.candidate_type} does not require a subject")
    if ctx.selected is None:
        # V5.8.1 D25.  "No interpretation carries a subject" is a statement
        # about the V5.2 splitter's four-language resource, not about the
        # source.  The typed binder is asked before that is treated as fact.
        binding = _roles_v2.bind(
            candidate_id=ctx.candidate.candidate_id,
            text=ctx.selected_text or ctx.seed_text or ctx.candidate.raw_span,
            language=ctx.language, left_context=_left_context(ctx),
            structural_context=ctx.structural_context)
        if binding.subject_state in _roles.SUBJECT_BOUND:
            return _finding(SATISFIED,
                            f"subject bound as {binding.subject_state} in "
                            f"{binding.language_or_script}")
        if binding.subject_state in _roles.SUBJECT_RECOVERABLE:
            return _finding(UNRESOLVED,
                            f"{binding.subject_state}: "
                            f"{binding.repair_requirement or binding.binding_reason}")
        if binding.subject_state == "EXPLETIVE_OR_IMPERSONAL_CONSTRUCTION":
            return _finding(NOT_APPLICABLE,
                            "an impersonal construction has no semantic subject "
                            "to bind")
        return _finding(VIOLATED, "no interpretation carries a subject, and the "
                                  "typed binder finds none either")
    warnings = ctx.selected_warnings
    subject = ctx.selected.subject
    if "NON_REFERENTIAL_SUBJECT" in warnings:
        return _finding(VIOLATED,
                        f"the subject {subject!r} refers to nothing on its own "
                        "and no antecedent was recoverable")
    if not subject.strip():
        return _finding(VIOLATED, "the required subject role is absent")
    if _non_referential(subject, ctx.language):
        # V5.8.1 D25.  Two different findings were collapsed here.  The
        # NON_REFERENTIAL_SUBJECT warning above means the machinery looked for
        # an antecedent and found none — that is genuinely invalid and stays
        # VIOLATED.  This branch is the bare lexical test: the word does not
        # refer *on its own*.  "It spreads when people cough" is a well formed
        # proposition whose subject is resolvable one sentence to the left, and
        # §5.1 is explicit that a named noun phrase is not required.  Rejecting
        # it outright was 47 of 900 development spans.
        return _finding(UNRESOLVED,
                        f"the subject {subject!r} does not refer on its own; an "
                        "antecedent may be recoverable from bounded context")
    if "ANTECEDENT_RESOLVED_FROM_CONTEXT" in warnings:
        return _finding(UNRESOLVED,
                        f"the subject {subject!r} was recovered from the "
                        "preceding sentence; the recorded span does not name it")
    return _finding(SATISFIED, f"referential subject {subject[:60]!r}")


def predicate_complete(ctx: InvariantContext) -> tuple[str, str]:
    """Is the predicate carried by the recorded span itself?

    A predicate supplied by a table header or a list stem is a legitimate
    V5.2 recovery, but it is not in the evidence the reviewer sees, so it
    quarantines rather than admits.
    """
    if "predicate" not in ctx.required_roles and \
            ctx.candidate_type not in _PROPOSITIONAL_TYPES:
        return _finding(NOT_APPLICABLE,
                        f"{ctx.candidate_type} does not require a predicate")
    if ctx.selected is None:
        return _finding(VIOLATED, "no interpretation carries a predicate")
    if "PREDICATE_FROM_TABLE_HEADER" in ctx.selected_warnings:
        return _finding(UNRESOLVED,
                        "the predicate was supplied by a table header, not by "
                        "the recorded span")
    if not ctx.selected.predicate.strip():
        if ctx.block is not None and ctx.block.finite_clause:
            return _finding(UNRESOLVED,
                            "the recorded span carries no predicate; the "
                            "enclosing block does")
        # V5.8.1 D25, second layer.  The role splitter extracts a predicate
        # *string* using the same four-language resource that decides
        # finite_clause, so on Arabic and Russian it returns an empty predicate
        # for spans that plainly predicate something.  Repairing
        # proposition_complete alone simply moved those rejections one
        # invariant downstream — 193 became 112 here and 81 there.  Where the
        # typed detector establishes predication, the predicate is in the span;
        # what is missing is the ability to name it, which is an extraction
        # limit and quarantines rather than rejects.
        evidence = _clauses.detect(
            ctx.selected_text or ctx.seed_text or ctx.candidate.raw_span,
            language=ctx.language,
            base_finite_clause=bool(ctx.selected and ctx.selected.finite_clause))
        if evidence.state in ("FINITE_CLAUSE_ESTABLISHED",
                              "FINITE_CLAUSE_RECOVERABLE"):
            # V5.8.1 D25, third layer.  Repairing predication alone left 119
            # spans quarantined on "the predicate exists but cannot be named".
            # The typed role binder names it, from the same source offsets, in
            # scripts the V5.2 splitter does not model.
            binding = _roles_v2.bind(
                candidate_id=ctx.candidate.candidate_id,
                text=ctx.selected_text or ctx.seed_text or ctx.candidate.raw_span,
                language=ctx.language, left_context=_left_context(ctx),
                structural_context=ctx.structural_context,
                clause_evidence=evidence)
            if binding.predicate_state in _roles.PREDICATE_BOUND:
                return _finding(SATISFIED,
                                f"predicate {binding.predicate_head!r} bound as "
                                f"{binding.predicate_state} in "
                                f"{binding.language_or_script}")
            return _finding(UNRESOLVED,
                            f"{binding.predicate_state}: {binding.binding_reason}")
        return _finding(VIOLATED, "no boundary recovers a predicate")
    return _finding(SATISFIED, f"predicate {ctx.selected.predicate[:60]!r}")


def object_or_value_complete(ctx: InvariantContext) -> tuple[str, str]:
    """Is the object or measured value the assertion is about recovered?"""
    required = ctx.required_roles
    if "object_or_value" not in required and "value" not in required:
        return _finding(NOT_APPLICABLE,
                        f"{ctx.candidate_type} requires no object or value")
    if ctx.selected is None:
        return _finding(VIOLATED, "no interpretation carries an object")
    if "OBJECT_FROM_CAPTION" in ctx.selected_warnings:
        return _finding(UNRESOLVED,
                        "the object identity was supplied by a figure caption")
    if "object_or_value" in required and not ctx.selected.object_or_value.strip():
        return _finding(VIOLATED, "the required object role is absent")
    if "value" in required and not ctx.selected.numeric_values:
        return _finding(VIOLATED, "the required numeric value is absent")
    return _finding(SATISFIED, "object or value recovered from the span")


def attribution_complete(ctx: InvariantContext) -> tuple[str, str]:
    """Is it recorded who said the thing, where saying it is the point?"""
    selected = ctx.selected
    applicable = (ctx.candidate_type == "QUOTATION" or
                  "attribution" in ctx.required_roles or
                  (selected is not None and selected.modality in _ATTRIBUTED_MODALITIES))
    if not applicable:
        return _finding(NOT_APPLICABLE,
                        "the span makes an unattributed assertion in its own voice")
    if selected is None or not (selected.attribution or "").strip():
        return _finding(VIOLATED, "no attribution was recovered at any boundary")
    licensed = (_enclosing_sentence_text(ctx) if ctx.candidate_type == "QUOTATION"
                else ctx.seed_text)
    if _norm(selected.attribution) not in _norm(licensed):
        return _finding(UNRESOLVED,
                        f"the attribution {selected.attribution!r} lies outside "
                        "the evidence the recorded span presents")
    return _finding(SATISFIED, f"attribution {selected.attribution[:60]!r}")


def polarity_preserved(ctx: InvariantContext) -> tuple[str, str]:
    """Does the stored reading assert the same polarity as the recorded span?

    Forbidden override LOST_POLARITY.  A boundary that turns "hat das System
    nicht in Betrieb genommen" into a positive reading has changed what the
    document says, however well the span scores.
    """
    if ctx.selected is None or ctx.seed is None:
        return _finding(NOT_APPLICABLE, "no competing boundary to compare")
    if ctx.selected.polarity == ctx.seed.polarity:
        return _finding(SATISFIED, f"polarity {ctx.seed.polarity} on both readings")
    return _finding(VIOLATED,
                    f"the recorded span reads {ctx.seed.polarity} but the "
                    f"{ctx.selected.level} boundary reads {ctx.selected.polarity}")


def modality_preserved(ctx: InvariantContext) -> tuple[str, str]:
    """Is material modality neither lost nor invented by the boundary?

    Forbidden override LOST_MATERIAL_MODALITY.  PLANNED read as ASSERTED is
    the single most consequential error this system can make.
    """
    if ctx.selected is None or ctx.seed is None:
        return _finding(NOT_APPLICABLE, "no competing boundary to compare")
    seed_modality, selected_modality = ctx.seed.modality, ctx.selected.modality
    if seed_modality == selected_modality:
        return _finding(SATISFIED, f"modality {seed_modality} on both readings")
    if seed_modality in _HEDGING_MODALITIES and \
            selected_modality not in _HEDGING_MODALITIES:
        return _finding(VIOLATED,
                        f"the recorded span hedges with {seed_modality}; the "
                        f"{ctx.selected.level} boundary reads {selected_modality}")
    if seed_modality in _MATERIAL_MODALITIES and \
            selected_modality not in _MATERIAL_MODALITIES:
        return _finding(VIOLATED,
                        f"the recorded span records {seed_modality}; the "
                        f"{ctx.selected.level} boundary reads {selected_modality}")
    if selected_modality in _HEDGING_MODALITIES and \
            seed_modality not in _HEDGING_MODALITIES:
        return _finding(SATISFIED,
                        f"the boundary hedges {seed_modality} down to "
                        f"{selected_modality}, which never over-claims")
    return _finding(UNRESOLVED,
                    f"competing modalities {seed_modality} and "
                    f"{selected_modality} remain")


def lifecycle_state_preserved(ctx: InvariantContext) -> tuple[str, str]:
    """Does the chosen boundary keep the lifecycle reading the span supports?"""
    if ctx.selected is None or ctx.seed is None:
        return _finding(NOT_APPLICABLE, "no competing boundary to compare")
    seed_state, selected_state = ctx.seed.lifecycle_state, ctx.selected.lifecycle_state
    if seed_state == selected_state:
        return _finding(SATISFIED, f"lifecycle state {seed_state} on both readings")
    if seed_state != "UNKNOWN" and selected_state == "UNKNOWN":
        return _finding(VIOLATED,
                        f"the recorded span supports {seed_state}; the "
                        f"{ctx.selected.level} boundary loses it")
    if seed_state == "UNKNOWN":
        return _finding(UNRESOLVED,
                        f"lifecycle state {selected_state} was supplied from "
                        "outside the recorded span")
    if lifecycle.stronger_than(seed_state, selected_state):
        return _finding(SATISFIED,
                        f"the boundary demotes {seed_state} to {selected_state}, "
                        "which is the conservative direction")
    return _finding(UNRESOLVED,
                    f"lifecycle readings {seed_state} and {selected_state} are "
                    "not comparable")


def temporal_scope_complete(ctx: InvariantContext) -> tuple[str, str]:
    """Is an asserted calendar scope bound, and bound to an exact mapping?

    Forbidden override UNRESOLVED_MATERIAL_TEMPORAL_SCOPE.  An asserted date
    resting on an approximate span mapping is unverifiable by construction.
    """
    required = "temporal_scope" in ctx.required_roles
    has_scope = ctx.selected is not None and ctx.selected.temporal_scope != (None, None)
    asserts_date = bool(_EXPLICIT_DATE.search(ctx.seed_text))
    if required and not has_scope:
        return _finding(VIOLATED,
                        f"{ctx.candidate_type} requires a temporal scope and "
                        "none was bound at any boundary")
    if has_scope and asserts_date and \
            ctx.candidate.mapping_precision not in _EXACT_PRECISIONS:
        return _finding(UNRESOLVED,
                        f"an asserted calendar scope rests on "
                        f"{ctx.candidate.mapping_precision} mapping")
    if asserts_date and not has_scope:
        return _finding(UNRESOLVED,
                        "the span carries a calendar expression the parser "
                        "could not bind to a scope")
    if not required and not asserts_date:
        return _finding(NOT_APPLICABLE, "the span asserts no calendar scope")
    return _finding(SATISFIED,
                    f"temporal scope {ctx.selected.temporal_scope if ctx.selected else ()}")


def geographic_scope_complete(ctx: InvariantContext) -> tuple[str, str]:
    """Is the place the assertion is about carried by the recorded span?"""
    required = ctx.candidate_type == "GEOGRAPHIC_SCOPE"
    seed_scope = set(ctx.seed.geographic_scope) if ctx.seed else set()
    selected_scope = set(ctx.selected.geographic_scope) if ctx.selected else set()
    if required and not selected_scope:
        return _finding(VIOLATED,
                        "the candidate is typed as a geographic scope and none "
                        "was recovered")
    if not required and not (seed_scope or selected_scope):
        return _finding(NOT_APPLICABLE, "the span asserts no geographic scope")
    if selected_scope and not seed_scope:
        return _finding(UNRESOLVED,
                        f"geographic scope {sorted(selected_scope)} was supplied "
                        "from outside the recorded span")
    return _finding(SATISFIED, f"geographic scope {sorted(seed_scope)}")


#: A figure that is quoted rather than merely matched.
_MEASUREMENT = re.compile(
    r"(?<![\w/])\d[\d  .,]*\s*(?:%|percent|prozent|per\s?cent|"
    r"(?:€|£|\$|EUR|USD|GBP|billion|million|milliarden|mio|mrd|bn\b|"
    r"tonnes?|tons?|kg|km|kwh|mwh|gwh|twh|mw|gw|years?|months?|days?|hours?))",
    re.IGNORECASE)

#: An identifier: matched, never quoted.  Approximate mapping does not endanger
#: it, because it is recognised rather than transcribed.
_IDENTIFIER_NUMBER = re.compile(
    r"\((?:EU|EC|EEC|UE)\)\s*(?:No\s*)?\d|Article\s+\d|Artikel\s+\d|"
    r"paragraph\s+\d|Annex\s+[IVX\d]|CELEX|ISBN|ISSN|doi:", re.IGNORECASE)


def _is_measurement(text: str) -> bool:
    """Is a figure in this span quoted, rather than merely matched?"""
    if not _MEASUREMENT.search(text or ""):
        return False
    # A span whose only figures sit inside citations is identifying, not
    # measuring, however many digits it contains.
    stripped = _IDENTIFIER_NUMBER.sub(" ", text or "")
    return bool(_MEASUREMENT.search(stripped))


def unit_and_quantity_complete(ctx: InvariantContext) -> tuple[str, str]:
    """Does a measurement carry both its number and its unit?"""
    selected = ctx.selected
    numeric = bool(selected and selected.numeric_values)
    if ctx.candidate_type != "NUMERIC_VALUE" and not numeric:
        return _finding(NOT_APPLICABLE, "the span states no quantity")
    if ctx.candidate_type == "NUMERIC_VALUE" and not numeric:
        return _finding(VIOLATED,
                        "a numeric candidate with no number at any boundary")
    if "UNIT_FROM_TABLE_HEADER" in ctx.selected_warnings:
        return _finding(UNRESOLVED,
                        "the unit was supplied by a table header, not by the "
                        "recorded span")
    if ctx.candidate_type == "NUMERIC_VALUE" and not _UNIT_HINT.search(ctx.seed_text):
        return _finding(VIOLATED,
                        "a measurement whose unit is absent from the recorded span")
    # A figure that must be quoted exactly cannot rest on an approximate span
    # mapping.  This is not a quarantine — the proposition is sound and its
    # evidence binding is incomplete, which is precisely EVIDENCE_BOUND.
    #
    # The rule is taken from the blind reviewers' own recorded reasoning rather
    # than inferred: "two exact percentages carried at only APPROXIMATE_SECTION
    # mapping precision, which is too weak for figures that must be quoted
    # exactly", and "the span is the article headline truncated at a decimal
    # point, so the recorded amount reads 'additional EUR1' where the source says
    # EUR1.2 billion".
    # ...but only when the figure is the claim's payload.  A legal citation
    # ("Regulation (EU) 2016/679"), an article number or a bare year is an
    # identifier: it is matched, not quoted, and approximate mapping does not
    # endanger it.  A measurement — a percentage, a currency amount, a quantity
    # with a unit — is quoted, and must be exact.  Every one of the reviewers'
    # recorded examples is a measurement.
    if (numeric and _is_measurement(ctx.seed_text)
            and ctx.candidate.mapping_precision not in _EXACT_PRECISIONS):
        return _finding(VIOLATED,
                        f"the span carries figures that must be quoted exactly but "
                        f"its mapping is only {ctx.candidate.mapping_precision}; the "
                        "evidence binding is incomplete, not the proposition")
    return _finding(SATISFIED, "quantity and unit are both present")


def discourse_assertive(ctx: InvariantContext) -> tuple[str, str]:
    """Is the span in the assertive mood at all?

    A question asserts nothing; a bibliographic reference to a legal act
    identifies a document and asserts nothing about the world.  V5.1 typed OJ
    footnote citations as claims and they reached the clean corpus.
    """
    if ctx.candidate_type not in _PROPOSITIONAL_TYPES:
        return _finding(NOT_APPLICABLE,
                        f"{ctx.candidate_type} carries no discourse assertion")
    text = ctx.seed_text.strip()
    if _INTERROGATIVE.search(text):
        return _finding(VIOLATED, "the span is a question")
    if is_legal_citation(text):
        return _finding(VIOLATED, "the span is a bibliographic legal citation")
    return _finding(SATISFIED, "the span is in the assertive mood")


def layout_mapping_defensible(ctx: InvariantContext) -> tuple[str, str]:
    """Can a reviewer find this span, as recorded, in the source layout?"""
    if ctx.layout_overlap:
        kinds = sorted({kind for _s, _e, kind in ctx.layout_overlap})
        return _finding(VIOLATED,
                        f"the span overlaps layout-quarantined regions {kinds}")
    # Sentence alignment is demanded only of the propositional types.  A
    # NUMERIC_VALUE, DATE or ENTITY_MENTION span is sub-sentential by design;
    # requiring it to be a whole sentence would reject every one of them,
    # which is the V5.1 over-rejection failure in a new costume.
    propositional = ctx.candidate_type in _PROPOSITIONAL_TYPES
    if propositional and not ctx.starts_clean and \
            not (ctx.selected and ctx.selected.finite_clause):
        return _finding(VIOLATED,
                        "the span begins inside a sentence and carries no finite "
                        "clause; it is a line-wrap fragment")
    if ctx.candidate_type in _EXACT_SPAN_TYPES and \
            ctx.candidate.mapping_precision not in _EXACT_PRECISIONS:
        return _finding(UNRESOLVED,
                        f"{ctx.candidate_type} requires exact span mapping; "
                        f"{ctx.candidate.mapping_precision} is weaker")
    if ctx.evidence.continues_next_page:
        return _finding(UNRESOLVED,
                        "the span continues across a declared page join")
    if propositional and not (ctx.starts_clean and ctx.ends_clean):
        # This invariant asks whether a reviewer can *locate* the span as
        # recorded.  Whether the span is a whole sentence is a different
        # question, owned by proposition_complete and the boundary handling
        # above.  Conflating the two made an exactly-mapped span quarantine for
        # not being sentence-aligned, and that single branch accounted for all
        # 32 UNRESOLVED verdicts in the first real measurement — a third of the
        # corpus, driving a 51% quarantine rate.
        #
        # An exact character mapping is locatable by construction: the reviewer
        # is given the page and the offsets.  Only a weaker mapping leaves them
        # unable to find what was recorded.
        if ctx.candidate.mapping_precision in _EXACT_PRECISIONS:
            return _finding(SATISFIED,
                            "the span is not sentence-aligned, but an exact "
                            "character mapping locates it unambiguously; sentence "
                            "completeness is proposition_complete's question")
        return _finding(UNRESOLVED,
                        f"the recorded span does not begin and end at sentence "
                        f"boundaries and its mapping is only "
                        f"{ctx.candidate.mapping_precision}")
    return _finding(SATISFIED, "the recorded span carries a defensible mapping")


def context_dependency_resolved(ctx: InvariantContext) -> tuple[str, str]:
    """Does the span say what it is about without its neighbours?

    Five of the eleven V5.2-era wrong admissions were a well-formed sentence
    whose referent lay outside it: "Dabei wurde die Software eingesetzt."
    """
    found = _ANAPHORIC.search(ctx.seed_text)
    if not found:
        return _finding(SATISFIED, "no unresolved deictic or discourse connective")
    marker = found.group(0).strip()
    antecedent = _antecedent(ctx.evidence.previous_sentence, ctx.language)
    if antecedent:
        return _finding(UNRESOLVED,
                        f"the connective {marker!r} points at {antecedent[:60]!r} "
                        "in the preceding sentence")
    return _finding(VIOLATED,
                    f"the connective {marker!r} has no recoverable antecedent")


def no_structural_chrome(ctx: InvariantContext) -> tuple[str, str]:
    """Is the recorded span site or archive furniture?

    Forbidden override STRUCTURAL_CHROME.  Six of the twelve V5.1
    report-faithfulness failures were chrome published as factual propositions.
    """
    found = chrome.classify_chrome(ctx.seed_text)
    if found:
        return _finding(VIOLATED, f"the span is {found}")
    navigational = [kind for _s, _e, kind in ctx.layout_overlap
                    if any(token in kind for token in _NAVIGATIONAL_KINDS)]
    if navigational:
        return _finding(VIOLATED,
                        f"the span sits in navigational layout {sorted(set(navigational))}")
    return _finding(SATISFIED, "the span is body text, not furniture")


def no_cross_column_contamination(ctx: InvariantContext) -> tuple[str, str]:
    """Did the boundary concatenate two independent layout columns?

    Forbidden override CROSS_COLUMN_CONTAMINATION.  A column gap inside a span
    is licensed only when a table header binds the row into one structured
    reading; otherwise the span joins text that was never adjacent.
    """
    seed_gap = bool(_TABLE_ROW.search(ctx.seed_text))
    selected_gap = bool(_TABLE_ROW.search(ctx.selected_text))
    if not (seed_gap or selected_gap):
        return _finding(NOT_APPLICABLE, "no column separator in either boundary")
    if selected_gap and not seed_gap:
        return _finding(VIOLATED,
                        f"the {ctx.selected.level if ctx.selected else 'chosen'} "
                        "boundary imported material across a column separator")
    if seed_gap and ctx.evidence.table_header:
        return _finding(SATISFIED,
                        "the column gap is a table row bound by its header")
    return _finding(VIOLATED,
                    "the recorded span crosses a column separator with no table "
                    "header to bind the row")


def no_unrelated_proposition_contamination(ctx: InvariantContext) -> tuple[str, str]:
    """Did the boundary import a neighbouring assertion's material?"""
    if ctx.selected is None or ctx.seed is None or not ctx.widened:
        return _finding(NOT_APPLICABLE,
                        "the recorded span is the selected boundary")
    if not ctx.selected.role_bearing:
        return _finding(VIOLATED,
                        "the recorded span is already a whole sentence, so a "
                        "wider boundary can only import a neighbouring assertion")
    seed_sentences = [span for span in ctx.sentences
                      if span[1] > ctx.seed.span_start and span[0] < ctx.seed.span_end]
    selected_sentences = [span for span in ctx.sentences
                          if span[1] > ctx.selected.span_start
                          and span[0] < ctx.selected.span_end]
    if len(selected_sentences) > len(seed_sentences):
        return _finding(UNRESOLVED,
                        f"the {ctx.selected.level} boundary spans "
                        f"{len(selected_sentences)} sentences where the recorded "
                        f"span covers {len(seed_sentences)}")
    return _finding(SATISFIED,
                    "the boundary stays inside the recorded span's own sentence")


def no_attribution_shift(ctx: InvariantContext) -> tuple[str, str]:
    """Does the boundary keep the attribution the recorded span carries?

    Forbidden override ATTRIBUTION_SHIFT.
    """
    if ctx.selected is None or ctx.seed is None:
        return _finding(NOT_APPLICABLE, "no competing boundary to compare")
    seed_attribution, selected_attribution = _norm(ctx.seed.attribution), \
        _norm(ctx.selected.attribution)
    if not seed_attribution:
        # There is no attribution on the recorded span to reassign.  Whether
        # one is *required* is attribution_complete's question, not this one.
        return _finding(NOT_APPLICABLE,
                        "the recorded span carries no attribution to reassign")
    if not selected_attribution:
        return _finding(VIOLATED,
                        f"the boundary drops the attribution {ctx.seed.attribution!r}")
    if seed_attribution != selected_attribution:
        return _finding(VIOLATED,
                        f"the boundary reassigns the attribution from "
                        f"{ctx.seed.attribution!r} to {ctx.selected.attribution!r}")
    return _finding(SATISFIED, "attribution unchanged across boundaries")


def no_actor_shift(ctx: InvariantContext) -> tuple[str, str]:
    """Does the boundary keep the actor the recorded span names?

    Forbidden override ACTOR_SHIFT.  A wider boundary whose first finite verb
    belongs to a different clause silently re-assigns who did the thing.
    """
    if ctx.candidate_type not in _PROPOSITIONAL_TYPES:
        return _finding(NOT_APPLICABLE,
                        f"{ctx.candidate_type} names no actor of its own")
    if ctx.selected is None or ctx.seed is None:
        return _finding(NOT_APPLICABLE, "no competing boundary to compare")
    if not ctx.seed.finite_clause:
        # ``split_roles`` returns the whole span as ``subject`` when it finds
        # no finite verb.  That string is a noun phrase, not an actor, so
        # comparing it against a real subject manufactures a shift that the
        # document never made.
        return _finding(NOT_APPLICABLE,
                        "the recorded span carries no clause whose actor could shift")
    seed_subject, selected_subject = _norm(ctx.seed.subject), _norm(ctx.selected.subject)
    if not seed_subject or not selected_subject:
        return _finding(NOT_APPLICABLE, "no subject on one of the boundaries")
    if seed_subject == selected_subject:
        return _finding(SATISFIED, "the actor is identical on both boundaries")
    if "ANTECEDENT_RESOLVED_FROM_CONTEXT" in ctx.selected_warnings:
        return _finding(UNRESOLVED,
                        "the actor was recovered from the preceding sentence")
    if seed_subject in selected_subject or selected_subject in seed_subject:
        return _finding(UNRESOLVED,
                        f"the boundary extends the actor phrase from "
                        f"{ctx.seed.subject!r} to {ctx.selected.subject!r}")
    return _finding(VIOLATED,
                    f"the boundary changes the actor from {ctx.seed.subject!r} to "
                    f"{ctx.selected.subject!r}")


def no_scope_strengthening(ctx: InvariantContext) -> tuple[str, str]:
    """Does the boundary widen the temporal or geographic claim?"""
    if ctx.selected is None or ctx.seed is None or not ctx.widened:
        return _finding(NOT_APPLICABLE,
                        "the recorded span is the selected boundary")
    seed_temporal, selected_temporal = ctx.seed.temporal_scope, ctx.selected.temporal_scope
    if seed_temporal != (None, None) and selected_temporal != (None, None) and \
            seed_temporal != selected_temporal:
        return _finding(VIOLATED,
                        f"the boundary restates the temporal scope from "
                        f"{seed_temporal} to {selected_temporal}")
    seed_scope = set(ctx.seed.geographic_scope)
    selected_scope = set(ctx.selected.geographic_scope)
    if seed_scope and not selected_scope <= seed_scope:
        return _finding(VIOLATED,
                        f"the boundary asserts places {sorted(selected_scope - seed_scope)} "
                        "the recorded span does not")
    if selected_scope and not seed_scope:
        return _finding(UNRESOLVED,
                        f"places {sorted(selected_scope)} were supplied from "
                        "outside the recorded span")
    return _finding(SATISFIED, "neither scope was widened by the boundary")


def no_lifecycle_strengthening(ctx: InvariantContext) -> tuple[str, str]:
    """Does the boundary read a stronger lifecycle state than the span supports?

    Forbidden override LIFECYCLE_STRENGTHENING.  PROPOSED read as DEPLOYED is
    the V5.1 lifecycle-corruption failure in one line.
    """
    if ctx.selected is None or ctx.seed is None:
        return _finding(NOT_APPLICABLE, "no competing boundary to compare")
    seed_state, selected_state = ctx.seed.lifecycle_state, ctx.selected.lifecycle_state
    if seed_state == "UNKNOWN" or selected_state == "UNKNOWN":
        return _finding(NOT_APPLICABLE,
                        "one boundary reads no lifecycle state to compare")
    if lifecycle.stronger_than(selected_state, seed_state):
        return _finding(VIOLATED,
                        f"the boundary reads {selected_state} where the recorded "
                        f"span supports only {seed_state}")
    return _finding(SATISFIED,
                    f"the boundary does not strengthen {seed_state}")


INVARIANT_PREDICATES: Mapping[str, Any] = {
    "proposition_complete": proposition_complete,
    "referential_subject_complete": referential_subject_complete,
    "predicate_complete": predicate_complete,
    "object_or_value_complete": object_or_value_complete,
    "attribution_complete": attribution_complete,
    "polarity_preserved": polarity_preserved,
    "modality_preserved": modality_preserved,
    "lifecycle_state_preserved": lifecycle_state_preserved,
    "temporal_scope_complete": temporal_scope_complete,
    "geographic_scope_complete": geographic_scope_complete,
    "unit_and_quantity_complete": unit_and_quantity_complete,
    "discourse_assertive": discourse_assertive,
    "layout_mapping_defensible": layout_mapping_defensible,
    "context_dependency_resolved": context_dependency_resolved,
    "no_structural_chrome": no_structural_chrome,
    "no_cross_column_contamination": no_cross_column_contamination,
    "no_unrelated_proposition_contamination": no_unrelated_proposition_contamination,
    "no_attribution_shift": no_attribution_shift,
    "no_actor_shift": no_actor_shift,
    "no_scope_strengthening": no_scope_strengthening,
    "no_lifecycle_strengthening": no_lifecycle_strengthening,
}
assert tuple(INVARIANT_PREDICATES) == INVARIANT_NAMES


# ---------------------------------------------------------------------------
# The invariant vector
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class InvariantVector(Record):
    """The twenty-one semantic invariants of one candidate interpretation."""

    vector_id: str
    candidate_id: str
    document_id: str
    candidate_type: str
    states: Mapping[str, str]
    findings: Mapping[str, str]
    invariant_version: str

    def __post_init__(self) -> None:
        if tuple(self.states) != INVARIANT_NAMES:
            raise ValueError("an invariant vector must carry all 21 invariants "
                             "in the declared order")
        for name, state in self.states.items():
            if state not in INVARIANT_STATES:
                raise ValueError(f"{name}: unknown invariant state {state!r}")

    def state(self, invariant: str) -> str:
        return self.states[invariant]

    def names_in(self, *states: str) -> tuple[str, ...]:
        wanted = frozenset(states)
        return tuple(name for name in INVARIANT_NAMES if self.states[name] in wanted)

    def material_violations(self) -> tuple[str, ...]:
        return tuple(name for name in MATERIAL_INVARIANTS
                     if self.states[name] == VIOLATED)

    def recoverable_violations(self) -> tuple[str, ...]:
        return tuple(name for name in RECOVERABLE_INVARIANTS
                     if self.states[name] == VIOLATED)

    def unresolved(self) -> tuple[str, ...]:
        return self.names_in(UNRESOLVED)


def compute_invariants(ctx: InvariantContext) -> InvariantVector:
    """Run all twenty-one predicates over one context, in declared order."""
    states: dict[str, str] = {}
    findings: dict[str, str] = {}
    for name in INVARIANT_NAMES:
        state, detail = INVARIANT_PREDICATES[name](ctx)
        InvariantFinding(name, state, detail)  # validates the state vocabulary
        states[name] = state
        findings[name] = detail
    return InvariantVector(
        stable_id("v5-4-invariants", ctx.candidate.candidate_id,
                  ctx.lattice.lattice_id),
        ctx.candidate.candidate_id, ctx.document.document_id, ctx.candidate_type,
        states, findings, INVARIANT_VERSION)


# ---------------------------------------------------------------------------
# Section 6.3 — deterministic terminal-state derivation
# ---------------------------------------------------------------------------

def derive_terminal_state(vector: InvariantVector | Mapping[str, str]
                          ) -> tuple[str, str]:
    """Terminal state from the invariant vector alone.

    A pure function: no learned weights, no thresholds, no corpus, no clock.
    The whole decision is the following five-line cascade over the materiality
    table, and that is the point — a reviewer can audit it without running it.

    * ``REJECTED`` — a MATERIAL invariant is VIOLATED.  Not a proposition,
      structural chrome, non-referential fragment, materially incomplete
      assertion, contamination, lost polarity or modality, changed
      attribution or actor, strengthened lifecycle, invalid mapping, or an
      unrecoverable boundary defect.
    * ``QUARANTINED`` — some invariant is UNRESOLVED_FROM_AVAILABLE_CONTEXT:
      the necessary context exists but the recorded span does not fix the
      reading, layout mapping is uncertain, or competing semantic structures
      remain.
    * ``SEMANTICALLY_PARSED`` — coherent semantics, but the span does not say
      what it is about without its neighbours, so it is not evidence-bound.
    * ``EVIDENCE_BOUND`` — defensible evidence mapping, one non-critical
      qualification or completion step outstanding.
    * ``ACCEPTED_CANDIDATE`` — every material invariant SATISFIED or
      vacuously NOT_APPLICABLE, and nothing UNRESOLVED anywhere.

    Returns ``(terminal_state, derivation_rule)``.
    """
    states = vector.states if isinstance(vector, InvariantVector) else dict(vector)
    missing = set(INVARIANT_NAMES) - set(states)
    if missing:
        raise ValueError(f"incomplete invariant vector, missing {sorted(missing)}")

    if any(states[name] == VIOLATED for name in MATERIAL_INVARIANTS):
        return ("REJECTED", "MATERIAL_INVARIANT_VIOLATED")
    if any(states[name] == UNRESOLVED for name in INVARIANT_NAMES):
        return ("QUARANTINED", "INVARIANT_UNRESOLVED_FROM_AVAILABLE_CONTEXT")
    if states["context_dependency_resolved"] == VIOLATED:
        return ("SEMANTICALLY_PARSED", "SEMANTIC_STRUCTURE_NOT_EVIDENCE_BOUND")
    if any(states[name] == VIOLATED for name in COMPLETION_INVARIANTS):
        return ("EVIDENCE_BOUND", "NON_CRITICAL_COMPLETION_REQUIRED")
    assert all(states[name] in ADMITTING_STATES for name in INVARIANT_NAMES)
    return ("ACCEPTED_CANDIDATE", "ALL_MATERIAL_INVARIANTS_SATISFIED")


def forbidden_overrides_present(vector: InvariantVector | Mapping[str, str]
                                ) -> tuple[str, ...]:
    """Which of the eleven no-rescue conditions hold for this vector."""
    states = vector.states if isinstance(vector, InvariantVector) else dict(vector)
    return tuple(sorted(
        name for name, (invariant, disqualifying) in FORBIDDEN_OVERRIDES.items()
        if states.get(invariant) in disqualifying))


def is_invariant_valid(vector: InvariantVector | Mapping[str, str]) -> bool:
    """May a learned score rank this candidate at all?

    True only when no material invariant is VIOLATED and none of the eleven
    forbidden-override conditions holds.  Everything else is removed from the
    ranking population before any weight is applied.
    """
    states = vector.states if isinstance(vector, InvariantVector) else dict(vector)
    if any(states[name] == VIOLATED for name in MATERIAL_INVARIANTS):
        return False
    return not forbidden_overrides_present(states)


# ---------------------------------------------------------------------------
# The assessment record
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class AdmissibilityAssessment(Record):
    """One candidate's invariant vector and the state derived from it."""

    assessment_id: str
    candidate_id: str
    document_id: str
    terminal_state: str
    derivation_rule: str
    vector: InvariantVector
    material_violations: tuple[str, ...]
    recoverable_violations: tuple[str, ...]
    unresolved: tuple[str, ...]
    forbidden_overrides: tuple[str, ...]
    invariant_valid: bool
    selected_level: str | None
    selected_span: tuple[int, int] | None
    rationale: str
    lattice_id: str
    invariant_version: str
    recorded_time: str

    def __post_init__(self) -> None:
        if self.terminal_state not in TERMINAL_STATES:
            raise ValueError(f"unknown terminal state: {self.terminal_state}")
        if self.derivation_rule not in DERIVATION_RULES:
            raise ValueError(f"unknown derivation rule: {self.derivation_rule}")
        if self.terminal_state == "ACCEPTED_CANDIDATE" and not self.invariant_valid:
            raise ValueError("an accepted candidate must be invariant-valid")
        if self.terminal_state == "ACCEPTED_CANDIDATE" and (
                self.material_violations or self.recoverable_violations or
                self.unresolved):
            raise ValueError("an accepted candidate may carry no invariant defect")


def assess_candidate(candidate: ExtractionCandidate, document: NormalizedDocument,
                     layout_annotation: Any = None, *, language: str | None = None,
                     document_date: str | None = None,
                     structural_context: Any = None) -> AdmissibilityAssessment:
    """Decide one candidate by computing invariants, then deriving the state.

    This is the whole V5.4 decision.  No model is consulted, because no model
    is allowed to decide the state — only to rank among states already
    derived.
    """
    ctx = build_context(candidate, document, layout_annotation,
                        language=language, document_date=document_date,
                        structural_context=structural_context)
    vector = compute_invariants(ctx)
    terminal_state, rule = derive_terminal_state(vector)
    overrides = forbidden_overrides_present(vector)
    valid = is_invariant_valid(vector)

    material = vector.material_violations()
    recoverable = vector.recoverable_violations()
    unresolved = vector.unresolved()
    if terminal_state == "REJECTED":
        rationale = ("material invariants violated: " +
                     ", ".join(f"{name} ({vector.findings[name]})"
                               for name in material))
    elif terminal_state == "QUARANTINED":
        rationale = ("unresolved from available context: " +
                     ", ".join(f"{name} ({vector.findings[name]})"
                               for name in unresolved))
    elif terminal_state == "SEMANTICALLY_PARSED":
        rationale = ("semantics coherent but not evidence-bound: " +
                     vector.findings["context_dependency_resolved"])
    elif terminal_state == "EVIDENCE_BOUND":
        rationale = ("evidence mapping defensible; completion outstanding: " +
                     ", ".join(f"{name} ({vector.findings[name]})"
                               for name in recoverable))
    else:
        rationale = ("all 21 invariants satisfied or vacuous at the "
                     f"{ctx.selected.level if ctx.selected else 'recorded'} boundary")

    return AdmissibilityAssessment(
        stable_id("v5-4-assessment", candidate.candidate_id, terminal_state),
        candidate.candidate_id, document.document_id, terminal_state, rule,
        vector, material, recoverable, unresolved, overrides, valid,
        ctx.selected.level if ctx.selected else None,
        (ctx.selected.span_start, ctx.selected.span_end) if ctx.selected else None,
        rationale, ctx.lattice.lattice_id, INVARIANT_VERSION, now_utc())


# ---------------------------------------------------------------------------
# Section 6.6 — residual ranking, structurally confined to survivors
# ---------------------------------------------------------------------------

RESIDUAL_RANKER_ROLE = (
    "rank among invariant-valid alternatives only; never select a terminal state")


@dataclass(frozen=True)
class ResidualRanking(Record):
    """The ranking, plus proof of which candidates the model ever saw."""

    ranking_id: str
    document_id: str
    considered: int
    scored_candidate_ids: tuple[str, ...]
    excluded_candidate_ids: tuple[str, ...]
    ordered: tuple[Mapping[str, Any], ...]
    excluded: tuple[Mapping[str, Any], ...]
    ranker_role: str
    ranker_version: str
    invariant_version: str
    recorded_time: str

    def __post_init__(self) -> None:
        overlap = set(self.scored_candidate_ids) & set(self.excluded_candidate_ids)
        if overlap:
            raise ValueError(f"candidates both scored and excluded: {sorted(overlap)}")


def _residual_scores(assessments: Sequence[AdmissibilityAssessment],
                     contexts: Mapping[str, InvariantContext],
                     model: RankingModel) -> dict[str, float]:
    """Score survivors with the V5.3 model.

    The guard is the architecture, not a comment: this function raises on a
    candidate that is not invariant-valid, so the eleven forbidden overrides
    cannot reach a weight even if a future caller forgets to filter.
    """
    scores: dict[str, float] = {}
    for assessment in assessments:
        if not assessment.invariant_valid:
            blocking = (list(assessment.forbidden_overrides) or
                        list(assessment.material_violations))
            raise ValueError(
                f"{assessment.candidate_id}: a learned score may never be "
                f"applied to a candidate with {blocking}")
        ctx = contexts[assessment.candidate_id]
        features = ranking.extract_features(
            ctx.candidate, ctx.document, ctx.lattice, ctx.selected,
            ctx.layout_annotation)
        scores[assessment.candidate_id] = model.score(assessment.terminal_state,
                                                      features)
    return scores


def residual_rank(candidates: Iterable[ExtractionCandidate],
                  document: NormalizedDocument,
                  model: RankingModel | None = None, *,
                  layout_annotation: Any = None,
                  language: str | None = None,
                  document_date: str | None = None) -> ResidualRanking:
    """Assess every candidate, then rank only the invariant-valid survivors.

    The order of operations is the contract.  Invariants decide admissibility;
    the V5.3 model is consulted afterwards and only to break ties among
    candidates that already passed — sentence versus expanded context, two
    complete propositions, two defensible attribution boundaries, two
    layout-consistent interpretations.  A candidate that fails a material
    invariant never enters :func:`_residual_scores`, so no weight vector can
    rescue it.
    """
    ranker = model if model is not None else RankingModel()
    contexts: dict[str, InvariantContext] = {}
    assessments: list[AdmissibilityAssessment] = []
    for candidate in candidates:
        ctx = build_context(candidate, document, layout_annotation,
                            language=language, document_date=document_date)
        contexts[candidate.candidate_id] = ctx
        vector = compute_invariants(ctx)
        terminal_state, rule = derive_terminal_state(vector)
        overrides = forbidden_overrides_present(vector)
        assessments.append(AdmissibilityAssessment(
            stable_id("v5-4-assessment", candidate.candidate_id, terminal_state),
            candidate.candidate_id, document.document_id, terminal_state, rule,
            vector, vector.material_violations(), vector.recoverable_violations(),
            vector.unresolved(), overrides, is_invariant_valid(vector),
            ctx.selected.level if ctx.selected else None,
            (ctx.selected.span_start, ctx.selected.span_end) if ctx.selected else None,
            "residual ranking population member", ctx.lattice.lattice_id,
            INVARIANT_VERSION, now_utc()))

    survivors = [item for item in assessments if item.invariant_valid]
    removed = [item for item in assessments if not item.invariant_valid]
    scores = _residual_scores(survivors, contexts, ranker)

    order = {state: index for index, state in enumerate(TERMINAL_STATES)}
    ranked = sorted(survivors,
                    key=lambda item: (-scores[item.candidate_id],
                                      order[item.terminal_state],
                                      item.candidate_id))
    return ResidualRanking(
        stable_id("v5-4-residual-ranking", document.document_id,
                  *(item.candidate_id for item in assessments)),
        document.document_id, len(assessments),
        tuple(item.candidate_id for item in ranked),
        tuple(item.candidate_id for item in removed),
        tuple({"candidate_id": item.candidate_id,
               "terminal_state": item.terminal_state,
               "residual_score": scores[item.candidate_id],
               "selected_level": item.selected_level,
               "unresolved": list(item.unresolved)} for item in ranked),
        tuple({"candidate_id": item.candidate_id,
               "terminal_state": item.terminal_state,
               "forbidden_overrides": list(item.forbidden_overrides),
               "material_violations": list(item.material_violations),
               "reason": item.rationale} for item in removed),
        RESIDUAL_RANKER_ROLE, ranker.version, INVARIANT_VERSION, now_utc())


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------

def invariant_failure_distribution(
        assessments: Iterable[AdmissibilityAssessment | Mapping[str, Any]]
        ) -> dict[str, dict[str, int]]:
    """How often each invariant was VIOLATED or UNRESOLVED across a population."""
    counts = {name: {VIOLATED: 0, UNRESOLVED: 0} for name in INVARIANT_NAMES}
    for item in assessments:
        vector = (item.get("vector") if isinstance(item, Mapping)
                  else getattr(item, "vector", None))
        states = (vector.get("states") if isinstance(vector, Mapping)
                  else getattr(vector, "states", None)) or {}
        for name, state in states.items():
            if name in counts and state in counts[name]:
                counts[name][state] += 1
    return counts


def assessment_report(assessments: Iterable[AdmissibilityAssessment]
                      ) -> dict[str, Any]:
    """Population summary a freeze manifest can hash."""
    items = list(assessments)
    states: dict[str, int] = {}
    overrides: dict[str, int] = {}
    for item in items:
        states[item.terminal_state] = states.get(item.terminal_state, 0) + 1
        for name in item.forbidden_overrides:
            overrides[name] = overrides.get(name, 0) + 1
    total = max(1, len(items))
    return {
        "assessments": len(items),
        "terminal_state_counts": states,
        "invariant_valid": sum(1 for item in items if item.invariant_valid),
        "forbidden_override_counts": overrides,
        "invariant_failure_distribution": invariant_failure_distribution(items),
        "acceptance_rate": round(states.get("ACCEPTED_CANDIDATE", 0) / total, 4),
        "quarantine_rate": round(states.get("QUARANTINED", 0) / total, 4),
        "rejection_rate": round(states.get("REJECTED", 0) / total, 4),
        "invariant_version": INVARIANT_VERSION,
    }
