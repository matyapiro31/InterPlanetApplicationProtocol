"""Protocol constants defined by RFC-9999 (IPAP) and this implementation's interpretation.

See docs/spec-notes.md for the choices made where the specification is silent.
"""

from __future__ import annotations

from enum import IntEnum, IntFlag, StrEnum

PROTOCOL_VERSION = 1
IPAP_VERSION_STRING = "1.0"

# Mission Timestamp = seconds since this epoch (2026-01-01T00:00:00Z) unless a
# simulation clock is supplied.
MISSION_EPOCH_UNIX = 1_767_225_600

HEADER_SIZE = 16
SIGNATURE_SIZE = 64
MAX_PACKET_SIZE = 0xFFFF


class MessageType(IntEnum):
    """Section 5.2 message types."""

    WAKE = 0x01
    READY = 0x02
    EXEC = 0x03
    RESULT = 0x04
    ABORT = 0x05
    STATUS = 0x06


class Priority(IntEnum):
    LOW = 0
    NORMAL = 1
    HIGH = 2
    CRITICAL = 3


DEFAULT_PRIORITY: dict[MessageType, Priority] = {
    MessageType.WAKE: Priority.NORMAL,
    MessageType.READY: Priority.NORMAL,
    MessageType.EXEC: Priority.HIGH,
    MessageType.RESULT: Priority.NORMAL,
    MessageType.ABORT: Priority.CRITICAL,
    MessageType.STATUS: Priority.LOW,
}


class Flags(IntFlag):
    NONE = 0
    SIGNED = 0x01
    ENCRYPTED = 0x02
    COMPRESSED = 0x04


KNOWN_FLAGS = Flags.SIGNED | Flags.ENCRYPTED | Flags.COMPRESSED


class Status(StrEnum):
    """RESULT message status values."""

    OK = "OK"
    VERIFY_FAILED = "VERIFY_FAILED"
    RESOLVE_FAILED = "RESOLVE_FAILED"
    EXEC_FAILED = "EXEC_FAILED"
    ABORTED = "ABORTED"
    REJECTED = "REJECTED"
    DEFERRED = "DEFERRED"


class LLMAvailability(StrEnum):
    AVAILABLE = "AVAILABLE"
    DEFERRED = "DEFERRED"
    UNAVAILABLE = "UNAVAILABLE"


class NodeState(StrEnum):
    """Section 6 remote node states."""

    IDLE = "IDLE"
    READY_CHECK = "READY_CHECK"
    DEFER = "DEFER"
    LLM_ACTIVE = "LLM_ACTIVE"
    VERIFYING = "VERIFYING"
    ROLLBACK = "ROLLBACK"
    EXECUTING = "EXECUTING"
    REPORTING = "REPORTING"
