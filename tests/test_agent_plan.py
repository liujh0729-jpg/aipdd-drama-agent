# SPDX-License-Identifier: Elastic-2.0
# Copyright (c) 2026 ClaymoreLab
"""[aipdd fork delta] Agent 待办计划测试：存储 / 清理规则 / 工具 / 系统提示 / API 端点。

计划落在项目目录（``<project_dir>/freezone/agent_plan.json``），测试用 tmp_path
伪造项目目录；API 端点用假 ``resolve_project_scope`` 解析项目。
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from novelvideo.agent import plans as agent_plans
from novelvideo.agent import tools as agent_tools
from novelvideo.agent.routes import require_api_user

USER = {"id": "u-alice", "username": "alice"}


@pytest.fixture()
def project_dir(tmp_path):
    path = tmp_path / "proj"
    (path / "freezone").mkdir(parents=True)
    return path


@pytest.fixture()
def plan_client(project_dir, monkeypatch):
    from novelvideo.agent.routes import router

    import novelvideo.api.deps as api_deps

    async def fake_resolve(project, user, *, required_role="viewer", media_read=False):
        return SimpleNamespace(project_dir=project_dir)

    monkeypatch.setattr(api_deps, "resolve_project_scope", fake_resolve)
    app = FastAPI()
    app.include_router(router, prefix="/api/v1")
    app.dependency_overrides[require_api_user] = lambda: dict(USER)
    return TestClient(app)


# ---------------------------------------------------------------------------
# 存储：增 / 改 / 删 / 匹配
# ---------------------------------------------------------------------------


def test_add_items_and_dedupe_pending(project_dir) -> None:
    items, added = agent_plans.add_items(project_dir, ["研读剧本", " 研读剧本 ", "拆分分镜"])
    assert [item["title"] for item in added] == ["研读剧本", "拆分分镜"]
    assert all(item["id"].startswith("plan_") for item in added)
    assert all(item["done"] is False and item["done_at"] is None for item in added)
    assert [item["title"] for item in items] == ["研读剧本", "拆分分镜"]

    # 未完成任务同名去重
    _items, again = agent_plans.add_items(project_dir, ["研读剧本"])
    assert again == []

    # 完成后同名任务可以再来一轮
    agent_plans.complete_items(project_dir, ["研读剧本"])
    _items, third = agent_plans.add_items(project_dir, ["研读剧本"])
    assert len(third) == 1


def test_complete_by_id_and_title(project_dir) -> None:
    items, added = agent_plans.add_items(project_dir, ["A 任务", "B 任务", "C 任务"])
    by_id = added[0]["id"]

    kept, completed, missing = agent_plans.complete_items(project_dir, [by_id, "b 任务", "不存在"])
    assert [item["id"] for item in completed] == [added[0]["id"], added[1]["id"]]
    assert missing == ["不存在"]
    done_status = {item["id"]: item["done"] for item in kept}
    assert done_status[added[0]["id"]] is True and done_status[added[2]["id"]] is False
    # 重复勾掉不报错、不重复计数
    _kept, again, _missing = agent_plans.complete_items(project_dir, [by_id])
    assert again == []


def test_update_and_remove_item(project_dir) -> None:
    _items, added = agent_plans.add_items(project_dir, ["旧标题"], source="user")
    item_id = added[0]["id"]

    updated = agent_plans.update_item(project_dir, item_id, title="新标题", done=True)
    assert updated is not None and updated["title"] == "新标题" and updated["done"] is True
    assert updated["done_at"]

    reopened = agent_plans.update_item(project_dir, item_id, done=False)
    assert reopened is not None and reopened["done"] is False and reopened["done_at"] is None

    with pytest.raises(ValueError):
        agent_plans.update_item(project_dir, item_id, title="   ")
    assert agent_plans.update_item(project_dir, "plan_missing", title="x") is None

    _kept, removed, missing = agent_plans.remove_items(project_dir, ["plan_missing"])
    assert removed == [] and missing == ["plan_missing"]

    assert agent_plans.remove_item(project_dir, item_id) is True
    assert agent_plans.remove_item(project_dir, item_id) is False
    assert agent_plans.load_plan(project_dir) == []


def test_remove_by_title_match(project_dir) -> None:
    _items, added = agent_plans.add_items(project_dir, ["删除我", "留下"])
    kept, removed, missing = agent_plans.remove_items(project_dir, ["删除我"])
    assert [item["title"] for item in removed] == ["删除我"]
    assert missing == []
    assert [item["title"] for item in kept] == ["留下"]


# ---------------------------------------------------------------------------
# 清理规则：超过 30 项时删掉已完成的（最早完成的先删；未完成的永不删）
# ---------------------------------------------------------------------------


def test_purge_removes_oldest_completed_first(project_dir) -> None:
    _items, added = agent_plans.add_items(
        project_dir, [f"任务{i}" for i in range(agent_plans.PLAN_MAX_ITEMS)]
    )
    assert len(added) == agent_plans.PLAN_MAX_ITEMS
    # 完成前三项，并手工把完成时间拉开（任务0 最早完成，任务2 最晚）
    for item in added[:3]:
        agent_plans.complete_items(project_dir, [item["id"]])
    stored = agent_plans.load_plan(project_dir)
    for index, item in enumerate(added[:3]):
        target = next(row for row in stored if row["id"] == item["id"])
        target["done_at"] = f"2026-01-0{index + 1}T00:00:00+00:00"
    agent_plans.save_plan(project_dir, stored)

    # 再加一项触发超容量清理：删掉最早完成的「任务0」
    agent_plans.add_items(project_dir, ["新任务"])
    final = agent_plans.load_plan(project_dir)
    assert len(final) == agent_plans.PLAN_MAX_ITEMS
    done_titles = [item["title"] for item in final if item["done"]]
    assert done_titles == ["任务1", "任务2"]


def test_purge_keeps_pending_over_limit(project_dir) -> None:
    total = agent_plans.PLAN_MAX_ITEMS + 5
    agent_plans.add_items(project_dir, [f"任务{i}" for i in range(total)])
    final = agent_plans.load_plan(project_dir)
    # 全是未完成任务：宁超容量也不丢用户的事
    assert len(final) == total


# ---------------------------------------------------------------------------
# 系统提示块
# ---------------------------------------------------------------------------


def test_build_plan_block(project_dir) -> None:
    assert agent_plans.build_plan_block(project_dir) == ""
    _items, added = agent_plans.add_items(project_dir, ["写剧本"])
    agent_plans.complete_items(project_dir, [added[0]["id"]])
    agent_plans.add_items(project_dir, ["画分镜"])

    block = agent_plans.build_plan_block(project_dir)
    assert "共 2 项，未完成 1 项" in block
    assert f"[x] {added[0]['id']} 写剧本" in block
    assert "agent_update_plan" in block


# ---------------------------------------------------------------------------
# 工具：agent_update_plan
# ---------------------------------------------------------------------------


def _tool_context(project_dir):
    return agent_tools.ToolContext(username="alice", project="proj", project_dir=project_dir)


def test_builtin_tool_schemas_include_plan_tool() -> None:
    names = [item["function"]["name"] for item in agent_tools.builtin_tool_schemas()]
    assert agent_tools.TOOL_UPDATE_PLAN in names


async def test_tool_update_plan_roundtrip(project_dir) -> None:
    context = _tool_context(project_dir)

    added = await agent_tools.execute_builtin_tool(
        agent_tools.TOOL_UPDATE_PLAN,
        {"action": "add", "items": ["研读剧本", "拆分分镜"]},
        context,
    )
    assert added is not None and added.ok is True
    assert "已新增 2 项待办" in added.content
    assert "研读剧本" in added.content

    stored = agent_plans.load_plan(project_dir)
    first_id = stored[0]["id"]

    completed = await agent_tools.execute_builtin_tool(
        agent_tools.TOOL_UPDATE_PLAN,
        {"action": "complete", "items": [first_id]},
        context,
    )
    assert completed is not None and completed.ok is True
    assert "已勾掉 1 项" in completed.content
    assert "未匹配到任务" not in completed.content

    removed = await agent_tools.execute_builtin_tool(
        agent_tools.TOOL_UPDATE_PLAN,
        {"action": "remove", "items": ["拆分分镜"]},
        context,
    )
    assert removed is not None and removed.ok is True
    assert "已删除 1 项" in removed.content
    assert [item["title"] for item in agent_plans.load_plan(project_dir)] == ["研读剧本"]


async def test_tool_update_plan_guards(project_dir) -> None:
    no_project = agent_tools.ToolContext(username="alice")
    result = await agent_tools.execute_builtin_tool(
        agent_tools.TOOL_UPDATE_PLAN, {"action": "add", "items": ["x"]}, no_project
    )
    assert result is not None and result.ok is False
    assert "未绑定项目" in result.content

    context = _tool_context(project_dir)
    empty = await agent_tools.execute_builtin_tool(
        agent_tools.TOOL_UPDATE_PLAN, {"action": "add", "items": ["  "]}, context
    )
    assert empty is not None and empty.ok is False

    bad_action = await agent_tools.execute_builtin_tool(
        agent_tools.TOOL_UPDATE_PLAN, {"action": "explode", "items": ["x"]}, context
    )
    assert bad_action is not None and bad_action.ok is False
    assert "不支持的 action" in bad_action.content


# ---------------------------------------------------------------------------
# 系统提示注入（engine）
# ---------------------------------------------------------------------------


def test_engine_system_prompt_injects_plan(project_dir, monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("NOVELVIDEO_AGENT_DATA_DIR", str(tmp_path / "agent-data"))
    agent_plans.add_items(project_dir, ["写第一集剧本"])

    from novelvideo.agent.engine import AgentEngine

    engine = AgentEngine(
        username="alice",
        conversation_id="aconv_x",
        project="proj",
        project_dir=project_dir,
        model="gw-text",
    )
    prompt = engine._system_prompt()
    assert "当前项目待办计划" in prompt
    assert "写第一集剧本" in prompt

    # 没有项目目录时不注入
    plain = AgentEngine(username="alice", conversation_id="aconv_x", model="gw-text")
    assert "当前项目待办计划" not in plain._system_prompt()


# ---------------------------------------------------------------------------
# API 端点
# ---------------------------------------------------------------------------


def test_api_plan_crud(plan_client) -> None:
    listed = plan_client.get("/api/v1/agent/plan", params={"project": "proj"})
    assert listed.status_code == 200
    assert listed.json()["data"] == {"items": [], "max_items": agent_plans.PLAN_MAX_ITEMS}

    assert plan_client.get("/api/v1/agent/plan").status_code == 400

    created = plan_client.post(
        "/api/v1/agent/plan/items", json={"project": "proj", "title": " 准备素材 "}
    )
    assert created.status_code == 200
    item = created.json()["data"]["item"]
    assert item["title"] == "准备素材" and item["source"] == "user"

    assert plan_client.post(
        "/api/v1/agent/plan/items", json={"project": "proj", "title": "准备素材"}
    ).status_code == 400
    assert plan_client.post(
        "/api/v1/agent/plan/items", json={"project": "proj", "title": "   "}
    ).status_code == 400

    toggled = plan_client.patch(
        f"/api/v1/agent/plan/items/{item['id']}", json={"project": "proj", "done": True}
    )
    assert toggled.status_code == 200
    assert toggled.json()["data"]["item"]["done"] is True
    assert toggled.json()["data"]["items"][0]["done"] is True

    renamed = plan_client.patch(
        f"/api/v1/agent/plan/items/{item['id']}", json={"project": "proj", "title": "备好素材"}
    )
    assert renamed.json()["data"]["item"]["title"] == "备好素材"

    assert plan_client.patch(
        f"/api/v1/agent/plan/items/{item['id']}", json={"project": "proj"}
    ).status_code == 400
    assert plan_client.patch(
        "/api/v1/agent/plan/items/plan_missing", json={"project": "proj", "done": True}
    ).status_code == 404

    deleted = plan_client.delete(
        f"/api/v1/agent/plan/items/{item['id']}", params={"project": "proj"}
    )
    assert deleted.status_code == 200 and deleted.json()["data"]["items"] == []
    assert plan_client.delete(
        f"/api/v1/agent/plan/items/{item['id']}", params={"project": "proj"}
    ).status_code == 404