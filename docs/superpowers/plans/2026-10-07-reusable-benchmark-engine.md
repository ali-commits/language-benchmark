# Reusable Benchmark Engine Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let users compare arbitrary HTTP codebases using configurable stages, fixed-load measurements, metric-constrained capacity searches and editable, resumable queues.

**Architecture:** A generic engine owns configuration, scheduling, evidence and lifecycle; a bundled social adapter owns schema, credentials and specialized runtime checks. SQLite stores queue revisions and control state; immutable files store measurements. Preserve the existing campaign interfaces and historical evidence.

**Tech Stack:** Python with uv, existing Pydantic and PyYAML, standard-library SQLite/argparse, Docker/Compose, k6 JavaScript, local/SSH transport, optional Linux user systemd and RDS CloudWatch.

**Spec:** `docs/superpowers/specs/2026-10-07-reusable-benchmark-engine-design.md` (approved in chat on 2026-10-07).

## Global Constraints

- Work in the main worktree; no new AWS experiment is authorized by this implementation.
- Public configuration version is `1`; secrets are environment references.
- Two primary user concepts: fixed-load and search. A sweep is fixed-load population generation; a standalone stage sequence is a fixed test with absolute stages. Accept the spec's `stress`/`staged` spellings as explicit aliases, normalized to these two concepts.
- Every stage has an ID, starting users, ending users, positive duration and evaluation selection.
- First release runs on Linux Docker, locally or through SSH; systemd is optional.
- Search always has finite trial/time budgets, even with `max: auto`.
- Never mutate completed measurement artifacts or relax legacy runtime guards.
- Missing required evidence is invalid, not an application failure or a passing zero.
- Generic infrastructure does not require the social schema, AWS, RDS, fixed paths or a known language enum.
- Preserve explicit host versus container CPU/RAM scope and historical calculation definitions.
- Keep commands and artifacts free of credentials; only disposable local resources are used for integration verification.

## Review Focus

1. Dispatch racing an update/remove must atomically freeze the old job or accept the new revision, never mix them (Task 8).
2. User reduction can leave requests in flight; stage attribution and achieved occupancy must remain accurate (Tasks 2, 6).
3. An SSH disconnect after starting a container must reconcile owned resources without double launch or accidental deletion (Task 7).
4. Missing host metrics, NaNs or partial gauge coverage must not move search bounds (Tasks 3, 4, 7).
5. Editing a shared script/image tag after enqueue must not silently alter a job or reuse incompatible votes (Tasks 5, 8, 9).

## File and interface map

New engine files live under `tools/bench/engine/`; queue controls under
`tools/bench/queue/`. Do not move legacy modules wholesale.

- `engine/config.py`, `types.py`, `stages.py`: typed configuration and stage resolution.
- `engine/metrics.py`, `search.py`: metric evaluation and pure search transitions.
- `engine/targets.py`, `workloads.py`, `social.py`: deployment and workload adapters.
- `engine/collectors.py`, `evidence.py`, `trial.py`: collection, immutable evidence and lifecycle.
- `queue/store.py`, `leases.py`, `controller.py`, `services.py`: durable queue and ownership.
- `commands.py`: installed public entry point; legacy `bench.cli` remains available.
- `benchmarks/k6/engine/`: reusable executor wrapper and helpers.
- `examples/http/`, `examples/social/`: actual runnable examples and YAML.
- `docs/configuration.md`, `docs/queues.md`, `docs/custom-workloads.md`,
  `docs/environments.md`, `docs/metrics.md`, `docs/results/2026-10/`: user documentation.

Contracts defined in `engine/types.py` use immutable Pydantic models with extra
fields forbidden. `ResolvedJob` contains frozen definitions and source hashes;
`ResolvedStage` contains concrete endpoints and seconds; `Sample` contains metric,
scope, timestamp, interval, stage, value and tags; `Evaluation` contains verdict,
criterion values and reasons; `TrialEvidence` references immutable samples,
manifest and cleanup evidence. Each consumer imports these types rather than
inventing a second dict schema. `SearchState` and `QueueStore` are introduced in
Tasks 4 and 8 respectively.

