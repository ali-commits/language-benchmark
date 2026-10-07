import json
import subprocess

import pytest
from pydantic import ValidationError

from tools.bench.engine import targets
from tools.bench.engine.types import Host, Target, TargetMount


def test_readonly_absolute_file_contract():
    for changes in (
        {"source": "relative"},
        {"destination": "relative"},
        {"read_only": False},
        {"source": "/tmp/a:b"},
    ):
        with pytest.raises(ValidationError):
            TargetMount(
                **(
                    {"source": "/tmp/ca.pem", "destination": "/certs/ca.pem", "read_only": True}
                    | changes
                )
            )


def test_backend_source_receipt_and_change(tmp_path):
    source = tmp_path / "CA with spaces.pem"
    source.write_text("certificate")
    target = Target(
        image="test",
        mounts=[TargetMount(source=str(source), destination="/certs/ca.pem", read_only=True)],
    )
    receipt = targets.mount_source_receipts(target, Host(transport="local"))
    assert receipt[0]["source"] == str(source)
    targets.verify_mount_sources(target, Host(transport="local"), receipt)
    source.chmod(0o600)
    with pytest.raises(ValueError, match="mount source changed"):
        targets.verify_mount_sources(target, Host(transport="local"), receipt)
    with pytest.raises(RuntimeError):
        targets.mount_source_receipts(
            target.model_copy(
                update={"mounts": [target.mounts[0].model_copy(update={"source": str(tmp_path)})]}
            ),
            Host(transport="local"),
        )


def test_remote_source_not_checked_locally(monkeypatch):
    host = Host(transport="ssh", host="backend")
    target = Target(
        image="test",
        mounts=[
            TargetMount(
                source="/remote only/CA.pem",
                destination="/certs/ca.pem",
                read_only=True,
                selinux="private",
            )
        ],
    )
    calls = []

    def execute(actual_host, argv, **kwargs):
        calls.append((actual_host, argv))
        return subprocess.CompletedProcess(
            argv,
            0,
            json.dumps({"source": target.mounts[0].source, "sha256": "a" * 64, "mode": 420}),
            "",
        )

    monkeypatch.setattr(targets, "execute_host", execute)
    assert targets.mount_source_receipts(target, host)[0]["mode"] == 420
    assert calls[0][0] == host
    assert calls[0][1][-1] == "/remote only/CA.pem"
    assert targets.mount_arguments(target) == ["--volume", "/remote only/CA.pem:/certs/ca.pem:ro,Z"]
    inspection = {
        "Mounts": [
            {
                "Type": "bind",
                "Source": "/remote only/CA.pem",
                "Destination": "/certs/ca.pem",
                "RW": False,
            }
        ]
    }
    targets.verify_mounts(target, inspection)
    inspection["Mounts"][0]["RW"] = True
    with pytest.raises(ValueError, match="mount mismatch"):
        targets.verify_mounts(target, inspection)


def test_mount_config_keeps_remote_path_and_opaque_parameters(tmp_path):
    from tools.bench.engine.config import _normalize

    data = {
        "targets": {
            "app": {
                "mounts": [
                    {
                        "source": "/not local/ca.pem",
                        "destination": "/certs/ca.pem",
                        "read_only": True,
                    }
                ]
            }
        },
        "workloads": {
            "w": {"parameters": {"source": "literal", "mounts": [{"source": "relative"}]}}
        },
    }
    assert _normalize(data, tmp_path) == data


def test_mount_identity_rejects_changed_bytes(tmp_path):
    from dataclasses import replace
    from pathlib import Path

    from tools.bench.engine.config import load_config, resolve_jobs
    from tools.bench.engine.workloads import prepare_workload

    job = resolve_jobs(load_config(Path("examples/http/queue.yaml")))[0]
    source = tmp_path / "ca.pem"
    source.write_text("old certificate")
    target = Target(
        image="test",
        mounts=[TargetMount(source=str(source), destination="/certs/ca.pem", read_only=True)],
    )
    job = job.model_copy(update={"target": target})
    prepared = prepare_workload(job.workload, tmp_path / "work")
    resolved = targets.ResolvedTarget(
        target,
        "test",
        "sha256:test",
        (),
        mount_sources=targets.mount_source_receipts(target, job.environment.backend),
    )
    identity = targets.prepared_execution_identity(job, 1, resolved, prepared)
    source.write_text("new certificate")
    with pytest.raises(ValueError, match="mount source changed"):
        targets.prepared_execution_identity(job, 1, resolved, prepared)
    changed = replace(
        resolved, mount_sources=targets.mount_source_receipts(target, job.environment.backend)
    )
    assert targets.prepared_execution_identity(job, 1, changed, prepared) != identity


def test_generator_source_ips_checked_on_generator_and_argv(monkeypatch):
    from tools.bench.engine.types import Environment

    env = Environment(
        backend=Host(transport="ssh", host="backend"),
        generator=Host(transport="ssh", host="generator"),
        source_ips=["10.0.0.10", "10.0.0.11"],
    )
    calls = []

    def execute(host, argv, **kwargs):
        calls.append((host, argv))
        return subprocess.CompletedProcess(
            argv,
            0,
            json.dumps([{"addr_info": [{"local": "10.0.0.10"}, {"local": "10.0.0.11"}]}]),
            "",
        )

    monkeypatch.setattr(targets, "execute_host", execute)
    targets.validate_source_ips(env)
    assert calls == [(env.generator, ["ip", "-j", "address", "show"])]
    assert targets.source_ip_arguments(env) == ["--local-ips", "10.0.0.10,10.0.0.11"]
    with pytest.raises(ValueError, match="not assigned"):
        targets.validate_source_ips(env.model_copy(update={"source_ips": ["10.0.0.12"]}))
    with pytest.raises(ValidationError):
        Environment(backend=env.backend, generator=env.generator, source_ips=["not-an-ip"])


def test_empty_additive_fields_preserve_identity_and_source_ips_bind_it():
    import hashlib
    from pathlib import Path

    from tools.bench.engine.config import load_config, resolve_jobs
    from tools.bench.engine.evidence import execution_identity
    from tools.bench.engine.stages import resolve_stages

    job = resolve_jobs(load_config(Path("examples/http/queue.yaml")))[0]
    provenance = {
        "image_id": "sha256:test",
        "workload_identity": "work",
        "build_input_identity": "build",
    }
    payload = job.model_dump(mode="json", by_alias=True)
    payload["target"].pop("mounts")
    payload["environment"].pop("source_ips")
    for key in ("criteria", "objectives", "test", "id"):
        payload.pop(key, None)
    payload["stages"] = [
        s.model_dump(mode="json", exclude={"evaluate"}) for s in resolve_stages(job, 1)
    ]
    payload["users"] = 1
    payload["prepared_inputs"] = provenance
    old = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    ).hexdigest()
    assert execution_identity(job, 1, provenance=provenance) == old
    configured = job.model_copy(
        update={"environment": job.environment.model_copy(update={"source_ips": ["10.0.0.10"]})}
    )
    assert execution_identity(configured, 1, provenance=provenance) != old
