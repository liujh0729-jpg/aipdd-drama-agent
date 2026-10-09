from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient


def _client(monkeypatch, tmp_path):
    from novelvideo.api.routes import generation
    from novelvideo.api.deps import ProjectResolution

    async def fake_resolve_project_scope(project, user, *, required_role="viewer"):
        return ProjectResolution(
            ctx=None,
            username="admin",
            project_name=project,
            project_dir=tmp_path,
            output_dir=str(tmp_path),
            state_dir=str(tmp_path / "state"),
            runtime_dir=str(tmp_path / "runtime"),
        )

    monkeypatch.setattr(generation, "resolve_project_scope", fake_resolve_project_scope)

    app = FastAPI()
    app.include_router(generation.router)
    app.dependency_overrides[generation.get_api_user] = lambda: {"username": "admin"}
    return TestClient(app)


def test_cut_grid_can_register_render_cells(monkeypatch, tmp_path):
    from novelvideo.generators import pool_indexer

    grids_dir = tmp_path / "grids" / "ep001"
    grids_dir.mkdir(parents=True)
    (grids_dir / "grid_02.png").write_bytes(b"fake image")
    seen = {}

    def _save_grid_and_split(**kwargs):
        seen.update(kwargs)
        return {"added": 2, "skipped": 0}

    monkeypatch.setattr(pool_indexer, "save_grid_and_split", _save_grid_and_split)
    client = _client(monkeypatch, tmp_path)

    response = client.post(
        "/projects/demo/episodes/1/grids/0/cut",
        json={
            "grid_type": "render",
            "rows": 1,
            "cols": 2,
            "beat_start": 5,
            "beat_end": 6,
            "beat_numbers": [5, 6],
        },
    )

    assert response.status_code == 200
    assert response.json()["ok"] is True
    assert seen["grid_type"] == "render"
    assert seen["mode_key"] == "1x2"
    assert seen["beat_nums"] == [5, 6]
    assert seen["promote_dir"] == tmp_path / "frames" / "ep001"
