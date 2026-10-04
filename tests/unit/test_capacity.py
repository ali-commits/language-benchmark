from bench.capacity import search_capacity


def test_ten_thousand_steps_then_binary_search():
    calls = []

    def probe(users):
        calls.append(users)
        return {"outcome": "pass" if users <= 23400 else "fail"}

    r = search_capacity(probe, start=10000, step=10000, ceiling=50000, resolution=100)
    assert calls[:3] == [10000, 20000, 30000]
    assert r["passing_users"] <= 23400 < r["failing_users"]
    assert r["failing_users"] - r["passing_users"] <= 100


def test_first_level_failure_searches_zero_to_ten_thousand():
    calls = []

    def probe(users):
        calls.append(users)
        return {"outcome": "pass" if users <= 1200 else "fail"}

    r = search_capacity(probe)
    assert calls[:2] == [10000, 5000]
    assert r["passing_users"] <= 1200 < r["failing_users"]


def test_generator_failure_never_becomes_backend_capacity():
    r = search_capacity(
        lambda users: {"outcome": "invalid", "reasons": ["generator out of memory"]}
    )
    assert r["status"] == "invalid" and r["failing_users"] is None


def test_fixture_ceiling_is_a_lower_bound_not_a_failure():
    r = search_capacity(lambda users: {"outcome": "pass"}, ceiling=50000)
    assert r["passing_users"] == 50000 and r["failing_users"] is None
    assert r["status"] == "ceiling_reached"
