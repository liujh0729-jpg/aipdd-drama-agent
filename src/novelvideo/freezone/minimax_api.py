"""MiniMax 上游直连客户端：渠道解析 + 双错误形态 + 二进制落盘 + 素材上传。

[aipdd fork delta req46] 本模块只负责「怎么和 MiniMax 说话」，不碰业务参数
（模式→字段在 :mod:`novelvideo.freezone.minimax_music` 收口）。M5 视频、M6 语音
复用同一套解析与出网口径，避免三条链路各写一份错误处理。

为什么需要独立一层（实测契约）：

- **站点与 Key 不通用**：海外与国内是两个渠道（provider ``minimax`` /
  ``minimax_cn``），各自的 Base URL 与 API Key 都存在设置库渠道行里
  （``upstreamKey`` + ``baseUrl``，读取期已归一补齐 ``/v1``）。地址可以在设置页
  改，所以这里只从渠道解析，不写死主机名。
- **错误有两种形态**：V2 协议（视频）返回真实 HTTP 状态码 + OpenAI 风格
  ``error.message``；V1 协议（音乐 / 语音）**HTTP 恒 200**，失败信息在响应体的
  ``base_resp.status_code / status_msg``。只按 HTTP 状态判断会把「鉴权失败、
  余额不足、参数错误」全读成成功。两种形态在这里一次消化，统一抛
  :class:`MiniMaxError`，并把上游原文带进 message——延续「失败提示必须是详细
  诊断」的口径。
- **音频是 hex**：``data.audio`` 是十六进制字符串而不是 URL/base64，落盘前要
  解码；解码失败也必须报清是哪一段不是合法 hex，而不是抛 ``ValueError`` 让
  任务日志变成无信息量的一行。

出网口径：直连使用**平台侧配置的渠道凭据**，与火山 MediaKit / 豆包语音同类
（见 :mod:`novelvideo.freezone.douyin_copywrite`）。因此组织任务
（``egress_context.is_organization``）一律 fail-closed：不得拿平台 Key 去连
第三方主机，报 ``ORG_EGRESS_DENIED`` 并说明该链路没有网关替代。
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx

from novelvideo.egress_context import TrustedEgressContext

logger = logging.getLogger(__name__)

PROVIDER_MINIMAX = "minimax"
PROVIDER_MINIMAX_CN = "minimax_cn"
MINIMAX_PROVIDERS: tuple[str, ...] = (PROVIDER_MINIMAX, PROVIDER_MINIMAX_CN)

# 官方文档 servers 的两个站点，另加两个同栈可达域名：Base URL 在渠道卡内可改，
# 命中任一都按 MiniMax 协议处理（M5 的协议分支判定与渠道 provider 判定并列）。
MINIMAX_HOST_HINTS: tuple[str, ...] = (
    "minimax.io",
    "minimax.cn",
    "minimaxi.com",
    "minimax.chat",
)

# 端点写成完整路径后缀：调用方只拼 ``api_root + PATH_*``，避免「谁负责 /v1」在
# 两条链路里漂移。以 ``/`` 开头的字面量也不会被模型名棘轮误伤。
PATH_MUSIC_GENERATION = "/v1/music_generation"
PATH_MUSIC_COVER_PREPROCESS = "/v1/music_cover_preprocess"
PATH_T2A = "/v1/t2a_v2"
PATH_FILE_UPLOAD = "/v1/files/upload"
PATH_VIDEO_GENERATION_V2 = "/v2/video_generation"
PATH_VIDEO_QUERY_V2 = "/v2/query/video_generation"

DEFAULT_TIMEOUT_SECONDS = 600.0
UPLOAD_TIMEOUT_SECONDS = 300.0
DOWNLOAD_TIMEOUT_SECONDS = 900.0

CHANNEL_NOT_CONFIGURED_MESSAGE = (
    "未配置 MiniMax 渠道：请到设置 → 模型与渠道，在 MiniMax（或 MiniMax 国内站）"
    "渠道卡里填写该站点的 API Key 并保存；两个站点的 Key 不通用，需用哪个站点就配哪个。"
)


class MiniMaxError(RuntimeError):
    """上游调用失败：message 里必须带上上游原话（状态码 + status_msg / error.message）。"""

    def __init__(
        self,
        message: str,
        *,
        status_code: int = 0,
        http_status: int = 0,
        endpoint: str = "",
    ) -> None:
        super().__init__(message)
        self.status_code = int(status_code or 0)
        self.http_status = int(http_status or 0)
        self.endpoint = str(endpoint or "")


class MiniMaxChannelUnavailable(RuntimeError):
    """渠道没配置 = 环境缺失，不是生成失败。

    不参与失败归一化：可操作的「去哪配」文案必须原样透到界面，否则会被上游错误
    包装器归成一句没有信息量的「调用失败」。
    """


@dataclass(frozen=True)
class MiniMaxChannel:
    """一次直连所需的渠道事实：站点根地址 + 该站点 Key + 渠道扩展配置。"""

    provider: str
    api_root: str
    api_key: str
    base_url: str
    settings: dict[str, Any]


def is_minimax_provider(provider: Any) -> bool:
    return str(provider or "").strip().lower() in MINIMAX_PROVIDERS


def is_minimax_host(base_url: Any) -> bool:
    host = str(urlsplit(str(base_url or "").strip()).hostname or "").lower()
    if not host:
        return False
    return any(host == hint or host.endswith(f".{hint}") for hint in MINIMAX_HOST_HINTS)


def api_root_from_base_url(base_url: str) -> str:
    """渠道 ``baseUrl`` → 站点根：剥掉读取期归一时补上的 ``/v1`` 尾巴。

    幂等：没带版本后缀的原样返回（末尾斜杠一律去掉）。业务端点自带
    ``/v1`` 或 ``/v2``，所以拼接前必须回到根，否则会出现 ``/v1/v1/...``。
    """

    clean = str(base_url or "").strip().rstrip("/")
    lowered = clean.lower()
    for suffix in ("/v1", "/v2", "/api/v3", "/chat/completions"):
        if lowered.endswith(suffix):
            return clean[: -len(suffix)].rstrip("/")
    return clean


def configured_minimax_providers() -> list[str]:
    """已填 Key 的 MiniMax 渠道（按登记顺序）。前端只在两条都配置时给站点切换。"""

    from novelvideo.model_gateway_settings import get_newapi_provider_channel

    ready: list[str] = []
    for provider in MINIMAX_PROVIDERS:
        try:
            channel = get_newapi_provider_channel(provider) or {}
        except Exception:  # noqa: BLE001 - 设置库不可读时按「未配置」处理，交给下面报文案
            continue
        if str(channel.get("upstreamKey") or "").strip():
            ready.append(provider)
    return ready


def resolve_minimax_channel(provider: str = "") -> MiniMaxChannel:
    """provider → 渠道凭据；不传时按已配置渠道唯一性兜底。

    两个站点都配置且没显式指定 provider 时取登记顺序第一条（海外站），与设置页
    「默认生成模型」的渠道归属保持一致：面板里选了哪个渠道的模型，payload 就带
    哪个 provider，真正需要区分站点时前端一定会传。
    """

    from novelvideo.model_gateway_settings import get_newapi_provider_channel

    wanted = str(provider or "").strip().lower()
    if wanted and wanted not in MINIMAX_PROVIDERS:
        raise MiniMaxError(
            f"未知的 MiniMax 渠道: {wanted}（可用: {', '.join(MINIMAX_PROVIDERS)}）"
        )
    candidates = [wanted] if wanted else list(MINIMAX_PROVIDERS)
    for name in candidates:
        try:
            channel = get_newapi_provider_channel(name) or {}
        except Exception:  # noqa: BLE001 - 读库异常按未配置处理
            channel = {}
        api_key = str(channel.get("upstreamKey") or "").strip()
        base_url = str(channel.get("baseUrl") or "").strip()
        if not api_key or not base_url:
            continue
        settings = channel.get("settings")
        return MiniMaxChannel(
            provider=name,
            api_root=api_root_from_base_url(base_url),
            api_key=api_key,
            base_url=base_url,
            settings=settings if isinstance(settings, dict) else {},
        )
    if wanted:
        raise MiniMaxChannelUnavailable(
            f"MiniMax 渠道（{wanted}）还没有保存 API Key 或 Base URL："
            + CHANNEL_NOT_CONFIGURED_MESSAGE
        )
    raise MiniMaxChannelUnavailable(CHANNEL_NOT_CONFIGURED_MESSAGE)


def minimax_channel_extra(channel: MiniMaxChannel) -> dict[str, Any]:
    """渠道级扩展配置 ``settings.minimax``（语音默认音色等），形状对齐 volcengine 的 asr 区。"""

    raw = channel.settings.get("minimax") if isinstance(channel.settings, dict) else None
    return raw if isinstance(raw, dict) else {}


def _parse_json_object(response: httpx.Response) -> dict[str, Any]:
    try:
        payload = response.json()
    except Exception:  # noqa: BLE001 - 上游给 HTML/纯文本时也要能报出原文
        raise MiniMaxError(
            f"MiniMax 响应不是合法 JSON（HTTP {response.status_code}）: "
            f"{response.text[:300]}",
            http_status=int(response.status_code),
        ) from None
    return payload if isinstance(payload, dict) else {"_raw": payload}


def _failure_from_payload(
    payload: dict[str, Any], *, http_status: int, endpoint: str
) -> MiniMaxError | None:
    """把两种错误形态归一成一个 :class:`MiniMaxError`（没失败返回 ``None``）。

    判定顺序不能换：V1 的音乐 / 语音即使业务失败也回 HTTP 200，所以
    ``base_resp`` 必须先于 HTTP 状态看；V2 的错误则是真实 4xx/5xx + ``error``。
    """

    base_resp = payload.get("base_resp")
    if isinstance(base_resp, dict):
        try:
            code = int(base_resp.get("status_code") or 0)
        except (TypeError, ValueError):
            code = -1
        if code != 0:
            message = str(base_resp.get("status_msg") or "").strip()
            return MiniMaxError(
                f"MiniMax 上游返回错误（base_resp.status_code={code}）："
                f"{message or '上游未给出 status_msg，请核对 API Key 与账号权限/余额'}",
                status_code=code,
                http_status=http_status,
                endpoint=endpoint,
            )
    error = payload.get("error")
    if isinstance(error, dict) and (error.get("message") or error.get("code")):
        message = str(error.get("message") or "").strip()
        code_raw = error.get("code")
        try:
            code = int(code_raw or 0)
        except (TypeError, ValueError):
            code = 0
        return MiniMaxError(
            f"MiniMax 上游返回错误（HTTP {http_status}）：{message or str(code_raw or '')}",
            status_code=code,
            http_status=http_status,
            endpoint=endpoint,
        )
    if http_status and http_status >= 400:
        # 状态码说失败、body 里却没有可读信息：把原文截出来，别把诊断丢了。
        return MiniMaxError(
            f"MiniMax 上游返回 HTTP {http_status}（{endpoint}），响应体没有可读错误字段",
            http_status=http_status,
            endpoint=endpoint,
        )
    return None


def _ensure_business_ok(
    payload: dict[str, Any], *, http_status: int, endpoint: str
) -> dict[str, Any]:
    failure = _failure_from_payload(
        payload, http_status=http_status, endpoint=endpoint
    )
    if failure is not None:
        raise failure
    return payload


def _request_headers(channel: MiniMaxChannel) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {channel.api_key}",
        "Content-Type": "application/json",
    }


async def post_json(
    channel: MiniMaxChannel,
    path: str,
    payload: dict[str, Any],
    *,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    """POST JSON：双形态错误在此消化，成功时原样返回响应体（调用方读自己的字段）。"""

    url = f"{channel.api_root}{path}"
    try:
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
            response = await client.post(
                url, headers=_request_headers(channel), json=payload
            )
    except httpx.HTTPError as exc:
        raise MiniMaxError(
            f"MiniMax 请求发送失败（{path}）: {type(exc).__name__}: {exc}"
        ) from exc
    body = _parse_json_object(response)
    return _ensure_business_ok(
        body, http_status=int(response.status_code), endpoint=path
    )


async def get_json(
    channel: MiniMaxChannel,
    path: str,
    *,
    params: dict[str, Any] | None = None,
    timeout: float = 120.0,
) -> dict[str, Any]:
    """GET JSON：同样消化两种错误形态（V2 的任务查询走这里）。"""

    url = f"{channel.api_root}{path}"
    headers = {"Authorization": f"Bearer {channel.api_key}"}
    try:
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
            response = await client.get(url, headers=headers, params=params or {})
    except httpx.HTTPError as exc:
        raise MiniMaxError(
            f"MiniMax 查询失败（{path}）: {type(exc).__name__}: {exc}"
        ) from exc
    body = _parse_json_object(response)
    return _ensure_business_ok(
        body, http_status=int(response.status_code), endpoint=path
    )


def decode_hex_audio(value: str, *, endpoint: str = "") -> bytes:
    """``data.audio`` 的十六进制字符串 → 字节。

    上游偶尔会在 hex 前后带空白 / 换行，先去掉再解；解不动就报「不是合法 hex」
    并带上长度，别把 ``binascii`` 的原始噪声丢给用户。
    """

    clean = str(value or "").strip().replace("\n", "").replace(" ", "")
    if not clean:
        raise MiniMaxError(
            f"MiniMax 返回的音频数据为空（{endpoint or '未知端点'}）",
            endpoint=endpoint,
        )
    try:
        return bytes.fromhex(clean)
    except (binascii.Error, ValueError) as exc:
        # ``bytes.fromhex`` 对非十六进制字符抛的是裸 ``ValueError``（``binascii.Error``
        # 只在长度不对时出现），两条都要归成带端点与长度的可诊断错误。
        raise MiniMaxError(
            f"MiniMax 返回的音频不是合法十六进制（长度 {len(clean)}，"
            f"{endpoint or '未知端点'}）: {exc}",
            endpoint=endpoint,
        ) from exc


async def write_hex_audio(value: str, output_path: Path, *, endpoint: str = "") -> Path:
    content = decode_hex_audio(value, endpoint=endpoint)
    if not content:
        raise MiniMaxError(
            f"MiniMax 返回的音频解码后为空（{endpoint or '未知端点'}）",
            endpoint=endpoint,
        )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    await asyncio.to_thread(output_path.write_bytes, content)
    return output_path


async def download_to(url: str, output_path: Path, *, label: str = "产物") -> Path:
    """下载公网直链到本地（视频产物 / 参考素材转本地文件都走这条）。"""

    clean = str(url or "").strip()
    if not clean:
        raise MiniMaxError(f"{label}下载地址为空")
    try:
        async with httpx.AsyncClient(
            timeout=DOWNLOAD_TIMEOUT_SECONDS, follow_redirects=True
        ) as client:
            response = await client.get(clean)
            if response.status_code >= 400:
                raise MiniMaxError(
                    f"{label}下载失败（HTTP {response.status_code}）: {clean}",
                    http_status=int(response.status_code),
                )
            content = response.content
    except httpx.HTTPError as exc:
        raise MiniMaxError(
            f"{label}下载失败: {type(exc).__name__}: {exc}"
        ) from exc
    if not content:
        raise MiniMaxError(f"{label}下载结果为空文件: {clean}")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    await asyncio.to_thread(output_path.write_bytes, content)
    return output_path


def encode_file_base64(path: Path) -> str:
    if not path.is_file():
        raise MiniMaxError(f"本地素材文件不存在: {path}")
    return base64.b64encode(path.read_bytes()).decode("ascii")


async def upload_file(
    channel: MiniMaxChannel,
    file_path: Path,
    *,
    purpose: str = "general",
) -> str:
    """上传本地素材换取 ``file_id``（拿不到公网直链时的回落通道，M5 用）。

    上游对 ``purpose`` 有白名单，传错会直接被拒；返回值请配合
    :func:`file_reference` 组成 ``mm_file://{file_id}``。
    """

    if not file_path.is_file():
        raise MiniMaxError(f"待上传文件不存在: {file_path}")
    url = f"{channel.api_root}{PATH_FILE_UPLOAD}"
    headers = {"Authorization": f"Bearer {channel.api_key}"}
    content = await asyncio.to_thread(file_path.read_bytes)
    try:
        async with httpx.AsyncClient(
            timeout=UPLOAD_TIMEOUT_SECONDS, follow_redirects=True
        ) as client:
            response = await client.post(
                url,
                headers=headers,
                data={"purpose": purpose},
                files={"file": (file_path.name, content)},
            )
    except httpx.HTTPError as exc:
        raise MiniMaxError(
            f"MiniMax 素材上传失败: {type(exc).__name__}: {exc}"
        ) from exc
    body = _parse_json_object(response)
    _ensure_business_ok(
        body, http_status=int(response.status_code), endpoint=PATH_FILE_UPLOAD
    )
    file_id = ""
    for key in ("file_id", "id"):
        node = body.get(key)
        if isinstance(node, dict):
            node = node.get("id") or node.get("file_id")
        if str(node or "").strip():
            file_id = str(node).strip()
            break
    if not file_id:
        raise MiniMaxError(
            "MiniMax 素材上传成功但没有返回 file_id: "
            f"{json.dumps(body, ensure_ascii=False)[:300]}"
        )
    return file_id


def file_reference(file_id: str) -> str:
    """素材引用形态：上游按 ``mm_file://{file_id}`` 读取已上传文件。"""

    clean = str(file_id or "").strip()
    if not clean:
        raise MiniMaxError("file_id 为空，无法组成素材引用")
    return f"mm_file://{clean}"


def deny_organization_egress(egress_context: TrustedEgressContext | None, label: str) -> None:
    """组织任务直连第三方 = fail-closed（与 MediaKit / 豆包语音同一取舍）。"""

    if egress_context is not None and bool(egress_context.is_organization):
        raise MiniMaxError(
            "ORG_EGRESS_DENIED: "
            f"{label} 直连使用平台侧配置的 MiniMax 渠道凭据，组织任务不允许"
            "用平台凭据外发数据；该链路没有网关替代，请在个人/平台工作区使用"
        )
