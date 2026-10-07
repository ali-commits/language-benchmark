"""Portable queue CLI. Execution dependencies are imported by their handlers."""

import argparse
import dataclasses
import json
import os
import re
import signal
import sys
import time
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from importlib.resources import files
from pathlib import Path
from types import FrameType

from pydantic import JsonValue

from .engine.records import DockerImage
from .engine.types import BenchmarkConfig, SearchUsers
from .queue.records import PublicJob, PublicStatus
from .queue.services import ServiceReceipt
from .queue.store import JobReceipt, QueueReceipt, QueueStore
from .records import checked_record


class Arguments(argparse.Namespace):
    command: str
    action: str
    template: str
    output: Path
    config: Path
    env_file: Path | None
    show_effective: bool
    state_dir: Path
    queue: str
    job_id: str
    job: Path
    file: Path
    expected_revision: int | None
    detach: bool
    after_current: bool
    timeout: float
    json: bool


@contextmanager
def environment_file(path: Path | None) -> Iterator[None]:
    previous = dict(os.environ)
    try:
        if path:
            path = path.resolve()
            import stat

            info = path.lstat()
            if (
                not stat.S_ISREG(info.st_mode)
                or info.st_uid != os.getuid()
                or stat.S_IMODE(info.st_mode) != 0o600
            ):
                raise ValueError("environment file must be an owned regular 0600 file")
            for line in path.read_text().splitlines():
                if not line.strip() or line.lstrip().startswith("#"):
                    continue
                key, separator, value = line.partition("=")
                if (
                    not separator
                    or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key)
                    or "\x00" in value
                ):
                    raise ValueError(
                        "environment file requires literal NAME=value lines; no shell syntax"
                    )
                os.environ[key] = value
        yield
    finally:
        os.environ.clear()
        os.environ.update(previous)


def receipt(value: QueueReceipt | JobReceipt | ServiceReceipt) -> None:
    print(json.dumps(dataclasses.asdict(value), sort_keys=True, default=str))


def public_status(store: QueueStore, queue: str) -> PublicStatus:
    snapshot = store.snapshot(queue)
    state = store.controller_state(queue)
    return {
        "queue": queue,
        "state": snapshot["queues"][0]["state"],
        "revision": snapshot["queues"][0]["revision"],
        "jobs": [checked_record(dataclasses.asdict(j), PublicJob) for j in store.jobs(queue)],
        "trials": snapshot["trials"],
        "stop_requested": store.stop_request(queue),
        "last_error": state["last_error"] if state else None,
    }


def foreground(store: QueueStore, queue: str) -> int:
    from .queue.controller import run_queue

    def stop(signum: int, frame: FrameType | None) -> None:
        store.request_stop(queue)

    handlers = {s: signal.getsignal(s) for s in (signal.SIGINT, signal.SIGTERM)}
    try:
        for s in handlers:
            signal.signal(s, stop)
        result = run_queue(store, queue)
    finally:
        for s, handler in handlers.items():
            signal.signal(s, handler)
    status = public_status(store, queue)
    print(json.dumps(status))
    return 0 if result["queues"][0]["state"] == "completed" else 1


def show_plan(config: BenchmarkConfig) -> None:
    from .engine.config import resolve_jobs
    from .engine.stages import fixed_populations, resolve_stages

    for job in resolve_jobs(config):
        if job.test.type == "fixed":
            populations = fixed_populations(job)
        else:
            assert isinstance(job.test.users, SearchUsers) and job.test.limits is not None
            populations = (job.test.users.start,)
        for users in populations:
            stages = resolve_stages(job, users)
            print(
                f"{job.id}/{job.target_name}: users={users}; scheduled={sum(s.duration for s in stages):g}s plus drain={job.drain:g}s; repetitions={job.test.repetitions}; preparation/cleanup additional"
            )
            for stage in stages:
                print(
                    f"  {stage.id}: {stage.from_users} -> {stage.to_users} ({stage.duration:g}s), evaluate={stage.evaluate}"
                )
        if job.test.type == "search":
            assert isinstance(job.test.users, SearchUsers) and job.test.limits is not None
            print(
                f"  search budget: {job.test.limits.model_dump()}; population max={job.test.users.max}; trials unknown until measured"
            )


