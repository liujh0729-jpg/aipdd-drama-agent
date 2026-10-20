# Windows 桌面端打包说明（NSIS EXE）

> 与 macOS 分属**独立构建流水线**：便携 Python、静态二进制都按 `win-x64` 单独准备，
> 不可复用 mac 产物。本文件对应需求 3.4/3.5/5.3「Windows 打包说明 + exe 签名示例命令」。
>
> 本文所有路径与二进制布局均已在真实 Windows x64（Win10 21H2+，MSVC 工具链）上按
> `desktop/src-tauri` 的 Windows 代码分支核对过；文末「本轮已实测」列出证据。

## 1. 构建机准备（Windows 10 21H2+ / Windows 11）

- Visual Studio 2022 Build Tools（含 "Desktop development with C++"，提供 link.exe / 窗口子系统）。
- Rust（rustup，MSVC 工具链默认）：`winget install Rustlang.Rustup`。
- Tauri CLI：`cargo install tauri-cli --version "^2"`（或 `npm i -D @tauri-apps/cli` 走 `npx tauri`）。
- Git Bash（prepare-resources.sh 是 bash 脚本；MSYS 自带 `tar`/`unzip`/`curl`）。
- Node.js LTS + pnpm（构建前端 dist）、bun（构建网关内嵌 web/dist）。
- **Go ≥ 1.25.1**（`third_party/dramaclaw-gateway/go.mod` 的 `go` 指令就是 1.25.1；静态编译网关，
  `CGO_ENABLED=0` 纯 Go 编译，**不需要** C 工具链）。低于该版本时 `GOTOOLCHAIN=auto` 会尝试
  去 `proxy.golang.org` 自动下载 1.25.1 工具链——在直连不通的网络里这一步会失败，所以
  要么装够新的 Go，要么显式给 `GOTOOLCHAIN=local`。免管理员的做法是解压 zip 版：
  `go1.27.2.windows-amd64.zip`（约 75MB）解到任意目录后把 `<dir>\bin` 加进 PATH 即可。
- uv（按 `uv.lock` 冻结预装 Python 依赖）。
- Microsoft Edge WebView2 Runtime：Win10 21H2+ / Win11 随 Edge 内置；缺失时安装向导会静默补装（见 §4）。
- Windows SDK（含 `signtool.exe`）：只有对外分发签名时才需要。

### 1.1 两个只在真机才会暴露的坑（本仓库已在脚本里处理）

1. **Git 的 `link.exe` 会遮住 MSVC 的 `link.exe`**。`<Git>\usr\bin\link.exe` 是 coreutils 的
   「创建硬链接」工具；PATH 上它排在 VS 之前时，Rust 的**最终链接阶段**（`cargo build` 编依赖
   时不报，只有产出 exe 时才炸）会拿到一个莫名其妙的语法错误。
   `build-windows.ps1` 现在无条件先 `Import-MsvcEnv`：用 `vswhere.exe` 定位 VS，跑
   `vcvars64.bat` 并把它的环境变量回灌进当前 PowerShell，然后**校验 link.exe 确实解析到
   `VC\Tools\MSVC\*\bin\Host*\x64\link.exe`**，不是就直接失败退出，不留到半夜编完才发现。
2. **代理不通时 Go 模块源要换**。`proxy.golang.org` / `sum.golang.org` 在本机所在网络是 TLS
   拦截后直接失败的，需要 `GOPROXY=https://goproxy.cn,direct`；`GOSUMDB=off` 只关掉在线核验，
   仓库里的 `go.sum` 仍会做本地哈希校验，完整性不受影响。

> 只缺 Go/bun 时也能出包：先用其它机器编好网关，出包时给
> `prepare-resources.sh` 传 `NEWAPI_BIN=<new-api.exe 路径>` 跳过 Go 构建（同 `NGINX_BIN`/`FFMPEG_BIN` 的思路）。

## 2. 准备资源（win-x64）

在仓库根打开 Git Bash：

