# Additional backend stacks

Six local staging cells use one latest stable runtime/framework release each, verified on 2026-10-05. They implement the shared API and PostgreSQL schema. They are enrolled in a separate native AWS campaign after the completed Python campaign.

| Cell/profile | Runtime | Framework/data access | Production topology |
| --- | --- | --- | --- |
| java | Temurin Java27+35 | Spring Boot4.1.1, JDBC42.7.13 | Virtual threads, Hikari20, hash slots2 |
| php | PHP8.5.11 | Laravel13.34.0 | nginx/FPM20, OPcache, request-scoped PDO, shared hash slots2 |
| go | Go1.27.1 | net/http, pgx5.11.0 | One process, pool20, GOMAXPROCS2, GOMEMLIMIT2GiB |
| rust | Rust1.99.0 | Axum0.8.9, Tokio1.53.2, SQLx0.9.0 | One process, Tokio2, pool20, blocking hash slots2 |
| node | Node26.10.0 | Express5.2.1, pg8.23.1 | Cluster2; each pool10/hash1, pending hashes64 |
| bun | Bun1.4.2 | Same Express application and dependencies | Same cluster2/pool10/hash1 topology |

Exact dependencies, image digests, upstream sources and native test commands live in each backend README/lockfile. Runtime staging records are runtimes/java, php, go, rust, node and bun/lock.json. The original runtimes/python/lock.json is unchanged.

These are realistic stack comparisons, not isolated language-speed measurements. They use the same SQL/schema, data, crypto parameters and wire contract, while connection lifecycle, scheduling, native hash libraries and servers differ. Node and Bun deliberately share identical source. All containers enforce two CPUs, 3GiB memory and no swap, with at most20 database connections. Every application connection is tagged lb-backend and has a10-second statement timeout; AWS mode requires certificate/hostname-verifying TLS.

See [recorded verification evidence](multilanguage-verification.md) for image IDs and check results.

## Reproduce verification

From the repository root, with Docker, uv and OpenSSL installed:

```sh
PYTHONPATH=tools uv run -m bench.languages list
PYTHONPATH=tools uv run -m bench.languages build
PYTHONPATH=tools uv run -m bench.languages verify --output artifacts/my-language-verification
# Or select one stack:
PYTHONPATH=tools uv run -m bench.languages verify --language go --output artifacts/my-go-verification
```

Verification creates only uniquely named local containers/networks and a dedicated language_benchmark_matrix_test database. It resets that test schema between cases, checks Docker limits, exercises real verified TLS and a deliberately wrong certificate hostname, and removes its disposable containers afterward. Logs, exact image IDs and verification.json remain in the chosen output folder. Defaults use local ports55440/58040; change --db-port/--api-port if occupied. --no-tls is available for explicitly plain local verification. The verifier makes no AWS calls and supplies its own localhost test DSN.

All six stacks passed62 shared HTTP cases per stack, including20 exact baseline validation responses, Argon2id PHC/JWT interoperability, future nbf/expiry/issuer/audience, precise bigint/microsecond cursors, Unicode limits, chunked413, following feeds, idempotent likes/follows, committed writes, and a lock-held SQL timeout/session-tag test. Independent subagents reviewed the implementations and their scoped findings were fixed and rechecked.

The canonical reference is the existing Python3.10 implementation. Its image also passed all62 cases. Python3.14 expands ISO8601 parsing to accept24:00, unlike3.10; the corresponding in-process3.14 conformance case is explicitly skipped. Generated cursors and normal user journeys never contain that clock value. The existing Python application code was not changed.

## Interactive local backend

Use only one profile at a time; all share the same localhost API port8010 and PostgreSQL volume.

```sh
export JWT_SECRET="$(openssl rand -hex 32)"
docker compose -f compose.languages.yaml up -d db
# Once, on this new empty local database only:
docker compose -f compose.languages.yaml exec -T db \
  psql -U benchmark -d language_benchmark_languages < database/schema.sql
docker compose -f compose.languages.yaml --profile go up -d --build go
curl http://127.0.0.1:8010/health/ready
# Stop that backend before selecting another profile:
docker compose -f compose.languages.yaml stop go
```

The local database password is a test-only default; override LANGUAGE_DB_PASSWORD for local data you wish to preserve. Change LANGUAGE_PORT/LANGUAGE_DB_PORT if occupied. Apps never migrate or seed on startup. Use the existing guarded fixture tools when preparing the full50,000-user/500,000-post dataset for a later experiment.

The native controller uses exact verified image IDs, locked runtime/topology labels, fresh container nonces and HTTP database readiness rather than Python GIL/JIT worker attestations. The separate native AWS campaign is managed by language-benchmark-native-capacity and language-benchmark-native-supervisor. Functional verification alone does not establish capacity.
