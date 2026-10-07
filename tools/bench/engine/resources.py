"""One authoritative wrapper tree in checkout and wheel."""
from pathlib import Path


def wrapper_path(name: str) -> Path:
    package = Path(__file__).parent / 'k6' / name
    if package.exists():
        return package
    return Path(__file__).resolve().parents[3] / 'benchmarks/k6/engine' / name


def runtime_root() -> Path:
    package = Path(__file__).parent / 'attestation' / 'runtimes'
    if package.exists():
        return package
    return Path(__file__).resolve().parents[3] / 'runtimes'


def dependency_lock_path(relative: str) -> Path:
    package = Path(__file__).parent / 'attestation' / relative
    if package.exists():
        return package
    return Path(__file__).resolve().parents[3] / relative
