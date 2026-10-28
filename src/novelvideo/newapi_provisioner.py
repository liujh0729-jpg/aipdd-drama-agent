"""Provision channels and relay tokens in a user-managed LocalAPI instance."""

from __future__ import annotations

import json
import inspect
import logging
import os
import secrets
import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

import httpx

# [aipdd fork delta] 分类真源：视觉候选资格与设置页 / 模型广场同口径（单一实现）。
from novelvideo.catalog.media_model_discovery import is_vision_capable
from novelvideo.model_gateway_settings import (
    build_newapi_embedding_model_status,
    build_newapi_media_model_mappings_status,
    build_newapi_provider_channels_status,
    build_newapi_database_status,
    get_model_gateway_settings,
    normalize_channel_base_url,
    normalize_relay_base_url,
    # [aipdd fork delta] WP-A: 别名映射单一真源 + 一次性反向导入。
    backfill_newapi_model_alias_mappings,
    # [aipdd fork delta] 拍平：注册调用名 -> 归属渠道（面板显示默认模型生效渠道）。
    build_alias_channel_roles,
    # [aipdd fork delta] 拍平：渠道完整注册表（投影 / 升级迁移重投影共用真源）。
    build_channel_registrations,
    flat_registration_migration_done,
    mark_flat_registration_migrated,
    get_newapi_model_alias_mappings,
    get_newapi_media_model_mappings,
    get_newapi_provider_channel,
    get_newapi_provider_channels,
    # [aipdd fork delta] 上游真实模型名快照：直通注册与广场展示的本地数据源。
    get_upstream_catalog,
    save_upstream_models_snapshot,
    # [aipdd fork delta req43] 渠道能力绑定事实（某模型的在线绑定吃什么输入）。
    save_provider_capability_bindings,
    # [aipdd fork delta req40] 目录刷新后自动取消指向已下架模型的绑定。
    prune_stale_bindings,
    model_alias_backfill_done,
)

logger = logging.getLogger(__name__)

# [aipdd fork delta] WP-A: ComfyUI 渠道(type=63)的 mapping 由工作流真源独立管理，
# 不参与别名反向导入。
_COMFYUI_CHANNEL_TYPE = 63
_EMBEDDING_INTERNAL_ALIAS = "DC-cognee-embedding"

# [aipdd fork delta] 渠道更新（改渠道名 / 换 Key / 全量合并回写）允许透传的字段
# 集合；update_provider_channel_credentials / _merge_channel_payload 与本文件的
# 渠道改名迁移共用同一口径（LocalAPI 通用更新端点只认这些字段）。
_CHANNEL_ALLOWED_UPDATE_FIELDS: frozenset[str] = frozenset(
    {
        "id",
        "name",
        "type",
        "key",
        "base_url",
        "models",
        "group",
        "model_mapping",
        "auto_ban",
        "priority",
        "weight",
        "test_model",
        "setting",
        "settings",
        "other",
        "remark",
    }
)

PROVIDER_PRESETS: dict[str, dict[str, Any]] = {
    # [aipdd fork delta] 外部 LocalAPI 网关（用户自配的 OpenAI 兼容中转）。渠道
    # provider key 原为 openai，已全量重命名为 newapi（五渠道收敛：aipdd /
    # newapi / volcengine / comfyui / mediakit，指存储用的 provider key 集合）；
    # type=1 = OpenAI 兼容适配器。展示层只有四大渠道：mediakit（火山 AI MediaKit）
    # 归属 volcengine 渠道，凭证真源在 video-enhance 配置，不进本表。
    # base_url 留空：用户填自己的网关地址，不预填 OpenAI 官方地址。
    "newapi": {"label": "LocalAPI", "type": 1, "base_url": ""},
    # [aipdd fork delta #A] AIPDD 网关预设：OpenAI 兼容(type=1)，用户粘贴 API Key 并同步模型。
    # [aipdd fork delta req41 分类] base_url **可以预填**：AIPDD 是公共厂商渠道，
    # 地址由渠道类型唯一决定（与下面 volcengine / anthropic / api2d 同一类：拿到渠道就
    # 拿到端点）。必须留空的是 newapi 那一行——外部 LocalAPI 网关有无数个，地址是部署
    # 方自己的数据，代码猜不得。
    "aipdd": {
        "label": "AIPDD",
        "type": 1,
        "base_url": "https://api.aipdd.work/v1",
    },
    "midjourney": {
        "label": "Midjourney",
        "type": 2,
        "base_url": "https://oa.api2d.net",
    },
    "azure": {"label": "Azure", "type": 3, "base_url": ""},
    # [aipdd fork delta req41 分类] Ollama 这一行不归入「站点地址」：11434 是 Ollama
    # 自己文档化的默认监听端口，跟上游 LocalAPI 渠道表给的建议值一致，语义是
    # 「在你跑 Ollama 的那台机器上」——它本来就是本机服务，不随我们的部署变。而且
    # 这只是网关后台渠道表单里的**可改预填值**，不配也能跑。真正猜不得的是
    # newapi 那一行（外部网关有无数个）。
    "ollama": {"label": "Ollama", "type": 4, "base_url": "http://localhost:11434"},
    "midjourneyplus": {
        "label": "MidjourneyPlus",
        "type": 5,
        "base_url": "https://api.openai-sb.com",
    },
    "openaimax": {
        "label": "OpenAIMax",
        "type": 6,
        "base_url": "https://api.openaimax.com",
    },
    "ohmygpt": {"label": "OhMyGPT", "type": 7, "base_url": "https://api.ohmygpt.com"},
    "custom": {"label": "Custom", "type": 8, "base_url": ""},
    "ails": {"label": "AILS", "type": 9, "base_url": "https://api.caipacity.com"},
    "aiproxy": {"label": "AIProxy", "type": 10, "base_url": "https://api.aiproxy.io"},
    "palm": {"label": "PaLM", "type": 11, "base_url": ""},
    "api2gpt": {"label": "API2GPT", "type": 12, "base_url": "https://api.api2gpt.com"},
    "aigc2d": {"label": "AIGC2D", "type": 13, "base_url": "https://api.aigc2d.com"},
    "anthropic": {
        "label": "Anthropic",
        "type": 14,
        "base_url": "https://api.anthropic.com",
    },
    "baidu": {"label": "Baidu", "type": 15, "base_url": "https://aip.baidubce.com"},
    "zhipu": {"label": "Zhipu", "type": 16, "base_url": "https://open.bigmodel.cn"},
    "ali": {"label": "Ali", "type": 17, "base_url": "https://dashscope.aliyuncs.com"},
    "xunfei": {"label": "Xunfei", "type": 18, "base_url": ""},
    "360": {"label": "360", "type": 19, "base_url": "https://api.360.cn"},
    "openrouter": {
        "label": "OpenRouter",
        "type": 20,
        "base_url": "https://openrouter.ai/api",
    },
    "aiproxylibrary": {
        "label": "AIProxyLibrary",
        "type": 21,
        "base_url": "https://api.aiproxy.io",
    },
    "fastgpt": {
        "label": "FastGPT",
        "type": 22,
        "base_url": "https://fastgpt.run/api/openapi",
    },
    "tencent": {
        "label": "Tencent",
        "type": 23,
        "base_url": "https://hunyuan.tencentcloudapi.com",
    },
    "gemini": {
        "label": "Gemini",
        "type": 24,
        "base_url": "https://generativelanguage.googleapis.com",
    },
    "moonshot": {
        "label": "Moonshot",
        "type": 25,
        "base_url": "https://api.moonshot.cn",
    },
    "zhipuv4": {"label": "ZhipuV4", "type": 26, "base_url": "https://open.bigmodel.cn"},
    "perplexity": {
        "label": "Perplexity",
        "type": 27,
        "base_url": "https://api.perplexity.ai",
    },
    "lingyiwanwu": {
        "label": "LingYiWanWu",
        "type": 31,
        "base_url": "https://api.lingyiwanwu.com",
    },
    "aws": {"label": "AWS", "type": 33, "base_url": ""},
    "cohere": {"label": "Cohere", "type": 34, "base_url": "https://api.cohere.ai"},
    # [aipdd fork delta req46] MiniMax 双渠道：海外站 minimax + 国内站 minimax_cn。
    # 改型理由：镜像自带的 MiniMax 适配器（type 35，api.minimax.chat）只讲厂商私有
    # 协议，本 fork 的文本链路统一走 OpenAI 兼容 `/v1/chat/completions`，且渠道模型
    # 清单要靠 `{base}/v1/models` 自动发现（req40：模型名只能来自渠道目录），因此
    # 两条都登记为 type 1（OpenAI 兼容）。base_url 属「渠道定了端点就定了」的公共
    # 厂商地址，可以预填；两边 API Key 不通用，所以是两个独立 provider key。
    "minimax": {
        "label": "MiniMax",
        "type": 1,
        "base_url": "https://api.minimax.io/v1",
    },
    "minimax_cn": {
        "label": "MiniMax CN",
        "type": 1,
        "base_url": "https://api.minimax.cn/v1",
    },
    # [aipdd fork delta req48] OpenAI 官方渠道。刻意**不复用** ``openai`` 这个
    # provider key：那个键在本 fork 里已经有两处既有语义——
    #   1) 五渠道收敛时它是「外部自配 LocalAPI 网关」的旧键，启动钩子
    #      ``migrate_provider_openai_to_newapi`` 会把真源里的 provider ``openai``
    #      整体改名成 ``newapi``，网关里的 ``DC-openai`` 也会被
    #      ``rename_gateway_openai_channel_to_newapi`` 改名（按名字精确匹配
    #      ``DC-openai``，故 ``DC-openai_official`` 不受影响）；
    #   2) ``config.py`` 仍有 ``provider == "openai"`` 的环境变量直连分支
    #      （吃 OPENAI_IMAGE_MODEL / OPENAI_API_KEY 的 legacy 生图路径）。
    # 新语义压在旧键上，这两处都会把用户配好的官方渠道改姓或劫持凭证来源，
    # 因此官方站另起 ``openai_official`` 键，展示名仍是 "OpenAI"。
    # type=1：走网关的 OpenAI 兼容适配器，协议转换（chat / images / audio /
    # embeddings / video）全部由 LocalAPI 负责，平台只跟网关说话。
    "openai_official": {
        "label": "OpenAI",
        "type": 1,
        "base_url": "https://api.openai.com/v1",
    },
    "sunoapi": {"label": "SunoAPI", "type": 36, "base_url": ""},
    "dify": {"label": "Dify", "type": 37, "base_url": "https://api.dify.ai"},
    "jina": {"label": "Jina", "type": 38, "base_url": "https://api.jina.ai"},
    "cloudflare": {
        "label": "Cloudflare",
        "type": 39,
        "base_url": "https://api.cloudflare.com",
    },
    "siliconflow": {
        "label": "SiliconFlow",
        "type": 40,
        "base_url": "https://api.siliconflow.cn",
    },
    "vertexai": {"label": "VertexAI", "type": 41, "base_url": ""},
    "mistral": {"label": "Mistral", "type": 42, "base_url": "https://api.mistral.ai"},
    "deepseek": {
        "label": "DeepSeek",
        "type": 43,
        "base_url": "https://api.deepseek.com",
    },
    "mokaai": {"label": "MokaAI", "type": 44, "base_url": "https://api.moka.ai"},
    "volcengine": {
        "label": "VolcEngine",
        "type": 45,
        "base_url": "https://ark.cn-beijing.volces.com",
    },
    "baiduv2": {
        "label": "BaiduV2",
        "type": 46,
        "base_url": "https://qianfan.baidubce.com",
    },
    "xinference": {"label": "Xinference", "type": 47, "base_url": ""},
    "xai": {"label": "xAI", "type": 48, "base_url": "https://api.x.ai"},
    "coze": {"label": "Coze", "type": 49, "base_url": "https://api.coze.cn"},
    "kling": {"label": "Kling", "type": 50, "base_url": "https://api.klingai.com"},
    "jimeng": {
        "label": "Jimeng",
        "type": 51,
        "base_url": "https://visual.volcengineapi.com",
    },
    "vidu": {"label": "Vidu", "type": 52, "base_url": "https://api.vidu.cn"},
    "submodel": {
        "label": "Submodel",
        "type": 53,
        "base_url": "https://llm.submodel.ai",
    },
    "doubaovideo": {
        "label": "DoubaoVideo",
        "type": 54,
        "base_url": "https://ark.cn-beijing.volces.com",
    },
    "sora": {"label": "Sora", "type": 55, "base_url": "https://api.openai.com"},
    "replicate": {
        "label": "Replicate",
        "type": 56,
        "base_url": "https://api.replicate.com",
    },
    "codex": {"label": "Codex", "type": 57, "base_url": "https://chatgpt.com"},
}


