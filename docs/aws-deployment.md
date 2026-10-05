# Live AWS benchmark deployment

Provisioned on 2026-10-04 in **ap-southeast-1c (Singapore)** using the existing VPC with dedicated benchmark security groups. Existing unrelated EC2/RDS resources were not modified.

| Role | Instance | Size |
| --- | --- | --- |
| Backend | i-045a47c800445e2a1 | c7i.large, 2 vCPUs / 4 GiB |
| Load generator | i-0169b9bccdf00ed65 | c7i.8xlarge, 32 vCPUs / 64 GiB |
| Dashboard/orchestrator | i-022a1b193ba5fb794 | c7i.large, 2 vCPUs / 4 GiB |
| Database | language-benchmark | RDS PostgreSQL 17.6, db.m7i.xlarge, encrypted gp3 100 GiB, private endpoint |

All hosts use Rocky Linux 10.2, the updated kernel, Docker CE **29.8.2**, and Compose **5.6.0**. EC2 quota was raised from 32 to 64 vCPUs to accommodate the dedicated monitoring host. Host versions are recorded in `/var/lib/language-benchmark/versions.txt`. SELinux remains enabled; Compose mounts use appropriate labels.

RDS contains the canonical 50,000-user / 500,000-post dataset plus 2.5 million follows, 1 million likes and 250,000 comments. Applications use a separate limited database role; admin credentials and fixture operations remain outside application containers. Backup retention is zero and minor upgrades are disabled for this disposable controlled benchmark.

## Dashboard

Open **http://127.0.0.1:3000/d/language-benchmark** while the SSH tunnel is active. It provides live operation rates, latency quantiles, failures/dropped arrivals, host CPU/memory, RDS CPU/connections, and validated historical trial summaries. Filter by runtime cell and trial. Read-only viewing requires only the authenticated SSH tunnel; Grafana admin credentials are private in `artifacts/aws/secrets.json`.

Reconnect the tunnel when needed:

```sh
ssh -F artifacts/aws/ssh-config -N -L 127.0.0.1:3000:127.0.0.1:3000 benchmark-monitor
```

The dashboard is not exposed publicly. Prometheus receives k6 data over private VPC addresses and retains history for 90 days. Raw trial artifacts remain the source of truth. A backfill service rechecks delayed CloudWatch data before historical qualification, without hiding failed load or cleanup evidence.

## Current experiment

The primary campaign now measures concurrent users performing stateful journeys with random 3–10 second waits. It starts at 10,000 users, adds 10,000 until failure, then bisects the passing/failing bracket to a 100-user interval and retests the final failing endpoint once under the faster policy. See [the methodology](user-capacity-methodology.md). All 13 runtime configurations use the same settings; invalid generator or telemetry evidence stops the campaign for attention.

The earlier arrival-rate pilot and its failed `language-benchmark-full.service` remain separate evidence. They cannot establish a concurrent-user capacity. The new controller is `language-benchmark-user-capacity.service`, with status/results in `artifacts/results/user-capacity/`. Valid but unstable boundaries are reported and the remaining configurations continue; invalid evidence still pauses the campaign.

Runtime depends on the number of search probes and repetitions and can span multiple days. The three EC2 machines total approximately **USD 1.85/hour**, and the RDS compute rate is **USD 0.494/hour** in the checked AWS on-demand catalog. Combined compute is about **USD 2.35/hour**, plus EBS/RDS storage and public IPv4 charges. These are estimates at provisioning time, not a billing guarantee.

Check progress:

```sh
ssh -F artifacts/aws/ssh-config benchmark-monitor \
  'cat /home/rocky/language-benchmark/artifacts/results/user-capacity/capacity-status.json; systemctl is-active language-benchmark-user-capacity'
```

Results live on the monitoring host under `/home/rocky/language-benchmark/artifacts/results/`. Concurrent-user brackets are written to `artifacts/results/user-capacity/capacity-results.json` there. The hourly reminder in this chat notifies on completion, failure, or prolonged idle resources; it does not stop machines automatically.

## Stop to save money

First interrupt the controller gracefully and confirm the backend/load services are stopped and the fixture is restored. To interrupt an active campaign:

