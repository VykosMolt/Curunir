"""Reviewer-seat custody tests (contract Section 11).

The V5.3 defects these close, all three of which were found only by
after-the-fact quarantine:

* a seat that returned templated null work;
* a seat that forked and raced itself on one shared output file;
* a foreign process that wrote into a sealed reviewer path.

Every refusal below asserts the *reason*, not merely that something was
raised, so a refactor cannot make a test pass by refusing for the wrong cause.
"""
from __future__ import annotations

import json
import os
import stat
from pathlib import Path

import pytest

from curunir_operational.v5_4 import custody as C

pytestmark = pytest.mark.no_db

SALT = "v5-4-run-salt-2026-07"
SESSION = "session-alpha"
ORDER = ("dossier-1", "dossier-2", "dossier-3")
LONG_ORDER = ("dossier-1", "dossier-2", "dossier-3", "dossier-4")
SURFACES = {
    "dossier-1": "SURFACE_1_SEMANTIC_EXTRACTION",
    "dossier-2": "SURFACE_1_SEMANTIC_EXTRACTION",
    "dossier-3": "SURFACE_4_CLAIM_SUPPORT",
    "dossier-4": "SURFACE_4_CLAIM_SUPPORT",
}


def _payload(index: int, *, label: str = "CORRECT", reasoning: str | None = None):
    return {
        "label": label,
        "reasoning": reasoning if reasoning is not None else f"reviewed dossier {index} against the cited span",
        "confidence": "MODERATE",
    }


def _open(tmp_path: Path, *, seat_id: str = "REVIEWER_A", session: str = SESSION,
          order=ORDER, root: Path | None = None, salt: str = SALT) -> C.ReviewerSeat:
    return C.ReviewerSeat.open(
        seat_id=seat_id, seat_root=root or (tmp_path / "panel" / seat_id.lower()),
        run_salt=salt, session_id=session, assignment_order=order)


def _fill(seat: C.ReviewerSeat, *, upto: int | None = None, label: str = "CORRECT",
          reasoning: str | None = None) -> None:
    order = seat.assignment_order[:upto] if upto is not None else seat.assignment_order
    for index, dossier in enumerate(order):
        seat.append(dossier_id=dossier,
                    payload=_payload(index, label=label, reasoning=reasoning))


# ---------------------------------------------------------------------------
# Identity, tokens, and the chain primitive
# ---------------------------------------------------------------------------

def test_the_panel_is_exactly_three_seats():
    assert C.SEAT_IDS == ("REVIEWER_A", "REVIEWER_B", "REVIEWER_C")


def test_capability_tokens_are_deterministic_and_per_seat():
    first = C.derive_capability_token(seat_id="REVIEWER_A", run_salt=SALT)
    again = C.derive_capability_token(seat_id="REVIEWER_A", run_salt=SALT)
    other_seat = C.derive_capability_token(seat_id="REVIEWER_B", run_salt=SALT)
    other_run = C.derive_capability_token(seat_id="REVIEWER_A", run_salt="another-salt")
    assert first == again          # reproducible: no random, no uuid4
    assert first != other_seat
    assert first != other_run


def test_records_store_only_the_token_hash(tmp_path):
    seat = _open(tmp_path)
    token = C.derive_capability_token(seat_id="REVIEWER_A", run_salt=SALT)
    record = seat.append(dossier_id="dossier-1", payload=_payload(0))
    assert record.capability_token_hash == C.capability_token_hash(token)
    on_disk = seat.records_path.read_text()
    assert token not in on_disk
    assert SALT not in on_disk


def test_first_record_chains_from_the_documented_genesis_constant(tmp_path):
    seat = _open(tmp_path)
    record = seat.append(dossier_id="dossier-1", payload=_payload(0))
    assert C.GENESIS_RECORD_HASH == "0" * 64
    assert record.previous_record_hash == C.GENESIS_RECORD_HASH


