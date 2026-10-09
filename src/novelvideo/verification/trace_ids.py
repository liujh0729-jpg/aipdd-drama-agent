"""Stable identifier factories for director-OS rows.

Identifiers embed enough context to be human-scannable in SQLite
inspectors while still being collision-resistant for the volume we
expect (single-user, thousands of traces per week).

Naming grammar:
- trace_id:   `trace_<project_slug>_ep<NNN>_b<NN>_<source_run_short>_<hash8>`
- sample_id:  `sample_<sketch_hash_prefix>_<project_slug>_ep<NNN>_b<NN>`
- reject_id:  `reject_<sketch_hash_prefix>_<short_timestamp>`
- event_id:   `evt_<trace_hash_prefix>_<short_timestamp>`
"""

from __future__ import annotations

import hashlib
import re
import time


_SLUG_RE = re.compile(r"[^a-zA-Z0-9_]+")


def _slugify(value: str, max_len: int = 24) -> str:
    cleaned = _SLUG_RE.sub("_", str(value)).strip("_").lower()
    return cleaned[:max_len] or "unknown"


def _short_hash(*parts: str, length: int = 8) -> str:
    joined = "::".join(parts)
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()[:length]


def _short_run_id(source_run_id: str, length: int = 12) -> str:
    # Existing scopes look like "edit_execute__09b5c6fb34bc" — keep the
    # tail bits since those are the actually unique part.
    tail = source_run_id.split("__", 1)[-1] if source_run_id else ""
    if tail:
        return tail[:length]
    return _short_hash(source_run_id, length=length)


def _short_timestamp() -> str:
    return str(int(time.time() * 1000))


def make_trace_id(
    *,
    project: str,
    episode_number: int,
    beat_number: int,
    source_run_id: str,
    salt: str = "",
) -> str:
    project_slug = _slugify(project)
    run_short = _short_run_id(source_run_id)
    hash_component = _short_hash(
        project_slug,
        f"ep{episode_number}",
        f"b{beat_number}",
        source_run_id,
        salt or _short_timestamp(),
    )
    return f"trace_{project_slug}_ep{int(episode_number):03d}_b{int(beat_number):02d}_{run_short}_{hash_component}"


def make_sample_id(
    *,
    sketch_sha256: str,
    project: str,
    episode_number: int,
    beat_number: int,
) -> str:
    project_slug = _slugify(project)
    prefix = (sketch_sha256 or "nohash")[:12]
    return f"sample_{prefix}_{project_slug}_ep{int(episode_number):03d}_b{int(beat_number):02d}"


def make_reject_id(*, sketch_sha256: str) -> str:
    prefix = (sketch_sha256 or "nohash")[:12]
    return f"reject_{prefix}_{_short_timestamp()}"


def make_event_id(*, trace_id: str) -> str:
    trace_prefix = (trace_id or "notrace")[-12:]
    return f"evt_{trace_prefix}_{_short_timestamp()}"
