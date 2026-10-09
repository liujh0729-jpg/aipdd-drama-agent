# SPDX-License-Identifier: Elastic-2.0
# Copyright (c) 2026 ClaymoreLab
"""[aipdd fork delta B] Blender 白模视频桥单测：零真实 Blender、零外网。

覆盖：
- ``_export_command`` / ``_export_path_from_command`` 纯函数（指令形状、导出路径可回解析）。
- ``resolve_connection``：显式 host+port 直通；缺省回落到内置 MCP 地址解析。
- ``pull_white_model_video`` happy path：内置 socketserver 假 Blender 读命令、按解析出的
  导出路径建文件、回结果；桥应返回该路径且文件存在。
- 负向：连接被拒 → ``BlenderUnreachable``；Blender 有响应但未产出文件 → ``BlenderUnreachable``。
"""

from __future__ import annotations

import json
import socket
import socketserver
import threading
from pathlib import Path

import pytest

from novelvideo import blender_bridge


def test_export_command_shape_and_marker() -> None:
    command = blender_bridge._export_command("/tmp/proj/blender/white_model_abc.mp4")
    assert command["type"] == "execute_code"
    code = command["params"]["code"]
    # 白模（clay）渲染关键设置都在指令里，缺一个都会导出成非白模/非视频。
    assert "BLENDER_WORKBENCH" in code
    assert "FLAT" in code
    assert "file_format = 'FFMPEG'" in code
    assert "codec = 'H264'" in code
    assert "render(animation=True)" in code
    assert "out = '/tmp/proj/blender/white_model_abc.mp4'" in code


def test_export_path_roundtrip() -> None:
    exported = "/var/tmp/a b/white_model_deadbeef.mp4"
    command = blender_bridge._export_command(exported)
    assert blender_bridge._export_path_from_command(command) == exported


def test_export_path_from_command_missing() -> None:
    assert blender_bridge._export_path_from_command({"type": "execute_code", "params": {"code": "print(1)"}}) is None
    assert blender_bridge._export_path_from_command({}) is None


def test_resolve_connection_explicit_passthrough() -> None:
    assert blender_bridge.resolve_connection("10.0.0.5", "9900") == ("10.0.0.5", 9900)
    assert blender_bridge.resolve_connection("10.0.0.5", 9900) == ("10.0.0.5", 9900)


def test_resolve_connection_falls_back_to_builtin(monkeypatch) -> None:
    import novelvideo.agent.mcp as mcp

    monkeypatch.setattr(mcp, "_blender_connection", lambda: ("host.docker.internal", 9876))
    host, port = blender_bridge.resolve_connection()
    assert (host, port) == ("host.docker.internal", 9876)


class _FakeBlenderHandler(socketserver.BaseRequestHandler):
    creates_file = True

    def handle(self) -> None:  # noqa: D401 - socketserver contract
        chunks: list[bytes] = []
        while True:
            chunk = self.request.recv(4096)
            if not chunk:
                break
            chunks.append(chunk)
        try:
            command = json.loads(b"".join(chunks).decode("utf-8").strip())
        except (ValueError, UnicodeDecodeError):
            command = {}
        exported = blender_bridge._export_path_from_command(command)
        if exported and _FakeBlenderHandler.creates_file:
            path = Path(exported)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"\x00\x00fake mp4")
        self.request.sendall(json.dumps({"result": "ok"}).encode("utf-8"))


class _FakeBlenderServer:
    def __enter__(self) -> tuple[str, int]:
        self.server = socketserver.ThreadingTCPServer(("127.0.0.1", 0), _FakeBlenderHandler)
        self.server.daemon_threads = True
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        host, port = self.server.server_address[:2]
        return host, port

    def __exit__(self, *_exc) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)


def test_pull_white_model_video_happy_path(tmp_path: Path) -> None:
    _FakeBlenderHandler.creates_file = True
    export_dir = tmp_path / "blender"
    with _FakeBlenderServer() as (host, port):
        result = blender_bridge.pull_white_model_video(export_dir, host=host, port=port, timeout=5)
    assert result.parent == export_dir
    assert result.suffix == ".mp4"
    assert result.exists()
    assert result.read_bytes().startswith(b"\x00\x00fake mp4")


def test_pull_white_model_video_connection_refused(tmp_path: Path) -> None:
    # 绑定再立即关闭，拿到一个几乎必然空闲的端口 → 连接被拒。
    probe = socket.socket()
    probe.bind(("127.0.0.1", 0))
    dead_port = probe.getsockname()[1]
    probe.close()
    with pytest.raises(blender_bridge.BlenderUnreachable):
        blender_bridge.pull_white_model_video(
            tmp_path / "blender", host="127.0.0.1", port=dead_port, timeout=2
        )


def test_pull_white_model_video_responds_but_no_file(
    tmp_path: Path, monkeypatch
) -> None:
    _FakeBlenderHandler.creates_file = False
    # 假 Blender 有响应但未落文件；把存在性轮询压成即刻失败，避免真实 5s 等待。
    monkeypatch.setattr(blender_bridge, "_wait_for_file", lambda *_a, **_k: False)
    export_dir = tmp_path / "blender"
    with _FakeBlenderServer() as (host, port):
        with pytest.raises(blender_bridge.BlenderUnreachable):
            blender_bridge.pull_white_model_video(export_dir, host=host, port=port, timeout=5)
    _FakeBlenderHandler.creates_file = True
