# [aipdd fork delta] WP-E: 模型广场（Model Plaza）——四个 connector 全量收录、状态判定
# 与 WP-B 共用同一套判定、响应体不含任何计费字段、探活红线（非 OpenAI 兼容一律
# skipped 且绝不建连）。清单聚合本身永不远程调用，所以这些用例都不需要 mock 上游。
# （原 audio 语音合成与 official-catalog 官方目录两个来源已按 aipdd 要求整体移除。）
from __future__ import annotations

import json
from urllib.parse import quote

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from novelvideo import config
from novelvideo import model_gateway_settings
from novelvideo import model_plaza
from novelvideo import model_slots
from novelvideo import newapi_provisioner
from novelvideo.api.routes import model_gateway
from novelvideo.model_gateway_settings import (
    save_custom_newapi_gateway,
    save_mediakit_config,
    save_newapi_embedding_model_config,
    save_newapi_media_model_mappings,
    save_newapi_model_alias_mappings,
    save_newapi_provider_channels,
    save_upstream_models_snapshot,
)

# 一个 SaveImage 输出的标准 API Format 工作流：媒体类型由输出节点推断，不靠模型名。
COMFYUI_IMAGE_WORKFLOW = {
    "4": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": "sd-xl.safetensors"}},
    "9": {"class_type": "SaveImage", "inputs": {"images": ["8", 0]}},
}
COMFYUI_CHANNEL_SETTINGS = {
    "comfyui": {"workflow_by_model": {"sd-xl-workflow": COMFYUI_IMAGE_WORKFLOW}}
}

GATEWAY_KEY = "sk-gateway-secret-value"

PROBE_FORBIDDEN_KINDS = (
    model_plaza.INVOKE_COMFYUI_WORKFLOW,
    model_plaza.INVOKE_MEDIAKIT_TASK,
    model_plaza.INVOKE_ASYNC_VIDEO_TASK,
    model_plaza.INVOKE_TTS_VOICE,
)


def _clear_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in (
        "ST_CONTROL_PLANE_DSN",
        "MODEL_GATEWAY_MODE",
        "MODEL_GATEWAY_RUNTIME_VERSION",
        "NEWAPI_API_KEY",
        "NEWAPI_BASE_URL",
        "NEWAPI_PUBLIC_BASE_URL",
        "TEXT_MODEL_FALLBACK",
        "VISION_MODEL_FALLBACK",
        "NEWAPI_TEXT_MODEL",
        "NEWAPI_IMAGE_MODEL",
        "NEWAPI_NANOBANANA2_MODEL",
        "COGNEE_EMBEDDING_MODEL",
        "INDEXTTS2_NEWAPI_MODEL",
        "TTS_PROVIDER",
        "MEDIAKIT_API_KEY",
        "MEDIAKIT_BASE_URL",
        "VIDEO_ENHANCE_BACKEND",
        "DASHSCOPE_API_KEY",
        "MODEL_API_KEY",
        "OPENAI_API_KEY",
    ):
        monkeypatch.delenv(key, raising=False)


