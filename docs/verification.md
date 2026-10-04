# Verification evidence

The repository records reproducibility through source locks and per-run private artifacts. It does not contain invented AWS benchmark data.

Verified locally:

- Full unit/contract/integration suite: 102 tests passed after the independent final review fixes. Tests use a dedicated `language_benchmark_test` database and refuse ordinary benchmark database names.
- Thirteen real source-built runtime/deployment configurations passed the shared HTTP contract suite: six baseline interpreters, standard modern, JIT off/on, and four free-threaded GIL/topology controls.
- Runtime imports retained the disabled GIL in the modern no-GIL image. JIT controls use the same JIT-capable executable.
- Docker/Compose configurations validate and enforce separate backend/generator services plus the backend's two-vCPU / 3-GiB / no-swap budget.
- Full dataset counts: 50,000 users, 500,000 posts, 2,500,000 follows, 1,000,000 likes, 250,000 comments. A deliberately changed post was restored and all five logical fingerprints matched the canonical snapshot.
- A local Docker/k6 trial measured operations, collected raw distributions, and restored the database afterward. Local reports correctly retained CPU/swap qualification warnings.
- Lint and whitespace validation passed with Ruff 0.16.10 and `git diff --check`.

Commands, individual logs and image identities are under ignored `artifacts/`; secrets and canonical dumps stay out of git. The independent review identified six important issues; each received regression coverage and a fix. Native PostgreSQL client capture/restore and a full-dataset no-GIL threaded trial then succeeded: 200 operations completed successfully, zero dropped iterations, and verified final restoration. Reports remain explicitly local smoke with unknown performance qualification because this machine has swap allocated. No minor review findings are deferred. See implementation-decisions.md for recorded choices.
