# Durable queues

Every public queue command accepts `--state-dir`; it uses `STATE_DIR/queue.db`. Storage must be a local Linux filesystem supporting SQLite locks and durability; NFS/SMB is unsupported. Registration freezes definitions and relevant inputs. Pending input changes require an explicit update; completed artifacts remain immutable.

`queue start CONFIG` registers and runs serially in the foreground. `queue list`, `status QUEUE` and `logs QUEUE` expose sanitized state/event history. `append QUEUE FILE` adds work; `update QUEUE JOB FILE` replaces a pending job, with optional expected revision; `remove QUEUE JOB` cancels pending work. Active/completed jobs cannot be replaced. Append after completion never starts a worker.

`queue stop QUEUE` persists cancellation and waits for owned cleanup within `--timeout`. `--after-current` finishes all expansions, repetitions and objectives of the current logical job before pausing. `queue resume QUEUE` explicitly clears stop and resumes retained state. Foreground signals persist stop; systemd shutdown signals interrupt transiently so an enabled unit can resume after boot. Cleanup is never cancelled midway.

Invalid trials with verified cleanup retry within `test.invalid_retries`; exhaustion requires attention and survives restart. Search retries still consume finite attempts. Lost ownership or changed prepared identities fail closed. Recovery uses exact retained owner receipts; there is no age-based takeover or removal of foreign resources.

Optional start/resume `--detach` installs/enables a user-systemd service only when the user session and lingering are already configured. The adapter uses no sudo and never enables lingering. Explicit stop disables reboot activation. Supply `--env-file` for owned regular 0600 literal NAME=value secrets needed by a service; shell exports do not automatically survive reboot. Actual reboot persistence remains unverified.
