# AIPDD DramaClaw 桌面端（Tauri v2）

把现有「React/Vite 前端 + 多服务后端」以 **Tauri v2 桌面壳 + 便携 Python** 的方式打包成
macOS `.app/.dmg`（及后续 Windows `.exe`），**弃用 Docker/Colima**：用户下载拖入 Applications
即可用，无需自行安装 Python/Docker/Colima/系统依赖。

> 需求原文见 `../需求/基于现有项目改造为 Tauri macOS 桌面应用.md`（路线 1：便携 Python 目录方案）。

---

## 1. 架构一览

```
┌─────────────────────────── Tauri App (Rust) ───────────────────────────┐
│  进程编排 orchestrator：按序启动 / 端口探活 / 崩溃自愈 / 日志采集 / 逆序退出     │
│  状态面板窗口 (panel)          主窗口 → http://127.0.0.1:8080 (加载前端)      │
│  模型下载器 (tokio+reqwest, 断点续传/sha256/进度)                             │
└──────────────┬───────────────────┬──────────────────┬──────────────────┘
   std::process│         std::process│        std::process│
        ┌──────▼─────┐      ┌────────▼───────┐   ┌──────▼───────┐
        │ nginx :8080│      │ new-api(Go):3000│  │ FastAPI:8780 │  (+ VectCut:9001 可选)
        │ 托管 dist   │      │ OpenAI 兼容网关  │  │ 便携 Python    │
        │ 反代 /api…  │      │ 内置 SQLite     │  │ torch 可选     │
        └────────────┘      └────────────────┘   └──────────────┘
```

- 主窗口只加载 `http://127.0.0.1:8080`，由内置 **nginx** 托管前端 `dist` 并反代后端，
  因此 **React 前端业务代码零改动**（相对路径 `/api/v1`、`/gateway/v1`、`/static` 原样可用）。
- 所有服务读写路径通过 **环境变量**指向 `app-data`（见 §4），后端已高度 env 化，改动集中在编排壳。

### 对需求文档的事实校正（勘查代码得出）
1. `services/vectcut` 是 **Python**（`requirements.txt`，HTTP `:9001`，compose 默认不启动），**不是 Go**；
   本骨架把它作为 **optional** 服务，与 FastAPI 共用同一便携 Python；失败不阻断就绪。
2. 根目录**无 `requirements.txt`**；后端依赖在 `pyproject.toml` + `uv.lock`，包名 `supertale-ce`、
   入口脚本 `novelvideo`。便携 Python 预装用 `uv pip install --python <exe> .`。
3. **CE 基础依赖不含 torch**：torch 只在可选 `[world]` extra（3DGS/SHARP）。默认包体因此显著小于预估；
   需该特性时 `INSTALL_WORLD=1`。
4. 额外去容器化依赖：**hermes-agent**（chat 后端）、**Chromium**（抖音 CDP）、**MCP servers**
   （原走 `docker exec`）。Chromium/MCP 的运行期接线为**未决项**（见 §11/§12）。

---

## 2. 目录结构

```
desktop/
  panel/                 服务状态面板（无构建静态页；也是启动/加载窗）
    index.html  app.js
  scripts/
    prepare-resources.sh      三架构便携运行时准备（Python/Go/nginx/ffmpeg/Chromium）
    build-macos-dmg.sh        产出 .app + .dmg
    mac-sign-notarize.sh      批量内嵌二进制签名 + notarytool 公证 + staple
    build-windows.md          Windows NSIS 打包 + 代码签名说明
  licenses/README.md     第三方开源许可与 AGPL/LGPL 义务清单
  resources/             （构建期生成，见 .gitignore）由 prepare-resources 填充
  src-tauri/
    tauri.conf.json  capabilities/default.json  entitlements.plist  build.rs  Cargo.toml
    icons/                 由 `cargo tauri icon` 生成（见 icons/README.md）
    resources/             打包真正读取的目录：nginx.conf / model-manifest.json / nginx/ 为静态提交，
                           bin / python / app 为 prepare 生成
    src/
      main.rs              入口：目录/窗口/生命周期/命令
      orchestrator.rs      进程编排（启停顺序/探活/自愈/退出）
      env.rs               路径与环境变量注入（APP_DATA_PATH/APP_RESOURCE_PATH → NOVELVIDEO_*）
      probe.rs             端口探活
      logs.rs              子进程 stdout/stderr tee 落盘 + 转发
      proc.rs / proc_unix.rs / proc_win.rs   进程树控制（killpg / Job Object）
      downloader.rs        模型下载器
      models.rs / error.rs 数据模型 / 错误归类
```

---

## 3. 进程拓扑与端口

