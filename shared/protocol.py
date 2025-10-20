"""Core protocol primitives shared between server and client.

The protocol uses a mix of TCP (for reliable delivery) and UDP (for low-latency media).
This module centralises serialization/deserialization helpers and message schemas
so both halves of the application remain in sync.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, Optional, TypedDict

import json
import struct


class Transport(Enum):
    """Supported transport mechanisms for a channel."""

    TCP = "tcp"
    UDP = "udp"


class ControlAction(str, Enum):
    """Control-plane events exchanged over TCP."""

    HELLO = "hello"
    WELCOME = "welcome"
    HEARTBEAT = "heartbeat"
    USER_JOINED = "user_joined"
    USER_LEFT = "user_left"
    CHAT_MESSAGE = "chat_message"
    PRESENTER_GRANTED = "presenter_granted"
    PRESENTER_REVOKED = "presenter_revoked"
    SCREEN_FRAME = "screen_frame"
    SCREEN_CONTROL = "screen_control"
    FILE_OFFER = "file_offer"
    FILE_REQUEST = "file_request"
    FILE_CHUNK = "file_chunk"
    FILE_COMPLETE = "file_complete"
    FILE_PROGRESS = "file_progress"
    VIDEO_STATUS = "video_status"
    AUDIO_STATUS = "audio_status"
    ERROR = "error"
    KICKED = "kicked"


@dataclass(slots=True)
class ChatMessage:
    """Payload for chat broadcasts."""

    sender: str
    message: str
    timestamp_ms: int

    def to_dict(self) -> Dict[str, Any]:
        return {
            "sender": self.sender,
            "message": self.message,
            "timestamp_ms": self.timestamp_ms,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ChatMessage":
        return cls(
            sender=data["sender"],
            message=data["message"],
            timestamp_ms=int(data["timestamp_ms"]),
        )


class ControlEnvelope(TypedDict):
    """Generic representation of control messages sent over TCP."""

    action: str
    data: Dict[str, Any]


def encode_control_message(action: ControlAction, data: Dict[str, Any]) -> bytes:
    """Serialize a control message using length-prefixed JSON."""

    envelope: ControlEnvelope = {
        "action": action.value,
        "data": data,
    }
    payload = json.dumps(envelope, separators=(',', ':')).encode("utf-8")
    return struct.pack("!I", len(payload)) + payload


def decode_control_stream(buffer: bytes) -> tuple[list[ControlEnvelope], bytes]:
    """Decode as many complete control messages from the buffer as possible.

    Returns a tuple of (messages, remaining_buffer).
    """

    offset = 0
    messages: list[ControlEnvelope] = []
    buf_len = len(buffer)

    while offset + 4 <= buf_len:
        (length,) = struct.unpack_from("!I", buffer, offset)
        if offset + 4 + length > buf_len:
            break
        start = offset + 4
        end = start + length
        envelope = json.loads(buffer[start:end].decode("utf-8"))
        messages.append(envelope)  # type: ignore[arg-type]
        offset = end

    return messages, buffer[offset:]


MEDIA_HEADER_STRUCT = struct.Struct("!IIfI")


@dataclass(slots=True)
class MediaFrameHeader:
    """Header carried before every UDP media frame."""

    stream_id: int
    sequence_number: int
    timestamp_ms: float
    payload_type: int

    def pack(self) -> bytes:
        return MEDIA_HEADER_STRUCT.pack(
            self.stream_id,
            self.sequence_number,
            self.timestamp_ms,
            self.payload_type,
        )

    @classmethod
    def unpack(cls, data: bytes) -> "MediaFrameHeader":
        stream_id, sequence_number, timestamp_ms, payload_type = MEDIA_HEADER_STRUCT.unpack(data)
        return cls(stream_id, sequence_number, timestamp_ms, payload_type)


class PayloadType(Enum):
    """Indicates the content type carried in a UDP payload."""

    VIDEO = 1
    AUDIO = 2
    SCREEN = 3


@dataclass(slots=True)
class FileChunkMetadata:
    """Metadata for a file transfer chunk."""

    file_id: str
    filename: str
    total_size: int
    chunk_index: int
    chunk_size: int
    total_chunks: int

    def to_dict(self) -> Dict[str, Any]:
        return {
            "file_id": self.file_id,
            "filename": self.filename,
            "total_size": self.total_size,
            "chunk_index": self.chunk_index,
            "chunk_size": self.chunk_size,
            "total_chunks": self.total_chunks,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "FileChunkMetadata":
        return cls(
            file_id=data["file_id"],
            filename=data["filename"],
            total_size=int(data["total_size"]),
            chunk_index=int(data["chunk_index"]),
            chunk_size=int(data["chunk_size"]),
            total_chunks=int(data["total_chunks"]),
        )


DEFAULT_TCP_PORT = 55000
DEFAULT_UDP_PORT = 56000
DEFAULT_VIDEO_PORT = 56000
DEFAULT_AUDIO_PORT = 56010
DEFAULT_SCREEN_PORT = 55010
DEFAULT_FILE_PORT = 55020


@dataclass(slots=True)
class FileOffer:
    """Announces a file available for download."""

    file_id: str
    filename: str
    total_size: int
    uploader: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "file_id": self.file_id,
            "filename": self.filename,
            "total_size": self.total_size,
            "uploader": self.uploader,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "FileOffer":
        return cls(
            file_id=data["file_id"],
            filename=data["filename"],
            total_size=int(data["total_size"]),
            uploader=data["uploader"],
        )


@dataclass(slots=True)
class ClientIdentity:
    """Identity packet exchanged during TCP handshake."""

    username: str
    client_version: str = "0.1.0"
    desired_room: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        data: Dict[str, Any] = {
            "username": self.username,
            "client_version": self.client_version,
        }
        if self.desired_room:
            data["desired_room"] = self.desired_room
        return data

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ClientIdentity":
        return cls(
            username=data["username"],
            client_version=data.get("client_version", "0.1.0"),
            desired_room=data.get("desired_room"),
        )
