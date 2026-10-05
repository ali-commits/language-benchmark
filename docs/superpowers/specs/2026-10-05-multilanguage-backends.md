# Additional social API implementations

Implement Java/Spring Boot, PHP/Laravel, Go, Rust, Node/Express and Bun/Express in the main checkout with subagent ownership. The running Python AWS campaign and its private configuration remain unchanged. Build locally and do not enroll new stacks in that running experiment.

The authoritative API/schema are contracts/api-v1.yaml, database/schema.sql, and the Python baseline source routes/schemas. Preserve endpoints, methods, status codes, payload fields, lowercase usernames, UTC timestamps, following-plus-own feed, descending (created_at,id) pagination, idempotent likes/follows and error behavior. Reject extra input fields and whitespace-only content. Body limit is 16384 bytes, including chunked input. Do not add caches, denormalized counters, weaker passwords or alternative schemas.

Inputs: DATABASE_URL, JWT_SECRET (minimum32 bytes), JWT_TTL_SECONDS (default3600), BIND_IP (default0.0.0.0), PORT (default8000), MODE, RUN_ID, RUN_DIR. Honor RDS TLS. Health/live and health/ready return {"status":"ok"}; readiness queries the database. Health/metrics exposes nonnegative connection_acquires and connection_acquire_ms. Never auto-migrate/seed.

Argon2id: memory65536KiB, time3, parallelism1, output32bytes, independently random salt16bytes. Verify existing Python PHC hashes. Unknown-user login does equivalent hash work with the same401 as wrong password. JWT: HS256, string sub, integer iat/exp, issuer language-benchmark, audience social-api. Reject expiry, future iat, invalid/missing claims and wrong algorithms. JWTs and cursors interoperate across stacks.

Docker budget: exactly2 CPUs, 3GiB memory, no swap, aggregate database connections20. Java uses current stable Spring Boot, latest stable JDK, virtual threads and Hikari. PHP uses current stable Laravel/PHP with a production server, OPcache, bounded workers, not artisan serve. Go uses standard net/http and pgxpool. Rust uses Axum/Tokio and pooled PostgreSQL. Node/Bun use identical Express code and two workers with10 connections each. JS dependencies/scripts use Bun; other stacks use native managers. Pin dependencies, image tags and lockfiles; verify stable versions from official upstream sources.

Use parameterized SQL, scoped transactions and release connections before password hashing. Idiomatic moderate optimization; comparisons measure realistic stack implementations, not isolated language speed. Do not silently lose bigint IDs or timestamp microseconds.

Provide Dockerfiles, READMEs, native tests and shared external HTTP contract verification on disposable local databases. Parent owns runtime staging definitions, shared tests and new Compose; leave Python runtime/campaign files unchanged. No AWS deployment in this task.

User refinement: use exactly one latest stable language/framework version per requested pair; no LTS preference or extra runtime variants. Verify versions from official upstream metadata and pin exact resolved versions.
