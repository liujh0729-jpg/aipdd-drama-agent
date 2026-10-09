# SPDX-License-Identifier: Elastic-2.0
# Copyright (c) 2026 ClaymoreLab
"""[aipdd fork delta B] freezone Blender 白模视频路由级测试：零真实 Blender、零外网。

打桩点：
- ``blender_bridge.pull_white_model_video`` → 造一个桩导出文件（成功）或直接抛
  ``BlenderUnreachable``（不可达，路由须回 HTTP 200 信封 ``status:"unreachable"``）。
- ``uploads._fetch_white_model_bytes`` → 外链抓取零出网。

断言：拉取/入库成功后，本项目 ``/static`` 直链被写回目标 ``whiteModelVideoNode`` 的
``data.videoUrl``；``commit_slot`` 再打节点级资产槽指针；不可达时节点原样不动。
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

WHITE_NODE = {
    "id": "wm1",
    "type": "whiteModelVideoNode",
    "position": {"x": 0, "y": 0},
    "data": {"videoUrl": None, "label": "白模"},
}


def _seed_canvas(project_dir: Path, node: dict) -> Path:
    canvas_file = project_dir / "freezone" / "canvases" / "default.json"
    canvas_file.parent.mkdir(parents=True, exist_ok=True)
    canvas_file.write_text(
        json.dumps(
            {
                "schema_version": 2,
                "canvas_id": "default",
                "project_id": "proj_demo",
                "revision": 1,
                "nodes": [node],
                "edges": [],
            }
        ),
        encoding="utf-8",
    )
    return canvas_file


def _node_data(canvas_file: Path) -> dict:
    payload = json.loads(canvas_file.read_text(encoding="utf-8"))
    return next(n for n in payload["nodes"] if n["id"] == "wm1")["data"]


@pytest.fixture()
def blender_client(monkeypatch, tmp_path):
    from novelvideo.api.auth import get_api_user
    from novelvideo.api.routes import freezone
    from novelvideo.api.routes.freezone import uploads

    project_dir = tmp_path / "project"
    project_dir.mkdir(parents=True, exist_ok=True)

    ctx = SimpleNamespace(
        project_id="proj_demo",
        owner_username="alice",
        project_name="demo",
        output_dir=str(project_dir),
        state_dir=str(project_dir),
        runtime_dir=str(project_dir / "_runtime"),
        is_home_node=True,
    )

    async def fake_resolve(project: str, user: dict, **_kwargs):
        return ctx, "alice", "demo", project_dir, str(project_dir)

    monkeypatch.setattr(freezone, "_resolve_freezone_project", fake_resolve)
    monkeypatch.setattr(freezone, "_append_canvas_event", lambda **_kwargs: None)

    async def fake_fetch(url: str) -> bytes:
        return b"\x00\x00fake remote bytes"

    monkeypatch.setattr(uploads, "_fetch_white_model_bytes", fake_fetch)

    app = FastAPI()
    app.include_router(freezone.router, prefix="/api/v1")
    app.dependency_overrides[get_api_user] = lambda: {"id": "u-alice", "username": "alice"}
    return TestClient(app), project_dir, uploads


def test_pull_writes_back_project_static_url(blender_client, monkeypatch) -> None:
    from novelvideo import blender_bridge

    client, project_dir, _uploads = blender_client
    canvas_file = _seed_canvas(project_dir, WHITE_NODE)

    def fake_pull(export_dir, **_kwargs):
        export_dir = Path(export_dir)
        export_dir.mkdir(parents=True, exist_ok=True)
        out = export_dir / "white_model_stub.mp4"
        out.write_bytes(b"\x00\x00stub")
        return out

    monkeypatch.setattr(blender_bridge, "pull_white_model_video", fake_pull)

    resp = client.post(
        "/api/v1/projects/proj_demo/freezone/blender/pull-white-model-video",
        json={"canvas_id": "default", "node_id": "wm1"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["data"]["status"] == "pulled"
    url = body["data"]["url"]
    assert url.startswith("/static/projects/proj_demo/")

    data = _node_data(canvas_file)
    assert data["videoUrl"] == url
    assert data["isUploading"] is False
    assert data["isPulling"] is False
    assert "committed_slot_url" not in data


def test_pull_with_commit_slot_writes_pointer(blender_client, monkeypatch) -> None:
    from novelvideo import blender_bridge

    client, project_dir, _uploads = blender_client
    canvas_file = _seed_canvas(project_dir, WHITE_NODE)

    def fake_pull(export_dir, **_kwargs):
        export_dir = Path(export_dir)
        export_dir.mkdir(parents=True, exist_ok=True)
        out = export_dir / "white_model_stub2.mp4"
        out.write_bytes(b"\x00\x00stub")
        return out

    monkeypatch.setattr(blender_bridge, "pull_white_model_video", fake_pull)

    resp = client.post(
        "/api/v1/projects/proj_demo/freezone/blender/pull-white-model-video",
        json={"canvas_id": "default", "node_id": "wm1", "commit_slot": True},
    )
    assert resp.status_code == 200, resp.text
    url = resp.json()["data"]["url"]
    data = _node_data(canvas_file)
    assert data["committed_slot_url"] == url
    assert data["slot_target"] == {"kind": "white_model_reference", "node": "wm1"}


def test_pull_unreachable_returns_envelope_and_leaves_node(blender_client, monkeypatch) -> None:
    from novelvideo import blender_bridge

    client, project_dir, _uploads = blender_client
    canvas_file = _seed_canvas(project_dir, WHITE_NODE)

    def fake_pull(_export_dir, **_kwargs):
        raise blender_bridge.BlenderUnreachable("连不上本机 Blender")

    monkeypatch.setattr(blender_bridge, "pull_white_model_video", fake_pull)

    resp = client.post(
        "/api/v1/projects/proj_demo/freezone/blender/pull-white-model-video",
        json={"canvas_id": "default", "node_id": "wm1"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["data"]["status"] == "unreachable"
    assert "连不上" in body["data"]["message"]
    # 不可达不写节点：videoUrl 保持原值。
    assert _node_data(canvas_file)["videoUrl"] is None


def test_ingest_external_url_normalizes_to_static_and_writes(blender_client) -> None:
    client, project_dir, _uploads = blender_client
    canvas_file = _seed_canvas(project_dir, WHITE_NODE)

    resp = client.post(
        "/api/v1/projects/proj_demo/freezone/blender/ingest-white-model-video",
        json={
            "canvas_id": "default",
            "node_id": "wm1",
            "url": "https://cdn.example.com/v.mp4",
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["data"]["status"] == "ingested"
    url = body["data"]["url"]
    assert url.startswith("/static/projects/proj_demo/")
    assert _node_data(canvas_file)["videoUrl"] == url


def test_ingest_same_project_url_passes_through(blender_client) -> None:
    client, project_dir, _uploads = blender_client
    canvas_file = _seed_canvas(project_dir, WHITE_NODE)
    existing = "/static/projects/proj_demo/freezone/_uploads/keep.mp4"

    resp = client.post(
        "/api/v1/projects/proj_demo/freezone/blender/ingest-white-model-video",
        json={"canvas_id": "default", "node_id": "wm1", "url": existing},
    )
    assert resp.status_code == 200, resp.text
    # 同项目直链原样放行，不重复入库。
    assert resp.json()["data"]["url"] == existing
    assert _node_data(canvas_file)["videoUrl"] == existing


def test_commit_wrong_node_type_rejected(blender_client) -> None:
    client, project_dir, _uploads = blender_client
    bad = dict(WHITE_NODE)
    bad["type"] = "imageNode"
    _seed_canvas(project_dir, bad)

    resp = client.post(
        "/api/v1/projects/proj_demo/freezone/blender/ingest-white-model-video",
        json={
            "canvas_id": "default",
            "node_id": "wm1",
            "url": "https://cdn.example.com/v.mp4",
        },
    )
    assert resp.status_code == 400, resp.text


def test_commit_missing_node_returns_404(blender_client) -> None:
    client, project_dir, _uploads = blender_client
    _seed_canvas(project_dir, WHITE_NODE)

    resp = client.post(
        "/api/v1/projects/proj_demo/freezone/blender/ingest-white-model-video",
        json={
            "canvas_id": "default",
            "node_id": "ghost",
            "url": "https://cdn.example.com/v.mp4",
        },
    )
    assert resp.status_code == 404, resp.text
