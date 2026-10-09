from __future__ import annotations

import re
from pathlib import Path

from fastapi.testclient import TestClient


def _size_to_bytes(value: str, unit: str) -> int:
    multiplier = {"k": 1024, "m": 1024 * 1024, "g": 1024 * 1024 * 1024}[unit.lower()]
    return int(value) * multiplier


def test_api_middleware_allows_large_project_uploads() -> None:
    from novelvideo.api.app import create_app

    client = TestClient(create_app())
    response = client.post(
        "/api/v1/projects/demo/scenes/Hall/custom/upload",
        content=b"x" * (6 * 1024 * 1024),
        headers={"content-type": "multipart/form-data; boundary=x"},
    )

    assert response.status_code != 413


def test_api_middleware_keeps_small_limit_for_non_upload_json() -> None:
    from novelvideo.api.app import create_app

    client = TestClient(create_app())
    response = client.post(
        "/api/v1/projects/demo/freezone/canvases/canvas_1",
        content=b"x" * (6 * 1024 * 1024),
        headers={"content-type": "application/json"},
    )

    assert response.status_code == 413


def test_freezone_audio_voice_oversize_returns_business_error() -> None:
    from novelvideo.api.app import create_app

    client = TestClient(create_app())
    body = b"x" * (6 * 1024 * 1024)
    response = client.post(
        "/api/v1/projects/demo/freezone/audio/voices",
        content=body,
        headers={
            "content-type": "multipart/form-data; boundary=x",
            "content-length": str(len(body)),
        },
    )

    assert response.status_code == 200
    assert response.json() == {
        "ok": False,
        "error": "参考音频超过 5MB 上限，请压缩或裁剪后重新上传",
        "data": {
            "code": "freezone_audio_voice_too_large",
            "field": "file",
            "limit": 5 * 1024 * 1024,
            "got": len(body),
        },
    }