def test_record_hash_chains_over_previous_seat_dossier_and_payload(tmp_path):
    seat = _open(tmp_path)
    first = seat.append(dossier_id="dossier-1", payload=_payload(0))
    second = seat.append(dossier_id="dossier-2", payload=_payload(1))
    assert second.previous_record_hash == first.record_hash
    assert second.record_hash == C.chain_record_hash(
        previous_record_hash=first.record_hash, seat_id="REVIEWER_A",
        dossier_id="dossier-2", payload=dict(second.payload))


def test_a_record_that_does_not_chain_cannot_be_constructed():
    with pytest.raises(C.CustodyViolation, match="does not chain"):
        C.SeatRecord(
            seat_id="REVIEWER_A", dossier_id="dossier-1", dossier_index=0,
            process_id=os.getpid(), session_id=SESSION,
            capability_token_hash="a" * 64,
            previous_record_hash=C.GENESIS_RECORD_HASH, record_hash="b" * 64,
            timestamp="2026-07-24T00:00:00+00:00", payload={"label": "CORRECT"})


def test_unknown_seat_id_is_refused_at_open(tmp_path):
    with pytest.raises(C.CustodyViolation, match="unknown reviewer seat"):
        C.ReviewerSeat.open(seat_id="REVIEWER_D", seat_root=tmp_path / "d",
                            run_salt=SALT, session_id=SESSION,
                            assignment_order=ORDER)


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------

def test_three_seat_happy_path_produces_three_disjoint_verified_chains(tmp_path):
    seats = C.open_panel(panel_root=tmp_path / "panel", run_salt=SALT,
                         session_id=SESSION,
                         assignments={seat: ORDER for seat in C.SEAT_IDS})
    for seat in seats.values():
        _fill(seat)
        manifest = seat.finalize()
        assert manifest["rows"] == 3
        assert manifest["assignment_complete"] is True
        assert manifest["chain_head"] == manifest["last_record_hash"]
    audit = C.audit_panel([seat.seat_root for seat in seats.values()])
    assert audit["verdict"] == "CUSTODY_CLEAN"
    assert audit["foreign_writes"] == []
    assert audit["shared_output_paths"] == []
    assert audit["hash_chain_breaks"] == []
    assert all(entry["chain_intact"] for entry in audit["per_seat"].values())


def test_the_finalized_manifest_is_immutable_and_carries_the_chain(tmp_path):
    seat = _open(tmp_path)
    _fill(seat)
    manifest = seat.finalize()
    assert seat.manifest_path.exists()
    assert manifest["seat_id"] == "REVIEWER_A"
    assert manifest["first_record_hash"] != manifest["last_record_hash"]
    assert manifest["genesis_record_hash"] == C.GENESIS_RECORD_HASH
    assert stat.S_IMODE(seat.manifest_path.stat().st_mode) == 0o444
    assert stat.S_IMODE(seat.records_path.stat().st_mode) == 0o444
    stored = json.loads(seat.manifest_path.read_text())
    assert stored["records_file_sha256"] == manifest["records_file_sha256"]


def test_assert_disjoint_seat_roots_accepts_the_panel_layout(tmp_path):
    roots = [tmp_path / "panel" / seat.lower() for seat in C.SEAT_IDS]
    assert C.assert_disjoint_seat_roots(roots)["disjoint"] is True


# ---------------------------------------------------------------------------
# Refusals at the write boundary
# ---------------------------------------------------------------------------

def test_foreign_seat_id_is_refused(tmp_path):
    seat = _open(tmp_path)
    with pytest.raises(C.CustodyViolation, match="foreign seat id"):
        seat.append(dossier_id="dossier-1", payload=_payload(0),
                    seat_id="REVIEWER_B")
    assert seat.rows == 0


def test_absent_capability_token_is_refused(tmp_path):
    seat = _open(tmp_path)
    with pytest.raises(C.CustodyViolation, match="capability token absent"):
        seat.append(dossier_id="dossier-1", payload=_payload(0),
                    capability_token=None)


