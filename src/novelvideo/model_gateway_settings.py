"""Runtime model gateway settings.

CE persists the selected official or bundled-NewAPI gateway in local settings,
which are its sole runtime credential source. EE has a control-plane DSN and
keeps its deployment environment as the sole credential source.
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import tempfile
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from novelvideo.official_defaults import (
    DEFAULT_COGNEE_EMBEDDING_DIM,
    DEFAULT_COGNEE_EMBEDDING_MODEL,
    DEFAULT_COGNEE_EMBEDDING_PROVIDER,
    DEFAULT_EMBEDDING_BATCH_SIZE,
    official_newapi_base_url,
)
from novelvideo.catalog.official_media_catalog_schema import (
    catalog_version as _catalog_version,
    validate_official_media_catalog as _validate_official_media_catalog,
)
from novelvideo.shared.runtime_env import is_ce_effective
from novelvideo.sqlite_pragmas import configure_sqlite_connection

MODE_OFFICIAL = "official"
MODE_CUSTOM = "custom"
MODE_HYBRID = "hybrid"
VALID_MODES = {MODE_OFFICIAL, MODE_CUSTOM, MODE_HYBRID}
PLACEHOLDER_API_KEYS = {
    "your_newapi_token",
    "your_model_api_key",
    "your_api_key",
    "your_dc_key",
}
OFFICIAL_MEDIA_CATALOG_AUTO_UPDATE_KEY = "official_media_catalog_auto_update"
OFFICIAL_MEDIA_CATALOG_LAST_CHECKED_KEY = "official_media_catalog_last_checked_at"
OFFICIAL_MEDIA_CATALOG_REMOTE_URL_KEY = "official_media_catalog_remote_url"
OFFICIAL_MEDIA_CATALOG_ETAG_KEY = "official_media_catalog_etag"
OFFICIAL_MEDIA_CATALOG_REVISION_KEY = "official_media_catalog_revision"
OFFICIAL_MEDIA_CATALOG_PUBLISHED_AT_KEY = "official_media_catalog_published_at"
OFFICIAL_MEDIA_CATALOG_SHA256_KEY = "official_media_catalog_sha256"
OFFICIAL_MEDIA_CATALOG_LAST_ERROR_KEY = "official_media_catalog_last_error"
_OFFICIAL_MEDIA_CATALOG_WRITE_LOCK = threading.Lock()


@dataclass(frozen=True)
class EffectiveNewApiConfig:
    mode: str
    source: str
    base_url: str
    api_key: str


@dataclass(frozen=True)
class EffectiveMediaRelayConfig:
    source: str
    provider: str
    ttl_seconds: int
    endpoint: str
    bucket: str
    access_key_id: str
    access_key_secret: str
    cloud_name: str = ""
    cloudinary_api_key: str = ""
    cloudinary_api_secret: str = ""
    cloudinary_folder: str = ""
    # [aipdd fork delta req44] 「AIPDD 数字资产云」与本地存储档的参考媒体中转：
    # Key 在设置页显式填写（与阿里云 OSS 的 AK/SK 同一口径的脱敏/合并语义），
    # 未填时仍可用已连接的 AIPDD 渠道 Key；这里另外存站点地址、对象前缀与登记开关。
    aipdd_api_key: str = ""
    aipdd_base_url: str = ""
    aipdd_prefix: str = ""
    aipdd_register_asset: bool = False
    # [aipdd fork delta] AWS S3 档（与阿里云 OSS 并行的对象存储中转）：AK/SK/Token 沿用
    # 与 OSS 同一口径的脱敏/合并语义，region/bucket 必填，endpoint_url 仅自建 S3 兼容网关才填。
    s3_region: str = ""
    s3_bucket: str = ""
    s3_access_key_id: str = ""
    s3_access_key_secret: str = ""
    s3_endpoint_url: str = ""
    s3_session_token: str = ""


@dataclass(frozen=True)
class EffectiveCogneeEmbeddingConfig:
    source: str
    provider: str
    model: str
    dimensions: str
    upstream_provider: str
    upstream_model: str
    batch_size: str = ""


def mask_secret(value: str) -> str:
    clean = str(value or "").strip()
    if not clean:
        return ""
    if len(clean) <= 10:
        return "*" * len(clean)
    return f"{clean[:4]}...{clean[-4:]}"


def normalize_api_key(value: str | None) -> str:
    clean = str(value or "").strip()
    if not clean:
        return ""
    lowered = clean.lower()
    if lowered in PLACEHOLDER_API_KEYS:
        return ""
    if lowered.startswith("your_") or lowered.startswith("<your_"):
        return ""
    return clean


def normalize_gateway_mode(value: str | None) -> str:
    # [aipdd fork delta] 去掉官方中转(RelayClaw)作为默认：未设置/非法时走 LocalNewAPI custom。
    mode = str(value or "").strip().lower()
    return mode if mode in VALID_MODES else MODE_CUSTOM


def normalize_relay_base_url(value: str | None) -> str:
    base = str(value or "").strip().rstrip("/")
    if not base:
        return ""
    if base.endswith("/v1"):
        return base
    return f"{base}/v1"


# [aipdd fork delta] 渠道 baseUrl 归一（与 newapi_provisioner.resolve_models_endpoint
# 共用本实现，避免两套拼接规则漂移）：自有库里的渠道 base 约定带版本后缀——媒体
# 直连要用 {base}/video/generations、{base}/images/generations 与 {base}/models；
# 写本地 NewAPI 网关时再由 build_channel_payload 剥掉后缀。用户在设置页手填的
# 裸域名（如 https://susciyuan.com）若不在保存 / 读取期补齐 /v1：媒体提交会打到
# 目标站的前端 SPA 兜底（HTTP 200 HTML → submit returned invalid JSON，req08），
# 能力发现（/v1/models）与上游清单拉取也会静默失败。
_CHANNEL_API_PATHS: dict[str, str] = {
    "volcengine": "/api/v3",
    "ark": "/api/v3",
    "doubao": "/api/v3",
    # [aipdd fork delta req48] Gemini 的 REST 根路径是 /v1beta（不是 /v1）：清单在
    # GET /v1beta/models。_has_versioned_path 只认末段 v+纯数字，不登记会被补成
    # /v1，拉清单与拼模型名都打到不存在的版本上。
    "gemini": "/v1beta",
}


def _has_versioned_path(base: str) -> bool:
    """base 已带版本后缀（/v1、/v2、/api/v3…）时保持原样。"""

    last_segment = base.rsplit("/", 1)[-1]
    return last_segment[:1] == "v" and last_segment[1:].isdigit()


def normalize_channel_base_url(provider: str, base_url: str | None) -> str:
    """把渠道 baseUrl 归一到「带版本后缀」形态；comfyui 的 base 不适用此规则。

    已带版本后缀原样保留；Ark 系（type 45 适配器硬编码拼 /api/v3）补齐 /api/v3；
    其余 OpenAI 兼容渠道补齐 /v1。空值原样返回空。
    """

    base = str(base_url or "").strip().rstrip("/")
    if not base:
        return ""
    provider_key = str(provider or "").strip().lower()
    if provider_key == "comfyui":
        return base
    if _has_versioned_path(base):
        return base
    api_path = _CHANNEL_API_PATHS.get(provider_key)
    if api_path:
        # [aipdd fork delta req48] /v1beta 这类带后缀的版本不在
        # _has_versioned_path 的「v+纯数字」识别面里，用户直接粘完整版本路径
        # （…/v1beta）时会再叠一层而变成 …/v1beta/v1beta。已以该路径结尾就原样
        # 保留（重复拼路径是渠道配错的第一大成因）。
        if base.endswith(api_path):
            return base
        return f"{base}{api_path}"
    return f"{base}/v1"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _settings_db_path() -> Path:
    from novelvideo import config

    return Path(config.STATE_DIR) / "local" / "settings.db"


def _connect() -> sqlite3.Connection:
    path = _settings_db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    for attempt in range(3):
        conn: sqlite3.Connection | None = None
        try:
            conn = sqlite3.connect(str(path), timeout=10, check_same_thread=False)
            conn.row_factory = sqlite3.Row
            configure_sqlite_connection(conn)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS runtime_settings (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """)
            conn.commit()
            return conn
        except sqlite3.OperationalError as exc:
            if conn is not None:
                conn.close()
            retryable = any(
                marker in str(exc).lower()
                for marker in (
                    "database is locked",
                    "database is busy",
                )
            )
            if not retryable or attempt == 2:
                raise
            time.sleep(0.05 * (attempt + 1))
        except Exception:
            if conn is not None:
                conn.close()
            raise
    raise RuntimeError("failed to open model gateway settings database")


def _read_all() -> dict[str, str]:
    conn = _connect()
    try:
        rows = conn.execute("SELECT key, value FROM runtime_settings").fetchall()
        return {str(row["key"]): str(row["value"]) for row in rows}
    finally:
        conn.close()


def _uses_ce_gateway_settings() -> bool:
    """Return whether this process owns the CE LocalNewAPI settings database."""
    return is_ce_effective()


def _write_many(values: dict[str, str]) -> None:
    now = _now_iso()
    conn = _connect()
    try:
        conn.execute("BEGIN IMMEDIATE")
        for key, value in values.items():
            conn.execute(
                """
                INSERT INTO runtime_settings(key, value, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET
                    value = excluded.value,
                    updated_at = excluded.updated_at
                """,
                (key, str(value or ""), now),
            )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def set_model_gateway_mode(mode: str) -> None:
    _write_many({"model_gateway_mode": normalize_gateway_mode(mode)})


def save_official_newapi_key(
    *,
    api_key: str,
    activate: bool = True,
) -> None:
    values = {
        "official_newapi_api_key": str(api_key or "").strip(),
    }
    if activate:
        values["model_gateway_mode"] = MODE_OFFICIAL
    _write_many(values)


def save_custom_newapi_gateway(
    *,
    base_url: str,
    api_key: str,
    admin_base_url: str = "",
    token_name: str = "",
    token_id: int | str = "",
    activate: bool = True,
) -> None:
    values = {
        "custom_newapi_base_url": normalize_relay_base_url(base_url),
        "custom_newapi_api_key": str(api_key or "").strip(),
        "custom_newapi_admin_base_url": str(admin_base_url or "").strip().rstrip("/"),
        "custom_newapi_token_name": str(token_name or "").strip(),
        "custom_newapi_token_id": str(token_id or "").strip(),
    }
    if activate:
        values["model_gateway_mode"] = MODE_CUSTOM
    _write_many(values)


def save_newapi_database_config(
    *,
    sql_dsn: str,
    sqlite_path: str = "",
    admin_username: str = "",
) -> None:
    _write_many(
        {
            "custom_newapi_db_sql_dsn": str(sql_dsn or "").strip(),
            "custom_newapi_db_sqlite_path": str(sqlite_path or "").strip(),
            "custom_newapi_admin_username": str(admin_username or "").strip(),
        }
    )


# [aipdd fork delta] 剪映草稿目录（设置页可配）。这个值会被透传到 VectCut 作为
# 草稿落盘根目录，只接受绝对路径、拒绝 ``..`` 段——相对路径或上跳段都会让
# 「草稿目录」逃出调用方划定的范围。


def _is_absolute_dir_path(text: str) -> bool:
    if text.startswith("/"):
        return True
    # Windows 盘符绝对路径（C:\... 或 C:/...）；VectCut 亦可原生跑在 Windows。
    return len(text) >= 3 and text[0].isalpha() and text[1] == ":" and text[2] in "\\/"


def normalize_vectcut_draft_dir(value: str | None) -> str:
    """校验并规范化「剪映草稿目录」；空串 = 取消配置（回 VectCut 部署默认）。"""
    text = str(value or "").strip()
    if not text:
        return ""
    if len(text) > 512:
        raise ValueError("draft dir is too long (max 512 characters)")
    if not _is_absolute_dir_path(text):
        raise ValueError("draft dir must be an absolute path (e.g. /drafts)")
    parts = text.replace("\\", "/").split("/")
    if any(part == ".." for part in parts):
        raise ValueError("draft dir must not contain '..'")
    return text


def get_vectcut_draft_dir() -> str:
    """读取配置的剪映草稿目录；未配置返回空串（调用侧回退 VectCut 默认）。"""
    # 非 CE 进程无本地库 → 空串（EE 不得打开 CE settings.db，回退 VectCut 部署默认）。
    if not _uses_ce_gateway_settings():
        return ""
    return str(_read_all().get(VECTCUT_DRAFT_DIR_SETTING_KEY) or "").strip()


def save_vectcut_draft_dir(value: str | None) -> str:
    normalized = normalize_vectcut_draft_dir(value)
    _write_many({VECTCUT_DRAFT_DIR_SETTING_KEY: normalized})
    return normalized


def _decode_provider_channels(value: str | None) -> list[dict[str, Any]]:
    if not value:
        return []
    try:
        raw = json.loads(value)
    except json.JSONDecodeError:
        return []
    if not isinstance(raw, list):
        return []

    channels: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in raw:
        if not isinstance(item, dict):
            continue
        provider = str(item.get("provider") or "").strip().lower()
        if not provider or provider in seen:
            continue
        seen.add(provider)
        channels.append(
            {
                "provider": provider,
                "type": max(0, int(item.get("type") or 0)),
                "upstreamKey": str(item.get("upstreamKey") or "").strip(),
                "baseUrl": str(item.get("baseUrl") or "").strip().rstrip("/"),
                "priority": int(item.get("priority") or 0),
                "settings": (
                    item.get("settings")
                    if isinstance(item.get("settings"), dict)
                    else {}
                ),
            }
        )
    return channels


# [aipdd fork delta] WP-A: 模型别名映射(modelMapping)的单一真源从网关
# channels.model_mapping 收敛到项目自有 runtime_settings，网关侧降级为投影。
# 结构: {"<alias>": {"provider": "<provider>", "target": "<真实模型>",
#                  "fallbackProvider": "<备援 provider>", "fallbackTarget": "<备援真实模型>"}}
# 后两个键是 WP-C 真主备用的可选位，缺省表示该别名只有主渠道。
MODEL_ALIAS_MAPPINGS_SETTING_KEY = "custom_newapi_model_alias_mappings"
# [aipdd fork delta] WP-A: 一次性反向导入的完成标记（只跑一次，不每次启动都去读网关）。
MODEL_ALIAS_IMPORT_MARKER_KEY = "custom_newapi_model_alias_mappings_imported_at"
# [aipdd fork delta] 上游模型清单快照：每次成功拉取渠道上游 /v1/models 后落库。
# 网关投影与模型广场在「永不远程」前提下要用渠道真实模型名（直通注册 / 展示）。
# 结构: {"<provider>": {"models": ["<真实模型名>", ...], "baseUrl": "...", "fetchedAt": "..."}}
UPSTREAM_MODELS_SNAPSHOT_SETTING_KEY = "custom_newapi_upstream_models_snapshot"
# [aipdd fork delta req43] 渠道「能力绑定」事实：``/v1/capabilities`` 里某模型的在线
# 绑定各自吃什么输入。结构: {"<provider>": {"baseUrl", "fetchedAt", "bindings": [...]}}
PROVIDER_CAPABILITY_BINDINGS_SETTING_KEY = "custom_newapi_provider_capability_bindings"
# [aipdd fork delta] 剪映草稿目录：VectCut 服务（草稿生成与资产库草稿上传的落盘
# 目的地）上的存储根目录。空值表示沿用 VectCut 服务的部署默认（VECTCUT_DRAFT_DIR，
# 容器部署即 /drafts）。api 侧只存配置，调用时透传给 VectCut（save_draft / 草稿包
# 下载 / 草稿上传 / 草稿目录同步四个入口同一口径）。
VECTCUT_DRAFT_DIR_SETTING_KEY = "custom_vectcut_draft_dir"


def _decode_alias_mappings(value: str | None) -> dict[str, dict[str, Any]]:
    if not value:
        return {}
    try:
        raw = json.loads(value)
    except json.JSONDecodeError:
        return {}
    if not isinstance(raw, dict):
        return {}

    mappings: dict[str, dict[str, Any]] = {}
    for alias, item in raw.items():
        alias_name = str(alias or "").strip()
        if not alias_name or not isinstance(item, dict):
            continue
        provider = str(item.get("provider") or "").strip().lower()
        target = str(item.get("target") or "").strip()
        if not provider or not target:
            continue
        entry: dict[str, Any] = {"provider": provider, "target": target}
        # [aipdd fork delta] WP-B: 视觉位 / 纯文本位的能力归属只存“已声明”的，
        # 缺省表示未归类（自检不报能力错配，避免误告警）。
        capability = str(item.get("capability") or "").strip().lower()
        if capability in {"text", "vision"}:
            entry["capability"] = capability
        fallback_provider = str(item.get("fallbackProvider") or "").strip().lower()
        if fallback_provider and fallback_provider != provider:
            entry["fallbackProvider"] = fallback_provider
            fallback_target = str(item.get("fallbackTarget") or "").strip()
            if fallback_target:
                entry["fallbackTarget"] = fallback_target
        mappings[alias_name] = entry
    return mappings


