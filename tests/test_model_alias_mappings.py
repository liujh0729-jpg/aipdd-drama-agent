# [aipdd fork delta] WP-A: 别名映射单一真源（项目自有 runtime_settings）的读写、
# 容错、按渠道编译投影与一次性反向导入行为。
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
    MODEL_ALIAS_IMPORT_MARKER_KEY,
    MODEL_ALIAS_MAPPINGS_SETTING_KEY,
    backfill_newapi_model_alias_mappings,
    build_channel_model_mapping,
    get_newapi_model_alias_mappings,
    get_newapi_provider_channels,
    model_alias_backfill_done,
    record_newapi_model_alias_mappings,
    save_newapi_embedding_model_config,
    save_newapi_model_alias_mappings,
    save_newapi_provider_channels,
    save_upstream_models_snapshot,
)


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


def _write_media_mappings_raw(mappings: dict) -> None:
    """绕过媒体目录 config 校验，只测别名投影编译逻辑本身。"""
    model_gateway_settings._write_many(
        {
            "custom_newapi_media_model_mappings": json.dumps(
                mappings, ensure_ascii=False, separators=(",", ":")
            )
        }
    )


def test_get_alias_mappings_is_empty_before_any_write(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    _isolate_settings_db(monkeypatch, tmp_path)
    assert get_newapi_model_alias_mappings() == {}
    assert build_channel_model_mapping("aipdd") == {}


def test_alias_mappings_survive_corrupted_json(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    _isolate_settings_db(monkeypatch, tmp_path)
    conn = model_gateway_settings._connect()
    try:
        conn.execute(
            "INSERT INTO runtime_settings(key, value, updated_at) VALUES (?, ?, ?)",
            (MODEL_ALIAS_MAPPINGS_SETTING_KEY, "{not-json", "2026-01-01"),
        )
        conn.commit()
    finally:
        conn.close()

    assert get_newapi_model_alias_mappings() == {}
    # 坏 JSON 不抛：写入按 merge 语义视作空表起步。
    saved = save_newapi_model_alias_mappings(
        {"DC-a-LLM": {"provider": "aipdd", "target": "glm-4.6"}},
        merge=True,
    )
    assert saved == {"DC-a-LLM": {"provider": "aipdd", "target": "glm-4.6"}}


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ('{"DC-a-LLM": "plain-string"}', {}),
        ('{"DC-a-LLM": {"provider": "aipdd"}}', {}),
        ('{"DC-a-LLM": {"target": "glm-4.6"}}', {}),
        ('{"": {"provider": "aipdd", "target": "glm-4.6"}}', {}),
        ('["DC-a-LLM"]', {}),
        (
            '{"DC-a-LLM": {"provider": "AIPDD", "target": " glm-4.6 ",'
            ' "fallbackProvider": "aipdd", "fallbackTarget": "other"}}',
            {"DC-a-LLM": {"provider": "aipdd", "target": "glm-4.6"}},
        ),
        (
            '{"DC-a-LLM": {"provider": "aipdd", "target": "glm-4.6",'
            ' "fallbackProvider": "ark", "fallbackTarget": "doubao-seed"}}',
            {
                "DC-a-LLM": {
                    "provider": "aipdd",
                    "target": "glm-4.6",
                    "fallbackProvider": "ark",
                    "fallbackTarget": "doubao-seed",
                }
            },
        ),
    ],
)
def test_decode_alias_mappings_normalizes_and_drops(
    raw: str,
    expected: dict,
) -> None:
    assert model_gateway_settings._decode_alias_mappings(raw) == expected


