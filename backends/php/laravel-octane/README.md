# Laravel Octane / RoadRunner backend

This is a separate PHP experiment. The original `backends/php/laravel` PHP-FPM application and registry remain unchanged. This application preserves the shared social API, PostgreSQL schema, Argon2id policy and JWT contract while using Laravel's ordinary persistent Octane worker lifecycle.

Pinned stack: PHP 8.5.11, Laravel 13.35.0, Octane 2.21.0, RoadRunner 2025.1.15, Composer 2.10.3. RoadRunner is a supported Octane engine with a current stable PHP CLI worker integration. It avoids switching to a server image that bundles a different PHP release. Laravel is one patch newer than the original FPM experiment (13.34.0); consequently, this is an Octane topology comparison with that dependency difference, not a controlled server-only causal experiment. No Swoole, FrankenPHP, JIT, development server, or alternate topology is included.

## Production topology and request isolation

One RoadRunner process schedules exactly 20 PHP workers. Each worker boots Laravel once and retains one lazy PDO object. The worker therefore has at most one physical PostgreSQL session; all workers together have at most 20. `PDO::ATTR_PERSISTENT` is deliberately unnecessary: the cached PDO object already persists across requests, and a hidden driver pool would obscure the connection budget. Workers recycle after 10,000 requests. The engine queue is bounded at 10,000 pending jobs; excess pressure can fail instead of growing without a limit.

Laravel Octane's default sandbox/reset listeners remain enabled. A warmed `Database` singleton contains only the worker's connection and lease state. Controllers hold user identity, validated inputs and responses in request-local variables. `ConnectionScope` always rolls back an abandoned transaction and clears lease state, including errors. Completed transactions retain their connection. Login ends its logical database lease before Argon2 hashing; the idle physical connection remains cached but no transaction or query is held during hashing. Across all workers, exactly two shared `flock` slots bound expensive Argon2 work. The 20 worker count bounds concurrent PHP requests.

A lost connection is invalidated. A read outside a transaction may reconnect and retry once; a failed mutation or in-flight transaction is never replayed. A connection failure before `BEGIN` can reconnect before any write occurs. PostgreSQL sessions use `application_name=lb-backend` and `statement_timeout=10000` for restoration and bounded queries. No schema migration or seed runs on startup.

The stateless API uses the required JWT secret. Octane also warms Laravel's encryption service, so startup derives its framework-only application key using a distinct HMAC domain and writes runtime config cache before workers start. No key is baked into the image. RoadRunner's static directory is empty in the production image; requests such as `/index.php` reach Laravel and return 404. RPC listens only on 127.0.0.1:6001; HTTP defaults to port 8000.

## Build and local verification

From the repository root:

```sh
docker build -f backends/php/laravel-octane/Dockerfile -t lb-php-laravel-octane:8.5.11 .
PYTHONPATH=tools uv run -m bench.languages verify --language php-octane --db-port 55655 --api-port 58075 --output artifacts/octane-contract-tls
```

The verification harness creates its own disposable PostgreSQL database, runs the complete shared HTTP suite, verifies CA/hostname TLS, rejects a wrong hostname, and checks the 2-vCPU / 3-GiB / no-swap container allocation. Native persistence tests require an isolated disposable database, never the benchmark RDS database:

```sh
docker run --rm --network host --entrypoint php \
  -e DATABASE_URL=postgresql://benchmark:benchmark@127.0.0.1:55654/language_benchmark_octane_test \
  lb-php-laravel-octane:8.5.11 /app/tests/persistence.php
docker run --rm --entrypoint php lb-php-laravel-octane:8.5.11 /app/tests/hash_slots.php
TARGET_URL=http://127.0.0.1:58074 \
  TEST_DATABASE_URL=postgresql://benchmark:benchmark@127.0.0.1:55654/language_benchmark_octane_test \
  uv run python backends/php/laravel-octane/tests/http_persistence.py
```

Required runtime variables: `DATABASE_URL`, `JWT_SECRET` (at least 32 bytes). Optional `JWT_TTL_SECONDS` defaults to 3600; `BIND_IP`/`PORT` default to 0.0.0.0/8000. `MODE=aws` requires an exact `sslmode=verify-full` URL parameter and a nonempty `sslrootcert` mounted read-only. The experiment runner supplies `RUN_ID` and `RUN_DIR` as metadata. Run with `--cpus 2 --memory 3g --memory-swap 3g --ulimit nofile=250000:250000`; the separate database retains its own budget. OPcache is enabled for CLI, JIT disabled. PHP's per-worker memory limit is 256 MiB; the aggregate cgroup is 3 GiB.

`/health/metrics` counts actual logical connection checkouts (including reconnects) and checkout milliseconds, aggregated with a locked shared file; it does not fabricate acquisition counts or represent connection creation count. `/health/live` and `/health/ready` follow the shared contract.

All Composer packages and base image digests are locked. `runtimes/php-octane/source-proof.json` records upstream sources, exact image identity and local test evidence. New capacity tests use 300 seconds ramp, no settle, 120 seconds hold and a 90% gate; fixed comparisons use 180/0/240 at 100, 500 and 1,000 users. Existing FPM results and protocols remain separate.
