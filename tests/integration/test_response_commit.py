import importlib

import psycopg
import pytest
from fastapi.testclient import TestClient


@pytest.mark.parametrize("module", ["baseline_api.main", "modern_api.main", "modern_api.threaded"])
def test_registration_commits_before_response_headers(database, monkeypatch, module):
    monkeypatch.setenv("DATABASE_URL", database)
    monkeypatch.setenv("JWT_SECRET", "test-signing-key-that-has-at-least-32-bytes")
    monkeypatch.setenv("WORKERS", "1")
    app = importlib.import_module(module).create_app()
    observed = []

    class ObserveCommit:
        def __init__(self, app):
            self.app = app

        async def __call__(self, scope, receive, send):
            async def observe(message):
                if (
                    scope.get("path") == "/api/v1/auth/register"
                    and message["type"] == "http.response.start"
                ):
                    with psycopg.connect(database) as c:
                        observed.append(
                            c.execute(
                                "SELECT count(*) FROM users WHERE username='alice'"
                            ).fetchone()[0]
                        )
                await send(message)

            await self.app(scope, receive, observe)

    with TestClient(ObserveCommit(app)) as client:
        r = client.post(
            "/api/v1/auth/register", json={"username": "alice", "password": "valid-user-password"}
        )
        assert r.status_code == 201
    assert observed == [1]