def test_save_alias_mappings_merge_and_delete(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    _isolate_settings_db(monkeypatch, tmp_path)
    save_newapi_model_alias_mappings(
        {
            "DC-a-LLM": {"provider": "aipdd", "target": "glm-4.6"},
            "DC-b-LLM": {"provider": "ark", "target": "doubao-1-5"},
        }
    )
    # merge=True：未提及的条目保留；裸字符串走 defaultProvider。
    merged = save_newapi_model_alias_mappings(
        {"DC-c-LLM": "ap-glm-5.2"},
        merge=True,
        default_provider="AiPDD",
    )
    assert merged == {
        "DC-a-LLM": {"provider": "aipdd", "target": "glm-4.6"},
        "DC-b-LLM": {"provider": "ark", "target": "doubao-1-5"},
        "DC-c-LLM": {"provider": "aipdd", "target": "ap-glm-5.2"},
    }
    # 空 target = 显式删除该别名。
    deleted = save_newapi_model_alias_mappings(
        {"DC-a-LLM": {"provider": "aipdd", "target": "  "}},
        merge=True,
    )
    assert "DC-a-LLM" not in deleted
    # merge=False 整体替换，读取到的是同一份真源。
    replaced = save_newapi_model_alias_mappings(
        {"DC-z-LLM": {"provider": "ark", "target": "deepseek-v3"}}
    )
    assert replaced == {"DC-z-LLM": {"provider": "ark", "target": "deepseek-v3"}}
    assert get_newapi_model_alias_mappings() == replaced


def test_save_alias_mappings_rejects_invalid_entries(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    _isolate_settings_db(monkeypatch, tmp_path)
    with pytest.raises(ValueError, match="invalid model alias mapping entry"):
        save_newapi_model_alias_mappings({"DC-a-LLM": 123})
    with pytest.raises(ValueError, match="provider is required"):
        save_newapi_model_alias_mappings({"DC-a-LLM": "glm-4.6"})


def test_record_alias_mappings_from_batch_style_payload(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    _isolate_settings_db(monkeypatch, tmp_path)
    saved = record_newapi_model_alias_mappings(
        {"DC-a-LLM": "glm-4.6", "DC-b-LLM": "", "  ": "x"},
        provider="AIPDD",
    )
    assert saved == {"DC-a-LLM": {"provider": "aipdd", "target": "glm-4.6"}}
    # 空 mapping 不改写真源。
    assert record_newapi_model_alias_mappings({}, provider="aipdd") == saved


def test_build_channel_model_mapping_compiles_per_provider(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    _isolate_settings_db(monkeypatch, tmp_path)
    save_newapi_model_alias_mappings(
        {
            "DC-text-LLM": {
                "provider": "aipdd",
                "target": "glm-4.6",
                "fallbackProvider": "ark",
                "fallbackTarget": "doubao-1-5",
            },
            "DC-vision-LLM": {"provider": "ark", "target": "doubao-vision"},
            "DC-no-fallback-LLM": {"provider": "aipdd", "target": "ap-glm-5.2"},
        }
    )
    _write_media_mappings_raw(
        {
            "seedream-4": {"provider": "aipdd", "mediaType": "image"},
            "Doubao-Seedance": {
                "provider": "ark",
                "upstreamModel": "doubao-seedance-1-0",
                "mediaType": "video",
            },
            "only-other": {"provider": "susciyuan", "mediaType": "video"},
        }
    )
    save_newapi_embedding_model_config(
        provider="aipdd",
        upstream_model="doubao-embedding-vision",
        dimension=1024,
    )

    assert build_channel_model_mapping("AIPDD") == {
        "DC-text-LLM": "glm-4.6",
        "DC-no-fallback-LLM": "ap-glm-5.2",
        "seedream-4": "seedream-4",
        # 目录按「渠道 + 模型」发复合调用名：显式映射的模型也必须有自己的复合名。
        "aipdd::seedream-4": "seedream-4",
        "DC-cognee-embedding": "doubao-embedding-vision",
    }
    # [aipdd fork delta] 拍平：备援编译退役（DC-text-LLM 不再进 ark 渠道）；媒体
    # 映射 upstreamModel 为空时回落模型名本身。
    assert build_channel_model_mapping("ark") == {
        "DC-vision-LLM": "doubao-vision",
        "Doubao-Seedance": "doubao-seedance-1-0",
        "ark::Doubao-Seedance": "doubao-seedance-1-0",
    }
    assert build_channel_model_mapping("susciyuan") == {
        "only-other": "only-other",
        "susciyuan::only-other": "only-other",
    }
    assert build_channel_model_mapping("") == {}
    assert build_channel_model_mapping("unknown") == {}


def test_backfill_alias_mappings_only_fills_missing(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    _isolate_settings_db(monkeypatch, tmp_path)
    save_newapi_model_alias_mappings(
        {"DC-text-LLM": {"provider": "aipdd", "target": "glm-4.6"}}
    )
    assert model_alias_backfill_done() is False

    result = backfill_newapi_model_alias_mappings(
        {
            # 已存在的别名不被网关旧值覆盖。
            "DC-text-LLM": {"provider": "ark", "target": "stale-from-gateway"},
            "DC-vision-LLM": {"provider": "ark", "target": "doubao-vision"},
            "bad-entry": {"target": ""},
        }
    )
    assert result["imported"] == 1
    assert result["aliases"] == ["DC-vision-LLM"]
    assert result["total"] == 2
    assert model_alias_backfill_done() is True

    settings = model_gateway_settings._read_all()
    assert settings[MODEL_ALIAS_IMPORT_MARKER_KEY]
    stored = json.loads(settings[MODEL_ALIAS_MAPPINGS_SETTING_KEY])
    assert stored["DC-text-LLM"] == {"provider": "aipdd", "target": "glm-4.6"}
    assert stored["DC-vision-LLM"] == {"provider": "ark", "target": "doubao-vision"}
    assert "bad-entry" not in stored


def _patch_gateway_channels(monkeypatch: pytest.MonkeyPatch, channels: list) -> None:
    monkeypatch.setattr(newapi_provisioner, "provisioner_enabled", lambda: True)
    # [aipdd fork delta] 接受关键字参数：回填现在按调用方传 init_timeout_ms
    # 预算（启动路径 5s），配置本体在本测试里已由后续 mock 旁路，返回 None 即可。
    monkeypatch.setattr(
        newapi_provisioner, "get_provisioner_config", lambda **kwargs: None
    )
    monkeypatch.setattr(
        newapi_provisioner, "ensure_admin_access_token", lambda cfg: None
    )
    monkeypatch.setattr(
        newapi_provisioner, "list_channels", lambda cfg, admin, **kwargs: channels
    )


GATEWAY_CHANNELS = [
    {
        "name": "DC-aipdd",
        "type": 1,
        "model_mapping": {
            "DC-text-LLM": "glm-4.6",
            "DC-empty-LLM": "  ",
            # 媒体模型名不属于别名真源（已有自己的存储）。
            "seedream": "gateway-workflow-name",
        },
    },
    {
        "name": "DC-ark",
        "type": 1,
        "model_mapping": {"DC-vision-LLM": "doubao-vision"},
    },
    # 未配置的 provider / ComfyUI(type=63) / 非 DC- 前缀都不进真源。
    {"name": "DC-stranger", "type": 1, "model_mapping": {"DC-x": "y"}},
    {"name": "DC-comfyui", "type": 63, "model_mapping": {"DC-comfy-alias": "wf"}},
    {"name": "Other", "type": 1, "model_mapping": {"DC-z": "w"}},
]


def test_provisioner_backfill_imports_gateway_mapping_once(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    _isolate_settings_db(monkeypatch, tmp_path)
    save_newapi_provider_channels(
        [
            {"provider": "aipdd", "upstreamKey": "sk-aipdd"},
            {"provider": "ark", "upstreamKey": "sk-ark"},
        ]
    )
    _write_media_mappings_raw({"seedream": {"provider": "comfyui", "mediaType": "image"}})
    _patch_gateway_channels(monkeypatch, GATEWAY_CHANNELS)

    result = newapi_provisioner.backfill_model_alias_mappings_from_gateway()

    assert result["imported"] == 2
    assert result["candidates"] == 2
    assert get_newapi_model_alias_mappings() == {
        "DC-text-LLM": {"provider": "aipdd", "target": "glm-4.6"},
        "DC-vision-LLM": {"provider": "ark", "target": "doubao-vision"},
    }

    # 标记位已写：再次启动级回填直接跳过，不重复拉网关。
    again = newapi_provisioner.backfill_model_alias_mappings_from_gateway()
    assert again == {"skipped": "already-imported", "imported": 0}
    # force=True 仍会拉一次（只补不覆盖，故真源不变）。
    forced = newapi_provisioner.backfill_model_alias_mappings_from_gateway(force=True)
    assert forced["imported"] == 0
    assert len(get_newapi_model_alias_mappings()) == 2


def test_provisioner_backfill_skips_when_disabled(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    _isolate_settings_db(monkeypatch, tmp_path)
    monkeypatch.setattr(newapi_provisioner, "provisioner_enabled", lambda: False)
    assert newapi_provisioner.backfill_model_alias_mappings_from_gateway() == {
        "skipped": "provisioner-disabled",
        "imported": 0,
    }


def test_removing_provider_channel_prunes_alias_mappings(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    _isolate_settings_db(monkeypatch, tmp_path)
    save_newapi_provider_channels(
        [
            {"provider": "aipdd", "upstreamKey": "sk-aipdd"},
            {"provider": "ark", "upstreamKey": "sk-ark"},
        ]
    )
    save_newapi_model_alias_mappings(
        {
            "DC-keep-LLM": {"provider": "aipdd", "target": "glm-4.6"},
            "DC-drop-LLM": {"provider": "ark", "target": "doubao-vision"},
            "DC-fallback-LLM": {
                "provider": "aipdd",
                "target": "glm-4.6",
                "fallbackProvider": "ark",
            },
        }
    )

    save_newapi_provider_channels([{"provider": "aipdd", "upstreamKey": "sk-aipdd"}])

    assert [c["provider"] for c in get_newapi_provider_channels()] == ["aipdd"]
    assert get_newapi_model_alias_mappings() == {
        "DC-keep-LLM": {"provider": "aipdd", "target": "glm-4.6"}
    }


@pytest.fixture(autouse=True)
def _register_local_auth_port():
    """model-gateway 路由组 router 级强制登录态；这里只注册 CE FileAuthPort
    （单用户模式 verify_session 恒放行），不改变鉴权层原有契约。"""
    from novelvideo.ports.local.auth import FileAuthPort
    from novelvideo.ports.registry import register_port

    register_port("auth", FileAuthPort())


def _gateway_client(monkeypatch: pytest.MonkeyPatch) -> tuple[TestClient, list]:
    """搭一个只含 model-gateway 路由的 app，并把渠道写入换成 spy。"""
    monkeypatch.setenv("NEWAPI_PROVISIONER_ENABLED", "true")
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


def test_alias_mappings_routes_read_and_project_truth_source(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    _isolate_settings_db(monkeypatch, tmp_path)
    save_newapi_provider_channels([{"provider": "aipdd", "upstreamKey": "sk-a"}])
    # [req40] 注册表只从渠道目录编译：先同步出目录，绑定目标才有可注册的实名。
    save_upstream_models_snapshot("aipdd", ["glm-4.6"])
    client, payloads = _gateway_client(monkeypatch)

    saved = client.post(
        "/model-gateway/custom/newapi/alias-mappings",
        json={
            "mappings": {"DC-text-LLM": "glm-4.6"},
            "defaultProvider": "AiPDD",
            "merge": True,
        },
    )
    assert saved.status_code == 200
    data = saved.json()["data"]
    assert data["source"] == "project"
    assert data["mappings"] == {"DC-text-LLM": {"provider": "aipdd", "target": "glm-4.6"}}
    assert data["projectionFailed"] == []
    assert data["projection"][0]["provider"] == "aipdd"
    assert data["projection"][0]["models"] == 3
    # [aipdd fork delta] 拍平：投影 = 渠道完整注册表——内部绑定（DC-* 裸名）+
    # 复合名（aipdd::glm-4.6）+ 单渠道唯一裸名（glm-4.6，无歧义直通兼容）。
    assert json.loads(payloads[0]["channel"]["model_mapping"]) == {
        "DC-text-LLM": "glm-4.6",
        "aipdd::glm-4.6": "glm-4.6",
        "glm-4.6": "glm-4.6",
    }

    fetched = client.get("/model-gateway/custom/newapi/alias-mappings")
    assert fetched.status_code == 200
    body = fetched.json()["data"]
    assert body["source"] == "project"
    assert body["mappings"] == data["mappings"]
    assert body["projection"] == {
        "aipdd": {
            "DC-text-LLM": "glm-4.6",
            "aipdd::glm-4.6": "glm-4.6",
            "glm-4.6": "glm-4.6",
        }
    }


def test_alias_projection_merges_snapshot_passthrough_truth_names(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    """[aipdd fork delta] 上游清单快照里的真名以拍平注册表并进投影（零远程）。

    外部调用者拿模型广场上的复合名（如 aipdd::ap-doubao-seed-2.1-turbo）或
    单渠道唯一裸名可以直接调通；快照是本地数据源，保存路径本身不打上游。
    """
    _isolate_settings_db(monkeypatch, tmp_path)
    save_newapi_provider_channels([{"provider": "aipdd", "upstreamKey": "sk-a"}])
    save_upstream_models_snapshot("aipdd", ["ap-doubao-seed-2.1-turbo", "glm-4.6"])
    client, payloads = _gateway_client(monkeypatch)

    saved = client.post(
        "/model-gateway/custom/newapi/alias-mappings",
        json={
            "mappings": {"DC-text-LLM": "glm-4.6"},
            "defaultProvider": "aipdd",
            "validate": False,
        },
    )
    assert saved.status_code == 200
    assert json.loads(payloads[0]["channel"]["model_mapping"]) == {
        "DC-text-LLM": "glm-4.6",
        "aipdd::ap-doubao-seed-2.1-turbo": "ap-doubao-seed-2.1-turbo",
        "ap-doubao-seed-2.1-turbo": "ap-doubao-seed-2.1-turbo",
        "aipdd::glm-4.6": "glm-4.6",
        "glm-4.6": "glm-4.6",
    }
    assert saved.json()["data"]["projection"][0]["models"] == 5


def test_passthrough_lists_every_channel_models_without_cession(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    """[aipdd fork delta] 拍平：直通清单 = 该渠道快照 ∪ 配置声明真名，不再做跨渠道
    归属让位——同名模型在每个渠道各列一份，由用户在复合名里显式选择（无隐式裁决）。"""
    _isolate_settings_db(monkeypatch, tmp_path)
    save_newapi_provider_channels(
        [
            {"provider": "aipdd", "upstreamKey": "sk-a"},
            {"provider": "newapi", "upstreamKey": "sk-n"},
        ]
    )
    # ap-glm-5.2 在真源登记在 aipdd；两个上游清单恰好都提供同名模型。
    save_newapi_model_alias_mappings(
        {"ap-glm-5.2": {"provider": "aipdd", "target": "ap-glm-5.2"}},
        merge=True,
    )
    save_upstream_models_snapshot("aipdd", ["ap-glm-5.2", "ap-image-2.0"])
    save_upstream_models_snapshot(
        "newapi", ["ap-glm-5.2", "AP Seedance-2.0 高性价比版"]
    )

    newapi_names = newapi_provisioner.upstream_passthrough_models(
        "newapi", from_snapshot=True
    )
    assert "ap-glm-5.2" in newapi_names
    assert "AP Seedance-2.0 高性价比版" in newapi_names

    aipdd_names = newapi_provisioner.upstream_passthrough_models(
        "aipdd", from_snapshot=True
    )
    assert "ap-glm-5.2" in aipdd_names
    assert "ap-image-2.0" in aipdd_names


def test_alias_projection_keeps_same_name_on_each_owning_channel(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    """[aipdd fork delta] 拍平：同名模型在多个拥有它的渠道各注册一条复合名
    （aipdd::ap-glm-5.2 / newapi::ap-glm-5.2）；裸名因非唯一而不注册——跨渠道
    同名必须由调用方显式选复合名，系统不做归属让位或抢注去重。"""
    _isolate_settings_db(monkeypatch, tmp_path)
    save_newapi_provider_channels(
        [
            {"provider": "aipdd", "upstreamKey": "sk-a"},
            {"provider": "newapi", "upstreamKey": "sk-n"},
        ]
    )
    save_newapi_model_alias_mappings(
        {"ap-glm-5.2": {"provider": "aipdd", "target": "ap-glm-5.2"}},
        merge=True,
    )
    save_upstream_models_snapshot("newapi", ["ap-glm-5.2"])
    # [req40] 同名模型在两个渠道各注册一条复合名：两边都得有自己的目录。
    save_upstream_models_snapshot("aipdd", ["ap-glm-5.2"])
    client, _payloads = _gateway_client(monkeypatch)

    fetched = client.get("/model-gateway/custom/newapi/alias-mappings")
    assert fetched.status_code == 200
    projection = fetched.json()["data"]["projection"]
    assert projection["aipdd"] == {"aipdd::ap-glm-5.2": "ap-glm-5.2"}
    assert projection["newapi"] == {"newapi::ap-glm-5.2": "ap-glm-5.2"}
    # 裸名 ap-glm-5.2 被两个渠道同时拥有：不注册（无隐式裁决）。
    assert "ap-glm-5.2" not in projection["aipdd"]
    assert "ap-glm-5.2" not in projection["newapi"]


def test_alias_mappings_post_reports_projection_failure_without_rollback(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    _isolate_settings_db(monkeypatch, tmp_path)
    save_newapi_provider_channels([{"provider": "aipdd", "upstreamKey": "sk-a"}])
    client, _payloads = _gateway_client(monkeypatch)

    def broken_upsert(_cfg, _admin, _payload):
        raise RuntimeError("gateway unreachable")

    monkeypatch.setattr(model_gateway, "upsert_channel", broken_upsert)

    response = client.post(
        "/model-gateway/custom/newapi/alias-mappings",
        json={
            "mappings": {"DC-text-LLM": {"provider": "aipdd", "target": "glm-4.6"}},
        },
    )
    assert response.status_code == 200
    data = response.json()["data"]
    failed = data["projectionFailed"]
    assert [item["provider"] for item in failed] == ["aipdd"]
    assert "gateway unreachable" in failed[0]["error"]
    # 真源不回滚：本项目才是权威。
    assert get_newapi_model_alias_mappings() == {
        "DC-text-LLM": {"provider": "aipdd", "target": "glm-4.6"}
    }


def test_alias_mappings_post_rejects_missing_provider(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    _isolate_settings_db(monkeypatch, tmp_path)
    client, payloads = _gateway_client(monkeypatch)

    response = client.post(
        "/model-gateway/custom/newapi/alias-mappings",
        json={"mappings": {"DC-text-LLM": "glm-4.6"}},
    )
    assert response.status_code == 400
    assert "provider is required" in response.json()["detail"]
    assert payloads == []
    assert get_newapi_model_alias_mappings() == {}


# --- WP-C: 假主备改真 priority --------------------------------------------------


def test_channel_registrations_register_bare_name_only_for_single_owner(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    """[aipdd fork delta] 拍平核心规则：复合名总是注册；裸名仅「恰好一个渠道
    拥有」时注册——跨渠道同名不注册裸名，系统不裁决，由用户显式选复合名。"""
    _isolate_settings_db(monkeypatch, tmp_path)
    save_newapi_provider_channels(
        [
            {"provider": "aipdd", "upstreamKey": "sk-a"},
            {"provider": "ark", "upstreamKey": "sk-k"},
        ]
    )
    save_newapi_model_alias_mappings(
        {
            "DC-text-LLM": {
                "provider": "aipdd",
                "target": "glm-4.6",
                "capability": "text",
            }
        }
    )
    save_upstream_models_snapshot("aipdd", ["glm-4.6", "ap-exclusive"])
    save_upstream_models_snapshot("ark", ["glm-4.6", "doubao-exclusive"])

    aipdd = model_gateway_settings.build_channel_registrations("aipdd")
    assert aipdd["DC-text-LLM"] == "glm-4.6"  # 内部绑定原样注册裸名
    assert aipdd["aipdd::glm-4.6"] == "glm-4.6"  # 复合名总是注册
    assert "glm-4.6" not in aipdd  # 双渠道同名：裸名不注册
    assert aipdd["ap-exclusive"] == "ap-exclusive"  # 单渠道拥有：裸名保留
    assert aipdd["aipdd::ap-exclusive"] == "ap-exclusive"

    ark = model_gateway_settings.build_channel_registrations("ark")
    assert ark["ark::glm-4.6"] == "glm-4.6"
    assert "glm-4.6" not in ark
    assert ark["doubao-exclusive"] == "doubao-exclusive"
    assert ark["ark::doubao-exclusive"] == "doubao-exclusive"


def test_build_alias_channel_roles_maps_registered_call_names_to_channels(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    """[aipdd fork delta] 拍平：roles 键 = 注册调用名（复合名 / 内部绑定裸名 /
    单渠道唯一裸名），值 = 归属渠道；没有备援，backup 恒 False。"""
    _isolate_settings_db(monkeypatch, tmp_path)
    save_newapi_provider_channels(
        [
            {"provider": "aipdd", "upstreamKey": "sk-a"},
            {"provider": "ark", "upstreamKey": "sk-k"},
        ]
    )
    save_upstream_models_snapshot("aipdd", ["glm-4.6"])
    save_upstream_models_snapshot("ark", ["glm-4.6", "doubao-vision"])
    save_newapi_model_alias_mappings(
        {
            "DC-text-LLM": {
                "provider": "aipdd",
                "target": "glm-4.6",
                "capability": "text",
            }
        }
    )

    assert model_gateway_settings.build_alias_channel_roles() == {
        # 内部绑定：裸名注册，归属定义它的渠道。
        "DC-text-LLM": {
            "provider": "aipdd",
            "target": "DC-text-LLM",
            "capability": "",
            "backup": False,
        },
        # 同名 glm-4.6 双渠道：各自一条复合名（裸名不注册）。
        "aipdd::glm-4.6": {
            "provider": "aipdd",
            "target": "aipdd::glm-4.6",
            "capability": "",
            "backup": False,
        },
        "ark::glm-4.6": {
            "provider": "ark",
            "target": "ark::glm-4.6",
            "capability": "",
            "backup": False,
        },
        # 单渠道持有：裸名 + 复合名都在。
        "doubao-vision": {
            "provider": "ark",
            "target": "doubao-vision",
            "capability": "",
            "backup": False,
        },
        "ark::doubao-vision": {
            "provider": "ark",
            "target": "ark::doubao-vision",
            "capability": "",
            "backup": False,
        },
    }


def test_alias_projection_payload_uses_flattened_registrations_without_priority(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    """[aipdd fork delta] 拍平：投影载荷 = 渠道完整注册表（内部绑定 + 复合名 +
    单渠道唯一裸名），priority 不再由别名归属推导（主 10 / 备 0 已退役）。"""
    _isolate_settings_db(monkeypatch, tmp_path)
    save_newapi_provider_channels(
        [
            {"provider": "aipdd", "upstreamKey": "sk-a"},
            {"provider": "deepseek", "upstreamKey": "sk-d"},
        ]
    )
    # [req40] 注册表只从渠道目录编译：aipdd 同步过目录才谈得上实名注册。
    save_upstream_models_snapshot("aipdd", ["glm-4.6"])
    client, payloads = _gateway_client(monkeypatch)

    response = client.post(
        "/model-gateway/custom/newapi/alias-mappings",
        json={
            "mappings": {
                "DC-text-LLM": {"provider": "aipdd", "target": "glm-4.6"}
            },
            "validate": False,
        },
    )
    assert response.status_code == 200
    failed = response.json()["data"]["projectionFailed"]
    assert failed == []
    by_name = {payload["channel"]["name"]: payload["channel"] for payload in payloads}
    # 未显式设过优先级：不推导，恒 0。
    assert by_name["DC-aipdd"]["priority"] == 0
    # 注册表 = 内部绑定 + 复合名 + 单渠道唯一裸名（glm-4.6 仅 aipdd 拥有）。
    assert json.loads(by_name["DC-aipdd"]["model_mapping"]) == {
        "DC-text-LLM": "glm-4.6",
        "aipdd::glm-4.6": "glm-4.6",
        "glm-4.6": "glm-4.6",
    }
    # deepseek 无任何绑定：空注册表被跳过（不提交空 mapping），也不参与裁决。
    assert "DC-deepseek" not in by_name
    provisioner = client.get("/model-gateway/config").json()["data"]["provisioner"]
    roles = provisioner["aliasChannelRoles"]
    assert roles["DC-text-LLM"]["provider"] == "aipdd"
    assert roles["DC-text-LLM"]["backup"] is False
    assert roles["aipdd::glm-4.6"]["provider"] == "aipdd"


def test_prune_legacy_alias_fallback_fields_strips_backup_keys(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    """[aipdd fork delta] 拍平迁移本地步：真源里的备援字段被清理、主位保留。"""
    _isolate_settings_db(monkeypatch, tmp_path)
    model_gateway_settings._write_many(
        {
            MODEL_ALIAS_MAPPINGS_SETTING_KEY: json.dumps(
                {
                    "DC-a-LLM": {
                        "provider": "aipdd",
                        "target": "glm-4.6",
                        "fallbackProvider": "volcengine",
                        "fallbackTarget": "doubao-seed-2-1-turbo-260628",
                    },
                    # 无备援字段的条目不动。
                    "DC-b-LLM": {"provider": "aipdd", "target": "glm-4.6"},
                },
                ensure_ascii=False,
            )
        }
    )

    assert model_gateway_settings.prune_legacy_alias_fallback_fields() == {
        "pruned": 1
    }
    saved = get_newapi_model_alias_mappings()
    assert saved["DC-a-LLM"] == {"provider": "aipdd", "target": "glm-4.6"}
    assert saved["DC-b-LLM"] == {"provider": "aipdd", "target": "glm-4.6"}
    # 幂等：第二次跑没有可清理项，零写入。
    assert model_gateway_settings.prune_legacy_alias_fallback_fields() == {
        "pruned": 0
    }


def _patch_flat_migration_provisioner(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(newapi_provisioner, "provisioner_enabled", lambda: True)
    monkeypatch.setattr(
        newapi_provisioner, "get_provisioner_config", lambda **kwargs: None
    )
    monkeypatch.setattr(
        newapi_provisioner, "ensure_admin_access_token", lambda cfg: None
    )


def test_project_flat_registrations_migration_projects_and_marks(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    """[aipdd fork delta] 拍平迁移远程步：存量渠道全量重投影为拍平注册表
    （复合名 + 单渠道唯一裸名），comfyui 不参与，全部成功后写完成标记。"""
    _isolate_settings_db(monkeypatch, tmp_path)
    save_newapi_provider_channels(
        [
            {"provider": "aipdd", "upstreamKey": "sk-aipdd"},
            {"provider": "volcengine", "type": 45, "upstreamKey": "sk-ark"},
        ]
    )
    # comfyui 存档有工作流校验开销，这里直接换投影入口的渠道列表（覆盖跳过分支）。
    monkeypatch.setattr(
        newapi_provisioner,
        "get_newapi_provider_channels",
        lambda: [
            {"provider": "aipdd"},
            {"provider": "volcengine"},
            {"provider": "comfyui"},
        ],
    )
    save_upstream_models_snapshot("aipdd", ["glm-4.6"])
    save_upstream_models_snapshot("volcengine", ["doubao-vision"])
    _patch_flat_migration_provisioner(monkeypatch)
    calls: list[dict] = []

    def fake_upsert(cfg, admin, payload):
        calls.append(payload["channel"])
        return {"ok": True, "action": "update"}

    monkeypatch.setattr(newapi_provisioner, "upsert_channel", fake_upsert)

    result = newapi_provisioner.project_flat_registrations_migration()

    assert result["migrated"] is True
    # comfyui 无 OpenAI 兼容清单，不参与拍平投影。
    assert result["providers"] == 2
    by_name = {channel["name"]: channel for channel in calls}
    assert set(by_name) == {"DC-aipdd", "DC-volcengine"}
    assert json.loads(by_name["DC-aipdd"]["model_mapping"]) == {
        "aipdd::glm-4.6": "glm-4.6",
        "glm-4.6": "glm-4.6",
    }
    assert json.loads(by_name["DC-volcengine"]["model_mapping"]) == {
        "volcengine::doubao-vision": "doubao-vision",
        "doubao-vision": "doubao-vision",
    }
    # 凭据 / 类型沿用渠道存档，不被迁移改写。
    assert by_name["DC-aipdd"]["key"] == "sk-aipdd"
    assert model_gateway_settings.flat_registration_migration_done() is True
    # 幂等：标记生效后再次启动直接跳过，不再打网关。
    assert newapi_provisioner.project_flat_registrations_migration() == {
        "skipped": "already-migrated"
    }
    assert len(calls) == 2


def test_project_flat_registrations_migration_retries_until_all_succeed(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    """[aipdd fork delta] 拍平迁移：任一渠道投影失败不写完成标记，下次启动重试。"""
    _isolate_settings_db(monkeypatch, tmp_path)
    save_newapi_provider_channels(
        [
            {"provider": "aipdd", "upstreamKey": "sk-aipdd"},
            {"provider": "volcengine", "type": 45, "upstreamKey": "sk-ark"},
        ]
    )
    save_upstream_models_snapshot("aipdd", ["glm-4.6"])
    save_upstream_models_snapshot("volcengine", ["doubao-vision"])
    _patch_flat_migration_provisioner(monkeypatch)

    def flaky_upsert(cfg, admin, payload):
        if payload["channel"]["name"] == "DC-volcengine":
            return {"ok": False, "action": "update"}
        return {"ok": True, "action": "update"}

    monkeypatch.setattr(newapi_provisioner, "upsert_channel", flaky_upsert)

    first = newapi_provisioner.project_flat_registrations_migration()
    assert first["migrated"] is False
    assert [item["provider"] for item in first["failed"]] == ["volcengine"]
    assert model_gateway_settings.flat_registration_migration_done() is False

    monkeypatch.setattr(
        newapi_provisioner,
        "upsert_channel",
        lambda cfg, admin, payload: {"ok": True, "action": "update"},
    )
    second = newapi_provisioner.project_flat_registrations_migration()
    assert second["migrated"] is True
    assert model_gateway_settings.flat_registration_migration_done() is True
