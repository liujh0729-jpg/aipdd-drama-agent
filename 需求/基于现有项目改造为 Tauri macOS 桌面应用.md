## 1. 项目背景

现有一套 Web 业务系统：React/Vite 前端 + 多服务后端流水线，当前基于 `Colima + docker-compose` 本地运行。

- 前端：React/Vite，接口采用相对路径 `/api/v1`、`/gateway/v1`、`/static`，**不能独立作为静态站点运行，依赖本地后端服务**；前端业务代码保持零改动。
- 后端服务栈：Python FastAPI（Torch 推理、FFmpeg、Chrome CDP）、Go newapi 网关、nginx 反代、vectcut 独立服务。
- 方案选型：**放弃 Docker/Colima 容器**，采用**便携 Python 目录方案**；Tauri 作为桌面壳 + 多进程编排管理器。

> 
> 用户体验目标：下载 DMG 拖入应用程序 / 运行 EXE 安装向导，打开软件即可使用；**用户无需手动安装 Python、Docker、Colima、任何系统依赖**。

## 2. 总体架构

1. Tauri App（主程序）
   - Rust 层：进程编排、启停顺序控制、端口探活、日志采集、进程保活、异常捕获、环境变量注入；
   - Tauri 窗口加载 `http://127.0.0.1:8080`，直接复用原有 React 前端；
   - 资源目录内置：`python-build-standalone`便携 Python、Go 静态二进制、nginx 二进制、ffmpeg、Chromium。
2. 后端服务（业务逻辑代码尽量不动，仅路径适配）
   - FastAPI：使用内置便携 Python 解释器启动；`requirements.txt`依赖清单保持不变，打包阶段预先离线安装全部依赖；
   - newapi、vectcut：Go 源码不变，静态编译为单二进制；
   - nginx：原生二进制 + 原有 nginx.conf，仅修改路径配置；
   - 所有服务读写路径改为读取环境变量，指向 Tauri `app-data`目录（模型、缓存、临时文件）。
3. 模型资源策略：**模型文件不打包进 DMG/EXE 主安装包**，软件首次启动后，由 Tauri 内置下载器异步下载模型至`app-data`，控制安装包体积。

## 3. 功能需求

### 3.1 Tauri 进程编排核心能力（新增开发，最核心模块）

1. 启动流程（串行依赖顺序）
   1. 环境自检：校验包内所有内置二进制文件是否完整；
   2. 启动 nginx；等待 8080 端口探活成功；
   3. 启动 newapi（Go 网关）；探活对应网关端口；
   4. 启动 vectcut；探活对应端口；
   5. 启动便携 Python，拉起 FastAPI；探活 /api 端口；
   6. 全部服务就绪后，打开 Tauri 窗口加载 `http://127.0.0.1:8080`。
2. 运行时管理
   - 实时捕获全部子进程 stdout/stderr，UI 内置**服务状态面板**：每个服务运行状态、实时日志；
   - 支持单独一键重启任意服务；
   - 服务崩溃自动重启，并记录故障日志；
   - 启动异常捕获：端口占用、二进制缺失、权限不足，给出用户友好提示。
3. 退出 / 关闭流程
   - 用户关闭 Tauri 窗口时，按逆序优雅终止所有子进程；
   - 父进程退出时递归杀死所有子进程，**杜绝后台残留进程占用端口**。
4. 环境变量注入
Tauri 启动子进程时注入环境变量，供所有后端服务读取：
   - `APP_DATA_PATH`：Tauri app-data 目录（模型、缓存、临时文件）
   - `APP_RESOURCE_PATH`：App 内置资源目录（只读，存放 python、二进制、静态资源）

### 3.2 前端 & Web 服务

1. React/Vite 前端业务代码完全不变，原有接口、路由、静态资源保持原样；
2. Vite 构建产出 dist 静态资源，放入 App 资源目录，由内置 nginx 托管；
3. nginx.conf 仅修改资源路径，原有反向代理逻辑保持不变。

### 3.3 资源打包策略（便携 Python 规范）

1. 便携 Python：**采用 python-build-standalone (indygreg)**，禁止 Anaconda/Miniconda，保证商用分发许可合规；
2. 打包阶段（在对应架构构建机上执行）：
   - 解压对应架构的 python-build-standalone；
   - 在该便携 Python 环境内执行 `pip install -r requirements.txt`，离线预装全部依赖；
   - 将整套便携 Python 目录，连同 FastAPI 源码一起打包进 App 资源目录；
   - 用户端**不会执行任何 pip 安装操作，全程离线运行**。
3. 附属二进制：
   - newapi、vectcut：`CGO_ENABLED=0 go build` 静态编译单文件；
   - nginx、ffmpeg、Chromium：下载官方对应架构预编译二进制，放入资源目录。

### 3.4 安装包产物

1. macOS：`.app` + 带 `/Applications` 替身拖拽安装的 `.dmg`
2. Windows：NSIS 格式 `.exe` 安装包，支持桌面快捷方式、开始菜单入口、卸载程序
3. 多架构隔离：三套独立构建产物，不可跨架构复用
   - macOS arm64（M 系列芯片）
   - macOS x86_64（Intel Mac）
   - Windows x64

### 3.5 签名、公证脚本（后续接入 Apple Developer ID / Windows 代码签名证书）

1. macOS 脚本
   - 自动遍历 App 包内**所有内嵌可执行文件、so 扩展库**，批量 codesign 签名；
   - notarytool 提交公证，staple 固化公证票据；
   - 开发阶段可跳过签名，正式对外发布必须全套签名公证。
