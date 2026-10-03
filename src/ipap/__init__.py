"""IPAP — Inter-Planet Application Protocol reference implementation."""

from .constants import (
    PROTOCOL_VERSION,
    Flags,
    LLMAvailability,
    MessageType,
    NodeState,
    Priority,
    Status,
)
from .packet import Packet, PacketError

__all__ = [
    "PROTOCOL_VERSION",
    "Flags",
    "LLMAvailability",
    "MessageType",
    "NodeState",
    "Packet",
    "PacketError",
    "Priority",
    "Status",
]
__version__ = "0.1.0"
