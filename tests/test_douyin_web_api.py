# SPDX-License-Identifier: Elastic-2.0
# Copyright (c) 2026 ClaymoreLab
"""[aipdd fork delta] 抖音 Web 纯 HTTP 主链路单测（无浏览器、无网络）。

覆盖三件事：
1. ``normalize_cookie`` 的三种来源格式（DevTools 整串 / EditThisCookie JSON 数组 /
   对象）都收敛成 ``k=v; k2=v2`` 请求头口径——用户为格式反复试是最常见的失败原因；
2. ``douyin_web_cookie`` 的真源优先级：CE 只认设置数据库（STATE_DIR 隔离到 tmp，
   绝不碰部署库），非 CE 才允许 ``DOUYIN_WEB_COOKIE`` 环境变量兜底；
3. ``fetch_douyin_detail_via_http`` 的失败分类：未配置/坏 ID/网络层异常 =
   ``DouyinHttpUnavailable``（引导去配 Cookie），空响应/非 JSON/HTTP≥400/详情为空/
   ID 不一致 = ``DouyinHttpRejected``（引导更新 Cookie 或查风控）。两类都必须让
   调用方回落 CDP，但文案不能混。

网络通过 ``client_factory`` 接缝完全桩掉：不发一个真实请求、不起浏览器。
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from novelvideo import config
from novelvideo.freezone.douyin_web_api import (
    DOUYIN_DETAIL_ENDPOINT,
    DouyinHttpRejected,
    DouyinHttpUnavailable,
    douyin_web_cookie,
    fetch_douyin_detail_via_http,
    normalize_cookie,
)

VIDEO_ID = "7412345678901234567"


# --------------------------------------------------------------------------- #
# Cookie 归一
# --------------------------------------------------------------------------- #
def test_normalize_cookie_accepts_devtools_header_string():
    raw = (
        "Cookie: sessionid=abc123; ttwid=zzz%3D; msToken=xy; "
        "\n  douyin.com=1; novc=; BADLINE\n"
    )
    assert normalize_cookie(raw) == "sessionid=abc123; ttwid=zzz%3D; msToken=xy; douyin.com=1"


def test_normalize_cookie_accepts_editthiscookie_json_export():
    raw = json.dumps(
        [
            {"name": "sessionid", "value": "abc"},
            {"name": "ttwid", "value": "def"},
            {"name": "", "value": "skip"},
            {"name": "no_value", "value": ""},
        ]
    )
    assert normalize_cookie(raw) == "sessionid=abc; ttwid=def"


def test_normalize_cookie_accepts_object_and_cookie_bundle():
    assert normalize_cookie('{"sessionid": "abc", "ttwid": "def"}') == "sessionid=abc; ttwid=def"
    # 整包导出（{"cookie": "…"}）走文本口径继续拆。
    assert normalize_cookie('{"cookie": "sessionid=abc; ttwid=def"}') == "sessionid=abc; ttwid=def"


def test_normalize_cookie_empty_and_garbage_are_blank():
    assert normalize_cookie("") == ""
    assert normalize_cookie(None) == ""  # type: ignore[arg-type]
    assert normalize_cookie("完全没有等号的一串文本") == ""


# --------------------------------------------------------------------------- #
# 真源优先级
# --------------------------------------------------------------------------- #
def _isolate_settings_db(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    """把设置数据库指向 tmp，避免读写到真实部署库（本仓库踩过的坑）。"""
    monkeypatch.setattr(config, "STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("ST_EDITION", "ce")
    monkeypatch.delenv("DOUYIN_WEB_COOKIE", raising=False)


def test_cookie_read_from_settings_db_in_ce(monkeypatch, tmp_path):
    from novelvideo.model_gateway_settings import save_douyin_web_cookie

    _isolate_settings_db(monkeypatch, tmp_path)
    save_douyin_web_cookie(cookie="Cookie: sessionid=dbvalue; ttwid=2")
    monkeypatch.setenv("DOUYIN_WEB_COOKIE", "sessionid=env-should-lose")

    assert douyin_web_cookie() == "sessionid=dbvalue; ttwid=2"


def test_cookie_unconfigured_in_ce_returns_blank_not_env(monkeypatch, tmp_path):
    _isolate_settings_db(monkeypatch, tmp_path)
    # CE 下环境变量不再是真源：库里没东西就是「未配置」，让调用方去回落 CDP。
    monkeypatch.setenv("DOUYIN_WEB_COOKIE", "sessionid=env-should-not-win")
    assert douyin_web_cookie() == ""


def test_cookie_falls_back_to_env_outside_ce(monkeypatch):
    # 非 CE（无设置库归属）才允许环境变量兜底。
    monkeypatch.setattr(
        "novelvideo.model_gateway_settings._uses_ce_gateway_settings",
        lambda: False,
    )
    monkeypatch.setenv("DOUYIN_WEB_COOKIE", "ttwid=envonly")
    assert douyin_web_cookie() == "ttwid=envonly"


def test_settings_db_read_failure_is_treated_as_unconfigured(monkeypatch):
    monkeypatch.setattr(
        "novelvideo.model_gateway_settings._uses_ce_gateway_settings",
        lambda: True,
    )

    def _boom() -> dict[str, Any]:
        raise RuntimeError("db locked")

    monkeypatch.setattr(
        "novelvideo.model_gateway_settings.get_model_gateway_settings",
        _boom,
    )
    # 读库异常不能把整条提取链路打断：按未配置处理，交由 CDP 兜底。
    assert douyin_web_cookie() == ""


# --------------------------------------------------------------------------- #
# 详情请求：成功与失败分类
# --------------------------------------------------------------------------- #
class _FakeResponse:
    def __init__(self, text: str, status_code: int = 200) -> None:
        self.text = text
        self.status_code = status_code


class _FakeClient:
    """替身 httpx.AsyncClient：只记录请求、返回预设响应或抛预设异常。"""

    def __init__(
        self,
        response: _FakeResponse | None = None,
        error: Exception | None = None,
    ) -> None:
        self.response = response
        self.error = error
        self.calls: list[dict[str, Any]] = []

    async def __aenter__(self) -> "_FakeClient":
        return self

    async def __aexit__(self, *_exc: Any) -> None:
        return None

    async def get(self, url: str, *, params: Any = None, headers: Any = None) -> _FakeResponse:
        self.calls.append({"url": url, "params": params, "headers": headers})
        if self.error is not None:
            raise self.error
        assert self.response is not None
        return self.response


def _factory(client: _FakeClient):
    return lambda: client


def _detail_payload(**overrides: Any) -> str:
    body: dict[str, Any] = {
        "status_code": 0,
        "aweme_detail": {
            "aweme_id": VIDEO_ID,
            "desc": "原文案标题",
            "statistics": {"digg_count": 1},
        },
    }
    body.update(overrides)
    return json.dumps(body, ensure_ascii=False)


async def test_success_returns_aweme_detail_and_sends_browser_shape():
    client = _FakeClient(response=_FakeResponse(_detail_payload()))

    payload = await fetch_douyin_detail_via_http(
        VIDEO_ID,
        cookie="Cookie: sessionid=abc; ttwid=2",
        client_factory=_factory(client),
    )

    assert payload["aweme_detail"]["desc"] == "原文案标题"
    call = client.calls[0]
    assert call["url"] == DOUYIN_DETAIL_ENDPOINT
    # Cookie 以归一后的整串进请求头；aweme_id 走查询参数。
    assert call["headers"]["Cookie"] == "sessionid=abc; ttwid=2"
    assert call["params"]["aweme_id"] == VIDEO_ID
    assert call["headers"]["Referer"] == f"https://www.douyin.com/video/{VIDEO_ID}"
    assert call["headers"]["User-Agent"].startswith("Mozilla/5.0")


async def test_missing_cookie_is_unavailable_not_rejected():
    with pytest.raises(DouyinHttpUnavailable) as exc:
        await fetch_douyin_detail_via_http(VIDEO_ID, cookie="")
    assert "未配置抖音 Cookie" in str(exc.value)


async def test_non_numeric_video_id_is_unavailable():
    with pytest.raises(DouyinHttpUnavailable) as exc:
        await fetch_douyin_detail_via_http(
            "not-a-number", cookie="sessionid=abc", client_factory=_factory(_FakeClient())
        )
    assert "纯数字" in str(exc.value)


async def test_network_error_is_unavailable():
    client = _FakeClient(error=OSError("connection reset"))

    with pytest.raises(DouyinHttpUnavailable) as exc:
        await fetch_douyin_detail_via_http(
            VIDEO_ID, cookie="sessionid=abc", client_factory=_factory(client)
        )
    assert "HTTP 请求失败" in str(exc.value)


async def test_http_error_status_is_rejected():
    client = _FakeClient(response=_FakeResponse(_detail_payload(), status_code=403))

    with pytest.raises(DouyinHttpRejected) as exc:
        await fetch_douyin_detail_via_http(
            VIDEO_ID, cookie="sessionid=abc", client_factory=_factory(client)
        )
    assert "HTTP 403" in str(exc.value)


async def test_empty_body_is_rejected_as_platform_silent_block():
    # 空响应体是平台对匿名/失效登录态的标志性拒绝形态。
    client = _FakeClient(response=_FakeResponse(""))

    with pytest.raises(DouyinHttpRejected) as exc:
        await fetch_douyin_detail_via_http(
            VIDEO_ID, cookie="sessionid=abc", client_factory=_factory(client)
        )
    assert "空响应" in str(exc.value)


async def test_html_challenge_page_is_rejected():
    client = _FakeClient(response=_FakeResponse("<html><body>验证码</body></html>" + "x" * 64))

    with pytest.raises(DouyinHttpRejected) as exc:
        await fetch_douyin_detail_via_http(
            VIDEO_ID, cookie="sessionid=abc", client_factory=_factory(client)
        )
    assert "没有返回 JSON" in str(exc.value)


async def test_empty_detail_reports_business_status_code():
    client = _FakeClient(
        response=_FakeResponse(
            json.dumps(
                {
                    "status_code": 10200,
                    "filter_detail": {"detail_msg": "作品已下架"},
                    "extra": {"reason": "资源不可用"},
                }
            )
        )
    )

    with pytest.raises(DouyinHttpRejected) as exc:
        await fetch_douyin_detail_via_http(
            VIDEO_ID, cookie="sessionid=abc", client_factory=_factory(client)
        )
    message = str(exc.value)
    assert "status_code=10200" in message
    assert "资源不可用" in message
    assert "作品已下架" in message


async def test_mismatched_aweme_id_is_rejected():
    body = json.dumps({"aweme_detail": {"aweme_id": "999", "desc": "推荐流"}})
    client = _FakeClient(response=_FakeResponse(body))

    with pytest.raises(DouyinHttpRejected) as exc:
        await fetch_douyin_detail_via_http(
            VIDEO_ID, cookie="sessionid=abc", client_factory=_factory(client)
        )
    assert "不一致" in str(exc.value)
