package main

import (
	"bytes"
	"context"
	"encoding/base64"
	"encoding/json"
	"errors"
	"github.com/golang-jwt/jwt/v5"
	"github.com/jackc/pgx/v5/pgxpool"
	"net/http/httptest"
	"net/url"
	"os"
	"strings"
	"testing"
	"time"
)

func TestCursorPrecision(t *testing.T) {
	stamp := time.Date(2026, 10, 5, 1, 2, 3, 456789000, time.UTC)
	c := encodeCursor(stamp, 9223372036854775806)
	got, id, e := decodeCursor(c)
	if e != nil || !got.Equal(stamp) || id != 9223372036854775806 {
		t.Fatalf("cursor precision: %v %d %v", got, id, e)
	}
	for _, v := range []string{"", base64.RawURLEncoding.EncodeToString([]byte(`["2026-10-05T00:00:00",1]`)), base64.RawURLEncoding.EncodeToString([]byte(`["2026-10-05T00:00:00Z",true]`))} {
		if _, _, e := decodeCursor(v); e == nil {
			t.Fatal("accepted invalid cursor")
		}
	}
}
func TestPasswordPHC(t *testing.T) {
	h, e := hashPassword("correct-long-password")
	if e != nil || !strings.HasPrefix(h, "$argon2id$v=19$m=65536,t=3,p=1$") {
		t.Fatalf("wrong policy: %s %v", h, e)
	}
	if !verifyPassword("correct-long-password", h) || verifyPassword("wrong", h) {
		t.Fatal("verification")
	}
}
func TestJWT(t *testing.T) {
	a := &app{secret: []byte(strings.Repeat("s", 32)), ttl: 3600}
	token, e := a.issueToken(42)
	if e != nil {
		t.Fatal(e)
	}
	if id, e := a.decodeToken(token); e != nil || id != 42 {
		t.Fatal(id, e)
	}
	a.secret = []byte(strings.Repeat("x", 32))
	if _, e := a.decodeToken(token); e == nil {
		t.Fatal("accepted bad signature")
	}
}
func TestValidation(t *testing.T) {
	for _, body := range []string{`{"content":" "}`, `{"content":12}`, `{"content":"ok","extra":true}`, `{}`} {
		r := httptest.NewRequest("POST", "/", strings.NewReader(body))
		w := httptest.NewRecorder()
		if _, ok := readInput(w, r, "post"); ok || w.Code != 422 {
			t.Fatalf("accepted %s: %d", body, w.Code)
		}
	}
	r := httptest.NewRequest("POST", "/", strings.NewReader(strings.Repeat("x", 16385)))
	w := httptest.NewRecorder()
	if _, ok := readInput(w, r, "post"); ok || w.Code != 413 {
		t.Fatal("body limit")
	}
}

func TestInvalidJWTClaims(t *testing.T) {
	a := &app{secret: []byte(strings.Repeat("s", 32)), ttl: 3600}
	now := time.Now().Unix()
	for _, change := range []map[string]any{{"exp": now - 1}, {"exp": true}, {"exp": float64(now) + 0.5}, {"iat": now + 3600}, {"iat": "1"}, {"sub": 42}, {"sub": "0"}, {"iss": "bad"}, {"aud": "bad"}, {"iat": nil}} {
		claims := jwt.MapClaims{"sub": "42", "iat": now, "exp": now + 3600, "iss": "language-benchmark", "aud": "social-api"}
		for k, v := range change {
			if v == nil {
				delete(claims, k)
			} else {
				claims[k] = v
			}
		}
		value, e := jwt.NewWithClaims(jwt.SigningMethodHS256, claims).SignedString(a.secret)
		if e != nil {
			t.Fatal(e)
		}
		if _, e = a.decodeToken(value); e == nil {
			t.Fatalf("accepted invalid claims %#v", change)
		}
	}
}
func TestChunkedBodyLimitMiddleware(t *testing.T) {
	a := &app{}
	r := httptest.NewRequest("PUT", "/api/v1/posts/1/like", strings.NewReader(strings.Repeat("x", 16385)))
	r.ContentLength = -1
	w := httptest.NewRecorder()
	a.handler().ServeHTTP(w, r)
	if w.Code != 413 || !strings.Contains(w.Body.String(), "request body too large") {
		t.Fatalf("%d %s", w.Code, w.Body.String())
	}
}

func TestValidationAggregatesFields(t *testing.T) {
	r := httptest.NewRequest("POST", "/", strings.NewReader(`{"username":12,"password":true,"extra":1}`))
	w := httptest.NewRecorder()
	readInput(w, r, "register")
	var body struct {
		Detail []any `json:"detail"`
	}
	if e := json.Unmarshal(w.Body.Bytes(), &body); e != nil || len(body.Detail) != 3 {
		t.Fatalf("expected all three validation failures: %s", w.Body.String())
	}
}

func TestRootValidationParity(t *testing.T) {
	for _, tc := range []struct{ body, kind, msg string }{{`[]`, "model_attributes_type", "Input should be a valid dictionary or object to extract fields from"}, {`null`, "missing", "Field required"}, {`{"username":"alice","password":""}`, "string_too_short", "String should have at least 1 character"}} {
		w := httptest.NewRecorder()
		r := httptest.NewRequest("POST", "/", strings.NewReader(tc.body))
		readInput(w, r, "login")
		var v map[string][]map[string]any
		json.Unmarshal(w.Body.Bytes(), &v)
		d := v["detail"][0]
		if d["type"] != tc.kind || d["msg"] != tc.msg {
			t.Fatalf("%s: %#v", tc.body, d)
		}
		if tc.body == `[]` || tc.body == `null` {
			if len(d["loc"].([]any)) != 1 {
				t.Fatalf("root location %#v", d)
			}
		}
	}
}

