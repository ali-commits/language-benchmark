from bench.report import plot_curves


def test_report_exports_latency_and_throughput_curves(tmp_path):
    report = {
        "trials": [
            {
                "campaign": "one",
                "cell": "python-baseline",
                "rate": 25,
                "successful_operations_s": 24,
                "metrics": {"p95": 50},
                "qualification": "pass",
            },
            {
                "campaign": "one",
                "cell": "python-baseline",
                "rate": 50,
                "successful_operations_s": 40,
                "metrics": {"p95": 250},
                "qualification": "fail",
            },
        ]
    }
    paths = plot_curves(report, tmp_path)
    assert len(paths) == 2
    assert all(p.exists() and p.stat().st_size > 1000 for p in paths)
