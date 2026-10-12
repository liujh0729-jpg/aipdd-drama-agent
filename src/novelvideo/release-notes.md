---
version: 2.1.6
attention: low
---
# v2.1.6

## User-facing Highlights (zh)

- **macOS 桌面客户端上线**: 新增 AIPDD DramaClaw 原生 macOS 应用（Apple 芯片），内置后端与全部服务，双击即用，无需手动配置 Docker/环境。
- **Windows 安装更可靠**: 修复已安装 WSL 2 却被误判为「不可用」的问题，安装脚本能正确识别本机 WSL 2，减少首次部署受阻。
- **全新品牌标识**: 桌面应用图标、网页登录页与浏览器标签页统一为 AIPDD 品牌 logo。

## User-facing Highlights (en)

- **macOS desktop app**: AIPDD DramaClaw now ships as a native macOS app (Apple silicon) with the backend and all services bundled — double-click to launch, no manual Docker or environment setup.
- **More reliable Windows install**: Fixed WSL 2 being falsely reported as unavailable on machines that already have it; the installer now detects WSL 2 correctly.
- **New brand identity**: The desktop app icon, web login page and browser tab now share the AIPDD logo.

## Fixes

- 修复 Windows 安装脚本 `Get-WslVersion` 的假阴性：wsl.exe 默认输出 UTF-16LE，被按 UTF-8 控制台编码误解码，导致已装 WSL 2 被判为不可用。现按 Unicode 与 UTF-8 各解码一次再判定。
- 修复 macOS 桌面运行期 nginx 因配置路径含空格未加引号而启动失败，以及网关目录未预建导致服务链路卡住的问题。

## Improvements

- macOS 应用图标按 Apple 图标栅格加圆角与四周留白，Dock 呈现更规范。
- 品牌资产统一由 `scripts/gen_brand_assets.py` 从单一源图派生，改 logo 一处即可重生成桌面图标与前端 favicon/登录字标。
