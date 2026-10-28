# [aipdd fork delta] WP-B: 别名映射目标存在性校验 + 启动期一致性自检。
# 根因是 250828 一类误配：别名 target 写了上游不存在的名字，或把纯文本模型写进
# 视觉位，直到运行期才在报文里爆出来。这里守住「保存前拦、启动后警」两条线。
from __future__ import annotations

import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from novelvideo import config
from novelvideo import model_gateway_settings
from novelvideo import newapi_provisioner
from novelvideo.api.routes import model_gateway
from novelvideo.model_gateway_settings import (
    get_newapi_model_alias_mappings,
    save_newapi_model_alias_mappings,
    save_newapi_provider_channels,
    save_upstream_models_snapshot,
)
from novelvideo.newapi_provisioner import (
    AliasTargetValidationError,
    build_alias_mapping_health,
    list_upstream_models,
    model_supports_vision,
    parse_upstream_model_ids,
    peek_alias_mapping_health,
    reset_alias_mapping_health_cache,
    reset_upstream_models_cache,
    resolve_models_endpoint,
    validate_alias_targets,
)
from novelvideo.model_slots import SLOTS  # [aipdd fork delta req40] 槽位登记表取代旧默认模型表


FUNCTIONAL_ALIASES = {
    str(slot.alias or "").strip()
    for slot in SLOTS.values()
    if str(slot.alias or "").strip()
}


def _isolate_settings_db(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.setattr(config, "STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("ST_EDITION", "ce")
    for key in (
        "ST_CONTROL_PLANE_DSN",
        "MODEL_GATEWAY_MODE",
        "MODEL_GATEWAY_RUNTIME_VERSION",
        "LOCALAPI_API_KEY",
        "LOCALAPI_BASE_URL",
    ):
        monkeypatch.delenv(key, raising=False)


@pytest.fixture(autouse=True)
def _clear_caches():
    """TTL 缓存在模块级：用例之间必须清零，否则会互相喂上一次的清单。"""
    reset_upstream_models_cache()
    reset_alias_mapping_health_cache()
    yield
    reset_upstream_models_cache()
    reset_alias_mapping_health_cache()


def _patch_upstream_listing(
    monkeypatch: pytest.MonkeyPatch,
    listing: dict[str, list[str] | str],
) -> list[str]:
    """把「拉上游清单」换成查表，并记录被调用的 provider 顺序。"""
    called: list[str] = []

    def fake_list(provider, **_kwargs):
        called.append(str(provider))
        value = listing.get(str(provider))
        if isinstance(value, str):
            return [], value
        return [{"name": str(name)} for name in (value or [])], ""

    # [req40] 目录拉取的唯一实现是 ``list_upstream_catalog``（名字视图只是它的薄
    # 封装），拦截点跟着上移一层，否则 fake 永远不会被内部调用链调到。
    monkeypatch.setattr(newapi_provisioner, "list_upstream_catalog", fake_list)
    return called


class _FakeResponse:
    def __init__(self, payload, status_code: int = 200):
        self._payload = payload
        self.status_code = status_code

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return self._payload


class _FakeHttpx:
    def __init__(self, payload=None, error: str = "", status_code: int = 200):
        self.payload = payload
        self.error = error
        self.status_code = status_code
        self.calls: list[tuple[str, dict]] = []

    def get(self, url, headers=None, timeout=None):
        self.calls.append((url, dict(headers or {})))
        if self.error:
            raise RuntimeError(self.error)
        return _FakeResponse(self.payload, self.status_code)


@pytest.mark.parametrize(
    ("base_url", "provider", "expected"),
    [
        ("https://ark.example.com/", "ark", "https://ark.example.com/api/v3"),
        ("https://ark.example.com/api/v3", "ark", "https://ark.example.com/api/v3"),
        ("https://api.example.com/v1", "aipdd", "https://api.example.com/v1"),
        ("https://api.example.com", "aipdd", "https://api.example.com/v1"),
        ("https://api.example.com/", "susciyuan", "https://api.example.com/v1"),
    ],
)
def test_resolve_models_endpoint_matches_provider_api_path(
    base_url: str,
    provider: str,
    expected: str,
) -> None:
    assert resolve_models_endpoint(base_url, provider) == expected


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        ({"data": [{"id": "b"}, {"id": "a"}, {"id": "a"}]}, ["a", "b"]),
        ({"models": [{"model": "glm-4.6"}, "  "]}, ["glm-4.6"]),
        ({"data": [{"name": "x"}]}, []),
        ("not-a-dict", []),
    ],
)
def test_parse_upstream_model_ids_handles_both_shapes(payload, expected) -> None:
    assert parse_upstream_model_ids(payload) == expected