def test_invalid_capability_token_is_refused(tmp_path):
    seat = _open(tmp_path)
    foreign = C.derive_capability_token(seat_id="REVIEWER_B", run_salt=SALT)
    with pytest.raises(C.CustodyViolation, match="capability token invalid"):
        seat.append(dossier_id="dossier-1", payload=_payload(0),
                    capability_token=foreign)


def test_a_write_from_another_pid_is_refused(tmp_path):
    seat = _open(tmp_path)
    with pytest.raises(C.CustodyViolation, match="process lease violated"):
        seat.append(dossier_id="dossier-1", payload=_payload(0),
                    process_id=os.getpid() + 1)


def test_a_write_from_another_session_is_refused(tmp_path):
    seat = _open(tmp_path)
    with pytest.raises(C.CustodyViolation, match="session lease violated"):
        seat.append(dossier_id="dossier-1", payload=_payload(0),
                    session_id="session-beta")


def test_a_shared_output_path_between_two_seats_is_refused(tmp_path):
    shared = tmp_path / "shared_reviewer_output"
    _open(tmp_path, seat_id="REVIEWER_A", root=shared)
    with pytest.raises(C.CustodyViolation, match="shared output path"):
        _open(tmp_path, seat_id="REVIEWER_B", root=shared)


def test_a_nested_output_path_between_two_seats_is_refused(tmp_path):
    outer = tmp_path / "outer"
    _open(tmp_path, seat_id="REVIEWER_A", root=outer)
    with pytest.raises(C.CustodyViolation, match="nested output path"):
        _open(tmp_path, seat_id="REVIEWER_B", root=outer / "inner")


def test_assert_disjoint_seat_roots_refuses_a_parent_child_layout(tmp_path):
    with pytest.raises(C.CustodyViolation, match="nested output path"):
        C.assert_disjoint_seat_roots([tmp_path / "a", tmp_path / "a" / "b"])


def test_a_missing_previous_record_hash_is_refused(tmp_path):
    seat = _open(tmp_path)
    with pytest.raises(C.CustodyViolation, match="previous-record hash missing"):
        seat.append(dossier_id="dossier-1", payload=_payload(0),
                    previous_record_hash="")


def test_a_mismatched_previous_record_hash_is_refused(tmp_path):
    seat = _open(tmp_path)
    seat.append(dossier_id="dossier-1", payload=_payload(0))
    with pytest.raises(C.CustodyViolation, match="previous-record hash mismatch"):
        seat.append(dossier_id="dossier-2", payload=_payload(1),
                    previous_record_hash=C.GENESIS_RECORD_HASH)


def test_a_racing_second_writer_is_refused_at_the_next_append(tmp_path):
    """The V5.3 fork race: two writers, one file, rows lost in between."""
    seat = _open(tmp_path)
    first = seat.append(dossier_id="dossier-1", payload=_payload(0))
    forged_payload = {"label": "CORRECT", "reasoning": "written by a forked sibling"}
    forged_hash = C.chain_record_hash(
        previous_record_hash=first.record_hash, seat_id="REVIEWER_A",
        dossier_id="dossier-2", payload=forged_payload)
    with seat.records_path.open("a") as handle:
        handle.write(json.dumps({
            "seat_id": "REVIEWER_A", "dossier_id": "dossier-2", "dossier_index": 1,
            "process_id": os.getpid() + 7, "session_id": "session-fork",
            "capability_token_hash": "c" * 64,
            "previous_record_hash": first.record_hash, "record_hash": forged_hash,
            "timestamp": "2026-07-24T00:00:00+00:00", "payload": forged_payload,
        }, sort_keys=True) + "\n")
    with pytest.raises(C.CustodyViolation, match="another writer is appending"):
        seat.append(dossier_id="dossier-2", payload=_payload(1))


