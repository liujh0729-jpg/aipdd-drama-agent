from __future__ import annotations

import tomllib
from pathlib import Path


def test_aliyun_media_relay_sdk_is_packaged() -> None:
    pyproject = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))
    dependencies = {
        dependency.split("[", 1)[0].split("=", 1)[0].split("<", 1)[0].split(">", 1)[0].strip()
        for dependency in pyproject["project"]["dependencies"]
    }

    assert "oss2" in dependencies
