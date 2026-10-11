# SPDX-License-Identifier: Elastic-2.0
# Copyright (c) 2026 ClaymoreLab
"""[aipdd fork delta] 设置→3D 世界：运行时按需安装/开关/下载源的回归测试。

覆盖 world_install 编排模块与 /admin/world/* 路由两层：
1. 默认状态（未安装、未启用）与下载源深合并落盘；
2. require_ready 闸门：未启用 / 已启用未装 各给可操作提示（抛 Sharp3DUnavailable）；
3. 子进程 env 与 uv 命令按下载源拼装（镜像/代理/权重缓存目录）；
4. 安装流程（mock Popen，绝不起真实 uv）跑通 INSTALLING → READY；
5. 路由超管闸门（member / agent token 被拒）+ status/sources/enable/install/uninstall 契约。

world_dir 用 monkeypatch 固定到 tmp_path，测试不碰真实 /data/state。
"""

from __future__ import annotations

import threading

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from novelvideo.api.auth import get_api_user
from novelvideo.api.routes import world as world_routes
from novelvideo.director_world import world_install
from novelvideo.director_world.pano_sharp import Sharp3DUnavailable


def _patch_world_dir(monkeypatch, tmp_path):
    root = tmp_path / "world"
    monkeypatch.setattr(world_install, "world_dir", lambda: root)
    return root


def _build_client(monkeypatch, tmp_path, user):
    _patch_world_dir(monkeypatch, tmp_path)
    app = FastAPI()
    app.include_router(world_routes.router, prefix="/api/v1")
    app.dependency_overrides[get_api_user] = lambda: dict(user)
    return TestClient(app)


@pytest.fixture()
def wdir(monkeypatch, tmp_path):
    return _patch_world_dir(monkeypatch, tmp_path)


@pytest.fixture()
def owner_client(monkeypatch, tmp_path):
    return _build_client(
        monkeypatch, tmp_path, {"id": "u-owner", "username": "admin", "role": "owner"}
    )


# --------------------------------------------------------------- 状态与配置


def test_default_status_not_installed(wdir):
    snap = world_install.status_snapshot()
    assert snap["installed"] is False
    assert snap["enabled"] is False
    assert snap["install_state"] == world_install.STATE_NOT_INSTALLED
    assert snap["installing"] is False
    assert snap["sources"]["pip_index_url"] == world_install.DEFAULT_SOURCES["pip_index_url"]


def test_save_load_sources_deep_merge(wdir):
    world_install.save_settings({"sources": {"pip_index_url": "http://mirror/simple"}})
    s = world_install.load_settings()
    assert s["sources"]["pip_index_url"] == "http://mirror/simple"
    # 未传的字段保留默认，不整表覆盖丢失。
    assert s["sources"]["npm_registry"] == world_install.DEFAULT_SOURCES["npm_registry"]
    assert (wdir / "settings.json").exists()


def test_corrupt_settings_falls_back(wdir):
    wdir.mkdir(parents=True, exist_ok=True)
    (wdir / "settings.json").write_text("{not json", encoding="utf-8")
    s = world_install.load_settings()
    assert s["enabled"] is False
    assert s["sources"] == world_install.DEFAULT_SOURCES


# --------------------------------------------------------------- 就绪闸门


def test_require_ready_raises_when_disabled(wdir):
    with pytest.raises(Sharp3DUnavailable) as exc:
        world_install.require_ready()
    assert "未启用" in str(exc.value)


def test_require_ready_raises_when_enabled_not_installed(wdir):
    world_install.save_settings({"enabled": True})
    with pytest.raises(Sharp3DUnavailable) as exc:
        world_install.require_ready()
    assert "尚未安装" in str(exc.value)


def test_resolve_world_interpreter_none_when_missing(wdir):
    assert world_install.resolve_world_interpreter() is None


# --------------------------------------------------------------- env / 命令拼装


def test_build_child_env_sets_cache_and_proxy(wdir):
    sources = dict(world_install.DEFAULT_SOURCES)
    sources["https_proxy"] = "http://127.0.0.1:7890"
    env = world_install._build_child_env(sources)
    assert env["HTTPS_PROXY"] == "http://127.0.0.1:7890"
    assert env["HF_ENDPOINT"] == sources["hf_endpoint"]
    assert env["TORCH_HOME"].endswith("torch_cache")
    assert env["HF_HOME"].endswith("hf_cache")


