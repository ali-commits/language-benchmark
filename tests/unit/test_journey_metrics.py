import gzip
import json

from bench.journeys import classify_journey, read_journey_metrics


def point(name, value, phase="steady", time="2026-10-04T12:00:00Z"):
    return {
        "type": "Point",
        "metric": name,
        "data": {"value": value, "time": time, "tags": {"phase": phase}},
    }


def test_capacity_uses_actual_users_and_steady_operations(tmp_path):
    p = tmp_path / "raw.gz"
    rows = [
        point("operations_attempted", 100, "ramp"),
        point("operations_succeeded", 50, "ramp"),
        point("users_authenticated", 10, "ramp"),
        point("journey_active_users", 10),
        point("operations_attempted", 100),
        point("operations_succeeded", 100),
        point("operation_duration", 30),
    ]
    with gzip.open(p, "wt") as f:
        f.write("\n".join(json.dumps(x) for x in rows))
    m = read_journey_metrics(p)
    assert m["attempted"] == 100 and m["succeeded"] == 100
    assert m["authenticated_users"] == 10 and m["active_users_max"] == 10
    assert classify_journey(m, 10, []) == "pass"


def test_unresponsive_users_fail_without_using_old_200ms_threshold():
    m = {
        "attempted": 100,
        "succeeded": 99,
        "authenticated_users": 10,
        "active_users_max": 10,
        "p95": 1000,
        "incomplete": False,
    }
    assert classify_journey(m, 10, []) == "pass"
    assert classify_journey({**m, "succeeded": 98}, 10, []) == "fail"
    assert classify_journey({**m, "authenticated_users": 5}, 10, []) == "fail"


def test_generator_or_partial_data_invalidates_search_probe():
    m = {
        "attempted": 100,
        "succeeded": 0,
        "authenticated_users": 0,
        "active_users_max": 10,
        "incomplete": False,
    }
    assert classify_journey(m, 10, ["generator CPU saturated"]) == "invalid"
    assert classify_journey({**m, "incomplete": True}, 10, []) == "invalid"


def test_insufficient_credentials_are_rejected(tmp_path):
    from bench.journeys import prepare_journey_data

    p = tmp_path / "credentials.json"
    p.write_text(
        json.dumps(
            [
                {"username": "alice", "password": "password"},
                {"username": "alice", "password": "password"},
            ]
        )
    )
    import pytest

    with pytest.raises(ValueError):
        prepare_journey_data(p, 2, tmp_path / "out.json")
