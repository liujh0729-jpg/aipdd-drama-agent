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
│  启动卡窗口 (panel)            主窗口 → http://127.0.0.1:8080 (加载前端)      │
│  诊断窗口 (console，仅失败时由启动卡「查看详细信息」打开)                       │
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
   （原走 `docker exec`）。内置 MCP 已改为本地 stdio 子进程并按部署形态取默认地址（见 §12）；
   Hermes 运行时已随包分发（`resources/hermes/site-packages`，由随包 Python 以 `-c` 启动，见 §12.3）；
   剩余未决项是 Chromium 的自动拉起。

---

## 2. 目录结构

```
desktop/
  panel/                 无构建静态页：启动卡 + 诊断窗口（两个 HTML 共用 app.js）
    index.html           启动卡（Photoshop 式 splash）：品牌 + 版本 + 百分比 + 一行阶段文字 + 平台网址
    console.html         运行期诊断：服务卡片 + 日志 + 单服务重启（启动失败时打开）
    app.js  logo.png
  scripts/
    prepare-resources.sh      三架构便携运行时准备（Python/Go/nginx/ffmpeg/Chromium/Hermes）
    build-nginx-x64.sh        mac-x64 专用：源码交叉编最小自包含 x86_64 nginx（纯 HTTP、静态 PCRE）
    build-macos-dmg.sh        产出 .app + .dmg
    mac-sign-notarize.sh      批量内嵌二进制签名 + notarytool 公证 + staple（结尾自动对 dmg 跑出厂终检）
    verify-release-dmg.sh     发布产物出厂终检：钉票/包内 App Gatekeeper/deep 校验/架构/版本一致性
    build-windows.ps1         产出 NSIS .exe 安装包（前置自检 → cargo tauri build --bundles nsis → 产物 sha256）
    win-sign.ps1              Windows 代码签名（-Stage Resources 签内嵌 PE / -Stage Installer 签安装包）
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
      portadmin.rs         端口占用者定位与释放（Windows: netstat+tasklist / Unix: lsof），只杀外部非受管 pid
      logs.rs              子进程 stdout/stderr tee 落盘 + 转发
      proc.rs / proc_unix.rs / proc_win.rs   进程树控制（killpg / Job Object）
      downloader.rs        模型下载器
      models.rs / error.rs 数据模型 / 错误归类
```

---

## 3. 进程拓扑与端口

| 服务 | 端口 | 可执行 | 说明 |
|---|---|---|---|
| nginx | 8080（默认） | `resources/bin/nginx` | 托管前端 dist + 反代 `/api` `/gateway/v1` `/static`；`-g "daemon off;"` 前台。**8080 被外部占用时自动往后避让到 8081…8089 并定格本次会话**（主窗口地址、nginx `listen`/CSP 同步跟随），10 个全占才报错 |
| gateway(new-api) | 3000 | `resources/bin/new-api` | Go 静态二进制，内置 SQLite（工作目录 `app-data/gateway`）|
| FastAPI | 8780 | 便携 `python` | `python -c "from novelvideo.cli import app; app()" api --host 127.0.0.1 --port 8780` |
| VectCut（可选） | 9001 | 便携 `python` | `python aipdd_server.py`；失败降级不阻断 |
| 换脸直播边车（可选，按需自装） | 18899 | 独立 venv 的 python + `resources/app/services/faceswap/server.py` | **不在 Rust 受管服务列表内**：由后端 `novelvideo/faceswap/faceswap_install.py` 在用户点「下载并部署」后用随包的 `resources/bin/uv` 建 `<状态目录>/faceswap/venv` 再 `Popen` 拉起；只绑 `127.0.0.1`，AGPL 隔离靠进程边界 |