```sh
ssh -F artifacts/aws/ssh-config benchmark-monitor \
  'sudo systemctl stop language-benchmark-supervisor; sudo systemctl stop language-benchmark-user-capacity'
```

A hard host failure can require the documented `bench.cli recover` operation. After verified cleanup, stop the tagged resources from this checkout:

```sh
uv run python infrastructure/aws/stop.py
```

This preserves disk/database data. **Storage charges continue while stopped**, and **RDS automatically restarts after seven days**. Later deletion requires a separate decision about preserving results/snapshots. The existing unrelated `rds-eb` database is not part of this deployment or shutdown script.

Private deployment identifiers, SSH configuration/key and credentials are under ignored `artifacts/aws/`. Do not share that directory wholesale; exported benchmark manifests/reports redact credentials.

## Generator reliability

The generator was enlarged to c7i.8xlarge after the first concurrent-user trial exhausted source ports and saturated generator CPU. It uses four VPC source addresses, a 1024–65535 ephemeral port range, TCP TIME_WAIT reuse and a 250,000-file-descriptor container limit, following Grafana’s [large-test guidance](https://grafana.com/docs/k6/latest/testing-guides/running-large-tests/). The backend remains c7i.large with Docker enforcing 2 CPUs and 3 GiB memory. No backend CPU allocation changed.

k6 records socket/resource failures separately from request timeouts. Warning-per-request logs are suppressed to prevent an overload log flood; raw counters retain failure evidence. Backend log collection retains the last 2,000 lines plus container exit/OOM and CPU/memory limits. The invalid initial campaign remains in `artifacts/results/user-capacity-invalid-20261004/` after the replacement starts.

## Automatic controller supervision

`language-benchmark-supervisor.service` runs on the monitoring machine and watches `language-benchmark-user-capacity.service`. It adopts an existing controller without interrupting its trial. If that controller crashes or the old controller stops with invalid evidence, the supervisor starts the updated controller with `--resume --retry-invalid`. Supervisor state and its persisted three-start recovery budget are in `artifacts/aws/supervisor-status.json`; controller output remains in `artifacts/aws/user-capacity.log`.

The updated runner retries an invalid probe up to three times, each in its own preserved trial directory. It starts an inactive Docker daemon when reachable, stops the old backend/load containers, restores the canonical fixture, and redeploys before each attempt. Expected overload failures and explicit interruptions are not retried. Generator/telemetry failures never move capacity bounds. Persistent invalid evidence exhausts the budget and requires attention instead of looping indefinitely.

Valid completed probe artifacts are replayed after a controller restart, including an unfinished runtime's search and confirmation probes. Invalid or unrestored trials are excluded. Resume rejects changes to workload, fixture, runtime ordering, response deadlines and confirmation settings.

With `auto_build_images` enabled, missing locked Python images are built on the load generator before the timed window, transferred to the backend, and checked against the build host's image ID. Existing images are reused. Runtime/dependency checks still run before load starts, and Docker limits must remain exactly 2 CPUs, 3 GiB memory and no swap. No build runs on the measured backend.

Both services are configured on the monitor, and the supervisor starts on reboot. It exits when the campaign is complete and never stops, deletes, resizes, or starts AWS instances or RDS. Check both services and supervisor state when diagnosing progress:

```sh
ssh -F artifacts/aws/ssh-config benchmark-monitor \
  'systemctl status language-benchmark-supervisor language-benchmark-user-capacity --no-pager; cat /home/rocky/language-benchmark/artifacts/aws/supervisor-status.json'
```

Before shutting down AWS, stop supervision first, then gracefully stop the controller so recovery cannot restart it:

```sh
ssh -F artifacts/aws/ssh-config benchmark-monitor \
  'sudo systemctl stop language-benchmark-supervisor; sudo systemctl stop language-benchmark-user-capacity'
```

Verify the last trial's `reset_verified` and that backend/load containers are stopped, then use the existing checked AWS shutdown command. To retry after repairing an exhausted recovery budget, stop the supervisor, remove only `artifacts/aws/supervisor-status.json` on the monitor, and start the supervisor again. Keep trial artifacts and capacity results.