def _decode_media_model_mappings(value: str | None) -> dict[str, dict[str, Any]]:
    if not value:
        return {}
    try:
        raw = json.loads(value)
    except json.JSONDecodeError:
        return {}
    if not isinstance(raw, dict):
        return {}

    mappings: dict[str, dict[str, Any]] = {}
    for model, item in raw.items():
        model_name = str(model or "").strip()
        if not model_name or not isinstance(item, dict):
            continue
        provider = str(item.get("provider") or "").strip().lower()
        if not provider:
            continue
        media_type = str(item.get("mediaType") or "").strip().lower()
        config = item.get("config") if isinstance(item.get("config"), dict) else {}
        mapping: dict[str, Any] = {
            "provider": provider,
            "upstreamModel": str(item.get("upstreamModel") or "").strip(),
        }
        # [aipdd fork delta req46] music：设置页新增的「音乐」类型，与 image/video/audio
        # 同机制持久化（mediaModels.mediaType）；调用走厂商直连协议，不经网关。
        if media_type in {"image", "video", "audio", "music"}:
            mapping["mediaType"] = media_type
        if str(item.get("label") or "").strip():
            mapping["label"] = str(item.get("label") or "").strip()
        if "enabled" in item:
            mapping["enabled"] = item.get("enabled") is not False
        if "sortOrder" in item:
            # [aipdd fork delta] 修复设默认不生效：`or 100` 会把合法的 0（最高优先级）
            # 当成假值变成 100，导致「设为默认」的 sortOrder=0 存不下也读不回。
            mapping["sortOrder"] = (
                int(item["sortOrder"])
                if item.get("sortOrder") is not None
                else 100
            )
        if config:
            mapping["config"] = config
        mappings[model_name] = mapping
    return mappings


def _decode_embedding_model_config(value: str | None) -> dict[str, Any]:
    if not value:
        return {}
    try:
        raw = json.loads(value)
    except json.JSONDecodeError:
        return {}
    if not isinstance(raw, dict):
        return {}

    provider = str(raw.get("provider") or "").strip().lower()
    upstream_model = str(raw.get("upstreamModel") or "").strip()
    dimensions = _int_setting(
        str(raw.get("dimension") or raw.get("dimensions") or ""), 0
    )
    batch_size = _int_setting(
        str(raw.get("batchSize") or raw.get("batch_size") or ""), 0
    )
    if not provider or not upstream_model or dimensions <= 0:
        return {}
    result: dict[str, Any] = {
        "provider": provider,
        "upstreamModel": upstream_model,
        "dimension": dimensions,
        # Retain the field for API compatibility, but request behavior is an
        # internal model contract and cannot be disabled by saved CE settings.
        "sendDimensions": True,
        "internalModel": "DC-cognee-embedding",
    }
    if batch_size > 0:
        result["batchSize"] = batch_size
    return result


def get_newapi_provider_channels() -> list[dict[str, Any]]:
    settings = get_model_gateway_settings()
    channels = _decode_provider_channels(settings.get("custom_newapi_provider_channels"))
    # [aipdd fork delta] 读取期归一 baseUrl（补齐 /v1、/api/v3）：修复历史数据里
    # 手填的裸域名，让媒体直连 / 能力发现 / 上游清单拉取在出口处就是完整 URL。
    # 幂等（已带版本后缀原样保留），不改变 comfyui 的 base。
    for channel in channels:
        channel["baseUrl"] = normalize_channel_base_url(
            channel.get("provider", ""), channel.get("baseUrl")
        )
    return channels


def get_newapi_provider_channel(provider: str) -> dict[str, Any] | None:
    wanted = str(provider or "").strip().lower()
    if not wanted:
        return None
    for channel in get_newapi_provider_channels():
        if channel["provider"] == wanted:
            return channel
    return None


def get_newapi_model_alias_mappings() -> dict[str, dict[str, Any]]:
    """[aipdd fork delta] WP-A: 读取别名映射真源（非 CE 进程无本地库 → 空表）。"""
    if not _uses_ce_gateway_settings():
        return {}
    settings = get_model_gateway_settings()
    return _decode_alias_mappings(settings.get(MODEL_ALIAS_MAPPINGS_SETTING_KEY))


def save_newapi_model_alias_mappings(
    mappings: dict[str, Any],
    *,
    merge: bool = False,
    default_provider: str = "",
    default_capability: str = "",
) -> dict[str, dict[str, Any]]:
    """[aipdd fork delta] WP-A: 写入别名映射真源。

    ``merge=True`` 保留未提及的既有条目（面板单项设默认走这条），
    ``merge=False`` 整体替换。值为空 target 表示显式删除该别名。
    ``default_capability`` 供批量设默认时整批声明能力位（text|vision）。
    """
    normalized: dict[str, dict[str, Any]] = (
        dict(get_newapi_model_alias_mappings()) if merge else {}
    )
    provider_default = str(default_provider or "").strip().lower()
    capability_default = str(default_capability or "").strip().lower()
    if capability_default and capability_default not in {"text", "vision"}:
        raise ValueError("capability must be one of: text, vision")
    for alias, item in (mappings or {}).items():
        alias_name = str(alias or "").strip()
        if not alias_name:
            continue
        capability = capability_default
        if isinstance(item, str):
            provider = provider_default
            target = item.strip()
            fallback_provider = ""
            fallback_target = ""
        elif isinstance(item, dict):
            provider = str(item.get("provider") or provider_default).strip().lower()
            target = str(item.get("target") or "").strip()
            fallback_provider = str(item.get("fallbackProvider") or "").strip().lower()
            fallback_target = str(item.get("fallbackTarget") or "").strip()
            item_capability = str(item.get("capability") or "").strip().lower()
            if item_capability and item_capability not in {"text", "vision"}:
                raise ValueError(
                    f"capability for model alias {alias_name} must be text or vision"
                )
            capability = item_capability or capability
        else:
            raise ValueError(f"invalid model alias mapping entry for {alias_name}")
        if not provider:
            raise ValueError(f"provider is required for model alias {alias_name}")
        if not target:
            normalized.pop(alias_name, None)
            continue
        entry: dict[str, Any] = {"provider": provider, "target": target}
        if capability in {"text", "vision"}:
            entry["capability"] = capability
        if fallback_provider and fallback_provider != provider:
            entry["fallbackProvider"] = fallback_provider
            if fallback_target:
                entry["fallbackTarget"] = fallback_target
        normalized[alias_name] = entry
    _write_many(
        {
            MODEL_ALIAS_MAPPINGS_SETTING_KEY: json.dumps(
                normalized,
                ensure_ascii=False,
                separators=(",", ":"),
            )
        }
    )
    return normalized


def record_newapi_model_alias_mappings(
    mapping: dict[str, Any],
    *,
    provider: str,
) -> dict[str, dict[str, Any]]:
    """[aipdd fork delta] WP-A: 把一份 alias->target 字典按渠道归属并入真源。

    供 channels/batch 使用：前端继续按旧形态提交 modelMapping，后端不再把它
    直接当网关真源，而是先落自有存储、再编译投影。
    """
    payload = {
        str(alias).strip(): str(target or "").strip()
        for alias, target in (mapping or {}).items()
        if str(alias or "").strip()
    }
    if not payload:
        return get_newapi_model_alias_mappings()
    return save_newapi_model_alias_mappings(
        {alias: {"provider": provider, "target": target} for alias, target in payload.items()},
        merge=True,
    )


def _decode_upstream_models_snapshot(value: str | None) -> dict[str, dict[str, Any]]:
    """解上游目录快照：``{provider: {"models": [条目], "baseUrl", "fetchedAt"}}``。

    [aipdd fork delta req40] 快照从「只有名字」升级为结构化条目（kind / 端点 /
    维度 / 能力档位）。旧格式（``models: ["名字"]``）在读取期就地补齐成推断条目，
    避免升级即清空目录；下次成功拉取会被整体覆盖为真元数据。
    """
    if not value:
        return {}
    try:
        raw = json.loads(value)
    except json.JSONDecodeError:
        return {}
    if not isinstance(raw, dict):
        return {}
    snapshot: dict[str, dict[str, Any]] = {}
    for provider, item in raw.items():
        provider_key = str(provider or "").strip().lower()
        if not provider_key:
            continue
        if isinstance(item, list):
            # 更旧的形态：{provider: ["name", ...]}。
            item = {"models": item}
        if not isinstance(item, dict):
            continue
        entries = _normalize_catalog_entries(item.get("models"))
        if not entries:
            continue
        snapshot[provider_key] = {
            "models": entries,
            "baseUrl": str(item.get("baseUrl") or "").strip(),
            "fetchedAt": str(item.get("fetchedAt") or "").strip(),
        }
    return snapshot


def _normalize_catalog_entries(raw: Any) -> list[dict[str, Any]]:
    """名字数组 / 条目数组 / 脏数据 → 统一的结构化条目（按名字排序去重）。"""

    from novelvideo.upstream_catalog import parse_catalog_response

    items = raw if isinstance(raw, list) else []
    entries: list[dict[str, Any]] = []
    plain_names: list[str] = []
    for item in items:
        if isinstance(item, dict) and str(item.get("name") or "").strip():
            entries.append(item)
        elif isinstance(item, str) and item.strip():
            plain_names.append(item.strip())
    if entries:
        merged = {str(item.get("name") or "").strip(): item for item in entries}
        for name in plain_names:
            merged.setdefault(name, {"name": name})
        normalized = [merged[key] for key in sorted(merged)]
    else:
        normalized = [{"name": name} for name in sorted(set(plain_names))]
    # parse_catalog_response 读 OpenAI 形态的 id/model 键；快照条目用 name 存储，
    # 这里刷平为解析器能认的形态（无元数据时落成 inferred 条目）。
    payload = {
        "data": [
            {**item, "id": str(item.get("name") or "").strip()}
            for item in normalized
            if str(item.get("name") or "").strip()
        ]
    }
    entries = parse_catalog_response(payload)
    if entries:
        return entries
    # 兜底：解析器不可用（异常环境）时至少保留名字，不静默清空目录。
    return [
        {"name": name, "available": True, "inferred": True, "mediaType": "other"}
        for name in sorted(
            {
                str(item.get("name") or "").strip() or str(item or "").strip()
                for item in items
            }
            - {""}
        )
    ]


def save_upstream_models_snapshot(
    provider: str,
    models: list[Any],
    *,
    base_url: str = "",
) -> None:
    """[aipdd fork delta] 记录某渠道最近一次成功拉取的上游模型目录（结构化）。

    [aipdd fork delta req40] 「渠道有什么模型只能拉了才知道」：快照是目录的落库
    形态，每次成功拉取按渠道**整体覆盖**（下架模型不会因为旧快照而残留）。快照
    只是数据源，写失败静默，绝不反向拖垮拉取主流程（``list_upstream_models`` 的
    成功返回）。
    """
    provider_key = str(provider or "").strip().lower()
    entries = _normalize_catalog_entries(models)
    if not provider_key or not entries:
        return
    try:
        settings = get_model_gateway_settings()
        snapshot = _decode_upstream_models_snapshot(
            settings.get(UPSTREAM_MODELS_SNAPSHOT_SETTING_KEY)
        )
        snapshot[provider_key] = {
            "models": entries,
            "baseUrl": str(base_url or "").strip().rstrip("/"),
            "fetchedAt": _now_iso(),
        }
        _write_many(
            {
                UPSTREAM_MODELS_SNAPSHOT_SETTING_KEY: json.dumps(
                    snapshot, ensure_ascii=False, separators=(",", ":")
                )
            }
        )
    except Exception:  # noqa: BLE001 - 快照写入失败不影响主流程
        return


def get_upstream_catalog(provider: str) -> list[dict[str, Any]]:
    """[aipdd fork delta req40] 渠道目录（结构化条目）；无快照时为空表。

    这是「注册 / 候选 / 默认值有效性 / 端点」的唯一模型来源：代码不再预先知道
    渠道里有什么模型，也不再从配置里反向造出模型名。
    """
    provider_key = str(provider or "").strip().lower()
    if not provider_key or not _uses_ce_gateway_settings():
        return []
    settings = get_model_gateway_settings()
    snapshot = _decode_upstream_models_snapshot(
        settings.get(UPSTREAM_MODELS_SNAPSHOT_SETTING_KEY)
    )
    return [dict(entry) for entry in snapshot.get(provider_key, {}).get("models") or []]


def get_upstream_catalog_meta() -> dict[str, dict[str, Any]]:
    """[aipdd fork delta req40] {provider: {baseUrl, fetchedAt, count}}：目录的**出处**。

    设置页要把「这份清单是什么时候从哪个 BaseURL 拉回来的」显示出来，否则用户无法判断
    看到的是不是新鲜的渠道事实（下拉里的名字来自快照，快照只会被成功拉取整体覆盖）。
    """

    out: dict[str, dict[str, Any]] = {}
    if not _uses_ce_gateway_settings():
        return out
    settings = get_model_gateway_settings()
    snapshot = _decode_upstream_models_snapshot(
        settings.get(UPSTREAM_MODELS_SNAPSHOT_SETTING_KEY)
    )
    for provider_key, block in snapshot.items():
        models = list((block or {}).get("models") or [])
        out[str(provider_key)] = {
            "baseUrl": str((block or {}).get("baseUrl") or ""),
            "fetchedAt": str((block or {}).get("fetchedAt") or ""),
            "count": len(models),
        }
    return out


def _decode_capability_bindings(value: str | None) -> dict[str, dict[str, Any]]:
    """解渠道能力绑定事实：``{provider: {baseUrl, fetchedAt, bindings: [...]}}``。"""

    if not value:
        return {}
    try:
        raw = json.loads(value)
    except json.JSONDecodeError:
        return {}
    if not isinstance(raw, dict):
        return {}
    out: dict[str, dict[str, Any]] = {}
    for provider, item in raw.items():
        provider_key = str(provider or "").strip().lower()
        if not provider_key or not isinstance(item, dict):
            continue
        bindings = item.get("bindings")
        if not isinstance(bindings, list):
            continue
        out[provider_key] = {
            "baseUrl": str(item.get("baseUrl") or "").strip(),
            "fetchedAt": str(item.get("fetchedAt") or "").strip(),
            "bindings": [dict(b) for b in bindings if isinstance(b, dict)],
        }
    return out


def save_provider_capability_bindings(
    provider: str,
    bindings: list[dict[str, Any]],
    *,
    base_url: str = "",
) -> None:
    """[aipdd fork delta req43] 落盘某渠道的视频能力绑定事实（整体覆盖，写失败静默）。

    与目录快照同口径：一次成功读取整体覆盖该渠道的旧事实，渠道摘掉某条绑定时
    判定会跟着收紧，不会因为旧快照而残留。空清单不写 —— 拉取失败 / 该渠道不暴露
    能力接口时应当「不表态」，而不是把「没有绑定」当成「都不吃参考视频」。
    """

    provider_key = str(provider or "").strip().lower()
    clean = [dict(item) for item in (bindings or []) if isinstance(item, dict)]
    if not provider_key or not clean:
        return
    try:
        settings = get_model_gateway_settings()
        facts = _decode_capability_bindings(
            settings.get(PROVIDER_CAPABILITY_BINDINGS_SETTING_KEY)
        )
        facts[provider_key] = {
            "baseUrl": str(base_url or "").strip().rstrip("/"),
            "fetchedAt": _now_iso(),
            "bindings": clean,
        }
        _write_many(
            {
                PROVIDER_CAPABILITY_BINDINGS_SETTING_KEY: json.dumps(
                    facts, ensure_ascii=False, separators=(",", ":")
                )
            }
        )
    except Exception:  # noqa: BLE001 - 事实写失败不影响主流程（判定退回不表态）
        return


def get_provider_capability_bindings(provider: str) -> list[dict[str, Any]]:
    """该渠道上次读回的视频能力绑定清单；没读过返回空表（= 不表态）。"""

    provider_key = str(provider or "").strip().lower()
    if not provider_key or not _uses_ce_gateway_settings():
        return []
    settings = get_model_gateway_settings()
    facts = _decode_capability_bindings(
        settings.get(PROVIDER_CAPABILITY_BINDINGS_SETTING_KEY)
    )
    return [dict(item) for item in facts.get(provider_key, {}).get("bindings") or []]


def upstream_catalog_entry(
    name: str,
    provider: str = "",
) -> dict[str, Any] | None:
    """按名字取目录条目（能力 / 端点 / 维度的真源）；不知道是哪个渠道时全表找。

    [aipdd fork delta req40] 「这个模型能不能看图、该发哪个端点、向量多少维」以前靠
    模型名正则猜 + 硬编码端点表；渠道其实自己在目录里声明了。找到条目就按声明判，找不
    到（或该条目无元数据、``inferred=True``）才回落关键词推断。

    [aipdd fork delta req41] 调用名带渠道位（``{provider}::{model}``，模型广场直通卡 /
    登记名都是这个形状）时先拆掉前缀再查，否则同一个模型会因为叫法不同查出空表。
    显式传了 ``provider`` 以它为准，没传就用名字里自带的渠道位。
    """

    clean = str(name or "").strip()
    if not clean:
        return None
    provider_key = str(provider or "").strip().lower()
    if "::" in clean:
        head, _, tail = clean.partition("::")
        clean = str(tail or "").strip()
        provider_key = provider_key or str(head or "").strip().lower()
        if not clean:
            return None
    provider_keys = [provider_key] if provider_key else list(get_upstream_models_snapshot())
    for item in provider_keys:
        for entry in get_upstream_catalog(item):
            if str(entry.get("name") or "").strip() == clean:
                return entry
    return None


