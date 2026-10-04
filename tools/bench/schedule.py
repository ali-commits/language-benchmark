import hashlib
import json
import random


def validate_campaign(c: dict) -> dict:
    if c.get("mode") not in ("local", "aws"):
        raise ValueError("mode must be local or aws")
    smoke = c.get("smoke", False)
    if (
        not c.get("cells")
        or not c.get("rates")
        or any(type(r) is not int or not 1 <= r <= c.get("max_rate", 3200) for r in c["rates"])
    ):
        raise ValueError("invalid finite rates")
    if not 1 <= c.get("vus", 0) <= 10000:
        raise ValueError("invalid VU ceiling")
    if not (1 if smoke else 5) <= c.get("repeats", 0) <= 100:
        raise ValueError("insufficient/excessive repetitions")
    if not 1 <= c.get("measure_seconds", 0) <= 3600 or not 0 <= c.get("warmup_seconds", -1) <= 600:
        raise ValueError("invalid durations")
    if not smoke and (c["warmup_seconds"] != 120 or c["measure_seconds"] != 300):
        raise ValueError("primary campaign requires 120/300 seconds")
    if not c.get("database_allowlist", "").startswith("language_benchmark"):
        raise ValueError("dedicated benchmark database required")
    if len(set(c["cells"])) != len(c["cells"]) or len(set(c["rates"])) != len(c["rates"]):
        raise ValueError("duplicate cells/rates")
    return c


def schedule_trials(c: dict) -> list[dict]:
    validate_campaign(c)
    rng = random.Random(c.get("seed", 20261004))
    trials = []
    identity = hashlib.sha256(json.dumps(c, sort_keys=True).encode()).hexdigest()[:12]
    for repeat in range(c["repeats"]):
        block = [
            {
                "campaign_id": identity,
                "cell": cell,
                "rate": r,
                "repetition": repeat,
                "seed": c.get("seed", 20261004),
            }
            for cell in c["cells"]
            for r in c["rates"]
        ]
        rng.shuffle(block)
        for t in block:
            t["id"] = f"{identity}-{len(trials):04d}"
            trials.append(t)
    return trials
