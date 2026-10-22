# SPDX-License-Identifier: Elastic-2.0
# Copyright (c) 2026 ClaymoreLab
"""[aipdd fork delta] 设置→换脸直播：本机边车引擎的按需部署/开关/下载源/控制转发回归测试。

对齐 tests/test_world_install_admin.py 的分层覆盖：
1. 默认状态（未装、未启用）与下载源深合并落盘、坏配置回落默认；
2. 独立 venv 命令拼装（uv venv + uv pip install，绝不 --frozen，不含 PySide6/torch，
   含 insightface/onnxruntime/pyvirtualcam）与子进程 env（HF/缓存/代理）；
3. 安装流程（mock Popen + mock 模型下载，绝不起真实 uv/联网）跑通 INSTALLING → READY，
   失败标 FAILED；
4. 权限闸门（member / agent token 被拒）+ 路由契约（status/sources/enable/install/uninstall）；
5. 「Web 不推流」：引擎未起时控制转发端点返回 idle、绝不外连。

faceswap_dir 用 monkeypatch 固定到 tmp_path，测试不碰真实 STATE_DIR、不起真实边车进程。
"""

from __future__ import annotations

import sys
import threading
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from novelvideo.api.auth import get_api_user
from novelvideo.api.routes import faceswap as faceswap_routes
from novelvideo.faceswap import faceswap_install, model_download


def _patch_dir(monkeypatch, tmp_path):
    root = tmp_path / "faceswap"
    monkeypatch.setattr(faceswap_install, "faceswap_dir", lambda: root)
    # 边车探活/本机形态在单测里固定：不真连 127.0.0.1、恒为可装形态。
    monkeypatch.setattr(faceswap_install, "engine_running", lambda: False)
    monkeypatch.setattr(faceswap_install, "is_local_runtime", lambda: True)
    return root


def _build_client(monkeypatch, tmp_path, user):
    _patch_dir(monkeypatch, tmp_path)
    app = FastAPI()
    app.include_router(faceswap_routes.router, prefix="/api/v1")
    app.dependency_overrides[get_api_user] = lambda: dict(user)
    return TestClient(app)


@pytest.fixture()
def fdir(monkeypatch, tmp_path):
    return _patch_dir(monkeypatch, tmp_path)


@pytest.fixture()
def owner_client(monkeypatch, tmp_path):
    return _build_client(
        monkeypatch, tmp_path, {"id": "u-owner", "username": "admin", "role": "owner"}
    )


# --------------------------------------------------------------- 状态与配置


def test_default_status_not_installed(fdir):
    snap = faceswap_install.status_snapshot()
    assert snap["installed"] is False
    assert snap["enabled"] is False
    assert snap["install_state"] == faceswap_install.STATE_NOT_INSTALLED
    assert snap["installing"] is False
    assert snap["engine_running"] is False
    assert snap["local_runtime"] is True
    assert snap["sources"]["hf_endpoint"] == faceswap_install.DEFAULT_SOURCES["hf_endpoint"]


def test_save_load_sources_deep_merge(fdir):
    faceswap_install.save_settings({"sources": {"hf_endpoint": "https://my.mirror"}})
    s = faceswap_install.load_settings()
    assert s["sources"]["hf_endpoint"] == "https://my.mirror"
    # 未传的字段保留默认，不整表覆盖丢失。
    assert s["sources"]["pip_index_url"] == faceswap_install.DEFAULT_SOURCES["pip_index_url"]
    assert (fdir / "settings.json").exists()


def test_corrupt_settings_falls_back(fdir):
    fdir.mkdir(parents=True, exist_ok=True)
    (fdir / "settings.json").write_text("{not json", encoding="utf-8")
    s = faceswap_install.load_settings()
    assert s["enabled"] is False
    assert s["sources"] == faceswap_install.DEFAULT_SOURCES


# --------------------------------------------------------------- 命令 / env 拼装


def test_build_venv_command_uses_uv_and_real_interpreter(fdir):
    cmd = faceswap_install._build_venv_command()
    assert "venv" in cmd
    assert str(faceswap_install.venv_dir()) in cmd
    # 回归：--python 必须指向一个已存在解释器（sys.executable），不能指向尚未创建的 venv python。
    assert sys.executable in cmd
    assert str(faceswap_install.venv_python()) not in cmd


def test_build_pip_install_command_no_frozen_and_permissive_only(fdir):
    cmd = faceswap_install._build_pip_install_command({"pip_index_url": "https://p/simple"})
    joined = " ".join(cmd)
    # 遵循记忆：uv 装依赖绝不 --frozen（那会照锁文件装、架空镜像源）。
    assert "--frozen" not in cmd
    assert "--index-url https://p/simple" in joined
    # 镜像只做主索引，官方 PyPI 必须做兜底：清华源对 uv 整站 403，只认镜像时一颗都装不上。
    assert f"--extra-index-url {faceswap_install._FALLBACK_INDEX}" in joined
    # 目标 venv 的 python 作为 --python。
    assert str(faceswap_install.venv_python()) in cmd
    lower = joined.lower()
    # 装的是宽松许可换脸核，不含上游 AGPL/重依赖。
    assert "insightface" in lower
    assert "pyvirtualcam" in lower
    assert "onnxruntime" in lower
    assert "pyside6" not in lower
    assert "torch" not in lower


