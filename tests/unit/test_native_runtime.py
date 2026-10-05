"""Native runtime guards use the same fixture lifecycle without Python attestations."""

import copy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from bench.models import NativeCell, RuntimeCell
from bench.native_runtime import (
    LABEL_PREFIX,
    expected_image_id,
    load_runtime_cell,
    native_collector_hosts,
    native_image_metadata,
    native_preflight,
    validate_native_container,
)

IMAGE = "sha256:" + "a" * 64


def cell():
    return NativeCell(**json.loads(Path("runtimes/go/lock.json").read_text())["cells"][0])


def labels(c):
    return {
        LABEL_PREFIX + key.replace("_", "-"): str(getattr(c, key))
        for key in (
            "language",
            "runtime_version",
            "framework",
            "framework_version",
            "workers",
            "connections_per_worker",
        )
    }


def proof():
    c = cell()
    return {
        "id": "container-one",
        "image": IMAGE,
        "running": True,
        "nano_cpus": 2000000000,
        "memory": 3221225472,
        "memory_swap": 3221225472,
        "network_mode": "host",
        "run_id": "trial-fresh-nonce",
        "cell_id": c.id,
        "mounts": [{"Destination": "/certs/rds-ca.pem", "RW": False}],
    }


def test_native_registry_loads_six_fixed_topologies_and_python_is_unchanged():
    assert isinstance(load_runtime_cell("python-baseline-3.10.22"), RuntimeCell)
    for name, workers, connections in (
        ("java-spring-boot", 1, 20),
        ("php-laravel", 20, 1),
        ("go-net-http", 1, 20),
        ("rust-axum", 1, 20),
        ("node-express", 2, 10),
        ("bun-express", 2, 10),
    ):
        c = load_runtime_cell(name)
        assert isinstance(c, NativeCell)
        assert (c.workers, c.connections_per_worker, c.pool_budget) == (workers, connections, 20)


def test_native_cell_rejects_uncontrolled_connection_and_worker_counts():
    data = json.loads(Path("runtimes/go/lock.json").read_text())["cells"][0]
    for change in ({"workers": 2}, {"connections_per_worker": 10}, {"workers": True}):
        with pytest.raises(ValueError):
            NativeCell(**{**data, **change})


def test_python_selection_does_not_validate_unselected_native_registry(tmp_path):
    (tmp_path / "python").mkdir()
    (tmp_path / "go").mkdir()
    (tmp_path / "python/lock.json").write_text(Path("runtimes/python/lock.json").read_text())
    bad = json.loads(Path("runtimes/go/lock.json").read_text())
    bad["budget"]["cpus"] = 4
    (tmp_path / "go/lock.json").write_text(json.dumps(bad))
    assert isinstance(load_runtime_cell("python-baseline-3.10.22", tmp_path), RuntimeCell)
    with pytest.raises(ValueError, match="budget"):
        load_runtime_cell("go-net-http", tmp_path)


def test_native_expected_image_requires_an_exact_verified_digest():
    c = cell()
    for config in ({}, {"expected_image_ids": {c.id: "tag-only"}}):
        with pytest.raises(ValueError):
            expected_image_id(config, c)
    assert expected_image_id({"expected_image_ids": {c.id: IMAGE}}, c) == IMAGE


@pytest.mark.parametrize(
    "change",
    [
        {"image": "sha256:" + "b" * 64},
        {"running": False},
        {"nano_cpus": 4000000000},
        {"memory": 4294967296},
        {"memory_swap": -1},
        {"network_mode": "bridge"},
        {"run_id": "stale"},
        {"cell_id": "other"},
        {"mounts": [{"Destination": "/certs/rds-ca.pem", "RW": True}]},
    ],
)
def test_native_container_refuses_stale_identity_or_resource_drift(change):
    with pytest.raises(ValueError):
        validate_native_container({**proof(), **change}, cell(), "trial-fresh-nonce", IMAGE)


