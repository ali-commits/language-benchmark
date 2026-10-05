"""Public behavior tests shared by Python and external language implementations."""

import base64
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import jwt
import psycopg
import pytest
from argon2 import PasswordHasher
from tests_support import actor

PASSWORD = "correct horse battery staple"

SECRET = "test-secret-that-has-at-least-32-bytes"


def token(user, **overrides):
    now = int(time.time())
    claims = {
        "sub": str(user),
        "iat": now,
        "exp": now + 3600,
        "iss": "language-benchmark",
        "aud": "social-api",
    }
    claims.update(overrides)
    return jwt.encode(claims, SECRET, algorithm="HS256")


def test_seeded_python_phc_and_external_jwt_are_accepted(api_client, database):
    hasher = PasswordHasher(time_cost=3, memory_cost=65536, parallelism=1, hash_len=32, salt_len=16)
    with psycopg.connect(database) as connection:
        user = connection.execute(
            "INSERT INTO users(username,password_hash) VALUES (%s,%s) RETURNING id",
            ("seeded", hasher.hash(PASSWORD)),
        ).fetchone()[0]
    response = api_client.post(
        "/api/v1/auth/login", json={"username": "SEEDED", "password": PASSWORD}
    )
    assert response.status_code == 200, response.text
    issued = jwt.decode(
        response.json()["access_token"],
        SECRET,
        algorithms=["HS256"],
        issuer="language-benchmark",
        audience="social-api",
    )
    assert issued["sub"] == str(user) and type(issued["iat"]) is int and type(issued["exp"]) is int
    assert response.json()["token_type"] == "bearer" and response.json()["expires_in"] == 3600
    assert api_client.get(
        "/api/v1/users/me", headers={"Authorization": "Bearer " + token(user)}
    ).json() == {"id": user, "username": "seeded"}


@pytest.mark.parametrize(
    "claims",
    [
        {"exp": 1},
        {"iat": int(time.time()) + 3600},
        {"iss": "wrong"},
        {"aud": "wrong"},
        {"sub": "0"},
        {"sub": "not-a-number"},
    ],
)
def test_invalid_jwt_claims_are_uniform_401(api_client, claims):
    user, _ = actor(api_client, "alice")
    r = api_client.get(
        "/api/v1/users/me", headers={"Authorization": "Bearer " + token(user, **claims)}
    )
    assert r.status_code == 401 and r.json() == {"detail": "invalid credentials"}
    assert r.headers.get("www-authenticate") == "Bearer"


def test_registered_hash_policy_and_normalization(api_client, database):
    _, headers = actor(api_client, "ALICE")
    with psycopg.connect(database) as c:
        row = c.execute("SELECT username,password_hash FROM users").fetchone()
    assert row[0] == "alice" and row[1].startswith("$argon2id$v=19$m=65536,t=3,p=1$")
    parts = row[1].split("$")
    assert len(base64.b64decode(parts[-2] + "=" * (-len(parts[-2]) % 4))) == 16
    assert len(base64.b64decode(parts[-1] + "=" * (-len(parts[-1]) % 4))) == 32
    assert api_client.get("/api/v1/users/me", headers=headers).json()["username"] == "alice"


def test_precise_cursor_reads_every_microsecond_and_bigint_id(api_client, database):
    user, headers = actor(api_client, "alice")
    ids = [9007199254740993, 9007199254740994, 9007199254740995]
    with psycopg.connect(database) as c:
        for i, post_id in enumerate(ids):
            stamp = datetime(2026, 1, 1, 0, 0, 0, 100 + i, tzinfo=timezone.utc)
            c.execute(
                "INSERT INTO posts(id,author_id,content,created_at) VALUES (%s,%s,%s,%s)",
                (post_id, user, str(i), stamp),
            )
    page = api_client.get("/api/v1/feed", params={"limit": 1}, headers=headers).json()
    assert page["items"][0]["id"] == ids[2]
    first = page["items"][0]
    assert datetime.fromisoformat(first["created_at"].replace("Z", "+00:00")).microsecond == 102
    # Use an independently Python-encoded cursor, rather than trusting server encoding.
    cursor = (
        base64.urlsafe_b64encode(
            json.dumps(["2026-01-01T00:00:00.000102+00:00", ids[2]], separators=(",", ":")).encode()
        )
        .decode()
        .rstrip("=")
    )
    second = api_client.get(
        "/api/v1/feed", params={"limit": 1, "cursor": cursor}, headers=headers
    ).json()
    assert second["items"][0]["id"] == ids[1]
    third = api_client.get(
        "/api/v1/feed", params={"limit": 1, "cursor": second["next_cursor"]}, headers=headers
    ).json()
    assert third["items"][0]["id"] == ids[0] and third["next_cursor"] is None


