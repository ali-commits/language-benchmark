"""Reproducible k6 source bundles, atomic verified staging and declared hooks."""

import hashlib
import io
import json
import os
import shlex
import shutil
import subprocess
import tarfile
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

from .targets import execute_host, tree_checksums
from .types import Environment, Hook, Host, Workload


@dataclass(frozen=True)
class PreparedWorkload:
    directory: Path
    script: Path
    checksums: dict[str, str]
    identity: str


@dataclass(frozen=True)
class StagedWorkload:
    directory: str
    script: str
    checksums: dict[str, str]
    identity: str


@dataclass(frozen=True)
class TrialContext:
    environment: Environment
    queue_id: str
    job_id: str
    trial_id: str


@dataclass(frozen=True)
class HookReceipt:
    host: str
    returncode: int
    elapsed_seconds: float


def _js_tokens(text: str):
    """Lex comments/strings/templates; reject ambiguous slash and escaped identifiers.

    This intentionally is not a JavaScript parser. Regex literals and division
    operators are unsupported to avoid lexical-goal ambiguity hiding imports.
    Template substitutions are recursively scanned as executable code.
    """
    tokens = []
    index = 0

    def scan(*, template_expression=False):
        nonlocal index
        depth = 0
        while index < len(text):
            char = text[index]
            if char.isspace():
                index += 1
                continue
            if text.startswith("//", index):
                index += 2
                while index < len(text) and text[index] not in "\r\n\u2028\u2029":
                    index += 1
                continue
            if text.startswith("/*", index):
                end = text.find("*/", index + 2)
                if end < 0:
                    raise ValueError("unterminated workload comment")
                index = end + 2
                continue
            if char == "/":
                raise ValueError(
                    "regex literals and slash operators unsupported in reproducible mode"
                )
            if char == "\\":
                raise ValueError("escaped workload identifiers unsupported in reproducible mode")
            if char in ("'", '"'):
                quote = char
                index += 1
                begin = index
                while index < len(text) and text[index] != quote:
                    if text[index] == "\\":
                        index += 2
                    else:
                        if text[index] in "\r\n":
                            raise ValueError("unterminated workload string")
                        index += 1
                if index >= len(text):
                    raise ValueError("unterminated workload string")
                tokens.append(("string", text[begin:index]))
                index += 1
                continue
            if char == "`":
                index += 1
                while index < len(text):
                    if text[index] == "\\":
                        index += 2
                    elif text[index] == "`":
                        index += 1
                        tokens.append(("template", ""))
                        break
                    elif text.startswith("${", index):
                        index += 2
                        tokens.append(("punctuation", "("))
                        scan(template_expression=True)
                        tokens.append(("punctuation", ")"))
                    else:
                        index += 1
                else:
                    raise ValueError("unterminated workload template")
                continue
            if char.isalpha() or char in "_$":
                begin = index
                index += 1
                while index < len(text) and (text[index].isalnum() or text[index] in "_$"):
                    index += 1
                tokens.append(("identifier", text[begin:index]))
                continue
            if char == "}":
                if template_expression and depth == 0:
                    index += 1
                    return
                depth -= 1
            elif char == "{":
                depth += 1
            tokens.append(("punctuation", char))
            index += 1
        if template_expression:
            raise ValueError("unterminated workload template expression")

    scan()
    return tokens


def _source_references(text: str):
    """Return static module/data references; unsupported import syntax fails closed."""
    tokens = _js_tokens(text)
    references = []
    for index, (kind, value) in enumerate(tokens):
        if kind != "identifier":
            continue
        following = tokens[index + 1 :]
        if value == "require":
            # Reject aliases too: const load = require could hide subsequent calls.
            raise ValueError("CommonJS workload imports are not reproducible")
        if value == "open" and following and following[0][1] == "(":
            if (
                len(following) < 3
                or following[1][0] != "string"
                or following[2][1] not in (",", ")")
            ):
                raise ValueError("workload data path must be a static string")
            references.append(("data", following[1][1]))
        if value not in ("import", "export"):
            continue
        if not following:
            raise ValueError("incomplete workload import/export")
        if value == "export" and following[0][1] not in ("*", "{"):
            continue
        if value == "import" and following[0][0] == "string":
            references.append(("module", following[0][1]))
            continue
        if value == "import" and following[0][1] in ("(", "."):
            raise ValueError("dynamic/meta workload imports are not reproducible")
        # Binding lists may span lines/comments. Only a static from string is allowed.
        found = False
        for offset, token in enumerate(following):
            if token[1] in (";", "import", "export"):
                break
            if token == ("identifier", "from"):
                if offset + 1 >= len(following) or following[offset + 1][0] != "string":
                    raise ValueError("workload import path must be a static string")
                references.append(("module", following[offset + 1][1]))
                found = True
                break
        if value == "import" and not found:
            raise ValueError("unsupported workload import syntax")
    return references


