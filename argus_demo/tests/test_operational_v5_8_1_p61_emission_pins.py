"""P6.1 R2/R3/R4 — what the two emission sites PUBLISH, pinned behaviourally.

The shipped `test_operational_v5_8_1_roles_v2.py` matrix pins 29 of the 31
comparable fields the refusal route emits.  The two it does not compare are
`language_or_script` and `binding_reason`, and both were shown to carry live
defects on this exact substrate:

* `language_or_script` was guarded only by a LEXICAL pin (the refusal's own
  syntax tree must not read the `language` parameter).  That pin has one
  genuine positive leg -- a direct in-function read -- and is blind to a
  rename, to a `locals()` lookup and to a caller-side rewrite of the argument.
  R2 adds the BEHAVIOURAL invariant the lexical pin was meant to express.  The
  lexical pin is NOT removed: it is the only thing that kills the
  language-conditional `binding_reason` mutant, and it is not weakened here.

* `binding_reason` was pinned only by substring containment, which a
  provenance inversion, an appended false safety assertion and a
  body-conditional covert channel all survive.  R3 pins it by EXACT EQUALITY
  across the same 112-case matrix; the value is a pure function of
  `order_state`.  The containment pins are left in place.

* R4 declares and pins that `internal_state` is, BY DESIGN, the SELECTION-layer
  verdict -- installed before the M7--M15 role rewrites and deliberately not
  refreshed -- and pins that the emitted `binding_reason` nevertheless makes no
  claim the record's own other fields contradict.

Nothing in the shipped test files is read, imported for reuse, modified or
relaxed by this module.  The matrix constants below are re-declared here and
then RECONCILED against the shipped file, so a later divergence is a failure
rather than a silent drift.
"""

from __future__ import annotations

import ast
import importlib.util
import inspect
import pathlib
import textwrap

import pytest

from curunir_operational.v5_1.models import stable_id
from curunir_operational.v5_8_1 import clauses as CL
from curunir_operational.v5_8_1 import roles_v2 as V2
from curunir_operational.v5_8_1 import structure as ST

pytestmark = pytest.mark.no_db


_TESTS_DIR = pathlib.Path(__file__).resolve().parent
_SHIPPED_MATRIX_FILE = _TESTS_DIR / "test_operational_v5_8_1_roles_v2.py"
_R1_DERIVATION_FILE = _TESTS_DIR / "test_operational_v5_8_1_p61_derivation.py"

#: The frozen identity prefix, written as a LITERAL for the same reason the
#: shipped file writes it as one: reading it back out of the module under test
#: would let a single edit move both sides of the comparison at once.
_BINDING_ID_PREFIX = "v5-8-1-bindingv2"

#: The two reading-order states that divert `bind` into the refusal.
_UNSOUND_ORDER_STATES = [
    "PACKET_CONSTRUCTION_DEFECT",
    "LAYOUT_RECONSTRUCTION_REQUIRED",
]

#: The reading-order states that do NOT divert, so `bind` runs the adapter.
#: Both current "routes" of the shipped matrix terminate in `_layout_refusal`,
#: which makes route-agreement evidence vacuous; these arms fix that.
_SOUND_ORDER_STATES = [
    "READING_ORDER_ESTABLISHED",
    "READING_ORDER_RECOVERABLE",
    "READING_ORDER_NOT_APPLICABLE",
]

_REFUSAL_LANGUAGES = [None, "en", "de", "fr", "ru", "ar", "zz"]

_REFUSAL_BODY_SHAPES = {
    "empty": "",
    "english_short": "The Committee shall review the report.",
    "annex_token": "Annex II to the report of the Committee.",
    "over_two_hundred_chars": "The Committee shall review the report. " * 12,
    "german": "Der Ausschuss prüft den Bericht des Sekretariats.",
    "russian": "Комитет рассматривает доклад Секретариата.",
    "arabic": "تستعرض اللجنة تقرير الأمانة العامة.",
    "digits_only": "12345",
}

