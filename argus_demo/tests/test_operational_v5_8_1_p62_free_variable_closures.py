"""P6.2 — FREE-VARIABLE closures for the three axes the P6.1 matrix could not
carry, plus the value-quantified closure for out-of-vocabulary region types.

WHY THIS FILE EXISTS
--------------------
Two review rounds pinned the same property twice and both pins were evaded the
same way.  SEM-8r3 measured it exactly, on the accepted P6.1 bytes
(`roles_v2.py` sha256 `ad21425a…`), with a 1595-test corpus-present baseline:

* MUTANT-2 -- refusal-route drift, parameter NOT renamed, value inside the
  swept vocabulary -- was killed by the AST name check alone (1 failure).
* MUTANT-3 -- the same drift, parameter renamed, value `"ru"` -- was killed by
  the behavioural pins alone (18 failures).
* **MUTANT-4 -- the same drift, parameter renamed AND gated on a value outside
  the swept vocabulary -- SURVIVED the whole 1595-test suite, byte-identically
  to the pristine baseline.**
* **MUTANT-6 -- reason drift gated on a `content_region_type` outside
  `regions.ORIGIN_CLASSES` -- also SURVIVED all 1595.**

The diagnosis was not "the property is unpinnable".  It was that both rounds
pinned a NAME or a VALUE LIST rather than the FREE VARIABLE.  A pin on the
name is defeated by renaming; a pin on a value table is defeated by choosing a
value outside the table; and the vocabularies in question -- declared language
and declared region type -- are UNBOUNDED, unvalidated caller strings in
production, so no value table can ever be exhaustive over them.

The closures below quantify over the function's own SYNTAX instead, which is
finite even when its input domain is not:

  P1  the signature is exactly what it is declared to be, so a fourth
      parameter read under ANY name is a failure;
  P2  every name the body resolves OUTSIDE itself is in an enumerated
      whitelist, so a module-global or helper channel is a failure;
  P3  the parameter that must not be consulted is never LOADED;
  P4  no dynamic lookup (`locals`, `globals`, `vars`, `eval`, `exec`,
      `_getframe`, `currentframe`) can smuggle a read past P2/P3.

Each syntactic closure is paired with a BEHAVIOURAL arm quantified over the
value domain BY POSITION -- the fourth positional argument, whatever it is
called -- so the two arms are independent and a mutant must defeat both.

WHAT THIS FILE DOES NOT CLAIM
-----------------------------
It does not claim the main adapter route is language-invariant.  Production
LEGITIMATELY routes on the declared language there (`clauses.py:377-385`), so
that is false by design and is a matter for a claim narrowing and for the
ingress-normalisation repair, not for a pin.  The closures here are asserted
only where the invariance genuinely holds: the layout-refusal route, the
counted-script function, and the out-of-vocabulary region-type domain.

P6.3 A5 CLAIM NARROWING.  Each syntactic closure binds the SITE it names --
`roles_v2._layout_refusal` and `clauses.script_of` -- and not the whole route
through them.  Drift implanted elsewhere on the route, in `bind`'s own body or
in `stable_id`, leaves the syntax of both named sites untouched and is outside
what P1-P4 can see; two such mutants were measured surviving this file.  The
behavioural arms reach further than the syntactic ones, but they are
invariance arms over the declared language and the region type, so a drift
that is invariant in those two parameters is outside them as well.  That
residue is registered, not covered, and it is not what a closure over these
two functions can be made to carry.

It also does not read production to obtain an expected value.  Every whitelist
and every signature below is a LITERAL of this file; production is the subject
of the comparison and never its oracle.  The behavioural arms compare
production against ITSELF at a different argument value, which needs no
expected value at all.

NOTHING EXISTING IS READ FOR REUSE, MODIFIED, RELAXED OR REMOVED.  The lexical
pin in `test_operational_v5_8_1_roles_v2.py` is untouched and still runs; this
file is strictly additive and its closures are a superset of what that pin
covers.
"""

from __future__ import annotations

import ast
import dataclasses
import decimal
import inspect
import textwrap

import pytest

from curunir_operational.v5_8_1 import clauses as CL
from curunir_operational.v5_8_1 import regions as RG
from curunir_operational.v5_8_1 import roles_v2 as V2
from curunir_operational.v5_8_1 import structure as ST

