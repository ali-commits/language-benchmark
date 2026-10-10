import copy
import json
from pathlib import Path

import pytest
from bench.models import NativeCell, RuntimeCell
from bench.native_runtime import load_runtime_cell


def profiles():
    from bench.tuning import load_tuned_profiles

    return load_tuned_profiles()


def test_nine_explicit_profiles_keep_originals_and_fixed_versions():
    rows = profiles()
    assert len(rows) == 9
    for profile in rows:
        original = load_runtime_cell(profile.base_cell)
        tuned = load_runtime_cell(profile.cell.id)
        assert tuned == profile.cell
        assert tuned.profile == "tuned4"
        assert original.profile == ""
        assert tuned.id == original.id + "-tuned4"
        assert tuned.image == original.image + "-tuned4"
        assert profile.resources == {"cpus": 4, "memory": 6442450944, "swap": 0, "nofile": 250000}
        if isinstance(original, NativeCell):
            assert tuned.runtime_version == original.runtime_version
            assert tuned.framework_version == original.framework_version
        else:
            assert tuned.python == original.python
            assert tuned.dependency_lock == original.dependency_lock


@pytest.mark.parametrize("mutation", ["duplicate", "budget", "version", "id", "environment"])
def test_registry_rejects_uncontrolled_profiles(tmp_path, mutation):
    from bench.tuning import load_tuned_profiles

    source = json.loads(Path("runtimes/profiles/tuned4.json").read_text())
    if mutation == "duplicate":
        source["cells"].append(copy.deepcopy(source["cells"][0]))
    elif mutation == "budget":
        source["resources"]["cpus"] = 8
    elif mutation == "version":
        source["cells"][0]["image"] = "lb-new-version:latest"
    elif mutation == "id":
        source["cells"][0]["id"] = "silent-replacement"
    else:
        source["cells"][0]["environment"]["WORKERS"] = "20"
    path = tmp_path / "tuned4.json"
    path.write_text(json.dumps(source))
    with pytest.raises(ValueError):
        load_tuned_profiles(path=path)


def test_four_worker_controls_remain_explicit():
    with pytest.raises(ValueError):
        RuntimeCell(id="uncontrolled", python="3.14.8", app="modern", workers=4)
    data = vars(load_runtime_cell("node-express")).copy()
    with pytest.raises(ValueError):
        NativeCell(**{**data, "workers": 4, "connections_per_worker": 5})
    for profile in profiles():
        if profile.base_cell.startswith(("python", "node", "bun")):
            assert profile.cell.workers == 4


def tuning_proof(base="go-net-http"):
    profile = next(p for p in profiles() if p.base_cell == base)
    language = "python" if isinstance(profile.cell, RuntimeCell) else profile.cell.language
    command = {
        "go": "social-api",
        "rust": "social-api",
        "java": "java -jar /app/api.jar",
        "csharp": "dotnet BenchmarkApi.dll",
        "node": "node /app/src/worker.js",
        "bun": "bun /app/src/worker.js",
        "python": "python -c from multiprocessing.spawn import spawn_main; spawn_main()",
        "php": "php-fpm: pool www",
    }[language]
    workers = profile.cell.workers
    scope = (
        "worker"
        if language in ("python", "node", "bun")
        else "global"
        if language == "php"
        else "process"
    )
    rows = []
    processes = []
    for pid in range(10, 10 + workers):
        processes.append({"pid": pid, "command": command, "started_at": 102.0})
        rows.append(
            {
                "filename": f"{pid}.json" if language == "python" else f"tuning-{pid}.json",
                "modified_at": 103.0,
                "receipt": {
                    "schema_version": 1,
                    "run_id": "nonce",
                    "pid": pid,
                    "language": language,
                    "workers_total": workers,
                    "connections_per_worker": 20 // workers,
                    "pool_budget": 20,
                    "cpu_parallelism": None if scope != "process" else 4,
                    "hash_concurrency": 1 if scope == "worker" else 4,
                    "hash_scope": scope,
                },
            }
        )
    return profile, rows, processes


@pytest.mark.parametrize(
    "base",
    [
        "go-net-http",
        "rust-axum",
        "java-spring-boot",
        "csharp-aspnet-core",
        "python-modern-3.14.8-standard-jit0-gil1-async",
        "node-express",
        "bun-express",
        "php-laravel",
        "php-laravel-octane",
    ],
)
def test_live_receipts_attest_arithmetic_and_processes(base):
    from bench.tuning import validate_tuning_receipts

    profile, rows, processes = tuning_proof(base)
    if base.endswith("octane"):
        for proc in processes:
            proc["command"] = "php /app/vendor/bin/roadrunner-worker"
    assert (
        len(validate_tuning_receipts(profile, rows, processes, "nonce", 100.0))
        == profile.cell.workers
    )