def test_build_uv_command_uses_extra_and_indexes(wdir):
    cmd = world_install._build_uv_command(
        {"pip_index_url": "https://p/simple", "torch_index_url": "https://t/cpu"}
    )
    joined = " ".join(cmd)
    # 回归（通用系统）：绝不能再 --frozen——那会绕过 torch 变体索引、照 uv.lock 里
    # 锁死的 CUDA-on-PyPI torch 图装，把无 GPU 机器拖去下载 GB 级 nvidia 依赖。
    assert "--frozen" not in cmd
    assert "--extra world" in joined
    assert "--default-index https://p/simple" in joined
    # 用户显式填了 torch 源 → 手动覆盖优先，原样作为 --index。
    assert "--index https://t/cpu" in joined
    # 回归：--python 绝不能指向尚未创建的 world venv（uv 会报 No interpreter found），
    # 必须用一个已存在的解释器；目标 venv 靠 UV_PROJECT_ENVIRONMENT 定位。
    assert str(world_install.venv_python()) not in joined
    import sys as _sys

    assert _sys.executable in cmd


def test_build_uv_command_skips_empty_pip_index(wdir):
    # pip 镜像留空 → 不拼 --default-index；torch 源显式填写则原样作为 --index。
    cmd = world_install._build_uv_command(
        {"pip_index_url": "", "torch_index_url": "https://t/explicit"}
    )
    joined = " ".join(cmd)
    assert "--default-index" not in cmd
    assert "--index https://t/explicit" in joined


def test_build_uv_command_auto_resolves_cpu_index_when_source_blank(wdir, monkeypatch):
    monkeypatch.setattr(
        world_install,
        "detect_torch_profile",
        lambda: {"os": "linux", "arch": "x86_64", "has_nvidia": False, "flavor": "cpu"},
    )
    cmd = world_install._build_uv_command({"pip_index_url": "https://p/simple", "torch_index_url": ""})
    joined = " ".join(cmd)
    # 留空时按硬件解析出 cpu 变体索引（南大镜像），而不是根本不装 torch 源。
    assert "--index https://mirrors.nju.edu.cn/pytorch/whl/cpu" in joined


# --------------------------------------------------------------- 环境/硬件解析


def test_pick_cuda_flavor_maps_to_largest_supported():
    assert world_install._pick_cuda_flavor(12.8) == "cu128"
    assert world_install._pick_cuda_flavor(12.6) == "cu126"
    assert world_install._pick_cuda_flavor(12.3) == "cu121"
    assert world_install._pick_cuda_flavor(11.8) == "cu118"
    assert world_install._pick_cuda_flavor(11.0) is None


def test_resolve_torch_index_manual_override_wins():
    idx = world_install.resolve_torch_index({"torch_index_url": "https://mine/custom"})
    assert idx == "https://mine/custom"


def test_resolve_torch_index_auto_from_flavor():
    idx = world_install.resolve_torch_index(
        {"torch_index_url": ""}, {"flavor": "cu124"}
    )
    assert idx == "https://mirrors.nju.edu.cn/pytorch/whl/cu124"


def test_detect_torch_profile_linux_no_gpu_is_cpu(wdir, monkeypatch):
    monkeypatch.setattr(world_install.platform, "system", lambda: "Linux")
    monkeypatch.setattr(world_install.platform, "machine", lambda: "x86_64")
    monkeypatch.setattr(world_install, "_nvidia_cuda_version", lambda: None)
    p = world_install.detect_torch_profile()
    assert p["os"] == "linux"
    assert p["has_nvidia"] is False
    assert p["flavor"] == "cpu"


def test_detect_torch_profile_linux_with_cuda_picks_flavor(wdir, monkeypatch):
    monkeypatch.setattr(world_install.platform, "system", lambda: "Linux")
    monkeypatch.setattr(world_install.platform, "machine", lambda: "x86_64")
    monkeypatch.setattr(world_install, "_nvidia_cuda_version", lambda: 12.6)
    p = world_install.detect_torch_profile()
    assert p["has_nvidia"] is True
    assert p["cuda_version"] == 12.6
    assert p["flavor"] == "cu126"


