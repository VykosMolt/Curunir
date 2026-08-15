"""P6.3 workstream A, A6 — the DEGENERATE-SPAN crash class.

WHY THIS FILE EXISTS
--------------------
P6.2's R6 closed the ZERO-TOKEN class: `tokens[-1][2]` raised `IndexError` in
the three script generators when a span carried no word tokens at all.  It did
not close, and did not touch, a second class with the same symptom and a
different cause: a NON-EMPTY body whose derived span is DEGENERATE.

`roles_v2._recital_candidates` built its `PREDICATE_COMPLEMENT` over
`(head[2], end)` -- from the end of the recital head to the end of the last
word token.  When the recital head IS the last word token, those two offsets
are the same, the span is `(n, n)`, and `Candidate.__post_init__` refuses it
with `RoleBindingV2Violation("empty span (n, n)")`.  The exception escapes
`bind` entirely: no record at all, lawful or otherwise.

Measured on a 45360-point constructed domain of recital heads, ordinary words
and short spans, in three scripts, at fifteen declared-language values and
three context shapes -- a domain WHOSE WHITESPACE ALPHABET WAS EXACTLY
{" ", "\t", "\n"}:

    P6.1 ad21425a   10080 points raised in this class
    P6.2 ac68abb6    9216 points raised in this class   <- R6 changed nothing
    P6.3             0 IN THAT ALPHABET

It is reachable on real text.  A recital head is a whole region whenever
segmentation puts the line break after it, and `v5_4/invariants.py` forwards
`ctx.selected_text or ctx.seed_text or ctx.candidate.raw_span` verbatim to
`bind`; nothing on that path requires the span to contain more than one word.

WHAT THAT "0" DID NOT COVER, AND WHY THIS FILE NOW SAYS SO  (P6.4, F-1/F-2)
--------------------------------------------------------------------------
{" ", "\t", "\n"} is EXACTLY the whitespace `roles_v2._M14_SPAN_TRIM` carries,
and `_m15_trim` removes exactly the characters of that literal set.  So the
domain above was, for the whitespace question, a domain of characters the
binder deletes before it looks -- the "0" was true, and it was true for a
reason unrelated to the property this file names.

The final adversarial review found the residue: `_m15_predicate_span`'s P3
branch wrote `first = lead.split()[0]` under the guard `if lead and ...`.  A
lead composed solely of whitespace OUTSIDE `_M14_SPAN_TRIM` -- U+000B, U+000C,
CR, U+001C-U+001F, U+0085 NEL, U+00A0 NBSP, U+1680, U+2000-U+200A, U+2028,
U+2029, U+202F, U+205F, U+3000 -- survives the trim, is TRUTHY, and
`str.split()` (which splits on `str.isspace()`, a strictly wider notion than
`_M14_SPAN_TRIM`) returns `[]`.  `[0]` then raised `IndexError`, which escaped
`bind` exactly as the empty-span violation had: no record at all.  For the
twenty head-final recitals below that is a REGRESSION the A6 repair
introduced -- it converted `RoleBindingV2Violation` into `IndexError` there --
and for ordinary Cyrillic and Arabic prose it had been present since P6.1 and
no round had seen it.  Measured on the pre-P6.4 bytes: 66 of an 11-body x
14-character grid, and 126 of 180 points built from THIS FILE'S OWN twenty
bodies.

NBSP is what an HTML `&nbsp;` becomes and CR is what a CRLF document carries,
and the only Unicode normalisation anywhere in the operational tree is NFC,
which preserves every one of these characters.

`_WHITESPACE_OUTSIDE_THE_SPAN_TRIM` below is therefore the alphabet this file
now sweeps, and `test_the_whitespace_alphabet_is_complete` derives its
required contents from `str.isspace()` itself rather than from a list someone
remembered to update -- so narrowing the alphabet back, or a future Python
adding a whitespace character, fails here instead of in production.

WHAT THE REPAIR IS, AND WHAT IT IS NOT
--------------------------------------
The repair drops the ONE candidate whose span cannot exist and emits the rest.
It does NOT weaken `Candidate.__post_init__`: an empty span is genuinely not a
role candidate, the invariant is right, and the generator was wrong to build
one.  The last test in this file pins that invariant so a future "repair" that
deletes the check instead fails here.

It is also NOT a pure crash guard, and this file does not pretend otherwise.
The alternative -- emitting no recital candidates at all for a head-final
recital -- would have re-routed the span to the script-specific generator and
published `ROLE_BINDING_UNRESOLVED / PREDICATE_UNRESOLVED`, i.e. "not a recital
at all", contradicting the generator's own match.  The shipped behaviour keeps
the recital reading and publishes `ROLE_BINDING_PARTIAL`, the same shape the
substrate publishes for an ordinary recital, with `RECITAL_BODY` absent
because there is no body.  Both arms below assert that shape, so the choice is
pinned rather than merely taken.

The three sibling generators that derive a complement the same way already
carry the identical `stop < end` test.  This file pins that they all still do,
because the class is a SHAPE and not a site.
"""

