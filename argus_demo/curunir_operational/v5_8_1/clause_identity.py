"""V5.8.1 — typed clause identity inside a span, and typed cross-clause licences.

D32 transports structural context BETWEEN regions.  Nothing in the pipeline says
where one clause ends and the next begins WITHIN a region, so the admissibility
layer had to approximate: it asked whether a single role span contained a
terminator character.  That question cannot see the defect it was aimed at.
Independent adjudication found 232 analyses reaching selection whose subject and
predicate belong to different clauses entirely -- a subject from one proposition
paired with a verb from the next -- and every one of them passed, because
neither span individually straddled a full stop.

This module supplies the missing representation.  It is deliberately not a
segmentation utility bolted onto admissibility.py: the pairing rule needs to
know not merely THAT two components sit in different clauses but WHICH clauses,
how those clauses are related, and whether a named, evidenced licence permits
the pairing anyway.  A subject in a matrix clause binding a predicate in the
relative clause it heads is lawful.  A subject from one independent sentence
binding a predicate in the next is not.  A boolean cannot tell them apart.

INDEPENDENCE FROM THE EVALUATOR
-------------------------------
The evaluation ruler segments clauses by counting boundary markers: it walks the
tokens, increments a counter on a subordinator or after terminal punctuation,
and returns the counter.  It cannot decide whether a coordinator joins two
clauses or two phrases, and says so in its own source -- it has no lexicon with
which to ask whether each side has its own finite verb, so it conservatively
never splits on one.

This module works the other way round.  It locates PREDICATION ANCHORS first,
using the multilingual finite-head resources this programme already owns in
`clauses.py`, and then projects clause extents around them.  Because it knows
where the finite heads are, it CAN decide the coordinator question: "A and B
shall report" is one clause, "A shall report and B shall decide" is two.

The two implementations therefore disagree by construction, and must be allowed
to.  That disagreement has already exposed four defects in the ruler and two in
production.  Nothing here may import the ruler, and the ruler may never import
this.

WHAT IS NOT A LICENCE
---------------------
Proximity.  Shared punctuation.  Membership of the same broad region.  The
evaluator having treated two clauses as one.  A licence is a typed relation
carrying evidence, an asserting component, a confidence and a decisiveness
state; anything less cannot be audited when one licence family starts
over-firing, which is the failure mode a boolean would hide.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, replace
from typing import Any

from ..v5_1.models import Record, now_utc, stable_id
from . import clauses as CL

#: Longest span this module will segment.  Matches clauses.MAX_SCAN_CHARS so the
#: two never disagree about how much text exists.
MAX_SCAN_CHARS = CL.MAX_SCAN_CHARS


class ClauseIdentityError(ValueError):
    """A clause, membership, relation or licence asserted outside its vocabulary."""


# ===========================================================================
# Vocabularies
# ===========================================================================

#: How a clause stands to its parent.  MATRIX is the root of a sentence; every
#: other value names the relation that subordinated it.
CLAUSE_RELATION_TYPES: tuple[str, ...] = (
    "MATRIX",
    "SUBORDINATE_COMPLEMENT",
    "ADVERBIAL_SUBORDINATE",
    "RELATIVE",
    "COORDINATE",
    "PARENTHETICAL",
    "QUOTED",
    "STRUCTURAL_CONTINUATION",
)

#: The seven licence families.  A cross-clause pairing is admissible only under
#: one of these, named explicitly and carrying its evidence.
LICENCE_TYPES: tuple[str, ...] = (
    "GOVERNING_SUBJECT_INHERITANCE",
    "CONTROL_RELATION",
    "RAISING_RELATION",
    "RELATIVE_CLAUSE_RELATION",
    "REPORTED_SPEECH_ATTRIBUTION",
    "TYPED_COORDINATION",
    "STRUCTURAL_CONTINUATION_OR_SHARED_ROLE",
)

#: Why a clause boundary was opened.  Recorded per clause so that a licence
#: family can be audited against the boundary that created the split.
BOUNDARY_EVENT_TYPES: tuple[str, ...] = (
    "SPAN_START",
    "TERMINAL_PUNCTUATION",
    "SUBORDINATOR",
    "RELATIVISER",
    "COORDINATOR_WITH_OWN_FINITE_HEAD",
    "ENCLOSURE_OPEN",
    "ENCLOSURE_CLOSE",
    "SEMICOLON_ENUMERATION",
    "MATRIX_RESUMPTION",
    "CORRELATIVE_APODOSIS",
)

#: How a candidate span sits relative to the clause structure.
MEMBERSHIP_STATES: tuple[str, ...] = (
    "FULLY_WITHIN_ONE_CLAUSE",
    "SPANS_MULTIPLE_CLAUSES",
    "NO_CLAUSE_RESOLVED",
)

DECISIVENESS: tuple[str, ...] = ("DECISIVE", "PROVISIONAL")


# ===========================================================================
# Predication anchors — the production-side finite-head resource
# ===========================================================================
#
# These are token-level detectors.  `clauses.py` answers "does this SPAN
# predicate"; this module needs "which TOKEN is the finite head", which is a
# different question over the same linguistic facts.  The lexical resources are
# shared with clauses.py wherever the pattern is already there, so production has
# one place where each language's predication vocabulary lives.

#: Latin-script auxiliaries and copulas.  Open-class finite morphology is
#: deliberately NOT guessed here: `clauses.py` records that a suffix set wide
#: enough to catch "spreads" also catches "Multilaterales" and "donnees", and a
#: signal that fires on plural nouns would split clauses at random.
_LAT_AUXILIARY = re.compile(
    r"^(is|are|was|were|be|been|being|has|have|had|do|does|did|"
    r"ist|sind|war|waren|wird|werden|wurde|wurden|hat|haben|hatte|hatten|sei|"
    r"est|sont|était|étaient|a|ont|avait|avaient|fut|furent|"
    r"es|son|era|eran|está|están|ha|han|había|habían|fue|fueron|"
    r"è|sono|era|erano|ha|hanno|aveva|avevano|fu|furono)$", re.IGNORECASE)

#: German verb-final and separable-prefix clusters end a subordinate clause with
#: the finite form last: "... nicht in der Lage ist".  Handled by the auxiliary
#: table above; listed here as the reason the table includes bare "ist"/"sind".

#: Russian: reuse the resources clauses.py already declares, applied per token.
_RU_FINITE_TOKEN = CL._RU_FINITE_SUFFIX
_RU_MODAL_TOKEN = re.compile(
    r"^(должен|должна|должно|должны|может|можно|могут|обязан|обязана|обязано|"
    r"обязаны|вправе|следует|надлежит|нельзя|необходимо)$", re.IGNORECASE)
_RU_COPULA_TOKEN = re.compile(
    r"^(является|являются|являлся|была|были|было|был|будет|будут|есть|стал|"
    r"стала|стало|стали)$", re.IGNORECASE)
_RU_IMPERSONAL_TOKEN = re.compile(
    r"^(запрещается|разрешается|устанавливается|определяется|применяется|"
    r"осуществляется|признаётся|признается|считается|подлежит|допускается|"
    r"предусматривается|регулируется|утверждается)$", re.IGNORECASE)

#: Arabic: an imperfect verb is marked at the head of the word.  The definite
#: article ال is not a verbal prefix, and clauses.py records what happens without
#: that exclusion -- المحتويات ("contents") read as a verb and a table-of-contents
#: heading was established as a proposition.
_AR_IMPERFECT_TOKEN = re.compile(r"^[يتنأ][ء-ي]{2,}$")
_AR_MODAL_TOKEN = re.compile(
    r"^(يجب|يجوز|ينبغي|يتعين|يلزم|يحظر|يُحظر|يمنع|يُمنع|يلتزم|تلتزم|يتولى|"
    r"تتولى|يعتبر|يُعتبر|تعتبر|تُعتبر|يعد|يُعد|يسري|تسري|يخضع|تخضع|يشترط|يُشترط)$")
_AR_COPULA_TOKEN = re.compile(r"^(يكون|تكون|كان|كانت|ليس|ليست|أصبح|أصبحت|صار|صارت)$")

#: Deontic modals in Latin script, from the register these documents are written
#: in.  Shared with clauses._LEGAL_MODAL in intent; written per token here.
_LAT_MODAL = re.compile(
    r"^(shall|must|may|should|can|could|might|will|would|"
    r"soll|sollen|muss|müssen|kann|können|darf|dürfen|wird|werden|möge|"
    r"doit|doivent|peut|peuvent|devra|devront|pourra|pourront|"
    r"debe|deben|puede|pueden|podrá|podrán|deberá|deberán|"
    r"deve|devono|può|possono|potrà|potranno|"
    # Unaccented variants.  Accents are lost to OCR and to plain-text
    # transcription often enough that omitting them left "debera presentar" with
    # no finite head at all, which stopped a relative clause from ever closing.
    # Only forms with no unaccented homograph are listed: "esta" is excluded
    # because it is also the demonstrative.
    r"podra|podran|debera|deberan|tendra|tendran|sera|seran|"
    r"potra|potranno|devra|devront|pourra|pourront)$", re.IGNORECASE)


# ===========================================================================
# Boundary vocabulary
# ===========================================================================

_SUBORDINATOR = re.compile(
    r"^(that|because|although|though|unless|whereas|while|whilst|since|"
    r"if|when|whenever|where|wherever|after|before|until|so|provided|"
    r"dass|daß|weil|obwohl|obgleich|wenn|falls|sofern|damit|bevor|nachdem|"
    r"während|solange|sobald|indem|"
    r"que|parce|puisque|bien|quoique|lorsque|si|quand|dès|afin|"
    r"porque|aunque|cuando|mientras|siempre|para|"
    r"perché|poiché|benché|sebbene|quando|mentre|affinché|"
    r"поскольку|если|когда|хотя|чтобы|пока|потому|так|"
    r"لأن|حيث|بينما|إذا|عندما|كي|لكي)$", re.IGNORECASE)

#: Relativisers.  der/die/das and que/che are also determiners; position and the
#: presence of a preceding nominal decide which, handled in `_boundary_events`.
_RELATIVISER = re.compile(
    r"^(who|whom|whose|which|that|"
    r"der|die|das|dem|den|dessen|deren|welcher|welche|welches|"
    r"que|qui|dont|lequel|laquelle|lesquels|lesquelles|"
    r"che|cui|quale|quali|"
    r"который|которая|которое|которые|которого|которой|"
    r"الذي|التي|الذين|اللاتي|اللواتي)$", re.IGNORECASE)

#: Relativisers that are also determiners, so position must decide.  "which" is
#: NOT in this set: its determiner use ("which report?") is interrogative and
#: essentially absent from this register, while pied-piped "to which he
#: contests", "in which the conditions apply" is everywhere in legal prose and
#: was being missed entirely.
_AMBIGUOUS_RELATIVISER = frozenset({
    "der", "die", "das", "dem", "den", "que", "che", "that"})

#: Tokens that subordinate only when followed by a complementiser.  On their own
#: they are prepositions or adverbs.
_NEEDS_COMPLEMENTISER = frozenset({
    "para", "siempre", "bien", "afin", "tandis", "so", "provided", "such",
    "given", "assuming", "puesto", "dado", "a", "prima",
})
_COMPLEMENTISERS = frozenset({"que", "that", "dass", "daß", "che", "qu'", "quo"})
#: Correlative adverbs that open a main clause after a fronted subordinate one.
_CORRELATIVE_APODOSIS = frozenset({"so", "dann", "alsdann", "sodann"})

#: A relativiser after a preposition is pied-piping -- "to which", "in der
#: Fassung ... auf die" -- and opens a relative clause.  The preposition guard
#: below exists for determiners, so it must not fire for relativisers that have
#: no determiner reading.
_UNAMBIGUOUS_RELATIVISER = re.compile(
    r"^(who|whom|whose|which|welcher|welche|welches|dessen|deren|"
    r"qui|dont|lequel|laquelle|lesquels|lesquelles|cui|quale|quali|"
    r"который|которая|которое|которые|которого|которой|"
    r"الذي|التي|الذين|اللاتي|اللواتي)$", re.IGNORECASE)

#: A determiner after a preposition is never a relativiser.  "in der Lage ist"
#: split into two clauses before this test existed, because "der" satisfied a
#: rule that only asked whether a finite verb appeared somewhere ahead.
_PREPOSITION = re.compile(
    r"^(in|on|at|to|of|for|with|by|from|into|über|unter|auf|an|aus|bei|mit|"
    r"nach|seit|von|vor|zu|zur|zum|durch|für|gegen|ohne|um|"
    r"dans|sur|sous|avec|sans|pour|par|chez|vers|"
    r"en|con|sin|para|por|sobre|entre|hacia|desde|"
    r"nel|nella|con|senza|per|tra|fra|su|"
    r"в|на|под|над|при|для|из|от|до|по|за|с|о|об|"
    r"في|على|من|إلى|عن|مع|بـ|لـ)$", re.IGNORECASE)

_COORDINATOR = re.compile(
    r"^(and|or|but|nor|yet|"
    r"und|oder|aber|sondern|denn|"
    r"et|ou|mais|or|donc|"
    r"y|e|o|u|pero|sino|"
    r"ma|oppure|però|"
    r"и|или|но|а|либо|"
    r"و|أو|لكن|بل)$", re.IGNORECASE)

#: Verbs whose complement clause shares the matrix subject.  Control and raising
#: are separated because they licence different things and must be auditable
#: apart: with control the matrix subject is an argument of both predicates;
#: with raising it is an argument only of the lower one.
_CONTROL_VERB = re.compile(
    r"^(agree|agrees|agreed|decide|decides|decided|undertake|undertakes|"
    r"undertook|intend|intends|intended|refuse|refuses|refused|promise|"
    r"promises|promised|attempt|attempts|attempted|try|tries|tried|"
    r"seek|seeks|sought|wish|wishes|wished|"
    r"beschließt|beschließen|beabsichtigt|beabsichtigen|verpflichtet|"
    r"décide|décident|s'engage|s'engagent|entend|entendent|"
    r"decide|deciden|acuerda|acuerdan|se compromete|"
    r"يقرر|تقرر|يتعهد|تتعهد|يعتزم|تعتزم)$", re.IGNORECASE)

_RAISING_VERB = re.compile(
    r"^(seem|seems|seemed|appear|appears|appeared|happen|happens|happened|"
    r"tend|tends|tended|prove|proves|proved|turn|turns|turned|"
    r"scheint|scheinen|pflegt|pflegen|"
    r"semble|semblent|paraît|paraissent|"
    r"parece|parecen|resulta|resultan|"
    r"sembra|sembrano|risulta|risultano)$", re.IGNORECASE)

_REPORTING_VERB = re.compile(
    r"^(say|says|said|state|states|stated|declare|declares|declared|"
    r"note|notes|noted|report|reports|reported|observe|observes|observed|"
    r"recall|recalls|recalled|affirm|affirms|affirmed|stress|stresses|stressed|"
    r"sagt|sagen|erklärt|erklären|stellt|stellen|betont|betonen|"
    r"déclare|déclarent|affirme|affirment|note|notent|rappelle|rappellent|"
    r"declara|declaran|afirma|afirman|señala|señalan|recuerda|recuerdan|"
    r"dichiara|dichiarano|afferma|affermano|"
    r"يقول|تقول|يعلن|تعلن|يذكر|تذكر|يؤكد|تؤكد)$", re.IGNORECASE)

#: Non-finite complement markers: the token that introduces a controlled or
#: raised predicate.  Their presence is the evidence a control/raising licence
#: carries; without one, neither licence may be asserted.
_INFINITIVE_MARKER = re.compile(
    r"^(to|zu|um|de|à|a|di|da|per|أن|ل)$", re.IGNORECASE)

#: Tokens that can open a subject noun phrase: determiners and pronouns.  An
#: adjective or a preposition cannot, and that difference is what distinguishes
#: clause coordination from phrase coordination in a verb-final language.
_SUBJECT_OPENER = re.compile(
    r"^(the|a|an|this|that|these|those|it|he|she|they|we|i|you|"
    r"der|die|das|dem|den|des|ein|eine|einer|eines|einem|einen|dieser|diese|"
    r"dieses|er|sie|es|wir|ihr|man|jeder|jede|jedes|kein|keine|"
    r"le|la|les|un|une|des|ce|cet|cette|ces|il|elle|ils|elles|nous|vous|on|"
    r"el|los|las|una|unos|unas|este|esta|estos|estas|él|ella|ellos|ellas|"
    r"il|lo|gli|uno|questa|questo|questi|queste|egli|essa|essi|"
    r"этот|эта|это|эти|он|она|оно|они|мы|вы|я|"
    r"هذا|هذه|هؤلاء|هو|هي|هم|نحن)$", re.IGNORECASE)

_TERMINAL = re.compile(r"[.!?؟。]$")
_OPENERS = "([{«„“‘"
_CLOSERS = ")]}»“”’"
_ENCLOSURE_PAIRS = {"(": ")", "[": "]", "{": "}", "«": "»", "„": "“",
                    "“": "”", "‘": "’"}
#: Marks that are their own closing partner, so parity decides.
_SYMMETRIC_QUOTE = frozenset({'"', "'", "ʼ"})
_SYMMETRIC_QUOTE_OPEN = "\x00SYMMETRIC"

#: Marks that are ALSO letters of a word.  U+2019 is the recommended apostrophe
#: of French, Italian and English typography ("L'expression", "dell'articolo",
#: "the operator's") AND the closing partner of U+2018; U+0027 is what plain-text
#: transcription and PDF text extraction leave of it; U+02BC is the
#: Unicode-recommended letter apostrophe.  U+0022 is deliberately NOT here: it
#: has no apostrophe reading in any language this module handles, and excluding
#: it keeps ordinary straight-quote quotation working exactly as before.
_APOSTROPHE_ALSO = frozenset({"'", "ʼ", "’"})

#: An enumerator or an abbreviation ends in a full stop without ending a clause:
#: "Art. 5", "1.", "No.".  The list is CLOSED on purpose.  A general rule of the
#: shape [A-Za-z]{1,4}\. was tried and reverted: it matches "vor.", "act.",
#: "note." -- any short word ending a sentence -- and so merged adjacent
#: propositions into one clause.  That silently re-admits precisely the
#: cross-sentence pairings this layer exists to remove, and it was caught by the
#: relative-clause mutation fixture rather than by inspection.
_ENUMERATOR_OR_ABBREVIATION = re.compile(
    r"^(?:\(?\d{1,3}[.)]|[a-zA-Z][.)]|"
    r"(?:art|artikel|article|arts|abs|abschn|nr|no|nos|bzw|ggf|vgl|etc|cf|"
    r"para|paras|sec|secs|fig|tab|ch|chap|p|pp|vol|ed|eds|al|approx|"
    r"resp|inkl|einschl|ff|f|s|z|u|i|d|v)\.)$",
    re.IGNORECASE)


#: Arabic writes several function words as PROCLITICS bound to the next word:
#: و (and), ف (so/then), ب (with), ل (for), ك (as).  "وعندما" is و + عندما, and
#: matching the subordinator inventory against the whole token missed every
#: clause opened that way -- including "وعندما يسعل الشخص ... ، ينتشر الرذاذ",
#: where the subordinate verb was then treated as the matrix predicate.
_AR_PROCLITIC = re.compile(r"^[وفبلك](?=[ء-ي]{3,})")


def _clean(word: str) -> str:
    return word.strip(".,;:()[]«»\"'“”„‟؛،—–-").casefold()


def _clean_forms(word: str) -> tuple[str, ...]:
    """The token, plus its form with an Arabic proclitic removed."""
    bare = _clean(word)
    stripped = _AR_PROCLITIC.sub("", bare)
    return (bare,) if stripped == bare else (bare, stripped)


def tokens(text: str) -> list[tuple[str, int, int]]:
    """Whitespace tokens with their character offsets."""
    return [(m.group(0), m.start(), m.end())
            for m in re.finditer(r"\S+", text[:MAX_SCAN_CHARS])]


# ===========================================================================
# Records
# ===========================================================================

@dataclass(frozen=True)
class BoundaryEvent(Record):
    """Why a clause boundary was opened, and on the strength of what."""

    event_type: str
    token_index: int
    character_offset: int
    trigger_text: str
    confidence: float

    def __post_init__(self) -> None:
        if self.event_type not in BOUNDARY_EVENT_TYPES:
            raise ClauseIdentityError(f"unknown boundary event {self.event_type!r}")


@dataclass(frozen=True)
class ClauseIdentity(Record):
    """One clause inside one region, with its extent, heads and parentage."""

    clause_id: str
    region_id: str
    token_start: int
    token_end: int
    character_start: int
    character_end: int
    finite_head_ids: tuple[str, ...] = ()
    finite_head_token_indices: tuple[int, ...] = ()
    subject_candidate_ids: tuple[str, ...] = ()
    predicate_candidate_ids: tuple[str, ...] = ()
    parent_clause_id: str | None = None
    relation_to_parent: str = "MATRIX"
    coordination_group_id: str | None = None
    enclosure_ids: tuple[str, ...] = ()
    structural_evidence_ids: tuple[str, ...] = ()
    opening_event: BoundaryEvent | None = None
    asserting_component: str = "v5_8_1.clause_identity"
    confidence: float = 0.0
    decisive_or_provisional: str = "PROVISIONAL"
    recorded_time: str = ""

    def __post_init__(self) -> None:
        if self.relation_to_parent not in CLAUSE_RELATION_TYPES:
            raise ClauseIdentityError(
                f"unknown clause relation {self.relation_to_parent!r}")
        if self.decisive_or_provisional not in DECISIVENESS:
            raise ClauseIdentityError(
                f"unknown decisiveness {self.decisive_or_provisional!r}")
        if self.token_end < self.token_start:
            raise ClauseIdentityError("clause ends before it starts")

    @property
    def has_finite_head(self) -> bool:
        return bool(self.finite_head_token_indices)


@dataclass(frozen=True)
class ClauseMembership(Record):
    """Where one candidate span sits in the clause structure."""

    candidate_id: str
    clause_id: str | None
    membership_state: str
    token_start: int
    token_end: int
    spanned_clause_ids: tuple[str, ...] = ()
    asserting_component: str = "v5_8_1.clause_identity"
    confidence: float = 0.0

    def __post_init__(self) -> None:
        if self.membership_state not in MEMBERSHIP_STATES:
            raise ClauseIdentityError(
                f"unknown membership state {self.membership_state!r}")


@dataclass(frozen=True)
class ClauseRelation(Record):
    """A typed relation between two clauses in the same span."""

    relation_id: str
    source_clause_id: str
    target_clause_id: str
    relation_type: str
    evidence_span: tuple[int, int] | None
    evidence_text: str
    asserting_component: str = "v5_8_1.clause_identity"
    confidence: float = 0.0
    decisive_or_provisional: str = "PROVISIONAL"

    def __post_init__(self) -> None:
        if self.relation_type not in CLAUSE_RELATION_TYPES:
            raise ClauseIdentityError(
                f"unknown clause relation {self.relation_type!r}")
        if self.decisive_or_provisional not in DECISIVENESS:
            raise ClauseIdentityError(
                f"unknown decisiveness {self.decisive_or_provisional!r}")


@dataclass(frozen=True)
class CrossClauseLicence(Record):
    """A named, evidenced permission to bind roles across a clause boundary.

    Every field is load bearing.  Without the licence type a family that starts
    over-firing cannot be found; without evidence the licence is an assertion;
    without the asserting component a defect cannot be traced to the code that
    introduced it; without decisiveness a guess is indistinguishable from a
    determination.
    """

    licence_id: str
    licence_type: str
    source_clause_id: str
    target_clause_id: str
    relation_type: str
    evidence_span: tuple[int, int] | None
    evidence_text: str
    asserting_component: str = "v5_8_1.clause_identity"
    confidence: float = 0.0
    decisive_or_provisional: str = "PROVISIONAL"

    def __post_init__(self) -> None:
        if self.licence_type not in LICENCE_TYPES:
            raise ClauseIdentityError(f"unknown licence type {self.licence_type!r}")
        if self.relation_type not in CLAUSE_RELATION_TYPES:
            raise ClauseIdentityError(
                f"unknown clause relation {self.relation_type!r}")
        if self.decisive_or_provisional not in DECISIVENESS:
            raise ClauseIdentityError(
                f"unknown decisiveness {self.decisive_or_provisional!r}")
        if not self.evidence_text and self.evidence_span is None:
            raise ClauseIdentityError(
                "a licence must carry evidence: proximity is not a licence")


@dataclass(frozen=True)
class ClauseContext(Record):
    """Everything this layer knows about one span's internal clause structure."""

    context_id: str
    region_id: str
    script: str
    clauses: tuple[ClauseIdentity, ...] = ()
    relations: tuple[ClauseRelation, ...] = ()
    boundary_events: tuple[BoundaryEvent, ...] = ()
    token_count: int = 0
    recorded_time: str = ""

    def clause_at_token(self, index: int) -> ClauseIdentity | None:
        for clause in self.clauses:
            if clause.token_start <= index <= clause.token_end:
                return clause
        return None

    def by_id(self, clause_id: str | None) -> ClauseIdentity | None:
        if clause_id is None:
            return None
        for clause in self.clauses:
            if clause.clause_id == clause_id:
                return clause
        return None


