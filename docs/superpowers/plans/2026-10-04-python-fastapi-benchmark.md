# Python FastAPI Benchmark Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build two realistic FastAPI backends and a reproducible Docker/k6 experiment that separates interpreter, application, and deployment effects.

**Architecture:** Separate baseline and modern application packages implement a shared HTTP contract and PostgreSQL schema. Shared fixture tooling and a manifest-driven runner control resets, runtime verification, workloads, and result collection. Compose runs the backend and generator on separate hosts in AWS; local Compose adds PostgreSQL for verification.

**Tech Stack:** CPython 3.10–3.15, FastAPI, Pydantic, SQLAlchemy 2.x, Psycopg 3, Alembic, Argon2id, signed bearer tokens, PostgreSQL, Docker Compose, k6, pytest. Dependency and runtime pins are selected by tested compatibility, not assumed here.

**Spec:** `docs/superpowers/specs/2026-10-04-python-fastapi-benchmark-design.md` (approved by the user on 2026-10-04).

## Global Constraints

- Baseline: identical source and dependency versions on Python 3.10, 3.11, 3.12, 3.13, 3.14, and 3.15. Modern: Python 3.15, separate locked dependencies; unsupported cells stay visible.
- Docker and Docker Compose; backend EC2 VM with two vCPUs; RDS PostgreSQL; independent k6 EC2 VM, initially `c7i.4xlarge` with 16 vCPUs and 32 GiB.
- Seed exactly 50,000 users and 500,000 posts; defaults: 2,500,000 follows, 1,000,000 likes, 250,000 comments; 50 distinct non-self follows per user.
- Following feed includes followed accounts plus the caller's own posts, descending `(created_at, id)`; pagination default 20, maximum 100.
- Post text: nonempty, maximum 280 Unicode code points. Comment text: nonempty, maximum 1,000 code points. All timestamps use UTC and all SQL is parameterized.
- Two processes for the production reference; one process for threaded diagnostics; thread limit 16; aggregate application DB pool limit 20, no overflow.
- Aggregate application budget: two vCPUs, 3 GiB memory, no swap. RDS connections use verified TLS. No response cache or artificial CPU work.
- Restore canonical data before and after every measured trial; warm-up 120 seconds, measurement 300 seconds, at least five repetitions with randomized blocked order.
- Mixed operations: 60% feed, 15% comment reads, 10% likes/unlikes, 5% post creation, 5% comment creation, 5% login. Start pilot at 25 operations/s.
- Mixed capacity targets: at least 99.9% successful operations, p95 <= 200 ms, p99 <= 500 ms, zero dropped iterations. Login p95 target: 500 ms.
- No AWS provisioning in this implementation. No fabricated AWS measurements; preserve failed trials, unsupported configurations, and local/AWS labels.
- Prefer Bun for JavaScript tooling if needed; k6 scripts run directly in k6, without an unnecessary Node package/toolchain.

## Review Focus

- Partial reset, process interruption, or stale campaign lock: preserve failure artifacts, clean up safely, and block the next trial until the fixture is verified (Tasks 7, 10).
- Native extensions re-enable the GIL or only some workers register readiness: preflight must reject mislabeled or partially ready runtime cells (Tasks 1, 6).
- Malformed cursors, equal timestamps, and concurrent inserts: reject invalid cursors and paginate without duplicates or unauthorized feed entries (Tasks 2, 4).
- Tokens expire during setup or leaked connection strings enter manifests: reject inadequate token lifetime and redact secrets from artifacts (Tasks 3, 8, 10).
- VU exhaustion, failed HTTP operations, or absent telemetry creates apparently good results: preserve attempted load and failures; flag unknown validity rather than inventing capacity (Tasks 8–11).

## File map and interfaces