from __future__ import annotations

import dataclasses

import pytest

from curunir_operational.v5_8_1 import roles_v2 as V2
from curunir_operational.v5_8_1 import structure as ST

pytestmark = pytest.mark.no_db


#: LITERAL.  Every character `str.isspace()` accepts that `_M14_SPAN_TRIM`
#: does NOT remove -- i.e. exactly the whitespace that survives `_m15_trim`,
#: makes a lead truthy and still splits to nothing.  Written out here as a
#: literal of THIS file, and checked for completeness against `str.isspace()`
#: by `test_the_whitespace_alphabet_is_complete`, so it can be neither
#: silently narrowed nor silently outgrown.
_WHITESPACE_OUTSIDE_THE_SPAN_TRIM = (
    "\x0b",      # VT
    "\x0c",      # FF
    "\r",        # CR -- what a CRLF document carries
    "\x1c", "\x1d", "\x1e", "\x1f",   # the four information separators
    "\x85",      # NEL
    "\xa0",      # NBSP -- what an HTML &nbsp; becomes
    " ",    # OGHAM SPACE MARK
    " ", " ", " ", " ", " ", " ",
    " ", " ", " ", " ", " ",
    " ",    # LINE SEPARATOR
    " ",    # PARAGRAPH SEPARATOR
    " ",    # NARROW NBSP
    " ",    # MEDIUM MATHEMATICAL SPACE
    "　",    # IDEOGRAPHIC SPACE
)

#: The three the trim DOES remove, kept separate so the two roles never blur.
_WHITESPACE_INSIDE_THE_SPAN_TRIM = (" ", "\t", "\n")

#: LITERAL.  Ordinary prose in the three scripts -- NOT recitals.  The 22
#: `IndexError`s of the review's grid that were NOT the A6 class came from
#: bodies of exactly this shape, so the widened sweep must carry them or it
#: would pin only half of what it closed.
_ORDINARY_PROSE = {
    "prose_ru": ("Комитет рассмотрел доклад.", "ru"),
    "prose_ru_fronted": ("В соответствии с решением Комитета доклад был "
                         "рассмотрен.", "ru"),
    "prose_ar": ("تعتمد المفوضية أعمالاً تنفيذية.", "ar"),
    "prose_ar_fronted": ("في هذا السياق تعتمد اللجنة التقرير.", "ar"),
    "prose_fr": ("Le Ministere adopte les mesures necessaires.", "fr"),
    "prose_en": ("The Committee shall review the report of the Secretariat.",
                 "en"),
    "prose_de": ("Die Behörde hat die Maßnahmen zu treffen.", "de"),
    "date_line": ("A70/12  Agenda item 13.1  22 May 2017", "en"),
}