## Task 1: Validated YAML and usable entry point

**Files:** create `tools/bench/commands.py`, `tools/bench/engine/{__init__,types,config}.py`, `tests/unit/test_engine_config.py`; modify `pyproject.toml`, `uv.lock`, `.gitignore`.

**Interfaces:** `load_config(path: Path) -> BenchmarkConfig`;
`resolve_jobs(config: BenchmarkConfig) -> list[ResolvedJob]`;
`commands.main(argv: list[str] | None = None) -> int`.

- [ ] Write failing parser tests for duplicate keys, unknown fields, bool-as-user-count, NaN, missing environment refs, relative paths from another working directory, and conflicting imported definitions. Pin units: `5m -> 300`, `1GB -> 1_000_000_000`, `1GiB -> 1_073_741_824`, `50% -> 0.5`.
- [ ] Run `uv run pytest tests/unit/test_engine_config.py -q`; confirm the missing engine produces a failing test, then implement the strict schema and safe YAML loader.
- [ ] Normalize fixed lists, sweep aliases, search settings and ordered stages. Defaults merge scalar fields explicitly; stage/criterion lists replace rather than concatenate. Reject incompatible metric units/types. Produce public redacted snapshots while retaining secret reference names.
- [ ] Add the `bench` console script using a build backend and discovery limited to `tools/bench`. Keep backend lockfiles untouched. Define an orchestration dependency group/extra and retain legacy development dependencies; do not import application libraries merely to print help. Ignore `.bench/`.
- [ ] Verify `uv sync --locked`, `uv run bench --help`, `uv run bench validate --help`, and the focused parser tests pass. Commit only this task's files.

## Task 2: Stage resolution and population expansion

**Files:** create `engine/stages.py`, `tests/unit/test_engine_stages.py`; extend models in `engine/types.py`.

**Interfaces:** `resolve_stages(job: ResolvedJob, users: int | None) -> tuple[ResolvedStage, ...]`;
`fixed_populations(job: ResolvedJob) -> tuple[int | None, ...]`.

- [ ] Write failing tests: fixed `[100,500,1000]` preserves order; sweep100..350step100 yields `(100,200,300,350)`; floor(101*0.5)=50; adjacent endpoints disagreeing after resolution are rejected; zero-user observation is accepted; search without an evaluated target hold is rejected.
- [ ] Run `uv run pytest tests/unit/test_engine_stages.py -q`, then implement deterministic stage binding. Validate absolute endpoints against the search minimum, unique IDs, finite fractions and positive durations. Search stages cannot exceed candidate users.
- [ ] Pin example total duration: 3m ramp +2m hold +1m reduction +2m recovery +1m cooldown +30s idle =570s. Record planned active-user curve and criterion windows; no fixture reset occurs between stages.
- [ ] Test standalone absolute stage sequences and requested/actual occupancy tolerance schema (`fraction: 0.05`, `grace: 0s` default, overridable). No-traffic stages cannot qualify a response criterion.
- [ ] Re-run stage/config tests and commit.

## Task 3: Metric evaluation and objective-specific decisions

**Files:** create `engine/metrics.py`, `tests/unit/test_engine_metrics.py`; extend `Sample`, `Evaluation`, `TrialEvidence` models.

**Interfaces:** `evaluate(evidence: TrialEvidence, criteria: tuple[Criterion, ...]) -> Evaluation`;
`execution_identity(job: ResolvedJob, users: int | None) -> str`;
`decision_identity(job: ResolvedJob, objective: str) -> str` (identity functions in `engine/evidence.py`).

