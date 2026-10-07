"""Build missing locked images away from the measured backend, then verify transfer."""

import tempfile
from collections.abc import Mapping
from pathlib import Path

from .models import NativeCell, RuntimeCell
from .native_runtime import expected_image_id
from .records import Campaign
from .transport import CommandFailure, copy_from, copy_to, execute


def image_id(host: Mapping[str, str], image: str) -> str | None:
    try:
        return execute(
            host, ["docker", "image", "inspect", "--format", "{{.Id}}", image], timeout=30
        ).stdout.strip()
    except CommandFailure as error:
        if "No such image" in error.stderr:
            return None
        raise


def ensure_backend_image(config: Campaign, cell: RuntimeCell | NativeCell, directory: Path) -> None:
    backend = config.get("backend_host", {})
    native_expected = expected_image_id(config, cell) if isinstance(cell, NativeCell) else None
    existing = image_id(backend, cell.image)
    if existing:
        if native_expected and existing != native_expected:
            raise RuntimeError("backend image differs from the verified native digest")
        return
    if not config.get("auto_build_images", False):
        raise RuntimeError("backend image is missing: " + cell.image)
    build = config.get("build_host", config.get("load_host", {}))
    if build == backend or (build.get("ssh") and build.get("ssh") == backend.get("ssh")):
        raise ValueError("build host must be separate from the measured backend")
    if not image_id(build, cell.image):
        if isinstance(cell, NativeCell):
            command = ["docker", "build", "-f", cell.dockerfile, "-t", cell.image, "."]
        else:
            command = [
                config.get("build_uv_path", "uv"),
                "run",
                "python",
                "runtimes/python/build.py",
                "--version",
                cell.python,
                "--mode",
                cell.build,
                "--apps",
                "--jobs",
                "8",
            ]
        try:
            result = execute(build, command, timeout=14400)
        except CommandFailure as error:
            (directory / "image-build.log").write_text(error.stdout + error.stderr)
            raise
        (directory / "image-build.log").write_text(result.stdout + result.stderr)
    expected = image_id(build, cell.image)
    if not expected:
        raise RuntimeError("build did not produce the requested locked image")
    if native_expected and expected != native_expected:
        raise RuntimeError(
            "native build image requires verification; digest differs from expected_image_ids"
        )
    build_dir = str(Path(build["project"]) / "artifacts/images")
    backend_dir = str(Path(backend["project"]) / "artifacts/images")
    archive_name = cell.id + ".tar"
    source = build_dir + "/" + archive_name
    destination = backend_dir + "/" + archive_name
    for host, path in ((build, build_dir), (backend, backend_dir)):
        execute(host, ["mkdir", "-p", path])
    try:
        execute(build, ["docker", "save", "--output", source, cell.image], timeout=1200)
        with tempfile.TemporaryDirectory(prefix="lb-image-") as folder:
            local = Path(folder) / archive_name
            copy_from(build, source, local)
            copy_to(backend, local, destination)
        execute(backend, ["docker", "load", "--input", destination], timeout=1200)
        if image_id(backend, cell.image) != expected:
            raise RuntimeError("deployed image does not match build host digest")
    finally:
        for host, archive in ((build, source), (backend, destination)):
            execute(host, ["rm", "-f", archive], timeout=30)


def verify_backend_limits(
    backend: Mapping[str, str], compose: list[str], env: Mapping[str, object]
) -> None:
    ids = execute(backend, compose + ["ps", "--quiet"], env, timeout=30).stdout.split()
    if len(ids) != 1:
        raise RuntimeError("exactly one measured backend container required")
    limits = execute(
        backend,
        [
            "docker",
            "inspect",
            "--format",
            "{{.HostConfig.NanoCpus}} {{.HostConfig.Memory}} {{.HostConfig.MemorySwap}}",
            ids[0],
        ],
        timeout=30,
    ).stdout.split()
    if list(map(int, limits)) != [2000000000, 3221225472, 3221225472]:
        raise RuntimeError("backend must enforce two CPUs, 3 GiB RAM and no swap")
