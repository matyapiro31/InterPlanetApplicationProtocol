"""Reference data shared by the simulator, the CLI and the tests: a terrain map
asset, pre-installed test vectors and a pre-installed fallback program.
"""

from __future__ import annotations

import json

from .payloads import TestVector

TERRAIN_MAP = json.dumps({
    "name": "Jezero crater sector 7, v3.2",
    "grid": [
        [1, 3, 3, 3, 3],
        [1, -1, -1, -1, 1],
        [1, 1, 1, -1, 1],
        [-1, -1, 1, -1, 1],
        [1, 1, 1, 1, 1],
    ],
}).encode()

SAFE_STOP = '''\
def main(input, assets):
    """Pre-installed fallback: stop in place."""
    return {"action": "stop", "turn_deg": 0}
'''

FALLBACK_PROGRAMS = {"safe_stop": SAFE_STOP}

OBSTACLE_VECTORS = [
    TestVector("tc_001", {"ranges": [5, 1, 1, 1, 1, 1, 1, 1]}, {"action": "go", "turn_deg": 0}),
    TestVector("tc_002", {"ranges": [1, 1, 6, 1, 1, 1, 1, 1]}, {"action": "go", "turn_deg": 90}),
    TestVector("tc_003", {"ranges": [1, 1, 1, 1, 1, 1, 7, 1]}, {"action": "go", "turn_deg": -90}),
    TestVector("tc_004", {"ranges": [0.5] * 8}, {"action": "stop", "turn_deg": 0}),
    TestVector("tc_005", {"ranges": [1, 3, 1, 1, 1, 1, 1, 3]}, {"action": "go", "turn_deg": 45}),
]

ROUTE_VECTORS = [
    TestVector("rt_001", {"start": [0, 0], "goal": [4, 4]}, {
        "reachable": True, "cost": 8,
        "path": [[0, 0], [1, 0], [2, 0], [2, 1], [2, 2], [3, 2], [4, 2], [4, 3], [4, 4]],
    }),
    TestVector("rt_002", {"start": [0, 0], "goal": [1, 1]}, {"reachable": False}),
    TestVector("rt_003", {"start": [4, 0], "goal": [4, 2]},
               {"reachable": True, "cost": 2, "path": [[4, 0], [4, 1], [4, 2]]}),
]

ALL_VECTORS = OBSTACLE_VECTORS + ROUTE_VECTORS

OBSTACLE_PROMPT = (
    "障害物回避アルゴリズムを生成せよ。入力 input['ranges'] は8方向(0=正面, 45度刻み時計回り)の"
    "LiDAR距離。最も開けた方向を選び {'action': 'go', 'turn_deg': 角度} を返す。"
    "右旋回は正、左旋回は負(±180は+180)。同距離なら旋回量が小さい方、それも同じなら番号の小さい方。"
    "最大距離が input.get('min_clearance', 1.0) 未満なら {'action': 'stop', 'turn_deg': 0}。"
)

ROUTE_PROMPT = (
    "地形データを解析し走行ルートを最適化せよ。資産のJSONの grid は各セルに進入するコスト"
    "(-1は通行不可)。input の start から goal まで上下左右移動で総コスト最小の経路を求め、"
    "{'reachable': True, 'cost': 総コスト, 'path': [[r, c], ...]} を返す。"
    "到達不能なら {'reachable': False}。"
)