- [ ] Write failing tests for combined raw quantiles (not mean of quantiles), distinct authentication denominator, absent samples, NaNs, uncovered gauge windows, and contradictory scopes. For gauge values10 over1s and30 over3s, assert time-weighted mean25.
- [ ] Run `uv run pytest tests/unit/test_engine_metrics.py -q`, then implement rate, mean, max and p0..p100. Quantiles use linear interpolation at `(n-1)*q`; rate uses summed numerator/denominator. Clip CPU intervals to windows; gauges hold until the next sample with a maximum allowed gap. Do not interpolate across missing coverage.
- [ ] Add explicit per-criterion minimum samples and coverage (`min_samples: 1`, `min_coverage: 0.95` defaults). Coverage applies to temporal collectors; event latency uses sample-count validity. Record counts and warn that very high quantiles on few samples are descriptive. Missing/nonfinite required samples invalidate instead of being discarded to manufacture a pass.
- [ ] Scope CPU host normalization to all host CPUs, container to quota. Preserve provider field names, collection interval and formulas. Keep latency including failed requests distinct from success-only latency; common success criteria apply to every objective.
- [ ] Test identical execution hashes but different decision hashes when only p99 threshold changes; changing stage timing or script hash changes execution hash. Run metrics/config tests and commit.

## Task 4: Bounded adaptive search with reusable raw evidence

**Files:** create `engine/search.py`, `tests/unit/test_engine_search.py`; reuse legacy majority logic only through compatible pure helpers, without changing old manifests.

**Interfaces:** `next_probe(state: SearchState, policy: SearchPolicy) -> int | None`;
`record_probe(state: SearchState, users: int, trial_id: str, evaluation: Evaluation) -> SearchState`;
`search_result(state: SearchState) -> SearchResult`.

- [ ] Write failing tests: start1000 grows1000/2000/4000 with auto max; valid failures bracket and bisect; start fail searches down; minimum fail invents no zero pass; ceiling pass is lower-bound; exhausted40-attempt budget is incomplete; deadline prevents a new probe but not cleanup.
- [ ] Run `uv run pytest tests/unit/test_engine_search.py -q`, then implement integer candidate transitions and durable state serialization. Account/generator limits cap feasible users; no silent expansion of infrastructure.
- [ ] Add majority tests for fail/fail, fail/pass/pass and fail/pass/fail; unique valid trial IDs only, no fourth valid vote. Invalid attempts consume the job budget but never move bounds. A newly passing former failing endpoint reopens expansion.
- [ ] Test objective-specific decisions reusing the same compatible raw evidence; never reuse labels from another objective. Run objectives in declaration order under one job budget. Persist nonmonotonic contradictions and return unstable when unresolved.
- [ ] Verify fixed mode does not call search confirmation; repetitions retain all measurements. Run new search and existing `test_capacity.py`, `test_majority_boundary.py`, `test_pending_outcome.py`, `test_resolution_handoff.py`. Commit.

## Task 5: Generic targets, hooks and reproducible inputs

**Files:** create `engine/targets.py`, `engine/workloads.py`, `tests/unit/test_engine_targets.py`, `tests/unit/test_engine_workloads.py`; narrowly extend `tools/bench/transport.py` if necessary.

**Interfaces:** `resolve_target(target: TargetSpec, environment: EnvironmentSpec) -> ResolvedTarget`;
`prepare_workload(workload: WorkloadSpec, destination: Path) -> PreparedWorkload`;
`run_hook(hook: HookSpec, context: TrialContext) -> HookReceipt`.

- [ ] Write failing tests for two arbitrary image versions without language enums, spaces in paths, remote argv quoting, tag changed after pinning, incomplete transfer, invalid build context and hook timeout.
- [ ] Run focused tests, then resolve image IDs/digests and record build provenance. Generic readiness supports declared HTTP path/status and timeout; verify actual resource limits. Bundle declared local workload dependencies with checksums; reject escaping paths and undeclared remote JS imports for reproducible mode.
- [ ] Hooks execute declared argv on their declared host with timeout and environment references. Never auto-detect shell commands or print secrets in error output. Fixtures may be `none`; no PostgreSQL imports are required for that adapter.
- [ ] Transfer inputs atomically to per-run workspaces, verify destination checksums and resolve generator-reachable endpoint separately from host readiness endpoint. Label owned resources by queue/job/trial IDs. Build before measured execution.
- [ ] Run target/workload tests plus existing transport-dependent unit tests; commit.

## Task 6: k6 wrapper and runnable arbitrary HTTP example

**Files:** create `benchmarks/k6/engine/{runner,metrics}.js`, `examples/http/{compose.yaml,queue.yaml,journey.js,server.py,Dockerfile}`, `tests/integration/test_engine_k6.py`.

