from dataclasses import FrozenInstanceError

import pytest

from halo_soundbar_service.protocol import (
    AmbientState,
    AmbientStateCollector,
    LightEntry,
    ParsedFrame,
    PowerTarget,
    ProtocolError,
    build_frame,
    build_power_packet,
    build_query_packet,
    parse_frame,
    parse_response_frame,
    style_fingerprint,
)


RAW_1 = bytes.fromhex(
    "2FBBECE3002802010C0301FFE6FE64FF0201000064080300000064FF"
    "04B476FF64FF0501000064FF06B139FF6408"
    "000000000000000000000000000000000000"
)
RAW_2 = bytes.fromhex(
    "2FBBECE3002802020C030700000064FF0800000064FF0901000064FF"
    "0A00000064FF0B000001FFFF0C000001FFFF"
    "000000000000000000000000000000000000"
)


def _response_frame(command, payload):
    return (
        b"\x2f\xbb\xec" + bytes((command,))
        + len(payload).to_bytes(2, "big") + payload
    ).ljust(64, b"\x00")


@pytest.mark.parametrize("raw", [RAW_1, RAW_2])
def test_parse_response_frame_accepts_exact_real_device_fixture(raw):
    frame = parse_response_frame(raw)

    assert frame == ParsedFrame(command=0xE3, payload=raw[6:46])
    with pytest.raises(FrozenInstanceError):
        frame.command = 0x6A


def test_real_device_fixtures_collect_complete_ambient_state():
    collector = AmbientStateCollector()

    assert collector.feed(parse_response_frame(RAW_1)) is None
    state = collector.feed(parse_response_frame(RAW_2))

    assert state is not None
    assert state.array_index == 0x0C
    assert state.effect_id == 0x03
    assert set(state.entries) == set(range(0x01, 0x0D))
    assert state.entries[0x0B] == LightEntry(0x0B, 0, 0, 1, 255, 255)
    assert state.entries[0x0C] == LightEntry(0x0C, 0, 0, 1, 255, 255)


@pytest.mark.parametrize(
    ("mutation", "reason"),
    [
        ("report_id", "header"), ("header", "header"),
        ("host_header", "header"), ("length", "capacity"),
        ("little_endian_length", "capacity"), ("truncation", "exactly 64"),
        ("oversized_report", "exactly 64"),
        ("payload_end_padding", "padding"), ("last_padding", "padding"),
    ],
)
def test_parse_response_frame_rejects_malformed_real_fixture(mutation, reason):
    report = bytearray(RAW_1)
    if mutation == "report_id":
        report[0] = 0x2E
    elif mutation == "header":
        report[1] = 0
    elif mutation == "host_header":
        report[:3] = bytes.fromhex("2EAAED")
    elif mutation == "length":
        report[4:6] = bytes.fromhex("003B")
    elif mutation == "little_endian_length":
        report[4:6] = bytes.fromhex("2800")
    elif mutation == "truncation":
        report.pop()
    elif mutation == "oversized_report":
        report.append(0)
    elif mutation == "payload_end_padding":
        report[46] = 1
    else:
        report[63] = 1

    with pytest.raises(ProtocolError, match=reason):
        parse_response_frame(bytes(report))


@pytest.mark.parametrize("report", [bytearray(RAW_1), memoryview(RAW_1), None, "raw"])
def test_parse_response_frame_requires_bytes(report):
    with pytest.raises(ProtocolError, match="must be bytes"):
        parse_response_frame(report)


@pytest.mark.parametrize("payload", [b"", bytes(range(58))])
def test_parse_response_frame_accepts_payload_capacity_boundaries_without_checksum(payload):
    frame = parse_response_frame(_response_frame(0xFF, payload))

    assert frame == ParsedFrame(command=0xFF, payload=payload)


@pytest.mark.parametrize(
    ("target", "enabled", "prefix"),
    [
        (PowerTarget.MAIN, False, "2EAAED6B00070C0B000000FFFF1E"),
        (PowerTarget.MAIN, True, "2EAAED6B00070C0B000001FFFF1F"),
        (PowerTarget.SUBWOOFER, False, "2EAAED6B00070C0C000000FFFF1F"),
        (PowerTarget.SUBWOOFER, True, "2EAAED6B00070C0C000001FFFF20"),
    ],
)
def test_build_power_packets_match_verified_device_frames(target, enabled, prefix):
    packet = build_power_packet(target, enabled)

    assert len(packet) == 64
    assert packet[:14].hex().upper() == prefix
    assert packet[14:] == bytes(50)


def test_build_query_packet_has_expected_report_id_and_checksum():
    packet = build_query_packet()

    assert len(packet) == 64
    assert packet[:7] == bytes.fromhex("2EAAED6A000001")
    assert packet[7:] == bytes(57)


