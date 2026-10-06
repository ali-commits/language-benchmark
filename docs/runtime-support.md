# Verified Python runtime support

Historical local verification on 2026-10-04 used the actual source-built Docker images, not just uv vendor interpreters. All 13 runtime/deployment cells configured at that time passed the shared HTTP contract tests over an external HTTP connection.

| Codebase | Releases / modes | Result |
| --- | --- | --- |
| Baseline | 3.10.22, 3.11.17, 3.12.13, 3.13.16, 3.14.8, 3.15.0rc3; two async processes | Contract tests passed for all six source builds |
| Modern | 3.15.0rc3 standard; two async processes | Passed |
| Modern | 3.15.0rc3 JIT build, JIT off/on; two async processes | Passed both modes |
| Modern | 3.15.0rc3 free-threaded build, GIL on/off; two async processes | Passed both modes |
| Modern | 3.15.0rc3 free-threaded build, GIL on/off; one process, 16 worker threads | Passed both modes |

Exact sources, checksums, OS digest and cells are in `runtimes/python/lock.json`. PGO/LTO are enabled in measured source builds. Compiler/build flags, installed packages, libpq implementation/version and actual post-import GIL/JIT state are recorded per worker. uv 0.12.22 is pinned in application/tooling builds.

Baseline locks stable FastAPI 0.142.2, Pydantic 2.13.5 / Core 2.46.5, SQLAlchemy 2.0.54 and Psycopg binary 3.3.6. Pydantic Core requires a source build on Python 3.15 standard CPython; the pinned Rust toolchain handles this.

Modern locks FastAPI 0.142.2, **Pydantic 2.14.0b2 / Core 2.49.0**, native Psycopg pools, and Psycopg C 3.3.6 linked to the image's system libpq. The Pydantic beta is intentional: stable Core 2.46.5 failed the new Python 3.15 free-threaded module ABI during a real import probe. Psycopg binary distributions lack cp315t wheels; compiling the same C driver for every modern mode avoids changing implementations between GIL controls. The verified free-threaded image retained GIL-disabled state after all application imports.

The modern comparison therefore includes dependency and database-access changes as well as Python API usage. It is not a pure interpreter speedup. Modern JIT/GIL controls retain the same modern application and dependency pins.

Python 3.15 is a release candidate. A later stable-release campaign needs new versioned inputs and must not be pooled with these prerelease cells. Simultaneous JIT/free-threaded mode is excluded; separate supported modes provide the comparisons without forcing unsafe imports.

Verification artifacts are private/ignored under `artifacts/source-contracts`, including each image ID and full test log. The shared contract now checks immediate registration/login over real HTTP and transaction commits before response headers. A full 50,000-user / 500,000-post fixture was generated with real salted Argon2id hashes and its restoration was verified by table fingerprints. Small Docker/k6 workflow trials completed successfully. AWS performance evidence is stored separately from these correctness checks; the verification artifacts do not establish capacity rankings.

These are correctness and reproducibility checks, not published throughput rankings. Local report qualification is unknown where concurrent builds, CPU/core utilization or swap allocation limit interpretation.

## Modern Python 3.14.8 reference (2026-10-06)

The registry now adds `python-modern-3.14.8-standard-jit0-gil1-async` alongside the 13 unchanged historical cells. It uses the existing checksum-pinned Python 3.14.8 source and Debian base, standard PGO/LTO build, two async processes, aggregate pool 20, JIT off and GIL on. The actual application image is `lb-python-modern:3.14.8-standard`.

The modern package floor and Ruff syntax target now include Python 3.14. The `uv.lock` was refreshed for that floor: all 26 previously locked package versions remain unchanged; additional Python 3.14 wheel/marker metadata is recorded. The Docker `requirements.lock` hashes are unchanged. Pydantic 2.14.0b2/Core 2.49.0 remains intentional, so this is a **stable interpreter reference with the existing beta-era dependency stack**, not a latest/all-stable dependency claim.

Actual source-built verification passed **62 external HTTP contract/interoperability tests** on disposable PostgreSQL, with two CPU quota, 3 GiB memory, no swap and nofile 250000. Both workers attested Python 3.14.8, native Psycopg C, PGO/LTO, GIL enabled and JIT unavailable/off; the full installed dependency inventory matched the lock. Focused runtime/cursor tests also passed. Image ID: `sha256:1b1cd48584b80cb4179c82bd08b9758b9351543eadba434d4d42fcfd8189e752`. Ignored evidence is in `artifacts/modern314-reference/` (`build.log`, original `contract.log`, `rebuild-strict.log`, `contract-strict.log`, worker snapshots and `proof.json`).

The first image exposed one compatibility difference: Python 3.14 accepts ISO8601 end-of-day `24:00:00`, while the original 3.10 API rejects that cursor. A focused modern-source guard now retains the 00–23 clock-hour range; valid timestamp microseconds, timezone offsets and year bounds are covered. The initial 61-pass/1-failure log is preserved, and the rebuilt image passed all 62 cases. This guard also applies to future rebuilt modern 3.15 images, so their source/image provenance must remain distinct from old recorded3.15 builds. Existing 3.15 registry cells, images running on AWS and historical results were not changed.

Build/verify commands:

```sh
uv run python runtimes/python/build.py --version 3.14.8 --mode standard --apps --jobs 8
uv lock --check --directory backends/python/modern --python 3.14.8
uv run -m pytest tests/unit/test_modern_reference.py tests/unit/test_modern_reference_cursor.py -q
TARGET_URL=http://127.0.0.1:58044 \
  TEST_DATABASE_URL=postgresql://benchmark:benchmark@127.0.0.1:55444/language_benchmark_modern314_test \
  uv run -m pytest tests/contract tests/multilanguage -q
```

The last command assumes the disposable local database and API are running; never point it at the canonical AWS database. Future mixed-language capacity/fixed-load templates select this reference and use the generic controller. See [future-language-campaigns.md](future-language-campaigns.md). No AWS deployment or performance run was performed for this new cell.
