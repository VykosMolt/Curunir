"""P6.1.2 — the FULL published `binding_reason` surface, pinned by EXACT
EQUALITY over the whole exposed population and over two constructed axes.
REFERENCE REGENERATED FOR P6.2 (see "REGENERATED FOR P6.2" below).

WHAT THIS CLOSES
----------------
P6.1.1 pinned the sentence the M2 emission site phrases
(`roles_v2._m2_binding_reason`).  The independent verification of that
amendment then measured what remained (findings R1 and R2):

* R1.  Only 22 of the 120 frozen corpus units publish an M2-path reason.  The
  other 98 publish `resolve_internal`'s OWN sentences -- 33 distinct texts on
  the P6.1 substrate `ad21425a`, 37 on the P6.2 substrate, 54 on the P6.3
  substrate, in six families
  (governing enactment 22, ambiguity margin 40, furniture 17, signal codes 9,
  no predicate 8, decisive lead 2 -- the per-unit family counts are the same on
  all three substrates) -- and NO test pinned any of them.  Measured directly: an unconditional false claim appended to every
  non-M2 reason, and caller BODY content interpolated into every non-M2 reason,
  both passed the complete suite as it then stood (28 operational files, the
  P6.1.2 generation).
* R2.  A drift gated on `language == "it"` escaped, because the 22 M2-path
  units carry only {ru, de, fr, ar, en}: the corpus's 14 Italian and 12 Spanish
  units never reach the M2 path, so no arm of the suite ever bound a record
  with those declared languages through the pinned site.

The independent verification of THIS file (P6.1.2) then adjudicated its
stopping rule SOUND BUT NOT MAXIMAL and enumerated exactly one remaining
FINITE-closable residue, plus a correction:

* E1 (finding F2, closed by the P6.1.3 delta below).  Production's
  `regions.ORIGIN_CLASSES` has SEVENTEEN `content_region_type` values; the
  P6.1.2 surface visited nine.  Eight were unvisited, and a drift gated on
  `content_region_type == "FOOTER_FURNITURE"` was measured to pass the whole
  suite as it then stood (29 operational files, the P6.1.2 generation) in both
  arena shapes, while the same gate on `NAVIGATION_MENU` -- a value the
  surface did visit -- failed 13 to 16 nodes.  That is not a predicate outside
  every enumerated axis; it is an unvisited VALUE of an axis already known to
  matter, on a small finite vocabulary.  The language axis was made exhaustive
  over its vocabulary; this one had not been.
* F1 (a claim in this file was too strong; corrected at its site below, in the
  INDEPENDENT STATEMENTS section).

This module pins the REACHABLE surface instead of one emission site:

1. every one of the 120 corpus units, by exact equality against an in-file
   literal reference, and the population PARTITION by identity, so a unit that
   silently migrates between families or stops publishing fails;
2. a constructed LANGUAGE axis -- 14 bodies x {ar, de, en, es, fr, it, ru,
   None} x 4 structural-context shapes = 448 pinned points -- so a drift gated
   on ANY of those declared values is caught, on the M2 path and on every
   non-M2 family alike;
3. a constructed SPAN-LENGTH axis -- 55 pinned points whose EMITTED predicate
   span is exactly 31, 50, 64, 100, 150, 199, 200, 201, 250, 300, 350, 400,
   450, 500, 516, 517, 518, 550, 600, 650 or 700 characters, on an M2 ladder
   and on a non-M2 (governing-enactment) ladder -- so a length-keyed clause,
   including the verifier's `== 517` example, is caught at the probed points;
4. a constructed REGION-TYPE axis -- ALL SEVENTEEN of production's own
   `regions.ORIGIN_CLASSES` values x 6 bodies x the same 4 context shapes = 408
   pinned points -- so a drift gated on ANY content_region_type is caught.  The
   swept set is asserted EQUAL to `regions.ORIGIN_CLASSES`, so a vocabulary
   that GROWS fails loudly instead of quietly acquiring an unpinned value,
   which is how E1 arose in the first place.

HOW THE REFERENCE WAS OBTAINED, AND WHY IT IS NOT SELF-SATISFYING
-----------------------------------------------------------------
Every expected string below was read off the CURRENT verified substrate
exactly once and then FROZEN here as literal data.

REGENERATED FOR P6.2.  The tables were first generated from the P6.1 substrate
(roles_v2.py sha256 ad21425a8adff7e2a19fdd858690244e03237c48e11ad2de645779735b
2a00c7) by `artifacts/curunir_v6_readiness/p6_1_3_amendment/evidence/probes/`
`gen_frozen_reference_v3.py --python`.  P6.2's R4 repairs three false or
overbroad sentence FAMILIES in production, so the sentences those families
publish moved and the tables were regenerated from the P6.2 substrate
(roles_v2.py sha256 ac68abb629abcfee33c78cfbd568c73f35794cd81abb60bc79e42933e7
5d3127) by `artifacts/curunir_v6_readiness/p6_2_implementation/evidence/`
`probes/gen_frozen_reference_v4.py`, whose ONLY difference from its predecessor
is that its family classifier recognises the three repaired wordings, so a
sentence whose TEXT changed keeps its FAMILY and the population partition stays
comparable across the repair.  MEASURED, and recorded in
`p6_2_implementation/evidence/FULLPOP_REGENERATION.json`: run against the P6.1
substrate the P6.2 generator reproduces the P6.1.3 tables and the digest
`507b1cd2…` byte-for-byte, so the regeneration below carries the substrate
change and nothing else.  Seventeen of the 120 corpus pins moved; every one of
them moved from a sentence a second author adjudicated FALSE, OVERBROAD or an
UNDERCOUNT on production's own facts to one adjudicated TRUE.

REGENERATED AGAIN FOR P6.3.  B1 repairs the AMBIGUITY/MARGIN family, which four
independent seats found defective on this very corpus in two distinct ways: the
published count was taken over the comparison pool while the same record
published `analyses_considered` for the full analysis list (14 of the 40 units
in the family diverge, by as much as 7 against 200), and the sentence that
justifies every ADMITTED record -- "one analysis leads the field by more than
the declared margin" -- was vacuous on both EVIDENCE_BOUND units, whose
comparison pool holds exactly ONE analysis, and misleading on
`d25unit-185e002e`, where three of the 168 legal analyses strictly outscore the
selected reading and the selection was made by D42J relation precedence rather
than by any scalar margin.  Two seats disagreed about whether the sentence was
false at all, and both were right: it never stated its scope.

The tables were regenerated from the P6.3 substrate (roles_v2.py sha256
67bba9d251eb42b9709068e36e3e309488ef32069183d276f902dff37c919d87) by
`artifacts/curunir_v6_readiness/p6_3_implementation/evidence/probes/`
`gen_frozen_reference_v5.py`, whose ONLY difference from the P6.2 generator is
that its family classifier recognises the two repaired wordings -- exactly the
precedent P6.2 itself set.  MEASURED and recorded in
`p6_3_implementation/evidence/FULLPOP_REGENERATION_B.json`: run against the
P6.2/P6.3-A substrate `bab4a84d`, the P6.3 generator reproduces all EIGHT
shipped tables byte-for-byte, so this regeneration too carries the substrate
change and nothing else.  Forty-two of the 120 corpus pins moved -- the 40
ambiguity-margin units and the 2 decisive-lead units -- and ONLY
`binding_reason` moved on them: the field-level dual-substrate diff over all
120 units and every published field records 0 movement of disposition,
internal_state, role_binding_state, proposition_status, the role states, the
selection and the scores, and the EVIDENCE_BOUND set is byte-identical.
The test file never runs that generator and never reads `roles_v2` to obtain an
expected value: production is the SUBJECT of every comparison and never its
oracle.  A coordinated edit of production and of its helper therefore cannot
satisfy these pins, because the expected text lives in this file.  What such an
edit CAN do if the tables are regenerated with it is measured and stated
precisely in the INDEPENDENT STATEMENTS section below; read that before relying
on this paragraph.

`_SENTENCES` maps a short alias to the exact sentence; every other table refers
to sentences by alias, so one sentence has one statement of its text and a
reader can check the 77 sentences by eye instead of the 1031 pins.

That count grew from 49 at P6.2 because P6.3's B1 repair makes each sentence
state the SCOPE it quantifies over, and the scope is a property of the unit:
"7 of the 7 analyses compared ... left of 200 considered" is a different text
from "4 of the 4 ... of 116".  The growth is the cost of the repair, not a
regression -- a family whose members differ in what they claim needs a
distinct text per claim -- and the family partition is unchanged, which is
checked below.

STOPPING RULE
-------------
This file completes the pin of the FINITELY ENUMERABLE surface: the whole
frozen exposed population; the declared-language axis, exhaustive over the
vocabulary the population carries plus the undeclared caller; the
content_region_type axis, exhaustive over production's own vocabulary; and a
dense emitted-span-length ladder on both an M2 and a non-M2 emission family.
It does NOT claim exhaustiveness over the binder's input space.

The independent verification enumerated what remains, with matched controls,
and certified the following classes UNBOUNDED -- not closable by enumeration at
any cost, and therefore deliberately NOT chased here:

* E2, emitted span length off the 88 values now covered (`== 523` escapes);
* E3, lexical predicates over the caller's `text`, `heading`, `left_context`
  or `candidate_id` (a token no pinned body contains escapes);
* E4, declared languages outside the frozen population's vocabulary (`pt`
  escapes; the axis IS exhaustive over what the population carries);
* E5, structural-context fields beyond the four constructed shapes, and the
  unswept `clause_evidence` and `governed_list_item` parameters.

That residue is carried, deliberately and explicitly, as a declared B-02-class
limitation: the runtime guard enforces the record's INTERNAL CONSISTENCY, not
the unconditionality of what it publishes, so unconditionality is carried by
this test matrix and is bounded by the matrix's reach.  Closing E2--E5 would
require either a search regime over the input space or a production change that
makes the reason a total function of declared axes; both are outside this
amendment's authority.  Do not read a green run of this file as "no covert
channel exists"; read it as "no covert channel exists at any point this matrix
reaches, and the matrix is now exhaustive over every finite vocabulary the
binder's published reason is known to depend on".

Nothing existing is read for reuse, modified, relaxed or removed.  Two
divergence guards compare this file's independent statements with the sibling
P6.1 files' independent statements; neither file derives its strings from the
other or from the module under test, so agreement is evidence and a divergence
is a change somebody has to argue.
"""

from __future__ import annotations

import ast
import dataclasses
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

pytestmark = pytest.mark.no_db


_THIS_FILE = pathlib.Path(__file__).resolve()
_TESTS_DIR = _THIS_FILE.parent
_REPO = _TESTS_DIR.parent
_EMISSION_PINS_FILE = (_TESTS_DIR
                       / "test_operational_v5_8_1_p61_emission_pins.py")
_MAINPATH_PIN_FILE = (_TESTS_DIR
                      / "test_operational_v5_8_1_p61_mainpath_reason_pin.py")

_ARTIFACTS = (_REPO / "artifacts"
              / "curunir_autonomous_completion_v5_8_1_20260725")
_POPULATION = _ARTIFACTS / "307_d42g_structural_region_truth" \
    / "exposed_population.jsonl"
_ACQUISITION = _ARTIFACTS / "08_acquisition_attempts"

#: The 120-unit frozen population and its immutable source bytes are custody
#: artifacts a bare source checkout does not carry.  The gate is EXACTLY their
#: presence, and `test_only_the_corpus_arms_are_custody_gated` asserts which
#: tests it may cover, so it cannot quietly grow to hide a failure.  The two
#: CONSTRUCTED axes -- which carry the language and span-length kills -- never
#: skip.
_CORPUS_AVAILABLE = _POPULATION.exists() and _ACQUISITION.exists()
needs_corpus = pytest.mark.skipif(
    not _CORPUS_AVAILABLE,
    reason="the frozen 120-unit exposed population is not present in this "
           "checkout")
_CUSTODY_GATED_TESTS = frozenset({
    "test_every_corpus_unit_publishes_exactly_the_frozen_reason",
    "test_the_corpus_population_partition_is_exactly_as_frozen",
    "test_the_corpus_non_m2_sentence_inventory_is_exactly_as_frozen",
    "test_every_corpus_unit_publishes_the_frozen_language_or_script",
    "test_the_frozen_corpus_language_column_matches_the_population_file",
})


# ===========================================================================
# THE FROZEN REFERENCE.  Literal data, generated once from the verified
# substrate and never derived from the module under test at run time.
# ===========================================================================

#: alias -> the EXACT published sentence.  One statement of each text.
_SENTENCES = {
    "AMB_0AD9D3":
        "15 of the 16 analyses compared score less than the declared 0.12 "
        "margin below the selected reading; the source does not decide; no "
        "typed governing relation reaches this span",
    "AMB_15A4F3":
        "5 of the 21 analyses compared score less than the declared 0.12 "
        "margin below the selected reading; the source does not decide; no "
        "typed governing relation reaches this span",
    "AMB_1E41A8":
        "5 of the 6 analyses compared score less than the declared 0.12 "
        "margin below the selected reading; the source does not decide; no "
        "typed governing relation reaches this span",
    "AMB_285680":
        "4 of the 5 analyses compared score less than the declared 0.12 "
        "margin below the selected reading; the source does not decide; one "
        "distinct governor parent through declared structural lineage",
    "AMB_324EB2":
        "7 of the 7 analyses compared score less than the declared 0.12 "
        "margin below the selected reading, and those 7 are what the "
        "structural precedence filters and a decisive "
        "LOCAL_SUBJECT_UNIQUE_CLOSED_FINITE_ANCHOR relation left of 60 "
        "considered; the source does not decide; no typed governing relation "
        "reaches this span",
    "AMB_362DAE":
        "8 of the 8 analyses compared score less than the declared 0.12 "
        "margin below the selected reading, and those 8 are what the "
        "structural precedence filters left of 139 considered; the source "
        "does not decide; no typed governing relation reaches this span",
    "AMB_389B7C":
        "5 of the 7 analyses compared score less than the declared 0.12 "
        "margin below the selected reading; the source does not decide; no "
        "typed governing relation reaches this span",
    "AMB_3B8AC6":
        "4 of the 4 analyses compared score less than the declared 0.12 "
        "margin below the selected reading, and those 4 are what the "
        "structural precedence filters and a decisive "
        "LOCAL_SUBJECT_REQUIRED_TO_DEONTIC_FRAME relation left of 94 "
        "considered; the source does not decide; no typed governing relation "
        "reaches this span",
    "AMB_3BF133":
        "16 of the 16 analyses compared score less than the declared 0.12 "
        "margin below the selected reading, and those 16 are what the "
        "structural precedence filters left of 142 considered; the source "
        "does not decide; no typed governing relation reaches this span",
    "AMB_4455E9":
        "11 of the 25 analyses compared score less than the declared 0.12 "
        "margin below the selected reading; the source does not decide; one "
        "distinct governor parent through declared structural lineage",
    "AMB_55EB15":
        "3 of the 6 analyses compared score less than the declared 0.12 "
        "margin below the selected reading; the source does not decide; no "
        "typed governing relation reaches this span",
    "AMB_62151B":
        "4 of the 4 analyses compared score less than the declared 0.12 "
        "margin below the selected reading, and those 4 are what the "
        "structural precedence filters and a decisive "
        "POST_RELATIVE_MODAL_RESUMES_SUBJECT relation left of 116 considered; "
        "the source does not decide; no typed governing relation reaches this "
        "span",
    "AMB_69CF7A":
        "6 of the 6 analyses compared score less than the declared 0.12 "
        "margin below the selected reading, and those 6 are what a decisive "
        "LOCAL_SUBJECT_UNIQUE_CLOSED_FINITE_ANCHOR relation left of 15 "
        "considered; the source does not decide; no typed governing relation "
        "reaches this span",
    "AMB_6DD014":
        "7 of the 7 analyses compared score less than the declared 0.12 "
        "margin below the selected reading, and those 7 are what the "
        "structural precedence filters and a decisive "
        "LOCAL_SUBJECT_UNIQUE_CLOSED_FINITE_ANCHOR relation left of 68 "
        "considered; the source does not decide; no typed governing relation "
        "reaches this span",
    "AMB_741754":
        "2 of the 13 analyses compared score less than the declared 0.12 "
        "margin below the selected reading; the source does not decide; no "
        "typed governing relation reaches this span",
    "AMB_75D2F7":
        "4 of the 4 analyses compared score less than the declared 0.12 "
        "margin below the selected reading, and those 4 are what the "
        "structural precedence filters and a decisive "
        "LOCAL_SUBJECT_UNIQUE_CLOSED_FINITE_ANCHOR relation left of 16 "
        "considered; the source does not decide",
    "AMB_7BBA1F":
        "6 of the 7 analyses compared score less than the declared 0.12 "
        "margin below the selected reading; the source does not decide; no "
        "typed governing relation reaches this span",
    "AMB_85C652":
        "4 of the 4 analyses compared score less than the declared 0.12 "
        "margin below the selected reading, and those 4 are what the "
        "structural precedence filters and a decisive "
        "LOCAL_SUBJECT_UNIQUE_CLOSED_FINITE_ANCHOR relation left of 16 "
        "considered; the source does not decide; one distinct governor parent "
        "through declared structural lineage",
    "AMB_8A0748":
        "3 of the 52 analyses compared score less than the declared 0.12 "
        "margin below the selected reading, and those 52 are what the "
        "structural precedence filters left of 179 considered; the source "
        "does not decide; one distinct governor parent through declared "
        "structural lineage",
    "AMB_8E22D2":
        "5 of the 5 analyses compared score less than the declared 0.12 "
        "margin below the selected reading, and those 5 are what the "
        "structural precedence filters and a decisive "
        "LOCAL_SUBJECT_UNIQUE_CLOSED_FINITE_ANCHOR relation left of 38 "
        "considered; the source does not decide; no typed governing relation "
        "reaches this span",
    "AMB_8E7E48":
        "3 of the 4 analyses compared score less than the declared 0.12 "
        "margin below the selected reading, and those 4 are what the "
        "hard-constraint filter left of 11 considered; the source does not "
        "decide; one distinct governor parent through declared structural "
        "lineage",
    "AMB_92F3D6":
        "3 of the 25 analyses compared score less than the declared 0.12 "
        "margin below the selected reading, and those 25 are what the "
        "structural precedence filters left of 97 considered; the source does "
        "not decide; no typed governing relation reaches this span",
    "AMB_999313":
        "48 of the 49 analyses compared score less than the declared 0.12 "
        "margin below the selected reading, and those 49 are what the "
        "structural precedence filters left of 67 considered; the source does "
        "not decide; no typed governing relation reaches this span",
    "AMB_9B37BB":
        "3 of the 8 analyses compared score less than the declared 0.12 "
        "margin below the selected reading; the source does not decide; no "
        "typed governing relation reaches this span",
    "AMB_A123A7":
        "7 of the 7 analyses compared score less than the declared 0.12 "
        "margin below the selected reading, and those 7 are what the "
        "hard-constraint filter and a decisive "
        "LOCAL_SUBJECT_UNIQUE_CLOSED_FINITE_ANCHOR relation left of 200 "
        "considered; the source does not decide; no typed governing relation "
        "reaches this span",
    "AMB_A1C635":
        "6 of the 31 analyses compared score less than the declared 0.12 "
        "margin below the selected reading; the source does not decide; one "
        "distinct governor parent through declared structural lineage",
    "AMB_A34A9B":
        "4 of the 4 analyses compared score less than the declared 0.12 "
        "margin below the selected reading, and those 4 are what the "
        "structural precedence filters and a decisive "
        "LOCAL_SUBJECT_UNIQUE_CLOSED_FINITE_ANCHOR relation left of 16 "
        "considered; the source does not decide; no typed governing relation "
        "reaches this span",
    "AMB_B64823":
        "9 of the 10 analyses compared score less than the declared 0.12 "
        "margin below the selected reading; the source does not decide; no "
        "typed governing relation reaches this span",
    "AMB_BA88B8":
        "5 of the 7 analyses compared score less than the declared 0.12 "
        "margin below the selected reading; the source does not decide",
    "AMB_BE1A8B":
        "2 of the 3 analyses compared score less than the declared 0.12 "
        "margin below the selected reading, and those 3 are what the "
        "hard-constraint filter left of 7 considered; the source does not "
        "decide; one distinct governor parent through declared structural "
        "lineage",
    "AMB_C52E6A":
        "5 of the 7 analyses compared score less than the declared 0.12 "
        "margin below the selected reading; the source does not decide; 2 "
        "distinct typed governor parents",
    "AMB_CB3C1A":
        "5 of the 7 analyses compared score less than the declared 0.12 "
        "margin below the selected reading; the source does not decide; one "
        "distinct governor parent through declared structural lineage",
    "AMB_CC5459":
        "7 of the 8 analyses compared score less than the declared 0.12 "
        "margin below the selected reading, and those 8 are what the "
        "hard-constraint filter left of 15 considered; the source does not "
        "decide; one distinct governor parent through declared structural "
        "lineage",
    "AMB_CF1D30":
        "8 of the 10 analyses compared score less than the declared 0.12 "
        "margin below the selected reading, and those 10 are what the "
        "structural precedence filters left of 25 considered; the source does "
        "not decide; no typed governing relation reaches this span",
    "AMB_D261BD":
        "3 of the 6 analyses compared score less than the declared 0.12 "
        "margin below the selected reading, and those 6 are what a decisive "
        "LOCAL_SUBJECT_UNIQUE_CLOSED_FINITE_ANCHOR relation left of 34 "
        "considered; the source does not decide; no typed governing relation "
        "reaches this span",
    "AMB_D28AD4":
        "2 of the 6 analyses compared score less than the declared 0.12 "
        "margin below the selected reading; the source does not decide; one "
        "distinct governor parent through declared structural lineage",
    "AMB_D77116":
        "6 of the 6 analyses compared score less than the declared 0.12 "
        "margin below the selected reading, and those 6 are what a decisive "
        "LOCAL_SUBJECT_UNIQUE_CLOSED_FINITE_ANCHOR relation left of 53 "
        "considered; the source does not decide; no typed governing relation "
        "reaches this span",
    "AMB_E0B3B8":
        "4 of the 4 analyses compared score less than the declared 0.12 "
        "margin below the selected reading, and those 4 are what the "
        "structural precedence filters and a decisive "
        "LOCAL_SUBJECT_UNIQUE_CLOSED_FINITE_ANCHOR relation left of 16 "
        "considered; the source does not decide; 2 distinct typed governor "
        "parents",
    "AMB_E40EE9":
        "several governing clauses are materially plausible; the structure "
        "does not decide between them; 2 distinct typed governor parents",
    "AMB_E41825":
        "5 of the 11 analyses compared score less than the declared 0.12 "
        "margin below the selected reading, and those 11 are what the "
        "structural precedence filters left of 21 considered; the source does "
        "not decide; one distinct governor parent through declared structural "
        "lineage",
    "AMB_E42E89":
        "3 of the 4 analyses compared score less than the declared 0.12 "
        "margin below the selected reading, and those 4 are what the "
        "structural precedence filters left of 10 considered; the source does "
        "not decide; no typed governing relation reaches this span",
    "AMB_E4C12F":
        "2 of the 3 analyses compared score less than the declared 0.12 "
        "margin below the selected reading, and those 3 are what the "
        "hard-constraint filter left of 5 considered; the source does not "
        "decide; one distinct governor parent through declared structural "
        "lineage",
    "AMB_E4E2DA":
        "4 of the 13 analyses compared score less than the declared 0.12 "
        "margin below the selected reading; the source does not decide; one "
        "distinct governor parent through declared structural lineage",
    "AMB_FB8269":
        "4 of the 9 analyses compared score less than the declared 0.12 "
        "margin below the selected reading; the source does not decide; no "
        "typed governing relation reaches this span",
    "AMB_FCE6AC":
        "12 of the 13 analyses compared score less than the declared 0.12 "
        "margin below the selected reading; the source does not decide; no "
        "typed governing relation reaches this span",
    "DEC_07E6D1":
        "the selected reading is the only analysis compared, which is what a "
        "decisive LOCAL_OVERT_PRONOMINAL_SUBJECT_OF relation left of 7 "
        "considered; no margin separated it from anything, because no other "
        "reading remained to compare it with; 2 distinct typed governor "
        "parents",
    "DEC_4FF013":
        "the selected reading is the only analysis compared, which is what a "
        "decisive LOCAL_OVERT_PRONOMINAL_SUBJECT_OF relation left of 7 "
        "considered; no margin separated it from anything, because no other "
        "reading remained to compare it with",
    "DEC_9EBD21":
        "the selected reading is the only analysis compared, which is what a "
        "decisive LOCAL_OVERT_PRONOMINAL_SUBJECT_OF relation left of 7 "
        "considered; no margin separated it from anything, because no other "
        "reading remained to compare it with; no typed governing relation "
        "reaches this span",
    "DEC_E52EE6":
        "the selected reading is the only analysis compared, which is what "
        "the structural precedence filters and a decisive "
        "LOCAL_SUBJECT_GERMAN_LEXICAL_FINITE relation left of 168 considered; "
        "no margin separated it from anything, because no other reading "
        "remained to compare it with; no typed governing relation reaches "
        "this span",
    "DEC_F222DD":
        "the selected reading is the only analysis compared, which is what a "
        "decisive LOCAL_OVERT_PRONOMINAL_SUBJECT_OF relation left of 7 "
        "considered; no margin separated it from anything, because no other "
        "reading remained to compare it with; one distinct governor parent "
        "through declared structural lineage",
    "FURN_2C03C6":
        "manifestation region NAVIGATION_MENU is typed document furniture, "
        "not a proposition container",
    "FURN_31DCB3":
        "manifestation region FOOTER_FURNITURE is typed document furniture, "
        "not a proposition container",
    "FURN_588A31":
        "manifestation region DOCUMENT_INDEX_ENTRY names a work or gives "
        "context rather than asserting about the world; it is not a "
        "proposition container",
    "FURN_9823D1":
        "manifestation region GENERIC_LINK_LABEL is typed document furniture, "
        "not a proposition container",
    "FURN_9B7B75":
        "manifestation region HEADING_CONTEXT_ONLY names a work or gives "
        "context rather than asserting about the world; it is not a "
        "proposition container",
    "FURN_C17B43":
        "manifestation region HEADER_FURNITURE is typed document furniture, "
        "not a proposition container",
    "GOVU_080546":
        "the predicate is bound and the subject is the governing enactment "
        "clause, which no typed relation reaches; no typed governing relation "
        "reaches this span",
    "GOVU_D93160":
        "the predicate is bound and the subject is the governing enactment "
        "clause, which the caller did not supply",
    "GOV_385D52":
        "a single typed governing enactment reaches this span and supplies "
        "the subject; one distinct governor parent through declared "
        "structural lineage",
    "M2_2503F1":
        "the subject is recoverable from context the caller supplies; the "
        "predicate is bound in span",
    "M2_2F8FB4": "the subject is not resolved; the predicate is bound in span",
    "M2_7A6AEC":
        "the subject is named to context the caller supplies but its recovery "
        "is not proven; the predicate is bound in span",
    "M2_D89E65":
        "the construction binds no subject; the predicate is bound in span",
    "NOPRED_019FED":
        "the best-supported analysis carries neither a predicate nor a "
        "governing clause; 2 distinct typed governor parents",
    "NOPRED_2FF212":
        "no analysis in the comparison pool carries a predicate or a "
        "governing clause",
    "NOPRED_6AF56F":
        "the best-supported analysis carries neither a predicate nor a "
        "governing clause; one distinct governor parent through declared "
        "structural lineage",
    "NOPRED_8A7A98":
        "no analysis in the comparison pool carries a predicate or a "
        "governing clause; no typed governing relation reaches this span",
    "NOPRED_8DDD04":
        "no analysis in the comparison pool carries a predicate or a "
        "governing clause; 2 distinct typed governor parents",
    "NOPRED_9778EE":
        "the best-supported analysis carries neither a predicate nor a "
        "governing clause",
    "NOPRED_9E5AF1":
        "the best-supported analysis carries neither a predicate nor a "
        "governing clause; no typed governing relation reaches this span",
    "NOPRED_B8A288":
        "no analysis in the comparison pool carries a predicate or a "
        "governing clause; one distinct governor parent through declared "
        "structural lineage",
    "NOPRED_D39C8C":
        "no analysis carries a predicate or a governing clause; no typed "
        "governing relation reaches this span",
    "NOPRED_ED6A96":
        "no analysis carries a predicate or a governing clause; one distinct "
        "governor parent through declared structural lineage",
    "SIG_84848B": "NO_VERBAL_OR_PREDICATIVE_EVIDENCE",
    "SIG_8684DA":
        "NO_VERBAL_OR_PREDICATIVE_EVIDENCE; OPENS_MID_CLAUSE_AND_UNTERMINATED",
    "SIG_E655F6":
        "SHORT_UNTERMINATED_FRAGMENT; NO_VERBAL_OR_PREDICATIVE_EVIDENCE; "
        "OPENS_MID_CLAUSE_AND_UNTERMINATED",
    "SIG_EF2767": "DATE_OR_SESSION_LINE; NO_VERBAL_OR_PREDICATIVE_EVIDENCE",
}