def prepare_workload(workload: Workload, destination: Path) -> PreparedWorkload:
    root = workload.script.resolve().parent
    destination = destination.resolve()
    if destination.exists():
        raise ValueError("bundle destination already exists")
    if destination == root:
        raise ValueError("bundle cannot replace source root")
    paths = [workload.script, *workload.dependencies]
    declared = {}
    for source in paths:
        path = source.resolve()
        try:
            relative = path.relative_to(root).as_posix()
        except ValueError:
            raise ValueError("workload dependency escapes script root") from None
        if not path.is_file() or source.is_symlink():
            raise ValueError("workload input must be a regular file")
        declared[relative] = path
    for name, path in declared.items():
        if path.suffix not in (".js", ".mjs", ".cjs", ".ts", ".jsx", ".tsx"):
            continue
        for reference_kind, dependency in _source_references(path.read_text()):
            if "\\" in dependency:
                raise ValueError("escaped workload reference unsupported in reproducible mode")
            if reference_kind == "module" and (dependency == "k6" or dependency.startswith("k6/")):
                continue
            if reference_kind == "module" and not dependency.startswith(("./", "../")):
                raise ValueError("remote or package workload imports must be bundled locally")
            resolved = (path.parent / dependency).resolve()
            try:
                relative = resolved.relative_to(root).as_posix()
            except ValueError:
                raise ValueError("workload reference escapes source root") from None
            if relative not in declared:
                raise ValueError("workload reference is undeclared")
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="bench-workload-", dir=destination.parent) as temporary:
        bundle = Path(temporary) / "bundle"
        bundle.mkdir()
        for name, path in declared.items():
            output = bundle / name
            output.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, output)
        checksums = tree_checksums(bundle)
        identity = hashlib.sha256(json.dumps(checksums, sort_keys=True).encode()).hexdigest()
        os.rename(bundle, destination)
    return PreparedWorkload(destination, destination / workload.script.name, checksums, identity)


_TRANSFER_TREE = """import hashlib,json,os,pathlib,shutil,stat,sys,tarfile,tempfile
p=json.loads(sys.argv[1]); dest=pathlib.Path(p['destination']);dest.parent.mkdir(parents=True,exist_ok=True)
if dest.exists(): raise RuntimeError('destination already exists')
with tempfile.TemporaryDirectory(prefix='.bench-transfer-',dir=dest.parent) as temp:
 root=pathlib.Path(temp)/'inputs';root.mkdir();modes={}
 with tarfile.open(fileobj=sys.stdin.buffer,mode='r|') as archive:
  for m in archive:
   member=pathlib.PurePosixPath(m.name)
   if member.is_absolute() or '..' in member.parts or not (m.isfile() or m.isdir() or m.issym()): raise RuntimeError('unsafe archive')
   target=root/member
   target.resolve().relative_to(root.resolve())
   if m.issym():
    link=pathlib.PurePosixPath(m.linkname)
    if link.is_absolute(): raise RuntimeError('unsafe link')
    (target.parent/link).resolve().relative_to(root.resolve())
   if sys.version_info >= (3,12): archive.extract(m,root,filter='data')
   else: archive.extract(m,root)
   if not m.issym(): modes[target]=m.mode & 0o7777
 for target,mode in sorted(modes.items(),key=lambda item:len(item[0].parts),reverse=True): os.chmod(target,mode)
 actual={}
 for f in sorted(root.rglob('*')):
  mode=stat.S_IMODE(f.lstat().st_mode)
  if f.is_symlink(): content=('symlink:'+str(mode)+':'+os.readlink(f)).encode()
  elif f.is_file(): content=('file:'+str(mode)+':').encode()+f.read_bytes()
  elif f.is_dir(): content=('directory:'+str(mode)+':').encode()
  else: raise RuntimeError('unsupported input type')
  actual[f.relative_to(root).as_posix()]=hashlib.sha256(content).hexdigest()
 if actual != p['checksums']: raise RuntimeError('transfer checksum mismatch')
 os.rename(root,dest)
"""


