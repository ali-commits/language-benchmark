"""Build locked source images and applications. Smoke images cannot pass measured preflight."""

import argparse
import json
import subprocess
from pathlib import Path

p = argparse.ArgumentParser()
p.add_argument("--version", action="append")
p.add_argument("--mode", choices=["standard", "jit", "free-threaded"], default="standard")
p.add_argument("--jobs", type=int, default=4)
p.add_argument("--smoke", action="store_true")
p.add_argument("--apps", action="store_true")
a = p.parse_args()
lock = json.loads(Path("runtimes/python/lock.json").read_text())
for s in lock["runtimes"]:
    if a.version and s["version"] not in a.version:
        continue
    if a.mode != "standard" and not s["version"].startswith("3.15"):
        continue
    prefix = "lb-python-smoke" if a.smoke else "lb-python"
    tag = prefix + ":" + s["version"] + "-" + a.mode
    argv = [
        "docker",
        "build",
        "-f",
        "runtimes/python/Dockerfile",
        "-t",
        tag,
        "--build-arg",
        "BASE=" + lock["base"],
        "--build-arg",
        "PYTHON_VERSION=" + s["version"],
        "--build-arg",
        "SOURCE_SHA256=" + s["sha256"],
        "--build-arg",
        "BUILD_MODE=" + a.mode,
        "--build-arg",
        f"JOBS={a.jobs}",
        "--build-arg",
        "OPTIMIZE=" + ("0" if a.smoke else "1"),
        ".",
    ]
    subprocess.run(argv, check=True)
    if a.apps:
        for app in (
            ["modern"]
            if a.mode != "standard"
            else (
                ["baseline", "modern"]
                if s["version"].startswith(("3.14", "3.15"))
                else ["baseline"]
            )
        ):
            name = f"lb-python-{app}{'-smoke' if a.smoke else ''}:{s['version']}" + (
                "-" + a.mode if app == "modern" else ""
            )
            subprocess.run(
                [
                    "docker",
                    "build",
                    "-f",
                    "backends/python/Dockerfile",
                    "-t",
                    name,
                    "--build-arg",
                    "RUNTIME_IMAGE=" + tag,
                    "--build-arg",
                    "APP=" + app,
                    ".",
                ],
                check=True,
            )
