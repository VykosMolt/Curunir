"""P6.3 workstream A — the regression pins for the declared-language ingress.

WHY THIS FILE EXISTS
--------------------
P6.2 shipped R5: the declared language tag is normalised ONCE, at each public
ingress of `roles_v2`, so that the module's ten comparison sites and the
family selection in `clauses.py` read one value derived one way.  R5 is a
DATA-INTEGRITY repair with a measured publication consequence -- on the frozen
axis body below, `language="ru"` published EVIDENCE_BOUND while `language="RU"`
published QUARANTINED, ten fields apart including the disposition.

The independent adversarial seat SEM-9r4 then measured that R5 was ENTIRELY
UNPINNED: replacing `_declared_language` with the identity function left the
whole operational suite green (3625 passed, 0 failed), while forcing it to
return the constant `"ru"` failed 53 nodes -- so the harness could see mutations
of that exact symbol and simply never observed the one that matters.  A
selector-affecting repair with no regression pin can be silently reverted.

Three properties are pinned here, each with a non-vacuity arm:

  A1  SPELLING INVARIANCE.  Every spelling of a declared language publishes
      the byte-identical record its canonical two-letter code publishes, on
      bodies that reach the decisive path.  This fails if the ingress
      normalisation is removed, weakened, or moved off any of the three
      ingresses.

  A2  TOTALITY AND REFUSAL PRECEDENCE.  `language` is an unvalidated caller
      value -- nothing between `v5_1/campaign.py:201`, `v4/pipeline.py:42` and
      `v5_4/invariants.py:299` enforces its `str` annotation, and a TRUTHY
      non-string passes `invariants.py:299`'s ``or "en"`` untouched.  A
      non-string must therefore produce the lawful "no declared language"
      record and never an exception, on BOTH bind routes; and the layout
      refusal, which reads nothing from `language` at all, must be reached
      BEFORE the tag is normalised.

  A3  WHICH RULE.  The normalisation is `strip`, then `casefold`, then the
      two-character prefix -- the rule `v5_1/extraction.py:253`,
      `v5_2/lifecycle.py:520` and `v5_4/calibration.py:80` already use.  Under
      the older `.lower()[:2]` a leading-whitespace tag was unstable across the
      tree: `_langs(" de") == "de"` selected the German lexicon in the V5.2
      splitter while `" de"` selected no German machinery here.  The
      whitespace spellings in the families below fail if that alignment is
      reverted.

WHAT THIS FILE DOES NOT CLAIM
-----------------------------
It does not claim the normalisation IDENTIFIES languages.  It is a two-letter
prefix rule inherited from `clauses.py:376` and `_langs`, so exonyms truncate
("german" -> "ge", "swedish" -> "sw") and three-letter codes collide
("est" -> "es", "run" -> "ru").  Those are pre-existing system-wide properties
that this module is being made CONSISTENT with, not properties it is being
asked to fix; the negative-control arm below pins the exonym behaviour as it
is so that a future exonym table is a visible change rather than a silent one.

It does not read production to obtain an expected value.  Every comparison is
production against ITSELF at a different argument, and every literal below --
the spelling families, the canonical codes, the field list -- is a literal of
this file.

P6.4 -- THE WHITESPACE ALPHABET THIS FILE SWEEPS
------------------------------------------------
A3's whitespace class was carried here as {" ", "\t", "\n"} plus, in five of
the seven families, U+00A0.  The final adversarial review's F-1/F-2 was that a
probe whose whitespace alphabet is the alphabet the code deletes cannot
observe the class it certifies, and this file was in the same position twice
over:

  * in the TAG, the four characters it swept are a small corner of the class
    `str.strip()` -- and therefore `_declared_language` -- actually removes;

  * in the BODY, it swept none at all.  Measured on the pre-P6.4 bytes, 135 of
    864 points built from THIS FILE'S OWN bodies (`operative_ru`,
    `deontic_ar`) raised an uncaught `IndexError` out of `bind` when the body
    carried a leading U+00A0 or CR: `roles_v2._m15_predicate_span` licensed
    `lead.split()[0]` on a lead that `_M14_SPAN_TRIM` -- a NARROWER set than
    `str.isspace()` -- had not emptied.  No record at all was produced.

So the alphabet is widened in both positions:

  * `_WHITESPACE_ALPHABET` and `_whitespace_spellings` give every family every
    whitespace spelling, so A3's "strip, then casefold, then two characters"
    is asserted over the whole class rather than over a corner of it;

  * `test_a_whitespace_led_body_still_publishes_the_canonical_record` re-asks
    A1's own question on bodies that BEGIN with each of those characters.
    That arm errors on the pre-P6.4 binder.

`test_the_whitespace_alphabet_is_complete` derives the required contents from
`str.isspace()` rather than from a remembered list, so the alphabet can be
neither silently narrowed nor silently outgrown.
"""

