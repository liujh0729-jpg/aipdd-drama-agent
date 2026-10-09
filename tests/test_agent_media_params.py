# SPDX-License-Identifier: Elastic-2.0
# Copyright (c) 2026 ClaymoreLab
"""[aipdd fork delta] Agent 回合媒体参数回归。

用户在输入区显式设定的分辨率 / 时长（Composer → SendMessageBody → AgentEngine →
ToolContext）优先于 LLM 从工具参数解析的同类值，且生成结果文本会注明用户设定，
便于 LLM 按真实参数回复用户；未设定时保持原解析（LLM / 网关默认值决定）。
"""

from __future__ import annotations

from pathlib import Path

from novelvideo.agent import tools as agent_tools
from novelvideo.agent.routes import _clean_video_upscale_resolution
from novelvideo.agent.tools import ToolContext
from novelvideo.freezone import jobs as freezone_jobs


def _ctx(**kwargs) -> ToolContext:
    base = dict(
        username="u1",
        project="p1",
        api_key="sk-gw",
        base_url="http://newapi:3000/v1",
        image_model="ap-image-2.0",
        video_model="ap-minimax-h3",
    )
    base.update(kwargs)
    return ToolContext(**base)


async def test_image_size_user_setting_wins_over_llm_argument(monkeypatch) -> None:
    """用户设定尺寸覆盖 LLM 参数，结果文本注明「用户设定尺寸」。"""
    captured: dict = {}

    async def fake_generate_image(**kwargs):
        captured.update(kwargs)
        return {"url": "https://oss/pic.png"}

    async def fake_persist(_context, _image):
        return {"url": "https://oss/pic.png"}

    monkeypatch.setattr(agent_tools.gateway, "generate_image", fake_generate_image)
    monkeypatch.setattr(agent_tools, "_persist_image", fake_persist)

    result = await agent_tools._run_generate_image(
        {"prompt": "叶辰站在窗前", "size": "1024x1024"},
        _ctx(image_size="4K"),
    )
    assert result.ok
    assert captured["size"] == "4K"
    assert captured["model"] == "ap-image-2.0"
    assert "用户设定尺寸 4K" in result.content


async def test_image_size_falls_back_to_llm_argument(monkeypatch) -> None:
    """未设定时 LLM 参数照常生效，结果文本不夹带用户设定注记。"""
    captured: dict = {}

    async def fake_generate_image(**kwargs):
        captured.update(kwargs)
        return {"url": "https://oss/pic.png"}

    async def fake_persist(_context, _image):
        return {"url": "https://oss/pic.png"}

    monkeypatch.setattr(agent_tools.gateway, "generate_image", fake_generate_image)
    monkeypatch.setattr(agent_tools, "_persist_image", fake_persist)

    result = await agent_tools._run_generate_image(
        {"prompt": "叶辰站在窗前", "size": "1024x1024"},
        _ctx(),
    )
    assert result.ok
    assert captured["size"] == "1024x1024"
    assert "用户设定" not in result.content


async def test_video_user_settings_win_over_llm_arguments(monkeypatch) -> None:
    """用户设定的分辨率 / 时长覆盖 LLM 参数；ratio 不受影响。"""
    monkeypatch.setattr(agent_tools, "_video_direct_channel", lambda _m: ("", ""))
    captured: dict = {}

    async def fake_generate_video(**kwargs):
        captured.update(kwargs)
        return {"url": "https://oss/clip.mp4"}

    async def fake_persist(_context, _video):
        return {"url": "https://oss/clip.mp4"}

    monkeypatch.setattr(agent_tools.gateway, "generate_video", fake_generate_video)
    monkeypatch.setattr(agent_tools, "_persist_video", fake_persist)

    result = await agent_tools._run_generate_video(
        {"prompt": "叶辰开口说话", "resolution": "1080p", "duration_seconds": 10, "ratio": "16:9"},
        _ctx(video_resolution="720p", video_duration=5),
    )
    assert result.ok
    assert captured["resolution"] == "720p"
    assert captured["duration"] == 5
    assert captured["ratio"] == "16:9"
    assert "用户设定：分辨率 720p、时长 5 秒" in result.content


