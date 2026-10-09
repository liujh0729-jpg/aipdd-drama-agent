from __future__ import annotations

import asyncio
import hashlib
import json
import os
from pathlib import Path

import pytest


import respx
from fastapi import FastAPI
from fastapi.testclient import TestClient
from httpx import Response

from novelvideo import config
from novelvideo import model_gateway_settings
from novelvideo.catalog import official_media_catalog_remote
from novelvideo.api.routes import freezone as freezone_routes
from novelvideo.api.routes import model_gateway
from novelvideo.model_gateway_settings import (
    EffectiveMediaRelayConfig,
    EffectiveNewApiConfig,
    MODE_CUSTOM,
    MODE_HYBRID,
    MODE_OFFICIAL,
    build_newapi_database_status,
    build_model_gateway_status,
    build_mediakit_status,
    get_effective_cognee_embedding_config,
    get_effective_media_relay_config,
    get_effective_newapi_config,
    get_ce_media_model_catalog,
    get_newapi_embedding_model_config,
    get_official_media_model_catalog,
    get_newapi_media_model_mappings,
    get_newapi_provider_channels,
    get_upstream_catalog,
    normalize_relay_base_url,
    save_official_newapi_key,
    save_custom_newapi_gateway,
    save_newapi_embedding_model_config,
    save_newapi_media_model_mappings,
    save_media_relay_config,
    save_mediakit_config,
    save_newapi_database_config,
    save_newapi_provider_channels,
    save_upstream_models_snapshot,
    get_upstream_models_snapshot,
    set_model_gateway_mode,
)
from novelvideo.model_gateway_runtime import refresh_model_gateway_runtime
from novelvideo.generators.video_generator import (
    NewApiVideoGenerator,
    newapi_video_backend_options,
)
from novelvideo.newapi_provisioner import (
    _merge_channel_payload,
    AdminToken,
    PROVIDER_PRESETS,
    build_channel_payload,
    ensure_newapi_setup,
    get_provisioner_config,
    list_channel_types,
    list_upstream_catalog,
    NewApiSetupCredentials,
    NewApiProvisionerConfig,
    normalize_admin_base_url,
    open_newapi_db,
    require_provisioner_enabled,
    reset_upstream_models_cache,
    update_provider_channel_credentials,
    upsert_channel,
)


@pytest.mark.parametrize("failures", [1, 3])
def test_settings_initialization_retries_lock_and_closes_connections(
    monkeypatch, tmp_path, failures
):
    import sqlite3
    from novelvideo import model_gateway_settings as settings

    monkeypatch.setattr(settings, "_settings_db_path", lambda: tmp_path / "settings.db")
    configure = settings.configure_sqlite_connection
    connections = []

    def contend_on_journal_mode(conn):
        connections.append(conn)
        if len(connections) <= failures:
            raise sqlite3.OperationalError("database is locked")
        configure(conn)

    monkeypatch.setattr(settings, "configure_sqlite_connection", contend_on_journal_mode)
    if failures == 1:
        assert settings._read_all() == {}
        assert len(connections) == 2
    else:
        with pytest.raises(sqlite3.OperationalError, match="database is locked"):
            settings._read_all()
        assert len(connections) == 3
    for conn in connections:
        with pytest.raises(sqlite3.ProgrammingError, match="closed"):
            conn.execute("SELECT 1")


def _newer_bundled_catalog_version(offset: int = 1) -> str:
    bundled = json.loads(
        Path(model_gateway_settings.__file__)
        .with_name("official_media_models.json")
        .read_text(encoding="utf-8")
    )
    parts = str(bundled["catalogVersion"]).split(".")
    parts[-1] = str(int(parts[-1]) + offset)
    return ".".join(parts)


def test_bundled_catalog_includes_seedance_25_capabilities():
    bundled = json.loads(
        Path(model_gateway_settings.__file__)
        .with_name("official_media_models.json")
        .read_text(encoding="utf-8")
    )

    model = bundled["mediaModels"]["seedance-2.5"]
    assert model["upstreamModel"] == "seedance-2.5"
    assert model["mediaType"] == "video"
    assert model["config"]["supportedModes"] == [
        "text_to_video",
        "image_reference",
        "all_reference",
        "video_edit",
        "first_last_frame",
    ]
    assert model["config"]["referenceImageMax"] == 30
    assert model["config"]["referenceVideoMax"] == 10
    assert model["config"]["referenceAudioMax"] == 10
    assert model["config"]["maxDuration"] == 30
    assert model["config"]["referenceVideoTotalMaxSeconds"] == 30
    assert model["config"]["referenceAudioTotalMaxSeconds"] == 30


def test_generic_comfyui_i2v_defaults_to_widescreen():
    config = model_gateway._default_comfyui_media_model_config("wan-i2v")

    assert config["ratioOptions"][0] == "16:9"


def test_comfyui_channel_update_replaces_removed_workflow_models():
    existing = {
        "id": 9,
        "type": 63,
        "model_mapping": json.dumps({"old-model": "old-model", "kept": "kept"}),
        "models": "old-model,kept",
        "base_url": "http://127.0.0.1:8188",
    }
    incoming = build_channel_payload(
        provider="comfyui",
        channel_type=63,
        upstream_key="",
        model_mapping={"kept": "kept"},
        base_url="http://127.0.0.1:8188",
        other_settings={"comfyui": {"workflow_by_model": {"kept": {"1": {}}}}},
    )

    merged = _merge_channel_payload(existing, incoming)["channel"]

    assert merged["models"] == "kept"
    assert json.loads(merged["model_mapping"]) == {"kept": "kept"}


@respx.mock
def test_list_channel_types_normalizes_newapi_metadata():
    cfg = NewApiProvisionerConfig(
        admin_base_url="http://new-api:3000",
        sql_dsn="local",
        sqlite_path="/tmp/one-api.db",
        admin_username="root",
        init_timeout_ms=1000,
        relay_token_name="dramaclaw-ce-runtime",
    )
    admin = AdminToken(
        admin_user_id=1,
        admin_username="root",
        access_token="admin-secret",
        token_created=False,
    )
    route = respx.get(
        "http://new-api:3000/api/channel/types",
        params={"status": 1},
    ).mock(
        return_value=Response(
            200,
            json={
                "success": True,
                "data": {
                    "items": [
                        {
                            "type": 62,
                            "provider": "TokenHub",
                            "name": "TokenHub",
                            "description": "Telecom gateway",
                            "icon": "TokenHub",
                            "default_base_url": "https://aigw.telecomjs.com",
                            "status": 1,
                            "capabilities": ["text", "video"],
                            "requires_base_url": False,
                            "supports_base_url_override": True,
                        }
                    ]
                },
            },
        )
    )

    assert list_channel_types(cfg, admin) == [
        {
            "type": 62,
            "provider": "tokenhub",
            "name": "TokenHub",
            "description": "Telecom gateway",
            "icon": "TokenHub",
            "defaultBaseUrl": "https://aigw.telecomjs.com",
            "status": 1,
            "capabilities": ["text", "video"],
            "requiresBaseUrl": False,
            "supportsBaseUrlOverride": True,
        }
    ]
    assert route.called


@pytest.fixture(autouse=True)
def _register_local_auth_port():
    """[aipdd fork delta] model-gateway 路由组现在 router 级强制登录态堵匿名
    旁路；本文件的测试手搭 FastAPI 不走 startup lifespan，会撞 pinned 503。
    这里只注册 CE FileAuthPort（单用户模式 verify_session 恒放行），
    让鉴权层不改变本文件原有行为契约。"""
    from novelvideo.ports.local.auth import FileAuthPort
    from novelvideo.ports.registry import register_port

    register_port("auth", FileAuthPort())