- `backends/python/baseline/pyproject.toml`, `requirements.lock`, `src/baseline_api/{main,settings,security,schemas,db,queries,auth,social,runtime}.py`: independent Python 3.10-compatible application.
- `backends/python/modern/pyproject.toml`, `requirements.lock`, `src/modern_api/{main,settings,security,schemas,db,queries,auth,social,runtime,threaded}.py`: independent modern implementation and explicit synchronous threaded adapter.
- `contracts/api-v1.yaml`: reviewed OpenAPI contract, including response bodies and error codes; `tests/contract/` verifies each adapter against it.
- `database/alembic.ini`, `database/env.py`, `database/versions/0001_social_schema.py`: single schema/migration source; both applications map to it.
- `tools/bench/{models,compatibility,runtime,seed,fixture,reset,load_data,manifest,schedule,transport,telemetry,runner,report,cli}.py`: a small Python 3.10-compatible tools package, runnable as `python -m bench.cli` after installation. Keep modules limited to responsibilities needed by this experiment.
- `docker/{python.Dockerfile,app.Dockerfile,tools.Dockerfile}`, `backends/python/entrypoint.py`, `runtimes/python/lock.json`, `docker/k6-lock.env`: source/runtime builds, worker launch, immutable inputs.
- `compose.yaml`, `compose.aws.yaml`, `compose.load.yaml`, `.env.example`: local backend/DB, external-RDS backend, separate generator. Profiles select one active local backend.
- `benchmarks/{campaign.example.json,smoke.json}`, `benchmarks/k6/{workload,warmup,journey,calibrate}.js`, `benchmarks/k6/lib/{selection,metrics,requests}.js`: campaign parameters and k6 workloads.
- `tests/{unit,integration,contract,smoke}/`: isolated meaningful tests; `tests/conftest.py` provides explicit fixture DSNs and temporary artifact directories.
- `README.md`, `docs/{aws-runbook,methodology,runtime-support}.md`, `.github/workflows/verify.yml`: usage, limitations, and repeatable verification.
- `.gitignore`: ignore secrets, fixtures, credentials, build outputs, and trial outputs. Commit small sanitized example artifacts only when useful.

Shared tool models live in `tools/bench/models.py`: `RuntimeCell` (id, family, app, Python release, build mode, JIT/GIL expectations, adapter, workers, threads, pool budget), `FixtureSpec` (seed, time origin, counts), `FixtureManifest` (schema revision, checksum, counts, generator version), `Campaign` (cells, rates, repeats, durations, fixture identity, workload, host aliases, ceilings), `TrialSpec` (id, campaign id, cell id, rate, repetition, workload/schedule seeds), and `TrialResult` (status, reasons, artifact paths, reset verified). Validate the exact fields and serializable representation as they are introduced, without speculative plugin APIs.

Every task uses the same cycle: add its named failing tests; run the listed command and confirm the intended failure; implement the named interfaces and behaviors; rerun and confirm success; commit only that task's files. Commands below assume a tools/test virtual environment is activated. Container commands become available in Task 9. A failure because a dependency or daemon is unavailable is an environment limitation, not a successful behavior test.

## Task 1: Resolve compatibility and capture immutable build inputs

**Files:** Create both application dependency files, `tools/bench/{__init__,compatibility,models}.py`, tools/test dependency files, `runtimes/python/lock.json`, `docs/runtime-support.md`, `.gitignore`; test `tests/unit/test_compatibility.py`.

**Interfaces:** Produce `probe_cell(cell: RuntimeCell) -> dict` with `supported`, `reasons`, release/build identity and import inventory. Produce `validate_runtime_lock(data: dict) -> dict`; introduce `RuntimeCell`. Application modules are not imported until they exist; rerun the complete probe in Task 6.

- [ ] Add `test_runtime_lock_requires_exact_release_digest_and_checksum`, `test_unsupported_cell_retains_reason`, and `test_baseline_dependencies_do_not_vary_by_python`; assert empty checksums/digests and floating release tags fail validation, and all baseline cells select one lockfile checksum.
- [ ] Run `python -m pytest tests/unit/test_compatibility.py -q`; confirm missing interfaces fail.
- [ ] Inspect official package release metadata/docs and CPython sources; select exact patch/prerelease releases and a common baseline dependency set. Verify imports/ABI support in disposable environments. Pin the complete transitive dependency graphs, OS digest, source SHA256, compiler/bootstrap/LLVM versions and build modes. Preserve evidence and unsupported-cell reasons. Do not silently loosen pins for free-threaded builds; prefer documented compatible drivers/extensions, otherwise flag support gaps.
- [ ] Implement the two interfaces and model; rerun the unit tests and record actual compatibility probe commands/results in `docs/runtime-support.md`. CPython source/build inputs for measured images must be matched, with PGO/LTO policy explicit.
- [ ] Commit: `build: pin Python runtime and dependency experiment inputs`.

