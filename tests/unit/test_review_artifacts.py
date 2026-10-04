import gzip
import json

from bench.report import read_metrics, summarize_campaign


def test_truncated_gzip_retains_readable_points_and_marks_unknown(tmp_path):
    points = [
        {
            "type": "Point",
            "metric": "operations_attempted",
            "data": {"value": 1, "tags": {"operation": "feed"}},
        }
    ]
    p = tmp_path / "metrics.gz"
    data = gzip.compress(("\n".join(json.dumps(x) for x in points) + "\n").encode())
    p.write_bytes(data[:-8])
    result = read_metrics(p)
    assert result["attempted"] == 1 and result["incomplete"] is True


def test_manifest_only_trial_remains_in_report(tmp_path):
    d = tmp_path / "trial"
    d.mkdir()
    m = {
        "trial": {"id": "trial", "campaign_id": "campaign", "cell": "python-test", "rate": 25},
        "campaign": {"mode": "local", "measure_seconds": 300},
        "label": "LOCAL SMOKE",
    }
    (d / "manifest.json").write_text(json.dumps(m))
    report = summarize_campaign(tmp_path)
    assert len(report["trials"]) == 1
    assert report["trials"][0]["qualification"] == "unknown"
    assert report["trials"][0]["status"] == "incomplete"


def test_private_pilot_configuration_never_enters_shareable_results(tmp_path):
    from bench.cli import private_pilot_config

    source = tmp_path / "private.json"
    output = tmp_path / "results"
    source.write_text(
        json.dumps(
            {
                "backend_database_url": "postgresql://user:private-pilot-value@host/db",
                "max_rate": 100,
            }
        )
    )
    with private_pilot_config(source, output) as executable:
        assert "private-pilot-value" in executable.read_text()
        assert executable.stat().st_mode & 0o777 == 0o600
        assert executable.parent != output
        assert all("private-pilot-value" not in p.read_text() for p in output.iterdir())
    assert not executable.exists()
