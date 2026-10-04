import fcntl
import json
import os
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

import httpx
import psycopg

from .jit_probe import JIT_PROBE
from .journeys import prepare_journey_data, read_journey_metrics
from .load_data import prepare_load_data
from .manifest import atomic_json, collect_manifest
from .models import RuntimeCell
from .reset import restore_fixture
from .runtime import validate_dependency_inventory, validate_worker_snapshots
from .schedule import schedule_trials
from .telemetry import Collector, rds_metrics, validate_rds_evidence
from .transport import CommandFailure, copy_from, copy_to, execute


@contextmanager
def campaign_lock(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as f:
        try:
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError("another campaign owns the host lock")
        try:
            yield
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)


def run_trial(campaign: dict, trial: dict, output_dir: Path) -> dict:
    output_dir.mkdir(parents=True, exist_ok=False)
    cells = json.loads(Path("runtimes/python/lock.json").read_text())["cells"]
    cell = RuntimeCell(**next(c for c in cells if c["id"] == trial["cell"]))
    journey = campaign.get("operation") == "user-journey"
    manifest = collect_manifest(campaign, trial)
    result = {
        "status": "failed",
        "reasons": [],
        "reset_verified": False,
        "trial": trial,
        "label": manifest["label"],
    }
    atomic_json(output_dir / "manifest.json", manifest)
    if not cell.supported:
        result.update(status="unsupported", reasons=cell.reasons)
        atomic_json(output_dir / "result.json", result)
        return result
    dsn = os.environ["DATABASE_URL"]
    fixture = Path(campaign["fixture"]).resolve()
    backend = campaign.get("backend_host", {})
    load = campaign.get("load_host", {})
    base = campaign.get("base_url", "http://127.0.0.1:8000")
    project = backend.get("project", str(Path.cwd()))
    load_project = load.get("project", str(Path.cwd()))
    runtime_identity = trial_runtime_identity(trial["id"])
    runtime = f"{project}/artifacts/runtime/{runtime_identity}"
    inputs = f"{load_project}/artifacts/load/{trial['id']}/inputs"
    outputs = f"{load_project}/artifacts/load/{trial['id']}/output"
    env = {
        "BACKEND_IMAGE": cell.image,
        "MODE": campaign["mode"],
        "WORKERS": str(cell.workers),
        "ADAPTER": cell.adapter,
        "RUN_ID": runtime_identity,
        "RUN_DIR_HOST": runtime,
        "EXPECTED_GIL": str(cell.gil).lower(),
        "EXPECTED_JIT": str(cell.jit).lower(),
        "PYTHON_JIT": str(int(cell.jit)),
        "DATABASE_URL": campaign.get("backend_database_url", dsn),
        "JWT_SECRET": os.environ["JWT_SECRET"],
        "BIND_IP": campaign.get("bind_ip", "127.0.0.1"),
        "RDS_CA_FILE": os.environ.get("RDS_CA_FILE", str(Path("docker/README-ca.txt").resolve())),
    }
    compose = ["docker", "compose", "-f", "compose.aws.yaml"]

    def stop():
        execute(
            load,
            ["docker", "compose", "-f", "compose.load.yaml", "down"],
            {"INPUT_DIR": inputs, "OUTPUT_DIR": outputs},
        )
        try:
            logs = execute(backend, compose + ["logs", "--no-color"], env, timeout=30)
            from .manifest import redact

            (output_dir / "backend.log").write_text(redact(logs.stdout + "\n" + logs.stderr))
        except Exception:
            result["reasons"].append("backend logs unavailable")
        execute(backend, compose + ["down"], env)

    def reset():
        restore_fixture(dsn, fixture, campaign["database_allowlist"])

    def measure():
        execute(
            backend,
            [
                "python3",
                "-c",
                "import pathlib,os; p=pathlib.Path(os.environ['TARGET']); p.mkdir(parents=True,exist_ok=True); p.chmod(0o777)",
            ],
            {"TARGET": runtime},
        )
        execute(backend, compose + ["up", "-d"], env, timeout=120)
        deadline = time.monotonic() + 120
        snapshots = []
        while time.monotonic() < deadline:
            code = "import pathlib,json,os; print(json.dumps([json.loads(p.read_text()) for p in pathlib.Path(os.environ['TARGET']).glob('*.json')]))"
            snapshots = json.loads(
                execute(backend, ["python3", "-c", code], {"TARGET": runtime}).stdout
            )
            if len(snapshots) == cell.workers and all(
                s["run_id"] == runtime_identity for s in snapshots
            ):
                break
            time.sleep(1)
        reasons = validate_worker_snapshots(cell, snapshots) + validate_dependency_inventory(
            cell, snapshots
        )
        if reasons:
            raise ValueError("; ".join(reasons))
        manifest["workers"] = snapshots
        if cell.build == "jit":
            probe = execute(
                backend,
                [
                    "docker",
                    "run",
                    "--rm",
                    "--cpus",
                    "2",
                    "--memory",
                    "128m",
                    "--memory-swap",
                    "128m",
                    "--entrypoint",
                    "python3",
                    "-e",
                    "PYTHON_JIT=" + str(int(cell.jit)),
                    cell.image,
                    "-c",
                    JIT_PROBE,
                ],
                timeout=30,
            )
            manifest["jit_engine_probe"] = json.loads(probe.stdout)
        manifest["image"] = json.loads(
            execute(
                backend, ["docker", "image", "inspect", cell.image, "--format", "{{json .}}"]
            ).stdout
        )
        manifest["fixture"] = json.loads(fixture.read_text())
        atomic_json(output_dir / "manifest.json", manifest)
        with httpx.Client(timeout=10) as c:
            c.get(base + "/health/ready").raise_for_status()
        secret_inputs = output_dir / "private"
        secret_inputs.mkdir(mode=0o700)
        if journey:
            prepare_journey_data(
                Path(campaign["credentials"]), trial["users"], secret_inputs / "load-data.json"
            )
            scenario = {
                k: campaign[k]
                for k in (
                    "ramp_seconds",
                    "settle_seconds",
                    "hold_seconds",
                    "wait_min",
                    "wait_max",
                    "timeout_seconds",
                )
            }
            scenario.update(
                users=trial["users"],
                tags={
                    "cell": cell.id,
                    "trial": trial["id"],
                    "campaign": trial["campaign_id"],
                    "workload": "user-journey",
                    "target_users": str(trial["users"]),
                },
            )
        else:
            required = campaign["warmup_seconds"] + campaign["measure_seconds"] + 120
            os.environ["PAIR_BUDGET"] = str(
                trial["rate"] * campaign["measure_seconds"] + campaign["vus"]
            )
            load_metadata = prepare_load_data(
                base, Path(campaign["credentials"]), secret_inputs / "load-data.json", required
            )
            scenario = {
                "rate": trial["rate"],
                "duration": campaign["measure_seconds"],
                "vus": campaign["vus"],
                "seed": trial["seed"],
                "operation": campaign.get("operation", "mixed"),
                "expires_at": load_metadata["expires_at"],
                "tags": {
                    "cell": cell.id,
                    "trial": trial["id"],
                    "campaign": trial["campaign_id"],
                    "workload": campaign.get("operation", "mixed"),
                    "offered_rate": str(trial["rate"]),
                },
            }
        (secret_inputs / "scenario.json").write_text(json.dumps(scenario))
        mkdir = "import pathlib,os; [pathlib.Path(p).mkdir(parents=True,exist_ok=True) for p in (os.environ['INPUTS'],os.environ['OUTPUTS'])]; pathlib.Path(os.environ['INPUTS']).chmod(0o700); print(str(os.getuid())+':'+str(os.getgid()))"
        uidgid = (
            execute(load, ["python3", "-c", mkdir], {"INPUTS": inputs, "OUTPUTS": outputs})
            .stdout.strip()
            .split(":")
        )
        for file in secret_inputs.iterdir():
            copy_to(load, file, inputs + "/" + file.name)
        load_env = {
            "INPUT_DIR": inputs,
            "OUTPUT_DIR": outputs,
            "BASE_URL": campaign.get("load_base_url", base),
            "LOAD_UID": uidgid[0],
            "LOAD_GID": uidgid[1],
            "WARMUP_SECONDS": str(campaign["warmup_seconds"]),
            "PROMETHEUS_URL": campaign.get("prometheus_url", "http://127.0.0.1:9090/api/v1/write"),
        }
        load_compose = ["docker", "compose", "-f", "compose.load.yaml", "run", "--rm", "k6", "run"]
        with Collector(
            {"backend": {**backend, "runtime_dir": runtime}, "generator": load},
            dsn,
            output_dir / "telemetry.jsonl",
        ) as collect:
            if campaign["warmup_seconds"] and not journey:
                w = execute(
                    load,
                    load_compose + ["/scripts/warmup.js"],
                    load_env,
                    timeout=campaign["warmup_seconds"] + 90,
                )
                (output_dir / "warmup.log").write_text(w.stdout)
            # Threshold failures are overload evidence, not grounds to discard the run.
            result["status"] = "measuring"
            result["execution_started_epoch"] = time.time()
            result["measurement_started_epoch"] = result["execution_started_epoch"]
            atomic_json(output_dir / "result.json", result)
            extra_output = (
                ["--out", "experimental-prometheus-rw"] if campaign.get("prometheus_url") else []
            )
            try:
                measured = execute(
                    load,
                    load_compose
                    + ["--out", "json=/output/metrics.json.gz"]
                    + extra_output
                    + ["/scripts/user-journey.js" if journey else "/scripts/workload.js"],
                    load_env,
                    timeout=campaign["measure_seconds"] + 120,
                )
                (output_dir / "k6.log").write_text(measured.stdout)
            except CommandFailure as e:
                (output_dir / "k6.log").write_text(e.stdout + "\n" + e.stderr)
                result["k6_exit_code"] = e.returncode
                if e.returncode != 99:
                    result["reasons"].append("k6 execution failed with exit " + str(e.returncode))
            result["execution_finished_epoch"] = time.time()
            result["measurement_finished_epoch"] = result["execution_finished_epoch"]
            copy_from(load, outputs, output_dir / "k6")
        result["status"] = "measured"
        if journey:
            result["journey_metrics"] = read_journey_metrics(output_dir / "k6/metrics.json.gz")
            jm = result["journey_metrics"]
            if jm.get("steady_started_epoch"):
                result["measurement_started_epoch"] = jm["steady_started_epoch"]
                result["measurement_finished_epoch"] = jm["steady_finished_epoch"]
        if collect.errors:
            result["reasons"].append(
                "telemetry collection incomplete: " + ",".join(set(collect.errors))
            )
        if campaign["mode"] == "aws":
            try:
                telemetry = rds_metrics(
                    campaign["rds_identifier"],
                    campaign["region"],
                    datetime.fromtimestamp(result["measurement_started_epoch"], timezone.utc),
                    datetime.fromtimestamp(result["measurement_finished_epoch"], timezone.utc),
                )
                atomic_json(output_dir / "rds.json", telemetry)
                result["reasons"] += validate_rds_evidence(
                    telemetry,
                    result["measurement_started_epoch"],
                    result["measurement_finished_epoch"],
                )
            except Exception as e:
                result["reasons"].append("RDS telemetry missing: " + type(e).__name__)

    state = trial_lifecycle(stop, reset, measure)
    result["status"] = state["status"]
    result["reset_verified"] = state["reset_verified"]
    result["reasons"] += state["reasons"]
    try:
        copy_from(load, outputs, output_dir / "k6")
    except Exception:
        if state["status"] == "measured":
            result["reasons"].append("measurement artifacts missing")
    if (output_dir / "private").exists():
        import shutil

        shutil.rmtree(output_dir / "private")
    try:
        execute(
            load,
            [
                "python3",
                "-c",
                "import shutil,os; shutil.rmtree(os.environ['INPUTS'],ignore_errors=True)",
            ],
            {"INPUTS": inputs},
        )
    except Exception:
        result["reasons"].append("private load inputs require manual cleanup")
    atomic_json(output_dir / "result.json", result)

    return result


