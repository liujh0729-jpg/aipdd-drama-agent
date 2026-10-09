from __future__ import annotations

import json
from pathlib import Path

import pytest

from novelvideo.api.deps import make_static_url_for_context
from novelvideo.api.app import create_app
from novelvideo.api.routes.freezone import _asset_record_from_path
from novelvideo.freezone.canvas_static_urls import (
    migrate_canvas_static_urls_in_memory,
    sanitize_project_local_paths_in_memory,
)
from novelvideo.project_context import ProjectContext


pytestmark = pytest.mark.m09


def _ctx(tmp_path: Path) -> ProjectContext:
    return ProjectContext(
        project_id="01KS77361FXAQNKQF2W4EWWVCW",
        project_name="xuanchuanpian",
        owner_type="user",
        owner_id="user_admin",
        owner_username="admin",
        requester_user_id="user_viewer",
        requester_username="viewer",
        requester_principals=(("user", "user_viewer"),),
        effective_role="viewer",
        home_node_id="local",
        output_dir=tmp_path,
        state_dir=tmp_path / "state",
        runtime_dir=tmp_path / "runtime",
        is_home_node=True,
    )


def test_context_static_url_uses_project_id_path(tmp_path: Path) -> None:
    media = tmp_path / "assets" / "scenes" / "兰州拉面馆" / "master.png"
    media.parent.mkdir(parents=True)
    media.write_bytes(b"png")

    url = make_static_url_for_context(
        _ctx(tmp_path),
        "assets/scenes/兰州拉面馆/master.png",
        local_path=media,
    )

    assert url.startswith(
        "/static/projects/01KS77361FXAQNKQF2W4EWWVCW/"
        "assets/scenes/%E5%85%B0%E5%B7%9E%E6%8B%89%E9%9D%A2%E9%A6%86/master.png?v="
    )


def test_context_static_url_versions_from_project_output_dir(tmp_path: Path) -> None:
    media = tmp_path / "frames" / "ep001" / "beat_01.png"
    media.parent.mkdir(parents=True)
    media.write_bytes(b"frame")

    url = make_static_url_for_context(_ctx(tmp_path), "frames/ep001/beat_01.png")

    assert url.startswith("/static/projects/01KS77361FXAQNKQF2W4EWWVCW/frames/ep001/beat_01.png?v=")


def test_context_static_url_prefers_sog_sidecar_for_3gs_ply(tmp_path: Path) -> None:
    ply = tmp_path / "director_worlds" / "Hall" / "v1" / "master_sharp.ply"
    sog = ply.with_suffix(".sog")
    sog.parent.mkdir(parents=True)
    ply.write_bytes(b"raw ply")
    sog.write_bytes(b"sog")

    url = make_static_url_for_context(
        _ctx(tmp_path),
        "director_worlds/Hall/v1/master_sharp.ply",
        local_path=ply,
    )

    assert url.startswith(
        "/static/projects/01KS77361FXAQNKQF2W4EWWVCW/" "director_worlds/Hall/v1/master_sharp.sog?v="
    )


def test_freezone_asset_record_uses_project_static_url_when_project_id_exists(
    tmp_path: Path,
) -> None:
    media = tmp_path / "assets" / "scenes" / "Hall" / "master.png"
    media.parent.mkdir(parents=True)
    media.write_bytes(b"png")

    record = _asset_record_from_path(
        username="admin",
        project="demo",
        project_dir=tmp_path,
        project_id="proj_123",
        tab="scenes",
        kind="scene",
        role="scene_master",
        label="Hall / master",
        abs_path=media,
    )

    assert record["url"].startswith("/static/projects/proj_123/assets/scenes/Hall/master.png?v=")
    assert record["mainline_context"][0]["sourceUrl"].startswith(
        "/static/projects/proj_123/assets/scenes/Hall/master.png?v="
    )


def test_freezone_asset_record_rejects_missing_project_id(tmp_path: Path) -> None:
    media = tmp_path / "assets" / "scenes" / "Hall" / "master.png"
    media.parent.mkdir(parents=True)
    media.write_bytes(b"png")

    try:
        _asset_record_from_path(
            username="admin",
            project="demo",
            project_dir=tmp_path,
            project_id="",
            tab="scenes",
            kind="scene",
            role="scene_master",
            label="Hall / master",
            abs_path=media,
        )
    except ValueError as exc:
        assert "project_id is required" in str(exc)
    else:
        raise AssertionError("expected missing project_id to fail")


