# SPDX-License-Identifier: Elastic-2.0
# Copyright (c) 2026 ClaymoreLab
"""[aipdd fork delta] Blender MCP 白模视频桥。

只负责与本机 Blender 插件（官方 mcp-for-blender 加载项）的 socket 通道：把一段白模
（clay）预览视频的渲染/导出指令下发给 Blender，并把导出文件取回后端可读路径。入库
成项目静态直链、写回画布节点由 freezone 路由层完成（复用既有上传/画布持久化机制），
本模块不碰项目/画布，保持 Blender 关注点内聚、可零网络单测。

Blender MCP 内置服务的地址解析（ST_BLENDER_HOST / ST_BLENDER_PORT）与
:mod:`novelvideo.agent.mcp` 共用，避免两处漂移；导出目录用 ST_BLENDER_EXPORT_DIR。

部署可达性：本地同机后端可直连 127.0.0.1:9876 并读导出文件；Docker 后端读宿主机导出
文件需把 ST_BLENDER_EXPORT_DIR 落在与容器共享的挂载目录，否则拉取失败由调用方回退手动入库。
"""
from __future__ import annotations

import json
import os
import re
import socket
from pathlib import Path
from typing import Any
from uuid import uuid4

# 导出目录与环境覆盖（与 mcp.py 的 host/port 约定同构，此处只管 Blender 侧文件产出）。
BLENDER_EXPORT_DIR_ENV = "ST_BLENDER_EXPORT_DIR"
BLENDER_TIMEOUT_ENV = "ST_BLENDER_TIMEOUT"
_DEFAULT_TIMEOUT_SECONDS = 180.0
# execute_code 跑完后，导出文件应已落盘；再给的短暂轮询余量兜住文件系统可见性。
_EXISTENCE_POLL_SECONDS = 5.0

_PATH_IN_CODE_RE = re.compile(r"^\s*out\s*=\s*[\"'](?P<path>.+?)[\"']\s*$", re.MULTILINE)


class BlenderUnreachable(RuntimeError):
    """连不上本机 Blender 插件 socket（未启动 / 地址错 / 宿主机不可达）。"""


def resolve_connection(host: str | None = None, port: str | int | None = None) -> tuple[str, int]:
    """解析 Blender 插件 socket 地址：显式参数 > ST_BLENDER_HOST/PORT > 内置默认。

    默认走 :func:`novelvideo.agent.mcp._blender_connection`，与内置 MCP 服务同源。
    """
    if host and port:
        return str(host).strip(), int(port)
    from novelvideo.agent.mcp import _blender_connection

    default_host, default_port = _blender_connection()
    resolved_host = str(host or default_host).strip() or default_host
    resolved_port = port if port not in (None, "") else default_port
    return resolved_host, int(resolved_port)


def _export_command(export_path: str) -> dict[str, Any]:
    """构造发往 Blender 插件 socket 的 ``execute_code`` 指令（纯函数、零 IO，便于单测）。

    Blender 加载项协议为 ``{"type": "execute_code", "params": {"code": "<python>"}}``；
    这里让它以 Workbench 平涂（白模）把当前场景逐帧渲染成 H.264 MP4 到 ``export_path``。
    """
    code = (
        "import bpy, os\n"
        f"out = '{export_path}'\n"
        "os.makedirs(os.path.dirname(out) or '.', exist_ok=True)\n"
        "scene = bpy.context.scene\n"
        "scene.render.engine = 'BLENDER_WORKBENCH'\n"
        "scene.display.shading.light = 'FLAT'\n"
        "scene.display.shading.color_type = 'SINGLE'\n"
        "scene.display.shading.single_color = (0.72, 0.72, 0.74)\n"
        "scene.render.image_settings.file_format = 'FFMPEG'\n"
        "scene.render.ffmpeg.format = 'MPEG4'\n"
        "scene.render.ffmpeg.codec = 'H264'\n"
        "scene.render.filepath = out\n"
        "bpy.ops.render.render(animation=True)\n"
        "print(os.path.abspath(out))\n"
    )
    return {"type": "execute_code", "params": {"code": code}}


