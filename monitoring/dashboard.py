"""Provision a small reproducible Grafana dashboard; benchmark artifacts remain authoritative."""

import json
from pathlib import Path


def build_dashboard():
    panels = []
    specs = [
        (
            "Live successful operations/s",
            'sum by (cell,operation) (rate(k6_operations_succeeded_total{cell=~"$cell",trial=~"$trial"}[30s]))',
            "ops",
        ),
        (
            "Latency p50 / p95 / p99 (seconds)",
            'k6_operation_duration_p95{cell=~"$cell",trial=~"$trial"}',
            "s",
        ),
        (
            "Failed operations",
            'sum by (cell) (k6_operations_failed_total{cell=~"$cell",trial=~"$trial"})',
            "short",
        ),
        (
            "Dropped arrivals",
            'sum by (cell) (k6_dropped_iterations_total{cell=~"$cell",trial=~"$trial"})',
            "short",
        ),
        (
            "Host CPU utilization",
            '100 * (1 - avg by (job) (rate(node_cpu_seconds_total{mode="idle"}[1m])))',
            "percent",
        ),
        ("Host available memory", "node_memory_MemAvailable_bytes", "bytes"),
        (
            "Historical successful throughput by offered rate",
            'benchmark_successful_ops_per_second{cell=~"$cell",trial=~"$trial"}',
            "ops",
        ),
        (
            "Historical p95 latency and validity",
            'benchmark_latency_p95_ms{cell=~"$cell",trial=~"$trial"}',
            "ms",
        ),
    ]
    for i, (title, expr, unit) in enumerate(specs):
        targets = [{"refId": "A", "expr": expr, "legendFormat": "{{cell}} {{operation}} {{job}}"}]
        if i == 1:
            targets += [
                {
                    "refId": "B",
                    "expr": expr.replace("_p95", "_p50"),
                    "legendFormat": "p50 {{operation}}",
                },
                {
                    "refId": "C",
                    "expr": expr.replace("_p95", "_p99"),
                    "legendFormat": "p99 {{operation}}",
                },
            ]
        if i == 7:
            targets.append(
                {
                    "refId": "B",
                    "expr": 'benchmark_trial_qualified{cell=~"$cell",trial=~"$trial"}',
                    "legendFormat": "validity: 1 pass, 0 fail, -1 unknown",
                }
            )
        panels.append(
            {
                "id": i + 1,
                "title": title,
                "type": "timeseries",
                "gridPos": {"x": (i % 2) * 12, "y": (i // 2) * 8, "w": 12, "h": 8},
                "datasource": {"type": "prometheus", "uid": "benchmark-prometheus"},
                "targets": targets,
                "fieldConfig": {"defaults": {"unit": unit}, "overrides": []},
            }
        )
    for index, (title, metric, unit) in enumerate(
        [
            ("RDS CPU", "CPUUtilization", "percent"),
            ("RDS connections", "DatabaseConnections", "short"),
        ]
    ):
        panels.append(
            {
                "id": 20 + index,
                "title": title,
                "type": "timeseries",
                "gridPos": {"x": index * 12, "y": 32, "w": 12, "h": 8},
                "datasource": {"type": "cloudwatch", "uid": "benchmark-cloudwatch"},
                "targets": [
                    {
                        "refId": "A",
                        "queryMode": "Metrics",
                        "namespace": "AWS/RDS",
                        "metricName": metric,
                        "statistic": "Average",
                        "dimensions": {"DBInstanceIdentifier": ["language-benchmark"]},
                        "region": "ap-southeast-1",
                        "period": "60",
                        "matchExact": True,
                        "metricQueryType": 0,
                        "metricEditorMode": 0,
                    }
                ],
                "fieldConfig": {"defaults": {"unit": unit}, "overrides": []},
            }
        )
    panels[6]["type"] = "table"
    panels[6]["targets"][0].update(instant=True, format="table")
    variables = [
        {
            "name": name,
            "type": "query",
            "datasource": {"type": "prometheus", "uid": "benchmark-prometheus"},
            "query": f"label_values(k6_operations_attempted_total, {name})",
            "includeAll": True,
            "allValue": ".*",
            "multi": True,
            "refresh": 1,
        }
        for name in ("cell", "trial")
    ]
    return {
        "uid": "language-benchmark",
        "title": "Language Benchmark — live and history",
        "schemaVersion": 39,
        "version": 1,
        "refresh": "5s",
        "timezone": "browser",
        "time": {"from": "now-1h", "to": "now"},
        "templating": {"list": variables},
        "panels": panels,
        "tags": ["benchmark", "python"],
        "description": "Compare equal workloads/rates/fixtures. Never average percentiles across trials. Saved raw artifacts define final qualification.",
    }


if __name__ == "__main__":
    Path("monitoring/grafana/dashboards/benchmark.json").write_text(
        json.dumps(build_dashboard(), indent=2)
    )
