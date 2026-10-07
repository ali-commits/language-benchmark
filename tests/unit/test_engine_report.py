import json

import pytest

from tools.bench.engine.report import write_queue_report


class Store:
    def __init__(self, directory):
        self.directory = directory

    def snapshot(self, queue):
        return {
            "queues": [{"state": "needs_attention", "revision": 3}],
            "jobs": [{"id": "<unsafe>", "state": "active"}],
            "trials": [
                {
                    "id": "trial",
                    "job": "<unsafe>",
                    "state": "completed",
                    "path": str(self.directory),
                }
            ],
        }

    def controller_state(self, queue):
        return {
            "jobs": {
                "<unsafe>": {
                    "results": [
                        {
                            "objective": "cpu",
                            "status": "incomplete",
                            "observed_only": True,
                            "passing_users": 2,
                        }
                    ],
                    "decisions": [],
                }
            }
        }


def test_report_missing_evidence_and_objectives(tmp_path):
    output = write_queue_report(Store(tmp_path / "absent"), "q", tmp_path / "report")
    data = json.loads((output.parent / "report.json").read_text())
    assert data["trials"][0]["status"] == "unavailable"
    assert data["objectives"][0]["observed_only"] is True
    assert "needs_attention" in output.read_text()
    assert "&lt;unsafe&gt;" in output.read_text()
    assert "<unsafe>" not in output.read_text()
    assert str(tmp_path) not in (output.parent / "report.json").read_text()


def test_report_metrics_and_curves(tmp_path):
    evidence = {
        "trial_id": "trial",
        "valid": True,
        "cleanup_verified": True,
        "stages": [
            {
                "id": "hold",
                "from_users": 2,
                "to_users": 2,
                "duration": 2.0,
                "evaluate": True,
                "start_seconds": 0.0,
                "end_seconds": 2.0,
            }
        ],
        "samples": [
            {"metric": "latency", "timestamp": t, "value": v, "unit": "ms"}
            for t, v in [(0.0, 10.0), (1.0, 30.0)]
        ]
        + [
            {
                "metric": "cpu_used",
                "scope": "container",
                "kind": "interval",
                "timestamp": 0.0,
                "end_seconds": 2.0,
                "value": 0.5,
                "unit": "fraction",
                "normalization": "container_quota",
            }
        ],
    }
    (tmp_path / "result.json").write_text(json.dumps(evidence))
    output = write_queue_report(Store(tmp_path), "q", tmp_path / "report")
    data = json.loads(output.with_suffix(".json").read_text())
    rows = data["trials"][0]["windows"][0]["metrics"]
    latency = next(r for r in rows if r["metric"] == "latency")
    assert latency["mean"] == 20.0
    assert latency["p50"] == 20.0
    assert latency["p99.9"] == pytest.approx(29.98)
    assert (
        next(r for r in rows if r["metric"] == "cpu_used" and r["scope"] == "container")["mean"]
        == 0.5
    )
    assert "<svg" in output.read_text()
    assert data["trials"][0]["stages"][0]["id"] == "hold"


