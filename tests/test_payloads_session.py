import json

import pytest

from ipap.constants import Flags, LLMAvailability, MessageType, Priority, Status
from ipap.crypto import Identity, SignatureError, sign_packet, verify_packet
from ipap.packet import Packet
from ipap.payloads import (
    AbortPayload,
    Constraints,
    ExecPayload,
    PayloadError,
    ReadyPayload,
    ResultPayload,
    TestVector,
    decode_payload,
    encode_payload,
)
from ipap.session import Endpoint, ProtocolError

RFC_EXEC_EXAMPLE = {
    "ipap_version": "1.0",
    "llm_model": "ipap-local-v2",
    "prompt": "障害物回避アルゴリズムを生成せよ。...",
    "constraints": {
        "language": "python",
        "max_tokens": 2048,
        "timeout_ms": 30000,
        "test_vectors": ["tc_001", "tc_002", "tc_003"],
    },
    "fallback": "program_id:abc123",
}


def test_rfc_exec_example_parses():
    p = decode_payload(MessageType.EXEC, json.dumps(RFC_EXEC_EXAMPLE).encode())
    assert isinstance(p, ExecPayload)
    assert p.constraints.test_vectors == ["tc_001", "tc_002", "tc_003"]
    assert p.fallback == "program_id:abc123"
    assert p.to_dict() == RFC_EXEC_EXAMPLE


def test_inline_test_vector_roundtrip():
    p = ExecPayload(prompt="x", constraints=Constraints(
        test_vectors=["tc_1", TestVector("v", input=[1], expected=2, tolerance=0.1)]))
    q = decode_payload(MessageType.EXEC, encode_payload(p))
    assert q == p


@pytest.mark.parametrize("raw,match", [
    (b"not json", "JSON"),
    (b"[]", "object"),
    (b'{"constraints":{}}', "prompt"),
    (b'{"prompt":"  "}', "empty"),
    (b'{"prompt":"x","constraints":{"timeout_ms":"9"}}', "timeout_ms"),
    (b'{"prompt":"x","assets":[1]}', "assets"),
])
def test_exec_schema_errors(raw, match):
    with pytest.raises(PayloadError, match=match):
        decode_payload(MessageType.EXEC, raw)


def test_result_and_ready_roundtrip():
    r = ResultPayload(in_reply_to=5, status=Status.VERIFY_FAILED, output={"a": 1},
                      program="fallback:abc")
    assert decode_payload(MessageType.RESULT, encode_payload(r)) == r
    rd = ReadyPayload(in_reply_to=1, power=87.0, power_level="FULL",
                      llm=LLMAvailability.AVAILABLE, llm_model="ipap-mock-v1")
    assert decode_payload(MessageType.READY, encode_payload(rd)) == rd


def test_sign_and_tamper():
    me = Identity.generate()
    p = sign_packet(Packet(MessageType.WAKE, 1, payload=b"{}"), me)
    verify_packet(Packet.decode(p.encode()), me.public_key)
    raw = bytearray(p.encode())
    raw[17] ^= 0x01
    with pytest.raises(SignatureError):
        verify_packet(Packet.decode(bytes(raw)), me.public_key)
    with pytest.raises(SignatureError):
        verify_packet(p, Identity.generate().public_key)


def _pair(**kw):
    earth, mars = Identity.generate(), Identity.generate()
    return Endpoint(earth, mars.public_key, **kw), Endpoint(mars, earth.public_key, **kw)


@pytest.mark.parametrize("encrypt", [False, True])
def test_endpoint_roundtrip(encrypt):
    e, m = _pair(encrypt=encrypt, compress_threshold=16)
    payload = ExecPayload(prompt="ルートを最適化せよ " * 20)
    raw = e.encode(payload, priority=Priority.CRITICAL)
    pkt = Packet.decode(raw)
    assert pkt.flags & Flags.SIGNED and pkt.flags & Flags.COMPRESSED
    assert bool(pkt.flags & Flags.ENCRYPTED) == encrypt
    if encrypt:
        assert "ルート".encode() not in raw
    msg = m.parse(raw)
    assert msg.payload == payload and msg.packet.priority == Priority.CRITICAL


def test_endpoint_rejects_replay_and_foreign_key():
    e, m = _pair()
    raw = e.encode(AbortPayload(reason="x"))
    m.parse(raw)
    with pytest.raises(ProtocolError, match="replayed"):
        m.parse(raw)
    stranger = Endpoint(Identity.generate(), m.identity.public_key)
    with pytest.raises(ProtocolError, match="signature"):
        m.parse(stranger.encode(AbortPayload()))


def test_endpoint_rejects_unsigned():
    _, m = _pair()
    with pytest.raises(ProtocolError, match="not signed"):
        m.parse(Packet(MessageType.WAKE, 99, payload=b"{}").encode())
