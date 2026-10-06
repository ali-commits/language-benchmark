import json
from pathlib import Path

from bench import journeys, report

from monitoring import history


def artifact(root: Path, name="completed", status="measured", metrics=True):
    folder = root / name
    (folder / "k6").mkdir(parents=True)
    trial = {"id": name, "campaign_id": "campaign", "cell": "go-net-http", "users": 100}
    config = {
        "operation": "user-journey",
        "mode": "local",
        "ramp_seconds": 180,
        "settle_seconds": 0,
        "hold_seconds": 240,
        "success_fraction": 0.9,
        "authentication_fraction": 0.9,
        "backend_nofile_limit": 250000,
    }
    (folder / "manifest.json").write_text(
        json.dumps({"campaign": config, "trial": trial, "label": "local"})
    )
    result = {
        "trial": trial,
        "status": status,
        "reset_verified": True,
        "reasons": [],
        "execution_started_epoch": 100,
        "execution_finished_epoch": 120,
    }
    if metrics:
        result["journey_metrics"] = {
            "attempted": 100,
            "succeeded": 100,
            "authenticated_users": 100,
            "active_users_max": 100,
            "p50": 10,
            "p95": 20,
            "p99": 30,
            "incomplete": False,
        }
    (folder / "result.json").write_text(json.dumps(result))
    with (folder / "k6/metrics.json.gz").open("wb") as f:
        f.truncate(1024**3)
    (folder / "telemetry.jsonl").write_text(
        "\n".join(
            json.dumps(
                {
                    "time": t,
                    "hosts": {
                        "generator": {
                            "stat": f"cpu 0 0 0 {idle} 0 0 0 0",
                            "meminfo": "SwapTotal: 0 kB\nSwapFree: 0 kB",
                        }
                    },
                }
            )
            for t, idle in [(100, 100), (120, 200)]
        )
    )
    return folder / "manifest.json"


def forbid_raw(monkeypatch):
    def fail(*args, **kwargs):
        raise AssertionError("history must never read large/growing raw gzip")

    monkeypatch.setattr(report, "metric_lines", fail)
    monkeypatch.setattr(journeys, "metric_lines", fail)


def test_saved_history_avoids_large_raw_and_preserves_missing_legacy_fields(tmp_path, monkeypatch):
    path = artifact(tmp_path)
    forbid_raw(monkeypatch)
    result = report.read_trial_summary(path, prefer_saved_metrics=True)
    assert result["qualification"] == "pass"
    assert result["successful_operations_s"] == 100 / 240
    assert result["metrics_source"] == "finalized saved reduction"
    assert result["metrics"].get("mean") is None and result["metrics"].get("p999") is None


def test_saved_history_still_invalidates_cleanup_and_backend_exhaustion(tmp_path, monkeypatch):
    path = artifact(tmp_path)
    forbid_raw(monkeypatch)
    (path.parent / "backend.log").write_text("server startup failed EMFILE")
    result = report.read_trial_summary(path, prefer_saved_metrics=True)
    assert result["qualification"] == "unknown"
    assert any("open-file" in value for value in result["reasons"])
    (path.parent / "backend.log").unlink()
    data = json.loads(path.with_name("result.json").read_text())
    data["reset_verified"] = False
    path.with_name("result.json").write_text(json.dumps(data))
    result = report.read_trial_summary(path, prefer_saved_metrics=True)
    assert result["qualification"] == "unknown"
    assert "final fixture restoration not verified" in result["reasons"]


def test_missing_final_saved_metrics_is_unknown_not_zero_throughput(tmp_path, monkeypatch):
    path = artifact(tmp_path, metrics=False)
    forbid_raw(monkeypatch)
    result = report.read_trial_summary(path, prefer_saved_metrics=True)
    assert result["qualification"] == "unknown"
    assert result["metrics"] == {}
    assert result["successful_operations_s"] is None


def test_exporter_skips_in_progress_and_uses_saved_metrics_only(tmp_path, monkeypatch):
    artifact(tmp_path)
    artifact(tmp_path, "running", status="failed", metrics=False)
    forbid_raw(monkeypatch)
    history.CACHE.clear()
    body = history.render_metrics(tmp_path)
    assert 'trial="completed"' in body
    assert 'trial="running"' not in body
    assert "benchmark_latency_mean_ms" not in body


def test_saved_history_preserves_generator_and_rds_validity(tmp_path, monkeypatch):
    path = artifact(tmp_path)
    forbid_raw(monkeypatch)
    (path.parent / "telemetry.jsonl").write_text(
        "\n".join(
            json.dumps(
                {
                    "time": t,
                    "hosts": {
                        "generator": {
                            "stat": f"cpu {busy} 0 0 100 0 0 0 0",
                            "meminfo": "SwapTotal: 0 kB\nSwapFree: 0 kB",
                        }
                    },
                }
            )
            for t, busy in [(100, 0), (120, 100)]
        )
    )
    result = report.read_trial_summary(path, prefer_saved_metrics=True)
    assert result["qualification"] == "unknown"
    assert any("generator CPU" in value for value in result["reasons"])
    manifest = json.loads(path.read_text())
    manifest["campaign"]["mode"] = "aws"
    path.write_text(json.dumps(manifest))
    (path.parent / "rds.json").write_text("{}")
    result = report.read_trial_summary(path, prefer_saved_metrics=True)
    assert result["qualification"] == "unknown"
    assert any(value.startswith("RDS ") for value in result["reasons"])


def test_history_cache_rechecks_backend_log_changes(tmp_path, monkeypatch):
    path = artifact(tmp_path)
    forbid_raw(monkeypatch)
    history.CACHE.clear()
    first = history.render_metrics(tmp_path)
    assert "benchmark_technical_valid" in first
    assert history.CACHE[str(path)][1]["qualification"] == "pass"
    (path.parent / "backend.log").write_text("server startup failed EMFILE")
    history.render_metrics(tmp_path)
    assert history.CACHE[str(path)][1]["qualification"] == "unknown"
