# [aipdd fork delta] 一键配齐功能位：把「这一部剧用哪几个模型」这一次选择铺开成整链
# 要用的全部绑定。这里钉住的是它最容易走偏的四条边界：
#
# 1. **只写用户点过名的东西**：任何一项留空 = 那一类不动（结果里连行都不留），绝不替
#    用户编一个模型名 —— 编出来的名字在网关那边就是 ``No available channel``（503）。
# 2. **落库前按渠道目录核名**：名字不在目录里（已下架 / 没开通）就地拒绝，回中文成因，
#    而不是写进绑定表再等网关打回。这条就是生产上 ``LingShan-NB-2`` 那类死绑的收口。
# 3. **替用户挑的只填缺口**：视觉位在「系统从目录里挑」时不许覆盖他手动绑过的位；用户
#    显式选的那一类才全量覆盖。
# 4. **向量位只提示不动手**：维度不容代猜，猜错的向量模型会把知识图谱写入直接打坏。
from __future__ import annotations

from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from novelvideo import config
from novelvideo.api.routes import model_gateway
from novelvideo.catalog.upstream_catalog import parse_catalog_response
from novelvideo.feature_provisioner import (
    _TEXT_ALIASES,
    _VISION_ALIASES,
    FeatureProvisionError,
    provision_feature_routes,
    resolve_call_name,
)
from novelvideo.model_gateway_settings import (
    MODE_CUSTOM,
    _default_media_model_from_mappings,
    get_newapi_media_model_mappings,
    get_newapi_model_alias_mappings,
    save_newapi_media_model_mappings,
    save_newapi_embedding_model_config,
    save_newapi_model_alias_mappings,
    save_newapi_provider_channels,
    save_upstream_models_snapshot,
    set_model_gateway_mode,
)
from novelvideo.model_slots import FEATURE_SLOTS
from novelvideo.newapi_provisioner import reset_upstream_models_cache

PROVIDER = "aipdd"
BASE_URL = "https://api.aipdd.example/v1"

# 一份够用的渠道目录：一个能读图的文本位、一个声明只吃文本的模型（名字里没有
# vision 也不算，声明压过关键词）、一个向量位、一个出图位，外加一个已下架的名字。
UPSTREAM_PAYLOAD: dict[str, Any] = {
    "data": [
        {
            "id": "ap-vision-llm",
            "aipdd": {
                "kind": "llm",
                "endpoint": ["/v1/chat/completions"],
                "modalities": {"input": ["text", "image"], "output": ["text"]},
            },
        },
        {
            "id": "ap-text-llm",
            "aipdd": {
                "kind": "llm",
                "endpoint": "/v1/chat/completions",
                "modalities": {"input": ["text"], "output": ["text"]},
            },
        },
        {
            "id": "ap-embed",
            "aipdd": {
                "kind": "embedding",
                "endpoint": "/v1/embeddings",
                "embedding_dimensions": 1536,
            },
        },
        {"id": "ap-image", "aipdd": {"kind": "image", "endpoint": ["/v1/images/generations"]}},
        {"id": "ap-delisted", "aipdd": {"kind": "llm", "available": False}},
    ]
}


