import json

import pytest
from bench.resources import parse_memory_bytes, summarize_resources


def test_docker_cpu_normalizes_to_allocated_two_cpu_and_working_set_mib(tmp_path):
    path = tmp_path / "telemetry.jsonl"
    samples = []
    for t, cpu, memory in [(100, 100, "100MiB"), (110, 200, "200MiB"), (125, 20, "50MiB")]:
        samples.append(
            {
                "time": t,
                "docker": "\n".join(
                    json.dumps(x)
                    for x in [
                        {
                            "Name": "language-benchmark-backend-backend-1",
                            "CPUPerc": f"{cpu}%",
                            "MemUsage": memory + " / 3GiB",
                        },
                        {"Name": "db", "CPUPerc": "500%", "MemUsage": "20GiB / 32GiB"},
                    ]
                ),
            }
        )
    path.write_text("\n".join(json.dumps(x) for x in samples))
    resources = summarize_resources(
        path, {"steady": [100, 120], "drain": [120, 130]}, allocated_cpus=2
    )
    steady = resources["phases"]["steady"]
    assert steady["cpu_percent_mean"] == 75
    assert steady["cpu_percent_peak"] == 100
    assert steady["memory_working_set_mib_mean"] == 150
    assert steady["memory_working_set_mib_peak"] == 200
    assert resources["phases"]["drain"]["cpu_percent_mean"] == 10
    assert "sampled" in resources["limitation"]


@pytest.mark.parametrize(
    "value,expected", [("1GiB", 2**30), ("100MiB", 100 * 2**20), ("1.5GB", 1500000000)]
)
def test_docker_human_memory_units(value, expected):
    assert parse_memory_bytes(value) == expected


def test_phase_windows_use_actual_k6_start_not_launcher_clock():
    from bench.resources import phase_windows

    result = {"execution_started_epoch": 80}
    cfg = {"ramp_seconds": 180, "settle_seconds": 0, "hold_seconds": 240}
    windows = phase_windows(result, cfg, {"scenario_started_epoch": 100})
    assert windows["steady"] == [280, 520]
    assert phase_windows(result, cfg, {}) == {}


def test_external_resources_keep_generator_and_rds_budgets_separate(tmp_path):
    from bench.resources import summarize_generator_resources, summarize_rds_resources

    path = tmp_path / "hosts.jsonl"
    path.write_text(
        "\n".join(
            json.dumps(
                {
                    "time": t,
                    "hosts": {"generator": {"stat": stat, "meminfo": "MemAvailable: 1048576 kB"}},
                }
            )
            for t, stat in [
                (100, "cpu 0 0 0 100 0 0 0 0\ncpu0 0 0 0 100 0 0 0 0"),
                (110, "cpu 100 0 0 200 0 0 0 0\ncpu0 100 0 0 200 0 0 0 0"),
            ]
        )
    )
    generator = summarize_generator_resources(path, {"steady": [100, 120]})
    assert generator["phases"]["steady"]["cpu_percent_of_generator_host_mean"] == 50
    rds = tmp_path / "rds.json"
    rds.write_text(
        json.dumps(
            {
                "CPUUtilization": [
                    {"Average": 25, "Maximum": 40, "Timestamp": 100},
                    {"Average": 50, "Maximum": 60, "Timestamp": 110},
                ]
            }
        )
    )
    summary = summarize_rds_resources(rds, 100, 120)
    assert summary["metrics"]["CPUUtilization"]["mean"] == 37.5
    assert "independent" in summary["budget"]