def _isolate_settings_db(monkeypatch: pytest.MonkeyPatch, tmp_path):
    monkeypatch.setattr(config, "STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("ST_EDITION", "ce")
    for key in (
        "ST_CONTROL_PLANE_DSN",
        "MODEL_GATEWAY_MODE",
        "MODEL_GATEWAY_RUNTIME_VERSION",
        "NEWAPI_API_KEY",
        "NEWAPI_BASE_URL",
        # [aipdd fork delta req41] 这两个现在分别是官方模式地址与管理面地址的唯一 env
        # 来源：不留宿主 .env / 开发机环境把预设值泄进用例。
        "OFFICIAL_NEWAPI_BASE_URL",
        "NEWAPI_ADMIN_BASE_URL",
    ):
        monkeypatch.delenv(key, raising=False)


def test_effective_newapi_config_uses_request_scoped_explicit_config_without_writes(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    _isolate_settings_db(monkeypatch, tmp_path)
    explicit = EffectiveNewApiConfig(
        mode=MODE_OFFICIAL,
        source="request",
        base_url="https://request.example/v1",
        api_key="sk-request-only",
    )
    environment_before = dict(os.environ)
    monkeypatch.setattr(
        model_gateway_settings,
        "_read_all",
        lambda: pytest.fail("explicit config must not read SQLite"),
    )

    result = get_effective_newapi_config(explicit_config=explicit)

    assert result is explicit
    assert dict(os.environ) == environment_before
    assert not (tmp_path / "state").exists()


def test_effective_media_relay_config_uses_request_scoped_explicit_config_without_writes(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    _isolate_settings_db(monkeypatch, tmp_path)
    explicit = EffectiveMediaRelayConfig(
        source="request",
        provider="aliyun_oss",
        ttl_seconds=60,
        endpoint="https://relay.example",
        bucket="tenant-bucket",
        access_key_id="request-ak",
        access_key_secret="request-sk",
    )
    environment_before = dict(os.environ)
    monkeypatch.setattr(
        model_gateway_settings,
        "_read_all",
        lambda: pytest.fail("explicit config must not read SQLite"),
    )

    result = get_effective_media_relay_config(explicit_config=explicit)

    assert result is explicit
    assert dict(os.environ) == environment_before
    assert not (tmp_path / "state").exists()


@pytest.mark.parametrize(
    ("resolver", "explicit_config"),
    [
        (get_effective_newapi_config, {"api_key": "forged"}),
        (get_effective_media_relay_config, {"access_key_secret": "forged"}),
    ],
)
def test_effective_config_rejects_untyped_explicit_override(
    resolver,
    explicit_config,
) -> None:
    with pytest.raises(TypeError, match="explicit_config"):
        resolver(explicit_config=explicit_config)


def test_comfyui_provider_channel_defaults_to_channel_type_63(monkeypatch, tmp_path):
    _isolate_settings_db(monkeypatch, tmp_path)

    saved = save_newapi_provider_channels(
        [
            {
                "provider": "comfyui",
                "baseUrl": "http://host.docker.internal:8188",
                "settings": {
                    "comfyui": {
                        "workflow_by_model": {
                            "h3-t2v": {
                                "1": {
                                    "class_type": "SaveVideo",
                                    "inputs": {},
                                }
                            }
                        }
                    }
                },
            }
        ]
    )

    assert saved[0]["type"] == 63


@pytest.mark.asyncio
async def test_custom_catalog_disabled_mapping_removes_official_model(
    monkeypatch, tmp_path
):
    _isolate_settings_db(monkeypatch, tmp_path)
    save_newapi_media_model_mappings(
        {
            "LingShan-G2": {
                "provider": "openrouter",
                "mediaType": "image",
                "enabled": False,
            }
        }
    )
    set_model_gateway_mode(MODE_CUSTOM)

    catalog = await freezone_routes._ee_media_model_catalog("image")

    assert catalog is not None
    assert all(item.get("id") != "LingShan-G2" for item in catalog)


def test_provider_channel_partial_save_preserves_unmentioned_channels(
    monkeypatch, tmp_path
):
    _isolate_settings_db(monkeypatch, tmp_path)
    save_newapi_provider_channels(
        [
            {"provider": "openrouter", "upstreamKey": "sk-openrouter-old"},
            {"provider": "volcengine", "upstreamKey": "sk-volcengine-old"},
        ]
    )

    saved = save_newapi_provider_channels(
        [{"provider": "volcengine", "upstreamKey": "sk-volcengine-new"}],
        preserve_unmentioned=True,
    )

    by_provider = {channel["provider"]: channel for channel in saved}
    assert by_provider["openrouter"]["upstreamKey"] == "sk-openrouter-old"
    assert by_provider["volcengine"]["upstreamKey"] == "sk-volcengine-new"


def test_provider_channel_priority_can_be_reset_to_zero(monkeypatch, tmp_path):
    _isolate_settings_db(monkeypatch, tmp_path)
    save_newapi_provider_channels(
        [
            {
                "provider": "openrouter",
                "upstreamKey": "sk-openrouter",
                "priority": 100,
            }
        ]
    )

    saved = save_newapi_provider_channels(
        [{"provider": "openrouter", "upstreamKey": "", "priority": 0}]
    )
    payload = model_gateway._build_channel_payload_from_spec(
        model_gateway.ChannelSpec(
            provider="openrouter",
            modelMapping={"DC-test-LLM": "openai/test"},
            priority=0,
        )
    )

    assert saved[0]["priority"] == 0
    assert payload["channel"]["priority"] == 0


def test_provider_channel_base_url_normalized_on_save_and_read(
    monkeypatch, tmp_path
):
    # [aipdd fork delta] req08：渠道 base 约定带版本后缀（媒体直连 {base}/video/generations、
    # 能力发现 {base}/models 要用）；裸域名保存 / 读取期都补齐 /v1，Ark 系补 /api/v3，
    # 已带版本后缀的不变，comfyui 的 base 不适用此规则。
    _isolate_settings_db(monkeypatch, tmp_path)
    saved = save_newapi_provider_channels(
        [
            {
                "provider": "newapi",
                "upstreamKey": "sk-newapi",
                "baseUrl": "https://relay.example.com",
            },
            {
                "provider": "aipdd",
                "upstreamKey": "sk-aipdd",
                "baseUrl": "https://api.aipdd.work/v1",
            },
            {
                "provider": "volcengine",
                "upstreamKey": "sk-volc",
                "baseUrl": "https://ark.example.com",
            },
            {
                "provider": "comfyui",
                "baseUrl": "http://host.docker.internal:8188",
                "settings": {
                    "comfyui": {
                        "workflow_by_model": {
                            "h3-t2v": {
                                "1": {"class_type": "SaveVideo", "inputs": {}}
                            }
                        }
                    }
                },
            },
        ]
    )

    by_provider = {channel["provider"]: channel for channel in saved}
    assert by_provider["newapi"]["baseUrl"] == "https://relay.example.com/v1"
    assert by_provider["aipdd"]["baseUrl"] == "https://api.aipdd.work/v1"
    assert by_provider["volcengine"]["baseUrl"] == "https://ark.example.com/api/v3"
    assert by_provider["comfyui"]["baseUrl"] == "http://host.docker.internal:8188"
    read_back = {
        channel["provider"]: channel for channel in get_newapi_provider_channels()
    }
    assert read_back["newapi"]["baseUrl"] == "https://relay.example.com/v1"


def test_provider_channel_read_normalizes_legacy_bare_base_url(
    monkeypatch, tmp_path
):
    # 存量库里的裸域名（修复前手填）在读取出口即归一：媒体直连不会再打到目标站
    # 的前端 SPA 兜底（HTTP 200 HTML → submit returned invalid JSON）。
    _isolate_settings_db(monkeypatch, tmp_path)
    model_gateway_settings._write_many(
        {
            "custom_newapi_provider_channels": json.dumps(
                [
                    {
                        "provider": "newapi",
                        "type": 1,
                        "upstreamKey": "sk-legacy",
                        "baseUrl": "https://susciyuan.com",
                        "priority": 0,
                        "settings": {},
                    }
                ],
                ensure_ascii=False,
                separators=(",", ":"),
            )
        }
    )

    channels = get_newapi_provider_channels()
    assert channels[0]["baseUrl"] == "https://susciyuan.com/v1"


def test_model_gateway_uses_explicit_custom_mode(monkeypatch, tmp_path):
    _isolate_settings_db(monkeypatch, tmp_path)

    save_custom_newapi_gateway(
        base_url="http://127.0.0.1:3000",
        api_key="sk-custom-secret",
        admin_base_url="http://127.0.0.1:3000",
        token_name="dramaclaw-ce-runtime",
        token_id=3,
        activate=True,
    )

    effective = get_effective_newapi_config(
        official_base_url="https://official.example/v1",
        official_api_key="sk-official-secret",
    )
    assert effective.mode == MODE_CUSTOM
    assert effective.base_url == "http://127.0.0.1:3000/v1"
    assert effective.api_key == "sk-custom-secret"


def test_hybrid_mode_uses_official_gateway_by_default(monkeypatch, tmp_path):
    _isolate_settings_db(monkeypatch, tmp_path)
    save_custom_newapi_gateway(
        base_url="http://127.0.0.1:3000",
        api_key="sk-custom-secret",
        activate=False,
    )
    save_official_newapi_key(api_key="sk-official-secret", activate=False)
    set_model_gateway_mode(MODE_HYBRID)

    effective = get_effective_newapi_config()

    assert effective.mode == MODE_HYBRID
    assert effective.source == "hybrid"
    assert effective.api_key == "sk-official-secret"


def test_hybrid_video_routes_only_comfyui_models_to_local_gateway(
    monkeypatch, tmp_path
):
    _isolate_settings_db(monkeypatch, tmp_path)
    save_custom_newapi_gateway(
        base_url="http://127.0.0.1:3000",
        api_key="sk-custom-secret",
        activate=False,
    )
    save_official_newapi_key(api_key="sk-official-secret", activate=False)
    save_newapi_media_model_mappings(
        {
            "wan-i2v": {"provider": "comfyui", "upstreamModel": ""},
            "seedance-2.0": {"provider": "volcengine", "upstreamModel": ""},
        }
    )
    set_model_gateway_mode(MODE_HYBRID)

    local = NewApiVideoGenerator(model="wan-i2v")
    official = NewApiVideoGenerator(model="seedance-2.0")

    assert local.base_url == "http://127.0.0.1:3000/v1"
    assert local.api_key == "sk-custom-secret"
    assert official.api_key == "sk-official-secret"
    assert newapi_video_backend_options()["newapi_wan-i2v"] == "wan-i2v"


def test_newapi_video_backends_only_include_enabled_comfyui_video_models(
    monkeypatch, tmp_path
):
    _isolate_settings_db(monkeypatch, tmp_path)
    save_newapi_media_model_mappings(
        {
            "legacy-video": {"provider": "comfyui"},
            "enabled-video": {
                "provider": "comfyui",
                "mediaType": "video",
                "enabled": True,
            },
            "disabled-video": {
                "provider": "comfyui",
                "mediaType": "video",
                "enabled": False,
            },
            "comfy-image": {
                "provider": "comfyui",
                "mediaType": "image",
                "enabled": True,
            },
        }
    )

    options = newapi_video_backend_options()

    assert "newapi_legacy-video" in options
    assert "newapi_enabled-video" in options
    assert "newapi_disabled-video" not in options
    assert "newapi_comfy-image" not in options


def test_newapi_runtime_credentials_prefer_saved_custom_gateway(monkeypatch, tmp_path):
    _isolate_settings_db(monkeypatch, tmp_path)
    monkeypatch.setenv("NEWAPI_API_KEY", "sk-env-secret")
    monkeypatch.setenv("NEWAPI_BASE_URL", "https://env.example/v1")

    save_custom_newapi_gateway(
        base_url="http://127.0.0.1:3000",
        api_key="sk-custom-secret",
        admin_base_url="http://127.0.0.1:3000",
        token_name="dramaclaw-ce-runtime",
        token_id=3,
        activate=True,
    )

    api_key, base_url = config.get_newapi_runtime_credentials()

    assert api_key == "sk-custom-secret"
    assert base_url == "http://127.0.0.1:3000/v1"


def test_newapi_runtime_credentials_allow_explicit_override(monkeypatch, tmp_path):
    _isolate_settings_db(monkeypatch, tmp_path)
    save_custom_newapi_gateway(
        base_url="http://127.0.0.1:3000",
        api_key="sk-custom-secret",
        activate=True,
    )

    api_key, base_url = config.get_newapi_runtime_credentials(
        api_key_override="sk-request-secret",
        base_url_override="https://request.example/v1",
    )

    assert api_key == "sk-request-secret"
    assert base_url == "https://request.example/v1"


def test_newapi_text_model_defaults_to_300_second_timeout(monkeypatch, tmp_path):
    _isolate_settings_db(monkeypatch, tmp_path)
    monkeypatch.delenv("NEWAPI_TEXT_TIMEOUT_SECONDS", raising=False)
    monkeypatch.delenv("DC_TEST_MODEL_TIMEOUT_SECONDS", raising=False)
    save_custom_newapi_gateway(
        base_url="http://127.0.0.1:3000",
        api_key="sk-custom-secret",
        activate=True,
    )
    captured: dict[str, object] = {}

    def fake_model(model_name, **kwargs):
        captured.update(model_name=model_name, **kwargs)
        return "newapi-model"

    monkeypatch.setattr(config, "_newapi_text_openai_model", fake_model)

    result = config.get_newapi_text_pydantic_model(
        "DC_TEST_MODEL",
        "DC-test-LLM",
    )

    assert result == "newapi-model"
    assert captured["timeout_seconds"] == 300.0


def test_legacy_pydantic_factory_uses_ce_gateway_settings(monkeypatch, tmp_path):
    _isolate_settings_db(monkeypatch, tmp_path)
    monkeypatch.setenv("MODEL_API_KEY", "sk-stale-env-secret")
    monkeypatch.setenv("MODEL_BASE_URL", "https://stale-env.example/v1")
    save_custom_newapi_gateway(
        base_url="http://new-api:3000",
        api_key="sk-database-secret",
        activate=True,
    )
    captured: dict[str, object] = {}

    def fake_model(model_name, **kwargs):
        captured.update(model_name=model_name, **kwargs)
        return "newapi-model"

    monkeypatch.setattr(config, "_newapi_text_openai_model", fake_model)

    result = config.get_pydantic_model(
        provider_override="openrouter",
        model_name_override="openrouter/DC-legacy-agent-LLM",
    )

    assert result == "newapi-model"
    assert captured["model_name"] == "DC-legacy-agent-LLM"
    assert captured["api_key"] == "sk-database-secret"
    assert captured["base_url"] == "http://new-api:3000/v1"
    assert captured["timeout_seconds"] == 300.0


def test_legacy_pydantic_factory_uses_ee_deployment_gateway(monkeypatch, tmp_path):
    _isolate_settings_db(monkeypatch, tmp_path)
    monkeypatch.setenv("ST_EDITION", "ee")
    monkeypatch.setenv("ST_CONTROL_PLANE_DSN", "postgresql://control-plane")
    monkeypatch.setenv("NEWAPI_API_KEY", "sk-ee-secret")
    monkeypatch.setenv("NEWAPI_BASE_URL", "https://ee-gateway.example/v1")
    monkeypatch.setattr(config, "NEWAPI_API_KEY", "sk-ee-secret")
    monkeypatch.setattr(config, "NEWAPI_BASE_URL", "https://ee-gateway.example/v1")
    captured: dict[str, object] = {}

    class FakeModel:
        async def request(self, *_args):
            return "newapi-response"

    def fake_model(model_name, **kwargs):
        captured.update(model_name=model_name, **kwargs)
        return FakeModel()

    monkeypatch.setattr(config, "_newapi_text_openai_model", fake_model)

    model = config.get_pydantic_model(model_name_override="DC-legacy-agent-LLM")

    assert captured == {}
    result = asyncio.run(model.request([], None, object()))

    assert result == "newapi-response"
    assert captured["model_name"] == "DC-legacy-agent-LLM"
    assert captured["api_key"] == "sk-ee-secret"
    assert captured["base_url"] == "https://ee-gateway.example/v1"


def test_ee_media_model_mappings_do_not_open_ce_settings(monkeypatch, tmp_path):
    _isolate_settings_db(monkeypatch, tmp_path)
    monkeypatch.setenv("ST_EDITION", "ee")
    monkeypatch.setenv("ST_CONTROL_PLANE_DSN", "postgresql://control-plane")
    monkeypatch.setattr(
        model_gateway_settings,
        "_connect",
        lambda: pytest.fail("EE must not open CE settings.db"),
    )

    assert get_newapi_media_model_mappings() == {}
    assert not (tmp_path / "state").exists()


def test_ee_platform_video_paths_do_not_call_ce_media_model_accessor(
    monkeypatch,
    tmp_path,
):
    _isolate_settings_db(monkeypatch, tmp_path)
    monkeypatch.setenv("ST_EDITION", "ee")
    monkeypatch.setenv("ST_CONTROL_PLANE_DSN", "postgresql://control-plane")
    monkeypatch.setenv("NEWAPI_API_KEY", "sk-ee-secret")
    monkeypatch.setenv("NEWAPI_BASE_URL", "https://ee-gateway.example/v1")
    monkeypatch.setattr(config, "NEWAPI_API_KEY", "sk-ee-secret")
    monkeypatch.setattr(config, "NEWAPI_BASE_URL", "https://ee-gateway.example/v1")
    monkeypatch.setattr(
        model_gateway_settings,
        "get_newapi_media_model_mappings",
        lambda: pytest.fail("EE video paths must not call the CE accessor"),
    )

    generator = NewApiVideoGenerator(model="seedance-2.0")
    options = newapi_video_backend_options()

    assert generator.api_key == "sk-ee-secret"
    assert generator.base_url == "https://ee-gateway.example/v1"
    assert "newapi_seedance-2.0" in options


def test_ee_model_gateway_settings_reader_does_not_open_sqlite(monkeypatch, tmp_path):
    _isolate_settings_db(monkeypatch, tmp_path)
    monkeypatch.setenv("ST_EDITION", "ee")
    monkeypatch.setenv("ST_CONTROL_PLANE_DSN", "postgresql://control-plane")
    monkeypatch.setattr(
        model_gateway_settings,
        "_connect",
        lambda: pytest.fail("EE must not open CE settings.db"),
    )

    # [aipdd fork delta] 本 fork 默认走 LocalNewAPI（不再默认官方中转），EE 依旧不碰
    # CE 的 settings.db——上面把 _connect 换成 fail 就是在守这条边界。
    assert model_gateway_settings.get_model_gateway_settings() == {
        "model_gateway_mode": MODE_CUSTOM
    }
    assert not (tmp_path / "state").exists()


def test_cognee_newapi_resolution_prefers_saved_gateway(monkeypatch, tmp_path):
    _isolate_settings_db(monkeypatch, tmp_path)
    monkeypatch.delenv("COGNEE_LLM_PROVIDER", raising=False)
    monkeypatch.delenv("COGNEE_LLM_MODEL", raising=False)
    monkeypatch.delenv("NEWAPI_BASE_URL", raising=False)
    monkeypatch.setenv("NEWAPI_API_KEY", "sk-env-secret")

    save_custom_newapi_gateway(
        base_url="https://custom.example",
        api_key="sk-custom-secret",
        activate=True,
    )

    from novelvideo.cognee import config as cognee_config

    assert cognee_config._resolve_llm_provider() == "newapi"
    assert (
        cognee_config._resolve_llm_api_key("newapi", "openai/DC-model")
        == "sk-custom-secret"
    )
    assert (
        cognee_config._get_endpoint_env("newapi", "COGNEE_LLM_ENDPOINT", "LLM_ENDPOINT")
        == "https://custom.example/v1"
    )


def test_cognee_provider_env_cannot_bypass_newapi(monkeypatch):
    from novelvideo.cognee import config as cognee_config

    monkeypatch.setenv("COGNEE_LLM_PROVIDER", "gemini")
    monkeypatch.setenv("COGNEE_LLM_API_KEY", "direct-secret")
    monkeypatch.setattr(
        cognee_config,
        "_effective_newapi_gateway",
        lambda: ("gateway-secret", "https://gateway.example/v1"),
    )

    assert cognee_config._resolve_llm_provider() == "newapi"
    assert (
        cognee_config._resolve_llm_api_key("newapi", "DC-cognee-LLM")
        == "gateway-secret"
    )


def test_cognee_embedding_provider_env_cannot_bypass_newapi(monkeypatch, tmp_path):
    _isolate_settings_db(monkeypatch, tmp_path)
    monkeypatch.setenv("COGNEE_EMBEDDING_PROVIDER", "gemini")
    monkeypatch.setenv("COGNEE_EMBEDDING_MODEL", "DC-cognee-embedding")

    effective = get_effective_cognee_embedding_config(llm_provider="gemini")

    assert effective.provider == "newapi"
    assert effective.model == "DC-cognee-embedding"


def test_ee_cognee_embedding_ignores_ce_database_config(monkeypatch, tmp_path):
    _isolate_settings_db(monkeypatch, tmp_path)
    save_newapi_embedding_model_config(
        provider="openai",
        upstream_model="stale-ce-embedding",
        dimension=3072,
    )
    monkeypatch.setenv("ST_EDITION", "ee")
    monkeypatch.setenv("ST_CONTROL_PLANE_DSN", "postgresql://control-plane")
    monkeypatch.setenv("COGNEE_EMBEDDING_MODEL", "DC-ee-embedding")
    monkeypatch.setenv("COGNEE_EMBEDDING_DIM", "1536")

    effective = get_effective_cognee_embedding_config()

    assert effective.source == "environment"
    assert effective.provider == "newapi"
    assert effective.model == "DC-ee-embedding"
    assert effective.dimensions == "1536"
    assert effective.upstream_model == ""


def test_model_gateway_can_switch_back_to_official(monkeypatch, tmp_path):
    _isolate_settings_db(monkeypatch, tmp_path)
    save_official_newapi_key(
        api_key="sk-official-secret",
        activate=True,
    )
    save_custom_newapi_gateway(
        base_url="http://127.0.0.1:3000/v1",
        api_key="sk-custom-secret",
        activate=True,
    )
    set_model_gateway_mode(MODE_OFFICIAL)

    effective = get_effective_newapi_config(
        official_base_url="https://official.example/v1",
        official_api_key="sk-official-secret",
    )
    assert effective.mode == MODE_OFFICIAL
    # [aipdd fork delta req41] 官方模式地址不再写死在代码里：CE 只看数据库 / 专用 env，
    # 两者都没配就是空（旧版本这里拿到的是被钉在源码里的那个站点域名）。
    assert effective.base_url == ""
    assert effective.api_key == "sk-official-secret"

    status = build_model_gateway_status(
        official_base_url="https://official.example/v1",
        official_api_key="sk-official-secret",
    )
    assert status["custom"]["configured"] is True
    assert status["effective"]["source"] == "official"


def test_model_gateway_status_keeps_official_section_when_custom_is_active(
    monkeypatch,
    tmp_path,
):
    _isolate_settings_db(monkeypatch, tmp_path)
    save_official_newapi_key(
        api_key="sk-official-secret",
        activate=True,
    )
    save_custom_newapi_gateway(
        base_url="http://new-api:3000",
        api_key="sk-custom-secret",
        activate=True,
    )

    status = build_model_gateway_status(
        official_base_url="https://env.example/v1",
        official_api_key="sk-env-secret",
    )

    assert status["mode"] == MODE_CUSTOM
    assert status["effective"]["source"] == "custom"
    assert status["effective"]["baseUrl"] == "http://new-api:3000/v1"
    assert status["official"]["baseUrl"] == ""
    # [aipdd fork delta req41] 数据库里只存了 official 的 key、没存地址：地址真正
    # 的来源是「未配置」，不能再像旧版那样拿源码常量充成 "database"。
    assert status["official"]["source"] == "unconfigured"


def test_model_gateway_official_database_key_overrides_env(monkeypatch, tmp_path):
    _isolate_settings_db(monkeypatch, tmp_path)
    save_official_newapi_key(
        api_key="sk-user-official-secret",
        activate=True,
    )

    effective = get_effective_newapi_config(
        official_base_url="https://env-official.example/v1",
        official_api_key="sk-env-official-secret",
    )
    assert effective.mode == MODE_OFFICIAL
    assert effective.base_url == ""
    assert effective.api_key == "sk-user-official-secret"

    status = build_model_gateway_status(
        official_base_url="https://env-official.example/v1",
        official_api_key="sk-env-official-secret",
    )
    # 数据库只给了 key：地址未配置，official.source 不再虚报 "database"。
    assert status["official"]["source"] == "unconfigured"
    assert status["official"]["environment"]["configured"] is False


def test_official_gateway_source_reports_real_origin(monkeypatch, tmp_path):
    """[aipdd fork delta req41] official.source 按地址真正的来源说话。

    旧版拿源码常量垫底，于是地址明明来自代码、状态页却报 ``database``；换网关地址
    / 不在本机部署时，这个假来源会把旧站点域名继续当成有效值。三态都要报对。
    """

    _isolate_settings_db(monkeypatch, tmp_path)
    save_official_newapi_key(api_key="sk-official-secret", activate=True)

    # 1) 哪里都没配地址 —— CE 下传入的 official_base_url 参数不参与官方栏。
    status = build_model_gateway_status(official_base_url="https://ignored.example/v1")
    assert status["official"]["baseUrl"] == ""
    assert status["official"]["source"] == "unconfigured"

    # 2) 只有专用 env —— 地址来自环境。
    monkeypatch.setenv("OFFICIAL_NEWAPI_BASE_URL", "https://official.env.example/v1")
    status = build_model_gateway_status()
    assert status["official"]["baseUrl"] == "https://official.env.example/v1"
    assert status["official"]["source"] == "environment"

    # 3) 数据库存了地址 —— 优先级最高，报 database。
    model_gateway_settings._write_many(
        {"official_newapi_base_url": "https://official-db.example/v1"}
    )
    status = build_model_gateway_status()
    assert status["official"]["baseUrl"] == "https://official-db.example/v1"
    assert status["official"]["source"] == "database"


def test_model_gateway_official_url_ignores_newapi_base_url_env(monkeypatch, tmp_path):
    _isolate_settings_db(monkeypatch, tmp_path)
    save_official_newapi_key(api_key="sk-database-secret", activate=True)
    monkeypatch.setenv("MODEL_GATEWAY_MODE", MODE_OFFICIAL)
    monkeypatch.setenv("NEWAPI_BASE_URL", "https://malicious.example/v1")
    monkeypatch.setenv("NEWAPI_API_KEY", "sk-env-secret")

    effective = get_effective_newapi_config()

    assert effective.mode == MODE_OFFICIAL
    # 专用 env 也没配时回空：不能拿 NEWAPI_BASE_URL 当官方地址（那是自配网关那一栏）。
    assert effective.base_url == ""
    assert effective.api_key == "sk-database-secret"

    # 只有专用 env 才能给官方模式定地址。
    monkeypatch.setenv("OFFICIAL_NEWAPI_BASE_URL", "https://official.env.example/v1")
    assert get_effective_newapi_config().base_url == "https://official.env.example/v1"


def test_ce_gateway_does_not_fall_back_to_env_after_database_is_initialized(
    monkeypatch, tmp_path
):
    _isolate_settings_db(monkeypatch, tmp_path)
    set_model_gateway_mode(MODE_OFFICIAL)
    monkeypatch.setenv("NEWAPI_API_KEY", "sk-later-secret")

    effective = get_effective_newapi_config()
    api_key, base_url = config.get_newapi_runtime_credentials()

    assert effective.api_key == ""
    assert api_key == ""
    assert base_url == ""


def test_ee_gateway_uses_environment_and_ignores_ce_settings(monkeypatch, tmp_path):
    _isolate_settings_db(monkeypatch, tmp_path)
    save_official_newapi_key(api_key="sk-ce-secret", activate=True)
    monkeypatch.setenv("ST_EDITION", "ee")
    monkeypatch.setenv("ST_CONTROL_PLANE_DSN", "postgresql://control-plane")
    monkeypatch.setenv("NEWAPI_API_KEY", "sk-ee-secret")
    monkeypatch.setenv("NEWAPI_BASE_URL", "https://ee-gateway.example/v1")

    effective = get_effective_newapi_config()

    assert effective.mode == MODE_OFFICIAL
    assert effective.source == "environment"
    assert effective.base_url == "https://ee-gateway.example/v1"
    assert effective.api_key == "sk-ee-secret"

    status = build_model_gateway_status()
    assert status["effective"]["baseUrl"] == "https://ee-gateway.example/v1"
    assert status["official"]["source"] == "environment"


def test_ee_cannot_mutate_ce_model_gateway_settings(monkeypatch, tmp_path):
    _isolate_settings_db(monkeypatch, tmp_path)
    monkeypatch.setenv("ST_EDITION", "ee")
    monkeypatch.setenv("ST_CONTROL_PLANE_DSN", "postgresql://control-plane")
    monkeypatch.setenv("NEWAPI_PROVISIONER_ENABLED", "true")

    app = FastAPI()
    app.include_router(model_gateway.router)
    response = TestClient(app).post(
        "/model-gateway/official/config",
        json={"newApiApiKey": "sk-should-not-be-saved"},
    )

    assert response.status_code == 403
    assert response.json()["detail"] == "ORG_SERVICE_EGRESS_DENIED"


def test_ce_runtime_refresh_never_mutates_process_environment(monkeypatch, tmp_path):
    from novelvideo.agents import global_video_optimizer

    _isolate_settings_db(monkeypatch, tmp_path)
    tracked = {
        "MODEL_GATEWAY_RUNTIME_VERSION": "startup-version",
        "NEWAPI_API_KEY": "startup-newapi-key",
        "NEWAPI_BASE_URL": "https://startup.example/v1",
        "OPENAI_API_KEY": "startup-openai-key",
        "OPENAI_BASE_URL": "https://startup-openai.example/v1",
        "LLM_API_KEY": "startup-llm-key",
        "LLM_ENDPOINT": "https://startup-llm.example/v1",
        "EMBEDDING_API_KEY": "startup-embedding-key",
        "EMBEDDING_ENDPOINT": "https://startup-embedding.example/v1",
    }
    for key, value in tracked.items():
        monkeypatch.setenv(key, value)
    # [aipdd fork delta req41] 官方模式地址不再由代码预设：这里要它「真的配上了」
    # 才能断言 configured=True，所以拿专用 env 给一个地址。
    monkeypatch.setenv("OFFICIAL_NEWAPI_BASE_URL", "https://official.env.example/v1")
    tracked["OFFICIAL_NEWAPI_BASE_URL"] = "https://official.env.example/v1"
    monkeypatch.setattr(global_video_optimizer, "_global_video_optimizer", object())
    save_official_newapi_key(api_key="sk-database-secret", activate=True)

    runtime = refresh_model_gateway_runtime()

    assert runtime["configured"] is True
    assert {key: os.environ.get(key) for key in tracked} == tracked
    assert global_video_optimizer._global_video_optimizer is None
    assert (
        "novelvideo.agents.global_video_optimizer._global_video_optimizer"
        in runtime["clearedCaches"]
    )


def test_newapi_base_url_normalizers_keep_admin_and_relay_urls_separate():
    assert normalize_admin_base_url("http://new-api:3000/v1") == "http://new-api:3000"
    assert normalize_admin_base_url("http://new-api:3000/") == "http://new-api:3000"
    assert normalize_relay_base_url("http://new-api:3000") == "http://new-api:3000/v1"
    assert (
        normalize_relay_base_url("http://new-api:3000/v1") == "http://new-api:3000/v1"
    )


def test_build_channel_payload_maps_dc_models_to_upstream_models():
    payload = build_channel_payload(
        provider="ali",
        name="user-supplied-name-is-ignored",
        upstream_key="sk-upstream",
        model_mapping={
            "DC-screenplay-normalizer-LLM": "qwen-plus",
            "DC-staging-prop-planner-LLM": "qwen-max",
        },
        group="default,drama",
        priority=2,
    )

    channel = payload["channel"]
    assert payload["mode"] == "single"
    assert channel["name"] == "DC-ali"
    assert channel["type"] == 17
    assert (
        channel["models"] == "DC-screenplay-normalizer-LLM,DC-staging-prop-planner-LLM"
    )
    assert channel["group"] == ",default,drama,"
    assert channel["test_model"] == "DC-screenplay-normalizer-LLM"
    assert channel["model_mapping"] == (
        '{"DC-screenplay-normalizer-LLM":"qwen-plus",'
        '"DC-staging-prop-planner-LLM":"qwen-max"}'
    )


# [aipdd fork delta] relay 会自行拼端点路径：渠道 base 自带同款版本后缀时必须剥掉，
# 否则请求打到 /v1/v1/... 或 /api/v3/api/v3/...，上游 404「资源不存在」。
def test_build_channel_payload_strips_trailing_v1_from_gateway_base():
    payload = build_channel_payload(
        provider="aipdd",
        upstream_key="sk-upstream",
        model_mapping={"DC-freezone-vision-LLM": "ap-doubao-seed-2.1-turbo"},
    )
    assert payload["channel"]["base_url"] == "https://api.aipdd.work"

    explicit = build_channel_payload(
        provider="aipdd",
        upstream_key="sk-upstream",
        model_mapping={"DC-freezone-vision-LLM": "ap-doubao-seed-2.1-turbo"},
        base_url="https://api.aipdd.work/v1/",
    )
    assert explicit["channel"]["base_url"] == "https://api.aipdd.work"

    # VolcEngine（type 45）适配器硬编码拼 /api/v3/...：base 自带的 /api/v3 同样要剥，
    # 剥后网关请求 = https://ark.cn-beijing.volces.com/api/v3/chat/completions。
    volc = build_channel_payload(
        provider="volcengine",
        upstream_key="sk-upstream",
        model_mapping={"seedance-2.0": "doubao-seedance-2-0-260128"},
        base_url="https://ark.cn-beijing.volces.com/api/v3",
    )
    assert volc["channel"]["base_url"] == "https://ark.cn-beijing.volces.com"


@respx.mock
def test_ensure_newapi_setup_creates_root_when_instance_is_fresh():
    cfg = NewApiProvisionerConfig(
        admin_base_url="http://new-api:3000",
        sql_dsn="local",
        sqlite_path="/tmp/one-api.db",
        admin_username="root",
        init_timeout_ms=1000,
        relay_token_name="dramaclaw-ce-runtime",
    )
    respx.get("http://new-api:3000/api/setup").mock(
        side_effect=[
            Response(200, json={"success": True, "data": {"status": False}}),
            Response(
                200,
                json={
                    "success": True,
                    "data": {
                        "status": False,
                        "root_init": False,
                        "database_type": "postgres",
                    },
                },
            ),
            Response(200, json={"success": True, "data": {"status": True}}),
            Response(
                200,
                json={
                    "success": True,
                    "data": {
                        "status": True,
                        "root_init": True,
                        "database_type": "postgres",
                    },
                },
            ),
        ]
    )
    setup_request = respx.post("http://new-api:3000/api/setup").mock(
        return_value=Response(200, json={"success": True})
    )

    status = ensure_newapi_setup(
        cfg,
        NewApiSetupCredentials(
            username="admin",
            password="strongpass",
            confirm_password="strongpass",
        ),
    )

    assert status.initialized is True
    assert status.root_initialized is True
    assert status.setup_performed is True
    assert status.already_initialized is False
    assert setup_request.calls.last.request.content
    assert json.loads(setup_request.calls.last.request.content) == {
        "SelfUseModeEnabled": True,
        "DemoSiteEnabled": False,
        "username": "admin",
        "password": "strongpass",
        "confirmPassword": "strongpass",
    }


@respx.mock
def test_ensure_newapi_setup_requires_credentials_for_fresh_instance():
    cfg = NewApiProvisionerConfig(
        admin_base_url="http://new-api:3000",
        sql_dsn="local",
        sqlite_path="/tmp/one-api.db",
        admin_username="root",
        init_timeout_ms=1000,
        relay_token_name="dramaclaw-ce-runtime",
    )
    respx.get("http://new-api:3000/api/setup").mock(
        side_effect=[
            Response(200, json={"success": True, "data": {"status": False}}),
            Response(
                200,
                json={
                    "success": True,
                    "data": {
                        "status": False,
                        "root_init": False,
                        "database_type": "postgres",
                    },
                },
            ),
        ]
    )

    with pytest.raises(ValueError, match="setupUsername"):
        ensure_newapi_setup(cfg, NewApiSetupCredentials(username="admin"))


@respx.mock
def test_ensure_newapi_setup_finishes_setup_when_root_already_exists():
    cfg = NewApiProvisionerConfig(
        admin_base_url="http://new-api:3000",
        sql_dsn="local",
        sqlite_path="/tmp/one-api.db",
        admin_username="root",
        init_timeout_ms=1000,
        relay_token_name="dramaclaw-ce-runtime",
    )
    respx.get("http://new-api:3000/api/setup").mock(
        side_effect=[
            Response(200, json={"success": True, "data": {"status": False}}),
            Response(
                200,
                json={
                    "success": True,
                    "data": {
                        "status": False,
                        "root_init": True,
                        "database_type": "postgres",
                    },
                },
            ),
            Response(200, json={"success": True, "data": {"status": True}}),
            Response(
                200,
                json={
                    "success": True,
                    "data": {
                        "status": True,
                        "root_init": True,
                        "database_type": "postgres",
                    },
                },
            ),
        ]
    )
    setup_request = respx.post("http://new-api:3000/api/setup").mock(
        return_value=Response(200, json={"success": True})
    )

    status = ensure_newapi_setup(cfg)

    assert status.initialized is True
    assert status.setup_performed is True
    assert status.already_initialized is False
    assert json.loads(setup_request.calls.last.request.content) == {
        "SelfUseModeEnabled": True,
        "DemoSiteEnabled": False,
    }


@respx.mock
def test_ensure_newapi_setup_skips_post_when_instance_is_initialized():
    cfg = NewApiProvisionerConfig(
        admin_base_url="http://new-api:3000",
        sql_dsn="local",
        sqlite_path="/tmp/one-api.db",
        admin_username="root",
        init_timeout_ms=1000,
        relay_token_name="dramaclaw-ce-runtime",
    )
    respx.get("http://new-api:3000/api/setup").mock(
        side_effect=[
            Response(200, json={"success": True, "data": {"status": True}}),
            Response(
                200,
                json={
                    "success": True,
                    "data": {
                        "status": True,
                        "root_init": True,
                        "database_type": "postgres",
                    },
                },
            ),
        ]
    )
    setup_request = respx.post("http://new-api:3000/api/setup").mock(
        return_value=Response(200, json={"success": True})
    )

    status = ensure_newapi_setup(
        cfg,
        NewApiSetupCredentials(
            username="root",
            password="strongpass",
            confirm_password="strongpass",
        ),
    )

    assert status.initialized is True
    assert status.root_initialized is True
    assert status.setup_performed is False
    assert status.already_initialized is True
    assert not setup_request.called


@respx.mock
def test_upsert_channel_replaces_existing_dc_provider_channel():
    """[aipdd fork delta] 拍平改造：upsert 的 model_mapping 按「替换」语义整体覆写，
    旧键不残留（merge 会让历史键永久回流）。"""
    cfg = NewApiProvisionerConfig(
        admin_base_url="http://new-api:3000",
        sql_dsn="local",
        sqlite_path="/tmp/one-api.db",
        admin_username="root",
        init_timeout_ms=1000,
        relay_token_name="dramaclaw-ce-runtime",
    )
    admin = AdminToken(
        admin_user_id=1,
        admin_username="root",
        access_token="admin-secret",
        token_created=False,
    )
    payload = build_channel_payload(
        provider="ali",
        upstream_key="sk-upstream-new",
        model_mapping={"DC-screenplay-normalizer-LLM": "qwen-plus"},
        base_url="https://dashscope-new.example.com",
    )

    respx.get("http://new-api:3000/api/channel/").mock(
        return_value=Response(
            200,
            json={
                "success": True,
                "data": {
                    "items": [{"id": 3, "name": "DC-ali", "type": 17}],
                    "total": 1,
                },
            },
        )
    )
    respx.get("http://new-api:3000/api/channel/3").mock(
        return_value=Response(
            200,
            json={
                "success": True,
                "data": {
                    "id": 3,
                    "name": "DC-ali",
                    "type": 17,
                    "key": "sk-upstream-old",
                    "base_url": "https://dashscope-old.example.com",
                    "models": "DC-old-model",
                    "model_mapping": json.dumps({"DC-old-model": "qwen-old"}),
                    "group": ",default,",
                    "status": 1,
                },
            },
        )
    )
    update_route = respx.put("http://new-api:3000/api/channel/").mock(
        return_value=Response(200, json={"success": True})
    )

    result = upsert_channel(cfg, admin, payload)

    assert result["ok"] is True
    assert result["action"] == "update"
    assert result["channelId"] == 3
    channel = json.loads(update_route.calls.last.request.content)
    assert channel["id"] == 3
    assert channel["name"] == "DC-ali"
    assert channel["key"] == "sk-upstream-new"
    assert channel["base_url"] == "https://dashscope-new.example.com"
    assert "status" not in channel
    # [aipdd fork delta] 拍平：mapping 替换语义——旧键 DC-old-model 不残留。
    assert channel["models"] == "DC-screenplay-normalizer-LLM"
    assert json.loads(channel["model_mapping"]) == {
        "DC-screenplay-normalizer-LLM": "qwen-plus",
    }


@respx.mock
def test_update_provider_channel_credentials_preserves_models_and_mapping():
    cfg = NewApiProvisionerConfig(
        admin_base_url="http://new-api:3000",
        sql_dsn="local",
        sqlite_path="/tmp/one-api.db",
        admin_username="root",
        init_timeout_ms=1000,
        relay_token_name="dramaclaw-ce-runtime",
    )
    admin = AdminToken(
        admin_user_id=1,
        admin_username="root",
        access_token="admin-secret",
        token_created=False,
    )

    respx.get("http://new-api:3000/api/channel/").mock(
        return_value=Response(
            200,
            json={
                "success": True,
                "data": {
                    "items": [{"id": 3, "name": "DC-ali", "type": 17}],
                    "total": 1,
                },
            },
        )
    )
    respx.get("http://new-api:3000/api/channel/3").mock(
        return_value=Response(
            200,
            json={
                "success": True,
                "data": {
                    "id": 3,
                    "name": "DC-ali",
                    "type": 17,
                    "key": "sk-upstream-old",
                    "base_url": "https://dashscope-old.example.com",
                    "models": "DC-old-model,DC-screenplay-normalizer-LLM",
                    "model_mapping": json.dumps(
                        {
                            "DC-old-model": "qwen-old",
                            "DC-screenplay-normalizer-LLM": "qwen-plus",
                        }
                    ),
                    "group": ",default,",
                    "priority": 2,
                    "weight": 3,
                    "test_model": "DC-old-model",
                },
            },
        )
    )
    update_route = respx.put("http://new-api:3000/api/channel/").mock(
        return_value=Response(200, json={"success": True})
    )

    result = update_provider_channel_credentials(
        cfg,
        admin,
        provider="ali",
        upstream_key="sk-upstream-new",
        base_url="https://dashscope-new.example.com/",
    )

    assert result["ok"] is True
    assert result["action"] == "update"
    assert result["channelId"] == 3
    channel = json.loads(update_route.calls.last.request.content)
    assert channel["key"] == "sk-upstream-new"
    assert channel["base_url"] == "https://dashscope-new.example.com"
    assert channel["models"] == "DC-old-model,DC-screenplay-normalizer-LLM"
    assert json.loads(channel["model_mapping"]) == {
        "DC-old-model": "qwen-old",
        "DC-screenplay-normalizer-LLM": "qwen-plus",
    }
    assert channel["priority"] == 2
    assert channel["weight"] == 3
    assert channel["test_model"] == "DC-old-model"


@respx.mock
def test_update_provider_channel_credentials_clears_base_url_override():
    cfg = NewApiProvisionerConfig(
        admin_base_url="http://new-api:3000",
        sql_dsn="local",
        sqlite_path="/tmp/one-api.db",
        admin_username="root",
        init_timeout_ms=1000,
        relay_token_name="dramaclaw-ce-runtime",
    )
    admin = AdminToken(
        admin_user_id=1,
        admin_username="root",
        access_token="admin-secret",
        token_created=False,
    )

    respx.get("http://new-api:3000/api/channel/").mock(
        return_value=Response(
            200,
            json={
                "success": True,
                "data": {
                    "items": [{"id": 3, "name": "DC-ali", "type": 17}],
                    "total": 1,
                },
            },
        )
    )
    respx.get("http://new-api:3000/api/channel/3").mock(
        return_value=Response(
            200,
            json={
                "success": True,
                "data": {
                    "id": 3,
                    "name": "DC-ali",
                    "type": 17,
                    "key": "sk-upstream-old",
                    "base_url": "https://dashscope-old.example.com",
                    "models": "DC-old-model",
                    "model_mapping": json.dumps({"DC-old-model": "qwen-old"}),
                    "group": ",default,",
                    "test_model": "DC-old-model",
                },
            },
        )
    )
    update_route = respx.put("http://new-api:3000/api/channel/").mock(
        return_value=Response(200, json={"success": True})
    )

    result = update_provider_channel_credentials(
        cfg,
        admin,
        provider="ali",
        upstream_key="sk-upstream-new",
        base_url="",
    )

    assert result["ok"] is True
    channel = json.loads(update_route.calls.last.request.content)
    assert channel["key"] == "sk-upstream-new"
    assert channel["base_url"] == ""
    assert channel["models"] == "DC-old-model"
    assert json.loads(channel["model_mapping"]) == {"DC-old-model": "qwen-old"}


@respx.mock
def test_upsert_channel_replaces_mapping_without_cross_channel_dedupe():
    """[aipdd fork delta] 拍平改造：upsert 不再跨渠道防抢注（dedupe 退役）。

    目标渠道的 model_mapping 按「替换」语义整体覆写——旧键不残留；其它渠道
    （即便挂有同名 DC-* 键）完全不被读取 / 改写。同名多渠道由复合调用名在网
    关侧天然隔离，系统不做任何隐式裁决。
    """
    cfg = NewApiProvisionerConfig(
        admin_base_url="http://new-api:3000",
        sql_dsn="local",
        sqlite_path="/tmp/one-api.db",
        admin_username="root",
        init_timeout_ms=1000,
        relay_token_name="dramaclaw-ce-runtime",
    )
    admin = AdminToken(
        admin_user_id=1,
        admin_username="root",
        access_token="admin-secret",
        token_created=False,
    )
    payload = build_channel_payload(
        provider="openrouter",
        upstream_key="sk-openrouter",
        model_mapping={"DC-hermes-LLM": "google/gemini-2.5-flash"},
    )

    respx.get("http://new-api:3000/api/channel/").mock(
        return_value=Response(
            200,
            json={
                "success": True,
                "data": {
                    "items": [
                        {"id": 4, "name": "DC-openrouter", "type": 20},
                        {"id": 3, "name": "DC-ali", "type": 17},
                    ],
                },
            },
        )
    )
    respx.get("http://new-api:3000/api/channel/4").mock(
        return_value=Response(
            200,
            json={
                "success": True,
                "data": {
                    "id": 4,
                    "name": "DC-openrouter",
                    "type": 20,
                    "key": "sk-old-openrouter",
                    "base_url": "https://openrouter.ai/api",
                    "models": "DC-old-openrouter-model",
                    "model_mapping": json.dumps(
                        {"DC-old-openrouter-model": "openrouter/old"}
                    ),
                    "group": ",default,",
                    "status": 1,
                },
            },
        )
    )
    # 渠道 3 故意不 mock：拍平后它既不该被读详情也不该被写——一旦回归 dedupe
    # 触碰其它渠道，respx 会因未匹配请求直接报错（防回归探针）。
    update_route = respx.put("http://new-api:3000/api/channel/").mock(
        return_value=Response(200, json={"success": True})
    )

    result = upsert_channel(cfg, admin, payload)

    assert result["ok"] is True
    assert result["action"] == "update"
    # dedupe 退役：不再有 dedupedChannels 字段，也不产生第二次 PUT。
    assert "dedupedChannels" not in result
    assert len(update_route.calls) == 1
    target_update = json.loads(update_route.calls[0].request.content)
    assert target_update["id"] == 4
    # 替换语义：旧键 DC-old-openrouter-model 被整体覆写，不残留（merge 会回流）。
    assert target_update["models"] == "DC-hermes-LLM"
    assert target_update["test_model"] == "DC-hermes-LLM"
    assert json.loads(target_update["model_mapping"]) == {
        "DC-hermes-LLM": "google/gemini-2.5-flash",
    }


def test_provisioner_enabled_by_default_and_can_be_disabled(monkeypatch):
    monkeypatch.setenv("ST_EDITION", "ce")
    monkeypatch.delenv("ST_CONTROL_PLANE_DSN", raising=False)
    monkeypatch.delenv("NEWAPI_PROVISIONER_ENABLED", raising=False)
    require_provisioner_enabled()

    monkeypatch.setenv("NEWAPI_PROVISIONER_ENABLED", "false")
    with pytest.raises(PermissionError, match="not enabled"):
        require_provisioner_enabled()

    monkeypatch.setenv("NEWAPI_PROVISIONER_ENABLED", "true")
    require_provisioner_enabled()


def test_provisioner_is_always_disabled_in_ee(monkeypatch):
    monkeypatch.setenv("ST_EDITION", "ee")
    monkeypatch.setenv("ST_CONTROL_PLANE_DSN", "postgresql://control-plane")
    monkeypatch.setenv("NEWAPI_PROVISIONER_ENABLED", "true")

    with pytest.raises(PermissionError, match="not enabled"):
        require_provisioner_enabled()


def test_newapi_db_defaults_to_managed_ce_sqlite_and_does_not_create_empty_file(
    monkeypatch,
    tmp_path,
):
    _isolate_settings_db(monkeypatch, tmp_path)
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("NEWAPI_SQL_DSN", raising=False)
    monkeypatch.delenv("NEWAPI_SQLITE_PATH", raising=False)

    cfg = model_gateway.get_provisioner_config()

    # [aipdd fork delta req41] 管理面地址不预设 127.0.0.1：那等于假定「网关跟后端在同一
    # 台机器」，容器网络 / 跨机部署下会指到错的地方。没配就是空，用它的地方报可执行错误。
    assert cfg.admin_base_url == ""
    monkeypatch.setenv("NEWAPI_ADMIN_BASE_URL", "http://newapi:3000")
    assert model_gateway.get_provisioner_config().admin_base_url == "http://newapi:3000"
    monkeypatch.delenv("NEWAPI_ADMIN_BASE_URL", raising=False)
    assert cfg.sql_dsn == "local"
    assert cfg.sqlite_path == str(tmp_path / "state" / "newapi" / "one-api.db")
    with pytest.raises(RuntimeError, match="does not exist"):
        open_newapi_db(cfg)

    assert not (tmp_path / "state" / "newapi" / "one-api.db").exists()


def test_newapi_db_rejects_missing_sqlite_file(monkeypatch, tmp_path):
    _isolate_settings_db(monkeypatch, tmp_path)
    missing = tmp_path / "missing-one-api.db"
    monkeypatch.setenv("NEWAPI_SQL_DSN", "local")
    monkeypatch.setenv("NEWAPI_SQLITE_PATH", str(missing))

    with pytest.raises(RuntimeError, match="does not exist"):
        open_newapi_db(model_gateway.get_provisioner_config())

    assert not missing.exists()


def test_provisioner_config_prefers_saved_database_settings(monkeypatch, tmp_path):
    _isolate_settings_db(monkeypatch, tmp_path)
    monkeypatch.setenv(
        "NEWAPI_SQL_DSN", "postgresql://env:envpass@127.0.0.1:5432/envdb"
    )
    monkeypatch.setenv("NEWAPI_SQLITE_PATH", "/env/one-api.db")
    monkeypatch.setenv("NEWAPI_ADMIN_USERNAME", "env-root")
    monkeypatch.setenv("NEWAPI_ADMIN_BASE_URL", "http://env-new-api:3000")
    save_custom_newapi_gateway(
        base_url="http://saved-new-api:3000/v1",
        api_key="sk-custom-secret",
        admin_base_url="http://saved-new-api:3000",
        activate=True,
    )
    save_newapi_database_config(
        sql_dsn="local",
        sqlite_path="/saved/one-api.db",
        admin_username="saved-root",
    )

    cfg = get_provisioner_config()

    assert cfg.admin_base_url == "http://saved-new-api:3000"
    assert cfg.sql_dsn == "local"
    assert cfg.sqlite_path == "/saved/one-api.db"
    assert cfg.admin_username == "saved-root"


def test_provisioner_config_request_database_overrides_saved_settings(
    monkeypatch, tmp_path
):
    _isolate_settings_db(monkeypatch, tmp_path)
    save_newapi_database_config(
        sql_dsn="local",
        sqlite_path="/saved/one-api.db",
        admin_username="saved-root",
    )

    cfg = get_provisioner_config(
        "http://request-new-api:3000",
        sql_dsn="postgresql://request:secret@127.0.0.1:5432/newapi",
        sqlite_path="",
        admin_username="request-root",
    )

    assert cfg.admin_base_url == "http://request-new-api:3000"
    assert cfg.sql_dsn == "postgresql://request:secret@127.0.0.1:5432/newapi"
    assert cfg.sqlite_path == ""
    assert cfg.admin_username == "request-root"


def test_database_status_does_not_expose_database_credentials(monkeypatch, tmp_path):
    _isolate_settings_db(monkeypatch, tmp_path)
    save_newapi_database_config(
        sql_dsn="postgresql://root:secret@127.0.0.1:5432/newapi",
        admin_username="root",
    )

    status = build_newapi_database_status()

    assert status["configured"] is True
    assert status["source"] == "database"
    assert status["databaseType"] == "external"
    assert "sqlDsnPreview" not in status
    assert "sqlitePath" not in status
    assert "adminUsername" not in status
    assert "secret" not in str(status)


def test_model_gateway_config_route_masks_effective_key(monkeypatch, tmp_path):
    _isolate_settings_db(monkeypatch, tmp_path)
    monkeypatch.setattr(
        model_gateway.app_config, "NEWAPI_BASE_URL", "https://official.example/v1"
    )
    monkeypatch.setattr(
        model_gateway.app_config, "NEWAPI_API_KEY", "sk-official-secret"
    )
    save_official_newapi_key(
        api_key="sk-official-secret",
        activate=True,
    )

    app = FastAPI()
    app.include_router(model_gateway.router)
    client = TestClient(app)

    response = client.get("/model-gateway/config")

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["mode"] == MODE_OFFICIAL
    assert data["effective"]["apiKeyPreview"] == "sk-o...cret"
    assert "sk-official-secret" not in response.text


def test_ee_model_gateway_config_skips_ce_provisioner_and_settings(
    monkeypatch,
    tmp_path,
):
    _isolate_settings_db(monkeypatch, tmp_path)
    monkeypatch.setenv("ST_EDITION", "ee")
    monkeypatch.setenv("ST_CONTROL_PLANE_DSN", "postgresql://control-plane")
    monkeypatch.setenv("NEWAPI_BASE_URL", "https://ee-gateway.example/v1")
    monkeypatch.setenv("NEWAPI_API_KEY", "sk-ee-secret")
    monkeypatch.setattr(model_gateway.app_config, "NEWAPI_API_KEY", "sk-ee-secret")
    monkeypatch.setattr(
        model_gateway,
        "build_provisioner_status",
        lambda: pytest.fail("EE must not build CE provisioner status"),
    )
    monkeypatch.setattr(
        model_gateway_settings,
        "_connect",
        lambda: pytest.fail("EE must not open CE settings.db"),
    )
    app = FastAPI()
    app.include_router(model_gateway.router)

    response = TestClient(app).get("/model-gateway/config")

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["effective"]["baseUrl"] == "https://ee-gateway.example/v1"
    assert data["provisioner"] == {
        "enabled": False,
        "adminBaseUrl": "",
        "dbConfigured": False,
        "database": {
            "configured": False,
            "available": False,
            "source": "unavailable",
        },
        "adminUsername": "",
        "relayTokenName": "",
        "providers": {},
        "providerChannels": [],
        "mediaModels": {},
        "embeddingModel": {},
        "relayBaseUrl": "",
    }
    assert not (tmp_path / "state").exists()


def test_ce_model_gateway_config_uses_provisioner_builder(monkeypatch, tmp_path):
    _isolate_settings_db(monkeypatch, tmp_path)
    expected = {
        "enabled": True,
        "adminBaseUrl": "http://new-api:3000",
        "dbConfigured": True,
        "database": {"configured": True},
        "adminUsername": "root",
        "relayTokenName": "ce-runtime",
        "providers": {"openrouter": {}},
        "providerChannels": [],
        "mediaModels": {},
        "embeddingModel": {},
        "relayBaseUrl": "http://new-api:3000/v1",
    }
    monkeypatch.setattr(model_gateway, "build_provisioner_status", lambda: expected)
    app = FastAPI()
    app.include_router(model_gateway.router)

    response = TestClient(app).get("/model-gateway/config")

    assert response.status_code == 200
    assert response.json()["data"]["provisioner"] == expected


def test_model_gateway_config_excludes_closed_source_provider_presets(
    monkeypatch, tmp_path
):
    _isolate_settings_db(monkeypatch, tmp_path)

    app = FastAPI()
    app.include_router(model_gateway.router)
    client = TestClient(app)

    response = client.get("/model-gateway/config")

    assert response.status_code == 200
    providers = response.json()["data"]["provisioner"]["providers"]
    assert "ali" in providers
    assert "openrouter" in providers
    assert "deepseek" in providers
    # [aipdd fork delta] openai preset 已重命名为 newapi（五渠道收敛），不再是内置预设。
    assert "newapi" in providers
    assert providers["newapi"]["type"] == 1
    assert "openai" not in providers
    assert providers["azure"]["type"] == 3
    assert providers["gemini"]["type"] == 24
    assert providers["volcengine"]["type"] == 45
    assert providers["codex"]["type"] == 57
    assert "huimeng" not in providers
    assert "fal" not in providers


def test_enable_official_gateway_route_switches_mode_when_enabled(
    monkeypatch, tmp_path
):
    _isolate_settings_db(monkeypatch, tmp_path)
    monkeypatch.setenv("NEWAPI_PROVISIONER_ENABLED", "true")
    # [aipdd fork delta req41] 官方地址只认专用 env（代码不预设站点），路由
    # 改成调 ``app_config.official_newapi_base_url()``，所以这里得 setenv 而不是 setattr 常量。
    monkeypatch.setenv("OFFICIAL_NEWAPI_BASE_URL", "https://official.example/v1")
    monkeypatch.setattr(
        model_gateway.app_config, "NEWAPI_API_KEY", "sk-official-secret"
    )
    save_official_newapi_key(
        api_key="sk-official-secret",
        activate=True,
    )
    save_custom_newapi_gateway(
        base_url="http://new-api:3000",
        api_key="sk-custom-secret",
        activate=True,
    )

    app = FastAPI()
    app.include_router(model_gateway.router)
    client = TestClient(app)

    response = client.post("/model-gateway/official/enable")

    assert response.status_code == 200
    assert response.json()["data"]["mode"] == MODE_OFFICIAL
    assert (
        get_effective_newapi_config(
            official_base_url="https://official.example/v1",
            official_api_key="sk-official-secret",
        ).mode
        == MODE_OFFICIAL
    )


def test_official_media_catalog_preferences_and_remote_update(monkeypatch, tmp_path):
    _isolate_settings_db(monkeypatch, tmp_path)
    source_url = "https://catalog.example.test/official_media_models.json"
    monkeypatch.setenv("OFFICIAL_MEDIA_CATALOG_URL", source_url)
    remote_version = _newer_bundled_catalog_version()
    payload = {
        "version": 1,
        "catalogVersion": remote_version,
        "name": "Test official media models",
        "mediaModels": {
            "test-video": {
                "provider": "newapi",
                "upstreamModel": "test-video",
                "mediaType": "video",
                "label": "Test Video",
                "enabled": True,
                "sortOrder": 10,
                "config": {
                    "request": {
                        "endpoint": "video/generations",
                        "parameters": [],
                    }
                },
            }
        },
    }
    app = FastAPI()
    app.include_router(model_gateway.router)
    client = TestClient(app)

    initial = client.get("/model-gateway/official/media-catalog")
    enabled = client.post(
        "/model-gateway/official/media-catalog/preferences",
        json={"autoUpdate": True},
    )
    with respx.mock:
        respx.get(source_url).mock(return_value=Response(200, json=payload))
        checked = client.post("/model-gateway/official/media-catalog/check")
    current = client.get("/model-gateway/official/media-catalog")

    assert initial.status_code == 200
    assert initial.json()["data"]["autoUpdate"] is False
    assert initial.json()["data"]["source"] == "bundled"
    assert enabled.json()["data"]["autoUpdate"] is True
    assert checked.status_code == 200, checked.text
    assert checked.json()["data"]["updated"] is True
    assert current.json()["data"]["source"] == "remote"
    assert current.json()["data"]["catalogVersion"] == remote_version
    assert current.json()["data"]["modelCount"] == 1


def test_official_media_catalog_default_is_offline_bundled_no_remote_url(monkeypatch):
    # Zero default outbound calls: with no operator-provided URL there is no built-in
    # remote endpoint, so the catalog stays on the bundled copy and a remote check
    # refuses rather than phoning home.
    monkeypatch.delenv("OFFICIAL_MEDIA_CATALOG_MANIFEST_URL", raising=False)
    monkeypatch.delenv("OFFICIAL_MEDIA_CATALOG_URL", raising=False)

    assert official_media_catalog_remote.DEFAULT_OFFICIAL_MEDIA_CATALOG_URL == ""
    assert official_media_catalog_remote.DEFAULT_OFFICIAL_MEDIA_CATALOG_MANIFEST_URL == ""
    with pytest.raises(ValueError):
        official_media_catalog_remote._remote_source()


def test_official_media_catalog_manifest_verifies_hash_and_revalidates_etag(
    monkeypatch, tmp_path
):
    _isolate_settings_db(monkeypatch, tmp_path)
    manifest_url = "https://catalog.example.test/official-media-catalog/manifest.json"
    monkeypatch.setenv("OFFICIAL_MEDIA_CATALOG_MANIFEST_URL", manifest_url)
    payload = {
        "version": 1,
        "catalogVersion": _newer_bundled_catalog_version(2),
        "name": "Test official media models",
        "mediaModels": {
            "test-video": {
                "provider": "newapi",
                "upstreamModel": "test-video",
                "mediaType": "video",
                "label": "Test Video",
                "enabled": True,
                "sortOrder": 10,
                "config": {
                    "request": {
                        "endpoint": "video/generations",
                        "parameters": [],
                    }
                },
            }
        },
    }
    canonical = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    digest = hashlib.sha256(canonical).hexdigest()
    catalog_path = f"catalogs/{digest}.json"
    catalog_url = f"https://catalog.example.test/official-media-catalog/{catalog_path}"
    manifest = {
        "schemaVersion": 1,
        "catalogVersion": payload["catalogVersion"],
        "revision": "a" * 40,
        "publishedAt": "2026-08-07T12:00:00Z",
        "sha256": digest,
        "path": catalog_path,
    }
    app = FastAPI()
    app.include_router(model_gateway.router)
    client = TestClient(app)

    with respx.mock:
        manifest_route = respx.get(manifest_url).mock(
            side_effect=[
                Response(200, json=manifest, headers={"etag": '"release-1"'}),
                Response(304, headers={"etag": '"release-1"'}),
            ]
        )
        catalog_route = respx.get(catalog_url).mock(
            return_value=Response(200, content=canonical)
        )
        installed = client.post("/model-gateway/official/media-catalog/check")
        unchanged = client.post("/model-gateway/official/media-catalog/check")

    assert installed.status_code == 200, installed.text
    installed_data = installed.json()["data"]
    assert installed_data["updated"] is True
    assert installed_data["source"] == "remote"
    assert installed_data["revision"] == "a" * 40
    assert installed_data["publishedAt"] == "2026-08-07T12:00:00Z"
    assert installed_data["sha256"] == digest
    assert installed_data["remoteUrl"] == manifest_url
    assert installed_data["lastError"] == ""
    assert unchanged.status_code == 200, unchanged.text
    assert unchanged.json()["data"]["updated"] is False
    assert len(catalog_route.calls) == 1
    assert len(manifest_route.calls) == 2
    assert manifest_route.calls[1].request.headers["if-none-match"] == '"release-1"'


def test_official_media_catalog_manifest_rejects_hash_mismatch(monkeypatch, tmp_path):
    _isolate_settings_db(monkeypatch, tmp_path)
    manifest_url = "https://catalog.example.test/releases/manifest.json"
    monkeypatch.setenv("OFFICIAL_MEDIA_CATALOG_MANIFEST_URL", manifest_url)
    bundled = json.loads(
        Path(model_gateway_settings.__file__)
        .with_name("official_media_models.json")
        .read_text(encoding="utf-8")
    )
    payload = {**bundled, "catalogVersion": "2026.08.07.2"}
    actual_digest = hashlib.sha256(
        json.dumps(
            payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
    ).hexdigest()
    manifest = {
        "schemaVersion": 1,
        "catalogVersion": payload["catalogVersion"],
        "revision": "b" * 40,
        "publishedAt": "2026-08-07T12:00:00Z",
        "sha256": "0" * 64,
        "path": f"catalogs/{'0' * 64}.json",
    }
    app = FastAPI()
    app.include_router(model_gateway.router)
    client = TestClient(app)

    with respx.mock:
        respx.get(manifest_url).mock(return_value=Response(200, json=manifest))
        respx.get(
            f"https://catalog.example.test/releases/catalogs/{'0' * 64}.json"
        ).mock(return_value=Response(200, json=payload))
        response = client.post("/model-gateway/official/media-catalog/check")

    assert response.status_code == 502
    assert "SHA256" in response.json()["detail"]
    status = client.get("/model-gateway/official/media-catalog").json()["data"]
    assert status["source"] == "bundled"
    assert "SHA256" in status["lastError"]
    assert actual_digest != manifest["sha256"]


def test_official_media_catalog_manifest_rejects_absolute_content_url():
    with pytest.raises(ValueError, match="path"):
        official_media_catalog_remote.validate_official_media_catalog_manifest(
            {
                "schemaVersion": 1,
                "catalogVersion": "2026.08.07.1",
                "revision": "c" * 40,
                "publishedAt": "2026-08-07T12:00:00Z",
                "sha256": "d" * 64,
                "path": "https://attacker.example/catalog.json",
            }
        )


def test_official_media_catalog_rejects_downgrade(monkeypatch, tmp_path):
    _isolate_settings_db(monkeypatch, tmp_path)
    source_url = "https://catalog.example.test/official_media_models.json"
    monkeypatch.setenv("OFFICIAL_MEDIA_CATALOG_URL", source_url)
    bundled = json.loads(
        Path(model_gateway_settings.__file__)
        .with_name("official_media_models.json")
        .read_text(encoding="utf-8")
    )
    downgraded = {**bundled, "catalogVersion": "2026.08.05.9"}
    app = FastAPI()
    app.include_router(model_gateway.router)
    client = TestClient(app)

    with respx.mock:
        route = respx.get(source_url)
        route.mock(return_value=Response(200, json=downgraded))
        downgrade_response = client.post(
            "/model-gateway/official/media-catalog/check"
        )

    assert downgrade_response.status_code == 502
    assert "downgrade" in downgrade_response.json()["detail"]
    status = client.get("/model-gateway/official/media-catalog").json()["data"]
    assert status["source"] == "bundled"


def test_official_media_catalog_rejects_reused_version_with_different_content(
    monkeypatch, tmp_path
):
    _isolate_settings_db(monkeypatch, tmp_path)
    bundled = json.loads(
        Path(model_gateway_settings.__file__)
        .with_name("official_media_models.json")
        .read_text(encoding="utf-8")
    )
    changed = {**bundled, "name": "Changed without a version bump"}

    with pytest.raises(ValueError, match="version already exists"):
        model_gateway_settings.install_official_media_catalog(
            changed,
            source_url="https://catalog.example.test/official_media_models.json",
        )

    cache_path = Path(config.STATE_DIR) / "local" / "official_media_models.json"
    assert not cache_path.exists()


def test_official_media_catalog_rejects_invalid_model_before_cache_write(
    monkeypatch, tmp_path
):
    _isolate_settings_db(monkeypatch, tmp_path)
    source_url = "https://catalog.example.test/official_media_models.json"
    monkeypatch.setenv("OFFICIAL_MEDIA_CATALOG_URL", source_url)
    payload = {
        "version": 1,
        "catalogVersion": "2026.08.06.2",
        "mediaModels": {
            "broken-video": {
                "provider": "newapi",
                "upstreamModel": "broken-video",
                "mediaType": "video",
                "sortOrder": "not-a-number",
                "config": {
                    "request": {
                        "endpoint": "video/generations",
                        "parameters": [],
                    }
                },
            }
        },
    }
    app = FastAPI()
    app.include_router(model_gateway.router)
    client = TestClient(app)

    with respx.mock:
        respx.get(source_url).mock(return_value=Response(200, json=payload))
        response = client.post("/model-gateway/official/media-catalog/check")

    assert response.status_code == 502
    assert "sortOrder must be an integer" in response.json()["detail"]
    cache_path = Path(config.STATE_DIR) / "local" / "official_media_models.json"
    assert not cache_path.exists()
    assert client.get("/model-gateway/official/media-catalog").json()["data"][
        "source"
    ] == "bundled"


def test_official_media_catalog_ignores_cache_older_than_bundle(monkeypatch, tmp_path):
    _isolate_settings_db(monkeypatch, tmp_path)
    bundled = json.loads(
        Path(model_gateway_settings.__file__)
        .with_name("official_media_models.json")
        .read_text(encoding="utf-8")
    )
    cached = json.loads(json.dumps(bundled))
    cached["catalogVersion"] = "2026.08.05.9"
    cache_path = Path(config.STATE_DIR) / "local" / "official_media_models.json"
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps(cached), encoding="utf-8")

    status = model_gateway_settings.get_official_media_catalog_update_status()

    assert status["source"] == "bundled"
    assert status["catalogVersion"] == bundled["catalogVersion"]


def test_save_official_gateway_route_persists_user_registered_key(
    monkeypatch, tmp_path
):
    _isolate_settings_db(monkeypatch, tmp_path)
    monkeypatch.setenv("NEWAPI_PROVISIONER_ENABLED", "true")
    monkeypatch.setattr(
        model_gateway.app_config, "NEWAPI_BASE_URL", "https://env.example/v1"
    )
    monkeypatch.setattr(model_gateway.app_config, "NEWAPI_API_KEY", "sk-env-secret")

    app = FastAPI()
    app.include_router(model_gateway.router)
    client = TestClient(app)

    response = client.post(
        "/model-gateway/official/config",
        json={
            "newApiApiKey": "sk-user-registered-secret",
        },
    )

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["mode"] == MODE_OFFICIAL
    assert data["official"]["baseUrl"] == ""
    assert data["official"]["source"] == "unconfigured"
    assert data["official"]["apiKeyPreview"] == "sk-u...cret"
    assert "sk-user-registered-secret" not in response.text

    effective = get_effective_newapi_config(
        official_base_url="https://env.example/v1",
        official_api_key="sk-env-secret",
    )
    assert effective.base_url == ""
    assert effective.api_key == "sk-user-registered-secret"


def test_save_official_gateway_route_ignores_submitted_gateway_url(
    monkeypatch, tmp_path
):
    _isolate_settings_db(monkeypatch, tmp_path)
    monkeypatch.setenv("NEWAPI_PROVISIONER_ENABLED", "true")
    # [aipdd fork delta req41] 官方地址只认专用 env：代码不预设站点，路由也不采纳
    # 前端提交的 URL（防把官方模式变成任意中转）。
    monkeypatch.setenv("OFFICIAL_NEWAPI_BASE_URL", "https://official.env.example/v1")
    monkeypatch.setattr(
        model_gateway.app_config, "NEWAPI_BASE_URL", "https://env.example/v1"
    )
    monkeypatch.setattr(model_gateway.app_config, "NEWAPI_API_KEY", "sk-env-secret")

    app = FastAPI()
    app.include_router(model_gateway.router)
    client = TestClient(app)

    response = client.post(
        "/model-gateway/official/config",
        json={
            "newApiBaseUrl": "https://official-user.example",
            "newApiApiKey": "sk-user-registered-secret",
        },
    )

    assert response.status_code == 200
    assert response.json()["data"]["official"]["baseUrl"] == "https://official.env.example/v1"
    assert "official-user.example" not in response.text
    effective = get_effective_newapi_config(
        official_base_url="https://env.example/v1",
        official_api_key="sk-env-secret",
    )
    assert effective.base_url == "https://official.env.example/v1"
    assert effective.api_key == "sk-user-registered-secret"


def test_custom_newapi_init_route_accepts_empty_body(monkeypatch, tmp_path):
    _isolate_settings_db(monkeypatch, tmp_path)
    monkeypatch.setenv("NEWAPI_PROVISIONER_ENABLED", "true")
    calls = {}

    class Admin:
        admin_user_id = 1
        admin_username = "root"
        token_created = False
        access_token = "admin-secret"

    def fake_get_config(base_url=None, **_kwargs):
        calls["base_url"] = base_url
        return type(
            "Cfg",
            (),
            {
                "admin_base_url": "http://new-api:3000",
                "relay_token_name": "dramaclaw-ce-runtime",
            },
        )()

    monkeypatch.setattr(model_gateway, "get_provisioner_config", fake_get_config)
    monkeypatch.setattr(
        model_gateway,
        "ensure_newapi_setup",
        lambda *_args, **_kwargs: type(
            "SetupStatus",
            (),
            {
                "initialized": True,
                "root_initialized": True,
                "database_type": "sqlite",
                "setup_performed": False,
                "already_initialized": True,
            },
        )(),
    )
    monkeypatch.setattr(
        model_gateway, "ensure_admin_access_token", lambda _cfg: Admin()
    )
    monkeypatch.setattr(
        model_gateway,
        "create_or_reuse_relay_token",
        lambda *_args, **_kwargs: {
            "created": False,
            "tokenId": 2,
            "name": "dramaclaw-ce-runtime",
            "key": "sk-runtime-secret",
            "keyPreview": "sk-r...cret",
        },
    )

    app = FastAPI()
    app.include_router(model_gateway.router)
    client = TestClient(app)

    response = client.post("/model-gateway/custom/newapi/init")

    assert response.status_code == 200
    data = response.json()["data"]
    assert calls["base_url"] is None
    assert data["mode"] == MODE_CUSTOM
    assert data["newApiAdminBaseUrl"] == "http://new-api:3000"
    assert data["newApiBaseUrl"] == "http://new-api:3000/v1"
    assert "sk-runtime-secret" not in response.text


def test_custom_newapi_init_route_persists_request_database_config(
    monkeypatch, tmp_path
):
    _isolate_settings_db(monkeypatch, tmp_path)
    monkeypatch.setenv("NEWAPI_PROVISIONER_ENABLED", "true")
    calls = {}

    class Admin:
        admin_user_id = 1
        admin_username = "root"
        token_created = False
        access_token = "admin-secret"

    def fake_get_config(base_url=None, **kwargs):
        calls["base_url"] = base_url
        calls["kwargs"] = kwargs
        return type(
            "Cfg",
            (),
            {
                "admin_base_url": "http://new-api:3000",
                "relay_token_name": "dramaclaw-ce-runtime",
                "sql_dsn": kwargs["sql_dsn"],
                "sqlite_path": kwargs["sqlite_path"],
                "admin_username": kwargs["admin_username"],
            },
        )()

    monkeypatch.setattr(model_gateway, "get_provisioner_config", fake_get_config)
    monkeypatch.setattr(
        model_gateway,
        "ensure_newapi_setup",
        lambda *_args, **_kwargs: type(
            "SetupStatus",
            (),
            {
                "initialized": True,
                "root_initialized": True,
                "database_type": "sqlite",
                "setup_performed": False,
                "already_initialized": True,
            },
        )(),
    )
    monkeypatch.setattr(
        model_gateway, "ensure_admin_access_token", lambda _cfg: Admin()
    )
    monkeypatch.setattr(
        model_gateway,
        "create_or_reuse_relay_token",
        lambda *_args, **_kwargs: {
            "created": True,
            "tokenId": 7,
            "name": "dramaclaw-ce-runtime",
            "key": "sk-runtime-secret",
            "keyPreview": "sk-r...cret",
        },
    )

    app = FastAPI()
    app.include_router(model_gateway.router)
    client = TestClient(app)

    response = client.post(
        "/model-gateway/custom/newapi/init",
        json={
            "newApiBaseUrl": "http://new-api:3000",
            "database": {
                "sqlDsn": "local",
                "sqlitePath": "/Users/hg/data/new-api/one-api.db",
                "adminUsername": "root",
            },
        },
    )

    assert response.status_code == 200
    assert calls["base_url"] == "http://new-api:3000"
    assert calls["kwargs"] == {
        "sql_dsn": "local",
        "sqlite_path": "/Users/hg/data/new-api/one-api.db",
        "admin_username": "root",
    }
    data = response.json()["data"]
    assert data["database"]["configured"] is True
    assert data["database"]["source"] == "database"
    assert data["database"]["databaseType"] == "sqlite"
    assert "sqlitePath" not in data["database"]
    cfg = get_provisioner_config()
    assert cfg.sql_dsn == "local"
    assert cfg.sqlite_path == "/Users/hg/data/new-api/one-api.db"
    assert cfg.admin_username == "root"
    assert cfg.admin_base_url == "http://new-api:3000"


def test_custom_newapi_channels_batch_reuses_admin_and_masks_keys(
    monkeypatch, tmp_path
):
    _isolate_settings_db(monkeypatch, tmp_path)
    monkeypatch.setenv("NEWAPI_PROVISIONER_ENABLED", "true")
    calls: dict[str, list[object] | int] = {"payloads": [], "ensure_admin": 0}

    class Admin:
        admin_user_id = 1
        admin_username = "root"
        token_created = False
        access_token = "admin-secret"

    def fake_get_config(base_url=None, **_kwargs):
        assert base_url == "http://new-api:3000"
        return type("Cfg", (), {"admin_base_url": "http://new-api:3000"})()

    def fake_ensure_admin(_cfg):
        calls["ensure_admin"] = int(calls["ensure_admin"]) + 1
        return Admin()

    def fake_upsert_channel(_cfg, _admin, payload):
        calls["payloads"].append(payload)
        return {
            "ok": True,
            "httpStatus": 200,
            "newApiResponse": {"success": True},
            "action": "create",
            "channelId": None,
        }

    monkeypatch.setattr(model_gateway, "get_provisioner_config", fake_get_config)
    monkeypatch.setattr(model_gateway, "ensure_admin_access_token", fake_ensure_admin)
    monkeypatch.setattr(model_gateway, "upsert_channel", fake_upsert_channel)

    app = FastAPI()
    app.include_router(model_gateway.router)
    client = TestClient(app)

    # [req40] 注册表只从渠道目录编译：批落真源前先把两个渠道的目录同步出来，
    # 复合名 / 单渠道唯一裸名才有实据可注。
    model_gateway_settings.save_upstream_models_snapshot("ali", ["qwen-plus"])
    model_gateway_settings.save_upstream_models_snapshot("deepseek", ["deepseek-chat"])

    response = client.post(
        "/model-gateway/custom/newapi/channels/batch",
        json={
            "newApiBaseUrl": "http://new-api:3000",
            "channels": [
                {
                    "provider": "ali",
                    "name": "ali-text",
                    "upstreamKey": "sk-upstream-one",
                    "modelMapping": {"DC-screenplay-normalizer-LLM": "qwen-plus"},
                },
                {
                    "provider": "deepseek",
                    "name": "deepseek-text",
                    "upstreamKey": "sk-upstream-two",
                    "modelMapping": {"DC-hermes-LLM": "deepseek-chat"},
                    "priority": 3,
                },
            ],
        },
    )

    assert response.status_code == 200
    data = response.json()["data"]
    assert response.json()["ok"] is True
    assert data["succeeded"] == 2
    assert data["failed"] == 0
    assert calls["ensure_admin"] == 1
    assert len(calls["payloads"]) == 2
    # [aipdd fork delta] 拍平：提交的映射先入真源，编译出完整注册表——内部绑定
    # （DC-* 裸名）+ 复合名（ali::qwen-plus）+ 单渠道唯一裸名（qwen-plus）。
    assert (
        data["results"][0]["sentPayload"]["channel"]["models"]
        == "DC-screenplay-normalizer-LLM,ali::qwen-plus,qwen-plus"
    )
    assert data["results"][1]["sentPayload"]["channel"]["type"] == 43
    assert "sk-upstream-one" not in response.text
    assert "sk-upstream-two" not in response.text


def test_custom_newapi_channel_types_route_patches_volcengine_vision_and_image(
    monkeypatch, tmp_path
):
    """[aipdd fork delta] 渠道能力补丁：volcengine 渠道类型叠加 vision + image。

    设置页「业务模型映射」的渠道候选完全依赖 capabilities 过滤（前端
    providersSupportingCapability：拿到渠道类型就以能力位为准）；LocalNewAPI 镜像
    内置表对 volcengine（type 45）只声明 text/embedding/video，缺此补丁会让
    VolcEngine 渠道在「视觉理解」和「图片」两个 TAB 里凭空消失——用户在图片 TAB
    选不到火山渠道，配不出 media_model_mappings 的 image 行，画布生图下拉（与设置
    页严格同源）也就永远不会有 Seedream。image 这一位有实测依据：向生效网关
    POST /v1/images/generations + doubao-seedream-4-0-20260415（网关路径原形：不带
    size，只 width/height + metadata）返回 200 与图片 URL。其余渠道保持镜像口径，
    不改写。
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
                "type": 45,
                "provider": "volcengine",
                "name": "VolcEngine",
                "description": "",
                "capabilities": ["text", "embedding", "video"],
            },
            {
                "type": 1,
                "provider": "openai",
                "name": "OpenAI",
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
    items = {
        item["provider"]: item for item in response.json()["data"]["items"]
    }
    # volcengine：补丁能力叠加在既有能力位之后，且不重复。
    assert items["volcengine"]["capabilities"] == [
        "text",
        "embedding",
        "video",
        "vision",
        "image",
    ]
    # 其余渠道保持镜像口径，不做任何改写。
    assert items["openai"]["capabilities"] == ["text"]
    # 合成行：镜像注册表缺失的 fork 自有渠道（newapi / aipdd / mediakit）在末尾补齐，
    # 供「供应商渠道管理」目录与已配置渠道 / 渠道连接卡一一对应。
    assert items["newapi"]["name"] == "NewAPI"
    assert items["newapi"]["type"] == 1
    assert items["aipdd"]["name"] == "AIPDD"
    assert items["aipdd"]["status"] == 1
    assert items["mediakit"]["name"] == "火山 AI MediaKit"
    assert items["mediakit"]["capabilities"] == ["video"]
    assert items["mediakit"]["status"] == 1
    assert items["mediakit"]["description"].startswith("火山 AI MediaKit")


def test_volcengine_image_mapping_surfaces_in_ce_image_catalog(monkeypatch, tmp_path):
    """[aipdd fork delta] 火山方舟生图进画布：配了就一定在目录里。

    能力位补上 image 后，用户能在「设置 › 模型 › 图片」把 Seedream 配成映射；这条
    钉住配好之后画布目录（与设置页同源）一定拿得到它：执行身份仍是 newapi（图片
    协议由生效网关做 Ark 转换、不绕过到 Ark base），channel 才是实际所属渠道；同时
    Ark 快照本身不声明任何档位（mediaMetadata 为空），目录这一侧必须补出默认能力，
    否则画布上会出现一个参数不能调的模型。
    """
    _isolate_settings_db(monkeypatch, tmp_path)
    save_newapi_media_model_mappings(
        {
            "doubao-seedream-4-0-20260415": {
                "provider": "volcengine",
                "upstreamModel": "",
                "mediaType": "image",
                "label": "Seedream 4.0",
                "enabled": True,
                "config": {"request": {"endpoint": "images/generations", "parameters": []}},
            }
        }
    )

    catalog = get_ce_media_model_catalog("image")
    entry = next(
        item for item in catalog if item["id"] == "doubao-seedream-4-0-20260415"
    )
    assert entry["providerId"] == "newapi"
    assert entry["channel"] == "volcengine"
    assert entry["apiModel"] == "doubao-seedream-4-0-20260415"
    assert entry["request"]["endpoint"] == "images/generations"

    from novelvideo.catalog.media_model_discovery import apply_default_image_capabilities

    apply_default_image_capabilities([entry])
    assert entry["resolutionOptions"]
    assert entry["ratioOptions"]


def test_minimax_channel_types_override_mirror_adapter_and_synthesize_cn(
    monkeypatch, tmp_path
):
    """[aipdd fork delta req46] MiniMax 双渠道的目录行。

    海外站：镜像注册表把 minimax 登成 type 35 的厂商私有适配器（能力仅 text），
    fork 事实是整体覆盖为 type 1 + 四路能力（含本需求新增的 music）；国内站：镜像
    没有该 provider，走合成行补进目录。两个能力位都错不得——覆盖不成 type 1 文本就
    走不到 OpenAI 兼容协议，缺 music 则「设置 › 音乐」的候选过滤直接筛不到渠道。
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
                "type": 35,
                "provider": "minimax",
                "name": "MiniMax",
                "description": "MiniMax official adapter",
                "capabilities": ["text"],
            }
        ],
    )

    app = FastAPI()
    app.include_router(model_gateway.router)
    client = TestClient(app)

    response = client.get("/model-gateway/custom/newapi/channel-types")
    assert response.status_code == 200
    items = {
        item["provider"]: item for item in response.json()["data"]["items"]
    }
    # 镜像行被 fork 事实整体覆盖（type 不再是 35，能力不再是只 text）。
    assert items["minimax"]["type"] == 1
    assert items["minimax"]["capabilities"] == ["text", "video", "audio", "music"]
    assert items["minimax"]["defaultBaseUrl"] == "https://api.minimax.io/v1"
    # 国内站靠合成行出现在目录里（与已配置渠道 / 渠道连接卡一一对应）。
    assert items["minimax_cn"]["type"] == 1
    assert items["minimax_cn"]["status"] == 1
    assert items["minimax_cn"]["capabilities"] == ["text", "video", "audio", "music"]
    assert items["minimax_cn"]["defaultBaseUrl"] == "https://api.minimax.cn/v1"


