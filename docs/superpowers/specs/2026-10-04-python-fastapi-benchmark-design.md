# Python FastAPI benchmark experiment

Date: 2026-10-04
Status: Written specification for user review; implementation has not started.

## 1. Intent and agreed constraints

Build a reproducible experiment for a realistic Twitter-style API, initially comparing Python rather than other languages. The user requests two codebases: one compatible with Python 3.10 and tested on 3.10 through 3.15, and one designed for Python 3.15 and current FastAPI capabilities. Use Docker and Docker Compose, a backend EC2 VM with two vCPUs, RDS PostgreSQL, and k6 on a substantially larger, separate EC2 VM. Restore the database between experiments. Seed exactly 50,000 users and 500,000 posts.

The user approved separating interpreter comparisons from application/dependency changes, separate JIT and free-threading experiments, following feeds, deterministic data, randomized repeated trials, and checking whether the load generator or database is the bottleneck.

Success means runnable applications and a harness that produces auditable results, including unsuccessful and unsupported configurations. No performance ranking is assumed. A database-bound result or a slower JIT/free-threaded result is useful evidence.

The source video could not be retrieved; this experiment does not claim to reproduce or critique its exact implementation.

## 2. Deliverables and boundaries

- Two independent applications in `apps/baseline` and `apps/modern`, with their own dependencies and Docker build inputs.
- A shared, versioned HTTP contract and database schema, migrations, deterministic seed tooling, reset tooling, and correctness tests. Sharing these controls keeps application behavior comparable; application implementations remain separate.
- Compose configurations for local development, AWS backend execution against external RDS, and an independent k6 load-generator host.
- A manifest-driven runner for preflight, reset, warm-up, measurement, collection, and report generation.
- An AWS setup/runbook describing VM sizing, network placement, database settings, credential configuration, and cleanup. No cloud resources are provisioned by writing this specification or building the repository.
- Reports containing raw measurement artifacts, per-trial manifests, statistical summaries, and invalid/unsupported trials with reasons.

This phase excludes frontend UI, media uploads, search, notifications, Redis, background feed fan-out, microservices, and other programming languages. The simple application and benchmark boundaries should allow another backend language later without building a generic framework now.

## 3. Experiment families

### A. Interpreter comparison

Run identical baseline source and the same compatible, locked dependency versions on exact selected patch releases of Python 3.10, 3.11, 3.12, 3.13, 3.14, and 3.15. Baseline compatibility is about Python syntax/APIs, not preserving obsolete FastAPI releases. First find and test a common dependency set across this matrix; do not silently upgrade dependencies per interpreter.

Use standard GIL-enabled builds, identical server settings, OS base, architecture, connection budgets, memory limits, and compiler optimization policy. Prefer matched source-built CPython images for published measurements to control build flags; convenient vendor images may be supplied for local smoke tests and must be labeled separately. Pin source checksums, OS images by digest, dependency versions, and build tooling. Native wheels can legitimately differ by Python ABI, but record their identity and build provenance. A missing compatible dependency/build marks a cell unsupported until resolved; it does not justify quietly changing the comparison.

Primary deployment uses two Uvicorn worker processes. Also provide a one-process diagnostic comparison, run separately. A two-vCPU VM does not mean two independent physical cores: record the CPU model and exposed topology.

### B. Modern application comparison

Run the modern application on Python 3.15 standard builds against the baseline application on the same Python release. The modern code may use current Python APIs and FastAPI/Pydantic idioms where they improve real application code. Do not insert syntax features solely to call the application modern, or rely on lazy imports as a steady-state throughput optimization.

Report this as an application/stack comparison, not a pure interpreter speedup. Dependencies may differ between applications, but must remain locked within the modern runtime comparisons.

### C. Modern runtime comparison

Use the same modern source in the following separately labeled configurations:

