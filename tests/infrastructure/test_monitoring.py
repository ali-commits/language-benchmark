from monitoring.dashboard import build_dashboard


def test_live_and_history_panels_keep_trial_and_runtime_filters():
    d = build_dashboard()
    names = {v["name"] for v in d["templating"]["list"]}
    assert {"cell", "trial"} <= names
    assert any("Historical" in p["title"] for p in d["panels"])
    assert any("Latency" in p["title"] for p in d["panels"])


def test_rds_backfill_does_not_hide_load_or_cleanup_failures():
    from monitoring.refresh_rds import replace_evidence_reasons

    assert replace_evidence_reasons(
        ["generator overloaded", "RDS CPU evidence missing", "cleanup failed"], []
    ) == ["generator overloaded", "cleanup failed"]