def catalog_endpoints(name: str, provider: str = "") -> list[str]:
    """[aipdd fork delta req41] 该模型在渠道目录里声明的端点相对路径清单（无 ``/v1`` 前缀）。

    AIPDD / 火山这类公共厂商是「渠道定了，端点就定了」：``/v1/videos``、
    ``/v1/images/edits``、``/v1/systemone`` 这些上游自己会在目录里声明，所以调用面
    应该按声明选协议，而不是拿 host 名字猜「上游大概长这样」。

    回空表表示「目录没说话」——模型查不到、或该渠道条目压根没带元数据（自建 newapi
    网关的清单常只有名字）。这时才由调用方决定兜底方式（host 提示 / 通用 OpenAI 路径）。
    ``{provider}::{model}`` 复合名由 ``upstream_catalog_entry`` 拆渠道位。
    """

    entry = upstream_catalog_entry(name, provider)
    if entry is None:
        return []
    return [
        str(path or "").strip()
        for path in (entry.get("endpoints") or [])
        if str(path or "").strip()
    ]


def catalog_endpoint_path(name: str, purpose: str, provider: str = "") -> str:
    """[aipdd fork delta req41] 目录里该模型声明的、命中 ``purpose`` 的那一条端点。

    和 :func:`catalog_endpoints` 的差别是这里已经按用途筛过，调用面可以直接拼 URL；
    和 ``upstream_catalog.endpoint_for`` 的差别是这里**不**拿「第一条」当结果 ——
    用途没命中就回空串，让调用方自己保留通用 OpenAI 路径作为兜底（自建网关常见）。
    """

    from novelvideo.upstream_catalog import normalize_endpoint

    declared = catalog_endpoints(name, provider)
    if not declared:
        return ""
    wanted = normalize_endpoint(purpose)
    if not wanted:
        return ""
    # 先取「恰好等于用途」的那条：目录常把提交端点与轮询子路径一起声明（AIPDD 的
    # ``audio/speech`` + ``audio/speech/{task_id}``），拿着路径参数的子路径去提交必错。
    plain = [path for path in declared if "{" not in path]
    for path in plain:
        if path == wanted:
            return path
    for path in plain:
        if wanted in path:
            return path
    return ""


def videos_request_paths(model: str, use_videos_api: bool) -> tuple[str, str]:
    """[aipdd fork delta req41] 视频调用的 ``(提交相对路径, 轮询路径前缀)``。

    确定渠道（AIPDD）把两条路径都声明在条目里：``videos`` 与 ``videos/{taskId}``。
    调用面因此不再自己拼 URL —— 上游换名只需重拉目录。``use_videos_api`` 为假时回到
    NewAPI 通用的 ``video/generations``；目录没端点声明（自建网关常只给名字）时也一样。
    """

    if not use_videos_api:
        return "video/generations", "/video/generations/"
    from novelvideo.upstream_catalog import EP_VIDEOS, task_lookup_endpoint

    clean = str(model or "").strip()
    submit = (catalog_endpoint_path(clean, EP_VIDEOS) if clean else "") or EP_VIDEOS
    entry = upstream_catalog_entry(clean) if clean else None
    lookup = task_lookup_endpoint(entry or {}, submit)
    poll = f"/{lookup.rsplit('<', 1)[0]}" if lookup else f"/{submit}/"
    return submit, poll


def get_upstream_models_snapshot() -> dict[str, list[str]]:
    """[aipdd fork delta] {provider: [上游真实模型名]}；无快照时为空表。

    兼容视图：只回名字。需要 kind / 端点 / 维度请用 ``get_upstream_catalog``。
    """
    if not _uses_ce_gateway_settings():
        return {}
    settings = get_model_gateway_settings()
    snapshot = _decode_upstream_models_snapshot(
        settings.get(UPSTREAM_MODELS_SNAPSHOT_SETTING_KEY)
    )
    return {
        provider: [
            str(entry.get("name") or "").strip()
            for entry in item.get("models") or []
            if str(entry.get("name") or "").strip()
        ]
        for provider, item in snapshot.items()
    }


def catalog_names_for_provider(provider: str) -> set[str]:
    """[aipdd fork delta req40] 渠道目录里的模型名集合（零远程，只读快照）。

    这是所有「模型还存在吗」判定的唯一依据：代码不再预先知道渠道里有什么，也不再
    从配置里反向拼出名字 —— 只有真拉回来过的名字才算数。
    """

    return {
        str(entry.get("name") or "").strip()
        for entry in get_upstream_catalog(provider)
        if str(entry.get("name") or "").strip()
    }


def has_upstream_catalog(provider: str) -> bool:
    """该渠道是否已有目录 —— 决定能不能对绑定做「失效」裁决。

    空目录是「还没同步过 / 这个渠道根本没有 OpenAI 清单」（comfyui 即此类），不等于
    「渠道里一个模型都没有」。没有目录时不裁决、不清绑定，否则一次没拉取就把用户的
    全部默认设置抹掉。
    """

    return bool(catalog_names_for_provider(provider))


def prune_stale_bindings(provider: str) -> dict[str, list[str]]:
    """[aipdd fork delta req40] 目录刷新后取消本渠道指向已消失模型的绑定。

    这是「从网关摘掉的模型为什么还会报错」的正解：绑定只在自己这一层被删掉，下一轮
    投影就不会再把这个名字注册回去，调用侧解析不到即前置拒绝，不会再有运行期 404。

    返回 ``{槽位: [被取消的模型名]}`` 供 sync 接口透出、前端提示「该默认因下架已取消」。
    只处理归属本渠道的条目；渠道无目录时直接返回空（不裁决）。
    """

    provider_key = str(provider or "").strip().lower()
    catalog = catalog_names_for_provider(provider_key)
    if not provider_key or not catalog:
        return {}
    removed: dict[str, list[str]] = {}

    alias_payload: dict[str, dict[str, Any]] = {}
    for alias, entry in get_newapi_model_alias_mappings().items():
        if str(entry.get("provider") or "").strip().lower() != provider_key:
            continue
        target = str(entry.get("target") or "").strip()
        if not target or target in catalog:
            continue
        removed.setdefault(str(alias), []).append(target)
        # 空 target 在 save 侧就是显式删除该 entry（alias 键保留给 UI 重新绑定）。
        alias_payload[str(alias)] = {"provider": provider_key, "target": ""}
    if alias_payload:
        save_newapi_model_alias_mappings(alias_payload, merge=True)

    media_mappings = get_newapi_media_model_mappings()
    stale_media = [
        str(model)
        for model, item in media_mappings.items()
        if str(item.get("provider") or "").strip().lower() == provider_key
        and (str(item.get("upstreamModel") or "").strip() or str(model)) not in catalog
    ]
    if stale_media:
        for model in stale_media:
            item = media_mappings.pop(model, None) or {}
            removed.setdefault(
                f"media:{str(item.get('mediaType') or 'media')}",
                [],
            ).append(str(item.get("upstreamModel") or "").strip() or model)
        save_newapi_media_model_mappings(media_mappings)

    embedding = get_newapi_embedding_model_config()
    if (
        str(embedding.get("provider") or "").strip().lower() == provider_key
        and str(embedding.get("upstreamModel") or "").strip()
        and str(embedding.get("upstreamModel") or "").strip() not in catalog
    ):
        removed.setdefault("embedding", []).append(
            str(embedding.get("upstreamModel") or "").strip()
        )
        _write_many({"custom_newapi_embedding_model": "{}"})

    return removed


# --------------------------------------------------------------------------- #
# [aipdd fork delta] 上游模型「是否真开通」的实测缓存（req：科普讲解视频 404）。
#
# 为什么需要它：火山方舟 ``GET /models`` 返回的是**平台支持的全量模型目录**，与本
# 账号已开通推理服务的模型是两件事（容器实测：抽 24 个只有 4 个能调通，其余一律
# 404 ``InvalidEndpointOrModel.NotFound``）。``custom_newapi_upstream_models_snapshot``
# 因此不能当作「可用清单」直接灌进工具页的模型候选，否则候选目录里绝大多数名字
# 都是踩下去就 404 的坑。这里只记录最小实测（一次 chat、max_tokens=1）的结论档位：
#   - ``ok``          上游接受了这个请求（HTTP 2xx）
#   - ``not-granted`` 上游明确说模型 / 接入点不存在或无权限（HTTP 404 那一族）
#   - ``not-vision``  （仅视觉）上游明确说这个名字读不了图
#   - ``unknown``     其余一切（限流、鉴权、超时、非 chat 协议的媒体模型…）
# 只有「上游明确判了不行」的档会把名字从候选里摘掉：拿不准的一律保留，绝不误伤。
#
# 文本与视觉各自一份缓存（``custom_newapi_{text,vision}_model_probe``）、结论互不
# 复用：视觉实测发的是**带一张极小图片**的请求，文本 ping 通说明不了能理解图片。
# --------------------------------------------------------------------------- #

TEXT_MODEL_PROBE_SETTING_KEY = "custom_newapi_text_model_probe"
VISION_MODEL_PROBE_SETTING_KEY = "custom_newapi_vision_model_probe"
TEXT_MODEL_PROBE_STATES = ("ok", "not-granted", "unknown")
# 视觉实测多一档 ``not-vision``：上游明确说这个名字读不了图（不支持图片输入）。
VISION_MODEL_PROBE_STATES = ("ok", "not-granted", "not-vision", "unknown")

_PROBE_KEYS: dict[str, tuple[str, tuple[str, ...]]] = {
    "text": (TEXT_MODEL_PROBE_SETTING_KEY, TEXT_MODEL_PROBE_STATES),
    "vision": (VISION_MODEL_PROBE_SETTING_KEY, VISION_MODEL_PROBE_STATES),
}


def _decode_model_probe(
    value: str | None, states: tuple[str, ...]
) -> dict[str, dict[str, Any]]:
    if not value:
        return {}
    try:
        raw = json.loads(value)
    except json.JSONDecodeError:
        return {}
    if not isinstance(raw, dict):
        return {}
    probe: dict[str, dict[str, Any]] = {}
    for provider, item in raw.items():
        provider_key = str(provider or "").strip().lower()
        if not provider_key or not isinstance(item, dict):
            continue
        models = item.get("models")
        if not isinstance(models, dict):
            continue
        cleaned = {
            str(name).strip(): str(state).strip().lower()
            for name, state in models.items()
            if str(name).strip() and str(state).strip().lower() in states
        }
        if not cleaned:
            continue
        probe[provider_key] = {
            "models": cleaned,
            "baseUrl": str(item.get("baseUrl") or "").strip(),
            "checkedAt": str(item.get("checkedAt") or "").strip(),
        }
    return probe


def _save_model_probe(
    kind: str,
    provider: str,
    states: dict[str, str],
    *,
    base_url: str = "",
    bump_checked_at: bool = True,
) -> None:
    """落盘某渠道的实测结论（写失败静默，与上游快照同口径）。

    增量合并：只覆盖本轮测到的名字，其余结论保留；空渠道 / 空结论是 no-op。

    [aipdd fork delta] ``bump_checked_at=False`` 是给「运行期真实失败回写」用的：
    一次偶发 404 不该把整渠道的 TTL 复测时钟拨到当下，否则其它还停在 ``unknown``
    的名字要多等一整个 TTL 才能被复测成结论。
    """

    setting_key, allowed = _PROBE_KEYS.get(kind) or _PROBE_KEYS["text"]
    provider_key = str(provider or "").strip().lower()
    clean = {
        str(name).strip(): str(state).strip().lower()
        for name, state in (states or {}).items()
        if str(name).strip() and str(state).strip().lower() in allowed
    }
    if not provider_key or not clean:
        return
    try:
        settings = get_model_gateway_settings()
        probe = _decode_model_probe(settings.get(setting_key), allowed)
        previous = probe.get(provider_key) or {}
        merged = dict(previous.get("models") or {})
        merged.update(clean)
        probe[provider_key] = {
            "models": merged,
            "baseUrl": str(base_url or "").strip().rstrip("/"),
            "checkedAt": (
                _now_iso()
                if bump_checked_at
                else str(previous.get("checkedAt") or "").strip() or _now_iso()
            ),
        }
        _write_many(
            {
                setting_key: json.dumps(
                    probe, ensure_ascii=False, separators=(",", ":")
                )
            }
        )
    except Exception:  # noqa: BLE001 - 实测缓存写失败不影响主流程
        return


def _get_model_probe(kind: str) -> dict[str, dict[str, Any]]:
    setting_key, allowed = _PROBE_KEYS.get(kind) or _PROBE_KEYS["text"]
    if not _uses_ce_gateway_settings():
        return {}
    settings = get_model_gateway_settings()
    return _decode_model_probe(settings.get(setting_key), allowed)


def save_text_model_probe(
    provider: str,
    states: dict[str, str],
    *,
    base_url: str = "",
    bump_checked_at: bool = True,
) -> None:
    """chat 文本实测三态（``ok`` / ``not-granted`` / ``unknown``）。"""

    _save_model_probe(
        "text", provider, states, base_url=base_url,
        bump_checked_at=bump_checked_at,
    )


def get_text_model_probe() -> dict[str, dict[str, Any]]:
    """{provider: {"models": {name: ok|not-granted|unknown}, ...}}；无缓存空表。"""

    return _get_model_probe("text")


def save_vision_model_probe(
    provider: str,
    states: dict[str, str],
    *,
    base_url: str = "",
    bump_checked_at: bool = True,
) -> None:
    """带图视觉实测结论（多一档 ``not-vision`` = 上游明确不读图）。"""

    _save_model_probe(
        "vision", provider, states, base_url=base_url,
        bump_checked_at=bump_checked_at,
    )


def get_vision_model_probe() -> dict[str, dict[str, Any]]:
    """{provider: {"models": {name: ok|not-granted|not-vision|unknown}, ...}}。"""

    return _get_model_probe("vision")


def is_internal_alias_name(name: str) -> bool:
    """[aipdd fork delta] ``DC-`` 前缀 = 内部功能位命名空间（非用户模型名）。"""

    return str(name or "").strip().casefold().startswith("dc-")


def build_channel_model_mapping(provider: str) -> dict[str, str]:
    """[aipdd fork delta] 渠道的「内部绑定」编译表：内部功能位别名 + 媒体映射 + embedding。

    拍平改造后：用户模型（上游真名）不再经别名表编译进渠道——它们的注册由
    ``build_channel_registrations`` 按「复合名全量 + 裸名单渠道唯一」规则生成；
    别名表只承载内部功能位（DC-*）的固定绑定。备援（fallback）机制退役：
    同一别名不再编译进第二个渠道。
    """
    provider_key = str(provider or "").strip().lower()
    if not provider_key:
        return {}
    # [aipdd fork delta req40] 目录裁决：有目录时，target 不在目录里就不编译（配置不能
    # 反向制造路由）；没目录时不裁决 —— 那是「还没同步」而不是「没有」，抹掉会把
    # comfyui 这类无清单渠道直接打成不可用。
    catalog = catalog_names_for_provider(provider_key)
    mapping: dict[str, str] = {}
    for alias, entry in get_newapi_model_alias_mappings().items():
        if str(entry.get("provider") or "").strip().lower() != provider_key:
            continue
        # [aipdd fork delta] 拍平：非内部功能位的用户别名不再编译（清单规则接管）。
        if not is_internal_alias_name(alias):
            continue
        target = str(entry.get("target") or "").strip()
        if target and (not catalog or target in catalog):
            mapping[alias] = target
    for model, item in get_newapi_media_model_mappings().items():
        if str(item.get("provider") or "").strip().lower() != provider_key:
            continue
        target = str(item.get("upstreamModel") or "").strip() or model
        if catalog and target not in catalog:
            continue
        mapping[model] = target
    embedding = get_newapi_embedding_model_config()
    if str(embedding.get("provider") or "").strip().lower() == provider_key:
        internal = str(
            embedding.get("internalModel") or DEFAULT_COGNEE_EMBEDDING_MODEL
        ).strip()
        upstream = str(embedding.get("upstreamModel") or "").strip()
        if internal and upstream and (not catalog or upstream in catalog):
            mapping[internal] = upstream
    return {alias: target for alias, target in mapping.items() if alias and target}


# [aipdd fork delta] 拍平模型路由：每个 (渠道, 模型) 组合以复合调用名
# ``{provider}::{model}`` 唯一注册进网关；裸名仅在「恰好一个渠道拥有该模型」时
# 注册（无歧义直通兼容）。没有优先级 / 备援 / 跨渠道抢注去重——同名多渠道一律
# 不注册裸名，必须由调用方显式选择复合名，系统不做任何隐式裁决。
PROVIDER_SPOT_SEPARATOR = "::"


def provider_spot_model(provider: str, model: str) -> str:
    """复合调用名：``{provider}::{model}``（拍平后网关注册与用户选择的唯一标识）。"""

    return (
        f"{str(provider or '').strip().lower()}"
        f"{PROVIDER_SPOT_SEPARATOR}{str(model or '').strip()}"
    )


def split_provider_spot_model(name: str) -> tuple[str, str]:
    """拆分复合调用名；不含分隔符时返回 ("", 原名)。"""

    clean = str(name or "").strip()
    if PROVIDER_SPOT_SEPARATOR not in clean:
        return "", clean
    provider, model = clean.split(PROVIDER_SPOT_SEPARATOR, 1)
    return provider.strip().lower(), model.strip()


