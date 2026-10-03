import json
from pathlib import Path

import pytest

from ipap import demo_data
from ipap.assets import cid_v0
from ipap.cli import main
from ipap.constants import MessageType
from ipap.payloads import TestVector, decode_payload
from ipap.sim import obstacle_exec, route_exec

EXAMPLES = Path(__file__).resolve().parent.parent / "examples"


def test_examples_match_demo_data():
    assert (EXAMPLES / "terrain_map.json").read_bytes() == demo_data.TERRAIN_MAP
    vectors = [TestVector.from_dict(d)
               for d in json.loads((EXAMPLES / "test_vectors.json").read_text())]
    assert vectors == demo_data.ALL_VECTORS
    obstacle_vectors = json.loads((EXAMPLES / "obstacle_vectors.json").read_text())
    assert [TestVector.from_dict(d) for d in obstacle_vectors] == demo_data.OBSTACLE_VECTORS
    obstacle = decode_payload(MessageType.EXEC, (EXAMPLES / "exec_obstacle.json").read_bytes())
    assert obstacle == obstacle_exec()
    route = decode_payload(MessageType.EXEC, (EXAMPLES / "exec_route.json").read_bytes())
    assert route == route_exec(cid_v0(demo_data.TERRAIN_MAP))


def test_keygen_encode_decode(tmp_path, capsys):
    assert main(["keygen", "earth", "--dir", str(tmp_path)]) == 0
    assert main(["keygen", "mars", "--dir", str(tmp_path)]) == 0
    out = tmp_path / "exec.bin"
    assert main(["encode", "--type", "exec", "--payload", str(EXAMPLES / "exec_obstacle.json"),
                 "--key", str(tmp_path / "earth.key"), "--peer", str(tmp_path / "mars.pub"),
                 "--encrypt", "--priority", "critical", "-o", str(out)]) == 0
    capsys.readouterr()

    assert main(["decode", str(out), "--peer", str(tmp_path / "earth.pub"),
                 "--key", str(tmp_path / "mars.key")]) == 0
    decoded = json.loads(capsys.readouterr().out)
    assert decoded["header"]["priority"] == "CRITICAL"
    assert decoded["signature"] == "valid"
    assert decoded["payload"]["fallback"] == "program_id:safe_stop"

    assert main(["decode", str(out), "--peer", str(tmp_path / "mars.pub")]) == 1
    assert "signature" in json.loads(capsys.readouterr().out)["error"]


def test_encode_hex_and_decode_unsigned_view(tmp_path, capsys):
    main(["keygen", "a", "--dir", str(tmp_path)])
    main(["keygen", "b", "--dir", str(tmp_path)])
    capsys.readouterr()
    main(["encode", "--type", "wake", "--key", str(tmp_path / "a.key"),
          "--peer", str(tmp_path / "b.pub")])
    hex_file = tmp_path / "wake.hex"
    hex_file.write_text(capsys.readouterr().out)
    assert main(["decode", str(hex_file)]) == 0
    decoded = json.loads(capsys.readouterr().out)
    assert decoded["header"]["type"] == "WAKE" and decoded["payload"]["ipap_version"] == "1.0"


def test_verify_command(capsys):
    args = ["verify", str(EXAMPLES / "obstacle_avoidance.py"),
            "--vectors", str(EXAMPLES / "test_vectors.json")]
    # Route vectors fail against the obstacle program, so the full set must fail ...
    assert main(args) == 1
    assert "FAILED" in capsys.readouterr().out
    # ... while the obstacle vectors alone pass, as does the fallback with no vectors.
    assert main(["verify", str(EXAMPLES / "obstacle_avoidance.py"),
                 "--vectors", str(EXAMPLES / "obstacle_vectors.json")]) == 0
    assert main(["verify", str(EXAMPLES / "fallback" / "safe_stop.py"), "--input", "{}"]) == 0


@pytest.mark.parametrize("scenario", ["critical", "low-power"])
def test_sim_command(scenario, capsys):
    assert main(["sim", scenario, "--time-scale", "0.0001", "-q"]) == 0
    assert "1/1 scenarios passed" in capsys.readouterr().out


def test_sim_unknown_scenario(capsys):
    assert main(["sim", "nope"]) == 2