@dataclass(frozen=True)
class NewApiProvisionerConfig:
    admin_base_url: str
    sql_dsn: str
    sqlite_path: str
    admin_username: str
    init_timeout_ms: int
    relay_token_name: str


@dataclass(frozen=True)
class AdminToken:
    admin_user_id: int
    admin_username: str
    access_token: str
    token_created: bool


@dataclass(frozen=True)
class NewApiSetupCredentials:
    username: str = ""
    password: str = ""
    confirm_password: str = ""
    self_use_mode_enabled: bool = True
    demo_site_enabled: bool = False


@dataclass(frozen=True)
class NewApiSetupStatus:
    initialized: bool
    root_initialized: bool
    database_type: str = ""
    setup_performed: bool = False
    already_initialized: bool = False


class ServiceControlEgressDenied(PermissionError):
    """Stable denial for an invalid LocalAPI management service boundary."""

    code = "ORG_SERVICE_EGRESS_DENIED"

    def __init__(self) -> None:
        super().__init__(
            "ORG_SERVICE_EGRESS_DENIED: model gateway management is only available in CE"
        )


class ServiceOperationNotReplayable(RuntimeError):
    """Raised when a durable service operation was already claimed."""


class ServiceInvocationFailed(RuntimeError):
    """Secret-free failure after a claimed service invocation."""

    def __init__(self) -> None:
        super().__init__("service operation failed")


@dataclass(frozen=True, slots=True)
class NewApiAdminServiceIdentity:
    """Secret-free identity for the LocalAPI management plane."""

    credential_id: str
    credential_version: int
    admin_base_url: str

    def __post_init__(self) -> None:
        if type(self.credential_id) is not str or not self.credential_id.strip():
            raise ValueError("credential_id is required")
        if type(self.credential_version) is not int or self.credential_version < 1:
            raise ValueError("credential_version must be positive")
        parsed = urlparse(normalize_admin_base_url(self.admin_base_url))
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("admin_base_url must be an HTTP service URL")


# [aipdd fork delta] 本地网关（LocalAPI）管理地址未就绪时的可执行错误。
# 早先这条只在 wait_for_newapi 里抛，但 ensure_newapi_setup 会先跑
# require_newapi_admin_service 构造 identity，空地址会先撞上
# NewApiAdminServiceIdentity 的底层通用错「must be an HTTP service URL」，
# 把可执行提示挡住——这里统一复用同一条可执行文案。
# [aipdd fork delta] 本地网关随部署自带（compose 把网关服务地址写入
# LOCALAPI_ADMIN_BASE_URL），不是让用户手动填的东西：文案不再要求用户去设置里
# 填地址，而是指向「用 Docker Compose 部署拉起网关」；只有接入自定义外部网关时
# 才需要用户填管理地址。命名统一 LocalAPI，不再出现 newapi 字样。
ADMIN_BASE_URL_UNCONFIGURED_HINT = (
    "LocalAPI 管理地址尚未就绪：本地网关随系统部署自动提供，无需手动填写地址。"
    "请用 Docker Compose 部署启动后端，让本地网关随编排一并拉起；"
    "仅当接入的是自定义外部网关时，才在「设置 → 模型与渠道」填写它的管理地址。"
)


def _is_valid_http_service_url(value: str | None) -> bool:
    parsed = urlparse(normalize_admin_base_url(value))
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def require_newapi_admin_service(
    cfg: NewApiProvisionerConfig,
    *,
    identity: NewApiAdminServiceIdentity | None = None,
    context=None,
) -> NewApiAdminServiceIdentity:
    """Resolve the internal admin identity and reject all request-scoped contexts."""

    if context is not None:
        raise ServiceControlEgressDenied()
    if identity is None:
        # 地址未配置 / 非合法 HTTP 服务时，先给可执行提示（由调用方转成 4xx），
        # 不要让 NewApiAdminServiceIdentity.__post_init__ 抛看不懂的通用错。
        if not _is_valid_http_service_url(cfg.admin_base_url):
            raise ValueError(ADMIN_BASE_URL_UNCONFIGURED_HINT)
    resolved = identity or NewApiAdminServiceIdentity(
        credential_id="svc-newapi-admin",
        credential_version=1,
        admin_base_url=cfg.admin_base_url,
    )
    if type(resolved) is not NewApiAdminServiceIdentity or normalize_admin_base_url(
        resolved.admin_base_url
    ) != normalize_admin_base_url(cfg.admin_base_url):
        raise ServiceControlEgressDenied()
    return resolved


async def run_newapi_admin_operation(
    *,
    identity: NewApiAdminServiceIdentity,
    admin_base_url: str,
    capability: str,
    business_task_id: str,
    request: Any,
    operations,
    invoke,
    context=None,
) -> Any:
    """Run one claimed LocalAPI management mutation under its service identity."""

    from novelvideo.egress_context import TrustedEgressContext
    from novelvideo.ports.egress_operations import (
        HandleKind,
        OperationSpec,
        canonical_request_digest,
        record_unknown_outcome,
    )

    requested_base = normalize_admin_base_url(admin_base_url)
    identity_base = normalize_admin_base_url(
        identity.admin_base_url if type(identity) is NewApiAdminServiceIdentity else ""
    )
    if (
        type(identity) is not NewApiAdminServiceIdentity
        or context is not None
        or requested_base != identity_base
        or type(capability) is not str
        or not capability.startswith("gateway.provisioning.")
        or not business_task_id
        or not callable(getattr(operations, "claim", None))
    ):
        raise ServiceControlEgressDenied()
    if type(context) is TrustedEgressContext and context.is_organization:
        raise ServiceControlEgressDenied()

    claim = await operations.claim(
        spec=OperationSpec(
            organization_id="service:newapi-admin",
            project_id="service-control",
            root_task_id=business_task_id,
            business_task_id=business_task_id,
            capability=capability,
            credential_id=identity.credential_id,
            credential_version=identity.credential_version,
            request_digest=canonical_request_digest(request),
            handle_kind=HandleKind.NONE,
        )
    )
    if not claim.won:
        raise ServiceOperationNotReplayable("service operation already claimed")

    try:
        result = invoke()
        if inspect.isawaitable(result):
            result = await result
    except Exception:
        await record_unknown_outcome(operations, claim=claim, capability=capability)
        raise ServiceInvocationFailed() from None
    # `completed` 只能来自 `accepted`（`0039:294-338`）。原先从 `dispatching` 直跳
    # completed，真库上必抛 P0001；替身没有状态机才一直是绿的。
    accepted = await operations.mark_accepted(
        operation_id=claim.operation.operation_id,
        transition_token=claim.transition_token,
        expected_version=claim.operation.version,
        provider_job_id=None,
    )
    await operations.mark_completed(
        operation_id=accepted.operation_id,
        transition_token=claim.transition_token,
        expected_version=accepted.version,
        result_ref=None,
    )
    return result


