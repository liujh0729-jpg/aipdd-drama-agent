# SPDX-License-Identifier: Elastic-2.0
# Copyright (c) 2026 ClaymoreLab
"""[aipdd fork delta] 动效大片渲染：把一页 MG 脚本按时间轴逐帧截成 PNG。

契约写在图册清单的 `render_contract` 里（`window.render(t)` / `window.DUR` /
可选 `window.EV`，逐帧确定性、不许靠 requestAnimationFrame 的累积状态），本模块
就是那份契约的执行侧：连上 Chrome，开一个自己的标签页，把页面时间轴钉在我们要的
那一秒上，截一张，再钉下一秒。

为什么每帧两步（evaluate + screenshot）而不是注一条 JS 一次截完：CDP 的截图命令
必须由外部发起，页面里拿不到自己刚画的那一帧的字节。为了让「render(t) 画完」和
「截图取到」之间不出现时间偷跑，注入的引导脚本把 `requestAnimationFrame` /
`performance.now` / `Date.now` 全部钉死在 t 上 —— 页面内部即使偷偷用了 rAF 或读
了时钟，看到的时间也只可能是我们给的那一秒。

帧**不在内存里攒**：900 帧 1080p PNG 攒起来能到 GB 级，所以这里做成异步迭代器，
由编码侧一边截一边喂 ffmpeg（与 `explainer_motion` 的 rawvideo 管道同一口径）。
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import math
import re
from dataclasses import dataclass, field
from typing import AsyncIterator, Callable, Optional

from novelvideo.freezone.chrome_cdp import (
    CdpUnavailable,
    _connect_browser_client,
    _shutdown_browser_client,
)

logger = logging.getLogger(__name__)

# 页面加载 + 首次取 DUR/EV 的预算（秒）。自包含的单文件 HTML 不该更久。
_PAGE_READY_TIMEOUT_SECONDS = 30.0
_READY_POLL_SECONDS = 0.1
# 单帧 render(t) 的往返上限：页面里写死循环时靠它兜底，不能让任务永久挂住。
_FRAME_CALL_TIMEOUT_SECONDS = 30.0
# 读到的拍点数上限：EV 只用于对齐，再多也不影响出片，但能挡住失控的数组。
_MAX_EVENTS = 4096
# 引导脚本的插点：开标签允许带属性（<html lang="zh-CN">、<head prefix="og: ...">）。
_HEAD_TAG = re.compile(r"<head\b[^>]*>", re.IGNORECASE)
_HTML_TAG = re.compile(r"<html\b[^>]*>", re.IGNORECASE)

# 注入在页面脚本之前的时间引导：先接住原生实现，再把时钟与 rAF 锁在 __mg_t。
# rAF 回调留到 step() 里同帧同步跑完 —— 截图发生在回调之后，页面就无处可偷时间。
_BOOTSTRAP = """
(function () {
  var nativeRaf = window.requestAnimationFrame
    ? window.requestAnimationFrame.bind(window)
    : null;
  var nativeNow = window.performance && window.performance.now
    ? window.performance.now.bind(window.performance)
    : null;
  var nativeDateNow = Date.now;
  var t = 0;
  var pending = [];
  var counter = 1;
  window.__mg_t = 0;
  window.__mg_step = function (next) {
    t = next;
    window.__mg_t = next;
    pending.length = 0;
    if (typeof window.render === "function") {
      window.render(next);
    }
    var queue = pending.slice();
    for (var i = 0; i < queue.length; i += 1) {
      queue[i].cb(next * 1000);
    }
    pending.length = 0;
    return t;
  };
  window.requestAnimationFrame = function (cb) {
    var handle = counter;
    counter += 1;
    pending.push({ handle: handle, cb: cb });
    return handle;
  };
  window.cancelAnimationFrame = function (handle) {
    for (var i = 0; i < pending.length; i += 1) {
      if (pending[i].handle === handle) {
        pending.splice(i, 1);
        return;
      }
    }
  };
  if (window.performance) {
    window.performance.now = function () {
      return t * 1000;
    };
  }
  Date.now = function () {
    return (nativeNow ? nativeNow.call(window.performance) : 0) + t * 1000;
  };
  if (nativeDateNow) {
    window.__mg_epoch_start = nativeDateNow.call(Date);
  }
})();
"""

_READ_TIMING = """
(function () {
  var duration = Number(window.DUR);
  if (!isFinite(duration) || duration <= 0) {
    return JSON.stringify({ error: "window.DUR 缺失或不合法" });
  }
  if (typeof window.render !== "function") {
    return JSON.stringify({ error: "window.render 不是一个函数（逐帧驱动靠它）" });
  }
  var raw = Array.isArray(window.EV) ? window.EV : [];
  var events = [];
  for (var i = 0; i < raw.length && i < %d; i += 1) {
    var item = raw[i] || {};
    var at = Number(item.t);
    if (isFinite(at) && at >= 0) {
      events.push({ t: at, type: String(item.type || "") });
    }
  }
  return JSON.stringify({ duration: duration, events: events });
})()
""" % _MAX_EVENTS


class MotionRenderError(RuntimeError):
    """页面不遵守契约，或渲染中途 Chrome/页面出事。"""


@dataclass
class MotionTiming:
    """页面自报的时间轴：总时长与拍点。"""

    duration_seconds: float
    events: list[dict] = field(default_factory=list)


def with_bootstrap(html: str) -> str:
    """把时间引导插到页面自己的脚本前面。

    插点按带属性的开标签找：模型产出的页十有八九写 ``<html lang="zh-CN">``，只认
    ``"<html>"`` 字面量的话就一路落到「前置到整篇之前」，把 ``<!doctype html>`` 挤到
    script 后面——doctype 不在首行浏览器就进 quirks mode，量出来的版式与用户在浏览器
    里复现的那份不一样，等于每条片子都可能在赌。
    """
    script = f"<script>{_BOOTSTRAP}</script>"
    for pattern, wrapper in ((_HEAD_TAG, ""), (_HTML_TAG, f"<head>{script}</head>")):
        match = pattern.search(html)
        if match:
            inserted = wrapper or script
            return html[: match.end()] + inserted + html[match.end() :]
    return script + html


def parse_timing(payload: str) -> MotionTiming:
    """解析页面回报的 `MOTION_TIMING`；不合法时把页面自己给的原因抛出去。"""
    try:
        parsed = json.loads(payload)
    except (TypeError, ValueError) as exc:
        raise MotionRenderError(f"页面时间轴回报不是合法 JSON: {payload!r:.200}") from exc
    if not isinstance(parsed, dict):
        raise MotionRenderError("页面时间轴回报不是对象")
    if parsed.get("error"):
        raise MotionRenderError(str(parsed["error"]))
    duration = parsed.get("duration")
    if (
        not isinstance(duration, (int, float))
        or isinstance(duration, bool)
        or duration <= 0
        or not math.isfinite(float(duration))
    ):
        raise MotionRenderError("window.DUR 必须是正数秒")
    events = parsed.get("events")
    cleaned: list[dict] = []
    if isinstance(events, list):
        # 逐项校验而不是原样透传：拍点后面要拿去做剪辑对齐（P3），一条缺 t 的记录
        # 会在那边变成 None 参与算术，不如在这里就丢掉 —— 页面自己已经过滤过一轮，
        # 这里是防外部清单/手写页面绕过 JS。
        for item in events:
            if not isinstance(item, dict):
                continue
            at = item.get("t")
            if isinstance(at, bool) or not isinstance(at, (int, float)) or at < 0:
                continue
            if not math.isfinite(float(at)):
                continue
            cleaned.append({"t": float(at), "type": str(item.get("type") or "")})
    return MotionTiming(duration_seconds=float(duration), events=cleaned)


async def _evaluate(client, session_id: str, expression: str, *, timeout: float) -> str:
    """跑一段表达式并取回字符串值；页面抛错时转成 `MotionRenderError`。"""
    result = await client.call(
        "Runtime.evaluate",
        {"expression": expression, "returnByValue": True, "awaitPromise": True},
        session_id=session_id,
        timeout=timeout,
    )
    details = result.get("exceptionDetails")
    if isinstance(details, dict):
        text = str(details.get("text") or "")
        error = details.get("exception")
        if isinstance(error, dict):
            text = str(error.get("description") or error.get("value") or text)
        raise MotionRenderError(f"页面脚本执行出错：{text[:400]}")
    value = result.get("result")
    if isinstance(value, dict) and isinstance(value.get("value"), str):
        return str(value["value"])
    raise MotionRenderError("页面脚本没有回报字符串结果")


async def _await_page_ready(client, session_id: str, deadline_left: float, page_url: str) -> None:
    """等**这一页**加载出来，而不是等「readyState 是 complete」。

    ``Page.navigate`` 是发完就回的，此时文档还是 ``about:blank``——它的 readyState 本来就是
    ``complete``。只看 readyState 的话第一次轮询就能过，紧接着去读 ``window.DUR`` 读的是空
    文档，一页本来合规的动画会以「window.DUR 缺失」失败，且是概率性的（取决于导航多快落地）。
    所以两个条件都要满足：文档地址已经是目标页，且 readyState 落地。
    """
    loop = asyncio.get_running_loop()
    stop_at = loop.time() + max(1.0, deadline_left)
    while loop.time() < stop_at:
        state, url = await _ready_probe(client, session_id)
        if url == page_url and state in ("complete", "interactive"):
            return
        await asyncio.sleep(_READY_POLL_SECONDS)
    raise MotionRenderError(f"页面 {int(_PAGE_READY_TIMEOUT_SECONDS)} 秒内没有加载完成")


async def _ready_probe(client, session_id: str) -> tuple[str, str]:
    """一次 ``Runtime.evaluate`` 同时拿 readyState 与文档地址。"""
    text = await _evaluate(
        client,
        session_id,
        "String(document.readyState) + '\\n' + String(document.URL)",
        timeout=_FRAME_CALL_TIMEOUT_SECONDS,
    )
    state, _, url = text.partition("\n")
    return state.strip(), url.strip()


class MotionPage:
    """一个被我们钉住时间的 MG 页面：读得到时间轴，也能按帧序列交出截图。

    分成 ``open`` 与 ``iter_frames`` 两步是因为**帧数得先由页面自报的时长算**：
    一条 29 秒的片子在 30fps 下是 870 帧，调用方拿到时长才能决定编码多久。
    """

    def __init__(
        self,
        ws_ctx: object,
        client: object,
        session_id: str,
        timing: MotionTiming,
    ) -> None:
        self._ws_ctx = ws_ctx
        self._client = client
        self._session_id = session_id
        self._target_id: Optional[str] = None
        self.timing = timing

    @classmethod
    async def open(cls, cdp_url: str, page_url: str, *, width: int, height: int) -> "MotionPage":
        ws_ctx, client = await _connect_browser_client(cdp_url)
        try:
            target = await client.call("Target.createTarget", {"url": "about:blank"})
            target_id = str(target.get("targetId") or "")
            if not target_id:
                raise CdpUnavailable("Target.createTarget 未返回 targetId")
            attached = await client.call(
                "Target.attachToTarget", {"targetId": target_id, "flatten": True}
            )
            session_id = str(attached.get("sessionId") or "")
            if not session_id:
                raise CdpUnavailable("Target.attachToTarget 未返回 sessionId")

            page = cls(ws_ctx, client, session_id, MotionTiming(duration_seconds=0.0))
            page._target_id = target_id
            await client.call("Page.enable", {}, session_id=session_id)
            await client.call(
                "Emulation.setDeviceMetricsOverride",
                {"width": width, "height": height, "deviceScaleFactor": 1, "mobile": False},
                session_id=session_id,
            )
            await client.call("Page.navigate", {"url": page_url}, session_id=session_id)
            await _await_page_ready(client, session_id, _PAGE_READY_TIMEOUT_SECONDS, page_url)
            page.timing = parse_timing(
                await _evaluate(client, session_id, _READ_TIMING, timeout=_FRAME_CALL_TIMEOUT_SECONDS)
            )
            logger.info(
                "[motion-render] 页面时间轴：%.3fs / %d 个拍点",
                page.timing.duration_seconds,
                len(page.timing.events),
            )
            return page
        except BaseException:
            await _shutdown_browser_client(ws_ctx, client)
            raise

    async def capture_at(self, at: float) -> bytes:
        """把页面时间钉在 ``at`` 秒上，截一张 PNG 交回字节。

        成片帧与拍点静帧走的是同一条：契约要求任意一帧只由 t 决定，所以「先出静帧
        检查节奏」不必另起一套截图路径，两者拿到的画面逐字节相同。
        """
        # String() 包一层：__mg_step 回报的是数字，而 _evaluate 只认字符串结果。
        await _evaluate(
            self._client,
            self._session_id,
            f"String(window.__mg_step({json.dumps(float(at))}))",
            timeout=_FRAME_CALL_TIMEOUT_SECONDS,
        )
        shot = await self._client.call(
            "Page.captureScreenshot",
            {"format": "png", "captureBeyondViewport": False},
            session_id=self._session_id,
            timeout=_FRAME_CALL_TIMEOUT_SECONDS,
        )
        data = shot.get("data")
        if not isinstance(data, str) or not data:
            raise MotionRenderError(f"{at:.3f}s 处截图为空")
        return base64.b64decode(data)

    async def iter_frames(
        self,
        frame_count: int,
        *,
        fps: int,
        on_progress: Optional[Callable[[int, int], None]] = None,
    ) -> AsyncIterator[bytes]:
        """逐帧 yield PNG 字节：把时间钉在 ``i / fps`` 上再截图。

        ``frame_count`` 由调用方算好（时长 × fps），本函数只管往前推时间，页面
        自己决定同一秒内画什么 —— 契约要求逐帧确定性，所以同一 i 永远同一张图。
        """
        if frame_count <= 0:
            raise MotionRenderError("帧数为 0，没有可截的内容")
        for index in range(frame_count):
            yield await self.capture_at(index / float(fps))
            if on_progress is not None:
                on_progress(index + 1, frame_count)

    async def close(self) -> None:
        """关自己的标签页再断开连接；收尾失败只记日志，不盖住已经取得的帧。"""
        if self._target_id:
            try:
                await self._client.call("Target.closeTarget", {"targetId": self._target_id})
            except Exception:  # noqa: BLE001
                logger.debug("[motion-render] closeTarget failed", exc_info=True)
        await _shutdown_browser_client(self._ws_ctx, self._client)
