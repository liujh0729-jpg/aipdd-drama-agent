# SPDX-License-Identifier: Elastic-2.0
# Copyright (c) 2026 ClaymoreLab
"""[aipdd fork delta] 换脸直播：运行时按需安装引擎依赖 + 下载模型 + 边车进程监管。

仿 :mod:`novelvideo.director_world.world_install` 的状态机与后台编排，但**不使用**主
pyproject 的 ``uv sync --extra``（那会把重依赖混进主锁、也污染 Elastic-2.0 依赖图）。改
为在持久卷上建**独立 venv**（``<STATE_DIR>/faceswap/venv``），用 ``uv venv`` +
``uv pip install`` 把边车引擎所需的宽松许可依赖装进去；换脸模型下到
``<STATE_DIR>/faceswap/models``（见 :mod:`novelvideo.faceswap.model_download`）。

AGPL 隔离：本模块只 spawn/监管 ``services/faceswap`` 下的自研边车进程，**从不 import**
上游 Deep-Live-Cam，也从不 import 边车内部模块——只通过 localhost HTTP 控制口交互。

依赖装好 + 模型就绪 = ready；就绪后 ``start_engine`` 拉起边车（抓物理摄像头→换脸→写
虚拟摄像头），供 OBS/直播软件读取。整个安装/下载依赖本机或容器能出网。
"""

from __future__ import annotations

import json
import os
import platform
import shutil
import subprocess
import sys
import threading
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from novelvideo.faceswap import model_download

# 安装状态机（与 world_install 同名同义，前端复用同一套文案口径）。
STATE_NOT_INSTALLED = "not_installed"
STATE_INSTALLING = "installing"
STATE_READY = "ready"
STATE_FAILED = "failed"

_LOCK = threading.RLock()
_INSTALL_THREAD: threading.Thread | None = None
_ENGINE_LOCK = threading.RLock()

# 边车本地控制服务默认端口（仅绑定 127.0.0.1）。换脸引擎是独立进程，Web 面板经此口控制。
DEFAULT_CONTROL_PORT = 18899

# 边车引擎所需依赖（全部宽松许可：insightface=Apache-2.0, opencv, onnxruntime(MIT),
# pyvirtualcam(MIT), numpy(BSD), fastapi/uvicorn(MIT) 等）。**不含** PySide6（那是上游
# GUI，我们自研 Web 控制面板，不需要桌面 GUI），也**不含** torch（推理走 onnxruntime）。
# insightface 只能取 1.0 以上的版本：0.7.3（边车最初对着写的版本）在 PyPI 上**只有 sdist**，
# 装它要在用户机上现编 Cython 扩展（实测产出 .cp312-win_amd64.pyd），没有 MSVC 的普通 Windows
# 必装失败；1.0.1 起发纯 Python 轮子，且 FaceAnalysis(root=…, allowed_modules=…)/
# model_zoo.get_model 的调用面与边车一致（已在装机版便携 Python 3.12 上实跑验证）。
_ENGINE_PACKAGES: tuple[str, ...] = (
    "numpy>=2.0,<3",
    "opencv-python",
    "insightface==1.0.1",
    "onnx",
    "pillow",
    "tqdm",
    "certifi",
    "psutil",
    "pyvirtualcam",
    "fastapi",
    "uvicorn[standard]",
    "python-multipart",
    "onnxruntime",
)

DEFAULT_SOURCES: dict[str, str] = {
    # 换脸引擎不需要 torch/npm，下载源保留 pip 镜像 / HF / 代理三项即可。
    # 镜像只当加速用，官方 PyPI 永远兜底（见 _FALLBACK_INDEX）：清华源对 uv 会整站 403。
    "pip_index_url": "https://pypi.tuna.tsinghua.edu.cn/simple",
    "hf_endpoint": "https://hf-mirror.com",
    "https_proxy": "",
}

# 镜像缺包 / 拒绝服务时的兜底索引，也是用户把镜像清空后的默认索引。
_FALLBACK_INDEX = "https://pypi.org/simple"

DEFAULT_SETTINGS: dict[str, Any] = {
    "enabled": False,  # 是否在「工具」开放换脸直播入口
    "install_state": STATE_NOT_INSTALLED,
    "install_progress": 0.0,
    "install_message": "",
    "installed_at": "",
    "models_ready": False,
    "sources": dict(DEFAULT_SOURCES),
    "control_port": DEFAULT_CONTROL_PORT,
}


def faceswap_dir() -> Path:
    from novelvideo import config

    return Path(config.STATE_DIR) / "faceswap"


def venv_dir() -> Path:
    return faceswap_dir() / "venv"