class NewApiDB:
    def __init__(self, kind: str, conn: Any):
        self.kind = kind
        self.conn = conn

    def _sql(self, sql: str) -> str:
        if self.kind in {"postgres", "mysql"}:
            return sql.replace("?", "%s")
        return sql

    def query(self, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        cursor = self.conn.cursor()
        try:
            cursor.execute(self._sql(sql), params)
            columns = [item[0] for item in cursor.description or []]
            return [dict(zip(columns, row)) for row in cursor.fetchall()]
        finally:
            cursor.close()

    def exec(self, sql: str, params: tuple[Any, ...] = ()) -> None:
        cursor = self.conn.cursor()
        try:
            cursor.execute(self._sql(sql), params)
            self.conn.commit()
        finally:
            cursor.close()

    def close(self) -> None:
        self.conn.close()


def env_bool(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def provisioner_enabled() -> bool:
    from novelvideo.shared.runtime_env import is_ce_effective

    return is_ce_effective() and env_bool("LOCALAPI_PROVISIONER_ENABLED", True)


def require_provisioner_enabled() -> None:
    if not provisioner_enabled():
        raise PermissionError("LOCALAPI_PROVISIONER_ENABLED is not enabled")


def normalize_admin_base_url(value: str | None) -> str:
    base = str(value or "").strip().rstrip("/")
    if not base:
        return ""
    # [aipdd fork delta] 允许只填 host:port（漏了协议）：补 http:// 再走后续判断，
    # 否则 NewApiAdminServiceIdentity 会抛「must be an HTTP service URL」这种用户
    # 看不懂的内部错误。管理面是内网 HTTP 服务，缺协议一律按 http 处理。
    if "://" not in base:
        base = f"http://{base}"
    if base.endswith("/v1"):
        base = base[:-3].rstrip("/")
    return base


def _status_reason(status_code: int) -> str:
    """把常见状态码翻成一句可执行判据（只解释含义，不替用户猜配置）。

    状态码本身不足以定位问题，所以这里只给「下一步查什么」的方向，具体原因靠同条
    消息里附的上游响应原文。
    """

    return {
        401: "鉴权失败：Key 无效/已禁用/过期，或该地址不是网关的 API 根",
        403: "地址可达但拒绝访问：Key 无此端点权限或站点有 CDN/WAF 拦截",
        404: "端点不存在：地址或版本路径不对（裸域名会被归一成 /v1）",
    }.get(int(status_code), "上游拒绝该请求")


def mask_token(token: str) -> str:
    clean = str(token or "").strip()
    if not clean:
        return ""
    if len(clean) <= 10:
        return "*" * len(clean)
    return f"{clean[:4]}...{clean[-4:]}"


def full_api_key(key: str) -> str:
    clean = key.strip()
    if clean.startswith("sk-"):
        return clean
    return f"sk-{clean}"


def random_token(length: int = 32) -> str:
    alphabet = "0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"
    return "".join(secrets.choice(alphabet) for _ in range(length))


def normalize_group(group: str | None) -> str:
    parts = [
        item.strip() for item in str(group or "default").split(",") if item.strip()
    ]
    unique = list(dict.fromkeys(parts or ["default"]))
    return f",{','.join(unique)},"


def validate_model_mapping(model_mapping: dict[str, str]) -> list[str]:
    if not isinstance(model_mapping, dict) or not model_mapping:
        raise ValueError("modelMapping must be a non-empty JSON object")
    keys: list[str] = []
    for key, value in model_mapping.items():
        clean_key = str(key or "").strip()
        if not clean_key:
            raise ValueError("modelMapping contains an empty model name")
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"modelMapping.{clean_key} must be a non-empty string")
        keys.append(clean_key)
    return keys


# [aipdd fork delta] WP-B: 上游模型清单拉取（带 TTL 缓存）+ 别名目标存在性/能力
# 校验。根因是 250828 一类误配：别名 target 写了一个上游根本不存在的名字，
# 或把纯文本模型写进了视觉位，运行期才在报文中爆出来。

# 拉不到清单时不阻断保存（降级为仅格式校验）；缓存只对同一 provider+endpoint 复用。
DEFAULT_UPSTREAM_MODELS_TTL_SECONDS = 600.0
DEFAULT_UPSTREAM_MODELS_TIMEOUT_SECONDS = 20.0

# [aipdd fork delta req40] 缓存从「名字数组」升级为「结构化目录条目」：同一次
# 拉取既要给候选列名字，也要带 kind / 端点 / 维度 / 能力档位。
_upstream_models_cache: dict[str, tuple[float, tuple[dict[str, Any], ...]]] = {}
_upstream_models_lock = threading.Lock()

# [aipdd fork delta] 视觉候选资格正则已收敛到 media_model_discovery.is_vision_capable
# （单一真源：与设置页 VISION_CAPABLE_RE / 模型广场共用同一实现，含 doubao-seed
# 多模态系列与 (?![a-zA-Z]) 尾部护栏）；本模块只通过 model_supports_vision 委托。


def resolve_models_endpoint(base_url: str, provider: str = "") -> str:
    """Return the /models-ready endpoint for *base_url*.

    统一给「面板拉取上游模型」与「保存期校验」用，避免两套拼接规则漂移。
    [aipdd fork delta] 拼接规则收敛到 model_gateway_settings.normalize_channel_base_url
    （渠道保存 / 读取期同一实现）；这里只保留空 base 的历史兜底。
    """

    stripped = str(base_url or "").strip().rstrip("/")
    normalized = normalize_channel_base_url(provider, stripped)
    return normalized or f"{stripped}/v1"


def _upstream_models_ttl_seconds() -> float:
    raw = str(os.environ.get("LOCALAPI_UPSTREAM_MODELS_TTL_SECONDS", "")).strip()
    try:
        return max(0.0, float(raw)) if raw else DEFAULT_UPSTREAM_MODELS_TTL_SECONDS
    except ValueError:
        return DEFAULT_UPSTREAM_MODELS_TTL_SECONDS


def parse_upstream_catalog(payload: Any) -> list[dict[str, Any]]:
    """[aipdd fork delta req40] OpenAI 兼容 /models 响应 → 结构化目录条目。

    真源在 :mod:`novelvideo.catalog.upstream_catalog`：吸收渠道在每个条目上声明的
    kind / 端点 / 模态 / 向量维度 / available / 能力档位，并且只保留渠道真的在提
    供的模型（available=false 直接丢弃）。平台不预先知道渠道有什么模型。
    """

    from novelvideo.catalog.upstream_catalog import parse_catalog_response

    return parse_catalog_response(payload)


def parse_upstream_model_ids(payload: Any) -> list[str]:
    """OpenAI 兼容 /models 响应（data/models + id/model）→ 排序去重的模型名。"""

    from novelvideo.catalog.upstream_catalog import catalog_names

    return catalog_names(parse_upstream_catalog(payload))


def upstream_models_cache_key(base_url: str, provider: str = "") -> str:
    return resolve_models_endpoint(base_url, provider)


def reset_upstream_models_cache() -> None:
    with _upstream_models_lock:
        _upstream_models_cache.clear()


# [aipdd fork delta req48] 三家厂商的原生「列模型」接口与 OpenAI 兼容只差**鉴权
# 头**与**响应外壳**（端点路径已由 normalize_channel_base_url 归一：anthropic 补
# /v1、gemini 补 /v1beta、openai_official 补 /v1，再统一拼 /models）。拉取链路只
# 有 :func:`list_upstream_catalog` 一处，所以差异也只在这一处按 provider 分支，不
# 另开第二条拉取链（否则快照、绑定修剪、重投影都会分家）。
_ANTHROPIC_VERSION = "2023-06-01"


def catalog_request_headers(provider: str, upstream_key: str) -> dict[str, str]:
    """厂商清单端点的鉴权头（三家都是 GET，无 body）。

    - anthropic：官方 ``GET /v1/models`` 要 ``x-api-key`` + ``anthropic-version``。
    - gemini：``x-goog-api-key``（Google API 家族的统一形态），Bearer 不被接受。
    - 其余（openai_official / newapi / aipdd / minimax…）：OpenAI 兼容 Bearer。
    """

    provider_key = str(provider or "").strip().lower()
    headers = {"Accept": "application/json"}
    if provider_key == "anthropic":
        headers["x-api-key"] = upstream_key
        headers["anthropic-version"] = _ANTHROPIC_VERSION
        return headers
    if provider_key == "gemini":
        headers["x-goog-api-key"] = upstream_key
        return headers
    headers["Authorization"] = f"Bearer {upstream_key}"
    return headers


# Gemini ``GET /v1beta/models`` 每条模型带 ``supportedGenerationMethods``：那是厂
# 商真的为该模型开放的调用方法，比按名字猜可靠，所以当真能力用（映射成平台侧要
# 打的 OpenAI 兼容相对路径，后续调用面只读目录声明的 endpoints）。
_GEMINI_METHOD_ROUTES: dict[str, tuple[str, str]] = {
    "generatecontent": ("chat/completions", ""),
    "streamgeneratecontent": ("chat/completions", ""),
    "embedcontent": ("embeddings", "embedding"),
    "embedtext": ("embeddings", "embedding"),
    "generatetextembedding": ("embeddings", "embedding"),
    "generateimages": ("images/generations", "image"),
    "predict": ("images/generations", "image"),
    "predictlongrunning": ("video/generations", "video"),
}


def _gemini_catalog_items(payload: Any) -> list[dict[str, Any]]:
    """Gemini 原生 ``{models: [{name: "models/x", supportedGenerationMethods}]}`` →
    OpenAI 兼容条目 ``{id, name, aipdd: {kind, endpoint, display_name}}``。

    为什么必须转：:func:`novelvideo.catalog.upstream_catalog.parse_catalog_response` 只收带
    ``id``/``model`` 的 dict（只带 ``name`` 的 dict 被当成已归一的快照形态而在解析
    上游这一步丢弃），Gemini 原生条目只有 ``name`` 且带 ``models/`` 资源前缀 —— 不
    转就是一个模型都拉不到。前缀必须剥掉：那是 REST 资源路径而不是模型名，网关拼
    ``:generateContent`` 用的是裸名。

    ``kind`` 只在能确定分类时给（embedding / image / video）：``generateContent``
    同时承载文本与图片输出（gemini-2.5-flash-image），拿它定 kind 会把生图模型钉
    成文本模型；留空 kind 时只声明端点，分类回到既有的名字推断口径。
    """

    raw_items = payload.get("models") if isinstance(payload, dict) else None
    if not isinstance(raw_items, list):
        return []
    items: list[dict[str, Any]] = []
    for raw in raw_items:
        if not isinstance(raw, dict):
            continue
        resource = str(raw.get("name") or raw.get("id") or "").strip()
        if not resource:
            continue
        model_id = resource.split("/")[-1].strip()
        if not model_id:
            continue
        methods = [
            str(method or "").strip().lower()
            for method in (raw.get("supportedGenerationMethods") or [])
            if str(method or "").strip()
        ]
        endpoints: list[str] = []
        kind = ""
        for method in methods:
            route = _GEMINI_METHOD_ROUTES.get(method)
            if not route:
                continue
            path, media_kind = route
            if path not in endpoints:
                endpoints.append(path)
            if media_kind and not kind:
                kind = media_kind
        display_name = str(raw.get("displayName") or "").strip()
        entry: dict[str, Any] = {"id": model_id}
        if display_name:
            entry["name"] = display_name
        if endpoints:
            meta: dict[str, Any] = {"endpoint": endpoints}
            if kind:
                meta["kind"] = kind
            if display_name:
                meta["display_name"] = display_name
            entry["aipdd"] = meta
        items.append(entry)
    return items


def adapt_catalog_payload(provider: str, payload: Any) -> Any:
    """厂商原生清单响应 → OpenAI 兼容外壳（再交 :func:`parse_upstream_catalog`）。

    除 Gemini 外，Anthropic 的 ``{data: [{id, display_name}]}`` 与 OpenAI 的
    ``{data: [{id, name}]}`` 本来就在解析器的兼容面里，不需要重包 —— 不为不存在
    的要求再造第二套归一逻辑。
    """

    provider_key = str(provider or "").strip().lower()
    if provider_key == "gemini":
        items = _gemini_catalog_items(payload)
        if not items:
            return payload
        return {"data": items}
    return payload


def list_upstream_catalog(
    provider: str,
    *,
    base_url: str,
    upstream_key: str,
    force: bool = False,
    timeout: float | None = None,
) -> tuple[list[dict[str, Any]], str]:
    """[aipdd fork delta req40] 拉某 provider 的上游**目录**，返回 ``(entries, error)``。

    这是拉取链路的唯一实现：命中 TTL 缓存时不发请求，成功拉到的目录同时落成快
    照（按渠道整体覆盖）。``error`` 非空表示远程不可用（无 key / 超时 / 上游报
    错），调用方应降级而不是阻断保存。
    """
    base = str(base_url or "").strip().rstrip("/")
    key = str(upstream_key or "").strip()
    if not base:
        return [], "baseUrl is required"
    if not key:
        return [], f"upstreamKey is required for provider {provider}"
    cache_key = upstream_models_cache_key(base, provider)
    ttl = _upstream_models_ttl_seconds()
    now = time.monotonic()
    if not force and ttl > 0:
        with _upstream_models_lock:
            cached = _upstream_models_cache.get(cache_key)
            if cached and now - cached[0] < ttl:
                return [dict(entry) for entry in cached[1]], ""
    try:
        response = httpx.get(
            f"{cache_key}/models",
            headers=catalog_request_headers(provider, key),
            timeout=(
                DEFAULT_UPSTREAM_MODELS_TIMEOUT_SECONDS
                if timeout is None
                else float(timeout)
            ),
        )
        if response.status_code >= 400:
            # [aipdd fork delta] 不能只把 HTTPStatusError 的 "Client error '401 …'" 往上
            # 抛：LocalAPI 系网关在这一步回的是 `{"error":{"message":"Invalid token …"}}`，
            # 那半句才是「令牌错 / 分组没权限 / 地址不是 API 根」的唯一判据。缺了它，
            # 用户只能看到「401」这一结果，无法知道该动哪里（排查成本全压在日志上）。
            # 原文截断到 200：诊断够用，又不把大段 HTML 塞进响应与日志。
            raise RuntimeError(
                f"HTTP {response.status_code}"
                f" [上游返回] {_status_reason(response.status_code)}"
                f"｜{response.text[:200].strip()}"
            )
        entries = parse_upstream_catalog(
            adapt_catalog_payload(provider, response.json())
        )
    except Exception as exc:  # noqa: BLE001 - 校验降级，不把远程故障当用户错误
        # 日志必须带**实际打出去的端点与掩码 Key**：界面上报的错若不说明「用的哪把
        # Key、哪个地址」，用户改对了配置也看不出来（实测踩坑：库里存的是 A 站地址，
        # 用户以为自己填的是 B 站，两边各执一词、无从判定）。
        logger.warning(
            "[aipdd fork delta] WP-B: upstream model listing failed for %s "
            "(endpoint=%s, key=%s): %s",
            provider,
            f"{cache_key}/models",
            mask_token(key),
            exc,
        )
        return [], str(exc)
    if not entries:
        # 空目录既可能是上游真的没模型，也可能是响应结构不符；不写缓存以免长期误判。
        return [], "upstream returned an empty model list"
    if ttl > 0:
        with _upstream_models_lock:
            _upstream_models_cache[cache_key] = (now, tuple(entries))
    # [aipdd fork delta] 成功拉到的目录落一份本地快照：网关注册、候选展示与
    # 默认值有效性判定在「永不远程」前提下都读这份快照（按渠道整体覆盖）。
    save_upstream_models_snapshot(provider, entries, base_url=base)
    return [dict(entry) for entry in entries], ""


def list_upstream_models(
    provider: str,
    *,
    base_url: str,
    upstream_key: str,
    force: bool = False,
    timeout: float | None = None,
) -> tuple[list[str], str]:
    """兼容入口：拉目录并只回名字（``(models, error)``）。

    [aipdd fork delta req40] 名字视图保留给旧调用面；需要 kind / 端点 / 维度时请
    用 :func:`list_upstream_catalog`。
    """
    entries, error = list_upstream_catalog(
        provider,
        base_url=base_url,
        upstream_key=upstream_key,
        force=force,
        timeout=timeout,
    )
    if error:
        return [], error
    from novelvideo.catalog.upstream_catalog import catalog_names

    return catalog_names(entries), ""


def refresh_provider_capability_bindings(provider: str) -> dict[str, Any]:
    """[aipdd fork delta req43] 拉并落盘某渠道的视频能力绑定事实。

    为什么需要它：``/v1/models`` 的静态声明与「这次提交真正会跑到哪条工作流」不是一回事。
    AIPDD 在 req43 当时给 ``ap-minimax-h3`` 静态声明了 ``supportsReferenceVideo: true``，而它的
    在线绑定入参里根本没有参考视频节点（同一个键 2026-10-08 复拉已翻成 ``false`` 并配
    ``maxVideoInputs: 0`` —— 布尔会漂，所以参考上限只照数量申报取）；同一个模型名下还挂着
    几条入参集完全不同的绑定（文生 / 首尾帧 / ref2va，其中两条连渠道自己写的 taskKind 都
    一模一样）。所以「这条路径递不递得进去」只能按**提交模式 → 具体绑定**逐条问能力接口，
    不能按模型名取并集下结论。

    【req44 更正的口径】绑定不足只说明「该提交模式要走的这条绑定没接这个入口」，**不等于
    模型没这个能力**，更不是把目录里的参考上限归零的理由 —— 那是拿渠道接线的完备程度当用户
    的能力边界。能力接口是「这条工作流吃什么」的权威面，不是「这个模型能做什么」的天花板。

    口径：任何失败（不暴露该接口 / 超时 / 鉴权 / 空清单）都只回 ``ok=False`` 并且
    **不写库** —— 没读到事实就是「不表态」，不能反过来把「没绑定」当成「都不吃」。
    """

    from novelvideo.catalog.upstream_capabilities import (
        fetch_upstream_capabilities,
        summarize_video_bindings,
    )

    provider_key = str(provider or "").strip().lower()
    if not provider_key:
        return {"provider": "", "ok": False, "error": "provider is required", "count": 0}
    saved = next(
        (
            channel
            for channel in get_newapi_provider_channels()
            if channel["provider"] == provider_key
        ),
        {},
    )
    base_url = str(saved.get("baseUrl") or "").strip()
    upstream_key = str(saved.get("upstreamKey") or "").strip()
    if not base_url or not upstream_key:
        return {"provider": provider_key, "ok": False, "error": "channel not configured", "count": 0}
    entries, error = fetch_upstream_capabilities(base_url, upstream_key)
    if error:
        return {"provider": provider_key, "ok": False, "error": error, "count": 0}
    bindings = summarize_video_bindings(entries)
    if not bindings:
        # 该渠道暴露了能力接口但里面没有视频绑定：同样不表态。渠道也可能只是把视频
        # 模型代理给第三方（不在自托管绑定清单里），此时判成「都不吃」会误伤。
        return {"provider": provider_key, "ok": True, "count": 0, "skipped": "no-video-bindings"}
    save_provider_capability_bindings(provider_key, bindings, base_url=base_url)
    return {"provider": provider_key, "ok": True, "count": len(bindings)}


def list_provider_upstream_catalog(
    provider: str,
    *,
    force: bool = False,
    timeout: float | None = None,
) -> tuple[list[dict[str, Any]], str]:
    """按已存渠道配置（baseUrl + upstreamKey）拉上游目录，不要求调用方重复传 Key。"""
    provider_key = str(provider or "").strip().lower()
    saved = next(
        (
            channel
            for channel in get_newapi_provider_channels()
            if channel["provider"] == provider_key
        ),
        {},
    )
    if not saved:
        return [], f"provider {provider_key} is not configured"
    return list_upstream_catalog(
        provider_key,
        base_url=str(saved.get("baseUrl") or ""),
        upstream_key=str(saved.get("upstreamKey") or ""),
        force=force,
        timeout=timeout,
    )


def list_provider_upstream_models(
    provider: str,
    *,
    force: bool = False,
    timeout: float | None = None,
) -> tuple[list[str], str]:
    """按已存渠道配置拉上游清单的名字视图（兼容旧调用面）。"""
    entries, error = list_provider_upstream_catalog(
        provider, force=force, timeout=timeout
    )
    if error:
        return [], error
    from novelvideo.catalog.upstream_catalog import catalog_names

    return catalog_names(entries), ""


# [aipdd fork delta] ComfyUI 渠道(type=63)没有 OpenAI 兼容的上游清单，其模型名
# 由工作流真源独立管理，永远不参与直通注册（也免去每次投影的无谓远程拉取）。
_PASSTHROUGH_EXCLUDED_PROVIDERS = frozenset({"comfyui"})


def _declared_passthrough_models(provider: str) -> list[str]:
    """[aipdd fork delta req40 已停用] 旧「配置声明」视图，仅留给健康检查展示。

    它把配置里引用的模型名当作事实参与注册，正是「已从网关摘掉的模型还会被调
    用」的成因：只要还有功能位把已下架模型写成 target，投影就会把它重新注册回
    网关。目录（实拉 / 快照）才是模型名的唯一来源，本函数不再被任何注册 / 展示
    链路调用。
    """

    from novelvideo.model_gateway_settings import provider_declared_models

    return sorted(provider_declared_models(provider))


def upstream_passthrough_models(
    provider: str,
    *,
    timeout: float | None = None,
    from_snapshot: bool = False,
) -> list[str]:
    """[aipdd fork delta] 某渠道名下可用的上游模型名清单（= 目录名字视图）。

    [aipdd fork delta req40] 口径收敛为**只从目录编译**：投影（写网关）场景实时
    拉取（TTL 缓存 + 可选超时，拉到即按渠道整体覆盖快照），展示场景传
    ``from_snapshot=True`` 只读本地快照（零远程、不阻塞请求线程）。

    以前这里还会并入「配置声明的真名」—— 摘掉必复活的根源。现在渠道拉不到它，
    它就不存在。含逗号的名字会破坏渠道 models 字段（逗号分隔）与 mapping 的可
    解析性，剔除。
    """
    provider_key = str(provider or "").strip().lower()
    if not provider_key or provider_key in _PASSTHROUGH_EXCLUDED_PROVIDERS:
        return []
    if from_snapshot:
        entries = get_upstream_catalog(provider_key)
    else:
        pulled, error = list_provider_upstream_catalog(provider_key, timeout=timeout)
        # 拉失败退回本地快照：一次网络抖动不能把已确认在册的目录整批剪掉；
        # 快照为空就是「还没同步过」，不猜。
        entries = pulled if not error else get_upstream_catalog(provider_key)
    from novelvideo.catalog.upstream_catalog import catalog_names

    # [aipdd fork delta] 拍平：跨渠道同名由 model_gateway_settings.
    # build_channel_registrations 统一处理（复合名总是注册，裸名仅「恰好一个渠道
    # 拥有」时注册），系统不做隐式裁决。
    return [name for name in catalog_names(entries) if "," not in name]


def model_supports_vision(name: str, provider: str = "") -> bool:
    """[aipdd fork delta req40] 视觉能力判定：先看渠道目录声明，无元数据才回落关键词。

    旧实现只有关键词一条路（``is_vision_capable``），漏网面一直在（新命名风格的名字
    正则认不出来 → 视觉位被判不可用 / 纯文本模型被判能用）。渠道目录自带
    ``modalities.input`` 时以声明为准，关键词只在 ``inferred`` 条目（通用 OpenAI 兼容
    渠道没写元数据）上当兜底。
    """

    from novelvideo.model_gateway_settings import upstream_catalog_entry
    from novelvideo.catalog.upstream_catalog import entry_has_declared_modality, supports_vision

    entry = upstream_catalog_entry(name, provider)
    if entry is not None and entry_has_declared_modality(entry):
        return supports_vision(entry)
    return is_vision_capable(name)


class AliasTargetValidationError(ValueError):
    """别名目标不存在 / 能力错配。``failures`` 带候选模型，供接口回给用户。"""

    def __init__(self, message: str, failures: list[dict[str, Any]]) -> None:
        super().__init__(message)
        self.failures = failures


def validate_alias_targets(
    mappings: dict[str, Any],
    *,
    capability: str = "",
    force_refresh: bool = False,
) -> dict[str, Any]:
    """校验本次要写入真源的 alias -> target 是否在上游存在（并做能力同类匹配）。

    ``mappings`` 接受真源条目形态（``{"provider":..., "target":...}``）或裸
    ``alias -> target`` 字符串（此时 provider 由调用方先行归并，不在此校验）。
    远程拉不到清单时只记 warning 并降级（不阻断设默认）；comfyui 渠道没有 OpenAI
    兼容 /models，直接跳过。
    """
    wanted: dict[str, list[dict[str, str]]] = {}
    for alias, item in (mappings or {}).items():
        if not isinstance(item, dict):
            continue
        alias_name = str(alias or "").strip()
        provider = str(item.get("provider") or "").strip().lower()
        target = str(item.get("target") or "").strip()
        if not alias_name or not provider or not target or provider == "comfyui":
            continue
        wanted.setdefault(provider, []).append(
            {
                "alias": alias_name,
                "target": target,
                "capability": str(
                    item.get("capability") or capability or ""
                ).strip().lower(),
            }
        )
    degraded: list[str] = []
    failures: list[dict[str, Any]] = []
    checked = 0
    for provider, entries in sorted(wanted.items()):
        models, error = list_provider_upstream_models(provider, force=force_refresh)
        if error:
            degraded.append(provider)
            continue
        known = set(models)
        providers_failures: list[dict[str, Any]] = []
        for entry in entries:
            target = entry["target"]
            checked += 1
            problem = ""
            if target not in known:
                problem = "not-found"
            elif entry["capability"] == "vision" and not model_supports_vision(
                target, provider
            ):
                # 反向防线：视觉位不得写纯文本模型（上次 400「不支持图片能力」的成因）。
                problem = "vision-required"
            if not problem:
                continue
            providers_failures.append(
                {
                    "provider": provider,
                    "alias": entry["alias"],
                    "target": target,
                    "reason": problem,
                    "candidates": _candidate_models(
                        models,
                        vision_only=problem == "vision-required",
                        exclude={target.lower()},
                        provider=provider,
                    ),
                }
            )
        if providers_failures:
            failures.extend(providers_failures)
    if failures:
        raise AliasTargetValidationError(_format_alias_failure(failures), failures)
    return {"checked": checked, "degraded": degraded, "failures": []}


def _candidate_models(
    models: list[str],
    *,
    vision_only: bool = False,
    exclude: set[str] | None = None,
    limit: int = 12,
    provider: str = "",
) -> list[str]:
    skipped = exclude or set()
    candidates = [
        name
        for name in models
        if str(name).lower() not in skipped
        and (not vision_only or model_supports_vision(name, provider))
    ]
    return candidates[:limit]


def _format_alias_failure(failures: list[dict[str, Any]]) -> str:
    parts: list[str] = []
    for item in failures:
        candidates = item.get("candidates") or []
        reason = (
            "上游不支持图片能力"
            if item.get("reason") == "vision-required"
            else "上游模型清单里不存在"
        )
        text = (
            f"{item['provider']} 的 {item['alias']} → {item['target']} {reason}"
        )
        if candidates:
            text += f"；候选：{', '.join(candidates)}"
        else:
            text += "；该渠道无可用的同类模型，请先到上游确认模型名"
        parts.append(text)
    return "；".join(parts)


# [aipdd fork delta] WP-B: 启动期 / 手动一致性自检。只告警不阻断，所以结果带 TTL
# 缓存，并且只在显式要求（启动后台任务 / 健检端点）时才去拉上游清单。
DEFAULT_ALIAS_HEALTH_TTL_SECONDS = 900.0
_alias_health_cache: dict[str, Any] = {}
_alias_health_lock = threading.Lock()


def _functional_text_aliases() -> list[str]:
    """项目功能位在册的文本/视觉别名（悬空别名判定的基准集）。

    [aipdd fork delta req40] 基准集来自槽位登记表 ``model_slots.SLOTS`` —— 旧
    ``official_defaults.DEFAULT_TEXT_MODEL_BY_ENV`` 表已并入该登记表（值全是 ``DC-*``
    槽位键，不是模型名）。
    """
    from novelvideo.model_slots import SLOTS

    return sorted(
        {str(slot.alias or "").strip() for slot in SLOTS.values() if str(slot.alias or "").strip()}
    )


def _alias_health_ttl_seconds() -> float:
    raw = str(os.environ.get("LOCALAPI_ALIAS_HEALTH_TTL_SECONDS", "")).strip()
    try:
        return max(0.0, float(raw)) if raw else DEFAULT_ALIAS_HEALTH_TTL_SECONDS
    except ValueError:
        return DEFAULT_ALIAS_HEALTH_TTL_SECONDS


def peek_alias_mapping_health() -> dict[str, Any]:
    """只读快照：不做任何远程调用，供 /config 与保存链路附带。"""
    with _alias_health_lock:
        return {
            key: value
            for key, value in _alias_health_cache.items()
            if key != "computedAtMono"
        } or {"status": "pending", "checked": False}


def reset_alias_mapping_health_cache() -> None:
    with _alias_health_lock:
        _alias_health_cache.clear()


def build_alias_mapping_health(*, force: bool = False) -> dict[str, Any]:
    """比对别名真源与上游清单，输出三类健度：悬空别名 / 失效目标 / 能力错配。

    返回体永不抛异常：拉不到上游清单的渠道归到 ``degraded``，当轮不判定其目标。
    """
    ttl = _alias_health_ttl_seconds()
    now = time.monotonic()
    if not force and ttl > 0:
        with _alias_health_lock:
            cached = dict(_alias_health_cache)
        if cached.get("checked") and now - float(cached.get("computedAtMono") or 0) < ttl:
            return {key: value for key, value in cached.items() if key != "computedAtMono"}

    mappings = get_newapi_model_alias_mappings()
    channels = {channel["provider"]: channel for channel in get_newapi_provider_channels()}
    result: dict[str, Any] = {
        "status": "ok",
        "checked": True,
        "source": "project",
        "total": len(mappings),
        "unmapped": [],
        "staleTargets": [],
        "capabilityMismatch": [],
        "noChannel": [],
        "degraded": [],
        "aliases": {},
    }
    for alias in _functional_text_aliases():
        if alias not in mappings:
            result["unmapped"].append(alias)
    for provider, channel in sorted(channels.items()):
        if provider == "comfyui":
            continue
        if not str(channel.get("upstreamKey") or "").strip():
            result["noChannel"].append(provider)
    for alias, entry in sorted(mappings.items()):
        provider = str(entry.get("provider") or "").strip().lower()
        target = str(entry.get("target") or "").strip()
        capability = str(entry.get("capability") or "").strip().lower()
        status = "mapped"
        if provider not in channels:
            status = "noChannel"
            result["noChannel"].append(provider)
        result["aliases"][alias] = {
            "status": status,
            "provider": provider,
            "target": target,
            "capability": capability,
        }
    result["noChannel"] = sorted(set(result["noChannel"]))
    for provider in sorted(
        {
            str(entry.get("provider") or "").strip().lower()
            for entry in mappings.values()
            if str(entry.get("provider") or "").strip().lower()
        }
    ):
        models, error = list_provider_upstream_models(provider)
        if error:
            result["degraded"].append({"provider": provider, "error": error})
            continue
        known = set(models)
        for alias, entry in sorted(mappings.items()):
            if str(entry.get("provider") or "").strip().lower() != provider:
                continue
            target = str(entry.get("target") or "").strip()
            item = result["aliases"].get(alias) or {}
            if target and target not in known:
                item["status"] = "stale"
                result["staleTargets"].append(
                    {
                        "alias": alias,
                        "provider": provider,
                        "target": target,
                        "candidates": _candidate_models(
                            models, exclude={target.lower()}
                        ),
                    }
                )
            elif (
                str(entry.get("capability") or "").strip().lower() == "vision"
                and target
                and not model_supports_vision(target)
            ):
                item["status"] = "capabilityMismatch"
                result["capabilityMismatch"].append(
                    {
                        "alias": alias,
                        "provider": provider,
                        "target": target,
                        "candidates": _candidate_models(models, vision_only=True),
                    }
                )
            result["aliases"][alias] = item
    if (
        result["unmapped"]
        or result["staleTargets"]
        or result["capabilityMismatch"]
        or result["noChannel"]
    ):
        result["status"] = "warning"
    elif result["degraded"]:
        result["status"] = "degraded"
    result["generatedAt"] = time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime())
    with _alias_health_lock:
        _alias_health_cache.clear()
        _alias_health_cache.update(result)
        _alias_health_cache["computedAtMono"] = now
    return result


