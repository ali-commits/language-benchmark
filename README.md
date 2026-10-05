# Social API language benchmark

A controlled Docker/k6 experiment with Python baseline/modern APIs and six additional language/framework cells. The API implements password authentication, following feeds, posts, likes and comments. This repository builds the experiment; AWS performance numbers must be collected on the specified AWS hosts.

| Directory | Responsibility |
| --- | --- |
| `backends/python/baseline` | Python 3.10-compatible FastAPI / SQLAlchemy async application; one dependency set across 3.10–3.15 |
| `backends/python/modern` | Python 3.15 FastAPI / native Psycopg async and threaded applications; current typed generic response models |
| `database` | PostgreSQL schema and Alembic migrations |
| `tools/bench` | Fixture generation, guarded restoration, runtime checks, trial runner and reports |
| `benchmarks/k6` | Arrival-rate capacity scenarios, mixed operations, warm-up, user journeys and calibration |
| `docker` | Matched source-built CPython and uv application images |

See the [live AWS deployment and dashboard](docs/aws-deployment.md), [repository layout](docs/file-structure.md), [verification evidence](docs/verification.md), [implementation decisions](docs/implementation-decisions.md), [methodology](docs/methodology.md), [AWS runbook](docs/aws-runbook.md), [runtime support](docs/runtime-support.md), the [approved specification](docs/superpowers/specs/2026-10-04-python-fastapi-benchmark-design.md), and [implementation plan](docs/superpowers/plans/2026-10-04-python-fastapi-benchmark.md).

## Additional languages

Java/Spring Boot, PHP/Laravel, Go/net-http, Rust/Axum, Node/Express and Bun/Express are available as local staging implementations. Each uses one latest stable version, the same API/schema, two CPU quota,3GiB/no-swap and20 total database connections. Node and Bun share the same Express source. See [versions, production topology and reproducible verification](docs/multilanguage-backends.md).

```sh
PYTHONPATH=tools uv run -m bench.languages build
PYTHONPATH=tools uv run -m bench.languages verify --output artifacts/language-check
```

These staging cells are separate from the active Python AWS campaign; functional verification does not establish capacity results.

## Concurrent-user experiment

The primary experiment uses distinct logged-in users, each fetching a following feed, waiting a random 3–10 seconds, liking and replying. It starts at 10,000 concurrent users, increases by 10,000 until a response failure, then searches the bracket to within 100 users. Every probe restores the canonical database. See [concurrent-user methodology](docs/user-capacity-methodology.md) for response deadlines, generator qualification and repeated boundary confirmation.

```sh
PYTHONPATH=tools uv run -m bench.cli user-capacity \
  --config artifacts/aws/user-capacity.json --output artifacts/results/user-capacity
```

This requires the private AWS configuration and 50,000 unique login credentials. Arrival-rate pilot results describe a different workload and must not be presented as concurrent-user capacity. If all 50,000 users pass, the result is a lower bound at the fixture ceiling.

## Prerequisites

Linux, Docker Engine/Compose, uv, and PostgreSQL client tools matching the database major version. Published source images target x86-64. Docker builds can take several minutes per interpreter; Python 3.15 also builds Pydantic Core with a pinned Rust toolchain. Build resources are independent of the measured two-vCPU runtime limit.

Use uv for environments, locks, installation and commands. Root and application `uv.lock` files are committed. Application `requirements.lock` files are hashed uv exports used in Docker. CPython itself is compiled from the checksum-pinned sources so vendor-build differences do not confound measured interpreter comparisons.

```sh
uv sync --locked
uv run python runtimes/python/build.py --apps
uv run python runtimes/python/build.py --version 3.15.0rc3 --mode jit --apps
uv run python runtimes/python/build.py --version 3.15.0rc3 --mode free-threaded --apps
```

