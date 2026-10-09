"""抖音文案提取：链接解析 → 详情拦截 → 音频获取 → 用户指定 ASR 模型转写。

[aipdd fork delta] 工具页「抖音文案提取」的执行叶，移植自参考实现
（``需求/参考源码/aipdd-copywrite-fetch-1.0.2/scripts/douyin_fetch.py``），
按本项目口径重写：

- 抖音详情两条通道（实测口径见 docs/changes/2026-10-06-douyin-http-detail-cookie-settings.md）：
  **主链路是纯 HTTP** —— 带登录态 Cookie 的请求不需要 ``a_bogus`` 签名就能拿到完整
  ``aweme_detail``（匿名请求才被静默拒回空体：分享页壳 / 403 ArgusSecurityPlugin /
  oEmbed 那条老结论只对匿名成立）。**兜底是 CDP** —— Cookie 失效、被风控或用户不愿
  配 Cookie 时，经裸 CDP 连「客户本机已安装、已登录的真实 Chrome」（与 Chrome MCP
  同一浏览器）拦 ``aweme/v1/web/aweme/detail/`` 响应。用户明确要求不引入 Playwright、
  不往镜像装任何浏览器。[aipdd fork delta req45] CDP 兜底走通后会把那份登录态**自动
  记进设置库**（见 :mod:`novelvideo.freezone.douyin_cookie_capture`），所以「首次处理
  以后」第二次起就不必再开着调试窗口。
- ASR 不沿用参考包的固定路由顺序：用户在模型候选里显式指定 ——

  ① 网关 ASR（``{网关}/audio/transcriptions``，OpenAI 兼容 multipart，本地音频
     直接上传，任意视频都能自动化；组织身份走 ``begin_trusted_audio_operation``
     受信出网，与 TTS 同一份租约协议）；
  ② FunASR（aipdd ``shared-tasks`` 异步任务协议，直连平台、仅接受公网音频
     直链——平台无公开文件上传接口；组织 fail-closed：直连使用平台渠道凭据，
     与 MediaKit 出网同性质，组织任务必须改选网关模型）；
  ③ 豆包语音 ASR（火山 openspeech v3 录音文件识别：标准版 submit/query 轮询、
     极速版单请求；公网直链或本地音频 base64。鉴权用单个 API Key（官方《API
     Key 使用》：任意接口带 ``X-Api-Key`` header 即可、无需 appid）。凭证存
     设置数据库（volcengine 渠道 settings.asr，设置页 VolcEngine 卡填写，
     不走环境变量）；组织 fail-closed 口径与 ② 同）。

- 文案来源以用户选定的 ASR 模型为主；模型返回空文本时回落视频内置字幕
  （抖音 ``subtitle_infos``，参考包的第一层数据源），两者皆空才报错。
- 产物按本项目口径落盘：``{freezone}/_outputs/freezone_douyin_copywrite/{job}.txt``
  （元信息头 + 正文），由 runner 生成静态 URL 并放进任务结果。
"""

from __future__ import annotations

import asyncio
import base64
import logging
import os
import re
import shutil
import subprocess
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from novelvideo.egress_context import TrustedEgressContext
from novelvideo.freezone.chrome_cdp import (
    CdpNoDetail,
    CdpUnavailable,
    fetch_douyin_detail,
)
from novelvideo.freezone.douyin_cookie_capture import capture_douyin_cookie
from novelvideo.freezone.douyin_web_api import (
    DouyinHttpRejected,
    DouyinHttpUnavailable,
    douyin_web_cookie,
    fetch_douyin_detail_via_http,
)
from novelvideo.freezone.paths import outputs_dir
from novelvideo.utils.chrome_cdp import browsable_cdp_url

logger = logging.getLogger(__name__)

# 抖音桌面 UA（下载/详情页）与移动 UA（短链解析），口径与参考包一致。
_DOUYIN_DESKTOP_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)
_DOUYIN_MOBILE_UA = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1"
)
_DOUYIN_REFERER = "https://www.douyin.com/"
_DOWNLOAD_TIMEOUT_SECONDS = 120.0
_AUDIO_EXTRACT_TIMEOUT_SECONDS = 300.0

# 视频直链择优口径（移植参考包 `_find_best_video_url`）：优先含音频的混合流。
_VIDEO_URL_PRIORITY_KEYWORDS = ("ve", "audio", "playwm", "play_addr", "douyinvod")

# 「最佳视频 URL」关键词按参考包「检查 URL 小写子串」实现：`ve` 是 douyin CDN
# 域名族（v3-web.douyinvod.com / v26-web.douyin.com 等）里的常见片段。
_SUBTITLE_TIME_RE = re.compile(
    r"^\d{2}:\d{2}:\d{2}[\.,]\d{3}\s*-->\s*\d{2}:\d{2}:\d{2}[\.,]\d{3}"
)
_TAG_RE = re.compile(r"#\S+")
_EMOJI_RE = re.compile(r"[\U00010000-\U0010ffff]")

# ASR 候选模型的名字**兜底**口径（网关渠道目录没声明能力时才用）：覆盖 asr /
# whisper / 转写 / SenseVoice / Paraformer / FunASR / stt 等常见命名。误伤面小、
# 漏选可让用户在设置页显式配置；宁可选多了用户不选，不可选漏了用户选不到。
ASR_MODEL_NAME_RE = re.compile(
    r"(asr|whisper|transcri|sensevoice|paraformer|funasr|\bstt\b)",
    re.IGNORECASE,
)


def is_asr_candidate_model(model: str, provider: str = "") -> bool:
    """转写候选资格：渠道目录**声明**优先，名字正则只在无声明时兜底。

    [aipdd fork delta req40] 旧实现只有一条名字正则：新命名风格的转写模型名字里
    没有 asr/whisper 就选不出来，反过来「名字里带 stt 但其实不是转写」这类误伤也无
    法排除。渠道自己在目录里写了 ``kind`` / ``endpoints`` 时就按声明判。
    """

    clean = str(model or "").strip()
    if not clean:
        return False
    try:
        from novelvideo.model_gateway_settings import upstream_catalog_entry
        from novelvideo.upstream_catalog import supports_asr

        entry = upstream_catalog_entry(clean, provider)
    except Exception:  # noqa: BLE001 - 目录不可用时退回名字正则，不能把候选清空
        entry = None
    if entry is not None and not entry.get("inferred"):
        return supports_asr(entry)
    return bool(ASR_MODEL_NAME_RE.search(clean))


def asr_accepts_local_upload(model: str, provider: str = "") -> bool:
    """网关候选是否接受**本地音频直传**（multipart）。

    [aipdd fork delta req40] 前端以前是拿 ``kind === "funasr"`` 当「只吃公网直链」
    的判据 —— 那是把「哪个模型能接受什么输入」绑在通道名上。现在由后端按目录声明回答：
    渠道写了 ``audio/transcriptions`` 端点 = 能直传；条目本身推断出来（无元数据）时沿用
    旧行为（能直传），不把候选误标成不可用。
    """

    clean = str(model or "").strip()
    if not clean:
        return False
    try:
        from novelvideo.model_gateway_settings import upstream_catalog_entry
        from novelvideo.upstream_catalog import EP_AUDIO_TRANSCRIPTIONS, declares_endpoint

        entry = upstream_catalog_entry(clean, provider)
    except Exception:  # noqa: BLE001 - 目录不可用时维持旧行为
        return True
    if entry is None or entry.get("inferred"):
        return True
    return declares_endpoint(entry, EP_AUDIO_TRANSCRIPTIONS)


# 内置 FunASR 通道：aipdd 平台 shared-tasks 的语音转写任务类型（参考包协议）。
FUNASR_TASK_TYPE_CODE = "aipdd_funasr_transcribe"
FUNASR_CANDIDATE_ID = f"funasr:aipdd:{FUNASR_TASK_TYPE_CODE}"
# [aipdd fork delta] 下拉 / 产物展示名：任务码是内部协议标识，不对外展示。
FUNASR_DISPLAY_NAME = "FunASR"
# 网关通道候选 id 前缀（`gateway:{provider}:{model}`）。
GATEWAY_CANDIDATE_PREFIX = "gateway:"