def run_campaign(config_path: Path, output_dir: Path) -> list[dict]:
    cfg = json.loads(config_path.read_text())
    cfg["campaign_instance"] = uuid.uuid4().hex
    trials = schedule_trials(cfg)
    results = []
    with (
        campaign_lock(Path("artifacts/campaign.lock")),
        database_campaign_lock(os.environ["DATABASE_URL"]),
    ):
        for trial in trials:
            result = run_trial(cfg, trial, output_dir / trial["id"])
            results.append(result)
            if result["status"] == "interrupted" or (
                result["status"] != "unsupported" and not result["reset_verified"]
            ):
                break
    return results


def trial_lifecycle(stop, reset, measure) -> dict:
    result = {"status": "failed", "reset_verified": False, "reasons": []}
    try:
        stop()
        reset()
        measure()
        result["status"] = "measured"
    except BaseException as e:
        result["status"] = (
            "interrupted" if isinstance(e, (KeyboardInterrupt, SystemExit)) else "failed"
        )
        result["reasons"].append(type(e).__name__ + ": " + str(e))
    finally:
        try:
            stop()
            reset()
            result["reset_verified"] = True
        except Exception as e:
            result["status"] = "failed"
            result["reasons"].append("cleanup failed: " + type(e).__name__ + ": " + str(e))
    return result


@contextmanager
def database_campaign_lock(dsn: str):
    # Separate from the short reset lock: held throughout the whole campaign.
    with psycopg.connect(dsn, autocommit=True, application_name="lb-orchestrator") as connection:
        if not connection.execute("SELECT pg_try_advisory_lock(20261005)").fetchone()[0]:
            raise RuntimeError("another orchestrator owns this database campaign lock")
        try:
            yield
        finally:
            connection.execute("SELECT pg_advisory_unlock(20261005)")


def trial_runtime_identity(trial_id: str) -> str:
    return trial_id + "-" + uuid.uuid4().hex
