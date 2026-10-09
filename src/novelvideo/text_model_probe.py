"""上游模型「是否真开通 / 真能用」的最小实测（[aipdd fork delta]）。

要解决的问题：工具页 / 画布的文本与视觉模型候选来自
``custom_newapi_upstream_models_snapshot``，而火山方舟 ``GET /models`` 返回的是
平台支持的**全量模型目录**，与「本账号已开通推理服务的模型」是两件事（容器实测：
135 个名字带图/带文各测一轮，只有个位数能调通，其余全部 404
``InvalidEndpointOrModel.NotFound``）。把清单当可用清单直接灌进候选，等于在模型
下拉里摆一百多个踩下去就 404 的坑。

做法：对每个渠道的候选名发一次最小 chat 请求（``max_tokens=1``）实测，按档位缓存
（见 model_gateway_settings 的 ``custom_newapi_{text,vision}_model_probe``），候选
目录只摘掉「上游明确判了不行」的名字 —— 拿不准的一律保留，绝不误伤：限流、鉴权
失败、超时只会得到 ``unknown``。

两类实测各自独立、结论互不复用：

- ``text``：纯文本 ping，三态 ``ok`` / ``not-granted`` / ``unknown``。
- ``vision``：**带一张极小图片**的 chat 请求，多一档 ``not-vision``（上游明确说这个
  名字读不了图）。只发文本 ping 是不够的：视觉目录里混着 embedding 之类的名字，
  文本 ping 通不代表能理解图片；反过来用文本 ping 判视觉模型也不公平。

调度：候选目录构建时按需后台触发（不阻塞请求），带 TTL、新名字检测与去抖；实测
可用 env 关掉：``MODEL_PROBE_ENABLED=0``、``MODEL_PROBE_TTL_HOURS``（旧名
``TEXT_MODEL_PROBE_*`` 继续兼容）。探测全程只用渠道自身的 baseUrl / upstreamKey，
不经本地网关，也不打印任何凭据。

**适用边界**：结论只描述「该渠道能否用 chat 协议（文本 / 带图）接这个模型」。ASR /
图像生成 / 视频生成等其它能力的可用性**不得**复用这里的缓存判定 —— 音频模型经 chat
一定拿不到 2xx，那既不说明开通也不说明没开通。
"""

from __future__ import annotations

import asyncio
import os
import struct
import sys
import time
import zlib
from datetime import datetime, timezone
from typing import Any, Callable

from novelvideo.model_gateway_settings import (
    get_newapi_provider_channel,
    get_text_model_probe,
    get_vision_model_probe,
    save_text_model_probe,
    save_vision_model_probe,
)

__all__ = [
    "build_not_granted_lookup",
    "chat_model_verdict",
    "classify_probe_response",
    "model_probe_state",
    "record_runtime_not_granted",
    "pending_probe_providers",
    "probe_enabled",
    "probe_provider",
    "provider_needs_probe",
    "run_probe_refresh",
    "schedule_probe_refresh",
]

MAX_CONCURRENCY = 8
REQUEST_TIMEOUT_SECONDS = 30.0
DEFAULT_TTL_HOURS = 24.0
# 同一渠道两次实测尝试的最小间隔：渠道 baseUrl 不可达时避免每次打开工具页都重打一遍。
MIN_RETRY_INTERVAL_SECONDS = 300.0

_NOT_GRANTED_SIGNALS = (
    "invalidendpointornotfound",
    "model_not_found",
    "endpoint_not_found",
    "no available channel",
    "does not exist or you do not have access",
)

# 上游明确「这个名字读不了图」的措辞（中英）。只认这些，而且只在请求被当场拒掉的状态码
# 上认（400 / 415）：限流、鉴权、过载、尺寸/格式类报错一律 unknown，
# 免得探测子系统把能用的视觉模型误摘出候选。
_VISION_UNSUPPORTED_SIGNALS = (
    "不支持图片",
    "不支持图像",
    "不支持 multimodal",
    "不支持多模态",
    "仅支持文本",
    "只支持文本",
    "does not support image",
    "do not support image",
    "not support image",
    "unsupported image",
    "image input is not supported",
    "does not support multimodal",
    "text-only model",
)

# 只有这些状态码上的拒接才是「上游看了请求内容后说不行」；429 / 401 / 5xx 只是链路不行的拒接。
_REQUEST_REJECTED_STATUSES = (400, 415)

