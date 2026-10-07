"""Native long-lived worker/media/isolation checks; disposable database only."""

import concurrent.futures
import json
import os
import uuid
import httpx
import psycopg

url = os.environ["TARGET_URL"]
dsn = os.environ["TEST_DATABASE_URL"]
assert "octane_test" in dsn, "dedicated disposable Octane database required"
prefix = "oct_" + uuid.uuid4().hex[:10]
with httpx.Client(base_url=url, timeout=30) as c:
    assert c.get("/index.php").status_code == 404
    users = []
    for suffix in ("a", "b"):
        r = c.post(
            "/api/v1/auth/register",
            json={"username": prefix + suffix, "password": "valid-password-long"},
        )
        assert r.status_code == 201, r.text
        user = r.json()
        token = c.post(
            "/api/v1/auth/login",
            json={"username": prefix + suffix, "password": "valid-password-long"},
        ).json()["access_token"]
        users.append((user, {"Authorization": "Bearer " + token}))
    for route, body, headers in [
        (
            "/api/v1/auth/register",
            {"username": prefix + "bad", "password": "valid-password-long"},
            {},
        ),
        ("/api/v1/auth/login", {"username": prefix + "a", "password": "valid-password-long"}, {}),
        ("/api/v1/posts", {"content": prefix + "-media-body"}, users[0][1]),
    ]:
        for media in ("text/plain", None):
            h = dict(headers)
            if media:
                h["Content-Type"] = media
            r = c.post(route, content=json.dumps(body), headers=h)
            assert (
                r.status_code == 422 and r.json()["detail"][0]["type"] == "model_attributes_type"
            ), r.text
        with psycopg.connect(dsn) as check_db:
            if route.endswith("/register"):
                assert (
                    check_db.execute(
                        "SELECT count(*) FROM users WHERE username=%s", (body["username"],)
                    ).fetchone()[0]
                    == 0
                )
            if route.endswith("/posts"):
                assert (
                    check_db.execute(
                        "SELECT count(*) FROM posts WHERE content=%s", (body["content"],)
                    ).fetchone()[0]
                    == 0
                )
        h = {**headers, "Content-Type": "application/vendor+json"}
        r = c.post(route, content=json.dumps(body), headers=h)
        assert r.status_code in (200, 201), r.text

    # Force concurrent scheduling across the bounded pool, then check real session reuse.
    def ready(_):
        return httpx.get(url + "/health/ready", timeout=30).status_code

    with concurrent.futures.ThreadPoolExecutor(max_workers=40) as pool:
        assert all(x == 200 for x in pool.map(ready, range(100)))
    with psycopg.connect(dsn) as db:

        def sessions():
            return {
                r[0]
                for r in db.execute(
                    "SELECT pid FROM pg_stat_activity WHERE datname=current_database() AND application_name='lb-backend' AND pid<>pg_backend_pid()"
                )
            }

        before = sessions()
        assert 1 <= len(before) <= 20, before
        for i in range(100):
            user, h = users[i % 2]
            r = c.get("/api/v1/users/me", headers=h)
            assert r.status_code == 200 and r.json() == user, r.text
            assert c.get("/api/v1/users/me").status_code == 401
            r = c.get("/api/v1/feed", headers=h)
            assert r.status_code == 200 and all(
                p["author"]["id"] == user["id"] for p in r.json()["items"]
            )
            assert c.post("/api/v1/posts", json={"content": 42}, headers=h).status_code == 422
        after = sessions()
        assert before <= after and len(after) <= 20, (before, after)
        assert (
            db.execute(
                "SELECT count(*) FROM pg_stat_activity WHERE datname=current_database() AND application_name='lb-backend' AND xact_start IS NOT NULL AND state='idle in transaction'"
            ).fetchone()[0]
            == 0
        )
        # Kill only this disposable app's sessions; safe reads must transparently reconnect.
        db.execute(
            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname=current_database() AND application_name='lb-backend' AND pid<>pg_backend_pid()"
        )
        db.commit()
        assert all(ready(i) == 200 for i in range(40))
        assert c.get("/api/v1/users/me", headers=users[0][1]).json() == users[0][0]
    print(
        json.dumps(
            {
                "status": "pass",
                "media_types": 9,
                "alternating_requests": 400,
                "physical_connections_before": len(before),
                "physical_connections_after": len(after),
                "connection_cap": 20,
                "request_isolation": True,
                "read_reconnect": True,
                "idle_transactions": 0,
            }
        )
    )
