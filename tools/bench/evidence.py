"""Shared immutable validity checks for recorded backend evidence."""

import re

_OPEN_FILES = re.compile(
    r"too\s+many\s+open\s+files|\bEMFILE\b|\b(?:os\s+error|errno)\s*[:=]?\s*24\b", re.I
)


def backend_open_file_exhaustion(text: str) -> bool:
    return bool(_OPEN_FILES.search(text))
