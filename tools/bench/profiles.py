"""Explicit methodology defaults and effective timing metadata."""

PROFILES = {"fast": (180, 0, 240), "legacy": (300, 120, 300)}


def normalize_config(raw: dict) -> dict:
    """Normalize a copy; historical explicit campaign files remain untouched."""
    c = dict(raw)
    inferred_legacy = "profile" not in c and any(
        k in c for k in ("ramp_seconds", "settle_seconds", "hold_seconds")
    )
    c.setdefault("profile", "legacy" if inferred_legacy else "fast")
    if c["profile"] not in PROFILES:
        raise ValueError("unknown benchmark profile")
    for key, value in zip(
        ("ramp_seconds", "settle_seconds", "hold_seconds"), PROFILES[c["profile"]]
    ):
        c.setdefault(key, value)
    c.setdefault("backend_nofile_limit", 250000)
    c.setdefault("experiment_type", "capacity")
    for key, value in {
        "start_users": 10000,
        "step_users": 10000,
        "max_users": 50000,
        "resolution_users": 100,
        "wait_min": 3,
        "wait_max": 10,
        "timeout_seconds": 5,
    }.items():
        c.setdefault(key, value)
    c.setdefault("success_fraction", 0.99 if c["profile"] == "legacy" else 0.90)
    c.setdefault("authentication_fraction", c["success_fraction"])
    c.setdefault("confirmation_repeats", 5 if inferred_legacy else 1)
    c.setdefault(
        "confirmation_policy",
        {
            "passing_repeats": c["confirmation_repeats"] if inferred_legacy else 0,
            "failing_repeats": c["confirmation_repeats"] if inferred_legacy else 1,
            "stop_on_contradiction": not inferred_legacy,
        },
    )
    if c["experiment_type"] == "fixed-load":
        c.setdefault("fixed_users", [100])
    return c


def methodology(c):
    timing = tuple(c[k] for k in ("ramp_seconds", "settle_seconds", "hold_seconds"))
    requested = c.get("profile", "legacy")
    effective = requested if timing == PROFILES.get(requested) else "custom"
    return {
        "backend_nofile_limit": c.get("backend_nofile_limit", 250000),
        "experiment_type": c.get("experiment_type", "capacity"),
        "profile": effective,
        "requested_profile": requested,
        "ramp_seconds": timing[0],
        "settle_seconds": timing[1],
        "hold_seconds": timing[2],
        "configured_trial_seconds": sum(timing),
        "steady_duration_seconds": timing[2],
        "success_fraction": c.get("success_fraction", 0.99),
        "authentication_fraction": c.get(
            "authentication_fraction", c.get("success_fraction", 0.99)
        ),
        "timeout_seconds": c["timeout_seconds"],
    }
