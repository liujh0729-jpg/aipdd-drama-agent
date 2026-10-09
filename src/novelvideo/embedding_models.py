"""Project-bound Cognee embedding model selection and request scoping."""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Iterator

from novelvideo.model_gateway_settings import (
    MODE_CUSTOM,
    MODE_OFFICIAL,
    get_ce_newapi_config_for_mode,
    get_effective_newapi_config,
    get_newapi_embedding_model_config,
)
from novelvideo.shared.runtime_env import is_ce_effective

PROJECT_EMBEDDING_MODEL_KEY = "cognee_embedding_model"
PROJECT_EMBEDDING_DIMENSION_KEY = "cognee_embedding_dimension"
COGNEE_EMBEDDING_MODEL_LEGACY = "DC-cognee-embedding"
COGNEE_EMBEDDING_MODEL_V1 = "DC-cognee-embedding-v1"
COGNEE_EMBEDDING_MODEL_V2 = "DC-cognee-embedding-v2"
# [aipdd fork delta req40] 官方网关两个历史别名（v1 / v2）的**存储契约**：库里已有的向量就是
# 这个尺寸，改它等于让既有检索失效，所以只作历史绑定的校验值，**不再**当作自定义渠道的
# 默认维度 —— 那边的维度以渠道目录声明为准（见 :func:`catalog_embedding_dimensions`）。
COGNEE_EMBEDDING_DIMENSIONS = 1024



def catalog_embedding_dimensions(model: str = "") -> int | None:
    """渠道目录里该向量模型**声明**的输出维度；渠道没写就返回 None。

    [aipdd fork delta req40] 以前代码把 1024 当成「上游向量模型都是这个维度」：换一个
    1536 / 2048 的模型就会把向量存成对不上号的尺寸，检索静默失效。渠道在目录里写了
    ``embedding_dimensions`` 就按声明走，没写则维持「用户显式配置优先、否则不给维度」。
    """

    target = str(model or "").strip()
    if not target:
        saved = get_newapi_embedding_model_config()
        target = str(saved.get("upstreamModel") or "").strip()
    if not target:
        return None
    try:
        from novelvideo.model_gateway_settings import upstream_catalog_entry
        from novelvideo.upstream_catalog import dimensions_for

        entry = upstream_catalog_entry(target)
    except Exception:  # noqa: BLE001 - 目录不可用时不宣称知道维度
        return None
    return dimensions_for(entry) if entry else None

@dataclass(frozen=True)
class EmbeddingModelSpec:
    internal_model: str
    dimensions: int
    send_dimensions: bool
    gateway: str


_CURRENT_COGNEE_EMBEDDING_SPEC: ContextVar[EmbeddingModelSpec | None] = ContextVar(
    "current_cognee_embedding_spec",
    default=None,
)


def active_gateway_uses_custom_embedding() -> bool:
    """Return whether new projects should bind to the CE custom model alias."""

    if not is_ce_effective():
        return False
    return get_effective_newapi_config().mode == MODE_CUSTOM


def embedding_model_for_new_project() -> str:
    """Choose the permanent model binding for a project created now."""

    return embedding_model_binding_for_new_project().internal_model


def embedding_model_binding_for_new_project() -> EmbeddingModelSpec:
    """Snapshot the embedding model contract for a project created now."""

    if active_gateway_uses_custom_embedding():
        saved = get_newapi_embedding_model_config()
        # 用户在设置页填的维度优先（它同时是既有向量的尺寸），其次问渠道目录。
        dimensions = int(saved.get("dimension") or 0) or catalog_embedding_dimensions()
        return embedding_model_spec(
            COGNEE_EMBEDDING_MODEL_LEGACY,
            dimensions=dimensions,
        )
    return embedding_model_spec(COGNEE_EMBEDDING_MODEL_V2)


def embedding_model_for_legacy_project() -> str:
    """Choose the compatibility binding for a pre-versioning project."""

    if active_gateway_uses_custom_embedding():
        return COGNEE_EMBEDDING_MODEL_LEGACY
    return COGNEE_EMBEDDING_MODEL_V1


