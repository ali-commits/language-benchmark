# Express social API shared by Node and Bun

Both runtime images execute these same sources with Express 5.2.1. By default production uses `node:cluster` with two independent workers, PostgreSQL `pg` pools of 10 connections each (20 aggregate) and one active Argon2 operation per worker (two aggregate), with at most 64 pending hash tasks per worker. Overflow returns 503; disconnected requests are removed from the queue before starting CPU work. Active native hashes may finish. Queries release their connection before login password verification. Mutations needing multiple queries run in transactions and commit before responding. There is no response cache or alternate schema.

PostgreSQL timestamps are returned as SQL-formatted UTC strings with six fractional digits. Cursor serialization and response JSON retain signed 64-bit IDs using native BigInts and json-bigint. Avoid converting API IDs to JavaScript Numbers in clients. Scalar Unicode input lengths match Python; input objects reject additional fields. Validation reports aggregate field errors in the same order, including the baseline error type/message/input/context fields and Python whitespace semantics. Express buffers at most 16,384 bytes before parsing, including chunked requests.

Passwords use Argon2id v19, 65,536 KiB, three iterations, one lane, independently random 16-byte salts and 32-byte hashes. Unknown usernames perform dummy password verification. argon2 0.45.1 emits PHC parameters in m,p,t order; stored hashes are canonicalized to m,t,p order for Python/libargon2 interoperability. The hash and verification work are unchanged. JWTs use HS256 and the shared issuer/audience, positive signed-64-bit string subjects, integer timestamps and the baseline 30-second future-iat tolerance.

## Configuration

`DATABASE_URL`, `JWT_SECRET` (at least 32 bytes), `JWT_TTL_SECONDS` (600–86400, default 3600), `BIND_IP` (default 0.0.0.0), `PORT` (default 8000), `MODE`, `RUN_ID`, `RUN_DIR`. AWS mode requires `sslmode=verify-full&sslrootcert=/mounted/path/to/ca.pem`; the CA is loaded without disabling hostname/certificate verification. No startup migration or seeding occurs. `/health/ready` queries PostgreSQL. `/health/metrics` and optional worker metrics files report actual connection checkouts and elapsed acquire time per worker. Every PostgreSQL lease uses `application_name=lb-backend` and a server-side 10-second statement deadline. Request cancellation prevents further SQL and a COMMIT that has not yet been dispatched, rolls back still-open transactions and discards the cancelled lease before reuse. A COMMIT already dispatched may complete and cannot subsequently be rolled back; a query already executing may run until the PostgreSQL deadline.

`WORKERS` accepts `2` (default) or `4`; each worker uses `20 / WORKERS` connections and one active hash, preserving pool budget 20 and queue limit 64. Build tuned Node/Bun images with `--build-arg WORKERS=4 --build-arg CONNECTIONS_PER_WORKER=5`; other build topologies are rejected. Runtime receipts are atomically written to `RUN_DIR/tuning-<serving PID>.json` with the current `RUN_ID`, effective pool maximum and hash capacity. CPU scheduling is measured outside these application processes.

For the four-worker Node profile on a 6 GiB container, set `NODE_OPTIONS=--max-old-space-size=1024`. Cluster children inherit it, limiting V8 old space to 1 GiB each and leaving room for native hashing, young-generation memory and the supervisor. Historical profiles retain their existing heap defaults. See [Node's documented memory control](https://nodejs.org/api/cli.html#--max-old-space-sizesize-in-mib). Bun 1.4.2 exposes [the `--smol` mode](https://bun.com/docs/runtime), which changes GC frequency and performance, but its CLI exposes no documented numeric per-process heap cap. The tuned Bun profile retains GC defaults under the common container limit; this does not impose independent per-worker memory caps.

Linux containers are required for Bun's clustered HTTP socket sharing. Bun's `node:http` bridge is used through Express; this does not replace Express with Bun's fetch API. The same launcher gracefully signals all workers and fails the container if a worker exits unexpectedly. Container/resource supervision remains outside the application.

## Dependencies and verification

Resolved from official upstream metadata on 2026-10-05: [Node release index](https://nodejs.org/dist/index.json), [Bun releases](https://github.com/oven-sh/bun/releases), [Express package](https://registry.npmjs.org/express/latest), [pg package](https://registry.npmjs.org/pg/latest), [argon2 package](https://registry.npmjs.org/argon2/latest), [jsonwebtoken package](https://registry.npmjs.org/jsonwebtoken/latest). Pins: Node 26.10.0, Bun 1.4.2, Express 5.2.1, pg 8.23.1, argon2 0.45.1, jsonwebtoken 9.0.3, json-bigint 1.0.0. `bun.lock` pins transitive dependencies; Docker installs it with `--frozen-lockfile` using Bun 1.4.2. Node 26 is the latest stable current release, rather than adding another LTS comparison cell.

From this directory:

```sh
bun install --frozen-lockfile
bun run test                   # Node test runner; integration tests require a database
bun test --timeout 15000 test                  # Bun test runner
EXPRESS_TEST_DATABASE_URL=postgresql://postgres:express_test_password@127.0.0.1:55435/language_benchmark_express_test bun run test
```

Integration tests refuse database names not ending in `_test`. Provision the canonical `database/schema.sql` first on a disposable local database. The tests exercise registration/login, following feed, idempotent likes, comments, validation, body limits, readiness, checkout metrics and both two-worker and four-worker production configurations. They also cover big IDs, microsecond cursors, invalid calendar dates/JSON ID tokens/UTF8, Unicode, PHC policy, JWT rejection, bounded queue overflow and cancellation, HTTP disconnects, real SQL deadlines and rollback/discard before reuse. Runtime Dockerfiles and run commands are in the sibling Node and Bun READMEs.
