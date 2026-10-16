# DramaClaw 换脸直播边车引擎 / FaceSwap Live Sidecar Engine

DramaClaw「换脸直播」功能的**本机引擎**。这是一个**独立进程、独立 venv、仅绑定
`127.0.0.1`** 的边车（sidecar），负责：

```
物理摄像头 → cv2 抓帧 → insightface 检测/对齐 + INSwapper 换脸 → pyvirtualcam 写系统虚拟摄像头
                                                                     → OBS / 直播软件直接读取该虚拟摄像头
                                     ▲
浏览器控制面板 ── localhost HTTP：上传人脸图 / 改参数 / 启停 ──┘   （视频永不经浏览器 / 主后端）
```

## 重要：许可与来源声明（AGPL 隔离）

- 本引擎为 **自研实现**，许可证 **Elastic-2.0**（与主项目一致），仅使用**宽松许可**依赖：
  insightface（MIT）、onnxruntime（MIT）、opencv（Apache-2.0）、pyvirtualcam（MIT）、
  fastapi/uvicorn（MIT）。换脸算法即 insightface 官方 `model_zoo.INSwapper.get()` 的调用。
- **不引用、不链接、不复制** 上游 [Deep-Live-Cam](https://github.com/hacksider/Deep-Live-Cam)
  的 **AGPL-3.0** 源码。上游项目仅作**行为参考**（数据流与参数语义），其代码不进本仓、不进本引擎。
- AGPL 传染被**进程边界**阻断：主后端 `src/novelvideo` 通过 **localhost HTTP** 控制本引擎，
  **零 `import`**；本引擎跑在运行时自助创建的独立 venv（`<STATE_DIR>/faceswap/venv`）里，不进主
  `pyproject.toml` / `uv.lock`。CI 依赖许可证门（`scripts/check_dependency_licenses.py`）扫描主
  环境，看不到本 venv，故不会因换脸依赖被 AGPL 阻断。

## 运行形态限定

需要**物理摄像头 + 系统级虚拟摄像头设备**，仅**桌面 / 本机**形态可用（Tauri 本机、可出网）。
Docker Web 容器无宿主摄像头、注册不了 vcam，此能力在该形态置灰。

各平台 vcam 前置：
- **Windows**：安装 OBS Studio（自带 OBS 虚拟摄像头驱动）。
- **macOS**：安装 OBS Studio（提供 `obs-mac-virtualcam` 设备）。
- **Linux**：加载 `v4l2loopback` 内核模块（如 `sudo modprobe v4l2loopback`）。

启动引擎后，在 OBS「视频捕获设备」里选择 **OBS Virtual Camera**（或 pyvirtualcam 对应设备）即可，
**不要**在 OBS 里选物理摄像头（那会被本引擎独占，属预期行为）。

## 手动运行（通常由主后端自动拉起，无需手动）

```bash
# 依赖装进独立 venv（等价于主后端的「下载并部署」）
python -m venv <STATE_DIR>/faceswap/venv
<STATE_DIR>/faceswap/venv/bin/pip install -r services/faceswap/requirements.txt

# 模型先落到 <STATE_DIR>/faceswap/models：inswapper_128_fp16.onnx 在根，buffalo_l 检测包必须
# 落在 <models>/models/buffalo_l（边车把 --models 目录当 insightface 的 root，它只认这一层）
<STATE_DIR>/faceswap/venv/bin/python services/faceswap/server.py \
    --port 18899 --models <STATE_DIR>/faceswap/models \
    [--camera 0] [--width 1280 --height 720 --fps 30] \
    [--provider cpu|coreml|cuda] [--det-size 128] [--vcam-backend auto|obs|unity|v4l2]
```

## 控制 API（仅 `127.0.0.1`，无任何视频流端点）

| 方法 | 路径 | 说明 |
|------|------|------|
| GET  | `/health` | 存活探测 + 当前状态快照 |
| GET  | `/state`  | running / fps / message / error / config |
| POST | `/config` | 改参数：`many_faces` `mouth_mask` `nsfw_filter` `face_enhancer` `live_mirror` `det_size` `provider` `vcam_backend` `source_image` |
| POST | `/face`   | multipart 上传人脸源图（存 `faces/source.*`，设为 `source_image`） |
| POST | `/start`  | 启动抓帧→换脸→写 vcam 工作线程 |
| POST | `/stop`   | 停止并让进程退出 |

主后端 `src/novelvideo/api/routes/faceswap.py` 在 `require_owner` 门禁下把浏览器控制面板的
REST 请求转发到上述端点。
