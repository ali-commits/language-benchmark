"""Read-only, credential-free queue summaries over retained raw evidence."""

import html
import json
import math
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Protocol

from typing_extensions import TypedDict

from ..records import checked_record
from .metrics import evaluate
from .records import RawCriterion, RawWorkload
from .types import Criterion, SampleScope, TrialEvidence

Catalog = dict[tuple[SampleScope, str, str | None], set[str]]

class Expansion(TypedDict, total=False):
    workload: RawWorkload
    criteria: list[RawCriterion]
    objectives: dict[str, list[RawCriterion]]

class ReportJob(TypedDict, total=False):
    id: str
    state: str
    resolved: str | list[Expansion]

class QueueRow(TypedDict):
    state: str
    revision: int

class TrialReference(TypedDict):
    id: str
    job: str
    state: str
    path: str

class ReportSnapshot(TypedDict):
    queues: list[QueueRow]
    jobs: list[ReportJob]
    trials: list[TrialReference]

class JobState(TypedDict, total=False):
    results: list[dict[str, object]]
    decisions: list[dict[str, object]]

class ReportState(TypedDict, total=False):
    jobs: dict[str, JobState]

class ReportStore(Protocol):
    def snapshot(self, queue: str) -> object: ...
    def controller_state(self, queue: str) -> object: ...

class Window(TypedDict):
    name: str
    metrics: list[dict[str, object]]

class TrialReport(TypedDict, total=False):
    id: str
    job: str
    state: str
    status: str
    windows: list[Window]
    evidence: str
    source_sha256: str
    users: int | None
    cleanup_verified: bool
    invalid_reasons: list[str]
    stages: list[dict[str, object]]

class QueueReport(TypedDict):
    version: int
    queue: str
    state: str
    revision: int
    jobs: list[dict[str, object]]
    objectives: list[dict[str, object]]
    decisions: list[dict[str, object]]
    trials: list[TrialReport]


def _metric_catalog(job: ReportJob) -> Catalog:
    """Extract only public metric definitions from frozen resolved expansions."""
    resolved = job.get("resolved", [])
    if isinstance(resolved, str):
        resolved = checked_record(json.loads(resolved), list[Expansion])
    catalog: Catalog = {}
    for expansion in resolved:
        for name, declaration in expansion.get("workload", {}).get("metrics", {}).items():
            key: tuple[SampleScope, str, str | None] = ("workload", declaration.get("name", name), declaration.get("unit"))
            catalog.setdefault(key, set()).add(
                "rate" if declaration.get("type") == "rate" else "mean"
            )
        criteria = [*expansion.get("criteria", [])]
        for objective in expansion.get("objectives", {}).values():
            criteria.extend(objective)
        for criterion in criteria:
            scope = checked_record(criterion.get("scope", "workload"), SampleScope)
            metric = criterion["metric"]
            unit = criterion.get("unit")
            if unit is None:
                declared = [key for key in catalog if key[:2] == (scope, metric)]
                if declared:
                    unit = declared[0][2]
            catalog.setdefault((scope, metric, unit), set()).add(criterion["statistic"])
    return catalog


def _metrics(evidence: TrialEvidence, stages: list[str], catalog: Catalog | None = None) -> list[dict[str, object]]:
    catalog = catalog or {}
    pairs: set[tuple[SampleScope, str, str | None]] = {(s.scope, s.metric, s.unit) for s in evidence.samples}
    pairs.update(catalog)
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
        rate = any(s.kind == "rate" for s in samples) or "rate" in catalog.get(
            (scope, metric, unit), set()
        )
        row: dict[str, object] = {
            "scope": scope,
            "metric": metric,
            "unit": unit,
            "status": "unavailable",
            "definitions": sorted({s.formula for s in samples if s.formula}),
            "normalizations": sorted({s.normalization for s in samples if s.normalization}),
            "includes_failures": sorted({s.includes_failures for s in samples}),
        }
        statistics = (
            ["rate"]
            if rate
            else ["mean", "max"] + ([] if temporal else ["p50", "p95", "p99", "p99.9"])
        )
        statistics = list(
            dict.fromkeys([*statistics, *sorted(catalog.get((scope, metric, unit), set()))])
        )
        unavailable_reasons: dict[str, str | None] = {}
        for stat in statistics:
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
                unavailable_reasons[stat] = result.reason
                row["unavailable_reasons"] = unavailable_reasons
        if scope == "workload" and metric == "success":
            windows = [(s.start_seconds, s.end_seconds) for s in evidence.stages if s.id in stages]
            selected = [s for s in samples if any(a <= s.timestamp < b for a, b in windows)]
            seconds = sum(b - a for a, b in windows)
            if row["status"] == "available" and seconds:
                row["attempted_rps"] = sum(s.denominator for s in selected if s.denominator is not None) / seconds
                row["successful_rps"] = sum(s.numerator for s in selected if s.numerator is not None) / seconds
            else:
                row["attempted_rps"] = row["successful_rps"] = None
        rows.append(row)
    return rows


