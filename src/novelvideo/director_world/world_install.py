# SPDX-License-Identifier: Elastic-2.0
# Copyright (c) 2026 ClaymoreLab
"""[aipdd fork delta] 3D 世界（world / 3DGS / SHARP）运行时按需安装与开关。

设计（用户选定的方案 A）：
- 精简镜像只常驻轻工具（git/nodejs/npm/@playcanvas/splat-transform），**不含**
  torch/sharp/da2 这几个 GB 级重依赖。
- 用户在「设置 → 3D 世界」点「下载并部署」→ 后台任务把 world extra 用 uv 装到
  **持久卷上的独立 venv**（``<STATE_DIR>/world/venv``，STATE_DIR 默认 /data/state，
  故跨容器重建保留）。安装目标 venv 含 novelvideo 自身 + world extra，因此
  ``venv/bin/python -m novelvideo.director_world.pano_sharp`` 能直接 import sharp。
- 模型权重（Apple SHARP .pt、DA-2）不进镜像，运行时下到 ``<STATE_DIR>/world`` 缓存。
- 下载源（pip 镜像 / npm / HuggingFace / 可选代理）默认已填国内可用镜像，用户可在
  设置里改。整个安装/下载依赖容器能出网。

本模块只负责编排（写 settings.json、跑 uv 子进程、更新状态），不改变 world 之外的行为。
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# 安装状态机
STATE_NOT_INSTALLED = "not_installed"
STATE_INSTALLING = "installing"
STATE_READY = "ready"
STATE_FAILED = "failed"

_LOCK = threading.RLock()
_INSTALL_THREAD: threading.Thread | None = None


def world_dir() -> Path:
    from novelvideo import config

    return Path(config.STATE_DIR) / "world"


def venv_dir() -> Path:
    return world_dir() / "venv"


def venv_python() -> Path:
    return venv_dir() / "bin" / "python"


def settings_path() -> Path:
    return world_dir() / "settings.json"


def install_log_path() -> Path:
    return world_dir() / "install.log"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# 国内默认可用镜像；用户可在设置里逐项覆盖（"帮我默认好，用户自己去选择下载"）。
DEFAULT_SOURCES: dict[str, str] = {
    "pip_index_url": "https://pypi.tuna.tsinghua.edu.cn/simple",
    "torch_index_url": "https://mirrors.nju.edu.cn/pytorch/whl/cpu",
    "npm_registry": "https://registry.npmmirror.com",
    "hf_endpoint": "https://hf-mirror.com",
    "https_proxy": "",  # 可选：需要走代理时填 http://host:port
}

DEFAULT_SETTINGS: dict[str, Any] = {
    "enabled": False,  # 是否对画布开放 3D 世界入口
    "install_state": STATE_NOT_INSTALLED,
    "install_progress": 0.0,
    "install_message": "",
    "installed_at": "",
    "sources": dict(DEFAULT_SOURCES),
}


def _default_settings() -> dict[str, Any]:
    data = dict(DEFAULT_SETTINGS)
    data["sources"] = dict(DEFAULT_SOURCES)
    return data


def load_settings() -> dict[str, Any]:
    """读取 world 特性配置；缺字段用默认补齐，绝不因文件损坏而抛。"""
    path = settings_path()
    data = _default_settings()
    try:
        if path.exists():
            raw = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                for k, v in raw.items():
                    if k == "sources" and isinstance(v, dict):
                        merged = dict(DEFAULT_SOURCES)
                        merged.update({str(a): str(b) for a, b in v.items()})
                        data["sources"] = merged
                    elif k in data:
                        data[k] = v
    except Exception:  # noqa: BLE001 - 配置文件坏掉时回落默认，不阻断
        pass
    return data


def save_settings(patch: dict[str, Any]) -> dict[str, Any]:
    """浅合并写回（sources 单独深合并），返回最新全量。"""
    with _LOCK:
        current = load_settings()
        for k, v in patch.items():
            if k == "sources" and isinstance(v, dict):
                merged = dict(current.get("sources", {}))
                merged.update({str(a): str(b) for a, b in v.items()})
                current["sources"] = merged
            else:
                current[k] = v
        path = settings_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(current, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, path)
        return current


def _venv_has_module(name: str) -> bool:
    """在 world venv 里探测某个包是否已装（不 import 主进程）。"""
    py = venv_python()
    if not py.exists():
        return False
    try:
        proc = subprocess.run(
            [str(py), "-c", f"import importlib.util,sys; sys.exit(0 if importlib.util.find_spec({name!r}) else 1)"],
            capture_output=True,
            text=True,
            timeout=60,
        )
        return proc.returncode == 0
    except Exception:  # noqa: BLE001
        return False


def sharp_ready() -> bool:
    return _venv_has_module("sharp")


def da2_ready() -> bool:
    return _venv_has_module("da2")


def is_installed() -> bool:
    """venv 里有 sharp 即视为 world 已就绪（torch/sharp/da2 同批装）。"""
    return sharp_ready()


def is_enabled() -> bool:
    """画布是否真正可用 3D 世界：既要开关打开，也要依赖已装好。"""
    s = load_settings()
    return bool(s.get("enabled")) and is_installed()


def install_running() -> bool:
    """后台安装线程是否存活（供路由层拒绝并发卸载 / 重复触发）。"""
    thread = _INSTALL_THREAD
    return thread is not None and thread.is_alive()


def resolve_world_interpreter() -> tuple[str, dict[str, str]] | None:
    """给 stage_asset_tasks 用：world 就绪时返回 (venv python, 注入 env)，否则 None。"""
    if not is_installed():
        return None
    py = venv_python()
    env = _build_child_env(load_settings().get("sources", {}))
    return str(py), env


def require_ready() -> tuple[str, dict[str, str]]:
    """就绪则返回 (venv python, env)；否则抛 Sharp3DUnavailable（带可操作提示）。"""
    from novelvideo.director_world.pano_sharp import Sharp3DUnavailable

    resolved = resolve_world_interpreter()
    if resolved is None:
        if not load_settings().get("enabled"):
            raise Sharp3DUnavailable(
                "3D 世界未启用。请在 设置 → 3D 世界 打开开关并点击「下载并部署」。"
            )
        raise Sharp3DUnavailable(
            "3D 世界依赖尚未安装完成。请在 设置 → 3D 世界 点击「下载并部署」；"
            "安装进度与失败原因见该页，多为网络/镜像源问题，可在下载源里换镜像或填代理后重试。"
        )
    return resolved


def _build_child_env(sources: dict[str, str]) -> dict[str, str]:
    wd = world_dir()
    env = dict(os.environ)
    # 权重与 uv/工具缓存都落在持久卷，跨重建保留、也避免容器根盘撑爆。
    env["TORCH_HOME"] = str(wd / "torch_cache")
    env["HF_HOME"] = str(wd / "hf_cache")
    env["HUGGINGFACE_HUB_CACHE"] = str(wd / "hf_cache")
    if sources.get("hf_endpoint"):
        env["HF_ENDPOINT"] = sources["hf_endpoint"]
    if sources.get("https_proxy"):
        env["HTTPS_PROXY"] = sources["https_proxy"]
        env["HTTP_PROXY"] = sources["https_proxy"]
    env["PIP_INDEX_URL"] = sources.get("pip_index_url") or env.get("PIP_INDEX_URL", "")
    return env


def _build_uv_command(sources: dict[str, str]) -> list[str]:
    # 目标 venv 靠子进程 env 里的 UV_PROJECT_ENVIRONMENT 指定（见 _run_install），
    # 不能把 --python 指向尚未创建的 venv/bin/python（uv 会报 No interpreter found）。
    # --python 传一个已存在的解释器（当前运行进程），uv 会在 UV_PROJECT_ENVIRONMENT
    # 位置用它建 venv；--frozen 直接复用 uv.lock（与 Dockerfile 的 INSTALL_WORLD 装法一致）。
    uv = shutil.which("uv") or "uv"
    cmd = [
        uv,
        "sync",
        "--frozen",
        "--extra",
        "world",
        "--no-dev",
        "--python",
        sys.executable,
    ]
    pip_index = sources.get("pip_index_url")
    if pip_index:
        cmd += ["--default-index", pip_index]
    torch_index = sources.get("torch_index_url")
    if torch_index:
        cmd += ["--index", torch_index]
    return cmd


def _project_root() -> str:
    # 运行镜像里源码在 /app；开发态取包的上上级（src/novelvideo/director_world -> repo root）。
    env_root = os.environ.get("NOVELVIDEO_PROJECT_ROOT")
    if env_root:
        return env_root
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / "pyproject.toml").exists() and (parent / "uv.lock").exists():
            return str(parent)
    return "/app"


def status_snapshot() -> dict[str, Any]:
    s = load_settings()
    installed = is_installed()
    state = s.get("install_state", STATE_NOT_INSTALLED)
    # 安装线程已死但状态仍停在 installing（进程被重启等）→ 回落真实情况。
    if state == STATE_INSTALLING and not install_running():
        state = STATE_READY if installed else STATE_NOT_INSTALLED
    return {
        "enabled": bool(s.get("enabled")),
        "install_state": state,
        "installing": install_running(),
        "installed": installed,
        "da2_ready": da2_ready() if installed else False,
        "progress": float(s.get("install_progress") or 0.0),
        "message": str(s.get("install_message") or ""),
        "installed_at": str(s.get("installed_at") or ""),
        "sources": s.get("sources", dict(DEFAULT_SOURCES)),
        "venv_path": str(venv_dir()),
        "splat_transform": shutil.which("splat-transform") is not None,
        "can_install": _project_root() != "/app" or Path("/app/uv.lock").exists(),
        "log_tail": _read_log_tail(),
    }


def _read_log_tail(max_bytes: int = 4000) -> str:
    try:
        path = install_log_path()
        if not path.exists():
            return ""
        data = path.read_bytes()[-max_bytes:]
        return data.decode("utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        return ""


def start_install() -> dict[str, Any]:
    """异步启动安装；已在跑就直接返回当前状态，避免并发重复下载。"""
    global _INSTALL_THREAD
    with _LOCK:
        if _INSTALL_THREAD is not None and _INSTALL_THREAD.is_alive():
            return status_snapshot()
        save_settings(
            {
                "install_state": STATE_INSTALLING,
                "install_progress": 0.02,
                "install_message": "准备安装环境…",
            }
        )
        _INSTALL_THREAD = threading.Thread(target=_run_install, name="world-install", daemon=True)
        _INSTALL_THREAD.start()
    return status_snapshot()


def _set_progress(progress: float, message: str) -> None:
    save_settings(
        {
            "install_state": STATE_INSTALLING,
            "install_progress": max(0.0, min(0.99, float(progress))),
            "install_message": message,
        }
    )


def _run_install() -> None:
    wd = world_dir()
    try:
        wd.mkdir(parents=True, exist_ok=True)
        (wd / "torch_cache").mkdir(parents=True, exist_ok=True)
        (wd / "hf_cache").mkdir(parents=True, exist_ok=True)
        sources = load_settings().get("sources", {})
        env = _build_child_env(sources)
        # uv 自己的缓存也放持久卷
        env["UV_CACHE_DIR"] = str(wd / "uv_cache")
        # 关键：覆盖镜像全局 UV_PROJECT_ENVIRONMENT=/app/.venv，把 world 依赖装进独立 venv，
        # 不动应用主环境；uv 会在该路径不存在时自动建 venv。
        env["UV_PROJECT_ENVIRONMENT"] = str(venv_dir())
        cmd = _build_uv_command(sources)
        with _LOCK:
            logf = install_log_path().open("w", encoding="utf-8")
        proc = subprocess.Popen(
            cmd,
            cwd=_project_root(),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            env=env,
            bufsize=1,
        )
        tail = ""
        assert proc.stdout is not None
        for line in proc.stdout:
            logf.write(line)
            logf.flush()
            tail = (tail + line)[-2000:]
            low = line.lower()
            if "resolved" in low:
                _set_progress(0.1, "解析依赖…")
            elif "downloading" in low or "fetch" in low:
                _set_progress(0.45, "下载依赖（torch/sharp/da2，体积大）…")
            elif "built" in low or "building" in low:
                _set_progress(0.75, "构建源码包…")
            elif "installed" in low or "prepared" in low:
                _set_progress(0.92, "写入环境…")
        rc = proc.wait()
        logf.close()
        if rc == 0 and is_installed():
            save_settings(
                {
                    "install_state": STATE_READY,
                    "install_progress": 1.0,
                    "install_message": "3D 世界依赖已就绪。",
                    "installed_at": _now_iso(),
                }
            )
        else:
            save_settings(
                {
                    "install_state": STATE_FAILED,
                    "install_progress": 0.0,
                    "install_message": f"安装失败（退出码 {rc}）。多为网络/镜像源问题，可在下载源里换镜像或填代理后重试。",
                }
            )
    except Exception as exc:  # noqa: BLE001
        save_settings(
            {
                "install_state": STATE_FAILED,
                "install_progress": 0.0,
                "install_message": f"安装异常：{exc}",
            }
        )


def uninstall() -> dict[str, Any]:
    """删除 world venv（保留权重缓存以省重复下载可选）。返回状态。"""
    global _INSTALL_THREAD
    with _LOCK:
        if _INSTALL_THREAD is not None and _INSTALL_THREAD.is_alive():
            return status_snapshot()
        py = venv_python()
        if py.exists():
            shutil.rmtree(venv_dir(), ignore_errors=True)
        save_settings(
            {
                "install_state": STATE_NOT_INSTALLED,
                "install_progress": 0.0,
                "install_message": "",
                "installed_at": "",
            }
        )
    return status_snapshot()