def provider_declared_models(provider: str) -> set[str]:
    """[aipdd fork delta] 渠道「配置声明」的上游真名集合（零远程，不含快照）。

    内部绑定（DC-* 功能位 / 媒体 / embedding）目标 ∪ 别名真源里挂在本渠道路径
    上的任何 target（含迁移期遗留的用户别名与备援位）——「配置即事实」，上游
    快照拉不到时也保持可注册。拍平后「注册（复合名）⇄ 展示（广场 / 文本候选 /
    ASR 候选）」共用此口径，展示什么就能调什么。
    """

    provider_key = str(provider or "").strip().lower()
    if not provider_key:
        return set()
    declared = {
        str(target).strip()
        for target in build_channel_model_mapping(provider_key).values()
        if str(target).strip()
    }
    for entry in get_newapi_model_alias_mappings().values():
        if str(entry.get("provider") or "").strip().lower() == provider_key:
            declared.add(str(entry.get("target") or "").strip())
        if str(entry.get("fallbackProvider") or "").strip().lower() == provider_key:
            declared.add(str(entry.get("fallbackTarget") or "").strip())
    return {name for name in declared if name and "," not in name}


def _channel_owned_models(provider_key: str) -> set[str]:
    """渠道名下模型集合（零远程）：**只取目录**。

    [aipdd fork delta req40] 旧实现是「快照 ∪ ``provider_declared_models``」，后者从
    配置（DC-* 功能位 / 媒体 / embedding 绑定）反向拼名字 —— 于是「配置会制造目录」：
    只要还有槽位把某个已从渠道摘掉的模型写成 target，投影就会把它重新注册回网关，
    表现为「彻底去掉了还会报错」。现在目录是唯一真源：拉不回来的名字不可能再被注册。
    没同步过目录的渠道（如 comfyui）回空集，由调用方各自的兜底分支处理。
    """

    return {
        name
        for name in catalog_names_for_provider(provider_key)
        if "," not in name
    }


def model_owner_providers(model: str) -> list[str]:
    """拥有该模型名的渠道集合（按渠道存档顺序；拍平裸名注册的裁决依据）。"""

    name = str(model or "").strip()
    if not name:
        return []
    return [
        channel["provider"]
        for channel in get_newapi_provider_channels()
        if channel["provider"] != "comfyui"
        and name in _channel_owned_models(channel["provider"])
    ]


def build_channel_registrations(provider: str) -> dict[str, str]:
    """[aipdd fork delta] 渠道 provider 的完整拍平注册表：调用名 -> 上游模型名。

    规则（拍平改造）：
    1. 内部绑定（DC-* 功能位 / 媒体映射 / embedding）原样注册裸名——配置即事实；
    2. 该渠道名下每个模型注册复合名 ``{provider}::{model}``（总是，天然唯一）；
    3. 裸名 ``{model}`` 仅在「恰好一个渠道拥有」时注册：无歧义时保留直通兼容，
       跨渠道同名一律不注册裸名——同名必须由调用方显式选复合名，系统不裁决。
    """

    provider_key = str(provider or "").strip().lower()
    if not provider_key:
        return {}
    registrations: dict[str, str] = dict(build_channel_model_mapping(provider_key))

    def _owned(name: str) -> set[str]:
        # 展示场景专用入口（零远程）：comfyui 无 OpenAI 兼容清单，不参与拍平。
        return _channel_owned_models(name)

    if provider_key == "comfyui":
        return registrations
    mine = _owned(provider_key)
    others: set[str] = set()
    for channel in get_newapi_provider_channels():
        other = channel["provider"]
        if other == provider_key or other == "comfyui":
            continue
        others |= _owned(other)
    for model in sorted(mine):
        registrations[provider_spot_model(provider_key, model)] = model
        if model not in others:
            registrations.setdefault(model, model)
    return registrations


# [aipdd fork delta] 拍平改造：主 / 备优先级机制退役（resolve_channel_priority 与
# 两个默认优先级常量已删除）。渠道 priority 不再由系统按别名归属推导——每个
# 调用名最多只命中一个渠道（复合名天然唯一；裸名仅单渠道唯一时注册），网关侧
# 不存在需要优先级裁决的重叠路由。用户显式填写过 priority 时原样保留（由调用
# 方透传渠道存档值，系统不生成）。


def build_alias_channel_roles() -> dict[str, dict[str, Any]]:
    """[aipdd fork delta] 拍平后：注册调用名 -> 归属渠道（面板显示默认模型生效渠道）。

    键 = 该渠道网关注册的调用名（复合名 ``{provider}::{model}`` 或单渠道唯一
    裸名）；值 = {provider, target(调用名自身), capability(空), backup: False}。
    拍平后不存在备援——同一模型在每个渠道独立可选，跨渠道同名必须显式选复合名。
    """

    roles: dict[str, dict[str, Any]] = {}
    try:
        channels = get_newapi_provider_channels()
    except Exception:  # noqa: BLE001 - 状态展示不能因配置读取失败而崩
        return roles
    for channel in channels:
        provider = str(channel.get("provider") or "").strip().lower()
        if not provider or provider == "comfyui":
            continue
        try:
            registrations = build_channel_registrations(provider)
        except Exception:  # noqa: BLE001 - 单渠道失败不影响其余渠道
            continue
        for call_name in registrations:
            roles[call_name] = {
                "provider": provider,
                "target": call_name,
                "capability": "",
                "backup": False,
            }
    return roles


def model_alias_backfill_done() -> bool:
    """[aipdd fork delta] WP-A: 反向导入是否已跑过（非 CE 进程视为无需跑）。"""
    if not _uses_ce_gateway_settings():
        return True
    settings = get_model_gateway_settings()
    return bool(str(settings.get(MODEL_ALIAS_IMPORT_MARKER_KEY) or "").strip())


def backfill_newapi_model_alias_mappings(
    mappings: dict[str, Any],
) -> dict[str, Any]:
    """[aipdd fork delta] WP-A: 一次性反向导入。

    只补真源里还没有的别名（不覆盖用户已设值），并与完成标记同事务写入，保证
    整个部署生命周期只拉一次网关 mapping。
    """
    merged: dict[str, dict[str, Any]] = dict(get_newapi_model_alias_mappings())
    imported: list[str] = []
    for alias, item in (mappings or {}).items():
        alias_name = str(alias or "").strip()
        if not alias_name or alias_name in merged:
            continue
        decoded = _decode_alias_mappings(
            json.dumps({alias_name: item}, ensure_ascii=False)
        ).get(alias_name)
        if not decoded:
            continue
        merged[alias_name] = decoded
        imported.append(alias_name)
    _write_many(
        {
            MODEL_ALIAS_MAPPINGS_SETTING_KEY: json.dumps(
                merged,
                ensure_ascii=False,
                separators=(",", ":"),
            ),
            MODEL_ALIAS_IMPORT_MARKER_KEY: _now_iso(),
        }
    )
    return {
        "imported": len(imported),
        "total": len(merged),
        "aliases": sorted(imported),
    }


# [aipdd fork delta] 渠道 provider 重命名（openai → newapi）的一次性迁移标记。
PROVIDER_RENAME_MARKER_KEY = "custom_newapi_provider_renamed_to_newapi_at"


def provider_rename_done() -> bool:
    """[aipdd fork delta] openai→newapi 重命名迁移是否已跑过（非 CE 进程视为无需跑）。"""
    if not _uses_ce_gateway_settings():
        return True
    settings = get_model_gateway_settings()
    return bool(str(settings.get(PROVIDER_RENAME_MARKER_KEY) or "").strip())


def migrate_provider_openai_to_newapi() -> dict[str, Any]:
    """[aipdd fork delta] 把存量「openai」渠道标识全量重命名为「newapi」。

    五渠道收敛（aipdd / newapi / volcengine / comfyui / mediakit）：外部 NewAPI
    渠道在旧版本的 provider key 是 openai，与用户认知及前端展示（newapi）不一致。
    本迁移把 5 处存储里的 provider 引用一次性改写为 newapi，并与完成标记同事务
    写入（幂等，整个部署生命周期只跑一次）：
    1) custom_newapi_provider_channels（渠道存档；newapi 已存在时丢弃旧 openai 项）
    2) custom_newapi_model_alias_mappings（provider / fallbackProvider）
    3) custom_newapi_media_model_mappings（provider）
    4) custom_newapi_upstream_models_snapshot（provider 键）
    5) custom_newapi_embedding_model（provider）
    网关注册渠道（DC-openai）的改名由 newapi_provisioner 侧单独完成。
    """
    if not _uses_ce_gateway_settings():
        return {"skipped": "not-ce"}
    settings = get_model_gateway_settings()
    if str(settings.get(PROVIDER_RENAME_MARKER_KEY) or "").strip():
        return {"skipped": "already-migrated"}
    old, new = "openai", "newapi"
    values: dict[str, str] = {}
    changed = False

    # 1) 渠道存档。
    channels = _decode_provider_channels(
        settings.get("custom_newapi_provider_channels")
    )
    has_new = any(channel["provider"] == new for channel in channels)
    renamed_channels: list[dict[str, Any]] = []
    dropped_channels = 0
    replaced_channels = 0
    for channel in channels:
        if channel["provider"] == old:
            changed = True
            if has_new:
                dropped_channels += 1
                continue
            channel = {**channel, "provider": new}
            replaced_channels += 1
        renamed_channels.append(channel)
    if replaced_channels or dropped_channels:
        values["custom_newapi_provider_channels"] = json.dumps(
            renamed_channels, ensure_ascii=False, separators=(",", ":")
        )

    # 2) 别名真源（provider 与备援位）。
    aliases = _decode_alias_mappings(settings.get(MODEL_ALIAS_MAPPINGS_SETTING_KEY))
    renamed_aliases = 0
    for entry in aliases.values():
        if entry.get("provider") == old:
            entry["provider"] = new
            renamed_aliases += 1
        if entry.get("fallbackProvider") == old:
            entry["fallbackProvider"] = new
            renamed_aliases += 1
    if renamed_aliases:
        changed = True
        values[MODEL_ALIAS_MAPPINGS_SETTING_KEY] = json.dumps(
            aliases, ensure_ascii=False, separators=(",", ":")
        )

    # 3) 媒体映射。
    media = _decode_media_model_mappings(
        settings.get("custom_newapi_media_model_mappings")
    )
    renamed_media = 0
    for mapping in media.values():
        if mapping.get("provider") == old:
            mapping["provider"] = new
            renamed_media += 1
    if renamed_media:
        changed = True
        values["custom_newapi_media_model_mappings"] = json.dumps(
            media, ensure_ascii=False, separators=(",", ":")
        )

    # 4) 上游快照（键 = provider）。
    snapshot = _decode_upstream_models_snapshot(
        settings.get(UPSTREAM_MODELS_SNAPSHOT_SETTING_KEY)
    )
    renamed_snapshot = 0
    if old in snapshot:
        changed = True
        renamed_snapshot = 1
        if new not in snapshot:
            snapshot[new] = snapshot.pop(old)
        else:
            snapshot.pop(old)
        values[UPSTREAM_MODELS_SNAPSHOT_SETTING_KEY] = json.dumps(
            snapshot, ensure_ascii=False, separators=(",", ":")
        )

    # 5) embedding 配置（解不出有效配置时不动）。
    embedding = _decode_embedding_model_config(
        settings.get("custom_newapi_embedding_model")
    )
    renamed_embedding = 0
    if embedding.get("provider") == old:
        changed = True
        renamed_embedding = 1
        embedding["provider"] = new
        values["custom_newapi_embedding_model"] = json.dumps(
            embedding, ensure_ascii=False, separators=(",", ":")
        )

    values[PROVIDER_RENAME_MARKER_KEY] = _now_iso()
    _write_many(values)
    return {
        "migrated": changed,
        "channels": replaced_channels,
        "droppedChannels": dropped_channels,
        "aliases": renamed_aliases,
        "mediaModels": renamed_media,
        "snapshot": renamed_snapshot,
        "embedding": renamed_embedding,
    }


# [aipdd fork delta] 拍平改造（2026-10-01）的一次性迁移：存量渠道全量重投影为
# 拍平注册表（复合名 + 内部绑定 + 单渠道唯一裸名）的完成标记。升级前的渠道
# mapping 是旧注册口径（无复合名），用户选复合名会路由不到；本迁移在升级后
# 首次启动把存量渠道收敛到新口径（见 newapi_provisioner
# .project_flat_registrations_migration），成功后写标记（幂等，整个部署生命周期
# 只跑一次）。
FLAT_REGISTRATION_MARKER_KEY = "custom_newapi_flat_registrations_projected_at"


def flat_registration_migration_done() -> bool:
    """[aipdd fork delta] 拍平迁移（渠道全量重投影）是否已跑过（非 CE 进程视为无需跑）。"""
    if not _uses_ce_gateway_settings():
        return True
    settings = get_model_gateway_settings()
    return bool(str(settings.get(FLAT_REGISTRATION_MARKER_KEY) or "").strip())


def mark_flat_registration_migrated() -> None:
    """[aipdd fork delta] 记录拍平迁移已完成（由投影调用方在全部渠道成功后调用）。"""
    _write_many({FLAT_REGISTRATION_MARKER_KEY: _now_iso()})


def prune_legacy_alias_fallback_fields() -> dict[str, Any]:
    """[aipdd fork delta] 拍平迁移（本地步）：清理别名真源里的备援字段。

    拍平后主/备优先级机制整体退役，``fallbackProvider`` / ``fallbackTarget``
    不再参与任何编译（历史部署的真源可能残留这两个键——旧版保存路径写入过）。
    留着它们会在 ``provider_declared_models`` 里造成「备援渠道也声明该模型」的
    隐性归属，与拍平口径（每个模型按显式配置归属单渠道）相悖。纯本地、幂等：
    无残留时零写入、返回 0。
    """
    if not _uses_ce_gateway_settings():
        return {"pruned": 0}
    settings = get_model_gateway_settings()
    aliases = _decode_alias_mappings(settings.get(MODEL_ALIAS_MAPPINGS_SETTING_KEY))
    pruned = 0
    for entry in aliases.values():
        if "fallbackProvider" in entry or "fallbackTarget" in entry:
            entry.pop("fallbackProvider", None)
            entry.pop("fallbackTarget", None)
            pruned += 1
    if pruned:
        _write_many(
            {
                MODEL_ALIAS_MAPPINGS_SETTING_KEY: json.dumps(
                    aliases, ensure_ascii=False, separators=(",", ":")
                )
            }
        )
    return {"pruned": pruned}


# [aipdd fork delta #A] Ark(火山方舟)系 provider 的上游不是标准 OpenAI 兼容网关:
# 视频是异步任务式 /contents/generations/tasks,直接 POST {ark}/video/generations 会 404。
# 这类媒体必须由生效网关(内置 dramaclaw-gateway 的 volcengine 渠道)做协议转换,
# 因此这里不返回 base 覆盖,交回网关路由。aipdd / newapi 等真·OpenAI 兼容自带网关仍可覆盖。
_GATEWAY_ONLY_MEDIA_PROVIDERS = {
    "volcengine",
    "doubaovideo",
    "doubao",
    "ark",
    "jianying",
    "jimeng",
    # [aipdd fork delta req48] OpenAI / Claude / Gemini 三家官方渠道同理：它们是
    # **厂商原生 API**，不是 OpenAI 兼容网关，只是经网关里的原生适配器（type 1 /
    # 14 / 24）接进来。绕过网关直接拿渠道 base 去 POST {base}/images/generations、
    # {base}/video/generations，等于把网关的协议转换跳掉了（Anthropic 没有图/视频
    # 端点，Gemini 生图是 :generateContent 形态，OpenAI 的 Sora 也不是
    # /video/generations），只会拿到 404。媒体调用一律交回生效网关路由。
    "openai_official",
    "anthropic",
    "gemini",
}


def resolve_media_provider_override(
    model: str, *, media_type: str | None = None
) -> tuple[str, str, str] | None:
    """[aipdd fork delta #A] 按“选中模型所属 provider 渠道”返回其自带的 base_url+api_key。

    这是“多网关并存、逐组件路由到各自网关”的运行时入口：若选中模型在
    ``media_model_mappings`` 里绑定到了某个非官方 provider 渠道,且该渠道配了
    完整的 ``baseUrl``+``upstreamKey``(如 aipdd 这类 OpenAI 兼容网关),则返回
    ``(base_url, api_key, provider)`` 让调用方覆盖单一生效网关;否则返回 ``None``
    (沿用现有生效网关 / comfyui 行为)。合并上游时只需保留本函数及其调用点。
    """

    model_name = str(model or "").strip()
    if not model_name:
        return None
    try:
        from novelvideo.shared.runtime_env import is_ce_effective

        if not is_ce_effective():
            return None
    except Exception:  # pragma: no cover - defensive: edition probe must not break calls
        return None
    try:
        mapping = get_newapi_media_model_mappings().get(model_name) or {}
    except Exception:
        return None
    provider = str(mapping.get("provider") or "").strip().lower()
    if not provider or provider == "official":
        return None
    if provider in _GATEWAY_ONLY_MEDIA_PROVIDERS:
        # Ark 原生:图片/视频走生效网关协议转换,不绕过到 Ark base。
        return None
    if media_type:
        bound_type = str(mapping.get("mediaType") or "").strip().lower()
        if bound_type and bound_type != str(media_type).strip().lower():
            return None
    channel = get_newapi_provider_channel(provider) or {}
    base_url = str(channel.get("baseUrl") or "").strip().rstrip("/")
    api_key = str(channel.get("upstreamKey") or "").strip()
    # comfyui 渠道无 upstreamKey 且走协议不同的自有分支,不在此覆盖。
    if not base_url or not api_key or provider == "comfyui":
        return None
    return base_url, api_key, provider


