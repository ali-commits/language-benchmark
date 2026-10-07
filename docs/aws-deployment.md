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

## Native campaign automation

After Python completes, the six native stacks run sequentially under language-benchmark-native-capacity.service and language-benchmark-native-supervisor.service. Configuration is private artifacts/aws/native-capacity.json; status/results are artifacts/results/native-capacity/capacity-status.json and capacity-results.json. Native supervisor state is artifacts/aws/native-supervisor-status.json and logs are artifacts/aws/native-capacity.log. Python results stay in their original folder.

The first language rechecks1309users and the1409failure hint from Python; later stacks start from measured prior-language results. Geometric growth avoids repeated small steps across unfamiliar capacity ranges, followed by width100 binary search and one failing-endpoint retest. All six verified images are preloaded on the backend and generator so execution does not depend on the PC. An equivalent-looking rebuild with a different image ID cannot silently reuse old measurements. Native model preflight enforces exact image/platform/locked labels, a fresh container nonce,2CPU/3GiB/no-swap, read-only CA and database readiness.

The supervisor starts on monitor reboot, recovers crashed controllers from valid checkpoints, retries invalid probes at most three times and advances through all six cells. Persistent invalid evidence requires attention rather than fabricating capacity. No AWS resources are stopped automatically.

Check progress:

    ssh -F artifacts/aws/ssh-config benchmark-monitor 'systemctl status language-benchmark-native-supervisor language-benchmark-native-capacity --no-pager; cat /home/rocky/language-benchmark/artifacts/results/native-capacity/capacity-status.json'

For graceful shutdown, stop the native supervisor FIRST, then stop the native controller and verify cleanup before using the checked AWS stop command. Stopping the supervisor prevents recovery from restarting an intentionally stopped controller.

## Fixed-load comparison campaign (2026-10-06)

The separate mixed-language services are `language-benchmark-fixed-load` (controller) and `language-benchmark-fixed-supervisor` (bounded recovery, enabled on monitor reboot). They use the private monitor configuration `artifacts/aws/fixed-load.json`, status/results in `artifacts/results/fixed-load-20261006`, and `artifacts/aws/fixed-supervisor-status.json`. Old campaign artifacts are preserved; old supervisors are disabled. The all-results RDS backfill job was stopped to prevent concurrent writes into new trial evidence.

Queue: Python3.14.8 modern FastAPI, Java/Spring Boot, PHP/Laravel, Go/net-http, Rust/Axum, Node/Express, Bun/Express. Each stack runs100,500,1000 distinct logged-in users, once per population:21 trials, plus bounded retries only for invalid infrastructure evidence. Timings180s ramp,0 settling,240s measurement; setup/restoration/drain add wall time. Both operation and authentication targets are0.90, reported separately. Valid below-target results are recorded and the fixed-load queue proceeds. No capacity search or follow-up campaign is started.

The backend retains2vCPUs/3GiB/no-swap/pool20 and an explicitly verified250000 soft/hard file limit. Python's source-tested image was transferred and verified by ordered filesystem and configuration equality despite Docker config-ID/OCI manifest-ID differences; private proof is `artifacts/aws/fixed-python-image-proof.json`. All six native image identities remain pinned.

Read status without printing private configurations:

```sh
ssh -F artifacts/aws/ssh-config benchmark-monitor \
  'cat /home/rocky/language-benchmark/artifacts/results/fixed-load-20261006/capacity-status.json; cat /home/rocky/language-benchmark/artifacts/aws/fixed-supervisor-status.json'
```

To stop this suite gracefully, stop the supervisor first so it cannot restart the controller:

```sh
ssh -F artifacts/aws/ssh-config benchmark-monitor \
  'sudo systemctl stop language-benchmark-fixed-supervisor; sudo systemctl stop language-benchmark-fixed-load'
```

Then inspect the latest result for verified fixture restoration and confirm backend/load test containers are stopped before using the checked EC2/RDS shutdown commands above. Never stop unrelated resources. The dashboard's history exporter mounts current tool modules and uses finalized saved reductions, avoiding raw-gzip parsing during live scrapes; full canonical offline reports still read raw evidence by default.

## Automatic idle shutdown authorization (2026-10-06)

