"""Environment guards for import-time side effects."""

from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager


@contextmanager
def preserve_st_env() -> Iterator[None]:
    """Restore all ``ST_*`` environment variables after a side-effectful block."""
    snapshot = {key: value for key, value in os.environ.items() if key.startswith("ST_")}
    try:
        yield
    finally:
        for key in list(os.environ):
            if key.startswith("ST_") and key not in snapshot:
                del os.environ[key]
        for key, value in snapshot.items():
            os.environ[key] = value
