import json
from types import SimpleNamespace

import pytest
from bench.stop_load import stop_owned_load
from bench.transport import CommandFailure

CID = "a" * 64
PROJECT = "/home/rocky/language-benchmark"


def info(**change):
    return {
        "id": CID,
        "running": True,
        "project": "language-benchmark-load",
        "service": "k6",
        "working_dir": PROJECT,
        **change,
    }


def failure(text):
    return CommandFailure(SimpleNamespace(returncode=1, stdout="", stderr=text))


def test_owned_oneoff_gracefully_stops_and_autoremoval_is_idempotent():
    calls = []
    listed = iter([CID, ""])

    def execute(host, argv, *args, **kw):
        calls.append(argv)
        if argv[:2] == ["docker", "ps"]:
            return SimpleNamespace(stdout=next(listed))
        if argv[:2] == ["docker", "inspect"]:
            if sum(1 for c in calls if c[:2] == ["docker", "inspect"]) > 1:
                raise failure("No such object: " + CID)
            return SimpleNamespace(stdout=json.dumps(info()))
        return SimpleNamespace(stdout=CID)

    result = stop_owned_load({}, PROJECT, execute_command=execute)
    assert result["verified_gone"] and any(c[:2] == ["docker", "stop"] for c in calls)
    assert not any(c[:2] == ["docker", "rm"] for c in calls)
    for c in calls:
        if c[:2] == ["docker", "ps"]:
            assert "label=com.docker.compose.project=language-benchmark-load" in c
            assert "label=com.docker.compose.service=k6" in c
            assert "label=com.docker.compose.project.working_dir=" + PROJECT in c


@pytest.mark.parametrize(
    "change",
    [
        {"project": "unrelated"},
        {"service": "other"},
        {"working_dir": "/other/project"},
        {"working_dir": None},
    ],
)
def test_wrong_ownership_never_stops_foreign_containers(change):
    calls = []

    def execute(host, argv, *a, **k):
        calls.append(argv)
        return SimpleNamespace(
            stdout=CID if argv[:2] == ["docker", "ps"] else json.dumps(info(**change))
        )

    with pytest.raises(RuntimeError, match="ownership"):
        stop_owned_load({}, PROJECT, execute_command=execute)
    assert not any(c[:2] in (["docker", "stop"], ["docker", "rm"]) for c in calls)


def test_stopped_owned_oneoff_is_removed_and_verification_failure_blocks_reset():
    calls = []
    lists = iter([CID, ""])

    def execute(host, argv, *a, **k):
        calls.append(argv)
        if argv[:2] == ["docker", "ps"]:
            return SimpleNamespace(stdout=next(lists))
        if argv[:2] == ["docker", "inspect"]:
            return SimpleNamespace(stdout=json.dumps(info(running=False)))
        return SimpleNamespace(stdout=CID)

    assert stop_owned_load({}, PROJECT, execute_command=execute)["verified_gone"]
    assert any(c[:2] == ["docker", "rm"] for c in calls)
    assert not any(c[:2] == ["docker", "stop"] for c in calls)


def test_shutdown_error_fails_closed():
    def execute(host, argv, *a, **k):
        if argv[:2] == ["docker", "ps"]:
            return SimpleNamespace(stdout=CID)
        if argv[:2] == ["docker", "inspect"]:
            return SimpleNamespace(stdout=json.dumps(info()))
        raise failure("Docker stop failed")

    with pytest.raises(CommandFailure):
        stop_owned_load({}, PROJECT, execute_command=execute)


def test_failed_owned_shutdown_prevents_fixture_reset_in_real_lifecycle():
    from bench.runner import trial_lifecycle

    resets = []

    def execute(host, argv, *a, **k):
        if argv[:2] == ["docker", "ps"]:
            return SimpleNamespace(stdout=CID)
        if argv[:2] == ["docker", "inspect"]:
            return SimpleNamespace(stdout=json.dumps(info()))
        raise failure("Docker stop failed")

    result = trial_lifecycle(
        lambda: stop_owned_load({}, PROJECT, execute_command=execute),
        lambda: resets.append(True),
        lambda: pytest.fail("load started"),
    )
    assert result["status"] == "failed" and not result["reset_verified"] and resets == []