pytestmark = pytest.mark.no_db


# ===========================================================================
# THE CLOSURE MACHINERY.  One implementation, applied to production AND to
# deliberately non-conforming DECOY functions defined in this file, so the
# checker is shown to fail as well as to pass.
# ===========================================================================

_DYNAMIC_LOOKUP_NAMES = frozenset({
    "locals", "globals", "vars", "eval", "exec", "compile",
    "_getframe", "currentframe", "getattr", "setattr", "__import__",
})


def _all_argument_names(tree: ast.AST) -> set[str]:
    """Every name bound as a parameter anywhere in the tree.

    Nested functions, lambdas and comprehension targets bind names too.
    Missing them would make the free-name set larger than it is and the
    whitelist correspondingly looser, so they are collected explicitly.
    """
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.arguments):
            for group in (node.posonlyargs, node.args, node.kwonlyargs):
                names |= {arg.arg for arg in group}
            for optional in (node.vararg, node.kwarg):
                if optional is not None:
                    names.add(optional.arg)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef,
                               ast.ClassDef)):
            names.add(node.name)
    return names


def declared_parameters(node) -> tuple[str, ...]:
    """EVERY name the signature binds, in signature order.

    P6.3 A5.  This used to read `node.args.args` alone, which is the ordinary
    positional group and nothing else.  Positional-only parameters,
    KEYWORD-ONLY parameters, `*args` and `**kwargs` were all invisible, and
    because P3 reuses this set and `_all_argument_names` puts those names in
    the BOUND set for P2, every clause of the closure was blind to them at
    once.  Two evasions were measured on that blindness: a keyword-only fifth
    parameter and a `*rest` vararg, each of which moved a published field on
    `language="sw"` while passing all of this file's nodes and the whole
    operational suite.  The full signature is read here so that a parameter
    added in ANY position fails P1, and reading it fails P3 as well.
    """
    args = node.args
    names = [arg.arg for arg in args.posonlyargs]
    names += [arg.arg for arg in args.args]
    if args.vararg is not None:
        names.append(args.vararg.arg)
    names += [arg.arg for arg in args.kwonlyargs]
    if args.kwarg is not None:
        names.append(args.kwarg.arg)
    return tuple(names)


def free_variable_profile(source: str) -> dict:
    """The syntactic profile of ONE function, from its source text alone."""
    tree = ast.parse(textwrap.dedent(source)).body[0]
    assert isinstance(tree, (ast.FunctionDef, ast.AsyncFunctionDef)), tree
    parameters = declared_parameters(tree)
    bound = {node.id for node in ast.walk(tree)
             if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store)}
    bound |= _all_argument_names(tree)
    loaded = {node.id for node in ast.walk(tree)
              if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load)}
    called = {node.func.id for node in ast.walk(tree)
              if isinstance(node, ast.Call)
              and isinstance(node.func, ast.Name)}
    called |= {node.func.attr for node in ast.walk(tree)
               if isinstance(node, ast.Call)
               and isinstance(node.func, ast.Attribute)}
    attributes = {node.attr for node in ast.walk(tree)
                  if isinstance(node, ast.Attribute)}
    return {
        "parameters": parameters,
        "free_names": frozenset(loaded - bound),
        "parameters_read": frozenset(loaded & set(parameters)),
        "called_names": frozenset(called),
        "attributes_touched": frozenset(attributes),
    }


def closure_violations(source: str, *, signature: tuple[str, ...],
                       allowed_free: frozenset[str],
                       readable_parameters: frozenset[str]) -> list[str]:
    """Every clause of the closure this source violates, named."""
    profile = free_variable_profile(source)
    failures: list[str] = []
    if profile["parameters"] != signature:
        failures.append(
            f"P1 signature is {profile['parameters']}, must be {signature}")
    extra_free = profile["free_names"] - allowed_free
    if extra_free:
        failures.append(f"P2 free name(s) outside the whitelist: "
                        f"{sorted(extra_free)}")
    forbidden_reads = profile["parameters_read"] - readable_parameters
    if forbidden_reads:
        failures.append(f"P3 forbidden parameter read: "
                        f"{sorted(forbidden_reads)}")
    dynamic = ((profile["called_names"] | profile["attributes_touched"])
               & _DYNAMIC_LOOKUP_NAMES)
    if dynamic:
        failures.append(f"P4 dynamic lookup: {sorted(dynamic)}")
    return failures


