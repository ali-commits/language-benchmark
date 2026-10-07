"""One authoritative wrapper tree in checkout and wheel."""
from pathlib import Path


def wrapper_path(name):
    package = Path(__file__).parent / 'k6' / name
    if package.exists():
        return package
    return Path(__file__).resolve().parents[3] / 'benchmarks/k6/engine' / name
