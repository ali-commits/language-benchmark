# Live AWS benchmark deployment

Provisioned on 2026-10-04 in **ap-southeast-1c (Singapore)** using the existing VPC with dedicated benchmark security groups. Existing unrelated EC2/RDS resources were not modified.

| Role | Instance | Size |
| --- | --- | --- |
| Backend | i-045a47c800445e2a1 | c7i.large, 2 vCPUs / 4 GiB |
| Load generator | i-0169b9bccdf00ed65 | c7i.4xlarge, 16 vCPUs / 32 GiB |
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

The AWS smoke workflow succeeded and verified its final database restoration. k6 calibration delivered approximately **5,000 requests/s**, zero dropped iterations, against an independent fixed-response target. This establishes generator headroom for the initial load grid, not application throughput.

The pilot runs first. `language-benchmark-full.service` waits for it, requires a qualified rate for every baseline interpreter, picks a common grid, and then runs all 13 configurations with five randomized repetitions, 120-second warm-up and 300-second measurement. It will stop rather than launch the full campaign if pilot evidence or cleanup is insufficient.

The full matrix can exceed **38 hours before setup/reset overhead**. The three EC2 machines total approximately **USD 1.03/hour**, and the RDS compute rate is **USD 0.494/hour** in the checked AWS on-demand catalog. Combined compute is about **USD 1.52/hour**, plus EBS/RDS storage and public IPv4 charges. These are estimates at provisioning time, not a billing guarantee.

Check progress:

```sh
ssh -F artifacts/aws/ssh-config benchmark-monitor \
  'cat /home/rocky/language-benchmark/artifacts/aws/campaign-status.json; systemctl is-active language-benchmark-pilot language-benchmark-full'
```

Results live on the monitoring host under `/home/rocky/language-benchmark/artifacts/results/`. Final static reports are generated under `artifacts/reports/full/` there. The hourly reminder in this chat notifies on completion, failure, or prolonged idle resources; it does not stop machines automatically.

## Stop to save money

First interrupt the controller gracefully and confirm the backend/load services are stopped and the fixture is restored. To interrupt an active campaign:

```sh
ssh -F artifacts/aws/ssh-config benchmark-monitor \
  'sudo systemctl kill --signal=SIGINT --kill-whom=all language-benchmark-full; sudo systemctl kill --signal=SIGINT --kill-whom=all language-benchmark-pilot'
```

A hard host failure can require the documented `bench.cli recover` operation. After verified cleanup, stop the tagged resources from this checkout:

```sh
uv run python infrastructure/aws/stop.py
```

This preserves disk/database data. **Storage charges continue while stopped**, and **RDS automatically restarts after seven days**. Later deletion requires a separate decision about preserving results/snapshots. The existing unrelated `rds-eb` database is not part of this deployment or shutdown script.

Private deployment identifiers, SSH configuration/key and credentials are under ignored `artifacts/aws/`. Do not share that directory wholesale; exported benchmark manifests/reports redact credentials.
