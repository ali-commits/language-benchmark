"""Pinned preparation and retained-copy reconstruction, independent of dispatch."""

from collections.abc import Callable
from pathlib import Path
from typing import Protocol

from tools.bench.engine.docker_env import validate_container_environment
from tools.bench.engine.environment_binding import (
    BoundEnvironment,
    bind_environment,
    reference_names,
)
from tools.bench.engine.stages import fixed_populations
from tools.bench.engine.targets import (
    BuildSnapshot,
    ResolvedTarget,
    load_build_snapshot,
    tree_checksums,
)
from tools.bench.engine.types import Build, Environment, Host, ResolvedJob, SearchUsers, Target
from tools.bench.engine.workloads import PreparedWorkload, prepare_workload
from tools.bench.records import checked_record

from .inputs import (
    check_build,
    check_build_policies,
    check_file,
    check_retained_build,
    check_workload,
    freeze_fixture,
    prepared_fixture,
)
from .records import FrozenInputs, JobCheckpoint, PreparedPin, TargetPin
from .store import QueueConflict

MAX_RECOVERY_ATTEMPTS = 3


class TargetResolver(Protocol):
    def __call__(
        self,
        target: Target,
        environment: Environment,
        *,
        snapshot_directory: Path | None = None,
        validate_snapshot: Callable[[BuildSnapshot], None] | None = None,
    ) -> ResolvedTarget: ...


class ExecutionIdentity(Protocol):
    def __call__(
        self,
        job: ResolvedJob,
        users: int | None,
        target: ResolvedTarget,
        workload: PreparedWorkload,
        /,
        *,
        generator_image_id: str,
        fixture_manifest: Path | None = None,
        environment_binding: str | None = None,
    ) -> str: ...


