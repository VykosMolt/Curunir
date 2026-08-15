"""V5.8.1 §4–§13 — D25 role binding: which spans carry which semantic role.

``clauses.py`` answers whether a span predicates.  It says nothing about *which
words* are the subject and which are the predicate, and that is why repairing it
moved 119 spans from REJECTED to QUARANTINED rather than to ACCEPTED: production
could see that an Arabic provision asserts something and still could not name
the thing asserting it.  The V5.2 role splitter that does the naming is built on
a four-language lexicon, subject-before-predicate order, and an overt copula —
three assumptions that Arabic verbal sentences, Russian zero-copula clauses and
Romance recitals each violate in a different way.

This module binds roles from typed evidence and records where every role came
from.  Three commitments run through it:

  * a role that cannot be bound defensibly stays UNRESOLVED.  Nothing here ever
    substitutes the nearest organisation for an unbound anaphor, which is the
    single most damaging thing a role binder can do;
  * grammatical subject, semantic actor, institutional issuer, attribution
    source and quoted speaker are five different questions with five different
    answers, and a passive patient is never silently promoted to actor;
  * an inherited role carries its lineage.  A list item that borrows its
    predicate from the lead-in says so, rather than quietly containing text it
    never had.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from ..v5_1.models import Record, now_utc, stable_id
from . import clauses as CL


class RoleBindingViolation(RuntimeError):
    """A role was bound without evidence, or an illegal combination was built."""


# ===========================================================================
# §5 — subject states
# ===========================================================================

SUBJECT_STATES: tuple[str, ...] = (
    "EXPLICIT_SUBJECT", "ZERO_COPULA_NOMINAL_SUBJECT", "POSTVERBAL_SUBJECT",
    "IMPLICIT_CONTEXT_BOUND_SUBJECT", "ANAPHORIC_SUBJECT_RECOVERABLE",
    "INHERITED_COORDINATE_SUBJECT", "GOVERNING_CLAUSE_SUBJECT",
    "PASSIVE_PATIENT_SUBJECT", "INSTITUTIONAL_ISSUER_SUBJECT",
    "EXPLETIVE_OR_IMPERSONAL_CONSTRUCTION", "SUBJECT_UNRESOLVED",
    "NO_SEMANTIC_SUBJECT",
)

#: Subject states that bind a span in the source.
SUBJECT_BOUND: frozenset[str] = frozenset({
    "EXPLICIT_SUBJECT", "ZERO_COPULA_NOMINAL_SUBJECT", "POSTVERBAL_SUBJECT",
    "PASSIVE_PATIENT_SUBJECT", "INSTITUTIONAL_ISSUER_SUBJECT",
})
#: Subject states that name a defensible repair rather than a span.
SUBJECT_RECOVERABLE: frozenset[str] = frozenset({
    "IMPLICIT_CONTEXT_BOUND_SUBJECT", "ANAPHORIC_SUBJECT_RECOVERABLE",
    "INHERITED_COORDINATE_SUBJECT", "GOVERNING_CLAUSE_SUBJECT",
})

# ===========================================================================
# §6 — predicate states
# ===========================================================================

PREDICATE_STATES: tuple[str, ...] = (
    "EXPLICIT_FINITE_PREDICATE", "NOMINAL_PREDICATE", "PARTICIPIAL_PREDICATE",
    "DEONTIC_OPERATOR_WITH_COMPLEMENT", "PASSIVE_OR_IMPERSONAL_PREDICATE",
    "SHARED_COORDINATE_PREDICATE", "GOVERNING_CLAUSE_PREDICATE",
    "RECITAL_RELATION_PREDICATE", "PREDICATE_RECOVERABLE",
    "PREDICATE_UNRESOLVED", "NO_SEMANTIC_PREDICATE",
)
PREDICATE_BOUND: frozenset[str] = frozenset({
    "EXPLICIT_FINITE_PREDICATE", "NOMINAL_PREDICATE", "PARTICIPIAL_PREDICATE",
    "DEONTIC_OPERATOR_WITH_COMPLEMENT", "PASSIVE_OR_IMPERSONAL_PREDICATE",
    "RECITAL_RELATION_PREDICATE",
})
PREDICATE_RECOVERABLE_STATES: frozenset[str] = frozenset({
    "SHARED_COORDINATE_PREDICATE", "GOVERNING_CLAUSE_PREDICATE",
    "PREDICATE_RECOVERABLE",
})

# ===========================================================================
# §7 — terminal states
# ===========================================================================

ROLE_BINDING_STATES: tuple[str, ...] = (
    "ROLE_BINDING_ESTABLISHED", "ROLE_BINDING_RECOVERABLE",
    "ROLE_BINDING_PARTIAL", "ROLE_BINDING_UNRESOLVED", "ROLE_BINDING_INVALID",
)

ANTECEDENT_STATES: tuple[str, ...] = (
    "ANTECEDENT_ESTABLISHED", "ANTECEDENT_RECOVERABLE", "ANTECEDENT_AMBIGUOUS",
    "ANTECEDENT_ABSENT",
)

#: §12 — how far a bounded antecedent search may look.  Declared, capped, and
#: never widened to rescue a difficult unit.
ANTECEDENT_CONTEXT_CHARS = 600
MAX_SCAN_CHARS = 4000


# ===========================================================================
# Language-specific surface evidence
# ===========================================================================

_AR_VERB_HEAD = re.compile(r"^(?:و|ف|ل|س|ثم)?([يتنأ][ء-ي]{2,})$")
_AR_DEONTIC = re.compile(
    r"^(?:و|ف)?(يجب|يجوز|ينبغي|يتعين|يلزم|يحظر|يُحظر|يمنع|يُمنع|يشترط|يُشترط)$")
_AR_PASSIVE = re.compile(r"^(?:و|ف)?[يت]ُ[ء-ي]{2,}$")
_AR_DEFINITE = re.compile(r"^ال[ء-ي]{2,}")
_AR_PRONOUN = re.compile(r"^(هو|هي|هم|هن|ذلك|هذا|هذه|تلك|هؤلاء)$")
_AR_NEG = re.compile(r"^(لا|لم|لن|ما|ليس|ليست)$")
_AR_PREP = re.compile(r"^(على|في|من|إلى|عن|مع|بين|خلال|وفق|وفقا|وفقاً|بموجب)$")

_RU_SHORT_FORM = re.compile(
    r"^\w{3,}(ён|ен|ена|ено|ены|ан|ана|ано|аны|им|има|имо|имы|ят|ята)$",
    re.IGNORECASE)
_RU_REFLEXIVE = re.compile(r"^\w{4,}(ся|сь)$", re.IGNORECASE)
_RU_MODAL = re.compile(
    r"^(должен|должна|должно|должны|может|можно|могут|обязан|обязана|обязано|"
    r"обязаны|вправе|следует|надлежит|нельзя|необходимо|запрещено)$",
    re.IGNORECASE)
_RU_COPULA = re.compile(
    r"^(является|являются|являлся|был|была|были|было|будет|будут|есть|"
    r"стал|стала|стало|стали|считается|признается|признаётся)$", re.IGNORECASE)
_RU_FINITE = re.compile(
    r"^\w{3,}(ет|ёт|ут|ют|ит|ат|ят|ешь|ишь|ем|им|ете|ите|ал|ала|али|ало|"
    r"ил|ила|или|ило)$", re.IGNORECASE)
_RU_NOMINATIVE = re.compile(r"^\w{3,}(ий|ый|ой|ая|яя|ое|ее|ые|ие|ь|й|а|я|о|е)?$",
                            re.IGNORECASE)
_RU_OBLIQUE = re.compile(r"\w{4,}(ого|ому|ыми|ими|ами|ями|ах|ях|ой|ей|ию|у|ю)$",
                         re.IGNORECASE)
_RU_PRONOUN = re.compile(r"^(он|она|оно|они|это|этот|эта|эти|тот|та|те)$",
                         re.IGNORECASE)

_LAT_FINITE = re.compile(
    r"^(is|are|was|were|be|been|has|have|had|does|do|did|shall|will|would|"
    r"can|could|may|might|must|ought|ist|sind|war|waren|wird|werden|wurde|"
    r"wurden|hat|haben|kann|können|muss|müssen|soll|sollen|darf|dürfen|"
    r"est|sont|était|étaient|sera|seront|a|ont|peut|peuvent|doit|doivent|"
    r"es|son|era|eran|será|serán|ha|han|puede|pueden|debe|deben)$",
    re.IGNORECASE)
_LAT_DEONTIC = re.compile(
    r"^(shall|must|may|should|ought|soll|sollen|muss|müssen|darf|dürfen|"
    r"doit|doivent|peut|peuvent|debe|deben|puede|pueden|podrá|podrán)$",
    re.IGNORECASE)
_LAT_PARTICIPLE = re.compile(
    r"^\w{4,}(ing|ed|ando|iendo|ant|ent|é|ée|és|ées|ado|ada|ido|ida|t|en)$",
    re.IGNORECASE)
_LAT_PRONOUN = re.compile(
    r"^(it|they|he|she|this|that|these|those|such|es|sie|er|dies|diese|"
    r"il|elle|ils|elles|ce|cette|este|esta|esto|estos|ella|ello)$",
    re.IGNORECASE)
_LAT_ARTICLE = re.compile(
    r"^(the|a|an|der|die|das|den|dem|des|ein|eine|einen|le|la|les|un|une|"
    r"des|du|el|los|las|una|unos|unas)$", re.IGNORECASE)
_LAT_PASSIVE_AUX = re.compile(r"^(is|are|was|were|been|being|wird|werden|wurde|"
                              r"wurden|est|sont|été|es|son|fue|fueron)$",
                              re.IGNORECASE)
_EXPLETIVE = re.compile(r"^(there|it|es|il|se|hay)$", re.IGNORECASE)

#: An institution named as issuer.  Structural: a capitalised or definite
#: nominal carrying an organisational head word, in the corpus languages.
_INSTITUTION_HEAD = re.compile(
    r"\b(organization|organisation|commission|council|parliament|agency|"
    r"authority|ministry|ministerium|ministère|ministerio|office|bureau|"
    r"secretariat|court|tribunal|assembly|committee|department|directorate|"
    r"bundesamt|behörde|organisme|organización|organizacion|"
    r"منظمة|الأمانة|المحكمة|الوزارة|الهيئة|اللجنة|المجلس|"
    r"организация|министерство|правительство|комитет|совет|суд|"
    r"федеральн\w*|ведомств\w*)\b", re.IGNORECASE)

_ATTRIBUTION_MARKER = re.compile(
    r"\b(according to|as reported by|stated by|said|reported|announced|"
    r"laut|zufolge|nach angaben|selon|d'après|según|de acuerdo con|"
    r"وفقاً ل|بحسب|حسب|أعلن|صرح|"
    r"по данным|согласно|как сообщает|заявил)\b", re.IGNORECASE)
_QUOTE_MARK = re.compile(r"[\"“”«»„‟]")

_COORD = re.compile(r"^(and|or|und|oder|et|ou|y|e|o|و|أو|и|или)$", re.IGNORECASE)
_ENUM_HEAD = re.compile(r"^\s*(?:\(?\d{1,3}[.)]|[a-z][.)]|[-–—•])\s+")


def _tokens(text: str) -> list[tuple[str, int, int]]:
    """Words with their source offsets.  Offsets are never recomputed later."""
    return [(match.group(0), match.start(), match.end())
            for match in re.finditer(r"\S+", text[:MAX_SCAN_CHARS])]


def _clean(word: str) -> str:
    return word.strip(".,;:()[]«»\"'“”„‟؛،—–-")


# ===========================================================================
# The binding record
# ===========================================================================

@dataclass(frozen=True)
class ClauseRoleBinding(Record):
    """§4 — which source spans carry which role, and on what evidence."""

    binding_id: str
    candidate_id: str
    clause_state: str

    subject_state: str
    subject_span: tuple[int, int] | None
    subject_source: str
    subject_confidence: float

    predicate_state: str
    predicate_span: tuple[int, int] | None
    predicate_head: str
    predicate_complement: str
    predicate_source: str
    predicate_confidence: float

    governing_clause_id: str | None
    shared_predicate_source: str | None
    shared_subject_source: str | None

    semantic_actor_state: str
    semantic_actor_span: tuple[int, int] | None
    attribution_state: str
    attribution_span: tuple[int, int] | None

    role_binding_state: str
    repair_requirement: str | None
    required_context_ids: tuple[str, ...]
    binding_confidence: float
    binding_reason: str
    language_or_script: str
    recorded_time: str

    def text_of(self, source: str, span: tuple[int, int] | None) -> str:
        return source[span[0]:span[1]] if span else ""

    def __post_init__(self) -> None:
        # §18 — illegal combinations are refused at construction rather than
        # found later in an audit.
        if self.role_binding_state == "ROLE_BINDING_ESTABLISHED":
            if self.subject_state in ("SUBJECT_UNRESOLVED",):
                raise RoleBindingViolation(
                    "ROLE_BINDING_ESTABLISHED with SUBJECT_UNRESOLVED")
            if self.predicate_state in ("PREDICATE_UNRESOLVED",
                                        "NO_SEMANTIC_PREDICATE"):
                raise RoleBindingViolation(
                    "ROLE_BINDING_ESTABLISHED with an unbound predicate")
        if self.subject_state == "NO_SEMANTIC_SUBJECT" and self.subject_span:
            raise RoleBindingViolation(
                "NO_SEMANTIC_SUBJECT with an explicit subject span")
        if self.subject_state in SUBJECT_RECOVERABLE and not self.repair_requirement:
            raise RoleBindingViolation(
                f"{self.subject_state} without a stated repair requirement")
        if self.subject_state == "PASSIVE_PATIENT_SUBJECT" and \
                self.semantic_actor_span is not None and \
                self.semantic_actor_span == self.subject_span:
            raise RoleBindingViolation(
                "the passive patient was reused as the semantic actor")


# ===========================================================================
# §12 — bounded antecedent resolution
# ===========================================================================

@dataclass(frozen=True)
class AntecedentResult(Record):
    state: str
    span: tuple[int, int] | None
    text: str
    score: float
    competitors: int
    reason: str


def resolve_antecedent(*, anaphor: str, left_context: str,
                       region_id: str = "", heading: str = "") -> AntecedentResult:
    """Find a defensible antecedent, or decline to.

    Nearest-entity selection is deliberately not implemented.  On the V5.6
    corpus it was the mechanism that put "Unadjusted data" and "Importers who"
    into speaker positions, and an ambiguous anaphor that stays ambiguous costs
    a quarantine; one that is force-resolved costs a wrong attribution.
    """
    window = left_context[-ANTECEDENT_CONTEXT_CHARS:]
    candidates: list[tuple[float, tuple[int, int], str]] = []
    offset = len(left_context) - len(window)
    for match in re.finditer(r"[A-ZÀ-ÖØ-Þء-يА-Я][^.;:!?\n]{2,80}",
                             window):
        text = _clean(match.group(0)).strip()
        if not text or len(text.split()) > 12:
            continue
        score = 0.3
        if _INSTITUTION_HEAD.search(text):
            score += 0.4
        if heading and text.casefold() in heading.casefold():
            score += 0.1
        if len(text.split()) >= 2:
            score += 0.1
        candidates.append((score, (offset + match.start(), offset + match.end()),
                           text))
    if not candidates:
        return AntecedentResult("ANTECEDENT_ABSENT", None, "", 0.0, 0,
                                "no antecedent in the bounded window")
    candidates.sort(key=lambda item: (-item[0], item[1][0]))
    best = candidates[0]
    rivals = [c for c in candidates[1:] if abs(c[0] - best[0]) < 0.15]
    if rivals:
        # §12 — an ambiguous antecedent must remain unresolved.  Choosing the
        # nearest of several equally good candidates is how a wrong actor gets
        # written into a report with full confidence.
        return AntecedentResult(
            "ANTECEDENT_AMBIGUOUS", None, "", best[0], len(rivals) + 1,
            f"{len(rivals) + 1} candidates within 0.15 of each other; the "
            "source does not decide between them")
    state = ("ANTECEDENT_ESTABLISHED" if best[0] >= 0.7
             else "ANTECEDENT_RECOVERABLE")
    return AntecedentResult(state, best[1], best[2], best[0], len(candidates),
                            f"single dominant candidate scoring {best[0]:.2f}")


# ===========================================================================
# Per-script binding
# ===========================================================================

def _bind_arabic(text: str, tokens: Sequence[tuple[str, int, int]]) -> dict[str, Any]:
    """Arabic: verb-initial by default, subject after the verb, no copula."""
    words = [(_clean(w), s, e) for w, s, e in tokens]
    head_index = None
    for index, (word, _, _) in enumerate(words[:6]):
        if _AR_NEG.match(word) or _AR_PREP.match(word):
            continue
        if _AR_DEONTIC.match(word) or _AR_PASSIVE.match(word) or \
                _AR_VERB_HEAD.match(word):
            head_index = index
            break
    if head_index is not None:
        word, start, end = words[head_index]
        deontic = bool(_AR_DEONTIC.match(word))
        passive = bool(_AR_PASSIVE.match(word))
        # The subject of a verbal sentence follows the verb, skipping any
        # preposition phrase the verb governs.  Assuming the first noun phrase
        # is the subject is the Latin-order assumption in disguise.
        subject = None
        cursor = head_index + 1
        while cursor < len(words) and _AR_PREP.match(words[cursor][0]):
            cursor += 2
        if cursor < len(words) and _AR_DEFINITE.match(words[cursor][0]):
            subject = words[cursor]
        complement = " ".join(w for w, _, _ in words[head_index + 1:])
        return {
            "predicate_state": ("DEONTIC_OPERATOR_WITH_COMPLEMENT" if deontic
                                else "PASSIVE_OR_IMPERSONAL_PREDICATE" if passive
                                else "EXPLICIT_FINITE_PREDICATE"),
            "predicate_span": (start, end), "predicate_head": word,
            "predicate_complement": complement[:200],
            "subject_state": ("PASSIVE_PATIENT_SUBJECT" if passive and subject
                              else "POSTVERBAL_SUBJECT" if subject
                              else "IMPLICIT_CONTEXT_BOUND_SUBJECT"),
            "subject_span": (subject[1], subject[2]) if subject else None,
            "confidence": 0.85 if subject else 0.6,
        }
    # Nominal clause: definite topic, then the predicate, with no verb at all.
    definites = [(w, s, e) for w, s, e in words if _AR_DEFINITE.match(w)]
    if len(definites) >= 1 and len(words) >= 5:
        topic = definites[0]
        rest_start = topic[2]
        return {
            "predicate_state": "NOMINAL_PREDICATE",
            "predicate_span": (rest_start, tokens[-1][2]),
            "predicate_head": words[min(len(words) - 1, 1)][0],
            "predicate_complement": text[rest_start:][:200].strip(),
            "subject_state": "ZERO_COPULA_NOMINAL_SUBJECT",
            "subject_span": (topic[1], topic[2]), "confidence": 0.75,
        }
    return {"predicate_state": "PREDICATE_UNRESOLVED", "predicate_span": None,
            "predicate_head": "", "predicate_complement": "",
            "subject_state": "SUBJECT_UNRESOLVED", "subject_span": None,
            "confidence": 0.2}


def _bind_russian(text: str, tokens: Sequence[tuple[str, int, int]]) -> dict[str, Any]:
    """Russian: free order, zero copula, short forms, reflexive passives."""
    words = [(_clean(w), s, e) for w, s, e in tokens]
    predicate_index = None
    kind = None
    for index, (word, _, _) in enumerate(words):
        if _RU_MODAL.match(word):
            predicate_index, kind = index, "DEONTIC_OPERATOR_WITH_COMPLEMENT"
            break
        # The copula is tested before the reflexive: "являются" ends in -ся
        # and is not a reflexive passive, and reading it as one turned every
        # copular clause into a passive with a patient subject.
        if _RU_COPULA.match(word):
            predicate_index, kind = index, "EXPLICIT_FINITE_PREDICATE"
            break
        if _RU_REFLEXIVE.match(word):
            predicate_index, kind = index, "PASSIVE_OR_IMPERSONAL_PREDICATE"
            break
        if _RU_SHORT_FORM.match(word):
            predicate_index, kind = index, "PARTICIPIAL_PREDICATE"
            break
        if _RU_FINITE.match(word):
            predicate_index, kind = index, "EXPLICIT_FINITE_PREDICATE"
            break
    if predicate_index is None:
        if len(words) >= 5:
            return {"predicate_state": "NOMINAL_PREDICATE",
                    "predicate_span": (words[-1][1], words[-1][2]),
                    "predicate_head": words[-1][0],
                    "predicate_complement": "",
                    "subject_state": "ZERO_COPULA_NOMINAL_SUBJECT",
                    "subject_span": (words[0][1], words[0][2]), "confidence": 0.6}
        return {"predicate_state": "PREDICATE_UNRESOLVED", "predicate_span": None,
                "predicate_head": "", "predicate_complement": "",
                "subject_state": "SUBJECT_UNRESOLVED", "subject_span": None,
                "confidence": 0.2}
    word, start, end = words[predicate_index]
    # The subject is the nearest non-oblique nominal to the left; if there is
    # none, Russian legal register routinely omits it entirely.
    subject = None
    for candidate in reversed(words[:predicate_index]):
        if _RU_OBLIQUE.search(candidate[0]) or _RU_PRONOUN.match(candidate[0]):
            continue
        if _RU_NOMINATIVE.match(candidate[0]) and len(candidate[0]) > 2:
            subject = candidate
            break
    passive = kind == "PASSIVE_OR_IMPERSONAL_PREDICATE"
    return {
        "predicate_state": kind, "predicate_span": (start, end),
        "predicate_head": word,
        "predicate_complement": text[end:][:200].strip(),
        "subject_state": ("PASSIVE_PATIENT_SUBJECT" if passive and subject
                          else "EXPLICIT_SUBJECT" if subject
                          else "EXPLETIVE_OR_IMPERSONAL_CONSTRUCTION" if passive
                          else "IMPLICIT_CONTEXT_BOUND_SUBJECT"),
        "subject_span": (subject[1], subject[2]) if subject else None,
        "confidence": 0.85 if subject else 0.65,
    }


def _bind_latin(text: str, tokens: Sequence[tuple[str, int, int]]) -> dict[str, Any]:
    words = [(_clean(w), s, e) for w, s, e in tokens]
    predicate_index, kind = None, None
    for index, (word, _, _) in enumerate(words):
        if _LAT_DEONTIC.match(word):
            predicate_index, kind = index, "DEONTIC_OPERATOR_WITH_COMPLEMENT"
            break
        if _LAT_FINITE.match(word):
            following = words[index + 1][0] if index + 1 < len(words) else ""
            passive = bool(_LAT_PASSIVE_AUX.match(word)
                           and _LAT_PARTICIPLE.match(following))
            predicate_index = index
            kind = ("PASSIVE_OR_IMPERSONAL_PREDICATE" if passive
                    else "EXPLICIT_FINITE_PREDICATE")
            break
    if predicate_index is None:
        # The closed finite-verb list is exactly the brittleness this module
        # replaces, so its miss is not a verdict.  Where the span has a
        # plausible verb position after a nominal head, the predicate is
        # recoverable rather than absent.
        for index, (word, start, end) in enumerate(words[1:6], start=1):
            if len(words) < 6:
                break
            if _LAT_ARTICLE.match(word) or _LAT_PRONOUN.match(word):
                continue
            if _LAT_PARTICIPLE.match(word) or word.lower().endswith(("s", "ed")):
                return {"predicate_state": "PREDICATE_RECOVERABLE",
                        "predicate_span": (start, end), "predicate_head": word,
                        "predicate_complement": text[end:][:200].strip(),
                        "subject_state": ("ANAPHORIC_SUBJECT_RECOVERABLE"
                                          if _LAT_PRONOUN.match(words[0][0])
                                          else "EXPLICIT_SUBJECT"),
                        "subject_span": (words[0][1], words[0][2]),
                        "confidence": 0.55}
        if words and _LAT_PARTICIPLE.match(words[0][0]) and len(words) >= 6:
            return {"predicate_state": "RECITAL_RELATION_PREDICATE",
                    "predicate_span": (words[0][1], words[0][2]),
                    "predicate_head": words[0][0],
                    "predicate_complement": text[words[0][2]:][:200].strip(),
                    "subject_state": "GOVERNING_CLAUSE_SUBJECT",
                    "subject_span": None, "confidence": 0.6}
        return {"predicate_state": "PREDICATE_UNRESOLVED", "predicate_span": None,
                "predicate_head": "", "predicate_complement": "",
                "subject_state": "SUBJECT_UNRESOLVED", "subject_span": None,
                "confidence": 0.2}
    word, start, end = words[predicate_index]
    subject = None
    for candidate in reversed(words[:predicate_index]):
        if _LAT_ARTICLE.match(candidate[0]):
            continue
        subject = candidate
        break
    if subject is not None and predicate_index > 0:
        subject = (text[words[0][1]:subject[2]], words[0][1], subject[2])
    passive = kind == "PASSIVE_OR_IMPERSONAL_PREDICATE"
    expletive = bool(words and _EXPLETIVE.match(words[0][0]))
    anaphoric = bool(words and _LAT_PRONOUN.match(words[0][0]))
    return {
        "predicate_state": kind, "predicate_span": (start, end),
        "predicate_head": word, "predicate_complement": text[end:][:200].strip(),
        "subject_state": ("EXPLETIVE_OR_IMPERSONAL_CONSTRUCTION" if expletive
                          else "ANAPHORIC_SUBJECT_RECOVERABLE" if anaphoric
                          else "PASSIVE_PATIENT_SUBJECT" if passive and subject
                          else "EXPLICIT_SUBJECT" if subject
                          else "IMPLICIT_CONTEXT_BOUND_SUBJECT"),
        "subject_span": (subject[1], subject[2]) if subject and not anaphoric
                        and not expletive else
                        (words[0][1], words[0][2]) if anaphoric or expletive
                        else None,
        "confidence": 0.85 if subject else 0.6,
    }


# ===========================================================================
# §4–§11 — the binder
# ===========================================================================

def bind(*, candidate_id: str, text: str, language: str | None = None,
         left_context: str = "", heading: str = "",
         governing_clause_id: str | None = None,
         shared_subject_source: str | None = None,
         shared_predicate_source: str | None = None,
         clause_evidence: CL.FiniteClauseEvidence | None = None
         ) -> ClauseRoleBinding:
    """Bind the roles of one span, recording where each came from."""
    body = (text or "")[:MAX_SCAN_CHARS]
    evidence = clause_evidence or CL.detect(body, language=language)
    tokens = _tokens(body)
    script = evidence.language_or_script

    if not tokens:
        return _unresolved(candidate_id, evidence, "empty span")

    if script == "ARABIC":
        bound = _bind_arabic(body, tokens)
    elif script == "CYRILLIC":
        bound = _bind_russian(body, tokens)
    else:
        bound = _bind_latin(body, tokens)

    subject_state = bound["subject_state"]
    predicate_state = bound["predicate_state"]
    subject_span = bound["subject_span"]
    predicate_span = bound["predicate_span"]
    required_context: list[str] = []
    repair: str | None = None
    subject_source = "RECORDED_SPAN" if subject_span else "NOT_BOUND"
    predicate_source = "RECORDED_SPAN" if predicate_span else "NOT_BOUND"

    # §11 — inherited roles carry lineage; they never silently become local.
    if predicate_state in ("PREDICATE_UNRESOLVED", "PREDICATE_RECOVERABLE") \
            and shared_predicate_source:
        predicate_state = "SHARED_COORDINATE_PREDICATE"
        predicate_source = f"SHARED_FROM:{shared_predicate_source}"
        repair = "bind the predicate from the coordinating lead-in"
        required_context.append(shared_predicate_source)
    if subject_state in ("SUBJECT_UNRESOLVED", "EXPLICIT_SUBJECT") \
            and shared_subject_source and not subject_span:
        subject_state = "INHERITED_COORDINATE_SUBJECT"
        subject_source = f"SHARED_FROM:{shared_subject_source}"
        repair = (repair or "") + "; inherit the subject from the coordinate head"
        required_context.append(shared_subject_source)
    if governing_clause_id and predicate_state in (
            "PREDICATE_UNRESOLVED", "PREDICATE_RECOVERABLE",
            "RECITAL_RELATION_PREDICATE"):
        if predicate_state in ("PREDICATE_UNRESOLVED", "PREDICATE_RECOVERABLE"):
            predicate_state = "GOVERNING_CLAUSE_PREDICATE"
            predicate_source = f"GOVERNING:{governing_clause_id}"
        if subject_state in ("SUBJECT_UNRESOLVED", "GOVERNING_CLAUSE_SUBJECT",
                             "EXPLICIT_SUBJECT"):
            subject_state = "GOVERNING_CLAUSE_SUBJECT"
            subject_span = None
            subject_source = f"GOVERNING:{governing_clause_id}"
        repair = "bind roles from the governing enactment clause"
        required_context.append(governing_clause_id)

    # §12 — an anaphoric subject is repaired only when the antecedent is
    # defensible.  Ambiguity stays ambiguous.
    if subject_state == "ANAPHORIC_SUBJECT_RECOVERABLE":
        antecedent = resolve_antecedent(
            anaphor=body[subject_span[0]:subject_span[1]] if subject_span else "",
            left_context=left_context, heading=heading)
        if antecedent.state == "ANTECEDENT_AMBIGUOUS":
            subject_state = "SUBJECT_UNRESOLVED"
            subject_span = None
            subject_source = "AMBIGUOUS_ANTECEDENT"
            repair = None
        elif antecedent.state == "ANTECEDENT_ABSENT":
            subject_state = "SUBJECT_UNRESOLVED"
            subject_span = None
            subject_source = "NO_ANTECEDENT_IN_BOUNDED_CONTEXT"
            repair = None
        else:
            subject_source = f"BOUNDED_ANTECEDENT:{antecedent.state}"
            repair = "expand left context to the resolved antecedent"
            required_context.append("LEFT_CONTEXT")

    # §5.1 — five different questions, five different answers.
    actor_state, actor_span = _semantic_actor(body, tokens, subject_state,
                                              subject_span, predicate_state)
    attribution_state, attribution_span = _attribution(body, tokens)

    state, reason = _terminal(subject_state, predicate_state, evidence.state)
    if state in ("ROLE_BINDING_RECOVERABLE",) and repair is None:
        repair = "recover the missing role from the declared bounded context"
    if subject_state in SUBJECT_RECOVERABLE and repair is None:
        repair = "recover the subject from the declared bounded context"

    confidence = round(min(bound["confidence"],
                           evidence.clause_boundary_confidence + 0.15), 4)
    return ClauseRoleBinding(
        stable_id("v5-8-1-binding", candidate_id, state),
        candidate_id, evidence.state,
        subject_state, subject_span, subject_source,
        round(bound["confidence"], 4),
        predicate_state, predicate_span, bound["predicate_head"],
        bound["predicate_complement"], predicate_source,
        round(bound["confidence"], 4),
        governing_clause_id, shared_predicate_source, shared_subject_source,
        actor_state, actor_span, attribution_state, attribution_span,
        state, repair, tuple(dict.fromkeys(required_context)), confidence,
        reason, script, now_utc())


def _semantic_actor(text: str, tokens, subject_state: str,
                    subject_span, predicate_state: str):
    """The actor is not the grammatical subject when the clause is passive."""
    if subject_state == "PASSIVE_PATIENT_SUBJECT":
        # The agent, if stated at all, arrives in a by-phrase.  Where it is not
        # stated the actor is unresolved — never the patient.
        match = re.search(r"\b(?:by|von|durch|par|por|بواسطة|من قبل)\s+(\S.{2,60})",
                          text, re.IGNORECASE)
        if match:
            return "SEMANTIC_ACTOR_EXPLICIT", (match.start(1), match.end(1))
        return "SEMANTIC_ACTOR_UNRESOLVED", None
    if subject_state == "EXPLETIVE_OR_IMPERSONAL_CONSTRUCTION":
        return "SEMANTIC_ACTOR_UNRESOLVED", None
    if subject_state in SUBJECT_BOUND and subject_span:
        span_text = text[subject_span[0]:subject_span[1]]
        if _INSTITUTION_HEAD.search(span_text):
            return "SEMANTIC_ACTOR_INSTITUTIONAL", subject_span
        return "SEMANTIC_ACTOR_IS_GRAMMATICAL_SUBJECT", subject_span
    return "SEMANTIC_ACTOR_UNRESOLVED", None


def _attribution(text: str, tokens):
    match = _ATTRIBUTION_MARKER.search(text)
    if match:
        tail = re.match(r"\s*(\S.{2,80}?)(?:[,.;]|$)", text[match.end():])
        if tail:
            return ("ATTRIBUTION_EXPLICIT",
                    (match.end() + tail.start(1), match.end() + tail.end(1)))
        return "ATTRIBUTION_MARKER_WITHOUT_SOURCE", None
    if _QUOTE_MARK.search(text):
        return "QUOTED_SPEAKER_UNRESOLVED", None
    return "ATTRIBUTION_ABSENT", None


def _terminal(subject_state: str, predicate_state: str, clause_state: str):
    subject_bound = subject_state in SUBJECT_BOUND
    subject_recoverable = subject_state in SUBJECT_RECOVERABLE
    predicate_bound = predicate_state in PREDICATE_BOUND
    predicate_recoverable = predicate_state in PREDICATE_RECOVERABLE_STATES

    if subject_bound and predicate_bound:
        return ("ROLE_BINDING_ESTABLISHED",
                f"{subject_state} and {predicate_state} both bound to source spans")
    if subject_state == "EXPLETIVE_OR_IMPERSONAL_CONSTRUCTION" and predicate_bound:
        return ("ROLE_BINDING_ESTABLISHED",
                "an impersonal construction has no semantic subject to bind, and "
                "the predicate is bound")
    if (subject_bound or subject_recoverable) and (predicate_bound
                                                   or predicate_recoverable):
        if subject_recoverable or predicate_recoverable:
            return ("ROLE_BINDING_RECOVERABLE",
                    "every role is either bound or recoverable from declared "
                    "bounded context")
    if clause_state == "NOT_A_FINITE_CLAUSE" and not (predicate_bound
                                                     or predicate_recoverable):
        # §7 — INVALID means contradicted, not merely unrecognised.  The two
        # layers are independent by design, so when the binder holds evidence
        # the clause detector lacked, the honest state is UNRESOLVED.  Letting
        # the detector's silence veto the binder would rebuild the single
        # brittle authority both layers exist to replace.
        return ("ROLE_BINDING_INVALID",
                "neither layer finds predication, and no role assignment is "
                "supportable")
    if predicate_bound and subject_state == "SUBJECT_UNRESOLVED":
        return ("ROLE_BINDING_PARTIAL",
                "the predicate is bound; the subject is not, and the source does "
                "not decide it")
    if subject_bound and not predicate_bound:
        return ("ROLE_BINDING_PARTIAL",
                "the subject is bound; the predicate is not")
    return ("ROLE_BINDING_UNRESOLVED",
            "predication exists but the source does not permit a defensible "
            "role assignment")


def _unresolved(candidate_id: str, evidence, reason: str) -> ClauseRoleBinding:
    return ClauseRoleBinding(
        stable_id("v5-8-1-binding", candidate_id, "UNRESOLVED"), candidate_id,
        evidence.state, "SUBJECT_UNRESOLVED", None, "NOT_BOUND", 0.0,
        "PREDICATE_UNRESOLVED", None, "", "", "NOT_BOUND", 0.0,
        None, None, None, "SEMANTIC_ACTOR_UNRESOLVED", None,
        "ATTRIBUTION_ABSENT", None, "ROLE_BINDING_UNRESOLVED", None, (), 0.0,
        reason, evidence.language_or_script, now_utc())


def audit(bindings: Iterable[ClauseRoleBinding]) -> dict[str, Any]:
    rows = list(bindings)

    def tally(attribute: str) -> dict[str, int]:
        out: dict[str, int] = {}
        for row in rows:
            value = getattr(row, attribute)
            out[value] = out.get(value, 0) + 1
        return dict(sorted(out.items()))

    return {
        "bindings": len(rows),
        "role_binding_state": tally("role_binding_state"),
        "subject_state": tally("subject_state"),
        "predicate_state": tally("predicate_state"),
        "semantic_actor_state": tally("semantic_actor_state"),
        "attribution_state": tally("attribution_state"),
        "by_script": tally("language_or_script"),
        "established_without_a_bound_predicate": sum(
            1 for row in rows if row.role_binding_state == "ROLE_BINDING_ESTABLISHED"
            and row.predicate_span is None),
        "passive_patient_reused_as_actor": sum(
            1 for row in rows if row.subject_state == "PASSIVE_PATIENT_SUBJECT"
            and row.semantic_actor_span == row.subject_span
            and row.subject_span is not None),
        "recoverable_without_repair_requirement": sum(
            1 for row in rows if row.role_binding_state == "ROLE_BINDING_RECOVERABLE"
            and not row.repair_requirement),
    }


__all__ = [
    "RoleBindingViolation", "SUBJECT_STATES", "PREDICATE_STATES",
    "ROLE_BINDING_STATES", "ANTECEDENT_STATES", "ANTECEDENT_CONTEXT_CHARS",
    "ClauseRoleBinding", "AntecedentResult", "bind", "resolve_antecedent",
    "audit",
]