def test_native_container_and_image_manifest_only_keep_filtered_metadata():
    c = cell()
    assert validate_native_container(proof(), c, "trial-fresh-nonce", IMAGE)["image"] == IMAGE
    source = {
        "id": IMAGE,
        "os": "linux",
        "architecture": "amd64",
        "labels": labels(c),
        "environment": ["JWT_SECRET=private"],
        "extra_secret": "private",
    }
    metadata = native_image_metadata(source, c, IMAGE)
    assert "private" not in json.dumps(metadata)
    wrong = copy.deepcopy(source)
    wrong["labels"][LABEL_PREFIX + "workers"] = "4"
    with pytest.raises(ValueError):
        native_image_metadata(wrong, c, IMAGE)


def test_native_collector_omits_python_files_but_keeps_hosts():
    hosts = native_collector_hosts({"ssh": "backend", "runtime_dir": "/old"}, {"ssh": "load"})
    assert hosts == {"backend": {"ssh": "backend"}, "generator": {"ssh": "load"}}


def test_native_preflight_uses_health_and_filtered_inspect_without_python(monkeypatch):
    import bench.native_runtime as module

    c = cell()
    calls = []

    def execute(host, command, env=None, timeout=30):
        calls.append(command)
        if "ps" in command:
            return SimpleNamespace(stdout="container-one\n")
        if command[:3] == ["docker", "image", "inspect"]:
            return SimpleNamespace(
                stdout=json.dumps(
                    {"id": IMAGE, "os": "linux", "architecture": "amd64", "labels": labels(c)}
                )
            )
        return SimpleNamespace(stdout=json.dumps(proof()))

    monkeypatch.setattr(module, "execute", execute)
    monkeypatch.setattr(module, "wait_native_ready", lambda *args, **kwargs: None)
    metadata = native_preflight(
        {},
        c,
        ["docker", "compose", "-f", "compose.native.aws.yaml"],
        {},
        "http://backend:8000",
        "trial-fresh-nonce",
        IMAGE,
    )
    assert metadata["container"]["run_id"] == "trial-fresh-nonce"
    assert all("python3" not in command for command in calls)
    assert all(".Config.Env" not in " ".join(command) for command in calls)


def test_native_missing_image_builds_with_its_dockerfile_on_generator(tmp_path, monkeypatch):
    from bench import images

    backend = {"ssh": "backend", "project": "/srv/project"}
    build = {"ssh": "generator", "project": "/srv/project"}
    back_ids, build_ids = iter([None, IMAGE]), iter([None, IMAGE])
    calls = []
    monkeypatch.setattr(
        images, "image_id", lambda host, _: next(back_ids if host == backend else build_ids)
    )

    def execute(host, command, *args, **kwargs):
        calls.append((host, command))
        return SimpleNamespace(stdout="build output", stderr="")

    monkeypatch.setattr(images, "execute", execute)
    monkeypatch.setattr(images, "copy_from", lambda host, source, dest: dest.write_text("archive"))
    monkeypatch.setattr(images, "copy_to", lambda *_: None)
    c = cell()
    images.ensure_backend_image(
        {
            "backend_host": backend,
            "load_host": build,
            "auto_build_images": True,
            "expected_image_ids": {c.id: IMAGE},
        },
        c,
        tmp_path,
    )
    assert (build, ["docker", "build", "-f", c.dockerfile, "-t", c.image, "."]) in calls
    assert not any("runtimes/python/build.py" in cmd for _, cmd in calls)


def test_existing_native_image_is_rejected_when_digest_changes(tmp_path, monkeypatch):
    from bench import images

    c = cell()
    monkeypatch.setattr(images, "image_id", lambda *_: "sha256:" + "b" * 64)
    with pytest.raises(RuntimeError, match="verified native digest"):
        images.ensure_backend_image({"expected_image_ids": {c.id: IMAGE}}, c, tmp_path)


