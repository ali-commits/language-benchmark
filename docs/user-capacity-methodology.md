# User journeys, capacity and fixed populations

The primary workload emulates distinct users rather than treating operations/sec as a user count. The earlier arrival-rate pilot remains separate historical evidence.

## Workload and resources

One k6 virtual user owns one unique seeded account and one stateful session. It performs a real password login, gets a following feed, waits a random 3–10 seconds, likes/unlikes a feed post, waits, optionally reads comments, writes a reply, waits and repeats. A small fraction creates posts. JWTs stay with each user; failed login attempts retry after think time. Every login verifies the real Argon2id hash.

The backend has exactly 2 vCPUs, 3 GiB memory and no swap; aggregate database connections remain 20. Worker topology and dependency pins are recorded per runtime. PostgreSQL/RDS and the calibrated generator have separate budgets. The canonical fixture contains 50000 users and 500000 posts plus the fixed relationships/comments. Restore and fingerprint-check it before and after every probe, including overload failures.

## Timing profiles

| Profile | Login/activity ramp | Settling | Constant population | Scheduled duration |
| --- | ---: | ---: | ---: | ---: |
| `fast` (new examples) |180 s|0 s|240 s|420 s / seven minutes|
| `legacy` |300 s|120 s|300 s|720 s / twelve minutes|

Explicit timing values remain supported. Zero settling moves directly from ramp to steady; it does not omit authentication or phase labels. Seven minutes excludes image deployment, readiness, fixture restoration, k6 initialization/drain and telemetry finalization. Total wall time is therefore longer.

Record the effective timing fields for each trial. Existing AWS configurations/checkpoints retain their explicit legacy timings; adding the profile implementation does not modify the running queue. Compare results with identical effective profiles, think times, request deadlines, password policy, fixture and resources. A timing change requires a fresh campaign/output directory.

## Classification

New examples require `success_fraction=0.90`: at least 90% of steady operations return the expected valid response within `timeout_seconds=5`. Failed responses and timeouts stay in the denominator. Authentication is a separate result and gate: `authentication_fraction` controls how many target accounts must successfully log in, defaulting to the recorded response target for older configurations that omit it. New examples explicitly set both targets to 0.90. Legacy campaigns typically recorded 0.99.

The full target virtual-user population must be reached. Ramp authentication and steady activity are reported separately; this workload includes startup authentication and is not a capacity test of already-logged-in users alone. Changing ramp speed, think times or the authentication gate changes the experiment. p50/p95/p99/p99.9 are diagnostic measurements rather than an additional latency qualification rule.

Infrastructure-invalid evidence never determines backend capacity. Missing/partial telemetry, generator saturation or socket limits, insufficient accounts, bad configuration, failed cleanup and backend open-file exhaustion require correction/retry. Preserve those attempts and their reasons. A normal slow response or expected backend overload remains valid failure evidence when the infrastructure is qualified. Previously constrained Rust observations must not silently become valid capacity evidence.

## Capacity search

`experiment_type=capacity` searches for a measured passing/failing bracket. Retest the previous runtime's valid passing count and any higher failing-count hint; hints never establish bounds for a new runtime. If the starting count fails, search downward. Geometric expansion doubles a passing population after any measured hint, caps it at the fixture ceiling and then binary-searches to a bracket no wider than 100 users (at most ±50 around its midpoint).

New examples use no additional passing repeats and rerun the final failing endpoint once. A failing endpoint that passes on retest is marked unstable; earlier valid contradictory trials remain visible. This is less repeatability evidence than the original five-repeat campaign. Valid unstable results do not stop later runtimes; persistent invalid evidence exhausts bounded recovery and needs attention.

The 50000-account ceiling is a fixture limit. Passing it establishes a lower bound, not a measured maximum. Increasing it requires a larger identical fixture for all compared runtimes.

## Fixed populations