_REFUSAL_CANDIDATE_IDS = ["refusal-plain", "zz-refusal-prefixed"]

_REFUSAL_INPUT_PAIRS = [
    (language, body_name)
    for language in _REFUSAL_LANGUAGES
    for body_name in sorted(_REFUSAL_BODY_SHAPES)
]

#: `bind(text="")` raises IndexError on the SOUND path.  That is a pre-existing
#: defect, identical in every generation of this module, logged as a
#: non-blocking Gate-5 item and NOT in this programme's authority.  It is
#: excluded from the sound arms only, and the exclusion is asserted to be
#: exactly one shape so it cannot quietly grow.
_SOUND_EXCLUDED_BODY_SHAPES = {"empty"}
_SOUND_BODY_NAMES = sorted(
    set(_REFUSAL_BODY_SHAPES) - _SOUND_EXCLUDED_BODY_SHAPES)
_SOUND_INPUT_PAIRS = [
    (language, body_name)
    for language in _REFUSAL_LANGUAGES
    for body_name in _SOUND_BODY_NAMES
]


def _load_shipped_matrix_module():
    """Import the shipped matrix file under a private name, read-only."""
    spec = importlib.util.spec_from_file_location(
        "_p61_shipped_matrix_readonly", _SHIPPED_MATRIX_FILE)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_matrix_reconciles_with_the_shipped_one():
    """R2/R3 measure the EXISTING matrix, not a private one that drifted.

    Every constant this module re-declares is compared against the shipped
    file's own.  If a later edit moves the shipped matrix, this fails here
    rather than leaving two matrices silently disagreeing about what "the
    112-case matrix" means.
    """
    shipped = _load_shipped_matrix_module()
    assert _BINDING_ID_PREFIX == shipped._BINDING_ID_PREFIX
    assert _UNSOUND_ORDER_STATES == shipped._UNSOUND_ORDER_STATES
    assert _REFUSAL_LANGUAGES == shipped._REFUSAL_LANGUAGES
    assert _REFUSAL_BODY_SHAPES == shipped._REFUSAL_BODY_SHAPES
    assert _REFUSAL_CANDIDATE_IDS == shipped._REFUSAL_CANDIDATE_IDS
    assert _REFUSAL_INPUT_PAIRS == shipped._REFUSAL_INPUT_PAIRS
    # 7 languages x 8 body shapes x 2 unsound order states.
    assert len(_REFUSAL_INPUT_PAIRS) * len(_UNSOUND_ORDER_STATES) == 112
    assert _SOUND_EXCLUDED_BODY_SHAPES == {"empty"}
    assert len(_SOUND_BODY_NAMES) == len(_REFUSAL_BODY_SHAPES) - 1
    assert set(_SOUND_ORDER_STATES).isdisjoint(_UNSOUND_ORDER_STATES)
    assert set(_SOUND_ORDER_STATES) | set(_UNSOUND_ORDER_STATES) == set(
        ST.READING_ORDER_STATES)


def test_the_r1_derivation_file_keeps_the_no_db_marker():
    """R1's protection is a no-op without it, and the file cannot say so.

    A file whose `no_db` marker is removed is SKIPPED wholesale when Postgres
    is unreachable -- including any self-check written inside it.  The guard
    therefore lives here, in a different file, and reads the source.
    """
    tree = ast.parse(_R1_DERIVATION_FILE.read_text(encoding="utf-8"))
    marks = [
        node for node in ast.walk(tree)
        if isinstance(node, ast.Attribute) and node.attr == "no_db"
    ]
    assert marks, "test_operational_v5_8_1_p61_derivation.py lost pytest.mark.no_db"
    assigned = [
        node for node in tree.body
        if isinstance(node, ast.Assign)
        and any(isinstance(t, ast.Name) and t.id == "pytestmark"
                for t in node.targets)
    ]
    assert assigned, "the marker must be module-level `pytestmark`"


