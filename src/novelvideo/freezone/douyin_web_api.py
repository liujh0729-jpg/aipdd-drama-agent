"""[aipdd fork delta] 抖音 Web 详情接口的纯 HTTP 通道（文案提取主链路）。

为什么要有这条通道
------------------
原实现只有一条路：经 CDP 连宿主机的真实 Chrome，导航视频页、拦 ``aweme/detail``
XHR。它能过抖音的 Argus 签名，但代价是**每次提取都必须开着 Chrome 调试窗口**——
窗口一关端口即消失，是本轮报错的主要来源。

2026-10-05 在 api 容器内用 ``.tmp_diag/douyin_http_probe.py`` 做了三档凭据实测：

- 匿名（无 Cookie、无签名）：``http=200 len=0`` —— 平台静默拒绝，不给任何错误码；
- 自建匿名 Cookie（访问首页领 ttwid）：同样 ``len=0``，首页并不下发凭据；
- **真实 Chrome 的完整登录态 Cookie、不传 a_bogus / X-Bogus / msToken**：
  ``http=200 len=68671 detail=True``。

结论：抖音 Web 详情接口**对带登录态 Cookie 的请求不校验签名**。因此只要用户把
Cookie 交给我们一次，纯 HTTP 就能直接拿到详情，不再依赖浏览器窗口；CDP 降级为
兜底（Cookie 过期、被风控时仍然可用）。

Cookie 与 CDP 的区别（用户明确问过）：CDP 是「每次提取都得开着调试端口的 Chrome」，
Cookie 是「采集一次、长期复用」——同一份登录态，运维成本完全不同。

凭据口径
--------
Cookie 只存设置数据库（设置页 → 抖音 Cookie），与 MediaKit / 豆包 ASR 同一套单真源
做法：CE 下不回退环境变量，避免 env/DB 双真源；日志与回显一律脱敏，绝不落 Cookie 值。
"""

from __future__ import annotations

import json
import logging
import os
import re
from typing import Any, Awaitable, Callable

logger = logging.getLogger(__name__)

DOUYIN_DETAIL_ENDPOINT = "https://www.douyin.com/aweme/v1/web/aweme/detail/"

# 探针里实测通过的那一版 Web 参数：只保留「身份/环境声明」类字段，不含任何签名位
# （a_bogus / X-Bogus / msToken / verifyFp 全部不需要）。字段缺失或 UA 与 sec-ch-ua
# 不一致时，平台更容易把请求判成脚本并静默返回空体。
_WEB_STATIC_PARAMS: dict[str, str] = {
    "device_platform": "webapp",
    "aid": "6383",
    "channel": "channel_pc_web",
    "version_code": "190500",
    "version_name": "19.5.0",
    "pc_client_type": "1",
    "pc_libra_divert": "Mac",
    "update_version_code": "170400",
    "support_h265": "1",
    "support_dash": "1",
    "cpu_core_num": "8",
    "device_memory": "8",
    "platform": "PC",
    "downlink": "10",
    "effective_type": "4g",
    "round_trip_time": "50",
    "cookie_enabled": "true",
    "screen_width": "1440",
    "screen_height": "900",
    "browser_language": "zh-CN",
    "browser_platform": "MacIntel",
    "browser_name": "Chrome",
    "browser_version": "154.0.0.0",
    "browser_online": "true",
    "engine_name": "Blink",
    "os_name": "Mac OS",
}

# UA 与 sec-ch-ua 浏览器版本必须互相自洽（探针实测通过的那一组）。
DOUYIN_WEB_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/154.0.0.0 Safari/537.36"
)
_SECH_UA = '"Chromium";v="154", "Google Chrome";v="154", "Not?A_Brand";v="99"'

# 详情接口是同源 XHR，Referer 用视频页（实测有效）。
_REFERER_TEMPLATE = "https://www.douyin.com/video/{video_id}"

_REQUEST_TIMEOUT_SECONDS = 20.0
# 空响应体是平台「静默拒绝」的标志性形态（匿名请求就是 len=0），给一个下限用于判读。
_MIN_VALID_BODY_LENGTH = 32

# 设置数据库里的 Cookie 键名（单真源；非 CE 才允许环境变量兜底）。
COOKIE_SETTINGS_KEY = "douyin_web_cookie"
COOKIE_ENV_KEY = "DOUYIN_WEB_COOKIE"

__all__ = [
    "DOUYIN_DETAIL_ENDPOINT",
    "DOUYIN_WEB_UA",
    "DouyinHttpRejected",
    "DouyinHttpUnavailable",
    "build_web_headers",
    "build_web_params",
    "douyin_web_cookie",
    "fetch_douyin_detail_via_http",
    "normalize_cookie",
]


