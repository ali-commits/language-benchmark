# AWS runbook

## Topology

Provision a dedicated VPC/availability-zone campaign environment:

| Role | Starting size | Purpose |
| --- | --- | --- |
| Backend | c7i.large, 2 vCPUs / 4 GiB | One active backend, two-vCPU / 3-GiB container budget |
| Generator | c7i.4xlarge, 16 vCPUs / 32 GiB | k6, enough headroom to avoid client saturation |
| Database | RDS PostgreSQL, initially db.m7i.xlarge | Fixed engine patch, gp3 capacity/IOPS/throughput and parameter group |

Verify regional availability. Use non-burstable classes, fixed x86 architecture for backend comparisons, private addresses, and the same AZ. Keep the backend VM unchanged during a comparison block; record CPU model/topology and any replacement. A vCPU is not necessarily an independent physical core.

Security groups: backend port 8000 accessible from generator group only; RDS port 5432 accessible from backend/admin groups only; administration through SSM or narrowly scoped SSH. Do not expose RDS publicly. Install Docker/Compose, uv and appropriate PostgreSQL clients on the orchestration/backend host. The generator needs Docker/Compose, Python 3 for transport/telemetry, and a clone of this repository. Build images outside the constrained VM or temporarily use a larger builder; transfer immutable built images to the backend. Capture digests.

Download the official AWS RDS regional/global CA bundle and verify its source. Set host `DATABASE_URL` with `sslmode=verify-full` and a host-readable `sslrootcert` path. If the app uses a mounted certificate at `/certs/rds-ca.pem`, set `backend_database_url` in the private campaign config to that path; `RDS_CA_FILE` names the host file to mount. Keep DSNs, JWT secrets, seed passwords and credentials out of git and report files.

Run the orchestrator on the backend/admin host with private connectivity. Configure an SSH alias such as `benchmark-load` for the generator. `backend_host` can be local; `load_host.project` must be the absolute generator checkout path. SSH commands transfer environment via encrypted stdin and load tokens via scp; no SSH passwords are placed in argv. Use an existing key/SSM-supported access path. Host aliases must be trusted configuration.

## Build and preflight

```sh
uv sync --locked
uv run python runtimes/python/build.py --apps
uv run python runtimes/python/build.py --version 3.15.0rc3 --mode jit --apps
uv run python runtimes/python/build.py --version 3.15.0rc3 --mode free-threaded --apps
```

These are source builds with PGO/LTO and checksum-pinned Python. uv's vendor interpreters can be used for local contract diagnostics but are not substitutes for this measured matrix. Runtime mode is verified after all imports in every worker. Unsafe extensions that re-enable the GIL cause preflight failure; never force the GIL off to conceal incompatibility.

Aggregate app pool budget is 20 connections, no overflow: ten per two-worker process or twenty for a single worker. The threaded adapter caps AnyIO threads at 16. Keep statement/pool/server timeouts, parser (`h11`), loop (`asyncio`), logging policy and telemetry identical across cells. Do not enable uvloop for just one variant.

## Canonical fixture

Use a dedicated empty database whose name begins `language_benchmark`. Export host credentials privately and set `PYTHONPATH=tools`; unset `DB_CLIENT_CONTAINER` for RDS.

```sh
uv run -m bench.cli migrate
uv run -m bench.cli mark --database language_benchmark
uv run -m bench.cli seed
uv run -m bench.cli capture --output artifacts/full-fixture
uv run -m bench.cli credentials --count 200 --output artifacts/credentials.json
```

Seed once, validate exact counts, and preserve the dump plus manifest. The explicit `mark` command establishes the reset identity; it is never implicitly applied by `restore`. Store artifacts on encrypted storage with restricted access. Allow room for trial raw metrics and fixture files. RDS backups/maintenance are recorded; avoid maintenance windows during the campaign.

## Calibrate k6, then select common rates

On the generator, run `benchmarks/k6/calibrate.js` against an isolated fast target, not the two-vCPU backend. Use a known high-capacity endpoint or several targets to ensure the target itself is not the calibration bottleneck. Set `CALIBRATION_URL`, choose an offered `RATE` above the planned backend test rates, and monitor per-core CPU, network, paging, sockets and dropped iterations.

The pinned k6 image is defined in `compose.load.yaml`. Its calibration command can be run directly with the same image and `/scripts` mount. Increase generator size or shard actors/workload seeds if CPU stays above 60%, network limits are approached, or scheduling cannot keep up. Rerun the affected full comparison using the new fixed generator configuration. Preallocate VUs before timing; do not initialize more VUs during measurement.

Copy `benchmarks/campaign.example.json` to a private configuration. Set fixture/credential paths, region/RDS identifier, generator SSH/project, backend private IP, TLS DSNs and bind IP. Pilot starts at 25 ops/s, doubles to a finite `max_rate`, and is explicitly exploratory smoke data:

```sh
uv run -m bench.cli pilot --config artifacts/campaign.json --output artifacts/pilot
```

Pick one common rate grid from the pilot and run it across all comparable cells. Five repeats and 120/300-second warm-up/measurement are enforced unless a campaign is explicitly marked smoke. RDS telemetry requires CloudWatch read and RDS instance/event read permissions; no AWS credentials are shipped in the repository.

```sh
uv run -m bench.cli run --config artifacts/campaign.json --output artifacts/aws-campaign
uv run -m bench.cli report --results artifacts/aws-campaign --output artifacts/aws-report.html
```

The runner stops generator/backend, restores and verifies data, starts the selected topology, verifies every worker, obtains real tokens, warms reads, measures, preserves raw metrics and telemetry, and finally restores again. An interrupted or failed restore requires recovery before another run. Check CloudWatch publication delay: missing points must be retrieved before qualifying AWS results.

## Recovery and cleanup

Stop `compose.load.yaml` and `compose.aws.yaml` services on their respective hosts first. Inspect recorded failure reasons and database identity, fix the cause, then:

```sh
uv run -m bench.cli recover --fixture artifacts/full-fixture/manifest.json --database language_benchmark
```

Verify fingerprints, session absence and service state before restarting a campaign. A host crash can leave a remote generator running; stop it explicitly. Retain completed and partial artifacts. The runner holds both a local host lock and a PostgreSQL advisory campaign lock, so another cooperating orchestrator cannot run against the same database concurrently.

After collecting evidence, remove transient token files, stop containers, and terminate dedicated EC2/RDS resources through the AWS account workflow. Preserve encrypted canonical fixtures/results if required. The repository never provisions or destroys cloud resources automatically.
