import json
import shutil
from pathlib import Path
from types import SimpleNamespace


def test_save_draft_sets_replace_path_for_video_without_draft_folder(monkeypatch, tmp_path):
    """Regression test: save_draft_background always downloads/copies material
    files into output_base_dir (which falls back to the project directory when
    draft_folder is None), but replace_path used to only be set `if draft_folder`.
    That mismatch left material.path == "" in draft_content.json even though the
    file had actually been copied. replace_path must be set unconditionally so it
    matches where the file really ends up."""
    import save_draft_impl
    from draft_cache import DRAFT_CACHE
    from draft_profiles import get_draft_profile
    from save_task_cache import create_task

    draft_id = "draft-no-folder-video"
    project_dir = Path(save_draft_impl.__file__).resolve().parent
    project_draft_dir = project_dir / draft_id
    if project_draft_dir.exists():
        shutil.rmtree(project_draft_dir)

    source_video = tmp_path / "source.mp4"
    source_video.write_bytes(b"fake video bytes")

    video_material = SimpleNamespace(
        remote_url=str(source_video),
        material_name="clip.mp4",
        material_type="video",
        replace_path=None,
    )

    payload = {"tracks": [], "materials": {}, "duration": 0}
    script = SimpleNamespace(
        materials=SimpleNamespace(audios=[], videos=[video_material]),
        tracks={},
        dumps=lambda profile=None: json.dumps(payload),
    )
    DRAFT_CACHE[draft_id] = script
    create_task(draft_id)

    monkeypatch.setattr(save_draft_impl, "get_draft_profile", lambda: get_draft_profile("jianying_pro_10"))
    monkeypatch.setattr(save_draft_impl, "update_media_metadata", lambda script, task_id=None: None)
    monkeypatch.setattr(save_draft_impl, "IS_UPLOAD_DRAFT", False)

    try:
        save_draft_impl.save_draft_background(draft_id, None, draft_id)

        expected_replace_path = save_draft_impl.build_asset_path(
            str(project_dir), draft_id, "video", "clip.mp4"
        )
        assert video_material.replace_path == expected_replace_path
        assert Path(expected_replace_path).exists()
    finally:
        if project_draft_dir.exists():
            shutil.rmtree(project_draft_dir)


def test_save_draft_sets_replace_path_for_audio_without_draft_folder(monkeypatch, tmp_path):
    import save_draft_impl
    from draft_cache import DRAFT_CACHE
    from draft_profiles import get_draft_profile
    from save_task_cache import create_task

    draft_id = "draft-no-folder-audio"
    project_dir = Path(save_draft_impl.__file__).resolve().parent
    project_draft_dir = project_dir / draft_id
    if project_draft_dir.exists():
        shutil.rmtree(project_draft_dir)

    source_audio = tmp_path / "source.mp3"
    source_audio.write_bytes(b"fake audio bytes")

    audio_material = SimpleNamespace(
        remote_url=str(source_audio),
        material_name="voice.mp3",
        replace_path=None,
    )

    payload = {"tracks": [], "materials": {}, "duration": 0}
    script = SimpleNamespace(
        materials=SimpleNamespace(audios=[audio_material], videos=[]),
        tracks={},
        dumps=lambda profile=None: json.dumps(payload),
    )
    DRAFT_CACHE[draft_id] = script
    create_task(draft_id)

    monkeypatch.setattr(save_draft_impl, "get_draft_profile", lambda: get_draft_profile("jianying_pro_10"))
    monkeypatch.setattr(save_draft_impl, "update_media_metadata", lambda script, task_id=None: None)
    monkeypatch.setattr(save_draft_impl, "IS_UPLOAD_DRAFT", False)

    try:
        save_draft_impl.save_draft_background(draft_id, None, draft_id)

        expected_replace_path = save_draft_impl.build_asset_path(
            str(project_dir), draft_id, "audio", "voice.mp3"
        )
        assert audio_material.replace_path == expected_replace_path
        assert Path(expected_replace_path).exists()
    finally:
        if project_draft_dir.exists():
            shutil.rmtree(project_draft_dir)
