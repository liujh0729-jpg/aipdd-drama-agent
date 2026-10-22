# SPDX-License-Identifier: Elastic-2.0
# Copyright (c) 2026 ClaymoreLab
"""[aipdd fork delta] 本地 ComfyUI「AI分析并自动配置」→ 适配文件 → 执行器注入。

零网络：分析模型的返回、ComfyUI 的工作流都由 monkeypatch 供数据；适配文件落在
``NOVELVIDEO_STATE_DIR`` 隔离出来的 tmp_path。
"""

from __future__ import annotations

from typing import Any
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from novelvideo import comfyui_adaptation
from novelvideo import config
from novelvideo.api.routes import model_gateway
from novelvideo.comfyui_executor import bind_comfyui_workflow
from novelvideo.model_gateway_settings import (
    get_newapi_media_model_mappings,
    save_newapi_media_model_mappings,
)

BASE_URL = "http://comfy.test:8188"


@pytest.fixture(autouse=True)
def _register_local_auth_port():
    """model-gateway 路由组在 router 级强制登录态；本文件手搭 FastAPI 不走
    startup lifespan 会撞 503。只注册 CE FileAuthPort（单用户模式 verify_session
    恒放行），让鉴权层不影响这里的适配/路由契约。"""

    from novelvideo.ports.local.auth import FileAuthPort
    from novelvideo.ports.registry import register_port

    register_port("auth", FileAuthPort())