#: alias -> the emission family the sentence belongs to.
_FAMILY_OF_SENTENCE = {
    "AMB_0AD9D3": "ambiguity_margin",
    "AMB_15A4F3": "ambiguity_margin",
    "AMB_1E41A8": "ambiguity_margin",
    "AMB_285680": "ambiguity_margin",
    "AMB_324EB2": "ambiguity_margin",
    "AMB_362DAE": "ambiguity_margin",
    "AMB_389B7C": "ambiguity_margin",
    "AMB_3B8AC6": "ambiguity_margin",
    "AMB_3BF133": "ambiguity_margin",
    "AMB_4455E9": "ambiguity_margin",
    "AMB_55EB15": "ambiguity_margin",
    "AMB_62151B": "ambiguity_margin",
    "AMB_69CF7A": "ambiguity_margin",
    "AMB_6DD014": "ambiguity_margin",
    "AMB_741754": "ambiguity_margin",
    "AMB_75D2F7": "ambiguity_margin",
    "AMB_7BBA1F": "ambiguity_margin",
    "AMB_85C652": "ambiguity_margin",
    "AMB_8A0748": "ambiguity_margin",
    "AMB_8E22D2": "ambiguity_margin",
    "AMB_8E7E48": "ambiguity_margin",
    "AMB_92F3D6": "ambiguity_margin",
    "AMB_999313": "ambiguity_margin",
    "AMB_9B37BB": "ambiguity_margin",
    "AMB_A123A7": "ambiguity_margin",
    "AMB_A1C635": "ambiguity_margin",
    "AMB_A34A9B": "ambiguity_margin",
    "AMB_B64823": "ambiguity_margin",
    "AMB_BA88B8": "ambiguity_margin",
    "AMB_BE1A8B": "ambiguity_margin",
    "AMB_C52E6A": "ambiguity_margin",
    "AMB_CB3C1A": "ambiguity_margin",
    "AMB_CC5459": "ambiguity_margin",
    "AMB_CF1D30": "ambiguity_margin",
    "AMB_D261BD": "ambiguity_margin",
    "AMB_D28AD4": "ambiguity_margin",
    "AMB_D77116": "ambiguity_margin",
    "AMB_E0B3B8": "ambiguity_margin",
    "AMB_E40EE9": "ambiguity_margin",
    "AMB_E41825": "ambiguity_margin",
    "AMB_E42E89": "ambiguity_margin",
    "AMB_E4C12F": "ambiguity_margin",
    "AMB_E4E2DA": "ambiguity_margin",
    "AMB_FB8269": "ambiguity_margin",
    "AMB_FCE6AC": "ambiguity_margin",
    "DEC_07E6D1": "decisive_lead",
    "DEC_4FF013": "decisive_lead",
    "DEC_9EBD21": "decisive_lead",
    "DEC_E52EE6": "decisive_lead",
    "DEC_F222DD": "decisive_lead",
    "FURN_2C03C6": "furniture",
    "FURN_31DCB3": "furniture",
    "FURN_588A31": "furniture",
    "FURN_9823D1": "furniture",
    "FURN_9B7B75": "furniture",
    "FURN_C17B43": "furniture",
    "GOVU_080546": "governing_enactment_unrecovered",
    "GOVU_D93160": "governing_enactment_unrecovered",
    "GOV_385D52": "governing_enactment",
    "M2_2503F1": "M2_MAIN_PATH",
    "M2_2F8FB4": "M2_MAIN_PATH",
    "M2_7A6AEC": "M2_MAIN_PATH",
    "M2_D89E65": "M2_MAIN_PATH",
    "NOPRED_019FED": "no_predicate",
    "NOPRED_2FF212": "no_predicate",
    "NOPRED_6AF56F": "no_predicate",
    "NOPRED_8A7A98": "no_predicate",
    "NOPRED_8DDD04": "no_predicate",
    "NOPRED_9778EE": "no_predicate",
    "NOPRED_9E5AF1": "no_predicate",
    "NOPRED_B8A288": "no_predicate",
    "NOPRED_D39C8C": "no_predicate",
    "NOPRED_ED6A96": "no_predicate",
    "SIG_84848B": "signal_codes",
    "SIG_8684DA": "signal_codes",
    "SIG_E655F6": "signal_codes",
    "SIG_EF2767": "signal_codes",
}

#: The 120-unit frozen exposed population: unit_id -> the sentence it
#: publishes, by alias.  This is R1's whole surface.
_CORPUS_REASON = {
    "v5-8-1-d25unit-0118ce35eab316223c36c7bf": "FURN_588A31",
    "v5-8-1-d25unit-012b8e4265ba5e9c26663e56": "FURN_C17B43",
    "v5-8-1-d25unit-01493f97c52e043624527933": "SIG_EF2767",
    "v5-8-1-d25unit-036eb840d82913ee1c262099": "FURN_C17B43",
    "v5-8-1-d25unit-0445457cf1508c3d2364418e": "GOV_385D52",
    "v5-8-1-d25unit-05c8de82684ebb21f46eb3d8": "NOPRED_ED6A96",
    "v5-8-1-d25unit-0673b7a2d56f8cd17e993ca4": "M2_2503F1",
    "v5-8-1-d25unit-073dca23dd00c6450c4ef0c2": "M2_2503F1",
    "v5-8-1-d25unit-08284b41b1907f642d55c62b": "M2_2503F1",
    "v5-8-1-d25unit-093fefc9115d629f968b6058": "AMB_A123A7",
    "v5-8-1-d25unit-098315960705ccd2b4646093": "GOV_385D52",
    "v5-8-1-d25unit-0a832c840f7e1308236ddb5d": "GOV_385D52",
    "v5-8-1-d25unit-0b44ed5a1a462c814668b1b6": "AMB_8E7E48",
    "v5-8-1-d25unit-0bff16e4b496e6a56a26e7f0": "NOPRED_9E5AF1",
    "v5-8-1-d25unit-0c83297a38203b27d1162aea": "GOV_385D52",
    "v5-8-1-d25unit-117ca9598439ac14b0d920b8": "AMB_324EB2",
    "v5-8-1-d25unit-11f09b0c8bbd1cf55706543a": "GOV_385D52",
    "v5-8-1-d25unit-13a4905dbbd83539a62a72a7": "FURN_588A31",
    "v5-8-1-d25unit-143f1cf82e10354cd57d3341": "M2_2F8FB4",
    "v5-8-1-d25unit-14aa11a8fdde872f69bd9e12": "M2_2503F1",
    "v5-8-1-d25unit-170efc785b8a8440948a8ca6": "AMB_92F3D6",
    "v5-8-1-d25unit-17c2312a28e3635e5d6812e7": "NOPRED_ED6A96",
    "v5-8-1-d25unit-185e002e61906d4224b85eaa": "DEC_E52EE6",
    "v5-8-1-d25unit-18c1433c1e7f7128c8d5756a": "GOV_385D52",
    "v5-8-1-d25unit-1a6252ee09afdb1d694651fe": "GOV_385D52",
    "v5-8-1-d25unit-1abfa89e41adbb403222d5e8": "GOV_385D52",
    "v5-8-1-d25unit-1af0db29d5de873be01b2155": "SIG_84848B",
    "v5-8-1-d25unit-1af7806b5d95ab1d618bf5a7": "M2_2503F1",
    "v5-8-1-d25unit-1b74b111b75eaf63aaad6b11": "AMB_55EB15",
    "v5-8-1-d25unit-1b75af49feeec8e7a2a542a0": "NOPRED_8A7A98",
    "v5-8-1-d25unit-1ba6367d4d9ff164e4d06f1b": "M2_2503F1",
    "v5-8-1-d25unit-1bb59d54106f608625c96d49": "AMB_8E22D2",
    "v5-8-1-d25unit-1c1667411105f302780c72cb": "M2_2503F1",
    "v5-8-1-d25unit-207fbb175cd90b42fc07bff9": "AMB_3BF133",
    "v5-8-1-d25unit-210cd10e4585b144d9f798fd": "M2_D89E65",
    "v5-8-1-d25unit-213e87d8b6698b901d20f9fd": "GOV_385D52",
    "v5-8-1-d25unit-21a83e86c2d459ed231a4853": "SIG_E655F6",
    "v5-8-1-d25unit-23e9614acb6dd19de9eeceeb": "AMB_B64823",
    "v5-8-1-d25unit-2911ce381af436ecd1550508": "AMB_15A4F3",
    "v5-8-1-d25unit-2a28ea00fb5dd331f9d2df40": "FURN_588A31",
    "v5-8-1-d25unit-2f05647bbe687206cbdbb9f0": "FURN_9823D1",
    "v5-8-1-d25unit-313e41fd038d3269ccde0548": "GOV_385D52",
    "v5-8-1-d25unit-3263a54d7f9dfa195d4e9600": "AMB_B64823",
    "v5-8-1-d25unit-32f1306a54cf06032ad8e922": "AMB_3B8AC6",
    "v5-8-1-d25unit-34340252a07ad1cb269ee682": "AMB_A1C635",
    "v5-8-1-d25unit-343f4bdef37cba7e07db6e7c": "SIG_8684DA",
    "v5-8-1-d25unit-3b871449547472e5b5b465db": "FURN_588A31",
    "v5-8-1-d25unit-44ad3a7c00b51f92085e2056": "AMB_362DAE",
    "v5-8-1-d25unit-4503591ec35e94f398370e3c": "NOPRED_D39C8C",
    "v5-8-1-d25unit-46add309bcdf828d687dce94": "SIG_84848B",
    "v5-8-1-d25unit-47946e8c24631c2c0e4271e1": "M2_2503F1",
    "v5-8-1-d25unit-4812297d342b97b252bc8456": "AMB_D28AD4",
    "v5-8-1-d25unit-4958a389f021ca25fe5ef053": "AMB_8A0748",
    "v5-8-1-d25unit-4d479fb19fa1fbd804ab9223": "GOV_385D52",
    "v5-8-1-d25unit-5373acd7265e48b0f46708ce": "FURN_9823D1",
    "v5-8-1-d25unit-53cd23ab44142155454a78d4": "NOPRED_6AF56F",
    "v5-8-1-d25unit-5407c2ac0504f0d5658f4c81": "AMB_E4E2DA",
    "v5-8-1-d25unit-5527080a23121e074c0ebc7e": "AMB_1E41A8",
    "v5-8-1-d25unit-565191140ef4b2719a721c63": "FURN_588A31",
    "v5-8-1-d25unit-572c9de6b21582a24e23a19c": "GOV_385D52",
    "v5-8-1-d25unit-592ce0b83649bfb0413f7b80": "SIG_84848B",
    "v5-8-1-d25unit-5a19a0d70bae17c87a4c01f6": "GOV_385D52",
    "v5-8-1-d25unit-5b68a928a318fd4660426ddb": "AMB_E42E89",
    "v5-8-1-d25unit-5bcb7b92dc6b11f0fe8dede5": "M2_2503F1",
    "v5-8-1-d25unit-5c48c8f0dc9f5f7559b97c80": "GOV_385D52",
    "v5-8-1-d25unit-637cf05fb04deb5f334447e9": "M2_2503F1",
    "v5-8-1-d25unit-674c655384d18b650fe1450a": "FURN_588A31",
    "v5-8-1-d25unit-6767876d8a870809722d9b19": "AMB_E4E2DA",
    "v5-8-1-d25unit-6d7d241a07e600edbd1de137": "GOV_385D52",
    "v5-8-1-d25unit-6e9d678522e4d20939c98869": "M2_2503F1",
    "v5-8-1-d25unit-70792c0c520eb70d1b199bc9": "DEC_F222DD",
    "v5-8-1-d25unit-71a1313af410432fb674054e": "GOV_385D52",
    "v5-8-1-d25unit-73803e5e18b4e352f3e66789": "AMB_0AD9D3",
    "v5-8-1-d25unit-73aec05069c433ae8195713d": "AMB_FB8269",
    "v5-8-1-d25unit-73bb918b89c0fda2867f09c4": "FURN_9B7B75",
    "v5-8-1-d25unit-79dd18df193670630d02a3a2": "AMB_CF1D30",
    "v5-8-1-d25unit-7b009d94384153569d480860": "GOV_385D52",
    "v5-8-1-d25unit-7dd58c6b2f53316cf83c6f87": "SIG_8684DA",
    "v5-8-1-d25unit-801ff4eb9f3539cc3fc2270d": "FURN_2C03C6",
    "v5-8-1-d25unit-831727d1f04253e4313799c4": "M2_2503F1",
    "v5-8-1-d25unit-85251efed316be60714e7078": "AMB_D261BD",
    "v5-8-1-d25unit-857cf58299de6485fa0fb839": "AMB_69CF7A",
    "v5-8-1-d25unit-872296703cf0ecc67e190efe": "FURN_588A31",
    "v5-8-1-d25unit-92aa15ca94f12e99696720ee": "AMB_B64823",
    "v5-8-1-d25unit-94f6a75583d1b3ccddc4753a": "M2_2503F1",
    "v5-8-1-d25unit-976004781c076fe3eac68d22": "AMB_285680",
    "v5-8-1-d25unit-98797772919946a670134415": "AMB_7BBA1F",
    "v5-8-1-d25unit-9d260f345924f2d99c55f020": "AMB_999313",
    "v5-8-1-d25unit-9d3e0dbb55d72c2620e8e1d9": "M2_2503F1",
    "v5-8-1-d25unit-9ff2d8812f93ed2f2ed0b411": "AMB_E41825",
    "v5-8-1-d25unit-a509bb92c9258e7977b0156f": "AMB_9B37BB",
    "v5-8-1-d25unit-a7493fd9b11f49f6b06b06fb": "FURN_588A31",
    "v5-8-1-d25unit-a7688dde80ed2301bf6d9c31": "AMB_741754",
    "v5-8-1-d25unit-aa7ed4660a19944f49036f0c": "GOV_385D52",
    "v5-8-1-d25unit-ac7c4d54ba88e5bf96673664": "GOV_385D52",
    "v5-8-1-d25unit-aed4c60b826c9bd2e74b7f44": "AMB_6DD014",
    "v5-8-1-d25unit-b75753ff330224624fe408b6": "FURN_9823D1",
    "v5-8-1-d25unit-b908aef48da4f81a5e540d11": "M2_2503F1",
    "v5-8-1-d25unit-b9dccfb3bc16bef11a6cd422": "FURN_9B7B75",
    "v5-8-1-d25unit-ba21f946a1355cb8bf5988c7": "AMB_FCE6AC",
    "v5-8-1-d25unit-ba9e9b224dc53b8fa1c5827d": "SIG_84848B",
    "v5-8-1-d25unit-c32bf3ded56bf5417c9dae4b": "M2_7A6AEC",
    "v5-8-1-d25unit-c3b98c0b5a6c2607ef925fec": "M2_2503F1",
    "v5-8-1-d25unit-ce7ae0f7bd7c2d539fb1f54a": "M2_2503F1",
    "v5-8-1-d25unit-d0553f607c26acd84adcd6b3": "M2_2503F1",
    "v5-8-1-d25unit-d0dbdda7c5d06f1b92faf440": "FURN_588A31",
    "v5-8-1-d25unit-d23d07009c814aa260d7be8f": "SIG_8684DA",
    "v5-8-1-d25unit-d8ddc1d8f6a631b7bea12d01": "M2_2503F1",
    "v5-8-1-d25unit-dc864e76884bb000b7c7789d": "AMB_B64823",
    "v5-8-1-d25unit-e122614df9ea672664ebf109": "AMB_CC5459",
    "v5-8-1-d25unit-e7bce3e5477f31c924363b5e": "GOV_385D52",
    "v5-8-1-d25unit-e835e419af1220df051ca379": "NOPRED_B8A288",
    "v5-8-1-d25unit-ec2604cde64ab695634d8840": "GOV_385D52",
    "v5-8-1-d25unit-ec5c505871a0a50e436db3f0": "NOPRED_6AF56F",
    "v5-8-1-d25unit-ed061856ee4e9d6feb5d9a0d": "GOV_385D52",
    "v5-8-1-d25unit-f14c5f9a1d4d13458f54d604": "AMB_4455E9",
    "v5-8-1-d25unit-f264654e1417975f288a6d5c": "AMB_D77116",
    "v5-8-1-d25unit-fd97441930c9a3b435f10e1c": "AMB_62151B",
    "v5-8-1-d25unit-fe1d9b8da9c99f9a91447688": "AMB_E4C12F",
    "v5-8-1-d25unit-fee83f2a722df9ada7f0f678": "AMB_BE1A8B",
}

#: The population's own declared language column, restated here so the
#: script pin does not have to read the custody artifact to know it.
_CORPUS_LANGUAGE = {
    "v5-8-1-d25unit-0118ce35eab316223c36c7bf": "ru",
    "v5-8-1-d25unit-012b8e4265ba5e9c26663e56": "de",
    "v5-8-1-d25unit-01493f97c52e043624527933": "ru",
    "v5-8-1-d25unit-036eb840d82913ee1c262099": "de",
    "v5-8-1-d25unit-0445457cf1508c3d2364418e": "es",
    "v5-8-1-d25unit-05c8de82684ebb21f46eb3d8": "en",
    "v5-8-1-d25unit-0673b7a2d56f8cd17e993ca4": "de",
    "v5-8-1-d25unit-073dca23dd00c6450c4ef0c2": "de",
    "v5-8-1-d25unit-08284b41b1907f642d55c62b": "ru",
    "v5-8-1-d25unit-093fefc9115d629f968b6058": "es",
    "v5-8-1-d25unit-098315960705ccd2b4646093": "ru",
    "v5-8-1-d25unit-0a832c840f7e1308236ddb5d": "ru",
    "v5-8-1-d25unit-0b44ed5a1a462c814668b1b6": "en",
    "v5-8-1-d25unit-0bff16e4b496e6a56a26e7f0": "es",
    "v5-8-1-d25unit-0c83297a38203b27d1162aea": "es",
    "v5-8-1-d25unit-117ca9598439ac14b0d920b8": "it",
    "v5-8-1-d25unit-11f09b0c8bbd1cf55706543a": "ru",
    "v5-8-1-d25unit-13a4905dbbd83539a62a72a7": "en",
    "v5-8-1-d25unit-143f1cf82e10354cd57d3341": "de",
    "v5-8-1-d25unit-14aa11a8fdde872f69bd9e12": "de",
    "v5-8-1-d25unit-170efc785b8a8440948a8ca6": "de",
    "v5-8-1-d25unit-17c2312a28e3635e5d6812e7": "fr",
    "v5-8-1-d25unit-185e002e61906d4224b85eaa": "de",
    "v5-8-1-d25unit-18c1433c1e7f7128c8d5756a": "it",
    "v5-8-1-d25unit-1a6252ee09afdb1d694651fe": "ru",
    "v5-8-1-d25unit-1abfa89e41adbb403222d5e8": "ru",
    "v5-8-1-d25unit-1af0db29d5de873be01b2155": "ru",
    "v5-8-1-d25unit-1af7806b5d95ab1d618bf5a7": "de",
    "v5-8-1-d25unit-1b74b111b75eaf63aaad6b11": "ru",
    "v5-8-1-d25unit-1b75af49feeec8e7a2a542a0": "it",
    "v5-8-1-d25unit-1ba6367d4d9ff164e4d06f1b": "ru",
    "v5-8-1-d25unit-1bb59d54106f608625c96d49": "it",
    "v5-8-1-d25unit-1c1667411105f302780c72cb": "fr",
    "v5-8-1-d25unit-207fbb175cd90b42fc07bff9": "de",
    "v5-8-1-d25unit-210cd10e4585b144d9f798fd": "ar",
    "v5-8-1-d25unit-213e87d8b6698b901d20f9fd": "ru",
    "v5-8-1-d25unit-21a83e86c2d459ed231a4853": "ru",
    "v5-8-1-d25unit-23e9614acb6dd19de9eeceeb": "ar",
    "v5-8-1-d25unit-2911ce381af436ecd1550508": "en",
    "v5-8-1-d25unit-2a28ea00fb5dd331f9d2df40": "it",
    "v5-8-1-d25unit-2f05647bbe687206cbdbb9f0": "es",
    "v5-8-1-d25unit-313e41fd038d3269ccde0548": "ar",
    "v5-8-1-d25unit-3263a54d7f9dfa195d4e9600": "ar",
    "v5-8-1-d25unit-32f1306a54cf06032ad8e922": "en",
    "v5-8-1-d25unit-34340252a07ad1cb269ee682": "es",
    "v5-8-1-d25unit-343f4bdef37cba7e07db6e7c": "ru",
    "v5-8-1-d25unit-3b871449547472e5b5b465db": "it",
    "v5-8-1-d25unit-44ad3a7c00b51f92085e2056": "de",
    "v5-8-1-d25unit-4503591ec35e94f398370e3c": "ru",
    "v5-8-1-d25unit-46add309bcdf828d687dce94": "ru",
    "v5-8-1-d25unit-47946e8c24631c2c0e4271e1": "de",
    "v5-8-1-d25unit-4812297d342b97b252bc8456": "ru",
    "v5-8-1-d25unit-4958a389f021ca25fe5ef053": "de",
    "v5-8-1-d25unit-4d479fb19fa1fbd804ab9223": "it",
    "v5-8-1-d25unit-5373acd7265e48b0f46708ce": "fr",
    "v5-8-1-d25unit-53cd23ab44142155454a78d4": "es",
    "v5-8-1-d25unit-5407c2ac0504f0d5658f4c81": "es",
    "v5-8-1-d25unit-5527080a23121e074c0ebc7e": "ru",
    "v5-8-1-d25unit-565191140ef4b2719a721c63": "it",
    "v5-8-1-d25unit-572c9de6b21582a24e23a19c": "it",
    "v5-8-1-d25unit-592ce0b83649bfb0413f7b80": "ru",
    "v5-8-1-d25unit-5a19a0d70bae17c87a4c01f6": "ru",
    "v5-8-1-d25unit-5b68a928a318fd4660426ddb": "ar",
    "v5-8-1-d25unit-5bcb7b92dc6b11f0fe8dede5": "en",
    "v5-8-1-d25unit-5c48c8f0dc9f5f7559b97c80": "ru",
    "v5-8-1-d25unit-637cf05fb04deb5f334447e9": "ru",
    "v5-8-1-d25unit-674c655384d18b650fe1450a": "it",
    "v5-8-1-d25unit-6767876d8a870809722d9b19": "es",
    "v5-8-1-d25unit-6d7d241a07e600edbd1de137": "ru",
    "v5-8-1-d25unit-6e9d678522e4d20939c98869": "ru",
    "v5-8-1-d25unit-70792c0c520eb70d1b199bc9": "ru",
    "v5-8-1-d25unit-71a1313af410432fb674054e": "ru",
    "v5-8-1-d25unit-73803e5e18b4e352f3e66789": "ar",
    "v5-8-1-d25unit-73aec05069c433ae8195713d": "fr",
    "v5-8-1-d25unit-73bb918b89c0fda2867f09c4": "it",
    "v5-8-1-d25unit-79dd18df193670630d02a3a2": "ar",
    "v5-8-1-d25unit-7b009d94384153569d480860": "ru",
    "v5-8-1-d25unit-7dd58c6b2f53316cf83c6f87": "ru",
    "v5-8-1-d25unit-801ff4eb9f3539cc3fc2270d": "fr",
    "v5-8-1-d25unit-831727d1f04253e4313799c4": "ar",
    "v5-8-1-d25unit-85251efed316be60714e7078": "fr",
    "v5-8-1-d25unit-857cf58299de6485fa0fb839": "ru",
    "v5-8-1-d25unit-872296703cf0ecc67e190efe": "it",
    "v5-8-1-d25unit-92aa15ca94f12e99696720ee": "ar",
    "v5-8-1-d25unit-94f6a75583d1b3ccddc4753a": "fr",
    "v5-8-1-d25unit-976004781c076fe3eac68d22": "ru",
    "v5-8-1-d25unit-98797772919946a670134415": "ar",
    "v5-8-1-d25unit-9d260f345924f2d99c55f020": "ar",
    "v5-8-1-d25unit-9d3e0dbb55d72c2620e8e1d9": "ru",
    "v5-8-1-d25unit-9ff2d8812f93ed2f2ed0b411": "fr",
    "v5-8-1-d25unit-a509bb92c9258e7977b0156f": "ru",
    "v5-8-1-d25unit-a7493fd9b11f49f6b06b06fb": "it",
    "v5-8-1-d25unit-a7688dde80ed2301bf6d9c31": "ru",
    "v5-8-1-d25unit-aa7ed4660a19944f49036f0c": "ru",
    "v5-8-1-d25unit-ac7c4d54ba88e5bf96673664": "ru",
    "v5-8-1-d25unit-aed4c60b826c9bd2e74b7f44": "es",
    "v5-8-1-d25unit-b75753ff330224624fe408b6": "es",
    "v5-8-1-d25unit-b908aef48da4f81a5e540d11": "ru",
    "v5-8-1-d25unit-b9dccfb3bc16bef11a6cd422": "es",
    "v5-8-1-d25unit-ba21f946a1355cb8bf5988c7": "ar",
    "v5-8-1-d25unit-ba9e9b224dc53b8fa1c5827d": "ru",
    "v5-8-1-d25unit-c32bf3ded56bf5417c9dae4b": "ru",
    "v5-8-1-d25unit-c3b98c0b5a6c2607ef925fec": "ru",
    "v5-8-1-d25unit-ce7ae0f7bd7c2d539fb1f54a": "ru",
    "v5-8-1-d25unit-d0553f607c26acd84adcd6b3": "ru",
    "v5-8-1-d25unit-d0dbdda7c5d06f1b92faf440": "ru",
    "v5-8-1-d25unit-d23d07009c814aa260d7be8f": "ru",
    "v5-8-1-d25unit-d8ddc1d8f6a631b7bea12d01": "ru",
    "v5-8-1-d25unit-dc864e76884bb000b7c7789d": "ar",
    "v5-8-1-d25unit-e122614df9ea672664ebf109": "ar",
    "v5-8-1-d25unit-e7bce3e5477f31c924363b5e": "ru",
    "v5-8-1-d25unit-e835e419af1220df051ca379": "ar",
    "v5-8-1-d25unit-ec2604cde64ab695634d8840": "ru",
    "v5-8-1-d25unit-ec5c505871a0a50e436db3f0": "ru",
    "v5-8-1-d25unit-ed061856ee4e9d6feb5d9a0d": "ru",
    "v5-8-1-d25unit-f14c5f9a1d4d13458f54d604": "fr",
    "v5-8-1-d25unit-f264654e1417975f288a6d5c": "it",
    "v5-8-1-d25unit-fd97441930c9a3b435f10e1c": "de",
    "v5-8-1-d25unit-fe1d9b8da9c99f9a91447688": "ar",
    "v5-8-1-d25unit-fee83f2a722df9ada7f0f678": "en",
}

