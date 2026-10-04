"""Concurrent-user search: invalid generator/evidence results never move backend bounds."""

from collections.abc import Callable


def search_capacity(
    probe: Callable[[int], dict],
    start: int = 10000,
    step: int = 10000,
    ceiling: int = 50000,
    resolution: int = 100,
) -> dict:
    if not 1 <= start <= ceiling or step < 1 or resolution < 1:
        raise ValueError("invalid finite search bounds")
    lower = 0
    upper = None
    observations = []

    def measure(users):
        result = probe(users)
        if result.get("outcome") not in ("pass", "fail", "invalid"):
            raise ValueError("unknown probe outcome")
        observations.append({"users": users, **result})
        return result["outcome"]

    candidate = start
    while candidate <= ceiling:
        outcome = measure(candidate)
        if outcome == "invalid":
            return {
                "status": "invalid",
                "passing_users": lower,
                "failing_users": upper,
                "observations": observations,
            }
        if outcome == "fail":
            upper = candidate
            break
        lower = candidate
        if candidate == ceiling:
            break
        candidate = min(candidate + step, ceiling)
    if upper is None:
        return {
            "status": "ceiling_reached",
            "passing_users": lower,
            "failing_users": None,
            "observations": observations,
        }
    while upper - lower > resolution:
        candidate = (lower + upper) // 2
        outcome = measure(candidate)
        if outcome == "invalid":
            return {
                "status": "invalid",
                "passing_users": lower,
                "failing_users": upper,
                "observations": observations,
            }
        if outcome == "pass":
            lower = candidate
        else:
            upper = candidate
    return {
        "status": "bracket_found",
        "passing_users": lower,
        "failing_users": upper,
        "resolution": resolution,
        "observations": observations,
    }
