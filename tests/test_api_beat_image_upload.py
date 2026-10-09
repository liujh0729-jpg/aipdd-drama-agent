from __future__ import annotations

import io
import json

from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image


def _png_bytes() -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (8, 8), "white").save(buffer, format="PNG")
    return buffer.getvalue()


def _client(monkeypatch, tmp_path):
    from novelvideo.api.routes import generation
    from novelvideo.api.deps import ProjectResolution

    async def fake_resolve_project_scope(project, user, *, required_role="viewer"):
        return ProjectResolution(
            ctx=None,
            username="alice",
            project_name=project,
            project_dir=tmp_path,
            output_dir=str(tmp_path),
            state_dir=str(tmp_path / "state"),
            runtime_dir=str(tmp_path / "runtime"),
        )

    monkeypatch.setattr(generation, "resolve_project_scope", fake_resolve_project_scope)

    def fake_static_url(ctx, relative_path, local_path=None):
        return f"/files/{relative_path}"

    monkeypatch.setattr(generation, "make_static_url_for_context", fake_static_url)

    class FakeStore:
        async def get_script_as_dict(self, episode_num: int):
            return {
                "beats": [
                    {"visual_description": ""},
                    {"visual_description": ""},
                    {"visual_description": ""},
                ],
                "sketch_colors": {},
            }

    async def fake_make_sqlite_store(username: str, project: str):
        return FakeStore()

    monkeypatch.setattr(generation, "make_sqlite_store", fake_make_sqlite_store)

    app = FastAPI()
    app.include_router(generation.router, prefix="/api/v1")
    app.dependency_overrides[generation.get_api_user] = lambda: {"username": "alice"}
    return TestClient(app)


def _write_pool_index(
    tmp_path,
    image_type: str,
    pool_id: str,
    cell_path: str,
    beat_content_hash: str | None = None,
):
    grids_dir = tmp_path / "grids" / "ep002"
    cell_full = grids_dir / cell_path
    cell_full.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (8, 8), "white" if image_type == "sketch" else "blue").save(
        cell_full, format="PNG"
    )
    (grids_dir / "pool_index.json").write_text(
        json.dumps(
            {
                "episode": 2,
                "generated_at": "2026-01-01T00:00:00",
                "version": 2,
                "modes": {},
                "grids": [],
                "images": [
                    {
                        "id": pool_id,
                        "mode": "upload",
                        "grid_index": 0,
                        "cell_index": 0,
                        "grid_path": "",
                        "cell_path": cell_path,
                        "row": 0,
                        "col": 0,
                        "original_beat": 3,
                        "generated_at": "2026-01-01T00:00:00",
                        "type": image_type,
                        **(
                            {"beat_content_hash": beat_content_hash}
                            if beat_content_hash
                            else {}
                        ),
                    }
                ],
                "beat_assignments": {"3": "render/old.png"},
            }
        ),
        encoding="utf-8",
    )
    return grids_dir


