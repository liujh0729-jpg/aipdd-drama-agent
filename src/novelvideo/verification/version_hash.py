"""Automatic version hashes for director-OS trace rows.

Three version fields must ship with every `live_edit_traces` row. None
of them may be hand-entered — they all come from canonical sources:

- `registry_version` hashes the current `verification.db` canonical
  failure-mode defs only. Fallback double-reads (project-local
  historical rows during the phase-1-to-phase-2 transition) must NOT
  contribute to this hash; otherwise two projects with divergent legacy
  state would compute different registry versions for the same run
  and training data would be silently poisoned.

- `prompt_version` hashes the actual prompt bytes sent to the model for
  this beat attempt. It is *not* a hash of the code template. Whatever
  the caller finally composes (style lock + negatives + panel rules +
  per-beat instruction) is what must hash. Call
  `compute_prompt_version_from_artifact(bytes)` after the prompt is
  assembled and before the model call.

- `sketch_format_version` is a stable constant pointing into the
  `sketch_format_versions` table in `director_training.db`.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

import aiosqlite


SKETCH_FORMAT_VERSION = "v1"


async def compute_registry_version(db_defs: aiosqlite.Connection) -> str:
    """Canonical hash of active failure-mode defs — verification.db only.

    Callers pass an aiosqlite connection already opened against the
    user-shared verification DB. The function never opens a project DB
    and never unions with any fallback source.
    """
    async with db_defs.execute(
        """
        SELECT code, layer, detection, prevention_rule, correction_template,
               negative_prompt_clause, gate_enabled
        FROM sketch_failure_mode_defs
        ORDER BY code
        """
    ) as cursor:
        rows = await cursor.fetchall()
    canonical = [
        {
            "code": row[0],
            "layer": row[1],
            "detection": row[2],
            "prevention_rule": row[3] or "",
            "correction_template": row[4] or "",
            "negative_prompt_clause": row[5] or "",
            "gate_enabled": int(row[6] or 0),
        }
        for row in rows
    ]
    blob = json.dumps(canonical, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:12]


def compute_prompt_version_from_artifact(prompt_bytes: bytes) -> str:
    """Hash the actual bytes of the prompt sent to the model.

    Mirrors the bytes that `artifact_store.write_text` would hash, so the
    returned value equals `sha256(prompt_bytes)[:12]`. We re-derive it
    here so callers don't have to round-trip through the artifact store
    just to get the short hash.
    """
    return hashlib.sha256(prompt_bytes).hexdigest()[:12]