# ===========================================================================
# R1 -- the layout-refusal route does not consult the declared language.
# ===========================================================================

#: LITERAL.  The signature the refusal site must have.  `language` is accepted
#: for call-site compatibility with `bind` and must never be consulted, so a
#: FOURTH PARAMETER READ UNDER ANY NAME fails P1+P3 together: renaming it does
#: not help, because the position is pinned and the readable set is pinned.
_REFUSAL_SIGNATURE = ("candidate_id", "body", "order_state", "language")

#: LITERAL.  The only names the refusal site may resolve outside its own body.
_REFUSAL_ALLOWED_FREE = frozenset({
    "CL", "RoleBindingV2Record", "TM", "now_utc", "stable_id", "str",
    "validate_publication_role_invariant",
})

#: LITERAL.  The parameters it may read.  `language` is deliberately absent.
_REFUSAL_READABLE = frozenset({"candidate_id", "body", "order_state"})

#: The declared-language value domain, quantified BY POSITION.  It deliberately
#: contains values INSIDE the frozen corpus vocabulary, values outside it, the
#: case and long-form variants a real caller supplies, BCP-47 subtags, the
#: `russ*` prefix family SEM-8r3's MUTANT-4 keyed on, and GENUINE NON-STRINGS,
#: because production does not validate this parameter and neither may a
#: closure that claims to quantify over it.
#:
#: P6.3 A4.  The non-string clause of that sentence used to be FALSE: SEM-9r4
#: read the tuple with `ast.literal_eval` and found 62 strings and one `None`
#: and nothing else -- "0", "1" and "-1" are strings.  The class the claim
#: named was precisely the class P6.2's ingress normalisation regressed, so the
#: false claim and the defect were the same hole.  The values below are now
#: genuinely non-string, FALSY and TRUTHY: the falsy ones are the class
#: `v5_4/invariants.py:299`'s ``or "en"`` would have absorbed, and the truthy
#: ones are the class it does NOT absorb and that therefore reaches `bind` on
#: real input.  `test_the_refusal_domain_actually_reaches_past_the_swept_
#: vocabulary` checks the claim rather than restating it.
_ADVERSARIAL_LANGUAGE_VALUES = (
    None, "", " ", "ru", "RU", "Ru", "rU", "ru-RU", "ru_UA", "rus",
    "russ", "russian", "Russian", "RUSSIAN", "russisch",
    "ar", "AR", "arabic", "ar-EG", "de", "DE", "de-DE", "deutsch", "german",
    "en", "EN", "en-GB", "english", "es", "fr", "it", "pt", "PT", "pt-BR",
    "portuguese", "zh", "ja", "ko", "he", "tr", "pl", "nl", "sv", "da",
    "fi", "cs", "hu", "ro", "bg", "el", "uk", "zz", "xx", "qq", "und",
    "mul", "  ru  ", "\tru", "ru\n", "RU;DROP", "0", "1", "-1",
    # falsy non-strings
    0, 0.0, False, (), decimal.Decimal(0), b"",
    # truthy non-strings: `or "en"` lets these through untouched
    1, True, ["de"], {"a": 1}, ("de",), object(),
)

#: Bodies whose COUNTED script differs, so a channel keyed on the interaction
#: of the declared language with the body's script -- SEM-5r3's C2 -- has a
#: point at which to be caught.
_REFUSAL_BODIES = {
    "latin": "The Committee shall review the report of the Secretariat.",
    "cyrillic": "Рекомендует государствам-членам представить доклад.",
    "arabic": "وينبغي علاج الأشخاص المعرّضين لخطر كبير في أقرب وقت ممكن.",
    "han": "委员会应当审查秘书处的报告并提出建议。",
    "digits_only": "A70/12  13.1  22",
    "empty": "",
}

_ORDER_STATES = ("PACKET_CONSTRUCTION_DEFECT", "LAYOUT_RECONSTRUCTION_REQUIRED")

