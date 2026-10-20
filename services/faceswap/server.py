#!/usr/bin/env python3
# SPDX-License-Identifier: Elastic-2.0
# Copyright (c) 2026 ClaymoreLab
"""[aipdd fork delta] 换脸直播边车引擎（独立进程 / 独立 venv / 仅本机控制）。

这是 DramaClaw「换脸直播」的本机引擎：**自研**实现，用宽松许可的库
（insightface=MIT、onnxruntime=MIT、opencv、pyvirtualcam=MIT、fastapi/uvicorn=MIT）
直接完成 摄像头抓帧 → inswapper 换脸 → 写系统虚拟摄像头，**不引用上游 Deep-Live-Cam 的
AGPL 源码**（算法即 insightface 官方 `model_zoo.INSwapper.get()` 调用）。

进程边界（AGPL 隔离 + Web 不推流）：

- 只绑定 ``127.0.0.1``，只暴露**控制类** HTTP 端点（上传人脸图 / 改参数 / 启停 / 取状态）。
- **不提供任何视频流端点**：视频路径是 摄像头 → 本进程 → pyvirtualcam 虚拟摄像头设备 →
  OBS/直播软件直接读取，全程不经过浏览器，也不经过 DramaClaw 主后端。
- 由主后端 ``novelvideo.faceswap.faceswap_install`` 以 ``faceswap/venv/bin/python
  services/faceswap/server.py --port <p> --models <dir>`` 拉起/监管。

用法（由系统自动调用，也可手动）::

    python server.py --port 18899 --models <STATE_DIR>/faceswap/models \
        [--camera 0] [--width 1280 --height 720 --fps 30] \
        [--provider cpu|coreml|cuda] [--det-size 128] [--vcam-backend auto|obs|unity|v4l2]
"""

from __future__ import annotations

import argparse
import threading
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Optional

import numpy as np

# ── 运行参数（控制面板可热改，除 source_image 外均即时生效）──────────────
@dataclass
class EngineConfig:
    source_image: Optional[str] = None  # 换脸源人脸图路径（上传后写入）
    camera_index: int = 0
    width: int = 1280
    height: int = 720
    fps: int = 30
    many_faces: bool = False
    mouth_mask: bool = False
    nsfw_filter: bool = False
    face_enhancer: bool = False
    live_mirror: bool = False
    det_size: int = 128
    provider: str = "cpu"
    vcam_backend: str = "auto"


@dataclass
class EngineState:
    running: bool = False
    fps: float = 0.0
    message: str = "idle"
    error: Optional[str] = None
    has_source: bool = False
    config: dict[str, Any] = field(default_factory=dict)