def test_native_ready_probe_retries_bad_payload_and_status(monkeypatch):
    import bench.native_runtime as module

    replies = iter(
        [
            SimpleNamespace(status_code=503, json=lambda: {}),
            SimpleNamespace(status_code=200, json=lambda: {"status": "wrong"}),
            SimpleNamespace(status_code=200, json=lambda: {"status": "ok"}),
        ]
    )

    class Client:
        def __init__(self, **_):
            self.urls = []

        def __enter__(self):
            return self

        def __exit__(self, *_):
            pass

        def get(self, url):
            assert url == "http://monitor-private:8000/health/ready"
            return next(replies)

    monkeypatch.setattr(module.httpx, "Client", Client)
    monkeypatch.setattr(module.time, "sleep", lambda _: None)
    module.wait_native_ready("http://monitor-private:8000/", timeout=120)


def test_native_trial_keeps_fixture_lifecycle_and_never_reads_python_workers(tmp_path, monkeypatch):
    from bench import images, runner

    fixture = tmp_path / "fixture.json"
    fixture.write_text("{}")
    c = cell()
    config = {
        "mode": "local",
        "operation": "user-journey",
        "fixture": str(fixture),
        "database_allowlist": "language_benchmark_test",
        "credentials": "unused",
        "warmup_seconds": 0,
        "measure_seconds": 1,
        "ramp_seconds": 1,
        "settle_seconds": 0,
        "hold_seconds": 1,
        "wait_min": 0,
        "wait_max": 1,
        "timeout_seconds": 1,
        "expected_image_ids": {c.id: IMAGE},
    }
    trial = {"id": "native-trial", "cell": c.id, "users": 1, "campaign_id": "campaign"}
    monkeypatch.setenv(
        "DATABASE_URL", "postgresql://postgres:unused@localhost/language_benchmark_test"
    )
    monkeypatch.setenv("JWT_SECRET", "test-secret-that-has-at-least-32-bytes")
    calls, resets = [], []
    monkeypatch.setattr(runner, "collect_manifest", lambda *_: {"label": "LOCAL SMOKE"})
    monkeypatch.setattr(runner, "restore_fixture", lambda *_: resets.append("reset"))
    monkeypatch.setattr(images, "ensure_backend_image", lambda *_: None)

    def execute(host, command, env=None, **kwargs):
        calls.append(command)
        if env and "INPUTS" in env and "OUTPUTS" in env:
            return SimpleNamespace(stdout="1000:1000", stderr="")
        return SimpleNamespace(stdout="", stderr="")

    monkeypatch.setattr(runner, "execute", execute)
    monkeypatch.setattr(runner, "native_preflight", lambda *_: {"image": {"id": IMAGE}})
    monkeypatch.setattr(
        runner,
        "validate_worker_snapshots",
        lambda *_: pytest.fail("Python worker preflight on native cell"),
    )
    monkeypatch.setattr(
        runner,
        "validate_dependency_inventory",
        lambda *_: pytest.fail("Python dependencies checked on native cell"),
    )
    monkeypatch.setattr(runner, "prepare_journey_data", lambda _, __, path: path.write_text("{}"))
    monkeypatch.setattr(runner, "read_journey_metrics", lambda *_: {})
    monkeypatch.setattr(runner, "copy_to", lambda *_: None)
    monkeypatch.setattr(runner, "copy_from", lambda _, __, target: target.mkdir(exist_ok=True))

    class Client:
        def __init__(self, **_):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_):
            pass

        def get(self, *_):
            return SimpleNamespace(raise_for_status=lambda: None)

    monkeypatch.setattr(runner.httpx, "Client", Client)

    class Collector:
        errors = []

        def __init__(self, hosts, *_):
            assert "runtime_dir" not in hosts["backend"]
            assert "generator" in hosts

        def __enter__(self):
            return self

        def __exit__(self, *_):
            pass

    monkeypatch.setattr(runner, "Collector", Collector)
    directory = tmp_path / "trial"
    result = runner.run_trial(config, trial, directory)
    assert result["status"] == "measured" and result["reset_verified"]
    assert resets == ["reset", "reset"]
    assert any(
        "compose.native.aws.yaml" in command and "--force-recreate" in command for command in calls
    )
    manifest = json.loads((directory / "manifest.json").read_text())
    assert manifest["runtime_lock"]["path"] == "runtimes/go/lock.json"
    assert "workers" not in manifest and manifest["image"]["id"] == IMAGE
