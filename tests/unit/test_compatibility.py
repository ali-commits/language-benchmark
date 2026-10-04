import pytest
from bench.compatibility import validate_runtime_lock

def test_runtime_lock_requires_exact_release_digest_and_checksum():
    with pytest.raises(ValueError):
        validate_runtime_lock({"base": "debian:latest", "runtimes": []})

def test_unsupported_cell_retains_reason():
    from bench.models import RuntimeCell
    c = RuntimeCell(id="bad", python="3.10.22", app="baseline", build="standard", supported=False, reasons=["dependency missing"])
    assert c.reasons == ["dependency missing"]

def test_baseline_dependencies_do_not_vary_by_python():
    import json
    from pathlib import Path
    data = json.loads(Path("docker/runtime-lock.json").read_text())
    cells = [c for c in data["cells"] if c["app"] == "baseline"]
    assert len({c["dependency_lock"] for c in cells}) == 1
    assert {c["python"].split(".")[1] for c in cells} == {"10","11","12","13","14","15"}
