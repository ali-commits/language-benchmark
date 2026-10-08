# Configuration

Public YAML schema version is `1`. Start from `bench init --template http --output PATH`; social initialization also writes explicit setup instructions and requires genuine fixture/runtime evidence before validation. `bench validate PATH` checks schema and resolved plan; `bench doctor PATH` checks read-only capabilities without running load.

Definitions contain arbitrary named targets, workloads and environments; jobs select target names and inherit defaults. Relative files resolve against their declaring YAML. Definitions can be included; cycles and conflicting definitions fail. Unknown fields fail validation. Secrets use `{env: VARIABLE}` references, rather than literal credentials in configuration.

Each stage has an ID, `from`, `to`, positive duration and `evaluate` selection. `target` endpoints scale with the candidate population; absolute integer endpoints are fixed. Durations accept units such as `3s`; execution rejects submillisecond timing. Supply an adequate hold grace and observed duration; very short holds can fail occupancy coverage.

Fixed tests accept user populations, a sweep or absolute stages. `stress` requires an explicit `strategy: search` or `strategy: sweep` and normalizes to search or fixed respectively. `staged` normalizes to fixed and does not accept a users field. Search uses `users: {min: 1, start: 100, max: auto}` plus finite `limits.max_trials`, `limits.max_elapsed` and a hard user ceiling. Objectives add criteria to the shared job criteria; bounds are separate per objective. The trial/time budget is shared across objectives and target expansions, including preparation and downtime.

Criteria declare metric, scope, statistic, operator and value; stages optionally override the evaluated windows. Percent, time and byte units are normalized. Host/container scopes are never interchangeable. Missing required evidence is invalid even if an unrelated criterion passes.

Targets may declare existing backend-host certificate files using additive version-1 `mounts`:

```yaml
targets:
  api:
    image: example/api:release
    mounts:
      - source: /srv/benchmark/certs/rds-ca.pem
        destination: /certs/rds-ca.pem
        read_only: true
        selinux: private
```

`mounts` defaults to an empty list. Both paths must be canonical absolute POSIX file paths; spaces are supported, while colons are rejected. `source` names a regular file on the backend Docker host, including for SSH environments. It is not resolved relative to the YAML or checked on the controller. Symlink files and directories are rejected. `read_only: true` is required. `selinux` defaults to `none`; `shared` requests Docker `z` labeling and `private` requests `Z` labeling. Choose labeling appropriate for the backend host and any other containers sharing the file.

Preparation records the file's SHA-256 and POSIX mode, and execution identity includes that receipt. Changed source bytes or mode block retained preparation/resume and deployment. Docker inspection must report the declared source and destination with write access disabled. Engine cleanup removes its containers, leaving the host source file in place. Empty mount lists preserve pre-existing execution identities.

Environments may set `source_ips: [10.0.0.10, 10.0.0.11]` to bind k6 connections to specific generator addresses. Each entry must be a unique canonical IP address assigned on the generator host; preparation and load preflight check `ip -j address show` through that host's transport. The ordered list is passed as `k6 run --local-ips 10.0.0.10,10.0.0.11` and participates in execution identity. The default empty list uses k6's normal address selection and preserves older execution identities.

For social workloads, distinct authenticated accounts produce the `authentication` rate. Configure its criterion (for example `>= 0.90`) to classify performance. Partial or zero authentication does not itself invalidate an otherwise valid trial; schedule, actual user occupancy, runtime, generator and fixture validity checks still apply.

### Search method and saved queues

New search jobs default to `search_method: upper_probe`. Once a measured passing
and failing bracket exists, it chooses the next count from decided observations:

- After a pass: probe `failing - max(resolution, ceil(bracket_width / 10))`.
- After a confirmed failure: probe the midpoint. Further failures continue midpoint
  reduction; a subsequent pass returns to probing near the upper bound.
- Pending confirmation votes repeat the same count; invalid evidence does not change
  direction. Before both bounds exist, geometric expansion/halving is unchanged.

For example, with resolution 50 and confirmation repeats omitted:
`2000 fail → 1000 pass → 1900 fail → 1450 pass → 1850 pass` finishes with the
measured bracket `[1850, 1900]`. Decisions survive resume through saved observations.
This strategy favors boundaries near the upper bound; it is not guaranteed to need
fewer trials than binary search when the actual boundary is much lower.

Select another method inside `test`:

```yaml
test:
  type: search
  search_method: upper_probe # alternatives: hybrid, binary
  users: {min: 1, start: 2000, max: auto}
  limits: {max_trials: 40, max_elapsed: 12h, hard_users: 50000}
  resolution: 50
```

`hybrid` uses numerical criterion measurements for safeguarded secant interpolation,
with midpoint fallback when interpolation is unsuitable. `binary` always uses the
midpoint inside the bracket. All methods retain the configured resolution, finite
trial/time budgets and confirmation policy. Unknown methods are rejected.

Saved queues, frozen job definitions, trial manifests and search checkpoints created
before this field existed keep their original binary policy. Reading or resuming those
records does not change their search method or completed evidence. An explicit pending
job update using a new definition may select another method; active jobs remain frozen. The
method is part of decision identity, while raw execution identity remains compatible.