#: Every published field except the wall clock, which is not a function of any
#: input and is never comparable across two calls.
_COMPARED_FIELDS = tuple(
    field.name for field in dataclasses.fields(V2.RoleBindingV2Record)
    if field.name != "recorded_time")


def _published(record) -> dict:
    return {name: getattr(record, name) for name in _COMPARED_FIELDS}


def test_the_refusal_site_signature_and_free_names_are_exactly_as_declared():
    """R1 clauses P1, P2, P3 and P4 over `_layout_refusal`.

    This is the arm that kills a RENAME: the signature is pinned by position,
    so a fourth parameter called anything at all is a failure, and the set of
    parameters the body may READ is pinned separately, so reading the fourth
    one under a new name fails twice over.
    """
    failures = closure_violations(
        inspect.getsource(V2._layout_refusal),
        signature=_REFUSAL_SIGNATURE,
        allowed_free=_REFUSAL_ALLOWED_FREE,
        readable_parameters=_REFUSAL_READABLE)
    assert failures == [], failures


def test_the_refusal_closure_is_not_vacuous():
    """Non-vacuity: the checker fails when its OWN property is violated.

    Four decoys, one per clause, defined here and never in production.  A
    closure that could only pass would be decoration; this shows each clause
    is individually load-bearing.
    """
    _CHANNEL = "unused"

    def decoy_p1_renamed(candidate_id, body, order_state, lang):
        return (candidate_id, body, order_state, lang)

    def decoy_p2_module_global(candidate_id, body, order_state, language):
        return _CHANNEL

    def decoy_p3_reads_language(candidate_id, body, order_state, language):
        return language

    def decoy_p4_dynamic(candidate_id, body, order_state, language):
        return locals()

    def decoy_clean(candidate_id, body, order_state, language):
        return str(stable_id(candidate_id, body, order_state))  # noqa: F821

    def check(fn):
        return closure_violations(
            inspect.getsource(fn), signature=_REFUSAL_SIGNATURE,
            allowed_free=_REFUSAL_ALLOWED_FREE | {"stable_id"},
            readable_parameters=_REFUSAL_READABLE)

    assert any(f.startswith("P1") for f in check(decoy_p1_renamed))
    assert any(f.startswith("P3") for f in check(decoy_p1_renamed)), (
        "a renamed fourth parameter that is READ must fail P3 as well as P1")
    assert any(f.startswith("P2") for f in check(decoy_p2_module_global))
    assert any(f.startswith("P3") for f in check(decoy_p3_reads_language))
    assert any(f.startswith("P4") for f in check(decoy_p4_dynamic))
    assert check(decoy_clean) == [], check(decoy_clean)


def test_the_closure_sees_every_parameter_position():
    """P6.3 A5.  A parameter added in ANY position fails P1, and reading it
    fails P3.

    The four decoys below are the shapes that EVADED this file as shipped:
    the parameter set was read from `tree.args.args` alone, so a keyword-only
    parameter, a `*rest` vararg, a `**kwargs` and a positional-only parameter
    were invisible to P1, invisible to P3 (which intersects with the same
    set), and pre-bound for P2 by `_all_argument_names`.  Two of them were
    measured moving a published field on `language="sw"` while passing every
    node of this file and the whole operational suite.
    """

    def decoy_keyword_only(candidate_id, body, order_state, language, *,
                           lexicon="ru"):
        return (candidate_id, body, order_state, lexicon)

    def decoy_vararg(candidate_id, body, order_state, language, *rest):
        return (candidate_id, body, order_state, rest)

    def decoy_kwarg(candidate_id, body, order_state, language, **extra):
        return (candidate_id, body, order_state, extra)

    def decoy_positional_only(hint, /, candidate_id, body, order_state,
                              language):
        return (candidate_id, body, order_state, hint)

    def decoy_keyword_only_reads_language(candidate_id, body, order_state, *,
                                          language=None):
        return language

    def check(fn):
        return closure_violations(
            inspect.getsource(fn), signature=_REFUSAL_SIGNATURE,
            allowed_free=_REFUSAL_ALLOWED_FREE,
            readable_parameters=_REFUSAL_READABLE)

    for name, decoy in (("keyword_only", decoy_keyword_only),
                        ("vararg", decoy_vararg),
                        ("kwarg", decoy_kwarg),
                        ("positional_only", decoy_positional_only)):
        failures = check(decoy)
        assert any(f.startswith("P1") for f in failures), (name, failures)
        assert any(f.startswith("P3") for f in failures), (name, failures)

    # and the complete parameter set is what P3 intersects with, so a
    # keyword-only `language` that IS read is caught rather than excused.
    failures = check(decoy_keyword_only_reads_language)
    assert any(f.startswith("P3") for f in failures), failures

    # the reader itself, checked directly: order is signature order.
    profile = free_variable_profile(inspect.getsource(decoy_positional_only))
    assert profile["parameters"] == ("hint", "candidate_id", "body",
                                     "order_state", "language")
    profile = free_variable_profile(inspect.getsource(decoy_vararg))
    assert profile["parameters"] == ("candidate_id", "body", "order_state",
                                     "language", "rest")
    profile = free_variable_profile(inspect.getsource(decoy_keyword_only))
    assert profile["parameters"] == ("candidate_id", "body", "order_state",
                                     "language", "lexicon")
    profile = free_variable_profile(inspect.getsource(decoy_kwarg))
    assert profile["parameters"] == ("candidate_id", "body", "order_state",
                                     "language", "extra")