from __future__ import annotations

import ast
import dataclasses
import decimal
import inspect
import textwrap

import pytest

from curunir_operational.v5_8_1 import roles_v2 as V2
from curunir_operational.v5_8_1 import structure as ST

pytestmark = pytest.mark.no_db


# ===========================================================================
# The comparison instrument
# ===========================================================================

#: Every published field except the wall clock, which is not a function of any
#: input and is never comparable across two calls.
_COMPARED_FIELDS = tuple(
    field.name for field in dataclasses.fields(V2.RoleBindingV2Record)
    if field.name != "recorded_time")


def _published(record) -> dict:
    return {name: getattr(record, name) for name in _COMPARED_FIELDS}


#: Bodies chosen so that the declared tag DECIDES something.  `decisive_ru` is
#: the frozen axis body on which the case defect flipped the disposition;
#: `fragment_ru` and `deontic_ar` are the two bodies on which R5's second,
#: tightening direction was measured; the rest exercise the German, English,
#: Spanish, Italian and French machinery and the non-proposition routes.
_BODIES = {
    "decisive_ru": dict(
        text="предотвратимой смертности, что особенно ярко проявилось в ходе "
             "пандемии COVID-19,",
        left_context="кислороду и что отсутствие этого доступа является "
                     "фактором, способствующим",
        heading="", content_region_type="UNKNOWN_STRUCTURAL_REGION"),
    "operative_ru": dict(
        text="Рекомендует государствам-членам представить доклад Секретариату.",
        left_context="Комитет рассмотрел доклад.", heading="",
        content_region_type="PRIMARY_PROPOSITION_CONTENT"),
    "fragment_ru": dict(
        text="но и позволяют добиться жизнестойкости в долгосрочной "
             "перспективе, что способствует",
        left_context="психосоциальной поддержке, которые не только "
                     "удовлетворяют насущные потребности,",
        heading="", content_region_type="UNKNOWN_STRUCTURAL_REGION"),
    "pronominal_ru": dict(
        text="что они представляют доклад Секретариату в установленный срок,",
        left_context="Комитет рассмотрел доклад,", heading="",
        content_region_type="UNKNOWN_STRUCTURAL_REGION"),
    "deontic_ar": dict(
        text="وينبغي علاج الأشخاص المعرّضين لخطر كبير بالأدوية المضادة "
             "للفيروسات في أقرب وقت ممكن.",
        left_context="التماس الرعاية الطبية عند ظهور الأعراض.", heading="",
        content_region_type="PRIMARY_PROPOSITION_CONTENT"),
    "subordinate_de": dict(
        text="sie hierzu aus rechtlichen Gründen nicht in der Lage ist;",
        left_context="1.", heading="§ 5",
        content_region_type="PRIMARY_PROPOSITION_CONTENT"),
    "german_lexical_de": dict(
        text="Die Behörde hat die Maßnahmen zu treffen, die sie für "
             "erforderlich hält.",
        left_context="Der Rat prüfte den Bericht.", heading="",
        content_region_type="PRIMARY_PROPOSITION_CONTENT"),
    "committee_en": dict(
        text="The Committee shall review the report of the Secretariat.",
        left_context="The Council considered the matter.", heading="",
        content_region_type="PRIMARY_PROPOSITION_CONTENT"),
    "operative_es": dict(
        text="El Ministerio de Sanidad adopta las medidas necesarias para la "
             "proteccion de la salud publica.",
        left_context="El Consejo examino el informe.", heading="",
        content_region_type="PRIMARY_PROPOSITION_CONTENT"),
    "operative_it": dict(
        text="Il Ministero della salute adotta le misure necessarie per la "
             "tutela della salute pubblica.",
        left_context="Il Consiglio ha esaminato la relazione.", heading="",
        content_region_type="PRIMARY_PROPOSITION_CONTENT"),
    "operative_fr": dict(
        text="Le Ministere adopte les mesures necessaires pour la protection "
             "de la sante publique.",
        left_context="Le Conseil a examine le rapport.", heading="",
        content_region_type="PRIMARY_PROPOSITION_CONTENT"),
    "date_line": dict(
        text="A70/12  Agenda item 13.1  22 May 2017",
        left_context="", heading="",
        content_region_type="UNKNOWN_STRUCTURAL_REGION"),
}