| 服务 | 端口 | 可执行 | 说明 |
|---|---|---|---|
| nginx | 8080 | `resources/bin/nginx` | 托管前端 dist + 反代 `/api` `/gateway/v1` `/static`；`-g "daemon off;"` 前台 |
| gateway(new-api) | 3000 | `resources/bin/new-api` | Go 静态二进制，内置 SQLite（工作目录 `app-data/gateway`）|
| FastAPI | 8780 | 便携 `python` | `python -c "from novelvideo.cli import app; app()" api --host 127.0.0.1 --port 8780` |
| VectCut（可选） | 9001 | 便携 `python` | `python aipdd_server.py`；失败降级不阻断 |

启动顺序（串行 + 探活）：`环境自检 → nginx → gateway → VectCut(可选) → FastAPI → 打开主窗口`。
退出（关闭所有窗口触发 `RunEvent::Exit`）：**逆序**终止；Unix 对独立进程组 `killpg`，
Windows 靠 Job Object `KILL_ON_JOB_CLOSE` 连带回收全树，杜绝端口残留。

---

## 4. 环境变量契约（注入给后端子进程）

Tauri 计算并注入两类目录，再派生后端既有的 env：

- `APP_DATA_PATH`  = Tauri `app_data_dir()`（可写：state/output/runtime/logs/models/gateway）
- `APP_RESOURCE_PATH` = Tauri `resource_dir()`（只读：python/bin/app/nginx.conf）

| 变量 | 值 | 消费方 |
|---|---|---|
| `NOVELVIDEO_DATA_ROOT` | `APP_DATA_PATH` | 后端 |
| `NOVELVIDEO_OUTPUT_DIR` | `…/output` | 后端 |
| `NOVELVIDEO_STATE_DIR` | `…/state` | 后端 |
| `NOVELVIDEO_RUNTIME_DIR` | `…/runtime` | 后端 |
| `ST_EDITION` | `ce` | 后端（CE inline 任务，无 Redis/Celery） |
| `ST_CE_MULTI_USER` / `ST_COOKIE_SECURE` | `true` / `false` | 本地 http 登录制 |
| `ST_REDIS_URL` / `ST_CELERY_*` | 空 | 单进程内联 |
| `NEWAPI_BASE_URL` | `http://127.0.0.1:3000` | 后端→网关 |
| `PYTHONPATH` | `…/app/src` | FastAPI 导入 novelvideo |
| `PORT` / `SQL_DSN` | `3000` / 空(内置 SQLite) | 网关 |
| `VECTCUT_PORT` | `9001` | VectCut |
| `PATH` | 前置 `python/bin` 与 `bin` | ffmpeg/chromium 等就近命中 |

> 引导口令：CE 多用户首次需要一个引导账户口令。可在打包时经 `.env` 的 `ST_LOCAL_PASSWORD`
> 注入，或首启读取后端打印的随机口令。**不要把口令写进仓库**。

---

## 5. 构建机准备

> 更新（本机 arm64 mac 实测）：已安装 rustup/cargo 1.99 与 bun 1.4，
> `cargo check` / `cargo build` / `cargo clippy` **全部零错误零告警**（clippy 干净）；
> 网关 `new-api` 已实编译并本地起服（`/api/status` 正常，内置 SQLite 模式）。
> 仍未实跑的是「重量级 prepare-resources 全量下载（便携 Python + CE 依赖 + 前端 + nginx/ffmpeg/Chromium）」
> 与 `cargo tauri build` 出包 / 签名 / 公证，这些仍需在干净构建机按下方步骤执行。

构建机需装：

macOS：
```bash
xcode-select --install                                   # 含 codesign/clang
curl https://sh.rustup.rs -sSf | sh                      # rustup → cargo
rustup component add clippy rustfmt                      # 代码质门禁
cargo install tauri-cli --version "^2"                   # cargo tauri
brew install go uv node                                  # 网关编译 / Python 依赖 / 主前端
curl -fsSL https://bun.sh/install | bash                 # bun：网关自带后台 web/dist 的构建器（见下）
```

> ⚠️ 网关 `new-api` 用 `//go:embed web/dist` 内嵌它自带的管理后台（rsbuild + **bun**）。
> 不先产出 `web/dist`，`go build` 会直接失败：`pattern web/dist: no matching files found`。
> `prepare-resources.sh` 第 5 步已改为「先 `bun install --frozen-lockfile && bun run build`，再 go build」。

Windows：见 `scripts/build-windows.md`（VS Build Tools + rustup MSVC + tauri-cli + WebView2 + bun）。

三套产物互不复用，便携 Python 不能跨架构 —— 需在对应机器分别构建。

