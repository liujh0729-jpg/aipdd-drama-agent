import struct
from pathlib import Path

import pytest


pytestmark = pytest.mark.m09


def _write_png(path: Path, *, width: int = 512, height: int = 768) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    header = b"\x89PNG\r\n\x1a\n"
    ihdr = b"IHDR" + struct.pack(">II", width, height) + b"\x08\x02\x00\x00\x00"
    path.write_bytes(header + struct.pack(">I", len(ihdr) - 4) + ihdr)


def test_seedance2_status_exposes_returned_last_frame_asset(tmp_path):
    from types import SimpleNamespace

    from novelvideo.api.routes.generation import _seedance2_returned_last_frame_status_payload

    project_dir = tmp_path / "output" / "alice" / "project"
    last_frame = project_dir / "videos" / "beats" / "ep001" / "returned_last_frames" / "beat_01.png"
    _write_png(last_frame)

    project_ctx = SimpleNamespace(project_id="proj-1", output_dir=str(project_dir))

    payload = _seedance2_returned_last_frame_status_payload(
        project_ctx=project_ctx,
        output_dir=project_dir,
        episode=1,
        beat_num=1,
        enabled=True,
    )

    assert payload is not None
    assert payload["key"] == "returned_last_frame"
    assert payload["label"] == "返回尾帧 · Beat 1"
    assert payload["media_type"] == "image"
    assert payload["exists"] is True
    assert payload["reference_label"] == "尾帧"
    assert payload["path"] == "videos/beats/ep001/returned_last_frames/beat_01.png"
    # Canonical protected static URL is project-id based after the storage refactor.
    assert payload["url"].startswith(
        "/static/projects/proj-1/videos/beats/ep001/returned_last_frames/beat_01.png?v="
    )
