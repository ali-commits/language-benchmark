# Runtime support

Pinned source releases are in runtimes/python/lock.json. The 3.15 source is 3.15.0rc3; uv offers rc2 locally, which is diagnostic only and never pooled with rc3 measurements. Baseline uses SQLAlchemy 2.0.54 (3.10 compatible); modern uses 2.1.3. Every matrix cell must pass imports and post-import runtime checks before measuring. A combined JIT/free-threaded build is excluded until verified. Source builds and full import/contract evidence will be recorded during implementation.