async def test_video_params_fall_back_to_llm_arguments(monkeypatch) -> None:
    """未设定时分辨率 / 时长保持 LLM 解析（用户消息里的「3 秒」可生效）。"""
    monkeypatch.setattr(agent_tools, "_video_direct_channel", lambda _m: ("", ""))
    captured: dict = {}

    async def fake_generate_video(**kwargs):
        captured.update(kwargs)
        return {"url": "https://oss/clip.mp4"}

    async def fake_persist(_context, _video):
        return {"url": "https://oss/clip.mp4"}

    monkeypatch.setattr(agent_tools.gateway, "generate_video", fake_generate_video)
    monkeypatch.setattr(agent_tools, "_persist_video", fake_persist)

    result = await agent_tools._run_generate_video(
        {"prompt": "叶辰开口说话", "resolution": "1080p", "duration_seconds": 3},
        _ctx(),
    )
    assert result.ok
    assert captured["resolution"] == "1080p"
    assert captured["duration"] == 3
    assert "用户设定" not in result.content


async def test_video_user_resolution_keeps_llm_duration(monkeypatch) -> None:
    """只设了分辨率时，时长仍走 LLM 解析（两个字段互相独立）。"""
    monkeypatch.setattr(agent_tools, "_video_direct_channel", lambda _m: ("", ""))
    captured: dict = {}

    async def fake_generate_video(**kwargs):
        captured.update(kwargs)
        return {"url": "https://oss/clip.mp4"}

    async def fake_persist(_context, _video):
        return {"url": "https://oss/clip.mp4"}

    monkeypatch.setattr(agent_tools.gateway, "generate_video", fake_generate_video)
    monkeypatch.setattr(agent_tools, "_persist_video", fake_persist)

    result = await agent_tools._run_generate_video(
        {"prompt": "叶辰开口说话", "duration_seconds": 3},
        _ctx(video_resolution="1080p"),
    )
    assert result.ok
    assert captured["resolution"] == "1080p"
    assert captured["duration"] == 3
    assert "用户设定：分辨率 1080p" in result.content
    assert "时长" not in result.content


# ---------------------------------------------------------------------------
# 超清两步处理（req22）：成片后走高清叶子，终稿替换展示媒体；失败不吞成片。
# ---------------------------------------------------------------------------


def _wire_video_mocks(monkeypatch, persist_media: dict) -> None:
    monkeypatch.setattr(agent_tools, "_video_direct_channel", lambda _m: ("", ""))

    async def fake_generate_video(**_kwargs):
        return {"url": "https://oss/clip.mp4"}

    async def fake_persist(_context, _video):
        return dict(persist_media)

    monkeypatch.setattr(agent_tools.gateway, "generate_video", fake_generate_video)
    monkeypatch.setattr(agent_tools, "_persist_video", fake_persist)


async def test_video_upscale_replaces_media_with_hd_final(
    monkeypatch, tmp_path: Path
) -> None:
    """全局会话：超清终稿搬回媒体目录，展示地址换成 _hd 产物，回复注明超清档位。"""
    src = tmp_path / "video_base.mp4"
    src.write_bytes(b"base")
    _wire_video_mocks(
        monkeypatch,
        {"kind": "video", "url": "/api/v1/agent/media/video_base.mp4",
         "name": "video_base.mp4", "local_path": str(src), "origin": "agent"},
    )
    leaf_out = tmp_path / "work" / "hd.mp4"

    async def fake_leaf(**kwargs):
        assert kwargs["resolution"] == "2k"
        leaf_out.parent.mkdir(parents=True, exist_ok=True)
        leaf_out.write_bytes(b"hd")
        return leaf_out, {}

    monkeypatch.setattr(freezone_jobs, "run_freezone_video_upscale", fake_leaf)

    result = await agent_tools._run_generate_video(
        {"prompt": "叶辰开口说话"},
        _ctx(username="u1", project="", project_dir=None, video_upscale_resolution="2k"),
    )
    assert result.ok
    final = result.media[0]
    assert final["url"] == "/api/v1/agent/media/video_base_hd.mp4"
    assert final["local_path"].endswith("video_base_hd.mp4")
    assert Path(final["local_path"]).read_bytes() == b"hd"
    assert not leaf_out.exists()  # 产物已搬走
    assert "超清 2k" in result.content
    assert "失败" not in result.content


