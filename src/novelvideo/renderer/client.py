# SPDX-License-Identifier: Elastic-2.0
# Copyright (c) 2026 ClaymoreLab
"""[aipdd fork delta] HyperFrames 渲染服务客户端（引擎即服务）。

口径：
- 项目目录走**共享卷上的 ``projectDir`` 路径**——api 与 renderer 容器挂同一
  ``/data``（见 docker-compose 的 ``ce-data``），producer 用 Puppeteer(Chromium)
  + FFmpeg 无头加载项目 ``index.html`` 渲染成 MP4。桌面端 api 与本地 producer
  子进程共享宿主 FS，同一套路径语义。
- ``@hyperframes/producer`` 的 ``POST /render`` 是**阻塞式**（服务端
  ``server.setTimeout(0)``，长片不限时），成功后返回 ``{outputPath, outputToken,
  outputUrl, fileSize, videoDurationSeconds, outcome, warnings}``。产物优先读
  共享路径上的 ``outputPath``；跨机部署（无共享卷）时用 ``outputToken`` 经
  ``GET /outputs/:token`` 下载兜底。
- 服务地址只认配置 ``DRAMACLAW_RENDERER_URL``，绝不猜本机（对齐 VectCut 口径）。
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

# 画幅预设 → producer 的 ``outputResolution``（合法枚举来自 server.ts
# validateOutputResolutionOverride：landscape/portrait/square 及其 -4k 变体）。
# 只传 tier 名，让编译阶段按 composition 自身朝向适配。
OUTPUT_RESOLUTION_PRESETS: dict[str, str] = {
    "landscape": "landscape",
    "portrait": "portrait",
    "square": "square",
}

RENDERER_NOT_CONFIGURED_HINT = (
    "未配置 HyperFrames 渲染服务地址：请设 DRAMACLAW_RENDERER_URL"
    "（compose 部署已注入 http://renderer:9847，桌面端为本地 producer 子进程地址）。"
)

# 渲染是重活（逐帧无头截图 + 编码），默认给足超时；调用方按片长可覆盖。
DEFAULT_RENDER_TIMEOUT_S = 1800


class RendererNotConfigured(RuntimeError):
    """渲染服务地址未配置。"""


class RenderFailed(RuntimeError):
    """渲染服务返回失败或产物不可用。"""

    def __init__(
        self,
        message: str,
        *,
        error_code: str | None = None,
        stage: str | None = None,
        retryable: bool | None = None,
    ) -> None:
        super().__init__(message)
        self.error_code = error_code
        self.stage = stage
        self.retryable = retryable


@dataclass
class RenderResult:
    """一次渲染的成功结果。"""

    output_path: str
    file_size: int
    video_duration_seconds: float | None
    outcome: str
    request_id: str = ""
    warnings: list[Any] = field(default_factory=list)


def renderer_base_url() -> str:
    """渲染服务基址（只认配置，未配置报可执行错误，不猜本机）。"""

    value = (os.environ.get("DRAMACLAW_RENDERER_URL") or "").strip().rstrip("/")
    if not value:
        raise RendererNotConfigured(RENDERER_NOT_CONFIGURED_HINT)
    return value


def is_configured() -> bool:
    """渲染服务是否已配置（供能力位探测与降级提示，不抛异常）。"""

    return bool((os.environ.get("DRAMACLAW_RENDERER_URL") or "").strip())


def _resolve_output_resolution(fmt: str | None) -> str | None:
    if not fmt:
        return None
    return OUTPUT_RESOLUTION_PRESETS.get(fmt, fmt)


async def render_project(
    project_dir: Path | str,
    *,
    output_path: Path | str | None = None,
    fps: int | str = 30,
    quality: str = "high",
    fmt: str = "mp4",
    canvas_format: str | None = None,
    entry_file: str = "index.html",
    variables: dict[str, Any] | None = None,
    timeout_s: int = DEFAULT_RENDER_TIMEOUT_S,
) -> RenderResult:
    """把一个 HyperFrames 项目目录渲染成视频，返回产物信息。

    ``project_dir`` 必须是渲染服务可访问的绝对路径（共享卷 / 宿主 FS）；
    ``output_path`` 建议落在同一共享目录，便于 api 直接读取产物。
    ``canvas_format``（landscape/portrait/square）映射到 ``outputResolution``，
    留空则按 composition 自身尺寸渲染。
    """

    base = renderer_base_url()
    project_abs = str(Path(project_dir).resolve())

    body: dict[str, Any] = {
        "projectDir": project_abs,
        "fps": fps,
        "quality": quality,
        "format": fmt,
        "entryFile": entry_file,
    }
    if output_path is not None:
        body["outputPath"] = str(Path(output_path).resolve())
    resolution = _resolve_output_resolution(canvas_format)
    if resolution:
        body["outputResolution"] = resolution
    if variables:
        body["variables"] = variables

    timeout = httpx.Timeout(timeout_s, connect=15.0)
    async with httpx.AsyncClient(timeout=timeout) as client:
        try:
            resp = await client.post(f"{base}/render", json=body)
        except httpx.HTTPError as exc:  # 连接/超时等传输层失败
            raise RenderFailed(f"无法连接 HyperFrames 渲染服务 {base}：{exc}") from exc

        try:
            data = resp.json()
        except ValueError as exc:
            raise RenderFailed(
                f"渲染服务返回非 JSON（HTTP {resp.status_code}）：{resp.text[:200]}"
            ) from exc

        if not data.get("success"):
            raise RenderFailed(
                str(data.get("error") or f"渲染失败（HTTP {resp.status_code}）"),
                error_code=data.get("errorCode"),
                stage=data.get("stage"),
                retryable=data.get("retryable"),
            )

        result = _materialize_result(data, base, client)
        if result.file_size <= 0:
            raise RenderFailed(
                f"渲染产物为空：{result.output_path}",
                error_code=data.get("errorCode"),
            )
        return result


def _materialize_result(data: dict[str, Any], base: str, client: httpx.AsyncClient) -> RenderResult:
    """优先取共享路径上的产物；缺失时经 /outputs/:token 下载兜底。"""

    output_path = str(data.get("outputPath") or "")
    token = str(data.get("outputToken") or "")
    file_size = int(data.get("fileSize") or 0)

    # 共享卷：产物已落在 api 可见路径，直接返回。
    if output_path and Path(output_path).exists():
        size = file_size or Path(output_path).stat().st_size
        return RenderResult(
            output_path=output_path,
            file_size=size,
            video_duration_seconds=_as_float(data.get("videoDurationSeconds")),
            outcome=str(data.get("outcome") or "completed"),
            request_id=str(data.get("requestId") or ""),
            warnings=list(data.get("warnings") or []),
        )

    # 跨机部署兜底：下载 outputUrl 到 outputPath 指定的目标（调用方须传 output_path）。
    if token and output_path:
        dest = Path(output_path)
        dest.parent.mkdir(parents=True, exist_ok=True)
        url = f"{base}/outputs/{token}"
        with client.stream("GET", url) as stream:
            stream.raise_for_status()
            with dest.open("wb") as fh:
                for chunk in stream.iter_bytes():
                    fh.write(chunk)
        return RenderResult(
            output_path=str(dest),
            file_size=dest.stat().st_size,
            video_duration_seconds=_as_float(data.get("videoDurationSeconds")),
            outcome=str(data.get("outcome") or "completed"),
            request_id=str(data.get("requestId") or ""),
            warnings=list(data.get("warnings") or []),
        )

    raise RenderFailed(
        "渲染服务未返回可读产物（outputPath 不存在且无 token 兜底）",
        error_code=data.get("errorCode"),
    )


def _as_float(value: Any) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


async def health(*, timeout_s: float = 5.0) -> dict[str, Any]:
    """探测渲染服务健康（供自检 / 能力位；未配置返回 configured=False）。"""

    if not is_configured():
        return {"configured": False, "ok": False}
    base = renderer_base_url()
    try:
        async with httpx.AsyncClient(timeout=timeout_s) as client:
            resp = await client.get(f"{base}/health")
            return {"configured": True, "ok": resp.status_code == 200, "data": resp.json()}
    except httpx.HTTPError as exc:
        return {"configured": True, "ok": False, "error": str(exc)}