@pytest.mark.parametrize(
    "mutation",
    [
        "missing",
        "nonce",
        "pid",
        "hash",
        "scope",
        "cpu",
        "pool",
        "workers",
        "stale",
        "before_process",
        "duplicate",
        "dead",
        "supervisor",
        "extra_process",
    ],
)
def test_rejects_stale_or_fabricated_worker_proof(mutation):
    from bench.tuning import validate_tuning_receipts

    profile, rows, processes = tuning_proof("node-express")
    if mutation == "missing":
        rows.pop()
    elif mutation == "nonce":
        rows[0]["receipt"]["run_id"] = "old"
    elif mutation == "pid":
        rows[0]["receipt"]["pid"] = 999
    elif mutation == "hash":
        rows[0]["receipt"]["hash_concurrency"] = 4
    elif mutation == "scope":
        rows[0]["receipt"]["hash_scope"] = "global"
    elif mutation == "cpu":
        rows[0]["receipt"]["cpu_parallelism"] = 4
    elif mutation == "pool":
        rows[0]["receipt"]["connections_per_worker"] = 10
    elif mutation == "workers":
        rows[0]["receipt"]["workers_total"] = 20
    elif mutation == "stale":
        rows[0]["modified_at"] = 99.0
    elif mutation == "before_process":
        processes[0]["started_at"] = 110.0
    elif mutation == "duplicate":
        rows.append(copy.deepcopy(rows[0]))
    elif mutation == "dead":
        processes.pop()
    elif mutation == "supervisor":
        processes[0]["command"] = "node /app/src/server.js"
    else:
        processes.append({**processes[0], "pid": 999})
    with pytest.raises(ValueError):
        validate_tuning_receipts(profile, rows, processes, "nonce", 100.0)


def test_php_recycling_ignores_dead_old_files_only():
    from bench.tuning import validate_tuning_receipts

    profile, rows, processes = tuning_proof("php-laravel")
    old = copy.deepcopy(rows[0])
    old["filename"] = "tuning-999.json"
    old["receipt"]["pid"] = 999
    old["receipt"]["run_id"] = "old"
    old["modified_at"] = 90
    assert len(validate_tuning_receipts(profile, rows + [old], processes, "nonce", 100.0)) == 20


@pytest.mark.parametrize(
    "change",
    [
        {"nano_cpus": 2000000000},
        {"memory": 3221225472},
        {"memory_swap": -1},
        {"running": False},
        {"run_id": "stale"},
    ],
)
def test_tuned_container_actual_resource_guard(change):
    from bench.tuning import validate_tuned_container

    profile = profiles()[0]
    data = dict(
        id="container",
        image="sha256:x",
        running=True,
        run_id="nonce",
        nano_cpus=4000000000,
        memory=6442450944,
        memory_swap=6442450944,
        ulimits=[dict(Name="nofile", Soft=250000, Hard=250000)],
    )
    validate_tuned_container(data, profile, "nonce")
    with pytest.raises(ValueError):
        validate_tuned_container({**data, **change}, profile, "nonce")


def test_cgroup_resource_proof_is_independent_of_declared_labels():
    from bench.tuning import parse_process_probe

    text = (
        "C\t200\t100\t100\nQ\t400000 100000\nM\t6442450944\nS\t0\nP\t10\t200\tsocial-api\nR\ttuning-10.json\t103\t"
        + json.dumps(tuning_proof()[1][0]["receipt"])
        + "\n"
    )
    rows, processes = parse_process_probe(text, profiles()[1])
    assert rows[0]["receipt"]["pid"] == 10
    assert processes[0]["started_at"] == 102
    for old, new in [
        ("400000 100000", "200000 100000"),
        ("6442450944", "3221225472"),
        ("S\t0", "S\tmax"),
        ("400000 100000", "max 100000"),
    ]:
        with pytest.raises(ValueError):
            parse_process_probe(text.replace(old, new), profiles()[1])