#: LITERAL.  Recital heads that are their span's LAST word token.  Three
#: scripts, because the generator branches on script; with and without
#: surrounding whitespace and punctuation, because segmentation leaves those.
#: P6.4: the last block carries whitespace from OUTSIDE `_M14_SPAN_TRIM`, so
#: the two arms below -- which assert the whole published shape, not merely
#: the absence of a raise -- cover the class F-1 named as well.
_HEAD_FINAL_RECITALS = {
    "fr_considerant": ("Considérant", "fr"),
    "fr_vu": ("Vu", "fr"),
    "fr_trailing_space": ("Considérant  ", "fr"),
    "fr_leading_space": ("  Considérant", "fr"),
    "fr_comma": ("Considérant,", "fr"),
    "fr_newline": ("Considérant\n", "fr"),
    "en_whereas": ("Whereas", "en"),
    "en_having": ("Having", "en"),
    "en_noting": ("Noting", "en"),
    "de_gestuetzt": ("Gestützt", "de"),
    "es_considerando": ("Considerando", "es"),
    "it_visto": ("Visto", "it"),
    "ru_uchityvaya": ("Учитывая", "ru"),
    "ru_prinimaya": ("Принимая", "ru"),
    "ru_otmechaya": ("Отмечая", "ru"),
    "ru_comma": ("Учитывая,", "ru"),
    "ru_leading_space": ("  Учитывая", "ru"),
    "ar_waidh": ("وإذ", "ar"),
    "ar_idh": ("إذ", "ar"),
    "ar_trailing_space": ("وإذ ", "ar"),
    # P6.4 -- whitespace OUTSIDE `_M14_SPAN_TRIM`.  Every one of these raised
    # `IndexError` out of `bind` on the pre-P6.4 bytes.
    "fr_leading_nbsp": ("\xa0Considérant", "fr"),
    "fr_leading_cr": ("\rConsidérant", "fr"),
    "fr_trailing_nbsp": ("Considérant\xa0", "fr"),
    "fr_vu_leading_ideographic": ("　Vu", "fr"),
    "ru_leading_nbsp": ("\xa0Учитывая", "ru"),
    "ru_leading_line_separator": (" Учитывая", "ru"),
    "ru_leading_narrow_nbsp": (" Принимая", "ru"),
    "ar_leading_nbsp": ("\xa0وإذ", "ar"),
    "ar_leading_nel": ("\x85وإذ", "ar"),
}

#: LITERAL.  The same class in the German, English, Spanish and Italian
#: lexicons.
#:
#: These are NOT in the table above, and the reason is a MEASUREMENT, not a
#: convenience.  `test_a_head_final_recital_keeps_its_recital_reading` asserts
#: `ROLE_BINDING_PARTIAL / RECITAL_RELATION_PREDICATE`, and a German, English,
#: Spanish or Italian head-final recital loses that reading under ANY leading
#: whitespace -- ordinary ASCII space and tab included:
#:
#:     bind("Gestützt")    -> ROLE_BINDING_PARTIAL
#:     bind("  Gestützt")  -> ROLE_BINDING_UNRESOLVED   <- plain spaces
#:     bind("\u1680Gestützt") -> ROLE_BINDING_UNRESOLVED   <- and here
#:
#: identically for "Whereas", "Considerando" and "Visto", while the French,
#: Russian and Arabic leads keep the reading under every character.  That is a
#: PRE-EXISTING property of those lexicons' route, present on the P6.3 bytes
#: and untouched by P6.4, and the old table could not see it because its only
#: leading-whitespace probes were French and Russian.  It is registered here,
#: not repaired: this workstream is authorised to close an uncaught exception,
#: not to change which reading a lexicon takes.
#:
#: They ARE swept by the crash arm and by the inertness arm below, which is
#: where they belong -- the crash class is theirs too.
_UNTRIMMED_LEAD_ONLY = {
    "en_leading_en_quad": (" Whereas", "en"),
    "de_leading_ogham": (" Gestützt", "de"),
    "es_leading_unit_separator": ("\x1fConsiderando", "es"),
    "it_leading_nbsp": ("\xa0Visto", "it"),
}