def prepare(
    data: JobCheckpoint,
    index: int,
    job: ResolvedJob,
    *,
    expected_inputs: FrozenInputs,
    allow_key_creation: bool,
    root: Path,
    save: Callable[[str | None], None],
    check_stop: Callable[[], None],
    resolve: TargetResolver,
    inspect_image: Callable[[Host, str], str],
    execution_identity: ExecutionIdentity,
) -> tuple[ResolvedTarget, PreparedWorkload, str, BoundEnvironment | None]:
    key = str(index)
    bindings = data.setdefault("environment_bindings", {})
    expected = bindings.get(key)
    if (
        reference_names(job)
        and expected is None
        and (
            key in data["prepared"]
            or data["pending"]
            or any(d["target"] == index for d in data["decisions"])
        )
    ):
        raise QueueConflict("legacy checkpoint lacks environment binding; create a new queue")
    try:
        bound = bind_environment(
            job,
            root / ".environment-key",
            expected=expected,
            create=allow_key_creation,
        )
        env = {name: bound.values[ref.env] for name, ref in job.target.env.items()} if bound else {}
        validate_container_environment(env)
    except ValueError as exc:
        raise QueueConflict(str(exc)) from exc
    if bound is not None and expected is None:
        bindings[key] = bound.digest
        save(None)
    check_stop()
    key = str(index)
    prep = data["prepared"].get(key)
    directory = root / data["directory"] / ("prepare-" + key)
    if prep is None:
        data["preparation_attempts"][key] = data["preparation_attempts"].get(key, 0) + 1
        if data["preparation_attempts"][key] > MAX_RECOVERY_ATTEMPTS:
            raise QueueConflict("preparation attempt budget exhausted")
        save("preparing")
        # Each interrupted preparation gets a fresh path; never overwrite receipts.
        directory /= str(data["preparation_attempts"][key])
        retained_target = data.setdefault("target_pins", {}).get(key)
        if retained_target is None:
            build_spec: Build | None = job.target.build
            if build_spec is not None:
                check_build_policies(build_spec, expected_inputs)
                target = resolve(
                    job.target,
                    job.environment,
                    snapshot_directory=directory / "build",
                    validate_snapshot=lambda snapshot: check_build(
                        build_spec, snapshot, expected_inputs
                    ),
                )
            else:
                target = resolve(
                    job.target, job.environment, snapshot_directory=directory / "build"
                )
            retained_target = checked_record(
                {
                    "image": target.image_id,
                    "requested_image": target.requested_image,
                    "repo_digests": list(target.repo_digests),
                    "build": str(target.build_snapshot.context.parent)
                    if target.build_snapshot
                    else None,
                },
                TargetPin,
            )
            data["target_pins"][key] = retained_target
            save(None)
        target = ResolvedTarget(
            job.target,
            retained_target["requested_image"],
            retained_target["image"],
            tuple(retained_target["repo_digests"]),
            load_build_snapshot(Path(retained_target["build"]))
            if retained_target["build"]
            else None,
        )
        check_stop()
        cached = data["workloads"].get(job.workload_name)
        if cached is None:
            workload = prepare_workload(job.workload, directory / "workload")
            check_workload(job.workload, workload, expected_inputs)
            cached = {
                "directory": str(workload.directory),
                "script": str(workload.script),
                "checksums": workload.checksums,
                "identity": workload.identity,
            }
            fixture_pin = freeze_fixture(job.workload, directory / "fixture", expected_inputs)
            if fixture_pin is not None:
                cached["fixture"] = fixture_pin
            data["workloads"][job.workload_name] = cached
        generator = inspect_image(job.environment.generator, "grafana/k6:1.4.2")
        prep = checked_record(
            {
                "image": target.image_id,
                "requested_image": target.requested_image,
                "repo_digests": list(target.repo_digests),
                "build": str(target.build_snapshot.context.parent)
                if target.build_snapshot
                else None,
                "workload": cached,
                "generator": generator,
            },
            PreparedPin,
        )
        data["prepared"][key] = prep
        save(None)
    build = load_build_snapshot(Path(prep["build"])) if prep["build"] else None
    if build is not None and job.target.build is not None:
        check_retained_build(job.target.build, build, expected_inputs)
    target = ResolvedTarget(
        job.target, prep["requested_image"], prep["image"], tuple(prep["repo_digests"]), build
    )
    work = prep["workload"]
    workload = PreparedWorkload(
        Path(work["directory"]), Path(work["script"]), work["checksums"], work["identity"]
    )
    check_workload(job.workload, workload, expected_inputs)
    fixture = prepared_fixture(work)
    if job.workload.fixture and job.workload.fixture.manifest:
        if fixture is None:
            # Upgrade an older pin only from the original frozen manifest. The
            # logical path/hash in execution identity remains unchanged.
            fixture_pin = freeze_fixture(
                job.workload, Path(work["directory"]).parent / "fixture", expected_inputs
            )
            if fixture_pin is not None:
                work["fixture"] = fixture_pin
            fixture = prepared_fixture(work)
        assert fixture is not None
        check_file(job.workload.fixture.manifest, fixture.verify(), expected_inputs)
    if tree_checksums(workload.directory) != workload.checksums:
        raise QueueConflict("prepared workload changed")
    inspect_image(job.environment.backend, target.image_id)
    inspect_image(job.environment.generator, prep["generator"])
    if job.test.type == "search":
        assert isinstance(job.test.users, SearchUsers)
        identity_users: int | None = job.test.users.min
    else:
        identity_users = fixed_populations(job)[0]
    identity = execution_identity(
        job,
        identity_users,
        target,
        workload,
        generator_image_id=prep["generator"],
        fixture_manifest=fixture.verify() if fixture else None,
        environment_binding=bound.digest if bound else None,
    )
    if prep.get("execution_identity", identity) != identity:
        raise QueueConflict("execution identity changed; cannot resume")
    prep["execution_identity"] = identity
    save(None)
    check_stop()
    return target, workload, prep["generator"], bound
