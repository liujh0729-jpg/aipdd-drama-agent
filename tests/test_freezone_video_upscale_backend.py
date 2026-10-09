from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from novelvideo.api.routes import freezone as freezone_routes
from novelvideo.freezone import jobs
from novelvideo.freezone.jobs import _video_upscale_filter
from novelvideo.generators import mediakit


def test_video_upscale_filter_uses_lanczos_and_enhancement() -> None:
    video_filter = _video_upscale_filter("1080p", "1x")

    assert "scale='if(gte(iw,ih),1920,-2)'" in video_filter
    assert "flags=lanczos" in video_filter
    assert "hqdn3d=1.2:1.2:4:4" in video_filter
    assert "unsharp=5:5:0.55:3:3:0.25" in video_filter
    assert video_filter.endswith("format=yuv420p")


@pytest.mark.asyncio
async def test_freezone_video_upscale_route_starts_task(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    username = "admin"
    project = "58"
    video_path = tmp_path / "clip.mp4"
    video_path.write_bytes(b"mp4")
    captured: dict[str, object] = {}
    queued = SimpleNamespace(
        backend="inline",
        queue="ffmpeg",
        task_state=SimpleNamespace(task_id="task-upscale"),
    )

    async def _fake_resolve(project_: str, user: dict, *, required_role: str = "editor"):
        del user, required_role
        ctx = SimpleNamespace(project_id=project_)
        return ctx, username, project_, tmp_path, str(tmp_path)

    monkeypatch.setattr(freezone_routes, "_resolve_freezone_project", _fake_resolve)
    monkeypatch.setattr(freezone_routes, "_new_job_id", lambda: "upscale_job")
    monkeypatch.setattr(
        freezone_routes,
        "resolve_static_url_to_path",
        lambda _url, _project_dir: video_path,
    )

    async def fake_enqueue_project_task(ctx, **kwargs):
        captured["ctx"] = ctx
        captured.update(kwargs)
        return queued

    monkeypatch.setattr(
        freezone_routes,
        "get_task_backend",
        lambda: SimpleNamespace(enqueue_project_task=fake_enqueue_project_task),
    )

    result = await freezone_routes.freezone_video_upscale(
        project=project,
        body=freezone_routes.FreezoneVideoUpscaleRequest(
            source_url="/static/admin/58/freezone/_uploads/clip.mp4",
            resolution="2k",
            frame_interpolation="none",
            denoise_strength="2x",
        ),
        user={"username": username},
    )

    assert result["ok"] is True
    assert result["data"]["task_type"] == "freezone_video_upscale"
    assert result["data"]["job_id"] == "upscale_job"
    assert result["data"]["backend"] == "inline"
    assert result["data"]["queue"] == "ffmpeg"
    assert result["data"]["task_id"] == "task-upscale"
    assert "freezone_video_upscale" in result["data"]["task_key"]
    assert captured["ctx"].project_id == project
    assert captured["task_type"] == "freezone_video_upscale"
    assert captured["queue_kind"] == "ffmpeg"
    assert captured["episode"] == 0
    assert captured["scope"] == "upscale_job"
    assert captured["payload"]["source_path"] == video_path.as_posix()
    assert captured["payload"]["resolution"] == "2k"
    assert captured["payload"]["frame_interpolation"] == "none"
    assert captured["payload"]["denoise_strength"] == "2x"


# ---- [aipdd fork delta] MediaKit 优先 / ffmpeg 兜底分支决策 ----


def _patch_ffmpeg_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(jobs.shutil, "which", lambda _name: "/usr/bin/ffmpeg")
    monkeypatch.setattr(
        jobs.subprocess,
        "run",
        lambda *a, **k: SimpleNamespace(returncode=0, stdout="", stderr=""),
    )


@pytest.mark.asyncio
async def test_upscale_prefers_mediakit_when_configured(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    src = tmp_path / "clip.mp4"
    src.write_bytes(b"x")

    async def _fake_enhance(**kwargs):
        out = Path(kwargs["output_path"])
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(b"enhanced")
        return {"backend": "mediakit", "mediakit_task_id": "t1"}

    monkeypatch.setattr(mediakit, "mediakit_configured", lambda: True)
    monkeypatch.setattr(mediakit, "enhance_video_file", _fake_enhance)

    out, meta = await jobs.run_freezone_video_upscale(
        project_dir=tmp_path, job_id="j1", source_path=str(src), resolution="1080p"
    )

    assert meta["backend"] == "mediakit"
    assert out.read_bytes() == b"enhanced"


@pytest.mark.asyncio
async def test_upscale_org_identity_uses_ffmpeg(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    src = tmp_path / "clip.mp4"
    src.write_bytes(b"x")

    async def _never_enhance(**kwargs):
        raise AssertionError("org identity must not call MediaKit")

    monkeypatch.setattr(mediakit, "mediakit_configured", lambda: True)
    monkeypatch.setattr(mediakit, "enhance_video_file", _never_enhance)
    _patch_ffmpeg_ok(monkeypatch)

    out, meta = await jobs.run_freezone_video_upscale(
        project_dir=tmp_path,
        job_id="j2",
        source_path=str(src),
        resolution="1080p",
        egress_context=SimpleNamespace(is_organization=True),
    )

    assert meta["backend"] == "ffmpeg"
    assert out.name == "j2.mp4"


@pytest.mark.asyncio
async def test_upscale_falls_back_when_mediakit_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    src = tmp_path / "clip.mp4"
    src.write_bytes(b"x")

    async def _raise_enhance(**kwargs):
        raise RuntimeError("mediakit boom")

    monkeypatch.setattr(mediakit, "mediakit_configured", lambda: True)
    monkeypatch.setattr(mediakit, "enhance_video_file", _raise_enhance)
    _patch_ffmpeg_ok(monkeypatch)

    out, meta = await jobs.run_freezone_video_upscale(
        project_dir=tmp_path, job_id="j3", source_path=str(src), resolution="1080p"
    )

    assert meta["backend"] == "ffmpeg"
