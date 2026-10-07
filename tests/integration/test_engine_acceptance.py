"""Opt-in disposable Docker/SSH acceptance through the public CLI."""

import copy
import hashlib
import json
import os
import pwd
import shutil
import socket
import subprocess
import sys
import time
import uuid
from collections import Counter
from pathlib import Path

import pytest
import yaml

from tools.bench.engine.config import load_config, resolve_jobs
from tools.bench.engine.trial import TrialContext, run_trial
from tools.bench.engine.types import Host, TrialEvidence
from tools.bench.queue.store import QueueStore

pytestmark = pytest.mark.skipif(
    os.environ.get("BENCH_INTEGRATION") != "1", reason="opt-in disposable Docker acceptance"
)
ROOT = Path(__file__).resolve().parents[2]


def command(*args, cwd=ROOT, env=None, timeout=180):
    return subprocess.run(
        list(map(str, args)),
        cwd=cwd,
        env=env,
        check=True,
        capture_output=True,
        text=True,
        timeout=timeout,
    ).stdout


def cli(*args, **kwargs):
    return command(sys.executable, "-m", "tools.bench.commands", *args, **kwargs)


def docker(*args):
    return command("docker", *args).strip()


def until(predicate, timeout=90):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(0.1)
    pytest.fail("acceptance condition did not become true")


@pytest.fixture
def config_file(tmp_path):
    path = tmp_path / "inputs with spaces" / "queue.yaml"
    cli("init", "--template", "http", "--output", path)
    data = yaml.safe_load(path.read_text())
    data["name"] = "acceptance-" + uuid.uuid4().hex[:12]
    data["defaults"].update(
        {
            "drain": "0.2s",
            "graceful_ramp_down": "0.2s",
            "stages": [
                {"id": "hold", "from": "target", "to": "target", "duration": "4s", "evaluate": True}
            ],
        }
    )
    data["environments"]["local"]["telemetry"]["interval"] = "0.2s"
    data["jobs"] = [data["jobs"][0]]
    data["jobs"][0]["test"]["users"] = [1]
    path.write_text(yaml.safe_dump(data))
    return path, data


def results(store, queue):
    return [
        TrialEvidence.model_validate_json((Path(row["path"]) / "result.json").read_text())
        for row in store.snapshot(queue)["trials"]
        if (Path(row["path"]) / "result.json").exists()
    ]


def assert_clean(queue):
    assert not docker("ps", "-aq", "--filter", "label=bench.queue=" + queue)
    assert not docker("network", "ls", "-q", "--filter", "label=bench.queue=" + queue)


def cleanup_owned_queue(queue):
    """Best-effort test cleanup; recheck ownership and remove immutable IDs only."""
    errors = []
    for kind, listing in (
        ("container", ("ps", "-aq", "--no-trunc")),
        ("network", ("network", "ls", "-q", "--no-trunc")),
    ):
        try:
            identifiers = docker(*listing, "--filter", "label=bench.queue=" + queue).split()
        except (OSError, subprocess.SubprocessError) as exc:
            errors.append(type(exc).__name__)
            continue
        for identifier in identifiers:
            try:
                info = json.loads(docker(kind, "inspect", identifier))[0]
                labels = (
                    info["Config"].get("Labels", {})
                    if kind == "container"
                    else info.get("Labels", {})
                )
                if info["Id"] != identifier or labels.get("bench.queue") != queue:
                    errors.append("ownership changed; resource preserved")
                    continue
                docker(kind, "rm", *(["-f"] if kind == "container" else []), identifier)
            except (OSError, subprocess.SubprocessError, ValueError, KeyError) as exc:
                errors.append(type(exc).__name__)
    return errors


def finish_cleanup(errors):
    """Keep the original test failure primary if cleanup also encounters trouble."""
    if not errors:
        return
    message = "acceptance cleanup errors: " + ", ".join(errors)
    active = sys.exc_info()[1]
    if active is None:
        pytest.fail(message)
    elif hasattr(active, "add_note"):
        active.add_note(message)