def test_minimax_provider_presets_are_openai_compatible_and_base_url_idempotent(
    monkeypatch, tmp_path
):
    """[aipdd fork delta req46] MiniMax 预设登记为 OpenAI 兼容，base 归一不重复加后缀。

    两边 Key 不通用，所以是两个 provider；自有库里的 base 必须带 /v1（面板拉清单要
    打 {base}/models），写网关时再由 build_channel_payload 剥掉后缀。
    """
    _isolate_settings_db(monkeypatch, tmp_path)

    assert PROVIDER_PRESETS["minimax"]["type"] == 1
    assert PROVIDER_PRESETS["minimax"]["base_url"] == "https://api.minimax.io/v1"
    assert PROVIDER_PRESETS["minimax_cn"]["type"] == 1
    assert PROVIDER_PRESETS["minimax_cn"]["base_url"] == "https://api.minimax.cn/v1"

    normalize = model_gateway_settings.normalize_channel_base_url
    for provider, host in (("minimax", "api.minimax.io"), ("minimax_cn", "api.minimax.cn")):
        bare = f"https://{host}"
        once = normalize(provider, bare)
        assert once == f"{bare}/v1"
        # 幂等：已带版本后缀（含 /v2 这类其它版本）原样保留。
        assert normalize(provider, once) == once
        assert normalize(provider, f"{bare}/v2") == f"{bare}/v2"

    saved = {
        channel["provider"]: channel
        for channel in save_newapi_provider_channels(
            [
                {
                    "provider": "minimax",
                    "upstreamKey": "sk-minimax-global",
                    "baseUrl": "https://api.minimax.io",
                },
                {
                    "provider": "minimax_cn",
                    "upstreamKey": "sk-minimax-cn",
                    "baseUrl": "https://api.minimax.cn",
                },
            ]
        )
    }
    assert saved["minimax"]["baseUrl"] == "https://api.minimax.io/v1"
    assert saved["minimax_cn"]["baseUrl"] == "https://api.minimax.cn/v1"

    for provider, host in (("minimax", "api.minimax.io"), ("minimax_cn", "api.minimax.cn")):
        payload = build_channel_payload(
            provider=provider,
            upstream_key="sk-minimax",
            model_mapping={"DC-test-LLM": "some-upstream-model"},
            base_url=f"https://{host}/v1",
        )
        # type=1 → 网关自行拼 /v1/chat/completions，渠道 base 不能带版本后缀。
        assert payload["channel"]["type"] == 1
        assert payload["channel"]["base_url"] == f"https://{host}"
        assert payload["channel"]["name"] == f"DC-{provider}"


