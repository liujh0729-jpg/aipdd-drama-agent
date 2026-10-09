# SPDX-License-Identifier: Elastic-2.0
# Copyright (c) 2026 ClaymoreLab
"""[aipdd fork delta req45] 「首次处理后自动记录抖音 Cookie」那条通路的回归测试。

覆盖三层，都是这条需求最容易悄悄退化处：

1. :func:`chrome_cdp.is_douyin_cookie_domain` / :func:`chrome_cdp.filter_douyin_cookies`
   —— CDP 的 jar 是**整个浏览器**的，裁不严会把别的站点凭据写进设置库；同名 cookie
   挂在不同 domain 时取哪份、多次抓取字节序是否稳定；
2. :func:`douyin_cookie_capture.capture_douyin_cookie` 的四条「不保存」分支——每条都要
   给出可操作理由，且**绝不抛异常打断一次已经成功的提取**；尤其「只有 ttwid 等匿名标识
   就不写」这条，写进去等于把没登录伪装成已配置；
3. ``fetch_douyin_cookie_jar`` 的命令候选与失败分因（sessionid 是 httpOnly，只有 CDP
   的 cookie 域是全量，命令在部分 Chrome 版本上被改过语义）。
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

from novelvideo import config
from novelvideo.freezone import chrome_cdp
from novelvideo.freezone.douyin_cookie_capture import (
    CookieCaptureResult,
    capture_douyin_cookie,
    has_douyin_login_state,
)


def _isolate_settings_db(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    """Cookie 只进设置数据库：把库挪到 tmp_path，并锁 CE Edition 判定。"""
    monkeypatch.setattr(config, "STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("ST_EDITION", "ce")
    for key in ("ST_CONTROL_PLANE_DSN",):
        monkeypatch.delenv(key, raising=False)


# ============================================================
# 域名裁剪：宁可少收，不可把别人的凭据当自己的存
# ============================================================


@pytest.mark.parametrize(
    "domain",
    [
        "douyin.com",
        ".douyin.com",
        "www.douyin.com",
        ".www.douyin.com",
        "v.douyin.com",
        "iesdouyin.com",
        ".iesdouyin.com",
        "DOUYIN.COM",  # Chrome 回报的 domain 大小写不保证
    ],
)
def test_douyin_domains_are_accepted(domain: str) -> None:
    assert chrome_cdp.is_douyin_cookie_domain(domain) is True


@pytest.mark.parametrize(
    "domain",
    [
        # 同尾不同域：裸 endswith 会把它们收进来，那是别人的登录态。
        "evil-douyin.com",
        ".evil-douyin.com",
        "douyin.com.evil.net",
        "notdouyin.com",
        # 无关站点。
        "tiktok.com",
        "bilibili.com",
        ".bilibili.com",
        "",
        "   ",
    ],
)
def test_non_douyin_and_lookalike_domains_are_rejected(domain: str) -> None:
    assert chrome_cdp.is_douyin_cookie_domain(domain) is False


def test_filter_keeps_only_douyin_and_skips_malformed_entries() -> None:
    jar = [
        {"name": "sid_tt", "value": "DOUYIN", "domain": ".douyin.com"},
        {"name": "SessionId", "value": "MAIL", "domain": ".corp-mail.com"},
        {"name": "tok", "value": "SHOP", "domain": ".shop.example"},
        # 脏数据不能炸整个抓取流程：非 dict、缺名、缺值一律跳过。
        "not-a-dict",
        {"name": "", "value": "x", "domain": ".douyin.com"},
        {"name": "empty_value", "value": "", "domain": ".douyin.com"},
        {"no_name": 1},
    ]
    assert chrome_cdp.filter_douyin_cookies(jar) == {"sid_tt": "DOUYIN"}


def test_filter_prefers_more_specific_domain_for_same_name() -> None:
    # 浏览器发请求时更具体的那份 domain 生效，抓取必须以它为准（否则可能存下已失效的
    # 注册域旧值）。
    jar = [
        {"name": "sessionid", "value": "registry-domain", "domain": ".douyin.com"},
        {"name": "sessionid", "value": "host-specific", "domain": "www.douyin.com"},
    ]
    assert chrome_cdp.filter_douyin_cookies(jar) == {"sessionid": "host-specific"}


def test_filter_output_order_is_stable_regardless_of_jar_order() -> None:
    entries = [
        {"name": "ttwid", "value": "1", "domain": ".douyin.com"},
        {"name": "sessionid", "value": "s", "domain": ".douyin.com"},
        {"name": "ui_template", "value": "u", "domain": ".douyin.com"},
    ]
    forward = chrome_cdp.filter_douyin_cookies(entries)
    backward = chrome_cdp.filter_douyin_cookies(list(reversed(entries)))
    assert list(forward) == list(backward) == ["sessionid", "ttwid", "ui_template"]


def test_filter_tolerates_non_list_payload() -> None:
    # Chrome 版本差异 / 命令返回异形时不能抛，交给上层转成一条理由。
    assert chrome_cdp.filter_douyin_cookies(None) == {}
    assert chrome_cdp.filter_douyin_cookies({"cookies": []}) == {}


# ============================================================
# 登录态判据：只有匿名标识 == 没登录
# ============================================================


def test_anonymous_only_cookie_is_not_login_state() -> None:
    # ttwid 未登录也有一份；实测匿名请求被平台静默拒回空体，拿它当登录态会把
    # 「没登录」写成「已配置」，从此 HTTP 主链路发出去被拒而不再回落 CDP。
    assert has_douyin_login_state({"ttwid": "1", "msToken": "x"}) is False
    assert has_douyin_login_state({}) is False


@pytest.mark.parametrize(
    "key",
    [
        "sessionid",
        "sessionid_ss",
        "sid_tt",
        "sid_guard",
        "uid_tt",
        "uid_tt_ss",
        "passport_assist_user",
    ],
)
def test_each_login_state_field_counts(key: str) -> None:
    assert has_douyin_login_state({key: "v", "ttwid": "1"}) is True


# ============================================================
# capture_douyin_cookie：成功落库 + 四条不保存分支都不抛
# ============================================================


def _jar(*items: dict[str, Any]) -> list[dict[str, Any]]:
    return list(items)


def _loader(jar: list[dict[str, Any]]):
    async def _load(_cdp_url: str) -> list[dict[str, Any]]:
        return jar

    return _load


def _failing_loader(error: Exception):
    async def _load(_cdp_url: str) -> list[dict[str, Any]]:
        raise error

    return _load


async def test_capture_saves_login_state_and_persists_header_format(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    from novelvideo.model_gateway_settings import get_douyin_web_cookie_setting

    _isolate_settings_db(monkeypatch, tmp_path)
    jar = _jar(
        {"name": "ttwid", "value": "TTWID-1", "domain": ".douyin.com"},
        {"name": "sessionid", "value": "SESSION-1", "domain": ".douyin.com"},
        {"name": "sid_tt", "value": "SIDTT-1", "domain": ".douyin.com"},
        {"name": "ck", "value": "MAILBOX", "domain": ".corp-mail.com"},  # 必须被裁掉
    )

    result = await capture_douyin_cookie(cdp_url="http://127.0.0.1:9222", jar_loader=_loader(jar))

    assert result.saved is True
    assert result.reason == ""
    assert result.cookie_count == 3
    assert result.cookie_names == ("sessionid", "sid_tt", "ttwid")
    # 库里是归一后的请求头格式，按名排序，且不含无关站点那条。
    assert (
        get_douyin_web_cookie_setting()
        == "sessionid=SESSION-1; sid_tt=SIDTT-1; ttwid=TTWID-1"
    )


async def test_capture_result_never_carries_cookie_values() -> None:
    result = await capture_douyin_cookie(
        cdp_url="http://127.0.0.1:9222",
        save=False,
        jar_loader=_loader(
            _jar({"name": "sessionid", "value": "SECRET-DO-NOT-LEAK", "domain": ".douyin.com"})
        ),
    )
    assert "SECRET-DO-NOT-LEAK" not in str(result)


async def test_capture_dry_run_does_not_write_settings_db(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    from novelvideo.model_gateway_settings import get_douyin_web_cookie_setting

    _isolate_settings_db(monkeypatch, tmp_path)
    result = await capture_douyin_cookie(
        cdp_url="http://127.0.0.1:9222",
        save=False,
        jar_loader=_loader(
            _jar({"name": "sessionid", "value": "S", "domain": ".douyin.com"})
        ),
    )
    assert result.saved is False
    assert result.cookie_count == 1
    assert get_douyin_web_cookie_setting() == ""


async def test_capture_without_douyin_cookies_gives_actionable_reason(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    from novelvideo.model_gateway_settings import get_douyin_web_cookie_setting

    _isolate_settings_db(monkeypatch, tmp_path)
    result = await capture_douyin_cookie(
        cdp_url="http://127.0.0.1:9222",
        jar_loader=_loader(_jar({"name": "ck", "value": "V", "domain": ".example.com"})),
    )
    assert result.saved is False
    assert "抖音" in result.reason
    assert "登录" in result.reason
    assert get_douyin_web_cookie_setting() == ""


async def test_capture_refuses_anonymous_only_cookie(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    """jar 里有抖音 Cookie 但只有匿名标识：宁可不写，也不伪装成「已配置」。"""
    from novelvideo.model_gateway_settings import get_douyin_web_cookie_setting

    _isolate_settings_db(monkeypatch, tmp_path)
    result = await capture_douyin_cookie(
        cdp_url="http://127.0.0.1:9222",
        jar_loader=_loader(
            _jar(
                {"name": "ttwid", "value": "1", "domain": ".douyin.com"},
                {"name": "msToken", "value": "2", "domain": ".douyin.com"},
            )
        ),
    )
    assert result.saved is False
    assert result.cookie_count == 2
    assert "sessionid" in result.reason
    assert get_douyin_web_cookie_setting() == ""


async def test_capture_cdp_failure_becomes_reason_not_exception() -> None:
    """自动记录只是顺手：读不到 Chrome 只该变成一条理由，绝不让已成功的提取翻车。"""
    result = await capture_douyin_cookie(
        cdp_url="http://127.0.0.1:9222",
        jar_loader=_failing_loader(chrome_cdp.CdpUnavailable("connection refused")),
    )
    assert isinstance(result, CookieCaptureResult)
    assert result.saved is False
    assert "调试端口" in result.reason
    assert "环境安装" in result.reason


async def test_capture_persist_failure_is_swallowed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _boom(**_kwargs: Any) -> None:
        raise OSError("settings db is read-only")

    monkeypatch.setattr("novelvideo.model_gateway_settings.save_douyin_web_cookie", _boom)
    result = await capture_douyin_cookie(
        cdp_url="http://127.0.0.1:9222",
        jar_loader=_loader(
            _jar({"name": "sessionid", "value": "S", "domain": ".douyin.com"})
        ),
    )
    assert result.saved is False
    assert "写进设置库失败" in result.reason


# ============================================================
# fetch_douyin_cookie_jar：命令候选与失败分因
# ============================================================


class _JarSocket:
    """按方法脚本报回/报错的假 browser 级 WebSocket。"""

    def __init__(self, replies: dict[str, Any]) -> None:
        self._replies = replies
        self._out: asyncio.Queue[str] = asyncio.Queue()
        self.sent_methods: list[str] = []
        self.ctx_exits = 0

    async def send(self, text: str) -> None:
        msg = json.loads(text)
        mid, method = msg["id"], msg["method"]
        self.sent_methods.append(method)
        reply = self._replies.get(method, {"missing": method})
        if isinstance(reply, Exception):
            self._out.put_nowait(json.dumps({"id": mid, "error": {"message": str(reply)}}))
            return
        self._out.put_nowait(json.dumps({"id": mid, "result": reply}))

    def __aiter__(self):
        return self._read()

    async def _read(self):
        while True:
            yield await self._out.get()


async def _fake_version(_url: str) -> dict[str, str]:
    return {"webSocketDebuggerUrl": "ws://localhost:9222/devtools/browser/x"}


def _ctx(sock: _JarSocket):
    class _Ctx:
        async def __aenter__(self) -> _JarSocket:
            return sock

        async def __aexit__(self, *_exc: object) -> bool:
            sock.ctx_exits += 1
            return False

    return lambda _url: _Ctx()


async def test_jar_uses_network_get_all_cookies() -> None:
    cookies = [{"name": "sessionid", "value": "S", "domain": ".douyin.com"}]
    sock = _JarSocket({"Network.getAllCookies": {"cookies": cookies}})
    got = await chrome_cdp.fetch_douyin_cookie_jar(
        "http://127.0.0.1:9222", ws_connect=_ctx(sock), version_getter=_fake_version
    )
    assert got == cookies
    # 首选命令成功就不该再发第二个（Storage.getCookies 需要权限，能不动就不动）。
    assert sock.sent_methods == ["Network.getAllCookies"]
    # 客户端与 WebSocket 成对退出：只 aclose 客户端会每抓一次漏一条连接。
    assert sock.ctx_exits == 1


async def test_jar_falls_back_to_storage_get_cookies() -> None:
    cookies = [{"name": "sid_tt", "value": "T", "domain": ".iesdouyin.com"}]
    sock = _JarSocket(
        {
            "Network.getAllCookies": RuntimeError("not supported"),
            "Storage.getCookies": {"cookies": cookies},
        }
    )
    got = await chrome_cdp.fetch_douyin_cookie_jar(
        "http://127.0.0.1:9222", ws_connect=_ctx(sock), version_getter=_fake_version
    )
    assert got == cookies
    assert sock.sent_methods == ["Network.getAllCookies", "Storage.getCookies"]


async def test_jar_drops_non_dict_entries() -> None:
    sock = _JarSocket(
        {
            "Network.getAllCookies": {
                "cookies": [{"name": "n", "value": "v", "domain": ".douyin.com"}, "junk"]
            }
        }
    )
    got = await chrome_cdp.fetch_douyin_cookie_jar(
        "http://127.0.0.1:9222", ws_connect=_ctx(sock), version_getter=_fake_version
    )
    assert got == [{"name": "n", "value": "v", "domain": ".douyin.com"}]


async def test_jar_both_commands_failure_names_both_reasons() -> None:
    sock = _JarSocket(
        {
            "Network.getAllCookies": RuntimeError("method not available"),
            "Storage.getCookies": {"no_cookies_here": True},
        }
    )
    with pytest.raises(chrome_cdp.CdpUnavailable) as ei:
        await chrome_cdp.fetch_douyin_cookie_jar(
            "http://127.0.0.1:9222", ws_connect=_ctx(sock), version_getter=_fake_version
        )
    text = str(ei.value)
    # 两个候选的成因都要贴出去，否则用户与运维只能猜是哪条命令哑了。
    assert "Network.getAllCookies" in text
    assert "Storage.getCookies" in text
    # 失败路径同样要收尾（异常从 finally 里再抛出来才是真故障）。
    assert sock.ctx_exits == 1
