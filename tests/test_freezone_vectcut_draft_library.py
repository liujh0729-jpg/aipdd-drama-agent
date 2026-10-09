# SPDX-License-Identifier: Elastic-2.0
# Copyright (c) 2026 ClaymoreLab
"""资产库「剪映草稿」的回归测试（VectCut 草稿单独成类，aipdd fork delta）。

覆盖四件事：
- 登记落位：media="draft" / category="draft" / folder="draft"，带 draft_id，稳定 id
  前缀让重复保存走 upsert 而不是新增；
- 保留名：「剪映草稿」不允许作为自建文件夹名，draft 是保留文件夹 key；
- 下载代理：draft_id 白名单校验（400）、上游 404/5xx/不可达的转发语义、
  正常情况下把 VectCut 的 zip 以附件形式流回；
- 工具挂点：save_draft 成功后自动登记，失败/无项目上下文时静默不动工具结果。
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from novelvideo.freezone.video_node import (
    DRAFT_FOLDER_KEY,
    RESERVED_FOLDER_KEYS,
    RESERVED_FOLDER_NAMES,
    VECTCUT_DRAFT_ITEM_ID_PREFIX,
    add_video_character_folder,
    library_folder_keys,
    load_video_character_library,
    register_vectcut_draft_item,
)


# --------------------------------------------------------------- 登记落位与保留名


def test_register_vectcut_draft_item_lands_in_draft_bucket(tmp_path: Path) -> None:
    project_dir = tmp_path / "project"

    item = register_vectcut_draft_item(project_dir, draft_id="dfd_abc123", name="EP01 精剪")

    assert item["id"] == f"{VECTCUT_DRAFT_ITEM_ID_PREFIX}dfd_abc123"
    assert item["media"] == "draft"
    assert item["source"] == "upload"
    assert item["category"] == "draft"
    assert item["folder"] == "draft"
    assert item["draft_id"] == "dfd_abc123"
    # 草稿没有可直接预览的素材 URL，三个媒体字段都为空。
    assert item["image_urls"] == []
    assert item["video_url"] is None
    assert item["audio_url"] is None
    stored = load_video_character_library(project_dir)
    assert [it["id"] for it in stored] == [f"{VECTCUT_DRAFT_ITEM_ID_PREFIX}dfd_abc123"]


def test_register_same_draft_upserts_instead_of_duplicating(tmp_path: Path) -> None:
    project_dir = tmp_path / "project"

    first = register_vectcut_draft_item(project_dir, draft_id="dfd_1", name="旧名字")
    again = register_vectcut_draft_item(project_dir, draft_id="dfd_1", name="EP01 精剪")

    assert again["id"] == first["id"]
    assert again["created_at"] == first["created_at"]
    stored = load_video_character_library(project_dir)
    assert len(stored) == 1
    assert stored[0]["name"] == "EP01 精剪"
    assert stored[0]["draft_id"] == "dfd_1"


def test_draft_bucket_is_reserved_for_system_folders(tmp_path: Path) -> None:
    project_dir = tmp_path / "project"

    # draft 是保留文件夹 key；「剪映草稿」是保留的文件夹名，用户建不出来。
    assert DRAFT_FOLDER_KEY == "draft"
    assert "draft" in RESERVED_FOLDER_KEYS
    assert "剪映草稿" in RESERVED_FOLDER_NAMES
    assert "draft" in library_folder_keys(project_dir)
    with pytest.raises(ValueError):
        add_video_character_folder(project_dir, name="剪映草稿")


# --------------------------------------------------------------- API 下载代理


@pytest.fixture()
def draft_client(monkeypatch, tmp_path):
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
    roles: list[str] = []

    async def fake_resolve(project: str, user: dict, *, required_role: str = "editor"):
        roles.append(required_role)
        return ctx, "alice", "demo", project_dir, str(project_dir)

    monkeypatch.setattr(freezone, "_resolve_freezone_project", fake_resolve)
    monkeypatch.setenv("VECTCUT_BASE_URL", "http://vectcut-test:9001")
    # 剪映草稿目录设置（req15）落在 runtime settings 库里；隔离到 tmp_path，
    # 保证「未配置 → 不带 draft_folder」这类断言不受开发机设置影响。
    from novelvideo import model_gateway_settings

    monkeypatch.setattr(
        model_gateway_settings, "_settings_db_path", lambda: tmp_path / "settings.db"
    )

    app = FastAPI()
    app.include_router(freezone.router, prefix="/api/v1")
    app.dependency_overrides[get_api_user] = lambda: {
        "id": "u-alice",
        "username": "alice",
    }
    return TestClient(app), roles, project_dir


_DRAFT_DOWNLOAD = "/api/v1/projects/proj_demo/freezone/video/drafts/{draft_id}/download"
_LIBRARY_URL = "/api/v1/projects/proj_demo/freezone/video/character-library"


class _FakeUpstream:
    def __init__(self, status_code: int, body: bytes, chunks: list[bytes] | None = None):
        self.status_code = status_code
        self._body = body
        self._chunks = chunks if chunks is not None else [body]
        self.closed = False

    async def aread(self) -> bytes:
        return self._body

    async def aiter_bytes(self):
        for chunk in self._chunks:
            yield chunk

    def json(self) -> object:
        return json.loads(self._body.decode("utf-8"))

    async def aclose(self) -> None:
        self.closed = True


class _FakeAsyncClient:
    """顶替 httpx.AsyncClient：记录转发请求，按预设返回或抛错。"""

    def __init__(self, *, response: _FakeUpstream | None, error: Exception | None):
        self._response = response
        self._error = error
        self.requests: list[tuple[str, str, bool]] = []
        self.posts: list[tuple[str, dict, list]] = []
        self.gets: list[tuple[str, dict | None]] = []
        self.last_headers: dict = {}
        self.closed = False

    def build_request(self, method: str, url: str, params=None, headers=None) -> tuple[str, str]:
        self.last_headers = headers or {}
        return method, url

    async def send(self, request, stream: bool = False):
        self.requests.append((request[0], request[1], stream))
        if self._error is not None:
            raise self._error
        assert self._response is not None
        return self._response

    async def post(self, url, data=None, files=None, headers=None):
        self.last_headers = headers or {}
        self.posts.append((url, data, files))
        if self._error is not None:
            raise self._error
        assert self._response is not None
        return self._response

    async def get(self, url, params=None, headers=None):
        self.last_headers = headers or {}
        self.gets.append((url, params))
        if self._error is not None:
            raise self._error
        assert self._response is not None
        return self._response

    async def aclose(self) -> None:
        self.closed = True


def _patch_httpx(monkeypatch, *, response=None, error=None) -> _FakeAsyncClient:
    from novelvideo.api.routes.freezone import library as library_module

    fake = _FakeAsyncClient(response=response, error=error)
    monkeypatch.setattr(
        library_module,
        "httpx",
        SimpleNamespace(
            AsyncClient=lambda **kwargs: fake,
            Timeout=lambda *args, **kwargs: object(),
            HTTPError=httpx.HTTPError,
        ),
    )
    return fake


def test_upload_into_draft_folder_is_rejected(draft_client) -> None:
    """普通素材不能上传进「剪映草稿」——和主线文件夹同一条守卫。"""
    client, _roles, project_dir = draft_client
    asset = project_dir / "freezone" / "_uploads" / "ref.png"
    asset.parent.mkdir(parents=True, exist_ok=True)
    asset.write_bytes(b"png")
    payload = {
        "name": "误传进草稿",
        "image_urls": ["/freezone/_uploads/ref.png"],
    }

    for folder in ("draft", "mainline"):
        response = client.post(_LIBRARY_URL, json={**payload, "folder": folder})
        assert response.status_code == 400, response.text
        assert f"cannot upload into the {folder} folder" in response.text
    assert load_video_character_library(project_dir) == []


@pytest.mark.parametrize("draft_id", ["bad!name", "x" * 65, "%2e%2e"])
def test_draft_download_rejects_invalid_ids(draft_client, draft_id: str) -> None:
    client, roles, _project_dir = draft_client

    response = client.get(_DRAFT_DOWNLOAD.format(draft_id=draft_id))

    assert response.status_code == 400
    # 白名单校验先于项目解析，非法 id 连项目都不碰。
    assert roles == []


def test_draft_download_streams_zip_from_vectcut(draft_client, monkeypatch) -> None:
    client, roles, _project_dir = draft_client
    fake = _patch_httpx(
        monkeypatch,
        response=_FakeUpstream(200, b"", chunks=[b"PK\x03\x04", b"zip-bytes"]),
    )

    response = client.get(_DRAFT_DOWNLOAD.format(draft_id="dfd_abc123"))

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/zip"
    assert (
        response.headers["content-disposition"]
        == 'attachment; filename="dfd_abc123.zip"'
    )
    assert response.content == b"PK\x03\x04zip-bytes"
    # 转发的上游地址固定为 VectCut 的草稿包端点；项目解析按 viewer 角色放行。
    assert fake.requests == [
        ("GET", "http://vectcut-test:9001/aipdd/drafts/dfd_abc123/download", True)
    ]
    assert roles == ["viewer"]


def test_draft_download_maps_upstream_404(draft_client, monkeypatch) -> None:
    client, _roles, _project_dir = draft_client
    _patch_httpx(monkeypatch, response=_FakeUpstream(404, b"draft not found"))

    response = client.get(_DRAFT_DOWNLOAD.format(draft_id="dfd_missing"))

    assert response.status_code == 404
    assert "draft not found" in response.text


def test_draft_download_maps_upstream_error_to_502(draft_client, monkeypatch) -> None:
    client, _roles, _project_dir = draft_client
    _patch_httpx(monkeypatch, response=_FakeUpstream(500, b"packing failed"))

    response = client.get(_DRAFT_DOWNLOAD.format(draft_id="dfd_broken"))

    assert response.status_code == 502
    assert "packing failed" in response.text


def test_draft_download_maps_unreachable_service_to_502(draft_client, monkeypatch) -> None:
    client, _roles, _project_dir = draft_client
    _patch_httpx(monkeypatch, error=httpx.HTTPError("connection refused"))

    response = client.get(_DRAFT_DOWNLOAD.format(draft_id="dfd_down"))

    assert response.status_code == 502
    assert "cannot reach the VectCut service" in response.text


# --------------------------------------------------------------- 工具挂点：save_draft 后自动归档


def test_archive_vectcut_draft_registers_item(tmp_path: Path, monkeypatch) -> None:
    from novelvideo.chat import dramaclaw_tools

    project_dir = tmp_path / "project"
    monkeypatch.setenv("DRAMACLAW_PROJECT_OUTPUT_DIR", str(project_dir))

    result: dict = {"success": True, "draft_url": "/drafts/dfd_abc123"}
    dramaclaw_tools._archive_vectcut_draft(
        {"draft_id": "dfd_abc123", "project_name": "EP01 精剪"}, result
    )

    assert result["draft_archived_to_library"] == {
        "item_id": f"{VECTCUT_DRAFT_ITEM_ID_PREFIX}dfd_abc123",
        "name": "EP01 精剪",
        "folder": "剪映草稿",
    }
    stored = load_video_character_library(project_dir)
    assert len(stored) == 1
    assert stored[0]["media"] == "draft"
    assert stored[0]["draft_id"] == "dfd_abc123"


def test_archive_vectcut_draft_falls_back_to_draft_id_as_name(
    tmp_path: Path, monkeypatch
) -> None:
    from novelvideo.chat import dramaclaw_tools

    project_dir = tmp_path / "project"
    monkeypatch.setenv("DRAMACLAW_PROJECT_OUTPUT_DIR", str(project_dir))

    result: dict = {"success": True}
    dramaclaw_tools._archive_vectcut_draft({"draft_id": "dfd_named"}, result)

    assert result["draft_archived_to_library"]["name"] == "dfd_named"


def test_archive_vectcut_draft_skips_failed_saves(tmp_path: Path, monkeypatch) -> None:
    from novelvideo.chat import dramaclaw_tools

    project_dir = tmp_path / "project"
    monkeypatch.setenv("DRAMACLAW_PROJECT_OUTPUT_DIR", str(project_dir))

    for result in ({"success": False, "error": "boom"}, "not-a-dict"):
        dramaclaw_tools._archive_vectcut_draft({"draft_id": "dfd_x"}, result)
    # 缺少 draft_id 同样不动库。
    ok_result: dict = {"success": True}
    dramaclaw_tools._archive_vectcut_draft({}, ok_result)

    assert "draft_archived_to_library" not in ok_result
    assert load_video_character_library(project_dir) == []


def test_archive_vectcut_draft_noops_without_project_context(monkeypatch) -> None:
    from novelvideo.chat import dramaclaw_tools

    monkeypatch.delenv("DRAMACLAW_PROJECT_OUTPUT_DIR", raising=False)
    monkeypatch.delenv("SUPERTALE_PROJECT_OUTPUT_DIR", raising=False)

    result: dict = {"success": True}
    dramaclaw_tools._archive_vectcut_draft({"draft_id": "dfd_noc"}, result)

    assert result == {"success": True}


def test_archive_vectcut_draft_swallows_registration_errors(
    tmp_path: Path, monkeypatch
) -> None:
    """登记失败不能翻车：save_draft 的成功结果保持原样，不加噪声字段。"""
    from novelvideo.chat import dramaclaw_tools

    project_dir = tmp_path / "project"
    freezone_dir = project_dir / "freezone"
    freezone_dir.mkdir(parents=True)
    # 把库文件路径占成目录，登记时必然失败。
    (freezone_dir / "video_character_library.json").mkdir()
    monkeypatch.setenv("DRAMACLAW_PROJECT_OUTPUT_DIR", str(project_dir))

    result: dict = {"success": True, "draft_url": "/drafts/dfd_bad"}
    dramaclaw_tools._archive_vectcut_draft({"draft_id": "dfd_bad"}, result)

    assert result == {"success": True, "draft_url": "/drafts/dfd_bad"}
    # 库路径仍是被占位的目录（登记失败时没有留下半截文件）。
    assert (freezone_dir / "video_character_library.json").is_dir()


# ------------------------------------------------- 草稿上传 / 目录同步（req15）

_DRAFT_UPLOAD = "/api/v1/projects/proj_demo/freezone/video/drafts/upload"
_DRAFT_SYNC = "/api/v1/projects/proj_demo/freezone/video/drafts/sync"


def _upload_multipart(name: str = "EP01 精剪") -> dict:
    return {
        "data": {
            "name": name,
            "relative_paths": ["draft_content.json", "materials/clip.mp4"],
        },
        "files": [
            ("files", ("draft_content.json", b'{"ok": true}', "application/json")),
            ("files", ("clip.mp4", b"video-bytes", "video/mp4")),
        ],
    }


def test_draft_upload_forwards_files_and_registers_item(
    draft_client, monkeypatch
) -> None:
    """上传文件夹：平铺文件 + relative_paths 转发 VectCut，成功后登记成草稿条目。"""
    client, _roles, project_dir = draft_client
    upstream_body = json.dumps(
        {"success": True, "output": {"draft_id": "up_abc", "files": 2}}
    ).encode()
    fake = _patch_httpx(monkeypatch, response=_FakeUpstream(200, upstream_body))

    response = client.post(_DRAFT_UPLOAD, **_upload_multipart())

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["ok"] is True
    item = payload["data"]
    assert item["media"] == "draft"
    assert item["folder"] == "draft"
    assert item["name"] == "EP01 精剪"
    # 转发给 VectCut 的形态：白名单 draft_id + 同序相对路径 + 文件名平铺
    assert len(fake.posts) == 1
    url, form, files = fake.posts[0]
    assert url.endswith("/aipdd/drafts/upload")
    assert form["draft_id"].startswith("up_")
    assert form["relative_paths"] == ["draft_content.json", "materials/clip.mp4"]
    assert [entry[1][0] for entry in files] == ["draft_content.json", "clip.mp4"]
    # 设置未配置草稿目录时透传不带 draft_folder（VectCut 用部署默认）。
    assert "draft_folder" not in form
    assert [it["media"] for it in load_video_character_library(project_dir)] == ["draft"]


def test_draft_upload_validates_name_and_relative_paths(
    draft_client, monkeypatch
) -> None:
    client, _roles, _project_dir = draft_client
    _patch_httpx(monkeypatch, response=_FakeUpstream(200, b"{}"))

    blank_name = client.post(
        _DRAFT_UPLOAD,
        data={"name": "   ", "relative_paths": ["a.txt"]},
        files=[("files", ("a.txt", b"x", "text/plain"))],
    )
    assert blank_name.status_code == 400
    assert "name is required" in blank_name.text

    mismatch = client.post(
        _DRAFT_UPLOAD,
        data={"name": "EP01", "relative_paths": ["a.txt", "b.txt"]},
        files=[("files", ("a.txt", b"x", "text/plain"))],
    )
    assert mismatch.status_code == 400
    assert "relative_paths" in mismatch.text


def test_draft_upload_maps_upstream_rejection(draft_client, monkeypatch) -> None:
    client, _roles, _project_dir = draft_client

    rejected_body = json.dumps(
        {"success": False, "error": "draft already exists: up_dup"}
    ).encode()
    _patch_httpx(monkeypatch, response=_FakeUpstream(400, rejected_body))
    rejected = client.post(_DRAFT_UPLOAD, **_upload_multipart())
    assert rejected.status_code == 400
    assert "already exists" in rejected.text

    _patch_httpx(monkeypatch, response=_FakeUpstream(500, b"boom"))
    failed = client.post(_DRAFT_UPLOAD, **_upload_multipart())
    assert failed.status_code == 502


def test_draft_upload_includes_configured_draft_folder(
    draft_client, monkeypatch
) -> None:
    """设置里配置了草稿目录：上传透传 draft_folder，与下载 / 同步同一口径。"""
    client, _roles, _project_dir = draft_client
    from novelvideo import model_gateway_settings

    model_gateway_settings.save_vectcut_draft_dir("/custom/drafts")
    upstream_body = json.dumps({"success": True, "output": {}}).encode()
    fake = _patch_httpx(monkeypatch, response=_FakeUpstream(200, upstream_body))

    response = client.post(_DRAFT_UPLOAD, **_upload_multipart())

    assert response.status_code == 200, response.text
    assert fake.posts[0][1]["draft_folder"] == "/custom/drafts"


def test_draft_sync_registers_new_and_skips_known_and_invalid(
    draft_client, monkeypatch
) -> None:
    """目录是真源：已登记的不动，新草稿补登记，非法 draft_id 跳过。"""
    client, _roles, project_dir = draft_client
    register_vectcut_draft_item(project_dir, draft_id="dfd_a", name="已登记")

    body = json.dumps(
        {
            "success": True,
            "drafts": [
                {"draft_id": "dfd_a"},
                {"draft_id": "dfd_b"},
                {"draft_id": "../evil"},
                {"draft_id": "dfd_c"},
            ],
        }
    ).encode()
    fake = _patch_httpx(monkeypatch, response=_FakeUpstream(200, body))

    response = client.post(_DRAFT_SYNC)

    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["added"] == 2
    assert data["total"] == 4
    assert {item["name"] for item in data["items"]} == {"dfd_b", "dfd_c"}
    ids = {item["id"] for item in load_video_character_library(project_dir)}
    assert f"{VECTCUT_DRAFT_ITEM_ID_PREFIX}dfd_a" in ids
    assert f"{VECTCUT_DRAFT_ITEM_ID_PREFIX}dfd_b" in ids
    assert f"{VECTCUT_DRAFT_ITEM_ID_PREFIX}dfd_c" in ids
    assert not any("evil" in item_id for item_id in ids)
    # 未配置设置时上游请求不带 draft_folder 参数。
    assert fake.gets[0][1] is None


def test_draft_sync_maps_unreachable_service_to_502(draft_client, monkeypatch) -> None:
    client, _roles, _project_dir = draft_client
    _patch_httpx(monkeypatch, error=httpx.HTTPError("connection refused"))

    response = client.post(_DRAFT_SYNC)

    assert response.status_code == 502
    assert "cannot reach the VectCut service" in response.text


def test_vectcut_draft_params_follow_configured_dir(monkeypatch, tmp_path) -> None:
    """下载 / 上传 / 同步共用的透传参数跟着设置走，空配置回 None。"""
    from novelvideo import model_gateway_settings
    from novelvideo.api.routes.freezone import library as library_module

    monkeypatch.setattr(
        model_gateway_settings, "_settings_db_path", lambda: tmp_path / "settings.db"
    )
    assert library_module._vectcut_draft_params() is None

    model_gateway_settings.save_vectcut_draft_dir("/drafts")
    assert library_module._vectcut_draft_params() == {"draft_folder": "/drafts"}

    model_gateway_settings.save_vectcut_draft_dir("")
    assert library_module._vectcut_draft_params() is None

def test_vectcut_headers_optional_shared_token(monkeypatch) -> None:
    """O19：未设 VECTCUT_INTERNAL_TOKEN 不带该头（向后兼容）；设了则透传。"""
    from novelvideo.api.routes.freezone import library as library_module

    monkeypatch.delenv("VECTCUT_INTERNAL_TOKEN", raising=False)
    assert library_module._vectcut_headers() == {}

    monkeypatch.setenv("VECTCUT_INTERNAL_TOKEN", "s-internal")
    assert library_module._vectcut_headers() == {"X-VectCut-Token": "s-internal"}

    monkeypatch.setenv("VECTCUT_INTERNAL_TOKEN", "   ")
    assert library_module._vectcut_headers() == {}


def test_draft_download_attaches_internal_token(draft_client, monkeypatch) -> None:
    """下载转发时把共享密钥头透传给上游；未设时不附带。"""
    client, _roles, _project_dir = draft_client

    monkeypatch.setenv("VECTCUT_INTERNAL_TOKEN", "s-internal")
    fake = _patch_httpx(
        monkeypatch,
        response=_FakeUpstream(200, b"", chunks=[b"PK\x03\x04"]),
    )
    response = client.get(_DRAFT_DOWNLOAD.format(draft_id="dfd_abc123"))
    assert response.status_code == 200
    assert fake.last_headers.get("X-VectCut-Token") == "s-internal"

    # 未设时不附带该头（向后兼容）。
    monkeypatch.delenv("VECTCUT_INTERNAL_TOKEN", raising=False)
    fake2 = _patch_httpx(
        monkeypatch,
        response=_FakeUpstream(200, b"", chunks=[b"PK\x03\x04"]),
    )
    response2 = client.get(_DRAFT_DOWNLOAD.format(draft_id="dfd_abc123"))
    assert response2.status_code == 200
    assert "X-VectCut-Token" not in fake2.last_headers