#: LITERAL.  Single tokens that are NOT recital heads to this module -- the
#: French "Attendu" only opens a recital as "Attendu que" and the rest are
#: ordinary words.  The guard must not have widened what matches, so these are
#: pinned to produce no recital candidates at all.
_NON_RECITAL_SINGLE_TOKENS = ("Attendu", "Commission", "Комитет", "اللجنة",
                              "Committee", "El", "Der", "x", "1.")

#: The same heads WITH a body: ordinary recitals, which must keep their body.
_ORDINARY_RECITALS = {
    "fr": ("Considérant que la Commission a adopté le rapport,", "fr"),
    "ru": ("Учитывая, что Комитет рассмотрел доклад,", "ru"),
    "ar": ("وإذ يلاحظ اللجنة التقرير,", "ar"),
    "en": ("Whereas the Council considered the matter,", "en"),
}

#: Declared-language values swept across the class, including the values that
#: select a DIFFERENT script's recital vocabulary and the non-strings A2 made
#: lawful, because the generator branches on the COUNTED script and the two
#: must not interact to produce a raise.
_LANGUAGES = (None, "", "ru", "RU", " ru", "ar", "de", "en", "fr", "it", "es",
              "zz", "russian", 0, ["de"])

_REGIONS = ("UNKNOWN_STRUCTURAL_REGION", "PRIMARY_PROPOSITION_CONTENT",
            "NAVIGATION_MENU")


def _shapes():
    yield "none", None
    yield "plain", ST.empty_context(
        document_id="p63-doc", region_id="p63-region",
        reading_order_state="READING_ORDER_ESTABLISHED")
    yield "refusing", ST.empty_context(
        document_id="p63-doc", region_id="p63-region",
        reading_order_state="PACKET_CONSTRUCTION_DEFECT")


@pytest.mark.parametrize("probe", sorted(_HEAD_FINAL_RECITALS))
def test_a_head_final_recital_binds_instead_of_raising(probe):
    """A6.  The whole sweep, through the production entry point.

    Removing the `head[2] < end` guard makes every point here raise
    `RoleBindingV2Violation`, which pytest reports as an error, not a
    failure -- either way this node does not pass.
    """
    text, native = _HEAD_FINAL_RECITALS[probe]
    for language in (native,) + _LANGUAGES:
        for region in _REGIONS:
            for shape, context in _shapes():
                record = V2.bind(candidate_id="p63-a6", text=text,
                                 language=language,
                                 content_region_type=region,
                                 structural_context=context)
                assert isinstance(record, V2.RoleBindingV2Record), (
                    probe, language, region, shape)
                # nothing in this class may be PUBLISHED: a recital with no
                # body is not an evidence-bound proposition.
                assert record.final_extraction_disposition != \
                    "EVIDENCE_BOUND", (probe, language, region, shape)


@pytest.mark.parametrize("probe", sorted(_HEAD_FINAL_RECITALS))
def test_a_head_final_recital_keeps_its_recital_reading(probe):
    """The SHAPE of the repair, pinned so the alternative is a visible change.

    A head-final recital keeps the head and the governing-enactment
    requirement the generator declares, and carries no `RECITAL_BODY`, because
    there is no body.  A repair that returned no recital candidates at all
    would fail here.
    """
    text, native = _HEAD_FINAL_RECITALS[probe]
    candidates, _analyses, _signals, _script = V2.build_lattice(
        text=text, language=native)
    rules = sorted(candidate.origin_rule for candidate in candidates)
    assert "RECITAL_HEAD" in rules, (probe, rules)
    assert "RECITAL_REQUIRES_ENACTMENT" in rules, (probe, rules)
    assert "RECITAL_BODY" not in rules, (probe, rules)
    record = V2.bind(candidate_id="p63-a6", text=text, language=native)
    assert record.role_binding_state == "ROLE_BINDING_PARTIAL", probe
    assert record.predicate_state == "RECITAL_RELATION_PREDICATE", probe


