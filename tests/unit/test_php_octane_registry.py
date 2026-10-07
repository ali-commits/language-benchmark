import hashlib
import json
from pathlib import Path

import pytest
from bench.models import NativeCell
from bench.native_runtime import (
    BUDGET,
    LABEL_FIELDS,
    LABEL_PREFIX,
    load_runtime_cell,
    native_image_metadata,
    registry_metadata,
)

OCTANE = {
    "id": "php-laravel-octane",
    "language": "php",
    "runtime_version": "8.5.11",
    "framework": "laravel-octane",
    "framework_version": "2.21.0",
    "image": "lb-php-laravel-octane:8.5.11",
    "dockerfile": "backends/php/laravel-octane/Dockerfile",
    "workers": 20,
    "connections_per_worker": 1,
    "topology": "RoadRunner2025.1.15; Laravel13.35.0;20longlivedworkers;PDO1each",
}
FPM_SHA = "c7860e450971ca13fb2e54915f6201a9ba5725b2f5b178ed1f6f8d2b71830133"


def setup(root):
    (root / "python").mkdir(parents=True)
    (root / "php").mkdir()
    (root / "php-octane").mkdir()
    (root / "python/lock.json").write_bytes(Path("runtimes/python/lock.json").read_bytes())
    (root / "php/lock.json").write_bytes(Path("runtimes/php/lock.json").read_bytes())
    (root / "php-octane/lock.json").write_text(json.dumps({"budget": BUDGET, "cells": [OCTANE]}))


def test_variant_registry_keeps_php_language_and_uses_its_own_provenance(tmp_path, monkeypatch):
    setup(tmp_path / "runtimes")
    monkeypatch.chdir(tmp_path)
    c = load_runtime_cell(OCTANE["id"])
    assert c.language == "php" and c.framework == "laravel-octane"
    assert registry_metadata(c)["path"] == "runtimes/php-octane/lock.json"
    assert registry_metadata(load_runtime_cell("php-laravel"))["path"] == "runtimes/php/lock.json"
    assert hashlib.sha256(Path("runtimes/php/lock.json").read_bytes()).hexdigest() == FPM_SHA


def test_cli_selects_only_octane_variant_and_preserves_fpm_selection(tmp_path, monkeypatch, capsys):
    from bench import languages

    setup(tmp_path / "runtimes")
    monkeypatch.chdir(tmp_path)
    assert languages.cells(["php-octane"])[0]["id"] == OCTANE["id"]
    assert languages.cells(["php"])[0]["framework"] == "laravel"
    monkeypatch.setattr("sys.argv", ["bench.languages", "list", "--languages", "php-octane"])
    languages.main()
    assert json.loads(capsys.readouterr().out)[0]["language"] == "php"


@pytest.mark.parametrize(
    "change",
    [
        {"workers": 2, "connections_per_worker": 10},
        {"workers": True},
        {"language": "php-octane"},
        {"framework": "laravel-octane", "id": "php-laravel"},
    ],
)
def test_variant_topology_is_narrow_and_budget_remains20(change):
    with pytest.raises(ValueError):
        NativeCell(**{**OCTANE, **change})


def test_octane_image_labels_attest_variant_without_changing_language():
    c = NativeCell(**OCTANE)
    image = "sha256:" + "a" * 64
    labels = {LABEL_PREFIX + k.replace("_", "-"): str(getattr(c, k)) for k in LABEL_FIELDS}
    assert (
        native_image_metadata(
            {"id": image, "os": "linux", "architecture": "amd64", "labels": labels}, c, image
        )["labels"][LABEL_PREFIX + "language"]
        == "php"
    )
    labels[LABEL_PREFIX + "framework"] = "laravel"
    with pytest.raises(ValueError):
        native_image_metadata(
            {"id": image, "os": "linux", "architecture": "amd64", "labels": labels}, c, image
        )


def test_octane_cannot_be_inserted_into_original_php_registry(tmp_path):
    setup(tmp_path / "runtimes")
    (tmp_path / "runtimes/php/lock.json").write_text(
        json.dumps({"budget": BUDGET, "cells": [OCTANE]})
    )
    with pytest.raises(ValueError, match="baseline PHP"):
        load_runtime_cell(OCTANE["id"], tmp_path / "runtimes")


def test_octane_cli_rejects_top_level_budget_drift(tmp_path, monkeypatch):
    from bench import languages

    setup(tmp_path / "runtimes")
    monkeypatch.chdir(tmp_path)
    p = Path("runtimes/php-octane/lock.json")
    data = json.loads(p.read_text())
    data["budget"]["cpus"] = 4
    p.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="budget"):
        languages.cells(["php-octane"])