The pinned 3.15 release is **3.15.0rc3**, not a stable release. The modern stack pins **Pydantic 2.14.0b2 / Core 2.49.0** for the new free-threaded ABI; the baseline keeps stable Pydantic 2.13.5 across all versions. `--smoke` builds use a different image prefix and lack PGO/LTO; do not substitute them for measured images.

## Local first run

Copy `.env.example` to a private `.env`, supply a randomly generated JWT secret and a test-only seed password. Export the variables into your shell (`set -a; source .env; set +a`) before using tooling. Do not commit that file. The application rejects missing/short secrets.

```sh
docker compose up -d db
export PYTHONPATH=tools
uv run -m bench.cli migrate
uv run -m bench.cli mark --database language_benchmark
uv run -m bench.cli seed --small
uv run -m bench.cli capture --output artifacts/small-fixture
uv run -m bench.cli credentials --count 20 --output artifacts/small-credentials.json
mkdir -p artifacts/runtime
chmod 777 artifacts/runtime
# Only one profile at a time; both applications bind the same port.
docker compose --profile baseline up -d baseline
# Or: docker compose --profile modern up -d modern
```

Open `http://127.0.0.1:8000/docs` for the API. Stop the interactive backend before running a campaign. `compose.aws.yaml` uses Linux host networking so the runner can connect to a local PostgreSQL port or private RDS address; its bind address defaults to loopback. Local `compose.yaml` uses ordinary Compose networking.

For local fixture capture/restore without installed PostgreSQL clients, set `DB_CLIENT_CONTAINER` to the name from `docker compose ps -q db` (ID is accepted). This helper only supports local database addresses and the known local DB container's socket. Leave it unset for RDS.

```sh
docker compose --profile baseline stop baseline
# benchmarks/smoke.json is explicitly local smoke: 3-second warm-up, 10-second measurement.
# Adjust its fixture, credentials and cell selection for your build; default is baseline 3.15 rc3.
uv run -m bench.cli run --config benchmarks/smoke.json --output artifacts/local-smoke
uv run -m bench.cli report --results artifacts/local-smoke --output artifacts/local-report.html
```

Use a fresh output directory for every campaign. The runner stops the load/backend, restores data before and after each trial, checks every worker, and keeps failed trials. A cleanup failure stops subsequent trials; use `recover` after stopping services and resolving the cause.

## Full fixture

Start with an empty migrated benchmark database. The seed command refuses populated users; do not rerun it over an existing fixture. Full seeding computes 50,000 real Argon2id hashes with independent salts and can take minutes. `SEED_HASH_WORKERS` controls seed preparation only; it does not tune timed login behavior.

```sh
uv run -m bench.cli seed
uv run -m bench.cli capture --output artifacts/full-fixture
uv run -m bench.cli credentials --count 200 --output artifacts/credentials.json
```

Full data: 50,000 users, 500,000 posts, 2,500,000 follows, 1,000,000 likes and 250,000 comments. Capture once and reuse the same dump/checksum across every cell. Dumps and credentials are private untracked artifacts.

## Verification

Contract tests use a **dedicated disposable** `language_benchmark_test` database and recreate its public schema for each test. Never point tests at your canonical AWS fixture database. Set `TEST_DATABASE_URL` for the test DB; default is `language_benchmark_test` on the development verification container port 55432. Test database names must end `_test`; ordinary benchmark databases are rejected.

```sh
uv run -m pytest -q
uvx ruff check backends tools tests runtimes docker/*.py
# Baseline-only tests on an older interpreter:
BASELINE_ONLY=1 uv run --python 3.10.22 -m pytest tests/contract tests/unit/test_cursor.py -q
```

Python 3.15 and free-threading builds may need native dependencies compiled from source. Runtime checks reject GIL re-enablement after imports. The modern implementation uses native Psycopg pools rather than a greenlet bridge to make that check meaningful. JIT on/off and GIL on/off are distinct comparisons; a combined JIT/no-GIL mode is not promised.

No AWS resources are created by these commands. Use the runbook to provision and calibrate separate backend, generator and RDS resources before publishing performance claims.