class DouyinHttpUnavailable(RuntimeError):
    """本通道用不了（未配置 Cookie 之类），应当直接走 CDP 兜底，不算平台拒绝。"""


class DouyinHttpRejected(RuntimeError):
    """请求发出去了但被平台拒/判空（Cookie 失效、风控、视频不存在），走 CDP 兜底。"""


def normalize_cookie(raw: str) -> str:
    """把用户粘贴的凭据归一成 ``Cookie`` 请求头格式（``k=v; k2=v2``）。

    接受三种常见来源，避免用户为了格式反复试：

    1. 直接从 DevTools 请求头复制的整串 ``Cookie: k=v; k2=v2``（允许带前缀）；
    2. EditThisCookie / Cookie-Editor 导出的 **JSON 数组**（``[{"name":..,"value":..}]``）；
    3. ``{name: value}`` 形式的 JSON 对象。

    只保留有名字且有值的项；解析不出来时按纯文本清洗（去换行/多余分号）。
    """

    text = str(raw or "").strip()
    if not text:
        return ""
    # 复制请求头时常常连着「Cookie:」前缀一起带过来。
    if text.lower().startswith("cookie:"):
        text = text[len("cookie:") :].strip()

    pairs: dict[str, str] = {}
    if text.startswith("[") or text.startswith("{"):
        try:
            payload = json.loads(text)
        except Exception:  # noqa: BLE001 - 不是合法 JSON 就退回文本口径解析
            payload = None
        if isinstance(payload, list):
            for item in payload:
                if isinstance(item, dict):
                    name = str(item.get("name") or "").strip()
                    value = str(item.get("value") or "").strip()
                    if name and value:
                        pairs[name] = value
        elif isinstance(payload, dict):
            # 兼容 {"cookie": "..."} 这种整包导出。
            inner = payload.get("cookie") or payload.get("Cookie")
            if isinstance(inner, str) and inner.strip():
                text = inner.strip()
            else:
                for name, value in payload.items():
                    name_s = str(name or "").strip()
                    value_s = str(value or "").strip()
                    if name_s and value_s:
                        pairs[name_s] = value_s

    if not pairs:
        for chunk in re.split(r"[;\n\r]+", text):
            chunk = chunk.strip()
            if not chunk or "=" not in chunk:
                continue
            name, _, value = chunk.partition("=")
            name = name.strip()
            value = value.strip()
            if name and value:
                pairs[name] = value

    return "; ".join(f"{name}={value}" for name, value in pairs.items())


def douyin_web_cookie() -> str:
    """读取抖音 Web 凭据：CE 只认设置数据库，非 CE（无设置库）回退环境变量。

    未配置返回空串——由调用方决定是提示用户配置还是直接走 CDP 兜底（不把「没配
    置」当成错误抛出，避免污染兜底链路）。
    """

    from novelvideo.model_gateway_settings import _uses_ce_gateway_settings

    if _uses_ce_gateway_settings():
        from novelvideo.model_gateway_settings import get_model_gateway_settings

        try:
            settings = get_model_gateway_settings()
        except Exception as exc:  # noqa: BLE001 - 设置库读不到时按未配置处理
            logger.warning("douyin cookie settings unreadable: %s", exc)
            return ""
        return normalize_cookie(str(settings.get(COOKIE_SETTINGS_KEY, "")))
    return normalize_cookie(os.environ.get(COOKIE_ENV_KEY, ""))


def build_web_params(video_id: str) -> dict[str, str]:
    """详情查询参数：固定 Web 环境声明 + 本次的 ``aweme_id``。"""

    return {**_WEB_STATIC_PARAMS, "aweme_id": str(video_id)}


def build_web_headers(video_id: str, cookie: str) -> dict[str, str]:
    """详情请求头：浏览器同源 XHR 口径 + 登录态 Cookie。"""

    return {
        "User-Agent": DOUYIN_WEB_UA,
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "zh-CN,zh;q=0.9",
        "Referer": _REFERER_TEMPLATE.format(video_id=video_id),
        "sec-ch-ua": _SECH_UA,
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-platform": '"macOS"',
        "sec-fetch-dest": "empty",
        "sec-fetch-mode": "cors",
        "sec-fetch-site": "same-origin",
        "Cookie": cookie,
    }


