"""V5.8.1 §14-§21 — Role Binding V2: candidates, analyses, constraints, ambiguity.

V1 was falsified by an independently constructed span-level ruler: terminal
accuracy 0.338 (95% CI 0.242-0.449) against a 0.95 gate, 32 safety-critical
failures, and 0.000 on Russian and on legal recitals — the two registers D25
existed to repair.  The diagnosis was not a set of missing regexes.  It was the
architecture:

    text -> one subject -> one predicate -> one terminal state

That shape has nowhere to put an alternative reading, so the first rule that
matches wins and the loser is never scored.  Three consequences showed up
directly in the ruler:

  * a Russian recital converb ("отмечая ...") has no finite verb, so V1 fell
    through to its nominal-sentence branch and reported a zero-copula subject
    where the reference says the subject is the governing enactment clause;
  * V1 has no way to say "this is not a proposition at all" — it has
    SUBJECT_UNRESOLVED, which means "there is one and I cannot find it".  A
    masthead scored as an unresolved clause instead of an invalid one;
  * the predicate was a head span plus a detached complement string, so no
    whole-predicate span existed to compare.

V2 keeps the useful part of V1 — its refusal to substitute the nearest
organisation for an unbound anaphor — and replaces the control flow:

    source span
      -> typed candidates per role, each carrying its own evidence
      -> complete analyses (alternative readings, not a bag of best spans)
      -> hard compatibility constraints
      -> ambiguity-aware resolution with a declared separation margin
      -> external five-state ontology through an explicit adapter

Nothing here consults a network, a model, or a corpus statistic.  Every rule is
deterministic and inspectable, and every candidate records why it was proposed.
"""

from __future__ import annotations

import collections
import math

import re
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from typing import Any

from ..v5_1.models import Record, now_utc, stable_id
from . import clause_identity as CI
from . import predicate_types as PT
from . import clauses as CL
from . import structure as ST
from . import terminal as TM


class RoleBindingV2Violation(RuntimeError):
    """An analysis was built that violates a hard compatibility constraint."""


# ===========================================================================
# §18 — internal terminal states, mapped out through an adapter
# ===========================================================================

INTERNAL_STATES: tuple[str, ...] = (
    "UNIQUE_BINDING_ESTABLISHED", "MULTIPLE_BINDINGS_RECOVERABLE",
    "AMBIGUOUS_BINDING", "INSUFFICIENT_ROLE_EVIDENCE",
    "INVALID_BINDING_CANDIDATE",
)

#: How far the best analysis must lead the runner-up before the reading counts
#: as unique.  Declared here, never widened to rescue a difficult unit.
SEPARATION_MARGIN = 0.12

#: An enacting clause names an organ and an operative verb.  A recital whose
#: supplied context holds only the tail of the previous recital has its subject
#: named but recoverable from nothing -- the adjudicators ruled this repeatedly,
#: and calling it RECOVERABLE asserts a recovery source that does not exist.
_ENACTING_CLAUSE = re.compile(
    r"(ПОСТАНОВЛЯЕТ|ПОРУЧАЕТ|ПРОСИТ|ПРЕДЛАГАЕТ|ПРИЗЫВАЕТ|РЕКОМЕНДУЕТ|"
    r"УТВЕРЖДАЕТ|ПРИНИМАЕТ|НАЗНАЧАЕТ|УПОЛНОМОЧИВАЕТ|Ассамблея здравоохранения|"
    r"DECIDES|REQUESTS|URGES|RECOMMENDS|ADOPTS|APPOINTS|AUTHORIZES|"
    r"The (?:Assembly|Board|Committee|Council|Conference|Commission)|"
    r"IL PRESIDENTE|LA PRESIDENTE|EL PRESIDENTE|DECRETA|"
    r"تقرر|تطلب|تحث|توصي|تعتمد|جمعية الصحة)")


def enacting_context_present(*parts: str) -> bool:
    return any(_ENACTING_CLAUSE.search(part or "") for part in parts)
MAX_SCAN_CHARS = 4000

#: V581C M7.  A span opening with a STANDALONE coordinating conjunction
#: begins a new conjunct; its inheritance axis is the unemitted COORDINATE
#: relation, never GOVERNING_CLAUSE_*.  Token-bounded on purpose: the Arabic
#: proclitic و attaches to the following word and is not a coordinator token.
_COORDINATOR_INITIAL = re.compile(
    r"^\s*(?:и|а|но|and|or|et|ou|y|o|e)\s", re.IGNORECASE)
ANTECEDENT_CONTEXT_CHARS = 600


# ===========================================================================
# Surface evidence
# ===========================================================================

_WORD = re.compile(r"\S+")

# --- Cyrillic --------------------------------------------------------------
#: Russian converbs (деепричастия) open a recital: учитывая, отмечая, признавая.
#: They are non-finite, which is exactly why V1's finite-verb search missed them
#: and its nominal fallback mis-analysed the clause.
_RU_CONVERB = re.compile(r"^\w{4,}(?:ая|яя|ив|ав|вши|ши|уя|юя)$", re.IGNORECASE)
_RU_RECITAL_HEAD = re.compile(
    r"^(учитывая|принимая|сознавая|признавая|стремясь|подтверждая|ссылаясь|"
    r"отмечая|будучи|рассмотрев|заслушав|напоминая|приветствуя|выражая|"
    r"подчеркивая|осознавая|исходя|руководствуясь)$", re.IGNORECASE)
_RU_OPERATIVE = re.compile(
    r"^(постановляет|поручает|просит|предлагает|призывает|рекомендует|"
    r"утверждает|принимает|назначает|уполномочивает|настоятельно|одобряет|"
    r"благодарит|приветствует|подтверждает|учреждает)$", re.IGNORECASE)
_RU_SHORT_PARTICIPLE = re.compile(
    r"^\w{3,}(ён|ен|ена|ено|ены|ан|ана|ано|аны|ят|ята)$", re.IGNORECASE)
_RU_REFLEXIVE = re.compile(r"^\w{4,}(ся|сь)$", re.IGNORECASE)
_RU_COPULA = re.compile(
    r"^(является|являются|являлся|был|была|были|было|будет|будут|есть|"
    r"стал|стала|стало|стали|считается|признается|признаётся)$", re.IGNORECASE)
_RU_MODAL = re.compile(
    r"^(должен|должна|должно|должны|может|можно|могут|обязан|обязана|обязано|"
    r"обязаны|вправе|следует|надлежит|нельзя|необходимо|запрещено|"
    r"запрещается|допускается)$", re.IGNORECASE)
_RU_FINITE = re.compile(
    r"^\w{3,}(ет|ёт|ут|ют|ит|ат|ят|ешь|ишь|ем|им|ете|ите|ал|ала|али|ало|"
    r"ил|ила|или|ило)$", re.IGNORECASE)
_RU_INFINITIVE = re.compile(r"^\w{3,}(ть|ться|ти|чь)$", re.IGNORECASE)
#: Oblique endings.  A noun in an oblique case is not a nominative subject, and
#: V1 selecting one was a recorded critical failure class.
_RU_OBLIQUE = re.compile(
    r"\w{3,}(ого|ому|ыми|ими|ами|ями|ах|ях|ою|ею|ей|ой|ию|ью|у|ю|е|и)$",
    re.IGNORECASE)
_RU_NOMINATIVE_HINT = re.compile(
    r"\w{2,}(ие|ые|ия|ка|ция|ство|ость|ель|тель|ент|ор|ер|а|я|о|й|ь)$",
    re.IGNORECASE)
_RU_PRONOUN = re.compile(r"^(он|она|оно|они|это|этот|эта|эти|тот|та|те|такое|"
                         r"такие|который|которые)$", re.IGNORECASE)
_RU_INSTRUMENTAL_AGENT = re.compile(r"\w{4,}(ом|ем|ой|ей|ами|ями)$", re.IGNORECASE)
_RU_NEGATIVE_QUANTIFIER = re.compile(r"^ни$", re.IGNORECASE)
_RU_ONE_DETERMINER = re.compile(
    r"^(один|одна|одно|одни|одного|одной|одному|одним|одними)$",
    re.IGNORECASE)
_RU_SENTENTIAL_NEGATION = re.compile(r"^не$", re.IGNORECASE)
_RU_PREPOSITION = re.compile(
    r"^(в|во|на|под|над|между|из|от|до|для|к|ко|по|с|со|у|о|об|при|"
    r"через|без|перед|за|после|против|согласно|помимо)$",
    re.IGNORECASE)
_RU_RELATIVE_BOUNDARY = re.compile(
    r"^(который|которая|которое|которые|которого|которой|которых)$",
    re.IGNORECASE)

# --- Arabic ----------------------------------------------------------------
_AR_RECITAL_HEAD = re.compile(r"^(وإذ|إذ|اعترافا|إدراكا|رغبة|اقتناعا|وقد|إذا)$")
_AR_VERB_HEAD = re.compile(r"^(?:و|ف|ل|س|ثم)?([يتنأ][\u0621-\u065f]{2,})$")
_AR_DEONTIC = re.compile(
    r"^(?:و|ف)?(يجب|يجوز|ينبغي|يتعين|يلزم|يحظر|يُحظر|يمنع|يُمنع|يشترط|يُشترط)$")
_AR_PASSIVE = re.compile(r"^(?:و|ف)?[يت]ُ[\u0621-\u065f]{2,}$")
#: The definite article.  Reading "الـ" as an imperfect verbal prefix was a
#: named V1 failure and is refused by construction here.
_AR_DEFINITE = re.compile(r"^ال[\u0621-\u065f]{2,}")
_AR_PRONOUN = re.compile(r"^(هو|هي|هم|هن|ذلك|هذا|هذه|تلك|هؤلاء|الذي|الذين|التي)$")
_AR_PREP = re.compile(r"^(على|في|من|إلى|عن|مع|بين|خلال|وفق|وفقا|وفقاً|بموجب|لـ|ل)$")
_AR_NEG = re.compile(r"^(لا|لم|لن|ما|ليس|ليست)$")
_AR_MASDAR = re.compile(r"^(?:و)?(?:ت|م)?[\u0621-\u065f]{4,}$")
_AR_ISTIFAL_ACTION_NOMINAL = re.compile(
    r"^(?:و)?است[\u0621-\u064a][\u0621-\u064a]ا[\u0621-\u064a]$")
#: The existential particle: "هناك 4 أنماط" — there are four types.
_AR_EXISTENTIAL = re.compile(r"^(?:و|ف)?(هناك|هنالك|ثمة|ثمّة)$")
#: A nominal carrying the proclitic لـ.  This is only ever *part* of the evidence
#: for fronted predication -- on its own it is a beneficiary, a possessor or an
#: adjunct, so it never licenses a predicate by itself.
_AR_LAM_NOMINAL = re.compile(r"^ل([ء-ٟ]{3,})$")
#: أن introduces the delayed nominal subject of a fronted predicate.
_AR_SUBORDINATOR = re.compile(r"^(أن|أنّ|إن|إنّ)$")
#: Coordinating conjunction, written as a proclitic و.
_AR_CONJUNCTION = re.compile(r"^و[ء-ٟ]{2,}$|^و$")
#: How far the delayed subject may sit from the fronted phrase.  The phrase is a
#: nominal, optionally in a construct chain, so this is small by construction.
AR_FRONTED_PREDICATE_MAX_GAP = 4
_AR_POSTNOMINAL_ADVERB = re.compile(
    r"^(أيضا|أيضاً|حاليا|حالياً|عادة|فقط)$")
MAX_AR_SUBJECT_CONSTITUENT_TOKENS = 8

# --- Latin script ----------------------------------------------------------
_LAT_RECITAL_HEAD = re.compile(
    r"^(whereas|considering|recognizing|recognising|recalling|reaffirming|"
    r"noting|bearing|desiring|having|convinced|determined|emphasizing|"
    r"emphasising|welcoming|acknowledging|aware|affirming|"
    r"consid[eé]rant|reconnaissant|rappelant|r[eé]affirmant|notant|vu|vue|vus|"
    r"vues|soucieux|convaincus?|"
    r"considerando|reconociendo|recordando|reafirmando|deseando|convencidos?|"
    r"teniendo|visto|vista|visti|viste|considerato|considerati|riconoscendo|"
    r"gest[uü]tzt|eingedenk|[uü]berzeugt)$", re.IGNORECASE)
_LAT_FINITE = re.compile(
    r"^(is|are|was|were|be|been|has|have|had|does|do|did|shall|will|would|"
    r"can|could|may|might|must|ought|ist|sind|war|waren|wird|werden|wurde|"
    r"wurden|hat|haben|kann|k[oö]nnen|muss|m[uü]ssen|soll|sollen|darf|d[uü]rfen|"
    r"est|sont|[eé]tait|[eé]taient|sera|seront|a|ont|peut|peuvent|doit|doivent|"
    r"es|son|era|eran|ser[aá]|ser[aá]n|ha|han|puede|pueden|debe|deben|"
    r"[eè]|sono|era|erano|deve|devono|pu[oò]|possono)$", re.IGNORECASE)
_LAT_DEONTIC = re.compile(
    r"^(shall|must|may|should|ought|soll|sollen|muss|m[uü]ssen|darf|d[uü]rfen|"
    r"doit|doivent|peut|peuvent|debe|deben|puede|pueden|podr[aá]|podr[aá]n|"
    r"deve|devono|pu[oò]|possono)$", re.IGNORECASE)
_LAT_PARTICIPLE = re.compile(
    r"^\w{4,}(ing|ed|ando|iendo|ant|ent|"
    r"[eé]|[eé]e|[eé]s|[eé]es|"
    r"ado|ada|ados|adas|ido|ida|idos|idas|"
    r"ato|ata|ati|ate|ito|ita|iti|ite|uto|uta|uti|ute|"
    r"t|en)$", re.IGNORECASE)
_LAT_PASSIVE_AUX = re.compile(
    r"^(is|are|was|were|been|being|wird|werden|wurde|wurden|worden|"
    r"est|sont|[eé]t[eé]|es|son|fue|fueron|[eè]|sono|viene|vengono)$",
    re.IGNORECASE)
_LAT_ARTICLE = re.compile(
    r"^(the|a|an|der|die|das|den|dem|des|ein|eine|einen|le|la|les|un|une|"
    r"du|el|los|las|una|unos|unas|il|lo|gli|i)$", re.IGNORECASE)
_LAT_PRONOUN = re.compile(
    r"^(it|they|he|she|this|that|these|those|such|es|sie|er|dies|diese|"
    r"il|elle|ils|elles|ce|cette|este|esta|esto|estos|ella|ello|questo|questa)$",
    re.IGNORECASE)
_EXPLETIVE = re.compile(r"^(there|it|es|il|se|hay|ci)$", re.IGNORECASE)
#: A bare infinitive heading a directive item.  "(12) внедрять механизмы",
#: "evitar el contacto directo", "se couvrir la bouche" -- the predicate is the
#: infinitive and the frame comes from the governing lead-in.  These are
#: non-finite, so they are proposed at low confidence and cannot reach
#: EVIDENCE_BOUND on their own; the point is that the analysis exists at all.
#: Romance reflexive clitics.  These belong to the verb: they may shift the
#: head one token to the right and they widen its span, but they are never
#: an argument in their own right.
_LAT_PREPOSITION = re.compile(r"^(in|an|auf|aus|bei|mit|nach|von|vor|zu|zur|zum|ueber|über|unter|durch|fuer|für|gegen|ohne|um|hierzu|dabei|de|del|du|des|of|to|for|with|by)$", re.IGNORECASE)
_LAT_NEGATION = re.compile(r"^(nicht|kein|keine|keinen|ne|pas|no|not)$", re.IGNORECASE)
#: Infinitive auxiliaries: "peuvent ETRE etablis".  These differ from the
#: finite auxiliaries in _LAT_PASSIVE_AUX, which are already inflected.
_LAT_INF_AUX = re.compile(r"^(être|etre|ser|estar|essere|werden|be|worden)$", re.IGNORECASE)
#: Past-participle shapes, including the French -ir family (etabli/etablis).
_LAT_CHAIN_PARTICIPLE = re.compile(r"^\w{3,}(é|ée|és|ées|i|ie|is|ies|u|ue|us|ues|ed|en|t|to|ta|ti|te|do|da|dos|das)$", re.IGNORECASE)
#: French subject clitics.  A clitic immediately before a word is grammatical
#: evidence that the word is a finite verb -- evidence a bare -e suffix rule
#: cannot supply, since -e is also the commonest noun and adjective ending.
_LAT_SUBJECT_CLITIC = re.compile(r"^(il|elle|on|ils|elles|je|tu|nous|vous)$", re.IGNORECASE)
#: A Germanic clause-final verb cluster: the infinitive or participle that a
#: modal governs stands immediately BEFORE it in a subordinate clause --
#: "... entzogen werden sollen".
_LAT_VERB_CLUSTER = re.compile(r"\b\w{3,}(en|den|ten|ren|elt|iert|acht|ommen)\s*$",
                               re.IGNORECASE)
_LAT_REFLEXIVE_CLITIC = re.compile(r"^(se|s'|s’|si)$", re.IGNORECASE)
_LAT_INFINITIVE = re.compile(
    r"^(?:se\s+)?\w{3,}(?:ar|er|ir|re|arse|erse|irse|are|ere|ire|en)$",
    re.IGNORECASE)
#: A copular infinitive inside a modal chain.  "shall be interpreted" is passive
#: and its grammatical subject is a patient; matching the modal first and
#: stopping there reported three reference passives as explicit subjects.
_LAT_PASSIVE_INFINITIVE = re.compile(
    r"^(be|been|être|etre|essere|ser|sein|werden)$", re.IGNORECASE)
#: A lexical finite verb, proposed rather than asserted.  V1 and the first V2
#: draft recognised only auxiliaries, copulas and modals, so an ordinary clause
#: ("The Assembly adopted the report") produced no predicate candidate at all.
#: This is bounded morphology, not parsing: it proposes, and the hard
#: constraints and separation margin decide.
_LAT_LEXICAL_FINITE = re.compile(
    r"^\w{3,}(?:s|es|ed|ent|ait|era|[oó]|[aá]|isce|isce|ano|ono|te|t|en)$",
    re.IGNORECASE)
_LAT_DEFINITION_FINITE = re.compile(
    r"^[^\W\d_]{4,}(?:e|ent|ait|aient|era|eront)$",
    re.IGNORECASE | re.UNICODE)
_LAT_BARE_ENGLISH_VERB = re.compile(
    r"^[A-Za-z][A-Za-z’'-]{2,}$")
#: Tokens that look verbal by suffix but are function words or common nominals.
_LAT_NOT_A_VERB = re.compile(
    r"^(this|these|those|its|his|hers|ours|yours|theirs|as|has|was|is|does|"
    r"des|les|ses|mes|tes|aux|dans|sous|entre|autres|"
    r"las|los|des|des|unas|unos|"
    r"eines|seines|ihres|dieses|jenes|alles|"
    r"delle|degli|dalle|nelle|sulle|"
    r"states|members|parties|measures|services|rights|means|goods|"
    r"provisions|principles|purposes|policies|activities|resources)$",
    re.IGNORECASE)

# --- shared ----------------------------------------------------------------
_INSTITUTION_HEAD = re.compile(
    r"\b(organization|organisation|commission|council|parliament|agency|"
    r"authority|ministry|ministerium|minist[eè]re|ministerio|office|bureau|"
    r"secretariat|court|tribunal|assembly|committee|department|directorate|"
    r"board|bundesamt|beh[oö]rde|organisme|organizaci[oó]n|presidente|"
    r"منظمة|الأمانة|المحكمة|الوزارة|الهيئة|اللجنة|المجلس|الجمعية|"
    r"организация|министерство|правительство|комитет|совет|суд|ассамблея|"
    r"бюро|секретариат|директор|федеральн\w*|ведомств\w*)\b", re.IGNORECASE)
_ATTRIBUTION_MARKER = re.compile(
    r"\b(according to|as reported by|stated by|said|reported|announced|"
    r"laut|zufolge|nach angaben|selon|d'apr[eè]s|seg[uú]n|de acuerdo con|"
    r"secondo|"
    r"وفقاً ل|بحسب|حسب|أعلن|صرح|"
    r"по данным|согласно|как сообщает|заявил)\b", re.IGNORECASE)
_QUOTE_OPEN = re.compile(r"[\"“«„‟]")
_QUOTE_CLOSE = re.compile(r"[\"”»]")
_COORD = re.compile(r"^(and|or|und|oder|et|ou|y|e|o|و|أو|и|или)$", re.IGNORECASE)
_ENUM_HEAD = re.compile(
    r"^\s*(?:\(?\d{1,3}[.)]|\(?[a-zA-Z][.)]|[-–—•*]|[IVXLC]{1,6}[.)])\s+")
_DATE_LINE = re.compile(
    r"^[^.]{0,60}\b(\d{1,2}\s+\w+\s+\d{4}|\d{4}\s*г\.?|\d{1,2}\.\d{1,2}\.\d{4})"
    r"[^.]{0,30}$")
_DOC_SYMBOL = re.compile(r"\b(WHA|EB|A\d{2}|B\d{3})[\d.\-/]+\b")


#: How many tokens a leading nominal constituent may span before it stops being
#: a constituent and starts being the clause.
MAX_SUBJECT_CONSTITUENT_TOKENS = 4
#: Words that open a resumptive main clause after a fronted subordinate clause.
#: German is V2, so the subject follows the finite verb in that configuration.
_LAT_RESUMPTIVE = re.compile(r"^(so|dann|insoweit|alsdann)$", re.IGNORECASE)


def _leading_nominal_constituent(run) -> int:
    """Where the leading nominal constituent of `run` ends (exclusive index).

    V581-D43.  "everything before the finite verb" is not a subject span in a
    verb-final or middle-field-heavy clause: for "sie hierzu aus rechtlichen
    Gruenden nicht in der Lage ist" it returns the whole clause, which then
    "contains" the reference subject `sie` and passes an overlap test while
    being semantically useless.  The constituent ends at the first preposition,
    negation or adverbial boundary, or after a small number of tokens.
    """
    end = 0
    for offset, (word, _start, _stop) in enumerate(run):
        if offset >= MAX_SUBJECT_CONSTITUENT_TOKENS:
            break
        if offset and (_LAT_PREPOSITION.match(word) or _LAT_NEGATION.match(word)):
            break
        end = offset + 1
        # German capitalises nouns, so a capitalised token closes the phrase it
        # heads: "der Erblasser", "die Strafe".  Without this the constituent ran
        # to its token limit and graded oversized against a two-word reference.
        if offset and word[:1].isupper():
            break
        # A pronoun or a quoted term stands alone.
        if offset == 0 and (_LAT_PRONOUN.match(word)
                            or word[:1] in "„\"«'"):
            break
    return max(end, 1)


def _directive_head_index(words: Sequence[tuple[str, int, int]]) -> int:
    """How far in the directive's own head may sit, past any enumeration marker."""
    if not words:
        return 0
    first = words[0][0]
    if _ENUM_HEAD.match(first + " ") or re.match(r"^\(?\d{1,3}[.)]?$", first) \
            or re.match(r"^[-–—•*]$", first):
        return 1
    # A reflexive clitic is part of the verb, not a word standing before it:
    # "se couvrir la bouche" has its head at index 1 and its span covers both
    # tokens.  The clitic is not an argument and must not be read as a subject.
    if _LAT_REFLEXIVE_CLITIC.match(first):
        return 1
    return 0


def _tokens(text: str) -> list[tuple[str, int, int]]:
    return [(m.group(0), m.start(), m.end())
            for m in _WORD.finditer(text[:MAX_SCAN_CHARS])]


def _clean(word: str) -> str:
    return word.strip(".,;:()[]«»\"'“”„‟؛،—–-")


def script_of(text: str) -> str:
    return CL.script_of(text)


def _span_is_sentence_initial(text: str, span: tuple[int, int] | None) -> bool:
    """Whether NO WORD TOKEN precedes ``span`` in ``text``.

    V6.1 T3-R1, isolated by the V6.1 isolation seat as P6.4-R1.

    ``predicate_types.classify`` guards one rule with ``span_initial``, and the
    rule it guards is stated in that module's own comment: "German orthography:
    a capitalised NON-INITIAL token is a noun."  That is a claim about
    SENTENCE-INITIAL POSITION.  Leading whitespace does not move a token out of
    sentence-initial position, so the two call sites must not decide the
    question by asking whether the span starts at literal character offset 0.

    They did.  ``bind`` takes ``body = (text or '')[:MAX_SCAN_CHARS]`` with no
    strip, so a single leading space moved a recital head's span from (0, 7) to
    (1, 8), ``span_initial`` flipped True -> False, the head was typed
    ARGUMENT_NOUN by ``_GERMAN_NOUN_SHAPE``, the V581-D42-M6 structural filter
    dropped the recital analysis, and the published record fell from PARTIAL /
    RECITAL_RELATION_PREDICATE to UNRESOLVED / PREDICATE_UNRESOLVED with a null
    predicate span.  M6's own "skip the filter when it would empty the pool"
    valve cannot fire, because the NULL analysis has ``predicate_head is None``
    and therefore survives the filter and keeps the pool non-empty.

    What is repaired here is the MEANING of ``span_initial`` and nothing else.
    ``_GERMAN_NOUN_SHAPE`` is NOT widened or narrowed -- the regex is correct
    about German orthography, and moving it would move genuine noun typings.
    The M6 filter is NOT touched -- it is being fed a wrong typing, not making
    a wrong decision.  No score, threshold, denominator, trim set, ruler or
    truth artifact is involved.

    A span of ``None`` returns False, which is what both call sites already
    computed for it.
    """
    if span is None:
        return False
    return not text[:span[0]].strip()


def _declared_language(language: object) -> str | None:
    """The caller's declared language tag, normalised ONCE, at ingress.

    P6.2 R5, authorised as ADJ-1, hardened by P6.3 workstream A.  This module
    compared the RAW string at ten sites (the local-pronominal inventory, the
    Russian relative, the two German relation attachments, the M10 copula and
    M11 modal inventories, and the M12/M13 language guards), so a tag that was
    not already the lowercase two-letter code -- "RU", "Russian", "ru-RU" --
    selected the foreign-script FAMILY in `clauses.py` while losing the
    corresponding role machinery here.  The two regimes disagreed on the
    PUBLICATION DISPOSITION, not merely on diagnostics: on the frozen axis body
    `decisive_ru`, "ru" published EVIDENCE_BOUND and "RU" published
    QUARANTINED.

    Normalising here, at every public entry point that accepts the tag, makes
    all ten comparisons read one value derived one way.  The function is
    IDEMPOTENT, so an entry point that forwards to another normalises nothing
    twice.

    WHICH RULE, AND WHY THIS ONE.  There is no single normalisation in the
    operational tree; there are FOUR live regimes, and the earlier wording of
    this docstring -- that `clauses.py:376` "is the only normalisation in the
    operational tree" -- was false.  The others are `v5_1/extraction.py:253`
    (`_langs`, live on this same production path through `v5_2/semantics.py`
    and `v5_4/invariants.py`), `v5_2/lifecycle.py:520` and `:577`, and
    `v5_4/calibration.py:80`.  Those three agree with each other on
    ``(language or "").strip().casefold()[:2]``; `clauses.py:376` uses
    ``(language or "").lower()[:2]``, which differs only in leading and
    trailing whitespace and in the handful of characters whose case folding is
    not their lower case.  P6.3 A3 adopts the THREE-REGIME rule -- strip, then
    casefold, then the two-character prefix -- so this module now agrees with
    every one of them on every ASCII tag and with the majority on the rest.
    Before that alignment a leading-whitespace tag was unstable across the
    tree: ``_langs(" de") == "de"`` selected the German lexicon in the V5.2
    splitter while ``_declared_language(" de") == " d"`` selected no German
    machinery here.

    `None` is preserved as `None` rather than becoming `""`, and every
    comparison site in this module treats those two identically (membership
    tests miss both, ``x or ""`` maps both to ``""``, and no equality test can
    match either).

    TOTALITY (P6.3 A2).  `language` is an UNVALIDATED caller value: nothing
    between `v5_1/campaign.py:201`, `v4/models.py`, `v4/pipeline.py:42` and
    `v5_4/invariants.py:299` enforces the `str` annotation, and a TRUTHY
    non-string passes `invariants.py:299`'s ``or "en"`` untouched.  A non-`str`
    therefore reaches this function on real input, where ``.lower()`` raised
    AttributeError and destroyed even the lawful refusal route.  Anything that
    is not a `str` is read as NO DECLARED LANGUAGE and returns `None`: that is
    the lawful reading of a value this module cannot interpret, and it is
    total, so no ingress and no future caller can be crashed by the parameter.

    This does not validate the tag.  It DOES widen this module's recognition
    rule: the tag set selecting Russian machinery grew from exactly {"ru"} to
    every tag whose normalised two-character prefix is "ru", and likewise for
    the other six.  What it does not do is invent a NEW system-level policy --
    `clauses.py:376` and `_langs` already recognised tags this way, and the
    two-character prefix collisions they carry (est->es, run->ru, fry->fr,
    arg->ar, del->de, itl->it) are inherited unchanged rather than created
    here.  It removes a normalisation DISAGREEMENT; a validated language-tag
    vocabulary at ingestion is a separate, unauthorised change.
    """
    if not isinstance(language, str):
        return None
    return language.strip().casefold()[:2]


# ===========================================================================
# §15 — typed candidates
# ===========================================================================

ROLE_TYPES: tuple[str, ...] = (
    "SUBJECT", "PREDICATE_HEAD", "PREDICATE_COMPLEMENT", "GOVERNING_CLAUSE",
    "ANTECEDENT", "SEMANTIC_ACTOR", "INSTITUTIONAL_ISSUER", "ATTRIBUTION",
    "QUOTED_SPEAKER",
)

ROLE_RELATION_TYPES: tuple[str, ...] = (
    "LOCAL_OVERT_PRONOMINAL_SUBJECT_OF",
    "LOCAL_SUBJECT_UNIQUE_CLOSED_FINITE_ANCHOR",
    "LOCAL_SUBJECT_GERMAN_LEXICAL_FINITE",
    "LOCAL_SUBJECT_REQUIRED_TO_DEONTIC_FRAME",
    "POST_RELATIVE_MODAL_RESUMES_SUBJECT",
)


