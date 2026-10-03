"""End-to-end Earth <-> Mars simulations over the delayed link."""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field, replace
from typing import Any

from . import demo_data
from .assets import AssetStore, cid_v0
from .constants import HEADER_SIZE, MessageType, NodeState, Priority, Status
from .crypto import Identity
from .egc import ExecOutcome, GroundControl, HandshakeDeferred
from .events import EventLog
from .link import Link, LinkConfig, SimClock
from .llm import LLMEngine, MockLLMEngine, create_engine
from .node import NodeConfig, RemoteNode
from .payloads import Constraints, ExecPayload, ResultPayload
from .power import Battery
from .registry import ProgramRegistry, TestVectorRegistry
from .verify import Verifier

NOMINAL_BINARY_BYTES = 100 * 1024 * 1024  # README: "a 100 MB software update"


@dataclass
class SimOptions:
    time_scale: float = 1e-3
    link: LinkConfig = field(default_factory=LinkConfig)
    battery: float = 87.0
    llm: str = "mock"
    llm_kwargs: dict[str, Any] = field(default_factory=dict)
    mock_latency_s: float = 45.0
    encrypt: bool = True
    status_interval_s: float | None = None
    compute_allowance_s: float = 30.0
    echo: Callable[[str], Any] | None = None


class World:
    """Ground control, a remote node and the link between them."""

    def __init__(self, opts: SimOptions, *, llm: LLMEngine | None = None,
                 uplink_tamper: Callable[[bytes], bytes] | None = None) -> None:
        self.opts = opts
        self.clock = SimClock(opts.time_scale)
        self.log = EventLog(self.clock.now, opts.echo)
        self.link = Link(opts.link, self.clock, uplink_tamper=uplink_tamper)
        earth, mars = Identity.generate(), Identity.generate()
        self.assets = AssetStore()
        self.terrain_cid = self.assets.put(demo_data.TERRAIN_MAP)
        if llm is None:
            if opts.llm == "mock":
                llm = MockLLMEngine(latency_s=opts.mock_latency_s, sleep=self.clock.sleep)
            else:
                llm = create_engine(opts.llm, **opts.llm_kwargs)
        self.node = RemoteNode.build(
            mars, earth.public_key, self.link.uplink, self.link.downlink,
            encrypt=opts.encrypt, clock=self.clock, log=self.log, llm=llm,
            battery=Battery(opts.battery), assets=self.assets,
            programs=ProgramRegistry(demo_data.FALLBACK_PROGRAMS),
            vectors=TestVectorRegistry(demo_data.ALL_VECTORS),
            verifier=Verifier(),
            config=NodeConfig(status_interval_s=opts.status_interval_s))
        self.egc = GroundControl.build(
            earth, mars.public_key, self.link.uplink, self.link.downlink,
            encrypt=opts.encrypt, clock=self.clock, log=self.log,
            compute_allowance_s=opts.compute_allowance_s)
        self._tasks: list[asyncio.Task] = []

    @property
    def rtt_s(self) -> float:
        return 2 * self.opts.link.one_way_delay_s

    def timeout(self, round_trips: int = 2) -> float:
        """A generous mission-time deadline for ``round_trips`` exchanges."""
        return round_trips * self.rtt_s + 3600 + max(
            (b1 - b0 for b0, b1 in self.opts.link.blackouts), default=0)

    async def __aenter__(self) -> World:
        self._tasks = [asyncio.create_task(self.node.run()), asyncio.create_task(self.egc.run())]
        return self

    async def __aexit__(self, *exc: object) -> None:
        for task in self._tasks:
            task.cancel()
        for task in self._tasks:
            with contextlib.suppress(asyncio.CancelledError):
                await task


@dataclass
class ScenarioResult:
    name: str
    title: str
    passed: bool
    summary: str
    details: dict[str, Any] = field(default_factory=dict)


def obstacle_exec(prompt: str = demo_data.OBSTACLE_PROMPT) -> ExecPayload:
    return ExecPayload(
        prompt=prompt,
        constraints=Constraints(timeout_ms=10_000,
                                test_vectors=[v.id for v in demo_data.OBSTACLE_VECTORS]),
        input={"ranges": [2.0, 1.5, 0.8, 0.4, 0.3, 0.6, 3.2, 2.5]},
        fallback="program_id:safe_stop",
    )


