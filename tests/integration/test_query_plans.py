from sqlalchemy import event
from tests_support import actor


def test_feed_query_count_does_not_scale_with_page_size(api_client):
    _, h = actor(api_client, "alice")
    for n in range(21):
        assert (
            api_client.post("/api/v1/posts", json={"content": str(n)}, headers=h).status_code == 201
        )
    # Count emitted SQL on baseline; native paths are one bounded SQL statement by design.
    if not hasattr(api_client.app.state, "engine"):
        page = api_client.get("/api/v1/feed?limit=20", headers=h).json()
        assert len(page["items"]) == 20 and page["next_cursor"]
        return
    engine = api_client.app.state.engine.sync_engine
    queries = []

    def record(*args):
        queries.append(args[2])

    event.listen(engine, "before_cursor_execute", record)
    try:
        api_client.get("/api/v1/feed?limit=1", headers=h)
        small = len(queries)
        queries.clear()
        api_client.get("/api/v1/feed?limit=20", headers=h)
        large = len(queries)
        assert small == large and large <= 2
    finally:
        event.remove(engine, "before_cursor_execute", record)