# [aipdd fork delta] 豆包语音（openspeech）录音文件识别：火山 ASR 属豆包语音
# 产品线，不在方舟模型列表里（方舟无 ASR 模型）、OpenAI 兼容网关也不代理，
# 执行叶直连 openspeech v3 协议。候选 id 形如 `doubao_asr:openspeech:{variant}`。
DOUBAO_ASR_CANDIDATE_PREFIX = "doubao_asr:openspeech:"
DOUBAO_ASR_ENDPOINT = "https://openspeech.bytedance.com"
_DOUBAO_ASR_VARIANTS: dict[str, dict[str, str]] = {
    # 标准版 2.0 / 1.0：submit 提交 + query 轮询（同一 X-Api-Request-Id）。
    "seedasr": {
        "label": "Doubao ASR 2.0",
        "resource_id": "volc.seedasr.auc",
        "submit_path": "/api/v3/auc/bigmodel/submit",
        "query_path": "/api/v3/auc/bigmodel/query",
    },
    "bigasr": {
        "label": "Doubao ASR 1.0",
        "resource_id": "volc.bigasr.auc",
        "submit_path": "/api/v3/auc/bigmodel/submit",
        "query_path": "/api/v3/auc/bigmodel/query",
    },
    # 极速版（Lite）：单请求同步返回。
    "flash": {
        "label": "Doubao ASR Flash",
        "resource_id": "volc.bigasr.auc_turbo",
        "recognize_path": "/api/v3/auc/bigmodel/recognize/flash",
    },
}
# submit/query 异步轮询间隔（秒）。
_DOUBAO_ASR_POLL_SECONDS = 3.0

# 提交结果落盘目录名（与 outputs_dir 的第二参数一致）。
_TASK_TYPE = "freezone_douyin_copywrite"


# ============================================================
# 链接解析（移植参考包 resolve_video_id，错误口径改为抛错）
# ============================================================


def extract_share_url(input_str: str) -> str:
    """从整段分享文案里抠出第一个可用链接（[aipdd fork delta]）。

    抖音/小红书等分享文案常混带随机口令（「7.99 c@n.qr 09/02 …」）、中文描述与
    尾部提示语（「复制此链接，打开…」）；直接喂给 yt-dlp 会被 generic 提取器判
    「is not a valid URL」而立即失败。优先匹配带协议的 ``https?://…``（截到空白为
    止、去掉尾随标点），其次补全无协议的 ``v.douyin.com`` 短链；两者都没有则原样
    返回（可能本就是纯链接或数字 ID，交由下游各自判断）。
    """

    text = str(input_str or "").strip()
    if not text:
        return text
    url_match = re.search(r"https?://[^\s]+", text)
    if url_match:
        return url_match.group(0).rstrip(".,;!?）)】]》")
    # 无协议短链混在分享文案里的情形（如「…… v.douyin.com/xxxx/ ……」）。
    short_match = re.search(r"v\.douyin\.com/[A-Za-z0-9_\-/]+", text)
    if short_match:
        return f"https://{short_match.group(0)}"
    return text


async def resolve_douyin_video_id(input_str: str) -> str:
    """从用户输入解析视频 ID：纯数字 / 完整链接 / v.douyin.com 短链（跟随重定向）。

    输入可以是整段分享文案（含中文描述与链接），先抠出 URL 再匹配；解析失败抛
    ``ValueError``（本地输入错，不重试）或 ``RuntimeError``（短链网络失败，可重试）。
    """

    import httpx

    text = str(input_str or "").strip()
    if not text:
        raise ValueError("抖音视频链接不能为空")
    if text.isdigit():
        return text

    text = extract_share_url(text)

    match = re.search(r"/(?:share/)?video/(\d+)", text)
    if match:
        return match.group(1)

    if "v.douyin.com" in text:
        url = text if text.startswith("http") else f"https://{text}"
        # [aipdd fork delta] 旧实现只看 "v.douyin.com" 子串：把内网主机拼成
        # ``http://169.254.169.254/?x=v.douyin.com`` 即可过门向内网发请求（盲 SSRF）。
        # 现按解析后的真实 host 严格收敛到 v.douyin.com 域：只有这一（公网）域会被连接，
        # 子串伪装的任意主机被拒。v.douyin.com 子域不可由调用方注册，不引入对系统 DNS 的
        # 运行期依赖（避免为拼一个 host 而做同步 getaddrinfo）。
        host = (urlsplit(url).hostname or "").lower()
        if host != "v.douyin.com" and not host.endswith(".v.douyin.com"):
            raise ValueError("无法识别的抖音链接：请提供纯数字视频 ID、完整链接或 v.douyin.com 短链")
        try:
            async with httpx.AsyncClient(
                timeout=15.0, follow_redirects=True
            ) as client:
                response = await client.get(
                    url, headers={"User-Agent": _DOUYIN_MOBILE_UA}
                )
                final_url = str(response.url)
        except httpx.HTTPError as exc:
            raise RuntimeError(f"抖音短链解析失败: {exc}") from exc
        resolved = re.search(r"/(?:share/)?video/(\d+)", final_url)
        if resolved:
            return resolved.group(1)
        # 新版分享链常落在图文 note 页或 modal_id 参数上：同样能拿到视频 ID。
        fallback = re.search(r"(?:modal_id=|/note/)(\d+)", final_url)
        if fallback:
            return fallback.group(1)
        raise RuntimeError("抖音短链解析后未找到视频 ID，请改用完整视频链接或纯数字 ID")

    raise ValueError("无法识别的抖音链接：请提供纯数字视频 ID、完整链接或 v.douyin.com 短链")


# ============================================================
# 详情拦截（裸 CDP 连宿主真实 Chrome，见 novelvideo.freezone.chrome_cdp）
# ============================================================

# [aipdd fork delta] 用户明确要求：不引入 Playwright、不往镜像装浏览器——拦截只经
# CDP 连「客户本机已安装的真实 Chrome」（调试端口 ST_CHROME_CDP_URL，compose 默认
# host.docker.internal:9222，与内置「Chrome MCP」/chrome-devtools-mcp 同一来源）。
# 真实 Chrome 复用用户登录态、能过抖音 Argus 详情签名；容器里 Chrome 不绑定
# localhost，连接前经 browsable_cdp_url 解析成宿主可达 IP。
_CHROME_CDP_URL_ENV = "ST_CHROME_CDP_URL"
_DEFAULT_CHROME_CDP_URL = "http://host.docker.internal:9222"
# CDP 整体预算（首页预热 + 视频页加载 + 详情 XHR 捕获），超时判定为需登录/风控。
_DETAIL_TIMEOUT_SECONDS = 60.0


def chrome_cdp_url() -> str:
    """客户自带 Chrome 的调试端口地址（与 Agent「Chrome MCP」共用同一来源）。

    [aipdd fork delta req45] 由 ``_chrome_cdp_url`` 改公开名：「拦详情」和「抓 Cookie」
    用的是同一个宿主 Chrome，必须走同一个端点换算（主机名解析成 IP），不能开第二份
    读环境的代码各自漂移。
    """
    raw = os.environ.get(_CHROME_CDP_URL_ENV, "").strip() or _DEFAULT_CHROME_CDP_URL
    # [aipdd fork delta] Chrome 只接受 IP / localhost 的 Host 头，容器里
    # host.docker.internal 这类主机名会被直接拒（HTTP 500），统一解析成 IP。
    return browsable_cdp_url(raw)


