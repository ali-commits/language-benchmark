# Reusable benchmark engine and configurable queues

Status: proposed specification for user review; implementation has not started.

## Purpose and agreed scope

Turn this repository into a tool that other people can use to compare languages,
frameworks, versions, and unrelated HTTP applications. Keep the social API as a
bundled, reproducible workload. Ali selected this approach and explicitly requested
user-defined fixed-load and stress tests, including user counts and durations.

Deliver a versioned YAML configuration, a small CLI, portable execution, resumable
editable queues, and accurate usage/results documentation. Retain uv. Work in the
main worktree. This project change does not start another AWS benchmark or alter
original measurements.

## Architecture

Separate four concepts:

1. Target: an application image or Docker build, readiness check, resource limits,
   runtime/version labels, and environment-variable references.
2. Workload: a k6 journey and its metric contract, optional fixture lifecycle, and
   workload parameters such as waits or request deadline.
3. Environment: controller, backend, generator, networking, optional database,
   and telemetry capabilities. Local execution and SSH execution use the same
   lifecycle operations.
4. Queue: ordered jobs selecting targets, workload, environment, and test policy.

Extract reusable lifecycle/evidence/search operations behind these boundaries;
do not make the new interface a YAML wrapper over AWS-specific campaign files.
Keep old commands and campaign readers operational for historical reproduction.
The existing runtime registries, specialized attestation and canonical PostgreSQL
fixture checks belong to the social workload integration. Generic targets need
neither a known language name nor a social-schema database.

Use focused modules under `tools/bench/` for configuration, target/workload
adapters, execution, queue persistence/control, and analysis. Expose an installed
`bench` entry point so `uv run bench ...` needs no manual PYTHONPATH setting.
Separate orchestration dependencies from backend application dependencies where
necessary; do not change backend locks or binaries as a side effect.

## Configuration contract

The public document has `version: 1`. Safe YAML parsing rejects duplicate keys,
unknown fields, invalid types, nonfinite numbers, unresolved references, and
invalid combinations. JSON with the same schema is also accepted. Relative paths
resolve against the declaring file, never the caller's working directory.
Durations accept units such as `30s`, `5m`, and `1h`; normalized manifests store
seconds. Resource quantities use explicit units. Secrets use environment-variable
references and are excluded from public snapshots and command output.

A single file is sufficient for a first run. Optional explicit definition files
allow reuse of targets, workloads, and environments; collisions are errors, with
no implicit deep-merge or arbitrary YAML templating.

Illustrative complete public shape (references and paths are validated before run):

```yaml
version: 1
name: api-comparison

environments:
  local:
    backend: {transport: local}
    generator: {transport: local}
    network: {mode: compose}
    telemetry: {host: optional, container: required}

targets:
  candidate:
    image: example/api:1.0
    port: 8000
    readiness: {path: /health, timeout: 60s}
    resources: {cpus: 2, memory: 3GiB, swap: 0, nofile: 250000}

workloads:
  api:
    adapter: k6
    script: ./workloads/api.js
    metrics:
      success: {name: operation_success, type: rate}
      latency: {name: operation_duration, type: trend, unit: ms}
    parameters: {request_timeout: 5s}

defaults:
  environment: local
  workload: api
  timing: {ramp: 5m, settle: 0s, sustain: 2m}
  criteria: {success_rate: 0.90}

jobs:
  - id: fixed-comparison
    targets: [candidate]
    test:
      type: fixed
      users: [100, 500, 1000]
      repetitions: 1

  - id: stress-search
    targets: [candidate]
    test:
      type: stress
      strategy: search
      users: {min: 1, start: 1000, end: 10000}
      expansion_factor: 2
      resolution: 50
      confirmation: majority_on_disagreement

  - id: stress-sweep
    targets: [candidate]
    test:
      type: stress
      strategy: sweep
      users: {start: 100, end: 1000, step: 100}
      stop_on_failure: false
      repetitions: 1
```

Examples shipped with the implementation must actually run with provided sample
targets and scripts, replacing illustrative image names. `bench init` generates
a small local demo; a separate social template selects the bundled API/fixture.
Job-level timing, criteria, and environment override the corresponding defaults
by documented field rules, and `validate --show-effective` prints the result.

## Test semantics

### Fixed load

`users` accepts a positive integer or a nonempty list of unique positive integers.
Each target/population pair gets a fresh trial with ramp, optional settling, and
sustained measurement. Preserve the declared order. Repetitions are explicit and
all outcomes remain visible. A valid below-threshold result is recorded and the
next population proceeds. Infrastructure-invalid runs have separate bounded
retries and never count as application failures.

### Stress sweep

Run independent, reset trials at `start`, successive `step` increments, and `end`.
Include `end` once even if the interval is not evenly divisible by `step`.
`stop_on_failure` chooses between covering all loads and stopping at the first
valid failing population. This is a load curve, not a continuous ramp across
trials. Every population uses the configured timing and repetitions.

