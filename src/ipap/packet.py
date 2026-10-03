"""Section 5.1 packet structure: a fixed 16-byte header, a variable payload and,
when the SIGNED flag is set, a trailing 64-byte Ed25519 signature.

     0                   1                   2                   3
     0 1 2 3 4 5 6 7 8 9 0 1 2 3 4 5 6 7 8 9 0 1 2 3 4 5 6 7 8 9 0 1
    |Version|  Type |    Flags      |         Packet Length         |
    |                        Sequence Number                        |
    |                       Mission Timestamp                       |
    |   LLM Version |  Priority     |       Reserved                |
    |                        Payload (Variable)                     |
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field, replace

from .constants import (
    DEFAULT_PRIORITY,
    HEADER_SIZE,
    KNOWN_FLAGS,
    MAX_PACKET_SIZE,
    PROTOCOL_VERSION,
    SIGNATURE_SIZE,
    Flags,
    MessageType,
    Priority,
)

_HEADER = struct.Struct("!BBHIIBBH")
assert _HEADER.size == HEADER_SIZE

_U32 = 0xFFFFFFFF


class PacketError(ValueError):
    """Raised when bytes cannot be decoded into a valid IPAP packet."""


@dataclass(frozen=True)
class Packet:
    msg_type: MessageType
    seq: int
    payload: bytes = b""
    timestamp: int = 0
    priority: Priority | None = None
    llm_version: int = 0
    flags: Flags = Flags.NONE
    signature: bytes | None = None
    version: int = PROTOCOL_VERSION
    reserved: int = field(default=0, compare=False)

    def __post_init__(self) -> None:
        if self.priority is None:
            object.__setattr__(self, "priority", DEFAULT_PRIORITY[MessageType(self.msg_type)])
        object.__setattr__(self, "msg_type", MessageType(self.msg_type))
        object.__setattr__(self, "priority", Priority(self.priority))
        object.__setattr__(self, "flags", Flags(self.flags))
        if not 0 <= self.version <= 0xF:
            raise PacketError(f"version out of range: {self.version}")
        if not 0 <= self.seq <= _U32:
            raise PacketError(f"sequence number out of range: {self.seq}")
        if not 0 <= self.timestamp <= _U32:
            raise PacketError(f"timestamp out of range: {self.timestamp}")
        if not 0 <= self.llm_version <= 0xFF:
            raise PacketError(f"LLM version out of range: {self.llm_version}")
        if self.length > MAX_PACKET_SIZE:
            raise PacketError(f"packet too large: {self.length} > {MAX_PACKET_SIZE} bytes")

    @property
    def length(self) -> int:
        trailer = SIGNATURE_SIZE if self.flags & Flags.SIGNED else 0
        return HEADER_SIZE + len(self.payload) + trailer

    def header_bytes(self) -> bytes:
        return _HEADER.pack(
            (self.version << 4) | int(self.msg_type),
            int(self.flags),
            self.length,
            self.seq,
            self.timestamp,
            self.llm_version,
            int(self.priority),
            self.reserved,
        )

    def signed_portion(self) -> bytes:
        """The bytes covered by the signature: header (with SIGNED set) + payload."""
        return self.header_bytes() + self.payload

    def with_signature(self, signature: bytes) -> Packet:
        if len(signature) != SIGNATURE_SIZE:
            raise PacketError(f"signature must be {SIGNATURE_SIZE} bytes")
        return replace(self, flags=self.flags | Flags.SIGNED, signature=signature)

    def encode(self) -> bytes:
        signed = bool(self.flags & Flags.SIGNED)
        if signed and self.signature is None:
            raise PacketError("SIGNED flag set but no signature attached")
        if not signed and self.signature is not None:
            raise PacketError("signature attached but SIGNED flag not set")
        return self.signed_portion() + (self.signature or b"")

    @classmethod
    def decode(cls, data: bytes) -> Packet:
        if len(data) < HEADER_SIZE:
            raise PacketError(f"truncated header: {len(data)} bytes")
        ver_type, flags, length, seq, ts, llm_ver, prio, reserved = _HEADER.unpack_from(data)
        version, type_code = ver_type >> 4, ver_type & 0x0F
        if version != PROTOCOL_VERSION:
            raise PacketError(f"unsupported protocol version: {version}")
        try:
            msg_type = MessageType(type_code)
        except ValueError as e:
            raise PacketError(f"unknown message type: {type_code:#x}") from e
        try:
            priority = Priority(prio)
        except ValueError as e:
            raise PacketError(f"unknown priority: {prio}") from e
        if flags & ~int(KNOWN_FLAGS):
            raise PacketError(f"unknown flags set: {flags:#04x}")
        if length != len(data):
            raise PacketError(f"length field {length} does not match {len(data)} bytes received")
        flags = Flags(flags)
        signature = None
        end = len(data)
        if flags & Flags.SIGNED:
            if len(data) < HEADER_SIZE + SIGNATURE_SIZE:
                raise PacketError("SIGNED packet too short to hold a signature")
            end -= SIGNATURE_SIZE
            signature = data[end:]
        return cls(
            msg_type=msg_type,
            seq=seq,
            payload=data[HEADER_SIZE:end],
            timestamp=ts,
            priority=priority,
            llm_version=llm_ver,
            flags=flags,
            signature=signature,
            version=version,
            reserved=reserved,
        )