def test_two_versions_fixed_stages_metric_search(config_file, tmp_path):
    path, data = config_file
    target = copy.deepcopy(data["targets"]["http"])
    # Distinct immutable images, deliberately unrelated to the language registry.
    source = path.parent / target["build"]["context"]
    tags = []
    image_ids = {}
    foreign = None
    foreign_queue = data["name"] + "-sentinel"
    try:
        for version in ("orchid", "copper"):
            tag = f"bench-acceptance-{uuid.uuid4().hex[:12]}:{version}"
            docker("build", "--label", "acceptance.version=" + version, "-t", tag, source)
            tags.append(tag)
            image_ids[version] = docker("image", "inspect", tag, "--format", "{{.Id}}")
        assert image_ids["orchid"] != image_ids["copper"]
        data["targets"] = {
            version: {**{k: v for k, v in target.items() if k != "build"}, "image": tag}
            for version, tag in zip(("orchid", "copper"), tags, strict=True)
        }
        # Actual workload events encode the selected population as a deterministic metric.
        script = path.parent / data["workloads"]["http"]["script"]
        script.write_text(
            script.read_text().replace(
                "visits += 1;",
                "visits += 1;\n  context.record('population', {value: __VU, unit: 'count'});\n  context.record('visits', {value: visits, unit: 'count'});",
            )
        )
        data["workloads"]["http"]["metrics"]["population"] = {
            "name": "population",
            "type": "trend",
            "unit": "count",
        }
        fixed = data["jobs"][0]
        fixed.update(targets=["orchid", "copper"], test={"type": "fixed", "users": [1, 2]})
        staged = {
            "id": "recovery",
            "targets": ["orchid"],
            "test": {"type": "staged"},
            "stages": [
                {"id": "high", "from": 3, "to": 3, "duration": "4s", "evaluate": True},
                {"id": "reduce", "from": 3, "to": 1, "duration": "1s"},
                {"id": "recover", "from": 1, "to": 1, "duration": "4s", "evaluate": True},
                {"id": "cool", "from": 1, "to": 0, "duration": "1s"},
                {"id": "idle", "from": 0, "to": 0, "duration": "3s"},
            ],
        }
        search = {
            "id": "search",
            "targets": ["copper"],
            "test": {
                "type": "search",
                "users": {"min": 1, "start": 1, "max": 4},
                "confirmation": "single_observation",
                "limits": {"max_trials": 4, "max_elapsed": "3m"},
            },
            "objectives": {
                "population": [
                    {"metric": "population", "statistic": "max", "operator": "<=", "value": 2}
                ]
            },
        }
        budget = copy.deepcopy(search)
        budget["id"] = "budget"
        budget["test"]["users"]["max"] = "auto"
        budget["test"]["limits"]["max_trials"] = 1
        data["jobs"] = [fixed, staged, search, budget]
        path.write_text(yaml.safe_dump(data))
        foreign = docker("create", "--label", "bench.queue=" + foreign_queue, "grafana/k6:1.4.2")
        state = tmp_path / "state"
        cli("validate", path)
        cli("queue", "start", path, "--state-dir", state, timeout=300)
        store = QueueStore(state / "queue.db")
        evidence = results(store, data["name"])
        assert all(e.valid and e.cleanup_verified for e in evidence)
        manifests = {e.trial_id: json.loads(e.manifest_path.read_text()) for e in evidence}
        assert len(evidence) == len(manifests) == len(store.snapshot(data["name"])["trials"]) == 10
        actual = Counter(
            (
                manifests[e.trial_id]["job"]["id"],
                manifests[e.trial_id]["job"]["target_name"],
                e.users,
            )
            for e in evidence
        )
        assert actual == Counter(
            [
                ("http-fixed", "orchid", 1),
                ("http-fixed", "orchid", 2),
                ("http-fixed", "copper", 1),
                ("http-fixed", "copper", 2),
                ("recovery", "orchid", None),
                ("search", "copper", 1),
                ("search", "copper", 2),
                ("search", "copper", 4),
                ("search", "copper", 3),
                ("budget", "copper", 1),
            ]
        )
        for evidence_item in evidence:
            manifest = manifests[evidence_item.trial_id]
            version = manifest["job"]["target_name"]
            assert manifest["image_id"] == image_ids[version]
            assert manifest["job"]["target"]["image"] == data["targets"][version]["image"]
            assert manifest["trial_id"] == evidence_item.trial_id
            assert manifest["users"] == evidence_item.users
        recovery = next(
            e
            for e in evidence
            if json.loads(e.manifest_path.read_text())["job"]["id"] == "recovery"
        )
        assert [(s.id, s.from_users, s.to_users) for s in recovery.stages] == [
            ("high", 3, 3),
            ("reduce", 3, 1),
            ("recover", 1, 1),
            ("cool", 1, 0),
            ("idle", 0, 0),
        ]
        assert any(s.metric == "active_users" and s.value == 0 for s in recovery.samples)
        sessions = [s for s in recovery.samples if s.metric == "visits"]
        before = {s.provider_fields["vu"]: s.value for s in sessions if s.timestamp < 4}
        after = [s for s in sessions if 5 <= s.timestamp < 9]
        assert after and all(s.value > before[s.provider_fields["vu"]] for s in after)
        checkpoint = store.controller_state(data["name"])
        result = checkpoint["jobs"]["search"]["results"][0]
        assert result["passing_users"] == 2 and result["failing_users"] == 3
        assert len(result["observations"]) == 4
        assert len({o["trial_id"] for o in result["observations"]}) == 4
        budget_result = checkpoint["jobs"]["budget"]["results"][0]
        assert budget_result["reason"] == "trial_budget"
        assert budget_result["attempts"] == 1
        assert checkpoint["leases"] == {}
        cli("report", data["name"], "--state-dir", state, "--output", tmp_path / "report")
        assert_clean(data["name"])
        assert docker("inspect", foreign, "--format", "{{.Config.Labels}}")
    finally:
        errors = cleanup_owned_queue(data["name"])
        if foreign:
            # The sentinel has its own unique owner; queue cleanup must not touch it.
            try:
                info = json.loads(docker("container", "inspect", foreign))[0]
                if (
                    info["Id"] == foreign
                    and info["Config"]["Labels"].get("bench.queue") == foreign_queue
                ):
                    docker("container", "rm", foreign)
                else:
                    errors.append("sentinel ownership changed; preserved")
            except (OSError, subprocess.SubprocessError, ValueError, KeyError) as exc:
                errors.append(type(exc).__name__)
        for tag in tags:
            try:
                docker("image", "rm", tag)
            except (OSError, subprocess.SubprocessError) as exc:
                errors.append(type(exc).__name__)
        finish_cleanup(errors)


