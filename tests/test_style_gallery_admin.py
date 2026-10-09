# SPDX-License-Identifier: Elastic-2.0
# Copyright (c) 2026 ClaymoreLab
"""[aipdd fork delta] 设置→风格管理：本地封面上传/服务/删除的回归测试。

覆盖：
1. 超管闸门（member / agent token 被拒，owner 放行）；
2. 上传 → 列表 has_cover → 同源 serve 200（content-type 按字节魔数）→ 删除 → 404 全链路；
3. style_id allowlist（未知风格 404）、非法类型 415、空文件 400；
4. serve 文件名必须是 cover.webp，否则 404；
5. AI 生成封面的模型选择：/style-gallery/image-models 目录端点 + cover/generate
   的 model 参数解析（catalogId / id / apiModel 均接受，执行身份折算成目录条目的
   provider + apiModel；未知模型 409 且不落图；不传 model 时沿用默认图片模型，
   不查目录）。

清单用 monkeypatch 固定成两条已知风格，避免依赖随版本漂移的内置图墙清单。
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from novelvideo.api.auth import get_api_user
from novelvideo.api.routes import styles as styles_routes
from novelvideo.freezone import jobs as freezone_jobs
from novelvideo.freezone import style_gallery

PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
JPEG_BYTES = b"\xff\xd8\xff\xe0" + b"\x00" * 64

_FAKE_TEMPLATES = [
    {
        "id": "s1",
        "label": "Style One",
        "category": "cat_a",
        "cover": "s1/cover.webp",
        "style_prompt": "cinematic soft glow",
    },
    {
        "id": "s2",
        "label": "Style Two",
        "category": "cat_b",
        "cover": "s2/cover.webp",
        "style_prompt": "ink wash minimal",
    },
]


def _build_client(monkeypatch, tmp_path, user):
    monkeypatch.setattr(style_gallery, "STATE_DIR", str(tmp_path))
    monkeypatch.setattr(style_gallery, "load_style_templates", lambda: list(_FAKE_TEMPLATES))

    app = FastAPI()
    app.include_router(styles_routes.router, prefix="/api/v1")
    app.dependency_overrides[get_api_user] = lambda: dict(user)
    return TestClient(app)


@pytest.fixture()
def owner_client(monkeypatch, tmp_path):
    return _build_client(
        monkeypatch, tmp_path, {"id": "u-owner", "username": "admin", "role": "owner"}
    )


# --------------------------------------------------------------- 权限门


def test_member_is_rejected(monkeypatch, tmp_path):
    client = _build_client(
        monkeypatch, tmp_path, {"id": "u-mem", "username": "alice", "role": "member"}
    )
    assert client.get("/api/v1/style-gallery/styles").status_code == 403
    resp = client.post(
        "/api/v1/style-gallery/s1/cover",
        files={"file": ("cover.png", PNG_BYTES, "image/png")},
    )
    assert resp.status_code == 403


def test_agent_token_is_rejected_even_with_owner_role(monkeypatch, tmp_path):
    client = _build_client(
        monkeypatch,
        tmp_path,
        {"id": "u-agent", "username": "bot", "role": "owner", "credential_kind": "agent_session"},
    )
    assert client.get("/api/v1/style-gallery/styles").status_code == 403


# --------------------------------------------------------------- 全链路


def test_upload_list_serve_delete_flow(owner_client):
    # 初始：两条风格都无封面。
    listing = owner_client.get("/api/v1/style-gallery/styles").json()
    assert listing["ok"] is True
    rows = {row["id"]: row for row in listing["data"]}
    assert rows["s1"]["has_cover"] is False
    assert rows["s1"]["cover_url"] is None

    # 上传 png（落盘文件名仍是 cover.webp）。
    upload = owner_client.post(
        "/api/v1/style-gallery/s1/cover",
        files={"file": ("cover.png", PNG_BYTES, "image/png")},
    )
    assert upload.status_code == 200
    body = upload.json()
    assert body["ok"] is True
    assert body["data"]["has_cover"] is True
    assert body["data"]["cover_url"].startswith("/api/v1/style-gallery/s1/cover.webp?v=")

    # 列表反映已上传。
    rows = {row["id"]: row for row in owner_client.get("/api/v1/style-gallery/styles").json()["data"]}
    assert rows["s1"]["has_cover"] is True
    assert rows["s1"]["cover_url"] is not None

    # 同源 serve：免鉴权、content-type 按真实字节（png），带 cache-control。
    serve = owner_client.get("/api/v1/style-gallery/s1/cover.webp")
    assert serve.status_code == 200
    assert serve.headers["content-type"] == "image/png"
    assert serve.content == PNG_BYTES
    assert "public" in serve.headers.get("cache-control", "")

    # 删除 → 回落占位、serve 404。
    deleted = owner_client.delete("/api/v1/style-gallery/s1/cover").json()
    assert deleted["ok"] is True
    assert deleted["data"]["removed"] is True
    assert owner_client.get("/api/v1/style-gallery/s1/cover.webp").status_code == 404
    rows = {row["id"]: row for row in owner_client.get("/api/v1/style-gallery/styles").json()["data"]}
    assert rows["s1"]["has_cover"] is False


def test_serve_sniffs_jpeg_content_type(owner_client):
    owner_client.post(
        "/api/v1/style-gallery/s2/cover",
        files={"file": ("photo.jpg", JPEG_BYTES, "image/jpeg")},
    )
    serve = owner_client.get("/api/v1/style-gallery/s2/cover.webp")
    assert serve.status_code == 200
    assert serve.headers["content-type"] == "image/jpeg"


# --------------------------------------------------------------- 校验分支


def test_upload_unknown_style_is_404(owner_client):
    resp = owner_client.post(
        "/api/v1/style-gallery/nope/cover",
        files={"file": ("cover.png", PNG_BYTES, "image/png")},
    )
    assert resp.status_code == 404


def test_upload_bad_type_is_415(owner_client):
    resp = owner_client.post(
        "/api/v1/style-gallery/s1/cover",
        files={"file": ("cover.bmp", b"BM" + b"\x00" * 32, "image/bmp")},
    )
    assert resp.status_code == 415


def test_upload_empty_file_is_400(owner_client):
    resp = owner_client.post(
        "/api/v1/style-gallery/s1/cover",
        files={"file": ("cover.png", b"", "image/png")},
    )
    assert resp.status_code == 400


def test_serve_rejects_unexpected_filename(owner_client):
    owner_client.post(
        "/api/v1/style-gallery/s1/cover",
        files={"file": ("cover.png", PNG_BYTES, "image/png")},
    )
    assert owner_client.get("/api/v1/style-gallery/s1/evil.png").status_code == 404


def test_delete_unknown_style_is_404(owner_client):
    assert owner_client.delete("/api/v1/style-gallery/nope/cover").status_code == 404


# --------------------------------------------------------------- AI 生成封面


def _fake_generator(monkeypatch, tmp_path, captured):
    """把画布同源出图函数 run_freezone_gen 换成一个写死 PNG 的假实现，
    顺带把 kwargs 透到 captured。"""

    async def _fake(**kwargs):
        captured.update(kwargs)
        out = Path(str(kwargs.get("project_dir") or tmp_path)) / "gen_0.png"
        out.write_bytes(PNG_BYTES)
        return out

    monkeypatch.setattr(freezone_jobs, "run_freezone_gen", _fake)


def test_generate_requires_owner(monkeypatch, tmp_path):
    client = _build_client(
        monkeypatch, tmp_path, {"id": "u-mem", "username": "alice", "role": "member"}
    )
    resp = client.post("/api/v1/style-gallery/s1/cover/generate")
    assert resp.status_code == 403


def test_generate_unknown_style_is_404(owner_client):
    assert owner_client.post("/api/v1/style-gallery/nope/cover/generate").status_code == 404


def test_generate_success_saves_cover_and_passes_style_prompt(owner_client, monkeypatch, tmp_path):
    captured: dict = {}
    _fake_generator(monkeypatch, tmp_path, captured)

    resp = owner_client.post("/api/v1/style-gallery/s1/cover/generate")
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["data"]["has_cover"] is True
    assert body["data"]["cover_url"].startswith("/api/v1/style-gallery/s1/cover.webp?v=")

    # 出图走画布同源路径：prompt 是画布同款「风格模板块」，16:9 与图墙展示一致。
    assert captured.get("prompt") == "风格模板:\n- Style One\n- cinematic soft glow"
    assert captured.get("aspect_ratio") == "16:9"
    assert captured.get("job_id") == "style_gallery_cover"

    # 生成结果落盘为封面，同源 serve 回 png。
    serve = owner_client.get("/api/v1/style-gallery/s1/cover.webp")
    assert serve.status_code == 200
    assert serve.headers["content-type"] == "image/png"


def test_generate_overwrites_existing_cover(owner_client, monkeypatch, tmp_path):
    _fake_generator(monkeypatch, tmp_path, {})
    assert (
        owner_client.post("/api/v1/style-gallery/s1/cover/generate").json()["data"]["has_cover"]
        is True
    )


def test_generate_failure_is_502(owner_client, monkeypatch, tmp_path):
    async def _boom(**kwargs):
        raise RuntimeError("INSUFFICIENT_CREDITS")

    monkeypatch.setattr(freezone_jobs, "run_freezone_gen", _boom)
    resp = owner_client.post("/api/v1/style-gallery/s1/cover/generate")
    assert resp.status_code == 502
    # 未产出时不应落任何封面。
    assert owner_client.get("/api/v1/style-gallery/s1/cover.webp").status_code == 404


def test_generate_missing_output_is_502(owner_client, monkeypatch, tmp_path):
    async def _missing(**kwargs):
        return Path(str(kwargs["project_dir"])) / "nope.png"

    monkeypatch.setattr(freezone_jobs, "run_freezone_gen", _missing)
    assert (
        owner_client.post("/api/v1/style-gallery/s1/cover/generate").status_code == 502
    )


# --------------------------------------------------- AI 生成封面的模型选择

IMAGE_CATALOG = [
    {
        "catalogId": "cat-1",
        "id": "aipdd/ap-image-2",
        "providerId": "aipdd",
        "apiModel": "ap-image-2.0",
        "label": "AP Image 2.0",
        "channel": "aipdd",
    }
]


def _patch_image_catalog(monkeypatch, catalog, calls=None):
    """把 scoped 媒体模型目录换成固定替身。

    /style-gallery/image-models 与 cover/generate 都在调用时从
    novelvideo.api.routes.freezone 取这个名字，所以 patch 包属性即可同时
    覆盖两条路径（渲染端「能选到的」与生成端「能执行的」严格同源）。
    """

    async def _fake(media_type, *, requester_user_id):
        if calls is not None:
            calls.append((media_type, requester_user_id))
        return list(catalog)

    from novelvideo.api.routes import freezone as freezone_routes

    monkeypatch.setattr(freezone_routes, "_scoped_media_model_catalog", _fake)


def test_image_models_requires_owner(monkeypatch, tmp_path):
    client = _build_client(
        monkeypatch, tmp_path, {"id": "u-mem", "username": "alice", "role": "member"}
    )
    assert client.get("/api/v1/style-gallery/image-models").status_code == 403


def test_image_models_returns_scoped_catalog(owner_client, monkeypatch):
    calls: list = []
    _patch_image_catalog(monkeypatch, IMAGE_CATALOG, calls)

    resp = owner_client.get("/api/v1/style-gallery/image-models")
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["data"] == IMAGE_CATALOG
    # 目录按当前请求者身份取，与生成端同一口径。
    assert calls == [("image", "u-owner")]


@pytest.mark.parametrize("requested", ["aipdd/ap-image-2", "cat-1", "ap-image-2.0"])
def test_generate_with_model_resolves_execution_identity(
    owner_client, monkeypatch, tmp_path, requested
):
    captured: dict = {}
    _fake_generator(monkeypatch, tmp_path, captured)
    _patch_image_catalog(monkeypatch, IMAGE_CATALOG)

    resp = owner_client.post(
        "/api/v1/style-gallery/s1/cover/generate", json={"model": requested}
    )
    assert resp.status_code == 200
    # catalogId / id / apiModel 都接受；执行身份统一折算成目录条目的
    # provider + apiModel 下发给出图链路。
    assert captured.get("provider") == "aipdd"
    assert captured.get("model") == "ap-image-2.0"


def test_generate_unknown_model_is_409_and_does_not_call_generator(
    owner_client, monkeypatch, tmp_path
):
    captured: dict = {}
    _fake_generator(monkeypatch, tmp_path, captured)
    _patch_image_catalog(monkeypatch, IMAGE_CATALOG)

    resp = owner_client.post(
        "/api/v1/style-gallery/s1/cover/generate", json={"model": "nope/unknown"}
    )
    assert resp.status_code == 409
    assert captured == {}
    assert owner_client.get("/api/v1/style-gallery/s1/cover.webp").status_code == 404


def test_generate_without_model_keeps_default_model_path(
    owner_client, monkeypatch, tmp_path
):
    captured: dict = {}
    _fake_generator(monkeypatch, tmp_path, captured)

    async def _forbidden(media_type, *, requester_user_id):
        raise AssertionError("未指定模型时不应查询模型目录")

    from novelvideo.api.routes import freezone as freezone_routes

    monkeypatch.setattr(freezone_routes, "_scoped_media_model_catalog", _forbidden)

    # 不传 body（老客户端）与传空 body 都应沿用默认图片模型。
    assert owner_client.post("/api/v1/style-gallery/s1/cover/generate").status_code == 200
    assert captured.get("provider") is None
    assert captured.get("model") is None