## Task 2: Define the HTTP contract, schema, and cursor rules

**Files:** Create `contracts/api-v1.yaml`, database migration files, baseline `schemas.py`, `queries.py`; test `tests/unit/test_cursor.py`, `tests/integration/test_schema.py`.

**Interfaces:** Produce `encode_cursor(created_at: datetime, item_id: int) -> str`, `decode_cursor(value: str) -> tuple[datetime, int]`, and SQLAlchemy mappings in baseline `db.py`. Cursor errors become documented 422 responses in routing. Database migrations, rather than either application, own schema creation.

- [ ] Add `test_cursor_roundtrip_preserves_utc_and_id`, `test_cursor_rejects_invalid_payload`, and schema tests for unique normalized usernames, unique follows/likes, foreign keys, non-self follows, and required indexes. Assertions include UTC round-trip equality and constraint rejection of duplicate pairs.
- [ ] Run `python -m pytest tests/unit/test_cursor.py tests/integration/test_schema.py -q`; use a disposable PostgreSQL test database and confirm the intended failures.
- [ ] Write concrete request/response schemas for every endpoint in spec section 4, including token expiry, author objects, counts, liked state, `items`/`next_cursor`, and mutation status codes. Fix username normalization to lowercase ASCII usernames of 3–32 letters/digits/underscores; passwords 12–128 Unicode code points, bounded request body 16 KiB. Treat these as explicit implementation choices applied identically to both apps.
- [ ] Implement the initial migration, mappings, and strict cursor validation (UTC timestamp, positive signed-64-bit ID, bounded encoded size); reuse parameterized query definitions within each app for async/sync adapters. Rerun both test files and inspect the migration against PostgreSQL.
- [ ] Commit: `feat: define social API contract and PostgreSQL schema`.

## Task 3: Implement baseline lifecycle and authentication

**Files:** Create baseline `main.py`, `settings.py`, `security.py`, `auth.py`; extend `db.py`, `schemas.py`; test `tests/contract/test_auth.py`, `tests/unit/test_security.py`.

**Interfaces:** Produce `create_app() -> FastAPI`, typed settings, `hash_password(password: str) -> str`, `verify_password(password: str, encoded: str) -> bool`, `issue_token(user_id: int, now: datetime) -> str`, `decode_token(token: str, now: datetime) -> int`; application process owns its pool/session factory through lifespan. Shared test fixture `api_client` parametrizes application/adapter, initially baseline async.

- [ ] Add `test_register_login_and_me`, `test_duplicate_normalized_username_is_409`, `test_unknown_and_wrong_password_are_uniform_401`, `test_expired_or_tampered_token_is_401`, `test_short_password_is_422`, and `test_security_settings_reject_missing_secret`. Assert independent salts and verify returned plaintext passwords never appear.
- [ ] Run `python -m pytest tests/unit/test_security.py tests/contract/test_auth.py -q`; confirm expected failures.
- [ ] Implement Argon2id policy: memory 65,536 KiB, time cost 3, parallelism 1, 32-byte hash and 16-byte salt; one policy in both apps. Limit concurrent password hashes to two per backend process, offload from async event loop, and record this setting in manifests. Use verified JWT library with explicit HS256 allowlist, issuer/audience validation, `sub`, `iat`, `exp`, external secret of at least 32 bytes, and a configured TTL. Reject unsafe RDS TLS settings in AWS mode. Configure no-overflow per-process pool according to worker count and a dummy hash for unknown usernames.
- [ ] Implement health/lifespan, `/auth/register`, `/auth/login`, `/users/me`; rerun the tests, including async responsiveness while hashing and pool teardown after app shutdown.
- [ ] Commit: `feat: add baseline application lifecycle and secure authentication`.

