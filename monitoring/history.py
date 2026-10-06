"""Expose completed immutable artifact summaries for Grafana, without touching the tested DB."""

import json
import os
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

from bench.report import read_trial_summary

CACHE = {}


def metric_values(row):
    """Policy failures remain technically valid and can pass a lower analysis target."""
    metrics = row["metrics"]
    values = {
        "benchmark_trial_qualified": {"pass": 1, "fail": 0, "unknown": -1}[row["qualification"]],
        "benchmark_technical_valid": int(row["qualification"] in ("pass", "fail")),
    }
    for name, field in (
        ("benchmark_successful_ops_per_second", "successful_operations_s"),
        ("benchmark_attempted_ops_per_second", "achieved_operations_s"),
        ("benchmark_http_requests_per_second", "achieved_http_requests_s"),
    ):
        if row.get(field) is not None:
            values[name] = row[field]
    for name, field in (
        ("benchmark_latency_mean_ms", "mean"),
        ("benchmark_latency_p50_ms", "p50"),
        ("benchmark_latency_p95_ms", "p95"),
        ("benchmark_latency_p99_ms", "p99"),
        ("benchmark_latency_p999_ms", "p999"),
    ):
        if metrics.get(field) is not None:
            values[name] = metrics[field]
    if metrics.get("attempted"):
        values["benchmark_operation_success_fraction"] = metrics["succeeded"] / metrics["attempted"]
    if row.get("target_users"):
        values.update(
            benchmark_target_users=row["target_users"],
            benchmark_authenticated_users=metrics.get("authenticated_users", 0),
            benchmark_active_users=metrics.get("active_users_max", 0),
            benchmark_user_timeouts=metrics.get("timeouts", 0),
        )
        fraction = row.get(
            "authentication_fraction", metrics.get("authenticated_users", 0) / row["target_users"]
        )
        gate = row.get("authentication_gate", 0.99)
        values.update(
            benchmark_authentication_fraction=fraction,
            benchmark_authentication_gate=gate,
            benchmark_authentication_gate_satisfied=int(fraction >= gate),
        )
    resource = row.get("resources", {}).get("phases", {}).get("steady", {})
    for field in (
        "cpu_percent_mean",
        "cpu_percent_peak",
        "memory_working_set_mib_mean",
        "memory_working_set_mib_peak",
    ):
        if field in resource:
            values["benchmark_backend_" + field] = resource[field]
    return values


def render_metrics(root: Path) -> str:
    lines = []
    for manifest in root.rglob("manifest.json"):
        result = manifest.with_name("result.json")
        raw = manifest.parent / "k6/metrics.json.gz"
        if not result.exists():
            continue
        try:
            finalized = json.loads(result.read_text())
            if finalized.get("status") != "measured":
                continue
            signature = tuple(
                (p.stat().st_mtime if p.exists() else 0)
                for p in [
                    manifest,
                    result,
                    raw,
                    manifest.with_name("rds.json"),
                    manifest.with_name("telemetry.jsonl"),
                    manifest.with_name("backend.log"),
                ]
            )
            key = str(manifest)
            if key not in CACHE or CACHE[key][0] != signature:
                CACHE[key] = (signature, read_trial_summary(manifest, prefer_saved_metrics=True))
            row = CACHE[key][1]
            if not row["metrics"]:
                continue
            m = json.loads(manifest.read_text())
            r = json.loads(result.read_text())
            t = r["trial"]
            labels = {
                "cell": t["cell"],
                "trial": t["id"],
                "campaign": t["campaign_id"],
                "offered_rate": str(t.get("rate", 0)),
                "target_users": str(t.get("users", 0)),
                "workload": m["campaign"].get("operation", "mixed"),
                "profile": row.get("methodology", {}).get("profile", "historical-unspecified"),
                "experiment_type": row.get("methodology", {}).get("experiment_type", "capacity"),
                "methodology": json.dumps(
                    row.get("methodology", {}), sort_keys=True, separators=(",", ":")
                ),
                "authentication_policy": str(row.get("authentication_gate", 0.99)),
            }
            label = ",".join(k + "=" + json.dumps(x) for k, x in labels.items())
            values = metric_values(row)
            for key, value in values.items():
                lines.append(f"{key}{{{label}}} {value}")
        except (KeyError, ValueError, OSError):
            continue
    return "\n".join(lines) + "\n"


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path != "/metrics":
            self.send_error(404)
            return
        data = render_metrics(Path(os.environ.get("RESULTS_DIR", "/results"))).encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; version=0.0.4")
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args):
        pass


if __name__ == "__main__":
    HTTPServer(("0.0.0.0", 9110), Handler).serve_forever()