Ali replaced the hourly notification-only heartbeat with `stop-idle-benchmark-machines`, a12-hour check explicitly authorized to stop the three dedicated benchmark EC2 instances and RDS `language-benchmark` only when no test, preparation or recovery is active and fixture cleanup is verified. This supersedes earlier reminder-only instructions. Uncertain ownership/activity/cleanup must prevent shutdown. Stop applicable supervisors before residual jobs; stop backend/generator/RDS and the monitor last. Never terminate/delete resources or touch unrelated instances or `rds-eb`. Keep the check scheduled to stop RDS again after its automatic seven-day restart; stopped storage remains billed. No capacity search or new suite is authorized by this automation.

## Fast 90% capacity campaign (2026-10-06)

After the fixed-load review, Ali explicitly authorized the next seven-stack capacity search. Its separate services are `language-benchmark-fast-capacity` and `language-benchmark-fast-supervisor`; private configuration is `artifacts/aws/fast-capacity.json`, output is `artifacts/results/capacity90-20261006`, and supervisor state is `artifacts/aws/fast-supervisor-status.json`. Old results are preserved and never replayed as new 90% evidence.

All six stacks that passed the fixed1,000-user trial start by measuring1,000 users again. PHP starts at100 and measures500 as a failing hint. Explicit per-cell hints override prior-stack seeds. From each measured starting point, geometric expansion finds a failure, then binary search stops at a bracket no wider than100 users. No additional passing repeats; the final failing count is repeated once. Contradictory valid retests remain flagged while later stacks continue. Infrastructure-invalid probes use bounded retries and never define capacity.

Each trial uses180s ramp,0s settling,240s steady measurement, random3-10-second waits and real logins. Both operation/authentication targets are0.90; successful operations require the expected valid response within5 seconds. Fixture restoration, readiness, drain and telemetry add wall time. Backend2vCPU/3GiB/no-swap, pool20 and verified nofile250000 remain unchanged. The current source-tested images are preloaded; missing/unverified images are not rebuilt automatically.

Read status:

```sh
ssh -F artifacts/aws/ssh-config benchmark-monitor \
  'cat /home/rocky/language-benchmark/artifacts/results/capacity90-20261006/capacity-status.json; cat /home/rocky/language-benchmark/artifacts/aws/fast-supervisor-status.json'
```

To stop gracefully, stop the supervisor first:

```sh
ssh -F artifacts/aws/ssh-config benchmark-monitor \
  'sudo systemctl stop language-benchmark-fast-supervisor; sudo systemctl stop language-benchmark-fast-capacity'
```

Confirm fixture restoration and absence of backend/k6 test containers before machine shutdown. The12-hour idle automation now follows these active service paths and must leave machines running during preparation/testing/recovery. No further suite starts automatically after this campaign.

## Reset to five-minute ramp / two-minute sustained capacity profile

Ali changed the next capacity profile to300s ramp,0s separate settling,120s sustained measurement (420 timed seconds total), and explicitly requested a clean reset. The former180/0/240 campaign was stopped and its partial trial preserved as user-interrupted. Remaining k6/API containers were removed and the canonical database restore passed full fingerprint verification. Only OS/Docker and host telemetry remain on the backend before startup.

The fresh private configuration is `artifacts/aws/capacity-ramp5.json`; evidence is isolated under `artifacts/results/capacity90-ramp5-20261006`. Services are `language-benchmark-capacity-ramp5` and `language-benchmark-ramp5-supervisor`, with supervisor state `artifacts/aws/ramp5-supervisor-status.json`. Seven stacks,90% operation/authentication gates,100-user resolution, geometric expansion, one final failing repeat and per-cell1000/PHP100–500 hints remain unchanged and are remeasured.

Signal-handling correction: the previous `uv run` process-group route delivered two SIGINTs, which could interrupt cleanup. New units prepare dependencies with `uv sync --frozen`, then exec the managed `.venv/bin/python` directly. `KillMode=mixed` initially signals only the main Python process; cleanup helpers survive until completion or the600-second forced-stop deadline. This was tested with a real isolated local systemd stop and `trial_lifecycle` cleanup markers.

To stop gracefully:

