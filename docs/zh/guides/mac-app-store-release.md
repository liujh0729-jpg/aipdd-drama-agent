# macOS 双轨发布：直分发 dmg 与 Mac App Store pkg

本项目 macOS 桌面端同时支持两条独立的发布链，产物、证书、授权清单、终检脚本全部分道，互不覆盖。

| 维度 | Track A · 直分发 dmg | Track B · Mac App Store pkg |
|---|---|---|
| 面向 | GitHub Releases / 官网下载 / 内部部署 | App Store 上架 |
| 证书对 | Developer ID Application + Developer ID Installer | 3rd Party Mac Developer Application + Installer（新命名：Apple Distribution / Apple Installer） |
| 授权清单 | [desktop/src-tauri/entitlements.plist](../../../desktop/src-tauri/entitlements.plist) | [desktop/src-tauri/entitlements.mas.plist](../../../desktop/src-tauri/entitlements.mas.plist) |
| 沙盒 | 关（`disable-library-validation` 让便携 Python 能 dlopen 第三方 .so） | **开**（App Review 一票否决项） |
| 内嵌 Chrome for Testing | 保留（换脸/浏览器自动化依赖） | **剥除**（App Store 禁止嵌套 .app） |
| 中间产物目录 | `desktop/src-tauri/target/<triple>/release/bundle/macos/` | `desktop/src-tauri/target/<triple>/release/bundle/macos-mas/` |
| 最终产物 | `bundle/dmg/*.dmg` | `bundle/macos-mas/*.pkg` |
| 上传通道 | 自己托管 / GitHub Releases | App Store Connect（Transporter / `xcrun altool`） |
| 终检脚本 | [verify-release-dmg.sh](../../../desktop/scripts/verify-release-dmg.sh) | [verify-release-pkg.sh](../../../desktop/scripts/verify-release-pkg.sh) |

## Track A · 直分发（2.3.2 起走 universal，单包兼容 arm64 + x86_64）

两条链共用同一颗合并产物（见下文「单包双架构」一节），差别只在 Track B 要先剥 Chrome。

```bash
cd desktop

# ① 串行出两棵单架构树并合并（备料 + cargo build + lipo 合并 + 全树双架构闸门）
#    Track A 必须让 Chrome 子树一起合：Intel 用户拿到 arm64-only 的 Chrome 是
#    「装得上、一进逐帧渲染就崩」。
UNIVERSAL_MERGE_CHROME=1 ./scripts/build-macos-universal.sh

# ② Developer ID 全子树重签 → 由已签 App 打 dmg → notarytool 公证 → 钉票
#    产物按主件实测架构自动命名，双切片时是 …_universal.dmg
NOTARIZE=1 NOTARY_PROFILE=aipdd-notary \
  ./scripts/mac-sign-notarize.sh \
    src-tauri/target/universal2-apple-darwin/release/bundle/macos/"AIPDD DramaClaw.app"

# ③ 出厂终检（文件名承诺的架构必须被实测 lipo 全覆盖，universal 要求双切片）
./scripts/verify-release-dmg.sh \
  src-tauri/target/universal2-apple-darwin/release/bundle/dmg/"AIPDD DramaClaw_2.3.2_universal.dmg"
```

仍要出单架构 dmg 时（比如只为快速验证某一侧）：`prepare-resources.sh mac-arm64` →
`build-macos-dmg.sh arm64` → 同样两步签名公证，产物命名回落 `_aarch64` / `_x64`。这条链的代码路径
与 universal 共用 `mac-sign-notarize.sh`，但 per-arch 包里不含 `overlays/` 与分流 `.pth`，行为与升级前一致。

## Track B · Mac App Store

### 前置：Apple 侧的一次性准备