_SHAPES = ("none", "plain", "unique", "ambiguous")


def _context_of(shape: str):
    if shape == "none":
        return None
    base = ST.empty_context(document_id="p63-doc", region_id="p63-region",
                            reading_order_state="READING_ORDER_ESTABLISHED")
    if shape == "plain":
        return base
    if shape == "unique":
        return dataclasses.replace(
            base, governing_clause_candidates=("p63-gov-a",),
            governing_clause_relation_types=("LIST_ITEM_OF",),
            governing_clause_confidences=(0.91,))
    return dataclasses.replace(
        base, governing_clause_candidates=("p63-gov-a", "p63-gov-b"),
        governing_clause_relation_types=("LIST_ITEM_OF", "LIST_ITEM_OF"),
        governing_clause_confidences=(0.91, 0.88))


def _bind(body_name: str, language, shape: str):
    spec = _BODIES[body_name]
    return V2.bind(
        candidate_id="p63-declared-language", text=spec["text"],
        language=language, left_context=spec["left_context"],
        heading=spec["heading"],
        content_region_type=spec["content_region_type"],
        structural_context=_context_of(shape))


# ===========================================================================
# A1 -- spelling invariance
# ===========================================================================

#: LITERAL.  Each canonical two-letter code and the spellings a real caller
#: supplies for it.  The `ru` family is the one SEM-9r4's repair note named;
#: the `ar` family is the second required one; the whitespace forms are the
#: A3 alignment's own class and are present in EVERY family, because a rule
#: that stripped only some tags would be a new inconsistency, not a repair.
#: LITERAL.  Every character `str.isspace()` accepts.  `_declared_language`
#: is `strip().casefold()[:2]` and `str.strip()` removes exactly this set, so
#: this is the exact class over which A3's rule must be stable -- not the four
#: characters this file used to carry.  `roles_v2._M14_SPAN_TRIM`, whose
#: whitespace is only {" ", "\t", "\n"}, is a DIFFERENT and narrower set, and
#: confusing the two is what produced F-1.
_WHITESPACE_ALPHABET = (
    " ", "\t", "\n",
    "\x0b", "\x0c", "\r",
    "\x1c", "\x1d", "\x1e", "\x1f",
    "\x85", "\xa0",
    "\u1680",
    "\u2000", "\u2001", "\u2002", "\u2003", "\u2004", "\u2005",
    "\u2006", "\u2007", "\u2008", "\u2009", "\u200a",
    "\u2028", "\u2029", "\u202f", "\u205f", "\u3000",
)


def _whitespace_spellings(code: str) -> tuple:
    """Every whitespace spelling of `code`, over the whole alphabet.

    Built from `_WHITESPACE_ALPHABET`, which is a literal of this file, so the
    families stay literals of this file -- production is never consulted for
    an expected value.
    """
    out = []
    for character in _WHITESPACE_ALPHABET:
        out.extend((character + code,
                    code + character,
                    character + code + character,
                    character * 2 + code.upper() + character))
    return tuple(out)


_SPELLING_FAMILIES = {
    "ru": ("ru", "RU", "Ru", "rU", "russian", "Russian", "RUSSIAN",
           "ru-RU", "ru_UA", "rus", "RUS", "russisch",
           "  ru", "  ru  ", " RU ", "\tRussian",
           *_whitespace_spellings("ru")),
    "ar": ("ar", "AR", "Ar", "aR", "arabic", "Arabic", "ARABIC",
           "ar-EG", "ar_SA", "ara", "ARA",
           "  ar", "  ar  ", " AR ", "\tArabic",
           *_whitespace_spellings("ar")),
    "de": ("de", "DE", "De", "deutsch", "Deutsch", "de-AT", "de_CH", "deu",
           "  de  ", " DE ", *_whitespace_spellings("de")),
    "en": ("en", "EN", "En", "english", "English", "en-GB", "en_US", "eng",
           "  en  ", " EN ", *_whitespace_spellings("en")),
    "es": ("es", "ES", "Es", "espanol", "es-MX", "es_ES", "esp",
           "  es  ", " ES ", *_whitespace_spellings("es")),
    "fr": ("fr", "FR", "Fr", "french", "French", "fr-CA", "fr_BE", "fra",
           "  fr  ", " FR ", *_whitespace_spellings("fr")),
    "it": ("it", "IT", "It", "italian", "Italian", "it-CH", "it_IT", "ita",
           "  it  ", " IT ", *_whitespace_spellings("it")),
}

