"""Pluggable LLM engines."""

from __future__ import annotations

from typing import Any

from .base import (
    PROGRAM_CONTRACT,
    AssetInfo,
    GenerationRequest,
    GenerationResult,
    LLMEngine,
    LLMError,
    build_user_prompt,
    extract_code,
)
from .mock import MockLLMEngine

ENGINES = ("mock", "anthropic", "ollama")


def create_engine(kind: str, **kwargs: Any) -> LLMEngine:
    if kind == "mock":
        return MockLLMEngine(**kwargs)
    if kind == "anthropic":
        from .anthropic_engine import AnthropicEngine

        return AnthropicEngine(**kwargs)
    if kind == "ollama":
        from .ollama_engine import OllamaEngine

        return OllamaEngine(**kwargs)
    raise ValueError(f"unknown LLM engine {kind!r}; choose from {', '.join(ENGINES)}")


__all__ = [
    "ENGINES",
    "PROGRAM_CONTRACT",
    "AssetInfo",
    "GenerationRequest",
    "GenerationResult",
    "LLMEngine",
    "LLMError",
    "MockLLMEngine",
    "build_user_prompt",
    "create_engine",
    "extract_code",
]
