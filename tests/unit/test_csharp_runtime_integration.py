"""C# reuses native attestation and budgets, independently of app availability."""

import copy
import json
from pathlib import Path

import pytest
from bench.models import NativeCell, RuntimeCell
from bench.native_runtime import (
    BUDGET,
    LABEL_FIELDS,
    LABEL_PREFIX,
    load_runtime_cell,
    load_runtime_cells,
    native_image_metadata,
    validate_native_container,
)

CSHARP = {
    "id": "csharp-aspnet-core",
    "language": "csharp",
    "runtime_version": "10.0.12",
    "framework": "aspnet-core",
    "framework_version": "10.0.12",
    "image": "lb-csharp-aspnet:10.0.12",
    "dockerfile": "backends/csharp/aspnet-core/Dockerfile",
    "workers": 1,
    "connections_per_worker": 20,
    "topology": "One process; Npgsql20; bounded hash2; server GC",
}
IMAGE = "sha256:" + "c" * 64


def registry(root, data=None):
    (root / "python").mkdir(parents=True)
    (root / "csharp").mkdir()
    (root / "python/lock.json").write_text(Path("runtimes/python/lock.json").read_text())
    (root / "csharp/lock.json").write_text(
        json.dumps({"budget": BUDGET, "cells": [data or CSHARP]})
    )


def test_csharp_registry_is_native_and_keeps_python_selection_independent(tmp_path):
    registry(tmp_path)
    cell = load_runtime_cell(CSHARP["id"], tmp_path)
    assert isinstance(cell, NativeCell)
    assert (cell.framework, cell.workers, cell.connections_per_worker) == ("aspnet-core", 1, 20)
    assert isinstance(load_runtime_cell("python-baseline-3.10.22", tmp_path), RuntimeCell)
    assert [c.id for c in load_runtime_cells(tmp_path) if isinstance(c, NativeCell)] == [
        CSHARP["id"]
    ]


@pytest.mark.parametrize(
    "change",
    [{"workers": 2}, {"connections_per_worker": 10}, {"workers": True}, {"framework": "express"}],
)
def test_csharp_rejects_topology_changes(change):
    with pytest.raises(ValueError, match="uncontrolled native"):
        NativeCell(**{**CSHARP, **change})


def test_csharp_uses_same_six_image_labels_and_native_resource_guard():
    cell = NativeCell(**CSHARP)
    labels = {LABEL_PREFIX + k.replace("_", "-"): str(getattr(cell, k)) for k in LABEL_FIELDS}
    image = native_image_metadata(
        {
            "id": IMAGE,
            "os": "linux",
            "architecture": "amd64",
            "labels": labels,
            "environment": ["JWT_SECRET=private"],
        },
        cell,
        IMAGE,
    )
    assert len(image["labels"]) == 6 and "private" not in json.dumps(image)
    container = {
        "id": "native",
        "image": IMAGE,
        "running": True,
        "nano_cpus": 2000000000,
        "memory": 3221225472,
        "memory_swap": 3221225472,
        "network_mode": "host",
        "run_id": "fresh",
        "cell_id": cell.id,
        "ulimits": [{"Name": "nofile", "Soft": 250000, "Hard": 250000}],
        "mounts": [{"Destination": "/certs/rds-ca.pem", "RW": False}],
    }
    assert validate_native_container(container, cell, "fresh", IMAGE)["image"] == IMAGE
    for change in (
        {"nano_cpus": 4000000000},
        {"memory_swap": -1},
        {"ulimits": [{"Name": "nofile", "Soft": 25000, "Hard": 25000}]},
        {"mounts": [{"Destination": "/certs/rds-ca.pem", "RW": True}]},
    ):
        with pytest.raises(ValueError):
            validate_native_container({**container, **change}, cell, "fresh", IMAGE)


def test_csharp_cli_choice_and_local_listing(tmp_path, monkeypatch, capsys):
    from bench import languages

    registry(tmp_path / "runtimes")
    monkeypatch.chdir(tmp_path)
    assert "csharp" in languages.LANGUAGES
    assert languages.cells(["csharp"])[0]["id"] == CSHARP["id"]
    monkeypatch.setattr("sys.argv", ["bench.languages", "list", "--language", "csharp"])
    languages.main()
    assert json.loads(capsys.readouterr().out)[0]["runtime_version"] == "10.0.12"


def test_local_csharp_listing_rejects_invalid_topology_even_with_aggregate_twenty(
    tmp_path, monkeypatch
):
    from bench import languages

    bad = copy.deepcopy(CSHARP)
    bad.update(workers=2, connections_per_worker=10)
    registry(tmp_path / "runtimes", bad)
    monkeypatch.chdir(tmp_path)
    with pytest.raises(ValueError, match="uncontrolled native"):
        languages.cells(["csharp"])