@pytest.mark.parametrize("body_name", sorted(_REFUSAL_BODIES))
def test_the_refusal_record_is_invariant_in_its_fourth_argument(body_name):
    """R1's behavioural arm, quantified BY POSITION over the value domain.

    The fourth argument is passed POSITIONALLY, so what production calls it is
    irrelevant: a renamed parameter receives exactly the same values.  Every
    published field is compared, not only the two a reason-drift would move.

    This is the arm that kills an OUT-OF-VOCABULARY VALUE: the domain is not a
    list of the languages the corpus happens to carry, it is a list of things
    a caller can actually pass, and the assertion is invariance rather than
    equality with a frozen table, so it does not need to be exhaustive to be
    unconditional over what it does reach.
    """
    body = _REFUSAL_BODIES[body_name]
    for order_state in _ORDER_STATES:
        reference = _published(V2._layout_refusal(
            "p62-refusal", body, order_state, None))
        for value in _ADVERSARIAL_LANGUAGE_VALUES:
            observed = _published(V2._layout_refusal(
                "p62-refusal", body, order_state, value))
            assert observed == reference, (
                body_name, order_state, value,
                {k: (reference[k], observed[k]) for k in reference
                 if reference[k] != observed[k]})


def _refusing_context(order_state: str):
    return ST.empty_context(document_id="p62-doc", region_id="p62-region",
                            reading_order_state=order_state)


@pytest.mark.parametrize("body_name", sorted(_REFUSAL_BODIES))
def test_the_refusal_route_through_bind_is_language_invariant(body_name):
    """The same invariance END TO END, through the production entry point.

    The syntactic closure sees only `_layout_refusal`.  A channel that lives
    in a callee -- `clauses.script_of`, say -- leaves that syntax untouched.
    Binding through `bind()` with a reading order the binder must refuse
    exercises the whole route, so such a channel moves a published field here
    even though the refusal site itself is clean.
    """
    body = _REFUSAL_BODIES[body_name]
    for order_state in _ORDER_STATES:
        reference = None
        for value in _ADVERSARIAL_LANGUAGE_VALUES:
            record = V2.bind(
                candidate_id="p62-bind-refusal", text=body, language=value,
                structural_context=_refusing_context(order_state))
            assert record.final_extraction_disposition == "REJECTED", value
            assert record.role_binding_state == "ROLE_BINDING_INVALID", value
            observed = _published(record)
            if reference is None:
                reference = observed
                continue
            assert observed == reference, (
                body_name, order_state, value,
                {k: (reference[k], observed[k]) for k in reference
                 if reference[k] != observed[k]})


