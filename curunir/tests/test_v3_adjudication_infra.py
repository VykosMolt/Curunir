"""V3 adjudication infrastructure — every control, in both directions.

The machinery under test lives in
``artifacts/curunir_v6_readiness/v3_adjudication_infra/`` and implements
``artifacts/curunir_v6_readiness/v3_protocol/V3_ADJUDICATION_PROTOCOL.json``.

Two rules govern this file.

* Every control is exercised in both directions: it PERMITS the lawful path and
  it REFUSES when its protected property is violated.  A control that cannot be
  shown to fail has not been shown to exist (A2 leak_check.negative_control).
* Every fixture is synthetic.  No unit of any V3 partition is read, no real
  identity list is loaded, and no real packet is built — building one is an
  irreversible custody event under ``V3_ELIGIBILITY_PREDICATE.json`` E-01/E-02
  and is not something a test may do.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from curunir_operational import partition_custody as PC

pytestmark = pytest.mark.no_db

INFRA = (Path(PC.__file__).resolve().parent.parent / "artifacts"
         / "curunir_v6_readiness" / "v3_adjudication_infra")

if not INFRA.exists():  # pragma: no cover - checkout without the campaign tree
    pytest.skip("V3 adjudication infrastructure not present in this checkout",
                allow_module_level=True)

if str(INFRA) not in sys.path:
    sys.path.insert(0, str(INFRA))

import v3_independence_audit as independence  # noqa: E402
import v3_p8_audit as p8  # noqa: E402
import v3_packet_builder as builder  # noqa: E402
import v3_packet_schema as packet_schema  # noqa: E402
import v3_payload_schema as payload_schema  # noqa: E402
import v3_reconciliation as reconciliation  # noqa: E402
import v3_seat_roster as roster  # noqa: E402
import v3_shard_delivery as delivery  # noqa: E402
import v3_synthetic_fixtures as fixtures  # noqa: E402
import v3_unit_reader as unit_reader  # noqa: E402

SEATS = ("V3_REFERENCE_SEAT_A", "V3_REFERENCE_SEAT_B", "V3_REFERENCE_SEAT_C")
ADJUDICATION_ID = "V3-ADJ-T1"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def units():
    return fixtures.synthetic_units(8)


@pytest.fixture
def reader(units):
    return unit_reader.MappingUnitReader(units)


@pytest.fixture
def packets(units, reader):
    ids = sorted(units)
    return builder.build_packets_for_identities(
        ids, reader, expect_identity_sha256=builder.identity_sha256(ids))


@pytest.fixture
def croot(tmp_path):
    return tmp_path / "custody"


def _payloads_for(unit_ids, seat):
    return {unit_id: fixtures.synthetic_payload(unit_id, seat)
            for unit_id in unit_ids}


# ---------------------------------------------------------------------------
# A2 — what a packet may carry
# ---------------------------------------------------------------------------


def test_a_lawful_packet_set_validates(packets):
    report = packet_schema.validate_packets(packets)
    assert report["clean"]
    assert report["packets_validated"] == len(packets)
    assert report["packets_checked_against"] == ["A2_ALLOW_LIST", "A2_DENY_LIST"]


def test_the_packet_carries_exactly_the_A2_field_list(packets):
    assert set(packets[0]) == set(packet_schema.PACKET_TOP_LEVEL_KEYS)
    assert set(packets[0]["publisher"]) == set(packet_schema.PUBLISHER_KEYS)
    assert set(packets[0]["location"]) == set(packet_schema.LOCATION_KEYS)
    assert set(packets[0]["evidence_hashes"]) == set(
        packet_schema.EVIDENCE_HASH_KEYS)
    # The V2 packet carried a fourth publisher key; A2's list is exhaustive.
    assert "member_role" not in packets[0]["publisher"]


def test_production_verdicts_in_the_unit_record_never_reach_the_packet(units,
                                                                      packets):
    assert "content_region_type" in units[sorted(units)[0]]
    body = packet_schema.canonical(packets)
    for key in ("content_region_type", "role_binding_state", "admitted",
                "refusal_reason"):
        assert f'"{key}"' not in body


def test_packet_id_is_the_A2_derivation(packets):
    for packet in packets:
        assert packet["packet_id"] == packet_schema.packet_id_for(
            packet["unit_id"])
        assert packet["packet_id"].startswith("v6-v3-packet-")


@pytest.mark.parametrize("key", ["content_region_type", "subject_state",
                                 "clause_state", "admitted", "refusal_reason",
                                 "partition", "partition_of_unit", "_strata",
                                 "gold_label", "threshold", "expected_answer"])
def test_a_forbidden_key_refuses_the_whole_build(packets, key):
    leaked = [dict(packet) for packet in packets]
    leaked[0] = dict(leaked[0], **{key: "x"})
    with pytest.raises(packet_schema.LeakRefusal) as excinfo:
        packet_schema.assert_packets_clean(leaked, what="test")
    assert key in str(excinfo.value)


def test_the_validator_looks_past_the_first_packet(packets):
    """The literal V2 defect: ``packets[:1]``."""

    leaked = [dict(packet) for packet in packets]
    leaked[-1] = dict(leaked[-1], content_region_type="PRIMARY_PROPOSITION")
    assert packet_schema.validate_packets(leaked[:1])["clean"], (
        "the first packet is clean, which is exactly why a packets[:1] check "
        "passed the V2 build")
    with pytest.raises(packet_schema.LeakRefusal):
        packet_schema.assert_packets_clean(leaked, what="test")


def test_every_leaking_packet_is_reported_not_just_the_first(packets):
    leaked = [dict(packet) for packet in packets]
    leaked[0] = dict(leaked[0], clause_state="X")
    leaked[-1] = dict(leaked[-1], refusal_reason="Y")
    faults = packet_schema.validate_packets(leaked)["faults"]
    named = {fault.split(":")[0] for fault in faults}
    assert "packet[0]" in named
    assert f"packet[{len(leaked) - 1}]" in named


def test_a_missing_A2_key_is_refused(packets):
    truncated = [dict(packet) for packet in packets]
    truncated[2].pop("evidence_hashes")
    with pytest.raises(packet_schema.LeakRefusal) as excinfo:
        packet_schema.assert_packets_clean(truncated, what="test")
    assert "A2 keys absent" in str(excinfo.value)


def test_lead_in_travels_only_with_a_list_item(units, packets):
    by_unit = {packet["unit_id"]: packet for packet in packets}
    for unit_id, unit in units.items():
        packet = by_unit[unit_id]
        if unit["list_item_state"] == "LIST_ITEM":
            assert packet["lead_in_text"] == unit["lead_in_text"]
        else:
            assert packet["lead_in_text"] is None

    stale = dict(units[sorted(units)[1]], lead_in_text="a neighbour's lead-in")
    assert stale["list_item_state"] == "NOT_A_LIST_ITEM"
    built = builder.build_packet(stale)
    assert built["lead_in_text"] is None, (
        "the builder drops a stale lead-in rather than shipping a "
        "neighbouring region's text")
    with pytest.raises(packet_schema.LeakRefusal):
        packet_schema.assert_packets_clean(
            [dict(built, lead_in_text="a neighbour's lead-in")], what="test")


def test_value_scanning_is_not_applied_to_packets(units, reader):
    """A packet whose SPAN contains a forbidden word is still lawful.

    This is the other half of the V2 defect: a substring check over a packet
    fires on the source's own words.  Structural validation does not.
    """

    unit_id = sorted(units)[0]
    talkative = dict(units[unit_id],
                     span_text=("The sealed partition of the register shall be "
                                "opened by the gate keeper on the threshold."))
    packet = builder.build_packet(talkative)
    assert packet_schema.validate_packets([packet])["clean"]


# ---------------------------------------------------------------------------
# A2 — seat-facing envelopes
# ---------------------------------------------------------------------------


def test_a_partition_field_in_a_manifest_is_refused():
    line = {"adjudication_id": ADJUDICATION_ID, "seat": SEATS[0],
            "shard_id": "V3-ADJ-T1-S001", "packet_count": 1,
            "order_sha256": "0" * 64, "membership_sha256": "0" * 64,
            "event": "DELIVERED", "utc": "2026-08-09T00:00:00Z"}
    packet_schema.assert_envelope_clean(
        line, what="ok", allowed_keys=packet_schema.SEAT_MANIFEST_LINE_KEYS)
    with pytest.raises(packet_schema.LeakRefusal):
        packet_schema.assert_envelope_clean(
            dict(line, partition_of_unit={"a": "b"}), what="leaky",
            allowed_keys=packet_schema.SEAT_MANIFEST_LINE_KEYS)


def test_a_partition_field_nested_below_the_top_level_is_refused():
    obj = {"a": {"b": [{"partition_of_unit": "SEALED"}]}}
    with pytest.raises(packet_schema.LeakRefusal) as excinfo:
        packet_schema.assert_envelope_clean(obj, what="nested")
    assert "partition_of_unit" in str(excinfo.value)


def test_a_partition_label_carried_as_a_value_is_refused():
    with pytest.raises(packet_schema.LeakRefusal) as excinfo:
        packet_schema.assert_envelope_clean(
            {"state": "AWAITING_THE_SEALED_PARTITION"}, what="value")
    assert "SEALED" in str(excinfo.value)


@pytest.mark.parametrize("bad", ["V3-ADJ-SEALED", "V3-ADJ-PROSPECTIVE",
                                 "SEALED-1", "V3-ADJ-", "V3-ADJ-toolongvalue",
                                 "V3-ADJ-lower"])
def test_an_adjudication_id_that_could_label_the_partition_is_refused(bad):
    with pytest.raises(packet_schema.LeakRefusal):
        packet_schema.assert_opaque_adjudication_id(bad)


def test_an_opaque_adjudication_id_is_accepted():
    packet_schema.assert_opaque_adjudication_id("V3-ADJ-T1")


# ---------------------------------------------------------------------------
# A3 P8 — no population-enumerating builder
# ---------------------------------------------------------------------------


def test_p8_holds_for_the_shipped_builder():
    findings = p8.audit_builder_source()
    assert findings["p8_holds"], findings
    assert findings["enumeration_hits"] == []
    assert findings["population_parameter_hits"] == []
    assert findings["entry_point_takes_a_required_identity_list"]


def test_p8_audit_fires_on_a_builder_that_can_enumerate(tmp_path):
    mutated = tmp_path / "mutated.py"
    mutated.write_text(
        Path(builder.__file__).read_text(encoding="utf-8").replace(
            "def build_packets_for_identities(\n    identity_list: Sequence[str],",
            "def build_all_principal_units(population):\n"
            "    return [line for line in open(population)]\n\n\n"
            "def build_packets_for_identities(\n    identity_list: Sequence[str],"),
        encoding="utf-8")
    with pytest.raises(packet_schema.LeakRefusal) as excinfo:
        p8.assert_p8(mutated)
    assert "P8 violated" in str(excinfo.value)


def test_p8_audit_fires_on_an_optional_identity_list(tmp_path):
    mutated = tmp_path / "optional.py"
    mutated.write_text(
        Path(builder.__file__).read_text(encoding="utf-8").replace(
            "    identity_list: Sequence[str],\n    reader: UnitReader,",
            "    identity_list=None,\n    reader: UnitReader = None,"),
        encoding="utf-8")
    with pytest.raises(packet_schema.LeakRefusal):
        p8.assert_p8(mutated)


def test_the_builder_cannot_be_called_without_an_identity_list(reader):
    with pytest.raises(TypeError) as excinfo:
        builder.build_packets_for_identities()  # type: ignore[call-arg]
    assert "identity_list" in str(excinfo.value)


def test_the_builder_refuses_an_empty_or_non_list_selection(reader):
    with pytest.raises(builder.BuildRefusal):
        builder.build_packets_for_identities(
            [], reader, expect_identity_sha256=builder.identity_sha256([]))
    with pytest.raises(builder.BuildRefusal):
        builder.build_packets_for_identities(
            "/store/units.jsonl", reader,  # type: ignore[arg-type]
            expect_identity_sha256="0" * 64)


def test_the_builder_refuses_a_list_that_is_not_the_pinned_one(units, reader):
    ids = sorted(units)
    with pytest.raises(builder.BuildRefusal) as excinfo:
        builder.build_packets_for_identities(
            ids[:-1], reader,
            expect_identity_sha256=builder.identity_sha256(ids))
    assert "does not match its pin" in str(excinfo.value)


def test_the_builder_refuses_a_reader_that_answers_the_wrong_unit(units):
    ids = sorted(units)[:2]

    def crooked(unit_id):
        return units[ids[0]]

    with pytest.raises(builder.BuildRefusal) as excinfo:
        builder.build_packets_for_identities(
            ids, crooked,
            expect_identity_sha256=builder.identity_sha256(ids))
    assert "not the one" in str(excinfo.value)


def test_write_packet_store_leaves_nothing_on_disk_when_it_refuses(packets,
                                                                  tmp_path):
    target = tmp_path / "packets.jsonl"
    leaked = [dict(packet) for packet in packets]
    leaked[-1] = dict(leaked[-1], content_region_type="X")
    with pytest.raises(packet_schema.LeakRefusal):
        builder.write_packet_store(leaked, target)
    assert not target.exists()
    builder.write_packet_store(packets, target)
    assert target.exists()


# ---------------------------------------------------------------------------
# A3 P4/P5/P6 — planning and order
# ---------------------------------------------------------------------------


def test_shard_planning_is_deterministic_and_pinned(units):
    ids = sorted(units)
    pin = builder.identity_sha256(ids)
    plan = delivery.plan_shards(ids, adjudication_id=ADJUDICATION_ID,
                                expect_identity_sha256=pin, shard_size=3)
    again = delivery.plan_shards(list(reversed(ids)),
                                 adjudication_id=ADJUDICATION_ID,
                                 expect_identity_sha256=pin, shard_size=3)
    assert plan.shards == again.shards
    assert plan.shard_count == 3
    assert [len(shard) for shard in plan.shards] == [3, 3, 2]
    assert sorted(unit for shard in plan.shards for unit in shard) == ids
    with pytest.raises(delivery.DeliveryRefusal):
        delivery.plan_shards(ids[:-1], adjudication_id=ADJUDICATION_ID,
                             expect_identity_sha256=pin, shard_size=3)
    with pytest.raises(delivery.DeliveryRefusal):
        delivery.plan_shards(ids + [ids[0]], adjudication_id=ADJUDICATION_ID,
                             expect_identity_sha256=pin, shard_size=3)
    with pytest.raises(delivery.DeliveryRefusal) as excinfo:
        delivery.plan_shards((unit for unit in ids),  # type: ignore[arg-type]
                             adjudication_id=ADJUDICATION_ID,
                             expect_identity_sha256=pin, shard_size=3)
    assert "explicit sequence" in str(excinfo.value)


def test_the_frozen_shard_size_is_thirty():
    assert delivery.SHARD_SIZE == 30, "A3 P6_shard_size"


def test_seat_order_is_the_keyed_hash_and_not_an_rng(units):
    ids = sorted(units)
    for seat in SEATS:
        expected = sorted(
            ids, key=lambda uid: (packet_schema.sha256_text(f"{seat}|{uid}"),
                                  uid))
        assert delivery.seat_order(seat, ids) == expected
    orders = {seat: tuple(delivery.seat_order(seat, ids)) for seat in SEATS}
    assert len(set(orders.values())) > 1, (
        "seat-specific order must actually differ across seats")
    assert all(sorted(order) == ids for order in orders.values()), (
        "order is a permutation; membership is identical (P4)")

    # P5: "Keyed hash, not a language RNG, so a third party can reproduce it."
    # The V2 builder used random.Random(seed).shuffle.
    import ast
    for module in (delivery, builder):
        tree = ast.parse(Path(module.__file__).read_text(encoding="utf-8"))
        imported = {
            alias.name.split(".")[0]
            for node in ast.walk(tree)
            if isinstance(node, (ast.Import, ast.ImportFrom))
            for alias in node.names
        } | {node.module.split(".")[0] for node in ast.walk(tree)
             if isinstance(node, ast.ImportFrom) and node.module}
        assert "random" not in imported, (
            f"{module.__name__} imports a language RNG")
        assert "secrets" not in imported


# ---------------------------------------------------------------------------
# A3 P7 — delivery discipline
# ---------------------------------------------------------------------------


def _plan(units):
    ids = sorted(units)
    return delivery.plan_shards(
        ids, adjudication_id=ADJUDICATION_ID,
        expect_identity_sha256=builder.identity_sha256(ids), shard_size=3)


def _answer(store, plan, shard_number, seat):
    rows = [fixtures.synthetic_payload(unit_id, seat)
            for unit_id in plan.membership(shard_number)]
    delivery.payload_path(store, seat, shard_number).write_text(
        "\n".join(json.dumps(row, sort_keys=True) for row in rows) + "\n",
        encoding="utf-8")


def test_delivery_writes_only_the_current_shard(tmp_path, units, reader, croot):
    plan = _plan(units)
    store = tmp_path / "store"
    delivery.deliver_shard(plan, 1, seats=SEATS, reader=reader,
                           store_root=store, custody_root=croot)
    for seat in SEATS:
        directory = delivery.seat_directory(store, seat)
        assert [path.name for path in sorted(directory.glob("shard_*.json"))] \
            == ["shard_001.json"]

    with pytest.raises(delivery.DeliveryRefusal) as excinfo:
        delivery.deliver_shard(plan, 2, seats=SEATS, reader=reader,
                               store_root=store, custody_root=croot)
    assert "is not written until shard 1 is sealed" in str(excinfo.value)


def test_a_shard_is_never_delivered_twice(tmp_path, units, reader, croot):
    plan = _plan(units)
    store = tmp_path / "store"
    delivery.deliver_shard(plan, 1, seats=SEATS, reader=reader,
                           store_root=store, custody_root=croot)
    with pytest.raises(delivery.DeliveryRefusal) as excinfo:
        delivery.deliver_shard(plan, 1, seats=SEATS, reader=reader,
                               store_root=store, custody_root=croot)
    assert "already been delivered" in str(excinfo.value)


def test_the_cohort_gate_waits_for_the_slowest_seat(tmp_path, units, reader,
                                                    croot):
    plan = _plan(units)
    store = tmp_path / "store"
    delivery.deliver_shard(plan, 1, seats=SEATS, reader=reader,
                           store_root=store, custody_root=croot)
    for seat in SEATS[:2]:
        _answer(store, plan, 1, seat)
        delivery.seal_shard(plan, 1, seat, store_root=store, custody_root=croot)

    with pytest.raises(delivery.DeliveryRefusal):
        delivery.deliver_shard(plan, 2, seats=SEATS, reader=reader,
                               store_root=store, custody_root=croot)

    _answer(store, plan, 1, SEATS[2])
    delivery.seal_shard(plan, 1, SEATS[2], store_root=store, custody_root=croot)
    result = delivery.deliver_shard(plan, 2, seats=SEATS, reader=reader,
                                    store_root=store, custody_root=croot)
    assert result["shard_number"] == 2


def test_a_shard_may_not_be_leapt_over(tmp_path, units, reader, croot):
    plan = _plan(units)
    store = tmp_path / "store"
    delivery.deliver_shard(plan, 1, seats=SEATS, reader=reader,
                           store_root=store, custody_root=croot)
    for seat in SEATS:
        _answer(store, plan, 1, seat)
        delivery.seal_shard(plan, 1, seat, store_root=store, custody_root=croot)
    with pytest.raises(delivery.DeliveryRefusal) as excinfo:
        delivery.deliver_shard(plan, 3, seats=SEATS, reader=reader,
                               store_root=store, custody_root=croot)
    assert "requires exactly [1, 2] sealed first" in str(excinfo.value)


def test_delivery_is_refused_when_the_ledger_does_not_verify(tmp_path, units,
                                                             reader, croot):
    plan = _plan(units)
    store = tmp_path / "store"
    delivery.deliver_shard(plan, 1, seats=SEATS, reader=reader,
                           store_root=store, custody_root=croot)
    log = PC.custody_root(croot).access_log
    lines = log.read_text().splitlines()
    log.chmod(0o600)
    log.write_text("\n".join(lines[:-1]) + "\n")
    with pytest.raises(delivery.DeliveryRefusal) as excinfo:
        delivery.delivery_position(ADJUDICATION_ID, SEATS[0],
                                   custody_root=croot)
    assert "does not verify" in str(excinfo.value)


def test_every_write_and_seal_is_in_the_append_only_log(tmp_path, units,
                                                        reader, croot):
    plan = _plan(units)
    store = tmp_path / "store"
    delivery.deliver_shard(plan, 1, seats=SEATS, reader=reader,
                           store_root=store, custody_root=croot)
    _answer(store, plan, 1, SEATS[0])
    delivery.seal_shard(plan, 1, SEATS[0], store_root=store, custody_root=croot)

    log = PC.FirstAccessLog(PC.custody_root(croot))
    assert log.verify()["verified"]
    events = log.events_for(delivery.seat_store_id(ADJUDICATION_ID, SEATS[0]))
    kinds = [event["event"] for event in events]
    assert kinds == [delivery.EVENT_WRITE, delivery.EVENT_SEAL]
    for event in events:
        assert event["utc"]
        detail = event["detail"]
        assert detail["seat"] == SEATS[0]
        assert detail["shard_id"] == plan.shard_id(1)
        assert detail["packet_count"] == len(plan.membership(1))


def test_a_denied_delivery_is_itself_recorded(tmp_path, units, reader, croot):
    plan = _plan(units)
    store = tmp_path / "store"
    delivery.deliver_shard(plan, 1, seats=SEATS, reader=reader,
                           store_root=store, custody_root=croot)
    with pytest.raises(delivery.DeliveryRefusal):
        delivery.deliver_shard(plan, 2, seats=SEATS, reader=reader,
                               store_root=store, custody_root=croot)
    log = PC.FirstAccessLog(PC.custody_root(croot))
    denied = [event for event in log.events_for(f"V3ADJ:{ADJUDICATION_ID}")
              if event["event"] == delivery.EVENT_DENIED]
    assert len(denied) == 1
    assert denied[0]["detail"]["shard_number"] == 2


def test_parity_and_membership_across_seats(tmp_path, units, reader, croot):
    plan = _plan(units)
    store = tmp_path / "store"
    result = delivery.deliver_shard(plan, 1, seats=SEATS, reader=reader,
                                    store_root=store, custody_root=croot)
    assert result["parity"]["packet_objects_byte_identical"]
    assert result["parity"]["membership_identical"]

    envelopes = {}
    for seat in SEATS:
        path = delivery.seat_directory(store, seat) / "shard_001.json"
        envelopes[seat] = json.loads(path.read_text())
    by_unit = {seat: {packet["unit_id"]: packet_schema.canonical(packet)
                      for packet in envelope["packets"]}
               for seat, envelope in envelopes.items()}
    for unit_id in plan.membership(1):
        assert len({by_unit[seat][unit_id] for seat in SEATS}) == 1
    orders = {seat: tuple(packet["unit_id"]
                          for packet in envelopes[seat]["packets"])
              for seat in SEATS}
    assert len(set(orders.values())) > 1


def test_a_tampered_seat_copy_fails_parity(tmp_path, units, reader, croot):
    plan = _plan(units)
    store = tmp_path / "store"
    delivery.deliver_shard(plan, 1, seats=SEATS, reader=reader,
                           store_root=store, custody_root=croot)
    path = delivery.seat_directory(store, SEATS[1]) / "shard_001.json"
    path.chmod(0o600)
    envelope = json.loads(path.read_text())
    envelope["packets"][0]["span_text"] += " inserted"
    path.write_text(json.dumps(envelope))
    with pytest.raises(delivery.DeliveryRefusal) as excinfo:
        delivery.verify_parity(plan, 1, seats=SEATS, store_root=store)
    assert "ORDER only" in str(excinfo.value)


def test_the_seat_manifest_and_cursor_carry_no_corpus_summary(tmp_path, units,
                                                              reader, croot):
    plan = _plan(units)
    store = tmp_path / "store"
    delivery.deliver_shard(plan, 1, seats=SEATS, reader=reader,
                           store_root=store, custody_root=croot)
    directory = delivery.seat_directory(store, SEATS[0])
    cursor = json.loads((directory / "cursor.json").read_text())
    assert set(cursor) == set(packet_schema.CURSOR_KEYS)
    body = (directory / "seat_manifest.jsonl").read_text()
    assert str(plan.shard_count) not in json.loads(body.splitlines()[0]).keys()
    for line in body.splitlines():
        record = json.loads(line)
        assert set(record) == set(packet_schema.SEAT_MANIFEST_LINE_KEYS)
        packet_schema.assert_envelope_clean(
            record, what="manifest",
            allowed_keys=packet_schema.SEAT_MANIFEST_LINE_KEYS)


def test_sealing_refuses_an_incomplete_or_invalid_shard(tmp_path, units,
                                                        reader, croot):
    plan = _plan(units)
    store = tmp_path / "store"
    delivery.deliver_shard(plan, 1, seats=SEATS, reader=reader,
                           store_root=store, custody_root=croot)
    membership = plan.membership(1)

    rows = [fixtures.synthetic_payload(unit_id, SEATS[0])
            for unit_id in membership[:-1]]
    delivery.payload_path(store, SEATS[0], 1).write_text(
        "\n".join(json.dumps(row, sort_keys=True) for row in rows) + "\n")
    with pytest.raises(delivery.DeliveryRefusal) as excinfo:
        delivery.seal_shard(plan, 1, SEATS[0], store_root=store,
                            custody_root=croot)
    assert "were not answered" in str(excinfo.value)

    rows = [fixtures.synthetic_payload(unit_id, SEATS[0])
            for unit_id in membership]
    rows[0]["subject_text"] = "a subject that is nowhere in the evidence"
    delivery.payload_path(store, SEATS[0], 1).write_text(
        "\n".join(json.dumps(row, sort_keys=True) for row in rows) + "\n")
    with pytest.raises(delivery.DeliveryRefusal) as excinfo:
        delivery.seal_shard(plan, 1, SEATS[0], store_root=store,
                            custody_root=croot)
    assert "does not occur in the packet evidence" in str(excinfo.value)
    assert "a subject that is nowhere" not in str(excinfo.value), (
        "a refusal message must not echo the text it refused")

    _answer(store, plan, 1, SEATS[0])
    sealed = delivery.seal_shard(plan, 1, SEATS[0], store_root=store,
                                 custody_root=croot)
    assert sealed["packets"] == len(membership)
    with pytest.raises(delivery.DeliveryRefusal):
        delivery.seal_shard(plan, 1, SEATS[0], store_root=store,
                            custody_root=croot)


def test_stores_must_be_physically_separate(tmp_path):
    root = tmp_path / "prospective"
    delivery.assert_store_separation(root, [tmp_path / "sealed"])
    with pytest.raises(delivery.DeliveryRefusal):
        delivery.assert_store_separation(root, [root / "inner"])
    with pytest.raises(delivery.DeliveryRefusal):
        delivery.assert_store_separation(root, [root])


# ---------------------------------------------------------------------------
# A1 — the seat roster
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("roles,holds", [
    (["V3_REFERENCE_SEAT"], True),
    (["V3_REFERENCE_ADJUDICATOR"], True),
    (["NONE_IN_THIS_CAMPAIGN"], True),
    (["V3_REFERENCE_SEAT", "PRODUCTION"], False),
    (["REPAIR"], False),
    (["TUNING"], False),
    (["HARNESS"], False),
    (["SELECTOR"], False),
    (["RULER"], False),
    (["THRESHOLD"], False),
    (["GATE_AUTHORING"], False),
    (["VERIFIER"], False),
    (["EXECUTOR"], False),
    (["SOMETHING_ELSE"], False),
    ([], False),
    ("V3_REFERENCE_SEAT", False),
])
def test_the_role_exclusion_is_a_predicate(roles, holds):
    assert roster.role_exclusion_holds(roles)[0] is holds


def test_all_ten_excluded_roles_are_present():
    assert set(roster.EXCLUDED_ROLES) == {
        "PRODUCTION", "REPAIR", "TUNING", "HARNESS", "SELECTOR", "RULER",
        "THRESHOLD", "GATE_AUTHORING", "VERIFIER", "EXECUTOR"}


def _engage(directory, croot, seat_id, kind, identity, roles, working,
            **kwargs):
    return roster.append_seat(
        directory, seat_id=seat_id, kind=kind, identity=identity,
        declared_roles=roles, private_working_directory=str(working),
        permanent_quarantine_acknowledged=kwargs.pop("quarantine", True),
        custody_root=croot, **kwargs)


def test_the_roster_permits_and_refuses(tmp_path, croot):
    directory = tmp_path / "roster"
    directory.mkdir()
    roster.create_scaffold(directory, note="test")
    assert roster.roster_is_complete_for_delivery(directory)[0] is False

    for index, seat in enumerate(SEATS):
        _engage(directory, croot, seat, "PRIMARY_SEAT", f"identity-{index}",
                ["V3_REFERENCE_SEAT"], tmp_path / f"seat_{index}")
    _engage(directory, croot, roster.ADJUDICATOR_ID, "ADJUDICATOR",
            "identity-adj", ["V3_REFERENCE_ADJUDICATOR"], tmp_path / "adj")
    assert roster.roster_is_complete_for_delivery(directory)[0] is True
    assert roster.engaged_seats(directory) == list(SEATS)

    with pytest.raises(roster.RosterRefusal):
        _engage(directory, croot, SEATS[0], "PRIMARY_SEAT", "identity-new",
                ["V3_REFERENCE_SEAT"], tmp_path / "seat_new")
    with pytest.raises(roster.RosterRefusal) as excinfo:
        _engage(directory, croot, "V3_REFERENCE_SEAT_D", "REPLACEMENT_SEAT",
                "identity-0", ["V3_REFERENCE_SEAT"], tmp_path / "seat_d",
                replaces=SEATS[0])
    assert "already holds a seat" in str(excinfo.value)
    with pytest.raises(roster.RosterRefusal):
        _engage(directory, croot, "V3_REFERENCE_SEAT_D", "REPLACEMENT_SEAT",
                "identity-d", ["V3_REFERENCE_SEAT"], tmp_path / "seat_0" / "in",
                replaces=SEATS[0])
    with pytest.raises(roster.RosterRefusal):
        _engage(directory, croot, "V3_REFERENCE_SEAT_D", "REPLACEMENT_SEAT",
                "identity-d", ["V3_REFERENCE_SEAT"], Path("relative/path"),
                replaces=SEATS[0])


def test_the_roster_is_append_only(tmp_path, croot):
    directory = tmp_path / "roster"
    directory.mkdir()
    roster.create_scaffold(directory, note="test")
    _engage(directory, croot, SEATS[0], "PRIMARY_SEAT", "identity-0",
            ["V3_REFERENCE_SEAT"], tmp_path / "seat_0")
    _engage(directory, croot, SEATS[1], "PRIMARY_SEAT", "identity-1",
            ["V3_REFERENCE_SEAT"], tmp_path / "seat_1")

    path = directory / roster.ROSTER_FILENAME
    good = json.loads(path.read_text())

    edited = json.loads(json.dumps(good))
    edited["entries"][0]["declared_roles"] = ["V3_REFERENCE_SEAT", "PRODUCTION"]
    path.write_text(json.dumps(edited))
    with pytest.raises(roster.RosterRefusal) as excinfo:
        roster.read_roster(directory)
    assert "altered" in str(excinfo.value)

    dropped = json.loads(json.dumps(good))
    dropped["entries"] = dropped["entries"][:1]
    path.write_text(json.dumps(dropped))
    with pytest.raises(roster.RosterRefusal) as excinfo:
        roster.read_roster(directory)
    assert "chain head" in str(excinfo.value)

    path.write_text(json.dumps(good))
    assert len(roster.read_roster(directory)["entries"]) == 2


def test_a_roster_append_is_recorded_in_the_ledger(tmp_path, croot):
    directory = tmp_path / "roster"
    directory.mkdir()
    roster.create_scaffold(directory, note="test")
    _engage(directory, croot, SEATS[0], "PRIMARY_SEAT", "identity-0",
            ["V3_REFERENCE_SEAT"], tmp_path / "seat_0")
    log = PC.FirstAccessLog(PC.custody_root(croot))
    events = log.events_for("V3_SEAT_ROSTER")
    assert [event["event"] for event in events] == ["SEAT_ROSTER_APPEND"]
    assert events[0]["detail"]["seat_id"] == SEATS[0]


def test_the_scaffold_is_never_recreated(tmp_path):
    directory = tmp_path / "roster"
    directory.mkdir()
    roster.create_scaffold(directory)
    with pytest.raises(roster.RosterRefusal):
        roster.create_scaffold(directory)


# ---------------------------------------------------------------------------
# A4 — independence
# ---------------------------------------------------------------------------


def test_the_audit_thresholds_are_the_frozen_ones():
    """A4 B-3 froze these before any V3 payload existed.  Do not tune."""

    assert independence.FIELD_AGREEMENT_ASYMMETRY_LIMIT == 0.10
    assert independence.SHARED_RATIONALE_LIMIT == 1


def test_the_audit_passes_on_independent_seats(units):
    unit_ids = sorted(units)
    payloads = {seat: _payloads_for(unit_ids, seat) for seat in SEATS}
    report = independence.audit_pairs(payloads)
    assert report["verdict"] == "PASS"
    assert report["units_judged_by_all_three"] == len(unit_ids)
    assert len(report["pairs"]) == 3
    for row in report["pairs"].values():
        assert row["identical_rationale_strings"] == 0


def test_one_shared_rationale_string_indicates_contamination(units):
    unit_ids = sorted(units)
    payloads = {seat: _payloads_for(unit_ids, seat) for seat in SEATS}
    payloads[SEATS[0]][unit_ids[0]]["rationale"] = "a shared sentence"
    payloads[SEATS[2]][unit_ids[4]]["rationale"] = "a shared sentence"
    with pytest.raises(independence.IndependenceRefusal) as excinfo:
        independence.assert_independent(payloads)
    assert "rationale" in str(excinfo.value)


def test_asymmetric_agreement_indicates_contamination(units):
    unit_ids = sorted(units)
    payloads = {seat: _payloads_for(unit_ids, seat) for seat in SEATS}
    for unit_id in unit_ids[1:]:
        payloads[SEATS[2]][unit_id].update({
            "subject_state": "SUBJECT_UNRESOLVED",
            "predicate_state": "PREDICATE_UNRESOLVED",
            "role_binding_terminal_state": "ROLE_BINDING_UNRESOLVED",
            "final_extraction_disposition": "QUARANTINED",
            "antecedent_state": "ANTECEDENT_ABSENT",
        })
    report = independence.audit_pairs(payloads)
    assert report["contamination_indicated"]
    assert report["quarantined_pairs"] == [f"{SEATS[0]}|{SEATS[1]}"]


def test_a_small_asymmetry_does_not_indicate_contamination(units):
    unit_ids = sorted(units)
    payloads = {seat: _payloads_for(unit_ids, seat) for seat in SEATS}
    # One field of one unit differs for seat C: far below 0.10 absolute.
    payloads[SEATS[2]][unit_ids[0]]["antecedent_state"] = "ANTECEDENT_ABSENT"
    report = independence.audit_pairs(payloads)
    assert not report["contamination_indicated"], report["pairs"]


def test_the_audit_refuses_an_empty_intersection():
    payloads = {seat: {} for seat in SEATS}
    with pytest.raises(independence.IndependenceRefusal) as excinfo:
        independence.audit_pairs(payloads)
    assert "not a PASS" in str(excinfo.value)


# ---------------------------------------------------------------------------
# A5 — comparison and routing
# ---------------------------------------------------------------------------


def test_comparison_is_whitespace_normalised_and_case_sensitive():
    assert (payload_schema.comparable("subject_text", "The  Authority")
            == payload_schema.comparable("subject_text", "The Authority"))
    assert (payload_schema.comparable("subject_text", "The Authority")
            != payload_schema.comparable("subject_text", "the authority"))


def test_lists_are_compared_as_sorted_sets():
    left = payload_schema.comparable("required_context_ids",
                                     ["HEADING", "LEAD_IN"])
    right = payload_schema.comparable("required_context_ids",
                                      ["LEAD_IN", "HEADING", "LEAD_IN"])
    assert left == right


def test_prose_requirement_fields_compare_at_decision_granularity():
    a = payload_schema.comparable(
        "repair_requirement", "bind the predicate from the coordinating lead-in")
    b = payload_schema.comparable(
        "repair_requirement", "inherit the predicate from the lead-in paragraph")
    assert a == b == "REQUIRED"
    assert payload_schema.comparable("repair_requirement", "") == "NONE"
    assert a != payload_schema.comparable("repair_requirement", "")


def test_the_rationale_is_not_a_material_field():
    assert payload_schema.RATIONALE_FIELD not in payload_schema.MATERIAL_FIELDS
    assert set(payload_schema.MATERIAL_FIELDS) | {"rationale"} == set(
        payload_schema.FIELDS)


def test_the_payload_schema_is_declared_provisional():
    """It is derived from a V2 precedent, not frozen V3 protocol."""

    assert payload_schema.PAYLOAD_SCHEMA_STATUS == (
        "PROVISIONAL_NOT_FROZEN_AT_PROTOCOL_LEVEL")


def test_material_fields_match_the_v2_precedent_a5_cites():
    precedent = (Path(PC.__file__).resolve().parent.parent / "artifacts"
                 / "curunir_autonomous_completion_v5_8_1_20260725"
                 / "55_d25_role_binding_reference" / "freeze" / "schema.py")
    if not precedent.exists():  # pragma: no cover
        pytest.skip("the V2 precedent is not present in this checkout")
    source = precedent.read_text(encoding="utf-8")
    for field in payload_schema.MATERIAL_FIELDS:
        assert f'"{field}"' in source, (
            f"{field} is not in the precedent A5 names; the V3 payload schema "
            "has drifted from its declared provenance")


def test_a_2_1_split_goes_to_the_adjudicator(tmp_path, units, reader):
    unit_ids = sorted(units)[:3]
    payloads = {seat: _payloads_for(unit_ids, seat) for seat in SEATS}
    payloads[SEATS[1]][unit_ids[0]]["predicate_head_text"] = "register"
    store = reconciliation.AdjudicationStore(tmp_path / "adj")
    summary = reconciliation.route(payloads, reader=reader, store=store)

    assert summary["disputed_new"] == 1
    assert summary["unanimous_new"] == 2
    assert summary["majority_used_as_a_resolution_rule"] is False
    body = (store.shards / "adjudication_001.json").read_text()
    assert "majority" not in body
    shard = json.loads(body)
    assert len(shard["units"]) == 1
    assert set(shard["units"][0]["seat_payloads"]) == set(SEATS)
    assert shard["units"][0]["differing_fields"] == ["predicate_head_text"]
    # The adjudicator sees the evidence, and the evidence is a lawful packet.
    assert packet_schema.validate_packets(
        [shard["units"][0]["evidence"]])["clean"]


def test_a_terminal_agreement_does_not_suppress_a_component_disagreement(units):
    unit_id = sorted(units)[0]
    payloads = {seat: fixtures.synthetic_payload(unit_id, seat)
                for seat in SEATS}
    payloads[SEATS[2]]["subject_text"] = "The Synthetic Authority"
    assert all(payload["role_binding_terminal_state"]
               == "ROLE_BINDING_ESTABLISHED" for payload in payloads.values())
    assert reconciliation.compare_unit(payloads) == ["subject_text"]


def test_a_unit_missing_a_seat_does_not_enter_the_reference(tmp_path, units,
                                                            reader):
    unit_ids = sorted(units)[:2]
    payloads = {seat: _payloads_for(unit_ids, seat) for seat in SEATS}
    lonely = sorted(units)[5]
    payloads[SEATS[0]][lonely] = fixtures.synthetic_payload(lonely, SEATS[0])
    store = reconciliation.AdjudicationStore(tmp_path / "adj")
    summary = reconciliation.route(payloads, reader=reader, store=store)
    assert summary["units_judged_by_all_three"] == 2
    assert summary["units_missing_a_seat_payload"] == 1
    rows = [json.loads(line) for line in
            store.unanimous.read_text().splitlines()]
    assert lonely not in {row["unit_id"] for row in rows}


def test_an_adjudicated_unit_is_never_routed_again(tmp_path, units, reader):
    unit_ids = sorted(units)[:2]
    payloads = {seat: _payloads_for(unit_ids, seat) for seat in SEATS}
    payloads[SEATS[1]][unit_ids[0]]["predicate_head_text"] = "register"
    store = reconciliation.AdjudicationStore(tmp_path / "adj")
    reconciliation.route(payloads, reader=reader, store=store)

    decision = {
        "unit_id": unit_ids[0],
        "resolution_action": "ADJUDICATOR_INDEPENDENT",
        "resolved_decision": {
            field: payloads[SEATS[0]][unit_ids[0]].get(field)
            for field in payload_schema.MATERIAL_FIELDS},
        "adjudicator_rationale": "the evidence decides the head",
        "preserved_dissent": [],
    }
    reconciliation.record_adjudications([decision], store=store)
    again = reconciliation.route(payloads, reader=reader, store=store)
    assert again["disputed_new"] == 0
    assert again["unanimous_new"] == 0
    with pytest.raises(reconciliation.ReconciliationRefusal):
        reconciliation.record_adjudications([decision], store=store)


def test_adjudication_shard_numbering_is_append_only(tmp_path, units, reader):
    unit_ids = sorted(units)
    payloads = {seat: _payloads_for(unit_ids[:2], seat) for seat in SEATS}
    payloads[SEATS[1]][unit_ids[0]]["predicate_head_text"] = "register"
    store = reconciliation.AdjudicationStore(tmp_path / "adj")
    first = reconciliation.route(payloads, reader=reader, store=store)
    assert first["adjudication_shards_written"][0]["shard_id"] == (
        "V3_ADJUDICATION-S001")

    for seat in SEATS:
        payloads[seat][unit_ids[4]] = fixtures.synthetic_payload(unit_ids[4],
                                                                 seat)
    payloads[SEATS[2]][unit_ids[4]]["semantic_actor_state"] = "NO_SEMANTIC_ACTOR"
    second = reconciliation.route(payloads, reader=reader, store=store)
    assert second["adjudication_shards_written"][0]["shard_id"] == (
        "V3_ADJUDICATION-S002")
    assert store.existing_shard_numbers() == [1, 2]


@pytest.mark.parametrize("bad,reason", [
    ({"resolution_action": "MAJORITY"}, "resolution_action"),
    ({"adjudicator_rationale": "x" * 301}, "300"),
    ({"adjudicator_rationale": ""}, "rationale"),
])
def test_the_adjudicator_is_bound_by_the_a5_constraints(tmp_path, units, reader,
                                                        bad, reason):
    unit_ids = sorted(units)[:2]
    payloads = {seat: _payloads_for(unit_ids, seat) for seat in SEATS}
    payloads[SEATS[1]][unit_ids[0]]["predicate_head_text"] = "register"
    store = reconciliation.AdjudicationStore(tmp_path / "adj")
    reconciliation.route(payloads, reader=reader, store=store)
    decision = {
        "unit_id": unit_ids[0],
        "resolution_action": "ADJUDICATOR_INDEPENDENT",
        "resolved_decision": {field: "" for field
                              in payload_schema.MATERIAL_FIELDS},
        "adjudicator_rationale": "ok",
        "preserved_dissent": [],
    }
    decision.update(bad)
    with pytest.raises(reconciliation.ReconciliationRefusal) as excinfo:
        reconciliation.record_adjudications([decision], store=store)
    assert reason in str(excinfo.value)


def test_the_six_adjudicator_options_are_the_frozen_ones():
    assert reconciliation.ADJUDICATOR_OPTIONS == (
        "ADOPTED_SEAT_A", "ADOPTED_SEAT_B", "ADOPTED_SEAT_C",
        "COMPONENT_MAJORITY", "ADJUDICATOR_INDEPENDENT", "PACKET_DEFECT")


# ---------------------------------------------------------------------------
# A6 — assembly and freeze
# ---------------------------------------------------------------------------


def test_the_reference_excludes_packet_defects_and_is_written_once(tmp_path,
                                                                   units,
                                                                   reader):
    unit_ids = sorted(units)[:3]
    payloads = {seat: _payloads_for(unit_ids, seat) for seat in SEATS}
    payloads[SEATS[1]][unit_ids[0]]["predicate_head_text"] = "register"
    payloads[SEATS[2]][unit_ids[1]]["subject_state"] = "SUBJECT_UNRESOLVED"
    store = reconciliation.AdjudicationStore(tmp_path / "adj")
    reconciliation.route(payloads, reader=reader, store=store)

    reconciliation.record_adjudications([
        {"unit_id": unit_ids[0],
         "resolution_action": "ADJUDICATOR_INDEPENDENT",
         "resolved_decision": {
             field: payloads[SEATS[0]][unit_ids[0]].get(field)
             for field in payload_schema.MATERIAL_FIELDS},
         "adjudicator_rationale": "resolved on the evidence",
         "preserved_dissent": []},
        {"unit_id": unit_ids[1],
         "resolution_action": "PACKET_DEFECT",
         "adjudicator_rationale": "the span is truncated",
         "preserved_dissent": []},
    ], store=store)

    freeze = reconciliation.assemble_reference(
        store, pinned_draw_record_sha256="a" * 64, substitutions_called=1)
    assert freeze["reference_row_count"] == 2
    assert freeze["packet_defect_count"] == 1
    rows = [json.loads(line) for line in store.reference.read_text().splitlines()]
    assert {row["unit_id"] for row in rows} == {unit_ids[0], unit_ids[2]}
    assert unit_ids[1] not in {row["unit_id"] for row in rows}

    with pytest.raises(reconciliation.ReconciliationRefusal) as excinfo:
        reconciliation.assemble_reference(store,
                                          pinned_draw_record_sha256="a" * 64)
    assert "already exists" in str(excinfo.value)


def test_the_freeze_record_discloses_nothing(tmp_path, units, reader):
    unit_ids = sorted(units)[:2]
    payloads = {seat: _payloads_for(unit_ids, seat) for seat in SEATS}
    store = reconciliation.AdjudicationStore(tmp_path / "adj")
    reconciliation.route(payloads, reader=reader, store=store)
    freeze = reconciliation.assemble_reference(
        store, pinned_draw_record_sha256="b" * 64)
    body = json.dumps(freeze)
    for unit_id in unit_ids:
        assert unit_id not in body
    assert "span_text" not in body
    reconciliation.assert_freeze_record_discloses_nothing(freeze)
    with pytest.raises(reconciliation.ReconciliationRefusal):
        reconciliation.assert_freeze_record_discloses_nothing(
            dict(freeze, reference_sha256="v6-v3-unit-000000000000000000000000"))
    with pytest.raises(reconciliation.ReconciliationRefusal):
        reconciliation.assert_freeze_record_discloses_nothing(
            dict(freeze, unit_ids=["x"]))


# ---------------------------------------------------------------------------
# Custody integration
# ---------------------------------------------------------------------------


def test_the_authorised_reader_never_deserializes_an_unnamed_unit(tmp_path,
                                                                  units, croot,
                                                                  monkeypatch):
    store = tmp_path / "units.jsonl"
    store.write_text("\n".join(json.dumps(unit, sort_keys=True)
                               for unit in units.values()) + "\n")
    named = sorted(units)[:2]
    calls = []
    real_loads = json.loads
    monkeypatch.setattr(json, "loads",
                        lambda *a, **k: (calls.append(1), real_loads(*a, **k))[1])
    reader = unit_reader.AuthorisedUnitReader(store, named, custody_root=croot)
    monkeypatch.undo()
    assert len(calls) == len(named)
    assert reader(named[0])["unit_id"] == named[0]
    with pytest.raises(unit_reader.ReaderRefusal):
        reader(sorted(units)[5])


def test_the_custody_layer_refuses_a_registered_holdout_identity(tmp_path,
                                                                 units, croot):
    """The finding: there is no adjudication-read path today.

    ``load_authorised_packets`` refuses any registered holdout identity, which
    is what every drawn V3 unit is; ``open_sealed_partition`` would allow the
    read but consumes the one-shot lock that A8 reserves for the single sealed
    OPENING.  Recorded, not repaired: partition_custody.py is out of scope.
    """

    store = tmp_path / "units.jsonl"
    store.write_text("\n".join(json.dumps(unit, sort_keys=True)
                               for unit in units.values()) + "\n")
    ids = sorted(units)
    PC.declare_ledger_scope("TEST_CORPUS", note="synthetic", root=croot)
    unit_reader.AuthorisedUnitReader(store, ids, custody_root=croot)

    PC.register_holdout_units("TEST_PARTITION", ids, corpus_id="TEST_CORPUS",
                              blinded=True, root=croot)
    with pytest.raises(PC.CustodyViolation) as excinfo:
        unit_reader.AuthorisedUnitReader(store, ids, custody_root=croot)
    assert "registered holdout units" in str(excinfo.value)
    assert not any(unit_id in str(excinfo.value) for unit_id in ids)


# ---------------------------------------------------------------------------
# The shipped evidence
# ---------------------------------------------------------------------------


def test_the_negative_control_evidence_shows_every_control_firing():
    evidence = json.loads((INFRA / "NEGATIVE_CONTROL_EVIDENCE.json")
                          .read_text(encoding="utf-8"))
    assert evidence["verdict"] == "ALL_CONTROLS_FIRED"
    assert evidence["controls_that_did_not_fire"] == []
    assert evidence["harness_error"] is None
    assert evidence["controls_fired"] == evidence["controls_total"]
    controls = {row["control"] for row in evidence["controls"]}
    # The four controls the contract names by hand.
    assert "NC-01-FORBIDDEN-KEY-IN-PACKET" in controls
    assert "NC-05-PARTITION-FIELD-IN-A-MANIFEST" in controls
    assert "NC-14-P8-AUDIT-FIRES-ON-AN-ENUMERATING-BUILDER" in controls
    assert "NC-25-SHARD-N-PLUS-1-BEFORE-SHARD-N-IS-SEALED" in controls
    for row in evidence["controls"]:
        assert row["fired"] is True, row


def test_the_seat_roster_scaffold_is_shipped_and_empty():
    scaffold = json.loads((INFRA / "SEAT_ROSTER.json")
                          .read_text(encoding="utf-8"))
    assert scaffold["record"] == "V3_SEAT_ROSTER"
    assert scaffold["append_only"] is True
    assert scaffold["entries"] == [], (
        "no seat has been engaged; engaging one is a programme act")
    assert scaffold["chain_head"] == roster.GENESIS
    assert set(scaffold["excluded_roles"]) == set(roster.EXCLUDED_ROLES)


def test_no_real_packet_store_has_been_built():
    """The whole point of this directory: machinery, not delivery."""

    for pattern in ("**/shard_*.json", "**/*packets*.jsonl",
                    "**/final_reference.jsonl"):
        assert list(INFRA.glob(pattern)) == [], (
            "an artifact that looks like a delivered packet exists under the "
            "infrastructure directory")
    assert not (INFRA / "_control_run").exists(), (
        "the negative-control scratch tree is removed when the run ends")
