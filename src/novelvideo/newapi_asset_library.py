"""[aipdd fork delta req47] NewAPI v2 官方素材库（Seedance 私域素材 + 真人活体认证）客户端。

两条用户口径（2026-10-08）钉死了本模块的形状：

1. **素材库不是「接了 NewAPI 就有」**。实测差分判据（2026-10-08 容器内只读探针，零计费）：
   - 内置网关：``POST {站点根}/v2/assets?Action=ListAssetGroups`` 与不存在的控制路径
     ``/v2/assets-probe-control`` **都返回 SPA 的 HTML**（HTTP 200 + ``text/html``）→ 这组路由没有；
   - 另一台 NewAPI：``/v2/assets?Action=`` 进到令牌鉴权层（401 ``new_api_error`` 无效的令牌），
     而控制路径返回 SPA HTML → 这组路由存在（被自身令牌中间件挡住，能力未证实）。
   支持性因此只能**探测**（``probe_asset_library_support``），任何调用点都不许假定。
2. **接口怎么样就怎么对照实现，不兼容错误**。请求体严格按上游文档的 PascalCase 字段与
   ``Action`` / ``Version`` 查询参数；响应按 ``{ResponseMetadata{...,Error}, Result{...}}`` 信封拆，
   上游错误码与原文一律透出（不改写、不吞、不降级成「未知错误」）。

端点形态也按实测选定：文档概览页写新式 ``POST /v2/assets/{Action}`` 并称旧式已停用，但
逐接口页全用旧式 ``?Action=``；实测部署里新式路径一律落 SPA 兜底、旧式命中路由 → 本模块用
旧式。地址全部从配置/渠道推导，源码里不写任何站点域名（req41 静态守卫）。
"""

from __future__ import annotations

import hashlib
import logging
import os
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

# 上游固定的 API 版本（文档逐接口页 ``Version=2024-01-01``）。
ASSET_API_VERSION = "2024-01-01"
# 资产入口路径。渠道 ``baseUrl`` 通常带 ``/v1`` 后缀，素材接口挂在站点根，必须先剥。
ASSET_ENDPOINT_PATH = "/v2/assets"
# 控制组路径：与资产入口同段但绝不该被注册，用来把「路由存在但被鉴权挡住」和
# 「整段前缀都被兜底」区分开（见模块文档串的差分判据）。
PROBE_CONTROL_PATH = "/v2/assets-probe-control"
# 探测用的只读 Action：List* 不写数据、不产生上游计费。
PROBE_ACTION = "ListAssetGroups"
PROBE_TIMEOUT_SECONDS = 20.0
ACTION_TIMEOUT_SECONDS = 60.0
SUPPORT_PROBE_TTL_SECONDS = 300.0

GROUP_TYPES = ("AIGC", "LivenessFace")
ASSET_TYPES = ("Image", "Video", "Audio")
ASSET_STATUSES = ("Processing", "Active", "Failed")
SORT_ORDERS = ("Asc", "Desc")

# [aipdd fork delta req47] 生成侧引用形态：素材入库拿到的 Id 折成 ``asset://{Id}``，直接递进
# ``content[].image_url.url``（上游口径）。它**不是可下载的地址**：库里那份才是本体，而
# ``GetAsset`` / ``ListAssets`` 回的是临时直链（约 12 小时过期），所以生成必须用引用而不是 URL。
ASSET_REFERENCE_SCHEME = "asset://"

# 探测状态：只有 SUPPORTED 允许面板放开写操作。
STATE_SUPPORTED = "supported"
STATE_UNSUPPORTED = "unsupported"
STATE_UNAUTHORIZED = "unauthorized"
STATE_AMBIGUOUS = "ambiguous"
STATE_UNCONFIGURED = "unconfigured"
STATE_UNREACHABLE = "unreachable"


