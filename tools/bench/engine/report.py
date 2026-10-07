"""Read-only, credential-free queue summaries over retained raw evidence."""

import html
import json
import math
from pathlib import Path

from .metrics import evaluate
from .types import Criterion, TrialEvidence


def _metrics(evidence, stages):
    pairs = {(s.scope, s.metric, s.unit) for s in evidence.samples}
    pairs.update(
        {
            ("backend_host", "cpu_used", "fraction"),
            ("container", "cpu_used", "fraction"),
            ("backend_host", "memory_used", "bytes"),
            ("container", "memory_used", "bytes"),
            ("database", "cpu_used", "fraction"),
            ("database", "memory_used", "bytes"),
        }
    )
    rows = []
    for scope, metric, unit in sorted(pairs, key=str):
        samples = [s for s in evidence.samples if s.scope == scope and s.metric == metric]
        temporal = any(s.kind in {"gauge", "interval"} for s in samples)
        rate = any(s.kind == "rate" for s in samples)
        row = {
            "scope": scope,
            "metric": metric,
            "unit": unit,
            "status": "unavailable",
            "definitions": sorted({s.formula for s in samples if s.formula}),
            "normalizations": sorted({s.normalization for s in samples if s.normalization}),
            "includes_failures": sorted({s.includes_failures for s in samples}),
        }
        for stat in (
            ["rate"]
            if rate
            else ["mean", "max"] + ([] if temporal else ["p50", "p95", "p99", "p99.9"])
        ):
            result = evaluate(
                evidence,
                (
                    Criterion(
                        metric=metric,
                        scope=scope,
                        statistic=stat,
                        operator=">=",
                        value=0.0,
                        unit=unit,
                        stages=stages,
                    ),
                ),
            ).results[0]
            row[stat] = result.value if result.valid else None
            row["sample_count"] = result.sample_count
            row["coverage"] = result.coverage
            if result.valid:
                row["status"] = "available"
            else:
                row.setdefault("unavailable_reasons", {})[stat] = result.reason
        if scope == "workload" and metric == "success":
            windows = [(s.start_seconds, s.end_seconds) for s in evidence.stages if s.id in stages]
            selected = [s for s in samples if any(a <= s.timestamp < b for a, b in windows)]
            seconds = sum(b - a for a, b in windows)
            if row["status"] == "available" and seconds:
                row["attempted_rps"] = sum(s.denominator for s in selected) / seconds
                row["successful_rps"] = sum(s.numerator for s in selected) / seconds
            else:
                row["attempted_rps"] = row["successful_rps"] = None
        rows.append(row)
    return rows


def _curve(points, title):
    points = sorted(
        (x, y)
        for x, y in points
        if isinstance(x, (float, int))
        and isinstance(y, (float, int))
        and math.isfinite(x)
        and math.isfinite(y)
    )
    if not points:
        return f"<p>{html.escape(title)}: unavailable</p>"
    xmax = max(x for x, _ in points) or 1
    ymax = max(y for _, y in points) or 1
    coordinates = " ".join(
        f"{20 + x / xmax * 560:.2f},{180 - y / ymax * 150:.2f}" for x, y in points
    )
    return f'<figure><figcaption>{html.escape(title)} (time in seconds; vertical maximum {ymax:g})</figcaption><svg viewBox="0 0 600 200" role="img" aria-label="{html.escape(title, quote=True)}"><path d="M20 20V180H580" fill="none" stroke="gray"/><polyline points="{coordinates}" fill="none" stroke="#2563eb" stroke-width="2"/></svg></figure>'


def _table(rows):
    if not rows:
        return "<p>unavailable</p>"
    keys = list(dict.fromkeys(k for row in rows for k in row))

    def cell(v):
        return html.escape(
            json.dumps(v, ensure_ascii=False)
            if isinstance(v, (dict, list))
            else "unavailable"
            if v is None
            else str(v)
        )

    return (
        "<table><thead><tr>"
        + "".join(f"<th>{cell(k)}</th>" for k in keys)
        + "</tr></thead><tbody>"
        + "".join(
            "<tr>" + "".join(f"<td>{cell(row.get(k))}</td>" for k in keys) + "</tr>" for row in rows
        )
        + "</tbody></table>"
    )


