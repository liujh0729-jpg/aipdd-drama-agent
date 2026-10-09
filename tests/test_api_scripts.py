from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

pytestmark = pytest.mark.m03


class _ScriptEpisodeStore:
    def __init__(self, identity_ids: list[str]):
        self.episode = SimpleNamespace(identity_ids=identity_ids)
        self.get_episode_calls: list[int] = []

    def get_episode(self, episode_num: int):
        self.get_episode_calls.append(episode_num)
        return self.episode


def _script_client(monkeypatch, tmp_path, identity_ids: list[str]):
    from novelvideo.api import deps
    from novelvideo.api.routes import scripts
    from novelvideo.api.deps import ProjectResolution
    from novelvideo.utils.path_resolver import PathResolver

    store = _ScriptEpisodeStore(identity_ids)
    clean_calls = []

    async def fake_make_sqlite_store(username: str, project: str):
        assert username == "alice"
        assert project == "demo"
        return store

    async def fake_resolve_project_scope(project, user, *, required_role="viewer"):
        assert project == "demo"
        assert user == {"username": "alice"}
        return ProjectResolution(
            ctx=None,
            username="alice",
            project_name="demo",
            project_dir=tmp_path,
            output_dir=str(tmp_path),
            state_dir=str(tmp_path / "state"),
            runtime_dir=str(tmp_path / "runtime"),
        )

    def fake_clean_sketches(self):
        clean_calls.append(self)
        return []

    monkeypatch.setattr(scripts, "resolve_project_scope", fake_resolve_project_scope)
    # 路由走 ``deps.sqlite_store_scope``，它在 deps 里按模块全局查 ``make_sqlite_store``。
    monkeypatch.setattr(deps, "make_sqlite_store", fake_make_sqlite_store)
    monkeypatch.setattr(PathResolver, "clean_sketches", fake_clean_sketches)

    app = FastAPI()
    app.include_router(scripts.router, prefix="/api/v1")
    app.dependency_overrides[scripts.get_api_user] = lambda: {"username": "alice"}

    return TestClient(app), store, clean_calls


def test_script_generate_requires_identity_plan_before_side_effects(monkeypatch, tmp_path):
    client, store, clean_calls = _script_client(monkeypatch, tmp_path, [])

    response = client.post("/api/v1/projects/demo/episodes/2/script/generate", json={})

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is False
    assert body["code"] == "identity_plan_required"
    assert body["error"]
    assert store.get_episode_calls == [2]
    assert clean_calls == []


def test_script_generate_starts_script_writer_when_identity_plan_exists(
    monkeypatch, tmp_path
):
    client, store, clean_calls = _script_client(monkeypatch, tmp_path, ["秦_幼年"])

    response = client.post("/api/v1/projects/demo/episodes/2/script/generate", json={})

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is False
    assert "project context" in body["error"]
    assert store.get_episode_calls == [2]
    assert len(clean_calls) == 1


def test_pipeline_script_step_uses_script_writer_task_type():
    from novelvideo.api.routes.pipeline import _STEP_MAP

    assert _STEP_MAP["script"][0] == "script_writer"