@dataclass(frozen=True)
class Candidate(Record):
    """One proposal for one role, with the evidence that proposed it."""

    candidate_id: str
    role_type: str
    span: tuple[int, int] | None
    text: str
    origin_rule: str
    morphological: tuple[str, ...] = ()
    syntactic: tuple[str, ...] = ()
    structural: tuple[str, ...] = ()
    discourse: tuple[str, ...] = ()
    confidence: float = 0.0
    subtype: str = ""
    #: V581-D38B.  What the token IS, orthogonal to origin_rule, which records
    #: what PROPOSED it.  Both are carried; neither replaces the other, because
    #: the origin rules encode passive / deontic / participial distinctions that
    #: the reference's own predicate_state vocabulary uses and that D39 -- still
    #: deferred -- will need.
    lexical_category: str = "UNRESOLVED"
    predicate_type: str = "UNRESOLVED"
    #: V581-D42C.  The three confidence components stay separately inspectable;
    #: the raw base is never overwritten by the position-adjusted value.
    base_confidence: float = 0.0
    token_index: int | None = None

    def __post_init__(self) -> None:
        if self.role_type not in ROLE_TYPES:
            raise RoleBindingV2Violation(f"unknown role type {self.role_type!r}")
        if self.span is not None and self.span[0] >= self.span[1]:
            raise RoleBindingV2Violation(f"empty span {self.span}")


@dataclass(frozen=True)
class RoleRelation(Record):
    """Source-grounded relation between two already generated role candidates.

    A relation records grammatical structure; it does not resolve discourse
    reference.  In particular, an overt pronoun can be the local grammatical
    subject of a predicate while its antecedent remains recoverable or unknown.
    """

    relation_id: str
    relation_type: str
    subject_candidate_id: str
    predicate_head_candidate_id: str
    clause_id: str
    evidence_span: tuple[int, int]
    evidence_kind: str
    asserting_component: str = "v5_8_1.roles_v2"
    decisiveness: str = "DECISIVE"

    def __post_init__(self) -> None:
        if self.relation_type not in ROLE_RELATION_TYPES:
            raise RoleBindingV2Violation(
                f"unknown role relation type {self.relation_type!r}")
        if self.evidence_span[0] >= self.evidence_span[1]:
            raise RoleBindingV2Violation(
                f"empty relation evidence span {self.evidence_span}")


# V581-D42C POSITIONAL SATURATION — IMPLEMENTED, MEASURED, REVERTED.
#
# The saturation is real and was measured exactly: the hard clip
# max(0.05, base - 0.10 * index) put 218 of 411 predicate-head candidates on the
# floor, 207 of them at token index 8 or beyond, destroying all resolution in
# German verb-final clauses and long enumerated provisions.  The authorised
# matched-slope exponential continuation removed it completely -- floor count
# 218 -> 0, positional prior still active, and the corpus-wide discrimination
# widened (reference-matching heads 0.3802 -> 0.4531 against 0.1332 -> 0.1892).
#
# MEASURED CONSEQUENCE: D42 selection fell 90/120 -> 88/120.  Repaired 0,
# regressed 2, both INSUFFICIENT_ROLE_EVIDENCE -> AMBIGUOUS_BINDING.  The floor
# was doing ACCIDENTAL WORK: by pinning late heads at 0.05 it kept them below the
# null reading's 0.30, and in those two units the null reading was correct.
# Removing the saturation let a weak late head win and repaired nothing.
# "D42 does not improve above 90/120" is a declared revert condition, so the
# transform is deleted rather than bypassed.  Evidence: 268_d42c_saturation_repair/.
#
# base_confidence and token_index are RETAINED: §5 of the authorisation
# explicitly permits the diagnostics that expose them, and the audit depends on
# them.  They are inert with respect to production behaviour.

def _candidate(role: str, span, text: str, rule: str, *, confidence: float,
               subtype: str = "", morphological=(), syntactic=(),
               structural=(), discourse=(), base_confidence: float | None = None,
               token_index: int | None = None) -> Candidate:
    return Candidate(
        stable_id("v5-8-1-cand", role, rule, text[:60], str(span)),
        role, span, text, rule, tuple(morphological), tuple(syntactic),
        tuple(structural), tuple(discourse), round(confidence, 4), subtype,
        base_confidence=(round(base_confidence, 4)
                         if base_confidence is not None else round(confidence, 4)),
        token_index=token_index)


# ---------------------------------------------------------------------------
# Non-proposition evidence — the state V1 could not express
# ---------------------------------------------------------------------------

def non_proposition_evidence(text: str, tokens: Sequence[tuple[str, int, int]],
                             script: str) -> list[str]:
    """Why this span asserts nothing, in the source's own terms.

    V1 had only SUBJECT_UNRESOLVED, which asserts that a subject exists and was
    not found.  A masthead has no subject to find, and the reference says so
    with NO_SEMANTIC_SUBJECT.  Conflating the two turned every heading into an
    unresolved clause and was a large part of the hard-negative failure.
    """
    stripped = text.strip()
    signals: list[str] = []
    words = [_clean(w) for w, _, _ in tokens]
    if not words:
        return ["EMPTY_SPAN"]

    letters = [c for c in stripped if c.isalpha()]
    if letters and sum(1 for c in letters if c.isupper()) / len(letters) > 0.85 \
            and len(words) <= 12:
        signals.append("ALL_CAPS_RUBRIC")
    if _DATE_LINE.match(stripped) and len(words) <= 10:
        signals.append("DATE_OR_SESSION_LINE")
    symbol = _DOC_SYMBOL.search(stripped)
    if symbol:
        # A masthead is a symbol with a few words around it.  A recital that
        # *cites* an instrument -- "Recalling resolution WHA65.4 on ..." -- is
        # prose, and refusing it as furniture discards the very construction
        # D25 exists to bind.  So the symbol must dominate the line.
        remainder = len(re.findall(r"\S+", stripped.replace(symbol.group(0), " ")))
        if remainder <= 4:
            signals.append("DOCUMENT_SYMBOL_LINE")
    if len(words) <= 6 and not stripped.endswith((".", "!", "?", "؟", ";", ":")):
        signals.append("SHORT_UNTERMINATED_FRAGMENT")

    verbal = _has_verbal_evidence(words, script)
    if not verbal:
        signals.append("NO_VERBAL_OR_PREDICATIVE_EVIDENCE")

    # A line lifted out of a paragraph starts in the middle of a constituent and
    # stops before its clause ends.  It may still contain a verb, so the verbal
    # test above cannot see it; the reference calls these INVALID.
    opens_mid = bool(re.match(r"^\s*[a-zà-öø-ÿа-я]", stripped)) and \
        not _ENUM_HEAD.match(stripped)
    terminated = stripped.endswith((".", "!", "?", "؟", ";", ":", "»", '"'))
    if opens_mid and not terminated:
        signals.append("OPENS_MID_CLAUSE_AND_UNTERMINATED")
    if stripped.endswith(("-", "–", "—")) or re.search(r"\w-\s*$", stripped):
        signals.append("HYPHENATED_LINE_BREAK")
    return signals


def _has_verbal_evidence(words: Sequence[str], script: str) -> bool:
    if script == "CYRILLIC":
        return any(_RU_RECITAL_HEAD.match(w) or _RU_CONVERB.match(w)
                   or _RU_OPERATIVE.match(w) or _RU_FINITE.match(w)
                   or _RU_COPULA.match(w) or _RU_MODAL.match(w)
                   or _RU_SHORT_PARTICIPLE.match(w) or _RU_REFLEXIVE.match(w)
                   or _RU_INFINITIVE.match(w) for w in words)
    if script == "ARABIC":
        return any(_AR_RECITAL_HEAD.match(w) or _AR_VERB_HEAD.match(w)
                   or _AR_DEONTIC.match(w) or _AR_PASSIVE.match(w)
                   for w in words)
    # D33.  The non-proposition test and the candidate generator must share one
    # notion of verbal evidence.  While they disagreed, an ordinary lexical
    # clause was refused as a non-proposition before its candidates were ever
    # built -- the generator's repair could not fire.
    if any(_LAT_RECITAL_HEAD.match(w) or _LAT_FINITE.match(w)
           or _LAT_DEONTIC.match(w) or _LAT_PARTICIPLE.match(w) for w in words):
        return True
    return any(_LAT_LEXICAL_FINITE.match(w) and not _LAT_NOT_A_VERB.match(w)
               and not _LAT_ARTICLE.match(w) and not _LAT_PRONOUN.match(w)
               for w in words[1:])


# ---------------------------------------------------------------------------
# §19 — language adapters, each proposing candidates for a shared role model
# ---------------------------------------------------------------------------

def _recital_candidates(text: str, tokens, script: str) -> list[Candidate]:
    """A recital predicates a relation to a governing enactment clause.

    Its subject is not in the span and must not be invented there; the reference
    is unanimous on this across 20 units in four languages.  The predicate is the
    whole recital, headed by the converb, participle or particle.
    """
    if not tokens:
        return []
    words = [(_clean(w), s, e) for w, s, e in tokens]
    head = words[0]
    matched = False
    if script == "CYRILLIC":
        matched = bool(_RU_RECITAL_HEAD.match(head[0]) or _RU_CONVERB.match(head[0]))
    elif script == "ARABIC":
        matched = bool(_AR_RECITAL_HEAD.match(head[0]))
    else:
        matched = bool(_LAT_RECITAL_HEAD.match(head[0]))
    if not matched:
        return []
    # A recital head followed by a finite operative verb is not a recital: it is
    # an ordinary clause that happens to start with a participle.
    tail = [w for w, _, _ in words[1:]]
    if script == "CYRILLIC" and any(_RU_OPERATIVE.match(w) for w in tail[:2]):
        return []
    end = tokens[-1][2]
    out = [
        _candidate("PREDICATE_HEAD", (head[1], head[2]), head[0],
                   "RECITAL_HEAD", confidence=0.88,
                   subtype="RECITAL_RELATION_PREDICATE",
                   morphological=("NON_FINITE_RECITAL_HEAD",),
                   discourse=("RECITAL_OPENS_SPAN",)),
    ]
    # P6.3 A6.  A HEAD-FINAL recital has no body: for "Considérant",
    # "Учитывая", "وإذ" alone -- and for any recital whose head is its last
    # word token -- `end` IS `head[2]`, so this complement's span was (n, n)
    # and `Candidate.__post_init__` refused it with
    # `RoleBindingV2Violation("empty span (n, n)")` before any of the
    # substrate's own routes could run.  The span is NOT empty and the token
    # list is NOT empty, so this is a different class from P6.2's R6 and R6
    # never touched it: measured identical on P6.1 ad21425a and P6.2
    # ac68abb6, and reachable on real text, because a recital head is a whole
    # region whenever the extractor's segmentation puts the line break there.
    #
    # The candidate that cannot exist is dropped and nothing else changes.
    # `Candidate.__post_init__` is RIGHT that a role candidate needs a
    # non-empty span; the generator was wrong to build one, so the guard
    # belongs here.  The three sibling generators that derive a complement
    # the same way -- `_russian_candidates`, `_arabic_candidates` and
    # `_latin_candidates` -- already carry exactly this `stop < end` test;
    # this site was the one that did not.  The recital head and the governing
    # enactment requirement are still emitted, because they are still true:
    # the head matched, and a recital still predicates a relation to a
    # governing clause that is not in the span.
    if head[2] < end:
        out.append(
            _candidate("PREDICATE_COMPLEMENT", (head[2], end),
                       text[head[2]:end].strip(),
                       "RECITAL_BODY", confidence=0.8,
                       discourse=("RECITAL_BODY",)))
    out.append(
        _candidate("GOVERNING_CLAUSE", None, "", "RECITAL_REQUIRES_ENACTMENT",
                   confidence=0.85,
                   discourse=("SUBJECT_IS_GOVERNING_ENACTMENT_CLAUSE",)))
    return out


def _russian_structural_subjects(text: str, words, heads) -> list[Candidate]:
    """Propose bounded Russian subject constituents licensed by structure.

    The ordinary nominal adapter deliberately proposes small noun-like units.
    Two constructions require the constituent itself to be present as an
    alternative: a negative-quantifier NP before a modal, and a coordinated
    patient after a passive/reflexive predicate.  These candidates supplement
    the small units; they do not delete or select among them.
    """
    out: list[Candidate] = []
    head_by_span = {
        candidate.span: candidate
        for candidate in heads
        if candidate.role_type == "PREDICATE_HEAD" and candidate.span is not None
    }

    # `ни одна страна не должна`: the three words are one quantified NP.
    for index in range(max(0, len(words) - 2)):
        if not _RU_NEGATIVE_QUANTIFIER.match(words[index][0]):
            continue
        if not _RU_ONE_DETERMINER.match(words[index + 1][0]):
            continue
        nominal = words[index + 2]
        if (_COORD.match(nominal[0]) or _RU_INFINITIVE.match(nominal[0])
                or _RU_REFLEXIVE.match(nominal[0])):
            continue
        cursor = index + 3
        if cursor < len(words) and _RU_SENTENTIAL_NEGATION.match(words[cursor][0]):
            cursor += 1
        if cursor >= len(words) or not _RU_MODAL.match(words[cursor][0]):
            continue
        out.append(_candidate(
            "SUBJECT", (words[index][1], nominal[2]),
            text[words[index][1]:nominal[2]].strip(),
            "RU_NEGATIVE_QUANTIFIER_NP", confidence=0.8,
            subtype="EXPLICIT_SUBJECT",
            syntactic=("BOUNDED_CONSTITUENT", "PRECEDES_MODAL"),
            morphological=("NEGATIVE_QUANTIFIER_AGREEMENT",)))

    # A coordinated patient may follow a passive predicate.  If two passive
    # predicates are themselves coordinated, step over that verbal coordinate
    # before opening the nominal phrase.
    for index, (_word, start, stop) in enumerate(words):
        head = head_by_span.get((start, stop))
        if head is None or head.subtype != "PASSIVE_OR_IMPERSONAL_PREDICATE":
            continue
        cursor = index + 1
        if (cursor + 1 < len(words)
                and _COORD.match(words[cursor][0])
                and (next_head := head_by_span.get(
                    (words[cursor + 1][1], words[cursor + 1][2]))) is not None
                and next_head.subtype == "PASSIVE_OR_IMPERSONAL_PREDICATE"):
            cursor += 2
        if (cursor >= len(words) or _COORD.match(words[cursor][0])
                or _RU_PREPOSITION.match(words[cursor][0])):
            continue

        boundary = min(len(words), cursor + 12)
        for offset in range(cursor, boundary):
            if offset > cursor and _RU_RELATIVE_BOUNDARY.match(words[offset][0]):
                boundary = offset
                break
        coordinators = [
            offset for offset in range(cursor + 1, boundary)
            if _COORD.match(words[offset][0])
        ]
        if not coordinators:
            continue
        last_coordinator = coordinators[-1]
        phrase_stop = None
        for offset in range(last_coordinator + 1, boundary):
            raw = text[words[offset][1]:words[offset][2]].rstrip()
            if raw.endswith((",", "،", ";", "؛")):
                phrase_stop = words[offset][2]
                break
        if phrase_stop is None and boundary > last_coordinator + 1:
            phrase_stop = words[boundary - 1][2]
        if phrase_stop is None:
            continue
        while phrase_stop > words[cursor][1] and (
                text[phrase_stop - 1].isspace()
                or unicodedata.category(text[phrase_stop - 1]).startswith("P")):
            phrase_stop -= 1
        if phrase_stop <= words[last_coordinator][2]:
            continue
        out.append(_candidate(
            "SUBJECT", (words[cursor][1], phrase_stop),
            text[words[cursor][1]:phrase_stop].strip(),
            "RU_POSTVERBAL_COORDINATED_PATIENT", confidence=0.78,
            subtype="PASSIVE_PATIENT_SUBJECT",
            syntactic=(
                "BOUNDED_CONSTITUENT",
                "FOLLOWS_PASSIVE_PREDICATE",
                "COORDINATED_ALL_CONJUNCTS",
            )))
    return out


def _cyrillic_candidates(text: str, tokens) -> list[Candidate]:
    # P6.2 R6.  `tokens[-1]` below raised IndexError on an empty or
    # whitespace-only span, at every declared language, in all six
    # generations.  `_recital_candidates` already refuses the same input this
    # way; the two remaining script generators now do too.  See the guard's
    # full rationale at `_latin_candidates`.
    if not tokens:
        return []
    words = [(_clean(w), s, e) for w, s, e in tokens]
    out: list[Candidate] = []
    end = tokens[-1][2]

    for index, (word, start, stop) in enumerate(words):
        kind = None
        confidence = 0.0
        morph: tuple[str, ...] = ()
        if _RU_OPERATIVE.match(word) and index <= 1:
            kind, confidence = "EXPLICIT_FINITE_PREDICATE", 0.9
            morph = ("OPERATIVE_VERB_CLAUSE_INITIAL",)
        elif _RU_MODAL.match(word):
            kind, confidence = "DEONTIC_OPERATOR_WITH_COMPLEMENT", 0.85
            morph = ("MODAL",)
        elif _RU_COPULA.match(word):
            kind, confidence = "EXPLICIT_FINITE_PREDICATE", 0.85
            morph = ("COPULA",)
        elif _RU_SHORT_PARTICIPLE.match(word):
            kind, confidence = "PARTICIPIAL_PREDICATE", 0.8
            morph = ("SHORT_FORM_PARTICIPLE",)
        elif _RU_REFLEXIVE.match(word) and not _RU_COPULA.match(word):
            # Tried and refuted 2026-07-26: relabelling agentless -ся forms as
            # EXPLICIT_FINITE_PREDICATE, on the reasoning that a reflexive
            # passive needs an instrumental agent.  Measured on the diagnostic
            # ruler it left candidate recall unchanged at 0.85 and moved
            # predicate-state accuracy 0.550 -> 0.5417, so the reference does
            # treat agentless -ся forms as passive more often than not.  The
            # original label is kept and the experiment recorded.
            kind, confidence = "PASSIVE_OR_IMPERSONAL_PREDICATE", 0.78
            morph = ("REFLEXIVE_PASSIVE",)
        elif _RU_FINITE.match(word):
            # V581-D45.  The third-person endings -ет/-ит are shared with
            # ordinary nouns ("Комитет"), so the ending alone cannot decide.
            # Structure can: two finite verbs do not stand adjacent in a simple
            # clause, so when the next token also carries finite morphology this
            # one is the nominal and the next is the verb.  A lexicon would be
            # needed to do better; this uses only what the clause shows.
            if (index + 1 < len(words) and _RU_FINITE.match(words[index + 1][0])
                    and not _RU_COPULA.match(word)):
                continue
            kind, confidence = "EXPLICIT_FINITE_PREDICATE", 0.75
            morph = ("FINITE_ENDING",)
        elif _RU_INFINITIVE.match(word) and index <= _directive_head_index(words):
            # "(12) внедрять ..." puts the enumeration marker at index 0, so an
            # index==0 test missed every numbered directive item -- six of the
            # seventeen measured predicate-head absences.
            kind, confidence = "PREDICATE_RECOVERABLE", 0.6
            morph = ("BARE_INFINITIVE_DIRECTIVE_ITEM",)
        if kind is None:
            continue
        # The main-clause predicate is the first finite head at the top level.
        # Without a positional prior every later verb produces a rival analysis
        # of near-equal score, and the resolver reports ambiguity where the
        # source has none -- 22 reference-ESTABLISHED units became PARTIAL.
        base_confidence = confidence
        confidence = max(0.05, confidence - 0.10 * index)
        out.append(_candidate("PREDICATE_HEAD", (start, stop), word,
                              f"RU_{kind}", confidence=confidence,
                              base_confidence=base_confidence, token_index=index,
                              subtype=kind, morphological=morph,
                              syntactic=(f"HEAD_INDEX_{index}",)))
        if stop < end:
            out.append(_candidate("PREDICATE_COMPLEMENT", (stop, end),
                                  text[stop:end].strip(), "RU_COMPLEMENT",
                                  confidence=0.7))

    out.extend(_russian_structural_subjects(text, words, out))

    # Subjects: nominative-looking, not oblique, to the left or right of a head.
    for index, (word, start, stop) in enumerate(words):
        if len(word) < 3 or _RU_PRONOUN.match(word):
            continue
        # V581-D44.  A word carrying finite verbal morphology is not a nominal
        # subject.  RU_NOMINAL was emitting "подвергаются" -- the clause's own
        # finite verb -- as a subject candidate, which inflates the incompatible
        # population the selector has to choose against.
        #
        # The test is NOT "matches a finite pattern".  A first draft used that
        # and rejected "Комитет", which ends in -ет exactly as a third-person
        # singular verb does; Russian deverbal nouns, substantivised participles
        # and short-form adjectives are all reachable the same way.  Morphology
        # alone cannot carry this.
        #
        # The evidence used instead is morphology a noun cannot share.  A
        # reflexive -ся form and a -ть/-ться infinitive are unambiguously verbal;
        # the -ет of a third-person singular is not, and "Комитет" carries it.
        #
        # A collision test -- reject a subject that is also a head candidate --
        # was tried and rejected: head generation has its own -ет false positive
        # (V581-D45), so the collision rule inherited that error and removed
        # "Комитет" as a subject for the wrong reason.  Two defects, two repairs.
        if _RU_INFINITIVE.match(word) or (
                _RU_REFLEXIVE.match(word) and not _RU_COPULA.match(word)):
            continue
        # With V581-D45 closed the collision test is finally sound: a token that
        # head generation admitted as the finite verb of THIS span is not also
        # its subject.  The same rule was tried before D45 and reverted, because
        # head generation then mis-read the noun "Комитет" as a verb and the
        # collision inherited that error.  Repair order mattered, not the rule.
        if any(head.role_type == "PREDICATE_HEAD" and head.span == (start, stop)
               for head in out):
            continue
        oblique = bool(_RU_OBLIQUE.search(word))
        nominative = bool(_RU_NOMINATIVE_HINT.search(word))
        # A hyphenated appositive compound ("матери-подростки") is one nominal,
        # and its ending is genuinely ambiguous: -и is nominative plural for many
        # nouns and oblique singular for others, so no suffix rule decides it.
        # Agreement does.  When such a compound stands immediately before a
        # finite verb, and nothing else has claimed the subject role there, the
        # ambiguity resolves towards nominative -- but at reduced confidence,
        # because agreement is evidence and not proof.
        agreeing_compound = (
            "-" in word[1:-1]
            and all(part.isalpha() for part in word.split("-") if part)
            and index + 1 < len(words)
            and bool(_RU_FINITE.match(words[index + 1][0])
                     or (_RU_REFLEXIVE.match(words[index + 1][0])
                         and not _RU_COPULA.match(words[index + 1][0]))))
        if oblique and not nominative and not agreeing_compound:
            continue
        confidence = 0.72 if nominative and not oblique else 0.45
        if agreeing_compound and not nominative:
            confidence = 0.55
        if word[:1].isupper() and index == 0:
            confidence += 0.08
        out.append(_candidate(
            "SUBJECT", (start, stop), word, "RU_NOMINAL", confidence=confidence,
            subtype="EXPLICIT_SUBJECT",
            morphological=(("NOMINATIVE_HINT",) if nominative else ())
                          + (("AGREEMENT_RESOLVED_COMPOUND",)
                             if agreeing_compound and not nominative else ())
                          + (("OBLIQUE_HINT",) if oblique else ())))
    for word, start, stop in words:
        if _RU_PRONOUN.match(word):
            out.append(_candidate("SUBJECT", (start, stop), word, "RU_ANAPHOR",
                                  confidence=0.5,
                                  subtype="ANAPHORIC_SUBJECT_RECOVERABLE",
                                  discourse=("PRONOMINAL",)))
    return out


#: Arabic proclitics that attach to the following word without being part of it.
#: و (and) and ف (so) are sentence connectives; ل (to/for) and ب (by/with) are
#: prepositions.  Including them in a predicate-head span means the span is not
#: the predicate: the reference writes ينبغي where the surface reads وينبغي, and
#: four of the six remaining Arabic head blockers were exactly this -- the
#: candidate existed and its boundary was wrong.
_AR_PROCLITIC = re.compile(r"^[وف]")


def _strip_arabic_proclitic(word: str, start: int, end: int
                            ) -> tuple[str, int, int]:
    """Drop a leading connective so the span is the predicate itself.

    Only applied when the remainder is still a plausible word, so that a word
    genuinely beginning with waw is not truncated.
    """
    if len(word) >= 4 and _AR_PROCLITIC.match(word):
        return word[1:], start + 1, end
    return word, start, end


def _arabic_coordinate_list(text: str, words) -> bool:
    """Is this a list of coordinate noun phrases rather than a predication?

    V581-D41.  The zero-copula fallback promotes the first definite nominal to
    topic and reads everything after it as the predicate.  On a bare inventory --
    "الأدوية، اللقاحات، الفحوصات، المعدات الطبية" -- that manufactures a
    proposition out of a list, which asserts nothing.

    The distinction is structural, not lexical: a list is several segments of
    parallel bare nominals with no predicative element between them.  Deciding it
    from a vocabulary of known list items would close this witness and nothing
    else, and would still fail on the next inventory.

    A genuine zero-copula sentence ("الأدوية مفيدة") has no such segmentation, so
    it is unaffected.
    """
    segments, current = [], []
    for word, start, stop in words:
        current.append(word)
        raw = text[start:stop].rstrip()
        if raw.endswith(("،", ",", "؛", ";")) or _AR_CONJUNCTION.match(word):
            segments.append(current)
            current = []
    if current:
        segments.append(current)
    if len(segments) < 3:
        return False
    # Every segment must be a short bare nominal: no verb, no deontic operator,
    # no existential.  One predicative segment is enough to make this a sentence.
    for segment in segments:
        if not segment or len(segment) > 3:
            return False
        for word in segment:
            if (_AR_VERB_HEAD.match(word) or _AR_DEONTIC.match(word)
                    or _AR_PASSIVE.match(word) or _AR_EXISTENTIAL.match(word)):
                return False
    # Parallel morphology: coordinate inventories agree in definiteness.
    definite = [bool(_AR_DEFINITE.match(segment[0])) for segment in segments]
    return all(definite) or not any(definite)


def _arabic_fronted_predicate(text: str, words, end: int) -> list[Candidate]:
    """A fronted لـ-phrase predicating over a delayed nominal subject.

    "لمجلس الأمن أن يقرر ..." is "it is for the Security Council to decide ...":
    the لـ-phrase is the predicate and the أن-clause is the delayed subject.

    The whole risk is that this becomes "an initial لـ makes a predicate", which
    would swallow every fronted beneficiary, possessor and adjunct in the corpus.
    So three conditions must hold together, and none of them is the preposition:

      * the phrase opens the span -- a لـ-phrase later in the clause is an
        argument of something already predicating;
      * a delayed nominal subject actually follows, marked by أن;
      * no finite verb intervenes, because that would make this a verbal
        sentence in which the لـ-phrase is an adjunct.
    """
    if not words:
        return []
    first, start, stop = words[0]
    if not _AR_LAM_NOMINAL.match(first) or _AR_VERB_HEAD.match(first):
        return []
    subordinator = None
    for index in range(1, min(len(words), AR_FRONTED_PREDICATE_MAX_GAP + 1)):
        word = words[index][0]
        if _AR_VERB_HEAD.match(word):
            return []          # verbal sentence: the لـ-phrase is an adjunct
        if _AR_SUBORDINATOR.match(word):
            subordinator = index
            break
    if subordinator is None:
        return []
    predicate_stop = words[subordinator - 1][2]
    subject_start = words[subordinator][1]
    if subject_start >= end:
        return []
    # The delayed subject is the أن-clause, not the remainder of the span.  A
    # span-plausibility audit caught the first version of this rule running to
    # the end of the span -- 329 characters where the reference names 85 -- which
    # would have passed the unit on a candidate wide enough to contain almost
    # anything.  The clause is bounded by punctuation, so bound it there.
    subject_stop = end
    for index in range(subordinator, len(words)):
        if text[words[index][1]:words[index][2]].rstrip().endswith(("،", ",", ";")):
            subject_stop = words[index][2]
            break
    subject_stop = min(subject_stop, end)
    while subject_stop > subject_start and text[subject_stop - 1] in "،,؛; ":
        subject_stop -= 1
    if subject_stop <= subject_start:
        return []
    return [
        _candidate("PREDICATE_HEAD", (start, predicate_stop),
                   text[start:predicate_stop].strip(), "AR_FRONTED_PREDICATE",
                   confidence=0.78, subtype="NOMINAL_PREDICATE",
                   syntactic=("FRONTED_PREDICATE", "ZERO_COPULA")),
        _candidate("SUBJECT", (subject_start, subject_stop),
                   text[subject_start:subject_stop].strip(), "AR_DELAYED_SUBJECT",
                   confidence=0.74, subtype="ZERO_COPULA_NOMINAL_SUBJECT",
                   syntactic=("DELAYED_SUBJECT",)),
    ]


def _arabic_nominal_constituent(text: str, words, start_index: int):
    """Return one bounded postverbal Arabic nominal constituent.

    Arabic subjects need not carry the definite article.  The constituent may
    include a numeral, construct/genitive chain, and a restrictive relative
    clause.  Punctuation, a parenthetical example, or a closed-class adverbial
    marks its right boundary.
    """
    if start_index >= len(words):
        return None
    first = words[start_index]
    if (_AR_PREP.match(first[0]) or _AR_SUBORDINATOR.match(first[0])
            or _AR_NEG.match(first[0]) or _AR_CONJUNCTION.match(first[0])):
        return None
    stop = first[2]
    for offset in range(
            start_index,
            min(len(words), start_index + MAX_AR_SUBJECT_CONSTITUENT_TOKENS)):
        word, start, end = words[offset]
        raw = text[start:end]
        if offset > start_index and (
                raw.lstrip().startswith(("(", "["))
                or _AR_POSTNOMINAL_ADVERB.match(word)):
            break
        stop = end
        if raw.rstrip().endswith(("،", ",", "؛", ";", ".", ":")):
            break
    while stop > first[1] and (
            text[stop - 1].isspace()
            or unicodedata.category(text[stop - 1]).startswith("P")):
        stop -= 1
    if stop <= first[1]:
        return None
    return first[1], stop