**Interfaces:** workload module exports `default(context)` for one bounded journey; context supplies `baseUrl`, `parameters`, `stage`, and operation recording helpers. Runner consumes serialized `ResolvedStage` records; produces raw `Sample` records and actual VU telemetry.

- [ ] Write an integration test against the disposable example: ramp0..4, hold4, reduce4..2, hold2, cooldown2..0, idle0. Assert all stage IDs appear in telemetry, measurements are phase-separated and no-load idle does not fabricate success.
- [ ] Run the explicit opt-in test (`BENCH_INTEGRATION=1 uv run pytest tests/integration/test_engine_k6.py -q`); confirm expected initial failure, then implement the wrapper using pinned k6 and its ramping-VU executor.
- [ ] Record request start-stage and completion-stage, in-flight drain and interrupted journeys. Do not restart k6 between stages. Default bounded graceful ramp-down is5s, configurable and recorded; requested versus observed VU occupancy must satisfy the configured hold tolerance.
- [ ] Test a deliberately slow response crossing a stage boundary and cancellation during reduction. Ensure phase windows use controller/load time alignment and that success/latency denominators match raw events.
- [ ] Verify the HTTP demo runs without AWS or any database, uses only owned containers and leaves none behind; commit. No JS package dependency is needed unless justified; use Bun if one is added.

## Task 7: Generic trial lifecycle, collectors and social adapter

**Files:** create `engine/{trial,collectors,evidence,social}.py`, `tests/unit/test_engine_trial.py`, `tests/integration/test_engine_social.py`; reuse guarded fixture, runtime and stop-load helpers through adapters.

**Interfaces:** `run_trial(context: TrialContext, stop_requested: Callable[[], bool]) -> TrialEvidence`;
`reconcile_trial(context: TrialContext) -> CleanupReceipt`;
`collectors_for(job: ResolvedJob) -> tuple[Collector, ...]`;
`social_adapter(spec: WorkloadSpec) -> WorkloadAdapter`.

- [ ] Write failing lifecycle tests with injected crashes after deploy, during load and during restore; test SSH success-with-lost-response reconciliation, readiness failure, saturated generator, missing required collector, and foreign containers surviving cleanup.
- [ ] Run `uv run pytest tests/unit/test_engine_trial.py -q`, then implement journaled lifecycle transitions and bounded retries. Acquire ownership before mutations; resource identity must be rechecked before cleanup. After ambiguous remote start, inspect by owner labels before retrying.
- [ ] Implement host/container/generator collectors with capability discovery, required/optional/off semantics and scope metadata. Promote criterion-required collectors during validation. Optional RDS collector remains opt-in. Capability absence is never silently replaced with another scope.
- [ ] Integrate stateless and hook-based fixtures, then the existing social fixture checks/runtime attestations. Adapt social journeys to the shared multi-stage contract; original social images, registries and old commands remain intact. Authentication counts distinct accounts over the declared target, not successful login attempts.
- [ ] Test clean stopped/ready baseline gates configurable per environment; resource-only criteria do not weaken workload/cleanup validity. Define immutable per-trial sample/manifest/result files and atomically finalize only after verified cleanup; interrupted evidence remains preserved.
- [ ] Run disposable social verification against a database whose name ends `_test`; exercise small fixture restoration and a100-user short smoke (not a published capacity run). Run legacy lifecycle/reset/stop tests and commit.

## Task 8: Transactional queue state and resource leases

**Files:** create `tools/bench/queue/{__init__,store,leases}.py`, `tests/unit/test_queue_store.py`, `tests/integration/test_queue_leases.py`.

**Interfaces:** `QueueStore(path: Path)` exposes `register(config)`, `append(queue, job)`, `update(queue, job_id, replacement, expected_revision)`, `cancel(queue, job_id)`, `claim_next(queue, owner)`, `request_stop(queue, after_current=False)` and snapshot/read methods. Mutators return typed receipts with revisions.

