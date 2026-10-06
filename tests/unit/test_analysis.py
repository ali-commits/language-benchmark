import json

import pytest
from bench.analysis import analyze_observations, load_observations, validate_threshold


def observation(users, success, auth=1.0, valid=True, **kwargs):
    return {
        "campaign": "c",
        "cell": "go",
        "users": users,
        "valid": valid,
        "reasons": [],
        "metrics": {
            "attempted": 1000,
            "succeeded": success * 1000,
            "active_users_max": users,
            "authenticated_users": users * auth,
        },
        "methodology": {"profile": "fast", "authentication_fraction": 0.90, "hold_seconds": 240},
        **kwargs,
    }


@pytest.mark.parametrize("value", [float("nan"), float("inf"), 0.899, 1.001, True])
def test_threshold_must_be_finite_90_to_100(value):
    with pytest.raises(ValueError):
        validate_threshold(value)


def test_observed_bounds_reclassify_valid_policy_failures_without_exact_capacity():
    rows = [
        observation(100, 0.999),
        observation(200, 0.97),
        observation(300, 0.92),
        observation(400, 0.85),
        observation(999, 0.999, valid=False),
    ]
    report = analyze_observations(rows, [0.90, 0.95, 0.99, 0.999])
    assert [r["highest_observed_passing_users"] for r in report["bounds"]] == [300, 200, 100, 100]
    assert report["bounds"][0]["lowest_observed_failing_users"] == 400
    assert "not exact capacity" in report["method"]
    assert report["bounds"][0]["unsampled_ranges"] == [[101, 199], [201, 299], [301, 399]]
    assert report["invalid_observations"] == 1


def test_auth_gate_separate_and_contradictory_repeats_preserved():
    rows = [observation(100, 0.98, auth=0.95), observation(200, 0.99), observation(200, 0.92)]
    bound = analyze_observations(rows, [0.95])["bounds"][0]
    assert bound["highest_observed_passing_users"] == 200
    assert bound["lowest_observed_failing_users"] == 200
    assert bound["contradictory"] is True
    assert bound["mixed_users"] == [200]
    assert (
        analyze_observations([rows[0]], [0.98])["bounds"][0]["highest_observed_passing_users"]
        == 100
    )
    assert (
        analyze_observations([rows[0]], [0.98], authentication_threshold=0.99)["bounds"][0][
            "highest_observed_passing_users"
        ]
        is None
    )


def test_compact_old_results_remain_usable_without_fabricating_new_metrics(tmp_path):
    path = tmp_path / "capacity-results.json"
    path.write_text(
        json.dumps(
            {
                "rust": {
                    "observations": [
                        {
                            "users": 100,
                            "outcome": "fail",
                            "reasons": [],
                            "metrics": {
                                "attempted": 100,
                                "succeeded": 95,
                                "authenticated_users": 100,
                                "active_users_max": 100,
                            },
                            "trial_id": "old",
                        }
                    ]
                }
            }
        )
    )
    rows = load_observations(path)
    assert rows[0]["valid"] is True
    assert "not stored" in rows[0]["methodology"]["description"]
    assert rows[0]["metrics"].get("mean") is None
    assert analyze_observations(rows, [0.90])["bounds"][0]["highest_observed_passing_users"] == 100


def test_historical_open_file_logs_invalidate_recorded_backend_failure(tmp_path):
    path = tmp_path / "capacity-results.json"
    trial = tmp_path / "old"
    trial.mkdir()
    (trial / "backend.log").write_text("error: Too many open files")
    path.write_text(
        json.dumps(
            {
                "rust": {
                    "observations": [
                        {
                            "users": 100,
                            "outcome": "fail",
                            "reasons": [],
                            "metrics": {
                                "attempted": 100,
                                "succeeded": 0,
                                "authenticated_users": 100,
                                "active_users_max": 100,
                            },
                            "trial_id": "old",
                        }
                    ]
                }
            }
        )
    )
    rows = load_observations(path)
    assert rows[0]["valid"] is False
    assert any("open-file" in r for r in rows[0]["reasons"])


