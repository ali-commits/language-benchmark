"""Cursor validation and parameterized SQL, shared by adapters within this app."""

import base64
import binascii
import json
from datetime import datetime, timezone


def encode_cursor(created_at: datetime, item_id: int) -> str:
    data = json.dumps(
        [created_at.astimezone(timezone.utc).isoformat(), item_id], separators=(",", ":")
    )
    return base64.urlsafe_b64encode(data.encode()).decode().rstrip("=")


def decode_cursor(value: str) -> tuple[datetime, int]:
    try:
        if not 1 <= len(value) <= 256:
            raise ValueError()
        data = json.loads(
            base64.b64decode(value + "=" * (-len(value) % 4), altchars=b"-_", validate=True)
        )
        if not isinstance(data, list) or len(data) != 2:
            raise ValueError()
        d = datetime.fromisoformat(data[0])
        if (
            d.utcoffset() is None
            or isinstance(data[1], bool)
            or not isinstance(data[1], int)
            or not 0 < data[1] < 2**63
        ):
            raise ValueError()
        return d.astimezone(timezone.utc), data[1]
    except (ValueError, TypeError, IndexError, binascii.Error, OverflowError) as e:
        raise ValueError("invalid cursor") from e


POST_SELECT = """SELECT p.id,p.content,p.created_at,p.author_id,u.username,
 (SELECT count(*) FROM likes l WHERE l.post_id=p.id) AS like_count,
 (SELECT count(*) FROM comments c WHERE c.post_id=p.id) AS comment_count,
 EXISTS(SELECT 1 FROM likes l WHERE l.post_id=p.id AND l.user_id=%(viewer)s) AS liked
 FROM posts p JOIN users u ON u.id=p.author_id"""
FEED = (
    POST_SELECT
    + """ WHERE p.author_id IN
 (SELECT followed_id FROM follows WHERE follower_id=%(viewer)s UNION ALL SELECT %(viewer)s)
 AND (CAST(%(cursor_time)s AS timestamptz) IS NULL OR (p.created_at,p.id)<(CAST(%(cursor_time)s AS timestamptz),%(cursor_id)s))
 ORDER BY p.created_at DESC,p.id DESC LIMIT %(limit)s"""
)
COMMENTS = """SELECT c.id,c.content,c.created_at,c.author_id,u.username FROM comments c JOIN users u ON u.id=c.author_id
 WHERE c.post_id=%(post_id)s AND (CAST(%(cursor_time)s AS timestamptz) IS NULL OR (c.created_at,c.id)<(CAST(%(cursor_time)s AS timestamptz),%(cursor_id)s))
 ORDER BY c.created_at DESC,c.id DESC LIMIT %(limit)s"""