def _arabic_governed_list_action(text: str, words) -> list[Candidate]:
    """Realize an Arabic action nominal licensed by typed list governance."""
    if not words:
        return []
    raw_word, raw_start, raw_stop = words[0]
    if _AR_ISTIFAL_ACTION_NOMINAL.match(raw_word):
        head, start, stop = raw_word, raw_start, raw_stop
        if raw_word.startswith("و") \
                and _AR_ISTIFAL_ACTION_NOMINAL.match(raw_word[1:]):
            head, start = raw_word[1:], raw_start + 1
        morphology = ("ISTIFAL_ACTION_NOMINAL",)
    else:
        # V581L L2 (variant 2; variant 1 falsified by the operational test
        # population -- see 398 L2_RETENTION).  A coordinated bare masdar
        # heading a governed list item: the attached connective marks the
        # item as a continuation of the enumerated action list, and the
        # remainder must carry no other predicative reading -- verbal
        # patterns, the definite article, particles and the istif'al class
        # (handled above) are all excluded.  Two shape guards keep perfective
        # clauses out: an alif-initial remainder is ambiguous with the
        # derived-form perfectives (استعرض vs استعراض) and is licensed only
        # through explicit shape patterns; and the following word must not be
        # definite -- a definite NP right after the head reads as a finite
        # clause's subject (واستعرض المجلسُ...), while the masdar's
        # complement in this construction is indefinite (وشرب كمياتٍ...).
        if len(words) < 2 or len(raw_word) < 4 \
                or not raw_word.startswith(("و", "ف")):
            return []
        stripped = raw_word[1:]
        if stripped.startswith(("ا", "أ", "إ", "آ")):
            return []
        for pattern in (_AR_DEFINITE, _AR_NEG, _AR_PREP, _AR_VERB_HEAD,
                        _AR_PASSIVE, _AR_DEONTIC, _AR_EXISTENTIAL,
                        _AR_ISTIFAL_ACTION_NOMINAL):
            if pattern.match(raw_word) or pattern.match(stripped):
                return []
        following = words[1][0]
        if _AR_DEFINITE.match(following) or _AR_DEFINITE.match(following[1:]):
            return []
        head, start, stop = stripped, raw_start + 1, raw_stop
        morphology = ("COORDINATED_ACTION_NOMINAL",)
    return [
        _candidate(
            "PREDICATE_HEAD", (start, stop), head,
            "AR_GOVERNED_LIST_ACTION_NOMINAL", confidence=0.78,
            subtype="GOVERNING_CLAUSE_PREDICATE",
            morphological=morphology,
            structural=("TYPED_LIST_GOVERNANCE",)),
        _candidate(
            "GOVERNING_CLAUSE", None, "", "TYPED_LIST_GOVERNOR",
            confidence=0.85,
            discourse=("SUBJECT_AND_FRAME_INHERITED_FROM_LIST_LEAD_IN",),
            structural=("TYPED_LIST_GOVERNANCE",)),
    ]


def _arabic_candidates(text: str, tokens, *,
                       governed_list_item: bool = False) -> list[Candidate]:
    # P6.2 R6.  See `_latin_candidates`.
    if not tokens:
        return []
    words = [(_clean(w), s, e) for w, s, e in tokens]
    out: list[Candidate] = []
    end = tokens[-1][2]
    if governed_list_item:
        out += _arabic_governed_list_action(text, words)
    out += _arabic_fronted_predicate(text, words, end)
    # Arabic permits a fronted adverbial before the main clause, set off by a
    # comma: "خلال الفترات التي ..., يمكن ...".  Counting eight words from the
    # absolute start of the span therefore misses the main-clause verb whenever
    # that preamble is long.  Anchor the scan at each clause opening -- the span
    # start, and each position following a comma -- rather than enlarging the
    # constant, which would admit heads from arbitrarily deep subordinate
    # material without evidence that they open a clause.
    anchors = [0] + [i + 1 for i, (_w, start, stop) in enumerate(words)
                     if text[start:stop].rstrip().endswith(("،", ","))]
    scan = sorted({i for anchor in anchors
                   for i in range(anchor, min(anchor + 8, len(words)))})
    for index in scan:
        raw_word, raw_start, raw_stop = words[index]
        if _AR_NEG.match(raw_word) or _AR_PREP.match(raw_word):
            continue
        # The definite article is never a verbal prefix.  V1 read it as one.
        if _AR_DEFINITE.match(raw_word):
            continue
        # Test the full surface form, then record the span without the
        # connective: the proclitic decides nothing about predicatehood.
        word, start, stop = raw_word, raw_start, raw_stop
        if _AR_DEONTIC.match(raw_word) or _AR_PASSIVE.match(raw_word) \
                or _AR_VERB_HEAD.match(raw_word) or _AR_EXISTENTIAL.match(raw_word):
            word, start, stop = _strip_arabic_proclitic(raw_word, raw_start,
                                                        raw_stop)
        kind = None
        confidence = 0.0
        if _AR_EXISTENTIAL.match(raw_word):
            # هناك introduces an existential proposition; its subject is the
            # delayed nominal, and it is not a lexical verb.
            kind, confidence = "NOMINAL_PREDICATE", 0.8
        elif _AR_DEONTIC.match(raw_word):
            kind, confidence = "DEONTIC_OPERATOR_WITH_COMPLEMENT", 0.88
        elif _AR_PASSIVE.match(raw_word):
            kind, confidence = "PASSIVE_OR_IMPERSONAL_PREDICATE", 0.82
        elif _AR_VERB_HEAD.match(raw_word):
            kind, confidence = "EXPLICIT_FINITE_PREDICATE", 0.8
        if kind is None:
            continue
        out.append(_candidate("PREDICATE_HEAD", (start, stop), word,
                              f"AR_{kind}", confidence=confidence, subtype=kind,
                              morphological=("VERBAL_MORPHOLOGY",)))
        if stop < end:
            out.append(_candidate("PREDICATE_COMPLEMENT", (stop, end),
                                  text[stop:end].strip(), "AR_COMPLEMENT",
                                  confidence=0.7))
        # Arabic verbal sentences are verb-initial: the subject follows, after
        # any preposition phrase the verb governs.
        cursor = index + 1
        # A passive verb may take its patient through a preposition: يُكشف عن X
        # is "X is detected", so the nominal governed by عن is the patient
        # subject and stepping over the whole phrase loses it.  After an *active*
        # verb the same nominal is a prepositional complement and must not become
        # a subject -- so this depends on the predicate state already decided
        # above, never on the preposition alone.
        if (kind == "PASSIVE_OR_IMPERSONAL_PREDICATE" and cursor < len(words)
                and _AR_PREP.match(words[cursor][0])):
            cursor += 1
        else:
            while cursor < len(words) and _AR_PREP.match(words[cursor][0]):
                cursor += 2
        if cursor < len(words) and _AR_DEFINITE.match(words[cursor][0]):
            subject = words[cursor]
            out.append(_candidate(
                "SUBJECT", (subject[1], subject[2]), subject[0],
                "AR_POSTVERBAL", confidence=0.8,
                subtype=("PASSIVE_PATIENT_SUBJECT"
                         if kind == "PASSIVE_OR_IMPERSONAL_PREDICATE"
                         else "POSTVERBAL_SUBJECT"),
                syntactic=("FOLLOWS_VERB",)))
        structural_subject = (
            kind in ("NOMINAL_PREDICATE", "PASSIVE_OR_IMPERSONAL_PREDICATE")
            or (kind == "EXPLICIT_FINITE_PREDICATE"
                and index > 0
                and _AR_SUBORDINATOR.match(words[index - 1][0])
                and cursor == index + 1)
        )
        if structural_subject:
            constituent = _arabic_nominal_constituent(text, words, cursor)
            if constituent is not None:
                subject_start, subject_stop = constituent
                out.append(_candidate(
                    "SUBJECT", constituent,
                    text[subject_start:subject_stop].strip(),
                    ("AR_EXISTENTIAL_DELAYED_NP"
                     if kind == "NOMINAL_PREDICATE"
                     else "AR_POSTVERBAL_PATIENT_NP"
                     if kind == "PASSIVE_OR_IMPERSONAL_PREDICATE"
                     else "AR_SUBORDINATE_POSTVERBAL_NP"),
                    confidence=0.8,
                    subtype=(
                        "ZERO_COPULA_NOMINAL_SUBJECT"
                        if kind == "NOMINAL_PREDICATE"
                        else "PASSIVE_PATIENT_SUBJECT"
                        if kind == "PASSIVE_OR_IMPERSONAL_PREDICATE"
                        else "POSTVERBAL_SUBJECT"
                    ),
                    syntactic=("BOUNDED_CONSTITUENT", "FOLLOWS_VERB")))
    if not any(c.role_type == "PREDICATE_HEAD" for c in out):
        definites = [(w, s, e) for w, s, e in words if _AR_DEFINITE.match(w)]
        if definites and len(words) >= 4 and not _arabic_coordinate_list(text, words):
            topic = definites[0]
            out.append(_candidate("SUBJECT", (topic[1], topic[2]), topic[0],
                                  "AR_NOMINAL_TOPIC", confidence=0.7,
                                  subtype="ZERO_COPULA_NOMINAL_SUBJECT",
                                  syntactic=("DEFINITE_TOPIC",)))
            if topic[2] < end:
                out.append(_candidate("PREDICATE_HEAD", (topic[2], end),
                                      text[topic[2]:end].strip(),
                                      "AR_NOMINAL_PREDICATE", confidence=0.68,
                                      subtype="NOMINAL_PREDICATE",
                                      syntactic=("ZERO_COPULA",)))
    return out


QUOTE_OPENERS = "\u201e\u201c\u201d\u00ab\u2018\u2019\"'"


def _latin_definition_entry(text: str, words, end: int) -> list[Candidate]:
    """A definition-list entry: quoted definiendum, zero copula, nominal definiens.

    Legal instruments define terms as list entries rather than sentences:
    '"Pseudonymisierung" die Verarbeitung personenbezogener Daten in einer
    Weise, ...'.  There is no verb, so every verbal rule misses it, and the
    zero-copula reading has to come from the quotation plus a nominal
    continuation.

    The guard against over-firing is that a quoted phrase followed by a FINITE
    verb is an ordinary sentence with a quoted subject, not a definition, and
    must keep its verbal analysis.
    """
    if len(words) < 3:
        return []
    first, start, stop = words[0]
    # _clean strips punctuation, so the quotation has to be read from the
    # raw text at the token offsets rather than from the cleaned word.
    raw = text[start:stop].strip()
    if not raw[:1] in QUOTE_OPENERS:
        return []
    rest = words[1:]
    if any(_LAT_FINITE.match(word) or _LAT_DEONTIC.match(word)
           for word, _s, _e in rest[:3]):
        return []
    if not _LAT_ARTICLE.match(rest[0][0]):
        return []
    definiens_end = _leading_nominal_constituent(rest)
    head_stop = rest[definiens_end - 1][2]
    if head_stop <= rest[0][1]:
        return []
    return [
        _candidate("SUBJECT", (start, stop), text[start:stop].strip(),
                   "LAT_DEFINIENDUM", confidence=0.76,
                   subtype="ZERO_COPULA_NOMINAL_SUBJECT",
                   syntactic=("QUOTED_TERM", "DEFINITION_ENTRY")),
        _candidate("PREDICATE_HEAD", (rest[0][1], head_stop),
                   text[rest[0][1]:head_stop].strip(), "LAT_DEFINIENS",
                   confidence=0.74, subtype="NOMINAL_PREDICATE",
                   syntactic=("ZERO_COPULA", "DEFINITION_ENTRY")),
    ]


def _latin_quoted_definition_clause(text: str, words) -> list[Candidate]:
    """A nominal metalinguistic subject followed by a finite definition head."""
    if len(words) < 5:
        return []
    base = _directive_head_index(words)
    if base >= len(words) - 2:
        return []
    opening_seen = False
    closing_index = None
    for index in range(base, len(words) - 1):
        raw = text[words[index][1]:words[index][2]]
        if _QUOTE_OPEN.search(raw):
            opening_seen = True
        if opening_seen and _QUOTE_CLOSE.search(raw):
            closing_index = index
            break
    if closing_index is None:
        return []
    head_word, head_start, head_stop = words[closing_index + 1]
    if (not _LAT_DEFINITION_FINITE.match(head_word)
            or _LAT_ARTICLE.match(head_word)
            or _LAT_PREPOSITION.match(head_word)
            or _LAT_PRONOUN.match(head_word)
            or _COORD.match(head_word)):
        return []
    subject_start = words[base][1]
    subject_stop = words[closing_index][2]
    return [
        _candidate(
            "SUBJECT", (subject_start, subject_stop),
            text[subject_start:subject_stop].strip(),
            "LAT_QUOTED_METALINGUISTIC_SUBJECT", confidence=0.8,
            subtype="EXPLICIT_SUBJECT",
            syntactic=("BOUNDED_CONSTITUENT", "BALANCED_QUOTATION")),
        _candidate(
            "PREDICATE_HEAD", (head_start, head_stop), head_word,
            "LAT_QUOTED_DEFINITION_FINITE", confidence=0.8,
            subtype="EXPLICIT_FINITE_PREDICATE",
            syntactic=("FOLLOWS_BALANCED_METALINGUISTIC_SUBJECT",),
            morphological=("CONSTRUCTION_LOCAL_FINITE_MORPHOLOGY",)),
    ]


def _latin_initial_operative(text: str, words) -> list[Candidate]:
    """A clause-initial all-caps operative with an infinitival complement."""
    if len(words) < 5:
        return []
    word, start, stop = words[0]
    raw = text[start:stop].strip(".,;:()[]")
    if not raw.isalpha() or len(raw) < 4 or not raw.isupper():
        return []
    if not _LAT_ARTICLE.match(words[1][0]):
        return []
    complement = False
    for index in range(2, min(len(words) - 1, 10)):
        if words[index][0].casefold() not in {"à", "a", "to", "zu"}:
            continue
        if _LAT_INFINITIVE.match(words[index + 1][0]):
            complement = True
            break
    if not complement:
        return []
    return [
        _candidate(
            "PREDICATE_HEAD", (start, stop), word,
            "LAT_INITIAL_ALL_CAPS_OPERATIVE", confidence=0.9,
            subtype="EXPLICIT_FINITE_PREDICATE",
            syntactic=("CLAUSE_INITIAL", "INFINITIVE_COMPLEMENT_FRAME"),
            morphological=("ALL_CAPS_OPERATIVE",)),
    ]


def _latin_governed_to_infinitive(text: str, words,
                                  left_context: str) -> list[Candidate]:
    """An enumerated English to-infinitive licensed by an enacting lead-in."""
    if len(words) < 3 or _directive_head_index(words) != 1:
        return []
    if words[1][0].casefold() != "to":
        return []
    verb, _verb_start, verb_stop = words[2]
    if (not _LAT_BARE_ENGLISH_VERB.match(verb)
            or _LAT_ARTICLE.match(verb)
            or _LAT_PREPOSITION.match(verb)
            or _LAT_PRONOUN.match(verb)
            or _COORD.match(verb)):
        return []
    if not enacting_context_present(left_context[-ANTECEDENT_CONTEXT_CHARS:]):
        return []
    head_start = words[1][1]
    return [
        _candidate(
            "PREDICATE_HEAD", (head_start, verb_stop),
            text[head_start:verb_stop].strip(),
            "LAT_GOVERNED_TO_INFINITIVE", confidence=0.78,
            subtype="GOVERNING_CLAUSE_PREDICATE",
            syntactic=("ENUMERATED_DIRECTIVE_ITEM",),
            structural=("ENACTING_LEFT_CONTEXT",)),
        _candidate(
            "GOVERNING_CLAUSE", None, "", "ENACTING_LEFT_CONTEXT_GOVERNOR",
            confidence=0.82,
            discourse=("SUBJECT_AND_FRAME_INHERITED_FROM_ENACTING_LEAD_IN",)),
    ]


#: V581L L3.  A modal-obligation frame in the governing left context ("shall
#: take the necessary measures, ...") licenses a purpose to-infinitive at a
#: clause boundary of the continuation fragment.
_LAT_MODAL_OBLIGATION_FRAME = re.compile(
    r"\b(?:shall|must)\s+[a-z]+", re.IGNORECASE)
_LAT_CONTINUATION_TO_INFINITIVE = re.compile(r",\s+(to)\s+([A-Za-z]+)")


def _latin_continuation_to_infinitive(text: str,
                                      left_context: str) -> list[Candidate]:
    """A comma-bounded to-infinitive continuing a modal-obligation frame.

    The enumerated variant above requires the directive shape "(a) to X".
    A continuation fragment — mid-sentence, lowercase-initial, governed by a
    "shall/must + verb" obligation in the left context — carries the same
    governed to-infinitive predicate at its internal clause boundary:
    "…, in conformity with its internal law, to ensure that …".  In-span
    negatives ("to accede", "to those") follow no comma and never fire.
    """
    tail = left_context[-ANTECEDENT_CONTEXT_CHARS:]
    if not tail or not _LAT_MODAL_OBLIGATION_FRAME.search(tail):
        return []
    stripped = text.lstrip()
    if not stripped or not stripped[0].islower():
        return []
    out: list[Candidate] = []
    for match in _LAT_CONTINUATION_TO_INFINITIVE.finditer(text):
        verb = match.group(2)
        if (not _LAT_BARE_ENGLISH_VERB.match(verb)
                or _LAT_ARTICLE.match(verb)
                or _LAT_PREPOSITION.match(verb)
                or _LAT_PRONOUN.match(verb)
                or _COORD.match(verb)):
            continue
        start, stop = match.start(1), match.end(2)
        out.append(_candidate(
            "PREDICATE_HEAD", (start, stop), text[start:stop],
            "LAT_GOVERNED_TO_INFINITIVE", confidence=0.78,
            subtype="GOVERNING_CLAUSE_PREDICATE",
            syntactic=("CLAUSE_BOUNDARY_INFINITIVAL",),
            structural=("MODAL_OBLIGATION_CONTINUATION",)))
        out.append(_candidate(
            "GOVERNING_CLAUSE", None, "", "ENACTING_LEFT_CONTEXT_GOVERNOR",
            confidence=0.82,
            discourse=("SUBJECT_AND_FRAME_INHERITED_FROM_ENACTING_LEAD_IN",)))
    return out


def _latin_candidates(text: str, tokens, *, left_context: str = "") -> list[Candidate]:
    # P6.2 R6.  A span with no word tokens has no last token, and
    # `end = tokens[-1][2]` raised IndexError for "", " ", a tab, a newline, a
    # non-breaking space and every mixed whitespace string, at every declared
    # language, in all six generations -- reachable from production because
    # `invariants.py` forwards `ctx.selected_text or ctx.seed_text or
    # ctx.candidate.raw_span` verbatim and a whitespace-only string is truthy.
    #
    # The substrate already DECLARED what to do with such a span:
    # `non_proposition_evidence` returns exactly ["EMPTY_SPAN"], `bind` lists
    # EMPTY_SPAN among its structural negatives, and that sets `hard_negative`
    # and routes to INVALID_BINDING_CANDIDATE.  The exception was raised
    # BEFORE that branch could run, so the site's own declared refusal for
    # empty spans was dead code for exactly the input class it names.  This
    # guard does not add a policy; it lets the declared one execute.
    if not tokens:
        return []
    words = [(_clean(w), s, e) for w, s, e in tokens]
    out: list[Candidate] = []
    end = tokens[-1][2]
    out += _latin_definition_entry(text, words, end)
    out += _latin_quoted_definition_clause(text, words)
    out += _latin_initial_operative(text, words)
    out += _latin_governed_to_infinitive(text, words, left_context)
    out += _latin_continuation_to_infinitive(text, left_context)
    for index, (word, start, stop) in enumerate(words):
        kind = None
        confidence = 0.0
        # A modal governing a passive infinitive is ONE predicate: "peuvent être
        # établis".  Emitting only "peuvent" or only "établis" drops the voice
        # marker, which the semantic-sufficiency check correctly refuses -- the
        # shorter span asserts something else.  This runs BEFORE the branch
        # dispatch because the modal is otherwise claimed by the deontic branch
        # and the chain is never reached.
        if (index + 2 < len(words)
                and (_LAT_DEONTIC.match(word) or _LAT_FINITE.match(word))
                and _LAT_INF_AUX.match(words[index + 1][0])
                and _LAT_CHAIN_PARTICIPLE.match(words[index + 2][0])):
            chain_stop = words[index + 2][2]
            out.append(_candidate(
                "PREDICATE_HEAD", (start, chain_stop),
                text[start:chain_stop].strip(), "LAT_MODAL_PASSIVE_CHAIN",
                confidence=0.84, subtype="PASSIVE_OR_IMPERSONAL_PREDICATE",
                syntactic=("MODAL_PASSIVE_CHAIN",),
                morphological=("PERIPHRASTIC_VOICE",)))
        if _LAT_DEONTIC.match(word):
            following = words[index + 1][0] if index + 1 < len(words) else ""
            after = words[index + 2][0] if index + 2 < len(words) else ""
            if _LAT_PASSIVE_INFINITIVE.match(following) \
                    and _LAT_PARTICIPLE.match(after):
                kind, confidence = "PASSIVE_OR_IMPERSONAL_PREDICATE", 0.86
            else:
                kind, confidence = "DEONTIC_OPERATOR_WITH_COMPLEMENT", 0.88
        elif _LAT_FINITE.match(word):
            following = words[index + 1][0] if index + 1 < len(words) else ""
            passive = bool(_LAT_PASSIVE_AUX.match(word)
                           and _LAT_PARTICIPLE.match(following))
            kind = ("PASSIVE_OR_IMPERSONAL_PREDICATE" if passive
                    else "EXPLICIT_FINITE_PREDICATE")
            confidence = 0.82
        elif (index <= _directive_head_index(words)
              and _LAT_INFINITIVE.match(word)
              and not _LAT_NOT_A_VERB.match(word)
              and not _LAT_ARTICLE.match(word)
              and not _LAT_PRONOUN.match(word)
              and len(words) >= 3):
            kind, confidence = "PREDICATE_RECOVERABLE", 0.6
        elif (index >= 1 and _LAT_SUBJECT_CLITIC.match(words[index - 1][0])
              and len(word) >= 4
              and not _LAT_NOT_A_VERB.match(word)
              and not _LAT_ARTICLE.match(word)
              and not _LAT_PRONOUN.match(word)
              and not _LAT_PREPOSITION.match(word)):
            # "Il convie tout Membre ...".  The evidence is the clitic subject
            # immediately before the word, not the -e it happens to end in: a
            # bare -e rule would make a finite verb of every French noun and
            # adjective in the corpus.
            kind, confidence = "EXPLICIT_FINITE_PREDICATE", 0.7
        elif (index >= 1 and _LAT_LEXICAL_FINITE.match(word)
              and not _LAT_NOT_A_VERB.match(word)
              and not _LAT_ARTICLE.match(word)
              and not _LAT_PRONOUN.match(word)):
            kind, confidence = "EXPLICIT_FINITE_PREDICATE", 0.62
        if kind is None:
            continue
        base_confidence = confidence
        confidence = max(0.05, confidence - 0.10 * index)
        head_start, head_text, clitic = start, word, ()
        if index >= 1 and _LAT_REFLEXIVE_CLITIC.match(words[index - 1][0]):
            # The complete predicate is "se couvrir", not "couvrir": the clitic
            # carries voice and can change lexical meaning, so dropping it from
            # the span loses part of the predicate.
            head_start = words[index - 1][1]
            head_text = text[head_start:stop].strip()
            clitic = ("REFLEXIVE_CLITIC",)
        out.append(_candidate("PREDICATE_HEAD", (head_start, stop), head_text,
                              f"LAT_{kind}", confidence=confidence, subtype=kind,
                              syntactic=(f"HEAD_INDEX_{index}",) + clitic,
                              base_confidence=base_confidence, token_index=index))
        if stop < end:
            out.append(_candidate("PREDICATE_COMPLEMENT", (stop, end),
                                  text[stop:end].strip(), "LAT_COMPLEMENT",
                                  confidence=0.7))
        # V581-D43.  German is a V2 language: when a fronted subordinate clause
        # or a resumptive "so"/"dann" occupies the first position, the subject
        # follows the finite verb -- "..., so ist die Strafe Freiheitsstrafe".
        # The preverbal rule cannot reach it at any window size, because the
        # subject is not before the verb at all.  Emitting it is a construction
        # repair, not a distance repair.
        if index > 0 and index + 1 < len(words):
            preceding = words[index - 1]
            inverted = (_LAT_RESUMPTIVE.match(preceding[0])
                        or text[preceding[1]:preceding[2]].rstrip().endswith(","))
            if inverted:
                tail = words[index + 1:]
                tail_end = _leading_nominal_constituent(tail)
                post_start, post_stop = tail[0][1], tail[tail_end - 1][2]
                post_text = text[post_start:post_stop].strip()
                if post_text and not _LAT_PREPOSITION.match(tail[0][0]):
                    out.append(_candidate(
                        "SUBJECT", (post_start, post_stop), post_text,
                        "LAT_POSTVERBAL_V2", confidence=0.76,
                        subtype="EXPLICIT_SUBJECT",
                        syntactic=("V2_INVERSION", "FOLLOWS_FINITE_VERB")))

        # Subject: the nominal run before the head, minus a leading article.
        # A reflexive clitic is excluded: it belongs to the predicate, and the
        # widened head span already covers it, so emitting it here would make the
        # same token both the verb and its own subject.
        if index > 0 and not (index == 1
                              and _LAT_REFLEXIVE_CLITIC.match(words[0][0])):
            first = words[0]
            begin = first[1]
            if _LAT_ARTICLE.match(first[0]) and len(words) > 1:
                begin = first[1]
            run = words[0:index]
            subject_stop = words[index - 1][2]
            subject_text = text[begin:subject_stop].strip()
            if subject_text:
                anaphoric = bool(_LAT_PRONOUN.match(first[0]))
                expletive = bool(_EXPLETIVE.match(first[0]))
                # V581-D43.  "Everything before the finite verb" is not a
                # subject span in a verb-final or middle-field-heavy clause: for
                # "sie hierzu aus rechtlichen Gruenden nicht in der Lage ist" it
                # returns the whole clause, which then contains the reference
                # subject and passes an overlap test while being useless.  The
                # bounded constituent is emitted ALONGSIDE the full run rather
                # than replacing it -- replacing it truncated subjects that were
                # correctly long and cost four units.  Both readings are
                # representable; selection decides between them.
                constituent_stop = run[_leading_nominal_constituent(run) - 1][2]
                if constituent_stop < subject_stop:
                    bounded = text[begin:constituent_stop].strip()
                    if bounded:
                        out.append(_candidate(
                            "SUBJECT", (begin, constituent_stop), bounded,
                            "LAT_PREVERBAL_CONSTITUENT", confidence=0.74,
                            subtype=("ANAPHORIC_SUBJECT_RECOVERABLE"
                                     if _LAT_PRONOUN.match(run[0][0])
                                     else "EXPLICIT_SUBJECT"),
                            syntactic=("BOUNDED_CONSTITUENT",)))
                out.append(_candidate(
                    "SUBJECT", (begin, subject_stop), subject_text,
                    "LAT_PREVERBAL", confidence=0.78,
                    subtype=("EXPLETIVE_OR_IMPERSONAL_CONSTRUCTION" if expletive
                             else "ANAPHORIC_SUBJECT_RECOVERABLE" if anaphoric
                             else "PASSIVE_PATIENT_SUBJECT"
                             if kind == "PASSIVE_OR_IMPERSONAL_PREDICATE"
                             else "EXPLICIT_SUBJECT"),
                    syntactic=("PRECEDES_FINITE_VERB",)))
    return out


def _shared_candidates(text: str, tokens, left_context: str,
                       heading: str) -> list[Candidate]:
    out: list[Candidate] = []
    match = _ATTRIBUTION_MARKER.search(text)
    if match:
        tail = re.match(r"\s*(\S.{2,80}?)(?:[,.;]|$)", text[match.end():])
        if tail:
            out.append(_candidate(
                "ATTRIBUTION",
                (match.end() + tail.start(1), match.end() + tail.end(1)),
                tail.group(1), "ATTRIBUTION_MARKER", confidence=0.8,
                discourse=("EXPLICIT_ATTRIBUTION",)))
    for source_name, body in (("HEADING", heading), ("SPAN", text)):
        if not body:
            continue
        institution = _INSTITUTION_HEAD.search(body)
        if institution:
            out.append(_candidate(
                "INSTITUTIONAL_ISSUER",
                (institution.start(), institution.end()) if source_name == "SPAN"
                else None,
                institution.group(0), f"INSTITUTION_IN_{source_name}",
                confidence=0.7 if source_name == "SPAN" else 0.6,
                structural=(f"FROM_{source_name}",)))
    if _QUOTE_OPEN.search(text) and not _QUOTE_CLOSE.search(text):
        out.append(_candidate("QUOTED_SPEAKER", None, "", "UNCLOSED_QUOTATION",
                              confidence=0.4,
                              discourse=("QUOTATION_NOT_CLOSED",)))
    return out


_CONSTRUCTIONAL_PREDICATION_RULES = frozenset({
    "LAT_QUOTED_DEFINITION_FINITE",
    "LAT_INITIAL_ALL_CAPS_OPERATIVE",
    "LAT_GOVERNED_TO_INFINITIVE",
    "AR_GOVERNED_LIST_ACTION_NOMINAL",
})


def _transport_constructional_predication(signals: list[str],
                                          candidates: list[Candidate]
                                          ) -> list[str]:
    """Remove the text-only null signal after typed predication is realized."""
    if not any(
            candidate.role_type == "PREDICATE_HEAD"
            and candidate.origin_rule in _CONSTRUCTIONAL_PREDICATION_RULES
            for candidate in candidates):
        return signals
    return [
        signal for signal in signals
        if signal != "NO_VERBAL_OR_PREDICATIVE_EVIDENCE"
    ]


# ===========================================================================
# §16 — complete analyses
# ===========================================================================