| Mode | Deployment | Purpose |
| --- | --- | --- |
| Standard GIL build | Two processes, async request/DB paths | Conventional production reference |
| JIT build, JIT off | Two processes, same async paths | Matched-build JIT control |
| Same JIT build, JIT on | Two processes, same async paths | Isolate runtime JIT toggle |
| Free-threaded build, GIL on/off | Two processes, same async paths | Measure build overhead and toggle effects with fixed topology |
| Free-threaded build, GIL on/off | One process, bounded threaded request adapter | Evaluate actual Python thread parallelism with fixed topology |

The threaded adapter is explicit: a synchronous FastAPI request/dependency path executes application work in the bounded worker pool and uses synchronous DB sessions. It must retain the same SQL, contract, authorization, and transaction boundaries. Do not share sessions across threads. Set a thread limit of 16 initially, identically for its GIL-on/off controls; changes are separately recorded tuning experiments. Keep total database connections fixed across process/thread modes. The async versus threaded comparison changes execution strategy and must be labeled accordingly, not attributed entirely to disabling the GIL.

Do not promise simultaneous JIT and free threading. Verify the exact pinned CPython source and dependency support first; include a combined cell only if it demonstrably works, otherwise report it unsupported. Check free-threaded build capability and actual GIL state inside every worker after all application imports. Check JIT availability/enabled state and perform a separate untimed hot-code probe where supported; enabled alone does not prove API handlers benefit. Never poll JIT activity in timed handlers. Fail preflight on mislabeled runtime modes. Do not force the GIL off around unverified native extensions.

Python 3.15 is documented as a release candidate at specification time. Record the exact prerelease label if used; a later stable-release campaign is a new manifest and must not be pooled with prerelease results.

## 4. Application contract

All application endpoints are under `/api/v1`, except operational health endpoints.

| Operation | Endpoint | Behavior |
| --- | --- | --- |
| Register | POST `/auth/register` | Unique normalized username, validated password, return user; duplicate is 409 |
| Login | POST `/auth/login` | Verify password and issue expiring bearer token; invalid credentials use a uniform 401 |
| Current user | GET `/users/me` | Authenticated user identity |
| Follow/unfollow | PUT/DELETE `/users/{user_id}/follow` | Idempotent relationship mutation; disallow self-follow, missing target is 404 |
| Create post | POST `/posts` | Authenticated author, nonempty text up to 280 Unicode code points |
| Post detail | GET `/posts/{post_id}` | Content, author, counts, caller's liked state; missing post is 404 |
| Following feed | GET `/feed` | Newest posts from followed accounts plus the caller's own posts |
| Like/unlike | PUT/DELETE `/posts/{post_id}/like` | Idempotent mutation, unique per user/post |
| Create comment | POST `/posts/{post_id}/comments` | Authenticated author, nonempty text up to 1,000 code points |
| List comments | GET `/posts/{post_id}/comments` | Cursor-paginated comments |

Require authentication for social operations, including feed, detail, and comments. Registration/login are public. Standard validation errors are 422; invalid cursors return a documented 422; unauthorized requests are 401. Bound request sizes and pagination, with default 20 and maximum 100 items. Use stable descending `(created_at, id)` cursor order for feed/comments. Cursors are opaque encoded values, not SQL or client-controlled query fragments. Concurrent new posts must not duplicate already-paged entries. All timestamps use UTC and all SQL is parameterized.

Use a minimal router/service/data-access structure, application lifespan for pools, typed settings, explicit response schemas, and migrations. SQLAlchemy 2.x and Psycopg 3 are proposed starting points, subject to cross-version/free-threaded compatibility verification. Prefer ordinary indexed SQL and batch hydration over N+1 queries. Keep blocking password hashing off event-loop threads. Use Argon2id with one fixed, documented policy for both applications, unique random salts, and no weakened benchmark-only verification. Expiring signed tokens use one verified signing policy; signing secrets are external configuration and do not appear in reports. Unknown-user login performs comparable hash work. Tests cover validation, ownership, expired tokens, and uniform auth failure behavior.

