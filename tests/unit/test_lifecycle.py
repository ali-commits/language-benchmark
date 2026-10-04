from bench.runner import trial_lifecycle


def test_runner_resets_before_and_after_each_rate():
    events = []
    result = trial_lifecycle(
        lambda: events.append("stop"),
        lambda: events.append("reset"),
        lambda: events.append("measure"),
    )
    assert events == ["stop", "reset", "measure", "stop", "reset"]
    assert result["reset_verified"]


def test_interrupt_preserves_failure_and_runs_cleanup():
    events = []

    def fail():
        events.append("measure")
        raise KeyboardInterrupt()

    result = trial_lifecycle(lambda: events.append("stop"), lambda: events.append("reset"), fail)
    assert events == ["stop", "reset", "measure", "stop", "reset"]
    assert result["status"] == "interrupted" and result["reset_verified"]


def test_failed_cleanup_is_explicit():
    attempts = []

    def reset():
        attempts.append(1)
        if len(attempts) == 2:
            raise RuntimeError("restore failed")

    result = trial_lifecycle(lambda: None, reset, lambda: None)
    assert result["status"] == "failed" and not result["reset_verified"]