@dataclass
class Analysis:
    """One complete alternative reading of the span."""

    analysis_id: str
    subject: Candidate | None
    predicate_head: Candidate | None
    predicate_complement: Candidate | None
    governing_clause: Candidate | None
    antecedent: Candidate | None
    semantic_actor: Candidate | None
    institutional_issuer: Candidate | None
    attribution: Candidate | None
    quoted_speaker: Candidate | None
    non_proposition_signals: tuple[str, ...] = ()
    shared_subject_source: str = "NONE"
    shared_predicate_source: str = "NONE"
    required_context_ids: tuple[str, ...] = ()
    role_relations: tuple[RoleRelation, ...] = ()
    compatibility_score: float = 0.0
    constraint_violations: tuple[str, ...] = field(default_factory=tuple)
    #: V581-D42-M7.  Where the head sits relative to the span's finite matrix
    #: clause, recorded once at build time because that is where the clause
    #: context lives.  One of NO_FINITE_MATRIX_CLAUSE, IN_FINITE_MATRIX_CLAUSE,
    #: OUTSIDE_FINITE_MATRIX_CLAUSE, or NOT_ASSESSED for a lattice built without
    #: clause structure.
    head_clause_state: str = "NOT_ASSESSED"
    #: V581-D42-M9.  Where the head sits relative to a correlative apodosis, if
    #: the span opens one.  NO_APODOSIS, IN_APODOSIS, OUTSIDE_APODOSIS, or
    #: NOT_ASSESSED.
    head_correlative_state: str = "NOT_ASSESSED"
    #: V581-D42-M10.  Whether the head sits inside a comma-bounded dependent
    #: region.  NO_DEPENDENT_REGION, IN_DEPENDENT_REGION,
    #: OUTSIDE_DEPENDENT_REGION, or NOT_ASSESSED.
    head_region_state: str = "NOT_ASSESSED"

    def predicate_span(self) -> tuple[int, int] | None:
        head = self.predicate_head.span if self.predicate_head else None
        if head is None:
            return None
        complement = (self.predicate_complement.span
                      if self.predicate_complement else None)
        return (head[0], complement[1]) if complement else head


# ===========================================================================
# §17 — hard compatibility constraints
# ===========================================================================

def hard_constraints(analysis: Analysis, text: str, script: str) -> list[str]:
    """Readings the source forbids.  Each is inspectable and independently tested."""
    violations: list[str] = []
    subject = analysis.subject
    head = analysis.predicate_head

    if subject and subject.span and head and head.span:
        if subject.span == head.span:
            violations.append("SAME_SPAN_ASSIGNED_SUBJECT_AND_PREDICATE")
    for candidate in (subject, head, analysis.predicate_complement):
        if candidate and candidate.span:
            if candidate.span[0] < 0 or candidate.span[1] > len(text):
                violations.append("SOURCE_OFFSETS_INVALID")
    if head and head.subtype == "DEONTIC_OPERATOR_WITH_COMPLEMENT" \
            and not analysis.predicate_complement:
        # German and Dutch put a modal's infinitive complement at the END of a
        # subordinate clause, before the modal itself: "... entzogen werden
        # sollen".  A rule that looks only to the right of the modal reports
        # every such clause as detached.  Measured: this produced 3 of the 4
        # constraint rejections in the whole lattice, one of them of a
        # reference-compatible analysis -- a filter that acts four times and is
        # wrong three times is worse than one that does nothing.
        preceding = text[:head.span[0]] if head and head.span else ""
        if not _LAT_VERB_CLUSTER.search(preceding[-40:]):
            violations.append("MODAL_DETACHED_FROM_COMPLEMENT")
    if subject and subject.subtype == "PASSIVE_PATIENT_SUBJECT" \
            and analysis.semantic_actor \
            and analysis.semantic_actor.span == subject.span:
        violations.append("PASSIVE_PATIENT_PROMOTED_TO_ACTOR")
    if analysis.antecedent and analysis.antecedent.subtype == "AMBIGUOUS" \
            and subject and subject.span:
        violations.append("AMBIGUOUS_ANTECEDENT_TREATED_AS_ESTABLISHED")
    if script == "CYRILLIC" and subject and "OBLIQUE_HINT" in subject.morphological \
            and "NOMINATIVE_HINT" not in subject.morphological \
            and "AGREEMENT_RESOLVED_COMPOUND" not in subject.morphological:
        # The guard stands on morphology alone.  The single exemption is a
        # hyphenated compound whose case was resolved by agreement with a
        # following finite verb -- there the ending is ambiguous rather than
        # oblique, and the generator recorded which evidence admitted it.  Any
        # other oblique nominal is still refused.
        violations.append("RUSSIAN_OBLIQUE_USED_AS_NOMINATIVE_SUBJECT")
    if script == "ARABIC" and head and _AR_DEFINITE.match(head.text or ""):
        violations.append("ARABIC_ARTICLE_READ_AS_VERBAL_PREFIX")
    if analysis.quoted_speaker and analysis.quoted_speaker.span is None \
            and analysis.quoted_speaker.text:
        violations.append("QUOTED_SPEAKER_ABSENT_FROM_SOURCE")
    if analysis.governing_clause and analysis.subject and analysis.subject.span:
        violations.append("GOVERNING_CLAUSE_SUBJECT_ALSO_BOUND_LOCALLY")
    if analysis.non_proposition_signals and (subject or head):
        if "NO_VERBAL_OR_PREDICATIVE_EVIDENCE" in analysis.non_proposition_signals:
            violations.append("ROLES_BOUND_IN_A_NON_PREDICATING_SPAN")
    return violations


# ===========================================================================
# §18 — ambiguity-aware resolution
# ===========================================================================

#: Structural evidence that the span asserts nothing.  A null reading backed by
#: these outscores a bound reading built from a lone weak head.
#: Only the decisive signals.  A recital opens lower-case and ends in a comma,
#: so the weaker fragment signals are true of it too; letting those carry the
#: null reading made it beat correct recital analyses.  These are the same
#: signals that already drive the non-proposition decision, so the null reading
#: wins exactly where the span is furniture and nowhere else.
_NULL_SUPPORTING = frozenset({
    "ALL_CAPS_RUBRIC", "DATE_OR_SESSION_LINE", "DOCUMENT_SYMBOL_LINE",
    "NO_VERBAL_OR_PREDICATIVE_EVIDENCE", "EMPTY_SPAN"})

#: Frozen manifestation/layout types on which the binder declines to bind a
#: proposition on the strength of the DECLARED type alone.  These values are
#: provenance evidence supplied by the extraction layer.
#:
#: `regions.py` is the TYPING AUTHORITY for every value here, and this module
#: does not re-type a region.  Four of the six are `STRUCTURAL_FURNITURE`
#: there ("not evidence in any reading, and never repairable into it"); two --
#: DOCUMENT_INDEX_ENTRY and HEADING_CONTEXT_ONLY -- are `REFERENTIAL_CONTENT`
#: there ("names a work or gives context; it does not assert about the
#: world").  The two typings differ, and the published sentence below says
#: which one applies rather than asserting the furniture typing of both.
#:
#: This list is deliberately NARROWER than `regions.STRUCTURAL_FURNITURE`:
#: BREADCRUMB, COOKIE_OR_CONSENT_TEXT, LANGUAGE_SELECTOR, PAGINATION_CONTROL,
#: SEARCH_CONTROL and SITE_SLOGAN are typed furniture there and are absent
#: here.  An earlier version of this comment justified their absence by
#: asserting that those containers "can carry governed or proposition-bearing
#: text".  That asserted the opposite of `regions.py`'s own documented typing
#: of the same six values, and it is WITHDRAWN: the disagreement was a
#: contradiction between two modules, not a finding about those containers.
#: What is true, and all that is claimed here, is that this refusal is a
#: SECOND and narrower refusal layered on top of the region layer's own
#: admission decision, taken on a caller-declared string that production does
#: not validate.  Widening it to match `regions.STRUCTURAL_FURNITURE` would be
#: a selection change and is not made here.
STRUCTURAL_NON_PROPOSITION_REGIONS = frozenset({
    "DOCUMENT_INDEX_ENTRY",
    "GENERIC_LINK_LABEL",
    "HEADER_FURNITURE",
    "FOOTER_FURNITURE",
    "HEADING_CONTEXT_ONLY",
    "NAVIGATION_MENU",
})

#: The members `regions.py` types REFERENTIAL_CONTENT rather than furniture.
#: Stated as a subset so the two halves cannot drift apart from the set above:
#: their union IS `STRUCTURAL_NON_PROPOSITION_REGIONS`, by construction.
REFERENTIAL_NON_PROPOSITION_REGIONS = frozenset({
    "DOCUMENT_INDEX_ENTRY",
    "HEADING_CONTEXT_ONLY",
}) & STRUCTURAL_NON_PROPOSITION_REGIONS

#: The remaining members, which `regions.py` types STRUCTURAL_FURNITURE.
FURNITURE_NON_PROPOSITION_REGIONS = (
    STRUCTURAL_NON_PROPOSITION_REGIONS - REFERENTIAL_NON_PROPOSITION_REGIONS)


def _non_proposition_region_sentence(content_region_type: str) -> str:
    """Why this region type is not a proposition container, TRUTHFULLY.

    P6.2 R4.  The single sentence this site used to publish asserted that the
    region "is typed document furniture" for every member of the set above.
    That is false of DOCUMENT_INDEX_ENTRY and HEADING_CONTEXT_ONLY, which
    `regions.py` deliberately types REFERENTIAL_CONTENT, with an explicit
    comment that the separation is intentional -- a published diagnostic
    naming the wrong typing authority.  Measured on the frozen population:
    eleven of the seventeen units reaching this site published the false
    claim.

    The sentence is now derived from which typing actually applies.  The
    furniture wording is unchanged, byte for byte, for the four values it was
    always true of.
    """
    if content_region_type in REFERENTIAL_NON_PROPOSITION_REGIONS:
        return (f"manifestation region {content_region_type} names a work or "
                "gives context rather than asserting about the world; it is "
                "not a proposition container")
    return (f"manifestation region {content_region_type} is typed document "
            "furniture, not a proposition container")


#: V581-D42.  Which slots a construction requires, which it merely permits, and
#: which it does not license at all.  Decided from production-visible evidence
#: only -- the head's origin rule, the presence of a transported governing
#: clause, and the non-proposition signals.
CONSTRUCTIONS: tuple[str, ...] = (
    "NON_PROPOSITION", "RECITAL", "GOVERNED_BY_ENACTMENT",
    "PASSIVE_OR_IMPERSONAL", "FINITE_CLAUSE",
)


def _construction_of(analysis: Analysis) -> str:
    """The semantic schema this analysis is competing under."""
    if _NULL_SUPPORTING.intersection(analysis.non_proposition_signals):
        return "NON_PROPOSITION"
    head = analysis.predicate_head
    rule = head.origin_rule if head is not None else ""
    if rule.startswith("RECITAL"):
        return "RECITAL"
    if analysis.governing_clause is not None:
        return "GOVERNED_BY_ENACTMENT"
    if head is not None and (
            "PASSIVE" in head.subtype or "IMPERSONAL" in head.subtype
            or "PASSIVE" in " ".join(head.morphological)):
        return "PASSIVE_OR_IMPERSONAL"
    return "FINITE_CLAUSE"


#: Third-person pronouns and demonstratives that stand for a participant named
#: elsewhere.  Binding one as the semantic subject fills the slot with a POINTER
#: rather than with the participant, which is what the reference records as
#: ANAPHORIC_SUBJECT_RECOVERABLE -- recoverable, not realised here.
#:
#: Spanish "el" and Italian "il" are deliberately ABSENT: they are articles, and
#: including them classified "El Comité de Estado Mayor" and "Il Ministro
#: dell'economia" as pronouns.  The Spanish pronoun is the accented "él".
_ANAPHORIC_SUBJECT = re.compile(
    r"^(sie|er|es|dies|diese|dieser|dieses|derselbe|"
    r"it|he|she|they|this|these|those|"
    r"elle|ils|elles|cela|celui|celle|"
    r"él|ella|ellos|ellas|esto|esta|este|"
    r"lui|lei|essi|esso|questo|questa|"
    r"он|она|оно|они|это|этот|эта|"
    r"هو|هي|هم|هذا|هذه)$", re.IGNORECASE)


def _subject_is_anaphoric(analysis: Analysis) -> bool:
    """Is the bound subject a pointer to a participant named elsewhere?"""
    subject = analysis.subject
    if subject is None:
        return False
    words = (subject.text or "").split()
    if not words:
        return False
    return bool(_ANAPHORIC_SUBJECT.match(_clean(words[0]))) and len(words) <= 3


def _subject_is_evidenceless_post_head_argument(analysis: Analysis) -> bool:
    """Is the bound subject an argument of the head rather than its subject?

    V581-D42-M5.  Two conjuncts, and neither alone is the claim.  The candidate
    carries NO syntactic evidence -- not bounded constituency, not ordering with
    respect to a finite verb, nothing -- and it lies entirely to the right of the
    predicate head, where that head's arguments sit.

    Position alone would be wrong: post-verbal subjects are ordinary in Russian
    and Arabic, and a candidate that follows its head while carrying evidence is
    untouched.  Emptiness alone would be wrong too: a preverbal nominal with no
    recorded marker is still in subject position, which is why
    "Комитет назначает директора" keeps ``Комитет`` -- itself unmarked -- and
    loses only the three post-head object bindings.

    MEASURED over the complete 120-unit population before implementation: 63
    analyses in 18 units fire, and NOT ONE of them is reference-compatible.  The
    construct fires on the selected analysis in 11 units, every one of which is
    currently selected incorrectly.
    """
    subject = analysis.subject
    head = analysis.predicate_head
    if subject is None or head is None:
        return False
    if subject.span is None or head.span is None:
        return False
    if subject.syntactic:
        return False
    return subject.span[0] >= head.span[1]


# V581-D42J.  These inventories license a grammatical relation, not an
# antecedent decision.  They are deliberately closed so that homographic
# articles (notably Italian ``il`` and Spanish ``el``) cannot acquire a subject
# relation merely because another language uses the same form as a pronoun.
_LOCAL_SUBJECT_PRONOUNS: dict[str, frozenset[str]] = {
    "de": frozenset({
        "sie", "er", "es", "dies", "diese", "dieser", "dieses",
        "derselbe",
    }),
    "en": frozenset({
        "it", "he", "she", "they", "this", "these", "those",
    }),
    "fr": frozenset({
        "il", "elle", "ils", "elles", "cela", "celui", "celle",
    }),
    "es": frozenset({
        "él", "ella", "ellos", "ellas", "esto", "esta", "este",
    }),
    "it": frozenset({
        "lui", "lei", "essi", "esso", "questo", "questa",
    }),
    "ru": frozenset({
        "он", "она", "оно", "они", "это", "этот", "эта", "что",
    }),
    "ar": frozenset({
        "هو", "هي", "هم", "هذا", "هذه",
    }),
}
_LOCAL_SUBJECT_COORDINATORS = frozenset(
    {"or", "oder", "ou", "o", "или", "أو"})
_LOCAL_COPULAR_FINITE = frozenset({
    "ist", "sind", "war", "waren", "est", "sont", "es", "son", "è",
    "sono",
})
_RELATION_BOUNDARY = re.compile(r"[.;:!?]")
_RELATION_WORD = re.compile(r"[^\W_]+(?:[’'-][^\W_]+)*", re.UNICODE)


def _relation_words(value: str) -> list[str]:
    return [
        match.group(0).casefold()
        for match in _RELATION_WORD.finditer(value)
    ]


def _local_pronominal_phrase(subject: Candidate, head: Candidate, text: str,
                             language: str | None) -> str | None:
    """Classify a maximal closed-class pronominal subject phrase."""
    tokens = _relation_words(subject.text or "")
    if language in _LOCAL_SUBJECT_PRONOUNS:
        inventory = _LOCAL_SUBJECT_PRONOUNS[language]
    else:
        inventory = frozenset().union(*_LOCAL_SUBJECT_PRONOUNS.values())
    if len(tokens) == 1 and tokens[0] in inventory:
        # A generator may offer both ``Sie`` and ``Sie oder er``.  Only the
        # maximal coordination carries the relation.
        if subject.span is not None and head.span is not None:
            between = _relation_words(text[subject.span[1]:head.span[0]])
            if (len(between) >= 2
                    and between[0] in _LOCAL_SUBJECT_COORDINATORS
                    and between[1] in inventory):
                return None
        return "SINGLE_CLOSED_CLASS_PRONOUN"
    if (len(tokens) == 3 and tokens[0] in inventory
            and tokens[1] in _LOCAL_SUBJECT_COORDINATORS
            and tokens[2] in inventory):
        return "MAXIMAL_COORDINATED_PRONOUNS"
    return None


def _local_pronominal_relation_for_pair(
        subject: Candidate, head: Candidate, text: str, language: str | None,
        clause_context: "CI.ClauseContext") -> RoleRelation | None:
    """Realise an overt pronoun's local grammatical relation to its head.

    The relation is emitted only from source-visible morphology, offsets and
    same-clause identity.  It never consumes an antecedent, a reference label,
    or a selector outcome.
    """
    if (subject.span is None or head.span is None
            or subject.span[1] > head.span[0]):
        return None
    phrase_kind = _local_pronominal_phrase(subject, head, text, language)
    if phrase_kind is None:
        return None

    pairing = CI.assess_pairing(
        subject, head, text, context=clause_context)
    if pairing["verdict"] != "SAME_CLAUSE":
        return None

    prefix = text[:subject.span[0]].rstrip()
    between_text = text[subject.span[1]:head.span[0]]
    between_words = _relation_words(between_text)
    subject_words = _relation_words(subject.text)
    first = subject_words[0] if subject_words else ""

    # Russian relative ``что`` can itself be the subordinate subject.  Its
    # complementizer use is excluded by requiring only an adverbial -о/-ё run
    # before the finite head, and no overt nominal subject in between.
    relative_chto = (
        language == "ru"
        and first == "что"
        and prefix.endswith(",")
        and bool(between_words)
        and all(word.endswith(("о", "ё")) for word in between_words)
    )
    if prefix and not relative_chto:
        return None
    if _RELATION_BOUNDARY.search(between_text):
        return None

    head_word_parts = _relation_words(head.text)
    head_word = head_word_parts[0] if head_word_parts else ""
    typed_finite = (
        "DEONTIC" in head.subtype
        or "PASSIVE" in head.subtype
        or "IMPERSONAL" in head.subtype
    )
    closed_copula = head_word in _LOCAL_COPULAR_FINITE
    direct_finite = (
        not between_words
        and head.origin_rule == "LAT_EXPLICIT_FINITE_PREDICATE"
    )
    if not (typed_finite or closed_copula or direct_finite or relative_chto):
        return None

    evidence_kind = (
        "RELATIVE_PRONOMINAL_SUBJECT_BEFORE_FINITE"
        if relative_chto else
        "CLOSED_CLASS_SUBJECT_COPULA_RELATION"
        if closed_copula and between_words else
        "LOCAL_SUBJECT_FINITE_ADJACENCY"
        if not between_words else
        "LOCAL_SUBJECT_TYPED_FINITE_RELATION"
    )
    clause_id = pairing["subject_clause_id"]
    return RoleRelation(
        relation_id=stable_id(
            "v5-8-1-role-relation",
            "LOCAL_OVERT_PRONOMINAL_SUBJECT_OF",
            subject.candidate_id,
            head.candidate_id,
            clause_id,
        ),
        relation_type="LOCAL_OVERT_PRONOMINAL_SUBJECT_OF",
        subject_candidate_id=subject.candidate_id,
        predicate_head_candidate_id=head.candidate_id,
        clause_id=clause_id,
        evidence_span=(subject.span[0], head.span[1]),
        evidence_kind=evidence_kind,
    )


def local_pronominal_subject_relations(
        *, subjects: Sequence[Candidate], heads: Sequence[Candidate],
        text: str, language: str | None,
        clause_context: "CI.ClauseContext") -> tuple[RoleRelation, ...]:
    """Build all decisive local overt-pronominal subject relations once."""
    language = _declared_language(language)
    relations: dict[tuple[str, str, str, str], RoleRelation] = {}
    for subject in subjects:
        for head in heads:
            relation = _local_pronominal_relation_for_pair(
                subject, head, text, language, clause_context)
            if relation is None:
                continue
            key = (
                relation.relation_type,
                relation.subject_candidate_id,
                relation.predicate_head_candidate_id,
                relation.clause_id,
            )
            relations.setdefault(key, relation)
    return tuple(relations.values())


def _has_decisive_local_pronominal_relation(analysis: Analysis) -> bool:
    return any(
        relation.relation_type == "LOCAL_OVERT_PRONOMINAL_SUBJECT_OF"
        and relation.decisiveness == "DECISIVE"
        for relation in analysis.role_relations
    )


def _attach_unique_closed_finite_relation(
        analyses: list[Analysis], text: str, script: str,
        clause_context: "CI.ClauseContext") -> None:
    """M1: attach the unique single-clause closed finite anchor relation."""
    if len(clause_context.clauses) != 1:
        return
    closed_heads = [
        (start, stop)
        for word, start, stop in CI.tokens(text)
        if CI._is_closed_class_head(CI._clean(word), script)
    ]
    if len(closed_heads) != 1:
        return
    anchor_start, anchor_stop = closed_heads[0]
    options = [
        analysis for analysis in analyses
        if analysis.predicate_head is not None
        and analysis.predicate_head.span is not None
        and analysis.predicate_head.span[0] <= anchor_start
        and analysis.predicate_head.span[1] >= anchor_stop
    ]
    if not options:
        return

    for analysis in options:
        subject = analysis.subject
        head = analysis.predicate_head
        if (subject is None or subject.span is None
                or head is None or head.span is None):
            continue
        pairing = CI.assess_pairing(
            subject, head, text, context=clause_context)
        if pairing["verdict"] != "SAME_CLAUSE":
            continue
        local = (
            "BOUNDED_CONSTITUENT" in subject.syntactic
            or "PRECEDES_FINITE_VERB" in subject.syntactic
        )
        relation = RoleRelation(
            relation_id=stable_id(
                "v5-8-1-role-relation",
                "LOCAL_SUBJECT_UNIQUE_CLOSED_FINITE_ANCHOR",
                subject.candidate_id,
                head.candidate_id,
                pairing["subject_clause_id"],
            ),
            relation_type="LOCAL_SUBJECT_UNIQUE_CLOSED_FINITE_ANCHOR",
            subject_candidate_id=subject.candidate_id,
            predicate_head_candidate_id=head.candidate_id,
            clause_id=pairing["subject_clause_id"],
            evidence_span=(
                min(subject.span[0], head.span[0]),
                max(subject.span[1], head.span[1]),
            ),
            evidence_kind=(
                "UNIQUE_CLOSED_FINITE_WITH_PREVERBAL_CONSTITUENT"
                if local else
                "UNIQUE_CLOSED_FINITE_WITH_OTHER_LOCAL_SUBJECT"
            ),
        )
        analysis.role_relations = analysis.role_relations + (relation,)


_GERMAN_LOCAL_FINITE = re.compile(
    r"^[a-zäöüß]{3,}(?:t|et)$", re.IGNORECASE)
_GERMAN_PARTICIPLE = re.compile(
    r"^(?:ge|zuge)\w*(?:t|et)$", re.IGNORECASE)
_GERMAN_FOLLOWING_AUXILIARY = re.compile(
    r"^(?:werden|worden|sein|sollen|müssen|können|dürfen)\b",
    re.IGNORECASE)


def _attach_german_lexical_finite_relation(
        analyses: list[Analysis], text: str, language: str | None,
        clause_context: "CI.ClauseContext") -> None:
    """M2: relate a local German nominal to its adjacent lexical finite."""
    if language != "de":
        return
    applicable: list[Analysis] = []
    heads: set[tuple[int, int]] = set()
    for analysis in analyses:
        head = analysis.predicate_head
        subject = analysis.subject
        if (head is None or subject is None
                or head.span is None or subject.span is None):
            continue
        word = head.text.strip(".,;:()[]")
        if (not _GERMAN_LOCAL_FINITE.match(word)
                or _GERMAN_PARTICIPLE.match(word)):
            continue
        if PT.classify(
                word,
                span_initial=_span_is_sentence_initial(text, head.span),
                script="LATIN").decisive:
            continue
        if text[subject.span[1]:head.span[0]].strip():
            continue
        if _GERMAN_FOLLOWING_AUXILIARY.match(
                text[head.span[1]:].lstrip()):
            continue
        if subject.origin_rule != "LAT_PREVERBAL":
            continue
        pairing = CI.assess_pairing(
            subject, head, text, context=clause_context)
        if pairing["verdict"] != "SAME_CLAUSE":
            continue
        heads.add(head.span)
        applicable.append(analysis)
    if len(heads) != 1:
        return
    for analysis in applicable:
        subject = analysis.subject
        head = analysis.predicate_head
        assert subject is not None and subject.span is not None
        assert head is not None and head.span is not None
        pairing = CI.assess_pairing(
            subject, head, text, context=clause_context)
        relation = RoleRelation(
            relation_id=stable_id(
                "v5-8-1-role-relation",
                "LOCAL_SUBJECT_GERMAN_LEXICAL_FINITE",
                subject.candidate_id,
                head.candidate_id,
                pairing["subject_clause_id"],
            ),
            relation_type="LOCAL_SUBJECT_GERMAN_LEXICAL_FINITE",
            subject_candidate_id=subject.candidate_id,
            predicate_head_candidate_id=head.candidate_id,
            clause_id=pairing["subject_clause_id"],
            evidence_span=(
                min(subject.span[0], head.span[0]),
                max(subject.span[1], head.span[1]),
            ),
            evidence_kind="ADJACENT_NOMINAL_AND_NONPARTICIPIAL_FINITE",
        )
        analysis.role_relations = analysis.role_relations + (relation,)


_REQUIRED_TO_FRAME = re.compile(
    r"\b(?:is|are)\s+required\s+to\b", re.IGNORECASE)


def _attach_required_to_relation(
        analyses: list[Analysis], text: str,
        clause_context: "CI.ClauseContext") -> None:
    """M3: type the closed English ``is/are required to`` deontic frame."""
    match = _REQUIRED_TO_FRAME.search(text)
    if match is None:
        return
    auxiliary = match.group(0).split()[0]
    head_span = (match.start(), match.start() + len(auxiliary))
    for analysis in analyses:
        subject = analysis.subject
        head = analysis.predicate_head
        if (subject is None or subject.span is None
                or head is None or head.span != head_span):
            continue
        pairing = CI.assess_pairing(
            subject, head, text, context=clause_context)
        if pairing["verdict"] != "SAME_CLAUSE":
            continue
        relation = RoleRelation(
            relation_id=stable_id(
                "v5-8-1-role-relation",
                "LOCAL_SUBJECT_REQUIRED_TO_DEONTIC_FRAME",
                subject.candidate_id,
                head.candidate_id,
                pairing["subject_clause_id"],
            ),
            relation_type="LOCAL_SUBJECT_REQUIRED_TO_DEONTIC_FRAME",
            subject_candidate_id=subject.candidate_id,
            predicate_head_candidate_id=head.candidate_id,
            clause_id=pairing["subject_clause_id"],
            evidence_span=(
                min(subject.span[0], head.span[0]),
                max(subject.span[1], match.end()),
            ),
            evidence_kind="CLOSED_REQUIRED_TO_DEONTIC_FRAME",
        )
        analysis.role_relations = analysis.role_relations + (relation,)


#: V581-D42-M4'.  A German matrix subject interrupted by a relative clause, then
#: resumed by a matrix modal.  Three properties are definitional, not tuning:
#:
#: * the relative material carries NO sentence terminator, so the pattern cannot
#:   reach across a full stop into an unrelated clause;
#: * the clause-final finite may be any German finite auxiliary or modal, because
#:   that is what closes a verb-final relative clause -- restricting it to the
#:   lemmas of one attested unit would be fitting to that unit;
#: * the token after the opening comma must be a relative pronoun, which is what
#:   separates this from the correlative apodosis "…, so kann der Erblasser …".
_POST_RELATIVE_MODAL = re.compile(
    r",\s+(?:der|die|das|dem|den|deren|dessen|welche[rmns]?)\b"
    r"[^.;:!?\n\r]*?"
    r"\b(?:ist|sind|war|waren|hat|haben|hatte|hatten|"
    r"wird|werden|wurde|wurden|"
    r"soll|sollen|sollte|sollten|will|wollen|wollte|wollten|"
    r"kann|können|konnte|konnten|muss|müssen|musste|mussten|"
    r"darf|dürfen|durfte|durften|mag|mögen|mochte|mochten)"
    r"\s*,\s*"
    r"(muss|müssen|soll|sollen|kann|können|darf|dürfen|will|wollen|"
    r"mag|mögen)\b",
    re.IGNORECASE,
)


def _attach_post_relative_modal_relation(
        analyses: list[Analysis], text: str, language: str | None,
        clause_context: "CI.ClauseContext") -> None:
    """M4': relate an interrupted German subject to its resumed matrix modal."""
    if language != "de":
        return
    match = _POST_RELATIVE_MODAL.search(text)
    if match is None:
        return
    open_comma = match.start()
    modal_span = match.span(1)
    for analysis in analyses:
        subject = analysis.subject
        head = analysis.predicate_head
        if (subject is None or subject.span is None
                or head is None or head.span is None):
            continue
        # The head is the resumed modal itself.
        if not (head.span[0] <= modal_span[0]
                and head.span[1] >= modal_span[1]):
            continue
        # The subject is the constituent the relative clause INTERRUPTS: it
        # begins at or before the interruption, reaches it, and ends before the
        # modal that resumes it.  A fragment stopping short of the opening comma
        # is not what was interrupted; a span running past the modal has
        # swallowed its own predicate head.
        if not (subject.span[0] <= open_comma
                and subject.span[1] >= open_comma
                and subject.span[1] <= modal_span[0]):
            continue
        pairing = CI.assess_pairing(
            subject, head, text, context=clause_context)
        clause_id = pairing["predicate_clause_id"]
        if clause_id is None:
            continue
        relation = RoleRelation(
            relation_id=stable_id(
                "v5-8-1-role-relation",
                "POST_RELATIVE_MODAL_RESUMES_SUBJECT",
                subject.candidate_id,
                head.candidate_id,
                clause_id,
            ),
            relation_type="POST_RELATIVE_MODAL_RESUMES_SUBJECT",
            subject_candidate_id=subject.candidate_id,
            predicate_head_candidate_id=head.candidate_id,
            clause_id=clause_id,
            evidence_span=(
                min(subject.span[0], open_comma),
                max(subject.span[1], modal_span[1]),
            ),
            evidence_kind="RELATIVE_CLAUSE_CLOSES_BEFORE_RESUMING_MODAL",
        )
        analysis.role_relations = analysis.role_relations + (relation,)