def doctor(config: BenchmarkConfig) -> int:
    import tempfile

    from .engine.collectors import collectors_for
    from .engine.config import resolve_jobs
    from .engine.docker_env import validate_container_environment
    from .engine.targets import execute_host, resolve_endpoints
    from .engine.workloads import prepare_workload

    for job in resolve_jobs(config):
        if job.workload.adapter == "social":
            from .engine.social import preflight_social
            from .engine.stages import fixed_populations, resolve_stages

            if job.test.type == "fixed":
                populations = fixed_populations(job)
            else:
                assert isinstance(job.test.users, SearchUsers) and job.test.limits is not None
                ceilings = [
                    value
                    for value in (job.test.users.max, job.test.limits.hard_users)
                    if isinstance(value, int)
                ]
                capacity = preflight_social(job.workload)
                ceilings.append(capacity["account_capacity"])
                populations = (min(ceilings),)
            peak = max(
                max(stage.from_users, stage.to_users)
                for users in populations
                for stage in resolve_stages(job, users)
            )
            capacity = preflight_social(job.workload, peak)
            print(f"{job.id}: social fixture/accounts/runtime: {json.dumps(capacity)}")
        validate_container_environment(
            {key: os.environ[value.env] for key, value in job.target.env.items()}
        )
        for host in (job.environment.backend, job.environment.generator):
            execute_host(host, ["docker", "info"], timeout=20)
        execute_host(
            job.environment.generator,
            ["docker", "image", "inspect", "grafana/k6:1.4.2"],
            timeout=20,
        )
        if job.target.image:
            inspection = execute_host(
                job.environment.backend,
                ["docker", "image", "inspect", job.target.image],
                timeout=20,
            )
            if job.workload.adapter == "social":
                from .engine.resources import runtime_root
                from .models import NativeCell
                from .native_runtime import load_runtime_cell, native_image_metadata

                assert (
                    job.workload.fixture is not None
                    and job.workload.fixture.runtime_cell is not None
                )
                cell = load_runtime_cell(job.workload.fixture.runtime_cell, runtime_root())
                if isinstance(cell, NativeCell):
                    image = checked_record(json.loads(inspection.stdout), list[DockerImage])[0]
                    native_image_metadata(
                        {
                            "id": image["Id"],
                            "os": image["Os"],
                            "architecture": image["Architecture"],
                            "labels": image["Config"].get("Labels") or {},
                        },
                        cell,
                        image["Id"],
                    )
        else:
            from .engine.types import Host

            execute_host(Host(transport="local"), ["docker", "buildx", "version"], timeout=20)
        resolve_endpoints(job.target, job.environment, host_port=job.target.port)
        for collector in collectors_for(job):
            collector.discover()
            print(
                f"{job.id}: {collector.scope}: {'available' if collector.available else collector.reason}"
            )
        with tempfile.TemporaryDirectory(prefix="bench-doctor-") as directory:
            prepare_workload(job.workload, Path(directory) / "workload")
        print(
            f"{job.id}: routing and inputs checked; generator capacity must be verified from actual load telemetry"
        )
    show_plan(config)
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="bench", description="Portable HTTP benchmark queues")
    commands = parser.add_subparsers(dest="command", required=True)
    init = commands.add_parser("init")
    init.add_argument("--template", choices=["http", "social"], default="http")
    init.add_argument("--output", type=Path, required=True)
    for name in ("validate", "doctor"):
        sub = commands.add_parser(name)
        sub.add_argument("config", type=Path)
        sub.add_argument(
            "--env-file",
            type=Path,
            help="Owned 0600 literal NAME=value file, or export references in the caller environment",
        )
        if name == "validate":
            sub.add_argument("--show-effective", action="store_true")
    queue = commands.add_parser("queue")
    actions = queue.add_subparsers(dest="action", required=True)
    for name in ("start", "list", "status", "logs", "append", "update", "remove", "stop", "resume"):
        sub = actions.add_parser(name)
        sub.add_argument("--state-dir", type=Path, default=Path(".bench"))
        if name == "start":
            sub.add_argument("config", type=Path)
        elif name != "list":
            sub.add_argument("queue")
        if name in ("update", "remove"):
            sub.add_argument("job_id")
        if name == "append":
            sub.add_argument("--job", type=Path, required=True)
        if name == "update":
            sub.add_argument("--file", type=Path, required=True)
            sub.add_argument("--expected-revision", type=int)
        if name in ("start", "resume"):
            sub.add_argument("--detach", action="store_true")
            sub.add_argument("--env-file", type=Path)
        if name == "stop":
            sub.add_argument("--after-current", action="store_true")
            sub.add_argument("--timeout", type=float, default=300)
        if name in ("status", "list"):
            sub.add_argument("--json", action="store_true")
    report = commands.add_parser("report")
    report.add_argument("queue")
    report.add_argument("--state-dir", type=Path, default=Path(".bench"))
    report.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv, namespace=Arguments())
    try:
        with environment_file(getattr(args, "env_file", None)):
            return dispatch(args)
    except Exception as exc:
        from .engine.config import ConfigError
        from .queue.services import ServiceUnavailable
        from .queue.store import QueueConflict

        # Validation and conflict messages contain public definitions, never subprocess output.
        message = (
            str(exc)
            if isinstance(
                exc, (ConfigError, QueueConflict, ServiceUnavailable, ValueError, FileExistsError)
            )
            else type(exc).__name__
        )
        print(f"bench: {message}", file=sys.stderr)
        return 2


