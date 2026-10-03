"""LLM engine backed by the Claude API (requires ``pip install ipap[anthropic]``).

On a real mission the engine runs on board; this adapter stands in for it when
evaluating how reliably a frontier model turns IPAP prompts into verifiable code.
"""

from __future__ import annotations

from typing import Any

from .base import (
    PROGRAM_CONTRACT,
    GenerationRequest,
    GenerationResult,
    LLMError,
    build_user_prompt,
    extract_code,
)

DEFAULT_MODEL = "claude-opus-5-5"


class AnthropicEngine:
    name = "ipap-claude"

    def __init__(self, model: str = DEFAULT_MODEL, *, effort: str = "high",
                 max_tokens: int = 16000, client: Any = None) -> None:
        self.model = model
        self.effort = effort
        # The EXEC max_tokens budgets the program itself; the API limit also has to
        # cover adaptive thinking, so it is never set below this floor.
        self.max_tokens = max_tokens
        self._client = client

    def _get_client(self) -> Any:
        if self._client is None:
            try:
                import anthropic
            except ImportError as e:
                raise LLMError("the 'anthropic' package is not installed; "
                               "run: pip install 'ipap[anthropic]'") from e
            self._client = anthropic.AsyncAnthropic()
        return self._client

    async def generate(self, request: GenerationRequest) -> GenerationResult:
        client = self._get_client()
        try:
            response = await client.beta.messages.create(
                model=self.model,
                max_tokens=max(self.max_tokens, request.max_tokens),
                system=PROGRAM_CONTRACT,
                messages=[{"role": "user", "content": build_user_prompt(request)}],
                thinking={"type": "adaptive"},
                output_config={"effort": self.effort},
                betas=["server-side-fallback-2026-07-01"],
                fallbacks="default",
            )
        except LLMError:
            raise
        except Exception as e:  # anthropic.APIError and transport errors
            raise LLMError(f"Claude API request failed: {type(e).__name__}: {e}") from e
        if response.stop_reason == "refusal":
            raise LLMError("Claude declined the request")
        if response.stop_reason == "max_tokens":
            raise LLMError("generation hit max_tokens before finishing")
        text = "".join(b.text for b in response.content if b.type == "text")
        return GenerationResult(code=extract_code(text), model=response.model,
                                output_tokens=response.usage.output_tokens)
