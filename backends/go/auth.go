package main

import (
	"errors"
	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgconn"
	"net/http"
	"strings"
)

func unauthorized(w http.ResponseWriter) {
	w.Header().Set("WWW-Authenticate", "Bearer")
	failure(w, 401, "invalid credentials")
}
func (a *app) register(w http.ResponseWriter, r *http.Request) {
	body, ok := readInput(w, r, "register")
	if !ok {
		return
	}
	hash, e := hashPasswordContext(r.Context(), body["password"])
	if e != nil {
		if r.Context().Err() != nil {
			return
		}
		failure(w, 500, "internal server error")
		return
	}
	c, e := a.checkout(r.Context())
	if e != nil {
		failure(w, 503, "database unavailable")
		return
	}
	defer c.Release()
	var u user
	e = c.QueryRow(r.Context(), "INSERT INTO users(username,password_hash) VALUES ($1,$2) RETURNING id,username", body["username"], hash).Scan(&u.ID, &u.Username)
	var pgErr *pgconn.PgError
	if errors.As(e, &pgErr) && pgErr.Code == "23505" {
		failure(w, 409, "username already exists")
		return
	}
	if e != nil {
		failure(w, 500, "internal server error")
		return
	}
	respond(w, 201, u)
}
func (a *app) login(w http.ResponseWriter, r *http.Request) {
	body, ok := readInput(w, r, "login")
	if !ok {
		return
	}
	c, e := a.checkout(r.Context())
	if e != nil {
		failure(w, 503, "database unavailable")
		return
	}
	var id int64
	var hash string
	e = c.QueryRow(r.Context(), "SELECT id,password_hash FROM users WHERE username=$1", body["username"]).Scan(&id, &hash)
	c.Release()
	if e != nil && !errors.Is(e, pgx.ErrNoRows) {
		failure(w, 500, "internal server error")
		return
	}
	exists := e == nil
	if !exists {
		hash = dummyHash
	}
	valid, e := verifyPasswordContext(r.Context(), body["password"], hash)
	if e != nil {
		return
	}
	if !valid || !exists {
		unauthorized(w)
		return
	}
	token, e := a.issueToken(id)
	if e != nil {
		failure(w, 500, "internal server error")
		return
	}
	respond(w, 200, map[string]any{"access_token": token, "token_type": "bearer", "expires_in": a.ttl})
}
func (a *app) identity(w http.ResponseWriter, r *http.Request) (int64, bool) {
	parts := strings.Fields(r.Header.Get("Authorization"))
	if len(parts) != 2 || !strings.EqualFold(parts[0], "Bearer") {
		unauthorized(w)
		return 0, false
	}
	id, e := a.decodeToken(parts[1])
	if e != nil {
		unauthorized(w)
		return 0, false
	}
	return id, true
}
