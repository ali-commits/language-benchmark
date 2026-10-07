"""Explicit methodology defaults and effective timing metadata."""

from .records import Campaign, Methodology

PROFILES = {"fast": (180, 0, 240), "legacy": (300, 120, 300)}


def normalize_config(raw: Campaign) -> Campaign:
    """Normalize a copy; historical explicit campaign files remain untouched."""
    c = raw.copy()
    inferred_legacy = "profile" not in c and any(
        k in c for k in ("ramp_seconds", "settle_seconds", "hold_seconds")
    )
    c.setdefault("profile", "legacy" if inferred_legacy else "fast")
    if c["profile"] not in PROFILES:
        raise ValueError("unknown benchmark profile")
    ramp, settle, hold = PROFILES[c["profile"]]
    c.setdefault("ramp_seconds", ramp)
    c.setdefault("settle_seconds", settle)
    c.setdefault("hold_seconds", hold)
    c.setdefault("backend_nofile_limit", 250000)
    c.setdefault("experiment_type", "capacity")
    c.setdefault("start_users", 10000)
    c.setdefault("step_users", 10000)
    c.setdefault("max_users", 50000)
    c.setdefault("resolution_users", 100)
    c.setdefault("wait_min", 3)
    c.setdefault("wait_max", 10)
    c.setdefault("timeout_seconds", 5)
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


def methodology(c: Campaign) -> Methodology:
    timing = (c["ramp_seconds"], c["settle_seconds"], c["hold_seconds"])
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
