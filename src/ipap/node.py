"""The IPAP remote node (rover, lander or planetary base): Sections 6, 7, 8 and 9."""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
from dataclasses import dataclass, field
from typing import Any, Protocol

from .assets import AssetError, AssetStore, parse_ref
from .constants import LLMAvailability, MessageType, NodeState, Priority, Status
from .crypto import Identity
from .events import EventLog
from .link import SimClock
from .llm import AssetInfo, GenerationRequest, LLMEngine, LLMError
from .payloads import (
    AbortPayload,
    ExecPayload,
    Payload,
    ReadyPayload,
    ResultPayload,
    StatusPayload,
    describe,
)
from .power import Battery, PowerLevel, PowerThresholds
from .registry import ModelRegistry, ProgramRegistry, RegistryError, TestVectorRegistry
from .session import Endpoint, Message, ProtocolError
from .verify import Sandbox, Verifier

TRANSITIONS: dict[NodeState, frozenset[NodeState]] = {
    NodeState.IDLE: frozenset({NodeState.READY_CHECK}),
    NodeState.READY_CHECK: frozenset({NodeState.IDLE, NodeState.DEFER, NodeState.LLM_ACTIVE,
                                      NodeState.REPORTING}),
    NodeState.DEFER: frozenset({NodeState.IDLE, NodeState.REPORTING}),
    NodeState.LLM_ACTIVE: frozenset({NodeState.VERIFYING, NodeState.ROLLBACK,
                                     NodeState.REPORTING}),
    NodeState.VERIFYING: frozenset({NodeState.EXECUTING, NodeState.ROLLBACK,
                                    NodeState.REPORTING}),
    NodeState.ROLLBACK: frozenset({NodeState.REPORTING}),
    NodeState.EXECUTING: frozenset({NodeState.REPORTING}),
    NodeState.REPORTING: frozenset({NodeState.IDLE}),
}


class IllegalTransition(RuntimeError):
    pass


class Transport(Protocol):
    def send(self, data: bytes) -> Any: ...

    async def recv(self) -> bytes: ...


@dataclass
class NodeConfig:
    llm_power_cost: float = 2.0  # percent of battery per generation
    exec_power_cost: float = 0.2  # percent of battery per execution
    max_timeout_ms: int = 120_000
    status_interval_s: float | None = None  # mission seconds between STATUS reports
    preview_bytes: int = 120


@dataclass
class Job:
    seq: int
    task: asyncio.Task
    abort_reason: str | None = None


@dataclass
class NodeStats:
    discarded: int = 0
    results: list[ResultPayload] = field(default_factory=list)


