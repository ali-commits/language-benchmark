"""Finish the pilot, choose one common rate grid, and run the authorized full matrix."""

import json
import os
import subprocess
import time
from pathlib import Path

from bench.report import summarize_campaign, write_report
from bench.runner import run_campaign

root = Path("artifacts/aws")


def status(value, **extra):
    (root / "campaign-status.json").write_text(
        json.dumps({"status": value, "updated_epoch": time.time(), **extra}, indent=2)
    )


if __name__ == "__main__":
    status("waiting-for-pilot")
    while (
        subprocess.run(["systemctl", "is-active", "--quiet", "language-benchmark-pilot"]).returncode
        == 0
    ):
        time.sleep(30)
    exit_file = root / "pilot.exit"
    if not exit_file.exists() or exit_file.read_text().strip() != "0":
        status("pilot-failed-needs-attention")
        raise SystemExit(1)
    # Allow delayed CloudWatch series to arrive; the backfill service updates evidence.
    time.sleep(180)
    summary = summarize_campaign(Path("artifacts/results/pilot"))
    by_cell = {}
    for trial in summary["trials"]:
        if trial["qualification"] == "pass":
            by_cell.setdefault(trial["cell"], []).append(trial["rate"])
    cfg = json.loads((root / "campaign.json").read_text())
    if not all(c in by_cell for c in cfg["cells"]):
        status(
            "pilot-evidence-needs-attention",
            reason="At least one baseline has no qualified pilot rate; full campaign was not launched.",
        )
        raise SystemExit(1)
    knee = min(max(rates) for rates in by_cell.values())
    rates = sorted(set(max(25, min(3200, int(knee / factor))) for factor in (8, 4, 2, 1, 0.5)))
    if len(rates) < 3:
        rates = [25, 50, 100, 200, 400]
    cfg.update(
        cells=[c["id"] for c in json.loads(Path("runtimes/python/lock.json").read_text())["cells"]],
        rates=rates,
        vus=1000,
    )
    path = root / "full-campaign.json"
    fd = os.open(path, os.O_CREAT | os.O_TRUNC | os.O_WRONLY, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(cfg, f, indent=2)
    status(
        "full-campaign-running",
        rates=rates,
        cells=cfg["cells"],
        trials=len(cfg["cells"]) * len(rates) * cfg["repeats"],
    )
    results = run_campaign(path, Path("artifacts/results/full"))
    write_report(Path("artifacts/results/full"), Path("artifacts/reports/full/report.html"))
    failed = any(
        r["status"] in ("failed", "interrupted") or not r["reset_verified"] for r in results
    )
    status(
        "failed-needs-attention" if failed else "complete-remind-to-stop",
        completed_trials=len(results),
    )
