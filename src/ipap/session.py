"""An endpoint's view of one peer: builds outgoing packets (sequence numbers,
compression, encryption, signature) and validates incoming ones (signature,
replay, decryption, schema).
"""

from __future__ import annotations

import time
import zlib
from collections.abc import Callable
from dataclasses import dataclass

from .constants import MISSION_EPOCH_UNIX, Flags, MessageType, Priority
from .crypto import (
    DecryptionError,
    Identity,
    SignatureError,
    decrypt_payload,
    encrypt_payload,
    sign_packet,
    verify_packet,
)
from .packet import Packet, PacketError
from .payloads import Payload, PayloadError, decode_payload, encode_payload

REPLAY_WINDOW = 1024
MAX_DECOMPRESSED_SIZE = 1 << 20


class ProtocolError(Exception):
    """Any reason an incoming packet must be discarded."""


@dataclass
class Message:
    packet: Packet
    payload: Payload

    @property
    def msg_type(self) -> MessageType:
        return self.packet.msg_type

    @property
    def seq(self) -> int:
        return self.packet.seq


class ReplayGuard:
    """Rejects duplicates and packets older than a sliding window."""

    def __init__(self, window: int = REPLAY_WINDOW) -> None:
        self.window = window
        self.highest = -1
        self.seen: set[int] = set()

    def check(self, seq: int) -> None:
        if seq in self.seen:
            raise ProtocolError(f"replayed sequence number {seq}")
        if seq <= self.highest - self.window:
            raise ProtocolError(f"sequence number {seq} is outside the replay window")

    def accept(self, seq: int) -> None:
        self.seen.add(seq)
        self.highest = max(self.highest, seq)
        floor = self.highest - self.window
        self.seen = {s for s in self.seen if s > floor}


def mission_time() -> float:
    return time.time() - MISSION_EPOCH_UNIX


class Endpoint:
    def __init__(
        self,
        identity: Identity,
        peer_public_key: bytes,
        clock: Callable[[], float] | None = None,
        *,
        encrypt: bool = False,
        compress_threshold: int | None = 256,
        first_seq: int = 1,
    ) -> None:
        self.identity = identity
        self.peer_public_key = peer_public_key
        self.clock = clock or mission_time
        self.encrypt = encrypt
        self.compress_threshold = compress_threshold
        self._next_seq = first_seq
        self.replay = ReplayGuard()

    def next_seq(self) -> int:
        seq = self._next_seq
        self._next_seq = (self._next_seq + 1) & 0xFFFFFFFF
        return seq

    def build(
        self,
        payload: Payload,
        *,
        priority: Priority | None = None,
        llm_version: int = 0,
        seq: int | None = None,
    ) -> Packet:
        body = encode_payload(payload)
        flags = Flags.NONE
        if self.compress_threshold is not None and len(body) >= self.compress_threshold:
            compressed = zlib.compress(body, 9)
            if len(compressed) < len(body):
                body, flags = compressed, flags | Flags.COMPRESSED
        if self.encrypt:
            body = encrypt_payload(body, self.identity, self.peer_public_key)
            flags |= Flags.ENCRYPTED
        packet = Packet(
            msg_type=payload.msg_type,
            seq=self.next_seq() if seq is None else seq,
            payload=body,
            timestamp=max(0, int(self.clock())) & 0xFFFFFFFF,
            priority=priority,
            llm_version=llm_version,
            flags=flags,
        )
        return sign_packet(packet, self.identity)

    def encode(self, payload: Payload, **kwargs) -> bytes:
        return self.build(payload, **kwargs).encode()

    def parse(self, data: bytes) -> Message:
        try:
            packet = Packet.decode(data)
            verify_packet(packet, self.peer_public_key)
            self.replay.check(packet.seq)
            body = packet.payload
            if packet.flags & Flags.ENCRYPTED:
                body = decrypt_payload(body, self.identity, self.peer_public_key)
            if packet.flags & Flags.COMPRESSED:
                d = zlib.decompressobj()
                body = d.decompress(body, MAX_DECOMPRESSED_SIZE)
                if d.unconsumed_tail:
                    raise ProtocolError("decompressed payload too large")
            payload = decode_payload(packet.msg_type, body)
        except (PacketError, SignatureError, DecryptionError, PayloadError, zlib.error) as e:
            raise ProtocolError(str(e)) from e
        self.replay.accept(packet.seq)
        return Message(packet, payload)