```bash
bash desktop/scripts/prepare-resources.sh win-x64
```

产物落在 `desktop/src-tauri/resources/`：

```
resources\
  python\python.exe              便携 Python（python-build-standalone win-x64 install_only）
  python\Lib\site-packages\      打包期离线预装的全部 CE 依赖（用户端零 pip）
  python\Scripts\                console script 入口
  bin\{nginx.exe,new-api.exe,ffmpeg.exe,ffprobe.exe,uv.exe,chrome\}
  app\dist\                      vite 产出的前端站点（nginx 托管）
  app\src\novelvideo\            后端源码副本（PYTHONPATH）
  app\services\{vectcut,faceswap}\   随包服务本体（换脸边车引擎 server.py 在此）
  nginx\mime.types
```

「用户端零 pip」只约束**主后端**依赖（上面那行 site-packages 预装）。换脸直播这类可选引擎按设计
在**运行期**自助安装：用随包的 `bin\uv.exe` 在 `<状态目录>\faceswap\venv` 建独立 venv 再装依赖，
既保住 AGPL 隔离的进程边界，也不污染主解释器。`build-windows.ps1` 的前置校验把
`bin\uv.exe` 与 `app\services\faceswap\server.py` 列为必需资源，缺了就直接失败退出——否则会出一个
「设置 → 环境安装」点了必炸的包。

**与 macOS 的两个布局差异**（Rust 侧 `env.rs::python()` / `python_path_dirs()` 与
`prepare-resources.sh` 使用同一套约定，改一处必须同时改两处）：

| | Unix/mac | Windows |
|---|---|---|
| 解释器 | `python/bin/python3` | `python\python.exe`（**install_only 包没有 `bin/` 这一层**） |
| site-packages | `python/lib/python3.12/site-packages` | `python\Lib\site-packages` |
| PATH 前置 | `python/bin` | `python` + `python\Scripts` |

可选开关（与 mac 相同）：`INSTALL_WORLD=1`（torch，体积剧增）、`INSTALL_RENDERER=1`（本地 HyperFrames producer）、
`SKIP_CHROMIUM=1`、`NGINX_VER=`、`FFMPEG_BIN=`、`NEWAPI_BIN=`、`PBS_URL=`/`PBS_RELEASE=`。

下载源事实核对（2026-10 在真实 Win10 x64 构建机上逐项跑通）：
- nginx 官方 win zip（`nginx.org/download/nginx-<ver>.zip`）内**只有 `nginx.exe`，无附带 DLL**（PCRE/OpenSSL/Zlib 已静态链接），可直接自包含分发。
- FFmpeg 走 BtbN/FFmpeg-Builds 的 **`ffmpeg-master-latest-win64-lgpl.zip`**（LGPL 变体，避免 x264 等 GPL 组件的额外源码义务，见 `licenses/README.md`）；脚本同时取 `bin\ffmpeg.exe` 与 `bin\ffprobe.exe`——后端有 63 处 ffprobe 调用，**两者必须同包**。
- **便携 Python 的资产名里 `+` 已被 GitHub API 百分号编码成 `%2B`**（`cpython-3.12.15%2B20261009-x86_64-pc-windows-msvc-install_only.tar.gz`）。解析正则原来只匹配字面 `\+`，于是「解析不到 URL」被误报成“疑似 API 限流”；现在 `(%2B|\+)` 两种都收。mac 分支同一条正则，同样受影响。
- **Chrome for Testing 的平台目录名只认小写**（`win64` / `mac-x64` / `mac-arm64`），写成 `Win64` 时 `storage.googleapis.com` 直接 404。解包后内层还有 `chrome-<平台>/` 一层，落位为 `bin\chrome\chrome.exe`；Git Bash 自带的 Info-ZIP unzip 6.00 **没有 `--strip-components`**，只能解到 `bin/` 再同级改名（跨盘 `cp -R` 在 MSYS 下拷 300 个文件要十几分钟）。

## 3. 构建 NSIS 安装包