def test_the_guard_did_not_widen_what_counts_as_a_recital():
    """Negative control: a single token that is not a recital head still is
    not one.  A "repair" that made the generator match more broadly would
    publish a recital reading for ordinary words, and would pass every arm
    above."""
    for text in _NON_RECITAL_SINGLE_TOKENS:
        for language in ("fr", "ru", "ar", "en", None):
            candidates, _a, _s, _sc = V2.build_lattice(text=text,
                                                       language=language)
            rules = sorted(candidate.origin_rule for candidate in candidates)
            assert not any(rule.startswith("RECITAL") for rule in rules), (
                text, language, rules)


@pytest.mark.parametrize("probe", sorted(_ORDINARY_RECITALS))
def test_an_ordinary_recital_still_carries_its_body(probe):
    """Non-vacuity: a recital that HAS a body must still get one, or the
    guard above would be indistinguishable from deleting the complement."""
    text, language = _ORDINARY_RECITALS[probe]
    candidates, _analyses, _signals, _script = V2.build_lattice(
        text=text, language=language)
    rules = sorted(candidate.origin_rule for candidate in candidates)
    assert "RECITAL_HEAD" in rules, (probe, rules)
    assert "RECITAL_BODY" in rules, (probe, rules)
    body = next(candidate for candidate in candidates
                if candidate.origin_rule == "RECITAL_BODY")
    assert body.span is not None and body.span[0] < body.span[1], probe
    assert body.text, probe


# ===========================================================================
# P6.4 -- the whitespace the span trim does NOT remove
# ===========================================================================

@pytest.mark.parametrize("character", _WHITESPACE_OUTSIDE_THE_SPAN_TRIM)
def test_leading_whitespace_outside_the_span_trim_does_not_destroy_the_record(
        character):
    """F-1, pinned by name, over the WHOLE alphabet `_m15_trim` leaves behind.

    `bind` must publish a record for every one of these bodies.  On the
    pre-P6.4 bytes `_m15_predicate_span`'s P3 branch raised `IndexError` --
    `lead` was truthy because `_m15_trim` only removes the characters of
    `_M14_SPAN_TRIM`, and `lead.split()` was empty because `str.split()`
    splits on `str.isspace()` -- and the exception escaped `bind`, so NO
    record at all was produced: not lawful, not refusing, not quarantining.

    Both classes the review measured are here: the head-final recitals A6
    claims to have closed (where the pre-P6.4 bytes turned one uncaught
    exception into another) and ORDINARY PROSE, which crashed identically in
    all four generations and which no earlier round observed.

    Reverting the `lead_words` guard in `roles_v2._m15_predicate_span` makes
    this node error.
    """
    probes = (list(_HEAD_FINAL_RECITALS.items())
              + list(_UNTRIMMED_LEAD_ONLY.items())
              + list(_ORDINARY_PROSE.items()))
    for name, (body, native) in probes:
        for text in (character + body, character * 2 + body,
                     body + character, character + body + character):
            for language in (native, None, "zz"):
                record = V2.bind(candidate_id="p64-f1", text=text,
                                 language=language)
                assert isinstance(record, V2.RoleBindingV2Record), (
                    name, hex(ord(character)), language)
                # and again through a structural context, which is the shape
                # `v5_4/invariants.py` actually builds.
                for shape, context in _shapes():
                    record = V2.bind(candidate_id="p64-f1", text=text,
                                     language=language,
                                     structural_context=context)
                    assert isinstance(record, V2.RoleBindingV2Record), (
                        name, hex(ord(character)), language, shape)


