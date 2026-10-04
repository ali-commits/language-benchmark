"""Reports derive distributions from raw points; failures remain in denominators."""

import html
import json
import statistics
import zlib
from pathlib import Path

from .manifest import atomic_json
from .telemetry import validate_rds_evidence


def qualify(metrics: dict, reasons: list[str], workload: str = "mixed") -> str:
    required = ("attempted", "succeeded", "dropped", "p95", "p99")
    if reasons or any(k not in metrics for k in required) or metrics.get("attempted", 0) <= 0:
        return "unknown"
    return (
        "pass"
        if metrics["succeeded"] / metrics["attempted"] >= 0.999
        and metrics["dropped"] == 0
        and metrics["p95"] <= (500 if workload == "login" else 200)
        and (workload == "login" or metrics["p99"] <= 500)
        else "fail"
    )


def percentile(values: list[float], percent: float) -> float:
    v = sorted(values)
    i = (len(v) - 1) * percent
    lo = int(i)
    hi = min(lo + 1, len(v) - 1)
    return v[lo] + (v[hi] - v[lo]) * (i - lo)


def read_metrics(path: Path) -> dict:
    durations = []
    per = {}
    metrics = {"attempted": 0, "succeeded": 0, "dropped": 0, "http_requests": 0}
    status = {"incomplete": False}
    for line in metric_lines(path, status):
        try:
            point = json.loads(line)
        except (json.JSONDecodeError, UnicodeDecodeError):
            status["incomplete"] = True
            continue
        if point.get("type") != "Point":
            continue
        name = point["metric"]
        data = point["data"]
        value = data["value"]
        op = data.get("tags", {}).get("operation", "unknown")
        bucket = per.setdefault(op, {"attempted": 0, "succeeded": 0, "durations": []})
        if name == "operation_duration":
            durations.append(value)
            bucket["durations"].append(value)
        if name == "operations_attempted":
            metrics["attempted"] += value
            bucket["attempted"] += value
        if name == "operations_succeeded":
            metrics["succeeded"] += value
            bucket["succeeded"] += value
        if name == "http_reqs":
            metrics["http_requests"] += value
        if name == "dropped_iterations":
            metrics["dropped"] += value
    metrics["incomplete"] = status["incomplete"]
    if durations:
        metrics.update(
            p50=percentile(durations, 0.5),
            p95=percentile(durations, 0.95),
            p99=percentile(durations, 0.99),
        )
    metrics["endpoints"] = {
        op: {
            **{k: b[k] for k in ["attempted", "succeeded"]},
            **(
                {
                    "p50": percentile(b["durations"], 0.5),
                    "p95": percentile(b["durations"], 0.95),
                    "p99": percentile(b["durations"], 0.99),
                }
                if b["durations"]
                else {}
            ),
        }
        for op, b in per.items()
        if b["attempted"]
    }
    return metrics


def generator_flags(path: Path) -> list[str]:
    if not path.exists():
        return ["host telemetry missing"]
    flags = []
    last = None
    samples = 0
    for line in path.read_text().splitlines():
        sample = json.loads(line)
        host = sample.get("hosts", {}).get("generator", {})
        if "stat" not in host:
            return ["generator telemetry incomplete"]
        mem = dict(
            (line.split(":")[0], int(line.split(":")[1].strip().split()[0]))
            for line in host["meminfo"].splitlines()
        )
        if mem.get("SwapTotal", 0) > mem.get("SwapFree", 0):
            flags.append(
                "generator has swap allocated; verify paging/activity before qualification"
            )
        cpus = [
            [int(x) for x in line.split()[1:9]]
            for line in host["stat"].splitlines()
            if line.startswith("cpu")
        ]
        if last:
            for old, new in zip(last, cpus):
                total = sum(new) - sum(old)
                idle = (new[3] + new[4]) - (old[3] + old[4])
                if total > 0 and (1 - idle / total) > 0.60:
                    flags.append("generator CPU/core above 60%; inspect saturation")
        last = cpus
        samples += 1
    if samples < 2:
        flags.append("insufficient host telemetry samples")
    return sorted(set(flags))