def test_upload_beat_sketch_writes_canonical_sketch_file(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    grids_dir = tmp_path / "grids" / "ep002"
    grids_dir.mkdir(parents=True)
    (grids_dir / "pool_index.json").write_text(
        """
        {
          "episode": 2,
          "generated_at": "2026-01-01T00:00:00",
          "version": 1,
          "modes": {},
          "grids": [],
          "images": [],
          "beat_assignments": {"3": "render/old.png"}
        }
        """,
        encoding="utf-8",
    )

    response = client.post(
        "/api/v1/projects/demo/episodes/2/beats/3/sketch/upload",
        files={"file": ("sketch.png", _png_bytes(), "image/png")},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["data"]["beat_num"] == 3
    assert body["data"]["pool_id"].startswith("beat_03_t")
    assert (tmp_path / "sketches" / "ep002" / "beat_03.png").exists()
    saved_index = json.loads((grids_dir / "pool_index.json").read_text("utf-8"))
    assert saved_index["beat_assignments"]["3"] == "render/old.png"


def test_upload_beat_render_writes_frame_and_assigns_uploaded_pool_image(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    grids_dir = tmp_path / "grids" / "ep002"
    grids_dir.mkdir(parents=True)
    (grids_dir / "pool_index.json").write_text(
        """
        {
          "episode": 2,
          "generated_at": "2026-01-01T00:00:00",
          "version": 1,
          "modes": {},
          "grids": [],
          "images": [],
          "beat_assignments": {"3": "old_pool"}
        }
        """,
        encoding="utf-8",
    )

    response = client.post(
        "/api/v1/projects/demo/episodes/2/beats/3/render/upload",
        files={"file": ("render.png", _png_bytes(), "image/png")},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["data"]["beat_num"] == 3
    assert body["data"]["pool_id"].startswith("beat_03_t")
    assert (tmp_path / "frames" / "ep002" / "beat_03.png").exists()
    saved_index = json.loads((grids_dir / "pool_index.json").read_text("utf-8"))
    assert saved_index["beat_assignments"]["3"].startswith("render/")
    assert saved_index["beat_assignments"]["3"] != "old_pool"


def test_select_sketch_pool_image_updates_sketch_without_overwriting_frame(
    monkeypatch, tmp_path
):
    client = _client(monkeypatch, tmp_path)
    grids_dir = _write_pool_index(tmp_path, "sketch", "sketch_pool", "sketch/beat_03.png")
    frame_path = tmp_path / "frames" / "ep002" / "beat_03.png"
    frame_path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (10, 10), "red").save(frame_path, format="PNG")
    before_frame = frame_path.read_bytes()

    response = client.post(
        "/api/v1/projects/demo/episodes/2/beats/3/pool-select",
        json={"pool_id": "sketch_pool"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["data"]["image_type"] == "sketch"
    assert "sketch_url" in body["data"]
    assert frame_path.read_bytes() == before_frame
    assert (tmp_path / "sketches" / "ep002" / "beat_03.png").exists()
    saved_index = json.loads((grids_dir / "pool_index.json").read_text("utf-8"))
    assert saved_index["beat_assignments"]["3"] == "render/old.png"


def test_select_stale_sketch_pool_image_marks_response_stale(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    _write_pool_index(
        tmp_path,
        "sketch",
        "sketch_pool",
        "sketch/beat_03.png",
        beat_content_hash="outdated-hash",
    )

    response = client.post(
        "/api/v1/projects/demo/episodes/2/beats/3/pool-select",
        json={"pool_id": "sketch_pool"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is False
    assert body["stale"] is True
    assert "过期" in body["error"]
    assert not (tmp_path / "sketches" / "ep002" / "beat_03.png").exists()


def test_get_sketch_candidates_separates_pool_from_current_sketch(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    _write_pool_index(tmp_path, "sketch", "sketch_pool", "sketch/beat_03.png")
    grids_dir = tmp_path / "grids" / "ep002"
    render_path = grids_dir / "render" / "beat_03.png"
    render_path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (8, 8), "blue").save(render_path, format="PNG")
    payload = json.loads((grids_dir / "pool_index.json").read_text("utf-8"))
    payload["images"].append(
        {
            "id": "render_pool",
            "mode": "upload",
            "grid_index": 0,
            "cell_index": 0,
            "grid_path": "",
            "cell_path": "render/beat_03.png",
            "row": 0,
            "col": 0,
            "original_beat": 3,
            "generated_at": "2026-01-02T00:00:00",
            "type": "render",
        }
    )
    (grids_dir / "pool_index.json").write_text(
        json.dumps(payload),
        encoding="utf-8",
    )
    current = tmp_path / "sketches" / "ep002" / "beat_03.png"
    current.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (8, 8), "black").save(current, format="PNG")

    response = client.get(
        "/api/v1/projects/demo/episodes/2/beats/3/sketch-candidates"
    )

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["data"]["current_sketch_url"] == "/files/sketches/ep002/beat_03.png"
    assert body["data"]["candidate_count"] == 1
    assert body["data"]["candidates"][0]["id"] == "sketch_pool"
    assert body["data"]["candidates"][0]["url"] == "/files/grids/ep002/sketch/beat_03.png"
    assert body["data"]["candidates"][0]["type"] == "sketch"
    assert all(candidate["type"] == "sketch" for candidate in body["data"]["candidates"])


def test_select_render_pool_image_updates_frame_and_render_assignment(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    grids_dir = _write_pool_index(tmp_path, "render", "render_pool", "render/beat_03.png")

    response = client.post(
        "/api/v1/projects/demo/episodes/2/beats/3/pool-select",
        json={"pool_id": "render_pool"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["data"]["image_type"] == "render"
    assert "frame_url" in body["data"]
    assert (tmp_path / "frames" / "ep002" / "beat_03.png").exists()
    saved_index = json.loads((grids_dir / "pool_index.json").read_text("utf-8"))
    assert saved_index["beat_assignments"]["3"] == "render/beat_03.png"
