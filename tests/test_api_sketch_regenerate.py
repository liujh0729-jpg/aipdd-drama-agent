from __future__ import annotations

from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient


class _SketchRegenStore:
    async def get_beats_as_dicts(self, episode_num: int):
        assert episode_num == 2
        return [
            {"beat_number": 1, "narration_segment": "a", "visual_description": "A"},
            {"beat_number": 2, "narration_segment": "b", "visual_description": "B"},
            {"beat_number": 3, "narration_segment": "c", "visual_description": "C"},
        ]

    def get_sketch_colors(self, episode_num: int):
        assert episode_num == 2
        return {"hero_main": "#ffffff"}

    def get_cached_prop(self, prop_id: str):
        return None


def _client(monkeypatch, tmp_path):
    from novelvideo.api.routes import generation
    from novelvideo.api.deps import ProjectResolution

    calls: list[dict] = []

    async def fake_make_sqlite_store(username: str, project: str):
        assert username == "alice"
        assert project == "demo"
        return _SketchRegenStore()

    async def fake_make_sqlite_store_for_context(ctx):
        assert ctx.project_id == "proj"
        return _SketchRegenStore()

    async def fake_character_map(store, beats, username, project, **kwargs):
        return {"hero": {"ref_path": ""}}

    async def fake_prop_menu(*args, **kwargs):
        return []

    async def fake_enqueue_project_task(ctx, **kwargs):
        calls.append(kwargs)
        return SimpleNamespace(
            task_state=SimpleNamespace(task_id=f"task-{len(calls)}"),
            backend="celery",
            queue=kwargs.get("queue_kind") or "default",
        )

    async def fake_resolve_project_scope(project, user, *, required_role="viewer"):
        return ProjectResolution(
            ctx=SimpleNamespace(
                project_id="proj",
                state_dir=tmp_path / "state",
                runtime_dir=tmp_path / "runtime",
            ),
            username="alice",
            project_name="demo",
            project_dir=tmp_path,
            output_dir=str(tmp_path),
            state_dir=str(tmp_path / "state"),
            runtime_dir=str(tmp_path / "runtime"),
        )

    monkeypatch.setattr(generation, "resolve_project_scope", fake_resolve_project_scope)
    monkeypatch.setattr(generation, "get_state_dir", lambda username, project: str(tmp_path / "state"))
    monkeypatch.setattr(generation, "load_project_config", lambda username, project: {})
    monkeypatch.setattr(generation, "make_sqlite_store", fake_make_sqlite_store)
    monkeypatch.setattr(
        generation, "make_sqlite_store_for_context", fake_make_sqlite_store_for_context
    )
    monkeypatch.setattr(generation, "_build_character_map", fake_character_map)
    monkeypatch.setattr(generation, "_runtime_prop_menu_with_global_props", fake_prop_menu)
    monkeypatch.setattr(generation, "get_task_backend", lambda: SimpleNamespace(enqueue_project_task=fake_enqueue_project_task))

    app = FastAPI()
    app.include_router(generation.router, prefix="/api/v1")
    app.dependency_overrides[generation.get_api_user] = lambda: {"username": "alice"}

    return TestClient(app), calls


def test_sketch_selected_regen_returns_scope(monkeypatch, tmp_path):
    from novelvideo.task_identity import selection_scope

    client, calls = _client(monkeypatch, tmp_path)

    response = client.post(
        "/api/v1/projects/demo/episodes/2/sketches/regenerate",
        json={"beat_indices": [3, 1], "mode_key": "1x1_2-3_sketch"},
    )

    assert response.status_code == 200
    body = response.json()
    expected_scope = selection_scope("1x1_2-3_sketch", [3, 1])
    assert body["ok"] is True
    assert body["task_type"] == "sketch_regen"
    assert body["scope"] == expected_scope
    assert calls[0]["payload"]["mode_key"] == "1x1_2-3_sketch"
    assert calls[0]["payload"]["config"]["selected_beat_numbers"] == [3, 1]
