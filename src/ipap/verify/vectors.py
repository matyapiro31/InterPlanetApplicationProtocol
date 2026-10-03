"""Verification layer 2 (Section 9.1, MUST): run the test vectors bundled with the prompt."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

from ..payloads import TestVector
from .sandbox import Sandbox


@dataclass
class VectorResult:
    id: str
    passed: bool
    detail: str | None = None


def matches(actual: Any, expected: Any, tolerance: float | None = None) -> bool:
    if isinstance(expected, bool) or isinstance(actual, bool):
        return actual is expected if isinstance(expected, bool) else False
    if isinstance(expected, int | float) and isinstance(actual, int | float):
        if tolerance is None:
            return actual == expected
        return math.isclose(actual, expected, rel_tol=0.0, abs_tol=tolerance)
    if isinstance(expected, list) and isinstance(actual, list):
        return len(actual) == len(expected) and all(
            matches(a, e, tolerance) for a, e in zip(actual, expected, strict=True))
    if isinstance(expected, dict) and isinstance(actual, dict):
        return actual.keys() == expected.keys() and all(
            matches(actual[k], expected[k], tolerance) for k in expected)
    return actual == expected


async def run_vectors(sandbox: Sandbox, source: str, vectors: list[TestVector],
                      assets: dict[str, bytes], timeout_s: float) -> list[VectorResult]:
    if not vectors:
        return []
    result = await sandbox.run_calls(source, [v.input for v in vectors], assets,
                                     timeout_s * len(vectors))
    if result.error:
        return [VectorResult(v.id, False, result.error) for v in vectors]
    out = []
    for vector, call in zip(vectors, result.calls, strict=True):
        if not call.ok:
            out.append(VectorResult(vector.id, False, call.error))
        elif not matches(call.output, vector.expected, vector.tolerance):
            out.append(VectorResult(vector.id, False,
                                    f"expected {vector.expected!r}, got {call.output!r}"))
        else:
            out.append(VectorResult(vector.id, True))
    return out
