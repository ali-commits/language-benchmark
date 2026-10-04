"""Execute shared contract tests against each real source-built container."""

import json
import os
import subprocess
import time
from pathlib import Path

import httpx

if __name__ == "__main__":
    cells = json.loads(Path("runtimes/python/lock.json").read_text())["cells"]
    report = []
    out = Path("artifacts/source-contracts")
    out.mkdir(parents=True, exist_ok=True)
    for cell in cells:
        image = cell["image"]
        if subprocess.run(
            ["docker", "image", "inspect", image],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        ).returncode:
            report.append({"cell": cell["id"], "status": "missing image"})
            continue
        env = {
            **os.environ,
            "DATABASE_URL": "postgresql://postgres:benchmark@127.0.0.1:55432/language_benchmark_test",
            "JWT_SECRET": "contract-matrix-signing-key-at-least-32-bytes",
            "MODE": "local",
            "WORKERS": str(cell.get("workers", 2)),
            "ADAPTER": cell.get("adapter", "async"),
            "EXPECTED_GIL": str(cell.get("gil", True)).lower(),
            "EXPECTED_JIT": str(cell.get("jit", False)).lower(),
            "PYTHON_JIT": str(int(cell.get("jit", False))),
            "BIND_IP": "127.0.0.1",
        }
        args = [
            "docker",
            "run",
            "--rm",
            "-d",
            "--name",
            "lb-contract",
            "--network",
            "host",
            "--cpus",
            "2",
            "--memory",
            "3g",
            "--memory-swap",
            "3g",
        ]
        for key in [
            "DATABASE_URL",
            "JWT_SECRET",
            "MODE",
            "WORKERS",
            "ADAPTER",
            "EXPECTED_GIL",
            "EXPECTED_JIT",
            "PYTHON_JIT",
            "BIND_IP",
        ]:
            args += ["-e", key]
        log = out / (cell["id"] + ".log")
        try:
            subprocess.run(args + [image], env=env, check=True, stdout=subprocess.DEVNULL)
            ready = False
            for _ in range(60):
                try:
                    if (
                        httpx.get("http://127.0.0.1:8000/health/ready", timeout=2).status_code
                        == 200
                    ):
                        ready = True
                        break
                except httpx.HTTPError:
                    pass
                time.sleep(1)
            if not ready:
                with log.open("w") as f:
                    subprocess.run(["docker", "logs", "lb-contract"], stdout=f, stderr=f)
                report.append({"cell": cell["id"], "status": "startup failed"})
                continue
            with log.open("w") as f:
                result = subprocess.run(
                    ["uv", "run", "-m", "pytest", "tests/contract", "-q"],
                    env={**os.environ, "TARGET_URL": "http://127.0.0.1:8000"},
                    stdout=f,
                    stderr=f,
                )
            report.append(
                {
                    "cell": cell["id"],
                    "status": "pass" if result.returncode == 0 else "contract failed",
                    "image_id": subprocess.check_output(
                        ["docker", "image", "inspect", image, "--format", "{{.Id}}"], text=True
                    ).strip(),
                }
            )
        finally:
            subprocess.run(
                ["docker", "stop", "lb-contract"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        (out / "results.json").write_text(json.dumps(report, indent=2) + "\n")
    (out / "results.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))
    if any(r["status"] != "pass" for r in report):
        raise SystemExit(1)