# --- R2: language independence, as BEHAVIOUR ---------------------------------
#
# For a fixed (candidate_id, body, order_state) the refusal's emitted
# `language_or_script` must be the same value for every caller-declared
# language, and that value must be the COUNTED script of the body.  A rename, a
# `locals()` lookup, a caller-side rewrite of the argument and an arbitrary
# drift all move an emitted value, so all of them fail this, and none of them
# fails the lexical pin alone.


def _refusal_records(candidate_id, body, order_state, language):
    """Both refusal routes for one input point, keyed by route name."""
    context = ST.empty_context(document_id="d", region_id="r",
                               reading_order_state=order_state)
    return {
        "helper": V2._layout_refusal(candidate_id, body, order_state, language),
        "bind": V2.bind(candidate_id=candidate_id, language=language,
                        text=body, structural_context=context),
    }


@pytest.mark.parametrize("order_state", _UNSOUND_ORDER_STATES)
@pytest.mark.parametrize("body_name", sorted(_REFUSAL_BODY_SHAPES))
def test_the_refusal_language_or_script_is_invariant_across_declared_language(
        body_name, order_state):
    """One value per body, whatever the caller declares, on BOTH routes."""
    body = _REFUSAL_BODY_SHAPES[body_name]
    counted = CL.script_of(body)
    emitted = {}
    for candidate_id in _REFUSAL_CANDIDATE_IDS:
        for language in _REFUSAL_LANGUAGES:
            for route, record in _refusal_records(
                    candidate_id, body, order_state, language).items():
                emitted[(candidate_id, language, route)] = \
                    record.language_or_script
    distinct = sorted(set(emitted.values()))
    assert distinct == [counted], (
        f"body={body_name} order_state={order_state}: the refusal published "
        f"{distinct} across declared languages; the counted script of the body "
        f"is {counted!r}.  Emitted map: {emitted}")


@pytest.mark.parametrize("order_state", _UNSOUND_ORDER_STATES)
@pytest.mark.parametrize("language,body_name", _REFUSAL_INPUT_PAIRS)
def test_the_refusal_language_or_script_is_the_counted_script_of_the_body(
        language, body_name, order_state):
    """The published value is a property of the body, not of the caller."""
    body = _REFUSAL_BODY_SHAPES[body_name]
    for candidate_id in _REFUSAL_CANDIDATE_IDS:
        for route, record in _refusal_records(
                candidate_id, body, order_state, language).items():
            assert record.language_or_script == CL.script_of(body), (
                f"{route} route on language={language!r} body={body_name} "
                f"candidate_id={candidate_id!r}")


def test_the_language_independence_pin_would_notice_a_moved_value():
    """Control: the matrix contains a body whose counted script is not LATIN.

    An invariance check over a set of inputs that all produce the same value
    anyway cannot fail.  These assertions fail if a later edit shrinks the
    matrix back to one script or drops the declared-language values that
    override a counted script inside `clauses.detect`.
    """
    counted = {CL.script_of(b) for b in _REFUSAL_BODY_SHAPES.values()}
    assert {"LATIN", "CYRILLIC", "ARABIC", "UNKNOWN"} <= counted
    assert "ru" in _REFUSAL_LANGUAGES and "ar" in _REFUSAL_LANGUAGES
    assert None in _REFUSAL_LANGUAGES
    # There is at least one point where the declared language and the counted
    # script disagree, which is the only place an evasion can hide.
    assert any(CL.script_of(_REFUSAL_BODY_SHAPES[name]) != "CYRILLIC"
               for name in _REFUSAL_BODY_SHAPES)


# --- R2 (SEM5-F04): the SOUND arm, so route agreement is not self-agreement --
#
# Both "routes" of the shipped matrix end in `_layout_refusal`, so comparing
# them compares the refusal with itself.  These arms drive `bind` through the
# ADAPTER and pin what it publishes.


