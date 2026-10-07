from pathlib import Path

from bench.languages import LANGUAGES, cells
from bench.native_runtime import REGISTRY_LANGUAGES


def test_each_requested_pair_has_one_cell_and_controlled_connection_budget():
    # A newly implemented native stack can be registered independently of its app build.
    # The temporary C# registry tests exercise support before the real lock arrives.
    registered = [
        language for language in LANGUAGES if (Path("runtimes") / language / "lock.json").exists()
    ]
    rows = cells(registered)
    assert len(rows) == len(registered) and len({r["id"] for r in rows}) == len(registered)
    assert {r["language"] for r in rows} == {REGISTRY_LANGUAGES[name] for name in registered}
    assert all(r["workers"] * r["connections_per_worker"] == 20 for r in rows)
    assert all(Path(r["dockerfile"]).is_file() for r in rows)
    node = next(r for r in rows if r["language"] == "node")
    bun = next(r for r in rows if r["language"] == "bun")
    assert node["framework_version"] == bun["framework_version"] == "5.2.1"