#: V581-D42-M10.  Punctuation that closes a bounded dependent region, across
#: the scripts in the population: Latin/Cyrillic comma and semicolon, Arabic
#: comma and semicolon.
_REGION_CLOSERS = ",\u060c;\u061b"


def _bounded_dependent_regions(
        text: str, clause_context: "CI.ClauseContext") -> list[tuple[int, int]]:
    """Comma-bounded regions opened by a relativiser or a dependent clause.

    A region with no following closer is NOT a bounded interruption and opens no
    region: an unclosed relativiser would otherwise swallow the remainder of the
    span and defer every head in it.
    """
    starts = {
        event.character_offset for event in clause_context.boundary_events
        if event.event_type in ("RELATIVISER", "SUBORDINATOR")
    }
    starts |= {
        clause.character_start for clause in clause_context.clauses
        if clause.relation_to_parent != "MATRIX"
    }
    regions: list[tuple[int, int]] = []
    for start in sorted(starts):
        closers = [
            index for index in
            (text.find(character, start) for character in _REGION_CLOSERS)
            if index != -1
        ]
        if not closers:
            continue
        regions.append((start, min(closers) + 1))
    return regions


def _record_head_clause_state(
        analyses: list[Analysis], text: str,
        clause_context: "CI.ClauseContext") -> None:
    """Record where each analysis's head sits relative to the finite matrix.

    V581-D42-M7.  Computed once here because this is where the clause context
    lives; the selector cannot recompute it, having neither the text nor the
    context.  It is a recorded structural fact about the reading, in the same
    sense as its typed relations, and it removes nothing from the lattice.
    """
    matrix_ids = {
        clause.clause_id for clause in clause_context.clauses
        if clause.relation_to_parent == "MATRIX" and clause.has_finite_head
    }
    # V581-D42-M9.  A correlative apodosis is already a declared boundary-event
    # type in clause_identity; where one is opened, it is the clause that says
    # what follows from the condition, and it carries the span's predication.
    apodosis_ids = {
        clause.clause_id for clause in clause_context.clauses
        if clause.opening_event is not None
        and clause.opening_event.event_type == "CORRELATIVE_APODOSIS"
    }
    regions = _bounded_dependent_regions(text, clause_context)
    toks = CI.tokens(text)
    for analysis in analyses:
        head = analysis.predicate_head
        clause = None
        if head is not None and head.span is not None:
            rng = CI.token_range(head.span, toks)
            clause = (
                clause_context.clause_at_token(rng[0])
                if rng is not None else None
            )
        if not matrix_ids or head is None or head.span is None:
            analysis.head_clause_state = "NO_FINITE_MATRIX_CLAUSE"
        else:
            analysis.head_clause_state = (
                "IN_FINITE_MATRIX_CLAUSE"
                if clause is not None and clause.clause_id in matrix_ids
                else "OUTSIDE_FINITE_MATRIX_CLAUSE"
            )
        if not apodosis_ids:
            analysis.head_correlative_state = "NO_APODOSIS"
        else:
            analysis.head_correlative_state = (
                "IN_APODOSIS"
                if clause is not None and clause.clause_id in apodosis_ids
                else "OUTSIDE_APODOSIS"
            )
        if not regions or head is None or head.span is None:
            analysis.head_region_state = "NO_DEPENDENT_REGION"
        else:
            analysis.head_region_state = (
                "IN_DEPENDENT_REGION"
                if any(start <= head.span[0] and head.span[1] <= stop
                       for start, stop in regions)
                else "OUTSIDE_DEPENDENT_REGION"
            )


#: required -> a missing one is a strong penalty; licensed -> permitted and
#: rewarded when supported; anything else populated is unsupported.
#:
#: A RECITAL licenses no local subject.  _recital_candidates says why in its own
#: docstring: "Its subject is not in the span and must not be invented there;
#: the reference is unanimous on this across 20 units in four languages."  The
#: same holds once a governing clause has been transported: the subject is
#: inherited, so binding a local one asserts a second, different subject.
_SLOT_SCHEMA: dict[str, tuple[frozenset[str], frozenset[str]]] = {
    "NON_PROPOSITION": (frozenset(), frozenset()),
    "RECITAL": (frozenset({"predicate_head"}),
                frozenset({"predicate_head", "predicate_complement",
                           "governing_clause"})),
    "GOVERNED_BY_ENACTMENT": (frozenset({"governing_clause"}),
                              frozenset({"predicate_head", "predicate_complement",
                                         "governing_clause"})),
    "PASSIVE_OR_IMPERSONAL": (frozenset({"predicate_head"}),
                              frozenset({"predicate_head", "subject",
                                         "predicate_complement"})),
    "FINITE_CLAUSE": (frozenset({"predicate_head"}),
                      frozenset({"predicate_head", "subject",
                                 "predicate_complement", "governing_clause"})),
}

#: Structural weights.  Chosen once, for what each term means, and NOT adjusted
#: afterwards to chase a margin: the D42 cohort's wrong-over-compatible gap was
#: 0.0633-0.1267, and a constant fitted to close it would be indistinguishable
#: from tuning on the answer.  A missing required slot is worse than an
#: unsupported extra one because the reading fails to say anything the
#: construction demands; a supported optional slot earns a bounded amount so
#: that populating slots can never dominate satisfying them.
REQUIRED_SLOT_MISSING_PENALTY = 0.35
UNSUPPORTED_SLOT_PENALTY = 0.15
SUPPORTED_OPTIONAL_SLOT_CREDIT = 0.03
MAX_OPTIONAL_CREDIT = 0.06


def _null_reading_support(analysis: Analysis, predication: str) -> int:
    """How much evidence there is that this span asserts nothing.

    The null reading claims the span is not a proposition, and `clauses.py` is
    production's dedicated multilingual answer to exactly that question -- yet
    the selector had never consulted it, resting instead on a flat base plus the
    non-proposition signals.  Candidate head confidence cannot stand in for it:
    "muessen", "dolzhna" and "leistet" are CORRECT heads sitting at 0.05, the
    same value junk heads receive, so confidence does not separate a real
    predicate from a spurious one at all.

    The predication verdict does separate them, monotonically.  Measured over the
    120 units: the null reading is correct in 24/34 spans called
    NOT_A_FINITE_CLAUSE, 10/26 called FINITE_CLAUSE_UNRESOLVED, and only 7/60
    called FINITE_CLAUSE_ESTABLISHED.  So a positive finding of
    non-predication counts as one more supporting signal, and a positive finding
    of established predication counts against -- in the same units as every
    other signal, rather than through a separate fitted weight.
    """
    support = len(_NULL_SUPPORTING.intersection(analysis.non_proposition_signals))
    if predication == "NOT_A_FINITE_CLAUSE":
        support += 1
    elif predication == "FINITE_CLAUSE_ESTABLISHED":
        support -= 1
    return support


def _score(analysis: Analysis, context=None, text: str = "",
           predication: str = "") -> float:
    """Score an analysis against its construction's slot schema.

    The previous form was the mean confidence of whatever roles happened to be
    populated, plus 0.05 for carrying both a head and a subject.  That bonus is
    V581-D42 in one line: it pays for CARDINALITY.  Measurement found 11 units
    whose reference requires no subject at all, where the selected analysis bound
    one and populated more slots than the correct reading -- and 25 of 48
    selection failures over the frozen lattice have exactly that shape.

    Populating a slot the construction does not license is now a cost, not a
    credit.  Leaving a slot empty that the construction does not require is
    neutral, never a penalty -- which is what keeps this from becoming a blanket
    tax on subjects.
    """
    construction = _construction_of(analysis)
    required, licensed = _SLOT_SCHEMA[construction]

    populated = {name: getattr(analysis, name) for name in
                 ("subject", "predicate_head", "predicate_complement",
                  "governing_clause")}
    filled = {name for name, value in populated.items() if value is not None}

    if construction == "NON_PROPOSITION" and not filled:
        # The null reading is evidenced, not merely empty: it scores by how much
        # of the span says "this is not a proposition".
        # REFUTED EXPERIMENT.  Making the score PROPORTIONAL to the evidence --
        # 0.30 * signals, scoring an unevidenced null at zero -- looked correct,
        # and the discriminator is sharp: where the null reading is INCORRECT all
        # 79 units carry zero supporting signals.  MEASURED: selection fell from
        # 82/120 to 73/120.  Precise, but poor recall -- 24 units where the null
        # reading IS correct also carry zero signals.  The flat base stays, and
        # the predication verdict supplies the evidence that was missing.
        return round(max(0.0, min(0.95, 0.30 + 0.30 * _null_reading_support(
            analysis, predication))), 4)

    if not filled:
        # REFUTED EXPERIMENT.  Making the score PROPORTIONAL to the evidence --
        # 0.30 * signals, scoring an unevidenced null at zero -- looked correct,
        # and the discriminator is sharp: where the null reading is INCORRECT all
        # 79 units carry zero supporting signals.  MEASURED: selection fell from
        # 82/120 to 73/120.  Precise, but poor recall -- 24 units where the null
        # reading IS correct also carry zero signals.  The flat base stays, and
        # the predication verdict supplies the evidence that was missing.
        return round(max(0.0, min(0.95, 0.30 + 0.30 * _null_reading_support(
            analysis, predication))), 4)

    # THE BASE IS THE REQUIRED SLOT, NOT THE AVERAGE OF WHAT HAPPENED TO BE
    # FILLED.  Averaging every populated role let a strong subject carry a
    # predicate that barely exists: "§ 137i Übergangsregelung" scored 0.425 as a
    # proposition on a head of confidence 0.05, beating the null reading's 0.30,
    # and 19 of 43 selection failures have exactly that shape.  An analysis
    # cannot be better supported than the slot its construction requires, so the
    # required slot sets the ceiling and optional roles add only bounded credit.
    required_filled = required & filled
    if required_filled:
        score = sum(populated[name].confidence for name in required_filled) \
            / len(required_filled)
    else:
        score = sum(populated[name].confidence for name in filled) / len(filled)

    # A required slot left empty: the reading does not say what the construction
    # demands.  An absent slot that is NOT required is neutral.
    score -= REQUIRED_SLOT_MISSING_PENALTY * len(required - filled)

    # A slot populated that this construction does not license.
    #
    # An anaphoric subject is an UNSUPPORTED populated slot, not a supported
    # optional one: it fills the participant slot with a pointer to something
    # named elsewhere, which is exactly what the reference records as
    # ANAPHORIC_SUBJECT_RECOVERABLE.  Measured over the population, the rule is
    # 8/9 precise once articles are excluded from the pronoun inventory -- "El
    # Comité de Estado Mayor" is not a pronoun -- with one genuine false
    # positive where a bare "sie" IS the reference subject.
    #
    # REFUTED EXPERIMENT, removed rather than left unreachable.  Typed clause
    # identity can say whether the predicate's own clause realises a subject at
    # all, and penalising a bound subject where it does not looked like the
    # missing signal: 29 of 43 failures still turned on slot cardinality, and
    # both readings classify as FINITE_CLAUSE, so the schema alone never
    # separated them.  MEASURED: selection fell from 77/120 to 71/120 and
    # INSUFFICIENT_ROLE_EVIDENCE rose from 27 to 42 -- the penalty pushed
    # subject-bearing readings below headless ones, so the selector stopped
    # finding a predicate rather than finding the right subject.  The signal is
    # real; used as a flat penalty it is destructive.
    #
    # M5 classifies the same slot on the same principle: a binding whose
    # candidate carries no syntactic evidence and sits entirely after the head
    # is populated, not supported.  It is an argument the head governs.  This is
    # not the refuted rule above -- that one keyed on whether the predicate's
    # CLAUSE realised a subject and fired on evidenced bindings; this keys on the
    # candidate's own evidence and is silent wherever any marker is present.
    unsupported = filled - licensed
    if ("subject" in filled
            and _subject_is_anaphoric(analysis)
            and not _has_decisive_local_pronominal_relation(analysis)):
        unsupported = unsupported | {"subject"}
    if ("subject" in filled
            and _subject_is_evidenceless_post_head_argument(analysis)):
        unsupported = unsupported | {"subject"}
    score -= UNSUPPORTED_SLOT_PENALTY * len(unsupported)

    # Supported optional slots contribute, but boundedly.
    optional_filled = (filled & licensed - required) - unsupported
    score += min(MAX_OPTIONAL_CREDIT,
                 SUPPORTED_OPTIONAL_SLOT_CREDIT * len(optional_filled))

    return round(max(0.0, min(score, 1.0)), 4)


def _build_analyses(candidates: list[Candidate], text: str, script: str,
                    signals: list[str], language: str | None = None
                    ) -> list[Analysis]:
    # Segment once for the whole lattice: clause structure is a property of the
    # text, and scoring thirty analyses must not segment it thirty times.
    clause_context = CI.build_clause_context(text)
    predication = CL.detect(text).state
    # V581-D46F PREDICATE-ROLE COMPATIBILITY, authorised by
    # D46_FREEZE_INTERPRETATION_F.
    #
    # Typing is ATTACHED here and enforced NOWHERE here.  The predecessor design
    # (D38B) refused non-verbal candidates a finite-predicate slot at this point,
    # and it failed: an analysis removed at assembly never reaches admissibility,
    # so to every D46 measurement it is indistinguishable from a candidate that
    # was never generated.  Two gates fell and the contract required reversion.
    #
    # The ruling that followed is the reason this loop only annotates: predicate
    # type may be ATTACHED during assembly, but predicate-type incompatibility
    # must be adjudicated by admissibility, where the evaluator can see it as a
    # rejection and agree or disagree with it.  Every constructible analysis
    # stays in the pre-admissibility lattice.
    typed: list[Candidate] = []
    for candidate in candidates:
        if candidate.role_type != "PREDICATE_HEAD":
            typed.append(candidate)
            continue
        following = ""
        if candidate.span is not None:
            after = text[candidate.span[1]:candidate.span[1] + 40].split()
            following = after[0] if after else ""
        typing = PT.classify(
            candidate.text,
            span_initial=_span_is_sentence_initial(text, candidate.span),
            script=script, following_token=following)
        typed.append(replace(candidate,
                             lexical_category=typing.lexical_category,
                             predicate_type=typing.predicate_type))
    candidates = typed

    heads = [c for c in candidates if c.role_type == "PREDICATE_HEAD"]
    subjects = [c for c in candidates if c.role_type == "SUBJECT"]
    complements = [c for c in candidates if c.role_type == "PREDICATE_COMPLEMENT"]
    governing = [c for c in candidates if c.role_type == "GOVERNING_CLAUSE"]
    issuers = [c for c in candidates if c.role_type == "INSTITUTIONAL_ISSUER"]
    attribution = [c for c in candidates if c.role_type == "ATTRIBUTION"]
    speakers = [c for c in candidates if c.role_type == "QUOTED_SPEAKER"]
    local_subject_relations = local_pronominal_subject_relations(
        subjects=subjects,
        heads=heads,
        text=text,
        language=language,
        clause_context=clause_context,
    )
    relations_by_pair: dict[tuple[str, str], tuple[RoleRelation, ...]] = {}
    for relation in local_subject_relations:
        key = (
            relation.subject_candidate_id,
            relation.predicate_head_candidate_id,
        )
        relations_by_pair[key] = relations_by_pair.get(key, ()) + (relation,)

    analyses: list[Analysis] = []
    head_options: list[Candidate | None] = list(heads) or [None]
    for head in head_options:
        complement = None
        if head is not None and head.span is not None:
            after = [c for c in complements
                     if c.span and c.span[0] >= head.span[1]]
            complement = max(after, key=lambda c: c.confidence, default=None)
        # D38 pairing repair.  Whether a head binds a *local* subject span is a
        # dimension with two values, and the lattice has to carry both for every
        # head.  It did not: with nearby subjects present the subject-absent
        # reading was dropped, and under a governing clause the subject-bearing
        # reading was.  Measurement named the consequence
        # BOTH_REACHABLE_NEVER_CO_OCCUR -- the reference's subject and the
        # reference's head each existed in the candidate set, in different
        # analyses, so no analysis was ever compatible with the reference.  That
        # is 12 of the 18 remaining units, across five languages and every
        # blocker category, which is why it read as five separate defects.
        #
        # This generates no new candidates and widens no vocabulary; it only
        # stops discarding pairings of evidence already found.  Deciding between
        # them is selection's job, and selection has the scores to do it.
        subject_options: list[Candidate | None] = [None]
        if head is not None:
            near = [s for s in subjects if s.span and head.span
                    and abs(s.span[0] - head.span[0]) <= 400]
            # Rank by adjacency to the finite verb, not by generation order.
            # Truncating an unordered list cost a unit whose only plausible
            # subject sat at index 8 of 17: every preverbal candidate starts at
            # offset 0, so start-to-start distance cannot separate them, while
            # the gap between the subject's edge and the verb's can -- that gap
            # is 1 character for the correct span and 42 for its rivals.
            def _adjacency(candidate):
                return min(abs(head.span[0] - candidate.span[1]),
                           abs(candidate.span[0] - head.span[1]))

            near.sort(key=_adjacency)
            # Neither ordering alone is safe.  Proximity promotes the longest
            # prefix span, because a run growing up to the verb is always the
            # most adjacent thing to it -- the very span the plausibility gate
            # rejects.  Constituency-first drops correct full runs elsewhere.
            # Measured: proximity alone 119/120, constituency-first 116/120.
            # The defect is the arbitrary truncation, not the sort, so take the
            # union of both readings instead of choosing between them.
            limit = 3 if governing else 6
            bounded = [c for c in near if "BOUNDED_CONSTITUENT" in c.syntactic]
            subject_options = list(dict.fromkeys(bounded[:2] + near[:limit]))
            subject_options = list(subject_options) + [None]
        for subject in subject_options:
            analysis = Analysis(
                analysis_id=stable_id("v5-8-1-analysis",
                                      str(head.span if head else None),
                                      str(subject.span if subject else None)),
                subject=subject, predicate_head=head,
                predicate_complement=complement,
                governing_clause=governing[0] if governing else None,
                antecedent=None,
                semantic_actor=None,
                institutional_issuer=issuers[0] if issuers else None,
                attribution=attribution[0] if attribution else None,
                quoted_speaker=speakers[0] if speakers else None,
                non_proposition_signals=tuple(signals),
                role_relations=(
                    relations_by_pair.get(
                        (subject.candidate_id, head.candidate_id), ())
                    if subject is not None and head is not None else ()
                ),
            )
            analysis.constraint_violations = tuple(
                hard_constraints(analysis, text, script))
            analysis.compatibility_score = _score(
                analysis, clause_context, text, predication)
            analyses.append(analysis)

    # D38 assembly repair.  The null reading -- "nothing here is bound at all"
    # -- must always be an alternative, not a fallback reached only when no head
    # candidate was proposed.  While the builder used `heads or [None]`, one
    # spurious head made the correct analysis unrepresentable for every unit the
    # reference calls NO_SEMANTIC_SUBJECT / NO_SEMANTIC_PREDICATE: 19 of the 120
    # diagnostic units, the largest single block of the candidate-recall gap.
    #
    # It is built explicitly rather than by threading None through the normal
    # path, because a null reading that still carries a governing clause is not
    # null -- that version outscored correct recital analyses and reported
    # ambiguity where the source has none.
    # The typed no-binding reading is always *representable*; whether it wins is
    # a separate question decided by its score.  Gating assembly on decisive
    # evidence conflated the two and left the correct analysis absent for 14
    # units whose reference states are NO_SEMANTIC_*.  Recall asks whether the
    # analysis exists; selection asks whether it should be chosen.
    if heads:
        null = Analysis(
            analysis_id=stable_id("v5-8-1-analysis", "NULL", text[:40]),
            subject=None, predicate_head=None, predicate_complement=None,
            governing_clause=None, antecedent=None, semantic_actor=None,
            institutional_issuer=None, attribution=None, quoted_speaker=None,
            non_proposition_signals=tuple(signals))
        null.constraint_violations = ()
        null.compatibility_score = _score(
            null, clause_context, text, predication)
        analyses.append(null)
    _attach_unique_closed_finite_relation(
        analyses, text, script, clause_context)
    _attach_german_lexical_finite_relation(
        analyses, text, language, clause_context)
    _attach_required_to_relation(
        analyses, text, clause_context)
    _attach_post_relative_modal_relation(
        analyses, text, language, clause_context)
    _record_head_clause_state(analyses, text, clause_context)
    return analyses


def _carries_predication(pool) -> bool:
    """Does ANY analysis in `pool` carry a predicate head or a governor?"""
    return any(analysis.predicate_head is not None
               or analysis.governing_clause is not None
               for analysis in pool)


def _no_predication_sentence(analyses, comparison_pool) -> str:
    """The scope over which "carries no predication" is actually true.

    P6.2 R4.  The single sentence this site used to publish -- "no analysis
    carries a predicate or a governing clause" -- is a UNIVERSAL claim over
    the analyses, decided from the best-scoring one alone.  It is false
    wherever a lower-ranked legal analysis carries a predicate head, and it is
    overbroad wherever such an analysis exists but was filtered out of the
    comparison pool before the comparison.  Measured on the frozen population:
    of the eight units reaching this site, three published a false claim, two
    published an overbroad one, and on three of those the SAME record emitted
    predicate_state=GOVERNING_CLAUSE_PREDICATE beside it.

    The claim is now made over the widest scope on which it is TRUE, and the
    universal wording is unchanged, byte for byte, where it was always true.
    """
    if not _carries_predication(analyses):
        return "no analysis carries a predicate or a governing clause"
    if not _carries_predication(comparison_pool):
        return ("no analysis in the comparison pool carries a predicate or a "
                "governing clause")
    return ("the best-supported analysis carries neither a predicate nor a "
            "governing clause")


#: The D42J relation types whose branch orders the comparison pool by
#: `closed_finite_relation_key` -- subject locality, then subject confidence,
#: then compatibility score -- rather than by compatibility score alone.
#:
#: P6.3 B1.  This set is named ONCE and read by BOTH the sort site and the
#: sentence that reports the selection basis.  While it was an anonymous set
#: literal inside the branch, nothing could stop a published justification from
#: describing an ordering the code no longer used; now the two cannot disagree
#: without the constant moving under both of them.
_RELATION_ORDERED_POOL = frozenset({
    "LOCAL_SUBJECT_UNIQUE_CLOSED_FINITE_ANCHOR",
    "LOCAL_SUBJECT_GERMAN_LEXICAL_FINITE",
    "LOCAL_SUBJECT_REQUIRED_TO_DEONTIC_FRAME",
    "POST_RELATIVE_MODAL_RESUMES_SUBJECT",
})


def _comparison_provenance(*, considered: int, legal: int, structural: int,
                           compared: int, active_relation_type: str) -> str:
    """WHICH analyses the margin comparison ran over, of all it considered.

    P6.3 B1(i).  The margin sentences count over the COMPARISON POOL while the
    same record publishes `analyses_considered = len(analyses)` -- the full
    list.  On the frozen population the two quantities diverge on 14 of the 40
    units reaching this family, by as much as 7 against 200.  Neither number is
    wrong; the sentence simply never said which one it meant, which is why two
    review seats measured the same sentence TRUE and FALSE respectively and
    were both right.  A claim that does not state its scope has no truth value
    to measure.

    This returns the missing scope as a phrase derived from the four counts
    themselves, so the sentence cannot claim a narrowing that did not happen or
    omit one that did.  Empty string means nothing was narrowed and the
    compared pool IS everything considered, in which case the sentence says so
    by saying nothing extra.
    """
    if compared == considered:
        return ""
    narrowers: list[str] = []
    if legal < considered:
        narrowers.append("the hard-constraint filter")
    if structural < legal:
        narrowers.append("the structural precedence filters")
    if active_relation_type and compared < structural:
        narrowers.append(f"a decisive {active_relation_type} relation")
    if not narrowers:
        # Defensive: the three narrowings are the only ones between `analyses`
        # and `comparison_pool`, so this is unreachable.  If a fourth is ever
        # added, the scope stays TRUE and merely stops naming its cause,
        # instead of silently attributing the gap to the wrong filter.
        return f"drawn from {considered} considered"
    if len(narrowers) == 1:
        left_by = narrowers[0]
    else:
        left_by = ", ".join(narrowers[:-1]) + " and " + narrowers[-1]
    return f"what {left_by} left of {considered} considered"


def _ambiguity_sentence(*, within_margin: int, compared: int,
                        provenance: str) -> str:
    """The ambiguity finding, quantified over a STATED scope.

    The relation is the one the code actually tests -- `best.compatibility_
    score - a.compatibility_score < SEPARATION_MARGIN` -- which is ONE-SIDED:
    it admits an analysis that OUTSCORES the selected reading, because a
    negative difference is also less than the margin.  "Within the margin"
    would be a two-sided claim and would be false for such an analysis, so the
    wording states the one-sided test literally.  No corpus unit currently
    exhibits that case; the wording is chosen so that one would not make the
    sentence false.

    The count includes the selected reading itself, which scores exactly zero
    below itself, so `within_margin + 1` is what the phrase names.
    """
    head = (f"{within_margin + 1} of the {compared} analyses compared score "
            f"less than the declared {SEPARATION_MARGIN} margin below the "
            f"selected reading")
    if provenance:
        head += f", and those {compared} are {provenance}"
    return head + "; the source does not decide"


def _unique_selection_sentence(*, compared: int, provenance: str,
                               active_relation_type: str) -> str:
    """WHY this reading was admitted, derived from how it was actually chosen.

    P6.3 B1(ii).  This sentence is the SOLE justification every EVIDENCE_BOUND
    record carries, and the one it used to carry -- "one analysis leads the
    field by more than the declared margin" -- was false in two independent
    ways on the frozen population.

    VACUOUS: both units reaching it have a comparison pool of ONE, so there
    was no field to lead.  Reaching this branch requires only that no
    materially different rival lie within the margin, and an empty rival list
    satisfies that trivially.

    MISLEADING: on `d25unit-185e002e` the pool of one was left of 168 legal
    analyses by the structural filters and a decisive
    LOCAL_SUBJECT_GERMAN_LEXICAL_FINITE relation, and THREE of those 168
    strictly outscore the selected reading (0.28, 0.28, 0.25 against 0.18).
    The selection was made by D42J relation precedence -- semantic precedence,
    deliberately independent of score -- and reporting it as a margin win
    claimed the opposite of what happened.

    The three cases are therefore reported separately and each says what the
    code did:  a singleton pool says there was nothing to compare against and
    never claims to lead a field;  a relation-ordered pool names the relation
    and its subject-locality key as the basis and does not claim a scalar win;
    a score-ordered pool states the margin property the code actually tested.

    The margin clause is the exact negation of the test that reached here --
    `rivals` empty means every materially different member of the pool scores
    AT LEAST the margin below the selected reading -- and is one-sided for the
    same reason as `_ambiguity_sentence`.

    What this sentence deliberately does NOT do is report how many analyses
    OUTSIDE the compared pool outscore the selection.  Naming the narrowing is
    what makes the claim honest; naming a raw outscoring count without the
    precedence rationale would imply the higher scores should have won, which
    is the opposite of what D42J decides.
    """
    if compared == 1:
        head = "the selected reading is the only analysis compared"
        if provenance:
            head += f", which is {provenance}"
        return (head + "; no margin separated it from anything, because no "
                "other reading remained to compare it with")
    head = (f"every materially different reading among the {compared} "
            f"analyses compared scores at least the declared "
            f"{SEPARATION_MARGIN} margin below the selected reading")
    if active_relation_type in _RELATION_ORDERED_POOL:
        head += (f", which was chosen by a decisive {active_relation_type} "
                 "relation and its subject's locality and confidence rather "
                 "than by score")
    if provenance:
        head += f"; those {compared} are {provenance}"
    return head