## Task 4: Implement social operations and following-feed queries

**Files:** Create baseline `social.py`; extend `queries.py`, `db.py`, `schemas.py`; test `tests/contract/test_social.py`, `tests/integration/test_concurrency.py`, `tests/integration/test_query_plans.py`.

**Interfaces:** Authenticated handlers consume Task 3 user identity/session dependencies. Produce post, relationship, comment and feed operations matching `contracts/api-v1.yaml`; `fetch_feed(session, user_id: int, cursor: tuple[datetime, int] | None, limit: int)` returns page rows for response serialization. SQL query definitions are reused by the modern implementation, by copying reviewed query shapes rather than importing baseline application services.

- [ ] Add `test_feed_contains_followed_and_own_posts_only`, `test_equal_timestamps_and_new_insert_do_not_duplicate_pages`, `test_limits_and_bad_cursor_are_422`, `test_post_and_comment_unicode_limits`, `test_missing_targets_are_404`, `test_self_follow_is_rejected`, `test_like_and_follow_are_idempotent`, and concurrent mutation assertions that each pair has at most one row.
- [ ] Run `python -m pytest tests/contract/test_social.py tests/integration/test_concurrency.py -q`; confirm failures against baseline.
- [ ] Implement parameterized indexed SQL, deterministic keyset pagination, batched author/count/liked hydration limited to page IDs, and explicit transactions with conflict-safe relationship inserts/deletes. Include counts and caller's liked state in detail/feed. Enforce body and page bounds consistently; no shared mutable session and no per-row SQL.
- [ ] Rerun contract/concurrency tests. Add query-count tests comparing 1-item and 20-item pages: counts stay bounded and do not scale per row. Inspect EXPLAIN on representative seeded data; assert required indexes exist rather than brittle exact planner output. Record findings.
- [ ] Commit: `feat: implement following feed and transactional social endpoints`.

## Task 5: Build the independent modern application and threaded adapter

**Files:** Create all modern application modules; extend `tests/conftest.py`; create `tests/integration/test_adapters.py`.

**Interfaces:** Modern produces `create_app() -> FastAPI`, the same security/cursor signatures and contract, plus `create_threaded_app() -> FastAPI`. Modern async and threaded routes use the same modern query definitions; separate synchronous/async session factories own connection lifetimes. Pool budget and thread capacity are explicit settings.

- [ ] Parametrize all existing contract tests over baseline async, modern async, modern threaded; add `test_adapters_return_equivalent_seeded_pages`, `test_threaded_sessions_are_not_shared`, and `test_thread_limit_is_16`. Assert equivalent IDs/order/counts/authorization, allowing generated token and hash bytes to differ.
- [ ] Run `python -m pytest tests/contract tests/integration/test_adapters.py -q`; confirm modern adapters are the failing cases.
- [ ] Implement independent modern code using verified current FastAPI/Pydantic features, modern annotations and context management where useful. Preserve baseline SQL/contract/security policies. Implement synchronous request dependencies and handlers for the threaded variant; never bridge with `asyncio.run` per request or share sessions across threads. Bound AnyIO worker capacity at 16 and password hashing concurrency consistently. Document concrete differences instead of claiming all new syntax improves throughput.
- [ ] Rerun all adapter/contract/concurrency tests under supported interpreters; verify threaded handler work actually occurs in worker threads. Record native-extension support for both adapter paths.
- [ ] Commit: `feat: add modern FastAPI backend with controlled threaded adapter`.

## Task 6: Verify runtime mode and every worker's readiness

**Files:** Create both app `runtime.py` files, `tools/bench/runtime.py`, `backends/python/entrypoint.py`; test `tests/unit/test_runtime.py`, `tests/integration/test_worker_readiness.py`.