def test_minimax_settings_extension_roundtrips_with_asr_style_block(
    monkeypatch, tmp_path
):
    """[aipdd fork delta req46] 渠道级扩展配置 settings.minimax（与 volcengine 的
    settings.asr 同形态）：保存后原样读回，且不丢其它扩展键。"""
    _isolate_settings_db(monkeypatch, tmp_path)

    save_newapi_provider_channels(
        [
            {
                "provider": "minimax",
                "upstreamKey": "sk-minimax-global",
                "baseUrl": "https://api.minimax.io/v1",
                "settings": {"minimax": {"voiceId": "male-qn-qingse"}},
            }
        ]
    )
    saved = model_gateway_settings.get_newapi_provider_channel("minimax") or {}
    assert saved["settings"]["minimax"]["voiceId"] == "male-qn-qingse"

    # 后续只改 baseUrl 时不携带 settings：原有扩展配置保留。
    save_newapi_provider_channels(
        [
            {
                "provider": "minimax",
                "upstreamKey": "sk-minimax-global",
                "baseUrl": "https://api.minimax.io/v1",
            }
        ]
    )
    saved = model_gateway_settings.get_newapi_provider_channel("minimax") or {}
    assert saved["settings"]["minimax"]["voiceId"] == "male-qn-qingse"


