"""V5.8.1 — which analyses deserve to be compared at all (V581-D46).

Measurement established the problem precisely.  Of 2,357 generated analyses the
constraint layer rejected four, one of which violated a production-visible
invariant; 766 analyses that do violate one reached selection as ordinary
competitors.  Compatible-candidate survival of 1.0000 said the layer discards
nothing correct, and nothing at all about whether it discards anything wrong.

So this module answers a different question from the hard constraints.  They ask
"is this analysis internally contradictory".  This asks "is this analysis a
structurally possible reading of this span" -- and it answers using only what
production can see.

The rule that governs everything here: an analysis is never inadmissible because
it disagrees with the reference.  A different but supportable reading is
evidence, not noise, and the 232 alternative readings measured in this lattice
are the population this layer exists to protect.  Where two readings are both
supportable the verdict is MATERIAL_AMBIGUITY, not a forced winner.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from . import clause_identity as CI
from . import enclosures as EN

#: A role span wider than this fraction of the unit is not a constituent of it.
SPAN_DOMINANCE_LIMIT = 0.60
#: Characters that end an independent proposition.  A colon is deliberately NOT
#: one: it introduces quoted or enumerated material rather than closing a
#: proposition, and treating it as a boundary destroyed the only compatible
#: analyses of "g) al comma 13, le parole: «...» sono sostituite ...", whose
#: subject legitimately spans the colon.
CLAUSE_TERMINATORS = ".;!?"

#: Primary violation classes.  Mutually exclusive for accounting: an analysis is
#: filed under the first that applies, in this order.  Secondary violations are
#: recorded separately and may overlap.
PRIMARY_VIOLATIONS: tuple[str, ...] = (
    "CONTRADICTORY_SLOT_BINDING",
    "OVERSIZED_NONCONSTITUENT_SPAN",
    "CROSS_CLAUSE_ROLE_PAIRING",
    "UNSUPPORTED_ROLE_FILLER",
    "SEMANTICALLY_INCOMPLETE_SPAN",
    "SPAN_CROSSES_ENCLOSURE_BOUNDARY",
    "INVALID_SOURCE_LINEAGE",
)

VERDICTS: tuple[str, ...] = (
    "ADMISSIBLE",
    "ADMISSIBLE_ALTERNATIVE",
    "MATERIAL_AMBIGUITY",
    "INADMISSIBLE",
    "CONSTRUCTION_DEFECT",
)


class AdmissibilityError(ValueError):
    """A verdict or violation outside the declared vocabulary."""


@dataclass
class AnalysisAdmissibilityAssessment:
    """Why an analysis may or may not compete, with its evidence."""

    analysis_id: str
    unit_id: str
    subject_candidate_id: str | None = None
    predicate_candidate_id: str | None = None
    span_plausibility_state: str = "NOT_ASSESSED"
    clause_coherence_state: str = "NOT_ASSESSED"
    role_compatibility_state: str = "NOT_ASSESSED"
    required_slot_state: str = "NOT_ASSESSED"
    primary_violation: str | None = None
    secondary_violations: tuple[str, ...] = ()
    admissibility_reasons: tuple[str, ...] = ()
    ambiguity_state: str = "NOT_ASSESSED"
    verdict: str = "ADMISSIBLE"
    #: Typed clause identity (V581 Campaign A).  Recorded on every assessment,
    #: not only on rejections, so that a licence family which begins over-firing
    #: can be found by counting survivors rather than by re-deriving them.
    subject_clause_id: str | None = None
    predicate_clause_id: str | None = None
    clause_pairing_verdict: str = "NOT_ASSESSED"
    cross_clause_licence: str | None = None
    cross_clause_licence_type: str | None = None
    cross_clause_licence_evidence: str | None = None
    #: Typed enclosure semantics (V581 Campaign C).
    improper_enclosure_ids: tuple[str, ...] = ()
    improper_enclosure_roles: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.verdict not in VERDICTS:
            raise AdmissibilityError(f"unknown verdict {self.verdict!r}")
        if self.primary_violation is not None \
                and self.primary_violation not in PRIMARY_VIOLATIONS:
            raise AdmissibilityError(
                f"unknown primary violation {self.primary_violation!r}")
        if self.verdict == "INADMISSIBLE" and self.primary_violation is None:
            raise AdmissibilityError(
                "an inadmissible analysis must name the invariant it violates")

    @property
    def competes(self) -> bool:
        """May this analysis reach selection?"""
        return self.verdict in ("ADMISSIBLE", "ADMISSIBLE_ALTERNATIVE",
                                "MATERIAL_AMBIGUITY")


#: Closed-class finite verbs.  Written independently of both roles_v2's
#: language-specific detectors and the evaluation ruler's list: this module must
#: not import either, or a defect in one becomes invisible to the other.
#: Modals and auxiliaries only -- no open-class guessing.
FINITE_CLOSED_CLASS = frozenset({
    "muss", "müssen", "musst", "soll", "sollen", "kann", "können", "darf",
    "dürfen", "ist", "sind", "war", "waren", "wird", "werden", "hat", "haben",
    "hatte", "hatten",
    "debe", "deben", "puede", "pueden", "podrá", "es", "son", "está", "están",
    "deve", "devono", "può", "possono", "è", "sono", "era", "erano",
    "doit", "doivent", "peut", "peuvent", "est", "sont", "était", "étaient",
    "must", "shall", "should", "may", "might", "can", "could", "is", "are",
    "was", "were", "has", "have", "had",
})


#: Relative pronouns and subordinators.  A finite verb after one of these, inside
#: a subject span, belongs to an EMBEDDED clause and says nothing about the
#: matrix predicate: "The officer who was appointed yesterday will report" has a
#: perfectly good subject containing "was".
#: Relativisers that are also definite articles; position decides which.
AMBIGUOUS_RELATIVISERS = frozenset({"der", "die", "das", "que", "che"})

RELATIVISERS = frozenset({
    "who", "whom", "whose", "which", "that",
    "der", "die", "das", "welcher", "welche", "welches", "dessen", "deren",
    "que", "qui", "dont", "lequel", "laquelle",
    "che", "cui", "quale",
    "который", "которая", "которое", "которые", "что",
    "الذي", "التي", "الذين", "اللاتي",
})


def _subject_contains_a_finite_verb(candidate: Any, text: str) -> bool:
    """A subject constituent does not contain its clause's own finite verb.

    Independent adjudication found 210 analyses reaching selection whose subject
    span swallows the verb it is the subject of -- "Sie oder er muss über die für
    die Erfüllung ..." offered as a subject.  Width alone did not catch these,
    because whether a span is too wide depends on what alternatives exist; this
    is a statement about content, and it holds at any width.
    """
    if candidate is None or getattr(candidate, "span", None) is None:
        return False
    body = text[candidate.span[0]:candidate.span[1]]
    # Only the MATRIX predicate disqualifies a subject.  Once a relativiser has
    # been seen, everything after it belongs to an embedded clause, so a finite
    # verb there is expected rather than disqualifying.  Without this the rule
    # rejects every subject carrying a relative clause -- a large and entirely
    # lawful family.
    embedded = False
    for position, word in enumerate(body.split()):
        token = word.strip(".,;:()«»\u201e\u201c\u201d\"'").casefold()
        # der/die/das are relative pronouns AND definite articles.  Treating
        # them as relativisers unconditionally set embedded=True on the very
        # first token of almost every German subject and silently disabled this
        # rule -- caught by its own regression fixture.  In article position
        # (span-initial) they are articles; later they may relativise.
        if token in AMBIGUOUS_RELATIVISERS and position == 0:
            continue
        if token in RELATIVISERS:
            embedded = True
            continue
        if not embedded and token in FINITE_CLOSED_CLASS:
            return True
    return False


# V581-D46F PREDICATE-ROLE COMPATIBILITY — IMPLEMENTED, MEASURED, REVERTED.
#
# The rule lived here, as Interpretation F directed, and it failed on three
# independent grounds.  Four of six D46 gates fell under BOTH rulers, so the
# failure is production's and not an artifact of the evaluator.  And 115 of its
# 393 rejections rested on classifications the independent adjudicator declined
# to resolve at all -- which violates the acceptance gate "ambiguous controls
# rejected solely by type = 0" and is the absence-of-evidence failure in a new
# guise: production's classifier is more confident than the evidence supports.
#
# Rule-level against the independent adjudicator: precision 264/278 = 0.9496
# (gate 0.95), recall 264/749 = 0.3525.  The dead path is deleted rather than
# bypassed, per Interpretation F §13.  Evidence: 267_predicate_role_ruler/.
#
#: Opening quotation marks.  A quoted phrase is one constituent however long --
#: "le parole: «Entro lo stesso termine ...»" is a single named object, and two
#: units whose reference subject is exactly such a phrase were destroyed by a
#: flat dominance limit before this exemption existed.
QUOTE_CHARACTERS = "\u201e\u201c\u201d\u00ab\u00bb\u2018\u2019\"'"


def _is_quoted_constituent(candidate: Any, text: str) -> bool:
    if getattr(candidate, "span", None) is None:
        return False
    body = text[candidate.span[0]:candidate.span[1]]
    return any(q in body for q in QUOTE_CHARACTERS)


def _role_violations(name: str, candidate: Any, text: str,
                     shorter_alternative: bool = True) -> list[str]:
    if candidate is None or getattr(candidate, "span", None) is None:
        return []
    start, stop = candidate.span
    found: list[str] = []
    if start < 0 or stop > len(text) or stop <= start:
        found.append("INVALID_SOURCE_LINEAGE")
        return found
    if not (getattr(candidate, "text", "") or "").strip():
        found.append("UNSUPPORTED_ROLE_FILLER")
    # Dominance alone is not evidence.  A span is a non-constituent only if the
    # lattice offers a SHORTER plausible candidate for the same role: without
    # that comparison the rule just punishes units whose subject really is most
    # of the sentence.  Quoted material is one constituent at any length.
    if ((stop - start) > SPAN_DOMINANCE_LIMIT * max(len(text), 1)
            and shorter_alternative
            and not _is_quoted_constituent(candidate, text)):
        found.append("OVERSIZED_NONCONSTITUENT_SPAN")
    interior = text[start:max(stop - 1, start)]
    if any(terminator in interior for terminator in CLAUSE_TERMINATORS):
        found.append("CROSS_CLAUSE_ROLE_PAIRING")
    return found


def assess(analysis: Any, text: str, *, unit_id: str = "",
           alternative: bool = False, shorter_subject: bool = True,
           shorter_head: bool = True,
           clause_context: Any = None,
           enclosure_context: Any = None) -> AnalysisAdmissibilityAssessment:
    """Judge one analysis on production-visible invariants alone.

    `alternative` marks a reading already known to be supportable; it never
    turns an invariant violation into an admission, it only distinguishes
    ADMISSIBLE from ADMISSIBLE_ALTERNATIVE for reporting.

    `clause_context` is the unit's typed clause structure.  It is passed in
    rather than rebuilt per analysis because segmentation is a property of the
    text, and a lattice of thirty analyses over one span must not segment it
    thirty times.
    """
    subject = getattr(analysis, "subject", None)
    head = getattr(analysis, "predicate_head", None)

    found: list[str] = []
    found += _role_violations("subject", subject, text,
                              shorter_alternative=shorter_subject)
    found += _role_violations("predicate_head", head, text,
                              shorter_alternative=shorter_head)

    if _subject_contains_a_finite_verb(subject, text):
        found.append("UNSUPPORTED_ROLE_FILLER")

    # V581 Campaign A.  The terminator scan in _role_violations asks whether ONE
    # span straddles a boundary; it cannot see a subject and a predicate that sit
    # in different clauses while each stays wholly inside its own.  232 analyses
    # reached selection that way.  A pairing across a clause boundary is refused
    # only when no named licence permits it -- relative attachment, control,
    # raising, reported speech, inherited governing subject, typed coordination
    # and structural continuation are all lawful, and rejecting them would close
    # the removal gates by breaking the preservation gates.
    pairing = CI.assess_pairing(subject, head, text, context=clause_context)
    if pairing["verdict"] == "UNLICENSED_CROSS_CLAUSE":
        found.append("CROSS_CLAUSE_ROLE_PAIRING")

    # A role span that begins inside a parenthesis and ends outside it is a
    # slice taken across a boundary, not a constituent.  The test is improper
    # INTERSECTION, never quote-mark parity: the French apostrophe is a closing
    # quote character, an enumerator carries a parenthesis that never opened,
    # and an opener with no closer means the extract is truncated rather than
    # malformed.  Where a boundary is not established nothing is concluded.
    enclosure_state = {}
    for role_candidate in (subject, head):
        if role_candidate is None or getattr(role_candidate, "span", None) is None:
            continue
        state = EN.assess_span(tuple(role_candidate.span), text,
                               enclosures=enclosure_context)
        if state["verdict"] == "IMPROPERLY_INTERSECTS_ENCLOSURE":
            found.append("SPAN_CROSSES_ENCLOSURE_BOUNDARY")
            enclosure_state = state

    if (subject is not None and head is not None
            and getattr(subject, "span", None) and getattr(head, "span", None)):
        s0, s1 = subject.span
        h0, h1 = head.span
        if s0 < h1 and h0 < s1:
            # One stretch of text cannot be both the subject and its own verb.
            found.append("CONTRADICTORY_SLOT_BINDING")

    primary = next((v for v in PRIMARY_VIOLATIONS if v in found), None)
    secondary = tuple(sorted({v for v in found if v != primary}))

    if primary is not None:
        verdict = "INADMISSIBLE"
    elif alternative:
        verdict = "ADMISSIBLE_ALTERNATIVE"
    else:
        verdict = "ADMISSIBLE"

    return AnalysisAdmissibilityAssessment(
        analysis_id=getattr(analysis, "analysis_id", ""),
        unit_id=unit_id,
        subject_candidate_id=getattr(subject, "candidate_id", None),
        predicate_candidate_id=getattr(head, "candidate_id", None),
        span_plausibility_state=(
            "SPAN_IMPLAUSIBLE" if "OVERSIZED_NONCONSTITUENT_SPAN" in found
            else "SPAN_PLAUSIBLE"),
        clause_coherence_state=(
            "CROSSES_CLAUSE_BOUNDARY" if "CROSS_CLAUSE_ROLE_PAIRING" in found
            else "WITHIN_ONE_CLAUSE"),
        role_compatibility_state=(
            "ROLES_CONTRADICTORY" if "CONTRADICTORY_SLOT_BINDING" in found
            else "ROLES_COMPATIBLE"),
        required_slot_state="NOT_ASSESSED",
        primary_violation=primary,
        secondary_violations=secondary,
        admissibility_reasons=tuple(sorted(set(found))) or ("NO_VIOLATION",),
        ambiguity_state="MATERIAL_AMBIGUITY_NOT_ASSESSED",
        verdict=verdict,
        subject_clause_id=pairing["subject_clause_id"],
        predicate_clause_id=pairing["predicate_clause_id"],
        clause_pairing_verdict=pairing["verdict"],
        cross_clause_licence=pairing["cross_clause_licence"],
        cross_clause_licence_type=pairing["cross_clause_licence_type"],
        cross_clause_licence_evidence=pairing["cross_clause_licence_evidence"],
        improper_enclosure_ids=tuple(
            enclosure_state.get("improper_enclosure_ids", ())),
        improper_enclosure_roles=tuple(
            enclosure_state.get("quoted_content_roles", ())),
    )


def admissible_analyses(analyses: list[Any], text: str, *, unit_id: str = ""
                        ) -> tuple[list[Any], list[AnalysisAdmissibilityAssessment]]:
    """Split a lattice into what may compete and why the rest may not."""
    def _widths(role: str) -> list[int]:
        out = []
        for a in analyses:
            c = getattr(a, role, None)
            if c is not None and getattr(c, "span", None):
                out.append(c.span[1] - c.span[0])
        return out

    subject_widths, head_widths = _widths("subject"), _widths("predicate_head")

    # Segment the span once for the whole lattice.
    clause_context = CI.build_clause_context(text, region_id=unit_id)
    enclosure_context = EN.build_enclosures(text, region_id=unit_id)

    def _has_shorter(role: str, analysis: Any, widths: list[int]) -> bool:
        c = getattr(analysis, role, None)
        if c is None or getattr(c, "span", None) is None:
            return False
        width = c.span[1] - c.span[0]
        return any(w < width for w in widths)

    assessments = [
        assess(a, text, unit_id=unit_id,
               shorter_subject=_has_shorter("subject", a, subject_widths),
               shorter_head=_has_shorter("predicate_head", a, head_widths),
               clause_context=clause_context,
               enclosure_context=enclosure_context)
        for a in analyses]
    competing = [a for a, verdict in zip(analyses, assessments)
                 if verdict.competes]
    return competing, assessments


__all__ = [
    "SPAN_DOMINANCE_LIMIT", "CLAUSE_TERMINATORS", "PRIMARY_VIOLATIONS",
    "VERDICTS", "AdmissibilityError", "AnalysisAdmissibilityAssessment",
    "assess", "admissible_analyses",
]
