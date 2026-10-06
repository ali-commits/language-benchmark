"""Audited decision-only majority votes; measurement evidence remains immutable."""

import hashlib
import json
from collections import Counter

from .profiles import normalize_config

POLICY = "majority-on-disagreement"
MEASUREMENT_KEYS = (
    "backend_nofile_limit",
    "mode",
    "experiment_type",
    "profile",
    "fixed_users",
    "authentication_fraction",
    "confirmation_policy",
    "growth_strategy",
    "initial_failing_hint",
    "cell_starting_hints",
    "cells",
    "start_users",
    "step_users",
    "max_users",
    "resolution_users",
    "ramp_seconds",
    "settle_seconds",
    "hold_seconds",
    "wait_min",
    "wait_max",
    "timeout_seconds",
    "success_fraction",
    "confirmation_repeats",
    "seed",
    "fixture",
    "expected_image_ids",
)


def measurement_identity(config):
    c = normalize_config(config)
    return hashlib.sha256(
        json.dumps(
            {k: c.get(k) for k in MEASUREMENT_KEYS},
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode()
    ).hexdigest()


def projected_config(original, new):
    """A queue extension can add only trailing cells and their hints/image identities."""
    old = normalize_config(original)
    current = normalize_config(new)
    if measurement_identity(old) == measurement_identity(current):
        return current
    receipt = current.get("queue_extension_handoff", {})
    before, after = old["cells"], current["cells"]
    if not isinstance(receipt, dict) or after[: len(before)] != before or len(after) <= len(before):
        key = next(
            (key for key in MEASUREMENT_KEYS if old.get(key) != current.get(key)),
            "measurement identity",
        )
        raise ValueError("resume configuration differs: " + key)
    added = set(after[len(before) :])
    projected = dict(current)
    projected["cells"] = before
    for key in ("cell_starting_hints", "expected_image_ids"):
        projected[key] = {k: v for k, v in current.get(key, {}).items() if k not in added}
        if key not in old and not projected[key]:
            projected.pop(key, None)
    if (
        receipt.get("from_cells") != before
        or receipt.get("to_cells") != after
        or receipt.get("previous_measurement_identity_sha256") != measurement_identity(old)
        or receipt.get("new_measurement_identity_sha256") != measurement_identity(current)
        or not isinstance(receipt.get("reason"), str)
        or not receipt["reason"].strip()
        or measurement_identity(projected) != measurement_identity(old)
    ):
        raise ValueError("queue extension handoff does not preserve previous measurements")
    return projected


def validate_decision_handoff(original, new):
    old = normalize_config(original)
    current = projected_config(old, new)
    before = old.get("boundary_decision_policy", "legacy")
    after = current.get("boundary_decision_policy", "legacy")
    if before == after:
        return current
    receipt = current.get("decision_policy_handoff", {})
    if (
        before != "legacy"
        or after != POLICY
        or not isinstance(receipt, dict)
        or receipt.get("from") != before
        or receipt.get("to") != after
        or receipt.get("measurement_identity_sha256") != measurement_identity(old)
        or not isinstance(receipt.get("reason"), str)
        or not receipt["reason"].strip()
    ):
        raise ValueError("decision policy change requires audited handoff receipt")
    return current


class Consensus:
    def __init__(self, probe, existing, persist):
        self.probe = probe
        self.votes = {}
        self.invalid = []
        self.persist = persist
        seen = {}
        for users, items in existing.items():
            self.votes[int(users)] = []
            for item in items:
                if item.get("outcome") not in ("pass", "fail"):
                    continue
                identity = item.get("trial_id")
                if not isinstance(identity, str) or not identity:
                    raise ValueError("votes require immutable trial identities")
                if identity in seen:
                    if seen[identity] != (int(users), item["outcome"]):
                        raise ValueError("conflicting trial vote identity")
                    continue
                seen[identity] = (int(users), item["outcome"])
                self.votes[int(users)].append(dict(item))
            if len(self.votes[int(users)]) > 3:
                raise ValueError("existing evidence exceeds three valid votes per population")
        self.save()

    def decision(self, users):
        items = self.votes.get(users, [])
        if not items:
            return None
        counts = Counter(i["outcome"] for i in items)
        if len(items) == 2 and len(counts) == 2:
            return None
        return max(counts, key=counts.get)

    def decisions(self):
        return {
            str(users): {
                "outcome": self.decision(users),
                "valid_vote_count": len(items),
                "votes": [{"trial_id": i["trial_id"], "outcome": i["outcome"]} for i in items],
                "consensus_final": len(items) >= 2,
            }
            for users, items in sorted(self.votes.items())
        }

    def save(self):
        if self.persist:
            self.persist(self.decisions())

    def collect(self, users):
        items = self.votes.setdefault(users, [])
        if len(items) >= 3:
            raise ValueError("fourth valid vote is prohibited")
        item = self.probe(users)
        if item.get("outcome") == "invalid":
            self.invalid.append({"users": users, **item})
            return False
        if item.get("outcome") not in ("pass", "fail") or not item.get("trial_id"):
            raise ValueError("invalid vote record")
        if any(i["trial_id"] == item["trial_id"] for values in self.votes.values() for i in values):
            raise ValueError("duplicate vote cannot count twice")
        items.append(dict(item))
        self.save()
        return True

    def explore(self, users):
        if not self.votes.get(users) and not self.collect(users):
            return "invalid"
        if self.decision(users) is None and not self.collect(users):
            return "invalid"
        return self.decision(users)

    def confirm_failure(self, users):
        if len(self.votes[users]) == 1 and not self.collect(users):
            return "invalid"
        if self.decision(users) is None and not self.collect(users):
            return "invalid"
        return self.decision(users)

    def result(self, status, resolution):
        decisions = {n: self.decision(n) for n in self.votes}
        passes = [n for n, value in decisions.items() if value == "pass"]
        fails = [n for n, value in decisions.items() if value == "fail"]
        lower = max(passes, default=0)
        upper = min((n for n in fails if n > lower), default=None)
        return {
            "status": status,
            "passing_users": lower,
            "failing_users": upper,
            "resolution": resolution,
            "boundary_decision_policy": POLICY,
            "decisions": self.decisions(),
            "observations": sorted(
                [{"users": n, **i} for n, items in self.votes.items() for i in items]
                + self.invalid,
                key=lambda i: i.get("trial_id", ""),
            ),
        }


def majority_search(
    probe,
    existing=None,
    start=10000,
    step=10000,
    ceiling=50000,
    resolution=100,
    next_candidate=None,
    growth_strategy="geometric",
    persist=None,
):
    if not 1 <= start <= ceiling or step < 1 or resolution < 1:
        raise ValueError("invalid search bounds")
    if growth_strategy not in ("geometric", "adaptive"):
        raise ValueError("unknown growth strategy")
    state = Consensus(probe, existing or {}, persist)
    # A previously contradictory final repeat is resolved first, before any higher search.
    for users in sorted(state.votes):
        if state.decision(users) is None and not state.collect(users):
            return state.result("invalid", resolution)
    if not state.votes and state.explore(start) == "invalid":
        return state.result("invalid", resolution)
    while True:
        decisions = {n: state.decision(n) for n in state.votes}
        passes = [n for n, v in decisions.items() if v == "pass"]
        fails = [n for n, v in decisions.items() if v == "fail"]
        lower = max(passes, default=0)
        conflicts = [n for n in fails if n < lower]
        if conflicts:
            result = state.result("unstable_boundary", resolution)
            result["non_monotonic_counts"] = {
                "failing_below_passing": conflicts,
                "highest_passing": lower,
            }
            return result
        upper = min((n for n in fails if n > lower), default=None)
        if upper is not None and upper - lower <= resolution:
            decision = state.confirm_failure(upper)
            if decision == "invalid":
                return state.result("invalid", resolution)
            if decision == "fail":
                return state.result("bracket_found", resolution)
            # A promoted failing point is now a lower bound; derive higher bounds again.
            continue
        if upper is not None:
            candidate = (lower + upper) // 2
        elif lower == ceiling:
            return state.result("ceiling_reached", resolution)
        elif not lower:
            candidate = start
        elif (
            next_candidate is not None
            and lower < next_candidate <= ceiling
            and next_candidate not in state.votes
        ):
            candidate = next_candidate
        elif growth_strategy == "geometric":
            candidate = min(max(lower + resolution, lower * 2), ceiling)
        else:
            candidate = min(lower + step, ceiling)
        if candidate in state.votes:
            raise ValueError("search made no finite progress")
        if state.explore(candidate) == "invalid":
            return state.result("invalid", resolution)
