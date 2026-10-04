def actor(c, name):
    password = "correct horse battery staple"
    r = c.post("/api/v1/auth/register", json={"username": name, "password": password})
    assert r.status_code == 201, r.text
    login = c.post("/api/v1/auth/login", json={"username": name, "password": password})
    assert login.status_code == 200, login.text
    t = login.json()["access_token"]
    return r.json()["id"], {"Authorization": "Bearer " + t}
