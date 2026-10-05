# Go social API

One Go process uses the standard `net/http` server, a 20-connection pgx pool and parameterized PostgreSQL queries. Argon2 work is bounded to two goroutines; login returns its connection before verifying the password. Writes commit before responses. Cancelled requests leave the password-hash queue before expensive work starts. All database sessions identify as `lb-backend` and enforce a 10-second statement timeout. The following feed and comments use `(created_at,id)` keyset pagination, preserving PostgreSQL microseconds and signed 64-bit IDs. There are no result caches, denormalized counts, migrations or automatic seeding.

Pinned stable versions, verified October 5, 2026: Go **1.27.1**, pgx **5.11.0**, golang-jwt **5.3.1**, x/crypto **0.57.0**. All transitive modules are recorded in `go.sum`. Upstream sources: [Go releases](https://go.dev/dl/?mode=json), [pgx](https://github.com/jackc/pgx/releases), [JWT](https://github.com/golang-jwt/jwt/releases), [x/crypto](https://pkg.go.dev/golang.org/x/crypto).

From the repository root:

```sh
docker build -f backends/go/Dockerfile -t lb-go:1.27.1 .
(cd backends/go && go test -race ./... && go vet ./...)
```

Set `DATABASE_URL`, `JWT_SECRET` (at least 32 bytes), optionally `JWT_TTL_SECONDS` (default 3600, range 600–86400), `BIND_IP` (default `0.0.0.0`), and `PORT` (default 8000). `MODE`, `RUN_ID` and `RUN_DIR` are campaign metadata; this single-process service does not emit Python worker-attestation files. `MODE=aws` requires exactly one `sslmode=verify-full` and one nonempty `sslrootcert` parameter. Supply `sslmode=verify-full&sslrootcert=/certs/rds-ca.pem` in the database URL and mount the CA when using RDS.

Run one container with `--cpus=2 --memory=3g --memory-swap=3g`, and use its internal PostgreSQL port. `GOMAXPROCS=2` and the soft Go heap limit `GOMEMLIMIT=2GiB` are set in the image, leaving headroom within the 3GiB container budget. Health endpoints are `/health/live`, `/health/ready` and `/health/metrics`; metrics count successful pool checkouts and cumulative checkout milliseconds, including readiness requests.

The shared external HTTP checks passed against a disposable local PostgreSQL database:

```sh
TARGET_URL=http://127.0.0.1:18036 \
TEST_DATABASE_URL=postgresql://postgres:go-test-only@127.0.0.1:55436/language_benchmark_go_test \
uv run -m pytest tests/contract tests/multilanguage -q
```

Use `JWT_SECRET=test-secret-that-has-at-least-32-bytes` for this test command. Tests drop/recreate the public schema, so only use a dedicated database whose name ends in `_test`. Production database preparation uses the repository's existing schema/fixture tooling; the API never mutates the schema.

Set `GO_TEST_DATABASE_URL` to a dedicated local `_test` PostgreSQL database when running native tests to verify the actual session application name and statement timeout. Without it that integration check is skipped.