def route_exec(terrain_cid: str) -> ExecPayload:
    return ExecPayload(
        prompt=demo_data.ROUTE_PROMPT,
        assets=[f"ipfs://{terrain_cid}  (地形マップ v3.2)"],
        constraints=Constraints(timeout_ms=10_000,
                                test_vectors=[v.id for v in demo_data.ROUTE_VECTORS]),
        input={"start": [0, 0], "goal": [4, 4]},
        fallback="program_id:safe_stop",
    )


def _uplink_bytes(world: World) -> int:
    return world.link.uplink.bytes_sent


async def scenario_happy(opts: SimOptions) -> ScenarioResult:
    async with World(opts) as w:
        payload = route_exec(w.terrain_cid)
        out = await w.egc.execute(payload, timeout_s=w.timeout())
    r = out.result
    exec_bytes = w.link.uplink.transmissions[-1].size
    code_bytes = (r.metrics or {}).get("code_bytes", 0)
    bw = opts.link.bandwidth_bps
    details = {
        "result": r.to_dict(),
        "exec_packet_bytes": exec_bytes,
        "uplink_bytes_total": _uplink_bytes(w),
        "generated_code_bytes": code_bytes,
        "exec_uplink_s": exec_bytes * 8 / bw,
        "binary_uplink_s": NOMINAL_BINARY_BYTES * 8 / bw,
        "round_trip_s": out.round_trip_s,
    }
    ok = r.status is Status.OK and r.output and r.output.get("cost") == 8
    summary = (f"WAKE→READY→EXEC→RESULT status={r.status}, route cost="
               f"{(r.output or {}).get('cost')}; EXEC packet {exec_bytes} B "
               f"({details['exec_uplink_s']:.1f} s at {bw / 1000:g} kbps) vs. 100 MB binary "
               f"({details['binary_uplink_s'] / 3600:.1f} h)")
    return ScenarioResult("happy", "", bool(ok), summary, details)


async def scenario_low_power(opts: SimOptions) -> ScenarioResult:
    async with World(replace(opts, battery=20.0)) as w:
        try:
            await w.egc.execute(obstacle_exec(), timeout_s=w.timeout())
        except HandshakeDeferred as e:
            ready = e.ready
            ok = ready.llm == "DEFERRED" and ready.reason == "LOW_POWER"
            return ScenarioResult("low-power", "", ok,
                                  f"READY llm={ready.llm} reason={ready.reason} at "
                                  f"power={ready.power}% — EXEC not sent",
                                  {"ready": ready.to_dict()})
    return ScenarioResult("low-power", "", False, "node did not defer")


async def scenario_verify_fail(opts: SimOptions) -> ScenarioResult:
    async with World(opts, llm=_mock(opts)) as w:
        out = await w.egc.execute(obstacle_exec("[mock:buggy] " + demo_data.OBSTACLE_PROMPT),
                                  timeout_s=w.timeout())
        history = list(w.node.history)
    r = out.result
    ok = (r.status is Status.VERIFY_FAILED and r.program == "fallback:safe_stop"
          and r.output == {"action": "stop", "turn_deg": 0} and NodeState.ROLLBACK in history)
    return ScenarioResult("verify-fail", "", ok,
                          f"status={r.status} program={r.program} output={r.output}; "
                          f"reason: {r.error}", {"result": r.to_dict()})


async def scenario_missing_asset(opts: SimOptions) -> ScenarioResult:
    async with World(opts, llm=_mock(opts)) as w:
        payload = route_exec(cid_v0(b"not on mars"))
        out = await w.egc.execute(payload, timeout_s=w.timeout())
    r = out.result
    return ScenarioResult("missing-asset", "", r.status is Status.RESOLVE_FAILED,
                          f"status={r.status}: {r.error}", {"result": r.to_dict()})


async def scenario_abort(opts: SimOptions) -> ScenarioResult:
    slow = MockLLMEngine(latency_s=4 * opts.link.one_way_delay_s)
    async with World(opts, llm=slow) as w:
        slow.sleep = w.clock.sleep
        seq, fut = w.egc.submit(obstacle_exec(), Priority.CRITICAL)
        await w.clock.sleep(60)
        w.egc.abort(seq, reason="operator abort: hazard detected")
        result = await w.egc.wait(fut, w.timeout())
        history = list(w.node.history)
    assert isinstance(result, ResultPayload)
    ok = result.status is Status.ABORTED and NodeState.LLM_ACTIVE in history
    return ScenarioResult("abort", "", ok,
                          f"ABORT reached the node during {history[-3]}; status={result.status} "
                          f"({result.error})", {"result": result.to_dict()})


