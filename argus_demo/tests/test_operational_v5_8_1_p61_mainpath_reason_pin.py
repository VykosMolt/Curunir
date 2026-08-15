"""P6.1.1 — the MAIN-path `binding_reason`, pinned by EXACT EQUALITY.

P6.1 made the main-path (adapter-route) `binding_reason` a pure function of the
record's FINAL emitted axes: `roles_v2._m2_binding_reason(terminal_facts,
predicate_span)` is called at the emission site, after M7--M15 have finished
rewriting the role states.  R3 pinned the REFUSAL route's reason by exact
equality.  The MAIN path was left bounded only by four exact-equality POINT pins
plus a claim-contract table that ignores any wording it does not know.

The independent verification of P6.1 measured what that leaves open (finding
L1).  Two mutants of `roles_v2.py` passed all 27 test files:

* A8_2 appends a false out-of-table claim ("this record is safe to publish")
  to the main-path reason, gated on the emitted predicate span being longer
  than 200 characters -- a shape none of the four point pins exercised.  It
  moves the PUBLISHED `binding_reason` on three real corpus units.
* A8_7 interpolates caller BODY content into the main-path reason behind the
  same gate.  Its unconditional twin A8_8 is killed by the point pins; the
  gated form is not.

This module closes that channel.  For every record the main path emits, the
published sentence must be EXACTLY the sentence an INDEPENDENT reference
derivation -- the tables in this file, written as literals and never read out of
the module under test -- produces from the record's own final emitted axes.  A
coordinated edit of production and of the production helper therefore cannot
satisfy it: the expected text lives here.

Nothing existing is read for reuse, modified, relaxed or removed.  This file is
purely additive protection.  Three things make it non-vacuous rather than
self-agreeing:

* the reference is a literal table, so `_m2_binding_reason` is the SUBJECT of
  the comparison and never its oracle;
* the population is pinned by IDENTITY, not by count alone: a mutant that moves
  a record OFF the reference wording is caught by the coverage assertions even
  though it no longer trips the equality assertion;
* explicit vacuity controls perturb the reference, append an out-of-table claim
  and interpolate body content, and assert the pin FAILS.
"""

from __future__ import annotations

import ast
import functools
import hashlib
import importlib.util
import json
import pathlib
import re

import pytest

from curunir_operational.v5_8_1 import layout as LO
from curunir_operational.v5_8_1 import regions as RG
from curunir_operational.v5_8_1 import roles_v2 as V2
from curunir_operational.v5_8_1 import structure as ST
from curunir_operational.v5_8_1 import terminal as TM

pytestmark = pytest.mark.no_db


_THIS_FILE = pathlib.Path(__file__).resolve()
_TESTS_DIR = _THIS_FILE.parent
_REPO = _TESTS_DIR.parent
_EMISSION_PINS_FILE = _TESTS_DIR / "test_operational_v5_8_1_p61_emission_pins.py"

_ARTIFACTS = (_REPO / "artifacts"
              / "curunir_autonomous_completion_v5_8_1_20260725")
_POPULATION = _ARTIFACTS / "307_d42g_structural_region_truth" \
    / "exposed_population.jsonl"
_ACQUISITION = _ARTIFACTS / "08_acquisition_attempts"

#: The 120-unit frozen population and its immutable source bytes are custody
#: artifacts that a bare source checkout does not carry.  The gate is EXACTLY
#: their presence, and `test_only_the_corpus_arms_are_custody_gated` asserts
#: which tests it may cover, so it cannot quietly grow to hide a failure.
_CORPUS_AVAILABLE = _POPULATION.exists() and _ACQUISITION.exists()
needs_corpus = pytest.mark.skipif(
    not _CORPUS_AVAILABLE,
    reason="the frozen 120-unit exposed population is not present in this "
           "checkout")
_CUSTODY_GATED_TESTS = frozenset({
    "test_every_corpus_unit_publishes_the_reference_reason",
    "test_the_corpus_main_path_population_is_exactly_these_units",
    "test_the_corpus_carries_long_predicate_spans_on_the_main_path",
})


# ---------------------------------------------------------------------------
# THE INDEPENDENT REFERENCE DERIVATION
#
# Every string below is a literal written HERE.  Nothing in this section reads
# `roles_v2`, so an edit to the module under test cannot move both sides of the
# comparison at once.  The mapping is stated exhaustively -- one entry per
# admissible (axis, axis) pair -- rather than as branch logic, so a reader can
# check it against the D39 vocabulary by eye and a vocabulary that grows fails
# `test_the_reference_table_covers_the_frozen_axis_vocabularies` instead of
# silently falling through a default.
# ---------------------------------------------------------------------------

#: The clause separator the published sentence uses.  A drift here is a drift.
_REASON_SEPARATOR = "; "