2. Windows 脚本 / 说明
   - 提供 EXE 代码签名命令示例，用于购买 Windows 代码签名证书后签名，降低 Defender 误报。

### 3.6 合规与软件关于页面

1. 增加「关于软件 / 第三方开源许可」页面；
2. 内置 Python、Torch、FFmpeg、Chromium、nginx、Go 的 LICENSE 文件；
3. LGPL 组件（FFmpeg）提供源码获取说明，满足开源分发合规要求。

### 3.7 模型下载器

1. Tauri 内置模型下载模块；
2. 首次启动检测 app-data 目录是否存在模型；缺失则后台拉取；
3. 支持断点续传、下载进度 UI 展示、下载失败重试；
4. 支持模型校验（sha256 哈希校验，防止文件损坏）。

## 4. 非功能需求

1. 兼容性
   - macOS：最低 macOS 12.0+
   - Windows：最低 Windows10 21H2+
2. 安全
   - `tauri.conf.json` 严格配置 shell 权限白名单，**仅允许调用 App 资源目录内的二进制**，禁止调用系统全局命令；
   - 资源目录只读，业务数据仅写入`app-data`；
3. 可维护性
   - 完整日志持久化，日志存放于 app-data，支持用户导出日志用于排障；
4. 性能
   - 服务启动增加进度提示，告知用户首次启动模型下载耗时；
5. 升级：预留 Tauri 自动更新能力，主程序全量更新。模型文件独立更新，不随主包下发。

## 5. 交付物清单

1. 完整 Tauri v2 工程目录
   - `tauri.conf.json` 双平台完整配置（窗口、bundle、权限、图标）
   - Rust 侧源码：进程编排、端口探活、日志捕获、模型下载器、状态面板前端页面
2. 资源目录模板：便携 Python 目录结构存放位置、nginx 配置模板
3. 自动化构建脚本
   - `build-macos-dmg.sh`：mac 编译打包，生成 dmg
   - `mac-sign-notarize.sh`：批量内嵌二进制签名、公证脚本
   - Windows 打包说明 + exe 签名示例命令
4. README.md
   - 构建环境准备（三套构建机器要求）
   - 打包全流程步骤
   - 许可合规说明
   - 已知风险清单（包体大小、MPS/CUDA 适配、杀毒误报）
5. 第三方开源许可文档

## 6. 约束条件

1. React 前端业务代码**禁止修改**，原有接口与静态资源逻辑保持原样；
2. FastAPI、Go、vectcut 业务逻辑代码尽量不变，**仅修改硬编码路径为环境变量读取**；
3. 不使用 Docker、Colima、docker-compose；
4. 三套构建流水线相互独立，便携 Python 环境不能跨架构复用；
5. 模型文件不打包进 DMG/EXE 主安装包。

---

# 👉 直接复制给 Qoder 的提示词（完整版）

```
需求：基于现有React/Vite前端项目，使用 Tauri v2 开发跨平台桌面应用，产出 Windows EXE 和 macOS DMG。
架构方案：路线1，便携Python目录方案，放弃Docker/Colima容器。
前端React采用相对路径 /api/v1、/gateway/v1、/static，无法独立静态运行，前端业务代码零改动。Tauri窗口加载 http://127.0.0.1:8080。

后端服务栈：Python FastAPI（torch推理、ffmpeg、Chrome CDP）、Go newapi网关、nginx反代、vectcut独立服务。业务逻辑代码尽量保持原样，仅将硬编码容器路径修改为读取Tauri注入的环境变量。

核心规则：
1. 便携Python使用 python-build-standalone (indygreg)，禁止Anaconda/Miniconda，保证商用分发许可合规。FastAPI原有requirements.txt依赖清单保持不变；打包阶段在对应架构机器上预先离线安装全部依赖进便携Python的site-packages，用户安装软件后不会执行pip。
2. Tauri Rust负责多进程编排：按顺序启动nginx、newapi、vectcut、fastapi；每个服务做端口探活；全部就绪后打开前端窗口。UI内置服务状态面板，实时采集所有子进程stdout/stderr日志，支持一键重启单个服务；服务崩溃自动重启。App关闭时逆序终止全部子进程，递归kill子进程，防止端口残留占用。
3. 模型文件不打包进DMG/EXE主安装包。Tauri内置模型下载器，首次启动检测app-data目录，缺失模型则断点续传下载，带sha256文件校验、进度UI、失败重试。
4. 资源：Go服务静态编译单二进制；nginx、ffmpeg、chromium采用官方预编译二进制，内置在App资源目录；nginx.conf仅修改路径，反向代理逻辑不变。所有业务读写路径指向Tauri app-data目录。
5. 打包产物：macOS产出.app + 带Applications替身的DMG；Windows产出NSIS EXE安装包，支持桌面快捷方式与卸载程序。三套独立构建：mac arm64(M芯片)、mac x86_64、Windows x64，环境不可跨架构复用。
6. 签名脚本：mac脚本自动遍历App内所有内嵌二进制、so库，批量codesign签名；notarytool提交公证并staple票据。附带Windows EXE代码签名命令示例。
7. 应用内增加【第三方开源许可】页面，附带Python、Torch、ffmpeg、Chromium、nginx、Go LICENSE，LGPL组件提供源码获取说明。
8. 权限：tauri.conf.json配置shell白名单，仅允许调用App资源目录内部二进制，禁止调用系统全局命令。
9. 兼容：macOS最低12.0，Windows最低Win10 21H2。
10. 输出完整Tauri工程结构、tauri.conf.json、rust代码、前端状态面板页面、全套打包脚本，附带详细注释与README文档，写明构建流水线、合规说明、已知风险。
```