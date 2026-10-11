from __future__ import annotations

import importlib

import pytest


class FakeAgent:
    def __init__(self, model, **kwargs):
        self.model = model
        self.kwargs = kwargs


def _capture_model_calls(monkeypatch):
    """记录 ``get_pydantic_model`` 的调用参数。

    [aipdd fork delta] 参数表里不再有 ``fallback_env``：env 兜底已退出解析链，调用方再传
    这个关键字会直接 TypeError —— 这正是本文件想要的守卫。
    """
    from novelvideo import config

    calls: list[dict[str, str | None]] = []

    def fake_get_pydantic_model(
        provider_override: str | None = None,
        model_name_override: str | None = None,
        *,
        default_model: str | None = None,
    ):
        calls.append(
            {
                "provider_override": provider_override,
                "model_name_override": model_name_override,
                "default_model": default_model,
            }
        )
        return object()

    monkeypatch.setattr(config, "get_pydantic_model", fake_get_pydantic_model)
    return calls


@pytest.mark.parametrize(
    ("module_name", "factory_name", "provider_env", "model_env"),
    [
        (
            "novelvideo.agents.global_video_optimizer",
            "create_global_video_reviewer_agent",
            "GLOBAL_VIDEO_PROVIDER",
            "GLOBAL_VIDEO_MODEL",
        ),
        (
            "novelvideo.agents.video_prompt_builder",
            "create_video_prompt_builder_agent",
            "VIDEO_PROMPT_PROVIDER",
            "VIDEO_PROMPT_MODEL",
        ),
    ],
)
def test_superpower_prompt_agents_use_default_model_provider_unless_overridden(
    monkeypatch,
    module_name,
    factory_name,
    provider_env,
    model_env,
):
    calls = _capture_model_calls(monkeypatch)
    module = importlib.import_module(module_name)
    monkeypatch.setattr(module, "Agent", FakeAgent)
    for env_name in (
        provider_env,
        model_env,
        "SUPERPOWER_PROVIDER",
        "SUPERPOWER_MODEL_PROVIDER",
        "SUPERPOWER_MODEL",
        "SUPERPOWER_MODEL_NAME",
    ):
        monkeypatch.delenv(env_name, raising=False)

    getattr(module, factory_name)()

    # [aipdd fork delta] 带图的 SuperPower 构建仍与纯文本位隔离，但隔离不再由
    # ``VISION_MODEL_FALLBACK`` 这个 env 完成：调用点点名视觉槽位别名
    # （``DC-video-prompt-optimizer-LLM``，登记表 capability=vision），模型名只来自
    # 「设置 → 模型与渠道」+ 渠道目录。env 只剩「选哪个厂商预设」这一件事。
    from novelvideo.official_defaults import DEFAULT_VIDEO_PROMPT_OPTIMIZER_MODEL

    assert calls == [
        {
            "provider_override": None,
            "model_name_override": None,
            "default_model": DEFAULT_VIDEO_PROMPT_OPTIMIZER_MODEL,
        }
    ]


def test_global_video_reviewer_env_selects_provider_preset_but_never_a_model_name(
    monkeypatch,
):
    """厂商预设可以按功能 env 换，模型名不行。

    [aipdd fork delta] ``GLOBAL_VIDEO_MODEL`` 过去会把一个设置页看不见的真名直接钉到请求上
    （渠道下架后就是 503 / 404）。现在只有 ``GLOBAL_VIDEO_PROVIDER`` 生效，
    ``model_name_override`` 恒为 ``None``，名字交给设置与渠道目录。
    """
    calls = _capture_model_calls(monkeypatch)
    from novelvideo.agents import global_video_optimizer

    monkeypatch.setattr(global_video_optimizer, "Agent", FakeAgent)
    monkeypatch.setenv("GLOBAL_VIDEO_PROVIDER", "openrouter")
    monkeypatch.setenv("GLOBAL_VIDEO_MODEL", "gemini-3.5-flash")

    global_video_optimizer.create_global_video_reviewer_agent()

    from novelvideo.official_defaults import DEFAULT_VIDEO_PROMPT_OPTIMIZER_MODEL

    assert calls == [
        {
            "provider_override": "openrouter",
            "model_name_override": None,
            "default_model": DEFAULT_VIDEO_PROMPT_OPTIMIZER_MODEL,
        }
    ]


def test_keyframe_prompt_builder_uses_video_optimizer_model(monkeypatch):
    """首尾帧提示词位点名视觉别名，视觉隔离由登记表能力档负责（不再传 env 兜底键）。"""
    from novelvideo import config, model_slots
    from novelvideo.agents import keyframe_prompt_builder

    calls: list[tuple[str, str, str]] = []

    def fake_get_newapi_text_pydantic_model(
        model_env: str,
        default_model: str,
        *,
        capability: str = "text.generate",
    ):
        calls.append((model_env, default_model, capability))
        return object()

    monkeypatch.setattr(
        config,
        "get_newapi_text_pydantic_model",
        fake_get_newapi_text_pydantic_model,
    )
    monkeypatch.setattr(keyframe_prompt_builder, "Agent", FakeAgent)

    keyframe_prompt_builder.create_keyframe_prompt_builder_agent()

    assert calls == [
        (
            "KEYFRAME_PROMPT_MODEL",
            "DC-video-prompt-optimizer-LLM",
            "text.generate.agent",
        )
    ]
    # 能力档落在登记表而不是 env：这一位兜底只会提名视觉模型。
    assert model_slots.SLOTS["KEYFRAME_PROMPT_MODEL"].capability == "vision"