@pytest.mark.parametrize(
    ("command", "payload"),
    [
        (-1, b""),
        (0x100, b""),
        (0x6A, bytes(58)),
        (0x6A, bytearray()),
        (0x6A, "not bytes"),
    ],
)
def test_build_frame_rejects_out_of_range_command_or_invalid_payload(command, payload):
    with pytest.raises(ProtocolError):
        build_frame(command, payload)


def test_build_frame_encodes_length_checksum_and_zero_padding():
    packet = build_frame(0x6A, b"\x01\x02")

    assert len(packet) == 64
    assert packet[:9] == bytes.fromhex("2EAAED6A0002010206")
    assert packet[9:] == bytes(55)


@pytest.mark.parametrize("mutation", ["header", "length", "checksum", "truncation"])
def test_parse_frame_rejects_bad_header_length_checksum_and_truncation(mutation):
    report = bytearray(build_frame(0xE3, b"\x01\x02"))
    if mutation == "header":
        report[1] = 0
    elif mutation == "length":
        report[5] = 3
    elif mutation == "checksum":
        report[8] ^= 0x01
    else:
        report.pop()

    with pytest.raises(ProtocolError):
        parse_frame(bytes(report))


def test_parse_frame_returns_an_immutable_frame_with_exact_payload():
    frame = parse_frame(build_frame(0xE3, b"\x01\x02"))

    assert frame == ParsedFrame(command=0xE3, payload=b"\x01\x02")
    with pytest.raises(FrozenInstanceError):
        frame.command = 0x6A


def test_parsed_frame_rejects_mutable_payload_storage():
    with pytest.raises(TypeError):
        ParsedFrame(command=0xE3, payload=bytearray(b"\x01"))


def test_light_entry_and_ambient_state_are_immutable():
    entry = LightEntry(index=0x0B, red=0, green=0, blue=1, brightness=0xFF, speed=0xFF)
    source_entries = {entry.index: entry}
    state = AmbientState(array_index=0x0C, effect_id=3, entries=source_entries)
    source_entries.clear()

    assert state.entries[0x0B] is entry
    with pytest.raises(FrozenInstanceError):
        entry.index = 0x01
    with pytest.raises(TypeError):
        state.entries[0x0C] = entry


def _e3_packet(part_number, effect_id, indices, array_index=0x0C, total_parts=2):
    entries = b"".join(
        bytes((index, index, index + 1, index + 2, 0x80 + index, 0x10 + index))
        for index in indices
    )
    payload = bytes((total_parts, part_number, array_index, effect_id)) + entries
    return parse_response_frame(_response_frame(0xE3, payload))


def test_collector_reassembles_two_e3_packets_out_of_order():
    collector = AmbientStateCollector()

    assert collector.feed(_e3_packet(2, 7, range(7, 13))) is None
    state = collector.feed(_e3_packet(1, 7, range(1, 7)))

    assert state is not None
    assert state.array_index == 0x0C
    assert state.effect_id == 7
    assert set(state.entries) == set(range(0x01, 0x0D))
    assert state.entries[0x01] == LightEntry(0x01, 1, 2, 3, 0x81, 0x11)
    assert state.entries[0x0C] == LightEntry(0x0C, 12, 13, 14, 0x8C, 0x1C)


def test_collector_discards_stale_generation_after_new_packet_one():
    collector = AmbientStateCollector()

    collector.feed(_e3_packet(2, 7, range(7, 13)))
    assert collector.feed(_e3_packet(1, 8, range(1, 7))) is None
    assert collector.feed(_e3_packet(2, 7, range(7, 13))) is None

    state = collector.feed(_e3_packet(2, 8, range(7, 13)))
    assert state is not None
    assert state.effect_id == 8


def test_collector_rejects_duplicate_entry_across_parts():
    collector = AmbientStateCollector()
    collector.feed(_e3_packet(1, 7, range(1, 7)))

    with pytest.raises(ProtocolError, match="duplicate"):
        collector.feed(_e3_packet(2, 7, range(6, 13)))


def test_collector_never_completes_with_missing_part():
    collector = AmbientStateCollector()

    assert collector.feed(_e3_packet(1, 7, range(1, 7))) is None
    assert collector.feed(_e3_packet(1, 7, range(1, 7))) is None


def test_collector_rejects_mismatched_effect_or_array():
    collector = AmbientStateCollector()
    collector.feed(_e3_packet(1, 7, range(1, 7)))
    assert collector.feed(_e3_packet(2, 8, range(7, 13))) is None

    with pytest.raises(ProtocolError, match="array"):
        collector.feed(_e3_packet(2, 7, range(7, 13), array_index=0x0D))


def test_style_fingerprint_uses_effect_and_complete_entries_one_through_ten():
    entries = {
        index: LightEntry(index, index, index + 1, index + 2, 0x80 + index, 0x10 + index)
        for index in range(1, 13)
    }
    state = AmbientState(array_index=0x0C, effect_id=7, entries=entries)

    assert style_fingerprint(state) == (7, tuple(entries[index] for index in range(1, 11)))
