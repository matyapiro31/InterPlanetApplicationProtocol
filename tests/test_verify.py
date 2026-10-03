import asyncio

import pytest

from ipap.payloads import TestVector
from ipap.verify import Sandbox, Verifier, check_source
from ipap.verify.vectors import matches

GOOD = """
import math

def main(input, assets):
    return {"hyp": round(math.hypot(*input), 3), "n_assets": len(assets)}
"""


@pytest.mark.parametrize("src,needle", [
    ("def main(i, a):\n  import os\n  return os.getcwd()", "'os'"),
    ("from subprocess import run\ndef main(i, a): return 1", "subprocess"),
    ("def main(i, a): return eval('1')", "eval"),
    ("def main(i, a): return open('/etc/passwd').read()", "open"),
    ("def main(i, a): return ().__class__.__bases__", "__class__"),
    ("def main(i, a): return getattr(i, 'x')", "getattr"),
    ("def main(i):\n  return 1", "two positional"),
    ("def helper(i, a): return 1", "no top-level"),
    ("def main(i, a) return 1", "syntax"),
])
def test_static_rejects(src, needle):
    report = check_source(src)
    assert not report.passed
    assert any(needle in issue for issue in report.issues), report.issues


def test_static_accepts_good_program():
    assert check_source(GOOD).passed


async def test_sandbox_runs_program():
    res = await Sandbox().run_calls(GOOD, [[3, 4], [5, 12]], {"QmX": b"data"}, 5)
    assert res.ok, res
    assert [c.output for c in res.calls] == [{"hyp": 5.0, "n_assets": 1},
                                             {"hyp": 13.0, "n_assets": 1}]


async def test_sandbox_captures_exceptions_and_prints():
    src = "def main(i, a):\n    print('noise')\n    return 1 / i"
    res = await Sandbox().run_calls(src, [1, 0], {}, 5)
    assert res.calls[0].ok and res.calls[0].output == 1.0
    assert not res.calls[1].ok and "ZeroDivisionError" in res.calls[1].error
    assert "noise" in res.stdout


async def test_sandbox_runtime_import_guard():
    # Bypasses the static checker on purpose: the sandbox must still refuse.
    src = "def main(i, a):\n    import os\n    return os.getcwd()"
    res = await Sandbox().run(src, None, {}, 5)
    assert not res.ok and "not allowed" in res.calls[0].error


async def test_sandbox_timeout():
    res = await Sandbox().run("def main(i, a):\n    while True: pass", None, {}, 0.5)
    assert res.timed_out and not res.ok


async def test_sandbox_cancellation_kills_child():
    task = asyncio.create_task(Sandbox().run("def main(i, a):\n    while True: pass", None, {}, 30))
    await asyncio.sleep(0.3)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


def test_matches():
    assert matches([1, 2.0001], [1, 2], tolerance=0.001)
    assert not matches([1, 2.1], [1, 2], tolerance=0.001)
    assert not matches(True, 1) and not matches(1, True)
    assert matches({"a": [1]}, {"a": [1]})


async def test_pipeline_pass_and_fail():
    v = Verifier()
    vectors = [TestVector("t1", [3, 4], {"hyp": 5.0, "n_assets": 0})]
    ok = await v.verify(GOOD, vectors, {}, 5, run_input=[1, 1])
    assert ok.passed, ok.to_list()
    assert [layer.layer for layer in ok.layers] == ["static", "test_vectors", "sandbox"]

    bad = await v.verify(GOOD, [TestVector("t1", [3, 4], {"hyp": 6})], {}, 5)
    assert not bad.passed and bad.failure.startswith("test_vectors")

    none = await v.verify(GOOD, [], {}, 5)
    assert not none.passed and "no test vectors" in none.failure

    lenient = await Verifier(require_test_vectors=False).verify(GOOD, [], {}, 5, run_input=[0, 0])
    assert lenient.passed
