import gzip
import json

from bench.report import read_metrics


def test_errors_reduce_successful_throughput_and_keep_raw_percentiles(tmp_path):
    p = tmp_path / "metrics.gz"
    points = []
    for name, values in {
        "operations_attempted": [1, 1, 1],
        "operations_succeeded": [1, 1],
        "operation_duration": [10, 20, 100],
        "dropped_iterations": [1],
    }.items():
        points += [
            {"type": "Point", "metric": name, "data": {"value": v, "tags": {"operation": "feed"}}}
            for v in values
        ]
    with gzip.open(p, "wt") as f:
        f.write("\n".join(json.dumps(row) for row in points))
    r = read_metrics(p)
    assert r["attempted"] == 3 and r["succeeded"] == 2 and r["dropped"] == 1
    assert r["p50"] == 20 and r["p95"] == 92
