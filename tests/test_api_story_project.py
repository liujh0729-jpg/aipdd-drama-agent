"""[aipdd fork delta] L1「故事」项目级链路：故事源存取、导入、AI 优化、整本剧本生成与结果摘要。"""

from __future__ import annotations

from io import BytesIO
from types import SimpleNamespace

import pytest
from starlette.datastructures import UploadFile

pytestmark = pytest.mark.m03


# ── 辅助：作用域与任务后台替身 ────────────────────────────────────────────────


def _patch_scope(
    monkeypatch,
    story_module,
    *,
    project_dir,
    state_dir,
    ctx=None,
):
    async def fake_resolve_project_scope(project, user, required_role="viewer"):
        return SimpleNamespace(
            ctx=ctx,
            username=user.get("username", "admin"),
            project_name=project,
            project_dir=str(project_dir),
            state_dir=str(state_dir),
            output_dir=str(project_dir),
        )

    monkeypatch.setattr(
        story_module, "resolve_project_scope", fake_resolve_project_scope
    )


class _FakeTaskBackend:
    def __init__(self):
        self.enqueued: list[tuple] = []

    async def enqueue_project_task(self, ctx, **kwargs):
        self.enqueued.append((ctx, kwargs))
        return SimpleNamespace(
            task_state=SimpleNamespace(task_id="task-1"),
            backend="local",
            queue="default",
        )


class _RecordingUsageMeter:
    def __init__(self):
        self.set_calls: list[tuple] = []
        self.clear_calls = 0

    def set_llm_usage_context(self, user_id, **kwargs):
        self.set_calls.append((user_id, kwargs))

    def clear_llm_usage_context(self):
        self.clear_calls += 1


class _FakeTaskManager:
    def __init__(self, task=None):
        self.task = task

    def get_task_for_project(self, ctx, task_type, episode):
        return self.task


# ── 故事源读写 ────────────────────────────────────────────────────────────────


def test_story_source_helpers_roundtrip(tmp_path) -> None:
    from novelvideo.api.routes import story

    assert story._read_story_source(tmp_path) == ""

    source_path = story._story_source_path(tmp_path)
    source_path.parent.mkdir(parents=True, exist_ok=True)
    source_path.write_text("  故事正文  ", encoding="utf-8")

    assert story._read_story_source(tmp_path) == "故事正文"


@pytest.mark.asyncio
async def test_get_project_story_source_returns_text(tmp_path, monkeypatch) -> None:
    from novelvideo.api.routes import story

    project_dir = tmp_path / "project"
    state_dir = tmp_path / "state"
    state_dir.mkdir(parents=True)

    source_path = story._story_source_path(project_dir)
    source_path.parent.mkdir(parents=True, exist_ok=True)
    source_path.write_text("第一章 风起\n他回来了。", encoding="utf-8")
    story.save_project_config_in_state_dir(
        str(state_dir), config={story._STORY_SOURCE_NAME_CONFIG_KEY: "novel.txt"}
    )

    _patch_scope(
        monkeypatch, story, project_dir=project_dir, state_dir=state_dir
    )
    response = await story.get_project_story_source(
        project="demo", user={"username": "admin"}
    )

    assert response["ok"] is True
    assert response["data"]["text"] == "第一章 风起\n他回来了。"
    assert response["data"]["chars"] > 0
    assert response["data"]["billable_chars"] > 0
    assert response["data"]["filename"] == "novel.txt"


# ── 导入 ──────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_import_project_story_from_text_persists(tmp_path, monkeypatch) -> None:
    from novelvideo.api.routes import story

    project_dir = tmp_path / "project"
    state_dir = tmp_path / "state"
    state_dir.mkdir(parents=True)
    _patch_scope(
        monkeypatch, story, project_dir=project_dir, state_dir=state_dir
    )

    response = await story.import_project_story_source(
        project="demo", file=None, text="  故事正文  ", user={"username": "admin"}
    )

    assert response["ok"] is True
    assert response["data"]["chars"] == len("故事正文")
    assert story._read_story_source(project_dir) == "故事正文"


