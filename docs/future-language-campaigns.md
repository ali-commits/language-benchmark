# Future mixed-language campaigns

The staged examples compare the modern Python 3.14.8 reference with Java/Spring Boot, PHP/Laravel, Go/net-http, Rust/Axum, Node/Express and Bun/Express. They do not modify or extend the currently running AWS queue. Use a fresh private configuration and output directory for changed timing, thresholds, experiment type, populations, images or runtime locks.

- `benchmarks/user-capacity.example.json`: fast capacity search, 180/0/240 seconds, 90% valid steady responses within five seconds and an explicitly recorded authentication gate.
- `benchmarks/fixed-load.example.json`: the same profile at declared populations, `[100, 500, 1000]` for the next campaign; no search or endpoint confirmation.
- `benchmarks/user-capacity.legacy.example.json`: explicit historical 300/120/300 timing and 99% targets. This template does not recover or reinterpret an existing checkpoint.

The stable runtime reference is `python-modern-3.14.8-standard-jit0-gil1-async`, image `lb-python-modern:3.14.8-standard`, with two async worker processes and a 20-connection aggregate pool. It retains the existing modern FastAPI and Pydantic beta dependency lock. Python 3.15 experimental controls remain available in their original registry and are not relabeled stable.

## Prepare privately

1. Build the locked Python reference and all selected native images outside the measured backend. Python source flags remain PGO/LTO, JIT off, GIL on. Capture actual image IDs and correctness evidence.
2. Provide the canonical fixture manifest/dump and unique seeded credentials. Use the same captured fixture for every runtime. Set the dedicated database allowlist and admin `DATABASE_URL` environment for restoration; supply the restricted backend database URL separately.
3. Configure backend, generator and build hosts with SSH aliases/private project paths. Backend HTTP and generator endpoints must agree on port 8000 and private bind address. The build host must be separate from the measured backend. Do not put credentials into checked-in examples.
4. For AWS, mount the trusted RDS CA read-only at `/certs/rds-ca.pem`, use `sslmode=verify-full&sslrootcert=/certs/rds-ca.pem` in the backend URL, and provide a host CA path for the controller's separate admin connection. Nonroot backend users must be able to read the certificate.
5. Populate `expected_image_ids` for each native cell with the immutable image that passed verification. Preload those images on the generator/backend. A newly rebuilt image with a different ID requires fresh verification; its tag alone is insufficient. Python's worker attestations validate release, compiler flags, dependencies and actual GIL/JIT state.
6. Preserve the exact backend budget: two CPUs, 3 GiB, no swap, aggregate connections 20 and nofile 250000. Calibrate the larger generator for the intended users using the same journey script. Backend open-file errors and constrained generator evidence invalidate capacity observations.

A mixed queue uses the generic controller, because `bench.native_campaign` deliberately accepts native cells only:

```sh
PYTHONPATH=tools uv run -m bench.cli user-capacity \
  --config artifacts/aws/future-capacity.json --output artifacts/results/future-capacity
PYTHONPATH=tools uv run -m bench.cli user-capacity \
  --config artifacts/aws/future-fixed-load.json --output artifacts/results/future-fixed-load
```

Set up its supervisor with those new paths if continuous recovery is wanted; do not repoint a running campaign's service/config. Configured durations are scheduled load time: fixture restoration, deployment/readiness, drain and telemetry make the seven-minute profile take longer in practice. Restarts replay valid evidence only when methodology and binary/registry provenance match.

Analyze saved results independently of execution:

```sh
PYTHONPATH=tools uv run -m bench.cli analyze \
  --input artifacts/results/future-capacity --output artifacts/analysis/future-capacity.json \
  --threshold 0.95
```

A threshold replay reports observed bounds and gaps; a 90%-guided search does not find exact higher-threshold capacity. Fixed-load samples compare responsiveness, attempted/successful throughput, latency including failures and phase-separated resources at the declared populations. See [methodology](user-capacity-methodology.md) for classification and historical-comparability limits.

This task stages code and local verification only. Starting these campaigns on AWS requires a separate fresh configuration/run. Never automatically stop or delete EC2/RDS; use the established completion reminder and [checked shutdown procedure](aws-deployment.md#stop-to-save-money).

The planned next AWS campaign is fixed-load comparisons at100,500 and1000 users for each of the seven stacks. After reviewing the existing results, Ali explicitly authorized starting this fixed-load suite on2026-10-06. The earlier hold is revoked for this suite only. The90% capacity search remains a separate later campaign and must not start automatically. The old native queue is complete; use the persistent fixed services and new output directory described in the AWS deployment guide.

The fixed-load suite completed21 trials. Ali subsequently authorized the fast90% capacity campaign on2026-10-06; its fresh config/output and persistent service checks are described in the AWS deployment guide. This authorization covers that campaign only, not any follow-up experiments.