def test_canvas_static_url_migration_is_field_aware_and_in_memory() -> None:
    project_dir = Path("/tmp/nonexistent-project")
    payload = {
        "nodes": [
            {
                "id": "img",
                "data": {
                    "imageUrl": "/static/admin/xuanchuanpian/assets/scenes/兰州/master.png?v=1",
                    "videoUrl": "/static/admin/xuanchuanpian/videos/beats/ep001/beat_01.mp4?v=3",
                    "sourceUrl": "/static/admin/xuanchuanpian/assets/source.png?v=4",
                    "referenceImageUrl": "/static/admin/xuanchuanpian/assets/reference.png?v=5",
                    "plyUrl": "/static/admin/xuanchuanpian/director_worlds/room/v1/pano_depth.ply?v=6",
                    "modelUrl": "/static/admin/xuanchuanpian/director_worlds/room/v1/custom.sog?v=7",
                    "fileUrl": "/static/admin/xuanchuanpian/director_worlds/room/v1/custom.sog?v=8",
                    "panoUrl": "/static/admin/xuanchuanpian/director_worlds/room/v1/pano_360.png?v=9",
                    "description": "/static/admin/xuanchuanpian/markdown/not-media.png",
                    "mainline_context": [
                        {
                            "url": "/static/admin/xuanchuanpian/freezone/bg.png#crop",
                            "source": {
                                "url": "/static/admin/other/freezone/foreign.png?v=2",
                            },
                        }
                    ],
                },
            }
        ],
        "metadata": {
            "shotMetadata": {
                "frames": [
                    {
                        "imageUrl": "/static/admin/xuanchuanpian/frames/ep001/beat_01.png",
                    }
                ]
            }
        },
    }

    migrated = migrate_canvas_static_urls_in_memory(
        payload,
        project_id="proj_123",
        owner_username="admin",
        project_name="xuanchuanpian",
        project_dir=project_dir,
    )

    node_data = migrated["nodes"][0]["data"]
    assert node_data["imageUrl"] == (
        "/static/projects/proj_123/assets/scenes/%E5%85%B0%E5%B7%9E/master.png?v=1"
    )
    assert node_data["videoUrl"] == ("/static/projects/proj_123/videos/beats/ep001/beat_01.mp4?v=3")
    assert node_data["sourceUrl"] == "/static/projects/proj_123/assets/source.png?v=4"
    assert node_data["referenceImageUrl"] == "/static/projects/proj_123/assets/reference.png?v=5"
    assert node_data["plyUrl"] == (
        "/static/projects/proj_123/director_worlds/room/v1/pano_depth.ply?v=6"
    )
    assert node_data["modelUrl"] == (
        "/static/projects/proj_123/director_worlds/room/v1/custom.sog?v=7"
    )
    assert node_data["fileUrl"] == (
        "/static/projects/proj_123/director_worlds/room/v1/custom.sog?v=8"
    )
    assert node_data["panoUrl"] == (
        "/static/projects/proj_123/director_worlds/room/v1/pano_360.png?v=9"
    )
    assert node_data["description"] == "/static/admin/xuanchuanpian/markdown/not-media.png"
    assert (
        node_data["mainline_context"][0]["url"] == "/static/projects/proj_123/freezone/bg.png#crop"
    )
    assert node_data["mainline_context"][0]["source"]["url"] == (
        "/static/admin/other/freezone/foreign.png?v=2"
    )
    assert migrated["metadata"]["shotMetadata"]["frames"][0]["imageUrl"] == (
        "/static/projects/proj_123/frames/ep001/beat_01.png"
    )

    assert payload["nodes"][0]["data"]["imageUrl"].startswith("/static/admin/xuanchuanpian/")


def test_canvas_static_url_migration_prefers_existing_sog_sidecar(tmp_path: Path) -> None:
    ply = tmp_path / "director_worlds" / "room" / "v1" / "master_sharp.ply"
    sog = ply.with_suffix(".sog")
    sog.parent.mkdir(parents=True)
    ply.write_bytes(b"ply")
    sog.write_bytes(b"sog")
    payload = {
        "nodes": [
            {
                "id": "world",
                "data": {
                    "plyUrl": "/static/admin/xuanchuanpian/director_worlds/room/v1/master_sharp.ply?v=old",
                    "modelUrl": "/static/projects/proj_123/director_worlds/room/v1/master_sharp.ply?v=old",
                },
            }
        ]
    }

    migrated = migrate_canvas_static_urls_in_memory(
        payload,
        project_id="proj_123",
        owner_username="admin",
        project_name="xuanchuanpian",
        project_dir=tmp_path,
    )

    node_data = migrated["nodes"][0]["data"]
    assert node_data["plyUrl"].startswith(
        "/static/projects/proj_123/director_worlds/room/v1/master_sharp.sog?v="
    )
    assert node_data["modelUrl"].startswith(
        "/static/projects/proj_123/director_worlds/room/v1/master_sharp.sog?v="
    )