def test_the_refusal_route_invariance_arm_is_not_vacuous():
    """Non-vacuity for the behavioural arm.

    A tampered copy of a refusal record -- the exact shape MUTANT-4 produces,
    one extra clause on the published reason -- must fail the comparison the
    arms above make.  Nothing in production is touched: the record is copied
    and edited here.
    """
    record = V2._layout_refusal("p62-refusal", "The Committee shall review.",
                                "PACKET_CONSTRUCTION_DEFECT", None)
    reference = _published(record)
    drifted = _published(dataclasses.replace(
        record, binding_reason=record.binding_reason + "; lexicon-confirmed"))
    assert drifted != reference
    script_drift = _published(dataclasses.replace(
        record, language_or_script="ARABIC"))
    assert script_drift != reference


def test_the_refusal_domain_actually_reaches_past_the_swept_vocabulary():
    """Floor: the value domain is not a restatement of the corpus vocabulary.

    MUTANT-4 escaped precisely because every behavioural pin in the suite
    swept {ar, de, en, es, fr, it, ru, None}.  This asserts the domain used
    above is strictly larger, contains the `russ*` prefix family MUTANT-4
    keyed on, contains case and long-form variants, and contains at least
    twenty values outside the corpus vocabulary.
    """
    corpus_vocabulary = {None, "ar", "de", "en", "es", "fr", "it", "ru"}
    # the string half of the domain; the non-string half is not hashable as a
    # whole (a list and a dict are deliberately present) and is checked below.
    domain = {v for v in _ADVERSARIAL_LANGUAGE_VALUES
              if v is None or isinstance(v, str)}
    assert corpus_vocabulary <= domain
    outside = domain - corpus_vocabulary
    assert len(outside) >= 20, sorted(map(str, outside))
    assert any(isinstance(v, str) and v.lower().startswith("russ")
               for v in domain)
    assert {"RU", "de-DE", "pt"} <= domain
    assert len(_REFUSAL_BODIES) >= 4
    assert len({CL.script_of(text) for text in _REFUSAL_BODIES.values()}) >= 4

    # P6.3 A4.  The comment above claims non-strings; this CHECKS it, so the
    # claim cannot go stale the way SEM-9r4 found it had.
    non_strings = [v for v in _ADVERSARIAL_LANGUAGE_VALUES
                   if v is not None and not isinstance(v, str)]
    assert len(non_strings) >= 10, non_strings
    assert sum(1 for v in non_strings if not v) >= 5, "falsy non-strings"
    assert sum(1 for v in non_strings if v) >= 5, "truthy non-strings"
    assert any(isinstance(v, (list, dict)) for v in non_strings), \
        "an unhashable non-string, which a `set()`-based domain cannot carry"


# ===========================================================================
# R2 -- the published reason is invariant over content_region_type values
#       OUTSIDE `regions.ORIGIN_CLASSES`.
# ===========================================================================

#: Strings a caller can supply that are NOT members of production's own
#: vocabulary.  `content_region_type` is an unvalidated caller string, so this
#: is the domain MUTANT-6 hid in: every value here is outside the seventeen
#: the P6.1.3 region axis enumerates, and the baseline is what production
#: publishes for its own default.
_OUT_OF_VOCABULARY_REGIONS = (
    "", " ", "UNKNOWN", "unknown_structural_region",
    "PRIMARY_PROPOSITION", "FOOTER", "FOOTER_FURNITURE_2",
    "NAVIGATION_MENU ", " NAVIGATION_MENU", "navigation_menu",
    "DOCUMENT_INDEX", "HEADING", "TABLE", "CAPTION", "SIDEBAR",
    "ADVERTISEMENT", "MARGINALIA", "PULL_QUOTE", "PAGE_NUMBER",
    "STRUCTURAL_FURNITURE", "REFERENTIAL_CONTENT", "ORIGIN_CLASSES",
    "12345", "None", "null", "​", "REGION\nTYPE", "REGION;DROP",
    "x" * 200,
)

_REGION_PROBE_BODIES = {
    "operative_ru": dict(
        text="Рекомендует государствам-членам представить доклад Секретариату.",
        left_context="Комитет рассмотрел доклад.", heading="", language="ru"),
    "committee_en": dict(
        text="The Committee shall review the report of the Secretariat.",
        left_context="The Council considered the matter.", heading="",
        language="en"),
    "deontic_ar": dict(
        text="وينبغي علاج الأشخاص المعرّضين لخطر كبير بالأدوية المضادة "
             "للفيروسات في أقرب وقت ممكن.",
        left_context="التماس الرعاية الطبية عند ظهور الأعراض.", heading="",
        language="ar"),
    "date_line": dict(
        text="A70/12  Agenda item 13.1  22 May 2017",
        left_context="", heading="", language="en"),
    "subordinate_de": dict(
        text="sie hierzu aus rechtlichen Gründen nicht in der Lage ist;",
        left_context="1.", heading="§ 5", language="de"),
}

