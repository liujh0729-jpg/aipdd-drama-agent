"""[aipdd fork delta] 主线资产同步进画布资产库的路由契约。

覆盖：角色 Portrait 之外，身份级一致性三件套（造型三视图 / 五官垫图 /
「三视图与五官垫图」拼接图）也要被同步，且缺文件的槽位静默跳过、重复同步不产生重复条目。
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from urllib.parse import unquote

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from novelvideo.models import CharacterIdentity, NovelCharacter

_SYNC = "/api/v1/projects/proj_demo/freezone/video/asset-library/sync-from-mainline"


def _decoded_path(url: str) -> str:
    """静态 URL 带中文百分号编码与 ?v=<内容指纹>，断言前先归一化成真实路径。"""
    return unquote(url.split("?", 1)[0])


def _write_png(path: Path) -> Path:
    import io

    from PIL import Image

    payload = io.BytesIO()
    Image.new("RGB", (4, 4), color=(10, 20, 30)).save(payload, format="PNG")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload.getvalue())
    return path


class _Store:
    def __init__(self, characters) -> None:
        self._characters = characters
        self.scenes: list = []

    def get_all_characters(self):
        return self._characters

    async def list_scenes(self):
        return list(self.scenes)

    async def list_props(self):
        return []


@pytest.fixture()
def sync_client(monkeypatch, tmp_path):
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

    async def fake_make_store(_ctx):
        return _store

    def fake_voice(*, project_dir, character):  # noqa: ARG001
        return SimpleNamespace(audio_path=None)

    identity = CharacterIdentity(
        identity_id="秦_少年",
        character_name="秦",
        identity_name="少年",
    )
    character = NovelCharacter(name="秦")
    character.identities = [identity]
    _store = _Store([character])

    monkeypatch.setattr(freezone, "_resolve_freezone_project", fake_resolve)
    monkeypatch.setattr(freezone, "make_sqlite_store_for_context", fake_make_store)
    monkeypatch.setattr(freezone, "resolve_character_voice", fake_voice)

    app = FastAPI()
    app.include_router(freezone.router, prefix="/api/v1")
    app.dependency_overrides[get_api_user] = lambda: {
        "id": "u-alice",
        "username": "alice",
    }
    return TestClient(app), project_dir, character, _store


def _synced(client: TestClient) -> dict[str, dict]:
    response = client.post(_SYNC, json={})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["ok"] is True
    return {str(item["id"]): item for item in body["data"]}


def test_sync_pulls_identity_face_mat_and_composite(sync_client) -> None:
    client, project_dir, character, _store = sync_client
    _write_png(project_dir / "assets" / "characters" / "秦" / "portrait.png")
    _write_png(project_dir / "assets" / "characters" / "秦" / "identities" / "少年.png")
    _write_png(
        project_dir
        / "assets"
        / "characters"
        / "秦"
        / "identities"
        / "秦_少年_face_mat.png"
    )
    _write_png(
        project_dir
        / "assets"
        / "characters"
        / "秦"
        / "identities"
        / "秦_少年_composite.png"
    )

    items = _synced(client)

    assert items["mainline:character:秦"]["name"] == "秦"
    three_view = items["mainline:identity_image:秦:少年"]
    face_mat = items["mainline:face_mat:秦:少年"]
    composite = items["mainline:composite:秦:少年"]
    assert three_view["name"] == "秦·少年 造型三视图"
    assert face_mat["name"] == "秦·少年 五官垫图"
    assert composite["name"] == "秦·少年 三视图与五官垫图"
    for item in (three_view, face_mat, composite):
        assert item["media"] == "image"
        assert item["source"] == "character"
        assert item["category"] == "character"
    assert _decoded_path(three_view["image_urls"][0]) == (
        "/static/projects/proj_demo/assets/characters/秦/identities/少年.png"
    )
    assert _decoded_path(face_mat["image_urls"][0]).endswith("/秦_少年_face_mat.png")
    assert _decoded_path(composite["image_urls"][0]).endswith("/秦_少年_composite.png")
    assert _decoded_path(items["mainline:character:秦"]["image_urls"][0]) == (
        "/static/projects/proj_demo/assets/characters/秦/portrait.png"
    )
    assert character.name == "秦"


def test_sync_skips_missing_identity_slots(sync_client) -> None:
    """只有造型三视图时，五官垫图 / 拼接图槽位不建空条目。"""
    client, project_dir, _character, _store = sync_client
    _write_png(project_dir / "assets" / "characters" / "秦" / "identities" / "少年.png")

    items = _synced(client)

    assert "mainline:identity_image:秦:少年" in items
    assert "mainline:face_mat:秦:少年" not in items
    assert "mainline:composite:秦:少年" not in items
    assert "mainline:character:秦" not in items


def test_sync_is_idempotent_and_busts_cache_on_replace(sync_client) -> None:
    client, project_dir, _character, _store = sync_client
    face_mat = _write_png(
        project_dir
        / "assets"
        / "characters"
        / "秦"
        / "identities"
        / "秦_少年_face_mat.png"
    )

    first = _synced(client)
    first_url = first["mainline:face_mat:秦:少年"]["image_urls"][0]
    assert _decoded_path(first_url).endswith("/秦_少年_face_mat.png")

    # 重新生成会把文件改名归档再写同名新文件：条目不能累积，但防缓存指纹要变，
    # 否则画布/前端会继续命中旧图。
    face_mat.unlink()
    _write_png(face_mat)
    second = _synced(client)

    assert len(second) == len(first)
    second_url = second["mainline:face_mat:秦:少年"]["image_urls"][0]
    assert _decoded_path(second_url) == _decoded_path(first_url)
    assert second_url != first_url


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


def test_sync_also_projects_style_covers(sync_client, monkeypatch, tmp_path) -> None:
    """同步结果同样要带「风格」封面投影：前端打开资产库时“先读后同步”，
    若同步结果未投影，会用未投影列表覆盖刚显示的封面 → 风格封面一闪而过。"""
    client, _project_dir, _character, _store = sync_client
    _patch_style_gallery(monkeypatch, tmp_path)
    _write_style_cover(tmp_path)

    items = _synced(client)

    projected = items["style_gallery:s1"]
    assert projected["name"] == "水墨国风"
    assert projected["category"] == "style"
    assert projected["folder"] == "style"
    assert projected["image_urls"][0].startswith("/api/v1/style-gallery/s1/cover.webp")


def test_sync_projection_is_readonly_and_not_persisted(
    sync_client, monkeypatch, tmp_path
) -> None:
    """投影只活在响应里，不因同步落进库文件。"""
    import json

    client, project_dir, _character, _store = sync_client
    _patch_style_gallery(monkeypatch, tmp_path)
    _write_style_cover(tmp_path)
    # 先造一条真实主线资产，确保同步会写库文件（无资产时按设计不写）。
    _write_png(project_dir / "assets" / "characters" / "秦" / "portrait.png")

    _synced(client)

    library_path = project_dir / "freezone" / "video_character_library.json"
    assert library_path.exists()
    stored = json.loads(library_path.read_text(encoding="utf-8"))
    assert stored
    assert all(
        not str(item.get("id") or "").startswith("style_gallery:") for item in stored
    )


# ---------------------------------------- 场景 360 全景（aipdd fork delta req98）


def test_sync_pulls_scene_master_and_360_pano(sync_client) -> None:
    """scene360 提交后的 360 全景也进主线同步：与 master 各自成条、幂等。"""
    client, project_dir, _character, store = sync_client
    store.scenes = [SimpleNamespace(name="厨房")]
    _write_png(project_dir / "assets" / "scenes" / "厨房" / "master.png")
    _write_png(project_dir / "director_worlds" / "厨房" / "v1" / "pano_360.png")

    items = _synced(client)

    master = items["mainline:scene:厨房"]
    pano = items["mainline:scene:厨房:pano"]
    assert master["name"] == "厨房"
    assert pano["name"] == "厨房 · 360 全景"
    for item in (master, pano):
        assert item["media"] == "image"
        assert item["source"] == "scene"
        assert item["category"] == "scene"
    assert _decoded_path(pano["image_urls"][0]).endswith(
        "/director_worlds/厨房/v1/pano_360.png"
    )

    # 重复同步只更新、不新增条目。
    again = _synced(client)
    scene_ids = [key for key in again if key.startswith("mainline:scene:厨房")]
    assert sorted(scene_ids) == ["mainline:scene:厨房", "mainline:scene:厨房:pano"]