class FaceSwapEngine:
    """抓帧 → 换脸 → 写虚拟摄像头的工作线程 + 惰性加载的 insightface 模型。"""

    def __init__(self, models_dir: str, config: EngineConfig) -> None:
        self.models_dir = models_dir
        self.config = config
        self.state = EngineState()
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._app = None          # insightface FaceAnalysis
        self._swapper = None      # insightface INSwapper
        self._cap = None          # cv2.VideoCapture
        self._cam = None          # pyvirtualcam.Writer
        self._source_face = None

    # ── 生命周期 ──
    def start(self) -> None:
        with self._lock:
            if self._thread and self._thread.is_alive():
                return
            self._stop.clear()
            self.state.running = True
            self.state.error = None
            self.state.message = "starting…"
            self._thread = threading.Thread(target=self._run, name="faceswap-worker", daemon=True)
            self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        thread = self._thread
        if thread and thread.is_alive():
            thread.join(timeout=5)
        self._thread = None
        self.state.running = False
        self.state.message = "idle"
        self._release()

    def _release(self) -> None:
        for attr in ("_cap", "_cam"):
            obj = getattr(self, attr, None)
            if obj is not None:
                try:
                    obj.release() if attr == "_cap" else obj.close()
                except Exception:  # noqa: BLE001
                    pass
                setattr(self, attr, None)

    # ── 工作循环 ──
    def _run(self) -> None:
        try:
            self._load_models()
            self._open_capture()
            self._open_vcam()
            self.state.message = "running"
            self._loop()
        except Exception as exc:  # noqa: BLE001
            self.state.error = str(exc)
            self.state.message = "error"
        finally:
            self._release()

    def _load_models(self) -> None:
        import os

        import cv2  # noqa: F401  触发 opencv 可用性检查（抓帧/写帧都要它）

        from insightface.app import FaceAnalysis
        from insightface.model_zoo import get_model

        onnx = os.path.join(self.models_dir, "inswapper_128_fp16.onnx")
        if not os.path.isfile(onnx):
            onnx = os.path.join(self.models_dir, "inswapper_128.onnx")
        if not os.path.isfile(onnx):
            raise RuntimeError(
                f"未找到 inswapper 模型：{self.models_dir}。请先在 设置→环境安装 下载并部署。"
            )

        app = FaceAnalysis(
            name="buffalo_l",
            allowed_modules=["detection", "recognition"],
            root=self.models_dir,
            providers=self._providers(),
        )
        det = (self.config.det_size, self.config.det_size)
        app.prepare(ctx_id=0, det_size=det)
        self._app = app
        self._swapper = get_model(onnx, download=False, providers=self._providers())
        # buffalo_l 检测包（root/models/buffalo_l）须已随模型下载就位。

    def _providers(self) -> list[str]:
        cpu = "CPUExecutionProvider"
        prov = (self.config.provider or "cpu").lower()
        if prov == "coreml":
            return ["CoreMLExecutionProvider", cpu]
        if prov == "cuda":
            return ["CUDAExecutionProvider", cpu]
        return [cpu]

    def _open_capture(self) -> None:
        import cv2

        cap = cv2.VideoCapture(self.config.camera_index)
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.config.width)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.config.height)
        cap.set(cv2.CAP_PROP_FPS, self.config.fps)
        if not cap.isOpened():
            raise RuntimeError(
                f"无法打开摄像头 index={self.config.camera_index}（可能被占用或无权限）。"
            )
        self._cap = cap

    def _open_vcam(self) -> None:
        import pyvirtualcam

        backend = self.config.vcam_backend
        kwargs: dict[str, Any] = {
            "width": self.config.width,
            "height": self.config.height,
            "fps": self.config.fps,
            "pixel_format": "BGR",
        }
        if backend and backend != "auto":
            kwargs["backend"] = backend
        try:
            self._cam = pyvirtualcam.Writer(**kwargs)
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(
                f"无法创建系统虚拟摄像头（{exc}）。macOS 需先安装 OBS Studio 的虚拟摄像头、"
                "Windows 需 OBS 虚拟摄像头驱动、Linux 需 v4l2loopback。"
            ) from exc

    def _ensure_source_face(self) -> None:
        import cv2
        import os

        path = self.config.source_image
        if not path or not os.path.isfile(path):
            self._source_face = None
            return
        img = cv2.imread(path)
        if img is None:
            self._source_face = None
            return
        faces = self._app.get(img)
        self._source_face = max(faces, key=lambda f: f.det_score) if faces else None

    def _loop(self) -> None:
        cv2 = _cv2()
        interval = 1.0 / max(1, self.config.fps)
        frame_counter = 0
        t_last = time.perf_counter()

        last_source_sig = None
        while not self._stop.is_set():
            cfg = self.config
            sig = (cfg.source_image, cfg.det_size, cfg.provider)
            if sig != last_source_sig:
                self._ensure_source_face()
                last_source_sig = sig

            ok, frame = self._cap.read()
            if not ok or frame is None:
                time.sleep(interval)
                continue

            if cfg.live_mirror:
                frame = cv2.flip(frame, 1)

            if self._source_face is not None and self._swapper is not None:
                frame = self._swap_one_frame(frame)

            if self._cam is not None:
                self._cam.send(np.ascontiguousarray(frame))

            frame_counter += 1
            now = time.perf_counter()
            if now - t_last >= 1.0:
                self.state.fps = frame_counter / (now - t_last)
                frame_counter = 0
                t_last = now

            # 让出 CPU，贴近目标帧率。
            time.sleep(max(0.0, interval - 0.001))

    def _swap_one_frame(self, frame):
        faces = self._app.get(frame)
        if not faces:
            return frame
        ordered = sorted(faces, key=lambda f: f.det_score, reverse=True)
        targets = ordered if self.config.many_faces else ordered[:1]
        result = frame
        for target in targets:
            try:
                result = self._swapper.get(result, target, self._source_face, paste_back=True)
            except Exception as exc:  # noqa: BLE001
                self.state.message = f"swap error: {exc}"
        return result


