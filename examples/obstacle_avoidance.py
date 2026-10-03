def main(input, assets):
    """Pick the heading with the most clearance from 8 lidar sectors (0 = straight ahead,
    sectors 45 degrees apart, clockwise). Ties prefer the smallest turn."""
    ranges = input["ranges"]
    min_clearance = input.get("min_clearance", 1.0)
    order = sorted(range(len(ranges)), key=lambda i: (-ranges[i], min(i, len(ranges) - i), i))
    best = order[0]
    if ranges[best] < min_clearance:
        return {"action": "stop", "turn_deg": 0}
    turn = best * 45 if best <= len(ranges) // 2 else (best - len(ranges)) * 45
    return {"action": "go", "turn_deg": turn}
