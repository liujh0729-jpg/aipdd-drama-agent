# SPDX-License-Identifier: Elastic-2.0
# Copyright (c) 2026 ClaymoreLab
"""[aipdd fork delta] 可执行工作计划：阶段推断 / 真实产物回勾 / 幂等 / 失败边界。"""

from __future__ import annotations

import pytest

from novelvideo.agent import plan_runner
from novelvideo.agent import plans as agent_plans
from novelvideo.pipeline import planner


@pytest.fixture()
def project_dir(tmp_path):
    path = tmp_path / "proj"
    (path / "freezone").mkdir(parents=True)
    return path


def _episode(number: int, **overrides):
    row = {
        "number": number,
        "identity_ids": ["ident_1"],
        "scene_menu": [{"name": "客厅"}],
        "prop_menu": [{"name": "杯子"}],
        "beat_count": 2,
    }
    row.update(overrides)
    return row


def _beat(number: int, *, sketch: str = "/static/s.png", frame: str = "/static/f.png"):
    return {
        "beat_number": number,
        "sketch_url": sketch,
        "frame_url": frame,
    }


def make_read(state: dict):
    async def read(method: str, path: str, *, query=None, body=None):
        assert method == "GET"
        if path.endswith("/story/source"):
            return {"ok": True, "data": {"text": state["story"]}}
        if path.endswith("/story/generate-script/result"):
            return {"ok": True, "data": {"status": state["screenplay"]}}
        if path.endswith("/characters"):
            return {"ok": True, "data": state["characters"]}
        if path.endswith("/episodes"):
            return {"ok": True, "data": state["episodes"]}
        if "/episodes/" in path and path.endswith("/beats"):
            number = int(path.split("/episodes/")[1].split("/")[0])
            return {"ok": True, "data": state["beats"].get(number, [])}
        if path.endswith("/tasks"):
            return {"ok": True, "data": state.get("tasks", [])}
        raise AssertionError(f"unexpected read: {path}")

    return read


def base_state(**overrides):
    state = {
        "story": "",
        "screenplay": "",
        "tasks": [],
        "characters": [],
        "episodes": [],
        "beats": {},
    }
    state.update(overrides)
    return state


def test_infer_stage_and_episode() -> None:
    assert agent_plans.infer_stage("生成第 2 集草图") == "sketches"
    assert agent_plans.infer_stage("做第1集成片首帧") == "frames"
    assert agent_plans.infer_stage("给第 3 集写分镜脚本") == "script"
    assert agent_plans.infer_stage("整理道具清单") == "props"
    assert agent_plans.infer_stage("随便聊聊") is None
    assert agent_plans.infer_episode("生成第 12 集草图") == 12
    assert agent_plans.infer_episode("EP3 首帧") == 3
    assert agent_plans.infer_episode("没有集号") is None


def test_mark_items_roundtrip(project_dir) -> None:
    _items, added = agent_plans.add_items(
        project_dir, [{"title": "第1集草图", "stage": "sketches", "episode": 1}]
    )
    assert added[0]["stage"] == "sketches" and added[0]["episode"] == 1

    failed = agent_plans.mark_items(project_dir, [added[0]["id"]], error="boom")
    assert failed[0]["last_error"] == "boom" and failed[0]["done"] is False

    done = agent_plans.mark_items(project_dir, [added[0]["id"]], done=True)
    assert done[0]["done"] is True and done[0]["last_error"] == ""


def test_add_items_rejects_unknown_stage(project_dir) -> None:
    with pytest.raises(ValueError):
        agent_plans.add_items(project_dir, [{"title": "x", "stage": "nope"}])


async def test_run_plan_marks_satisfied_items_without_firing(
    project_dir, monkeypatch
) -> None:
    state = base_state(
        story="很久以前……",
        screenplay="completed",
        characters=[{"name": "阿青"}],
        episodes=[_episode(1)],
        beats={1: [_beat(1), _beat(2)]},
    )

    async def fail_plan_next(*_args, **_kwargs):
        raise AssertionError("plan_next should not run when artifacts already exist")

    monkeypatch.setattr(planner, "plan_next", fail_plan_next)

    _items, added = agent_plans.add_items(
        project_dir,
        [
            {"title": "导入小说", "stage": "story"},
            {"title": "生成整本剧本", "stage": "screenplay"},
            {"title": "第1集草图", "stage": "sketches", "episode": 1},
            {"title": "第1集首帧", "stage": "frames", "episode": 1},
        ],
    )

    result = await plan_runner.run_plan(
        make_read(state), "proj", project_dir, {}, budget_s=5
    )

    assert result["status"] == "done"
    assert set(result["completed"]) == {item["id"] for item in added}
    assert result["remaining"] == []
    assert all(item["done"] for item in agent_plans.load_plan(project_dir))