def test_analysis_cli_help_and_export(tmp_path):
    import os
    import subprocess
    import sys

    env = {**os.environ, "PYTHONPATH": "tools"}
    help_result = subprocess.run(
        [sys.executable, "-m", "bench.cli", "analyze", "--help"],
        env=env,
        text=True,
        capture_output=True,
    )
    assert help_result.returncode == 0, help_result.stderr
    source = tmp_path / "capacity-results.json"
    output = tmp_path / "analysis.json"
    source.write_text(
        json.dumps(
            {
                "go": {
                    "observations": [
                        {
                            "users": 100,
                            "outcome": "fail",
                            "reasons": [],
                            "metrics": {
                                "attempted": 100,
                                "succeeded": 95,
                                "authenticated_users": 100,
                                "active_users_max": 100,
                            },
                            "trial_id": "old",
                        }
                    ]
                }
            }
        )
    )
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "bench.cli",
            "analyze",
            "--input",
            str(source),
            "--output",
            str(output),
            "--threshold",
            ".93",
        ],
        env=env,
        text=True,
        capture_output=True,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(output.read_text())["bounds"][0]["highest_observed_passing_users"] == 100
    assert output.with_suffix(".txt").exists()
    bad = subprocess.run(
        [
            sys.executable,
            "-m",
            "bench.cli",
            "analyze",
            "--input",
            str(source),
            "--output",
            str(output),
            "--threshold",
            "nan",
        ],
        env=env,
        text=True,
        capture_output=True,
    )
    assert bad.returncode == 2 and "Traceback" not in bad.stderr


@pytest.mark.parametrize(
    "message",
    [
        "Too many open files",
        "os error 24",
        "[Errno 24] failed",
        "errno:24",
        "server startup failed EMFILE",
    ],
)
def test_all_descriptor_exhaustion_variants_exclude_compact_bounds(tmp_path, message):
    source = tmp_path / "capacity-results.json"
    trial = tmp_path / "old"
    trial.mkdir()
    (trial / "backend.log").write_text(message)
    source.write_text(
        json.dumps(
            {
                "rust": {
                    "observations": [
                        {
                            "users": 100,
                            "outcome": "fail",
                            "reasons": [],
                            "metrics": {
                                "attempted": 100,
                                "succeeded": 0,
                                "authenticated_users": 100,
                                "active_users_max": 100,
                            },
                            "trial_id": "old",
                        }
                    ]
                }
            }
        )
    )
    report = analyze_observations(load_observations(source), [0.9])
    assert report["invalid_observations"] == 1
    assert report["bounds"][0]["lowest_observed_failing_users"] is None


def test_full_raw_artifact_emfile_invalidates_original_report_and_threshold_analysis(tmp_path):
    import gzip

    from bench.report import read_trial_summary

    trial = tmp_path / "trial"
    (trial / "k6").mkdir(parents=True)
    identity = {"id": "trial", "campaign_id": "c", "cell": "rust", "users": 100}
    (trial / "manifest.json").write_text(
        json.dumps(
            {
                "trial": identity,
                "label": "AWS",
                "campaign": {
                    "operation": "user-journey",
                    "mode": "local",
                    "ramp_seconds": 60,
                    "settle_seconds": 0,
                    "hold_seconds": 20,
                    "success_fraction": 0.9,
                    "authentication_fraction": 0.9,
                },
            }
        )
    )
    (trial / "result.json").write_text(
        json.dumps(
            {
                "trial": identity,
                "status": "measured",
                "reset_verified": True,
                "reasons": [],
                "execution_started_epoch": 100,
                "execution_finished_epoch": 120,
            }
        )
    )
    points = [
        {
            "type": "Point",
            "metric": name,
            "data": {"value": value, "time": "1970-01-01T00:01:50Z", "tags": {"phase": "steady"}},
        }
        for name, value in [
            ("operations_attempted", 100),
            ("operations_succeeded", 100),
            ("journey_active_users", 100),
            ("users_authenticated", 100),
            ("operation_duration", 10),
        ]
    ]
    with gzip.open(trial / "k6/metrics.json.gz", "wt") as f:
        f.write("\n".join(json.dumps(p) for p in points))
    (trial / "telemetry.jsonl").write_text(
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
    (trial / "backend.log").write_text("server startup failed EMFILE")
    summary = read_trial_summary(trial / "manifest.json")
    assert summary["qualification"] == "unknown"
    assert any("open-file" in r for r in summary["reasons"])
    bounds = analyze_observations(load_observations(tmp_path), [0.9])
    assert bounds["bounds"][0]["highest_observed_passing_users"] is None
