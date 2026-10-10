"""模型 HTTP 调用的可行动诊断（[aipdd fork delta]）。

背景：工具页 / 画布上的文本与视觉调用最终都经
``config.get_newapi_text_pydantic_model`` 走到 PydanticAI 的 OpenAI 模型，上游
返回 4xx/5xx 时抛的是 ``pydantic_ai.exceptions.ModelHTTPError``，它的 ``str()``
形如::

    status_code: 404, model_name: volcengine::deepseek-v4-flash-260425,
    body: {'message': 'The model or endpoint ... does not exist or you do not
    have access to it. Request id: ...', 'type': 'Not Found',
    'code': 'InvalidEndpointOrModel.NotFound'}

这串残渣会被任务存储原样落库、再原样显示到工具页，用户既看不出「是谁的错」，
也不知道该怎么行动。本模块把它压成一句中文诊断：HTTP 状态码 + 上游错误码 +
上游原文（截断）+ 按错误类型给出的处理指引，并且全程不带任何凭据。

最要紧的一条指引来自实测结论：**火山方舟 ``GET /models`` 返回的是平台支持的
全部模型目录，与「本账号已开通推理服务的模型」是两件事**——清单里存在不代表
能调用，未开通的一律 404 ``InvalidEndpointOrModel.NotFound``。所以候选目录里的
名字不能当作「可用」的承诺，失败时必须把「去控制台开通 / 改选模型」说清楚。
"""

from __future__ import annotations

import re
from typing import Any

__all__ = [
    "describe_model_call_failure",
    "is_http_call_failure",
    "model_call_failure",
    "note_runtime_not_granted",
    "provider_display_name",
]


# 渠道 id -> 诊断里的人话名称（只用于文案，不影响执行）。与 model_plaza 的展示
# 名口径一致，但诊断文案要带上中文厂商名，用户才知道去哪个控制台。
_PROVIDER_HINT_NAMES: dict[str, str] = {
    "aipdd": "AIPDD",
    "newapi": "NewAPI",
    "openai": "OpenAI",
    "volcengine": "VolcEngine 火山方舟",
    "ark": "VolcEngine 火山方舟",
    "doubao": "VolcEngine 火山方舟",
    "jimeng": "VolcEngine 即梦",
    "comfyui": "ComfyUI",
}

_REQUEST_ID_RE = re.compile(r"request id[:：]\s*([0-9a-zA-Z\-]+)", re.IGNORECASE)

# 上游错误码里明确指向「账号没开通 / 无权限」的那一族（火山方舟形状）。
_ARK_NOT_GRANTED_CODES = ("invalidendpointornotfound", "accessdeniedexception")
# 指向「本地网关没把模型绑到可用渠道」的那一族（new-api 形状）。
_GATEWAY_UNBOUND_CODES = ("model_not_found", "endpoint_not_found")


def provider_display_name(provider: str) -> str:
    """渠道 id -> 文案里的人话名称（供提交前置校验等处复用，避免第二套文案口径）。"""

    key = str(provider or "").strip().lower()
    return _PROVIDER_HINT_NAMES.get(key, "") or key or "对应渠道"


def _provider_of(model_name: str) -> str:
    """调用名里的渠道段：``{provider}::{model}`` 取前缀，裸名返回空串。"""

    text = str(model_name or "").strip()
    if "::" not in text:
        return ""
    return text.split("::", 1)[0].strip().lower()


def _status_code(exc: BaseException) -> int | None:
    for holder in (exc, getattr(exc, "response", None)):
        value = getattr(holder, "status_code", None)
        if isinstance(value, int):
            return value
    return None


def _error_fields(payload: Any) -> tuple[str, str]:
    """从上游 JSON 里取 (code, message)，兼容两种包裹形状。

    - ``{"error": {"message": ..., "code": ...}}``（网关/OpenAI 标准包裹）
    - ``{"message": ..., "code": ..., "type": ...}``（openai SDK 已拆包后的 body）
    """

    if isinstance(payload, dict):
        inner = payload.get("error")
        if isinstance(inner, dict):
            return (
                str(inner.get("code") or inner.get("type") or "").strip(),
                str(inner.get("message") or "").strip(),
            )
        if inner is not None:
            return "", str(inner).strip()
        return (
            str(payload.get("code") or payload.get("type") or "").strip(),
            str(payload.get("message") or "").strip(),
        )
    return "", str(payload or "").strip()


