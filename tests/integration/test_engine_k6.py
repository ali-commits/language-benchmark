"""Disposable Docker smoke tests; explicitly opt in with BENCH_INTEGRATION=1."""

import json
import os
import shutil
import socket
import subprocess
import time
import uuid
from pathlib import Path
from urllib.request import urlopen

import pytest

from tools.bench.engine.config import load_config
from tools.bench.engine.trial import parse_events
from tools.bench.engine.types import Sample
from tools.bench.engine.workloads import prepare_workload

pytestmark = pytest.mark.skipif(
    os.getenv("BENCH_INTEGRATION") != "1", reason="opt-in Docker integration"
)
ROOT = Path(__file__).resolve().parents[2]


def run_load(tmp_path, stages, *, delay=0.04, grace=0.2):
    runner = ROOT / "benchmarks/k6/engine/runner.js"
    assert runner.exists()
    name = "bench-engine-test-" + uuid.uuid4().hex[:12]
    image = name + ":local"
    tmp_path = tmp_path / name
    tmp_path.mkdir()
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    subprocess.run(
        ["docker", "build", "-q", "-t", image, str(ROOT / "examples/http")],
        check=True,
        capture_output=True,
    )
    try:
        subprocess.run(
            [
                "docker",
                "run",
                "-d",
                "--name",
                name,
                "--network",
                "host",
                "-e",
                f"PORT={port}",
                image,
            ],
            check=True,
            capture_output=True,
        )
        for attempt in range(100):
            try:
                with urlopen(f"http://127.0.0.1:{port}/health", timeout=1) as response:
                    assert response.status == 200
                break
            except OSError:
                time.sleep(0.05)
        else:
            pytest.fail("HTTP target did not become ready")
        for filename in ("runner.js", "metrics.js"):
            shutil.copy(ROOT / "benchmarks/k6/engine" / filename, tmp_path / filename)
        workload = load_config(ROOT / "examples/http/queue.yaml").workloads["http"]
        prepared = prepare_workload(workload, tmp_path / "workload")
        runner_path = tmp_path / "runner.js"
        relative_entry = "./workload/" + prepared.script.relative_to(prepared.directory).as_posix()
        runner_path.write_text(
            runner_path.read_text().replace("'./workload.js'", json.dumps(relative_entry))
        )
        config = {
            "stages": stages,
            "baseUrl": f"http://127.0.0.1:{port}",
            "parameters": {"delay": delay},
            "gracefulRampDown": grace,
            "drain": grace,
            "telemetryInterval": 0.05,
        }
        result = subprocess.run(
            [
                "docker",
                "run",
                "--rm",
                "--name",
                name + "-load",
                "--user",
                str(os.getuid()),
                "--network",
                "host",
                "-v",
                f"{tmp_path}:/scripts:ro",
                "-e",
                "BENCH_CONFIG=" + json.dumps(config),
                "grafana/k6:1.4.2",
                "run",
                "--log-format",
                "raw",
                "--out",
                "json=/dev/stdout",
                "/scripts/runner.js",
            ],
            text=True,
            capture_output=True,
            timeout=45,
        )
        assert result.returncode == 0, result.stderr[-2500:]
        events = [
            json.loads(line.split("BENCH_EVENT ", 1)[1])
            for line in result.stderr.splitlines()
            if "BENCH_EVENT " in line
        ]
        parsed = parse_events(result.stdout + result.stderr)
        occupancy = [s for s in parsed.samples if s.metric == "active_users"]
        for sample in occupancy:
            stage = next(
                (
                    stage
                    for stage in stages
                    if stage["start_seconds"] <= sample.timestamp < stage["end_seconds"]
                ),
                None,
            )
            tagged = sample.model_copy(
                update={
                    "provider_fields": {
                        **sample.provider_fields,
                        "stage": stage["id"] if stage else "drain",
                    }
                }
            )
            events.append({"type": "sample", "sample": tagged.model_dump(mode="json")})
        assert events
        return events
    finally:
        subprocess.run(["docker", "rm", "-f", name + "-load", name], capture_output=True)
        subprocess.run(["docker", "image", "rm", image], capture_output=True)
        assert (
            subprocess.run(
                ["docker", "ps", "-aq", "--filter", "name=" + name], capture_output=True, text=True
            ).stdout.strip()
            == ""
        )


