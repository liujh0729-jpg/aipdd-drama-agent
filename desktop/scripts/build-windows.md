# Windows 桌面端打包说明（NSIS EXE）

> 与 macOS 分属**独立构建流水线**：便携 Python、静态二进制都按 `win-x64` 单独准备，
> 不可复用 mac 产物。本文件是脚手架 + 命令清单，实际构建需在真实 Windows 机器上执行验证。

## 1. 构建机准备（Windows 10 21H2+ / Windows 11）

- Visual Studio 2022 Build Tools（含 "Desktop development with C++"，提供 link.exe / 窗口子系统）。
- Rust（rustup，MSVC 工具链默认）：`winget install Rustlang.Rustup`。
- Tauri CLI：`cargo install tauri-cli --version "^2"`。
- Node.js LTS + npm（构建前端 dist）。
- Go 1.22+（静态编译网关）、uv（预装 Python 依赖）、7-Zip（可选解包）。
- Microsoft Edge WebView2 Runtime：Win10 21H2+ / Win11 一般内置；否则安装 `Evergreen` bootstrapper。

## 2. 准备资源

在仓库根（含 `desktop/` 的机器）打开 PowerShell：

```powershell
# 让 prepare-resources 感知 win-x64（脚本内部按 GOOS=windows 处理 .exe 后缀）
bash desktop/scripts/prepare-resources.sh win-x64
```

产物落在 `desktop/src-tauri/resources/`：`python\`、`bin\{nginx.exe,new-api.exe,ffmpeg.exe,chrome\}`、`app\dist\`、`nginx\mime.types`。

## 3. 构建 NSIS 安装包

```powershell
cd desktop
cargo tauri build --target x86_64-pc-windows-msvc --bundles nsis
```

产物：`src-tauri\target\x86_64-pc-windows-msvc\release\bundle\nsis\AIPDD DramaClaw_<版本>_x64-setup.exe`
（含桌面快捷方式、开始菜单入口、卸载程序；`tauri.conf.json > bundle.windows.nis.installMode=currentUser`）。

## 4. 代码签名（降低 Defender / SmartScreen 误报）

购买代码签名证书后，对**内嵌全部 EXE/DLL** 与**最终安装包**分别签名（内部先签、外层后签）：

```powershell
# 遍历资源目录里的可执行逐个签（示例；证书在 PFX 或云 HSM）
Get-ChildItem -Recurse desktop\src-tauri\resources\bin -Include *.exe,*.dll | ForEach-Object {
  signtool sign /f code-sign.pfx /p $env:PFX_PWD /tr http://timestamp.digicert.com /td sha256 /fd sha256 $_.FullName
}

# 最终 NSIS 安装包签名
signtool sign /f code-sign.pfx /p $env:PFX_PWD `
  /tr http://timestamp.digicert.com /td sha256 /fd sha256 `
  "desktop\src-tauri\target\x86_64-pc-windows-msvc\release\nsis\AIPDD DramaClaw_*_x64-setup.exe"

# 校验
signtool verify /pa /v "…\AIPDD DramaClaw_*_x64-setup.exe"
```

要点：
- 代码签名证书与 Apple Developer ID 是**两套**证书，需分别采购。
- 未签名的便携 Python / 大量 .exe 极易被 Defender 判定可疑：**务必签名内嵌可执行**再签安装包；
  SmartScreen 信誉需累计下载量，新证书初期仍可能弹提示，属正常。
- 组织内可另配 Group Policy 白名单 / 提交 Microsoft 误报申诉。

## 5. 进程树清理（已实现，供核对）

Rust 侧 `src-tauri/src/proc_win.rs` 用 **Job Object**（`JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`）
把每个服务子进程指派进作业；App 退出时 `TerminateJobObject` + `CloseHandle` 连带结束
作业内**全部**进程（python/uvicorn/ffmpeg/chromium 树），杜绝端口残留。Windows API
细节较多，**首次在本机编译时重点验证 `proc_win.rs`**。

## 6. 已知风险（Windows 专项）

- nginx / ffmpeg 需为 win-x64 自包含二进制（官方 zip 可）；DLL 依赖需随附于 `resources\bin\`。
- Chromium 目录体积大（数百 MB）；如不需要抖音 CDP 可在 prepare 阶段跳过。
- 长路径（python site-packages 深目录）建议开启 Win10 长路径策略或缩短安装目录。