def test_the_whitespace_class_is_inert_apart_from_the_trim_itself():
    """The repair is a CRASH repair and nothing else -- stated exactly.

    A guard that also changed what a lawful body PUBLISHES would be a
    behavioural change wearing a crash repair's clothes.  The reference is
    production against ITSELF at a leading character the trim DOES remove, and
    the property measured over the whole alphabet is:

      * every published field except `predicate_span` is IDENTICAL, because
        P3 declines to fire for the same reason in both cases -- the lead
        carries no word token, so there is no fronted oblique frame to test;

      * `predicate_span` is identical too, EXCEPT where the emitted span
        begins at the very front of the body, and there it differs by exactly
        the one prefix character, because `_m15_trim` removes an ASCII space
        from the boundary and does not remove a character outside
        `_M14_SPAN_TRIM`.  That difference is `_M14_SPAN_TRIM`'s, not the
        guard's, and it is the reason the repair does NOT widen that set:
        widening it would move this boundary on EVERY body, not only on the
        ones that used to crash.

    Both branches are counted, so the arm cannot pass by exercising neither.
    """
    fields = tuple(field.name for field in dataclasses.fields(
        V2.RoleBindingV2Record) if field.name != "recorded_time")
    others = tuple(name for name in fields if name != "predicate_span")
    probes = (sorted(_ORDINARY_PROSE.items())
              + sorted(_HEAD_FINAL_RECITALS.items())
              + sorted(_UNTRIMMED_LEAD_ONLY.items()))
    compared = identical_span = trim_boundary = 0
    for name, (body, native) in probes:
        body = body.lstrip("".join(_WHITESPACE_OUTSIDE_THE_SPAN_TRIM)
                           + "".join(_WHITESPACE_INSIDE_THE_SPAN_TRIM))
        if not body:
            continue
        reference = V2.bind(candidate_id="p64-f1-inert", text=" " + body,
                            language=native)
        for character in _WHITESPACE_OUTSIDE_THE_SPAN_TRIM:
            observed = V2.bind(candidate_id="p64-f1-inert",
                               text=character + body, language=native)
            for field in others:
                compared += 1
                assert getattr(observed, field) == getattr(reference,
                                                           field), (
                    name, hex(ord(character)), field,
                    getattr(reference, field), getattr(observed, field))
            if observed.predicate_span == reference.predicate_span:
                identical_span += 1
                continue
            # the ONLY licensed difference: the untrimmed character is inside
            # the emitted span and the ASCII space was not.  Same stop, start
            # differs by exactly the one prefix character, which is at 0.
            assert reference.predicate_span is not None, name
            assert observed.predicate_span is not None, name
            assert observed.predicate_span[1] == reference.predicate_span[1], (
                name, hex(ord(character)), reference.predicate_span,
                observed.predicate_span)
            assert observed.predicate_span[0] == 0, (
                name, hex(ord(character)), observed.predicate_span)
            assert reference.predicate_span[0] == 1, (
                name, hex(ord(character)), reference.predicate_span)
            trim_boundary += 1
    assert compared >= 5000, compared
    assert identical_span >= 100, identical_span
    assert trim_boundary >= 1, trim_boundary


def test_the_whitespace_alphabet_is_complete():
    """The anti-narrowing arm -- F-2's own lesson applied to F-2's repair.

    The A6 domain certified "P6.3 0" over an alphabet of exactly
    {" ", "\t", "\n"}, which is precisely the whitespace `_M14_SPAN_TRIM`
    removes; the class it named could not appear in it.  So this file does
    not trust its own list: the required contents are DERIVED from
    `str.isspace()` -- the predicate `str.split()` itself uses -- and from
    production's own `_M14_SPAN_TRIM`, and the literal above must cover the
    difference exactly.

    Narrowing `_WHITESPACE_OUTSIDE_THE_SPAN_TRIM` back towards the ASCII
    three fails here, and so does a future Python that adds a whitespace
    character this file has never seen.
    """
    universe = {chr(code) for code in range(0x110000) if chr(code).isspace()}
    trimmed = set(V2._M14_SPAN_TRIM)
    required = universe - trimmed
    carried = set(_WHITESPACE_OUTSIDE_THE_SPAN_TRIM)
    assert carried == required, {
        "missing_from_this_file": sorted(hex(ord(c))
                                         for c in required - carried),
        "not_actually_outside_the_trim": sorted(hex(ord(c))
                                                for c in carried - required)}
    # the two roles never blur: the file's "inside" list is inside, and the
    # two are disjoint.
    assert set(_WHITESPACE_INSIDE_THE_SPAN_TRIM) <= trimmed
    assert carried.isdisjoint(set(_WHITESPACE_INSIDE_THE_SPAN_TRIM))
    # and the class is non-trivial: every carried character IS whitespace to
    # `str.split()` and is NOT removed by the trim, which is exactly the
    # combination that produced the defect.
    assert len(carried) >= 26, sorted(hex(ord(c)) for c in carried)
    for character in carried:
        assert character.isspace(), hex(ord(character))
        assert (character + "x").strip(V2._M14_SPAN_TRIM) != "x", \
            hex(ord(character))
        assert (character * 3).split() == [], hex(ord(character))