### 5.1 本地无重量级资源时的“仅编译校验”
不想跑完整个 `prepare-resources.sh`（多 GB）只想验 Rust 代码能编译时：
```bash
python3 scripts/gen-placeholder-icons.py   # 生成占位资源 glob + 扁平色图标（png/icns/ico）
iconutil -c icns /tmp/appicon.iconset -o src-tauri/icons/icon.icns
cd src-tauri && cargo clippy               # 零告警即达标
```
占位资源仅供 `tauri-build` 的资源 glob 校验与 `generate_context!` 读图标通过，
**不可用于真实运行**；正式出包前必须由 `prepare-resources.sh` 覆盖。

---

## 6. 打包全流程

```bash
cd desktop

# 1) 生成应用图标（一次性；见 src-tauri/icons/README.md）
cargo tauri icon assets/app-icon-source.png

# 2) 准备便携运行时资源（大下载/编译，按目标架构各跑一次）
scripts/prepare-resources.sh mac-arm64        # 或 mac-x64 / win-x64
#   可选： INSTALL_WORLD=1 装 torch（3DGS/SHARP，体积大，Mac 无 GPU）
#         FFMPEG_BIN=<静态ffmpeg> NGINX_BIN=<自包含nginx> 提供自包含二进制

# 3) 收集第三方许可文本（见 licenses/README.md，AGPL/LGPL 义务）
# 4) 构建 .app + .dmg
scripts/build-macos-dmg.sh arm64              # 开发/本机自测：不签名（ad-hoc）

# 5) 对外分发前：签名 + 公证 + staple（需 Apple Developer ID）
scripts/mac-sign-notarize.sh \
  "src-tauri/target/aarch64-apple-darwin/release/bundle/macos/AIPDD DramaClaw.app" \
  "src-tauri/target/aarch64-apple-darwin/release/bundle/dmg/AIPDD DramaClaw_<ver>_aarch64.dmg"
```

---

## 7. 开发调试

```bash
cd desktop
# 先跑一次 prepare-resources.sh 让 resources 就位（dev 也从 src-tauri/resources 读取）
cargo tauri dev
```
- dev 不签名；子进程用系统真实路径，逻辑与 prod 一致。
- 面板窗口即启动窗，可实时看各服务日志与状态，定位冷启动失败最方便。

---

## 8. 模型下载器

- 安装包**不含模型**。`src-tauri/resources/model-manifest.json` 声明要下载的模型：
  ```json
  { "models": [
    { "name":"示例权重", "url":"https://…/w.safetensors",
      "dest":"gallery/w.safetensors", "sha256":"<hex>", "size": 123456789 }
  ] }
  ```
- 留空 `models: []` = 不下载（安全默认，当前仓库状态）。
- 首启后台下载到 `app-data/models/<dest>`，**断点续传**（HTTP Range + `.part`）、**sha256 校验**、
  进度上报面板、失败退避重试 3 次。
- **未决**：`url`/`sha256` 清单需对接后端既有下载源（`feature_provisioner` / `catalog`），见 §12。

---

## 9. 服务状态面板 & IPC 契约

命令（Rust→`#[tauri::command]`，面板 `invoke`）：`get_status`、`restart_service{id}`、
`open_app_window`、`logs_dir_path`、`app_version`。

事件（Rust `emit`→面板 `listen`）：
- `svc://state`   单服务状态快照（含 status/pid/restarts/last_error）
- `log://line`    `{lines:[{service,stream,line,ts}]}` 批量日志
- `boot://progress` `{stage,message,progress}`（含 `stage:"error"` 时附排障提示）
- `model://progress` 下载进度

status 取值：`stopped/starting/ready/crashed/restarting`。

---

## 10. 权限与安全

- **Web 侧零 shell 能力**：进程编排全在 Rust 用 `std::process`，前端 JS 无法触发任何命令执行。
- `capabilities/default.json` 仅授予窗口/事件等最小权限；不注册 shell/updater 插件。
- 资源目录只读；业务只写 `app-data`。
- `entitlements.plist` 放开 `disable-library-validation` 等，便携 Python 才能 dlopen 第三方 `.so`。
- 密钥/凭据只经环境或钥匙串读取，绝不入库（`gitleaks` pre-commit 兜底）。

---

## 11. 里程碑进度