class RemoteNode:
    actor = "mars"

    def __init__(
        self,
        endpoint: Endpoint,
        rx: Transport,
        tx: Transport,
        *,
        llm: LLMEngine,
        battery: Battery | None = None,
        assets: AssetStore | None = None,
        programs: ProgramRegistry | None = None,
        vectors: TestVectorRegistry | None = None,
        verifier: Verifier | None = None,
        executor: Sandbox | None = None,
        models: ModelRegistry | None = None,
        thresholds: PowerThresholds | None = None,
        config: NodeConfig | None = None,
        clock: SimClock | None = None,
        log: EventLog | None = None,
    ) -> None:
        self.endpoint = endpoint
        self.rx, self.tx = rx, tx
        self.llm = llm
        self.battery = battery or Battery()
        self.assets = assets or AssetStore()
        self.programs = programs or ProgramRegistry()
        self.vectors = vectors or TestVectorRegistry()
        self.verifier = verifier or Verifier()
        self.executor = executor or self.verifier.sandbox
        self.models = models or ModelRegistry()
        self.thresholds = thresholds or PowerThresholds()
        self.config = config or NodeConfig()
        self.clock = clock or SimClock()
        self.log = log or EventLog(self.clock.now)
        self.state = NodeState.IDLE
        self.history: list[NodeState] = [NodeState.IDLE]
        self.job: Job | None = None
        self.stats = NodeStats()

    @classmethod
    def build(cls, identity: Identity, ground_public_key: bytes, rx: Transport, tx: Transport,
              *, encrypt: bool = False, clock: SimClock | None = None, **kwargs: Any
              ) -> RemoteNode:
        clock = clock or SimClock()
        endpoint = Endpoint(identity, ground_public_key, clock.now, encrypt=encrypt)
        return cls(endpoint, rx, tx, clock=clock, **kwargs)

    # -- state machine -------------------------------------------------------------

    def _to(self, new: NodeState) -> None:
        if new not in TRANSITIONS[self.state]:
            raise IllegalTransition(f"{self.state} -> {new}")
        self.log(self.actor, "state", f"state {self.state} -> {new}")
        self.state = new
        self.history.append(new)

    @property
    def power_level(self) -> PowerLevel:
        return self.thresholds.classify(self.battery.percent)

    @property
    def busy(self) -> bool:
        return self.job is not None and not self.job.task.done()

    # -- I/O -----------------------------------------------------------------------

    def _send(self, payload: Payload, priority: Priority | None = None) -> None:
        try:
            llm_version = self.models.code(self.llm.name)
        except RegistryError:
            llm_version = 0
        packet = self.endpoint.build(payload, priority=priority, llm_version=llm_version)
        raw = packet.encode()
        self.log(self.actor, "send", f"send {payload.msg_type.name} seq={packet.seq} "
                                     f"({len(raw)} B) {describe(payload)}")
        self.tx.send(raw)

    async def run(self) -> None:
        status_task = None
        if self.config.status_interval_s:
            status_task = asyncio.create_task(self._status_loop(self.config.status_interval_s))
        try:
            while True:
                await self.handle(await self.rx.recv())
        finally:
            if status_task:
                status_task.cancel()
            if self.busy:
                self.job.task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await self.job.task

    async def _status_loop(self, interval: float) -> None:
        while True:
            await self.clock.sleep(interval)
            self._send(self._status())

    async def handle(self, data: bytes) -> None:
        try:
            msg = self.endpoint.parse(data)
        except ProtocolError as e:
            self.stats.discarded += 1
            self.log(self.actor, "discard", f"discarded packet: {e}")
            return
        self.log(self.actor, "recv", f"recv {msg.msg_type.name} seq={msg.seq} "
                                     f"priority={msg.packet.priority.name}")
        level = self.power_level
        if not self.thresholds.accepts(msg.msg_type, level):
            self._reject_for_power(msg)
            return
        if msg.msg_type is MessageType.WAKE:
            self._on_wake(msg)
        elif msg.msg_type is MessageType.EXEC:
            self._on_exec(msg)
        elif msg.msg_type is MessageType.ABORT:
            self._on_abort(msg)
        elif msg.msg_type is MessageType.STATUS:
            self._send(self._status())
        else:
            self.log(self.actor, "ignore", f"ignoring unexpected {msg.msg_type.name}")

    # -- handlers ------------------------------------------------------------------

    def _ready(self, in_reply_to: int, llm: LLMAvailability, reason: str | None) -> ReadyPayload:
        return ReadyPayload(in_reply_to=in_reply_to, power=round(self.battery.percent, 1),
                            power_level=self.power_level.value, llm=llm,
                            llm_model=self.llm.name, reason=reason)

    def _status(self) -> StatusPayload:
        return StatusPayload(state=self.state.value, power=round(self.battery.percent, 1),
                             power_level=self.power_level.value,
                             llm=self._availability().value,
                             active_seq=self.job.seq if self.busy else None)

    def _availability(self) -> LLMAvailability:
        if self.busy:
            return LLMAvailability.UNAVAILABLE
        if self.thresholds.llm_allowed(self.power_level):
            return LLMAvailability.AVAILABLE
        return LLMAvailability.DEFERRED

    def _reject_for_power(self, msg: Message) -> None:
        self.log(self.actor, "reject", f"EMERGENCY power: rejecting {msg.msg_type.name}")
        if msg.msg_type is MessageType.WAKE:
            self._send(self._ready(msg.seq, LLMAvailability.UNAVAILABLE, "EMERGENCY_POWER"))
        elif msg.msg_type is MessageType.EXEC:
            self._send(ResultPayload(in_reply_to=msg.seq, status=Status.REJECTED,
                                     error="EMERGENCY_POWER"))

    def _on_wake(self, msg: Message) -> None:
        if self.busy:
            self._send(self._ready(msg.seq, LLMAvailability.UNAVAILABLE, "BUSY"))
            return
        self._to(NodeState.READY_CHECK)
        if self.thresholds.llm_allowed(self.power_level):
            self._send(self._ready(msg.seq, LLMAvailability.AVAILABLE, None))
        else:
            self._to(NodeState.DEFER)
            self._send(self._ready(msg.seq, LLMAvailability.DEFERRED, "LOW_POWER"))
        self._to(NodeState.IDLE)

    def _on_exec(self, msg: Message) -> None:
        if self.busy:
            self._send(ResultPayload(in_reply_to=msg.seq, status=Status.REJECTED, error="BUSY"))
            return
        task = asyncio.create_task(self._run_job(msg))
        self.job = Job(msg.seq, task)

    def _on_abort(self, msg: Message) -> None:
        payload = msg.payload
        assert isinstance(payload, AbortPayload)
        if self.busy and payload.target_seq in (None, self.job.seq):
            self.log(self.actor, "abort", f"aborting job seq={self.job.seq}")
            self.job.abort_reason = payload.reason or "aborted by ground control"
            self.job.task.cancel()
            return
        self._send(ResultPayload(in_reply_to=msg.seq, status=Status.REJECTED,
                                 error="NO_ACTIVE_JOB"))

    # -- EXEC pipeline (Section 4.2) -------------------------------------------------

    async def _run_job(self, msg: Message) -> None:
        payload = msg.payload
        assert isinstance(payload, ExecPayload)
        metrics: dict[str, Any] = {"power_before": round(self.battery.percent, 1)}
        try:
            result = await self._pipeline(msg.seq, payload, metrics)
        except asyncio.CancelledError:
            job = self.job
            if job is None or job.abort_reason is None:
                raise  # node shutting down, not an ABORT
            result = ResultPayload(in_reply_to=msg.seq, status=Status.ABORTED,
                                   error=job.abort_reason)
        metrics["power_after"] = round(self.battery.percent, 1)
        result.metrics = {**(result.metrics or {}), **metrics}
        self._to(NodeState.REPORTING)
        self.stats.results.append(result)
        self._send(result)
        self._to(NodeState.IDLE)

    async def _pipeline(self, seq: int, p: ExecPayload, metrics: dict[str, Any]) -> ResultPayload:
        def result(status: Status, **kw: Any) -> ResultPayload:
            return ResultPayload(in_reply_to=seq, status=status, **kw)

        self._to(NodeState.READY_CHECK)
        level = self.power_level
        if not self.thresholds.llm_allowed(level):
            self._to(NodeState.DEFER)
            return result(Status.DEFERRED, error="LOW_POWER")
        if p.constraints.language.lower() != "python":
            return result(Status.REJECTED, error=f"unsupported language {p.constraints.language}")
        if p.llm_model and p.llm_model != self.llm.name:
            return result(Status.REJECTED, error=f"model {p.llm_model} not installed")
        timeout_ms = min(p.constraints.timeout_ms, self.config.max_timeout_ms)
        timeout_s = timeout_ms / 1000 * self.thresholds.timeout_factor(level)
        metrics["timeout_s"] = timeout_s

        try:
            assets = self.assets.resolve(p.assets)
        except AssetError as e:
            return result(Status.RESOLVE_FAILED, error=f"asset not resolvable: {e}")
        try:
            vectors = self.vectors.resolve(p.constraints.test_vectors)
        except RegistryError as e:
            return result(Status.REJECTED, error=str(e.args[0]))

        self._to(NodeState.LLM_ACTIVE)
        self.battery.consume(self.config.llm_power_cost)
        t0 = self.clock.now()
        try:
            gen = await self.llm.generate(GenerationRequest(
                prompt=p.prompt, language=p.constraints.language,
                max_tokens=p.constraints.max_tokens, assets=self._asset_info(p.assets, assets),
                model=p.llm_model))
        except LLMError as e:
            return await self._rollback(seq, p, assets, f"generation failed: {e}", None, None)
        metrics["generation_s"] = round(self.clock.now() - t0, 1)
        code = gen.code
        digest = hashlib.sha256(code.encode()).hexdigest()
        metrics["code_bytes"] = len(code.encode())
        self.log(self.actor, "generated",
                 f"generated {len(code.encode())} B program sha256={digest[:12]}")

        self._to(NodeState.VERIFYING)
        report = await self.verifier.verify(code, vectors, assets, timeout_s, p.input)
        self.log(self.actor, "verified",
                 "verification " + ("passed" if report.passed else f"FAILED ({report.failure})"))
        if not report.passed:
            return await self._rollback(seq, p, assets, report.failure, report.to_list(), digest)

        self._to(NodeState.EXECUTING)
        self.battery.consume(self.config.exec_power_cost)
        run = await self.executor.run(code, p.input, assets, timeout_s)
        metrics["exec_ms"] = round(run.duration_ms, 1)
        extra = {"program": "generated", "code_sha256": digest,
                 "verification": report.to_list(),
                 "code": code if p.constraints.return_code else None}
        if not run.ok:
            error = run.error or run.calls[0].error
            return result(Status.EXEC_FAILED, error=error, **extra)
        return result(Status.OK, output=run.calls[0].output, **extra)

    async def _rollback(self, seq: int, p: ExecPayload, assets: dict[str, bytes], reason: str,
                        verification: list[dict[str, Any]] | None,
                        digest: str | None) -> ResultPayload:
        """Section 9.2: discard the generated code, switch to the fallback, report VERIFY_FAILED."""
        self._to(NodeState.ROLLBACK)
        out = ResultPayload(in_reply_to=seq, status=Status.VERIFY_FAILED, error=reason,
                            code_sha256=digest, verification=verification)
        if not p.fallback:
            return out
        try:
            program_id, source = self.programs.resolve(p.fallback)
        except RegistryError as e:
            out.error = f"{reason}; {e.args[0]}"
            return out
        self.log(self.actor, "fallback", f"running fallback program {program_id}")
        run = await self.executor.run(source, p.input, assets,
                                      self.config.max_timeout_ms / 1000)
        out.program = f"fallback:{program_id}"
        if run.ok:
            out.output = run.calls[0].output
        else:
            out.error = f"{reason}; fallback failed: {run.error or run.calls[0].error}"
        return out

    def _asset_info(self, refs: list[str], resolved: dict[str, bytes]) -> list[AssetInfo]:
        infos = []
        for ref in refs:
            cid, label = parse_ref(ref)
            data = resolved[cid]
            try:
                preview = data[: self.config.preview_bytes].decode()
            except UnicodeDecodeError:
                preview = None
            infos.append(AssetInfo(cid, len(data), label, preview))
        return infos