def _sound_record(candidate_id, body, order_state, language):
    context = ST.empty_context(document_id="d", region_id="r",
                               reading_order_state=order_state)
    return V2.bind(candidate_id=candidate_id, language=language, text=body,
                   structural_context=context)


@pytest.mark.parametrize("order_state", _SOUND_ORDER_STATES)
@pytest.mark.parametrize("body_name", _SOUND_BODY_NAMES)
def test_the_sound_order_states_reach_the_adapter_not_the_refusal(
        body_name, order_state):
    """Non-vacuity: these arms genuinely exercise the second emission site."""
    body = _REFUSAL_BODY_SHAPES[body_name]
    for language in _REFUSAL_LANGUAGES:
        record = _sound_record("sound-plain", body, order_state, language)
        assert record.binding_id == stable_id(
            _BINDING_ID_PREFIX, "sound-plain", record.role_binding_state)
        assert record.binding_id != stable_id(
            _BINDING_ID_PREFIX, "sound-plain", order_state)
        assert record.repair_requirement != (
            "re-extract the manifestation with a sound reading order")
        assert "reading order is" not in record.binding_reason
        # CONSTRUCTION_DEFECT is emitted by `_layout_refusal` and by nothing
        # else: the terminal adapter cannot produce it.
        assert record.proposition_status != "CONSTRUCTION_DEFECT"
        assert record.analyses_considered > 0


@pytest.mark.parametrize("order_state", _SOUND_ORDER_STATES)
@pytest.mark.parametrize("language,body_name", _SOUND_INPUT_PAIRS)
def test_the_adapter_route_publishes_the_clause_evidence_script(
        language, body_name, order_state):
    """The adapter publishes `clauses.detect(...).language_or_script`, exactly.

    This is NOT the language-independent invariant the refusal route satisfies.
    `clauses.detect` derives its emitted value from an analyzer-routing FAMILY
    that a caller-declared `ru` or `ar` overrides, so the adapter route is
    caller-language dependent.  That asymmetry is PRE-EXISTING in every
    generation of this module, its root cause lives in `clauses.py`, and
    repairing it is explicitly outside this programme's authority.  It is
    pinned here rather than left unmeasured, so it cannot move unnoticed.
    """
    body = _REFUSAL_BODY_SHAPES[body_name]
    record = _sound_record("sound-plain", body, order_state, language)
    assert record.language_or_script == CL.detect(
        body, language=language).language_or_script


#: The measured, currently-true adapter-route derivation, written as a literal
#: so it does not move with the module under test.
def _expected_adapter_script(body: str, language: str | None) -> str:
    counted = CL.script_of(body)
    declared = (language or "").lower()[:2]
    if counted == "ARABIC" or declared == "ar":
        return "ARABIC"
    if counted == "CYRILLIC" or declared == "ru":
        return "CYRILLIC"
    return counted


@pytest.mark.parametrize("order_state", _SOUND_ORDER_STATES)
@pytest.mark.parametrize("language,body_name", _SOUND_INPUT_PAIRS)
def test_the_adapter_route_script_matches_the_recorded_derivation(
        language, body_name, order_state):
    """Independent statement of the same value, not read from production."""
    body = _REFUSAL_BODY_SHAPES[body_name]
    record = _sound_record("sound-plain", body, order_state, language)
    assert record.language_or_script == _expected_adapter_script(body, language)


def test_the_two_routes_disagree_on_language_or_script_pre_existing():
    """The measured asymmetry between the two emission sites, pinned.

    A Latin body declared `ru` publishes CYRILLIC through the adapter and LATIN
    through the refusal.  This test asserts the disagreement EXISTS today; it
    does not endorse it.  When the `clauses.py` root cause is repaired under
    its own authority, this test must be REPLACED by the language-independence
    invariant above -- tightened, never deleted to hide the change.
    """
    body = _REFUSAL_BODY_SHAPES["english_short"]
    assert CL.script_of(body) == "LATIN"
    disagreeing = {
        language
        for language in _REFUSAL_LANGUAGES
        if _sound_record("sound-plain", body, "READING_ORDER_ESTABLISHED",
                         language).language_or_script
        != V2._layout_refusal("sound-plain", body,
                              "PACKET_CONSTRUCTION_DEFECT",
                              language).language_or_script
    }
    assert disagreeing == {"ru", "ar"}


