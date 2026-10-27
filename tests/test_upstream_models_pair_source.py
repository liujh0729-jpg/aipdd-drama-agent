# [aipdd fork delta] 「在渠道卡填 base+key 就能拉通」这条链路的接线契约。
#
# 踩过的真实坑：用户在设置页填了新网关地址与新令牌，点「拉取模型」却收到
# ``Client error '401' for url 'https://<旧站点>/v1/models'``——报错里的域名根本
# 不是他输入的。原因不在拼 URL（裸域会归一成 /v1，实测两种写法等价），而在两处
# 接线：① 前端拉清单只传 provider，后端一律回查库里已存的 base+key，而库里那份
# 只有点「保存并同步」才更新；② 界面只回显掩码预览，预览串一旦被当成 Key 提交并
# 入库，后续每次拉取都是必死 401，且预览与用户手里那份看着一样，无从判定。
#
# 因此本文件守三件事：请求携带值优先、掩码值当场拦下、报错自曝「这次用的哪对、从哪来」。
from __future__ import annotations

import json
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from novelvideo import config, model_gateway_settings
from novelvideo.api.routes import model_gateway
from novelvideo.api.routes.model_gateway import channels as channels_route

UPSTREAM_URL = "/model-gateway/custom/newapi/upstream-models"

SAVED_BASE = "https://old-site.example/v1"
SAVED_KEY = "sk-old-site-secret"


@pytest.fixture(autouse=True)
def _register_local_auth_port():
    """model-gateway 路由组 router 级强制登录：单用户模式下 FileAuthPort 恒放行。"""
    from novelvideo.ports.local.auth import FileAuthPort
    from novelvideo.ports.registry import register_port

    register_port("auth", FileAuthPort())


@pytest.fixture()
def seeded(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> tuple[TestClient, dict[str, Any]]:
    """库里已有一条配置完整的 newapi 渠道；返回 (client, 实际发出的拉取参数)。"""
    monkeypatch.setattr(config, "STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("ST_EDITION", "ce")
    monkeypatch.delenv("ST_CONTROL_PLANE_DSN", raising=False)
    model_gateway_settings.save_newapi_provider_channels(
        [
            {
                "provider": "newapi",
                "baseUrl": SAVED_BASE,
                "upstreamKey": SAVED_KEY,
            }
        ]
    )

    captured: dict[str, Any] = {}

    def _fake_list(provider: str, **kwargs):  # noqa: ANN001, ANN003
        captured.clear()
        captured["provider"] = provider
        captured.update(kwargs)
        return [{"name": "ap-chat-1"}, {"name": "ap-image-1"}], None

    monkeypatch.setattr(channels_route, "list_upstream_catalog", _fake_list)
    app = FastAPI()
    app.include_router(model_gateway.router)
    return TestClient(app), captured


def test_route_prefers_values_carried_by_the_request(
    seeded: tuple[TestClient, dict[str, Any]],
) -> None:
    """填好新地址+新令牌、还没保存就点拉取：必须用表单这一对，而不是库里旧的。"""
    client, captured = seeded
    res = client.post(
        UPSTREAM_URL,
        json={
            "provider": "newapi",
            "baseUrl": "https://new-site.example/",
            "upstreamKey": "sk-brand-new-token",
        },
    )

    assert res.status_code == 200, res.text
    assert res.json()["data"]["models"] == ["ap-chat-1", "ap-image-1"]
    # 尾斜杠在入口就被剥掉（拉取时再由 resolve_models_endpoint 补 /v1）。
    assert captured["base_url"] == "https://new-site.example"
    assert captured["upstream_key"] == "sk-brand-new-token"


def test_saved_pair_used_when_request_carries_nothing(
    seeded: tuple[TestClient, dict[str, Any]],
) -> None:
    """只传 provider 时沿用库里那份：其它调用点依赖这个语义，不能被改坏。"""
    client, captured = seeded
    res = client.post(UPSTREAM_URL, json={"provider": "newapi"})

    assert res.status_code == 200, res.text
    assert captured["base_url"] == SAVED_BASE
    assert captured["upstream_key"] == SAVED_KEY


def test_masked_key_submitted_by_request_is_rejected(
    seeded: tuple[TestClient, dict[str, Any]],
) -> None:
    """把界面回显的掩码预览粘回来当 Key：当场拒，而不是发出去拿 401。"""
    client, captured = seeded
    res = client.post(
        UPSTREAM_URL,
        json={"provider": "newapi", "upstreamKey": "sk-os...cret"},
    )

    assert res.status_code == 400, res.text
    assert "掩码" in res.json()["detail"]
    assert captured == {}  # 一次都不该发出


def test_masked_key_already_saved_is_named(
    seeded: tuple[TestClient, dict[str, Any]],
) -> None:
    """历史脏数据（库里存的就是预览串）：报错直接点名要去重新粘贴完整 Key。

    写库口现在会拦掉掩码值，所以这里只能直接写 runtime_settings 复现旧数据。
    """
    client, captured = seeded
    model_gateway_settings._write_many(  # noqa: SLF001
        {
            "custom_newapi_provider_channels": json.dumps(
                [
                    {
                        "provider": "newapi",
                        "type": 0,
                        "upstreamKey": "sk-os...cret",
                        "baseUrl": SAVED_BASE,
                        "priority": 0,
                        "settings": {},
                    }
                ],
                ensure_ascii=False,
            )
        }
    )

    res = client.post(UPSTREAM_URL, json={"provider": "newapi"})

    assert res.status_code == 400, res.text
    assert "重新粘贴完整 Key" in res.json()["detail"]
    assert captured == {}


def test_failure_report_states_endpoint_and_value_source(
    seeded: tuple[TestClient, dict[str, Any]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """拉取失败必须自曝「打到哪个端点、用的是哪对、值从哪来」，否则无从判定。"""
    client, _captured = seeded

    def _fail(_provider: str, **_kwargs):  # noqa: ANN001, ANN003
        return [], "HTTP 401 [上游返回] 鉴权失败｜{\"error\":{\"message\":\"无效的令牌\"}}"

    monkeypatch.setattr(channels_route, "list_upstream_catalog", _fail)
    res = client.post(UPSTREAM_URL, json={"provider": "newapi"})

    assert res.status_code == 502, res.text
    detail = res.json()["detail"]
    assert f"{SAVED_BASE}/models" in detail
    # 「库内已存（改完需先保存）」这一句是用户判断「我输入的那对到底有没有生效」的关键。
    assert "库内已存" in detail
    assert "无效的令牌" in detail


def test_saving_masked_key_is_refused(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    """写库口也要拦：预览串入库后形状与真 Key 无异，只能靠规则识别。"""
    monkeypatch.setattr(config, "STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("ST_EDITION", "ce")
    monkeypatch.delenv("ST_CONTROL_PLANE_DSN", raising=False)
    model_gateway_settings.save_newapi_provider_channels(
        [
            {
                "provider": "newapi",
                "baseUrl": SAVED_BASE,
                "upstreamKey": SAVED_KEY,
            }
        ]
    )

    with pytest.raises(ValueError, match="掩码"):
        model_gateway_settings.save_newapi_provider_channels(
            [
                {
                    "provider": "newapi",
                    "baseUrl": "https://new-site.example",
                    "upstreamKey": "********",
                }
            ]
        )
    # 拒写不留副作用：库里仍是原来那对完整可用的 base+key（没被预览串、也没被新地址污染）。
    saved = model_gateway_settings.get_newapi_provider_channel("newapi") or {}
    assert saved.get("upstreamKey") == SAVED_KEY
    assert saved.get("baseUrl") == SAVED_BASE
