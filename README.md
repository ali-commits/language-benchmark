# Language benchmark

Run reusable HTTP workloads against Linux Docker targets, locally or over SSH. The engine supports **fixed-load** tests (explicit populations, sweeps or absolute stage sequences) and **search** tests (finite-budget observed user bounds for separate objectives). The social workload and nine runtime implementations remain available with their original runtime and database safeguards.

Install Python 3.14 and [uv](https://docs.astral.sh/uv/), and make Docker available to your user. From this checkout:

```sh
uv sync
uv run bench init --template http --output /tmp/bench-demo/queue.yaml
uv run bench validate /tmp/bench-demo/queue.yaml
uv run bench doctor /tmp/bench-demo/queue.yaml
```

The template is self-contained and builds a standard-library HTTP target. It declares fixed 100/500/1000 users and finite auto-search objectives. For a short functional smoke, edit the generated configuration to retain only `http-fixed` and set `test.users: [2]`. This is not capacity evidence. Keep queue state outside the target build context.

```sh
uv run bench queue start /tmp/bench-demo/queue.yaml --state-dir /tmp/bench-demo-state
uv run bench queue status http-demo --state-dir /tmp/bench-demo-state
uv run bench report http-demo --state-dir /tmp/bench-demo-state --output /tmp/bench-demo-report
```

Start runs serially in the foreground by default; append alone does not run a queue. Ctrl-C persists stop and allows cleanup. Use explicit `queue resume` to continue a stopped or recoverable interrupted queue. Optional `--detach` requires a configured user-systemd session and lingering; secrets needed after reboot require an explicit owned 0600 environment file.

Reports contain selected-window and per-stage metrics, objective decisions/bounds and local inline charts. Missing required evidence invalidates a trial; it is never a zero or an application failure. Reports exclude private configuration and load logs.

Read [configuration](docs/configuration.md), [queues](docs/queues.md), [custom workloads](docs/custom-workloads.md), [environments](docs/environments.md) and [metrics](docs/metrics.md). The [October 2026 measured results](docs/results/2026-10/README.md) publish sanitized fixed/stress tables and immutable PDF copies. Those historical campaigns used their own timing and resource methodology; they do not establish performance of this reusable engine.

```sh
uv run pytest tests/unit -q
```

Docker integration is opt-in with `BENCH_INTEGRATION=1`. Full remote deployment, RDS telemetry and actual reboot persistence are not verified by local unit tests. Legacy AWS runbooks are historical operational records, not the current portable quick start.
