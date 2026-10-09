"""[aipdd fork delta] 整本故事→多集剧本任务：分集大纲 → 逐集生成 → 落盘 uploads/ 与项目配置。"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from novelvideo.i18n_message import message_payload

pytestmark = pytest.mark.m03


class _FakeTaskManager:
    def __init__(self):
        self.progress_calls: list[dict] = []

    def update_progress_for_project(
        self, ctx, task_type, episode, *, progress, current_task, logs=None
    ):
        self.progress_calls.append(
            {
                "task_type": task_type,
                "episode": episode,
                "progress": progress,
                "current_task": current_task,
                "logs": logs or [],
            }
        )


def _episode(number: int, title: str):
    return SimpleNamespace(number=number, title=title, brief=f"第{number}集大纲", hook=f"钩子{number}")


@pytest.mark.asyncio
async def test_runner_generates_episodes_and_persists(tmp_path, monkeypatch) -> None:
    from novelvideo.agents import director_screenwriter
    from novelvideo.project_config import load_project_config_file_from_state_dir
    from novelvideo.task_backend.runners import novel_to_screenplay as runner

    story_path = tmp_path / "story" / "source.txt"
    story_path.parent.mkdir(parents=True, exist_ok=True)
    story_path.write_text("第一章 风起\n他回来了。", encoding="utf-8")
    output_path = tmp_path / "uploads" / "screenplay_test.txt"
    state_dir = tmp_path / "state"

    async def fake_plan(novel, *, project_title="", episode_count=10, story_style=""):
        assert novel.startswith("第一章")
        assert episode_count == 2
        assert story_style == "suspense"
        return SimpleNamespace(
            protagonist="秦昭",
            logline="落魄青年归来复仇。",
            episodes=[_episode(1, "风起"), _episode(2, "暗涌")],
        )

    write_calls: list[dict] = []

    async def fake_write(**kwargs):
        write_calls.append(kwargs)
        number = kwargs["episode_number"]
        return (
            f"第{number}集 {kwargs['episode_title']}\n"
            f"场次（1）地点：林家客厅，夜，内\n"
            f"人物：秦昭\n"
            f"△ 秦昭站在门口，目光扫过客厅。\n"
            f"秦昭：我回来了。\n"
        )

    monkeypatch.setattr(director_screenwriter, "plan_novel_episodes", fake_plan)
    monkeypatch.setattr(
        director_screenwriter, "write_novel_episode_screenplay", fake_write
    )

    manager = _FakeTaskManager()
    monkeypatch.setattr(runner, "get_task_manager", lambda: manager)

    ctx = SimpleNamespace(project_name="demo", project_id="p1", state_dir=str(state_dir))
    envelope = {
        "payload": {
            "story_path": str(story_path),
            "output_path": str(output_path),
            "episode_count": 2,
            "story_style": "suspense",
        }
    }

    result = await runner._run_novel_to_screenplay(envelope, ctx)

    assert result["filename"] == "screenplay_test.txt"
    assert result["episodes"] == 2
    assert result["detected_chapters"] == 2
    assert result["protagonist"] == "秦昭"

    text = output_path.read_text(encoding="utf-8")
    assert "第1集 风起" in text and "第2集 暗涌" in text

    # 逐集生成时第二集拿到的衔接尾部来自第一集正文。
    assert len(write_calls) == 2
    assert write_calls[0]["prev_tail"] == ""
    assert "第1集" in write_calls[1]["prev_tail"]

    config = load_project_config_file_from_state_dir(str(state_dir))
    assert config["novel_screenplay_filename"] == "screenplay_test.txt"
    assert config["novel_screenplay_episodes"] == 2

    # 有进度上报（0.02 起步、1.0 收尾），任务类型正确。
    assert manager.progress_calls[0]["progress"] == 0.02
    assert manager.progress_calls[-1]["progress"] == 1.0
    assert all(c["task_type"] == "novel_to_screenplay" for c in manager.progress_calls)
    assert all(c["episode"] == 0 for c in manager.progress_calls)

    # 进度与日志都必须是可本地化消息：后端照旧写中文当兜底，但 i18n code 要一路
    # 带到 /tasks 响应里，否则英文界面下进度条与日志面板会原样漏出中文。
    # 棘轮脚本只能静态扫描，这里钉住运行时真的带了 code。
    for call in manager.progress_calls:
        assert message_payload(call["current_task"]) is not None
        for entry in call["logs"]:
            assert message_payload(entry) is not None
    assert message_payload(manager.progress_calls[-1]["current_task"]) == {
        "code": "tasks.progress.screenplay.saved",
        "params": {"filename": "screenplay_test.txt"},
    }


@pytest.mark.asyncio
async def test_runner_rejects_empty_story(tmp_path, monkeypatch) -> None:
    from novelvideo.task_backend.runners import novel_to_screenplay as runner

    story_path = tmp_path / "story" / "source.txt"
    story_path.parent.mkdir(parents=True, exist_ok=True)
    story_path.write_text("   ", encoding="utf-8")

    manager = _FakeTaskManager()
    monkeypatch.setattr(runner, "get_task_manager", lambda: manager)

    ctx = SimpleNamespace(project_name="demo", project_id="p1", state_dir=str(tmp_path / "state"))
    envelope = {
        "payload": {
            "story_path": str(story_path),
            "output_path": str(tmp_path / "uploads" / "out.txt"),
            "episode_count": 2,
        }
    }

    with pytest.raises(ValueError):
        await runner._run_novel_to_screenplay(envelope, ctx)