async def test_run_plan_fires_story_then_screenplay_and_marks_done(
    project_dir, monkeypatch
) -> None:
    state = base_state()

    async def fake_run_actions(call, actions):
        fired = []
        for action in actions:
            path = action["path"]
            if path.endswith("/story/source"):
                state["story"] = action["body"]["text"]
            elif path.endswith("/story/generate-script"):
                state["screenplay"] = "completed"
            fired.append({"method": action["method"], "path": path, "ok": True})
        return fired

    monkeypatch.setattr(planner, "run_actions", fake_run_actions)

    _items, added = agent_plans.add_items(
        project_dir,
        [
            {"title": "导入小说", "stage": "story"},
            {"title": "生成整本剧本", "stage": "screenplay"},
        ],
    )

    result = await plan_runner.run_plan(
        make_read(state), "proj", project_dir, {"story_text": "新故事正文"}, budget_s=5
    )

    assert result["status"] == "done"
    assert result["advanced"] == ["story_saved", "screenplay_started"]
    assert set(result["completed"]) == {item["id"] for item in added}
    stored = agent_plans.load_plan(project_dir)
    assert all(item["done"] and item["done_at"] for item in stored)

    # 幂等：再次执行不会重复发射，也不会把已完成条目重新打开。
    second = await plan_runner.run_plan(
        make_read(state), "proj", project_dir, {"story_text": "新故事正文"}, budget_s=5
    )
    assert second["status"] == "done"
    assert second["advanced"] == []
    assert all(item["done"] for item in agent_plans.load_plan(project_dir))


async def test_run_plan_accepts_empty_menus_when_planner_completed(
    project_dir, monkeypatch
) -> None:
    """规划任务跑完但本集没有场景/道具：应回勾完成，而不是被越过守卫判失败。"""
    state = base_state(
        story="正文",
        screenplay="completed",
        characters=[{"name": "阿青"}],
        episodes=[_episode(1, scene_menu=[], prop_menu=[])],
        beats={1: [_beat(1)]},
        tasks=[
            {"task_type": "episode_scene_planner", "episode": 1, "status": "completed"},
            {"task_type": "episode_prop_planner", "episode": 1, "status": "completed"},
        ],
    )

    async def fail_plan_next(*_args, **_kwargs):
        raise AssertionError("completed planning tasks must satisfy the item")

    monkeypatch.setattr(planner, "plan_next", fail_plan_next)

    _items, added = agent_plans.add_items(
        project_dir,
        [
            {"title": "第1集场景规划", "stage": "scenes", "episode": 1},
            {"title": "第1集道具规划", "stage": "props", "episode": 1},
        ],
    )

    result = await plan_runner.run_plan(
        make_read(state), "proj", project_dir, {}, budget_s=5
    )

    assert result["status"] == "done"
    assert set(result["completed"]) == {item["id"] for item in added}
    assert all(item["done"] for item in agent_plans.load_plan(project_dir))


async def test_run_plan_infers_legacy_titles(project_dir, monkeypatch) -> None:
    state = base_state(
        story="正文",
        screenplay="completed",
        characters=[{"name": "阿青"}],
        episodes=[_episode(1)],
        beats={1: [_beat(1), _beat(2, sketch="")]},
    )

    async def fake_run_actions(call, actions):
        fired = []
        for action in actions:
            if action["path"].endswith("/sketches/generate"):
                state["beats"][1] = [_beat(1), _beat(2)]
            fired.append({"method": action["method"], "path": action["path"], "ok": True})
        return fired

    monkeypatch.setattr(planner, "run_actions", fake_run_actions)

    _items, added = agent_plans.add_items(project_dir, ["生成第 1 集草图"])
    result = await plan_runner.run_plan(
        make_read(state), "proj", project_dir, {}, budget_s=5
    )

    assert result["status"] == "done"
    stored = agent_plans.load_plan(project_dir)
    assert stored[0]["stage"] == "sketches"
    assert stored[0]["stage_inferred"] is True
    assert stored[0]["episode"] == 1
    assert stored[0]["done"] is True
    assert added[0]["id"] == stored[0]["id"]
    assert "episode_1_sketches_generating" in result["advanced"]


async def test_run_plan_missing_episode_fails_fast(project_dir, monkeypatch) -> None:
    state = base_state(story="正文", screenplay="completed", episodes=[_episode(1)])

    async def fail_plan_next(*_args, **_kwargs):
        raise AssertionError("must not fire for a nonexistent episode")

    monkeypatch.setattr(planner, "plan_next", fail_plan_next)
    _items, added = agent_plans.add_items(
        project_dir, [{"title": "第3集草图", "stage": "sketches", "episode": 3}]
    )

    result = await plan_runner.run_plan(
        make_read(state), "proj", project_dir, {}, budget_s=5
    )

    assert result["status"] == "failed"
    assert "第 3 集不存在" in result["error"]
    stored = agent_plans.load_plan(project_dir)
    assert stored[0]["done"] is False
    assert "第 3 集不存在" in stored[0]["last_error"]


