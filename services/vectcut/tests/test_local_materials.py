from pathlib import Path

import pytest


def _make_video_file(tmp_path: Path) -> Path:
    # A tiny valid enough file for os.path.exists()/isfile() checks; the
    # material construction path we're testing never shells out to ffprobe
    # because duration/width/height are supplied explicitly.
    video_path = tmp_path / "clip.mp4"
    video_path.write_bytes(b"not a real video, just needs to exist")
    return video_path


def test_video_material_local_path_passed_as_remote_url_keeps_usable_path(tmp_path):
    """Regression test: callers that don't pass draft_folder (e.g. add_video_track)
    always pass the caller-supplied video_url through as remote_url. When that
    value is actually a local filesystem path (not http/https), the material
    must still end up with a usable local `path`, not `path=""`."""
    from pyJianYingDraft import Video_material

    video_path = _make_video_file(tmp_path)

    material = Video_material(
        material_type="video",
        remote_url=str(video_path),
        material_name="clip.mp4",
        duration=5.0,
        width=0,
        height=0,
    )

    assert material.path == str(video_path.resolve()) or material.path == str(video_path)
    assert material.remote_url == str(video_path)
    exported = material.export_json()
    assert exported["path"] != ""
    assert exported["remote_url"] == str(video_path)


def test_video_material_true_http_remote_url_still_blanks_path():
    from pyJianYingDraft import Video_material

    material = Video_material(
        material_type="video",
        remote_url="https://example.com/video.mp4",
        material_name="video.mp4",
        duration=5.0,
        width=0,
        height=0,
    )

    assert material.path == ""
    exported = material.export_json()
    assert exported["path"] == ""
    assert exported["remote_url"] == "https://example.com/video.mp4"


def test_video_material_nonexistent_local_path_blanks_path():
    from pyJianYingDraft import Video_material

    material = Video_material(
        material_type="video",
        remote_url=r"C:\definitely\does\not\exist\clip.mp4",
        material_name="clip.mp4",
        duration=5.0,
        width=0,
        height=0,
    )

    assert material.path == ""


def test_audio_material_local_path_passed_as_remote_url_keeps_usable_path(tmp_path):
    from pyJianYingDraft import Audio_material

    audio_path = tmp_path / "voice.mp3"
    audio_path.write_bytes(b"not real audio, just needs to exist")

    material = Audio_material(
        remote_url=str(audio_path),
        material_name="voice.mp3",
        duration=3.0,
    )

    assert material.path != ""
    exported = material.export_json()
    assert exported["path"] != ""


def test_audio_material_true_http_remote_url_still_blanks_path():
    from pyJianYingDraft import Audio_material

    material = Audio_material(
        remote_url="https://example.com/voice.mp3",
        material_name="voice.mp3",
        duration=3.0,
    )

    assert material.path == ""
