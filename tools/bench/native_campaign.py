"""Persistent native campaign entrypoint: start fresh or resume without changing provenance."""

import argparse
import json
import uuid
from pathlib import Path

from .models import NativeCell
from .native_runtime import expected_image_id, load_runtime_cell
from .user_capacity import run_user_capacity


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    config = json.loads(args.config.read_text())
    for name in config["cells"]:
        cell = load_runtime_cell(name)
        if not isinstance(cell, NativeCell):
            raise ValueError("native campaign requires native cells only")
        expected_image_id(config, cell)
    resume = bool(list(args.output.glob("*/manifest.json")))
    if args.output.exists() and not resume:
        if (args.output / "capacity-results.json").exists() or list(
            args.output.glob("*/result.json")
        ):
            raise RuntimeError("unverified previous results require their original manifests")
        archive = args.output.with_name(
            args.output.name + "-premeasurement-" + uuid.uuid4().hex[:8]
        )
        args.output.rename(archive)  # Preserve a status/empty-trial checkpoint from an early crash.
    results = run_user_capacity(args.config, args.output, resume=resume, retry_invalid=True)
    print(json.dumps(results, indent=2))


if __name__ == "__main__":
    main()
