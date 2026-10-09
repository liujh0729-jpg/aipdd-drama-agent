# SPDX-License-Identifier: Elastic-2.0
# Copyright (c) 2026 ClaymoreLab
"""``GET /aipdd/drafts/<draft_id>/download`` 的回归测试。

资产库「剪映草稿」的下载入口依赖这条路由把草稿文件夹打包成 zip：这里钉住
打包内容、draft_id 白名单（含纯点段与目录穿越）和不存在草稿的 404 语义。
"""

from __future__ import annotations

import zipfile
from io import BytesIO

import pytest


@pytest.fixture()
def drafts_client(monkeypatch, tmp_path):
    flask = pytest.importorskip("flask")
    from aipdd import server

    drafts = tmp_path / "drafts"
    drafts.mkdir(parents=True)
    monkeypatch.setattr(server, "default_draft_dir", lambda: drafts)

    app = flask.Flask(__name__)
    server._register_extras(app)
    return app.test_client(), drafts


def test_download_packs_draft_folder_as_zip(drafts_client) -> None:
    http, drafts = drafts_client
    draft_dir = drafts / "dfd_abc123"
    draft_dir.mkdir()
    (draft_dir / "draft_content.json").write_text('{"ok": true}', encoding="utf-8")
    (draft_dir / "materials").mkdir()
    (draft_dir / "materials" / "clip.mp4").write_bytes(b"video-bytes")

    response = http.get("/aipdd/drafts/dfd_abc123/download")

    assert response.status_code == 200
    assert response.mimetype == "application/zip"
    disposition = response.headers["Content-Disposition"]
    assert "attachment" in disposition
    assert "dfd_abc123.zip" in disposition
    with zipfile.ZipFile(BytesIO(response.data)) as archive:
        names = set(archive.namelist())
        assert "draft_content.json" in names
        assert "materials/clip.mp4" in names


@pytest.mark.parametrize("draft_id", ["bad!name", "x" * 65, "%2e%2e", "%2e"])
def test_download_rejects_invalid_ids(drafts_client, draft_id: str) -> None:
    http, drafts = drafts_client
    # 建一个真实草稿目录，证明被拦下的是 id 本身而不是「目录不存在」。
    (drafts / "dfd_ok").mkdir()

    response = http.get(f"/aipdd/drafts/{draft_id}/download")

    assert response.status_code == 400
    assert response.get_json()["success"] is False


def test_download_missing_draft_is_404(drafts_client) -> None:
    http, _drafts = drafts_client

    response = http.get("/aipdd/drafts/dfd_missing/download")

    assert response.status_code == 404
    assert response.get_json()["success"] is False


def test_download_refuses_symlink_escape(drafts_client, tmp_path) -> None:
    """resolve 后父目录不再是草稿根（符号链接绕出去）的一律按不存在处理。"""
    http, drafts = drafts_client
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("secret", encoding="utf-8")
    (drafts / "dfd_evil").symlink_to(outside)

    response = http.get("/aipdd/drafts/dfd_evil/download")

    assert response.status_code == 404