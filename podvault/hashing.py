"""
File-checksum utility, shared by the download path (which stamps a baseline
hash on every newly-downloaded episode) and the integrity-check background
job (which re-hashes and compares against that baseline to catch corruption).

A standalone leaf module — no dependency on `download/` or `database/` — so
both of those can import it without a circular import.
"""

import hashlib
from pathlib import Path
from typing import Union

CHUNK_SIZE = 1024 * 1024  # 1 MB


def compute_checksum(file_path: Union[str, Path], chunk_size: int = CHUNK_SIZE) -> str:
    """SHA-256 hex digest of a file's contents, read in chunks so large audio files don't need to fit in memory at once."""
    hasher = hashlib.sha256()
    with open(file_path, 'rb') as f:
        for chunk in iter(lambda: f.read(chunk_size), b''):
            hasher.update(chunk)
    return hasher.hexdigest()
