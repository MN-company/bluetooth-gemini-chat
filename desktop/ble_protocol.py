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
