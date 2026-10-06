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


def test_generator_window_covers_authentication_and_steady_activity():
    from bench.journeys import generator_window

    result = {
        "measurement_started_epoch": 300,
        "measurement_finished_epoch": 600,
        "journey_metrics": {"activity_started_epoch": 100, "activity_finished_epoch": 610},
    }
    assert generator_window(result) == (100, 610)


def test_missing_interval_on_interrupted_probe_is_safe():
    from bench.journeys import generator_window
    from bench.user_capacity import await_rds

    result = {"status": "interrupted", "measurement_started_epoch": 100}
    assert generator_window(result) == (None, None)
    await_rds({"mode": "aws"}, result, __import__("pathlib").Path("/not-needed"))


def test_journey_diagnostics_include_http_requests_and_completed_cycles(tmp_path):
    p = tmp_path / "raw.gz"
    rows = [
        point("http_reqs", 1),
        point("user_journeys_completed", 1),
        point("operations_attempted", 1),
        point("operations_succeeded", 1),
        point("users_authenticated", 1, "drain"),
    ]
    with gzip.open(p, "wt") as f:
        f.write("\n".join(json.dumps(x) for x in rows))
    m = read_journey_metrics(p)
    assert m["http_requests"] == 1 and m["completed_journeys"] == 1
    assert m["authenticated_users"] == 0


def test_generator_socket_exhaustion_invalidates_trial_even_if_responses_pass(tmp_path):
    p = tmp_path / "raw.gz"
    rows = [
        point("generator_resource_errors", 1, "ramp"),
        point("operations_attempted", 100),
        point("operations_succeeded", 100),
        point("journey_active_users", 10),
        point("users_authenticated", 10, "ramp"),
    ]
    with gzip.open(p, "wt") as f:
        f.write("\n".join(json.dumps(x) for x in rows))
    metrics = read_journey_metrics(p)
    assert metrics["generator_resource_errors"] == 1
    assert classify_journey(metrics, 10, []) == "invalid"


def test_activity_windows_keep_extrema_when_points_arrive_out_of_order(tmp_path):
    p = tmp_path / "raw.gz"
    rows = [
        point("operations_attempted", 1, time="2026-10-04T12:00:20Z"),
        point("users_authenticated", 1, "ramp", "2026-10-04T12:00:00Z"),
        point("operations_succeeded", 1, time="2026-10-04T12:00:10Z"),
        point("operations_attempted", 1, "drain", "2026-10-04T12:01:00Z"),
    ]
    with gzip.open(p, "wt") as f:
        f.write("\n".join(json.dumps(x) for x in rows))
    m = read_journey_metrics(p)
    assert m["activity_finished_epoch"] - m["activity_started_epoch"] == 20
    assert m["steady_finished_epoch"] - m["steady_started_epoch"] == 10


def test_phase_endpoint_latency_includes_failures_and_configured_throughput(tmp_path):
    p = tmp_path / "raw.gz"
    rows = []
    for op, phase, value, time in [
        ("login", "ramp", 100, "2026-10-04T12:00:00Z"),
        ("feed", "steady", 10, "2026-10-04T12:03:00Z"),
        ("feed", "steady", 5000, "2026-10-04T12:06:59Z"),
    ]:
        for metric, v in [
            ("operations_attempted", 1),
            ("operation_duration", value),
            ("http_reqs", 1),
        ]:
            row = point(metric, v, phase, time)
            row["data"]["tags"]["operation"] = op
            rows.append(row)
    for metric, value in [("operations_succeeded", 1), ("journey_active_users", 10)]:
        rows.append(point(metric, value, time="2026-10-04T12:03:00Z"))
    rows.append(point("users_authenticated", 10, "ramp"))
    with gzip.open(p, "wt") as f:
        f.write("\n".join(json.dumps(x) for x in rows))
    m = read_journey_metrics(p, steady_seconds=240)
    assert m["successful_operations_s"] == 1 / 240
    assert m["attempted_operations_s"] == 2 / 240
    assert m["mean"] == 2505
    assert m["p999"] > m["p99"]
    assert m["endpoints"]["feed"]["mean"] == 2505
    assert m["phases"]["ramp"]["endpoints"]["login"]["mean"] == 100
    assert m["phases"]["steady"]["attempted"] == 2
    assert m["authentication"]["login_attempted"] == 1
    assert m["steady_seconds"] == 240


def test_authentication_gate_independent_of_operation_threshold():
    metrics = {
        "attempted": 1000,
        "succeeded": 980,
        "authenticated_users": 95,
        "active_users_max": 100,
    }
    assert classify_journey(metrics, 100, [], 0.98, authentication_fraction=0.90) == "pass"
    assert classify_journey(metrics, 100, [], 0.98, authentication_fraction=0.99) == "fail"
