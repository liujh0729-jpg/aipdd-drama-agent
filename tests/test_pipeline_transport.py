# SPDX-License-Identifier: Elastic-2.0
# Copyright (c) 2026 ClaymoreLab
"""[aipdd fork delta] 传输层契约测试：``asgi_caller`` 必须与 MCP ``_request`` 同形状。

为什么需要这一层：计划器 ``plan_next`` 的所有读法（``source["data"]["text"]``、
``_rows(resp, "tasks")`` …）都按「信封只有一层」的 MCP 形状编写。此前 asgi 在成功
分支把**整份路由响应**再塞进 ``data``，于是经 REST ``POST /pipeline/plan`` 的两个自动化
入口（前端 episode driver、MCP 工具）读到的是空载荷 —— 故事源明明有内容却被判
``requires_input``，自动化在第一道闸门原地结束（用户现象：按了按钮，身份/脚本/场景
什么都没提交，也不报错）。单测里的假 ``read`` 用的是 MCP 形状，因此从未暴露。

本文件用**真实 FastAPI app + 真实 asgi_caller**钉住两端形状一致，并钉住计划器经真实
自调用能读到载荷。改传输层或计划器任一侧形状时，这里必须一起改。
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI

from novelvideo.pipeline.planner import plan_next, run_actions
from novelvideo.pipeline.transport import asgi_caller

P = "/api/v1/projects/test"


def _add_route(app: FastAPI, method: str, path: str, payload: Any, status_code: int = 200) -> None:
    """注册一个只回固定 JSON 体的端点（可选非 2xx）。"""

    async def handler() -> Any:
        return payload

    app.add_api_route(
        path,
        handler,
        methods=[method],
        status_code=status_code,
        response_model=None,
    )


async def test_success_envelope_carries_single_data_level() -> None:
    """成功分支：路由响应体摊平，``data`` 直接就是业务载荷（与 MCP 一致）。"""
    app = FastAPI()
    _add_route(app, "GET", f"{P}/story/source", {"ok": True, "data": {"text": "从前有座山"}})

    res = await asgi_caller(app)("GET", f"{P}/story/source")

    assert res == {"ok": True, "status_code": 200, "data": {"text": "从前有座山"}}
    # 计划器就是按这一层读故事源的；多套一层时这里会变成 {ok, data} 而读不到 text。
    assert res["data"]["text"] == "从前有座山"


async def test_business_failure_in_http_200_is_not_reported_as_ok() -> None:
    """路由以 200 回 ``ok:false``（业务失败）必须如实传出去，不能被 HTTP 状态洗白。"""
    app = FastAPI()
    _add_route(app, "PUT", f"{P}/story/source", {"ok": False, "error": "故事内容为空"})

    res = await asgi_caller(app)("PUT", f"{P}/story/source", body={"text": ""})

    assert res["ok"] is False
    assert res["status_code"] == 200
    assert res["error"] == "故事内容为空"


async def test_transport_error_shape_keeps_error_text() -> None:
    app = FastAPI()
    _add_route(app, "GET", f"{P}/boom", {"detail": "Project 'x' not found"}, status_code=404)

    res = await asgi_caller(app)("GET", f"{P}/boom")

    assert res["ok"] is False and res["status_code"] == 404
    assert res["error"] == "Project 'x' not found"


# --------------------------------------------------------------------------- #
# 计划器经真实进程内自调用读进度（回归：自动化原地结束）
# --------------------------------------------------------------------------- #
def _project_app(
    *,
    story_text: str = "从前有座山",
    screenplay_status: str | None = "completed",
    episodes: list[dict[str, Any]] | None = None,
    tasks: list[dict[str, Any]] | None = None,
    beats: list[dict[str, Any]] | None = None,
) -> FastAPI:
    app = FastAPI()
    _add_route(app, "GET", f"{P}/story/source", {"ok": True, "data": {"text": story_text}})
    _add_route(
        app,
        "GET",
        f"{P}/story/generate-script/result",
        {"ok": True, "data": {"status": screenplay_status}},
    )
    _add_route(app, "GET", f"{P}/episodes", {"ok": True, "data": episodes or [{"number": 4}]})
    _add_route(app, "GET", f"{P}/tasks", {"ok": True, "data": tasks or []})
    _add_route(app, "GET", f"{P}/episodes/4/beats", {"ok": True, "data": beats or []})
    _add_route(app, "GET", f"{P}/freezone/video/character-library", {"ok": True, "data": {"items": []}})
    return app


async def test_planner_reads_story_source_over_real_asgi() -> None:
    """有故事源 + 剧本已完成 + 该集无 beats：必须推进到身份规划，而不是 requires_input。"""
    step = await plan_next(asgi_caller(_project_app()), "test", {"episode": 4})

    assert step["status"] != "requires_input", "故事源读空会让整条自动化在第一道闸门停住"
    assert step["stage"] == "episode_4_identities_planning"
    assert step["actions"][0]["path"] == f"{P}/episodes/4/identities/plan-async"


async def test_planner_sees_running_task_over_real_asgi() -> None:
    """任务行要读得到：该集脚本在跑时应回 running（等它），不能被当成没进度。"""
    app = _project_app(tasks=[{"task_type": "script_writer", "episode": 4, "status": "running"}])

    step = await plan_next(asgi_caller(app), "test", {"episode": 4})

    assert step["status"] == "running"
    assert step["stage"] == "episode_4_script_writer_running"


async def test_planner_plans_scene_then_prop_over_real_asgi() -> None:
    """该集已有 beats 且场景/道具菜单为空：打开资产规划后应先补场景，再补道具。"""
    app = _project_app(beats=[{"beat_number": 1, "sketch_url": "", "frame_url": ""}])

    step = await plan_next(asgi_caller(app), "test", {"episode": 4, "plan_episode_assets": True})
    assert step["stage"] == "episode_4_scenes_planning"
    assert step["actions"][0]["path"] == f"{P}/episodes/4/scenes/plan"

    app2 = _project_app(
        beats=[{"beat_number": 1, "sketch_url": "", "frame_url": ""}],
        episodes=[{"number": 4, "scene_menu": [{"name": "祠堂"}]}],
    )
    step2 = await plan_next(asgi_caller(app2), "test", {"episode": 4, "plan_episode_assets": True})
    assert step2["stage"] == "episode_4_props_planning"


async def test_run_actions_reports_fire_failure_over_real_asgi() -> None:
    """服务端发射遇业务失败必须回 ok:false，否则调用方会把失败当推进成功空耗。"""
    app = FastAPI()
    _add_route(app, "POST", f"{P}/episodes/4/identities/plan-async", {"ok": False, "error": "模型不可用"})

    fired = await run_actions(
        asgi_caller(app),
        [{"method": "POST", "path": f"{P}/episodes/4/identities/plan-async", "await_task": True}],
    )

    assert fired[0]["ok"] is False
    assert "模型不可用" in fired[0]["error"]
