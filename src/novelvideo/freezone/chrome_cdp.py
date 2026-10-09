# SPDX-License-Identifier: Elastic-2.0
# Copyright (c) 2026 ClaymoreLab
"""[aipdd fork delta] 裸 Chrome DevTools Protocol（CDP）客户端：抖音详情拦截。

设计动机（用户明确要求）：**不引入 Playwright、不往镜像里装任何浏览器**，
直接经 CDP 连接「客户本机已安装的真实 Chrome」（与内置「Chrome MCP」/
chrome-devtools-mcp 同一调试端口来源 ``ST_CHROME_CDP_URL``）。容器里没有
Chrome，唯一能「用本地 Chrome」的方式就是经网络 CDP 连宿主机调试端口。

抖音 Web 详情接口 ``/aweme/v1/web/aweme/detail/`` 由页面自身的 JS 在加载
``/video/{id}`` 时发起，并带 ``a_bogus`` 等风控签名——纯 HTTP 无法自造签名，
所以这里让**真实 Chrome 自己发请求**，我们只监听 Network 域、捕获那条响应体。
真实 Chrome 复用用户登录态与 cookie，通过率远高于无头浏览器（后者被 Argus
否决签名）。

实现只依赖 ``websockets``（uvicorn[standard] 已带）+ ``httpx``，零新增依赖。

CDP 协议要点：
- ``GET {http}/json/version`` 拿 browser 级 ``webSocketDebuggerUrl``；
- browser WS 上 ``Target.createTarget`` + ``Target.attachToTarget(flatten=True)``
  得到 ``sessionId``，后续页面级命令都带 ``sessionId``；
- ``Network.enable`` 后监听 ``Network.responseReceived`` 事件，命中详情 URL 先记下
  ``requestId``，等该请求的 ``Network.loadingFinished`` 到达后再
  ``Network.getResponseBody`` 取 body（可能 base64）——提前取会撞 no data。
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
from typing import Any, Awaitable, Callable
from urllib.parse import urlsplit, urlunsplit

logger = logging.getLogger(__name__)

# 详情接口特征串（页面加载视频时由前端 JS 发起）。
_DETAIL_URL_MARKER = "aweme/v1/web/aweme/detail/"

# [aipdd fork delta req45] 从宿主 Chrome 读 Cookie 时的域名裁剪范围。
# CDP 的 cookie jar 是**整个浏览器**的（含邮箱、购物、内部系统等所有站点），
# 不裁就把无关站点的凭据交给调用方写进设置库。只认这两个注册域及其子域。
_DOUYIN_COOKIE_DOMAINS = ("douyin.com", "iesdouyin.com")
# 首页与视频页预热/捕获的整体预算（秒）。真实 Chrome 有头加载 + SPA 请求留足时间。
_DEFAULT_TIMEOUT_SECONDS = 60.0
# 单次 JSON-RPC 往返超时（秒）。
_CALL_TIMEOUT_SECONDS = 15.0
# 首页预热停留（让页面 JS 生成风控 cookie / signature）。
_WARMUP_SECONDS = 4.0


# 详情响应加载结束后取 body 的重试次数与间隔（CDP 偶尔仍会短暂报 no data）。
_BODY_RETRIES = 3
_BODY_RETRY_INTERVAL_SECONDS = 0.4


class CdpUnavailable(RuntimeError):
    """连不上宿主 Chrome 调试端口（未启动 / 仅监听 127.0.0.1 容器访问不到）。"""


class CdpNoDetail(RuntimeError):
    """连上了 Chrome、导航了视频页，但预算内拿不到详情响应体（风控 / 需登录 / 已删除）。"""


def _is_detail_url(url: str) -> bool:
    return _DETAIL_URL_MARKER in str(url or "")


def is_douyin_cookie_domain(domain: str) -> bool:
    """CDP 回报的 cookie domain 是否属于抖音系。

    domain 常见形态是带前导点的注册域（``.douyin.com``）或主机名（``www.douyin.com``）。
    按「等于注册域或以 ``.注册域`` 结尾」判定——裸 ``endswith("douyin.com")`` 会把
    ``evil-douyin.com`` 这类同尾不同域的站点收进来，那是把别人的凭据当自己的存。
    """

    host = str(domain or "").strip().lower().lstrip(".")
    if not host:
        return False
    return any(
        host == base or host.endswith(f".{base}") for base in _DOUYIN_COOKIE_DOMAINS
    )


def filter_douyin_cookies(cookies: Any) -> dict[str, str]:
    """把 CDP 的 cookie jar 裁成「只留抖音系的 ``name -> value``」。

    同名 cookie 可能同时挂在注册域和具体主机上（``.douyin.com`` 与 ``www.douyin.com``）：
    domain 越具体越优先（浏览器发请求时也是更具体的那份生效），最后按名字排序，让
    多次抓取写进库的字节序稳定（否则 Cookie 名顺序抖动会让「同一次登录」看起来变了）。
    """

    picked: dict[str, tuple[int, str]] = {}
    for item in cookies if isinstance(cookies, (list, tuple)) else []:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        value = str(item.get("value") or "").strip()
        if not name or not value or not is_douyin_cookie_domain(str(item.get("domain") or "")):
            continue
        specificity = len(str(item.get("domain") or "").strip())
        if name not in picked or specificity >= picked[name][0]:
            picked[name] = (specificity, value)
    return {name: picked[name][1] for name in sorted(picked)}


def _rewrite_ws_host(ws_url: str, http_url: str) -> str:
    """把 ``/json/version`` 返回的 ws URL 的 host:port 换成本端实际连的 host:port。

    Chrome 常返回 ``ws://localhost:9222/devtools/browser/<id>``；容器里
    ``localhost`` 指向容器自身，连不通。我们是用 ``http_url``（已解析成宿主 IP）
    访问到端点的，因此把 ws 的 netloc 对齐到它，只保留 ``/devtools/browser/<id>`` 路径。
    """

    try:
        ws_parts = urlsplit(ws_url)
        http_parts = urlsplit(http_url)
    except ValueError:
        return ws_url
    if not http_parts.netloc:
        return ws_url
    scheme = "wss" if http_parts.scheme == "https" else "ws"
    return urlunsplit((scheme, http_parts.netloc, ws_parts.path, ws_parts.query, ""))


def _decode_response_body(result: dict[str, Any]) -> Any:
    """把 ``Network.getResponseBody`` 结果解码并 JSON 解析；失败返回 None。"""

    body = result.get("body")
    if body is None:
        return None
    if result.get("base64Encoded"):
        try:
            body = base64.b64decode(body)
        except (ValueError, TypeError):
            return None
    if isinstance(body, bytes):
        body = body.decode("utf-8", errors="replace")
    try:
        return json.loads(body)
    except (ValueError, TypeError):
        return None


class CdpClient:
    """在单条 browser 级 WebSocket 上做 JSON-RPC，并把事件塞进队列。

    并发模型：一个后台 reader 任务持续 ``async for`` 收消息——带 ``id`` 的
    回填对应 ``call`` 的 Future，带 ``method`` 的塞进 ``events`` 队列供捕获循环消费。
    """

    def __init__(self, ws: Any) -> None:
        self._ws = ws
        self._next_id = 0
        self._pending: dict[int, asyncio.Future[dict[str, Any]]] = {}
        self.events: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self._reader: asyncio.Task[None] | None = None

    async def start(self) -> None:
        self._reader = asyncio.create_task(self._read_loop())

    async def _read_loop(self) -> None:
        try:
            async for raw in self._ws:
                try:
                    msg = json.loads(raw)
                except (ValueError, TypeError):
                    continue
                if not isinstance(msg, dict):
                    continue
                mid = msg.get("id")
                if isinstance(mid, int) and mid in self._pending:
                    fut = self._pending.pop(mid)
                    if not fut.done():
                        fut.set_result(msg)
                elif "method" in msg:
                    self.events.put_nowait(msg)
        except Exception:  # noqa: BLE001 - reader 收尾（连接关闭/异常）不打断主流程
            logger.debug("cdp reader loop ended", exc_info=True)

    async def call(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        *,
        session_id: str | None = None,
        timeout: float = _CALL_TIMEOUT_SECONDS,
    ) -> dict[str, Any]:
        self._next_id += 1
        mid = self._next_id
        payload: dict[str, Any] = {"id": mid, "method": method}
        if params is not None:
            payload["params"] = params
        if session_id is not None:
            payload["sessionId"] = session_id
        fut: asyncio.Future[dict[str, Any]] = asyncio.get_running_loop().create_future()
        self._pending[mid] = fut
        await self._ws.send(json.dumps(payload))
        try:
            resp = await asyncio.wait_for(fut, timeout)
        except asyncio.TimeoutError as exc:
            self._pending.pop(mid, None)
            raise CdpUnavailable(f"CDP 命令 {method} 超时") from exc
        if "error" in resp:
            err = resp["error"]
            raise RuntimeError(f"CDP {method} 报错: {err.get('message') if isinstance(err, dict) else err}")
        result = resp.get("result")
        return result if isinstance(result, dict) else {}

    async def aclose(self) -> None:
        if self._reader is not None:
            self._reader.cancel()
            try:
                await self._reader
            except (asyncio.CancelledError, Exception):  # noqa: BLE001 - 收尾
                pass


async def _fetch_detail_body(
    client: CdpClient, session_id: str, request_id: str
) -> dict[str, Any] | None:
    """取回并解码一条详情的响应体；重试耗尽仍失败返回 ``None``。"""

    last_error = ""
    for _attempt in range(_BODY_RETRIES):
        try:
            body = await client.call(
                "Network.getResponseBody",
                {"requestId": request_id},
                session_id=session_id,
            )
        except Exception as exc:  # noqa: BLE001 - 时机过早时 CDP 报 no data，需重试
            last_error = str(exc)
            await asyncio.sleep(_BODY_RETRY_INTERVAL_SECONDS)
            continue
        payload = _decode_response_body(body)
        if isinstance(payload, dict):
            return payload
        last_error = "响应体不是合法 JSON"
        await asyncio.sleep(_BODY_RETRY_INTERVAL_SECONDS)
    logger.warning(
        "cdp getResponseBody exhausted retries for %s: %s", request_id, last_error
    )
    return None


async def _await_detail(
    client: CdpClient,
    session_id: str,
    *,
    deadline: float,
    expected_video_id: str | None = None,
) -> dict[str, Any]:
    """消费事件队列，拦下详情响应并在**加载结束后**取回响应体。

    [aipdd fork delta] 取 body 的时机必须卡在 ``Network.loadingFinished``：在
    ``Network.responseReceived`` 立刻 ``Network.getResponseBody`` 会撞上
    「No data found for resource with given identifier」——详情 XHR 明明 200 回来了，
    body 却因取得太早而拿不到；旧代码把它跟 ``except: continue`` 一起吞掉，最后只报
    「预算内未拦到详情」，把用户误导成「需登录 / 风控」。

    ``expected_video_id`` 用于校验拦到的确实是目标视频（失败重试分支里页面会先点
    一条推荐位，可能带回别的视频的详情），返回别人的文案比报错更糟。
    """

    loop = asyncio.get_running_loop()
    pending: dict[str, int] = {}  # requestId -> HTTP status（已收到、等 loadingFinished）
    seen = 0
    body_failures = 0
    foreign = 0
    while True:
        remaining = deadline - loop.time()
        if remaining <= 0:
            if body_failures:
                raise CdpNoDetail(
                    f"已拦到 {seen} 条详情响应（HTTP 200）但 CDP 取不到响应体"
                )
            if foreign:
                raise CdpNoDetail(
                    "拦到的详情响应属于其它视频（aweme_id 不匹配），未采用"
                )
            raise CdpNoDetail("预算内未拦到抖音详情响应")
        try:
            event = await asyncio.wait_for(client.events.get(), timeout=min(remaining, 5.0))
        except asyncio.TimeoutError:
            continue
        if event.get("sessionId") != session_id:
            continue
        method = event.get("method")
        params = event.get("params") or {}
        if method == "Network.responseReceived":
            response = params.get("response") or {}
            if not _is_detail_url(response.get("url")):
                continue
            request_id = params.get("requestId")
            if not request_id:
                continue
            seen += 1
            pending[str(request_id)] = int(response.get("status") or 0)
            continue
        if method == "Network.loadingFailed":
            pending.pop(str(params.get("requestId") or ""), None)
            continue
        if method != "Network.loadingFinished":
            continue
        request_id = str(params.get("requestId") or "")
        if request_id not in pending:
            continue
        del pending[request_id]
        payload = await _fetch_detail_body(client, session_id, request_id)
        if payload is None:
            body_failures += 1
            continue
        detail = payload.get("aweme_detail")
        if not isinstance(detail, dict):
            continue
        if expected_video_id:
            got_id = str(detail.get("aweme_id") or "")
            # 拿不到 aweme_id 字段时不卡（无法证伪），错配到别的视频则丢弃继续等。
            if got_id and got_id != str(expected_video_id):
                foreign += 1
                logger.debug(
                    "cdp detail aweme_id mismatch: got %s want %s",
                    got_id,
                    expected_video_id,
                )
                continue
        return payload


async def _connect_browser_client(
    cdp_http_url: str,
    *,
    ws_connect: Callable[[str], Any] | None = None,
    version_getter: Callable[[str], Awaitable[dict[str, Any]]] | None = None,
) -> tuple[Any, CdpClient]:
    """连上宿主 Chrome 的 browser 级 WebSocket，返回 ``(ws 上下文, 已启动的客户端)``。

    连接样板之前内联在 :func:`fetch_douyin_detail` 里；抓 Cookie 要用同一条
    路径（同一端点来源、同一换算与分因报错），抽出来避免两份写法各自漂移。
    调用方负责 ``client.aclose()``。
    """

    if ws_connect is None:
        import websockets

        ws_connect = lambda url: websockets.connect(url, max_size=None)  # noqa: E731

    async def _default_version_getter(url: str) -> dict[str, Any]:
        import httpx

        async with httpx.AsyncClient(timeout=8.0) as client:
            resp = await client.get(f"{url.rstrip('/')}/json/version")
            resp.raise_for_status()
            return resp.json()

    if version_getter is None:
        version_getter = _default_version_getter

    try:
        version = await version_getter(cdp_http_url)
    except CdpUnavailable:
        raise
    except Exception as exc:  # noqa: BLE001 - 端口没起 / 连不上
        raise CdpUnavailable(f"无法连接宿主 Chrome 调试端口 {cdp_http_url}: {exc}") from exc

    ws_url = _rewrite_ws_host(str(version.get("webSocketDebuggerUrl") or ""), cdp_http_url)
    if not ws_url:
        raise CdpUnavailable(f"调试端口 {cdp_http_url} 未返回 webSocketDebuggerUrl")

    try:
        ws_ctx = ws_connect(ws_url)
        ws = await ws_ctx.__aenter__()
    except Exception as exc:  # noqa: BLE001 - WS 握手失败
        raise CdpUnavailable(f"无法连接宿主 Chrome CDP WebSocket {ws_url}: {exc}") from exc

    client = CdpClient(ws)
    await client.start()
    return ws_ctx, client


async def _shutdown_browser_client(ws_ctx: Any, client: CdpClient) -> None:
    """关掉浏览器级客户端与它底下的 WebSocket。

    [aipdd fork delta req45] 收尾必须成对：只 ``client.aclose()`` 会留下
    ``websockets.connect`` 的上下文没退出，每拦一次详情就漏一条连接。收尾异常
    只记日志，不许覆盖已经拿到的结果。
    """

    await client.aclose()
    try:
        await ws_ctx.__aexit__(None, None, None)
    except Exception:  # noqa: BLE001 - WS 关闭异常不影响已拿到的结果
        logger.debug("cdp ws close failed", exc_info=True)


async def fetch_douyin_cookie_jar(
    cdp_http_url: str,
    *,
    ws_connect: Callable[[str], Any] | None = None,
    version_getter: Callable[[str], Awaitable[dict[str, Any]]] | None = None,
) -> list[dict[str, Any]]:
    """读宿主 Chrome 浏览器 cookie jar 的**原始条目**（未按域名裁剪，交给调用方裁）。

    [aipdd fork delta req45] 抖音的 ``sessionid`` 是 httpOnly：页面 JS
    （``document.cookie``）读不到，拦到的 XHR 请求头里也只有实际发出去的那部分，
    只有 CDP 的 cookie 域是完整的一份——所以「把宿主 Chrome 的登录态记下来」只能走这里。

    先 ``Network.getAllCookies``（浏览器全局，不需 session），失败再试
    ``Storage.getCookies``：两个都在部分 Chrome 版本上被改过语义/作废，只试一个会
    因版本而哑。两个都失败才报 `CdpUnavailable`，把成因一起贴出去。
    """

    ws_ctx, client = await _connect_browser_client(
        cdp_http_url, ws_connect=ws_connect, version_getter=version_getter
    )
    errors: list[str] = []
    try:
        for method in ("Network.getAllCookies", "Storage.getCookies"):
            try:
                result = await client.call(method)
            except Exception as exc:  # noqa: BLE001 - 命令被版本/策略拒：下一个候选
                errors.append(f"{method}: {exc}")
                continue
            cookies = result.get("cookies")
            if isinstance(cookies, list):
                return [item for item in cookies if isinstance(item, dict)]
            errors.append(f"{method}: 返回里没有 cookies 数组")
    finally:
        await _shutdown_browser_client(ws_ctx, client)
    raise CdpUnavailable("宿主 Chrome 未返回 cookie 数据：" + "；".join(errors))


async def fetch_douyin_detail(
    cdp_http_url: str,
    video_id: str,
    *,
    timeout: float = _DEFAULT_TIMEOUT_SECONDS,
    ws_connect: Callable[[str], Any] | None = None,
    version_getter: Callable[[str], Awaitable[dict[str, Any]]] | None = None,
) -> dict[str, Any]:
    """经 CDP 连宿主 Chrome，导航视频页并捕获 ``aweme/detail`` 响应体。

    返回形如 ``{"aweme_detail": {...}}`` 的 payload（与 ``parse_aweme_detail`` 口径一致）。
    连接失败抛 ``CdpUnavailable``；连上但拦不到抛 ``CdpNoDetail``。

    ``ws_connect`` / ``version_getter`` 为可注入接缝（默认用 websockets / httpx），
    便于无浏览器单测。
    """

    ws_ctx, client = await _connect_browser_client(
        cdp_http_url, ws_connect=ws_connect, version_getter=version_getter
    )
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    target_id: str | None = None
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

        await client.call("Network.enable", {}, session_id=session_id)
        await client.call("Page.enable", {}, session_id=session_id)

        # 首页预热：让真实 Chrome 生成 __ac_signature 等风控 cookie。
        await client.call("Page.navigate", {"url": "https://www.douyin.com/"}, session_id=session_id)
        await asyncio.sleep(_WARMUP_SECONDS)

        await client.call(
            "Page.navigate",
            {"url": f"https://www.douyin.com/video/{video_id}"},
            session_id=session_id,
        )

        try:
            return await _await_detail(
                client, session_id, deadline=deadline, expected_video_id=video_id
            )
        except CdpNoDetail:
            # 详情 XHR 未自发（SPA 冷启动偶发）：触发一次点击推荐位再回到原页，二次等待。
            await client.call(
                "Runtime.evaluate",
                {
                    "expression": (
                        "var a=document.querySelector(\"a[href*='/video/']\");"
                        "if(a){a.click();}"
                    )
                },
                session_id=session_id,
            )
            await asyncio.sleep(2.0)
            await client.call(
                "Page.navigate",
                {"url": f"https://www.douyin.com/video/{video_id}"},
                session_id=session_id,
            )
            return await _await_detail(
                client, session_id, deadline=deadline, expected_video_id=video_id
            )
    finally:
        if target_id:
            try:
                await client.call("Target.closeTarget", {"targetId": target_id})
            except Exception:  # noqa: BLE001 - 收尾关标签失败不打断
                logger.debug("cdp closeTarget failed", exc_info=True)
        await _shutdown_browser_client(ws_ctx, client)