#: LITERAL.  Tags that denote NO language this module has machinery for.  They
#: are the reference for the non-vacuity arm: if the canonical record equalled
#: the unrecognised record everywhere, spelling invariance would be trivial.
_UNRECOGNISED = ("zz", "xx", "qq", "zxx", "und", "mul", "x-custom", "!!",
                 "0", "-1")


@pytest.mark.parametrize("code", sorted(_SPELLING_FAMILIES))
@pytest.mark.parametrize("body_name", sorted(_BODIES))
def test_every_spelling_of_a_language_publishes_the_canonical_record(
        body_name, code):
    """A1.  The whole published record, not just the disposition.

    Reverting the ingress normalisation -- to the identity, to a rule applied
    at only some of the three ingresses, or to `.lower()[:2]` without the
    strip -- moves at least one of these spellings off the canonical answer.
    """
    for shape in _SHAPES:
        canonical = _published(_bind(body_name, code, shape))
        for spelling in _SPELLING_FAMILIES[code]:
            observed = _published(_bind(body_name, spelling, shape))
            assert observed == canonical, (
                body_name, code, repr(spelling), shape,
                {k: (canonical[k], observed[k]) for k in canonical
                 if canonical[k] != observed[k]})


@pytest.mark.parametrize("code", sorted(_SPELLING_FAMILIES))
def test_the_spelling_invariance_arm_is_not_vacuous(code):
    """Non-vacuity.  The canonical tag must DECIDE something on at least one
    body, or the equality above would hold for a binder that ignored the
    parameter entirely.

    This is the arm that makes the identity mutation observable: under the
    identity, a non-canonical spelling behaves exactly as an unrecognised tag,
    so the two records this arm requires to DIFFER become the two records the
    arm above requires to be EQUAL, and both cannot hold at once.
    """
    deciding = []
    for body_name in sorted(_BODIES):
        for shape in _SHAPES:
            canonical = _published(_bind(body_name, code, shape))
            for unknown in _UNRECOGNISED:
                if _published(_bind(body_name, unknown, shape)) != canonical:
                    deciding.append((body_name, shape, unknown))
    assert deciding, (
        f"declared language {code!r} changed no published field on any of "
        f"{len(_BODIES)} bodies x {len(_SHAPES)} context shapes against "
        f"{len(_UNRECOGNISED)} unrecognised tags; spelling invariance for "
        f"this family would be vacuous")


def test_the_case_defect_body_still_decides_the_disposition():
    """The specific data-integrity defect S8R3-F1, pinned by name.

    On the frozen axis body, the declared Russian tag and an unrecognised tag
    publish DIFFERENT dispositions, and every Russian spelling publishes the
    declared tag's one.  Pre-R5 the second half of that sentence was false for
    "RU", "Russian", "ru-RU" and "rus".
    """
    decisive = _published(_bind("decisive_ru", "ru", "none"))
    unknown = _published(_bind("decisive_ru", "zz", "none"))
    assert decisive["final_extraction_disposition"] != \
        unknown["final_extraction_disposition"]
    for spelling in _SPELLING_FAMILIES["ru"]:
        observed = _published(_bind("decisive_ru", spelling, "none"))
        assert observed["final_extraction_disposition"] == \
            decisive["final_extraction_disposition"], repr(spelling)
        assert observed == decisive, repr(spelling)


def test_the_spelling_families_are_not_a_restatement_of_the_corpus():
    """Floor: the families reach past the seven canonical codes the frozen
    corpus carries, and every family carries the whitespace class A3 aligned
    and the case class R5 closed."""
    for code, family in sorted(_SPELLING_FAMILIES.items()):
        assert code in family
        assert len(family) >= 14, code
        assert any(spelling != spelling.strip() for spelling in family), code
        assert any(spelling[:1].isspace() for spelling in family), code
        assert any(spelling.upper() == spelling and spelling.isalpha()
                   for spelling in family), code
        assert any(len(spelling) > 2 and spelling.isalpha()
                   for spelling in family), code
    assert set(_SPELLING_FAMILIES) == {"ar", "de", "en", "es", "fr", "it",
                                       "ru"}
    assert set(_UNRECOGNISED).isdisjoint(set(_SPELLING_FAMILIES))


