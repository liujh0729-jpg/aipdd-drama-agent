"""[aipdd fork delta] 出网 URL 的 SSRF 守卫：只放行可路由的公网 http(s) 目标。

认证用户可控的外链抓取（如 freezone 白模视频入库）会把抓回字节落成本项目
``/static`` 可下载资产——等于把内网探测的响应回读出来，是典型的 SSRF +
数据外泄面。这里提供一个复用的解析式校验：拒非 http(s) 协议、拒私网/回环/
链路本地/保留/多播/未指定地址（含十进制、八进制等会经解析归到内网的写法），
堵住对 ``169.254.169.254``、``localhost``、RFC1918 等内部端点的直连。

局限（登记为观察项）：校验在请求前解析一次，httpx 随后仍会独立再解析，
理论上的 DNS rebinding（两次解析结果不同）不在本守卫覆盖内；调用方应同时
关闭重定向，避免用一次合法外链 302 到内网绕过。
"""

from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlparse

_ALLOWED_SCHEMES = frozenset({"http", "https"})


class PublicUrlRejected(ValueError):
    """Raised when a fetch target is not an allowed public http(s) URL."""


def _ip_is_public(addr: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    return not (
        addr.is_private
        or addr.is_loopback
        or addr.is_link_local
        or addr.is_reserved
        or addr.is_multicast
        or addr.is_unspecified
    )


def _check_address(raw: str) -> None:
    try:
        addr = ipaddress.ip_address(raw)
    except ValueError:
        return
    # IPv4-mapped IPv6 (::ffff:127.0.0.1) 仍指向内网，取 mapped 地址再判一次。
    if isinstance(addr, ipaddress.IPv6Address):
        mapped = addr.ipv4_mapped
        if mapped is not None:
            addr = mapped
    if not _ip_is_public(addr):
        raise PublicUrlRejected("target resolves to a non-public address")


def ensure_public_http_url(url: str) -> str:
    """Return *url* unchanged when it targets a public http(s) host.

    Raises :class:`PublicUrlRejected` for non-http(s) schemes, missing hosts,
    or any host / resolved address in a private, loopback, link-local,
    reserved, multicast, or unspecified range. Fail-closed on resolution error.
    """

    cleaned = str(url or "").strip()
    parsed = urlparse(cleaned)
    if parsed.scheme not in _ALLOWED_SCHEMES:
        raise PublicUrlRejected("scheme must be http or https")
    host = parsed.hostname
    if not host:
        raise PublicUrlRejected("host is required")
    try:
        port = parsed.port
    except ValueError:
        raise PublicUrlRejected("invalid port") from None
    if port == 0:
        raise PublicUrlRejected("invalid port")

    # IP 字面量直接判定；主机名解析出全部地址，任一非公网即拒（fail-closed）。
    try:
        ipaddress.ip_address(host)
    except ValueError:
        is_literal = False
    else:
        is_literal = True

    if is_literal:
        _check_address(host)
        return cleaned

    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except (OSError, socket.gaierror, UnicodeError) as exc:
        raise PublicUrlRejected("host cannot be resolved") from exc
    if not infos:
        raise PublicUrlRejected("host cannot be resolved")
    for info in infos:
        sock_addr = info[4]
        if not sock_addr:
            continue
        _check_address(str(sock_addr[0]))
    return cleaned