@respx.mock
def test_minimax_upstream_catalog_listing_hits_versioned_models_endpoint(
    monkeypatch, tmp_path
):
    """[aipdd fork delta req46] 渠道保存即拉清单：打桩 {base}/models 成功入快照。

    模型名只能来自渠道目录（req40），所以 MiniMax 的文本 / 音乐候选能不能出现，
    全看这一路拉取；裸域名必须被归一到 /v1 后才能命中官方端点。
    """
    _isolate_settings_db(monkeypatch, tmp_path)
    reset_upstream_models_cache()
    route = respx.get("https://api.minimax.io/v1/models").mock(
        return_value=Response(
            200,
            json={
                "object": "list",
                "data": [
                    {"id": "MiniMax-M3", "object": "model"},
                    {"id": "music-3.0", "object": "model"},
                    {
                        "id": "removed-model",
                        "object": "model",
                        "aipdd": {"available": False},
                    },
                ],
            },
        )
    )

    entries, error = list_upstream_catalog(
        "minimax",
        base_url=model_gateway_settings.normalize_channel_base_url(
            "minimax", "https://api.minimax.io"
        ),
        upstream_key="sk-minimax-global",
        force=True,
    )

    assert error == ""
    assert route.called
    names = [entry["name"] for entry in entries]
    assert "MiniMax-M3" in names
    assert "music-3.0" in names
    # 渠道明确标了 available=false 的名字不入库：它已经不在上游提供，不能成为候选。
    assert "removed-model" not in names
    snapshot = get_upstream_catalog("minimax")
    assert {entry["name"] for entry in snapshot} == set(names)


def test_custom_newapi_provider_channels_route_persists_and_masks_keys(
    monkeypatch,
    tmp_path,
):
    _isolate_settings_db(monkeypatch, tmp_path)
    monkeypatch.setenv("NEWAPI_PROVISIONER_ENABLED", "true")

    app = FastAPI()
    app.include_router(model_gateway.router)
    client = TestClient(app)

    response = client.post(
        "/model-gateway/custom/newapi/provider-channels",
        json={
            "channels": [
                {
                    "provider": "ali",
                    "upstreamKey": "sk-ali-upstream-secret",
                    "baseUrl": "https://dashscope.example.com/",
                },
                {
                    "provider": "deepseek",
                    "upstreamKey": "sk-deepseek-upstream-secret",
                },
            ]
        },
    )

    assert response.status_code == 200
    assert response.json()["ok"] is True
    assert "sk-ali-upstream-secret" not in response.text
    assert "sk-deepseek-upstream-secret" not in response.text

    config_response = client.get("/model-gateway/config")
    channels = config_response.json()["data"]["provisioner"]["providerChannels"]
    # [aipdd fork delta] req08：渠道 base 保存 / 读取期补齐版本后缀（裸域名 → /v1），
    # 媒体直连与能力发现依赖该形态；deepseek 未填 base 保持空串。
    assert channels == [
        {
            "provider": "ali",
            "type": 0,
            "configured": True,
            "upstreamKeyPreview": "sk-a...cret",
            "baseUrl": "https://dashscope.example.com/v1",
            "priority": 0,
            "settings": {},
        },
        {
            "provider": "deepseek",
            "type": 0,
            "configured": True,
            "upstreamKeyPreview": "sk-d...cret",
            "baseUrl": "",
            "priority": 0,
            "settings": {},
        },
    ]
    assert "sk-ali-upstream-secret" not in config_response.text
    assert "sk-deepseek-upstream-secret" not in config_response.text


def test_custom_newapi_provider_channels_route_removes_final_channel(
    monkeypatch,
    tmp_path,
):
    _isolate_settings_db(monkeypatch, tmp_path)
    monkeypatch.setenv("NEWAPI_PROVISIONER_ENABLED", "true")

    app = FastAPI()
    app.include_router(model_gateway.router)
    client = TestClient(app)

    create_response = client.post(
        "/model-gateway/custom/newapi/provider-channels",
        json={
            "channels": [
                {
                    "provider": "openrouter",
                    "upstreamKey": "sk-openrouter-secret",
                }
            ]
        },
    )
    assert create_response.status_code == 200
    save_newapi_media_model_mappings(
        {
            "speech-preview": {
                "provider": "openrouter",
                "upstreamModel": "speech-upstream",
                "mediaType": "audio",
            }
        }
    )
    save_newapi_embedding_model_config(
        provider="openrouter",
        upstream_model="embedding-upstream",
        dimension=1024,
    )

    delete_response = client.post(
        "/model-gateway/custom/newapi/provider-channels",
        json={"channels": [], "preserveUnmentioned": False},
    )
    assert delete_response.status_code == 200
    assert delete_response.json()["data"]["channels"] == []
    assert delete_response.json()["data"]["mediaModels"] == {}
    assert delete_response.json()["data"]["embeddingModel"] is None

    config_response = client.get("/model-gateway/config")
    assert config_response.status_code == 200
    provisioner = config_response.json()["data"]["provisioner"]
    assert provisioner["providerChannels"] == []
    assert provisioner["mediaModels"] == {}
    assert provisioner["embeddingModel"] == {}


def test_provider_channel_replacement_only_cascades_removed_provider(
    monkeypatch,
    tmp_path,
):
    _isolate_settings_db(monkeypatch, tmp_path)
    save_newapi_provider_channels(
        [
            {"provider": "openrouter", "upstreamKey": "sk-openrouter"},
            {"provider": "volcengine", "upstreamKey": "sk-volcengine"},
        ]
    )
    save_newapi_media_model_mappings(
        {
            "openrouter-image": {
                "provider": "openrouter",
                "upstreamModel": "openrouter/image",
                "mediaType": "image",
            },
            "volcengine-video": {
                "provider": "volcengine",
                "upstreamModel": "volcengine/video",
                "mediaType": "video",
            },
        }
    )
    save_newapi_embedding_model_config(
        provider="openrouter",
        upstream_model="openrouter/embedding",
        dimension=1024,
    )

    save_newapi_provider_channels(
        [{"provider": "volcengine", "upstreamKey": ""}],
        preserve_unmentioned=False,
    )

    channels = get_newapi_provider_channels()
    assert [channel["provider"] for channel in channels] == ["volcengine"]
    assert channels[0]["upstreamKey"] == "sk-volcengine"
    assert get_newapi_media_model_mappings() == {
        "volcengine-video": {
            "provider": "volcengine",
            "upstreamModel": "volcengine/video",
            "mediaType": "video",
        }
    }
    assert get_newapi_embedding_model_config() == {}


def test_comfyui_provider_channel_writes_workflows_to_newapi(
    monkeypatch,
    tmp_path,
):
    _isolate_settings_db(monkeypatch, tmp_path)
    monkeypatch.setenv("NEWAPI_PROVISIONER_ENABLED", "true")
    captured: dict[str, object] = {}

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
    app = FastAPI()
    app.include_router(model_gateway.router)
    client = TestClient(app)
    workflow = {
        "6": {"class_type": "CLIPTextEncode", "inputs": {"text": ""}},
    }

    response = client.post(
        "/model-gateway/custom/newapi/provider-channels",
        json={
            "channels": [
                {
                    "provider": "comfyui",
                    "type": 63,
                    "baseUrl": "http://127.0.0.1:8188",
                    "priority": 100,
                    "settings": {
                        "comfyui": {"workflow_by_model": {"wan-i2v": workflow}}
                    },
                }
            ]
        },
    )

    assert response.status_code == 200
    channel = captured["payload"]["channel"]
    assert channel["type"] == 63
    assert channel["key"] == "none"
    assert channel["models"] == "wan-i2v"
    assert channel["priority"] == 100
    assert json.loads(channel["settings"])["comfyui"]["workflow_by_model"] == {
        "wan-i2v": workflow
    }
    comfy_mapping = get_newapi_media_model_mappings()["wan-i2v"]
    assert comfy_mapping["provider"] == "comfyui"
    assert comfy_mapping["upstreamModel"] == ""
    assert comfy_mapping["mediaType"] == "video"
    assert comfy_mapping["config"]["request"]["endpoint"] == "video/generations"
    assert comfy_mapping["config"]["resolutionOptions"] == ["480p", "640p"]
    assert comfy_mapping["config"]["ratioOptions"] == ["16:9", "1:1"]
    assert comfy_mapping["config"]["supportedModes"] == [
        "image_to_video",
        "image_reference",
    ]
    assert comfy_mapping["config"]["referenceImageMax"] == 1
    assert "humanReview" not in comfy_mapping["config"]
    assert comfy_mapping["config"]["_dcManagedByWorkflow"] is True


def test_comfyui_workflow_routes_create_one_media_model(monkeypatch, tmp_path):
    _isolate_settings_db(monkeypatch, tmp_path)
    monkeypatch.setenv("NEWAPI_PROVISIONER_ENABLED", "true")
    captured: dict[str, object] = {}
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
    app = FastAPI()
    app.include_router(model_gateway.router)
    client = TestClient(app)
    workflow = {"6": {"class_type": "CLIPTextEncode", "inputs": {"text": ""}}}
    routes = [
        {
            "id": route_id,
            "match": {},
            "workflow": workflow,
        }
        for route_id in ("minimax_h3_t2v", "minimax_h3_i2v", "minimax_h3_r2v")
    ]

    response = client.post(
        "/model-gateway/custom/newapi/provider-channels",
        json={
            "channels": [
                {
                    "provider": "comfyui",
                    "type": 63,
                    "baseUrl": "http://127.0.0.1:8188",
                    "settings": {
                        "comfyui": {
                            "model_name": "MiniMax-H3-local",
                            "workflow_routes": routes,
                        }
                    },
                }
            ]
        },
    )

    assert response.status_code == 200, response.text
    channel = captured["payload"]["channel"]
    assert channel["models"] == "MiniMax-H3-local"
    assert json.loads(channel["settings"])["comfyui"]["workflow_routes"] == routes
    mappings = get_newapi_media_model_mappings()
    assert set(mappings) == {"MiniMax-H3-local"}
    config = mappings["MiniMax-H3-local"]["config"]
    assert config["resolutionOptions"] == ["480p", "768p", "1080p"]
    assert config["ratioOptions"] == [
        "21:9",
        "16:9",
        "4:3",
        "1:1",
        "3:4",
        "9:16",
    ]
    assert config["supportedModes"] == [
        "text_to_video",
        "first_frame",
        "all_reference",
    ]
    assert config["referenceImageMax"] == 9
    assert config["referenceVideoMax"] == 3
    assert config["referenceAudioMax"] == 3


def test_comfyui_workflow_routes_require_one_model_name(monkeypatch, tmp_path):
    _isolate_settings_db(monkeypatch, tmp_path)
    app = FastAPI()
    app.include_router(model_gateway.router)
    client = TestClient(app)

    response = client.post(
        "/model-gateway/custom/newapi/provider-channels",
        json={
            "channels": [
                {
                    "provider": "comfyui",
                    "type": 63,
                    "baseUrl": "http://127.0.0.1:8188",
                    "settings": {
                        "comfyui": {
                            "workflow_routes": [
                                {
                                    "id": "minimax_h3_t2v",
                                    "match": {},
                                    "workflow": {
                                        "6": {
                                            "class_type": "CLIPTextEncode",
                                            "inputs": {"text": ""},
                                        }
                                    },
                                }
                            ]
                        }
                    },
                }
            ]
        },
    )

    assert response.status_code == 400
    assert "one model name" in response.json()["detail"]


def test_clear_comfyui_removes_channel_and_media_models(monkeypatch, tmp_path):
    _isolate_settings_db(monkeypatch, tmp_path)
    monkeypatch.setenv("NEWAPI_PROVISIONER_ENABLED", "true")
    save_newapi_provider_channels(
        [
            {
                "provider": "openrouter",
                "type": 20,
                "upstreamKey": "secret",
                "baseUrl": "",
                "settings": {},
            },
            {
                "provider": "comfyui",
                "type": 63,
                "upstreamKey": "",
                "baseUrl": "http://127.0.0.1:8188",
                "settings": {
                    "comfyui": {
                            "model_name": "MiniMax-H3-local",
                            "workflow_routes": [
                                {
                                    "id": "minimax_h3_t2v",
                                    "match": {},
                                    "workflow": {
                                        "6": {
                                            "class_type": "CLIPTextEncode",
                                            "inputs": {"text": ""},
                                        }
                                    },
                                }
                            ],
                    }
                },
            },
        ]
    )
    save_newapi_media_model_mappings(
        {
            "MiniMax-H3-local": {
                "provider": "comfyui",
                "upstreamModel": "",
                "mediaType": "video",
                "config": {},
            },
            "seedance-2.0": {
                "provider": "volcengine",
                "upstreamModel": "doubao-seedance-2-0",
                "mediaType": "video",
                "config": {},
            },
        }
    )
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
    deleted: dict[str, object] = {}

    def fake_delete(_cfg, _admin, **kwargs):
        deleted.update(kwargs)
        return True

    monkeypatch.setattr(model_gateway, "delete_channel_by_name", fake_delete)
    app = FastAPI()
    app.include_router(model_gateway.router)
    client = TestClient(app)

    response = client.delete("/model-gateway/custom/newapi/comfyui")

    assert response.status_code == 200, response.text
    assert deleted == {"name": "DC-comfyui", "channel_type": 63}
    assert [item["provider"] for item in get_newapi_provider_channels()] == [
        "openrouter"
    ]
    assert set(get_newapi_media_model_mappings()) == {"seedance-2.0"}


