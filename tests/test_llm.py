from types import SimpleNamespace

import pytest

from ipap import demo_data
from ipap.assets import cid_v0
from ipap.llm import (
    AssetInfo,
    GenerationRequest,
    LLMError,
    MockLLMEngine,
    build_user_prompt,
    create_engine,
    extract_code,
)
from ipap.llm.anthropic_engine import AnthropicEngine
from ipap.llm.mock import BUGGY, FORBIDDEN, OBSTACLE_AVOIDANCE, ROUTE_OPTIMIZER
from ipap.verify import Sandbox, Verifier, check_source


def test_extract_code():
    assert extract_code("intro\n```python\nx = 1\n```\nbye") == "x = 1\n"
    assert extract_code("```\na\n```\n```py\nb\n```") == "b\n"
    assert extract_code("def main(i, a): pass") == "def main(i, a): pass\n"
    with pytest.raises(LLMError):
        extract_code("   ")


def test_user_prompt_lists_assets():
    text = build_user_prompt(GenerationRequest("do it", assets=[
        AssetInfo("QmA", 10, "terrain", '{"grid"')]))
    assert "QmA" in text and "terrain" in text and "do it" in text


async def test_mock_is_deterministic():
    engine = MockLLMEngine()
    a = await engine.generate(GenerationRequest(demo_data.OBSTACLE_PROMPT))
    b = await engine.generate(GenerationRequest(demo_data.OBSTACLE_PROMPT))
    assert a.code == b.code == OBSTACLE_AVOIDANCE
    assert (await engine.generate(GenerationRequest("[mock:buggy] obstacle"))).code == BUGGY
    with pytest.raises(LLMError):
        await engine.generate(GenerationRequest("bake a cake"))


@pytest.mark.parametrize("code,vectors,assets", [
    (OBSTACLE_AVOIDANCE, demo_data.OBSTACLE_VECTORS, {}),
    (ROUTE_OPTIMIZER, demo_data.ROUTE_VECTORS,
     {cid_v0(demo_data.TERRAIN_MAP): demo_data.TERRAIN_MAP}),
    (demo_data.SAFE_STOP, [], {}),
])
async def test_canned_programs_verify(code, vectors, assets):
    run_input = vectors[0].input if vectors else None
    verifier = Verifier(require_test_vectors=False)
    report = await verifier.verify(code, vectors, assets, 5, run_input=run_input)
    assert report.passed, report.to_list()


async def test_bad_programs_fail_verification():
    assert not check_source(FORBIDDEN).passed
    report = await Verifier(Sandbox()).verify(BUGGY, demo_data.OBSTACLE_VECTORS, {}, 5)
    assert not report.passed and report.failure.startswith("test_vectors")


class _FakeMessages:
    def __init__(self, response):
        self.response = response
        self.kwargs = None

    async def create(self, **kwargs):
        self.kwargs = kwargs
        return self.response


def _fake_client(stop_reason="end_turn", text="```python\ndef main(i, a):\n    return 1\n```"):
    response = SimpleNamespace(
        stop_reason=stop_reason, model="claude-opus-5-5",
        content=[SimpleNamespace(type="thinking", thinking=""),
                 SimpleNamespace(type="text", text=text)],
        usage=SimpleNamespace(output_tokens=42))
    messages = _FakeMessages(response)
    return SimpleNamespace(beta=SimpleNamespace(messages=messages)), messages


async def test_anthropic_engine_request_shape():
    client, messages = _fake_client()
    result = await AnthropicEngine(client=client).generate(GenerationRequest("x", max_tokens=2048))
    assert result.code == "def main(i, a):\n    return 1\n" and result.output_tokens == 42
    kw = messages.kwargs
    assert kw["model"] == "claude-opus-5-5"
    assert kw["thinking"] == {"type": "adaptive"}
    assert kw["fallbacks"] == "default"
    assert kw["max_tokens"] >= 16000


@pytest.mark.parametrize("reason", ["refusal", "max_tokens"])
async def test_anthropic_engine_stop_reasons(reason):
    client, _ = _fake_client(stop_reason=reason)
    with pytest.raises(LLMError):
        await AnthropicEngine(client=client).generate(GenerationRequest("x"))


def test_create_engine():
    assert isinstance(create_engine("mock"), MockLLMEngine)
    with pytest.raises(ValueError):
        create_engine("gpt")