func TestHashQueueCancellation(t *testing.T) {
	hashSlots <- struct{}{}
	hashSlots <- struct{}{}
	defer func() { <-hashSlots; <-hashSlots }()
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	done := make(chan error, 1)
	started := make(chan struct{})
	go func() { close(started); _, e := hashPasswordContext(ctx, "unstarted-password"); done <- e }()
	<-started
	select {
	case e := <-done:
		t.Fatalf("full queue unexpectedly completed: %v", e)
	case <-time.After(10 * time.Millisecond):
	}
	cancel()
	select {
	case e := <-done:
		if !errors.Is(e, context.Canceled) {
			t.Fatalf("wrong cancellation %v", e)
		}
	case <-time.After(time.Second):
		t.Fatal("cancelled hash stayed queued")
	}
}
func TestEmptyBodyAndExtraKeyOrder(t *testing.T) {
	for _, tc := range []struct {
		body string
		want []string
	}{{"", []string{""}}, {`{"username":"alice","password":"long-enough-password","z_extra":true,"a_extra":false}`, []string{"z_extra", "a_extra"}}} {
		w := httptest.NewRecorder()
		r := httptest.NewRequest("POST", "/", strings.NewReader(tc.body))
		readInput(w, r, "register")
		var v struct {
			Detail []struct {
				Loc  []any  `json:"loc"`
				Type string `json:"type"`
			} `json:"detail"`
		}
		json.Unmarshal(w.Body.Bytes(), &v)
		if len(v.Detail) != len(tc.want) {
			t.Fatal(w.Body.String())
		}
		for i, key := range tc.want {
			if key == "" {
				if v.Detail[i].Type != "missing" || len(v.Detail[i].Loc) != 1 {
					t.Fatal(w.Body.String())
				}
			} else if v.Detail[i].Loc[1] != key {
				t.Fatal(w.Body.String())
			}
		}
	}
}
func TestLimitEmptyAndDuplicate(t *testing.T) {
	for _, tc := range []struct {
		query string
		ok    bool
		limit int
	}{{"limit=", false, 0}, {"limit=1&limit=2", true, 2}} {
		w := httptest.NewRecorder()
		r := httptest.NewRequest("GET", "/api/v1/feed?"+tc.query, nil)
		limit, _, _, ok := pagination(w, r)
		if ok != tc.ok || ok && limit != tc.limit {
			t.Fatalf("%s => %d %v", tc.query, limit, ok)
		}
	}
}
func TestDatabaseConfigBudgets(t *testing.T) {
	for _, ds := range []string{"postgresql://u:p@localhost/test?sslmode=disable", "postgresql://u:p@localhost/test?sslmode=disable&note=sslmode=verify-full&sslrootcert=x", "postgresql://u:p@localhost/test?sslmode=verify-full&sslmode=disable&sslrootcert=x", "postgresql://u:p@localhost/test?sslmode=verify-full&sslrootcert="} {
		if _, e := databaseConfig(ds, "aws"); e == nil {
			t.Fatal("accepted invalid aws tls", ds)
		}
	}
	cfg, e := databaseConfig("postgresql://u:p@localhost/test?sslmode=disable", "local")
	if e != nil || cfg.MaxConns != 20 || cfg.ConnConfig.RuntimeParams["application_name"] != "lb-backend" || cfg.ConnConfig.RuntimeParams["statement_timeout"] != "10000" {
		t.Fatalf("session config %#v %v", cfg, e)
	}
	for _, ttl := range []int64{1, 599, 86401} {
		if validTTL(ttl) {
			t.Fatal("invalid ttl accepted")
		}
	}
}

func TestPostgresSessionGuards(t *testing.T) {
	dsn := os.Getenv("GO_TEST_DATABASE_URL")
	if dsn == "" {
		t.Skip("set GO_TEST_DATABASE_URL to a dedicated _test database")
	}
	u, e := url.Parse(dsn)
	if e != nil || !strings.HasSuffix(u.Path, "_test") {
		t.Fatal("dedicated test database required")
	}
	cfg, e := databaseConfig(dsn, "local")
	if e != nil {
		t.Fatal(e)
	}
	pool, e := pgxpool.NewWithConfig(context.Background(), cfg)
	if e != nil {
		t.Fatal(e)
	}
	defer pool.Close()
	var name, timeout string
	e = pool.QueryRow(context.Background(), "SELECT current_setting('application_name'),current_setting('statement_timeout')").Scan(&name, &timeout)
	if e != nil || name != "lb-backend" || timeout != "10s" {
		t.Fatal(name, timeout, e)
	}
}

func TestInvalidUTF8AndYearZero(t *testing.T) {
	w := httptest.NewRecorder()
	r := httptest.NewRequest("POST", "/", bytes.NewReader([]byte{'{', '"', 'c', 'o', 'n', 't', 'e', 'n', 't', '"', ':', '"', 0xff, '"', '}'}))
	readInput(w, r, "post")
	if w.Code != 400 {
		t.Fatal("invalid UTF8 accepted", w.Code, w.Body.String())
	}
	cursor := base64.RawURLEncoding.EncodeToString([]byte(`["0000-01-01T00:00:00Z",1]`))
	if _, _, e := decodeCursor(cursor); e == nil {
		t.Fatal("year zero accepted")
	}
}
