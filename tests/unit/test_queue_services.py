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
    assert "EnvironmentFile=" in unit
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
    receipt = services.install_service("queue % name", tmp_path / "state with % space")
    result = subprocess.run(
        [analyzer, "--user", "verify", str(receipt.unit_path)], capture_output=True, text=True
    )
    assert result.returncode == 0, result.stderr
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
