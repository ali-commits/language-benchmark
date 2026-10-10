import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from types import SimpleNamespace

import pytest

from tools.bench.engine.types import Host, Target
from tools.bench.tuning import load_tuned_profiles


def test_php_warmup_initializes_workers_with_bounded_readiness_only():
    from tools.bench.engine.social import warm_tuned_php

    paths = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            paths.append(self.path)
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b'{"status":"ok"}')

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        warm_tuned_php(
            Host(transport="local"), f"http://127.0.0.1:{server.server_port}/health/ready", 10
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
    assert paths == ["/health/ready"] * 100


def test_php_warmup_rejects_unready_response():
    from tools.bench.engine.social import warm_tuned_php

    with pytest.raises(RuntimeError):
        warm_tuned_php(Host(transport="local"), "http://127.0.0.1:1/health/ready", 0.2)


def context():
    return SimpleNamespace(
        trial_id="nonce",
        environment=SimpleNamespace(backend=Host(transport="local")),
        job=SimpleNamespace(target=Target(image="test")),
        prepared_target=None,
    )


def container():
    return dict(
        Id="container",
        Image="sha256:x",
        Config=dict(Labels={"bench.trial": "nonce"}),
        State=dict(Running=True, StartedAt="1970-01-01T00:01:40Z"),
        HostConfig=dict(
            NanoCpus=4000000000,
            Memory=6442450944,
            MemorySwap=6442450944,
            Ulimits=[dict(Name="nofile", Soft=250000, Hard=250000)],
        ),
    )


def test_social_tuned_attestation_requires_process_and_quota_proof(monkeypatch):
    from tools.bench.engine import social

    profile = next(p for p in load_tuned_profiles() if p.base_cell == "go-net-http")
    receipt = dict(
        schema_version=1,
        run_id="nonce",
        pid=10,
        language="go",
        workers_total=1,
        connections_per_worker=20,
        pool_budget=20,
        cpu_parallelism=4,
        hash_concurrency=4,
        hash_scope="process",
    )
    text = (
        "C\t200\t100\t100\nQ\t400000 100000\nM\t6442450944\nS\t0\nP\t10\t200\tsocial-api\nR\ttuning-10.json\t103\t"
        + json.dumps(receipt)
        + "\n"
    )
    monkeypatch.setattr(social, "execute_host", lambda *a, **kw: SimpleNamespace(stdout=text))
    assert social.attest_tuned_runtime(context(), "container", container(), profile)[
        "receipts"
    ] == [receipt]
    text = text.replace("400000 100000", "200000 100000")
    with pytest.raises(ValueError):
        social.attest_tuned_runtime(context(), "container", container(), profile)
    with pytest.raises(ValueError):
        social.attest_tuned_runtime(context(), "another-container", container(), profile)


def test_missing_receipts_exhaust_finite_readiness_budget(monkeypatch):
    from tools.bench.engine import social
    from tools.bench.engine.types import Readiness

    profile = next(p for p in load_tuned_profiles() if p.base_cell == "go-net-http")
    ctx = context()
    ctx.job.target = Target(image="test", readiness=Readiness(timeout=0.01))
    text = "C\t200\t100\t100\nQ\t400000 100000\nM\t6442450944\nS\t0\nP\t10\t200\tsocial-api\n"
    monkeypatch.setattr(social, "execute_host", lambda *a, **kw: SimpleNamespace(stdout=text))
    with pytest.raises(ValueError, match="deadline"):
        social.attest_tuned_runtime(ctx, "container", container(), profile)