- [ ] Write failing multiprocess tests for dispatch racing update/remove, duplicate IDs, rollback on disk error and append to completed queue. Exactly one valid revision is claimed; an active job cannot be edited. Cancelling preserves the audit row.
- [ ] Run `uv run pytest tests/unit/test_queue_store.py -q`, then implement SQLite transactions with `BEGIN IMMEDIATE`, foreign keys, schema version and bounded busy timeout. Store queue/job revisions, trials, requests and append-only events. Completed evidence remains in files.
- [ ] Freeze target/workload inputs when registered; verify hashes again before dispatch. Appending after completion changes pending work but does not launch a controller. Original YAML edits do not mutate stored definitions.
- [ ] Add local controller locking and host-side resource leases through both transports. Lease identity uses actual host identity, not just SSH alias; separate state directories pointing to the same backend must still conflict. Never reclaim remote ownership from age alone: reconcile owner/controller/container evidence first.
- [ ] Test second controller and alias-equivalent host contention; require local control-store storage and document network-filesystem exclusion. Run store/lease tests and commit.

## Task 9: Queue controller, stop/resume and optional services

**Files:** create `queue/controller.py`, `queue/services.py`, `tests/unit/test_queue_controller.py`, `tests/unit/test_queue_services.py`.

**Interfaces:** `run_queue(store: QueueStore, queue: str) -> QueueSnapshot`;
`reconcile_queue(store: QueueStore, queue: str) -> QueueSnapshot`;
`install_service(queue: str, state_dir: Path) -> ServiceReceipt`.

- [ ] Write failing tests for stop during preparation/load/cleanup, after-current pause, crash after result persistence before decision persistence, exhausted recovery, missing pinned image and resume after pending append. Finalized evidence must not trigger an unnecessary repeat.
- [ ] Run controller tests, then integrate job expansion, pure search, lifecycle and leases. Poll durable stop requests during cancellable operations; defer cancellation through cleanup. Prevent next dispatch before cleanup is verified.
- [ ] Persist bounded controller/probe attempts across restarts. A valid criterion failure continues fixed jobs; infrastructure failures default to needs_attention. Optional continue-on-error requires verified cleanup. Changed execution identity blocks resume.
- [ ] Add opt-in user-systemd service creation without sudo or a mandatory daemon. Unit uses absolute executable/state paths, bounded graceful shutdown and reboot resume; a deliberate stop disables restart until explicit resume. Foreground runs work when systemctl is absent.
- [ ] Run controller/service tests plus existing pipeline/supervisor regressions. Verify detach errors clearly if service support or user lingering is absent; never claim reboot persistence without that prerequisite. Commit.

## Task 10: Complete user CLI and templates

**Files:** extend `tools/bench/commands.py`; create `examples/social/queue.yaml`, `tests/unit/test_bench_commands.py` and packaged template files under `tools/bench/templates/`.

**Interfaces:** CLI forms are exactly those in the spec. Queue names resolve through project `.bench/` or explicit `--state-dir`. Mutations produce receipts; status supports human output and `--json`.

- [ ] Write failing CLI tests for init, validate/show-effective, doctor, start/list/status/logs, append/update/remove, stop/after-current, resume and report dispatch. Test commands from paths containing spaces and after changing working directory with explicit state-dir.
- [ ] Implement command handlers using Tasks1–9 contracts. Update uses replacement YAML and optional expected revision; show conflict errors and never silently replace active work. Stop waits for cleanup by default with bounded CLI wait; timeout reports still-stopping rather than success.
- [ ] Make init generate functioning HTTP/social templates; include fixed100/500/1000 and auto-search examples with target-relative stages, individual and combined p99/CPU/RAM criteria. Doctor checks routing, capabilities, images, fixture references and generator capacity without starting a benchmark.
- [ ] Show expanded population/stage plans and runtime estimates; unknown search counts are ranges/budgets. No extra interactive approval is required for an explicit start command.
- [ ] Run CLI tests and exercise installed `uv run bench` commands against the short local example. Commit.

## Task 11: Reports, measured results and accurate documentation

