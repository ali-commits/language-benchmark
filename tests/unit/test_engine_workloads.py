import subprocess
import sys
from pathlib import Path

import pytest

from tools.bench.engine.types import Environment, Hook, Host, Workload
from tools.bench.engine.workloads import TrialContext, prepare_workload, run_hook, stage_workload


def workload(path, dependencies=()):
    return Workload(script=path, dependencies=list(dependencies), metrics={})


def test_bundle_paths_and_hashes(tmp_path):
    source = tmp_path / "source space"
    source.mkdir()
    script = source / "main.js"
    script.write_text("import './dep.js'; export default function() {}")
    dep = source / "dep.js"
    dep.write_text("export const n=1;")
    prepared = prepare_workload(workload(script, [dep]), tmp_path / "bundle space")
    script.write_text("changed")
    assert prepared.script.read_text().startswith("import")
    assert set(prepared.checksums) == {"main.js", "dep.js"}
    staged = stage_workload(prepared, Host(transport="local"), tmp_path / "remote space")
    assert (Path(staged.directory) / "main.js").read_text().startswith("import")


@pytest.mark.parametrize(
    "text",
    [
        "import './missing.js';",
        "import 'https://example.com/x.js';",
        "const x = await import('./x.js');",
    ],
)
def test_undeclared_import_rejected(tmp_path, text):
    path = tmp_path / "main.js"
    path.write_text(text)
    with pytest.raises(ValueError):
        prepare_workload(workload(path), tmp_path / "bundle")


