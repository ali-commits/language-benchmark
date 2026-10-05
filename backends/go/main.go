package main

import (
	"bytes"
	"context"
	"fmt"
	"github.com/jackc/pgx/v5/pgxpool"
	"io"
	"log"
	"net/http"
	"os"
	"os/signal"
	"strconv"
	"strings"
	"sync/atomic"
	"syscall"
	"time"
)

type app struct {
	pool      *pgxpool.Pool
	secret    []byte
	ttl       int64
	acquires  atomic.Uint64
	acquireNs atomic.Uint64
}

func itoa(n int) string { return strconv.Itoa(n) }
func env(k, def string) string {
	v := os.Getenv(k)
	if v == "" {
		return def
	}
	return v
}
func (a *app) checkout(ctx context.Context) (*pgxpool.Conn, error) {
	start := time.Now()
	c, e := a.pool.Acquire(ctx)
	if e == nil {
		a.acquires.Add(1)
		a.acquireNs.Add(uint64(time.Since(start)))
	}
	return c, e
}
func (a *app) handler() http.Handler {
	mux := http.NewServeMux()
	mux.HandleFunc("GET /health/live", func(w http.ResponseWriter, r *http.Request) { respond(w, 200, map[string]string{"status": "ok"}) })
	mux.HandleFunc("GET /health/ready", func(w http.ResponseWriter, r *http.Request) {
		c, e := a.checkout(r.Context())
		if e != nil {
			failure(w, 503, "database unavailable")
			return
		}
		defer c.Release()
		if e = c.QueryRow(r.Context(), "SELECT 1").Scan(new(int)); e != nil {
			failure(w, 503, "database unavailable")
			return
		}
		respond(w, 200, map[string]string{"status": "ok"})
	})
	mux.HandleFunc("GET /health/metrics", func(w http.ResponseWriter, r *http.Request) {
		respond(w, 200, map[string]any{"connection_acquires": a.acquires.Load(), "connection_acquire_ms": float64(a.acquireNs.Load()) / 1e6})
	})
	mux.HandleFunc("POST /api/v1/auth/register", a.register)
	mux.HandleFunc("POST /api/v1/auth/login", a.login)
	for _, route := range []string{"GET /api/v1/users/me", "POST /api/v1/posts", "GET /api/v1/posts/{post_id}", "GET /api/v1/feed", "PUT /api/v1/users/{target_id}/follow", "DELETE /api/v1/users/{target_id}/follow", "PUT /api/v1/posts/{post_id}/like", "DELETE /api/v1/posts/{post_id}/like", "POST /api/v1/posts/{post_id}/comments", "GET /api/v1/posts/{post_id}/comments"} {
		mux.HandleFunc(route, a.social)
	}
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Method == "POST" || r.Method == "PUT" || r.Method == "PATCH" {
			body, err := io.ReadAll(io.LimitReader(r.Body, 16385))
			if err != nil {
				failure(w, 400, "invalid request")
				return
			}
			if len(body) > 16384 {
				failure(w, 413, "request body too large")
				return
			}
			r.Body = io.NopCloser(bytes.NewReader(body))
		}
		mux.ServeHTTP(w, r)
	})
}
func main() {
	secret := env("JWT_SECRET", "")
	if len(secret) < 32 {
		log.Fatal("JWT_SECRET must have at least 32 bytes")
	}
	ttl, e := strconv.ParseInt(env("JWT_TTL_SECONDS", "3600"), 10, 64)
	if e != nil || !validTTL(ttl) {
		log.Fatal("invalid JWT_TTL_SECONDS")
	}
	cfg, e := databaseConfig(os.Getenv("DATABASE_URL"), env("MODE", "local"))
	if e != nil {
		log.Fatal(e)
	}
	pool, e := pgxpool.NewWithConfig(context.Background(), cfg)
	if e != nil {
		log.Fatal(e)
	}
	defer pool.Close()
	dummyHash, e = hashPassword("dummy-only-not-a-valid-user-password")
	if e != nil {
		log.Fatal(e)
	}
	a := &app{pool: pool, secret: []byte(secret), ttl: ttl}
	server := &http.Server{Addr: fmt.Sprintf("%s:%s", env("BIND_IP", "0.0.0.0"), env("PORT", "8000")), Handler: a.handler(), ReadHeaderTimeout: 5 * time.Second, IdleTimeout: 60 * time.Second}
	ctx, cancel := signal.NotifyContext(context.Background(), syscall.SIGINT, syscall.SIGTERM)
	defer cancel()
	go func() {
		<-ctx.Done()
		stop, c := context.WithTimeout(context.Background(), 30*time.Second)
		defer c()
		_ = server.Shutdown(stop)
	}()
	log.Printf("Go social API listening on %s mode=%s", server.Addr, strings.TrimSpace(env("MODE", "standard")))
	if e = server.ListenAndServe(); e != nil && e != http.ErrServerClosed {
		log.Fatal(e)
	}
}
