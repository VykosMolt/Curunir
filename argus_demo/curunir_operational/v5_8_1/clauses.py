"""V5.8.1 §3 — D25: finite-clause recognition that is not shaped like English.

The V5.2 lexicon that decides `finite_clause` has entries for four languages —
en, de, fr, es — and nothing else.  A corpus containing Arabic and Russian
therefore did not fail to *classify* those propositions; it failed to see that
they were propositions at all.  39 of 40 Arabic development spans were rejected
for carrying "no propositional content", and Spanish and French legal recitals
failed the same way because their predicate is non-finite.

That is one defect wearing three hats, and it is the programme's recurring
shape once more: a signal attributed to something it does not govern.  Absence
of an English-style finite verb was read as absence of predication.

So this module derives predication evidence from independent, typed dimensions
and reports which one established the clause.  It never guesses a predicate: a
span with no evidence in any dimension resolves to UNRESOLVED, and only a span
that is positively non-propositional — a bare noun phrase, a heading, a
fragment — is called NOT_A_FINITE_CLAUSE.

Every scan here is linear.  The catastrophic title-case regex found in the D24
module was a nested-quantifier pattern that ran for an hour on real input; the
lesson generalises, so nothing below nests a quantifier inside a quantifier.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from ..v5_1.models import Record, now_utc, stable_id


class ClauseViolation(RuntimeError):
    """A clause state was asserted without evidence for it."""


#: §3.1 — the four states.  RECOVERABLE and UNRESOLVED are distinct from
#: NOT_A_FINITE_CLAUSE, and neither may be collapsed into it.
CLAUSE_STATES: tuple[str, ...] = (
    "FINITE_CLAUSE_ESTABLISHED", "FINITE_CLAUSE_RECOVERABLE",
    "FINITE_CLAUSE_UNRESOLVED", "NOT_A_FINITE_CLAUSE",
)

#: How far a governing predicate may sit from a dependent recital.  Bounded,
#: declared, and never widened to make a difficult case pass.
GOVERNING_CONTEXT_CHARS = 600
MAX_SCAN_CHARS = 4000


# ===========================================================================
# Script detection — language routing that does not need a language tag
# ===========================================================================

def script_of(text: str) -> str:
    """The dominant script, decided by counting, not by guessing."""
    counts: dict[str, int] = {}
    for character in text[:MAX_SCAN_CHARS]:
        if not character.isalpha():
            continue
        try:
            name = unicodedata.name(character)
        except ValueError:
            continue
        family = ("ARABIC" if name.startswith("ARABIC") else
                  "CYRILLIC" if name.startswith("CYRILLIC") else
                  "HAN" if name.startswith("CJK") else
                  "LATIN" if name.startswith("LATIN") else "OTHER")
        counts[family] = counts.get(family, 0) + 1
    if not counts:
        return "UNKNOWN"
    return max(counts.items(), key=lambda item: item[1])[0]


# ===========================================================================
# Arabic
# ===========================================================================

#: Arabic verbs are marked at the head of the word, not at the end.  The
#: imperfect prefixes are ي ت ن أ; the passive adds a damma the text usually
#: drops.  Matching the prefix plus a three-consonant body recognises the
#: paradigm without listing the verbs, which a whitelist could never do.
_AR_IMPERFECT = re.compile(r"(?:^|\s|و|ف|ل|س)([يتنأ][ء-ي]{2,})")
#: The definite article ال is not a verbal prefix, and a word carrying it is a
#: noun however its second letter looks.  Without this, المحتويات ("contents")
#: read as an imperfect verb and a table-of-contents heading was established as
#: a proposition — the exact over-admission this repair must not introduce.
_AR_DEFINITE_WORD = re.compile(r"(?:^|\s)ال[ء-ي]{2,}")
_AR_PERFECT = re.compile(r"(?:^|\s|و|ف)([ء-ي]{3,}(?:َ|ت|وا)?)\s")
#: Deontic and legal predicates.  Listed because they are the register's
#: closed-class operators — the equivalent of "shall" and "may" — not because
#: the detector depends on a phrase list.
_AR_MODAL = re.compile(
    r"(يجب|يجوز|لا\s*يجوز|ينبغي|يتعين|يلزم|يحظر|يُحظر|يمنع|يُمنع|"
    r"يلتزم|تلتزم|يتولى|تتولى|يعتبر|يُعتبر|تعتبر|تُعتبر|يعد|يُعد|"
    r"يعمل\s*به|يُعمل\s*به|يسري|تسري|يخضع|تخضع|يُشترط|يشترط)")
_AR_COPULA = re.compile(r"(يكون|تكون|كان|كانت|ليس|ليست|أصبح|أصبحت|صار|صارت)")
#: A nominal clause: a definite subject followed by a predicate with no verb.
#: Arabic present-tense predication has no copula at all, so requiring one is
#: precisely the English assumption this module exists to remove.
_AR_DEFINITE = re.compile(r"(?:^|\s)(ال[ء-ي]{2,})")
_AR_PARTICLE = re.compile(r"(?:^|\s)(إن|أن|لأن|حيث|بما|من\s*أجل|بينما|إذا)")
_AR_NEGATION = re.compile(r"(?:^|\s)(لا|لم|لن|ما|ليس)(?:\s|$)")


def _arabic_evidence(text: str) -> dict[str, Any]:
    body = text[:MAX_SCAN_CHARS]
    words = body.split()
    modal = bool(_AR_MODAL.search(body))
    copula = bool(_AR_COPULA.search(body))
    imperfect = any(
        not match.group(1).startswith("ال")
        for match in _AR_IMPERFECT.finditer(body))
    # One word predicates nothing, whatever morphology it carries.
    if len(words) < 3:
        imperfect = False
    definite = len(_AR_DEFINITE.findall(body))
    # A nominal clause needs a topic and something predicated of it.  Two
    # definite noun phrases in a clause-length span is the canonical shape.
    zero_copula = definite >= 2 and len(words) >= 5 and not imperfect
    passive = bool(re.search(r"(?:^|\s)(يُ|تُ)[ء-ي]{2,}", body))
    return {
        "finite_verbal_morphology": imperfect,
        "auxiliary_or_modal_predicate": modal,
        "copular_predication": copula,
        "zero_copula_nominal_predication": zero_copula,
        "short_form_participial_predication": False,
        "passive_or_impersonal_legal_predication": passive,
        "legal_formula_predicate": modal,
        "subject_predicate_relation": definite >= 1 and (imperfect or modal
                                                         or copula or zero_copula),
        "bounded_governing_clause": bool(_AR_PARTICLE.match(body.strip())),
        "negation_present": bool(_AR_NEGATION.search(body)),
    }


# ===========================================================================
# Russian
# ===========================================================================

_RU_MODAL = re.compile(
    r"(?:^|\W)(должен|должна|должно|должны|может|можно|могут|обязан|обязана|"
    r"обязано|обязаны|вправе|следует|надлежит|нельзя|необходимо)(?:\W|$)",
    re.IGNORECASE)
#: Impersonal legal predication: the -ся passive that carries most of the
#: normative content of Russian statute.
_RU_IMPERSONAL = re.compile(
    r"(?:^|\W)(запрещается|разрешается|устанавливается|определяется|"
    r"применяется|осуществляется|признаётся|признается|считается|"
    r"подлежит|допускается|предусматривается|регулируется|утверждается)"
    r"(?:\W|$)", re.IGNORECASE)
#: Short-form participles and adjectives: "утверждён", "принят", "установлена".
#: These *are* the predicate; there is no verb beside them.
_RU_SHORT_FORM = re.compile(
    r"(?:^|\W)(\w{3,}(?:ён|ен|ена|ено|ены|ан|ана|ано|аны|ят|ята|"
    r"им|има|имо|имы))(?:\W|$)", re.IGNORECASE)
_RU_COPULA = re.compile(
    r"(?:^|\W)(является|являются|являлся|была|были|было|был|будет|будут|"
    r"есть|стал|стала|стало|стали)(?:\W|$)", re.IGNORECASE)
#: Ordinary finite endings.  Anchored per word, not applied to the whole span.
_RU_FINITE_SUFFIX = re.compile(
    r"^\w{3,}(?:ет|ёт|ут|ют|ит|ат|ят|ешь|ишь|ем|им|ете|ите|ал|ала|"
    r"али|ало|ил|ила|или|ило)$", re.IGNORECASE)
_RU_SUBORDINATOR = re.compile(
    r"^(?:поскольку|так\s+как|учитывая|принимая|если|когда|хотя|чтобы)\b",
    re.IGNORECASE)
_RU_CASE_MARKED = re.compile(r"\w{4,}(?:ого|ому|ыми|ами|ями|ах|ях|ой|ей|ию|ию)$",
                             re.IGNORECASE)
#: Recital-discourse converbs.  This is the same closed constructional
#: inventory the role binder has used to realize RECITAL_RELATION_PREDICATE
#: spans since D38.  It is intentionally not the open Russian converb
#: morphology: forms such as ``включая`` can be grammaticalized prepositions
#: and must not become predicates merely because they end in -ая.
_RU_RECITAL_CONVERB_HEAD = re.compile(
    r"^(учитывая|принимая|сознавая|признавая|стремясь|подтверждая|ссылаясь|"
    r"отмечая|будучи|рассмотрев|заслушав|напоминая|приветствуя|выражая|"
    r"подчеркивая|осознавая|исходя|руководствуясь)(?=\W|$)",
    re.IGNORECASE)


def _russian_evidence(text: str) -> dict[str, Any]:
    body = text[:MAX_SCAN_CHARS]
    words = [w.strip(".,;:()«»\"'—–-") for w in body.split()]
    recital_converb = bool(_RU_RECITAL_CONVERB_HEAD.match(body.lstrip()))
    finite = any(_RU_FINITE_SUFFIX.match(word) for word in words)
    modal = bool(_RU_MODAL.search(body))
    impersonal = bool(_RU_IMPERSONAL.search(body))
    short_form = bool(_RU_SHORT_FORM.search(body))
    copula = bool(_RU_COPULA.search(body))
    # Present-tense predication with no copula at all: "Договор — основной
    # документ", "Настоящий закон обязателен".  A clause-length nominal span
    # with case-marked dependents is predication, not a noun phrase.
    zero_copula = (not (finite or copula or impersonal)
                   and len(words) >= 5
                   and any(_RU_CASE_MARKED.match(word) for word in words))
    return {
        "finite_verbal_morphology": finite,
        "auxiliary_or_modal_predicate": modal,
        "copular_predication": copula,
        "zero_copula_nominal_predication": zero_copula,
        "short_form_participial_predication": short_form,
        "passive_or_impersonal_legal_predication": impersonal,
        "legal_formula_predicate": modal or impersonal,
        "dependent_nonfinite_predication": recital_converb,
        "subject_predicate_relation": finite or modal or impersonal or short_form
                                      or copula or zero_copula
                                      or recital_converb,
        "bounded_governing_clause": (
            recital_converb or bool(_RU_SUBORDINATOR.match(body.strip()))),
        "negation_present": bool(re.search(r"(?:^|\W)(не|ни)(?:\W|$)", body,
                                           re.IGNORECASE)),
    }


# ===========================================================================
# Latin-script legal register — recitals and non-finite predication
# ===========================================================================

#: A recital opens a dependent clause whose governing predicate sits elsewhere
#: in the instrument.  Recognising the opener is what lets the state be
#: RECOVERABLE rather than either accepted or invalidated.
_RECITAL_OPENER = re.compile(
    r"^\s*(whereas|having regard to|having considered|considering|recalling|"
    r"reaffirming|recognizing|recognising|noting|desiring|convinced|mindful|"
    r"in erw(?:ä|ae)gung|in anbetracht|gest(?:ü|ue)tzt auf|"
    r"in der erw(?:ä|ae)gung|in kenntnis|"
    r"considérant|vu que|vu les|rappelant|réaffirmant|reconnaissant|"
    r"considerando|visto que|recordando|reafirmando|reconociendo|"
    r"teniendo en cuenta|convencidos)\b", re.IGNORECASE)
#: Participial and gerundive heads, which carry the predication in this
#: register without any finite form: "Reafirmando su propósito de consolidar…".
_PARTICIPIAL_HEAD = re.compile(
    r"^\s*\w+(?:ing|ed|ando|iendo|ant|ent|end|ando)\b", re.IGNORECASE)
_LEGAL_MODAL = re.compile(
    r"(?:^|\W)(shall|must|may not|may|is required to|are required to|"
    r"is prohibited|are prohibited|soll|sollen|muss|müssen|darf|dürfen|"
    r"doit|doivent|peut|peuvent|ne peut|debe|deben|puede|pueden|no podrá|"
    r"podrá|podrán)(?:\W|$)", re.IGNORECASE)
_ENUMERATED = re.compile(r"^\s*(?:\(?\d{1,3}[.)]|[a-z][.)]|art(?:icle|ikel)?\.?\s*\d)",
                         re.IGNORECASE)
_COORDINATOR = re.compile(r"(?:^|\W)(and|or|und|oder|et|ou|y|o)(?:\W|$)",
                          re.IGNORECASE)


#: Ordinary finite morphology in Latin script: third-person -s, past -ed, and
#: the Romance/German inflections.  The V5.2 splitter models these for the four
#: languages it covers, but detect() must not *depend* on its caller having run
#: that splitter — called on its own it reported NOT_A_FINITE_CLAUSE for "It
#: spreads when people cough", which is not a defensible answer from a module
#: whose whole purpose is to stop treating unfamiliarity as absence.
_LATIN_FINITE_MORPHOLOGY = re.compile(
    r"^\w{3,}(?:s|ed|es|ies|t|te|ten|tet|ait|aient|ent|ió|ó|an|en|a|e)$",
    re.IGNORECASE)
_LATIN_FUNCTION_WORD = re.compile(
    r"^(the|a|an|of|in|on|to|for|with|by|as|at|and|or|its|their|his|her|this|"
    r"that|these|those|is|are|was|were|be|been|der|die|das|des|dem|den|ein|"
    r"eine|und|oder|von|mit|für|im|le|la|les|des|du|de|et|ou|un|une|dans|par|"
    r"pour|el|los|las|un|una|y|o|en|con|por|para)$", re.IGNORECASE)


def _latin_evidence(text: str, *, base_finite: bool) -> dict[str, Any]:
    body = text[:MAX_SCAN_CHARS]
    stripped = body.strip()
    recital = bool(_RECITAL_OPENER.match(stripped))
    participial = bool(_PARTICIPIAL_HEAD.match(stripped))
    modal = bool(_LEGAL_MODAL.search(body))
    return {
        # Morphological finite detection was tried here and reverted: the
        # suffix set that catches "spreads" also catches "Multilaterales" and
        # "donnees", so it traded three correct refusals for one case the V5.2
        # splitter already handles through base_finite.  A weak signal that
        # fires on plural nouns is not evidence of predication.
        "finite_verbal_morphology": base_finite,
        "auxiliary_or_modal_predicate": modal,
        "copular_predication": base_finite,
        "zero_copula_nominal_predication": False,
        "short_form_participial_predication": participial and not base_finite,
        "passive_or_impersonal_legal_predication": bool(
            re.search(r"(?:^|\W)(is|are|was|were|wird|werden|est|sont|es|son)"
                      r"\s+\w+(?:ed|en|é|és|ado|ido|t)\b", body, re.IGNORECASE)),
        "legal_formula_predicate": modal or bool(_ENUMERATED.match(stripped)),
        "subject_predicate_relation": base_finite or modal,
        "bounded_governing_clause": recital or participial,
        "negation_present": bool(re.search(r"(?:^|\W)(not|nicht|ne|pas|no)(?:\W|$)",
                                           body, re.IGNORECASE)),
    }


# ===========================================================================
# The evidence record
# ===========================================================================

@dataclass(frozen=True)
class FiniteClauseEvidence(Record):
    """§3.1 — independently derived predication evidence, and the state it supports."""

    evidence_id: str
    state: str
    language_or_script: str
    finite_verbal_morphology: bool
    auxiliary_or_modal_predicate: bool
    copular_predication: bool
    zero_copula_nominal_predication: bool
    short_form_participial_predication: bool
    passive_or_impersonal_legal_predication: bool
    shared_coordinated_predicate: bool
    bounded_governing_clause: bool
    legal_formula_predicate: bool
    subject_predicate_relation: bool
    dependent_nonfinite_predication: bool
    semantic_predication_status: str
    predicate_realization_mode: str
    local_predicate_span: tuple[int, int] | None
    local_predicate_text: str
    clause_boundary_confidence: float
    establishing_dimensions: tuple[str, ...]
    reason: str
    recorded_time: str

    @property
    def establishes(self) -> bool:
        return self.state == "FINITE_CLAUSE_ESTABLISHED"

    @property
    def refutes(self) -> bool:
        return self.state == "NOT_A_FINITE_CLAUSE"


#: Dimensions that on their own establish predication.
_ESTABLISHING = (
    "finite_verbal_morphology", "auxiliary_or_modal_predicate",
    "copular_predication", "zero_copula_nominal_predication",
    "short_form_participial_predication",
    "passive_or_impersonal_legal_predication", "legal_formula_predicate",
)

_HEADING_SHAPE = re.compile(r"[.!?؟。]\s*$")


def _clause_boundary_confidence(text: str) -> float:
    """How confident we are that this span is a whole clause, not a fragment."""
    stripped = text.strip()
    if not stripped:
        return 0.0
    score = 0.4
    if _HEADING_SHAPE.search(stripped) or stripped.endswith((";", "؛", ":")):
        score += 0.3
    if len(stripped.split()) >= 8:
        score += 0.2
    if stripped[0].isupper() or script_of(stripped) in ("ARABIC", "CYRILLIC"):
        score += 0.1
    return round(min(score, 1.0), 4)


#: Languages whose predication the V5.2 role splitter already models.  For
#: these, a negative result from the existing lexicon *is* evidence: the
#: resource is competent, so silence means no predicate.  For every other
#: language silence means only that nothing here can see one, which is a
#: different fact and must not be reported as the same one.
LEXICON_COVERED: frozenset[str] = frozenset({"en", "de", "fr", "es"})


def detect(text: str, *, language: str | None = None,
           governing_context: str = "",
           base_finite_clause: bool = False) -> FiniteClauseEvidence:
    """Decide the finite-clause state of one span.

    ``base_finite_clause`` carries whatever the V5.2 role splitter already
    found, so this detector only ever *adds* evidence.  It cannot take a clause
    away from a language the existing lexicon handles.
    """
    body = (text or "")[:MAX_SCAN_CHARS]
    script = script_of(body)
    declared = (language or "").lower()[:2]
    if script == "ARABIC" or declared == "ar":
        signals = _arabic_evidence(body)
        family = "ARABIC"
    elif script == "CYRILLIC" or declared == "ru":
        signals = _russian_evidence(body)
        family = "CYRILLIC"
    else:
        signals = _latin_evidence(body, base_finite=base_finite_clause)
        family = script

    words = body.split()
    coordinated = (bool(_COORDINATOR.search(body)) and len(words) >= 8
                   and not signals["subject_predicate_relation"]
                   and bool(governing_context))
    confidence = _clause_boundary_confidence(body)
    establishing = tuple(name for name in _ESTABLISHING if signals.get(name))
    dependent_nonfinite = bool(
        signals.get("dependent_nonfinite_predication", False))

    # A recital converb predicates, but it does not thereby become finite or
    # independently assertable.  This branch therefore precedes incidental
    # finite-looking material in its complement and keeps the syntactic axis at
    # RECOVERABLE while the separate semantic axis records establishment.
    if dependent_nonfinite:
        state = "FINITE_CLAUSE_RECOVERABLE"
        reason = ("dependent non-finite recital predication is established "
                  "locally; its controlled subject is supplied by the "
                  "governing enactment clause")
    elif base_finite_clause:
        state = "FINITE_CLAUSE_ESTABLISHED"
        reason = "the role splitter already found a finite clause"
    elif establishing:
        state = "FINITE_CLAUSE_ESTABLISHED"
        reason = f"predication established by {', '.join(establishing)}"
    elif signals["bounded_governing_clause"] or coordinated:
        # A recital or a coordinated item: the predicate exists, in bounded
        # context, and the span depends on it.  Neither accepted outright nor
        # thrown away.
        state = "FINITE_CLAUSE_RECOVERABLE"
        reason = ("the span opens a dependent clause whose governing predicate "
                  "sits in bounded context" if signals["bounded_governing_clause"]
                  else "the span is a coordinated item sharing a predicate")
    elif len(words) < 4 or not _looks_propositional(body, family):
        state = "NOT_A_FINITE_CLAUSE"
        reason = ("too short to predicate" if len(words) < 4 else
                  "a bare nominal with no predication in any dimension")
    elif declared in LEXICON_COVERED and family == "LATIN":
        state = "NOT_A_FINITE_CLAUSE"
        reason = (f"no predication in any dimension, and the role splitter "
                  f"covers {declared}: for this language a negative result is "
                  "evidence rather than silence")
    else:
        state = "FINITE_CLAUSE_UNRESOLVED"
        reason = ("clause-length text with no predication evidence in any "
                  "dimension, in a language the role splitter does not model; "
                  "the detector will not invent a predicate")

    if dependent_nonfinite:
        semantic_status = "SEMANTIC_PROPOSITION_ESTABLISHED"
        realization_mode = "DEPENDENT_NONFINITE_PREDICATION/CONVERB"
        leading = len(body) - len(body.lstrip())
        converb = _RU_RECITAL_CONVERB_HEAD.match(body.lstrip())
        local_span = ((leading, leading + len(converb.group(0)))
                      if converb is not None else None)
        local_text = body[local_span[0]:local_span[1]] if local_span else ""
    elif state == "FINITE_CLAUSE_ESTABLISHED":
        semantic_status = "SEMANTIC_PROPOSITION_ESTABLISHED"
        realization_mode = "EXISTING_FINITE_OR_OTHER_PREDICATION"
        local_span = None
        local_text = ""
    elif state == "NOT_A_FINITE_CLAUSE":
        semantic_status = "NO_SEMANTIC_PROPOSITION"
        realization_mode = "NON_PREDICATIVE"
        local_span = None
        local_text = ""
    else:
        semantic_status = "SEMANTIC_PROPOSITION_UNRESOLVED"
        realization_mode = "UNRESOLVED"
        local_span = None
        local_text = ""

    return FiniteClauseEvidence(
        stable_id("v5-8-1-clause", body[:120], family),
        state, family,
        signals["finite_verbal_morphology"],
        signals["auxiliary_or_modal_predicate"],
        signals["copular_predication"],
        signals["zero_copula_nominal_predication"],
        signals["short_form_participial_predication"],
        signals["passive_or_impersonal_legal_predication"],
        coordinated, signals["bounded_governing_clause"],
        signals["legal_formula_predicate"],
        signals["subject_predicate_relation"],
        dependent_nonfinite, semantic_status, realization_mode,
        local_span, local_text,
        confidence, establishing, reason, now_utc())


def _looks_propositional(text: str, family: str) -> bool:
    """A last, deliberately weak, check that this is not a bare noun phrase."""
    stripped = text.strip()
    words = stripped.split()
    if len(words) < 4:
        return False
    if _HEADING_SHAPE.search(stripped) or stripped.endswith((";", ":")):
        return True
    if family == "LATIN":
        # A run of capitalised words with no lower-case function word is a
        # title, not a clause.
        lowered = [w for w in words if w[:1].islower()]
        return len(lowered) >= 2
    return len(words) >= 6


def audit(evidence: Iterable[FiniteClauseEvidence]) -> dict[str, Any]:
    rows = list(evidence)
    by_state: dict[str, int] = {}
    by_script: dict[str, int] = {}
    for row in rows:
        by_state[row.state] = by_state.get(row.state, 0) + 1
        by_script[row.language_or_script] = by_script.get(row.language_or_script, 0) + 1
    return {
        "spans": len(rows),
        "by_state": dict(sorted(by_state.items())),
        "by_script": dict(sorted(by_script.items())),
        "established_without_any_dimension": sum(
            1 for row in rows if row.establishes and not row.establishing_dimensions
            and not row.reason.startswith("the role splitter")),
        "establishing_dimensions": {
            name: sum(1 for row in rows if name in row.establishing_dimensions)
            for name in _ESTABLISHING},
    }


__all__ = [
    "ClauseViolation", "CLAUSE_STATES", "GOVERNING_CONTEXT_CHARS",
    "FiniteClauseEvidence", "detect", "script_of", "audit",
]