def _seed_full_plaza(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    """把四个 connector 都喂上真配置：网关 / comfyui / MediaKit / env。"""
    monkeypatch.setattr(config, "STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("ST_EDITION", "ce")
    _clear_env(monkeypatch)
    # peek 是进程级缓存，别的用例可能已经写过：目录测试一律固定成「本轮没算过」，
    # 让状态判定走 plaza 自己的本地回落分支，结果才与 STATE_DIR 无关。
    monkeypatch.setattr(
        newapi_provisioner,
        "peek_alias_mapping_health",
        lambda: {"status": "pending", "checked": False},
    )

    save_custom_newapi_gateway(base_url="http://newapi:3000/v1", api_key=GATEWAY_KEY)
    save_newapi_provider_channels(
        [
            {
                "provider": "aipdd",
                "baseUrl": "https://api.aipdd.example/v1",
                "upstreamKey": "sk-aipdd-secret",
            },
            {
                "provider": "ark",
                "baseUrl": "https://ark.example.com/api/v3",
                "upstreamKey": "sk-ark-secret",
            },
            {
                "provider": "comfyui",
                "baseUrl": "http://127.0.0.1:8188",
                "settings": COMFYUI_CHANNEL_SETTINGS,
            },
        ]
    )
    # [req40] 渠道目录是模型名的唯一来源：用例里要出卡的真名必须先「拉到过」。
    save_upstream_models_snapshot(
        "aipdd",
        [
            {"name": "glm-4.6", "mediaType": "text", "endpoints": ["chat/completions"]},
            {"name": "seedream-4-0", "mediaType": "image", "endpoints": ["images/generations"]},
            {
                "name": "doubao-embedding-vision",
                "mediaType": "embedding",
                "endpoints": ["embeddings"],
                "dimensions": 1024,
            },
        ],
        base_url="https://api.aipdd.example/v1",
    )
    save_upstream_models_snapshot(
        "ark",
        [
            {
                "name": "doubao-vision",
                "mediaType": "vision",
                "endpoints": ["chat/completions"],
                "inputModalities": ["text", "image"],
            },
            {
                "name": "seed-vision",
                "mediaType": "vision",
                "endpoints": ["chat/completions"],
                "inputModalities": ["text", "image"],
            },
            {"name": "doubao-seedance-1-0", "mediaType": "video", "endpoints": ["videos"]},
        ],
        base_url="https://ark.example.com/api/v3",
    )
    save_newapi_model_alias_mappings(
        {
            # 真实在册的功能位别名（HERMES_MODEL / SCENE_HEADER_AUTOGEN_MODEL）。
            "DC-hermes-LLM": {
                "provider": "aipdd",
                "target": "glm-4.6",
                "capability": "text",
            },
            "DC-freezone-vision-LLM": {
                "provider": "ark",
                "target": "doubao-vision",
                "capability": "vision",
                "fallbackProvider": "aipdd",
                "fallbackTarget": "seed-vision",
            },
            # provider 不在渠道表里：状态该是 noChannel（与 WP-B 同一套词表）。
            "DC-orphan-LLM": {"provider": "ghost", "target": "gone-model"},
        }
    )
    save_newapi_media_model_mappings(
        {
            "seedream-4": {
                "provider": "aipdd",
                "mediaType": "image",
                "upstreamModel": "seedream-4-0",
                "label": "Seedream 4",
                "sortOrder": 1,
            },
            "Doubao-Seedance": {
                "provider": "ark",
                "mediaType": "video",
                "upstreamModel": "doubao-seedance-1-0",
                "sortOrder": 2,
            },
            "off-balance": {
                "provider": "aipdd",
                "mediaType": "image",
                "enabled": False,
                "sortOrder": 3,
            },
            # comfyui 的媒体模型归 comfyui 分区，不在 gateway 分区重复列一遍。
            "sd-xl-workflow": {"provider": "comfyui", "mediaType": "image", "sortOrder": 4},
        }
    )
    save_newapi_embedding_model_config(
        provider="aipdd",
        upstream_model="doubao-embedding-vision",
        dimension=1024,
    )
    save_mediakit_config(api_key="mk-secret-value", base_url="https://mediakit.example/api/v1")


def _by_id(data: dict) -> dict[str, dict]:
    return {entry["id"]: entry for entry in data["models"]}


def test_plaza_lists_all_connectors_including_comfyui_and_mediakit(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    _seed_full_plaza(monkeypatch, tmp_path)
    data = model_plaza.build_model_plaza()

    assert [item["id"] for item in data["connectors"]] == list(model_plaza.PLAZA_CONNECTORS)
    counts = {item["id"]: item["modelCount"] for item in data["connectors"]}
    # 计划硬要求：comfyui / MediaKit 必须真有条目，不能是空配。
    for connector in model_plaza.PLAZA_CONNECTORS:
        assert counts[connector] > 0, f"{connector} 分区不能为空"
    assert counts["comfyui"] == 1
    assert counts["mediakit"] == 1

    entries = _by_id(data)
    assert entries[f"{model_plaza.CONNECTOR_COMFYUI}:sd-xl-workflow"]["invokeKind"] == (
        model_plaza.INVOKE_COMFYUI_WORKFLOW
    )
    assert entries[f"{model_plaza.CONNECTOR_MEDIAKIT}:video-enhance"]["status"] == (
        model_plaza.STATUS_MAPPED
    )
    # 「模型功能位」分区把全部功能位列出来，包括仍走默认别名的（登记表 = model_slots）。
    assert counts["model-slot"] == len(model_slots.FEATURE_SLOTS)


def test_plaza_gateway_entries_share_alias_health_verdict(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    _seed_full_plaza(monkeypatch, tmp_path)
    data = model_plaza.build_model_plaza()
    entries = _by_id(data)

    # [aipdd fork delta] 拍平：每个 (渠道, 模型) 一条，id / 调用名都是复合名
    # {provider}::{model}；展示名与 upstreamModel 保留上游真名，同名多渠道并列。
    text = entries["gateway:aipdd::glm-4.6"]
    assert text["status"] == model_plaza.STATUS_MAPPED
    assert text["note"] == ""
    assert text["modelName"] == "aipdd::glm-4.6"
    assert text["upstreamModel"] == "glm-4.6"
    assert text["capabilities"] == ["text"]
    # [aipdd fork delta] 没绑的文本位现在自动跟随同类里已绑好的 HERMES（兜底第一档），
    # 这张卡的 inUse 因此覆盖全部文本位 —— 广场报的就是实际会跑的那条路由。
    assert set(text["inUseBy"]) == {
        env for env, slot in model_slots.FEATURE_SLOTS.items()
        if slot.capability == "text"
    }
    assert {"HERMES_MODEL", "SCENE_HEADER_AUTOGEN_MODEL"} <= set(text["inUseBy"])

    vision = entries["gateway:ark::doubao-vision"]
    # 拍平后备援退役：渠道可用即无备注，也不再有 fallback 字段；能力声明照旧
    # 按真名反查到这张卡（DC-freezone-vision-LLM -> doubao-vision）。
    assert vision["note"] == ""
    assert "fallbackProvider" not in vision
    assert "fallbackModel" not in vision
    assert vision["capabilities"] == ["vision", "text"]

    # DC-* 内部别名一律不再作为「模型」出现在 gateway 分区；悬空别名
    # （DC-orphan-LLM -> ghost/gone-model）没有可服务的在册渠道，不摆卡。
    gateway_ids = [
        entry["id"] for entry in data["models"] if entry["connector"] == "gateway"
    ]
    assert all(not entry_id.startswith("gateway:DC-") for entry_id in gateway_ids)
    assert "gateway:DC-orphan-LLM" not in entries
    assert "gateway:gone-model" not in entries

    # WP-B 的自检结果一旦可用就直接采信（按 target 反查到真名卡片），plaza 不再另起一套判定。
    health = {
        "status": "ok",
        "checked": True,
        "aliases": {"DC-hermes-LLM": {"status": model_plaza.STATUS_STALE}},
        "staleTargets": [{"alias": "DC-hermes-LLM", "provider": "aipdd", "target": "glm-4.6"}],
    }
    assert "staleTargets" not in data["aliasHealth"]
    reseeded = _by_id(model_plaza.build_model_plaza(health=health))
    assert reseeded["gateway:aipdd::glm-4.6"]["status"] == model_plaza.STATUS_STALE
    assert reseeded["gateway:ark::doubao-vision"]["status"] == model_plaza.STATUS_MAPPED
    assert data["aliasHealth"]["status"] == "pending"


def test_plaza_media_and_embedding_capability_grouping(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    _seed_full_plaza(monkeypatch, tmp_path)
    entries = _by_id(model_plaza.build_model_plaza())

    image = entries["gateway:seedream-4"]
    assert image["capabilities"] == ["image"]
    assert image["invokeKind"] == model_plaza.INVOKE_OPENAI_IMAGES
    assert image["endpoint"] == "images/generations"
    assert image["upstreamModel"] == "seedream-4-0"
    assert image["displayName"] == "Seedream 4"
    # [aipdd fork delta] 调用名 = 上游真名：登记名 seedream-4 只做内部桥接。
    assert image["modelName"] == "seedream-4-0"

    video = entries["gateway:Doubao-Seedance"]
    assert video["capabilities"] == ["video"]
    assert video["invokeKind"] == model_plaza.INVOKE_ASYNC_VIDEO_TASK
    # [aipdd fork delta req41] 端点来自渠道目录：ark 目录给 doubao-seedance-1-0 写明的是
    # ``videos``，媒体卡就照声明给（代码里那张通用 OpenAI 表只在目录没说话时兜底）。
    assert video["endpoint"] == "videos"
    assert video["modelName"] == "doubao-seedance-1-0"

    disabled = entries["gateway:off-balance"]
    assert disabled["status"] == model_plaza.STATUS_NO_CHANNEL
    assert disabled["note"] == "disabled"

    assert "gateway:sd-xl-workflow" not in entries

    # embedding 卡同样以真名对外：内部名 DC-cognee-embedding 不再是调用名。
    embedding = entries["gateway:doubao-embedding-vision"]
    assert embedding["capabilities"] == ["embedding"]
    assert embedding["invokeKind"] == model_plaza.INVOKE_OPENAI_EMBEDDINGS
    assert embedding["modelName"] == "doubao-embedding-vision"
    assert embedding["upstreamModel"] == "doubao-embedding-vision"
    assert embedding["dimension"] == 1024
    assert embedding["status"] == model_plaza.STATUS_MAPPED
    assert "gateway:DC-cognee-embedding" not in entries

    comfyui = entries["comfyui:sd-xl-workflow"]
    # 媒体类型由输出节点推断（SaveImage -> image），不硬编码模型名。
    assert comfyui["mediaType"] == "image"
    assert comfyui["capabilities"] == ["image"]
    assert comfyui["baseUrl"] == "http://127.0.0.1:8188"


def test_plaza_media_endpoint_follows_catalog_declaration(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    """[aipdd fork delta req41] 媒体卡的端点以渠道目录声明为准，声明缺失才回通用路径。

    AIPDD / 火山这类确定渠道的上游会在目录里给自己的模型写 ``videos``、
    ``images/edits`` 这些真实路径；以前媒体行把通用 OpenAI 路径写死在代码里，照卡片
    复制出去的 ``/video/generations`` 在 AIPDD 上直接 404。目录条目只有名字（自建
    newapi 网关的常态）时，才退回通用路径。
    """
    _seed_full_plaza(monkeypatch, tmp_path)
    save_upstream_models_snapshot(
        "aipdd",
        [
            {"name": "glm-4.6", "mediaType": "text", "endpoints": ["chat/completions"]},
            {"name": "seedream-4-0", "mediaType": "image", "endpoints": ["images/generations"]},
            {
                "name": "doubao-embedding-vision",
                "mediaType": "embedding",
                "endpoints": ["embeddings"],
                "dimensions": 1024,
            },
            {
                "name": "vid-declared",
                "mediaType": "video",
                "endpoints": ["videos", "videos/{taskId}", "videos/{taskId}/content"],
            },
            {
                "name": "img-both",
                "mediaType": "image",
                "endpoints": ["images/generations", "images/edits"],
            },
            # 只有名字、没带元数据：等于目录没说话。
            {"name": "vid-silent", "mediaType": "video"},
        ],
        base_url="https://api.aipdd.example/v1",
    )
    save_newapi_media_model_mappings(
        {
            **model_gateway_settings.get_newapi_media_model_mappings(),
            "declared-vid": {
                "provider": "aipdd",
                "mediaType": "video",
                "upstreamModel": "vid-declared",
            },
            "silent-vid": {
                "provider": "aipdd",
                "mediaType": "video",
                "upstreamModel": "vid-silent",
            },
            "both-img": {
                "provider": "aipdd",
                "mediaType": "image",
                "upstreamModel": "img-both",
            },
        }
    )
    # 示例里的对外地址靠请求 origin（本例直接调 build，模拟路由层已记下 origin）。
    model_plaza.set_request_origin("https://plaza.test")
    try:
        entries = _by_id(model_plaza.build_model_plaza())
    finally:
        model_plaza.set_request_origin("")

    assert entries["gateway:declared-vid"]["endpoint"] == "videos"
    assert entries["gateway:silent-vid"]["endpoint"] == "video/generations"

    # [aipdd fork delta req41] 轮询示例也照目录：AIPDD 声明了 ``videos/{taskId}``，
    # 卡上提交走 ``/videos`` 而轮询写死 ``/video/generations/<TASK_ID>`` 的话，用户
    # 复制第二条命令必 404。目录没声明端点时才按 ``{submit}/<TASK_ID>`` 拼。
    declared_poll = entries["gateway:declared-vid"]["examples"]["poll"]
    assert "https://plaza.test/gateway/v1/videos/<TASK_ID>" in declared_poll
    assert "video/generations" not in declared_poll
    silent_poll = entries["gateway:silent-vid"]["examples"]["poll"]
    assert "/video/generations/<TASK_ID>" in silent_poll

    # [aipdd fork delta req41] 目录同时声明了文生图与图生图：卡片主端点还是 generations，
    # 但图生图（``images/edits``）得在卡内并列一条可复制示例，不能只教一半。
    both = entries["gateway:both-img"]
    assert both["endpoint"] == "images/generations"
    assert "images/edits" in both["examples"]["edit"]
    assert "https://plaza.test/gateway/v1/images/edits" in both["examples"]["edit"]
    assert "<API_KEY>" in both["examples"]["edit"]
    # 只声明了 generations 的模型不多这条页签。
    assert "edit" not in entries["gateway:seedream-4"]["examples"]


def test_plaza_channel_names_follow_fork_provider_labels(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    """[aipdd fork delta] 渠道展示名与前端 FORK_PROVIDER_LABELS 对齐。

    provider=openai 是用户附加的「其它额外渠道」（非默认五家），显示 OpenAI
    而不是裸 id，也不与自配网关 newapi→NewAPI 混同——模型广场卡片与筛选器
    都读 channelName。
    """
    _seed_full_plaza(monkeypatch, tmp_path)
    save_newapi_provider_channels(
        [
            *model_gateway_settings.get_newapi_provider_channels(),
            {
                "provider": "openai",
                "baseUrl": "https://api.newapi.example/v1",
                "upstreamKey": "sk-openai-secret",
            },
        ]
    )
    save_newapi_model_alias_mappings(
        {
            **model_gateway_settings.get_newapi_model_alias_mappings(),
            "AP Seedance-2.0 标准版": {
                "provider": "openai",
                "target": "AP Seedance-2.0 标准版",
            },
        }
    )
    # [req40] 这个名字只有在 openai 渠道被拉到过才存在（配置声明不再造模型）。
    save_upstream_models_snapshot(
        "openai", ["AP Seedance-2.0 标准版"], base_url="https://api.newapi.example/v1"
    )
    entries = _by_id(model_plaza.build_model_plaza())

    # 直通条目（拍平复合名）：openai -> OpenAI、aipdd -> AIPDD。
    video = entries["gateway:openai::AP Seedance-2.0 标准版"]
    assert video["provider"] == "openai"
    assert video["channelName"] == "OpenAI"

    hermes = entries["gateway:aipdd::glm-4.6"]
    assert hermes["provider"] == "aipdd"
    assert hermes["channelName"] == "AIPDD"

    # 媒体 / embedding 条目同表映射。
    assert entries["gateway:seedream-4"]["channelName"] == "AIPDD"
    assert entries["gateway:doubao-embedding-vision"]["channelName"] == "AIPDD"

    # 「模型功能位」条目同样不再把 provider id 当展示名。
    assert entries["model-slot:HERMES_MODEL"]["channelName"] == "AIPDD"

    # 其余渠道取自身 id；自带固定展示名的条目不受映射影响。
    assert entries["gateway:Doubao-Seedance"]["channelName"] == "ark"
    assert entries["comfyui:sd-xl-workflow"]["channelName"] == "ComfyUI"


def test_plaza_classifies_imported_media_aliases_with_the_project_classifier(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    """[aipdd fork delta] WP-A 反向导入的媒体别名不得当成 openai-chat 展示。

    这类条目只存在于网关 channels.model_mapping、项目侧没有媒体元数据；归错类会
    连带调用示例与探活一起错（实测网关回 404 / 「价格未配置」）。归类复用
    media_model_discovery.guess_model_kind（分类真源），不新增模型名硬编码。
    """
    _seed_full_plaza(monkeypatch, tmp_path)
    save_newapi_model_alias_mappings(
        {
            **model_gateway_settings.get_newapi_model_alias_mappings(),
            "AP Seedance-2.0 标准版": {"provider": "aipdd", "target": "doubao-seedance-2-0"},
            "ap-jimeng-image-4.0": {"provider": "aipdd", "target": "jimeng-4-0"},
        }
    )
    # [req40] 目录里只回名字（无元数据）= inferred 条目，归类才允许走关键词兜底。
    save_upstream_models_snapshot(
        "aipdd",
        ["glm-4.6", "doubao-seedance-2-0", "jimeng-4-0"],
        base_url="https://api.aipdd.example/v1",
    )
    entries = _by_id(model_plaza.build_model_plaza())

    # 条目真名挂在归属渠道的复合名下；归类复用 media_model_discovery.guess_model_kind。
    video = entries["gateway:aipdd::doubao-seedance-2-0"]
    assert video["capabilities"] == ["video"]
    assert video["invokeKind"] == model_plaza.INVOKE_ASYNC_VIDEO_TASK
    assert video["endpoint"] == "video/generations"
    assert video["testable"] is False

    image = entries["gateway:aipdd::jimeng-4-0"]
    assert image["capabilities"] == ["image"]
    assert image["invokeKind"] == model_plaza.INVOKE_OPENAI_IMAGES
    assert image["endpoint"] == "images/generations"
    assert image["testable"] is False

    # 文本 / 视觉真名的原有判定不受影响（名字里带 LLM 的别名归到其 target 上）。
    text = entries["gateway:aipdd::glm-4.6"]
    assert text["invokeKind"] == model_plaza.INVOKE_OPENAI_CHAT
    assert text["capabilities"] == ["text"]
    vision = entries["gateway:ark::doubao-vision"]
    assert vision["invokeKind"] == model_plaza.INVOKE_OPENAI_CHAT
    assert vision["capabilities"] == ["vision", "text"]


def test_plaza_gateway_lists_snapshot_truth_names_and_skips_unusable_ones(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    """[aipdd fork delta] 上游清单快照是真名直通的第二数据源（零远程）。

    快照里的真名照常出卡（展示什么就能调什么），调用名 = 渠道复合名；ComfyUI
    是工作流渠道、含逗号的名字会破坏渠道 models 字段，都必须排除；快照与配置
    声明的真名并集去重，同一 (渠道, 模型) 只出一条。
    """
    _seed_full_plaza(monkeypatch, tmp_path)
    save_upstream_models_snapshot(
        "aipdd", ["ap-doubao-seed-2.1-turbo", "glm-4.6", "bad,name"]
    )
    save_upstream_models_snapshot("comfyui", ["sd3-workflow"])
    data = model_plaza.build_model_plaza()
    entries = _by_id(data)

    turbo = entries["gateway:aipdd::ap-doubao-seed-2.1-turbo"]
    assert turbo["provider"] == "aipdd"
    assert turbo["channelName"] == "AIPDD"
    assert turbo["modelName"] == "aipdd::ap-doubao-seed-2.1-turbo"
    assert turbo["upstreamModel"] == "ap-doubao-seed-2.1-turbo"
    assert turbo["status"] == model_plaza.STATUS_MAPPED

    # 快照与配置声明并集去重：glm-4.6 只出一条（同一 (渠道, 模型) 不摆两张）。
    gateway_ids = [
        entry["id"] for entry in data["models"] if entry["connector"] == "gateway"
    ]
    assert gateway_ids.count("gateway:aipdd::glm-4.6") == 1

    # comfyui 无上游清单语义；含逗号名无法进渠道 models/mapping，两者都不直通。
    assert "gateway:sd3-workflow" not in entries
    assert "gateway:bad,name" not in entries


def test_plaza_in_use_by_reverse_lookup_respects_effective_layer(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    _seed_full_plaza(monkeypatch, tmp_path)
    entries = _by_id(model_plaza.build_model_plaza())

    # 默认层是官方别名：这些功能位应反查回同一个真名（同一份 routes，不误计）。
    routes = model_plaza._effective_feature_routes()
    target = entries["gateway:aipdd::glm-4.6"]
    assert target["inUseBy"] == sorted(
        env for env, (model, _layer) in routes.items() if model == "DC-hermes-LLM"
    )
    # [aipdd fork delta] 不止自己绑过的那两位：剩下的文本功能位也都反查落在这同一个
    # 真名上 —— 它们没绑定，实际会跑在同类已绑位的调用名上（model_slots 的两档兜底）。
    # 广场回答的是「这一步现在实际跑在哪个模型上」，不是「谁自己点过绑定」。
    assert target["inUseBy"] == sorted(
        env
        for env, slot in model_slots.FEATURE_SLOTS.items()
        if slot.capability == "text"
    )
    assert {"HERMES_MODEL", "SCENE_HEADER_AUTOGEN_MODEL"} <= set(target["inUseBy"])

    # 图 / 影 / 音的默认模型位反查 default_<media>_model。
    assert entries["gateway:seedream-4"]["inUseBy"] == ["default_image_model"]
    assert entries["gateway:Doubao-Seedance"]["inUseBy"] == ["default_video_model"]
    assert entries["gateway:doubao-embedding-vision"]["inUseBy"] == [
        "knowledge_graph_embedding"
    ]

    # [aipdd fork delta] 环境变量不再参与模型选定：把旧的 per-feature env 与两档全局
    # 兜底全设上，反查落点与功能位卡片都必须一动不动 —— 设置页看不见的名字不能赢过用户
    # 的绑定，这是「模型只读设置、不读 .env」的可执行契约。
    monkeypatch.setenv("TEXT_MODEL_FALLBACK", "ap-glm-5.2")
    monkeypatch.setenv("VISION_MODEL_FALLBACK", "ap-glm-5.2")
    monkeypatch.setenv("NEWAPI_TEXT_MODEL", "ap-glm-5.2")
    monkeypatch.setenv("HERMES_MODEL", "ap-glm-5.2")
    monkeypatch.setenv("STYLE_ANALYZER_MODEL", "ap-vision-pro")
    refreshed = _by_id(model_plaza.build_model_plaza())
    assert refreshed["gateway:aipdd::glm-4.6"]["inUseBy"] == entries[
        "gateway:aipdd::glm-4.6"
    ]["inUseBy"]
    # 视觉位与文本位各自留在自己的能力档上（带图调用不被纯文本模型接管）。
    vision_users = refreshed["gateway:ark::doubao-vision"]["inUseBy"]
    assert "FREEZONE_VISION_MODEL" in vision_users
    assert vision_users == sorted(
        env
        for env, slot in model_slots.FEATURE_SLOTS.items()
        if slot.capability == "vision"
    )
    hermes = refreshed["model-slot:HERMES_MODEL"]
    assert hermes["note"] == "defaultAlias"
    assert hermes["modelName"] == "glm-4.6"
    # 标题是模型名（方便调用），功能位键与中文用途回填 extra。
    assert hermes["displayName"] == "glm-4.6"
    assert hermes["slot"] == "HERMES_MODEL"
    assert hermes["slotLabel"] == model_slots.FEATURE_SLOTS["HERMES_MODEL"].label


def test_plaza_model_slot_cards_show_model_names_as_titles(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    """[aipdd fork delta] 功能位键是业务标识：标题放模型名，键名与中文用途回填 extra。

    这类卡片的存在价值是「这个功能位现在实际跑在哪个模型上」——展示层给能直接调通的
    模型名，功能位键只在 extra 里保留身份（前端渲染「模型功能位」行、排错时定位）。
    """
    _seed_full_plaza(monkeypatch, tmp_path)
    entries = _by_id(model_plaza.build_model_plaza())

    # 默认层：这一位自己没绑定，实际兜到同类已绑位的调用名上 —— 标题给能直接调通的
    # 真名（不是会撞 503 的槽位键），层级单列 autoFallback，不装作它自己绑过。
    screenplay = entries["model-slot:SCREENPLAY_NORMALIZER_MODEL"]
    assert screenplay["displayName"] == "glm-4.6"
    assert screenplay["note"] == "autoFallback"
    assert screenplay["slot"] == "SCREENPLAY_NORMALIZER_MODEL"
    assert screenplay["slotLabel"] == model_slots.FEATURE_SLOTS[
        "SCREENPLAY_NORMALIZER_MODEL"
    ].label

    # 自己绑过的那一位仍是 defaultAlias：两层不能混同。
    hermes = entries["model-slot:HERMES_MODEL"]
    assert hermes["displayName"] == "glm-4.6"
    assert hermes["note"] == "defaultAlias"

    # [aipdd fork delta] 以前这里还断言 per-env 覆盖层（设 STYLE_ANALYZER_MODEL 就换
    # 卡片）与 envSet 型卡片。env 已退出解析链，两类层级一起删掉：广场只报「槽位绑定 /
    # 自动兜底 / 未绑定」三种事实，显示的就是实际会走的路由。
    assert not hasattr(model_plaza, "_LAYER_PER_ENV")
    assert not hasattr(model_plaza, "_LAYER_FALLBACK")
    assert not hasattr(model_plaza, "_ENV_SLOT_NAMES")
    slot_cards = [
        entry
        for entry in entries.values()
        if entry["connector"] == model_plaza.CONNECTOR_SLOT
    ]
    assert slot_cards
    assert {entry["note"] for entry in slot_cards} <= {
        model_plaza._LAYER_DEFAULT_ALIAS,
        model_plaza._LAYER_AUTO_FALLBACK,
        model_plaza._LAYER_UNBOUND,
    }



def test_plaza_model_slot_hides_foreign_provider_models(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    """[aipdd fork delta] 「模型功能位」分区只保留本 fork 渠道（AIPDD / NewAPI /
    火山引擎 / ComfyUI / MediaKit）：功能位指向外部渠道时不再列进本分区（清理规则）。

    空 provider 的内置别名不受影响：它们没有指向任何外部渠道，且「功能位全量列出」
    是广场的设计前提。
    """
    _seed_full_plaza(monkeypatch, tmp_path)
    # 把通用文本位的绑定改指外部渠道（模拟渠道删除后残留的映射）。
    save_newapi_model_alias_mappings(
        {
            "DC-hermes-LLM": {
                "provider": "google",
                "target": "gemini-3.5-flash",
            },
        },
        merge=True,
    )

    data = model_plaza.build_model_plaza()
    slot_entries = [m for m in data["models"] if m["connector"] == "model-slot"]
    counts = {item["id"]: item["modelCount"] for item in data["connectors"]}

    assert counts["model-slot"] == len(slot_entries)
    # 指向未登记渠道的那一位整条消失，其余照常列出；绝不出现外部渠道卡片。
    # （空 provider = 没登记归属的内置别名，按设计保留，见 _slot_provider_visible。）
    assert all(model_plaza._slot_provider_visible(m["provider"]) for m in slot_entries)
    assert not any(m["provider"] == "google" for m in slot_entries)
    # [aipdd fork delta] env 里写别的名字也换不动路由：设置绑定才是真源。
    monkeypatch.setenv("SCENE_HEADER_AUTOGEN_MODEL", "gemini-3.5-flash")
    after_env = _by_id(model_plaza.build_model_plaza())
    before = _by_id(data)
    for entry_id, entry in after_env.items():
        if entry["connector"] != "model-slot":
            continue
        assert entry["displayName"] == before[entry_id]["displayName"]



def test_plaza_never_leaks_billing_fields_or_raw_secrets(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    _seed_full_plaza(monkeypatch, tmp_path)
    data = model_plaza.build_model_plaza()
    dumped = json.dumps(data, ensure_ascii=False)

    banned_keys = {
        "credit", "credits", "creditCost", "quota", "price", "pricing", "cost",
        "balance", "billing", "billingMetadata", "reserve", "reservation", "settle",
        "amount", "recharge", "payment",
    }

    def walk(node) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                assert str(key).lower() not in banned_keys, f"计费字段回潮: {key}"
                walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(data)
    for banned_text in ("积分", "充值", "credit", "quota", "price"):
        assert banned_text.lower() not in dumped.replace("capability", ""), banned_text

    # 密钥只回掩码预览 + 占位符。
    for secret in (GATEWAY_KEY, "sk-aipdd-secret", "sk-ark-secret", "mk-secret-value"):
        assert secret not in dumped
    assert data["gateway"]["apiKeyPreview"] == model_gateway_settings.mask_secret(GATEWAY_KEY)
    assert model_plaza.API_KEY_PLACEHOLDER in dumped
    assert all("<API_KEY>" in entry["examples"].get("curl", "") or "client_id" in entry["examples"].get("curl", "")
               for entry in data["models"] if entry["examples"].get("curl"))


def test_plaza_public_base_url_comes_from_request_origin_not_host_guess(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    """[aipdd fork delta req41] 对外地址：显式 env > 本请求 origin > 如实生效地址。

    旧实现把容器内主机名硬改写成 ``http://localhost:3000``，等于假定「用户一定在这台
    机器上」，局域网 / 域名反代部署下复制出来的示例地址全部连不通。
    """
    _seed_full_plaza(monkeypatch, tmp_path)
    model_plaza.set_request_origin("")
    data = model_plaza.build_model_plaza()
    # 无请求上下文 + 生效地址是容器内服务名（newapi:3000）：宁可不给，也不拿集群
    # 内部名当用户能打开的入口（更不能编个 localhost）。
    assert data["gateway"]["publicBaseUrl"] == ""
    assert "newapi:3000" not in json.dumps(data)
    assert "localhost:3000" not in json.dumps(data)

    # 有请求上下文：拿本请求自己的 origin + 固定对外出口 /gateway/v1。
    model_plaza.set_request_origin("https://drama.example.com")
    assert model_plaza.newapi_public_base_url() == "https://drama.example.com/gateway/v1"
    # 带端口的局域网部署也一样（origin 里含端口，不被 $host 去端口那种写法坑掉）。
    model_plaza.set_request_origin("http://192.168.1.20:8080")
    assert model_plaza.newapi_public_base_url() == "http://192.168.1.20:8080/gateway/v1"

    # NEWAPI_PUBLIC_BASE_URL 显式覆盖优先（要固定成另一个域名时才用）。
    monkeypatch.setenv("NEWAPI_PUBLIC_BASE_URL", "http://192.168.1.20:3000")
    override = model_plaza.newapi_public_base_url()
    assert override == "http://192.168.1.20:3000/v1"

    # 它只是对外地址，不是模型：设了它不能凭空长出一张「模型名 = URL」的卡片
    # （「模型功能位」卡片数不变，gateway.publicBaseUrl 跟随覆盖值）。
    refreshed = model_plaza.build_model_plaza()
    assert refreshed["gateway"]["publicBaseUrl"] == "http://192.168.1.20:3000/v1"
    slot_cards = [m for m in refreshed["models"] if m["connector"] == "model-slot"]
    assert len(slot_cards) == len(model_slots.FEATURE_SLOTS)
    # env 名现在在 extra.env：指针型 env 不得长出卡片（标题与 env 都不含它）。
    assert all(m.get("slot") != "NEWAPI_PUBLIC_BASE_URL" for m in slot_cards)
    assert all(m["displayName"] != "NEWAPI_PUBLIC_BASE_URL" for m in slot_cards)
    model_plaza.set_request_origin("")


def test_plaza_public_base_url_keeps_effective_host_when_not_internal(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    """生效地址本来就是公网域名 / 本机地址时，无请求上下文也照实给（不改写）."""

    _seed_full_plaza(monkeypatch, tmp_path)
    model_plaza.set_request_origin("")
    save_custom_newapi_gateway(base_url="https://gw.partner.example/v1", api_key=GATEWAY_KEY)
    assert model_plaza.newapi_public_base_url() == "https://gw.partner.example/v1"
    # localhost 是部署方自己在配置里写的本机地址，不属于集群内部服务名。
    save_custom_newapi_gateway(base_url="http://localhost:3000/v1", api_key=GATEWAY_KEY)
    assert model_plaza.newapi_public_base_url() == "http://localhost:3000/v1"


def test_plaza_connector_filter_and_unknown_connector(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    _seed_full_plaza(monkeypatch, tmp_path)
    only = model_plaza.build_model_plaza(connector="ComfyUI")
    assert {entry["connector"] for entry in only["models"]} == {"comfyui"}
    assert [item["id"] for item in only["connectors"]] == ["comfyui"]
    with pytest.raises(ValueError, match="unknown connector"):
        model_plaza.build_model_plaza(connector="nope")


def test_plaza_survives_unconfigured_gateway(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    """什么都没配时目录仍要出四个分区，条目一律 noChannel / unmapped。"""
    monkeypatch.setattr(config, "STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("ST_EDITION", "ce")
    _clear_env(monkeypatch)
    data = model_plaza.build_model_plaza()

    assert [item["id"] for item in data["connectors"]] == list(model_plaza.PLAZA_CONNECTORS)
    assert all(item["availableCount"] == 0 for item in data["connectors"])
    assert data["gateway"]["configured"] is False
    assert data["gateway"]["publicBaseUrl"] == ""
    entries = _by_id(data)
    # 一个渠道都没配：gateway 分区没有可对外教的真名，空是诚实结果
    # （DC-* 内部别名不会替上来充数）；功能位照常全列出、标 unmapped。
    assert [m for m in data["models"] if m["connector"] == "gateway"] == []
    first_env = sorted(model_slots.FEATURE_SLOTS)[0]
    assert entries[f"model-slot:{first_env}"]["status"] == model_plaza.STATUS_UNMAPPED
    assert entries[f"{model_plaza.CONNECTOR_MEDIAKIT}:video-enhance"]["status"] == (
        model_plaza.STATUS_NO_CHANNEL
    )
    assert entries[f"{model_plaza.CONNECTOR_MEDIAKIT}:video-enhance"]["note"] == "noChannel"


def test_plaza_mediakit_backend_forced_to_ffmpeg_is_marked(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    _seed_full_plaza(monkeypatch, tmp_path)
    monkeypatch.setenv("VIDEO_ENHANCE_BACKEND", "ffmpeg")
    entry = _by_id(model_plaza.build_model_plaza())["mediakit:video-enhance"]
    assert entry["status"] == model_plaza.STATUS_NO_CHANNEL
    assert entry["note"] == "backendForcedFfmpeg"


# ---------------------------------------------------------------------------
# 探活红线
# ---------------------------------------------------------------------------


class _ForbiddenClient:
    def __init__(self, *args, **kwargs) -> None:  # noqa: ANN002, ANN003
        raise AssertionError("非 OpenAI 兼容链路不得建连")


class _FakeResponse:
    def __init__(self, status_code: int, text: str = "") -> None:
        self.status_code = status_code
        self.text = text


class _FakeClient:
    captured: dict = {}
    status_code = 200
    text = ""

    def __init__(self, *args, **kwargs) -> None:  # noqa: ANN002, ANN003
        pass

    async def __aenter__(self) -> "_FakeClient":
        return self

    async def __aexit__(self, *exc_info) -> bool:
        return False

    async def post(self, url, *, json, headers):  # noqa: ANN001, ANN002, ANN003
        type(self).captured = {"url": url, "json": json, "headers": headers}
        return _FakeResponse(type(self).status_code, type(self).text)


@pytest.mark.asyncio
@pytest.mark.parametrize("invoke_kind", PROBE_FORBIDDEN_KINDS)
async def test_probe_skips_non_openai_kinds_without_connecting(
    monkeypatch: pytest.MonkeyPatch,
    invoke_kind: str,
) -> None:
    monkeypatch.setattr(model_plaza.httpx, "AsyncClient", _ForbiddenClient)
    result = await model_plaza.probe_plaza_model(
        {"invokeKind": invoke_kind, "baseUrl": "http://localhost:3000/v1", "modelName": "x"}
    )
    assert result["skipped"] is True
    assert result["ok"] is False
    assert result["reason"] == "notProbeableInvokeKind"
    assert result["latencyMs"] == 0


@pytest.mark.asyncio
async def test_probe_chat_uses_single_token_and_gateway_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(model_plaza.httpx, "AsyncClient", _FakeClient)
    monkeypatch.setattr(
        config, "get_newapi_runtime_credentials", lambda **kw: (GATEWAY_KEY, "")
    )
    result = await model_plaza.probe_plaza_model(
        {
            "invokeKind": model_plaza.INVOKE_OPENAI_CHAT,
            "baseUrl": "http://localhost:3000/v1",
            "modelName": "DC-text-LLM",
        }
    )
    assert result["ok"] is True and result["skipped"] is False
    assert result["httpStatus"] == 200
    sent = _FakeClient.captured
    assert sent["url"].endswith("/chat/completions")
    assert sent["json"]["max_tokens"] == 1
    assert sent["json"]["model"] == "DC-text-LLM"
    assert sent["headers"]["Authorization"] == f"Bearer {GATEWAY_KEY}"


@pytest.mark.asyncio
async def test_probe_prefers_server_reachable_base_over_public_example(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """[aipdd fork delta] WP-E: 卡片上的 baseUrl 是 localhost 示例地址，容器内不可达；
    探活必须走服务端自己解析出的生效网关地址。"""
    monkeypatch.setattr(model_plaza.httpx, "AsyncClient", _FakeClient)
    monkeypatch.setattr(
        config,
        "get_newapi_runtime_credentials",
        lambda **kw: (GATEWAY_KEY, "http://newapi:3000/v1"),
    )
    result = await model_plaza.probe_plaza_model(
        {
            "invokeKind": model_plaza.INVOKE_OPENAI_CHAT,
            "baseUrl": "http://localhost:3000/v1",
            "modelName": "DC-text-LLM",
        }
    )
    assert result["ok"] is True
    assert _FakeClient.captured["url"] == "http://newapi:3000/v1/chat/completions"


@pytest.mark.asyncio
async def test_probe_embeddings_and_missing_key_and_http_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(model_plaza.httpx, "AsyncClient", _FakeClient)
    monkeypatch.setattr(
        config, "get_newapi_runtime_credentials", lambda **kw: (GATEWAY_KEY, "")
    )
    embeddings = await model_plaza.probe_plaza_model(
        {
            "invokeKind": model_plaza.INVOKE_OPENAI_EMBEDDINGS,
            "baseUrl": "http://localhost:3000/v1",
            "endpoint": "embeddings",
            "modelName": "DC-cognee-embedding",
        }
    )
    assert embeddings["ok"] is True
    assert _FakeClient.captured["url"].endswith("/embeddings")
    assert _FakeClient.captured["json"] == {
        "model": "DC-cognee-embedding",
        "input": "hi",
    }

    _FakeClient.status_code = 429
    _FakeClient.text = '{"error": {"message": "upstream rate limited"}}'
    failed = await model_plaza.probe_plaza_model(
        {
            "invokeKind": model_plaza.INVOKE_OPENAI_CHAT,
            "baseUrl": "http://localhost:3000/v1",
            "modelName": "DC-text-LLM",
        }
    )
    assert failed["ok"] is False and failed["skipped"] is False
    assert failed["httpStatus"] == 429
    assert "rate limited" in failed["error"]

    monkeypatch.setattr(config, "get_newapi_runtime_credentials", lambda **kw: ("", ""))
    no_key = await model_plaza.probe_plaza_model(
        {
            "invokeKind": model_plaza.INVOKE_OPENAI_CHAT,
            "baseUrl": "http://localhost:3000/v1",
            "modelName": "DC-text-LLM",
        }
    )
    assert no_key["skipped"] is True
    assert no_key["reason"] == "noGatewayKey"


def test_find_plaza_model_roundtrips_entry_id(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    _seed_full_plaza(monkeypatch, tmp_path)
    found = model_plaza.find_plaza_model("comfyui:sd-xl-workflow")
    assert found is not None and found["connector"] == "comfyui"
    assert model_plaza.find_plaza_model("gateway:not-there") is None


# ---------------------------------------------------------------------------
# 路由
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _register_local_auth_port():
    """model-gateway 路由组 router 级强制登录态：注册 CE FileAuthPort（单用户模式
    verify_session 恒放行），不改变鉴权层原有契约。"""
    from novelvideo.ports.local.auth import FileAuthPort
    from novelvideo.ports.registry import register_port

    register_port("auth", FileAuthPort())


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(model_gateway.router)
    return TestClient(app)


def test_model_plaza_route_returns_catalog_and_rejects_unknown_connector(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    _seed_full_plaza(monkeypatch, tmp_path)
    client = _client()

    response = client.get("/model-gateway/model-plaza")
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert [c["id"] for c in body["data"]["connectors"]] == list(model_plaza.PLAZA_CONNECTORS)
    assert "credit" not in json.dumps(body).lower().replace("capability", "")

    filtered = client.get("/model-gateway/model-plaza", params={"connector": "mediakit"})
    assert {m["connector"] for m in filtered.json()["data"]["models"]} == {"mediakit"}

    bad = client.get("/model-gateway/model-plaza", params={"connector": "nope"})
    assert bad.status_code == 400
    assert "unknown connector" in bad.json()["detail"]


def test_model_plaza_test_route_skips_task_backed_kinds_and_404s(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    _seed_full_plaza(monkeypatch, tmp_path)
    calls: list[str] = []

    def forbid_build(*args, **kwargs):  # noqa: ANN002, ANN003
        calls.append("connect")
        raise AssertionError("探活不该为任务制链路建连")

    monkeypatch.setattr(model_plaza.httpx, "AsyncClient", forbid_build)
    client = _client()

    response = client.post(f"/model-gateway/model-plaza/{quote('comfyui:sd-xl-workflow')}/test")
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["modelId"] == "comfyui:sd-xl-workflow"
    assert data["skipped"] is True
    assert data["reason"] == "notProbeableInvokeKind"
    assert calls == []  # 探活压根没建连，也就没产生任何真实任务

    missing = client.post(f"/model-gateway/model-plaza/{quote('gateway:no-such-model')}/test")
    assert missing.status_code == 404
    assert "unknown model" in missing.json()["detail"]


# ---------------------------------------------------------------------------
# req33 §6.3：实测三态标注（只读缓存，这一页依旧零远程）
# ---------------------------------------------------------------------------


def test_plaza_marks_probe_not_granted_card_unusable(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    """后台文本实测判过 not-granted 的真名，广场卡片要当场标出来。

    实测缓存的键是上游真名（glm-4.6），而卡片调用名是渠道复合名
    （aipdd::glm-4.6）——查缓存必须两级回退，否则网关别名永远漏在这一刀之外。
    """
    _seed_full_plaza(monkeypatch, tmp_path)
    model_gateway_settings.save_text_model_probe("aipdd", {"glm-4.6": "not-granted"})
    entries = _by_id(model_plaza.build_model_plaza())

    card = entries["gateway:aipdd::glm-4.6"]
    assert card["modelName"] == "aipdd::glm-4.6"
    assert card["upstreamModel"] == "glm-4.6"
    assert card["probeState"] == "not-granted"
    assert card["probeUnusable"] is True


def test_plaza_vision_card_reads_vision_cache_not_text_cache(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    """同一张卡只按它自己的调用场景取结论：视觉卡看带图实测，不看纯文本实测。

    文本那侧「能聊天」判了 ok，不能推翻视觉那侧「不读图」的 not-vision——
    FREEZONE 视觉下拉里塞个不能读图的名字，正是当初这条要防的事故。
    """
    _seed_full_plaza(monkeypatch, tmp_path)
    model_gateway_settings.save_text_model_probe("ark", {"doubao-vision": "ok"})
    model_gateway_settings.save_vision_model_probe("ark", {"doubao-vision": "not-vision"})
    entries = _by_id(model_plaza.build_model_plaza())

    card = entries["gateway:ark::doubao-vision"]
    assert card["capabilities"] == ["vision", "text"]
    assert card["probeState"] == "not-vision"
    assert card["probeUnusable"] is True


def test_plaza_unprobeable_entries_stay_unprobed(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    """任务制 / embedding 链路没有 chat 实测语义，一律 unprobed 而非「不行」。"""
    _seed_full_plaza(monkeypatch, tmp_path)
    model_gateway_settings.save_text_model_probe("aipdd", {"glm-4.6": "not-granted"})
    data = model_plaza.build_model_plaza()
    entries = _by_id(data)

    for entry in data["models"]:
        if entry["invokeKind"] != model_plaza.INVOKE_OPENAI_CHAT:
            assert entry["probeState"] == "unprobed", entry["id"]
            assert entry["probeUnusable"] is False

    # chat 卡但缓存里没这个名字：落到 unknown（拿不准就保留，绝不误伤）。
    assert entries["gateway:ark::doubao-vision"]["probeState"] == "unknown"
    assert entries["gateway:ark::doubao-vision"]["probeUnusable"] is False


def test_plaza_probe_cache_failure_does_not_break_catalog(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    """探测缓存再坏也伤不到目录：照常出卡，只是谁都不被标成不可用。"""
    _seed_full_plaza(monkeypatch, tmp_path)

    def boom() -> dict:
        raise RuntimeError("state 读不了")

    monkeypatch.setattr(model_plaza, "get_text_model_probe", boom)
    monkeypatch.setattr(model_plaza, "get_vision_model_probe", boom)
    data = model_plaza.build_model_plaza()

    assert data["models"]
    entries = _by_id(data)
    card = entries["gateway:aipdd::glm-4.6"]
    assert card["probeState"] == "unknown"
    assert card["probeUnusable"] is False
    assert all(not entry["probeUnusable"] for entry in data["models"])


# ---------------------------------------------------------------------------
# [aipdd fork delta] The model-slot whitelist must stay in sync with
# _FORK_PROVIDER_LABELS: a newly added channel that is not whitelisted here has
# its feature slots silently hidden by _slot_provider_visible (violates the
# "never silently disappear" rule). Regression guard for req46 / req48 channels.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "provider",
    [
        "minimax",
        "minimax_cn",
        "openai_official",
        "anthropic",
        "gemini",
        # pre-existing fork channels must stay visible too
        "aipdd",
        "newapi",
        "volcengine",
        "doubao",
        "jimeng",
        "comfyui",
        "mediakit",
    ],
)
def test_slot_provider_visible_whitelists_fork_channels(provider: str) -> None:
    assert model_plaza._slot_provider_visible(provider) is True


def test_slot_provider_visible_keeps_empty_and_hides_unknown() -> None:
    # Empty provider = unregistered built-in alias: kept.
    assert model_plaza._slot_provider_visible("") is True
    # An external / removed channel that is not whitelisted is hidden.
    assert model_plaza._slot_provider_visible("some_random_vendor") is False