启动顺序（串行 + 探活）：`环境自检 → nginx → gateway → VectCut(可选) → FastAPI → 打开主窗口 → 关闭启动卡`。
启动期间界面上只有启动卡：品牌 + 版本号 + 一行阶段文字 + 百分比进度条 + 平台网址（`AIPDD算力平台：www.aipdd.work`），
**不显示日志**。百分比完全按实际：各服务按实测耗时分段、段内随探活轮次缓动推进（只前进不回退），
就绪后补到 100% 并自动进入主界面，启动卡随即关闭、不做额外停留。只有启动失败时才会在卡片上露出「查看详细信息」，
点开诊断窗口看服务卡片与日志。
退出（关闭所有窗口触发 `RunEvent::Exit`）：**逆序**终止；Unix 对独立进程组 `killpg`，
Windows 给**每个服务单建一个 Job Object**（`KILL_ON_JOB_CLOSE`）——单服务重启只结束顶层 pid 时，
其 nginx worker / uvicorn 子进程 / ffmpeg / chrome 后代也随作业一起回收，不会留着占端口。

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
| `PATH` | 前置便携 Python 与 `bin` | ffmpeg/chromium 等就近命中；Windows 为 `python\`+`python\Scripts\`，Unix 为 `python/bin` |
| `PYTHONUTF8` / `PYTHONIOENCODING` / `PYTHONNOUSERSITE` | `1` / `utf-8` / `1`（仅 Windows 注入） | 让 Python 子进程 stdio 统一 UTF-8，否则中文 Windows 按 cp936 输出，面板与日志会乱码、读线程会中断 |

> 引导口令：CE 多用户首次需要一个引导账户口令。可在打包时经 `.env` 的 `ST_LOCAL_PASSWORD`
> 注入，或首启读取后端打印的随机口令。**不要把口令写进仓库**。

### 4.1 动效大片（MG）链路的取物顺序

`图册 → 逐帧渲染 → 代码编曲 → 剪映草稿`这条链**不需要编排器注入任何新变量**，三个需要外部
二进制/资源的环节各自按「显式 env → 包内资源 → PATH」探，装机版默认三个都不设：

| 环节 | 显式开关（可选） | 装机版怎么命中 |
|---|---|---|
| 逐帧浏览器（`motion_render/chrome.py`） | `ST_CHROME_CDP_URL` / `ST_CHROME_PATH` | `APP_RESOURCE_PATH` 下探 `resources/bin/chrome/`（与 `env.rs` 的 `rel()` 同一探法，mac 的 `.app` 再进一层），最后退 PATH |
| 编码 + 响度归一 + 合轨（`novelvideo/video_encoder.py`、`motion_render/encoder.py`、`motion_audio/ffmpeg.py`） | `FFMPEG_PATH`、`VIDEO_CODEC` | 上面 §4 已前置的 `PATH` → `resources/bin/ffmpeg(.exe)`，`ffprobe` 同目录；`VIDEO_CODEC` 只在包内这台 build 真有那个编码器、且它是阶梯里的 H.264 实现时才生效，否则按阶梯另选 |
| 剪映 10.2 草稿骨架（`motion_draft/writer.py`） | `MOTION_DRAFT_TEMPLATE_DIR` / `NOVELVIDEO_PROJECT_ROOT` | 随包 `resources/app/services/vectcut/template_jianying_10_2`（按「哪层找得到骨架」反推工程根） |

指错不会静默回落：`ST_CHROME_PATH` 指不到文件、`FFMPEG_PATH` 既不是文件也不在 PATH 上时，
当次任务直接报「找不到」并把变量名打出来（可用编码器清单是按这个二进制探的，悄悄换二进制会失真）。

> **随包 ffmpeg 是 BtbN 的 LGPL 构建**（win-x64 默认，见 `desktop/scripts/prepare-resources.sh`），
> 以 `--disable-libx264` 编译：实测包内那份没有 `libx264`，但有 `h264_mf`/`libopenh264`、原生 `aac`
> 与 `loudnorm`/`alimiter`/`afade`。因此 `-c:v` 全系统不写死：`src/novelvideo/video_encoder.py`
> 先探本机 `-encoders`，按 `libx264 → h264_videotoolbox → h264_mf → libopenh264` 取第一个可用的
> （非 x264 时改码率模式，取 `VIDEO_BITRATE`），MG 渲染、整集合成、`jobs.py` 那批一键工具都走这一处。
> 声音口径也不受影响（实测归一后 `-14.05 LUFS / -1.00 dBTP`）。装机版实测：`VideoComposer().codec`
> 从 `libx264` 变成 `h264_mf`，同一条命令 `exit 0` 且 ffprobe 回读 `h264,320,30`。取舍与例外（老链路
> argv 跑的是 PATH 上那台裸 `ffmpeg`）见 [ffmpeg 指南](../docs/zh/guides/ffmpeg.md)。

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

Windows：见 `scripts/build-windows.md`（VS Build Tools + rustup MSVC + tauri-cli + Git Bash + bun + WebView2），
出包用 `scripts/build-windows.ps1`，签名用 `scripts/win-sign.ps1`。

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
#   脚本开头会 `rm -rf resources/{bin,python,app}`，所以哪怕从 Windows 机器整目录拷过来、
#   里面还躺着 2.4GB 的 win 版便携 Python 与 .exe，也会在 mac 上被换成本机架构，不会混进 .dmg。
#   启动卡不在这一步：它在 ../panel 里，编译期进二进制，改卡片不需要重跑资源准备。
#   可选： INSTALL_WORLD=1 装 torch（3DGS/SHARP，体积大，Mac 无 GPU）
#         FFMPEG_BIN=<静态ffmpeg> NGINX_BIN=<自包含nginx> 提供自包含二进制

# 3) 收集第三方许可文本（见 licenses/README.md，AGPL/LGPL 义务）
# 4) 构建 .app + .dmg
scripts/build-macos-dmg.sh arm64              # 开发/本机自测：不签名（ad-hoc）

# 5) 对外分发前：签名 + 公证 + staple（需 Apple Developer ID）
#    ⚠️ 公证开关 NOTARIZE 默认关闭——不加则只做本地签名、不向 Apple 提交；对外分发必须显式 NOTARIZE=1。
NOTARIZE=1 scripts/mac-sign-notarize.sh \
  "src-tauri/target/aarch64-apple-darwin/release/bundle/macos/AIPDD DramaClaw.app" \
  "src-tauri/target/aarch64-apple-darwin/release/bundle/dmg/AIPDD DramaClaw_<ver>_aarch64.dmg"
#    凭据二选一：NOTARY_PROFILE=<store-credentials 存好的凭据名>，或 APPLE_ID + APPLE_APP_PWD + TEAM_ID。
#    脚本内置防线：缺签名身份开头即败；内嵌 Mach-O 按核数并行批量签；提交公证前做「全量漏签预扫」
#    （漏签/seal 坏/仍 ad-hoc 当场列出 exit 12，不进 Apple 队列）；公证非 Valid 自动按 id 拉公证日志（exit 13）；
#    公证+钉票后自动对 dmg 跑出厂终检（exit 14）——脚本跑绿即「产物可分发」。

# 6) 存量产物随时复核（不改动产物，只读挂卷实测）：
scripts/verify-release-dmg.sh "dist/AIPDD DramaClaw_<ver>_aarch64.dmg" "dist/AIPDD DramaClaw_<ver>_x64.dmg"
#    公证记录绑 cdhash、永久有效：已公证的 dmg 无需重复公证；只有产物字节变了（重新构建/签名）才需重新提交。
```