def test_a_repeated_dossier_is_refused_append_only(tmp_path):
    seat = _open(tmp_path)
    seat.append(dossier_id="dossier-1", payload=_payload(0))
    with pytest.raises(C.CustodyViolation, match="append-only"):
        seat.append(dossier_id="dossier-1", payload=_payload(0, label="INCORRECT"))
    assert seat.rows == 1


def test_an_out_of_order_dossier_is_refused(tmp_path):
    seat = _open(tmp_path)
    with pytest.raises(C.CustodyViolation, match="out-of-order dossier"):
        seat.append(dossier_id="dossier-3", payload=_payload(2))


def test_writing_past_the_assignment_order_is_refused(tmp_path):
    seat = _open(tmp_path)
    _fill(seat)
    with pytest.raises(C.CustodyViolation, match="assignment order exhausted"):
        seat.append(dossier_id="dossier-9", payload=_payload(9))


def test_a_foreign_file_in_the_seat_root_is_refused_at_the_next_write(tmp_path):
    seat = _open(tmp_path)
    seat.append(dossier_id="dossier-1", payload=_payload(0))
    (seat.seat_root / "reviewer_notes.md").write_text("dropped in by another process\n")
    with pytest.raises(C.CustodyViolation, match="foreign file in the sealed seat root"):
        seat.append(dossier_id="dossier-2", payload=_payload(1))


def test_a_second_finalization_is_refused(tmp_path):
    seat = _open(tmp_path)
    _fill(seat)
    seat.finalize()
    with pytest.raises(C.CustodyViolation, match="already finalized"):
        seat.finalize()


def test_any_append_after_finalization_is_refused(tmp_path):
    seat = _open(tmp_path, order=LONG_ORDER)
    _fill(seat, upto=3)
    seat.finalize()
    with pytest.raises(C.CustodyViolation, match="append after finalization"):
        seat.append(dossier_id="dossier-4", payload=_payload(3))


def test_reopening_a_finalized_seat_root_is_refused(tmp_path):
    root = tmp_path / "panel" / "reviewer_a"
    seat = _open(tmp_path, root=root)
    _fill(seat)
    seat.finalize()
    with pytest.raises(C.CustodyViolation, match="already exists and is finalized"):
        _open(tmp_path, root=root)


def test_reopening_an_unfinalized_seat_root_is_refused_in_favour_of_resume(tmp_path):
    root = tmp_path / "panel" / "reviewer_a"
    seat = _open(tmp_path, root=root)
    seat.append(dossier_id="dossier-1", payload=_payload(0))
    with pytest.raises(C.CustodyViolation, match="continued through resume"):
        _open(tmp_path, root=root)


def test_a_payload_that_is_not_a_mapping_is_refused(tmp_path):
    seat = _open(tmp_path)
    with pytest.raises(C.CustodyViolation, match="payload must be a mapping"):
        seat.append(dossier_id="dossier-1", payload=["CORRECT"])  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Resume
# ---------------------------------------------------------------------------

def test_resume_continues_after_a_simulated_provider_failure(tmp_path):
    root = tmp_path / "panel" / "reviewer_a"
    seat = _open(tmp_path, root=root)
    _fill(seat, upto=2)
    del seat  # the provider process dies here

    resumed = C.ReviewerSeat.resume(
        seat_id="REVIEWER_A", seat_root=root, run_salt=SALT, session_id=SESSION,
        assignment_order=ORDER)
    assert resumed.next_dossier_index == 2
    assert resumed.next_dossier_id == "dossier-3"
    before = C.verify_chain(root)
    resumed.append(dossier_id="dossier-3", payload=_payload(2))
    after = C.verify_chain(root)
    assert after["rows"] == 3
    assert after["chain_intact"] is True
    # earlier records were not rewritten
    assert after["first_record_hash"] == before["first_record_hash"]
    manifest = resumed.finalize()
    assert manifest["assignment_complete"] is True