def test_process_mutations_stop_resume_and_crash(config_file, tmp_path):
    path, data = config_file
    data["defaults"]["stages"][0]["duration"] = "10s"
    path.write_text(yaml.safe_dump(data))
    state = tmp_path / "control"
    output = (tmp_path / "controller.log").open("w")
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "tools.bench.commands",
            "queue",
            "start",
            str(path),
            "--state-dir",
            str(state),
        ],
        cwd=ROOT,
        stdout=output,
        stderr=output,
    )
    queue = data["name"]
    store = QueueStore(state / "queue.db")

    def loading():
        return docker(
            "ps",
            "-q",
            "--filter",
            "label=bench.queue=" + queue,
            "--filter",
            "ancestor=grafana/k6:1.4.2",
        )

    try:
        until(loading)
        replacement = copy.deepcopy(data["jobs"][0])
        replacement["id"] = "pending"
        jobfile = tmp_path / "pending.yaml"
        jobfile.write_text(yaml.safe_dump(replacement))
        cli("queue", "append", queue, "--job", jobfile, "--state-dir", state)
        replacement["test"]["users"] = [2]
        jobfile.write_text(yaml.safe_dump(replacement))
        cli("queue", "update", queue, "pending", "--file", jobfile, "--state-dir", state)
        replacement["id"] = "removed"
        jobfile.write_text(yaml.safe_dump(replacement))
        cli("queue", "append", queue, "--job", jobfile, "--state-dir", state)
        cli("queue", "remove", queue, "removed", "--state-dir", state)
        cli("queue", "stop", queue, "--state-dir", state, "--timeout", "60")
        assert process.wait(timeout=60) != 0
        assert store.snapshot(queue)["queues"][0]["state"] != "completed"
        assert_clean(queue)
        process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "tools.bench.commands",
                "queue",
                "resume",
                queue,
                "--state-dir",
                str(state),
            ],
            cwd=ROOT,
            stdout=output,
            stderr=output,
        )
        until(loading)
        process.kill()
        process.wait(timeout=10)
        preserved = {
            p: hashlib.sha256(p.read_bytes()).hexdigest()
            for p in state.rglob("*.json")
            if p.name in {"result.json", "interrupted.json", "manifest.json"}
        }
        assert store.snapshot(queue)["queues"][0]["state"] != "completed"
        cli("queue", "resume", queue, "--state-dir", state, timeout=180)
        assert all(
            hashlib.sha256(p.read_bytes()).hexdigest() == digest for p, digest in preserved.items()
        )
        assert store.snapshot(queue)["queues"][0]["state"] == "completed"
        valid = [e for e in results(store, queue) if e.valid]
        assert sorted(
            (json.loads(e.manifest_path.read_text())["job"]["id"], e.users) for e in valid
        ) == [("http-fixed", 1), ("pending", 2)]
        assert len({e.trial_id for e in valid}) == 2
        assert store.controller_state(queue)["leases"] == {}
        assert_clean(queue)
    finally:
        errors = []
        try:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=60)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=10)
        except (OSError, subprocess.SubprocessError) as exc:
            errors.append(type(exc).__name__)
        finally:
            output.close()
            errors.extend(cleanup_owned_queue(queue))
        finish_cleanup(errors)


