"""[aipdd fork delta req48] OpenAI / Claude / Gemini 三条官方渠道的接入契约（全离线）。

钉住四件事，都是「配了 Key 就能用」这条链路上会静默坏掉的地方：

1. 渠道目录层：provider 键位、网关 type、能力位。能力位是设置页媒体模型下拉的
   **唯一过滤器**（req46 教训）：少一位则该渠道在对应 TAB 凭空消失，多一位则在
   TAB 里造一个选不动的行。
2. baseUrl 归一：三家官方端点的版本路径不同（/v1、/v1、/v1beta），而写进网关的
   渠道 base 必须是**裸域**（适配器自己拼版本），两个形态不能互相污染。
3. 清单拉取：三家只差鉴权头与响应外壳，而且 Gemini 原生条目只有 ``name`` 且带
   ``models/`` 前缀 —— 不适配就是一个模型都拉不到（req40：模型名只能来自目录）。
4. 媒体路由：三家都是厂商原生 API，不是 OpenAI 兼容网关，绕过生效网关直连必定
   404，所以 ``resolve_media_provider_override`` 一律不回覆盖。

不打真实上游（本轮不实测），HTTP 全部 respx 打桩。
"""

from __future__ import annotations

import pytest

import respx
from fastapi import FastAPI
from fastapi.testclient import TestClient
from httpx import Response

from novelvideo import config
from novelvideo import model_gateway_settings
from novelvideo.api.routes import model_gateway
from novelvideo.model_gateway_settings import (
    get_newapi_media_model_mappings,  # noqa: F401  (保留可读入口，便于排障时补断言)
    get_upstream_catalog,
    normalize_channel_base_url,
    resolve_media_provider_override,
    save_newapi_media_model_mappings,
    save_newapi_provider_channels,
)
from novelvideo.newapi_provisioner import (
    PROVIDER_PRESETS,
    build_channel_payload,
    catalog_request_headers,
    list_upstream_catalog,
    reset_upstream_models_cache,
)


@pytest.fixture(autouse=True)
def _register_local_auth_port():
    """model-gateway 路由组在 router 级强制登录态；手搭 FastAPI 不走 lifespan 会撞
    503。只注册 CE FileAuthPort（单用户模式恒放行），不改变本文件的行为契约。"""
    from novelvideo.ports.local.auth import FileAuthPort
    from novelvideo.ports.registry import register_port

    register_port("auth", FileAuthPort())