def summarize_campaign(results_dir: Path) -> dict:
    trials = []
    for manifest_path in sorted(results_dir.glob("*/manifest.json")):
        p = manifest_path.with_name("result.json")
        manifest = json.loads(manifest_path.read_text())
        result = (
            json.loads(p.read_text())
            if p.exists()
            else {
                "trial": manifest["trial"],
                "status": "incomplete",
                "label": manifest.get("label"),
                "reset_verified": False,
                "reasons": ["trial ended before final result was persisted"],
            }
        )
        metrics_path = p.parent / "k6/metrics.json.gz"
        metrics = {}
        if metrics_path.exists():
            metrics = read_metrics(metrics_path)
        reasons = list(result.get("reasons", []))
        if metrics.get("incomplete"):
            reasons.append("raw metric stream is incomplete or corrupt")
        if result.get("status") != "measured":
            reasons.append("trial did not complete measurement lifecycle")
        if not result.get("reset_verified"):
            reasons.append("final fixture restoration not verified")
        reasons += generator_flags(p.parent / "telemetry.jsonl")
        if manifest.get("campaign", {}).get("mode") == "aws":
            evidence = p.parent / "rds.json"
            if not evidence.exists():
                reasons.append("RDS telemetry missing")
            else:
                reasons += validate_rds_evidence(
                    json.loads(evidence.read_text()),
                    result.get("measurement_started_epoch", 0),
                    result.get("measurement_finished_epoch", 0),
                )
        qualification = qualify(metrics, reasons, manifest["campaign"].get("operation", "mixed"))
        login = metrics.get("endpoints", {}).get("login", {})
        if qualification == "pass" and login.get("p95", 0) > 500:
            qualification = "fail"
        seconds = manifest["campaign"]["measure_seconds"]
        trials.append(
            {
                "id": result["trial"]["id"],
                "campaign": result["trial"]["campaign_id"],
                "cell": result["trial"]["cell"],
                "rate": result["trial"]["rate"],
                "label": result.get("label"),
                "status": result["status"],
                "qualification": qualification,
                "reasons": reasons,
                "metrics": metrics,
                "achieved_operations_s": metrics.get("attempted", 0) / seconds,
                "achieved_http_requests_s": metrics.get("http_requests", 0) / seconds,
                "successful_operations_s": metrics.get("succeeded", 0) / seconds,
                "artifact": str(p.parent),
            }
        )
    groups = {}
    for t in trials:
        key = (t["campaign"], t["cell"], t["rate"])
        groups.setdefault(key, []).append(t)
    summary = []
    for (campaign, cell, rate), rows in groups.items():
        values = [r["successful_operations_s"] for r in rows]
        summary.append(
            {
                "campaign": campaign,
                "cell": cell,
                "offered_rate": rate,
                "repetitions": len(rows),
                "successful_ops_s_median": statistics.median(values),
                "successful_ops_s_range": [min(values), max(values)],
                "all_trials_qualify": all(r["qualification"] == "pass" for r in rows),
            }
        )
    return {
        "trials": trials,
        "groups": summary,
        "method": "Per-trial percentiles from raw points; medians/ranges of trial throughput. Different campaign identities are never pooled. No AWS performance inferred from local results.",
    }


def write_report(results_dir: Path, output_path: Path) -> None:
    report = summarize_campaign(results_dir)
    atomic_json(output_path.with_suffix(".json"), report)
    rows = []
    for t in report["trials"]:
        m = t["metrics"]
        values = [
            t["label"],
            t["cell"],
            t["rate"],
            round(t["successful_operations_s"], 2),
            round(m.get("p95", 0), 2),
            round(m.get("p99", 0), 2),
            m.get("dropped", "missing"),
            t["qualification"],
            "; ".join(t["reasons"]),
        ]
        rows.append(
            "<tr>" + "".join("<td>" + html.escape(str(v)) + "</td>" for v in values) + "</tr>"
        )
    charts = plot_curves(report, output_path.parent)
    output_path.write_text(
        '<!doctype html><html lang="en"><meta charset="utf-8"><title>Python benchmark report</title><style>body{font:16px system-ui;margin:2rem}table{border-collapse:collapse}td,th{padding:.5rem;border:1px solid #ccc}td:last-child{max-width:30rem}</style><h1>Python FastAPI benchmark</h1><p>'
        + html.escape(report["method"])
        + "</p><table><tr>"
        + "".join(
            "<th>" + x + "</th>"
            for x in [
                "Label",
                "Cell",
                "Offered ops/s",
                "Successful ops/s",
                "p95 ms",
                "p99 ms",
                "Dropped",
                "Qualification",
                "Evidence/limitations",
            ]
        )
        + "</tr>"
        + "".join(rows)
        + "</table>"
        + "".join(
            '<figure><img style="max-width:100%" src="'
            + html.escape(p.name)
            + '" alt="Benchmark curve"></figure>'
            for p in charts
        )
        + "</html>"
    )


def metric_lines(path: Path, status: dict):
    decoder = zlib.decompressobj(16 + zlib.MAX_WBITS)
    buffer = b""
    with path.open("rb") as f:
        while chunk := f.read(65536):
            try:
                buffer += decoder.decompress(chunk)
            except zlib.error:
                status["incomplete"] = True
                break
            lines = buffer.split(b"\n")
            buffer = lines.pop()
            for line in lines:
                if line.strip():
                    yield line
            if len(buffer) > 1048576:
                status["incomplete"] = True
                break
    if not decoder.eof:
        status["incomplete"] = True
    if buffer.strip():
        yield buffer


def plot_curves(report: dict, output_dir: Path) -> list[Path]:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    output_dir.mkdir(parents=True, exist_ok=True)
    groups = {}
    for trial in report["trials"]:
        if not trial["metrics"].get("attempted", 1):
            continue
        groups.setdefault((trial["campaign"], trial["cell"]), []).append(trial)
    paths = []
    for metric, label, filename in [
        ("successful_operations_s", "Successful operations/s", "throughput.png"),
        ("p95", "p95 operation latency (ms)", "latency-p95.png"),
    ]:
        fig, axis = plt.subplots(figsize=(9, 5), layout="constrained")
        for (campaign, cell), trials in groups.items():
            rates = sorted({t["rate"] for t in trials})
            values = []
            used = []
            for rate in rates:
                sample = [
                    t.get(metric, t["metrics"].get(metric)) for t in trials if t["rate"] == rate
                ]
                sample = [v for v in sample if v is not None]
                if sample:
                    used.append(rate)
                    values.append(statistics.median(sample))
            if used:
                axis.plot(used, values, marker="o", label=cell + " / " + campaign)
        axis.set_xlabel("Offered operations/s")
        axis.set_ylabel(label)
        axis.grid(True, alpha=0.25)
        axis.set_title("Trial medians; consult qualification and individual-trial evidence")
        if groups:
            axis.legend(fontsize=8)
        path = output_dir / filename
        fig.savefig(path, dpi=150)
        plt.close(fig)
        paths.append(path)
    return paths