# 每种实测的：缓存读取 / 写入 / 会摘出候选的结论 / 候选名口径。
_KINDS: dict[str, dict[str, Any]] = {
    "text": {
        "get": get_text_model_probe,
        "save": save_text_model_probe,
        "removed": ("not-granted",),
        "name_filter": None,
    },
    "vision": {
        "get": get_vision_model_probe,
        "save": save_vision_model_probe,
        "removed": ("not-granted", "not-vision"),
        "name_filter": "vision",
    },
}

# 后台任务必须被持有引用，否则 CPython 会随时回收 task（与 media 侧同口径）。
_background_tasks: set[asyncio.Task[Any]] = set()
_inflight: set[str] = set()
_last_attempt: dict[str, float] = {}


def _kind_spec(kind: str) -> dict[str, Any]:
    return _KINDS.get(str(kind or "text").strip().lower()) or _KINDS["text"]


def _kind_key(kind: str) -> str:
    spec = _kind_spec(kind)
    for name, value in _KINDS.items():
        if value is spec:
            return name
    return "text"


def probe_enabled() -> bool:
    """实测开关（文本与视觉共用）。

    默认在真实环境里开；测试进程（``PYTEST_CURRENT_TEST``）里默认关 —— 后台实测会
    打真网络，既拖慢测试也会让断言依赖 DNS。需要时用 ``MODEL_PROBE_ENABLED=1``
    强制开（配套用 ``client_factory`` 注入 MockTransport）。旧名
    ``TEXT_MODEL_PROBE_ENABLED`` 继续生效，便于已部署的环境不动配置。
    """

    for key in ("MODEL_PROBE_ENABLED", "TEXT_MODEL_PROBE_ENABLED"):
        raw = str(os.environ.get(key, "")).strip().lower()
        if raw in {"0", "false", "no", "off"}:
            return False
        if raw in {"1", "true", "yes", "on"}:
            return True
    return not os.environ.get("PYTEST_CURRENT_TEST")


def probe_ttl_seconds() -> float:
    for key in ("MODEL_PROBE_TTL_HOURS", "TEXT_MODEL_PROBE_TTL_HOURS"):
        raw = str(os.environ.get(key, "")).strip()
        if raw:
            try:
                return max(0.0, float(raw)) * 3600.0
            except (TypeError, ValueError):
                return DEFAULT_TTL_HOURS * 3600.0
    return DEFAULT_TTL_HOURS * 3600.0


def classify_probe_response(
    status_code: int, body_text: str = "", *, kind: str = "text"
) -> str:
    """把一次实测响应折成结论档位（文本三态；视觉多一档 not-vision）。"""

    code = int(status_code)
    if 200 <= code < 300:
        return "ok"
    low = str(body_text or "").lower()
    if code == 404 or any(signal in low for signal in _NOT_GRANTED_SIGNALS):
        return "not-granted"
    if _kind_key(kind) == "vision" and code in _REQUEST_REJECTED_STATUSES and any(
        signal in low for signal in _VISION_UNSUPPORTED_SIGNALS
    ):
        return "not-vision"
    return "unknown"


def build_not_granted_lookup(kind: str = "text") -> Callable[[str, str], bool]:
    """一次性读缓存，返回 ``(provider, model) -> 是否明确不可用`` 的判定器。

    候选目录一次要判上百个名字，用这个把 settings 读取压到一次；读缓存失败时判定器
    恒为 False —— 探测子系统再不可靠也不能伤候选目录。
    """

    spec = _kind_spec(kind)
    try:
        probe = spec["get"]()
    except Exception:  # noqa: BLE001
        return lambda *_args: False
    removed = spec["removed"]

    def not_usable(provider: str, model: str) -> bool:
        entry = probe.get(str(provider or "").strip().lower()) or {}
        states = entry.get("models") or {}
        return str(states.get(str(model or "").strip()) or "") in removed

    return not_usable


def model_probe_state(provider: str, model: str, *, kind: str = "text") -> str:
    """缓存里的结论；没测过也是 ``unknown``（文本 / 视觉各自的缓存互不干扰）。"""

    probe = _kind_spec(kind)["get"]().get(
        str(provider or "").strip().lower()
    ) or {}
    states = probe.get("models") or {}
    return str(states.get(str(model or "").strip()) or "unknown")


