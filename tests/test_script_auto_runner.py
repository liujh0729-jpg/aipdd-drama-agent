"""一键自动化 runner：进度切片、任务归属与失败分级。

这条链最容易跑偏的三件事都有历史教训：
1. 子步骤各自把进度条打满 0→100%，步骤窗口形同虚设；
2. 进度写成子任务类型（``build_characters`` 等），任务中心凭空多出一排幻影行；
3. 场景/图谱这种派生步骤一失败就把前面已经发布的资产整轮推翻。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

import pytest

from novelvideo.i18n_message import LocalizableMessage
from novelvideo.project_context import ProjectContext
from novelvideo.task_backend.runners import graph_build, script_auto

pytestmark = pytest.mark.m07


def _ctx(tmp_path: Path) -> ProjectContext:
    return ProjectContext(
        project_id="proj_script_auto",
        project_name="demo",
        owner_type="user",
        owner_id="owner",
        owner_username="alice",
        requester_user_id="editor",
        requester_username="bob",
        requester_principals=(("user", "editor"),),
        effective_role="editor",
        home_node_id="node_a",
        output_dir=tmp_path / "output",
        state_dir=tmp_path / "state",
        runtime_dir=tmp_path / "runtime",
        is_home_node=True,
    )


class _RecordingTaskManager:
    def __init__(self) -> None:
        self.updates: list[dict] = []

    def update_progress_for_project(self, ctx, task_type, episode, **kwargs):
        self.updates.append({"task_type": task_type, "episode": episode, **kwargs})

    @property
    def progress_values(self) -> list[float]:
        return [u["progress"] for u in self.updates if u["progress"] is not None]

    def codes(self) -> list[str]:
        out = []
        for update in self.updates:
            task = update.get("current_task")
            if isinstance(task, LocalizableMessage):
                out.append(task.code)
        return out


def _patch_reporting(
    monkeypatch: pytest.MonkeyPatch, manager: _RecordingTaskManager
) -> None:
    monkeypatch.setattr(graph_build, "get_task_manager", lambda: manager)


async def _success_steps(monkeypatch: pytest.MonkeyPatch) -> None:
    async def characters(_ctx, *, report=None):
        report(0.0, "角色 0%")
        report(1.0, "角色 100%")
        return {"characters": 3, "added_characters": 3}

    async def episodes(_envelope, _ctx, *, report=None):
        report(0.0, "分集 0%")
        report(1.0, "分集 100%")
        return {"episodes": 5}

    async def scenes(_ctx, *, report=None):
        report(0.0, "场景 0%")
        report(1.0, "场景 100%")
        return {"scenes": 2, "added_scenes": 2}

    async def props(_ctx, *, report=None):
        report(0.0, "道具 0%")
        report(1.0, "道具 100%")
        return {"props": 1}

    async def graph(_ctx, *, report=None):
        report(0.0, "图谱 0%")
        report(1.0, "图谱 100%")
        return {"nodes": 2, "edges": 1, "published": True}

    monkeypatch.setattr(script_auto, "_run_build_characters", characters)
    monkeypatch.setattr(script_auto, "_run_build_episodes", episodes)
    monkeypatch.setattr(script_auto, "_run_build_scenes", scenes)
    monkeypatch.setattr(script_auto, "_run_build_props", props)
    monkeypatch.setattr(script_auto, "_run_graph_preview", graph)


def _envelope(**config: Any) -> dict[str, Any]:
    return {"payload": {"config": dict(config)}}


async def test_step_progress_is_scaled_into_one_bar(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager = _RecordingTaskManager()
    _patch_reporting(monkeypatch, manager)
    monkeypatch.setattr(script_auto, "require_imported_novel", lambda _dir: None)
    await _success_steps(monkeypatch)

    await script_auto._run_script_auto(_envelope(), _ctx(tmp_path))

    values = manager.progress_values
    # 子步骤各自跑过 100%，但对外只体现在自己那一段。
    assert values == sorted(values)
    assert values[0] == 0.0
    assert values[-1] == 1.0
    assert 0.45 in values and 0.60 in values and 0.82 in values and 0.84 in values
    # 只有图谱收尾和最终完成才允许把条打到 100%。
    assert values.count(1.0) == 2


async def test_all_progress_belongs_to_script_auto(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager = _RecordingTaskManager()
    _patch_reporting(monkeypatch, manager)
    monkeypatch.setattr(script_auto, "require_imported_novel", lambda _dir: None)
    await _success_steps(monkeypatch)

    await script_auto._run_script_auto(_envelope(), _ctx(tmp_path))

    # 进度必须收在 script_auto 名下，否则任务中心会多出幻影行。
    assert {u["task_type"] for u in manager.updates} == {"script_auto"}
    assert {u["episode"] for u in manager.updates} == {0}


async def test_derived_step_failures_do_not_abort_the_chain(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager = _RecordingTaskManager()
    _patch_reporting(monkeypatch, manager)
    monkeypatch.setattr(script_auto, "require_imported_novel", lambda _dir: None)
    await _success_steps(monkeypatch)

    async def scenes_boom(_ctx, *, report=None):
        raise RuntimeError("场景规划不可用")

    async def graph_boom(_ctx, *, report=None):
        raise RuntimeError("模型未配置")

    monkeypatch.setattr(script_auto, "_run_build_scenes", scenes_boom)
    monkeypatch.setattr(script_auto, "_run_graph_preview", graph_boom)

    results = await script_auto._run_script_auto(_envelope(), _ctx(tmp_path))

    assert results["characters"]["added_characters"] == 3
    assert results["episodes"] == {"episodes": 5}
    assert "场景规划不可用" in results["scenes"]["error"]
    assert results["graph"] == {"published": False, "error": "模型未配置"}
    assert "tasks.log.auto.scenesFailed" in manager.codes()
    assert "tasks.log.auto.graphFailed" in manager.codes()


@pytest.mark.parametrize("failing_step", ["_run_build_characters", "_run_build_episodes"])
async def test_foundational_failures_abort(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failing_step: str
) -> None:
    manager = _RecordingTaskManager()
    _patch_reporting(monkeypatch, manager)
    monkeypatch.setattr(script_auto, "require_imported_novel", lambda _dir: None)
    await _success_steps(monkeypatch)

    async def boom(*_args, **_kwargs):
        raise RuntimeError("上游失败")

    monkeypatch.setattr(script_auto, failing_step, boom)

    with pytest.raises(RuntimeError, match="上游失败"):
        await script_auto._run_script_auto(_envelope(), _ctx(tmp_path))


async def test_episodes_step_defaults_to_the_deterministic_chapter_mode(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[dict] = []

    async def record_episodes(envelope, _ctx, *, report: Optional[Any] = None):
        seen.append(envelope["payload"]["config"])
        return {"episodes": 1}

    monkeypatch.setattr(script_auto, "require_imported_novel", lambda _dir: None)
    await _success_steps(monkeypatch)
    monkeypatch.setattr(script_auto, "_run_build_episodes", record_episodes)

    await script_auto._run_script_auto(_envelope(target_episodes=8), _ctx(tmp_path))

    # 「剧本页一键」不选规划模式，默认走两条轨都能跑的 chapter 映射：落到 ai
    # 会在 structured 项目上直接抱 KnowledgePipelineUnsupported。
    assert seen[0]["planning_mode"] == "chapters"
    assert seen[0]["target_episodes"] == 8

    # 调用方显式选了模式时不能被链覆写——那是用户在分集页做的决定。
    seen.clear()
    await script_auto._run_script_auto(
        _envelope(planning_mode="ai_events"), _ctx(tmp_path)
    )
    assert seen[0]["planning_mode"] == "ai_events"


def test_both_new_task_types_are_registered() -> None:
    from novelvideo.task_backend import runners  # noqa: F401  (触发注册)
    from novelvideo.task_backend.registry import get_project_task_runner

    assert get_project_task_runner("graph_preview") is not None
    assert get_project_task_runner("script_auto") is not None


def test_step_windows_cover_the_whole_bar_without_gaps() -> None:
    ordered = list(script_auto._STEP_WINDOWS.values())

    assert ordered[0][0] == 0.0
    assert ordered[-1][1] == 1.0
    for (_, high), (low, _) in zip(ordered, ordered[1:]):
        assert high == low


async def test_log_lines_leave_the_bar_alone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """progress=None 是「只记日志、不动进度」的契约，日志行不能把条打回原点。"""
    manager = _RecordingTaskManager()
    _patch_reporting(monkeypatch, manager)
    monkeypatch.setattr(script_auto, "require_imported_novel", lambda _dir: None)
    await _success_steps(monkeypatch)

    await script_auto._run_script_auto(_envelope(), _ctx(tmp_path))

    hits = [
        update
        for update in manager.updates
        if isinstance(update.get("current_task"), LocalizableMessage)
        and update["current_task"].code == "tasks.log.auto.charactersDone"
    ]
    assert hits
    assert hits[0]["progress"] is None


async def test_graph_preview_runner_routes_by_pipeline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from novelvideo import structured_graph
    from novelvideo.graph_preview import empty_graph_preview

    manager = _RecordingTaskManager()
    _patch_reporting(monkeypatch, manager)
    monkeypatch.setattr(graph_build, "require_imported_novel", lambda _dir: None)

    calls: list[str] = []

    class _Store:
        project_dir = tmp_path / "output"
        state_dir = tmp_path / "state"

        async def initialize(self) -> None:
            return None

        async def load_graph_state(self) -> None:
            return None

        async def close(self) -> None:
            return None

        async def materialize_graph_preview(self):
            calls.append("legacy")
            return {"nodes": [{"id": "a"}], "edges": []}

    async def fake_load_store(_ctx):
        return _Store()

    async def fake_build_structured(_store, **_kwargs):
        calls.append("structured")
        return {**empty_graph_preview(), "nodes": [{"id": "a"}, {"id": "b"}], "published": True}

    monkeypatch.setattr(graph_build, "_load_store", fake_load_store)
    monkeypatch.setattr(structured_graph, "build_graph_structured", fake_build_structured)
    monkeypatch.setattr(graph_build, "is_structured_pipeline", lambda _state: True)

    result = await graph_build._run_graph_preview(_ctx(tmp_path))

    assert calls == ["structured"]
    assert result == {"nodes": 2, "edges": 0, "published": True}

    calls.clear()
    monkeypatch.setattr(graph_build, "is_structured_pipeline", lambda _state: False)
    result = await graph_build._run_graph_preview(_ctx(tmp_path))

    assert calls == ["legacy"]
    assert result["published"] is True


def _last_progress_code(manager: _RecordingTaskManager) -> str:
    rows = [u for u in manager.updates if u["progress"] == 1.0]
    assert rows, "整链收尾必须把进度条打到 100%"
    task = rows[-1].get("current_task")
    return task.code if isinstance(task, LocalizableMessage) else ""


async def test_terminal_message_names_a_graph_that_produced_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """图谱整轮抽不出东西时，终态不能只写「自动化完成」。

    现场形状：进度条 100%、状态 completed、图谱却是空的，而派生步骤按设计不致命，
    所以既没有失败状态也没有异常。用户看到的只有一个没生效的按钮。
    """
    manager = _RecordingTaskManager()
    _patch_reporting(monkeypatch, manager)
    monkeypatch.setattr(script_auto, "require_imported_novel", lambda _dir: None)
    await _success_steps(monkeypatch)

    async def empty_graph(_ctx, *, report=None):
        report(1.0, "图谱 100%")
        return {"nodes": 0, "edges": 0, "published": False}

    monkeypatch.setattr(script_auto, "_run_graph_preview", empty_graph)

    await script_auto._run_script_auto(_envelope(), _ctx(tmp_path))

    assert _last_progress_code(manager) == "tasks.progress.auto.doneGapGraph"


async def test_terminal_message_names_every_missing_derived_step(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager = _RecordingTaskManager()
    _patch_reporting(monkeypatch, manager)
    monkeypatch.setattr(script_auto, "require_imported_novel", lambda _dir: None)
    await _success_steps(monkeypatch)

    async def scenes_boom(_ctx, *, report=None):
        raise RuntimeError("场景规划不可用")

    async def graph_boom(_ctx, *, report=None):
        raise RuntimeError("模型未配置")

    monkeypatch.setattr(script_auto, "_run_build_scenes", scenes_boom)
    monkeypatch.setattr(script_auto, "_run_graph_preview", graph_boom)

    await script_auto._run_script_auto(_envelope(), _ctx(tmp_path))

    assert _last_progress_code(manager) == "tasks.progress.auto.doneGapsBoth"


async def test_full_chain_still_reports_a_plain_done(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """五步都产出东西时不许冒出「未生成」——那会把成功的整链说成失败。"""
    manager = _RecordingTaskManager()
    _patch_reporting(monkeypatch, manager)
    monkeypatch.setattr(script_auto, "require_imported_novel", lambda _dir: None)
    await _success_steps(monkeypatch)

    await script_auto._run_script_auto(_envelope(), _ctx(tmp_path))

    assert _last_progress_code(manager) == "tasks.progress.auto.done"