Use database uniqueness and transactions for follows/likes, including concurrency races. Start with counters read from indexed relationship tables in bounded batched queries for returned page IDs; no full-dataset aggregation per request. Add persisted counters only if a documented query-plan problem warrants them, and then change both implementations identically with concurrency tests. No response caching or artificial CPU work.

## 5. Schema and data

Tables: `users`, `posts`, `follows`, `likes`, and `comments`. Use foreign keys, unique username and composite relationship keys, UTC timestamps, and deterministic numeric seed IDs. Important indexes include posts `(author_id, created_at DESC, id DESC)`, follows `(follower_id, followed_id)`, likes keyed by `(post_id, user_id)` with user-oriented access as required, and comments `(post_id, created_at DESC, id DESC)`.

The initial full-size fixture contains:

- 50,000 users.
- 500,000 posts.
- 2,500,000 following edges: exactly 50 distinct other accounts followed by each user, with skewed popularity.
- 1,000,000 unique likes.
- 250,000 comments.

The extra relationship counts are design defaults, configurable for separate campaigns. Use a fixed, recorded PRNG seed, fixed UTC time origin, varied text lengths, skewed posting/popularity, and bounded collision handling that guarantees exact counts. Seed popularity should include heavily followed accounts and rarely active users. Use actual password hashes with unique salts; capture the resulting seeded database once for exact subsequent restoration rather than expecting password hashing to regenerate identical bytes. Credentials are test-only and kept out of committed files/results.

Bulk-load with PostgreSQL COPY where appropriate, repair sequences, validate row counts/referential integrity and follower uniqueness, then refresh statistics. Save a versioned logical PostgreSQL dump and a fixture manifest containing checksum, schema revision, seed parameters, counts, and generator version. Produce a small fixture for tests and smoke runs; never mix its results with full-size measurements.

## 6. Infrastructure and resource controls

Recommended initial AWS topology, contingent on regional availability:

| Role | Initial choice | Control |
| --- | --- | --- |
| Backend | `c7i.large`: 2 vCPUs, 4 GiB | One application configuration active at a time |
| k6 generator | `c7i.4xlarge`: 16 vCPUs, 32 GiB | Eight times backend vCPUs; capacity must still be validated |
| Database | Non-burstable RDS PostgreSQL, initially `db.m7i.xlarge` | Fixed engine patch, class, gp3 storage/IOPS/throughput and parameter group |

The database class is a starting recommendation, not a guarantee of sufficient capacity. Verify availability and exact hardware before provisioning. Place all three roles in the same region, VPC and availability zone using private addresses. Keep backend and RDS private; permit backend access only from the load-generator/admin paths, and RDS access only from the backend and dedicated administration path. Use verified TLS for RDS connections. Primary timing uses HTTP over the private benchmark path; public HTTPS performance, if tested, is a distinct campaign.

Local Compose includes PostgreSQL and both selectable application builds. AWS backend Compose must not launch a local database or k6. Load-generator Compose is independent. Allocate the backend application an aggregate two-vCPU quota and 3 GiB memory budget with no swap; retain host headroom and report cgroup throttling. Initial aggregate application DB pool limit is 20 connections, with no overflow: 10 per process for two workers, 20 for one process. RDS administration/telemetry connections are separate and recorded. Fix timeouts, server loop/HTTP parser choices, access-log policy, and telemetry settings across comparisons. Do not silently substitute uvloop or other native accelerators in just some cells.

Generator instances are substantially larger by design, but sizing alone cannot prove sufficient load capacity. Record per-core CPU, memory, network, sockets and k6 dropped iterations. Calibrate with an isolated fast target before the campaign and validate during each run. Flag sustained generator CPU above 60%, swapping, network saturation, or scheduling failures for investigation. Increase size or shard generators if necessary, then rerun the whole affected comparison at the fixed new generator configuration. Dropped iterations can also reflect backend slowdown and insufficient VUs, so never classify them as generator hardware failure without supporting evidence. Provision VUs before timing; avoid dynamic VU initialization during measurement.