async def _intercept_douyin_detail(video_id: str) -> dict[str, Any]:
    """经 CDP 连「客户本机已安装的真实 Chrome」拦截抖音详情（替代 Playwright）。

    [aipdd fork delta] 用户明确要求：不引入 Playwright、不下载/安装任何浏览器——
    浏览器只用客户本机已装、以调试端口启动的 Chrome（与内置「Chrome MCP」同一
    来源，见 :mod:`novelvideo.freezone.chrome_cdp`）。真实 Chrome 复用登录态、能过
    抖音 Argus 详情签名。连接失败给可操作指引（调试端口没起 / 主机名形式的 Host 头
    被 Chrome 拒回 500）；连上但拦不到详情判为需登录 / 风控 / 视频已删除。
    """

    cdp_url = chrome_cdp_url()
    try:
        return await fetch_douyin_detail(
            cdp_url, video_id, timeout=_DETAIL_TIMEOUT_SECONDS
        )
    except CdpUnavailable as exc:
        raise RuntimeError(
            "抖音文案提取需要宿主机以调试端口启动的真实 Chrome（与「Chrome MCP」"
            f"同一浏览器，不额外安装任何浏览器）。当前无法连接 CDP 端点 {cdp_url}："
            "请在宿主机执行 bash scripts/douyin-cdp-chrome.sh --background 启动该 "
            "Chrome（独立 profile，不动日常 Chrome），并在弹出的窗口里登录抖音。"
            "注意：日常 Chrome 已经在运行时，用 open -a 加 --remote-debugging-port "
            "不会生效（参数会被现有进程忽略，默认 profile 也不开放调试端口）。"
            "容器内不可达时按需设置 ST_CHROME_CDP_URL。"
            f"（原始错误：{exc}）"
        ) from exc
    except CdpNoDetail as exc:
        # [aipdd fork delta] 「拦到详情但取不到响应体」与「一条都没拦到」成因不同，
        # 开同一句「需登录/风控」会把用户带偏，按子异常分流给可操作提示。
        if "取不到响应体" in str(exc):
            reason = (
                "详情接口已响应但 Chrome 侧取不到响应体（多为该标签页被回收/内存节省，"
                "或被其它调试客户端占用），请刷新该视频页后重试"
            )
        elif "其它视频" in str(exc):
            reason = "只拦到了其它视频的详情（已按 aweme_id 丢弃），请稍后重试"
        else:
            reason = "视频可能已删除、需要登录或触发风控"
        raise RuntimeError(
            f"未能拦截到抖音详情数据（已连上宿主 Chrome，{reason}），"
            f"请确认该 Chrome 已登录抖音后重试。（{exc}）"
        ) from exc


# ============================================================
# 详情获取主链路：纯 HTTP（登录态 Cookie）优先 → CDP 兜底
# ============================================================

# [aipdd fork delta] 渠道顺序调整的背景：CDP 是唯一通道时，「Chrome 窗口必须开着」
# 成为报错主因；实测证明带登录态 Cookie 的请求不需要 a_bogus 签名（见
# :mod:`novelvideo.freezone.douyin_web_api` 模块文档），因此把纯 HTTP 提为主链路，
# CDP 保留为兜底（Cookie 失效 / 风控 / 未配置时仍然可用，也仍是无 Cookie 用户的唯一路）。
# yt-dlp 不参与详情获取（它拿不到 desc/字幕等结构化详情），仍只作媒体下载的回退。

_DETAIL_SOURCE_LABELS = {"http": "HTTP（登录态 Cookie）", "cdp": "CDP 真实 Chrome"}


async def _record_douyin_cookie_from_chrome() -> None:
    """[aipdd fork delta req45] CDP 兜底走通后，顺手把宿主 Chrome 的登录态记进设置库。

    这才是「首次处理以后记录抖音 Cookie」那条通路：本次就在用真实 Chrome，凭据就在
    浏览器里，不记下来下次还得再开一次调试窗口。best-effort：抓不到 / 写不进只记日志，
    绝不改变本次提取结果，也不把「没 Chrome 可抓」变成用户眼中的提取失败。
    """

    try:
        result = await capture_douyin_cookie(cdp_url=chrome_cdp_url())
    except Exception as exc:  # noqa: BLE001 - 记录失败不能拖垮已成功的提取
        logger.warning("douyin cookie auto-capture crashed: %s", exc)
        return
    if result.saved:
        logger.info(
            "douyin cookie auto-recorded from host chrome: %d cookies (%s)",
            result.cookie_count,
            ", ".join(result.cookie_names[:12]),
        )
    else:
        logger.info("douyin cookie not auto-recorded: %s", result.reason)


async def _fetch_douyin_detail(video_id: str) -> tuple[dict[str, Any], str]:
    """拉取抖音详情：先走纯 HTTP，不可用/被拒时回落 CDP。

    返回 ``(payload, 渠道标记)``。两路都失败时把两侧成因拼进同一条报错，避免用户
    在「其实只是 Cookie 过期」时被引导去开浏览器，或在「Chrome 没开」时白刷 Cookie。
    """

    http_error = ""
    # 「库里的凭据被平台明确拒过」与「本通道用不了」分开记：前者 = 已过期，正好拿
    # 浏览器里那份新的顶掉；后者不能拿来当「用户手贴的凭据作废」的结论。
    http_rejected = False
    configured = bool(douyin_web_cookie())
    if configured:
        try:
            return await fetch_douyin_detail_via_http(video_id), "http"
        except DouyinHttpRejected as exc:
            # 主链路失败不阻断：记下成因，继续走 CDP 兜底。
            http_rejected = True
            http_error = str(exc)
            logger.warning("douyin http detail rejected for %s: %s", video_id, exc)
        except DouyinHttpUnavailable as exc:
            http_error = str(exc)
            logger.warning("douyin http detail channel unusable for %s: %s", video_id, exc)
    else:
        http_error = "未配置抖音 Cookie（设置 → 抖音 Cookie）"

    payload: dict[str, Any]
    try:
        payload = await _intercept_douyin_detail(video_id)
    except RuntimeError as exc:
        if http_error:
            raise RuntimeError(f"{exc}；HTTP 主链路也不可用：{http_error}") from exc
        raise
    # [aipdd fork delta req45] 只有「确实靠得上宿主 Chrome」才记录：HTTP 主链路本来
    # 就通时不抓（不拿浏览器那份覆盖用户手贴的凭据）；而库里的被平台拒过 = 已过期，
    # 正好换成浏览器里新鲜的那份。
    if not configured or http_rejected:
        await _record_douyin_cookie_from_chrome()
    return payload, "cdp"


# ============================================================
# 详情解析（移植参考包 parse_aweme_api_response / _find_best_video_url）
# ============================================================


def _find_best_video_url(urls: list[str]) -> str:
    """按参考包优先级选「含音频的混合流」；都命不中取第一个。"""

    if not urls:
        return ""
    for keyword in _VIDEO_URL_PRIORITY_KEYWORDS:
        for url in urls:
            if keyword in str(url).lower():
                return str(url)
    return str(urls[0])


def parse_aweme_detail(api_data: dict[str, Any]) -> dict[str, Any]:
    """把详情 API 响应解析成结构化视频信息（移植参考包，字段口径不变）。"""

    aweme = (
        api_data.get("aweme_detail")
        or (api_data.get("aweme_list") or [{}])[0]
        or (api_data.get("item_list") or [{}])[0]
        or {}
    )
    stats = aweme.get("statistics") or {}
    video_info = aweme.get("video") or {}
    play_addr = video_info.get("play_addr") or video_info.get("download_addr") or {}
    urls = list(play_addr.get("url_list") or video_info.get("url_list") or [])

    subtitle_infos = aweme.get("subtitle_infos") or []
    if not subtitle_infos:
        subtitle_infos = (video_info.get("subtitle") or {}).get("subtitleInfos") or []

    # 原声音频直链：music.title 含「原声」时（如「@作者创作的原声」），play_url
    # 是 douyinstatic.com 的无防盗链 mp3——参考包实测视频直链（douyinvod）有
    # 防盗链会被平台拒，这个直链是 FunASR 通道的可用音频源。
    music = aweme.get("music") or {}
    music_audio_url = ""
    if "原声" in str(music.get("title") or ""):
        music_urls = (music.get("play_url") or {}).get("url_list") or []
        if music_urls:
            music_audio_url = str(music_urls[0])

    return {
        "aweme_id": str(aweme.get("aweme_id") or ""),
        "desc": str(aweme.get("desc") or ""),
        "author": str((aweme.get("author") or {}).get("nickname") or ""),
        "stats": {
            "digg_count": int(stats.get("digg_count") or 0),
            "comment_count": int(stats.get("comment_count") or 0),
            "collect_count": int(stats.get("collect_count") or 0),
            "share_count": int(stats.get("share_count") or 0),
        },
        "duration_seconds": int(video_info.get("duration") or 0) // 1000,
        "video_url": _find_best_video_url(urls),
        "subtitle_infos": list(subtitle_infos),
        "music_audio_url": music_audio_url,
    }


# ============================================================
# 媒体下载与本地音频抽取
# ============================================================