#: subject claim, keyed by (subject_completeness, repairability).
#: "is recoverable" asserts recovery as ESTABLISHED, so the record may say it
#: only where the repair it names is proven.
_SUBJECT_CLAIM = {
    ("ABSENT_BY_CONSTRUCTION", "NO_REPAIR_OWED"):
        "the construction binds no subject",
    ("ABSENT_BY_CONSTRUCTION", "BOUNDED_REPAIR_PROVEN"):
        "the construction binds no subject",
    ("ABSENT_BY_CONSTRUCTION", "REPAIR_NAMED_NOT_PROVEN"):
        "the construction binds no subject",
    ("ABSENT_BY_CONSTRUCTION", "IRREPARABLE"):
        "the construction binds no subject",

    ("UNRESOLVED", "NO_REPAIR_OWED"):
        "the subject is not resolved",
    ("UNRESOLVED", "BOUNDED_REPAIR_PROVEN"):
        "the subject is not resolved",
    ("UNRESOLVED", "REPAIR_NAMED_NOT_PROVEN"):
        "the subject is not resolved",
    ("UNRESOLVED", "IRREPARABLE"):
        "the subject is not resolved",

    ("RECOVERABLE_BOUNDED", "BOUNDED_REPAIR_PROVEN"):
        "the subject is recoverable from context the caller supplies",
    ("RECOVERABLE_BOUNDED", "NO_REPAIR_OWED"):
        "the subject is named to context the caller supplies but its recovery "
        "is not proven",
    ("RECOVERABLE_BOUNDED", "REPAIR_NAMED_NOT_PROVEN"):
        "the subject is named to context the caller supplies but its recovery "
        "is not proven",
    ("RECOVERABLE_BOUNDED", "IRREPARABLE"):
        "the subject is named to context the caller supplies but its recovery "
        "is not proven",

    ("BOUND_LOCAL", "NO_REPAIR_OWED"): "the subject is bound",
    ("BOUND_LOCAL", "BOUNDED_REPAIR_PROVEN"): "the subject is bound",
    ("BOUND_LOCAL", "REPAIR_NAMED_NOT_PROVEN"): "the subject is bound",
    ("BOUND_LOCAL", "IRREPARABLE"): "the subject is bound",

    ("BOUND_CONTEXT", "NO_REPAIR_OWED"): "the subject is bound",
    ("BOUND_CONTEXT", "BOUNDED_REPAIR_PROVEN"): "the subject is bound",
    ("BOUND_CONTEXT", "REPAIR_NAMED_NOT_PROVEN"): "the subject is bound",
    ("BOUND_CONTEXT", "IRREPARABLE"): "the subject is bound",
}

#: predicate claim, keyed by (predicate_completeness, predicate_span is None).
#: A transported FRAME does not remove the local span, so the clause that
#: distinguishes "in span" from "to the declared context" reads the EMITTED
#: span and not the frame axis.
_PREDICATE_CLAIM = {
    ("ABSENT_BY_CONSTRUCTION", True): "the construction carries no predicate",
    ("ABSENT_BY_CONSTRUCTION", False): "the construction carries no predicate",

    ("UNRESOLVED", True): "the predicate is not resolved",
    ("UNRESOLVED", False): "the predicate is not resolved",

    ("BOUND_LOCAL", True): "the predicate is bound to the declared context",
    ("BOUND_LOCAL", False): "the predicate is bound in span",

    ("BOUND_CONTEXT", True): "the predicate is bound to the declared context",
    ("BOUND_CONTEXT", False): "the predicate is bound in span",

    ("RECOVERABLE_BOUNDED", True):
        "the predicate is bound to the declared context",
    ("RECOVERABLE_BOUNDED", False): "the predicate is bound in span",
}

#: Every distinct phrase either table can emit.  A published sentence that
#: contains ONE of these is a main-path sentence and is held to exact equality;
#: a sentence that contains NONE of them is a different emission site's and is
#: counted, not rewritten, by this module.
_CLAIM_PHRASES = frozenset(_SUBJECT_CLAIM.values()) | frozenset(
    _PREDICATE_CLAIM.values())

#: The complete language of the reference: every sentence it can produce.
_REFERENCE_LANGUAGE = frozenset(
    f"{subject}{_REASON_SEPARATOR}{predicate}"
    for subject in set(_SUBJECT_CLAIM.values())
    for predicate in set(_PREDICATE_CLAIM.values()))


def _reference_reason_from_axes(subject_completeness: str,
                                repairability: str,
                                predicate_completeness: str,
                                predicate_span) -> str:
    """The sentence the main path must publish, from the FINAL emitted axes."""
    subject_claim = _SUBJECT_CLAIM[(subject_completeness, repairability)]
    predicate_claim = _PREDICATE_CLAIM[
        (predicate_completeness, predicate_span is None)]
    return f"{subject_claim}{_REASON_SEPARATOR}{predicate_claim}"


def reference_reason(record) -> str:
    """The same derivation, read off one emitted record."""
    return _reference_reason_from_axes(
        record.subject_completeness, record.repairability,
        record.predicate_completeness, record.predicate_span)


def _describe(record) -> str:
    span = record.predicate_span
    return (f"subject_completeness={record.subject_completeness} "
            f"repairability={record.repairability} "
            f"predicate_completeness={record.predicate_completeness} "
            f"predicate_span={span!r} "
            f"span_len={None if span is None else span[1] - span[0]}")


def assert_main_path_reason(record, where: str = "") -> bool:
    """Hold a record's published reason to the reference, EXACTLY.

    Returns whether the record published a main-path sentence at all, so a
    caller can assert COVERAGE: a mutant that moves a record off the reference
    wording entirely stops being counted here, and the coverage assertions in
    this module fail even though this one no longer fires.
    """
    reason = record.binding_reason
    if not any(claim in reason for claim in _CLAIM_PHRASES):
        return False
    expected = reference_reason(record)
    assert reason == expected, (
        f"{where}: the main path published {reason!r}; the reference "
        f"derivation for this record's own final axes is {expected!r} "
        f"({_describe(record)})")
    return True


# ---------------------------------------------------------------------------
# Constructed inputs.  Every one is an INPUT; no expected output is recorded
# with it, because the expected output is derived by the reference above.
# ---------------------------------------------------------------------------

def _sound_context(order_state: str = "READING_ORDER_ESTABLISHED"):
    return ST.empty_context(document_id="d", region_id="r",
                            reading_order_state=order_state)


