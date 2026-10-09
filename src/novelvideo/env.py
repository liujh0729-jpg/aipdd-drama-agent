"""Environment bootstrap helpers."""

from __future__ import annotations

from pathlib import Path

from dotenv import load_dotenv


def load_project_dotenv(*, override: bool = False) -> None:
    """Load repo-level and cwd-level ``.env`` files.

    Runtime environment variables keep priority by default. This keeps local
    development convenient without making production depend on a dotenv file.
    """
    project_root = Path(__file__).resolve().parents[2]
    load_dotenv(project_root / ".env", override=override)
    load_dotenv(override=override)
