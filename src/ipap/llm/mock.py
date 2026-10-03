"""A deterministic stand-in for an on-board LLM.

The mock maps prompt keywords to canned programs so that the same prompt always
"generates" the same program — the reproducibility property IPAP relies on.
Tags such as ``[mock:buggy]`` let tests and simulations force failure modes.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

from .base import GenerationRequest, GenerationResult, LLMError

OBSTACLE_AVOIDANCE = '''\
def main(input, assets):
    """Pick the heading with the most clearance from 8 lidar sectors (0 = straight ahead,
    sectors 45 degrees apart, clockwise). Ties prefer the smallest turn."""
    ranges = input["ranges"]
    min_clearance = input.get("min_clearance", 1.0)
    order = sorted(range(len(ranges)), key=lambda i: (-ranges[i], min(i, len(ranges) - i), i))
    best = order[0]
    if ranges[best] < min_clearance:
        return {"action": "stop", "turn_deg": 0}
    turn = best * 45 if best <= len(ranges) // 2 else (best - len(ranges)) * 45
    return {"action": "go", "turn_deg": turn}
'''

ROUTE_OPTIMIZER = '''\
import heapq
import json


def main(input, assets):
    """Dijkstra over a terrain cost grid taken from the first asset (-1 = impassable)."""
    grid = json.loads(next(iter(assets.values())).decode())["grid"]
    rows, cols = len(grid), len(grid[0])
    start, goal = tuple(input["start"]), tuple(input["goal"])
    dist = {start: 0}
    prev = {}
    queue = [(0, start)]
    while queue:
        d, cell = heapq.heappop(queue)
        if cell == goal:
            break
        if d > dist[cell]:
            continue
        r, c = cell
        for nxt in ((r + 1, c), (r - 1, c), (r, c + 1), (r, c - 1)):
            nr, nc = nxt
            if 0 <= nr < rows and 0 <= nc < cols and grid[nr][nc] >= 0:
                nd = d + grid[nr][nc]
                if nd < dist.get(nxt, float("inf")):
                    dist[nxt], prev[nxt] = nd, cell
                    heapq.heappush(queue, (nd, nxt))
    if goal not in dist:
        return {"reachable": False}
    path = [goal]
    while path[-1] != start:
        path.append(prev[path[-1]])
    return {"reachable": True, "cost": dist[goal], "path": [list(p) for p in reversed(path)]}
'''

BUGGY = '''\
def main(input, assets):
    # An off-by-one "hallucination": always turns one sector too far.
    ranges = input["ranges"]
    best = max(range(len(ranges)), key=lambda i: ranges[i])
    return {"action": "go", "turn_deg": (best + 1) * 45}
'''

FORBIDDEN = '''\
import os


def main(input, assets):
    return os.listdir("/")
'''

DEFAULT_RULES: list[tuple[tuple[str, ...], str]] = [
    (("[mock:buggy]",), BUGGY),
    (("[mock:forbidden]",), FORBIDDEN),
    (("obstacle", "障害物"), OBSTACLE_AVOIDANCE),
    (("route", "ルート", "経路"), ROUTE_OPTIMIZER),
]


class MockLLMEngine:
    name = "ipap-mock-v1"

    def __init__(
        self,
        rules: list[tuple[tuple[str, ...], str]] | None = None,
        *,
        latency_s: float = 0.0,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self.rules = DEFAULT_RULES if rules is None else rules
        self.latency_s = latency_s
        self.sleep = sleep
        self.calls: list[GenerationRequest] = []

    async def generate(self, request: GenerationRequest) -> GenerationResult:
        self.calls.append(request)
        if self.latency_s:
            await self.sleep(self.latency_s)
        text = request.prompt.lower()
        for keywords, code in self.rules:
            if any(k.lower() in text for k in keywords):
                return GenerationResult(code=code, model=self.name,
                                        output_tokens=len(code) // 4)
        raise LLMError("mock engine has no program matching this prompt")