@pytest.fixture
def loopback_ssh(tmp_path, monkeypatch):
    if not all(shutil.which(name) for name in ("sshd", "ssh", "ssh-keygen")):
        pytest.skip("OpenSSH server/client unavailable")
    key = tmp_path / "ssh key"
    command("ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", key)
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    config = tmp_path / "sshd_config"
    config.write_text(
        f'Port {port}\nListenAddress 127.0.0.1\nHostKey "{key}"\nPidFile "{tmp_path}/pid"\nAuthorizedKeysFile "{key}.pub"\nStrictModes no\nUsePAM no\nPasswordAuthentication no\nKbdInteractiveAuthentication no\n'
    )
    pub = Path(str(key) + ".pub").read_text().split()
    known = tmp_path / "known_hosts"
    known.write_text(f"[127.0.0.1]:{port} {pub[0]} {pub[1]}\n")
    ssh = shutil.which("ssh")
    binary = tmp_path / "bin"
    binary.mkdir()
    wrapper = binary / "ssh"
    wrapper.write_text(
        f'#!{sys.executable}\nimport os,sys\nos.execv({ssh!r},[{ssh!r},"-o",{("UserKnownHostsFile=" + str(known))!r},"-o","StrictHostKeyChecking=yes",*sys.argv[1:]])\n'
    )
    wrapper.chmod(0o755)
    process = subprocess.Popen(
        [shutil.which("sshd"), "-D", "-e", "-f", str(config)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )

    def ready():
        assert process.poll() is None, process.stderr.read().decode()
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.1):
                return True
        except OSError:
            return False

    try:
        until(ready, timeout=10)
        monkeypatch.setenv("PATH", str(binary) + os.pathsep + os.environ["PATH"])
        yield Host(
            transport="ssh",
            host="127.0.0.1",
            port=port,
            user=pwd.getpwuid(os.getuid()).pw_name,
            identity_file=key,
            workdir=tmp_path / "remote paths with spaces",
        )
    finally:
        process.terminate()
        process.wait(timeout=10)