async def test_video_upscale_project_origin_rebuilds_static_url(
    monkeypatch, tmp_path: Path
) -> None:
    """项目会话：终稿重建项目静态地址（/static/projects/...）。"""
    media_dir = tmp_path / "freezone" / "agent_media"
    media_dir.mkdir(parents=True)
    src = media_dir / "video_p.mp4"
    src.write_bytes(b"base")
    _wire_video_mocks(
        monkeypatch,
        {"kind": "video", "url": "/static/projects/p1/old.mp4",
         "name": "video_p.mp4", "local_path": str(src), "origin": "project"},
    )
    leaf_out = tmp_path / "outs" / "hd.mp4"

    async def fake_leaf(**_kwargs):
        leaf_out.parent.mkdir(parents=True, exist_ok=True)
        leaf_out.write_bytes(b"hd")
        return leaf_out, {}

    monkeypatch.setattr(freezone_jobs, "run_freezone_video_upscale", fake_leaf)

    result = await agent_tools._run_generate_video(
        {"prompt": "叶辰开口说话"},
        _ctx(project="p1", project_dir=tmp_path, video_upscale_resolution="1080p"),
    )
    assert result.ok
    final = result.media[0]
    assert final["url"].startswith("/static/projects/p1/")
    assert "video_p_hd.mp4" in final["url"]
    assert Path(final["local_path"]).exists()


async def test_video_upscale_failure_keeps_base_video(monkeypatch, tmp_path: Path) -> None:
    """高清叶子报错：不阻断回合，保留基础成片并在结果里说明。"""
    src = tmp_path / "video_b.mp4"
    src.write_bytes(b"base")
    _wire_video_mocks(
        monkeypatch,
        {"kind": "video", "url": "/api/v1/agent/media/video_b.mp4",
         "name": "video_b.mp4", "local_path": str(src), "origin": "agent"},
    )

    async def failing_leaf(**_kwargs):
        raise RuntimeError("MediaKit 不可达")

    monkeypatch.setattr(freezone_jobs, "run_freezone_video_upscale", failing_leaf)

    result = await agent_tools._run_generate_video(
        {"prompt": "叶辰开口说话"},
        _ctx(project="", project_dir=None, video_upscale_resolution="4k"),
    )
    assert result.ok
    assert result.media[0]["url"] == "/api/v1/agent/media/video_b.mp4"
    assert "超清处理失败，已返回基础分辨率视频" in result.content


async def test_video_without_upscale_skips_leaf(monkeypatch, tmp_path: Path) -> None:
    """未开启超清：高清叶子绝不参与，行为与原两步前一致。"""
    src = tmp_path / "video_n.mp4"
    src.write_bytes(b"base")
    _wire_video_mocks(
        monkeypatch,
        {"kind": "video", "url": "/api/v1/agent/media/video_n.mp4",
         "name": "video_n.mp4", "local_path": str(src), "origin": "agent"},
    )

    async def never_leaf(**_kwargs):  # pragma: no cover
        raise AssertionError("不应调用高清叶子")

    monkeypatch.setattr(freezone_jobs, "run_freezone_video_upscale", never_leaf)

    result = await agent_tools._run_generate_video(
        {"prompt": "叶辰开口说话"}, _ctx(project="", project_dir=None),
    )
    assert result.ok
    assert "超清" not in result.content


def test_clean_video_upscale_resolution_whitelist() -> None:
    """档位归一：大小写/空白宽容，白名单外一律当未开启。"""
    assert _clean_video_upscale_resolution("2K") == "2k"
    assert _clean_video_upscale_resolution(" 1080p ") == "1080p"
    assert _clean_video_upscale_resolution("4k") == "4k"
    assert _clean_video_upscale_resolution("") == ""
    assert _clean_video_upscale_resolution("8k") == ""
    assert _clean_video_upscale_resolution("720p") == ""