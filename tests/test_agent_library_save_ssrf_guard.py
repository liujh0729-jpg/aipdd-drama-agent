# SPDX-License-Identifier: Elastic-2.0
# Copyright (c) 2026 ClaymoreLab
"""[aipdd fork delta] Agent 存库外链 SSRF 守卫回归。

``_localize_media_url`` 的 ``http(s)`` 分支把用户可控外链交
``download_media`` 抓回字节、写入项目 ``_uploads`` 并回 ``/static`` URL——与白模
外链同类 SSRF + 数据外泄面。断言：内网 URL 在触网前即被拒（download_media 从不被
调用）、公网外链调用时关闭重定向。
"""

from __future__ import annotations

import pytest
from fastapi import HTTPException

from novelvideo.agent import gateway as agent_gateway
from novelvideo.agent import routes as agent_routes


@pytest.mark.parametrize(
    "url",
    [
        "http://169.254.169.254/latest/meta-data/",
        "http://127.0.0.1:8780/api/v1/admin/world/status",
        "http://localhost/secret",
        "http://10.0.0.9/internal.png",
    ],
)
async def test_localize_rejects_internal_before_any_download(tmp_path, monkeypatch, url) -> None:
    def explode(*_a, **_k):
        raise AssertionError("download_media must not run for a rejected URL")

    monkeypatch.setattr(agent_gateway, "download_media", explode)
    with pytest.raises(HTTPException) as exc:
        await agent_routes._localize_media_url(
            url, project="proj", project_dir=tmp_path, username="alice", media="image"
        )
    assert exc.value.status_code == 400


async def test_localize_public_download_disables_redirects(tmp_path, monkeypatch) -> None:
    captured: dict[str, object] = {}

    async def fake_download(url, *, timeout: float = 120.0, follow_redirects: bool = True):
        captured["url"] = url
        captured["follow_redirects"] = follow_redirects
        return b"\x89PNG-bytes", "image/png"

    monkeypatch.setattr(agent_gateway, "download_media", fake_download)
    out = await agent_routes._localize_media_url(
        "http://8.8.8.8/valid.png",
        project="proj",
        project_dir=tmp_path,
        username="alice",
        media="image",
    )
    assert captured["follow_redirects"] is False
    assert out.startswith("/static/projects/proj/")
