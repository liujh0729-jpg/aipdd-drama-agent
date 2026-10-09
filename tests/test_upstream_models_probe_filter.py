# [aipdd fork delta] req33 §6.3：业务模型映射的下拉候选按实测三态收敛。
#
# ``/custom/newapi/upstream-models`` 原本把上游 ``/v1/models`` 全量吐给前端，
# 于是后台已经实测判过「没开通 / 不读图」的名字照样能配成映射——配上去就是死映射。
# 现在带 ``kind`` 时按对应那份实测缓存摘一刀；判据与设置页文本 / 视觉下拉完全一致：
# 只摘明确不可用的，unknown（限流 / 鉴权 / 没测过）一律保留，探测缓存坏了就全量放行。
from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from novelvideo import config
from novelvideo import model_gateway_settings
from novelvideo import text_model_probe
from novelvideo.api.routes import model_gateway
from novelvideo.api.routes.model_gateway import channels as channels_route

UPSTREAM_URL = "/model-gateway/custom/newapi/upstream-models"


@pytest.fixture(autouse=True)
def _register_local_auth_port():
    """model-gateway 路由组 router 级强制登录：单用户模式下 FileAuthPort 恒放行。"""
    from novelvideo.ports.local.auth import FileAuthPort
    from novelvideo.ports.registry import register_port

    register_port("auth", FileAuthPort())

LISTING = [
    "glm-4.6",              # 文本实测：not-granted
    "doubao-vision",        # 视觉实测：not-vision
    "doubao-seed-1-6",      # 视觉实测：unknown（限流 / 鉴权这类链路不行，不算结论）
    "ap-doubao-seed-2.1-turbo",  # 实测：ok
    "never-probed",         # 压根没进过实测
]


@pytest.fixture()
def seeded(monkeypatch: pytest.MonkeyPatch, tmp_path):
    monkeypatch.setattr(config, "STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("ST_EDITION", "ce")
    monkeypatch.delenv("ST_CONTROL_PLANE_DSN", raising=False)
    model_gateway_settings.save_newapi_provider_channels(
        [
            {
                "provider": "aipdd",
                "baseUrl": "https://api.aipdd.example/v1",
                "upstreamKey": "sk-aipdd-secret",
            }
        ]
    )
    model_gateway_settings.save_text_model_probe("aipdd", {"glm-4.6": "not-granted"})
    model_gateway_settings.save_vision_model_probe(
        "aipdd",
        {
            "glm-4.6": "not-granted",
            "doubao-vision": "not-vision",
            "doubao-seed-1-6": "unknown",
            "ap-doubao-seed-2.1-turbo": "ok",
        },
    )

    # [req40] 路由读的是结构化目录（名字只是它的一个视图），拦截点随之上移一层。
    def _fake_list(provider: str, **_kwargs):  # noqa: ANN001, ANN003
        return [{"name": name} for name in LISTING], None

    monkeypatch.setattr(channels_route, "list_upstream_catalog", _fake_list)
    app = FastAPI()
    app.include_router(model_gateway.router)
    return TestClient(app)


def test_upstream_models_vision_kind_drops_granted_and_vision_rejects(
    seeded: TestClient,
) -> None:
    """视觉下拉：not-granted 与 not-vision 都要摘，unknown 与没测过的照旧留着。"""
    body = seeded.post(UPSTREAM_URL, json={"provider": "aipdd", "kind": "vision"}).json()
    data = body["data"]

    assert data["models"] == [
        "ap-doubao-seed-2.1-turbo",
        "doubao-seed-1-6",
        "never-probed",
    ]
    assert data["count"] == len(data["models"])
    assert data["probeKind"] == "vision"
    assert data["droppedNotGranted"] == 2
    # 结构化目录与名字视图同进同退：前端下拉要按条目分组，不能各裁一半。
    assert [entry["name"] for entry in data["catalog"]] == data["models"]


def test_upstream_models_text_kind_only_drops_not_granted(
    seeded: TestClient,
) -> None:
    """文本下拉只看文本那份缓存：视觉判的 not-vision 不该反过来污染文本候选。"""
    data = seeded.post(UPSTREAM_URL, json={"provider": "aipdd", "kind": "text"}).json()["data"]

    assert "glm-4.6" not in data["models"]
    assert "doubao-vision" in data["models"]
    assert data["droppedNotGranted"] == 1


def test_upstream_models_without_kind_still_returns_raw_listing(
    seeded: TestClient,
) -> None:
    """不传 kind 就是改造前的行为：上游全量原样给回，一个都不裁。"""
    data = seeded.post(UPSTREAM_URL, json={"provider": "aipdd"}).json()["data"]

    assert data["models"] == sorted(LISTING)
    assert data["probeKind"] == ""
    assert data["droppedNotGranted"] == 0


def test_upstream_models_probe_failure_keeps_everything(
    seeded: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """探测缓存读不了时宁可多给，也不能把能用的名字藏起来。"""

    def boom() -> dict:
        raise RuntimeError("state 读不了")

    monkeypatch.setitem(text_model_probe._KINDS["vision"], "get", boom)  # noqa: SLF001
    data = seeded.post(UPSTREAM_URL, json={"provider": "aipdd", "kind": "vision"}).json()["data"]

    assert data["models"] == sorted(LISTING)
    assert data["droppedNotGranted"] == 0
