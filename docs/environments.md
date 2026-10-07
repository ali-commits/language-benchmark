# Environments

The first release supports Linux Docker with local or SSH transport. Backend and generator may be separate hosts. SSH uses checked argv and encrypted stdin for environment data; it does not place secrets in command arguments. Hosts need synchronized clocks; offset correction is not implemented. Full remote deployed queue execution remains unverified; transport serialization and loopback tests do not establish complete deployment.

Network modes include host, direct Docker bridge routing, and owned bridge/compose layouts. Host mode requires a free configured target port. Docker resources declare CPU quota, memory and swap limits. Prepared target and immutable generator image identities are retained; recovery never silently replaces missing binaries.

Backend/generator on one host can interfere. Doctor checks declared capability/routing, but measured generator CPU, occupancy and exit/OOM evidence decide load validity. Linux host CPU/memory and container CPU/cgroup memory are distinct scopes. Optional baseline gates check observed CPU, memory and exact container allowances; they do not establish exclusive physical hardware.

Database telemetry defaults off. Opt-in RDS CloudWatch uses read-only capability discovery and 60-second Average buckets; publication delay can leave immediate short trials without evidence. Required missing database samples invalidate the trial. No cloud calls are needed for generic examples, and this implementation did not verify live RDS collection.

Target environment variables must have portable names and single-line values. CR, LF and NUL are rejected; literal backslash-n remains literal. Values are passed through a mode-0600 anonymous Docker env file and cannot configure the Docker client environment.
