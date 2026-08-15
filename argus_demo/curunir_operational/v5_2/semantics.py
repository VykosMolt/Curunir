"""Semantic candidate resolution over a candidate lattice (contract Section 9).

V5.1 admitted candidates through ``extraction.admit_candidate``: a single-span,
single-shot gate that parsed exactly the span it was handed.  When a required
role was missing it either rejected outright (if the span looked like a
complete sentence) or asked for a context expansion that no caller ever
performed, because ``expand_context`` sits outside the admission path.  On the
three V5.1 campaigns that produced 3891 rejections, of which 2836 were
"noun-phrase span has no propositional content" and 1054 were "complete
propositional span could not be semantically parsed".  Blind reviewers judged
48 of 90 sampled rejections wrong, and every single Surface-1 error was a
rejection: the boundary over-rejects, it does not over-admit.

This module replaces the single-shot gate with a resolver that

* builds a lattice of alternative span boundaries — narrow, sentence,
  expanded and block — over abbreviation-aware sentence segmentation;
* generates a competing proposition structure at each level;
* scores them on role completeness, boundary defensibility and layout
  evidence, and selects the best-supported interpretation while retaining the
  losers;
* recovers actors from a previous sentence, modality from a heading, a
  predicate or unit from a table header, a qualification from a footnote, an
  object identity from a caption, an attribution after a quotation, and a
  continuation across a page break;
* separates the dispositions V5.1 collapsed into REJECTED: chrome and page
  furniture is quarantined, recoverable truncation asks for context, and only
  a span that genuinely cannot carry its type is rejected.

Every rule is language-general (en/de/fr/es/it cue tables with a deterministic
union fallback).  Nothing here encodes a campaign, source or fixture answer.

Research shadow only.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache
from typing import Any, Iterable, Mapping, Sequence

from ..v4.models import ExtractionCandidate, NormalizedDocument
from ..v5_1.extraction import (
    SemanticParse, _langs, _normalize_type, _quarantined_regions,
    _structural_function, parse_semantics,
)
from ..v5_1.models import (
    CapabilityOutcome, Record, capability_outcome, now_utc, sha256, stable_id,
)
from . import chrome
from .lifecycle import LifecycleReading, derive_lifecycle

# ---------------------------------------------------------------------------
# Section 9.2 — lattice levels
# ---------------------------------------------------------------------------

SPAN_LEVELS = ("NARROW", "SENTENCE", "EXPANDED", "BLOCK")
_LEVEL_INDEX = {level: index for index, level in enumerate(SPAN_LEVELS)}

# Required semantic roles per candidate type, kept identical to V5.1 so the
# admission contract is not quietly relaxed while the resolver is repaired.
REQUIRED_ROLES: Mapping[str, tuple[str, ...]] = {
    "ENTITY_MENTION": ("subject",),
    "CLAIM": ("subject", "predicate", "object_or_value"),
    "RELATION": ("subject", "predicate", "object_or_value"),
    "EVENT": ("subject", "predicate"),
    "QUOTATION": ("attribution",),
    "DATE": ("temporal_scope",),
    "NUMERIC_VALUE": ("value", "unit", "referent"),
    "CORRECTION": ("predicate",),
    "RETRACTION": ("predicate",),
    "CITATION": (),
    "POLARITY": (), "MODALITY": (), "TEMPORAL_SCOPE": (), "GEOGRAPHIC_SCOPE": (),
}

_EXACT_PRECISIONS = frozenset({"EXACT_BYTE", "EXACT_CHARACTER", "EXACT_PAGE_CHARACTER"})
_EXACT_SPAN_TYPES = frozenset({"QUOTATION", "NUMERIC_VALUE", "DATE"})
_MATERIAL_TYPES = frozenset({"CLAIM", "RELATION", "EVENT", "QUOTATION",
                             "CORRECTION", "RETRACTION"})

BOUNDARY_WARNINGS = (
    "TRUNCATED_AT_ABBREVIATION", "TRUNCATED_MID_SENTENCE", "OPEN_PARENTHESIS",
    "OPEN_QUOTATION", "CROSSES_PAGE_BREAK", "NON_REFERENTIAL_SUBJECT",
    "ANTECEDENT_RESOLVED_FROM_CONTEXT", "MODALITY_FROM_HEADING",
    "PREDICATE_FROM_TABLE_HEADER", "UNIT_FROM_TABLE_HEADER",
    "QUALIFIED_BY_FOOTNOTE", "OBJECT_FROM_CAPTION",
    "ATTRIBUTION_AFTER_QUOTATION", "CONTINUES_ON_NEXT_PAGE",
    "SUPERSEDED_BY_CORRECTION_NOTICE", "LIST_ITEM_WITHOUT_STEM",
)


# ---------------------------------------------------------------------------
# Section 9.3 — abbreviation-aware sentence segmentation
# ---------------------------------------------------------------------------

# A period after one of these never ends a sentence.  This is the mechanism
# that stopped V5.1 truncating "... adversarial testing (e." out of "(e.g.
# red teaming)".
_ABBREVIATIONS = frozenset({
    # cross-language scholarly and legal
    "e.g", "eg", "i.e", "ie", "cf", "etc", "viz", "al", "vs", "resp",
    "no", "nos", "art", "arts", "para", "paras", "sec", "secs", "fig", "figs",
    "tab", "ch", "pp", "p", "ed", "eds", "vol", "cap", "approx", "incl", "excl",
    # english titles
    "mr", "mrs", "ms", "dr", "prof", "st", "jr", "sr", "inc", "ltd", "co",
    # german
    "z.b", "zb", "u.a", "ua", "bzw", "ggf", "d.h", "dh", "vgl", "abs", "nr",
    "bspw", "evtl", "einschl", "s.o", "s.u", "usw", "ca", "bzgl", "gem",
    # french
    "p.ex", "c.-à-d", "cf", "env", "chap", "éd", "art", "n°", "m", "mme",
    # spanish / italian
    "p.ej", "ej", "núm", "núms", "pág", "págs", "sr", "sra", "cf", "ecc",
    "n.b", "nb", "op.cit", "ibid",
    # station, quantity and contact abbreviations that end a line without
    # ending a sentence
    "bf", "hbf", "str", "inkl", "max", "min", "tel", "mio", "mrd", "tsd",
})

_SENTENCE_END = re.compile(r"[.!?…]+[\"'»›)\]]*(?=\s|$)")
_ORDINAL_TAIL = re.compile(r"(?:^|\s)\d{1,4}$")


def _is_abbreviation(text: str, dot_index: int) -> bool:
    head = text[:dot_index]
    token = re.split(r"[\s(\[]", head)[-1] if head else ""
    folded = token.casefold().rstrip(".")
    if folded in _ABBREVIATIONS:
        return True
    # A single capital letter is an initial: "J. Smith", "Art. 5".
    if len(folded) == 1 and folded.isalpha():
        return True
    # German and Romance ordinals: "16. Januar", "1. Absatz".
    if _ORDINAL_TAIL.search(head):
        following = text[dot_index + 1: dot_index + 3]
        if following[:1] == " " and following[1:2].isalpha() and not following[1:2].isupper():
            return True
        if following[:1] == " " and following[1:2].isupper() and len(head) - len(token) > 0:
            return True
    return False


def segment_sentences(text: str) -> tuple[tuple[int, int], ...]:
    """Sentence spans over ``text``, abbreviation and ordinal aware."""
    body = text or ""
    spans: list[tuple[int, int]] = []
    start = 0
    for match in _SENTENCE_END.finditer(body):
        dot = match.start()
        if _is_abbreviation(body, dot):
            continue
        end = match.end()
        if body[start:end].strip():
            spans.append((start, end))
        start = end
        while start < len(body) and body[start] in " \t":
            start += 1
    if body[start:].strip():
        spans.append((start, len(body)))
    if not spans:
        spans.append((0, len(body)))
    # Newlines end a sentence too: a heading or a list item has no terminator.
    refined: list[tuple[int, int]] = []
    for span_start, span_end in spans:
        cursor = span_start
        for line_break in re.finditer(r"\n+", body[span_start:span_end]):
            absolute = span_start + line_break.start()
            if body[cursor:absolute].strip():
                refined.append((cursor, absolute))
            cursor = span_start + line_break.end()
        if body[cursor:span_end].strip():
            refined.append((cursor, span_end))
    return tuple(refined or spans)


@lru_cache(maxsize=64)
def _segment_cached(text: str) -> tuple[tuple[int, int], ...]:
    """Segmentation is per document, not per candidate.

    Without this the resolver re-segments a 500k-character regulation once per
    candidate, which is the difference between seconds and hours on a real
    campaign.  Pure function of the text, so caching cannot change a decision.
    """
    return segment_sentences(text)


def _containing(spans: Sequence[tuple[int, int]], start: int, end: int) -> tuple[int, int]:
    first = last = None
    for index, (span_start, span_end) in enumerate(spans):
        if span_end > start and span_start < end:
            first = index if first is None else first
            last = index
    if first is None:
        return (start, end)
    return (spans[first][0], spans[last][1])


# A block is bounded so a document with no blank lines — the shape HTML
# normalization produces — cannot make the whole document one block.
_BLOCK_WINDOW = 2400


def _block_bounds(text: str, start: int, end: int) -> tuple[int, int]:
    """The paragraph or table row containing the span.

    Blank lines delimit a block where the document has them; otherwise the
    block is a bounded window snapped to line boundaries.
    """
    body = text or ""
    floor, ceiling = max(0, start - _BLOCK_WINDOW), min(len(body), end + _BLOCK_WINDOW)
    before = body.rfind("\n\n", floor, start)
    block_start = floor if before < 0 else before + 2
    after = body.find("\n\n", end, ceiling)
    block_end = ceiling if after < 0 else after
    if block_start > floor:
        pass
    elif block_start > 0:
        line = body.rfind("\n", floor, start)
        block_start = floor if line < 0 else line + 1
    if block_end < ceiling:
        pass
    elif block_end < len(body):
        line = body.find("\n", end, ceiling)
        block_end = ceiling if line < 0 else line
    return (block_start, max(block_end, end))


# ---------------------------------------------------------------------------
# Section 9.1 — role recovery
# ---------------------------------------------------------------------------

# Modal and auxiliary forms V5.1's lexicon missed, including the inflected
# German subjunctives ("sollte") whose complement is a pronoun rather than a
# determiner.  V5.1's finite-verb test required the token after the verb to be
# a determiner or preposition, so German verb-second order ("sollte man ...")
# read as a bare noun phrase and the candidate was rejected.
_EXTRA_FINITE = {
    "en": {"shall", "ought", "need", "needs", "used", "gets", "get", "got",
           "makes", "make", "made", "says", "say", "said", "goes", "go", "went",
           "comes", "come", "came", "takes", "take", "took", "sets", "set",
           "holds", "hold", "held", "runs", "run", "ran", "seems", "seem",
           "appears", "appear", "includes", "include", "included", "provides",
           "provide", "provided", "requires", "require", "required", "allows",
           "allow", "allowed", "covers", "cover", "covered"},
    # German strong verbs are invisible to suffix morphology ("traf", "ging",
    # "kam"), so the preterite and third-person forms of the high-frequency
    # strong paradigms are listed.  A general language resource, not a
    # campaign vocabulary.
    "de": {"sollte", "sollten", "könnte", "könnten", "müsste", "müssten",
           "dürfte", "dürften", "wollte", "wollten", "möchte", "möchten",
           "war", "wäre", "wären", "hätte", "hätten", "würde", "würden",
           "gibt", "geben", "gab", "steht", "stehen", "stand", "liegt",
           "liegen", "lag", "geht", "gehen", "ging", "macht", "machen",
           "sagt", "sagen", "sagte", "lohnt", "lohnen", "zeigt", "zeigen",
           "nimmt", "nehmen", "bietet", "bieten", "erhält", "erhalten",
           "traf", "trafen", "trifft", "treffen", "kam", "kamen", "kommt",
           "kommen", "sah", "sahen", "sieht", "sehen", "fand", "fanden",
           "findet", "finden", "hielt", "hielten", "hält", "halten",
           "sprach", "sprachen", "spricht", "sprechen", "schrieb", "schreibt",
           "trug", "trägt", "tragen", "zog", "zieht", "ziehen", "blieb",
           "blieben", "hieß", "heißt", "ließ", "lässt", "lassen", "rief",
           "ruft", "rufen", "brachte", "bringt", "bringen", "fuhr", "fährt",
           "fahren", "lief", "läuft", "laufen", "trat", "tritt", "treten",
           "begann", "beginnt", "beginnen", "gilt", "galt", "betrifft",
           "betraf", "erfolgt", "erfolgte", "besteht", "bestand", "entfällt",
           "entfiel", "verfügt", "verfügte", "beträgt", "betrug", "umfasst",
           "umfasste", "ermöglicht", "ermöglichte", "führt", "führte",
           "setzt", "setzte", "stellt", "stellte", "nutzt", "nutzte"},
    "fr": {"était", "serait", "seraient", "aurait", "auraient", "fait", "font",
           "dit", "disent", "prend", "prennent", "met", "mettent", "vient",
           "viennent", "devient", "deviennent", "permet", "permettent",
           "comprend", "comprennent", "prévoit", "prévoient"},
    "es": {"sería", "serían", "habría", "habrían", "hace", "hacen", "dice",
           "dicen", "tiene", "tienen", "pone", "ponen", "viene", "vienen",
           "permite", "permiten", "incluye", "incluyen", "prevé", "prevén",
           "cuenta", "cuentan"},
}

# Complement starters extended with the personal and indefinite pronouns that
# German verb-second order and Romance clitics put right after the verb.
_EXTRA_COMPLEMENTS = {
    "en": {"it", "they", "he", "she", "we", "you", "this", "these", "those",
           "there", "not", "no", "also", "now", "still", "already", "only"},
    "de": {"man", "es", "sie", "er", "wir", "ich", "ihr", "dies", "diese",
           "dieser", "dieses", "nicht", "auch", "nun", "noch", "bereits",
           "sich", "dazu", "davon", "damit", "künftig", "wieder", "jetzt"},
    "fr": {"il", "elle", "ils", "elles", "on", "nous", "vous", "ce", "cette",
           "ces", "se", "ne", "pas", "aussi", "encore", "déjà"},
    "es": {"él", "ella", "ellos", "ellas", "se", "no", "también", "aún", "ya",
           "esto", "esta", "este", "estos", "estas"},
}

_INFLECTION = {
    "en": re.compile(r"(?:ed|ies|es|s)$"),
    "de": re.compile(r"(?:tet|ten|te|st|t)$"),
    "fr": re.compile(r"(?:aient|ait|eront|era|ent|it|e)$"),
    "es": re.compile(r"(?:aron|arán|ará|irán|irá|ó|an|en|a|e)$"),
}

_BASE_FINITE = {
    "en": {"am", "is", "are", "was", "were", "be", "been", "being", "has",
           "have", "had", "do", "does", "did", "will", "would", "shall",
           "should", "can", "could", "may", "might", "must", "remains",
           "remain", "became", "become", "becomes"},
    "de": {"ist", "sind", "war", "waren", "wird", "werden", "wurde", "wurden",
           "hat", "haben", "hatte", "hatten", "kann", "können", "muss",
           "müssen", "soll", "sollen", "darf", "dürfen", "will", "wollen",
           "bleibt", "bleiben", "gilt", "gelten"},
    "fr": {"est", "sont", "était", "étaient", "sera", "seront", "a", "ont",
           "avait", "avaient", "fut", "furent", "peut", "peuvent", "doit",
           "doivent", "va", "vont", "reste", "restent", "demeure"},
    "es": {"es", "son", "era", "eran", "fue", "fueron", "está", "están",
           "estaba", "estaban", "ha", "han", "había", "habían", "será",
           "serán", "puede", "pueden", "debe", "deben", "va", "van", "hay",
           "sigue", "siguen"},
}

_BASE_COMPLEMENTS = {
    "en": {"the", "a", "an", "its", "their", "his", "her", "to", "that", "in",
           "on", "with", "by", "as", "into", "at", "for"},
    "de": {"die", "der", "das", "den", "dem", "des", "ein", "eine", "einen",
           "einem", "einer", "im", "in", "an", "auf", "mit", "für", "dass",
           "zu", "seit", "als"},
    "fr": {"le", "la", "les", "un", "une", "des", "du", "que", "à", "en",
           "dans", "par", "pour", "au", "aux", "sur"},
    "es": {"el", "la", "los", "las", "un", "una", "unos", "unas", "que", "a",
           "en", "de", "con", "por", "para", "al"},
}

FINITE_LEXICON = {code: _BASE_FINITE[code] | _EXTRA_FINITE[code]
                  for code in _BASE_FINITE}
COMPLEMENT_STARTERS = {code: _BASE_COMPLEMENTS[code] | _EXTRA_COMPLEMENTS[code]
                       for code in _BASE_COMPLEMENTS}

# Subjects that refer to nothing on their own.  V5.1 accepted "Damit" as the
# subject of a claim; the resolver rejects them and looks for an antecedent in
# the expanded span.
_NON_REFERENTIAL = {
    "en": {"this", "that", "it", "there", "these", "those", "thereby", "thus",
           "hence", "here", "also", "however", "meanwhile", "such", "which",
           "who", "one", "some", "they"},
    "de": {"damit", "dabei", "dadurch", "daher", "deshalb", "darum", "dann",
           "hier", "dort", "so", "es", "dies", "das", "diese", "dieser",
           "außerdem", "zudem", "ferner", "somit", "hierzu", "hierbei", "man",
           "sie", "er", "wir", "jedoch", "allerdings"},
    "fr": {"cela", "ceci", "ainsi", "donc", "ici", "il", "elle", "on", "ils",
           "elles", "ce", "cet", "cette", "par ailleurs", "toutefois",
           "cependant", "en outre"},
    "es": {"esto", "eso", "así", "aquí", "él", "ella", "ellos", "ellas",
           "este", "esta", "estos", "estas", "por tanto", "además", "sin embargo"},
}

_PREDICATE_CONTINUATION = {"not", "no", "to", "been", "being", "longer",
                           "nicht", "mehr", "pas", "plus", "ne", "été",
                           "wieder", "auch", "bereits", "noch"}
_PARTICIPLE_END = re.compile(r"(?:ed|en|t|é|ée|és|ado|ido|ada|ida)$")

_TOKEN = re.compile(r"[\w'’À-ÿ-]+")


def _tokenize(text: str) -> tuple[tuple[str, int], ...]:
    return tuple((m.group(0), m.start()) for m in _TOKEN.finditer(text))


# Function words that can never head a subject.  Used by the subject-head
# test that replaced V5.1's next-token requirement.
_FUNCTION_WORDS = frozenset({
    "the", "a", "an", "of", "in", "on", "at", "to", "for", "with", "by", "from",
    "and", "or", "but", "as", "that", "which", "who", "whom", "whose", "if",
    "when", "where", "while", "than", "into", "onto", "upon", "about", "over",
    "under", "between", "among", "per", "via", "such", "any", "each", "every",
    "no", "not", "also", "more", "most", "less", "very", "only",
    "der", "die", "das", "den", "dem", "des", "ein", "eine", "einen", "einem",
    "einer", "eines", "und", "oder", "aber", "von", "zu", "zur", "zum", "mit",
    "für", "auf", "an", "im", "in", "bei", "nach", "über", "unter", "durch",
    "als", "wie", "dass", "wenn", "weil", "auch", "nicht", "nur", "sehr",
    "le", "la", "les", "un", "une", "des", "du", "de", "et", "ou", "mais",
    "pour", "avec", "par", "dans", "sur", "sous", "entre", "au", "aux", "que",
    "qui", "dont", "si", "comme", "ne", "pas", "plus", "très", "aussi",
    "el", "los", "las", "unos", "unas", "y", "o", "pero", "para", "con",
    "por", "en", "sobre", "bajo", "entre", "al", "del", "si", "como", "muy",
    "también", "solo", "más", "menos",
})

_PRONOUNS = frozenset({
    "it", "they", "he", "she", "we", "you", "i", "this", "these", "those",
    "there", "one", "someone", "everyone",
    "es", "sie", "er", "wir", "ich", "man", "dies", "diese", "dieser",
    "il", "elle", "ils", "elles", "on", "nous", "vous", "cela", "ceci",
    "ellos", "ellas", "esto", "eso", "él",
})

_NOMINAL_SUFFIX = re.compile(
    r"(?:tion|tions|ment|ments|ung|ungen|heit|keit|schaft|ität|itaet|ance|"
    r"ence|ity|ities|ism|ist|ists|ency|ancy|ship|hood|dad|ción|ciones|dade|"
    r"zione|zioni|té|tés|ies|ors|ers|ies|ies)$", re.IGNORECASE)


_DETERMINERS = frozenset({
    "the", "a", "an", "this", "these", "those", "its", "their", "his", "her",
    "our", "your", "any", "each", "every", "such",
    "der", "die", "das", "den", "dem", "des", "ein", "eine", "einen", "einem",
    "einer", "eines", "dieser", "diese", "dieses", "diesen", "diesem",
    "le", "la", "les", "un", "une", "des", "du", "ce", "cet", "cette", "ces",
    "el", "los", "las", "unos", "unas", "este", "esta", "estos", "estas",
})
_GOVERNING = _FUNCTION_WORDS - _DETERMINERS


def _subject_head(tokens: Sequence[str], verb_index: int) -> bool:
    """Can the material before a candidate verb head that verb's subject?

    V5.1 required the token *after* an inflected form to be a determiner or
    preposition, which German verb-second order ("sollte man", "traf den")
    and ordinary English ("Stuttgart halted everything") routinely violate.
    Looking left is both more permissive where it should be and stricter where
    it matters: a noun phrase governed by a preposition is an object, not a
    subject, so "of the measures put in place" stays a noun phrase and
    "a detailed description" is still not a clause.
    """
    index = verb_index - 1
    if index < 0:
        return False
    if tokens[index].casefold() in _FUNCTION_WORDS:
        return False
    for position in range(index, max(-1, index - 5), -1):
        folded = tokens[position].casefold()
        if folded in _GOVERNING:
            return False
        if folded in _DETERMINERS:
            if position > 0 and tokens[position - 1].casefold() in _GOVERNING:
                return False
            return True
    return True


def is_finite(token: str, nxt: str | None, language: str, *,
              tokens: Sequence[str] = (), index: int = -1) -> bool:
    """Is this token the finite verb of a clause?

    Three routes, in order of confidence: a lexicon hit; an inflected form
    with a plausible subject to its left; or a base form flanked by a subject
    to the left and a complement starter to the right ("AI Factories leverage
    the supercomputing capacity"), which no morphology test can catch.
    """
    folded = token.casefold()
    uncontracted = re.sub(r"^(?:n|l|d|s|j|c|m|t)['’]", "", folded)
    for code in _langs(language):
        if folded in FINITE_LEXICON[code] or uncontracted in FINITE_LEXICON[code]:
            return True
    if not token[:1].islower():
        return False
    if folded in _FUNCTION_WORDS or folded in _PRONOUNS:
        return False
    has_subject = _subject_head(tokens, index) if index >= 0 else False
    for code in _langs(language):
        if _INFLECTION[code].search(folded):
            if code == "en" and (len(folded) < 4 or folded.endswith(("ss", "us", "is"))):
                continue
            if nxt is not None and nxt.casefold() in COMPLEMENT_STARTERS[code]:
                return True
            if has_subject and not _NOMINAL_SUFFIX.search(folded):
                return True
        # The base-form route is English-only.  English plural-agreeing verbs
        # carry no inflection at all ("AI Factories leverage the capacity"), so
        # morphology cannot see them.  German and the Romance languages inflect
        # every finite form, so applying the same route there would read
        # ordinary nouns as verbs — "Estado miembro en cuestion" became a
        # clause under an earlier draft of this rule.
        if (code == "en" and has_subject and len(folded) > 2 and nxt is not None
                and nxt.casefold() in COMPLEMENT_STARTERS[code]
                and not _NOMINAL_SUFFIX.search(folded)):
            return True
    return False


def _non_referential(subject: str, language: str) -> bool:
    folded = " ".join(subject.split()).casefold().strip(" ,;:.")
    if not folded:
        return True
    for code in _langs(language):
        if folded in _NON_REFERENTIAL[code]:
            return True
        first = folded.split()[0] if folded.split() else ""
        if first in _NON_REFERENTIAL[code] and len(folded.split()) == 1:
            return True
    return False


@dataclass(frozen=True)
class RoleSplit:
    subject: str
    predicate: str
    object_or_value: str
    finite_clause: bool
    warnings: tuple[str, ...]


def split_roles(text: str, language: str) -> RoleSplit:
    """Split a span into subject, predicate and object.

    Differs from V5.1 in two ways that its failures required: the finite-verb
    scan accepts inflected modals followed by a pronoun (German verb-second),
    and a non-referential subject is reported rather than accepted.
    """
    tokens = _tokenize(text)
    words = [token for token, _ in tokens]
    finite_at = -1
    for index, (token, _) in enumerate(tokens):
        nxt = tokens[index + 1][0] if index + 1 < len(tokens) else None
        if is_finite(token, nxt, language, tokens=words, index=index):
            finite_at = index
            break
    if finite_at < 0:
        subject = " ".join(token for token, _ in tokens).strip()
        return RoleSplit(subject, "", "", False, ())

    subject = text[:tokens[finite_at][1]].strip(" ,;:—-–\"'«»()")
    end = finite_at
    while end + 1 < len(tokens):
        token = tokens[end + 1][0]
        folded = token.casefold()
        in_lexicon = any(folded in FINITE_LEXICON[code] for code in _langs(language))
        if folded in _PREDICATE_CONTINUATION or in_lexicon or (
                token[:1].islower() and _PARTICIPLE_END.search(folded)):
            end += 1
        else:
            break
    predicate_start = tokens[finite_at][1]
    predicate_end = tokens[end][1] + len(tokens[end][0])
    predicate = " ".join(text[predicate_start:predicate_end].split())
    object_part = text[predicate_end:].strip(" ,;:—-–\"'«»().!?…")

    warnings: list[str] = []
    if _non_referential(subject, language):
        warnings.append("NON_REFERENTIAL_SUBJECT")
    return RoleSplit(subject, predicate, object_part, True, tuple(warnings))


# ---------------------------------------------------------------------------
# Section 9.3 — boundary evidence recovered from surrounding structure
# ---------------------------------------------------------------------------

_HEADING = re.compile(r"^\s*(?:[A-Z0-9][^\n]{0,90})\s*$")
_TABLE_ROW = re.compile(r"\S[ \t]{2,}\S")
_FOOTNOTE_MARK = re.compile(r"(?:\(\s*\d{1,3}\s*\)|\[\d{1,3}\]|[*†‡])")
_CAPTION = re.compile(
    r"^\s*(?:abbildung|abb|tabelle|tableau|tabela|tabla|table|tab|figure|figura"
    r"|fig|grafik|graph|chart|immagine)\.?\s*\d*\s*[:.\-–]?\s*(.+)$",
    re.IGNORECASE)
_POST_QUOTE_ATTRIBUTION = re.compile(
    r"[\"”»']\s*[,.]?\s*(?:said|says|stated|told|according\s+to|sagte|erklärte|"
    r"so|d[ée]clare|a\s+d[ée]clar[ée]|selon|dijo|declar[óo]|seg[úu]n)\s+"
    r"([^.,;:\n]{2,80})", re.IGNORECASE)
_CORRECTION_NOTICE = re.compile(
    r"\b(?:correction|corrigendum|erratum|berichtigung|richtigstellung|"
    r"rectificatif|rectificaci[óo]n|corrigenda|rettifica)\b", re.IGNORECASE)
_RETRACTION_NOTICE = re.compile(
    r"\b(?:retract(?:ed|ion)|zur[üu]ckgezogen|r[ée]tract(?:ation|[ée])|"
    r"retirad[oa]|ritirat[oa])\b", re.IGNORECASE)
_LIST_ITEM = re.compile(r"^\s*(?:\d{1,3}[.)]|[-–—•*]|\([a-z]\)|[a-z][.)])\s+")


@dataclass(frozen=True)
class BoundaryEvidence(Record):
    """What the structure around a span supplies to its interpretation."""

    evidence_id: str
    heading: str | None
    table_header: str | None
    footnote: str | None
    caption: str | None
    post_quotation_attribution: str | None
    previous_sentence: str | None
    correction_notice: bool
    retraction_notice: bool
    continues_next_page: bool
    enumerated_context: bool
    warnings: tuple[str, ...]


def _preceding_lines(text: str, position: int, count: int = 6) -> list[str]:
    head = text[:position]
    return [line for line in head.split("\n")[-count - 1:-1] if line.strip()]


def collect_boundary_evidence(document_text: str, span_start: int, span_end: int,
                              *, sentences: Sequence[tuple[int, int]],
                              cross_page_joins: Sequence[Any] = ()) -> BoundaryEvidence:
    body = document_text or ""
    span_text = body[span_start:span_end]
    warnings: list[str] = []

    previous_sentence = None
    for index, (start, end) in enumerate(sentences):
        if end > span_start:
            if index > 0:
                previous_sentence = body[sentences[index - 1][0]:sentences[index - 1][1]].strip()
            break

    heading = None
    table_header = None
    caption = None
    for line in reversed(_preceding_lines(body, span_start)):
        stripped = line.strip()
        if caption is None:
            found = _CAPTION.match(stripped)
            if found:
                caption = found.group(1).strip()
                warnings.append("OBJECT_FROM_CAPTION")
                continue
        if table_header is None and _TABLE_ROW.search(line) and _TABLE_ROW.search(span_text):
            table_header = " ".join(stripped.split())
            continue
        if heading is None and stripped and _HEADING.match(stripped) and \
                not stripped.endswith((".", ";", ",")) and len(stripped.split()) <= 14:
            heading = stripped
    if heading:
        warnings.append("MODALITY_FROM_HEADING")
    if table_header:
        warnings.append("PREDICATE_FROM_TABLE_HEADER")

    footnote = None
    if _FOOTNOTE_MARK.search(span_text):
        tail = body[span_end:span_end + 1200]
        for line in tail.split("\n"):
            if _FOOTNOTE_MARK.match(line.strip()) and len(line.strip()) > 12:
                footnote = " ".join(line.split())
                warnings.append("QUALIFIED_BY_FOOTNOTE")
                break

    post_attribution = None
    window = body[span_start:min(len(body), span_end + 240)]
    found = _POST_QUOTE_ATTRIBUTION.search(window)
    if found:
        post_attribution = " ".join(found.group(1).split()).strip(" '\"«»")
        warnings.append("ATTRIBUTION_AFTER_QUOTATION")

    context_window = body[max(0, span_start - 600):min(len(body), span_end + 600)]
    correction = bool(_CORRECTION_NOTICE.search(context_window))
    retraction = bool(_RETRACTION_NOTICE.search(context_window))
    if correction or retraction:
        warnings.append("SUPERSEDED_BY_CORRECTION_NOTICE")

    continues = False
    for join in cross_page_joins or ():
        try:
            left, right = join[0], join[1]
        except (TypeError, IndexError):
            continue
        if int(left) <= span_end <= int(right) or int(left) <= span_start <= int(right):
            continues = True
    if continues:
        warnings.append("CONTINUES_ON_NEXT_PAGE")

    # A numbered or bulleted stem on the preceding line means the span is one
    # item of an enumeration whose predicate lives in the list stem or the
    # heading above it.  Such a span is incomplete, not inadmissible.
    preceding = _preceding_lines(body, span_start, count=2)
    enumerated = bool(_LIST_ITEM.match(span_text.lstrip())) or any(
        _LIST_ITEM.match(line.strip()) or re.fullmatch(r"\s*\d{1,3}[.)]\s*", line)
        for line in preceding)
    if enumerated:
        warnings.append("LIST_ITEM_WITHOUT_STEM")

    return BoundaryEvidence(
        stable_id("boundary-evidence", str(span_start), str(span_end),
                  sha256(span_text)),
        heading, table_header, footnote, caption, post_attribution,
        previous_sentence, correction, retraction, continues, enumerated,
        tuple(warnings))


def _antecedent(previous_sentence: str | None, language: str) -> str | None:
    """The subject a non-referential pronoun most plausibly picks up."""
    if not previous_sentence:
        return None
    split = split_roles(previous_sentence, language)
    for candidate in (split.subject, split.object_or_value):
        cleaned = " ".join((candidate or "").split()).strip(" ,;:.")
        if cleaned and not _non_referential(cleaned, language) and len(cleaned) > 2:
            return cleaned[:120]
    return None


# ---------------------------------------------------------------------------
# Section 9.2 — the lattice
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Interpretation(Record):
    """One competing reading of one span boundary."""

    interpretation_id: str
    level: str
    span_start: int
    span_end: int
    text_sha256: str
    subject: str
    predicate: str
    object_or_value: str
    attribution: str | None
    polarity: str
    modality: str
    lifecycle_state: str
    evidence_act: str
    temporal_scope: tuple[str | None, str | None]
    temporal_basis: str
    geographic_scope: tuple[str, ...]
    numeric_values: tuple[str, ...]
    finite_clause: bool
    missing_roles: tuple[str, ...]
    warnings: tuple[str, ...]
    role_bearing: bool
    score: float
    parse_id: str


@dataclass(frozen=True)
class CandidateLattice(Record):
    """Every boundary considered for one candidate, and why one won."""

    lattice_id: str
    document_id: str
    candidate_id: str
    candidate_type: str
    seed_start: int
    seed_end: int
    language: str
    interpretations: tuple[Interpretation, ...]
    selected_interpretation_id: str | None
    selection_rationale: str
    boundary_evidence: BoundaryEvidence


def _missing_roles(candidate_type: str, split: RoleSplit, parse: SemanticParse,
                   attribution: str | None, unit_supplied: bool,
                   referent_supplied: bool) -> tuple[str, ...]:
    required = list(REQUIRED_ROLES.get(candidate_type, ()))
    # V5.1 demanded a unit and a referent from any CLAIM containing a number,
    # so every claim citing "Regulation (EU) 2024/1689" or "Article 43" was
    # held incomplete.  A unit belongs to a measurement, which is what the
    # NUMERIC_VALUE type is for; a claim that mentions a number does not
    # thereby become one.
    if candidate_type == "NUMERIC_VALUE" and parse.numeric_values:
        for extra in ("unit", "referent"):
            if extra not in required:
                required.append(extra)
    missing: list[str] = []
    for role in required:
        if role == "subject" and not split.subject.strip():
            missing.append(role)
        elif role == "predicate" and not split.predicate.strip():
            missing.append(role)
        elif role == "object_or_value" and not split.object_or_value.strip():
            missing.append(role)
        elif role == "attribution" and not (attribution or "").strip():
            missing.append(role)
        elif role == "temporal_scope" and parse.temporal_scope == (None, None):
            missing.append(role)
        elif role == "value" and not parse.numeric_values:
            missing.append(role)
        elif role == "unit" and not unit_supplied:
            missing.append(role)
        elif role == "referent" and not referent_supplied:
            missing.append(role)
    return tuple(missing)


_UNIT_HINT = re.compile(
    r"\b(?:%|percent|prozent|pour\s?cent|por\s?ciento|eur|euro|euros|usd|"
    r"million|millionen|millions|millones|billion|milliarden|milliards|"
    r"km|m|kg|mw|gw|kwh|tb|pb|flops|exaflops|hours?|stunden|heures|horas|"
    r"days?|tage|jours|d[íi]as|years?|jahre|ans|a[ñn]os)\b", re.IGNORECASE)


def _score(level: str, missing: tuple[str, ...], finite: bool,
           warnings: tuple[str, ...], role_bearing: bool = True) -> float:
    """Prefer the smallest defensible boundary that recovers every role.

    Completeness dominates; among complete readings the narrower span wins,
    because a wider span risks importing a different assertion's attribution.
    A boundary that is not role-bearing — one that crosses a sentence
    boundary although the seed is already a complete sentence — is scored
    below every role-bearing reading, so it can never supply a proposition
    the seed does not make.
    """
    score = 100.0
    if not role_bearing:
        score -= 60.0
    score -= 25.0 * len(missing)
    if finite:
        score += 12.0
    score -= 3.0 * _LEVEL_INDEX[level]
    for warning in warnings:
        if warning in {"TRUNCATED_AT_ABBREVIATION", "TRUNCATED_MID_SENTENCE",
                       "OPEN_PARENTHESIS", "OPEN_QUOTATION", "NON_REFERENTIAL_SUBJECT",
                       "LIST_ITEM_WITHOUT_STEM"}:
            score -= 9.0
        elif warning in {"ANTECEDENT_RESOLVED_FROM_CONTEXT",
                         "PREDICATE_FROM_TABLE_HEADER", "UNIT_FROM_TABLE_HEADER",
                         "OBJECT_FROM_CAPTION", "ATTRIBUTION_AFTER_QUOTATION",
                         "MODALITY_FROM_HEADING"}:
            score += 4.0
    return round(score, 3)


def _truncation_warnings(text: str) -> tuple[str, ...]:
    warnings: list[str] = []
    stripped = text.strip()
    if stripped.count("(") > stripped.count(")"):
        warnings.append("OPEN_PARENTHESIS")
    if (stripped.count('"') % 2) or (stripped.count("«") != stripped.count("»")):
        warnings.append("OPEN_QUOTATION")
    if stripped and not stripped.rstrip("\"'»«)]").endswith((".", "!", "?", "…", ":", ";")):
        warnings.append("TRUNCATED_MID_SENTENCE")
    trailing = re.search(r"(\S+)\.$", stripped)
    if trailing and trailing.group(1).casefold().rstrip(".") in _ABBREVIATIONS:
        warnings.append("TRUNCATED_AT_ABBREVIATION")
    if _LIST_ITEM.match(stripped):
        warnings.append("LIST_ITEM_WITHOUT_STEM")
    return tuple(warnings)


def build_lattice(candidate: ExtractionCandidate, document: NormalizedDocument,
                  *, language: str | None = None,
                  layout_annotation: Any = None,
                  document_date: str | None = None) -> CandidateLattice:
    """Generate and score every plausible boundary for one candidate."""
    body = document.text
    lang = language or getattr(document, "language", None) or "en"
    ntype = _normalize_type(candidate.candidate_type)
    sentences = _segment_cached(body)
    joins = tuple(getattr(layout_annotation, "cross_page_joins", ()) or ())
    evidence = collect_boundary_evidence(
        body, candidate.span_start, candidate.span_end,
        sentences=sentences, cross_page_joins=joins)

    sentence_bounds = _containing(sentences, candidate.span_start, candidate.span_end)
    sentence_index = next((i for i, s in enumerate(sentences)
                           if s == sentence_bounds), None)
    if sentence_index is not None and sentence_index > 0:
        expanded_bounds = (sentences[sentence_index - 1][0], sentence_bounds[1])
    else:
        expanded_bounds = sentence_bounds
    block_bounds = _block_bounds(body, candidate.span_start, candidate.span_end)

    levels: list[tuple[str, tuple[int, int]]] = [
        ("NARROW", (candidate.span_start, candidate.span_end)),
        ("SENTENCE", sentence_bounds),
        ("EXPANDED", expanded_bounds),
        ("BLOCK", block_bounds),
    ]

    # When the seed already spans a whole sentence, a wider boundary can only
    # import a neighbouring assertion; it may still contribute boundary
    # evidence, but it may not supply the seed's own missing roles.  When the
    # seed is a strict fragment of its sentence, widening is exactly the
    # repair the failures called for.
    seed_is_whole_sentence = (
        sentence_bounds == (candidate.span_start, candidate.span_end) or
        body[sentence_bounds[0]:sentence_bounds[1]].strip() ==
        body[candidate.span_start:candidate.span_end].strip())

    interpretations: list[Interpretation] = []
    seen: set[tuple[int, int]] = set()
    for level, (start, end) in levels:
        if end <= start or (start, end) in seen:
            continue
        seen.add((start, end))
        text = body[start:end]
        if not text.strip():
            continue
        context = body[max(0, start - 400):min(len(body), end + 400)]
        parse = parse_semantics(text, context, lang, document_date=document_date)
        split = split_roles(text, lang)

        truncation = list(_truncation_warnings(text))
        # A span that coincides with a whole sentence is not truncated, even
        # when the document supplies no terminal punctuation (headings, table
        # cells and final lines routinely have none).
        if (start, end) == sentence_bounds or (start, end) in set(sentences):
            if "TRUNCATED_MID_SENTENCE" in truncation:
                truncation.remove("TRUNCATED_MID_SENTENCE")
        warnings = list(split.warnings) + truncation
        subject = split.subject
        if "NON_REFERENTIAL_SUBJECT" in warnings:
            antecedent = _antecedent(evidence.previous_sentence, lang)
            if antecedent:
                subject = antecedent
                warnings.remove("NON_REFERENTIAL_SUBJECT")
                warnings.append("ANTECEDENT_RESOLVED_FROM_CONTEXT")

        predicate = split.predicate
        if not predicate.strip() and evidence.table_header:
            predicate = evidence.table_header
            warnings.append("PREDICATE_FROM_TABLE_HEADER")
        object_part = split.object_or_value
        if not object_part.strip() and evidence.caption:
            object_part = evidence.caption
            warnings.append("OBJECT_FROM_CAPTION")

        attribution = parse.attribution or evidence.post_quotation_attribution
        unit_supplied = bool(_UNIT_HINT.search(text)) or bool(
            evidence.table_header and _UNIT_HINT.search(evidence.table_header))
        if unit_supplied and not _UNIT_HINT.search(text) and evidence.table_header:
            warnings.append("UNIT_FROM_TABLE_HEADER")
        referent_supplied = bool(subject.strip() or object_part.strip())

        adjusted = RoleSplit(subject, predicate, object_part,
                             split.finite_clause or bool(predicate.strip()), ())
        missing = _missing_roles(ntype, adjusted, parse, attribution,
                                 unit_supplied, referent_supplied)
        if evidence.footnote:
            warnings.append("QUALIFIED_BY_FOOTNOTE")
        if evidence.continues_next_page:
            warnings.append("CONTINUES_ON_NEXT_PAGE")

        lifecycle_source = text
        if evidence.heading and level in {"EXPANDED", "BLOCK"}:
            lifecycle_source = f"{evidence.heading}. {text}"
        reading: LifecycleReading = derive_lifecycle(lifecycle_source, lang)

        if evidence.enumerated_context and "LIST_ITEM_WITHOUT_STEM" not in warnings:
            warnings.append("LIST_ITEM_WITHOUT_STEM")
        role_bearing = not (seed_is_whole_sentence and
                            level in {"EXPANDED", "BLOCK"})

        unique_warnings = tuple(dict.fromkeys(warnings))
        interpretations.append(Interpretation(
            stable_id("interpretation", candidate.candidate_id, level,
                      str(start), str(end)),
            level, start, end, sha256(text), subject, predicate, object_part,
            attribution, parse.polarity, parse.modality, reading.state,
            reading.evidence_act, parse.temporal_scope, parse.temporal_basis,
            parse.geographic_scope, parse.numeric_values,
            adjusted.finite_clause, missing, unique_warnings, role_bearing,
            _score(level, missing, adjusted.finite_clause, unique_warnings,
                   role_bearing),
            parse.parse_id))

    if interpretations:
        best = max(interpretations,
                   key=lambda item: (item.score, -_LEVEL_INDEX[item.level]))
        rationale = (f"selected the {best.level} boundary: score {best.score}, "
                     f"{len(best.missing_roles)} required role(s) missing, "
                     f"chosen over {len(interpretations) - 1} alternative(s)")
        selected_id: str | None = best.interpretation_id
    else:
        rationale = "no interpretable boundary could be built over this span"
        selected_id = None

    return CandidateLattice(
        stable_id("lattice", candidate.candidate_id, document.document_id),
        document.document_id, candidate.candidate_id, ntype,
        candidate.span_start, candidate.span_end, lang,
        tuple(interpretations), selected_id, rationale, evidence)


# ---------------------------------------------------------------------------
# Section 9.5 — admission contract
# ---------------------------------------------------------------------------

ADMISSION_STAGES = ("STRUCTURALLY_VALID", "SEMANTICALLY_PARSED", "EVIDENCE_BOUND",
                    "ACCEPTED_CANDIDATE", "QUARANTINED", "REJECTED")

CANDIDATE_FUNCTIONS = frozenset({
    "ANALYTICALLY_MATERIAL", "SUPPORTING_DETAIL", "NAVIGATIONAL_METADATA",
    "DOCUMENT_STRUCTURE", "DUPLICATE", "NOISE", "UNKNOWN_VALUE",
})

_CHROME_FUNCTION = {
    "ARCHIVAL_WRAPPER": "DOCUMENT_STRUCTURE",
    "COOKIE_NOTICE": "NAVIGATIONAL_METADATA",
    "NAVIGATION_CHROME": "NAVIGATIONAL_METADATA",
    "CONTACT_BOILERPLATE": "NAVIGATIONAL_METADATA",
    "LEGAL_BOILERPLATE": "DOCUMENT_STRUCTURE",
    "SOCIAL_SHARE": "NAVIGATIONAL_METADATA",
    "PRINT_OR_EXPORT": "NAVIGATIONAL_METADATA",
    "SUBSCRIPTION_PROMPT": "NAVIGATIONAL_METADATA",
}

# Warnings that mean the span is recoverable with more context rather than
# inadmissible: the decision is SEMANTICALLY_PARSED, not REJECTED.
_RECOVERABLE = frozenset({
    "TRUNCATED_AT_ABBREVIATION", "TRUNCATED_MID_SENTENCE", "OPEN_PARENTHESIS",
    "OPEN_QUOTATION", "CONTINUES_ON_NEXT_PAGE", "LIST_ITEM_WITHOUT_STEM",
    "NON_REFERENTIAL_SUBJECT",
})

# The subset that means the recorded span was cut, as opposed to being
# structurally incomplete.  A cut span is always recoverable.
_TRUNCATION_WARNINGS = frozenset({
    "TRUNCATED_AT_ABBREVIATION", "TRUNCATED_MID_SENTENCE", "OPEN_PARENTHESIS",
    "OPEN_QUOTATION", "CONTINUES_ON_NEXT_PAGE",
})

_ASSERTION_TYPES = frozenset({"CLAIM", "RELATION", "EVENT"})
_INTERROGATIVE = re.compile(r"\?\s*[\"'»›)\]]*$")


_TERMINATORS = ".!?\u2026"

# Lines at least this long that end without punctuation are wrapped sentence
# continuations rather than headings.
_WRAP_LINE_MINIMUM = 60

# A bibliographic reference to a legal act.  V5.1 typed OJ footnote citations
# as claims and they reached the clean corpus; a citation asserts nothing
# about the world, so it cannot carry a CLAIM, RELATION or EVENT type.
_LEGAL_CITATION = re.compile(
    r"^\s*(?:[(\[]\s*(?:\*|\d{1,3})\s*[)\]]\s*)?"
    r"(?:council\s+|commission\s+|european\s+parliament\s+)?"
    r"(?:regulations?|directives?|decisions?|verordnung|richtlinie|beschluss|"
    r"r\u00e8glement|d\u00e9cision|reglamento|directiva|decisi\u00f3n|regolamento|"
    r"direttiva|decisione)\b"
    r"[^.]{0,160}?\d{1,4}\s*/\s*\d{2,4}", re.IGNORECASE)


def is_legal_citation(text: str) -> bool:
    """Is this span a bibliographic reference to a legal act?"""
    return bool(_LEGAL_CITATION.match(text or ""))


def span_alignment(document_text: str, span_start: int, span_end: int) -> tuple[bool, bool]:
    """Does the recorded span begin and end at sentence boundaries?

    Measured against the document characters rather than against segmentation
    output, so a segmentation quirk in one document cannot turn a well-formed
    sentence into a fragment.  A candidate that starts inside a sentence is a
    mis-boundaried extraction: the lattice can still read what the text says,
    but the span as recorded is not a well-formed analytical item, and no
    amount of neighbouring context makes it one.

    A newline is not by itself a sentence boundary.  PDF text layers wrap
    lines mid-sentence, which is how V5.1 produced fragments such as
    "Estado miembro en cuesti\u00f3n." and typed them as claims.
    """
    body = document_text or ""
    head = body[:span_start].rstrip(" \t\"'\u00ab\u00bb([")
    if not head:
        starts = True
    elif head[-1] in _TERMINATORS + ":;":
        # A colon or semicolon closes the preceding clause and introduces new
        # material, which is exactly how a quotation is presented.
        starts = True
    elif head[-1] == "\n":
        previous = head.rstrip("\n").rsplit("\n", 1)[-1].rstrip(" \t\"'\u00bb)]")
        # A line that ends on a determiner, preposition or conjunction demands
        # a continuation, so the span below it is the rest of that sentence
        # rather than a new one.  Failing that, a short unpunctuated line is a
        # heading or a label and does end the preceding unit; a long one is a
        # wrapped sentence, which is the PDF line-wrap fragment case.
        last_token = _TOKEN.findall(previous)[-1].casefold() if _TOKEN.search(previous) else ""
        if last_token in _FUNCTION_WORDS:
            starts = False
        else:
            starts = (not previous or previous[-1] in _TERMINATORS + ":;"
                      or len(previous) < _WRAP_LINE_MINIMUM)
    else:
        starts = False

    span_text = body[span_start:span_end]
    first_alpha = next((ch for ch in span_text if ch.isalpha()), "")
    if first_alpha and first_alpha.islower():
        # No sentence in any supported language opens with a lowercase word.
        starts = False
    opener = span_text.lstrip(" \t\"'\u00ab([")[:2]
    if opener[:1].isdigit() and opener[1:2] not in {".", ")"}:
        # A bare number opening a span is a wrapped year or reference, not a
        # numbered list item.
        starts = False

    trimmed = span_text.rstrip(" \t\"'\u00bb)]")
    tail = body[span_end:].lstrip(" \t\"'\u00bb)]")
    if not tail:
        ends = True
    elif trimmed and trimmed[-1] in _TERMINATORS:
        # An ordinal or abbreviation full stop does not end a sentence.
        ends = not _is_abbreviation(body, span_start + len(trimmed) - 1)
    else:
        ends = tail[0] == "\n"
    return starts, ends


@dataclass(frozen=True)
class ResolutionDecision(Record):
    """The production decision for one candidate, with its full lattice."""

    decision_id: str
    candidate_id: str
    document_id: str
    stage: str
    candidate_function: str
    selected_level: str | None
    selected_span: tuple[int, int] | None
    lifecycle_state: str
    evidence_act: str
    polarity: str | None
    modality: str | None
    temporal_scope: tuple[str | None, str | None]
    subject: str | None
    predicate: str | None
    object_or_value: str | None
    attribution: str | None
    missing_roles: tuple[str, ...]
    expansion_request: tuple[str, ...]
    boundary_warnings: tuple[str, ...]
    alternative_count: int
    rationale: str
    lattice_id: str
    capability_failure: CapabilityOutcome | None
    recorded_time: str

    def __post_init__(self) -> None:
        if self.stage not in ADMISSION_STAGES:
            raise ValueError(f"unknown admission stage: {self.stage}")
        if self.candidate_function not in CANDIDATE_FUNCTIONS:
            raise ValueError(f"unknown candidate function: {self.candidate_function}")
        if self.stage == "ACCEPTED_CANDIDATE" and self.missing_roles:
            raise ValueError("an accepted candidate may not miss a required role")
        if self.capability_failure is not None and self.stage == "ACCEPTED_CANDIDATE":
            raise ValueError("a capability failure may never accept a candidate")


def _decision(candidate: ExtractionCandidate, lattice: CandidateLattice,
              best: Interpretation | None, stage: str, function: str,
              rationale: str, *, expansion: tuple[str, ...] = (),
              failure: CapabilityOutcome | None = None) -> ResolutionDecision:
    return ResolutionDecision(
        stable_id("resolution", candidate.candidate_id, stage, function),
        candidate.candidate_id, lattice.document_id, stage, function,
        best.level if best else None,
        (best.span_start, best.span_end) if best else None,
        best.lifecycle_state if best else "UNKNOWN",
        best.evidence_act if best else "ACTOR_INTENTION",
        best.polarity if best else None, best.modality if best else None,
        best.temporal_scope if best else (None, None),
        best.subject if best else None, best.predicate if best else None,
        best.object_or_value if best else None, best.attribution if best else None,
        best.missing_roles if best else (), expansion,
        best.warnings if best else (), max(0, len(lattice.interpretations) - 1),
        rationale, lattice.lattice_id, failure, now_utc())


def resolve_candidate(candidate: ExtractionCandidate, document: NormalizedDocument,
                      layout_annotation: Any = None, *,
                      language: str | None = None,
                      document_date: str | None = None) -> ResolutionDecision:
    """Decide one candidate by comparing alternative semantic interpretations.

    The decision order is: provenance integrity, structural quarantine
    (layout regions and web/document chrome), type admissibility, then the
    lattice.  A span that cannot carry its type is rejected; a span that is
    merely truncated or missing an antecedent asks for context; furniture is
    quarantined.  V5.1 collapsed all three into REJECTED.
    """
    if candidate.document_id != document.document_id:
        raise ValueError("candidate/document provenance mismatch")
    if document.text[candidate.span_start:candidate.span_end] != candidate.original_text:
        raise ValueError("candidate span fabrication or derivative drift")

    lang = language or getattr(document, "language", None) or "en"
    ntype = _normalize_type(candidate.candidate_type)
    lattice = build_lattice(candidate, document, language=lang,
                            layout_annotation=layout_annotation,
                            document_date=document_date)
    best = next((item for item in lattice.interpretations
                 if item.interpretation_id == lattice.selected_interpretation_id), None)

    for start, end, kind in _quarantined_regions(layout_annotation):
        if candidate.span_start < end and candidate.span_end > start:
            return _decision(candidate, lattice, best, "QUARANTINED",
                             _structural_function(kind),
                             f"span overlaps layout-quarantined region kind={kind}")

    chrome_class = chrome.classify_chrome(candidate.original_text)
    if chrome_class:
        return _decision(candidate, lattice, best, "QUARANTINED",
                         _CHROME_FUNCTION[chrome_class],
                         f"span is {chrome_class}: site or archive furniture, not "
                         "an assertion the document makes about the world")

    if ntype not in REQUIRED_ROLES:
        return _decision(
            candidate, lattice, best, "REJECTED", "UNKNOWN_VALUE",
            "candidate type is outside the required type ontology",
            failure=capability_outcome(
                subject_kind="EXTRACTION_CANDIDATE", subject_id=candidate.candidate_id,
                outcome="SYSTEM_CAPABILITY_FAILURE",
                rationale="candidate carries a type the semantic layer cannot represent",
                capability_failure_class="SEMANTIC_TYPE_ERROR"))

    if ntype not in {"DATE", "NUMERIC_VALUE"} and not re.search(
            r"[A-Za-zÀ-ÿ]{2}", candidate.original_text):
        return _decision(candidate, lattice, best, "REJECTED", "NOISE",
                         "span contains no linguistic content")

    if best is None:
        return _decision(
            candidate, lattice, None, "REJECTED", "UNKNOWN_VALUE",
            "no interpretable boundary could be built over this span",
            failure=capability_outcome(
                subject_kind="EXTRACTION_CANDIDATE", subject_id=candidate.candidate_id,
                outcome="SYSTEM_CAPABILITY_FAILURE",
                rationale="the resolver produced no interpretation for a span with text",
                capability_failure_class="PARSER_INCAPABILITY"))

    if ntype == "NUMERIC_VALUE" and not best.numeric_values:
        return _decision(
            candidate, lattice, best, "REJECTED", "UNKNOWN_VALUE",
            "numeric candidate without a numeric value at any boundary",
            failure=capability_outcome(
                subject_kind="EXTRACTION_CANDIDATE", subject_id=candidate.candidate_id,
                outcome="SYSTEM_CAPABILITY_FAILURE",
                rationale="span typed as numeric value carries no number the parser can bind",
                capability_failure_class="SEMANTIC_TYPE_ERROR"))

    if ntype in _ASSERTION_TYPES and _INTERROGATIVE.search(candidate.original_text.strip()):
        return _decision(
            candidate, lattice, best, "REJECTED", "UNKNOWN_VALUE",
            "the span is a question; an interrogative asserts nothing and cannot "
            "carry a claim, relation or event")

    if ntype in _ASSERTION_TYPES and is_legal_citation(candidate.original_text):
        return _decision(
            candidate, lattice, best, "REJECTED", "SUPPORTING_DETAIL",
            "the span is a bibliographic reference to a legal act; a citation "
            "identifies a document and asserts nothing about the world")

    narrow = next((item for item in lattice.interpretations
                   if item.level == "NARROW"), best)
    block = next((item for item in lattice.interpretations
                  if item.level == "BLOCK"), None)
    truncation = set(narrow.warnings) & _TRUNCATION_WARNINGS
    starts_clean, ends_clean = span_alignment(
        document.text, candidate.span_start, candidate.span_end)

    # A span that begins inside a sentence is a mis-boundaried extraction.  No
    # bounded expansion repairs it, because the recorded span — the thing the
    # decision is about — is the wrong unit of evidence.
    if not starts_clean and not best.finite_clause:
        return _decision(
            candidate, lattice, best, "REJECTED", "UNKNOWN_VALUE",
            "the recorded span begins inside a sentence and carries no finite "
            "clause; it is a line-wrap fragment and cannot carry its type")

    if ntype in {"CLAIM", "RELATION", "EVENT"} and not best.finite_clause:
        if truncation:
            return _decision(
                candidate, lattice, best, "SEMANTICALLY_PARSED", "UNKNOWN_VALUE",
                f"the recorded span is truncated ({sorted(truncation)}); the "
                "assertion is recoverable from wider context and the span is not "
                "inadmissible as evidence", expansion=("predicate",))
        if ends_clean and block is not None and block.finite_clause:
            return _decision(
                candidate, lattice, best, "SEMANTICALLY_PARSED", "UNKNOWN_VALUE",
                "the span carries no finite clause but its enclosing block does; "
                "the predicate lives in the list stem or heading above it and "
                "wider context is required before a decision",
                expansion=("predicate",))
        return _decision(
            candidate, lattice, best, "REJECTED", "UNKNOWN_VALUE",
            "no boundary in the lattice yields propositional content and the "
            "enclosing block supplies no predicate; the span is a noun phrase "
            "and cannot carry a claim")

    if best.missing_roles:
        return _decision(
            candidate, lattice, best, "SEMANTICALLY_PARSED", "UNKNOWN_VALUE",
            f"required roles {best.missing_roles} were not recovered at any "
            "boundary the lattice could build; wider or structural context is "
            "required before a decision", expansion=best.missing_roles)

    # Acceptance requires the recorded span itself to be a well-formed unit.
    # A context-supplied subject or a span that starts or ends inside a
    # sentence both mean the reviewer cannot verify the item as recorded.
    if not (starts_clean and ends_clean):
        return _decision(
            candidate, lattice, best, "SEMANTICALLY_PARSED", "UNKNOWN_VALUE",
            "the recorded span does not start and end at sentence boundaries; "
            "the assertion is recoverable but the span as recorded is a fragment",
            expansion=("subject",))
    if "ANTECEDENT_RESOLVED_FROM_CONTEXT" in best.warnings:
        return _decision(
            candidate, lattice, best, "SEMANTICALLY_PARSED", "UNKNOWN_VALUE",
            "the subject is a pronominal adverb whose antecedent was recovered "
            f"from the preceding sentence ({best.subject!r}); the span alone does "
            "not identify what the claim is about",
            expansion=("subject",))
    if "NON_REFERENTIAL_SUBJECT" in best.warnings:
        return _decision(
            candidate, lattice, best, "SEMANTICALLY_PARSED", "UNKNOWN_VALUE",
            "the subject refers to nothing on its own and no antecedent was "
            "recoverable from the preceding sentence", expansion=("subject",))

    if ntype in _EXACT_SPAN_TYPES and candidate.mapping_precision not in _EXACT_PRECISIONS:
        return _decision(
            candidate, lattice, best, "EVIDENCE_BOUND", "UNKNOWN_VALUE",
            f"{ntype} requires exact span mapping; {candidate.mapping_precision} "
            "is insufficient for acceptance")

    function = "ANALYTICALLY_MATERIAL" if ntype in _MATERIAL_TYPES else "SUPPORTING_DETAIL"
    return _decision(candidate, lattice, best, "ACCEPTED_CANDIDATE", function,
                     f"every required role recovered at the {best.level} boundary; "
                     f"{lattice.selection_rationale}")


# ---------------------------------------------------------------------------
# Section 9.6 — decision-quality accounting
# ---------------------------------------------------------------------------

def decision_quality(decisions: Iterable[ResolutionDecision | Mapping[str, Any]],
                     truth: Mapping[str, str]) -> dict[str, Any]:
    """Score production decisions, not merely generated candidates.

    ``truth`` maps candidate_id to the adjudicated stage.  Reported counts are
    the ones Section 9.6 requires, including the wrong-admission counts that
    the V5.1 sample could not measure because it drew no accepted candidate.
    """
    counters = {
        "correct_acceptances": 0, "correct_rejections": 0, "wrong_admissions": 0,
        "missed_recoverable_assertions": 0, "correct_quarantines": 0,
        "other_mismatches": 0, "scored": 0,
    }
    for item in decisions:
        get = (item.get if isinstance(item, Mapping)
               else lambda key, default=None, obj=item: getattr(obj, key, default))
        candidate_id = str(get("candidate_id"))
        if candidate_id not in truth:
            continue
        counters["scored"] += 1
        actual, expected = str(get("stage")), truth[candidate_id]
        if actual == expected:
            if expected == "ACCEPTED_CANDIDATE":
                counters["correct_acceptances"] += 1
            elif expected == "REJECTED":
                counters["correct_rejections"] += 1
            elif expected == "QUARANTINED":
                counters["correct_quarantines"] += 1
            continue
        if actual == "ACCEPTED_CANDIDATE":
            counters["wrong_admissions"] += 1
        elif actual == "REJECTED" and expected in {
                "ACCEPTED_CANDIDATE", "SEMANTICALLY_PARSED", "EVIDENCE_BOUND",
                "QUARANTINED"}:
            counters["missed_recoverable_assertions"] += 1
        else:
            counters["other_mismatches"] += 1
    total = max(1, counters["scored"])
    correct = (counters["correct_acceptances"] + counters["correct_rejections"] +
               counters["correct_quarantines"])
    counters["semantic_correctness"] = round(correct / total, 4)
    counters["wrong_admission_rate"] = round(counters["wrong_admissions"] / total, 4)
    counters["over_rejection_rate"] = round(
        counters["missed_recoverable_assertions"] / total, 4)
    return counters
