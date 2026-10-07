# Reusable engine verification

This is a functional acceptance record for the version-1 engine, not a capacity measurement. Verification uses disposable local Docker resources, an unprivileged loopback OpenSSH daemon and temporary queue state. No AWS experiment or database operation is part of this acceptance run.

## Platform

Observed on 2026-10-07: Linux 7.2.9-1-cachyos, Python 3.14.8, uv 0.12.23, Docker Engine 29.8.2, systemd 262 and pinned grafana/k6:1.4.2. Docker and SSH tests run serially because the engine deliberately reserves each daemon for one benchmark owner.

## Repeatable commands

```sh
uv run pytest tests/unit -q
uvx ruff check tools tests
BENCH_INTEGRATION=1 uv run pytest tests/integration/test_engine_acceptance.py -q
BENCH_INTEGRATION=1 uv run pytest tests/integration/test_queue_leases.py -q
```

The opt-in suite needs Docker, cached/buildable HTTP example and k6 images, and OpenSSH server/client binaries. The service test additionally needs an available user bus and already-enabled lingering; it does not enable lingering. Its new unit name is derived from a unique queue and temporary state directory. It removes that unit after execution and never stops or changes an existing unrelated service.

## Observed outcomes

- Final unit suite: **507 passed, 6 opt-in skips**.
- Core acceptance (versions/stages/search, process recovery, SSH, fresh README installation): **4 passed in 239.27 seconds**.
- Additional real disposable user-service lifecycle plus Docker lease integration: **4 passed** (one service test and three lease tests). All five acceptance tests were therefore exercised; the service test was added after the four-case run.
- Review follow-up: the strengthened exact-version/evidence and subprocess recovery cases passed again (**2 passed in174.24 seconds**); a new actual owned-resource failure-cleanup regression also passed (**1 passed**).
- Full `uvx ruff check tools tests` and `git diff --check`: passed.
- The 54-test focused workload/CLI/service run also passed, including the real systemd parser for ordinary and space/percent paths.

The real user service was stopped during load with systemd, cleaned up, remained enabled without a durable user-stop flag, resumed explicitly and completed with one valid result. Completion disabled its unit; final cleanup removed its file and enablement. No host reboot was attempted.

Acceptance found a harmless `new.target` expression was incorrectly rejected as a meta import. A failing regression reproduced it; the parser now accepts it while retaining an explicit `import.meta` rejection test. The unquoted YAML `off` regression now asserts the generated quoted form before replacement. The broad lint failure was confined to the new engine/test files and was fixed by safe import cleanup, statement formatting and removing one unused test local.

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

The loopback SSH test uses one Docker daemon through SSH and does not establish operation across independent machines, unsynchronized clocks or a production network. The service test exercises a real user-manager stop/resume, not a host reboot or power loss. Baseline gating stays disabled on this shared host; unit tests cover its rejection and cancellation behavior. These small populations test correctness, not generator capacity or target capacity.

No AWS/RDS calls or live CloudWatch evidence are claimed. The earlier disposable cached Go/PostgreSQL social smoke and fixture-restoration tests remain the social integration evidence; this acceptance does not repeat those database runs or claim Python social runtime deployment. Required missing evidence remains invalid; reports do not fill gaps with zero. Existing historical capacity measurements and artifacts are unchanged.
