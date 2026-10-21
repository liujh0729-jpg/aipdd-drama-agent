---
version: 2.3.1
attention: medium
---
# v2.3.1

## User-facing Highlights (zh)

- **安装位置可选其他盘**: Windows 安装向导提供「选择安装位置」页，程序与随包环境（便携 Python / Chromium / nginx 等，约 2.4GB）可装到 D/E 盘，不再默认挤占 C 盘。
- **数据根可迁移**: 主窗口菜单「应用 → 服务与存储…」打开桌面服务窗口，在「存储位置」里可把模型、成片、日志、状态等运行期数据整体迁到其他磁盘；逐文件校验后再切换，失败自动回退旧位置。
- **卸载入口**: 安装包自带卸载程序；桌面服务窗口提供「卸载应用」（Windows），二次确认后拉起同一份卸载器。卸载默认保留运行期数据，避免误删模型与成片。

## User-facing Highlights (en)

- **Choose another drive at install time**: The Windows installer now exposes a directory page, so the app and bundled runtime (portable Python / Chromium / nginx, about 2.4GB) can be installed on D/E instead of C.
- **Relocatable data root**: The desktop console (Application -> Services & Storage...) can move models, renders, logs and state to another disk, with per-file verification before switching and automatic fallback to the old root on failure.
- **Uninstall entry**: The installer bundles an uninstaller, and the desktop console adds an in-app Uninstall App action (Windows) that launches it after confirmation. Runtime data is preserved by default.

## Fixes

- 桌面壳新增主窗口原生菜单「服务与存储…」：诊断窗口不再只在启动失败时可达，存储迁移与卸载入口随时可用。
- 数据迁移在停服后、服务启动前执行；目标目录必须为空或带本项目标记，不会覆盖无关文件。
- 自定义数据盘不可用时阻断启动并给出「重新检测 / 改用默认位置」，不会静默在 C 盘新建数据。
