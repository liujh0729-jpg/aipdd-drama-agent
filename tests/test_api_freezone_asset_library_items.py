from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


@pytest.fixture()
def items_client(monkeypatch, tmp_path):
    from novelvideo.api.auth import get_api_user
    from novelvideo.api.routes import freezone

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

    async def fake_resolve(project: str, user: dict, *, required_role: str = "editor"):
        return ctx, "alice", "demo", project_dir, str(project_dir)

    monkeypatch.setattr(freezone, "_resolve_freezone_project", fake_resolve)

    app = FastAPI()
    app.include_router(freezone.router, prefix="/api/v1")
    app.dependency_overrides[get_api_user] = lambda: {
        "id": "u-alice",
        "username": "alice",
    }
    return TestClient(app), project_dir


_LIBRARY = "/api/v1/projects/proj_demo/freezone/video/character-library"


def _new_item(client: TestClient, project_dir, name: str = "原子朋克") -> str:
    asset = project_dir / "freezone" / "_uploads" / "ref.png"
    asset.parent.mkdir(parents=True, exist_ok=True)
    asset.write_bytes(b"png")
    response = client.post(
        _LIBRARY,
        json={"name": name, "image_urls": ["/freezone/_uploads/ref.png"]},
    )
    assert response.status_code == 200, response.text
    return str(response.json()["data"]["id"])


def _items(client: TestClient) -> list[dict]:
    response = client.get(_LIBRARY)
    assert response.status_code == 200, response.text
    return response.json()["data"]


def test_rename_item_changes_only_the_name(items_client) -> None:
    client, project_dir = items_client
    item_id = _new_item(client, project_dir)
    before = _items(client)[0]

    response = client.patch(f"{_LIBRARY}/{item_id}", json={"name": "  赛博霓虹  "})

    assert response.status_code == 200, response.text
    after = response.json()["data"]
    assert after["name"] == "赛博霓虹"
    # 改名只动名字：地址、类目、保存位置都得原样留着，否则画布上引用该素材的节点会裂图。
    assert after["image_urls"] == before["image_urls"]
    assert after["category"] == before["category"]
    assert after["folder"] == before["folder"]
    assert [entry["name"] for entry in _items(client)] == ["赛博霓虹"]


def test_rename_missing_item_is_404(items_client) -> None:
    client, _project_dir = items_client

    response = client.patch(f"{_LIBRARY}/nope", json={"name": "赛博霓虹"})

    assert response.status_code == 404


@pytest.mark.parametrize("name", ["", "   ", "长" * 61])
def test_rename_rejects_empty_and_overlong_names(items_client, name: str) -> None:
    client, project_dir = items_client
    item_id = _new_item(client, project_dir)

    response = client.patch(f"{_LIBRARY}/{item_id}", json={"name": name})

    assert response.status_code == 400, response.text
    assert [entry["name"] for entry in _items(client)] == ["原子朋克"]


# ----------------------------------------------- 风格封面投影（aipdd fork delta）

_FAKE_STYLE_TEMPLATES = [
    {"id": "s1", "label": "水墨国风", "category": "cat_a", "cover": "s1/cover.webp"},
]


def _patch_style_gallery(monkeypatch, tmp_path) -> None:
    from novelvideo.freezone import style_gallery

    monkeypatch.setattr(style_gallery, "STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setattr(
        style_gallery,
        "load_style_templates",
        lambda: [dict(row) for row in _FAKE_STYLE_TEMPLATES],
    )


def _write_style_cover(tmp_path) -> None:
    cover = tmp_path / "state" / "_shared" / "style_gallery" / "s1" / "cover.webp"
    cover.parent.mkdir(parents=True, exist_ok=True)
    cover.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 16)


def test_library_projects_style_covers_as_readonly_items(
    items_client, monkeypatch, tmp_path
) -> None:
    """设置→风格管理上传的封面要在资产库「风格」栏目里可见（读取时投影）。"""
    client, project_dir = items_client
    _patch_style_gallery(monkeypatch, tmp_path)
    _new_item(client, project_dir, name="已有素材")
    _write_style_cover(tmp_path)

    rows = {entry["id"]: entry for entry in _items(client)}

    projected = rows["style_gallery:s1"]
    assert projected["name"] == "水墨国风"
    assert projected["category"] == "style"
    assert projected["folder"] == "style"
    assert projected["image_urls"][0].startswith("/api/v1/style-gallery/s1/cover.webp")
    # 投影不落盘：库里只留真实条目，不因投影长出记录。
    stored = json.loads(
        (project_dir / "freezone" / "video_character_library.json").read_text(
            encoding="utf-8"
        )
    )
    assert all(
        not str(item.get("id") or "").startswith("style_gallery:") for item in stored
    )


def test_style_without_cover_is_not_projected(items_client, monkeypatch, tmp_path) -> None:
    client, project_dir = items_client
    _patch_style_gallery(monkeypatch, tmp_path)
    _new_item(client, project_dir)

    assert all(not entry["id"].startswith("style_gallery:") for entry in _items(client))


def test_style_cover_item_rejects_rename_and_delete(
    items_client, monkeypatch, tmp_path
) -> None:
    client, project_dir = items_client
    _patch_style_gallery(monkeypatch, tmp_path)
    _new_item(client, project_dir)
    _write_style_cover(tmp_path)

    rename = client.patch(f"{_LIBRARY}/style_gallery:s1", json={"name": "改不动"})
    assert rename.status_code == 400
    removed = client.delete(f"{_LIBRARY}/style_gallery:s1")
    assert removed.status_code == 400
    # 拦下写操作后，下一次读里投影条目照常出现（数据在设置里，不归这边管）。
    assert "style_gallery:s1" in {entry["id"] for entry in _items(client)}


def test_existing_cover_reference_skips_projection(
    items_client, monkeypatch, tmp_path
) -> None:
    """真实条目已引用同一封面 URL 时不再投影，避免「风格」栏目里出现双份。"""
    client, project_dir = items_client
    _patch_style_gallery(monkeypatch, tmp_path)
    _write_style_cover(tmp_path)

    library_file = project_dir / "freezone" / "video_character_library.json"
    library_file.parent.mkdir(parents=True, exist_ok=True)
    library_file.write_text(
        json.dumps(
            [
                {
                    "id": "manual-1",
                    "name": "手动登记",
                    "media": "image",
                    "source": "upload",
                    "category": "style",
                    "folder": "style",
                    "image_urls": ["/api/v1/style-gallery/s1/cover.webp?v=9"],
                }
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    assert [entry["id"] for entry in _items(client)] == ["manual-1"]
