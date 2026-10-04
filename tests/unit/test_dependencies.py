from bench.models import RuntimeCell
from bench.runtime import validate_dependency_inventory


def test_wrong_modern_dependency_versions_are_rejected():
    c = RuntimeCell(
        id="modern",
        python="3.15.0rc3",
        app="modern",
        dependency_lock="backends/python/modern/requirements.lock",
    )
    assert validate_dependency_inventory(c, [{"dependencies": {"pydantic": "2.13.5"}}])
