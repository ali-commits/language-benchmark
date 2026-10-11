# Production-guided four-CPU configurations

Ali explicitly selected one configuration per stack based on official production guidance and senior engineering judgment, without a configuration sweep. This supersedes equal database pool budgets for the next comparison. Preserve the active tuned4 run unchanged as historical evidence; deploy the new comparison separately after it completes. Configurations are informed choices, not claims of experimentally proven global optima.

## Invariants

- Same c7a.xlarge backend, four CPUs, eight nominal GiB; common application limit four CPUs/six GiB/no swap/nofile250000. Same RDS, generator and monitoring.
- Same API, schema, parameterized queries, following feed, 50,000-user/500,000-post fixture, password strength, JWT semantics, TLS verification and isolation. No application-result caching or write replay.
- Same 300-second ramp/120-second hold, real login and random 3–10-second waits, five-second deadline, p95/p99 targets and separate 90% operation/authentication gates; upper_probe, resolution50, majority on disagreement.
- One selected profile per existing configuration, immutable new image names ending `-production4`. Keep original locks, historical images and `tuned4.json` immutable. Retain each existing runtime/framework version; only add pinned compatible Python server accelerators to an isolated production dependency lock.
- Pool settings describe real aggregate maxima and minimums, including per-process fragmentation. Runtime receipts and actual process/connection/TLS readiness must attest selected values; replace hardcoded20 assumptions only for new profiles.

## Selected profiles

| Stack | Processes / execution | Database | Other selected controls |
|---|---|---|---|
| Python modern FastAPI | Four Uvicorn workers; uvloop, httptools | Psycopg async pool min2/max10 per worker, aggregate40 | One active hash per worker; AnyIO16; no security/serialization shortcut |
| Node Express | Four cluster workers | pg max10 per worker, aggregate40 | Production mode; libuv4; one active hash/worker; pending64; old-space1024MiB/worker |
| Bun Express | Four supported Bun cluster workers using native Linux reusePort, same Express application | pg max10 per worker, aggregate40 | Production mode; normal GC; one active hash/worker; pending64; graceful worker lifecycle |
| Java Spring Boot | One JVM, virtual request threads, dedicated four-platform-thread hash executor | Fixed/prewarmed Hikari16 | Four global hash permits held until physical computation ends; G1 defaults, heap65%, processors4 |
| Go net/http | One process, GOMAXPROCS4 | pgx min4/max16 | GOMEMLIMIT5GiB, GOGC100, four hashes; existing prepared statement cache |
| Rust Axum | One process, Tokio4; bounded spawn_blocking | SQLx min4/max16 | Four hash permits held through actual computation; standard allocator/preparation |
| C# ASP.NET Core | One process, processor count4, server/background GC | Npgsql min4/max16 | Heap65%; four hashes; MaxAutoPrepare64, AutoPrepareMinUsages5, Enlist=false |
| Laravel FPM | Static32 request workers | One persistent PDO/process, aggregate32 | Explicit rollback/release safety; no write retries; global hashes4; PHP memory128MiB; recycle10000 |
| Laravel Octane / RoadRunner | Fixed32 request workers, pipes | One safely retained PDO/process, aggregate32 | Global hashes4; PHP memory128MiB; recycle10000; RR soft memory recycle256MiB |

Both PHP profiles use OPcache256MiB, interned strings16MiB, files20000, timestamp validation off, JIT off; Octane CLI OPcache enabled. Preserve production debug/cache/autoload settings already in place. Counts and exact numerical limits are documented engineering choices; official documentation supplies mechanisms and tradeoffs, not a universal optimum.

## Primary guidance

- [FastAPI workers](https://fastapi.tiangolo.com/deployment/server-workers/), [Uvicorn settings](https://www.uvicorn.org/settings/), [Psycopg pools](https://www.psycopg.org/psycopg3/docs/advanced/pool.html), [uvloop releases](https://github.com/MagicStack/uvloop/releases).
- [Express production](https://expressjs.com/en/advanced/best-practice-performance/), [node-postgres pool sizing](https://node-postgres.com/guides/pool-sizing), [Node CLI](https://nodejs.org/api/cli.html), [Bun clustering](https://bun.sh/guides/http/cluster), [Bun node:http listen](https://bun.com/reference/node/http/Server/listen).
- [Hikari sizing](https://github.com/brettwooldridge/HikariCP/wiki/About-Pool-Sizing), [Oracle virtual threads](https://docs.oracle.com/en/java/javase/25/core/virtual-threads.html), [Spring task execution](https://docs.spring.io/spring-boot/reference/features/task-execution-and-scheduling.html).
- [Go GC guide](https://go.dev/doc/gc-guide), [pgxpool](https://pkg.go.dev/github.com/jackc/pgx/v5/pgxpool), [Tokio blocking work](https://docs.rs/tokio/latest/tokio/task/fn.spawn_blocking.html), [SQLx pools](https://docs.rs/sqlx/latest/sqlx/struct.Pool.html).
- [ASP.NET performance](https://learn.microsoft.com/en-us/aspnet/core/fundamentals/best-practices?view=aspnetcore-10.0), [.NET GC](https://learn.microsoft.com/en-us/dotnet/core/runtime-config/garbage-collector), [Npgsql performance](https://www.npgsql.org/doc/performance.html), [connection parameters](https://www.npgsql.org/doc/connection-string-parameters.html).
- [PDO connections](https://www.php.net/manual/en/pdo.connections.php), [FPM](https://www.php.net/manual/en/install.fpm.configuration.php), [OPcache](https://www.php.net/manual/en/opcache.configuration.php), [Laravel deployment](https://laravel.com/framework/docs/deployment), [RoadRunner production](https://docs.roadrunner.dev/docs/app-server/production), [RoadRunner pool](https://docs.roadrunner.dev/docs/php-worker/pool).

## Verification and operational handoff

Use focused unit/concurrency/cancellation/isolation tests and strict engine typing/formatting/lint hooks. Validate actual normal image entrypoints and resource/pool/hash/process attestations for all nine images, shared API behavior, TLS and physical connection reuse/cleanup. These are correctness checks, not configuration-selection load tests. Native CPU cancellation must not release permits while computation continues. Persistent PDO must not leak transactions or per-request state. Bun must prove four live distinct processes serve the shared port and shut down cleanly. Pinned Bun1.4.2 ignores reusePort on independently spawned node:http listeners; actual validation selected its supported cluster-worker SO_REUSEPORT path instead of introducing an adapter or changing runtime version.

Do not hot-update the running AWS suite, its selectors, images or source. Build and verify separately, record immutable source/image identities, preserve all original/tuned results and actual homelab backups. After the current run completes with verified cleanup, install a fresh supervised queue with fresh checkpoints and remeasure previous passing counts as hints. No old performance votes carry over. Preserve idle-stop protections throughout preparation and the followup; final scoped shutdown still requires actual verified backups of all completed campaigns.
