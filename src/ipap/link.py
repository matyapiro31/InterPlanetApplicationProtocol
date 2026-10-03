"""Simulated deep-space link (the IPAP/TRANS + IPAP/PHY layers of Section 4.1).

Each direction is a store-and-forward channel with serialization delay
(bandwidth), one-way light-time delay, random loss and blackout windows such
as solar conjunction, during which bundles are held until the link returns.
Mission time runs ``1 / time_scale`` times faster than wall-clock time.
"""

from __future__ import annotations

import asyncio
import random
import time
from collections.abc import Callable
from dataclasses import dataclass, field


class SimClock:
    def __init__(self, time_scale: float = 1.0) -> None:
        if time_scale <= 0:
            raise ValueError("time_scale must be positive")
        self.time_scale = time_scale
        self._start = time.monotonic()

    def now(self) -> float:
        """Mission seconds elapsed since the clock started."""
        return (time.monotonic() - self._start) / self.time_scale

    def to_real(self, mission_seconds: float) -> float:
        return max(0.0, mission_seconds) * self.time_scale

    async def sleep(self, mission_seconds: float) -> None:
        await asyncio.sleep(self.to_real(mission_seconds))


def format_mission_time(t: float) -> str:
    t = max(0.0, t)
    h, rem = divmod(int(t), 3600)
    m, s = divmod(rem, 60)
    return f"T+{h:02d}:{m:02d}:{s:02d}"


@dataclass
class LinkConfig:
    one_way_delay_s: float = 600.0  # Earth-Mars: 180..1320 s
    bandwidth_bps: float = 10_000.0  # DSN: 10 kbps..8 Mbps
    loss_rate: float = 0.0
    blackouts: list[tuple[float, float]] = field(default_factory=list)
    seed: int = 0


@dataclass
class Transmission:
    sent_at: float
    arrives_at: float | None  # None when lost
    size: int


class Channel:
    def __init__(self, name: str, config: LinkConfig, clock: SimClock,
                 tamper: Callable[[bytes], bytes] | None = None) -> None:
        self.name = name
        self.config = config
        self.clock = clock
        self.tamper = tamper
        self._rng = random.Random(f"{config.seed}:{name}")
        self._queue: asyncio.Queue[bytes] = asyncio.Queue()
        self._tx_free_at = 0.0
        self.transmissions: list[Transmission] = []

    @property
    def bytes_sent(self) -> int:
        return sum(t.size for t in self.transmissions)

    def _schedule(self, now: float, size: int) -> tuple[float, float]:
        duration = size * 8 / self.config.bandwidth_bps
        start = max(now, self._tx_free_at)
        for b0, b1 in sorted(self.config.blackouts):
            if start < b1 and start + duration > b0:
                start = b1  # store-and-forward: hold the bundle until the link returns
        self._tx_free_at = start + duration
        return start, start + duration

    def send(self, data: bytes) -> float | None:
        """Queue ``data`` for delivery and return its mission arrival time (None if lost)."""
        now = self.clock.now()
        _, tx_end = self._schedule(now, len(data))
        if self._rng.random() < self.config.loss_rate:
            self.transmissions.append(Transmission(now, None, len(data)))
            return None
        arrival = tx_end + self.config.one_way_delay_s
        if self.tamper is not None:
            data = self.tamper(data)
        asyncio.get_running_loop().call_later(
            self.clock.to_real(arrival - now), self._queue.put_nowait, data)
        self.transmissions.append(Transmission(now, arrival, len(data)))
        return arrival

    async def recv(self) -> bytes:
        return await self._queue.get()


class Link:
    """A pair of channels between Earth Ground Control and a remote node."""

    def __init__(self, config: LinkConfig, clock: SimClock, *,
                 uplink_tamper: Callable[[bytes], bytes] | None = None) -> None:
        self.config = config
        self.uplink = Channel("earth->mars", config, clock, uplink_tamper)
        self.downlink = Channel("mars->earth", config, clock)
