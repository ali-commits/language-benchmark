from .models import RuntimeCell


def validate_worker_snapshots(cell: RuntimeCell, snapshots: list[dict]) -> list[str]:
    reasons = []
    if len(snapshots) != cell.workers or len({s.get("pid") for s in snapshots}) != cell.workers:
        reasons.append("missing or duplicate worker readiness")
    if len({s.get("run_id") for s in snapshots}) != 1 or any(
        not s.get("run_id") for s in snapshots
    ):
        reasons.append("stale or missing run identity")
    for s in snapshots:
        if s.get("python") != cell.python:
            reasons.append("Python release mismatch")
        if s.get("gil") != cell.gil:
            reasons.append("GIL state mismatch after imports")
        if s.get("free_threaded") != (cell.build == "free-threaded"):
            reasons.append("build mode mismatch")
        if s.get("jit") != cell.jit or (cell.jit and not s.get("jit_available")):
            reasons.append("JIT unavailable or state mismatch")
    return reasons
