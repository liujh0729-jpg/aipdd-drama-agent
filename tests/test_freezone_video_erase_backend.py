"""[aipdd fork delta] 智能去字幕：MediaKit 字幕擦除优先 / ffmpeg delogo 兜底分支决策。"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from novelvideo.freezone import jobs
from novelvideo.generators import mediakit


def _patch_ffmpeg_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    """绕开真实 ffprobe/ffmpeg：直接给出尺寸、字幕框，并"产出"擦除后的文件。"""

    monkeypatch.setattr(jobs.shutil, "which", lambda _name: "/usr/bin/ffmpeg")

    async def _fake_probe_size(_path: str):
        return 1920, 1080

    async def _fake_detect_box(_path: str, _temp):
        return 100, 800, 1500, 120

    async def _fake_render(**kwargs):
        out = Path(kwargs["output_path"])
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(b"erased-ffmpeg")

    monkeypatch.setattr(jobs, "_probe_video_size", _fake_probe_size)
    monkeypatch.setattr(jobs, "_detect_subtitle_box", _fake_detect_box)
    monkeypatch.setattr(jobs, "_render_delogo_video", _fake_render)


@pytest.mark.asyncio
async def test_erase_prefers_mediakit_when_configured(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    src = tmp_path / "clip.mp4"
    src.write_bytes(b"x")

    async def _fake_erase(**kwargs):
        out = Path(kwargs["output_path"])
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(b"erased-ai")
        return {"backend": "mediakit", "erase_mode": "Subtitle", "mediakit_task_id": "t1"}

    monkeypatch.setattr(mediakit, "mediakit_configured", lambda: True)
    monkeypatch.setattr(mediakit, "erase_video_file", _fake_erase)

    out, meta = await jobs.run_freezone_video_erase(
        project_dir=tmp_path, job_id="j1", source_path=str(src), mode="smart_subtitle"
    )

    assert meta["backend"] == "mediakit"
    assert out.read_bytes() == b"erased-ai"


@pytest.mark.asyncio
async def test_erase_box_mode_maps_to_text_with_locations(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """框选擦除：归一化左上+宽高 → MediaKit Text 模式的右下角归一化矩形。"""
    src = tmp_path / "clip.mp4"
    src.write_bytes(b"x")
    captured: dict[str, object] = {}

    async def _fake_erase(**kwargs):
        captured.update(kwargs)
        out = Path(kwargs["output_path"])
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(b"erased-ai")
        return {"backend": "mediakit", "erase_mode": kwargs["mode"]}

    monkeypatch.setattr(mediakit, "mediakit_configured", lambda: True)
    monkeypatch.setattr(mediakit, "erase_video_file", _fake_erase)

    _out, meta = await jobs.run_freezone_video_erase(
        project_dir=tmp_path,
        job_id="jbox",
        source_path=str(src),
        mode="box",
        box_x=0.62,
        box_y=0.86,
        box_width=0.45,  # 0.62 + 0.45 = 1.07 越界，应被夹到 1.0
        box_height=0.10,
    )

    assert meta["backend"] == "mediakit"
    assert captured["mode"] == "Text"
    (loc,) = captured["erase_ratio_location"]  # type: ignore[misc]
    assert loc["top_left_x"] == pytest.approx(0.62)
    assert loc["top_left_y"] == pytest.approx(0.86)
    assert loc["bottom_right_x"] == pytest.approx(1.0)
    assert loc["bottom_right_y"] == pytest.approx(0.96)


@pytest.mark.asyncio
async def test_erase_org_identity_uses_ffmpeg(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    src = tmp_path / "clip.mp4"
    src.write_bytes(b"x")

    async def _never_erase(**kwargs):
        raise AssertionError("org identity must not call MediaKit")

    monkeypatch.setattr(mediakit, "mediakit_configured", lambda: True)
    monkeypatch.setattr(mediakit, "erase_video_file", _never_erase)
    _patch_ffmpeg_fallback(monkeypatch)

    out, meta = await jobs.run_freezone_video_erase(
        project_dir=tmp_path,
        job_id="j2",
        source_path=str(src),
        mode="smart_subtitle",
        egress_context=SimpleNamespace(is_organization=True),
    )

    assert meta["backend"] == "ffmpeg"
    assert out.name == "j2.mp4"
    assert out.read_bytes() == b"erased-ffmpeg"


@pytest.mark.asyncio
async def test_erase_falls_back_when_mediakit_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    src = tmp_path / "clip.mp4"
    src.write_bytes(b"x")

    async def _raise_erase(**kwargs):
        raise RuntimeError("mediakit boom")

    monkeypatch.setattr(mediakit, "mediakit_configured", lambda: True)
    monkeypatch.setattr(mediakit, "erase_video_file", _raise_erase)
    _patch_ffmpeg_fallback(monkeypatch)

    _out, meta = await jobs.run_freezone_video_erase(
        project_dir=tmp_path, job_id="j3", source_path=str(src), mode="smart_subtitle"
    )

    assert meta["backend"] == "ffmpeg"


@pytest.mark.asyncio
async def test_erase_rejects_unsupported_mode(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    src = tmp_path / "clip.mp4"
    src.write_bytes(b"x")
    monkeypatch.setattr(mediakit, "mediakit_configured", lambda: False)

    with pytest.raises(RuntimeError, match="unsupported erase mode"):
        await jobs.run_freezone_video_erase(
            project_dir=tmp_path, job_id="j5", source_path=str(src), mode="nonsense"
        )


@pytest.mark.asyncio
async def test_erase_box_requires_box_params(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    src = tmp_path / "clip.mp4"
    src.write_bytes(b"x")
    monkeypatch.setattr(mediakit, "mediakit_configured", lambda: False)

    with pytest.raises(RuntimeError, match="box mode requires"):
        await jobs.run_freezone_video_erase(
            project_dir=tmp_path, job_id="j6", source_path=str(src), mode="box"
        )


@pytest.mark.asyncio
async def test_erase_local_engine_skips_mediakit_even_if_configured(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """工具页显式选择「本地引擎」：即使配了 MediaKit Key 也不出网。"""
    src = tmp_path / "clip.mp4"
    src.write_bytes(b"x")

    async def _never_erase(**kwargs):
        raise AssertionError("engine=local must not call MediaKit")

    monkeypatch.setattr(mediakit, "mediakit_configured", lambda: True)
    monkeypatch.setattr(mediakit, "erase_video_file", _never_erase)
    _patch_ffmpeg_fallback(monkeypatch)

    out, meta = await jobs.run_freezone_video_erase(
        project_dir=tmp_path,
        job_id="jlocal",
        source_path=str(src),
        mode="smart_subtitle",
        engine="local",
    )

    assert meta["backend"] == "ffmpeg"
    assert meta["requested_engine"] == "local"
    assert out.read_bytes() == b"erased-ffmpeg"


@pytest.mark.asyncio
async def test_erase_mediakit_engine_falls_back_without_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """工具页显式选择「火山 AI-MediaKit」但未配置 Key：静默回退本地（容错）。"""
    src = tmp_path / "clip.mp4"
    src.write_bytes(b"x")

    async def _never_erase(**kwargs):
        raise AssertionError("mediakit must not run without key")

    monkeypatch.setattr(mediakit, "mediakit_configured", lambda: False)
    monkeypatch.setattr(mediakit, "erase_video_file", _never_erase)
    _patch_ffmpeg_fallback(monkeypatch)

    _out, meta = await jobs.run_freezone_video_erase(
        project_dir=tmp_path,
        job_id="jnowkey",
        source_path=str(src),
        mode="smart_subtitle",
        engine="mediakit",
    )

    assert meta["backend"] == "ffmpeg"
    assert meta["requested_engine"] == "mediakit"


@pytest.mark.asyncio
async def test_erase_auto_engine_meta_records_requested(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """auto（画布等既有入口）：有 Key 走 MediaKit，meta 记录请求引擎。"""
    src = tmp_path / "clip.mp4"
    src.write_bytes(b"x")

    async def _fake_erase(**kwargs):
        out = Path(kwargs["output_path"])
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(b"erased-ai")
        return {"backend": "mediakit", "erase_mode": "Subtitle"}

    monkeypatch.setattr(mediakit, "mediakit_configured", lambda: True)
    monkeypatch.setattr(mediakit, "erase_video_file", _fake_erase)

    _out, meta = await jobs.run_freezone_video_erase(
        project_dir=tmp_path, job_id="jauto", source_path=str(src), mode="smart_subtitle"
    )

    assert meta["backend"] == "mediakit"
    assert meta["requested_engine"] == "auto"


@pytest.mark.asyncio
async def test_erase_unknown_engine_normalizes_to_auto(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """非法 engine 值归一为 auto（外部调用容错）。"""
    src = tmp_path / "clip.mp4"
    src.write_bytes(b"x")

    async def _fake_erase(**kwargs):
        out = Path(kwargs["output_path"])
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(b"erased-ai")
        return {"backend": "mediakit"}

    monkeypatch.setattr(mediakit, "mediakit_configured", lambda: True)
    monkeypatch.setattr(mediakit, "erase_video_file", _fake_erase)

    _out, meta = await jobs.run_freezone_video_erase(
        project_dir=tmp_path,
        job_id="junk",
        source_path=str(src),
        mode="smart_subtitle",
        engine="nonsense",
    )

    assert meta["requested_engine"] == "auto"