# ===========================================================================
# P6.4 -- the same question with the whitespace class in the BODY
# ===========================================================================

@pytest.mark.parametrize("character", _WHITESPACE_ALPHABET)
def test_a_whitespace_led_body_still_publishes_the_canonical_record(character):
    """A1, re-asked on bodies that begin with whitespace -- F-1's own class.

    Two things are pinned at once.  First, `bind` must PUBLISH: on the
    pre-P6.4 bytes a body beginning with any character outside
    `roles_v2._M14_SPAN_TRIM` sent `_m15_predicate_span` to
    `lead.split()[0]` on an empty list and the `IndexError` escaped `bind`,
    so no record existed to compare.  Second, spelling invariance must still
    hold there: the tag's normalisation and the body's leading whitespace are
    independent, and a repair to one that perturbed the other would show up
    here.

    Reverting the `lead_words` guard in `roles_v2._m15_predicate_span` makes
    this node error.
    """
    spellings = ("ru", "RU", " ru", "\u00a0ru", "russian", "ar", "AR",
                 "\tar", None, "zz")
    for body_name in sorted(_BODIES):
        spec = _BODIES[body_name]
        text = character + spec["text"]
        canonical = {}
        for spelling in spellings:
            record = V2.bind(
                candidate_id="p64-ws-body", text=text, language=spelling,
                left_context=spec["left_context"], heading=spec["heading"],
                content_region_type=spec["content_region_type"])
            assert isinstance(record, V2.RoleBindingV2Record), (
                body_name, hex(ord(character)), repr(spelling))
            key = V2._declared_language(spelling)
            if key in canonical:
                assert _published(record) == canonical[key], (
                    body_name, hex(ord(character)), repr(spelling), key)
            else:
                canonical[key] = _published(record)
    # the sweep must have compared something, or it proves nothing.
    assert len({V2._declared_language(s) for s in spellings}) < len(spellings)


def test_the_whitespace_alphabet_is_complete():
    """The anti-narrowing arm -- F-2's lesson applied to F-2's repair.

    `_WHITESPACE_ALPHABET` must be EXACTLY the set `str.strip()` removes,
    which is the set `str.isspace()` accepts, because that is the set
    `_declared_language` folds away and the set `str.split()` divides on.
    Derived here rather than remembered, so narrowing the alphabet back
    towards {" ", "\t", "\n"} fails, and so does a future Python that adds a
    whitespace character this file has never seen.
    """
    universe = {chr(code) for code in range(0x110000) if chr(code).isspace()}
    carried = set(_WHITESPACE_ALPHABET)
    assert carried == universe, {
        "missing": sorted(hex(ord(c)) for c in universe - carried),
        "not_whitespace": sorted(hex(ord(c)) for c in carried - universe)}
    assert len(carried) >= 29, len(carried)
    # the class is NOT the span trim's class -- confusing the two is F-1.
    trimmed = {c for c in V2._M14_SPAN_TRIM if c.isspace()}
    assert trimmed == {" ", "\t", "\n"}, sorted(hex(ord(c)) for c in trimmed)
    assert carried - trimmed, "the alphabet adds nothing to the span trim"
    assert len(carried - trimmed) >= 26, len(carried - trimmed)
    # and every family actually carries the widened class in the TAG.
    for code, family in sorted(_SPELLING_FAMILIES.items()):
        leading = {spelling[:1] for spelling in family
                   if spelling[:1].isspace()}
        assert leading >= carried, (
            code, sorted(hex(ord(c)) for c in carried - leading))


# ===========================================================================
# A1 negative controls -- what the rule does NOT do
# ===========================================================================

#: LITERAL.  Exonyms and three-letter codes whose first two letters are ANOTHER
#: language's code.  The prefix rule truncates them; this file pins that as the
#: CURRENT behaviour so that introducing an exonym table is a visible change.
_EXONYM_CONTROLS = ("german", "German", "GERMAN", "ger", "spanish",
                    "Spanish", "spa", "swedish", "portuguese", "polish")