def write_queue_report(store, queue: str, output: Path) -> Path:
    """Write report.html and report.json; never export config, paths or logs."""
    output = Path(output)
    destination = output if output.suffix == ".html" else output / "report.html"
    destination.parent.mkdir(parents=True, exist_ok=True)
    snapshot = store.snapshot(queue)
    state = store.controller_state(queue) or {}
    report = {
        "version": 1,
        "queue": queue,
        "state": snapshot["queues"][0]["state"],
        "revision": snapshot["queues"][0]["revision"],
        "jobs": [{k: j[k] for k in ("id", "state") if k in j} for j in snapshot["jobs"]],
        "objectives": [],
        "decisions": [],
        "trials": [],
    }
    for job, data in state.get("jobs", {}).items():
        for result in data.get("results", []):
            report["objectives"].append(
                {
                    "job": job,
                    **{
                        k: v
                        for k, v in result.items()
                        if k
                        in {
                            "objective",
                            "target",
                            "status",
                            "passing_users",
                            "failing_users",
                            "observed_only",
                            "unstable_users",
                            "reason",
                            "limit",
                            "contradictions",
                            "observations",
                            "attempts",
                            "confirmation",
                        }
                    },
                }
            )
        for decision in data.get("decisions", []):
            report["decisions"].append(
                {
                    "job": job,
                    **{
                        k: v
                        for k, v in decision.items()
                        if k
                        in {
                            "trial_id",
                            "target",
                            "objective",
                            "reused",
                            "source_objective",
                            "evaluation",
                        }
                    },
                }
            )
    figures = []
    for ref in snapshot["trials"]:
        trial = {"id": ref["id"], "job": ref["job"], "state": ref["state"], "status": "unavailable"}
        path = Path(ref["path"])
        source = next(
            (p for p in (path / "result.json", path / "interrupted.json") if p.is_file()), None
        )
        if source:
            try:
                evidence = TrialEvidence.model_validate_json(source.read_text())
                import hashlib

                public_evidence = evidence.model_dump(
                    mode="json", exclude={"manifest_path", "samples_path"}
                )
                for sample in public_evidence["samples"]:
                    sample.pop("provider_fields", None)
                artifact_name = hashlib.sha256(ref["id"].encode()).hexdigest() + ".json"
                artifact = destination.parent / "evidence" / artifact_name
                artifact.parent.mkdir(exist_ok=True)
                artifact.write_text(json.dumps(public_evidence, indent=2, allow_nan=False) + "\n")
                trial["evidence"] = "evidence/" + artifact_name
                trial["source_sha256"] = hashlib.sha256(source.read_bytes()).hexdigest()
                trial.update(
                    status="valid" if evidence.valid else "invalid",
                    users=evidence.users,
                    cleanup_verified=evidence.cleanup_verified,
                    invalid_reasons=list(evidence.invalid_reasons),
                    stages=[s.model_dump(mode="json") for s in evidence.stages],
                    windows=[],
                )
                selected = [s.id for s in evidence.stages if s.evaluate]
                for name, ids in [
                    ("selected", selected),
                    *[(s.id, [s.id]) for s in evidence.stages],
                ]:
                    if ids:
                        trial["windows"].append({"name": name, "metrics": _metrics(evidence, ids)})
                figures.append(
                    _curve(
                        [(s.start_seconds, s.from_users) for s in evidence.stages]
                        + [(s.end_seconds, s.to_users) for s in evidence.stages],
                        ref["id"] + ": requested users",
                    )
                )
                for scope, metric in sorted({(s.scope, s.metric) for s in evidence.samples}):
                    figures.append(
                        _curve(
                            [
                                (
                                    s.timestamp,
                                    s.numerator / s.denominator
                                    if s.kind == "rate" and s.denominator
                                    else s.value,
                                )
                                for s in evidence.samples
                                if s.scope == scope and s.metric == metric
                            ],
                            ref["id"] + ": " + scope + "/" + metric,
                        )
                    )
            except (ValueError, OSError):
                trial["status"] = "invalid artifact"
        report["trials"].append(trial)
    destination.with_suffix(".json").write_text(
        json.dumps(report, indent=2, allow_nan=False) + "\n"
    )
    body = (
        "<h1>Queue "
        + html.escape(queue)
        + "</h1><p>State: "
        + html.escape(report["state"])
        + "</p><p>Unavailable evidence is never zero. Bounds belong to their objective; observed_only means reused observations, not an independent search. CPU temporal means are time weighted. Shared generator/backend hosts can interfere. Raw private configuration and logs are excluded.</p>"
    )
    for key in ("jobs", "objectives", "decisions"):
        body += "<h2>" + key + "</h2>" + _table(report[key])
    for trial in report["trials"]:
        body += (
            "<h2>Trial "
            + html.escape(trial["id"])
            + "</h2>"
            + _table([{k: v for k, v in trial.items() if k not in {"windows", "stages"}}])
            + _table(trial.get("stages", []))
        )
        if trial.get("evidence"):
            body += (
                '<p><a href="'
                + html.escape(trial["evidence"], quote=True)
                + '">Sanitized raw evidence</a></p>'
            )
        for window in trial.get("windows", []):
            body += "<h3>" + html.escape(window["name"]) + "</h3>" + _table(window["metrics"])
    destination.write_text(
        '<!doctype html><html lang="en"><meta charset="utf-8"><title>Benchmark report</title><style>body{font:14px system-ui;margin:2rem}table{border-collapse:collapse;display:block;overflow:auto}td,th{border:1px solid #ccc;padding:.4rem}svg{max-width:700px;width:100%}</style>'
        + body
        + "".join(figures)
        + "</html>"
    )
    return destination