def resolve_internal(analyses: list[Analysis], *,
                     enacting_context: bool = False,
                     governing_state: str = "GOVERNING_CLAUSE_NOT_SUPPLIED",
                     content_region_type: str = "UNKNOWN_STRUCTURAL_REGION",
                     ) -> tuple[str, Analysis | None, str]:
    """Pick a reading, or decline to, with a declared separation margin."""
    legal = [a for a in analyses if not a.constraint_violations]
    if content_region_type in STRUCTURAL_NON_PROPOSITION_REGIONS:
        null = next(
            (analysis for analysis in legal
             if analysis.subject is None
             and analysis.predicate_head is None
             and analysis.governing_clause is None),
            None)
        if null is None:
            return (
                "INVALID_BINDING_CANDIDATE",
                None,
                f"typed non-proposition region {content_region_type} has no "
                "lawful null analysis",
            )
        return (
            "INVALID_BINDING_CANDIDATE",
            null,
            _non_proposition_region_sentence(content_region_type),
        )
    # REFUTED EXPERIMENT.  A dominance rule was tried here: an analysis whose
    # predicate head is the governing predication anchor -- a finite head, in the
    # matrix clause -- would dominate one whose head is a noun or sits in a
    # subordinate clause.  The motivation was sound and the diagnosis was right:
    # candidate confidence ranks "Hat" above "kann" by 0.67 in the wrong
    # direction, and no scalar weight could overturn that without distorting
    # every other comparison.  MEASURED BY ABLATION: 90/120 with the rule and
    # 90/120 without it.  The clause-structure defects it depended on were real
    # and have been repaired in clause_identity, but the rule itself earns
    # nothing on this population, so it is removed rather than left as a code
    # path a later reader would take for load-bearing.
    if not legal:
        return ("INVALID_BINDING_CANDIDATE", None,
                "every candidate analysis violates a hard constraint")
    # WHERE the span's predication can live is settled before WHICH reading of
    # it is best supported.  M6, M9, M10 and M7 each defer a class of readings
    # whose head cannot be the span's predicate; the typed relations then choose
    # among what remains.  Running the structural filters first is not a
    # margin-chasing order -- MEASURED over the complete population, the two
    # orders select byte-identically on all 120 units and produce identical
    # resolution states -- it is the order in which the questions are asked.
    #
    # None of these four is a rejection.  Every analysis stays in ``legal``,
    # stays admissible, and stays available for audit and gate accounting; each
    # filter is skipped entirely when it would empty the pool; and none of them
    # moves a score, a constant, a threshold or a denominator.
    structural_pool = legal

    # V581-D42-M6.  A candidate POSITIVELY typed as an argument noun does not
    # head a predication.  `predicate_types` already computes this on every head
    # candidate, and its policy is deliberately narrow: a noun heading an Arabic
    # zero-copula or a fronted nominal predication is never typed ARGUMENT_NOUN,
    # because predicating without a verb is not a defect.  MEASURED: 434
    # analyses in 22 units carry such a head and NOT ONE is reference-compatible.
    verbal = [
        analysis for analysis in structural_pool
        if analysis.predicate_head is None
        or analysis.predicate_head.predicate_type != "ARGUMENT_NOUN"
    ]
    if verbal:
        structural_pool = verbal

    # V581-D42-M9.  Where the span states a condition and what follows from it,
    # the predication is what follows.  `clause_identity` already types the
    # apodosis as a boundary event.  MEASURED: 237 firings across the two units
    # that open one, NONE reference-compatible.
    if any(a.head_correlative_state == "OUTSIDE_APODOSIS"
           for a in structural_pool):
        in_apodosis = [
            analysis for analysis in structural_pool
            if analysis.head_correlative_state != "OUTSIDE_APODOSIS"
        ]
        if in_apodosis:
            structural_pool = in_apodosis

    # V581-D42-M10.  A head inside a comma-bounded dependent region does not
    # head the span's predication when a head outside every region exists.  A
    # region with no closing punctuation is not a bounded interruption and opens
    # no region at all.  MEASURED: 349 firings in 8 units, NONE
    # reference-compatible.
    if any(a.head_region_state == "IN_DEPENDENT_REGION"
           for a in structural_pool):
        outside_regions = [
            analysis for analysis in structural_pool
            if analysis.head_region_state != "IN_DEPENDENT_REGION"
        ]
        if outside_regions:
            structural_pool = outside_regions

    # V581-D42-M7, reopened under an explicit contract.  Where the span HAS a
    # finite matrix clause, a reading whose head sits outside it is considered
    # only if no reading keeps its head inside.  The guard IS the mechanism:
    # unguarded, "the head's clause is not MATRIX" fires on 766 analyses of
    # which SIX are reference-compatible, because in a span with no matrix
    # predication it demotes the only reading available.  Guarded, 419 firings
    # and NONE.
    if any(a.head_clause_state == "OUTSIDE_FINITE_MATRIX_CLAUSE"
           for a in structural_pool):
        in_matrix = [
            analysis for analysis in structural_pool
            if analysis.head_clause_state != "OUTSIDE_FINITE_MATRIX_CLAUSE"
        ]
        if in_matrix:
            structural_pool = in_matrix

    # D42J.  A decisive local subject relation makes the finite predication
    # relationally complete.  Compare those readings with one another before
    # considering scalar scores from readings which omit the overt subject or
    # bind an unrelated nominal.  This too is semantic precedence, not rejection.
    relation_complete: list[Analysis] = []
    active_relation_type = ""
    for relation_type in (
            "LOCAL_OVERT_PRONOMINAL_SUBJECT_OF",
            "LOCAL_SUBJECT_UNIQUE_CLOSED_FINITE_ANCHOR",
            "LOCAL_SUBJECT_GERMAN_LEXICAL_FINITE",
            "LOCAL_SUBJECT_REQUIRED_TO_DEONTIC_FRAME",
            "POST_RELATIVE_MODAL_RESUMES_SUBJECT"):
        relation_complete = [
            analysis for analysis in structural_pool
            if any(
                relation.relation_type == relation_type
                and relation.decisiveness == "DECISIVE"
                for relation in analysis.role_relations
            )
        ]
        if relation_complete:
            active_relation_type = relation_type
            break
    comparison_pool = relation_complete if relation_complete else structural_pool
    if active_relation_type in _RELATION_ORDERED_POOL:
        def closed_finite_relation_key(
                analysis: Analysis) -> tuple[int, float, float]:
            subject = analysis.subject
            if subject is None:
                return (0, -1.0, analysis.compatibility_score)
            local = (
                "BOUNDED_CONSTITUENT" in subject.syntactic
                or "PRECEDES_FINITE_VERB" in subject.syntactic
                or (
                    active_relation_type
                    in {
                        "LOCAL_SUBJECT_GERMAN_LEXICAL_FINITE",
                        "LOCAL_SUBJECT_REQUIRED_TO_DEONTIC_FRAME",
                        "POST_RELATIVE_MODAL_RESUMES_SUBJECT",
                    }
                    and "FOLLOWS_FINITE_VERB" in subject.syntactic
                )
            )
            return (
                int(local),
                subject.confidence,
                analysis.compatibility_score,
            )

        comparison_pool.sort(key=closed_finite_relation_key, reverse=True)
    else:
        comparison_pool.sort(key=lambda a: -a.compatibility_score)
    best = comparison_pool[0]
    if best.predicate_head is None and best.governing_clause is None:
        if _NULL_SUPPORTING.intersection(best.non_proposition_signals):
            return ("INVALID_BINDING_CANDIDATE", best,
                    "the best-supported reading is that the span asserts "
                    "nothing: " + "; ".join(sorted(
                        _NULL_SUPPORTING.intersection(
                            best.non_proposition_signals))))
        return ("INSUFFICIENT_ROLE_EVIDENCE", best,
                _no_predication_sentence(analyses, comparison_pool))
    # P6.2 R4.  The published sentence counts "analyses within the declared
    # margin".  It used to publish `len(rivals) + 1`, which is the count of
    # MATERIALLY DISTINCT readings within the margin -- a different and always
    # smaller quantity than the one the sentence names.  Measured on the frozen
    # population: one unit published 2 where 3 analyses lay within the margin.
    #
    # The two quantities are now computed separately and each is used for what
    # it is: material distinctness still DECIDES whether the binding is
    # ambiguous -- the selection algebra is untouched -- and the count the
    # sentence names is what the sentence publishes.
    within_margin = [
        a for a in comparison_pool[1:]
        if best.compatibility_score - a.compatibility_score < SEPARATION_MARGIN]
    rivals = [a for a in within_margin if _materially_different(best, a)]
    # P6.3 B1.  The scope every sentence in this family quantifies over,
    # derived from the four pool sizes the filter chain above actually
    # produced.  It is computed once, here, so the ambiguity finding and the
    # admission justification cannot state different scopes for the same
    # comparison.  Lengths are read AFTER the sort, which is safe because
    # sorting a list in place changes its order and never its length.
    provenance = _comparison_provenance(
        considered=len(analyses), legal=len(legal),
        structural=len(structural_pool), compared=len(comparison_pool),
        active_relation_type=active_relation_type)
    if rivals:
        return ("AMBIGUOUS_BINDING", best,
                _ambiguity_sentence(within_margin=len(within_margin),
                                    compared=len(comparison_pool),
                                    provenance=provenance))
    if best.subject is None and (
        best.governing_clause is not None
        or (best.predicate_head is not None
            and governing_state == "GOVERNING_CLAUSE_UNIQUE")
    ):
        # §10 terminal behaviour, decided by transported structure.
        #
        # V581 M2'.  The second disjunct is the coupled half of M2.  The branch
        # used to require that the LOCAL candidate generator had also proposed a
        # governing clause, which made a structural fact conditional on a
        # text-window guess — the same category error M2 repairs one level up.
        # A span that predicates something, binds no subject of its own, and is
        # reached by exactly one typed governing relation has its subject
        # supplied by that relation, whether or not a local heuristic noticed.
        if governing_state == "GOVERNING_CLAUSE_UNIQUE":
            return ("MULTIPLE_BINDINGS_RECOVERABLE", best,
                    "a single typed governing enactment reaches this span and "
                    "supplies the subject")
        if governing_state == "GOVERNING_CLAUSE_AMBIGUOUS":
            return ("AMBIGUOUS_BINDING", best,
                    "several governing clauses are materially plausible; the "
                    "structure does not decide between them")
        # Absent, or never supplied: the subject is named but recovered from
        # nothing.  That is PARTIAL, and the two cases are reported distinctly
        # so an unwired caller is never mistaken for an absent enactment.
        return ("INSUFFICIENT_ROLE_EVIDENCE", best,
                "the predicate is bound and the subject is the governing "
                "enactment clause, which "
                + ("no typed relation reaches"
                   if governing_state == "GOVERNING_CLAUSE_ABSENT"
                   else "the caller did not supply"))
    if best.subject is not None and best.predicate_head is not None:
        return ("UNIQUE_BINDING_ESTABLISHED", best,
                _unique_selection_sentence(
                    compared=len(comparison_pool), provenance=provenance,
                    active_relation_type=active_relation_type))
    return ("INSUFFICIENT_ROLE_EVIDENCE", best,
            "the strongest analysis leaves a required role unbound")


def _materially_different(left: Analysis, right: Analysis) -> bool:
    def key(a: Analysis):
        return (a.subject.span if a.subject else None,
                a.predicate_head.span if a.predicate_head else None)
    return key(left) != key(right)


__all__ = [
    "RoleBindingV2Violation", "INTERNAL_STATES", "SEPARATION_MARGIN",
    "UNDECIDED_ROLE", "validate_publication_role_invariant",
    "ROLE_TYPES", "ROLE_RELATION_TYPES", "Candidate", "RoleRelation",
    "Analysis", "script_of", "local_pronominal_subject_relations",
    "non_proposition_evidence", "hard_constraints", "resolve_internal",
    "build_lattice", "derive_obligations",
]


# ===========================================================================
# §18 adapter + §21 terminal safety policy
# ===========================================================================

@dataclass(frozen=True)
class RoleBindingV2Record(Record):
    """The external contract, produced only through the adapter below."""

    binding_id: str
    candidate_id: str
    internal_state: str
    subject_state: str
    subject_span: tuple[int, int] | None
    predicate_state: str
    predicate_span: tuple[int, int] | None
    predicate_head: str
    predicate_complement: str
    semantic_actor_state: str
    semantic_actor_span: tuple[int, int] | None
    attribution_state: str
    attribution_span: tuple[int, int] | None
    institutional_issuer_text: str
    antecedent_state: str
    governing_clause_source: str
    shared_subject_source: str
    shared_predicate_source: str
    required_context_ids: tuple[str, ...]
    proposition_status: str
    subject_completeness: str
    predicate_completeness: str
    binding_uniqueness: str
    required_context_status: str
    repairability: str
    role_binding_state: str
    terminal_derivation_rule: str
    repair_requirement: str | None
    final_extraction_disposition: str
    disposition_derivation_rule: str
    binding_confidence: float
    binding_reason: str
    language_or_script: str
    analyses_considered: int
    candidates_considered: int
    recorded_time: str


#: Subject states that REQUIRE a non-empty repair_requirement: recoverable from
#: declared context rather than bound here.
_RECOVERABLE_SUBJECT_STATES = frozenset({
    "IMPLICIT_CONTEXT_BOUND_SUBJECT", "ANAPHORIC_SUBJECT_RECOVERABLE",
    "INHERITED_COORDINATE_SUBJECT", "GOVERNING_CLAUSE_SUBJECT",
})

# Exact critical P4 authority: a record exposing any of these final role
# states has not established the corresponding role and may not be published.
# This vocabulary is intentionally independent of the terminal ontology's
# lawful ABSENT_BY_CONSTRUCTION state for genuine expletive/impersonal
# propositions.
UNDECIDED_ROLE = frozenset({
    "NO_SEMANTIC_PREDICATE",
    "NO_SEMANTIC_SUBJECT",
    "PREDICATE_UNRESOLVED",
    "SUBJECT_UNRESOLVED",
})


def validate_publication_role_invariant(
    *,
    subject_state: str,
    predicate_state: str,
    final_extraction_disposition: str,
) -> None:
    """Refuse publication when a final role remains exactly undecided.

    ``terminal.validate_decisions`` protects the terminal/disposition
    crosswalk.  This separate cross-field guard protects the role vocabulary
    after all M7--M15 rewrites and therefore catches a future caller that
    accidentally serializes a stale or otherwise drifted final role state.
    """
    if final_extraction_disposition != "EVIDENCE_BOUND":
        return
    offending = tuple(
        state for state in (subject_state, predicate_state)
        if state in UNDECIDED_ROLE
    )
    if offending:
        raise RoleBindingV2Violation(
            "EVIDENCE_BOUND cannot publish undecided role state(s): "
            + ", ".join(offending)
        )


def derive_obligations(
    *,
    subject_state: str,
    predicate_state: str,
    predicate_frame_inherited: bool = False,
    shared_subject_source: str | None = None,
    shared_predicate_source: str | None = None,
) -> tuple[tuple[str, ...], str | None]:
    """Derive the final role-context obligations in a stable order.

    Role states are rewritten by M7--M15 after the initial local reading has
    been selected.  This helper is deliberately the sole source of the
    external context/repair pair and therefore reads only those final states
    and the typed lineage names supplied by the caller.  Subject obligations
    precede predicate obligations; repeated context IDs and repair phrases are
    removed without changing first-seen order.

    V6.1 LRI-D1.  The two coordinate routes fell back to the literal
    ``COORDINATE_CONTEXT`` when the caller named no shared source.  That token
    is in no vocabulary this system declares: the frozen reference schema
    admits exactly LEAD_IN, LEFT_CONTEXT, HEADING and SOURCE_PUBLISHER for
    ``required_context_ids``, and production never supplies a shared source, so
    every coordinate unit emitted it.  The declared value is LEFT_CONTEXT, on
    this module's own semantics and for reasons that are independent of any
    measurement -- LEAD_IN and LEFT_CONTEXT are indistinguishable to every gate,
    because ``required_context_ids`` is not one of the six D39 fact axes and no
    member of it except SOURCE_PUBLISHER enters the required-context ladder:

      * ``_governing_clause_source`` reserves LEAD_IN for a governing region
        that was actually resolved and found in ``list_lead_in_ids``, and
        already uses LEFT_CONTEXT as its answer when no region was resolved.
        This fallback fires precisely when no source was resolved, so LEAD_IN
        would assert a declared list lead-in that nothing identified;
      * the adjacent PREDICATE_RECOVERABLE branch, which owes the same kind of
        external frame, already falls back to LEFT_CONTEXT;
      * M13d, the only rule in this module that installs
        INHERITED_COORDINATE_SUBJECT, inherits from THE PRECEDING CONJUNCT of a
        coordinator-initial span -- preceding text, which is what LEFT_CONTEXT
        names.  The repair phrase's prose "coordinating lead-in" describes the
        antecedent conjunct, not the typed LEAD_IN region.

    Widening the vocabulary to admit COORDINATE_CONTEXT was the alternative and
    is refused: the schema is frozen, and the audit that caught this exists to
    stop the code from naming its own vocabulary.
    """

    context_ids: list[str] = []
    repairs: list[str] = []

    def add(context_id: str | None, repair: str | None) -> None:
        if context_id and context_id not in context_ids:
            context_ids.append(context_id)
        if repair and repair not in repairs:
            repairs.append(repair)

    if subject_state == "GOVERNING_CLAUSE_SUBJECT":
        add(
            "LEFT_CONTEXT",
            "bind the subject from the governing enactment clause",
        )
    elif subject_state == "IMPLICIT_CONTEXT_BOUND_SUBJECT":
        add(
            "SOURCE_PUBLISHER",
            "recover the acting body from the declared publishing context",
        )
    elif subject_state == "ANAPHORIC_SUBJECT_RECOVERABLE":
        add(
            "LEFT_CONTEXT",
            "resolve the anaphor against the bounded left context",
        )
    elif subject_state == "INHERITED_COORDINATE_SUBJECT":
        add(
            shared_subject_source or "LEFT_CONTEXT",
            "inherit the subject from the coordinating lead-in",
        )

    if predicate_state == "PREDICATE_RECOVERABLE":
        add(
            shared_predicate_source or "LEFT_CONTEXT",
            "recover the predicate from the declared governing context",
        )
    elif predicate_state == "SHARED_COORDINATE_PREDICATE":
        add(
            shared_predicate_source or "LEFT_CONTEXT",
            "bind the predicate from the coordinating lead-in",
        )

    # A transported frame may retain local predicate text while its semantic
    # frame comes from the governor.  The state name alone cannot distinguish
    # that case from an ordinary local predicate, so M7 records this fact and
    # the final adapter consumes it here.
    if predicate_frame_inherited:
        add(
            shared_predicate_source or "LEFT_CONTEXT",
            "recover the predicate frame from the governing enactment clause",
        )

    return tuple(context_ids), "; ".join(repairs) if repairs else None


def _m2_binding_reason(
    facts: TM.TerminalFacts,
    predicate_span: tuple[int, int] | None,
) -> str:
    """Phrase M2's finding about the roles the record FINALLY emits.

    M2 records WHY it installs MULTIPLE_BINDINGS_RECOVERABLE, and that is a
    claim about a subject and a predicate.  M2 itself runs before M7--M15
    rewrite the role states, so phrasing the claim there published a sentence
    the record's own later fields contradicted: a span that M13b rewrites to
    EXPLETIVE_OR_IMPERSONAL_CONSTRUCTION emits subject_completeness=
    ABSENT_BY_CONSTRUCTION beside a reason asserting a recoverable subject, and
    a span whose named recovery is not proven emits REPAIR_NAMED_NOT_PROVEN
    beside a reason asserting recovery as fact.  Both were shipped.

    The claim is therefore phrased HERE, from the final D39 axes and the final
    emitted predicate span, so every clause is a statement the record's own
    other fields support.  This does not recompute the internal state: that
    value is the SELECTION-layer verdict by design (§18) and is deliberately
    left as the selector returned it.  Only the sentence that describes the
    final roles is derived from the final roles.
    """

    subject = facts.subject_completeness
    if subject == "ABSENT_BY_CONSTRUCTION":
        subject_claim = "the construction binds no subject"
    elif subject == "UNRESOLVED":
        subject_claim = "the subject is not resolved"
    elif subject == "RECOVERABLE_BOUNDED":
        # "is recoverable" asserts recovery as established.  The record says so
        # only when the repair it names is proven; otherwise it says the source
        # is named and the recovery is not.
        subject_claim = (
            "the subject is recoverable from context the caller supplies"
            if facts.repairability == "BOUNDED_REPAIR_PROVEN"
            else "the subject is named to context the caller supplies but "
                 "its recovery is not proven"
        )
    else:
        subject_claim = "the subject is bound"

    predicate = facts.predicate_completeness
    if predicate == "ABSENT_BY_CONSTRUCTION":
        predicate_claim = "the construction carries no predicate"
    elif predicate == "UNRESOLVED":
        predicate_claim = "the predicate is not resolved"
    elif predicate_span is not None:
        # A transported FRAME does not remove the local span: the V7 correction
        # that governing-clause rows may carry local predicate words is exactly
        # why this clause reads the emitted span rather than the frame axis.
        predicate_claim = "the predicate is bound in span"
    else:
        predicate_claim = "the predicate is bound to the declared context"

    return f"{subject_claim}; {predicate_claim}"


def _terminal_facts(
    *,
    internal_state: str,
    subject_state: str,
    subject_span: tuple[int, int] | None,
    predicate_state: str,
    predicate_span: tuple[int, int] | None,
    antecedent_state: str,
    required_context_ids: Sequence[str],
    repair_requirement: str | None,
    structural_context_supplied: bool,
    governing_state: str,
    predicate_frame_inherited: bool = False,
    subject_absence_is_evidential: bool = False,
    predicate_absence_is_evidential: bool = False,
    predicate_local_predication_is_evidential: bool = False,
) -> TM.TerminalFacts:
    """Serialize selected semantics into the frozen D39 typed axes.

    This adapter is intentionally declarative.  It does not consult confidence,
    source wording, unit identity, reference rows, or evaluator outcomes.

    V6.1 LRI-R1.  The absent-vs-unresolved axis is decided by the EVIDENCE the
    role-state pipeline actually had, not by which code path produced the state
    NAME.  ``NO_SEMANTIC_SUBJECT`` and ``NO_SEMANTIC_PREDICATE`` are each
    reachable from two unrelated provenances: M8's positive evidence of absence
    (no local predication and a structural verdict that no governing clause
    exists), and a span-closure removal (M14 S3 / M15 P0) that says only that
    the constituent it was handed is not a role.  The first licenses
    ABSENT_BY_CONSTRUCTION; the second licenses UNRESOLVED and nothing more.
    The caller therefore reports WHY, in the two flags below, and both axes are
    mapped by the IDENTICAL rule -- the asymmetry that mapped
    NO_SEMANTIC_SUBJECT to UNRESOLVED while NO_SEMANTIC_PREDICATE stayed
    ABSENT_BY_CONSTRUCTION emitted, from one span and one body of evidence, a
    record saying the subject exists and was not found beside a predicate that
    does not exist at all.  EXPLETIVE_OR_IMPERSONAL_CONSTRUCTION is untouched:
    it is a positive typing of a subjectless construction, not an absence.
    """

    antecedent_absent = antecedent_state == "ANTECEDENT_ABSENT"
    if internal_state == "INVALID_BINDING_CANDIDATE":
        proposition_status = "NO_SEMANTIC_PROPOSITION"
    elif internal_state == "INSUFFICIENT_ROLE_EVIDENCE" or antecedent_absent:
        proposition_status = "PROPOSITION_INDETERMINATE"
    else:
        proposition_status = "PROPOSITION"

    if subject_state == "EXPLETIVE_OR_IMPERSONAL_CONSTRUCTION":
        subject_completeness = "ABSENT_BY_CONSTRUCTION"
    elif subject_state == "NO_SEMANTIC_SUBJECT" and subject_absence_is_evidential:
        subject_completeness = "ABSENT_BY_CONSTRUCTION"
    elif subject_state in {"NO_SEMANTIC_SUBJECT", "SUBJECT_UNRESOLVED"} \
            or antecedent_absent:
        subject_completeness = "UNRESOLVED"
    elif subject_state in _RECOVERABLE_SUBJECT_STATES:
        # A materially ambiguous selected relation does not establish which
        # context-dependent subject is inherited.  Likewise, an explicitly
        # absent or untransported governor leaves that required role unresolved,
        # not recoverable from a merely named source.
        subject_completeness = (
            "UNRESOLVED"
            if internal_state in {
                "AMBIGUOUS_BINDING", "INSUFFICIENT_ROLE_EVIDENCE",
            }
            else "RECOVERABLE_BOUNDED"
        )
    else:
        subject_completeness = "BOUND_LOCAL"

    if predicate_state == "NO_SEMANTIC_PREDICATE":
        # The IDENTICAL rule as the subject axis above, which is the whole of
        # LRI-R1: one construct, applied symmetrically, so a single body of
        # evidence can no longer answer the two axes differently.
        predicate_completeness = (
            "ABSENT_BY_CONSTRUCTION" if predicate_absence_is_evidential
            else "UNRESOLVED"
        )
    elif predicate_state == "PREDICATE_UNRESOLVED":
        predicate_completeness = "UNRESOLVED"
    elif predicate_state in {
        "SHARED_COORDINATE_PREDICATE", "PREDICATE_RECOVERABLE",
    } or predicate_frame_inherited:
        # V581 M4.  Completeness tracks where the FRAME comes from, which is a
        # different axis from what kind of predicate this is.  A bare-infinitive
        # directive item is named GOVERNING_CLAUSE_PREDICATE because its frame is
        # its governor's, and that is precisely why it is RECOVERABLE_BOUNDED
        # rather than BOUND_LOCAL.  Renaming the state must not silently move the
        # completeness axis; the frozen D39 fact vector is the arbiter and it
        # requires RECOVERABLE_BOUNDED here.
        predicate_completeness = "RECOVERABLE_BOUNDED"
    elif predicate_state == "GOVERNING_CLAUSE_PREDICATE":
        # GOVERNING_CLAUSE_PREDICATE is the locally spanned action whose
        # subject comes from its governor; it is not itself transported.
        #
        # V6.1 T1-R1.  The SAME construct LRI-R1 installed on the absence axis,
        # applied to the bound-vs-unresolved decision that LRI-R1 did not
        # cover.  This branch decided BOUND_LOCAL from the existence of a span
        # alone -- the sole evidence for "bound LOCALLY" was that some span had
        # been emitted.  A mid-sentence continuation fragment whose only
        # predicate-shaped material is an infinitival adjunct, whose frame sits
        # in the left context, therefore published predicate_completeness
        # BOUND_LOCAL, which is precisely what a transported frame is NOT; the
        # terminal ladder then read one resolved and one unresolved role and
        # returned ROLE_BINDING_PARTIAL against both truth artifacts.
        #
        # The axis is now decided by the EVIDENCE the pipeline already holds:
        # ``clauses.detect`` establishes predication on this span, or it does
        # not.  ``FiniteClauseEvidence.establishes`` is the detector's OWN
        # establishment test -- a finite, modal-or-deontic, copular, zero-copula
        # nominal, short-form participial, passive-or-impersonal-legal or
        # legal-formula head resolved INSIDE the span -- and it is used whole,
        # not as a subset selected after seeing which units it moves.  Its
        # complement is the reading the design names: FINITE_CLAUSE_RECOVERABLE
        # is exactly a dependent non-finite adjunct whose governing predicate
        # sits in bounded context, and NOT_A_FINITE_CLAUSE is a span that
        # predicates in no dimension at all.  Neither is bound LOCALLY.
        #
        # The caller reports WHY through an explicit flag, in the same shape as
        # ``subject_absence_is_evidential`` and
        # ``predicate_absence_is_evidential``, so the axis is answered by
        # recorded evidence rather than inferred from a state name.  No score,
        # threshold, denominator, ruler, truth artifact or disposition rule is
        # involved, and every other predicate state keeps the span test below.
        predicate_completeness = (
            "BOUND_LOCAL"
            if predicate_span is not None
            and predicate_local_predication_is_evidential
            else "UNRESOLVED"
        )
    else:
        predicate_completeness = (
            "BOUND_LOCAL" if predicate_span is not None else "UNRESOLVED"
        )

    if internal_state in {
        "INVALID_BINDING_CANDIDATE", "INSUFFICIENT_ROLE_EVIDENCE",
    } or antecedent_absent:
        binding_uniqueness = "NONE"
    elif internal_state == "AMBIGUOUS_BINDING":
        binding_uniqueness = "MATERIAL_AMBIGUITY"
    else:
        binding_uniqueness = "UNIQUE"

    if not required_context_ids:
        required_context_status = "NONE"
    elif not structural_context_supplied:
        required_context_status = "NOT_TRANSPORTED"
    elif antecedent_absent:
        required_context_status = "ABSENT"
    elif (
        governing_state == "GOVERNING_CLAUSE_UNIQUE"
        or antecedent_state == "ANTECEDENT_RECOVERABLE"
        or "SOURCE_PUBLISHER" in required_context_ids
    ):
        required_context_status = "AVAILABLE_AND_UNIQUE"
    else:
        required_context_status = "AVAILABLE_AMBIGUOUS"

    # A transported role rewrite must not make a source that was already
    # rejected as non-propositional repairable merely by naming context.  The
    # obligation remains auditable on the record, while the invalid internal
    # analysis stays irreparable and therefore REJECTED.
    # V6.1 LRI-D2 WAS IMPLEMENTED, MEASURED AND REFUSED HERE; this ladder is
    # deliberately unchanged.  The designed repair replaced the internal-state
    # key below with "no obligation is recorded" so that a record owing nothing
    # could not be called IRREPARABLE.  Executed over the 120-unit cohort it
    # moved repairability on 34 records, not the 1 it was written for: 30
    # AMBIGUOUS_BINDING and 3 INSUFFICIENT_ROLE_EVIDENCE records went to
    # NO_REPAIR_OWED.  For those the internal state is not noise -- a material
    # ambiguity, or a governing enactment no typed relation reaches, is an
    # outstanding defect that NO context the caller supplies can repair, which
    # is what IRREPARABLE means.  "Owes nothing" is not "nothing is wrong".
    # The independent D39 panel records IRREPARABLE for 33 of the 34, and the
    # repair falsified LRI-R1's pre-registered prediction (D39 six-axis
    # exactness 87/120 -> 61/120, and 61/61 -> 47/61 inside the stratum where
    # the reference is about the current object).  The incoherence it targeted
    # is real and stays open: see v6_1_lri_repair/LRI_D2_REFUSAL.json, which
    # also shows the repair is not separable from a publication decision.
    if internal_state == "INVALID_BINDING_CANDIDATE":
        repairability = "IRREPARABLE"
    elif repair_requirement is None:
        repairability = (
            "NO_REPAIR_OWED"
            if internal_state == "UNIQUE_BINDING_ESTABLISHED"
            else "IRREPARABLE"
        )
    elif (
        required_context_status == "AVAILABLE_AND_UNIQUE"
        and binding_uniqueness == "UNIQUE"
    ):
        repairability = "BOUNDED_REPAIR_PROVEN"
    else:
        repairability = "REPAIR_NAMED_NOT_PROVEN"

    facts = TM.TerminalFacts(
        proposition_status=proposition_status,
        subject_completeness=subject_completeness,
        predicate_completeness=predicate_completeness,
        binding_uniqueness=binding_uniqueness,
        required_context_status=required_context_status,
        repairability=repairability,
    )
    facts.validate()
    return facts


#: V581E M9 (M9_CONTRACT.md).  Middle-voice / active-modal evidence that
#: defeats a PASSIVE typing of the selected head.  Grammar-derived and
#: surface-observable; reads neither truth nor unit identity.
_RU_REFLEXIVE_END = re.compile(r"(ся|сь)$", re.IGNORECASE)
_RU_PAST_REFLEXIVE_END = re.compile(r"(лся|лась|лось|лись)$", re.IGNORECASE)
_RU_MIDDLE_DATIVE_ADJ = re.compile(r"\w{3,}(ому|ему)$", re.IGNORECASE)
_RU_COMPARATIVE_ADVERB = re.compile(r"^(более|менее)$", re.IGNORECASE)
_AR_DIACRITIC = re.compile(r"[ً-ْٰ]")
_M9_TOKEN_TRIM = ".,;:()«»\"'—–"


