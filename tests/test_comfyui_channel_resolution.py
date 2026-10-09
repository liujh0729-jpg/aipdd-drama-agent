"""[aipdd fork delta] ComfyUI 渠道执行配置解析测试。

覆盖 ``resolve_comfyui_channel_execution`` / ``default_comfyui_channel_model`` /
``is_comfyui_channel_model`` / ``detect_comfyui_media_type``：两种工作流存法
（workflow_by_model、workflow_routes）、缺配置报错、媒体类型匹配与组织无关的纯解析。
"""

from __future__ import annotations

import pytest

import novelvideo.model_gateway_settings as mgs

_IMAGE_WF = {"1": {"class_type": "SaveImage", "inputs": {}}}
_VIDEO_WF = {"1": {"class_type": "VHS_VideoCombine", "inputs": {}}}


def _patch_channel(monkeypatch, mappings: dict, channel: dict | None) -> None:
    monkeypatch.setattr(mgs, "get_newapi_media_model_mappings", lambda: mappings)
    monkeypatch.setattr(
        mgs, "get_newapi_provider_channel", lambda provider: channel
    )


def test_non_comfyui_provider_returns_none(monkeypatch) -> None:
    _patch_channel(
        monkeypatch,
        {"gpt-image-2": {"provider": "newapi", "mediaType": "image"}},
        {"baseUrl": "http://comfy:8188"},
    )
    assert mgs.resolve_comfyui_channel_execution("gpt-image-2", "image") is None


def test_media_type_mismatch_returns_none(monkeypatch) -> None:
    _patch_channel(
        monkeypatch,
        {"my-wan": {"provider": "comfyui", "mediaType": "video"}},
        {"baseUrl": "http://comfy:8188"},
    )
    assert mgs.resolve_comfyui_channel_execution("my-wan", "image") is None


def test_missing_base_url_raises(monkeypatch) -> None:
    _patch_channel(
        monkeypatch,
        {"my-wan": {"provider": "comfyui", "mediaType": "video"}},
        {"baseUrl": ""},
    )
    with pytest.raises(ValueError):
        mgs.resolve_comfyui_channel_execution("my-wan", "video")


def test_workflow_by_model_resolution(monkeypatch) -> None:
    _patch_channel(
        monkeypatch,
        {"my-img": {"provider": "comfyui", "mediaType": "image"}},
        {
            "baseUrl": "https://comfy.example/base",
            "settings": {"comfyui": {"workflow_by_model": {"my-img": _IMAGE_WF}}},
        },
    )
    base_url, workflow = mgs.resolve_comfyui_channel_execution("my-img", "image")
    assert base_url == "https://comfy.example/base"
    assert workflow == _IMAGE_WF


def test_workflow_routes_single_match_resolution(monkeypatch) -> None:
    _patch_channel(
        monkeypatch,
        {"my-wan": {"provider": "comfyui", "mediaType": "video"}},
        {
            "baseUrl": "http://comfy:8188",
            "settings": {
                "comfyui": {
                    "workflow_routes": [
                        {"id": "r1", "match": {"models": ["my-wan"]}, "workflow": _VIDEO_WF}
                    ]
                }
            },
        },
    )
    base_url, workflow = mgs.resolve_comfyui_channel_execution("my-wan", "video")
    assert base_url == "http://comfy:8188"
    assert workflow == _VIDEO_WF


def test_missing_workflow_raises_readable_error(monkeypatch) -> None:
    _patch_channel(
        monkeypatch,
        {"my-wan": {"provider": "comfyui", "mediaType": "video"}},
        {"baseUrl": "http://comfy:8188", "settings": {"comfyui": {}}},
    )
    with pytest.raises(ValueError) as exc_info:
        mgs.resolve_comfyui_channel_execution("my-wan", "video")
    assert "工作流" in str(exc_info.value)


def test_default_comfyui_channel_model_orders_by_sort_and_enabled(monkeypatch) -> None:
    monkeypatch.setattr(
        mgs,
        "get_newapi_media_model_mappings",
        lambda: {
            "wan-b": {
                "provider": "comfyui",
                "mediaType": "video",
                "enabled": True,
                "sortOrder": 5,
            },
            "wan-a": {
                "provider": "comfyui",
                "mediaType": "video",
                "enabled": True,
                "sortOrder": 1,
            },
            "wan-off": {
                "provider": "comfyui",
                "mediaType": "video",
                "enabled": False,
                "sortOrder": 0,
            },
            "img-a": {
                "provider": "comfyui",
                "mediaType": "image",
                "enabled": True,
                "sortOrder": 2,
            },
        },
    )
    assert mgs.default_comfyui_channel_model("video") == "wan-a"
    assert mgs.default_comfyui_channel_model("image") == "img-a"


def test_default_comfyui_channel_model_none_when_empty(monkeypatch) -> None:
    monkeypatch.setattr(mgs, "get_newapi_media_model_mappings", lambda: {})
    assert mgs.default_comfyui_channel_model("video") is None


def test_is_comfyui_channel_model_media_type_and_enabled(monkeypatch) -> None:
    monkeypatch.setattr(
        mgs,
        "get_newapi_media_model_mappings",
        lambda: {
            "my-img": {"provider": "comfyui", "mediaType": "image", "enabled": True},
            "my-off": {
                "provider": "comfyui",
                "mediaType": "image",
                "enabled": False,
            },
            "other": {"provider": "newapi", "mediaType": "image"},
        },
    )
    assert mgs.is_comfyui_channel_model("my-img", "image") is True
    assert mgs.is_comfyui_channel_model("my-img", "video") is False
    assert mgs.is_comfyui_channel_model("my-off", "image") is False
    assert mgs.is_comfyui_channel_model("other", "image") is False
    assert mgs.is_comfyui_channel_model("", "image") is False


def test_detect_comfyui_media_type_from_channel_settings(monkeypatch) -> None:
    assert (
        mgs.detect_comfyui_media_type(
            {"comfyui": {"workflow_by_model": {"my-img": _IMAGE_WF}}}, "my-img"
        )
        == "image"
    )
    assert (
        mgs.detect_comfyui_media_type(
            {"comfyui": {"workflow_by_model": {"my-wan": _VIDEO_WF}}}, "my-wan"
        )
        == "video"
    )
    assert mgs.detect_comfyui_media_type({"comfyui": {}}, "missing") is None
