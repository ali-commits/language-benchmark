"""Build and verify the local-only non-Python matrix, without AWS access."""

import argparse
import json
import os
import subprocess
import tempfile
import time
import uuid
from pathlib import Path

import httpx

from .manifest import atomic_json
from .native_runtime import BUDGET, REGISTRY_LANGUAGES, validate_native_registry_cell

LANGUAGES = tuple(REGISTRY_LANGUAGES)
SECRET = "test-secret-that-has-at-least-32-bytes"


def execute(argv, **kwargs):
    return subprocess.run(argv, check=True, capture_output=True, text=True, **kwargs)


def cells(selected):
    result = []
    for language in selected:
        lock = json.loads((Path("runtimes") / language / "lock.json").read_text())
        if lock.get("budget") != BUDGET:
            raise ValueError("uncontrolled native registry budget")
        if len(lock["cells"]) != 1:
            raise ValueError("exactly one cell per language/framework required")
        cell = lock["cells"][0]
        validate_native_registry_cell(cell, language)
        if cell["workers"] * cell["connections_per_worker"] != 20:
            raise ValueError("aggregate database budget must be20")
        result.append(cell)
    return result


def build(selected):
    for cell in cells(selected):
        subprocess.run(
            ["docker", "build", "-f", cell["dockerfile"], "-t", cell["image"], "."], check=True
        )


def wait_database(name):
    deadline = time.monotonic() + 90
    while time.monotonic() < deadline:
        ready = subprocess.run(
            ["docker", "exec", name, "pg_isready", "-h", "127.0.0.1", "-U", "postgres"],
            capture_output=True,
        )
        if ready.returncode == 0:
            return
        time.sleep(0.5)
    raise RuntimeError("disposable database did not become ready")


def wait_ready(port):
    deadline = time.monotonic() + 120
    with httpx.Client(timeout=3) as client:
        while time.monotonic() < deadline:
            try:
                response = client.get(f"http://127.0.0.1:{port}/health/ready")
                if response.status_code == 200 and response.json() == {"status": "ok"}:
                    return
            except (httpx.HTTPError, ValueError):
                pass
            time.sleep(0.5)
    raise RuntimeError("backend readiness deadline exceeded")


def tls_files(folder):
    folder = Path(folder)
    folder.chmod(0o755)
    execute(
        [
            "openssl",
            "req",
            "-x509",
            "-newkey",
            "rsa:2048",
            "-nodes",
            "-keyout",
            str(folder / "ca.key"),
            "-out",
            str(folder / "ca.pem"),
            "-days",
            "1",
            "-subj",
            "/CN=LanguageBenchmarkTestCA",
        ]
    )
    execute(
        [
            "openssl",
            "req",
            "-new",
            "-newkey",
            "rsa:2048",
            "-nodes",
            "-keyout",
            str(folder / "server.key"),
            "-out",
            str(folder / "server.csr"),
            "-subj",
            "/CN=db",
        ]
    )
    (folder / "extensions.cnf").write_text(
        "basicConstraints=critical,CA:FALSE\nkeyUsage=critical,digitalSignature,keyEncipherment\nextendedKeyUsage=serverAuth\nsubjectAltName=DNS:db\n"
    )
    execute(
        [
            "openssl",
            "x509",
            "-req",
            "-in",
            str(folder / "server.csr"),
            "-CA",
            str(folder / "ca.pem"),
            "-CAkey",
            str(folder / "ca.key"),
            "-CAcreateserial",
            "-out",
            str(folder / "server.crt"),
            "-days",
            "1",
            "-extfile",
            str(folder / "extensions.cnf"),
        ]
    )
    (folder / "server.key").chmod(0o600)
    # Only the ephemeral test certificate/key is changed, inside this temporary folder.
    execute(
        [
            "docker",
            "run",
            "--rm",
            "-v",
            f"{folder}:/certs",
            "--entrypoint",
            "chown",
            "postgres:17.6",
            "postgres:postgres",
            "/certs/server.key",
            "/certs/server.crt",
        ]
    )


