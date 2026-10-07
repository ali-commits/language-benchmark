"""Social fixture adapter: legacy guarded restore and runtime attestation."""

import json
import os
import time
from dataclasses import replace
from pathlib import Path

from ..fixture import verify_fixture
from ..models import FixtureManifest, NativeCell
from ..native_runtime import load_runtime_cell, native_image_metadata, validate_process_nofile
from ..reset import guard, restore_fixture
from ..runtime import validate_dependency_inventory, validate_worker_snapshots
from .evidence import immutable_json
from .targets import execute_host


class SocialAdapter:
    def __init__(self, spec):
        self.spec = spec
        fixture = spec.fixture
        if (
            not fixture
            or fixture.adapter != "social"
            or not fixture.dsn
            or not fixture.manifest
            or not fixture.allowed_database
        ):
            raise ValueError(
                "social fixture requires adapter, dsn env reference, manifest and allowed_database"
            )
        if not fixture.runtime_cell:
            raise ValueError("social adapter requires locked runtime_cell")
        self.fixture = fixture

    def _dsn(self):
        dsn = os.environ[self.fixture.dsn.env]
        guard(dsn, self.fixture.allowed_database)
        return dsn

    def prepare(self, context):
        self._dsn()
        manifest = FixtureManifest(**json.loads(self.fixture.manifest.read_text()))
        from .stages import resolve_stages

        stages = resolve_stages(context.job, context.users)
        peak = max(max(s.from_users, s.to_users) for s in stages)
        if manifest.counts.get("users", 0) < peak:
            raise ValueError("social fixture contains fewer accounts than target")
        load_runtime_cell(
            self.fixture.runtime_cell, Path(__file__).resolve().parents[3] / "runtimes"
        )

    def reset(self, context):
        return restore_fixture(self._dsn(), self.fixture.manifest, self.fixture.allowed_database)

    def verify(self, context):
        manifest = FixtureManifest(**json.loads(self.fixture.manifest.read_text()))
        if verify_fixture(self._dsn(), manifest):
            raise ValueError("social fixture fingerprints differ")

    def attest(self, context, container_id):
        host = context.environment.backend
        cell = load_runtime_cell(
            self.fixture.runtime_cell, Path(__file__).resolve().parents[3] / "runtimes"
        )
        info = json.loads(execute_host(host, ["docker", "inspect", container_id]).stdout)[0]
        image = json.loads(
            execute_host(host, ["docker", "image", "inspect", info["Image"]]).stdout
        )[0]
        receipt = {"cell": cell.id, "image_id": image["Id"]}
        if isinstance(cell, NativeCell):
            receipt["runtime"] = native_image_metadata(
                {
                    "id": image["Id"],
                    "os": image["Os"],
                    "architecture": image["Architecture"],
                    "labels": image["Config"].get("Labels", {}),
                },
                cell,
                info["Image"],
            )
        else:
            cell = replace(
                cell,
                dependency_lock=str(Path(__file__).resolve().parents[3] / cell.dependency_lock),
            )
            code = "import pathlib,json; print(json.dumps([json.loads(p.read_text()) for p in pathlib.Path('/tmp/bench-runtime').glob('*.json')]))"
            deadline = time.monotonic() + context.job.target.readiness.timeout
            snapshots = []
            while time.monotonic() < deadline:
                snapshots = json.loads(
                    execute_host(
                        host, ["docker", "exec", container_id, "python", "-c", code]
                    ).stdout
                )
                if len(snapshots) == cell.workers:
                    break
                time.sleep(0.1)
            reasons = validate_worker_snapshots(cell, snapshots) + validate_dependency_inventory(
                cell, snapshots
            )
            if any(s.get("run_id") != context.trial_id for s in snapshots):
                reasons.append("runtime trial identity mismatch")
            if reasons:
                raise ValueError("social runtime attestation failed")
            receipt["workers"] = snapshots
        limits = execute_host(
            host,
            [
                "docker",
                "exec",
                container_id,
                "sh",
                "-c",
                'for path in /proc/[0-9]*/limits; do cat "$path" 2>/dev/null || true; done',
            ],
        ).stdout
        receipt["nofile"] = validate_process_nofile(limits)
        immutable_json(context.directory / "social-runtime.json", receipt)

    def authentication(self, samples, target):
        accounts = set()
        for sample in samples:
            if sample.metric == "authenticated_account" and sample.value == 1:
                account = sample.provider_fields.get("account")
                if isinstance(account, str) and account:
                    accounts.add(account.lower())
        return {
            "distinct_accounts": len(accounts),
            "target_accounts": target,
            "fraction": len(accounts) / target if target else None,
        }


def social_adapter(spec):
    return SocialAdapter(spec)
