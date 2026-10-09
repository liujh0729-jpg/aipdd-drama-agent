"""[aipdd fork delta] Tests for the automation MCP tools (thin delegation layer).

阶段状态机（故事→剧本→摄入/角色/分集→逐集身份→分镜脚本→草图→首帧）已从
MCP 插件下沉到服务端唯一逻辑源 ``novelvideo.pipeline.planner``（由
``tests/test_pipeline_planner.py`` 钉住）。本文件只验证插件作为薄壳的正确行为：
①aipdd_drama_auto_storyboard / run_pipeline 将推进委托到 POST /pipeline/plan，
  checkpoint 未 approve 时零副作用（execute=false）；②aipdd_drama_video_range 按
  分镜区间一次性并发提交镜头视频（缺帧跳过）；③注册/schema 自洽与区间解析。
"""

from __future__ import annotations

import importlib.util
from pathlib import Path


def _load_impl():
    impl_path = (
        Path(__file__).resolve().parents[1]
        / "src" / "novelvideo" / "chat" / "dramaclaw_tools.py"
    )
    spec = importlib.util.spec_from_file_location("test_auto_pipeline_impl", impl_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.tool_result = lambda value: value
    module.tool_error = lambda value: value
    return module


AUTO = "aipdd_drama_auto_storyboard"
RANGE = "aipdd_drama_video_range"
P = "/api/v1/projects/test"


def _fake_request(monkeypatch, plugin, routes, default=None):
    calls: list[dict] = []

    def _fake(method, path, *, query=None, body=None):
        calls.append({"method": method, "path": path, "query": query, "body": body})
        resp = routes.get((method, path), default)
        if resp is None:
            return {"ok": True}
        return resp

    monkeypatch.setattr(plugin, "_request", _fake)
    return calls


def _plan_step(**fields):
    """构造服务端 /pipeline/plan 返回的 PlanStep（委托测试用）。"""
    step = {"ok": True, "actions": []}
    step.update(fields)
    return step


def _stub_plan(plugin, monkeypatch, step, *, error=None):
    """stub 真实 HTTP：POST /pipeline/plan 返回给定 step（或失败），返回 calls。"""
    monkeypatch.setenv("DRAMACLAW_PROJECT_ID", "test")
    path = f"{P}/pipeline/plan"
    if error is not None:
        routes = {("POST", path): {"ok": False, "error": error}}
    else:
        routes = {("POST", path): {"ok": True, "data": step}}
    return _fake_request(monkeypatch, plugin, routes)


# ---------------------------------------------------------------------------
# 注册与 schema 自洽
# ---------------------------------------------------------------------------


def test_both_tools_registered_with_self_consistent_schemas(monkeypatch):
    plugin = _load_impl()
    tools = [item for item in plugin.TOOLS if item[0] in {AUTO, RANGE}]
    assert {item[0] for item in tools} == {AUTO, RANGE}
    assert len(tools) == 2
    for name, schema, handler in tools:
        assert schema["name"] == name
        assert schema["description"]
        assert callable(handler)
        params = schema["parameters"]
        for field in params["required"]:
            assert field in params["properties"], f"{name}: required {field} missing"
    by_name = {name: schema for name, schema, _ in tools}
    assert by_name[RANGE]["parameters"]["required"] == ["beats"]
    assert "aspect_ratio" in by_name[AUTO]["parameters"]["properties"]


def test_new_tools_require_main_api_credentials(monkeypatch):
    plugin = _load_impl()
    registered = []

    class FakeCtx:
        def register_tool(self, **kwargs):
            registered.append(kwargs)

    plugin.register(FakeCtx())
    matched = [kwargs for kwargs in registered if kwargs["name"] in {AUTO, RANGE}]
    assert {kwargs["name"] for kwargs in matched} == {AUTO, RANGE}
    for kwargs in matched:
        assert kwargs["check_fn"] is plugin._available
        assert kwargs["requires_env"] == ["DRAMACLAW_API_URL", "DRAMACLAW_AGENT_TOKEN"]


# ---------------------------------------------------------------------------
# 纯函数：区间解析 / 时长推导
# ---------------------------------------------------------------------------


def test_parse_beat_range_forms(monkeypatch):
    plugin = _load_impl()
    assert plugin._parse_beat_range("1-8") == list(range(1, 9))
    assert plugin._parse_beat_range("1,3,5-8") == [1, 3, 5, 6, 7, 8]
    assert plugin._parse_beat_range("5，3－4、6") == [3, 4, 5, 6]
    assert plugin._parse_beat_range([4, "2", "7-8"]) == [2, 4, 7, 8]
    assert plugin._parse_beat_range("8-5") == [5, 6, 7, 8]
    assert plugin._parse_beat_range(0) == []
    assert plugin._parse_beat_range(None) == []
    for bad in ("abc", "1-x"):
        try:
            plugin._parse_beat_range(bad)
            raise AssertionError(f"expected ValueError for {bad!r}")
        except ValueError:
            pass


# ---------------------------------------------------------------------------
# aipdd_drama_auto_storyboard：委托服务端计划器（单段推进）
# ---------------------------------------------------------------------------


def test_auto_storyboard_delegates_single_plan_call_with_execute(monkeypatch):
    plugin = _load_impl()
    step = _plan_step(stage="screenplay_started", status="planned")
    calls = _stub_plan(plugin, monkeypatch, step)
    result = plugin._handle_auto_storyboard(
        {"project_id": "test", "total_seconds": 300, "seconds_per_episode": 60, "story_style": ""}
    )
    plan_calls = [c for c in calls if c["path"] == f"{P}/pipeline/plan"]
    assert len(plan_calls) == 1
    assert plan_calls[0]["method"] == "POST"
    body = plan_calls[0]["body"]
    assert body["execute"] is True and body["mode"] == "manual"
    assert body["total_seconds"] == 300 and body["seconds_per_episode"] == 60
    # 空字符串参数被剔除，不污染请求体；故事版不由 MCP 请求。
    assert "story_style" not in body
    assert "include_story_version" not in body
    # 服务端 PlanStep 原样透传回工具结果。
    assert result["stage"] == "screenplay_started"


def test_auto_storyboard_passes_through_generation_params(monkeypatch):
    plugin = _load_impl()
    step = _plan_step(stage="episode_1_script_generating", status="planned", episode=1)
    calls = _stub_plan(plugin, monkeypatch, step)
    plugin._handle_auto_storyboard(
        {
            "project_id": "test",
            "episode": 1,
            "story_style": "anime",
            "aspect_ratio": "16:9",
            "model": "nanobanana",
            "style": "ink",
        }
    )
    body = next(c for c in calls if c["path"] == f"{P}/pipeline/plan")["body"]
    assert body["episode"] == 1
    assert body["story_style"] == "anime"
    assert body["aspect_ratio"] == "16:9" and body["model"] == "nanobanana" and body["style"] == "ink"


def test_auto_storyboard_surfaces_plan_error(monkeypatch):
    plugin = _load_impl()
    _stub_plan(plugin, monkeypatch, None, error="模型不可用")
    result = plugin._handle_auto_storyboard({"project_id": "test"})
    assert "模型不可用" in str(result)


# ---------------------------------------------------------------------------
# aipdd_drama_video_range：一次性并发提交
# ---------------------------------------------------------------------------


def _range_driver(plugin, monkeypatch, routes, args):
    monkeypatch.setenv("DRAMACLAW_PROJECT_ID", "test")
    calls = _fake_request(monkeypatch, plugin, routes)
    result = plugin._handle_video_range({"project_id": "test", **args})
    return result, calls


def test_video_range_classifies_submitted_and_skipped(monkeypatch):
    plugin = _load_impl()
    routes = {
        ("GET", f"{P}/episodes/1/beats"): {
            "ok": True,
            "data": [
                {"beat_number": 1, "frame_url": "/f1.png"},
                {"beat_number": 2, "frame_url": ""},
                {"beat_number": 3, "frame_url": "/f3.png"},
            ],
        },
        ("POST", f"{P}/episodes/1/beats/1/video"): {"ok": True, "task_id": "j1"},
        ("POST", f"{P}/episodes/1/beats/3/video"): {"ok": True, "task_id": "j3"},
    }
    result, calls = _range_driver(
        plugin,
        monkeypatch,
        routes,
        {"beats": "1-3", "model": "newapi_seedance-1.0-pro-fast", "resolution": "720p", "duration": 8},
    )
    assert result["ok"] is True
    assert [s["beat_number"] for s in result["submitted"]] == [1, 3]
    assert result["submitted"][0]["task_id"] == "j1"
    assert result["skipped"] and result["skipped"][0]["beat_number"] == 2
    assert "首帧" in result["skipped"][0]["reason"]
    assert result["errors"] == []
    posts = [c for c in calls if c["method"] == "POST"]
    assert len(posts) == 2
    assert posts[0]["body"] == {
        "video_backend": "newapi_seedance-1.0-pro-fast",
        "resolution": "720p",
        "duration": 8,
    }
    assert "single_video" in result["next_action"]


def test_video_range_reports_missing_and_backend_errors(monkeypatch):
    plugin = _load_impl()
    routes = {
        ("GET", f"{P}/episodes/2/beats"): {
            "ok": True,
            "data": [{"beat_number": 1, "frame_url": "/f1.png"}],
        },
        ("POST", f"{P}/episodes/2/beats/1/video"): {"ok": False, "error": "prompt is required"},
    }
    result, _ = _range_driver(plugin, monkeypatch, routes, {"episode": 2, "beats": [1, 9]})
    assert result["errors"][0]["beat_number"] == 1
    assert "prompt is required" in result["errors"][0]["error"]
    assert result["skipped"][0]["beat_number"] == 9
    assert result["submitted_count"] == 0


def test_video_range_requires_beats_and_valid_episode(monkeypatch):
    plugin = _load_impl()
    monkeypatch.setenv("DRAMACLAW_PROJECT_ID", "test")
    _fake_request(monkeypatch, plugin, {})
    missing = plugin._handle_video_range({"project_id": "test"})
    assert "beats is required" in str(missing)
    bad = plugin._handle_video_range({"project_id": "test", "beats": "x-y"})
    assert "invalid beat range" in str(bad)
    negative = plugin._handle_video_range({"project_id": "test", "episode": 0, "beats": "1"})
    assert "positive integer" in str(negative)

# ---------------------------------------------------------------------------
# [aipdd fork delta] aipdd_drama_regen_sketches：按镜头批量重生成草图
# ---------------------------------------------------------------------------


def _sketch_driver(plugin, monkeypatch, routes, args):
    monkeypatch.setenv("DRAMACLAW_PROJECT_ID", "test")
    calls = _fake_request(monkeypatch, plugin, routes)
    result = plugin._handle_regen_sketches({"project_id": "test", **args})
    return result, calls


def test_regen_sketches_posts_selected_beats_with_sketch_mode(monkeypatch):
    plugin = _load_impl()
    routes = {
        ("POST", f"{P}/episodes/1/sketches/regenerate"): {
            "ok": True,
            "task_type": "sketch_regen",
            "scope": "abc",
            "task_id": "j1",
        },
    }
    result, calls = _sketch_driver(plugin, monkeypatch, routes, {"episode": 1, "beat_indices": [1, 2, 3]})
    assert result["ok"] is True
    assert result["task_type"] == "sketch_regen"
    post = next(c for c in calls if c["method"] == "POST")
    assert post["path"] == f"{P}/episodes/1/sketches/regenerate"
    assert post["body"]["beat_indices"] == [1, 2, 3]
    assert post["body"]["mode_key"] == "1x1_2-3_sketch"
    # beat_indices given → no episode-wide GET resolution needed
    assert not any(c["method"] == "GET" for c in calls)


def test_regen_sketches_accepts_range_and_resolves_all_when_omitted(monkeypatch):
    plugin = _load_impl()
    routes = {
        ("GET", f"{P}/episodes/2/beats"): {
            "ok": True,
            "data": [{"beat_number": 1}, {"beat_number": 2}, {"beat_number": 3}],
        },
        ("POST", f"{P}/episodes/2/sketches/regenerate"): {"ok": True, "task_type": "sketch_regen"},
    }
    # range string normalizes through _parse_beat_range
    result, calls = _sketch_driver(plugin, monkeypatch, routes, {"episode": 2, "beats": "1-3"})
    post = next(c for c in calls if c["method"] == "POST")
    assert post["body"]["beat_indices"] == [1, 2, 3]
    assert result["ok"] is True
    # omit beats → resolve ALL episode beats
    result2, calls2 = _sketch_driver(plugin, monkeypatch, routes, {"episode": 2, "style": "ink"})
    post2 = next(c for c in calls2 if c["method"] == "POST")
    assert post2["body"]["beat_indices"] == [1, 2, 3]
    assert post2["body"]["style"] == "ink"


def test_regen_sketches_requires_episode_and_resolvable_beats(monkeypatch):
    plugin = _load_impl()
    monkeypatch.setenv("DRAMACLAW_PROJECT_ID", "test")
    _fake_request(monkeypatch, plugin, {})
    missing_ep = plugin._handle_regen_sketches({"project_id": "test", "beat_indices": [1]})
    assert "episode is required" in str(missing_ep)
    # no beats arg + empty GET resolution → cannot regenerate
    routes = {("GET", f"{P}/episodes/1/beats"): {"ok": True, "data": []}}
    _sketch_driver(plugin, monkeypatch, routes, {"episode": 1})
    empty = plugin._handle_regen_sketches({"project_id": "test", "episode": 1})
    assert "could not resolve beats" in str(empty)


# ---------------------------------------------------------------------------
# [aipdd fork delta] aipdd_drama_generate_video_prompts：按镜头批量生成视频提示词
# ---------------------------------------------------------------------------


def _vprompt_driver(plugin, monkeypatch, routes, args):
    monkeypatch.setenv("DRAMACLAW_PROJECT_ID", "test")
    calls = _fake_request(monkeypatch, plugin, routes)
    result = plugin._handle_generate_video_prompts({"project_id": "test", **args})
    return result, calls


def test_video_prompts_classify_submitted_skipped_errors(monkeypatch):
    plugin = _load_impl()
    routes = {
        ("POST", f"{P}/episodes/1/beats/1/video-prompt/generate"): {
            "ok": True,
            "task_type": "beat_video_prompt",
            "task_id": "t1",
        },
        ("POST", f"{P}/episodes/1/beats/2/video-prompt/generate"): {
            "ok": False,
            "status_code": 409,
            "error": "missing first frame",
        },
        ("POST", f"{P}/episodes/1/beats/3/video-prompt/generate"): {
            "ok": False,
            "status_code": 500,
            "error": "model unavailable",
        },
    }
    result, calls = _vprompt_driver(plugin, monkeypatch, routes, {"episode": 1, "beats": "1-3"})
    assert result["ok"] is True
    assert [s["beat_number"] for s in result["submitted"]] == [1]
    assert result["submitted"][0]["task_id"] == "t1"
    assert [s["beat_number"] for s in result["skipped"]] == [2]
    assert result["skipped"][0]["status_code"] == 409
    assert [e["beat_number"] for e in result["errors"]] == [3]
    assert result["submitted_count"] == 1
    posts = [c for c in calls if c["method"] == "POST"]
    assert len(posts) == 3
    assert posts[0]["body"] == {}
    assert "beat_video_prompt" in result["next_action"]


def test_video_prompts_resolves_all_beats_when_omitted(monkeypatch):
    plugin = _load_impl()
    routes = {
        ("GET", f"{P}/episodes/1/beats"): {
            "ok": True,
            "data": [{"beat_number": 1}, {"beat_number": 2}],
        },
    }
    result, calls = _vprompt_driver(plugin, monkeypatch, routes, {"episode": 1})
    assert result["requested"] == [1, 2]
    assert result["submitted_count"] == 2  # unmatched routes default to {"ok": True}
    assert not [e for e in result["errors"]]


def test_video_prompts_requires_resolvable_beats(monkeypatch):
    plugin = _load_impl()
    routes = {("GET", f"{P}/episodes/3/beats"): {"ok": True, "data": []}}
    result, _ = _vprompt_driver(plugin, monkeypatch, routes, {"episode": 3})
    assert "beats is required" in str(result)
    neg = plugin._handle_generate_video_prompts({"project_id": "test", "episode": 0, "beats": "1"})
    assert "positive integer" in str(neg)


# ---------------------------------------------------------------------------
# [aipdd fork delta] 编排层四工具（薄壳委托）：run_pipeline 三档模式 / get_run_status /
# cancel_run / suggest_config。run_pipeline 把推进委托到 POST /pipeline/plan，
# 测试钉住委托语义（checkpoint 未 approve 时 execute=false 零副作用）。
# ---------------------------------------------------------------------------

ORCH = "aipdd_drama_run_pipeline"
STATUS = "aipdd_drama_get_run_status"
CANCEL = "aipdd_drama_cancel_run"
SUGGEST = "aipdd_drama_suggest_config"


def test_orchestration_tools_registered_with_self_consistent_schemas():
    plugin = _load_impl()
    by_name = {item[0]: item for item in plugin.TOOLS}
    for name in (ORCH, STATUS, CANCEL, SUGGEST):
        assert name in by_name, name
        schema, handler = by_name[name][1], by_name[name][2]
        assert schema["name"] == name
        assert schema["description"]
        assert callable(handler)
        params = schema["parameters"]
        for field in params["required"]:
            assert field in params["properties"], f"{name}: required {field} missing"
    assert by_name[CANCEL][1]["parameters"]["required"] == ["episode"]
    mode_props = by_name[ORCH][1]["parameters"]["properties"]
    assert mode_props["mode"]["enum"] == ["manual", "checkpoint", "auto"]
    # 统一后 run_pipeline 绝不提交视频：悬空的 scope/分辨率等视频参数已从 schema 移除。
    assert "scope" not in mode_props
    assert "resolution" not in mode_props


def test_run_pipeline_checkpoint_plans_without_firing(monkeypatch):
    plugin = _load_impl()
    step = _plan_step(
        stage="story_saved",
        status="planned",
        actions=[{"method": "PUT", "path": f"{P}/story/source", "body": {"text": "x"}}],
    )
    calls = _stub_plan(plugin, monkeypatch, step)
    result = plugin._handle_run_pipeline({"project_id": "test", "mode": "checkpoint"})
    assert result["requires_approval"] is True
    assert result["next_stage"] == "story_saved"
    assert [r for r in result["planned_requests"] if r["method"] == "PUT"]
    # 未 approve 时只计划不发射：唯一一次 /pipeline/plan 调用 execute=False。
    assert len(calls) == 1 and calls[0]["body"]["execute"] is False


def test_run_pipeline_checkpoint_approve_executes(monkeypatch):
    plugin = _load_impl()
    step = _plan_step(stage="done", status="done")
    calls = _stub_plan(plugin, monkeypatch, step)
    result = plugin._handle_run_pipeline(
        {"project_id": "test", "mode": "checkpoint", "approve": True}
    )
    assert not result.get("requires_approval")
    assert result["storyboard_done"] is True
    assert calls[0]["body"]["execute"] is True


def test_run_pipeline_manual_advances_one_step(monkeypatch):
    plugin = _load_impl()
    step = _plan_step(stage="story_saved", status="planned")
    calls = _stub_plan(plugin, monkeypatch, step)
    result = plugin._handle_run_pipeline({"project_id": "test", "mode": "manual"})
    assert result["advanced"] == ["story_saved"]
    assert result["step"]["stage"] == "story_saved"
    assert "requires_approval" not in result
    assert calls[0]["body"]["execute"] is True and calls[0]["body"]["mode"] == "manual"


def test_run_pipeline_auto_loops_until_done(monkeypatch):
    plugin = _load_impl()
    monkeypatch.setenv("DRAMACLAW_PROJECT_ID", "test")
    steps = [
        {"ok": True, "stage": "screenplay_started", "status": "planned", "actions": []},
        {"ok": True, "stage": "done", "status": "done", "actions": []},
    ]
    seen: list[dict] = []

    def _fake(method, path, *, query=None, body=None):
        seen.append(body)
        return {"ok": True, "data": steps[min(len(seen) - 1, len(steps) - 1)]}

    monkeypatch.setattr(plugin, "_request", _fake)
    monkeypatch.setattr(
        plugin, "_wait_until_idle", lambda project, deadline, interval=2.0: True
    )
    result = plugin._handle_run_pipeline({"project_id": "test", "mode": "auto"})
    assert result["storyboard_done"] is True
    assert result["advanced"] == ["screenplay_started", "done"]
    # auto 每段都以 execute=True 发射；全程只委托 /pipeline/plan，绝不提交视频。
    assert all(b["execute"] is True for b in seen)
    assert all(b["mode"] == "auto" for b in seen)


def test_run_pipeline_rejects_unknown_mode(monkeypatch):
    plugin = _load_impl()
    monkeypatch.setenv("DRAMACLAW_PROJECT_ID", "test")
    calls = _fake_request(monkeypatch, plugin, {})
    result = plugin._handle_run_pipeline({"project_id": "test", "mode": "bogus"})
    assert "mode must be" in str(result)
    assert calls == []  # 校验在委托前，未发起任何请求


def test_get_run_status_classifies_tasks(monkeypatch):
    plugin = _load_impl()
    routes = {
        ("GET", f"{P}/pipeline/status"): {"ok": True, "data": {"progress": 0.6}},
        ("GET", f"{P}/tasks"): {
            "ok": True,
            "data": [
                {"task_type": "script_writer", "episode": 1, "status": "running", "progress": 0.5},
                {"task_type": "sketch_generation", "episode": 1, "status": "failed", "error": "boom"},
                {"task_type": "build_episodes", "episode": 0, "status": "completed"},
            ],
        },
        ("GET", f"{P}/episodes/1/beats"): {
            "ok": True,
            "data": {
                "beats": [
                    {"beat_number": 1, "sketch_url": "s", "frame_url": "f", "video_url": "v"},
                    {"beat_number": 2, "sketch_url": "s"},
                ]
            },
        },
    }
    monkeypatch.setenv("DRAMACLAW_PROJECT_ID", "test")
    _fake_request(monkeypatch, plugin, routes)
    result = plugin._handle_get_run_status({"project_id": "test", "episode": 1})
    assert [r["task_type"] for r in result["running"]] == ["script_writer"]
    assert result["failed"][0]["error"] == "boom"
    assert [c["task_type"] for c in result["completed"]] == ["build_episodes"]
    assert result["done"] is False
    assert result["counts"]["total_beats"] == 2
    assert result["counts"]["beats_with_frame"] == 1
    assert result["counts"]["beats_with_video"] == 1


def test_cancel_run_deletes_explicit_task_types(monkeypatch):
    plugin = _load_impl()
    monkeypatch.setenv("DRAMACLAW_PROJECT_ID", "test")
    calls = _fake_request(monkeypatch, plugin, {})
    result = plugin._handle_cancel_run(
        {"project_id": "test", "episode": 1, "task_types": ["script_writer"]}
    )
    deletes = [c for c in calls if c["method"] == "DELETE"]
    assert len(deletes) == 1
    assert deletes[0]["path"] == f"{P}/tasks/script_writer/1"
    assert result["cancelled"] == [{"task_type": "script_writer"}]
    assert result["auto_derived_task_types"] is False


def test_cancel_run_single_video_per_beat(monkeypatch):
    plugin = _load_impl()
    monkeypatch.setenv("DRAMACLAW_PROJECT_ID", "test")
    calls = _fake_request(monkeypatch, plugin, {})
    result = plugin._handle_cancel_run(
        {"project_id": "test", "episode": 2, "task_types": "single_video", "beats": "1-3"}
    )
    deletes = [c for c in calls if c["method"] == "DELETE"]
    assert len(deletes) == 3
    assert {d["query"]["beat_num"] for d in deletes} == {1, 2, 3}
    assert len(result["cancelled"]) == 3


def test_cancel_run_requires_episode(monkeypatch):
    plugin = _load_impl()
    monkeypatch.setenv("DRAMACLAW_PROJECT_ID", "test")
    _fake_request(monkeypatch, plugin, {})
    result = plugin._handle_cancel_run({"project_id": "test"})
    assert "episode" in str(result).lower()


def test_suggest_config_picks_first_available_models(monkeypatch):
    plugin = _load_impl()
    routes = {
        ("GET", f"{P}/freezone/image/models"): {
            "ok": True,
            "data": [{"id": "img-a"}, {"id": "img-b"}, {"id": "img-c"}],
        },
        ("GET", f"{P}/freezone/video/models"): {"ok": True, "data": ["vid-a", "vid-b"]},
    }
    monkeypatch.setenv("DRAMACLAW_PROJECT_ID", "test")
    _fake_request(monkeypatch, plugin, routes)
    result = plugin._handle_suggest_config({"project_id": "test"})
    rec = result["recommended"]
    assert rec["image_model"] == "img-a"
    assert rec["video_model"] == "vid-a"
    assert rec["ratio"] == "2:3"
    assert rec["resolution"] == "720x1280"
    assert result["alternatives"]["image_models"] == ["img-b", "img-c"]
    assert result["alternatives"]["video_models"] == ["vid-b"]