def _curve(points: Sequence[tuple[float | None, float | None]], title: str) -> str:
    finite_points = sorted(
        (x, y)
        for x, y in points
        if isinstance(x, (float, int))
        and isinstance(y, (float, int))
        and math.isfinite(x)
        and math.isfinite(y)
    )
    if not finite_points:
        return f"<p>{html.escape(title)}: unavailable</p>"
    xmax = max(x for x, _ in finite_points) or 1
    ymax = max(y for _, y in finite_points) or 1
    coordinates = " ".join(
        f"{20 + x / xmax * 560:.2f},{180 - y / ymax * 150:.2f}" for x, y in finite_points
    )
    return f'<figure><figcaption>{html.escape(title)} (time in seconds; vertical maximum {ymax:g})</figcaption><svg viewBox="0 0 600 200" role="img" aria-label="{html.escape(title, quote=True)}"><path d="M20 20V180H580" fill="none" stroke="gray"/><polyline points="{coordinates}" fill="none" stroke="#2563eb" stroke-width="2"/></svg></figure>'


def _table(rows: Sequence[Mapping[str, object]]) -> str:
    if not rows:
        return "<p>unavailable</p>"
    keys = list(dict.fromkeys(k for row in rows for k in row))

    def cell(v: object) -> str:
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


def write_queue_report(store: ReportStore, queue: str, output: Path) -> Path:
    """Write report.html and report.json; never export config, paths or logs."""
    output = Path(output)
    destination = output if output.suffix == ".html" else output / "report.html"
    destination.parent.mkdir(parents=True, exist_ok=True)
    snapshot = checked_record(store.snapshot(queue), ReportSnapshot)
    state = checked_record(store.controller_state(queue) or {}, ReportState)
    report: QueueReport = {
        "version": 1,
        "queue": queue,
        "state": snapshot["queues"][0]["state"],
        "revision": snapshot["queues"][0]["revision"],
        "jobs": [{"id": j["id"], "state": j["state"]} for j in snapshot["jobs"]],
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
    catalogs = {job["id"]: _metric_catalog(job) for job in snapshot["jobs"]}
    figures = []
    for ref in snapshot["trials"]:
        trial: TrialReport = {
            "id": ref["id"],
            "job": ref["job"],
            "state": ref["state"],
            "status": "unavailable",
            "windows": [
                {
                    "name": "unavailable",
                    "metrics": _metrics(TrialEvidence(), [], catalogs.get(ref["job"])),
                }
            ],
        }
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
                trial.update({
                    "status": "valid" if evidence.valid else "invalid",
                    "users": evidence.users,
                    "cleanup_verified": evidence.cleanup_verified,
                    "invalid_reasons": list(evidence.invalid_reasons),
                    "stages": [s.model_dump(mode="json") for s in evidence.stages],
                    "windows": [],
                })
                selected = [s.id for s in evidence.stages if s.evaluate]
                for name, ids in [
                    ("selected", selected),
                    *[(s.id, [s.id]) for s in evidence.stages],
                ]:
                    if ids or name == "selected":
                        trial["windows"].append(
                            {
                                "name": name,
                                "metrics": _metrics(evidence, ids, catalogs.get(ref["job"])),
                            }
                        )
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
                                    if s.kind == "rate" and s.denominator and s.numerator is not None
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
    for key, rows in (("jobs", report["jobs"]), ("objectives", report["objectives"]), ("decisions", report["decisions"])):
        body += "<h2>" + key + "</h2>" + _table(rows)
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
