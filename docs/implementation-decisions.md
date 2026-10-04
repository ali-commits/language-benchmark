# Implementation decisions

The following records the decisions made during native implementation, including the initial workspace decision superseded by the user.

- Ruling: Use attached native worktree for isolation — authorized implementation needs a reviewable branch — cost if wrong: files live in attached worktree instead of primary checkout.

- Ruling: Python 3.15 source build is rc3; available uv rc2 is diagnostic only — preserve exact labels — cost if wrong: prerelease results cannot establish stable-release performance.

- Ruling: Use independently maintained copies for modern async/sync handlers and generic response pages — preserve equivalent queries without sharing application code — cost if wrong: adapter implementations require parity tests when changed.

- Ruling: User explicitly requested primary checkout with live changes — all implementation now happens in /home/ali/projects/language-benchmark — cost if wrong: primary checkout carries active development changes; work remains on python-fastapi-benchmark branch.

- Ruling: Modern application uses native Psycopg async/sync pools rather than SQLAlchemy greenlet bridging — free-threaded safety and direct async I/O require avoiding an unsupported bridge — cost if wrong: application comparison includes database-access architecture as well as dependency changes.

- Ruling: Modern pins Pydantic 2.14.0b2 / Core 2.49.0 and Psycopg 3.3.6 C distribution across all modern runtime modes — stable Pydantic/Core failed the Python 3.15t module ABI and psycopg-binary has no cp315t wheels — cost if wrong: modern campaign is explicitly prerelease and includes stack differences; baseline stays fixed stable across all interpreters.

- Ruling: User requested language-oriented layout — moved applications to backends/python and runtime builds to runtimes/python while keeping schema/contracts/k6 shared — cost if wrong: historical paths and build commands must be updated together.

- Ruling: Cache lifespan settings for JWT operations — avoid rereading and revalidating environment in every authentication request while preserving both applications’ policies — cost if wrong: configuration changes require application restart, as documented fixed campaign configuration already requires.

The final review found six important issues, all fixed with regression tests. Its one stale-documentation finding was also corrected. No minor findings are deferred. Actual AWS performance and infrastructure capacity remain unmeasured.
