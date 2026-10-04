from datetime import datetime, timezone

from bench.telemetry import REQUIRED_RDS_METRICS, validate_rds_evidence


def complete():
    return {
        name: [{"Timestamp": datetime.fromtimestamp(100, timezone.utc).isoformat(), "Average": 0}]
        for name in REQUIRED_RDS_METRICS
    }


def test_missing_storage_evidence_cannot_qualify():
    data = complete()
    data["WriteIOPS"] = []
    assert validate_rds_evidence(data, 100, 200)


def test_outside_measurement_points_are_rejected():
    assert validate_rds_evidence(complete(), 1000, 1100)


def test_failover_event_is_flagged():
    data = complete()
    data["events"] = [
        {
            "Message": "Database instance failover completed",
            "Date": datetime.fromtimestamp(150, timezone.utc).isoformat(),
        }
    ]
    assert validate_rds_evidence(data, 100, 200)


def test_complete_quiet_measurement_evidence_is_accepted():
    assert not validate_rds_evidence(complete(), 100, 200)