#: The three units P6.1 actually repaired, with the frozen population's own
#: inputs, so the classes the repair addressed are exercised even in a checkout
#: that carries no custody artifacts.  M13b rewrites the subject to the
#: impersonal construction; M13d to the inherited coordinate subject; the third
#: is an anaphor whose bounded left context holds no antecedent.
_REPAIRED_UNITS = {
    "v5-8-1-d25unit-210cd10e4585b144d9f798fd": dict(
        language="ar",
        content_region_type="PRIMARY_PROPOSITION_CONTENT",
        text="وينبغي علاج الأشخاص المعرّضين لخطر كبير أو الذين يعانون من "
             "أعراض وخيمة بالأدوية المضادة للفيروسات في أقرب وقت ممكن.",
        left_context="التماس الرعاية الطبية عند ظهور الأعراض.",
        heading="العلاج"),
    "v5-8-1-d25unit-c32bf3ded56bf5417c9dae4b": dict(
        language="ru",
        content_region_type="UNKNOWN_STRUCTURAL_REGION",
        text="но и позволяют добиться жизнестойкости в долгосрочной "
             "перспективе, что способствует",
        left_context="психосоциальной поддержке, которые не только "
                     "удовлетворяют насущные потребности,",
        heading=""),
    "v5-8-1-d25unit-143f1cf82e10354cd57d3341": dict(
        language="de",
        content_region_type="PRIMARY_PROPOSITION_CONTENT",
        text="sie hierzu aus rechtlichen Gründen nicht in der Lage ist;",
        left_context="1.",
        heading="§ 5 Voraussetzungen und Grenzen der Amtshilfe"),
}

#: The reason each repaired unit publishes, written out.  This duplicates what
#: the reference derives, deliberately: the derivation proves the sentence is
#: consistent with the record, and these literals prove WHICH sentence the
#: repair settled on.  Both must hold.
_REPAIRED_UNIT_REASONS = {
    "v5-8-1-d25unit-210cd10e4585b144d9f798fd":
        "the construction binds no subject; the predicate is bound in span",
    "v5-8-1-d25unit-c32bf3ded56bf5417c9dae4b":
        "the subject is named to context the caller supplies but its recovery "
        "is not proven; the predicate is bound in span",
    "v5-8-1-d25unit-143f1cf82e10354cd57d3341":
        "the subject is not resolved; the predicate is bound in span",
}

#: Inputs whose EMITTED predicate span is longer than 200 characters, on bodies
#: longer than 200 characters, on the main path.  This is precisely the shape
#: A8_2 and A8_7 hid behind: the four P6.1 point pins never reached it.  The
#: arm asserts the shape is genuinely reached, so the gate cannot stop being
#: tripped without the test saying so.
_LONG_SPAN_CASES = {
    "ar_impersonal_385": dict(
        candidate_id="p61-1-ar-long", language="ar",
        content_region_type="PRIMARY_PROPOSITION_CONTENT",
        text="وينبغي علاج الأشخاص المعرّضين لخطر كبير بالأدوية المضادة "
             "للفيروسات في أقرب وقت ممكن مع متابعة الحالة السريرية وتقديم "
             "المشورة والدعم النفسي والاجتماعي وتوفير الرعاية المستمرة على "
             "مدى فترة العلاج بأكملها وفقاً للإرشادات الوطنية المعتمدة "
             "وبالتنسيق مع السلطات الصحية المختصة وشركاء التنفيذ في جميع "
             "المناطق المتأثرة وعلى امتداد سنوات الخطة الاستراتيجية المتفق "
             "عليها بين الأطراف المعنية كافة.",
        left_context="التماس الرعاية الطبية عند ظهور الأعراض."),
    "ru_recoverable_310": dict(
        candidate_id="p61-1-ru-long", language="ru",
        content_region_type="PRIMARY_PROPOSITION_CONTENT",
        text="Рекомендует государствам-членам представить Секретариату "
             "доклад о мерах, принятых для укрепления национальных систем "
             "здравоохранения, включая профилактику, диагностику, лечение и "
             "последующее наблюдение, о выделенных ресурсах, достигнутых "
             "результатах, извлеченных уроках и остающихся пробелах за "
             "отчетный период, а также о планах дальнейшей работы на "
             "предстоящий двухгодичный период.",
        left_context="Комитет рассмотрел доклад.",
        structural_context=_sound_context()),
    "ru_named_not_proven_310": dict(
        candidate_id="p61-1-ru-long-unwired", language="ru",
        content_region_type="PRIMARY_PROPOSITION_CONTENT",
        text="Рекомендует государствам-членам представить Секретариату "
             "доклад о мерах, принятых для укрепления национальных систем "
             "здравоохранения, включая профилактику, диагностику, лечение и "
             "последующее наблюдение, о выделенных ресурсах, достигнутых "
             "результатах, извлеченных уроках и остающихся пробелах за "
             "отчетный период, а также о планах дальнейшей работы на "
             "предстоящий двухгодичный период.",
        left_context="Комитет рассмотрел доклад."),
    "de_unresolved_327": dict(
        candidate_id="p61-1-de-long", language="de",
        content_region_type="PRIMARY_PROPOSITION_CONTENT",
        text="sie hierzu aus rechtlichen Gründen nicht in der Lage ist und "
             "die zuständigen Behörden keine abweichende Anordnung getroffen "
             "haben, die eine andere Vorgehensweise für den betreffenden "
             "Zeitraum ausdrücklich zulassen würde, soweit die betroffenen "
             "Stellen rechtzeitig unterrichtet worden sind und keine "
             "gegenteilige Mitteilung vorliegt;",
        left_context="1."),
}