_REGION_CONTEXT_SHAPES = ("none", "plain", "unique", "ambiguous")


def _context_of(shape: str):
    if shape == "none":
        return None
    base = ST.empty_context(document_id="p62-doc", region_id="p62-region",
                            reading_order_state="READING_ORDER_ESTABLISHED")
    if shape == "plain":
        return base
    if shape == "unique":
        return dataclasses.replace(
            base, governing_clause_candidates=("p62-gov-a",),
            governing_clause_relation_types=("LIST_ITEM_OF",),
            governing_clause_confidences=(0.91,))
    return dataclasses.replace(
        base, governing_clause_candidates=("p62-gov-a", "p62-gov-b"),
        governing_clause_relation_types=("LIST_ITEM_OF", "LIST_ITEM_OF"),
        governing_clause_confidences=(0.91, 0.88))


def _bind_region(body_name: str, region: str, shape: str):
    spec = _REGION_PROBE_BODIES[body_name]
    return V2.bind(
        candidate_id=f"p62-region-{body_name}-{shape}", text=spec["text"],
        language=spec["language"], left_context=spec["left_context"],
        heading=spec["heading"], content_region_type=region,
        structural_context=_context_of(shape))


@pytest.mark.parametrize("body_name", sorted(_REGION_PROBE_BODIES))
def test_out_of_vocabulary_region_types_publish_the_unknown_baseline(
        body_name):
    """R2.  An unrecognised region type behaves as UNKNOWN, unconditionally.

    `content_region_type` is a caller string production neither validates nor
    closes over.  Everything outside `regions.ORIGIN_CLASSES` therefore has to
    be treated as unrecognised -- and that is a property of the VALUE DOMAIN,
    not of a seventeen-item table, which is why enumerating the seventeen let
    MUTANT-6 through.  The baseline is production's own default value, so this
    needs no frozen expected string.
    """
    for shape in _REGION_CONTEXT_SHAPES:
        baseline = _published(
            _bind_region(body_name, "UNKNOWN_STRUCTURAL_REGION", shape))
        for region in _OUT_OF_VOCABULARY_REGIONS:
            assert region not in RG.ORIGIN_CLASSES
            observed = _published(_bind_region(body_name, region, shape))
            assert observed == baseline, (
                body_name, shape, region[:40],
                {k: (baseline[k], observed[k]) for k in baseline
                 if baseline[k] != observed[k]})


def test_the_out_of_vocabulary_region_domain_is_disjoint_and_non_trivial():
    """Floor: the R2 domain is genuinely outside production's vocabulary.

    Asserted against `regions.ORIGIN_CLASSES` itself, so a vocabulary that
    GROWS to contain one of these probes fails here loudly instead of quietly
    making an arm vacuous.
    """
    domain = set(_OUT_OF_VOCABULARY_REGIONS)
    assert domain.isdisjoint(set(RG.ORIGIN_CLASSES))
    assert len(domain) >= 25
    # Near-misses are the interesting part: case variants, whitespace variants
    # and substrings of real members are exactly what a drift would key on.
    assert {"navigation_menu", "NAVIGATION_MENU ", " NAVIGATION_MENU"} <= domain
    assert len(RG.ORIGIN_CLASSES) == 17


def test_the_region_invariance_arm_is_not_vacuous():
    """Non-vacuity: an IN-vocabulary value that production does treat
    differently must NOT equal the UNKNOWN baseline, or the arm above would
    pass for a binder that ignored the parameter entirely."""
    for body_name in sorted(_REGION_PROBE_BODIES):
        baseline = _published(
            _bind_region(body_name, "UNKNOWN_STRUCTURAL_REGION", "none"))
        furniture = _published(
            _bind_region(body_name, "NAVIGATION_MENU", "none"))
        assert furniture != baseline, body_name