# --- R3: binding_reason on the refusal route, by EXACT EQUALITY --------------
#
# The value is a pure function of `order_state`.  Substring containment lets a
# provenance inversion, an appended false safety claim and a body-conditional
# covert channel through; exact equality does not.  The expected string is a
# LITERAL here, so one edit cannot move both sides.


def _expected_refusal_reason(order_state: str) -> str:
    return (f"reading order is {order_state}; the span's word sequence is the "
            "extractor's, not the document's")


@pytest.mark.parametrize("order_state", _UNSOUND_ORDER_STATES)
@pytest.mark.parametrize("language,body_name", _REFUSAL_INPUT_PAIRS)
def test_the_refusal_binding_reason_is_exactly_the_order_state_sentence(
        language, body_name, order_state):
    """Nothing else may appear in the published reason, on either route."""
    body = _REFUSAL_BODY_SHAPES[body_name]
    expected = _expected_refusal_reason(order_state)
    for candidate_id in _REFUSAL_CANDIDATE_IDS:
        for route, record in _refusal_records(
                candidate_id, body, order_state, language).items():
            assert record.binding_reason == expected, (
                f"{route} route moved binding_reason on language={language!r} "
                f"body={body_name} candidate_id={candidate_id!r}")


def test_the_refusal_binding_reason_is_a_pure_function_of_order_state():
    """One reason per order_state across the whole matrix, and only two."""
    reasons = {}
    for order_state in _UNSOUND_ORDER_STATES:
        for language, body_name in _REFUSAL_INPUT_PAIRS:
            for candidate_id in _REFUSAL_CANDIDATE_IDS:
                for record in _refusal_records(
                        candidate_id, _REFUSAL_BODY_SHAPES[body_name],
                        order_state, language).values():
                    reasons.setdefault(order_state, set()).add(
                        record.binding_reason)
    assert {k: sorted(v) for k, v in reasons.items()} == {
        state: [_expected_refusal_reason(state)]
        for state in _UNSOUND_ORDER_STATES}
    assert len({next(iter(v)) for v in reasons.values()}) == len(
        _UNSOUND_ORDER_STATES)


def test_no_caller_body_content_reaches_the_published_reason():
    """A covert channel is a reason that varies with the body.  It may not."""
    marker = "CANARY-9f3a1c"
    body = f"The Committee shall review {marker} the report."
    for order_state in _UNSOUND_ORDER_STATES:
        for language in _REFUSAL_LANGUAGES:
            for record in _refusal_records("refusal-plain", body, order_state,
                                           language).values():
                assert marker not in record.binding_reason
                assert record.binding_reason == _expected_refusal_reason(
                    order_state)


# --- R4: internal_state is the SELECTION-layer verdict, BY DESIGN ------------
#
# `internal` is installed by rule M2 before the M7--M15 role rewrites and is
# deliberately never refreshed: it answers "what did the selector conclude",
# not "what do the final role states look like".  That is a DECLARED design
# property, not an accident, and these tests hold it in place.  Any future
# change of that semantics -- in either direction -- must fail here and be
# argued, not slipped in.
#
# The consequence is that the D39 axes derived from it can be more permissive
# than a post-rewrite recomputation would be.  That consequence is pinned
# explicitly below rather than left implicit.

#: M2's own premise, verbatim from the rule's guard.
_M2_PREMISE_SUBJECT_STATES = ("ANAPHORIC_SUBJECT_RECOVERABLE",
                              "IMPLICIT_CONTEXT_BOUND_SUBJECT",
                              "GOVERNING_CLAUSE_SUBJECT")