def is_http_call_failure(exc: BaseException) -> bool:
    """只认「上游 HTTP 状态码类失败」。

    其余异常必须原样透传：出网拒绝码（``ORG_EGRESS_DENIED``）、结构化输出校验
    失败、取消信号等都靠类型/文案判定，被这里改写会让上层逻辑与测试同时失真。
    """

    module = str(type(exc).__module__ or "")
    if not module.startswith(("pydantic_ai", "openai")):
        return False
    return _status_code(exc) is not None


def _raw_detail(exc: BaseException) -> tuple[str, str, str]:
    """返回 (code, message, request_id)，取不到就空串。"""

    body = getattr(exc, "body", None)
    if body is None:
        response = getattr(exc, "response", None)
        body = None
        if response is not None:
            try:
                body = response.json()
            except Exception:  # noqa: BLE001 - 非 JSON 响应体回落原文
                body = getattr(response, "text", "")
    code, message = _error_fields(body)
    if not message:
        message = " ".join(str(exc or "").split())
    message = " ".join(message.split())
    match = _REQUEST_ID_RE.search(message)
    request_id = match.group(1) if match else ""
    return code, message, request_id


def _not_granted_signal(
    *,
    status: int | None,
    code: str,
    message: str,
    provider: str,
    include_bare_404: bool = True,
) -> bool:
    """是不是「上游账号没这个模型 / 没权限」那一族。

    [aipdd fork delta] req38：文案与回写共用这一条判定，避免出现第二套口径。差别只在
    ``include_bare_404``：给用户看的文案可以把「方舟渠道 + 裸 404」也归到这一族（措辞
    已经足够具体），而**回写**只认上游当场说死的原话与错误码——裸 404 也可能是我们的
    请求路径打错了，那属于我们的 bug，不该把模型判死。

    网关自己的 ``model_not_found`` / ``no available channel`` 不在这里：那是本地没绑定，
    直连上游可能照样能用，判死会误伤。
    """

    signature = f"{code} {message}".lower()
    if (
        "does not exist or you do not have access" in signature
        or str(code).lower() in _ARK_NOT_GRANTED_CODES
    ):
        return True
    return bool(
        include_bare_404
        and status == 404
        and str(provider or "") in {"volcengine", "ark", "doubao", "jimeng"}
    )


def _gateway_unbound_guidance(call_name: str) -> str:
    """网关说「没有可用渠道」时的指引 —— 按调用名是不是内部功能位分叉。

    [aipdd fork delta] 两类同一个状态码，但动作完全不同：

    - 真模型名（``{provider}::{model}`` / 裸名）：渠道里确实没注册这个名字，去网关侧
      为渠道开通 / 改选候选；
    - ``DC-*`` 内部功能位：它**不是模型名**，而是网关 model_mapping 的路由键，而这个
      键只从「设置 → 模型」的功能位绑定投影出来。叫用户去「为渠道开通该模型」是一条
      死路 —— 渠道目录里找不到这个选项，只能给功能位绑一个真模型。
    """

    from novelvideo.model_slots import is_internal_alias_name

    if is_internal_alias_name(call_name):
        return (
            f"「{call_name}」是系统内部功能位名（不是渠道里的模型），报错来自本地网关而"
            "不是上游：这个功能位还没绑定可用模型，所以网关里没有任何渠道会接这笔调用。"
            "请到「设置 → 模型」给该功能位从渠道目录里选一个模型（渠道还没同步过清单时"
            "先点「同步模型清单」）—— 网关侧的路由会随绑定自动投影，不需要手动开通。"
        )
    return (
        "本地网关没有把该模型绑到可用渠道（报错来自网关，不是上游）：请在「设置"
        " → 模型网关」为对应渠道开通该模型，或改选已就绪的其它候选。"
    )