def _resolve_call_owner(call_name: str) -> tuple[str, str]:
    """调用名 -> ``(渠道, 模型名)``；归属判不出时渠道为空串（系统不猜渠道）。

    两种归属：复合调用名 ``{provider}::{model}`` 的前缀，或裸名恰好只有一个渠道拥有。
    多归属 / 无归属一律返回空渠道 —— 与候选目录的 ``_call_name_channel`` 同一口径，
    调用方据此放弃判定，不做隐式裁决。
    """

    from novelvideo.model_gateway_settings import (
        model_owner_providers,
        split_provider_spot_model,
    )

    provider, model = split_provider_spot_model(str(call_name or ""))
    if not model:
        return "", ""
    if provider:
        return provider, model
    owners = {str(p or "").strip().lower() for p in model_owner_providers(model)}
    owners.discard("")
    if len(owners) != 1:
        return "", model
    return next(iter(owners)), model


def chat_model_verdict(call_name: str, *, kind: str = "text") -> tuple[str, str]:
    """[aipdd fork delta] req39：提交前查一次「这个名字是否已被实测判死」。

    返回 ``(渠道, 结论档位)``；只有缓存里已经写明「上游明确不可用」（文本
    ``not-granted``；视觉 ``not-granted`` / ``not-vision``）才给出档位，其余一律空串。

    门槛刻意与候选目录的摘除口径**完全一致**：``ok``、``unknown``（限流 / 鉴权 /
    超时 / 端点临时关闭 / 这轮根本没测过）、归属判不出、缓存读失败，全部返回空串 =
    不拦。前置拦截挡在任务入口，误拦的代价是「本来能跑的任务跑不了」，比漏拦严重，
    所以这里宁可放行让调用照常出网（出网失败仍有 req38 的回写与中文诊断）。
    """

    try:
        provider, model = _resolve_call_owner(call_name)
        if not provider:
            return "", ""
        state = model_probe_state(provider, model, kind=kind)
    except Exception:  # noqa: BLE001 - 缓存不可读时不拦，交给真实调用判定
        return "", ""
    return (provider, state) if state in _kind_spec(kind)["removed"] else ("", "")


def _invalidate_candidate_caches() -> None:
    """清掉 API 进程里的文本 / 视觉候选缓存（req38）。

    只碰**已经加载**的模块，绝不主动 import 路由层：任务 runner 进程里模型网关路由包
    未必在 sys.modules，主动把它 import 进来等于把 HTTP 层拖进任务进程。没加载就说明
    这个进程根本不产候选目录，无需清理。
    """

    module = sys.modules.get("novelvideo.api.routes.model_gateway")
    reset = getattr(module, "invalidate_model_candidate_caches", None)
    if not callable(reset):
        return
    try:
        reset()
    except Exception:  # noqa: BLE001 - 缓存清理失败最多让候选名多留 60 秒
        pass


def record_runtime_not_granted(call_name: str) -> bool:
    """[aipdd fork delta] req38：把一次真实调用拿到的「上游明确说没有这个模型」固化。

    背景：实测只在候选目录构建时后台跑，所以两类坑都得用户反复踩——旧列表里残留的
    名字，以及本轮没测过、按「拿不准就保留」留在下拉里的 ``unknown`` 名字。真调用已经
    拿到 ``InvalidEndpointOrModel.NotFound`` 这种账号级结论了，却没地方落，下一个任务
    照样踩。这里让失败本身回写缓存：一次失败，文本 / 视觉两个下拉同时收敛（两份缓存的
    ``removed`` 都含 ``not-granted``，且这条措辞说的是「这个名字在该账号不存在 / 无权限」，
    与模态无关）。

    归属只认两种：复合调用名 ``{provider}::{model}`` 的前缀，或裸名恰好只有一个渠道
    拥有。归属不明一律不写——系统不做隐式裁决。写缓存不拨 ``checkedAt``，避免一次偶发
    失败把整渠道其它 ``unknown`` 名字的 TTL 复测往后推。
    """

    provider, model = _resolve_call_owner(call_name)
    if not provider or not model:
        return False

    # 到这里再取：写入面必须按**调用时**的模块属性解析，单测才能把「缓存写不了」
    # 这种现场注进来验证降级行为。
    from novelvideo.model_gateway_settings import (
        save_text_model_probe,
        save_vision_model_probe,
    )

    wrote = False
    for save in (save_text_model_probe, save_vision_model_probe):
        try:
            save(provider, {model: "not-granted"}, bump_checked_at=False)
        except Exception:  # noqa: BLE001 - 回写只是加速收敛，坏了照旧走探测
            continue
        wrote = True
    if wrote:
        _invalidate_candidate_caches()
    return wrote