_COLLIDING_CODES = {"est": "es", "esu": "es", "fry": "fr", "frr": "fr",
                    "arg": "ar", "arn": "ar", "run": "ru", "rue": "ru",
                    "del": "de", "den": "de", "itl": "it", "enm": "en"}


def test_an_exonym_is_not_the_language_it_names():
    """`german` truncates to `ge`, which selects nothing; likewise `spanish`.

    Registered, not repaired: a correct fix is an exonym table at ingestion,
    which is a separate authorised change.  This arm exists so the defect
    cannot be lost, and so that closing it later shows up as a test change.
    """
    for exonym in _EXONYM_CONTROLS:
        for body_name in ("german_lexical_de", "subordinate_de",
                          "operative_es"):
            observed = _published(_bind(body_name, exonym, "none"))
            unknown = _published(_bind(body_name, "zz", "none"))
            assert observed == unknown, (exonym, body_name)


def test_a_three_letter_code_takes_its_two_letter_prefixs_answer():
    """The inherited prefix collision, pinned as it is.

    `clauses.py:376` and `v5_1/extraction._langs` collide identically and did
    so before this module adopted the rule, so `roles_v2` is CONSISTENT with
    the tree rather than newly wrong.  Registered, not repaired: a BCP-47
    parser is out of scope.
    """
    for code, collides_with in sorted(_COLLIDING_CODES.items()):
        for body_name in ("decisive_ru", "committee_en", "operative_es"):
            assert _published(_bind(body_name, code, "none")) == \
                _published(_bind(body_name, collides_with, "none")), code


# ===========================================================================
# A2 -- totality, and the refusal before the normalisation
# ===========================================================================

class _Opaque:
    """A truthy object with no string protocol at all."""

    def __repr__(self) -> str:  # pragma: no cover - diagnostics only
        return "<Opaque>"


#: LITERAL.  Genuine non-strings, FALSY and TRUTHY.  The falsy ones are the
#: class SEM-9r4 measured; the truthy ones are the class that is reachable end
#: to end, because `v5_4/invariants.py:299`'s ``or "en"`` replaces only falsy
#: values and `v5_1/campaign.py:189-201` validates `language` by requiring
#: ``str(value).strip()`` to be non-empty -- which `["de"]` satisfies -- and
#: then stores the value VERBATIM while `str()`-coercing its sibling field.
_NON_STRING_LANGUAGES = (
    0, 0.0, False, (), decimal.Decimal(0), 0j, b"",
    1, True, ["de"], {"a": 1}, ("de",), {"de"}, b"de", _Opaque(), 3.14,
    [], {}, set(),
)

_UNSOUND_ORDER_STATES = ("PACKET_CONSTRUCTION_DEFECT",
                         "LAYOUT_RECONSTRUCTION_REQUIRED")


def _refusing_context(order_state: str):
    return ST.empty_context(document_id="p63-doc", region_id="p63-region",
                            reading_order_state=order_state)


def test_the_normaliser_is_total():
    """A2(a).  No input of any type raises, and every non-`str` reads as the
    lawful "no declared language"."""
    for value in _NON_STRING_LANGUAGES:
        assert V2._declared_language(value) is None, repr(value)
    assert V2._declared_language(None) is None
    for tag, expected in (("ru", "ru"), ("RU", "ru"), (" ru", "ru"),
                          ("ru-RU", "ru"), ("", ""), ("   ", ""),
                          ("RUSSIAN", "ru")):
        assert V2._declared_language(tag) == expected, tag


@pytest.mark.parametrize("body_name", sorted(_BODIES))
def test_a_non_string_language_publishes_the_undeclared_record(body_name):
    """A2(a), behaviourally, on the ORDINARY route.

    A value this module cannot interpret is not a licence to crash and not a
    licence to invent: the record must be exactly the one an absent tag
    produces.
    """
    for shape in _SHAPES:
        reference = _published(_bind(body_name, None, shape))
        for value in _NON_STRING_LANGUAGES:
            observed = _published(_bind(body_name, value, shape))
            assert observed == reference, (
                body_name, shape, repr(value),
                {k: (reference[k], observed[k]) for k in reference
                 if reference[k] != observed[k]})


