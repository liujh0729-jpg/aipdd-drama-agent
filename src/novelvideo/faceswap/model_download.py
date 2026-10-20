# SPDX-License-Identifier: Elastic-2.0
# Copyright (c) 2026 ClaymoreLab
"""[aipdd fork delta] 换脸模型运行时下载器（用户自助，权重不进包）。

从 HuggingFace ``hacksider/deep-live-cam`` 仓库拉取换脸/增强/人脸检测所需 ONNX，落到
``<STATE_DIR>/faceswap/models``，支持断点续传（``.part``）、期望大小校验、进度回调、
可覆盖镜像端点（默认 hf-mirror）与代理，且始终用 certifi CA 校验证书。

注意：模型权重的许可由上游/用户承担（insightface / inswapper 多为研究或非商用）。本系统
只在用户明确点「下载」时代理其自助下载，不在软件包内置这些权重。
"""

from __future__ import annotations

import os
import ssl
import threading
import urllib.error
import urllib.request
from typing import Callable, Optional

ProgressCb = Callable[[str, int, int], None]

HF_REPO_ID = "hacksider/deep-live-cam"
HF_RESOLVE_PATH = f"https://huggingface.co/{HF_REPO_ID}/resolve/main/"
HF_MIRROR_RESOLVE_PATH = f"https://hf-mirror.com/{HF_REPO_ID}/resolve/main/"

# 换脸直播所需的最小模型集：inswapper 换脸核 + buffalo_l 人脸检测/对齐包（5 个 onnx）。
# 增强器（gfpgan / GPEN）可选，默认不强制下载，用户开启增强时再取。
#
# 这里的名字是**落盘相对路径**，不是仓库路径：边车用
# ``FaceAnalysis(root=<models_dir>)`` 加载，insightface 只认 ``<root>/models/<pack>/*.onnx``
# 这一层布局。原先直接落到 ``<models_dir>/buffalo_l`` 时它找不到，会静默地去 GitHub 再下一份。
REQUIRED_MODELS: tuple[str, ...] = (
    "inswapper_128_fp16.onnx",
    "models/buffalo_l/1k3d68.onnx",
    "models/buffalo_l/2d106det.onnx",
    "models/buffalo_l/det_10g.onnx",
    "models/buffalo_l/genderage.onnx",
    "models/buffalo_l/w600k_r50.onnx",
)

OPTIONAL_MODELS: tuple[str, ...] = (
    "gfpgan-1024.onnx",
    "GPEN-BFR-256.onnx",
    "GPEN-BFR-512.onnx",
)

# 上游 hacksider/deep-live-cam 仓库里 buffalo_l 套了两层同名目录
# （``buffalo_l/buffalo_l/*.onnx``），照落盘名直取会 404。
_LOCAL_TO_REPO_PREFIX: dict[str, str] = {
    "models/buffalo_l/": "buffalo_l/buffalo_l/",
}

MODEL_SIZES: dict[str, int] = {
    "inswapper_128.onnx": 554253681,
    "inswapper_128_fp16.onnx": 277680638,
    "gfpgan-1024.onnx": 365875079,
    "GPEN-BFR-256.onnx": 75715262,
    "GPEN-BFR-512.onnx": 284244491,
    "models/buffalo_l/1k3d68.onnx": 143607619,
    "models/buffalo_l/2d106det.onnx": 5030888,
    "models/buffalo_l/det_10g.onnx": 16923827,
    "models/buffalo_l/genderage.onnx": 1322532,
    "models/buffalo_l/w600k_r50.onnx": 174383860,
}

_CHUNK = 1024 * 256
_LOCKS: dict[str, threading.Lock] = {}
_LOCKS_GUARD = threading.Lock()


def _lock_for(name: str) -> threading.Lock:
    with _LOCKS_GUARD:
        lock = _LOCKS.get(name)
        if lock is None:
            lock = threading.Lock()
            _LOCKS[name] = lock
        return lock


def _ssl_context() -> ssl.SSLContext:
    """下载用的 TLS 上下文：始终用 certifi CA 校验（对齐上游修复 #1890 的口径）。"""
    try:
        import certifi

        return ssl.create_default_context(cafile=certifi.where())
    except Exception:  # noqa: BLE001 - certifi 缺失时退回系统默认校验，不关校验
        return ssl.create_default_context()


def remote_path(name: str) -> str:
    """落盘相对名 → 上游仓库内的路径（buffalo_l 在仓库里多套了一层同名目录）。"""
    rel = name.replace(os.sep, "/").lstrip("/")
    for local_prefix, repo_prefix in _LOCAL_TO_REPO_PREFIX.items():
        if rel.startswith(local_prefix):
            return repo_prefix + rel[len(local_prefix) :]
    return rel


