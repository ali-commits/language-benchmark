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