def test_the_head_final_sweep_actually_carries_the_widened_alphabet():
    """Floor for the two arms at the top of this file.

    `_HEAD_FINAL_RECITALS` is the domain those arms sweep, and F-2 was that a
    domain can certify a class it cannot contain.  So the domain is checked:
    it must carry bodies whose whitespace is outside the span trim, in more
    than one script, at both ends.
    """
    outside = set(_WHITESPACE_OUTSIDE_THE_SPAN_TRIM)
    leading = {name for name, (text, _) in _HEAD_FINAL_RECITALS.items()
               if text[:1] in outside}
    trailing = {name for name, (text, _) in _HEAD_FINAL_RECITALS.items()
                if text[-1:] in outside}
    assert len(leading) >= 8, sorted(leading)
    assert trailing, sorted(trailing)
    # all three SCRIPTS the generator branches on -- Latin, Cyrillic, Arabic --
    # carry an untrimmed lead in the shape-asserting table.
    scripts = {native for name, (text, native) in _HEAD_FINAL_RECITALS.items()
               if name in leading | trailing}
    assert scripts >= {"fr", "ru", "ar"}, sorted(scripts)
    # the four lexicons whose reading leading whitespace already changed, on
    # the P6.3 bytes and with plain ASCII spaces, are carried too -- in the
    # crash and inertness arms, where that pre-existing property is not in
    # the way.  Together the two tables span seven declared languages.
    assert all(text[:1] in outside for text, _ in _UNTRIMMED_LEAD_ONLY.values())
    languages = scripts | {native for _, native in _UNTRIMMED_LEAD_ONLY.values()}
    assert len(languages) >= 7, sorted(languages)
    # and the prose class the review found is carried too, in four languages.
    assert len(_ORDINARY_PROSE) >= 6
    assert len({native for _, native in _ORDINARY_PROSE.values()}) >= 4


def test_the_zero_token_class_R6_closed_stays_closed():
    """The neighbouring class, carried here so a repair to one cannot
    silently reopen the other.

    P6.4 widens this arm's alphabet too: a body made ENTIRELY of whitespace
    the span trim does not remove is the degenerate limit of F-1, and it must
    still take the lawful no-token route rather than any exception.
    """
    exotic = [character * length
              for character in _WHITESPACE_OUTSIDE_THE_SPAN_TRIM
              for length in (1, 3)]
    for text in ("", " ", "  ", "\t", "\n", " ", " \t\n ", "...",
                 "---", "«»", "()", "§§", "|", *exotic,
                 "".join(_WHITESPACE_OUTSIDE_THE_SPAN_TRIM)):
        for language in _LANGUAGES:
            for shape, context in _shapes():
                record = V2.bind(candidate_id="p63-a6-zero", text=text,
                                 language=language,
                                 structural_context=context)
                assert record.final_extraction_disposition != \
                    "EVIDENCE_BOUND", (repr(text), language, shape)


