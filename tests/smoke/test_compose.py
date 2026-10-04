import json
import subprocess


def rendered(path):
    return json.loads(
        subprocess.check_output(
            ["docker", "compose", "-f", path, "config", "--format", "json"], text=True
        )
    )


def test_aws_compose_has_no_local_db_or_k6():
    assert set(rendered("compose.aws.yaml")["services"]) == {"backend"}


def test_load_compose_has_no_backend_or_db():
    assert set(rendered("compose.load.yaml")["services"]) == {"k6"}


def test_backend_has_two_cpu_three_gib_no_swap_budget():
    b = rendered("compose.aws.yaml")["services"]["backend"]
    assert (
        b["cpus"] == 2.0
        and int(b["mem_limit"]) == 3221225472
        and int(b["memswap_limit"]) == 3221225472
    )
