# SPDX-License-Identifier: Elastic-2.0
# Copyright (c) 2026 ClaymoreLab
"""[aipdd fork delta] 客户自带 Chrome 的 CDP 地址解析（Agent Chrome MCP 与抖音拦截共用）。

Chrome 的 DevTools HTTP 端点会校验 Host 头：只接受 IP 或 localhost——容器里经
``host.docker.internal`` 访问时 Host 头是主机名，Chrome 直接回 500
（``Host header is specified and is not an IP address or localhost``）。
所以统一把地址里的主机名解析成 IP 再交给驱动；localhost 与已是 IP 的地址
原样保留，解析失败（源码部署本机没有该主机名）也保留原值，让调用方的
错误处理接手，绝不因为换算把可用地址改坏。
"""

from __future__ import annotations

import ipaddress
import os
import socket
from urllib.parse import urlsplit, urlunsplit

DEFAULT_CDP_PORT = 9222


def in_container() -> bool:
    """当前进程是否跑在容器里（Docker / Podman）。"""

    return os.path.exists("/.dockerenv") or os.path.exists("/run/.containerenv")


def default_cdp_url() -> str:
    """Chrome 调试端口的默认地址：容器走宿主别名，本机（exe/dmg）走回环。

    桌面版把 Chrome 与后端跑在同一台机器上：``host.docker.internal`` 在本机
    既解析不到、也没有理由绕一圈；容器里则相反——回环指向容器自身而非宿主的
    Chrome，必须走宿主别名。
    """

    host = "host.docker.internal" if in_container() else "127.0.0.1"
    return f"http://{host}:{DEFAULT_CDP_PORT}"


def browsable_cdp_url(url: str) -> str:
    """把 CDP 地址中的主机名解析成 IP（Chrome 只接受 IP / localhost 的 Host 头）。"""

    cleaned = (url or "").strip()
    if not cleaned:
        return cleaned
    try:
        parsed = urlsplit(cleaned)
    except ValueError:
        return cleaned
    host = parsed.hostname or ""
    if not host or host == "localhost":
        return cleaned
    try:
        ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        return cleaned  # 已经是 IP，原样返回
    try:
        resolved = socket.gethostbyname(host)
    except OSError:
        return cleaned
    try:
        address = ipaddress.ip_address(resolved)
    except ValueError:
        return cleaned
    netloc = f"[{resolved}]" if address.version == 6 else resolved
    if parsed.port is not None:
        netloc = f"{netloc}:{parsed.port}"
    return urlunsplit((parsed.scheme or "http", netloc, parsed.path, parsed.query, parsed.fragment))