**Interfaces:** Produce app `runtime_snapshot() -> dict`; tool `validate_worker_snapshots(cell: RuntimeCell, snapshots: list[dict]) -> list[str]`. Workers write unique PID-specific snapshots/readiness records to a mounted run directory, atomically, after lifespan/import completion; the launcher clears stale records before startup. Runner reads all expected worker records rather than relying on repeatedly requesting one health URL.

- [ ] Add `test_reenabled_gil_rejects_free_threaded_cell`, `test_jit_enabled_requires_available_build`, `test_missing_duplicate_or_stale_worker_rejects_readiness`, and `test_standard_old_python_without_private_probes_is_valid`. Assert two distinct current worker records are required for two-worker cells and mismatches produce reasons.
- [ ] Run `python -m pytest tests/unit/test_runtime.py tests/integration/test_worker_readiness.py -q`; confirm failures.
- [ ] Implement build/GIL/JIT introspection with capability checks for older interpreters; capture dependency imports before state checks. Do not override native-extension warnings with `PYTHON_GIL=0`; use free-threaded default-off GIL behavior for verified libraries and explicit `PYTHON_GIL=1` for controls. Reject unsupported combined modes from actual build/probe evidence. Perform hot-code JIT activity probe separately before timing, if supported, and record whether execution was observed without claiming handlers compiled.
- [ ] Rerun tests and Task 1 compatibility probes with actual app imports. Verify readiness timeout, worker crash and restart behavior; preserve all raw diagnostics.
- [ ] Commit: `feat: validate runtime modes and complete worker readiness`.

## Task 7: Seed, snapshot, and safely restore the canonical dataset

**Files:** Create `tools/bench/{seed,fixture,reset}.py`; extend `models.py`; test `tests/unit/test_seed.py`, `tests/integration/test_fixture_reset.py`, `tests/unit/test_reset_guards.py`.

**Interfaces:** Produce `generate_fixture(spec: FixtureSpec, output_dir: Path) -> dict[str, Path]`, `seed_database(dsn: str, spec: FixtureSpec) -> dict[str, int]`, `capture_fixture(dsn: str, output_dir: Path) -> FixtureManifest`, `restore_fixture(dsn: str, manifest_path: Path, allowed_database: str) -> FixtureManifest`, and `verify_fixture(dsn: str, manifest: FixtureManifest) -> list[str]`. Secrets are supplied through environment/config files and redacted from logs.

- [ ] Add `test_seed_exact_counts_and_no_self_or_duplicate_follows`, `test_seed_rejects_impossible_relationship_counts`, `test_seed_is_logically_deterministic_except_salted_hashes`, `test_reset_roundtrip_restores_changed_data_and_sequences`, `test_reset_refuses_unmarked_or_wrong_database`, and `test_failed_restore_does_not_report_verified`. Use small fixture: 100 users, 1,000 posts, 10 follows/user, 2,000 likes, 500 comments.
- [ ] Run `python -m pytest tests/unit/test_seed.py tests/unit/test_reset_guards.py tests/integration/test_fixture_reset.py -q`; confirm failures.
- [ ] Implement deterministic weighted sampling with bounded collision fallback and exact counts, unique salts, COPY loading, migrations, sequence repair, counts/integrity checks, and ANALYZE. Capture a custom-format logical dump with PostgreSQL client version compatible with the server, checksum it and save the fixture manifest. Keep fixture credentials and dump files untracked.
- [ ] Implement identity-marker and allowlist checks before destructive work, explicit benchmark-only DB connection/application names, session checks, checked subprocess exit codes and rollback/recovery state. Marker initialization is a separate explicit command; never auto-mark an arbitrary supplied DSN. Verify the dump checksum before restore and exact logical fixture contents afterward using canonical table data digests, not counts alone; retain fingerprint metadata in the manifest.
- [ ] Rerun tests, including restore interruption and content mutation with unchanged counts. Build/capture full-size fixture when resources permit and record elapsed time, exact counts and verified checksum.
- [ ] Commit: `feat: add deterministic fixture generation and guarded database restoration`.

## Task 8: Implement k6 workloads and honest operation accounting

**Files:** Create k6 scripts/libs, `tools/bench/load_data.py`, `benchmarks/{campaign.example,smoke}.json`; test `tests/unit/test_load_data.py`, `tests/smoke/test_k6_workloads.py`.