def stages_of(values):
    result = []
    start = 0.0
    for name, source, target, duration in values:
        result.append(
            {
                "id": name,
                "from_users": source,
                "to_users": target,
                "duration": duration,
                "start_seconds": start,
                "end_seconds": start + duration,
                "evaluate": source == target and target > 0,
            }
        )
        start += duration
    return result


def test_continuous_stage_telemetry_and_raw_samples(tmp_path):
    stages = stages_of(
        [
            ("ramp", 0, 4, 1.0),
            ("hold", 4, 4, 2.0),
            ("reduce", 4, 2, 1.0),
            ("recover", 2, 2, 2.0),
            ("cool", 2, 0, 1.0),
            ("idle", 0, 0, 2.0),
        ]
    )
    events = run_load(tmp_path, stages)
    samples = [Sample.model_validate(e["sample"]) for e in events if e["type"] == "sample"]
    occupancy = [s for s in samples if s.metric == "active_users"]
    assert {s.provider_fields["stage"] for s in occupancy} >= {"hold", "recover", "idle"}
    assert all(s.provider_fields["provider"] == "k6_native_vus" for s in occupancy)
    assert next(e for e in events if e["type"] == "metadata")["occupancy_excludes_monitor_vus"] == 0
    for name, users in [("hold", 4), ("recover", 2), ("idle", 0)]:
        selected = [
            s
            for s in occupancy
            if s.provider_fields["stage"] == name
            and s.timestamp > next(x["start_seconds"] for x in stages if x["id"] == name) + 0.3
        ]
        assert selected, (
            name,
            [(s.timestamp, s.value, s.provider_fields["stage"]) for s in occupancy],
        )
        assert all(s.value == users for s in selected)
    success = [s for s in samples if s.metric == "success"]
    assert success and all(s.denominator == 1 and s.numerator in (0, 1) for s in success)
    assert not any(s.provider_fields["start_stage"] == "idle" for s in success)
    assert not any(
        s.timestamp > 7.2 for s in success
    )  # Only real in-flight drain can cross into idle.
    assert len(success) == len([s for s in samples if s.metric == "latency"])
    starts = [e for e in events if e["type"] == "journey_start"]
    assert max(e["iteration"] for e in starts) > 1


def test_boundary_completion_and_interrupted_operations(tmp_path):
    events = run_load(
        tmp_path,
        stages_of([("hold", 2, 2, 0.4), ("reduce", 2, 0, 0.1), ("idle", 0, 0, 2.0)]),
        delay=0.65,
        grace=0.05,
    )
    starts = {e["operation_id"] for e in events if e["type"] == "operation_start"}
    ends = {e["operation_id"] for e in events if e["type"] == "operation_end"}
    assert starts - ends  # Forceful ramp-down cannot execute JavaScript finally blocks.
    assert not [e for e in events if e["type"] == "sample" and e["sample"]["metric"] == "success"]
    events = run_load(
        tmp_path,
        stages_of([("hold", 1, 1, 0.2), ("next", 1, 1, 0.6), ("cool", 1, 0, 0.1)]),
        delay=0.3,
        grace=0.4,
    )
    completed = [e for e in events if e["type"] == "operation_end"]
    assert any(e["start_stage"] == "hold" and e["completion_stage"] == "next" for e in completed)


def test_timeout_attempt_retained_in_latency_denominator(tmp_path):
    events = run_load(
        tmp_path,
        stages_of([("hold", 1, 1, 2.2), ("cool", 1, 0, 0.1), ("idle", 0, 0, 0.2)]),
        delay=3,
        grace=0.05,
    )
    samples = [Sample.model_validate(e["sample"]) for e in events if e["type"] == "sample"]
    success = [s for s in samples if s.metric == "success"]
    latency = [s for s in samples if s.metric == "latency"]
    assert len(success) == len(latency) == 1
    assert success[0].numerator == 0 and success[0].denominator == 1
    assert latency[0].includes_failures and latency[0].value >= 1900


def test_submillisecond_stage_is_rejected_instead_of_rounded(tmp_path):
    with pytest.raises(AssertionError, match="exact millisecond precision"):
        run_load(tmp_path, stages_of([("hold", 1, 1, 0.0001)]))