# ===========================================================================
# Predication anchors
# ===========================================================================

def finite_head_indices(toks: list[tuple[str, int, int]], script: str) -> list[int]:
    """Token indices carrying a finite predicate head.

    This is the step that makes production capable of a judgement the evaluator
    explicitly declines: whether a coordinator joins clauses or phrases.
    """
    found: list[int] = []
    for index, (word, _start, _stop) in enumerate(toks):
        token = _clean(word)
        if not token:
            continue
        if script == "ARABIC":
            hit = bool(_AR_MODAL_TOKEN.match(token) or _AR_COPULA_TOKEN.match(token)
                       or (_AR_IMPERFECT_TOKEN.match(token)
                           and not token.startswith("ال")))
        elif script == "CYRILLIC":
            hit = bool(_RU_MODAL_TOKEN.match(token) or _RU_COPULA_TOKEN.match(token)
                       or _RU_IMPERSONAL_TOKEN.match(token)
                       or _RU_FINITE_TOKEN.match(token))
        else:
            hit = bool(_LAT_MODAL.match(token) or _LAT_AUXILIARY.match(token))
        if hit:
            found.append(index)
    return found


def _has_finite_head_between(heads: list[int], start: int, stop: int) -> bool:
    return any(start <= head <= stop for head in heads)


