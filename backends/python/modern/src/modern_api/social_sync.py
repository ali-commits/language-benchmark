from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, Response

from .auth_sync import Identity, Session
from .queries import COMMENTS, FEED, POST_SELECT, decode_cursor, encode_cursor
from .schemas import Comment, CommentInput, CommentPage, Post, PostInput, PostPage

router = APIRouter(prefix="/api/v1")
Limit = Annotated[int, Query(ge=1, le=100)]


def params(cursor, limit):
    try:
        stamp, id = decode_cursor(cursor) if cursor is not None else (None, 0)
    except ValueError:
        raise HTTPException(422, "invalid cursor")
    return {"cursor_time": stamp, "cursor_id": id, "limit": limit + 1}


def item(row):
    d = dict(row)
    d["author"] = {"id": d.pop("author_id"), "username": d.pop("username")}
    return d


def page(rows, limit):
    more = len(rows) > limit
    selected = rows[:limit]
    cursor = encode_cursor(selected[-1]["created_at"], selected[-1]["id"]) if more else None
    return {"items": [item(r) for r in selected], "next_cursor": cursor}


def exists(s, table, id):
    if not s.execute(f"SELECT 1 FROM {table} WHERE id=%(id)s", {"id": id}).fetchone():
        raise HTTPException(404, "not found")


@router.post("/posts", response_model=Post, status_code=201)
def create_post(body: PostInput, user_id: Identity, s: Session):
    id = s.execute(
        "INSERT INTO posts(author_id,content) VALUES (%(id)s,%(content)s) RETURNING id",
        {"id": user_id, "content": body.content},
    ).fetchone()["id"]
    row = s.execute(POST_SELECT + " WHERE p.id=%(id)s", {"id": id, "viewer": user_id}).fetchone()
    return item(row)


@router.get("/posts/{post_id}", response_model=Post)
def detail(post_id: int, user_id: Identity, s: Session):
    row = s.execute(
        POST_SELECT + " WHERE p.id=%(id)s", {"id": post_id, "viewer": user_id}
    ).fetchone()
    if row is None:
        raise HTTPException(404, "not found")
    return item(row)


@router.get("/feed", response_model=PostPage)
def feed(user_id: Identity, s: Session, limit: Limit = 20, cursor: str | None = None):
    p = params(cursor, limit)
    p["viewer"] = user_id
    rows = s.execute(FEED, p).fetchall()
    return page(rows, limit)


@router.put("/users/{target_id}/follow", status_code=204)
def follow(target_id: int, user_id: Identity, s: Session):
    if target_id == user_id:
        raise HTTPException(422, "cannot follow yourself")
    exists(s, "users", target_id)
    s.execute(
        "INSERT INTO follows(follower_id,followed_id) VALUES (%(id)s,%(target)s) ON CONFLICT DO NOTHING",
        {"id": user_id, "target": target_id},
    )
    return Response(status_code=204)


@router.delete("/users/{target_id}/follow", status_code=204)
def unfollow(target_id: int, user_id: Identity, s: Session):
    if target_id == user_id:
        raise HTTPException(422, "cannot follow yourself")
    exists(s, "users", target_id)
    s.execute(
        "DELETE FROM follows WHERE follower_id=%(id)s AND followed_id=%(target)s",
        {"id": user_id, "target": target_id},
    )
    return Response(status_code=204)


@router.put("/posts/{post_id}/like", status_code=204)
def like(post_id: int, user_id: Identity, s: Session):
    exists(s, "posts", post_id)
    s.execute(
        "INSERT INTO likes(post_id,user_id) VALUES (%(post)s,%(id)s) ON CONFLICT DO NOTHING",
        {"post": post_id, "id": user_id},
    )
    return Response(status_code=204)


@router.delete("/posts/{post_id}/like", status_code=204)
def unlike(post_id: int, user_id: Identity, s: Session):
    exists(s, "posts", post_id)
    s.execute(
        "DELETE FROM likes WHERE post_id=%(post)s AND user_id=%(id)s",
        {"post": post_id, "id": user_id},
    )
    return Response(status_code=204)


@router.post("/posts/{post_id}/comments", response_model=Comment, status_code=201)
def create_comment(post_id: int, body: CommentInput, user_id: Identity, s: Session):
    exists(s, "posts", post_id)
    row = s.execute(
        "WITH inserted AS (INSERT INTO comments(post_id,author_id,content) VALUES (%(post)s,%(id)s,%(content)s) RETURNING *)\n     SELECT c.id,c.content,c.created_at,c.author_id,u.username FROM inserted c JOIN users u ON u.id=c.author_id",
        {"post": post_id, "id": user_id, "content": body.content},
    ).fetchone()
    return item(row)


@router.get("/posts/{post_id}/comments", response_model=CommentPage)
def comments(
    post_id: int, user_id: Identity, s: Session, limit: Limit = 20, cursor: str | None = None
):
    exists(s, "posts", post_id)
    p = params(cursor, limit)
    p["post_id"] = post_id
    rows = s.execute(COMMENTS, p).fetchall()
    return page(rows, limit)