### Stress search

Treat `end` as a hard maximum. Validate `1 <= min <= start <= end`, factor > 1,
and positive resolution. Probe the start, expand geometrically when passing,
or search downward toward `min` when failing. Never assume an unmeasured count
passes. Bisect a measured bracket until failing minus passing <= resolution.
Round/clamp integer candidates to guarantee progress; do not silently exceed end.
Passing at end means a lower bound; failing at min with no passing count means
capacity below the tested minimum, not a fabricated zero-user passing result.

Default confirmation preserves the current behavior: no extra passing repeats;
repeat the candidate failing endpoint once, and run a third trial only when
two valid outcomes disagree. Two agreeing valid votes decide. Saved votes are
unique trial IDs. A fail endpoint that becomes a pass reopens the search.
Contradictory measured counts remain visible and can produce an unstable result,
with finite probe/recovery budgets preventing endless search. Support an explicit
single-observation policy for exploratory runs, labeled accordingly.

Prior results can supply starting hints only. Reuse measured bounds/votes only
within a matching immutable experiment identity. Timing, binary, workload,
fixture, criteria or resource changes create a new experiment.

### Duration and criteria

Duration fields apply per trial: timed duration = ramp + settle + sustain.
Report deployment, reset, readiness, drain and recovery time separately. Ramp
and settle may be zero; sustain must be positive. Show a trial count and timing
estimate before execution, identifying uncertain search/retry time.

Success is a workload-declared operation rate with an explicit denominator;
missing samples are invalid, never 100% success. Allow success fractions in
(0, 1], and optional named latency criteria such as p95/p99 ceilings. For the
social workload, record operation success within the deadline and the distinct
authenticated-account fraction separately. Generic workloads do not require an
authentication gate. Criteria apply to sustained samples; phase labels exclude
ramp, settle and drain. Raw phase data remain available for analysis.

## Workload and fixture lifecycle

Provide a documented k6 module contract and example wrapper. The engine controls
VU scheduling, phases, output destinations and timeouts; the user implements the
journey and emits declared metrics. Validate the workload with a small explicit
smoke command before a substantial run. Record script and dependency checksums.

Lifecycle: resolve/build -> acquire resource ownership -> preflight -> reset and
verify fixture -> clean baseline -> deploy -> readiness -> ready baseline -> run
load -> drain/stop owned load and application -> restore and verify fixture ->
finalize evidence. Snapshot state before and after each irreversible transition.

Generic stateless apps can declare no fixture. Stateful workloads declare a
fixture adapter or explicit executable argv hooks for prepare/reset/verify;
hooks are trusted user code with declared host, timeout and environment refs,
not shell strings inferred from YAML. Social uses the existing guarded database
restore/fingerprint adapter. Cleanup failure blocks the next job. A crashing
controller must reconcile actual owned resources before retrying a trial.

## Portability and telemetry

First release: Linux local Docker Engine/Compose, and remote Linux Docker hosts
over SSH. No AWS account, RDS, systemd, fixed username or fixed project path is
required for the generic runner. Foreground control works without systemd;
optional user-systemd service installation provides durable Linux queues.
Do not claim macOS/Windows native host telemetry support until verified. Document
WSL2 Linux execution and Docker VM boundaries as separate environments; unsupported
capabilities must be reported rather than silently substituted.

Support a single-machine development layout and separate backend/generator
hosts. Label colocated load-generator contention in results; do not represent
container quotas as dedicated physical VM capacity. Explicit routing determines
how the generator reaches the backend. Isolate networks/containers by run owner.

Record whole-backend-host CPU and memory separately from container CPU/memory,
load-generator health, HTTP attempted/successful throughput, latency distribution,
errors and timeouts. Optional database collectors include RDS CloudWatch; generic
runs do not require AWS or database telemetry. Capability requirements distinguish
`required`, `optional` and `off`; unavailable optional values are null with reason.
Record sample intervals, windows, aggregation definitions and metric provenance.

Generic image references resolve to immutable identities before measurement;
restarts verify them. Builds occur before measurement and outside the measured
backend where a separate build host is configured. Bundled social comparisons
retain stronger runtime/worker checks. Resource budgets are configurable, with
effective limits validated and recorded rather than globally fixed at 2 CPUs.

## Queue CLI and state

```sh
uv run bench init --template http --output comparison.yaml
uv run bench validate comparison.yaml --show-effective
uv run bench doctor comparison.yaml
uv run bench queue start comparison.yaml
uv run bench queue list
uv run bench queue status api-comparison
uv run bench queue logs api-comparison
uv run bench queue append api-comparison --job additional-job.yaml
uv run bench queue update api-comparison job-id --file replacement-job.yaml
uv run bench queue remove api-comparison job-id
uv run bench queue stop api-comparison
uv run bench queue resume api-comparison
uv run bench report api-comparison --output report.html
```