def _is_closed_class_head(token: str, script: str) -> bool:
    """Is this token a modal, auxiliary or copula rather than a pattern match?

    The distinction matters where a wrong answer moves a clause boundary.  The
    Arabic imperfect PATTERN -- prefix ي/ت/ن/أ plus a root -- also matches
    ordinary nouns: "نشاط" ("activity") is not a verb, and reading it as one
    resumed the matrix clause in the middle of a relative clause, cutting that
    relative's post-verbal subject away from its own verb.  Closed-class
    membership is decidable; pattern shape is not, so only the former is trusted
    where the cost of being wrong is a moved boundary.
    """
    if script == "ARABIC":
        return bool(_AR_MODAL_TOKEN.match(token) or _AR_COPULA_TOKEN.match(token))
    if script == "CYRILLIC":
        return bool(_RU_MODAL_TOKEN.match(token) or _RU_COPULA_TOKEN.match(token)
                    or _RU_IMPERSONAL_TOKEN.match(token))
    return bool(_LAT_MODAL.match(token) or _LAT_AUXILIARY.match(token))


# ===========================================================================
# Segmentation — head-driven projection, not boundary counting
# ===========================================================================

def _apostrophe_letter_offsets(text: str) -> frozenset[int]:
    """Offsets where an apostrophe-class mark is a LETTER, not a delimiter.

    A quotation mark stands BETWEEN words; an elision or possessive apostrophe
    stands INSIDE one.  "l'expression", "dell'articolo", "aujourd'hui", "the
    operator's" all put a letter on each side of the mark, and no quotation
    convention in this corpus's languages does.  So the orthographic test is
    decidable locally, needs no lexicon, and is the same in every script:
    `str.isalpha` answers it for Latin, Cyrillic and Arabic alike.

    WHY THIS EXISTS.  Without it the depth answers a question about orthography
    instead of a question about structure, in BOTH directions, and both were
    measured:

      * two apostrophes in ordinary UNQUOTED prose paired with each other and
        raised the depth over everything between them, so "L'expression designe
        un acte. Si le Conseil decide, l'autorite ..." lost the full stop AND
        the subordinator -- there is no quotation anywhere in that sentence;
      * an apostrophe inside a REAL quotation closed it early, because U+2019 is
        the closing partner of U+2018, so the amending form "Article 3 is
        replaced by the following: ‘The competent authority shall carry out the
        checks.  Where the operator’s records are incomplete, ...’" fell to
        depth zero at the POSSESSIVE and then opened a top-level clause at the
        next full stop -- INSIDE the enacted provision.

    That second direction is exactly what this module's own two earlier
    enclosure repairs were about (the abandoned opener in PASS 1, the U+201C
    ordering in PASS 2): treating text as enclosed when it is not, or as
    unenclosed when it is, disables segmentation silently.

    WHAT THIS DOES NOT DECIDE.  A word-FINAL apostrophe -- the English plural
    possessive "the Members' rights" -- is locally indistinguishable from a
    closing straight quote ("he said 'yes' loudly"), and is left alone.  A test
    that guessed there would trade one silent failure for another, which is the
    trade the sibling enclosure work refused.
    """
    return frozenset(
        offset for offset, character in enumerate(text)
        if character in _APOSTROPHE_ALSO
        and 0 < offset < len(text) - 1
        and text[offset - 1].isalpha() and text[offset + 1].isalpha())


