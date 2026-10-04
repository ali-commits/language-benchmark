# Backend implementations

Every language gets its own directory. Current implementations are:

```text
backends/
  python/
    Dockerfile             # application images; repository root is build context
    entrypoint.py          # explicit async/threaded worker launch
    baseline/              # Python 3.10-compatible code and frozen dependencies
      pyproject.toml
      uv.lock
      requirements.lock
      src/baseline_api/
    modern/                # Python 3.15 native async/threaded code
      pyproject.toml
      uv.lock
      requirements.lock
      src/modern_api/
```

Future languages go under `backends/<language>/` with their own source, package-manager lockfiles, Dockerfile and language-specific tests. Do not duplicate the database schema, HTTP contract or k6 scenarios. Keep one realistic reference implementation per language unless a distinct variant answers an explicit experiment question.

The harness controls images/configurations; backends serve the shared `/api/v1` contract. Each new implementation must pass `tests/contract`, use the canonical fixture, retain auth/transaction/query semantics, and record its runtime/compiler/dependency settings. Language runtime source/build metadata belongs under `runtimes/<language>/`.

Only Python is implemented today. Empty placeholder applications are intentionally not presented as working backends.
