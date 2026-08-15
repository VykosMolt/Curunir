"""Section 5 — the hierarchical claim-support decision architecture.

V5.3's claim-support classifier scored 0.2154 on clean evidence.  Its dominant
error was diagnosing a *specific* mismatch — usually ``WRONG_ENTITY`` — where
the reviewer majority found the broader and logically prior failure:

    production said WRONG_ENTITY 40 times
    reviewers said NOT_SUPPORTED 24, PARTIAL_SUPPORT 8, FULL_SUPPORT 5

The defect is architectural, not a threshold.  A flat classifier is free to
reach for ``WRONG_ENTITY`` the moment the evidence names a different actor,
without ever asking whether the evidence addresses the claimed proposition at
all.  "Agency X attended a conference" does not name the wrong actor in a claim
about Agency X operating system Y — it says nothing about operating anything.

This module replaces the flat decision with a nine-stage hierarchy in which
*proposition addressability* is evaluated first and a specific mismatch is
reachable only through it.  Every terminal class is derivable from a structured
support vector, and the first failing stage is recorded, so a wrong answer can
be attributed to a stage rather than to a mood.

The V5.2 lifecycle gate is preserved intact and runs inside stage 7: a plan may
never partially support an implementation claim, whatever the other stages say.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from ..v5_1.models import Record, now_utc, stable_id
from ..v5_2 import claim_support as v5_2_support
from ..v5_2 import lifecycle

# ---------------------------------------------------------------------------
# Section 5.1 — the support classes.  None may be removed.
# ---------------------------------------------------------------------------

SUPPORT_CLASSES: tuple[str, ...] = (
    "FULL_SUPPORT",
    "PARTIAL_SUPPORT",
    "QUALIFIED_SUPPORT",
    "CONTEXT_DEPENDENT_SUPPORT",
    "CONTRADICTED",
    "NOT_SUPPORTED",
    "WRONG_SCOPE",
    "WRONG_TIME",
    "WRONG_ENTITY",
    "WRONG_MODALITY",
    "WRONG_POLARITY",
    "INFERENCE_ONLY",
)

#: Classes that assert the evidence carries the claim.
AFFIRMATIVE_SUPPORT = frozenset({
    "FULL_SUPPORT", "PARTIAL_SUPPORT", "QUALIFIED_SUPPORT",
    "CONTEXT_DEPENDENT_SUPPORT",
})

#: Classes naming a specific dimensional mismatch.  Reachable only when the
#: evidence addresses the same proposition family (Section 5.4).
SPECIFIC_MISMATCH = frozenset({
    "WRONG_ENTITY", "WRONG_SCOPE", "WRONG_TIME", "WRONG_MODALITY",
    "WRONG_POLARITY",
})

# ---------------------------------------------------------------------------
# Section 5.2 — the stages, in evaluation order.
# ---------------------------------------------------------------------------

STAGES: tuple[str, ...] = (
    "S1_PROPOSITION_ADDRESSABILITY",
    "S2_SUBJECT_AND_OBJECT_ALIGNMENT",
    "S3_PREDICATE_ALIGNMENT",
    "S4_SCOPE_ALIGNMENT",
    "S5_TEMPORAL_ALIGNMENT",
    "S6_POLARITY_ALIGNMENT",
    "S7_MODALITY_AND_LIFECYCLE_ALIGNMENT",
    "S8_SUPPORT_SUFFICIENCY",
    "S9_CONTRADICTION_AND_INFERENCE",
)

#: The class a stage failure resolves to.  Stage 1 is deliberately the broad
#: class: an unaddressed proposition is not a wrong entity.
STAGE_FAILURE_CLASS: Mapping[str, str] = {
    "S1_PROPOSITION_ADDRESSABILITY": "NOT_SUPPORTED",
    "S2_SUBJECT_AND_OBJECT_ALIGNMENT": "WRONG_ENTITY",
    "S3_PREDICATE_ALIGNMENT": "NOT_SUPPORTED",
    "S4_SCOPE_ALIGNMENT": "WRONG_SCOPE",
    "S5_TEMPORAL_ALIGNMENT": "WRONG_TIME",
    "S6_POLARITY_ALIGNMENT": "WRONG_POLARITY",
    "S7_MODALITY_AND_LIFECYCLE_ALIGNMENT": "WRONG_MODALITY",
}

# ---------------------------------------------------------------------------
# Predicate families.  Section 5.2 stage 3 names the actions a claim can make;
# two predicates in the same family address the same proposition dimension,
# two in different families do not.
# ---------------------------------------------------------------------------

PREDICATE_FAMILIES: Mapping[str, tuple[str, ...]] = {
    # NB: the copulas are deliberately absent.  "is"/"are"/"was"/"were" appear in
    # almost every sentence, so including them made EXISTS the family of record
    # for arbitrary text and produced different families for a claim and an
    # evidence item that were the same sentence.
    "EXISTS": ("exist", "existiert", "vorhanden", "besteht"),
    "FUNDS": ("fund", "finance", "financing", "grant", "subsid", "allocat", "budget",
              "finanzier", "förder", "foerder", "mittel", "zuschuss", "finance"),
    "DEVELOPS": ("develop", "build", "construct", "design", "engineer", "entwickel",
                 "bau", "konstruier", "entwurf"),
    "PROCURES": ("procure", "tender", "solicit", "call for tender", "award",
                 "beschaff", "ausschreib", "vergab", "vergibt", "zuschlag"),
    "OPERATES": ("operat", "run", "administer", "manage", "maintain", "betreib",
                 "betrieb", "verwalt", "unterhalt"),
    "DEPLOYS": ("deploy", "roll out", "rollout", "install", "field", "commission",
                "einsatz", "einführ", "einfuehr", "installier", "inbetriebnahm"),
    "REGULATES": ("regulat", "govern", "legislat", "mandate", "require", "prohibit",
                  "regel", "regulier", "vorschreib", "untersag", "verbiet"),
    "CAUSES": ("caus", "result in", "lead to", "trigger", "produce",
               "verursach", "führt zu", "fuehrt zu", "auslös", "ausloes"),
    "REVISES": ("revis", "amend", "update", "correct", "supersed", "replace",
                "änder", "aender", "berichtig", "ersetz", "aktualisier"),
    "CANCELS": ("cancel", "terminat", "withdraw", "abandon", "discontinu", "retire",
                "storn", "einstell", "zurückzieh", "zurueckzieh", "beend"),
    "REPORTS": ("report", "state", "announc", "declar", "publish", "note",
                "berichte", "erklär", "erklaer", "verkünd", "verkuend", "meld"),
    "ATTENDS": ("attend", "participat", "join", "visit", "meet",
                "teilnahm", "teilnehm", "besuch", "treffen"),
}

_STOPWORDS = frozenset("""
a an the of to in on for by with at from and or as is are was were be been being
that this these those its his her their our your it they he she we you i not no
der die das den dem des ein eine einer eines und oder als ist sind war waren
für fuer von mit auf bei aus zu im am nicht kein keine sich auch nach
""".split())

#: Institutional and legal *frame* vocabulary.  These words identify the genre
#: of a document, not the proposition inside it.  In an EU-regulatory corpus
#: "European", "Regulation", "Parliament", "Council" and "Article" appear in
#: nearly every document, so counting them as shared propositional content makes
#: two unrelated instruments look like they address one another — which is how a
#: claim about a children's participation platform came to be scored a wrong
#: entity against evidence concerning product-safety accreditation.
#:
#: This is the same defect class as the negation-anywhere polarity test: a
#: similarity signal computed over vocabulary that is constant across the
#: corpus.  Excluded from the addressability computation only; every other stage
#: still sees these terms.
#: Scope note: *document-structure* vocabulary only.  Polity and subject-matter
#: words are deliberately absent — "European" is frame vocabulary in "European
#: Parliament" and load-bearing content in "European railway network", and a
#: blanket exclusion suppresses real signal in the second case.  Only words that
#: can never discriminate one proposition from another belong here.
FRAME_TERMS = frozenset("""
regulation regulations directive directives decision decisions
article articles paragraph paragraphs subparagraph annex annexes chapter
chapters title section sections provision provisions referred accordance
pursuant purpose purposes respect shall should whereas thereof therein hereby
implementing delegated official journal text entry apply applies
applicable laid down set out adopted amended amending repealed
verordnung verordnungen richtlinie beschluss artikel absatz anhang
kapitel titel abschnitt bestimmung gemäß gemaess genannt festgelegt
""".split())


def _propositional(terms: set[str]) -> set[str]:
    """Content terms with institutional frame vocabulary removed."""
    return {t for t in terms if t not in FRAME_TERMS}


def _terms(text: str) -> set[str]:
    """Content terms, casefolded, with a crude suffix trim for DE/EN."""
    words = re.findall(r"[\wÀ-ɏ'-]+", (text or "").casefold())
    out: set[str] = set()
    for word in words:
        if word in _STOPWORDS or len(word) < 3:
            continue
        out.add(word)
        for suffix in ("ungen", "ung", "en", "er", "es", "s", "ed", "ing"):
            if len(word) > len(suffix) + 3 and word.endswith(suffix):
                out.add(word[: -len(suffix)])
                break
    return out


def predicate_family(text: str) -> str | None:
    """The predicate family a phrase belongs to, or None if unrecognised."""
    folded = (text or "").casefold()
    best: tuple[int, str] | None = None
    for family, markers in PREDICATE_FAMILIES.items():
        for marker in markers:
            if marker in folded:
                score = len(marker)
                if best is None or score > best[0]:
                    best = (score, family)
    return best[1] if best else None


# ---------------------------------------------------------------------------
# Section 5.3 — the support vector.
# ---------------------------------------------------------------------------

ALIGNMENT_VALUES = ("ALIGNED", "MISALIGNED", "UNRESOLVED", "NOT_APPLICABLE")


@dataclass(frozen=True)
class SupportVector(Record):
    """The structured representation every support class must derive from."""

    addresses_proposition: bool
    entity_alignment: str
    predicate_alignment: str
    scope_alignment: str
    time_alignment: str
    polarity_alignment: str
    modality_alignment: str
    lifecycle_alignment: str
    attribution_alignment: str
    dependence_limitations: tuple[str, ...]
    counterevidence: tuple[str, ...]
    support_completeness: str

    def __post_init__(self) -> None:
        for name in ("entity_alignment", "predicate_alignment", "scope_alignment",
                     "time_alignment", "polarity_alignment", "modality_alignment",
                     "lifecycle_alignment", "attribution_alignment"):
            value = getattr(self, name)
            if value not in ALIGNMENT_VALUES:
                raise ValueError(f"{name} must be one of {ALIGNMENT_VALUES}: {value!r}")
        if self.support_completeness not in (
                "COMPLETE", "PARTIAL", "QUALIFIED", "CONTEXT_DEPENDENT",
                "INFERRED", "ABSENT", "CONTRADICTED"):
            raise ValueError(f"unknown support completeness: {self.support_completeness}")


@dataclass(frozen=True)
class HierarchicalSupport(Record):
    """A support assessment carrying its whole derivation."""

    support_id: str
    claim_id: str
    support_class: str
    vector: SupportVector
    decision_path: tuple[str, ...]
    first_failing_stage: str | None
    stage_findings: tuple[tuple[str, str, str], ...]   # (stage, outcome, detail)
    evidence_ids: tuple[str, ...]
    alternatives_rejected: tuple[tuple[str, str], ...]  # (class, why not)
    allowed_report_wording: tuple[str, ...]
    prohibited_stronger_wording: tuple[str, ...]
    lifecycle_verdict: str
    critical_error: str | None
    recorded_time: str

    def __post_init__(self) -> None:
        if self.support_class not in SUPPORT_CLASSES:
            raise ValueError(f"unknown support class: {self.support_class}")
        if self.critical_error and self.support_class in AFFIRMATIVE_SUPPORT:
            raise ValueError(
                "a lifecycle critical error may never resolve to affirmative support; "
                "this is the plan-as-implementation defect")
        if self.support_class in SPECIFIC_MISMATCH and not self.vector.addresses_proposition:
            raise ValueError(
                f"{self.support_class} is a specific mismatch and requires the evidence "
                "to address the same proposition family; unaddressed evidence is "
                "NOT_SUPPORTED (Section 5.4)")
        if self.support_class in AFFIRMATIVE_SUPPORT and self.first_failing_stage:
            raise ValueError(
                "affirmative support requires every alignment stage to pass; "
                f"stage {self.first_failing_stage} failed")


# ---------------------------------------------------------------------------
# Proposition signatures
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Proposition:
    """The dimensions a claim asserts, extracted once and compared per stage."""

    text: str
    entity: str
    predicate: str
    obj: str
    scope: tuple[str, ...] = ()
    valid_time: tuple[str | None, str | None] = (None, None)
    polarity: str = "POSITIVE"
    modality: str = "ASSERTED"
    lifecycle_state: str = "UNKNOWN"
    attribution: str | None = None

    @property
    def family(self) -> str | None:
        return predicate_family(self.predicate) or predicate_family(self.text)

    @property
    def entity_terms(self) -> set[str]:
        return _terms(self.entity)

    @property
    def object_terms(self) -> set[str]:
        return _terms(self.obj)


def proposition_from(obj: Any) -> Proposition:
    """Build a Proposition from a claim-like mapping or record."""
    get = (obj.get if isinstance(obj, Mapping)
           else lambda key, default=None, o=obj: getattr(o, key, default))
    text = str(get("proposition") or get("normalized_statement") or get("text") or "")
    return Proposition(
        text=text,
        entity=str(get("entity") or ""),
        predicate=str(get("predicate") or text),
        obj=str(get("object") or get("obj") or get("target") or ""),
        scope=tuple(get("scope") or ()),
        valid_time=tuple(get("valid_time") or (None, None)),
        polarity=str(get("polarity") or "POSITIVE"),
        modality=str(get("modality") or "ASSERTED"),
        lifecycle_state=lifecycle.normalize_state(str(get("lifecycle_state") or "UNKNOWN")),
        attribution=get("attribution"),
    )


# ---------------------------------------------------------------------------
# Section 5.2 stage implementations.  Each returns (outcome, detail).
# ---------------------------------------------------------------------------

#: How much of the claim's predicate/object dimension the evidence must engage
#: before a *specific* mismatch becomes reachable.  Calibrated on exposed V5.3
#: development cases; a threshold, never a semantic definition.
ADDRESSABILITY_THRESHOLD = 0.34


def addressability(claim: Proposition, evidence: Proposition) -> tuple[float, str]:
    """Does the evidence engage the claim's proposition dimension at all?

    Engagement is predicate-family agreement or object-term overlap.  Naming the
    same entity is deliberately *not* enough: "Agency X attended a conference"
    shares an entity with "Agency X operates system Y" and addresses nothing.
    """
    claim_family, evidence_family = claim.family, evidence.family
    family_match = bool(claim_family) and claim_family == evidence_family
    evidence_terms = _propositional(_terms(evidence.text))
    objects = _propositional(claim.object_terms)
    overlap = (len(objects & evidence_terms) / len(objects)) if objects else 0.0

    if claim_family is None:
        # Auto-extracted claims frequently carry a degenerate predicate ("set to",
        # "is") that no family matches.  Falling back to propositional content
        # overlap keeps such claims adjudicable instead of routing every one of
        # them to NOT_SUPPORTED, which is over-rejection wearing a hierarchy.
        content = _propositional(_terms(claim.text))
        carried = (len(content & evidence_terms) / len(content)) if content else 0.0
        score = max(0.4 * overlap, 0.75 * carried)
        return score, (f"claim predicate family unrecognised; propositional content "
                       f"overlap {carried:.2f} (frame vocabulary excluded), object "
                       f"overlap {overlap:.2f}")

    score = (0.6 if family_match else 0.0) + 0.4 * overlap
    if family_match and overlap:
        detail = f"predicate family {claim_family} and object overlap {overlap:.2f}"
    elif family_match:
        detail = f"predicate family {claim_family} engaged, no object overlap"
    elif overlap:
        detail = (f"object overlap {overlap:.2f} but evidence predicate family is "
                  f"{evidence_family or 'unrecognised'}, claim family is "
                  f"{claim_family}")
    else:
        detail = ("evidence engages neither the claimed predicate family nor the "
                  "claimed object")
    return score, detail


def family_established(claim: Proposition, evidence: Proposition) -> bool:
    """Do claim and evidence engage the *same proposition family*?

    Section 5.4 licenses a specific mismatch only when "the evidence addresses
    the same proposition family".  Shared topic vocabulary is weaker than that:
    two EU instruments can share a subject area while asserting nothing about
    each other's predicate.  Topic overlap is enough to descend to sufficiency;
    it is not enough to license a dimensional diagnosis such as WRONG_ENTITY or
    WRONG_TIME, because naming a dimension asserts that every *other* dimension
    lined up — and that claim cannot be made when the predicate never matched.
    """
    claim_family = claim.family
    return bool(claim_family) and claim_family == evidence.family


def _stage_1(claim: Proposition, evidence: Proposition) -> tuple[str, str]:
    score, detail = addressability(claim, evidence)
    if score >= ADDRESSABILITY_THRESHOLD:
        return "PASS", f"addressability {score:.2f}: {detail}"
    return "FAIL", (f"addressability {score:.2f} below {ADDRESSABILITY_THRESHOLD}: "
                    f"{detail}")


def _entity_tokens(text: str) -> list[str]:
    """Entity tokens, keeping short discriminators.

    ``_terms`` drops tokens under three characters, which is right for content
    matching and catastrophic for entity matching: "Agency X" and "Agency Z"
    differ *only* in the token it discards, so a bag-of-terms comparison scores
    them identical and a real wrong-actor mismatch reads as full support.
    """
    return [t for t in re.findall(r"[\wÀ-ɏ'-]+", (text or "").casefold())
            if t not in _STOPWORDS]


def _contains_sequence(haystack: Sequence[str], needle: Sequence[str]) -> bool:
    if not needle:
        return False
    for start in range(len(haystack) - len(needle) + 1):
        if list(haystack[start:start + len(needle)]) == list(needle):
            return True
    return False


#: Leading material that upstream span extraction leaves attached to a subject
#: field.  "For transparency and certainty, the European Commission" names the
#: Commission; "Immediately" names nobody.  Matching the whole prefix as if it
#: were an entity mention is what made WRONG_ENTITY over-fire.
_SUBJECT_PREFIX = re.compile(
    r"^\s*(?:"
    r"(?:for|under|following|after|before|during|within|through|by|with|from|in|on|at|"
    r"according to|pursuant to|subject to|as regards|regarding|despite|besides|"
    r"für|fuer|unter|nach|vor|während|waehrend|innerhalb|durch|mit|von|in|an|bei|gemäß|gemaess)"
    r"\b[^,]{0,80},\s*"
    r"|if\b[^,]{0,80},\s*|where\b[^,]{0,80},\s*|when\b[^,]{0,80},\s*"
    # participial openers: "Launched in 2016,", "Adopted as part of X,",
    # "Speaking to reporters,".  Upstream extraction leaves these attached to
    # the subject field, where they read as part of the entity mention.
    r"|(?:[a-z]+(?:ed|ing)\b[^,]{0,80},\s*)"
    r"|falls\b[^,]{0,80},\s*|sofern\b[^,]{0,80},\s*|wenn\b[^,]{0,80},\s*"
    r")+", re.IGNORECASE)

#: A subject phrase that reduces to one of these names no entity at all.
_NON_ENTITY_SUBJECT = re.compile(
    r"^(?:immediately|subsequently|previously|currently|recently|thereafter|meanwhile|"
    r"however|therefore|moreover|additionally|finally|initially|overall|"
    r"sofort|anschließend|anschliessend|derzeit|zuletzt|schließlich|schliesslich)\W*$",
    re.IGNORECASE)

#: A heading glued to the sentence beneath it: "About Galileo: Galileo is ...".
#: The label is layout, not part of the entity mention.
_HEADING_LABEL = re.compile(r"^\s*(?:about|on|re)\s+[^:]{1,40}:\s*", re.IGNORECASE)

#: A subject headed by a demonstrative refers out of the span, not within it.
#: "That right", "This provision", "Such measures" name whatever the previous
#: sentence named; comparing them against an evidence item as if they were
#: entity mentions produces a wrong-actor verdict about an unresolved pronoun.
_DEMONSTRATIVE_HEAD = re.compile(
    r"^(?:that|this|these|those|such|said|the\s+(?:former|latter|same))\b",
    re.IGNORECASE)

#: A subject that is a subordinate or participial fragment rather than a noun
#: phrase: "following the exchanges", "where granting of the authorisation".
#: Upstream extraction produces these when a span begins mid-clause.
_CLAUSAL_FRAGMENT = re.compile(
    r"^(?:following|where|when|whereas|whilst|while|given|pursuant|subject|"
    r"in\s+order|for\s+the\s+purposes?|notwithstanding|having|being|"
    r"nach|sofern|soweit|wenn|falls|gemäß|gemaess)\b", re.IGNORECASE)

#: A subject whose head noun is a bare relational or legal abstraction carries
#: no actor.  "The person whose status ...", "That right ..." are conditions on
#: an actor, not the actor.
_ABSTRACT_HEAD = re.compile(
    r"^(?:the\s+)?(?:right|rights|obligation|obligations|provision|provisions|"
    r"requirement|requirements|measure|measures|condition|conditions|period|"
    r"deadline|procedure|procedures|derogation|exemption|penalty|penalties|"
    r"person|persons|applicant|applicants|holder|holders|party|parties)\b"
    r"(?:\s+(?:whose|which|that|referred|specified|laid|set)\b|\W*$)", re.IGNORECASE)


def entity_head(subject: str) -> str | None:
    """The nominal head of a subject phrase, or None if there isn't one.

    Upstream extraction hands us whole sentence prefixes in the subject field.
    This trims the adverbial and conditional material so entity comparison runs
    on the noun phrase that actually names the actor.
    """
    text = (subject or "").strip()
    if not text:
        return None
    trimmed = _HEADING_LABEL.sub("", text).strip()
    trimmed = _SUBJECT_PREFIX.sub("", trimmed).strip(" ,;:")
    if not trimmed or _NON_ENTITY_SUBJECT.match(trimmed):
        return None
    if _DEMONSTRATIVE_HEAD.match(trimmed):
        return None
    if _CLAUSAL_FRAGMENT.match(trimmed):
        return None
    if _ABSTRACT_HEAD.match(trimmed):
        return None
    return trimmed


def _joined(tokens: Sequence[str]) -> str:
    """Tokens joined without separators, to survive PDF intra-word spacing.

    Real corpus text contains "pe rsons" where the extractor split a word across
    a layout boundary; token-sequence matching fails on it and a genuine full
    support reads as a wrong entity.
    """
    return "".join(tokens)


def _stage_2(claim: Proposition, evidence: Proposition) -> tuple[str, str]:
    """Subject and object alignment, on the entity head of the subject phrase.

    A shared head noun with a different modifier ("Agency Z" for "Agency X") is
    the wrong-actor case this stage exists to catch, so that must fail here.
    """
    head = entity_head(claim.entity)
    if head is None:
        return "NOT_APPLICABLE", (
            f"the claim's subject field carries no entity mention: {claim.entity[:60]!r}")
    claim_tokens = _entity_tokens(head)
    if not claim_tokens:
        return "NOT_APPLICABLE", "the claim names no entity"
    evidence_tokens = _entity_tokens(evidence.entity) + _entity_tokens(evidence.text)
    if _contains_sequence(evidence_tokens, claim_tokens):
        return "PASS", f"entity mention matched: {' '.join(claim_tokens)}"
    if _joined(claim_tokens) in _joined(evidence_tokens):
        return "PASS", (f"entity mention matched across a layout word break: "
                        f"{' '.join(claim_tokens)}")
    if len(claim_tokens) > 1 and set(claim_tokens) <= set(evidence_tokens):
        # "the number of persons employed" and "the number of employed persons"
        # name one thing.  Order-insensitive containment is safe here precisely
        # because the discriminating short tokens are retained: "agency x" still
        # fails against "agency z".
        return "PASS", (f"entity mention matched with different word order: "
                        f"{' '.join(claim_tokens)}")
    noun = claim_tokens[-1] if len(claim_tokens) == 1 else claim_tokens[0]
    if noun in evidence_tokens:
        return "FAIL", (f"the claim asserts '{' '.join(claim_tokens)}'; the evidence "
                        f"names a different entity sharing the head '{noun}'")
    return "FAIL", (f"the claim asserts '{' '.join(claim_tokens)}'; the evidence "
                    "identifies a different actor")


#: Above this share of the claim's content terms appearing in the evidence, the
#: evidence is carrying the claim's proposition more or less verbatim.
VERBATIM_CARRY_THRESHOLD = 0.85


def verbatim_carry(claim: Proposition, evidence: Proposition) -> float:
    """How much of the claim's propositional content the evidence restates.

    Tokenised *without* the minimum-length filter.  "Agency X operates system Y"
    and "Agency Z operates system Y" share every content term once the single
    letters are dropped, and treating that as a restatement would let the
    verbatim short-circuit overturn the canonical wrong-actor case.
    """
    content = set(_entity_tokens(claim.text))
    if not content:
        return 0.0
    return len(content & set(_entity_tokens(evidence.text))) / len(content)


def _stage_3(claim: Proposition, evidence: Proposition) -> tuple[str, str]:
    """Predicate alignment.

    Only a *recognised* disagreement is a failure.  When the claim's predicate
    family is unrecognised the stage cannot speak; when the evidence's is
    unrecognised, family bookkeeping is not evidence of a different action.
    """
    claim_family = claim.family
    if claim_family is None:
        return "UNRESOLVED", "the claim's predicate family is unrecognised"
    evidence_family = evidence.family
    if evidence_family is None:
        return "UNRESOLVED", (
            f"the claim asserts {claim_family}; the evidence states no recognised "
            "predicate, which is not the same as stating a different one")
    if evidence_family == claim_family:
        return "PASS", f"predicate family {claim_family}"
    return "FAIL", (f"the claim asserts {claim_family}; the evidence supports "
                    f"{evidence_family}")


_SCOPE_HINTS = ("eu-wide", "union-wide", "nationwide", "member state", "member states",
                "europe", "european union", "worldwide", "global", "pilot region",
                "single site", "one site", "bundesweit", "europaweit", "weltweit")


def _stage_4(claim: Proposition, evidence: Proposition) -> tuple[str, str]:
    claimed = {s.casefold() for s in claim.scope}
    if not claimed:
        return "NOT_APPLICABLE", "the claim asserts no explicit scope"
    text = evidence.text.casefold()
    supported = {s for s in claimed if s in text}
    supported |= {s for s in claimed if s in {x.casefold() for x in evidence.scope}}
    if supported == claimed:
        return "PASS", f"scope carried: {sorted(supported)}"
    missing = sorted(claimed - supported)
    narrower = [h for h in _SCOPE_HINTS if h in text and h not in claimed]
    return "FAIL", (f"claimed scope not carried: {missing}"
                    + (f"; evidence scope markers: {narrower[:3]}" if narrower else ""))


_YEAR = re.compile(r"\b(19|20)\d{2}\b")


def _stage_5(claim: Proposition, evidence: Proposition) -> tuple[str, str]:
    start, end = claim.valid_time
    if not start and not end:
        return "NOT_APPLICABLE", "the claim asserts no valid time"
    claim_years = {m.group(0) for value in (start, end) if value
                   for m in _YEAR.finditer(str(value))}
    evidence_years = {m.group(0) for m in _YEAR.finditer(evidence.text)}
    for value in evidence.valid_time:
        if value:
            evidence_years |= {m.group(0) for m in _YEAR.finditer(str(value))}
    if not evidence_years:
        return "UNRESOLVED", "the evidence states no time"
    if claim_years & evidence_years:
        return "PASS", f"time matched on {sorted(claim_years & evidence_years)}"
    return "FAIL", (f"the claim asserts {sorted(claim_years)}; the evidence states "
                    f"{sorted(evidence_years)}")


_NEGATION = re.compile(
    r"\b(not|no|never|without|fails? to|declined|refused|rejected|denies|denied|"
    r"nicht|kein|keine|keinen|niemals|ohne|abgelehnt|verweigert)\b", re.IGNORECASE)


#: Above this length the merged evidence is several assertions, and "does a
#: negation appear anywhere in it" stops being a polarity test.  The same defect
#: class as the cross-document negation check already removed from activation.py.
_POLARITY_ASSESSABLE_CHARS = 600


def _stage_6(claim: Proposition, evidence: Proposition) -> tuple[str, str]:
    claim_polarity = (claim.polarity or "POSITIVE").upper()
    stated = (evidence.polarity or "").upper()
    if stated in ("POSITIVE", "NEGATIVE"):
        evidence_polarity = stated
    elif len(evidence.text) > _POLARITY_ASSESSABLE_CHARS:
        return "UNRESOLVED", (
            f"the merged evidence is {len(evidence.text)} characters and carries "
            "several assertions; a negation somewhere in it is not this claim's "
            "polarity")
    else:
        evidence_polarity = "NEGATIVE" if _NEGATION.search(evidence.text) else "POSITIVE"
    if claim_polarity == evidence_polarity:
        return "PASS", f"polarity {claim_polarity}"
    return "FAIL", (f"the claim asserts {claim_polarity}; the evidence supports "
                    f"{evidence_polarity}")


def _stage_7(claim: Proposition, evidence: Proposition,
             evidence_items: Sequence[Any]) -> tuple[str, str, str, str | None]:
    """Modality and lifecycle.  Wraps the V5.2 gate; its verdict is binding."""
    supported_state, act = v5_2_support.strongest_supported_state(evidence_items)
    claimed_state = claim.lifecycle_state
    verdict, critical = "CONSISTENT", None
    if supported_state == "UNKNOWN":
        # UNKNOWN is the absence of a lifecycle reading, not a weaker one.  It
        # asserts nothing, so it cannot contradict anything — the same principle
        # V5.2 established for ``act_licenses``.  Firing the gate here reports a
        # plan-as-implementation violation on evidence that never mentioned a
        # lifecycle state at all, and it accounted for the largest block of
        # over-rejection in the V5.3 development set.
        #
        # This does NOT weaken the protection: that fires when the evidence
        # licenses an actual *planning* state against an implemented claim, and
        # that path is untouched below.
        return ("UNRESOLVED",
                f"the evidence states no lifecycle reading; {claimed_state} is "
                "neither licensed nor contradicted", "NOT_ESTABLISHED_BY_EVIDENCE",
                None)
    if claimed_state != "UNKNOWN":
        if not lifecycle.entails(supported_state, claimed_state):
            verdict = "CLAIM_EXCEEDS_EVIDENCE"
            critical = (f"evidence licenses {supported_state}; the claim asserts "
                        f"{claimed_state}")
    if critical:
        return "FAIL", critical, verdict, critical
    claim_modality = (claim.modality or "ASSERTED").upper()
    evidence_modality = (evidence.modality or "ASSERTED").upper()
    if claim_modality != evidence_modality and evidence_modality in (
            "PLANNED", "INTENDED", "POSSIBLE", "PROPOSED", "REPORTED"):
        return ("FAIL",
                f"the claim asserts {claim_modality}; the evidence is {evidence_modality}",
                verdict, None)
    return "PASS", f"lifecycle {supported_state} licenses {claimed_state} (act {act})", \
        verdict, None


_QUALIFIER = re.compile(
    r"\b(may|might|could|expected to|intends? to|plans? to|reportedly|allegedly|"
    r"approximately|around|about|up to|at least|preliminary|provisional|subject to|"
    r"kann|könnte|koennte|voraussichtlich|angeblich|etwa|rund|vorläufig|vorlaeufig)\b",
    re.IGNORECASE)
_CONTEXT_MARKER = re.compile(
    r"\b(if|unless|provided that|where|in cases? where|conditional on|depending on|"
    r"falls|sofern|soweit|wenn)\b", re.IGNORECASE)
_INFERENCE_MARKER = re.compile(
    r"\b(suggests?|implies|indicat\w+|consistent with|points? to|would appear|"
    r"deutet auf|lässt vermuten|laesst vermuten)\b", re.IGNORECASE)
_CONTRADICTION = re.compile(
    r"\b(contradict\w*|refut\w*|disprov\w*|contrary to|in fact not|"
    r"widerspricht|widerlegt)\b", re.IGNORECASE)


def _stage_8_and_9(claim: Proposition, evidence: Proposition,
                   counterevidence: Sequence[str]) -> tuple[str, str, str]:
    """Support sufficiency, then contradiction and inference."""
    text = evidence.text
    if _CONTRADICTION.search(text):
        return "CONTRADICTED", "CONTRADICTED", "the evidence explicitly contradicts the claim"
    if counterevidence:
        return "CONTRADICTED", "CONTRADICTED", (
            f"{len(counterevidence)} counterevidence item(s) recorded")
    if _INFERENCE_MARKER.search(text):
        return "INFERENCE_ONLY", "INFERRED", (
            "the evidence indicates rather than states the proposition")
    if _CONTEXT_MARKER.search(text):
        return "CONTEXT_DEPENDENT_SUPPORT", "CONTEXT_DEPENDENT", (
            "the evidence carries the claim only under a stated condition")
    if _QUALIFIER.search(text):
        return "QUALIFIED_SUPPORT", "QUALIFIED", (
            "the evidence carries the claim with an explicit qualification")
    claim_terms = _terms(claim.text)
    if claim_terms:
        carried = len(claim_terms & _terms(text)) / len(claim_terms)
        if carried < 0.5:
            return "PARTIAL_SUPPORT", "PARTIAL", (
                f"the evidence carries {carried:.0%} of the claimed content")
    return "FULL_SUPPORT", "COMPLETE", "the evidence carries the claim as stated"


# ---------------------------------------------------------------------------
# Report wording derivable from a support class (consumed by publication.py)
# ---------------------------------------------------------------------------

ALLOWED_WORDING: Mapping[str, tuple[str, ...]] = {
    "FULL_SUPPORT": ("states", "establishes", "shows", "records"),
    "PARTIAL_SUPPORT": ("partly indicates", "records in part", "supports in part"),
    "QUALIFIED_SUPPORT": ("states, with the qualification that", "reports, subject to"),
    "CONTEXT_DEPENDENT_SUPPORT": ("states, where", "applies when"),
    "INFERENCE_ONLY": ("is consistent with", "may indicate", "suggests"),
    "CONTRADICTED": ("is contradicted by",),
    "NOT_SUPPORTED": (),
    "WRONG_SCOPE": (), "WRONG_TIME": (), "WRONG_ENTITY": (),
    "WRONG_MODALITY": (), "WRONG_POLARITY": (),
}

PROHIBITED_WORDING: Mapping[str, tuple[str, ...]] = {
    "PARTIAL_SUPPORT": ("establishes", "confirms", "proves", "demonstrates", "shows that"),
    "QUALIFIED_SUPPORT": ("establishes", "confirms", "unconditionally", "proves"),
    "CONTEXT_DEPENDENT_SUPPORT": ("in all cases", "generally", "always", "establishes"),
    "INFERENCE_ONLY": ("establishes", "confirms", "shows", "states", "proves",
                       "demonstrates"),
}
_UNIVERSAL_PROHIBITED = ("proves", "confirms beyond doubt", "demonstrates conclusively")


def _wording(support_class: str) -> tuple[tuple[str, ...], tuple[str, ...]]:
    allowed = ALLOWED_WORDING.get(support_class, ())
    prohibited = tuple(PROHIBITED_WORDING.get(support_class, ())) + _UNIVERSAL_PROHIBITED
    if support_class not in AFFIRMATIVE_SUPPORT and support_class != "INFERENCE_ONLY":
        prohibited = prohibited + ("states", "establishes", "shows", "records", "confirms")
    return allowed, tuple(dict.fromkeys(prohibited))


# ---------------------------------------------------------------------------
# The hierarchy
# ---------------------------------------------------------------------------

def assess(claim_like: Any, evidence_items: Sequence[Any], *,
           counterevidence: Sequence[str] = (),
           dependence_limitations: Sequence[str] = (),
           ) -> HierarchicalSupport:
    """Run the nine-stage hierarchy and return the derived support assessment.

    Stages run in order and the first material failure terminates the descent.
    A specific mismatch class is only ever reachable *after* stage 1 passes,
    which is the whole point of the architecture.
    """
    claim = proposition_from(claim_like)
    merged_text = " ".join(
        str(_get(item, "normalized_statement") or _get(item, "text") or
            _get(item, "statement") or "") for item in evidence_items).strip()
    evidence = Proposition(
        text=merged_text,
        entity=" ".join(str(_get(item, "entity") or "") for item in evidence_items),
        predicate=merged_text,
        obj="",
        scope=tuple(s for item in evidence_items for s in (_get(item, "scope") or ())),
        valid_time=(_first(evidence_items, "valid_time") or (None, None)),
        polarity=str(_first(evidence_items, "polarity") or ""),
        modality=str(_first(evidence_items, "modality") or ""),
    )

    findings: list[tuple[str, str, str]] = []
    path: list[str] = []
    first_failure: str | None = None

    # When the evidence restates the claim's proposition almost verbatim, the
    # proposition is carried and no dimensional mismatch is diagnosable: the
    # dimensions are the same words.  Establishing this before the descent stops
    # family bookkeeping from converting an identical sentence into WRONG_ENTITY.
    carried = verbatim_carry(claim, evidence)
    verbatim = carried >= VERBATIM_CARRY_THRESHOLD

    outcome, detail = _stage_1(claim, evidence)
    if verbatim and outcome != "PASS":
        outcome, detail = "PASS", (
            f"the evidence restates {carried:.0%} of the claim's content; the "
            "proposition is addressed by restatement")
    findings.append(("S1_PROPOSITION_ADDRESSABILITY", outcome, detail))
    path.append("S1_PROPOSITION_ADDRESSABILITY")
    addresses = outcome == "PASS"
    if not addresses:
        first_failure = "S1_PROPOSITION_ADDRESSABILITY"

    alignments = {
        "entity_alignment": "NOT_APPLICABLE", "predicate_alignment": "NOT_APPLICABLE",
        "scope_alignment": "NOT_APPLICABLE", "time_alignment": "NOT_APPLICABLE",
        "polarity_alignment": "NOT_APPLICABLE", "modality_alignment": "NOT_APPLICABLE",
        "lifecycle_alignment": "NOT_APPLICABLE",
    }
    lifecycle_verdict, critical = "NOT_EVALUATED", None

    if addresses:
        for stage, fn, key in (
                ("S2_SUBJECT_AND_OBJECT_ALIGNMENT", _stage_2, "entity_alignment"),
                ("S3_PREDICATE_ALIGNMENT", _stage_3, "predicate_alignment"),
                ("S4_SCOPE_ALIGNMENT", _stage_4, "scope_alignment"),
                ("S5_TEMPORAL_ALIGNMENT", _stage_5, "time_alignment"),
                ("S6_POLARITY_ALIGNMENT", _stage_6, "polarity_alignment")):
            outcome, detail = fn(claim, evidence)
            if verbatim and outcome == "FAIL" and stage in (
                    "S2_SUBJECT_AND_OBJECT_ALIGNMENT", "S3_PREDICATE_ALIGNMENT"):
                # The evidence restates the claim; entity and predicate cannot be
                # misaligned against a restatement of themselves.
                outcome, detail = "PASS", (
                    f"restatement carries {carried:.0%} of the claim's content, "
                    f"including this dimension ({detail})")
            findings.append((stage, outcome, detail))
            path.append(stage)
            alignments[key] = {"PASS": "ALIGNED", "FAIL": "MISALIGNED",
                               "UNRESOLVED": "UNRESOLVED"}.get(outcome, "NOT_APPLICABLE")
            if outcome == "FAIL" and first_failure is None:
                first_failure = stage
                break

        if first_failure is None:
            outcome, detail, lifecycle_verdict, critical = _stage_7(
                claim, evidence, evidence_items)
            findings.append(("S7_MODALITY_AND_LIFECYCLE_ALIGNMENT", outcome, detail))
            path.append("S7_MODALITY_AND_LIFECYCLE_ALIGNMENT")
            alignments["modality_alignment"] = {
                "PASS": "ALIGNED", "FAIL": "MISALIGNED"}.get(outcome, "UNRESOLVED")
            alignments["lifecycle_alignment"] = {
                "CONSISTENT": "ALIGNED", "NOT_ESTABLISHED_BY_EVIDENCE": "UNRESOLVED",
            }.get(lifecycle_verdict, "MISALIGNED")
            if outcome == "FAIL":
                first_failure = "S7_MODALITY_AND_LIFECYCLE_ALIGNMENT"

    if first_failure is not None:
        support_class = STAGE_FAILURE_CLASS[first_failure]
        completeness = "ABSENT"
        sufficiency_detail = f"terminated at {first_failure}"
        # Section 5.4 discipline, second half.  Naming a dimension asserts that
        # every other dimension lined up.  That assertion is unavailable when the
        # evidence never engaged the claim's predicate family in the first place
        # — shared topic is not the same proposition.  Demote to the broad class.
        if support_class in SPECIFIC_MISMATCH and not family_established(claim, evidence):
            findings.append((
                "S4_SPECIFIC_MISMATCH_DISCIPLINE", "DEMOTED",
                f"{support_class} requires the same proposition family; the claim "
                f"asserts {claim.family or 'no recognised predicate'} and the evidence "
                f"{evidence.family or 'no recognised predicate'}, so the failure is "
                "reported as NOT_SUPPORTED"))
            path.append("S4_SPECIFIC_MISMATCH_DISCIPLINE")
            support_class = "NOT_SUPPORTED"
    else:
        support_class, completeness, sufficiency_detail = _stage_8_and_9(
            claim, evidence, counterevidence)
        findings.append(("S8_SUPPORT_SUFFICIENCY", "PASS", sufficiency_detail))
        path.append("S8_SUPPORT_SUFFICIENCY")
        if support_class in ("CONTRADICTED", "INFERENCE_ONLY"):
            findings.append(("S9_CONTRADICTION_AND_INFERENCE", "PASS", sufficiency_detail))
            path.append("S9_CONTRADICTION_AND_INFERENCE")

    # The V5.2 gate is binding: a lifecycle critical error may never resolve to
    # affirmative support, whatever stages 8 and 9 concluded.
    if critical and support_class in AFFIRMATIVE_SUPPORT:
        support_class = "WRONG_MODALITY"
        completeness = "ABSENT"

    vector = SupportVector(
        addresses_proposition=addresses,
        dependence_limitations=tuple(dependence_limitations),
        counterevidence=tuple(counterevidence),
        support_completeness=completeness,
        attribution_alignment=("ALIGNED" if claim.attribution is None
                               or claim.attribution in evidence.text else "UNRESOLVED"),
        **alignments)
    allowed, prohibited = _wording(support_class)
    rejected = _alternatives_rejected(support_class, addresses, first_failure)
    claim_id = str(_get(claim_like, "claim_id") or stable_id("v5-4-claim", claim.text))
    return HierarchicalSupport(
        stable_id("v5-4-support", claim_id, support_class, merged_text[:120]),
        claim_id, support_class, vector, tuple(path), first_failure, tuple(findings),
        tuple(str(_get(item, "span_id") or _get(item, "evidence_span_id") or "")
              for item in evidence_items),
        rejected, allowed, prohibited, lifecycle_verdict, critical, now_utc())


def _alternatives_rejected(support_class: str, addresses: bool,
                           first_failure: str | None) -> tuple[tuple[str, str], ...]:
    """Why the neighbouring classes were not chosen — the V5.3 confusion pairs."""
    out: list[tuple[str, str]] = []
    if support_class == "NOT_SUPPORTED" and not addresses:
        out.append(("WRONG_ENTITY",
                    "the evidence does not address the claimed proposition, so no "
                    "specific dimensional mismatch is diagnosable (Section 5.4)"))
        out.append(("WRONG_TIME",
                    "same reason: a specific mismatch requires proposition addressability"))
    if support_class == "WRONG_ENTITY":
        out.append(("NOT_SUPPORTED",
                    "the evidence addresses the same predicate and object; the failure "
                    "is localised to the actor"))
    if support_class == "FULL_SUPPORT":
        out.append(("PARTIAL_SUPPORT", "the evidence carries the claimed content in full"))
        out.append(("QUALIFIED_SUPPORT", "the evidence states no qualification"))
    if support_class == "PARTIAL_SUPPORT":
        out.append(("FULL_SUPPORT", "the evidence carries only part of the claimed content"))
    if support_class == "QUALIFIED_SUPPORT":
        out.append(("FULL_SUPPORT",
                    "the evidence carries an explicit qualification that the claim drops"))
    if support_class == "WRONG_MODALITY" and first_failure:
        out.append(("NOT_SUPPORTED",
                    "the evidence does address the proposition; it licenses a weaker "
                    "lifecycle state or modality than the claim asserts"))
    return tuple(out)


def _get(obj: Any, key: str, default: Any = None) -> Any:
    if isinstance(obj, Mapping):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _first(items: Sequence[Any], key: str) -> Any:
    for item in items:
        value = _get(item, key)
        if value:
            return value
    return None


# ---------------------------------------------------------------------------
# Section 5.7 — critical claim-support conditions
# ---------------------------------------------------------------------------

CRITICAL_CONDITIONS: tuple[str, ...] = (
    "plan_as_implementation",
    "announcement_as_existing_capability",
    "vendor_claim_as_demonstrated_fact",
    "pilot_as_operational",
    "exercise_as_deployment",
    "retracted_support_as_current",
    "unsupported_claim_accepted",
)

_PLAN_STATES = frozenset({
    "PROPOSED", "ANNOUNCED", "POLICY_ADOPTED", "BUDGET_REQUESTED",
    "PROCUREMENT_PLANNED", "DEPLOYMENT_PLANNED", "PILOT_PLANNED", "IN_DEVELOPMENT",
})
_IMPLEMENTED_STATES = frozenset({"OPERATIONAL", "DEPLOYED", "EXERCISED", "PILOT_ACTIVE",
                                 "PILOT_COMPLETED", "TECHNICALLY_AVAILABLE"})


def audit_critical(supports: Iterable[HierarchicalSupport | Mapping[str, Any]],
                   claims: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Count the Section 5.7 conditions that must all be zero."""
    claims = claims or {}
    counts = {name: 0 for name in CRITICAL_CONDITIONS}
    offenders: dict[str, list[str]] = {name: [] for name in CRITICAL_CONDITIONS}

    for support in supports:
        support_class = str(_get(support, "support_class"))
        claim_id = str(_get(support, "claim_id"))
        affirmative = support_class in AFFIRMATIVE_SUPPORT
        critical = _get(support, "critical_error")
        claim = claims.get(claim_id, {})
        claimed_state = lifecycle.normalize_state(
            str(_get(claim, "lifecycle_state") or "UNKNOWN"))
        supported = str(_get(support, "lifecycle_verdict") or "")

        if affirmative and critical:
            counts["plan_as_implementation"] += 1
            offenders["plan_as_implementation"].append(claim_id)
        if affirmative and supported == "CLAIM_EXCEEDS_EVIDENCE":
            counts["announcement_as_existing_capability"] += 1
            offenders["announcement_as_existing_capability"].append(claim_id)
        if affirmative and claimed_state in _IMPLEMENTED_STATES:
            evidence_state = lifecycle.normalize_state(
                str(_get(claim, "evidence_lifecycle_state") or "UNKNOWN"))
            if evidence_state in _PLAN_STATES:
                counts["plan_as_implementation"] += 1
                offenders["plan_as_implementation"].append(claim_id)
            if evidence_state in ("PILOT_ACTIVE", "PILOT_COMPLETED") and \
                    claimed_state == "OPERATIONAL":
                counts["pilot_as_operational"] += 1
                offenders["pilot_as_operational"].append(claim_id)
            if evidence_state == "EXERCISED" and claimed_state == "DEPLOYED":
                counts["exercise_as_deployment"] += 1
                offenders["exercise_as_deployment"].append(claim_id)
        if affirmative and str(_get(claim, "source_authority") or "") == "VENDOR_CLAIM":
            counts["vendor_claim_as_demonstrated_fact"] += 1
            offenders["vendor_claim_as_demonstrated_fact"].append(claim_id)
        if affirmative and str(_get(claim, "correction_state") or "") in (
                "RETRACTED", "WITHDRAWN", "SUPERSEDED"):
            counts["retracted_support_as_current"] += 1
            offenders["retracted_support_as_current"].append(claim_id)
        vector = _get(support, "vector")
        addresses = _get(vector, "addresses_proposition") if vector is not None else None
        if affirmative and addresses is False:
            counts["unsupported_claim_accepted"] += 1
            offenders["unsupported_claim_accepted"].append(claim_id)

    return {
        "counts": counts,
        "offenders": {k: v[:20] for k, v in offenders.items() if v},
        "all_zero": all(v == 0 for v in counts.values()),
        "verdict": "PASS" if all(v == 0 for v in counts.values()) else "FAIL",
    }


def class_coverage(supports: Iterable[HierarchicalSupport | Mapping[str, Any]]
                   ) -> dict[str, Any]:
    """Which of the 12 support classes were actually produced."""
    seen: dict[str, int] = {name: 0 for name in SUPPORT_CLASSES}
    for support in supports:
        name = str(_get(support, "support_class"))
        if name in seen:
            seen[name] += 1
    exercised = sum(1 for v in seen.values() if v)
    return {"per_class": seen, "classes_exercised": exercised,
            "classes_total": len(SUPPORT_CLASSES),
            "distinct_classes": exercised,
            "largest_class_share": (max(seen.values()) / sum(seen.values()))
            if sum(seen.values()) else 0.0}