```powershell
cd desktop
powershell -ExecutionPolicy Bypass -File scripts\build-windows.ps1
```

脚本做四件事：导入 MSVC 环境（§1.1）→ 构建机/资源自检 → `cargo tauri build --target x86_64-pc-windows-msvc --bundles nsis` →
打印安装包绝对路径、体积、sha256（并写同名 `.json` sidecar 供 CI 取用）。

- `--bundles nsis` **不能省**：`tauri.conf.json` 的 `bundle.targets` 是给 mac 用的 `["app","dmg"]`，
  Windows 上不带该参数会去构建 dmg 而失败。mac 流水线保持原样，两端共用一份配置。
- 装到 Program Files（需管理员）：`-InstallMode perMachine`；让用户二选一：`-InstallMode both`。
- 默认 `installMode=currentUser`，免管理员权限、写 HKCU，产物在
  `src-tauri\target\x86_64-pc-windows-msvc\release\bundle\nsis\AIPDD DramaClaw_<版本>_x64-setup.exe`，
  含桌面快捷方式、开始菜单项（目录 `AIPDD`）、卸载程序 —— 均由 Tauri 内置 NSIS 模板提供。

### 3.1 NSIS 的 2GB SOLID 数据块上限（实测踩过，已用本地模板分叉绕开）

内置资源齐（含 Chromium）时未压缩体积实测 **2 387 877 853 B ≈ 2.39GB**。Tauri 模板写的是
`SetCompressor /SOLID lzma`，SOLID 要把全部数据并成**一个**数据块，而 `makensis` 是 32 位程序，
映射 >2GB 单块失败，报错长得完全不像体积问题：

```
Internal compiler error #12345: error mmapping datablock to 22190506.
Error failed to bundle project: `Failed to bundle app with makensis`
```

三条尝试过的路子，只有第三条成立：

1. `bundle.windows.nsis.compression` —— 只能换算法（zlib/bzip2/lzma/none），换不掉 `/SOLID`。
2. `bundle.windows.nsis.installerHooks` —— 模板里 `!include "{{installer_hooks}}"` 的位置在
   所有 `File` 之前，看着正合适，但已在 `!include MUI2.nsh` 等之后，NSIS 拒绝再动压缩器：
   `can't change compressor after data already got compressed or header already changed!`。
3. **`bundle.windows.nsis.template = "windows/installer.nsi"`**（采用）：把上游模板落到仓库，
   只把 `SetCompressor /SOLID "{{compression}}"` 改成 `SetCompressor "{{compression}}"`。
   `desktop/src-tauri/windows/installer.nsi` 文件头记录了上游版本、sha256 与行数，
   实质差异用 `diff` 验证过只有这一行；文件保持 ASCII（makensis 对无 BOM 脚本按系统代码页解码）。

代价与收益（同机同输入实测）：

| 方案 | 未压缩数据 | 安装包 | 结果 |
|---|---|---|---|
| SOLID lzma + 含 Chromium | 2.39GB | — | makensis 崩溃，出不了包 |
| SOLID lzma + `SKIP_CHROMIUM=1` | ≈1.93GB | 514MB | ✅ 也成立，但包里没有 Chromium |
| 非 SOLID lzma + 含 Chromium（模板分叉） | 2.39GB | **700,438,828 B** | ✅ 本轮交付形态（单独手跑 makensis 得 695,430,436 B，差的是壳 exe 与头） |

结论：**2GB 是 SOLID 的硬线**。即使裁掉 Chromium 回到 1.93GB，余量也只有 8%——依赖再涨就会
再次出不了包，所以模板分叉不是权宜之计。要缩体积的正当手段是 `SKIP_CHROMIUM=1`
（Chromium 目前只是随包、编排器还没把它接成受管服务，见 `desktop/README.md §12`），
而不是删 Python 依赖。

### 3.2 `bundle.resources` 的单层 glob 不递归目录