@pytest.mark.parametrize("order_state", _UNSOUND_ORDER_STATES)
def test_the_layout_refusal_survives_an_uninterpretable_declared_language(
        order_state):
    """A2, end to end through `bind`, on the REFUSAL route.

    This is the arm the coordinator's correction requires: a TRUTHY non-string
    reaches `bind` on real input, and on the P6.2 bytes it destroyed the
    refusal itself -- normalisation ran BEFORE the layout guard, so a lawful
    REJECTED / ROLE_BINDING_INVALID / CONSTRUCTION_DEFECT record became an
    AttributeError.  Layout precedes syntax, and it must precede the declared
    tag too.
    """
    for body_name in sorted(_BODIES):
        body = _BODIES[body_name]["text"]
        reference = _published(V2.bind(
            candidate_id="p63-refusal", text=body, language=None,
            structural_context=_refusing_context(order_state)))
        assert reference["final_extraction_disposition"] == "REJECTED"
        assert reference["role_binding_state"] == "ROLE_BINDING_INVALID"
        assert reference["proposition_status"] == "CONSTRUCTION_DEFECT"
        for value in _NON_STRING_LANGUAGES:
            record = V2.bind(
                candidate_id="p63-refusal", text=body, language=value,
                structural_context=_refusing_context(order_state))
            assert _published(record) == reference, (
                body_name, order_state, repr(value))


def test_the_non_string_domain_actually_contains_non_strings():
    """Floor.  SEM-9r4's F3 was that a domain documented as containing
    non-strings contained 62 strings and one `None`; this arm makes the same
    mistake impossible here."""
    assert all(not isinstance(v, str) and v is not None
               for v in _NON_STRING_LANGUAGES)
    assert sum(1 for v in _NON_STRING_LANGUAGES if not v) >= 6
    assert sum(1 for v in _NON_STRING_LANGUAGES if v) >= 6
    assert len(_NON_STRING_LANGUAGES) >= 15
    assert any(isinstance(v, (list, dict, set)) for v in _NON_STRING_LANGUAGES)


# ===========================================================================
# A2(b) -- the ORDER of the two statements in `bind`, syntactically
# ===========================================================================

def _bind_order_violations(source: str) -> list[str]:
    """Every clause of the ordering property this source violates, named.

    O1  the layout refusal is returned before any call to the declared-tag
        normaliser;
    O2  the normaliser IS still called on the ordinary route, so O1 cannot be
        satisfied by deleting it;
    O3  the refusal is returned exactly once, so O1 cannot be satisfied by
        adding a second, later refusal.
    """
    tree = ast.parse(textwrap.dedent(source)).body[0]
    assert isinstance(tree, ast.FunctionDef), tree
    normalise = [node.lineno for node in ast.walk(tree)
                 if isinstance(node, ast.Call)
                 and isinstance(node.func, ast.Name)
                 and node.func.id == "_declared_language"]
    refusal = [node.lineno for node in ast.walk(tree)
               if isinstance(node, ast.Call)
               and isinstance(node.func, ast.Name)
               and node.func.id == "_layout_refusal"]
    failures: list[str] = []
    if not normalise:
        failures.append("O2 the declared tag is never normalised")
    if not refusal:
        failures.append("O3 the layout refusal is never returned")
    elif len(refusal) != 1:
        failures.append(f"O3 {len(refusal)} refusal sites, must be 1")
    if normalise and refusal and min(normalise) < max(refusal):
        failures.append(
            f"O1 the declared tag is normalised at line {min(normalise)}, "
            f"before the layout refusal at line {max(refusal)}")
    return failures


def test_the_layout_refusal_precedes_the_declared_tag_normalisation():
    """A2(b), on production's own source.

    `_layout_refusal` resolves ZERO `language` names -- the P6.2 closure file
    pins that syntactically and behaviourally -- so normalising before it buys
    the refusal nothing and, on the P6.2 bytes, pre-empted it.
    """
    assert _bind_order_violations(inspect.getsource(V2.bind)) == [], \
        _bind_order_violations(inspect.getsource(V2.bind))


def test_the_refusal_site_still_reads_no_declared_language():
    """The premise A2(b) rests on, checked rather than assumed.

    If `_layout_refusal` ever started reading `language`, moving the
    normalisation after it would change what it publishes, and this file's
    ordering pin would be actively harmful.  That premise is pinned here as
    well as in the P6.2 closure file, because the two conclusions are
    different.
    """
    tree = ast.parse(textwrap.dedent(
        inspect.getsource(V2._layout_refusal))).body[0]
    reads = [node.lineno for node in ast.walk(tree)
             if isinstance(node, ast.Name) and node.id == "language"
             and isinstance(node.ctx, ast.Load)]
    assert reads == [], reads


