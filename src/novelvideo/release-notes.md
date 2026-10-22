---
version: 2.3.1
attention: low
---
# v2.3.1

## User-facing Highlights (zh)

- **动效大片支持视频预览**: 工具「动效大片」与案例图册内 37 条案例新增循环预览片，悬停或选中即可看到真实动效，不再只有静态海报。
- **启动卡不再卡 180 秒**: 本地服务按各自预算等待就绪（nginx 25s、网关/剪辑 90s、FastAPI 180s）；进程提前退出会立即报错，并附退出码与最近日志（nginx 另含本轮 `error.log` 新增内容）。nginx 端口冲突会自动换到 8081–8089 重试，失败页提供「重试启动」，不必重装。
- **设置页「初始化 LocalNewAPI」不再报「管理地址尚未就绪」**: 桌面应用现在正确注入网关管理地址，本地部署场景下点击初始化即可连通，无需手动填写。
- **安装向导可选安装 Docker Desktop**: Windows 安装向导在「选择安装位置」之后新增「可选组件」页，默认勾选安装 Docker Desktop（约 650 MB，需管理员权限，装完需重启）。检测到已安装时勾选项置灰并显示「已检测到 Docker Desktop，将跳过安装」，不重复安装；页面附 Docker 订阅服务协议链接。安装包从 Docker 官方地址下载并校验 Docker Inc. 数字签名后才提权运行；跳过或安装失败都不影响主程序安装。静默、被动与 /UPDATE 升级安装会跳过 Docker 可选组件。
- **安装位置可选其他盘**: Windows 安装向导提供「选择安装位置」页，程序与随包环境（便携 Python / Chromium / nginx 等，约 2.4GB）可装到 D/E 盘，不再默认挤占 C 盘。
- **数据根可迁移**: 主窗口菜单「应用 → 服务与存储…」打开桌面服务窗口，在「存储位置」里可把模型、成片、日志、状态等运行期数据整体迁到其他磁盘；逐文件校验后再切换，失败自动回退旧位置。
- **卸载入口**: 安装包自带卸载程序；桌面服务窗口提供「卸载应用」（Windows），二次确认后拉起同一份卸载器。卸载默认保留运行期数据，避免误删模型与成片。

## User-facing Highlights (en)

- **Motion previews**: The Motion Feature Film tool and its gallery now include looping preview clips for 37 cases, so users can see the actual motion on hover or selection instead of a static poster.
- **No more 180-second startup stall**: Local services now wait within per-service budgets (nginx 25s, gateway/editor 90s, FastAPI 180s). A child process that exits early fails immediately with its exit code and recent logs (including new nginx `error.log` lines from the current run). Port conflicts make nginx retry on 8081-8089, and the error screen offers a Retry startup action, so users do not have to reinstall.
- **LocalNewAPI init no longer reports "admin address not ready"**: The desktop app now injects the gateway admin URL correctly, so clicking Initialize in Settings connects in local deployments without manual address entry.
- **Optional Docker Desktop in the Windows installer**: A new Optional Components page after the install-directory step offers Docker Desktop (about 650 MB, administrator rights, reboot required), checked by default. If Docker Desktop is already installed, the option is greyed out and shows "Docker Desktop detected - installation will be skipped". The page links to Docker's Subscription Service Agreement. The package is downloaded from Docker's official server and its Docker Inc. signature is verified before elevation. Skipping or a failed Docker install never blocks the main application; silent, passive and /UPDATE installs skip this component.
- **Choose another drive at install time**: The Windows installer now exposes a directory page, so the app and bundled runtime (portable Python / Chromium / nginx, about 2.4GB) can be installed on D/E instead of C.
- **Relocatable data root**: The desktop console (Application -> Services & Storage...) can move models, renders, logs and state to another disk, with per-file verification before switching and automatic fallback to the old root on failure.
- **Uninstall entry**: The installer bundles an uninstaller, and the desktop console adds an in-app Uninstall App action (Windows) that launches it after confirmation. Runtime data is preserved by default.

## Notes

- 本轮更新包含桌面启动修复、动效预览素材、管理员初始化修复与 LocalNewAPI 管理地址注入修复，Windows x64 安装包已重新构建；macOS dmg 仍需在 Mac 构建机上重新出包后另行补发。

## Fixes

- 修复首次初始化界面只能按回车、点击「下一步」无反应的问题。
- 修复本地服务启动失败时固定干等 180 秒、且错误详情为空的问题：改为按服务预算等待、进程提前退出立即报错，并附带退出码与最近日志；nginx 启动失败自动换端口重试，启动卡新增「重试启动」。
- 修复设置页「初始化 LocalNewAPI」报「管理地址尚未就绪」的问题：桌面应用 `env.rs` 补注入 `NEWAPI_ADMIN_BASE_URL`，本地网关随桌面编排一并提供，无需手工填写。Windows exe 与 macOS dmg 共用同一段 Rust，两端同步修复。
- 桌面壳新增主窗口原生菜单「服务与存储…」：诊断窗口不再只在启动失败时可达，存储迁移与卸载入口随时可用。
- 数据迁移在停服后、服务启动前执行；目标目录必须为空或带本项目标记，不会覆盖无关文件。
- 自定义数据盘不可用时阻断启动并给出「重新检测 / 改用默认位置」，不会静默在 C 盘新建数据。