async def _download_binary(url: str, dest: Path) -> Path:
    """流式下载媒体文件到 ``dest``；带桌面 UA 与抖音 Referer（移植参考包口径）。"""

    import httpx

    dest.parent.mkdir(parents=True, exist_ok=True)
    headers = {"User-Agent": _DOUYIN_DESKTOP_UA, "Referer": _DOUYIN_REFERER}
    async with httpx.AsyncClient(
        timeout=_DOWNLOAD_TIMEOUT_SECONDS, follow_redirects=True
    ) as client:
        async with client.stream("GET", url, headers=headers) as response:
            response.raise_for_status()
            with dest.open("wb") as handle:
                async for chunk in response.aiter_bytes(chunk_size=65536):
                    handle.write(chunk)
    if not dest.exists() or dest.stat().st_size <= 0:
        raise RuntimeError("抖音媒体下载失败：目标文件为空")
    return dest


async def _extract_audio_16k_wav(video_path: Path, dest: Path) -> Path:
    """ffmpeg 抽取 16k 单声道 wav（参考包参数：-vn -acodec pcm_s16le -ar 16000 -ac 1）。"""

    if not shutil.which("ffmpeg"):
        raise RuntimeError("未检测到 ffmpeg（提取视频音频需要）：macOS 可执行 brew install ffmpeg")
    dest.parent.mkdir(parents=True, exist_ok=True)
    process = await asyncio.to_thread(
        subprocess.run,
        [
            "ffmpeg",
            "-y",
            "-i",
            str(video_path),
            "-vn",
            "-acodec",
            "pcm_s16le",
            "-ar",
            "16000",
            "-ac",
            "1",
            str(dest),
        ],
        capture_output=True,
        text=True,
        timeout=int(_AUDIO_EXTRACT_TIMEOUT_SECONDS),
    )
    if process.returncode != 0 or not dest.exists() or dest.stat().st_size <= 1000:
        raise RuntimeError(f"音频提取失败: {(process.stderr or '')[-300:]}")
    return dest


# ============================================================
# 内置字幕兜底（移植参考包 extract_subtitle_from_api / _parse_subtitle_content）
# ============================================================


def parse_subtitle_text(content: str) -> str:
    """解析 VTT / SRT 字幕为纯文本（跳过时间轴、序号与无中文的标签行）。"""

    lines: list[str] = []
    for raw in str(content or "").strip().splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith("WEBVTT") or line.startswith("NOTE"):
            continue
        if _SUBTITLE_TIME_RE.match(line):
            continue
        if re.fullmatch(r"\d+", line):
            continue
        if line.startswith("<") and not re.search(r"[\u4e00-\u9fff]", line):
            continue
        clean = re.sub(r"<[^>]+>", "", line).strip()
        if clean and len(clean) > 1:
            lines.append(clean)
    return "\n".join(lines)


async def _fetch_subtitle_text(subtitle_infos: Any) -> str:
    """下载并解析视频内置字幕（zh 优先，其次任意语言）；失败返回空串。"""

    import httpx

    infos = subtitle_infos if isinstance(subtitle_infos, list) else []
    zh_url = ""
    any_url = ""
    for info in infos:
        if not isinstance(info, dict):
            continue
        url = str(info.get("Url") or info.get("url") or "").strip()
        if not url:
            continue
        language = str(info.get("LanguageCodeName") or info.get("languageCodeName") or "").lower()
        if "zh" in language or "cn" in language or "chinese" in language:
            zh_url = url
            break
        any_url = any_url or url
    subtitle_url = zh_url or any_url
    if not subtitle_url:
        return ""
    try:
        async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
            response = await client.get(subtitle_url)
            response.raise_for_status()
            return parse_subtitle_text(response.text)
    except Exception as exc:  # noqa: BLE001 - 字幕兜底是 best-effort，失败不翻任务
        logger.warning("douyin subtitle download failed: %s", exc)
        return ""


# ============================================================
# ASR 模型候选（网关渠道快照过滤 + 内置 FunASR 通道）
# ============================================================


def _gateway_candidate_id(provider: str, model: str) -> str:
    return f"{GATEWAY_CANDIDATE_PREFIX}{provider}:{model}"


def _funasr_configured() -> bool:
    """FunASR 通道是否可用（aipdd 渠道 baseUrl/upstreamKey 是否齐备）。

    仅做只读探测供候选清单标注就绪状态；探测失败一律按未配置处理。
    """

    try:
        _funasr_platform_config()
    except Exception:  # noqa: BLE001 - 就绪探测不因配置读取异常而失败
        return False
    return True


def _doubao_asr_configured() -> bool:
    """豆包语音 ASR 凭证是否已配置（设置数据库 volcengine 渠道 settings.asr）。

    openspeech 凭证（单个 API Key）与方舟/网关 API Key 不通用，属独立
    产品线；此处只探测存在性供前端在提交入口提醒「去设置」，不代表调用会成功。
    """

    try:
        _doubao_asr_credentials()
    except Exception:  # noqa: BLE001 - 就绪探测不因配置读取异常而失败
        return False
    return True


def list_douyin_asr_models() -> list[dict[str, Any]]:
    """抖音文案提取的 ASR 模型候选清单（提交端点与前端下拉共用）。

    来源基于「网关拉取的模型 list」+ 内置直连通道：

    1. 各渠道目录按转写能力过滤（渠道声明了 ``kind`` / 端点就按声明，没声明才按名字
       正则兜底，如 doubao-seed-asr / whisper-1），零远程读取——与设置页模型广场展示
       同一份快照真源；
    2. 内置 FunASR 通道（aipdd ``shared-tasks`` 异步协议，需公网音频直链）；
    3. 内置豆包语音 ASR 通道（火山 openspeech v3，直链或本地音频 base64）。

    每项形如 ``{"id", "kind", "provider", "channel", "model", "label", "ready"}``；
    ``id`` 是提交时回传的稳定标识（``gateway:{provider}:{model}`` /
    ``funasr:aipdd:{task}`` / ``doubao_asr:openspeech:{variant}``）。``channel``
    是展示用渠道（工具页在模型名旁渲染彩色渠道胶囊），与网关渠道名同源；
    ``label`` 是展示名（不再带渠道后缀，也不含内部任务码）。``ready`` 标记该
    候选依赖的渠道/凭证是否就绪（gateway 恒真——候选本身来自已配置渠道快照；
    funasr / doubao_asr 为只读探测结果），前端在提交按钮附近对未就绪候选提醒
    去设置页配置。``acceptsLocalAudio`` 回答「这一路能不能收本地上传的音频」：
    网关候选按渠道目录声明的 ``audio/transcriptions`` 端点判，内置 FunASR 通道
    固定为 ``False``（协议只吃公网直链）—— 前端不再拿通道名反推输入形态。
    """

    from novelvideo.model_gateway_settings import get_newapi_provider_channels
    from novelvideo.newapi_provisioner import upstream_passthrough_models

    try:
        channels = get_newapi_provider_channels()
    except Exception:  # noqa: BLE001 - 渠道配置读不到时仍给出 FunASR 候选
        logger.warning("douyin copywrite: failed to read provider channels", exc_info=True)
        channels = []

    candidates: list[dict[str, Any]] = []
    for channel in channels:
        if not isinstance(channel, dict):
            continue
        provider = str(channel.get("provider") or "").strip().lower()
        if not provider or provider == "comfyui":
            continue
        try:
            models = upstream_passthrough_models(provider, from_snapshot=True)
        except Exception:  # noqa: BLE001 - 单渠道快照读失败不影响其他候选
            continue
        for model in models:
            if not is_asr_candidate_model(str(model), provider):
                continue
            candidates.append(
                {
                    "id": _gateway_candidate_id(provider, str(model)),
                    "kind": "gateway",
                    "provider": provider,
                    # [aipdd fork delta] 展示用渠道：工具页把渠道渲染成模型名旁的
                    # 彩色胶囊，label 里不再重复「（provider）」后缀。
                    "channel": provider,
                    "model": str(model),
                    "label": str(model),
                    "ready": True,
                    "acceptsLocalAudio": asr_accepts_local_upload(str(model), provider),
                }
            )

    candidates.append(
        {
            "id": FUNASR_CANDIDATE_ID,
            "kind": "funasr",
            "provider": "aipdd",
            # [aipdd fork delta] 展示用渠道：内置 FunASR 通道归属 aipdd。
            "channel": "aipdd",
            "model": FUNASR_TASK_TYPE_CODE,
            "label": FUNASR_DISPLAY_NAME,
            "ready": _funasr_configured(),
            # 内置 FunASR 异步通道只接受公网音频直链（协议硬约束，与目录无关）。
            "acceptsLocalAudio": False,
        }
    )
    # [aipdd fork delta] 豆包语音 ASR：内置通道（openspeech 直连，非方舟/网关），
    # 展示归属 volcengine（火山渠道）。凭证缺失不影响候选展示，ready 标志供工具页
    # 在提交入口提醒配置；执行时仍会给出可操作报错。
    doubao_ready = _doubao_asr_configured()
    for variant, spec in _DOUBAO_ASR_VARIANTS.items():
        candidates.append(
            {
                "id": f"{DOUBAO_ASR_CANDIDATE_PREFIX}{variant}",
                "kind": "doubao_asr",
                "provider": "volcengine",
                "channel": "volcengine",
                "model": variant,
                "label": spec["label"],
                "ready": doubao_ready,
                # openspeech v3 支持直链或本地音频 base64，归为可本地上传。
                "acceptsLocalAudio": True,
            }
        )

    seen: set[str] = set()
    unique: list[dict[str, Any]] = []
    for candidate in candidates:
        if candidate["id"] in seen:
            continue
        seen.add(candidate["id"])
        unique.append(candidate)
    return unique