def embedding_model_spec(
    model: str,
    *,
    dimensions: int | None = None,
) -> EmbeddingModelSpec:
    """Resolve and strictly validate one persisted internal model name."""

    clean_model = str(model or "").strip()
    project_dimensions = int(dimensions) if dimensions is not None else None
    if project_dimensions is not None and project_dimensions <= 0:
        raise RuntimeError(
            f"Unsupported embedding dimensions: {project_dimensions}"
        )
    if clean_model == COGNEE_EMBEDDING_MODEL_V1:
        if project_dimensions not in {None, COGNEE_EMBEDDING_DIMENSIONS}:
            raise RuntimeError(
                "Embedding dimension does not match DC-cognee-embedding-v1: "
                f"expected {COGNEE_EMBEDDING_DIMENSIONS}, "
                f"configured {project_dimensions}"
            )
        return EmbeddingModelSpec(
            internal_model=clean_model,
            dimensions=COGNEE_EMBEDDING_DIMENSIONS,
            send_dimensions=True,
            gateway=MODE_OFFICIAL,
        )
    if clean_model == COGNEE_EMBEDDING_MODEL_V2:
        if project_dimensions not in {None, COGNEE_EMBEDDING_DIMENSIONS}:
            raise RuntimeError(
                "Embedding dimension does not match DC-cognee-embedding-v2: "
                f"expected {COGNEE_EMBEDDING_DIMENSIONS}, "
                f"configured {project_dimensions}"
            )
        return EmbeddingModelSpec(
            internal_model=clean_model,
            dimensions=COGNEE_EMBEDDING_DIMENSIONS,
            send_dimensions=True,
            gateway=MODE_OFFICIAL,
        )
    if clean_model == COGNEE_EMBEDDING_MODEL_LEGACY:
        if not is_ce_effective():
            raise RuntimeError(
                "DC-cognee-embedding is reserved for CE custom NewAPI projects"
            )
        if project_dimensions is None:
            saved = get_newapi_embedding_model_config()
            project_dimensions = int(saved.get("dimension") or 0)
            if project_dimensions <= 0:
                # [aipdd fork delta req40] 不再凭空按 1024 建库：维度只能来自用户设置或渠道
                # 目录声明，两者都没有就拒绝，避免把向量存成一个上游并不产出的尺寸。
                declared = catalog_embedding_dimensions()
                if not declared:
                    # 维度没来头 = 向量模型根本没绑定（或渠道没声明）：按 req40 口径提交前拒绝，
                    # 不凭空建库（API 层统一转 HTTP 400 中文）。
                    from novelvideo.model_slots import REASON_UNBOUND, SLOT_COGNEE_EMBEDDING
                    from novelvideo.model_slots import ModelSlotNotConfigured

                    raise ModelSlotNotConfigured(
                        SLOT_COGNEE_EMBEDDING,
                        REASON_UNBOUND,
                        detail="向量维度未知：请填维度或重新同步会声明 embedding_dimensions 的渠道目录",
                    )
                project_dimensions = declared
        return EmbeddingModelSpec(
            internal_model=clean_model,
            dimensions=project_dimensions,
            send_dimensions=True,
            gateway=MODE_CUSTOM,
        )
    raise RuntimeError(f"Unsupported embedding model: {clean_model or '<empty>'}")


def embedding_gateway_credentials(spec: EmbeddingModelSpec) -> tuple[str, str]:
    """Resolve credentials for the gateway permanently implied by a model spec."""

    if is_ce_effective():
        gateway = get_ce_newapi_config_for_mode(spec.gateway)
        return gateway.api_key, gateway.base_url
    if spec.gateway != MODE_OFFICIAL:
        raise RuntimeError("Custom embedding gateway is only available in CE")
    # [aipdd fork delta req41] 不再把某个站点地址当参数递进去：地址只由 env / 数据库
    # 决定，被调用方自己读。
    gateway = get_effective_newapi_config()
    return gateway.api_key, gateway.base_url


def current_embedding_model_spec() -> EmbeddingModelSpec | None:
    return _CURRENT_COGNEE_EMBEDDING_SPEC.get()


def require_current_embedding_model_spec() -> EmbeddingModelSpec:
    spec = current_embedding_model_spec()
    if spec is None:
        raise RuntimeError("Cognee embedding request has no project model context")
    return spec


@contextmanager
def embedding_model_scope(
    model: str,
    *,
    dimensions: int | None = None,
) -> Iterator[EmbeddingModelSpec]:
    """Bind one project's embedding model to all calls in the current async context."""

    spec = embedding_model_spec(model, dimensions=dimensions)
    token = _CURRENT_COGNEE_EMBEDDING_SPEC.set(spec)
    try:
        yield spec
    finally:
        _CURRENT_COGNEE_EMBEDDING_SPEC.reset(token)