def test_full_ssh_lifecycle_lost_response(config_file, loopback_ssh, tmp_path, monkeypatch):
    from tools.bench.engine import trial

    path, data = config_file
    host = loopback_ssh.model_dump(mode="json", exclude_none=True)
    data["environments"]["local"].update(backend=host, generator=host)
    # Real SSH latency is intentionally not used as a collector capacity benchmark.
    data["environments"]["local"]["telemetry"]["interval"] = "1s"
    data["defaults"]["stages"][0]["duration"] = "5s"
    path.write_text(yaml.safe_dump(data))
    job = resolve_jobs(load_config(path))[0]
    original = trial.execute_host
    lost = []

    def lose_response(host, argv, **kwargs):
        response = original(host, argv, **kwargs)
        if host.transport == "ssh" and argv[:2] == ["docker", "start"] and not lost:
            lost.append(argv[-1])
            raise subprocess.TimeoutExpired(argv, 1)
        return response

    monkeypatch.setattr(trial, "execute_host", lose_response)
    evidence = run_trial(
        TrialContext(job, 1, tmp_path / "ssh trial", data["name"], "ssh-lost-response")
    )
    assert lost, "fault must be injected after an actual remote Docker start"
    assert evidence.valid, evidence.invalid_reasons
    assert evidence.cleanup_verified
    assert any(s.scope == "backend_host" for s in evidence.samples)
    assert any(s.scope == "container" for s in evidence.samples)
    assert_clean(data["name"])
    assert not list(loopback_ssh.workdir.rglob("runner.js"))


def test_fresh_readme_install_without_aws(tmp_path):
    checkout = tmp_path / "fresh checkout"
    checkout.mkdir()
    # Snapshot tracked working-tree files, including current fixes, without Git internals.
    files = command("git", "ls-files", "-z").split("\0")
    for name in filter(None, files):
        source = ROOT / name
        if not source.is_file():
            continue
        destination = checkout / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
    env = {
        k: v
        for k, v in os.environ.items()
        if not k.startswith("AWS_") and k not in {"VIRTUAL_ENV", "PYTHONPATH"}
    }
    env.update(
        AWS_EC2_METADATA_DISABLED="true",
        AWS_SHARED_CREDENTIALS_FILE=str(tmp_path / "no-credentials"),
        AWS_CONFIG_FILE=str(tmp_path / "no-config"),
    )

    def uv(*args):
        return command("uv", *args, cwd=checkout, env=env, timeout=240)

    uv("sync")
    config = tmp_path / "demo" / "queue.yaml"
    uv("run", "bench", "init", "--template", "http", "--output", config)
    uv("run", "bench", "validate", config)
    uv("run", "bench", "doctor", config)
    data = yaml.safe_load(config.read_text())
    data["jobs"] = [data["jobs"][0]]
    data["jobs"][0]["test"]["users"] = [2]
    config.write_text(yaml.safe_dump(data))
    state = tmp_path / "demo-state"
    assert not state.exists()
    uv("run", "bench", "queue", "start", config, "--state-dir", state)
    uv("run", "bench", "queue", "status", "http-demo", "--state-dir", state)
    uv(
        "run",
        "bench",
        "report",
        "http-demo",
        "--state-dir",
        state,
        "--output",
        tmp_path / "demo-report",
    )
    evidence = results(QueueStore(state / "queue.db"), "http-demo")
    assert len(evidence) == 1 and evidence[0].users == 2
    assert evidence[0].valid and evidence[0].cleanup_verified
    command(
        "uv", "run", "-m", "bench.cli", "--help", cwd=checkout, env={**env, "PYTHONPATH": "tools"}
    )
    assert_clean("http-demo")