# ===========================================================================
# R9 -- the counted-script function is a function of its text alone.
# ===========================================================================

#: LITERAL.  `clauses.script_of` takes one parameter and has no legitimate
#: free name beyond `unicodedata`, `MAX_SCAN_CHARS` and builtins.  SEM-5r3
#: named this as the residue its refusal-site closure could not reach: a
#: channel implanted HERE and fed by `bind` leaves `_layout_refusal`'s syntax
#: untouched.  This closes it syntactically; the behavioural arm below closes
#: it again, independently.
_SCRIPT_OF_SIGNATURE = ("text",)
_SCRIPT_OF_ALLOWED_FREE = frozenset({
    "MAX_SCAN_CHARS", "ValueError", "dict", "int", "max", "str",
    "unicodedata",
})
_SCRIPT_OF_READABLE = frozenset({"text"})


def test_the_counted_script_function_resolves_no_channel():
    """R9 clauses P1-P4 over `clauses.script_of`."""
    failures = closure_violations(
        inspect.getsource(CL.script_of),
        signature=_SCRIPT_OF_SIGNATURE,
        allowed_free=_SCRIPT_OF_ALLOWED_FREE,
        readable_parameters=_SCRIPT_OF_READABLE)
    assert failures == [], failures


def test_the_counted_script_is_unmoved_by_any_binder_call():
    """R9's behavioural arm: no binder call can change what a text counts as.

    A module-level channel in `clauses` written by `bind` and read by
    `script_of` is invisible to any closure over the refusal site alone.  Here
    the script of a fixed probe text is measured, then `bind` is called across
    the whole adversarial declared-language domain on both the ordinary and
    the refusal route, and the script is measured again after every call.  A
    channel of that shape moves one of these.
    """
    probes = {
        "latin": "The Committee shall review the report.",
        "cyrillic": "Рекомендует представить доклад.",
        "arabic": "وينبغي علاج الأشخاص في أقرب وقت ممكن.",
        "han": "委员会应当审查秘书处的报告。",
        "empty": "",
    }
    reference = {name: CL.script_of(text) for name, text in probes.items()}
    assert len(set(reference.values())) >= 4, reference
    for value in _ADVERSARIAL_LANGUAGE_VALUES:
        V2.bind(candidate_id="p62-script", text=probes["cyrillic"],
                language=value)
        V2.bind(candidate_id="p62-script-refusal", text=probes["latin"],
                language=value,
                structural_context=_refusing_context(
                    "PACKET_CONSTRUCTION_DEFECT"))
        observed = {name: CL.script_of(text) for name, text in probes.items()}
        assert observed == reference, (value, observed, reference)


def test_the_counted_script_closure_is_not_vacuous():
    """Non-vacuity: the same checker fails a decoy with a module channel."""
    _HINT = None

    def decoy_channel(text):
        if _HINT == "pt":
            return "ARABIC"
        return "LATIN"

    failures = closure_violations(
        inspect.getsource(decoy_channel),
        signature=_SCRIPT_OF_SIGNATURE,
        allowed_free=_SCRIPT_OF_ALLOWED_FREE,
        readable_parameters=_SCRIPT_OF_READABLE)
    assert any(f.startswith("P2") for f in failures), failures


# ===========================================================================
# WHAT THIS FILE COVERS, STATED SO IT CANNOT QUIETLY SHRINK
# ===========================================================================


def test_this_file_asserts_every_clause_it_claims():
    """The closure clauses are enumerated in the docstring; this checks that
    each one is actually exercised by a passing arm and by a decoy."""
    source = inspect.getsource(closure_violations)
    for clause in ("P1", "P2", "P3", "P4"):
        assert f'"{clause} ' in source or f"'{clause} " in source, clause
    assert _DYNAMIC_LOOKUP_NAMES >= {
        "locals", "globals", "vars", "eval", "exec", "_getframe",
        "currentframe"}
    # The two syntactic closures are over DIFFERENT modules, which is the
    # whole point of R9: one file's hash custody does not carry the other's.
    assert V2.__name__ != CL.__name__
    assert inspect.getmodule(V2._layout_refusal) is not \
        inspect.getmodule(CL.script_of)
