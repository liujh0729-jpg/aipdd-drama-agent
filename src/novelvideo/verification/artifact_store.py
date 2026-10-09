"""Content-addressable artifact store for director-OS data.

All large payloads (prompts, model responses, gate verdicts, grid images,
individual sketches referenced by traces) land under a single
`artifacts/<sha256_prefix>/<sha256>.<ext>` tree so identical bytes
are naturally deduplicated. Call sites only ever see the resolved
path plus the hash; they never pick filenames by hand.

Only the `artifact_store` module should own the layout — callers must
not invent their own subdirectories under `global_shared_artifacts_dir`.
"""

from __future__ import annotations

import gzip
import hashlib
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ArtifactRef:
    path: Path
    sha256: str
    size_bytes: int


def compute_sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _target_path(root: Path, sha: str, ext: str) -> Path:
    ext_clean = ext.lstrip(".")
    prefix = sha[:2]
    return root / prefix / f"{sha}.{ext_clean}"


def write_bytes(root: Path, data: bytes, *, ext: str) -> ArtifactRef:
    """Write raw bytes content-addressably. Idempotent on identical bytes."""
    sha = compute_sha256(data)
    target = _target_path(root, sha, ext)
    if not target.exists():
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    return ArtifactRef(path=target, sha256=sha, size_bytes=len(data))


def write_text(root: Path, text: str, *, ext: str = "txt") -> ArtifactRef:
    return write_bytes(root, text.encode("utf-8"), ext=ext)


def write_json_gz(root: Path, payload: str | bytes) -> ArtifactRef:
    """Gzip-compress a JSON string then store. Use for verdicts / responses."""
    if isinstance(payload, str):
        payload = payload.encode("utf-8")
    compressed = gzip.compress(payload)
    return write_bytes(root, compressed, ext="json.gz")


def copy_file_in(root: Path, src: Path, *, ext: str | None = None) -> ArtifactRef:
    """Read a file from disk, hash its bytes, land it into the store.

    Idempotent: if an identical file already exists at the target path,
    the incoming file is not rewritten. Used by backfill / replay flows
    that want to suck existing `sketches/epXXX/beat_XX.png` files into
    the shared store without picking new filenames by hand.
    """
    data = Path(src).read_bytes()
    resolved_ext = ext if ext is not None else Path(src).suffix.lstrip(".") or "bin"
    return write_bytes(root, data, ext=resolved_ext)


def resolve_path(root: Path, sha: str, ext: str) -> Path:
    """Pure path arithmetic — does not check existence."""
    return _target_path(root, sha, ext)


def read_bytes(path: Path) -> bytes:
    return Path(path).read_bytes()


def read_json_gz_text(path: Path) -> str:
    data = Path(path).read_bytes()
    return gzip.decompress(data).decode("utf-8")