def _enclosure_depth_at(toks: list[tuple[str, int, int]], text: str) -> list[int]:
    """Bracket/quote depth entering each token.

    Depth is tracked so that a full stop or subordinator inside a quotation or a
    parenthesis does not open a top-level clause.  Full typed enclosure semantics
    are a separate work item; this is the minimum needed to stop segmentation
    fracturing inside quoted material.
    """
    # An apostrophe inside a word is a letter.  Skipping it here is enough for
    # BOTH passes: PASS 2 only ever looks at offsets PASS 1 put in `balanced`,
    # and a skipped offset is never added there and never pushed onto `pending`.
    letters = _apostrophe_letter_offsets(text)
    # PASS 1 -- find which opening marks actually close inside this span.
    # An enclosure that never closes must not suppress boundaries for the rest
    # of the text: "folgenden Eid: „Ich schwöre, dass ich meine Kraft" opens a
    # quotation the span never closes, and treating everything after it as
    # enclosed returned the whole remainder as a single clause.  An unmatched
    # opener is far more likely to be a truncated extract or a typographic
    # artifact than a real enclosure covering all following text.
    balanced: set[int] = set()
    pending: list[tuple[str, int]] = []
    for offset, character in enumerate(text):
        if offset in letters:
            continue
        if pending and character == pending[-1][0]:
            _closer, opened_at = pending.pop()
            balanced.add(opened_at)
            balanced.add(offset)
        elif character in _SYMMETRIC_QUOTE:
            if pending and pending[-1][0] == _SYMMETRIC_QUOTE_OPEN:
                _closer, opened_at = pending.pop()
                balanced.add(opened_at)
                balanced.add(offset)
            else:
                pending.append((_SYMMETRIC_QUOTE_OPEN, offset))
        elif character in _ENCLOSURE_PAIRS:
            pending.append((_ENCLOSURE_PAIRS[character], offset))

    depth_at: list[int] = []
    depth = 0
    stack: list[str] = []
    for word, start, stop in toks:
        depth_at.append(depth)
        for index, character in enumerate(text[start:stop], start=start):
            if index not in balanced:
                continue
            # CLOSING IS TESTED FIRST, and this ordering is the whole point.
            # U+201C is the CLOSING mark of a German „…“ pair and the OPENING
            # mark of an English “…” pair, so it appears both as a key in
            # _ENCLOSURE_PAIRS and in _CLOSERS.  With the opener tested first,
            # „Pseudonymisierung“ opened a quote that never closed, depth stayed
            # above zero for the remaining 400 characters, and every subordinator
            # after it was skipped as "inside an enclosure" -- silently disabling
            # segmentation for exactly the German definitional register this
            # corpus is full of.  Matching the top of the stack first resolves
            # the ambiguity by context rather than by character identity.
            if stack and character == stack[-1]:
                stack.pop()
                depth = max(0, depth - 1)
            elif character in _SYMMETRIC_QUOTE:
                # One character opens and closes; parity decides which.
                if _SYMMETRIC_QUOTE_OPEN in stack:
                    stack.remove(_SYMMETRIC_QUOTE_OPEN)
                    depth = max(0, depth - 1)
                else:
                    stack.append(_SYMMETRIC_QUOTE_OPEN)
                    depth += 1
            elif character in _ENCLOSURE_PAIRS:
                stack.append(_ENCLOSURE_PAIRS[character])
                depth += 1
    return depth_at