def _first_non_empty(*values: str | None) -> str:
    for value in values:
        clean = str(value or "").strip()
        if clean:
            return clean
    return ""


def _ce_sqlite_path() -> str:
    """Return the managed CE LocalAPI SQLite database path."""
    from novelvideo.config import STATE_DIR

    target = Path(STATE_DIR) / "localapi" / "localapi.db"
    legacy = Path(STATE_DIR) / "newapi" / "one-api.db"
    if not target.exists() and legacy.exists():
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            legacy.replace(target)
        except OSError:
            pass
    return str(target)


def get_provisioner_config(
    admin_base_url: str | None = None,
    *,
    sql_dsn: str | None = None,
    sqlite_path: str | None = None,
    admin_username: str | None = None,
    init_timeout_ms: int | None = None,
) -> NewApiProvisionerConfig:
    settings = get_model_gateway_settings()
    request_sql_dsn = str(sql_dsn or "").strip()
    saved_sql_dsn = str(settings.get("custom_newapi_db_sql_dsn", "")).strip()
    env_sql_dsn = str(os.environ.get("LOCALAPI_SQL_DSN", "")).strip()
    if request_sql_dsn:
        resolved_sql_dsn = request_sql_dsn
        resolved_sqlite_path = str(sqlite_path or "").strip()
    elif saved_sql_dsn:
        resolved_sql_dsn = saved_sql_dsn
        resolved_sqlite_path = str(
            settings.get("custom_newapi_db_sqlite_path", "")
        ).strip()
    else:
        resolved_sql_dsn = env_sql_dsn or "local"
        resolved_sqlite_path = (
            str(os.environ.get("LOCALAPI_SQLITE_PATH", "")).strip() or _ce_sqlite_path()
        )
    return NewApiProvisionerConfig(
        admin_base_url=normalize_admin_base_url(
            _first_non_empty(
                admin_base_url,
                settings.get("custom_newapi_admin_base_url"),
                os.environ.get("LOCALAPI_ADMIN_BASE_URL", ""),
                # [aipdd fork delta req41] 旧这里还有一个 ``http://127.0.0.1:3000`` 兜底，
                # 等于假定网关就在宿主机回环。网关在哪是部署形态数据（compose 里是
                # ``http://newapi:3000``，分开机 / 反代又不同），代码不猜：三级来源都空就
                # 报「未配置」（wait_for_newapi 会抛可执行错误）。
            )
        ),
        sql_dsn=resolved_sql_dsn,
        sqlite_path=resolved_sqlite_path,
        admin_username=(
            _first_non_empty(
                admin_username,
                settings.get("custom_newapi_admin_username"),
                os.environ.get("LOCALAPI_ADMIN_USERNAME", "root"),
            )
            or "root"
        ),
        # 等待窗口默认走部署流程的 2 分钟语义；发现类调用方（媒体模型目录的
        # best-effort 探测）可通过 init_timeout_ms 传一个秒级预算，避免
        # "等待 DB 就绪" 的重试循环在不可用环境里长时间占用调用线程。
        init_timeout_ms=int(
            init_timeout_ms
            if init_timeout_ms is not None
            else os.environ.get("LOCALAPI_PROVISIONER_INIT_TIMEOUT_MS", "120000")
        ),
        relay_token_name=(
            os.environ.get("LOCALAPI_RELAY_TOKEN_NAME", "dramaclaw-ce-runtime").strip()
            or "dramaclaw-ce-runtime"
        ),
    )