| 里程碑 | 内容 | 状态 |
|---|---|---|
| M0 | 可行性 Spike（便携 Python 起 api / nginx 托管 / 网关直跑） | 🟢 **本机 arm64 全链路实跑通**：便携 Python(uv.lock 冻结)→FastAPI `/api/v1/config` 200、nginx 托管 dist + 反代、网关 `/api/status` 200 |
| M1 | 最小可跑桌面 App（编排 + 窗口 + env + nginx 配置） | 🟢 **`.app` 真实构建 + 端到端编排冒烟通过**：`tauri build` 产出 1.53 GiB `.app`（arm64），启动后串行编排 nginx/gateway/vectcut/api 全就绪，主窗口加载 `http://127.0.0.1:8080` |
| M2 | 面板/自愈/日志/异常提示 | 代码已落地且随 M1 一同编译通过 |
| M3 | 三架构资源准备脚本 | 🟢 **`prepare-resources.sh mac-arm64` 全流程实跑产出真实资源**（~1.3 GiB：便携 Python/前端 dist/后端源码/new-api/nginx/ffmpeg） |
| M4 | 模型下载器 | 代码已编译通过；**manifest 数据源待定** |
| M5 | 打包/签名/公证脚本 | 🟢 **`.dmg` 真实产出**：`tauri build --bundles dmg` → 597 MiB `.dmg`（hdiutil 校验通过，内嵌有效 arm64 `.app`）；**签名/公证仍需 Developer ID + 真机** |
| M6 | 合规/关于页/文档 | 义务清单 + 本 README 落地；**App 内合规页待做（前端）** |
| M7 | Windows NSIS | 脚手架 + 说明；**需真实 Windows** |

---

## 12. 已知风险 / 未决项

1. **体积**：默认 CE 不含 torch，包体可控；`INSTALL_WORLD=1` 引入 torch 后可达数 GB。
2. **可重定位性**：macOS 上 Homebrew 版 nginx/ffmpeg 链接 cellar/系统 dylib，跨机分发可能缺库——
   建议提供**自包含静态二进制**或在签名脚本一并 vendor + 重签依赖库。
3. **Chromium/CDP 接线**：后端连抖音用的是 CDP；去容器后需由编排器以 `--remote-debugging-port`
   启动内置 Chrome 并把路径交给后端（后端连接方式需适配）。**尚未接入 orchestrator**。
4. **MCP servers / hermes-agent**：原 `docker exec` 起 MCP、`HERMES_CLI_PATH` 指 hermes；
   桌面需改为本地子进程 + 正确 env。**后续里程碑**。
5. **便携 Python 移动性**：console-script shim 写死构建机绝对路径——已规避（用 `python -c` 调 `app()`）；
   M0 实跑已确认打包后的便携 Python 能正常拉起 FastAPI（不依赖 `.venv`）。
6. **AGPL 网关**：分发义务见 `licenses/README.md` §2。
7. **模型清单**：需复用后端 `feature_provisioner`/`catalog` 的源与校验值，否则下载器空转。
8. **自动更新**：需求 4.5 预留 Tauri updater，本骨架**未接**（主程序全量更新，模型独立更新）。
9. **强杀不回收子进程树**：优雅退出（关窗 → `RunEvent::Exit` → `shutdown()`）会逆序回收全树；
   但 `SIGKILL`/崩溃时 nginx/python 子进程会短暂残留（各自独立 process group）。后续可加信号
   处理器/启动前端口自清。本机冒烟时以 `pkill` 验证过手动回收。

### 12.1 本轮端到端冒烟实测修正（macOS）

- **nginx 配置路径含空格**：app-data 与 `.app` 路径都含空格（`…/Application Support/…`、
  `AIPDD DramaClaw.app/…`），未加引号的 `pid`/`root`/`include`/`*_temp_path`/`access_log` 会被 nginx
  当作多参数报错。已在 `resources/nginx.conf` 模板里给所有路径值加双引号。
- **网关工作目录未预建**：new-api 以 `app-data/gateway` 为 cwd 启动，`RuntimePaths::new` 旧版未预建
  该目录→`Command::current_dir` 的 spawn 直接失败→`start_all` 卡住。已在预建列表补 `gateway`。

---

## 13. 故障排查（Gatekeeper / 启动失败）

- **「已损坏 / 无法验证开发者」**：未签名或未公证。本机自测可 `xattr -dr com.apple.quarantine "<App>"`；
  对外分发必须走 `mac-sign-notarize.sh` 完成签名 + 公证 + staple。
- **「端口 8080/8780/3000/9001 已被占用」**：多为仍在运行的 Docker/Colima 旧栈占用，先 `docker-compose -f docker-compose.aipdd.yml down` 或关掉占用进程。面板会红条提示。
- **某服务一直「启动中/异常」**：看面板日志（红色为 stderr）。FastAPI 首次冷启动（import 依赖）可能较慢，就绪超时 180s。
- **日志位置**：`app-data/logs/{nginx,gateway,vectcut,api}.log` + `app-data/nginx/{access,error}.log`；面板底部「复制日志目录路径」。
- **主窗口打不开**：服务未全就绪时主窗口不会出现，先解决面板所示失败服务；就绪后可点面板「打开主界面」。

---

## 14. 与既有 Docker 栈的关系

本桌面方案与仓库现有 `docker-compose.aipdd.yml` **并存**、互不影响：compose 用于服务器/自托管，
桌面方案用于「开箱即用」本地分发。二者共用同一份前端源码与后端业务代码（零改动），差异仅在
「后端如何被拉起与喂路径」。