def test_disposable_user_service_shutdown_resume(config_file, tmp_path):
    """Only a unique new unit is created; existing user services are untouched."""
    from tools.bench.queue.services import _unit_name

    if not shutil.which("systemctl") or not shutil.which("loginctl"):
        pytest.skip("user systemd unavailable")
    available = subprocess.run(["systemctl", "--user", "show-environment"], capture_output=True)
    if (
        available.returncode
        or command("loginctl", "show-user", os.getuid(), "--property=Linger", "--value").strip()
        != "yes"
    ):
        pytest.skip("user bus/lingering unavailable")
    path, data = config_file
    data["defaults"]["stages"][0]["duration"] = "10s"
    path.write_text(yaml.safe_dump(data))
    queue = data["name"]
    state = tmp_path / "service state with spaces"
    name = _unit_name(queue, state)
    unit = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "systemd/user" / name
    assert not unit.exists()
    try:
        cli("queue", "start", path, "--state-dir", state, "--detach")
        store = QueueStore(state / "queue.db")
        until(
            lambda: docker(
                "ps",
                "-q",
                "--filter",
                "label=bench.queue=" + queue,
                "--filter",
                "ancestor=grafana/k6:1.4.2",
            )
        )
        command("systemctl", "--user", "stop", name)
        assert store.snapshot(queue)["queues"][0]["state"] == "interrupted"
        assert store.stop_request(queue) is None
        assert command("systemctl", "--user", "is-enabled", name).strip() == "enabled"
        assert_clean(queue)
        cli("queue", "resume", queue, "--state-dir", state, "--detach")
        until(lambda: store.snapshot(queue)["queues"][0]["state"] == "completed", timeout=120)
        until(
            lambda: (
                command(
                    "systemctl", "--user", "show", name, "--property=ActiveState", "--value"
                ).strip()
                == "inactive"
            )
        )
        valid = [e for e in results(store, queue) if e.valid]
        assert len(valid) == 1 and valid[0].cleanup_verified
        assert (
            command(
                "systemctl", "--user", "show", name, "--property=UnitFileState", "--value"
            ).strip()
            == "disabled"
        )
        assert_clean(queue)
    finally:
        if unit.exists():
            command("systemctl", "--user", "stop", name)
            command("systemctl", "--user", "disable", name)
            unit.unlink()
            command("systemctl", "--user", "daemon-reload")


def test_owned_queue_failure_cleanup_preserves_foreign(monkeypatch):
    queue = "acceptance-finalizer-" + uuid.uuid4().hex[:12]
    foreign_queue = queue + "-sentinel"
    foreign = None
    try:
        docker("create", "--label", "bench.queue=" + queue, "grafana/k6:1.4.2")
        docker("network", "create", "--label", "bench.queue=" + queue, queue)
        foreign = docker("create", "--label", "bench.queue=" + foreign_queue, "grafana/k6:1.4.2")
        with pytest.raises(AssertionError, match="original acceptance failure"):
            try:
                raise AssertionError("original acceptance failure")
            finally:
                finish_cleanup(cleanup_owned_queue(queue))
        assert_clean(queue)
        assert json.loads(docker("inspect", foreign))[0]["Id"] == foreign
        with monkeypatch.context() as patch:

            def unavailable(*args):
                raise subprocess.TimeoutExpired(args, 1)

            patch.setattr(sys.modules[__name__], "docker", unavailable)
            with pytest.raises(AssertionError, match="original acceptance failure") as failure:
                try:
                    raise AssertionError("original acceptance failure")
                finally:
                    finish_cleanup(cleanup_owned_queue(queue))
            assert any("cleanup errors" in note for note in failure.value.__notes__)
    finally:
        finish_cleanup(cleanup_owned_queue(queue) + cleanup_owned_queue(foreign_queue))
