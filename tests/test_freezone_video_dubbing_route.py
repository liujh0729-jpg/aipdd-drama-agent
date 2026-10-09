# SPDX-License-Identifier: Elastic-2.0
# Copyright (c) 2026 ClaymoreLab
"""[aipdd fork delta] 「视频配音」提交路由的后端契约回归。

零网络、零真实音色登记：直接调路由处理函数
``freezone_routes.freezone_video_dubbing``，打桩项目解析、静态地址反解、音色登记、
入队。钉住：源/参考反解与存在性、参考音频注册成 ``user_custom`` 声线进 ``voice_ref``、
``language / model / asr_model`` 进 payload、返回 accepted 信封、未知 model→409、
未知 asr→409、缺参考音频→404、非 /static→400。
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from novelvideo.api.routes import freezone as freezone_routes
from novelvideo.api.schemas import FreezoneVideoDubbingRequest

_PROJECT_DIRNAME = "project"


def _make_ctx() -> SimpleNamespace:
    return SimpleNamespace(requester_username="alice", requester_user_id="user-1")


def _patch_route_resolution(
    monkeypatch: pytest.MonkeyPatch,
    project_dir: Path,
    ctx: object | None = None,
) -> None:
    if ctx is None:
        ctx = _make_ctx()

    async def fake_resolve(project: str, user: dict, *, required_role: str = "editor"):
        del user, required_role
        return ctx, "alice", project, project_dir, str(project_dir)

    monkeypatch.setattr(freezone_routes, "_resolve_freezone_project", fake_resolve)


def _patch_static_resolution(
    monkeypatch: pytest.MonkeyPatch, project_dir: Path
) -> None:
    """把 /static/... 反解成 ``project_dir/freezone/_uploads/<basename>``。"""

    def fake_resolve_to_path(url: str, base: Path) -> Path:
        if not str(url).startswith("/static/"):
            raise ValueError("only /static urls are allowed")
        name = str(url).rstrip("/").split("/")[-1]
        return base / "freezone" / "_uploads" / name

    monkeypatch.setattr(freezone_routes, "resolve_static_url_to_path", fake_resolve_to_path)


def _make_files(project_dir: Path) -> tuple[Path, Path]:
    uploads = project_dir / "freezone" / "_uploads"
    uploads.mkdir(parents=True, exist_ok=True)
    video = uploads / "clip.mp4"
    video.write_bytes(b"video")
    reference = uploads / "ref.wav"
    reference.write_bytes(b"audio")
    return video, reference


def _patch_voice_and_enqueue(
    monkeypatch: pytest.MonkeyPatch, captured: dict
) -> None:
    monkeypatch.setattr(
        "novelvideo.api.routes.freezone.audio_ops.is_readable_audio_file",
        lambda *_a, **_k: True,
    )
    monkeypatch.setattr(
        "novelvideo.api.routes.freezone.audio_ops.create_user_audio_voice",
        lambda **kwargs: {"voice_id": "fv_registered"},
    )
    monkeypatch.setattr(freezone_routes, "_new_job_id", lambda: "job-fixed")

    async def fake_enqueue(**kwargs):
        captured.update(kwargs)
        return {
            "ok": True,
            "data": {
                "task_type": kwargs["task_type"],
                "job_id": kwargs["job_id"],
                "status": "queued",
            },
        }

    monkeypatch.setattr(
        freezone_routes, "_enqueue_freezone_background_job", fake_enqueue
    )


@pytest.mark.asyncio
async def test_route_enqueues_full_payload(tmp_path: Path, monkeypatch) -> None:
    project_dir = tmp_path / _PROJECT_DIRNAME
    _patch_route_resolution(monkeypatch, project_dir)
    _patch_static_resolution(monkeypatch, project_dir)
    video, reference = _make_files(project_dir)

    captured: dict = {}
    _patch_voice_and_enqueue(monkeypatch, captured)

    result = await freezone_routes.freezone_video_dubbing(
        project="58",
        body=FreezoneVideoDubbingRequest(
            source_url="/static/alice/58/freezone/_uploads/clip.mp4",
            reference_audio_url="/static/alice/58/freezone/_uploads/ref.wav",
            language="vi",
            asr_model="",
            archive_to_library=True,
            source_tool="video-dubbing",
        ),
        user={"username": "alice"},
    )

    assert result["ok"] is True
    assert captured["task_type"] == "freezone_video_dubbing"
    payload = captured["payload"]
    assert payload["source_path"] == video.as_posix()
    assert payload["source_kind"] == "video"
    assert payload["voice_ref"] == {"scope": "user_custom", "voice_id": "fv_registered"}
    assert payload["language"] == "vi"
    assert payload["model"] == ""
    assert payload["archive_to_library"] is True
    assert payload["source_tool"] == "video-dubbing"
    assert payload["account_voice_username"] == "alice"


@pytest.mark.asyncio
async def test_route_validates_known_asr_model(tmp_path: Path, monkeypatch) -> None:
    from fastapi import HTTPException

    project_dir = tmp_path / _PROJECT_DIRNAME
    _patch_route_resolution(monkeypatch, project_dir)
    _patch_static_resolution(monkeypatch, project_dir)
    _make_files(project_dir)
    _patch_voice_and_enqueue(monkeypatch, {})

    def boom(model: str) -> dict:
        raise ValueError(f"unknown {model}")

    monkeypatch.setattr(
        "novelvideo.freezone.douyin_copywrite.resolve_douyin_asr_candidate", boom
    )

    with pytest.raises(HTTPException) as exc:
        await freezone_routes.freezone_video_dubbing(
            project="58",
            body=FreezoneVideoDubbingRequest(
                source_url="/static/alice/58/freezone/_uploads/clip.mp4",
                reference_audio_url="/static/alice/58/freezone/_uploads/ref.wav",
                asr_model="bogus:model",
            ),
            user={"username": "alice"},
        )
    assert exc.value.status_code == 409


@pytest.mark.asyncio
async def test_route_rejects_unknown_audio_model(tmp_path: Path, monkeypatch) -> None:
    from fastapi import HTTPException

    project_dir = tmp_path / _PROJECT_DIRNAME
    _patch_route_resolution(monkeypatch, project_dir)
    _patch_static_resolution(monkeypatch, project_dir)
    _make_files(project_dir)
    _patch_voice_and_enqueue(monkeypatch, {})

    async def empty_catalog(*_a, **_k):
        return []

    monkeypatch.setattr(freezone_routes, "_scoped_media_model_catalog", empty_catalog)

    with pytest.raises(HTTPException) as exc:
        await freezone_routes.freezone_video_dubbing(
            project="58",
            body=FreezoneVideoDubbingRequest(
                source_url="/static/alice/58/freezone/_uploads/clip.mp4",
                reference_audio_url="/static/alice/58/freezone/_uploads/ref.wav",
                model="not-in-catalog",
            ),
            user={"username": "alice"},
        )
    assert exc.value.status_code == 409


@pytest.mark.asyncio
async def test_route_missing_reference_returns_404(tmp_path: Path, monkeypatch) -> None:
    from fastapi import HTTPException

    project_dir = tmp_path / _PROJECT_DIRNAME
    _patch_route_resolution(monkeypatch, project_dir)
    _patch_static_resolution(monkeypatch, project_dir)
    # 只建视频、不建参考音频 → 参考音频 404。
    uploads = project_dir / "freezone" / "_uploads"
    uploads.mkdir(parents=True, exist_ok=True)
    (uploads / "clip.mp4").write_bytes(b"video")

    with pytest.raises(HTTPException) as exc:
        await freezone_routes.freezone_video_dubbing(
            project="58",
            body=FreezoneVideoDubbingRequest(
                source_url="/static/alice/58/freezone/_uploads/clip.mp4",
                reference_audio_url="/static/alice/58/freezone/_uploads/missing.wav",
            ),
            user={"username": "alice"},
        )
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_route_non_static_source_returns_400(tmp_path: Path, monkeypatch) -> None:
    from fastapi import HTTPException

    project_dir = tmp_path / _PROJECT_DIRNAME
    _patch_route_resolution(monkeypatch, project_dir)
    _patch_static_resolution(monkeypatch, project_dir)

    with pytest.raises(HTTPException) as exc:
        await freezone_routes.freezone_video_dubbing(
            project="58",
            body=FreezoneVideoDubbingRequest(
                source_url="https://cdn.example.com/clip.mp4",
                reference_audio_url="/static/alice/58/freezone/_uploads/ref.wav",
            ),
            user={"username": "alice"},
        )
    assert exc.value.status_code == 400