def test_probe_ignores_express_readiness_files_and_rejects_extra_receipt_fields():
    from bench.tuning import parse_process_probe, validate_tuning_receipts

    profile, rows, processes = tuning_proof("node-express")
    text = 'C\t200\t100\t100\nQ\t400000 100000\nM\t6442450944\nS\t0\nR\t10.json\t103\t{"pid":10,"dependencies":{}}\n'
    assert parse_process_probe(text, profile)[0] == []
    rows[0]["receipt"]["unapproved"] = "arbitrary environment dump"
    with pytest.raises(ValueError):
        validate_tuning_receipts(profile, rows, processes, "nonce", 100.0)


def test_native_explicit_profile_rejects_forged_identity():
    data = vars(load_runtime_cell("go-net-http")).copy()
    with pytest.raises(ValueError):
        NativeCell(**{**data, "id": "fake-go-tuned4", "profile": "tuned4"})


def test_profile_target_controls_are_checked_before_deployment():
    from bench.tuning import validate_profile_configuration

    profile = profiles()[0]
    validate_profile_configuration(profile, profile.resources, profile.environment)
    for resources, environment in [
        ({**profile.resources, "cpus": 2}, profile.environment),
        (profile.resources, {}),
        (profile.resources, {**profile.environment, "WORKERS": "2"}),
    ]:
        with pytest.raises(ValueError):
            validate_profile_configuration(profile, resources, environment)


def test_tuned_native_resource_guard_requires_explicit_cell_and_legacy_preflight_cannot_bypass_receipts():
    from bench.native_runtime import native_preflight, validate_native_container

    tuned = load_runtime_cell("go-net-http-tuned4")
    data = dict(
        id="container",
        image="sha256:x",
        running=True,
        run_id="nonce",
        cell_id=tuned.id,
        nano_cpus=4000000000,
        memory=6442450944,
        memory_swap=6442450944,
        network_mode="host",
        ulimits=[dict(Name="nofile", Soft=250000, Hard=250000)],
        mounts=[dict(Destination="/certs/rds-ca.pem", RW=False)],
    )
    validate_native_container(data, tuned, "nonce", "sha256:x")
    with pytest.raises(ValueError):
        validate_native_container(
            {**data, "cell_id": "go-net-http"},
            load_runtime_cell("go-net-http"),
            "nonce",
            "sha256:x",
        )
    with pytest.raises(ValueError, match="social"):
        native_preflight({}, tuned, [], {}, "http://127.0.0.1:1", "nonce", "sha256:x")


def test_tuned_loading_is_opt_in_for_full_registry_and_metadata():
    from bench.native_runtime import load_runtime_cells, registry_metadata

    legacy = load_runtime_cells()
    assert all(not cell.profile for cell in legacy)
    full = load_runtime_cells(include_profiles=True)
    assert len(full) == len(legacy) + 9
    metadata = registry_metadata(load_runtime_cell("go-net-http-tuned4"))
    assert metadata["path"] == "runtimes/profiles/tuned4.json"


@pytest.mark.parametrize("field", ["modified_at", "started_at"])
def test_nonfinite_timestamps_cannot_bypass_freshness(field):
    from bench.tuning import validate_tuning_receipts

    profile, rows, processes = tuning_proof()
    (rows if field == "modified_at" else processes)[0][field] = float("nan")
    with pytest.raises(ValueError):
        validate_tuning_receipts(profile, rows, processes, "nonce", 100.0)


def test_probe_ignores_invalid_json_in_unselected_express_files():
    from bench.tuning import parse_process_probe

    text = (
        "C\t200\t100\t100\nQ\t400000 100000\nM\t6442450944\nS\t0\nR\t10.json\t103\tinvalid-json\n"
    )
    assert (
        parse_process_probe(text, next(p for p in profiles() if p.base_cell == "node-express"))[0]
        == []
    )


def test_registry_rejects_extra_version_or_topology_fields(tmp_path):
    from bench.tuning import load_tuned_profiles

    document = json.loads(Path("runtimes/profiles/tuned4.json").read_text())
    document["cells"][0]["runtime_version"] = "unfrozen-new-version"
    path = tmp_path / "tuned4.json"
    path.write_text(json.dumps(document))
    with pytest.raises(ValueError):
        load_tuned_profiles(path=path)


def test_tuned_profiles_reject_changed_original_lock_fingerprint(tmp_path):
    import shutil

    from bench.tuning import load_tuned_profiles

    shutil.copytree("runtimes", tmp_path / "runtimes")
    root = tmp_path / "runtimes"
    path = root / "node/lock.json"
    data = json.loads(path.read_text())
    data["cells"][0]["framework_version"] = "changed-version"
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="fingerprint"):
        load_tuned_profiles(root)