def test_list_upstream_models_uses_cached_listing_and_bearer_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = _FakeHttpx(payload={"data": [{"id": "glm-4.6"}, {"id": "doubao-vision"}]})
    monkeypatch.setattr(newapi_provisioner, "httpx", fake)

    models, error = list_upstream_models(
        "aipdd",
        base_url="https://api.example.com",
        upstream_key="sk-secret",
    )
    assert error == ""
    assert models == ["doubao-vision", "glm-4.6"]
    assert fake.calls[0][0] == "https://api.example.com/v1/models"
    assert fake.calls[0][1]["Authorization"] == "Bearer sk-secret"

    # 命中 TTL 缓存：第二次不再打远程。
    again, _ = list_upstream_models(
        "aipdd",
        base_url="https://api.example.com",
        upstream_key="sk-secret",
    )
    assert again == models
    assert len(fake.calls) == 1

    # force=True 绕过缓存（面板「刷新模型」保持原行为）。
    list_upstream_models(
        "aipdd",
        base_url="https://api.example.com",
        upstream_key="sk-other",
        force=True,
    )
    assert len(fake.calls) == 2


def test_list_upstream_models_degrades_without_raising(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = _FakeHttpx(error="connect timeout")
    monkeypatch.setattr(newapi_provisioner, "httpx", fake)

    models, error = list_upstream_models(
        "aipdd", base_url="https://api.example.com", upstream_key="sk-secret"
    )
    assert models == []
    assert "connect timeout" in error
    # 远程失败不写缓存：下一次仍会真试。
    list_upstream_models(
        "aipdd", base_url="https://api.example.com", upstream_key="sk-secret"
    )
    assert len(fake.calls) == 2


def test_list_upstream_models_requires_base_and_key() -> None:
    assert list_upstream_models("aipdd", base_url="", upstream_key="sk") == (
        [],
        "baseUrl is required",
    )
    models, error = list_upstream_models("aipdd", base_url="https://x", upstream_key="")
    assert models == []
    assert "upstreamKey is required" in error


def test_empty_upstream_listing_is_treated_as_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = _FakeHttpx(payload={"data": []})
    monkeypatch.setattr(newapi_provisioner, "httpx", fake)
    models, error = list_upstream_models(
        "aipdd", base_url="https://api.example.com", upstream_key="sk"
    )
    assert models == []
    assert "empty model list" in error
    # 空清单不写缓存，避免长期误判成「该渠道没有模型」。
    list_upstream_models("aipdd", base_url="https://api.example.com", upstream_key="sk")
    assert len(fake.calls) == 2


@pytest.mark.parametrize(
    ("name", "vision"),
    [
        ("doubao-1-5-vision-pro", True),
        ("glm-4v-plus", False),
        ("qwen-vl-max", True),
        ("qwen2.5-vl-72b", True),
        ("DeepSeek-V3", False),
        ("kimi-k2", False),
        # [aipdd fork delta] doubao-seed 多模态系列（实测可理解图像与时序帧）。
        ("ap-doubao-seed-2.1-turbo", True),
        ("doubao-seed-2-1-turbo-260628", True),
        ("doubao-seed-1-6-250615", True),
        # 生成类 doubao-seedream 不得被 seed 分支误吞（非「看图」理解模型）。
        ("doubao-seedream-4.0", False),
    ],
)
def test_model_supports_vision(name: str, vision: bool) -> None:
    assert model_supports_vision(name) is vision


def _configure_channels() -> None:
    save_newapi_provider_channels(
        [
            {"provider": "aipdd", "baseUrl": "https://api.example.com", "upstreamKey": "sk-a"},
            {"provider": "ark", "baseUrl": "https://ark.example.com", "upstreamKey": "sk-k"},
        ]
    )


def test_validate_alias_targets_rejects_unknown_target_with_candidates(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    _isolate_settings_db(monkeypatch, tmp_path)
    _configure_channels()
    called = _patch_upstream_listing(
        monkeypatch,
        {"aipdd": ["glm-4.6", "ap-glm-5.2"], "ark": ["doubao-seed"]},
    )

    ok = validate_alias_targets(
        {"DC-text-LLM": {"provider": "aipdd", "target": "glm-4.6"}}
    )
    assert ok == {"checked": 1, "degraded": [], "failures": []}

    with pytest.raises(AliasTargetValidationError) as exc_info:
        validate_alias_targets(
            {"DC-text-LLM": {"provider": "AIPDD", "target": "glm-250828"}}
        )
    failures = exc_info.value.failures
    assert [item["reason"] for item in failures] == ["not-found"]
    assert failures[0]["provider"] == "aipdd"
    # [req40] 目录名集合按名字排序去重（顺序不再是上游响应里的出现顺序）。
    assert failures[0]["candidates"] == ["ap-glm-5.2", "glm-4.6"]
    # detail 直接可读：带 provider/别名/目标/候选，用户不用再去翻上游。
    detail = str(exc_info.value)
    assert "上游模型清单里不存在" in detail
    assert "候选：ap-glm-5.2, glm-4.6" in detail
    assert called == ["aipdd", "aipdd"]


def test_validate_alias_targets_blocks_text_model_in_vision_slot(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    _isolate_settings_db(monkeypatch, tmp_path)
    _configure_channels()
    _patch_upstream_listing(
        monkeypatch,
        {
            "aipdd": ["glm-4.6", "doubao-1-5-vision-pro", "ap-glm-5.2"],
            "ark": ["doubao-seed"],
        },
    )

    with pytest.raises(AliasTargetValidationError) as exc_info:
        validate_alias_targets(
            {
                "DC-vision-LLM": {
                    "provider": "aipdd",
                    "target": "glm-4.6",
                    "capability": "vision",
                }
            }
        )
    failure = exc_info.value.failures[0]
    assert failure["reason"] == "vision-required"
    # 候选按能力同类过滤：视觉位只回能看图的模型。
    assert failure["candidates"] == ["doubao-1-5-vision-pro"]
    assert "不支持图片能力" in str(exc_info.value)

    # 同一目标在纯文本位不算错配（capability 未声明即不检能力）。
    assert validate_alias_targets(
        {"DC-text-LLM": {"provider": "aipdd", "target": "glm-4.6"}}
    )["checked"] == 1


def test_validate_alias_targets_accepts_doubao_seed_multimodal_in_vision_slot(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    """[aipdd fork delta] doubao-seed 多模态系列在视觉位不得被误拦。

    视频解析 404 修复期间实测 ap-doubao-seed-2.1-turbo 可理解截图与时序帧，
    旧正则却把它判成纯文本 → 面板保存被拦、自检误报 capabilityMismatch。
    """
    _isolate_settings_db(monkeypatch, tmp_path)
    _configure_channels()
    _patch_upstream_listing(
        monkeypatch, {"aipdd": ["ap-doubao-seed-2.1-turbo", "ap-glm-5.2"]}
    )

    result = validate_alias_targets(
        {
            "DC-freezone-vision-LLM": {
                "provider": "aipdd",
                "target": "ap-doubao-seed-2.1-turbo",
                "capability": "vision",
            }
        }
    )
    assert result == {"checked": 1, "degraded": [], "failures": []}


def test_validate_alias_targets_capability_comes_from_batch_argument(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    _isolate_settings_db(monkeypatch, tmp_path)
    _configure_channels()
    _patch_upstream_listing(monkeypatch, {"aipdd": ["glm-4.6", "doubao-vision-pro"]})

    with pytest.raises(AliasTargetValidationError) as exc_info:
        validate_alias_targets(
            {"DC-vision-LLM": {"provider": "aipdd", "target": "glm-4.6"}},
            capability="VISION",
        )
    assert exc_info.value.failures[0]["reason"] == "vision-required"


def test_validate_alias_targets_degrades_when_upstream_unreachable(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    _isolate_settings_db(monkeypatch, tmp_path)
    _configure_channels()
    _patch_upstream_listing(monkeypatch, {"aipdd": "connect timeout"})

    # 拉不到清单 → 只降级记 warning，绝不因远程故障阻断「设为默认」。
    result = validate_alias_targets(
        {"DC-text-LLM": {"provider": "aipdd", "target": "does-not-exist"}},
    )
    assert result == {"checked": 0, "degraded": ["aipdd"], "failures": []}


def _write_channels_raw(channels: list[dict]) -> None:
    """绕过 save_newapi_provider_channels 的必填校验，造「在册但没 key」这类脏配置。"""
    model_gateway_settings._write_many(
        {
            "custom_newapi_provider_channels": json.dumps(
                channels, ensure_ascii=False, separators=(",", ":")
            )
        }
    )


def test_validate_alias_targets_skips_comfyui_and_unresolvable_entries(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    _isolate_settings_db(monkeypatch, tmp_path)
    _configure_channels()
    called = _patch_upstream_listing(monkeypatch, {"aipdd": ["glm-4.6"]})

    result = validate_alias_targets(
        {
            # comfyui 没有 OpenAI 兼容 /models，跳过。
            "DC-comfy-alias": {"provider": "comfyui", "target": "wf-1"},
            # 裸字符串（无 provider 归属）由调用方先行归并，这里不检。
            "DC-bare": "glm-4.6",
            "DC-empty-target": {"provider": "aipdd", "target": "  "},
            "DC-no-provider": {"target": "glm-4.6"},
            # 未配置渠道的 provider：按 degraded 处理，不阻断。
            "DC-stranger": {"provider": "stranger", "target": "x"},
        }
    )
    assert result["checked"] == 0
    assert result["degraded"] == ["stranger"]
    # 没配过的 provider 在查表阶段就短路了，根本不会去打远程。
    assert called == []


# --- 路由层：400 带候选 / validate=false 跳过 / capability 落真源 ----------------


@pytest.fixture(autouse=True)
def _register_local_auth_port():
    """model-gateway 路由组 router 级强制登录态；单用户模式恒放行。"""
    from novelvideo.ports.local.auth import FileAuthPort
    from novelvideo.ports.registry import register_port

    register_port("auth", FileAuthPort())


def _gateway_client(monkeypatch: pytest.MonkeyPatch) -> tuple[TestClient, list]:
    monkeypatch.setenv("LOCALAPI_PROVISIONER_ENABLED", "true")
    payloads: list[dict] = []
    monkeypatch.setattr(
        model_gateway,
        "get_provisioner_config",
        lambda _base_url=None, **_kwargs: type(
            "Cfg", (), {"admin_base_url": "http://newapi:3000"}
        )(),
    )
    monkeypatch.setattr(
        model_gateway, "ensure_admin_access_token", lambda _cfg: object()
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
    return TestClient(app), payloads


def test_alias_mappings_post_rejects_unknown_target_with_candidates(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    _isolate_settings_db(monkeypatch, tmp_path)
    _configure_channels()
    client, payloads = _gateway_client(monkeypatch)
    _patch_upstream_listing(monkeypatch, {"aipdd": ["glm-4.6", "ap-glm-5.2"]})

    response = client.post(
        "/model-gateway/custom/newapi/alias-mappings",
        json={
            "mappings": {"DC-text-LLM": "glm-250828"},
            "defaultProvider": "aipdd",
        },
    )
    assert response.status_code == 400
    detail = response.json()["detail"]
    assert "glm-250828" in detail
    assert "glm-4.6" in detail
    # 校验不过就不落库、不投影：真源和网关都不被脏数据污染。
    assert get_newapi_model_alias_mappings() == {}
    assert payloads == []


def test_alias_mappings_post_blocks_text_model_in_vision_default(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    _isolate_settings_db(monkeypatch, tmp_path)
    _configure_channels()
    client, _payloads = _gateway_client(monkeypatch)
    _patch_upstream_listing(
        monkeypatch,
        {"aipdd": ["glm-4.6", "doubao-1-5-vision-pro"]},
    )

    response = client.post(
        "/model-gateway/custom/newapi/alias-mappings",
        json={
            "mappings": {"DC-style-analyzer-LLM": "glm-4.6"},
            "defaultProvider": "aipdd",
            "capability": "vision",
        },
    )
    assert response.status_code == 400
    assert "不支持图片能力" in response.json()["detail"]

    # 换成能看图的模型即通过，并把 capability 落进真源供自检复用。
    passed = client.post(
        "/model-gateway/custom/newapi/alias-mappings",
        json={
            "mappings": {"DC-style-analyzer-LLM": "doubao-1-5-vision-pro"},
            "defaultProvider": "aipdd",
            "capability": "vision",
        },
    )
    assert passed.status_code == 200
    assert get_newapi_model_alias_mappings() == {
        "DC-style-analyzer-LLM": {
            "provider": "aipdd",
            "target": "doubao-1-5-vision-pro",
            "capability": "vision",
        }
    }


def test_alias_mappings_post_validate_false_falls_back_to_format_only(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    _isolate_settings_db(monkeypatch, tmp_path)
    _configure_channels()
    client, payloads = _gateway_client(monkeypatch)
    called = _patch_upstream_listing(monkeypatch, {"aipdd": ["glm-4.6"]})
    # [req40] 直通注册名只来自渠道目录：这里「拉到过」glm-250828，复合名 / 裸名才存在。
    save_upstream_models_snapshot("aipdd", ["glm-250828"])

    response = client.post(
        "/model-gateway/custom/newapi/alias-mappings",
        json={
            "mappings": {"DC-text-LLM": "glm-250828"},
            "defaultProvider": "aipdd",
            "validate": False,
        },
    )
    assert response.status_code == 200
    assert called == []
    # 投影按拍平注册表：内部绑定 + 复合名（总是）+ 裸名（单渠道唯一时）；
    # 不打上游仍是底线（validate=False 只是跳过别名目标校验）。
    assert payloads[0]["channel"]["model_mapping"] == (
        '{"DC-text-LLM":"glm-250828","aipdd::glm-250828":"glm-250828","glm-250828":"glm-250828"}'
    )


def test_alias_mappings_read_and_config_carry_health_snapshot(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    _isolate_settings_db(monkeypatch, tmp_path)
    client, _payloads = _gateway_client(monkeypatch)

    # 未自检过：两处都只回 pending 快照，绝不因为一次 GET 去打上游。
    listing = _patch_upstream_listing(monkeypatch, {"aipdd": ["glm-4.6"]})
    fetched = client.get("/model-gateway/custom/newapi/alias-mappings")
    assert fetched.status_code == 200
    assert fetched.json()["data"]["health"] == {"status": "pending", "checked": False}
    config_body = client.get("/model-gateway/config")
    assert config_body.status_code == 200
    assert config_body.json()["data"]["aliasMappingHealth"] == {
        "status": "pending",
        "checked": False,
    }
    assert listing == []

    health = client.get(
        "/model-gateway/custom/newapi/alias-mappings/health", params={"refresh": "true"}
    )
    assert health.status_code == 200
    assert health.json()["data"]["checked"] is True
    # 显式检查之后，/config 的快照才有内容。
    after = client.get("/model-gateway/config")
    assert after.json()["data"]["aliasMappingHealth"]["checked"] is True


# --- 自检：三类输出 + TTL 缓存 --------------------------------------------------


def test_build_alias_mapping_health_reports_three_categories(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    _isolate_settings_db(monkeypatch, tmp_path)
    functional = sorted(FUNCTIONAL_ALIASES)
    stale_alias, mismatch_alias = functional[0], functional[1]
    save_newapi_provider_channels(
        [{"provider": "aipdd", "baseUrl": "https://api.example.com", "upstreamKey": "sk-a"}]
    )
    # 渠道在册但没 key：算 noChannel，同时拉不到清单 → degraded。
    _write_channels_raw(
        [
            {
                "provider": "aipdd",
                "type": 1,
                "upstreamKey": "sk-a",
                "baseUrl": "https://api.example.com",
                "priority": 0,
                "settings": {},
            },
            {
                "provider": "susciyuan",
                "type": 1,
                "upstreamKey": "",
                "baseUrl": "https://s.example.com",
                "priority": 0,
                "settings": {},
            },
        ]
    )
    save_newapi_model_alias_mappings(
        {
            stale_alias: {"provider": "aipdd", "target": "glm-gone"},
            mismatch_alias: {
                "provider": "aipdd",
                "target": "glm-4.6",
                "capability": "vision",
            },
            "DC-orphan-LLM": {"provider": "stranger", "target": "x"},
            # 渠道在册但没 key：目标无法核验，归 degraded。
            "DC-susc-LLM": {"provider": "susciyuan", "target": "susc-text"},
        }
    )
    called = _patch_upstream_listing(
        monkeypatch,
        {
            "aipdd": ["glm-4.6", "doubao-1-5-vision-pro"],
            "susciyuan": "upstreamKey is required for provider susciyuan",
            "stranger": "provider stranger is not configured",
        },
    )

    health = build_alias_mapping_health()

    assert health["status"] == "warning"
    assert health["checked"] is True
    assert health["source"] == "project"
    assert health["total"] == 4
    # 悬空别名：功能位在册、真源里没有。
    assert set(health["unmapped"]) == FUNCTIONAL_ALIASES - {stale_alias, mismatch_alias}
    assert [item["alias"] for item in health["staleTargets"]] == [stale_alias]
    assert health["staleTargets"][0]["candidates"] == [
        "doubao-1-5-vision-pro",
        "glm-4.6",
    ]
    assert [item["alias"] for item in health["capabilityMismatch"]] == [mismatch_alias]
    # 能力错配的候选只给能看图的。
    assert health["capabilityMismatch"][0]["candidates"] == ["doubao-1-5-vision-pro"]
    assert health["noChannel"] == ["stranger", "susciyuan"]
    assert sorted(item["provider"] for item in health["degraded"]) == [
        "stranger",
        "susciyuan",
    ]
    assert health["aliases"][stale_alias]["status"] == "stale"
    assert health["aliases"][mismatch_alias]["status"] == "capabilityMismatch"
    assert health["aliases"]["DC-orphan-LLM"]["status"] == "noChannel"
    assert health["aliases"]["DC-susc-LLM"]["status"] == "mapped"
    assert health["aliases"][mismatch_alias]["capability"] == "vision"
    # 只有真源里出现、且本地确有渠道的 provider 才会去打远程。
    assert called == ["aipdd", "susciyuan"]

    # 结果带 TTL 缓存：第二次读不再拉上游。
    build_alias_mapping_health()
    assert called == ["aipdd", "susciyuan"]
    # force=True 供「立即检查」按钮用，重新算一遍。
    build_alias_mapping_health(force=True)
    assert called == ["aipdd", "susciyuan", "aipdd", "susciyuan"]


def test_alias_mapping_health_is_clean_when_everything_maps(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    _isolate_settings_db(monkeypatch, tmp_path)
    save_newapi_provider_channels(
        [{"provider": "aipdd", "baseUrl": "https://api.example.com", "upstreamKey": "sk-a"}]
    )
    save_newapi_model_alias_mappings(
        {alias: {"provider": "aipdd", "target": "glm-4.6"} for alias in FUNCTIONAL_ALIASES}
    )
    _patch_upstream_listing(monkeypatch, {"aipdd": ["glm-4.6"]})

    health = build_alias_mapping_health()

    assert health["status"] == "ok"
    assert health["unmapped"] == []
    assert health["staleTargets"] == []
    assert health["capabilityMismatch"] == []
    assert health["noChannel"] == []


def test_peek_alias_mapping_health_is_cache_only_and_resettable(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    _isolate_settings_db(monkeypatch, tmp_path)
    assert peek_alias_mapping_health() == {"status": "pending", "checked": False}

    _patch_upstream_listing(monkeypatch, {"aipdd": ["glm-4.6"]})
    built = build_alias_mapping_health()
    peeked = peek_alias_mapping_health()
    assert peeked["checked"] is True
    assert peeked["status"] == built["status"]
    # 内部计时字段不外泄。
    assert "computedAtMono" not in peeked
    assert peek_alias_mapping_health() is not peeked

    # 真源被改写后作废：下次读重新算（保存链路靠它收敛横幅）。
    reset_alias_mapping_health_cache()
    assert peek_alias_mapping_health() == {"status": "pending", "checked": False}


def test_saving_alias_mappings_invalidates_health_snapshot(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    _isolate_settings_db(monkeypatch, tmp_path)
    _configure_channels()
    client, _payloads = _gateway_client(monkeypatch)
    _patch_upstream_listing(monkeypatch, {"aipdd": ["glm-4.6"]})

    client.get(
        "/model-gateway/custom/newapi/alias-mappings/health", params={"refresh": "true"}
    )
    assert client.get("/model-gateway/config").json()["data"]["aliasMappingHealth"][
        "checked"
    ] is True

    client.post(
        "/model-gateway/custom/newapi/alias-mappings",
        json={"mappings": {"DC-text-LLM": "glm-4.6"}, "defaultProvider": "aipdd"},
    )
    # 写完立刻回到 pending，避免横幅继续展示上一轮结论。
    assert client.get("/model-gateway/config").json()["data"]["aliasMappingHealth"] == {
        "status": "pending",
        "checked": False,
    }