def _isolate(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    state_root = tmp_path / "state"
    monkeypatch.setenv("NOVELVIDEO_STATE_DIR", str(state_root))
    monkeypatch.setenv("ST_EDITION", "ce")
    monkeypatch.setattr(config, "STATE_DIR", str(state_root))
    for key in (
        "ST_CONTROL_PLANE_DSN",
        "MODEL_GATEWAY_MODE",
        "MODEL_GATEWAY_RUNTIME_VERSION",
        "LOCALAPI_API_KEY",
        "LOCALAPI_BASE_URL",
        "OFFICIAL_NEWAPI_BASE_URL",
        "LOCALAPI_ADMIN_BASE_URL",
        "COMFYUI_BASE_URL",
    ):
        monkeypatch.delenv(key, raising=False)
    return state_root


def _workflow() -> dict[str, Any]:
    """一份典型 API 格式工作流：两个文本节点、空潜空间、采样器、SaveImage。"""

    return {
        "6": {
            "class_type": "CLIPTextEncode",
            "_meta": {"title": "正向提示词"},
            "inputs": {"text": "old positive", "clip": [5, 0]},
        },
        "7": {
            "class_type": "CLIPTextEncode",
            "_meta": {"title": "负向提示词"},
            "inputs": {"text": "old negative", "clip": [5, 0]},
        },
        "10": {
            "class_type": "EmptyLatentImage",
            "inputs": {"width": 512, "height": 512, "batch_size": 1},
        },
        "12": {
            "class_type": "KSampler",
            "inputs": {
                "seed": 42,
                "steps": 20,
                "model": [4, 0],
                "positive": [6, 0],
                "negative": [7, 0],
                "latent_image": [10, 0],
            },
        },
        "20": {"class_type": "VAEDecode", "inputs": {"samples": [12, 0], "vae": [3, 0]}},
        "21": {"class_type": "SaveImage", "inputs": {"images": [20, 0], "filename_prefix": "Demo"}},
    }


def _entry(**overrides: Any) -> dict[str, Any]:
    entry = {
        "workflowName": "my-wan",
        "workflowDigest": "sha256:x",
        "mediaType": "image",
        "binding": {
            "prompt": {"node": "6", "input": "text"},
            "outputs": [{"node": "21", "kind": "image"}],
        },
        "capabilities": {"mediaType": "image", "resolutionOptions": ["1K"]},
        "notes": "",
        "generatedAt": "2026-10-08T00:00:00Z",
        "analyzer": "DC-text",
        "schemaVersion": comfyui_adaptation.ADAPTATION_SCHEMA_VERSION,
    }
    entry.update(overrides)
    return entry


# ---------------------------------------------------------------------------
# 摘要 / 校验
# ---------------------------------------------------------------------------


def test_build_workflow_summary_marks_links_and_values() -> None:
    summary = comfyui_adaptation.build_workflow_summary(_workflow())
    assert summary["nodeCount"] == 6
    assert summary["truncated"] is False
    nodes = {node["id"]: node for node in summary["nodes"]}
    # 连线输入只报 link，不给模型当成可写标量
    assert nodes["12"]["inputs"]["negative"] == {"link": "7:0"}
    assert nodes["12"]["inputs"]["steps"] == {"value": "20"}
    assert nodes["6"]["inputs"]["text"] == {"value": "old positive"}
    assert nodes["6"]["title"] == "正向提示词"


def test_build_workflow_summary_truncates_long_values_and_nodes() -> None:
    workflow = {
        str(index): {
            "class_type": "ConstantInput",
            "inputs": {"payload": "x" * (comfyui_adaptation.ANALYZER_VALUE_MAX_CHARS + 50)},
        }
        for index in range(comfyui_adaptation.ANALYZER_MAX_NODES + 5)
    }
    summary = comfyui_adaptation.build_workflow_summary(workflow)
    assert summary["truncated"] is True
    assert len(summary["nodes"]) == comfyui_adaptation.ANALYZER_MAX_NODES
    value = summary["nodes"][0]["inputs"]["payload"]["value"]
    assert value.endswith("…")


def test_validate_binding_keeps_valid_refs_and_reports_out_of_range() -> None:
    workflow = _workflow()
    clean, errors = comfyui_adaptation.validate_binding(
        {
            "prompt": {"node": "6", "input": "text"},
            # KSampler.negative 是连线输入，不能写标量
            "negative": {"node": "12", "input": "negative"},
            "images": [{"node": "99", "input": "image"}],
            "size": {"node": "10", "width": "width", "height": "height"},
            "seed": [{"node": "12", "input": "seed"}],
            "outputs": [{"node": "21", "kind": "image"}, {"node": "21", "kind": "audio"}],
        },
        workflow,
    )
    assert clean["prompt"] == {"node": "6", "input": "text"}
    assert clean["size"] == {"node": "10", "width": "width", "height": "height"}
    assert clean["seed"] == [{"node": "12", "input": "seed"}]
    assert clean["outputs"] == [{"node": "21", "kind": "image"}]
    assert "negative" not in clean
    assert "images" not in clean
    assert any("由连线提供" in message for message in errors)
    assert any("不存在的节点 99" in message for message in errors)
    assert any("kind 只能是 image/video" in message for message in errors)


def test_validate_binding_rejects_non_object_shapes() -> None:
    clean, errors = comfyui_adaptation.validate_binding("nope", _workflow())
    assert clean == {}
    assert errors == ["binding 必须是对象"]

    clean, errors = comfyui_adaptation.validate_binding(
        {"seed": {"node": "12", "input": "seed"}}, _workflow()
    )
    assert clean == {}
    assert errors == ["binding.seed 必须是数组"]


def test_validate_capabilities_whitelists_and_drops_durations_for_image() -> None:
    declared = comfyui_adaptation.validate_capabilities(
        {
            "supportedModes": ["text_to_image", " ", "image_to_image"],
            "resolutionOptions": ["1K", "2K"],
            "minDuration": 3,
            "maxDuration": 12,
            "fps": 24,
            "humanReview": True,
        },
        "image",
    )
    assert declared == {
        "mediaType": "image",
        "supportedModes": ["text_to_image", "image_to_image"],
        "resolutionOptions": ["1K", "2K"],
        "fps": 24,
    }
    video = comfyui_adaptation.validate_capabilities({"maxDuration": 15}, "video")
    assert video["maxDuration"] == 15
    # 只有 mediaType、没有实质档位 → 不产出空声明去覆盖真值
    assert comfyui_adaptation.validate_capabilities({}, "video") == {}


def test_workflow_digest_and_channel_key_are_stable() -> None:
    first = comfyui_adaptation.workflow_digest(_workflow())
    second = comfyui_adaptation.workflow_digest(_workflow())
    assert first == second and first.startswith("sha256:")
    changed = _workflow()
    changed["10"]["inputs"]["width"] = 768
    assert comfyui_adaptation.workflow_digest(changed) != first
    # 同一地址不同写法收敛到同一个渠道文件
    assert comfyui_adaptation.channel_key(BASE_URL + "/") == comfyui_adaptation.channel_key(
        BASE_URL
    )


# ---------------------------------------------------------------------------
# 落盘 / 读取
# ---------------------------------------------------------------------------


def test_write_and_load_adaptation_roundtrip(monkeypatch, tmp_path) -> None:
    state_root = _isolate(monkeypatch, tmp_path)
    entry = _entry()
    written = comfyui_adaptation.write_adaptations(BASE_URL, {"my-model": entry})
    path = state_root / "comfyui" / "adaptations" / f"{comfyui_adaptation.channel_key(BASE_URL)}.json"
    assert Path(written["path"]) == path and path.exists()
    assert written["file"] == str(path.relative_to(state_root))
    pointer = written["pointers"]["my-model"]
    assert pointer["entry"] == "my-model" and pointer["digest"] == entry["workflowDigest"]

    settings = {"comfyui": {"adaptation_by_model": written["pointers"]}}
    assert comfyui_adaptation.load_adaptation(settings, "my-model") == entry
    assert comfyui_adaptation.load_adaptation(settings, "other-model") is None
    assert comfyui_adaptation.load_adaptation({}, "my-model") is None


def test_write_adaptations_merges_without_dropping_other_models(
    monkeypatch, tmp_path
) -> None:
    _isolate(monkeypatch, tmp_path)
    comfyui_adaptation.write_adaptations(BASE_URL, {"a": _entry()})
    second = comfyui_adaptation.write_adaptations(BASE_URL, {"b": _entry(mediaType="video")})
    settings = {"comfyui": {"adaptation_by_model": second["pointers"]}}
    # 指针表只带本次写入的模型，但文件里先前模型的适配仍在
    assert set(second["pointers"]) == {"b"}
    document = comfyui_adaptation._read_file(comfyui_adaptation.adaptation_path(BASE_URL))
    assert set(document["adaptations"]) == {"a", "b"}
    assert comfyui_adaptation.load_adaptation(
        {"comfyui": {"adaptation_by_model": document_pointers(document)}}, "a"
    ) == _entry()
    assert comfyui_adaptation.load_adaptation(settings, "b")["mediaType"] == "video"


def document_pointers(document: dict[str, Any]) -> dict[str, Any]:
    # 指针里的 file 必须是相对 STATE_DIR 的路径（和 write_adaptations 的口径一致），
    # 裸文件名会解析到适配目录外而被 _safe_adaptation_file 拒掉。
    rel = str(
        comfyui_adaptation.adaptation_path(BASE_URL).relative_to(
            comfyui_adaptation._state_root()
        )
    )
    return {
        model: {"file": rel, "entry": model}
        for model in document["adaptations"]
    }


def test_load_adaptation_rejects_pointer_outside_adaptations_dir(
    monkeypatch, tmp_path
) -> None:
    _isolate(monkeypatch, tmp_path)
    settings = {
        "comfyui": {
            "adaptation_by_model": {
                "my-model": {"file": "../../evil.json", "entry": "my-model"}
            }
        }
    }
    assert comfyui_adaptation.load_adaptation(settings, "my-model") is None


def test_load_adaptation_surfaces_corrupt_file(monkeypatch, tmp_path) -> None:
    _isolate(monkeypatch, tmp_path)
    written = comfyui_adaptation.write_adaptations(BASE_URL, {"my-model": _entry()})
    path = comfyui_adaptation.adaptation_path(BASE_URL)
    path.write_text("{ not json", encoding="utf-8")
    settings = {"comfyui": {"adaptation_by_model": written["pointers"]}}
    with pytest.raises(comfyui_adaptation.ComfyUIAdaptationError, match="重新执行"):
        comfyui_adaptation.load_adaptation(settings, "my-model")


# ---------------------------------------------------------------------------
# 分析（模型返回全部由 fake 供）
# ---------------------------------------------------------------------------


def test_extract_json_tolerates_fenced_output() -> None:
    assert comfyui_adaptation._extract_json('```json\n{"a": 1}\n```') == {"a": 1}
    assert comfyui_adaptation._extract_json('前置说明 {"a": {"b": 2}} 收尾') == {"a": {"b": 2}}
    with pytest.raises(comfyui_adaptation.ComfyUIAdaptationError, match="JSON"):
        comfyui_adaptation._extract_json("完全不是 JSON")
    with pytest.raises(comfyui_adaptation.ComfyUIAdaptationError, match="顶层结构"):
        comfyui_adaptation._extract_json("[1, 2]")


def test_resolve_analyzer_model_prefers_explicit_then_slot(monkeypatch) -> None:
    assert comfyui_adaptation.resolve_analyzer_model(" my-text-model ") == "my-text-model"
    seen: dict[str, Any] = {}

    def fake_slot(model_env: str, default_model: str, **_kwargs: Any) -> str:
        seen["model_env"] = model_env
        seen["default_model"] = default_model
        return "DC-freezone-text-writer-LLM"

    monkeypatch.setattr(config, "get_newapi_text_model_name", fake_slot)
    assert comfyui_adaptation.resolve_analyzer_model("") == "DC-freezone-text-writer-LLM"
    assert seen["model_env"] == "FREEZONE_TEXT_WRITER_MODEL"
    # 第二参数必须是兜底**槽位键**，不是模型名（req40 口径）
    assert str(seen["default_model"]).startswith("DC-")


async def test_analyze_one_builds_entry_and_flags_missing_outputs(monkeypatch) -> None:
    workflow = _workflow()

    async def fake_ask(*, summary: dict[str, Any], model: str, workflow_name: str, text_model: str):
        assert summary["nodes"] and model == "my-model"
        assert text_model == "DC-text"
        return {
            "mediaType": "IMAGE",
            "binding": {
                "prompt": {"node": "6", "input": "text"},
                "negative": {"node": "7", "input": "text"},
                "size": {"node": "10", "width": "width", "height": "height"},
                "seed": [{"node": "12", "input": "seed"}],
                "outputs": [{"node": "21", "kind": "image"}],
            },
            "capabilities": {"supportedModes": ["text_to_image"], "resolutionOptions": ["1K"]},
            "notes": "SaveImage 输出，判定为图片",
        }

    monkeypatch.setattr(comfyui_adaptation, "ask_analyzer", fake_ask)
    outcome = await comfyui_adaptation.analyze_one(
        model="my-model",
        workflow_name="my-wan",
        workflow=workflow,
        text_model="DC-text",
    )
    entry = outcome["entry"]
    assert outcome["errors"] == []
    assert entry["mediaType"] == "image"
    assert entry["workflowDigest"] == comfyui_adaptation.workflow_digest(workflow)
    assert entry["analyzer"] == "DC-text"
    assert entry["binding"]["negative"] == {"node": "7", "input": "text"}
    assert entry["capabilities"]["supportedModes"] == ["text_to_image"]

    async def fake_no_outputs(**_kwargs: Any) -> dict[str, Any]:
        return {"mediaType": "video", "binding": {"prompt": {"node": "6", "input": "text"}}}

    monkeypatch.setattr(comfyui_adaptation, "ask_analyzer", fake_no_outputs)
    outcome = await comfyui_adaptation.analyze_one(
        model="my-model", workflow_name="w", workflow=workflow, text_model="DC-text"
    )
    assert any("未能确定输出节点" in message for message in outcome["errors"])
    # 模型没给媒体类型时按输出节点推断，推断不出按 video 兜底并交给 detect
    assert outcome["entry"]["mediaType"] == "video"


# ---------------------------------------------------------------------------
# 执行器消费适配
# ---------------------------------------------------------------------------


def test_bind_uses_adaptation_nodes_and_gates_heuristics() -> None:
    workflow = _workflow()
    bound, report = bind_comfyui_workflow(
        workflow,
        prompt="a girl in the rain",
        negative_prompt="blurry",
        seed=7,
        width=1024,
        height=576,
        adaptation={
            "binding": {
                # 故意与启发式相反：节点 7 标题是「负向提示词」，启发式会把它当负向
                "prompt": {"node": "7", "input": "text"},
                "negative": {"node": "6", "input": "text"},
                "seed": [{"node": "12", "input": "seed"}],
                "size": {"node": "10", "width": "width", "height": "height"},
                "outputs": [{"node": "21", "kind": "image"}],
            }
        },
    )
    assert report.source == "adaptation"
    assert bound["7"]["inputs"]["text"] == "a girl in the rain"
    assert bound["6"]["inputs"]["text"] == "blurry"
    assert report.prompt_nodes == ["7"]
    assert report.negative_nodes == ["6"]
    assert bound["12"]["inputs"]["seed"] == 7
    assert bound["10"]["inputs"]["width"] == 1024
    assert report.size_nodes == ["10"]
    assert report.has_image_output is True and report.has_video_output is False
    # 适配里的引用全部有效 → 不该有任何回落警告
    assert report.warnings == []
    assert bound["6"]["inputs"]["clip"] == [5, 0]


def test_bind_adaptation_frames_uses_declared_fps_node() -> None:
    workflow = _workflow()
    workflow["30"] = {
        "class_type": "WanImageToVideo",
        "inputs": {"length": 81, "fps": 16, "width": 512, "height": 512},
    }
    bound, report = bind_comfyui_workflow(
        workflow,
        prompt="p",
        duration=5.0,
        adaptation={
            "binding": {
                "prompt": {"node": "6", "input": "text"},
                "frames": {"node": "30", "input": "length", "fps": {"node": "30", "input": "fps"}},
                "outputs": [{"node": "30", "kind": "video"}],
            }
        },
    )
    # 5s × 16fps = 80 → Wan 族要 4n+1
    assert bound["30"]["inputs"]["length"] == 81
    assert report.frame_nodes == ["30"]
    assert report.has_video_output is True
    assert not any("adaptation.frames" in message for message in report.warnings)


def test_bind_falls_back_to_heuristics_when_adaptation_is_stale() -> None:
    workflow = _workflow()
    bound, report = bind_comfyui_workflow(
        workflow,
        prompt="hello",
        width=1024,
        height=576,
        adaptation={
            "binding": {
                "prompt": {"node": "99", "input": "text"},
                "size": {"node": "10", "width": "nope", "height": "height"},
                "outputs": [{"node": "98", "kind": "image"}],
            }
        },
    )
    assert any("不存在的节点 99" in message for message in report.warnings)
    assert any("adaptation.size" in message for message in report.warnings)
    assert any("adaptation.outputs" in message for message in report.warnings)
    assert bound["6"]["inputs"]["text"] == "hello"
    assert report.prompt_nodes == ["6"]
    # 启发式仍按输出节点类名判定产物类型（适配的 outputs 全越界）
    assert report.has_image_output is True


# ---------------------------------------------------------------------------
# 渠道真源：适配指针 / 能力声明读取 + 媒体配置投影
# ---------------------------------------------------------------------------


def test_channel_helper_reads_pointer_and_declared_capabilities(
    monkeypatch, tmp_path
) -> None:
    from novelvideo import model_gateway_settings as settings_module

    _isolate(monkeypatch, tmp_path)
    written = comfyui_adaptation.write_adaptations(BASE_URL, {"my-model": _entry()})
    capabilities = {"mediaType": "image", "resolutionOptions": ["1K", "2K"]}
    channel_settings = {
        "comfyui": {
            "workflow_by_model": {"my-model": _workflow()},
            "adaptation_by_model": written["pointers"],
            "capabilities_by_model": {"my-model": capabilities},
        }
    }
    assert settings_module.comfyui_declared_capabilities(channel_settings, "my-model") == capabilities
    assert settings_module.comfyui_declared_capabilities(channel_settings, "other") == {}
    assert settings_module.detect_comfyui_media_type(channel_settings, "my-model") == "image"

    monkeypatch.setattr(
        settings_module,
        "get_newapi_provider_channel",
        lambda provider: {"baseUrl": BASE_URL, "settings": channel_settings}
        if provider == "comfyui"
        else {},
    )
    adaptation = settings_module.load_comfyui_channel_adaptation("my-model")
    assert adaptation and adaptation["binding"]["prompt"] == {"node": "6", "input": "text"}
    assert settings_module.load_comfyui_channel_adaptation("") is None

    # 没有声明时回落到输出节点类名探测（SaveImage → image）
    plain = {"comfyui": {"workflow_by_model": {"my-model": _workflow()}}}
    assert settings_module.detect_comfyui_media_type(plain, "my-model") == "image"
    # 渠道里没存指针 → 适配读不出来，执行链回到启发式
    monkeypatch.setattr(
        settings_module,
        "get_newapi_provider_channel",
        lambda provider: {"baseUrl": BASE_URL, "settings": plain} if provider == "comfyui" else {},
    )
    assert settings_module.load_comfyui_channel_adaptation("my-model") is None


def _gateway_client() -> TestClient:
    app = FastAPI()
    app.include_router(model_gateway.router)
    return TestClient(app)


def _patch_provisioner(monkeypatch, captured: dict[str, Any]) -> None:
    monkeypatch.setenv("NEWAPI_PROVISIONER_ENABLED", "true")
    monkeypatch.setattr(
        model_gateway,
        "get_provisioner_config",
        lambda: type("Cfg", (), {"admin_base_url": "http://new-api:3000"})(),
    )
    monkeypatch.setattr(
        model_gateway,
        "ensure_admin_access_token",
        lambda _cfg: type("Admin", (), {"access_token": "admin-secret"})(),
    )

    def fake_upsert(_cfg, _admin, payload):
        captured["payload"] = payload
        return {"ok": True, "httpStatus": 200, "newApiResponse": {"success": True}}

    monkeypatch.setattr(model_gateway, "upsert_channel", fake_upsert)


def test_declared_capabilities_replace_token_guessed_media_config(monkeypatch, tmp_path) -> None:
    """AI 声明的档位优先于按名字 token 的猜测，且不再被标记为「工作流托管」。"""

    _isolate(monkeypatch, tmp_path)
    captured: dict[str, Any] = {}
    _patch_provisioner(monkeypatch, captured)
    client = _gateway_client()

    response = client.post(
        "/model-gateway/custom/newapi/provider-channels",
        json={
            "channels": [
                {
                    "provider": "comfyui",
                    "type": 63,
                    "baseUrl": BASE_URL,
                    "settings": {
                        "comfyui": {
                            "workflow_by_model": {"wan-i2v": _workflow()},
                            "capabilities_by_model": {
                                "wan-i2v": {
                                    "mediaType": "video",
                                    "resolutionOptions": ["480p", "720p", "1080p"],
                                    "ratioOptions": ["16:9", "9:16"],
                                    "supportedModes": ["text_to_video", "image_to_video"],
                                    "minDuration": 2,
                                    "maxDuration": 10,
                                    "fps": 24,
                                }
                            },
                        }
                    },
                }
            ]
        },
    )
    assert response.status_code == 200, response.text
    config = get_newapi_media_model_mappings()["wan-i2v"]["config"]
    assert config["resolutionOptions"] == ["480p", "720p", "1080p"]
    assert config["ratioOptions"] == ["16:9", "9:16"]
    assert config["maxDuration"] == 10
    assert config["fps"] == 24
    assert "_dcManagedByWorkflow" not in config
    # 未声明的键仍来自默认（request 端点由媒体类型决定）
    assert config["request"]["endpoint"] == "video/generations"


def test_declared_capabilities_do_not_override_user_authored_keys(
    monkeypatch, tmp_path
) -> None:
    """用户在模型广场手动编过的档位不被 AI 声明改掉，只补缺失的键。"""

    _isolate(monkeypatch, tmp_path)
    save_newapi_media_model_mappings(
        {
            "wan-i2v": {
                "provider": "comfyui",
                "mediaType": "video",
                "config": {"request": {"endpoint": "video/generations"}, "ratioOptions": ["4:3"]},
            }
        }
    )
    captured: dict[str, Any] = {}
    _patch_provisioner(monkeypatch, captured)
    client = _gateway_client()

    response = client.post(
        "/model-gateway/custom/newapi/provider-channels",
        json={
            "channels": [
                {
                    "provider": "comfyui",
                    "type": 63,
                    "baseUrl": BASE_URL,
                    "settings": {
                        "comfyui": {
                            "workflow_by_model": {"wan-i2v": _workflow()},
                            "capabilities_by_model": {
                                "wan-i2v": {
                                    "mediaType": "video",
                                    "resolutionOptions": ["720p"],
                                    "ratioOptions": ["16:9"],
                                }
                            },
                        }
                    },
                }
            ]
        },
    )
    assert response.status_code == 200, response.text
    config = get_newapi_media_model_mappings()["wan-i2v"]["config"]
    assert config["ratioOptions"] == ["4:3"]
    assert config["resolutionOptions"] == ["720p"]


# ---------------------------------------------------------------------------
# POST /custom/newapi/comfyui/ai-adapt
# ---------------------------------------------------------------------------


def _patch_analyzer(monkeypatch, answers: dict[str, dict[str, Any]]) -> list[str]:
    """把「连 ComfyUI 取工作流」与「调分析模型」都换成本地假数据。"""

    called: list[str] = []

    async def fake_resolve(_base_url: str, items: list[dict[str, Any]]):
        workflows = {item["model"]: _workflow() for item in items}
        return workflows, []

    async def fake_ask(*, summary: dict[str, Any], model: str, workflow_name: str, text_model: str):
        called.append(f"{model}|{workflow_name}|{text_model}")
        return answers[model]

    module = model_gateway.comfyui
    monkeypatch.setattr(module, "resolve_comfyui_workflows", fake_resolve)
    monkeypatch.setattr(comfyui_adaptation, "ask_analyzer", fake_ask)
    return called


def _ai_adapt_payload(**overrides: Any) -> dict[str, Any]:
    payload = {
        "baseUrl": BASE_URL,
        "items": [{"model": "my-model", "workflowName": "my-wan"}],
        "textModel": "my-analyzer",
    }
    payload.update(overrides)
    return payload


def test_ai_adapt_route_writes_adaptation_and_returns_pointers(
    monkeypatch, tmp_path
) -> None:
    state_root = _isolate(monkeypatch, tmp_path)
    called = _patch_analyzer(
        monkeypatch,
        {
            "my-model": {
                "mediaType": "image",
                "binding": {
                    "prompt": {"node": "6", "input": "text"},
                    "size": {"node": "10", "width": "width", "height": "height"},
                    "outputs": [{"node": "21", "kind": "image"}],
                },
                "capabilities": {"supportedModes": ["text_to_image"], "resolutionOptions": ["1K"]},
                "notes": "SaveImage 输出",
            }
        },
    )
    client = _gateway_client()
    response = client.post("/model-gateway/custom/newapi/comfyui/ai-adapt", json=_ai_adapt_payload())
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert called == ["my-model|my-wan|my-analyzer"]
    assert data["analyzerModel"] == "my-analyzer"
    assert data["adapted"] == ["my-model"]
    assert data["errors"] == []
    assert data["typeByModel"] == {"my-model": "image"}
    assert data["workflowNameByModel"] == {"my-model": "my-wan"}
    assert data["capabilitiesByModel"]["my-model"]["resolutionOptions"] == ["1K"]
    pointer = data["adaptationPointers"]["my-model"]
    assert (state_root / pointer["file"]).exists()
    settings = {"comfyui": {"adaptation_by_model": data["adaptationPointers"]}}
    loaded = comfyui_adaptation.load_adaptation(settings, "my-model")
    assert loaded["binding"]["prompt"] == {"node": "6", "input": "text"}


def test_ai_adapt_route_reports_per_model_failures_without_silence(
    monkeypatch, tmp_path
) -> None:
    _isolate(monkeypatch, tmp_path)
    answers = {
        "good": {
            "mediaType": "image",
            "binding": {"outputs": [{"node": "21", "kind": "image"}]},
        },
        "bad": {"mediaType": "image", "binding": {"outputs": [{"node": "77", "kind": "image"}]}},
    }

    async def fake_resolve(_base_url: str, items: list[dict[str, Any]]):
        # 解析不到工作流的模型只进 errors，不会出现在 workflow_by_model 里
        resolved = {item["model"]: _workflow() for item in items if item["model"] != "missing"}
        return resolved, [
            {"model": "missing", "workflowName": "gone", "error": "在 ComfyUI 工作流列表里找不到该名称"}
        ]

    async def fake_ask(*, summary: dict[str, Any], model: str, workflow_name: str, text_model: str):
        if model == "boom":
            raise comfyui_adaptation.ComfyUIAdaptationError("分析模型网关返回 401：unauthorized")
        return answers[model]

    monkeypatch.setattr(model_gateway.comfyui, "resolve_comfyui_workflows", fake_resolve)
    monkeypatch.setattr(comfyui_adaptation, "ask_analyzer", fake_ask)
    client = _gateway_client()
    response = client.post(
        "/model-gateway/custom/newapi/comfyui/ai-adapt",
        json=_ai_adapt_payload(
            items=[
                {"model": "good", "workflowName": "g"},
                {"model": "bad", "workflowName": "b"},
                {"model": "boom", "workflowName": "x"},
                {"model": "missing", "workflowName": "gone"},
            ]
        ),
    )
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    # 只有拿到可用输出节点绑定的模型才落指针
    assert data["adapted"] == ["good"]
    assert set(data["adaptationPointers"]) == {"good"}
    # 同一个模型可能有两跳错误（越界输出 + 因此不可用），按模型归集不能只留最后一条
    errors: dict[str, list[str]] = {}
    for item in data["errors"]:
        errors.setdefault(str(item["model"]), []).append(str(item["error"]))
    assert any("不存在的节点 77" in message for message in errors["bad"])
    assert any("未能确定输出节点" in message for message in errors["bad"])
    assert "分析模型网关返回 401：unauthorized" in errors["boom"]
    assert "在 ComfyUI 工作流列表里找不到该名称" in errors["missing"]
    # 上游错误原文逐条透，不能只回一句“部分失败”
    assert set(errors) >= {"bad", "boom", "missing"}


def test_ai_adapt_route_validation_and_unbound_text_model(monkeypatch, tmp_path) -> None:
    from novelvideo.model_slots import ModelSlotNotConfigured

    _isolate(monkeypatch, tmp_path)
    client = _gateway_client()
    assert client.post(
        "/model-gateway/custom/newapi/comfyui/ai-adapt",
        json=_ai_adapt_payload(baseUrl=""),
    ).status_code == 400
    assert client.post(
        "/model-gateway/custom/newapi/comfyui/ai-adapt",
        json=_ai_adapt_payload(items=[]),
    ).status_code == 400

    def raise_unbound(*_args: Any, **_kwargs: Any) -> str:
        raise ModelSlotNotConfigured(
            "FREEZONE_TEXT_WRITER_MODEL", "槽位未绑定模型", detail="去设置里选"
        )

    monkeypatch.setattr(config, "get_newapi_text_model_name", raise_unbound)
    response = client.post(
        "/model-gateway/custom/newapi/comfyui/ai-adapt",
        json=_ai_adapt_payload(textModel=""),
    )
    # 文本功能位未绑定必须是 400 原话，不能被兜底分支吞成 500
    assert response.status_code == 400
    assert "FREEZONE_TEXT_WRITER_MODEL" in response.json()["detail"]


def test_ai_adapt_route_connection_failure_keeps_original_error(
    monkeypatch, tmp_path
) -> None:
    _isolate(monkeypatch, tmp_path)

    async def fake_resolve(_base_url: str, _items: list[dict[str, Any]]):
        raise ValueError("连接 ComfyUI 失败：Connection refused")

    monkeypatch.setattr(model_gateway.comfyui, "resolve_comfyui_workflows", fake_resolve)
    client = _gateway_client()
    response = client.post(
        "/model-gateway/custom/newapi/comfyui/ai-adapt", json=_ai_adapt_payload()
    )
    assert response.status_code == 400
    assert "Connection refused" in response.json()["detail"]