### 6.1 产出 Intel（x86_64）macOS 包

三套产物互不复用，便携 Python 不能跨架构 —— **Intel 包必须单独出一份**（universal 因
Python 及其 C 扩展 wheel 无法合并而不可行）。在 Apple Silicon 本机即可交叉产出，前置：
```bash
softwareupdate --install-rosetta --agree-to-license   # 构建期用 Rosetta 跑 x64 python/uv 装 wheel
rustup target add x86_64-apple-darwin                 # Rust x86_64 标准库
```

`scripts/prepare-resources.sh mac-x64` 已内置 Intel 专属处理，正常一键即可：
- **依赖钉版本**：`lancedb→0.25.3`、`cryptography→48.0.1`（较新版停发 macOS Intel 轮；env `LANCEDB_X64_PIN`/`CRYPTO_X64_PIN` 覆盖）
- **nginx**：调 `scripts/build-nginx-x64.sh` 源码交叉编一个**最小 x86_64**（纯 HTTP、静态 PCRE、无 OpenSSL —— Homebrew 已停 macOS Intel 瓶，`brew install nginx` 只会给 arm64）
- **ffmpeg/ffprobe**：取 `ffmpeg-static` 的 `ffmpeg-darwin-x64.gz` + `ffprobe-static` tarball 内 `bin/darwin/x64/ffprobe`（均只依赖系统库）
- **new-api**：`go build GOARCH=amd64`（模块代理网络差时 `GOPROXY=https://goproxy.cn,direct GOSUMDB=off`）
- **uv**：从 PyPI 的 `macosx_*_x86_64` wheel 取

出包与签名/公证与 arm 一致，只是 target 换成 `x86_64-apple-darwin`：
```bash
scripts/build-macos-dmg.sh x64      # 产物在 target/x86_64-apple-darwin/release/bundle
# 对外分发同样必须显式开公证：
NOTARIZE=1 scripts/mac-sign-notarize.sh \
  "src-tauri/target/x86_64-apple-darwin/release/bundle/macos/AIPDD DramaClaw.app" \
  "src-tauri/target/x86_64-apple-darwin/release/bundle/dmg/AIPDD DramaClaw_<ver>_x64.dmg"
```
> 手上有自包含 x86_64 二进制时，也可 `NGINX_BIN=/… FFMPEG_BIN=/…`（`FFMPEG_BIN` 同目录需含 ffprobe）显式注入，跳过自动取件。
> 验收：出包后全包 Mach-O 里「纯 arm64」应为 0 ——
> `find "…/AIPDD DramaClaw.app" -type f -print0 | xargs -0 -I{} sh -c 'lipo -archs "{}" 2>/dev/null' | grep -c arm64`

Windows x64（PowerShell，与 mac 分属独立流水线；细节见 `scripts/build-windows.md`）：

```powershell
cd desktop
bash scripts/prepare-resources.sh win-x64                                  # 便携资源（win 布局）
powershell -File scripts\build-windows.ps1                                 # NSIS setup.exe
# 对外分发前（证书与 Apple 是两套；顺序不能反，先签内嵌 PE 再出包）：
powershell -File scripts\win-sign.ps1 -Stage Resources
powershell -File scripts\build-windows.ps1
powershell -File scripts\win-sign.ps1 -Stage Installer -Installer '<上一步打印的 setup.exe>'
```

---

## 7. 开发调试

```bash
cd desktop
# 先跑一次 prepare-resources.sh 让 resources 就位（dev 也从 src-tauri/resources 读取）
cargo tauri dev
```
- dev 不签名；子进程用系统真实路径，逻辑与 prod 一致。
- 启动期界面只有启动卡（无日志）。要看实时日志/状态：启动失败时点卡片上的「查看详细信息」打开诊断窗口，
  其底部「复制日志目录路径」给出的就是 `app-data/logs/`。

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
  进度上报诊断窗口、失败退避重试 3 次。
