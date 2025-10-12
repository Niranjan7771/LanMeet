from shared.protocol import (
    ControlAction,
    decode_control_stream,
    encode_control_message,
    MediaFrameHeader,
)


def test_encode_decode_control_roundtrip() -> None:
    payload = {"message": "hello"}
    encoded = encode_control_message(ControlAction.CHAT_MESSAGE, payload)
    messages, remaining = decode_control_stream(encoded)
    assert remaining == b""
    assert len(messages) == 1
    assert messages[0]["action"] == ControlAction.CHAT_MESSAGE.value
    assert messages[0]["data"] == payload


def test_media_frame_header_pack_unpack() -> None:
    header = MediaFrameHeader(stream_id=1, sequence_number=42, timestamp_ms=1234.5, payload_type=2)
    packed = header.pack()
    restored = MediaFrameHeader.unpack(packed)
    assert restored.stream_id == header.stream_id
    assert restored.sequence_number == header.sequence_number
    assert restored.timestamp_ms == header.timestamp_ms
    assert restored.payload_type == header.payload_type