def test_resume_refuses_a_different_session(tmp_path):
    root = tmp_path / "panel" / "reviewer_a"
    _fill(_open(tmp_path, root=root), upto=1)
    with pytest.raises(C.CustodyViolation, match="same session"):
        C.ReviewerSeat.resume(seat_id="REVIEWER_A", seat_root=root, run_salt=SALT,
                              session_id="session-beta", assignment_order=ORDER)


def test_resume_refuses_a_different_capability_token(tmp_path):
    root = tmp_path / "panel" / "reviewer_a"
    _fill(_open(tmp_path, root=root), upto=1)
    with pytest.raises(C.CustodyViolation, match="same capability token"):
        C.ReviewerSeat.resume(seat_id="REVIEWER_A", seat_root=root,
                              run_salt="a-different-run-salt", session_id=SESSION,
                              assignment_order=ORDER)


def test_resume_refuses_a_foreign_seat(tmp_path):
    root = tmp_path / "panel" / "reviewer_a"
    _fill(_open(tmp_path, root=root), upto=1)
    with pytest.raises(C.CustodyViolation, match="foreign seat id"):
        C.ReviewerSeat.resume(seat_id="REVIEWER_B", seat_root=root, run_salt=SALT,
                              session_id=SESSION, assignment_order=ORDER)


def test_resume_refuses_a_changed_assignment_order(tmp_path):
    root = tmp_path / "panel" / "reviewer_a"
    _fill(_open(tmp_path, root=root), upto=1)
    with pytest.raises(C.CustodyViolation, match="same assignment order"):
        C.ReviewerSeat.resume(seat_id="REVIEWER_A", seat_root=root, run_salt=SALT,
                              session_id=SESSION,
                              assignment_order=("dossier-2", "dossier-1", "dossier-3"))


def test_resume_refuses_a_broken_chain(tmp_path):
    root = tmp_path / "panel" / "reviewer_a"
    seat = _open(tmp_path, root=root)
    _fill(seat, upto=2)
    lines = seat.records_path.read_text().splitlines()
    tampered = json.loads(lines[1])
    tampered["payload"]["label"] = "INCORRECT"
    seat.records_path.write_text(
        lines[0] + "\n" + json.dumps(tampered, sort_keys=True) + "\n")
    with pytest.raises(C.CustodyViolation, match="hash chain is broken at index 1"):
        C.ReviewerSeat.resume(seat_id="REVIEWER_A", seat_root=root, run_salt=SALT,
                              session_id=SESSION, assignment_order=ORDER)


def test_resume_refuses_a_finalized_seat(tmp_path):
    root = tmp_path / "panel" / "reviewer_a"
    seat = _open(tmp_path, root=root)
    _fill(seat)
    seat.finalize()
    with pytest.raises(C.CustodyViolation, match="never resumed"):
        C.ReviewerSeat.resume(seat_id="REVIEWER_A", seat_root=root, run_salt=SALT,
                              session_id=SESSION, assignment_order=ORDER)


def test_resume_refuses_a_root_that_was_never_opened(tmp_path):
    with pytest.raises(C.CustodyViolation, match="no seat lease to resume"):
        C.ReviewerSeat.resume(seat_id="REVIEWER_A", seat_root=tmp_path / "nothing",
                              run_salt=SALT, session_id=SESSION,
                              assignment_order=ORDER)


# ---------------------------------------------------------------------------
# Verification and audit
# ---------------------------------------------------------------------------

def test_verify_chain_detects_a_hand_tampered_record(tmp_path):
    seat = _open(tmp_path)
    _fill(seat)
    seat.finalize()
    os.chmod(seat.records_path, 0o600)  # an attacker with filesystem rights
    lines = seat.records_path.read_text().splitlines()
    tampered = json.loads(lines[2])
    tampered["payload"]["label"] = "INCORRECT"
    seat.records_path.write_text("\n".join(lines[:2] + [json.dumps(tampered, sort_keys=True)]) + "\n")

    verification = C.verify_chain(seat.seat_root)
    assert verification["chain_intact"] is False
    assert verification["break_index"] == 2
    assert verification["break_reason"] == "record hash does not match its content"
    assert verification["finalized"] is True
    assert verification["manifest_consistent"] is False


