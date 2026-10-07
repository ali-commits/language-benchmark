import json

import pytest
from bench.baseline import wait_backend_baseline


def run(tmp_path, dirty=(), cpu=0, available=3000, eventual=False):
    clock = [0]
    index = [0]

    def probe():
        index[0] += 1
        bad = index[0] < 3 if eventual else True
        return {
            "epoch": clock[0],
            "cpu_counters": [cpu * index[0] if bad else 0, 0, 0, 100 * index[0], 0, 0, 0, 0],
            "available_memory_mib": available if bad else 3000,
            "unexpected_jobs": list(dirty) if bad else [],
            "containers": [],
        }

    return wait_backend_baseline(
        {},
        {"enabled": True},
        "stopped",
        tmp_path / "baseline.json",
        probe=probe,
        sleep=lambda seconds: clock.__setitem__(0, clock[0] + seconds),
        monotonic=lambda: clock[0],
    )


@pytest.mark.parametrize(
    "kwargs", [{"dirty": [{"pid": 9, "name": "dotnet"}]}, {"cpu": 100}, {"available": 100}]
)
def test_unexpected_jobs_high_cpu_or_low_memory_blocks_load(tmp_path, kwargs):
    with pytest.raises(RuntimeError, match="baseline did not settle"):
        run(tmp_path, **kwargs)
    assert json.loads((tmp_path / "baseline.json").read_text())["status"] == "timeout"


def test_eventual_clean_baseline_records_three_consecutive_low_samples(tmp_path):
    result = run(tmp_path, dirty=[{"pid": 9, "name": "k6"}], eventual=True)
    assert result["status"] == "ready" and result["samples"][-1]["consecutive_clean"] == 3
    assert result["interval_seconds"] == 5


def test_other_container_blocks_even_when_cpu_idle(tmp_path):
    clock = [0]

    def probe():
        return {
            "epoch": clock[0],
            "cpu_counters": [0, 0, 0, 100 + clock[0], 0, 0, 0, 0],
            "available_memory_mib": 3000,
            "unexpected_jobs": [],
            "containers": [{"id": "old", "name": "old-api"}],
        }

    with pytest.raises(RuntimeError):
        wait_backend_baseline(
            {},
            {"enabled": True},
            "stopped",
            tmp_path / "baseline.json",
            probe=probe,
            sleep=lambda s: clock.__setitem__(0, clock[0] + s),
            monotonic=lambda: clock[0],
        )


def test_baseline_detects_leftover_octane_workers_but_allows_current_container(tmp_path):
    import os
    import subprocess
    import sys

    from bench.baseline import HOST_PROBE

    root = tmp_path / "proc"
    root.mkdir()
    (root / "stat").write_text("cpu 0 0 0 100 0 0 0 0\n")
    (root / "meminfo").write_text("MemAvailable: 3000000 kB\n")
    for idx, name in enumerate(["php", "rr", "frankenphp"], 900001):
        p = root / str(idx)
        p.mkdir()
        (p / "comm").write_text(name)
        (p / "stat").write_text(str(idx) + " (" + name + ") S 0")
        (p / "cgroup").write_text("0::/system.slice/docker-current-api.scope")
    script = HOST_PROBE.replace("'/proc'", repr(str(root)))

    def probe(ids):
        return json.loads(
            subprocess.check_output(
                [sys.executable, "-c", script],
                env={**os.environ, "BASELINE_ALLOWED_IDS": json.dumps(ids)},
                text=True,
            )
        )

    assert {p["name"] for p in probe([])["unexpected_jobs"]} == {"php", "rr", "frankenphp"}
    assert not probe(["current-api"])["unexpected_jobs"]