def venv_python() -> Path:
    # macOS/Linux 布局；Windows 的 Scripts 分支在 engine_command 里按平台取。
    if platform.system().lower() == "windows":
        return venv_dir() / "Scripts" / "python.exe"
    return venv_dir() / "bin" / "python"


def models_dir() -> Path:
    return faceswap_dir() / "models"


def settings_path() -> Path:
    return faceswap_dir() / "settings.json"


def install_log_path() -> Path:
    return faceswap_dir() / "install.log"


def engine_log_path() -> Path:
    return faceswap_dir() / "engine.log"


def pid_path() -> Path:
    return faceswap_dir() / "engine.pid"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _default_settings() -> dict[str, Any]:
    data = dict(DEFAULT_SETTINGS)
    data["sources"] = dict(DEFAULT_SOURCES)
    return data


def load_settings() -> dict[str, Any]:
    """读取换脸特性配置；缺字段用默认补齐，绝不因文件损坏而抛。"""
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
    """在 faceswap venv 里探测某个包是否已装（不 import 主进程）。"""
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


def deps_ready() -> bool:
    """venv 里 insightface + onnxruntime + pyvirtualcam 齐备即视为依赖就绪。"""
    return (
        _venv_has_module("insightface")
        and _venv_has_module("onnxruntime")
        and _venv_has_module("pyvirtualcam")
    )


def models_ready() -> bool:
    return model_download.required_present(str(models_dir()))


def is_installed() -> bool:
    """就绪 = 依赖装好 **且** 必需模型已下载。"""
    return deps_ready() and models_ready()


def is_enabled() -> bool:
    s = load_settings()
    return bool(s.get("enabled"))


def install_running() -> bool:
    thread = _INSTALL_THREAD
    return thread is not None and thread.is_alive()


def control_port() -> int:
    try:
        return int(load_settings().get("control_port") or DEFAULT_CONTROL_PORT)
    except (TypeError, ValueError):
        return DEFAULT_CONTROL_PORT


# ── 硬件/推理后端探测（通用系统，不假设具体机器）─────────────────────────
def detect_profile() -> dict[str, Any]:
    """只读探测 OS/架构 + 有没有 NVIDIA GPU，给出建议的 onnxruntime 执行后端。

    - macOS（Apple Silicon 常见）：coreml / cpu（onnxruntime 自带 CoreML EP）。
    - Linux/Windows 且有 nvidia-smi：倾向 cuda（但默认包仍是 CPU onnxruntime，
      GPU 需要用户后续换成 onnxruntime-gpu，这里只给建议，避免默认下 GB 级 CUDA 轮子）。
    - 无 GPU：cpu。
    """
    system = (platform.system() or "").lower()
    machine = (platform.machine() or "").lower()
    has_nvidia = False
    if system == "linux":
        exe = shutil.which("nvidia-smi")
        if exe:
            try:
                proc = subprocess.run([exe], capture_output=True, text=True, timeout=5)
                has_nvidia = proc.returncode == 0
            except (OSError, subprocess.SubprocessError):
                has_nvidia = False
    if system == "darwin":
        provider = "coreml" if machine == "arm64" else "cpu"
        reason = f"macOS/{machine}：默认 onnxruntime（CoreML 加速可选，CPU 兜底）。"
    elif has_nvidia:
        provider = "cuda"
        reason = "检测到 NVIDIA GPU：可后续切换 onnxruntime-gpu 获得 CUDA 加速（默认仍装 CPU 版以保轻量，失败自动 CPU 兜底）。"
    else:
        provider = "cpu"
        reason = "未检测到可用 GPU：使用 CPU 版 onnxruntime 推理。"
    return {
        "os": system or "unknown",
        "arch": machine or "unknown",
        "has_nvidia": has_nvidia,
        "provider": provider,
        "reason": reason,
    }


# ── 平台/形态/虚拟摄像头前置探测 ────────────────────────────────────────
def vcam_probe() -> dict[str, Any]:
    """探测系统级虚拟摄像头是否可用（OBS 读它）。仅桌面/本机形态有意义。"""
    system = (platform.system() or "").lower()
    available = False
    hint = ""
    if system == "darwin":
        # macOS：pyvirtualcam 走 "obs" 后端，需要已安装 OBS Studio 的虚拟摄像头设备。
        available = os.path.isdir("/Applications/OBS.app") or _obs_plugin_present()
        hint = "macOS：需在系统安装 OBS Studio（其虚拟摄像头供 OBS/直播软件读取）。"
    elif system == "windows":
        # Windows：需要 OBS 虚拟摄像头或 Unity Video Capture 设备随附的驱动。
        available = True
        hint = "Windows：需安装 OBS Studio 虚拟摄像头驱动。"
    elif system == "linux":
        # Linux：需要内核 v4l2loopback 模块提供 /dev/video* 回环设备。
        available = _v4l2loopback_present()
        hint = "Linux：需加载 v4l2loopback 内核模块生成回环摄像头设备。"
    else:
        hint = "未知平台：无法探测虚拟摄像头设备。"
    return {"system": system, "available": available, "hint": hint}


