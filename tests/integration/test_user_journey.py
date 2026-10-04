import json
import os
import subprocess
from pathlib import Path


def test_real_login_feed_like_reply_journey_emits_phase_metrics(database, tmp_path, monkeypatch):
    import socket
    import threading
    import time

    import uvicorn
    from baseline_api.main import create_app
    from bench.journeys import prepare_journey_data, read_journey_metrics
    from bench.models import FixtureSpec
    from bench.seed import seed_database

    monkeypatch.setenv("DATABASE_URL", database)
    monkeypatch.setenv("JWT_SECRET", "journey-test-signing-secret-at-least-32-bytes")
    monkeypatch.setenv("WORKERS", "1")
    monkeypatch.setenv("SEED_PASSWORD", "journey-test-password")
    monkeypatch.setenv("SEED_HASH_WORKERS", "1")
    seed_database(database, FixtureSpec(users=4, posts=40, follows_per_user=2, likes=4, comments=4))
    creds = tmp_path / "credentials.json"
    creds.write_text(
        json.dumps(
            [{"username": f"user_{i:05}", "password": "journey-test-password"} for i in range(1, 5)]
        )
    )
    prepare_journey_data(creds, 4, tmp_path / "load-data.json")
    cfg = {
        "users": 4,
        "ramp_seconds": 1,
        "settle_seconds": 1,
        "hold_seconds": 4,
        "wait_min": 0.01,
        "wait_max": 0.03,
        "timeout_seconds": 5,
        "tags": {"cell": "smoke", "trial": "smoke"},
    }
    (tmp_path / "scenario.json").write_text(json.dumps(cfg))
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    server = uvicorn.Server(
        uvicorn.Config(create_app(), host="127.0.0.1", port=port, log_level="error")
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    try:
        for _ in range(100):
            if server.started:
                break
            time.sleep(0.05)
        r = subprocess.run(
            [
                "docker",
                "run",
                "--rm",
                "--network",
                "host",
                "--user",
                str(os.getuid()),
                "-e",
                f"BASE_URL=http://127.0.0.1:{port}",
                "-e",
                "LOAD_DATA=/inputs/load-data.json",
                "-e",
                "SCENARIO_CONFIG=/inputs/scenario.json",
                "-v",
                str(Path("benchmarks/k6").resolve()) + ":/scripts:ro",
                "-v",
                str(tmp_path) + ":/inputs:ro",
                "-v",
                str(tmp_path) + ":/output",
                "grafana/k6:1.4.2",
                "run",
                "--out",
                "json=/output/metrics.json.gz",
                "/scripts/user-journey.js",
            ],
            capture_output=True,
            text=True,
            timeout=90,
        )
        assert r.returncode == 0, r.stdout + r.stderr
        m = read_journey_metrics(tmp_path / "metrics.json.gz")
        assert m["authenticated_users"] == 4
        assert m["active_users_max"] == 4
        assert m["attempted"] > 0 and m["attempted"] == m["succeeded"]
        import psycopg

        with psycopg.connect(database) as c:
            assert c.execute("SELECT count(*) FROM comments").fetchone()[0] > 4
    finally:
        server.should_exit = True
        thread.join(10)