def _boundary_events(toks: list[tuple[str, int, int]], text: str,
                     heads: list[int], script: str) -> list[BoundaryEvent]:
    """Typed clause-opening events, decided with knowledge of the finite heads."""
    depth_at = _enclosure_depth_at(toks, text)
    events: list[BoundaryEvent] = [
        BoundaryEvent("SPAN_START", 0, toks[0][1] if toks else 0, "", 1.0)]

    for index, (word, start, _stop) in enumerate(toks):
        if index == 0:
            continue
        token = _clean(word)
        inside = depth_at[index] > 0

        previous_word = toks[index - 1][0]
        # Terminal punctuation on the PREVIOUS token opens a new proposition --
        # unless it is an enumerator or an abbreviation, where the stop is
        # orthographic rather than propositional.
        if (_TERMINAL.search(previous_word) and not inside
                and not _ENUMERATOR_OR_ABBREVIATION.match(previous_word)):
            events.append(BoundaryEvent(
                "TERMINAL_PUNCTUATION", index, start, previous_word, 0.95))
            continue

        if inside:
            continue

        # that/que/che are complementisers AND relative pronouns, and both
        # readings are in the subordinator inventory, so whichever test runs
        # first wins unconditionally.  Testing subordination first made "El
        # funcionario que es nombrado" a complement clause and detached the
        # matrix subject from its own verb.  The relative reading is checked
        # first, on evidence: a relative clause puts its finite verb straight
        # after the pronoun ("que ES nombrado"), while a complement clause
        # brings its own subject ("that THE CONDITIONS are met").
        # Some tokens subordinate only in a fixed pairing: "para QUE", "so THAT",
        # "siempre QUE", "bien QUE".  Alone they are prepositions or adverbs, and
        # treating "para" as a subordinator split the title "Convenio de Berna
        # para la Protección de las Obras" into two clauses.
        if token in _NEEDS_COMPLEMENTISER:
            following = _clean(toks[index + 1][0]) if index + 1 < len(toks) else ""
            # German fronts a conditional protasis and opens the main clause with
            # correlative "so": "Hat sich ein Abkömmling ... ergeben, SO KANN der
            # Erblasser ...".  That is a clause boundary on the strength of the
            # comma that closes the protasis.  English "so" needs "that".
            # Requiring the complementiser unconditionally merged the German
            # protasis and apodosis into one clause.
            apodosis = (token in _CORRELATIVE_APODOSIS
                        and toks[index - 1][0].endswith((",", "،", ";", "؛")))
            if following not in _COMPLEMENTISERS and not apodosis:
                continue
            if apodosis:
                # The apodosis is the MAIN clause, and the fronted protasis
                # before it is the subordinate one -- "HAT sich ein Abkömmling
                # ... ergeben, SO KANN der Erblasser ...".  Typing the apodosis
                # as subordinate had it exactly backwards, and left the
                # protasis verb looking like the matrix predicate.
                events.append(BoundaryEvent(
                    "CORRELATIVE_APODOSIS", index, start, token, 0.80))
                continue

        relative_reading = (token in _AMBIGUOUS_RELATIVISER
                            and (index + 1) in heads
                            and not _PREPOSITION.match(
                                _clean(toks[index - 1][0])))
        subordinator_form = next(
            (form for form in _clean_forms(word) if _SUBORDINATOR.match(form)), "")
        if subordinator_form and not relative_reading:
            events.append(BoundaryEvent("SUBORDINATOR", index, start,
                                        subordinator_form, 0.85))
            continue

        if _RELATIVISER.match(token):
            # der/die/das/que/che/that/which are relative pronouns AND
            # determiners, and the cost of the two errors is not symmetric.
            # Splitting a clause that is really one destroys lawful pairings and
            # breaks three gates that currently pass; failing to split leaves a
            # pairing admitted that a later rule may still catch.  So the
            # ambiguous set demands strong evidence: either a preceding comma,
            # which German orthography requires before a relative clause, or a
            # finite head in the very next token.  A determiner is followed by
            # its noun, not by a verb.
            previous = _clean(toks[index - 1][0]) if index else ""
            if token in _AMBIGUOUS_RELATIVISER:
                # A determiner after a preposition is not a relativiser: "in der
                # Lage ist" is a prepositional phrase.  Pied-piping is handled by
                # _UNAMBIGUOUS_RELATIVISER, which has no determiner reading.
                if _PREPOSITION.match(previous):
                    continue
                comma_before = toks[index - 1][0].endswith((",", "،"))
                head_immediately_after = (index + 1) in heads
                if not (comma_before or head_immediately_after):
                    continue
            if not _has_finite_head_between(heads, index, min(index + 12,
                                                             len(toks) - 1)):
                continue
            events.append(BoundaryEvent("RELATIVISER", index, start, token, 0.75))
            continue

        if _COORDINATOR.match(token):
            # THE JUDGEMENT THE EVALUATOR CANNOT MAKE -- and the one that has to
            # be made carefully, because getting it wrong is expensive in both
            # directions.  The ruler splits on no coordinator at all, by its own
            # admission; an earlier ruler version that split on every one
            # manufactured 701 of 722 reported violations.
            #
            # "a finite head somewhere before and somewhere after" was tried and
            # MEASURED: it split "unvorhergesehenen UND unabweisbaren
            # Bedürfnisses" and "wissenschaftlicher ODER statistischer Form",
            # which coordinate adjectives, and cost 32 lawful analyses across 6
            # units while removing only 40 unlawful ones.  German clause-final
            # verbal complexes put an auxiliary near the end of almost every
            # sentence, so that test is satisfied by nearly any coordinator.
            #
            # What is required instead is positive evidence that the material
            # after the coordinator is an INDEPENDENT PREDICATION.
            before = _has_finite_head_between(heads, 0, index - 1)
            after_heads = [h for h in heads if h > index]
            if not (before and after_heads):
                continue
            first_after = after_heads[0]
            # A conjunct whose only finite head is the span's final token is a
            # verbal complex closing the current clause -- "... erteilt werden."
            # -- not a new proposition.
            if first_after >= len(toks) - 1:
                continue
            # Verb-first: German V1 coordination and Arabic VSO both put the
            # finite verb immediately after the coordinator.
            verb_first = first_after == index + 1
            # Subject-first: an overt subject NP opening the conjunct, with its
            # own finite head close behind.  A determiner or pronoun opens a
            # subject; an adjective or a preposition does not, which is exactly
            # what separates "and THE COMMISSION shall decide" from "und
            # UNABWEISBAREN Bedürfnisses erteilt werden".
            next_token = _clean(toks[index + 1][0]) if index + 1 < len(toks) else ""
            subject_first = (bool(_SUBJECT_OPENER.match(next_token))
                             and first_after - index <= 4)
            if verb_first or subject_first:
                events.append(BoundaryEvent(
                    "COORDINATOR_WITH_OWN_FINITE_HEAD", index, start, token, 0.70))
            continue

        # A semicolon separating enumerated items: a boundary only where each
        # side predicates.  Legal enumerations use it for both purposes.
        if previous_word.endswith((";", "؛")) and not inside:
            before = _has_finite_head_between(heads, 0, index - 1)
            after = _has_finite_head_between(heads, index, len(toks) - 1)
            if before and after:
                events.append(BoundaryEvent(
                    "SEMICOLON_ENUMERATION", index, start, previous_word, 0.65))

    unique: dict[int, BoundaryEvent] = {}
    for event in events:
        unique.setdefault(event.token_index, event)
    ordered = [unique[key] for key in sorted(unique)]

    # A relative clause ENDS; the clause it interrupted resumes.  Without this
    # the relative in "The officer who was appointed yesterday will report"
    # swallows the matrix predicate, and the matrix subject then appears to be
    # in a different clause from its own verb.  The resumption is marked as a
    # continuation rather than folded back into the matrix clause because
    # ClauseIdentity describes a contiguous token range; the discontinuity is
    # recorded honestly and licensed by STRUCTURAL_CONTINUATION_OR_SHARED_ROLE.
    resumptions: list[BoundaryEvent] = []
    for position, event in enumerate(ordered):
        if event.event_type != "RELATIVISER":
            continue
        segment_end = (ordered[position + 1].token_index - 1
                       if position + 1 < len(ordered) else len(toks) - 1)
        inside_heads = [h for h in heads if event.token_index <= h <= segment_end]
        if len(inside_heads) < 2:
            continue
        # REVERTED EXPERIMENT.  A German relative clause opened after a comma is
        # comma-delimited on both sides, and in a verb-final language its own
        # finite verb sits at the end ("die ... umgewandelt werden SOLLEN,
        # müssen ..."), so resuming the matrix at the first COMMA-INTRODUCED
        # later head rather than simply the second head looked right.  Measured:
        # false rejections rose from 51 to 56 and material-alternative survival
        # fell from 0.9470 to 0.9416, because the later resumption point
        # lengthens every relative clause and turns other pairings cross-clause.
        # A fallback form was tried too and measured identically.  Reverted; the
        # German case is instead handled where it actually belongs, in the
        # relative-clause licence.
        resume_at = inside_heads[1]
        if depth_at[resume_at] > 0:
            continue
        # The matrix resumes at an ORTHOGRAPHIC boundary or at a closed-class
        # finite verb -- never at a mere pattern match.  German and Arabic both
        # close a relative clause with a comma; English restrictive relatives do
        # not, and resume instead at a modal or auxiliary ("... yesterday WILL
        # report").  Without this test the Arabic noun "نشاط" resumed the matrix
        # inside a relative clause and separated its post-verbal subject from
        # its own verb, costing 9 lawful analyses.
        comma_before = toks[resume_at - 1][0].endswith((",", "،", ";", "؛"))
        if not (comma_before or _is_closed_class_head(
                _clean(toks[resume_at][0]), script)):
            continue
        resumptions.append(BoundaryEvent(
            "MATRIX_RESUMPTION", resume_at, toks[resume_at][1],
            toks[resume_at][0], 0.65))

    for event in resumptions:
        unique.setdefault(event.token_index, event)
    return [unique[key] for key in sorted(unique)]