- **未决**：`url`/`sha256` 清单需对接后端既有下载源（`feature_provisioner` / `catalog`），见 §12。

---

## 9. 启动卡 / 诊断窗口 & IPC 契约

两块 UI 都在 `desktop/panel/`（无构建静态页，`build.frontendDist` 直接内嵌进包），共用 `app.js`，
用 `window.PANEL_VIEW` 区分：`index.html` = 启动卡（label `panel`），`console.html` = 诊断窗口（label `console`）。

命令（Rust `#[tauri::command]`，Web 侧 `invoke`）：

| 命令 | 用途 | 调用方 |
|---|---|---|
| `app_version` | 版本号（启动卡拼成 `Version x · Windows`） | 两者 |
| `quit_app` | 无边框启动卡唯一的手动退出入口（走 `app.exit` 才会回收子进程） | panel |
| `open_console_window` | 创建/聚焦诊断窗口 | panel |
| `get_status` | 全服务状态快照 | console |
| `get_boot_state` | 最后一条 `boot://progress`（窗口建得晚于事件时用它补历史） | 两者 |
| `recent_logs{limit}` | 内存日志尾巴（上限 2000 行），同上 | console |
| `restart_service{id}` | 单服务重启 | console |
| `open_app_window` | 打开/聚焦主窗口 | console |
| `splash_finished` | 启动卡自报「百分比已跑满并停留完毕」，壳收到才开主窗口、关卡 | panel |
| `logs_dir_path` | 复制日志目录路径 | console |
| `port_health` | `WATCH_PORTS` 逐个给出 `free`/`ours`/`external` + 外部占用者 `pid(程序名)` | console |
| `release_ports` | 一键释放：只终止受管端口上的**外部**监听进程，我方 pid 一律不动 | console |
| `restart_to_default` | 先释放外部占用，再 `restart_all()`（内部 `reset_web_port()` 把站点收回 8080），最后把主窗口 `navigate` 到新地址 | console |

事件（Rust `emit`→Web 侧 `listen`）：
- `boot://progress` `{stage,message,progress}` — 启动卡的全部数据来源：`check` / `start:<svc>` /
  `wait:<svc>` / `ready` / `error`；`progress` 为 0~1，按服务实测耗时分段（`orchestrator.rs` 的
  `BOOT_STAGES` + `boot_progress()`），段内随探活轮次缓动。启动卡显示的就是这条真实进度：限速连续爬、
  单调不回退，`ready` 后补到 100%。`error` 时启动卡转琥珀色并露出「查看详细信息」。

Web 侧取桥：`withGlobalTauri` 注入的全局名是 **`window.__TAURI__`**（前后各两个下划线，见
`tauri/scripts/bundle.global.js` 末行），写成 v1 的 `__TAURI` 会拿到 `undefined`，`invoke`/`listen`
静默退化成桩——表现就是启动卡收不到任何阶段事件。`app.js` 两个名字都读，且启动卡额外用
`get_boot_state` 补一次历史，避免只依赖事件时序。

换屏时机不靠固定 sleep：`start_all` 返回后 Rust 先 `wait_splash_finished()`（轮询 `SPLASH_DONE`，
上限 `SPLASH_FINISH_WAIT=1.5s`，桥不通时兜底），再 `open_main_window` → `close_splash`。
前端在 100% 原地停 0.35s 才发 `splash_finished`，所以「跑满」那一帧一定看得见，不会被主窗口盖掉。

### 9.1 启动卡：macOS 与 Windows 是同一份实现

不需要为 mac 再做一张卡，也不该做：`panel/` 由 `build.frontendDist` 直接编进二进制，
mac 的 `.app/.dmg` 与 win 的 `setup.exe` 内嵌的是同一份 HTML/CSS/JS 和同一个 `main.rs`。
`src-tauri/src/` 里带 `#[cfg(windows)]`/`#[cfg(unix)]` 的只有 `proc.rs`、`env.rs`、`logs.rs`
三处（进程树、便携 Python 布局、日志代码页），启动卡这条链路（`boot_sequence` →
`splash_finished` → `DARK_BG` → `BOOT_STAGES` 分段进度）零平台分支。

已经按 mac 对齐过的四处细节：

- 底色：`background_color` 在 mac 落到 `NSWindow` 底色（Windows 忽略 alpha 通道），同样不闪白帧。
- 字体：`-apple-system, "PingFang SC", "Microsoft YaHei"`，mac 上落 PingFang，Windows 上落雅黑。
- 版本行：`osLabel()` 在 mac 显示 `Version <app_version> · macOS`（当前包为 `Version 2.3.0 · macOS`）。
- 换屏顺序：mac 与 win 一样「最后一个窗口关闭即退出」，所以必须先开主窗口再关启动卡，
  顺序反了进程会自己把自己杀掉，服务也就跟着没了。

