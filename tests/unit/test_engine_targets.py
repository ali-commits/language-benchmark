import json
import subprocess
from pathlib import Path

import pytest

from tools.bench.engine.targets import (
    execute_host,
    resolve_endpoints,
    resolve_target,
    snapshot_build,
    verify_resources,
)
from tools.bench.engine.types import Build, Environment, Host, Target


def environment():
    return Environment(backend=Host(transport="local"), generator=Host(transport="local"))


@pytest.mark.parametrize("image", ["vendor/arbitrary:v1", "vendor/arbitrary:v2"])
def test_image_is_pinned(monkeypatch, image):
    calls = []

    def execute(host, argv, **kwargs):
        calls.append(argv)
        return subprocess.CompletedProcess(
            argv,
            0,
            json.dumps(
                [{"Id": "sha256:immutable", "RepoDigests": ["vendor/arbitrary@sha256:digest"]}]
            ),
            "",
        )

    monkeypatch.setattr("tools.bench.engine.targets.execute_host", execute)
    resolved = resolve_target(Target(image=image), environment())
    assert resolved.image_id == "sha256:immutable"
    assert resolved.requested_image == image
    assert resolved.repo_digests == ("vendor/arbitrary@sha256:digest",)
    # Deployment consumes image_id; moving the tag cannot change this receipt.
    assert resolved.deployment_image == resolved.image_id


def test_remote_argv_has_no_shell_interpolation(monkeypatch):
    captured = {}

    def run(argv, **kwargs):
        captured.update(argv=argv, **kwargs)
        return subprocess.CompletedProcess(
            argv, 0, json.dumps({"returncode": 0, "stdout": "ok", "stderr": ""}), ""
        )

    monkeypatch.setattr(subprocess, "run", run)
    execute_host(
        Host(transport="ssh", host="example", user="user", port=2222, workdir=Path("/space dir")),
        ["printf", "$(secret); space"],
        env={"X": "secret"},
    )
    payload = json.loads(captured["input"])
    assert payload["argv"] == ["printf", "$(secret); space"]
    assert payload["cwd"] == "/space dir"
    assert "secret" not in " ".join(captured["argv"])
    assert "2222" in captured["argv"]


def test_invalid_build_context(tmp_path):
    with pytest.raises(ValueError, match="context"):
        snapshot_build(Build(context=tmp_path / "missing"), tmp_path / "snapshot")


def test_resources_and_endpoints():
    target = Target(image="api:1")
    inspect = {
        "HostConfig": {
            "NanoCpus": 2000000000,
            "Memory": 3221225472,
            "MemorySwap": 3221225472,
            "Ulimits": [{"Name": "nofile", "Soft": 250000, "Hard": 250000}],
        }
    }
    verify_resources(target, inspect)
    inspect["HostConfig"]["NanoCpus"] = 1000000000
    with pytest.raises(ValueError):
        verify_resources(target, inspect)
    endpoints = resolve_endpoints(
        target, environment(), host_port=12345, generator_host="backend.internal"
    )
    assert endpoints.readiness_url == "http://127.0.0.1:12345/health"
    assert endpoints.generator_url == "http://backend.internal:12345"


def test_native_snapshot_matches_dockerfile_specific_ignore_and_freezes_inputs(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    (source / "Dockerfile").write_text("FROM scratch\nCOPY . /\n")
    (source / ".dockerignore").write_text("keep.txt\n")
    (source / "Dockerfile.dockerignore").write_text("drop.txt\n!keep.txt\n.git\n")
    (source / "keep.txt").write_text("kept")
    (source / "drop.txt").write_text("dropped")
    snapshot = snapshot_build(Build(context=source), tmp_path / "snapshot")
    assert (snapshot.context / "keep.txt").read_text() == "kept"
    assert not (snapshot.context / "drop.txt").exists()
    assert (snapshot.context / ".dockerignore").read_text() == "keep.txt\n"
    before = snapshot.identity
    (source / "keep.txt").write_text("changed runtime")
    assert snapshot.identity == before
    assert (snapshot.context / "keep.txt").read_text() == "kept"


def test_readiness_declared_status_and_timeout(monkeypatch):
    from tools.bench.engine.targets import wait_ready
    from tools.bench.engine.types import Readiness

    seen = []

    def execute(host, argv, **kwargs):
        seen.append(argv)
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr("tools.bench.engine.targets.execute_host", execute)
    wait_ready(
        Target(image="api", readiness=Readiness(path="/ready", status=204, timeout=0.1)),
        Host(transport="local"),
        "http://localhost/ready",
    )
    assert seen[0][-1] == "204"

    def failing(*args, **kwargs):
        raise RuntimeError("not ready")

    monkeypatch.setattr("tools.bench.engine.targets.execute_host", failing)
    with pytest.raises(TimeoutError):
        wait_ready(
            Target(image="api", readiness=Readiness(timeout=0.01, interval=0.01)),
            Host(transport="local"),
            "http://localhost",
        )


def test_readiness_accepts_declared_error_http_status():
    from http.server import BaseHTTPRequestHandler, HTTPServer
    from threading import Thread

    from tools.bench.engine.targets import wait_ready
    from tools.bench.engine.types import Readiness

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(418)
            self.end_headers()

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        wait_ready(
            Target(image="api", readiness=Readiness(status=418, timeout=0.2, interval=0.01)),
            Host(transport="local"),
            f"http://127.0.0.1:{server.server_port}/ready",
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def test_snapshot_resume_detects_mutation(tmp_path):
    from tools.bench.engine.targets import load_build_snapshot

    source = tmp_path / "source"
    source.mkdir()
    (source / "Dockerfile").write_text("FROM scratch\nCOPY . /\n")
    (source / "a").write_text("original")
    receipt = snapshot_build(Build(context=source, args={"A": "B"}), tmp_path / "snapshot")
    assert load_build_snapshot(tmp_path / "snapshot").identity == receipt.identity
    (receipt.context / "a").write_text("changed")
    with pytest.raises(ValueError, match="checksum"):
        load_build_snapshot(tmp_path / "snapshot")


def test_prepared_identity_survives_source_mutation_and_tracks_image(tmp_path):
    from tools.bench.engine.targets import ResolvedTarget, prepared_execution_identity
    from tools.bench.engine.types import ResolvedJob, Stage, TestPolicy, Workload
    from tools.bench.engine.workloads import prepare_workload

    source = tmp_path / "main.js"
    source.write_text("export default function() {}")
    workload = Workload(script=source, metrics={})
    target = Target(image="mutable:tag")
    env = environment()
    job = ResolvedJob(
        id="job",
        target_name="api",
        target=target,
        workload_name="load",
        workload=workload,
        environment_name="local",
        environment=env,
        test=TestPolicy(type="fixed", users=10),
        stages=[
            Stage(id="hold", from_users="target", to_users="target", duration=1, evaluate=True)
        ],
        criteria=[],
    )
    prepared = prepare_workload(workload, tmp_path / "bundle")
    resolved = ResolvedTarget(target, target.image, "sha256:original", ())
    before = prepared_execution_identity(job, 10, resolved, prepared)
    source.unlink()
    assert prepared_execution_identity(job, 10, resolved, prepared) == before
    changed = ResolvedTarget(target, target.image, "sha256:changed", ())
    assert prepared_execution_identity(job, 10, changed, prepared) != before