def test_verify_chain_reports_a_clean_finalized_seat(tmp_path):
    seat = _open(tmp_path)
    _fill(seat)
    seat.finalize()
    verification = C.verify_chain(seat.seat_root)
    assert verification["rows"] == 3
    assert verification["chain_intact"] is True
    assert verification["break_index"] is None
    assert verification["break_reason"] is None
    assert verification["finalized"] is True
    assert verification["manifest_consistent"] is True
    assert verification["head_hash"] == seat.head_hash
    assert verification["seat_id"] == "REVIEWER_A"
    assert verification["dossiers"] == list(ORDER)
    assert verification["foreign_seat_rows"] == []
    assert verification["lease_mismatch_rows"] == []


def test_verify_chain_reports_a_missing_record_file(tmp_path):
    verification = C.verify_chain(tmp_path / "not-a-seat")
    assert verification["chain_intact"] is False
    assert verification["break_reason"] == "record file is missing"
    assert verification["rows"] == 0


def test_audit_panel_reports_a_shared_output_path(tmp_path):
    shared = tmp_path / "shared"
    seat = _open(tmp_path, seat_id="REVIEWER_A", root=shared)
    _fill(seat)
    seat.finalize()
    other = _open(tmp_path, seat_id="REVIEWER_B", root=tmp_path / "b")
    _fill(other)
    other.finalize()

    audit = C.audit_panel([shared, shared, other.seat_root])
    assert audit["verdict"] == "CUSTODY_VIOLATED"
    assert any(item["relation"] == "IDENTICAL" for item in audit["shared_output_paths"])


def test_audit_panel_reports_a_foreign_file_written_into_a_sealed_path(tmp_path):
    seat = _open(tmp_path)
    _fill(seat)
    seat.finalize()
    (seat.seat_root / "someone_elses_output.jsonl").write_text("{}\n")
    audit = C.audit_panel([seat.seat_root])
    assert audit["verdict"] == "CUSTODY_VIOLATED"
    kinds = {item["kind"] for item in audit["foreign_writes"]}
    assert "FOREIGN_FILE_IN_SEAT_ROOT" in kinds


def test_audit_panel_reports_a_foreign_seat_row_and_a_chain_break(tmp_path):
    seat = _open(tmp_path)
    _fill(seat, upto=1)
    forged_payload = {"label": "CORRECT", "reasoning": "written by REVIEWER_C"}
    forged_hash = C.chain_record_hash(
        previous_record_hash=seat.head_hash, seat_id="REVIEWER_C",
        dossier_id="dossier-2", payload=forged_payload)
    with seat.records_path.open("a") as handle:
        handle.write(json.dumps({
            "seat_id": "REVIEWER_C", "dossier_id": "dossier-2", "dossier_index": 1,
            "process_id": os.getpid(), "session_id": "session-gamma",
            "capability_token_hash": "d" * 64,
            "previous_record_hash": seat.head_hash, "record_hash": forged_hash,
            "timestamp": "2026-07-24T00:00:00+00:00", "payload": forged_payload,
        }, sort_keys=True) + "\n")
    audit = C.audit_panel([seat.seat_root])
    kinds = {item["kind"] for item in audit["foreign_writes"]}
    assert kinds >= {"FOREIGN_SEAT_ID_IN_RECORD", "FOREIGN_LEASE_IN_RECORD"}
    assert audit["verdict"] == "CUSTODY_VIOLATED"


# ---------------------------------------------------------------------------
# Section 11.6 quality gate
# ---------------------------------------------------------------------------

