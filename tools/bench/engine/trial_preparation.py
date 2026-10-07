"""Resolve and publish immutable trial preparation before lifecycle side effects."""

import hashlib
import json
import os
import shutil
from dataclasses import dataclass
from pathlib import Path

from ..records import checked_record
from .docker_env import validate_container_environment
from .evidence import immutable_json
from .records import DockerImage, TrialManifest
from .resources import wrapper_path
from .stages import resolve_stages
from .targets import (
    HostExecutor,
    ResolvedTarget,
    owner_labels,
    prepared_execution_identity,
    resolve_target,
)
from .trial_context import TrialContext
from .types import ResolvedStage
from .workloads import PreparedWorkload, prepare_workload


@dataclass(frozen=True)
class Preparation:
    directory: Path
    target: ResolvedTarget
    workload: PreparedWorkload
    bundle: Path
    stages: tuple[ResolvedStage, ...]
    generator_image_id: str
    execution_id: str
    labels: dict[str, str]
    target_environment: dict[str, str]


def bundle_workload(context: TrialContext, prepared: PreparedWorkload) -> Path:
    bundle = context.directory / "execution"
    bundle.mkdir()
    shutil.copytree(prepared.directory, bundle / "workload")
    for name in ("runner.js", "metrics.js"):
        shutil.copy(wrapper_path(name), bundle / name)
    runner = bundle / "runner.js"
    entry = "./workload/" + prepared.script.relative_to(prepared.directory).as_posix()
    runner.write_text(runner.read_text().replace("'./workload.js'", json.dumps(entry)))
    immutable_json(
        bundle / ".owner.json", owner_labels(context.queue_id, context.job_id, context.trial_id)
    )
    return bundle


def prepare_trial(context: TrialContext, *, execute: HostExecutor) -> Preparation:
    directory = context.directory.resolve()
    if (directory / "manifest.json").exists():
        raise ValueError("trial already exists: reconcile and use a new trial identity")
    job = context.job
    target_env = {key: os.environ[ref.env] for key, ref in job.target.env.items()}
    validate_container_environment(target_env)
    stages = resolve_stages(job, context.users)
    for value in [*(s.duration for s in stages), job.drain, job.graceful_ramp_down]:
        if abs(value * 1000 - round(value * 1000)) > 1e-7:
            raise ValueError("k6 durations require exact millisecond precision")
    prepared_target = context.prepared_target or resolve_target(
        job.target, job.environment, snapshot_directory=directory / "build"
    )
    prepared_workload = context.prepared_workload or prepare_workload(
        job.workload, directory / "workload"
    )
    from .targets import tree_checksums

    if tree_checksums(prepared_workload.directory) != prepared_workload.checksums:
        raise ValueError("prepared workload checksum mismatch")
    bundle = bundle_workload(context, prepared_workload)
    generator_image_id = (
        context.generator_image_id
        or checked_record(
            json.loads(
                execute(
                    job.environment.generator, ["docker", "image", "inspect", "grafana/k6:1.4.2"]
                ).stdout
            ),
            list[DockerImage],
        )[0]["Id"]
    )
    execution_id = prepared_execution_identity(
        job,
        context.users,
        prepared_target,
        prepared_workload,
        generator_image_id=generator_image_id,
        fixture_manifest=context.fixture_manifest,
    )
    labels = owner_labels(context.queue_id, context.job_id, context.trial_id)
    if any(key.startswith("bench.") for key in job.target.labels):
        raise ValueError("target cannot override reserved owner labels")
    fixture_manifest = context.fixture_manifest
    manifest: TrialManifest = {
        "version": 1,
        "queue_id": context.queue_id,
        "trial_id": context.trial_id,
        "users": context.users,
        "job": job.model_dump(mode="json"),
        "execution_id": execution_id,
        "image_id": prepared_target.image_id,
        "generator_image_id": generator_image_id,
        "workload_identity": prepared_workload.identity,
        "fixture_manifest_sha256": hashlib.sha256(
            fixture_manifest.read_bytes()
        ).hexdigest()
        if fixture_manifest is not None
        else None,
        "wrapper_sha256": {
            p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in bundle.glob("*.js")
        },
        "colocated_generator": job.environment.backend == job.environment.generator,
    }
    if context.prepared_fixture is not None:
        manifest["prepared_fixture"] = {"manifest": str(context.prepared_fixture.manifest), "checksums": context.prepared_fixture.checksums}
    immutable_json(directory / "manifest.json", manifest)
    return Preparation(
        directory,
        prepared_target,
        prepared_workload,
        bundle,
        stages,
        generator_image_id,
        execution_id,
        labels,
        target_env,
    )