mac 侧验收口径与 `scripts/build-windows.md` 的窗口行一致，出包后 `open` 那个 `.app`，看四件事：
① 首帧不闪白；② 卡上只有品牌 + 版本 + 百分比 + 一行阶段文字 + `AIPDD算力平台：www.aipdd.work`，
没有日志；③ 百分比随真实阶段爬（如 `正在启动 FastAPI 主服务 (Python)… 48%`）；
④ 跑到 100% 并原地停约 0.35s 才出现主界面。要留证据就连拍 8s（启动卡固定 660×380 居中）：

```bash
mkdir -p /tmp/splash && for i in $(seq -w 0 79); do screencapture -x /tmp/splash/$i.png; sleep 0.1; done
```
- `svc://state`   单服务状态快照（含 status/pid/restarts/last_error）
- `log://line`    `{lines:[{service,stream,line,ts}]}` 批量日志（仅诊断窗口消费）
- `model://progress` 下载进度（仅诊断窗口消费）

status 取值：`stopped/starting/ready/crashed/restarting`。

---

## 10. 权限与安全

- **Web 侧零 shell 能力**：进程编排全在 Rust 用 `std::process`，前端 JS 无法触发任何命令执行。
- `capabilities/default.json` 仅授予 `main` / `panel`（启动卡）/ `console`（诊断窗口）三个窗口的
  窗口/事件最小权限；不注册 shell/updater 插件。
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
| M7 | Windows NSIS | 🟢 **全链路真机跑通**：`prepare-resources.sh win-x64` 真实产出 2.39GB 资源（便携 Python/前端 dist/后端源码/`new-api.exe`/nginx/ffmpeg+ffprobe/Chromium，共 28 733 个文件）→ `build-windows.ps1` 出 `AIPDD DramaClaw_2.1.7_x64-setup.exe` **668MB**（sha256 见 sidecar）→ 静默安装 58s/退出码 0 → 起服后 8080/3000 返回 200、9001/8780 探活通过 → 强杀壳零残留 → 卸载后包内只剩 1 个运行期写出的文件。详见 `scripts/build-windows.md §8.1`。**签名仍需代码签名证书** |

---

## 12. 已知风险 / 未决项

1. **体积**：默认 CE 不含 torch，包体可控；`INSTALL_WORLD=1` 引入 torch 后可达数 GB。
2. **可重定位性**：macOS 上 Homebrew 版 nginx/ffmpeg 链接 cellar/系统 dylib，跨机分发可能缺库——
   建议提供**自包含静态二进制**或在签名脚本一并 vendor + 重签依赖库。
3. **Chromium/CDP 接线**：**动效大片渲染这条链已经自备这套**（`freezone/motion_render/chrome.py`：
   `ST_CHROME_CDP_URL` 连现成实例 → `ST_CHROME_PATH` 指可执行 → `APP_RESOURCE_PATH` 下内置
   `resources/bin/chrome/` → 退 `PATH` 上的 `chrome`；都没有就吵）。它按作业自己以
   `--remote-debugging-port` 拉起临时无头实例、用完连同后代回收，不依赖编排器常驻浏览器。
   **抖音详情抽取**那条仍只「连」一个已经开着调试端口的 Chrome，但默认地址已按部署形态收敛：
   本机/桌面为 `127.0.0.1:9222`，容器为 `host.docker.internal:9222`（`utils/chrome_cdp.default_cdp_url`，
   内置 Chrome MCP、环境探测、抖音详情共用同一处）。装机版要么由用户自己起 Chrome，要么设
   `ST_CHROME_CDP_URL`；复用上面 `chrome.py` 的拉起逻辑仍是留给 orchestrator 的未决项。
   取物顺序与可选覆盖见 §4.1。
4. **MCP servers / hermes-agent**：原 `docker exec` 起 MCP、`HERMES_CLI_PATH` 指 hermes。
   四个内置 MCP 已改为本地 stdio 子进程：本机默认连 `127.0.0.1` 的 Chrome / Blender，容器才走
   `host.docker.internal`；Windows 上 npm/uv 的 `.cmd/.bat` 包装经 `cmd.exe /c` 启动。
   Hermes 运行时已随包：依赖用 `uv pip install --target` 落到 `resources/hermes/site-packages`
   （**不是 venv**——`pyvenv.cfg` 记录 base 解释器绝对路径，安装包挪目录即失效），Rust 注入
   `HERMES_CLI_PATH=<随包 python>` + `HERMES_CLI_ARGS=-c "from hermes_cli.main import main; main()"`
   + `HERMES_SITE_PACKAGES`，由子进程 `PYTHONPATH` 加载。Windows 的 Hermes 子进程还会拿到
   `SYSTEMROOT`/`USERPROFILE`（收敛到用户工作区）等基础变量，否则 `Path.home()` 直接抛错。
   技能/插件在工作区的镜像优先建软链；Windows 无符号链接权限（WinError 1314）时退化为带内容
   指纹的托管副本，源码升级后下次 spawn 自动刷新。
5. **便携 Python 移动性**：console-script shim 写死构建机绝对路径——已规避（用 `python -c` 调 `app()`）；
   M0 实跑已确认打包后的便携 Python 能正常拉起 FastAPI（不依赖 `.venv`）。
