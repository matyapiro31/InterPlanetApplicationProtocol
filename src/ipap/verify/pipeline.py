"""Section 9.1: static analysis (MUST) -> test vectors (MUST) -> sandbox run (SHOULD).

Verification checks behavioural equivalence with the specification carried by
the prompt, not byte equality with any reference binary.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from ..payloads import TestVector
from .sandbox import Sandbox
from .static import check_source
from .vectors import run_vectors


@dataclass
class LayerResult:
    layer: str
    passed: bool
    detail: Any = None


@dataclass
class VerificationReport:
    layers: list[LayerResult] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return bool(self.layers) and all(layer.passed for layer in self.layers)

    @property
    def failure(self) -> str | None:
        for layer in self.layers:
            if not layer.passed:
                return f"{layer.layer}: {_brief(layer.detail)}"
        return None

    def to_list(self) -> list[dict[str, Any]]:
        return [asdict(layer) for layer in self.layers]


def _brief(detail: Any) -> str:
    if isinstance(detail, list) and detail:
        first = detail[0]
        if isinstance(first, dict):
            first = f"{first.get('id')}: {first.get('detail')}"
        more = f" (+{len(detail) - 1} more)" if len(detail) > 1 else ""
        return f"{first}{more}"
    return str(detail)


class Verifier:
    def __init__(self, sandbox: Sandbox | None = None, *, require_test_vectors: bool = True,
                 sandbox_check: bool = True) -> None:
        self.sandbox = sandbox or Sandbox()
        self.require_test_vectors = require_test_vectors
        self.sandbox_check = sandbox_check

    async def verify(self, source: str, vectors: list[TestVector], assets: dict[str, bytes],
                     timeout_s: float, run_input: Any = None) -> VerificationReport:
        report = VerificationReport()

        static = check_source(source)
        report.layers.append(LayerResult("static", static.passed, static.issues or None))
        if not static.passed:
            return report

        if not vectors:
            ok = not self.require_test_vectors
            report.layers.append(LayerResult("test_vectors", ok, "no test vectors supplied"))
            if not ok:
                return report
        else:
            results = await run_vectors(self.sandbox, source, vectors, assets, timeout_s)
            failed = [asdict(r) for r in results if not r.passed]
            report.layers.append(LayerResult(
                "test_vectors", not failed,
                failed or f"{len(results)}/{len(results)} passed"))
            if failed:
                return report

        if self.sandbox_check:
            dry = await self.sandbox.run(source, run_input, assets, timeout_s)
            detail = dry.error or (dry.calls[0].error if dry.calls else None)
            report.layers.append(LayerResult(
                "sandbox", dry.ok, detail or f"dry run ok in {dry.duration_ms:.0f} ms"))
        return report
