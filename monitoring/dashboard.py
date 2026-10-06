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
    for index, (title, expr) in enumerate(
        [
            ("Concurrent active users", 'k6_journey_active_users{cell=~"$cell",trial=~"$trial"}'),
            (
                "Authenticated unique users",
                'sum by (cell,trial) (k6_users_authenticated_total{cell=~"$cell",trial=~"$trial"})',
            ),
            (
                "User response timeouts",
                'sum by (cell,trial) (k6_journey_timeouts_total{cell=~"$cell",trial=~"$trial"})',
            ),
            (
                "Historical concurrent-user capacity probes",
                'benchmark_target_users{cell=~"$cell",trial=~"$trial"}',
            ),
        ]
    ):
        panels.append(
            {
                "id": 30 + index,
                "title": title,
                "type": "timeseries",
                "gridPos": {"x": (index % 2) * 12, "y": 40 + (index // 2) * 8, "w": 12, "h": 8},
                "datasource": {"type": "prometheus", "uid": "benchmark-prometheus"},
                "targets": [{"refId": "A", "expr": expr, "legendFormat": "{{cell}} {{trial}}"}],
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
    variables.append(
        {
            "name": "threshold",
            "type": "custom",
            "query": "0.90,0.95,0.99,0.999",
            "current": {"text": "0.90", "value": "0.90"},
            "includeAll": False,
            "multi": False,
            "allowCustomValue": True,
            "label": "Operation success threshold",
        }
    )
    historical = 'cell=~"$cell",trial=~"$trial"'
    # Pass is a selectable policy; technical validity and the saved auth gate remain independent.
    eligible = f"benchmark_target_users{{{historical}}} and on (cell,trial,campaign,profile,experiment_type,methodology,authentication_policy,target_users,offered_rate,workload) (benchmark_technical_valid{{{historical}}} == 1) and on (cell,trial,campaign,profile,experiment_type,methodology,authentication_policy,target_users,offered_rate,workload) (benchmark_authentication_gate_satisfied{{{historical}}} == 1)"
    extra = [
        (
            "Historical highest observed passing users (not exact capacity)",
            f"max by (campaign,cell,profile,methodology) (({eligible}) and on (cell,trial,campaign,profile,experiment_type,methodology,authentication_policy,target_users,offered_rate,workload) (benchmark_operation_success_fraction{{{historical}}} >= $threshold))",
            "short",
        ),
        (
            "Historical lowest observed failing users (check contradictions in CLI)",
            f"min by (campaign,cell,profile,methodology) (benchmark_target_users{{{historical}}} and on (cell,trial,campaign,profile,experiment_type,methodology,authentication_policy,target_users,offered_rate,workload) (benchmark_technical_valid{{{historical}}} == 1) and on (cell,trial,campaign,profile,experiment_type,methodology,authentication_policy,target_users,offered_rate,workload) ((benchmark_operation_success_fraction{{{historical}}} < $threshold) or (benchmark_authentication_gate_satisfied{{{historical}}} == 0)))",
            "short",
        ),
        (
            "Historical mean latency (failed replies included)",
            f"benchmark_latency_mean_ms{{{historical}}}",
            "ms",
        ),
        (
            "Historical p99.9 latency (raw trial samples)",
            f"benchmark_latency_p999_ms{{{historical}}}",
            "ms",
        ),
        (
            "Backend container mean CPU: 100% = 2 vCPUs",
            f"benchmark_backend_cpu_percent_mean{{{historical}}}",
            "percent",
        ),
        (
            "Backend sampled working set (MiB)",
            f"benchmark_backend_memory_working_set_mib_peak{{{historical}}}",
            "mebibytes",
        ),
        (
            "Live operation mean / p99.9 latency (seconds)",
            'k6_operation_duration_avg{cell=~"$cell",trial=~"$trial"}',
            "s",
        ),
        (
            "Operation success fraction and chosen threshold",
            f"benchmark_operation_success_fraction{{{historical}}}",
            "percentunit",
        ),
    ]
    for index, (title, expr, unit) in enumerate(extra):
        targets = [
            {
                "refId": "A",
                "expr": expr,
                "legendFormat": "{{cell}} {{campaign}} {{profile}} {{operation}}",
            }
        ]
        if index == 6:
            targets.append(
                {
                    "refId": "B",
                    "expr": expr.replace("_avg", "_p999"),
                    "legendFormat": "p99.9 {{operation}}",
                }
            )
        if index == 7:
            targets.append({"refId": "B", "expr": "$threshold", "legendFormat": "chosen target"})
        panels.append(
            {
                "id": 50 + index,
                "title": title,
                "type": "timeseries",
                "gridPos": {"x": (index % 2) * 12, "y": 56 + (index // 2) * 8, "w": 12, "h": 8},
                "datasource": {"type": "prometheus", "uid": "benchmark-prometheus"},
                "targets": targets,
                "fieldConfig": {"defaults": {"unit": unit}, "overrides": []},
            }
        )
    return {
        "uid": "language-benchmark",
        "title": "Language Benchmark — live and history",
        "schemaVersion": 39,
        "version": 2,
        "refresh": "5s",
        "timezone": "browser",
        "time": {"from": "now-1h", "to": "now"},
        "templating": {"list": variables},
        "panels": panels,
        "tags": ["benchmark", "languages"],
        "description": "Compare equal workloads/rates/fixtures. Never average percentiles across trials. Saved raw artifacts define final qualification. Threshold comparisons show observed bounds only; invalid telemetry remains excluded. Authentication uses its saved separate gate. Docker CPU100% means both allocatedCPUs; memory is sampled working set.",
    }


if __name__ == "__main__":
    Path("monitoring/grafana/dashboards/benchmark.json").write_text(
        json.dumps(build_dashboard(), indent=2)
    )
