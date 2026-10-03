import pytest

from ipap.assets import (
    AssetError,
    AssetNotFound,
    AssetStore,
    b58decode,
    b58encode,
    cid_v0,
    parse_ref,
)
from ipap.constants import MessageType
from ipap.payloads import TestVector
from ipap.power import Battery, PowerLevel, PowerThresholds
from ipap.registry import ModelRegistry, ProgramRegistry, RegistryError, TestVectorRegistry


@pytest.mark.parametrize("pct,level", [
    (100, PowerLevel.FULL), (60.1, PowerLevel.FULL), (60, PowerLevel.NORMAL),
    (30, PowerLevel.NORMAL), (29.9, PowerLevel.LOW), (10, PowerLevel.LOW),
    (9.9, PowerLevel.EMERGENCY), (0, PowerLevel.EMERGENCY),
])
def test_power_classification(pct, level):
    assert PowerThresholds().classify(pct) is level


def test_power_policy():
    t = PowerThresholds()
    assert t.llm_allowed(PowerLevel.NORMAL) and not t.llm_allowed(PowerLevel.LOW)
    assert t.timeout_factor(PowerLevel.NORMAL) == 0.5
    assert t.timeout_factor(PowerLevel.FULL) == 1.0
    assert t.accepts(MessageType.ABORT, PowerLevel.EMERGENCY)
    assert not t.accepts(MessageType.WAKE, PowerLevel.EMERGENCY)
    b = Battery(5)
    b.consume(10)
    assert b.percent == 0


def test_base58_known_vectors():
    assert b58encode(b"hello world") == "StV1DL6CwTryKyV"
    assert b58encode(b"\x00\x00\x01") == "112"
    assert b58decode("112") == b"\x00\x00\x01"


def test_cid_v0_known_vector():
    # sha2-256 multihash of the empty string, as produced by the multiformats reference tooling
    assert cid_v0(b"") == "QmdfTbBqBPQ7VNxZEYEj14VmRuZBkqFbiwReogJgS1zR1n"
    cid = cid_v0(b"terrain")
    assert cid.startswith("Qm") and len(cid) == 46


def test_parse_ref_with_label():
    cid = cid_v0(b"x")
    assert parse_ref(f"ipfs://{cid}  (地形マップ v3.2)") == (cid, "地形マップ v3.2")
    assert parse_ref(cid) == (cid, None)
    with pytest.raises(AssetError):
        parse_ref("ipfs://QmXf3kR9v2T...")


def test_store_resolve(tmp_path):
    store = AssetStore(tmp_path)
    cid = store.put(b"map")
    assert store.resolve([f"ipfs://{cid} (map)"]) == {cid: b"map"}
    assert AssetStore(tmp_path).has(cid)  # persisted
    with pytest.raises(AssetNotFound):
        store.resolve([f"ipfs://{cid_v0(b'missing')}"])


def test_registries():
    m = ModelRegistry()
    assert m.name(m.code("ipap-local-v2")) == "ipap-local-v2"
    with pytest.raises(ValueError):
        m.register(9, "gpt-x")
    p = ProgramRegistry({"abc123": "def main(i, a): return 0"})
    assert p.resolve("program_id:abc123")[0] == "abc123"
    with pytest.raises(RegistryError):
        p.resolve("program_id:nope")
    v = TestVectorRegistry([TestVector("tc_001", 1, 2)])
    inline = TestVector("inline", 3, 4)
    assert [x.id for x in v.resolve(["tc_001", inline])] == ["tc_001", "inline"]
    with pytest.raises(RegistryError):
        v.resolve(["tc_404"])
