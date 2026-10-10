"""导入后的图谱与一键自动化入口：入队形状、导入闸门，以及 structured 图谱可读。

这三条约定是这次优化的用户可见面，跑偏一次就会退回到「导入后什么都没有」：
1. 两个入口都在入队前检查「有没有已导入的正文」和「调用方能不能编辑项目」；
2. 排队回执带 ``message_code``，英文界面才不会回显中文；
3. structured 项目的 ``GET /ingest/graph`` 不再短路返回空图，侧车里有什么就画什么。
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from novelvideo.api.routes import ingest as ingest_routes
from novelvideo.api.schemas import IngestAutoStart
from novelvideo.graph_preview import write_graph_preview
from novelvideo.knowledge_pipeline import (
    KNOWLEDGE_PIPELINE_KEY,
    KNOWLEDGE_PIPELINE_STRUCTURED,
)
from novelvideo.novel_source import NOVEL_IMPORT_REQUIRED_CODE
from novelvideo.project_context import ProjectContext

SNAPSHOT = {
    "nodes": [
        {
            "id": "entity-linmo",
            "label": "林默",
            "type": "Character",
            "degree": 1,
            "properties": {"mentions": 2, "source": "structured_v1"},
        },
        {
            "id": "entity-suwan",
            "label": "苏晚",
            "type": "Character",
            "degree": 1,
            "properties": {},
        },
    ],
    "edges": [
        {
            "id": "entity-linmo:entity-suwan:0",
            "source": "entity-linmo",
            "target": "entity-suwan",
            "relation": "knows",
            "properties": {},
        }
    ],
    "total_nodes": 2,
    "total_edges": 1,
    "truncated": False,
}


def _ctx(tmp_path: Path) -> ProjectContext:
    return ProjectContext(
        project_id="proj_ingest_auto",
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


class _Recorder:
    def __init__(self) -> None:
        self.enqueued: list[dict] = []
        self.roles: list[str] = []

    def patch(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
        *,
        imported: bool = True,
        with_context: bool = True,
    ) -> None:
        ctx = _ctx(tmp_path) if with_context else None
        resolved = SimpleNamespace(
            ctx=ctx,
            project_dir=tmp_path / "output",
            output_dir=tmp_path / "output",
            state_dir=tmp_path / "state",
        )

        async def fake_resolve(_project, _user, *, required_role="viewer"):
            self.roles.append(required_role)
            return resolved

        async def fake_enqueue(_ctx, **kwargs):
            self.enqueued.append(kwargs)
            return SimpleNamespace(
                task_state=SimpleNamespace(task_id="task-ingest-followup"),
                backend="celery",
                queue="default",
            )

        monkeypatch.setattr(ingest_routes, "resolve_project_scope", fake_resolve)
        monkeypatch.setattr(ingest_routes, "has_imported_novel", lambda _dir: imported)
        monkeypatch.setattr(
            ingest_routes,
            "get_task_backend",
            lambda: SimpleNamespace(enqueue_project_task=fake_enqueue),
        )


async def test_graph_build_queues_a_graph_preview_task(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rec = _Recorder()
    rec.patch(monkeypatch, tmp_path)

    res = await ingest_routes.build_ingest_knowledge_graph(
        project="demo", user={"username": "admin"}
    )

    assert res["ok"] is True
    assert res["task_type"] == "graph_preview"
    assert res["task_id"] == "task-ingest-followup"
    # 排队回执必须带词条 key，否则英文界面直接回显后端那句中文。
    assert res["message_code"] == "ingest.graph.buildQueued"
    assert res["message"] == "图谱生成任务已进入队列"
    assert rec.roles == ["editor"]

    (kwargs,) = rec.enqueued
    assert kwargs["product_surface"] == "mainline"
    assert kwargs["task_type"] == "graph_preview"
    assert kwargs["queue_kind"] == "default"
    assert kwargs["episode"] == 0
    payload = kwargs["payload"]
    assert payload["config"] == {}
    assert Path(payload["output_dir"]) == tmp_path / "output"
    assert Path(payload["state_dir"]) == tmp_path / "state"


async def test_auto_queues_one_script_auto_task_with_the_episode_target(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rec = _Recorder()
    rec.patch(monkeypatch, tmp_path)

    res = await ingest_routes.start_ingest_auto(
        project="demo",
        body=IngestAutoStart(target_episodes=12),
        user={"username": "admin"},
    )

    assert res["ok"] is True
    assert res["task_type"] == "script_auto"
    assert res["message_code"] == "ingest.auto.queued"
    # 插值走 params，不要把中文句子烘进 text，英文词条要的是占位符。
    assert res["message_params"] == {"targetEpisodes": 12}

    (kwargs,) = rec.enqueued
    assert kwargs["task_type"] == "script_auto"
    assert kwargs["payload"]["config"] == {"target_episodes": 12}


async def test_auto_defaults_to_ten_episodes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rec = _Recorder()
    rec.patch(monkeypatch, tmp_path)

    await ingest_routes.start_ingest_auto(
        project="demo", body=IngestAutoStart(), user={"username": "admin"}
    )

    assert rec.enqueued[0]["payload"]["config"] == {"target_episodes": 10}


@pytest.mark.parametrize("route_name", ["build_ingest_knowledge_graph", "start_ingest_auto"])
async def test_followups_refuse_a_project_without_imported_text(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, route_name: str
) -> None:
    rec = _Recorder()
    rec.patch(monkeypatch, tmp_path, imported=False)
    handler = getattr(ingest_routes, route_name)
    body = IngestAutoStart() if route_name == "start_ingest_auto" else None

    res = (
        await handler(project="demo", body=body, user={"username": "admin"})
        if body is not None
        else await handler(project="demo", user={"username": "admin"})
    )

    assert res["ok"] is False
    assert res["code"] == NOVEL_IMPORT_REQUIRED_CODE
    assert rec.enqueued == []


@pytest.mark.parametrize("route_name", ["build_ingest_knowledge_graph", "start_ingest_auto"])
async def test_followups_need_a_project_context(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, route_name: str
) -> None:
    rec = _Recorder()
    rec.patch(monkeypatch, tmp_path, with_context=False)
    handler = getattr(ingest_routes, route_name)
    body = IngestAutoStart() if route_name == "start_ingest_auto" else None

    res = (
        await handler(project="demo", body=body, user={"username": "admin"})
        if body is not None
        else await handler(project="demo", user={"username": "admin"})
    )

    assert res["ok"] is False
    assert rec.enqueued == []


def _prepare_structured_project(tmp_path: Path, *, novel: bool = True) -> ProjectContext:
    ctx = _ctx(tmp_path)
    ctx.state_dir.mkdir(parents=True, exist_ok=True)
    ctx.output_dir.mkdir(parents=True, exist_ok=True)
    (ctx.state_dir / "project_config.json").write_text(
        json.dumps({KNOWLEDGE_PIPELINE_KEY: KNOWLEDGE_PIPELINE_STRUCTURED}),
        encoding="utf-8",
    )
    if novel:
        (ctx.output_dir / "novel.txt").write_text("1-1 巷口 日 外\n林默推开门。\n", encoding="utf-8")
    write_graph_preview(ctx.state_dir, SNAPSHOT)
    return ctx


async def test_structured_project_reads_the_extracted_graph_from_the_sidecar(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """structured 项目曾经在这里被短路成空图，导入后永远画不出东西。"""
    ctx = _prepare_structured_project(tmp_path)

    async def fake_resolve(_project, _user, *, required_role="viewer"):
        return SimpleNamespace(ctx=ctx)

    monkeypatch.setattr(ingest_routes, "resolve_project_scope", fake_resolve)

    res = await ingest_routes.get_ingest_knowledge_graph(
        project="demo", user={"username": "viewer"}
    )

    assert res["ok"] is True
    assert [node["label"] for node in res["data"]["nodes"]] == ["林默", "苏晚"]
    assert res["data"]["edges"][0]["relation"] == "knows"


async def test_graph_reads_empty_until_the_import_lands(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """侧车写在 novel.txt 之前，未导入的项目不能显示上一次的图谱。"""
    ctx = _prepare_structured_project(tmp_path, novel=False)

    async def fake_resolve(_project, _user, *, required_role="viewer"):
        return SimpleNamespace(ctx=ctx)

    monkeypatch.setattr(ingest_routes, "resolve_project_scope", fake_resolve)

    res = await ingest_routes.get_ingest_knowledge_graph(
        project="demo", user={"username": "viewer"}
    )

    assert res["data"]["nodes"] == []
    assert res["data"]["truncated"] is False