def open_newapi_db(cfg: NewApiProvisionerConfig) -> NewApiDB:
    dsn = cfg.sql_dsn.strip()
    if not dsn:
        raise RuntimeError(
            "LOCALAPI_SQL_DSN is required; set it to a Postgres/MySQL DSN, "
            "an existing SQLite DB path, or 'local' with LOCALAPI_SQLITE_PATH"
        )
    if (
        dsn == "local"
        or dsn.endswith((".db", ".sqlite", ".sqlite3"))
        or dsn.startswith("file:")
    ):
        path = cfg.sqlite_path if dsn == "local" else dsn.removeprefix("file:")
        if not path:
            raise RuntimeError(
                "LOCALAPI_SQLITE_PATH is required when LOCALAPI_SQL_DSN=local"
            )
        if not Path(path).expanduser().is_file():
            raise RuntimeError(f"LocalAPI SQLite database does not exist: {path}")
        return NewApiDB("sqlite", sqlite3.connect(path))

    if dsn.startswith(("postgresql://", "postgres://")):
        try:
            import psycopg
        except ImportError as exc:
            raise RuntimeError("missing dependency: install psycopg[binary]") from exc
        return NewApiDB("postgres", psycopg.connect(dsn))

    if dsn.startswith("mysql://"):
        try:
            import pymysql
        except ImportError as exc:
            raise RuntimeError("missing dependency: install pymysql") from exc
        parsed = urlparse(dsn)
        conn = pymysql.connect(
            host=parsed.hostname or "127.0.0.1",
            port=parsed.port or 3306,
            user=unquote(parsed.username or ""),
            password=unquote(parsed.password or ""),
            database=unquote(parsed.path.lstrip("/")),
            charset="utf8mb4",
            autocommit=False,
        )
        return NewApiDB("mysql", conn)

    raise RuntimeError(f"unsupported LOCALAPI_SQL_DSN: {dsn}")


def wait_for_db(cfg: NewApiProvisionerConfig) -> NewApiDB:
    deadline = time.monotonic() + cfg.init_timeout_ms / 1000
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            db = open_newapi_db(cfg)
            db.query("select 1")
            return db
        except Exception as exc:
            last_error = exc
            time.sleep(1.5)
    raise RuntimeError(f"database not ready: {last_error}")


def wait_for_newapi(cfg: NewApiProvisionerConfig) -> None:
    if not cfg.admin_base_url:
        # [aipdd fork delta req41] 报错要可执行：说清去哪设、两个合法入口各是什么。
        raise RuntimeError(ADMIN_BASE_URL_UNCONFIGURED_HINT)
    deadline = time.monotonic() + cfg.init_timeout_ms / 1000
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            with httpx.Client(timeout=5) as client:
                res = client.get(f"{cfg.admin_base_url}/api/setup")
            if res.status_code < 500:
                return
            last_error = RuntimeError(f"HTTP {res.status_code}")
        except Exception as exc:
            last_error = exc
        time.sleep(1.5)
    raise RuntimeError(f"LocalAPI not ready: {last_error}")


def get_newapi_setup_status(cfg: NewApiProvisionerConfig) -> NewApiSetupStatus:
    wait_for_newapi(cfg)
    with httpx.Client(timeout=10) as client:
        res = client.get(f"{cfg.admin_base_url}/api/setup")
    try:
        body: Any = res.json()
    except ValueError:
        body = res.text
    if res.status_code >= 400:
        raise RuntimeError(f"LocalAPI setup status failed: HTTP {res.status_code} {body}")
    require_newapi_success(body, "get setup status")
    data = response_data(body)
    if not isinstance(data, dict):
        raise RuntimeError(f"get setup status failed: unexpected response {body}")
    return NewApiSetupStatus(
        initialized=bool(data.get("status")),
        root_initialized=bool(data.get("root_init")),
        database_type=str(data.get("database_type") or ""),
    )


def ensure_newapi_setup(
    cfg: NewApiProvisionerConfig,
    credentials: NewApiSetupCredentials | None = None,
    *,
    service_identity: NewApiAdminServiceIdentity | None = None,
    context=None,
) -> NewApiSetupStatus:
    require_newapi_admin_service(
        cfg,
        identity=service_identity,
        context=context,
    )
    status = get_newapi_setup_status(cfg)
    if status.initialized:
        return NewApiSetupStatus(
            initialized=status.initialized,
            root_initialized=status.root_initialized,
            database_type=status.database_type,
            setup_performed=False,
            already_initialized=True,
        )

    creds = credentials or NewApiSetupCredentials()
    payload: dict[str, Any] = {
        "SelfUseModeEnabled": bool(creds.self_use_mode_enabled),
        "DemoSiteEnabled": bool(creds.demo_site_enabled),
    }
    if not status.root_initialized:
        # [aipdd fork delta] 本地网关是内部调用：fresh 且未带凭据时不再向用户索要
        # 建号信息，而是自动生成一次性 root 凭据去过 /api/setup。建完后管理接口只认
        # 直写 DB 的 access_token（ensure_admin_access_token），这个密码此后不再被
        # 使用，因而不落盘、不回显。显式传入的凭据仍优先（保留 override 路径，不改
        # 变已有成功分支的行为）。
        username = creds.username.strip() or (cfg.admin_username.strip() or "root")
        password = creds.password or random_token(24)
        confirm_password = creds.confirm_password or password
        payload.update(
            {
                "username": username,
                "password": password,
                "confirmPassword": confirm_password,
            }
        )

    with httpx.Client(timeout=15) as client:
        res = client.post(f"{cfg.admin_base_url}/api/setup", json=payload)
    try:
        body: Any = res.json()
    except ValueError:
        body = res.text
    if res.status_code >= 400:
        raise RuntimeError(f"LocalAPI setup failed: HTTP {res.status_code} {body}")
    require_newapi_success(body, "setup LocalAPI")
    final_status = get_newapi_setup_status(cfg)
    return NewApiSetupStatus(
        initialized=final_status.initialized,
        root_initialized=final_status.root_initialized,
        database_type=final_status.database_type,
        setup_performed=True,
        already_initialized=False,
    )