`experiment_type=fixed-load` probes each configured runtime at each `fixed_users` population once, using the same real logins, profile, deadlines, generator checks and guarded restoration. The example starts with `[100]`. Valid below-target responses remain measured failure evidence and execution proceeds to the next population/runtime. Infrastructure retries remain bounded.

Fixed-load mode does not expand, binary-search or confirm endpoints. It answers resource efficiency and responsiveness at a specific population; a successful 100-user trial does not establish a 100-user capacity limit. Fixed populations, experiment type, timing, response/authentication targets and runtime/image identities participate in checkpoint compatibility.

## Metrics and threshold analysis

Raw samples retain `ramp`, `settle`, `steady` and `drain` phases, operation names and failures. Steady attempted requests/sec and successful operations/sec use the configured steady duration, not the incidental interval between the first and last sample. Report partial execution explicitly.

Analysis reports mean response latency including failed requests, p50/p95/p99/p99.9, endpoint distributions, response and authentication fractions, timeouts, active users and completed journeys. Resource summaries separate phases and distinguish the backend from the generator and RDS. Backend Docker CPU is normalized to its allocation:100% means both allocated CPUs are fully used. Memory averages/peaks are sampled Docker working-set MiB, not a precise allocator/heap profile; transient peaks between samples can be missed.

The analysis CLI accepts finite response thresholds in `[0.90,1.00]` and defaults to 0.90,0.95,0.99,0.999. It reclassifies recorded valid observations while preserving immutable invalidity reasons and the recorded authentication gate, unless an explicit authentication-threshold override is supplied. Show highest observed passing and lowest observed failing populations, contradictory observations and unsampled ranges. A 90%-guided search does not measure the exact capacity at 95%,99% or 99.9%; sparse samples provide observed bounds only. Fixed-load analysis remains a population comparison.

Older artifacts can be analyzed without inventing missing percentiles or resource measurements. Preserve their original effective methodology and label inferred legacy fields. CloudWatch queries use overlapping minute buckets while retaining exact request windows and disruptive-event checks.

## Future comparison reference and commands

New capacity/fixed-load examples compare `python-modern-3.14.8-standard-jit0-gil1-async` with the six native stacks. It uses the stable Python 3.14.8 interpreter, standard PGO/LTO build, two async processes, GIL on and JIT off. Its existing modern dependency lock intentionally retains Pydantic 2.14.0b2/Core 2.49.0; this is a stable **runtime** reference, not an all-stable/latest dependency claim. Python 3.15 experimental cells and evidence are preserved.

Use the generic controller for a mixed Python/native queue:

```sh
PYTHONPATH=tools uv run -m bench.cli user-capacity \
  --config benchmarks/user-capacity.example.json --output artifacts/results/new-capacity
PYTHONPATH=tools uv run -m bench.cli user-capacity \
  --config benchmarks/fixed-load.example.json --output artifacts/results/new-fixed-load
PYTHONPATH=tools uv run -m bench.cli analyze \
  --input artifacts/results/new-capacity --output artifacts/analysis/new-capacity.json \
  --threshold 0.95
```

These are templates: provide the canonical fixture/credentials, dedicated database allowlist, admin environment, backend/load/build hosts and their private endpoints. Native cells require `expected_image_ids` from tested immutable images; backend runtime-lock/image identity, fresh nonce,2 CPU/3 GiB/no-swap, nofile 250000 and read-only RDS CA mount are checked before timing. Python workers attest their release, build flags, dependencies and GIL/JIT state. The native-only entrypoint rejects Python cells and must not launch these mixed examples.

A separate legacy template is `benchmarks/user-capacity.legacy.example.json`. Keep private AWS configuration under ignored `artifacts/aws/`; do not put credentials in examples or commands. Starting a new profile/threshold/mode/image version requires fresh output. Same-methodology restarts can replay saved valid probes with `--resume --retry-invalid` after verified cleanup. See the [AWS supervision/shutdown runbook](aws-deployment.md#automatic-controller-supervision). No existing AWS queue is changed by this staged implementation.
