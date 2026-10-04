"""Expose completed immutable artifact summaries for Grafana, without touching the tested DB."""

import json
import os
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

from bench.report import qualify, read_metrics


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path != "/metrics":
            self.send_error(404)
            return
        lines = []
        root = Path(os.environ.get("RESULTS_DIR", "/results"))
        for manifest in root.rglob("manifest.json"):
            result = manifest.with_name("result.json")
            raw = manifest.parent / "k6/metrics.json.gz"
            if not result.exists() or not raw.exists():
                continue
            try:
                m = json.loads(manifest.read_text())
                r = json.loads(result.read_text())
                t = r["trial"]
                v = read_metrics(raw)
                labels = {
                    "cell": t["cell"],
                    "trial": t["id"],
                    "campaign": t["campaign_id"],
                    "offered_rate": str(t["rate"]),
                    "workload": m["campaign"].get("operation", "mixed"),
                }
                label = ",".join(k + "=" + json.dumps(x) for k, x in labels.items())
                reasons = list(r.get("reasons", []))
                if not r.get("reset_verified") or v.get("incomplete") or r["status"] != "measured":
                    reasons.append("incomplete")
                valid = qualify(v, reasons, labels["workload"])
                values = {
                    "benchmark_successful_ops_per_second": v.get("succeeded", 0)
                    / m["campaign"]["measure_seconds"],
                    "benchmark_latency_p95_ms": v.get("p95", 0),
                    "benchmark_latency_p99_ms": v.get("p99", 0),
                    "benchmark_trial_qualified": {"pass": 1, "fail": 0, "unknown": -1}[valid],
                }
                for key, value in values.items():
                    lines.append(f"{key}{{{label}}} {value}")
            except (KeyError, ValueError, OSError):
                continue
        data = ("\n".join(lines) + "\n").encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; version=0.0.4")
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args):
        pass


if __name__ == "__main__":
    HTTPServer(("0.0.0.0", 9110), Handler).serve_forever()
