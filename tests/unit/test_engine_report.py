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