def _extract_comfyui_workflow_for_model(
    comfyui: dict[str, Any], model: str
) -> dict[str, Any] | None:
    """从渠道 comfyui 配置里取出该模型对应的 API 格式工作流。

    两种存法都支持：``workflow_by_model[model]``，或 ``workflow_routes`` 里
    ``match.models == [model]`` 的路由（无 match 时取唯一路由）。
    """

    by_model = comfyui.get("workflow_by_model")
    if isinstance(by_model, dict):
        workflow = by_model.get(model)
        if isinstance(workflow, dict) and workflow:
            return workflow
    routes = comfyui.get("workflow_routes")
    if isinstance(routes, list):
        matched: list[dict[str, Any]] = []
        for route in routes:
            if not isinstance(route, dict):
                continue
            match = route.get("match")
            models = match.get("models") if isinstance(match, dict) else None
            workflow = route.get("workflow")
            if not isinstance(workflow, dict) or not workflow:
                continue
            if isinstance(models, list) and models:
                if str(models[0]).strip() == str(model).strip():
                    matched.append(workflow)
            else:
                matched.append(workflow)
        if len(matched) == 1:
            return matched[0]
    return None


def resolve_comfyui_channel_execution(
    model: str | None,
    media_type: str,
) -> tuple[str, dict[str, Any]] | None:
    """[aipdd fork delta] 渠道驱动的 ComfyUI 执行配置解析。

    当选中模型属于 comfyui 渠道且媒体类型匹配时，返回 (渠道 baseUrl, API 格式
    工作流)；否则返回 None（表示该模型不走直连 ComfyUI）。工作流缺失时抛可读
    错误，引导去渠道配置里补「模型 + 工作流」，绝不回落到任何硬编码模板。
    """

    model_name = str(model or "").strip()
    if not model_name:
        return None
    wanted = str(media_type or "").strip().lower()
    try:
        mapping = get_newapi_media_model_mappings().get(model_name) or {}
    except Exception:  # pragma: no cover - defensive settings probe
        return None
    if str(mapping.get("provider") or "").strip().lower() != "comfyui":
        return None
    bound_type = str(mapping.get("mediaType") or "video").strip().lower()
    if wanted and bound_type != wanted:
        return None
    channel = get_newapi_provider_channel("comfyui") or {}
    base_url = str(channel.get("baseUrl") or "").strip().rstrip("/")
    if not base_url:
        raise ValueError(
            f"ComfyUI 渠道 {model_name} 未配置服务器地址（baseUrl）"
        )
    settings = channel.get("settings") if isinstance(channel.get("settings"), dict) else {}
    comfyui = settings.get("comfyui") if isinstance(settings.get("comfyui"), dict) else {}
    workflow = _extract_comfyui_workflow_for_model(comfyui, model_name)
    if not isinstance(workflow, dict) or not workflow:
        raise ValueError(
            f"ComfyUI 渠道模型 {model_name} 未配置可用的工作流，请在渠道设置里"
            f"选择该名称的 ComfyUI 工作流后重试"
        )
    return base_url, workflow


def default_comfyui_channel_model(media_type: str) -> str | None:
    """[aipdd fork delta] 取某媒体类型下首个启用的 comfyui 渠道模型名。

    供字面量后端（如 ``comfyui``）解析到具体渠道模型，避免任何写死模型名。
    """

    wanted = str(media_type or "").strip().lower()
    if not wanted:
        return None
    try:
        mappings = get_newapi_media_model_mappings()
    except Exception:  # pragma: no cover - defensive settings probe
        return None
    ordered: list[tuple[int, int, str]] = []
    for index, (name, cfg) in enumerate(mappings.items()):
        if not isinstance(cfg, dict):
            continue
        if str(cfg.get("provider") or "").strip().lower() != "comfyui":
            continue
        if str(cfg.get("mediaType") or "video").strip().lower() != wanted:
            continue
        if cfg.get("enabled", True) is False:
            continue
        try:
            sort_order = int(cfg.get("sortOrder") or 100)
        except (TypeError, ValueError):
            sort_order = 100
        ordered.append((sort_order, index, str(name).strip()))
    if not ordered:
        return None
    ordered.sort(key=lambda item: (item[0], item[1]))
    return ordered[0][2]


def comfyui_declared_capabilities(
    settings: dict[str, Any], model: str
) -> dict[str, Any]:
    """[aipdd fork delta] 该 comfyui 模型「AI分析并自动配置」声明的能力。

    声明存在渠道设置 ``settings.comfyui.capabilities_by_model`` 里（媒体类型 /
    支持模式 / 分辨率与宽高比档位 / 时长 / 参考图上限等），用来替代此前按模型名与
    工作流名 token 猜档位的做法。没有声明时回 ``{}``（调用方自行回落）。
    """

    if not isinstance(settings, dict):
        return {}
    comfyui = settings.get("comfyui") if isinstance(settings.get("comfyui"), dict) else {}
    declared = comfyui.get("capabilities_by_model")
    entry = declared.get(str(model)) if isinstance(declared, dict) else None
    return dict(entry) if isinstance(entry, dict) else {}


def load_comfyui_adaptation(
    settings: dict[str, Any], model: str
) -> dict[str, Any] | None:
    """[aipdd fork delta] 按渠道指针读出该模型的适配条目。

    渠道设置里只存指针（``settings.comfyui.adaptation_by_model``），真正的节点绑定
    落在 ``<STATE_DIR>/comfyui/adaptations``。没做过分析回 ``None``；指针在、文件坏
    了抛 :class:`novelvideo.comfyui_adaptation.ComfyUIAdaptationError`（原文带上
    「重新执行 AI分析并自动配置」的出路），不会静默退回猜节点。
    """

    from novelvideo.comfyui_adaptation import load_adaptation

    return load_adaptation(
        settings if isinstance(settings, dict) else {}, str(model or "")
    )


def load_comfyui_channel_adaptation(model: str | None) -> dict[str, Any] | None:
    """[aipdd fork delta] 执行链入口：读 comfyui 渠道里该模型的适配条目。"""

    model_name = str(model or "").strip()
    if not model_name:
        return None
    try:
        channel = get_newapi_provider_channel("comfyui") or {}
    except Exception:  # pragma: no cover - defensive settings probe
        return None
    settings = channel.get("settings") if isinstance(channel.get("settings"), dict) else {}
    return load_comfyui_adaptation(settings, model_name)


def detect_comfyui_media_type(
    settings: dict[str, Any], model: str
) -> str | None:
    """[aipdd fork delta] 依据渠道工作流的输出节点推断该 comfyui 模型的媒体类型。

    回 ``"image"`` / ``"video"``；工作流缺失或输出节点非标准时回 ``None``（交由
    显式声明），不做任何模型名硬编码推断。

    先取「AI分析并自动配置」声明的 ``mediaType``（那次分析看的是真实输出节点绑定），
    没有声明才回落到输出节点类名探测。
    """

    from novelvideo.comfyui_adaptation import MEDIA_TYPES
    from novelvideo.comfyui_executor import detect_media_type_from_workflow

    if not isinstance(settings, dict):
        return None
    declared_type = str(
        comfyui_declared_capabilities(settings, model).get("mediaType") or ""
    ).strip().lower()
    if declared_type in MEDIA_TYPES:
        return declared_type
    comfyui = settings.get("comfyui") if isinstance(settings.get("comfyui"), dict) else {}
    workflow = _extract_comfyui_workflow_for_model(comfyui, model)
    return detect_media_type_from_workflow(workflow) if workflow else None


def is_comfyui_channel_model(model: str | None, media_type: str | None = None) -> bool:
    """[aipdd fork delta] 判断模型是否属于 comfyui 渠道（可选媒体类型匹配）。

    用于分发入口：命中的模型必须走直连渠道 ComfyUI，绝不回落 NewAPI 桥接或
    任何硬编码模板。
    """

    model_name = str(model or "").strip()
    if not model_name:
        return False
    try:
        mapping = get_newapi_media_model_mappings().get(model_name) or {}
    except Exception:  # pragma: no cover - defensive settings probe
        return False
    if str(mapping.get("provider") or "").strip().lower() != "comfyui":
        return False
    if media_type:
        bound_type = str(mapping.get("mediaType") or "video").strip().lower()
        if bound_type != str(media_type).strip().lower():
            return False
    return mapping.get("enabled", True) is not False


def parse_comfyui_channel_workflows(
    settings: dict[str, Any],
) -> tuple[list[str], list[str]]:
    comfyui = settings.get("comfyui")
    if not isinstance(comfyui, dict):
        raise ValueError("ComfyUI settings are required")
    routes = comfyui.get("workflow_routes")
    if routes is not None:
        if not isinstance(routes, list) or not routes:
            raise ValueError("ComfyUI requires at least one workflow route")
        route_ids: list[str] = []
        configured_model = str(comfyui.get("model_name") or "").strip()
        models: set[str] = {configured_model} if configured_model else set()
        for route in routes:
            if not isinstance(route, dict):
                raise ValueError("each ComfyUI workflow route must be an object")
            route_id = str(route.get("id") or "").strip()
            workflow = route.get("workflow")
            match = route.get("match")
            route_models = match.get("models") if isinstance(match, dict) else None
            if not route_id or route_id in route_ids:
                raise ValueError("each ComfyUI workflow route requires a unique id")
            if route_models is not None:
                if (
                    not isinstance(route_models, list)
                    or len(route_models) != 1
                    or not str(route_models[0] or "").strip()
                ):
                    raise ValueError(
                        "each ComfyUI workflow route accepts at most one model name"
                    )
                models.add(str(route_models[0]).strip())
            if not isinstance(workflow, dict) or not workflow:
                raise ValueError(
                    "each ComfyUI workflow route requires an API Format JSON object"
                )
            if not any(
                isinstance(node, dict) and ("class_type" in node or "inputs" in node)
                for node in workflow.values()
            ):
                raise ValueError(
                    f"ComfyUI workflow {route_id} must use exported API Format"
                )
            route_ids.append(route_id)
        if len(models) != 1:
            raise ValueError("a ComfyUI channel supports one model name")
        return sorted(models), route_ids

    workflows = comfyui.get("workflow_by_model")
    if not isinstance(workflows, dict) or not workflows:
        raise ValueError("ComfyUI requires at least one model workflow")
    for model, workflow in workflows.items():
        if (
            not str(model or "").strip()
            or not isinstance(workflow, dict)
            or not workflow
        ):
            raise ValueError(
                "each ComfyUI workflow requires a model name and JSON object"
            )
        if not any(
            isinstance(node, dict) and ("class_type" in node or "inputs" in node)
            for node in workflow.values()
        ):
            raise ValueError(
                f"ComfyUI workflow for {model} must use exported API Format"
            )
    return (
        [str(model).strip() for model in workflows],
        [str(model) for model in workflows],
    )


def save_newapi_provider_channels(
    channels: list[dict[str, Any]],
    *,
    preserve_unmentioned: bool = False,
) -> list[dict[str, Any]]:
    current_settings = get_model_gateway_settings()
    existing_channels = _decode_provider_channels(
        current_settings.get("custom_newapi_provider_channels")
    )
    # [aipdd fork delta] 未提及而被 preserve_unmentioned 保留的渠道同样走归一，
    # 避免旧裸域名随该路径原样回写。
    for channel in existing_channels:
        channel["baseUrl"] = normalize_channel_base_url(
            channel.get("provider", ""), channel.get("baseUrl")
        )
    existing_by_provider = {
        channel["provider"]: channel for channel in existing_channels
    }
    normalized: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in channels:
        provider = str(item.get("provider") or "").strip().lower()
        if not provider or provider in seen:
            continue
        seen.add(provider)
        previous = existing_by_provider.get(provider, {})
        upstream_key = str(item.get("upstreamKey") or "").strip() or previous.get(
            "upstreamKey",
            "",
        )
        base_url = str(item.get("baseUrl") or "").strip().rstrip("/")
        # [aipdd fork delta] 保存期统一归一 baseUrl（补齐 /v1、/api/v3 版本后缀），
        # 保证存储值包含完整版本路径：媒体直连 / 拉取上游模型 / 自动发现 embedding
        # 需要完整 URL；NewAPI 网关内部对 Ark type=45 自行拼路径，写网关 payload
        # 时由 build_channel_payload 统一剥后缀。
        base_url = normalize_channel_base_url(provider, base_url)
        channel_type = max(
            0,
            int(item.get("type") or previous.get("type") or 0),
        )
        if provider == "comfyui" and channel_type == 0:
            channel_type = 63
        raw_priority = item.get("priority")
        priority = int(
            previous.get("priority", 0) if raw_priority is None else raw_priority
        )
        raw_settings = item.get("settings", previous.get("settings", {}))
        channel_settings = raw_settings if isinstance(raw_settings, dict) else {}
        if provider == "comfyui":
            if not base_url:
                raise ValueError("baseUrl is required for provider comfyui")
            parse_comfyui_channel_workflows(channel_settings)
        if not upstream_key and provider != "comfyui":
            raise ValueError(f"upstreamKey is required for provider {provider}")
        normalized.append(
            {
                "provider": provider,
                "type": channel_type,
                "upstreamKey": upstream_key,
                "baseUrl": base_url,
                "priority": priority,
                "settings": channel_settings,
            }
        )
    if preserve_unmentioned:
        normalized.extend(
            channel
            for provider, channel in existing_by_provider.items()
            if provider not in seen
        )
    values = {
        "custom_newapi_provider_channels": json.dumps(
            normalized,
            ensure_ascii=False,
            separators=(",", ":"),
        )
    }
    if not preserve_unmentioned:
        remaining_providers = {channel["provider"] for channel in normalized}
        removed_providers = set(existing_by_provider) - remaining_providers
        if removed_providers:
            media_mappings = _decode_media_model_mappings(
                current_settings.get("custom_newapi_media_model_mappings")
            )
            media_mappings = {
                model: mapping
                for model, mapping in media_mappings.items()
                if mapping.get("provider") not in removed_providers
            }
            values["custom_newapi_media_model_mappings"] = json.dumps(
                media_mappings,
                ensure_ascii=False,
                separators=(",", ":"),
            )
            embedding_model = _decode_embedding_model_config(
                current_settings.get("custom_newapi_embedding_model")
            )
            if embedding_model.get("provider") in removed_providers:
                values["custom_newapi_embedding_model"] = "{}"
            # [aipdd fork delta] WP-A: 删渠道时同步清掉挂在被删 provider（含备援位）
            # 上的别名，避免真源里留下悬空映射。
            alias_mappings = _decode_alias_mappings(
                current_settings.get(MODEL_ALIAS_MAPPINGS_SETTING_KEY)
            )
            kept_aliases = {
                alias: entry
                for alias, entry in alias_mappings.items()
                if entry.get("provider") not in removed_providers
                and entry.get("fallbackProvider") not in removed_providers
            }
            if len(kept_aliases) != len(alias_mappings):
                values[MODEL_ALIAS_MAPPINGS_SETTING_KEY] = json.dumps(
                    kept_aliases,
                    ensure_ascii=False,
                    separators=(",", ":"),
                )
    _write_many(values)
    return normalized


def get_newapi_media_model_mappings() -> dict[str, dict[str, Any]]:
    if not _uses_ce_gateway_settings():
        return {}
    settings = get_model_gateway_settings()
    return _decode_media_model_mappings(
        settings.get("custom_newapi_media_model_mappings")
    )


# [aipdd fork delta] 拆解 LingShan-G2/NB-2 一体化模型：默认出图模型改由「默认生成
# 模型」面板写入的 media model mappings 决定（同类型里 enabled、sortOrder 最小者为
# 默认），其次 env 覆盖，最后兜底常量。scene_360 / prop / scene_ref / sketch /
# stage / image config 等消费点统一走这里，不再各自硬编码模型名。
def _default_media_model_from_mappings(media_type: str) -> str:
    wanted = str(media_type or "").strip().lower()
    if not wanted:
        return ""
    try:
        mappings = get_newapi_media_model_mappings()
    except Exception:  # pragma: no cover - defensive: settings probe must not break
        return ""
    entries: list[tuple[str, dict[str, Any]]] = []
    for name, cfg in mappings.items():
        if not isinstance(cfg, dict):
            continue
        if str(cfg.get("mediaType") or "").strip().lower() != wanted:
            continue
        if cfg.get("enabled", True) is False:
            continue
        entries.append((str(name).strip(), cfg))
    if not entries:
        return ""

    def _sort_key(kv: tuple[str, dict[str, Any]]) -> tuple[int, str]:
        raw = kv[1].get("sortOrder")
        try:
            order = int(raw)
        except (TypeError, ValueError):
            order = 100
        return (order, kv[0])

    entries.sort(key=_sort_key)
    return entries[0][0]


def get_effective_default_image_model() -> str:
    """Resolve the default image model: DB mappings -> env -> bundled const."""
    from novelvideo import config as _config

    db_model = _default_media_model_from_mappings("image")
    if db_model:
        return db_model
    env_model = str(os.environ.get("NEWAPI_IMAGE_MODEL") or "").strip()
    return env_model or str(getattr(_config, "NEWAPI_IMAGE_MODEL", "") or "").strip()


