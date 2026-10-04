"""Backfill delayed CloudWatch evidence after measurement; preserve final validity rules."""

import json
import time
from datetime import datetime, timezone
from pathlib import Path

from bench.manifest import atomic_json
from bench.telemetry import rds_metrics, validate_rds_evidence


def replace_evidence_reasons(previous, validated):
    return [r for r in previous if not r.startswith("RDS ")] + validated


def refresh(root: Path):
    for manifest in root.rglob("manifest.json"):
        result = manifest.with_name("result.json")
        evidence = manifest.with_name("rds.json")
        if not result.exists():
            continue
        m = json.loads(manifest.read_text())
        r = json.loads(result.read_text())
        if m.get("campaign", {}).get("mode") != "aws" or r.get("status") != "measured":
            continue
        start = r.get("measurement_started_epoch")
        end = r.get("measurement_finished_epoch")
        if not start or not end or time.time() - end < 90:
            continue
        if evidence.exists() and not validate_rds_evidence(
            json.loads(evidence.read_text()), start, end
        ):
            continue
        cfg = m["campaign"]
        try:
            data = rds_metrics(
                cfg["rds_identifier"],
                cfg["region"],
                datetime.fromtimestamp(start, timezone.utc),
                datetime.fromtimestamp(end, timezone.utc),
            )
            reasons = validate_rds_evidence(data, start, end)
            atomic_json(evidence, data)
            r["reasons"] = replace_evidence_reasons(r.get("reasons", []), reasons)
            atomic_json(result, r)
        except Exception as e:
            print(type(e).__name__, flush=True)


if __name__ == "__main__":
    while True:
        refresh(Path("artifacts/results"))
        time.sleep(60)