_EVENT_TO_RELATION = {
    "SPAN_START": "MATRIX",
    "TERMINAL_PUNCTUATION": "MATRIX",
    "SUBORDINATOR": "ADVERBIAL_SUBORDINATE",
    "RELATIVISER": "RELATIVE",
    "COORDINATOR_WITH_OWN_FINITE_HEAD": "COORDINATE",
    "SEMICOLON_ENUMERATION": "COORDINATE",
    "ENCLOSURE_OPEN": "PARENTHETICAL",
    "ENCLOSURE_CLOSE": "MATRIX",
    "MATRIX_RESUMPTION": "STRUCTURAL_CONTINUATION",
    "CORRELATIVE_APODOSIS": "MATRIX",
}

#: Subordinators that introduce a verbal complement rather than an adjunct.
_COMPLEMENT_SUBORDINATOR = frozenset({
    "that", "dass", "daß", "que", "che", "чтобы", "что", "أن",
})


def build_clause_context(text: str, *, region_id: str = "",
                         script: str | None = None) -> ClauseContext:
    """Segment one span into typed clauses by projecting from finite heads."""
    body = (text or "")[:MAX_SCAN_CHARS]
    toks = tokens(body)
    resolved_script = script or CL.script_of(body)
    if not toks:
        return ClauseContext(
            stable_id("v5-8-1-clausectx", body[:120], region_id),
            region_id, resolved_script, (), (), (), 0, now_utc())

    heads = finite_head_indices(toks, resolved_script)
    events = _boundary_events(toks, body, heads, resolved_script)

    clauses: list[ClauseIdentity] = []
    relations: list[ClauseRelation] = []
    coordination_group: str | None = None

    for position, event in enumerate(events):
        token_start = event.token_index
        token_end = (events[position + 1].token_index - 1
                     if position + 1 < len(events) else len(toks) - 1)
        if token_end < token_start:
            continue
        own_heads = [h for h in heads if token_start <= h <= token_end]
        relation = _EVENT_TO_RELATION[event.event_type]
        if (event.event_type == "SUBORDINATOR"
                and _clean(event.trigger_text) in _COMPLEMENT_SUBORDINATOR):
            relation = "SUBORDINATE_COMPLEMENT"

        clause_id = stable_id("v5-8-1-clause-id", region_id, body[:60],
                              token_start, token_end)

        # Parentage.  A subordinate or relative clause attaches to the nearest
        # preceding clause that carries a finite head of its own; a coordinate
        # clause shares that clause's parent and its coordination group.
        parent_id: str | None = None
        if relation != "MATRIX" and clauses:
            for previous in reversed(clauses):
                if previous.has_finite_head or previous.relation_to_parent == "MATRIX":
                    parent_id = previous.clause_id
                    break
            else:
                parent_id = clauses[-1].clause_id
        if relation == "STRUCTURAL_CONTINUATION" and clauses:
            # The clause being resumed is the one the relative interrupted, so
            # the continuation attaches to the interrupted clause's parent --
            # not to the relative clause that sits between them.
            interrupted = clauses[-1]
            parent_id = interrupted.parent_clause_id or interrupted.clause_id
        elif relation == "COORDINATE":
            anchor = next((c for c in reversed(clauses)
                           if c.relation_to_parent != "COORDINATE"), None)
            if anchor is not None:
                parent_id = anchor.parent_clause_id or anchor.clause_id
                coordination_group = (anchor.coordination_group_id
                                      or stable_id("v5-8-1-coordgroup",
                                                   anchor.clause_id))
        elif relation == "MATRIX":
            coordination_group = None

        clause = ClauseIdentity(
            clause_id=clause_id,
            region_id=region_id,
            token_start=token_start,
            token_end=token_end,
            character_start=toks[token_start][1],
            character_end=toks[token_end][2],
            finite_head_ids=tuple(
                stable_id("v5-8-1-finitehead", clause_id, h) for h in own_heads),
            finite_head_token_indices=tuple(own_heads),
            parent_clause_id=parent_id,
            relation_to_parent=relation,
            coordination_group_id=(coordination_group
                                   if relation == "COORDINATE" else None),
            opening_event=event,
            confidence=event.confidence,
            decisive_or_provisional=("DECISIVE" if own_heads and event.confidence
                                     >= 0.85 else "PROVISIONAL"),
            recorded_time=now_utc(),
        )
        clauses.append(clause)

        if parent_id is not None:
            relations.append(ClauseRelation(
                relation_id=stable_id("v5-8-1-clauserel", parent_id, clause_id),
                source_clause_id=parent_id,
                target_clause_id=clause_id,
                relation_type=relation,
                evidence_span=(event.character_offset,
                               event.character_offset + len(event.trigger_text)),
                evidence_text=event.trigger_text,
                confidence=event.confidence,
                decisive_or_provisional=clause.decisive_or_provisional,
            ))

    # A span that BEGINS with a subordinator is subordinate from its first
    # token.  The boundary scan skips index 0 -- SPAN_START already opened a
    # clause there -- so "وعندما يسعل الشخص ..." and "If the Council decides, ..."
    # were typed MATRIX, and their subordinate verb then looked like the
    # proposition's predicate.
    if clauses and clauses[0].opening_event is not None \
            and clauses[0].opening_event.event_type == "SPAN_START" \
            and any(_SUBORDINATOR.match(form)
                    for form in _clean_forms(toks[clauses[0].token_start][0])):
        successor = next((c for c in clauses[1:]
                          if c.relation_to_parent in ("MATRIX", "COORDINATE")), None)
        clauses[0] = replace(
            clauses[0], relation_to_parent="ADVERBIAL_SUBORDINATE",
            parent_clause_id=successor.clause_id if successor else None)
        if successor is not None and successor.relation_to_parent == "COORDINATE":
            # With the opener re-typed there is no matrix clause left for the
            # remainder to coordinate WITH; it carries the proposition.
            position = clauses.index(successor)
            clauses[position] = replace(successor, relation_to_parent="MATRIX",
                                        parent_clause_id=None,
                                        coordination_group_id=None)

    # A clause immediately preceding a correlative apodosis is a fronted
    # protasis: subordinate, however it is punctuated and whether or not any
    # subordinator introduced it.  German V1 conditionals carry no subordinator
    # at all, so nothing else in this module can see that they are dependent.
    for position, clause in enumerate(clauses):
        following = clauses[position + 1] if position + 1 < len(clauses) else None
        if (following is not None and following.opening_event is not None
                and following.opening_event.event_type == "CORRELATIVE_APODOSIS"
                and clause.relation_to_parent == "MATRIX"):
            clauses[position] = replace(
                clause, relation_to_parent="ADVERBIAL_SUBORDINATE",
                parent_clause_id=following.clause_id)

    return ClauseContext(
        context_id=stable_id("v5-8-1-clausectx", body[:120], region_id),
        region_id=region_id,
        script=resolved_script,
        clauses=tuple(clauses),
        relations=tuple(relations),
        boundary_events=tuple(events),
        token_count=len(toks),
        recorded_time=now_utc(),
    )


# ===========================================================================
# Membership
# ===========================================================================

def token_range(span: tuple[int, int] | None,
                toks: list[tuple[str, int, int]]) -> tuple[int, int] | None:
    if span is None:
        return None
    start, stop = span
    covered = [i for i, (_w, s, e) in enumerate(toks) if s < stop and e > start]
    return (covered[0], covered[-1]) if covered else None


