"""Sequential, checkpointed campaign supervision on the monitoring host."""

import argparse
import fcntl
import hashlib
import subprocess
import time
from pathlib import Path

from .majority import measurement_identity
from .manifest import atomic_json
from .native_runtime import load_runtime_cell, registry_metadata
from .profiles import normalize_config
from .runner import defer_cleanup_signals
from .supervisor import COMPLETE, controller_active, read_status, supervise
from .transport import execute
from .user_capacity import validate_resume_resource_policy


def verify_completion(stage):
    output = Path(stage["output"])
    if read_status(output / "capacity-status.json").get("status") not in COMPLETE:
        raise RuntimeError("stage has not completed with qualified evidence")
    config = read_status(Path(stage["config"]))
    fixed = config["experiment_type"] == "fixed-load"
    results = read_status(
        output / ("fixed-load-results.json" if fixed else "capacity-results.json")
    )
    if set(results) != set(config["cells"]):
        raise RuntimeError("stage results do not cover the configured cells")
    for result in results.values():
        if fixed:
            observations = result.get("observations", [])
            if (
                result.get("status") != "fixed_load_complete"
                or result.get("fixed_users") != config["fixed_users"]
                or [o.get("users") for o in observations] != config["fixed_users"]
                or any(o.get("outcome") not in ("pass", "fail") for o in observations)
            ):
                raise RuntimeError("fixed-load stage has incomplete or invalid evidence")
        elif result.get("status") not in ("bracket_found", "ceiling_reached", "unstable_boundary"):
            raise RuntimeError("capacity stage has incomplete or invalid evidence")
    manifests = sorted(output.glob("*/manifest.json"))
    if not manifests:
        raise RuntimeError("stage is missing original trial manifests")
    for path in manifests:
        manifest = read_status(path)
        saved = read_status(path.with_name("result.json"))
        if measurement_identity(manifest.get("campaign", {})) != measurement_identity(config):
            raise RuntimeError("completed stage measurement identity differs from configuration")
        validate_resume_resource_policy(
            manifest,
            normalize_config(config),
            saved.get("status") == "measured" and saved.get("reset_verified") is True,
        )
        if manifest.get("runtime_lock"):
            runtime = load_runtime_cell(manifest["trial"]["cell"])
            if manifest["runtime_lock"] != registry_metadata(runtime):
                raise RuntimeError("completed stage runtime registry differs from measured binary")
    latest = read_status(manifests[-1].with_name("result.json"))
    if latest.get("status") != "measured" or latest.get("reset_verified") is not True:
        raise RuntimeError("final fixture cleanup is not verified")


def verify_hosts_idle(stage):
    if controller_active(stage["controller"]):
        raise RuntimeError("previous controller remains active")
    config = read_status(Path(stage["config"]))
    allowed = (
        config.get("execution_policy", {})
        .get("backend_baseline_gate", {})
        .get("allowed_telemetry_containers", ["language-benchmark-host-telemetry-node-exporter-1"])
    )
    for key in ("backend_host", "load_host"):
        output = execute(config[key], ["docker", "ps", "--format", "{{.Names}}"], timeout=30).stdout
        if any(name not in allowed for name in output.splitlines() if name):
            raise RuntimeError("test containers remain after stage completion")


def run_pipeline(config_path, state_path, sleep=time.sleep):
    config_path, state_path = Path(config_path), Path(state_path)
    config = read_status(config_path)
    stages = config["stages"]

    def fingerprint():
        return hashlib.sha256(
            config_path.read_bytes() + b"".join(Path(s["config"]).read_bytes() for s in stages)
        ).hexdigest()

    identity = fingerprint()

    def pinned():
        if fingerprint() != identity:
            raise RuntimeError("pipeline configuration changed during execution")

    state_path.parent.mkdir(parents=True, exist_ok=True)
    previous = read_status(state_path)
    if previous.get("identity") not in (None, identity):
        raise RuntimeError("pipeline configuration changed; preserve the original queue")
    completed = previous.get("completed_stages", [])
    if not isinstance(completed, list) or completed != [
        s["name"] for s in stages[: len(completed)]
    ]:
        raise RuntimeError("pipeline completion receipts are not an ordered prefix")
    current = None

    def status(value, **details):
        atomic_json(
            state_path,
            {
                "status": value,
                "identity": identity,
                "completed_stages": list(completed),
                "updated_epoch": time.time(),
                **details,
            },
        )

    with state_path.with_suffix(".lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            for current in stages:
                pinned()
                name = current["name"]
                if name in completed:
                    # Revalidate immutable past evidence, not the hosts now owned by a later stage.
                    verify_completion(current)
                    continue
                if current.get("observe_supervisor"):
                    while controller_active(current["controller"]) or controller_active(
                        current["observe_supervisor"]
                    ):
                        status("watching_existing_controller", stage=name)
                        sleep(15)
                elif (
                    read_status(Path(current["output"]) / "capacity-status.json").get("status")
                    not in COMPLETE
                ):
                    status("running_controller", stage=name)
                    supervise(
                        Path(current["config"]),
                        Path(current["output"]),
                        Path(current["state"]),
                        current["controller"],
                    )
                pinned()
                verify_completion(current)
                verify_hosts_idle(current)
                pinned()
                completed.append(name)
                status("stage_complete", stage=name)
            status("complete", stages=[s["name"] for s in stages])
        except KeyboardInterrupt:
            # Stop only the owned stage; its controller performs protected cleanup.
            with defer_cleanup_signals():
                unfinished = next((s for s in stages if s["name"] not in completed), None)
                if unfinished and not unfinished.get("observe_supervisor"):
                    subprocess.run(
                        ["sudo", "systemctl", "stop", unfinished["controller"]],
                        check=True,
                        capture_output=True,
                    )
                status("interrupted", stage=unfinished["name"] if unfinished else None)
            raise
        except Exception as error:
            status(
                "needs_attention",
                stage=current["name"] if current else None,
                error_type=type(error).__name__,
            )
            raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--state", type=Path, required=True)
    args = parser.parse_args()
    run_pipeline(args.config, args.state)


if __name__ == "__main__":
    main()