def get_effective_default_video_model() -> str:
    """Resolve the default video model: DB mappings -> env -> bundled const."""
    from novelvideo import config as _config

    db_model = _default_media_model_from_mappings("video")
    if db_model:
        return db_model
    env_model = str(os.environ.get("NEWAPI_VIDEO_MODEL") or "").strip()
    return env_model or str(getattr(_config, "NEWAPI_VIDEO_MODEL", "") or "").strip()


def get_effective_default_nanobanana_model() -> str:
    """Resolve the default secondary (nanobanana) image model.

    Falls back to the primary default image when no dedicated nanobanana model
    is configured, so a single configured default image covers both slots.
    """
    from novelvideo import config as _config

    env_model = str(os.environ.get("NEWAPI_NANOBANANA2_MODEL") or "").strip()
    if env_model:
        return env_model
    db_model = _default_media_model_from_mappings("image")
    if db_model:
        return db_model
    return str(getattr(_config, "NEWAPI_NANOBANANA2_MODEL", "") or "").strip()


def resolve_default_media_model(media_type: str) -> str:
    """Resolve the configured default model for an arbitrary media type."""
    wanted = str(media_type or "").strip().lower()
    if wanted == "image":
        return get_effective_default_image_model()
    return _default_media_model_from_mappings(wanted)


def get_effective_default_text_model() -> str:
    """Best-effort echo of the effective default text LLM for status display.

    The authoritative text routing is per-feature via gateway modelMapping; this
    only surfaces a representative id for the settings UI combination summary.

    [aipdd fork delta] 与 freezone 纯文本执行链对齐：显式 env ``NEWAPI_TEXT_MODEL``
    优先；否则复用 ``FREEZONE_TEXT_WRITER_MODEL`` 的解析链（per-env →
    ``TEXT_MODEL_FALLBACK`` → 槽位绑定）。此前只读 env/config，未走兜底链——明明设了
    ``TEXT_MODEL_FALLBACK`` 仍回显空串，工具页只能显示「跟随系统默认」而看不到实际会
    用的模型。

    [req40] 这只是**状态回显**，不是调用：解析不到（槽位未绑定 / 绑定目标已下架）就回
    空串，让设置页显示「未绑定」，绝不在展示路径上抛 ``ModelSlotNotConfigured``。真正
    的提交前拒绝发生在调用链上。
    """
    from novelvideo import config as _config
    from novelvideo.config import get_newapi_text_model_name
    from novelvideo.model_slots import ModelSlotNotConfigured
    from novelvideo.official_defaults import DEFAULT_FREEZONE_TEXT_WRITER_MODEL

    env_model = str(os.environ.get("NEWAPI_TEXT_MODEL") or "").strip()
    if env_model:
        return env_model
    legacy = str(getattr(_config, "NEWAPI_TEXT_MODEL", "") or "").strip()
    if legacy:
        return legacy
    try:
        return get_newapi_text_model_name(
            "FREEZONE_TEXT_WRITER_MODEL", DEFAULT_FREEZONE_TEXT_WRITER_MODEL
        )
    except ModelSlotNotConfigured:
        return ""


def build_default_model_combination_status() -> dict[str, Any]:
    embedding = get_newapi_embedding_model_config() or {}
    return {
        "text": get_effective_default_text_model(),
        "image": get_effective_default_image_model(),
        "embedding": str(embedding.get("upstreamModel") or "").strip(),
    }