def membership_of(candidate: Any, context: ClauseContext,
                  text: str) -> ClauseMembership:
    """Which clause a candidate belongs to, or that it straddles several."""
    span = getattr(candidate, "span", None)
    candidate_id = getattr(candidate, "candidate_id", "")
    toks = tokens(text)
    rng = token_range(span, toks)
    if rng is None:
        return ClauseMembership(candidate_id, None, "NO_CLAUSE_RESOLVED", -1, -1)

    start, stop = rng
    touched = [c for c in context.clauses
               if not (c.token_end < start or stop < c.token_start)]
    if not touched:
        return ClauseMembership(candidate_id, None, "NO_CLAUSE_RESOLVED", start, stop)
    if len(touched) == 1:
        return ClauseMembership(candidate_id, touched[0].clause_id,
                                "FULLY_WITHIN_ONE_CLAUSE", start, stop,
                                (touched[0].clause_id,), confidence=0.9)

    # A span touching several clauses is anchored to the one holding its first
    # token: that is where its head sits.  A subject carrying a relative clause
    # legitimately extends into the relative clause, and must not be reported as
    # having no clause at all.
    anchor = context.clause_at_token(start) or touched[0]
    return ClauseMembership(candidate_id, anchor.clause_id,
                            "SPANS_MULTIPLE_CLAUSES", start, stop,
                            tuple(c.clause_id for c in touched), confidence=0.6)


# ===========================================================================
# Licences
# ===========================================================================

def _descends_from(context: ClauseContext, clause: ClauseIdentity | None,
                   ancestor_id: str, *, limit: int = 8) -> bool:
    seen = 0
    while clause is not None and seen < limit:
        if clause.parent_clause_id == ancestor_id:
            return True
        clause = context.by_id(clause.parent_clause_id)
        seen += 1
    return False


def _tokens_between(toks: list[tuple[str, int, int]], start: int,
                    stop: int) -> list[str]:
    return [_clean(toks[i][0]) for i in range(max(0, start), min(stop + 1, len(toks)))]


#: Function words that may stand between a coordinator and a shared predicate
#: without constituting a subject of their own.
_NON_SUBJECT_FUNCTION_WORD = re.compile(
    r"^(also|then|therefore|thus|hereby|further|moreover|not|never|"
    r"auch|dann|somit|hiermit|ferner|nicht|"
    r"aussi|donc|ainsi|ne|pas|"
    r"tambien|también|entonces|asi|así|no|"
    r"anche|quindi|così|non|"
    r"также|затем|поэтому|не|"
    r"أيضا|ثم|لا)$", re.IGNORECASE)


#: Possessive determiners open a subject just as articles do.
_POSSESSIVE = re.compile(
    r"^(my|your|his|her|its|our|their|"
    r"mein|meine|dein|deine|sein|seine|ihr|ihre|unser|unsere|euer|eure|"
    r"mon|ma|mes|ton|ta|tes|son|sa|ses|notre|nos|votre|vos|leur|leurs|"
    r"mi|mis|tu|tus|su|sus|nuestro|nuestra|vuestro|"
    r"mio|mia|tuo|tua|suo|sua|nostro|vostro|loro|"
    r"мой|моя|твой|его|её|их|наш|ваш|свой)$", re.IGNORECASE)


def _looks_like_subject_material(raw: str, cleaned: str) -> bool:
    """Positive evidence that a token could head or open a subject.

    A POSITIVE test is used rather than "anything that is not a known function
    word".  The negative form needed an unbounded adverb list -- it read
    "otherwise" in "unless OTHERWISE decided" as a subject and withdrew a
    correct inheritance -- and a list that must grow to stay correct is a list
    that is quietly wrong between growths.
    """
    if _SUBJECT_OPENER.match(cleaned) or _POSSESSIVE.match(cleaned):
        return True
    # A capitalised token inside a clause is a noun in German and a proper noun
    # elsewhere; either way it is nominal, and neither is an adverb.
    return bool(raw[:1].isupper() and cleaned)


def _has_own_subject_before(clause: ClauseIdentity, upto: int,
                            toks: list[tuple[str, int, int]]) -> bool:
    """Is there nominal material between the clause opener and `upto`?

    A clause that carries its own subject shares nothing and inherits nothing.
    The material between the token that opened the clause and its predicate is
    where a subject sits; anything there other than adverbial function words is
    one.

    This is deliberately a POSITIVE test for a subject rather than a negative
    test for a finite head.  Inheritance was previously licensed by "no finite
    head was detected in the subordinate clause", and Latin-script open-class
    finite verbs are undetectable here on purpose -- clauses.py records that a
    suffix set wide enough to catch them also fires on plural nouns.  So "no
    head detected" meant "no head detectable", and the licence fired on 83
    analyses whose subordinate clauses had perfectly good subjects of their own.
    Absence of evidence is not evidence of absence; this asks for the evidence.
    """
    start = clause.token_start + 1
    stop = min(upto, clause.token_end + 1)
    for i in range(max(0, start), min(stop, len(toks))):
        # A noun governed by a preposition is inside a prepositional phrase, not
        # a subject.  German capitalises all nouns, so "die IN GRUNDKAPITAL
        # umgewandelt werden" read its adverbial PP as a subject and withdrew
        # the relative-clause licence from seven lawful analyses.
        if i > 0 and _PREPOSITION.match(_clean(toks[i - 1][0])):
            continue
        if _looks_like_subject_material(toks[i][0], _clean(toks[i][0])):
            return True
    return False


def _conjunct_has_own_subject(clause: ClauseIdentity,
                              toks: list[tuple[str, int, int]]) -> bool:
    """Does a coordinate clause carry its own subject before its finite head?

    Gapping is licensed by the absence of one.
    """
    if not clause.finite_head_token_indices:
        return False
    return _has_own_subject_before(clause, clause.finite_head_token_indices[0], toks)


