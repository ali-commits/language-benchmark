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
