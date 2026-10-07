"""Reports derive distributions from raw points; failures remain in denominators."""

import html
import json
import statistics
import zlib
from collections.abc import Iterator
from pathlib import Path

from .manifest import atomic_json
from .records import (
    CampaignSummary,
    EndpointMetrics,
    ExternalResources,
    JourneyMetrics,
    MetricPoint,
    StreamStatus,
    TrialManifest,
    TrialResult,
    TrialSummary,
    checked_record,
)
from .telemetry import validate_rds_evidence


def qualify(metrics: JourneyMetrics, reasons: list[str], workload: str = "mixed") -> str:
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


def read_metrics(path: Path) -> JourneyMetrics:
    durations = []
    per: dict[str, EndpointMetrics] = {}
    metrics: JourneyMetrics = {"attempted": 0, "succeeded": 0, "dropped": 0, "http_requests": 0}
    status: StreamStatus = {"incomplete": False}
    for line in metric_lines(path, status):
        try:
            point = checked_record(json.loads(line), MetricPoint)
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
            {
                "mean": statistics.fmean(durations),
                "p999": percentile(durations, 0.999),
                "p50": percentile(durations, 0.5),
                "p95": percentile(durations, 0.95),
                "p99": percentile(durations, 0.99),
            }
        )
    metrics["endpoints"] = {
        op: {
            "attempted": b["attempted"],
            "succeeded": b["succeeded"],
            **(
                {
                    "mean": statistics.fmean(b["durations"]),
                    "p999": percentile(b["durations"], 0.999),
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


def generator_flags(path: Path, start: float | None = None, end: float | None = None) -> list[str]:
    if not path.exists():
        return ["host telemetry missing"]
    flags = []
    last = None
    samples = 0
    for line in path.read_text().splitlines():
        sample = json.loads(line)
        if start is not None and end is not None and not start <= sample.get("time", 0) <= end:
            continue
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


def summarize_campaign(results_dir: Path) -> CampaignSummary:
    trials = []
    for manifest_path in sorted(results_dir.glob("*/manifest.json")):
        trials.append(read_trial_summary(manifest_path))
    groups: dict[tuple[str, str, int | None], list[TrialSummary]] = {}
    for t in trials:
        key = (t["campaign"], t["cell"], t["rate"])
        groups.setdefault(key, []).append(t)
    summary: list[dict[str, object]] = []
    for (campaign, cell, rate), rows in groups.items():
        values = [
            r["successful_operations_s"] for r in rows if r["successful_operations_s"] is not None
        ]
        summary.append(
            {
                "campaign": campaign,
                "cell": cell,
                "offered_rate": rate,
                "repetitions": len(rows),
                "successful_ops_s_median": statistics.median(values) if values else None,
                "successful_ops_s_range": [min(values), max(values)] if values else None,
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
            round(t["successful_operations_s"], 2)
            if t["successful_operations_s"] is not None
            else "unavailable",
            round(m["mean"], 2) if m.get("mean") is not None else "unavailable",
            round(m["p95"], 2) if m.get("p95") is not None else "unavailable",
            round(m["p99"], 2) if m.get("p99") is not None else "unavailable",
            round(m["p999"], 2) if m.get("p999") is not None else "unavailable",
            m.get("success_fraction", "unavailable"),
            t.get("authentication_fraction", "unavailable"),
            t.get("resources", {})
            .get("phases", {})
            .get("steady", {})
            .get("cpu_percent_mean", "unavailable"),
            t.get("resources", {})
            .get("phases", {})
            .get("steady", {})
            .get("memory_working_set_mib_peak", "unavailable"),
            m.get("dropped", "missing"),
            t["qualification"],
            "; ".join(t["reasons"]),
        ]
        rows.append(
            "<tr>" + "".join("<td>" + html.escape(str(v)) + "</td>" for v in values) + "</tr>"
        )
    charts = plot_curves(report, output_path.parent)
    output_path.write_text(
        '<!doctype html><html lang="en"><meta charset="utf-8"><title>Language benchmark report</title><style>body{font:16px system-ui;margin:2rem}table{border-collapse:collapse}td,th{padding:.5rem;border:1px solid #ccc}td:last-child{max-width:30rem}</style><h1>Language benchmark</h1><p>'
        + html.escape(report["method"])
        + "</p><table><tr>"
        + "".join(
            "<th>" + x + "</th>"
            for x in [
                "Label",
                "Cell",
                "Concurrent users"
                if any(t.get("workload") == "user-journey" for t in report["trials"])
                else "Offered ops/s",
                "Successful ops/s",
                "Mean ms (failures included)",
                "p95 ms",
                "p99 ms",
                "p99.9 ms",
                "Operation success",
                "Authentication fraction",
                "Backend CPU % of 2 CPUs",
                "Sampled peak working set MiB",
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


def metric_lines(path: Path, status: StreamStatus) -> Iterator[bytes]:
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


def plot_curves(report: CampaignSummary, output_dir: Path) -> list[Path]:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    output_dir.mkdir(parents=True, exist_ok=True)
    groups: dict[tuple[str, str], list[TrialSummary]] = {}
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
            rates = sorted({t["rate"] for t in trials if t["rate"] is not None})
            values = []
            used = []
            for rate in rates:
                sample = [
                    (
                        t.get("successful_operations_s")
                        if metric == "successful_operations_s"
                        else t["metrics"].get("p95")
                    )
                    for t in trials
                    if t["rate"] == rate
                ]
                present = [v for v in sample if v is not None]
                if present:
                    used.append(rate)
                    values.append(statistics.median(present))
            if used:
                axis.plot(used, values, marker="o", label=cell + " / " + campaign)
        axis.set_xlabel(
            "Concurrent users"
            if any(t.get("workload") == "user-journey" for t in report["trials"])
            else "Offered operations/s"
        )
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


def read_trial_summary(manifest_path: Path, *, prefer_saved_metrics: bool = False) -> TrialSummary:
    p = manifest_path.with_name("result.json")
    manifest = checked_record(json.loads(manifest_path.read_text()), TrialManifest)
    result: TrialResult = (
        checked_record(json.loads(p.read_text()), TrialResult)
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
    metrics: JourneyMetrics = {}
    config = manifest["campaign"]
    journey = manifest["campaign"].get("operation") == "user-journey"
    metrics_source = "raw metric samples"
    if prefer_saved_metrics:
        # The live exporter trusts only finalized reductions and never scans raw gzip.
        saved = result.get("journey_metrics") if journey else result.get("metrics")
        required = (
            ("attempted", "succeeded", "authenticated_users", "active_users_max", "p95", "p99")
            if journey
            else ("attempted", "succeeded", "dropped", "p95", "p99")
        )
        if (
            result.get("status") == "measured"
            and isinstance(saved, dict)
            and all(key in saved for key in required)
        ):
            metrics = saved.copy()
            metrics_source = "finalized saved reduction"
        else:
            metrics_source = "finalized saved reduction unavailable"
    elif metrics_path.exists():
        if journey:
            from .journeys import read_journey_metrics

            metrics = read_journey_metrics(metrics_path, steady_seconds=config.get("hold_seconds"))
        else:
            metrics = read_metrics(metrics_path)
    from .analysis import backend_log_flags, methodology
    from .resources import (
        phase_windows,
        summarize_generator_resources,
        summarize_rds_resources,
        summarize_resources,
    )

    reasons = list(result.get("reasons", [])) + backend_log_flags(p.parent)
    if prefer_saved_metrics and not metrics:
        reasons.append("finalized metric reduction unavailable; offline raw analysis required")
    if metrics.get("incomplete"):
        reasons.append("raw metric stream is incomplete or corrupt")
    if result.get("status") != "measured":
        reasons.append("trial did not complete measurement lifecycle")
    if not result.get("reset_verified"):
        reasons.append("final fixture restoration not verified")
    if journey:
        from .journeys import generator_window, journey_flags

        reasons += generator_flags(p.parent / "telemetry.jsonl", *generator_window(result))
        reasons += journey_flags(metrics)
    else:
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
    if journey:
        from .journeys import classify_journey

        outcome = classify_journey(
            metrics,
            result["trial"]["users"],
            reasons,
            config.get("success_fraction", 0.99),
            authentication_fraction=config.get("authentication_fraction"),
        )
        qualification = {"pass": "pass", "fail": "fail", "invalid": "unknown"}[outcome]
    else:
        qualification = qualify(metrics, reasons, manifest["campaign"].get("operation", "mixed"))
    login = metrics.get("endpoints", {}).get("login", {})
    if not journey and qualification == "pass" and login.get("p95", 0) > 500:
        qualification = "fail"
    seconds = config.get("hold_seconds") if journey else config.get("measure_seconds")
    users = result["trial"].get("users")
    resources = summarize_resources(
        p.parent / "telemetry.jsonl", phase_windows(result, config, metrics)
    )
    method = methodology(config)
    external_resources: ExternalResources = {
        "generator": summarize_generator_resources(
            p.parent / "telemetry.jsonl", phase_windows(result, config, metrics)
        ),
        "rds": summarize_rds_resources(
            p.parent / "rds.json",
            result.get("measurement_started_epoch", 0),
            result.get("measurement_finished_epoch", 0),
        ),
    }

    def rate(count: float) -> float | None:
        return count / seconds if seconds else None

    return {
        "id": result["trial"]["id"],
        "campaign": result["trial"]["campaign_id"],
        "cell": result["trial"]["cell"],
        "rate": result["trial"].get("users", result["trial"].get("rate")),
        "target_users": result["trial"].get("users"),
        "workload": manifest["campaign"].get("operation", "mixed"),
        "label": result.get("label"),
        "status": result["status"],
        "qualification": qualification,
        "reasons": reasons,
        "metrics": metrics,
        "metrics_source": metrics_source,
        "achieved_operations_s": rate(metrics["attempted"]) if "attempted" in metrics else None,
        "achieved_http_requests_s": rate(metrics["http_requests"])
        if "http_requests" in metrics
        else None,
        "successful_operations_s": rate(metrics["succeeded"]) if "succeeded" in metrics else None,
        "authentication_fraction": metrics.get("authenticated_users", 0) / users if users else None,
        "authentication_gate": config.get(
            "authentication_fraction", config.get("success_fraction", 0.99)
        ),
        "methodology": method,
        "resources": resources,
        "external_resources": external_resources,
        "evidence": {
            "generator_telemetry": str(p.parent / "telemetry.jsonl"),
            "rds_telemetry": str(p.parent / "rds.json"),
            "backend_logs": str(p.parent / "backend.log"),
        },
        "artifact": str(p.parent),
    }
