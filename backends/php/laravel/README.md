# PHP / Laravel

One implementation: PHP **8.5.11**, Laravel **13.34.0**, Composer **2.10.3**. `composer.lock` pins the complete PHP dependency graph.

A production nginx + PHP-FPM topology uses exactly **20 FPM workers**, OPcache, and nonpersistent PostgreSQL PDO connections (one per worker, maximum20 total). Parameterized SQL shares the original schema; mutations use scoped transactions. A shared two-slot `flock` semaphore bounds Argon2 memory/CPU work across all workers. Account lookup releases its connection before password verification. This is a conventional FPM deployment, with no developer server, Octane or JIT variant.

Build from repository root:

```sh
docker build -f backends/php/laravel/Dockerfile -t lb-php-laravel:8.5.11 .
docker run --rm --cpus=2 --memory=3g --memory-swap=3g -p 8000:8000 \
  -e DATABASE_URL='postgresql://user:password@db:5432/language_benchmark' \
  -e JWT_SECRET='replace-with-at-least-32-bytes-secret' lb-php-laravel:8.5.11
```

The build runs native protocol tests. Run `composer test` with PHP8.5 or `docker run --rm lb-php-laravel:8.5.11 php tests/run.php`. Shared HTTP checks use root `uv run -m pytest tests/contract tests/multilanguage`, with `TARGET_URL` and a dedicated `TEST_DATABASE_URL` named `language_benchmark*_test`. Local worker verification used disposable PostgreSQL port55439 and API18039.

`DATABASE_URL` supports URL-escaped credentials and TLS `sslmode` / `sslrootcert`; `MODE=aws` requires verify-full and a mounted RDS CA file. `JWT_SECRET` requires32 bytes; `JWT_TTL_SECONDS` defaults3600 (600–86400). `PORT` defaults8000; `BIND_IP` defaults0.0.0.0. `RUN_ID` and `RUN_DIR` are optional orchestration metadata. No migrations/seeding run automatically. IDs remain native64-bit integers, dates retain microseconds, and JWT/Argon2id PHC/cursors interoperate with Python.

Nginx and the application both enforce16384-byte bodies; nginx covers chunked ingress. `/health/metrics` aggregates actual PDO checkouts and connection setup time across FPM workers under a file lock. FPM worker recycling does not reset this total. Metrics and hash locks are local to the single API container. Do not share its `/tmp` directory with another API instance.

The entrypoint gracefully shuts down both nginx and FPM when sent TERM/INT and exits if either server dies. This topology is meant for one API container with the repository's2-vCPU/3GiB/no-swap budget.

Version sources: [PHP8.5.11](https://www.php.net/releases/8_5_11.php), [Laravel releases](https://github.com/laravel/framework/releases), [Laravel13 release notes](https://laravel.com/framework/docs/releases). FPM's maximum workers provides an aggregate connection cap rather than a persistent application connection pool.

Verified locally: all **62 external contract/interoperability tests** passed, including the captured Python validation-response oracle. Wait for `/health/ready` before starting verification or measurement. The shared harness emits one existing Starlette/httpx deprecation warning.

All database sessions identify as `lb-backend` for the shared restore guard and use a10-second PostgreSQL statement timeout. AWS TLS settings reject duplicate or ambiguous parameters. JWT validation includes optional `nbf` and audience arrays.

`HASH_CONCURRENCY` accepts integers 1–4 and defaults to 2. The four-CPU
profile builds with `--build-arg HASH_CONCURRENCY=4`; runtime overrides are
validated before the server starts. Admission uses shared file locks across
all 20 workers, with one database connection per worker. Argon2 parameters,
worker recycling, OPcache/JIT and connection lifetime are unchanged.

When `RUN_DIR` is set, `RUN_ID` is required. Initialized serving workers
atomically publish `tuning-<PID>.json` in that existing writable directory.
Receipts contain safe topology and effective global hash capacity;
`cpu_parallelism` is null because PHP has no scheduler control. FPM emits on
application initialization, and Octane on its WorkerStarting event (Laravel
initialization happens as requests reach each RoadRunner worker).

Run `php tests/tuning.php` and `php tests/hash_slots.php` in a dedicated
container in addition to the existing protocol tests. Hash tests exercise real
cross-process flock admission and canonical Argon2id hashing; a deterministic
test-only hashing barrier proves four holders and a blocked fifth process.
