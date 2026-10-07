"""Docker target preparation and checked argv transport for local/SSH Linux hosts."""

import hashlib
import json
import os
import shlex
import shutil
import stat
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Protocol
from uuid import uuid4

from ..records import Ulimit, checked_record
from .records import BuildManifest, CommandResponse, ContainerInfo, DockerImage

if TYPE_CHECKING:
    from .workloads import PreparedWorkload

from .resources import wrapper_path
from .types import Build, Environment, Host, ResolvedJob, Target


@dataclass(frozen=True)
class BuildSnapshot:
    context: Path
    dockerfile: Path
    checksums: dict[str, str]
    identity: str
    source_dockerfile_hash: str
    build_args_hash: str


@dataclass(frozen=True)
class ResolvedTarget:
    spec: Target
    requested_image: str | None
    image_id: str
    repo_digests: tuple[str, ...]
    build_snapshot: BuildSnapshot | None = None

    @property
    def deployment_image(self) -> str:
        return self.image_id


@dataclass(frozen=True)
class Endpoints:
    readiness_url: str
    generator_url: str


# The remote child receives argv/environment exclusively through SSH stdin.
_REMOTE = """import json,os,subprocess,sys
p=json.load(sys.stdin)
try:
 r=subprocess.run(p['argv'],input=p.get('stdin'),cwd=p['cwd'],env={**os.environ,**p['env']},capture_output=True,text=True,timeout=p['timeout'])
 print(json.dumps({'returncode':r.returncode,'stdout':r.stdout,'stderr':r.stderr}))
except subprocess.TimeoutExpired:
 print(json.dumps({'timeout':True}))
"""


def execute_host(
    host: Host,
    argv: list[str],
    *,
    env: dict[str, str] | None = None,
    timeout: float = 1800,
    input_data: str | None = None,
) -> subprocess.CompletedProcess[str]:
    if not argv or any(not isinstance(v, str) for v in argv):
        raise ValueError("command must be a nonempty argv list")
    supplied = {str(k): str(v) for k, v in (env or {}).items()}
    cwd = str(host.workdir) if host.workdir else None
    try:
        if host.transport == "ssh":
            command = ["ssh", "-o", "BatchMode=yes", "-p", str(host.port)]
            if host.identity_file:
                command += ["-i", str(host.identity_file)]
            assert host.host is not None
            address = f"{host.user}@{host.host}" if host.user else host.host
            command += ["--", address, "python3 -c " + shlex.quote(_REMOTE)]
            outer = subprocess.run(
                command,
                input=json.dumps(
                    {
                        "argv": argv,
                        "env": supplied,
                        "cwd": cwd,
                        "timeout": timeout,
                        "stdin": input_data,
                    }
                ),
                text=True,
                capture_output=True,
                timeout=timeout + 10,
            )
            if outer.returncode:
                raise RuntimeError("SSH command transport failed")
            try:
                payload = checked_record(json.loads(outer.stdout), CommandResponse)
            except (ValueError, TypeError):
                raise RuntimeError("SSH command response incomplete") from None
            if payload.get("timeout"):
                raise TimeoutError("command timed out")
            result = subprocess.CompletedProcess(
                argv, payload["returncode"], payload["stdout"], payload["stderr"]
            )
        else:
            result = subprocess.run(
                argv,
                input=input_data,
                cwd=cwd,
                env={**os.environ, **supplied},
                capture_output=True,
                text=True,
                timeout=timeout,
            )
    except subprocess.TimeoutExpired:
        raise TimeoutError("command timed out") from None
    if result.returncode:
        # stdout/stderr may contain resolved environment credentials.
        raise RuntimeError(f"command failed (exit {result.returncode})")
    return result


