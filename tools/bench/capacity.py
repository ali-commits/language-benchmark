"""Concurrent-user search: invalid generator/evidence results never move backend bounds."""

from collections.abc import Callable
from .records import Outcome, CapacityResult


def search_capacity(
    probe: Callable[[int], Outcome],
    start: int = 10000,
    step: int = 10000,
    ceiling: int = 50000,
    resolution: int = 100,
    next_candidate: int | None = None,
    growth_strategy: str = "adaptive",
) -> CapacityResult:
    if not 1 <= start <= ceiling or step < 1 or resolution < 1:
        raise ValueError("invalid finite search bounds")
    if growth_strategy not in ("adaptive", "geometric"):
        raise ValueError("unknown search growth strategy")
    lower = 0
    upper = None
    observations: list[Outcome] = []

    def measure(users: int) -> str:
        result = probe(users)
        if result.get("outcome") not in ("pass", "fail", "invalid"):
            raise ValueError("unknown probe outcome")
        observations.append({"users": users, **result})
        return result["outcome"]

    candidate = start
    adaptive = next_candidate is not None and start < next_candidate <= ceiling
    growth = max(resolution, next_candidate - start) if adaptive and next_candidate is not None else step
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
        if adaptive and candidate == start and next_candidate is not None:
            candidate = next_candidate
        elif growth_strategy == "geometric":
            candidate = min(max(candidate + resolution, candidate * 2), ceiling)
        else:
            if adaptive:
                growth = min(step, growth * 2)
            candidate = min(candidate + growth, ceiling)
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