def _cv2():
    import cv2

    return cv2


# ── HTTP 控制服务（仅 127.0.0.1，无视频端点）─────────────────────────────
def build_app(engine: FaceSwapEngine):
    import os

    from fastapi import FastAPI, File, UploadFile
    from fastapi.responses import JSONResponse

    app = FastAPI(title="DramaClaw FaceSwap Engine", docs_url=None, redoc_url=None)

    def _snapshot() -> dict[str, Any]:
        engine.state.has_source = bool(engine.config.source_image)
        engine.state.config = asdict(engine.config)
        return asdict(engine.state)

    @app.get("/health")
    def health() -> JSONResponse:
        return JSONResponse({"ok": True, "data": _snapshot()})

    @app.get("/state")
    def state() -> JSONResponse:
        return JSONResponse({"ok": True, "data": _snapshot()})

    @app.post("/config")
    def config(body: dict) -> JSONResponse:
        cfg = engine.config
        # 布尔开关：只接受真正的 bool，避免把 provider 误设为字符串/把开关设成字符串。
        for key in (
            "many_faces", "mouth_mask", "nsfw_filter",
            "face_enhancer", "live_mirror",
        ):
            if key in body and isinstance(body[key], bool):
                setattr(cfg, key, body[key])
        # 字符串枚举：provider / vcam_backend 只接受非空字符串。
        for key in ("provider", "vcam_backend"):
            if key in body and isinstance(body[key], str) and body[key]:
                setattr(cfg, key, body[key])
        if "det_size" in body:
            try:
                cfg.det_size = max(64, min(640, int(body["det_size"])))
            except (TypeError, ValueError):
                pass
        if "source_image" in body:
            cfg.source_image = str(body["source_image"]) or None
        return JSONResponse({"ok": True, "data": _snapshot()})

    @app.post("/face")
    async def face(file: UploadFile = File(...)) -> JSONResponse:
        faces_dir = os.path.join(engine.models_dir, "..", "faces")
        os.makedirs(faces_dir, exist_ok=True)
        ext = os.path.splitext(file.filename or ".png")[1] or ".png"
        dest = os.path.join(os.path.abspath(faces_dir), "source" + ext)
        data = await file.read()
        if not data:
            return JSONResponse({"ok": False, "error": "empty upload"}, status_code=400)
        with open(dest, "wb") as fh:
            fh.write(data)
        engine.config.source_image = dest
        return JSONResponse({"ok": True, "data": {"path": dest, "state": _snapshot()}})

    @app.post("/start")
    def start() -> JSONResponse:
        engine.start()
        return JSONResponse({"ok": True, "data": _snapshot()})

    @app.post("/stop")
    def stop() -> JSONResponse:
        engine.stop()
        # 停完让 uvicorn 也退出（进程随之结束，主后端据 /health 探死）。
        threading.Thread(target=_shutdown_later, daemon=True).start()
        return JSONResponse({"ok": True, "data": _snapshot()})

    return app


def _shutdown_later() -> None:
    import signal

    time.sleep(0.5)
    import os

    os.kill(os.getpid(), signal.SIGTERM)


def main() -> None:
    parser = argparse.ArgumentParser(description="DramaClaw FaceSwap live engine (local sidecar)")
    parser.add_argument("--port", type=int, default=18899)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--models", required=True, help="换脸模型目录（含 inswapper + buffalo_l）")
    parser.add_argument("--camera", type=int, default=0)
    parser.add_argument("--width", type=int, default=1280)
    parser.add_argument("--height", type=int, default=720)
    parser.add_argument("--fps", type=int, default=30)
    parser.add_argument("--provider", default="cpu")
    parser.add_argument("--det-size", type=int, default=128)
    parser.add_argument("--vcam-backend", default="auto")
    args = parser.parse_args()

    config = EngineConfig(
        camera_index=args.camera,
        width=args.width,
        height=args.height,
        fps=args.fps,
        provider=args.provider,
        det_size=args.det_size,
        vcam_backend=args.vcam_backend,
    )
    engine = FaceSwapEngine(args.models, config)

    import uvicorn

    app = build_app(engine)
    # 仅绑定本机回环，绝不对外提供网络可达的服务或视频流。
    uvicorn.run(app, host=args.host if args.host in {"127.0.0.1", "localhost"} else "127.0.0.1",
                port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
