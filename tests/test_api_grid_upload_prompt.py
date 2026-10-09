from __future__ import annotations

from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest


def _client(monkeypatch, tmp_path):
    from novelvideo.api.routes import generation

    async def resolve(*args, **kwargs):
        return SimpleNamespace(
            ctx=SimpleNamespace(project_id="proj_demo", output_dir=tmp_path),
            username="admin",
            project_name="demo",
            project_dir=tmp_path,
            output_dir=str(tmp_path),
        )

    monkeypatch.setattr(generation, "_resolve_generation_project", resolve)

    app = FastAPI()
    app.include_router(generation.router)
    app.dependency_overrides[generation.get_api_user] = lambda: {"username": "admin"}
    return TestClient(app)


def _seed_pool(grids_dir):
    from novelvideo.generators.pool_indexer import save_pool_index
    from novelvideo.models import GridEntry, PoolImage, PoolIndex

    pool = PoolIndex(episode=1)
    pool.grids.append(
        GridEntry(
            type="render",
            mode_key="2x2",
            beat_nums=[5, 6],
            preset="custom",
            grid_path="custom/render_2x2_5-6_grid_old.png",
            prompt_path="custom/render_2x2_5-6_prompt.txt",
        )
    )
    for idx, beat in enumerate([5, 6], start=1):
        pool.images.append(
            PoolImage(
                id=f"beat_{beat:02d}_old_render",
                mode="2x2",
                grid_index=2,
                cell_index=idx,
                grid_path="custom/render_2x2_5-6_grid_old.png",
                cell_path=f"render/beat_{beat:02d}.png",
                row=0,
                col=idx - 1,
                original_beat=beat,
                type="render",
            )
        )
    save_pool_index(pool, grids_dir)


@pytest.mark.asyncio
async def test_list_grids_uses_local_paths_for_cache_busting(monkeypatch, tmp_path):
    from novelvideo.api.routes import generation

    grids_dir = tmp_path / "grids" / "ep001"
    (grids_dir / "render").mkdir(parents=True)
    (grids_dir / "custom").mkdir(parents=True)
    (grids_dir / "render" / "beat_05.png").write_bytes(b"cell")
    (grids_dir / "custom" / "render_2x2_5-6_grid_old.png").write_bytes(b"grid")
    _seed_pool(grids_dir)

    class Store:
        async def get_script_as_dict(self, episode_num):
            return {"beats": []}

    async def resolve(*args, **kwargs):
        return SimpleNamespace(
            ctx=SimpleNamespace(project_id="proj_demo", output_dir=tmp_path),
            username="admin",
            project_name="demo",
            project_dir=tmp_path,
            output_dir=str(tmp_path),
        )

    captured: list[tuple[str, object]] = []

    def static_url(ctx, path, local_path=None):
        captured.append((path, local_path))
        return f"/static/projects/{ctx.project_id}/{path}"

    monkeypatch.setattr(generation, "_resolve_generation_project", resolve)

    async def fake_store(*_args, **_kwargs):
        return Store()

    monkeypatch.setattr(generation, "make_sqlite_store_for_context", fake_store)
    monkeypatch.setattr(generation, "make_static_url_for_context", static_url)

    payload = await generation.list_grids("project-id", 1, user={"username": "admin"})

    assert payload["ok"] is True
    assert (
        "grids/ep001/render/beat_05.png",
        grids_dir / "render" / "beat_05.png",
    ) in captured
    assert (
        "grids/ep001/custom/render_2x2_5-6_grid_old.png",
        grids_dir / "custom" / "render_2x2_5-6_grid_old.png",
    ) in captured


def test_upload_grid_replaces_pool_grid_path(monkeypatch, tmp_path):
    from novelvideo.generators.pool_indexer import load_pool_index

    grids_dir = tmp_path / "grids" / "ep001"
    (grids_dir / "custom").mkdir(parents=True)
    (grids_dir / "custom" / "render_2x2_5-6_prompt.txt").write_text(
        "stored prompt",
        encoding="utf-8",
    )
    _seed_pool(grids_dir)
    client = _client(monkeypatch, tmp_path)

    response = client.post(
        "/projects/demo/episodes/1/grids/2/upload",
        data={
            "grid_type": "render",
            "mode_key": "2x2",
            "beat_numbers": "5,6",
        },
        files={"file": ("grid.png", b"uploaded-grid", "image/png")},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True
    grid_path = payload["data"]["grid_path"]
    assert grid_path == "custom/render_2x2_5-6_grid_upload.png"
    assert (grids_dir / grid_path).read_bytes() == b"uploaded-grid"
    assert payload["data"]["grid_url"].startswith(
        f"/static/projects/proj_demo/grids/ep001/{grid_path}"
    )

    pool = load_pool_index(grids_dir)
    assert pool is not None
    assert pool.find_grid("render", "2x2", [5, 6]).grid_path == grid_path
    assert {
        image.grid_path for image in pool.images if image.type == "render" and image.grid_index == 2
    } == {grid_path}


def test_export_grid_prompt_reads_pool_prompt_path(monkeypatch, tmp_path):
    grids_dir = tmp_path / "grids" / "ep001"
    (grids_dir / "custom").mkdir(parents=True)
    (grids_dir / "custom" / "render_2x2_5-6_prompt.txt").write_text(
        "stored render prompt",
        encoding="utf-8",
    )
    _seed_pool(grids_dir)
    client = _client(monkeypatch, tmp_path)

    response = client.get(
        "/projects/demo/episodes/1/grids/2/prompt",
        params={
            "grid_type": "render",
            "mode_key": "2x2",
            "beat_numbers": "5,6",
        },
    )

    assert response.status_code == 200
    assert response.json() == {
        "ok": True,
        "data": {
            "grid_index": 2,
            "grid_type": "render",
            "mode_key": "2x2",
            "beat_numbers": [5, 6],
            "prompt": "stored render prompt",
            "prompt_path": "custom/render_2x2_5-6_prompt.txt",
        },
    }


def test_cut_grid_can_use_pool_grid_entry(monkeypatch, tmp_path):
    from novelvideo.generators import pool_indexer

    grids_dir = tmp_path / "grids" / "ep001"
    (grids_dir / "custom").mkdir(parents=True)
    uploaded = grids_dir / "custom" / "render_2x2_5-6_grid_upload.png"
    uploaded.write_bytes(b"uploaded-grid")
    _seed_pool(grids_dir)

    pool = pool_indexer.load_pool_index(grids_dir)
    assert pool is not None
    pool.find_grid("render", "2x2", [5, 6]).grid_path = "custom/render_2x2_5-6_grid_upload.png"
    pool_indexer.save_pool_index(pool, grids_dir)

    seen = {}

    def _save_grid_and_split(**kwargs):
        seen.update(kwargs)
        return {"added": 2, "skipped": 0}

    monkeypatch.setattr(pool_indexer, "save_grid_and_split", _save_grid_and_split)
    client = _client(monkeypatch, tmp_path)

    response = client.post(
        "/projects/demo/episodes/1/grids/2/cut",
        json={
            "grid_type": "render",
            "mode_key": "2x2",
            "rows": 1,
            "cols": 2,
            "beat_start": 5,
            "beat_end": 6,
            "beat_numbers": [5, 6],
        },
    )

    assert response.status_code == 200
    assert response.json()["ok"] is True
    assert seen["grid_image_path"] == str(uploaded)
