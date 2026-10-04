# Runtime build inputs

`python/lock.json` pins exact source releases/checksums, OS base, build policy and runtime cells. `python/Dockerfile` builds interpreters; `python/build.py` builds interpreter and application images. `python/rust-lock.json` pins the build-only Rust toolchain needed by native Python dependencies.

Later language runtime directories use the same separation: immutable runtime/build metadata here, application source and application dependency locks under `backends/<language>`. Add a language-specific matrix and extend the runner's preflight deliberately when adding that language; Python GIL/JIT checks must not be reused as another language's readiness check.
