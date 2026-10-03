import pytest

from ipap.constants import HEADER_SIZE, MAX_PACKET_SIZE, Flags, MessageType, Priority
from ipap.packet import Packet, PacketError


def test_header_layout_matches_rfc():
    p = Packet(MessageType.EXEC, seq=1002, payload=b"abc", timestamp=600, llm_version=2)
    raw = p.encode()
    assert len(raw) == HEADER_SIZE + 3
    assert raw[0] == (1 << 4) | 0x03  # Version | Type
    assert raw[1] == 0  # Flags
    assert int.from_bytes(raw[2:4], "big") == len(raw)
    assert int.from_bytes(raw[4:8], "big") == 1002
    assert int.from_bytes(raw[8:12], "big") == 600
    assert raw[12] == 2  # LLM Version
    assert raw[13] == Priority.HIGH  # EXEC default priority
    assert raw[14:16] == b"\x00\x00"


@pytest.mark.parametrize("mtype", list(MessageType))
def test_roundtrip(mtype):
    p = Packet(mtype, seq=0xFFFFFFFF, payload=b"\x00\xff" * 10, timestamp=123,
               priority=Priority.LOW, flags=Flags.COMPRESSED)
    assert Packet.decode(p.encode()) == p


def test_default_priorities():
    assert Packet(MessageType.ABORT, 1).priority == Priority.CRITICAL
    assert Packet(MessageType.STATUS, 1).priority == Priority.LOW


def test_signed_roundtrip():
    p = Packet(MessageType.WAKE, 7).with_signature(b"s" * 64)
    raw = p.encode()
    assert len(raw) == HEADER_SIZE + 64
    q = Packet.decode(raw)
    assert q.signature == b"s" * 64 and q.payload == b""


def test_length_mismatch_rejected():
    raw = Packet(MessageType.WAKE, 1, payload=b"xyz").encode()
    with pytest.raises(PacketError, match="length"):
        Packet.decode(raw + b"!")
    with pytest.raises(PacketError, match="length"):
        Packet.decode(raw[:-1])


def test_truncated_and_bad_fields():
    with pytest.raises(PacketError, match="truncated"):
        Packet.decode(b"\x13\x00")
    raw = bytearray(Packet(MessageType.WAKE, 1).encode())
    raw[0] = (2 << 4) | 1
    with pytest.raises(PacketError, match="version"):
        Packet.decode(bytes(raw))
    raw[0] = (1 << 4) | 0x0F
    with pytest.raises(PacketError, match="message type"):
        Packet.decode(bytes(raw))
    raw[0] = 0x11
    raw[1] = 0x80
    with pytest.raises(PacketError, match="flags"):
        Packet.decode(bytes(raw))


def test_size_limit():
    Packet(MessageType.EXEC, 1, payload=b"x" * (MAX_PACKET_SIZE - HEADER_SIZE))
    with pytest.raises(PacketError, match="too large"):
        Packet(MessageType.EXEC, 1, payload=b"x" * (MAX_PACKET_SIZE - HEADER_SIZE + 1))


def test_signature_flag_consistency():
    with pytest.raises(PacketError):
        Packet(MessageType.WAKE, 1, flags=Flags.SIGNED).encode()
