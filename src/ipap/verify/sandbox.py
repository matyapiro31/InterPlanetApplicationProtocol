"""Isolated execution of generated programs in a child interpreter.

Isolation measures: a fresh ``python -I -S`` process in an empty temporary
directory with a scrubbed environment, POSIX resource limits (CPU, address
space, file size, open files, process count), a wall-clock timeout, a
restricted ``__builtins__`` for the program module and an import allowlist.
Cancelling ``run_calls`` kills the child, which is how ABORT stops execution.

This is not a hardened sandbox (no seccomp/namespaces, no network isolation);
on a real spacecraft it would be backed by an OS- or hypervisor-level jail.
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .static import ALLOWED_MODULES, RUNTIME_REMOVED_BUILTINS

RESULT_MARKER = "\x1eIPAP-RESULT "
MAX_STDOUT_BYTES = 1 << 20

_RUNNER = r'''
import builtins, contextlib, io, json, sys, base64

def _run():
    req = json.loads(sys.stdin.read())
    allowed = set(req["allowed"])
    real_import = builtins.__import__

    def guarded_import(name, globals=None, locals=None, fromlist=(), level=0):
        if level != 0 or name.split(".")[0] not in allowed:
            raise ImportError(f"import of {name!r} is not allowed")
        return real_import(name, globals, locals, fromlist, level)

    safe = {k: v for k, v in vars(builtins).items() if k not in set(req["forbidden"])}
    safe["__import__"] = guarded_import
    ns = {"__name__": "ipap_program", "__builtins__": safe}
    assets = {k: base64.b64decode(v) for k, v in req["assets"].items()}
    captured = io.StringIO()
    results = []
    with contextlib.redirect_stdout(captured):
        try:
            exec(compile(req["source"], "<program>", "exec"), ns)
            main = ns["main"]
        except BaseException as e:
            results = [{"ok": False, "error": f"load failed: {type(e).__name__}: {e}"}
                       for _ in req["inputs"]]
            main = None
        if main is not None:
            for value in req["inputs"]:
                try:
                    out = main(value, dict(assets))
                    json.dumps(out)
                    results.append({"ok": True, "output": out})
                except BaseException as e:
                    results.append({"ok": False, "error": f"{type(e).__name__}: {e}"})
    report = {"results": results, "stdout": captured.getvalue()[-4096:]}
    sys.__stdout__.write(req["marker"] + json.dumps(report) + "\n")

_run()
'''


@dataclass
class CallResult:
    ok: bool
    output: Any = None
    error: str | None = None


@dataclass
class SandboxResult:
    calls: list[CallResult] = field(default_factory=list)
    timed_out: bool = False
    error: str | None = None  # harness-level failure (crash, limit hit, bad output)
    stdout: str = ""
    duration_ms: float = 0.0

    @property
    def ok(self) -> bool:
        return self.error is None and not self.timed_out and all(c.ok for c in self.calls)


@dataclass
class SandboxLimits:
    memory_bytes: int = 512 * 1024 * 1024
    file_size_bytes: int = 1024 * 1024
    open_files: int = 64


class Sandbox:
    def __init__(
        self,
        limits: SandboxLimits | None = None,
        allowed_modules: frozenset[str] = ALLOWED_MODULES,
        python: str = sys.executable,
    ) -> None:
        self.limits = limits or SandboxLimits()
        self.allowed_modules = allowed_modules
        self.python = python

    def _preexec(self, cpu_seconds: int):
        limits = self.limits

        def apply() -> None:
            import resource

            def setlim(res: int, value: int) -> None:
                try:
                    resource.setrlimit(res, (value, value))
                except (ValueError, OSError):
                    pass

            setlim(resource.RLIMIT_CPU, cpu_seconds)
            setlim(resource.RLIMIT_AS, limits.memory_bytes)
            setlim(resource.RLIMIT_FSIZE, limits.file_size_bytes)
            setlim(resource.RLIMIT_NOFILE, limits.open_files)
            setlim(resource.RLIMIT_CORE, 0)
            if hasattr(resource, "RLIMIT_NPROC"):
                setlim(resource.RLIMIT_NPROC, 0)
            os.setsid()

        return apply

    async def run(self, source: str, value: Any, assets: dict[str, bytes],
                  timeout_s: float) -> SandboxResult:
        return await self.run_calls(source, [value], assets, timeout_s)

    async def run_calls(self, source: str, inputs: list[Any], assets: dict[str, bytes],
                        timeout_s: float) -> SandboxResult:
        request = json.dumps({
            "source": source,
            "inputs": inputs,
            "assets": {k: base64.b64encode(v).decode() for k, v in assets.items()},
            "allowed": sorted(self.allowed_modules),
            "forbidden": sorted(RUNTIME_REMOVED_BUILTINS),
            "marker": RESULT_MARKER,
        }).encode()
        start = time.monotonic()
        with tempfile.TemporaryDirectory(prefix="ipap-sandbox-") as tmp:
            runner = Path(tmp) / "runner.py"
            runner.write_text(_RUNNER)
            proc = await asyncio.create_subprocess_exec(
                self.python, "-I", "-S", "-B", str(runner),
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=tmp,
                env={"PYTHONHASHSEED": "0", "LANG": "C.UTF-8"},
                preexec_fn=self._preexec(max(1, int(timeout_s) + 1)),
            )
            try:
                stdout, stderr = await asyncio.wait_for(proc.communicate(request), timeout_s)
            except TimeoutError:
                await self._kill(proc)
                return SandboxResult(timed_out=True, error=f"timed out after {timeout_s:.3f}s",
                                     duration_ms=(time.monotonic() - start) * 1000)
            except asyncio.CancelledError:
                await self._kill(proc)
                raise
        duration = (time.monotonic() - start) * 1000
        return self._parse(proc.returncode, stdout, stderr, len(inputs), duration)

    @staticmethod
    async def _kill(proc: asyncio.subprocess.Process) -> None:
        if proc.returncode is None:
            try:
                os.killpg(proc.pid, 9)
            except (ProcessLookupError, PermissionError):
                proc.kill()
            await proc.wait()

    @staticmethod
    def _parse(code: int | None, stdout: bytes, stderr: bytes, n: int,
               duration: float) -> SandboxResult:
        text = stdout[-MAX_STDOUT_BYTES:].decode("utf-8", "replace")
        idx = text.rfind(RESULT_MARKER)
        if code != 0 or idx < 0:
            tail = stderr.decode("utf-8", "replace").strip().splitlines()[-1:] or [""]
            return SandboxResult(error=f"sandbox process exited with {code}: {tail[0]}",
                                 duration_ms=duration)
        try:
            data = json.loads(text[idx + len(RESULT_MARKER):])
            calls = [CallResult(r["ok"], r.get("output"), r.get("error")) for r in data["results"]]
        except (json.JSONDecodeError, KeyError, TypeError) as e:
            return SandboxResult(error=f"malformed sandbox output: {e}", duration_ms=duration)
        if len(calls) != n:
            return SandboxResult(error="sandbox returned wrong number of results",
                                 duration_ms=duration)
        return SandboxResult(calls=calls, stdout=data.get("stdout", ""), duration_ms=duration)