```sh
ssh -F artifacts/aws/ssh-config benchmark-monitor \
  'sudo systemctl stop language-benchmark-ramp5-supervisor; sudo systemctl stop language-benchmark-capacity-ramp5'
```

Always verify final restoration and absence of test containers before resource shutdown. The12-hour idle automation follows the new deployment metadata; no further campaign starts automatically.

## Majority decisions and clean baselines (2026-10-07)

Ali authorized resuming the existing five-minute-ramp campaign with a decision-only policy change. A final failure is repeated once; if the two valid results disagree, run one third valid trial at that same user count and decide by two of three. Two agreeing results need no third. A passing majority raises the measured lower bound and continues searching; raw results and metrics remain separate. Infrastructure-invalid runs do not vote. Python's completed two-failure boundary is retained; Java's saved split is resolved before searching further. Audited handoff receipts preserve the original timing, images, resource budgets and measurement configuration. A separate vote ledger links each decision to immutable trial IDs.

Before each new trial, the runner requires a stopped backend with no leftover test containers or jobs, three low-CPU samples (at most15% with at least1024MiB available RAM), then a ready API with three samples at most20% CPU and at least512MiB available RAM. Samples are five seconds apart, with a120-second deadline. Only host telemetry and the current verified API are allowed. The stopped/ready baseline artifacts record evidence; failure is an infrastructure-invalid probe, not a language capacity failure. No cache purge or unrelated-job termination is performed.

Cleanup defers further SIGINT/SIGTERM while stopping backend/load processes, restoring the fixture and saving the interrupted result. C#/ASP.NET Core is appended to this same authorized queue only after locked-image, API, TLS and resource proofs; it is not permission to launch a different campaign. The same persistent supervisor resumes checkpoints independently of the desktop.

## .NET fixed loads and Laravel Octane queue (2026-10-07)

Ali authorized three additional suites after the eight-stack capacity campaign completed. Historical outputs/registry locks remain intact. .NET fixed100/500/1000 runs first, independently supervised by `language-benchmark-dotnet-fixed-supervisor` and controller `language-benchmark-dotnet-fixed`, with config `artifacts/aws/dotnet-fixed-20261007.json`, results `artifacts/results/dotnet-fixed-20261007` and supervisor state `artifacts/aws/dotnet-fixed-supervisor-status.json`.

The monitor's `language-benchmark-octane-dotnet-queue` uses `bench.pipeline`, private queue `artifacts/aws/octane-dotnet-queue.json` and state `artifacts/aws/octane-dotnet-queue-status.json`. It observes .NET's supervisor without competing with it, requires qualified completion and cleanup, then supervises `language-benchmark-octane-capacity` and `language-benchmark-octane-fixed` sequentially. Their configs/results are `artifacts/aws/octane-{capacity,fixed}-20261007.json` and `artifacts/results/octane-{capacity,fixed}-20261007`, with separate bounded supervisor states `artifacts/aws/octane-{capacity,fixed}-supervisor-status.json`. This queue starts only after Octane image/API/persistence/TLS/resource verification and preload. Pipeline config and stage bytes are pinned; original manifests/registry identities and actual file-limit attestations are verified before any stage is accepted as complete.

Fixed suites match the earlier180-second ramp/240-second steady comparison at100,500 and1000 users. Octane capacity uses300-second ramp/120-second steady,90% operation/authentication gates and majority-on-disagreement. The original Laravel/FPM and Octane have distinct cell IDs; Octane13.35.0 versus FPM13.34.0 is an explicitly recorded framework patch difference. Budget, fixture resets, user journeys and clean-VM baseline gates remain the same. Invalid evidence or unverified cleanup halts advancement; valid below-target fixed trials are preserved and the suite continues. The queue operates independently of the desktop and resumes checkpoints on monitor reboot. No suite beyond these three is authorized.

For graceful shutdown, stop/disable `language-benchmark-octane-dotnet-queue` first, then the .NET supervisor, then any active .NET/Octane controller. Verify fixture restoration and absent API/k6 containers before the scoped resource shutdown procedure. The12-hour idle automation follows these metadata paths and must not stop resources while implementation/deployment preparation, a controller or the queue is active.