`resources/bin/*` 只收目录下的**文件**，不会把 `chrome/` 这层目录整个搬进去——于是 Chromium
在 Windows 上装完就没影（`resources\bin\` 里只有 4 个 exe）。这条对 mac 同样成立：
`.app` 里从来就没有过 `bin/chrome`，只是没人装过包去核对。现已显式补
`"resources/bin/chrome/**/*"`；今后往 `resources/bin/` 放**目录**时必须同步加一条 `**/*`。

手动执行等价命令（排障时用）：

```powershell
cd desktop
cargo tauri build --target x86_64-pc-windows-msvc --bundles nsis
```

## 4. WebView2 与「用户零依赖」

`tauri.conf.json > bundle.windows.webviewInstallMode = embedBootstrapper`（静默）：
Win10 21H2+/Win11 一般已随 Edge 内置 WebView2 Runtime，缺失时安装向导自动补装，
用户全程无需手动装东西。三种模式的取舍：

| 模式 | 安装包增量 | 装机时是否要联网 |
|---|---|---|
| `downloadBootstrapper`（Tauri 默认） | ~0 | 需要（仅当 runtime 缺失） |
| **`embedBootstrapper`（本项目采用）** | ~1.8 MB | 需要（仅当 runtime 缺失） |
| `offlineInstaller` | ~127 MB | 完全离线 |

内网/无外网交付场景改 `offlineInstaller`，代价是包体积 +127MB。

## 5. 代码签名（降低 Defender / SmartScreen 误报）

签名脚本：`scripts\win-sign.ps1`（对应 mac 的 `mac-sign-notarize.sh`）。
证书需单独采购，与 Apple Developer ID 是两套。

```powershell
$env:CODESIGN_PFX     = 'D:\certs\aipdd-code.pfx'   # 或用 -Thumbprint 走本地证书存储
$env:CODESIGN_PFX_PWD = '****'                       # 口令只走环境变量，绝不入库

# ① 构建前：签 resources 内全部内嵌 PE（nginx/new-api/ffmpeg/chrome + 便携 Python 的 exe/dll/pyd）
powershell -ExecutionPolicy Bypass -File scripts\win-sign.ps1 -Stage Resources -DryRun   # 先看清单
powershell -ExecutionPolicy Bypass -File scripts\win-sign.ps1 -Stage Resources

# ② 出包
powershell -ExecutionPolicy Bypass -File scripts\build-windows.ps1

# ③ 构建后：签最终安装包
powershell -ExecutionPolicy Bypass -File scripts\win-sign.ps1 -Stage Installer `
  -Installer 'src-tauri\target\x86_64-pc-windows-msvc\release\bundle\nsis\AIPDD DramaClaw_2.1.7_x64-setup.exe'

# 验收
signtool verify /pa /v "…\AIPDD DramaClaw_2.1.7_x64-setup.exe"
```

**顺序不能颠倒**：NSIS 打进去的是资源的**副本**，出包后再签 `resources\` 不会作用到包内，
包内文件也无法补签。已带官方签名（`signtool verify /pa` 通过）的 Python DLL 会被跳过，
不破坏其原始签名链。

也可让 Tauri 在打包链路里自动签内层二进制：
`tauri.conf.json > bundle.windows.signCommand = { cmd = "powershell", args = [...] }`（`%1` 为待签文件），
或配 `certificateThumbprint` + `timestampUrl` 由 Tauri 自己调 signtool。开发期保持不签名。

要点：
- 内嵌 PE 数量随依赖增长（裸便携 Python 58 个；装完 CE 依赖可达数百），逐个签名耗时按几秒/个估算。
- 未签名的便携 Python + 大量 `.exe` 极易被 Defender 判可疑：**先签内嵌、再签安装包**。
  SmartScreen 信誉要累计下载量，新证书初期仍可能弹提示，属正常。
- 组织内可另配 Group Policy 白名单 / 向 Microsoft 提交误报申诉。

## 6. Windows 侧已实现的关键机制（供核对）

1. **进程树回收**（`src/proc_win.rs`）：**每个服务一个 Job Object**（`JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`），
   而不是全局一个作业。原因：单服务重启只结束顶层 pid，若共用作业，nginx worker /
   uvicorn 子进程 / ffmpeg / chrome 会留在作业里继续占端口，重启必然探活超时。
   App 被强杀或崩溃时，作业句柄随进程销毁 → 系统连带结束作业内全部进程，这是
   「杜绝后台残留进程占用端口」的兜底。回归测试：`cargo test -- --ignored`（`proc_win::tests`）。
2. **无控制台黑框**：子进程统一带 `CREATE_NO_WINDOW` 创建（GUI 壳拉起 console 程序否则会闪黑窗）。
3. **nginx 配置路径**（`env::nginx_path_str` + `orchestrator::prepare_nginx_conf`）：写进
   `nginx.conf` 的路径值一律转成**正斜杠**——nginx 在 Windows 把反斜杠当转义符
   （`C:\Users\x` 会被读成 `C:Usersx`）；`-p` 前缀参数仍用原生反斜杠路径。
4. **日志编码 + bytecode 落点**（`logs.rs` + `env::base_env`）：Python 服务注入 `PYTHONUTF8=1`/
   `PYTHONIOENCODING=utf-8`；读管道改为按字节取行，非 UTF-8 时回落 `GetOEMCP` 代码页解码
   （中文 Windows 上 nginx.exe 等原生程序按 cp936 输出）。旧实现 `read_line` 遇非 UTF-8 会
   直接结束读线程，既断日志又会因管道写满把子进程卡死。另注入
   `PYTHONPYCACHEPREFIX=<app-data>/pycache`，把运行期 `.pyc` 全挡在包外——包内资源视为只读，
   否则首次运行会往 `site-packages` 写几千个 `__pycache__`，Windows 上留卸载残骸、
   macOS 上直接破坏 `.app` 签名。
5. **自检**（`env::missing_required_bins`）：按平台检查 `python.exe` / `nginx.exe` /
   `new-api.exe` / `ffmpeg.exe` / `app\dist`，缺件即面板红字提示。

## 7. 已知风险（Windows 专项）

- **长路径**：`python\Lib\site-packages` 深层目录 + NSIS 解包路径可能超 260 字符。
  建议装机目录用短路径（如 `D:\DramaClaw`），或开启组策略「启用 Win32 长路径」；
  构建机可 `git config --global core.longpaths true`（若仓库在 NTFS 长路径下）。
- **后端自起的孙进程**：`CREATE_NO_WINDOW` 只作用于桌面壳直接拉起的进程；后端
  （novelvideo）内部用 `subprocess` 起 ffmpeg/chrome 时若不带该标志，仍可能闪窗。
  属后端适配项，不在本次桌面壳改动内。
- **Chromium 目录**数百 MB；不需要抖音 CDP 时 `SKIP_CHROMIUM=1`。CDP 接线仍是未决项（README §12）。
- **杀软扫描**：NSIS 包内上千个文件在安装时被实时扫描，首装明显变慢；签名可显著缓解。
- **首次运行数据**：app-data 落在 `%APPDATA%\work.aipdd.dramaclaw`，卸载器默认不删
  （模型/产物/日志），需要彻底清理时手动删该目录。
- **包内 `.pyc`**：`prepare-resources.sh` 里 `uv sync` 之后，`resources/` 已带 158 个
  `__pycache__`（1 193 个 `.pyc`）。它们是**构建产物、随包分发**，卸载器认得了、能删干净
  （实测装机 28 735 个文件，卸载后不剩这些）。运行期新生成的 bytecode 走
  `PYTHONPYCACHEPREFIX`（§6.4）进 app-data，两者不要混为一谈。
- **VectCut 往包里写配置**：首次运行会在
  `resources\app\services\vectcut\config.json` 落一份配置（该文件不在仓库里）。Windows 上
  它成为卸载后**唯一的残骸**（1 个文件），macOS 上则是 `.app` 内的写入、会让签名失效。
  正解是让 VectCut 把配置写到 `APP_DATA_PATH`，属后端适配项，不在本次桌面壳改动内。

## 8. 本轮已实测（真实 Windows x64，Win10 21H2+ / MSVC 工具链）

### 8.1 真件全链路：prepare-resources → NSIS → 安装 → 起服 → 强杀 → 卸载

被测对象是 **正式产物**（不再是上一轮的联调替身）：`prepare-resources.sh win-x64` 全量产出 +
`build-windows.ps1` 出的安装包 + 装完后的真实运行。

| 项 | 结果 |
|---|---|
| `prepare-resources.sh win-x64`（真件） | 6 步全通过。产出 `resources/`：python 27 372 个文件 796MB、app 1 046 个 150MB、bin 312 个 544MB（nginx/new-api 119MB/ffmpeg+ffprobe 各 138MB/chrome 目录）＝**28 733 个文件、未压缩 2.39GB** |
| 便携 Python | `cpython-3.12.15+20261009-x86_64-pc-windows-msvc-install_only.tar.gz`（脚本按 §2 的 `%2B` 解析后取到），`python\python.exe -V` 正常 |
| Go 网关 | `new-api.exe` 119,952,896 B；装后启动日志 `New API ready in 277ms` |
| 前端 | `pnpm build` → `resources/app/dist`（`index.html` 3,020 B） |
| Chromium | Chrome for Testing 按 §2 的小写 `win64` 取到并整目录进 `bin/chrome`（307 个文件） |
| `build-windows.ps1` 出包 | 退出码 0。`AIPDD DramaClaw_2.1.7_x64-setup.exe` **700,438,828 B（668MB）**，sha256 `264f15a7b91e87a11400bbe94ee528337d61c881e0f118b611bbd73a7eaeca0c`，同名 `.json` sidecar 已写 |
| 静默安装 `/S` | 退出码 0，**58s**（Defender 实时扫描参与下），装到 `%LOCALAPPDATA%\AIPDD DramaClaw`，共 **28 735 个文件**＝资源的 28 733 ＋壳 exe ＋卸载器，**不多不少** |
| 包内关键件 | `dramaclaw-desktop.exe`、`resources\python\python.exe`、`bin\{nginx,new-api,ffmpeg,ffprobe}.exe`、**`bin\chrome\chrome.exe`（4,537,856 B）**、`app\dist\index.html` 全在——§3.2 那条 `resources/bin/chrome/**/*` 在真包里验证生效 |
| 桌面集成 | 快捷方式、开始菜单 `AIPDD` 目录、HKCU 卸载项均生成 |
| **起服（真件）** | 启动后约 10s 内四端口全开：`:8080`→200、`:3000`→200、`:9001`→404、`:8780`→404（后两个根路径本就无路由，就绪判定走 TCP 探活；`/api/v1/config` 实测 200） |
| 窗口 | WebView2 正常渲染：启动卡（标题 `AIPDD DramaClaw`，无边框 660×380，只有品牌+版本+百分比+阶段文字+`AIPDD算力平台：www.aipdd.work`，窗口底色与页面一致不闪白）→ 百分比按真实阶段进度爬，就绪后自动开主窗口并关掉启动卡；诊断窗口（标题 `AIPDD DramaClaw · 服务状态`）仅在启动失败、点「查看详细信息」时出现 |
| 强杀桌面壳（`Stop-Process -Force`，等价崩溃） | 安装目录派生进程 **0 残留**，8080/3000/9001/8780 全部释放；宿主上另一套宝塔 nginx 的 21 个进程**没被误杀** |
| 卸载 `uninstall.exe /S` | 退出码 0，9s。快捷方式/开始菜单/HKCU 项全部移除，app-data 按设计保留（§7） |
| **卸载残骸** | 只剩 **1 个文件**：`resources\app\services\vectcut\config.json`（VectCut 首启往自己包里写配置，见 §7）。上一轮的 4 057 个 `__pycache__` 残骸已归零——`PYTHONPYCACHEPREFIX` 生效后运行期 4 736 个 `.pyc` 全落在 `app-data\pycache` |

### 8.2 上一轮的替身联调结论（保留备查）

上一轮没有 Go/bun，装不了真网关，所以桌面壳用 `--no-bundle` 出 exe 后直接实跑，被测资源是
真 `nginx.exe` + 真便携 Python + 两个替身（40 行 Rust 假 `new-api.exe`、打印中文的 stdlib
HTTP 服务假 `aipdd_server.py`）。其中**只有 8.1 覆盖不到的项**留在这里：

| 项 | 结果 |
|---|---|
| `cargo check` / `cargo clippy`（Windows 分支全开） | 通过，零告警 |
| 渲染后的 `app-data/nginx/nginx.conf` | 路径全部正斜杠且无 `\\?\` 前缀；`include`/`root`/`pid` 均生效（nginx 正常起服） |
| 日志采集 | `logs/gateway.log` 里故意发的 **GBK 字节行**被正确解码成中文（读线程不再中断）；`logs/vectcut.log` 收到便携 Python 的 UTF-8 中文与 `sys.executable` 路径 |
| 失败路径 | 后端依赖未装时 `logs/api.log` 完整收到 Python traceback；缺 `new-api.exe` 时自检直接拦下 |
| `proc_win` 进程树回归：`terminate_tree` 连带杀孙进程、`shutdown_all` 清残留 | `cargo test -- --ignored` 2 passed |
| nginx 官方 win zip 自包含性 | 实测包内仅 `nginx.exe`，无 DLL 依赖 |
| `win-sign.ps1` 待签清单枚举 | `-Stage Resources -DryRun` 列出便携 Python 内 58 个 PE |

### 8.3 本轮（Hermes 随包）装机实测（2026-10-20）

在 8.1 的同一台 Win10 x64 上，用含 `resources/hermes` 的 2.2.1 重出包复跑装机链路。为排除
历史安装残骸干扰，先把已卸载目录整个清空，再全新安装。

| 项 | 结果 |
|---|---|
| `build-windows.ps1` 出包 | 退出码 0；`AIPDD DramaClaw_2.2.1_x64-setup.exe` **738,525,546 B（704.3 MB）**，sha256 `407cc992c7d552debfaf13c59f19f6aa745f56cf7d9d9d5e4d1f11ef38505f0b`（不含 Hermes 的旧包 683 MB） |
| Hermes 载荷 | `resources/hermes` **5,712 个文件 / 98.7 MiB**；资源总计 **34,715** 个文件，装机后 **34,717**（＋壳 exe、卸载器） |
| 静默安装 `/S` | 退出码 0，**57.6s** |
| 起服 | **11.6s** 四端口全开：`:8080`→200、`:3000`→200、`:9001`→404、`:8780`→404（后两个根路径无路由，就绪判定走 TCP 探活） |
| 装机内 ACP | 用装机目录内的 `resources/python/python.exe` + `resources/hermes/site-packages`，按 `base_env()` 同款 argv/env 发 `initialize`，**13.0s** 返回 `agentInfo: {name: hermes-agent, version: 0.19.0}` |
| 可用性判定 | 装机副本上 `is_hermes_backend_available()` == **True**，`_hermes_cli_args()` 解析出 `['-c', 'from hermes_cli.main import main; main()']` |
| 浏览器验收 | 真实浏览器打开 `http://127.0.0.1:8080`，`#root` 完整渲染落地页；启动瞬间一次 `/api/v1/config` 502 在 API 就绪后消失，匿名会话仅 `media-catalog` 401（预期） |
| 强杀桌面壳 | 安装目录派生进程 **0**、四端口 **0 监听**；宿主另一套 nginx 21 个进程未受影响 |
| 卸载 `uninstall.exe /S` | 退出码 0，9.2s；干净口径残骸 **1 个文件**：`resources\app\services\vectcut\config.json`（运行期写入，与 8.1 一致）；无 `__pycache__` / Hermes / dist 残骸 |
