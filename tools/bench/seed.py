"""Deterministic logical data; password hashes use real random salts."""

from collections.abc import Iterator

import csv
import json
import os
import random
from concurrent.futures import ProcessPoolExecutor
from dataclasses import asdict
from datetime import datetime, timedelta
from pathlib import Path

import psycopg
from argon2 import PasswordHasher

from .models import FixtureSpec

COLUMNS = {
    "users": ("id", "username", "password_hash", "created_at"),
    "posts": ("id", "author_id", "content", "created_at"),
    "follows": ("follower_id", "followed_id"),
    "likes": ("post_id", "user_id"),
    "comments": ("id", "post_id", "author_id", "content", "created_at"),
}


FixtureRow = tuple[int | str | datetime, ...]


def fixture_rows(spec: FixtureSpec) -> dict[str, Iterator[FixtureRow]]:
    origin = datetime.fromisoformat(spec.time_origin)

    def posts() -> Iterator[FixtureRow]:
        rng = random.Random(spec.seed + 1)
        for i in range(1, spec.posts + 1):
            author = 1 + int(rng.random() ** 2 * spec.users)
            yield (
                i,
                author,
                ("Post %d: " % i) + ("social benchmark " * rng.randint(1, 14))[:250],
                origin + timedelta(seconds=i),
            )

    def follows() -> Iterator[FixtureRow]:
        rng = random.Random(spec.seed + 2)
        for user in range(1, spec.users + 1):
            seen: set[int] = set()
            attempts = 0
            while len(seen) < spec.follows_per_user and attempts < spec.follows_per_user * 20:
                target = 1 + int(rng.random() ** 2.5 * spec.users)
                attempts += 1
                if target != user:
                    seen.add(target)
            if len(seen) < spec.follows_per_user:
                for target in range(1, spec.users + 1):
                    if target != user:
                        seen.add(target)
                    if len(seen) == spec.follows_per_user:
                        break
            for target in sorted(seen):
                yield (user, target)

    def likes() -> Iterator[FixtureRow]:
        # Skewed hot prefix, with a deterministic bounded unique fallback.
        rng = random.Random(spec.seed + 3)
        seen: set[tuple[int, int]] = set()
        budget = min(spec.likes, spec.users * spec.posts)
        for _ in range(budget * 3):
            if len(seen) >= budget:
                break
            pair = (1 + int(rng.random() ** 2 * spec.posts), rng.randint(1, spec.users))
            if pair not in seen:
                seen.add(pair)
                yield pair
        if len(seen) < budget:
            for p in range(1, spec.posts + 1):
                for u in range(1, spec.users + 1):
                    if len(seen) >= budget:
                        return
                    if (p, u) not in seen:
                        seen.add((p, u))
                        yield (p, u)

    def comments() -> Iterator[FixtureRow]:
        rng = random.Random(spec.seed + 4)
        for i in range(1, spec.comments + 1):
            yield (
                i,
                1 + int(rng.random() ** 2 * spec.posts),
                rng.randint(1, spec.users),
                "Comment " + str(i) + " " + ("conversation " * rng.randint(1, 20)),
                origin + timedelta(seconds=spec.posts + i),
            )

    return {"posts": posts(), "follows": follows(), "likes": likes(), "comments": comments()}


def _hashed_user(args: tuple[int, str, str]) -> tuple[int, str, str, str]:
    id, password, origin = args
    h = PasswordHasher(time_cost=3, memory_cost=65536, parallelism=1, hash_len=32, salt_len=16)
    return (id, f"user_{id:05}", h.hash(password), origin)


def user_rows(spec: FixtureSpec, password: str, workers: int) -> Iterator[FixtureRow]:
    args = ((i, password, spec.time_origin) for i in range(1, spec.users + 1))
    if workers == 1:
        yield from map(_hashed_user, args)
    else:
        with ProcessPoolExecutor(max_workers=workers) as pool:
            yield from pool.map(_hashed_user, args, chunksize=20)


def seed_database(dsn: str, spec: FixtureSpec) -> dict[str, int]:
    password = os.environ["SEED_PASSWORD"]
    if not 12 <= len(password) <= 128:
        raise ValueError("seed password length invalid")
    workers = int(os.environ.get("SEED_HASH_WORKERS", "4"))
    if not 1 <= workers <= 64:
        raise ValueError("hash workers outside bounds")
    with psycopg.connect(dsn) as c:
        if count_rows(c, "users"):
            raise ValueError("seed requires empty schema")
        rows = {"users": user_rows(spec, password, workers), **fixture_rows(spec)}
        for table, values in rows.items():
            print("loading " + table, flush=True)
            with c.cursor().copy(f"COPY {table} ({','.join(COLUMNS[table])}) FROM STDIN") as copy:
                for row in values:
                    copy.write_row(row)
        for table in ("users", "posts", "comments"):
            c.execute(
                f"SELECT setval(pg_get_serial_sequence('{table}','id'),(SELECT max(id) FROM {table}))"
            )
        c.execute("ANALYZE")
        counts = {t: count_rows(c, t) for t in COLUMNS}
        expected = {
            "users": spec.users,
            "posts": spec.posts,
            "follows": spec.users * spec.follows_per_user,
            "likes": spec.likes,
            "comments": spec.comments,
        }
        if counts != expected:
            raise ValueError("incorrect seed counts")
        c.execute(
            "UPDATE benchmark_identity SET fixture_spec=%s::jsonb WHERE singleton",
            (json.dumps(asdict(spec)),),
        )
        return counts


def generate_fixture(spec: FixtureSpec, output_dir: Path) -> dict[str, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    result = {}
    for table, rows in fixture_rows(spec).items():
        path = output_dir / (table + ".csv")
        with path.open("w", newline="") as f:
            csv.writer(f).writerows(rows)
        result[table] = path
    return result


def count_rows(connection: psycopg.Connection[tuple[object, ...]], table: str) -> int:
    """COUNT(*) returns one non-null PostgreSQL bigint, checked at the SQL boundary."""
    from psycopg import sql

    row = connection.execute(sql.SQL("SELECT count(*) FROM {}").format(sql.Identifier(table))).fetchone()
    if row is None or not isinstance(row[0], int):
        raise ValueError("database count is missing or invalid")
    return row[0]