def test_no_candidate_generator_derives_a_degenerate_span():
    """The class as a SHAPE, over a wide constructed domain.

    Every candidate every generator proposes for every probe body must have
    either no span or a strictly positive one.  This reaches past the recital
    generator to the Russian, Arabic and Latin complement derivations, which
    already carry the same test, so a future site that loses it fails here
    rather than in production.
    """
    heads = [text for text, _ in _HEAD_FINAL_RECITALS.values()]
    bodies = heads + [text for text, _ in _ORDINARY_RECITALS.values()] + [
        "", " ", "x", "Комитет", "اللجنة", "Committee", "El", "Der",
        "Комитет рассмотрел", "يلاحظ اللجنة", "The Committee shall review.",
        "sie hierzu nicht in der Lage ist;", "A70/12  13.1  22",
        "Рекомендует", "وينبغي", "peuvent", "должна", "1.", "(a)", "—",
    ] + [
        # P6.4: the same shape question asked with whitespace the span trim
        # does NOT remove, on both the recital and the ordinary-prose route.
        character + body
        for character in _WHITESPACE_OUTSIDE_THE_SPAN_TRIM
        for body in ("Considérant", "Учитывая", "وإذ",
                     "Комитет рассмотрел доклад.",
                     "تعتمد المفوضية أعمالاً تنفيذية.")
    ]
    seen = 0
    for text in bodies:
        for language in _LANGUAGES:
            for region in _REGIONS:
                candidates, _a, _s, _sc = V2.build_lattice(
                    text=text, language=language,
                    content_region_type=region)
                for candidate in candidates:
                    seen += 1
                    assert (candidate.span is None
                            or candidate.span[0] < candidate.span[1]), (
                        repr(text), language, candidate.origin_rule,
                        candidate.span)
    # the sweep must actually have built candidates, or it proves nothing.
    assert seen >= 500, seen


def test_the_empty_span_invariant_is_not_weakened():
    """The repair must be in the GENERATOR, never in the invariant.

    `Candidate.__post_init__` is right that a role candidate needs a non-empty
    span.  Deleting that check would also make every point above pass, so it
    is pinned here: an empty span, and an inverted one, must still be refused.
    """
    for span in ((9, 9), (0, 0), (5, 3)):
        with pytest.raises(V2.RoleBindingV2Violation):
            V2.Candidate(
                candidate_id="p63-a6-invariant", role_type="PREDICATE_HEAD",
                span=span, text="x", origin_rule="TEST_ONLY")
    # and a lawful span is still accepted, so the check is not simply always
    # raising.
    lawful = V2.Candidate(
        candidate_id="p63-a6-invariant", role_type="PREDICATE_HEAD",
        span=(0, 1), text="x", origin_rule="TEST_ONLY")
    assert lawful.span == (0, 1)
    assert V2.Candidate(
        candidate_id="p63-a6-invariant", role_type="GOVERNING_CLAUSE",
        span=None, text="", origin_rule="TEST_ONLY").span is None


def test_the_sibling_complement_derivations_all_carry_the_guard():
    """The class is a shape, not a site: the four generators that derive a
    complement from `(head_end, span_end)` must all test it first.

    Read off the AST rather than the text, so a reformatting does not break
    the pin and a DELETED guard does.
    """
    import ast
    import inspect
    import textwrap

    source = inspect.getsource(V2)
    tree = ast.parse(source)
    guarded = 0
    unguarded = []
    for function in ast.walk(tree):
        if not isinstance(function, ast.FunctionDef):
            continue
        for node in ast.walk(function):
            if not (isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Name)
                    and node.func.id == "_candidate"):
                continue
            role = node.args[0] if node.args else None
            if not (isinstance(role, ast.Constant)
                    and role.value == "PREDICATE_COMPLEMENT"):
                continue
            span = node.args[1] if len(node.args) > 1 else None
            if not isinstance(span, ast.Tuple):
                continue
            # is this call inside an `if <lower> < <upper>:` test?
            enclosing = [
                test for test in ast.walk(function)
                if isinstance(test, ast.If)
                and any(inner is node for inner in ast.walk(test))
                and isinstance(test.test, ast.Compare)
                and len(test.test.ops) == 1
                and isinstance(test.test.ops[0], ast.Lt)
                and ast.unparse(test.test.left) == ast.unparse(span.elts[0])
                and ast.unparse(test.test.comparators[0]) == ast.unparse(
                    span.elts[1])
            ]
            if enclosing:
                guarded += 1
            else:
                unguarded.append((function.name, node.lineno,
                                  ast.unparse(span)))
    assert unguarded == [], unguarded
    assert guarded >= 4, guarded
    assert textwrap.dedent("") == ""