def _parse_iso(text: str) -> datetime | None:
    raw = str(text or "").strip()
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _snapshot_names(provider: str, kind: str = "text") -> list[str]:
    from novelvideo.newapi_provisioner import upstream_passthrough_models

    try:
        names = [
            str(name).strip()
            for name in upstream_passthrough_models(provider, from_snapshot=True)
            if str(name).strip()
        ]
    except Exception:  # noqa: BLE001 - 快照缺失时不探测
        return []
    if _kind_spec(kind)["name_filter"] == "vision":
        # 视觉实测只打「有资格当视觉候选」的名字：拿全渠道清单去带图请求，既贵又
        # 会把文本模型的 400 当成结论。
        from novelvideo.media_model_discovery import is_vision_candidate_name

        names = [name for name in names if is_vision_candidate_name(name)]
    return names


def provider_needs_probe(provider: str, *, kind: str = "text") -> bool:
    """该渠道是否需要（重新）实测：没缓存、缓存过期、或清单里出现了新名字。"""

    names = set(_snapshot_names(provider, kind))
    if not names:
        return False
    probe = _kind_spec(kind)["get"]().get(str(provider or "").strip().lower()) or {}
    states = probe.get("models") or {}
    if not states:
        return True
    if names - {str(name) for name in states}:
        return True
    checked_at = _parse_iso(str(probe.get("checkedAt") or ""))
    if checked_at is None:
        return True
    attempt_key = f"{_kind_key(kind)}:{provider}"
    age = time.monotonic() - _last_attempt.get(attempt_key, 0.0)
    if 0.0 <= age < MIN_RETRY_INTERVAL_SECONDS:
        # 去抖：渠道 baseUrl 不可达时，别每次打开工具页都重打一整轮。
        return False
    ttl = probe_ttl_seconds()
    return (datetime.now(timezone.utc) - checked_at).total_seconds() > ttl


def pending_probe_providers(kind: str = "text") -> list[str]:
    """当前需要实测的渠道（有 baseUrl + upstreamKey 且候选清单非空）。"""

    from novelvideo.model_gateway_settings import get_newapi_provider_channels

    pending: list[str] = []
    try:
        channels = get_newapi_provider_channels()
    except Exception:  # noqa: BLE001 - 渠道配置读不到时什么都不做
        return pending
    for channel in channels:
        provider = str(channel.get("provider") or "").strip().lower()
        if not provider or provider == "comfyui":
            continue
        if not str(channel.get("baseUrl") or "").strip():
            continue
        if not str(channel.get("upstreamKey") or "").strip():
            continue
        if provider_needs_probe(provider, kind=kind):
            pending.append(provider)
    return pending


def _probe_image_data_url() -> str:
    """128×128 纯色 PNG 的 data URL（零依赖、258 字节）。

    视觉实测只需要问一件事：「这个名字能不能接收图片输入」。尺寸取 128 是为了躲开
    上游对「图片过小」的拒绝（那属于拿不准，只该得 unknown，不该被当成不能读图）。
    """

    width = height = 128
    row = b"\x00" + bytes((220, 40, 60)) * width

    def _chunk(tag: bytes, data: bytes) -> bytes:
        body = tag + data
        return (
            struct.pack(">I", len(data))
            + body
            + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF)
        )

    png = (
        b"\x89PNG\r\n\x1a\n"
        + _chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
        + _chunk(b"IDAT", zlib.compress(row * height, 9))
        + _chunk(b"IEND", b"")
    )
    import base64

    return "data:image/png;base64," + base64.b64encode(png).decode("ascii")


_PROBE_IMAGE_DATA_URL = _probe_image_data_url()


def _probe_payload(model: str, kind: str) -> dict[str, Any]:
    """实测请求体：文本发一句 ping，视觉发「文字 + 一张极小图」。"""

    if _kind_key(kind) == "vision":
        content: Any = [
            {"type": "text", "text": "图里是什么颜色？只回一个词。"},
            {"type": "image_url", "image_url": {"url": _PROBE_IMAGE_DATA_URL}},
        ]
    else:
        content = "ping"
    return {
        "model": model,
        "messages": [{"role": "user", "content": content}],
        "max_tokens": 1,
    }


