# SPDX-License-Identifier: Elastic-2.0
# Copyright (c) 2026 ClaymoreLab
"""客户自带 Chrome CDP 地址解析（``utils/chrome_cdp.py``）回归测试。

Chrome 的 DevTools HTTP 端点只接受 IP / localhost 的 Host 头：容器里经
``host.docker.internal`` 访问会被直接拒（HTTP 500）。``browsable_cdp_url``
把主机名解析成 IP 再交给驱动；localhost、已是 IP 的地址原样保留，
解析失败（源码部署本机没有该主机名）也保留原值。
"""

from __future__ import annotations

import pytest

from novelvideo.utils.chrome_cdp import browsable_cdp_url


def test_localhost_and_ip_urls_keep_as_is() -> None:
    # 本机/桌面部署与已是 IP 的地址不做任何换算。
    assert browsable_cdp_url("http://localhost:9222") == "http://localhost:9222"
    assert browsable_cdp_url("http://127.0.0.1:9333") == "http://127.0.0.1:9333"
    assert browsable_cdp_url("http://192.168.5.2:9222") == "http://192.168.5.2:9222"


def test_hostname_is_resolved_to_ip(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "novelvideo.utils.chrome_cdp.socket.gethostbyname",
        lambda _host: "192.168.65.254",
    )
    assert (
        browsable_cdp_url("http://host.docker.internal:9222")
        == "http://192.168.65.254:9222"
    )
    # 无端口 + 带路径：端口不出现、路径原样保留。
    assert (
        browsable_cdp_url("http://host.docker.internal/json/version")
        == "http://192.168.65.254/json/version"
    )


def test_unresolvable_hostname_keeps_original(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _fail(_host: str) -> str:
        raise OSError("name not known")

    monkeypatch.setattr("novelvideo.utils.chrome_cdp.socket.gethostbyname", _fail)
    url = "http://host.docker.internal:9222"
    assert browsable_cdp_url(url) == url


def test_blank_and_malformed_input() -> None:
    assert browsable_cdp_url("") == ""
    assert browsable_cdp_url("   ") == ""
    # 无主机名的相对串不猜测、原样返回。
    assert browsable_cdp_url("/json/version") == "/json/version"