def resolve_douyin_asr_candidate(model: str) -> dict[str, str]:
    """把候选 id 解析成 ``{id, kind, provider, model, display}``；未指定默认 FunASR。

    提交端点先用 ``list_douyin_asr_models`` 校验存在性；runner/leaf 侧只做形状
    解析（任务执行时不重读渠道配置，避免投递与执行之间配置漂移改变语义）。
    ``display`` 是产物 txt / 来源标注用的展示名：网关候选 = 上游模型名真名，
    FunASR / 豆包候选 = 型号展示名 —— 任务码等内部协议标识不对外展示。
    """

    clean = str(model or "").strip() or FUNASR_CANDIDATE_ID
    if clean == FUNASR_CANDIDATE_ID or clean == FUNASR_TASK_TYPE_CODE:
        return {
            "id": FUNASR_CANDIDATE_ID,
            "kind": "funasr",
            "provider": "aipdd",
            "model": FUNASR_TASK_TYPE_CODE,
            "display": FUNASR_DISPLAY_NAME,
        }
    if clean.startswith(DOUBAO_ASR_CANDIDATE_PREFIX):
        variant = clean[len(DOUBAO_ASR_CANDIDATE_PREFIX) :].strip()
        spec = _DOUBAO_ASR_VARIANTS.get(variant)
        if spec:
            return {
                "id": clean,
                "kind": "doubao_asr",
                "provider": "volcengine",
                "model": variant,
                "display": spec["label"],
            }
    if clean.startswith(GATEWAY_CANDIDATE_PREFIX):
        parts = clean[len(GATEWAY_CANDIDATE_PREFIX) :].split(":", 1)
        if len(parts) == 2 and parts[0].strip() and parts[1].strip():
            return {
                "id": clean,
                "kind": "gateway",
                "provider": parts[0].strip(),
                "model": parts[1].strip(),
                "display": parts[1].strip(),
            }
    raise ValueError(f"无法识别的 ASR 模型候选: {clean}")


# [aipdd fork delta] 「本地音频（无公网直链）」场景的统一 ASR 选择口径。人声复刻、
# 视频复刻台词转写等都走这里，避免各处再各写一套优先级导致行为漂移。
def pick_asr_candidate_for_local_audio(
    asr_model: str = "", *, is_organization: bool = False
) -> dict[str, str] | None:
    """为「只有本地音频文件、拿不到公网直链」的转写挑一个可用 ASR 候选。

    口径（与 ``_transcribe`` 的通道能力一致）：

    - **FunASR 永不参与**：它只吃公网音频直链（平台没有上传接口），本地抽出的
      mp3/wav 没有直链可用；
    - **组织任务不用直连通道**（豆包语音 / FunASR 都用平台侧凭据），只留网关
      multipart 上传这一条路；
    - 显式指定的候选不可用（未识别 / 被上面两条排除）时**退回自动选择**而不是报错，
      因为候选清单会随渠道配置漂移，提交时的选择到执行时可能已经失效；
    - 自动顺序：**豆包语音 → 网关 ASR**。网关候选的 ``ready`` 只代表「上游快照里有
      这个模型名」，不代表本地网关真的给它绑了可用渠道（渠道 ``models`` 列表由
      provisioner 按注册表投影，ASR 模型不在其中），未开通时 multipart 上传会吃到
      503 ``model_not_found``；豆包直连凭证就绪时优先走它更稳。

    返回 ``resolve_douyin_asr_candidate`` 同形状的候选 dict；返回 ``None`` 表示当前
    没有可用通道，由调用方决定跳过（best-effort）还是报错（必须成功的链路）。
    """

    def _allowed(kind: str) -> bool:
        if kind == "funasr":
            return False
        if kind == "doubao_asr" and is_organization:
            return False
        return True

    clean = str(asr_model or "").strip()
    if clean:
        try:
            candidate = resolve_douyin_asr_candidate(clean)
        except ValueError:
            logger.warning("ASR 候选 %r 无法识别，改用自动选择", clean)
        else:
            if _allowed(str(candidate.get("kind") or "")):
                return candidate
            logger.info(
                "ASR 候选 %s 不适用于本地音频（组织任务只走网关），改用自动选择",
                candidate.get("display") or candidate.get("kind"),
            )

    try:
        options = list_douyin_asr_models()
    except Exception:  # noqa: BLE001 - 候选清单读不到时按「无通道」降级
        logger.warning("ASR 候选清单读取失败", exc_info=True)
        return None
    usable = [
        option
        for option in options
        if option.get("ready") and _allowed(str(option.get("kind") or ""))
    ]
    for prefer in ("doubao_asr", "gateway"):
        for option in usable:
            if str(option.get("kind") or "") != prefer:
                continue
            try:
                return resolve_douyin_asr_candidate(str(option.get("id") or ""))
            except ValueError:
                continue
    return None


# ============================================================
# 通道 ①：网关 ASR（OpenAI 兼容 /audio/transcriptions，multipart）
# ============================================================


class GatewayAsrUnavailable(RuntimeError):
    """环境缺失（网关地址未配置），不是请求失败。

    不参与下面「网关 ASR 转写失败 · …」的二次包装（那会把可执行提示变成
    ``请求未发出或传输异常：RuntimeError``），直接透传给调用方。
    """


def _newapi_transcription_endpoint(base_url: str | None = None, *, model: str = "") -> str:
    if base_url is None:
        from novelvideo.config import get_newapi_runtime_credentials

        _api_key, resolved_base_url = get_newapi_runtime_credentials()
    else:
        resolved_base_url = base_url
    # [aipdd fork delta req41] 不再兜底到 ``http://localhost:3000/v1``：网关不在「本
    # 机回环 3000」是常态（容器内网、独立主机、反代域名），猜一个只会让请求打到
    # 不存在的监听上，报出来的是无意义的 connect refused。没地址就是没配置。
    clean = str(resolved_base_url or "").strip().rstrip("/")
    if not clean:
        raise GatewayAsrUnavailable(
            "未配置模型网关地址，无法走网关 ASR：请到「设置 › 模型」连接网关，"
            "或在 .env 设 NEWAPI_BASE_URL=<网关地址>。"
        )
    endpoint = clean
    # [aipdd fork delta req41] 转写路径照渠道目录：条目声明了什么就拼什么（AIPDD 写了
    # ``audio/transcriptions``）；目录没说话才按通用 OpenAI 兼容路径拼。
    from novelvideo.model_gateway_settings import catalog_endpoint_path
    from novelvideo.upstream_catalog import EP_AUDIO_TRANSCRIPTIONS

    suffix = (
        catalog_endpoint_path(str(model or ""), EP_AUDIO_TRANSCRIPTIONS)
        or EP_AUDIO_TRANSCRIPTIONS
    )
    if not endpoint.endswith(f"/{suffix}"):
        endpoint = f"{endpoint}/{suffix}"
    return endpoint


def _audio_upload_content_type(audio_path: Path) -> str:
    return {
        ".wav": "audio/wav",
        ".mp3": "audio/mpeg",
        ".m4a": "audio/mp4",
        ".mp4": "video/mp4",
        ".aac": "audio/aac",
        ".ogg": "audio/ogg",
    }.get(audio_path.suffix.lower(), "application/octet-stream")