async def scenario_tamper(opts: SimOptions) -> ScenarioResult:
    def flip_exec_byte(data: bytes) -> bytes:
        if data[0] & 0x0F == MessageType.EXEC:
            data = bytearray(data)
            data[HEADER_SIZE + 4] ^= 0x20
            return bytes(data)
        return data

    async with World(opts, llm=_mock(opts), uplink_tamper=flip_exec_byte) as w:
        attempt = asyncio.create_task(w.egc.execute(obstacle_exec(), timeout_s=None))
        while w.node.stats.discarded == 0 and not attempt.done():
            await w.clock.sleep(60)
        await w.clock.sleep(w.rtt_s)  # give a (wrongly) accepted EXEC time to report
        answered = attempt.done()
        attempt.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await attempt
        discarded = w.node.stats.discarded
        executed = len(w.node.stats.results)
    ok = not answered and discarded == 1 and executed == 0
    return ScenarioResult("tamper", "", ok,
                          f"tampered EXEC discarded by the node (signature check), "
                          f"discarded={discarded}, executed={executed}")


async def scenario_critical(opts: SimOptions) -> ScenarioResult:
    async with World(opts, llm=_mock(opts)) as w:
        out = await w.egc.execute(obstacle_exec(), priority=Priority.CRITICAL,
                                  timeout_s=w.timeout())
        sent = [e.text.split()[1] for e in w.log.find("send", "earth")]
    r = out.result
    ok = r.status is Status.OK and "WAKE" not in sent
    return ScenarioResult("critical", "", ok,
                          f"handshake skipped (sent: {', '.join(sent)}); status={r.status} "
                          f"output={r.output}; round trip {out.round_trip_s / 60:.0f} min",
                          {"result": r.to_dict()})


async def scenario_emergency(opts: SimOptions) -> ScenarioResult:
    async with World(replace(opts, battery=5.0), llm=_mock(opts)) as w:
        out = await w.egc.execute(obstacle_exec(), priority=Priority.CRITICAL,
                                  timeout_s=w.timeout())
    r = out.result
    ok = r.status is Status.REJECTED and r.error == "EMERGENCY_POWER"
    return ScenarioResult("emergency", "", ok, f"status={r.status} ({r.error}) at 5% power")


async def scenario_conjunction(opts: SimOptions) -> ScenarioResult:
    blackout = (0.0, 4 * 3600.0)
    link = replace(opts.link, blackouts=[blackout])
    # Hours of blackout: run this one ten times faster than the other scenarios.
    fast = replace(opts, link=link, time_scale=opts.time_scale / 10)
    async with World(fast, llm=_mock(opts)) as w:
        out = await w.egc.execute(obstacle_exec(), priority=Priority.CRITICAL,
                                  timeout_s=w.timeout())
    r = out.result
    ok = r.status is Status.OK and out.round_trip_s >= blackout[1]
    return ScenarioResult("conjunction", "", ok,
                          f"EXEC held during a {blackout[1] / 3600:.0f} h blackout and delivered "
                          f"afterwards; status={r.status}, round trip "
                          f"{out.round_trip_s / 3600:.1f} h")


def _mock(opts: SimOptions) -> MockLLMEngine | None:
    """Scenarios that rely on the mock's failure tags always use the mock engine."""
    return None if opts.llm == "mock" else MockLLMEngine()


Scenario = Callable[[SimOptions], Awaitable[ScenarioResult]]

SCENARIOS: dict[str, tuple[str, Scenario]] = {
    "happy": ("Route optimisation with an IPFS asset", scenario_happy),
    "low-power": ("WAKE at 20% battery is deferred", scenario_low_power),
    "verify-fail": ("Hallucinated program fails test vectors -> fallback", scenario_verify_fail),
    "missing-asset": ("Unknown CID -> RESOLVE_FAILED", scenario_missing_asset),
    "abort": ("CRITICAL ABORT interrupts generation", scenario_abort),
    "tamper": ("Packet modified in transit is discarded", scenario_tamper),
    "critical": ("CRITICAL EXEC skips the WAKE handshake", scenario_critical),
    "emergency": ("EMERGENCY power rejects EXEC", scenario_emergency),
    "conjunction": ("Solar-conjunction blackout, store-and-forward", scenario_conjunction),
}


async def run_scenario(name: str, opts: SimOptions) -> ScenarioResult:
    title, fn = SCENARIOS[name]
    result = await fn(opts)
    result.title = title
    return result


__all__ = ["SCENARIOS", "ExecOutcome", "ScenarioResult", "SimOptions", "World", "run_scenario"]