#: An Arabic deontic over a masdar: M2 installs
#: MULTIPLE_BINDINGS_RECOVERABLE over IMPLICIT_CONTEXT_BOUND_SUBJECT, then M13b
#: rewrites the subject to the impersonal construction.
_M13B_TRIGGER = dict(
    candidate_id="p61-m13b", language="ar",
    text="وينبغي علاج الأشخاص المعرّضين لخطر كبير بالأدوية المضادة للفيروسات "
         "في أقرب وقت ممكن.",
    left_context="التماس الرعاية الطبية عند ظهور الأعراض.",
    content_region_type="PRIMARY_PROPOSITION_CONTENT")

#: A Russian standalone-coordinator-initial conjunct: M13d rewrites the subject
#: to the inherited coordinate subject.
_M13D_TRIGGER = dict(
    candidate_id="p61-m13d", language="ru",
    text="но и позволяют добиться жизнестойкости в долгосрочной перспективе, "
         "что способствует",
    left_context="психосоциальной поддержке, которые не только удовлетворяют "
                 "насущные потребности,",
    content_region_type="UNKNOWN_STRUCTURAL_REGION")

#: An anaphoric subject whose bounded left context holds no antecedent: the
#: terminal adapter reports the subject UNRESOLVED and the context ABSENT.
_ANTECEDENT_ABSENT_TRIGGER = dict(
    candidate_id="p61-anaphor", language="de",
    text="sie hierzu aus rechtlichen Gründen nicht in der Lage ist;",
    left_context="1.",
    content_region_type="PRIMARY_PROPOSITION_CONTENT")


@pytest.mark.parametrize("trigger,final_subject_state", [
    (_M13B_TRIGGER, "EXPLETIVE_OR_IMPERSONAL_CONSTRUCTION"),
    (_M13D_TRIGGER, "INHERITED_COORDINATE_SUBJECT"),
])
def test_internal_state_is_the_pre_rewrite_selection_verdict_by_design(
        trigger, final_subject_state):
    """The emitted internal state survives a rewrite that defeats M2's premise.

    M2's guard reads the subject state as it was BEFORE M7--M15.  On these
    inputs a later rule moves the subject out of that premise, and the emitted
    `internal_state` still reports what the selection layer concluded.  This is
    the declared design: `internal_state` is the selection verdict, and the
    role states are the post-rewrite semantics.  They answer different
    questions and are not required to agree.
    """
    record = V2.bind(**trigger)
    assert record.subject_state == final_subject_state
    assert record.subject_state not in _M2_PREMISE_SUBJECT_STATES
    assert record.internal_state == "MULTIPLE_BINDINGS_RECOVERABLE"


def test_the_declared_consequence_of_the_pre_rewrite_verdict_is_pinned():
    """What the stale-by-design verdict actually publishes, field by field.

    Written out because it is the part a reader would otherwise have to
    reconstruct: the D39 axes are derived from the SELECTION verdict, so
    `binding_uniqueness` is UNIQUE and the terminal is ESTABLISHED even though
    the final subject is absent by construction.  The record does not publish:
    the disposition is QUARANTINED.  If any of this moves, it is a decision,
    not a detail.
    """
    record = V2.bind(**_M13B_TRIGGER)
    assert record.internal_state == "MULTIPLE_BINDINGS_RECOVERABLE"
    assert record.subject_state == "EXPLETIVE_OR_IMPERSONAL_CONSTRUCTION"
    assert record.subject_completeness == "ABSENT_BY_CONSTRUCTION"
    assert record.proposition_status == "PROPOSITION"
    assert record.binding_uniqueness == "UNIQUE"
    assert record.role_binding_state == "ROLE_BINDING_ESTABLISHED"
    assert record.final_extraction_disposition == "QUARANTINED"


