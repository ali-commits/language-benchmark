import copy
import json
from pathlib import Path

import pytest
from bench.models import NativeCell
from bench.native_runtime import load_runtime_cell
from bench.tuning import load_production_profiles


def test_production_profiles_preserve_frozen_base_and_tuned():
    profiles = load_production_profiles()
    assert len(profiles) == 9
    for p in profiles:
        original = load_runtime_cell(p.base_cell)
        assert load_runtime_cell(p.cell.id) == p.cell
        assert p.cell.profile == "production4"
        assert p.cell.id == original.id + "-production4"
        assert p.environment["BENCH_PROFILE"] == "production4"
        if isinstance(original, NativeCell):
            assert p.cell.runtime_version == original.runtime_version
            assert p.cell.framework_version == original.framework_version
        else:
            assert p.cell.python == original.python
            assert p.cell.dependency_lock == "backends/python/production/requirements.lock"
    assert {p.cell.pool_budget for p in profiles} == {16, 32, 40}


@pytest.mark.parametrize("mutation", ["budget", "env", "image", "fingerprint", "extra"])
def test_production_registry_tampering_is_rejected(tmp_path, mutation):
    d = json.loads(Path("runtimes/profiles/production4.json").read_text())
    if mutation == "budget":
        d["resources"]["memory"] = 1
    elif mutation == "env":
        d["cells"][0]["environment"]["DB_POOL_MAX_SIZE"] = "11"
    elif mutation == "image":
        d["cells"][0]["image"] = "mutable:latest"
    elif mutation == "fingerprint":
        d["dependency_locks"]["backends/python/production/uv.lock"] = "0" * 64
    else:
        d["uncontrolled"] = True
    path = tmp_path / "registry.json"
    path.write_text(json.dumps(d))
    with pytest.raises(ValueError):
        load_production_profiles(path=path)


def proof(base):
    from bench.tuning import _production_receipt_fields
    from test_tuned_profiles import tuning_proof

    oldbase, rows, processes = tuning_proof(base)
    p = next(p for p in load_production_profiles() if p.base_cell == base)
    if base.startswith("php"):
        template = rows[0]
        command = (
            "php /app/vendor/bin/roadrunner-worker"
            if base.endswith("octane")
            else "php-fpm: pool www"
        )
        rows = []
        processes = []
        for pid in range(10, 42):
            row = copy.deepcopy(template)
            row["filename"] = f"tuning-{pid}.json"
            row["receipt"]["pid"] = pid
            rows.append(row)
            processes.append(dict(pid=pid, command=command, started_at=102.0))
    for row in rows:
        r = row["receipt"]
        r.update(
            schema_version=2,
            workers_total=p.cell.workers,
            connections_per_worker=p.cell.pool_budget // p.cell.workers,
            pool_budget=p.cell.pool_budget,
        )
        r.update(_production_receipt_fields(base))
    return p, rows, processes


@pytest.mark.parametrize("base", [p.base_cell for p in load_production_profiles()])
def test_production_receipts_prove_live_per_stack_budgets(base):
    from bench.tuning import validate_tuning_receipts

    p, rows, processes = proof(base)
    assert len(validate_tuning_receipts(p, rows, processes, "nonce", 100.0)) == p.cell.workers
    for mutation in ("pool_budget", "schema_version", "run_id", "extra", "wrong_feature"):
        bad = copy.deepcopy(rows)
        r = bad[0]["receipt"]
        if mutation == "pool_budget":
            r["pool_budget"] = 20
        elif mutation == "schema_version":
            r["schema_version"] = 1
        elif mutation == "run_id":
            r["run_id"] = "old"
        elif mutation == "extra":
            r["unsafe_feature"] = True
        else:
            from bench.tuning import _production_receipt_fields

            key = next(iter(_production_receipt_fields(base)))
            r[key] = "wrong"
        with pytest.raises(ValueError):
            validate_tuning_receipts(p, bad, processes, "nonce", 100.0)


def test_production_profile_inclusion_is_explicit():
    from bench.native_runtime import load_runtime_cells

    old = load_runtime_cells(include_profiles=True)
    new = load_runtime_cells(include_profiles=True, include_production_profiles=True)
    assert len(new) == len(old) + 9
    assert sum(c.profile == "production4" for c in new) == 9


def test_python_production_requires_protocol_evidence():
    from bench.runtime import validate_worker_snapshots

    p = load_production_profiles()[0]
    row = dict(
        config_args="--enable-optimizations --with-lto",
        jit_available=False,
        python="3.14.8",
        gil=True,
        free_threaded=False,
        jit=False,
        run_id="nonce",
    )
    snapshots = [{**row, "pid": i} for i in range(4)]
    assert validate_worker_snapshots(p.cell, snapshots)
    for r in snapshots:
        r["tuning"] = dict(schema_version=2, event_loop="uvloop", http_parser="httptools")
    assert validate_worker_snapshots(p.cell, snapshots) == []


@pytest.mark.parametrize("base", ["go-net-http", "php-laravel", "node-express"])
def test_production_receipts_reject_incomplete_or_stale_process_proof(base):
    from bench.tuning import validate_tuning_receipts

    p, rows, processes = proof(base)
    with pytest.raises(ValueError):
        validate_tuning_receipts(p, rows, processes[:-1], "nonce", 100.0)
    bad = copy.deepcopy(rows)
    bad[0]["modified_at"] = 99.0
    with pytest.raises(ValueError):
        validate_tuning_receipts(p, bad, processes, "nonce", 100.0)
    bad = copy.deepcopy(rows)
    bad[0]["receipt"]["pid"] = 9999
    with pytest.raises(ValueError):
        validate_tuning_receipts(p, bad, processes, "nonce", 100.0)


def test_production_rejects_unregistered_identity_and_profile_forgery():
    from dataclasses import replace

    from bench.tuning import profile_for_cell

    p = load_production_profiles()[1]
    with pytest.raises(ValueError):
        replace(p.cell, pool_budget=20, connections_per_worker=20)
    with pytest.raises(ValueError):
        replace(p.cell, id=p.base_cell + "-tuned4")
    forged = copy.deepcopy(p.cell)
    forged.image = "tampered:latest"
    with pytest.raises(ValueError):
        profile_for_cell(forged)


def test_production_fingerprints_each_dependency_input():
    import hashlib

    from bench.engine.resources import dependency_lock_path

    d = json.loads(Path("runtimes/profiles/production4.json").read_text())
    for path, digest in d["dependency_locks"].items():
        assert hashlib.sha256(dependency_lock_path(path).read_bytes()).hexdigest() == digest
