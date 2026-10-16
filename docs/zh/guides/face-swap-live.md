<!-- lang-switch -->
[English](../../en/guides/face-swap-live.md) · **简体中文**

# 换脸直播指南

> DramaClaw「换脸直播」用本机引擎把**物理摄像头**里的人脸实时替换成你上传的目标人脸，
> 并输出到一个**系统级虚拟摄像头**，OBS / 直播软件直接读取那个虚拟摄像头即可。
> 浏览器端只是一个**控制面板**——上传人脸图、切换参数、启停，**视频不经过浏览器**。

## 数据流

```
物理摄像头 → [本机边车引擎] 抓帧 → 人脸检测/对齐 + inswapper 换脸 → 写入系统虚拟摄像头
                                                                      → OBS / 直播软件读取
                            ▲
浏览器控制面板 ── localhost HTTP：上传人脸图 / 改参数 / 启停 ──┘   （视频永不经浏览器 / 主后端）
```

## 运行形态限定（重要）

本功能需要**物理摄像头 + 系统级虚拟摄像头设备**，仅**桌面 / 本机**形态可用（Tauri 本机、可出网）。
**Docker Web 容器无宿主摄像头、也注册不了虚拟摄像头**，此能力在该形态会在设置页/工具面板置灰，
仅保留状态查看。

## 各平台虚拟摄像头前置

引擎用 `pyvirtualcam` 写系统虚拟摄像头，需先具备对应设备：

| 平台 | 前置 |
|---|---|
| **Windows** | 安装 OBS Studio（自带「OBS Virtual Camera」驱动） |
| **macOS** | 安装 OBS Studio（提供 `obs-mac-virtualcam` 设备） |
| **Linux** | 加载 `v4l2loopback` 内核模块，如 `sudo modprobe v4l2loopback` |

设置页会自动探测虚拟摄像头是否可用；不可用时给出提示且「启动」按钮禁用。

## 桌面安装包形态

- 安装包**内置边车引擎本体**（`resources/app/services/faceswap/`：`server.py` + `requirements.txt`）
  与**一份 `uv`**（`resources/bin/uv.exe` / `bin/uv`）——「下载并部署」就是用它在你机器的
  **状态目录**里建独立 venv 并装依赖，所以**不需要**你另外安装 uv 或 python。
- 依赖与模型仍然**不进安装包**：运行时才下载到 `<状态目录>/faceswap/{venv,models}`（见下一节）。
- 需要能出网（PyPI + HuggingFace 镜像）；公司网络受限可在设置页改 pip 镜像 / HF 端点 / 代理。
- Windows 桌面版发布前未做代码签名，首次安装会有一次 SmartScreen「仍要运行」提示。

## 一、在「设置 → 环境安装」部署引擎（仿 3D 世界的自助下载）

换脸引擎依赖与模型**不进主依赖、不进安装包**，与 3D 世界一样按需自助下载：

1. 打开 **设置 → 环境安装 → 换脸直播**。
2. （可选）配置下载源：pip 镜像 / HuggingFace 端点（默认 `hf-mirror`）/ 代理。
3. 打开「启用」开关，点「下载并部署」——后台会在**独立 venv**（`<状态目录>/faceswap/venv`）
   安装宽松许可依赖（insightface 1.0.1 / onnxruntime / opencv / pyvirtualcam / fastapi），并从
   HuggingFace 下载换脸模型：`inswapper_128_fp16.onnx` 落在 `faceswap/models`，`buffalo_l`
   检测包按其识别布局落在 `faceswap/models/models/buffalo_l`。
   进度与日志尾部在该卡片内展示；支持「重新部署」与「卸载」（删 venv + 模型省磁盘）。
   pip 镜像只做主索引，**装不上会自动改连官方 PyPI 重试**（清华源对 uv 会整站 403，
   只认镜像时一颗都装不上），无需你手动换源。
4. 页面会显示硬件探测建议的执行后端（CPU / CoreML / CUDA）。

## 二、在「工具 → 换脸直播」控制面板操作

部署就绪、且本机形态、虚拟摄像头可用时，「工具」里的**换脸直播**入口才可操作：

1. 点「启动换脸」——主后端拉起边车进程（抓摄像头 → 换脸 → 写虚拟摄像头）。
2. 「上传人脸图」：选一张清晰正脸图作为换脸源（仅本机保存，作为 inswapper 的源脸）。
3. 按需切换参数：`换所有人脸` / `保留原嘴部` / `NSFW 过滤` / `面部增强` / `镜像显示` /
   检测尺寸 / 推理后端；面板显示运行状态与实时帧率。
4. 点「停止换脸」关闭引擎。

## 三、在 OBS 读取

启动后，在 OBS「来源 → 视频捕获设备」里选择 **OBS Virtual Camera**（或 pyvirtualcam 对应设备），
即可拿到换脸后的画面。**不要**在 OBS 里选物理摄像头——它已被本机引擎独占（属预期行为）。

## 许可与合规（务必阅读）

- 本机边车引擎为 DramaClaw **自研**，许可证 **Elastic-2.0**，仅使用宽松许可依赖
  （insightface=MIT、onnxruntime=MIT、opencv=Apache-2.0、pyvirtualcam=MIT、
  fastapi/uvicorn=MIT）。
- 上游 [Deep-Live-Cam](https://github.com/hacksider/Deep-Live-Cam) 为 **AGPL-3.0**，本项目
  **不链接、不内嵌、不复制** 其任何源码，仅作行为参考；主后端只经 **127.0.0.1 HTTP** 控制边车、
  **零 import**，AGPL 传染被进程边界阻断，不波及 Elastic-2.0 分发（详见 `services/faceswap/NOTICE`
  与根 `NOTICE` 的「FaceSwap Live」段）。
- 换脸**模型权重**由用户运行时自行下载、不随包分发；insightface / inswapper 权重多为
  **研究 / 非商用**许可，请自行确认并承担相应许可义务。
- 使用换脸/深度合成技术须遵守当地法律，**不得**用于欺骗、侵犯他人肖像权与隐私；请对被采集者
  尽到告知与同意义务。

## 故障排查

- **打不开摄像头**：设备可能被其它程序占用或权限不足；关闭占用程序，或在系统隐私设置里授权。
- **虚拟摄像头不可用**：按上表装好 OBS 虚拟摄像头 / `v4l2loopback` 后重试；设置页的探测结果会更新。
- **帧率低**：CPU 推理较慢，优先选 GPU（CoreML / CUDA），并把检测尺寸调小（如 128/160）。
- **装依赖 / 下模型失败**：需可出网；镜像源失败会自动回退官方 PyPI 再试一次，两趟都失败才报错，
  此时可在下载源里换镜像、填代理，或清空 pip 镜像。模型侧默认走 `hf-mirror`，不可达就在下载源
  里把 HF 端点改成 `https://huggingface.co`（与 3D 世界同源约束）。