async def _probe_one(
    client: Any, *, base_url: str, api_key: str, model: str, kind: str = "text"
) -> tuple[str, str]:
    import httpx

    try:
        response = await client.post(
            f"{base_url.rstrip('/')}/chat/completions",
            headers={"Authorization": f"Bearer {api_key}"},
            json=_probe_payload(model, kind),
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
    except (httpx.HTTPError, OSError):
        # 传输层失败说明不了开通与否，只留 unknown，绝不因此把名字摘掉。
        return model, "unknown"
    return model, classify_probe_response(
        response.status_code, response.text, kind=kind
    )


async def probe_provider(
    provider: str,
    *,
    kind: str = "text",
    models: list[str] | None = None,
    client_factory: Callable[[], Any] | None = None,
) -> dict[str, str]:
    """实测一个渠道，返回 {模型名: 结论档位}，并写进对应缓存。"""

    provider_key = str(provider or "").strip().lower()
    channel = get_newapi_provider_channel(provider_key) or {}
    base_url = str(channel.get("baseUrl") or "").strip()
    api_key = str(channel.get("upstreamKey") or "").strip()
    if not provider_key or not base_url or not api_key:
        return {}
    names = [
        str(name).strip()
        for name in (
            models if models is not None else _snapshot_names(provider_key, kind)
        )
        if str(name).strip()
    ]
    if not names:
        return {}

    semaphore = asyncio.Semaphore(MAX_CONCURRENCY)

    def _make_client() -> Any:
        if client_factory is not None:
            return client_factory()
        import httpx

        # 一个渠道共用一条连接池：并发上限与探测并发对齐，避免瞬开上百连接。
        return httpx.AsyncClient(limits=httpx.Limits(max_connections=MAX_CONCURRENCY))

    async def _one(client: Any, model: str) -> tuple[str, str]:
        async with semaphore:
            return await _probe_one(
                client, base_url=base_url, api_key=api_key, model=model, kind=kind
            )

    async with _make_client() as client:
        results = await asyncio.gather(*[_one(client, model) for model in names])
    states = {model: state for model, state in results}
    _kind_spec(kind)["save"](provider_key, states, base_url=base_url)
    return states


async def run_probe_refresh(
    providers: list[str],
    *,
    kind: str = "text",
    client_factory: Callable[[], Any] | None = None,
) -> dict[str, dict[str, str]]:
    """逐个渠道实测（渠道之间也限并发为 1，避免同时打满上游配额）。"""

    kind_key = _kind_key(kind)
    summary: dict[str, dict[str, str]] = {}
    for provider in providers:
        provider_key = str(provider or "").strip().lower()
        attempt_key = f"{kind_key}:{provider_key}"
        if not provider_key or attempt_key in _inflight:
            continue
        _inflight.add(attempt_key)
        _last_attempt[attempt_key] = time.monotonic()
        try:
            summary[provider_key] = await probe_provider(
                provider_key, kind=kind_key, client_factory=client_factory
            )
        except Exception:  # noqa: BLE001 - 实测失败只影响缓存，不影响任何主流程
            summary[provider_key] = {}
        finally:
            _inflight.discard(attempt_key)
    return summary


def schedule_probe_refresh(
    *,
    kind: str = "text",
    providers: list[str] | None = None,
    client_factory: Callable[[], Any] | None = None,
) -> list[str]:
    """后台幂等触发实测；返回本轮真正新排队的渠道（空列表 = 无需测 / 没事件循环）。"""

    if not probe_enabled():
        return []
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return []
    kind_key = _kind_key(kind)
    targets = [
        str(p).strip().lower()
        for p in (
            providers
            if providers is not None
            else pending_probe_providers(kind_key)
        )
        if str(p).strip()
    ]
    targets = [
        p
        for p in dict.fromkeys(targets)
        if f"{kind_key}:{p}" not in _inflight
    ]
    if not targets:
        return []

    task = loop.create_task(
        run_probe_refresh(targets, kind=kind_key, client_factory=client_factory)
    )
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)
    return targets


def reset_probe_scheduler_state() -> None:
    """测试用：清掉在跑标记与去抖时间戳。"""

    _inflight.clear()
    _last_attempt.clear()
    _background_tasks.clear()
