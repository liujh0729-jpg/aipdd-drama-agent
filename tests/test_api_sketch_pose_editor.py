from __future__ import annotations

from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image


class _PoseStore:
    async def get_beats_as_dicts(self, episode_num: int):
        assert episode_num == 1
        return [
            {
                "beat_number": 1,
                "visual_description": "Hero_Main stands",
                "detected_identities": ["Hero_Main"],
            }
        ]

    def get_sketch_colors(self, episode_num: int):
        assert episode_num == 1
        return {"Hero_Main": "#00ffff CYAN"}


def _client(monkeypatch, tmp_path):
    from novelvideo.api.routes import generation

    async def fake_make_sqlite_store(username: str, project: str):
        assert username == "alice"
        assert project == "demo"
        return _PoseStore()

    async def fake_resolve_project(project: str, user: dict, required_role: str = "editor"):
        return SimpleNamespace(
            ctx=None,
            username="alice",
            project_name="demo",
            project_dir=tmp_path,
            output_dir=str(tmp_path),
            state_dir=str(tmp_path / "_state"),
            runtime_dir=str(tmp_path / "_runtime"),
        )

    monkeypatch.setattr(generation, "_resolve_generation_project", fake_resolve_project)
    monkeypatch.setattr(generation, "make_sqlite_store", fake_make_sqlite_store)

    async def fake_store_for_context(*_args, **_kwargs):
        return await generation.make_sqlite_store("alice", "demo")

    monkeypatch.setattr(generation, "make_sqlite_store_for_context", fake_store_for_context)
    monkeypatch.setattr(
        generation,
        "make_static_url_for_context",
        lambda ctx, path, local_path=None: (
            f"/static/projects/{getattr(ctx, 'project_id', 'proj_demo')}/{path}"
        ),
    )

    sketch_dir = tmp_path / "sketches" / "ep001"
    sketch_dir.mkdir(parents=True)
    Image.new("RGBA", (64, 96), (255, 255, 255, 255)).save(
        sketch_dir / "beat_01.png",
    )

    app = FastAPI()
    app.include_router(generation.router, prefix="/api/v1")
    app.dependency_overrides[generation.get_api_user] = lambda: {"username": "alice"}

    return TestClient(app), sketch_dir / "beat_01.png"


def test_get_sketch_pose_editor_payload(monkeypatch, tmp_path):
    client, _sketch_path = _client(monkeypatch, tmp_path)

    response = client.get("/api/v1/projects/demo/episodes/1/beats/1/sketch/pose-editor")

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["data"]["beat_num"] == 1
    assert body["data"]["sketch_url"] == "/static/projects/proj_demo/sketches/ep001/beat_01.png"
    assert body["data"]["width"] == 64
    assert body["data"]["height"] == 96
    assert body["data"]["candidates"][0]["identity_id"] == "Hero_Main"
    assert "standing_front" in body["data"]["pose_presets"]
    assert body["data"]["skeletons"][0]["identityId"] == "Hero_Main"


def test_save_sketch_pose_editor_state(monkeypatch, tmp_path):
    client, sketch_path = _client(monkeypatch, tmp_path)

    response = client.post(
        "/api/v1/projects/demo/episodes/1/beats/1/sketch/pose-editor",
        json={
            "strokes": [
                {
                    "points": [{"x": 5, "y": 5}, {"x": 30, "y": 30}],
                    "width": 4,
                    "colorHex": "#ff0000",
                }
            ],
            "skeletons": [],
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["data"]["sketch_url"] == "/static/projects/proj_demo/sketches/ep001/beat_01.png"
    saved = Image.open(sketch_path).convert("RGBA")
    assert saved.getpixel((10, 10))[:3] == (255, 0, 0)


def test_crop_current_sketch_saves_canonical_image(monkeypatch, tmp_path):
    client, sketch_path = _client(monkeypatch, tmp_path)
    Image.new("RGBA", (64, 96), (255, 255, 255, 255)).save(sketch_path)

    response = client.post(
        "/api/v1/projects/demo/episodes/1/beats/1/sketch/crop",
        json={"x": 4, "y": 6, "width": 20, "height": 30},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["data"]["width"] == 20
    assert body["data"]["height"] == 30
    cropped = Image.open(sketch_path)
    assert cropped.size == (20, 30)