6. **AGPL 网关**：分发义务见 `licenses/README.md` §2。
7. **模型清单**：需复用后端 `feature_provisioner`/`catalog` 的源与校验值，否则下载器空转。
8. **自动更新**：需求 4.5 预留 Tauri updater，本骨架**未接**（主程序全量更新，模型独立更新）。
9. **强杀不回收子进程树**：优雅退出（关窗 → `RunEvent::Exit` → `shutdown()`）会逆序回收全树；
   但 macOS 上被 `SIGKILL`/崩溃时 nginx/python 子进程会短暂残留（各自独立 process group）。
   本机冒烟时以 `pkill` 验证过手动回收。**Windows 已闭环**：每个服务一个 Job Object
   （`KILL_ON_JOB_CLOSE`），实测强杀桌面壳后 nginx/网关/便携 Python 与其孙进程零残留、端口全释放；
   macOS 侧可用「子进程也放一个独立会话/作业」的等价方案跟进（未做，避免动已验证的 mac 链路）。

### 12.1 本轮端到端冒烟实测修正（macOS）

- **nginx 配置路径含空格**：app-data 与 `.app` 路径都含空格（`…/Application Support/…`、
  `AIPDD DramaClaw.app/…`），未加引号的 `pid`/`root`/`include`/`*_temp_path`/`access_log` 会被 nginx
  当作多参数报错。已在 `resources/nginx.conf` 模板里给所有路径值加双引号。
- **网关工作目录未预建**：new-api 以 `app-data/gateway` 为 cwd 启动，`RuntimePaths::new` 旧版未预建
  该目录→`Command::current_dir` 的 spawn 直接失败→`start_all` 卡住。已在预建列表补 `gateway`。

---

### 12.2 本轮端到端冒烟实测修正（Windows x64，真机）

在真实 Win10 x64（MSVC 工具链）上编译并实跑桌面壳，逐条修掉「只在 Windows 才暴露」的问题：

- **便携 Python 布局**：install_only 包在 Windows 是 `python\python.exe`（**没有 `bin/` 这一层**），
  旧 `env.rs::python()` 写死 `python/bin/python3` → 环境自检必然判「缺 python」、FastAPI 起不来。
  现按平台解析（`python()` / `python_path_dirs()`），`prepare-resources.sh` 用同一约定。
- **`\\?\` verbatim 路径**：未打包运行时 Tauri 的 `resource_dir()` 源自 `current_exe()`，返回
  `\\?\E:\…`；它被 `display()` 原样写进 nginx 的 `include`/`root` 和注入的 `PYTHONPATH`/`PATH`。
  verbatim 语义关闭系统路径规范化，第三方程序支持不一。`RuntimePaths::new` 现统一剥前缀（实测修前后对照）。
- **nginx 配置反斜杠**：nginx 在 Windows 把 `\` 当转义符（`C:\Users\x` → `C:Usersx`），配置内路径
  一律经 `env::nginx_path_str` 转正斜杠；`-p` 前缀参数保持原生路径。实测渲染后 8080 返回 200。
- **进程树回收改成「每服务一个作业」**：原来全套服务共用一个 Job，`terminate_tree` 是空实现，
  单服务重启只 kill 顶层 → nginx worker/后代留在作业里继续占端口，重启必然超时。
  现按 pid 建作业，重启即 `TerminateJobObject`；回归测试 `proc_win::tests`（`cargo test -- --ignored`，2 passed）。
- **日志读线程会被非 UTF-8 打断**：旧实现 `read_line` into `String`，中文 Windows 上 nginx.exe 等
  原生程序按 cp936 输出 → `Err` 即 break，日志断流且管道写满后**子进程直接卡死**。现改为按字节读行 +
  `GetOEMCP` 代码页回落解码，并对 Python 服务注入 `PYTHONUTF8=1`。实测 GBK 行与 Python 中文行都正确入库。
- **控制台黑框**：GUI 壳拉起 console 程序会闪黑窗，子进程统一带 `CREATE_NO_WINDOW`。
- **ffprobe 随包**：后端有 63 处 ffprobe 调用，`prepare-resources.sh` 现在与 ffmpeg 一起取
  （Windows 取 BtbN **LGPL** win64 构建，避免 GPL 变体带来的额外源码义务）。
- **WebView2**：`bundle.windows.webviewInstallMode = embedBootstrapper`（静默补装，+~1.8MB），
  对齐「用户无需手动安装任何系统依赖」；完全离线交付可改 `offlineInstaller`（+~127MB）。

12.2 的替身联调之后，Windows 侧已用**真实资源出过真包并装过**（同机 Win10 x64）：
`prepare-resources.sh win-x64` 全量产出 2.39GB / 28 733 个文件 → NSIS 安装包 **668MB** →
静默安装 58s → 四服务起服 → 强杀零残留 → 卸载只剩 1 个运行期写出的文件。数字与逐条证据在
`scripts/build-windows.md §8.1`。这一轮又暴露三个只有出真包才会碰到的问题，已修：

- **makensis 的 2GB SOLID 上限**：资源含 Chromium 后未压缩体积 2.39GB，Tauri 模板写死
  `SetCompressor /SOLID`，32 位 makensis 并成单块映射即崩（`Internal compiler error #12345`）。
  `compression` 配置换不掉 `/SOLID`、`installerHooks` 位置太晚被 NSIS 拒绝，最后用
  `bundle.windows.nsis.template` 落一份本地模板，只改那一行。代价是逐文件压缩、丢掉 SOLID 的
  跨文件字典（含 Chromium 668MB；`SKIP_CHROMIUM=1` 走 SOLID 是 514MB），换来的是不再顶 2GB 硬线。