**Interfaces:** Produce `prepare_load_data(base_url: str, credentials_path: Path, output_path: Path, required_valid_seconds: int) -> dict`; k6 consumes a sanitized scenario config plus private actor tokens/credentials and seed post IDs/cursors. Output metrics include attempted/completed/successful operations, per-operation duration, HTTP/check failures, dropped iterations, and expected versus actual like mutations.

- [ ] Add `test_prepare_data_rejects_short_token_lifetime`, `test_workload_mix_is_60_15_10_5_5_5`, `test_failed_requests_count_as_failed_operations`, `test_warmup_does_not_mutate_fixture`, `test_nonfirst_page_is_exercised`, and `test_shards_use_distinct_actor_partitions`. Smoke tests run each scenario at small rate and check emitted metrics and DB changes.
- [ ] Run `python -m pytest tests/unit/test_load_data.py tests/smoke/test_k6_workloads.py -q`; confirm failures.
- [ ] Implement real login preparation outside timing, deterministic iteration/shard selection, preallocated VUs (`maxVUs == preAllocatedVUs`), private-path HTTP keepalive, status/schema checks, and explicit operation timing distinct from HTTP requests. Use an initially unliked actor/post pairing manifest and disjoint deterministic mutation assignments; alternate verified unlike operations with prior successful likes, accounting for state and avoiding silent no-op mutation inflation. Preserve hot/cold target distribution and reserve a separate contention scenario.
- [ ] Implement read/auth-only warm-up, single-operation arrival-rate scenarios, mixed arrival-rate scenario, journey with configurable think time, and a calibration workload targeting a separate fast service. Persist summary plus compressed raw k6 metric stream needed to inspect distributions. Reject malformed scenario configuration and exhausted mutation-pair budgets before running.
- [ ] Rerun tests against all adapters; deliberately provoke errors/VU exhaustion and confirm failures/dropped iterations remain visible. Keep checks lightweight with deeper semantic verification in samples outside timing.
- [ ] Commit: `feat: add controlled k6 workloads and load-generator calibration`.

## Task 9: Supply matched Docker builds and separate Compose deployments

**Files:** Create Dockerfiles, `docker/k6-lock.env`, Compose files, `.env.example`; test `tests/smoke/test_compose.py`.

**Interfaces:** Runtime image builds consume Task 1 immutable lock inputs; app builds consume runtime image and app lockfile. Local app service names are `baseline` and `modern`; PostgreSQL is `db`; tooling is `tools`. AWS service is `backend`; load-host service is `k6`, with host telemetry service as needed. Service selection and mounted run/output paths are explicit runner configuration.

- [ ] Add `test_aws_compose_has_no_local_db_or_k6`, `test_load_compose_has_no_backend_or_db`, `test_backend_has_two_cpu_three_gib_no_swap_budget`, `test_pools_sum_to_20`, and `test_secrets_are_not_baked_into_images`. Validate rendered Compose settings for each topology.
- [ ] Run `python -m pytest tests/smoke/test_compose.py -q`; confirm failures before Compose files exist.
- [ ] Implement source-build CPython with matched optimization/compiler/OS policy, pinned JIT prerequisites, explicit standard/JIT/free-threaded build targets, nonroot application containers and app-specific hashed dependencies. Keep asyncio loop and h11 parser explicit in every cell. Compose passes verified RDS TLS config externally; Docker secrets/private env files never enter the build context. Include clients/tools for migration, dump/restore, and local testing.
- [ ] Run `docker compose config --quiet`, `docker compose -f compose.aws.yaml config --quiet`, `docker compose -f compose.load.yaml config --quiet` with documented dummy settings; rerun smoke tests. Build and run meaningful baseline contract smoke tests on every supported version and modern runtime mode; archive failures as unsupported only with diagnosed reasons. Validate separate vendor-image smoke path labels if supplied.
- [ ] Commit: `build: add reproducible runtime images and separate Compose deployments`.

## Task 10: Orchestrate trials with locking, telemetry, and failure recovery

