package main

import (
	"context"
	"errors"
	"github.com/jackc/pgx/v5"
	"net/http"
	"strconv"
	"strings"
	"time"
)

type user struct {
	ID       int64  `json:"id"`
	Username string `json:"username"`
}
type comment struct {
	ID        int64     `json:"id"`
	Content   string    `json:"content"`
	CreatedAt time.Time `json:"created_at"`
	Author    user      `json:"author"`
}
type post struct {
	comment
	LikeCount    int64 `json:"like_count"`
	CommentCount int64 `json:"comment_count"`
	Liked        bool  `json:"liked"`
}

const postSelect = `SELECT p.id,p.content,p.created_at,p.author_id,u.username,(SELECT count(*) FROM likes l WHERE l.post_id=p.id),(SELECT count(*) FROM comments c WHERE c.post_id=p.id),EXISTS(SELECT 1 FROM likes l WHERE l.post_id=p.id AND l.user_id=$1) FROM posts p JOIN users u ON u.id=p.author_id`

func scanPost(row pgx.Row) (post, error) {
	var p post
	e := row.Scan(&p.ID, &p.Content, &p.CreatedAt, &p.Author.ID, &p.Author.Username, &p.LikeCount, &p.CommentCount, &p.Liked)
	p.CreatedAt = p.CreatedAt.UTC()
	return p, e
}
func scanComment(row pgx.Row) (comment, error) {
	var c comment
	e := row.Scan(&c.ID, &c.Content, &c.CreatedAt, &c.Author.ID, &c.Author.Username)
	c.CreatedAt = c.CreatedAt.UTC()
	return c, e
}
func pathID(w http.ResponseWriter, r *http.Request, key string) (int64, bool) {
	s := r.PathValue(key)
	if s == "" {
		return 0, true
	}
	id, e := strconv.ParseInt(s, 10, 64)
	if e != nil {
		validation(w, "path", key, "int_parsing", "Input should be a valid integer, unable to parse string as an integer", s, nil)
		return 0, false
	}
	return id, true
}
func pagination(w http.ResponseWriter, r *http.Request) (int, *time.Time, int64, bool) {
	limit := 20
	if values, present := r.URL.Query()["limit"]; present {
		s := values[len(values)-1]
		n, e := strconv.Atoi(s)
		if e != nil {
			validation(w, "query", "limit", "int_parsing", "Input should be a valid integer, unable to parse string as an integer", s, nil)
			return 0, nil, 0, false
		}
		if n < 1 {
			validation(w, "query", "limit", "greater_than_equal", "Input should be greater than or equal to 1", s, map[string]int{"ge": 1})
			return 0, nil, 0, false
		}
		if n > 100 {
			validation(w, "query", "limit", "less_than_equal", "Input should be less than or equal to 100", s, map[string]int{"le": 100})
			return 0, nil, 0, false
		}
		limit = n
	}
	if values, present := r.URL.Query()["cursor"]; present {
		stamp, id, e := decodeCursor(values[len(values)-1])
		if e != nil {
			failure(w, 422, "invalid cursor")
			return 0, nil, 0, false
		}
		return limit, &stamp, id, true
	}
	return limit, nil, 0, true
}
func exists(ctx context.Context, tx pgx.Tx, table string, id int64) (bool, error) {
	var result bool
	e := tx.QueryRow(ctx, "SELECT EXISTS(SELECT 1 FROM "+table+" WHERE id=$1)", id).Scan(&result)
	return result, e
}
func (a *app) social(w http.ResponseWriter, r *http.Request) {
	viewer, ok := a.identity(w, r)
	if !ok {
		return
	}
	pid, ok := pathID(w, r, "post_id")
	if !ok {
		return
	}
	target, ok := pathID(w, r, "target_id")
	if !ok {
		return
	}
	var body map[string]string
	if r.Method == "POST" {
		kind := "post"
		if strings.HasSuffix(r.URL.Path, "/comments") {
			kind = "comment"
		}
		body, ok = readInput(w, r, kind)
		if !ok {
			return
		}
	}
	limit := 20
	var stamp *time.Time
	var cursorID int64
	if r.URL.Path == "/api/v1/feed" || r.Method == "GET" && strings.HasSuffix(r.URL.Path, "/comments") {
		limit, stamp, cursorID, ok = pagination(w, r)
		if !ok {
			return
		}
	}
	c, e := a.checkout(r.Context())
	if e != nil {
		failure(w, 503, "database unavailable")
		return
	}
	defer c.Release()
	tx, e := c.Begin(r.Context())
	if e != nil {
		failure(w, 500, "internal server error")
		return
	}
	defer tx.Rollback(r.Context())
	ctx := r.Context()
	status := 200
	var result any
	if r.URL.Path == "/api/v1/users/me" {
		var u user
		e = tx.QueryRow(ctx, "SELECT id,username FROM users WHERE id=$1", viewer).Scan(&u.ID, &u.Username)
		if errors.Is(e, pgx.ErrNoRows) {
			failure(w, 401, "invalid credentials")
			return
		}
		result = u
	} else if r.URL.Path == "/api/v1/posts" {
		var id int64
		e = tx.QueryRow(ctx, "INSERT INTO posts(author_id,content) VALUES ($1,$2) RETURNING id", viewer, body["content"]).Scan(&id)
		if e == nil {
			result, e = scanPost(tx.QueryRow(ctx, postSelect+" WHERE p.id=$2", viewer, id))
		}
		status = 201
	} else if r.URL.Path == "/api/v1/feed" {
		rows, err := tx.Query(ctx, postSelect+` WHERE p.author_id IN (SELECT followed_id FROM follows WHERE follower_id=$1 UNION ALL SELECT $1) AND ($2::timestamptz IS NULL OR (p.created_at,p.id)<($2::timestamptz,$3)) ORDER BY p.created_at DESC,p.id DESC LIMIT $4`, viewer, stamp, cursorID, limit+1)
		if err != nil {
			e = err
		} else {
			items := []post{}
			for rows.Next() {
				p, err := scanPost(rows)
				if err != nil {
					e = err
					break
				}
				items = append(items, p)
			}
			if e == nil {
				e = rows.Err()
			}
			rows.Close()
			var next any
			if len(items) > limit {
				items = items[:limit]
				last := items[len(items)-1]
				next = encodeCursor(last.CreatedAt, last.ID)
			}
			result = map[string]any{"items": items, "next_cursor": next}
		}
	} else if target != 0 || strings.HasSuffix(r.URL.Path, "/follow") {
		if target == viewer {
			failure(w, 422, "cannot follow yourself")
			return
		}
		found, err := exists(ctx, tx, "users", target)
		if err != nil {
			e = err
		} else if !found {
			failure(w, 404, "not found")
			return
		} else {
			if r.Method == "PUT" {
				_, e = tx.Exec(ctx, "INSERT INTO follows(follower_id,followed_id) VALUES ($1,$2) ON CONFLICT DO NOTHING", viewer, target)
			} else {
				_, e = tx.Exec(ctx, "DELETE FROM follows WHERE follower_id=$1 AND followed_id=$2", viewer, target)
			}
			status = 204
		}
	} else if strings.HasSuffix(r.URL.Path, "/like") {
		found, err := exists(ctx, tx, "posts", pid)
		if err != nil {
			e = err
		} else if !found {
			failure(w, 404, "not found")
			return
		} else {
			if r.Method == "PUT" {
				_, e = tx.Exec(ctx, "INSERT INTO likes(post_id,user_id) VALUES ($1,$2) ON CONFLICT DO NOTHING", pid, viewer)
			} else {
				_, e = tx.Exec(ctx, "DELETE FROM likes WHERE post_id=$1 AND user_id=$2", pid, viewer)
			}
			status = 204
		}
	} else if strings.HasSuffix(r.URL.Path, "/comments") {
		found, err := exists(ctx, tx, "posts", pid)
		if err != nil {
			e = err
		} else if !found {
			failure(w, 404, "not found")
			return
		} else if r.Method == "POST" {
			result, e = scanComment(tx.QueryRow(ctx, `WITH inserted AS (INSERT INTO comments(post_id,author_id,content) VALUES ($1,$2,$3) RETURNING *) SELECT c.id,c.content,c.created_at,c.author_id,u.username FROM inserted c JOIN users u ON u.id=c.author_id`, pid, viewer, body["content"]))
			status = 201
		} else {
			rows, err := tx.Query(ctx, `SELECT c.id,c.content,c.created_at,c.author_id,u.username FROM comments c JOIN users u ON u.id=c.author_id WHERE c.post_id=$1 AND ($2::timestamptz IS NULL OR (c.created_at,c.id)<($2::timestamptz,$3)) ORDER BY c.created_at DESC,c.id DESC LIMIT $4`, pid, stamp, cursorID, limit+1)
			if err != nil {
				e = err
			} else {
				items := []comment{}
				for rows.Next() {
					p, err := scanComment(rows)
					if err != nil {
						e = err
						break
					}
					items = append(items, p)
				}
				if e == nil {
					e = rows.Err()
				}
				rows.Close()
				var next any
				if len(items) > limit {
					items = items[:limit]
					last := items[len(items)-1]
					next = encodeCursor(last.CreatedAt, last.ID)
				}
				result = map[string]any{"items": items, "next_cursor": next}
			}
		}
	} else {
		result, e = scanPost(tx.QueryRow(ctx, postSelect+" WHERE p.id=$2", viewer, pid))
		if errors.Is(e, pgx.ErrNoRows) {
			failure(w, 404, "not found")
			return
		}
	}
	if e != nil {
		failure(w, 500, "internal server error")
		return
	}
	if e = tx.Commit(ctx); e != nil {
		failure(w, 500, "internal server error")
		return
	}
	respond(w, status, result)
}