Avoid EC2 and RDS burstable classes. Keep telemetry lightweight and identical across cells; separate heavier profiling from measured trials. RDS backup/maintenance activity and failovers must be detected and recorded. Keep the same backend VM during a comparison block; if it is replaced, record a new block with CPU identity.

## 7. Workloads and load model

Provide independent feed-read, comment-read, post-create, like/unlike, comment-create, and login scenarios, plus a mixed scenario. Registration and follow mutations are correctness-tested and available as optional scenarios; do not change the follower graph in the primary mixed workload.

Default mixed operation probabilities: 60% feed, 15% comment reads, 10% likes/unlikes, 5% post creation, 5% comment creation, and 5% login. Each arrival represents one selected operation; report both offered operations/s and actual HTTP requests/s, since login retries or scenario setup would otherwise alter their relationship. Failed checks never disappear from throughput/error reporting.

Use k6 arrival-rate executors for capacity measurements. Provide a separately labeled closed-model user journey with think time to describe user experience. A user journey does not define maximum server capacity. Use deterministic choices keyed by workload seed and iteration/shard identifiers, with distinct actor partitions if distributed generators are needed.

Prepare valid tokens by real login outside timing for authenticated-operation scenarios. Token expiry must exceed setup, warm-up, and measurement; never bypass token verification inside the application. The login scenario continues to perform real password verification in timed requests. Reuse HTTP connections consistently. Select posts from a seeded manifest or fetched feed, include hot/cold targets and non-first feed pages, and avoid repeated idempotent writes that turn a supposed mutation workload into mostly no-ops. Deliberate like contention is a separate labeled scenario. Validate response structure/status and sampled semantic correctness.

Start a pilot at 25 operations/s and geometrically increase load to find the useful range; set finite configurable ceilings for rate, VUs, and time. Pilot results choose a common rate grid for all comparable cells. Run each rate as its own restored trial. Primary mixed-workload capacity criterion is highest offered rate with at least 99.9% successful operations, p95 <= 200 ms, p99 <= 500 ms, and zero dropped iterations. These are experiment targets, not universal production requirements. Login has a separate latency target, default p95 <= 500 ms; report per-endpoint values even when aggregate mixed latency passes. Retain overload results and latency/throughput curves.

## 8. Trial lifecycle and reset

1. Run correctness/build/runtime preflight, capture environment metadata, and confirm fixture checksum.
2. Stop the previous backend and all generators. Verify no remaining application DB sessions; enforce a single-campaign lock.
3. Restore the canonical logical dump to a dedicated benchmark database, repair/verify sequences and counts, and run ANALYZE. Reset requires an explicit benchmark-database identity marker and configured database allowlist; it must refuse arbitrary production databases.
4. Start exactly the selected application topology. Wait for every worker's readiness and verify actual runtime mode after imports. Capture pool/thread settings.
5. Obtain tokens and warm the selected application for 120 seconds using read/auth operations only, preserving the logical fixture. Preflight checks can exercise mutations before restoration, not in the measured fixture. Warm-up metrics are excluded.
6. Measure for 300 seconds at the chosen fixed rate. Capture telemetry and failures. Abort visibly on crashes; keep partial artifacts.
7. Stop load, drain in-flight requests, collect all artifacts and validate the trial. Stop the application, reset the database to the canonical fixture, and verify the reset. A failed cleanup is explicit and blocks the next trial.

Repeat every selected cell/rate at least five times with randomized blocked order and a recorded schedule seed. Reset occurs before and after every measured trial, including between rates, not just between Python versions. Preserve traceability to the fixture and workload seeds. Scheduled trials never run competing backends on the two-vCPU machine.

Restoration resets logical data, not RDS OS/storage caches. The primary campaign is an explicitly warmed comparison with a fixed warm-up recipe; do not claim cold-cache equality or flush shared RDS caches. Check cache/bottleneck indicators and use randomized repeated trials to reduce order effects. Cold-start performance, if needed later, is a separate experiment.