def _describe_rejection(payload: dict[str, Any]) -> str:
    """把详情响应里的业务错误码翻成人话（extra 里常带具体原因）。"""

    status_code = payload.get("status_code")
    filter_reason = payload.get("filter_detail") or payload.get("status_msg")
    extra = payload.get("extra") if isinstance(payload.get("extra"), dict) else {}
    bits = [f"status_code={status_code}"]
    for key in ("reason", "description", "now"):
        value = extra.get(key)
        if value:
            bits.append(f"{key}={str(value)[:120]}")
    if filter_reason:
        bits.append(f"detail={str(filter_reason)[:120]}")
    return " ".join(bits)


def _parse_body(response: Any) -> dict[str, Any]:
    """把响应体解成 JSON；平台返 HTML 验证页 / 空体时归为「被拒」。"""

    text = str(getattr(response, "text", "") or "")
    if len(text) < _MIN_VALID_BODY_LENGTH:
        raise DouyinHttpRejected(
            f"抖音详情接口返回空响应（HTTP {getattr(response, 'status_code', 'N/A')}、"
            f"{len(text)} 字节）——这是平台对登录态失效/风控的典型拒绝形态"
        )
    try:
        payload = json.loads(text)
    except Exception:
        head = text[:120].replace("\n", " ")
        raise DouyinHttpRejected(
            f"抖音详情接口没有返回 JSON（疑似验证码/风控页）：{head!r}"
        ) from None
    if not isinstance(payload, dict):
        raise DouyinHttpRejected("抖音详情接口返回结构异常（不是对象）")
    return payload


async def fetch_douyin_detail_via_http(
    video_id: str,
    *,
    cookie: str | None = None,
    timeout: float = _REQUEST_TIMEOUT_SECONDS,
    client_factory: Callable[[], Any] | None = None,
) -> dict[str, Any]:
    """纯 HTTP 拉抖音详情，返回 ``{"aweme_detail": {...}}``（与 CDP 通道同口径）。

    ``client_factory`` 是可注入接缝（默认用 ``httpx.AsyncClient``），便于无网络单测；
    与 :mod:`novelvideo.freezone.chrome_cdp` 的 ``ws_connect`` / ``version_getter``
    接缝设计一致。

    失败分类：未配置凭据抛 ``DouyinHttpUnavailable``，被平台拒抛
    ``DouyinHttpRejected``——两者调用方都应当回落 CDP，但报错文案不能混为一谈
    （一个引导去配置 Cookie，一个引导更新 Cookie / 查风控）。
    """

    resolved_cookie = normalize_cookie(cookie) if cookie is not None else douyin_web_cookie()
    if not resolved_cookie:
        raise DouyinHttpUnavailable(
            "未配置抖音 Cookie：请到设置 → 「抖音 Cookie」粘贴已登录 Chrome 的抖音请求 "
            "Cookie（一次采集长期复用，之后无需再开浏览器窗口）"
        )

    video_id = str(video_id or "").strip()
    if not video_id.isdigit():
        raise DouyinHttpUnavailable(f"抖音视频 ID 不是纯数字，无法发起详情请求：{video_id!r}")

    params = build_web_params(video_id)
    headers = build_web_headers(video_id, resolved_cookie)

    if client_factory is None:
        import httpx

        def _default_factory() -> Any:
            return httpx.AsyncClient(timeout=timeout, follow_redirects=True)

        client_factory = _default_factory

    try:
        async with client_factory() as client:
            get: Awaitable[Any] = client.get(
                DOUYIN_DETAIL_ENDPOINT, params=params, headers=headers
            )
            response = await get
    except DouyinHttpRejected:
        raise
    except Exception as exc:  # noqa: BLE001 - 网络层异常统一归为「本通道不可用」
        raise DouyinHttpUnavailable(f"抖音详情 HTTP 请求失败: {type(exc).__name__}: {exc}") from exc

    status = int(getattr(response, "status_code", 0) or 0)
    if status >= 400:
        raise DouyinHttpRejected(
            f"抖音详情接口 HTTP {status}（Cookie 可能已失效或触发风控）"
        )

    payload = _parse_body(response)
    detail = payload.get("aweme_detail")
    if not isinstance(detail, dict) or not detail:
        raise DouyinHttpRejected(f"抖音详情为空：{_describe_rejection(payload)}")

    returned_id = str(detail.get("aweme_id") or "")
    if returned_id and returned_id != video_id:
        # 理论上不该发生（按 ID 精确查询），真发生说明被降级成了推荐流，不能当结果用。
        raise DouyinHttpRejected(
            f"详情返回的 aweme_id 与请求不一致（要 {video_id}、得 {returned_id}）"
        )

    logger.info("douyin http detail ok id=%s bytes=%d", video_id, len(str(getattr(response, "text", ""))))
    return payload