def resolve_url(name: str, hf_endpoint: Optional[str] = None) -> str:
    """按（可覆盖的）HF 端点拼下载地址；默认走 hf-mirror 以适配国内可达。"""
    rel = remote_path(name)
    endpoint = (hf_endpoint or "").strip().rstrip("/")
    if endpoint:
        if endpoint.endswith(".com") and "hf-mirror" in endpoint:
            base = f"{endpoint}/{HF_REPO_ID}/resolve/main/"
        elif "huggingface.co" in endpoint:
            base = f"{endpoint}/{HF_REPO_ID}/resolve/main/"
        else:
            base = HF_MIRROR_RESOLVE_PATH
        return base + rel
    return HF_MIRROR_RESOLVE_PATH + rel


def local_path(name: str, models_dir: str) -> str:
    return os.path.join(models_dir, *name.replace("/", os.sep).split(os.sep))


def expected_size(name: str) -> Optional[int]:
    return MODEL_SIZES.get(name.replace(os.sep, "/"))


def is_present(name: str, models_dir: str) -> bool:
    path = local_path(name, models_dir)
    if not os.path.isfile(path):
        return False
    size = expected_size(name)
    actual = os.path.getsize(path)
    # 有期望大小时据此判定完整性，否则只要非空即视为就绪。
    return actual > 0 and (size is None or actual == size)


def required_present(models_dir: str) -> bool:
    return all(is_present(name, models_dir) for name in REQUIRED_MODELS)


def _apply_proxy(https_proxy: Optional[str]) -> None:
    if https_proxy:
        os.environ.setdefault("HTTPS_PROXY", https_proxy)
        os.environ.setdefault("HTTP_PROXY", https_proxy)


def download_one(
    name: str,
    models_dir: str,
    *,
    hf_endpoint: Optional[str] = None,
    https_proxy: Optional[str] = None,
    progress: Optional[ProgressCb] = None,
) -> bool:
    """下载单个模型（断点续传 + 大小校验）。成功或被判定已就绪返回 True。"""
    _apply_proxy(https_proxy)
    target = local_path(name, models_dir)
    if is_present(name, models_dir):
        if progress:
            progress(name, 1, 1)
        return True

    url = resolve_url(name, hf_endpoint)
    expected = expected_size(name)
    os.makedirs(os.path.dirname(target) or models_dir, exist_ok=True)
    partial = target + ".part"
    resume_from = os.path.getsize(partial) if os.path.isfile(partial) else 0

    headers = {"User-Agent": "DramaClaw-FaceSwap"}
    if resume_from:
        headers["Range"] = f"bytes={resume_from}-"

    request = urllib.request.Request(url, headers=headers)
    ctx = _ssl_context()
    mode = "ab" if resume_from else "wb"
    try:
        with urllib.request.urlopen(request, timeout=60, context=ctx) as resp:  # noqa: S310
            total = expected
            content_len = resp.headers.get("Content-Length")
            if content_len:
                total = resume_from + int(content_len)
            downloaded = resume_from
            with open(partial, mode) as fh:
                while True:
                    chunk = resp.read(_CHUNK)
                    if not chunk:
                        break
                    fh.write(chunk)
                    downloaded += len(chunk)
                    if progress:
                        progress(name, downloaded, total or 0)
    except (urllib.error.URLError, OSError, ValueError):
        # 网络中断：保留 .part 以便下次续传，返回失败让上层记录。
        return False

    if expected and os.path.getsize(partial) != expected:
        # 大小不符：删掉重下（多半是被镜像截断或 206 未生效）。
        try:
            os.remove(partial)
        except OSError:
            pass
        return False

    os.replace(partial, target)
    return True


def download_required(
    models_dir: str,
    *,
    hf_endpoint: Optional[str] = None,
    https_proxy: Optional[str] = None,
    should_cancel: Optional[Callable[[], bool]] = None,
    overall_progress: Optional[Callable[[float], None]] = None,
) -> tuple[bool, Optional[str]]:
    """依次下载全部必需模型；返回 (是否全部就绪, 失败模型名)。"""
    names = list(REQUIRED_MODELS)
    per_weights = [max(1, expected_size(n) or 1) for n in names]
    total_bytes = sum(per_weights)
    done_bytes = 0
    for idx, name in enumerate(names):
        if should_cancel and should_cancel():
            return False, name
        lock = _lock_for(name)
        with lock:
            before = done_bytes
            marker = {"max_seen": 0}

            def _cb(_n: str, cur: int, _tot: int, _idx: int = idx) -> None:
                nonlocal done_bytes
                advanced = max(0, cur - marker["max_seen"])
                if advanced:
                    marker["max_seen"] = cur
                    done_bytes = before + min(cur, per_weights[_idx])
                    if overall_progress and total_bytes:
                        overall_progress(min(0.99, done_bytes / total_bytes))

            ok = download_one(
                name,
                models_dir,
                hf_endpoint=hf_endpoint,
                https_proxy=https_proxy,
                progress=_cb,
            )
        if not ok:
            return False, name
    if overall_progress:
        overall_progress(1.0)
    return True, None