def test_engine_packages_pin_wheel_bearing_insightface(fdir):
    # 回归：0.7.3 在 PyPI 只有 sdist，装它要在用户机上现编 Cython 扩展（实测产出 .pyd），
    # 没装 MSVC 的普通 Windows 必然「依赖安装失败」。必须是发纯 Python 轮子的版本。
    joined = " ".join(faceswap_install._ENGINE_PACKAGES)
    pin = next(p for p in faceswap_install._ENGINE_PACKAGES if p.startswith("insightface"))
    assert pin != "insightface==0.7.3"
    assert "insightface==" in joined
    # 与边车随包清单保持一致（两份清单必须同步改）。
    req = Path(faceswap_install._project_root()) / "services" / "faceswap" / "requirements.txt"
    if req.is_file():
        assert pin in req.read_text(encoding="utf-8")


def test_build_pip_install_empty_index_uses_official_pypi(fdir):
    joined = " ".join(faceswap_install._build_pip_install_command({"pip_index_url": ""}))
    assert f"--index-url {faceswap_install._FALLBACK_INDEX}" in joined
    assert joined.count("--index-url") == 1


def test_build_child_env_sets_cache_proxy_and_index(fdir):
    sources = dict(faceswap_install.DEFAULT_SOURCES)
    sources["https_proxy"] = "http://127.0.0.1:7890"
    sources["pip_index_url"] = "https://p/simple"
    env = faceswap_install._build_child_env(sources)
    assert env["HTTPS_PROXY"] == "http://127.0.0.1:7890"
    assert env["HF_ENDPOINT"] == sources["hf_endpoint"]
    assert env["UV_INDEX_URL"] == "https://p/simple"
    assert env["HF_HOME"].endswith("hf_cache")


# --------------------------------------------------------------- 安装流程（mock）


class _FakePopen:
    last: dict = {}

    def __init__(self, *args, **kwargs):
        type(self).last = {
            "cmd": list(args[0]) if args else [],
            "env": dict(kwargs.get("env") or {}),
        }
        self.stdout = iter(
            ["Resolved 40 packages\n", "Downloading onnxruntime\n", "Installed 40 packages\n"]
        )

    def wait(self) -> int:
        return 0


def test_start_install_transitions_to_ready(fdir, monkeypatch):
    monkeypatch.setattr(faceswap_install.subprocess, "Popen", _FakePopen)
    monkeypatch.setattr(faceswap_install, "is_installed", lambda: True)
    monkeypatch.setattr(faceswap_install, "_INSTALL_THREAD", None)
    # 模型下载不外连，直接成功。
    monkeypatch.setattr(model_download, "download_required", lambda *a, **k: (True, None))

    faceswap_install.start_install()
    thread = faceswap_install._INSTALL_THREAD
    assert isinstance(thread, threading.Thread)
    thread.join(timeout=10)
    assert not thread.is_alive()

    final = faceswap_install.load_settings()
    assert final["install_state"] == faceswap_install.STATE_READY
    assert final["installed_at"]
    assert final["models_ready"] is True


def test_start_install_falls_back_to_official_pypi(fdir, monkeypatch):
    """镜像源装失败 → 必须自动直连官方 PyPI 再试一次，而不是把用户卡在「换镜像/填代理」。"""

    class _FlakyPopen:
        calls: list = []

        def __init__(self, *args, **kwargs):
            cmd = list(args[0]) if args else []
            type(self).calls.append((cmd, dict(kwargs.get("env") or {})))
            is_pip = len(cmd) > 1 and cmd[1] == "pip"
            pip_so_far = sum(1 for c, _ in type(self).calls if len(c) > 1 and c[1] == "pip")
            self._fail = is_pip and pip_so_far == 1
            self.stdout = iter(["Resolved 40 packages\n", "Installed 40 packages\n"])

        def wait(self) -> int:
            return 1 if self._fail else 0

    _FlakyPopen.calls = []
    monkeypatch.setattr(faceswap_install.subprocess, "Popen", _FlakyPopen)
    monkeypatch.setattr(faceswap_install, "is_installed", lambda: True)
    monkeypatch.setattr(faceswap_install, "_INSTALL_THREAD", None)
    monkeypatch.setattr(model_download, "download_required", lambda *a, **k: (True, None))

    faceswap_install.start_install()
    faceswap_install._INSTALL_THREAD.join(timeout=10)

    pip_calls = [(c, e) for c, e in _FlakyPopen.calls if len(c) > 1 and c[1] == "pip"]
    assert len(pip_calls) == 2
    second_cmd, second_env = pip_calls[1]
    joined = " ".join(second_cmd)
    assert faceswap_install._FALLBACK_INDEX in joined
    # 兜底这一趟必须彻底甩开镜像：既不再叠镜像，也不留镜像类环境变量。
    assert "--extra-index-url" not in joined
    assert "UV_INDEX_URL" not in second_env
    assert faceswap_install.load_settings()["install_state"] == faceswap_install.STATE_READY