#: Shorter main-path inputs, so the pin is not measured only on the long shape
#: it was added for.  Together with the repaired units and the long cases these
#: reach every (subject claim, predicate claim) pair the binder produces.
_SHORT_CASES = {
    "ru_recoverable_proven": dict(
        candidate_id="p61-1-ru-short", language="ru",
        content_region_type="PRIMARY_PROPOSITION_CONTENT",
        text="Рекомендует государствам-членам представить доклад "
             "Секретариату.",
        left_context="Комитет рассмотрел доклад.",
        structural_context=_sound_context()),
    "ru_recoverable_unwired": dict(
        candidate_id="p61-1-ru-short-unwired", language="ru",
        content_region_type="PRIMARY_PROPOSITION_CONTENT",
        text="Рекомендует государствам-членам представить доклад "
             "Секретариату.",
        left_context="Комитет рассмотрел доклад."),
    "en_committee": dict(
        candidate_id="p61-1-en", language="en",
        content_region_type="PRIMARY_PROPOSITION_CONTENT",
        text="The Committee shall review the report of the Secretariat.",
        left_context="The Council considered the matter.",
        structural_context=_sound_context()),
    "de_committee": dict(
        candidate_id="p61-1-de", language="de",
        content_region_type="PRIMARY_PROPOSITION_CONTENT",
        text="Der Ausschuss prüft den Bericht des Sekretariats.",
        left_context="Der Rat hat die Angelegenheit geprüft.",
        structural_context=_sound_context()),
    "fr_committee": dict(
        candidate_id="p61-1-fr", language="fr",
        content_region_type="PRIMARY_PROPOSITION_CONTENT",
        text="Recommande aux États Membres de présenter un rapport au "
             "Secrétariat.",
        left_context="Le Comité a examiné le rapport.",
        structural_context=_sound_context()),
    "ar_deontic": dict(
        candidate_id="p61-1-ar", language="ar",
        content_region_type="PRIMARY_PROPOSITION_CONTENT",
        text="وينبغي علاج الأشخاص المعرّضين لخطر كبير بالأدوية المضادة "
             "للفيروسات في أقرب وقت ممكن.",
        left_context="التماس الرعاية الطبية عند ظهور الأعراض."),
    "furniture_region": dict(
        candidate_id="p61-1-furniture", language="en",
        content_region_type="NAVIGATION_MENU",
        text="Home | About | Contact",
        left_context=""),
    "digits_only": dict(
        candidate_id="p61-1-digits", language="en",
        content_region_type="PRIMARY_PROPOSITION_CONTENT",
        text="12345",
        left_context=""),
}

_CONSTRUCTED_CASES = dict(_SHORT_CASES)
_CONSTRUCTED_CASES.update(_LONG_SPAN_CASES)
for _uid, _kwargs in _REPAIRED_UNITS.items():
    _CONSTRUCTED_CASES[f"repaired::{_uid}"] = dict(_kwargs, candidate_id=_uid)


@functools.lru_cache(maxsize=None)
def _constructed_records() -> tuple:
    return tuple(
        (name, V2.bind(**_CONSTRUCTED_CASES[name]))
        for name in sorted(_CONSTRUCTED_CASES))


# ---------------------------------------------------------------------------
# The frozen 120-unit population, reached through production's own route.
# ---------------------------------------------------------------------------

_LEADING_ENUMERATOR = re.compile(r"^\s*\d+\s*\.?\s+")


def _match_key(text: str) -> str:
    return re.sub(r"\s+", "", _LEADING_ENUMERATOR.sub("", text))


@functools.lru_cache(maxsize=None)
def _content_store() -> dict:
    found = {}
    for path in _ACQUISITION.rglob("*"):
        if path.is_file() and len(path.name) == 64:
            try:
                int(path.name, 16)
            except ValueError:
                continue
            found.setdefault(path.name, path)
    return found


@functools.lru_cache(maxsize=None)
def _corpus_records() -> tuple:
    """Bind every unit of the frozen population through the production route.

    immutable source bytes -> layout.assess(...).ordered_text() (PDF only)
    -> regions.segment(...) -> structure.build_structural_context(...)
    -> roles_v2.bind(..., structural_context=...)

    A unit whose region cannot be reproduced is still bound, through the
    unwired call, so the denominator stays at 120.
    """
    units = sorted(
        (json.loads(line) for line in
         _POPULATION.read_text(encoding="utf-8").splitlines() if line.strip()),
        key=lambda row: row["unit_id"])
    store = _content_store()
    segmented: dict = {}

    def regions_for(unit):
        digest = unit["content_hash"]
        if digest not in segmented:
            path = store.get(digest)
            if path is None:
                segmented[digest] = ([], "CONTENT_ABSENT")
            elif unit["container"] == "PDF":
                assessment = LO.assess(
                    path.read_bytes(), document_id=unit["source_id"],
                    manifestation_id=unit["manifestation_id"])
                segmented[digest] = (
                    RG.segment(assessment.ordered_text(),
                               source_id=unit["source_id"],
                               source_family_id=unit["source_family_id"],
                               container=unit["container"]),
                    assessment.state)
            else:
                segmented[digest] = (
                    RG.segment(path.read_bytes(),
                               source_id=unit["source_id"],
                               source_family_id=unit["source_family_id"],
                               container=unit["container"]),
                    "READING_ORDER_NOT_APPLICABLE")
        return segmented[digest]

    records = []
    for unit in units:
        regions, order_state = regions_for(unit)
        context = None
        if regions:
            by_hash = {
                hashlib.sha256(region.text.encode("utf-8")).hexdigest(): region
                for region in regions}
            region = by_hash.get(unit["region_text_hash"])
            if region is None:
                key = _match_key(unit["span_text"])
                near = [r for r in regions if _match_key(r.text) == key]
                region = near[0] if len(near) == 1 else None
            if region is not None:
                context = ST.build_structural_context(
                    regions=regions, region_id=region.region_id,
                    document_id=unit["source_id"],
                    manifestation_id=unit["manifestation_id"],
                    span_offsets=(unit["span_start"], unit["span_end"]),
                    reading_order_state=order_state)
        kwargs = dict(
            candidate_id=unit["unit_id"], text=unit["span_text"],
            language=unit["language"],
            left_context=unit.get("left_context") or "",
            heading=unit.get("heading") or "",
            content_region_type=unit["content_region_type"])
        if context is not None:
            kwargs["structural_context"] = context
        records.append((unit["unit_id"], V2.bind(**kwargs)))
    return tuple(records)