def test_escape_rejected(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    path = root / "main.js"
    path.write_text("")
    outside = tmp_path / "out.js"
    outside.write_text("")
    with pytest.raises(ValueError):
        prepare_workload(workload(path, [outside]), tmp_path / "bundle")


def test_hook_local_argv_and_secret_redaction(tmp_path, monkeypatch):
    monkeypatch.setenv("TEST_HOOK_SECRET", "do-not-print")
    from tools.bench.engine.types import SecretRef

    env = Environment(
        backend=Host(transport="local", workdir=tmp_path), generator=Host(transport="local")
    )
    context = TrialContext(environment=env, queue_id="q", job_id="j", trial_id="t")
    receipt = run_hook(
        Hook(
            argv=[sys.executable, "-c", "import os; print(os.getenv('VALUE'))"],
            env={"VALUE": SecretRef(env="TEST_HOOK_SECRET")},
        ),
        context,
    )
    assert receipt.returncode == 0
    assert "do-not-print" not in repr(receipt)
    with pytest.raises(RuntimeError, match="hook") as failure:
        run_hook(
            Hook(
                argv=[sys.executable, "-c", "import os,sys;print(os.getenv('VALUE'));sys.exit(1)"],
                env={"VALUE": SecretRef(env="TEST_HOOK_SECRET")},
            ),
            context,
        )
    assert "do-not-print" not in str(failure.value)


def test_hook_timeout(tmp_path):
    env = Environment(backend=Host(transport="local"), generator=Host(transport="local"))
    with pytest.raises(TimeoutError):
        run_hook(
            Hook(argv=[sys.executable, "-c", "import time; time.sleep(10)"], timeout=0.05),
            TrialContext(environment=env, queue_id="q", job_id="j", trial_id="t"),
        )


def test_incomplete_bundle_rejected(tmp_path):
    p = tmp_path / "main.js"
    p.write_text("")
    prepared = prepare_workload(workload(p), tmp_path / "bundle")
    prepared.script.write_text("corrupt")
    with pytest.raises(ValueError, match="checksum"):
        stage_workload(prepared, Host(transport="local"), tmp_path / "dest")


def test_multiline_remote_import_rejected(tmp_path):
    p = tmp_path / "main.js"
    p.write_text("import {\n thing\n} from 'https://remote.example/mod.js';")
    with pytest.raises(ValueError):
        prepare_workload(workload(p), tmp_path / "bundle")


def test_real_loopback_ssh_stages_verified_inputs_and_argv(tmp_path, monkeypatch):
    import os
    import pwd
    import shutil
    import socket
    import time

    from tools.bench.engine.targets import execute_host
    from tools.bench.engine.workloads import _TRANSFER_TREE, _ssh

    if not all(shutil.which(command) for command in ("sshd", "ssh", "ssh-keygen")):
        pytest.skip("local OpenSSH server/client not installed")
    key = tmp_path / "ssh key"
    subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(key)], check=True)
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    config = tmp_path / "sshd_config"
    config.write_text(
        f'Port {port}\nListenAddress 127.0.0.1\nHostKey "{key}"\nPidFile "{tmp_path}/pid"\nAuthorizedKeysFile "{key}.pub"\nStrictModes no\nUsePAM no\nPasswordAuthentication no\nKbdInteractiveAuthentication no\n'
    )
    pub = Path(str(key) + ".pub").read_text().split()
    known = tmp_path / "known_hosts"
    known.write_text(f"[127.0.0.1]:{port} {pub[0]} {pub[1]}\n")
    ssh = shutil.which("ssh")
    sshd = shutil.which("sshd")
    binpath = tmp_path / "bin"
    binpath.mkdir()
    wrapper = binpath / "ssh"
    wrapper.write_text(
        f'#!{sys.executable}\nimport os,sys\nos.execv({ssh!r},[{ssh!r},"-o",{("UserKnownHostsFile=" + str(known))!r},"-o","StrictHostKeyChecking=yes",*sys.argv[1:]])\n'
    )
    wrapper.chmod(0o755)
    process = subprocess.Popen(
        [sshd, "-D", "-e", "-f", str(config)], stdout=subprocess.DEVNULL, stderr=subprocess.PIPE
    )
    try:
        for _ in range(100):
            if process.poll() is not None:
                pytest.fail("disposable loopback sshd failed: " + process.stderr.read().decode())
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=0.1):
                    break
            except OSError:
                time.sleep(0.01)
        monkeypatch.setenv("PATH", str(binpath) + os.pathsep + os.environ["PATH"])
        host = Host(
            transport="ssh",
            host="127.0.0.1",
            port=port,
            user=pwd.getpwuid(os.getuid()).pw_name,
            identity_file=key,
            workdir=tmp_path,
        )
        result = execute_host(
            host,
            [sys.executable, "-c", "import sys;print(sys.argv[1])", "$(echo unsafe); actual space"],
        )
        assert result.stdout.strip() == "$(echo unsafe); actual space"
        script = tmp_path / "main.js"
        script.write_text("export default function() {}")
        prepared = prepare_workload(workload(script), tmp_path / "bundle")
        staged = stage_workload(prepared, host, tmp_path / "remote space")
        assert Path(staged.script).read_text() == prepared.script.read_text()
        from tools.bench.engine.workloads import fetch_file

        fetched = tmp_path / "downloaded file.js"
        fetch_file(host, staged.script, fetched)
        assert fetched.read_bytes() == prepared.script.read_bytes()
        with pytest.raises(RuntimeError):
            _ssh(
                host,
                _TRANSFER_TREE,
                {"destination": str(tmp_path / "incomplete"), "checksums": {"missing": "bad"}},
                b"bad archive",
            )
        assert not (tmp_path / "incomplete").exists()
        with pytest.raises(TimeoutError):
            execute_host(host, [sys.executable, "-c", "import time;time.sleep(5)"], timeout=0.05)
    finally:
        process.terminate()
        process.wait(timeout=5)
        process.stderr.close()


def test_local_file_transfer_is_atomic_and_verified(tmp_path):
    from tools.bench.engine.workloads import transfer_file

    source = tmp_path / "a"
    source.write_bytes(b"input")
    destination = tmp_path / "space path" / "b"
    checksum = transfer_file(source, Host(transport="local"), str(destination))
    import hashlib

    assert checksum == hashlib.sha256(b"input").hexdigest()
    assert destination.read_bytes() == b"input"
    with pytest.raises(ValueError):
        transfer_file(source, Host(transport="local"), str(destination))