def save_newapi_media_model_mappings(
    mappings: dict[str, dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    from novelvideo.catalog.media_model_request_schema import (
        normalize_media_model_catalog_config,
        validate_media_model_catalog_config,
    )

    normalized: dict[str, dict[str, Any]] = {}
    for model, item in mappings.items():
        model_name = str(model or "").strip()
        if not model_name:
            continue
        provider = str(item.get("provider") or "").strip().lower()
        if not provider:
            raise ValueError(f"provider is required for media model {model_name}")
        media_type = str(item.get("mediaType") or "").strip().lower()
        config = item.get("config") if isinstance(item.get("config"), dict) else {}
        if media_type in {"image", "video"}:
            config = normalize_media_model_catalog_config(config)
            validate_media_model_catalog_config(config, media_type)
        normalized_item: dict[str, Any] = {
            "provider": provider,
            "upstreamModel": str(item.get("upstreamModel") or "").strip(),
        }
        if media_type in {"image", "video", "audio", "music"}:
            normalized_item["mediaType"] = media_type
        label = str(item.get("label") or "").strip()
        if label:
            normalized_item["label"] = label
        if "enabled" in item:
            normalized_item["enabled"] = item.get("enabled") is not False
        if "sortOrder" in item:
            # [aipdd fork delta] 同 decode 路径：保留合法的 sortOrder=0。
            normalized_item["sortOrder"] = (
                int(item["sortOrder"])
                if item.get("sortOrder") is not None
                else 100
            )
        if config:
            normalized_item["config"] = config
        normalized[model_name] = normalized_item
    _write_many(
        {
            "custom_newapi_media_model_mappings": json.dumps(
                normalized,
                ensure_ascii=False,
                separators=(",", ":"),
            )
        }
    )
    return normalized


def build_newapi_media_model_mappings_status() -> dict[str, dict[str, Any]]:
    return get_newapi_media_model_mappings()


def _official_media_catalog_bundle_path() -> Path:
    return Path(__file__).with_name("official_media_models.json")


def _official_media_catalog_cache_path() -> Path:
    from novelvideo import config

    return Path(config.STATE_DIR) / "local" / "official_media_models.json"


def _read_official_media_catalog(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError("official media model configuration is invalid") from exc
    try:
        return _validate_official_media_catalog(payload)
    except ValueError as exc:
        raise RuntimeError("official media model configuration is invalid") from exc


def _effective_official_media_catalog() -> tuple[dict[str, Any], str]:
    bundled = _read_official_media_catalog(_official_media_catalog_bundle_path())
    cache_path = _official_media_catalog_cache_path()
    if cache_path.is_file():
        try:
            cached = _read_official_media_catalog(cache_path)
            if _catalog_version(cached) >= _catalog_version(bundled):
                return cached, "remote"
        except RuntimeError:
            pass
    return bundled, "bundled"


def _official_media_catalog_digest(payload: dict[str, Any]) -> str:
    encoded = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def get_official_media_catalog_update_status() -> dict[str, Any]:
    settings = _read_all()
    payload, source = _effective_official_media_catalog()
    digest = _official_media_catalog_digest(payload)
    metadata_matches = settings.get(OFFICIAL_MEDIA_CATALOG_SHA256_KEY) == digest
    return {
        "autoUpdate": settings.get(OFFICIAL_MEDIA_CATALOG_AUTO_UPDATE_KEY, "0")
        != "0",
        "source": source,
        "schemaVersion": int(payload.get("version") or 1),
        "catalogVersion": str(
            payload.get("catalogVersion") or payload.get("version") or "1"
        ),
        "modelCount": len(payload["mediaModels"]),
        "lastCheckedAt": settings.get(OFFICIAL_MEDIA_CATALOG_LAST_CHECKED_KEY, ""),
        "sha256": digest,
        "revision": (
            settings.get(OFFICIAL_MEDIA_CATALOG_REVISION_KEY, "")
            if metadata_matches
            else ""
        ),
        "publishedAt": (
            settings.get(OFFICIAL_MEDIA_CATALOG_PUBLISHED_AT_KEY, "")
            if metadata_matches
            else ""
        ),
        "remoteUrl": settings.get(OFFICIAL_MEDIA_CATALOG_REMOTE_URL_KEY, ""),
        "lastError": settings.get(OFFICIAL_MEDIA_CATALOG_LAST_ERROR_KEY, ""),
    }


def save_official_media_catalog_auto_update(enabled: bool) -> dict[str, Any]:
    _write_many({OFFICIAL_MEDIA_CATALOG_AUTO_UPDATE_KEY: "1" if enabled else "0"})
    return get_official_media_catalog_update_status()


def get_official_media_catalog_remote_etag(source_url: str) -> str:
    settings = _read_all()
    if settings.get(OFFICIAL_MEDIA_CATALOG_REMOTE_URL_KEY) != str(source_url).strip():
        return ""
    payload, _source = _effective_official_media_catalog()
    if settings.get(OFFICIAL_MEDIA_CATALOG_SHA256_KEY) != _official_media_catalog_digest(
        payload
    ):
        return ""
    return settings.get(OFFICIAL_MEDIA_CATALOG_ETAG_KEY, "")


def record_official_media_catalog_check(
    *, source_url: str, etag: str = "", error: str = ""
) -> dict[str, Any]:
    values = {
        OFFICIAL_MEDIA_CATALOG_LAST_CHECKED_KEY: _now_iso(),
        OFFICIAL_MEDIA_CATALOG_REMOTE_URL_KEY: str(source_url or "").strip(),
        OFFICIAL_MEDIA_CATALOG_LAST_ERROR_KEY: str(error or "").strip()[:500],
        OFFICIAL_MEDIA_CATALOG_ETAG_KEY: str(etag or "").strip(),
    }
    _write_many(values)
    return get_official_media_catalog_update_status()


def install_official_media_catalog(
    payload: dict[str, Any],
    *,
    source_url: str,
    expected_sha256: str = "",
    revision: str = "",
    published_at: str = "",
    etag: str = "",
) -> tuple[bool, dict[str, Any]]:
    validated = _validate_official_media_catalog(payload)
    candidate_digest = _official_media_catalog_digest(validated)
    normalized_expected_digest = str(expected_sha256 or "").strip().lower()
    if normalized_expected_digest and candidate_digest != normalized_expected_digest:
        raise ValueError("official media catalog SHA256 does not match manifest")
    with _OFFICIAL_MEDIA_CATALOG_WRITE_LOCK:
        current, _source = _effective_official_media_catalog()
        candidate_version = _catalog_version(validated)
        current_version = _catalog_version(current)
        current_digest = _official_media_catalog_digest(current)
        if candidate_version < current_version:
            raise ValueError("official media catalog downgrade is not allowed")
        if candidate_version == current_version and candidate_digest != current_digest:
            raise ValueError(
                "official media catalog version already exists with different content"
            )
        updated = candidate_digest != current_digest
        if updated:
            cache_path = _official_media_catalog_cache_path()
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            temporary_path: Path | None = None
            try:
                with tempfile.NamedTemporaryFile(
                    mode="w",
                    encoding="utf-8",
                    dir=cache_path.parent,
                    prefix=f".{cache_path.name}.",
                    suffix=".tmp",
                    delete=False,
                ) as temporary:
                    temporary.write(
                        json.dumps(validated, ensure_ascii=False, indent=2) + "\n"
                    )
                    temporary.flush()
                    os.fsync(temporary.fileno())
                    temporary_path = Path(temporary.name)
                temporary_path.replace(cache_path)
            finally:
                if temporary_path is not None:
                    temporary_path.unlink(missing_ok=True)
    _write_many(
        {
            OFFICIAL_MEDIA_CATALOG_LAST_CHECKED_KEY: _now_iso(),
            OFFICIAL_MEDIA_CATALOG_REMOTE_URL_KEY: str(source_url or "").strip(),
            OFFICIAL_MEDIA_CATALOG_ETAG_KEY: str(etag or "").strip(),
            OFFICIAL_MEDIA_CATALOG_REVISION_KEY: str(revision or "").strip(),
            OFFICIAL_MEDIA_CATALOG_PUBLISHED_AT_KEY: str(published_at or "").strip(),
            OFFICIAL_MEDIA_CATALOG_SHA256_KEY: candidate_digest,
            OFFICIAL_MEDIA_CATALOG_LAST_ERROR_KEY: "",
        }
    )
    return updated, get_official_media_catalog_update_status()


def get_official_media_model_mappings() -> dict[str, dict[str, Any]]:
    payload, _source = _effective_official_media_catalog()
    models = payload["mediaModels"]
    return {
        str(model): dict(item)
        for model, item in models.items()
        if str(model).strip() and isinstance(item, dict)
    }


def _media_model_catalog(
    mappings: dict[str, dict[str, Any]],
    media_type: str,
    *,
    provider: str | None = None,
    include_disabled: bool = False,
) -> list[dict[str, Any]]:
    from novelvideo.catalog.media_model_request_schema import normalize_media_model_catalog_config

    wanted = str(media_type or "").strip().lower()
    # [aipdd fork delta] 放开 audio：画布音频节点要按类型选模型，候选来自后台
    # 「默认生成模型」面板里 mediaType=audio 的条目（此前这里只认 image/video，
    # audio 目录永远是空的）。
    # [aipdd fork delta req46] 再放开 music：工具页「音乐创作」的模型候选同样只来自
    # 设置页配的条目，与 image/video/audio 三目录同语义。
    if wanted not in {"image", "video", "audio", "music"}:
        return []
    result: list[dict[str, Any]] = []
    for model, item in mappings.items():
        if provider and item.get("provider") != provider:
            continue
        disabled = item.get("enabled") is False
        if item.get("mediaType") != wanted or (disabled and not include_disabled):
            continue
        config = item.get("config") if isinstance(item.get("config"), dict) else {}
        config = normalize_media_model_catalog_config(config)
        config.setdefault(
            "request",
            {
                "endpoint": (
                    "images/generations"
                    if wanted == "image"
                    else "video/generations"
                    if wanted == "video"
                    else "music_generation"
                    if wanted == "music"
                    else "audio/speech"
                ),
                "parameters": [],
            },
        )
        gateway_model = str(item.get("upstreamModel") or model)
        api_model = model if wanted != "video" else f"newapi_{model}"
        aliases = item.get("aliases") if isinstance(item.get("aliases"), list) else []
        # [aipdd fork delta] 展示用渠道：条目在「默认生成模型」里配置的所属渠道
        # （aipdd / openai / volcengine / comfyui ...）。providerId 是执行身份——
        # 媒体调用统一经 LocalNewAPI，恒为 newapi；画布模型选择器读 channel 在模型旁
        # 做彩色渠道标注，未登记的渠道由前端回落成中性样式。
        channel = str(item.get("provider") or "").strip().lower()
        result.append(
            {
                **config,
                "catalogId": model,
                "catalog_id": model,
                "id": model,
                "providerId": "newapi",
                "provider": "newapi",
                "channel": channel,
                "apiModel": api_model,
                "api_model": api_model,
                "gatewayModel": gateway_model,
                "gateway_model": gateway_model,
                "aliases": [str(alias) for alias in aliases if str(alias).strip()],
                "label": str(item.get("label") or model),
                "sortOrder": int(item.get("sortOrder") or 100),
                **({"enabled": False} if disabled else {}),
            }
        )
    return sorted(result, key=lambda entry: (int(entry["sortOrder"]), str(entry["id"])))


def get_official_media_model_catalog(media_type: str) -> list[dict[str, Any]]:
    return _media_model_catalog(get_official_media_model_mappings(), media_type)


def get_ce_media_model_catalog(
    media_type: str,
    *,
    provider: str | None = None,
    include_disabled: bool = False,
) -> list[dict[str, Any]]:
    """Expose CE-local media settings using the EE catalog response contract."""
    return _media_model_catalog(
        get_newapi_media_model_mappings(),
        media_type,
        provider=provider,
        include_disabled=include_disabled,
    )


def get_newapi_embedding_model_config() -> dict[str, Any]:
    settings = get_model_gateway_settings()
    return _decode_embedding_model_config(settings.get("custom_newapi_embedding_model"))


def save_newapi_embedding_model_config(
    *,
    provider: str,
    upstream_model: str,
    dimension: int | str,
    batch_size: int | str | None = None,
    send_dimensions: bool = True,
) -> dict[str, Any]:
    normalized_provider = str(provider or "").strip().lower()
    normalized_upstream_model = str(upstream_model or "").strip()
    normalized_dimension = _int_setting(str(dimension), 0)
    normalized_batch_size = _int_setting(str(batch_size or ""), 0)
    if not normalized_provider:
        raise ValueError("provider is required for embedding model")
    if not normalized_upstream_model:
        raise ValueError("upstreamModel is required for embedding model")
    if normalized_dimension <= 0:
        raise ValueError("dimension must be positive")
    if batch_size not in (None, "") and normalized_batch_size <= 0:
        raise ValueError("batchSize must be positive")
    config = {
        "provider": normalized_provider,
        "upstreamModel": normalized_upstream_model,
        "dimension": normalized_dimension,
        "sendDimensions": True,
        "internalModel": "DC-cognee-embedding",
    }
    if normalized_batch_size > 0:
        config["batchSize"] = normalized_batch_size
    _write_many(
        {
            "custom_newapi_embedding_model": json.dumps(
                config,
                ensure_ascii=False,
                separators=(",", ":"),
            )
        }
    )
    return config


def build_newapi_embedding_model_status() -> dict[str, Any]:
    return get_newapi_embedding_model_config()


def build_newapi_provider_channels_status() -> list[dict[str, Any]]:
    return [
        {
            "provider": channel["provider"],
            "type": channel.get("type", 0),
            "configured": bool(channel["upstreamKey"])
            or (
                channel["provider"] == "comfyui"
                and bool(channel["baseUrl"])
                and bool(channel.get("settings"))
            ),
            "upstreamKeyPreview": mask_secret(channel["upstreamKey"]),
            "baseUrl": channel["baseUrl"],
            "priority": channel.get("priority", 0),
            "settings": channel.get("settings", {}),
        }
        for channel in get_newapi_provider_channels()
    ]


@dataclass(frozen=True)
class EffectiveMediaKitConfig:
    """[aipdd fork delta] 火山 AI MediaKit 视频高清（生成式超分）运行时配置。"""

    source: str
    api_key: str
    base_url: str
    backend: str


MEDIAKIT_DEFAULT_BASE_URL = "https://mediakit.cn-beijing.volces.com/api/v1"
_VALID_VIDEO_ENHANCE_BACKENDS = {"auto", "mediakit", "ffmpeg"}


def normalize_video_enhance_backend(value: str | None) -> str:
    backend = str(value or "").strip().lower()
    return backend if backend in _VALID_VIDEO_ENHANCE_BACKENDS else "auto"


def save_mediakit_config(
    *,
    api_key: str,
    base_url: str = "",
    backend: str = "auto",
) -> None:
    _write_many(
        {
            "mediakit_api_key": str(api_key or "").strip(),
            "mediakit_base_url": str(base_url or "").strip().rstrip("/"),
            "video_enhance_backend": normalize_video_enhance_backend(backend),
        }
    )


def get_effective_mediakit_config(
    *,
    env_api_key: str | None = None,
    env_base_url: str | None = None,
    env_backend: str | None = None,
) -> EffectiveMediaKitConfig:
    """[aipdd fork delta] CE 模式 MediaKit 凭证只认设置数据库（设置页 → 模型配置）。

    CE 下 API Key / Base URL 不再回退 ``MEDIAKIT_API_KEY`` / ``MEDIAKIT_BASE_URL``
    环境变量（避免密钥 env/DB 双真源）；Base URL 未配置时落到内置默认端点。
    非 CE（EE 无设置库）仍按环境变量解析。两种模式下 backend 都读运维级
    ``VIDEO_ENHANCE_BACKEND``（强制本地/云端的总开关，非密钥）。
    """
    backend_env = env_backend or os.environ.get("VIDEO_ENHANCE_BACKEND", "")
    if _uses_ce_gateway_settings():
        settings = get_model_gateway_settings()
        db_api_key = normalize_api_key(settings.get("mediakit_api_key", ""))
        db_base_url = str(settings.get("mediakit_base_url", "")).strip().rstrip("/")
        db_backend = str(settings.get("video_enhance_backend", "")).strip().lower()
        has_db_config = bool(db_api_key or db_base_url or db_backend)
        return EffectiveMediaKitConfig(
            source="database" if has_db_config else "unset",
            api_key=db_api_key,
            base_url=db_base_url or str(MEDIAKIT_DEFAULT_BASE_URL).strip().rstrip("/"),
            backend=normalize_video_enhance_backend(db_backend or backend_env),
        )
    return EffectiveMediaKitConfig(
        source="environment",
        api_key=normalize_api_key(env_api_key or os.environ.get("MEDIAKIT_API_KEY", "")),
        base_url=str(
            env_base_url
            or os.environ.get("MEDIAKIT_BASE_URL", "")
            or MEDIAKIT_DEFAULT_BASE_URL
        )
        .strip()
        .rstrip("/"),
        backend=normalize_video_enhance_backend(backend_env),
    )


def build_mediakit_status(
    *,
    env_backend: str | None = None,
) -> dict[str, Any]:
    effective = get_effective_mediakit_config(env_backend=env_backend)
    return {
        "source": effective.source,
        "apiKeyPreview": mask_secret(effective.api_key),
        "configured": bool(effective.api_key),
        "baseUrl": effective.base_url,
        "backend": effective.backend,
    }


def save_douyin_web_cookie(*, cookie: str) -> None:
    """[aipdd fork delta] 保存抖音 Web 登录态 Cookie（设置数据库单真源）。

    存前统一归一成 ``k=v; k2=v2`` 请求头格式（兼容 DevTools 整串、EditThisCookie
    的 JSON 数组与对象导出）。Cookie 是完整登录态，只写不读回显（状态接口只回
    条数与 Cookie 名，绝不回值）。
    """

    from novelvideo.freezone.douyin_web_api import normalize_cookie

    _write_many({"douyin_web_cookie": normalize_cookie(cookie)})


def get_douyin_web_cookie_setting() -> str:
    """设置数据库里的抖音 Cookie；未配置返回空串（不报错，由调用方回落 CDP）。"""

    if not _uses_ce_gateway_settings():
        return ""
    return str(get_model_gateway_settings().get("douyin_web_cookie", "")).strip()


def build_douyin_cookie_status() -> dict[str, Any]:
    """[aipdd fork delta] 抖音 Cookie 配置状态（只回非敏感线索，供设置页回显）。

    回「Cookie 名清单」而非值本身：用户能由此判断缺不缺 sessionid / ttwid 等关键
    凭据，而泄露面限于字段名。上限 12 个，避免把整串结构外推回去。
    """

    from novelvideo.freezone.douyin_web_api import normalize_cookie

    cookie = normalize_cookie(get_douyin_web_cookie_setting())
    names = [
        part.split("=", 1)[0].strip()
        for part in cookie.split(";")
        if "=" in part and part.split("=", 1)[0].strip()
    ]
    return {
        "configured": bool(cookie),
        "cookieCount": len(names),
        "cookieNames": names[:12],
        "length": len(cookie),
    }


def save_prompt_export_password(*, password: str) -> None:
    """[aipdd fork delta] operator 口令（导出提示词等）存设置数据库。"""
    _write_many({"prompt_export_password": str(password or "").strip()})


def get_prompt_export_password_setting() -> str:
    """设置数据库里的 operator 口令；CE 下未配置返回空串（fail-closed 交给调用方）。"""
    if not _uses_ce_gateway_settings():
        return ""
    return str(get_model_gateway_settings().get("prompt_export_password", "")).strip()


def save_media_relay_config(
    *,
    provider: str,
    ttl_seconds: int,
    endpoint: str = "",
    bucket: str = "",
    access_key_id: str = "",
    access_key_secret: str = "",
    cloud_name: str = "",
    cloudinary_api_key: str = "",
    cloudinary_api_secret: str = "",
    cloudinary_folder: str = "",
    aipdd_api_key: str | None = None,
    aipdd_base_url: str | None = None,
    aipdd_prefix: str = "",
    aipdd_register_asset: bool = False,
    # [aipdd fork delta] AWS S3 档：AK/SK/Token 沿用同一密钥语义（None=不改，空串=清空，
    # UI 会传回完整旧值）；region/bucket/endpoint 为普通字段。
    s3_region: str = "",
    s3_bucket: str = "",
    s3_access_key_id: str | None = None,
    s3_access_key_secret: str | None = None,
    s3_endpoint_url: str = "",
    s3_session_token: str | None = None,
) -> None:
    current = get_model_gateway_settings()
    # 密钥字段沿用现有语义：None = 不改，空串 = 清空（UI 会传回完整旧值）。
    next_aipdd_api_key = (
        normalize_api_key(aipdd_api_key)
        if aipdd_api_key is not None
        else str(current.get("aipdd_relay_api_key", "")).strip()
    )
    next_aipdd_base_url = (
        str(aipdd_base_url).strip().rstrip("/")
        if aipdd_base_url is not None
        else str(current.get("aipdd_relay_base_url", "")).strip()
    )
    # [aipdd fork delta] AWS S3 密钥字段沿用同一语义：None = 不改，否则取新值。
    next_s3_access_key_id = (
        normalize_api_key(s3_access_key_id)
        if s3_access_key_id is not None
        else str(current.get("s3_relay_ak", "")).strip()
    )
    next_s3_access_key_secret = (
        normalize_api_key(s3_access_key_secret)
        if s3_access_key_secret is not None
        else str(current.get("s3_relay_sk", "")).strip()
    )
    next_s3_session_token = (
        normalize_api_key(s3_session_token)
        if s3_session_token is not None
        else str(current.get("s3_relay_token", "")).strip()
    )
    _write_many(
        {
            "media_relay_provider": str(provider or "").strip().lower(),
            "media_relay_ttl_seconds": str(int(ttl_seconds)),
            "oss_relay_endpoint": str(endpoint or "").strip(),
            "oss_relay_bucket": str(bucket or "").strip(),
            "oss_relay_ak": str(access_key_id or "").strip(),
            "oss_relay_sk": str(access_key_secret or "").strip(),
            "cloudinary_relay_cloud_name": str(cloud_name or "").strip(),
            "cloudinary_relay_api_key": str(cloudinary_api_key or "").strip(),
            "cloudinary_relay_api_secret": str(cloudinary_api_secret or "").strip(),
            "cloudinary_relay_folder": str(cloudinary_folder or "").strip().strip("/"),
            # [aipdd fork delta req44] 数字资产云中转专属字段。
            "aipdd_relay_api_key": next_aipdd_api_key,
            "aipdd_relay_base_url": next_aipdd_base_url,
            "aipdd_relay_prefix": str(aipdd_prefix or "").strip().strip("/"),
            "aipdd_relay_register_asset": (
                "true" if _bool_setting(aipdd_register_asset, False) else "false"
            ),
            # [aipdd fork delta] AWS S3 档专属字段（与 OSS 并行，切档不抹除）。
            "s3_relay_region": str(s3_region or "").strip(),
            "s3_relay_bucket": str(s3_bucket or "").strip(),
            "s3_relay_ak": next_s3_access_key_id,
            "s3_relay_sk": next_s3_access_key_secret,
            "s3_relay_endpoint": str(s3_endpoint_url or "").strip(),
            "s3_relay_token": next_s3_session_token,
        }
    )


def get_model_gateway_settings() -> dict[str, str]:
    data = _read_all() if _uses_ce_gateway_settings() else {}
    # [aipdd fork delta] 默认走 LocalNewAPI custom，不再默认官方中转(RelayClaw)。
    data.setdefault("model_gateway_mode", MODE_CUSTOM)
    return data


def _official_base_url(settings: dict[str, str]) -> str:
    """「官方模式」网关地址：只看数据库里存过什么，再看 env；**代码不预设站点地址**。

    [aipdd fork delta req41] 旧实现把某个具体域名当常量兜底，于是「换了网关地址」
    和「不在本机部署」这两种情况下，旧域名还会被当成有效值打出去（CE 的 official
    栏甚至把 source 报成 database，其实那地址来自源码）。真源缺失就返回空，由状态
    页的 ``configured=false`` 与调用面的「未配置」报错说话。
    """

    return str(settings.get("official_newapi_base_url", "") or "").strip() or (
        official_newapi_base_url()
    )


def get_effective_newapi_config(
    *,
    explicit_config: EffectiveNewApiConfig | None = None,
    official_base_url: str | None = None,
    official_api_key: str | None = None,
) -> EffectiveNewApiConfig:
    if explicit_config is not None:
        if type(explicit_config) is not EffectiveNewApiConfig:
            raise TypeError("explicit_config must be an EffectiveNewApiConfig")
        return explicit_config
    if not _uses_ce_gateway_settings():
        return EffectiveNewApiConfig(
            mode=MODE_OFFICIAL,
            source="environment",
            base_url=normalize_relay_base_url(
                os.environ.get("NEWAPI_BASE_URL", "")
                or official_base_url
                or official_newapi_base_url()
            ),
            api_key=normalize_api_key(
                official_api_key
                if official_api_key is not None
                else os.environ.get("NEWAPI_API_KEY", "")
            ),
        )

    settings = get_model_gateway_settings()
    mode = normalize_gateway_mode(settings.get("model_gateway_mode"))
    return get_ce_newapi_config_for_mode(mode)


def get_ce_newapi_config_for_mode(mode: str) -> EffectiveNewApiConfig:
    """Return CE credentials for one gateway without changing the active mode.

    Embedding projects can remain bound to the gateway that created their vector
    space even when the installation's general model gateway is switched later.
    """

    if not _uses_ce_gateway_settings():
        raise RuntimeError("CE model gateway settings are not available in EE")

    settings = get_model_gateway_settings()
    mode = normalize_gateway_mode(mode)
    if mode == MODE_CUSTOM:
        return EffectiveNewApiConfig(
            mode=MODE_CUSTOM,
            source="custom",
            base_url=normalize_relay_base_url(
                settings.get("custom_newapi_base_url", "")
            ),
            api_key=normalize_api_key(settings.get("custom_newapi_api_key", "")),
        )
    db_official_api_key = normalize_api_key(settings.get("official_newapi_api_key", ""))
    return EffectiveNewApiConfig(
        mode=mode,
        source="hybrid" if mode == MODE_HYBRID else "official",
        base_url=normalize_relay_base_url(_official_base_url(settings)),
        api_key=db_official_api_key,
    )


def _int_setting(value: str | None, default: int) -> int:
    try:
        return int(str(value or "").strip())
    except ValueError:
        return default


def _bool_setting(value: Any, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def get_effective_media_relay_config(
    *,
    explicit_config: EffectiveMediaRelayConfig | None = None,
    env_provider: str | None = None,
    env_ttl_seconds: int | str | None = None,
    env_endpoint: str | None = None,
    env_bucket: str | None = None,
    env_access_key_id: str | None = None,
    env_access_key_secret: str | None = None,
    env_cloud_name: str | None = None,
    env_cloudinary_api_key: str | None = None,
    env_cloudinary_api_secret: str | None = None,
    env_cloudinary_folder: str | None = None,
    env_aipdd_prefix: str | None = None,
    env_aipdd_api_key: str | None = None,
    env_aipdd_base_url: str | None = None,
    env_aipdd_register_asset: bool | str | None = None,
    env_s3_region: str | None = None,
    env_s3_bucket: str | None = None,
    env_s3_access_key_id: str | None = None,
    env_s3_access_key_secret: str | None = None,
    env_s3_endpoint_url: str | None = None,
    env_s3_session_token: str | None = None,
) -> EffectiveMediaRelayConfig:
    if explicit_config is not None:
        if type(explicit_config) is not EffectiveMediaRelayConfig:
            raise TypeError("explicit_config must be an EffectiveMediaRelayConfig")
        return explicit_config
    settings = get_model_gateway_settings() if _uses_ce_gateway_settings() else {}
    db_provider = str(settings.get("media_relay_provider", "")).strip().lower()
    db_endpoint = str(settings.get("oss_relay_endpoint", "")).strip()
    db_bucket = str(settings.get("oss_relay_bucket", "")).strip()
    db_access_key_id = str(settings.get("oss_relay_ak", "")).strip()
    db_access_key_secret = str(settings.get("oss_relay_sk", "")).strip()
    db_cloud_name = str(settings.get("cloudinary_relay_cloud_name", "")).strip()
    db_cloudinary_api_key = str(settings.get("cloudinary_relay_api_key", "")).strip()
    db_cloudinary_api_secret = str(
        settings.get("cloudinary_relay_api_secret", "")
    ).strip()
    db_cloudinary_folder = (
        str(settings.get("cloudinary_relay_folder", "")).strip().strip("/")
    )
    # [aipdd fork delta req44] 数字资产云中转专属配置。
    db_aipdd_api_key = normalize_api_key(settings.get("aipdd_relay_api_key", ""))
    db_aipdd_base_url = str(settings.get("aipdd_relay_base_url", "")).strip().rstrip("/")
    db_aipdd_prefix = str(settings.get("aipdd_relay_prefix", "")).strip().strip("/")
    db_aipdd_register_asset = _bool_setting(
        settings.get("aipdd_relay_register_asset"), False
    )
    # [aipdd fork delta] AWS S3 档专属配置（与 OSS 并行，切档不抹除）。
    db_s3_region = str(settings.get("s3_relay_region", "")).strip()
    db_s3_bucket = str(settings.get("s3_relay_bucket", "")).strip()
    db_s3_access_key_id = normalize_api_key(settings.get("s3_relay_ak", ""))
    db_s3_access_key_secret = normalize_api_key(settings.get("s3_relay_sk", ""))
    db_s3_endpoint_url = str(settings.get("s3_relay_endpoint", "")).strip()
    db_s3_session_token = normalize_api_key(settings.get("s3_relay_token", ""))
    has_db_config = any(
        [
            db_provider,
            db_endpoint,
            db_bucket,
            db_access_key_id,
            db_access_key_secret,
            db_cloud_name,
            db_cloudinary_api_key,
            db_cloudinary_api_secret,
            db_cloudinary_folder,
            db_aipdd_api_key,
            db_aipdd_base_url,
            db_aipdd_prefix,
            db_aipdd_register_asset,
            db_s3_region,
            db_s3_bucket,
            db_s3_access_key_id,
            db_s3_access_key_secret,
            db_s3_endpoint_url,
            db_s3_session_token,
        ]
    )
    if has_db_config:
        return EffectiveMediaRelayConfig(
            source="database",
            provider=db_provider or "aliyun_oss",
            ttl_seconds=_int_setting(settings.get("media_relay_ttl_seconds"), 1800),
            endpoint=db_endpoint,
            bucket=db_bucket,
            access_key_id=db_access_key_id,
            access_key_secret=db_access_key_secret,
            cloud_name=db_cloud_name,
            cloudinary_api_key=db_cloudinary_api_key,
            cloudinary_api_secret=db_cloudinary_api_secret,
            cloudinary_folder=db_cloudinary_folder,
            aipdd_api_key=db_aipdd_api_key,
            aipdd_base_url=db_aipdd_base_url,
            aipdd_prefix=db_aipdd_prefix,
            aipdd_register_asset=db_aipdd_register_asset,
            s3_region=db_s3_region,
            s3_bucket=db_s3_bucket,
            s3_access_key_id=db_s3_access_key_id,
            s3_access_key_secret=db_s3_access_key_secret,
            s3_endpoint_url=db_s3_endpoint_url,
            s3_session_token=db_s3_session_token,
        )

    if _uses_ce_gateway_settings():
        # [aipdd fork delta] CE 模式但设置库该功能为空：数据库为唯一真源，
        # 绝不回退 env 密钥（OSS AK/SK、Cloudinary Key/Secret）。非密钥的
        # provider/endpoint/bucket 等部署配置仍保留下方 env 分支供非 CE 使用。
        return EffectiveMediaRelayConfig(
            source="unset",
            provider="",
            ttl_seconds=1800,
            endpoint="",
            bucket="",
            access_key_id="",
            access_key_secret="",
            cloud_name="",
            cloudinary_api_key="",
            cloudinary_api_secret="",
            cloudinary_folder="",
        )

    raw_ttl = (
        env_ttl_seconds
        if env_ttl_seconds is not None
        else os.environ.get(
            "MEDIA_RELAY_TTL_SECONDS",
            "1800",
        )
    )
    return EffectiveMediaRelayConfig(
        source="environment",
        provider=str(
            env_provider or os.environ.get("MEDIA_RELAY_PROVIDER", "local")
        )
        .strip()
        .lower(),
        ttl_seconds=_int_setting(str(raw_ttl), 1800),
        endpoint=str(env_endpoint or os.environ.get("OSS_RELAY_ENDPOINT", "")).strip(),
        bucket=str(env_bucket or os.environ.get("OSS_RELAY_BUCKET", "")).strip(),
        access_key_id=str(
            env_access_key_id or os.environ.get("OSS_RELAY_AK", "")
        ).strip(),
        access_key_secret=str(
            env_access_key_secret or os.environ.get("OSS_RELAY_SK", "")
        ).strip(),
        cloud_name=str(
            env_cloud_name or os.environ.get("CLOUDINARY_RELAY_CLOUD_NAME", "")
        ).strip(),
        cloudinary_api_key=str(
            env_cloudinary_api_key or os.environ.get("CLOUDINARY_RELAY_API_KEY", "")
        ).strip(),
        cloudinary_api_secret=str(
            env_cloudinary_api_secret
            or os.environ.get("CLOUDINARY_RELAY_API_SECRET", "")
        ).strip(),
        cloudinary_folder=str(
            env_cloudinary_folder or os.environ.get("CLOUDINARY_RELAY_FOLDER", "")
        )
        .strip()
        .strip("/"),
        aipdd_api_key=normalize_api_key(
            env_aipdd_api_key
            if env_aipdd_api_key is not None
            else os.environ.get("AIPDD_CLOUD_API_KEY", "")
        ),
        aipdd_base_url=str(
            env_aipdd_base_url
            if env_aipdd_base_url is not None
            else os.environ.get("AIPDD_CLOUD_BASE_URL", "")
        )
        .strip()
        .rstrip("/"),
        aipdd_prefix=str(
            env_aipdd_prefix
            if env_aipdd_prefix is not None
            else os.environ.get("AIPDD_CLOUD_PREFIX", "")
        )
        .strip()
        .strip("/"),
        aipdd_register_asset=_bool_setting(
            env_aipdd_register_asset
            if env_aipdd_register_asset is not None
            else os.environ.get("AIPDD_CLOUD_REGISTER_ASSET"),
            False,
        ),
        s3_region=str(
            env_s3_region if env_s3_region is not None
            else os.environ.get("AWS_S3_REGION", "")
        ).strip(),
        s3_bucket=str(
            env_s3_bucket if env_s3_bucket is not None
            else os.environ.get("AWS_S3_BUCKET", "")
        ).strip(),
        s3_access_key_id=str(
            env_s3_access_key_id if env_s3_access_key_id is not None
            else os.environ.get("AWS_S3_ACCESS_KEY_ID", "")
        ).strip(),
        s3_access_key_secret=str(
            env_s3_access_key_secret if env_s3_access_key_secret is not None
            else os.environ.get("AWS_S3_SECRET_ACCESS_KEY", "")
        ).strip(),
        s3_endpoint_url=str(
            env_s3_endpoint_url if env_s3_endpoint_url is not None
            else os.environ.get("AWS_S3_ENDPOINT_URL", "")
        ).strip(),
        s3_session_token=str(
            env_s3_session_token if env_s3_session_token is not None
            else os.environ.get("AWS_S3_SESSION_TOKEN", "")
        ).strip(),
    )


def get_effective_cognee_embedding_config(
    *,
    env_provider: str | None = None,
    env_model: str | None = None,
    env_dimensions: str | int | None = None,
    llm_provider: str | None = None,
) -> EffectiveCogneeEmbeddingConfig:
    saved: dict[str, Any] = {}
    if _uses_ce_gateway_settings():
        gateway = get_effective_newapi_config()
        if gateway.mode == MODE_CUSTOM:
            saved = get_newapi_embedding_model_config()
    if saved:
        saved_batch_size = str(
            saved.get("batchSize")
            or os.environ.get("EMBEDDING_BATCH_SIZE", DEFAULT_EMBEDDING_BATCH_SIZE)
        ).strip()
        if saved_batch_size:
            saved_batch_size = str(_int_setting(saved_batch_size, 0) or "")
        return EffectiveCogneeEmbeddingConfig(
            source="database",
            provider="newapi",
            model=str(saved["internalModel"]),
            dimensions=str(saved["dimension"]),
            upstream_provider=str(saved["provider"]),
            upstream_model=str(saved["upstreamModel"]),
            batch_size=saved_batch_size or DEFAULT_EMBEDDING_BATCH_SIZE,
        )

    # Product runtime always sends embeddings through newAPI. Keep the
    # arguments for API compatibility, but do not let legacy provider settings
    # bypass the gateway.
    del env_provider, llm_provider
    provider = DEFAULT_COGNEE_EMBEDDING_PROVIDER
    default_model = DEFAULT_COGNEE_EMBEDDING_MODEL
    model = str(
        env_model or os.environ.get("COGNEE_EMBEDDING_MODEL", default_model)
    ).strip()
    dimensions = (
        str(
            env_dimensions
            if env_dimensions is not None
            else os.environ.get("COGNEE_EMBEDDING_DIM", DEFAULT_COGNEE_EMBEDDING_DIM)
        ).strip()
        or DEFAULT_COGNEE_EMBEDDING_DIM
    )
    batch_size = str(
        os.environ.get("EMBEDDING_BATCH_SIZE", DEFAULT_EMBEDDING_BATCH_SIZE)
    ).strip()
    if batch_size:
        batch_size = str(_int_setting(batch_size, 0) or "")
    if not batch_size:
        batch_size = DEFAULT_EMBEDDING_BATCH_SIZE
    return EffectiveCogneeEmbeddingConfig(
        source="environment",
        provider=provider,
        model=model,
        dimensions=dimensions,
        upstream_provider="",
        upstream_model="",
        batch_size=batch_size,
    )


def build_model_gateway_status(
    *,
    official_base_url: str | None = None,
    official_api_key: str | None = None,
) -> dict[str, Any]:
    uses_ce_settings = _uses_ce_gateway_settings()
    settings = get_model_gateway_settings() if uses_ce_settings else {}
    db_official_base_url = _official_base_url(settings)
    # [aipdd fork delta req41] official.source 以前只看 ``db_official_base_url``，而这个
    # 值在旧实现里总被源码常量垫底，于是地址明明来自代码、状态页却报 "database"。现在
    # 按地址真正的来源报三态：数据库 / 环境 / 未配置。
    db_official_url_raw = str(settings.get("official_newapi_base_url", "") or "").strip()
    official_base_url_value = normalize_relay_base_url(
        db_official_base_url
        if uses_ce_settings
        else (
            os.environ.get("NEWAPI_BASE_URL", "")
            or official_base_url
            or official_newapi_base_url()
        )
    )
    if db_official_url_raw:
        official_url_source = "database"
    elif official_base_url_value:
        official_url_source = "environment"
    else:
        official_url_source = "unconfigured"
    env_official_api_key = (
        ""
        if uses_ce_settings
        else normalize_api_key(
            official_api_key
            if official_api_key is not None
            else os.environ.get("NEWAPI_API_KEY", "")
        )
    )
    db_official_api_key = (
        normalize_api_key(settings.get("official_newapi_api_key", ""))
        if uses_ce_settings
        else ""
    )
    official_api_key_value = (
        db_official_api_key if uses_ce_settings else env_official_api_key
    )
    custom_base_url = (
        normalize_relay_base_url(settings.get("custom_newapi_base_url", ""))
        if uses_ce_settings
        else ""
    )
    custom_api_key = (
        normalize_api_key(settings.get("custom_newapi_api_key", ""))
        if uses_ce_settings
        else ""
    )
    effective = get_effective_newapi_config(
        official_base_url=official_base_url,
        official_api_key=official_api_key,
    )
    return {
        "mode": effective.mode,
        "effective": {
            "source": effective.source,
            "baseUrl": effective.base_url,
            "apiKeyPreview": mask_secret(effective.api_key),
            "configured": bool(effective.base_url and effective.api_key),
        },
        "official": {
            "baseUrl": official_base_url_value,
            "apiKeyPreview": mask_secret(official_api_key_value),
            "configured": bool(official_base_url_value and official_api_key_value),
            "source": official_url_source,
            "environment": {
                "baseUrl": official_base_url_value,
                "apiKeyPreview": mask_secret(env_official_api_key),
                "configured": bool(official_base_url_value and env_official_api_key),
            },
        },
        "custom": {
            "baseUrl": custom_base_url,
            "apiKeyPreview": mask_secret(custom_api_key),
            "configured": bool(custom_base_url and custom_api_key),
            "adminBaseUrl": settings.get("custom_newapi_admin_base_url", ""),
            "tokenName": settings.get("custom_newapi_token_name", ""),
            "tokenId": settings.get("custom_newapi_token_id", ""),
        },
        # [aipdd fork delta] 拆解一体化模型：回显当前默认模型组合（LLM/Embedding/图像）。
        "defaultCombination": build_default_model_combination_status(),
    }


def build_newapi_database_status(
    *,
    sql_dsn: str | None = None,
    sqlite_path: str | None = None,
    admin_username: str | None = None,
) -> dict[str, Any]:
    settings = get_model_gateway_settings()
    db_sql_dsn = str(settings.get("custom_newapi_db_sql_dsn", "")).strip()
    db_sqlite_path = str(settings.get("custom_newapi_db_sqlite_path", "")).strip()
    db_admin_username = str(settings.get("custom_newapi_admin_username", "")).strip()
    env_sql_dsn = str(
        sql_dsn if sql_dsn is not None else os.environ.get("NEWAPI_SQL_DSN", "")
    )
    env_sql_dsn = env_sql_dsn.strip()
    env_sqlite_path = str(
        sqlite_path
        if sqlite_path is not None
        else os.environ.get("NEWAPI_SQLITE_PATH", "")
    ).strip()
    if not db_sql_dsn and not env_sql_dsn:
        from novelvideo.config import STATE_DIR

        env_sql_dsn = "local"
        env_sqlite_path = env_sqlite_path or str(
            Path(STATE_DIR) / "newapi" / "one-api.db"
        )
    effective_sql_dsn = db_sql_dsn or env_sql_dsn
    effective_sqlite_path = db_sqlite_path or env_sqlite_path
    source = (
        "database"
        if any([db_sql_dsn, db_sqlite_path, db_admin_username])
        else "environment"
    )
    configured = bool(
        effective_sql_dsn and (effective_sql_dsn != "local" or effective_sqlite_path)
    )
    available = configured
    if effective_sql_dsn == "local":
        available = bool(
            effective_sqlite_path and Path(effective_sqlite_path).expanduser().is_file()
        )
    return {
        "configured": configured,
        "available": available,
        "source": source,
        "databaseType": "sqlite" if effective_sql_dsn == "local" else "external",
    }


def build_media_relay_status(
    *,
    env_provider: str | None = None,
    env_ttl_seconds: int | str | None = None,
    env_endpoint: str | None = None,
    env_bucket: str | None = None,
    env_access_key_id: str | None = None,
    env_access_key_secret: str | None = None,
    env_cloud_name: str | None = None,
    env_cloudinary_api_key: str | None = None,
    env_cloudinary_api_secret: str | None = None,
    env_cloudinary_folder: str | None = None,
    env_aipdd_api_key: str | None = None,
    env_aipdd_base_url: str | None = None,
    env_s3_region: str | None = None,
    env_s3_bucket: str | None = None,
    env_s3_access_key_id: str | None = None,
    env_s3_access_key_secret: str | None = None,
    env_s3_endpoint_url: str | None = None,
    env_s3_session_token: str | None = None,
) -> dict[str, Any]:
    effective = get_effective_media_relay_config(
        env_provider=env_provider,
        env_ttl_seconds=env_ttl_seconds,
        env_endpoint=env_endpoint,
        env_bucket=env_bucket,
        env_access_key_id=env_access_key_id,
        env_access_key_secret=env_access_key_secret,
        env_cloud_name=env_cloud_name,
        env_cloudinary_api_key=env_cloudinary_api_key,
        env_cloudinary_api_secret=env_cloudinary_api_secret,
        env_cloudinary_folder=env_cloudinary_folder,
        env_aipdd_api_key=env_aipdd_api_key,
        env_aipdd_base_url=env_aipdd_base_url,
        env_s3_region=env_s3_region,
        env_s3_bucket=env_s3_bucket,
        env_s3_access_key_id=env_s3_access_key_id,
        env_s3_access_key_secret=env_s3_access_key_secret,
        env_s3_endpoint_url=env_s3_endpoint_url,
        env_s3_session_token=env_s3_session_token,
    )
    aliyun_configured = bool(
        effective.endpoint
        and effective.bucket
        and effective.access_key_id
        and effective.access_key_secret
    )
    cloudinary_configured = bool(
        effective.cloud_name
        and effective.cloudinary_api_key
        and effective.cloudinary_api_secret
    )
    # [aipdd fork delta] AWS S3 就绪判定：region + bucket + AK + SK 齐备（与 OSS 同口径）。
    aws_configured = bool(
        effective.s3_region
        and effective.s3_bucket
        and effective.s3_access_key_id
        and effective.s3_access_key_secret
    )
    # [aipdd fork delta req44] 数字资产云中转的就绪判定与「是不是 aipdd 档」无关：
    # 本地存储档的参考视频/音频同样靠它转出公网直链，所以两个档位都必须能解析出
    # ``(站点根, Key)``（设置页填的 > env > 已连接的 AIPDD 渠道自身的 Key）。
    from novelvideo.storage.media_relay import resolve_aipdd_cloud_target

    aipdd_origin, _aipdd_key = resolve_aipdd_cloud_target(
        saved_base_url=effective.aipdd_base_url,
        saved_api_key=effective.aipdd_api_key,
    )
    aipdd_relay_ready = bool(aipdd_origin)
    # [aipdd fork delta] 本地存储档的图片仍走内联，但视频/音频必须有中转直链：
    # 按用户口径「选了本地存储就必须填 AIPDD API Key，否则很多地方跑不通」，
    # 未就绪时照旧弹黄条。
    if effective.provider == "local":
        configured = aipdd_relay_ready
    elif effective.provider == "aipdd_cloud":
        configured = aipdd_relay_ready
    elif effective.provider == "cloudinary":
        configured = cloudinary_configured
    elif effective.provider == "aws_s3":
        configured = aws_configured
    else:
        configured = aliyun_configured
    return {
        "source": effective.source,
        "provider": effective.provider,
        "ttlSeconds": effective.ttl_seconds,
        "endpoint": effective.endpoint,
        "bucket": effective.bucket,
        "accessKeyIdPreview": mask_secret(effective.access_key_id),
        "accessKeySecretPreview": mask_secret(effective.access_key_secret),
        "cloudName": effective.cloud_name,
        "cloudinaryApiKeyPreview": mask_secret(effective.cloudinary_api_key),
        "cloudinaryApiSecretPreview": mask_secret(effective.cloudinary_api_secret),
        "apiFolder": effective.cloudinary_folder,
        # [aipdd fork delta req44] 数字资产云中转状态（本地档也消费同一份凭据）。
        "aipddApiKeyPreview": mask_secret(effective.aipdd_api_key),
        "aipddBaseUrl": effective.aipdd_base_url,
        "aipddPrefix": effective.aipdd_prefix,
        "aipddRegisterAsset": bool(effective.aipdd_register_asset),
        "aipddChannelConfigured": aipdd_relay_ready,
        # [aipdd fork delta] AWS S3 档回显（密钥仅回脱敏 preview，完整值不下发前端）。
        "s3Region": effective.s3_region,
        "s3Bucket": effective.s3_bucket,
        "s3EndpointUrl": effective.s3_endpoint_url,
        "s3AccessKeyIdPreview": mask_secret(effective.s3_access_key_id),
        "s3AccessKeySecretPreview": mask_secret(effective.s3_access_key_secret),
        "s3SessionTokenPreview": mask_secret(effective.s3_session_token),
        "awsConfigured": aws_configured,
        "configured": configured,
    }