1. **Apple Developer Program**（$99/年），必须是 Organization 或 Individual 均可。
2. **两张证书**，均已导入钥匙串：
   - `3rd Party Mac Developer Application: <你或公司名> (<TEAM_ID>)` — Application 证书，走 codesign 策略
   - `3rd Party Mac Developer Installer: <你或公司名> (<TEAM_ID>)` — Installer 证书，走 Apple Installer 策略
   - ⚠️ **两张证书验证方式不同**（常见误判点）：
     ```bash
     # Application 证书会出现在 codesigning 策略下：
     security find-identity -v -p codesigning | grep "3rd Party Mac Developer Application"
     # Installer 证书不出现在上面（Extended Key Usage 不同），只能验公钥：
     security find-certificate -c "3rd Party Mac Developer Installer" >/dev/null && echo "公钥 OK"
     ```
     Installer 私钥在不在本机只能由 productbuild 自己报（它会写 `identity cannot be used for signing code`）。本项目的 macos-pkg-appstore.sh 已按此正确处理。
   - 若用的是 2020 年后签发的新命名 `Apple Distribution` / `Apple Installer`，两条脚本都支持，把 `SIGN_APP_IDENTITY` / `SIGN_PKG_IDENTITY` 覆盖即可。
3. **一个 Mac App Store 类型的 Provisioning Profile**：
   - 登录 [developer.apple.com/account/resources/profiles/add](https://developer.apple.com/account/resources/profiles/add)
   - 选 **Mac** → **App Store**（Distribution）
   - App ID 选 `work.aipdd.dramaclaw`（与 [tauri.conf.json](../../../desktop/src-tauri/tauri.conf.json) 的 `identifier` 一致）
   - 证书选上面那张 Application 证书
   - Profile Name 建议 `AIPDD-mas-<version>`
   - 下载 `.provisionprofile`，存本机私有路径（比如 `~/secrets/aipdd-mas.provisionprofile`），**不入库**
4. **App Store Connect 应用条目**：
   - 登录 [appstoreconnect.apple.com](https://appstoreconnect.apple.com) → 我的 App → 新建 App
   - Bundle ID 用同一个 `work.aipdd.dramaclaw`；SKU 自定义；平台勾 macOS
   - 填完基本信息（名称、主要语言、分类、年龄分级）
   - **隐私政策 URL** 必填 — 上架前你需要一个可访问的 https 页面

### 每次上架的标准动作（universal，2.3.2 起）

> ⚠️ MAS 链只能上传 **含 arm64** 的产物（ASC 已拒收无 arm64 切片的构建，报 ITMS-91167）。
> 2.3.2 起 pkg 与 dmg 共用同一颗 universal 产物，一个包同时覆盖 Intel 与 Apple Silicon；
> 纯 `x64` 产物进 pkg 链仍会被 `macos-pkg-appstore.sh` 硬拦。

```bash
cd desktop

# ① 串行备料 + 双构建 + 合并，产出 universal .app
#    pkg 链下一步就剥掉 Chrome，UNIVERSAL_MERGE_CHROME 保持默认 0 能省一大截时间
./scripts/build-macos-universal.sh

# ①ᵇ MAS 化裁剪（universal 自动等效 NO_BUILD：不重跑 cargo，只 ditto + 剥禁用子树）
NO_BUILD=1 ./scripts/build-macos-mas-app.sh universal

# ①′ 抬 build 号（Apple 要求每个上传的 CFBundleVersion 唯一）。上次因 ITMS 错误被判
#      校验失败的 build 多数不占号，但也有小概率同号重传被拒；加一位最稳。营销版本不动：
/usr/libexec/PlistBuddy -c 'Set :CFBundleVersion 2.3.2.1' \
  src-tauri/target/universal2-apple-darwin/release/bundle/macos-mas/"AIPDD DramaClaw.app"/Contents/Info.plist
# 必须在 ①ᵇ 之后、② 之前：productbuild 打的就是 macos-mas 里这份，提前改会被 cargo 重生刷掉。

# ② 用 3rd Party 证书签 + productbuild 出 pkg
PROFILE=~/secrets/aipdd-mas.provisionprofile \
  ./scripts/macos-pkg-appstore.sh \
    src-tauri/target/universal2-apple-darwin/release/bundle/macos-mas/"AIPDD DramaClaw.app"

# ③ 出厂终检
./scripts/verify-release-pkg.sh \
  src-tauri/target/universal2-apple-darwin/release/bundle/macos-mas/"AIPDD DramaClaw_2.3.2_universal.pkg"

# ④ 上传：Transporter GUI 拖 pkg 进去点"交付"；或命令行
xcrun altool --upload-app \
  -f src-tauri/target/universal2-apple-darwin/release/bundle/macos-mas/"AIPDD DramaClaw_2.3.2_universal.pkg" \
  -t macos \
  --apiKey <KEY_ID> --apiIssuer <ISSUER_ID>
```

只想重跑 ②③（没改代码、只改 entitlements / profile）时跳过 ①①ᵇ，直接对现成的
`macos-mas/*.app` 重签重打包；但 universal 合并产物一旦丢了就必须从 ① 重来，重跑 ② 不会补回切片。

`verify-release-pkg.sh` 校验的 14 项：Installer 证书链 / App 内 .app / App 主签名 / deep 签名 / 沙盒开关 / 无嵌套 .app / `embedded.provisionprofile` 合法（含 application-identifier）/ Bundle ID 与 pkg identifier 一致 / 版本一致 / 主执行件含 arm64 / **主执行件架构覆盖（`_universal.pkg` 要求 arm64 + x86_64 双切片）** / 随包 Python 架构覆盖 / profile 与签名标识符同源（90886）/ payload 隔离属性（91109）。任何一项红就修脚本重签，别硬上传——ASC 会自动预检并拒。

真机跑通这条链后发现、且已内置闸门的两个隐形来源（值得单独记一笔，因为它们都不会在构建时报错）：

- **profile 文件本身就带隔离属性**。它几乎总是浏览器从 developer.apple.com 下载来的，身上挂着
  `com.apple.quarantine`（还有 `kMDItemWhereFroms`）。pkg 脚本的清属性在注入 profile **之前**，于是修
  90886 的那个文件反而把 91109 请了回来。现在注入后会再清一次并全树复核，不过就 exit 9。
- **`services/vectcut/.venv` 被整目录误拷进包**。`prepare-resources.sh` 用 `cp -R` 拷服务目录时，把开发机
  上那个 `bin/python` 指向 `/opt/anaconda3` 的宿主 venv 一起带进产物：用户机上必然悬空、白撑 86 MB，而且
  里面全是宿主架构的 `.so`。vectcut 依赖实际装在便携 Python 里，全仓无代码引用该目录，现已剪掉。

### 单包双架构（universal）：2.3.2 起就是出货默认

MAS 上传**只要含 arm64 就能过 ITMS-91167**，所以 arm64-only 是可发的；但从 2.3.2 起两条链默认
都出 **arm64 + x86_64 的单包**。[build-macos-universal.sh](../../../desktop/scripts/build-macos-universal.sh)
就是把这件事做成可重复产物的脚本。

难点不在 Rust 侧也不在 lipo，而在**同版本 wheel 在不同平台下的文件布局差异**。2026-11 拿两棵
3.8 GiB 级的树逐项分过类，缺口只有三种性质，处置方式相反，混在一起必错：

- **可直接 union**（基底同目录里没暴露同一模块名的另一份）：cv2 / PIL 两侧 `.dylibs` 版本不同（每个
  架构的主件按自己写死的 install name 找自己那份）、pycryptodome 的 x86 专属可选加速件（`_raw_aesni`
  / `_ghash_clmul`，`_raw_api.py` 是 `except ImportError` 加载）。→ 原样补入即可。
- **模块名冲突**（同目录、同模块名、不同后缀）：实测只有 lupa——arm64 轮发 `lua54.cpython-312-darwin.so`，
  x86_64 轮发 `lua54.abi3.so`。union 等于把 Intel 侧 import 打死：CPython 按 `EXTENSION_SUFFIXES` 优先级
  只取第一个，dlopen 报 wrong architecture **不会**回退下一个后缀。→ 转 **per-arch overlay**：把整个包
  搬到 site-packages 同级的 `overlays/{arm64,x86_64}/<pkg>`，共享 site-packages 里**两份都不留**（不留就没
  有选错的余地，也不依赖 sys.path 顺序），再由 `_dramaclaw_arch_overlay.pth` 按 `platform.machine()` 把
  对应 overlay 插进 `sys.path`。随包解释器实测 `sys.flags.no_site==0`，所以这套分流是**纯随包数据，
  Rust 一行不用改**；per-arch 包里既没 `overlays/` 也没 `.pth`，两条链互不干扰。
  ⚠️ 只能用在随包 Python 的 site-packages：`hermes/site-packages` 是 PYTHONPATH 挂进来的，那种目录
  **不处理 .pth**，冲突落在那里脚本直接 exit 16 而不是猜一个路径糊过去。
- **设计上就单架构**（如 Chrome 的 `WidevineCdm/_platform_specific/mac_{arm64,x64}`）：这些不是缺陷，
  所以下游不能一刀切拒绝。脚本把它们机械生成成清单、随包落盘为
  `Contents/Resources/resources/universal-per-arch.txt`，`macos-pkg-appstore.sh` 的全树 arm64 闸门和
  `STRICT_UNIVERSAL` 都按这份清单豁免；**不在清单里的反向缺口仍然硬拦**，不要改成全部放行。

实测读数（2.3.2 两棵树）：依赖清单 262 项一致 → 合并补片 320 / 本就双架构 61 / 无对侧 27 / 异常 0；
lupa 1 个包 5 件转 overlay；49 件登记为设计上单架构。运行期不止看 lipo，两个架构各跑一次真实 import：
`arm64` 与 `arch -x86_64` 下 lupa 分别解析到 `overlays/arm64/…cpython-312-darwin.so` 与
`overlays/x86_64/…abi3.so`，9 个带原生扩展的包在两侧全部 import 通过（测时必须带
`PYTHONNOUSERSITE=1`，否则会落到本机 `~/.local` 的包上得到假失败）。

其余四条结构约束仍然成立，重试时别再踩：

- **必须串行**：`prepare-resources.sh` 开头就 `rm -rf resources/{bin,python,app}`，共享目录只留最后准备的
  架构，所以每棵 `.app` 构建完、下一次备料前要先 `ditto` 快照到 `target/.universal-stage/`。
- **依赖版本必须逐包对齐**：mac-x64 把 lancedb / cryptography 钉到仍发 Intel 轮的老版本，arm 侧默认不钉，
  脚本因此默认导出 `FORCE_X64_COMPAT_PINS=1`，合完再用 `dist-info` 清单硬比一次（不一致 exit 13）。
- **磁盘闸门**：三棵 3~4 GiB 级的 `.app` 要同时落盘，默认要求 ≥ 14 GB 余量；只跑合并（`ARM_APP`/`X64_APP`
  已就位）时 `NEED_FREE_GB=8` 就够，因为此时只需容纳输出树。
- **Chrome 不能被降级**：`mac-sign-notarize.sh` 的 deref 修复会拿 `resources/bin/chrome` 的 pristine 副本
  整棵覆盖 App 内 Chrome，而 pristine 永远是单架构的。脚本已改成先查 App 内 Chrome 主件是否已含双切片，
  是则跳过还原。这一步不报错、不拦公证、不过终检，只能靠这条判据防——**它静默把一个能跑的 universal
  包变成 Intel 逐帧渲染就崩的包**。

常用开关：`SKIP_PREPARE=arm|x64|both`、`ARM_APP` / `X64_APP`（拿现成两棵产物只做合并）、
`UNIVERSAL_MERGE_CHROME=1`（Track A 必开）、`STRICT_UNIVERSAL=0`（应急口子，产物就不是真双架构，不要用来出货）。

> `cargo tauri build --target universal2-apple-darwin` 只把 **Rust 侧**编成双架构，管不到随包资源——
> 别把它当成“已经 universal 了”。

> 尚无法本地闭死的一项：包里必然留着那 49 个“设计上单架构”件。Apple 的 ITMS-91167 到目前为止只要求
> “含 arm64”，但它是否逐文件点名，只有真实上传能给出答案；被拒就按报错回到这一节。

### Transporter 显示 `Delivered` 但后台没有这个构建版本

`Delivered` 只代表**文件上传成功**；Apple 后台的异步校验失败时不会生成构建版本（也就不出现在
【构建版本】列表、不能用于 TestFlight），失败原因走邮件下发。三类高频报错已在构建链里做成 fail-fast 闸门：

| 报错 | 成因 | 链上闸门 / 手工修复 |
|---|---|---|
| ITMS-90886 | 签名的 entitlements 缺 `application-identifier`，或与 `embedded.provisionprofile` 里的 Bundle ID 不同源（混用开发 / AdHoc profile 也报这个） | `macos-pkg-appstore.sh` 的 `-1)`：比对 profile 的 Bundle ID / application-identifier、拦掉含 `ProvisioningDevices` 的开发型 profile，并从 profile 读出同源标识符注入构建期 entitlements（通配符 `TEAM.*` 会拼成真实的 `TEAM.包名`）；签完再回读签名验一次。若已报此错：在开发者后台重建 **Mac App Store** 类型 profile（勾选 `work.aipdd.dramaclaw`）后重跑步骤 ② |
| ITMS-91167 | 包内二进制没有 arm64 切片（只编了 x86_64），Apple Silicon 无法原生运行 | `-1)` 扫描 App 内**全部** Mach-O，纯 x86_64 直接 exit 11 并提示改用 `build-macos-mas-app.sh arm64`；`arm64e` 同样算含 arm64。上轮上传的就是一个纯 x86_64 包 |
| ITMS-91109 | 包内文件带 `com.apple.quarantine` 隔离属性（下载/解压留下的，实测 `crypto.py` 中招）。**任何一个文件带标记，整包驳回** | `-2)` 在签名 / productbuild **之前**执行 `xattr -cr "$APP"` 并复核无残留；`build-macos-mas-app.sh` 在 ditto 之后先清一次（ditto 会保留扩展属性）；**注入 profile 后再清一次并全树复核**（profile 文件自身带标记，见上一节） |

手工排查（本地已有 .app 时，按顺序）：

```bash
APP="src-tauri/target/aarch64-apple-darwin/release/bundle/macos-mas/AIPDD\ DramaClaw.app"
# ① 架构自检：必须看到 arm64（或 arm64e）
lipo -archs "$APP/Contents/MacOS/AIPDD DramaClaw"
# ② 隔离属性自检：应无输出；有输出就清（必须在打 pkg 之前）
xattr -sr "$APP" | grep -E ': com\.apple\.quarantine$'
xattr -cr "$APP"
# ③ 签名标识符与 profile 是否同源
codesign -d --entitlements :- "$APP" | plutil -convert json -o - | grep -o 'application-identifier":"[^"]*"'
plutil -extract Entitlements json -o - <(security cms -D -i "$APP/Contents/embedded.provisionprofile")
```

> 隔离属性**打完 pkg 再清无效**：标记落在 payload 里，必须重出包。
> 上传后请等 Apple 的处理邮件/后台状态变为「处理完毕」，再确认构建版本出现——不要凭 `Delivered` 判断成功。

### 反复调 entitlements / profile 时省一次编译

`NO_BUILD=1 ./scripts/build-macos-mas-app.sh universal`（或 `arm64`）会跳过 cargo，仅重做 ditto + 裁剪。适合你在 [entitlements.mas.plist](../../../desktop/src-tauri/entitlements.mas.plist) 加/减权限、换 profile 后快速重出 pkg。

## 已知上架阻塞（Phase 2~4，未落地）

Track B 脚手架现在能出 pkg，但**当前架构过不了 App Review**，会踩下面这三条硬约束。每条都单独立项修复：

### ① 便携 Python 子树里 .so 非本 Team 签名

- **症状**：pkg 装到本机后，MAS 沙盒一开，`torch/*.so`、`numpy/*.so`、`onnxruntime/*.so` 全部 dlopen 报 `Code Signature Invalid`，主进程 `Killed: 9`。
- **根因**：`python-build-standalone` + pip wheel 里的 `.so` 是 Google / Anaconda / Facebook 各自的 Team 签（甚至 ad-hoc）。沙盒强制 library validation。
- **修复方案（Phase 2）**：在 [macos-pkg-appstore.sh](../../../desktop/scripts/macos-pkg-appstore.sh) 已有的 `sign_files_parallel` 之后追加一步"检测非本 Team 签名的 Mach-O 数量"，超过 0 直接 fail-fast；真正的重签在构建脚本里做——遍历 `Contents/Resources/resources/python/**` 里每个 `.so`/`.dylib`，用本 Team 证书重签（不带 hardened runtime、不带 entitlements，纯签名）。
- **备选（更长期）**：把 Python 生成/推理下沉到 Rust + XPC 服务，App 里干脆不带 Python。工程量 3–4 周。

### ② 数据目录未容器化

- **症状**：装到 MAS 后，App 想写 `~/Library/Application Support/AIPDD DramaClaw/`，沙盒拒绝。
- **根因**：MAS 沙盒只允许 App 写自己的容器 `~/Library/Containers/work.aipdd.dramaclaw/Data/…`。
- **修复方案（Phase 3）**：
  - Rust 侧的 [src-tauri](../../../desktop/src-tauri) 里所有 `app_data_dir` / `app_config_dir` 已经走 Tauri API，通常沙盒下会自动落进容器；需要逐个 check。
  - Python 侧（`src/novelvideo/config.py` 之类）硬编码的 `~/Library/Application Support/…` 路径要改成从环境变量 / CLI 参数接收容器路径。
  - nginx / localapi / ffmpeg 的输出目录同样要参数化。
  - 提供从 dmg 版到 MAS 版的一次性数据迁移工具（可选：合并 / 让用户手动迁 / 不做迁移）。

### ③ 功能面收窄（Chrome 换脸）

- **症状**：Track B 的 [build-macos-mas-app.sh](../../../desktop/scripts/build-macos-mas-app.sh) 已经自动剥掉 `Contents/Resources/resources/bin/chrome/`，装了 MAS 版的用户点用换脸/浏览器自动化相关功能会报缺依赖。
- **产品决策**：MAS 版本要么显式隐藏这些入口，要么给出"打开浏览器手动完成"的降级路径。上架前需要与产品/文档一起把 [docs/zh/guides/face-swap-live.md](face-swap-live.md) 里的 macOS 章节改成"dmg 版 vs MAS 版功能对照"。

## 常见被拒项与应对

| Apple 拒信关键词 | 应对 |
|---|---|
| "app includes embedded executables that download and execute code" | 是便携 Python + pip 装包路径。要么在提审备注里说清"下载的都是签名版本、无 JIT、无 dynamic code generation"，要么走上面的 Phase 2 全量重签。 |
| "Guideline 2.5.2 — Uses private API" | 检查 Tauri 依赖里有没有 `_LSGetApplicationForInfo` 之类。项目里目前未见。 |
| "Guideline 4.0 — Design" | 前端 UI 与 iOS/Mac 系统观感冲突时会被要求整改。 |
| "Guideline 2.1 — App Review Information missing" | 提审备注里给"演示账号 / 关键流程截屏 / 网络需求说明"。本项目模型网关是有成本的付费服务，务必在备注里说明"审核期间提供的 demo key"或"审核模式"。 |
| "Guideline 5.1.1 — Data use and sharing" | 隐私政策 + 数据收集声明必须在 ASC 后台的 App Privacy 表格里逐项对齐。 |

## 密钥与安全红线（沿用仓库既有约束）

- `.provisionprofile` **不入仓库**，仅本机私有路径；`SIGN_*` 环境变量、`PROFILE` 路径都在 shell 环境里传，脚本从不打印。
- API Key 类凭证遵循仓库既有硬约束：只存 `~/.aipdd/*.key`（`chmod 600`）或环境变量，脚本运行时读取。
- 出包产物（`.pkg`）**不上传 git**、不 commit；`.gitignore` 已覆盖 `desktop/src-tauri/target/`。

## 相关脚本一览

- 直分发链：[build-macos-universal.sh](../../../desktop/scripts/build-macos-universal.sh)（`UNIVERSAL_MERGE_CHROME=1`）→ [mac-sign-notarize.sh](../../../desktop/scripts/mac-sign-notarize.sh) → [verify-release-dmg.sh](../../../desktop/scripts/verify-release-dmg.sh)
- MAS 上架链：[build-macos-universal.sh](../../../desktop/scripts/build-macos-universal.sh) → [build-macos-mas-app.sh](../../../desktop/scripts/build-macos-mas-app.sh) → [macos-pkg-appstore.sh](../../../desktop/scripts/macos-pkg-appstore.sh) → [verify-release-pkg.sh](../../../desktop/scripts/verify-release-pkg.sh)
- 单包双架构：[build-macos-universal.sh](../../../desktop/scripts/build-macos-universal.sh) —— 2.3.2 起两条链的共用入口，union + per-arch overlay + 随包单架构清单，机制见上面那一节
- 单架构备选路径（仅本机调试 / 快速验证某一侧）：`prepare-resources.sh` → `build-macos-dmg.sh` / `build-macos-mas-app.sh`
- 共用输入：`prepare-resources.sh`（`build-macos-universal.sh` 内部已按两个 target 各跑一次）
