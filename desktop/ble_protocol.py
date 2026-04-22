from __future__ import annotations

import math
import struct
import time
from dataclasses import dataclass, field
from typing import Dict, Optional

PROTOCOL_VERSION = 1
HEADER_STRUCT = struct.Struct(">BHHH")
HEADER_SIZE = HEADER_STRUCT.size
DEFAULT_MAX_PACKET_SIZE = 20
ASSEMBLY_TIMEOUT_SECONDS = 300.0

# Protocol v2: compact binary ping/pong frames (6 bytes each, 90% smaller)
# Format: [0x01 or 0x02][timestamp_ms:4][status:1]
PROTO2_PING_TYPE = 0x01
PROTO2_PONG_TYPE = 0x02
PROTO2_PING_STRUCT = struct.Struct(">BIB")  # type(1) + ts_ms(4) + status(1) = 6 bytes
PROTO2_MAGIC = b"\xfe\xfd"  # 2-byte prefix to distinguish v2 binary frames from v1

BINARY_PING_SIZE = 2 + PROTO2_PING_STRUCT.size  # magic(2) + payload(6) = 8 bytes

PROMPT_BUNDLE_MAGIC = b"bgp2"
PROMPT_BUNDLE_HEADER_STRUCT = struct.Struct(">4sBII")
PROMPT_BUNDLE_FLAGS_GZIP_METADATA = 0x01


def encode_binary_ping(ts_ms: int) -> bytes:
    return PROTO2_MAGIC + PROTO2_PING_STRUCT.pack(PROTO2_PING_TYPE, ts_ms & 0xFFFFFFFF, 0)


def encode_binary_pong(ts_ms: int) -> bytes:
    return PROTO2_MAGIC + PROTO2_PING_STRUCT.pack(PROTO2_PONG_TYPE, ts_ms & 0xFFFFFFFF, 0)


def decode_binary_frame(data: bytes) -> Optional[tuple[int, int]]:
    """Return (frame_type, ts_ms) if data is a valid v2 binary frame, else None."""
    if len(data) < BINARY_PING_SIZE or data[:2] != PROTO2_MAGIC:
        return None
    frame_type, ts_ms, _ = PROTO2_PING_STRUCT.unpack(data[2:2 + PROTO2_PING_STRUCT.size])
    return (frame_type, ts_ms)


def encode_prompt_bundle(
    metadata: bytes,
    image_bytes: bytes = b"",
    *,
    gzip_metadata: bool = False,
) -> bytes:
    flags = 0
    if gzip_metadata:
        import gzip

        metadata = gzip.compress(metadata, compresslevel=6)
        flags |= PROMPT_BUNDLE_FLAGS_GZIP_METADATA

    header = PROMPT_BUNDLE_HEADER_STRUCT.pack(
        PROMPT_BUNDLE_MAGIC,
        flags,
        len(metadata),
        len(image_bytes),
    )
    return header + metadata + image_bytes


def decode_prompt_bundle(data: bytes) -> Optional[tuple[bytes, bytes]]:
    if len(data) < PROMPT_BUNDLE_HEADER_STRUCT.size:
        return None

    magic, flags, metadata_len, image_len = PROMPT_BUNDLE_HEADER_STRUCT.unpack(
        data[:PROMPT_BUNDLE_HEADER_STRUCT.size]
    )
    if magic != PROMPT_BUNDLE_MAGIC:
        return None

    expected_size = PROMPT_BUNDLE_HEADER_STRUCT.size + metadata_len + image_len
    if len(data) != expected_size:
        raise ValueError("prompt bundle size mismatch")

    metadata_start = PROMPT_BUNDLE_HEADER_STRUCT.size
    metadata_end = metadata_start + metadata_len
    metadata = data[metadata_start:metadata_end]
    image_bytes = data[metadata_end:]

    if flags & PROMPT_BUNDLE_FLAGS_GZIP_METADATA:
        import gzip

        metadata = gzip.decompress(metadata)

    return metadata, image_bytes


@dataclass
class Frame:
    transport_id: int
    index: int
    total: int
    payload: bytes


class TransportIdGenerator:
    def __init__(self, start: int = 1) -> None:
        if not (0 <= start <= 0xFFFF):
            raise ValueError("start must be between 0 and 65535")
        self._next = start or 1

    def next(self) -> int:
        current = self._next
        self._next += 1
        if self._next > 0xFFFF:
            self._next = 1
        return current


class FrameCodec:
    @staticmethod
    def encode_message(transport_id: int, payload: bytes, max_packet_size: int) -> list[bytes]:
        if not (0 <= transport_id <= 0xFFFF):
            raise ValueError("transport_id must be between 0 and 65535")
        if max_packet_size <= HEADER_SIZE:
            raise ValueError(f"max_packet_size must be > {HEADER_SIZE}")

        chunk_size = max_packet_size - HEADER_SIZE
        total_chunks = max(1, math.ceil(len(payload) / chunk_size))
        if total_chunks > 0xFFFF:
            raise ValueError("payload too large for this protocol")

        packets: list[bytes] = []
        for idx in range(total_chunks):
            start = idx * chunk_size
            end = start + chunk_size
            chunk = payload[start:end]
            header = HEADER_STRUCT.pack(PROTOCOL_VERSION, transport_id, idx, total_chunks)
            packets.append(header + chunk)
        return packets

    @staticmethod
    def decode_packet(packet: bytes) -> Frame:
        if len(packet) < HEADER_SIZE:
            raise ValueError("packet is too short")

        version, transport_id, index, total = HEADER_STRUCT.unpack(packet[:HEADER_SIZE])
        if version != PROTOCOL_VERSION:
            raise ValueError(f"unsupported protocol version: {version}")
        if total == 0:
            raise ValueError("total chunk count cannot be 0")
        if index >= total:
            raise ValueError("chunk index is outside chunk range")

        return Frame(
            transport_id=transport_id,
            index=index,
            total=total,
            payload=packet[HEADER_SIZE:],
        )


@dataclass
class _PendingAssembly:
    total: int
    chunks: Dict[int, bytes] = field(default_factory=dict)
    created_at: float = field(default_factory=time.monotonic)


class FrameAssembler:
    def __init__(self, timeout_seconds: float = ASSEMBLY_TIMEOUT_SECONDS) -> None:
        self._timeout_seconds = timeout_seconds
        self._pending: Dict[int, _PendingAssembly] = {}

    def add_packet(self, packet: bytes) -> Optional[bytes]:
        self._cleanup_expired()
        frame = FrameCodec.decode_packet(packet)
        assembly = self._pending.get(frame.transport_id)

        if assembly is None:
            assembly = _PendingAssembly(total=frame.total)
            self._pending[frame.transport_id] = assembly
        elif assembly.total != frame.total:
            self._pending.pop(frame.transport_id, None)
            raise ValueError("inconsistent total chunk count for transport_id")

        assembly.chunks[frame.index] = frame.payload
        if len(assembly.chunks) != assembly.total:
            return None

        payload = b"".join(assembly.chunks[idx] for idx in range(assembly.total))
        self._pending.pop(frame.transport_id, None)
        return payload

    def _cleanup_expired(self) -> None:
        now = time.monotonic()
        expired = [
            message_id
            for message_id, assembly in self._pending.items()
            if now - assembly.created_at > self._timeout_seconds
        ]
        for message_id in expired:
            self._pending.pop(message_id, None)