- **`bundle.resources` 单层 glob 不递归**：`resources/bin/*` 收不进 `chrome/` 目录，装完包里没有
  Chromium（mac 的 `.app` 同样从来没带过）。现显式补 `resources/bin/chrome/**/*`。
- **运行期 `.pyc` 写进包内**：默认行为首次运行往 `site-packages` 生成 4 057 个 `__pycache__` 文件，
  卸载器不认得 → 装机目录留残骸（macOS 上更会破坏 `.app` 签名）。现注入
  `PYTHONPYCACHEPREFIX=<app-data>/pycache`，卸载残骸降到 1 个文件。

### 12.3 Hermes 运行时随包（本轮闭环）

`prepare-resources.sh` 第 7 步用随包 Python 把 `hermes-agent[acp]==0.19.0` 装进
`resources/hermes/site-packages`（`HERMES_AGENT_SPEC` 可覆盖版本）。三个关键取舍：

- **不用 venv**：`pyvenv.cfg` 的 `home` 是构建机上的绝对路径，安装包换目录/改名后解释器
  基座即失效；`--target` 目录只靠运行时注入的 `PYTHONPATH` 定位，可随包移动。
- **不用 console script**：`hermes.exe`/`hermes` 入口 shim 写死构建机绝对解释器路径
  （与 API 服务 `python -c "... app()"` 同一种规避）。Rust `base_env()` 注入
  `HERMES_CLI_PATH=<随包 python>`、`HERMES_CLI_ARGS=-c "from hermes_cli.main import main; main()"`、
  `HERMES_SITE_PACKAGES`；Python 侧 `HermesSdkThread` 把 args 插在 `acp` 子命令之前。
- **Windows 子进程 env 补齐**：Hermes 启动期解析默认数据目录会调 `Path.home()`；裸 env 下
  直接 `RuntimeError: Could not determine home directory`。`_child_runtime_env()` 现补
  `SYSTEMROOT` 等基础变量，并把 `USERPROFILE`/`LOCALAPPDATA`/`APPDATA`/`TEMP` 全部收敛到
  该用户工作区（既满足解析，又保持多用户隔离）。
- **技能/插件软链兜底**：Windows 非管理员/未开开发者模式建符号链接会 WinError 1314；旧实现
  只打 warning，装机版 Hermes 会「启动正常但技能/插件全空」。现退化为带内容指纹的托管副本
  （`.aipdd-managed-copy`），源升级后下次 spawn 自动刷新，用户自建同名目录仍不覆盖。

本机（Win10 x64）验证：随包解释器 + `--target` 目录跑 `hermes acp --version` 输出 `0.19.0`，
`acp --check` 输出 `Hermes ACP check OK`；以 `hermes_sdk` 相同的 argv/env 拉起真实 ACP
`initialize` 握手，19.2s 返回 `agentInfo: {name: hermes-agent, version: 0.19.0}`。

**真机装机验收（2026-10-20，Win10 x64，含 Hermes 的 2.2.1 重出包）**：

- 安装包 738,525,546 B（704.3 MB），sha256 `407cc992c7d552debfaf13c59f19f6aa745f56cf7d9d9d5e4d1f11ef38505f0b`，
  比不含 Hermes 的旧包大约 +21 MB。包内 `resources/hermes` 5,712 个文件 / 98.7 MiB；
  资源总计 34,715 个文件，装机后 34,717（＋壳 exe、卸载器，不多不少）。
- 静默安装 57.6s；启动后 11.6s 四端口全开（`:8080`/`:3000`→200，`:9001`/`:8780`→404 属预期）。
- 用**装机目录内**的随包 Python + `resources/hermes/site-packages` 按桌面端注入的同一组 env
  拉起 ACP `initialize`，13.0s 返回 `agentInfo: hermes-agent 0.19.0`；
  `novelvideo.chat.hermes_pool.is_hermes_backend_available()` 在装机副本上判 `True`。
- 真实浏览器打开 `http://127.0.0.1:8080`：`#root` 完整渲染落地页（标题与正文可见）；启动瞬间的一次
  `/api/v1/config` 502 在 API 就绪后消失，匿名会话只剩媒体目录接口的 401（预期）。
- 强杀桌面壳：安装目录派生进程 0、四端口全释放，宿主另一套 nginx 的 21 个进程未受影响。
- 干净口径卸载（先清空历史残骸再装）：退出码 0，残骸仅
  `resources/app/services/vectcut/config.json` 1 个文件（VectCut 首启运行期写入），
  无 `__pycache__`、无 Hermes、无 dist 残骸。