def _gateway_transcription_detail(exc: BaseException) -> str:
    """把上游报错包成可诊断摘要：HTTP 状态 + 上游 message（不含任何凭据）。

    背景：以前所有异常都被压成一句 ``NewAPI audio transcription failed``（并
    ``from None`` 掉因果链），用户和运维看不出是「网关没给该模型绑可用渠道」还是
    「鉴权失败 / 上游超时」，也没法行动。这里只取状态码与上游错误文本（截断），
    并对最常见、最可行动的 ``model_not_found`` / "No available channel" 给出指引。
    """

    response = getattr(exc, "response", None)
    status = getattr(response, "status_code", "?")
    payload: Any = None
    try:
        payload = response.json()  # type: ignore[union-attr]
    except Exception:  # noqa: BLE001 - 非 JSON 响应体回落到原始文本
        payload = None
    err_code = ""
    if isinstance(payload, dict):
        err = payload.get("error")
        if isinstance(err, dict):
            err_code = str(err.get("code") or "").strip()
            message = str(err.get("message") or "").strip()
        else:
            message = str(err or "").strip()
    else:
        message = str(getattr(response, "text", "") or "").strip()
    message = " ".join(message.split())[:240]
    detail = f"HTTP {status}" + (f" · {message}" if message else "")
    low = f"{message} {err_code}".lower()
    if "model_not_found" in low or "no available channel" in low:
        detail += (
            "（网关侧没有该 ASR 模型的可用渠道：请在「设置 → 模型网关」为对应渠道开通该模型，"
            "或改选已就绪的其它 ASR 候选）"
        )
    return detail


async def _transcribe_via_gateway(
    *,
    audio_path: Path,
    model: str,
    job_id: str,
    egress_context: TrustedEgressContext | None = None,
    timeout_seconds: float = 900.0,
) -> str:
    """POST ``{网关}/audio/transcriptions``（multipart 上传本地音频）取回文本。

    出网契约与 ``audio_node._write_newapi_audio_speech`` 共用同一单点实现
    （``begin_trusted_audio_operation``）：有受信上下文时非组织先于一切拒绝
    （``ORG_EGRESS_DENIED``）；传输前异常 ``reject`` 租约、传输后异常
    ``mark_unknown``、成功后 ``complete``；无受信上下文回落平台运行期凭证。
    返回可能为空字符串（上游转写无内容），由调用方决定兜底。
    """

    import httpx

    from novelvideo.egress_context import ambient_organization_egress_context
    from novelvideo.generators.audio_gateway import begin_trusted_audio_operation
    from novelvideo.generators.tts_generator import (
        complete_audio_operation,
        mark_audio_operation_unknown,
        reject_audio_operation,
    )

    lease = None
    if egress_context is None:
        egress_context = ambient_organization_egress_context()
    if egress_context is not None:
        session = await begin_trusted_audio_operation(
            egress_context,
            capability="audio.asr.gateway",
            business_task_id=f"freezone-douyin-copywrite:{job_id}",
            request={
                "model": str(model or "").strip(),
                "audio": audio_path.name,
                "audio_size": audio_path.stat().st_size if audio_path.exists() else 0,
            },
        )
        if session.denial_code is not None:
            raise RuntimeError(session.denial_code)
        lease = session.lease
        credential = session.credential
        key = credential.api_key if credential else ""
        resolved_base_url = credential.base_url if credential else None
    else:
        from novelvideo.config import get_newapi_runtime_credentials

        key, resolved_base_url = get_newapi_runtime_credentials()
    key = str(key or "").strip()
    if not key:
        raise RuntimeError("NEWAPI_API_KEY is required for gateway ASR")

    transport_started = False
    try:
        endpoint = _newapi_transcription_endpoint(resolved_base_url, model=model)
        with audio_path.open("rb") as handle:
            async with httpx.AsyncClient(
                timeout=timeout_seconds, follow_redirects=True
            ) as client:
                transport_started = True
                response = await client.post(
                    endpoint,
                    headers={"Authorization": f"Bearer {key}"},
                    data={"model": str(model or "").strip()},
                    files={
                        "file": (
                            audio_path.name,
                            handle,
                            _audio_upload_content_type(audio_path),
                        )
                    },
                )
        response.raise_for_status()
        payload = response.json()
        text = ""
        if isinstance(payload, dict):
            text = str(payload.get("text") or "").strip()
        if lease is not None:
            await complete_audio_operation(lease, result_ref="audio:newapi:transcription")
        return text
    except BaseException as exc:
        if lease is not None:
            if transport_started:
                await mark_audio_operation_unknown(lease)
            else:
                await reject_audio_operation(lease)
        if isinstance(exc, GatewayAsrUnavailable):
            # 环境未配置：保留可执行提示，不伪装成一次失败的转写请求。
            raise
        if isinstance(exc, Exception):
            import httpx

            detail = (
                _gateway_transcription_detail(exc)
                if isinstance(exc, httpx.HTTPStatusError)
                else f"请求未发出或传输异常：{type(exc).__name__}"
            )
            logger.error("gateway ASR transcription failed: %s", detail)
            raise RuntimeError(f"网关 ASR 转写失败 · {detail}") from None
        raise


# ============================================================
# 通道 ②：FunASR（aipdd shared-tasks 异步协议直连）
# ============================================================


def _funasr_platform_config() -> tuple[str, str]:
    """FunASR 平台根地址与凭据：aipdd 渠道真源的 baseUrl（去 /v1 尾缀）与 upstreamKey。

    与 ``media_model_discovery`` 直连渠道拉 ``/models`` 同一先例：``shared-tasks``
    是平台异步任务协议，不经 OpenAI 兼容网关，直连渠道配置。渠道缺配置时抛错。
    """

    from novelvideo.model_gateway_settings import get_newapi_provider_channel

    channel = get_newapi_provider_channel("aipdd") or {}
    key = str(channel.get("upstreamKey") or "").strip()
    base_url = str(channel.get("baseUrl") or "").strip().rstrip("/")
    # 渠道 baseUrl 是 OpenAI 兼容根（如 https://api.aipdd.work/v1）；shared-tasks
    # 挂在站点根上，去掉 /v1 尾缀。
    if base_url.endswith("/v1"):
        base_url = base_url[: -len("/v1")]
    if not key or not base_url:
        raise RuntimeError(
            "未配置 aipdd 渠道（baseUrl/upstreamKey），无法使用 FunASR 转写"
        )
    return base_url, key