def test_the_ordering_checker_is_not_vacuous():
    """Non-vacuity: three decoys, one per clause, defined here and never in
    production."""

    def decoy_o1_normalises_first(candidate_id, language, structural_context):
        language = _declared_language(language)  # noqa: F821
        if structural_context is not None:
            return _layout_refusal(candidate_id, "", "", language)  # noqa: F821
        return language

    def decoy_o2_never_normalises(candidate_id, language, structural_context):
        if structural_context is not None:
            return _layout_refusal(candidate_id, "", "", language)  # noqa: F821
        return language

    def decoy_o3_two_refusals(candidate_id, language, structural_context):
        if structural_context is None:
            return _layout_refusal(candidate_id, "", "a", language)  # noqa: F821
        language = _declared_language(language)  # noqa: F821
        return _layout_refusal(candidate_id, "", "b", language)  # noqa: F821

    def decoy_clean(candidate_id, language, structural_context):
        if structural_context is not None:
            return _layout_refusal(candidate_id, "", "", language)  # noqa: F821
        language = _declared_language(language)  # noqa: F821
        return language

    o1 = _bind_order_violations(inspect.getsource(decoy_o1_normalises_first))
    assert any(f.startswith("O1") for f in o1), o1
    o2 = _bind_order_violations(inspect.getsource(decoy_o2_never_normalises))
    assert any(f.startswith("O2") for f in o2), o2
    o3 = _bind_order_violations(inspect.getsource(decoy_o3_two_refusals))
    assert any(f.startswith("O3") for f in o3), o3
    assert _bind_order_violations(inspect.getsource(decoy_clean)) == []


# ===========================================================================
# A3 -- the rule this module now agrees with
# ===========================================================================

#: LITERAL.  The rule `v5_1/extraction.py:253`, `v5_2/lifecycle.py:520`,
#: `v5_2/lifecycle.py:577` and `v5_4/calibration.py:80` all apply.  Written out
#: here so the agreement is a comparison against a stated rule and not against
#: whatever those modules happen to do today.
def _three_regime_rule(language: str) -> str:
    return (language or "").strip().casefold()[:2]


def test_the_ingress_rule_is_the_three_regime_rule():
    """A3.  Agreement on the whole spelling domain this file carries, plus the
    tags on which `lower` and `casefold` genuinely differ."""
    domain = ["", " ", "   ", "\t", "\n", "ru", "RU", " ru", "ru ",
              "  ru  ", "\tde", "\nfr", " it", "russian", "GERMAN",
              "est", "run", "x-custom", "0", "-1", "ẞe", "ßx", "ﬁn", "İT",
              "TR", "tr", "  ", "zz", "ZZ", "ar-EG", "AR_SA"]
    for tag in domain:
        assert V2._declared_language(tag) == _three_regime_rule(tag), repr(tag)


def test_the_alignment_actually_moved_something():
    """Non-vacuity for A3: the two rules must genuinely disagree somewhere in
    the domain above, or "aligned" would be an empty claim."""
    disagreeing = [tag for tag in (" ru", "\tde", "  ar  ", "\nfr", "ẞe")
                   if (tag or "").lower()[:2] != _three_regime_rule(tag)]
    assert len(disagreeing) >= 4, disagreeing
    # and the disagreement is observable: a stripped tag now selects the
    # machinery its canonical form selects, which `.lower()[:2]` did not.
    assert V2._declared_language(" ru") == "ru"
    assert (" ru").lower()[:2] != "ru"


def test_the_ingresses_that_take_a_declared_language_all_normalise():
    """Every public entry point that accepts the tag applies the rule.

    A repair applied at two of three ingresses is a repair with a hole, and
    the hole is invisible to a test that only drives `bind`.
    """
    for function in (V2.bind, V2.build_lattice,
                     V2.local_pronominal_subject_relations):
        source = inspect.getsource(function)
        tree = ast.parse(textwrap.dedent(source)).body[0]
        calls = [node for node in ast.walk(tree)
                 if isinstance(node, ast.Call)
                 and isinstance(node.func, ast.Name)
                 and node.func.id == "_declared_language"]
        assert len(calls) == 1, (function.__name__, len(calls))
        assert "language" in inspect.signature(function).parameters, \
            function.__name__
