# Verified Python runtime support

Local verification on 2026-10-04 used the actual source-built Docker images, not just uv vendor interpreters. All 13 configured runtime/deployment cells passed the shared HTTP contract tests over an external HTTP connection.

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

Verification artifacts are private/ignored under `artifacts/source-contracts`, including each image ID and full test log. The shared contract now checks immediate registration/login over real HTTP and transaction commits before response headers. A full 50,000-user / 500,000-post fixture was generated with real salted Argon2id hashes and its restoration was verified by table fingerprints. Small Docker/k6 workflow trials completed successfully. Actual AWS performance measurements remain to be collected on the specified infrastructure.

These are correctness and reproducibility checks, not published throughput rankings. Local report qualification is unknown where concurrent builds, CPU/core utilization or swap allocation limit interpretation.
