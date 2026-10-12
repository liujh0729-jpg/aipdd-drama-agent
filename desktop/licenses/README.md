# 第三方开源许可与合规

本桌面 App 在分发包内**捆绑**了多个第三方运行时与二进制。对外分发（尤其商用）前，
必须随包附上各组件许可证文本与必要的源码获取说明。以下为关键清单与义务提示。

> 权威来源以仓库根的 `LICENSES/`、`REUSE.toml`、`license-inventory.csv`、`sbom.spdx.json`
> 为准；`scripts/check_dependency_licenses.py` 会校验依赖许可证。本文件只列**桌面新增**的
> 捆绑二进制/解释器所引入的义务。

## 1. 组件许可证一览

| 组件 | 用途 | 许可证 | 分发义务要点 |
|---|---|---|---|
| 本产品 / supertale-ce 后端 | 业务 | **Elastic-2.0** | 见 `LICENSES/Elastic-2.0.txt`；非 OSI，注意商标与再分发条款 |
| Tauri v2 / Rust | 桌面壳 | MIT / Apache-2.0 | 附许可全文 |
| python-build-standalone | 便携 Python | Apache-2.0（构建）+ CPython PSF（解释器） | 附 Python LICENSE；PSF 商用友好 |
| CPython 标准库 | 解释器 | PSF | LICENSE 全文 |
| PyTorch / torchvision | 可选 [world] | BSD-3 | 仅在装 world extra 时随之附带 |
| opencv-python-headless | 白板渲染 | Apache-2.0 | 附 NOTICE |
| **new-api / dramaclaw-gateway（Go 网关）** | 模型传输层 | **AGPL-3.0** | ⚠️ **强 copyleft**，见下节 |
| FFmpeg | 音视频处理 | LGPL-2.1+（含可选 GPL 组件） | ⚠️ LGPL：需源码获取说明与可重链接对象，见下 |
| nginx | 本地反代 | BSD-2-Clause | 附许可全文 |
| Chromium（Chrome for Testing） | 抖音 CDP 提取 | BSD 系 + 大量三方 | 附 Chrome OSS 归因（credits.txt）与 Google 条款 |
| pyJianYingDraft / VectCut | 草稿组装（可选服务） | 见 `services/vectcut/LICENSE` | 按上游许可 |

各 `.txt` 许可证正文在构建时应拷贝进 `resources/licenses/`（见 §4），并在 App「关于 /
第三方开源许可」页可展开查看。

## 2. ⚠️ AGPL-3.0（Go 网关）——最需注意

`third_party/dramaclaw-gateway` 是 **AGPL-3.0**。App 把它作为**独立进程/独立二进制**随包分发，
AGPL 要求对**该网关组件**：

- 提供 AGPL 全文与版权/许可声明；
- 提供**对应源码获取途径**（随包给源码，或书面要约给出下载地址，含你对其的任何修改）；
- 因为是与主程序**进程隔离**聚合（separate executable），一般不因此把主程序也“传染”成 AGPL，
  但网关**自身的任何改动必须开源**。

务必与法务确认商用分发策略；如无法承担 AGPL 义务，考虑替换网关或走独立发行 + 链接方式。
（注：仓库根 `third_party/README.md` 已声明网关为 AGPL。）

## 3. ⚠️ LGPL（FFmpeg）

以动态方式使用 LGPL 组件时，需：
- 附 LGPL 全文；
- 给出**源码获取说明**（写明获取上游 FFmpeg 源码/本处所用构建的方式与地址）；
- 允许用户替换该库（保持可重链接/可替换）。
若静态链接 FFmpeg 则义务更重，建议用动态构建并附替换说明。

## 4. 许可证文本收集（构建期自动化）

在构建机上于 `prepare-resources` 之后、打包之前，生成随包许可文本：

```bash
# Rust 侧第三方许可（需在 desktop/src-tauri 下配 about.toml）
cargo install cargo-about
cargo about generate about.toml > ../resources/licenses/THIRD-PARTY-RUST.txt

# Python 侧依赖许可清单
uv pip install pip-licenses
"$RES/python/bin/python3" -m piplicenses --format=markdown > ../resources/licenses/THIRD-PARTY-PYTHON.md

# 上游各二进制/解释器许可证：从官方发布物拷贝进 resources/licenses/
#   Python/LICENSE、ffmpeg( LGPL-2.1 / GPL-2.0 )、nginx(BSD)、gateway(AGPL-3.0)、
#   Chrome for Testing credits.txt + LICENSE …
```

## 5. App 内合规页

需求 3.6 的「关于 / 第三方开源许可」页面应读取 `resources/licenses/` 下文本渲染，
并对 AGPL / LGPL 条目单列**源码获取入口**（链接或书面要约地址）。该页面属主前端职责，
本骨架把它作为后续里程碑（M6）落地；此处先固化义务清单与素材来源。
