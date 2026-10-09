"""[aipdd fork delta] 故事模块：数据层往返、故事存取、AI 优化、故事转剧本导入与自动触发。"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from novelvideo.models import NovelEpisode
from novelvideo.sqlite_store import SQLiteStore

pytestmark = pytest.mark.m03


# ── 数据层 ──────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_story_and_style_roundtrip_on_sqlite_store(tmp_path) -> None:
    store = SQLiteStore(
        "admin/demo",
        output_dir=str(tmp_path / "output" / "admin" / "demo"),
        state_dir=str(tmp_path / "state" / "admin" / "demo"),
    )
    try:
        await store.initialize()
        await store.add_episodes([NovelEpisode(number=1, title="第一集")])

        await store.patch_episode(1, story="故事正文", story_style="suspense")

        episode = await store.get_episode_from_graph(1)
        assert episode.story == "故事正文"
        assert episode.story_style == "suspense"

        # 未命名的列保持原样：patch 其它字段不碰故事与风格。
        await store.patch_episode(1, beat_source_text="剧本第一行")
        episode = await store.get_episode_from_graph(1)
        assert episode.story == "故事正文"
        assert episode.story_style == "suspense"
        assert episode.beat_source_text == "剧本第一行"

        listed = (await store.list_episodes())[0]
        assert listed.story == "故事正文"
        assert listed.story_style == "suspense"
    finally:
        await store.close()


# ── 导演风格映射 ─────────────────────────────────────────────────────────────


def test_story_style_prompt_value_mapping() -> None:
    from novelvideo.agents.director_screenwriter import (
        is_valid_story_style,
        story_style_prompt_value,
    )

    assert story_style_prompt_value("") == "自动推荐"
    assert story_style_prompt_value(None) == "自动推荐"
    assert story_style_prompt_value("suspense") == "悬疑压迫流（suspense）"
    assert story_style_prompt_value("unknown") == "自动推荐"
    assert is_valid_story_style("")
    assert is_valid_story_style("comedy")
    assert not is_valid_story_style("unknown")


# ── LLM 调用与提示词渲染 ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_story_to_script_renders_library_prompt_with_style(monkeypatch) -> None:
    from novelvideo.catalog import prompt_library
    from novelvideo.agents import director_screenwriter

    monkeypatch.setattr(prompt_library, "_overrides", lambda: {})

    calls: dict[str, object] = {}

    class FakeAgent:
        def __init__(self, model, **kwargs):
            calls["model"] = model
            calls["kwargs"] = kwargs

        async def run(self, task):
            calls["task"] = task
            return SimpleNamespace(
                output=director_screenwriter.StoryToScriptOutput(
                    lines=["秦昭冷声道：“你好。”", "他转身离开。"]
                )
            )

    def fake_newapi_model(model_env, default_model="", *, capability="text.generate"):
        calls["model_env"] = model_env
        calls["default_model"] = default_model
        return "newapi-model"

    monkeypatch.setattr(director_screenwriter, "Agent", FakeAgent)
    monkeypatch.setattr(
        director_screenwriter, "get_newapi_text_pydantic_model", fake_newapi_model
    )
    monkeypatch.setattr(
        director_screenwriter,
        "get_newapi_structured_output_model_settings",
        lambda: {},
    )

    script = await director_screenwriter.story_to_script(
        "故事：秦昭归来。",
        project_title="demo",
        episode_title="第一集",
        episode_number=1,
        story_style="suspense",
        protagonist_name="秦昭",
        target_beats=24,
        beat_chars_range=(14, 20),
    )

    assert script == "秦昭冷声道：“你好。”\n他转身离开。"
    assert calls["model"] == "newapi-model"
    assert calls["model_env"] == "DIRECTOR_SCREENWRITER_MODEL"
    # [req40] 调用点不再自带兜底模型名：该功能位只登记槽位键
    # DIRECTOR_SCREENWRITER_MODEL -> DC-director-screenwriter-LLM，由渠道目录裁决。
    assert calls["default_model"] == ""
    system_prompt = calls["kwargs"]["system_prompt"]
    assert "悬疑压迫流（suspense）" in system_prompt
    assert "导演风格库" in system_prompt  # {style_library} 已注入
    assert "{style_library}" not in system_prompt
    assert "目标行数：24" in system_prompt
    assert "14-20 字" in system_prompt
    assert "秦昭" in system_prompt
    assert "故事：秦昭归来。" in calls["task"]


# ── 路由：存取 ───────────────────────────────────────────────────────────────


class _StoryRouteStore:
    def __init__(self, episode: NovelEpisode):
        self.episode = episode
        self.updates: list[tuple[int, dict]] = []
        self.adapted_content_writes: list[tuple[int, str]] = []

    def get_episode(self, number: int):
        return self.episode if number == self.episode.number else None

    def get_all_characters(self):
        return []

    async def patch_episode(self, episode_number: int, **updates) -> None:
        self.updates.append((episode_number, updates))
        for key, value in updates.items():
            setattr(self.episode, key, value)

    async def save_adapted_content(self, episode_num: int, content: str) -> None:
        self.adapted_content_writes.append((episode_num, content))
        self.episode.adapted_content = content


class _RecordingUsageMeter:
    def __init__(self):
        self.set_calls: list[tuple] = []
        self.clear_calls = 0

    def set_llm_usage_context(self, user_id, **kwargs):
        self.set_calls.append((user_id, kwargs))

    def clear_llm_usage_context(self):
        self.clear_calls += 1


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


def _patch_story_route(
    monkeypatch,
    *,
    story_module,
    store: _StoryRouteStore,
    ctx,
    output_dir,
    backend: _FakeTaskBackend | None = None,
    meter: _RecordingUsageMeter | None = None,
) -> None:
    async def fake_resolve_project_scope(project, user, required_role="viewer"):
        return SimpleNamespace(
            ctx=ctx,
            username=user.get("username", "admin"),
            project_name=project,
            output_dir=str(output_dir),
        )

    async def fake_store_for_context(context, *, load_graph_state=True):
        return store

    async def fake_store(username, project):
        return store

    class FakePathResolver:
        def __init__(self, output_dir_arg, episode_num):
            pass

        def clean_sketches(self):
            pass

    import novelvideo.utils.path_resolver as path_resolver_module
    from novelvideo.api import deps

    monkeypatch.setattr(story_module, "resolve_project_scope", fake_resolve_project_scope)
    # 路由走 ``deps`` 的 scope helper，工厂按 deps 模块全局解析——mock 必须打在 deps。
    monkeypatch.setattr(deps, "make_sqlite_store_for_context", fake_store_for_context)
    monkeypatch.setattr(deps, "make_sqlite_store", fake_store)
    monkeypatch.setattr(path_resolver_module, "PathResolver", FakePathResolver)
    if backend is not None:
        monkeypatch.setattr(story_module, "get_task_backend", lambda: backend)
    monkeypatch.setattr(
        story_module,
        "get_usage_meter",
        lambda: meter if meter is not None else _RecordingUsageMeter(),
    )


@pytest.mark.asyncio
async def test_get_story_returns_episode_story() -> None:
    from novelvideo.api.routes import story

    store = _StoryRouteStore(
        NovelEpisode(number=1, title="第一集", story="旧故事", story_style="comedy")
    )
    response = await story.get_story(
        project="demo", episode_num=1, user={"username": "admin"}, store=store
    )

    assert response["ok"] is True
    assert response["data"]["story"] == "旧故事"
    assert response["data"]["story_style"] == "comedy"


@pytest.mark.asyncio
async def test_put_story_persists_story_and_style() -> None:
    from novelvideo.api.routes import story
    from novelvideo.api.schemas import StoryUpdateRequest

    store = _StoryRouteStore(NovelEpisode(number=1, title="第一集"))
    response = await story.put_story(
        project="demo",
        episode_num=1,
        body=StoryUpdateRequest(story="新故事", story_style="suspense"),
        user={"username": "admin"},
        store=store,
    )

    assert response["ok"] is True
    assert store.updates == [(1, {"story": "新故事", "story_style": "suspense"})]


@pytest.mark.asyncio
async def test_put_story_without_style_keeps_existing_style() -> None:
    from novelvideo.api.routes import story
    from novelvideo.api.schemas import StoryUpdateRequest

    store = _StoryRouteStore(
        NovelEpisode(number=1, title="第一集", story_style="comedy")
    )
    await story.put_story(
        project="demo",
        episode_num=1,
        body=StoryUpdateRequest(story="仅更新故事"),
        user={"username": "admin"},
        store=store,
    )

    assert store.updates == [(1, {"story": "仅更新故事"})]


@pytest.mark.asyncio
async def test_put_story_rejects_unknown_style() -> None:
    from fastapi import HTTPException

    from novelvideo.api.routes import story
    from novelvideo.api.schemas import StoryUpdateRequest

    store = _StoryRouteStore(NovelEpisode(number=1, title="第一集"))
    with pytest.raises(HTTPException) as excinfo:
        await story.put_story(
            project="demo",
            episode_num=1,
            body=StoryUpdateRequest(story="x", story_style="nope"),
            user={"username": "admin"},
            store=store,
        )
    assert excinfo.value.status_code == 400
    assert store.updates == []


# ── 路由：AI 优化（不落盘） ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_optimize_story_returns_preview_without_saving(
    monkeypatch, tmp_path
) -> None:
    from novelvideo.agents import director_screenwriter
    from novelvideo.api.routes import story
    from novelvideo.api.schemas import StoryOptimizeRequest

    async def fake_optimize(story_text, **kwargs):
        assert story_text == "原始故事"
        assert kwargs["story_style"] == "comedy"
        return "优化后的故事", ["补强冲突", "明确动机"]

    monkeypatch.setattr(director_screenwriter, "optimize_story", fake_optimize)

    store = _StoryRouteStore(
        NovelEpisode(number=1, title="第一集", story="原始故事", story_style="comedy")
    )
    meter = _RecordingUsageMeter()
    _patch_story_route(
        monkeypatch,
        story_module=story,
        store=store,
        ctx=None,
        output_dir=tmp_path,
        meter=meter,
    )

    response = await story.optimize_story_endpoint(
        project="demo",
        episode_num=1,
        body=StoryOptimizeRequest(),
        user={"username": "admin"},
    )

    assert response["ok"] is True
    assert response["data"]["story"] == "优化后的故事"
    assert response["data"]["notes"] == ["补强冲突", "明确动机"]
    assert response["data"]["changed"] is True
    assert store.updates == []  # 优化结果不落盘，等待用户确认
    assert meter.clear_calls == 1


# ── 路由：故事转剧本（导入 + 自动触发） ─────────────────────────────────────


@pytest.mark.asyncio
async def test_story_to_script_imports_script_and_enqueues_writer(
    monkeypatch, tmp_path
) -> None:
    from novelvideo.agents import director_screenwriter
    from novelvideo.api.routes import story
    from novelvideo.api.schemas import StoryToScriptRequest

    async def fake_story_to_script(story_text, **kwargs):
        assert story_text == "原始故事"
        assert kwargs["protagonist_name"] == "秦昭"
        return "第一行剧本\n第二行剧本"

    monkeypatch.setattr(director_screenwriter, "story_to_script", fake_story_to_script)

    def fake_characters():
        return [SimpleNamespace(name="秦昭", is_main=True)]

    episode = NovelEpisode(
        number=1,
        title="第一集",
        story="原始故事",
        identity_ids=["秦_青年"],
    )
    store = _StoryRouteStore(episode)
    store.get_all_characters = fake_characters  # type: ignore[method-assign]
    ctx = SimpleNamespace(project_id="project-1", requester_user_id="user-1")
    backend = _FakeTaskBackend()
    meter = _RecordingUsageMeter()
    _patch_story_route(
        monkeypatch,
        story_module=story,
        store=store,
        ctx=ctx,
        output_dir=tmp_path,
        backend=backend,
        meter=meter,
    )

    response = await story.story_to_script_endpoint(
        project="demo",
        episode_num=1,
        body=StoryToScriptRequest(autostart=True),
        user={"username": "admin"},
    )

    assert response["ok"] is True
    data = response["data"]
    assert data["line_count"] == 2
    assert data["adapted_content"] == "第一行剧本\n第二行剧本"
    assert data["identity_plan_required"] is False
    assert data["task"]["task_id"] == "task-1"
    assert data["task"]["task_type"] == "script_writer"
    # 剧本导入剧本模板：adapted_content 与 beat_source_text 双写
    assert store.adapted_content_writes == [(1, "第一行剧本\n第二行剧本")]
    assert store.updates == [(1, {"beat_source_text": "第一行剧本\n第二行剧本"})]
    # 自动进入剧本流程：同一 script_writer 任务
    ctx_arg, kwargs = backend.enqueued[0]
    assert ctx_arg is ctx
    assert kwargs["task_type"] == "script_writer"
    assert kwargs["product_surface"] == "mainline"
    assert kwargs["episode"] == 1
    assert meter.clear_calls == 1


@pytest.mark.asyncio
async def test_story_to_script_flags_identity_plan_when_missing(
    monkeypatch, tmp_path
) -> None:
    from novelvideo.agents import director_screenwriter
    from novelvideo.api.routes import story
    from novelvideo.api.schemas import StoryToScriptRequest

    async def fake_story_to_script(story_text, **kwargs):
        return "第一行剧本"

    monkeypatch.setattr(director_screenwriter, "story_to_script", fake_story_to_script)

    episode = NovelEpisode(number=1, title="第一集", story="原始故事")  # 未规划身份
    store = _StoryRouteStore(episode)
    ctx = SimpleNamespace(project_id="project-1", requester_user_id="user-1")
    backend = _FakeTaskBackend()
    _patch_story_route(
        monkeypatch,
        story_module=story,
        store=store,
        ctx=ctx,
        output_dir=tmp_path,
        backend=backend,
    )

    response = await story.story_to_script_endpoint(
        project="demo", episode_num=1, body=StoryToScriptRequest(), user={"username": "admin"}
    )

    assert response["ok"] is True
    assert response["data"]["identity_plan_required"] is True
    assert response["data"]["task"] is None
    # 即使不能自动开跑，剧本内容仍已导入
    assert store.adapted_content_writes == [(1, "第一行剧本")]
    assert backend.enqueued == []


@pytest.mark.asyncio
async def test_story_to_script_autostart_false_skips_task(monkeypatch, tmp_path) -> None:
    from novelvideo.agents import director_screenwriter
    from novelvideo.api.routes import story
    from novelvideo.api.schemas import StoryToScriptRequest

    async def fake_story_to_script(story_text, **kwargs):
        return "第一行剧本"

    monkeypatch.setattr(director_screenwriter, "story_to_script", fake_story_to_script)

    store = _StoryRouteStore(
        NovelEpisode(number=1, title="第一集", story="原始故事", identity_ids=["秦_青年"])
    )
    ctx = SimpleNamespace(project_id="project-1", requester_user_id="user-1")
    backend = _FakeTaskBackend()
    _patch_story_route(
        monkeypatch,
        story_module=story,
        store=store,
        ctx=ctx,
        output_dir=tmp_path,
        backend=backend,
    )

    response = await story.story_to_script_endpoint(
        project="demo",
        episode_num=1,
        body=StoryToScriptRequest(autostart=False),
        user={"username": "admin"},
    )

    assert response["ok"] is True
    assert response["data"]["task"] is None
    assert response["data"]["identity_plan_required"] is False
    assert backend.enqueued == []


@pytest.mark.asyncio
async def test_story_to_script_rejects_empty_story(monkeypatch, tmp_path) -> None:
    from novelvideo.api.routes import story
    from novelvideo.api.schemas import StoryToScriptRequest

    store = _StoryRouteStore(NovelEpisode(number=1, title="第一集"))
    _patch_story_route(
        monkeypatch,
        story_module=story,
        store=store,
        ctx=None,
        output_dir=tmp_path,
    )

    response = await story.story_to_script_endpoint(
        project="demo", episode_num=1, body=StoryToScriptRequest(), user={"username": "admin"}
    )

    assert response["ok"] is False
    assert "没有可改编的原文内容" in response["error"]
    assert store.adapted_content_writes == []


@pytest.mark.asyncio
async def test_story_to_script_falls_back_to_planned_episode_source(
    monkeypatch, tmp_path
) -> None:
    """回归：自动化不该因为 ``story`` 列是空的就报「尚未有故事」。

    分集规划／原文导入产出的正文就是这份集手上的底稿，to-script 要直接拿它跑，
    并且**不回写** ``episode.story``——那一列留给用户自己写的创作稿。
    """
    from novelvideo.agents import director_screenwriter
    from novelvideo.api.routes import story
    from novelvideo.api.schemas import StoryToScriptRequest

    seen: dict[str, str] = {}

    async def fake_story_to_script(story_text, **kwargs):
        seen["story_text"] = story_text
        return "第一行剧本"

    monkeypatch.setattr(director_screenwriter, "story_to_script", fake_story_to_script)

    episode = NovelEpisode(
        number=4,
        title="第四集",
        raw_content="秦昭闯入大殿，与反派对峙。",
        content_summary="大殿对峙",
    )
    store = _StoryRouteStore(episode)
    ctx = SimpleNamespace(project_id="project-4", requester_user_id="user-1")
    _patch_story_route(
        monkeypatch,
        story_module=story,
        store=store,
        ctx=ctx,
        output_dir=tmp_path,
        backend=_FakeTaskBackend(),
    )

    response = await story.story_to_script_endpoint(
        project="demo", episode_num=4, body=StoryToScriptRequest(), user={"username": "admin"}
    )

    assert seen["story_text"] == "秦昭闯入大殿，与反派对峙。"
    assert response["ok"] is True
    assert store.adapted_content_writes == [(4, "第一行剧本")]
    # 只读解析：没有把底稿当用户故事存进 story 列。
    assert episode.story == ""
    assert all("story" not in updates for _, updates in store.updates)


@pytest.mark.asyncio
async def test_optimize_story_falls_back_to_raw_content(monkeypatch, tmp_path) -> None:
    """故事页的「AI 优化」与 to-script 同一口径：没手填故事时用已有正文。"""
    from novelvideo.agents import director_screenwriter
    from novelvideo.api.routes import story
    from novelvideo.api.schemas import StoryOptimizeRequest

    async def fake_optimize(story_text, **kwargs):
        assert story_text == "殿中对峙的原文正文"
        return "优化后的故事", ["补强冲突"]

    monkeypatch.setattr(director_screenwriter, "optimize_story", fake_optimize)

    store = _StoryRouteStore(
        NovelEpisode(number=4, title="第四集", raw_content="殿中对峙的原文正文")
    )
    _patch_story_route(
        monkeypatch,
        story_module=story,
        store=store,
        ctx=None,
        output_dir=tmp_path,
    )

    response = await story.optimize_story_endpoint(
        project="demo",
        episode_num=4,
        body=StoryOptimizeRequest(),
        user={"username": "admin"},
    )

    assert response["ok"] is True
    assert response["data"]["story"] == "优化后的故事"