#: V581F M10 (M10_CONTRACT.md).  Grammar-derived closed copula paradigms.
#: A copular finite head linking a bound semantic subject to a predicative
#: complement is a NOMINAL predication, not a lexical finite one.  Classes
#: not exercised by any cohort construct (ru, en) are deliberately absent —
#: recorded in the contract, not hidden.
_M10_COPULA_CLASSES = {
    "de": frozenset({"ist", "sind", "war", "waren", "sei", "seien"}),
    "es": frozenset({"es", "son", "era", "eran", "fue", "fueron"}),
    "it": frozenset({"è", "sono", "era", "erano"}),
    "fr": frozenset({"est", "sont", "était", "étaient"}),
}
_M10_SEMANTIC_SUBJECTS = frozenset({
    "EXPLICIT_SUBJECT", "ANAPHORIC_SUBJECT_RECOVERABLE", "POSTVERBAL_SUBJECT",
    "ZERO_COPULA_NOMINAL_SUBJECT", "INHERITED_COORDINATE_SUBJECT",
    "PASSIVE_PATIENT_SUBJECT",
})

#: V581G M11 (M11_CONTRACT.md).  Grammar-derived closed deontic modal
#: paradigms.  A closed-class modal heading the predication and governing a
#: complement is ONE deontic predicate, whatever the voice of its
#: complement.  en will/would/can excluded (future/ability/epistemic);
#: ru/ar/es/it modals are already candidate-typed DEONTIC — no gap there.
_M11_MODAL_CLASSES = {
    "en": frozenset({"shall", "must", "may"}),
    "fr": frozenset({"peut", "peuvent", "doit", "doivent"}),
    "de": frozenset({"kann", "können", "muss", "müssen", "darf", "dürfen",
                     "soll", "sollen"}),
}

#: V581H M12 (M12_CONTRACT.md).  A German participle immediately before the
#: passive auxiliary: the processual werden-passive and the stative
#: sein-passive under a modal.  An active infinitive chain ("erhalten
#: sollen") cannot match.
_M12_DE_PASSIVE_INF = re.compile(r"\w+(t|en)\s+(werden|sein)\b")

#: V581I M13 (M13_CONTRACT.md).  The Arabic existential particles.
_M13_AR_EXISTENTIALS = frozenset({"هناك", "هنالك", "ثمة"})

#: V581J M14 (M14_CONTRACT.md) — subject-span closure.  A declared
#: enumeration marker, a comma-bounded adjunct and an independent
#: following clause are not part of the grammatical subject constituent;
#: an attributive modifier, an indispensable nominal complement and an
#: Arabic definite continuation are.
_M14_MARKER = re.compile(r"^\s*(?:\(\d{1,3}\)|\d{1,3}\.|[^\W\d_]\))\s+")
_M14_NO_LOCAL_SUBJECT_STATES = frozenset({
    "NO_SEMANTIC_SUBJECT", "EXPLETIVE_OR_IMPERSONAL_CONSTRUCTION",
    "SUBJECT_UNRESOLVED", "GOVERNING_CLAUSE_SUBJECT",
    "INHERITED_COORDINATE_SUBJECT"})
#: Closed-class adjunct prepositions of the cohort's Latin-script
#: languages.  Kept separate from _LAT_PREPOSITION, which the candidate
#: generator reads: M14 runs post-terminal and may not perturb the lattice.
_M14_ADJUNCT_PREPOSITION = re.compile(
    r"^(con|sin|para|por|segun|según|durante|mediante|sobre|ante|tras|"
    r"avec|sans|pour|par|selon|apres|après|avant|dans|sous|"
    r"mit|ohne|für|fuer|durch|gemäß|gemaess|laut|nach|bei|"
    r"senza|secondo|tra|fra|presso|"
    r"with|without|according|under|over|from|upon)$", re.IGNORECASE)
_M14_RU_ATTRIBUTIVE = re.compile(r"^\w{4,}(ый|ий|ой|ая|яя|ое|ее|ые|ие)$",
                                 re.IGNORECASE)
_M14_EN_QUANTIFIER = re.compile(
    r"^(none|all|any|some|each|part|neither|either|both|most)$", re.IGNORECASE)
#: An Arabic token opening a new coordinated clause: wa- + imperfect verb.
_M14_AR_COORDINATED_VERB = re.compile(r"\s(و[يتنأ][ء-ٟ]{2,})")
#: A definite continuation of an Arabic nominal constituent.
_M14_AR_DEFINITE_CONTINUATION = re.compile(r"^(?:ال|لل|بال|وال|فال|كال)")
_M14_SPAN_TRIM = ".,;:()«»„“\"'—– \t\n"


#: V581K M15 (M15_CONTRACT.md) — predicate-span closure.
_M15_AR_NEGATION = re.compile(r"^[وف]?(?:لا|لم|لن|ما)$")
_M15_AR_CLAUSE_OPENER = re.compile(r"^و\S")
_M15_RU_CLAUSE_OPENER = re.compile(
    r"^(что|который|которые|которая|которое|а|но|и)$", re.IGNORECASE)
#: States whose local words are contractually NOT an independent-clause
#: boundary question: a recital carries its governed proposition, and a
#: governing-clause row's local words are the item's action phrase.
_M15_NO_CLAUSE_CUT = frozenset({"RECITAL_RELATION_PREDICATE",
                                "GOVERNING_CLAUSE_PREDICATE"})


def _m14_tokens(body: str):
    return [(m.group(0), m.start(), m.end()) for m in re.finditer(r"\S+", body)]


def _m15_trim(body: str, start: int, stop: int) -> tuple[int, int]:
    while start < stop and body[start] in _M14_SPAN_TRIM + "؛،":
        start += 1
    while stop > start and body[stop - 1] in _M14_SPAN_TRIM + "؛،":
        stop -= 1
    return start, stop


def _m15_words(body: str, start: int, stop: int):
    return collections.Counter(
        m.group(0).casefold() for m in re.finditer(r"\w+", body[start:stop]))


def _m15_predicate_span(body, span, state, subject_span, head_span,
                        complement_span, script):
    """The frozen M15 predicate-span mechanism (M15_CONTRACT.md §3)."""
    if span is None:
        return span, state
    start, stop = span
    word_tokens = _m14_tokens(body)
    fired = False

    # P0 — a nominal governed by an immediately preceding preposition is
    # not a predicate head.
    if head_span is not None:
        before = [t for t in word_tokens if t[2] <= head_span[0]]
        if before and before[-1][2] >= head_span[0] - 1:
            previous = before[-1][0].strip(_M14_SPAN_TRIM)
            if (_RU_PREPOSITION.match(previous)
                    or _LAT_PREPOSITION.match(previous)) \
                    and state in ("PARTICIPIAL_PREDICATE",
                                  "EXPLICIT_FINITE_PREDICATE") \
                    and head_span[1] - head_span[0] == stop - start:
                return None, "NO_SEMANTIC_PREDICATE"

    subject_precedes = (subject_span is not None and head_span is not None
                        and subject_span[1] <= head_span[0])

    # P1 — begin where the local subject ends, never crossing a comma.
    if subject_precedes and start > subject_span[1]:
        floor = subject_span[1]
        for match in re.finditer(r"[,،;؛]", body[subject_span[1]:start]):
            floor = subject_span[1] + match.end()
        if floor < start:
            start = floor
            fired = True

    # P2 — an attached connective and a preceding negation belong to the
    # predicate (Arabic, subject not preceding).
    if script == "ARABIC" and not subject_precedes:
        moved = True
        while moved and start > 0:
            moved = False
            if body[start - 1] in "وف" and (start - 1 == 0
                                            or body[start - 2].isspace()):
                start -= 1
                moved = fired = True
                continue
            before = [t for t in word_tokens if t[2] <= start]
            if before and before[-1][2] >= start - 1 \
                    and _M15_AR_NEGATION.match(
                        before[-1][0].strip(_M14_SPAN_TRIM)):
                start = before[-1][1]
                moved = fired = True

    # P3 — a fronted oblique frame belongs to an impersonal predicate.
    if not subject_precedes and head_span is not None:
        boundary = 0
        for match in re.finditer(r"[,،;؛]", body[:head_span[0]]):
            boundary = match.end()
        lead_start, lead_stop = _m15_trim(body, boundary, start)
        lead = body[lead_start:lead_stop]
        # P6.4 F-1.  `_m15_trim` removes only the characters of the LITERAL
        # set `_M14_SPAN_TRIM`, whose whitespace is exactly {" ", "\t", "\n"}.
        # A lead composed solely of whitespace OUTSIDE that set -- all
        # TWENTY-SIX characters `str.isspace()` accepts and the trim does not
        # remove: VT, FF, CR, U+001C-U+001F, U+0085 NEL, U+00A0 NBSP, U+1680,
        # U+2000-U+200A, U+2028, U+2029, U+202F, U+205F, U+3000 -- therefore
        # survives the trim and is TRUTHY, while
        # `str.split()` (which splits on `str.isspace()`) returns [].  The
        # old `lead and ...` test licensed `[0]` on that empty list, and the
        # `IndexError` escaped `bind` entirely: no record at all, lawful or
        # refusing.  Reachable on real text -- `v5_4/invariants.py` forwards
        # `ctx.selected_text or ctx.seed_text or ctx.candidate.raw_span`
        # verbatim, that slice is raw `document.text`, and the only Unicode
        # normalisation in the tree is NFC, which preserves every one of
        # those characters (NBSP is `&nbsp;`, CR is CRLF).
        #
        # A lead with no word token carries no fronted oblique frame, exactly
        # as an ABSENT lead carries none, so P3 does not fire -- the
        # destination the empty-lead case has always taken.  The repair tests
        # the WORD LIST instead of the string.  It is NOT a widening of
        # `_M14_SPAN_TRIM`: that set also sets the emitted boundary through
        # `_m15_trim` at the bottom of this function, so widening it would
        # MOVE published spans, and this repair moves none.
        lead_words = lead.split()
        if lead_words and lead_stop <= head_span[0]:
            first = lead_words[0].strip(_M14_SPAN_TRIM)
            if _RU_PREPOSITION.match(first) or _AR_PREP.match(first) \
                    or _LAT_PREPOSITION.match(first):
                start = lead_start
                fired = True

    # P4 — the predicate does not absorb a postverbal local subject.
    if subject_span is not None and head_span is not None \
            and subject_span[0] >= head_span[1] \
            and start < subject_span[0] < stop:
        stop = subject_span[0]
        fired = True

    # P5 — the predicate excludes an independent following clause.
    if state not in _M15_NO_CLAUSE_CUT:
        for match in re.finditer(r"[,،]\s*(\S+)", body[start:stop]):
            nxt = match.group(1)
            if (script == "ARABIC" and _M15_AR_CLAUSE_OPENER.match(nxt)) or \
               (script == "CYRILLIC"
                    and _M15_RU_CLAUSE_OPENER.match(
                        nxt.strip(_M14_SPAN_TRIM))):
                if start + match.start() > start:
                    stop = start + match.start()
                    fired = True
                break

    # P6 — the predicate carries its governed local complement.
    if complement_span is not None and head_span is not None \
            and (start, stop) == tuple(head_span) \
            and complement_span[0] >= head_span[1] \
            and complement_span[1] > stop:
        stop = complement_span[1]
        fired = True

    if fired:
        start, stop = _m15_trim(body, start, stop)
    if stop <= start:
        return span, state
    # A boundary moves only when the constituent's word content changes.
    if _m15_words(body, start, stop) == _m15_words(body, span[0], span[1]):
        return span, state
    return (start, stop), state


def _m14_subject_span(body, span, state, predicate_span, head_span,
                      candidates, script):
    """The frozen M14 subject-span mechanism (M14_CONTRACT.md §3)."""
    if span is None:
        return span, state
    start, stop = span

    # S1 — a declared enumeration marker is not part of the subject.
    if body[:start].strip() == "":
        marker = _M14_MARKER.match(body[start:stop])
        if marker:
            start += marker.end()

    bare = body[start:stop].strip(_M14_SPAN_TRIM)

    # S3 — a bare preposition is not a subject constituent.
    if bare and len(bare.split()) == 1 and (
            _RU_PREPOSITION.match(bare) or _AR_PREP.match(bare)
            or _LAT_PREPOSITION.match(bare)):
        return None, "NO_SEMANTIC_SUBJECT"

    # S4 — a token the lattice itself types as a predicate head is not a
    # subject; under German V2 the main clause's subject follows the
    # SELECTED finite verb, not a deeper subordinate one.
    head_spans = {tuple(c.span) for c in candidates
                  if c.role_type == "PREDICATE_HEAD" and c.span is not None}
    if (start, stop) in head_spans:
        postverbal = [c for c in candidates
                      if c.role_type == "SUBJECT"
                      and c.origin_rule == "LAT_POSTVERBAL_V2"
                      and c.span is not None and head_span is not None
                      and c.span[0] >= head_span[1]]
        if postverbal:
            return tuple(min(postverbal, key=lambda c: c.span[0]).span), state
        return None, "SUBJECT_UNRESOLVED"

    # S2 — a state that asserts no local subject carries no local span.
    if state in _M14_NO_LOCAL_SUBJECT_STATES:
        return None, state

    # S5 — the subject was extended over the predicate head when it
    # terminates exactly at that head's end.
    if head_span is not None and start < head_span[0] and stop == head_span[1]:
        stop = head_span[0]

    # S6 — a comma-bounded adjunct is not part of the subject.
    for match in re.finditer(r"[,،]\s+(\S+)", body[start:stop]):
        if _M14_ADJUNCT_PREPOSITION.match(
                match.group(1).strip(_M14_SPAN_TRIM)):
            stop = start + match.start()
            break

    # S7 — an attributive modifier immediately preceding the head noun.
    if script == "CYRILLIC":
        before = [t for t in _m14_tokens(body) if t[2] <= start]
        if before and _M14_RU_ATTRIBUTIVE.match(
                before[-1][0].strip(_M14_SPAN_TRIM)) \
                and before[-1][2] >= start - 1:
            start = before[-1][1]

    if script == "ARABIC":
        # S10 — no independent following clause.  Runs before S9 so the
        # truncated span is not re-extended over the next clause.
        coordinated = _M14_AR_COORDINATED_VERB.search(body[start:stop])
        if coordinated:
            stop = start + coordinated.start()
        # S9 — Arabic definite-NP extent.
        while True:
            rest = [t for t in _m14_tokens(body) if t[1] >= stop]
            if not rest:
                break
            token, _t_start, t_stop = rest[0]
            if token.startswith("("):
                close = body.find(")", _t_start)
                if close < 0:
                    break
                stop = close + 1
                continue
            if _M14_AR_DEFINITE_CONTINUATION.match(token):
                stop = t_stop
                continue
            break

    # S8 — an indispensable nominal complement of a bare quantifier.
    if _M14_EN_QUANTIFIER.match(body[start:stop].strip(_M14_SPAN_TRIM)):
        after = [t for t in _m14_tokens(body) if t[1] >= stop]
        if after and after[0][0].strip(_M14_SPAN_TRIM).casefold() == "of":
            limit = (predicate_span[0] if predicate_span is not None
                     else len(body))
            if limit > stop:
                stop = limit

    return (start, stop), state


def _m13_bare_arabic(token: str) -> str:
    out = _AR_DIACRITIC.sub("", token)
    if len(out) >= 4 and _AR_PROCLITIC.match(out):
        out = out[1:]
    return out


def _middle_voice_not_passive(body: str, head) -> bool:
    """M9: does the selected PASSIVE head actually show middle voice or an
    active Arabic modal?

    R1 — a -ся head governing a bare unambiguous-dative NP is a lexical
    middle (подвергаться кому/чему): the reflexive passive assigns no bare
    dative; its only bare-case dependent is an instrumental agent.
    R2 — a past -ся head with no bare instrumental agent is a lexical
    reflexive: the productive reflexive passive of this register is present
    imperfective.
    R3 — a -ся head whose next token is между is a reciprocal middle; the
    между-phrase names co-participants, not a demoted agent.
    A1 — يمكن + أن is the form-IV ACTIVE possibility modal; the damma that
    the passive pattern keys on is the form-IV subject-prefix vowel.
    """
    if head is None or head.span is None:
        return False
    head_text = head.text or ""
    following = []
    for match in re.finditer(r"\S+", body[head.span[1]:]):
        token = match.group(0).strip(_M9_TOKEN_TRIM)
        if token:
            following.append(token)
    if _RU_REFLEXIVE_END.search(head_text):
        # R1: bare dative government -> lexical middle.
        for token in following:
            if _RU_PREPOSITION.match(token):
                break
            if _RU_COMPARATIVE_ADVERB.match(token):
                continue
            if _RU_MIDDLE_DATIVE_ADJ.search(token):
                return True
            break
        # R3: reciprocal middle.
        if following and following[0].lower() == "между":
            return True
        # R2: past reflexive without a bare instrumental agent.
        if _RU_PAST_REFLEXIVE_END.search(head_text):
            preposition_governed = False
            for token in following:
                if _RU_PREPOSITION.match(token):
                    preposition_governed = True
                    continue
                if not preposition_governed \
                        and _RU_INSTRUMENTAL_AGENT.search(token):
                    return False
                preposition_governed = False
            return True
    # A1: active form-IV possibility modal misread through its damma.
    bare = _AR_DIACRITIC.sub("", head_text)
    if len(bare) >= 4 and _AR_PROCLITIC.match(bare):
        bare = bare[1:]
    if bare == "يمكن" and following \
            and _AR_DIACRITIC.sub("", following[0]) == "أن":
        return True
    return False


#: V581L L1.  Region types under which structural list-governance evidence may
#: license governed-list candidate generation: positively-typed proposition
#: content, or the declared vocabulary's OWN name for a region whose typing
#: failed.  Positively-typed furniture (navigation, search, link labels, index
#: entries, breadcrumbs, headers, heading-only context) never licenses.
#:
#: P6.3 B2.  This is an ENUMERATION, not a predicate over "was the typing
#: successful", and the difference is not cosmetic.  `UNKNOWN_STRUCTURAL_
#: REGION` is admitted because it is a MEMBER of this set, not because it
#: denotes a typing failure; every OTHER way of failing to type a region --
#: `SIDEBAR_PROMOTION`, `UNKNOWN`, `unknown_structural_region`, the empty
#: string, or the same name with a stray space -- is outside the set and is
#: refused exactly as furniture is.  MEASURED on a governed Arabic list item
#: carrying list-governance evidence: 108 of 240 out-of-vocabulary points
#: diverge from the `UNKNOWN_STRUCTURAL_REGION` answer, moving up to 21
#: published fields including `internal_state`, `subject_state`,
#: `predicate_state` and the emitted predicate span -- while 0 of 40
#: in-licence points move.
_LIST_LICENSE_REGION_TYPES = frozenset({
    "PRIMARY_PROPOSITION_CONTENT",
    "UNKNOWN_STRUCTURAL_REGION",
})


def build_lattice(*, text: str, language: str | None = None,
                  left_context: str = "", heading: str = "",
                  content_region_type: str = "UNKNOWN_STRUCTURAL_REGION",
                  structural_context: "ST.StructuralClauseContext | None" = None,
                  clause_evidence: CL.FiniteClauseEvidence | None = None,
                  governed_list_item: bool | None = None
                  ) -> tuple[list[Candidate], list[Analysis], list[str], str]:
    """Build the exact production lattice without resolving or adapting it.

    Evaluation serializers use this entry point so the diagnostic path cannot
    silently diverge from ``bind``.  It reads source and typed structure only;
    references, labels and selector verdicts are not parameters.
    """
    language = _declared_language(language)
    body = (text or "")[:MAX_SCAN_CHARS]
    evidence = clause_evidence or CL.detect(body, language=language)
    script = evidence.language_or_script
    tokens = _tokens(body)
    signals = non_proposition_evidence(body, tokens, script)
    candidates: list[Candidate] = []
    candidates.extend(_recital_candidates(body, tokens, script))
    if not any(c.origin_rule.startswith("RECITAL") for c in candidates):
        if script == "CYRILLIC":
            candidates.extend(_cyrillic_candidates(body, tokens))
        elif script == "ARABIC":
            if governed_list_item is None:
                # V581L L1.  A positively-typed non-proposition region never
                # licenses typed-list governance.  Requiring
                # PRIMARY_PROPOSITION_CONTENT suppressed the governed-list
                # generators on governed LIST_ITEMs whose region typing came
                # back UNKNOWN_STRUCTURAL_REGION, so that ONE name for a failed
                # typing was added to the licence set and, for it, M5 list
                # membership and the M6/M7 governor evidence in the structural
                # context decide.
                #
                # P6.3 B2.  What this comment used to say -- "a typing FAILURE
                # is not a licence refusal" -- is FALSE as a general claim and
                # is withdrawn.  The test below is set MEMBERSHIP, so it admits
                # the one spelling the vocabulary declares and refuses every
                # other way a caller can fail to type a region; an
                # out-of-vocabulary string is treated exactly as furniture is.
                # See `_LIST_LICENSE_REGION_TYPES` for the measurement.
                #
                # This is not repaired here because it is UNREACHABLE from
                # production and repairing it would be a selector change:
                # `content_region_type` occurs ZERO times in
                # `v5_4/invariants.py`, so neither bind call site (:478, :565)
                # supplies it and production always takes this function's own
                # default, which IS in the set.  The register-bounded ground is
                # therefore the UNWIRED PARAMETER, and not -- as the acceptance
                # record's NARROW-1 stated -- that the capability is correct on
                # out-of-vocabulary input.  Widening the gate to "anything that
                # is not positively-typed furniture" would move published role
                # states and needs its own authorisation.
                governed_list_item = bool(
                    content_region_type in _LIST_LICENSE_REGION_TYPES
                    and structural_context is not None
                    and (
                        structural_context.list_lead_in_ids
                        or structural_context.governing_clause_candidates
                    )
                )
            candidates.extend(_arabic_candidates(
                body, tokens, governed_list_item=bool(governed_list_item)))
        else:
            candidates.extend(_latin_candidates(
                body, tokens, left_context=left_context))
    candidates.extend(_shared_candidates(body, tokens, left_context, heading))
    signals = _transport_constructional_predication(signals, candidates)
    analyses = _build_analyses(
        candidates, body, script, signals, language=language)
    return candidates, analyses, signals, script