## 9. Observability, results, and interpretation

Every trial records git revision, dirty state, application variant, exact Python release, source/build flags/compiler, image digest, dependency inventory, native-wheel identity, JIT/GIL status per worker, CPU topology, host/region/AZ, limits, pool/thread settings, PostgreSQL engine/settings, fixture checksum, workload seed, rate, VU budget, and timestamps. Redact credentials and connection secrets.

Collect achieved requests and operations/s, successful throughput, status/error/timeout rates, dropped iterations, per-endpoint latency distributions, CPU/memory, cgroup throttling, generator utilization, pool wait, and RDS CPU/connections/query/wait/cache/storage indicators. Distinguish application, database, client-network, and generator limitations from evidence; do not infer the cause solely from throughput.

Write one output directory per trial, with machine-readable manifest, k6 summary and histogram/raw artifacts sufficient for distributions, telemetry, logs, validity assessment, and final state. Retain unsupported cells and failed trials. Reports show all repetitions, medians and ranges, and paired/block comparisons where applicable. Do not average percentiles into a pooled percentile; pooled percentiles require mergeable distributions. Bootstrap intervals may be provided with the method and small-sample limitations recorded.

Changes to code, dependencies, build flags, fixture, hardware, or workload create a new campaign identity. Publish speedup ratios only for appropriately controlled families. A two-process async versus one-process threaded result is an architecture comparison. A DB-saturated trial cannot establish interpreter scalability beyond the measured system. Never fabricate AWS results from local tests.

## 10. Verification and completion criteria

- Both applications pass the same contract tests for auth, following-feed visibility, pagination, writes, empty/missing cases, and input validation.
- Concurrent follow/like operations preserve unique relationships; synchronous/async adapters preserve the same semantics and transaction behavior.
- Baseline builds and meaningful smoke tests execute on every supported Python version; failures are diagnosed rather than ignored.
- Modern runtime preflight detects unsupported JIT/free-threading combinations and GIL re-enablement after imports.
- A small deterministic fixture round-trips through reset with exact logical data/counts; destructive reset guards and interrupted-trial cleanup are tested.
- Compose configuration validates; local integration tests exercise PostgreSQL and both applications; k6 smoke tests validate its workloads and reported failures.
- Full-size seed/reset and at least one end-to-end local trial execute when host capacity allows, with actual evidence recorded. Local results are not AWS benchmark results.
- AWS runbook provides exact steps to reproduce the campaign and collect results. Provisioning and measured AWS execution remain separate actions requiring an available AWS account/environment.

## 11. Source references and uncertainty

Checked during design; recheck exact versions and availability during implementation:

- Python 3.15 changes and release labeling: https://docs.python.org/3.15/whatsnew/3.15.html
- Runtime build flags: https://docs.python.org/3.15/using/configure.html
- Free-threading checks and native-extension behavior: https://docs.python.org/3.15/howto/free-threading-python.html
- JIT/free-threading integration tracking: https://github.com/python/cpython/issues/133171
- FastAPI concurrency behavior: https://fastapi.tiangolo.com/async/
- k6 arrival-rate pacing: https://grafana.com/docs/k6/latest/using-k6/scenarios/executors/constant-arrival-rate/
- k6 VU allocation and dropped iterations: https://grafana.com/docs/k6/latest/using-k6/scenarios/concepts/arrival-rate-vu-allocation/
- EC2 size specifications: https://aws.amazon.com/ec2/instance-types/compute-optimized/
- EC2 CPU credits: https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/burstable-credits-baseline-concepts.html
- RDS class specifications: https://docs.aws.amazon.com/AmazonRDS/latest/UserGuide/Concepts.DBInstanceClass.Summary.html

Exact dependency pins, release patches, and source-build recipes are implementation outputs chosen through compatibility checks, not unsupported promises in this spec. Runtime and library support can constrain the matrix; every such constraint must be explicit in the harness and report.