**Files:** create `engine/report.py`, `tests/unit/test_engine_report.py`, `docs/{configuration,queues,custom-workloads,environments,metrics}.md`, `docs/results/2026-10/{README.md,fixed-load.csv,stress.csv,provenance.json}`; update `README.md`, `docs/file-structure.md`, legacy runbook introductions.

**Interfaces:** `write_queue_report(store: QueueStore, queue: str, output: Path) -> Path`; sanitized historical exports consume the existing verified report JSON reductions, never private configs.

- [ ] Write report tests for missing metrics shown as unavailable, separate objective bounds, phase curves, explicit incomplete/unstable states and escaped HTML. Check mean/peak/p50/p95/p99/p99.9, attempted/successful RPS, host/container CPU/RAM and optional database metrics against known samples.
- [ ] Generate HTML and JSON summaries with evidence links and collector definitions. Include per-stage and selected-window views; expose decisions without recomputing incompatible legacy formulas. Label colocated generator interference and differing methodology/resource budgets.
- [ ] Publish sanitized fixed27-row and final stress9-row data from verified local report reductions. Assert final Rust3656/3687, Go3250/3281, Bun2937/2968, Node2843/2875, Python2375/2406, Java2062/2093, Octane1718/1750, .NET1281/1312 and FPM300/350. Preserve Go's fail/pass/pass votes and fixed versus stress timing differences.
- [ ] Copy the two existing final PDFs into `docs/results/2026-10/reports/`, verify checksums against their provenance, and use relative links so a clone has working reports. Do not regenerate measured evidence. Record which raw private artifacts are not distributed.
- [ ] Rewrite README with tested quick start and two-mode explanation; document hooks, workload contract, scope formulas, queue mutation/stop semantics, local/SSH setup, optional systemd and database telemetry. Mark old runbooks as historical, removing conflicting current-status claims.
- [ ] Check tables against source rows, scan public artifacts for private host addresses/credentials/absolute personal paths, and check Markdown links. Run report tests; commit.

## Task 12: End-to-end acceptance and compatibility review

**Files:** create `tests/integration/test_engine_acceptance.py`, `docs/verification/reusable-engine.md`; adjust only defects demonstrated by validation.

- [ ] Run `uv run pytest tests/unit -q` and `uvx ruff check tools tests`; fix concrete regressions without weakening existing guards.
- [ ] Run opt-in Docker integration for two arbitrary example image versions, fixed populations, a staged recovery curve and a deterministic metric-driven search. Assert exact trial populations, metrics, budget stop reasons and owned-resource cleanup.
- [ ] Exercise a Linux SSH host in a disposable local test environment, including path spaces and a simulated lost transport response. Validate both foreground without systemd and service behavior where user systemd is available; document any platform verification gap explicitly.
- [ ] In separate processes, append/update/remove pending jobs during a running local queue, stop and resume mid-load, kill the controller and reconcile. Assert no overlapping backend ownership, duplicate valid vote, lost evidence or false completed state.
- [ ] Execute README quick-start commands from a fresh temporary checkout/environment with empty queue state and no AWS credentials. Ensure packaged templates load and old `PYTHONPATH=tools uv run -m bench.cli --help` still works.
- [ ] Record commands, platform versions, outcomes and limitations in the verification document. Review the complete diff against the approved spec, resolve correctness findings and commit only verified fixes/documentation. Do not deploy to AWS.

## Plan self-review and execution boundary

Coverage: configuration/entry point1; stages2; metrics/objectives3; search4;
target/workload contract5–6; telemetry/fixtures7; queue ownership8; recovery9;
CLI10; results/docs11; integrated acceptance12. All five Review Focus risks have
explicit tests in their owning tasks. Shared types and signatures are defined
before consumers; no legacy measurement format is repurposed.

This is one integration plan because queue correctness depends on trial cleanup
and evidence identity; splitting it into independently shipped engines and queue
products would leave those contracts unverified. Each task nevertheless has an
independently testable deliverable and a scoped commit.

Review this plan before implementation. Preserve Ali's prior preference for
subagent implementation with main-chat coordination; use existing explicit
execution authorization only after the written-plan review. Independent docs or
adapter tasks can be delegated once their input interfaces are stable; do not
parallelize edits to shared core contracts.