def bind(*, candidate_id: str, text: str, language: str | None = None,
         left_context: str = "", heading: str = "",
         content_region_type: str = "UNKNOWN_STRUCTURAL_REGION",
         governing_clause_id: str | None = None,
         shared_subject_source: str | None = None,
         shared_predicate_source: str | None = None,
         structural_context: "ST.StructuralClauseContext | None" = None,
         clause_evidence: CL.FiniteClauseEvidence | None = None
         ) -> RoleBindingV2Record:
    """Bind roles for one span through candidates, analyses and constraints."""
    body = (text or "")[:MAX_SCAN_CHARS]
    # §9/§11.  Layout precedes syntax.  A span drawn from a manifestation whose
    # reading order could not be established is not a sentence the document
    # contains, and no amount of clause analysis can make it one.  The binder
    # refuses rather than compensating.
    if structural_context is not None:
        order_state = getattr(structural_context, "reading_order_state",
                              "READING_ORDER_NOT_APPLICABLE")
        if order_state in ("PACKET_CONSTRUCTION_DEFECT",
                           "LAYOUT_RECONSTRUCTION_REQUIRED"):
            return _layout_refusal(candidate_id, body, order_state, language)
    # P6.3 A2(b).  The declared tag is normalised AFTER the refusal, not
    # before it.  `_layout_refusal` never reads `language` -- it resolves zero
    # `language` names, which the P6.2 closure file pins syntactically and
    # behaviourally -- so normalising first bought the refusal nothing and, on
    # the P6.2 bytes, pre-empted it: a caller value that could not be
    # normalised destroyed the very route whose purpose is to refuse.  Layout
    # precedes syntax, and it now precedes the declared tag as well.
    language = _declared_language(language)
    # V6.1 T1-R1.  The clause evidence is resolved ONCE, here, on exactly the
    # arguments `build_lattice` would have used, and then handed to it -- so
    # the lattice and the terminal adapter answer from the same evidence object
    # rather than from two independent detections of the same span.
    #
    # The tag passed here is the ONCE-normalised one, so the P6.3 A3 ingress
    # pin -- exactly one `_declared_language` call per ingress -- is preserved.
    # That needs an argument, because `_declared_language` is NOT idempotent
    # (it strips, casefolds, then takes a TWO-CHARACTER prefix, so a prefix
    # ENDING in whitespace loses that character only on a second pass) and
    # `build_lattice` normalises its own argument again on entry.  The two
    # values are nonetheless INDISTINGUISHABLE TO `CL.detect`:
    #
    #   Let L1 be the once-normalised tag and L2 the twice-normalised one.
    #   len(L1) <= 2 and casefold is idempotent, so L2 is exactly L1 stripped;
    #   they differ ONLY when L1 contains whitespace, which -- at length <= 2 --
    #   leaves at most ONE non-whitespace character in either value.
    #   `CL.detect` reads the tag only through `declared = tag.lower()[:2]` and
    #   tests it against "ar", "ru" and LEXICON_COVERED {"en","de","fr","es"},
    #   every one of which is TWO non-whitespace characters.  So whenever L1
    #   and L2 differ, NEITHER can match ANY of those tests and both take the
    #   same branch.
    #
    # Measured as well as argued: an executable arm carrying the alternative
    # was swept over 10 bodies x 37 adversarial tags with ZERO differing cells.
    # See evidence/LANGUAGE_TAG_SWEEP.json and
    # test_the_two_normalisation_depths_are_indistinguishable_to_the_detector.
    predication_evidence = clause_evidence or CL.detect(body, language=language)
    candidates, analyses, signals, script = build_lattice(
        text=body,
        language=language,
        left_context=left_context,
        heading=heading,
        content_region_type=content_region_type,
        structural_context=structural_context,
        clause_evidence=predication_evidence,
    )
    # D32.  Whether a governing clause exists is a structural fact the document
    # carries, not something to infer from a text window.  When the caller
    # transports it we use it; when it does not, we say the structure is absent
    # rather than guessing from prose.
    if structural_context is not None:
        governing_state, governing_id, governing_reason = \
            structural_context.governing_resolution()
    else:
        governing_state, governing_id = "GOVERNING_CLAUSE_NOT_SUPPLIED", None
        governing_reason = "no structural context reached the binder"
    internal, analysis, reason = resolve_internal(
        analyses, governing_state=governing_state,
        content_region_type=content_region_type)
    if governing_state != "GOVERNING_CLAUSE_NOT_SUPPLIED":
        reason = f"{reason}; {governing_reason}"

    # --- role states -------------------------------------------------------
    # A masthead, a session line or a document symbol is furniture whatever
    # verb-shaped tokens it happens to contain.  Keying the non-proposition
    # decision on absent verbal evidence alone meant D33's lexical candidates
    # weakened it, because a rubric can carry a word ending in -s.
    _STRUCTURAL_NEGATIVES = frozenset({
        "ALL_CAPS_RUBRIC", "DATE_OR_SESSION_LINE", "DOCUMENT_SYMBOL_LINE",
        "EMPTY_SPAN"})
    hard_negative = (
        "NO_VERBAL_OR_PREDICATIVE_EVIDENCE" in signals
        or bool(_STRUCTURAL_NEGATIVES.intersection(signals))
        or content_region_type in STRUCTURAL_NON_PROPOSITION_REGIONS
    )
    predicate_frame_inherited = False
    # V6.1 LRI-R1.  WHY a role state is NO_SEMANTIC, carried to the terminal
    # adapter so the absent-vs-unresolved axis is decided by evidence rather
    # than by the state name.  True only where M8's frozen construct licenses
    # evidence of absence: no local predication AND the POSITIVE structural
    # verdict GOVERNING_CLAUSE_ABSENT.  GOVERNING_CLAUSE_NOT_SUPPLIED is
    # ignorance and GOVERNING_CLAUSE_AMBIGUOUS is a frame the structure could
    # not choose between -- in both there is something for a role to be
    # unresolved about, so neither licenses absence.  Every other route to a
    # NO_SEMANTIC state, in particular M14's and M15's span-closure removals,
    # leaves these False.
    subject_absence_is_evidential = False
    predicate_absence_is_evidential = False
    # V581 M2.  Whether M2's finding is what the record reports.  The finding
    # is a claim about the FINAL roles, so the sentence is built after the
    # M7--M15 rewrites, at the emission site.
    m2_reason_owed = False
    if hard_negative:
        subject_state, predicate_state = "NO_SEMANTIC_SUBJECT", "NO_SEMANTIC_PREDICATE"
        # M8's "stays" population: a structural negative with a structural
        # verdict that no governing clause exists has no frame anywhere.
        subject_absence_is_evidential = predicate_absence_is_evidential = (
            governing_state == "GOVERNING_CLAUSE_ABSENT")
        subject_span = predicate_span = None
        head_text = complement_text = ""
        internal = "INVALID_BINDING_CANDIDATE"
        reason = (
            _non_proposition_region_sentence(content_region_type)
            if content_region_type in STRUCTURAL_NON_PROPOSITION_REGIONS
            else "; ".join(signals)
        )
    elif analysis is None:
        subject_state, predicate_state = "SUBJECT_UNRESOLVED", "PREDICATE_UNRESOLVED"
        subject_span = predicate_span = None
        head_text = complement_text = ""
    else:
        head = analysis.predicate_head
        if analysis.governing_clause is not None:
            subject_state, subject_span = "GOVERNING_CLAUSE_SUBJECT", None
        elif analysis.subject is not None:
            subject_state = analysis.subject.subtype or "EXPLICIT_SUBJECT"
            subject_span = analysis.subject.span
        elif head is not None and governing_state == "GOVERNING_CLAUSE_UNIQUE":
            # V581 M2.  The span predicates something and binds no subject of its
            # own, and the frozen structural transport resolves exactly one
            # governor for it.  That subject is not "implicitly bound to context":
            # the context is identified, typed and named by region.  A span that
            # binds its own subject is excluded above, so a local subject always
            # outranks an inherited one and the coherence rule
            # GOVERNING_CLAUSE_SUBJECT_ALSO_BOUND_LOCALLY cannot be provoked here.
            subject_state, subject_span = "GOVERNING_CLAUSE_SUBJECT", None
        elif head is not None:
            subject_state, subject_span = "IMPLICIT_CONTEXT_BOUND_SUBJECT", None
        else:
            subject_state, subject_span = "SUBJECT_UNRESOLVED", None
        predicate_state = (head.subtype if head else "PREDICATE_UNRESOLVED")
        predicate_span = analysis.predicate_span()
        # V581 M4.  PREDICATE_RECOVERABLE is the subtype for a bare infinitive
        # opening a numbered directive item -- "(12) внедрять ...".  Its finite
        # frame is not missing; it is supplied by the lead-in that governs the
        # item.  That is this module's own definition of
        # GOVERNING_CLAUSE_PREDICATE: the locally spanned action whose frame
        # comes from its governor.  The span is already right; only the name was
        # wrong, and only where a governor is actually there to supply the frame.
        if predicate_state == "PREDICATE_RECOVERABLE" \
                and predicate_span is not None \
                and governing_state == "GOVERNING_CLAUSE_UNIQUE":
            predicate_state = "GOVERNING_CLAUSE_PREDICATE"
            predicate_frame_inherited = True
        head_text = head.text if head else ""
        complement_text = (analysis.predicate_complement.text
                           if analysis.predicate_complement else "")

    # V581 M2.  The same span, under the same evidence, reached this block as
    # IMPLICIT_CONTEXT_BOUND_SUBJECT before the transported governor was
    # consumed.  Naming the subject more precisely may not lower the assessed
    # quality of the binding: the evidence is strictly stronger, not weaker.
    # Restricted to the population M2 creates — an analysis that binds no
    # subject and carries no local governing candidate — so every span whose
    # governing clause the local generator already proposed keeps the internal
    # state resolve_internal gives it, unchanged.
    _transported_subject_only = (
        subject_state == "GOVERNING_CLAUSE_SUBJECT"
        and analysis is not None
        and analysis.subject is None
        and analysis.governing_clause is None
    )
    if (subject_state in ("ANAPHORIC_SUBJECT_RECOVERABLE",
                          "IMPLICIT_CONTEXT_BOUND_SUBJECT")
            or _transported_subject_only) \
            and predicate_state not in ("PREDICATE_UNRESOLVED",
                                        "NO_SEMANTIC_PREDICATE") \
            and internal != "INVALID_BINDING_CANDIDATE":
        internal = "MULTIPLE_BINDINGS_RECOVERABLE"
        m2_reason_owed = True

    # V581C M7.  Transported-role consumption (M7_CONTRACT.md, families
    # FA-FE).  Role states are consumed here, and the authoritative terminal
    # axes are derived only after this block and all later M8-M15 rewrites.
    # Consumption is lawful only under a UNIQUE transported governor, only
    # where the local generator proposed no governing clause of its own, and
    # only per edge-family semantics: an E4 continuation or E2 operative
    # clause carries its own frame, an E3 item's frame is its lead-in's, and
    # an E1 attachment to a non-recital span was admitted through the
    # frame-less fallback.  A standalone-coordinator-initial span continues a
    # COORDINATION and never inherits.
    if governing_state == "GOVERNING_CLAUSE_UNIQUE" and analysis is not None \
            and analysis.governing_clause is None \
            and structural_context is not None:
        _edges = frozenset(structural_context.governing_clause_relation_types)
        _e4 = "GRAMMATICAL_CONTINUATION_INHERITS_FRAME" in _edges
        _e3s = "LIST_ITEM_INHERITS_LEAD_IN_SUBJECT" in _edges
        _e3p = "LIST_ITEM_INHERITS_LEAD_IN_PREDICATE" in _edges
        _e2 = "OPERATIVE_CLAUSE_ISSUED_BY_ORGAN" in _edges
        _e1 = "RECITAL_GOVERNED_BY_ENACTMENT" in _edges
        _coordinator = bool(_COORDINATOR_INITIAL.match(body))
        _subject = analysis.subject
        _head = analysis.predicate_head
        _flip_subject = _flip_predicate = False
        if hard_negative:
            # FC: no local verbal evidence at all; a clause-level edge
            # supplies the whole frame.
            if (_e4 or _e3s or _e3p) and not _coordinator:
                _flip_subject = _flip_predicate = True
        elif _subject is None and _head is None:
            # FA: the span predicates nothing locally and binds nothing;
            # the continuation or list frame is the only frame there is.
            if (_e4 or _e3s) and not _coordinator:
                _flip_subject = _flip_predicate = True
        elif _subject is not None:
            if predicate_frame_inherited:
                # FB: on a bare-infinitive item the local "subject" is a
                # mis-segment of the infinitive phrase whose frame M4b has
                # already attributed to the governor.
                _flip_subject = True
            elif _e2 and _head is not None and _head.span is not None \
                    and _subject.span is not None \
                    and _head.span[0] <= _subject.span[0]:
                # FD1: verb-initial performative; the claimed subject is the
                # verb's object.  The local predicate stays authoritative.
                _flip_subject = True
            elif _e3s and _subject.subtype == "POSTVERBAL_SUBJECT":
                # FD2: a postverbal NP inside a declared item is
                # predicate-internal; the lead-in supplies both roles.
                _flip_subject = _flip_predicate = True
            elif _e3s and _head is not None \
                    and _head.subtype == "EXPLICIT_FINITE_PREDICATE" \
                    and _head.span is not None and _subject.span is not None \
                    and _head.span[0] >= _subject.span[0] \
                    and _head.span[1] <= _subject.span[1]:
                # FD3: a finite head inside its own claimed subject is a
                # parse contradiction (the zero-copula NOMINAL_PREDICATE
                # construction is untouched).
                _flip_subject = _flip_predicate = True
            elif _e1 and not ST.is_recital(" ".join(body.split())):
                # FD4: the frame-less fallback admitted this E1 edge, so any
                # local subject claim contradicts the gate that admitted it.
                _flip_subject = _flip_predicate = True
        if _subject is None and _head is not None and not hard_negative \
                and _e3p and _head.subtype == "EXPLICIT_FINITE_PREDICATE":
            # FE: the declared item's frame is its lead-in's.
            _flip_predicate = True
        if _flip_subject:
            subject_state, subject_span = "GOVERNING_CLAUSE_SUBJECT", None
        if _flip_predicate:
            # Local predicate spans, head and complement text are preserved
            # (the V7 correction that governing-clause rows may carry local
            # predicate words is retained).  The frame's provenance is still
            # contextual even when an earlier M4 rule already used the same
            # state name.
            if predicate_state != "GOVERNING_CLAUSE_PREDICATE":
                predicate_state = "GOVERNING_CLAUSE_PREDICATE"
            predicate_frame_inherited = True

    # V581D M8.  The absent-vs-unresolved axis is decided by EVIDENCE, not by
    # which code path failed (M8_CONTRACT.md).  A verbally-empty fragment
    # inside a governed block that consumption lawfully refused still HAS
    # roles — the block's — so they are unresolved, not absent by
    # construction.  Conversely a span with no local predication and a
    # structural verdict that no governing clause exists has nothing for a
    # role to be unresolved about.  GOVERNING_CLAUSE_NOT_SUPPLIED is
    # ignorance, not evidence of absence, and stays untouched.  Terminal
    # serialization is deferred until all role-state rewrites finish.
    if hard_negative and governing_state == "GOVERNING_CLAUSE_UNIQUE":
        if subject_state == "NO_SEMANTIC_SUBJECT":
            subject_state = "SUBJECT_UNRESOLVED"
            subject_absence_is_evidential = False
        if predicate_state == "NO_SEMANTIC_PREDICATE":
            predicate_state = "PREDICATE_UNRESOLVED"
            predicate_absence_is_evidential = False
    elif analysis is not None and analysis.subject is None \
            and analysis.predicate_head is None \
            and governing_state == "GOVERNING_CLAUSE_ABSENT":
        # This branch IS the M8 evidence-of-absence construct, so the flag is
        # recorded exactly where the state is installed and nowhere else.
        if subject_state == "SUBJECT_UNRESOLVED":
            subject_state = "NO_SEMANTIC_SUBJECT"
            subject_absence_is_evidential = True
        if predicate_state == "PREDICATE_UNRESOLVED":
            predicate_state = "NO_SEMANTIC_PREDICATE"
            predicate_absence_is_evidential = True

    # V581E M9.  Passive-overfire (M9_CONTRACT.md).  The selected head shows
    # middle-voice or active-modal morphology, so the PASSIVE typing misread
    # the -ся / damma surface.  Terminal serialization is deferred until the
    # final role state is known.
    if predicate_state == "PASSIVE_OR_IMPERSONAL_PREDICATE" \
            and analysis is not None \
            and _middle_voice_not_passive(body, analysis.predicate_head):
        predicate_state = "EXPLICIT_FINITE_PREDICATE"

    # V581F M10.  Copula -> NOMINAL (M10_CONTRACT.md).  A closed-class
    # copular head with a bound semantic subject is a nominal predication;
    # a lexical finite head is not.  Subject-less rubric material cannot fire
    # (a predicative relation requires a subject); terminal serialization is
    # deferred until the final role state is known.
    if predicate_state == "EXPLICIT_FINITE_PREDICATE" \
            and analysis is not None and analysis.predicate_head is not None \
            and predicate_span is not None \
            and subject_state in _M10_SEMANTIC_SUBJECTS \
            and (analysis.predicate_head.text or "").strip(
                _M9_TOKEN_TRIM).casefold() \
            in _M10_COPULA_CLASSES.get(language or "", frozenset()):
        predicate_state = "NOMINAL_PREDICATE"

    # V581G M11.  Modal -> DEONTIC (M11_CONTRACT.md).  A closed-class
    # deontic modal heading the predication and governing a complement is
    # ONE deontic predicate; a passive-infinitive complement does not make
    # the predication passive.  M11a: modal-led multi-token head chain
    # (complement inside the chain).  M11b: bare modal head with a bound
    # complement.  Terminal serialization is deferred until the final role
    # state is known.
    if predicate_state in ("PASSIVE_OR_IMPERSONAL_PREDICATE",
                           "EXPLICIT_FINITE_PREDICATE") \
            and analysis is not None and analysis.predicate_head is not None \
            and predicate_span is not None:
        _m11_inventory = _M11_MODAL_CLASSES.get(language or "", frozenset())
        _m11_tokens = [t.strip(_M9_TOKEN_TRIM).casefold()
                       for t in (analysis.predicate_head.text or "").split()]
        if _m11_tokens and _m11_tokens[0] in _m11_inventory:
            _m11a = (predicate_state == "PASSIVE_OR_IMPERSONAL_PREDICATE"
                     and len(_m11_tokens) >= 2)
            _m11b = (predicate_state == "EXPLICIT_FINITE_PREDICATE"
                     and len(_m11_tokens) == 1
                     and analysis.predicate_complement is not None)
            if _m11a or _m11b:
                predicate_state = "DEONTIC_OPERATOR_WITH_COMPLEMENT"

    # V581H M12.  Passive-patient subjects (M12_CONTRACT.md).  The subject
    # relation of a passive or deontic-passive predication is the promoted
    # patient; an oblique nominal reached through a preposition after an
    # impersonal passive is no subject at all.  Placed BEFORE the actor
    # derivation so the actor follows the corrected relation; terminal facts
    # are derived after this final rewrite (with completeness mapping fixed by
    # the terminal adapter).
    if analysis is not None and subject_span is not None \
            and predicate_span is not None:
        _m12_preverbal = subject_span[0] < predicate_span[0]
        _m12_head_tokens = [t.strip(_M9_TOKEN_TRIM).casefold()
                            for t in (analysis.predicate_head.text or "").split()
                            ] if analysis.predicate_head is not None else []
        if subject_state == "EXPLICIT_SUBJECT" and _m12_preverbal \
                and predicate_state == "PASSIVE_OR_IMPERSONAL_PREDICATE":
            subject_state = "PASSIVE_PATIENT_SUBJECT"
        elif subject_state == "EXPLICIT_SUBJECT" and _m12_preverbal \
                and predicate_state == "DEONTIC_OPERATOR_WITH_COMPLEMENT" \
                and ((len(_m12_head_tokens) >= 2
                      and _m12_head_tokens[1] in ("be", "être"))
                     or (language == "de" and _M12_DE_PASSIVE_INF.search(
                         analysis.predicate_complement.text
                         if analysis.predicate_complement else ""))):
            subject_state = "PASSIVE_PATIENT_SUBJECT"
        elif subject_state == "EXPLETIVE_OR_IMPERSONAL_CONSTRUCTION" \
                and _m12_preverbal \
                and predicate_state == "PASSIVE_OR_IMPERSONAL_PREDICATE":
            subject_state = "PASSIVE_PATIENT_SUBJECT"
        elif subject_state == "PASSIVE_PATIENT_SUBJECT" \
                and predicate_state == "PASSIVE_OR_IMPERSONAL_PREDICATE" \
                and script == "ARABIC":
            _m12_before = body[:subject_span[0]].split()
            if _m12_before and _m12_before[-1].strip(_M9_TOKEN_TRIM) == "عن":
                subject_state = "EXPLETIVE_OR_IMPERSONAL_CONSTRUCTION"

    # V581I M13.  Construct-grouped residual singles (M13_CONTRACT.md).
    # M13a: a هناك-existential has no grammatical subject — the delayed NP
    # is the pivot.  M13b: an Arabic deontic over a masdar (not an
    # أن-clause) predicates impersonally.  M13c: a NOMINAL predication
    # whose head is not a copula is zero-copula, and so is its subject
    # relation.  M13d: a standalone-coordinator-initial conjunct (the
    # retained M7 axis) inherits the preceding conjunct's subject.  Terminal
    # serialization is deferred until the final role state is known.
    if analysis is not None and analysis.predicate_head is not None:
        _m13_head = (analysis.predicate_head.text or "").strip()
        _m13_head_first = (_m13_head.split()[0].strip(_M9_TOKEN_TRIM)
                           if _m13_head else "")
        _m13_comp = (analysis.predicate_complement.text
                     if analysis.predicate_complement else "").strip()
        if predicate_state == "NOMINAL_PREDICATE" \
                and subject_state == "ZERO_COPULA_NOMINAL_SUBJECT" \
                and _m13_bare_arabic(_m13_head_first) in _M13_AR_EXISTENTIALS:
            subject_state = "EXPLETIVE_OR_IMPERSONAL_CONSTRUCTION"
        elif language == "ar" \
                and predicate_state == "DEONTIC_OPERATOR_WITH_COMPLEMENT" \
                and subject_state == "IMPLICIT_CONTEXT_BOUND_SUBJECT" \
                and (not _m13_comp or _AR_DIACRITIC.sub(
                    "", _m13_comp.split()[0].strip(_M9_TOKEN_TRIM)) != "أن"):
            subject_state = "EXPLETIVE_OR_IMPERSONAL_CONSTRUCTION"
        elif predicate_state == "NOMINAL_PREDICATE" \
                and subject_state == "EXPLICIT_SUBJECT" \
                and _m13_head_first.casefold() \
                not in _M10_COPULA_CLASSES.get(language or "", frozenset()):
            subject_state = "ZERO_COPULA_NOMINAL_SUBJECT"
        elif language == "ru" \
                and predicate_state == "EXPLICIT_FINITE_PREDICATE" \
                and subject_state == "IMPLICIT_CONTEXT_BOUND_SUBJECT" \
                and _COORDINATOR_INITIAL.match(body):
            subject_state = "INHERITED_COORDINATE_SUBJECT"

    # V581J M14.  Subject-span closure (M14_CONTRACT.md).  The selected
    # reading's subject BOUNDARY is corrected against the frozen §13.2
    # span contract; no candidate, analysis or resolution is touched, so
    # selected semantics and the lattice-scored rulers (D38/D42/D46) are
    # pinned by construction, and subject_completeness never reads the
    # span.  Runs before the actor derivation so the actor follows the
    # corrected constituent.
    if subject_span is not None:
        subject_span, subject_state = _m14_subject_span(
            body, subject_span, subject_state, predicate_span,
            (analysis.predicate_head.span
             if analysis is not None and analysis.predicate_head is not None
             else None),
            candidates, script)

    # V581K M15.  Predicate-span closure (M15_CONTRACT.md).  Same layer and
    # the same pin as M14; runs after it so P1/P4 read the corrected
    # subject constituent.
    if predicate_span is not None and analysis is not None:
        predicate_span, predicate_state = _m15_predicate_span(
            body, predicate_span, predicate_state, subject_span,
            (analysis.predicate_head.span
             if analysis.predicate_head is not None else None),
            (analysis.predicate_complement.span
             if analysis.predicate_complement is not None else None),
            script)

    # D39 is authoritative for the final selected semantics, not the local
    # pre-consumption reading.  Derive every obligation and terminal axis only
    # after M7-M15 have completed their role-state and span rewrites.
    required, repair = derive_obligations(
        subject_state=subject_state,
        predicate_state=predicate_state,
        predicate_frame_inherited=predicate_frame_inherited,
        shared_subject_source=shared_subject_source,
        shared_predicate_source=shared_predicate_source,
    )
    antecedent_state = _antecedent_state(subject_state, left_context)
    terminal_facts = _terminal_facts(
        internal_state=internal,
        subject_state=subject_state,
        subject_span=subject_span,
        predicate_state=predicate_state,
        predicate_span=predicate_span,
        predicate_frame_inherited=predicate_frame_inherited,
        subject_absence_is_evidential=subject_absence_is_evidential,
        predicate_absence_is_evidential=predicate_absence_is_evidential,
        # V6.1 T1-R1.  WHY the predicate axis may read BOUND_LOCAL, carried to
        # the terminal adapter so the bound-vs-unresolved decision is made from
        # the detector's own establishment verdict on this span rather than
        # from the bare existence of an emitted span.
        predicate_local_predication_is_evidential=(
            predication_evidence.establishes),
        antecedent_state=antecedent_state,
        required_context_ids=required,
        repair_requirement=repair,
        structural_context_supplied=structural_context is not None,
        governing_state=governing_state,
    )
    # M2's finding is reported only now, phrased from the axes just derived, so
    # the emitted reason cannot answer an earlier generation than the emitted
    # role states it describes.
    if m2_reason_owed:
        reason = _m2_binding_reason(terminal_facts, predicate_span)
    terminal_decision = TM.derive_terminal(terminal_facts)
    disposition_decision = TM.derive_disposition(
        terminal_facts, terminal_decision.value,
    )
    TM.validate_decisions(
        terminal_facts, terminal_decision, disposition_decision,
    )
    external = terminal_decision.value
    disposition = disposition_decision.value
    validate_publication_role_invariant(
        subject_state=subject_state,
        predicate_state=predicate_state,
        final_extraction_disposition=disposition,
    )

    actor_state, actor_span = _semantic_actor(body, analysis, subject_state,
                                              subject_span)
    attribution_state, attribution_span = _attribution_state(analysis, body)

    return RoleBindingV2Record(
        stable_id("v5-8-1-bindingv2", candidate_id, external),
        candidate_id, internal, subject_state, subject_span, predicate_state,
        predicate_span, head_text, complement_text, actor_state, actor_span,
        attribution_state, attribution_span,
        (analysis.institutional_issuer.text
         if analysis and analysis.institutional_issuer else ""),
        antecedent_state,
        _governing_clause_source(structural_context, governing_id,
                                 subject_state),
        shared_subject_source or "NONE", shared_predicate_source or "NONE",
        tuple(dict.fromkeys(required)),
        terminal_facts.proposition_status,
        terminal_facts.subject_completeness,
        terminal_facts.predicate_completeness,
        terminal_facts.binding_uniqueness,
        terminal_facts.required_context_status,
        terminal_facts.repairability,
        external, terminal_decision.rule, repair,
        disposition, disposition_decision.rule,
        round(analysis.compatibility_score if analysis else 0.0, 4), reason,
        script, len(analyses), len(candidates), now_utc())


def _governing_clause_source(structural_context, governing_id: str | None,
                             subject_state: str) -> str:
    """WHERE the governing clause was found, in the frozen vocabulary.

    The frozen reference schema admits exactly NONE, LEAD_IN, LEFT_CONTEXT and
    HEADING here.  This field previously emitted the governing REGION ID when
    one had been resolved, which is not a member of that vocabulary at all.

    The defect was unreachable until the exposed harness was wired: with no
    structural context, `governing_id` was always None and the expression fell
    through to its vocabulary-valued branches every time.  Wiring the harness
    made four units resolve a unique governor, and the corrected safety-critical
    audit -- which checks every emitted value against the frozen vocabulary --
    caught it immediately.  The region's identity is not lost: it travels in
    the structural context, which is where a region identifier belongs.
    """
    if governing_id is None:
        return ("LEFT_CONTEXT" if subject_state == "GOVERNING_CLAUSE_SUBJECT"
                else "NONE")
    if structural_context is None:
        return "LEFT_CONTEXT"
    if governing_id in getattr(structural_context, "list_lead_in_ids", ()):
        return "LEAD_IN"
    if governing_id in getattr(structural_context, "heading_ancestor_ids", ()):
        return "HEADING"
    # Found inside the bounded lookback window and not itself a heading or a
    # list lead-in: it is preceding text.
    return "LEFT_CONTEXT"


def _layout_refusal(candidate_id: str, body: str, order_state: str,
                    language: str | None) -> "RoleBindingV2Record":
    """A packet whose reading order is unsound carries no role binding.

    This is the second emission site for a RoleBindingV2Record, and it must
    agree with `_terminal_facts` about what a role state means.  NO_SEMANTIC_
    SUBJECT is UNRESOLVED there -- absence of a *recoverable* subject, not the
    lawful ABSENT_BY_CONSTRUCTION of a genuine expletive or impersonal
    construction -- and the same reading holds here.  The refusal's terminal
    state and disposition are unaffected: CONSTRUCTION_DEFECT already forces
    ROLE_BINDING_INVALID and IRREPARABLE already forces REJECTED, so no record
    changes its published fate.  proposition_status stays CONSTRUCTION_DEFECT
    rather than NO_SEMANTIC_PROPOSITION because it is the more specific lawful
    value for an unsound reading order.

    V6.1 LRI-R1 propagation.  The adapter now maps BOTH NO_SEMANTIC states by
    one rule, and this site's provenance licenses evidence of absence on
    neither: there is no analysis, no structural context and no governing
    verdict here, and the whole finding is that the span's word sequence is not
    the document's.  A packet whose reading order is unsound supports no claim
    that a role is missing BY CONSTRUCTION -- only that none was resolved.  So
    predicate_completeness follows subject_completeness to UNRESOLVED.  The
    value was NOT chosen here and is not this edit's to choose:
    `test_layout_refusal_agrees_with_the_adapter_on_role_completeness` derives
    it by running the adapter on this record's own emitted role states, that
    test is unmodified, and it FAILED until this literal moved.  Published fate
    is unchanged -- CONSTRUCTION_DEFECT short-circuits `derive_terminal` before
    either completeness axis is read -- and the guards plus the re-derivation
    below prove that on the emitted fields rather than by assertion.

    Because this site does not pass through the terminal adapter, nothing above
    derived its axes and nothing below would have refused an inconsistent
    record: its publication safety rested entirely on four literals happening to
    be right.  The record is therefore validated HERE, on its own emitted
    fields, by exactly the two guards the adapter path calls, before it is
    returned.  The literals are still correct -- these calls are silent on every
    input -- but the site now fails closed by construction rather than by
    inspection.
    """
    record = RoleBindingV2Record(
        stable_id("v5-8-1-bindingv2", candidate_id, order_state), candidate_id,
        "INVALID_BINDING_CANDIDATE", "NO_SEMANTIC_SUBJECT", None,
        "NO_SEMANTIC_PREDICATE", None, "", "", "NO_SEMANTIC_ACTOR", None,
        "ATTRIBUTION_ABSENT", None, "", "NO_ANAPHOR_PRESENT", "NONE", "NONE",
        "NONE", (),
        "CONSTRUCTION_DEFECT", "UNRESOLVED",
        "UNRESOLVED", "NONE", "NONE", "IRREPARABLE",
        "ROLE_BINDING_INVALID", "NO_SEMANTIC_PROPOSITION",
        "re-extract the manifestation with a sound reading order",
        "REJECTED", "IRREPARABLE_NON_PROPOSITION", 0.0,
        f"reading order is {order_state}; the span's word sequence is the "
        "extractor's, not the document's", CL.script_of(body), 0, 0, now_utc())

    # Every value below is read back OFF the emitted record, never off a local
    # that would only agree with itself, so a drifted constructor argument is
    # what is being checked.
    facts = TM.TerminalFacts(
        proposition_status=record.proposition_status,
        subject_completeness=record.subject_completeness,
        predicate_completeness=record.predicate_completeness,
        binding_uniqueness=record.binding_uniqueness,
        required_context_status=record.required_context_status,
        repairability=record.repairability,
    )
    emitted_terminal = TM.TerminalDecision(
        record.role_binding_state, record.terminal_derivation_rule,
    )
    emitted_disposition = TM.TerminalDecision(
        record.final_extraction_disposition,
        record.disposition_derivation_rule,
    )
    TM.validate_decisions(facts, emitted_terminal, emitted_disposition)
    validate_publication_role_invariant(
        subject_state=record.subject_state,
        predicate_state=record.predicate_state,
        final_extraction_disposition=record.final_extraction_disposition,
    )
    # `validate_decisions` protects the crosswalk but trusts the terminal it is
    # handed, and on this path there is no adapter above to have derived one.
    # The emitted pair must therefore BE what the state machine derives from the
    # emitted facts.  It is, exactly -- rule strings included -- so this refuses
    # a future drift rather than changing anything emitted today.
    derived_terminal = TM.derive_terminal(facts)
    derived_disposition = TM.derive_disposition(facts, derived_terminal.value)
    if (emitted_terminal, emitted_disposition) != (
            derived_terminal, derived_disposition):
        raise TM.TerminalContractViolation(
            "the layout refusal emitted a terminal/disposition pair the D39 "
            f"state machine does not derive from its own facts: emitted "
            f"{(emitted_terminal, emitted_disposition)!r}, derived "
            f"{(derived_terminal, derived_disposition)!r}"
        )
    return record


def _semantic_actor(text: str, analysis, subject_state: str, subject_span):
    if subject_state in ("NO_SEMANTIC_SUBJECT",):
        return "NO_SEMANTIC_ACTOR", None
    if subject_state == "PASSIVE_PATIENT_SUBJECT":
        match = re.search(r"\b(?:by|von|durch|par|por|بواسطة|من قبل)\s+(\S.{2,60})",
                          text, re.IGNORECASE)
        if match:
            return "SEMANTIC_ACTOR_EXPLICIT", (match.start(1), match.end(1))
        return "SEMANTIC_ACTOR_UNRESOLVED", None
    if subject_state in ("GOVERNING_CLAUSE_SUBJECT",
                         "IMPLICIT_CONTEXT_BOUND_SUBJECT",
                         "EXPLETIVE_OR_IMPERSONAL_CONSTRUCTION"):
        return "SEMANTIC_ACTOR_UNRESOLVED", None
    if subject_span is not None:
        # A clause with passive morphology has a patient in subject position
        # even when the subject analysis did not label it one.  Promoting it to
        # actor is a recorded safety-critical failure, so passive evidence
        # anywhere in the span withdraws the promotion.
        if _passive_evidence(text):
            return "SEMANTIC_ACTOR_UNRESOLVED", None
        span_text = text[subject_span[0]:subject_span[1]]
        if _INSTITUTION_HEAD.search(span_text):
            return "SEMANTIC_ACTOR_INSTITUTIONAL", subject_span
        return "SEMANTIC_ACTOR_IS_GRAMMATICAL_SUBJECT", subject_span
    return "SEMANTIC_ACTOR_UNRESOLVED", None


_PASSIVE_SURFACE = re.compile(
    r"\b(is|are|was|were|be|been|being)\s+\w{3,}(?:ed|en)\b|"
    r"\b(shall|must|may|should|can|could|will|would)\s+(?:be|been)\s+\w{3,}\b|"
    r"\b(wird|werden|wurde|wurden|worden)\b|"
    r"\b(est|sont|[eé]tait|[eé]taient|être|etre)\s+\w{3,}(?:[eé]|[eé]e|[eé]s|[eé]es)\b|"
    r"\b(peut|peuvent|doit|doivent)\s+(?:être|etre)\s+\w{3,}\b|"
    r"\b(es|son|fue|fueron)\s+\w{3,}(?:ado|ada|ados|adas|ido|ida|idos|idas)\b|"
    r"\b(è|e|sono|viene|vengono|essere)\s+\w{3,}"
    r"(?:ato|ata|ati|ate|ito|ita|iti|ite|uto|uta|uti|ute)\b|"
    r"\w{4,}(?:ся|сь)\b|[يت]ُ[\u0621-\u065f]{2,}", re.IGNORECASE)


def _passive_evidence(text: str) -> bool:
    return bool(_PASSIVE_SURFACE.search(text))


def _attribution_state(analysis, text: str):
    if analysis and analysis.attribution and analysis.attribution.span:
        return "ATTRIBUTION_EXPLICIT", analysis.attribution.span
    if _ATTRIBUTION_MARKER.search(text):
        return "ATTRIBUTION_MARKER_WITHOUT_SOURCE", None
    if _QUOTE_OPEN.search(text):
        return "QUOTED_SPEAKER_UNRESOLVED", None
    return "ATTRIBUTION_ABSENT", None


def _antecedent_state(subject_state: str, left_context: str) -> str:
    """An antecedent is recoverable only when one is actually there.

    Reporting RECOVERABLE for any non-empty context is the invention: it asserts
    that a referent exists in the window without having found one.  The window
    must contain a nominal that could serve, and even then the claim is only
    that recovery is possible.
    """
    if subject_state != "ANAPHORIC_SUBJECT_RECOVERABLE":
        return "NO_ANAPHOR_PRESENT"
    window = left_context[-ANTECEDENT_CONTEXT_CHARS:]
    if not window.strip():
        return "ANTECEDENT_ABSENT"
    nominals = re.findall(r"[A-ZÀ-ÖØ-Þ\u0410-\u042f][^.;:!?\n]{3,60}", window)
    if not nominals:
        return "ANTECEDENT_ABSENT"
    return "ANTECEDENT_RECOVERABLE"


__all__ += ["RoleBindingV2Record", "bind"]