#: The units of the frozen population whose reason the MAIN path phrases, by
#: IDENTITY.  Pinning the set and not merely its size is what catches a mutant
#: that moves a record off the reference wording: the record stops being a
#: member and the set no longer matches.
_CORPUS_MAIN_PATH_UNITS = frozenset({
    "v5-8-1-d25unit-0673b7a2d56f8cd17e993ca4",
    "v5-8-1-d25unit-073dca23dd00c6450c4ef0c2",
    "v5-8-1-d25unit-08284b41b1907f642d55c62b",
    "v5-8-1-d25unit-143f1cf82e10354cd57d3341",
    "v5-8-1-d25unit-14aa11a8fdde872f69bd9e12",
    "v5-8-1-d25unit-1af7806b5d95ab1d618bf5a7",
    "v5-8-1-d25unit-1ba6367d4d9ff164e4d06f1b",
    "v5-8-1-d25unit-1c1667411105f302780c72cb",
    "v5-8-1-d25unit-210cd10e4585b144d9f798fd",
    "v5-8-1-d25unit-47946e8c24631c2c0e4271e1",
    "v5-8-1-d25unit-5bcb7b92dc6b11f0fe8dede5",
    "v5-8-1-d25unit-637cf05fb04deb5f334447e9",
    "v5-8-1-d25unit-6e9d678522e4d20939c98869",
    "v5-8-1-d25unit-831727d1f04253e4313799c4",
    "v5-8-1-d25unit-94f6a75583d1b3ccddc4753a",
    "v5-8-1-d25unit-9d3e0dbb55d72c2620e8e1d9",
    "v5-8-1-d25unit-b908aef48da4f81a5e540d11",
    "v5-8-1-d25unit-c32bf3ded56bf5417c9dae4b",
    "v5-8-1-d25unit-c3b98c0b5a6c2607ef925fec",
    "v5-8-1-d25unit-ce7ae0f7bd7c2d539fb1f54a",
    "v5-8-1-d25unit-d0553f607c26acd84adcd6b3",
    "v5-8-1-d25unit-d8ddc1d8f6a631b7bea12d01",
})

#: The three units on which A8_2 moved the PUBLISHED reason: the whole of the
#: corpus evidence for finding L1.  Their emitted predicate span is longer than
#: 200 characters, which is what put them outside the four P6.1 point pins.
_CORPUS_LONG_SPAN_UNITS = frozenset({
    "v5-8-1-d25unit-0673b7a2d56f8cd17e993ca4",
    "v5-8-1-d25unit-94f6a75583d1b3ccddc4753a",
    "v5-8-1-d25unit-ce7ae0f7bd7c2d539fb1f54a",
})

_A8_GATE_SPAN_CHARS = 200


# ---------------------------------------------------------------------------
# The reference itself
# ---------------------------------------------------------------------------


def test_the_reference_table_covers_the_frozen_axis_vocabularies():
    """The reference is exhaustive over D39, so nothing falls through it.

    Production ends both of its clause selections in an `else`.  A reference
    written the same way would silently absorb a NEW axis value and keep
    passing.  These tables are keyed instead, and this test asserts the keys
    are exactly the frozen vocabulary, so a vocabulary that grows fails HERE,
    loudly, and is then decided rather than absorbed.
    """
    assert set(_SUBJECT_CLAIM) == {
        (completeness, repairability)
        for completeness in TM.ROLE_COMPLETENESS
        for repairability in TM.REPAIRABILITIES}
    assert len(_SUBJECT_CLAIM) == 20
    assert set(_PREDICATE_CLAIM) == {
        (completeness, span_absent)
        for completeness in TM.ROLE_COMPLETENESS
        for span_absent in (True, False)}
    assert len(_PREDICATE_CLAIM) == 10
    # Five distinct subject claims and four distinct predicate claims: twenty
    # sentences, which is the whole language of the main-path reason.
    assert len(set(_SUBJECT_CLAIM.values())) == 5
    assert len(set(_PREDICATE_CLAIM.values())) == 4
    assert len(_CLAIM_PHRASES) == 9
    assert len(_REFERENCE_LANGUAGE) == 20
    assert _REASON_SEPARATOR == "; "


def test_the_claim_phrases_agree_with_the_p61_emission_pins_table():
    """Divergence guard, not an oracle.

    The R4 claim-contract table in the sibling P6.1 file names the same nine
    phrases from the other direction (claim -> the predicate the record must
    satisfy).  Neither file derives its strings from the other or from the
    module under test, so agreement between two independent statements is
    evidence; a divergence is a change somebody has to argue.
    """
    spec = importlib.util.spec_from_file_location(
        "_p611_emission_pins_readonly", _EMISSION_PINS_FILE)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert set(module._CLAIM_CONTRACTS) == set(_CLAIM_PHRASES)


def test_the_reference_never_produces_a_sentence_outside_its_language():
    """Closure: every axis pair maps into the twenty declared sentences."""
    produced = {
        _reference_reason_from_axes(subject, repairability, predicate, span)
        for subject in TM.ROLE_COMPLETENESS
        for repairability in TM.REPAIRABILITIES
        for predicate in TM.ROLE_COMPLETENESS
        for span in (None, (0, 1))}
    assert produced == set(_REFERENCE_LANGUAGE)


# ---------------------------------------------------------------------------
# The helper's whole output space
# ---------------------------------------------------------------------------

#: Span shapes around and far beyond the 200-character gate A8_2 and A8_7 hid
#: behind, plus the absent span, so a length-conditional clause cannot survive.
_HELPER_SPAN_SHAPES = (
    None, (0, 0), (0, 1), (7, 199), (0, 200), (0, 201), (13, 313), (0, 4096))


