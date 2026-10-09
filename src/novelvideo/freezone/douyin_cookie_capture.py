# SPDX-License-Identifier: Elastic-2.0
# Copyright (c) 2026 ClaymoreLab
"""[aipdd fork delta req45] 把宿主 Chrome 里的抖音登录态记进设置数据库。

需求背景（用户口径）：抖音 Cookie 本该「首次处理以后自动记录」，但 2.0.88 落地时只做了
「设置页手动粘贴一次」—— CDP 兜底链路复用真实 Chrome 的登录态，却从不把凭据读出来，
于是提取成功 N 次之后，设置里的抖音 Cookie 依然是空的。本模块补上那条通路：

- **抓取源只有 CDP**：抖音的 ``sessionid`` 是 httpOnly，页面 JS 与拦到的请求头都不完整，
  只有宿主 Chrome 的 cookie jar 是全量（见 :mod:`novelvideo.freezone.chrome_cdp`）；
- **域名先裁再存**：CDP 的 jar 是整个浏览器的（邮箱、购物、内部系统都在里面），只留
  抖音系；绝不把无关站点的凭据写进设置库；
- **没有登录态就不写**：只有 ``ttwid`` 这类匿名标识时等于没登录（实测匿名请求被平台静默
  拒回空体），写进去反而让 HTTP 主链路从「跳过」变成「发出去被拒」，报错更难读；
- **永不回显原文**：结果只带条数 / Cookie 名 / 成因文案，凭据本身只进设置库。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from novelvideo.freezone.chrome_cdp import filter_douyin_cookies

logger = logging.getLogger(__name__)

# 登录态判据：命中任意一个才算「这个 Chrome 真的登录过抖音」。
# ``ttwid`` 故意不在里面——它是匿名设备标识，未登录也有一份（实测：只有 ttwid 的请求
# 被平台静默拒回空响应体），把它当登录态会把「没登录」写成「已配置」，从此不再走 CDP。
_LOGIN_STATE_KEYS = (
    "sessionid",
    "sessionid_ss",
    "sid_tt",
    "sid_guard",
    "uid_tt",
    "uid_tt_ss",
    "passport_assist_user",
)

__all__ = [
    "CookieCaptureResult",
    "capture_douyin_cookie",
    "has_douyin_login_state",
]


@dataclass(frozen=True)
class CookieCaptureResult:
    """一次抓取的结果（**脱敏**：只有条数、Cookie 名与给人看的文案，没有凭据值）。"""

    saved: bool
    cookie_count: int = 0
    cookie_names: tuple[str, ...] = ()
    reason: str = ""


def has_douyin_login_state(pairs: dict[str, str]) -> bool:
    """裁出来的抖音 Cookie 里是否含真正的登录态字段。"""

    return any(key in pairs for key in _LOGIN_STATE_KEYS)


def _build_header(pairs: dict[str, str]) -> str:
    return "; ".join(f"{name}={value}" for name, value in pairs.items())


async def capture_douyin_cookie(
    *,
    cdp_url: str,
    save: bool = True,
    jar_loader: Callable[[str], Awaitable[list[dict[str, Any]]]] | None = None,
) -> CookieCaptureResult:
    """从宿主 Chrome 抓抖音 Cookie，归一后写进设置数据库。

    ``jar_loader`` 是注入接缝（默认 :func:`novelvideo.freezone.chrome_cdp.fetch_douyin_cookie_jar`），
    让单测零浏览器零网络。这里**不把异常抛给调用方**：自动记录只是「顺手把凭据留下来」，
    任何一步失败都只该变成一条可操作的理由，不该让一次已经成功的提取翻车；设置页的显式
    抓取按钮则按 :attr:`CookieCaptureResult.saved` 与 ``reason`` 直接告诉用户该做什么。
    """

    if jar_loader is None:
        from novelvideo.freezone.chrome_cdp import fetch_douyin_cookie_jar

        jar_loader = fetch_douyin_cookie_jar

    try:
        jar = await jar_loader(cdp_url)
    except Exception as exc:  # noqa: BLE001 - 抓取失败一律转成理由，不中断调用方
        return CookieCaptureResult(
            saved=False,
            reason=(
                f"读不到宿主 Chrome 的 Cookie：{exc}。请确认已按「环境安装」页说明以调试端口"
                "启动那个 Chrome（与「Chrome MCP」同一浏览器），并确认抖音已在里面登录过。"
            ),
        )

    pairs = filter_douyin_cookies(jar)
    if not pairs:
        return CookieCaptureResult(
            saved=False,
            reason=(
                "宿主 Chrome 里没有抖音域名的 Cookie——那个 Chrome 还没打开过抖音，"
                "或在抖音上还没登录。请在它里面登录抖音后重试。"
            ),
        )
    if not has_douyin_login_state(pairs):
        # 只有匿名标识（ttwid / csrf 之类）：写进库等于把「没登录」伪装成「已配置」，
        # HTTP 主链路会从此发出去被静默拒回空体，报错比现在更难读。
        return CookieCaptureResult(
            saved=False,
            cookie_count=len(pairs),
            cookie_names=tuple(sorted(pairs)),
            reason=(
                "宿主 Chrome 里有抖音的 Cookie，但没有登录态字段（sessionid / sid_tt 等）——"
                "只有匿名标识时请求会被抖音静默拒回空响应体（实测），记下来反而更糟。"
                "请在弹出的那个 Chrome 里登录抖音后重试。"
            ),
        )

    if not save:
        return CookieCaptureResult(
            saved=False,
            cookie_count=len(pairs),
            cookie_names=tuple(sorted(pairs)),
            reason="已抓到抖音登录态，但本次要求不落库（dry-run）。",
        )

    from novelvideo.model_gateway_settings import save_douyin_web_cookie

    try:
        save_douyin_web_cookie(cookie=_build_header(pairs))
    except Exception as exc:  # noqa: BLE001 - 设置库写不进时提取链路照跑
        logger.warning("douyin cookie capture could not persist: %s", exc)
        return CookieCaptureResult(
            saved=False,
            cookie_count=len(pairs),
            cookie_names=tuple(sorted(pairs)),
            reason=f"抓到了抖音登录态，但写进设置库失败：{exc}",
        )

    return CookieCaptureResult(
        saved=True,
        cookie_count=len(pairs),
        cookie_names=tuple(sorted(pairs)),
        reason="",
    )