def tree_checksums(root: Path) -> dict[str, str]:
    """Bind Docker-relevant input type, POSIX mode, content and symlink target."""
    result = {}
    for path in sorted(root.rglob("*")):
        mode = stat.S_IMODE(path.lstat().st_mode)
        if path.is_symlink():
            try:
                path.resolve().relative_to(root.resolve())
            except ValueError:
                raise ValueError("input symlink escapes bundle") from None
            content = ("symlink:" + str(mode) + ":" + os.readlink(path)).encode()
        elif path.is_file():
            content = ("file:" + str(mode) + ":").encode() + path.read_bytes()
        elif path.is_dir():
            content = ("directory:" + str(mode) + ":").encode()
        else:
            raise ValueError("unsupported input file type")
        result[path.relative_to(root).as_posix()] = hashlib.sha256(content).hexdigest()
    return result


def snapshot_build(build: Build, destination: Path) -> BuildSnapshot:
    """Export COPY's input using native BuildKit Docker ignore matching.

    The helper and export live outside the user's source; effective Dockerfile-specific
    ignore rules become the isolated context's .dockerignore. No homemade glob matcher.
    """
    source = build.context.resolve()
    destination = destination.resolve()
    if not source.is_dir():
        raise ValueError("build context is not a directory")
    if destination == source or source in destination.parents:
        raise ValueError("snapshot must live outside build context")
    if destination.exists():
        raise ValueError("snapshot destination already exists")
    dockerfile = (build.dockerfile or source / "Dockerfile").resolve()
    if not dockerfile.is_file():
        raise ValueError("build Dockerfile missing")
    dockerfile_bytes = dockerfile.read_bytes()
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="bench-build-", dir=destination.parent) as temporary:
        temp = Path(temporary)
        context = temp / "source"
        shutil.copytree(source, context, symlinks=True)
        effective = Path(str(dockerfile) + ".dockerignore")
        if not effective.exists():
            effective = source / ".dockerignore"
        helper = temp / "snapshot.Dockerfile"
        helper.write_text("FROM scratch\nCOPY . /\n")
        Path(str(helper) + ".dockerignore").write_bytes(
            effective.read_bytes() if effective.exists() else b""
        )
        output = temp / "prepared"
        output.mkdir()
        execute_host(
            Host(transport="local"),
            [
                "docker",
                "buildx",
                "build",
                "--network=none",
                "--file",
                str(helper),
                "--output",
                f"type=local,dest={output}",
                str(context),
            ],
        )
        # Dockerfile stays separate so original exclusions never prevent its use.
        original = temp / "Dockerfile.original"
        original.write_bytes(dockerfile_bytes)
        Path(str(original) + ".dockerignore").write_bytes(b"")
        checksums = tree_checksums(output)
        args_hash = hashlib.sha256(json.dumps(build.args, sort_keys=True).encode()).hexdigest()
        digest = hashlib.sha256(
            json.dumps(
                {
                    "files": checksums,
                    "dockerfile": hashlib.sha256(dockerfile_bytes).hexdigest(),
                    "args_hash": args_hash,
                },
                sort_keys=True,
            ).encode()
        ).hexdigest()
        receipt = temp / "receipt"
        receipt.mkdir()
        shutil.move(str(output), receipt / "context")
        shutil.copy2(original, receipt / "Dockerfile.original")
        (receipt / "Dockerfile.original.dockerignore").write_bytes(b"")
        (receipt / "manifest.json").write_text(
            json.dumps(
                {
                    "identity": digest,
                    "checksums": checksums,
                    "dockerfile_sha256": hashlib.sha256(dockerfile_bytes).hexdigest(),
                    "build_args_sha256": args_hash,
                },
                sort_keys=True,
            )
        )
        os.rename(receipt, destination)
    return BuildSnapshot(
        destination / "context",
        destination / "Dockerfile.original",
        checksums,
        digest,
        hashlib.sha256(dockerfile_bytes).hexdigest(),
        args_hash,
    )


