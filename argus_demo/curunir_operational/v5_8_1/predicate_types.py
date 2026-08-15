"""V5.8.1 — what a predicate-head candidate actually IS (V581-D38B).

Independent adjudication of all 110 predicate-head candidate tokens in the 13
units where the selected and correct heads share a matrix clause established
that production asserts predicate types that are FALSE of the tokens they
describe.  LAT_EXPLICIT_FINITE_PREDICATE is emitted for German nouns
(Gerechtigkeit, Kraft, Schaden, Volkes, Jahren), adjectives (rechtlichen,
letzten), a negation particle (nicht), a complementiser (dass), a preposition
(gegen) and the English article (a); RU_PARTICIPIAL_PREDICATE for the noun
страна; LAT_PASSIVE_OR_IMPERSONAL_PREDICATE for the Italian coordinator "e".

This module says what the token is, separately from what proposed it.

TWO ORTHOGONAL FIELDS, NOT ONE
------------------------------
A candidate carries BOTH its origin rule -- which construction proposed it --
and, from here, a lexical category and a predicate type.  They are not merged.
Merging them was the design error the adjudicator caught: the nine-type
vocabulary alone would have collapsed the passive / deontic / participial
distinctions that the reference's own predicate_state vocabulary uses, and D39
terminal mapping is DEFERRED, not dead.  Destroying that information now to
tidy a taxonomy would corrupt a gate that has not run yet.

WHAT MAY BE REFUSED, AND WHAT MAY NOT
-------------------------------------
Only a POSITIVE identification refuses anything.  UNRESOLVED never does.  The
programme has twice converted "the detector cannot see it" into "it is not
there", and both times measurement caught it; the same error here would delete
lawful analyses and drop D38 below 120/120, which is a declared revert
condition.

Refusal is also CONSTRUCTION-CONDITIONAL.  A noun is not a finite verb, but a
noun can perfectly well be a predicate: Arabic and Russian predicate nominally
with no copula at all, and `clauses.py` exists precisely because requiring a
finite verb is the English assumption this programme removed.  So a positively
non-verbal token is refused only where the ORIGIN RULE CLAIMS FINITE OR
PARTICIPIAL predication.  Nominal, fronted and zero-copula constructions are
untouched.

Closed-class lists appear below and are legitimate: articles, determiners,
prepositions, coordinators, complementisers and negation particles are genuinely
closed classes.  Open-class discrimination is morphological and orthographic
instead -- German capitalises its nouns, and that is a fact about German
orthography rather than a list of German nouns.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

#: What the token IS, lexically.  Orthogonal to the predicate type below.
LEXICAL_CATEGORIES: tuple[str, ...] = (
    "VERB_FINITE", "VERB_NONFINITE", "NOUN", "ADJECTIVE", "ARTICLE_OR_DETERMINER",
    "PREPOSITION", "COORDINATOR", "COMPLEMENTISER", "NEGATION", "ADVERB",
    "UNRESOLVED",
)

#: What the token DOES, predicationally.
PREDICATE_TYPES: tuple[str, ...] = (
    "FINITE_VERBAL_PREDICATE", "COPULAR_PREDICATION",
    "NOMINAL_PREDICATE_COMPLEMENT", "ADJECTIVAL_PREDICATE_COMPLEMENT",
    "NONFINITE_PREDICATE", "NOMINAL_MENTION", "ARGUMENT_NOUN",
    "NO_PREDICATE", "UNRESOLVED",
)

#: Lexical categories that positively CANNOT be a finite verbal predicate.
#: NOUN and ADJECTIVE are deliberately absent from the refusal set used against
#: nominal constructions -- see `refuses_finite_predication`.
POSITIVELY_NOT_VERBAL: frozenset[str] = frozenset({
    "NOUN", "ADJECTIVE", "ARTICLE_OR_DETERMINER", "PREPOSITION",
    "COORDINATOR", "COMPLEMENTISER", "NEGATION",
})

#: Origin rules that CLAIM finite or participial predication.  Only these are
#: subject to refusal; nominal, fronted, deontic and zero-copula constructions
#: predicate by other means and are left alone.
_CLAIMS_FINITE = re.compile(r"(EXPLICIT_FINITE|PARTICIPIAL)")

# ---------------------------------------------------------------------------
# Closed classes.  These lists are legitimate because the classes are closed.
# ---------------------------------------------------------------------------

_ARTICLE_OR_DETERMINER = re.compile(
    r"^(the|a|an|this|that|these|those|each|every|any|some|no|all|both|"
    r"der|die|das|des|dem|den|ein|eine|einer|eines|einem|einen|dieser|diese|"
    r"dieses|diesen|diesem|jeder|jede|jedes|kein|keine|seinen|seiner|seines|"
    r"ihren|ihrer|meinen|meiner|solchem|solchen|letzten|"
    r"le|la|les|un|une|des|du|ce|cet|cette|ces|tout|toute|tous|toutes|"
    r"el|los|las|una|unos|unas|este|esta|estos|estas|todo|toda|todos|todas|"
    r"il|lo|gli|uno|questo|questa|questi|queste|ogni|"
    r"этот|эта|это|эти|весь|вся|все)$", re.IGNORECASE)

_PREPOSITION = re.compile(
    r"^(in|on|at|to|of|for|with|by|from|into|over|under|about|against|between|"
    r"through|during|without|within|upon|"
    r"an|auf|aus|bei|mit|nach|seit|von|vor|zu|zur|zum|durch|für|gegen|ohne|um|"
    r"über|unter|zwischen|während|wegen|"
    r"dans|sur|sous|avec|sans|pour|par|chez|vers|entre|depuis|"
    r"en|con|sin|para|por|sobre|hacia|desde|entre|"
    r"nel|nella|con|senza|per|tra|fra|su|da|"
    r"в|на|под|над|при|для|из|от|до|по|за|с|о|об|между|"
    r"في|على|من|إلى|عن|مع)$", re.IGNORECASE)

_COORDINATOR = re.compile(
    r"^(and|or|but|nor|yet|und|oder|aber|sondern|denn|et|ou|mais|ni|"
    r"y|e|o|u|pero|sino|ma|oppure|né|и|или|но|а|либо|و|أو|لكن|بل)$",
    re.IGNORECASE)

_COMPLEMENTISER = re.compile(
    r"^(that|whether|if|dass|daß|ob|que|qu|si|che|se|что|чтобы|أن|إن)$",
    re.IGNORECASE)

_NEGATION = re.compile(
    r"^(not|no|never|nicht|kein|keine|ne|pas|non|nunca|nè|не|ни|لا|لم|لن|ليس)$",
    re.IGNORECASE)

# ---------------------------------------------------------------------------
# Open classes.  Morphology and orthography, not inventories.
# ---------------------------------------------------------------------------

#: German capitalises every noun.  A capitalised token that is not span-initial
#: and is not a closed-class word is a noun.  This is a rule about German
#: orthography, not a list of German nouns.
_GERMAN_NOUN_SHAPE = re.compile(r"^[A-ZÄÖÜ][a-zäöüß]{2,}$")

#: Attributive adjective endings.  German adjectives inflect; a lower-case token
#: ending in these, in a language that capitalises nouns, is adjectival.
_GERMAN_ADJECTIVE = re.compile(
    r"^[a-zäöüß]{4,}(en|em|er|es|e)$", re.IGNORECASE)

#: Romance adjective/participle plural agreement.
_ROMANCE_ADJECTIVE = re.compile(
    r"^[a-záéíóúàèìòùâêîôûäëïöü]{4,}(ales|ados|idos|adas|idas|osos|osas|"
    r"anti|enti|ivi|ive|ico|ica|ici|iche)$", re.IGNORECASE)


#: Forms whose closed-class membership is CROSS-LINGUISTICALLY AMBIGUOUS, so
#: that membership alone cannot decide them.  "a" is the English indefinite
#: article AND the French third-person singular of avoir; "e" is the Italian
#: coordinator AND, unaccented, a damaged copula; "o" is a Spanish coordinator
#: AND a Portuguese article.
#:
#: This set exists because the first draft refused "a" in "Le régulateur A
#: PUBLIÉ la décision" -- the exact mirror of the defect this module was written
#: to fix, where English "a" was read as the French auxiliary.  The contract's
#: own rule governs both directions: independent context must license the
#: reading.  Without that context these forms are UNRESOLVED, and UNRESOLVED
#: refuses nothing.
_CROSS_LINGUISTICALLY_AMBIGUOUS = frozenset({"a", "e", "o", "и", "у"})

#: Past-participle shapes.  An ambiguous form followed by one of these is an
#: auxiliary, not an article.
_PARTICIPLE_SHAPE = re.compile(
    r"^\w{3,}(é|ée|és|ées|e|ee|es|ado|ada|ados|adas|ido|ida|idos|idas|"
    r"ato|ata|ati|ate|ito|ita|iti|ite|uto|uta|t|to|ed|en)$", re.IGNORECASE)


@dataclass(frozen=True)
class PredicateTyping:
    """What a candidate is, beside what proposed it."""

    lexical_category: str
    predicate_type: str
    basis: str
    decisive: bool

    def __post_init__(self) -> None:
        if self.lexical_category not in LEXICAL_CATEGORIES:
            raise ValueError(f"unknown lexical category {self.lexical_category!r}")
        if self.predicate_type not in PREDICATE_TYPES:
            raise ValueError(f"unknown predicate type {self.predicate_type!r}")


_UNRESOLVED = PredicateTyping("UNRESOLVED", "UNRESOLVED",
                              "no positive evidence in any dimension", False)


def classify(token: str, *, span_initial: bool = False,
             script: str = "LATIN",
             following_token: str = "") -> PredicateTyping:
    """Type one candidate head token.  UNRESOLVED unless positively identified.

    `following_token` licenses the cross-linguistically ambiguous forms.  Without
    it they resolve to UNRESOLVED rather than to whichever language's reading the
    lexicon happens to list first.
    """
    bare = (token or "").split()
    if not bare:
        return _UNRESOLVED
    word = bare[0].strip(".,;:()[]«»\"'“”„‟؛،—–-")
    if not word:
        return _UNRESOLVED
    folded = word.casefold()

    if folded in _CROSS_LINGUISTICALLY_AMBIGUOUS:
        following = (following_token or "").strip(".,;:()[]«»\"'“”„‟؛،—–-")
        if not following or _PARTICIPLE_SHAPE.match(following):
            # Either no context, or context indicating an auxiliary.  Decline.
            return PredicateTyping(
                "UNRESOLVED", "UNRESOLVED",
                "cross-linguistically ambiguous form; context does not license "
                "a closed-class reading", False)
        return PredicateTyping(
            "ARTICLE_OR_DETERMINER" if folded in ("a", "o") else "COORDINATOR",
            "NO_PREDICATE",
            "cross-linguistically ambiguous form licensed as closed-class by a "
            "non-participial follower", True)

    # Closed classes first: these are decidable, and being decidable is the
    # whole reason they may refuse anything.
    for pattern, category in ((_NEGATION, "NEGATION"),
                              (_COMPLEMENTISER, "COMPLEMENTISER"),
                              (_COORDINATOR, "COORDINATOR"),
                              (_ARTICLE_OR_DETERMINER, "ARTICLE_OR_DETERMINER"),
                              (_PREPOSITION, "PREPOSITION")):
        if pattern.match(folded):
            return PredicateTyping(category, "NO_PREDICATE",
                                   f"closed-class membership: {category}", True)

    if script == "LATIN":
        # German orthography: a capitalised non-initial token is a noun.  Applied
        # only where it is not also a closed-class word, tested above.
        if not span_initial and _GERMAN_NOUN_SHAPE.match(word):
            return PredicateTyping("NOUN", "ARGUMENT_NOUN",
                                   "capitalised non-initial token in a "
                                   "noun-capitalising orthography", True)
        if _GERMAN_ADJECTIVE.match(word) and word[:1].islower():
            return PredicateTyping("ADJECTIVE", "ADJECTIVAL_PREDICATE_COMPLEMENT",
                                   "inflected attributive adjective ending", False)
        if _ROMANCE_ADJECTIVE.match(folded):
            return PredicateTyping("ADJECTIVE", "ADJECTIVAL_PREDICATE_COMPLEMENT",
                                   "Romance adjectival agreement ending", False)

    return _UNRESOLVED


def refuses_finite_predication(typing: PredicateTyping, origin_rule: str) -> bool:
    """May this candidate fill a slot whose construction claims finite predication?

    Refusal requires BOTH a positive non-verbal identification AND an origin rule
    that claims finite or participial predication.  A noun heading an Arabic
    zero-copula or fronted nominal predication is untouched: predicating without
    a verb is not a defect, it is most of the world's languages.
    """
    if not typing.decisive:
        return False
    if typing.lexical_category not in POSITIVELY_NOT_VERBAL:
        return False
    return bool(_CLAIMS_FINITE.search(origin_rule or ""))


__all__ = [
    "LEXICAL_CATEGORIES", "PREDICATE_TYPES", "POSITIVELY_NOT_VERBAL",
    "PredicateTyping", "classify", "refuses_finite_predication",
]
