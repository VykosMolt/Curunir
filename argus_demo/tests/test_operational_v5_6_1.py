"""V5.6.1 — the repaired protocol, identity, packets, custody, routing and gates.

Five modules, one theme: *a distinction that cannot be represented cannot be
preserved*. V5.6 asked five questions through one boolean and carried a
grammatical position where a semantic actor belongs, and neither defect was
findable by reviewing more carefully — they were defects in what the record
could say.

So the tests below check representability, not behaviour on a happy path: that a
vocabulary cannot be collapsed, that an unresolved role stays unresolved, that a
menu cannot offer an option the seat's own answers forbid, and that a
normalisation rule absorbs only what genuinely changes nothing.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from curunir_operational.v5_6 import schema as S
from curunir_operational.v5_6_1 import custody as CU
from curunir_operational.v5_6_1 import gates as G
from curunir_operational.v5_6_1 import identity as ID
from curunir_operational.v5_6_1 import packets as PK
from curunir_operational.v5_6_1 import protocol as P
from curunir_operational.v5_6_1 import reference as R

pytestmark = pytest.mark.no_db

ROOT = Path(__file__).resolve().parents[1]


# ===========================================================================
# §7 — the typed extraction protocol
# ===========================================================================

def test_the_five_dimensions_have_disjoint_substantive_vocabularies():
    """Overlap is how a reviewer answers two questions with one word."""
    shared_everywhere = {"NOT_APPLICABLE", "EPISTEMICALLY_UNRESOLVABLE"}
    for left in P.DIMENSIONS:
        for right in P.DIMENSIONS:
            if left >= right:
                continue
            overlap = (set(P.DIMENSIONS[left]) & set(P.DIMENSIONS[right])
                       ) - shared_everywhere
            assert not overlap, f"{left} and {right} share {overlap}"


def test_every_dimension_has_exactly_one_reviewer_question():
    assert set(P.REVIEWER_QUESTIONS) == set(P.DIMENSIONS)
    assert len(set(P.REVIEWER_QUESTIONS.values())) == len(P.DIMENSIONS)


@pytest.mark.parametrize("dimension,left,right", P.PROHIBITED_COLLAPSES)
def test_a_prohibited_collapse_names_two_real_and_distinct_values(dimension, left, right):
    assert left in P.DIMENSIONS[dimension]
    assert right in P.DIMENSIONS[dimension]
    assert left != right


def test_the_legacy_boolean_never_yields_a_typed_value():
    for value in (True, False, None):
        migrated = P.migrate_legacy(value)
        assert migrated[P.LEGACY_FIELD] is value
        assert migrated["inferred_typed_values"] is None
        assert migrated["requires_re_adjudication"] is True


def test_a_construction_defect_carries_no_substantive_wording():
    with pytest.raises(P.ProtocolViolation, match="posed no answerable question"):
        P.typed_decision(
            candidate_id="c", seat_id="REVIEWER_A_REPAIR",
            candidate_semantic_validity="CONSTRUCTION_DEFECT",
            boundary_repair_requirement="NOT_APPLICABLE",
            exact_quotation_permission="NO_EXACT_QUOTATION_REQUESTED",
            paraphrase_permission="PARAPHRASE_PERMITTED",
            wording_as_written_permission="NOT_APPLICABLE")


def test_wording_as_written_cannot_survive_a_required_boundary_repair():
    with pytest.raises(P.ProtocolViolation, match="PERMITTED_ONLY_AFTER_BOUNDARY_REPAIR"):
        P.typed_decision(
            candidate_id="c", seat_id="REVIEWER_A_REPAIR",
            candidate_semantic_validity="RECOVERABLE_WITH_BOUNDARY_REPAIR",
            boundary_repair_requirement="EXPAND_BOTH_DIRECTIONS",
            exact_quotation_permission="NO_EXACT_QUOTATION_REQUESTED",
            paraphrase_permission="PARAPHRASE_PERMITTED",
            wording_as_written_permission="PERMITTED_AS_WRITTEN")


# ===========================================================================
# §8 — the semantic actor model
# ===========================================================================

@pytest.mark.parametrize("phrase", [
    "Importers who", "For transparency", "Immediately", "That right",
    "These security", "This process", "Unadjusted data", "As previously",
    "The reporting obligation", "If, following the exchanges",
    "The person whose status", "Through systematic mapping of channels and their",
])
def test_a_grammatical_position_is_not_an_actor(phrase):
    assert not ID.is_referential(phrase)
    derivation = ID.derive_actor(phrase)
    assert derivation.outcome != "RESOLVED"
    assert derivation.resolved_id is None
    assert derivation.resolved_span is None


@pytest.mark.parametrize("phrase", [
    "The European Commission", "Rail Baltica", "The European Court of Auditors",
    "Bundesnetzagentur", "The Swiss Federal Council", "Amazon Web Services",
])
def test_a_named_party_resolves(phrase):
    derivation = ID.derive_actor(phrase)
    assert derivation.outcome == "RESOLVED", derivation.steps
    assert derivation.resolved_id


def test_an_unresolved_derivation_may_never_carry_an_identity():
    with pytest.raises(ID.IdentityViolation, match="substitution"):
        ID.ActorDerivation(
            "d", "semantic_actor", "Importers who", "UNRESOLVED_NOT_REFERENTIAL",
            "v5-6-1-entity-nearest-organisation", None, (),
            "2026-07-25T00:00:00+00:00")


def test_every_derivation_records_its_provenance():
    derivation = ID.derive_actor("The European Commission", predicate="published")
    steps = dict(derivation.steps)
    assert "raw_grammatical_phrase" in steps
    assert "referentiality_check" in steps
    assert "candidate_entity_linking" in steps


def test_a_document_cannot_be_the_actor_of_an_operational_predicate():
    derivation = ID.derive_actor("The Regulation", role="semantic_actor",
                                 predicate="operates")
    assert derivation.outcome == "UNRESOLVED_TYPE_INCOMPATIBLE"


def test_the_splitter_refuses_reserved_keys_with_a_reason():
    identity = ID.build_identity(grammatical_subject="The European Commission")
    with pytest.raises(ID.IdentityViolation, match="derived from the canonical identity"):
        ID.split_fact_and_metaclaim_typed(
            identity=identity, predicate="p", object_or_value="o",
            attribution="smuggled in")


# ===========================================================================
# §9 — canonical serialization
# ===========================================================================

def test_the_five_hash_families_are_distinct_for_the_same_payload():
    payload = {"record_type": "SUPPORT_UNIT", "proposition_id": "p",
               "evidence_bundle_id": "b", "original_language": "en",
               "raw_evidence_ids": ["r"], "seat_id": "REVIEWER_A_REPAIR",
               "object_id": "o", "protocol_version": "V5_6_1_PROTOCOL_1"}
    digests = {family: ID.canonical_hash(payload, family=family,
                                         record_type="SUPPORT_UNIT")
               for family in ID.HASH_FAMILIES}
    assert len(set(digests.values())) == len(ID.HASH_FAMILIES)


def test_null_and_absent_are_distinguished():
    with_null = ID.canonical_hash(
        {"record_type": "X", "translation_language": None},
        family="evidence_content_hash", record_type="X")
    without = ID.canonical_hash(
        {"record_type": "X"}, family="evidence_content_hash", record_type="X")
    assert with_null != without


def test_hashing_is_unicode_normalised_and_order_stable():
    left = ID.canonical_hash(
        {"record_type": "X", "document_context": "Amélie"},
        family="evidence_content_hash", record_type="X")
    right = ID.canonical_hash(
        {"document_context": "Amélie", "record_type": "X"},
        family="evidence_content_hash", record_type="X")
    assert left == right


def test_an_unknown_hash_family_is_refused():
    with pytest.raises(ID.IdentityViolation, match="unknown hash family"):
        ID.canonical_hash({}, family="made_up_hash", record_type="X")


# ===========================================================================
# §5, §8.2 — packets and adaptive menus
# ===========================================================================

def test_stage_e_is_derived_from_the_seats_own_answers():
    for support_class in S.SUPPORT_CLASSES:
        for wording in PK.available_wording_permissions(support_class):
            for disposition in PK.available_dispositions(
                    support_class, wording_permission=wording):
                assert not S.contradicts(support_class, disposition), (
                    support_class, wording, disposition)


def test_no_support_class_is_offered_an_empty_disposition_menu():
    for support_class in S.SUPPORT_CLASSES:
        for wording in PK.available_wording_permissions(support_class):
            assert PK.available_dispositions(
                support_class, wording_permission=wording), (support_class, wording)


def test_a_valid_candidate_is_not_offered_a_boundary_repair():
    assert PK.available_boundary_repairs("VALID") == ("NO_REPAIR_REQUIRED",
                                                      "NOT_APPLICABLE")
    recoverable = PK.available_boundary_repairs("RECOVERABLE_WITH_BOUNDARY_REPAIR")
    assert "NO_REPAIR_REQUIRED" not in recoverable
    assert "NOT_APPLICABLE" not in recoverable


def test_the_actor_question_changes_shape_when_no_actor_resolved():
    resolved = PK.support_questions(actor_resolved=True)
    unresolved = PK.support_questions(actor_resolved=False)
    assert PK.ACTOR_QUESTION_RESOLVED in resolved
    assert PK.ACTOR_QUESTION_UNRESOLVED in unresolved
    assert len(resolved) == len(unresolved)
    assert "Do not supply a nearest organisation" in PK.ACTOR_QUESTION_UNRESOLVED


def test_a_packet_carrying_a_prior_answer_is_refused():
    with pytest.raises(PK.PacketViolation, match="may not be shown a prior answer"):
        PK.RepairedSupportPacket(
            "p", "p", "SUPPORT", "prop", "claim", "bundle", "V5_6_1_PROTOCOL_1",
            "V5_6_1_QUESTIONS_1", "sentence", "subject", None, "UNRESOLVED",
            None, None, None, "pred", "obj", "UNDERLYING_FACT_CLAIM", "POSITIVE",
            "ASSERTED", "UNKNOWN", "INDEPENDENCE_UNKNOWN", "evidence", "en",
            "text", None, None, False, (), (), "title",
            tuple(S.SUPPORT_QUESTION_ORDER), tuple(S.SUPPORT_CLASSES),
            tuple(S.QUALIFICATION_DIMENSIONS), tuple(S.WORDING_PERMISSIONS),
            PK.SUPPORT_QUESTIONS, "h1", "h2", "h3",
            {"partition": "REFERENCE_DEVELOPMENT"},
            "2026-07-25T00:00:00+00:00")


def test_an_unresolved_actor_may_not_carry_a_span():
    with pytest.raises(PK.PacketViolation, match="nearest-entity substitution"):
        PK.RepairedSupportPacket(
            "p", "p", "SUPPORT", "prop", "claim", "bundle", "V5_6_1_PROTOCOL_1",
            "V5_6_1_QUESTIONS_1", "sentence", "subject",
            "The European Commission", "UNRESOLVED",
            None, None, None, "pred", "obj", "UNDERLYING_FACT_CLAIM", "POSITIVE",
            "ASSERTED", "UNKNOWN", "INDEPENDENCE_UNKNOWN", "evidence", "en",
            "text", None, None, False, (), (), "title",
            tuple(S.SUPPORT_QUESTION_ORDER), tuple(S.SUPPORT_CLASSES),
            tuple(S.QUALIFICATION_DIMENSIONS), tuple(S.WORDING_PERMISSIONS),
            PK.SUPPORT_QUESTIONS, "h1", "h2", "h3", {},
            "2026-07-25T00:00:00+00:00")


def test_a_packet_named_with_an_answer_counts_as_leakage():
    audit = PK.audit_blinding([], paths=["units/rejected/batch.json"])
    assert audit["verdict"] == "FAIL"
    clean = PK.audit_blinding([], paths=["units/batch-0007.json"])
    assert clean["verdict"] == "PASS"


def test_reasoning_shorter_than_the_minimum_is_refused():
    from tests.test_operational_v5_6_1_mutations import support_answer
    problems = PK.validate_answer(support_answer(reasoning="short"))
    assert any(str(PK.MIN_REASONING_CHARS) in p for p in problems)


# ===========================================================================
# §9 routing — material versus normalised
# ===========================================================================

def _support(**overrides):
    from tests.test_operational_v5_6_1_mutations import support_answer
    return support_answer(**overrides)


def test_unresolved_and_not_applicable_are_one_family():
    decisions = [_support(time_alignment="UNRESOLVED"),
                 _support(time_alignment="NOT_APPLICABLE"),
                 _support(time_alignment="UNRESOLVED")]
    assert R.material_dimensions(decisions) == ()
    absorbed = R.nonmaterial_normalisations(decisions)
    assert [row["dimension"] for row in absorbed] == ["time_alignment"]


def test_declining_to_read_is_not_the_same_as_reading_alignment():
    """The normalisation absorbs UNRESOLVED vs NOT_APPLICABLE and nothing more.

    ALIGNED against either of them is a seat asserting a reading against a seat
    declining to, which is a real disagreement.
    """
    decisions = [_support(), _support(time_alignment="UNRESOLVED"),
                 _support(time_alignment="NOT_APPLICABLE")]
    assert "time_alignment" in R.material_dimensions(decisions)


def test_aligned_versus_misaligned_is_never_normalised_away():
    decisions = [_support(), _support(time_alignment="MISALIGNED"), _support()]
    assert "time_alignment" in R.material_dimensions(decisions)


def test_qualification_order_is_not_a_disagreement():
    decisions = [
        _support(support_class="QUALIFIED_SUPPORT",
                 disposition="PUBLISHED_WITH_QUALIFICATION",
                 required_qualifications=["PILOT_ONLY", "PARTIAL_SCOPE"]),
        _support(support_class="QUALIFIED_SUPPORT",
                 disposition="PUBLISHED_WITH_QUALIFICATION",
                 required_qualifications=["PARTIAL_SCOPE", "PILOT_ONLY"]),
        _support(support_class="QUALIFIED_SUPPORT",
                 disposition="PUBLISHED_WITH_QUALIFICATION",
                 required_qualifications=["PILOT_ONLY", "PARTIAL_SCOPE"]),
    ]
    assert "required_qualifications" not in R.material_dimensions(decisions)


def test_a_dropped_qualification_is_a_disagreement():
    decisions = [
        _support(support_class="QUALIFIED_SUPPORT",
                 disposition="PUBLISHED_WITH_QUALIFICATION",
                 required_qualifications=["PILOT_ONLY"]),
        _support(support_class="QUALIFIED_SUPPORT",
                 disposition="PUBLISHED_WITH_QUALIFICATION",
                 required_qualifications=[]),
        _support(support_class="QUALIFIED_SUPPORT",
                 disposition="PUBLISHED_WITH_QUALIFICATION",
                 required_qualifications=["PILOT_ONLY"]),
    ]
    assert "required_qualifications" in R.material_dimensions(decisions)


def test_a_three_way_split_records_every_seat_as_dissenting():
    decisions = [
        _support(),
        _support(support_class="PARTIAL_SUPPORT",
                 disposition="PUBLISHED_WITH_QUALIFICATION"),
        _support(support_class="NOT_SUPPORTED", addresses_proposition=False,
                 support_completeness="ABSENT",
                 first_material_failure="addresses_proposition",
                 wording_permission="NO_PUBLICATION_PERMITTED",
                 disposition="REJECTED_UNSUPPORTED"),
    ]
    assert R.agreement_pattern(decisions) == "THREE_WAY_SPLIT"
    assert len(R.dissent(decisions, ["A", "B", "C"])) == 3


def test_the_reference_name_is_not_a_prohibited_one():
    assert R.reference_name_is_permitted(R.REFERENCE_NAME)
    for name in R.PROHIBITED_NAMES:
        assert not R.reference_name_is_permitted(name)
    assert "GOLD" not in R.REFERENCE_NAME
    assert "MODEL_PANEL" in R.REFERENCE_NAME


# ===========================================================================
# §6, §8 — custody
# ===========================================================================

def test_the_repair_namespace_is_scoped_not_permanent():
    from curunir_operational.v5_4 import custody as C4
    assert C4.SEAT_IDS == ("REVIEWER_A", "REVIEWER_B", "REVIEWER_C")
    with CU.seat_namespace():
        assert "REVIEWER_A_REPAIR" in C4.SEAT_IDS
    assert C4.SEAT_IDS == ("REVIEWER_A", "REVIEWER_B", "REVIEWER_C")


def test_rehearsal_credentials_do_not_authorise_the_real_panel():
    salt = "one-salt-for-both"
    rehearsal = CU.derive_repair_token(seat_id="PREFLIGHT_REVIEWER_A", run_salt=salt)
    panel = CU.derive_repair_token(seat_id="REVIEWER_A_REPAIR", run_salt=salt)
    assert rehearsal != panel


def test_a_seat_resumes_without_rewriting_an_earlier_decision(tmp_path):
    order = ["u0", "u1", "u2"]
    seat = CU.RepairSeat.open(seat_id="REVIEWER_B_REPAIR", seat_root=tmp_path / "b",
                              run_salt="salt", session_id="s",
                              assignment_order=order)
    seat.append(dossier_id="u0", payload={"unit_id": "u0"})
    head = seat.head_hash
    resumed = CU.RepairSeat.resume(seat_id="REVIEWER_B_REPAIR",
                                   seat_root=tmp_path / "b", run_salt="salt",
                                   session_id="s", assignment_order=order)
    assert resumed.head_hash == head
    assert resumed.next_dossier_id == "u1"
    assert resumed.rows == 1


def test_two_seats_may_not_share_or_nest_output_roots(tmp_path):
    CU.RepairSeat.open(seat_id="REVIEWER_A_REPAIR", seat_root=tmp_path / "panel/a",
                       run_salt="salt", session_id="s", assignment_order=["u0"])
    with pytest.raises(CU.CustodyViolation, match="nested output path|shared output path"):
        CU.RepairSeat.open(seat_id="REVIEWER_B_REPAIR",
                           seat_root=tmp_path / "panel/a/nested", run_salt="salt",
                           session_id="s", assignment_order=["u0"])


def test_a_salt_lives_outside_every_seat_root(tmp_path):
    path = CU.salt_path(tmp_path, "REVIEWER_A_REPAIR")
    assert "seats" not in path.parts
    assert path.parent.name == "salts"


# ===========================================================================
# §13, §16 — gates
# ===========================================================================

def test_insufficient_evidence_names_every_reason():
    verdict = G.sufficient_evidence_for_variant_selection(
        nondefective_units=5, discriminating_units=1, fold_sizes={"a": 2},
        family_shares={"a": 1.0}, uncertainty_estimated=False)
    assert verdict["sufficient"] is False
    joined = " ".join(verdict["reasons_against"])
    for fragment in ("non-defective development units", "variants differ",
                     "source-family folds", "uncertainty estimate",
                     "decide the outcome"):
        assert fragment in joined, fragment


def test_the_validation_seal_starts_sealed(tmp_path):
    seal = G.ValidationSeal(seal_path=tmp_path / "seal.json",
                            candidate_freeze_path=tmp_path / "freeze.json")
    assert seal.sealed
    assert seal.open_events == 0
    seal.assert_sealed()


def test_the_canonical_guard_covers_this_deployments_port():
    assert G.canonical_dsn_port() in G.guarded_ports()
    assert G.CONVENTIONAL_POSTGRES_PORTS <= G.guarded_ports()


def test_the_declared_canonical_port_matches_the_real_dsn():
    """The guard's port is declared, not imported — so it is pinned from here.

    ``curunir_operational`` may not reference canonical database machinery, so
    :data:`gates.DECLARED_CANONICAL_PORT` is a literal.  A literal that drifts
    from the real DSN silently stops guarding the store, which is why this test
    lives on the ``argus`` side of the boundary, where reading the DSN is
    allowed.
    """
    from argus import db
    import re
    match = re.search(r":(\d+)/", db.DEFAULT_DSN)
    assert match, db.DEFAULT_DSN
    assert G.DECLARED_CANONICAL_PORT == int(match.group(1))