def find_admin_user(
    db: NewApiDB, cfg: NewApiProvisionerConfig
) -> dict[str, Any] | None:
    rows = db.query(
        "select id, username, role, status, access_token from users where username = ? limit 1",
        (cfg.admin_username,),
    )
    if rows:
        return rows[0]
    rows = db.query(
        """
        select id, username, role, status, access_token
        from users
        where role >= ?
        order by role desc, id asc
        limit 1
        """,
        (10,),
    )
    return rows[0] if rows else None


def wait_for_admin_user(db: NewApiDB, cfg: NewApiProvisionerConfig) -> dict[str, Any]:
    deadline = time.monotonic() + cfg.init_timeout_ms / 1000
    while time.monotonic() < deadline:
        user = find_admin_user(db, cfg)
        if user:
            return user
        time.sleep(1.5)
    raise RuntimeError(
        "admin user not found; initialize LocalAPI or create root user first"
    )


def admin_headers(admin: AdminToken) -> dict[str, str]:
    return {
        "Authorization": admin.access_token,
        "New-Api-User": str(admin.admin_user_id),
    }


def require_newapi_success(body: Any, action: str) -> None:
    if not isinstance(body, dict):
        raise RuntimeError(f"{action} failed: unexpected response {body!r}")
    if body.get("success") is False:
        raise RuntimeError(f"{action} failed: {body.get('message') or body}")


def response_data(body: Any) -> Any:
    if not isinstance(body, dict):
        return None
    return body.get("data", body.get("response"))


def token_items(body: Any) -> list[dict[str, Any]]:
    data = response_data(body)
    if isinstance(data, dict) and isinstance(data.get("items"), list):
        return [item for item in data["items"] if isinstance(item, dict)]
    if isinstance(body, dict) and isinstance(body.get("items"), list):
        return [item for item in body["items"] if isinstance(item, dict)]
    return []


def channel_items(body: Any) -> list[dict[str, Any]]:
    data = response_data(body)
    if isinstance(data, dict) and isinstance(data.get("items"), list):
        return [item for item in data["items"] if isinstance(item, dict)]
    if isinstance(body, dict) and isinstance(body.get("items"), list):
        return [item for item in body["items"] if isinstance(item, dict)]
    return []


def verify_admin_api(cfg: NewApiProvisionerConfig, admin: AdminToken) -> None:
    with httpx.Client(timeout=10) as client:
        res = client.get(
            f"{cfg.admin_base_url}/api/channel/",
            params={"p": 1, "page_size": 1},
            headers=admin_headers(admin),
        )
    if res.status_code == 401:
        raise RuntimeError(
            "admin access token was written, but LocalAPI rejected it with 401"
        )
    if res.status_code >= 400:
        raise RuntimeError(
            f"LocalAPI admin check failed: HTTP {res.status_code} {res.text[:300]}"
        )


def ensure_admin_access_token(cfg: NewApiProvisionerConfig) -> AdminToken:
    db = wait_for_db(cfg)
    try:
        wait_for_newapi(cfg)
        user = wait_for_admin_user(db, cfg)
        access_token = str(user.get("access_token") or "").strip()
        token_created = False
        if not access_token:
            access_token = random_token()
            db.exec(
                "update users set access_token = ? where id = ?",
                (access_token, user["id"]),
            )
            token_created = True
        admin = AdminToken(
            admin_user_id=int(user["id"]),
            admin_username=str(user["username"]),
            access_token=access_token,
            token_created=token_created,
        )
        verify_admin_api(cfg, admin)
        return admin
    finally:
        db.close()


def get_token_key(
    cfg: NewApiProvisionerConfig, admin: AdminToken, token_id: int
) -> str:
    with httpx.Client(timeout=15) as client:
        res = client.post(
            f"{cfg.admin_base_url}/api/token/{token_id}/key",
            headers=admin_headers(admin),
        )
    try:
        body: Any = res.json()
    except ValueError:
        body = res.text
    if res.status_code >= 400:
        raise RuntimeError(f"get token key failed: HTTP {res.status_code} {body}")
    require_newapi_success(body, "get token key")
    data = response_data(body)
    key = data.get("key") if isinstance(data, dict) else None
    if not isinstance(key, str) or not key.strip():
        raise RuntimeError(f"get token key failed: missing key in {body}")
    return full_api_key(key)


def find_token_by_name(
    cfg: NewApiProvisionerConfig,
    admin: AdminToken,
    name: str,
) -> dict[str, Any] | None:
    with httpx.Client(timeout=15) as client:
        res = client.get(
            f"{cfg.admin_base_url}/api/token/search",
            params={"keyword": name, "p": 1, "page_size": 100},
            headers=admin_headers(admin),
        )
    try:
        body: Any = res.json()
    except ValueError:
        body = res.text
    if res.status_code >= 400:
        raise RuntimeError(f"search token failed: HTTP {res.status_code} {body}")
    require_newapi_success(body, "search token")
    matches = [item for item in token_items(body) if item.get("name") == name]
    if not matches:
        return None
    return sorted(matches, key=lambda item: int(item.get("id") or 0), reverse=True)[0]


def create_or_reuse_relay_token(
    cfg: NewApiProvisionerConfig,
    admin: AdminToken,
    *,
    name: str,
    group: str = "default",
    unlimited_quota: bool = True,
    remain_quota: int = 0,
    expired_time: int = -1,
    reuse_existing: bool = True,
) -> dict[str, Any]:
    token_name = name.strip()
    if not token_name:
        raise ValueError("token name is required")
    existing = find_token_by_name(cfg, admin, token_name) if reuse_existing else None
    if existing:
        key = get_token_key(cfg, admin, int(existing["id"]))
        return {
            "created": False,
            "tokenId": existing["id"],
            "name": existing["name"],
            "key": key,
            "keyPreview": mask_token(key),
        }

    payload = {
        "name": token_name,
        "expired_time": int(expired_time),
        "remain_quota": int(remain_quota),
        "unlimited_quota": bool(unlimited_quota),
        "model_limits_enabled": False,
        "model_limits": "",
        "group": group.strip() or "default",
        "allow_ips": "",
        "cross_group_retry": False,
    }
    with httpx.Client(timeout=15) as client:
        res = client.post(
            f"{cfg.admin_base_url}/api/token/",
            headers=admin_headers(admin),
            json=payload,
        )
    try:
        body: Any = res.json()
    except ValueError:
        body = res.text
    if res.status_code >= 400:
        raise RuntimeError(f"create token failed: HTTP {res.status_code} {body}")
    require_newapi_success(body, "create token")

    created = find_token_by_name(cfg, admin, token_name)
    if not created:
        raise RuntimeError("token was created, but could not be found by name")
    key = get_token_key(cfg, admin, int(created["id"]))
    return {
        "created": True,
        "tokenId": created["id"],
        "name": created["name"],
        "key": key,
        "keyPreview": mask_token(key),
    }


def build_channel_payload(
    *,
    provider: str,
    channel_type: int | None = None,
    upstream_key: str,
    model_mapping: dict[str, str],
    name: str | None = None,
    group: str = "default",
    priority: int = 0,
    weight: int = 0,
    base_url: str | None = None,
    test_model: str | None = None,
    other_settings: dict[str, Any] | None = None,
) -> dict[str, Any]:
    provider_key = (provider or "").strip()
    preset = PROVIDER_PRESETS.get(provider_key)
    if not preset and channel_type is None:
        raise ValueError("unknown provider; pass type when using a custom provider")
    model_keys = validate_model_mapping(model_mapping)
    provider_label = preset["label"] if preset else f"type-{channel_type}"
    resolved_base_url = (base_url or (preset or {}).get("base_url") or "").strip()
    # [aipdd fork delta] LocalAPI relay 对上游自行拼端点路径：OpenAI 兼容类型拼
    # /v1/chat/completions，VolcEngine（type 45）适配器硬编码拼 /api/v3/...。渠道
    # base 若自带同款后缀会得到 /v1/v1/... 或 /api/v3/api/v3/...，上游回 404（火山
    # 对错误路径回空 body 404——DC-* 视觉/文本调用 404/500 的根源）。写网关前归一：
    # 先剥尾斜杠，再剥与适配器拼接路径重复的版本后缀；自有库原值不动——面板拉取
    # （resolve_models_endpoint）与媒体直连仍用带 /v1、/api/v3 的 base。
    resolved_base_url = resolved_base_url.rstrip("/")
    if resolved_base_url.endswith("/v1"):
        resolved_base_url = resolved_base_url[: -len("/v1")].rstrip("/")
    effective_channel_type = int(channel_type or preset["type"])
    if effective_channel_type == 45 and resolved_base_url.endswith("/api/v3"):
        resolved_base_url = resolved_base_url[: -len("/api/v3")].rstrip("/")
    # [aipdd fork delta req48] Gemini（type 24）适配器自己拼
    # ``{base}/v1beta/models/{model}:generateContent``，渠道 base 得是裸域——带着
    # /v1beta 写进去就变成 …/v1beta/v1beta/…，上游回 404。自有库里仍存带版本的
    # base（拉清单要用），只在写网关时剥掉，与上面 Ark 的 /api/v3 同一口径。
    if effective_channel_type == 24 and resolved_base_url.endswith("/v1beta"):
        resolved_base_url = resolved_base_url[: -len("/v1beta")].rstrip("/")
    if preset:
        channel_name = f"DC-{provider_key}"
    elif provider_key:
        channel_name = (name or f"DC-{provider_key}").strip()
    else:
        channel_name = (name or f"DC-type-{channel_type}").strip()
    resolved_upstream_key = upstream_key.strip()
    # LocalAPI's generic channel validator rejects an empty key even for ComfyUI.
    # ComfyUI itself may run without authentication, so keep the CE setting
    # empty while using a non-secret sentinel only in the LocalAPI channel row.
    if provider_key == "comfyui" and not resolved_upstream_key:
        resolved_upstream_key = "none"
    channel = {
        "name": channel_name,
        "type": effective_channel_type,
        "key": resolved_upstream_key,
        "base_url": resolved_base_url,
        "models": ",".join(model_keys),
        "group": normalize_group(group),
        "model_mapping": json.dumps(
            model_mapping, ensure_ascii=False, separators=(",", ":")
        ),
        "status": 1,
        "auto_ban": 1,
        "priority": int(priority),
        "weight": int(weight),
        "test_model": (test_model or model_keys[0]).strip(),
        "setting": "{}",
        "settings": json.dumps(
            other_settings or {}, ensure_ascii=False, separators=(",", ":")
        ),
        "other": "",
        "remark": f"created by DramaClaw CE provisioner for {provider_label}",
    }
    if not channel["key"] and provider_key != "comfyui":
        raise ValueError("upstreamKey is required")
    return {"mode": "single", "channel": channel}


def _parse_model_mapping(value: Any) -> dict[str, str]:
    if isinstance(value, dict):
        return {
            str(key).strip(): str(item).strip()
            for key, item in value.items()
            if str(key).strip() and str(item).strip()
        }
    if isinstance(value, str) and value.strip():
        try:
            decoded = json.loads(value)
        except json.JSONDecodeError:
            return {}
        return _parse_model_mapping(decoded)
    return {}


def _parse_models(value: Any) -> list[str]:
    if isinstance(value, list):
        raw_items = value
    else:
        raw_items = str(value or "").split(",")
    models: list[str] = []
    seen: set[str] = set()
    for item in raw_items:
        model = str(item or "").strip()
        if not model or model in seen:
            continue
        seen.add(model)
        models.append(model)
    return models