def test_custom_newapi_provider_channel_sync_updates_newapi_and_local_config(
    monkeypatch,
    tmp_path,
):
    _isolate_settings_db(monkeypatch, tmp_path)
    monkeypatch.setenv("NEWAPI_PROVISIONER_ENABLED", "true")

    class Admin:
        admin_user_id = 1
        admin_username = "root"
        token_created = False
        access_token = "admin-secret"

    calls: dict[str, object] = {}

    monkeypatch.setattr(
        model_gateway,
        "get_provisioner_config",
        lambda _base_url=None, **_kwargs: type(
            "Cfg",
            (),
            {"admin_base_url": "http://new-api:3000"},
        )(),
    )
    monkeypatch.setattr(
        model_gateway, "ensure_admin_access_token", lambda _cfg: Admin()
    )

    def fake_update_credentials(_cfg, _admin, *, provider, upstream_key, base_url=None):
        calls["provider"] = provider
        calls["upstream_key"] = upstream_key
        calls["base_url"] = base_url
        return {
            "ok": True,
            "httpStatus": 200,
            "newApiResponse": {"success": True},
            "sentPayload": {
                "mode": "single",
                "channel": {
                    "id": 7,
                    "name": "DC-ali",
                    "key": upstream_key,
                    "base_url": base_url,
                },
            },
            "channelId": 7,
        }

    monkeypatch.setattr(
        model_gateway,
        "update_provider_channel_credentials",
        fake_update_credentials,
    )

    app = FastAPI()
    app.include_router(model_gateway.router)
    client = TestClient(app)

    response = client.post(
        "/model-gateway/custom/newapi/provider-channel/sync",
        json={
            "newApiBaseUrl": "http://new-api:3000",
            "provider": "ali",
            "upstreamKey": "sk-ali-new-upstream-secret",
            "baseUrl": "https://dashscope-new.example.com/",
        },
    )

    assert response.status_code == 200
    assert response.json()["ok"] is True
    assert calls == {
        "provider": "ali",
        "upstream_key": "sk-ali-new-upstream-secret",
        "base_url": "https://dashscope-new.example.com/",
    }
    assert "sk-ali-new-upstream-secret" not in response.text
    # [aipdd fork delta] req08：savedChannel 回读的是归一后的库存值（带 /v1）；
    # 上面的 calls 断言仍是路由直传给 provisioner 的原始输入（带尾斜杠）。
    assert response.json()["data"]["savedChannel"] == {
        "provider": "ali",
        "configured": True,
        "upstreamKeyPreview": "sk-a...cret",
        "baseUrl": "https://dashscope-new.example.com/v1",
    }

    config_response = client.get("/model-gateway/config")
    channels = config_response.json()["data"]["provisioner"]["providerChannels"]
    assert channels == [
        {
            "provider": "ali",
            "type": 0,
            "configured": True,
            "upstreamKeyPreview": "sk-a...cret",
            "baseUrl": "https://dashscope-new.example.com/v1",
            "priority": 0,
            "settings": {},
        }
    ]
    assert "sk-ali-new-upstream-secret" not in config_response.text


def test_custom_newapi_provider_channel_sync_allows_clearing_saved_base_url(
    monkeypatch,
    tmp_path,
):
    _isolate_settings_db(monkeypatch, tmp_path)
    monkeypatch.setenv("NEWAPI_PROVISIONER_ENABLED", "true")
    save_newapi_provider_channels(
        [
            {
                "provider": "ali",
                "upstreamKey": "sk-ali-old-upstream-secret",
                "baseUrl": "https://dashscope-old.example.com",
            }
        ]
    )

    class Admin:
        admin_user_id = 1
        admin_username = "root"
        token_created = False
        access_token = "admin-secret"

    calls: dict[str, object] = {}

    monkeypatch.setattr(
        model_gateway,
        "get_provisioner_config",
        lambda _base_url=None, **_kwargs: type(
            "Cfg",
            (),
            {"admin_base_url": "http://new-api:3000"},
        )(),
    )
    monkeypatch.setattr(
        model_gateway, "ensure_admin_access_token", lambda _cfg: Admin()
    )

    def fake_update_credentials(_cfg, _admin, *, provider, upstream_key, base_url=None):
        calls["provider"] = provider
        calls["upstream_key"] = upstream_key
        calls["base_url"] = base_url
        return {
            "ok": True,
            "httpStatus": 200,
            "newApiResponse": {"success": True},
            "sentPayload": {
                "mode": "single",
                "channel": {
                    "id": 7,
                    "name": "DC-ali",
                    "key": upstream_key,
                    "base_url": base_url,
                },
            },
            "channelId": 7,
        }

    monkeypatch.setattr(
        model_gateway,
        "update_provider_channel_credentials",
        fake_update_credentials,
    )

    app = FastAPI()
    app.include_router(model_gateway.router)
    client = TestClient(app)

    response = client.post(
        "/model-gateway/custom/newapi/provider-channel/sync",
        json={
            "newApiBaseUrl": "http://new-api:3000",
            "provider": "ali",
            "upstreamKey": "sk-ali-new-upstream-secret",
            "baseUrl": "",
        },
    )

    assert response.status_code == 200
    assert response.json()["ok"] is True
    assert calls == {
        "provider": "ali",
        "upstream_key": "sk-ali-new-upstream-secret",
        "base_url": "",
    }
    assert "https://dashscope-old.example.com" not in response.text

    config_response = client.get("/model-gateway/config")
    channels = config_response.json()["data"]["provisioner"]["providerChannels"]
    assert channels == [
        {
            "provider": "ali",
            "type": 0,
            "configured": True,
            "upstreamKeyPreview": "sk-a...cret",
            "baseUrl": "",
            "priority": 0,
            "settings": {},
        }
    ]


def test_custom_newapi_provider_channel_sync_does_not_save_when_newapi_update_fails(
    monkeypatch,
    tmp_path,
):
    _isolate_settings_db(monkeypatch, tmp_path)
    monkeypatch.setenv("NEWAPI_PROVISIONER_ENABLED", "true")

    class Admin:
        admin_user_id = 1
        admin_username = "root"
        token_created = False
        access_token = "admin-secret"

    monkeypatch.setattr(
        model_gateway,
        "get_provisioner_config",
        lambda _base_url=None, **_kwargs: type(
            "Cfg",
            (),
            {"admin_base_url": "http://new-api:3000"},
        )(),
    )
    monkeypatch.setattr(
        model_gateway, "ensure_admin_access_token", lambda _cfg: Admin()
    )
    monkeypatch.setattr(
        model_gateway,
        "update_provider_channel_credentials",
        lambda *_args, **_kwargs: {
            "ok": False,
            "httpStatus": 400,
            "newApiResponse": {"success": False, "message": "invalid key"},
            "sentPayload": {
                "mode": "single",
                "channel": {
                    "id": 7,
                    "name": "DC-ali",
                    "key": "sk-ali-new-upstream-secret",
                },
            },
            "channelId": 7,
        },
    )

    app = FastAPI()
    app.include_router(model_gateway.router)
    client = TestClient(app)

    response = client.post(
        "/model-gateway/custom/newapi/provider-channel/sync",
        json={
            "newApiBaseUrl": "http://new-api:3000",
            "provider": "ali",
            "upstreamKey": "sk-ali-new-upstream-secret",
        },
    )

    assert response.status_code == 200
    assert response.json()["ok"] is False
    assert response.json()["data"]["savedChannel"] is None
    assert "sk-ali-new-upstream-secret" not in response.text

    config_response = client.get("/model-gateway/config")
    assert config_response.json()["data"]["provisioner"]["providerChannels"] == []


def test_custom_newapi_channels_batch_uses_saved_provider_channel_config(
    monkeypatch,
    tmp_path,
):
    _isolate_settings_db(monkeypatch, tmp_path)
    monkeypatch.setenv("NEWAPI_PROVISIONER_ENABLED", "true")
    save_newapi_provider_channels(
        [
            {
                "provider": "ali",
                "upstreamKey": "sk-saved-upstream-secret",
                "baseUrl": "https://saved-dashscope.example.com",
            }
        ]
    )

    class Admin:
        admin_user_id = 1
        admin_username = "root"
        token_created = False
        access_token = "admin-secret"

    payloads: list[dict] = []

    monkeypatch.setattr(
        model_gateway,
        "get_provisioner_config",
        lambda _base_url=None, **_kwargs: type(
            "Cfg",
            (),
            {"admin_base_url": "http://new-api:3000"},
        )(),
    )
    monkeypatch.setattr(
        model_gateway, "ensure_admin_access_token", lambda _cfg: Admin()
    )

    def fake_upsert_channel(_cfg, _admin, payload):
        payloads.append(payload)
        return {
            "ok": True,
            "httpStatus": 200,
            "newApiResponse": {"success": True},
            "action": "create",
            "channelId": None,
        }

    monkeypatch.setattr(model_gateway, "upsert_channel", fake_upsert_channel)

    app = FastAPI()
    app.include_router(model_gateway.router)
    client = TestClient(app)

    response = client.post(
        "/model-gateway/custom/newapi/channels/batch",
        json={
            "channels": [
                {
                    "provider": "ali",
                    "modelMapping": {"DC-screenplay-normalizer-LLM": "qwen-plus"},
                }
            ]
        },
    )

    assert response.status_code == 200
    assert response.json()["ok"] is True
    assert payloads[0]["channel"]["key"] == "sk-saved-upstream-secret"
    assert payloads[0]["channel"]["base_url"] == "https://saved-dashscope.example.com"
    assert "sk-saved-upstream-secret" not in response.text


