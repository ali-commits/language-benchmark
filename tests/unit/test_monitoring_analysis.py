from monitoring.dashboard import build_dashboard
from monitoring.history import metric_values


def test_history_keeps_technical_validity_separate_from_chosen_threshold():
    row = {
        "qualification": "fail",
        "metrics": {
            "attempted": 100,
            "succeeded": 95,
            "mean": 40,
            "p999": 500,
            "authenticated_users": 95,
        },
        "achieved_operations_s": 10,
        "achieved_http_requests_s": 10,
        "successful_operations_s": 9.5,
        "target_users": 100,
        "authentication_fraction": 0.95,
        "authentication_gate": 0.90,
        "resources": {
            "phases": {"steady": {"cpu_percent_mean": 50, "memory_working_set_mib_peak": 200}}
        },
    }
    values = metric_values(row)
    assert values["benchmark_trial_qualified"] == 0
    assert values["benchmark_technical_valid"] == 1
    assert values["benchmark_operation_success_fraction"] == 0.95
    assert values["benchmark_authentication_gate_satisfied"] == 1
    assert values["benchmark_backend_cpu_percent_mean"] == 50
    assert values["benchmark_backend_memory_working_set_mib_peak"] == 200


def test_dashboard_threshold_and_resource_units_are_explicit():
    dashboard = build_dashboard()
    assert any(variable["name"] == "threshold" for variable in dashboard["templating"]["list"])
    panels = dashboard["panels"]
    assert any("100% = 2 vCPUs" in panel["title"] for panel in panels)
    assert any("working set" in panel["title"].lower() for panel in panels)
    assert any(
        "benchmark_technical_valid" in target.get("expr", "")
        and "$threshold" in target.get("expr", "")
        for panel in panels
        for target in panel.get("targets", [])
    )