`start` registers a uniquely named queue and runs it in the foreground by default;
`--detach` uses the installed optional service adapter, with clear errors if it is
unavailable. Commands address queues in an explicit `--state-dir` or the project
default `.bench/`. Queue registration copies a normalized definition and records
source paths/hashes; later editing the original YAML alone does not mutate it.

Use a transactional local SQLite control store for jobs, revisions and commands,
and ordinary immutable per-trial files for evidence. Controller ownership plus
backend/generator resource leases prevents competing controllers. All CLI
mutations and controller dispatch use transactions so an update/remove racing
dispatch either wins while pending or fails clearly after activation. Require
local storage for the control database; do not promise network-filesystem locks.

Append creates pending jobs with unique IDs. Update and remove apply only to
pending jobs; remove marks cancelled and preserves audit history. The active
job's normalized configuration is frozen. Changing a completed/running job means
appending a new job, never rewriting its evidence. A queue can accept pending
jobs while running. Appending after completion does not launch them automatically;
`resume` is explicit. Shared definition changes cannot affect frozen jobs.

`stop` immediately requests graceful cancellation of the active load, then waits
for owned-resource shutdown and fixture restoration. Report `stopping` until
cleanup is verified; interrupted trials are not valid votes. Pending jobs remain
queued. `resume` reconciles incomplete cleanup, then resumes compatible evidence
and pending jobs. If cleanup cannot be verified, stop with `needs_attention`.
Provide `--after-current` to finish the active job before pausing dispatch.
No CLI command stops unrelated Docker resources or cloud machines.

States: pending, preparing, running, cleaning, completed, interrupted, cancelled,
needs_attention. A failed performance criterion is an observation, not a queue
infrastructure error. Recovery attempts have persistent finite budgets; optional
continuation after a job infrastructure failure is allowed only after cleanup is
verified, with default stop-on-infrastructure-failure.

## Documentation and results publication

Rewrite README around purpose, supported/tested platforms, a runnable local
example, fixed/stress YAML and queue commands. Link a concise configuration
reference, custom workload/target guide, execution/telemetry guide, troubleshooting
and reproducibility guide. Move experiment-specific operations into historical
runbooks and remove contradictory claims that completed stacks are only staged.

Publish Ali's measurements under `docs/results/2026-10/`: methods, fixed100/500/1000
summary tables, final stress brackets, versions/topologies, full-VM versus Docker
metric definitions, limitations, and sanitized machine-readable summary data.
Nine configurations include FPM, Octane and .NET. Preserve the distinction between
fixed180/0/240 and stress300/0/120 schedules, authentication and operation gates,
and earlier12-minute99% evidence. Document historical file-limit-invalid trials
only in their relevant historical cohorts. Include Go's split majority evidence.
Do not make universal language-speed claims from this application deployment.

Use existing verified report reductions as sources; retain checksums/trial IDs
and source provenance. Choose stable tracked locations for final PDFs or documented
release assets, so README links do not point into ignored private output folders.
Exclude credentials, dumps, personal host paths and private infrastructure config.

## Acceptance and verification

- A clean checkout can run a small provided HTTP example from YAML with uv and
  Docker, without AWS credentials or a social database.
- Two arbitrary image versions can be compared without modifying a language enum.
- Fixed lists and sweeps schedule exactly the declared populations; search respects
  min/end/resolution and records majority evidence without a fourth valid vote.
- Parser tests cover typos, duplicate YAML keys, secret refs, relative paths,
  duration/resource units and invalid test combinations.
- Queue tests cover concurrent append/update/remove versus dispatch, stop/resume,
  crash during cleanup, completed queue append, immutable evidence and leases.
- Disposable Docker integration verifies generic HTTP and social lifecycle paths;
  remote transport tests verify quoting, paths and ownership. Validate Linux
  foreground behavior without systemd and optional service behavior separately.
- A deliberately unready backend, overloaded generator or failed fixture reset
  yields invalid/needs_attention evidence rather than false capacity conclusions.
- Existing runner/majority/pipeline tests remain passing; legacy JSON commands
  preserve existing manifests and measurement identities.
- README commands and examples are exercised; results tables reconcile to exported
  evidence, with missing telemetry explicit and no secrets in public artifacts.

## Delivery order and exclusions

Implement configuration/entry point and generic lifecycle first, then queue state
and controls, social integration, reports/documentation, and end-to-end validation.
Keep historical execution paths until compatibility tests justify migration.

Out of scope: web UI, cloud provisioning, native Windows controller, Kubernetes,
distributed multi-generator coordination, arbitrary non-HTTP benchmark engines,
and automatic starting/stopping of cloud resources. These do not prevent users
from running Docker backends or SSH hosts on different cloud providers.