def licence_for_pairing(subject_membership: ClauseMembership,
                        predicate_membership: ClauseMembership,
                        context: ClauseContext, text: str
                        ) -> CrossClauseLicence | None:
    """The named licence permitting this cross-clause pairing, or None.

    Returning None means INADMISSIBLE, not "unknown": a pairing across a clause
    boundary with no licence is exactly the population this layer exists to
    remove.  Every branch below asserts a licence only on evidence that is
    recorded on the licence itself.
    """
    subject_clause = context.by_id(subject_membership.clause_id)
    predicate_clause = context.by_id(predicate_membership.clause_id)
    if subject_clause is None or predicate_clause is None:
        return None
    if subject_clause.clause_id == predicate_clause.clause_id:
        return None  # not a cross-clause pairing; no licence needed

    # A predicate that IS its own clause's opening FUNCTION WORD predicates
    # nothing: "…folgenden Eid: „Ich schwöre, DASS" offered "dass" as the head.
    # The test is restricted to clauses opened by a subordinator, relativiser or
    # coordinator; a clause opened by MATRIX_RESUMPTION begins at the finite verb
    # itself, and excluding that would refuse every subject that carries a
    # relative clause -- a large and entirely lawful family.
    opening_type = (predicate_clause.opening_event.event_type
                    if predicate_clause.opening_event else "")
    if (opening_type in ("SUBORDINATOR", "RELATIVISER",
                         "COORDINATOR_WITH_OWN_FINITE_HEAD")
            and predicate_membership.token_start <= predicate_clause.token_start):
        return None

    toks = tokens(text)

    def build(licence_type: str, relation: str, evidence_span, evidence_text,
              confidence: float, decisiveness: str) -> CrossClauseLicence:
        return CrossClauseLicence(
            licence_id=stable_id("v5-8-1-licence", licence_type,
                                 subject_clause.clause_id,
                                 predicate_clause.clause_id),
            licence_type=licence_type,
            source_clause_id=subject_clause.clause_id,
            target_clause_id=predicate_clause.clause_id,
            relation_type=relation,
            evidence_span=evidence_span,
            evidence_text=evidence_text or licence_type,
            confidence=confidence,
            decisive_or_provisional=decisiveness,
        )

    opening = predicate_clause.opening_event
    trigger = opening.trigger_text if opening else ""
    trigger_span = ((opening.character_offset,
                     opening.character_offset + len(trigger))
                    if opening and trigger else None)

    # 1. RELATIVE_CLAUSE_RELATION.  The predicate sits in a relative clause whose
    #    head noun is the subject: "The officer who was appointed will report"
    #    read with the subject in the matrix and the predicate in the relative.
    if (predicate_clause.relation_to_parent == "RELATIVE"
            and (predicate_clause.parent_clause_id == subject_clause.clause_id
                 or _descends_from(context, predicate_clause,
                                   subject_clause.clause_id))
            # The head noun is the subject of the relative's verb only when the
            # relativiser IS that subject.  In "the extent to which HE OR SHE
            # contests the decision" the relative has its own subject, and the
            # matrix noun is not it -- the pronoun is.  Licensing that pairing
            # was this family over-firing.
            and not _has_own_subject_before(
                predicate_clause, predicate_membership.token_start, toks)):
        return build("RELATIVE_CLAUSE_RELATION", "RELATIVE", trigger_span,
                     trigger, 0.85, "DECISIVE")

    # 2. REPORTED_SPEECH_ATTRIBUTION.  The subject clause carries a reporting
    #    verb and the predicate sits in its complement or in quoted material.
    if predicate_clause.relation_to_parent in ("SUBORDINATE_COMPLEMENT", "QUOTED"):
        reporting = [t for t in _tokens_between(toks, subject_clause.token_start,
                                                subject_clause.token_end)
                     if _REPORTING_VERB.match(t)]
        if reporting and _descends_from(context, predicate_clause,
                                        subject_clause.clause_id):
            return build("REPORTED_SPEECH_ATTRIBUTION", "SUBORDINATE_COMPLEMENT",
                         trigger_span, reporting[0], 0.80, "DECISIVE")

    # 3. CONTROL_RELATION and 4. RAISING_RELATION.  Both require the matrix
    #    clause to carry the licensing verb AND a non-finite marker to introduce
    #    the lower predicate.  Without the marker there is no complement to
    #    control, and the licence is not asserted.
    if _descends_from(context, predicate_clause, subject_clause.clause_id) \
            or predicate_clause.parent_clause_id == subject_clause.clause_id:
        matrix_tokens = _tokens_between(toks, subject_clause.token_start,
                                        subject_clause.token_end)
        # The marker must introduce THE PREDICATE BEING PAIRED, not merely occur
        # somewhere in the lower clause.  Without this, "The Parties agree,
        # although the dispute is complex, to submit it" licensed a pairing of
        # the matrix subject with "is" -- the subordinate clause's own finite
        # verb, which controls nothing.
        marker_before_predicate = _tokens_between(
            toks, predicate_clause.token_start,
            max(predicate_clause.token_start, predicate_membership.token_start - 1))
        has_marker = any(_INFINITIVE_MARKER.match(t) for t in marker_before_predicate)
        control = [t for t in matrix_tokens if _CONTROL_VERB.match(t)]
        raising = [t for t in matrix_tokens if _RAISING_VERB.match(t)]
        if control and has_marker:
            return build("CONTROL_RELATION", "SUBORDINATE_COMPLEMENT",
                         trigger_span, control[0], 0.75, "DECISIVE")
        if raising and has_marker:
            return build("RAISING_RELATION", "SUBORDINATE_COMPLEMENT",
                         trigger_span, raising[0], 0.75, "DECISIVE")

    # 5. TYPED_COORDINATION.  Coordinated clauses share a subject only when the
    #    later conjunct has no subject of its own -- gapping.  "The Council shall
    #    report and shall decide" shares one; "The Council shall report and the
    #    Commission shall decide" does not, and licensing the second was this
    #    family over-firing, found by its own mutation fixture.  The evidence for
    #    the licence is therefore the ABSENCE of nominal material between the
    #    coordinator and the conjunct's own finite head.
    if (predicate_clause.relation_to_parent == "COORDINATE"
            and predicate_clause.coordination_group_id is not None
            and (subject_clause.coordination_group_id
                 == predicate_clause.coordination_group_id
                 or subject_clause.clause_id == predicate_clause.parent_clause_id)
            and not _conjunct_has_own_subject(predicate_clause, toks)):
        return build("TYPED_COORDINATION", "COORDINATE", trigger_span,
                     trigger or "GAPPED_CONJUNCT_NO_OWN_SUBJECT", 0.70,
                     "PROVISIONAL")

    # 6. GOVERNING_SUBJECT_INHERITANCE.  A subordinate clause with no finite
    #    head of its own inherits the governing clause's subject.  The absence of
    #    an own head is the evidence; a subordinate clause that HAS its own head
    #    has its own subject and inherits nothing.
    if (predicate_clause.relation_to_parent in
            ("ADVERBIAL_SUBORDINATE", "SUBORDINATE_COMPLEMENT")
            and not predicate_clause.has_finite_head
            and not _has_own_subject_before(
                predicate_clause, predicate_membership.token_start, toks)
            and (predicate_clause.parent_clause_id == subject_clause.clause_id
                 or _descends_from(context, predicate_clause,
                                   subject_clause.clause_id))):
        return build("GOVERNING_SUBJECT_INHERITANCE", "SUBORDINATE_COMPLEMENT",
                     trigger_span, trigger or "NO_OWN_SUBJECT_BEFORE_PREDICATE",
                     0.70, "PROVISIONAL")

    # 7. STRUCTURAL_CONTINUATION_OR_SHARED_ROLE.  An interruption -- a
    #    parenthetical, or a relative clause the segmenter had to close -- does
    #    not end the clause it interrupts.  The material after it continues the
    #    same proposition, so a subject before the interruption and a predicate
    #    after it are one clause in fact, and the boundary between them is an
    #    artifact of representing clauses as contiguous ranges.
    if (predicate_clause.relation_to_parent
            in ("PARENTHETICAL", "STRUCTURAL_CONTINUATION")
            and predicate_clause.parent_clause_id == subject_clause.clause_id):
        return build("STRUCTURAL_CONTINUATION_OR_SHARED_ROLE",
                     predicate_clause.relation_to_parent,
                     trigger_span, trigger or "CLAUSE_RESUMED_AFTER_INTERRUPTION",
                     0.60, "PROVISIONAL")

    return None


def assess_pairing(subject: Any, predicate_head: Any, text: str, *,
                   region_id: str = "", context: ClauseContext | None = None
                   ) -> dict[str, Any]:
    """The clause-identity verdict on one subject/predicate pairing.

    This is the entry point admissibility.py consumes.  It returns a typed
    record rather than a boolean, so that a licence family which begins
    over-firing can be found by counting.
    """
    ctx = context or build_clause_context(text, region_id=region_id)
    result: dict[str, Any] = {
        "clause_context_id": ctx.context_id,
        "clause_count": len(ctx.clauses),
        "subject_clause_id": None,
        "predicate_clause_id": None,
        "subject_membership_state": "NO_CLAUSE_RESOLVED",
        "predicate_membership_state": "NO_CLAUSE_RESOLVED",
        "same_clause": None,
        "cross_clause_licence": None,
        "cross_clause_licence_type": None,
        "cross_clause_licence_evidence": None,
        "verdict": "NOT_ASSESSED",
    }
    if subject is None or predicate_head is None:
        result["verdict"] = "NOT_APPLICABLE_SINGLE_ROLE"
        return result

    subject_membership = membership_of(subject, ctx, text)
    predicate_membership = membership_of(predicate_head, ctx, text)
    result["subject_clause_id"] = subject_membership.clause_id
    result["predicate_clause_id"] = predicate_membership.clause_id
    result["subject_membership_state"] = subject_membership.membership_state
    result["predicate_membership_state"] = predicate_membership.membership_state

    if subject_membership.clause_id is None or predicate_membership.clause_id is None:
        # Unresolved clause structure is not evidence of a violation.  A layer
        # that cannot see the structure must not reject on that basis.
        result["verdict"] = "CLAUSE_STRUCTURE_UNRESOLVED"
        return result

    same = subject_membership.clause_id == predicate_membership.clause_id
    result["same_clause"] = same
    if same:
        result["verdict"] = "SAME_CLAUSE"
        return result

    # A subject span that STRADDLES a clause boundary used to be treated as
    # sharing a clause with anything it covered.  That was too generous by far:
    # "internal law, to ensure that the" is a fragment straddling a
    # complementiser, and it licensed every candidate head inside it.  Reaching
    # across a boundary is exactly what needs a licence, so the case is sent
    # through the licence machinery like any other -- where a subject genuinely
    # carrying its own relative clause is licensed by RELATIVE_CLAUSE_RELATION,
    # and a fragment straddling an unrelated complement clause is not.
    licence = licence_for_pairing(subject_membership, predicate_membership,
                                  ctx, text)
    if licence is None:
        result["verdict"] = "UNLICENSED_CROSS_CLAUSE"
        return result

    result["cross_clause_licence"] = licence.licence_id
    result["cross_clause_licence_type"] = licence.licence_type
    result["cross_clause_licence_evidence"] = licence.evidence_text
    result["verdict"] = "LICENSED_CROSS_CLAUSE"
    return result


__all__ = [
    "CLAUSE_RELATION_TYPES", "LICENCE_TYPES", "BOUNDARY_EVENT_TYPES",
    "MEMBERSHIP_STATES", "DECISIVENESS", "ClauseIdentityError",
    "BoundaryEvent", "ClauseIdentity", "ClauseMembership", "ClauseRelation",
    "CrossClauseLicence", "ClauseContext",
    "tokens", "token_range", "finite_head_indices", "build_clause_context",
    "membership_of", "licence_for_pairing", "assess_pairing",
]
