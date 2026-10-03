"""LLM engine backed by a local Ollama server — the closest analogue to an
on-board model. Uses temperature 0 and a fixed seed so the same prompt maps to
the same program as far as the runtime allows.
"""

from __future__ import annotations

import asyncio
import json
import urllib.error
import urllib.request

from .base import (
    PROGRAM_CONTRACT,
    GenerationRequest,
    GenerationResult,
    LLMError,
    build_user_prompt,
    extract_code,
)


class OllamaEngine:
    name = "ipap-ollama"

    def __init__(self, model: str = "qwen2.5-coder", *,
                 host: str = "http://localhost:11434", timeout_s: float = 600.0) -> None:
        self.model = model
        self.url = host.rstrip("/") + "/api/generate"
        self.timeout_s = timeout_s

    def _post(self, body: dict) -> dict:
        req = urllib.request.Request(self.url, data=json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout_s) as resp:
                return json.loads(resp.read())
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as e:
            raise LLMError(f"Ollama request failed: {e}") from e

    async def generate(self, request: GenerationRequest) -> GenerationResult:
        body = {
            "model": request.model if request.model and ":" in request.model else self.model,
            "system": PROGRAM_CONTRACT,
            "prompt": build_user_prompt(request),
            "stream": False,
            "options": {"temperature": 0, "seed": 0, "num_predict": request.max_tokens},
        }
        data = await asyncio.to_thread(self._post, body)
        if "error" in data:
            raise LLMError(f"Ollama error: {data['error']}")
        return GenerationResult(code=extract_code(data.get("response", "")), model=body["model"],
                                output_tokens=data.get("eval_count"))
