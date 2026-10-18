from dataclasses import FrozenInstanceError

import pytest

from halo_soundbar_service.protocol import (
    AmbientState,
    LightEntry,
    ParsedFrame,
    PowerTarget,
    ProtocolError,
    build_frame,
    build_power_packet,
    build_query_packet,
    parse_frame,
)


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