def _ssh(host: Host, code: str, payload: dict, data: bytes, timeout=600):
    argv = ["ssh", "-o", "BatchMode=yes", "-p", str(host.port)]
    if host.identity_file:
        argv += ["-i", str(host.identity_file)]
    argv += [
        "--",
        f"{host.user}@{host.host}" if host.user else host.host,
        "python3 -c " + shlex.quote(code) + " " + shlex.quote(json.dumps(payload)),
    ]
    try:
        result = subprocess.run(argv, input=data, capture_output=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        raise TimeoutError("input transfer timed out") from None
    if result.returncode:
        raise RuntimeError("input transfer failed verification")
    return result.stdout


def transfer_tree(source: Path, host: Host, destination: str, *, expected_checksums=None):
    checksums = tree_checksums(source)
    if expected_checksums is not None and checksums != expected_checksums:
        raise ValueError("source checksum mismatch before transfer")
    if host.transport == "local":
        dest = Path(destination)
        if dest.exists():
            raise ValueError("transfer destination already exists")
        dest.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix=".bench-transfer-", dir=dest.parent) as temporary:
            staged = Path(temporary) / "inputs"
            shutil.copytree(source, staged, symlinks=True)
            if tree_checksums(staged) != checksums:
                raise ValueError("transfer checksum mismatch")
            os.rename(staged, dest)
    else:
        with io.BytesIO() as stream:
            with tarfile.open(fileobj=stream, mode="w") as archive:
                for path in sorted(source.rglob("*")):
                    archive.add(path, arcname=path.relative_to(source).as_posix(), recursive=False)
            _ssh(
                host,
                _TRANSFER_TREE,
                {"destination": destination, "checksums": checksums},
                stream.getvalue(),
            )
    return checksums


def transfer_file(source: Path, host: Host, destination: str):
    data = source.read_bytes()
    checksum = hashlib.sha256(data).hexdigest()
    code = """import hashlib,json,os,pathlib,sys,tempfile
p=json.loads(sys.argv[1]);dest=pathlib.Path(p['destination']);dest.parent.mkdir(parents=True,exist_ok=True)
data=sys.stdin.buffer.read()
if hashlib.sha256(data).hexdigest()!=p['checksum']: raise RuntimeError('checksum mismatch')
fd,tmp=tempfile.mkstemp(prefix='.bench-transfer-',dir=dest.parent)
try:
 with os.fdopen(fd,'wb') as f: f.write(data);f.flush();os.fsync(f.fileno())
 if dest.exists(): raise RuntimeError('destination already exists')
 os.rename(tmp,dest)
finally:
 if os.path.exists(tmp): os.unlink(tmp)
"""
    if host.transport == "ssh":
        _ssh(host, code, {"destination": destination, "checksum": checksum}, data)
    else:
        dest = Path(destination)
        dest.parent.mkdir(parents=True, exist_ok=True)
        if dest.exists():
            raise ValueError("transfer destination already exists")
        with tempfile.NamedTemporaryFile(dir=dest.parent, delete=False) as f:
            temporary = Path(f.name)
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        try:
            if hashlib.sha256(temporary.read_bytes()).hexdigest() != checksum:
                raise ValueError("transfer checksum mismatch")
            os.rename(temporary, dest)
        finally:
            temporary.unlink(missing_ok=True)
    return checksum


def stage_workload(prepared: PreparedWorkload, host: Host, destination: Path | str):
    if tree_checksums(prepared.directory) != prepared.checksums:
        raise ValueError("prepared workload checksum mismatch")
    transfer_tree(prepared.directory, host, str(destination), expected_checksums=prepared.checksums)
    return StagedWorkload(
        str(destination),
        str(Path(destination) / prepared.script.relative_to(prepared.directory)),
        prepared.checksums,
        prepared.identity,
    )


def run_hook(hook: Hook, context: TrialContext) -> HookReceipt:
    host = (
        Host(transport="local")
        if hook.host == "controller"
        else getattr(context.environment, hook.host)
    )
    env = {}
    for key, reference in hook.env.items():
        if reference.env not in os.environ:
            raise ValueError(f"hook environment reference missing: {reference.env}")
        env[key] = os.environ[reference.env]
    start = time.monotonic()
    try:
        execute_host(host, hook.argv, env=env, timeout=hook.timeout)
    except TimeoutError:
        raise TimeoutError("hook timed out") from None
    except RuntimeError:
        raise RuntimeError("hook command failed") from None
    return HookReceipt(hook.host, 0, time.monotonic() - start)


def fetch_file(host: Host, source: str, destination: Path):
    """Fetch a remote artifact with an authenticated checksum and atomic local publish."""
    if destination.exists():
        raise ValueError("fetch destination already exists")
    if host.transport == "local":
        return transfer_file(Path(source), host, str(destination))
    code = "import hashlib,json,pathlib,sys;data=pathlib.Path(json.loads(sys.argv[1])['source']).read_bytes();sys.stdout.buffer.write(hashlib.sha256(data).hexdigest().encode()+b'\\n'+data)"
    response = _ssh(host, code, {"source": source}, b"")
    checksum, separator, data = response.partition(b"\n")
    if not separator or hashlib.sha256(data).hexdigest().encode() != checksum:
        raise ValueError("fetched artifact checksum mismatch")
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=destination.parent, delete=False) as file:
        temporary = Path(file.name)
        file.write(data)
        file.flush()
        os.fsync(file.fileno())
    try:
        os.rename(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)
    return checksum.decode()
