"""Tests for frame encoding and decoding, using frames captured from a panel."""

from __future__ import annotations

import pytest

from custom_components.truma_inetx.protocol.const import ControlType, MessageType
from custom_components.truma_inetx.protocol.errors import InetXProtocolError
from custom_components.truma_inetx.protocol.frame import decode_frame, encode_frame
from custom_components.truma_inetx.protocol.messages import (
    discovery_request,
    is_last_message,
    parameter_reports,
    registered_address,
    registration_request,
    subscribe_request,
    write_request,
)

CAPTURED_REGISTRATION_REQUEST = bytes.fromhex(
    "FF FF 00 05 12 00 01 00 00 00 00 00 00 00 00 00 01 42 A1 62 70 76 82 05 01"
)
CAPTURED_REGISTRATION_RESPONSE = bytes.fromhex(
    "FF FF 00 00 1C 00 01 00 00 00 00 00 00 00 00 00 02 42 BF 62 70 76 9F 05 01 FF"
    " 64 61 64 64 72 19 05 01 FF"
)
CAPTURED_WATER_TEMPERATURE_INFO = bytes.fromhex(
    "FF FF 01 02 4A 00 03 00 00 00 00 00 00 00 00 00 00 28 BF 62 74 6E 6C 57 61 74"
    " 65 72 48 65 61 74 69 6E 67 62 70 6E 64 54 65 6D 70 64 74 79 70 65 0A 63 6D 69"
    " 6E 39 01 8F 63 6D 61 78 19 05 14 65 61 76 61 69 6C 01 64 70 65 72 6D 00 61 76"
    " 18 D7 FF"
)
CAPTURED_LAST_MESSAGE = bytes.fromhex(
    "01 05 01 02 1A 00 03 00 0E 00 00 00 00 00 00 00 84 60 BF 6B 4C 61 73 74 4D 65"
    " 73 73 61 67 65 01 FF"
)
CAPTURED_PANEL_DISCOVERY_REQUEST = bytes.fromhex(
    "01 01 01 05 1B 00 03 00 00 00 00 00 00 00 00 00 04 64 A1 62 74 6E 6B 52 6F 6F"
    " 6D 43 6C 69 6D 61 74 65"
)


def test_registration_request_matches_capture() -> None:
    """The registration request is byte-identical to a working capture."""
    assert registration_request() == CAPTURED_REGISTRATION_REQUEST


def test_discovery_request_matches_capture() -> None:
    """The discovery request is byte-identical to a working capture."""
    request = discovery_request(
        source=0x0501, destination=0x0101, topic="RoomClimate", correlation_id=0x64
    )
    assert request == CAPTURED_PANEL_DISCOVERY_REQUEST


def test_registration_response_yields_assigned_address() -> None:
    """The assigned address is read from an indefinite-length CBOR map."""
    frame = decode_frame(CAPTURED_REGISTRATION_RESPONSE)
    assert frame.control == ControlType.REGISTRATION
    assert registered_address(frame) == 0x0501


def test_info_frame_decodes_to_one_report() -> None:
    """A broadcast info frame carries one flat parameter description."""
    frame = decode_frame(CAPTURED_WATER_TEMPERATURE_INFO)
    assert (frame.source, frame.destination) == (0x0201, 0xFFFF)
    assert frame.message_type == MessageType.INFO
    assert parameter_reports(frame) == [
        {
            "tn": "WaterHeating",
            "pn": "Temp",
            "type": 10,
            "min": -400,
            "max": 1300,
            "avail": 1,
            "perm": 0,
            "v": 215,
        }
    ]


def test_last_message_ends_discovery_despite_segment_number() -> None:
    """A non-zero segment number without segment flags is a normal frame."""
    frame = decode_frame(CAPTURED_LAST_MESSAGE)
    assert frame.correlation_id == 0x60
    assert is_last_message(frame)
    assert parameter_reports(frame) == []


def test_discovery_response_is_flattened_per_parameter() -> None:
    """Topic grouping is resolved and every report names its topic."""
    frame = decode_frame(
        encode_frame(
            destination=0x0501,
            source=0x0201,
            control=ControlType.MESSAGE_BROKER,
            message_type=MessageType.PARAMETER_DISCOVERY_RESPONSE,
            correlation_id=1,
            body={
                "avail": 1,
                "topics": [
                    {
                        "tn": "AirHeating",
                        "parameters": [
                            {"pn": "Mode", "v": 1},
                            {"tn": "AirHeating", "pn": "Temp", "v": 200},
                        ],
                    },
                    {"tn": "Broken", "parameters": "not a list"},
                ],
            },
        )
    )
    assert parameter_reports(frame) == [
        {"tn": "AirHeating", "pn": "Mode", "v": 1},
        {"tn": "AirHeating", "pn": "Temp", "v": 200},
    ]


def test_frames_round_trip() -> None:
    """Encoded subscribe and write frames decode to the same content."""
    subscribe = decode_frame(
        subscribe_request(source=0x0501, topics=["RoomClimate"], correlation_id=7)
    )
    assert (subscribe.destination, subscribe.message_type) == (
        0x0000,
        MessageType.SUBSCRIBE,
    )
    assert subscribe.body == {"tn": ["RoomClimate"]}

    write = decode_frame(
        write_request(
            source=0x0501,
            destination=0x0101,
            topic="RoomClimate",
            parameter="Mode",
            value=3,
        )
    )
    assert write.body == {"tn": "RoomClimate", "pn": "Mode", "v": 3, "id": 0}
    assert (write.destination, write.correlation_id) == (0x0101, 0)


def test_trailing_padding_is_ignored() -> None:
    """Bytes beyond the size field do not break decoding."""
    frame = decode_frame(CAPTURED_LAST_MESSAGE + bytes(4))
    assert is_last_message(frame)


@pytest.mark.parametrize(
    "data",
    [
        pytest.param(b"\x01\x02", id="too short"),
        pytest.param(CAPTURED_LAST_MESSAGE[:-3], id="truncated"),
        pytest.param(
            CAPTURED_LAST_MESSAGE[:7] + b"\x01" + CAPTURED_LAST_MESSAGE[8:],
            id="segmented",
        ),
        pytest.param(
            bytes(
                [0x01, 0x05, 0x01, 0x02, 0x0C, 0x00, 0x03, *bytes(9), 0x84, 0x60, 0x1C]
            ),
            id="invalid cbor",
        ),
    ],
)
def test_malformed_frames_are_rejected(data: bytes) -> None:
    """Malformed frames raise a protocol error instead of decoding garbage."""
    with pytest.raises(InetXProtocolError):
        decode_frame(data)
