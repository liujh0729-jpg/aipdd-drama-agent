# SPDX-License-Identifier: Elastic-2.0
# Copyright (c) 2026 ClaymoreLab
"""[aipdd fork delta] POST /pipeline/plan 端点契约测试（薄壳委托 wiring）。

计划器本身的状态机由 ``tests/test_pipeline_planner.py`` 钉住；这里只验证端点作为
单一入口的委托语义：execute=false 只计划零副作用（检查点预览）、execute=true 才经
run_actions 发射并回传 fired、非法参数在委托前以 400 拒绝。
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


@pytest.fixture()
def plan_client(monkeypatch):
    from novelvideo.api.auth import get_api_user
    from novelvideo.api.routes import pipeline

    async def fake_resolve(project, user, *, required_role="viewer", **_kw):
        return None

    monkeypatch.setattr(pipeline, "resolve_project_scope", fake_resolve)

    app = FastAPI()
    app.include_router(pipeline.router, prefix="/api/v1")
    app.dependency_overrides[get_api_user] = lambda: {"id": "u-alice", "username": "alice"}
    return TestClient(app)


def _patch_planner(monkeypatch, plan, fired=None):
    from novelvideo.pipeline import planner

    run_calls: list[list] = []

    async def fake_plan_next(read, project, opts):
        fake_plan_next.opts = opts
        if isinstance(plan, Exception):
            raise plan
        return plan

    async def fake_run_actions(call, actions):
        run_calls.append(actions)
        return fired if fired is not None else []

    monkeypatch.setattr(planner, "plan_next", fake_plan_next)
    monkeypatch.setattr(planner, "run_actions", fake_run_actions)
    fake_plan_next.run_calls = run_calls
    return fake_plan_next


def test_plan_execute_false_plans_without_firing(monkeypatch, plan_client):
    plan = {
        "ok": True,
        "stage": "story_saved",
        "status": "planned",
        "actions": [
            {"method": "PUT", "path": "/api/v1/projects/p/story/source", "body": {"text": "x"}}
        ],
    }
    stub = _patch_planner(monkeypatch, plan)
    r = plan_client.post("/api/v1/projects/p/pipeline/plan", json={"execute": False})
    assert r.status_code == 200
    data = r.json()["data"]
    assert data["stage"] == "story_saved"
    assert "fired" not in data  # 只计划不发射
    assert stub.run_calls == []  # run_actions 从未被调用


def test_plan_execute_true_attaches_fired(monkeypatch, plan_client):
    plan = {
        "ok": True,
        "stage": "episode_1_script_generating",
        "status": "planned",
        "actions": [{"method": "POST", "path": "/api/v1/projects/p/episodes/1/story/to-script"}],
    }
    fired = [{"method": "POST", "path": ".../to-script", "ok": True, "task_key": "t1"}]
    stub = _patch_planner(monkeypatch, plan, fired=fired)
    r = plan_client.post("/api/v1/projects/p/pipeline/plan", json={"execute": True})
    assert r.status_code == 200
    data = r.json()["data"]
    assert data["fired"] == fired
    assert stub.run_calls == [plan["actions"]]  # 动作被原样交给发射器


def test_plan_execute_true_marks_failed_on_fire_error(monkeypatch, plan_client):
    """发射失败必须回写 status=failed：否则 MCP auto 循环会把它当作推进成功空耗。"""
    plan = {
        "ok": True,
        "stage": "episode_1_script_generating",
        "status": "planned",
        "actions": [{"method": "POST", "path": ".../to-script"}],
    }
    fired = [
        {
            "method": "POST",
            "path": ".../to-script",
            "ok": False,
            "status_code": 400,
            "error": "模型不可用",
        }
    ]
    _patch_planner(monkeypatch, plan, fired=fired)
    r = plan_client.post("/api/v1/projects/p/pipeline/plan", json={"execute": True})
    assert r.status_code == 200
    data = r.json()["data"]
    assert data["status"] == "failed"
    assert data["error"] == "模型不可用"


def test_plan_deferred_story_version_not_counted_as_fire_failure(monkeypatch, plan_client):
    """类型化故事版步（deferred）由客户端自跑，不计入服务端发射失败。"""
    plan = {
        "ok": True,
        "stage": "episode_1_script_generating",
        "status": "planned",
        "actions": [
            {"method": "POST", "path": ".../to-script"},
            {"kind": "story_version", "episode": 1},
        ],
    }
    fired = [
        {"method": "POST", "path": ".../to-script", "ok": True, "task_key": "t"},
        {"kind": "story_version", "ok": False, "deferred": True},
    ]
    _patch_planner(monkeypatch, plan, fired=fired)
    r = plan_client.post("/api/v1/projects/p/pipeline/plan", json={"execute": True})
    data = r.json()["data"]
    assert data["status"] == "planned"
    assert "error" not in data


def test_plan_passes_opts_to_planner(monkeypatch, plan_client):
    plan = {"ok": True, "stage": "done", "status": "done", "actions": []}
    stub = _patch_planner(monkeypatch, plan)
    plan_client.post(
        "/api/v1/projects/p/pipeline/plan",
        json={"mode": "auto", "execute": True, "episode": 3, "aspect_ratio": "16:9"},
    )
    opts = stub.opts
    assert opts["episode"] == 3 and opts["aspect_ratio"] == "16:9"
    # 可选字段以 exclude_none 精简，不塞入 None 噪音
    assert "story_text" not in opts


def test_plan_rejects_bad_input_with_400(monkeypatch, plan_client):
    _patch_planner(monkeypatch, ValueError("aspect_ratio must be '2:3' or '16:9'"))
    r = plan_client.post("/api/v1/projects/p/pipeline/plan", json={"aspect_ratio": "4:3"})
    assert r.status_code == 400
    assert "aspect_ratio" in r.text