def _export_path_from_command(command: dict[str, Any]) -> str | None:
    """从 execute_code 指令里解析出目标导出路径（供假 socket / 调试用）。"""
    code = str((command.get("params") or {}).get("code") or "")
    match = _PATH_IN_CODE_RE.search(code)
    return match.group("path") if match else None


def _send_and_wait(host: str, port: int, command: dict[str, Any], timeout: float) -> str:
    """下发一条命令并等其执行完成（读到 EOF），返回插件回传的原始文本（尽力解析）。"""
    payload = json.dumps(command).encode("utf-8") + b"\n"
    try:
        with socket.create_connection((host, port), timeout=timeout) as sock:
            sock.settimeout(timeout)
            sock.sendall(payload)
            try:
                # 半关闭写端：加载项按 EOF 判定命令结束并开始执行、再回结果。
                sock.shutdown(socket.SHUT_WR)
            except OSError:
                pass
            chunks: list[bytes] = []
            while True:
                try:
                    chunk = sock.recv(65536)
                except socket.timeout as exc:  # 渲染超时：视为不可达，交由上层回退
                    raise BlenderUnreachable(
                        f"等待 Blender 执行超时（{timeout:.0f}s）：{host}:{port}"
                    ) from exc
                if not chunk:
                    break
                chunks.append(chunk)
    except BlenderUnreachable:
        raise
    except OSError as exc:
        raise BlenderUnreachable(f"无法连接 Blender 插件 socket {host}:{port}：{exc}") from exc
    return b"".join(chunks).decode("utf-8", errors="replace")


def _wait_for_file(path: Path, poll_seconds: float = _EXISTENCE_POLL_SECONDS) -> bool:
    if path.exists() and path.is_file():
        return True
    import time

    deadline = time.monotonic() + max(0.0, poll_seconds)
    while time.monotonic() < deadline:
        time.sleep(0.2)
        if path.exists() and path.is_file():
            return True
    return False


def pull_white_model_video(
    export_dir: Path,
    *,
    host: str | None = None,
    port: str | int | None = None,
    timeout: float | None = None,
) -> Path:
    """驱动本机 Blender 渲染并导出白模视频，返回后端可读的导出文件路径。

    导出文件路径由后端生成（``export_dir/white_model_<token>.mp4``）并告知 Blender，
    执行完成后直接校验该路径存在——不依赖插件回传 stdout 里的路径。连不上 / 未产出文件
    抛 :class:`BlenderUnreachable`，调用方据此回退手动入库。
    """
    resolved_host, resolved_port = resolve_connection(host, port)
    if timeout is None:
        raw_timeout = os.environ.get(BLENDER_TIMEOUT_ENV, "").strip()
        try:
            timeout = float(raw_timeout) if raw_timeout else _DEFAULT_TIMEOUT_SECONDS
        except ValueError:
            timeout = _DEFAULT_TIMEOUT_SECONDS
    export_dir = Path(export_dir)
    try:
        export_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise BlenderUnreachable(f"导出目录不可写：{export_dir}（{exc}）") from exc
    export_path = export_dir / f"white_model_{uuid4().hex[:12]}.mp4"
    command = _export_command(str(export_path))
    _send_and_wait(resolved_host, resolved_port, command, timeout)
    if not _wait_for_file(export_path):
        raise BlenderUnreachable(
            f"Blender 已响应但未产出导出文件：{export_path}"
            "（检查场景是否可渲染、插件对导出目录是否有写权限）"
        )
    return export_path


__all__ = [
    "BLENDER_EXPORT_DIR_ENV",
    "BlenderUnreachable",
    "_export_command",
    "_export_path_from_command",
    "pull_white_model_video",
    "resolve_connection",
]