@pytest.mark.asyncio
async def test_import_project_story_from_upload(tmp_path, monkeypatch) -> None:
    from novelvideo.api.routes import story

    project_dir = tmp_path / "project"
    state_dir = tmp_path / "state"
    state_dir.mkdir(parents=True)
    _patch_scope(
        monkeypatch, story, project_dir=project_dir, state_dir=state_dir
    )

    upload = UploadFile(
        file=BytesIO("上传的故事正文".encode("utf-8")), filename="novel.txt"
    )
    response = await story.import_project_story_source(
        project="demo", file=upload, text=None, user={"username": "admin"}
    )

    assert response["ok"] is True
    assert response["data"]["filename"] == "novel.txt"
    assert story._read_story_source(project_dir) == "上传的故事正文"
    raw_path = story._story_dir(project_dir) / "raw_source.txt"
    assert raw_path.is_file()


@pytest.mark.asyncio
async def test_import_project_story_rejects_empty_and_bad_file(
    tmp_path, monkeypatch
) -> None:
    from novelvideo.api.routes import story

    project_dir = tmp_path / "project"
    state_dir = tmp_path / "state"
    state_dir.mkdir(parents=True)
    _patch_scope(
        monkeypatch, story, project_dir=project_dir, state_dir=state_dir
    )

    empty = await story.import_project_story_source(
        project="demo", file=None, text="   ", user={"username": "admin"}
    )
    assert empty["ok"] is False

    bad = await story.import_project_story_source(
        project="demo",
        file=UploadFile(file=BytesIO(b"x"), filename="novel.pdf"),
        text=None,
        user={"username": "admin"},
    )
    assert bad["ok"] is False
    assert story._read_story_source(project_dir) == ""


# ── AI 优化 ───────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_optimize_project_story_uses_text_and_style(
    tmp_path, monkeypatch
) -> None:
    from novelvideo.agents import director_screenwriter
    from novelvideo.api.routes import story
    from novelvideo.api.schemas import ProjectStoryOptimizeRequest

    project_dir = tmp_path / "project"
    state_dir = tmp_path / "state"
    state_dir.mkdir(parents=True)
    ctx = SimpleNamespace(project_id="p1", requester_user_id="u1")
    _patch_scope(
        monkeypatch, story, project_dir=project_dir, state_dir=state_dir, ctx=ctx
    )
    meter = _RecordingUsageMeter()
    monkeypatch.setattr(story, "get_usage_meter", lambda: meter)

    calls: dict[str, object] = {}

    async def fake_optimize(story_text, **kwargs):
        calls["story_text"] = story_text
        calls["kwargs"] = kwargs
        return "优化后的故事", ["补强冲突"]

    monkeypatch.setattr(director_screenwriter, "optimize_novel", fake_optimize)

    response = await story.optimize_project_story_source(
        project="demo",
        body=ProjectStoryOptimizeRequest(text="原始故事", story_style="comedy"),
        user={"username": "admin"},
    )

    assert response["ok"] is True
    assert response["data"]["story"] == "优化后的故事"
    assert response["data"]["notes"] == ["补强冲突"]
    assert response["data"]["changed"] is True
    assert calls["story_text"] == "原始故事"
    assert calls["kwargs"]["story_style"] == "comedy"
    assert meter.clear_calls == 1


@pytest.mark.asyncio
async def test_optimize_project_story_without_source_fails(
    tmp_path, monkeypatch
) -> None:
    from novelvideo.api.routes import story

    project_dir = tmp_path / "project"
    state_dir = tmp_path / "state"
    state_dir.mkdir(parents=True)
    _patch_scope(
        monkeypatch, story, project_dir=project_dir, state_dir=state_dir
    )

    response = await story.optimize_project_story_source(
        project="demo", body=None, user={"username": "admin"}
    )
    assert response["ok"] is False