def dispatch(args: Arguments) -> int:
    from .engine.config import load_config, resolve_jobs

    if args.command == "init":
        output = args.output.resolve()
        source = files("tools.bench").joinpath("templates").joinpath(args.template)
        output.parent.mkdir(parents=True, exist_ok=True)
        if output.exists():
            raise FileExistsError("output already exists")
        # Copy sidecars into a unique sibling directory; YAML remains relative and portable.
        import shutil

        assets = output.parent / (output.stem + "-assets")
        if assets.exists():
            raise FileExistsError("template asset directory already exists")
        shutil.copytree(str(source), assets)
        import yaml

        from .engine.config import UniqueLoader

        data = checked_record(
            yaml.load((assets / "queue.yaml").read_text(), Loader=UniqueLoader), JsonValue
        )

        def relocate(value: JsonValue) -> JsonValue:
            if isinstance(value, dict):
                return {key: relocate(item) for key, item in value.items()}
            if isinstance(value, list):
                return [relocate(item) for item in value]
            if isinstance(value, str) and value.startswith("./"):
                return f"./{assets.name}/" + value[2:]
            return value

        output.write_text(yaml.safe_dump(relocate(data), sort_keys=False))
        (assets / "queue.yaml").unlink()
        print(str(output))
        return 0
    if args.command in ("validate", "doctor"):
        config = load_config(args.config)
        if args.command == "doctor":
            return doctor(config)
        if args.show_effective:
            print(
                json.dumps(
                    [j.model_dump(mode="json", by_alias=True) for j in resolve_jobs(config)],
                    indent=2,
                )
            )
        else:
            print(f"Valid configuration: {config.name} ({len(config.jobs)} jobs)")
            show_plan(config)
        return 0
    from .queue.store import QueueStore

    store = QueueStore(args.state_dir / "queue.db")
    if args.command == "report":
        from .engine.report import write_queue_report

        write_queue_report(store, args.queue, args.output)
        print(str(args.output.resolve()))
        return 0
    action = args.action
    if action == "list":
        with store._connection() as conn:
            queues = [
                dict(row)
                for row in conn.execute("SELECT id,revision,state FROM queues ORDER BY id")
            ]
        print(
            json.dumps(queues)
            if args.json
            else "\n".join(f"{q['id']}: {q['state']} (revision {q['revision']})" for q in queues)
        )
        return 0
    if action in ("status", "logs"):
        status = public_status(store, args.queue)
        if action == "logs":
            # Audit events deliberately omit frozen definitions and arbitrary hook/load text.
            for event in store.snapshot(args.queue)["events"]:
                print(
                    json.dumps(
                        {
                            "sequence": event["sequence"],
                            "job": event["job"],
                            "kind": event["kind"],
                            "revision": event["revision"],
                        }
                    )
                )
        else:
            print(
                json.dumps(status, indent=2)
                if args.json
                else f"{args.queue}: {status['state']}\n"
                + "\n".join(
                    f"  {j['job_id']}: {j['state']} (revision {j['revision']})"
                    for j in status["jobs"]
                )
            )
        return 0
    if action in ("append", "update"):
        from .engine.config import _read
        from .engine.types import BenchmarkConfig

        snapshot = store.snapshot(args.queue)
        config = BenchmarkConfig.model_validate_json(snapshot["queues"][0]["config"])
        job_data = _read(args.job if action == "append" else args.file)
        # Full single-job YAML is also accepted; shared definitions remain frozen.
        if "version" in job_data:
            loaded = load_config(args.job if action == "append" else args.file)
            if len(loaded.jobs) != 1:
                raise ValueError("replacement/append file must contain exactly one job")
            job = loaded.jobs[0]
            for group in ("targets", "workloads", "environments"):
                frozen = getattr(config, group)
                for name, definition in getattr(loaded, group).items():
                    if name not in frozen or definition != frozen[name]:
                        raise ValueError(
                            "shared definitions are frozen; replacement files may only replace a job"
                        )
            if loaded.defaults != config.defaults:
                raise ValueError(
                    "shared defaults are frozen; supply a standalone full job replacement"
                )
        else:
            normalized = config.model_dump(mode="python", by_alias=True)
            normalized["jobs"] = [job_data]
            # Normalize criteria units through the same loader using frozen definitions.
            import tempfile

            import yaml

            with tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "job.yaml"
                path.write_text(yaml.safe_dump(json.loads(json.dumps(normalized, default=str))))
                job = load_config(path).jobs[0]
        if action == "append":
            receipt(store.append(args.queue, job))
        else:
            revision = args.expected_revision
            if revision is None:
                revision = next(
                    (j.revision for j in store.jobs(args.queue) if j.job_id == args.job_id), None
                )
                if revision is None:
                    raise ValueError("unknown job")
            receipt(store.update(args.queue, args.job_id, job, revision))
        return 0
    if action == "remove":
        receipt(store.cancel(args.queue, args.job_id))
        return 0
    if action == "stop":
        if args.timeout < 0 or not __import__("math").isfinite(args.timeout):
            raise ValueError("timeout must be finite and nonnegative")
        from .queue.services import _unit_name, stop_service

        unit = (
            Path(os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config")))
            / "systemd/user"
            / _unit_name(args.queue, args.state_dir)
        )
        if unit.exists():
            stop_service(args.queue, args.state_dir.resolve(), after_current=args.after_current)
            print(json.dumps({"queue_id": args.queue, "stop_requested": True}))
        else:
            receipt(store.request_stop(args.queue, args.after_current))
        deadline = time.monotonic() + args.timeout
        while True:
            status = public_status(store, args.queue)
            if status["state"] in ("completed", "interrupted", "needs_attention"):
                print(json.dumps(status))
                return 0 if status["state"] != "needs_attention" else 1
            if not any(j["state"] == "active" for j in status["jobs"]):
                from .queue.controller import reconcile_queue

                reconcile_queue(store, args.queue)
                final = public_status(store, args.queue)
                print(json.dumps(final))
                return 1 if final["state"] == "needs_attention" else 0
            if time.monotonic() >= deadline:
                print("stopping: cleanup has not yet been verified", file=sys.stderr)
                return 1
            time.sleep(min(0.2, max(0, deadline - time.monotonic())))
    if action == "start":
        config = load_config(args.config)
        show_plan(config)
        registered = store.register(config)
        receipt(registered)
        queue = registered.queue_id
    else:
        queue = args.queue
        store.snapshot(queue)
    if args.detach:
        from .queue.services import install_service, resume_service, start_service

        if action == "start":
            receipt(
                install_service(queue, args.state_dir.resolve(), environment_file=args.env_file)
            )
            start_service(queue, args.state_dir.resolve())
        else:
            resume_service(queue, args.state_dir.resolve(), environment_file=args.env_file)
        return 0
    if action == "resume":
        receipt(store.clear_stop(queue))
    return foreground(store, queue)


if __name__ == "__main__":
    raise SystemExit(main())