def _isolate_settings_db(monkeypatch: pytest.MonkeyPatch, tmp_path):
    monkeypatch.setattr(config, "STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("ST_EDITION", "ce")
    for key in (
        "ST_CONTROL_PLANE_DSN",
        "MODEL_GATEWAY_MODE",
        "NEWAPI_API_KEY",
        "NEWAPI_BASE_URL",
        "OFFICIAL_NEWAPI_BASE_URL",
        "NEWAPI_ADMIN_BASE_URL",
    ):
        monkeypatch.delenv(key, raising=False)


# ---------------------------------------------------------------------------
# 1. 渠道目录层：键位 / type / 能力位
# ---------------------------------------------------------------------------


def test_vendor_presets_register_official_types_and_base_urls():
    """三家在 PROVIDER_PRESETS 里各有键位，type 用网关的原生适配器。

    openai_official 刻意不叫 ``openai``：那个键被 openai→newapi 的启动改名与
    config.py 的环境变量直连分支占着，压新语义会把用户配好的渠道改姓。
    """
    assert PROVIDER_PRESETS["openai_official"] == {
        "label": "OpenAI",
        "type": 1,
        "base_url": "https://api.openai.com/v1",
    }
    assert PROVIDER_PRESETS["anthropic"]["type"] == 14
    assert PROVIDER_PRESETS["anthropic"]["base_url"] == "https://api.anthropic.com"
    assert PROVIDER_PRESETS["gemini"]["type"] == 24
    assert (
        PROVIDER_PRESETS["gemini"]["base_url"]
        == "https://generativelanguage.googleapis.com"
    )
    # 「provider=openai」的旧语义没有被这次改动扩大：本 fork 的外部网关仍是 newapi。
    assert "newapi" in PROVIDER_PRESETS


def test_vendor_channel_types_route_declares_real_capabilities(
    monkeypatch, tmp_path
):
    """渠道目录行：anthropic / gemini 覆盖镜像行，openai_official 走合成行。

    镜像注册表对这两行只声明文本能力；不覆盖就三家在媒体 TAB 全部选不到（能力位
    是设置页候选过滤的唯一依据）。
    """
    _isolate_settings_db(monkeypatch, tmp_path)
    monkeypatch.setenv("NEWAPI_PROVISIONER_ENABLED", "true")

    class Admin:
        admin_user_id = 1
        admin_username = "root"
        token_created = False
        access_token = "admin-secret"

    from novelvideo.api.routes.model_gateway import channels as channels_routes

    monkeypatch.setattr(
        model_gateway,
        "get_provisioner_config",
        lambda **_kwargs: type("Cfg", (), {"admin_base_url": "http://new-api:3000"})(),
    )
    monkeypatch.setattr(
        model_gateway, "ensure_admin_access_token", lambda _cfg: Admin()
    )
    monkeypatch.setattr(
        channels_routes,
        "list_channel_types",
        lambda _cfg, _admin: [
            {
                "type": 14,
                "provider": "anthropic",
                "name": "Anthropic",
                "description": "",
                "capabilities": ["text"],
            },
            {
                "type": 24,
                "provider": "gemini",
                "name": "Gemini",
                "description": "",
                "capabilities": ["text"],
            },
        ],
    )

    app = FastAPI()
    app.include_router(model_gateway.router)
    client = TestClient(app)

    response = client.get("/model-gateway/custom/newapi/channel-types")
    assert response.status_code == 200
    items = {item["provider"]: item for item in response.json()["data"]["items"]}

    assert items["anthropic"]["type"] == 14
    assert items["anthropic"]["name"] == "Claude"
    assert items["anthropic"]["capabilities"] == ["text", "vision"]
    assert items["anthropic"]["defaultBaseUrl"] == "https://api.anthropic.com/v1"

    assert items["gemini"]["type"] == 24
    assert items["gemini"]["capabilities"] == [
        "text",
        "vision",
        "embedding",
        "image",
        "video",
        "audio",
    ]
    assert (
        items["gemini"]["defaultBaseUrl"]
        == "https://generativelanguage.googleapis.com/v1beta"
    )

    # 镜像没有 openai_official 这一 provider：靠末尾合成行进目录，否则已配置渠道
    # 在「渠道管理」里看不到、状态无法对应。
    assert items["openai_official"]["type"] == 1
    assert items["openai_official"]["name"] == "OpenAI"
    assert items["openai_official"]["status"] == 1
    assert items["openai_official"]["capabilities"] == [
        "text",
        "vision",
        "embedding",
        "image",
        "video",
        "audio",
    ]
    assert items["openai_official"]["defaultBaseUrl"] == "https://api.openai.com/v1"
    # 三家都没接音乐：网关里没有 music 路由，声明了就是造一个选不到的死行（req46
    # 的 MiniMax 是例外，那边有厂商直连适配器）。
    for provider in ("openai_official", "anthropic", "gemini"):
        assert "music" not in items[provider]["capabilities"]


# ---------------------------------------------------------------------------
# 2. baseUrl 归一：自有库存带版本，写网关时剥版本
# ---------------------------------------------------------------------------


def test_vendor_base_url_normalization_is_idempotent():
    """裸域名补齐各渠道自己的版本路径；已补齐过（含 /v1beta）不能二次叠加。"""
    assert (
        normalize_channel_base_url("openai_official", "https://api.openai.com")
        == "https://api.openai.com/v1"
    )
    assert (
        normalize_channel_base_url("anthropic", "https://api.anthropic.com")
        == "https://api.anthropic.com/v1"
    )
    # Gemini 的 REST 根是 /v1beta：_has_versioned_path 认不出 v1beta（不是 v+纯数字），
    # 没有 _CHANNEL_API_PATHS 登记就会被补成 /v1 而打不到端点。
    assert (
        normalize_channel_base_url("gemini", "https://generativelanguage.googleapis.com")
        == "https://generativelanguage.googleapis.com/v1beta"
    )
    # 用户直接粘完整版路径 → 原样保留（重复拼路径是渠道配错的第一大成因）。
    assert (
        normalize_channel_base_url(
            "gemini", "https://generativelanguage.googleapis.com/v1beta/"
        )
        == "https://generativelanguage.googleapis.com/v1beta"
    )
    assert (
        normalize_channel_base_url("anthropic", "https://api.anthropic.com/v1")
        == "https://api.anthropic.com/v1"
    )


def test_vendor_channel_payload_types_and_bare_base_url():
    """写进网关的渠道：type 用原生适配器，base 必须是裸域（适配器自己拼版本）。"""
    payload = build_channel_payload(
        provider="openai_official",
        upstream_key="sk-official",
        model_mapping={"DC-test-LLM": "gpt-4o"},
        base_url="https://api.openai.com/v1",
    )
    assert payload["channel"]["type"] == 1
    assert payload["channel"]["base_url"] == "https://api.openai.com"
    assert payload["channel"]["name"] == "DC-openai_official"

    payload = build_channel_payload(
        provider="anthropic",
        upstream_key="sk-ant",
        model_mapping={"DC-test-LLM": "claude-sonnet-4-5"},
        base_url="https://api.anthropic.com/v1",
    )
    assert payload["channel"]["type"] == 14
    assert payload["channel"]["base_url"] == "https://api.anthropic.com"

    payload = build_channel_payload(
        provider="gemini",
        upstream_key="AIza",
        model_mapping={"DC-test-LLM": "gemini-2.5-pro"},
        base_url="https://generativelanguage.googleapis.com/v1beta",
    )
    assert payload["channel"]["type"] == 24
    # 带 /v1beta 写进去会被适配器再拼一次而得到 …/v1beta/v1beta/…，上游回 404。
    assert (
        payload["channel"]["base_url"] == "https://generativelanguage.googleapis.com"
    )


# ---------------------------------------------------------------------------
# 3. 清单拉取：鉴权头 + 响应外壳
# ---------------------------------------------------------------------------


def test_catalog_request_headers_per_vendor():
    headers = catalog_request_headers("anthropic", "sk-ant-1")
    assert headers["x-api-key"] == "sk-ant-1"
    assert headers["anthropic-version"] == "2023-06-01"
    assert "Authorization" not in headers

    headers = catalog_request_headers("gemini", "AIza-1")
    assert headers["x-goog-api-key"] == "AIza-1"
    assert "Authorization" not in headers

    # OpenAI 官方与其余 OpenAI 兼容渠道同一口径。
    assert catalog_request_headers("openai_official", "sk-1")["Authorization"] == (
        "Bearer sk-1"
    )
    assert catalog_request_headers("newapi", "sk-1")["Authorization"] == "Bearer sk-1"


@respx.mock
def test_anthropic_catalog_listing_uses_vendor_headers_and_keeps_display_name(
    monkeypatch, tmp_path
):
    _isolate_settings_db(monkeypatch, tmp_path)
    reset_upstream_models_cache()
    route = respx.get("https://api.anthropic.com/v1/models").mock(
        return_value=Response(
            200,
            json={
                "data": [
                    {
                        "type": "model",
                        "id": "claude-sonnet-4-5",
                        "display_name": "Claude Sonnet 4.5",
                    },
                    {"type": "model", "id": "claude-opus-4-1", "display_name": "Opus"},
                ],
                "has_more": False,
            },
        )
    )

    entries, error = list_upstream_catalog(
        "anthropic",
        base_url=normalize_channel_base_url("anthropic", "https://api.anthropic.com"),
        upstream_key="sk-ant-secret",
        force=True,
    )

    assert error == ""
    assert route.called
    sent = route.calls.last.request
    assert sent.headers["x-api-key"] == "sk-ant-secret"
    assert sent.headers["anthropic-version"] == "2023-06-01"
    assert "authorization" not in sent.headers

    names = [entry["name"] for entry in entries]
    assert names == ["claude-opus-4-1", "claude-sonnet-4-5"]
    # 展示名不丢：目录里只有裸 id 时，设置页候选就只能给用户看一串机器名。
    assert entries[names.index("claude-sonnet-4-5")]["displayName"] == (
        "Claude Sonnet 4.5"
    )
    # Claude 没有生图 / 视频 / 语音模型：按名字分类只能是文本。
    assert all(entry["mediaType"] == "text" for entry in entries)
    snapshot = get_upstream_catalog("anthropic")
    assert {entry["name"] for entry in snapshot} == set(names)


@respx.mock
def test_gemini_catalog_listing_adapts_native_payload_and_methods(
    monkeypatch, tmp_path
):
    """Gemini 原生条目只有 ``name``（带 models/ 前缀）→ 必须适配成 OpenAI 兼容条目。

    ``supportedGenerationMethods`` 是厂商真的开放了哪些调用方法，当真能力用：
    映射成平台侧要打的 OpenAI 兼容相对路径 + 可确定时的 kind。
    """
    _isolate_settings_db(monkeypatch, tmp_path)
    reset_upstream_models_cache()
    route = respx.get("https://generativelanguage.googleapis.com/v1beta/models").mock(
        return_value=Response(
            200,
            json={
                "models": [
                    {
                        "name": "models/gemini-2.5-pro",
                        "displayName": "Gemini 2.5 Pro",
                        "supportedGenerationMethods": [
                            "generateContent",
                            "streamGenerateContent",
                            "countTokens",
                        ],
                    },
                    {
                        "name": "models/gemini-embedding-001",
                        "supportedGenerationMethods": ["embedContent", "countTextTokens"],
                    },
                    {
                        "name": "models/imagen-4.0-generate-001",
                        "supportedGenerationMethods": ["generateImages"],
                    },
                    {
                        "name": "models/veo-3.0-generate-001",
                        "displayName": "Veo 3.0",
                        "supportedGenerationMethods": ["predictLongRunning"],
                    },
                    {
                        # 只有不可用的方法（RAG 资源管理）：不声明端点，回到名字推断。
                        "name": "models/gemini-1.5-pro-tuned",
                        "supportedGenerationMethods": ["listRagResources"],
                    },
                ]
            },
        )
    )

    entries, error = list_upstream_catalog(
        "gemini",
        base_url=normalize_channel_base_url(
            "gemini", "https://generativelanguage.googleapis.com"
        ),
        upstream_key="AIza-secret",
        force=True,
    )

    assert error == ""
    assert route.called
    sent = route.calls.last.request
    assert sent.headers["x-goog-api-key"] == "AIza-secret"

    by_name = {entry["name"]: entry for entry in entries}
    # models/ 前缀剥掉了：那是 REST 资源路径，不是模型名。
    assert set(by_name) == {
        "gemini-2.5-pro",
        "gemini-embedding-001",
        "imagen-4.0-generate-001",
        "veo-3.0-generate-001",
        "gemini-1.5-pro-tuned",
    }
    pro = by_name["gemini-2.5-pro"]
    assert pro["endpoints"] == ["chat/completions"]
    assert pro["mediaType"] == "text"
    assert pro["displayName"] == "Gemini 2.5 Pro"
    embedding = by_name["gemini-embedding-001"]
    assert embedding["endpoints"] == ["embeddings"]
    assert embedding["mediaType"] == "embedding"
    assert by_name["imagen-4.0-generate-001"]["mediaType"] == "image"
    assert by_name["imagen-4.0-generate-001"]["endpoints"] == ["images/generations"]
    assert by_name["veo-3.0-generate-001"]["mediaType"] == "video"
    # 没有可用方法时不凭空声明端点，分类退到名字推断（gemini → text）。
    tuned = by_name["gemini-1.5-pro-tuned"]
    assert tuned["endpoints"] == []
    assert tuned["inferred"] is True

    # 快照回来不能因为「再解析一次」把声明抹平（幂等是目录的硬要求）。
    snapshot = get_upstream_catalog("gemini")
    assert {entry["name"] for entry in snapshot} == set(by_name)
    assert next(item for item in snapshot if item["name"] == "veo-3.0-generate-001")[
        "mediaType"
    ] == "video"


@respx.mock
def test_openai_catalog_listing_is_plain_openai_compatible(monkeypatch, tmp_path):
    """OpenAI 官方不需要任何特殊外壳：默认 Bearer + ``{data:[{id}]}`` 就命中。"""
    _isolate_settings_db(monkeypatch, tmp_path)
    reset_upstream_models_cache()
    route = respx.get("https://api.openai.com/v1/models").mock(
        return_value=Response(
            200,
            json={
                "object": "list",
                "data": [
                    {"id": "gpt-4o", "name": "gpt-4o-2024-08-06", "owned_by": "openai"},
                    {"id": "gpt-image-1", "owned_by": "openai"},
                    {"id": "sora-2", "owned_by": "openai"},
                    {"id": "whisper-1", "owned_by": "openai"},
                    {"id": "text-embedding-3-large", "owned_by": "openai"},
                    {"id": "omni-moderation-latest", "owned_by": "openai"},
                ],
            },
        )
    )

    entries, error = list_upstream_catalog(
        "openai_official",
        base_url=normalize_channel_base_url("openai_official", "https://api.openai.com"),
        upstream_key="sk-official-secret",
        force=True,
    )

    assert error == ""
    assert route.called
    assert route.calls.last.request.headers["Authorization"] == (
        "Bearer sk-official-secret"
    )
    media_types = {entry["name"]: entry["mediaType"] for entry in entries}
    assert media_types["gpt-4o"] == "text"
    assert media_types["gpt-image-1"] == "image"
    assert media_types["sora-2"] == "video"
    # whisper 系转写过去一个都不命中 audio，只能落 other（目录里还有
    # audio/transcriptions 端点声明，候选面却看不到它）。
    assert media_types["whisper-1"] == "audio"
    assert media_types["text-embedding-3-large"] == "embedding"
    # omni-moderation-* 是内容审核接口，名字里的 omni 会命中视觉资格正则：一律 other。
    assert media_types["omni-moderation-latest"] == "other"


def test_catalog_listing_degrades_without_key(monkeypatch, tmp_path):
    """没配 Key 只回错误文本，不抛异常——渠道保存不能被拉取拖死。"""
    _isolate_settings_db(monkeypatch, tmp_path)
    entries, error = list_upstream_catalog(
        "anthropic",
        base_url="https://api.anthropic.com/v1",
        upstream_key="",
        force=True,
    )
    assert entries == []
    assert "upstreamKey is required" in error


# ---------------------------------------------------------------------------
# 4. 媒体路由：三家一律交回生效网关
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("provider", ["openai_official", "anthropic", "gemini"])
def test_vendor_media_models_never_bypass_gateway(
    monkeypatch, tmp_path, provider
):
    """绑到三家渠道的媒体模型不返回 base 覆盖：协议转换是网关原生适配器的事。

    这些渠道的 base 是**厂商原生 API**，直接 POST {base}/images/generations、
    {base}/video/generations 只会拿到 404（Ark 系渠道早就是同一口径）。
    """
    _isolate_settings_db(monkeypatch, tmp_path)
    save_newapi_provider_channels(
        [
            {
                "provider": provider,
                "upstreamKey": "sk-vendor-secret",
                "baseUrl": normalize_channel_base_url(provider, "https://vendor.test"),
            }
        ]
    )
    save_newapi_media_model_mappings(
        {
            "vendor-image-model": {
                "provider": provider,
                "upstreamModel": "",
                "mediaType": "image",
                "label": "Vendor Image",
                "enabled": True,
                "config": {},
            }
        }
    )

    assert resolve_media_provider_override("vendor-image-model") is None
    # 真源里渠道凭证仍在（拉清单、网关投影都要用），只是媒体调用不绕过网关。
    channel = model_gateway_settings.get_newapi_provider_channel(provider) or {}
    assert channel["upstreamKey"] == "sk-vendor-secret"
    assert get_newapi_media_model_mappings()["vendor-image-model"]["provider"] == provider


# ---------------------------------------------------------------------------
# 5. 分类镜像（前端 model-classification.ts 同口径，钉在同一组用例上）
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("gpt-4o", "text"),
        ("gpt-4o-audio-preview", "audio"),
        ("gpt-image-1", "image"),
        ("sora-2", "video"),
        ("whisper-1", "audio"),
        ("whisper-1-transcribe", "audio"),
        ("text-embedding-3-small", "embedding"),
        ("omni-moderation-latest", "other"),
        ("claude-sonnet-4-5", "text"),
        ("gemini-2.5-flash", "text"),
        ("gemini-2.5-flash-preview-tts", "audio"),
        ("veo-3.0-generate-001", "video"),
        ("imagen-4.0-generate-001", "image"),
        ("lyria-002", "music"),
        ("lyria-realtime-exp", "music"),
    ],
)
def test_vendor_model_name_classification(name, expected):
    from novelvideo.media_model_discovery import guess_model_kind

    assert guess_model_kind(name) == expected


def test_moderation_models_have_no_vision_eligibility():
    from novelvideo.media_model_discovery import is_vision_capable

    assert is_vision_capable("omni-moderation-latest") is False
    # 真视觉模型不受护栏影响。
    assert is_vision_capable("gpt-4o") is False
    assert is_vision_capable("gemini-2.5-flash-image") is False
    assert is_vision_capable("doubao-seed-2.1-turbo") is True
