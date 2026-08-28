"""Writing bytes to disk. Deterministic, so identical input gives an identical file."""
from __future__ import annotations

import gzip
import hashlib
import io
import json
import os
import tempfile
from pathlib import Path


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def gzip_bytes(data: bytes) -> bytes:
    """gzip with mtime=0 and no filename, so the output depends only on the input."""
    buf = io.BytesIO()
    with gzip.GzipFile(fileobj=buf, mode="wb", compresslevel=9, mtime=0) as gz:
        gz.write(data)
    return buf.getvalue()


def read_gzip(path: Path) -> bytes:
    with gzip.open(path, "rb") as fh:
        return fh.read()


def _atomic_write(path: Path, payload: bytes) -> None:
    """Write via a temp file in the same directory, then replace.

    A run killed mid-write must not leave a half file that a later run would
    mistake for a complete leg.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".tmp-", suffix=".part")
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(payload)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def write_gzip(path: Path, data: bytes) -> dict:
    """Store raw bytes gzipped. Returns the facts the manifest records about them."""
    payload = gzip_bytes(data)
    _atomic_write(path, payload)
    return {
        "file": path.name,
        "bytes_raw": len(data),
        "bytes_stored": len(payload),
        "sha256_raw": sha256(data),
    }


def write_json(path: Path, obj) -> None:
    text = json.dumps(obj, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    _atomic_write(path, text.encode("utf-8"))


def read_json(path: Path):
    with path.open("rb") as fh:
        return json.loads(fh.read().decode("utf-8"))
