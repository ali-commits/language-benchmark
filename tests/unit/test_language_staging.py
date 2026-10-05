from pathlib import Path

from bench.languages import LANGUAGES, cells


def test_each_requested_pair_has_one_cell_and_controlled_connection_budget():
    rows = cells(LANGUAGES)
    assert len(rows) == 6 and len({r["id"] for r in rows}) == 6
    assert {r["language"] for r in rows} == set(LANGUAGES)
    assert all(r["workers"] * r["connections_per_worker"] == 20 for r in rows)
    assert all(Path(r["dockerfile"]).is_file() for r in rows)
    node = next(r for r in rows if r["language"] == "node")
    bun = next(r for r in rows if r["language"] == "bun")
    assert node["framework_version"] == bun["framework_version"] == "5.2.1"