def _obs_plugin_present() -> bool:
    return os.path.exists(
        "/Applications/OBS.app/Contents/PlugIns/Obs-fake-device.app"
    ) or os.path.exists(
        "/Library/CoreMediaIO/Plug-Ins/DAL/obs-mac-virtualcam.plugin"
    )


def _v4l2loopback_present() -> bool:
    try:
        import glob

        return bool(glob.glob("/sys/module/v4l2loopback"))
    except Exception:  # noqa: BLE001
        return False


def is_local_runtime() -> bool:
    """换脸直播仅在能访问物理摄像头 + 系统虚拟摄像头的本机/桌面形态可用。

    Docker Web 容器形态（``DRAMACLAW_CONTAINER=1`` 或类似标记）下应置灰。
    """
    if os.environ.get("DRAMACLAW_CONTAINER", "").lower() in {"1", "true", "yes"}:
        return False
    # 容器里通常没有 /dev/video*（Linux）且无法注册系统级虚拟摄像头。
    return True


# ── 边车引擎进程监管（spawn / stop / health）────────────────────────────
def engine_script_path() -> Path:
    return Path(_project_root()) / "services" / "faceswap" / "server.py"


def _project_root() -> str:
    env_root = os.environ.get("NOVELVIDEO_PROJECT_ROOT")
    if env_root:
        return env_root
    here = Path(__file__).resolve()
    # 认「能找到边车本体」的那一层，而不是认 pyproject.toml：桌面安装包里没有
    # pyproject.toml，布局是 <资源根>/app/{src,services}，只按 pyproject 找就会一路
    # 落到 "/app"，Windows 上那是个不存在的目录 → 引擎脚本永远探不到、拉起也必炸。
    for parent in here.parents:
        if (parent / "services" / "faceswap" / "server.py").exists():
            return str(parent)
    return "/app"


def _read_pid() -> int | None:
    try:
        if pid_path().exists():
            return int(pid_path().read_text(encoding="utf-8").strip())
    except (TypeError, ValueError, OSError):
        return None
    return None


def engine_running() -> bool:
    """边车控制口 /health 探活（进程被外部杀掉也能据此判死）。"""
    port = control_port()
    try:
        req = urllib.request.Request(f"http://127.0.0.1:{port}/health", method="GET")
        with urllib.request.urlopen(req, timeout=2) as resp:  # noqa: S310
            return 200 <= getattr(resp, "status", 200) < 300
    except (urllib.error.URLError, OSError, ValueError):
        return False


def start_engine() -> dict[str, Any]:
    """就绪后拉起边车引擎进程（抓帧→换脸→写虚拟摄像头）。已运行则直接返回状态。"""
    with _ENGINE_LOCK:
        if engine_running():
            return status_snapshot()
        if not is_installed():
            raise RuntimeError(
                "换脸直播尚未就绪：请先在 设置 → 环境安装 点「下载并部署」完成依赖与模型下载。"
            )
        py = str(venv_python())
        server = str(engine_script_path())
        if not Path(py).exists() or not Path(server).exists():
            raise RuntimeError("换脸引擎文件缺失，请重新「下载并部署」。")
        port = control_port()
        env = _build_child_env(load_settings().get("sources", {}))
        cmd = [py, server, "--port", str(port), "--models", str(models_dir())]
        engine_log_path().parent.mkdir(parents=True, exist_ok=True)
        logf = engine_log_path().open("ab")
        proc = subprocess.Popen(  # noqa: S603
            cmd,
            cwd=_project_root(),
            stdout=logf,
            stderr=subprocess.STDOUT,
            env=env,
        )
        pid_path().write_text(str(proc.pid), encoding="utf-8")
        save_settings({"engine_pid": proc.pid})
    # 稍等其起控制口
    _wait_engine_up(port)
    return status_snapshot()


def _wait_engine_up(port: int, timeout: float = 8.0) -> None:
    import time

    deadline = time.time() + timeout
    while time.time() < deadline:
        if engine_running():
            return
        time.sleep(0.4)