def test_quality_gate_passes_a_clean_seat(tmp_path):
    seat = _open(tmp_path, order=LONG_ORDER)
    for index, dossier in enumerate(LONG_ORDER):
        seat.append(dossier_id=dossier,
                    payload=_payload(index, label="CORRECT" if index % 2 else "INCORRECT"))
    seat.finalize()
    gate = C.seat_quality_gate(seat.seat_root, surfaces_by_dossier=SURFACES)
    assert gate["verdict"] == "PASS"
    assert all(gate["checks"].values())
    assert gate["distinct_reasoning_ratio"] == 1.0


def test_quality_gate_fails_a_templated_seat(tmp_path):
    seat = _open(tmp_path, order=LONG_ORDER)
    for index, dossier in enumerate(LONG_ORDER):
        seat.append(dossier_id=dossier,
                    payload=_payload(index, label="CORRECT" if index % 2 else "INCORRECT",
                                     reasoning="Insufficient information to adjudicate."))
    seat.finalize()
    gate = C.seat_quality_gate(seat.seat_root, surfaces_by_dossier=SURFACES)
    assert gate["verdict"] == "FAIL"
    assert gate["checks"]["distinct_reasoning_ratio_met"] is False
    assert gate["distinct_reasoning_ratio"] == 0.25
    assert gate["checks"]["custody_chain_valid"] is True


def test_quality_gate_fails_a_constant_label_seat(tmp_path):
    seat = _open(tmp_path, order=LONG_ORDER)
    _fill(seat)
    seat.finalize()
    gate = C.seat_quality_gate(seat.seat_root, surfaces_by_dossier=SURFACES)
    assert gate["verdict"] == "FAIL"
    assert gate["checks"]["constant_label_surfaces_within_limit"] is False
    assert gate["constant_label_surfaces"] == [
        "SURFACE_1_SEMANTIC_EXTRACTION", "SURFACE_4_CLAIM_SUPPORT"]
    assert gate["checks"]["distinct_reasoning_ratio_met"] is True


def test_quality_gate_fails_an_incomplete_seat(tmp_path):
    seat = _open(tmp_path, order=LONG_ORDER)
    for index, dossier in enumerate(LONG_ORDER[:2]):
        seat.append(dossier_id=dossier,
                    payload=_payload(index, label="CORRECT" if index else "INCORRECT"))
    seat.finalize()
    gate = C.seat_quality_gate(seat.seat_root, surfaces_by_dossier=SURFACES)
    assert gate["verdict"] == "FAIL"
    assert gate["checks"]["all_assigned_dossiers_completed"] is False
    assert gate["missing_dossiers"] == ["dossier-3", "dossier-4"]


def test_quality_gate_fails_an_unfinalized_seat(tmp_path):
    seat = _open(tmp_path, order=LONG_ORDER)
    for index, dossier in enumerate(LONG_ORDER):
        seat.append(dossier_id=dossier,
                    payload=_payload(index, label="CORRECT" if index % 2 else "INCORRECT"))
    gate = C.seat_quality_gate(seat.seat_root, surfaces_by_dossier=SURFACES)
    assert gate["verdict"] == "FAIL"
    assert gate["checks"]["finalized_by_owning_seat"] is False
    assert gate["checks"]["all_assigned_dossiers_completed"] is True


def test_quality_gate_fails_a_seat_whose_chain_was_tampered(tmp_path):
    seat = _open(tmp_path, order=LONG_ORDER)
    for index, dossier in enumerate(LONG_ORDER):
        seat.append(dossier_id=dossier,
                    payload=_payload(index, label="CORRECT" if index % 2 else "INCORRECT"))
    seat.finalize()
    os.chmod(seat.records_path, 0o600)
    lines = seat.records_path.read_text().splitlines()
    tampered = json.loads(lines[1])
    tampered["payload"]["reasoning"] = "rewritten after the fact"
    seat.records_path.write_text(
        "\n".join([lines[0], json.dumps(tampered, sort_keys=True)] + lines[2:]) + "\n")
    gate = C.seat_quality_gate(seat.seat_root, surfaces_by_dossier=SURFACES)
    assert gate["verdict"] == "FAIL"
    assert gate["checks"]["custody_chain_valid"] is False