def test_the_helper_output_space_matches_the_independent_reference():
    """Exhaustive: every D39 fact combination, every span shape.

    `_m2_binding_reason` is the SUBJECT here and never the oracle -- the
    expected string comes from the tables above.  The enumeration is the
    complete product of the frozen D39 vocabulary, so no reachable input is
    left for a conditional clause to hide in, whatever it keys on among the
    facts and the span.
    """
    facts_space = [
        TM.TerminalFacts(
            proposition_status=proposition,
            subject_completeness=subject,
            predicate_completeness=predicate,
            binding_uniqueness=uniqueness,
            required_context_status=context_status,
            repairability=repairability)
        for proposition in sorted(TM.PROPOSITION_STATUSES)
        for subject in sorted(TM.ROLE_COMPLETENESS)
        for predicate in sorted(TM.ROLE_COMPLETENESS)
        for uniqueness in sorted(TM.BINDING_UNIQUENESS)
        for context_status in sorted(TM.REQUIRED_CONTEXT_STATUSES)
        for repairability in sorted(TM.REPAIRABILITIES)]
    assert len(facts_space) == 4 * 5 * 5 * 3 * 5 * 4 == 6000

    seen = set()
    checked = 0
    for facts in facts_space:
        expected_by_span = {}
        for span in _HELPER_SPAN_SHAPES:
            emitted = V2._m2_binding_reason(facts, span)
            expected = _reference_reason_from_axes(
                facts.subject_completeness, facts.repairability,
                facts.predicate_completeness, span)
            assert emitted == expected, (
                f"_m2_binding_reason({facts!r}, {span!r}) emitted {emitted!r}; "
                f"the reference derivation says {expected!r}")
            expected_by_span[span] = emitted
            seen.add(emitted)
            checked += 1
        # Two spans that are both present may not be told apart: the emitted
        # sentence distinguishes only presence from absence.
        present = {value for span, value in expected_by_span.items()
                   if span is not None}
        assert len(present) == 1, (
            f"the span LENGTH changed the published sentence: {present}")
    assert checked == 6000 * len(_HELPER_SPAN_SHAPES) == 48000
    assert seen == set(_REFERENCE_LANGUAGE), sorted(
        set(_REFERENCE_LANGUAGE) ^ seen)


def test_the_helper_reads_only_the_axes_the_reference_reads():
    """The three D39 axes the sentence does not describe may not move it.

    proposition_status, binding_uniqueness and required_context_status are
    facts about the record, not about the roles the sentence reports.  A clause
    keyed on one of them would be a channel the reference cannot see; it is
    refused here explicitly rather than left implied.
    """
    for subject in sorted(TM.ROLE_COMPLETENESS):
        for predicate in sorted(TM.ROLE_COMPLETENESS):
            for repairability in sorted(TM.REPAIRABILITIES):
                for span in (None, (0, 260)):
                    emitted = {
                        V2._m2_binding_reason(
                            TM.TerminalFacts(
                                proposition_status=proposition,
                                subject_completeness=subject,
                                predicate_completeness=predicate,
                                binding_uniqueness=uniqueness,
                                required_context_status=context_status,
                                repairability=repairability),
                            span)
                        for proposition in sorted(TM.PROPOSITION_STATUSES)
                        for uniqueness in sorted(TM.BINDING_UNIQUENESS)
                        for context_status in sorted(
                            TM.REQUIRED_CONTEXT_STATUSES)}
                    assert emitted == {_reference_reason_from_axes(
                        subject, repairability, predicate, span)}


# ---------------------------------------------------------------------------
# What `bind` actually publishes
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name", sorted(_CONSTRUCTED_CASES))
def test_every_constructed_record_publishes_the_reference_reason(name):
    """Exact equality at the EMISSION site, not only inside the helper.

    A8_7 interpolated the caller's body into the reason AFTER the helper
    returned, which no amount of helper enumeration can see.  This arm reads
    the published record.
    """
    record = V2.bind(**_CONSTRUCTED_CASES[name])
    assert_main_path_reason(record, where=f"constructed case {name}")


def test_the_constructed_population_reaches_the_main_path():
    """Non-vacuity: the arms above are not all falling through the guard.

    If a change stops the main path from phrasing its reason at all, every
    equality assertion in this module becomes trivially satisfiable.  The
    floors below make that a failure.
    """
    reached = [
        (name, record) for name, record in _constructed_records()
        if any(claim in record.binding_reason for claim in _CLAIM_PHRASES)]
    assert len(reached) >= 10, [name for name, _ in reached]
    texts = {record.binding_reason for _, record in reached}
    assert texts <= set(_REFERENCE_LANGUAGE)
    assert len(texts) >= 4, sorted(texts)
    profiles = {
        (record.subject_completeness, record.repairability,
         record.predicate_completeness, record.predicate_span is None)
        for _, record in reached}
    assert len(profiles) >= 4, sorted(profiles)
    # And the guard is a guard: some constructed inputs deliberately do NOT
    # reach the main path, so a test that "everything is a main-path record"
    # would be measuring nothing.
    assert len(reached) < len(_constructed_records())


@pytest.mark.parametrize("unit_id", sorted(_REPAIRED_UNITS))
def test_the_repaired_units_publish_exactly_the_repaired_sentence(unit_id):
    """The three units P6.1 moved, held to their text by two independent
    statements: the literal above and the reference derivation."""
    record = V2.bind(**dict(_REPAIRED_UNITS[unit_id], candidate_id=unit_id))
    assert record.internal_state == "MULTIPLE_BINDINGS_RECOVERABLE"
    assert record.binding_reason == _REPAIRED_UNIT_REASONS[unit_id], _describe(
        record)
    assert record.binding_reason == reference_reason(record)