# ── 生成整本剧本 ──────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_generate_project_screenplay_enqueues_task(
    tmp_path, monkeypatch
) -> None:
    from novelvideo.api.routes import story
    from novelvideo.api.schemas import NovelToScreenplayRequest

    project_dir = tmp_path / "project"
    state_dir = tmp_path / "state"
    state_dir.mkdir(parents=True)
    source_path = story._story_source_path(project_dir)
    source_path.parent.mkdir(parents=True, exist_ok=True)
    source_path.write_text("整本故事", encoding="utf-8")

    ctx = SimpleNamespace(project_id="p1")
    backend = _FakeTaskBackend()
    _patch_scope(
        monkeypatch, story, project_dir=project_dir, state_dir=state_dir, ctx=ctx
    )
    monkeypatch.setattr(story, "get_task_backend", lambda: backend)

    response = await story.generate_project_screenplay(
        project="demo",
        body=NovelToScreenplayRequest(episode_count=6, story_style="suspense"),
        user={"username": "admin"},
    )

    assert response["ok"] is True
    assert response["task_type"] == "novel_to_screenplay"
    assert len(backend.enqueued) == 1
    _, kwargs = backend.enqueued[0]
    assert kwargs["task_type"] == "novel_to_screenplay"
    assert kwargs["episode"] == 0
    payload = kwargs["payload"]
    assert payload["story_path"] == str(source_path)
    assert payload["episode_count"] == 6
    assert payload["story_style"] == "suspense"
    output = payload["output_path"]
    assert "/uploads/screenplay_" in output and output.endswith(".txt")


@pytest.mark.asyncio
async def test_generate_project_screenplay_validates_inputs(
    tmp_path, monkeypatch
) -> None:
    from fastapi import HTTPException

    from novelvideo.api.routes import story
    from novelvideo.api.schemas import NovelToScreenplayRequest

    project_dir = tmp_path / "project"
    state_dir = tmp_path / "state"
    state_dir.mkdir(parents=True)
    ctx = SimpleNamespace(project_id="p1")
    backend = _FakeTaskBackend()
    _patch_scope(
        monkeypatch, story, project_dir=project_dir, state_dir=state_dir, ctx=ctx
    )
    monkeypatch.setattr(story, "get_task_backend", lambda: backend)

    # 尚未导入故事
    missing = await story.generate_project_screenplay(
        project="demo",
        body=NovelToScreenplayRequest(episode_count=6),
        user={"username": "admin"},
    )
    assert missing["ok"] is False

    source_path = story._story_source_path(project_dir)
    source_path.parent.mkdir(parents=True, exist_ok=True)
    source_path.write_text("整本故事", encoding="utf-8")

    # 集数越界
    out_of_range = await story.generate_project_screenplay(
        project="demo",
        body=NovelToScreenplayRequest(episode_count=0),
        user={"username": "admin"},
    )
    assert out_of_range["ok"] is False

    # 未知风格
    with pytest.raises(HTTPException) as excinfo:
        await story.generate_project_screenplay(
            project="demo",
            body=NovelToScreenplayRequest(episode_count=6, story_style="nope"),
            user={"username": "admin"},
        )
    assert excinfo.value.status_code == 400
    assert backend.enqueued == []


# ── 生成结果摘要 ──────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_generated_result_returns_summary(tmp_path, monkeypatch) -> None:
    from novelvideo.api.routes import story

    project_dir = tmp_path / "project"
    state_dir = tmp_path / "state"
    state_dir.mkdir(parents=True)

    screenplay = (
        "第1集 风起\n"
        "场次（1）地点：林家客厅，夜，内\n"
        "人物：林晚\n"
        "△ 林晚推门而入，目光扫过空荡的客厅。\n"
        "林晚：我回来了。\n"
    )
    uploads = project_dir / "uploads"
    uploads.mkdir(parents=True, exist_ok=True)
    (uploads / "screenplay_20261002_000000.txt").write_text(
        screenplay, encoding="utf-8"
    )
    story.save_project_config_in_state_dir(
        str(state_dir),
        config={
            story._NOVEL_SCREENPLAY_CONFIG_KEY: "screenplay_20261002_000000.txt",
            story._NOVEL_SCREENPLAY_EPISODES_CONFIG_KEY: 1,
        },
    )

    task = SimpleNamespace(
        status="completed", error=None, progress=1.0, current_task="完成"
    )
    _patch_scope(
        monkeypatch,
        story,
        project_dir=project_dir,
        state_dir=state_dir,
        ctx=SimpleNamespace(project_id="p1"),
    )
    monkeypatch.setattr(
        story, "get_task_manager", lambda: _FakeTaskManager(task=task)
    )

    response = await story.get_generated_screenplay_result(
        project="demo", user={"username": "admin"}
    )

    data = response["data"]
    assert response["ok"] is True
    assert data["status"] == "completed"
    assert data["filename"] == "screenplay_20261002_000000.txt"
    assert data["episodes"] == 1
    assert data["total_chars"] > 0
    assert len(data["chapter_summaries"]) == 1
    assert data["chapter_summaries"][0]["number"] == 1
    assert data["format_check"]["level"] in {"ok", "warning", "blocking"}


