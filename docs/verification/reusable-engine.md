# Reusable engine verification

This is a functional acceptance record for the version-1 engine, not a capacity measurement. Verification uses disposable local Docker resources, an unprivileged loopback OpenSSH daemon and temporary queue state. The social integration test creates and restores only its own disposable local PostgreSQL database. No AWS experiment or existing database is used.

## Platform

Observed on 2026-10-07: Linux 7.2.9-1-cachyos, Python 3.14.8, uv 0.12.23, Docker Engine 29.8.2, systemd 262 and pinned grafana/k6:1.4.2. Docker and SSH tests run serially because the engine deliberately reserves each daemon for one benchmark owner.

## Repeatable commands

```sh
uv sync --locked
bun install --frozen-lockfile
uv run --locked mypy
uv run --locked ruff check .
uv run --locked ruff format --check .
bun run typecheck
bun run check
uv run --locked pre-commit run --all-files
uv run --locked pytest tests/unit -q
BENCH_INTEGRATION=1 uv run --locked pytest tests/integration/test_engine_acceptance.py tests/integration/test_queue_leases.py tests/integration/test_engine_social.py tests/integration/test_engine_k6.py -q
```

The opt-in suite needs Docker, cached/buildable HTTP example and k6 images, and OpenSSH server/client binaries. The service test additionally needs an available user bus and already-enabled lingering; it does not enable lingering. Its new unit name is derived from a unique queue and temporary state directory. It removes that unit after execution and never stops or changes an existing unrelated service.

## Observed outcomes

- Final post-refactor unit suite: **551 passed, 9 opt-in skips**, 22.14 seconds.
- Full real acceptance after the stdout fix: **16 passed in 347.38 seconds**.
  This includes all six core acceptance cases, three lease tests, three social
  tests and four k6 lifecycle cases. Its production launcher used `--quiet --no-summary`; the
  final source uses the equivalent canonical `--summary-mode=disabled` form.
  With that production launcher, two-version acceptance and the four k6 cases
  passed again: **5 passed in 148.62 seconds**. The low-level k6 launcher was then
  aligned with the final flags and asserts JSON-only stdout, retained BENCH_EVENT
  stderr and no deprecation warning: **4 passed in 33.18 seconds** after a failing
  real stdout-purity regression.
- Locked clean reinstallation: `uv sync --locked --reinstall` resolved 87 packages
  and reinstalled 78; `bun install --frozen-lockfile --force` installed six packages.
- Strict Mypy: **72 production files**, no errors. Tests are excluded from this
  type scope and covered by Ruff and executed regressions.
- Ruff lint passed; **144 files** already formatted. Strict TypeScript/checkJs
  passed; Biome checked **30 eligible files**, with informational style suggestions.
- All seven pre-commit all-files gates passed with the installed hook active.
- Temporary Python and JavaScript source probes each assigned a number to a
  string and failed their full configured checker with assignment diagnostics.
  Both were removed; both full type gates then passed again.
- A newly built, extracted wheel generated HTTP and social templates outside the
  checkout. Both templates passed strict JavaScript checking with their packaged
  canonical declarations. The unit suite also runs extracted-wheel social doctor.
- All **181 protected SHA256 hashes** matched the Task 13 baseline. Backend tree:
  `3f9dbff901a1b81e556b66487218a605bda799e1`.

The real user service was stopped during load with systemd, cleaned up, remained enabled without a durable user-stop flag, resumed explicitly and completed with one valid result. Completion disabled its unit; final cleanup removed its file and enablement. No host reboot was attempted.

Final acceptance exposed interleaved k6 progress text inside native metric JSON
on stdout (11 passed, one failed in the initial run). The generator now runs with
`--quiet --summary-mode=disabled`; native JSON metrics and BENCH_EVENT logging remain enabled.
The parser still invalidates malformed evidence. A launch regression failed before
the fix and passed afterward. Earlier parser regressions continue to distinguish
`new.target` from prohibited `import.meta`, and quoted YAML `off` remains covered.

## Acceptance coverage

| Surface | Evidence |
| --- | --- |
| Arbitrary versions | Two separately labelled, distinct immutable HTTP image IDs named orchid and copper; every retained manifest must match its named version and original image tag. No language registry change. |
| Fixed populations | Exact (target, users) pairs orchid/1, orchid/2, copper/1, copper/2; the complete ten-trial (job, target, users) multiset, unique trial IDs and queue references are checked. |
| Recovery stages | Actual 3-user hold, reduction to 1, recovery hold, reduction to zero and idle; native occupancy includes zero. Visit counters for surviving VUs continue across the reduction. |
| Metric search | Workload emits actual VU IDs. Maximum ID criterion deterministically produces passing 2/failing 3 with four unique real probes. |
| Budget stop | Finite auto search stops after one launch with explicit trial_budget reason. |
| Queue concurrency | Independent CLI processes append, replace and remove pending jobs while a controller performs actual load. Stop cleans resources; explicit resume restarts interrupted work. A hard-killed controller is reconciled before new work. |
| Evidence durability | Retained manifest/result/interrupted file hashes survive crash reconciliation. Only one valid result per requested fixed population remains; removed job never runs. |
| Ownership | Queue leases are empty after completion; owned containers/networks are absent. A foreign-labelled sentinel survives normal cleanup. Failure finalizers inspect ownership again before removing immutable container/network IDs, and preserve the original failure if cleanup also fails. Separate real lease tests cover alias contention and restoration. |
| Linux SSH | Actual loopback daemon, private generated key, strict host-key checking, paths containing spaces, remote target/workload staging, collectors and cleanup. A lost response after a successful remote Docker start is injected at the transport boundary. |
| Public quick start | A fresh tracked-file snapshot and new virtual environment run uv sync, init, validate, doctor, start, status and report with no AWS credentials. The README's documented two-user edit is the only workload adjustment. Legacy PYTHONPATH=tools uv run -m bench.cli --help remains callable. |
| Metrics/reporting | Real reports generated from accepted immutable evidence; unit regressions cover missing metrics, scope/units, weighted reductions, quantiles, throughput availability and observed-only reuse. |

## Limits

The loopback SSH test uses one Docker daemon through SSH and does not establish operation across independent machines, unsynchronized clocks or a production network. The service test exercises a real user-manager stop/resume, not a host reboot or power loss. Baseline gating stays disabled on this shared host; unit tests cover its rejection and cancellation behavior. These small populations test correctness, not generator capacity or target capacity. Target environment values remain single-line; CR, LF and NUL are rejected.

No AWS/RDS calls or live CloudWatch evidence are claimed. The disposable cached Go/PostgreSQL social fixture-restoration test is included in the final acceptance command; this does not claim Python social runtime deployment. Required missing evidence remains invalid; reports do not fill gaps with zero. Existing historical capacity measurements and artifacts are unchanged.
