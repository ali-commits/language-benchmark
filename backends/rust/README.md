# Rust social API

One Axum process uses a two-thread Tokio runtime and a SQLx PostgreSQL pool capped at 20 connections. CPU-bound Argon2 work runs on blocking threads with a two-permit semaphore, and login releases its database connection before password verification. All mutation responses follow successful commit. All database sessions identify as `lb-backend` and enforce a 10-second statement timeout. PostgreSQL timestamps retain microseconds and IDs remain signed 64-bit integers. Feed and comments use descending `(created_at,id)` pagination, with following and own posts only. No caches, denormalized counts, migrations or automatic seed are added.

Pinned stable versions, verified October 5, 2026: Rust **1.99.0**, Axum **0.8.9**, Tokio **1.53.2**, SQLx **0.9.0**, Argon2 **0.6.0**, jsonwebtoken **11.1.0**. Exact direct versions are in `Cargo.toml` and all transitive dependencies in `Cargo.lock`. Upstream sources: [Rust stable channel](https://static.rust-lang.org/dist/channel-rust-stable.toml), [Axum](https://docs.rs/axum/0.8.9/axum/), [Tokio](https://docs.rs/tokio/1.53.2/tokio/), [SQLx](https://docs.rs/sqlx/0.9.0/sqlx/), [Argon2](https://docs.rs/argon2/0.6.0/argon2/), [JWT](https://docs.rs/jsonwebtoken/11.1.0/jsonwebtoken/).

From the repository root:

```sh
docker build -f backends/rust/Dockerfile -t lb-rust:1.99.0 .
(cd backends/rust && cargo test --locked && cargo clippy --all-targets --locked -- -D warnings)
```

The image builds in release mode with ordinary thin LTO and runs as a non-root user. Run one container with `--cpus=2 --memory=3g --memory-swap=3g`.

Set `DATABASE_URL`, `JWT_SECRET` (at least 32 bytes), optionally `JWT_TTL_SECONDS` (default 3600, range 600–86400), `BIND_IP` (default `0.0.0.0`) and `PORT` (default 8000). `MODE`, `RUN_ID` and `RUN_DIR` are campaign metadata; this single-process service does not emit Python worker-attestation files. `MODE=aws` requires exactly one `sslmode=verify-full` and one nonempty `sslrootcert` parameter. RDS TLS uses SQLx's Rustls transport: include `sslmode=verify-full&sslrootcert=/certs/rds-ca.pem` in the URL and mount the CA.

`/health/live` reports liveness; `/health/ready` actually queries PostgreSQL. `/health/metrics` counts successful pool checkouts and cumulative checkout milliseconds, including readiness calls. SQLx 0.9's `AssertSqlSafe` wrapper is only used for SQL composed from internal literal fragments; request data remains parameterized.

The shared external HTTP checks passed against a disposable local PostgreSQL database:

```sh
TARGET_URL=http://127.0.0.1:18037 \
TEST_DATABASE_URL=postgresql://postgres:rust-test-only@127.0.0.1:55437/language_benchmark_rust_test \
uv run -m pytest tests/contract tests/multilanguage -q
```

Use `JWT_SECRET=test-secret-that-has-at-least-32-bytes` for this test command. Tests drop/recreate public schema, so only use a dedicated database whose name ends in `_test`. Shared tests verify Python PHC/JWT compatibility, microsecond cursors above JavaScript's safe integer range, following-feed semantics, committed writes, idempotence and strict body validation.

Set `RUST_TEST_DATABASE_URL` to a dedicated local `_test` PostgreSQL database when running native tests to verify actual session application name and statement timeout. Other native tests do not require a database.
