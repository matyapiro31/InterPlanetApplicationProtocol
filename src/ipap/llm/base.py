"""The LLM engine interface used by the remote node (Section 4.1 "LLM Engine")."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from ..verify.static import ALLOWED_MODULES


class LLMError(Exception):
    """Generation failed (engine unavailable, refusal, truncated or empty output)."""


@dataclass
class AssetInfo:
    cid: str
    size: int
    label: str | None = None
    preview: str | None = None  # leading text of the asset, when it decodes as UTF-8


@dataclass
class GenerationRequest:
    prompt: str
    language: str = "python"
    max_tokens: int = 2048
    assets: list[AssetInfo] = field(default_factory=list)
    model: str | None = None


@dataclass
class GenerationResult:
    code: str
    model: str
    output_tokens: int | None = None


@runtime_checkable
class LLMEngine(Protocol):
    name: str

    async def generate(self, request: GenerationRequest) -> GenerationResult: ...


PROGRAM_CONTRACT = f"""\
You are the code-generation engine of an IPAP remote node (a rover or planetary base).
Ground control sends a natural-language specification; you return one Python program
that implements it. The program is verified against hidden test vectors and then run
unattended, so correctness matters more than brevity.

Program contract:
- Define a top-level function `main(input, assets)`.
  `input` is a JSON value; `assets` maps IPFS CIDs to the asset bytes.
- Return a JSON-serializable value (dict, list, str, int, float, bool or None).
- You may import only these standard-library modules: {", ".join(sorted(ALLOWED_MODULES))}.
- Do not use open, eval, exec, compile, getattr, setattr, globals, locals, vars,
  __import__ or dunder attributes. Do not read files, the network or the environment.
- Be deterministic: if you use `random`, seed it.

Reply with the program only, in a single ```python code block."""


def build_user_prompt(request: GenerationRequest) -> str:
    parts = [f"Specification:\n{request.prompt}"]
    if request.assets:
        lines = []
        for a in request.assets:
            line = f"- {a.cid} ({a.label or 'unlabelled'}, {a.size} bytes)"
            if a.preview:
                line += f"\n  begins with: {a.preview!r}"
            lines.append(line)
        parts.append("Assets available in `assets`:\n" + "\n".join(lines))
    parts.append(f"Language: {request.language}. Keep the program under "
                 f"{request.max_tokens} tokens.")
    return "\n\n".join(parts)


_FENCE = re.compile(r"```(?:python|py)?[ \t]*\n(.*?)```", re.DOTALL)


def extract_code(text: str) -> str:
    """Take the last fenced code block, or the whole reply if there is none."""
    blocks = _FENCE.findall(text)
    code = (blocks[-1] if blocks else text).strip()
    if not code:
        raise LLMError("engine returned no code")
    return code + "\n"