def resolve_target(
    target: Target, environment: Environment, *, snapshot_directory: Path | None = None
) -> ResolvedTarget:
    host = environment.build or environment.backend
    snapshot = None
    image = target.image
    if target.build:
        if snapshot_directory is None:
            snapshot_directory = Path(tempfile.mkdtemp(prefix="bench-input-parent-")) / "snapshot"
        snapshot = snapshot_build(target.build, snapshot_directory)
        if host.transport != "local":
            from .workloads import transfer_tree

            remote_root = str((host.workdir or Path("/tmp")) / ("bench-build-" + uuid4().hex))
            transfer_tree(snapshot.context.parent, host, remote_root)
            context = remote_root + "/context"
            dockerfile = remote_root + "/Dockerfile.original"
        else:
            context = str(snapshot.context)
            dockerfile = str(snapshot.dockerfile)
        image = "bench-prepared:" + uuid4().hex
        argv = ["docker", "build", "--file", dockerfile, "--tag", image]
        for key, value in target.build.args.items():
            argv += ["--build-arg", f"{key}={value}"]
        execute_host(host, [*argv, context])
    assert image is not None
    try:
        result = execute_host(host, ["docker", "image", "inspect", image])
    except RuntimeError:
        if target.build:
            raise
        execute_host(host, ["docker", "pull", image])
        result = execute_host(host, ["docker", "image", "inspect", image])
    info = checked_record(json.loads(result.stdout), list[DockerImage])[0]
    image_id = info.get("Id", "")
    if not image_id.startswith("sha256:"):
        raise ValueError("Docker did not return immutable image ID")
    # A separate build host must deliver the pinned binary to the backend.
    if host != environment.backend:
        from .workloads import fetch_file, transfer_file

        archive = Path(tempfile.mkdtemp(prefix="bench-image-")) / "image.tar"
        try:
            if host.transport == "local":
                execute_host(host, ["docker", "save", "--output", str(archive), image_id])
            else:
                export_path = str(
                    (host.workdir or Path("/tmp")) / ("bench-export-" + uuid4().hex + ".tar")
                )
                try:
                    execute_host(host, ["docker", "save", "--output", export_path, image_id])
                    fetch_file(host, export_path, archive)
                finally:
                    execute_host(host, ["rm", "-f", "--", export_path])
            remote = str(
                (environment.backend.workdir or Path("/tmp"))
                / ("bench-image-" + uuid4().hex + ".tar")
            )
            transfer_file(archive, environment.backend, remote)
            try:
                execute_host(environment.backend, ["docker", "load", "--input", remote])
            finally:
                execute_host(environment.backend, ["rm", "-f", "--", remote])
            loaded = checked_record(
                json.loads(
                    execute_host(
                        environment.backend, ["docker", "image", "inspect", image_id]
                    ).stdout
                ),
                list[DockerImage],
            )[0]
            if loaded["Id"] != image_id:
                raise ValueError("loaded image identity mismatch")
        finally:
            shutil.rmtree(archive.parent)
    return ResolvedTarget(
        target, target.image, image_id, tuple(info.get("RepoDigests") or ()), snapshot
    )


def resolve_endpoints(
    target: Target, environment: Environment, *, host_port: int, generator_host: str | None = None
) -> Endpoints:
    generator = environment.network.backend_url
    if generator is None:
        host = generator_host
        if host is None:
            if environment.generator == environment.backend:
                host = "127.0.0.1"
            elif environment.backend.transport == "ssh":
                host = environment.backend.host
            else:
                raise ValueError("generator-reachable backend address required")
        generator = f"http://{host}:{host_port}"
    return Endpoints(f"http://127.0.0.1:{host_port}{target.readiness.path}", generator.rstrip("/"))


def verify_resources(target: Target, inspection: ContainerInfo) -> None:
    actual = inspection["HostConfig"]
    wanted = target.resources
    if actual.get("NanoCpus") != round(wanted.cpus * 1_000_000_000):
        raise ValueError("container CPU limit mismatch")
    if actual.get("Memory") != int(wanted.memory):
        raise ValueError("container memory limit mismatch")
    if actual.get("MemorySwap") != int(wanted.memory + wanted.swap):
        raise ValueError("container swap limit mismatch")
    nofile: Ulimit = next(
        (v for v in (actual.get("Ulimits") or []) if v["Name"] == "nofile"), Ulimit()
    )
    if nofile.get("Soft") != wanted.nofile or nofile.get("Hard") != wanted.nofile:
        raise ValueError("container nofile limit mismatch")