@pytest.mark.parametrize("name", sorted(_LONG_SPAN_CASES))
def test_a_long_predicate_span_cannot_change_what_is_published(name):
    """The exact shape finding L1 was demonstrated on.

    A8_2 and A8_7 emitted their payload only when the emitted predicate span
    exceeded 200 characters.  The arm asserts the shape is REACHED before it
    asserts the sentence, so the gate cannot stop being tripped without this
    test saying so.
    """
    record = V2.bind(**_LONG_SPAN_CASES[name])
    span = record.predicate_span
    assert span is not None, _describe(record)
    assert span[1] - span[0] > _A8_GATE_SPAN_CHARS, _describe(record)
    assert len(_LONG_SPAN_CASES[name]["text"]) > _A8_GATE_SPAN_CHARS
    assert assert_main_path_reason(record, where=f"long-span case {name}"), (
        f"{name} stopped publishing a main-path sentence: "
        f"{record.binding_reason!r}")


def test_no_caller_supplied_content_reaches_the_main_path_reason():
    """A covert channel is a reason that varies with the caller's input.

    The canary is carried in a body long enough to trip the A8_2/A8_7 gate and
    in the left context and heading as well, so an interpolation from any of
    the three caller-supplied strings is visible.
    """
    canary = "CANARY-4b81f7"
    body = ("Рекомендует государствам-членам представить Секретариату доклад "
            f"о мерах, принятых {canary} для укрепления национальных систем "
            "здравоохранения, включая профилактику, диагностику, лечение и "
            "последующее наблюдение, о выделенных ресурсах, достигнутых "
            "результатах, извлеченных уроках и остающихся пробелах за "
            "отчетный период, а также о планах дальнейшей работы.")
    record = V2.bind(
        candidate_id=f"p61-1-{canary}", language="ru", text=body,
        left_context=f"Комитет рассмотрел доклад {canary}.",
        heading=f"Раздел {canary}",
        content_region_type="PRIMARY_PROPOSITION_CONTENT",
        structural_context=_sound_context())
    span = record.predicate_span
    assert span is not None and span[1] - span[0] > _A8_GATE_SPAN_CHARS, \
        _describe(record)
    assert canary not in record.binding_reason
    assert record.candidate_id not in record.binding_reason
    assert assert_main_path_reason(record, where="canary case")
    assert record.binding_reason in _REFERENCE_LANGUAGE


@pytest.mark.parametrize("order_state", [
    "READING_ORDER_ESTABLISHED",
    "READING_ORDER_RECOVERABLE",
    "READING_ORDER_NOT_APPLICABLE",
])
def test_the_reading_order_state_does_not_reach_the_main_path_reason(
        order_state):
    """The three states that route `bind` through the adapter agree.

    The refusal route's reason IS a function of the reading-order state (R3
    pins it).  The main path's is not: it reports the roles, and the three
    sound states must publish the same sentence for the same span.
    """
    reached = set()
    for name, kwargs in sorted(_LONG_SPAN_CASES.items()):
        record = V2.bind(**dict(kwargs, structural_context=_sound_context(
            order_state)))
        if assert_main_path_reason(record, f"{name} under {order_state}"):
            reached.add(name)
        assert order_state not in record.binding_reason
    # Non-vacuity: every long case must still BE a main-path record here, or
    # the equality assertion above ran on nothing.
    assert reached == set(_LONG_SPAN_CASES), sorted(
        set(_LONG_SPAN_CASES) - reached)


# ---------------------------------------------------------------------------
# The frozen 120-unit population
# ---------------------------------------------------------------------------


@needs_corpus
def test_every_corpus_unit_publishes_the_reference_reason():
    """The pin, on the real population the campaign measures."""
    records = _corpus_records()
    assert len(records) == 120
    assert len({unit_id for unit_id, _ in records}) == 120
    for unit_id, record in records:
        assert_main_path_reason(record, where=f"corpus unit {unit_id}")


@needs_corpus
def test_the_corpus_main_path_population_is_exactly_these_units():
    """Identity, not count: a record that leaves the reference is caught.

    A mutant that replaces a main-path sentence with wording of its own stops
    tripping the equality assertion above -- and fails here instead, because
    the unit is no longer a member of the pinned set.
    """
    reached = {
        unit_id for unit_id, record in _corpus_records()
        if any(claim in record.binding_reason for claim in _CLAIM_PHRASES)}
    assert reached == set(_CORPUS_MAIN_PATH_UNITS), {
        "unexpected": sorted(reached - _CORPUS_MAIN_PATH_UNITS),
        "missing": sorted(_CORPUS_MAIN_PATH_UNITS - reached)}
    by_id = dict(_corpus_records())
    for unit_id in sorted(_REPAIRED_UNIT_REASONS):
        assert unit_id in reached
        assert by_id[unit_id].binding_reason == \
            _REPAIRED_UNIT_REASONS[unit_id], _describe(by_id[unit_id])
    # Every published main-path sentence is one of the twenty, and each is the
    # one this record's own axes call for.
    for unit_id in sorted(reached):
        assert by_id[unit_id].binding_reason in _REFERENCE_LANGUAGE
        assert by_id[unit_id].binding_reason == reference_reason(by_id[unit_id])


@needs_corpus
def test_the_corpus_carries_long_predicate_spans_on_the_main_path():
    """The three units A8_2 moved, and the fact that the shape is real.

    Finding L1's corpus evidence is exactly these units.  Pinning them by
    identity means the gate the mutant hid behind is covered on real data as
    well as on constructed input.
    """
    by_id = dict(_corpus_records())
    long_units = {
        unit_id for unit_id, record in _corpus_records()
        if unit_id in _CORPUS_MAIN_PATH_UNITS
        and record.predicate_span is not None
        and record.predicate_span[1] - record.predicate_span[0]
        > _A8_GATE_SPAN_CHARS}
    assert long_units == set(_CORPUS_LONG_SPAN_UNITS), {
        "unexpected": sorted(long_units - _CORPUS_LONG_SPAN_UNITS),
        "missing": sorted(_CORPUS_LONG_SPAN_UNITS - long_units)}
    for unit_id in sorted(long_units):
        record = by_id[unit_id]
        assert assert_main_path_reason(record, where=f"long corpus {unit_id}")
        assert record.binding_reason == reference_reason(record)


