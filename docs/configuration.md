# Configuration

Public YAML schema version is `1`. Start from `bench init --template http --output PATH`; social initialization also writes explicit setup instructions and requires genuine fixture/runtime evidence before validation. `bench validate PATH` checks schema and resolved plan; `bench doctor PATH` checks read-only capabilities without running load.

Definitions contain arbitrary named targets, workloads and environments; jobs select target names and inherit defaults. Relative files resolve against their declaring YAML. Definitions can be included; cycles and conflicting definitions fail. Unknown fields fail validation. Secrets use `{env: VARIABLE}` references, rather than literal credentials in configuration.

Each stage has an ID, `from`, `to`, positive duration and `evaluate` selection. `target` endpoints scale with the candidate population; absolute integer endpoints are fixed. Durations accept units such as `3s`; execution rejects submillisecond timing. Supply an adequate hold grace and observed duration; very short holds can fail occupancy coverage.

Fixed tests accept user populations, a sweep or absolute stages. `stress` requires an explicit `strategy: search` or `strategy: sweep` and normalizes to search or fixed respectively. `staged` normalizes to fixed and does not accept a users field. Search uses `users: {min: 1, start: 100, max: auto}` plus finite `limits.max_trials`, `limits.max_elapsed` and a hard user ceiling. Objectives add criteria to the shared job criteria; bounds are separate per objective. The trial/time budget is shared across objectives and target expansions, including preparation and downtime.

Criteria declare metric, scope, statistic, operator and value; stages optionally override the evaluated windows. Percent, time and byte units are normalized. Host/container scopes are never interchangeable. Missing required evidence is invalid even if an unrelated criterion passes.