def _guidance(
    *, status: int | None, code: str, message: str, provider: str, call_name: str = ""
) -> str:
    """按错误特征给最可行动的一句处理指引。

    判定顺序很重要：``model_not_found`` 是本地网关（new-api distributor）的话，
    ``InvalidEndpointOrModel.NotFound`` 是上游火山方舟的话，两者都是「404」但
    该去改的地方完全不同 —— 先看谁的措辞，再看状态码。
    """

    signature = f"{code} {message}".lower()
    provider_name = provider_display_name(provider)

    if _not_granted_signal(
        status=status, code=code, message=message, provider=provider
    ):
        return (
            f"该模型在 {provider_name} 侧未开通推理服务或这把 API Key 没有权限："
            f"{provider_name} 的模型清单返回的是平台全量目录，「清单里有」不等于"
            f"「能调用」——请在火山控制台为该账号开通这个模型的推理服务，"
            f"或在模型下拉里改选其它模型 / 保留「跟随系统默认」。"
        )

    if status in (404, 503) and (
        "no available channel" in signature
        or code.lower() in _GATEWAY_UNBOUND_CODES
    ):
        return _gateway_unbound_guidance(call_name)

    if status == 404:
        return (
            "模型名或接入点不存在：请确认该模型已在上游开通，或在模型下拉里改选"
            "其它候选 / 保留「跟随系统默认」。"
        )

    if status == 503 or "no available channel" in signature:
        return _gateway_unbound_guidance(call_name)

    if status in (401, 403):
        return (
            "鉴权失败：请检查「设置 → 模型网关」里该渠道的 API Key 是否有效、"
            "是否有调用该模型的权限。"
        )

    if status == 429:
        return "上游限流或额度不足：请稍后重试，或在模型下拉里改选其它候选。"

    if status == 400 and (
        "image" in signature
        or "图片" in message
        or "vision" in signature
        or "多模态" in message
    ):
        return (
            "该模型不具备请求所需的图片 / 视觉理解能力：请在「设置 → 模型网关」把"
            "视觉兜底（VISION_MODEL_FALLBACK）指到真正支持视觉的模型，或改选带视觉"
            "能力的候选。"
        )

    return "请确认该模型与渠道当前可用，或在模型下拉里改选其它候选 / 保留「跟随系统默认」。"


def describe_model_call_failure(
    exc: BaseException, *, model_name: str = ""
) -> str:
    """把一次模型 HTTP 失败压成中文单行诊断（不含任何凭据）。"""

    call_name = str(model_name or "").strip() or str(
        getattr(exc, "model_name", "") or ""
    ).strip()
    status = _status_code(exc)
    code, message, request_id = _raw_detail(exc)
    provider = _provider_of(call_name)

    parts = [
        f"模型「{call_name or '未知'}」调用失败：HTTP {status if status is not None else '未知'}"
    ]
    if code:
        parts[0] += f" · {code}"
    if message:
        parts.append(f"上游提示「{message[:240]}」")
    parts.append(
        "处理指引："
        + _guidance(
            status=status,
            code=code,
            message=message,
            provider=provider,
            call_name=call_name,
        ).rstrip("。")
    )
    if request_id:
        parts.append(f"上游 Request id：{request_id}（提工单时附上可加快定位）")
    return "；".join(parts)


def model_call_failure(
    exc: BaseException, *, model_name: str = ""
) -> RuntimeError:
    """把上游异常换成带指引的 ``RuntimeError``（保留 ``from`` 因果链）。

    [aipdd fork delta] req38：顺手把「上游当场判死这个名字」的结论回写实测缓存，让
    候选下拉与模型广场在**这次失败之后**就收敛，而不是等下一轮后台探测再让用户踩一次。
    回写全程 best-effort，任何异常都不允许盖掉用户本该看到的诊断文案。
    """

    note_runtime_not_granted(exc, model_name=model_name)
    return RuntimeError(describe_model_call_failure(exc, model_name=model_name))


def note_runtime_not_granted(exc: BaseException, *, model_name: str = "") -> bool:
    """命中「账号没这个模型 / 没权限」时回写实测结论；其余情况一律不写。"""

    call_name = str(model_name or "").strip() or str(
        getattr(exc, "model_name", "") or ""
    ).strip()
    if not call_name:
        return False
    code, message, _request_id = _raw_detail(exc)
    if not _not_granted_signal(
        status=_status_code(exc),
        code=code,
        message=message,
        provider=_provider_of(call_name),
        include_bare_404=False,
    ):
        return False
    try:
        from novelvideo.text_model_probe import record_runtime_not_granted

        return bool(record_runtime_not_granted(call_name))
    except Exception:  # noqa: BLE001 - 缓存不可写时保持原有行为
        return False