def _merge_channel_payload(
    existing: dict[str, Any], payload: dict[str, Any]
) -> dict[str, Any]:
    incoming = dict(payload["channel"])
    # [aipdd fork delta] 拍平改造：model_mapping 改为「替换」语义（原为非 ComfyUI
    # 渠道的只增不减 merge）。投影调用方总是提交完整注册表
    # （build_channel_registrations），替换才能让上游下架 / 改名在下一次投影时
    # 收敛删除；merge 语义会让历史键永久残留，正是同名抢注反复回流的根源之一。
    incoming_mapping = _parse_model_mapping(incoming.get("model_mapping"))
    merged_mapping = incoming_mapping
    model_keys = validate_model_mapping(merged_mapping)

    allowed_fields = _CHANNEL_ALLOWED_UPDATE_FIELDS
    merged = {
        key: value
        for key, value in {**existing, **incoming}.items()
        if key in allowed_fields
    }
    channel_id = existing.get("id")
    if channel_id is not None:
        merged["id"] = channel_id
    merged["model_mapping"] = json.dumps(
        merged_mapping,
        ensure_ascii=False,
        separators=(",", ":"),
    )
    merged["models"] = ",".join(model_keys)
    merged["test_model"] = str(merged.get("test_model") or model_keys[0]).strip()
    if not incoming.get("base_url") and existing.get("base_url"):
        merged["base_url"] = existing["base_url"]
    if not incoming.get("key") and existing.get("key"):
        merged["key"] = existing["key"]
    return {"mode": "single", "channel": merged}


def list_channels(
    cfg: NewApiProvisionerConfig,
    admin: AdminToken,
    *,
    page_size: int = 100,
    max_pages: int = 20,
) -> list[dict[str, Any]]:
    channels: list[dict[str, Any]] = []
    with httpx.Client(timeout=15) as client:
        for page in range(1, max_pages + 1):
            res = client.get(
                f"{cfg.admin_base_url}/api/channel/",
                params={"p": page, "page_size": page_size},
                headers=admin_headers(admin),
            )
            try:
                body: Any = res.json()
            except ValueError:
                body = res.text
            if res.status_code >= 400:
                raise RuntimeError(
                    f"list channels failed: HTTP {res.status_code} {body}"
                )
            require_newapi_success(body, "list channels")
            items = channel_items(body)
            channels.extend(items)
            if len(items) < page_size:
                break
    return channels


def find_channel_by_name(
    cfg: NewApiProvisionerConfig,
    admin: AdminToken,
    *,
    name: str,
    channel_type: int | None = None,
) -> dict[str, Any] | None:
    matches = [
        item
        for item in list_channels(cfg, admin)
        if str(item.get("name") or "") == name
        and (channel_type is None or int(item.get("type") or 0) == int(channel_type))
    ]
    if not matches:
        return None
    return sorted(matches, key=lambda item: int(item.get("id") or 0), reverse=True)[0]


def delete_channel_by_name(
    cfg: NewApiProvisionerConfig,
    admin: AdminToken,
    *,
    name: str,
    channel_type: int | None = None,
) -> bool:
    """Delete one managed LocalAPI channel; return False when it does not exist."""
    existing = find_channel_by_name(
        cfg,
        admin,
        name=name,
        channel_type=channel_type,
    )
    if not existing:
        return False
    channel_id = existing.get("id")
    if channel_id is None:
        raise RuntimeError(f"delete channel {name} failed: missing channel id")
    with httpx.Client(timeout=15) as client:
        res = client.delete(
            f"{cfg.admin_base_url}/api/channel/{channel_id}",
            headers=admin_headers(admin),
        )
    try:
        body: Any = res.json()
    except ValueError:
        body = res.text
    if res.status_code >= 400:
        raise RuntimeError(
            f"delete channel {name} failed: HTTP {res.status_code} {body}"
        )
    require_newapi_success(body, f"delete channel {name}")
    return True


def get_channel_detail(
    cfg: NewApiProvisionerConfig,
    admin: AdminToken,
    channel_id: int | str,
) -> dict[str, Any]:
    with httpx.Client(timeout=15) as client:
        res = client.get(
            f"{cfg.admin_base_url}/api/channel/{channel_id}",
            headers=admin_headers(admin),
        )
    try:
        body: Any = res.json()
    except ValueError:
        body = res.text
    if res.status_code >= 400:
        raise RuntimeError(f"get channel failed: HTTP {res.status_code} {body}")
    require_newapi_success(body, "get channel")
    data = response_data(body)
    if not isinstance(data, dict):
        raise RuntimeError(f"get channel failed: missing data in {body}")
    return data


def list_channel_types(
    cfg: NewApiProvisionerConfig,
    admin: AdminToken,
) -> list[dict[str, Any]]:
    """Return the channel adapters exposed by the connected LocalAPI instance."""
    with httpx.Client(timeout=15) as client:
        res = client.get(
            f"{cfg.admin_base_url}/api/channel/types",
            params={"status": 1},
            headers=admin_headers(admin),
        )
    try:
        body: Any = res.json()
    except ValueError:
        body = res.text
    if res.status_code >= 400:
        raise RuntimeError(f"list channel types failed: HTTP {res.status_code} {body}")
    require_newapi_success(body, "list channel types")
    data = response_data(body)
    raw_items = data.get("items") if isinstance(data, dict) else None
    if not isinstance(raw_items, list):
        raise RuntimeError("list channel types failed: missing data.items")

    items: list[dict[str, Any]] = []
    seen_providers: set[str] = set()
    for raw in raw_items:
        if not isinstance(raw, dict):
            continue
        provider = str(raw.get("provider") or "").strip().lower()
        channel_type = int(raw.get("type") or 0)
        if not provider or channel_type <= 0 or provider in seen_providers:
            continue
        seen_providers.add(provider)
        capabilities = raw.get("capabilities")
        items.append(
            {
                "type": channel_type,
                "provider": provider,
                "name": str(raw.get("name") or provider).strip(),
                "description": str(raw.get("description") or "").strip(),
                "icon": str(raw.get("icon") or "").strip(),
                "defaultBaseUrl": str(raw.get("default_base_url") or "").strip(),
                "status": int(raw.get("status") or 0),
                "capabilities": (
                    [
                        str(value).strip().lower()
                        for value in capabilities
                        if str(value).strip()
                    ]
                    if isinstance(capabilities, list)
                    else []
                ),
                "requiresBaseUrl": bool(raw.get("requires_base_url")),
                "supportsBaseUrlOverride": bool(raw.get("supports_base_url_override")),
            }
        )
    return sorted(items, key=lambda item: (item["type"], item["provider"]))