def stop_engine() -> dict[str, Any]:
    """停边车引擎：先试 /stop 优雅退出，再按 pid 兜底 kill。"""
    with _ENGINE_LOCK:
        port = control_port()
        try:
            req = urllib.request.Request(
                f"http://127.0.0.1:{port}/stop", method="POST"
            )
            with urllib.request.urlopen(req, timeout=3):  # noqa: S310
                pass
        except (urllib.error.URLError, OSError, ValueError):
            pid = _read_pid()
            if pid:
                try:
                    os.kill(pid, 15)
                except OSError:
                    pass
        try:
            pid_path().unlink()
        except OSError:
            pass
    return status_snapshot()


def status_snapshot() -> dict[str, Any]:
    s = load_settings()
    installed = is_installed()
    state = s.get("install_state", STATE_NOT_INSTALLED)
    if state == STATE_INSTALLING and not install_running():
        state = STATE_READY if installed else STATE_NOT_INSTALLED
    running = engine_running()
    return {
        "enabled": bool(s.get("enabled")),
        "install_state": state,
        "installing": install_running(),
        "installed": installed,
        "deps_ready": deps_ready(),
        "models_ready": models_ready() if installed or deps_ready() else bool(s.get("models_ready")),
        "progress": float(s.get("install_progress") or 0.0),
        "message": str(s.get("install_message") or ""),
        "installed_at": str(s.get("installed_at") or ""),
        "sources": s.get("sources", dict(DEFAULT_SOURCES)),
        "venv_path": str(venv_dir()),
        "models_dir": str(models_dir()),
        "control_port": control_port(),
        "engine_running": running,
        "local_runtime": is_local_runtime(),
        "vcam": vcam_probe(),
        "hardware": detect_profile(),
        "can_install": shutil.which("uv") is not None or Path(sys.executable).exists(),
        "engine_script_present": engine_script_path().exists(),
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


def _build_child_env(sources: dict[str, str]) -> dict[str, str]:
    fd = faceswap_dir()
    env = dict(os.environ)
    env["HF_HOME"] = str(fd / "hf_cache")
    env["HUGGINGFACE_HUB_CACHE"] = str(fd / "hf_cache")
    if sources.get("hf_endpoint"):
        env["HF_ENDPOINT"] = sources["hf_endpoint"]
    if sources.get("https_proxy"):
        env["HTTPS_PROXY"] = sources["https_proxy"]
        env["HTTP_PROXY"] = sources["https_proxy"]
    pip_index = (sources.get("pip_index_url") or "").strip()
    if pip_index:
        env["UV_INDEX_URL"] = pip_index
        env["PIP_INDEX_URL"] = pip_index
    return env


def _uv() -> str:
    return shutil.which("uv") or "uv"


def _build_venv_command() -> list[str]:
    return [_uv(), "venv", str(venv_dir()), "--python", sys.executable]


def _build_pip_install_command(
    sources: dict[str, str], *, index_url: str | None = None
) -> list[str]:
    cmd = [_uv(), "pip", "install", "--python", str(venv_python())]
    if index_url is None:
        pip_index = (sources.get("pip_index_url") or "").strip()
    else:
        pip_index = index_url.strip()
    if pip_index:
        # 镜像做主索引，PyPI 做兜底：只认镜像时，镜像 403/缺包会让整套依赖一颗都装不上
        # （清华源对 uv 就整站 403）。多索引下 uv 仍优先走镜像，速度不受影响。
        cmd += ["--index-url", pip_index, "--extra-index-url", _FALLBACK_INDEX]
    else:
        cmd += ["--index-url", _FALLBACK_INDEX]
    cmd += list(_ENGINE_PACKAGES)
    return cmd


def _pip_install_env(sources: dict[str, str], *, direct_pypi: bool = False) -> dict[str, str]:
    env = _build_child_env(sources)
    if direct_pypi:
        # 兜底重试必须把镜像类环境变量摘掉，否则子进程里的库仍可能去撞同一个坏镜像。
        for key in ("UV_INDEX_URL", "PIP_INDEX_URL", "UV_DEFAULT_INDEX"):
            env.pop(key, None)
    return env


def _run_pip_install(cmd: list[str], env: dict[str, str], logf) -> int:
    proc = subprocess.Popen(  # noqa: S603
        cmd,
        cwd=_project_root(),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        env=env,
        bufsize=1,
    )
    assert proc.stdout is not None
    for line in proc.stdout:
        logf.write(line)
        low = line.lower()
        if "resolved" in low:
            _set_progress(0.25, "解析依赖…")
        elif "downloading" in low or "fetch" in low:
            _set_progress(0.4, "下载依赖…")
        elif "installed" in low or "prepared" in low:
            _set_progress(0.55, "写入环境…")
    return proc.wait()


def start_install() -> dict[str, Any]:
    """异步启动「下载并部署」；已在跑就返回当前状态，避免并发重复下载。"""
    global _INSTALL_THREAD
    with _LOCK:
        if _INSTALL_THREAD is not None and _INSTALL_THREAD.is_alive():
            return status_snapshot()
        save_settings(
            {
                "install_state": STATE_INSTALLING,
                "install_progress": 0.02,
                "install_message": "准备安装换脸引擎环境…",
            }
        )
        _INSTALL_THREAD = threading.Thread(
            target=_run_install, name="faceswap-install", daemon=True
        )
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
    fd = faceswap_dir()
    try:
        fd.mkdir(parents=True, exist_ok=True)
        (fd / "hf_cache").mkdir(parents=True, exist_ok=True)
        models_dir().mkdir(parents=True, exist_ok=True)
        sources = load_settings().get("sources", {})
        env = _build_child_env(sources)
        env["UV_CACHE_DIR"] = str(fd / "uv_cache")

        profile = detect_profile()
        _set_progress(0.05, f"下载方案：{profile.get('reason', '')}")

        # ── 第一步：建独立 venv 并装引擎依赖 ──
        with _LOCK:
            logf = install_log_path().open("w", encoding="utf-8")
        if not venv_python().exists():
            vproc = subprocess.Popen(  # noqa: S603
                _build_venv_command(),
                cwd=_project_root(),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                env=env,
                bufsize=1,
            )
            assert vproc.stdout is not None
            for line in vproc.stdout:
                logf.write(line)
            vrc = vproc.wait()
            if vrc != 0:
                raise RuntimeError(f"创建 venv 失败（退出码 {vrc}）。")

        _set_progress(0.15, "安装引擎依赖（insightface / onnxruntime / opencv / pyvirtualcam）…")
        irc = _run_pip_install(_build_pip_install_command(sources), env, logf)
        if irc != 0:
            # 镜像整站挂/拒绝（清华源对 uv 就 403）时兜底直连官方 PyPI 再试一次。
            _set_progress(0.15, "下载源安装失败，改连官方 PyPI 重试…")
            logf.write("\n[aipdd] 镜像源失败，回退官方 PyPI 重试\n")
            fallback_env = _pip_install_env(sources, direct_pypi=True)
            fallback_env["UV_CACHE_DIR"] = str(fd / "uv_cache")
            irc = _run_pip_install(
                _build_pip_install_command(sources, index_url=""),
                fallback_env,
                logf,
            )
        logf.close()
        if irc != 0:
            raise RuntimeError(
                f"依赖安装失败（退出码 {irc}）。镜像源与官方 PyPI 都没装上，"
                "多为网络/代理问题：可在下载源里换镜像、填代理，或清空 pip 镜像后重试。"
            )

        # ── 第二步：下载换脸模型（用户自助，权重不进包）──
        _set_progress(0.6, "下载换脸模型（inswapper + buffalo_l）…")

        def _overall(frac: float) -> None:
            _set_progress(0.6 + 0.39 * frac, "下载换脸模型…")

        ok_model, failed = model_download.download_required(
            str(models_dir()),
            hf_endpoint=sources.get("hf_endpoint"),
            https_proxy=sources.get("https_proxy"),
            overall_progress=_overall,
        )
        if not ok_model:
            raise RuntimeError(
                f"模型下载失败（{failed}）。请检查网络/HF 镜像，可在下载源里改 hf_endpoint 或填代理后重试。"
            )

        save_settings(
            {
                "install_state": STATE_READY,
                "install_progress": 1.0,
                "install_message": "换脸直播引擎与模型已就绪。",
                "installed_at": _now_iso(),
                "models_ready": True,
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
    """删除 venv 与模型缓存（换脸重插件，删干净以省磁盘）。安装中拒绝。"""
    global _INSTALL_THREAD
    with _LOCK:
        if _INSTALL_THREAD is not None and _INSTALL_THREAD.is_alive():
            return status_snapshot()
    stop_engine()
    with _LOCK:
        if venv_python().exists():
            shutil.rmtree(venv_dir(), ignore_errors=True)
        if models_dir().exists():
            shutil.rmtree(models_dir(), ignore_errors=True)
        save_settings(
            {
                "install_state": STATE_NOT_INSTALLED,
                "install_progress": 0.0,
                "install_message": "",
                "installed_at": "",
                "models_ready": False,
            }
        )
    return status_snapshot()