@pytest.mark.parametrize(
    "body",
    [
        {"username": "alice", "password": PASSWORD, "extra": True},
        {"username": 123, "password": PASSWORD},
        {"username": "alice", "password": True},
        {"username": "a-b", "password": PASSWORD},
        {"username": "alice"},
    ],
)
def test_credentials_reject_types_missing_and_extra_fields(api_client, body):
    r = api_client.post("/api/v1/auth/register", json=body)
    assert r.status_code == 422, r.text
    detail = r.json()["detail"]
    assert isinstance(detail, list) and detail
    assert all(isinstance(v.get("loc"), list) and v.get("msg") and v.get("type") for v in detail)


def test_chunked_body_over_limit_is_413(api_client):
    r = api_client.post(
        "/api/v1/auth/login",
        content=iter([b"x" * 9000, b"x" * 9000]),
        headers={"Content-Type": "application/json"},
    )
    assert r.status_code == 413 and r.json() == {"detail": "request body too large"}


def test_committed_post_comment_and_empty_204_are_visible(api_client, database):
    _, headers = actor(api_client, "alice")
    response = api_client.post(
        "/api/v1/posts", json={"content": "  preserved whitespace  "}, headers=headers
    )
    assert response.status_code == 201, response.text
    post = response.json()
    assert post["content"] == "  preserved whitespace  "
    with psycopg.connect(database) as c:
        assert (
            c.execute("SELECT content FROM posts WHERE id=%s", (post["id"],)).fetchone()[0]
            == post["content"]
        )
    like = api_client.put(f"/api/v1/posts/{post['id']}/like", headers=headers)
    assert like.status_code == 204 and like.content == b""
    comment = api_client.post(
        f"/api/v1/posts/{post['id']}/comments", json={"content": "reply"}, headers=headers
    )
    assert comment.status_code == 201 and set(comment.json()) == {
        "id",
        "content",
        "created_at",
        "author",
    }
    fetched = api_client.get(f"/api/v1/posts/{post['id']}", headers=headers).json()
    assert fetched["like_count"] == 1 and fetched["comment_count"] == 1 and fetched["liked"] is True


VALIDATION_CASES = json.loads(Path("contracts/validation-cases.json").read_text())


@pytest.mark.parametrize("case", VALIDATION_CASES, ids=lambda c: c["name"])
def test_validation_matches_baseline_response(api_client, case):
    _, headers = actor(api_client, "alice")
    api_client.post("/api/v1/posts", json={"content": "one"}, headers=headers)
    kwargs = {"headers": headers}
    if case["method"] == "POST":
        kwargs.update(
            content=json.dumps(case["body"]),
            headers={**headers, "Content-Type": "application/json"},
        )
    response = api_client.request(case["method"], case["path"], **kwargs)
    assert response.status_code == case["status"], response.text
    assert response.json() == case["response"]


def test_bearer_scheme_is_case_insensitive(api_client):
    _, headers = actor(api_client, "alice")
    headers["Authorization"] = headers["Authorization"].replace("Bearer ", "bEaReR ")
    assert api_client.get("/api/v1/users/me", headers=headers).status_code == 200


def test_optional_future_nbf_is_rejected(api_client):
    user, _ = actor(api_client, "alice")
    r = api_client.get(
        "/api/v1/users/me",
        headers={"Authorization": "Bearer " + token(user, nbf=int(time.time()) + 3600)},
    )
    assert r.status_code == 401 and r.json() == {"detail": "invalid credentials"}


def test_audience_array_with_expected_member_is_accepted(api_client):
    user, _ = actor(api_client, "alice")
    r = api_client.get(
        "/api/v1/users/me",
        headers={"Authorization": "Bearer " + token(user, aud=["other", "social-api"])},
    )
    assert r.status_code == 200


def test_empty_json_body_has_root_missing_error(api_client):
    r = api_client.post(
        "/api/v1/auth/login", content=b"", headers={"Content-Type": "application/json"}
    )
    assert r.status_code == 422
    assert r.json() == {
        "detail": [{"type": "missing", "loc": ["body"], "msg": "Field required", "input": None}]
    }


