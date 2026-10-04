from bench.report import qualify


def test_capacity_requires_all_targets_and_zero_drops():
    base = {"attempted": 1000, "succeeded": 1000, "dropped": 0, "p95": 100, "p99": 400}
    assert qualify(base, []) == "pass"
    assert qualify({**base, "dropped": 1}, []) == "fail"
    assert qualify({**base, "p99": 600}, []) == "fail"
    assert qualify({**base, "succeeded": 998}, []) == "fail"


def test_missing_metrics_cannot_pass():
    assert qualify({}, []) == "unknown"
    assert (
        qualify(
            {"attempted": 10, "succeeded": 10, "dropped": 0, "p95": 1, "p99": 1},
            ["missing telemetry"],
        )
        == "unknown"
    )


def test_standalone_login_uses_500_ms_p95_target():
    metrics = {"attempted": 1000, "succeeded": 1000, "dropped": 0, "p95": 300, "p99": 700}
    assert qualify(metrics, [], workload="login") == "pass"
    assert qualify(metrics, [], workload="mixed") == "fail"
    assert qualify({**metrics, "p95": 501}, [], workload="login") == "fail"
