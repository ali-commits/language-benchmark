"""Persistent mixed-language fixed-load controller; never starts a capacity search."""

import argparse
import json
import uuid
from pathlib import Path

from .models import NativeCell
from .native_runtime import expected_image_id, load_runtime_cell
from .profiles import normalize_config
from .records import Campaign, checked_record
from .user_capacity import run_user_capacity, validate_config


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    config = normalize_config(checked_record(json.loads(args.config.read_text()), Campaign))
    if config["experiment_type"] != "fixed-load":
        raise ValueError("fixed campaign requires experiment_type=fixed-load")
    validate_config(config)
    for name in config["cells"]:
        cell = load_runtime_cell(name)
        if not cell.supported:
            raise ValueError("fixed campaign runtime is unsupported: " + name)
        if isinstance(cell, NativeCell):
            expected_image_id(config, cell)
    resume = bool(list(args.output.glob("*/manifest.json")))
    if args.output.exists() and not resume:
        if any(
            (args.output / name).exists()
            for name in ("capacity-results.json", "fixed-load-results.json")
        ) or list(args.output.glob("*/result.json")):
            raise RuntimeError("unverified previous results require their original manifests")
        archive = args.output.with_name(
            args.output.name + "-premeasurement-" + uuid.uuid4().hex[:8]
        )
        args.output.rename(archive)
    results = run_user_capacity(args.config, args.output, resume=resume, retry_invalid=True)
    print(json.dumps(results, indent=2))
    # A valid below-target observation is useful fixed-load evidence. Infrastructure-invalid
    # or incomplete results must make the service fail so supervision can report/recover it.
    return (
        0
        if len(results) == len(config["cells"])
        and all(result.get("status") == "fixed_load_complete" for result in results.values())
        else 1
    )


if __name__ == "__main__":
    raise SystemExit(main())