def update_channel(
    cfg: NewApiProvisionerConfig,
    admin: AdminToken,
    channel_id: int | str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    channel = dict(payload["channel"])
    # LocalAPI treats channel status as read-only on the general update endpoint.
    channel.pop("status", None)
    channel["id"] = channel_id
    sanitized_payload = {**payload, "channel": channel}
    attempts = [
        ("PUT", f"{cfg.admin_base_url}/api/channel/", channel),
        ("PUT", f"{cfg.admin_base_url}/api/channel/{channel_id}", channel),
        ("PUT", f"{cfg.admin_base_url}/api/channel/", sanitized_payload),
        ("PUT", f"{cfg.admin_base_url}/api/channel/{channel_id}", sanitized_payload),
    ]
    last_status = 0
    last_body: Any = None
    last_payload: dict[str, Any] | None = None
    with httpx.Client(timeout=30) as client:
        for method, url, body_payload in attempts:
            res = client.request(
                method, url, headers=admin_headers(admin), json=body_payload
            )
            try:
                body: Any = res.json()
            except ValueError:
                body = res.text
            last_status = res.status_code
            last_body = body
            last_payload = body_payload
            ok = (
                res.status_code < 400
                and isinstance(body, dict)
                and body.get("success") is not False
            )
            if ok:
                return {
                    "ok": True,
                    "httpStatus": res.status_code,
                    "newApiResponse": body,
                    "sentPayload": body_payload,
                }
            if res.status_code in {404, 405}:
                continue
            if isinstance(body, dict) and "record not found" in str(body).lower():
                continue
            return {
                "ok": False,
                "httpStatus": res.status_code,
                "newApiResponse": body,
                "sentPayload": body_payload,
            }
    return {
        "ok": False,
        "httpStatus": last_status,
        "newApiResponse": last_body,
        "sentPayload": last_payload,
    }


def create_channel(
    cfg: NewApiProvisionerConfig,
    admin: AdminToken,
    payload: dict[str, Any],
) -> dict[str, Any]:
    with httpx.Client(timeout=30) as client:
        res = client.post(
            f"{cfg.admin_base_url}/api/channel/",
            headers=admin_headers(admin),
            json=payload,
        )
    try:
        body: Any = res.json()
    except ValueError:
        body = res.text
    return {
        "ok": res.status_code < 400
        and isinstance(body, dict)
        and body.get("success") is not False,
        "httpStatus": res.status_code,
        "newApiResponse": body,
    }


def update_provider_channel_credentials(
    cfg: NewApiProvisionerConfig,
    admin: AdminToken,
    *,
    provider: str,
    channel_type: int | None = None,
    upstream_key: str,
    base_url: str | None = None,
) -> dict[str, Any]:
    provider_key = str(provider or "").strip().lower()
    preset = PROVIDER_PRESETS.get(provider_key)
    if not preset and channel_type is None:
        raise ValueError("unknown provider")
    key = str(upstream_key or "").strip()
    if not key:
        raise ValueError("upstreamKey is required")

    channel_name = f"DC-{provider_key}"
    existing = find_channel_by_name(
        cfg,
        admin,
        name=channel_name,
        channel_type=int(channel_type or preset["type"]),
    )
    if not existing:
        raise LookupError(f"LocalAPI channel {channel_name} does not exist")

    detail = get_channel_detail(cfg, admin, existing["id"])
    allowed_fields = _CHANNEL_ALLOWED_UPDATE_FIELDS
    channel = {
        field: value for field, value in detail.items() if field in allowed_fields
    }
    channel["id"] = existing["id"]
    channel["name"] = channel_name
    channel["type"] = int(channel_type or preset["type"])
    channel["key"] = key
    if base_url is not None:
        channel["base_url"] = str(base_url or "").strip().rstrip("/")

    payload = {"mode": "single", "channel": channel}
    result = update_channel(cfg, admin, existing["id"], payload)
    return {
        **result,
        "action": "update",
        "channelId": existing["id"],
        "name": channel_name,
        "provider": provider_key,
    }


def upsert_channel(
    cfg: NewApiProvisionerConfig,
    admin: AdminToken,
    payload: dict[str, Any],
    *,
    service_identity: NewApiAdminServiceIdentity | None = None,
    context=None,
) -> dict[str, Any]:
    require_newapi_admin_service(
        cfg,
        identity=service_identity,
        context=context,
    )
    channel = payload["channel"]
    # [aipdd fork delta] 拍平改造：写前仍校验 mapping 形状（空 / 非法即刻失败），
    # 但不再做跨渠道防抢注去重（remove_model_keys_from_other_dc_channels 已退役）
    # ——注册表由 build_channel_registrations 按「复合名 + 单渠道裸名」生成，天然
    # 不与其它渠道重叠；同名多渠道一律不注册裸名，无需系统裁决。
    validate_model_mapping(_parse_model_mapping(channel.get("model_mapping")))
    existing = find_channel_by_name(
        cfg,
        admin,
        name=str(channel["name"]),
        channel_type=int(channel["type"]),
    )
    if not existing:
        result = create_channel(cfg, admin, payload)
        return {**result, "action": "create", "channelId": None}

    detail = get_channel_detail(cfg, admin, existing["id"])
    update_payload = _merge_channel_payload(detail, payload)
    result = update_channel(cfg, admin, existing["id"], update_payload)
    return {
        **result,
        "action": "update",
        "channelId": existing["id"],
        "sentPayload": update_payload,
    }


# [aipdd fork delta] WP-A: 升级启动时的一次性反向导入——若项目真源还没建立，
# 从网关 DC-<provider> 渠道的 model_mapping 读回来当种子。只补不覆盖、幂等（靠
# runtime_settings 里的标记位），并只收已配置渠道的别名，排除媒体模型与 embedding
# 内部名（那两项已有自己的真源）。
def backfill_model_alias_mappings_from_gateway(
    *,
    force: bool = False,
    init_timeout_ms: int | None = None,
) -> dict[str, Any]:
    if not provisioner_enabled():
        return {"skipped": "provisioner-disabled", "imported": 0}
    if not force and model_alias_backfill_done():
        return {"skipped": "already-imported", "imported": 0}
    # [aipdd fork delta] WP-A: 与发现类调用方同一约定（见 get_provisioner_config
    # 注释）——best-effort 的启动探测可传秒级预算，避免网关未就绪时在
    # wait_for_newapi / wait_for_db 的默认 2 分钟窗口里长时间占用调用线程。
    cfg = get_provisioner_config(init_timeout_ms=init_timeout_ms)
    admin = ensure_admin_access_token(cfg)
    known_providers = {
        channel["provider"] for channel in get_newapi_provider_channels()
    }
    media_names = set(get_newapi_media_model_mappings()) | {_EMBEDDING_INTERNAL_ALIAS}
    collected: dict[str, dict[str, Any]] = {}
    for channel in list_channels(cfg, admin):
        name = str(channel.get("name") or "")
        if not name.startswith("DC-"):
            continue
        provider = name[len("DC-") :].strip().lower()
        if provider not in known_providers:
            continue
        if int(channel.get("type") or 0) == _COMFYUI_CHANNEL_TYPE:
            continue
        for alias, target in _parse_model_mapping(channel.get("model_mapping")).items():
            if alias in media_names or alias in collected:
                continue
            collected[alias] = {"provider": provider, "target": target}
    result = backfill_newapi_model_alias_mappings(collected)
    result["candidates"] = len(collected)
    if result.get("imported"):
        logger.warning(
            "[aipdd fork delta] WP-A: imported %s model alias mappings from gateway "
            "channels into project-owned store (total now %s)",
            result["imported"],
            result["total"],
        )
    return result


# [aipdd fork delta] openai→newapi 渠道重命名（网关侧，一次性）：把存量
# DC-openai 渠道（type 1，外部 LocalAPI）原 id 改名为 DC-newapi —— 只改 name/remark，
# 保留渠道 id / key / base / models / abilities，网关在跑的任务不受影响。幂等：
# DC-newapi 已存在或无 DC-openai 时直接跳过。改名后投影链（按 DC-{provider}
# 查找/创建）与别名反向导入（按 "DC-" 前缀推导 provider）与 newapi 语义自洽。
def rename_gateway_openai_channel_to_newapi(
    *,
    init_timeout_ms: int | None = None,
) -> dict[str, Any]:
    if not provisioner_enabled():
        return {"skipped": "provisioner-disabled"}
    cfg = get_provisioner_config(init_timeout_ms=init_timeout_ms)
    admin = ensure_admin_access_token(cfg)
    existing_new = find_channel_by_name(cfg, admin, name="DC-newapi", channel_type=1)
    if existing_new:
        return {"skipped": "already-renamed"}
    existing_old = find_channel_by_name(cfg, admin, name="DC-openai", channel_type=1)
    if not existing_old:
        return {"skipped": "missing-source"}
    detail = get_channel_detail(cfg, admin, existing_old["id"])
    channel = {
        key: value
        for key, value in detail.items()
        if key in _CHANNEL_ALLOWED_UPDATE_FIELDS
    }
    channel["id"] = existing_old["id"]
    channel["name"] = "DC-newapi"
    label = str((PROVIDER_PRESETS.get("newapi") or {}).get("label") or "LocalAPI")
    if "created by DramaClaw CE provisioner" in str(channel.get("remark") or ""):
        channel["remark"] = f"created by DramaClaw CE provisioner for {label}"
    result = update_channel(
        cfg, admin, existing_old["id"], {"mode": "single", "channel": channel}
    )
    renamed = bool(result.get("ok"))
    if renamed:
        logger.warning(
            "[aipdd fork delta] renamed gateway channel DC-openai (id=%s) to DC-newapi",
            existing_old["id"],
        )
    return {
        "renamed": renamed,
        "channelId": existing_old["id"],
        "httpStatus": result.get("httpStatus"),
    }


# [aipdd fork delta] 拍平迁移（远程步，一次性）：把全部已配置渠道的 model_mapping
# 重投影为拍平注册表（复合名 ``{provider}::{model}`` + 内部绑定裸名 + 单渠道唯一
# 裸名）。升级前的渠道 mapping 是旧注册口径（无复合名），用户按复合名调用会路由
# 不到；本迁移在升级后首次启动把存量渠道收敛到新口径，全部渠道成功后写完成标记
# （幂等；认证失败 / 网关未就绪时失败静默、下次启动重试——与 rename / backfill
# 同一「秒级探测预算」约定）。本地步（清理真源备援字段）在 model_gateway_settings
# .prune_legacy_alias_fallback_fields，由启动钩子在两段之间调用。
def project_provider_registrations(
    provider: str,
    *,
    cfg: "NewApiProvisionerConfig | None" = None,
    admin: "AdminToken | None" = None,
) -> dict[str, Any]:
    """[aipdd fork delta req40] 把单个渠道的注册表重投影到网关（可单独触发、幂等）。

    目录一刷新就必须立刻重投影：否则网关 ``channels.models`` / ``model_mapping``
    里还留着上一轮编译出来的名字，「从网关摘掉」只停留在本地，用户照旧能路由到
    一个已经下架的模型。投影对 mapping / models 是**整体替换**语义，所以目录里已
    不存在的名字会随这次写入一并消失。

    编译结果为空（该渠道既没目录也没任何有效绑定）时刻意不写空投影 —— 那会把渠
    道整体打成不可用，而空目录多半意味着「还没同步过」；只把判定结果回给调用方。
    """

    provider_key = str(provider or "").strip().lower()
    if not provider_key or provider_key == "comfyui":
        return {"provider": provider_key, "ok": True, "skipped": "not-projectable"}
    mapping = build_channel_registrations(provider_key)
    if not mapping:
        return {"provider": provider_key, "ok": True, "skipped": "empty-mapping", "models": 0}
    provisioner_cfg = cfg or get_provisioner_config()
    admin_token = admin or ensure_admin_access_token(provisioner_cfg)
    saved = get_newapi_provider_channel(provider_key) or {}
    payload = build_channel_payload(
        provider=provider_key,
        channel_type=int(saved.get("type") or 0) or None,
        upstream_key=str(saved.get("upstreamKey") or ""),
        model_mapping=mapping,
        # [aipdd fork delta] 拍平：优先级不推导；沿用已存渠道值。
        priority=int(saved.get("priority") or 0),
        base_url=str(saved.get("baseUrl") or ""),
        other_settings=saved.get("settings") or {},
    )
    outcome = upsert_channel(provisioner_cfg, admin_token, payload)
    return {
        "provider": provider_key,
        "ok": bool(outcome.get("ok")),
        "action": outcome.get("action"),
        "models": len(mapping),
    }


def refresh_provider_catalog(
    provider: str,
    *,
    force: bool = True,
    timeout: float | None = None,
    reproject: bool = True,
) -> dict[str, Any]:
    """[aipdd fork delta req40] 目录刷新的唯一入口：拉 → 覆盖 → 剪失效绑定 → 重投影。

    「用户每次动渠道设置，就是拉清单的时机」在这里落地；渠道同步路由、渠道保存、
    设置页模型 Tab、模型广场手动刷新都走它，不再有「只改 Key 不刷目录」的分支。

    失败语义是刻意的：拉取失败**不动快照也不剪绑定**（一次网络抖动不该取消用户全
    部默认设置），只把 error 透出给前端提示；此时目录若为空，调用侧一律走「未绑定
    可用模型」前置拒绝，而不是带着死名跑到一半再 404。
    """

    provider_key = str(provider or "").strip().lower()
    if not provider_key:
        return {"provider": "", "ok": False, "error": "provider is required", "pruned": {}}
    entries, error = list_provider_upstream_catalog(
        provider_key, force=force, timeout=timeout
    )
    result: dict[str, Any] = {
        "provider": provider_key,
        "ok": not error,
        "count": len(entries),
        "entries": entries,
        "pruned": {},
        "error": error,
    }
    if error:
        # 拉不到就退回既有目录（快照）继续判定：快照也为空时前端显示「尚未同步」。
        result["entries"] = get_upstream_catalog(provider_key)
        result["count"] = len(result["entries"])
        result["stale"] = True
        return result
    result["pruned"] = prune_stale_bindings(provider_key)
    # [aipdd fork delta req43] 目录拉到之后顺手拉一次「能力绑定」：这是渠道对
    # 「某个模型的在线绑定到底吃什么输入」的自述，也是 omni 提交前闸门的唯一事实源。
    # 失败 / 该渠道不暴露能力接口一律不表态（不写库），不影响目录刷新结果。
    result["capabilities"] = refresh_provider_capability_bindings(provider_key)
    if reproject:
        try:
            result["projection"] = project_provider_registrations(provider_key)
        except Exception as exc:  # noqa: BLE001 - 投影失败不回滚已刷新的目录与绑定
            logger.warning(
                "[aipdd fork delta req40] reproject after catalog refresh failed for %s: %s",
                provider_key,
                exc,
            )
            result["projection"] = {"provider": provider_key, "ok": False, "error": str(exc)}
    return result


def project_flat_registrations_migration(
    *,
    init_timeout_ms: int | None = None,
) -> dict[str, Any]:
    if not provisioner_enabled():
        return {"skipped": "provisioner-disabled"}
    if flat_registration_migration_done():
        return {"skipped": "already-migrated"}
    cfg = get_provisioner_config(init_timeout_ms=init_timeout_ms)
    admin = ensure_admin_access_token(cfg)
    providers = [
        str(channel.get("provider") or "").strip().lower()
        for channel in get_newapi_provider_channels()
    ]
    results: list[dict[str, Any]] = []
    for provider in providers:
        if not provider or provider == "comfyui":
            continue
        try:
            results.append(project_provider_registrations(provider, cfg=cfg, admin=admin))
        except Exception as exc:  # noqa: BLE001 - 单渠道失败不影响其余渠道与标记
            results.append(
                {
                    "provider": provider,
                    "ok": False,
                    "error": str(exc),
                    "models": len(build_channel_registrations(provider)),
                }
            )
    failed = [item for item in results if not item.get("ok")]
    migrated = bool(results) and not failed
    if migrated:
        mark_flat_registration_migrated()
        logger.warning(
            "[aipdd fork delta] flat registration migration projected "
            "%s channels: %s",
            len(results),
            ", ".join(str(item.get("provider")) for item in results),
        )
    return {
        "migrated": migrated,
        "providers": len(results),
        "failed": failed,
        "results": results,
    }


def build_provisioner_status() -> dict[str, Any]:
    cfg = get_provisioner_config()
    return {
        "enabled": provisioner_enabled(),
        "adminBaseUrl": cfg.admin_base_url,
        "dbConfigured": bool(cfg.sql_dsn or cfg.sqlite_path),
        "database": build_newapi_database_status(
            sql_dsn=os.environ.get("LOCALAPI_SQL_DSN", ""),
            sqlite_path=os.environ.get("LOCALAPI_SQLITE_PATH", ""),
            admin_username=os.environ.get("LOCALAPI_ADMIN_USERNAME", "root"),
        ),
        "adminUsername": cfg.admin_username,
        "relayTokenName": cfg.relay_token_name,
        "providers": PROVIDER_PRESETS,
        "providerChannels": build_newapi_provider_channels_status(),
        # [aipdd fork delta] WP-A: 别名映射真源已收敛到项目自有 runtime_settings，
        # 网关 channels.model_mapping 只是投影；面板据此展示「当前真源=本项目」。
        "aliasMappings": get_newapi_model_alias_mappings(),
        "aliasMappingSource": "project",
        # [aipdd fork delta] 拍平后：注册调用名 -> 归属渠道（主备机制退役，backup 恒 false）。
        "aliasChannelRoles": build_alias_channel_roles(),
        "mediaModels": build_newapi_media_model_mappings_status(),
        "embeddingModel": build_newapi_embedding_model_status(),
        "relayBaseUrl": normalize_relay_base_url(cfg.admin_base_url),
    }