def test_temporal_weighting_rates_and_scope_separation(tmp_path):
    evidence = {
        "valid": True,
        "cleanup_verified": True,
        "stages": [
            {
                "id": "hold",
                "from_users": 2,
                "to_users": 2,
                "duration": 4.0,
                "evaluate": True,
                "start_seconds": 0.0,
                "end_seconds": 4.0,
            }
        ],
        "samples": [
            {
                "metric": "cpu_used",
                "scope": "container",
                "kind": "interval",
                "timestamp": 0.0,
                "end_seconds": 1.0,
                "value": 0.2,
                "normalization": "container_quota",
            },
            {
                "metric": "cpu_used",
                "scope": "container",
                "kind": "interval",
                "timestamp": 1.0,
                "end_seconds": 4.0,
                "value": 0.6,
                "normalization": "container_quota",
            },
            {
                "metric": "memory_used",
                "scope": "database",
                "kind": "interval",
                "timestamp": 0.0,
                "end_seconds": 4.0,
                "value": 100.0,
                "unit": "bytes",
            },
            {
                "metric": "success",
                "kind": "rate",
                "timestamp": 1.0,
                "numerator": 3.0,
                "denominator": 4.0,
                "unit": "fraction",
            },
        ],
    }
    (tmp_path / "result.json").write_text(json.dumps(evidence))
    output = write_queue_report(Store(tmp_path), "q", tmp_path / "report")
    rows = json.loads(output.with_suffix(".json").read_text())["trials"][0]["windows"][0]["metrics"]
    cpu = next(
        r
        for r in rows
        if r["scope"] == "container" and r["metric"] == "cpu_used" and r["unit"] is None
    )
    assert cpu["mean"] == pytest.approx(0.5)
    assert cpu["max"] == 0.6
    assert (
        next(r for r in rows if r["scope"] == "backend_host" and r["metric"] == "cpu_used")["mean"]
        is None
    )
    success = next(r for r in rows if r["metric"] == "success")
    assert success["rate"] == 0.75
    assert success["attempted_rps"] == 1.0
    assert success["successful_rps"] == 0.75
    assert (
        next(r for r in rows if r["scope"] == "database" and r["metric"] == "memory_used")["mean"]
        == 100.0
    )


def test_invalid_evidence_remains_invalid_and_private_provider_fields_excluded(tmp_path):
    (tmp_path / "result.json").write_text(
        json.dumps(
            {
                "valid": False,
                "invalid_reasons": ["missing occupancy"],
                "samples": [],
                "manifest_path": "/private/environment",
                "samples_path": "/private/samples",
            }
        )
    )
    output = write_queue_report(Store(tmp_path), "q", tmp_path / "report")
    data = json.loads(output.with_suffix(".json").read_text())
    trial = data["trials"][0]
    assert trial["status"] == "invalid"
    assert trial["invalid_reasons"] == ["missing occupancy"]
    artifact = (output.parent / trial["evidence"]).read_text()
    assert "/private" not in artifact
    assert "Sanitized raw evidence" in output.read_text()


def test_unstable_objective_and_observed_only_are_preserved(tmp_path):
    store = Store(tmp_path)
    store.controller_state = lambda queue: {
        "jobs": {
            "j": {
                "results": [
                    {
                        "objective": "latency",
                        "status": "unstable",
                        "contradictions": [[2, 3]],
                        "passing_users": 3,
                        "failing_users": 2,
                        "observed_only": True,
                        "attempts": 4,
                    }
                ],
                "decisions": [
                    {
                        "trial_id": "trial",
                        "objective": "latency",
                        "reused": True,
                        "source_objective": "cpu",
                        "evaluation": {"verdict": "fail"},
                    }
                ],
            }
        }
    }
    output = write_queue_report(store, "q", tmp_path / "report")
    data = json.loads(output.with_suffix(".json").read_text())
    assert data["objectives"][0]["status"] == "unstable"
    assert data["objectives"][0]["contradictions"] == [[2, 3]]
    assert data["decisions"][0]["source_objective"] == "cpu"
    assert data["decisions"][0]["evaluation"]["verdict"] == "fail"