async def test_run_plan_no_executable_items(project_dir) -> None:
    state = base_state()
    _items, _added = agent_plans.add_items(project_dir, ["和客户对一下方向"])
    result = await plan_runner.run_plan(
        make_read(state), "proj", project_dir, {}, budget_s=5
    )
    assert result["status"] == "no_executable_items"
    assert result["manual_pending"][0]["title"] == "和客户对一下方向"


async def test_run_plan_busy_when_locked(project_dir) -> None:
    state = base_state()
    lock = plan_runner._lock_for(project_dir)
    async with lock:
        result = await plan_runner.run_plan(
            make_read(state), "proj", project_dir, {}, budget_s=5
        )
    assert result["status"] == "busy"


def test_planner_stage_rank_mapping() -> None:
    assert plan_runner._planner_stage_rank("episode_1_identity_planner_running") == 5
    assert plan_runner._planner_stage_rank("episode_2_script_writer_running") == 6
    assert plan_runner._planner_stage_rank("episode_1_episode_scene_planner_running") == 7
    assert plan_runner._planner_stage_rank("episode_1_episode_prop_planner_running") == 8
    assert plan_runner._planner_stage_rank("episode_1_sketches_generating") == 9
    assert plan_runner._planner_stage_rank("sketch_grid_generation") == 9
    assert plan_runner._planner_stage_rank("episode_1_frames_generating") == 10
    assert plan_runner._planner_stage_rank("done") == 99
    assert plan_runner._planner_stage_rank("script_auto_running") is None


async def test_ingest_item_not_failed_while_characters_build(project_dir, monkeypatch) -> None:
    """摄入已完成、角色刚入队时，ingest 条目必须回勾而不是被判「流水线已越过」。"""
    state = base_state(
        story="正文",
        screenplay="completed",
        tasks=[{"task_type": "ingest_fast", "episode": 0, "status": "completed"}],
    )

    async def fake_plan_next(_read, _project, _opts):
        return {
            "stage": "characters_building",
            "status": "planned",
            "actions": [{"method": "POST", "path": "/characters/build", "body": None}],
        }

    async def fake_run_actions(_call, _actions):
        state["characters"] = [{"name": "阿青"}]
        return [{"method": "POST", "path": "/characters/build", "ok": True}]

    monkeypatch.setattr(planner, "plan_next", fake_plan_next)
    monkeypatch.setattr(planner, "run_actions", fake_run_actions)

    _items, added = agent_plans.add_items(
        project_dir,
        [
            {"title": "剧本入库", "stage": "ingest"},
            {"title": "提取角色", "stage": "characters"},
        ],
    )

    result = await plan_runner.run_plan(
        make_read(state), "proj", project_dir, {}, budget_s=5
    )

    assert result["status"] == "done"
    assert set(result["completed"]) == {item["id"] for item in added}
    assert all(item["done"] for item in agent_plans.load_plan(project_dir))


async def test_run_plan_episode_override_limits_target(project_dir, monkeypatch) -> None:
    """episode 覆盖计划条目的集号，只按指定集判断完成。"""
    state = base_state(
        story="正文",
        screenplay="completed",
        characters=[{"name": "阿青"}],
        episodes=[_episode(1), _episode(2)],
        beats={1: [_beat(1, sketch="")], 2: [_beat(1)]},
    )

    async def fail_plan_next(*_args, **_kwargs):
        raise AssertionError("overridden episode already has sketches; must not fire")

    monkeypatch.setattr(planner, "plan_next", fail_plan_next)
    _items, added = agent_plans.add_items(
        project_dir, [{"title": "第1集草图", "stage": "sketches", "episode": 1}]
    )

    result = await plan_runner.run_plan(
        make_read(state), "proj", project_dir, {"episode": 2}, budget_s=5
    )

    assert result["status"] == "done"
    assert result["completed"] == [added[0]["id"]]
    assert agent_plans.load_plan(project_dir)[0]["done"] is True


async def test_run_plan_rejects_missing_override_episode(project_dir, monkeypatch) -> None:
    state = base_state(
        story="正文",
        screenplay="completed",
        characters=[{"name": "阿青"}],
        episodes=[_episode(1)],
        beats={1: [_beat(1, sketch="")]},
    )

    async def fail_plan_next(*_args, **_kwargs):
        raise AssertionError("must not fire for a nonexistent override episode")

    monkeypatch.setattr(planner, "plan_next", fail_plan_next)
    _items, added = agent_plans.add_items(
        project_dir, [{"title": "第1集草图", "stage": "sketches", "episode": 1}]
    )

    result = await plan_runner.run_plan(
        make_read(state), "proj", project_dir, {"episode": 3}, budget_s=5
    )

    assert result["status"] == "failed"
    assert "第 3 集不存在" in result["error"]
    assert added[0]["id"] in result["remaining"]
