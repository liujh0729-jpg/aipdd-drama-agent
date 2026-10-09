from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest


@pytest.mark.parametrize("edition", [None, "", "ce"])
def test_runtime_config_includes_stable_instance_id(monkeypatch, edition) -> None:
    from novelvideo.api.routes import config
    from novelvideo.shared import runtime_env

    monkeypatch.setattr(runtime_env, "load_project_dotenv", lambda override=False: None)
    if edition is None:
        monkeypatch.delenv("ST_EDITION", raising=False)
    else:
        monkeypatch.setenv("ST_EDITION", edition)
    monkeypatch.delenv("ST_CONTROL_PLANE_DSN", raising=False)

    app = FastAPI()
    app.include_router(config.router, prefix="/api/v1")
    client = TestClient(app)

    first = client.get("/api/v1/config")
    second = client.get("/api/v1/config")

    assert first.status_code == 200
    assert second.status_code == 200
    first_data = first.json()["data"]
    second_data = second.json()["data"]

    assert first_data["edition"] == "ce"
    assert first_data["auth_required"] is False
    assert isinstance(first_data["instance_id"], str)
    assert first_data["instance_id"]
    assert second_data["instance_id"] == first_data["instance_id"]


def _config_client(monkeypatch):
    from novelvideo.api.routes import config
    from novelvideo.shared import runtime_env

    monkeypatch.setattr(runtime_env, "load_project_dotenv", lambda override=False: None)
    monkeypatch.setenv("ST_EDITION", "ce")
    monkeypatch.delenv("ST_CONTROL_PLANE_DSN", raising=False)

    app = FastAPI()
    app.include_router(config.router, prefix="/api/v1")
    return TestClient(app)


def test_runtime_config_exposes_alias_mapping_health_summary(monkeypatch) -> None:
    """[aipdd fork delta] WP-B: 公开的配置端点只挂汇总，不泄露逐别名明细。"""
    from novelvideo import newapi_provisioner

    monkeypatch.setattr(
        newapi_provisioner,
        "peek_alias_mapping_health",
        lambda: {
            "status": "degraded",
            "checked": True,
            "source": "project",
            "total": 32,
            "unmapped": ["DC-a-LLM", "DC-b-LLM"],
            "staleTargets": ["gone-model"],
            "capabilityMismatch": [],
            "noChannel": ["comfyui"],
            "degraded": ["aipdd"],
            "aliases": {"DC-a-LLM": {"status": "unmapped", "target": ""}},
        },
    )

    data = _config_client(monkeypatch).get("/api/v1/config").json()["data"]

    health = data["alias_mapping_health"]
    assert health["checked"] is True
    assert health["status"] == "degraded"
    assert health["total"] == 32
    assert health["unmapped"] == 2
    assert health["staleTargets"] == 1
    assert health["capabilityMismatch"] == 0
    assert health["noChannel"] == ["comfyui"]
    assert "aliases" not in health


@pytest.mark.parametrize("failure", ["pending", "raise"])
def test_runtime_config_alias_health_never_breaks_healthcheck(monkeypatch, failure) -> None:
    """自检还没跑过 / 底层异常时，/api/v1/config（容器 HEALTHCHECK）必须仍 200。"""
    from novelvideo import newapi_provisioner

    if failure == "pending":
        monkeypatch.setattr(
            newapi_provisioner,
            "peek_alias_mapping_health",
            lambda: {"status": "pending", "checked": False},
        )
    else:

        def _boom() -> dict:
            raise RuntimeError("store unavailable")

        monkeypatch.setattr(newapi_provisioner, "peek_alias_mapping_health", _boom)

    response = _config_client(monkeypatch).get("/api/v1/config")

    assert response.status_code == 200
    health = response.json()["data"]["alias_mapping_health"]
    assert health["checked"] is False
    assert health["status"] == "pending"
