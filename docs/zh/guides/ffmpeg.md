<!-- lang-switch -->
[English](../../en/guides/ffmpeg.md) · **简体中文**

# ffmpeg 指南

> DramaClaw 用 `ffmpeg` / `ffprobe` 做媒体探测、抽取、合成、转码和成片校验。本篇讲怎么装、怎么让程序找到它,以及许可义务。

## 为什么要自己装

CE **不分发 ffmpeg/ffprobe 二进制**——它们的许可证(LGPL 或 GPL)取决于编译时启用的编解码器与 build flag,随包分发需要单独的来源/构建/署名审计。所以 CE 把 ffmpeg 当**系统依赖**,由你的操作系统、包管理器或部署镜像提供(背景见 [ADR-0002](../../adr/0002-ffmpeg-system-dependency.md))。

- **Docker**:镜像已 `apt-get install ffmpeg`,无需额外操作。
- **本地开发**:需自己装,见下。

## 安装

| 平台 | 命令 |
|---|---|
| **macOS** | `brew install ffmpeg` |
| **Windows** | `winget install Gyan.FFmpeg`(或在 WSL2 里走 Linux 流程) |
| **Linux(Debian/Ubuntu)** | `sudo apt install ffmpeg` |
| **Linux(Fedora/RHEL)** | `sudo dnf install ffmpeg`(需启用相应仓库) |

一次装好同时带 `ffmpeg` 与 `ffprobe`(同一项目)。

## 让程序找到它

默认从 `PATH` 解析 `ffmpeg`。装在非标准路径,用环境变量显式指定:

```bash
FFMPEG_PATH=/usr/local/bin/ffmpeg     # 默认 "ffmpeg"(从 PATH 找)
```

## 校验

```bash
ffmpeg -version      # 打印版本即在 PATH 中
ffprobe -version
```

成片默认编码为 **H.264 / `libx264`**(`VIDEO_CODEC` 默认值)。请确认你的 ffmpeg build **包含 libx264**;否则合成会失败,或改 `VIDEO_CODEC` 为你的 build 支持的编码器(相关参数见 [环境变量参考](../reference/environment-variables.md))。

## 许可义务(请自查)

ffmpeg 的实际许可证由其 build flag 决定:启用 `--enable-gpl` 或 `x264` 等会落入 **GPL**。你需要:

- 为自己的环境和分发场景选择**合规的 ffmpeg build**;
- 自行承担相应许可义务(LGPL/GPL 的署名、源码提供等)。

CE 既不替你选择 build,也不对其许可状态作担保。

## 相关

- [安装指南](../getting-started/installation.md) ｜ [自托管手册](self-hosting.md) ｜ [排错](troubleshooting.md)