#: The units whose reason the M2 emission site phrases, by IDENTITY.
_M2_PATH_UNITS = frozenset({
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

#: "body|declared language|context shape" -> (sentence alias,
#: language_or_script).  448 points; the R2 axis.
_LANGUAGE_AXIS_PINS = {
    "committee_en|None|ambiguous": ("AMB_E0B3B8", "LATIN"),
    "committee_en|None|none": ("AMB_75D2F7", "LATIN"),
    "committee_en|None|plain": ("AMB_A34A9B", "LATIN"),
    "committee_en|None|unique": ("AMB_85C652", "LATIN"),
    "committee_en|ar|ambiguous": ("SIG_84848B", "ARABIC"),
    "committee_en|ar|none": ("SIG_84848B", "ARABIC"),
    "committee_en|ar|plain": ("SIG_84848B", "ARABIC"),
    "committee_en|ar|unique": ("SIG_84848B", "ARABIC"),
    "committee_en|de|ambiguous": ("AMB_E0B3B8", "LATIN"),
    "committee_en|de|none": ("AMB_75D2F7", "LATIN"),
    "committee_en|de|plain": ("AMB_A34A9B", "LATIN"),
    "committee_en|de|unique": ("AMB_85C652", "LATIN"),
    "committee_en|en|ambiguous": ("AMB_E0B3B8", "LATIN"),
    "committee_en|en|none": ("AMB_75D2F7", "LATIN"),
    "committee_en|en|plain": ("AMB_A34A9B", "LATIN"),
    "committee_en|en|unique": ("AMB_85C652", "LATIN"),
    "committee_en|es|ambiguous": ("AMB_E0B3B8", "LATIN"),
    "committee_en|es|none": ("AMB_75D2F7", "LATIN"),
    "committee_en|es|plain": ("AMB_A34A9B", "LATIN"),
    "committee_en|es|unique": ("AMB_85C652", "LATIN"),
    "committee_en|fr|ambiguous": ("AMB_E0B3B8", "LATIN"),
    "committee_en|fr|none": ("AMB_75D2F7", "LATIN"),
    "committee_en|fr|plain": ("AMB_A34A9B", "LATIN"),
    "committee_en|fr|unique": ("AMB_85C652", "LATIN"),
    "committee_en|it|ambiguous": ("AMB_E0B3B8", "LATIN"),
    "committee_en|it|none": ("AMB_75D2F7", "LATIN"),
    "committee_en|it|plain": ("AMB_A34A9B", "LATIN"),
    "committee_en|it|unique": ("AMB_85C652", "LATIN"),
    "committee_en|ru|ambiguous": ("SIG_84848B", "CYRILLIC"),
    "committee_en|ru|none": ("SIG_84848B", "CYRILLIC"),
    "committee_en|ru|plain": ("SIG_84848B", "CYRILLIC"),
    "committee_en|ru|unique": ("SIG_84848B", "CYRILLIC"),
    "date_line|None|ambiguous": ("NOPRED_8DDD04", "LATIN"),
    "date_line|None|none": ("NOPRED_2FF212", "LATIN"),
    "date_line|None|plain": ("NOPRED_8A7A98", "LATIN"),
    "date_line|None|unique": ("NOPRED_B8A288", "LATIN"),
    "date_line|ar|ambiguous": ("SIG_84848B", "ARABIC"),
    "date_line|ar|none": ("SIG_84848B", "ARABIC"),
    "date_line|ar|plain": ("SIG_84848B", "ARABIC"),
    "date_line|ar|unique": ("SIG_84848B", "ARABIC"),
    "date_line|de|ambiguous": ("NOPRED_8DDD04", "LATIN"),
    "date_line|de|none": ("NOPRED_2FF212", "LATIN"),
    "date_line|de|plain": ("NOPRED_8A7A98", "LATIN"),
    "date_line|de|unique": ("NOPRED_B8A288", "LATIN"),
    "date_line|en|ambiguous": ("NOPRED_8DDD04", "LATIN"),
    "date_line|en|none": ("NOPRED_2FF212", "LATIN"),
    "date_line|en|plain": ("NOPRED_8A7A98", "LATIN"),
    "date_line|en|unique": ("NOPRED_B8A288", "LATIN"),
    "date_line|es|ambiguous": ("NOPRED_8DDD04", "LATIN"),
    "date_line|es|none": ("NOPRED_2FF212", "LATIN"),
    "date_line|es|plain": ("NOPRED_8A7A98", "LATIN"),
    "date_line|es|unique": ("NOPRED_B8A288", "LATIN"),
    "date_line|fr|ambiguous": ("NOPRED_8DDD04", "LATIN"),
    "date_line|fr|none": ("NOPRED_2FF212", "LATIN"),
    "date_line|fr|plain": ("NOPRED_8A7A98", "LATIN"),
    "date_line|fr|unique": ("NOPRED_B8A288", "LATIN"),
    "date_line|it|ambiguous": ("NOPRED_8DDD04", "LATIN"),
    "date_line|it|none": ("NOPRED_2FF212", "LATIN"),
    "date_line|it|plain": ("NOPRED_8A7A98", "LATIN"),
    "date_line|it|unique": ("NOPRED_B8A288", "LATIN"),
    "date_line|ru|ambiguous": ("SIG_84848B", "CYRILLIC"),
    "date_line|ru|none": ("SIG_84848B", "CYRILLIC"),
    "date_line|ru|plain": ("SIG_84848B", "CYRILLIC"),
    "date_line|ru|unique": ("SIG_84848B", "CYRILLIC"),
    "decisive_ru|None|ambiguous": ("AMB_C52E6A", "CYRILLIC"),
    "decisive_ru|None|none": ("AMB_BA88B8", "CYRILLIC"),
    "decisive_ru|None|plain": ("AMB_389B7C", "CYRILLIC"),
    "decisive_ru|None|unique": ("AMB_CB3C1A", "CYRILLIC"),
    "decisive_ru|ar|ambiguous": ("SIG_8684DA", "ARABIC"),
    "decisive_ru|ar|none": ("SIG_8684DA", "ARABIC"),
    "decisive_ru|ar|plain": ("SIG_8684DA", "ARABIC"),
    "decisive_ru|ar|unique": ("SIG_8684DA", "ARABIC"),
    "decisive_ru|de|ambiguous": ("AMB_C52E6A", "CYRILLIC"),
    "decisive_ru|de|none": ("AMB_BA88B8", "CYRILLIC"),
    "decisive_ru|de|plain": ("AMB_389B7C", "CYRILLIC"),
    "decisive_ru|de|unique": ("AMB_CB3C1A", "CYRILLIC"),
    "decisive_ru|en|ambiguous": ("AMB_C52E6A", "CYRILLIC"),
    "decisive_ru|en|none": ("AMB_BA88B8", "CYRILLIC"),
    "decisive_ru|en|plain": ("AMB_389B7C", "CYRILLIC"),
    "decisive_ru|en|unique": ("AMB_CB3C1A", "CYRILLIC"),
    "decisive_ru|es|ambiguous": ("AMB_C52E6A", "CYRILLIC"),
    "decisive_ru|es|none": ("AMB_BA88B8", "CYRILLIC"),
    "decisive_ru|es|plain": ("AMB_389B7C", "CYRILLIC"),
    "decisive_ru|es|unique": ("AMB_CB3C1A", "CYRILLIC"),
    "decisive_ru|fr|ambiguous": ("AMB_C52E6A", "CYRILLIC"),
    "decisive_ru|fr|none": ("AMB_BA88B8", "CYRILLIC"),
    "decisive_ru|fr|plain": ("AMB_389B7C", "CYRILLIC"),
    "decisive_ru|fr|unique": ("AMB_CB3C1A", "CYRILLIC"),
    "decisive_ru|it|ambiguous": ("AMB_C52E6A", "CYRILLIC"),
    "decisive_ru|it|none": ("AMB_BA88B8", "CYRILLIC"),
    "decisive_ru|it|plain": ("AMB_389B7C", "CYRILLIC"),
    "decisive_ru|it|unique": ("AMB_CB3C1A", "CYRILLIC"),
    "decisive_ru|ru|ambiguous": ("DEC_07E6D1", "CYRILLIC"),
    "decisive_ru|ru|none": ("DEC_4FF013", "CYRILLIC"),
    "decisive_ru|ru|plain": ("DEC_9EBD21", "CYRILLIC"),
    "decisive_ru|ru|unique": ("DEC_F222DD", "CYRILLIC"),
    "deontic_ar|None|ambiguous": ("M2_2503F1", "ARABIC"),
    "deontic_ar|None|none": ("M2_7A6AEC", "ARABIC"),
    "deontic_ar|None|plain": ("M2_2503F1", "ARABIC"),
    "deontic_ar|None|unique": ("M2_2503F1", "ARABIC"),
    "deontic_ar|ar|ambiguous": ("M2_D89E65", "ARABIC"),
    "deontic_ar|ar|none": ("M2_D89E65", "ARABIC"),
    "deontic_ar|ar|plain": ("M2_D89E65", "ARABIC"),
    "deontic_ar|ar|unique": ("M2_2503F1", "ARABIC"),
    "deontic_ar|de|ambiguous": ("M2_2503F1", "ARABIC"),
    "deontic_ar|de|none": ("M2_7A6AEC", "ARABIC"),
    "deontic_ar|de|plain": ("M2_2503F1", "ARABIC"),
    "deontic_ar|de|unique": ("M2_2503F1", "ARABIC"),
    "deontic_ar|en|ambiguous": ("M2_2503F1", "ARABIC"),
    "deontic_ar|en|none": ("M2_7A6AEC", "ARABIC"),
    "deontic_ar|en|plain": ("M2_2503F1", "ARABIC"),
    "deontic_ar|en|unique": ("M2_2503F1", "ARABIC"),
    "deontic_ar|es|ambiguous": ("M2_2503F1", "ARABIC"),
    "deontic_ar|es|none": ("M2_7A6AEC", "ARABIC"),
    "deontic_ar|es|plain": ("M2_2503F1", "ARABIC"),
    "deontic_ar|es|unique": ("M2_2503F1", "ARABIC"),
    "deontic_ar|fr|ambiguous": ("M2_2503F1", "ARABIC"),
    "deontic_ar|fr|none": ("M2_7A6AEC", "ARABIC"),
    "deontic_ar|fr|plain": ("M2_2503F1", "ARABIC"),
    "deontic_ar|fr|unique": ("M2_2503F1", "ARABIC"),
    "deontic_ar|it|ambiguous": ("M2_2503F1", "ARABIC"),
    "deontic_ar|it|none": ("M2_7A6AEC", "ARABIC"),
    "deontic_ar|it|plain": ("M2_2503F1", "ARABIC"),
    "deontic_ar|it|unique": ("M2_2503F1", "ARABIC"),
    "deontic_ar|ru|ambiguous": ("M2_2503F1", "ARABIC"),
    "deontic_ar|ru|none": ("M2_7A6AEC", "ARABIC"),
    "deontic_ar|ru|plain": ("M2_2503F1", "ARABIC"),
    "deontic_ar|ru|unique": ("M2_2503F1", "ARABIC"),
    "fragment_ru|None|ambiguous": ("M2_2503F1", "CYRILLIC"),
    "fragment_ru|None|none": ("M2_7A6AEC", "CYRILLIC"),
    "fragment_ru|None|plain": ("M2_2503F1", "CYRILLIC"),
    "fragment_ru|None|unique": ("M2_2503F1", "CYRILLIC"),
    "fragment_ru|ar|ambiguous": ("SIG_8684DA", "ARABIC"),
    "fragment_ru|ar|none": ("SIG_8684DA", "ARABIC"),
    "fragment_ru|ar|plain": ("SIG_8684DA", "ARABIC"),
    "fragment_ru|ar|unique": ("SIG_8684DA", "ARABIC"),
    "fragment_ru|de|ambiguous": ("M2_2503F1", "CYRILLIC"),
    "fragment_ru|de|none": ("M2_7A6AEC", "CYRILLIC"),
    "fragment_ru|de|plain": ("M2_2503F1", "CYRILLIC"),
    "fragment_ru|de|unique": ("M2_2503F1", "CYRILLIC"),
    "fragment_ru|en|ambiguous": ("M2_2503F1", "CYRILLIC"),
    "fragment_ru|en|none": ("M2_7A6AEC", "CYRILLIC"),
    "fragment_ru|en|plain": ("M2_2503F1", "CYRILLIC"),
    "fragment_ru|en|unique": ("M2_2503F1", "CYRILLIC"),
    "fragment_ru|es|ambiguous": ("M2_2503F1", "CYRILLIC"),
    "fragment_ru|es|none": ("M2_7A6AEC", "CYRILLIC"),
    "fragment_ru|es|plain": ("M2_2503F1", "CYRILLIC"),
    "fragment_ru|es|unique": ("M2_2503F1", "CYRILLIC"),
    "fragment_ru|fr|ambiguous": ("M2_2503F1", "CYRILLIC"),
    "fragment_ru|fr|none": ("M2_7A6AEC", "CYRILLIC"),
    "fragment_ru|fr|plain": ("M2_2503F1", "CYRILLIC"),
    "fragment_ru|fr|unique": ("M2_2503F1", "CYRILLIC"),
    "fragment_ru|it|ambiguous": ("M2_2503F1", "CYRILLIC"),
    "fragment_ru|it|none": ("M2_7A6AEC", "CYRILLIC"),
    "fragment_ru|it|plain": ("M2_2503F1", "CYRILLIC"),
    "fragment_ru|it|unique": ("M2_2503F1", "CYRILLIC"),
    "fragment_ru|ru|ambiguous": ("M2_7A6AEC", "CYRILLIC"),
    "fragment_ru|ru|none": ("M2_7A6AEC", "CYRILLIC"),
    "fragment_ru|ru|plain": ("M2_7A6AEC", "CYRILLIC"),
    "fragment_ru|ru|unique": ("M2_2503F1", "CYRILLIC"),
    "furniture_nav|None|ambiguous": ("FURN_2C03C6", "LATIN"),
    "furniture_nav|None|none": ("FURN_2C03C6", "LATIN"),
    "furniture_nav|None|plain": ("FURN_2C03C6", "LATIN"),
    "furniture_nav|None|unique": ("FURN_2C03C6", "LATIN"),
    "furniture_nav|ar|ambiguous": ("FURN_2C03C6", "ARABIC"),
    "furniture_nav|ar|none": ("FURN_2C03C6", "ARABIC"),
    "furniture_nav|ar|plain": ("FURN_2C03C6", "ARABIC"),
    "furniture_nav|ar|unique": ("FURN_2C03C6", "ARABIC"),
    "furniture_nav|de|ambiguous": ("FURN_2C03C6", "LATIN"),
    "furniture_nav|de|none": ("FURN_2C03C6", "LATIN"),
    "furniture_nav|de|plain": ("FURN_2C03C6", "LATIN"),
    "furniture_nav|de|unique": ("FURN_2C03C6", "LATIN"),
    "furniture_nav|en|ambiguous": ("FURN_2C03C6", "LATIN"),
    "furniture_nav|en|none": ("FURN_2C03C6", "LATIN"),
    "furniture_nav|en|plain": ("FURN_2C03C6", "LATIN"),
    "furniture_nav|en|unique": ("FURN_2C03C6", "LATIN"),
    "furniture_nav|es|ambiguous": ("FURN_2C03C6", "LATIN"),
    "furniture_nav|es|none": ("FURN_2C03C6", "LATIN"),
    "furniture_nav|es|plain": ("FURN_2C03C6", "LATIN"),
    "furniture_nav|es|unique": ("FURN_2C03C6", "LATIN"),
    "furniture_nav|fr|ambiguous": ("FURN_2C03C6", "LATIN"),
    "furniture_nav|fr|none": ("FURN_2C03C6", "LATIN"),
    "furniture_nav|fr|plain": ("FURN_2C03C6", "LATIN"),
    "furniture_nav|fr|unique": ("FURN_2C03C6", "LATIN"),
    "furniture_nav|it|ambiguous": ("FURN_2C03C6", "LATIN"),
    "furniture_nav|it|none": ("FURN_2C03C6", "LATIN"),
    "furniture_nav|it|plain": ("FURN_2C03C6", "LATIN"),
    "furniture_nav|it|unique": ("FURN_2C03C6", "LATIN"),
    "furniture_nav|ru|ambiguous": ("FURN_2C03C6", "CYRILLIC"),
    "furniture_nav|ru|none": ("FURN_2C03C6", "CYRILLIC"),
    "furniture_nav|ru|plain": ("FURN_2C03C6", "CYRILLIC"),
    "furniture_nav|ru|unique": ("FURN_2C03C6", "CYRILLIC"),
    "operative_es|None|ambiguous": ("NOPRED_019FED", "LATIN"),
    "operative_es|None|none": ("NOPRED_9778EE", "LATIN"),
    "operative_es|None|plain": ("NOPRED_9E5AF1", "LATIN"),
    "operative_es|None|unique": ("NOPRED_6AF56F", "LATIN"),
    "operative_es|ar|ambiguous": ("SIG_84848B", "ARABIC"),
    "operative_es|ar|none": ("SIG_84848B", "ARABIC"),
    "operative_es|ar|plain": ("SIG_84848B", "ARABIC"),
    "operative_es|ar|unique": ("SIG_84848B", "ARABIC"),
    "operative_es|de|ambiguous": ("NOPRED_019FED", "LATIN"),
    "operative_es|de|none": ("NOPRED_9778EE", "LATIN"),
    "operative_es|de|plain": ("NOPRED_9E5AF1", "LATIN"),
    "operative_es|de|unique": ("NOPRED_6AF56F", "LATIN"),
    "operative_es|en|ambiguous": ("NOPRED_019FED", "LATIN"),
    "operative_es|en|none": ("NOPRED_9778EE", "LATIN"),
    "operative_es|en|plain": ("NOPRED_9E5AF1", "LATIN"),
    "operative_es|en|unique": ("NOPRED_6AF56F", "LATIN"),
    "operative_es|es|ambiguous": ("NOPRED_019FED", "LATIN"),
    "operative_es|es|none": ("NOPRED_9778EE", "LATIN"),
    "operative_es|es|plain": ("NOPRED_9E5AF1", "LATIN"),
    "operative_es|es|unique": ("NOPRED_6AF56F", "LATIN"),
    "operative_es|fr|ambiguous": ("NOPRED_019FED", "LATIN"),
    "operative_es|fr|none": ("NOPRED_9778EE", "LATIN"),
    "operative_es|fr|plain": ("NOPRED_9E5AF1", "LATIN"),
    "operative_es|fr|unique": ("NOPRED_6AF56F", "LATIN"),
    "operative_es|it|ambiguous": ("NOPRED_019FED", "LATIN"),
    "operative_es|it|none": ("NOPRED_9778EE", "LATIN"),
    "operative_es|it|plain": ("NOPRED_9E5AF1", "LATIN"),
    "operative_es|it|unique": ("NOPRED_6AF56F", "LATIN"),
    "operative_es|ru|ambiguous": ("SIG_84848B", "CYRILLIC"),
    "operative_es|ru|none": ("SIG_84848B", "CYRILLIC"),
    "operative_es|ru|plain": ("SIG_84848B", "CYRILLIC"),
    "operative_es|ru|unique": ("SIG_84848B", "CYRILLIC"),
    "operative_it|None|ambiguous": ("M2_2503F1", "LATIN"),
    "operative_it|None|none": ("M2_7A6AEC", "LATIN"),
    "operative_it|None|plain": ("M2_2503F1", "LATIN"),
    "operative_it|None|unique": ("M2_2503F1", "LATIN"),
    "operative_it|ar|ambiguous": ("SIG_84848B", "ARABIC"),
    "operative_it|ar|none": ("SIG_84848B", "ARABIC"),
    "operative_it|ar|plain": ("SIG_84848B", "ARABIC"),
    "operative_it|ar|unique": ("SIG_84848B", "ARABIC"),
    "operative_it|de|ambiguous": ("M2_2503F1", "LATIN"),
    "operative_it|de|none": ("M2_7A6AEC", "LATIN"),
    "operative_it|de|plain": ("M2_2503F1", "LATIN"),
    "operative_it|de|unique": ("M2_2503F1", "LATIN"),
    "operative_it|en|ambiguous": ("M2_2503F1", "LATIN"),
    "operative_it|en|none": ("M2_7A6AEC", "LATIN"),
    "operative_it|en|plain": ("M2_2503F1", "LATIN"),
    "operative_it|en|unique": ("M2_2503F1", "LATIN"),
    "operative_it|es|ambiguous": ("M2_2503F1", "LATIN"),
    "operative_it|es|none": ("M2_7A6AEC", "LATIN"),
    "operative_it|es|plain": ("M2_2503F1", "LATIN"),
    "operative_it|es|unique": ("M2_2503F1", "LATIN"),
    "operative_it|fr|ambiguous": ("M2_2503F1", "LATIN"),
    "operative_it|fr|none": ("M2_7A6AEC", "LATIN"),
    "operative_it|fr|plain": ("M2_2503F1", "LATIN"),
    "operative_it|fr|unique": ("M2_2503F1", "LATIN"),
    "operative_it|it|ambiguous": ("M2_2503F1", "LATIN"),
    "operative_it|it|none": ("M2_7A6AEC", "LATIN"),
    "operative_it|it|plain": ("M2_2503F1", "LATIN"),
    "operative_it|it|unique": ("M2_2503F1", "LATIN"),
    "operative_it|ru|ambiguous": ("SIG_84848B", "CYRILLIC"),
    "operative_it|ru|none": ("SIG_84848B", "CYRILLIC"),
    "operative_it|ru|plain": ("SIG_84848B", "CYRILLIC"),
    "operative_it|ru|unique": ("SIG_84848B", "CYRILLIC"),
    "operative_ru|None|ambiguous": ("M2_2503F1", "CYRILLIC"),
    "operative_ru|None|none": ("M2_7A6AEC", "CYRILLIC"),
    "operative_ru|None|plain": ("M2_2503F1", "CYRILLIC"),
    "operative_ru|None|unique": ("M2_2503F1", "CYRILLIC"),
    "operative_ru|ar|ambiguous": ("SIG_84848B", "ARABIC"),
    "operative_ru|ar|none": ("SIG_84848B", "ARABIC"),
    "operative_ru|ar|plain": ("SIG_84848B", "ARABIC"),
    "operative_ru|ar|unique": ("SIG_84848B", "ARABIC"),
    "operative_ru|de|ambiguous": ("M2_2503F1", "CYRILLIC"),
    "operative_ru|de|none": ("M2_7A6AEC", "CYRILLIC"),
    "operative_ru|de|plain": ("M2_2503F1", "CYRILLIC"),
    "operative_ru|de|unique": ("M2_2503F1", "CYRILLIC"),
    "operative_ru|en|ambiguous": ("M2_2503F1", "CYRILLIC"),
    "operative_ru|en|none": ("M2_7A6AEC", "CYRILLIC"),
    "operative_ru|en|plain": ("M2_2503F1", "CYRILLIC"),
    "operative_ru|en|unique": ("M2_2503F1", "CYRILLIC"),
    "operative_ru|es|ambiguous": ("M2_2503F1", "CYRILLIC"),
    "operative_ru|es|none": ("M2_7A6AEC", "CYRILLIC"),
    "operative_ru|es|plain": ("M2_2503F1", "CYRILLIC"),
    "operative_ru|es|unique": ("M2_2503F1", "CYRILLIC"),
    "operative_ru|fr|ambiguous": ("M2_2503F1", "CYRILLIC"),
    "operative_ru|fr|none": ("M2_7A6AEC", "CYRILLIC"),
    "operative_ru|fr|plain": ("M2_2503F1", "CYRILLIC"),
    "operative_ru|fr|unique": ("M2_2503F1", "CYRILLIC"),
    "operative_ru|it|ambiguous": ("M2_2503F1", "CYRILLIC"),
    "operative_ru|it|none": ("M2_7A6AEC", "CYRILLIC"),
    "operative_ru|it|plain": ("M2_2503F1", "CYRILLIC"),
    "operative_ru|it|unique": ("M2_2503F1", "CYRILLIC"),
    "operative_ru|ru|ambiguous": ("M2_2503F1", "CYRILLIC"),
    "operative_ru|ru|none": ("M2_7A6AEC", "CYRILLIC"),
    "operative_ru|ru|plain": ("M2_2503F1", "CYRILLIC"),
    "operative_ru|ru|unique": ("M2_2503F1", "CYRILLIC"),
    "recital_en|None|ambiguous": ("AMB_E40EE9", "LATIN"),
    "recital_en|None|none": ("GOVU_D93160", "LATIN"),
    "recital_en|None|plain": ("GOVU_080546", "LATIN"),
    "recital_en|None|unique": ("GOV_385D52", "LATIN"),
    "recital_en|ar|ambiguous": ("SIG_84848B", "ARABIC"),
    "recital_en|ar|none": ("SIG_84848B", "ARABIC"),
    "recital_en|ar|plain": ("SIG_84848B", "ARABIC"),
    "recital_en|ar|unique": ("SIG_84848B", "ARABIC"),
    "recital_en|de|ambiguous": ("AMB_E40EE9", "LATIN"),
    "recital_en|de|none": ("GOVU_D93160", "LATIN"),
    "recital_en|de|plain": ("GOVU_080546", "LATIN"),
    "recital_en|de|unique": ("GOV_385D52", "LATIN"),
    "recital_en|en|ambiguous": ("AMB_E40EE9", "LATIN"),
    "recital_en|en|none": ("GOVU_D93160", "LATIN"),
    "recital_en|en|plain": ("GOVU_080546", "LATIN"),
    "recital_en|en|unique": ("GOV_385D52", "LATIN"),
    "recital_en|es|ambiguous": ("AMB_E40EE9", "LATIN"),
    "recital_en|es|none": ("GOVU_D93160", "LATIN"),
    "recital_en|es|plain": ("GOVU_080546", "LATIN"),
    "recital_en|es|unique": ("GOV_385D52", "LATIN"),
    "recital_en|fr|ambiguous": ("AMB_E40EE9", "LATIN"),
    "recital_en|fr|none": ("GOVU_D93160", "LATIN"),
    "recital_en|fr|plain": ("GOVU_080546", "LATIN"),
    "recital_en|fr|unique": ("GOV_385D52", "LATIN"),
    "recital_en|it|ambiguous": ("AMB_E40EE9", "LATIN"),
    "recital_en|it|none": ("GOVU_D93160", "LATIN"),
    "recital_en|it|plain": ("GOVU_080546", "LATIN"),
    "recital_en|it|unique": ("GOV_385D52", "LATIN"),
    "recital_en|ru|ambiguous": ("SIG_84848B", "CYRILLIC"),
    "recital_en|ru|none": ("SIG_84848B", "CYRILLIC"),
    "recital_en|ru|plain": ("SIG_84848B", "CYRILLIC"),
    "recital_en|ru|unique": ("SIG_84848B", "CYRILLIC"),
    "recital_es|None|ambiguous": ("AMB_E40EE9", "LATIN"),
    "recital_es|None|none": ("GOVU_D93160", "LATIN"),
    "recital_es|None|plain": ("GOVU_080546", "LATIN"),
    "recital_es|None|unique": ("GOV_385D52", "LATIN"),
    "recital_es|ar|ambiguous": ("SIG_84848B", "ARABIC"),
    "recital_es|ar|none": ("SIG_84848B", "ARABIC"),
    "recital_es|ar|plain": ("SIG_84848B", "ARABIC"),
    "recital_es|ar|unique": ("SIG_84848B", "ARABIC"),
    "recital_es|de|ambiguous": ("AMB_E40EE9", "LATIN"),
    "recital_es|de|none": ("GOVU_D93160", "LATIN"),
    "recital_es|de|plain": ("GOVU_080546", "LATIN"),
    "recital_es|de|unique": ("GOV_385D52", "LATIN"),
    "recital_es|en|ambiguous": ("AMB_E40EE9", "LATIN"),
    "recital_es|en|none": ("GOVU_D93160", "LATIN"),
    "recital_es|en|plain": ("GOVU_080546", "LATIN"),
    "recital_es|en|unique": ("GOV_385D52", "LATIN"),
    "recital_es|es|ambiguous": ("AMB_E40EE9", "LATIN"),
    "recital_es|es|none": ("GOVU_D93160", "LATIN"),
    "recital_es|es|plain": ("GOVU_080546", "LATIN"),
    "recital_es|es|unique": ("GOV_385D52", "LATIN"),
    "recital_es|fr|ambiguous": ("AMB_E40EE9", "LATIN"),
    "recital_es|fr|none": ("GOVU_D93160", "LATIN"),
    "recital_es|fr|plain": ("GOVU_080546", "LATIN"),
    "recital_es|fr|unique": ("GOV_385D52", "LATIN"),
    "recital_es|it|ambiguous": ("AMB_E40EE9", "LATIN"),
    "recital_es|it|none": ("GOVU_D93160", "LATIN"),
    "recital_es|it|plain": ("GOVU_080546", "LATIN"),
    "recital_es|it|unique": ("GOV_385D52", "LATIN"),
    "recital_es|ru|ambiguous": ("SIG_84848B", "CYRILLIC"),
    "recital_es|ru|none": ("SIG_84848B", "CYRILLIC"),
    "recital_es|ru|plain": ("SIG_84848B", "CYRILLIC"),
    "recital_es|ru|unique": ("SIG_84848B", "CYRILLIC"),
    "recital_it|None|ambiguous": ("AMB_E40EE9", "LATIN"),
    "recital_it|None|none": ("GOVU_D93160", "LATIN"),
    "recital_it|None|plain": ("GOVU_080546", "LATIN"),
    "recital_it|None|unique": ("GOV_385D52", "LATIN"),
    "recital_it|ar|ambiguous": ("SIG_84848B", "ARABIC"),
    "recital_it|ar|none": ("SIG_84848B", "ARABIC"),
    "recital_it|ar|plain": ("SIG_84848B", "ARABIC"),
    "recital_it|ar|unique": ("SIG_84848B", "ARABIC"),
    "recital_it|de|ambiguous": ("AMB_E40EE9", "LATIN"),
    "recital_it|de|none": ("GOVU_D93160", "LATIN"),
    "recital_it|de|plain": ("GOVU_080546", "LATIN"),
    "recital_it|de|unique": ("GOV_385D52", "LATIN"),
    "recital_it|en|ambiguous": ("AMB_E40EE9", "LATIN"),
    "recital_it|en|none": ("GOVU_D93160", "LATIN"),
    "recital_it|en|plain": ("GOVU_080546", "LATIN"),
    "recital_it|en|unique": ("GOV_385D52", "LATIN"),
    "recital_it|es|ambiguous": ("AMB_E40EE9", "LATIN"),
    "recital_it|es|none": ("GOVU_D93160", "LATIN"),
    "recital_it|es|plain": ("GOVU_080546", "LATIN"),
    "recital_it|es|unique": ("GOV_385D52", "LATIN"),
    "recital_it|fr|ambiguous": ("AMB_E40EE9", "LATIN"),
    "recital_it|fr|none": ("GOVU_D93160", "LATIN"),
    "recital_it|fr|plain": ("GOVU_080546", "LATIN"),
    "recital_it|fr|unique": ("GOV_385D52", "LATIN"),
    "recital_it|it|ambiguous": ("AMB_E40EE9", "LATIN"),
    "recital_it|it|none": ("GOVU_D93160", "LATIN"),
    "recital_it|it|plain": ("GOVU_080546", "LATIN"),
    "recital_it|it|unique": ("GOV_385D52", "LATIN"),
    "recital_it|ru|ambiguous": ("SIG_84848B", "CYRILLIC"),
    "recital_it|ru|none": ("SIG_84848B", "CYRILLIC"),
    "recital_it|ru|plain": ("SIG_84848B", "CYRILLIC"),
    "recital_it|ru|unique": ("SIG_84848B", "CYRILLIC"),
    "recital_ru|None|ambiguous": ("AMB_E40EE9", "CYRILLIC"),
    "recital_ru|None|none": ("GOVU_D93160", "CYRILLIC"),
    "recital_ru|None|plain": ("GOVU_080546", "CYRILLIC"),
    "recital_ru|None|unique": ("GOV_385D52", "CYRILLIC"),
    "recital_ru|ar|ambiguous": ("SIG_8684DA", "ARABIC"),
    "recital_ru|ar|none": ("SIG_8684DA", "ARABIC"),
    "recital_ru|ar|plain": ("SIG_8684DA", "ARABIC"),
    "recital_ru|ar|unique": ("SIG_8684DA", "ARABIC"),
    "recital_ru|de|ambiguous": ("AMB_E40EE9", "CYRILLIC"),
    "recital_ru|de|none": ("GOVU_D93160", "CYRILLIC"),
    "recital_ru|de|plain": ("GOVU_080546", "CYRILLIC"),
    "recital_ru|de|unique": ("GOV_385D52", "CYRILLIC"),
    "recital_ru|en|ambiguous": ("AMB_E40EE9", "CYRILLIC"),
    "recital_ru|en|none": ("GOVU_D93160", "CYRILLIC"),
    "recital_ru|en|plain": ("GOVU_080546", "CYRILLIC"),
    "recital_ru|en|unique": ("GOV_385D52", "CYRILLIC"),
    "recital_ru|es|ambiguous": ("AMB_E40EE9", "CYRILLIC"),
    "recital_ru|es|none": ("GOVU_D93160", "CYRILLIC"),
    "recital_ru|es|plain": ("GOVU_080546", "CYRILLIC"),
    "recital_ru|es|unique": ("GOV_385D52", "CYRILLIC"),
    "recital_ru|fr|ambiguous": ("AMB_E40EE9", "CYRILLIC"),
    "recital_ru|fr|none": ("GOVU_D93160", "CYRILLIC"),
    "recital_ru|fr|plain": ("GOVU_080546", "CYRILLIC"),
    "recital_ru|fr|unique": ("GOV_385D52", "CYRILLIC"),
    "recital_ru|it|ambiguous": ("AMB_E40EE9", "CYRILLIC"),
    "recital_ru|it|none": ("GOVU_D93160", "CYRILLIC"),
    "recital_ru|it|plain": ("GOVU_080546", "CYRILLIC"),
    "recital_ru|it|unique": ("GOV_385D52", "CYRILLIC"),
    "recital_ru|ru|ambiguous": ("AMB_E40EE9", "CYRILLIC"),
    "recital_ru|ru|none": ("GOVU_D93160", "CYRILLIC"),
    "recital_ru|ru|plain": ("GOVU_080546", "CYRILLIC"),
    "recital_ru|ru|unique": ("GOV_385D52", "CYRILLIC"),
    "subordinate_de|None|ambiguous": ("M2_2F8FB4", "LATIN"),
    "subordinate_de|None|none": ("M2_2F8FB4", "LATIN"),
    "subordinate_de|None|plain": ("M2_2F8FB4", "LATIN"),
    "subordinate_de|None|unique": ("M2_2F8FB4", "LATIN"),
    "subordinate_de|ar|ambiguous": ("SIG_84848B", "ARABIC"),
    "subordinate_de|ar|none": ("SIG_84848B", "ARABIC"),
    "subordinate_de|ar|plain": ("SIG_84848B", "ARABIC"),
    "subordinate_de|ar|unique": ("SIG_84848B", "ARABIC"),
    "subordinate_de|de|ambiguous": ("M2_2F8FB4", "LATIN"),
    "subordinate_de|de|none": ("M2_2F8FB4", "LATIN"),
    "subordinate_de|de|plain": ("M2_2F8FB4", "LATIN"),
    "subordinate_de|de|unique": ("M2_2F8FB4", "LATIN"),
    "subordinate_de|en|ambiguous": ("M2_2F8FB4", "LATIN"),
    "subordinate_de|en|none": ("M2_2F8FB4", "LATIN"),
    "subordinate_de|en|plain": ("M2_2F8FB4", "LATIN"),
    "subordinate_de|en|unique": ("M2_2F8FB4", "LATIN"),
    "subordinate_de|es|ambiguous": ("M2_2F8FB4", "LATIN"),
    "subordinate_de|es|none": ("M2_2F8FB4", "LATIN"),
    "subordinate_de|es|plain": ("M2_2F8FB4", "LATIN"),
    "subordinate_de|es|unique": ("M2_2F8FB4", "LATIN"),
    "subordinate_de|fr|ambiguous": ("M2_2F8FB4", "LATIN"),
    "subordinate_de|fr|none": ("M2_2F8FB4", "LATIN"),
    "subordinate_de|fr|plain": ("M2_2F8FB4", "LATIN"),
    "subordinate_de|fr|unique": ("M2_2F8FB4", "LATIN"),
    "subordinate_de|it|ambiguous": ("M2_2F8FB4", "LATIN"),
    "subordinate_de|it|none": ("M2_2F8FB4", "LATIN"),
    "subordinate_de|it|plain": ("M2_2F8FB4", "LATIN"),
    "subordinate_de|it|unique": ("M2_2F8FB4", "LATIN"),
    "subordinate_de|ru|ambiguous": ("SIG_84848B", "CYRILLIC"),
    "subordinate_de|ru|none": ("SIG_84848B", "CYRILLIC"),
    "subordinate_de|ru|plain": ("SIG_84848B", "CYRILLIC"),
    "subordinate_de|ru|unique": ("SIG_84848B", "CYRILLIC"),
}

#: "ladder|emitted span length" -> (ladder words, pad characters,
#: emitted predicate-span length, sentence alias).  55 points.
_SPAN_AXIS_PINS = {
    "gov|100": (3, 10, 100, "GOV_385D52"),
    "gov|150": (8, 9, 150, "GOV_385D52"),
    "gov|199": (14, 3, 199, "GOV_385D52"),
    "gov|200": (14, 4, 200, "GOV_385D52"),
    "gov|201": (14, 5, 201, "GOV_385D52"),
    "gov|250": (19, 9, 250, "GOV_385D52"),
    "gov|300": (25, 6, 300, "GOV_385D52"),
    "gov|350": (33, 9, 350, "GOV_385D52"),
    "gov|400": (41, 7, 400, "GOV_385D52"),
    "gov|450": (46, 11, 450, "GOV_385D52"),
    "gov|500": (51, 7, 500, "GOV_385D52"),
    "gov|516": (52, 11, 516, "GOV_385D52"),
    "gov|517": (53, 10, 517, "GOV_385D52"),
    "gov|518": (53, 11, 518, "GOV_385D52"),
    "gov|550": (56, 4, 550, "GOV_385D52"),
    "gov|600": (64, 1, 600, "GOV_385D52"),
    "gov|650": (70, 9, 650, "GOV_385D52"),
    "gov|700": (75, 10, 700, "GOV_385D52"),
    "m2|64": (0, 0, 64, "M2_7A6AEC"),
    "m2|100": (5, 4, 100, "M2_7A6AEC"),
    "m2|150": (9, 10, 150, "M2_7A6AEC"),
    "m2|199": (15, 1, 199, "M2_7A6AEC"),
    "m2|200": (15, 2, 200, "M2_7A6AEC"),
    "m2|201": (15, 3, 201, "M2_7A6AEC"),
    "m2|250": (20, 6, 250, "M2_7A6AEC"),
    "m2|300": (27, 3, 300, "M2_7A6AEC"),
    "m2|350": (34, 11, 350, "M2_7A6AEC"),
    "m2|400": (43, 2, 400, "M2_7A6AEC"),
    "m2|450": (47, 4, 450, "M2_7A6AEC"),
    "m2|500": (52, 4, 500, "M2_7A6AEC"),
    "m2|516": (54, 7, 516, "M2_7A6AEC"),
    "m2|517": (54, 8, 517, "M2_7A6AEC"),
    "m2|518": (54, 9, 518, "M2_7A6AEC"),
    "m2|550": (57, 6, 550, "M2_7A6AEC"),
    "m2|600": (64, 10, 600, "M2_7A6AEC"),
    "m2|650": (71, 8, 650, "M2_7A6AEC"),
    "m2|700": (76, 10, 700, "M2_7A6AEC"),
    "m2short|31": (0, 0, 31, "M2_7A6AEC"),
    "m2short|50": (2, 11, 50, "M2_7A6AEC"),
    "m2short|64": (5, 1, 64, "M2_7A6AEC"),
    "m2short|100": (8, 1, 100, "M2_7A6AEC"),
    "m2short|150": (12, 10, 150, "M2_7A6AEC"),
    "m2short|199": (19, 0, 199, "M2_7A6AEC"),
    "m2short|200": (19, 1, 200, "M2_7A6AEC"),
    "m2short|201": (19, 2, 201, "M2_7A6AEC"),
    "m2short|250": (24, 7, 250, "M2_7A6AEC"),
    "m2short|300": (33, 1, 300, "M2_7A6AEC"),
    "m2short|350": (38, 9, 350, "M2_7A6AEC"),
    "m2short|400": (45, 11, 400, "M2_7A6AEC"),
    "m2short|516": (57, 5, 516, "M2_7A6AEC"),
    "m2short|517": (57, 6, 517, "M2_7A6AEC"),
    "m2short|518": (57, 7, 518, "M2_7A6AEC"),
    "m2short|550": (63, 6, 550, "M2_7A6AEC"),
    "m2short|600": (70, 1, 600, "M2_7A6AEC"),
    "m2short|650": (75, 2, 650, "M2_7A6AEC"),
}

#: "content_region_type|body|context shape" -> (sentence alias,
#: language_or_script).  408 points; the P6.1.3 axis that closes the
#: independent verifier's escape class E1.  EXHAUSTIVE over production's
#: own regions.ORIGIN_CLASSES: 17 values x 6 bodies x 4 context shapes.
_REGION_AXIS_PINS = {
    "BREADCRUMB|date_line|ambiguous": ("NOPRED_8DDD04", "LATIN"),
    "BREADCRUMB|date_line|none": ("NOPRED_2FF212", "LATIN"),
    "BREADCRUMB|date_line|plain": ("NOPRED_8A7A98", "LATIN"),
    "BREADCRUMB|date_line|unique": ("NOPRED_B8A288", "LATIN"),
    "BREADCRUMB|deontic_ar|ambiguous": ("M2_D89E65", "ARABIC"),
    "BREADCRUMB|deontic_ar|none": ("M2_D89E65", "ARABIC"),
    "BREADCRUMB|deontic_ar|plain": ("M2_D89E65", "ARABIC"),
    "BREADCRUMB|deontic_ar|unique": ("M2_2503F1", "ARABIC"),
    "BREADCRUMB|furniture_nav|ambiguous": ("NOPRED_019FED", "LATIN"),
    "BREADCRUMB|furniture_nav|none": ("NOPRED_9778EE", "LATIN"),
    "BREADCRUMB|furniture_nav|plain": ("NOPRED_9E5AF1", "LATIN"),
    "BREADCRUMB|furniture_nav|unique": ("NOPRED_6AF56F", "LATIN"),
    "BREADCRUMB|operative_ru|ambiguous": ("M2_2503F1", "CYRILLIC"),
    "BREADCRUMB|operative_ru|none": ("M2_7A6AEC", "CYRILLIC"),
    "BREADCRUMB|operative_ru|plain": ("M2_2503F1", "CYRILLIC"),
    "BREADCRUMB|operative_ru|unique": ("M2_2503F1", "CYRILLIC"),
    "BREADCRUMB|recital_it|ambiguous": ("AMB_E40EE9", "LATIN"),
    "BREADCRUMB|recital_it|none": ("GOVU_D93160", "LATIN"),
    "BREADCRUMB|recital_it|plain": ("GOVU_080546", "LATIN"),
    "BREADCRUMB|recital_it|unique": ("GOV_385D52", "LATIN"),
    "BREADCRUMB|subordinate_de|ambiguous": ("M2_2F8FB4", "LATIN"),
    "BREADCRUMB|subordinate_de|none": ("M2_2F8FB4", "LATIN"),
    "BREADCRUMB|subordinate_de|plain": ("M2_2F8FB4", "LATIN"),
    "BREADCRUMB|subordinate_de|unique": ("M2_2F8FB4", "LATIN"),
    "CAPTION_OR_FIGURE_PROPOSITION|date_line|ambiguous":
        ("NOPRED_8DDD04", "LATIN"),
    "CAPTION_OR_FIGURE_PROPOSITION|date_line|none": ("NOPRED_2FF212", "LATIN"),
    "CAPTION_OR_FIGURE_PROPOSITION|date_line|plain":
        ("NOPRED_8A7A98", "LATIN"),
    "CAPTION_OR_FIGURE_PROPOSITION|date_line|unique":
        ("NOPRED_B8A288", "LATIN"),
    "CAPTION_OR_FIGURE_PROPOSITION|deontic_ar|ambiguous":
        ("M2_D89E65", "ARABIC"),
    "CAPTION_OR_FIGURE_PROPOSITION|deontic_ar|none": ("M2_D89E65", "ARABIC"),
    "CAPTION_OR_FIGURE_PROPOSITION|deontic_ar|plain": ("M2_D89E65", "ARABIC"),
    "CAPTION_OR_FIGURE_PROPOSITION|deontic_ar|unique": ("M2_2503F1", "ARABIC"),
    "CAPTION_OR_FIGURE_PROPOSITION|furniture_nav|ambiguous":
        ("NOPRED_019FED", "LATIN"),
    "CAPTION_OR_FIGURE_PROPOSITION|furniture_nav|none":
        ("NOPRED_9778EE", "LATIN"),
    "CAPTION_OR_FIGURE_PROPOSITION|furniture_nav|plain":
        ("NOPRED_9E5AF1", "LATIN"),
    "CAPTION_OR_FIGURE_PROPOSITION|furniture_nav|unique":
        ("NOPRED_6AF56F", "LATIN"),
    "CAPTION_OR_FIGURE_PROPOSITION|operative_ru|ambiguous":
        ("M2_2503F1", "CYRILLIC"),
    "CAPTION_OR_FIGURE_PROPOSITION|operative_ru|none":
        ("M2_7A6AEC", "CYRILLIC"),
    "CAPTION_OR_FIGURE_PROPOSITION|operative_ru|plain":
        ("M2_2503F1", "CYRILLIC"),
    "CAPTION_OR_FIGURE_PROPOSITION|operative_ru|unique":
        ("M2_2503F1", "CYRILLIC"),
    "CAPTION_OR_FIGURE_PROPOSITION|recital_it|ambiguous":
        ("AMB_E40EE9", "LATIN"),
    "CAPTION_OR_FIGURE_PROPOSITION|recital_it|none": ("GOVU_D93160", "LATIN"),
    "CAPTION_OR_FIGURE_PROPOSITION|recital_it|plain": ("GOVU_080546", "LATIN"),
    "CAPTION_OR_FIGURE_PROPOSITION|recital_it|unique": ("GOV_385D52", "LATIN"),
    "CAPTION_OR_FIGURE_PROPOSITION|subordinate_de|ambiguous":
        ("M2_2F8FB4", "LATIN"),
    "CAPTION_OR_FIGURE_PROPOSITION|subordinate_de|none":
        ("M2_2F8FB4", "LATIN"),
    "CAPTION_OR_FIGURE_PROPOSITION|subordinate_de|plain":
        ("M2_2F8FB4", "LATIN"),
    "CAPTION_OR_FIGURE_PROPOSITION|subordinate_de|unique":
        ("M2_2F8FB4", "LATIN"),
    "COOKIE_OR_CONSENT_TEXT|date_line|ambiguous": ("NOPRED_8DDD04", "LATIN"),
    "COOKIE_OR_CONSENT_TEXT|date_line|none": ("NOPRED_2FF212", "LATIN"),
    "COOKIE_OR_CONSENT_TEXT|date_line|plain": ("NOPRED_8A7A98", "LATIN"),
    "COOKIE_OR_CONSENT_TEXT|date_line|unique": ("NOPRED_B8A288", "LATIN"),
    "COOKIE_OR_CONSENT_TEXT|deontic_ar|ambiguous": ("M2_D89E65", "ARABIC"),
    "COOKIE_OR_CONSENT_TEXT|deontic_ar|none": ("M2_D89E65", "ARABIC"),
    "COOKIE_OR_CONSENT_TEXT|deontic_ar|plain": ("M2_D89E65", "ARABIC"),
    "COOKIE_OR_CONSENT_TEXT|deontic_ar|unique": ("M2_2503F1", "ARABIC"),
    "COOKIE_OR_CONSENT_TEXT|furniture_nav|ambiguous":
        ("NOPRED_019FED", "LATIN"),
    "COOKIE_OR_CONSENT_TEXT|furniture_nav|none": ("NOPRED_9778EE", "LATIN"),
    "COOKIE_OR_CONSENT_TEXT|furniture_nav|plain": ("NOPRED_9E5AF1", "LATIN"),
    "COOKIE_OR_CONSENT_TEXT|furniture_nav|unique": ("NOPRED_6AF56F", "LATIN"),
    "COOKIE_OR_CONSENT_TEXT|operative_ru|ambiguous": ("M2_2503F1", "CYRILLIC"),
    "COOKIE_OR_CONSENT_TEXT|operative_ru|none": ("M2_7A6AEC", "CYRILLIC"),
    "COOKIE_OR_CONSENT_TEXT|operative_ru|plain": ("M2_2503F1", "CYRILLIC"),
    "COOKIE_OR_CONSENT_TEXT|operative_ru|unique": ("M2_2503F1", "CYRILLIC"),
    "COOKIE_OR_CONSENT_TEXT|recital_it|ambiguous": ("AMB_E40EE9", "LATIN"),
    "COOKIE_OR_CONSENT_TEXT|recital_it|none": ("GOVU_D93160", "LATIN"),
    "COOKIE_OR_CONSENT_TEXT|recital_it|plain": ("GOVU_080546", "LATIN"),
    "COOKIE_OR_CONSENT_TEXT|recital_it|unique": ("GOV_385D52", "LATIN"),
    "COOKIE_OR_CONSENT_TEXT|subordinate_de|ambiguous": ("M2_2F8FB4", "LATIN"),
    "COOKIE_OR_CONSENT_TEXT|subordinate_de|none": ("M2_2F8FB4", "LATIN"),
    "COOKIE_OR_CONSENT_TEXT|subordinate_de|plain": ("M2_2F8FB4", "LATIN"),
    "COOKIE_OR_CONSENT_TEXT|subordinate_de|unique": ("M2_2F8FB4", "LATIN"),
    "DOCUMENT_INDEX_ENTRY|date_line|ambiguous": ("FURN_588A31", "LATIN"),
    "DOCUMENT_INDEX_ENTRY|date_line|none": ("FURN_588A31", "LATIN"),
    "DOCUMENT_INDEX_ENTRY|date_line|plain": ("FURN_588A31", "LATIN"),
    "DOCUMENT_INDEX_ENTRY|date_line|unique": ("FURN_588A31", "LATIN"),
    "DOCUMENT_INDEX_ENTRY|deontic_ar|ambiguous": ("FURN_588A31", "ARABIC"),
    "DOCUMENT_INDEX_ENTRY|deontic_ar|none": ("FURN_588A31", "ARABIC"),
    "DOCUMENT_INDEX_ENTRY|deontic_ar|plain": ("FURN_588A31", "ARABIC"),
    "DOCUMENT_INDEX_ENTRY|deontic_ar|unique": ("FURN_588A31", "ARABIC"),
    "DOCUMENT_INDEX_ENTRY|furniture_nav|ambiguous": ("FURN_588A31", "LATIN"),
    "DOCUMENT_INDEX_ENTRY|furniture_nav|none": ("FURN_588A31", "LATIN"),
    "DOCUMENT_INDEX_ENTRY|furniture_nav|plain": ("FURN_588A31", "LATIN"),
    "DOCUMENT_INDEX_ENTRY|furniture_nav|unique": ("FURN_588A31", "LATIN"),
    "DOCUMENT_INDEX_ENTRY|operative_ru|ambiguous": ("FURN_588A31", "CYRILLIC"),
    "DOCUMENT_INDEX_ENTRY|operative_ru|none": ("FURN_588A31", "CYRILLIC"),
    "DOCUMENT_INDEX_ENTRY|operative_ru|plain": ("FURN_588A31", "CYRILLIC"),
    "DOCUMENT_INDEX_ENTRY|operative_ru|unique": ("FURN_588A31", "CYRILLIC"),
    "DOCUMENT_INDEX_ENTRY|recital_it|ambiguous": ("FURN_588A31", "LATIN"),
    "DOCUMENT_INDEX_ENTRY|recital_it|none": ("FURN_588A31", "LATIN"),
    "DOCUMENT_INDEX_ENTRY|recital_it|plain": ("FURN_588A31", "LATIN"),
    "DOCUMENT_INDEX_ENTRY|recital_it|unique": ("FURN_588A31", "LATIN"),
    "DOCUMENT_INDEX_ENTRY|subordinate_de|ambiguous": ("FURN_588A31", "LATIN"),
    "DOCUMENT_INDEX_ENTRY|subordinate_de|none": ("FURN_588A31", "LATIN"),
    "DOCUMENT_INDEX_ENTRY|subordinate_de|plain": ("FURN_588A31", "LATIN"),
    "DOCUMENT_INDEX_ENTRY|subordinate_de|unique": ("FURN_588A31", "LATIN"),
    "FOOTER_FURNITURE|date_line|ambiguous": ("FURN_31DCB3", "LATIN"),
    "FOOTER_FURNITURE|date_line|none": ("FURN_31DCB3", "LATIN"),
    "FOOTER_FURNITURE|date_line|plain": ("FURN_31DCB3", "LATIN"),
    "FOOTER_FURNITURE|date_line|unique": ("FURN_31DCB3", "LATIN"),
    "FOOTER_FURNITURE|deontic_ar|ambiguous": ("FURN_31DCB3", "ARABIC"),
    "FOOTER_FURNITURE|deontic_ar|none": ("FURN_31DCB3", "ARABIC"),
    "FOOTER_FURNITURE|deontic_ar|plain": ("FURN_31DCB3", "ARABIC"),
    "FOOTER_FURNITURE|deontic_ar|unique": ("FURN_31DCB3", "ARABIC"),
    "FOOTER_FURNITURE|furniture_nav|ambiguous": ("FURN_31DCB3", "LATIN"),
    "FOOTER_FURNITURE|furniture_nav|none": ("FURN_31DCB3", "LATIN"),
    "FOOTER_FURNITURE|furniture_nav|plain": ("FURN_31DCB3", "LATIN"),
    "FOOTER_FURNITURE|furniture_nav|unique": ("FURN_31DCB3", "LATIN"),
    "FOOTER_FURNITURE|operative_ru|ambiguous": ("FURN_31DCB3", "CYRILLIC"),
    "FOOTER_FURNITURE|operative_ru|none": ("FURN_31DCB3", "CYRILLIC"),
    "FOOTER_FURNITURE|operative_ru|plain": ("FURN_31DCB3", "CYRILLIC"),
    "FOOTER_FURNITURE|operative_ru|unique": ("FURN_31DCB3", "CYRILLIC"),
    "FOOTER_FURNITURE|recital_it|ambiguous": ("FURN_31DCB3", "LATIN"),
    "FOOTER_FURNITURE|recital_it|none": ("FURN_31DCB3", "LATIN"),
    "FOOTER_FURNITURE|recital_it|plain": ("FURN_31DCB3", "LATIN"),
    "FOOTER_FURNITURE|recital_it|unique": ("FURN_31DCB3", "LATIN"),
    "FOOTER_FURNITURE|subordinate_de|ambiguous": ("FURN_31DCB3", "LATIN"),
    "FOOTER_FURNITURE|subordinate_de|none": ("FURN_31DCB3", "LATIN"),
    "FOOTER_FURNITURE|subordinate_de|plain": ("FURN_31DCB3", "LATIN"),
    "FOOTER_FURNITURE|subordinate_de|unique": ("FURN_31DCB3", "LATIN"),
    "GENERIC_LINK_LABEL|date_line|ambiguous": ("FURN_9823D1", "LATIN"),
    "GENERIC_LINK_LABEL|date_line|none": ("FURN_9823D1", "LATIN"),
    "GENERIC_LINK_LABEL|date_line|plain": ("FURN_9823D1", "LATIN"),
    "GENERIC_LINK_LABEL|date_line|unique": ("FURN_9823D1", "LATIN"),
    "GENERIC_LINK_LABEL|deontic_ar|ambiguous": ("FURN_9823D1", "ARABIC"),
    "GENERIC_LINK_LABEL|deontic_ar|none": ("FURN_9823D1", "ARABIC"),
    "GENERIC_LINK_LABEL|deontic_ar|plain": ("FURN_9823D1", "ARABIC"),
    "GENERIC_LINK_LABEL|deontic_ar|unique": ("FURN_9823D1", "ARABIC"),
    "GENERIC_LINK_LABEL|furniture_nav|ambiguous": ("FURN_9823D1", "LATIN"),
    "GENERIC_LINK_LABEL|furniture_nav|none": ("FURN_9823D1", "LATIN"),
    "GENERIC_LINK_LABEL|furniture_nav|plain": ("FURN_9823D1", "LATIN"),
    "GENERIC_LINK_LABEL|furniture_nav|unique": ("FURN_9823D1", "LATIN"),
    "GENERIC_LINK_LABEL|operative_ru|ambiguous": ("FURN_9823D1", "CYRILLIC"),
    "GENERIC_LINK_LABEL|operative_ru|none": ("FURN_9823D1", "CYRILLIC"),
    "GENERIC_LINK_LABEL|operative_ru|plain": ("FURN_9823D1", "CYRILLIC"),
    "GENERIC_LINK_LABEL|operative_ru|unique": ("FURN_9823D1", "CYRILLIC"),
    "GENERIC_LINK_LABEL|recital_it|ambiguous": ("FURN_9823D1", "LATIN"),
    "GENERIC_LINK_LABEL|recital_it|none": ("FURN_9823D1", "LATIN"),
    "GENERIC_LINK_LABEL|recital_it|plain": ("FURN_9823D1", "LATIN"),
    "GENERIC_LINK_LABEL|recital_it|unique": ("FURN_9823D1", "LATIN"),
    "GENERIC_LINK_LABEL|subordinate_de|ambiguous": ("FURN_9823D1", "LATIN"),
    "GENERIC_LINK_LABEL|subordinate_de|none": ("FURN_9823D1", "LATIN"),
    "GENERIC_LINK_LABEL|subordinate_de|plain": ("FURN_9823D1", "LATIN"),
    "GENERIC_LINK_LABEL|subordinate_de|unique": ("FURN_9823D1", "LATIN"),
    "HEADER_FURNITURE|date_line|ambiguous": ("FURN_C17B43", "LATIN"),
    "HEADER_FURNITURE|date_line|none": ("FURN_C17B43", "LATIN"),
    "HEADER_FURNITURE|date_line|plain": ("FURN_C17B43", "LATIN"),
    "HEADER_FURNITURE|date_line|unique": ("FURN_C17B43", "LATIN"),
    "HEADER_FURNITURE|deontic_ar|ambiguous": ("FURN_C17B43", "ARABIC"),
    "HEADER_FURNITURE|deontic_ar|none": ("FURN_C17B43", "ARABIC"),
    "HEADER_FURNITURE|deontic_ar|plain": ("FURN_C17B43", "ARABIC"),
    "HEADER_FURNITURE|deontic_ar|unique": ("FURN_C17B43", "ARABIC"),
    "HEADER_FURNITURE|furniture_nav|ambiguous": ("FURN_C17B43", "LATIN"),
    "HEADER_FURNITURE|furniture_nav|none": ("FURN_C17B43", "LATIN"),
    "HEADER_FURNITURE|furniture_nav|plain": ("FURN_C17B43", "LATIN"),
    "HEADER_FURNITURE|furniture_nav|unique": ("FURN_C17B43", "LATIN"),
    "HEADER_FURNITURE|operative_ru|ambiguous": ("FURN_C17B43", "CYRILLIC"),
    "HEADER_FURNITURE|operative_ru|none": ("FURN_C17B43", "CYRILLIC"),
    "HEADER_FURNITURE|operative_ru|plain": ("FURN_C17B43", "CYRILLIC"),
    "HEADER_FURNITURE|operative_ru|unique": ("FURN_C17B43", "CYRILLIC"),
    "HEADER_FURNITURE|recital_it|ambiguous": ("FURN_C17B43", "LATIN"),
    "HEADER_FURNITURE|recital_it|none": ("FURN_C17B43", "LATIN"),
    "HEADER_FURNITURE|recital_it|plain": ("FURN_C17B43", "LATIN"),
    "HEADER_FURNITURE|recital_it|unique": ("FURN_C17B43", "LATIN"),
    "HEADER_FURNITURE|subordinate_de|ambiguous": ("FURN_C17B43", "LATIN"),
    "HEADER_FURNITURE|subordinate_de|none": ("FURN_C17B43", "LATIN"),
    "HEADER_FURNITURE|subordinate_de|plain": ("FURN_C17B43", "LATIN"),
    "HEADER_FURNITURE|subordinate_de|unique": ("FURN_C17B43", "LATIN"),
    "HEADING_CONTEXT_ONLY|date_line|ambiguous": ("FURN_9B7B75", "LATIN"),
    "HEADING_CONTEXT_ONLY|date_line|none": ("FURN_9B7B75", "LATIN"),
    "HEADING_CONTEXT_ONLY|date_line|plain": ("FURN_9B7B75", "LATIN"),
    "HEADING_CONTEXT_ONLY|date_line|unique": ("FURN_9B7B75", "LATIN"),
    "HEADING_CONTEXT_ONLY|deontic_ar|ambiguous": ("FURN_9B7B75", "ARABIC"),
    "HEADING_CONTEXT_ONLY|deontic_ar|none": ("FURN_9B7B75", "ARABIC"),
    "HEADING_CONTEXT_ONLY|deontic_ar|plain": ("FURN_9B7B75", "ARABIC"),
    "HEADING_CONTEXT_ONLY|deontic_ar|unique": ("FURN_9B7B75", "ARABIC"),
    "HEADING_CONTEXT_ONLY|furniture_nav|ambiguous": ("FURN_9B7B75", "LATIN"),
    "HEADING_CONTEXT_ONLY|furniture_nav|none": ("FURN_9B7B75", "LATIN"),
    "HEADING_CONTEXT_ONLY|furniture_nav|plain": ("FURN_9B7B75", "LATIN"),
    "HEADING_CONTEXT_ONLY|furniture_nav|unique": ("FURN_9B7B75", "LATIN"),
    "HEADING_CONTEXT_ONLY|operative_ru|ambiguous": ("FURN_9B7B75", "CYRILLIC"),
    "HEADING_CONTEXT_ONLY|operative_ru|none": ("FURN_9B7B75", "CYRILLIC"),
    "HEADING_CONTEXT_ONLY|operative_ru|plain": ("FURN_9B7B75", "CYRILLIC"),
    "HEADING_CONTEXT_ONLY|operative_ru|unique": ("FURN_9B7B75", "CYRILLIC"),
    "HEADING_CONTEXT_ONLY|recital_it|ambiguous": ("FURN_9B7B75", "LATIN"),
    "HEADING_CONTEXT_ONLY|recital_it|none": ("FURN_9B7B75", "LATIN"),
    "HEADING_CONTEXT_ONLY|recital_it|plain": ("FURN_9B7B75", "LATIN"),
    "HEADING_CONTEXT_ONLY|recital_it|unique": ("FURN_9B7B75", "LATIN"),
    "HEADING_CONTEXT_ONLY|subordinate_de|ambiguous": ("FURN_9B7B75", "LATIN"),
    "HEADING_CONTEXT_ONLY|subordinate_de|none": ("FURN_9B7B75", "LATIN"),
    "HEADING_CONTEXT_ONLY|subordinate_de|plain": ("FURN_9B7B75", "LATIN"),
    "HEADING_CONTEXT_ONLY|subordinate_de|unique": ("FURN_9B7B75", "LATIN"),
    "LANGUAGE_SELECTOR|date_line|ambiguous": ("NOPRED_8DDD04", "LATIN"),
    "LANGUAGE_SELECTOR|date_line|none": ("NOPRED_2FF212", "LATIN"),
    "LANGUAGE_SELECTOR|date_line|plain": ("NOPRED_8A7A98", "LATIN"),
    "LANGUAGE_SELECTOR|date_line|unique": ("NOPRED_B8A288", "LATIN"),
    "LANGUAGE_SELECTOR|deontic_ar|ambiguous": ("M2_D89E65", "ARABIC"),
    "LANGUAGE_SELECTOR|deontic_ar|none": ("M2_D89E65", "ARABIC"),
    "LANGUAGE_SELECTOR|deontic_ar|plain": ("M2_D89E65", "ARABIC"),
    "LANGUAGE_SELECTOR|deontic_ar|unique": ("M2_2503F1", "ARABIC"),
    "LANGUAGE_SELECTOR|furniture_nav|ambiguous": ("NOPRED_019FED", "LATIN"),
    "LANGUAGE_SELECTOR|furniture_nav|none": ("NOPRED_9778EE", "LATIN"),
    "LANGUAGE_SELECTOR|furniture_nav|plain": ("NOPRED_9E5AF1", "LATIN"),
    "LANGUAGE_SELECTOR|furniture_nav|unique": ("NOPRED_6AF56F", "LATIN"),
    "LANGUAGE_SELECTOR|operative_ru|ambiguous": ("M2_2503F1", "CYRILLIC"),
    "LANGUAGE_SELECTOR|operative_ru|none": ("M2_7A6AEC", "CYRILLIC"),
    "LANGUAGE_SELECTOR|operative_ru|plain": ("M2_2503F1", "CYRILLIC"),
    "LANGUAGE_SELECTOR|operative_ru|unique": ("M2_2503F1", "CYRILLIC"),
    "LANGUAGE_SELECTOR|recital_it|ambiguous": ("AMB_E40EE9", "LATIN"),
    "LANGUAGE_SELECTOR|recital_it|none": ("GOVU_D93160", "LATIN"),
    "LANGUAGE_SELECTOR|recital_it|plain": ("GOVU_080546", "LATIN"),
    "LANGUAGE_SELECTOR|recital_it|unique": ("GOV_385D52", "LATIN"),
    "LANGUAGE_SELECTOR|subordinate_de|ambiguous": ("M2_2F8FB4", "LATIN"),
    "LANGUAGE_SELECTOR|subordinate_de|none": ("M2_2F8FB4", "LATIN"),
    "LANGUAGE_SELECTOR|subordinate_de|plain": ("M2_2F8FB4", "LATIN"),
    "LANGUAGE_SELECTOR|subordinate_de|unique": ("M2_2F8FB4", "LATIN"),
    "NAVIGATION_MENU|date_line|ambiguous": ("FURN_2C03C6", "LATIN"),
    "NAVIGATION_MENU|date_line|none": ("FURN_2C03C6", "LATIN"),
    "NAVIGATION_MENU|date_line|plain": ("FURN_2C03C6", "LATIN"),
    "NAVIGATION_MENU|date_line|unique": ("FURN_2C03C6", "LATIN"),
    "NAVIGATION_MENU|deontic_ar|ambiguous": ("FURN_2C03C6", "ARABIC"),
    "NAVIGATION_MENU|deontic_ar|none": ("FURN_2C03C6", "ARABIC"),
    "NAVIGATION_MENU|deontic_ar|plain": ("FURN_2C03C6", "ARABIC"),
    "NAVIGATION_MENU|deontic_ar|unique": ("FURN_2C03C6", "ARABIC"),
    "NAVIGATION_MENU|furniture_nav|ambiguous": ("FURN_2C03C6", "LATIN"),
    "NAVIGATION_MENU|furniture_nav|none": ("FURN_2C03C6", "LATIN"),
    "NAVIGATION_MENU|furniture_nav|plain": ("FURN_2C03C6", "LATIN"),
    "NAVIGATION_MENU|furniture_nav|unique": ("FURN_2C03C6", "LATIN"),
    "NAVIGATION_MENU|operative_ru|ambiguous": ("FURN_2C03C6", "CYRILLIC"),
    "NAVIGATION_MENU|operative_ru|none": ("FURN_2C03C6", "CYRILLIC"),
    "NAVIGATION_MENU|operative_ru|plain": ("FURN_2C03C6", "CYRILLIC"),
    "NAVIGATION_MENU|operative_ru|unique": ("FURN_2C03C6", "CYRILLIC"),
    "NAVIGATION_MENU|recital_it|ambiguous": ("FURN_2C03C6", "LATIN"),
    "NAVIGATION_MENU|recital_it|none": ("FURN_2C03C6", "LATIN"),
    "NAVIGATION_MENU|recital_it|plain": ("FURN_2C03C6", "LATIN"),
    "NAVIGATION_MENU|recital_it|unique": ("FURN_2C03C6", "LATIN"),
    "NAVIGATION_MENU|subordinate_de|ambiguous": ("FURN_2C03C6", "LATIN"),
    "NAVIGATION_MENU|subordinate_de|none": ("FURN_2C03C6", "LATIN"),
    "NAVIGATION_MENU|subordinate_de|plain": ("FURN_2C03C6", "LATIN"),
    "NAVIGATION_MENU|subordinate_de|unique": ("FURN_2C03C6", "LATIN"),
    "PAGINATION_CONTROL|date_line|ambiguous": ("NOPRED_8DDD04", "LATIN"),
    "PAGINATION_CONTROL|date_line|none": ("NOPRED_2FF212", "LATIN"),
    "PAGINATION_CONTROL|date_line|plain": ("NOPRED_8A7A98", "LATIN"),
    "PAGINATION_CONTROL|date_line|unique": ("NOPRED_B8A288", "LATIN"),
    "PAGINATION_CONTROL|deontic_ar|ambiguous": ("M2_D89E65", "ARABIC"),
    "PAGINATION_CONTROL|deontic_ar|none": ("M2_D89E65", "ARABIC"),
    "PAGINATION_CONTROL|deontic_ar|plain": ("M2_D89E65", "ARABIC"),
    "PAGINATION_CONTROL|deontic_ar|unique": ("M2_2503F1", "ARABIC"),
    "PAGINATION_CONTROL|furniture_nav|ambiguous": ("NOPRED_019FED", "LATIN"),
    "PAGINATION_CONTROL|furniture_nav|none": ("NOPRED_9778EE", "LATIN"),
    "PAGINATION_CONTROL|furniture_nav|plain": ("NOPRED_9E5AF1", "LATIN"),
    "PAGINATION_CONTROL|furniture_nav|unique": ("NOPRED_6AF56F", "LATIN"),
    "PAGINATION_CONTROL|operative_ru|ambiguous": ("M2_2503F1", "CYRILLIC"),
    "PAGINATION_CONTROL|operative_ru|none": ("M2_7A6AEC", "CYRILLIC"),
    "PAGINATION_CONTROL|operative_ru|plain": ("M2_2503F1", "CYRILLIC"),
    "PAGINATION_CONTROL|operative_ru|unique": ("M2_2503F1", "CYRILLIC"),
    "PAGINATION_CONTROL|recital_it|ambiguous": ("AMB_E40EE9", "LATIN"),
    "PAGINATION_CONTROL|recital_it|none": ("GOVU_D93160", "LATIN"),
    "PAGINATION_CONTROL|recital_it|plain": ("GOVU_080546", "LATIN"),
    "PAGINATION_CONTROL|recital_it|unique": ("GOV_385D52", "LATIN"),
    "PAGINATION_CONTROL|subordinate_de|ambiguous": ("M2_2F8FB4", "LATIN"),
    "PAGINATION_CONTROL|subordinate_de|none": ("M2_2F8FB4", "LATIN"),
    "PAGINATION_CONTROL|subordinate_de|plain": ("M2_2F8FB4", "LATIN"),
    "PAGINATION_CONTROL|subordinate_de|unique": ("M2_2F8FB4", "LATIN"),
    "PRIMARY_PROPOSITION_CONTENT|date_line|ambiguous":
        ("NOPRED_8DDD04", "LATIN"),
    "PRIMARY_PROPOSITION_CONTENT|date_line|none": ("NOPRED_2FF212", "LATIN"),
    "PRIMARY_PROPOSITION_CONTENT|date_line|plain": ("NOPRED_8A7A98", "LATIN"),
    "PRIMARY_PROPOSITION_CONTENT|date_line|unique": ("NOPRED_B8A288", "LATIN"),
    "PRIMARY_PROPOSITION_CONTENT|deontic_ar|ambiguous":
        ("M2_D89E65", "ARABIC"),
    "PRIMARY_PROPOSITION_CONTENT|deontic_ar|none": ("M2_D89E65", "ARABIC"),
    "PRIMARY_PROPOSITION_CONTENT|deontic_ar|plain": ("M2_D89E65", "ARABIC"),
    "PRIMARY_PROPOSITION_CONTENT|deontic_ar|unique": ("M2_2503F1", "ARABIC"),
    "PRIMARY_PROPOSITION_CONTENT|furniture_nav|ambiguous":
        ("NOPRED_019FED", "LATIN"),
    "PRIMARY_PROPOSITION_CONTENT|furniture_nav|none":
        ("NOPRED_9778EE", "LATIN"),
    "PRIMARY_PROPOSITION_CONTENT|furniture_nav|plain":
        ("NOPRED_9E5AF1", "LATIN"),
    "PRIMARY_PROPOSITION_CONTENT|furniture_nav|unique":
        ("NOPRED_6AF56F", "LATIN"),
    "PRIMARY_PROPOSITION_CONTENT|operative_ru|ambiguous":
        ("M2_2503F1", "CYRILLIC"),
    "PRIMARY_PROPOSITION_CONTENT|operative_ru|none": ("M2_7A6AEC", "CYRILLIC"),
    "PRIMARY_PROPOSITION_CONTENT|operative_ru|plain":
        ("M2_2503F1", "CYRILLIC"),
    "PRIMARY_PROPOSITION_CONTENT|operative_ru|unique":
        ("M2_2503F1", "CYRILLIC"),
    "PRIMARY_PROPOSITION_CONTENT|recital_it|ambiguous":
        ("AMB_E40EE9", "LATIN"),
    "PRIMARY_PROPOSITION_CONTENT|recital_it|none": ("GOVU_D93160", "LATIN"),
    "PRIMARY_PROPOSITION_CONTENT|recital_it|plain": ("GOVU_080546", "LATIN"),
    "PRIMARY_PROPOSITION_CONTENT|recital_it|unique": ("GOV_385D52", "LATIN"),
    "PRIMARY_PROPOSITION_CONTENT|subordinate_de|ambiguous":
        ("M2_2F8FB4", "LATIN"),
    "PRIMARY_PROPOSITION_CONTENT|subordinate_de|none": ("M2_2F8FB4", "LATIN"),
    "PRIMARY_PROPOSITION_CONTENT|subordinate_de|plain": ("M2_2F8FB4", "LATIN"),
    "PRIMARY_PROPOSITION_CONTENT|subordinate_de|unique":
        ("M2_2F8FB4", "LATIN"),
    "SEARCH_CONTROL|date_line|ambiguous": ("NOPRED_8DDD04", "LATIN"),
    "SEARCH_CONTROL|date_line|none": ("NOPRED_2FF212", "LATIN"),
    "SEARCH_CONTROL|date_line|plain": ("NOPRED_8A7A98", "LATIN"),
    "SEARCH_CONTROL|date_line|unique": ("NOPRED_B8A288", "LATIN"),
    "SEARCH_CONTROL|deontic_ar|ambiguous": ("M2_D89E65", "ARABIC"),
    "SEARCH_CONTROL|deontic_ar|none": ("M2_D89E65", "ARABIC"),
    "SEARCH_CONTROL|deontic_ar|plain": ("M2_D89E65", "ARABIC"),
    "SEARCH_CONTROL|deontic_ar|unique": ("M2_2503F1", "ARABIC"),
    "SEARCH_CONTROL|furniture_nav|ambiguous": ("NOPRED_019FED", "LATIN"),
    "SEARCH_CONTROL|furniture_nav|none": ("NOPRED_9778EE", "LATIN"),
    "SEARCH_CONTROL|furniture_nav|plain": ("NOPRED_9E5AF1", "LATIN"),
    "SEARCH_CONTROL|furniture_nav|unique": ("NOPRED_6AF56F", "LATIN"),
    "SEARCH_CONTROL|operative_ru|ambiguous": ("M2_2503F1", "CYRILLIC"),
    "SEARCH_CONTROL|operative_ru|none": ("M2_7A6AEC", "CYRILLIC"),
    "SEARCH_CONTROL|operative_ru|plain": ("M2_2503F1", "CYRILLIC"),
    "SEARCH_CONTROL|operative_ru|unique": ("M2_2503F1", "CYRILLIC"),
    "SEARCH_CONTROL|recital_it|ambiguous": ("AMB_E40EE9", "LATIN"),
    "SEARCH_CONTROL|recital_it|none": ("GOVU_D93160", "LATIN"),
    "SEARCH_CONTROL|recital_it|plain": ("GOVU_080546", "LATIN"),
    "SEARCH_CONTROL|recital_it|unique": ("GOV_385D52", "LATIN"),
    "SEARCH_CONTROL|subordinate_de|ambiguous": ("M2_2F8FB4", "LATIN"),
    "SEARCH_CONTROL|subordinate_de|none": ("M2_2F8FB4", "LATIN"),
    "SEARCH_CONTROL|subordinate_de|plain": ("M2_2F8FB4", "LATIN"),
    "SEARCH_CONTROL|subordinate_de|unique": ("M2_2F8FB4", "LATIN"),
    "SITE_SLOGAN|date_line|ambiguous": ("NOPRED_8DDD04", "LATIN"),
    "SITE_SLOGAN|date_line|none": ("NOPRED_2FF212", "LATIN"),
    "SITE_SLOGAN|date_line|plain": ("NOPRED_8A7A98", "LATIN"),
    "SITE_SLOGAN|date_line|unique": ("NOPRED_B8A288", "LATIN"),
    "SITE_SLOGAN|deontic_ar|ambiguous": ("M2_D89E65", "ARABIC"),
    "SITE_SLOGAN|deontic_ar|none": ("M2_D89E65", "ARABIC"),
    "SITE_SLOGAN|deontic_ar|plain": ("M2_D89E65", "ARABIC"),
    "SITE_SLOGAN|deontic_ar|unique": ("M2_2503F1", "ARABIC"),
    "SITE_SLOGAN|furniture_nav|ambiguous": ("NOPRED_019FED", "LATIN"),
    "SITE_SLOGAN|furniture_nav|none": ("NOPRED_9778EE", "LATIN"),
    "SITE_SLOGAN|furniture_nav|plain": ("NOPRED_9E5AF1", "LATIN"),
    "SITE_SLOGAN|furniture_nav|unique": ("NOPRED_6AF56F", "LATIN"),
    "SITE_SLOGAN|operative_ru|ambiguous": ("M2_2503F1", "CYRILLIC"),
    "SITE_SLOGAN|operative_ru|none": ("M2_7A6AEC", "CYRILLIC"),
    "SITE_SLOGAN|operative_ru|plain": ("M2_2503F1", "CYRILLIC"),
    "SITE_SLOGAN|operative_ru|unique": ("M2_2503F1", "CYRILLIC"),
    "SITE_SLOGAN|recital_it|ambiguous": ("AMB_E40EE9", "LATIN"),
    "SITE_SLOGAN|recital_it|none": ("GOVU_D93160", "LATIN"),
    "SITE_SLOGAN|recital_it|plain": ("GOVU_080546", "LATIN"),
    "SITE_SLOGAN|recital_it|unique": ("GOV_385D52", "LATIN"),
    "SITE_SLOGAN|subordinate_de|ambiguous": ("M2_2F8FB4", "LATIN"),
    "SITE_SLOGAN|subordinate_de|none": ("M2_2F8FB4", "LATIN"),
    "SITE_SLOGAN|subordinate_de|plain": ("M2_2F8FB4", "LATIN"),
    "SITE_SLOGAN|subordinate_de|unique": ("M2_2F8FB4", "LATIN"),
    "TABLE_OF_CONTENTS_ENTRY|date_line|ambiguous": ("NOPRED_8DDD04", "LATIN"),
    "TABLE_OF_CONTENTS_ENTRY|date_line|none": ("NOPRED_2FF212", "LATIN"),
    "TABLE_OF_CONTENTS_ENTRY|date_line|plain": ("NOPRED_8A7A98", "LATIN"),
    "TABLE_OF_CONTENTS_ENTRY|date_line|unique": ("NOPRED_B8A288", "LATIN"),
    "TABLE_OF_CONTENTS_ENTRY|deontic_ar|ambiguous": ("M2_D89E65", "ARABIC"),
    "TABLE_OF_CONTENTS_ENTRY|deontic_ar|none": ("M2_D89E65", "ARABIC"),
    "TABLE_OF_CONTENTS_ENTRY|deontic_ar|plain": ("M2_D89E65", "ARABIC"),
    "TABLE_OF_CONTENTS_ENTRY|deontic_ar|unique": ("M2_2503F1", "ARABIC"),
    "TABLE_OF_CONTENTS_ENTRY|furniture_nav|ambiguous":
        ("NOPRED_019FED", "LATIN"),
    "TABLE_OF_CONTENTS_ENTRY|furniture_nav|none": ("NOPRED_9778EE", "LATIN"),
    "TABLE_OF_CONTENTS_ENTRY|furniture_nav|plain": ("NOPRED_9E5AF1", "LATIN"),
    "TABLE_OF_CONTENTS_ENTRY|furniture_nav|unique": ("NOPRED_6AF56F", "LATIN"),
    "TABLE_OF_CONTENTS_ENTRY|operative_ru|ambiguous":
        ("M2_2503F1", "CYRILLIC"),
    "TABLE_OF_CONTENTS_ENTRY|operative_ru|none": ("M2_7A6AEC", "CYRILLIC"),
    "TABLE_OF_CONTENTS_ENTRY|operative_ru|plain": ("M2_2503F1", "CYRILLIC"),
    "TABLE_OF_CONTENTS_ENTRY|operative_ru|unique": ("M2_2503F1", "CYRILLIC"),
    "TABLE_OF_CONTENTS_ENTRY|recital_it|ambiguous": ("AMB_E40EE9", "LATIN"),
    "TABLE_OF_CONTENTS_ENTRY|recital_it|none": ("GOVU_D93160", "LATIN"),
    "TABLE_OF_CONTENTS_ENTRY|recital_it|plain": ("GOVU_080546", "LATIN"),
    "TABLE_OF_CONTENTS_ENTRY|recital_it|unique": ("GOV_385D52", "LATIN"),
    "TABLE_OF_CONTENTS_ENTRY|subordinate_de|ambiguous": ("M2_2F8FB4", "LATIN"),
    "TABLE_OF_CONTENTS_ENTRY|subordinate_de|none": ("M2_2F8FB4", "LATIN"),
    "TABLE_OF_CONTENTS_ENTRY|subordinate_de|plain": ("M2_2F8FB4", "LATIN"),
    "TABLE_OF_CONTENTS_ENTRY|subordinate_de|unique": ("M2_2F8FB4", "LATIN"),
    "TABLE_OR_STRUCTURED_PROPOSITION|date_line|ambiguous":
        ("NOPRED_8DDD04", "LATIN"),
    "TABLE_OR_STRUCTURED_PROPOSITION|date_line|none":
        ("NOPRED_2FF212", "LATIN"),
    "TABLE_OR_STRUCTURED_PROPOSITION|date_line|plain":
        ("NOPRED_8A7A98", "LATIN"),
    "TABLE_OR_STRUCTURED_PROPOSITION|date_line|unique":
        ("NOPRED_B8A288", "LATIN"),
    "TABLE_OR_STRUCTURED_PROPOSITION|deontic_ar|ambiguous":
        ("M2_D89E65", "ARABIC"),
    "TABLE_OR_STRUCTURED_PROPOSITION|deontic_ar|none": ("M2_D89E65", "ARABIC"),
    "TABLE_OR_STRUCTURED_PROPOSITION|deontic_ar|plain":
        ("M2_D89E65", "ARABIC"),
    "TABLE_OR_STRUCTURED_PROPOSITION|deontic_ar|unique":
        ("M2_2503F1", "ARABIC"),
    "TABLE_OR_STRUCTURED_PROPOSITION|furniture_nav|ambiguous":
        ("NOPRED_019FED", "LATIN"),
    "TABLE_OR_STRUCTURED_PROPOSITION|furniture_nav|none":
        ("NOPRED_9778EE", "LATIN"),
    "TABLE_OR_STRUCTURED_PROPOSITION|furniture_nav|plain":
        ("NOPRED_9E5AF1", "LATIN"),
    "TABLE_OR_STRUCTURED_PROPOSITION|furniture_nav|unique":
        ("NOPRED_6AF56F", "LATIN"),
    "TABLE_OR_STRUCTURED_PROPOSITION|operative_ru|ambiguous":
        ("M2_2503F1", "CYRILLIC"),
    "TABLE_OR_STRUCTURED_PROPOSITION|operative_ru|none":
        ("M2_7A6AEC", "CYRILLIC"),
    "TABLE_OR_STRUCTURED_PROPOSITION|operative_ru|plain":
        ("M2_2503F1", "CYRILLIC"),
    "TABLE_OR_STRUCTURED_PROPOSITION|operative_ru|unique":
        ("M2_2503F1", "CYRILLIC"),
    "TABLE_OR_STRUCTURED_PROPOSITION|recital_it|ambiguous":
        ("AMB_E40EE9", "LATIN"),
    "TABLE_OR_STRUCTURED_PROPOSITION|recital_it|none":
        ("GOVU_D93160", "LATIN"),
    "TABLE_OR_STRUCTURED_PROPOSITION|recital_it|plain":
        ("GOVU_080546", "LATIN"),
    "TABLE_OR_STRUCTURED_PROPOSITION|recital_it|unique":
        ("GOV_385D52", "LATIN"),
    "TABLE_OR_STRUCTURED_PROPOSITION|subordinate_de|ambiguous":
        ("M2_2F8FB4", "LATIN"),
    "TABLE_OR_STRUCTURED_PROPOSITION|subordinate_de|none":
        ("M2_2F8FB4", "LATIN"),
    "TABLE_OR_STRUCTURED_PROPOSITION|subordinate_de|plain":
        ("M2_2F8FB4", "LATIN"),
    "TABLE_OR_STRUCTURED_PROPOSITION|subordinate_de|unique":
        ("M2_2F8FB4", "LATIN"),
    "UNKNOWN_STRUCTURAL_REGION|date_line|ambiguous":
        ("NOPRED_8DDD04", "LATIN"),
    "UNKNOWN_STRUCTURAL_REGION|date_line|none": ("NOPRED_2FF212", "LATIN"),
    "UNKNOWN_STRUCTURAL_REGION|date_line|plain": ("NOPRED_8A7A98", "LATIN"),
    "UNKNOWN_STRUCTURAL_REGION|date_line|unique": ("NOPRED_B8A288", "LATIN"),
    "UNKNOWN_STRUCTURAL_REGION|deontic_ar|ambiguous": ("M2_D89E65", "ARABIC"),
    "UNKNOWN_STRUCTURAL_REGION|deontic_ar|none": ("M2_D89E65", "ARABIC"),
    "UNKNOWN_STRUCTURAL_REGION|deontic_ar|plain": ("M2_D89E65", "ARABIC"),
    "UNKNOWN_STRUCTURAL_REGION|deontic_ar|unique": ("M2_2503F1", "ARABIC"),
    "UNKNOWN_STRUCTURAL_REGION|furniture_nav|ambiguous":
        ("NOPRED_019FED", "LATIN"),
    "UNKNOWN_STRUCTURAL_REGION|furniture_nav|none": ("NOPRED_9778EE", "LATIN"),
    "UNKNOWN_STRUCTURAL_REGION|furniture_nav|plain":
        ("NOPRED_9E5AF1", "LATIN"),
    "UNKNOWN_STRUCTURAL_REGION|furniture_nav|unique":
        ("NOPRED_6AF56F", "LATIN"),
    "UNKNOWN_STRUCTURAL_REGION|operative_ru|ambiguous":
        ("M2_2503F1", "CYRILLIC"),
    "UNKNOWN_STRUCTURAL_REGION|operative_ru|none": ("M2_7A6AEC", "CYRILLIC"),
    "UNKNOWN_STRUCTURAL_REGION|operative_ru|plain": ("M2_2503F1", "CYRILLIC"),
    "UNKNOWN_STRUCTURAL_REGION|operative_ru|unique": ("M2_2503F1", "CYRILLIC"),
    "UNKNOWN_STRUCTURAL_REGION|recital_it|ambiguous": ("AMB_E40EE9", "LATIN"),
    "UNKNOWN_STRUCTURAL_REGION|recital_it|none": ("GOVU_D93160", "LATIN"),
    "UNKNOWN_STRUCTURAL_REGION|recital_it|plain": ("GOVU_080546", "LATIN"),
    "UNKNOWN_STRUCTURAL_REGION|recital_it|unique": ("GOV_385D52", "LATIN"),
    "UNKNOWN_STRUCTURAL_REGION|subordinate_de|ambiguous":
        ("M2_2F8FB4", "LATIN"),
    "UNKNOWN_STRUCTURAL_REGION|subordinate_de|none": ("M2_2F8FB4", "LATIN"),
    "UNKNOWN_STRUCTURAL_REGION|subordinate_de|plain": ("M2_2F8FB4", "LATIN"),
    "UNKNOWN_STRUCTURAL_REGION|subordinate_de|unique": ("M2_2F8FB4", "LATIN"),
}


# ===========================================================================
# INDEPENDENT STATEMENTS OF THE SAME FACTS.  These are written from the
# independent verifier's own measurement record
# (p6_1_verification/amendment_evidence/V_A_CORPUS_REASON_PROFILE.json), not
# from the tables above.
#
# WHAT THEY DEFEND, AND WHAT THEY DO NOT.  MEASURED, NOT ASSERTED.
#
# An earlier draft of this comment claimed that "a table regenerated against a
# drifted substrate disagrees with them instead of quietly redefining the
# population".  The independent verification of P6.1.2 tested that claim
# directly -- mutate production AND regenerate the frozen tables from the
# mutated substrate, recomputing the digest -- and recorded it as finding F1:
# the claim is TRUE in two of the three cases and FALSE in the third.
#
#   * A regeneration that MOVES THE PARTITION -- a unit changing family, a
#     family count changing, a unit entering or leaving the M2 set -- is
#     CAUGHT, by the literal counts and identity sets in this section.
#   * A regeneration that touches an M2 SENTENCE is CAUGHT, because the sibling
#     P6.1.1 file states those twenty sentences as literals of its own and
#     `test_the_m2_sentences_agree_with_the_p611_reference_language` compares
#     them.
#   * A regeneration that appends a constant suffix to the NON-M2 sentences
#     ONLY -- preserving every family label, the distinct-text count and all
#     seven family counts -- is NOT CAUGHT.  The tables, the digest and the
#     whole suite move together.
#
# THE FIGURES, WITH THEIR GENERATION AND ARENA SHAPE NAMED (SEM-6r3-F03).
#
# An earlier version of this comment gave "16 failures", "32 failures" and
# "1544 passed, 9 skipped" with NEITHER coordinate named.  They were the
# P6.1.2-generation figures -- test file `6a1a726c`, corpus-present arena, the
# previous verifier's own three mutants -- and they did not hold on the
# P6.1.3 bytes this file shipped as.  A bare node count with no substrate and
# no arena shape beside it is exactly what went stale, so the corrected form
# below names both, and says which mutant produced it.
#
# Re-measured by the P6.2 implementation
# (`p6_2_implementation/evidence/probes/f1_remeasure.py`, record
# `p6_2_implementation/evidence/F1_REMEASURED.json`) on:
#
#     substrate  roles_v2.py sha256 ac68abb6… (P6.2)
#     subject    ALL operational test files, this file included
#     mutants    three CONSTRUCTED cases defined in that probe -- NOT the
#                P6.1.2 verifier's mutants, so the node counts below are not
#                comparable with the three numbers they replace
#     baseline   corpus-present 1622 passed; corpus-absent 1602 passed,
#                20 skipped
#
#   1. partition-moving regeneration (40 units driven from ambiguity_margin
#      into decisive_lead, then the tables regenerated):
#        corpus-present  1 failed, 1621 passed   -> CAUGHT
#        corpus-absent   1602 passed, 20 skipped -> NOT CAUGHT
#   2. M2-sentence regeneration (a clause appended to the M2 sentences, then
#      the tables regenerated):
#        corpus-present  33 failed, 1589 passed             -> CAUGHT
#        corpus-absent   30 failed, 1572 passed, 20 skipped -> CAUGHT
#   3. non-M2 constant-suffix regeneration (a fixed clause appended to every
#      non-M2 sentence, then the tables regenerated):
#        corpus-present  1622 passed                 -> NOT CAUGHT
#        corpus-absent   1602 passed, 20 skipped     -> NOT CAUGHT
#
# TWO THINGS THAT MEASUREMENT SHOWS AND THE OLD FIGURES DID NOT.
#   * Case 1's protection is CUSTODY-GATED.  The partition counts and identity
#     sets that catch it live in the corpus arms, which SKIP when the frozen
#     population is absent.  In a bare source checkout a partition-moving
#     regeneration is invisible.  The old comment said "CAUGHT" without
#     qualification; the qualification is the arena shape.
#   * Case 3 remains NOT CAUGHT in both shapes on the P6.2 bytes, exactly as
#     recorded before.  The conclusion below is therefore unchanged and is
#     re-confirmed rather than inherited.
#
# So: the non-M2 sentence TEXTS have NO SECOND INDEPENDENT AUTHOR anywhere in
# the test layer.  What defends them is CUSTODY OF THIS FILE'S HASH -- the
# candidate ledger, the noninterference sweep, and review of the diff -- and
# not any assertion in this suite.  That is a declared limitation of the
# frozen-literal design, recorded here rather than papered over; whether the
# non-M2 texts warrant a second independent author is routed to the semantic
# review round and is not decided here.
# ===========================================================================

#: One value over the whole frozen data section, so an accidental edit to any
#: of the 1031 pins is a single visible failure rather than a silent shift.
_FROZEN_TABLE_DIGEST = \
    "d93be2c6e66ea0c93ea4293b4a88a295ccb5947c201603694b15e11abae079c8"

_CORPUS_UNITS = 120
_CORPUS_M2_PATH_UNITS = 22
_CORPUS_NON_M2_UNITS = 98
_CORPUS_NON_M2_DISTINCT_SENTENCES = 54
_EXPECTED_FAMILY_COUNTS = {
    "M2_MAIN_PATH": 22,
    "ambiguity_margin": 40,
    "decisive_lead": 2,
    "furniture": 17,
    "governing_enactment": 22,
    "no_predicate": 8,
    "signal_codes": 9,
}

#: The nine phrases the M2 emission site can produce, restated here as
#: literals.  A sentence containing one of them is an M2-path sentence; a
#: sentence containing none of them was phrased somewhere else.  This is the
#: rule the family column above must obey, and both directions are asserted.
_M2_CLAIM_PHRASES = frozenset({
    "the construction binds no subject",
    "the subject is not resolved",
    "the subject is recoverable from context the caller supplies",
    "the subject is named to context the caller supplies but its recovery "
    "is not proven",
    "the subject is bound",
    "the construction carries no predicate",
    "the predicate is not resolved",
    "the predicate is bound to the declared context",
    "the predicate is bound in span",
})

#: The published `language_or_script` for each declared language of the frozen
#: population, written as literals.  The adapter route's dependence on the
#: CALLER-declared language is pre-existing, has its root cause in `clauses.py`
#: and is pinned (not endorsed) by the sibling P6.1 emission-pin file; it is
#: pinned again here so it cannot move under cover of a reason-only edit.
_SCRIPT_BY_LANGUAGE = {
    "ar": "ARABIC",
    "de": "LATIN",
    "en": "LATIN",
    "es": "LATIN",
    "fr": "LATIN",
    "it": "LATIN",
    "ru": "CYRILLIC",
}


# ===========================================================================
# CONSTRUCTED INPUTS.  Every entry is an INPUT.  The expected output is never
# stated beside it: it is looked up in the frozen tables above by key.
# ===========================================================================

#: The declared-language values swept on every constructed body.  {it, es} are
#: present in the corpus but NEVER reach the M2 path, which is exactly why R2's
#: `language == "it"` drift escaped the P6.1.1 pin; `None` is the undeclared
#: caller.
_LANGUAGES = ("ar", "de", "en", "es", "fr", "it", "ru", None)

#: The four structural-context shapes the binder distinguishes: absent, present
#: but carrying no governor, one distinct governor, two distinct governors.
#: They select different `governing_resolution()` verdicts, hence different
#: appended governing clauses, hence different published sentences.
_CONTEXT_SHAPES = ("none", "plain", "unique", "ambiguous")

_LANGUAGE_AXIS_BODIES = {
    "committee_en": dict(
        text="The Committee shall review the report of the Secretariat.",
        left_context="The Council considered the matter.", heading="",
        content_region_type="PRIMARY_PROPOSITION_CONTENT"),
    "date_line": dict(
        text="A70/12  Agenda item 13.1  22 May 2017",
        left_context="", heading="",
        content_region_type="UNKNOWN_STRUCTURAL_REGION"),
    "decisive_ru": dict(
        text="предотвратимой смертности, что особенно ярко проявилось в ходе "
             "пандемии COVID-19,",
        left_context="кислороду и что отсутствие этого доступа является "
                     "фактором, способствующим",
        heading="", content_region_type="UNKNOWN_STRUCTURAL_REGION"),
    "deontic_ar": dict(
        text="وينبغي علاج الأشخاص المعرّضين لخطر كبير بالأدوية المضادة "
             "للفيروسات في أقرب وقت ممكن.",
        left_context="التماس الرعاية الطبية عند ظهور الأعراض.", heading="",
        content_region_type="PRIMARY_PROPOSITION_CONTENT"),
    "fragment_ru": dict(
        text="но и позволяют добиться жизнестойкости в долгосрочной "
             "перспективе, что способствует",
        left_context="психосоциальной поддержке, которые не только "
                     "удовлетворяют насущные потребности,",
        heading="", content_region_type="UNKNOWN_STRUCTURAL_REGION"),
    "furniture_nav": dict(
        text="Home | About | Contact", left_context="", heading="",
        content_region_type="NAVIGATION_MENU"),
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
    "operative_ru": dict(
        text="Рекомендует государствам-членам представить доклад "
             "Секретариату.",
        left_context="Комитет рассмотрел доклад.", heading="",
        content_region_type="PRIMARY_PROPOSITION_CONTENT"),
    "recital_en": dict(
        text="Recalling resolution WHA75.11 on the health of refugees and "
             "migrants,",
        left_context="WHA76.1", heading="",
        content_region_type="PRIMARY_PROPOSITION_CONTENT"),
    "recital_es": dict(
        text="Recordando que la asignación de recursos financieros debe "
             "acompañarse de un seguimiento de",
        left_context="WHA76.1", heading="",
        content_region_type="PRIMARY_PROPOSITION_CONTENT"),
    "recital_it": dict(
        text="Visto il decreto legislativo 30 dicembre 1992, n. 502, e "
             "successive modificazioni,",
        left_context="IL PRESIDENTE DELLA REPUBBLICA", heading="",
        content_region_type="PRIMARY_PROPOSITION_CONTENT"),
    "recital_ru": dict(
        text="отмечая шесть основных принципов Межучрежденческого постоянного "
             "комитета,",
        left_context="прав человека и основных свобод и обеспечения их "
                     "доступа к защите и помощи;",
        heading="", content_region_type="UNKNOWN_STRUCTURAL_REGION"),
    "subordinate_de": dict(
        text="sie hierzu aus rechtlichen Gründen nicht in der Lage ist;",
        left_context="1.", heading="§ 5",
        content_region_type="PRIMARY_PROPOSITION_CONTENT"),
}

#: The seventeen `content_region_type` values of production's own
#: `regions.ORIGIN_CLASSES`, restated here as a literal so the sweep is a
#: statement this file makes and not a value it inherits.
#: `test_the_region_axis_covers_the_frozen_origin_class_vocabulary` asserts
#: this tuple EQUALS the production vocabulary; that read is for COVERAGE --
#: "which values must be swept" -- and never for an expected output.
_REGION_TYPES_SWEPT = (
    "BREADCRUMB",
    "CAPTION_OR_FIGURE_PROPOSITION",
    "COOKIE_OR_CONSENT_TEXT",
    "DOCUMENT_INDEX_ENTRY",
    "FOOTER_FURNITURE",
    "GENERIC_LINK_LABEL",
    "HEADER_FURNITURE",
    "HEADING_CONTEXT_ONLY",
    "LANGUAGE_SELECTOR",
    "NAVIGATION_MENU",
    "PAGINATION_CONTROL",
    "PRIMARY_PROPOSITION_CONTENT",
    "SEARCH_CONTROL",
    "SITE_SLOGAN",
    "TABLE_OF_CONTENTS_ENTRY",
    "TABLE_OR_STRUCTURED_PROPOSITION",
    "UNKNOWN_STRUCTURAL_REGION",
)

#: The eight values the P6.1.2 surface never visited -- escape class E1's whole
#: content, named so a later shrink of the axis is visible and not merely
#: smaller.  FOOTER_FURNITURE is the one the verifier demonstrated a passing
#: drift on; NAVIGATION_MENU was its matched, caught control.
_E1_PREVIOUSLY_UNVISITED_REGION_TYPES = frozenset({
    "CAPTION_OR_FIGURE_PROPOSITION",
    "COOKIE_OR_CONSENT_TEXT",
    "FOOTER_FURNITURE",
    "LANGUAGE_SELECTOR",
    "PAGINATION_CONTROL",
    "SITE_SLOGAN",
    "TABLE_OF_CONTENTS_ENTRY",
    "TABLE_OR_STRUCTURED_PROPOSITION",
})

#: Six bodies for the region axis, each carrying its OWN declared language, so
#: the axis also spreads over {ar, de, en, it, ru}: a Russian operative
#: sentence, a German subordinate clause, an Arabic deontic sentence, an
#: Italian recital, a short navigational label and a date/session line.
_REGION_AXIS_BODIES = {
    "date_line": dict(
        text="A70/12  Agenda item 13.1  22 May 2017",
        left_context="", heading="", language="en"),
    "deontic_ar": dict(
        text="وينبغي علاج الأشخاص المعرّضين لخطر كبير بالأدوية المضادة "
             "للفيروسات في أقرب وقت ممكن.",
        left_context="التماس الرعاية الطبية عند ظهور الأعراض.", heading="",
        language="ar"),
    "furniture_nav": dict(
        text="Home | About | Contact", left_context="", heading="",
        language="en"),
    "operative_ru": dict(
        text="Рекомендует государствам-членам представить доклад "
             "Секретариату.",
        left_context="Комитет рассмотрел доклад.", heading="", language="ru"),
    "recital_it": dict(
        text="Visto il decreto legislativo 30 dicembre 1992, n. 502, e "
             "successive modificazioni,",
        left_context="IL PRESIDENTE DELLA REPUBBLICA", heading="",
        language="it"),
    "subordinate_de": dict(
        text="sie hierzu aus rechtlichen Gründen nicht in der Lage ist;",
        left_context="1.", heading="§ 5", language="de"),
}

#: The span-length ladder.  A body is `prefix + the first N of these words +
#: the pad + "."`, so an EMITTED predicate span of an exact target length is
#: reproducible from two small integers instead of a 700-character literal.
#: The (words, pad) pair for each target was resolved once by the generator and
#: is frozen in `_SPAN_AXIS_PINS`; nothing is searched for at run time.
_SPAN_LADDER_WORDS = (
    "о мерах принятых для укрепления национальных систем здравоохранения "
    "включая профилактику диагностику лечение и последующее наблюдение "
    "о выделенных ресурсах достигнутых результатах извлеченных уроках "
    "и остающихся пробелах за отчетный период а также о планах "
    "дальнейшей работы на предстоящий двухгодичный период и о мерах "
    "по укреплению потенциала национальных органов здравоохранения "
    "в области профилактики неинфекционных заболеваний и укрепления "
    "первичной медико-санитарной помощи во всех регионах страны "
    "с учетом накопленного опыта и имеющихся ресурсов и потребностей "
    "населения в долгосрочной перспективе устойчивого развития").split()

_SPAN_PAD_CHARACTER = "а"

_SPAN_LADDERS = {
    "gov": dict(
        prefix="отмечая шесть основных принципов Межучрежденческого "
               "постоянного комитета",
        left_context="прав человека и основных свобод и обеспечения их "
                     "доступа к защите и помощи;",
        content_region_type="UNKNOWN_STRUCTURAL_REGION",
        context_shape="unique", language="ru"),
    "m2": dict(
        prefix="Рекомендует государствам-членам представить Секретариату "
               "доклад",
        left_context="Комитет рассмотрел доклад.",
        content_region_type="PRIMARY_PROPOSITION_CONTENT",
        context_shape="none", language="ru"),
    "m2short": dict(
        prefix="Рекомендует представить доклад",
        left_context="Комитет рассмотрел доклад.",
        content_region_type="PRIMARY_PROPOSITION_CONTENT",
        context_shape="none", language="ru"),
}

#: The emitted span lengths the ladder must reach.  50, 200, 300, 517 and 600
#: are the contract's floor; 516 and 518 bracket 517 so an `== 517` clause is
#: told apart from a `>= 517` one; 31 and 700 are the ends.
_REQUIRED_SPAN_LENGTHS = frozenset({
    31, 50, 64, 100, 150, 199, 200, 201, 250, 300, 350, 400, 450, 500,
    516, 517, 518, 550, 600, 650, 700})


def _context_of(shape: str):
    """A structural context of the named shape, built from production types."""
    if shape == "none":
        return None
    base = ST.empty_context(document_id="p612-doc", region_id="p612-region",
                            reading_order_state="READING_ORDER_ESTABLISHED")
    if shape == "plain":
        return base
    if shape == "unique":
        return dataclasses.replace(
            base, governing_clause_candidates=("p612-gov-a",),
            governing_clause_relation_types=("LIST_ITEM_OF",),
            governing_clause_confidences=(0.91,))
    if shape == "ambiguous":
        return dataclasses.replace(
            base, governing_clause_candidates=("p612-gov-a", "p612-gov-b"),
            governing_clause_relation_types=("LIST_ITEM_OF", "LIST_ITEM_OF"),
            governing_clause_confidences=(0.91, 0.88))
    raise ValueError(f"unknown context shape {shape!r}")


def _ladder_body(prefix: str, words: int, pad: int) -> str:
    chosen = list(_SPAN_LADDER_WORDS[:words])
    if pad:
        if not chosen:
            raise ValueError("a pad needs a word to pad")
        chosen[-1] = chosen[-1] + _SPAN_PAD_CHARACTER * pad
    return prefix + (" " + " ".join(chosen) if chosen else "") + "."


@functools.lru_cache(maxsize=None)
def _language_axis_records() -> tuple:
    """Every (body, declared language, context shape) point, bound once."""
    rows = []
    for name in sorted(_LANGUAGE_AXIS_BODIES):
        spec = _LANGUAGE_AXIS_BODIES[name]
        for language in _LANGUAGES:
            for shape in _CONTEXT_SHAPES:
                rows.append((
                    f"{name}|{language}|{shape}",
                    V2.bind(
                        candidate_id=f"p612-lang-{name}-{language}-{shape}",
                        text=spec["text"], language=language,
                        left_context=spec["left_context"],
                        heading=spec["heading"],
                        content_region_type=spec["content_region_type"],
                        structural_context=_context_of(shape))))
    return tuple(rows)


@functools.lru_cache(maxsize=None)
def _region_axis_records() -> tuple:
    """Every (content_region_type, body, context shape) point, bound once."""
    rows = []
    for region in _REGION_TYPES_SWEPT:
        for name in sorted(_REGION_AXIS_BODIES):
            spec = _REGION_AXIS_BODIES[name]
            for shape in _CONTEXT_SHAPES:
                rows.append((
                    f"{region}|{name}|{shape}",
                    V2.bind(
                        candidate_id=f"p613-region-{region}-{name}-{shape}",
                        text=spec["text"], language=spec["language"],
                        left_context=spec["left_context"],
                        heading=spec["heading"],
                        content_region_type=region,
                        structural_context=_context_of(shape))))
    return tuple(rows)


@functools.lru_cache(maxsize=None)
def _span_axis_records() -> tuple:
    """Every frozen span-ladder point, bound once, from its (words, pad)."""
    rows = []
    for key in sorted(_SPAN_AXIS_PINS):
        ladder = key.split("|")[0]
        spec = _SPAN_LADDERS[ladder]
        words, pad, _length, _alias = _SPAN_AXIS_PINS[key]
        body = _ladder_body(spec["prefix"], words, pad)
        rows.append((key, body, V2.bind(
            candidate_id=f"p612-span-{ladder}", text=body,
            language=spec["language"], left_context=spec["left_context"],
            content_region_type=spec["content_region_type"],
            structural_context=_context_of(spec["context_shape"]))))
    return tuple(rows)


# ===========================================================================
# The frozen 120-unit population, reached through production's own route.
# This is the SAME route the sibling P6.1.1 file uses: immutable source bytes
# -> layout.assess(...).ordered_text() for PDFs -> regions.segment(...) ->
# structure.build_structural_context(...) -> roles_v2.bind(...).
# ===========================================================================

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
def _population_units() -> tuple:
    return tuple(sorted(
        (json.loads(line) for line in
         _POPULATION.read_text(encoding="utf-8").splitlines() if line.strip()),
        key=lambda row: row["unit_id"]))


@functools.lru_cache(maxsize=None)
def _corpus_records() -> tuple:
    """Bind every unit of the frozen population through the production route.

    A unit whose region cannot be reproduced is still bound, through the
    unwired call, so the denominator stays at 120.
    """
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
    for unit in _population_units():
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


# ===========================================================================
# The comparison.  `sentences` is a parameter so a vacuity control can hand in
# a PERTURBED table and demonstrate that the pin fails against pristine
# production -- the table is load-bearing, not decorative.
# ===========================================================================


def frozen_reason(alias: str, sentences=None) -> str:
    """The sentence this file says the named alias stands for."""
    table = _SENTENCES if sentences is None else sentences
    return table[alias]


def reason_mismatches(rows, alias_of, sentences=None) -> list:
    """Every (key, published, expected) triple that disagrees, exactly."""
    bad = []
    for key, record in rows:
        expected = frozen_reason(alias_of(key), sentences)
        if record.binding_reason != expected:
            bad.append((key, record.binding_reason, expected))
    return bad


def assert_frozen_reason(record, alias: str, where: str,
                         sentences=None) -> None:
    expected = frozen_reason(alias, sentences)
    assert record.binding_reason == expected, (
        f"{where}: the binder published {record.binding_reason!r}; the frozen "
        f"reference for {alias!r} is {expected!r}")


def family_of_published(reason: str, sentences=None) -> str:
    """The family of a published sentence, by EXACT lookup in the table.

    There is no pattern matching and no default: a sentence this file has never
    seen is not silently absorbed into a family, it raises, and the arm that
    called this fails.
    """
    table = _SENTENCES if sentences is None else sentences
    by_text = {text: alias for alias, text in table.items()}
    alias = by_text[reason]
    return _FAMILY_OF_SENTENCE[alias]


# ===========================================================================
# THE FROZEN TABLES, CHECKED AGAINST THEMSELVES
# ===========================================================================


def test_the_frozen_tables_are_internally_coherent():
    """Shape, totals and referential integrity of the literal data."""
    assert len(_SENTENCES) == 77
    assert len(set(_SENTENCES.values())) == len(_SENTENCES), \
        "two aliases name the same sentence"
    assert all(text.strip() for text in _SENTENCES.values())
    assert set(_FAMILY_OF_SENTENCE) == set(_SENTENCES)
    assert set(_FAMILY_OF_SENTENCE.values()) >= set(_EXPECTED_FAMILY_COUNTS)

    assert len(_CORPUS_REASON) == _CORPUS_UNITS
    assert len(_CORPUS_LANGUAGE) == _CORPUS_UNITS
    assert set(_CORPUS_LANGUAGE) == set(_CORPUS_REASON)
    assert set(_CORPUS_REASON.values()) <= set(_SENTENCES)
    assert set(_CORPUS_LANGUAGE.values()) == set(_SCRIPT_BY_LANGUAGE)

    assert len(_LANGUAGE_AXIS_PINS) == len(_LANGUAGE_AXIS_BODIES) * \
        len(_LANGUAGES) * len(_CONTEXT_SHAPES) == 448
    assert {alias for alias, _script in _LANGUAGE_AXIS_PINS.values()} \
        <= set(_SENTENCES)
    assert {script for _alias, script in _LANGUAGE_AXIS_PINS.values()} \
        <= set(_SCRIPT_BY_LANGUAGE.values())
    assert set(_LANGUAGE_AXIS_PINS) == {
        f"{name}|{language}|{shape}"
        for name in _LANGUAGE_AXIS_BODIES
        for language in _LANGUAGES
        for shape in _CONTEXT_SHAPES}

    assert len(_SPAN_AXIS_PINS) == 55
    assert {key.split("|")[0] for key in _SPAN_AXIS_PINS} == set(_SPAN_LADDERS)
    assert {alias for _w, _p, _n, alias in _SPAN_AXIS_PINS.values()} \
        <= set(_SENTENCES)
    for key, (words, pad, length, _alias) in _SPAN_AXIS_PINS.items():
        assert int(key.split("|")[1]) == length
        assert 0 <= words <= len(_SPAN_LADDER_WORDS)
        assert 0 <= pad <= 11

    assert len(_REGION_AXIS_PINS) == len(_REGION_TYPES_SWEPT) * \
        len(_REGION_AXIS_BODIES) * len(_CONTEXT_SHAPES) == 408
    assert {alias for alias, _script in _REGION_AXIS_PINS.values()} \
        <= set(_SENTENCES)
    assert {script for _alias, script in _REGION_AXIS_PINS.values()} \
        <= set(_SCRIPT_BY_LANGUAGE.values())
    assert set(_REGION_AXIS_PINS) == {
        f"{region}|{name}|{shape}"
        for region in _REGION_TYPES_SWEPT
        for name in _REGION_AXIS_BODIES
        for shape in _CONTEXT_SHAPES}
    assert {spec["language"] for spec in _REGION_AXIS_BODIES.values()} == {
        "ar", "de", "en", "it", "ru"}

    # 1031 pins over 49 sentences: the whole frozen surface, counted once.
    assert (len(_CORPUS_REASON) + len(_LANGUAGE_AXIS_PINS)
            + len(_SPAN_AXIS_PINS) + len(_REGION_AXIS_PINS)) == 1031


def test_the_family_column_obeys_the_m2_claim_phrases():
    """The partition's own rule, asserted in BOTH directions.

    A sentence is an M2-path sentence exactly when it contains one of the nine
    phrases the M2 emission site can produce.  Reading that rule off the
    literal tables -- rather than off production -- means a mislabelled family
    is a failure here and not a silently accepted re-partition.
    """
    for alias, text in sorted(_SENTENCES.items()):
        carries = any(phrase in text for phrase in _M2_CLAIM_PHRASES)
        family = _FAMILY_OF_SENTENCE[alias]
        assert carries == (family == "M2_MAIN_PATH"), (
            f"{alias} is labelled {family!r} but "
            f"{'carries' if carries else 'does not carry'} an M2 claim "
            f"phrase: {text!r}")
    m2_texts = {text for alias, text in _SENTENCES.items()
                if _FAMILY_OF_SENTENCE[alias] == "M2_MAIN_PATH"}
    assert len(m2_texts) == 4, sorted(m2_texts)
    non_m2 = {text for alias, text in _SENTENCES.items()
              if _FAMILY_OF_SENTENCE[alias] != "M2_MAIN_PATH"}
    assert len(non_m2) == 73
    assert m2_texts.isdisjoint(non_m2)


def test_the_m2_sentences_agree_with_the_p611_reference_language():
    """Divergence guard, not an oracle.

    The sibling P6.1.1 file states the twenty sentences the M2 emission site
    can produce, as literals of its own.  The four this population reaches must
    be four of those twenty.  Neither file derives its strings from the other
    or from the module under test.
    """
    spec = importlib.util.spec_from_file_location(
        "_p612_mainpath_pin_readonly", _MAINPATH_PIN_FILE)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert set(module._CLAIM_PHRASES) == set(_M2_CLAIM_PHRASES)
    m2_texts = {text for alias, text in _SENTENCES.items()
                if _FAMILY_OF_SENTENCE[alias] == "M2_MAIN_PATH"}
    assert m2_texts <= set(module._REFERENCE_LANGUAGE), sorted(
        m2_texts - set(module._REFERENCE_LANGUAGE))
    assert set(module._CORPUS_MAIN_PATH_UNITS) == set(_M2_PATH_UNITS)


def test_the_pinned_scripts_agree_with_the_p61_emission_pin_derivation():
    """Divergence guard for `language_or_script`.

    The sibling P6.1 emission-pin file states the adapter route's
    (pre-existing, caller-language dependent) script derivation as a function
    of its own.  The 448 language-axis and 408 region-axis scripts frozen here
    are compared against it.  Two independent statements agreeing is evidence;
    a divergence is a change somebody has to argue.
    """
    spec = importlib.util.spec_from_file_location(
        "_p612_emission_pins_readonly", _EMISSION_PINS_FILE)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    disagreeing = {}
    for key, (_alias, script) in sorted(_LANGUAGE_AXIS_PINS.items()):
        name, language, _shape = key.split("|")
        declared = None if language == "None" else language
        expected = module._expected_adapter_script(
            _LANGUAGE_AXIS_BODIES[name]["text"], declared)
        if script != expected:
            disagreeing[key] = (script, expected)
    for key, (_alias, script) in sorted(_REGION_AXIS_PINS.items()):
        _region, name, _shape = key.split("|")
        expected = module._expected_adapter_script(
            _REGION_AXIS_BODIES[name]["text"],
            _REGION_AXIS_BODIES[name]["language"])
        if script != expected:
            disagreeing[key] = (script, expected)
    assert disagreeing == {}, disagreeing
    assert len(_LANGUAGE_AXIS_PINS) + len(_REGION_AXIS_PINS) == 856


# ===========================================================================
# THE 120-UNIT POPULATION
# ===========================================================================


@needs_corpus
def test_every_corpus_unit_publishes_exactly_the_frozen_reason():
    """R1's whole surface: all 120 units, M2 and non-M2 alike, by equality."""
    records = _corpus_records()
    assert len(records) == _CORPUS_UNITS
    assert {unit_id for unit_id, _ in records} == set(_CORPUS_REASON)
    bad = reason_mismatches(records, _CORPUS_REASON.__getitem__)
    assert bad == [], bad


@needs_corpus
def test_the_corpus_population_partition_is_exactly_as_frozen():
    """Identity, not count.

    A drift that REPLACES a sentence rather than appending to it can leave the
    equality arm above untriggered for a family it abandons; it cannot leave
    this one untriggered, because membership is recomputed from what each unit
    actually published and compared with the frozen partition unit by unit.
    """
    observed = {}
    for unit_id, record in _corpus_records():
        observed.setdefault(
            family_of_published(record.binding_reason), set()).add(unit_id)
    expected = {}
    for unit_id, alias in _CORPUS_REASON.items():
        expected.setdefault(_FAMILY_OF_SENTENCE[alias], set()).add(unit_id)
    assert observed == expected, {
        family: {
            "unexpected": sorted(observed.get(family, set())
                                 - expected.get(family, set())),
            "missing": sorted(expected.get(family, set())
                              - observed.get(family, set()))}
        for family in sorted(set(observed) | set(expected))
        if observed.get(family) != expected.get(family)}
    # And the partition agrees with the independently recorded profile.
    assert {family: len(units) for family, units in observed.items()} == \
        _EXPECTED_FAMILY_COUNTS
    assert observed["M2_MAIN_PATH"] == set(_M2_PATH_UNITS)
    assert len(_M2_PATH_UNITS) == _CORPUS_M2_PATH_UNITS
    assert sum(len(units) for family, units in observed.items()
               if family != "M2_MAIN_PATH") == _CORPUS_NON_M2_UNITS


@needs_corpus
def test_the_corpus_non_m2_sentence_inventory_is_exactly_as_frozen():
    """The 33 distinct non-M2 texts R1 measured, as a set."""
    published = {}
    for unit_id, record in _corpus_records():
        published.setdefault(record.binding_reason, set()).add(unit_id)
    non_m2 = {text for text in published
              if not any(phrase in text for phrase in _M2_CLAIM_PHRASES)}
    assert len(non_m2) == _CORPUS_NON_M2_DISTINCT_SENTENCES, sorted(non_m2)
    frozen_non_m2 = {
        _SENTENCES[alias] for alias in set(_CORPUS_REASON.values())
        if _FAMILY_OF_SENTENCE[alias] != "M2_MAIN_PATH"}
    assert non_m2 == frozen_non_m2, {
        "unexpected": sorted(non_m2 - frozen_non_m2),
        "missing": sorted(frozen_non_m2 - non_m2)}
    # Non-vacuity: no unit publishes an empty reason, so "equality" is not
    # equality with nothing.
    assert "" not in published


@needs_corpus
def test_every_corpus_unit_publishes_the_frozen_language_or_script():
    """The second emitted string field, pinned over the same population."""
    bad = {}
    for unit_id, record in _corpus_records():
        expected = _SCRIPT_BY_LANGUAGE[_CORPUS_LANGUAGE[unit_id]]
        if record.language_or_script != expected:
            bad[unit_id] = (record.language_or_script, expected)
    assert bad == {}, bad


@needs_corpus
def test_the_frozen_corpus_language_column_matches_the_population_file():
    """The frozen language column is the population's own, not an invention."""
    declared = {unit["unit_id"]: unit["language"]
                for unit in _population_units()}
    assert declared == _CORPUS_LANGUAGE
    counts = {language: sum(1 for value in declared.values()
                            if value == language)
              for language in sorted(set(declared.values()))}
    assert counts == {"ar": 16, "de": 14, "en": 7, "es": 12, "fr": 9,
                      "it": 14, "ru": 48}
    # R2's premise, restated as an assertion: Italian and Spanish units exist
    # in the population and none of them is on the M2 path.
    assert {unit_id for unit_id, language in declared.items()
            if language in ("it", "es")} & set(_M2_PATH_UNITS) == set()


# ===========================================================================
# THE LANGUAGE AXIS -- R2
# ===========================================================================


@pytest.mark.parametrize("language", [
    "ar", "de", "en", "es", "fr", "it", "ru", "None"])
def test_the_language_axis_publishes_exactly_the_frozen_reason(language):
    """One arm per declared language, over all 14 bodies and 4 shapes.

    R2's drift was gated on `language == "it"`, a value the M2-path population
    never carries.  Every value the corpus carries, plus the undeclared caller,
    is bound here through the SAME `bind` entry point and its published
    sentence is held to a literal.
    """
    rows = [(key, record) for key, record in _language_axis_records()
            if key.split("|")[1] == language]
    assert len(rows) == len(_LANGUAGE_AXIS_BODIES) * len(_CONTEXT_SHAPES) == 56
    bad = reason_mismatches(
        rows, lambda key: _LANGUAGE_AXIS_PINS[key][0])
    assert bad == [], bad
    script_bad = {
        key: (record.language_or_script, _LANGUAGE_AXIS_PINS[key][1])
        for key, record in rows
        if record.language_or_script != _LANGUAGE_AXIS_PINS[key][1]}
    assert script_bad == {}, script_bad


@pytest.mark.parametrize("body", sorted(_LANGUAGE_AXIS_BODIES))
def test_the_language_axis_publishes_exactly_the_frozen_reason_per_body(body):
    """The same 448 points, sliced the other way, for diagnosability."""
    rows = [(key, record) for key, record in _language_axis_records()
            if key.split("|")[0] == body]
    assert len(rows) == len(_LANGUAGES) * len(_CONTEXT_SHAPES) == 32
    bad = reason_mismatches(rows, lambda key: _LANGUAGE_AXIS_PINS[key][0])
    assert bad == [], bad


def test_the_language_axis_is_not_degenerate():
    """Non-vacuity: the matrix reaches both emission paths and every family.

    An axis on which every point publishes the same sentence would satisfy the
    arms above without measuring anything.  These floors make that a failure,
    and they are what guarantees a language-gated drift has somewhere to be
    caught on the NON-M2 side as well as the M2 side.
    """
    rows = _language_axis_records()
    assert len(rows) == 448
    families = {}
    for key, record in rows:
        families.setdefault(family_of_published(record.binding_reason),
                            set()).add(key)
    assert set(_EXPECTED_FAMILY_COUNTS) <= set(families), sorted(
        set(_EXPECTED_FAMILY_COUNTS) - set(families))
    assert len({record.binding_reason for _key, record in rows}) >= 10
    # Both sides of the M2 boundary, at EVERY declared language value.
    for language in _LANGUAGES:
        token = "None" if language is None else language
        reached = {
            family for family, keys in families.items()
            if any(key.split("|")[1] == token for key in keys)}
        assert "M2_MAIN_PATH" in reached, token
        assert reached - {"M2_MAIN_PATH"}, token
    # The corpus's two never-main-path languages are genuinely present.
    assert {"it", "es"} <= set(_LANGUAGES)
    assert None in _LANGUAGES


# ===========================================================================
# THE SPAN-LENGTH AXIS
# ===========================================================================


@pytest.mark.parametrize("key", sorted(
    _SPAN_AXIS_PINS, key=lambda k: (k.split("|")[0], int(k.split("|")[1]))))
def test_the_span_axis_publishes_exactly_the_frozen_reason(key):
    """One arm per probed emitted span length, on both ladders.

    The arm asserts the SHAPE is reached -- the emitted predicate span really
    is the pinned number of characters -- before it asserts the sentence, so a
    length-keyed clause cannot be missed because the probe silently stopped
    landing on its length.
    """
    _words, _pad, length, alias = _SPAN_AXIS_PINS[key]
    rows = {row_key: record for row_key, _body, record in _span_axis_records()}
    record = rows[key]
    span = record.predicate_span
    assert span is not None, key
    assert span[1] - span[0] == length, (
        f"{key}: the emitted predicate span is {span[1] - span[0]} "
        f"characters, not the pinned {length}")
    assert_frozen_reason(record, alias, where=f"span point {key}")


def test_the_span_ladder_reaches_every_required_length():
    """Non-vacuity and coverage of the length axis.

    The contract's floor is 50/200/300/517/600.  516 and 518 bracket 517 so an
    `== 517` clause is distinguished from a `>= 517` one, and both ladders are
    required to reach the middle of the range so the axis is probed on the M2
    path and on a NON-M2 family.
    """
    reached = {}
    for key, _body, record in _span_axis_records():
        span = record.predicate_span
        assert span is not None, key
        reached.setdefault(key.split("|")[0], set()).add(span[1] - span[0])
    everything = set().union(*reached.values())
    assert _REQUIRED_SPAN_LENGTHS <= everything, sorted(
        _REQUIRED_SPAN_LENGTHS - everything)
    assert {50, 200, 300, 517, 600} <= everything
    assert {516, 517, 518} <= reached["m2"]
    assert {516, 517, 518} <= reached["gov"]
    assert {516, 517, 518} <= reached["m2short"]
    assert len(everything) >= 21
    # The gov ladder is a NON-M2 family, so the length axis is not probed only
    # where P6.1.1 already looks.
    gov_families = {
        family_of_published(record.binding_reason)
        for key, _body, record in _span_axis_records()
        if key.startswith("gov|")}
    assert gov_families == {"governing_enactment"}
    m2_families = {
        family_of_published(record.binding_reason)
        for key, _body, record in _span_axis_records()
        if key.startswith("m2|")}
    assert m2_families == {"M2_MAIN_PATH"}


# ===========================================================================
# THE REGION-TYPE AXIS -- escape class E1
# ===========================================================================


def test_the_region_axis_covers_the_frozen_origin_class_vocabulary():
    """EXHAUSTIVE over production's own `content_region_type` vocabulary.

    `regions.ORIGIN_CLASSES` is read here for COVERAGE and never for an
    expected value: it answers "which values must be swept", not "what must
    they publish".  The comparison is an EQUALITY, so a vocabulary that grows
    fails HERE, loudly, and the new value is then pinned by decision instead of
    being quietly left unvisited -- which is exactly how escape class E1 arose:
    seventeen values existed, nine were visited, and a drift gated on one of
    the other eight passed the whole suite.
    """
    assert set(_REGION_TYPES_SWEPT) == set(RG.ORIGIN_CLASSES), {
        "unswept": sorted(set(RG.ORIGIN_CLASSES) - set(_REGION_TYPES_SWEPT)),
        "not_in_production": sorted(
            set(_REGION_TYPES_SWEPT) - set(RG.ORIGIN_CLASSES))}
    assert len(_REGION_TYPES_SWEPT) == len(set(_REGION_TYPES_SWEPT)) == 17
    assert len(RG.ORIGIN_CLASSES) == 17
    # The eight E1 values, by name, so a later shrink of the axis is visible.
    assert _E1_PREVIOUSLY_UNVISITED_REGION_TYPES <= set(_REGION_TYPES_SWEPT)
    assert len(_E1_PREVIOUSLY_UNVISITED_REGION_TYPES) == 8
    assert "FOOTER_FURNITURE" in _E1_PREVIOUSLY_UNVISITED_REGION_TYPES
    assert "NAVIGATION_MENU" not in _E1_PREVIOUSLY_UNVISITED_REGION_TYPES
    assert {key.split("|")[0] for key in _REGION_AXIS_PINS} == \
        set(_REGION_TYPES_SWEPT)
    # Production's three sub-vocabularies partition the seventeen, so the axis
    # is exhaustive over each of them as well as over the whole.
    assert RG.PROPOSITION_BEARING <= set(_REGION_TYPES_SWEPT)
    assert RG.STRUCTURAL_FURNITURE <= set(_REGION_TYPES_SWEPT)


@pytest.mark.parametrize("region", sorted(_REGION_TYPES_SWEPT))
def test_the_region_axis_publishes_exactly_the_frozen_reason(region):
    """One arm per content_region_type, over all 6 bodies and 4 shapes.

    E1's drift was gated on `content_region_type == "FOOTER_FURNITURE"`, a
    value of production's own vocabulary that the P6.1.2 surface never
    instantiated.  Every value is bound here through the SAME `bind` entry
    point and its published sentence is held to a literal.
    """
    rows = [(key, record) for key, record in _region_axis_records()
            if key.split("|")[0] == region]
    assert len(rows) == len(_REGION_AXIS_BODIES) * len(_CONTEXT_SHAPES) == 24
    bad = reason_mismatches(rows, lambda key: _REGION_AXIS_PINS[key][0])
    assert bad == [], bad
    script_bad = {
        key: (record.language_or_script, _REGION_AXIS_PINS[key][1])
        for key, record in rows
        if record.language_or_script != _REGION_AXIS_PINS[key][1]}
    assert script_bad == {}, script_bad


@pytest.mark.parametrize("body", sorted(_REGION_AXIS_BODIES))
def test_the_region_axis_publishes_exactly_the_frozen_reason_per_body(body):
    """The same 408 points, sliced the other way, for diagnosability."""
    rows = [(key, record) for key, record in _region_axis_records()
            if key.split("|")[1] == body]
    assert len(rows) == len(_REGION_TYPES_SWEPT) * len(_CONTEXT_SHAPES) == 68
    bad = reason_mismatches(rows, lambda key: _REGION_AXIS_PINS[key][0])
    assert bad == [], bad


def test_the_region_axis_is_not_degenerate():
    """Non-vacuity: the region axis discriminates, and reaches both paths.

    An axis on which every point published the same sentence would satisfy the
    arms above without measuring anything.  These floors make that a failure.
    """
    rows = _region_axis_records()
    assert len(rows) == 408
    families = {}
    for key, record in rows:
        families.setdefault(family_of_published(record.binding_reason),
                            set()).add(key)
    # Both sides of the M2 boundary are reached, so a region-gated drift at the
    # emission site has somewhere to be caught on either path.
    assert "M2_MAIN_PATH" in families
    assert set(families) - {"M2_MAIN_PATH"}
    assert len(families) >= 4, sorted(families)
    assert len({record.binding_reason for _key, record in rows}) >= 8
    published = {tuple(key.split("|")): record.binding_reason
                 for key, record in rows}
    # The typing is LOAD-BEARING: for every body and every context shape the
    # seventeen region values do not all publish the same sentence.  An axis on
    # which the value made no difference would be pinning a constant.
    for body in sorted(_REGION_AXIS_BODIES):
        for shape in _CONTEXT_SHAPES:
            distinct = {published[(region, body, shape)]
                        for region in _REGION_TYPES_SWEPT}
            assert len(distinct) >= 2, (body, shape, sorted(distinct))
    # And the six values the BINDER treats as non-proposition regions publish
    # sentences disjoint from the other eleven, at every body and shape.
    for body in sorted(_REGION_AXIS_BODIES):
        for shape in _CONTEXT_SHAPES:
            overridden = {published[(region, body, shape)]
                          for region in _REGION_TYPES_SWEPT
                          if region in V2.STRUCTURAL_NON_PROPOSITION_REGIONS}
            rest = {published[(region, body, shape)]
                    for region in _REGION_TYPES_SWEPT
                    if region not in V2.STRUCTURAL_NON_PROPOSITION_REGIONS}
            assert overridden and rest
            assert overridden.isdisjoint(rest), (body, shape)
    # Every one of the seventeen values publishes something non-empty.
    for region in _REGION_TYPES_SWEPT:
        texts = {published[(region, body, shape)]
                 for body in _REGION_AXIS_BODIES
                 for shape in _CONTEXT_SHAPES}
        assert texts and all(text.strip() for text in texts), region


def test_the_two_furniture_vocabularies_disagree_pre_existing():
    """A measured asymmetry between two production modules.

    Pinned, NOT endorsed.

    `regions.STRUCTURAL_FURNITURE` names TEN region types as furniture proper.
    `roles_v2.STRUCTURAL_NON_PROPOSITION_REGIONS` -- the set the binder
    actually consults when it decides to overwrite the reason with the
    furniture sentence -- names SIX, and the two sets are not nested: six of
    the ten are absent from it, and two of its members are not in the ten at
    all.

    So a span typed BREADCRUMB, COOKIE_OR_CONSENT_TEXT, LANGUAGE_SELECTOR,
    PAGINATION_CONTROL, SEARCH_CONTROL or SITE_SLOGAN is furniture to
    `regions` and an ordinary candidate to `roles_v2`, and is analysed as
    prose.  This test asserts the disagreement EXISTS today; it does not
    endorse it, and repairing it would be a production change outside this
    amendment's authority.  It is recorded so it cannot move unnoticed, and so
    that a reader of the region axis understands why six furniture-named values
    publish analysis sentences rather than the furniture sentence.
    """
    assert len(RG.STRUCTURAL_FURNITURE) == 10
    assert len(V2.STRUCTURAL_NON_PROPOSITION_REGIONS) == 6
    assert V2.STRUCTURAL_NON_PROPOSITION_REGIONS <= set(RG.ORIGIN_CLASSES)
    assert RG.STRUCTURAL_FURNITURE <= set(RG.ORIGIN_CLASSES)
    furniture_not_overridden = (
        RG.STRUCTURAL_FURNITURE - V2.STRUCTURAL_NON_PROPOSITION_REGIONS)
    assert furniture_not_overridden == {
        "BREADCRUMB", "COOKIE_OR_CONSENT_TEXT", "LANGUAGE_SELECTOR",
        "PAGINATION_CONTROL", "SEARCH_CONTROL", "SITE_SLOGAN"}
    overridden_not_furniture = (
        V2.STRUCTURAL_NON_PROPOSITION_REGIONS - RG.STRUCTURAL_FURNITURE)
    assert overridden_not_furniture == {
        "DOCUMENT_INDEX_ENTRY", "HEADING_CONTEXT_ONLY"}
    # The consequence, on emitted records: the six publish a non-furniture
    # sentence for at least one pinned body, which is what makes the asymmetry
    # observable rather than merely declarative.
    published = {tuple(key.split("|")): record.binding_reason
                 for key, record in _region_axis_records()}
    furniture_sentences = {
        published[(region, body, shape)]
        for region in V2.STRUCTURAL_NON_PROPOSITION_REGIONS
        for body in _REGION_AXIS_BODIES for shape in _CONTEXT_SHAPES}
    for region in sorted(furniture_not_overridden):
        theirs = {published[(region, body, shape)]
                  for body in _REGION_AXIS_BODIES
                  for shape in _CONTEXT_SHAPES}
        assert theirs.isdisjoint(furniture_sentences), region


#: Two markers of the SAME length, so a body carrying one and a body carrying
#: the other differ in content and in nothing else.  A published reason that
#: tells them apart is reading caller content.
_CANARY_A = "CANARY-9f2c71"
_CANARY_B = "MARKER-4b1e08"


@pytest.mark.parametrize("region_type", sorted(_REGION_TYPES_SWEPT))
def test_no_caller_supplied_content_reaches_any_published_reason(region_type):
    """A covert channel is a published string that varies with the input.

    The marker rides in the body, the left context and the heading of a long
    span, at every declared language and every context shape, on ALL SEVENTEEN
    content_region_type values.  Two markers of equal length hold the body's
    length and structure fixed, so the two bindings must publish the SAME
    sentence: a difference is content-dependence whatever the equality tables
    happen to say, and it is caught even on families this file does not
    otherwise reach.
    """
    assert len(_CANARY_A) == len(_CANARY_B) and _CANARY_A != _CANARY_B
    for language in _LANGUAGES:
        for shape in _CONTEXT_SHAPES:
            published = {}
            for marker in (_CANARY_A, _CANARY_B):
                body = _ladder_body(
                    _SPAN_LADDERS["m2"]["prefix"] + f" {marker}", 40, 0)
                assert len(body) > 300
                record = V2.bind(
                    candidate_id=f"p612-canary-{language}-{shape}", text=body,
                    language=language,
                    left_context=f"Комитет рассмотрел доклад {marker}.",
                    heading=f"Раздел {marker}",
                    content_region_type=region_type,
                    structural_context=_context_of(shape))
                assert marker not in record.binding_reason, (language, shape)
                assert record.candidate_id not in record.binding_reason
                assert marker not in record.language_or_script
                assert record.binding_reason.strip()
                published[marker] = record.binding_reason
            assert published[_CANARY_A] == published[_CANARY_B], (
                region_type, language, shape, published)


def test_only_the_corpus_arms_are_custody_gated():
    """The skip may cover the population arms and nothing else.

    A skip that spreads is a gate that quietly stops running.  The set of tests
    in THIS file wearing the custody marker is read from the source, so
    widening it fails; and this test is itself NOT gated.
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
    # No other skip, xfail or tolerance mechanism is used anywhere in the file.
    # Read from the syntax tree, so the sentence describing the rule cannot
    # satisfy the rule.
    relaxations = [
        node.attr for node in ast.walk(tree)
        if isinstance(node, ast.Attribute)
        and node.attr in ("skip", "skipif", "xfail", "approx",
                          "importorskip")]
    assert relaxations == ["skipif"], relaxations


def test_the_constructed_axes_are_not_custody_gated():
    """The kills this amendment owes must survive a bare checkout.

    R1's non-M2 mutants, R2's language-gated mutant and E1's region-gated
    mutant are caught by the three CONSTRUCTED axes, which read no artifact.
    If a later edit gated them on custody, the whole amendment would evaporate
    in any tree without the population, so the property is asserted rather than
    assumed.
    """
    tree = ast.parse(_THIS_FILE.read_text(encoding="utf-8"))
    gated = {
        node.name for node in tree.body
        if isinstance(node, ast.FunctionDef)
        and any(isinstance(decorator, ast.Name)
                and decorator.id == "needs_corpus"
                for decorator in node.decorator_list)}
    for name in ("test_the_language_axis_publishes_exactly_the_frozen_reason",
                 "test_the_language_axis_publishes_exactly_the_frozen_"
                 "reason_per_body",
                 "test_the_language_axis_is_not_degenerate",
                 "test_the_span_axis_publishes_exactly_the_frozen_reason",
                 "test_the_span_ladder_reaches_every_required_length",
                 "test_the_region_axis_covers_the_frozen_origin_class_"
                 "vocabulary",
                 "test_the_region_axis_publishes_exactly_the_frozen_reason",
                 "test_the_region_axis_publishes_exactly_the_frozen_"
                 "reason_per_body",
                 "test_the_region_axis_is_not_degenerate",
                 "test_a_region_gated_drift_is_caught_for_every_origin_class",
                 "test_no_caller_supplied_content_reaches_any_published_"
                 "reason"):
        assert name not in gated, name
    # No constructed axis touches the custody artifacts.
    assert _LANGUAGE_AXIS_BODIES and _SPAN_LADDERS and _REGION_AXIS_BODIES


# ===========================================================================
# VACUITY CONTROLS.  Each demonstrates that the pin CAN fail, against
# PRISTINE production, using only the tables in this file.
# ===========================================================================


def _perturbed(alias: str, suffix: str) -> dict:
    """`_SENTENCES` with one entry moved, everything else untouched."""
    table = dict(_SENTENCES)
    table[alias] = table[alias] + suffix
    return table


def test_a_perturbed_reference_entry_fails_against_pristine_production():
    """Control: the frozen table is load-bearing in both directions.

    If a future edit moved a table entry to agree with a drifted production,
    the entry would stop describing what pristine production publishes.  Here
    an entry is perturbed deliberately and the disagreement is asserted -- on
    an M2 sentence and on a non-M2 sentence.
    """
    rows = [(key, record) for key, record in _language_axis_records()]
    assert reason_mismatches(
        rows, lambda key: _LANGUAGE_AXIS_PINS[key][0]) == []
    for family in ("M2_MAIN_PATH", "governing_enactment", "ambiguity_margin",
                   "furniture", "signal_codes", "no_predicate",
                   "decisive_lead"):
        aliases = sorted(
            {alias for alias, _script in _LANGUAGE_AXIS_PINS.values()
             if _FAMILY_OF_SENTENCE[alias] == family})
        assert aliases, family
        perturbed = _perturbed(aliases[0], "; this record is safe to publish")
        bad = reason_mismatches(
            rows, lambda key: _LANGUAGE_AXIS_PINS[key][0],
            sentences=perturbed)
        assert bad, (
            f"perturbing {aliases[0]!r} ({family}) changed nothing: the pin "
            f"does not depend on the frozen table")


def test_a_perturbed_reference_entry_fails_on_the_region_axis_too():
    """The same control on the P6.1.3 axis, family by family.

    A new axis whose pins happened not to depend on the frozen table would add
    node count and no protection.  Every family the region axis reaches is
    perturbed here and the disagreement asserted, against pristine production.
    """
    rows = [(key, record) for key, record in _region_axis_records()]
    assert reason_mismatches(
        rows, lambda key: _REGION_AXIS_PINS[key][0]) == []
    families = {
        _FAMILY_OF_SENTENCE[alias]
        for alias, _script in _REGION_AXIS_PINS.values()}
    assert len(families) >= 4, sorted(families)
    for family in sorted(families):
        aliases = sorted(
            {alias for alias, _script in _REGION_AXIS_PINS.values()
             if _FAMILY_OF_SENTENCE[alias] == family})
        assert aliases, family
        perturbed = _perturbed(aliases[0], "; this record is safe to publish")
        bad = reason_mismatches(
            rows, lambda key: _REGION_AXIS_PINS[key][0], sentences=perturbed)
        assert bad, (
            f"perturbing {aliases[0]!r} ({family}) changed nothing on the "
            f"region axis: those pins do not depend on the frozen table")
    # And a perturbation reaches at least one point in an E1 region, so the
    # eight newly visited values are load-bearing and not decorative.
    e1_aliases = sorted({
        alias for key, (alias, _script) in _REGION_AXIS_PINS.items()
        if key.split("|")[0] in _E1_PREVIOUSLY_UNVISITED_REGION_TYPES})
    assert e1_aliases
    perturbed = _perturbed(e1_aliases[0], " [cleared]")
    bad = reason_mismatches(
        rows, lambda key: _REGION_AXIS_PINS[key][0], sentences=perturbed)
    assert any(key.split("|")[0] in _E1_PREVIOUSLY_UNVISITED_REGION_TYPES
               for key, _p, _e in bad)


def test_an_appended_out_of_table_claim_fails_a_non_m2_pin():
    """Control: R1's unconditional false-claim shape, on a NON-M2 family."""
    rows = {key: record for key, record in _language_axis_records()}
    key = "recital_ru|ru|unique"
    record = rows[key]
    alias = _LANGUAGE_AXIS_PINS[key][0]
    assert _FAMILY_OF_SENTENCE[alias] == "governing_enactment"
    assert_frozen_reason(record, alias, where="control base")
    tampered = dataclasses.replace(
        record,
        binding_reason=record.binding_reason
        + "; this record is safe to publish")
    with pytest.raises(AssertionError):
        assert_frozen_reason(tampered, alias, where="appended false claim")


def test_interpolated_caller_body_content_fails_a_non_m2_pin():
    """Control: R1's body-channel shape, on a NON-M2 family."""
    rows = {key: record for key, record in _language_axis_records()}
    key = "date_line|ru|none"
    record = rows[key]
    alias = _LANGUAGE_AXIS_PINS[key][0]
    assert _FAMILY_OF_SENTENCE[alias] != "M2_MAIN_PATH"
    assert_frozen_reason(record, alias, where="control base")
    body = _LANGUAGE_AXIS_BODIES["date_line"]["text"]
    tampered = dataclasses.replace(
        record, binding_reason=f"{record.binding_reason} [{body[:24]}]")
    with pytest.raises(AssertionError):
        assert_frozen_reason(tampered, alias, where="interpolated body")


def test_a_family_migration_is_caught_by_the_partition():
    """Control: the REPLACE drift the equality arm alone cannot see.

    A record whose sentence is replaced by another family's LAWFUL sentence
    still equals SOMETHING in the table, so a containment test or a
    family-blind check passes.  The partition does not: the unit is no longer
    where the frozen data says it is.
    """
    rows = list(_language_axis_records())
    victim = next(
        key for key, _record in sorted(rows)
        if _FAMILY_OF_SENTENCE[_LANGUAGE_AXIS_PINS[key][0]]
        == "governing_enactment")
    amb_alias = next(alias for alias in sorted(_SENTENCES)
                     if _FAMILY_OF_SENTENCE[alias] == "ambiguity_margin")
    migrated = [
        (key, dataclasses.replace(record, binding_reason=_SENTENCES[amb_alias])
         if key == victim else record)
        for key, record in rows]

    # The migrated sentence is a LAWFUL sentence of another family, so a
    # membership or containment check does not notice the move at all.
    assert all(record.binding_reason in set(_SENTENCES.values())
               for _key, record in migrated)
    # The equality leg notices, and only for the migrated point.
    bad = reason_mismatches(migrated, lambda key: _LANGUAGE_AXIS_PINS[key][0])
    assert [key for key, _p, _e in bad] == [victim]
    # The partition leg notices independently: the point has changed family.
    observed, expected = {}, {}
    for key, record in migrated:
        observed.setdefault(
            family_of_published(record.binding_reason), set()).add(key)
    for key, _record in rows:
        expected.setdefault(
            _FAMILY_OF_SENTENCE[_LANGUAGE_AXIS_PINS[key][0]], set()).add(key)
    assert observed != expected
    assert victim in observed["ambiguity_margin"]
    assert victim not in expected["ambiguity_margin"]
    assert victim in expected["governing_enactment"]
    assert victim not in observed.get("governing_enactment", set())


def test_a_language_gated_drift_is_caught_on_both_paths():
    """Control: R2's exact shape, simulated against pristine production.

    The drift appends a payload only when the caller declares `it`.  It is
    applied here to the RECORDS, not to production, and the language axis is
    then re-evaluated: the arm must fail, and it must fail on an M2 point and
    on a non-M2 point, because a drift at the emission site reaches both.
    """
    rows = _language_axis_records()

    def drift_gated_on(target):
        """The records as a mutant gated on `language == target` would emit."""
        return [
            (key, record if key.split("|")[1] != target else
             dataclasses.replace(
                 record,
                 binding_reason=record.binding_reason
                 + " [reviewed and cleared]"))
            for key, record in rows]

    # R2's own value first, in full.
    bad = reason_mismatches(drift_gated_on("it"),
                            lambda key: _LANGUAGE_AXIS_PINS[key][0])
    assert bad, "an `it`-gated drift went unnoticed by the language axis"
    assert all(key.split("|")[1] == "it" for key, _p, _e in bad)
    hit_families = {
        _FAMILY_OF_SENTENCE[_LANGUAGE_AXIS_PINS[key][0]]
        for key, _published, _expected in bad}
    assert "M2_MAIN_PATH" in hit_families
    assert hit_families - {"M2_MAIN_PATH"}, sorted(hit_families)

    # And then EVERY declared value the axis sweeps, including the undeclared
    # caller: none of them is a blind spot.
    for language in _LANGUAGES:
        target = "None" if language is None else language
        bad = reason_mismatches(drift_gated_on(target),
                                lambda key: _LANGUAGE_AXIS_PINS[key][0])
        assert bad, f"a drift gated on language={target!r} went unnoticed"
        assert all(key.split("|")[1] == target for key, _p, _e in bad), target
        families = {
            _FAMILY_OF_SENTENCE[_LANGUAGE_AXIS_PINS[key][0]]
            for key, _published, _expected in bad}
        assert "M2_MAIN_PATH" in families, target
        assert families - {"M2_MAIN_PATH"}, (target, sorted(families))


def test_a_length_keyed_drift_is_caught_at_the_probed_point():
    """Control: the verifier's `== 517` clause, simulated the same way.

    A clause that fires only when the emitted predicate span is exactly 517
    characters long is invisible to any pin that never lands on 517.  The
    ladder lands on it, on both the M2 and the governing-enactment family, and
    the neighbouring 516 and 518 points stay green, so the arm distinguishes an
    equality gate from a threshold.
    """
    rows = [(key, record) for key, _body, record in _span_axis_records()]

    def gated(record, length):
        span = record.predicate_span
        if span is None or span[1] - span[0] != length:
            return record
        return dataclasses.replace(
            record,
            binding_reason=record.binding_reason + "; safe to publish")

    for length in (517, 516, 518, 200, 300, 600, 50):
        drifted = [(key, gated(record, length)) for key, record in rows]
        bad = reason_mismatches(
            drifted, lambda key: _SPAN_AXIS_PINS[key][3])
        assert bad, f"a drift keyed on span length {length} went unnoticed"
        assert all(int(key.split("|")[1]) == length for key, _p, _e in bad)
    # At 517 the drift is caught on BOTH ladders, so the length axis is not
    # probed only where P6.1.1 already looks.
    drifted = [(key, gated(record, 517)) for key, record in rows]
    bad = reason_mismatches(drifted, lambda key: _SPAN_AXIS_PINS[key][3])
    assert {key.split("|")[0] for key, _p, _e in bad} == set(_SPAN_LADDERS)


def test_a_region_gated_drift_is_caught_for_every_origin_class():
    """Control: escape class E1's exact shape, at every one of the 17 values.

    The independent verification of P6.1.2 measured that a drift gated on
    `content_region_type == "FOOTER_FURNITURE"` -- one of the eight values of
    production's own vocabulary the surface never instantiated -- passed all 29
    files in BOTH arena shapes, while the matched control on NAVIGATION_MENU (a
    value the surface did instantiate) failed 13 to 16 nodes.  The axis is now
    exhaustive, so the simulation below must fail for EVERY value, with the
    failures confined to the gated one.
    """
    rows = _region_axis_records()

    def drift_gated_on(region):
        """The records a mutant gated on `content_region_type` would emit."""
        return [
            (key, record if key.split("|")[0] != region else
             dataclasses.replace(
                 record,
                 binding_reason=record.binding_reason + " [cleared]"))
            for key, record in rows]

    # E1's own value first, and its matched control, by name.
    for region in ("FOOTER_FURNITURE", "NAVIGATION_MENU"):
        bad = reason_mismatches(drift_gated_on(region),
                                lambda key: _REGION_AXIS_PINS[key][0])
        assert bad, (
            f"a drift gated on content_region_type={region!r} went unnoticed "
            f"-- escape class E1 is NOT closed")
        assert all(key.split("|")[0] == region for key, _p, _e in bad), region

    # And then every value of production's vocabulary, none excepted.
    for region in _REGION_TYPES_SWEPT:
        bad = reason_mismatches(drift_gated_on(region),
                                lambda key: _REGION_AXIS_PINS[key][0])
        assert bad, f"a drift gated on {region!r} went unnoticed"
        assert all(key.split("|")[0] == region for key, _p, _e in bad), region
        # Every point of that region's slice moves, so the arm is not resting
        # on a single lucky body or context shape.
        assert len(bad) == len(_REGION_AXIS_BODIES) * len(_CONTEXT_SHAPES), (
            region, len(bad))

    # The eight values E1 named are among them, and each is caught on its own.
    for region in sorted(_E1_PREVIOUSLY_UNVISITED_REGION_TYPES):
        bad = reason_mismatches(drift_gated_on(region),
                                lambda key: _REGION_AXIS_PINS[key][0])
        assert {key.split("|")[0] for key, _p, _e in bad} == {region}


def test_an_unpinned_sentence_raises_rather_than_being_absorbed():
    """Control: there is no default branch to fall through.

    A published sentence this file has never written down is not classified
    into some nearest family; the lookup raises and the arm that called it
    fails.  That is what makes a NEW wording a decision instead of a silent
    absorption.
    """
    with pytest.raises(KeyError):
        family_of_published("roles were assigned by the binder")
    with pytest.raises(KeyError):
        family_of_published(
            sorted(_SENTENCES.values())[0] + "; and safe to publish")


def test_the_frozen_tables_are_stable_under_rehashing():
    """Determinism: the pins do not depend on set or dict iteration order.

    The reference is keyed literal data and every derived collection here is
    sorted before it is compared, so a different PYTHONHASHSEED cannot move a
    result.  The digest below is a single value a later run can compare.
    """
    digest = hashlib.sha256(json.dumps(
        {"sentences": _SENTENCES,
         "families": _FAMILY_OF_SENTENCE,
         "corpus": _CORPUS_REASON,
         "language_axis": {key: list(value)
                           for key, value in _LANGUAGE_AXIS_PINS.items()},
         "span_axis": {key: list(value)
                       for key, value in _SPAN_AXIS_PINS.items()},
         "region_axis": {key: list(value)
                         for key, value in _REGION_AXIS_PINS.items()}},
        sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()
    assert digest == _FROZEN_TABLE_DIGEST, digest