# ── 项目级 agent：提示词渲染与输出归一化 ──────────────────────────────────────


def _patch_director_agent(monkeypatch, ds, output, calls) -> None:
    class FakeAgent:
        def __init__(self, model, **kwargs):
            calls["kwargs"] = kwargs

        async def run(self, task):
            calls["task"] = task
            return SimpleNamespace(output=output)

    monkeypatch.setattr(ds, "Agent", FakeAgent)
    monkeypatch.setattr(ds, "get_newapi_text_pydantic_model", lambda *a, **k: "model")
    monkeypatch.setattr(
        ds, "get_newapi_structured_output_model_settings", lambda: {}
    )
    monkeypatch.setattr(ds, "model_gateway_output_retries", lambda n: n)


@pytest.mark.asyncio
async def test_plan_novel_episodes_renders_prompt(monkeypatch) -> None:
    from novelvideo.catalog import prompt_library
    from novelvideo.agents import director_screenwriter as ds

    monkeypatch.setattr(prompt_library, "_overrides", lambda: {})

    calls: dict[str, object] = {}
    output = ds.NovelEpisodePlanOutput(
        protagonist="秦昭",
        logline="归来复仇",
        episodes=[
            ds.EpisodePlanItem(number=7, title="风起", brief="大纲一"),
            ds.EpisodePlanItem(number=9, title="暗涌", brief="大纲二"),
        ],
    )
    _patch_director_agent(monkeypatch, ds, output, calls)

    plan = await ds.plan_novel_episodes(
        "第一章 风起", project_title="demo", episode_count=2, story_style="suspense"
    )

    system_prompt = calls["kwargs"]["system_prompt"]
    assert "悬疑压迫流（suspense）" in system_prompt
    assert "{episode_count}" not in system_prompt
    assert "{selected_style}" not in system_prompt
    assert calls["task"].startswith("## 故事全文")
    # 输出重编号：与请求的分集号解耦，从 1 连续。
    assert [item.number for item in plan.episodes] == [1, 2]
    assert plan.episodes[0].title == "风起"


@pytest.mark.asyncio
async def test_write_novel_episode_screenplay_renders_prompt(monkeypatch) -> None:
    from novelvideo.catalog import prompt_library
    from novelvideo.agents import director_screenwriter as ds

    monkeypatch.setattr(prompt_library, "_overrides", lambda: {})

    calls: dict[str, object] = {}
    output = ds.NovelEpisodeScreenplayOutput(
        text="第1集 风起\n场次（1）地点：客厅，夜，内"
    )
    _patch_director_agent(monkeypatch, ds, output, calls)

    text = await ds.write_novel_episode_screenplay(
        project_title="demo",
        episode_number=1,
        episode_count=2,
        episode_title="风起",
        episode_brief="大纲一",
        episode_hook="钩子",
        protagonist_name="秦昭",
        story_style="suspense",
        prev_tail="上一集最后一行",
    )

    system_prompt = calls["kwargs"]["system_prompt"]
    assert "大纲一" in system_prompt
    assert "上一集最后一行" in system_prompt
    assert "{prev_tail}" not in system_prompt
    assert "{episode_brief}" not in system_prompt
    assert "风格库参考" in system_prompt
    # 格式纪律：集头数字与“集”之间不能有空格（ChapterDetector 硬要求）。
    assert "数字与“集”之间不能有空格" in system_prompt
    assert text.startswith("第1集")


@pytest.mark.asyncio
async def test_optimize_novel_returns_story_and_notes(monkeypatch) -> None:
    from novelvideo.catalog import prompt_library
    from novelvideo.agents import director_screenwriter as ds

    monkeypatch.setattr(prompt_library, "_overrides", lambda: {})

    calls: dict[str, object] = {}
    output = ds.StoryOptimizationOutput(story="优化稿", notes=["补强冲突"])
    _patch_director_agent(monkeypatch, ds, output, calls)

    story, notes = await ds.optimize_novel(
        "原始故事", project_title="demo", story_style="comedy"
    )

    assert story == "优化稿"
    assert notes == ["补强冲突"]
    assert "轻喜沙雕流（comedy）" in calls["kwargs"]["system_prompt"]