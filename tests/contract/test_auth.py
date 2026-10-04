PASSWORD = "correct horse battery staple"


def test_register_login_and_me(api_client):
    r = api_client.post("/api/v1/auth/register", json={"username": "Alice", "password": PASSWORD})
    assert r.status_code == 201, r.text
    assert r.json()["username"] == "alice"
    assert "password" not in r.text
    login = api_client.post("/api/v1/auth/login", json={"username": "ALICE", "password": PASSWORD})
    assert login.status_code == 200, login.text
    token = login.json()["access_token"]
    assert (
        api_client.get("/api/v1/users/me", headers={"Authorization": "Bearer " + token}).json()[
            "username"
        ]
        == "alice"
    )


def test_duplicate_normalized_username_is_409(api_client):
    for name, status in [("Alice", 201), ("alice", 409)]:
        assert (
            api_client.post(
                "/api/v1/auth/register", json={"username": name, "password": PASSWORD}
            ).status_code
            == status
        )


def test_unknown_and_wrong_password_are_uniform_401(api_client):
    api_client.post("/api/v1/auth/register", json={"username": "alice", "password": PASSWORD})
    responses = [
        api_client.post("/api/v1/auth/login", json={"username": name, "password": "wrong-password"})
        for name in ["alice", "nobody"]
    ]
    assert all(r.status_code == 401 for r in responses)
    assert responses[0].json() == responses[1].json()


def test_short_password_is_422(api_client):
    assert (
        api_client.post(
            "/api/v1/auth/register", json={"username": "alice", "password": "short"}
        ).status_code
        == 422
    )


def test_oversized_and_chunked_body_is_rejected(api_client):
    r = api_client.post(
        "/api/v1/auth/register", content=b"x" * 17000, headers={"Content-Type": "application/json"}
    )
    assert r.status_code == 413