def test_detect_torch_profile_macos_is_cpu_without_probe(wdir, monkeypatch):
    monkeypatch.setattr(world_install.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(world_install.platform, "machine", lambda: "arm64")
    # macOS 不应去跑 nvidia-smi（无 CUDA pip 轮子）。
    monkeypatch.setattr(
        world_install,
        "_nvidia_cuda_version",
        lambda: (_ for _ in ()).throw(AssertionError("macOS 不应探测 nvidia-smi")),
    )
    p = world_install.detect_torch_profile()
    assert p["os"] == "darwin"
    assert p["arch"] == "arm64"
    assert p["flavor"] == "cpu"


# --------------------------------------------------------------- 安装流程（mock）


class _FakePopen:
    # 记录子进程收到的 cmd 与 env，供断言 venv 定位与安装参数。
    last: dict = {}

    def __init__(self, *args, **kwargs):
        type(self).last = {"cmd": list(args[0]) if args else [], "env": dict(kwargs.get("env") or {})}
        self.stdout = iter(
            [
                "Resolved 120 packages in 3ms\n",
                "Downloading torch (2.1GiB)\n",
                "Built sharp @ git+https://github.com/apple/ml-sharp\n",
                "Installed 90 packages\n",
            ]
        )

    def wait(self) -> int:
        return 0


def test_start_install_transitions_to_ready(wdir, monkeypatch):
    monkeypatch.setattr(world_install.subprocess, "Popen", _FakePopen)
    monkeypatch.setattr(world_install, "is_installed", lambda: True)
    monkeypatch.setattr(world_install, "_INSTALL_THREAD", None)

    world_install.start_install()
    thread = world_install._INSTALL_THREAD
    assert isinstance(thread, threading.Thread)
    thread.join(timeout=10)
    assert not thread.is_alive()

    # 关键回归：安装必须打到独立 world venv，而不是镜像里默认的 /app/.venv。
    captured_env = _FakePopen.last["env"]
    assert captured_env.get("UV_PROJECT_ENVIRONMENT") == str(world_install.venv_dir())

    final = world_install.load_settings()
    assert final["install_state"] == world_install.STATE_READY
    assert final["installed_at"]


def test_start_install_failure_marks_failed(wdir, monkeypatch):
    class _FailPopen(_FakePopen):
        def wait(self) -> int:
            return 1

    monkeypatch.setattr(world_install.subprocess, "Popen", _FailPopen)
    monkeypatch.setattr(world_install, "is_installed", lambda: False)
    monkeypatch.setattr(world_install, "_INSTALL_THREAD", None)

    world_install.start_install()
    world_install._INSTALL_THREAD.join(timeout=10)
    assert world_install.load_settings()["install_state"] == world_install.STATE_FAILED


# --------------------------------------------------------------- 权限门


def test_member_is_rejected(monkeypatch, tmp_path):
    client = _build_client(
        monkeypatch, tmp_path, {"id": "u-mem", "username": "alice", "role": "member"}
    )
    assert client.get("/api/v1/admin/world/status").status_code == 403
    assert (
        client.post("/api/v1/admin/world/install").status_code == 403
    )


def test_agent_token_is_rejected_even_with_owner_role(monkeypatch, tmp_path):
    client = _build_client(
        monkeypatch,
        tmp_path,
        {"id": "u-agent", "username": "bot", "role": "owner", "credential_kind": "agent_session"},
    )
    assert client.get("/api/v1/admin/world/status").status_code == 403


# --------------------------------------------------------------- 路由契约


def test_get_status_endpoint(owner_client):
    resp = owner_client.get("/api/v1/admin/world/status")
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["data"]["installed"] is False
    assert body["data"]["enabled"] is False


def test_update_sources_endpoint(owner_client):
    resp = owner_client.put(
        "/api/v1/admin/world/sources",
        json={"pip_index_url": "https://my.mirror/simple", "https_proxy": "http://p:8080"},
    )
    assert resp.status_code == 200
    sources = resp.json()["data"]["sources"]
    assert sources["pip_index_url"] == "https://my.mirror/simple"
    assert sources["https_proxy"] == "http://p:8080"
    # 未传的字段保留默认。
    assert sources["npm_registry"] == world_install.DEFAULT_SOURCES["npm_registry"]


def test_enable_toggle_endpoint(owner_client):
    on = owner_client.post("/api/v1/admin/world/enable", json={"enabled": True}).json()
    assert on["data"]["enabled"] is True
    off = owner_client.post("/api/v1/admin/world/enable", json={"enabled": False}).json()
    assert off["data"]["enabled"] is False


def test_install_endpoint_wiring(owner_client, monkeypatch):
    monkeypatch.setattr(
        world_install, "start_install", lambda: {"install_state": world_install.STATE_INSTALLING}
    )
    resp = owner_client.post("/api/v1/admin/world/install")
    assert resp.status_code == 200
    assert resp.json()["data"]["install_state"] == world_install.STATE_INSTALLING


def test_uninstall_endpoint(owner_client):
    resp = owner_client.post("/api/v1/admin/world/uninstall")
    assert resp.status_code == 200
    assert resp.json()["data"]["install_state"] == world_install.STATE_NOT_INSTALLED
