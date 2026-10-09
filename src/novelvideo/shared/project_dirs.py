"""Shared project directory conventions for CE and EE registries."""

from __future__ import annotations

from pathlib import Path


def default_project_dirs(owner_username: str, project_name: str) -> tuple[str, str, str]:
    from novelvideo import config

    return (
        str((Path(config.OUTPUT_DIR) / owner_username / project_name).resolve()),
        str((Path(config.STATE_DIR) / owner_username / project_name).resolve()),
        str((Path(config.RUNTIME_DIR) / owner_username / project_name).resolve()),
    )