def test_custom_newapi_media_models_groups_by_provider_and_persists_mapping(
    monkeypatch,
    tmp_path,
):
    _isolate_settings_db(monkeypatch, tmp_path)
    monkeypatch.setenv("NEWAPI_PROVISIONER_ENABLED", "true")
    save_newapi_provider_channels(
        [
            {
                "provider": "newapi",
                "upstreamKey": "sk-newapi-upstream-secret",
                "baseUrl": "",
            },
            {
                "provider": "volcengine",
                "upstreamKey": "sk-volc-upstream-secret",
                "baseUrl": "https://ark.example.com",
            },
        ]
    )

    class Admin:
        admin_user_id = 1
        admin_username = "root"
        token_created = False
        access_token = "admin-secret"

    payloads: list[dict] = []

    monkeypatch.setattr(
        model_gateway,
        "get_provisioner_config",
        lambda _base_url=None, **_kwargs: type(
            "Cfg",
            (),
            {"admin_base_url": "http://new-api:3000"},
        )(),
    )
    monkeypatch.setattr(
        model_gateway, "ensure_admin_access_token", lambda _cfg: Admin()
    )

    def fake_upsert_channel(_cfg, _admin, payload):
        payloads.append(payload)
        return {
            "ok": True,
            "httpStatus": 200,
            "newApiResponse": {"success": True},
            "action": "update",
            "channelId": 3,
        }

    monkeypatch.setattr(model_gateway, "upsert_channel", fake_upsert_channel)

    app = FastAPI()
    app.include_router(model_gateway.router)
    client = TestClient(app)

    response = client.post(
        "/model-gateway/custom/newapi/media-models",
        json={
            "newApiBaseUrl": "http://new-api:3000",
            "models": {
                "LingShan-G2": {
                    "provider": "newapi",
                    "upstreamModel": "gpt-image-upstream",
                },
                "seedance-1.5-pro": {
                    "provider": "volcengine",
                    "upstreamModel": "doubao-seedance-1-5",
                },
                "seedance-2.0-fast": {
                    "provider": "volcengine",
                    "upstreamModel": "",
                },
                "index-tts-2": {
                    "provider": "volcengine",
                    "upstreamModel": "index-tts-2-upstream",
                },
                "LingShan-MU-11": {
                    "provider": "volcengine",
                    "upstreamModel": "lingshan-mu-upstream",
                },
            },
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["data"]["succeeded"] == 2
    assert len(payloads) == 2
    by_name = {payload["channel"]["name"]: payload["channel"] for payload in payloads}
    assert json.loads(by_name["DC-newapi"]["model_mapping"]) == {
        "LingShan-G2": "gpt-image-upstream",
    }
    assert json.loads(by_name["DC-volcengine"]["model_mapping"]) == {
        "seedance-1.5-pro": "doubao-seedance-1-5",
        "seedance-2.0-fast": "seedance-2.0-fast",
        "index-tts-2": "index-tts-2-upstream",
        "LingShan-MU-11": "lingshan-mu-upstream",
    }
    assert by_name["DC-newapi"]["key"] == "sk-newapi-upstream-secret"
    # [aipdd fork delta] volcengine/ark/doubao 的 baseUrl 入库时补齐本地直连所需的
    # /api/v3（拉上游模型清单和 embedding 自动发现要用完整 URL）；写网关的 payload
    # 里剥掉——type 45 适配器硬编码拼 /api/v3/...，保留会得到 /api/v3/api/v3/...。
    assert by_name["DC-volcengine"]["base_url"] == "https://ark.example.com"
    assert "sk-newapi-upstream-secret" not in response.text
    assert "sk-volc-upstream-secret" not in response.text

    config_response = client.get("/model-gateway/config")
    media_models = config_response.json()["data"]["provisioner"]["mediaModels"]
    expected_mappings = {
        "LingShan-G2": {
            "provider": "newapi",
            "upstreamModel": "gpt-image-upstream",
        },
        "seedance-1.5-pro": {
            "provider": "volcengine",
            "upstreamModel": "doubao-seedance-1-5",
        },
        "seedance-2.0-fast": {
            "provider": "volcengine",
            "upstreamModel": "",
        },
        "index-tts-2": {
            "provider": "volcengine",
            "upstreamModel": "index-tts-2-upstream",
        },
        "LingShan-MU-11": {
            "provider": "volcengine",
            "upstreamModel": "lingshan-mu-upstream",
        },
    }
    assert {
        model: {
            "provider": entry["provider"],
            "upstreamModel": entry["upstreamModel"],
        }
        for model, entry in media_models.items()
    } == expected_mappings
    assert media_models["LingShan-G2"]["mediaType"] == "image"
    assert media_models["seedance-1.5-pro"]["mediaType"] == "video"
    assert media_models["index-tts-2"]["mediaType"] == "audio"


def test_ce_media_model_catalog_uses_saved_custom_model_capabilities(
    monkeypatch, tmp_path
):
    _isolate_settings_db(monkeypatch, tmp_path)
    save_newapi_media_model_mappings(
        {
            "custom-video": {
                "provider": "volcengine",
                "upstreamModel": "doubao-custom-video",
                "mediaType": "video",
                "label": "Custom Video",
                "enabled": True,
                "sortOrder": 12,
                "config": {
                    "resolutionOptions": ["720p", "1080p"],
                    "ratioOptions": ["16:9", "9:16"],
                    "minDuration": 4,
                    "maxDuration": 10,
                    "supportedModes": ["text_to_video", "first_frame"],
                    "request": {
                        "endpoint": "video/generations",
                        "parameters": [],
                    },
                },
            },
            "disabled-image": {
                "provider": "openrouter",
                "upstreamModel": "disabled-image",
                "mediaType": "image",
                "enabled": False,
                "config": {},
            },
        }
    )

    catalog = get_ce_media_model_catalog("video")

    assert len(catalog) == 1
    assert catalog[0]["id"] == "custom-video"
    assert catalog[0]["apiModel"] == "newapi_custom-video"
    assert catalog[0]["label"] == "Custom Video"
    # [aipdd fork delta] 展示用渠道（画布模型选择器据此在模型旁做彩色渠道标注）；
    # providerId 仍是执行身份 newapi，channel 才是这条模型实际所属的渠道。
    assert catalog[0]["providerId"] == "newapi"
    assert catalog[0]["channel"] == "volcengine"
    assert catalog[0]["resolutionOptions"] == ["720p", "1080p"]
    assert catalog[0]["supportedModes"] == ["text_to_video", "first_frame"]
    assert get_ce_media_model_catalog("image") == []
    assert get_ce_media_model_catalog("video", provider="comfyui") == []


def test_official_media_model_catalog_uses_ce_export_shape():
    images = get_official_media_model_catalog("image")
    videos = get_official_media_model_catalog("video")

    assert len(images) == 8
    assert len(videos) == 11
    assert [entry["id"] for entry in videos[:2]] == [
        "wan3.0-video-prime",
        "wan3.0-video",
    ]
    wan = videos[0]
    assert wan["referenceFileMax"] == 1
    assert wan["referenceLinkMax"] == 1
    assert "pdf" in wan["referenceFileTypes"]
    seedance_25 = next(entry for entry in videos if entry["id"] == "seedance-2.5")
    assert seedance_25["apiModel"] == "newapi_seedance-2.5"
    assert seedance_25["resolutionOptions"] == ["480p", "720p", "1080p"]
    assert seedance_25["ratioOptions"][-1] == "auto"
    seedream = next(entry for entry in images if entry["id"] == "seedream-5.0-pro")
    assert seedream["gatewayModel"] == "seedream-5.0-pro"
    assert seedream["resolutionOptions"] == ["1k", "2k"]
    assert seedream["minPixels"] == 3686400
    seedance = next(entry for entry in videos if entry["id"] == "seedance-2.0-mini")
    assert seedance["apiModel"] == "newapi_seedance-2.0-mini"
    assert "video_edit" in seedance["supportedModes"]
    happyhorse_11 = next(entry for entry in videos if entry["id"] == "happyhorse-1.1")
    assert happyhorse_11["gatewayModel"] == "happyhorse-1.1"
    assert happyhorse_11["minDuration"] == 3
    assert happyhorse_11["maxDuration"] == 15
    assert happyhorse_11["ratioOptions"] == [
        "16:9",
        "9:16",
        "1:1",
        "4:3",
        "3:4",
        "21:9",
        "9:21",
        "5:4",
        "4:5",
    ]
    assert happyhorse_11["supportedModes"] == [
        "text_to_video",
        "first_frame",
        "image_to_video",
        "image_reference",
    ]
    assert happyhorse_11["supportsGenerateAudio"] is False
    assert happyhorse_11["referenceImageMax"] == 9
    assert happyhorse_11["referenceVideoMax"] == 0
    assert happyhorse_11["referenceAudioMax"] == 0
    minimax = videos[-1]
    assert minimax["id"] == "MiniMax-H3"
    assert minimax["gatewayModel"] == "MiniMax-H3"
    assert minimax["resolutionOptions"] == ["768p", "2k"]
    assert minimax["ratioOptions"] == [
        "21:9",
        "16:9",
        "4:3",
        "1:1",
        "3:4",
        "9:16",
    ]
    assert minimax["minDuration"] == 4
    assert minimax["maxDuration"] == 15
    assert minimax["supportedModes"] == [
        "text_to_video",
        "first_frame",
        "first_last_frame",
        "image_to_video",
        "image_reference",
        "all_reference",
    ]
    assert minimax["referenceImageMax"] == 9
    assert minimax["referenceVideoMax"] == 3
    assert minimax["referenceAudioMax"] == 3


def test_custom_media_model_accepts_arbitrary_image_and_video_models():
    specs, normalized = model_gateway._build_media_model_channel_specs(
        {
            "kling-custom": model_gateway.MediaModelConfigBody(
                provider="openrouter",
                upstreamModel="kling-v2",
                mediaType="video",
                label="Kling Custom",
                config={
                    "resolutionOptions": ["720p", "1080p"],
                    "supportedModes": ["text_to_video", "first_frame"],
                    "request": {
                        "endpoint": "video/generations",
                        "parameters": [],
                    },
                },
            )
        }
    )

    assert specs[0].model_mapping == {"kling-custom": "kling-v2"}
    assert normalized["kling-custom"]["mediaType"] == "video"
    assert normalized["kling-custom"]["label"] == "Kling Custom"
    assert normalized["kling-custom"]["config"]["resolutionOptions"] == [
        "720p",
        "1080p",
    ]


def test_custom_newapi_media_models_rejects_official_value_models(
    monkeypatch, tmp_path
):
    _isolate_settings_db(monkeypatch, tmp_path)
    monkeypatch.setenv("NEWAPI_PROVISIONER_ENABLED", "true")

    app = FastAPI()
    app.include_router(model_gateway.router)
    client = TestClient(app)

    response = client.post(
        "/model-gateway/custom/newapi/media-models",
        json={
            "models": {
                "seedance-2.0-value": {
                    "provider": "volcengine",
                    "upstreamModel": "seedance-2.0-value",
                }
            }
        },
    )

    assert response.status_code == 400
    assert "official-channel only" in response.text


def test_custom_newapi_embedding_model_writes_mapping_and_persists_dimension(
    monkeypatch,
    tmp_path,
):
    _isolate_settings_db(monkeypatch, tmp_path)
    monkeypatch.setenv("NEWAPI_PROVISIONER_ENABLED", "true")
    save_newapi_provider_channels(
        [
            {
                "provider": "newapi",
                "upstreamKey": "sk-newapi-upstream-secret",
                "baseUrl": "",
            }
        ]
    )

    class Admin:
        admin_user_id = 1
        admin_username = "root"
        token_created = False
        access_token = "admin-secret"

    payloads: list[dict] = []

    monkeypatch.setattr(
        model_gateway,
        "get_provisioner_config",
        lambda _base_url=None, **_kwargs: type(
            "Cfg",
            (),
            {"admin_base_url": "http://new-api:3000"},
        )(),
    )
    monkeypatch.setattr(
        model_gateway, "ensure_admin_access_token", lambda _cfg: Admin()
    )

    def fake_upsert_channel(_cfg, _admin, payload):
        payloads.append(payload)
        return {
            "ok": True,
            "httpStatus": 200,
            "newApiResponse": {"success": True},
            "action": "update",
            "channelId": 7,
        }

    monkeypatch.setattr(model_gateway, "upsert_channel", fake_upsert_channel)

    app = FastAPI()
    app.include_router(model_gateway.router)
    client = TestClient(app)

    response = client.post(
        "/model-gateway/custom/newapi/embedding-model",
        json={
            "newApiBaseUrl": "http://new-api:3000",
            "provider": "newapi",
            "upstreamModel": "text-embedding-3-large",
            "dimension": 1024,
            "batchSize": 36,
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert payloads[0]["channel"]["name"] == "DC-newapi"
    assert payloads[0]["channel"]["key"] == "sk-newapi-upstream-secret"
    assert json.loads(payloads[0]["channel"]["model_mapping"]) == {
        "DC-cognee-embedding": "text-embedding-3-large",
    }
    assert "dimension" not in payloads[0]["channel"]
    assert "1024" not in payloads[0]["channel"]["model_mapping"]
    assert "sk-newapi-upstream-secret" not in response.text

    config_response = client.get("/model-gateway/config")
    embedding = config_response.json()["data"]["provisioner"]["embeddingModel"]
    assert embedding == {
        "provider": "newapi",
        "upstreamModel": "text-embedding-3-large",
        "dimension": 1024,
        "batchSize": 36,
        "sendDimensions": True,
        "internalModel": "DC-cognee-embedding",
    }


def test_custom_newapi_embedding_model_accepts_positive_project_dimension():
    body = model_gateway.SaveEmbeddingModelBody.model_validate(
        {
            "provider": "newapi",
            "upstreamModel": "text-embedding-3-large",
            "dimension": 3072,
        }
    )

    _, normalized = model_gateway._build_embedding_model_channel_spec(body)

    assert normalized["dimension"] == 3072
    assert normalized["sendDimensions"] is True


def test_effective_cognee_embedding_prefers_saved_custom_config(monkeypatch, tmp_path):
    _isolate_settings_db(monkeypatch, tmp_path)
    set_model_gateway_mode(MODE_CUSTOM)
    monkeypatch.setenv("COGNEE_EMBEDDING_PROVIDER", "gemini")
    monkeypatch.setenv("COGNEE_EMBEDDING_MODEL", "gemini-embedding-001")
    monkeypatch.setenv("COGNEE_EMBEDDING_DIM", "768")

    save_newapi_embedding_model_config(
        provider="openai",
        upstream_model="text-embedding-3-large",
        dimension=3072,
    )

    effective = get_effective_cognee_embedding_config(llm_provider="gemini")

    assert effective.source == "database"
    assert effective.provider == "newapi"
    assert effective.model == "DC-cognee-embedding"
    assert effective.dimensions == "3072"
    assert effective.upstream_provider == "openai"
    assert effective.upstream_model == "text-embedding-3-large"


def test_effective_cognee_embedding_keeps_saved_batch_size(monkeypatch, tmp_path):
    _isolate_settings_db(monkeypatch, tmp_path)
    set_model_gateway_mode(MODE_CUSTOM)

    save_newapi_embedding_model_config(
        provider="ali",
        upstream_model="text-embedding-v3",
        dimension=1024,
        batch_size=10,
    )

    effective = get_effective_cognee_embedding_config(llm_provider="newapi")

    assert effective.source == "database"
    assert effective.provider == "newapi"
    assert effective.model == "DC-cognee-embedding"
    assert effective.dimensions == "1024"
    assert effective.batch_size == "10"


def test_ce_official_embedding_ignores_saved_custom_model(monkeypatch, tmp_path):
    _isolate_settings_db(monkeypatch, tmp_path)
    save_newapi_embedding_model_config(
        provider="openai",
        upstream_model="stale-custom-model",
        dimension=1024,
    )
    set_model_gateway_mode(MODE_OFFICIAL)
    monkeypatch.setenv("COGNEE_EMBEDDING_MODEL", "DC-cognee-embedding")
    monkeypatch.setenv("COGNEE_EMBEDDING_DIM", "1024")

    effective = get_effective_cognee_embedding_config()

    assert effective.source == "environment"
    assert effective.model == "DC-cognee-embedding"
    assert effective.dimensions == "1024"
    assert effective.upstream_model == ""


def test_cognee_apply_embedding_env_sets_saved_batch_size(monkeypatch, tmp_path):
    _isolate_settings_db(monkeypatch, tmp_path)
    monkeypatch.delenv("EMBEDDING_BATCH_SIZE", raising=False)

    save_custom_newapi_gateway(
        base_url="https://custom.example",
        api_key="sk-custom-secret",
        activate=True,
    )
    save_newapi_embedding_model_config(
        provider="ali",
        upstream_model="text-embedding-v3",
        dimension=1024,
        batch_size=10,
    )

    from novelvideo.cognee import config as cognee_config

    cognee_config._apply_embedding_env("newapi", "sk-custom-secret")

    assert os.environ["EMBEDDING_BATCH_SIZE"] == "10"


def test_custom_newapi_channels_batch_reports_partial_failure(monkeypatch, tmp_path):
    # [aipdd fork delta] WP-A: modelMapping 先落项目自有真源（按 provider 归属），
    # 再编译投影；故同一批里两个渠道必须分属不同 provider，否则它们的别名会
    # 按设计汇入同一个 provider 的真源并互相出现在对方投影里。
    _isolate_settings_db(monkeypatch, tmp_path)
    monkeypatch.setenv("NEWAPI_PROVISIONER_ENABLED", "true")

    class Admin:
        admin_user_id = 1
        admin_username = "root"
        token_created = False
        access_token = "admin-secret"

    monkeypatch.setattr(
        model_gateway,
        "get_provisioner_config",
        lambda _base_url=None, **_kwargs: type(
            "Cfg",
            (),
            {"admin_base_url": "http://new-api:3000"},
        )(),
    )
    monkeypatch.setattr(
        model_gateway, "ensure_admin_access_token", lambda _cfg: Admin()
    )

    def fake_upsert_channel(_cfg, _admin, payload):
        if "DC-staging-prop-planner-LLM" in payload["channel"]["models"]:
            return {
                "ok": False,
                "httpStatus": 400,
                "newApiResponse": {"success": False, "message": "bad model"},
                "action": "update",
                "channelId": 7,
            }
        return {
            "ok": True,
            "httpStatus": 200,
            "newApiResponse": {"success": True},
            "action": "create",
            "channelId": None,
        }

    monkeypatch.setattr(model_gateway, "upsert_channel", fake_upsert_channel)

    app = FastAPI()
    app.include_router(model_gateway.router)
    client = TestClient(app)

    response = client.post(
        "/model-gateway/custom/newapi/channels/batch",
        json={
            "channels": [
                {
                    "provider": "ali",
                    "name": "ok-channel",
                    "upstreamKey": "sk-upstream-one",
                    "modelMapping": {"DC-screenplay-normalizer-LLM": "qwen-plus"},
                },
                {
                    "provider": "deepseek",
                    "name": "bad-channel",
                    "upstreamKey": "sk-upstream-two",
                    "modelMapping": {"DC-staging-prop-planner-LLM": "qwen-plus"},
                },
            ],
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is False
    assert body["data"]["succeeded"] == 1
    assert body["data"]["failed"] == 1
    assert body["data"]["results"][0]["ok"] is True
    assert body["data"]["results"][1]["ok"] is False
    assert body["data"]["results"][1]["httpStatus"] == 400
    # 真源落在项目侧，与单个渠道投影成败无关。
    assert body["data"]["aliasMappingSource"] == "project"
    assert body["data"]["aliasMappings"] == {
        "DC-screenplay-normalizer-LLM": {
            "provider": "ali",
            "target": "qwen-plus",
        },
        "DC-staging-prop-planner-LLM": {
            "provider": "deepseek",
            "target": "qwen-plus",
        },
    }


def test_media_relay_config_route_persists_and_masks_oss_keys(monkeypatch, tmp_path):
    _isolate_settings_db(monkeypatch, tmp_path)
    monkeypatch.setenv("NEWAPI_PROVISIONER_ENABLED", "true")
    monkeypatch.setattr(model_gateway.app_config, "MEDIA_RELAY_PROVIDER", "aliyun_oss")
    monkeypatch.setattr(model_gateway.app_config, "MEDIA_RELAY_TTL_SECONDS", 1800)
    monkeypatch.setattr(model_gateway.app_config, "OSS_RELAY_ENDPOINT", "env.endpoint")
    monkeypatch.setattr(model_gateway.app_config, "OSS_RELAY_BUCKET", "env-bucket")
    monkeypatch.setattr(model_gateway.app_config, "OSS_RELAY_AK", "env-ak-secret")
    monkeypatch.setattr(model_gateway.app_config, "OSS_RELAY_SK", "env-sk-secret")

    app = FastAPI()
    app.include_router(model_gateway.router)
    client = TestClient(app)

    response = client.post(
        "/model-gateway/media-relay/config",
        json={
            "provider": "aliyun_oss",
            "ttlSeconds": 900,
            "endpoint": "oss-cn-shanghai.aliyuncs.com",
            "bucket": "user-relay",
            "accessKeyId": "LTAI-user-secret",
            "accessKeySecret": "SK-user-secret",
        },
    )

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["source"] == "database"
    assert data["ttlSeconds"] == 900
    assert data["endpoint"] == "oss-cn-shanghai.aliyuncs.com"
    assert data["bucket"] == "user-relay"
    assert data["accessKeyIdPreview"] == "LTAI...cret"
    assert data["accessKeySecretPreview"] == "SK-u...cret"
    assert "LTAI-user-secret" not in response.text
    assert "SK-user-secret" not in response.text


def test_media_relay_config_route_persists_and_masks_cloudinary_keys(
    monkeypatch, tmp_path
):
    _isolate_settings_db(monkeypatch, tmp_path)
    monkeypatch.setenv("NEWAPI_PROVISIONER_ENABLED", "true")
    monkeypatch.setattr(model_gateway.app_config, "MEDIA_RELAY_PROVIDER", "aliyun_oss")
    monkeypatch.setattr(model_gateway.app_config, "MEDIA_RELAY_TTL_SECONDS", 1800)
    monkeypatch.setattr(model_gateway.app_config, "OSS_RELAY_ENDPOINT", "env.endpoint")
    monkeypatch.setattr(model_gateway.app_config, "OSS_RELAY_BUCKET", "env-bucket")
    monkeypatch.setattr(model_gateway.app_config, "OSS_RELAY_AK", "env-ak-secret")
    monkeypatch.setattr(model_gateway.app_config, "OSS_RELAY_SK", "env-sk-secret")
    monkeypatch.setattr(model_gateway.app_config, "CLOUDINARY_RELAY_CLOUD_NAME", "")
    monkeypatch.setattr(model_gateway.app_config, "CLOUDINARY_RELAY_API_KEY", "")
    monkeypatch.setattr(model_gateway.app_config, "CLOUDINARY_RELAY_API_SECRET", "")
    monkeypatch.setattr(model_gateway.app_config, "CLOUDINARY_RELAY_FOLDER", "relay")

    app = FastAPI()
    app.include_router(model_gateway.router)
    client = TestClient(app)

    response = client.post(
        "/model-gateway/media-relay/config",
        json={
            "provider": "cloudinary",
            "ttlSeconds": 900,
            "cloudName": "demo-cloud",
            "apiKey": "cloudinary-api-key-secret",
            "apiSecret": "cloudinary-api-secret",
            "apiFolder": "dramaclaw-relay",
        },
    )

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["source"] == "database"
    assert data["provider"] == "cloudinary"
    assert data["ttlSeconds"] == 900
    assert data["cloudName"] == "demo-cloud"
    assert data["apiFolder"] == "dramaclaw-relay"
    assert data["cloudinaryApiKeyPreview"] == "clou...cret"
    assert data["cloudinaryApiSecretPreview"] == "clou...cret"
    assert data["configured"] is True
    assert "cloudinary-api-key-secret" not in response.text
    assert "cloudinary-api-secret" not in response.text


def test_media_relay_config_route_supports_partial_credential_updates(
    monkeypatch, tmp_path
):
    _isolate_settings_db(monkeypatch, tmp_path)
    monkeypatch.setenv("NEWAPI_PROVISIONER_ENABLED", "true")
    monkeypatch.setattr(model_gateway.app_config, "MEDIA_RELAY_PROVIDER", "aliyun_oss")
    monkeypatch.setattr(model_gateway.app_config, "MEDIA_RELAY_TTL_SECONDS", 1800)
    monkeypatch.setattr(model_gateway.app_config, "OSS_RELAY_ENDPOINT", "")
    monkeypatch.setattr(model_gateway.app_config, "OSS_RELAY_BUCKET", "")
    monkeypatch.setattr(model_gateway.app_config, "OSS_RELAY_AK", "")
    monkeypatch.setattr(model_gateway.app_config, "OSS_RELAY_SK", "")
    monkeypatch.setattr(model_gateway.app_config, "CLOUDINARY_RELAY_CLOUD_NAME", "")
    monkeypatch.setattr(model_gateway.app_config, "CLOUDINARY_RELAY_API_KEY", "")
    monkeypatch.setattr(model_gateway.app_config, "CLOUDINARY_RELAY_API_SECRET", "")
    monkeypatch.setattr(model_gateway.app_config, "CLOUDINARY_RELAY_FOLDER", "")

    app = FastAPI()
    app.include_router(model_gateway.router)
    client = TestClient(app)

    initial = client.post(
        "/model-gateway/media-relay/config",
        json={
            "provider": "aliyun_oss",
            "ttlSeconds": 900,
            "endpoint": "oss-cn-shanghai.aliyuncs.com",
            "bucket": "user-relay",
            "accessKeyId": "LTAI-old-secret",
            "accessKeySecret": "SK-old-secret",
        },
    )
    assert initial.status_code == 200

    updated = client.post(
        "/model-gateway/media-relay/config",
        json={
            "provider": "aliyun_oss",
            "ttlSeconds": 1200,
            "endpoint": "oss-cn-beijing.aliyuncs.com",
            "bucket": "user-relay",
            "accessKeyId": "LTAI-new-secret",
            "accessKeySecret": "",
        },
    )

    assert updated.status_code == 200
    data = updated.json()["data"]
    assert data["ttlSeconds"] == 1200
    assert data["endpoint"] == "oss-cn-beijing.aliyuncs.com"
    assert data["accessKeyIdPreview"] == "LTAI...cret"
    assert data["accessKeySecretPreview"] == "SK-o...cret"
    assert data["configured"] is True
    assert "LTAI-new-secret" not in updated.text
    assert "SK-old-secret" not in updated.text


def test_media_relay_config_route_preserves_inactive_provider_credentials(
    monkeypatch, tmp_path
):
    _isolate_settings_db(monkeypatch, tmp_path)
    monkeypatch.setenv("NEWAPI_PROVISIONER_ENABLED", "true")
    monkeypatch.setattr(model_gateway.app_config, "MEDIA_RELAY_PROVIDER", "aliyun_oss")
    monkeypatch.setattr(model_gateway.app_config, "MEDIA_RELAY_TTL_SECONDS", 1800)
    monkeypatch.setattr(model_gateway.app_config, "OSS_RELAY_ENDPOINT", "")
    monkeypatch.setattr(model_gateway.app_config, "OSS_RELAY_BUCKET", "")
    monkeypatch.setattr(model_gateway.app_config, "OSS_RELAY_AK", "")
    monkeypatch.setattr(model_gateway.app_config, "OSS_RELAY_SK", "")
    monkeypatch.setattr(model_gateway.app_config, "CLOUDINARY_RELAY_CLOUD_NAME", "")
    monkeypatch.setattr(model_gateway.app_config, "CLOUDINARY_RELAY_API_KEY", "")
    monkeypatch.setattr(model_gateway.app_config, "CLOUDINARY_RELAY_API_SECRET", "")
    monkeypatch.setattr(model_gateway.app_config, "CLOUDINARY_RELAY_FOLDER", "")

    app = FastAPI()
    app.include_router(model_gateway.router)
    client = TestClient(app)

    oss = client.post(
        "/model-gateway/media-relay/config",
        json={
            "provider": "aliyun_oss",
            "endpoint": "oss.example.com",
            "bucket": "oss-bucket",
            "accessKeyId": "oss-access-key",
            "accessKeySecret": "oss-secret-key",
        },
    )
    assert oss.status_code == 200

    cloudinary = client.post(
        "/model-gateway/media-relay/config",
        json={
            "provider": "cloudinary",
            "cloudName": "demo-cloud",
            "apiKey": "cloudinary-api-key",
            "apiSecret": "cloudinary-api-secret",
        },
    )
    assert cloudinary.status_code == 200
    cloudinary_data = cloudinary.json()["data"]
    assert cloudinary_data["accessKeyIdPreview"] == "oss-...-key"
    assert cloudinary_data["accessKeySecretPreview"] == "oss-...-key"

    cloudinary_partial = client.post(
        "/model-gateway/media-relay/config",
        json={
            "provider": "cloudinary",
            "cloudName": "demo-cloud",
            "apiKey": "",
            "apiSecret": "cloudinary-new-secret",
        },
    )
    assert cloudinary_partial.status_code == 200
    cloudinary_partial_data = cloudinary_partial.json()["data"]
    assert cloudinary_partial_data["cloudinaryApiKeyPreview"] == "clou...-key"
    assert cloudinary_partial_data["cloudinaryApiSecretPreview"] == "clou...cret"

    switched_back = client.post(
        "/model-gateway/media-relay/config",
        json={
            "provider": "aliyun_oss",
            "endpoint": "oss.example.com",
            "bucket": "oss-bucket",
        },
    )
    assert switched_back.status_code == 200
    switched_data = switched_back.json()["data"]
    assert switched_data["configured"] is True
    assert switched_data["cloudinaryApiKeyPreview"] == "clou...-key"
    assert switched_data["cloudinaryApiSecretPreview"] == "clou...cret"


def test_media_relay_config_route_persists_and_masks_aws_s3_keys(
    monkeypatch, tmp_path
):
    # [aipdd fork delta] AWS S3 档：与阿里云 OSS 并行，保存后回写 CE 设置库，
    # 完整 AK/SK 不下发（只回脱敏 preview）。
    _isolate_settings_db(monkeypatch, tmp_path)
    monkeypatch.setenv("NEWAPI_PROVISIONER_ENABLED", "true")
    monkeypatch.setattr(model_gateway.app_config, "MEDIA_RELAY_PROVIDER", "local")
    monkeypatch.setattr(model_gateway.app_config, "MEDIA_RELAY_TTL_SECONDS", 1800)
    for name in (
        "OSS_RELAY_ENDPOINT",
        "OSS_RELAY_BUCKET",
        "OSS_RELAY_AK",
        "OSS_RELAY_SK",
        "AWS_S3_REGION",
        "AWS_S3_BUCKET",
        "AWS_S3_ACCESS_KEY_ID",
        "AWS_S3_SECRET_ACCESS_KEY",
        "AWS_S3_ENDPOINT_URL",
        "AWS_S3_SESSION_TOKEN",
    ):
        monkeypatch.setattr(model_gateway.app_config, name, "")

    app = FastAPI()
    app.include_router(model_gateway.router)
    client = TestClient(app)

    response = client.post(
        "/model-gateway/media-relay/config",
        json={
            "provider": "aws_s3",
            "ttlSeconds": 900,
            "s3Region": "us-east-1",
            "s3Bucket": "user-relay",
            "s3AccessKeyId": "AKIAuser-secret",
            "s3AccessKeySecret": "SKuser-secret",
        },
    )

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["source"] == "database"
    assert data["provider"] == "aws_s3"
    assert data["ttlSeconds"] == 900
    assert data["s3Region"] == "us-east-1"
    assert data["s3Bucket"] == "user-relay"
    assert data["awsConfigured"] is True
    assert data["configured"] is True
    assert data["s3AccessKeyIdPreview"]
    assert "AKIAuser-secret" not in response.text
    assert "SKuser-secret" not in response.text


def test_media_relay_config_route_rejects_incomplete_aws_s3(monkeypatch, tmp_path):
    # [aipdd fork delta] AWS S3 必填 region + bucket + AK + SK，缺字段当场 400。
    _isolate_settings_db(monkeypatch, tmp_path)
    monkeypatch.setenv("NEWAPI_PROVISIONER_ENABLED", "true")
    monkeypatch.setattr(model_gateway.app_config, "MEDIA_RELAY_PROVIDER", "local")
    monkeypatch.setattr(model_gateway.app_config, "MEDIA_RELAY_TTL_SECONDS", 1800)
    for name in (
        "OSS_RELAY_ENDPOINT",
        "OSS_RELAY_BUCKET",
        "OSS_RELAY_AK",
        "OSS_RELAY_SK",
        "AWS_S3_REGION",
        "AWS_S3_BUCKET",
        "AWS_S3_ACCESS_KEY_ID",
        "AWS_S3_SECRET_ACCESS_KEY",
        "AWS_S3_ENDPOINT_URL",
        "AWS_S3_SESSION_TOKEN",
    ):
        monkeypatch.setattr(model_gateway.app_config, name, "")

    app = FastAPI()
    app.include_router(model_gateway.router)
    client = TestClient(app)

    response = client.post(
        "/model-gateway/media-relay/config",
        json={
            "provider": "aws_s3",
            "ttlSeconds": 900,
            "s3Region": "us-east-1",
        },
    )

    assert response.status_code == 400
    detail = response.json()["detail"]
    assert "bucket" in detail
    assert "accessKeyId" in detail
    assert "accessKeySecret" in detail


def test_media_relay_status_prefers_database_config(monkeypatch, tmp_path):
    _isolate_settings_db(monkeypatch, tmp_path)
    save_media_relay_config(
        provider="aliyun_oss",
        ttl_seconds=600,
        endpoint="db.endpoint",
        bucket="db-bucket",
        access_key_id="db-ak-secret",
        access_key_secret="db-sk-secret",
    )

    status = model_gateway._media_relay_status()

    assert status["source"] == "database"
    assert status["ttlSeconds"] == 600
    assert status["endpoint"] == "db.endpoint"
    assert status["bucket"] == "db-bucket"
    assert status["configured"] is True


def test_ee_media_relay_ignores_ce_database_config(monkeypatch, tmp_path):
    _isolate_settings_db(monkeypatch, tmp_path)
    save_media_relay_config(
        provider="aliyun_oss",
        ttl_seconds=600,
        endpoint="stale-ce.endpoint",
        bucket="stale-ce-bucket",
        access_key_id="stale-ce-ak",
        access_key_secret="stale-ce-sk",
    )
    monkeypatch.setenv("ST_EDITION", "ee")
    monkeypatch.setenv("ST_CONTROL_PLANE_DSN", "postgresql://control-plane")
    # [aipdd fork delta req44] 必须显式把档位也设成 aliyun_oss：默认值是 local，而
    # local 从 req44 起不再视为「天然已配置」（它要 AIPDD 中转凭据），那样这条用例
    # 只是在测 local，而不是测「EE 忽略 CE 库、只看 env 的 OSS 四件套」。
    monkeypatch.setattr(config, "MEDIA_RELAY_PROVIDER", "aliyun_oss")
    monkeypatch.setattr(config, "OSS_RELAY_ENDPOINT", "ee.endpoint")
    monkeypatch.setattr(config, "OSS_RELAY_BUCKET", "ee-bucket")
    monkeypatch.setattr(config, "OSS_RELAY_AK", "ee-ak")
    monkeypatch.setattr(config, "OSS_RELAY_SK", "ee-sk")

    status = model_gateway._media_relay_status()

    assert status["source"] == "environment"
    assert status["endpoint"] == "ee.endpoint"
    assert status["bucket"] == "ee-bucket"
    assert status["configured"] is True


def test_media_relay_ce_empty_db_does_not_fall_back_to_env(monkeypatch, tmp_path):
    # [aipdd fork delta] CE 模式设置库该功能为空时，密钥只认 DB：即便 env 配了
    # OSS_RELAY_AK/SK 也不回填，configured 侧为空、source="unset"（避免密钥 env/DB 双真源）。
    _isolate_settings_db(monkeypatch, tmp_path)
    monkeypatch.setenv("MEDIA_RELAY_PROVIDER", "aliyun_oss")
    monkeypatch.setenv("OSS_RELAY_ENDPOINT", "env.endpoint")
    monkeypatch.setenv("OSS_RELAY_BUCKET", "env-bucket")
    monkeypatch.setenv("OSS_RELAY_AK", "env-ak")
    monkeypatch.setenv("OSS_RELAY_SK", "env-sk")

    resolved = get_effective_media_relay_config()

    assert resolved.source == "unset"
    assert resolved.access_key_id == ""
    assert resolved.access_key_secret == ""
    assert resolved.provider == ""
    assert resolved.endpoint == ""


# ---- [aipdd fork delta] 火山 AI MediaKit 视频高清配置读写 ----


def _clear_mediakit_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in ("MEDIAKIT_API_KEY", "MEDIAKIT_BASE_URL", "VIDEO_ENHANCE_BACKEND"):
        monkeypatch.delenv(key, raising=False)


def test_mediakit_status_prefers_database_config(monkeypatch, tmp_path):
    _isolate_settings_db(monkeypatch, tmp_path)
    _clear_mediakit_env(monkeypatch)
    save_mediakit_config(
        api_key="mk-db-secret-key",
        base_url="https://mediakit.custom.example/api/v1/",
        backend="mediakit",
    )

    status = build_mediakit_status()

    assert status["source"] == "database"
    assert status["configured"] is True
    assert status["baseUrl"] == "https://mediakit.custom.example/api/v1"
    assert status["backend"] == "mediakit"
    assert "mk-db-secret-key" not in str(status)
    assert status["apiKeyPreview"].endswith("-key")


def test_mediakit_ignores_env_api_key_but_keeps_ops_backend_env(
    monkeypatch, tmp_path
):
    # [aipdd fork delta] MediaKit 凭证只认设置数据库：只设 env MEDIAKIT_API_KEY
    # 不再算已配置（避免密钥 env/DB 双真源）；仅运维级 VIDEO_ENHANCE_BACKEND=ffmpeg
    # 作为非密钥开关仍被尊重。
    _isolate_settings_db(monkeypatch, tmp_path)
    _clear_mediakit_env(monkeypatch)
    monkeypatch.setenv("MEDIAKIT_API_KEY", "mk-env-secret")
    monkeypatch.setenv("VIDEO_ENHANCE_BACKEND", "ffmpeg")

    status = build_mediakit_status()

    assert status["source"] == "unset"
    assert status["configured"] is False
    assert status["backend"] == "ffmpeg"
    assert status["baseUrl"] == "https://mediakit.cn-beijing.volces.com/api/v1"


def test_mediakit_default_when_unconfigured(monkeypatch, tmp_path):
    _isolate_settings_db(monkeypatch, tmp_path)
    _clear_mediakit_env(monkeypatch)

    status = build_mediakit_status()

    assert status["configured"] is False
    assert status["backend"] == "auto"


def test_video_enhance_config_route_saves_and_preserves_secret(monkeypatch, tmp_path):
    _isolate_settings_db(monkeypatch, tmp_path)
    _clear_mediakit_env(monkeypatch)

    app = FastAPI()
    app.include_router(model_gateway.router)
    client = TestClient(app)

    initial = client.post(
        "/model-gateway/video-enhance/config",
        json={"apiKey": "mk-route-secret", "backend": "auto"},
    )
    assert initial.status_code == 200
    data = initial.json()["data"]
    assert data["configured"] is True
    assert "mk-route-secret" not in initial.text

    # 提交空 apiKey 不覆盖既有密钥，只更新 backend。
    updated = client.post(
        "/model-gateway/video-enhance/config",
        json={"apiKey": "", "backend": "ffmpeg", "baseUrl": "https://mk.example/api/v1"},
    )
    assert updated.status_code == 200
    updated_data = updated.json()["data"]
    assert updated_data["configured"] is True
    assert updated_data["backend"] == "ffmpeg"
    assert updated_data["baseUrl"] == "https://mk.example/api/v1"

    # clear=true：显式清除 Key / Base URL（渠道目录「移除」动作），configured 回落 false。
    cleared = client.post(
        "/model-gateway/video-enhance/config",
        json={"clear": True},
    )
    assert cleared.status_code == 200
    cleared_data = cleared.json()["data"]
    assert cleared_data["configured"] is False
    assert cleared_data["apiKeyPreview"] == ""
    assert cleared_data["baseUrl"] == "https://mediakit.cn-beijing.volces.com/api/v1"

    rejected = client.post(
        "/model-gateway/video-enhance/config",
        json={"backend": "bogus"},
    )
    assert rejected.status_code == 400


def test_douyin_cookie_status_exposes_names_not_values(monkeypatch, tmp_path):
    """[aipdd fork delta] Cookie 是完整登录态：状态只回名/条数/长度，绝不回值。"""
    from novelvideo.freezone.douyin_web_api import normalize_cookie
    from novelvideo.model_gateway_settings import (
        build_douyin_cookie_status,
        save_douyin_web_cookie,
    )

    _isolate_settings_db(monkeypatch, tmp_path)

    save_douyin_web_cookie(
        cookie="Cookie: sessionid=SESSION-do-not-leak; ttwid=TTWID-do-not-leak"
    )

    status = build_douyin_cookie_status()
    assert status["configured"] is True
    assert status["cookieNames"] == ["sessionid", "ttwid"]
    assert status["cookieCount"] == 2
    assert status["length"] == len(
        normalize_cookie("sessionid=SESSION-do-not-leak; ttwid=TTWID-do-not-leak")
    )
    blob = str(status)
    assert "SESSION-do-not-leak" not in blob
    assert "TTWID-do-not-leak" not in blob


def test_douyin_cookie_config_route_normalizes_and_never_echoes_secret(
    monkeypatch, tmp_path
):
    """[aipdd fork delta] 保存 / 状态 / 清除三段契约：入库归一、回显脱敏、空串与 clear 同为清除。"""
    from novelvideo.model_gateway_settings import (
        build_douyin_cookie_status,
        get_douyin_web_cookie_setting,
    )

    _isolate_settings_db(monkeypatch, tmp_path)

    app = FastAPI()
    app.include_router(model_gateway.router)
    client = TestClient(app)

    saved = client.post(
        "/model-gateway/douyin-cookie/config",
        json={
            "cookie": (
                '[{"name":"sessionid","value":"SESSION-do-not-leak"},'
                '{"name":"ttwid","value":"TTWID-1"}]'
            )
        },
    )
    assert saved.status_code == 200
    data = saved.json()["data"]
    assert data["configured"] is True
    assert data["cookieCount"] == 2
    assert data["cookieNames"] == ["sessionid", "ttwid"]
    # 响应体里不得出现任何 Cookie 值（前端只拿得到字段名）。
    assert "SESSION-do-not-leak" not in saved.text
    # 库里存的是归一后的请求头格式，不是用户粘贴的 JSON 数组原文。
    assert get_douyin_web_cookie_setting() == "sessionid=SESSION-do-not-leak; ttwid=TTWID-1"

    # 状态接口（设置页读取路径）同样只回脱敏线索。
    status = client.get("/model-gateway/config").json()["data"]["douyinCookie"]
    assert status["configured"] is True
    assert status["cookieNames"] == ["sessionid", "ttwid"]
    assert "SESSION-do-not-leak" not in str(status)

    cleared = client.post(
        "/model-gateway/douyin-cookie/config",
        json={"clear": True},
    )
    assert cleared.status_code == 200
    cleared_data = cleared.json()["data"]
    assert cleared_data["configured"] is False
    assert cleared_data["cookieCount"] == 0
    assert get_douyin_web_cookie_setting() == ""

    # 重新保存后空串与 clear 同义（都是清除）。
    client.post("/model-gateway/douyin-cookie/config", json={"cookie": "ttwid=2"})
    empty = client.post("/model-gateway/douyin-cookie/config", json={"cookie": ""})
    assert empty.status_code == 200
    assert empty.json()["data"]["configured"] is False
    assert build_douyin_cookie_status()["cookieNames"] == []


# ------------------------------------------------------------
# [aipdd fork delta req45] 设置页「从 Chrome 抓取」按钮的路由契约
# ------------------------------------------------------------


def _capture_client(monkeypatch, tmp_path, jar):
    """搭只含 model-gateway 路由的 app，把 CDP 取 jar 换成固定数据（零浏览器）。"""

    _isolate_settings_db(monkeypatch, tmp_path)
    monkeypatch.setenv("ST_CHROME_CDP_URL", "http://127.0.0.1:9222")

    async def _jar_loader(_cdp_url):
        if isinstance(jar, Exception):
            raise jar
        return jar

    monkeypatch.setattr(
        "novelvideo.freezone.chrome_cdp.fetch_douyin_cookie_jar", _jar_loader
    )
    app = FastAPI()
    app.include_router(model_gateway.router)
    return TestClient(app)


def test_douyin_cookie_capture_route_records_host_chrome(monkeypatch, tmp_path):
    """抓取成功：落库 + 回脱敏状态，响应体里不得出现 Cookie 原文。"""

    from novelvideo.model_gateway_settings import get_douyin_web_cookie_setting

    client = _capture_client(
        monkeypatch,
        tmp_path,
        [
            {"name": "sessionid", "value": "SESSION-do-not-leak", "domain": ".douyin.com"},
            {"name": "ttwid", "value": "TTWID-do-not-leak", "domain": ".douyin.com"},
            {"name": "master", "value": "MAILBOX-do-not-leak", "domain": ".corp-mail.com"},
        ],
    )

    resp = client.post("/model-gateway/douyin-cookie/capture")
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["configured"] is True
    assert data["captured"] is True
    assert data["cookieNames"] == ["sessionid", "ttwid"]
    for secret in ("SESSION-do-not-leak", "TTWID-do-not-leak", "MAILBOX-do-not-leak"):
        assert secret not in resp.text
    # 无关站点的凭据不得进设置库（CDP 的 jar 是整个浏览器的）。
    assert "corp-mail" not in get_douyin_web_cookie_setting()
    assert "MAILBOX-do-not-leak" not in get_douyin_web_cookie_setting()


def test_douyin_cookie_capture_route_refuses_anonymous_only_jar(monkeypatch, tmp_path):
    """没有登录态就不写（写了反而让 HTTP 主链路发出去被静默拒），并给出路。"""

    from novelvideo.model_gateway_settings import get_douyin_web_cookie_setting

    client = _capture_client(
        monkeypatch,
        tmp_path,
        [{"name": "ttwid", "value": "1", "domain": ".douyin.com"}],
    )

    resp = client.post("/model-gateway/douyin-cookie/capture")
    assert resp.status_code == 400
    assert "登录" in resp.json()["detail"]
    assert get_douyin_web_cookie_setting() == ""


def test_douyin_cookie_capture_route_turns_cdp_failure_into_reason(monkeypatch, tmp_path):
    """Chrome 没开不是提取失败：400 + 能照着做的指引（调试端口 / 环境安装页）。"""

    from novelvideo.freezone.chrome_cdp import CdpUnavailable
    from novelvideo.model_gateway_settings import get_douyin_web_cookie_setting

    client = _capture_client(monkeypatch, tmp_path, CdpUnavailable("connection refused"))

    resp = client.post("/model-gateway/douyin-cookie/capture")
    assert resp.status_code == 400
    detail = resp.json()["detail"]
    assert "调试端口" in detail
    assert "connection refused" in detail
    assert get_douyin_web_cookie_setting() == ""


def test_upstream_models_snapshot_roundtrip_and_merge(monkeypatch, tmp_path):
    """[aipdd fork delta] 上游清单快照：按渠道去重排序落库、多渠合并、覆盖更新。

    快照是直通注册与广场展示的本地数据源（零远程）；空渠道 / 空清单是 no-op。
    """
    _isolate_settings_db(monkeypatch, tmp_path)

    save_upstream_models_snapshot(
        "aipdd",
        ["glm-4.6", " glm-4.6 ", "ap-doubao-seed-2.1-turbo"],
        base_url="https://api.aipdd.work/v1/",
    )
    save_upstream_models_snapshot("volcengine", ["doubao-seedance-2-0-260128"])

    assert get_upstream_models_snapshot() == {
        "aipdd": ["ap-doubao-seed-2.1-turbo", "glm-4.6"],
        "volcengine": ["doubao-seedance-2-0-260128"],
    }

    # 同渠道再写：覆盖而不是累加（快照 = 最近一次成功拉取的清单）。
    save_upstream_models_snapshot("aipdd", ["glm-5.3"])
    assert get_upstream_models_snapshot()["aipdd"] == ["glm-5.3"]

    # 空渠道 / 空清单是 no-op，不产生空条目。
    save_upstream_models_snapshot("", ["x"])
    save_upstream_models_snapshot("openai", [])
    assert "openai" not in get_upstream_models_snapshot()


def test_prop_reference_normalizes_channel_name_provider_to_gateway(
    monkeypatch,
    tmp_path,
):
    """[aipdd fork delta] 道具参考图 provider 归一：渠道名（aipdd/volcengine）不在
    直连分派集合里，必须归一到 LocalNewAPI，不得落到 Google 直连分支。"""
    _isolate_settings_db(monkeypatch, tmp_path)
    from novelvideo.generators import prop_generation

    assert prop_generation._prop_image_provider("aipdd") == "newapi"
    assert prop_generation._prop_image_provider("volcengine") == "newapi"
    assert prop_generation._prop_image_provider("comfyui") == "newapi"
    assert prop_generation._prop_image_provider("newapi") == "newapi"
    assert prop_generation._prop_image_provider("google") == "google"
    assert prop_generation._prop_image_provider("openai") == "openai"
    assert prop_generation._prop_image_provider("openrouter") == "openrouter"


def test_prop_reference_routes_channel_bound_model_through_channel_credentials(
    monkeypatch,
    tmp_path,
):
    """[aipdd fork delta] 道具参考图端到端路由回归：mediaModels 选中项把渠道名
    （aipdd）当 provider 传入时，必须走渠道自带 base+key（与 scene_ref 同一惯例）。
    修复前此路径落到 _generate_via_google，用网关 key 直连 Google 报 400 API key
    not valid，task 层表现为「图像 API 未返回有效图像」。"""
    import base64

    import httpx

    _isolate_settings_db(monkeypatch, tmp_path)
    save_newapi_provider_channels(
        [
            {
                "provider": "aipdd",
                "type": 0,
                "upstreamKey": "sk-aipdd-upstream",
                "baseUrl": "https://api.aipdd.work/v1",
            }
        ]
    )
    save_newapi_media_model_mappings(
        {
            "ap-image-2.0": {
                "provider": "aipdd",
                "mediaType": "image",
                "enabled": True,
                "sortOrder": 0,
            }
        }
    )

    from novelvideo.generators import prop_generation

    posted: dict[str, object] = {}

    class FakeResponse:
        status_code = 200
        headers = {"content-type": "application/json"}

        def raise_for_status(self) -> None:
            return None

        def json(self):
            return {"data": [{"b64_json": base64.b64encode(b"prop-ref").decode()}]}

    class FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def post(self, url, *, headers, json):
            posted["url"] = url
            posted["headers"] = headers
            return FakeResponse()

    monkeypatch.setattr(httpx, "AsyncClient", FakeAsyncClient)

    output_path = tmp_path / "assets" / "props" / "玉佩" / "reference_3view.png"
    result = asyncio.run(
        prop_generation.generate_prop_reference(
            visual_prompt="青绿色玉佩，边缘有金色纹路",
            output_path=str(output_path),
            model="ap-image-2.0",
        )
    )

    assert result == str(output_path)
    assert output_path.read_bytes() == b"prop-ref"
    assert posted["url"] == "https://api.aipdd.work/v1/images/generations"
    assert posted["headers"]["Authorization"] == "Bearer sk-aipdd-upstream"


def test_vectcut_draft_dir_normalize_and_roundtrip(monkeypatch, tmp_path) -> None:
    """剪映草稿目录（req15）：绝对路径校验 + 空串取消配置 + SQLite 往返。"""
    monkeypatch.setattr(
        model_gateway_settings, "_settings_db_path", lambda: tmp_path / "settings.db"
    )

    assert (
        model_gateway_settings.save_vectcut_draft_dir("  /drafts/out  ")
        == "/drafts/out"
    )
    assert model_gateway_settings.get_vectcut_draft_dir() == "/drafts/out"
    # Windows 盘符同样按绝对路径接受（容器内也会配 /drafts 这类 POSIX 路径）。
    assert model_gateway_settings.save_vectcut_draft_dir("D:\\drafts") == "D:\\drafts"
    # 空串 = 取消配置（回 VectCut 部署默认）。
    assert model_gateway_settings.save_vectcut_draft_dir("") == ""
    assert model_gateway_settings.get_vectcut_draft_dir() == ""

    for bad in ("relative/path", "/drafts/../etc", "x" * 513):
        with pytest.raises(ValueError):
            model_gateway_settings.save_vectcut_draft_dir(bad)


# ---- [aipdd fork delta] operator 口令（导出提示词等）入库 ----


def test_prompt_export_password_db_overrides_env(monkeypatch, tmp_path):
    from novelvideo.model_gateway_settings import (
        get_prompt_export_password_setting,
        save_prompt_export_password,
    )
    from novelvideo.security.operator_auth import get_prompt_export_password

    _isolate_settings_db(monkeypatch, tmp_path)
    monkeypatch.setenv("PROMPT_EXPORT_PASSWORD", "env-fallback")
    save_prompt_export_password(password="db-secret-1")

    assert get_prompt_export_password_setting() == "db-secret-1"
    # DB 优先于 env
    assert get_prompt_export_password() == "db-secret-1"


def test_prompt_export_password_falls_back_to_env_when_db_empty(monkeypatch, tmp_path):
    from novelvideo.model_gateway_settings import get_prompt_export_password_setting
    from novelvideo.security.operator_auth import get_prompt_export_password

    _isolate_settings_db(monkeypatch, tmp_path)
    monkeypatch.setenv("PROMPT_EXPORT_PASSWORD", "env-fallback")

    assert get_prompt_export_password_setting() == ""
    assert get_prompt_export_password() == "env-fallback"


def test_prompt_export_password_fail_closed_when_unset(monkeypatch, tmp_path):
    from novelvideo.security.operator_auth import get_prompt_export_password
    from novelvideo.shared import runtime_env

    _isolate_settings_db(monkeypatch, tmp_path)
    # 屏蔽 dotenv 惰性回灌：仓库 .env 里的 PROMPT_EXPORT_PASSWORD 会在任何
    # _load_env() 时把键补回 os.environ，让「未配置」场景失效（同 fresh_install）。
    monkeypatch.setattr(runtime_env, "load_project_dotenv", lambda **_: None)
    monkeypatch.delenv("PROMPT_EXPORT_PASSWORD", raising=False)

    assert get_prompt_export_password() is None


def test_prompt_export_password_empty_string_clears_db(monkeypatch, tmp_path):
    from novelvideo.model_gateway_settings import (
        get_prompt_export_password_setting,
        save_prompt_export_password,
    )

    _isolate_settings_db(monkeypatch, tmp_path)
    save_prompt_export_password(password="db-secret-1")
    assert get_prompt_export_password_setting() == "db-secret-1"
    save_prompt_export_password(password="")
    assert get_prompt_export_password_setting() == ""


def test_prompt_export_password_setting_ignores_ce_database_in_ee(
    monkeypatch, tmp_path
):
    from novelvideo.model_gateway_settings import (
        get_prompt_export_password_setting,
        save_prompt_export_password,
    )

    _isolate_settings_db(monkeypatch, tmp_path)
    save_prompt_export_password(password="ce-stale")
    monkeypatch.setenv("ST_EDITION", "ee")
    monkeypatch.setenv("ST_CONTROL_PLANE_DSN", "postgresql://control-plane")
    # EE 不读 CE 设置库（避免 EE/CE 双真源）；返回空串，env 兜底交调用方。
    assert get_prompt_export_password_setting() == ""