@pytest.mark.parametrize("observed", [False, True])
def test_missing_declared_and_required_workload_metrics_visible(tmp_path, observed):
    store = Store(tmp_path)
    original = store.snapshot

    def snapshot(queue):
        value = original(queue)
        value["jobs"][0]["resolved"] = json.dumps(
            [
                {
                    "workload": {
                        "metrics": {
                            "latency": {"name": "latency", "type": "trend", "unit": "ms"},
                            "success": {"name": "success", "type": "rate", "unit": "fraction"},
                            "authentication": {
                                "name": "authentication",
                                "type": "rate",
                                "unit": "fraction",
                            },
                        }
                    },
                    "criteria": [
                        {
                            "metric": "required_custom",
                            "scope": "workload",
                            "statistic": "p99",
                            "unit": "ms",
                        }
                    ],
                    "objectives": {
                        "quality": [
                            {
                                "metric": "objective_custom",
                                "scope": "workload",
                                "statistic": "rate",
                                "unit": "fraction",
                            }
                        ]
                    },
                }
            ]
        )
        return value

    store.snapshot = snapshot
    (tmp_path / "result.json").write_text(
        json.dumps(
            {
                "stages": [
                    {
                        "id": "hold",
                        "from_users": 2,
                        "to_users": 2,
                        "duration": 2.0,
                        "evaluate": True,
                        "start_seconds": 0.0,
                        "end_seconds": 2.0,
                    }
                ],
                "samples": [{"metric": "latency", "timestamp": 1.0, "value": 5.0, "unit": "ms"}]
                if observed
                else [],
            }
        )
    )
    output = write_queue_report(store, "q", tmp_path / "report")
    data = json.loads(output.with_suffix(".json").read_text())
    rows = {
        r["metric"]: r
        for r in data["trials"][0]["windows"][0]["metrics"]
        if r["scope"] == "workload"
    }
    assert rows["latency"]["status"] == ("available" if observed else "unavailable")
    for metric in ["success", "authentication", "required_custom", "objective_custom"]:
        assert rows[metric]["status"] == "unavailable"
        assert metric in output.read_text()
    assert rows["success"]["rate"] is None
    assert rows["success"]["attempted_rps"] is None
    assert rows["success"]["successful_rps"] is None
    assert rows["authentication"]["rate"] is None
    assert rows["required_custom"]["p99"] is None
    assert rows["objective_custom"]["rate"] is None


def test_missing_artifact_still_shows_declared_metric_unavailable(tmp_path):
    store = Store(tmp_path / "missing")
    original = store.snapshot

    def snapshot(queue):
        value = original(queue)
        value["jobs"][0]["resolved"] = json.dumps(
            [
                {
                    "workload": {
                        "metrics": {
                            "success": {"name": "success", "type": "rate", "unit": "fraction"}
                        }
                    }
                }
            ]
        )
        return value

    store.snapshot = snapshot
    output = write_queue_report(store, "q", tmp_path / "report")
    trial = json.loads(output.with_suffix(".json").read_text())["trials"][0]
    assert trial["status"] == "unavailable"
    row = next(row for row in trial["windows"][0]["metrics"] if row["metric"] == "success")
    assert row["status"] == "unavailable"
    assert row["rate"] is None
    assert row["attempted_rps"] is None
    assert "success" in output.read_text()


@pytest.mark.parametrize("unit_fields", [{}, {"unit": None}])
def test_queued_nullable_metric_and_criterion_units_generate_report(tmp_path, unit_fields):
    from pathlib import Path

    from tools.bench.engine.config import load_config
    from tools.bench.engine.types import Criterion, Metric, Target
    from tools.bench.queue.store import QueueStore

    config = load_config(Path("examples/http/queue.yaml"))
    metric = Metric(name="requests", type="counter", **unit_fields)
    criterion = Criterion(metric="requests", statistic="sum", operator=">=", value=0.0)
    config = config.model_copy(
        update={
            "targets": {"http": Target(image="example:1")},
            "workloads": {
                "http": config.workloads["http"].model_copy(
                    update={"metrics": {"requests": metric}}
                )
            },
            "jobs": [config.jobs[0].model_copy(update={"criteria": [criterion]})],
        }
    )
    store = QueueStore(tmp_path / "queue.db")
    store.register(config)
    owner = "nullable-unit-test"
    claimed = store.claim_next(config.name, owner)
    assert claimed is not None
    store.record_trial(config.name, claimed.job_id, owner, "trial", tmp_path / "absent")
    resolved = json.loads(store.snapshot(config.name)["jobs"][0]["resolved"])[0]
    assert resolved["workload"]["metrics"]["requests"]["unit"] is None
    assert resolved["criteria"][0]["unit"] is None

    output = write_queue_report(store, config.name, tmp_path / "report")
    trial = json.loads(output.with_suffix(".json").read_text())["trials"][0]
    row = next(row for row in trial["windows"][0]["metrics"] if row["metric"] == "requests")
    assert row["unit"] is None
    assert row["status"] == "unavailable"
    assert row["sum"] is None
    assert "requests" in output.read_text()