def test_only_the_corpus_arms_are_custody_gated():
    """The skip may cover the population arms and nothing else.

    A skip that spreads is a gate that quietly stops running.  The set of
    tests in THIS file wearing the custody marker is asserted, from the source,
    so widening it fails.
    """
    tree = ast.parse(_THIS_FILE.read_text(encoding="utf-8"))
    gated = {
        node.name
        for node in tree.body
        if isinstance(node, ast.FunctionDef)
        and any(isinstance(decorator, ast.Name)
                and decorator.id == "needs_corpus"
                for decorator in node.decorator_list)}
    assert gated == set(_CUSTODY_GATED_TESTS), {
        "unexpected": sorted(gated - _CUSTODY_GATED_TESTS),
        "missing": sorted(_CUSTODY_GATED_TESTS - gated)}
    assert _CORPUS_AVAILABLE == (
        _POPULATION.exists() and _ACQUISITION.exists())
    # No other skip, xfail or tolerance mechanism is used anywhere in the
    # file.  Read from the syntax tree, so the sentence describing the rule
    # cannot satisfy the rule.
    relaxations = [
        node.attr for node in ast.walk(tree)
        if isinstance(node, ast.Attribute)
        and node.attr in ("skip", "skipif", "xfail", "approx",
                          "importorskip")]
    assert relaxations == ["skipif"], relaxations


# ---------------------------------------------------------------------------
# VACUITY CONTROLS.  Each asserts the pin CAN fail.
# ---------------------------------------------------------------------------


def test_the_pin_fails_when_the_reference_itself_is_perturbed():
    """Control: the reference is load-bearing.

    If the table below were wrong -- or if a future edit moved it to agree with
    a drifted production -- the derivation would produce a different sentence
    and the comparison would fail.  Here it is perturbed deliberately, and the
    disagreement is asserted.
    """
    record = V2.bind(**dict(
        _REPAIRED_UNITS["v5-8-1-d25unit-210cd10e4585b144d9f798fd"],
        candidate_id="v5-8-1-d25unit-210cd10e4585b144d9f798fd"))
    assert record.binding_reason == reference_reason(record)

    axes = (record.subject_completeness, record.repairability)
    perturbed_subject = dict(_SUBJECT_CLAIM)
    perturbed_subject[axes] = (
        "the construction binds no subject and this record is safe to publish")
    predicate_claim = _PREDICATE_CLAIM[
        (record.predicate_completeness, record.predicate_span is None)]
    perturbed = (f"{perturbed_subject[axes]}{_REASON_SEPARATOR}"
                 f"{predicate_claim}")
    assert perturbed != record.binding_reason
    # The unperturbed table produces the published sentence; the perturbed one
    # does not.  The comparison is therefore load-bearing in both directions.
    assert (f"{_SUBJECT_CLAIM[axes]}{_REASON_SEPARATOR}{predicate_claim}"
            == record.binding_reason)


def test_the_pin_fails_on_an_appended_out_of_table_claim():
    """Control: the A8_1/A8_2 shape, rejected.

    The claim is invisible to a containment checker and to a claim-contract
    table that only knows its own keys.  Exact equality sees it.
    """
    from dataclasses import replace
    record = V2.bind(**_LONG_SPAN_CASES["ru_recoverable_310"])
    assert assert_main_path_reason(record, where="control base")
    tampered = replace(
        record,
        binding_reason=record.binding_reason + "; this record is safe to "
                                               "publish")
    with pytest.raises(AssertionError):
        assert_main_path_reason(tampered, where="appended false claim")


def test_the_pin_fails_on_interpolated_caller_body_content():
    """Control: the A8_7/A8_8 shape, rejected."""
    from dataclasses import replace
    record = V2.bind(**_LONG_SPAN_CASES["de_unresolved_327"])
    assert assert_main_path_reason(record, where="control base")
    body = _LONG_SPAN_CASES["de_unresolved_327"]["text"]
    tampered = replace(
        record, binding_reason=f"{record.binding_reason} [{body[:60]}]")
    with pytest.raises(AssertionError):
        assert_main_path_reason(tampered, where="interpolated body")


def test_the_pin_fails_on_a_swapped_but_in_language_sentence():
    """Control: a sentence from the right language but the wrong record.

    A drift does not have to leave the language to be false.  The reference is
    a function of THIS record's axes, so a sentence that is lawful for some
    other record is still a failure here.
    """
    from dataclasses import replace
    record = V2.bind(**_LONG_SPAN_CASES["ru_recoverable_310"])
    other = "the construction binds no subject; the predicate is not resolved"
    assert other in _REFERENCE_LANGUAGE
    assert other != record.binding_reason
    with pytest.raises(AssertionError):
        assert_main_path_reason(replace(record, binding_reason=other),
                                where="swapped sentence")


def test_the_coverage_assertions_fail_when_the_population_empties():
    """Control: the identity pin notices a record that leaves the reference.

    This is the leg that catches a mutant which REPLACES the sentence instead
    of appending to it -- such a record stops matching any claim phrase, so the
    equality assertion never fires and only the membership set can see it.
    """
    from dataclasses import replace
    records = _constructed_records()

    def main_path_members(rows):
        return {name for name, record in rows
                if any(claim in record.binding_reason
                       for claim in _CLAIM_PHRASES)}

    assert main_path_members(records), \
        "no constructed input reaches the main path at all"
    replaced = [(name, replace(record, binding_reason="binding reported"))
                for name, record in records]
    # Every member has left the population, so a membership assertion of the
    # shape used above -- and in the corpus arms -- fails on this input.
    assert main_path_members(replaced) == set()
