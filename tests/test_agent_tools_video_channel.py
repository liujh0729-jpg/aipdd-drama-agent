# SPDX-License-Identifier: Elastic-2.0
# Copyright (c) 2026 ClaymoreLab
"""[aipdd fork delta] Agent 视频直连渠道回归。

LocalNewAPI 代理对 aipdd 视频提交会包 {code, message(任务JSON), data} 壳且
不登记任务，轮询恒 task_not_exist；Agent 生成视频必须命中绑定渠道（如
aipdd）时改用直连 base_url+key 走原生 /videos 协议，未命中沿用生效网关。
"""

from __future__ import annotations

from novelvideo.agent import tools as agent_tools
from novelvideo.agent.tools import ToolContext


def _ctx(**kwargs) -> ToolContext:
    base = dict(
        username="u1",
        project="p1",
        api_key="sk-gw",
        base_url="http://newapi:3000/v1",
        image_model="img-1",
        video_model="ap-minimax-h3",
    )
    base.update(kwargs)
    return ToolContext(**base)


async def test_generate_video_uses_direct_channel_when_bound(monkeypatch) -> None:
    """命中渠道绑定（aipdd 默认视频模型）时用直连 base_url+key，参数透传。"""
    monkeypatch.setattr(
        agent_tools,
        "_video_direct_channel",
        lambda _m: ("https://api.aipdd.work/v1", "sk-direct"),
    )
    captured: dict = {}

    async def fake_persist(_context, _video):
        return {"url": "https://oss.aipdd.work/x.mp4"}

    async def fake_generate_video(**kwargs):
        captured.update(kwargs)
        return {"url": "https://oss.aipdd.work/x.mp4"}

    monkeypatch.setattr(agent_tools.gateway, "generate_video", fake_generate_video)
    monkeypatch.setattr(agent_tools, "_persist_video", fake_persist)

    result = await agent_tools._run_generate_video(
        {"prompt": "猫", "resolution": "720p", "duration_seconds": 3, "ratio": "16:9"},
        _ctx(),
    )
    assert result.ok
    assert captured["base_url"] == "https://api.aipdd.work/v1"
    assert captured["api_key"] == "sk-direct"
    assert captured["model"] == "ap-minimax-h3"
    assert captured["resolution"] == "720p"
    assert captured["duration"] == 3
    assert captured["ratio"] == "16:9"


async def test_generate_video_falls_back_to_context_credentials(monkeypatch) -> None:
    """模型未绑定渠道时沿用生效网关（代理）凭据。"""
    monkeypatch.setattr(agent_tools, "_video_direct_channel", lambda _m: ("", ""))
    captured: dict = {}

    async def fake_persist(_context, _video):
        return {"url": "https://oss/x.mp4"}

    async def fake_generate_video(**kwargs):
        captured.update(kwargs)
        return {"url": "https://oss/x.mp4"}

    monkeypatch.setattr(agent_tools.gateway, "generate_video", fake_generate_video)
    monkeypatch.setattr(agent_tools, "_persist_video", fake_persist)

    result = await agent_tools._run_generate_video({"prompt": "猫"}, _ctx())
    assert result.ok
    assert captured["base_url"] == "http://newapi:3000/v1"
    assert captured["api_key"] == "sk-gw"


def test_video_direct_channel_resolve_variants(monkeypatch) -> None:
    """_video_direct_channel 对 override 的三种结果（命中/未命中/异常）归一化。"""
    from novelvideo import model_gateway_settings as mgs

    monkeypatch.setattr(
        mgs,
        "resolve_media_provider_override",
        lambda _m, *, media_type=None: ("https://api.aipdd.work/v1", "sk-1", "aipdd"),
    )
    assert agent_tools._video_direct_channel("ap-minimax-h3") == (
        "https://api.aipdd.work/v1",
        "sk-1",
    )

    monkeypatch.setattr(
        mgs, "resolve_media_provider_override", lambda _m, *, media_type=None: None
    )
    assert agent_tools._video_direct_channel("ap-minimax-h3") == ("", "")

    def boom(_m, *, media_type=None):
        raise RuntimeError("probe failed")

    monkeypatch.setattr(mgs, "resolve_media_provider_override", boom)
    assert agent_tools._video_direct_channel("ap-minimax-h3") == ("", "")


async def test_generate_video_failure_message_preserved(monkeypatch) -> None:
    """网关错误仍以「生成失败：」前缀回显给用户，直连解析异常也不吞错误。"""
    monkeypatch.setattr(agent_tools, "_video_direct_channel", lambda _m: ("", ""))

    async def fake_generate_video(**kwargs):
        raise agent_tools.gateway.GatewayError("视频生成响应缺少任务编号")

    monkeypatch.setattr(agent_tools.gateway, "generate_video", fake_generate_video)
    result = await agent_tools._run_generate_video({"prompt": "猫"}, _ctx())
    assert not result.ok
    assert result.content == "生成失败：视频生成响应缺少任务编号"