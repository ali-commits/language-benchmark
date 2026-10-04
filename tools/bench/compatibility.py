"""Validate recorded runtime inputs and probe installed application imports."""

import hashlib
import importlib
import platform
import re
import sys
from dataclasses import asdict
from pathlib import Path

from .models import RuntimeCell


def validate_runtime_lock(data: dict) -> dict:
    if not re.fullmatch(r".+@sha256:[a-f0-9]{64}", data.get("base", "")):
        raise ValueError("base must be pinned by digest")
    if not data.get("runtimes"):
        raise ValueError("no pinned Python sources")
    for source in data["runtimes"]:
        if not re.fullmatch(r"3\.\d+\.\d+(?:rc\d+)?", source.get("version", "")):
            raise ValueError("exact Python release required")
        if not re.fullmatch(r"[a-f0-9]{64}", source.get("sha256", "")):
            raise ValueError("source checksum required")
    for raw in data["cells"]:
        RuntimeCell(**raw)
    return data


def probe_cell(cell: RuntimeCell) -> dict:
    result = asdict(cell)
    reasons = list(cell.reasons)
    if platform.python_version() != cell.python:
        reasons.append("interpreter release does not match cell")
    for name in ("fastapi", "pydantic", "sqlalchemy", "psycopg", "argon2", "jwt"):
        try:
            importlib.import_module(name)
        except Exception as e:
            reasons.append(f"{name}: {type(e).__name__}: {e}")
    result.update(supported=not reasons, reasons=reasons, executable=sys.executable)
    return result


def lock_checksum(path: str) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()