def owner_labels(queue_id: str, job_id: str, trial_id: str) -> dict[str, str]:
    return {"bench.queue": queue_id, "bench.job": job_id, "bench.trial": trial_id}


def wait_ready(target: Target, host: Host, url: str) -> subprocess.CompletedProcess[str]:
    deadline = time.monotonic() + target.readiness.timeout
    while time.monotonic() < deadline:
        remaining = deadline - time.monotonic()
        try:
            result = execute_host(
                host,
                [
                    "python3",
                    "-c",
                    "import urllib.request,urllib.error,sys\ntry: r=urllib.request.urlopen(sys.argv[1],timeout=float(sys.argv[2]))\nexcept urllib.error.HTTPError as e: r=e\nsys.exit(0 if r.status==int(sys.argv[3]) else 1)",
                    url,
                    str(min(remaining, 5)),
                    str(target.readiness.status),
                ],
                timeout=min(remaining, 6),
            )
            return result
        except (RuntimeError, TimeoutError):
            time.sleep(min(target.readiness.interval, max(0, deadline - time.monotonic())))
    raise TimeoutError("target readiness timed out")


def load_build_snapshot(directory: Path) -> BuildSnapshot:
    """Read retained preparation evidence and fail closed on mutation at resume."""
    directory = directory.resolve()
    manifest = checked_record(json.loads((directory / "manifest.json").read_text()), BuildManifest)
    checksums = tree_checksums(directory / "context")
    dockerfile = directory / "Dockerfile.original"
    dockerfile_hash = hashlib.sha256(dockerfile.read_bytes()).hexdigest()
    if checksums != manifest["checksums"] or dockerfile_hash != manifest["dockerfile_sha256"]:
        raise ValueError("build snapshot checksum mismatch")
    args_hash = manifest["build_args_sha256"]
    identity = hashlib.sha256(
        json.dumps(
            {"files": checksums, "dockerfile": dockerfile_hash, "args_hash": args_hash},
            sort_keys=True,
        ).encode()
    ).hexdigest()
    if identity != manifest["identity"]:
        raise ValueError("build snapshot identity mismatch")
    return BuildSnapshot(
        directory / "context", dockerfile, checksums, identity, dockerfile_hash, args_hash
    )


def prepared_execution_identity(
    job: ResolvedJob,
    users: int | None,
    resolved_target: ResolvedTarget,
    prepared_workload: "PreparedWorkload",
    *,
    generator_image_id: str | None = None,
) -> str:
    """Canonical identity from preparation receipts, independent of mutable source."""
    from .evidence import execution_identity

    return execution_identity(
        job,
        users,
        provenance={
            "image_id": resolved_target.image_id,
            "generator_image_id": generator_image_id,
            "generator_version": "grafana/k6:1.4.2",
            "fixture_manifest_sha256": hashlib.sha256(
                job.workload.fixture.manifest.read_bytes()
            ).hexdigest()
            if job.workload.fixture and job.workload.fixture.manifest
            else None,
            "wrapper_sha256": {
                name: hashlib.sha256((wrapper_path(name)).read_bytes()).hexdigest()
                for name in ("runner.js", "metrics.js")
            },
            "build_input_identity": resolved_target.build_snapshot.identity
            if resolved_target.build_snapshot
            else None,
            "workload_identity": prepared_workload.identity,
        },
    )


class HostExecutor(Protocol):
    def __call__(
        self,
        host: Host,
        argv: list[str],
        *,
        env: dict[str, str] | None = None,
        timeout: float = 1800,
        input_data: str | None = None,
    ) -> subprocess.CompletedProcess[str]: ...
