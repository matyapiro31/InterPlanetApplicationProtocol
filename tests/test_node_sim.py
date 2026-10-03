import asyncio
from dataclasses import replace

import pytest

from ipap import demo_data
from ipap.constants import LLMAvailability, NodeState, Priority, Status
from ipap.link import Link, LinkConfig, SimClock
from ipap.llm import MockLLMEngine
from ipap.node import TRANSITIONS, IllegalTransition, RemoteNode
from ipap.payloads import AbortPayload, ResultPayload, TestVector
from ipap.sim import SCENARIOS, SimOptions, World, obstacle_exec, run_scenario

FAST = SimOptions(time_scale=1e-4, mock_latency_s=10.0)


@pytest.mark.parametrize("name", list(SCENARIOS))
async def test_scenario(name):
    result = await run_scenario(name, FAST)
    assert result.passed, result.summary


def test_state_machine_matches_rfc_diagram():
    assert TRANSITIONS[NodeState.IDLE] == {NodeState.READY_CHECK}
    assert NodeState.ROLLBACK in TRANSITIONS[NodeState.VERIFYING]
    assert NodeState.EXECUTING in TRANSITIONS[NodeState.VERIFYING]
    assert TRANSITIONS[NodeState.REPORTING] == {NodeState.IDLE}
    for state, targets in TRANSITIONS.items():
        if state not in (NodeState.IDLE, NodeState.REPORTING):
            assert NodeState.REPORTING in targets, f"{state} cannot report an ABORT"


def test_illegal_transition():
    w = World(FAST)
    with pytest.raises(IllegalTransition):
        w.node._to(NodeState.EXECUTING)


async def _exec(w: World, payload, priority=Priority.HIGH):
    _, fut = w.egc.submit(payload, priority)
    result = await w.egc.wait(fut, w.timeout())
    assert isinstance(result, ResultPayload)
    return result


async def test_busy_node_rejects_second_exec():
    slow = MockLLMEngine(latency_s=5000)
    async with World(FAST, llm=slow) as w:
        slow.sleep = w.clock.sleep
        first_seq, first = w.egc.submit(obstacle_exec())
        second = await _exec(w, obstacle_exec())
        assert second.status is Status.REJECTED and second.error == "BUSY"
        w.egc.abort(first_seq)
        assert (await asyncio.wait_for(first, 5)).status is Status.ABORTED


async def test_abort_without_job():
    async with World(FAST) as w:
        _, fut = w.egc.submit(AbortPayload(reason="nothing to do"), Priority.CRITICAL)
        result = await asyncio.wait_for(fut, 5)
    assert result.status is Status.REJECTED and result.error == "NO_ACTIVE_JOB"


async def test_rejections_before_generation():
    async with World(FAST) as w:
        wrong_model = replace(obstacle_exec(), llm_model="ipap-local-v9")
        assert (await _exec(w, wrong_model)).status is Status.REJECTED
        p = obstacle_exec()
        p.constraints.test_vectors = ["tc_404"]
        r = await _exec(w, p)
        assert r.status is Status.REJECTED and "tc_404" in r.error
        p = obstacle_exec()
        p.constraints.language = "rust"
        assert (await _exec(w, p)).status is Status.REJECTED
        assert w.node.llm.calls == []  # never woke the LLM


async def test_llm_failure_rolls_back_to_fallback():
    async with World(FAST) as w:
        r = await _exec(w, replace(obstacle_exec(), prompt="bake a cake"))
    assert r.status is Status.VERIFY_FAILED
    assert r.error.startswith("generation failed")
    assert r.program == "fallback:safe_stop"


async def test_normal_power_halves_timeout_and_consumes_battery():
    async with World(replace(FAST, battery=50.0)) as w:
        r = await _exec(w, obstacle_exec())
    assert r.status is Status.OK
    assert r.metrics["timeout_s"] == 5.0  # 10 s * 0.5
    assert r.metrics["power_after"] < r.metrics["power_before"]


async def test_inline_vectors_and_return_code():
    p = obstacle_exec()
    p.constraints.test_vectors = [TestVector("inline", {"ranges": [9] + [1] * 7},
                                             {"action": "go", "turn_deg": 0})]
    p.constraints.return_code = True
    async with World(FAST) as w:
        r = await _exec(w, p)
    assert r.status is Status.OK and "def main" in r.code
    assert r.verification[1]["layer"] == "test_vectors"


async def test_status_reports():
    async with World(replace(FAST, status_interval_s=600)) as w:
        ready = await w.egc.wake(timeout_s=w.timeout())
        assert ready.llm is LLMAvailability.AVAILABLE
        w.egc.request_status()
        await w.clock.sleep(2 * w.rtt_s)
    assert w.egc.stats.statuses and w.egc.stats.statuses[0].state == "IDLE"


async def test_link_bandwidth_and_loss():
    clock = SimClock(1e-4)
    link = Link(LinkConfig(one_way_delay_s=100, bandwidth_bps=8000, loss_rate=1.0), clock)
    assert link.uplink.send(b"x" * 1000) is None
    link = Link(LinkConfig(one_way_delay_s=100, bandwidth_bps=8000), clock)
    t1 = link.uplink.send(b"x" * 1000)  # 1 s on the wire
    t2 = link.uplink.send(b"x" * 1000)  # queued behind the first
    assert t2 - t1 == pytest.approx(1.0, abs=0.01)
    assert await asyncio.wait_for(link.uplink.recv(), 1) == b"x" * 1000


async def test_node_survives_garbage():
    async with World(FAST) as w:
        w.link.uplink.send(b"\x00garbage")
        ready = await w.egc.wake(timeout_s=w.timeout())
    assert ready.llm is LLMAvailability.AVAILABLE
    assert w.node.stats.discarded == 1


def test_build_helpers_share_keys():
    w = World(FAST)
    assert isinstance(w.node, RemoteNode)
    assert w.node.endpoint.peer_public_key == w.egc.endpoint.identity.public_key
    assert w.assets.has(w.terrain_cid) and demo_data.TERRAIN_MAP
