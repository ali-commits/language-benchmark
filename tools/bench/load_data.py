"""Private k6 inputs: real tokens, known mutation pairs, and nonfirst-page cursors."""

import json
import os
import time
from pathlib import Path
from typing import TypeVar

import httpx
import psycopg
from typing_extensions import TypedDict

from .records import Actor, Credential, LoadMetadata, LoadToken, checked_record


class FeedCursor(TypedDict):
    next_cursor: str | None


def validate_token_lifetime(remaining_seconds: int, required_valid_seconds: float) -> None:
    if remaining_seconds < required_valid_seconds + 60:
        raise ValueError("token expires before trial completes")


T = TypeVar("T")


def partition_actors(actors: list[T], shard: int, shards: int) -> list[T]:
    if not 0 <= shard < shards:
        raise ValueError("invalid shard")
    return actors[shard::shards]


def prepare_load_data(
    base_url: str, credentials_path: Path, output_path: Path, required_valid_seconds: float
) -> LoadMetadata:
    credentials = checked_record(json.loads(credentials_path.read_text()), list[Credential])
    actors: list[Actor] = []
    start = time.time()
    with httpx.Client(base_url=base_url, timeout=30) as c:
        for cred in credentials:
            r = c.post(
                "/api/v1/auth/login",
                json={"username": cred["username"], "password": cred["password"]},
            )
            r.raise_for_status()
            t = checked_record(r.json(), LoadToken)
            validate_token_lifetime(
                t["expires_in"] - int(time.time() - start), required_valid_seconds
            )
            headers = {"Authorization": "Bearer " + t["access_token"]}
            me = c.get("/api/v1/users/me", headers=headers)
            me.raise_for_status()
            feed = c.get("/api/v1/feed", headers=headers)
            feed.raise_for_status()
            actors.append(
                {
                    **cred,
                    **checked_record(me.json(), Actor),
                    "token": t["access_token"],
                    "cursor": checked_record(feed.json(), FeedCursor)["next_cursor"],
                }
            )
    with psycopg.connect(os.environ["DATABASE_URL"]) as db:
        posts = [checked_record(r[0], int) for r in db.execute("SELECT id FROM posts ORDER BY id")]
        known_likes = set(
            db.execute(
                "SELECT user_id,post_id FROM likes WHERE user_id=ANY(%s)",
                ([a["id"] for a in actors],),
            ).fetchall()
        )
    if not posts:
        raise ValueError("fixture has no posts")
    budget = int(os.environ.get("PAIR_BUDGET", "100000"))
    pairs = []
    for pi, p in enumerate(posts):
        for ai, a in enumerate(actors):
            if (a["id"], p) not in known_likes:
                pairs.append([ai, p])
            if len(pairs) >= budget:
                break
        if len(pairs) >= budget:
            break
    if len(pairs) < budget:
        raise ValueError("not enough distinct unliked pairs for trial")
    expires_at = start + min(3600, t["expires_in"])
    data = {
        "actors": actors,
        "posts": posts,
        "like_pairs": pairs,
        "created_at": time.time(),
        "expires_at": expires_at,
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(output_path, os.O_CREAT | os.O_TRUNC | os.O_WRONLY, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(data, f)
    return {"actors": len(actors), "pairs": len(pairs), "expires_at": expires_at}
