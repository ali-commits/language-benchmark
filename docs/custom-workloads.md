# Custom workloads

Use the generated HTTP `journey.js` as the smallest example. A k6 module exports a default function accepting the engine context. `context.baseUrl`, `context.parameters` and `context.operation(name, request, successPredicate)` define measured work. Module/VU state persists across one continuous ramping-VUs scenario. Dependencies and parameters are declared in configuration; scripts are prepared and retained for reproducibility.

Declare metrics by name/type/unit and latency failure population. Operation events record starts/completions, success, timeouts and network failures; unmatched starts remain interrupted counts. The native k6 occupancy stream measures actual active users, including trailing idle observations. The collector tail lies outside scheduled evaluation windows.

Targets may use an existing image or a retained build context. Fixture adapters may declare `prepare`, `reset` and `verify` hooks with `argv`, `host`, `timeout` and environment references as bounded argv commands, not interpolated shell strings. Inspect the configuration types for hook-specific fields; use `bench validate` before running. Generic hook/stateless workloads do not require the social schema.

The `social` adapter additionally requires an allowlisted DSN environment reference, checksum-verified fixture manifest/archive, a genuine locked runtime cell and unique case-insensitive actor names with passwords. Exactly the peak distinct account population must be available. Authentication measures distinct accounts separately from successful operations. Social setup/build/seed tools require the repository; initialization does not manufacture fixture evidence. Restoration retains the existing archive, fingerprint, session and runtime guards.
