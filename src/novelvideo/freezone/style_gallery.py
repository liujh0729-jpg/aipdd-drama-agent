# SPDX-License-Identifier: Elastic-2.0
# Copyright (c) 2026 ClaymoreLab
"""[aipdd fork delta] 风格图墙的本地封面存储。

背景：画布风格墙(StyleGalleryModal)的封面/示例图不随仓库发布,原本必须靠
`STYLE_GALLERY_ASSET_BASE` 指向 OSS/CDN。没配就是整墙占位块。这套 helper 提供
「本地托管」通路:超管在设置→风格管理里逐个风格上传封面,落到状态卷里的持久目录,
再由 `/api/v1/style-gallery/...` 同源提供 —— 传完即生效、跨部署留存、不依赖 OSS,
也不用每次改图重建前端镜像。

存储根落在 `STATE_DIR/_shared/style_gallery`(即容器里的 `/data/state/_shared/...`),
是挂载卷的一部分,重建镜像不会丢。逻辑路径严格对齐清单里那条 `cover`(形如
`<style_id>/cover.webp`):上传的字节按原始内容写到这个固定相对路径下,serve 时按
真实内容类型回 mime —— 文件名保留 `.webp` 只为跟清单/前端拼出的地址一致,浏览器
看的是响应头里的 content-type,所以传 png/jpg 也能正常显示。

一切写入都以清单里的 style_id 为准绳做 allowlist 校验,挡掉路径穿越与越权写盘。
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

from novelvideo.config import STATE_DIR
from novelvideo.freezone.style_templates import load_style_templates

# 上传落盘的逻辑文件名(与清单 cover 的 basename 对齐);清单统一用 cover.webp。
COVER_BASENAME = "cover.webp"


def gallery_root() -> Path:
    return Path(STATE_DIR) / "_shared" / "style_gallery"


def _covers_by_style() -> dict[str, str]:
    """style_id -> 清单里的 cover 相对路径(如 `period_idol/cover.webp`)。"""
    covers: dict[str, str] = {}
    for item in load_style_templates():
        style_id = str(item.get("id") or "").strip()
        cover = str(item.get("cover") or "").strip()
        if style_id and cover:
            covers[style_id] = cover
    return covers


def is_known_style(style_id: str) -> bool:
    return style_id in _covers_by_style()


def get_style_entry(style_id: str) -> Optional[dict]:
    """清单里这条风格的完整条目(含 label / style_prompt);未知 style_id 返回 None。"""
    for item in load_style_templates():
        if str(item.get("id") or "").strip() == style_id:
            return item
    return None


def cover_generation_prompt(style_id: str) -> str:
    """[aipdd fork delta] AI 生成封面用的提示词:按画布同款「风格模板块」拼装
    (风格名 + 清单里那段丰富的画风描述),与画布选这条风格出图时的口径一致。"""
    entry = get_style_entry(style_id)
    if not entry:
        return ""
    label = str(entry.get("label") or "").strip()
    style_prompt = str(entry.get("style_prompt") or "").strip()
    parts = [f"- {part}" for part in (label, style_prompt) if part]
    if not parts:
        return ""
    return "风格模板:\n" + "\n".join(parts)


def cover_rel(style_id: str) -> Optional[str]:
    """清单里这条风格的 cover 相对路径;未知 style_id 返回 None。"""
    return _covers_by_style().get(style_id)


def cover_path(style_id: str) -> Optional[Path]:
    """上传封面的落盘绝对路径(含越权防护);无对应风格或无 cover 时 None。"""
    rel = cover_rel(style_id)
    if not rel:
        return None
    root = gallery_root().resolve()
    target = (root / rel).resolve()
    # 兜底:清单是运维可换的外部文件,解析后必须仍在 gallery_root 内。
    if not target.is_relative_to(root):
        return None
    return target


def has_cover(style_id: str) -> bool:
    path = cover_path(style_id)
    return bool(path and path.is_file())


def cover_cache_token(style_id: str) -> str:
    """用 mtime_ns 做 cache-busting:换图后 URL 变,浏览器不会命中旧缓存。"""
    path = cover_path(style_id)
    if not path or not path.is_file():
        return ""
    try:
        return str(path.stat().st_mtime_ns)
    except OSError:
        return ""


def save_cover(style_id: str, data: bytes) -> Path:
    """写入封面字节。调用方已完成类型/大小校验,这里只负责安全落盘。"""
    path = cover_path(style_id)
    if path is None:
        raise ValueError(f"unknown style: {style_id}")
    if not data:
        raise ValueError("empty image data")
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.tmp.{os.getpid()}")
    tmp.write_bytes(data)
    os.replace(tmp, path)
    return path


def delete_cover(style_id: str) -> bool:
    path = cover_path(style_id)
    if path and path.is_file():
        try:
            path.unlink()
            return True
        except OSError:
            return False
    return False


def sniff_content_type(data: bytes) -> str:
    """按魔数判定图片 content-type,清单文件名是 .webp 但真实字节可能是 png/jpg。"""
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith(b"GIF87a") or data.startswith(b"GIF89a"):
        return "image/gif"
    if data[0:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return "application/octet-stream"


def list_gallery_styles() -> list[dict]:
    """给管理面板用:每条风格的元信息 + 是否已上传封面 + 可预览的 URL。"""
    rows: list[dict] = []
    for item in load_style_templates():
        style_id = str(item.get("id") or "").strip()
        if not style_id:
            continue
        uploaded = has_cover(style_id)
        url = (
            f"/api/v1/style-gallery/{style_id}/{COVER_BASENAME}?v={cover_cache_token(style_id)}"
            if uploaded
            else None
        )
        rows.append(
            {
                "id": style_id,
                "label": str(item.get("label") or ""),
                "category": str(item.get("category") or ""),
                "has_cover": uploaded,
                "cover_url": url,
            }
        )
    return rows