def test_internal_is_frozen_before_the_role_states_stop_moving():
    """Structural pin: no refresh of `internal` was added after the rewrites.

    A future edit that recomputes the internal state after M7--M15 changes the
    published meaning of the field on real units.  It must fail a test.
    """
    source = textwrap.dedent(inspect.getsource(V2.bind))
    tree = ast.parse(source)

    def assigned_linenos(name):
        return [
            target.lineno
            for node in ast.walk(tree)
            if isinstance(node, (ast.Assign, ast.AugAssign, ast.AnnAssign))
            for target in (node.targets if isinstance(node, ast.Assign)
                           else [node.target])
            for target in ([target] if isinstance(target, ast.Name)
                           else list(ast.walk(target)))
            if isinstance(target, ast.Name) and target.id == name
        ]

    internal_writes = assigned_linenos("internal")
    subject_writes = assigned_linenos("subject_state")
    predicate_writes = assigned_linenos("predicate_state")
    assert internal_writes, "bind no longer assigns `internal`"
    assert subject_writes and predicate_writes
    assert max(internal_writes) < max(subject_writes), (
        "`internal` is written after the last subject-state rewrite: the "
        "selection verdict is no longer the pre-rewrite one")
    assert max(internal_writes) < max(predicate_writes)
    # And the value that is frozen is the value that is serialized.
    facts_calls = [
        node for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "_terminal_facts"
    ]
    assert len(facts_calls) == 1
    passed = {
        keyword.arg: keyword.value for keyword in facts_calls[0].keywords}
    assert isinstance(passed["internal_state"], ast.Name)
    assert passed["internal_state"].id == "internal"


# --- R4: no published claim may be contradicted by the same record -----------
#
# Each phrase the main-path reason can contain is a CLAIM.  Every claim is
# paired here with the predicate the record's OTHER emitted fields must
# satisfy, so a reason that answers an earlier generation than the fields
# beside it is a failure.

_CLAIM_CONTRACTS = {
    "the subject is recoverable from context the caller supplies":
        lambda r: (r.subject_completeness == "RECOVERABLE_BOUNDED"
                   and r.repairability == "BOUNDED_REPAIR_PROVEN"),
    "the subject is named to context the caller supplies but its recovery is "
    "not proven":
        lambda r: (r.subject_completeness == "RECOVERABLE_BOUNDED"
                   and r.repairability != "BOUNDED_REPAIR_PROVEN"),
    "the construction binds no subject":
        lambda r: r.subject_completeness == "ABSENT_BY_CONSTRUCTION",
    "the subject is not resolved":
        lambda r: r.subject_completeness == "UNRESOLVED",
    "the subject is bound":
        lambda r: r.subject_completeness in ("BOUND_LOCAL", "BOUND_CONTEXT"),
    "the predicate is bound in span":
        lambda r: (r.predicate_span is not None
                   and r.predicate_completeness not in
                   ("UNRESOLVED", "ABSENT_BY_CONSTRUCTION")),
    "the predicate is bound to the declared context":
        lambda r: (r.predicate_span is None
                   and r.predicate_completeness not in
                   ("UNRESOLVED", "ABSENT_BY_CONSTRUCTION")),
    "the construction carries no predicate":
        lambda r: r.predicate_completeness == "ABSENT_BY_CONSTRUCTION",
    "the predicate is not resolved":
        lambda r: r.predicate_completeness == "UNRESOLVED",
}
#: Longest first, so a claim that is a substring of another is not mis-read.
_CLAIMS_LONGEST_FIRST = sorted(_CLAIM_CONTRACTS, key=len, reverse=True)


def contradicted_claims(record) -> list[str]:
    """Every claim in the reason that the record's own fields refute."""
    residue = record.binding_reason
    found = []
    for claim in _CLAIMS_LONGEST_FIRST:
        if claim in residue:
            found.append(claim)
            residue = residue.replace(claim, "")
    return [claim for claim in found if not _CLAIM_CONTRACTS[claim](record)]


