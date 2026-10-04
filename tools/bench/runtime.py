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


def validate_dependency_inventory(cell: RuntimeCell, snapshots: list[dict]) -> list[str]:
    import re
    from pathlib import Path

    from packaging.markers import default_environment
    from packaging.requirements import Requirement
    from packaging.utils import canonicalize_name

    environment = default_environment()
    environment["python_version"] = ".".join(cell.python.split(".")[:2])
    environment["python_full_version"] = cell.python
    expected = {}
    for line in Path(cell.dependency_lock).read_text().splitlines():
        if not re.match(r"^[A-Za-z0-9_.-]+==", line):
            continue
        requirement = Requirement(line.rstrip().removesuffix("\\").strip())
        if requirement.marker and not requirement.marker.evaluate(environment):
            continue
        expected[canonicalize_name(requirement.name)] = next(iter(requirement.specifier)).version
    reasons = []
    for snapshot in snapshots:
        observed = {canonicalize_name(k): v for k, v in snapshot.get("dependencies", {}).items()}
        for package, version in expected.items():
            if observed.get(package) != version:
                reasons.append(f"dependency mismatch: {package}, expected {version}")
    return reasons
