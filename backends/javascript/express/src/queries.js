import { encodeCursor } from "./cursors.js";
const postColumns = `p.id,p.content,to_char(p.created_at AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US"Z"') AS created_at,p.author_id,u.username,
 (SELECT count(*) FROM likes l WHERE l.post_id=p.id) AS like_count,
 (SELECT count(*) FROM comments c WHERE c.post_id=p.id) AS comment_count,
 EXISTS(SELECT 1 FROM likes l WHERE l.post_id=p.id AND l.user_id=$1) AS liked`;
export const POST_SELECT = `SELECT ${postColumns} FROM posts p JOIN users u ON u.id=p.author_id`;
export const FEED =
  POST_SELECT +
  ` WHERE p.author_id IN (SELECT followed_id FROM follows WHERE follower_id=$1 UNION ALL SELECT $1)
 AND ($2::timestamptz IS NULL OR (p.created_at,p.id)<($2::timestamptz,$3::bigint)) ORDER BY p.created_at DESC,p.id DESC LIMIT $4`;
export const COMMENTS = `SELECT c.id,c.content,to_char(c.created_at AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US"Z"') AS created_at,c.author_id,u.username FROM comments c JOIN users u ON u.id=c.author_id
 WHERE c.post_id=$1 AND ($2::timestamptz IS NULL OR (c.created_at,c.id)<($2::timestamptz,$3::bigint)) ORDER BY c.created_at DESC,c.id DESC LIMIT $4`;
export function item(row) {
  const { author_id, username, ...rest } = row;
  return { ...rest, author: { id: author_id, username } };
}
export function page(rows, limit) {
  const selected = rows.slice(0, limit);
  return {
    items: selected.map(item),
    next_cursor:
      rows.length > limit
        ? encodeCursor(selected.at(-1).created_at, selected.at(-1).id)
        : null,
  };
}