async def _shared_tasks_request(
    client,
    base_url: str,
    method: str,
    path: str,
    *,
    headers: dict[str, str],
    json: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """shared-tasks 统一信封请求（``{code, message, data}``，code != 0 即错）。"""

    response = await client.request(
        method, f"{base_url}{path}", headers=headers, json=json
    )
    if response.status_code == 401:
        raise RuntimeError("FunASR 接口鉴权失败（401）：请检查 aipdd 渠道 upstreamKey")
    if response.status_code >= 400:
        raise RuntimeError(
            f"FunASR 接口错误（{response.status_code}）: {response.text[:300]}"
        )
    try:
        payload = response.json()
    except Exception:
        raise RuntimeError(
            f"FunASR 接口响应不是合法 JSON: {response.text[:300]}"
        ) from None
    if not isinstance(payload, dict) or payload.get("code") != 0:
        message = payload.get("message", payload) if isinstance(payload, dict) else payload
        raise RuntimeError(f"FunASR 接口返回错误: {message}")
    data = payload.get("data")
    return data if isinstance(data, dict) else {}


async def _transcribe_via_funasr(
    *,
    audio_url: str,
    job_id: str,
    task_name: str = "",
    timeout_seconds: float = 1800.0,
) -> str:
    """FunASR 完整流程：创建任务 → 轮询 → 取结果（移植参考包协议）。

    平台无公开文件上传接口，``audio`` 只能是公网音频 URL（原声音频直链）。
    返回可能为空字符串（无语音内容），由调用方决定兜底。
    """

    import httpx

    base_url, api_key = _funasr_platform_config()
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    create_body: dict[str, Any] = {
        "requestId": f"funasr-{time.strftime('%Y%m%d%H%M%S')}-{uuid.uuid4().hex[:8]}",
        "taskName": task_name or "抖音视频转写",
        "taskTypeCode": FUNASR_TASK_TYPE_CODE,
        "input": {
            "audio": audio_url,
            "language": "auto",
            "outputFormats": ["json"],
            "enableEmotionLabels": True,
            "enableSpeakerDiarization": True,
            "maxDurationSeconds": 3600,
        },
    }
    async with httpx.AsyncClient(timeout=60.0, follow_redirects=True) as client:
        task = await _shared_tasks_request(
            client,
            base_url,
            "POST",
            "/shared-tasks/tasks",
            headers=headers,
            json=create_body,
        )
        task_id = str(task.get("id") or task.get("taskId") or "").strip()
        if not task_id:
            raise RuntimeError("FunASR 创建任务未返回任务号")
        deadline = time.monotonic() + timeout_seconds
        poll_after_ms = int(task.get("pollAfterMs") or 5000)
        logger.info(
            "douyin copywrite: funasr task %s created (job_id=%s)", task_id, job_id
        )
        while time.monotonic() < deadline:
            await asyncio.sleep(max(poll_after_ms, 1000) / 1000.0)
            status = await _shared_tasks_request(
                client,
                base_url,
                "GET",
                f"/shared-tasks/tasks/{task_id}",
                headers=headers,
            )
            if status.get("resultReady"):
                result = await _shared_tasks_request(
                    client,
                    base_url,
                    "GET",
                    f"/shared-tasks/tasks/{task_id}/result",
                    headers=headers,
                )
                output = result.get("output") if isinstance(result.get("output"), dict) else {}
                return str(output.get("text") or "").strip()
            state = str(status.get("status") or "")
            if state in ("FAILED", "CANCELED", "EXPIRED"):
                raise RuntimeError(f"FunASR 任务进入终态 {state}")
        raise RuntimeError("FunASR 转写超时，请稍后重试")


async def _resolve_local_audio(
    *,
    music_url: str,
    video_url: str,
    work_dir: Path,
) -> Path:
    """本地音频：优先原声直链下载，失败或无原声时回退「下载视频 → ffmpeg 抽 wav」。"""

    audio_path: Path | None = None
    if music_url:
        try:
            audio_path = await _download_binary(music_url, work_dir / "audio.mp3")
        except Exception as exc:  # noqa: BLE001 - 直链可能过期，回退视频抽取
            logger.warning(
                "douyin copywrite: music audio download failed, fallback to video: %s",
                exc,
            )
            audio_path = None
    if audio_path is None:
        if not video_url:
            raise RuntimeError("抖音详情未返回可用媒体地址（视频可能已下架或受限）")
        video_path = await _download_binary(video_url, work_dir / "source.mp4")
        audio_path = await _extract_audio_16k_wav(video_path, work_dir / "audio.wav")
    return audio_path


# ============================================================
# 通道 ③：豆包语音 ASR（火山 openspeech v3 直连）
# ============================================================


def _doubao_asr_credentials() -> str:
    """豆包语音凭证：只读设置数据库（volcengine 渠道 settings.asr）。

    豆包语音（火山 openspeech）统一以**单个 API Key** 鉴权——官方《API Key
    使用》文档明确「在任意接口中，填入 header 即可，不用填写 appid」，请求头
    带 ``X-Api-Key: <api-key>``（含录音文件识别 submit/query 全部接口）。该
    API Key 在豆包语音控制台「API Key 管理」获取，与方舟/网关 API Key 不通用。
    设置页 VolcEngine 卡的语音识别区把它存进渠道 settings.asr（settings.db 单
    真源，不走环境变量）。未配置时给出可操作的报错。
    """

    from novelvideo.model_gateway_settings import get_newapi_provider_channel

    asr: dict[str, Any] = {}
    try:
        channel = get_newapi_provider_channel("volcengine") or {}
        settings = channel.get("settings")
        if isinstance(settings, dict) and isinstance(settings.get("asr"), dict):
            asr = settings["asr"]
    except Exception:  # noqa: BLE001 - 渠道配置读不到时按未配置处理
        asr = {}
    api_key = str(asr.get("apiKey") or "").strip()
    if not api_key:
        raise RuntimeError(
            "未配置豆包语音凭证：请到设置 → 模型与渠道 → VolcEngine 卡的语音识别区"
            "填写 API Key（豆包语音控制台「API Key 管理」获取，保存在设置数据库，"
            "与方舟/网关 API Key 不通用）"
        )
    return api_key


def _doubao_audio_payload(
    *, audio_url: str, audio_path: Path | None
) -> dict[str, str]:
    """豆包语音音频参数：公网直链优先（零下载），否则本地文件 base64。

    直链无后缀时按抖音原声惯例默认 mp3；本地文件按其真实后缀声明 format。
    """

    clean_url = str(audio_url or "").strip()
    if clean_url:
        suffix = Path(urlsplit(clean_url).path).suffix.lstrip(".").lower()
        return {"format": suffix or "mp3", "url": clean_url}
    if audio_path is not None and audio_path.exists():
        suffix = audio_path.suffix.lstrip(".").lower()
        return {
            "format": suffix or "wav",
            "data": base64.b64encode(audio_path.read_bytes()).decode("ascii"),
        }
    raise RuntimeError("豆包语音转写缺少音频源（公网直链与本地音频均不可用）")


def _doubao_status_code(response: Any) -> str:
    return str(response.headers.get("X-Api-Status-Code") or "").strip()


def _doubao_result_text(response: Any, *, stage: str) -> str:
    """校验 openspeech 状态码并取回文本；20000000 为成功，其余给可读报错。"""

    status_code = _doubao_status_code(response)
    if status_code != "20000000":
        # [aipdd fork delta] openspeech 的业务状态码在响应头；请求在网关解析阶段就被
        # 拒时（如 45000000）响应头里没有该字段，只报「N/A」无法排查，补上 HTTP 状态码。
        label = status_code or f"N/A/HTTP {getattr(response, 'status_code', 'N/A')}"
        raise RuntimeError(
            f"豆包语音识别{stage}失败（X-Api-Status-Code={label}）: "
            f"{response.text[:300]}"
        )
    try:
        payload = response.json()
    except Exception:
        raise RuntimeError(
            f"豆包语音识别响应不是合法 JSON: {response.text[:300]}"
        ) from None
    result = payload.get("result") if isinstance(payload, dict) else None
    if isinstance(result, dict):
        return str(result.get("text") or "").strip()
    return ""


async def _transcribe_via_doubao_asr(
    *,
    variant: str,
    audio_url: str,
    audio_path: Path | None,
    job_id: str,
    timeout_seconds: float = 1800.0,
) -> str:
    """豆包语音录音文件识别：标准版 submit/query 轮询；极速版单请求同步返回。

    v3 协议要点：X-Api-Key（单个 API Key）/ X-Api-Resource-Id 恒定，
    submit 额外带 ``X-Api-Sequence: -1``；submit 与 query 用同一 Request-Id。
    query 的任务身份完全靠 header 里的 Request-Id，但**请求体仍必须是一个空 JSON
    对象** ``{}``——不带 body 时网关反序列化直接失败（45000000 unexpected end of
    JSON input），详见下面轮询处的注释。
    返回可能为空字符串（无语音内容），由调用方决定兜底。
    """

    import httpx

    spec = _DOUBAO_ASR_VARIANTS.get(str(variant or "").strip())
    if not spec:
        raise ValueError(f"未知的豆包语音识别型号: {variant}")
    api_key = _doubao_asr_credentials()
    payload = _doubao_audio_payload(audio_url=audio_url, audio_path=audio_path)
    request_id = f"doubao-asr-{uuid.uuid4().hex}"
    base_headers = {
        "X-Api-Key": api_key,
        "X-Api-Resource-Id": spec["resource_id"],
        "X-Api-Request-Id": request_id,
    }
    body: dict[str, Any] = {
        "user": {"uid": "novelvideo"},
        "audio": payload,
        "request": {
            "model_name": "bigmodel",
            "enable_itn": True,
            "enable_punc": True,
        },
    }

    async with httpx.AsyncClient(timeout=120.0, follow_redirects=True) as client:
        if spec.get("recognize_path"):
            # 极速版（Lite）：单请求同步返回。
            response = await client.post(
                f"{DOUBAO_ASR_ENDPOINT}{spec['recognize_path']}",
                headers=base_headers,
                json=body,
            )
            return _doubao_result_text(response, stage="（极速版）")
        # 标准版：submit（X-Api-Sequence: -1）ack → query 轮询结果。
        submit = await client.post(
            f"{DOUBAO_ASR_ENDPOINT}{spec['submit_path']}",
            headers={**base_headers, "X-Api-Sequence": "-1"},
            json=body,
        )
        submit_status = _doubao_status_code(submit)
        if submit_status != "20000000":
            raise RuntimeError(
                f"豆包语音识别提交失败（X-Api-Status-Code={submit_status or 'N/A'}）: "
                f"{submit.text[:300]}"
            )
        deadline = time.monotonic() + timeout_seconds
        logger.info(
            "douyin copywrite: doubao asr %s submitted (job_id=%s)", variant, job_id
        )
        while time.monotonic() < deadline:
            await asyncio.sleep(_DOUBAO_ASR_POLL_SECONDS)
            # [aipdd fork delta] query 必须带一个空的 JSON body（``{}``）：openspeech
            # 网关在鉴权前会先反序列化请求体，不带 body 时服务端 json.Unmarshal("")
            # 失败，返回 45000000「unexpected end of JSON input」且响应头里没有
            # X-Api-Status-Code（用户侧只看到 N/A）。任务由 header 的
            # X-Api-Request-Id 定位，body 内容不参与，所以传空对象即可。
            query = await client.post(
                f"{DOUBAO_ASR_ENDPOINT}{spec['query_path']}",
                headers=base_headers,
                json={},
            )
            status_code = _doubao_status_code(query)
            # 20000001/20000002 = 处理中，继续轮询；其余进结果校验（非 20000000 报错）。
            if status_code in {"20000001", "20000002"}:
                continue
            return _doubao_result_text(query, stage="查询")
        raise RuntimeError("豆包语音转写超时，请稍后重试")


# ============================================================
# 主叶函数（runner 经 _call_freezone_leaf 调用）
# ============================================================


@dataclass(frozen=True, slots=True)
class DouyinCopywriteResult:
    text_path: Path
    text: str
    source: str
    model: str
    video_id: str
    title: str
    author: str
    duration_seconds: int


def _render_copywrite_file(
    *,
    video_id: str,
    title: str,
    author: str,
    stats: dict[str, Any],
    source_label: str,
    model_label: str,
    text: str,
    detail_label: str = "",
) -> str:
    """产物 txt：元信息头 + 正文（参考包 save_transcript_to_file 的口径）。"""

    lines = [f"视频ID: {video_id}"]
    if title:
        lines.append(f"标题: {title}")
    if author:
        lines.append(f"作者: {author}")
    stats_line = (
        f"互动: 点赞{int(stats.get('digg_count') or 0):,} "
        f"评论{int(stats.get('comment_count') or 0):,} "
        f"收藏{int(stats.get('collect_count') or 0):,} "
        f"分享{int(stats.get('share_count') or 0):,}"
    )
    lines.append(stats_line)
    lines.append(f"文案来源: {source_label}")
    if detail_label:
        # [aipdd fork delta] 详情从哪条链路拿的（HTTP/Cookie 还是 CDP）：排查时最先要看的字段。
        lines.append(f"详情来源: {detail_label}")
    if model_label:
        lines.append(f"模型: {model_label}")
    lines.append("=" * 50)
    lines.append("【口播文案】")
    lines.append(text)
    return "\n".join(lines) + "\n"


async def run_freezone_douyin_copywrite(
    *,
    project_dir: Path,
    job_id: str,
    video: str,
    model: str,
    egress_context: TrustedEgressContext | None = None,
) -> DouyinCopywriteResult:
    """抖音文案提取执行叶：解析链接 → 拦截详情 → 音频 → 用户指定 ASR 转写。

    出网说明（``FREEZONE_LEAF_EGRESS`` 记为 NETWORK）：

    - 抖音详情/媒体：直连公开网页与 CDN，无凭据、不涉及组织数据外发（只传用户
      显式提供的视频 ID），个人与组织均放行；
    - 网关 ASR：经 ``begin_trusted_audio_operation`` 出网，组织任务走组织凭据；
    - FunASR / 豆包语音直连：使用平台侧配置的凭据，组织任务 fail-closed（与
      MediaKit 同一取舍：不得用平台凭据直连第三方主机），须改选网关 ASR 模型。
    """

    candidate = resolve_douyin_asr_candidate(model)
    is_organization = (
        egress_context is not None and bool(egress_context.is_organization)
    )
    if candidate["kind"] in {"funasr", "doubao_asr"} and is_organization:
        raise RuntimeError(
            "ORG_EGRESS_DENIED: "
            f"{candidate.get('display') or candidate['model']} 直连使用平台侧凭据，"
            "组织任务请选择网关 ASR 模型"
        )

    video_id = await resolve_douyin_video_id(video)
    detail, detail_source = await _fetch_douyin_detail(video_id)
    info = parse_aweme_detail(detail)

    outputs = outputs_dir(project_dir, _TASK_TYPE)
    work_dir = outputs / job_id
    work_dir.mkdir(parents=True, exist_ok=True)

    music_url = str(info.get("music_audio_url") or "")
    video_url = str(info.get("video_url") or "")
    title = str(info.get("desc") or "")
    task_name = f"抖音视频转写 {info.get('aweme_id') or title[:20]}"

    display = candidate.get("display") or candidate["model"]
    detail_label = _DETAIL_SOURCE_LABELS.get(detail_source, detail_source)
    if candidate["kind"] == "funasr":
        # 平台只接受公网音频 URL（无公开上传接口）：原声音频直链是唯一通道。
        if not music_url:
            raise RuntimeError(
                "该视频没有可用的原声音频直链（可能带背景音乐），FunASR 无法转写；"
                "请改选网关 ASR 模型（本地音频可直接上传）"
            )
        text = await _transcribe_via_funasr(
            audio_url=music_url, job_id=job_id, task_name=task_name
        )
        source_label = f"语音识别（{display}）"
    elif candidate["kind"] == "doubao_asr":
        # 豆包语音：公网直链优先（零下载）；无直链回落本地音频 base64 提交。
        audio_path: Path | None = None
        if not music_url:
            audio_path = await _resolve_local_audio(
                music_url=music_url, video_url=video_url, work_dir=work_dir
            )
        text = await _transcribe_via_doubao_asr(
            variant=candidate["model"],
            audio_url=music_url,
            audio_path=audio_path,
            job_id=job_id,
        )
        source_label = f"语音识别（{display}）"
    else:
        # 网关通道：本地音频 multipart 上传，任意视频都能自动化。
        # 音频源优先原声直链（无防盗链、体积小）；下载失败或无原声时落到
        # 「下载视频 → ffmpeg 抽 16k 单声道 wav」。
        from novelvideo.model_gateway_settings import provider_spot_model

        audio_path = await _resolve_local_audio(
            music_url=music_url, video_url=video_url, work_dir=work_dir
        )
        text = await _transcribe_via_gateway(
            audio_path=audio_path,
            # [aipdd fork delta] 拍平后网关调用名用复合名（渠道::模型）：同名模型
            # 可能被多渠道拥有、不再注册裸名，只有复合名是稳定可达的调用名。
            model=provider_spot_model(candidate["provider"], candidate["model"]),
            job_id=job_id,
            egress_context=egress_context,
        )
        source_label = f"语音识别（{display}）"

    clean_text = str(text or "").strip()
    source = "asr"
    if not clean_text:
        # ASR 无结果时回落视频内置字幕（参考包的第一层数据源），仍是空才报错。
        subtitle_text = await _fetch_subtitle_text(info.get("subtitle_infos"))
        if subtitle_text and len(subtitle_text) > 10:
            clean_text = subtitle_text
            source = "subtitle"
            source_label = "视频内置字幕"
    if not clean_text:
        raise RuntimeError(
            "未能提取到文案：ASR 未识别到有效语音内容，且该视频没有可用的内置字幕"
        )

    text_path = outputs / f"{job_id}.txt"
    text_path.write_text(
        _render_copywrite_file(
            video_id=video_id,
            title=title,
            author=str(info.get("author") or ""),
            stats=info.get("stats") or {},
            source_label=source_label,
            model_label=display if source == "asr" else "",
            text=clean_text,
            detail_label=detail_label,
        ),
        encoding="utf-8",
    )
    return DouyinCopywriteResult(
        text_path=text_path,
        text=clean_text,
        source=source,
        model=candidate["id"],
        video_id=video_id,
        title=title,
        author=str(info.get("author") or ""),
        duration_seconds=int(info.get("duration_seconds") or 0),
    )