macOS 的安装逻辑同源（`prepare-resources.sh mac-arm64|mac-x64`），但**真机 dmg 验收仍需在 Mac 上执行**。

## 13. 故障排查（Gatekeeper / SmartScreen / 启动失败）

- **「已损坏 / 无法验证开发者」**（macOS）：未签名或未公证。本机自测可 `xattr -dr com.apple.quarantine "<App>"`；
  对外分发必须走 `mac-sign-notarize.sh` 完成签名 + 公证 + staple（脚本公证默认关闭，需显式 `NOTARIZE=1`才会向 Apple 提交）。
- **「Windows 已保护你的电脑 / 未知发布者」**（Windows）：未签名安装包的 SmartScreen 拦截。本机自测点
  「更多信息 → 仍要运行」；对外分发必须按 `win-sign.ps1` 两段式签名（先内嵌 PE、后安装包）。
- **装机目录过长导致 Python 依赖找不到**：`python\Lib\site-packages` 深层路径叠加长安装目录会顶到
  260 字符上限；用短目录（如 `D:\DramaClaw`）或开启「Win32 长路径」组策略。
- **端口被占用**：web 端口（默认 8080）被占时**会自动避让到 8081…8089**，无需手动处理；只有 8080–8089 全被占、或非 web 服务端口（8780/3000/9001）被占时才会报错。报错多为仍在运行的 Docker/Colima 旧栈占用，先 `docker-compose -f docker-compose.aipdd.yml down` 或关掉占用进程；启动卡会转琥珀色错误态并给出提示（诊断窗口另有「端口健康」一键释放与重启回默认，见下）。
- **某服务一直「启动中/异常」**：启动卡在 180s 超时后报错，点「查看详细信息」进诊断窗口看该服务日志（红色为 stderr）。FastAPI 首次冷启动（import 依赖）可能较慢。
- **日志位置**：`app-data/logs/{nginx,gateway,vectcut,api}.log` + `app-data/nginx/{access,error}.log`；诊断窗口底部「复制日志目录路径」。
- **主窗口打不开**：服务未全就绪时主窗口不会出现，启动卡也就不会自动关闭；先按卡上提示解决失败服务，
  或在诊断窗口点「打开主界面」/单服务「重启」。
- **启动卡没有「×」以外的关闭方式**：它是无边框窗口，右上角「×」即整程序退出（走 `app.exit`，
  会逆序回收已拉起的子进程，不留端口）。

### 13.1 端口健康与一键释放（诊断窗口）

诊断窗口（`panel/console.html`）里的「端口健康」块，数据来自 `port_health`，按 `portadmin::WATCH_PORTS`
这份清单逐个端口列一行：`:8080 Nginx 反代 / 前端`、`:3000 newapi 网关`、`:8780 FastAPI 主服务`、
`:9001 VectCut 剪辑`。每行三态：绿点 `运行中`（我方受管 pid 在监听）、灰点 `空闲`、红点 `被外部占用`
并带占用者 `pid(程序名)`。占用者由 `netstat -ano` + `tasklist`（Windows）/ `lsof -nP -iTCP:<port> -sTCP:LISTEN`
（Unix）解析而来，解析逻辑是纯函数（`parse_netstat` / `parse_lsof`）并有单测。

三个动作按钮：

- **重新检测** → `port_health`，只读。
- **释放占用端口** → `release_ports`：对这四个端口上 LISTEN 的进程，**先剔除我方受管 pid**（`managed_pids()`），
  剩下的外部进程才 `kill_pid`（Unix 先 SIGTERM 再 SIGKILL，进程已消失算成功）。返回每端口
  `{port, killed[], failed[]}`，汇总成一行写进日志区。不会去杀受管服务，也不会碰这四个端口之外的进程。
- **重启回默认** → `restart_to_default`：先做一遍上面的释放，再 `restart_all()`（其内部 `reset_web_port()`
  重新从 8080 起选端口，站点因此收回 8080），最后把主窗口 `navigate` 到 `http://127.0.0.1:<新端口>`；
  主窗口还没开时直接开一个。用于「避让到 8081 之后想把 8080 抢回来」。

两个已知边界（都不影响启动，只影响提示与观测）：

- 避让发生时，启动卡上那行「端口 8080 被占用，站点本次改用 8081…」会在同一个同步块里被紧随其后的
  自检文案覆盖，用户实际看不到；功能正常，仅提示不可见。
- `port_health` 列的是**默认端口**，不跟随避让后的实际端口：nginx 已跑到 8081 时，`:8080` 那行仍显示
  真正的 8080 占用者为「被外部占用」，而 8081 上我方 nginx 不出现在清单里。

---

## 14. 与既有 Docker 栈的关系

本桌面方案与仓库现有 `docker-compose.aipdd.yml` **并存**、互不影响：compose 用于服务器/自托管，
桌面方案用于「开箱即用」本地分发。二者共用同一份前端源码与后端业务代码（零改动），差异仅在
「后端如何被拉起与喂路径」。
