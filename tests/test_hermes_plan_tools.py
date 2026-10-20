"""[aipdd fork delta] Tests for the plan MCP tools: get_plan / update_plan / run_plan.

These three tools are the Agent-facing half of "用户设置工作计划 → Agent 读取计划并
自动完成对应工作". The HTTP side (plan storage, /plan/run, plan_runner) is pinned by
``tests/test_agent_plan.py`` and ``tests/test_agent_plan_runner.py``; this file verifies
the MCP layer stays a thin, faithful delegation:

① ``aipdd_drama_get_plan`` reads the project plan and tells the model which pending
   items are executable (have a stage); ② ``aipdd_drama_update_plan`` maps add/update/
   complete/remove onto the REST endpoints while preserving stage/episode; ③
   ``aipdd_drama_run_plan`` posts the run request with a timeout covering the budget.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

GET_PLAN = "aipdd_drama_get_plan"
UPDATE_PLAN = "aipdd_drama_update_plan"
RUN_PLAN = "aipdd_drama_run_plan"
PLAN = "/api/v1/agent/plan"


def _load_impl():
    impl_path = (
        Path(__file__).resolve().parents[1]
        / "src" / "novelvideo" / "chat" / "dramaclaw_tools.py"
    )
    spec = importlib.util.spec_from_file_location("test_plan_tools_impl", impl_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.tool_result = lambda value: value
    module.tool_error = lambda value: value
    return module


def _fake_request(monkeypatch, plugin, routes):
    calls: list[dict] = []

    def _fake(method, path, *, query=None, body=None, timeout=None):
        calls.append(
            {
                "method": method,
                "path": path,
                "query": query,
                "body": body,
                "timeout": timeout,
            }
        )
        resp = routes.get((method, path))
        if resp is None:
            return {"ok": True, "data": {}}
        return resp

    monkeypatch.setattr(plugin, "_request", _fake)
    return calls


def _plan(items):
    return {"ok": True, "data": {"items": items, "max_items": 30}}


ITEMS = [
    {"id": "plan_a", "title": "写第 1 集故事", "done": False, "stage": "story", "episode": 1},
    {"id": "plan_b", "title": "研读剧本", "done": False},
    {"id": "plan_c", "title": "生成第 1 集草图", "done": False, "stage": "sketches", "episode": 1},
]


# ---------------------------------------------------------------------------
# 注册与 schema 自洽
# ---------------------------------------------------------------------------


def test_plan_tools_registered_with_self_consistent_schemas():
    plugin = _load_impl()
    tools = [item for item in plugin.TOOLS if item[0] in {GET_PLAN, UPDATE_PLAN, RUN_PLAN}]
    assert {item[0] for item in tools} == {GET_PLAN, UPDATE_PLAN, RUN_PLAN}
    assert len(tools) == 3
    for name, schema, handler in tools:
        assert schema["name"] == name
        assert schema["description"]
        assert callable(handler)
        params = schema["parameters"]
        for field in params["required"]:
            assert field in params["properties"], f"{name}: required {field} missing"
    by_name = {name: schema for name, schema, _ in tools}
    assert by_name[UPDATE_PLAN]["parameters"]["required"] == ["action", "items"]
    assert by_name[UPDATE_PLAN]["parameters"]["properties"]["action"]["enum"] == [
        "add",
        "update",
        "complete",
        "remove",
    ]
    assert by_name[RUN_PLAN]["parameters"]["properties"]["mode"]["enum"] == [
        "auto",
        "manual",
        "checkpoint",
    ]


def test_plan_tools_require_main_api_credentials():
    plugin = _load_impl()
    registered = []

    class FakeCtx:
        def register_tool(self, **kwargs):
            registered.append(kwargs)

    plugin.register(FakeCtx())
    matched = [kwargs for kwargs in registered if kwargs["name"] in {GET_PLAN, UPDATE_PLAN, RUN_PLAN}]
    assert {kwargs["name"] for kwargs in matched} == {GET_PLAN, UPDATE_PLAN, RUN_PLAN}
    for kwargs in matched:
        assert kwargs["check_fn"] is plugin._available
        assert kwargs["requires_env"] == ["DRAMACLAW_API_URL", "DRAMACLAW_AGENT_TOKEN"]


# ---------------------------------------------------------------------------
# aipdd_drama_get_plan
# ---------------------------------------------------------------------------


def test_get_plan_returns_pending_and_executable(monkeypatch):
    plugin = _load_impl()
    monkeypatch.setenv("DRAMACLAW_PROJECT_ID", "test")
    calls = _fake_request(monkeypatch, plugin, {("GET", PLAN): _plan(ITEMS)})
    result = plugin._handle_get_plan({"project_id": "test"})
    assert calls == [
        {"method": "GET", "path": PLAN, "query": {"project": "test"}, "body": None, "timeout": None}
    ]
    assert [item["id"] for item in result["items"]] == ["plan_a", "plan_b", "plan_c"]
    assert result["pending"] == 3
    # 只有带 stage 的条目会被执行器自动推进；手工待办不在其中。
    assert result["executable_pending"] == ["plan_a", "plan_c"]
    assert "sketches" in result["stages"]
    assert "run_plan" in result["next_hint"]


def test_get_plan_uses_env_project_and_surfaces_errors(monkeypatch):
    plugin = _load_impl()
    calls = _fake_request(
        monkeypatch,
        plugin,
        {("GET", PLAN): {"ok": False, "status_code": 404, "error": "project not found"}},
    )
    # 没有 project_id 也没有环境变量：校验先于请求，直接返回错误。
    result = plugin._handle_get_plan({})
    assert "project_id is required" in str(result)
    assert calls == []

    monkeypatch.setenv("DRAMACLAW_PROJECT_ID", "envproj")
    calls = _fake_request(
        monkeypatch,
        plugin,
        {("GET", PLAN): {"ok": False, "status_code": 404, "error": "project not found"}},
    )
    assert "project not found" in str(plugin._handle_get_plan({}))
    assert calls[0]["query"] == {"project": "envproj"}

    calls = _fake_request(monkeypatch, plugin, {("GET", PLAN): _plan([])})
    plugin._handle_get_plan({})
    assert calls[0]["query"] == {"project": "envproj"}


# ---------------------------------------------------------------------------
# aipdd_drama_update_plan
# ---------------------------------------------------------------------------


def test_update_plan_add_writes_batch_preserving_stage(monkeypatch):
    plugin = _load_impl()
    added = [
        {"id": "plan_d", "title": "生成第 2 集草图", "stage": "sketches", "episode": 2, "done": False},
        {"id": "plan_e", "title": "研读剧本", "done": False},
    ]
    calls = _fake_request(
        monkeypatch,
        plugin,
        {
            ("POST", f"{PLAN}/items/batch"): {
                "ok": True,
                "data": {"added": added, "items": added, "max_items": 30},
            }
        },
    )
    result = plugin._handle_update_plan(
        {
            "project_id": "test",
            "action": "add",
            "items": [
                {"title": "生成第 2 集草图", "stage": "sketches", "episode": 2},
                "研读剧本",
            ],
        }
    )
    post = next(c for c in calls if c["method"] == "POST")
    assert post["path"] == f"{PLAN}/items/batch"
    assert post["body"] == {
        "project": "test",
        "items": [
            {"title": "生成第 2 集草图", "stage": "sketches", "episode": 2},
            {"title": "研读剧本"},
        ],
    }
    assert [item["id"] for item in result["added"]] == ["plan_d", "plan_e"]
    assert "run_plan" in result["next_hint"]


def test_update_plan_add_surfaces_backend_validation(monkeypatch):
    plugin = _load_impl()
    _fake_request(
        monkeypatch,
        plugin,
        {
            ("POST", f"{PLAN}/items/batch"): {
                "ok": False,
                "status_code": 400,
                "error": "invalid stage 'storyboard'",
            }
        },
    )
    result = plugin._handle_update_plan(
        {"project_id": "test", "action": "add", "items": [{"title": "x", "stage": "storyboard"}]}
    )
    assert "invalid stage" in str(result)


def test_update_plan_update_resolves_title_and_patches(monkeypatch):
    plugin = _load_impl()
    patched = {**ITEMS[2], "stage": "frames"}
    calls = _fake_request(
        monkeypatch,
        plugin,
        {
            ("GET", PLAN): _plan(ITEMS),
            ("PATCH", f"{PLAN}/items/plan_c"): {"ok": True, "data": {"item": patched}},
        },
    )
    result = plugin._handle_update_plan(
        {
            "project_id": "test",
            "action": "update",
            "items": [{"title": "生成第 1 集草图", "stage": "frames", "episode": 1}],
        }
    )
    patch = next(c for c in calls if c["method"] == "PATCH")
    assert patch["path"] == f"{PLAN}/items/plan_c"
    assert patch["body"] == {"project": "test", "title": "生成第 1 集草图", "stage": "frames", "episode": 1}
    assert result["updated"][0]["stage"] == "frames"
    assert result["missing"] == [] and result["errors"] == []


def test_update_plan_complete_and_remove(monkeypatch):
    plugin = _load_impl()
    calls = _fake_request(
        monkeypatch,
        plugin,
        {
            ("GET", PLAN): _plan(ITEMS),
            ("PATCH", f"{PLAN}/items/plan_a"): {"ok": True, "data": {"item": {**ITEMS[0], "done": True}}},
            ("DELETE", f"{PLAN}/items/plan_c"): {"ok": True, "data": {"items": []}},
        },
    )
    completed = plugin._handle_update_plan(
        {"project_id": "test", "action": "complete", "items": [{"id": "plan_a"}, "不存在的任务"]}
    )
    assert [item["id"] for item in completed["changed"]] == ["plan_a"]
    assert completed["missing"] == ["不存在的任务"]
    patch = next(c for c in calls if c["method"] == "PATCH")
    assert patch["body"] == {"project": "test", "done": True}

    calls.clear()
    removed = plugin._handle_update_plan(
        {"project_id": "test", "action": "remove", "items": ["生成第 1 集草图"]}
    )
    delete = next(c for c in calls if c["method"] == "DELETE")
    assert delete["path"] == f"{PLAN}/items/plan_c"
    assert delete["query"] == {"project": "test"}
    assert [item["id"] for item in removed["changed"]] == ["plan_c"]


def test_update_plan_guards(monkeypatch):
    plugin = _load_impl()
    monkeypatch.setenv("DRAMACLAW_PROJECT_ID", "test")
    calls = _fake_request(monkeypatch, plugin, {})
    empty = plugin._handle_update_plan({"project_id": "test", "action": "add", "items": ["  "]})
    assert "items 不能为空" in str(empty)
    bad = plugin._handle_update_plan({"project_id": "test", "action": "explode", "items": ["x"]})
    assert "action must be" in str(bad)
    assert calls == []


# ---------------------------------------------------------------------------
# aipdd_drama_run_plan
# ---------------------------------------------------------------------------


def test_run_plan_posts_budget_params_with_covering_timeout(monkeypatch):
    plugin = _load_impl()
    payload = {"status": "done", "advanced": ["story_saved"], "completed": ["plan_a"], "remaining": 0}
    calls = _fake_request(
        monkeypatch,
        plugin,
        {("POST", f"{PLAN}/run"): {"ok": True, "data": payload}},
    )
    result = plugin._handle_run_plan(
        {
            "project_id": "test",
            "mode": "auto",
            "budget_s": 300,
            "max_steps": 7,
            "story_text": "从前有座山。",
            "aspect_ratio": "16:9",
            "episode_count": 3,
        }
    )
    call = calls[0]
    assert call["path"] == f"{PLAN}/run"
    assert call["body"] == {
        "project": "test",
        "mode": "auto",
        "approve": False,
        "budget_s": 300.0,
        "episode_count": 3,
        "story_text": "从前有座山。",
        "aspect_ratio": "16:9",
        "max_steps": 7,
    }
    # HTTP 超时必须覆盖执行器预算，避免 urllib 先于执行器超时。
    assert call["timeout"] == 360.0
    assert result["project"] == "test"
    assert result["status"] == "done" and result["completed"] == ["plan_a"]


def test_run_plan_defaults_and_error_surface(monkeypatch):
    plugin = _load_impl()
    monkeypatch.setenv("DRAMACLAW_PROJECT_ID", "test")
    calls = _fake_request(
        monkeypatch,
        plugin,
        {("POST", f"{PLAN}/run"): {"ok": False, "status_code": 500, "error": "planner exploded"}},
    )
    result = plugin._handle_run_plan({})
    assert calls[0]["body"]["mode"] == "auto" and calls[0]["body"]["budget_s"] == 90.0
    assert calls[0]["timeout"] == 150.0
    assert "planner exploded" in str(result)


def test_run_plan_rejects_unknown_mode_before_request(monkeypatch):
    plugin = _load_impl()
    monkeypatch.setenv("DRAMACLAW_PROJECT_ID", "test")
    calls = _fake_request(monkeypatch, plugin, {})
    result = plugin._handle_run_plan({"project_id": "test", "mode": "bogus"})
    assert "mode must be" in str(result)
    assert calls == []
