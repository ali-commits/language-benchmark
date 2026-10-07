import pytest

from tools.bench.queue import services


def test_missing_systemd_clear_error(tmp_path, monkeypatch):
    monkeypatch.setattr(services.shutil, "which", lambda name: None)
    with pytest.raises(services.ServiceUnavailable, match="systemd"):
        services.install_service("queue", tmp_path)


def test_missing_linger_clear_error(tmp_path, monkeypatch):
    monkeypatch.setattr(services.shutil, "which", lambda name: "/usr/bin/" + name)
    monkeypatch.setattr(services, "_command", lambda args: "no" if "show-user" in args else "")
    with pytest.raises(services.ServiceUnavailable, match="lingering"):
        services.install_service("queue", tmp_path)


def test_unit_absolute_quoted_paths_and_enable(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setattr(services.shutil, "which", lambda name: "/usr/bin/" + name)
    calls = []

    def command(args):
        calls.append(args)
        return "yes" if "show-user" in args else ""

    monkeypatch.setattr(services, "_command", command)
    receipt = services.install_service("../bad %name $HOME", tmp_path / "state with % space")
    assert receipt.reboot_persistent
    assert receipt.unit_path.parent == tmp_path / "config/systemd/user"
    text = receipt.unit_path.read_text()
    assert "%%name $$HOME" in text
    assert "Restart=on-failure" in text
    assert "TimeoutStopSec=" in text
    assert any("enable" in c for c in calls)
    assert not any("sudo" in c for c in calls)


def test_private_environment_file_only_path_in_unit(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setattr(services.shutil, "which", lambda name: "/usr/bin/" + name)
    monkeypatch.setattr(services, "_command", lambda args: "yes" if "show-user" in args else "")
    private = tmp_path / "private.env"
    private.write_text("API_TOKEN=secret-for-test\n")
    private.chmod(0o600)
    receipt = services.install_service("q", tmp_path / "state", environment_file=private)
    unit = receipt.unit_path.read_text()
    assert "EnvironmentFile=" + str(private) + "\n" in unit
    assert str(private) in unit
    assert "secret-for-test" not in unit
    private.chmod(0o644)
    with pytest.raises(services.ServiceUnavailable, match="0600"):
        services.install_service("q", tmp_path / "state", environment_file=private)


@pytest.mark.parametrize("folder", ["ordinary", "spaces and % specifier"])
def test_actual_systemd_parser_accepts_unit(tmp_path, monkeypatch, folder):
    import subprocess

    analyzer = services.shutil.which("systemd-analyze")
    if analyzer is None:
        pytest.skip("systemd parser unavailable")
    project = tmp_path / folder
    (project / "tools/bench/queue").mkdir(parents=True)
    monkeypatch.setattr(services, "__file__", str(project / "tools/bench/queue/services.py"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setattr(services, "_command", lambda args: "yes" if "show-user" in args else "")
    private = project / "private.env"
    private.write_text("BENCH_SERVICE_TEST=sentinel\n")
    private.chmod(0o600)
    receipt = services.install_service(
        "queue % name", tmp_path / "state with % space", environment_file=private
    )
    result = subprocess.run(
        [analyzer, "--user", "verify", str(receipt.unit_path)], capture_output=True, text=True
    )
    assert result.returncode == 0, result.stderr
    assert "not absolute" not in result.stderr
    assert "ignoring" not in result.stderr.lower()
    assert "WorkingDirectory=" + str(project).replace("%", "%%") in receipt.unit_path.read_text()


def test_sigterm_keeps_reboot_eligibility(tmp_path, monkeypatch):
    import signal

    from tests.unit.test_queue_store import config

    store = services.QueueStore(tmp_path / "queue.db")
    store.register(config(tmp_path))
    handlers = {}
    commands = []
    monkeypatch.setattr(
        services.signal, "signal", lambda signum, handler: handlers.setdefault(signum, handler)
    )
    monkeypatch.setattr(services, "_command", lambda args: commands.append(args))

    def interrupted(store, queue, *, interrupt_requested=lambda: False):
        handlers[signal.SIGTERM](signal.SIGTERM, None)
        assert interrupt_requested()
        assert store.stop_request(queue) is None
        return {"queues": [{"state": "interrupted"}]}

    monkeypatch.setattr(services, "run_queue", interrupted)
    assert services._run("test", tmp_path) == 0
    assert not any("disable" in cmd for cmd in commands)
    assert store.stop_request("test") is None

    def rebooted(store, queue, *, interrupt_requested=lambda: False):
        assert not interrupt_requested()
        assert store.stop_request(queue) is None
        return {"queues": [{"state": "completed"}]}

    monkeypatch.setattr(services, "run_queue", rebooted)
    assert services._run("test", tmp_path) == 0
    assert any("disable" in cmd for cmd in commands)


def test_explicit_stop_latch_survives_signal_and_worker(tmp_path, monkeypatch):
    from tests.unit.test_queue_store import config

    store = services.QueueStore(tmp_path / "queue.db")
    store.register(config(tmp_path))
    calls = []
    monkeypatch.setattr(services, "_command", lambda args: calls.append(args))
    services.stop_service("test", tmp_path)
    assert store.stop_request("test") is False
    assert services._run("test", tmp_path) == 0
    assert store.stop_request("test") is False
    assert store.jobs("test")[0].state == "pending"
    assert any("disable" in call for call in calls)


def test_environment_file_path_escapes_glob_and_specifiers():
    from pathlib import Path

    assert (
        services._environment_file_path(Path("/tmp/space % *?[x]\\.env "))
        == "/tmp/space[ ]%%[ ][*][?][[]x[]]\\\\.env[ ]"
    )
    for path in (Path("relative.env"), Path("/tmp/bad\nname.env")):
        with pytest.raises(ValueError, match="absolute and single-line"):
            services._environment_file_path(path)


@pytest.mark.parametrize("filename", ["ordinary.env", "spaces %n $HOME *?[x]\\.env "])
def test_actual_user_systemd_reads_exact_environment_file(tmp_path, filename):
    import os
    import subprocess
    import sys
    from uuid import uuid4

    if os.environ.get("BENCH_SYSTEMD_ENV_SMOKE") != "1":
        pytest.skip("opt-in ephemeral user-systemd execution")
    control = services.shutil.which("systemctl")
    if not control:
        pytest.skip("systemctl unavailable")
    private = tmp_path / filename
    private.write_text("BENCH_SERVICE_TEST=expected-sentinel\n")
    private.chmod(0o600)
    name = "bench-env-smoke-" + uuid4().hex + ".service"
    unit = tmp_path / name
    argv = [
        sys.executable,
        "-c",
        "import os; assert os.environ['BENCH_SERVICE_TEST']=='expected-sentinel'",
    ]
    unit.write_text(
        "[Service]\nType=oneshot\nEnvironmentFile="
        + services._environment_file_path(private)
        + "\nExecStart="
        + " ".join(services._quote(arg) for arg in argv)
        + "\n"
    )
    try:
        subprocess.run(
            [control, "--user", "link", "--runtime", str(unit)],
            check=True,
            capture_output=True,
            timeout=30,
        )
        result = subprocess.run(
            [control, "--user", "start", "--wait", name], capture_output=True, text=True, timeout=30
        )
        assert result.returncode == 0, result.stderr
    finally:
        subprocess.run(
            [control, "--user", "disable", "--runtime", name],
            check=True,
            capture_output=True,
            timeout=30,
        )
        subprocess.run([control, "--user", "reset-failed", name], capture_output=True, timeout=30)
