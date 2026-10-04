from bench.models import RuntimeCell
from bench.runtime import validate_worker_snapshots


def test_reenabled_gil_rejects_free_threaded_cell():
    cell = RuntimeCell(id="ft", python="3.15.0rc3", app="modern", build="free-threaded", gil=False)
    rows = [
        dict(
            pid=i,
            run_id="current",
            python="3.15.0rc3",
            gil=True,
            free_threaded=True,
            jit=False,
            jit_available=False,
        )
        for i in [1, 2]
    ]
    assert any("GIL" in r for r in validate_worker_snapshots(cell, rows))


def test_missing_duplicate_or_stale_worker_rejects_readiness():
    cell = RuntimeCell(id="std", python="3.14.8")
    assert validate_worker_snapshots(cell, [])
    row = dict(
        pid=1,
        run_id="current",
        python="3.14.8",
        gil=True,
        free_threaded=False,
        jit=False,
        jit_available=False,
    )
    assert validate_worker_snapshots(cell, [row, row])


def test_jit_enabled_requires_available_build():
    cell = RuntimeCell(id="jit", python="3.15.0rc3", app="modern", build="jit", jit=True)
    rows = [
        dict(
            pid=i,
            run_id="current",
            python=cell.python,
            gil=True,
            free_threaded=False,
            jit=True,
            jit_available=False,
        )
        for i in [1, 2]
    ]
    assert validate_worker_snapshots(cell, rows)