**Files:** Create `tools/bench/{manifest,schedule,transport,telemetry,runner,cli}.py`; extend models; test `tests/unit/test_schedule.py`, `tests/unit/test_manifest.py`, `tests/unit/test_runner.py`, `tests/integration/test_runner_local.py`.

**Interfaces:** Produce `schedule_trials(campaign: Campaign) -> list[TrialSpec]`, `run_trial(campaign: Campaign, trial: TrialSpec, output_dir: Path) -> TrialResult`, `run_campaign(config_path: Path, output_dir: Path) -> list[TrialResult]`, `collect_manifest(campaign: Campaign, trial: TrialSpec) -> dict`, `collect_telemetry(config: dict, output_dir: Path) -> dict`. Host transport supports local subprocess and an existing SSH alias using argv-based commands with fixed operations; do not introduce a general remote job framework. Runner controls backend and load hosts, preserving exit codes, deadlines and cleanup evidence.

- [ ] Add `test_schedule_is_block_randomized_and_repeatable`, `test_each_cell_rate_has_five_repetitions`, `test_manifest_redacts_credentials`, `test_runner_resets_before_and_after_each_rate`, `test_interrupt_preserves_artifacts_and_runs_cleanup`, `test_failed_cleanup_blocks_next_trial`, `test_second_campaign_cannot_acquire_lock`, and `test_missing_telemetry_marks_validity_unknown`. Mock transports only for lifecycle tests; use real DB/k6 for local integration.
- [ ] Run `python -m pytest tests/unit/test_schedule.py tests/unit/test_manifest.py tests/unit/test_runner.py -q`; confirm intended failures.
- [ ] Implement manifest validation with finite pilot/rate/VU/deadline ceilings; default pilot begins at 25 operations/s with doubling capped at configured 3,200 operations/s, then uses one common rate grid. Default repeats/durations match global constraints; smoke overrides are prominently labeled. Acquire host/DB campaign locks without automatic unsafe stale-lock deletion. Capture exact runtime/image/dependency/system/DB identities and secret-redacted settings.
- [ ] Implement sequential trial lifecycle from spec section 8 with hard readiness/reset checks, untimed token preparation, separate warm-up, measured phase, graceful drain, final verified restoration and atomic result writes. Preserve partial results on timeout, crash or interrupt. Use checked transport calls and deadlines for every external process; host failure produces explicit recovery-required state.
- [ ] Implement lightweight host/cgroup/generator metrics and PostgreSQL stats/pool-wait telemetry; AWS collector retrieves RDS CloudWatch metrics/events with aligned timestamps and records retrieval delay/absence. Flag generator CPU >60%, swap, network/scheduling issues, RDS maintenance/failover, and backend throttling. Keep diagnostic profiling outside timing. Missing permissions/metrics must remain visible, not zeros.
- [ ] Rerun unit tests and `python -m pytest tests/integration/test_runner_local.py -q`; execute one small-fixture local trial end to end, inject a measurement failure, and verify the final database matches the snapshot. Supply CLI commands `compatibility`, `seed`, `capture`, `restore`, `prepare-load`, `pilot`, `run`, `report` and `recover`; document flags through argparse help.
- [ ] Commit: `feat: orchestrate reproducible trials and recoverable benchmark runs`.

## Task 11: Generate reports without hiding errors or mixing comparisons

**Files:** Create `tools/bench/report.py`; test `tests/unit/test_report.py`; create small sanitized test artifacts under `tests/fixtures/results/`.

**Interfaces:** Produce `summarize_campaign(results_dir: Path) -> dict`, `write_report(results_dir: Path, output_path: Path) -> None`. Reports consume only recorded artifacts, not the current live environment, and never output secrets.