def verify(selected, output, db_port=55440, api_port=58040, tls=True):
    output.mkdir(parents=True, exist_ok=False)
    nonce = uuid.uuid4().hex[:10]
    network, db = "lb-contract-net-" + nonce, "lb-contract-db-" + nonce
    backend = "lb-contract-api-" + nonce
    password = "local-contract-only"
    database = "language_benchmark_matrix_test"
    results = []
    network_created = False
    with tempfile.TemporaryDirectory(prefix="lb-tls-") as folder:
        try:
            execute(["docker", "network", "create", network])
            network_created = True
            argv = [
                "docker",
                "run",
                "-d",
                "--name",
                db,
                "--network",
                network,
                "--network-alias",
                "db",
                "--network-alias",
                "wrong-db",
                "-p",
                f"127.0.0.1:{db_port}:5432",
                "-e",
                "POSTGRES_PASSWORD=" + password,
                "-e",
                "POSTGRES_DB=" + database,
            ]
            if tls:
                tls_files(folder)
                argv += ["-v", f"{folder}:/certs:ro"]
            argv += ["postgres:17.6"]
            if tls:
                argv += [
                    "-c",
                    "ssl=on",
                    "-c",
                    "ssl_cert_file=/certs/server.crt",
                    "-c",
                    "ssl_key_file=/certs/server.key",
                ]
            execute(argv)
            wait_database(db)
            for cell in cells(selected):
                directory = output / cell["id"]
                directory.mkdir()
                url = f"postgresql://postgres:{password}@db/{database}"
                if tls:
                    url += "?sslmode=verify-full&sslrootcert=/certs/ca.pem"
                argv = [
                    "docker",
                    "run",
                    "-d",
                    "--name",
                    backend,
                    "--network",
                    network,
                    "--cpus",
                    "2",
                    "--memory",
                    "3g",
                    "--memory-swap",
                    "3g",
                    "-p",
                    f"127.0.0.1:{api_port}:8000",
                    "-e",
                    "DATABASE_URL=" + url,
                    "-e",
                    "JWT_SECRET=" + SECRET,
                    "-e",
                    "JWT_TTL_SECONDS=3600",
                    "-e",
                    "PORT=8000",
                    "-e",
                    "BIND_IP=0.0.0.0",
                    "-e",
                    "MODE=" + ("aws" if tls else "local"),
                ]
                if tls:
                    argv += ["-v", f"{folder}/ca.pem:/certs/ca.pem:ro"]
                try:
                    execute(argv + [cell["image"]])
                    wait_ready(api_port)
                    info = json.loads(execute(["docker", "inspect", backend]).stdout)[0]
                    config = info["HostConfig"]
                    if [config["NanoCpus"], config["Memory"], config["MemorySwap"]] != [
                        2000000000,
                        3221225472,
                        3221225472,
                    ]:
                        raise RuntimeError("uncontrolled backend resource limits")
                    env = {
                        **os.environ,
                        "TARGET_URL": f"http://127.0.0.1:{api_port}",
                        "TEST_DATABASE_URL": f"postgresql://postgres:{password}@127.0.0.1:{db_port}/{database}",
                    }
                    run = subprocess.run(
                        [
                            "uv",
                            "run",
                            "-m",
                            "pytest",
                            "tests/contract",
                            "tests/multilanguage",
                            "-q",
                        ],
                        env=env,
                        capture_output=True,
                        text=True,
                    )
                    (directory / "pytest.log").write_text(run.stdout + run.stderr)
                    row = {
                        "cell": cell,
                        "status": "pass" if run.returncode == 0 else "fail",
                        "tls_verified": tls,
                        "image_id": info["Image"],
                        "resources": {k: config[k] for k in ("NanoCpus", "Memory", "MemorySwap")},
                    }
                    results.append(row)
                    atomic_json(output / "verification.json", results)
                    print(cell["id"] + ": " + row["status"], flush=True)
                    if run.returncode:
                        raise RuntimeError(
                            "shared HTTP tests failed; inspect " + str(directory / "pytest.log")
                        )
                finally:
                    log = subprocess.run(
                        ["docker", "logs", "--tail", "300", backend], capture_output=True, text=True
                    )
                    (directory / "backend.log").write_text(log.stdout + log.stderr)
                    subprocess.run(["docker", "rm", "-f", backend], capture_output=True)
                if tls:
                    negative = [
                        v.replace("@db/", "@wrong-db/") if v.startswith("DATABASE_URL=") else v
                        for v in argv
                    ]
                    try:
                        execute(negative + [cell["image"]])
                        deadline = time.monotonic() + 12
                        while time.monotonic() < deadline:
                            state = execute(
                                ["docker", "inspect", "--format", "{{.State.Running}}", backend]
                            ).stdout.strip()
                            if state == "false":
                                break
                            try:
                                health = httpx.get(
                                    f"http://127.0.0.1:{api_port}/health/ready", timeout=2
                                )
                                if health.status_code == 200:
                                    raise RuntimeError(
                                        "database TLS accepted a mismatched hostname"
                                    )
                            except httpx.HTTPError:
                                pass
                            time.sleep(0.5)
                        row["tls_hostname_rejected"] = True
                        atomic_json(output / "verification.json", results)
                    finally:
                        log = subprocess.run(
                            ["docker", "logs", "--tail", "100", backend],
                            capture_output=True,
                            text=True,
                        )
                        (directory / "tls-negative.log").write_text(log.stdout + log.stderr)
                        subprocess.run(["docker", "rm", "-f", backend], capture_output=True)
        finally:
            logs = subprocess.run(
                ["docker", "logs", "--tail", "100", db], capture_output=True, text=True
            )
            (output / "database.log").write_text(logs.stdout + logs.stderr)
            subprocess.run(["docker", "rm", "-f", "-v", db], capture_output=True)
            if network_created:
                subprocess.run(["docker", "network", "rm", network], capture_output=True)
    return results


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["list", "build", "verify"])
    parser.add_argument(
        "--language", "--languages", dest="language", choices=LANGUAGES, action="append"
    )
    parser.add_argument("--output", type=Path, default=Path("artifacts/language-verification"))
    parser.add_argument("--db-port", type=int, default=55440)
    parser.add_argument("--api-port", type=int, default=58040)
    parser.add_argument("--no-tls", action="store_true")
    args = parser.parse_args()
    selected = args.language or tuple(
        name for name in LANGUAGES if (Path("runtimes") / name / "lock.json").exists()
    )
    if args.command == "list":
        print(json.dumps(cells(selected), indent=2))
    elif args.command == "build":
        build(selected)
    else:
        verify(selected, args.output, args.db_port, args.api_port, not args.no_tls)


if __name__ == "__main__":
    main()