def test_empty_and_duplicate_limit_match_query_semantics(api_client):
    _, h = actor(api_client, "alice")
    r = api_client.get("/api/v1/feed?limit=", headers=h)
    assert r.status_code == 422
    assert r.json() == {
        "detail": [
            {
                "type": "int_parsing",
                "loc": ["query", "limit"],
                "msg": "Input should be a valid integer, unable to parse string as an integer",
                "input": "",
            }
        ]
    }
    assert api_client.get("/api/v1/feed?limit=101&limit=1", headers=h).status_code == 200
    assert api_client.get("/api/v1/feed?limit=1&limit=101", headers=h).status_code == 422


def test_extra_validation_errors_keep_request_key_order(api_client):
    r = api_client.post(
        "/api/v1/auth/register", json={"username": "alice", "password": PASSWORD, "z": 1, "a": 2}
    )
    assert r.status_code == 422
    assert r.json() == {
        "detail": [
            {
                "type": "extra_forbidden",
                "loc": ["body", "z"],
                "msg": "Extra inputs are not permitted",
                "input": 1,
            },
            {
                "type": "extra_forbidden",
                "loc": ["body", "a"],
                "msg": "Extra inputs are not permitted",
                "input": 2,
            },
        ]
    }


def test_invalid_utf8_is_rejected_without_mutation(api_client, database):
    _, h = actor(api_client, "alice")
    r = api_client.post(
        "/api/v1/posts",
        content=b'{"content":"invalid\xff"}',
        headers={**h, "Content-Type": "application/json"},
    )
    assert r.status_code == 400 and r.json() == {"detail": "There was an error parsing the body"}
    with psycopg.connect(database) as c:
        assert c.execute("SELECT count(*) FROM posts").fetchone()[0] == 0


@pytest.mark.parametrize(
    "date,item_id",
    [
        ("0000-01-01T00:00:00Z", 1),
        pytest.param(
            "2026-01-01T24:00:00Z",
            1,
            marks=pytest.mark.skipif(
                not os.environ.get("TARGET_URL") and sys.version_info >= (3, 14),
                reason="Python3.14 expands ISO8601 end-of-day parsing; canonical reference is3.10",
            ),
        ),
        ("2026-02-30T00:00:00Z", 1),
        ("2026-01-01T00:00:00Z", 1.0),
    ],
)
def test_invalid_cursor_date_or_json_id_type_is_422(api_client, date, item_id):
    _, h = actor(api_client, "alice")
    raw = json.dumps([date, item_id], separators=(",", ":")).encode()
    cursor = base64.urlsafe_b64encode(raw).decode().rstrip("=")
    r = api_client.get("/api/v1/feed", params={"cursor": cursor}, headers=h)
    assert r.status_code == 422 and r.json() == {"detail": "invalid cursor"}


def test_locked_write_has_tagged_connection_and_bounded_server_timeout(api_client, database):
    import os
    from concurrent.futures import ThreadPoolExecutor

    _, headers = actor(api_client, "alice")
    post = api_client.post("/api/v1/posts", json={"content": "locked"}, headers=headers).json()[
        "id"
    ]
    with psycopg.connect(database) as blocker:
        blocker.execute("SELECT id FROM posts WHERE id=%s FOR UPDATE", (post,))

        def write():
            try:
                return api_client.put(f"/api/v1/posts/{post}/like", headers=headers).status_code
            except Exception:
                # In-process TestClient rethrows the deliberate server SQL timeout;
                # external clients must receive a bounded failed HTTP response.
                if os.environ.get("TARGET_URL"):
                    raise
                return 500

        started = time.monotonic()
        with ThreadPoolExecutor(1) as pool:
            future = pool.submit(write)
            deadline = started + 4
            tagged = False
            while time.monotonic() < deadline:
                with psycopg.connect(database) as inspector:
                    tagged = bool(
                        inspector.execute(
                            "SELECT 1 FROM pg_stat_activity WHERE datname=current_database() AND application_name='lb-backend' AND wait_event_type='Lock'"
                        ).fetchone()
                    )
                if tagged:
                    break
                time.sleep(0.05)
            assert tagged, "blocked application session must be visible to shared restore guard"
            assert future.result(timeout=12) >= 500
            assert time.monotonic() - started < 15
    with psycopg.connect(database) as inspector:
        assert (
            inspector.execute("SELECT count(*) FROM likes WHERE post_id=%s", (post,)).fetchone()[0]
            == 0
        )