class AssetLibraryError(RuntimeError):
    """素材库调用失败：``message`` 保持上游原文，``kind`` 供门控分派。"""

    kind = "upstream_error"

    def __init__(
        self,
        message: str,
        *,
        code: str = "",
        status: int = 0,
        request_id: str = "",
        envelope: bool = False,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.code = str(code or "")
        self.status = int(status or 0)
        self.request_id = str(request_id or "")
        # 错误体是不是资产接口的 ResponseMetadata 信封：这是能力探测的关键差异点——
        # 拿到信封就说明请求打到了资产接口，后面的错只是账号/参数层面的。
        self.envelope = bool(envelope)

    def to_dict(self) -> dict[str, object]:
        return {
            "kind": self.kind,
            "code": self.code,
            "message": self.message,
            "httpStatus": self.status,
            "requestId": self.request_id,
            "envelope": self.envelope,
        }


class AssetLibraryUnauthorized(AssetLibraryError):
    """网关/令牌层拒绝（``new_api_error`` 一类），属凭据问题而非能力问题。"""

    kind = "unauthorized"


class AssetLibraryUnsupported(AssetLibraryError):
    """这台 NewAPI 没有暴露 v2 资产接口（响应是站点页面 / 404，不是资产信封）。"""

    kind = "unsupported"


class AssetLibraryTransportError(AssetLibraryError):
    """连不上 / 超时：不能据此判定支持性。"""

    kind = "transport"


class AssetLibraryNotConfigured(AssetLibraryError):
    """解析不出 ``(站点根, Key)``：还没连 NewAPI 网关/渠道。"""

    kind = "unconfigured"


@dataclass(frozen=True, slots=True)
class AssetLibraryTarget:
    """一次调用实际使用的 ``(站点根, Key)`` 与它的来源（面板要显示来源，避免猜）。

    ``origin`` 在构造时就洗成站点根：它是拼请求 URL 的唯一入口，不能因为调用方
    递了个带 ``/v1`` 的渠道地址进来，就把请求发到 ``…/v1/v2/assets`` 上去。
    """

    origin: str
    api_key: str
    source: str

    def __post_init__(self) -> None:
        cleaned = _asset_api_origin(self.origin)
        if cleaned:
            object.__setattr__(self, "origin", cleaned)

    def masked_key(self) -> str:
        key = self.api_key
        if not key:
            return ""
        return f"{key[:4]}…{key[-4:]}（{len(key)} 位）" if len(key) > 12 else "已配置"


def _asset_api_origin(base_url: str) -> str:
    """把渠道/网关地址还原成站点根（``scheme://host[:port]``）。

    实测资产口只挂在站点根，``baseUrl`` 带的 ``/v1`` 之类前缀必须先剥掉。
    """

    raw = str(base_url or "").strip()
    if not raw:
        return ""
    parsed = urlparse(raw if "://" in raw else f"https://{raw}")
    netloc = str(parsed.netloc or "").strip()
    # 垃圾地址不能当主机用：``https://not a url`` 能被 urlparse 接住，拼出去就是一
    # 个永远连不上的 URL，不如在这里判「没解析出地址」，让上层报未配置而不是诡异地超时。
    if not parsed.scheme or not netloc or any(ch.isspace() for ch in netloc):
        return ""
    return f"{parsed.scheme}://{netloc}"


GROUP_SORT_BY = ("CreateTime", "UpdateTime")
ASSET_SORT_BY = ("CreateTime", "UpdateTime", "GroupId")


def _first_credential_source() -> tuple[str, str, str] | None:
    """按「显式设置 > 生效网关 > 已连 NewAPI 渠道」取第一条凑齐的 ``(根, Key, 来源)``。"""

    explicit_base = str(os.environ.get("SEEDANCE_ASSET_BASE_URL", "") or "").strip()
    explicit_key = str(os.environ.get("SEEDANCE_ASSET_API_KEY", "") or "").strip()
    if explicit_base and explicit_key:
        return _asset_api_origin(explicit_base), explicit_key, "environment"

    try:
        from novelvideo.model_gateway_settings import get_effective_newapi_config

        gateway = get_effective_newapi_config()
        origin = _asset_api_origin(str(gateway.base_url or ""))
        key = str(gateway.api_key or "").strip()
        if origin and key:
            return origin, key, f"gateway[{gateway.mode}/{gateway.source}]"
    except Exception:  # noqa: BLE001 - 设置库不可读时继续往渠道找
        logger.warning("Seedance 素材库：读取生效网关配置失败", exc_info=True)

    try:
        from novelvideo.newapi_provisioner import get_newapi_provider_channels

        channels = get_newapi_provider_channels()
    except Exception:  # noqa: BLE001
        logger.warning("Seedance 素材库：读取渠道配置失败", exc_info=True)
        channels = []
    for channel in channels:
        if str(channel.get("provider") or "").strip().lower() != "newapi":
            continue
        origin = _asset_api_origin(str(channel.get("baseUrl") or ""))
        key = str(channel.get("upstreamKey") or "").strip()
        if origin and key:
            name = str(channel.get("name") or channel.get("id") or "").strip()
            return origin, key, f"channel[{name or 'newapi'}]"
    return None


def resolve_asset_library_target(
    *,
    base_url: str = "",
    api_key: str = "",
) -> AssetLibraryTarget:
    """解析素材库调用目标；凑不齐 ``(站点根, Key)`` 直接抛可执行的错。

    调用点的固定顺序是「先按文档校验参数、再解析凭据发请求」：坏请求不该先被
    「素材库未就绪」挡住，否则用户看到的是凭据错，实际是自己字段传错了。
    """

    origin = _asset_api_origin(base_url)
    key = str(api_key or "").strip()
    if origin and key:
        return AssetLibraryTarget(origin=origin, api_key=key, source="manual")
    resolved = _first_credential_source()
    if resolved is None:
        raise AssetLibraryNotConfigured(
            "Seedance 素材库未就绪：素材库只随 NewAPI 网关提供，请先在「设置 → 模型与渠道」"
            "填好 NewAPI 地址与 Key（或连接一条 NewAPI 渠道）。"
        )
    resolved_origin, resolved_key, source = resolved
    # 只填一半也能跑：给了地址就沿用解析到的 Key，反之亦然。
    return AssetLibraryTarget(
        origin=origin or resolved_origin,
        api_key=key or resolved_key,
        source=source if not origin else f"{source}+manualBaseUrl",
    )


def _looks_like_html(response) -> bool:
    """SPA 兜底的识别：资产接口不存在时，站点会回前端页面而不是 JSON。"""

    content_type = str(getattr(response, "headers", {}).get("content-type", "") or "").lower()
    if "text/html" in content_type:
        return True
    text = str(getattr(response, "text", "") or "").lstrip().lower()
    return text.startswith(("<!doctype html", "<html", "<head"))


def _response_snippet(response, limit: int = 200) -> str:
    text = str(getattr(response, "text", "") or "").strip().replace("\n", " ")
    return text[:limit]


def _post_action(
    action: str,
    payload: dict[str, object],
    target: AssetLibraryTarget,
    *,
    timeout: float = ACTION_TIMEOUT_SECONDS,
) -> dict[str, object]:
    """发一次 ``POST {站点根}/v2/assets?Action=&Version=``，返回信封里的 ``Result``。

    非 2xx 也要先把信封读出来：上游的参数错、账号未开通都带 ``ResponseMetadata.Error``，
    原文比 HTTP 状态码有用得多，绝不能在这一步被 ``raise_for_status`` 丢掉。
    """

    import httpx

    url = f"{target.origin}{ASSET_ENDPOINT_PATH}"
    headers = {
        "Authorization": f"Bearer {target.api_key}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    try:
        with httpx.Client(timeout=timeout) as client:
            response = client.post(
                url,
                params={"Action": action, "Version": ASSET_API_VERSION},
                json=payload,
                headers=headers,
            )
    except httpx.HTTPError as exc:
        raise AssetLibraryTransportError(
            f"Seedance 素材库请求失败（{action}）：{exc}",
            code="TransportError",
        ) from exc

    status = int(getattr(response, "status_code", 0) or 0)
    if _looks_like_html(response):
        raise AssetLibraryUnsupported(
            f"该 NewAPI 未暴露 v2 素材库接口（{action} 返回的是站点页面，不是资产信封）",
            code="RouteNotFound",
            status=status,
        )
    try:
        body = response.json()
    except ValueError:
        raise AssetLibraryError(
            f"Seedance 素材库响应无法识别（{action}，HTTP {status}）："
            f"{_response_snippet(response)}",
            code="BadEnvelope",
            status=status,
        )
    if not isinstance(body, dict):
        raise AssetLibraryError(
            f"Seedance 素材库响应不是对象（{action}，HTTP {status}）：{_response_snippet(response)}",
            code="BadEnvelope",
            status=status,
        )

    meta = body.get("ResponseMetadata")
    error = meta.get("Error") if isinstance(meta, dict) else None
    if isinstance(error, dict):
        code = str(error.get("Code") or "")
        message = str(error.get("Message") or "") or _response_snippet(response)
        request_id = str(meta.get("RequestId") or "") if isinstance(meta, dict) else ""
        if status in (401, 403):
            raise AssetLibraryUnauthorized(
                message,
                code=code,
                status=status,
                request_id=request_id,
                envelope=True,
            )
        raise AssetLibraryError(
            message,
            code=code,
            status=status,
            request_id=request_id,
            envelope=True,
        )

    # 网关自己的令牌层用的是 OpenAI 风格错误体（实测 ``无效的令牌``），原文照透出。
    if status >= 400:
        err = body.get("error")
        if isinstance(err, dict):
            message = str(err.get("message") or "") or _response_snippet(response)
            code = str(err.get("code") or "") or str(err.get("type") or "")
            exc_cls = (
                AssetLibraryUnauthorized if status in (401, 403) else AssetLibraryError
            )
            raise exc_cls(message, code=code, status=status)
        if status == 404:
            raise AssetLibraryUnsupported(
                f"该 NewAPI 没有 v2 素材库接口（{action} 返回 404）",
                code="NotFound",
                status=status,
            )
        raise AssetLibraryError(
            f"Seedance 素材库请求失败（{action}，HTTP {status}）：{_response_snippet(response)}",
            status=status,
        )

    result = body.get("Result")
    if isinstance(result, dict):
        return result
    if isinstance(meta, dict):
        # 信封在、Result 缺：上游确实可能返回空对象，按空结果处理而不是报错。
        return {}
    # 没有信封的裸对象（少数转发实现）：原样交给调用方，不做兜底改写。
    return body


def _require(value: str, field: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{field} 必填")
    return text


def _one_of(value: str, choices: tuple[str, ...], field: str) -> str:
    text = _require(value, field)
    if text not in choices:
        raise ValueError(f"{field} 只能是 {'/'.join(choices)}，收到 {text!r}")
    return text


def _paged_payload(
    *,
    filter_: dict[str, object],
    max_results: int | None,
    next_token: str,
    page_number: int | None,
    page_size: int | None,
    sort_by: str,
    sort_by_choices: tuple[str, ...],
    sort_order: str,
) -> dict[str, object]:
    """组装列表请求：两套分页参数互斥（文档口径），不传的不进请求体。

    ``SortBy`` 的可选值两个接口不一样（素材组只有创建/更新时间，素材多一个按组排），
    所以由调用方把合法集合传进来，不在这里做「宽容接受」。
    """

    payload: dict[str, object] = {
        "Filter": {k: v for k, v in filter_.items() if v not in (None, "", [])}
    }
    if max_results is not None:
        payload["MaxResults"] = int(max_results)
        if str(next_token or "").strip():
            payload["NextToken"] = str(next_token).strip()
    elif page_number is not None or page_size is not None:
        if page_number is not None:
            payload["PageNumber"] = int(page_number)
        if page_size is not None:
            payload["PageSize"] = int(page_size)
    if str(sort_by or "").strip():
        payload["SortBy"] = _one_of(sort_by, sort_by_choices, "SortBy")
    payload["SortOrder"] = _one_of(sort_order or "Desc", SORT_ORDERS, "SortOrder")
    return payload


# ---------------------------------------------------------------------------
# 七个 Action：字段名、必填项、大小写全部按上游文档原样。
# ---------------------------------------------------------------------------


def list_asset_groups(
    *,
    group_type: str = "AIGC",
    group_ids: list[str] | None = None,
    name: str = "",
    project_name: str = "",
    max_results: int | None = None,
    next_token: str = "",
    page_number: int | None = None,
    page_size: int | None = None,
    sort_by: str = "CreateTime",
    sort_order: str = "Desc",
    target: AssetLibraryTarget | None = None,
) -> dict[str, object]:
    """``ListAssetGroups``：``Filter.GroupType`` 必填（AIGC=虚拟素材组 / LivenessFace=真人组）。"""

    filter_: dict[str, object] = {
        "GroupType": _one_of(group_type, GROUP_TYPES, "GroupType"),
        "GroupIds": [str(g).strip() for g in (group_ids or []) if str(g).strip()],
        "Name": str(name or "").strip(),
    }
    if str(project_name or "").strip():
        filter_["ProjectName"] = str(project_name).strip()
    target = target or resolve_asset_library_target()
    return _post_action(
        PROBE_ACTION,
        _paged_payload(
            filter_=filter_,
            max_results=max_results,
            next_token=next_token,
            page_number=page_number,
            page_size=page_size,
            sort_by=sort_by,
            sort_by_choices=GROUP_SORT_BY,
            sort_order=sort_order,
        ),
        target,
    )


def get_asset_group(group_id: str, *, target: AssetLibraryTarget | None = None) -> dict[str, object]:
    """``GetAssetGroup``。"""

    target = target or resolve_asset_library_target()
    return _post_action(
        "GetAssetGroup", {"Id": _require(group_id, "GroupId")}, target
    )


def list_assets(
    *,
    group_type: str = "AIGC",
    group_ids: list[str] | None = None,
    name: str = "",
    asset_type: str = "",
    statuses: list[str] | None = None,
    project_name: str = "",
    max_results: int | None = None,
    next_token: str = "",
    page_number: int | None = None,
    page_size: int | None = None,
    sort_by: str = "CreateTime",
    sort_order: str = "Desc",
    target: AssetLibraryTarget | None = None,
) -> dict[str, object]:
    """``ListAssets``：可按组 / 名称 / 类型 / 审核状态过滤。"""

    filter_: dict[str, object] = {
        "GroupType": _one_of(group_type, GROUP_TYPES, "GroupType"),
        "GroupIds": [str(g).strip() for g in (group_ids or []) if str(g).strip()],
        "Name": str(name or "").strip(),
        "AssetType": (
            _one_of(asset_type, ASSET_TYPES, "AssetType") if str(asset_type or "").strip() else ""
        ),
        "Statuses": [
            _one_of(s, ASSET_STATUSES, "Statuses[]") for s in (statuses or []) if str(s).strip()
        ],
    }
    if str(project_name or "").strip():
        filter_["ProjectName"] = str(project_name).strip()
    target = target or resolve_asset_library_target()
    return _post_action(
        "ListAssets",
        _paged_payload(
            filter_=filter_,
            max_results=max_results,
            next_token=next_token,
            page_number=page_number,
            page_size=page_size,
            sort_by=sort_by,
            sort_by_choices=ASSET_SORT_BY,
            sort_order=sort_order,
        ),
        target,
    )


def get_asset(asset_id: str, *, target: AssetLibraryTarget | None = None) -> dict[str, object]:
    """``GetAsset``：轮询审核状态就用它（``Processing → Active/Failed``）。"""

    target = target or resolve_asset_library_target()
    return _post_action("GetAsset", {"Id": _require(asset_id, "AssetId")}, target)


def create_asset(
    *,
    group_id: str,
    asset_type: str,
    url: str,
    name: str = "",
    project_name: str = "",
    target: AssetLibraryTarget | None = None,
) -> dict[str, object]:
    """``CreateAsset``：把一张公网直链登记进素材组，返回 ``{"Id": …}``。

    ``URL`` 必须是上游能直接抓取的公网直链（本地素材先走媒体中转，见 req44）；
    素材异步审核，拿到 Id 不等于可用，得靠 ``get_asset`` 看 ``Status``。
    """

    payload: dict[str, object] = {
        "GroupId": _require(group_id, "GroupId"),
        "AssetType": _one_of(asset_type, ASSET_TYPES, "AssetType"),
        "URL": _require(url, "URL"),
    }
    if str(name or "").strip():
        asset_name = str(name).strip()
        if len(asset_name) > 64:
            raise ValueError("Name 最长 64 字符")
        payload["Name"] = asset_name
    if str(project_name or "").strip():
        payload["ProjectName"] = str(project_name).strip()
    target = target or resolve_asset_library_target()
    return _post_action("CreateAsset", payload, target)


def create_visual_validate_session(
    *,
    callback_url: str,
    project_name: str = "",
    target: AssetLibraryTarget | None = None,
) -> dict[str, object]:
    """``CreateVisualValidateSession``：拿真人活体认证的 ``H5Link`` + ``BytedToken``。

    ``CallbackURL`` 必须公网可达（本人在手机上做完认证后浏览器要跳回来），
    认证结果只能用 ``get_visual_validate_result`` 查，会话本身不落数据。
    """

    payload: dict[str, object] = {"CallbackURL": _require(callback_url, "CallbackURL")}
    if str(project_name or "").strip():
        payload["ProjectName"] = str(project_name).strip()
    target = target or resolve_asset_library_target()
    return _post_action("CreateVisualValidateSession", payload, target)


def get_visual_validate_result(
    byted_token: str,
    *,
    target: AssetLibraryTarget | None = None,
) -> dict[str, object]:
    """``GetVisualValidateResult``：认证完成后返回锁定的真人 ``GroupId``。"""

    target = target or resolve_asset_library_target()
    return _post_action(
        "GetVisualValidateResult",
        {"BytedToken": _require(byted_token, "BytedToken")},
        target,
    )


# ---------------------------------------------------------------------------
# 能力探测：这台 NewAPI 「有没有」素材库只能问它自己，任何调用点都不许假定。
# ---------------------------------------------------------------------------


def _probe_cache_key(origin: str, api_key: str) -> str:
    digest = hashlib.sha1(f"{origin}|{api_key}".encode()).hexdigest()[:16]
    return f"{origin}|{digest}"


_probe_cache: dict[str, tuple[float, dict[str, object]]] = {}


def _raw_probe_post(target: AssetLibraryTarget, path: str) -> str:
    """控制组请求的形态：只关心「回的是 JSON 还是站点页面」，不解析内容。

    返回 ``json`` / ``html`` / ``transport``。资产接口不存在时部署会把路径交给前端 SPA
    兜底（实测 HTTP 200 + ``text/html``），这就是「没有这组路由」的正身。
    """

    import httpx

    headers = {
        "Authorization": f"Bearer {target.api_key}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    try:
        with httpx.Client(timeout=PROBE_TIMEOUT_SECONDS) as client:
            response = client.post(
                f"{target.origin}{path}",
                params={"Action": PROBE_ACTION, "Version": ASSET_API_VERSION},
                json={"Filter": {"GroupType": "AIGC"}, "MaxResults": 1},
                headers=headers,
            )
    except httpx.HTTPError:
        return "transport"
    return "html" if _looks_like_html(response) else "json"


def _probe_result(
    state: str,
    *,
    reason: str,
    target: AssetLibraryTarget | None = None,
    exc: AssetLibraryError | None = None,
    warning: str = "",
) -> dict[str, object]:
    payload: dict[str, object] = {
        "supported": state == STATE_SUPPORTED,
        "state": state,
        "reason": reason,
        "baseUrl": target.origin if target else "",
        "credentialSource": target.source if target else "",
        "keyPreview": target.masked_key() if target else "",
        "httpStatus": exc.status if exc else 0,
        "upstreamCode": exc.code if exc else "",
        "upstreamMessage": exc.message if exc else "",
        "warning": warning,
        "checkedAt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "cached": False,
    }
    return payload


def probe_asset_library_support(
    *,
    base_url: str = "",
    api_key: str = "",
    force: bool = False,
) -> dict[str, object]:
    """探测这台 NewAPI 是否暴露 v2 素材库，结果按 ``(地址, Key)`` 缓存 TTL 秒。

    用的判据是**差分**：拿真实 Action 的响应形态 + 一个绝不该存在的同段控制路径一起看，
    才能把「路由存在但令牌被拒」和「这台部署根本没有这组路由」分开——只看状态码会误判
    （实测两条路径都可能是 401，也可能一个是 401 一个是 HTML）。
    """

    try:
        target = resolve_asset_library_target(base_url=base_url, api_key=api_key)
    except AssetLibraryNotConfigured as exc:
        return _probe_result(STATE_UNCONFIGURED, reason=str(exc))

    cache_key = _probe_cache_key(target.origin, target.api_key)
    if not force:
        cached = _probe_cache.get(cache_key)
        if cached is not None and time.monotonic() - cached[0] < SUPPORT_PROBE_TTL_SECONDS:
            result = dict(cached[1])
            result["cached"] = True
            return result

    try:
        list_asset_groups(group_type="AIGC", max_results=1, target=target)
    except AssetLibraryTransportError as exc:
        # 连不上不代表不支持，也不能缓存住——否则网关恢复后面板还灰着。
        return _probe_result(
            STATE_UNREACHABLE,
            reason=f"无法连接该 NewAPI 网关：{exc.message}",
            target=target,
            exc=exc,
        )
    except AssetLibraryUnsupported as exc:
        result = _probe_result(
            STATE_UNSUPPORTED,
            reason=(
                "这台 NewAPI 没有暴露 v2 素材库接口，素材库与真人认证不可用"
                "（换一个支持素材库的 NewAPI 网关，或让当前网关开启该转发）"
            ),
            target=target,
            exc=exc,
        )
    except AssetLibraryError as exc:
        if getattr(exc, "envelope", False):
            # 拿到了 ResponseMetadata 信封：请求确实打到资产接口，是账号/参数层面的问题
            # ——功能在，所以判 supported，同时把上游原文留在 warning 里。
            result = _probe_result(
                STATE_SUPPORTED,
                reason="素材库接口可达（上游返回了资产信封）",
                target=target,
                exc=exc,
                warning=f"{exc.code}: {exc.message}".strip(": "),
            )
        else:
            control = _raw_probe_post(target, PROBE_CONTROL_PATH)
            if control == "json":
                # 控制路径也回 JSON：整段前缀被网关自己的中间件接管，无法判定能力。
                result = _probe_result(
                    STATE_AMBIGUOUS,
                    reason=(
                        "无法判定这台 NewAPI 是否支持素材库：资产接口与控制路径返回了同形态的"
                        "错误（鉴权层覆盖了整段前缀）。"
                    ),
                    target=target,
                    exc=exc,
                )
            elif control == "transport":
                return _probe_result(
                    STATE_UNREACHABLE,
                    reason="控制路径都连不上，本次探测不作判定。",
                    target=target,
                    exc=exc,
                )
            else:
                # 控制路径落 SPA 兜底、资产路径进到了网关错误层：路由在，但当前 Key 被拒。
                result = _probe_result(
                    STATE_UNAUTHORIZED,
                    reason=(
                        "这台 NewAPI 有素材库路由，但当前 Key 被它拒绝，能力尚未证实"
                        f"（上游原文：{exc.message}）"
                    ),
                    target=target,
                    exc=exc,
                )
    else:
        result = _probe_result(
            STATE_SUPPORTED,
            reason="素材库可用：读到了资产组列表。",
            target=target,
        )

    state = str(result["state"])
    if state != STATE_AMBIGUOUS:
        # 含糊结论不缓存：下一次换 Key / 换网络环境就要重新问。
        _probe_cache[cache_key] = (time.monotonic(), dict(result))
    return result


def invalidate_asset_library_probe() -> None:
    """网关地址 / Key 刚被改过时清掉探测缓存（不然面板要等 TTL 才松绑）。"""

    _probe_cache.clear()


def is_asset_reference(value: object) -> bool:
    """这条媒体输入是不是素材库引用（``asset://{Id}``）。

    提交链路用它来判断「还需不需要中转拿直链」：引用指的就是上游库里那份已过审的素材，
    再下载一次、再上传一次既没有意义，也会把临时直链的过期时间带进一次付费生成。
    """

    return str(value or "").strip().startswith(ASSET_REFERENCE_SCHEME)


def asset_reference(asset_id: str) -> str:
    """把素材 Id 折成生成请求能直接递交的引用；形态不对就当场报错，不递半成品。"""

    clean = str(asset_id or "").strip()
    if clean.startswith(ASSET_REFERENCE_SCHEME):
        # 已经带 scheme 就别再包一层（面板复制出来的就是这种形态）。
        clean = clean.removeprefix(ASSET_REFERENCE_SCHEME).strip()
    if not clean:
        raise ValueError("素材 Id 不能为空（引用形态是 asset://<Id>）")
    if any(ch.isspace() for ch in clean) or "/" in clean or "\\" in clean:
        raise ValueError(f"素材 Id 形态不对：{asset_id}")
    return f"{ASSET_REFERENCE_SCHEME}{clean}"


def require_asset_library_supported() -> dict[str, object]:
    """写操作（建素材 / 起真人认证）前的闸门：不支持就直接拦，绝不「先试一把」。"""

    status = probe_asset_library_support()
    if status["supported"]:
        return status
    state = str(status.get("state") or "")
    if state == STATE_UNAUTHORIZED:
        raise AssetLibraryUnauthorized(
            str(status.get("reason") or "当前 Key 被该 NewAPI 拒绝"),
            code=str(status.get("upstreamCode") or ""),
            status=int(status.get("httpStatus") or 0),
        )
    if state == STATE_UNCONFIGURED:
        raise AssetLibraryNotConfigured(str(status.get("reason") or "素材库未就绪"))
    raise AssetLibraryUnsupported(
        str(status.get("reason") or "这台 NewAPI 不支持素材库"),
        code="AssetLibraryUnsupported",
    )