def test_start_install_model_failure_marks_failed(fdir, monkeypatch):
    monkeypatch.setattr(faceswap_install.subprocess, "Popen", _FakePopen)
    monkeypatch.setattr(faceswap_install, "_INSTALL_THREAD", None)
    # 模型下载失败（网络/镜像问题）→ 整体标 FAILED，与 world 同源约束一致。
    monkeypatch.setattr(
        model_download, "download_required", lambda *a, **k: (False, "inswapper_128_fp16.onnx")
    )

    faceswap_install.start_install()
    faceswap_install._INSTALL_THREAD.join(timeout=10)
    s = faceswap_install.load_settings()
    assert s["install_state"] == faceswap_install.STATE_FAILED
    assert "inswapper" in s["install_message"]


# --------------------------------------------------------------- 权限门


def test_member_is_rejected(monkeypatch, tmp_path):
    client = _build_client(
        monkeypatch, tmp_path, {"id": "u-mem", "username": "alice", "role": "member"}
    )
    assert client.get("/api/v1/admin/faceswap/status").status_code == 403
    assert client.post("/api/v1/admin/faceswap/install").status_code == 403


def test_agent_token_is_rejected_even_with_owner_role(monkeypatch, tmp_path):
    client = _build_client(
        monkeypatch,
        tmp_path,
        {"id": "u-agent", "username": "bot", "role": "owner", "credential_kind": "agent_session"},
    )
    assert client.get("/api/v1/admin/faceswap/status").status_code == 403


# --------------------------------------------------------------- 路由契约


def test_get_status_endpoint(owner_client):
    resp = owner_client.get("/api/v1/admin/faceswap/status")
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["data"]["installed"] is False
    assert body["data"]["enabled"] is False
    assert body["data"]["engine_running"] is False


def test_update_sources_endpoint(owner_client):
    resp = owner_client.put(
        "/api/v1/admin/faceswap/sources",
        json={"hf_endpoint": "https://hf.example", "https_proxy": "http://p:8080"},
    )
    assert resp.status_code == 200
    sources = resp.json()["data"]["sources"]
    assert sources["hf_endpoint"] == "https://hf.example"
    assert sources["https_proxy"] == "http://p:8080"
    assert sources["pip_index_url"] == faceswap_install.DEFAULT_SOURCES["pip_index_url"]


def test_enable_toggle_endpoint(owner_client):
    on = owner_client.post("/api/v1/admin/faceswap/enable", json={"enabled": True}).json()
    assert on["data"]["enabled"] is True
    off = owner_client.post("/api/v1/admin/faceswap/enable", json={"enabled": False}).json()
    assert off["data"]["enabled"] is False


def test_install_endpoint_wiring(owner_client, monkeypatch):
    monkeypatch.setattr(
        faceswap_install,
        "start_install",
        lambda: {"install_state": faceswap_install.STATE_INSTALLING},
    )
    resp = owner_client.post("/api/v1/admin/faceswap/install")
    assert resp.status_code == 200
    assert resp.json()["data"]["install_state"] == faceswap_install.STATE_INSTALLING


def test_uninstall_endpoint(owner_client):
    resp = owner_client.post("/api/v1/admin/faceswap/uninstall")
    assert resp.status_code == 200
    assert resp.json()["data"]["install_state"] == faceswap_install.STATE_NOT_INSTALLED


def test_engine_start_rejected_when_not_local_runtime(owner_client, monkeypatch):
    monkeypatch.setattr(faceswap_install, "is_local_runtime", lambda: False)
    resp = owner_client.post("/api/v1/admin/faceswap/engine/start")
    assert resp.status_code == 409


# --------------------------------------------------------------- Web 不推流：未起边车返回 idle


def test_engine_state_idle_when_not_running(owner_client):
    resp = owner_client.get("/api/v1/admin/faceswap/engine/state")
    assert resp.status_code == 200
    # 边车未起：不转发、不外连，直接返回 idle。
    assert resp.json()["data"]["running"] is False


def test_engine_config_idle_when_not_running(owner_client):
    resp = owner_client.post(
        "/api/v1/admin/faceswap/engine/config", json={"many_faces": True}
    )
    assert resp.status_code == 200
    assert resp.json()["data"]["running"] is False
