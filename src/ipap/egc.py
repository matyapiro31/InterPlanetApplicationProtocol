"""Earth Ground Control (EGC): composes prompts, runs the WAKE/READY handshake
(Section 7.2) and collects RESULT and STATUS reports.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field, replace
from typing import Any

from .constants import LLMAvailability, Priority
from .crypto import Identity
from .events import EventLog
from .link import SimClock
from .node import Transport
from .payloads import (
    AbortPayload,
    ExecPayload,
    Payload,
    ReadyPayload,
    ResultPayload,
    StatusPayload,
    WakePayload,
    describe,
)
from .session import Endpoint, ProtocolError


class HandshakeDeferred(Exception):
    """The node answered WAKE with something other than AVAILABLE."""

    def __init__(self, ready: ReadyPayload) -> None:
        super().__init__(f"node not ready: llm={ready.llm} reason={ready.reason}")
        self.ready = ready


@dataclass
class ExecOutcome:
    exec_seq: int
    result: ResultPayload
    ready: ReadyPayload | None = None
    round_trip_s: float = 0.0


@dataclass
class GroundStats:
    discarded: int = 0
    statuses: list[StatusPayload] = field(default_factory=list)


class GroundControl:
    actor = "earth"

    def __init__(self, endpoint: Endpoint, tx: Transport, rx: Transport, *,
                 clock: SimClock | None = None, log: EventLog | None = None,
                 compute_allowance_s: float = 0.0) -> None:
        self.endpoint = endpoint
        # Real seconds added to every deadline: on-board generation and verification take
        # wall-clock time that a compressed simulation clock does not scale down.
        self.compute_allowance_s = compute_allowance_s
        self.tx, self.rx = tx, rx
        self.clock = clock or SimClock()
        self.log = log or EventLog(self.clock.now)
        self._pending: dict[int, asyncio.Future[Payload]] = {}
        self.stats = GroundStats()

    @classmethod
    def build(cls, identity: Identity, node_public_key: bytes, tx: Transport, rx: Transport, *,
              encrypt: bool = False, clock: SimClock | None = None, **kwargs: Any
              ) -> GroundControl:
        clock = clock or SimClock()
        endpoint = Endpoint(identity, node_public_key, clock.now, encrypt=encrypt)
        return cls(endpoint, tx, rx, clock=clock, **kwargs)

    async def run(self) -> None:
        while True:
            data = await self.rx.recv()
            try:
                msg = self.endpoint.parse(data)
            except ProtocolError as e:
                self.stats.discarded += 1
                self.log(self.actor, "discard", f"discarded packet: {e}")
                continue
            self.log(self.actor, "recv",
                     f"recv {msg.msg_type.name} seq={msg.seq} {describe(msg.payload)}")
            fut = None
            if isinstance(msg.payload, StatusPayload):
                self.stats.statuses.append(msg.payload)
            elif isinstance(msg.payload, ReadyPayload | ResultPayload):
                fut = self._pending.pop(msg.payload.in_reply_to, None)
            if fut is not None and not fut.done():
                fut.set_result(msg.payload)

    def send(self, payload: Payload, priority: Priority | None = None) -> int:
        packet = self.endpoint.build(payload, priority=priority)
        raw = packet.encode()
        arrival = self.tx.send(raw)
        eta = f"eta T+{arrival:.0f}s" if arrival is not None else "LOST"
        self.log(self.actor, "send", f"send {payload.msg_type.name} seq={packet.seq} "
                                     f"priority={packet.priority.name} ({len(raw)} B, {eta})")
        return packet.seq

    def submit(self, payload: Payload, priority: Priority | None = None
               ) -> tuple[int, asyncio.Future[Payload]]:
        """Send a message and return a future for the reply that names its sequence number."""
        seq = self.endpoint.upcoming_seq
        fut: asyncio.Future[Payload] = asyncio.get_running_loop().create_future()
        self._pending[seq] = fut
        sent = self.send(payload, priority)
        assert sent == seq
        return seq, fut

    async def wait(self, fut: asyncio.Future[Payload], timeout_s: float | None) -> Payload:
        """Wait for a reply; ``timeout_s`` is in mission seconds (None waits forever)."""
        if timeout_s is None:
            return await fut
        return await asyncio.wait_for(
            fut, self.clock.to_real(timeout_s) + self.compute_allowance_s)

    async def wake(self, *, priority: Priority = Priority.NORMAL,
                   timeout_s: float | None = None) -> ReadyPayload:
        _, fut = self.submit(WakePayload(), priority)
        ready = await self.wait(fut, timeout_s)
        assert isinstance(ready, ReadyPayload)
        return ready

    async def execute(self, payload: ExecPayload, *, priority: Priority = Priority.HIGH,
                      handshake: bool | None = None, timeout_s: float | None = None
                      ) -> ExecOutcome:
        """Section 7.2: WAKE/READY first unless CRITICAL priority (handshake MAY be skipped)."""
        start = self.clock.now()
        if handshake is None:
            handshake = priority is not Priority.CRITICAL
        ready = None
        if handshake:
            ready = await self.wake(timeout_s=timeout_s)
            if ready.llm is not LLMAvailability.AVAILABLE:
                raise HandshakeDeferred(ready)
            if payload.llm_model is None:
                payload = replace(payload, llm_model=ready.llm_model)
        seq, fut = self.submit(payload, priority)
        result = await self.wait(fut, timeout_s)
        assert isinstance(result, ResultPayload)
        return ExecOutcome(seq, result, ready, self.clock.now() - start)

    def abort(self, target_seq: int | None = None, reason: str | None = None
              ) -> tuple[int, asyncio.Future[Payload]]:
        return self.submit(AbortPayload(target_seq=target_seq, reason=reason), Priority.CRITICAL)

    def request_status(self) -> int:
        return self.send(StatusPayload(), Priority.LOW)


__all__ = ["ExecOutcome", "GroundControl", "HandshakeDeferred"]