@pytest.fixture(autouse=True)
def _ce_local_settings(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    """CE + 本地设置库：目录 / 绑定 / 媒体默认都走自有存储，零远程。"""
    from novelvideo.ports.local.auth import FileAuthPort
    from novelvideo.ports.registry import register_port

    register_port("auth", FileAuthPort())
    monkeypatch.setattr(config, "STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("ST_EDITION", "ce")
    monkeypatch.delenv("ST_CONTROL_PLANE_DSN", raising=False)
    # 功能位的 env 覆盖层优先级最高：用例默认全部清掉，避免开发机 .env 混进断言。
    for key in (*FEATURE_SLOTS.keys(), "TEXT_MODEL_FALLBACK", "VISION_MODEL_FALLBACK"):
        monkeypatch.delenv(key, raising=False)
    for key in (
        "NEWAPI_IMAGE_MODEL",
        "NEWAPI_VIDEO_MODEL",
        "NEWAPI_AUDIO_MODEL",
        "NEWAPI_ASR_MODEL",
        "MODEL_GATEWAY_MODE",
        "NEWAPI_API_KEY",
        "NEWAPI_BASE_URL",
    ):
        monkeypatch.delenv(key, raising=False)
    reset_upstream_models_cache()
    set_model_gateway_mode(MODE_CUSTOM)
    save_newapi_provider_channels(
        [{"provider": PROVIDER, "baseUrl": BASE_URL, "upstreamKey": "sk-secret"}]
    )


def _seed_catalog(provider: str = PROVIDER, names: list[str] | None = None) -> None:
    """把目录灌成结构化条目（走生产同一个写入口，含归一化）。"""
    entries = parse_catalog_response(UPSTREAM_PAYLOAD)
    if names is not None:
        entries = [{"name": name, "kind": "llm"} for name in names]
    save_upstream_models_snapshot(provider, entries, base_url=BASE_URL)


def _seed_media_catalog() -> None:
    save_newapi_media_model_mappings(
        {
            "ap-image": {
                "provider": PROVIDER,
                "upstreamModel": "ap-image",
                "mediaType": "image",
                "sortOrder": 0,
            },
            "seedream-4": {
                "provider": PROVIDER,
                "upstreamModel": "ap-image-alt",
                "mediaType": "image",
                "sortOrder": 7,
            },
            "ap-video-1": {
                "provider": PROVIDER,
                "upstreamModel": "ap-video-1",
                "mediaType": "video",
                "sortOrder": 3,
            },
        }
    )


# --------------------------------------------------------------------------- #
# §1 文本 / 视觉功能位
# --------------------------------------------------------------------------- #


def test_text_selection_binds_every_text_slot_in_one_shot() -> None:
    """一次选择 = 20 个文本别名全指过去（21 个功能位里通用位与场景头位共用一把别名）。"""
    _seed_catalog()
    out = provision_feature_routes(text_model=f"{PROVIDER}::ap-vision-llm")

    assert out["text"]["bound"] == len(_TEXT_ALIASES) == 20
    assert set(out["text"]["slots"]) == set(_TEXT_ALIASES)
    bindings = get_newapi_model_alias_mappings()
    for alias in _TEXT_ALIASES:
        assert bindings[alias]["provider"] == PROVIDER
        assert bindings[alias]["target"] == "ap-vision-llm"
    assert out["touched"] == [PROVIDER]

    # 文本模型自己就能读图：视觉位沿用同一个名字，不必再从目录里替用户挑。
    # 风格分析按带图语义归在 vision 档，所以 vision 是 4 个而不是 3 个。
    assert out["vision"]["source"] == "same-as-text"
    assert out["vision"]["bound"] == len(_VISION_ALIASES) == 4
    assert out["vision"]["model"] == f"{PROVIDER}::ap-vision-llm"


def test_vision_picked_from_catalog_only_fills_gaps(monkeypatch: pytest.MonkeyPatch) -> None:
    """替用户挑的名字不许冲掉他手动绑过的视觉位 —— 那是下一条「我设的模型被改了」。"""
    _seed_catalog()
    save_newapi_model_alias_mappings(
        {
            "DC-freezone-vision-LLM": {
                "provider": PROVIDER,
                "target": "ap-vision-llm",
                "capability": "vision",
            }
        },
        merge=True,
    )

    out = provision_feature_routes(text_model=f"{PROVIDER}::ap-text-llm")
    assert out["vision"]["source"] == "catalog"
    assert out["vision"]["preserved"] == 1
    assert "DC-freezone-vision-LLM" not in out["vision"]["slots"]
    assert out["vision"]["bound"] == len(_VISION_ALIASES) - 1
    assert (
        get_newapi_model_alias_mappings()["DC-freezone-vision-LLM"]["target"]
        == "ap-vision-llm"
    )
    # 其余两位（关键帧提示 / 视频身份检测）确实补上了。
    assert get_newapi_model_alias_mappings()["DC-video-identity-detector-LLM"]["target"]

    # 上游探测明确判死的那个视觉候选要跳过：宁可不配，也不配一个调不通的名字。
    monkeypatch.setattr(
        "novelvideo.feature_provisioner._not_granted_lookup",
        lambda _capability: (lambda provider, name: name == "ap-vision-llm"),
    )
    again = provision_feature_routes(text_model=f"{PROVIDER}::ap-text-llm")
    assert again["vision"]["bound"] == 0
    assert "没有能读图的模型" in again["vision"]["hint"]


def test_explicit_vision_model_overwrites_the_whole_capability() -> None:
    """用户点名了视觉模型 = 这一类全量覆盖，包括他已经绑好的位。"""
    _seed_catalog()
    save_newapi_model_alias_mappings(
        {
            "DC-freezone-vision-LLM": {
                "provider": PROVIDER,
                "target": "ap-vision-llm",
                "capability": "vision",
            }
        },
        merge=True,
    )
    out = provision_feature_routes(
        text_model=f"{PROVIDER}::ap-text-llm",
        vision_model=f"{PROVIDER}::ap-vision-llm",
    )
    assert out["vision"]["source"] == "explicit"
    assert out["vision"]["preserved"] == 0
    assert out["vision"]["bound"] == len(_VISION_ALIASES)
    assert (
        get_newapi_model_alias_mappings()["DC-freezone-vision-LLM"]["target"]
        == "ap-vision-llm"
    )


def test_nothing_picked_touches_nothing() -> None:
    """三个都留空：那一类根本不动，结果里也不留「做过但失败」的假象。"""
    _seed_catalog()
    out = provision_feature_routes()
    assert out["text"] is None
    assert out["vision"] is None
    assert out["image"] is None
    assert out["video"] is None
    assert out["touched"] == []
    assert get_newapi_model_alias_mappings() == {}


# --------------------------------------------------------------------------- #
# §2 核名：拦截点从网关前移到本地
# --------------------------------------------------------------------------- #


def test_delisted_name_is_refused_before_it_reaches_the_gateway() -> None:
    """生产那条 503 的本地版：名字不在渠道目录里就地 400，且不写脏真源。"""
    _seed_catalog()
    with pytest.raises(FeatureProvisionError) as excinfo:
        provision_feature_routes(text_model=f"{PROVIDER}::ap-delisted")
    message = str(excinfo.value)
    assert "ap-delisted" in message
    assert f"不在渠道 {PROVIDER} 的模型清单里" in message
    assert "已从渠道下架或尚未开通" in message
    assert get_newapi_model_alias_mappings() == {}


def test_bare_name_needs_a_single_owner_in_the_catalog() -> None:
    """裸名只有「恰好一个渠道有它」才敢认渠道；同名多渠道必须让用户带渠道选。"""
    _seed_catalog(names=["shared-model"])
    assert resolve_call_name("shared-model", label="文本模型") == (PROVIDER, "shared-model")

    save_upstream_models_snapshot("newapi", [{"name": "shared-model"}], base_url=BASE_URL)
    with pytest.raises(FeatureProvisionError) as excinfo:
        resolve_call_name("shared-model", label="文本模型")
    assert "多个渠道" in str(excinfo.value)


def test_unsynced_catalog_reports_the_channel_instead_of_binding_a_guess() -> None:
    """一次都没同步过清单：没有裁决依据，只能原样认下名字，但也不能编渠道写绑定。"""
    out = provision_feature_routes(text_model="ap-mystery")
    assert out["text"]["bound"] == 0
    assert "同步渠道目录" in out["text"]["hint"]
    assert out["vision"]["hint"]
    assert out["touched"] == []
    assert get_newapi_model_alias_mappings() == {}
    # 视觉位连渠道都认不出，只能保持现状（不写没有 provider 的绑定）。
    assert "保持现状" in out["vision"]["hint"]


# --------------------------------------------------------------------------- #
# §3 媒体默认：只调 sortOrder，不新增条目
# --------------------------------------------------------------------------- #


def test_media_defaults_promote_the_picked_entry_by_sort_order() -> None:
    _seed_media_catalog()
    assert _default_media_model_from_mappings("image") == "ap-image"

    out = provision_feature_routes(image_model="seedream-4", video_model="ap-video-1")
    assert out["image"] == {"model": "seedream-4", "ok": True, "hint": ""}
    assert out["video"]["model"] == "ap-video-1"
    # 默认 = 同类型已启用、sortOrder 最小者，所以这里必须换人。
    assert _default_media_model_from_mappings("image") == "seedream-4"
    assert _default_media_model_from_mappings("video") == "ap-video-1"

    mappings = get_newapi_media_model_mappings()
    assert mappings["seedream-4"]["sortOrder"] == 0
    # 其余同类抬到 1 以上：并列时默认会按名字排序飘走。
    assert mappings["ap-image"]["sortOrder"] >= 1
    # 条目本身归设置页管：配齐不新增、不删除、不动别人的 enabled。
    assert set(mappings) == {"ap-image", "seedream-4", "ap-video-1"}


def test_media_default_finds_the_entry_by_upstream_name() -> None:
    """目录条目 id 与上游真名可以不同：两个写法都要能定位到同一条。"""
    _seed_media_catalog()
    out = provision_feature_routes(image_model="ap-image-alt")
    assert out["image"]["ok"] is True
    assert out["image"]["model"] == "seedream-4"


def test_media_default_reports_when_the_model_is_not_in_the_catalog() -> None:
    _seed_media_catalog()
    before = get_newapi_media_model_mappings()
    out = provision_feature_routes(image_model="ap-never-added")
    assert out["image"]["ok"] is False
    assert "不在「默认生成模型」" in out["image"]["hint"]
    assert get_newapi_media_model_mappings() == before


# --------------------------------------------------------------------------- #
# §4 向量位：只提示，不动手
# --------------------------------------------------------------------------- #


def test_embedding_slot_is_only_reported_never_written() -> None:
    _seed_catalog()
    missing = provision_feature_routes(text_model=f"{PROVIDER}::ap-vision-llm")
    assert any("向量模型" in hint for hint in missing["hints"])
    # 向量位的真源是独立的「向量模型」配置（含维度），不在这张别名表里：
    # 配齐一个字也不许替它写。
    assert "DC-cognee-embedding" not in get_newapi_model_alias_mappings()

    save_newapi_embedding_model_config(
        provider=PROVIDER, upstream_model="ap-embed", dimension=1536
    )
    healthy = provision_feature_routes(text_model=f"{PROVIDER}::ap-vision-llm")
    assert healthy["hints"] == []

    save_newapi_embedding_model_config(
        provider=PROVIDER, upstream_model="ap-gone", dimension=1536
    )
    stale = provision_feature_routes(text_model=f"{PROVIDER}::ap-vision-llm")
    assert any("已从渠道下架" in hint for hint in stale["hints"])


# --------------------------------------------------------------------------- #
# §5 路由：契约与「投影失败不拦运行」
# --------------------------------------------------------------------------- #


def _gateway_client(
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[TestClient, list[dict[str, Any]]]:
    monkeypatch.setenv("NEWAPI_PROVISIONER_ENABLED", "true")
    payloads: list[dict[str, Any]] = []

    def fake_upsert_channel(_cfg, _admin, payload):
        payloads.append(payload)
        return {"ok": True, "httpStatus": 200, "newApiResponse": {"success": True}}

    monkeypatch.setattr(model_gateway, "upsert_channel", fake_upsert_channel)
    monkeypatch.setattr(
        model_gateway,
        "get_provisioner_config",
        lambda _base_url=None, **_kwargs: type("Cfg", (), {"admin_base_url": "http://newapi:3000"})(),
    )
    monkeypatch.setattr(
        model_gateway, "ensure_admin_access_token", lambda _cfg: object()
    )
    app = FastAPI()
    app.include_router(model_gateway.router)
    return TestClient(app), payloads


def test_provision_route_writes_true_source_and_projects(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _seed_catalog()
    _seed_media_catalog()
    client, payloads = _gateway_client(monkeypatch)
    response = client.post(
        "/model-gateway/custom/newapi/feature-routes/provision",
        json={
            "textModel": f"{PROVIDER}::ap-vision-llm",
            "imageModel": "seedream-4",
        },
    )
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["text"]["bound"] == len(_TEXT_ALIASES)
    assert data["image"]["ok"] is True
    assert get_newapi_model_alias_mappings()[_TEXT_ALIASES[0]]["target"] == "ap-vision-llm"
    assert _default_media_model_from_mappings("image") == "seedream-4"
    # 真源写完立即投影到受影响的渠道（不投影就是一个「设好了但网关不认」的假完成）。
    assert len(payloads) == 1
    assert data["projection"][0]["provider"] == PROVIDER
    assert data["projection"][0]["ok"] is True
    assert data["projection"][0]["models"] > 0
    assert data["projectionFailed"] == []
    assert data["projectionError"] == ""


def test_provision_route_rejects_a_dead_name_with_chinese_detail(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _seed_catalog()
    client, payloads = _gateway_client(monkeypatch)
    response = client.post(
        "/model-gateway/custom/newapi/feature-routes/provision",
        json={"textModel": f"{PROVIDER}::ap-delisted"},
    )
    assert response.status_code == 400
    assert "已从渠道下架或尚未开通" in response.json()["detail"]
    assert get_newapi_model_alias_mappings() == {}
    assert payloads == []


def test_provision_route_treats_projection_as_best_effort(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """网关那一侧同步失败不许把配齐报成失败：真源已落对，拦一次运行只会多一条报错。"""
    _seed_catalog()
    client, _payloads = _gateway_client(monkeypatch)
    monkeypatch.setattr(
        model_gateway,
        "ensure_admin_access_token",
        lambda _cfg: (_ for _ in ()).throw(RuntimeError("admin token 获取失败")),
    )
    response = client.post(
        "/model-gateway/custom/newapi/feature-routes/provision",
        json={"textModel": f"{PROVIDER}::ap-vision-llm"},
    )
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["text"]["bound"] == len(_TEXT_ALIASES)
    assert data["projection"] == []
    assert "admin token" in data["projectionError"]
    assert get_newapi_model_alias_mappings() != {}