- [ ] Add `test_capacity_requires_all_targets_and_zero_drops`, `test_errors_reduce_successful_throughput`, `test_missing_metrics_cannot_pass`, `test_incompatible_campaigns_are_not_pooled`, `test_unsupported_and_partial_trials_remain_visible`, and `test_percentiles_are_not_averaged_into_pooled_percentiles`. Synthetic results with p99 600 ms or one dropped iteration cannot qualify despite high throughput.
- [ ] Run `python -m pytest tests/unit/test_report.py -q`; confirm failures.
- [ ] Implement per-trial/per-endpoint distributions, offered/achieved/successful throughput, trial medians/ranges, target-qualified capacity, generator/DB/CPU context, and clearly labeled interpreter/application/topology comparisons. Plot static latency/throughput curves if plotting dependencies are available; a self-contained HTML report with tables and lightweight SVG charts is sufficient. Keep individual-trial percentiles unless raw distributions can be merged correctly. Do not add unsupported statistical confidence claims.
- [ ] Rerun report tests and generate a report from Task 10 real local trial; ensure local labeling and artifact links, and manually inspect that missing/failed/unsupported rows are readable.
- [ ] Commit: `feat: report controlled comparisons and benchmark validity evidence`.

## Task 12: Verify full integration and document the AWS campaign

**Files:** Create `README.md`, `docs/aws-runbook.md`, `docs/methodology.md`, `.github/workflows/verify.yml`; update `docs/runtime-support.md` with measured verification evidence.

**Interfaces:** Provide documented commands and environment templates that invoke preceding tasks unchanged. CI runs lint/type checks where configured, tools/unit tests, PostgreSQL contract tests, Compose validation, and a small-fixture k6 smoke; heavier matched-source runtime builds run as a separately bounded matrix job.

- [ ] Add `test_documented_smoke_configuration_completes` to `tests/smoke/test_documented_workflow.py`; assert actual manifest, k6 summary, valid fixture reset, local labels and generated report exist. Run it before completing the runbook, confirming missing documentation/config causes failure.
- [ ] Write the local quickstart and AWS runbook for c7i.large backend, c7i.4xlarge generator, non-burstable RDS, same-AZ private networking, SSH/SSM administration, security groups, verified DB TLS, fixed storage/engine/settings, exact secrets setup and cleanup. Explain sizing validation, when to increase/shard k6, token preparation, fixture restore guard initialization, and restart/recovery after host failures. Do not provision anything or embed keys. Document that extra instance capacity is a proposal until actual calibration runs.
- [ ] Run full tools/unit and PostgreSQL contract/integration suites, Compose validation, baseline-version and modern-mode smoke matrix, and documented small-fixture workflow. Run full-size seed/capture/reset and one full-size local end-to-end trial if resources allow; record exact commands, results and genuine environment limitations. Full-size local work must not be represented as completed if only small fixtures ran.
- [ ] Review implementation against every spec section, especially pool budgets, thread/hash limits, native imports/GIL state, operation semantics, failed-trial preservation and reset fingerprints. Follow the user-selected execution method's final review requirements; fix material findings and rerun affected verification.
- [ ] Commit: `docs: document and verify the Python FastAPI benchmark campaign`. Deliver both applications, harness/runbook, verification evidence, support matrix, and limitations; state that AWS measurements require running in the user's AWS environment.

## Execution order and review checkpoint

Tasks 1–6 establish compatibility and both application implementations; Task 7 supplies fixture integrity; Tasks 8–9 make workloads deployable; Tasks 10–12 complete orchestration, reporting and reproducibility. These are dependent parts of one experiment, so keep one sequential implementation plan rather than independently shipping incompatible subsystem plans.

Plan self-review: spec sections 1–3 map to Tasks 1, 5, 6, 9; section 4 to Tasks 2–5; section 5 to Task 7; section 6 to Tasks 9, 10, 12; section 7 to Task 8; section 8 to Tasks 7, 10; section 9 to Tasks 10, 11; section 10 to Task 12 and each task's verification steps. Source/version uncertainty is handled by Task 1 preflight, not speculative pins. All five Review Focus conditions have named tests in their owning tasks.

Implementation begins after the user reviews this written plan and selects an execution method. Recommended: native execution in this session, followed by an independent whole-branch review, because the tasks share contract, fixture, runtime and runner interfaces. Subagent-driven execution remains available for a separate implementer/reviewer gate at each task.
