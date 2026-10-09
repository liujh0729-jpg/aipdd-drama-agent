# SPDX-License-Identifier: Elastic-2.0
# Copyright (c) 2026 ClaymoreLab
"""[aipdd fork delta] freezone 白模外链 SSRF 守卫测试。

覆盖两层：
- ``utils.net_guard.ensure_public_http_url``：拒私网/回环/链路本地/保留/多播/非
  http(s) 协议，公网放行，解析失败 fail-closed。
- ``uploads._fetch_white_model_bytes``：内网 URL 在触网前即被拒（零出网）、3xx 重定向
  被拒、构造客户端时关闭重定向、公网直连正常回字节。
"""

from __future__ import annotations

import pytest
from fastapi import HTTPException

from novelvideo.utils.net_guard import PublicUrlRejected, ensure_public_http_url


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1/x.mp4",
        "http://localhost:8780/api",
        "http://169.254.169.254/latest/meta-data/",
        "http://10.0.0.5/secret",
        "http://192.168.1.1/",
        "http://172.16.5.9/",
        "http://[::1]/x",
        "http://[::ffff:127.0.0.1]/x",
        "http://0.0.0.0/x",
        "http://224.0.0.1/x",
        "http://2130706433/x",  # 127.0.0.1 的十进制写法
        "file:///etc/passwd",
        "gopher://127.0.0.1:11211/",
        "http:///no-host",
    ],
)
def test_guard_rejects_non_public(url: str) -> None:
    with pytest.raises(PublicUrlRejected):
        ensure_public_http_url(url)


@pytest.mark.parametrize("url", ["http://8.8.8.8/x.mp4", "https://1.1.1.1/a"])
def test_guard_allows_public_ip(url: str) -> None:
    assert ensure_public_http_url(url) == url


def test_guard_fail_closed_on_unresolvable_host() -> None:
    with pytest.raises(PublicUrlRejected):
        ensure_public_http_url("http://this-host-does-not-exist.invalid/x")


class _FakeResponse:
    def __init__(self, status_code: int, content: bytes = b"", redirect: bool = False):
        self.status_code = status_code
        self.content = content
        self.is_redirect = redirect
        self.is_redirection = 300 <= status_code < 400

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"http {self.status_code}")


class _FakeClient:
    def __init__(self, response: _FakeResponse, captured: dict):
        self._response = response
        self._captured = captured
        captured.setdefault("get_urls", []).append(None)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc):
        return False

    async def get(self, url: str):
        self._captured["get_urls"][-1] = url
        return self._response


def _patch_client(monkeypatch, response: _FakeResponse) -> dict:
    from novelvideo.api.routes.freezone import uploads

    captured: dict = {}

    def factory(**kwargs):
        captured.update(kwargs)
        return _FakeClient(response, captured)

    monkeypatch.setattr(uploads.httpx, "AsyncClient", factory)
    return captured


async def test_fetch_rejects_internal_url_before_any_network(monkeypatch) -> None:
    from novelvideo.api.routes.freezone import uploads

    def explode(**_kwargs):
        raise AssertionError("AsyncClient must not be constructed for a rejected URL")

    monkeypatch.setattr(uploads.httpx, "AsyncClient", explode)
    with pytest.raises(HTTPException) as exc:
        await uploads._fetch_white_model_bytes("http://169.254.169.254/l")
    assert exc.value.status_code == 400


async def test_fetch_disables_redirects_and_returns_public_bytes(monkeypatch) -> None:
    from novelvideo.api.routes.freezone import uploads

    captured = _patch_client(monkeypatch, _FakeResponse(200, content=b"\x00vid"))
    data = await uploads._fetch_white_model_bytes("http://8.8.8.8/a.mp4")
    assert data == b"\x00vid"
    assert captured.get("follow_redirects") is False


async def test_fetch_rejects_redirect_response(monkeypatch) -> None:
    from novelvideo.api.routes.freezone import uploads

    _patch_client(monkeypatch, _FakeResponse(302, content=b"", redirect=True))
    with pytest.raises(HTTPException) as exc:
        await uploads._fetch_white_model_bytes("http://8.8.8.8/a.mp4")
    assert exc.value.status_code == 400
