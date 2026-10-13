---
version: 2.1.7
attention: low
---
# v2.1.7

## User-facing Highlights (zh)

- **口播智能剪辑上线**: 新增「口播智能剪辑」工具，上传一条口播视频即可自动跑多阶段流水线——本地静音/停顿裁剪 + 网关 ASR 文本瑕疵识别裁剪，直接产出精剪成片，全程本地渲染。
- **环境安装更可操作**: 「设置 → 环境安装」检测到缺失依赖时，点「安装 / 重装」会复制一条可直接粘进终端运行的命令（Mac「终端」/ Windows PowerShell 分别提示），并新增「部署前提：Docker / Colima」说明与《安装环境-Setup》脚本入口。
- **初始化可自定义管理员用户名**: 配置本地模型网关时不再写死 root，可自定义管理员用户名。

## User-facing Highlights (en)

- **Narration smart-edit tool**: A new tool turns one talking-head video into a polished cut through a multi-stage pipeline — local silence/pause trimming plus gateway-ASR verbal-filler trimming, rendered locally end to end.
- **More actionable environment setup**: When a runtime dependency is missing, Install / Reinstall now copies a terminal-ready command (Mac Terminal / Windows PowerShell aware), and a new "Deployment prerequisite: Docker / Colima" note points to the 安装环境-Setup script.
- **Custom admin username at init**: Configuring the local model gateway no longer hardcodes root.

## Fixes

- 本地网关报错文案统一称 LocalNewAPI（不再出现 newapi 措辞），并说明网关随系统部署自动提供、无需手动填写管理地址。
- 「设置 → 环境安装」安装提示由裸容器命令升级为含 Docker 前提的终端命令，按客户端平台给出对应终端名称。
