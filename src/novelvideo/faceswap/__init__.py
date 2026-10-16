# SPDX-License-Identifier: Elastic-2.0
# Copyright (c) 2026 ClaymoreLab
"""[aipdd fork delta] 换脸直播（faceswap live）本机能力编排。

设计约束（见「换脸直播融合方案」计划）：

- **AGPL 隔离**：上游 Deep-Live-Cam 是 AGPL-3.0，绝不可 link/import 进 Elastic-2.0
  的 ``src/novelvideo``。这里只做「下载部署 + 进程监管 + 状态」，边车引擎是 ``services/
  faceswap`` 下**我们自研**的独立进程（用宽松许可的 insightface / onnxruntime / opencv /
  pyvirtualcam 直接实现抓帧→换脸→写虚拟摄像头），零引用上游源码。
- **环境管理自助下载**：依赖与换脸模型都**不进镜像/不进包**，用户在「设置 → 环境安装」
  点「下载并部署」时后台装到持久卷上的独立 venv、下到 ``<STATE_DIR>/faceswap/models``，
  与 3D 世界（:mod:`novelvideo.director_world.world_install`）同一套范式。
- **仅桌面/本机形态可用**：需要物理摄像头 + 系统级虚拟摄像头设备。

真正的安装编排、状态机、venv/模型解析与边车进程生命周期都在
:mod:`novelvideo.faceswap.faceswap_install`。
"""

from . import faceswap_install, model_download

__all__ = ["faceswap_install", "model_download"]
