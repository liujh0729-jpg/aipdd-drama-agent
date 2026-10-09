# SPDX-License-Identifier: Elastic-2.0
# Copyright (c) 2026 ClaymoreLab
"""[aipdd fork delta] 部署环境依赖探测：为「设置 → 环境安装」提供服务器侧
真实运行依赖的就绪清单（ffmpeg / node / npm / uv / 中文字体 / Chrome CDP / OpenCV）。

本 fork 走 Docker 源码部署，容器里常驻的运行时依赖就是产品能力的地基：
剪辑 / 混剪 / 配音要有 ffmpeg，本地 Agent CLI 与 SOG 转换要有 node/npm，
3D 世界按需安装要有 uv，中文字幕要有 fontconfig 能匹配到 CJK 字体，
浏览器自动化 / 抖音文案要有可连的 Chrome CDP。缺哪个、怎么补，都在这份
清单里如实回显——探测全程只读，绝不改动任何状态。

每个条目统一形状：``{id, installed, version, detail}``（version/detail 可空）。
前端按 id 取图标 / 名称 / 用途 / 修复指引，这里只回事实。
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import urllib.request
from typing import Any

from novelvideo.utils.chrome_cdp import browsable_cdp_url

DEFAULT_CHROME_CDP_URL = "http://host.docker.internal:9222"
_CHROME_PROBE_TIMEOUT = 2.0
_VERSION_RE = re.compile(r"(\d+(?:\.\d+){0,3})")

# 字幕可用字体候选（与 freezone.jobs._MIX_SUBTITLE_FONT_CANDIDATES 同口径）：
# 命中任一即视为中文字体就绪。
_FONT_CANDIDATES = (
    "Noto Sans CJK SC",
    "Noto Sans SC",
    "Source Han Sans SC",
    "Source Han Sans CN",
    "PingFang SC",
    "Hiragino Sans GB",
    "Microsoft YaHei",
    "WenQuanYi Zen Hei",
    "WenQuanYi Micro Hei",
    "Droid Sans Fallback",
    "SimHei",
)


def _first_version(text: str | None) -> str | None:
    if not text:
        return None
    match = _VERSION_RE.search(text)
    return match.group(1) if match else None


def _run(argv: list[str], timeout: float = 5.0) -> subprocess.CompletedProcess[str] | None:
    try:
        return subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError):
        return None


def _command_item(tool_id: str, binary: str, version_args: list[str]) -> dict[str, Any]:
    path = shutil.which(binary)
    version: str | None = None
    if path is not None:
        completed = _run([binary, *version_args])
        if completed is not None and completed.returncode == 0:
            raw = (completed.stdout or completed.stderr or "").strip()
            version = _first_version(raw.splitlines()[0] if raw else None)
    return {
        "id": tool_id,
        "installed": path is not None,
        "version": version,
        "detail": path,
    }


def _fonts_item() -> dict[str, Any]:
    completed = _run(["fc-list", "--format", "%{family}\n"], timeout=10.0)
    if completed is None or completed.returncode != 0:
        return {"id": "fonts", "installed": False, "version": None, "detail": None}
    families: set[str] = set()
    for line in completed.stdout.splitlines():
        for family in line.split(","):
            name = family.strip()
            if name:
                families.add(name)
    matched = next((c for c in _FONT_CANDIDATES if c in families), None)
    return {"id": "fonts", "installed": matched is not None, "version": None, "detail": matched}


def _chrome_item() -> dict[str, Any]:
    """宿主 Chrome 的 CDP 是否**真的可用**（不是「端口有没有人监听」）。

    [aipdd fork delta] 不做纯 TCP 探测：TCP 握手可能成功而 Chrome 因 Host 头校验
    回 500（主机名形式的 ``host.docker.internal`` 就是这样，实测 200 与 500 的差别
    只在 URL 里写 IP 还是写主机名）。设置页亮绿灯、用户一提取就失败，比少一条
    绿灯更坏。因此与抖音拦截那侧同口径：先把主机名解析成 IP，再实打实 GET 一次
    ``/json/version``，拿到 ``Browser`` 字段才算就绪，顺手把真实版本回显给设置页；
    失败成因（连接拒绝 / 超时 / HTTP 码 / 端口上不是 Chrome）写进 detail。
    """

    raw = (os.environ.get("ST_CHROME_CDP_URL", "").strip() or DEFAULT_CHROME_CDP_URL)
    resolved = browsable_cdp_url(raw if "//" in raw else f"//{raw}")
    base = resolved if "://" in resolved else f"http://{resolved.lstrip('/')}"
    probe_url = f"{base.rstrip('/')}/json/version"
    try:
        with urllib.request.urlopen(probe_url, timeout=_CHROME_PROBE_TIMEOUT) as resp:
            payload = json.loads(resp.read().decode("utf-8", "ignore"))
    except Exception as exc:  # noqa: BLE001  探测失败一律如实回未就绪 + 成因
        code = getattr(exc, "code", None)
        if code:
            reason = f"HTTP {code}"
        else:
            # urllib 会把真正的传输错误包进 URLError.reason：只报类名就分不出
            # 「宿主 Chrome 没起」与「主机名解析不到」，而这正是这条目要回答的事。
            underlying = getattr(exc, "reason", None) or exc
            reason = f"{type(exc).__name__}: {underlying}"
        return {
            "id": "chrome",
            "installed": False,
            "version": None,
            "detail": f"{probe_url}（{reason}）",
        }
    browser = str(payload.get("Browser") or "").strip()
    if not browser:
        return {
            "id": "chrome",
            "installed": False,
            "version": None,
            "detail": f"{probe_url}（有响应但没有 Browser 字段，该端口不是 Chrome 调试端口）",
        }
    matched = _VERSION_RE.search(browser)
    return {
        "id": "chrome",
        "installed": True,
        "version": matched.group(1) if matched else None,
        "detail": base,
    }


def _opencv_item() -> dict[str, Any]:
    """探测 OpenCV（python 包）：白板动画逐笔揭示渲染器需要 import cv2。

    headless 构建不带 GUI 依赖，适配无显示器的容器；导入失败（未装/缺系统库）
    一律视为未就绪，detail 回异常摘要供补齐指引。
    """
    try:
        import cv2  # noqa: PLC0415  仅探测可用性，延迟导入避免污染模块导入路径。
    except Exception as exc:  # pragma: no cover - 取决于运行环境是否装了 opencv
        return {"id": "opencv", "installed": False, "version": None, "detail": str(exc)}
    return {
        "id": "opencv",
        "installed": True,
        "version": getattr(cv2, "__version__", None),
        "detail": None,
    }


def probe() -> dict[str, Any]:
    """探测部署环境的运行依赖，返回 ``{items: [...]}``（只读、无副作用）。"""
    items = [
        _command_item("ffmpeg", "ffmpeg", ["-version"]),
        _command_item("node", "node", ["-v"]),
        _command_item("npm", "npm", ["-v"]),
        _command_item("uv", "uv", ["--version"]),
        _fonts_item(),
        _chrome_item(),
        _opencv_item(),
    ]
    return {"items": items}