def test_canvas_static_url_migration_rewrites_local_project_paths(tmp_path: Path) -> None:
    sog = (
        tmp_path
        / "freezone"
        / "_outputs"
        / "freezone_image_to_3gs"
        / "job_3gs"
        / "master_sharp.sog"
    )
    sog.parent.mkdir(parents=True)
    sog.write_bytes(b"PK\x03\x04sog")
    payload = {
        "nodes": [
            {
                "id": "world",
                "data": {
                    "plyUrl": str(sog),
                    "modelUrl": str(sog),
                    "fileUrl": str(sog),
                    "ply_path": str(sog),
                    "sog_path": str(sog),
                    "url": str(sog),
                },
            }
        ]
    }

    migrated = migrate_canvas_static_urls_in_memory(
        payload,
        project_id="proj_123",
        owner_username="admin",
        project_name="wxxxx",
        project_dir=tmp_path,
    )

    node_data = migrated["nodes"][0]["data"]
    for key in ("plyUrl", "modelUrl", "fileUrl", "ply_path", "sog_path", "url"):
        assert node_data[key].startswith(
            "/static/projects/proj_123/freezone/_outputs/freezone_image_to_3gs/job_3gs/"
            "master_sharp.sog?v="
        )
        assert not node_data[key].startswith(str(tmp_path))


def test_generation_history_sanitizes_local_project_paths_in_debug_fields(tmp_path: Path) -> None:
    source = tmp_path / "assets" / "scenes" / "孟遥新租的屋子" / "master.png"
    sog = (
        tmp_path
        / "freezone"
        / "_outputs"
        / "freezone_image_to_3gs"
        / "job_3gs"
        / "master_sharp.sog"
    )
    run_dir = sog.parent / "single_face_sharp_runs" / "20260529093743"
    source.parent.mkdir(parents=True)
    sog.parent.mkdir(parents=True)
    run_dir.mkdir(parents=True)
    source.write_bytes(b"img")
    sog.write_bytes(b"PK\x03\x04sog")
    record = {
        "result": {
            "image_path": str(source),
            "ply_path": str(sog),
            "sog_path": str(sog),
            "run_dir": str(run_dir),
            "artifact_dir": str(sog.parent),
            "stdout_tail": f'{{"image":"{source}","output_ply":"{sog.with_suffix(".ply")}"}}',
        }
    }

    migrated = migrate_canvas_static_urls_in_memory(
        record,
        project_id="proj_123",
        owner_username="admin",
        project_name="wxxxx",
        project_dir=tmp_path,
    )
    sanitized = sanitize_project_local_paths_in_memory(
        migrated,
        project_id="proj_123",
        project_dir=tmp_path,
    )

    encoded = json.dumps(sanitized, ensure_ascii=False)
    assert str(tmp_path) not in encoded
    assert "/static/projects/proj_123/assets/scenes/孟遥新租的屋子/master.png" in encoded
    assert "/static/projects/proj_123/freezone/_outputs/freezone_image_to_3gs/job_3gs/" in encoded


def test_backend_rejects_legacy_static_paths() -> None:
    app = create_app()
    route_paths = [getattr(route, "path", "") for route in app.routes]

    project_static_index = route_paths.index("/static/projects/{project}/{file_path:path}")
    legacy_static_index = route_paths.index("/static/{legacy_path:path}")

    assert project_static_index < legacy_static_index

    from fastapi.testclient import TestClient

    response = TestClient(app).get("/static/admin/demo/assets/scenes/Hall/master.png")
    assert response.status_code == 410
    assert "legacy static path" in response.text


@pytest.mark.asyncio
async def test_generation_history_static_urls_are_migrated_in_memory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from novelvideo.api.routes import freezone as freezone_routes

    ctx = _ctx(tmp_path)

    async def fake_resolve_freezone_project(*_args, **_kwargs):
        return ctx, ctx.owner_username, ctx.project_name, Path(ctx.output_dir), str(ctx.output_dir)

    def fake_read_generation_history(*_args, **_kwargs):
        return [
            {
                "schema_version": 1,
                "canvas_id": "default",
                "node_id": "video_1",
                "recorded_at": "2026-05-29T00:00:00",
                "id": "freezone_video_gen:job_1",
                "task_type": "freezone_video_gen",
                "task_key": "task",
                "job_id": "job_1",
                "status": "completed",
                "media_type": "video",
                "result": {
                    "output_url": (
                        "/static/admin/xuanchuanpian/videos/freezone/job_1/output.mp4?v=1"
                    ),
                    "note": "/static/admin/xuanchuanpian/not-a-url-field.mp4",
                },
            }
        ]

    monkeypatch.setattr(
        freezone_routes,
        "_resolve_freezone_project",
        fake_resolve_freezone_project,
    )
    monkeypatch.setattr(freezone_routes, "read_generation_history", fake_read_generation_history)

    response = await freezone_routes.get_node_generation_history(
        project=ctx.project_id,
        canvas_id="default",
        node_id="video_1",
        user={"username": "viewer"},
    )

    record = response["data"]["records"][0]
    assert record["result"]["output_url"] == (
        "/static/projects/01KS77361FXAQNKQF2W4EWWVCW/videos/freezone/job_1/output.mp4?v=1"
    )
    assert record["result"]["note"] == "/static/admin/xuanchuanpian/not-a-url-field.mp4"
