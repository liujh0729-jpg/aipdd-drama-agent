"""Copy a gateway-archived result when the EE delivery port is enabled."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Callable

from novelvideo import ports
from novelvideo.ports.video_delivery import ArchivedVideoSource, VideoDeliveryError


async def copy_archived_result(
    archive: object,
    output_path: str | Path,
    *,
    before_copy: Callable[[], None] | None = None,
) -> bool:
    try:
        delivery = ports.get_video_result_delivery()
    except Exception as exc:
        # [aipdd fork delta] 按类名而非类身份判定：契约测试会
        # importlib.reload(novelvideo.ports.registry) 重建 PortNotRegistered 类，
        # 模块级 `from ... import PortNotRegistered` 绑定的是旧类，类身份比较
        # 会让 except 失配、异常穿透到调用方（并行全量下 grid 图片流程曾因此
        # 偶发「请求异常: PortNotRegistered」）。与 ports/__init__.py 既有惯例一致。
        if exc.__class__.__name__ != "PortNotRegistered":
            raise
        if (
            os.environ.get("ST_MEDIA_ARCHIVE_COPY_ENABLED", "").strip().lower()
            == "true"
        ):
            raise VideoDeliveryError(
                "MEDIA_DELIVERY_PORT_UNAVAILABLE", retryable=False
            ) from exc
        delivery = None
    if delivery is None:
        return False
    if not isinstance(archive, dict) or archive.get("status") != "success":
        raise VideoDeliveryError("MEDIA_ARCHIVE_UNAVAILABLE", retryable=False)
    try:
        source = ArchivedVideoSource(
            asset_id=str(archive.get("asset_id") or ""),
            storage_provider=str(archive.get("storage_provider") or ""),
            bucket=str(archive.get("bucket") or ""),
            object_key=str(archive.get("object_key") or ""),
            url=str(archive.get("url") or ""),
            content_type=str(archive.get("content_type") or ""),
            size=int(archive.get("size") or 0),
            sha256=str(archive.get("sha256") or "").lower(),
        )
    except (TypeError, ValueError) as exc:
        raise VideoDeliveryError("MEDIA_ARCHIVE_INVALID", retryable=False) from exc
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    if before_copy is not None:
        before_copy()
    await delivery.deliver(source=source, output_path=str(output_path))
    return True