@pytest.mark.parametrize("trigger", [
    _M13B_TRIGGER, _M13D_TRIGGER, _ANTECEDENT_ABSENT_TRIGGER,
])
def test_the_published_reason_makes_no_claim_the_record_contradicts(trigger):
    """The defect these three inputs realised: a reason from a stale premise."""
    record = V2.bind(**trigger)
    assert contradicted_claims(record) == [], (
        f"{record.binding_reason!r} vs subject_completeness="
        f"{record.subject_completeness} repairability={record.repairability} "
        f"predicate_completeness={record.predicate_completeness}")


@pytest.mark.parametrize("trigger,expected_reason", [
    (_M13B_TRIGGER,
     "the construction binds no subject; the predicate is bound in span"),
    (_M13D_TRIGGER,
     "the subject is named to context the caller supplies but its recovery "
     "is not proven; the predicate is bound in span"),
    (_ANTECEDENT_ABSENT_TRIGGER,
     "the subject is not resolved; the predicate is bound in span"),
])
def test_the_repaired_reason_texts_are_exactly_these(trigger, expected_reason):
    """Exact equality, so the repair cannot silently become vague again."""
    assert V2.bind(**trigger).binding_reason == expected_reason


def test_the_unmoved_population_keeps_the_original_reason_verbatim():
    """The repair is not a rewrite of every reason: M2's own case is untouched.

    A span whose subject really is recoverable from a proven bounded repair
    still publishes the original sentence, byte for byte.
    """
    record = V2.bind(
        candidate_id="p61-recoverable", language="ru",
        text="Рекомендует государствам-членам представить доклад Секретариату.",
        left_context="Комитет рассмотрел доклад.",
        content_region_type="PRIMARY_PROPOSITION_CONTENT",
        structural_context=ST.empty_context(
            document_id="d", region_id="r",
            reading_order_state="READING_ORDER_ESTABLISHED"))
    assert record.internal_state == "MULTIPLE_BINDINGS_RECOVERABLE"
    assert record.subject_state == "IMPLICIT_CONTEXT_BOUND_SUBJECT"
    assert record.subject_completeness == "RECOVERABLE_BOUNDED"
    assert record.repairability == "BOUNDED_REPAIR_PROVEN"
    assert record.binding_reason == (
        "the subject is recoverable from context the caller supplies; "
        "the predicate is bound in span")
    assert contradicted_claims(record) == []


def test_the_claim_contract_table_would_catch_a_stale_reason():
    """Control: the checker is not vacuous.

    A record whose reason is restored to M2's pre-rewrite wording, with every
    other field left exactly as the module emits it, must be reported as
    contradicted.  If this passes, the table below it proves nothing.
    """
    from dataclasses import replace
    record = V2.bind(**_M13B_TRIGGER)
    stale = replace(record, binding_reason=(
        "the subject is recoverable from context the caller supplies; "
        "the predicate is bound in span"))
    assert contradicted_claims(stale) == [
        "the subject is recoverable from context the caller supplies"]


def test_the_reason_is_phrased_after_the_terminal_axes_are_derived():
    """Structural pin: the repair is the ORDER, and the order must hold.

    Phrasing M2's finding at M2 time is exactly what published a sentence the
    record's later fields contradicted.  The sentence must be built after the
    role rewrites and after the axes are derived from them.
    """
    source = textwrap.dedent(inspect.getsource(V2.bind))
    tree = ast.parse(source)
    reason_calls = [
        node for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        and node.func.id == "_m2_binding_reason"
    ]
    facts_calls = [
        node for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        and node.func.id == "_terminal_facts"
    ]
    assert len(reason_calls) == 1 and len(facts_calls) == 1
    assert reason_calls[0].lineno > facts_calls[0].lineno
    # It reads the derived facts, not a pre-rewrite local.
    args = reason_calls[0].args
    assert [isinstance(a, ast.Name) for a in args] == [True, True]
    assert [a.id for a in args] == ["terminal_facts", "predicate_span"]
