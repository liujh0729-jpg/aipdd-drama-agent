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

默认从 `PATH` 解析 `ffmpeg`。装在非标准路径时,`FFMPEG_PATH` 是这台系统里唯一的显式开关:

```bash
FFMPEG_PATH=/usr/local/bin/ffmpeg     # 默认 "ffmpeg"(从 PATH 找)
```

**但不是所有链路都读它**:只有动效大片(MG)的渲染/配乐(`freezone/motion_render` +
`freezone/motion_audio`:逐帧编码、两遍 loudnorm、合轨)和那个共用的编码器解析器按
`FFMPEG_PATH` → PATH 的顺序找;其余链路——`generators/video_composer.py` 和 `freezone/jobs.py`
那批老任务链路——argv 里写的是裸 `ffmpeg`,跑的就是 PATH 上那一个,`FFMPEG_PATH` 对它们无效。
MG 链路在 `FFMPEG_PATH` 指了却指不到时直接报「找不到 ffmpeg」,不会悄悄换一条二进制
——本机可用编码器清单是按这个二进制 `ffmpeg -encoders` 探出来的,换了就对不上。

收口编码器之后,这一点要特别注意:编码器是**按 `FFMPEG_PATH` → PATH 探出来的**,而非 MG 的链路
执行的是 PATH 上那台。把 `FFMPEG_PATH` 指到一条不在 PATH 上的二进制时,这些链路就会出现
「探到的清单」和「真正跑的编码器」不是同一台;解析器在这种时候会打一条一次性 warning。桌面装机版
不会走到这里(它把 `resources/bin` 前置进了 PATH,两边是同一个二进制)。要给全系统换一条含
x264 的 ffmpeg,请把它**放在 PATH 最前面**(许可义务随之转移到你这边,见下文),而不是只设
`FFMPEG_PATH`。

## 校验

```bash
ffmpeg -version      # 打印版本即在 PATH 中
ffprobe -version
```

成片编码为 **H.264**，默认值写的是 `libx264`（`VIDEO_CODEC` 的默认值），但**真正跑哪个编码器是每次调用由唯一一处解析器决定的**——[src/novelvideo/video_encoder.py](../../../src/novelvideo/video_encoder.py)：它先对自己解析出来的那台二进制（`FFMPEG_PATH` → PATH）跑一次 `ffmpeg -encoders`，再取本机真实存在的第一个 H.264 实现。所以一个不含 libx264 的 build 不再把链路带崩，它会用 `h264_mf` / `libopenh264` 出片。想指定某个实现就设 `VIDEO_CODEC`（本机有才生效，本机没有则忽略、继续走阶梯）。它只允许落在阶梯那四个 H.264 实现里：指到 `mpeg4`、`libvpx`、`aac` 这类本机真有、但不是 H.264 的编码器时，解析器会忽略它、按阶梯选一个 H.264 并警告一次——交付给剪辑软件/平台的成片必须是 H.264。相关参数见 [环境变量参考](../reference/environment-variables.md)。

## 桌面版随包的 ffmpeg(实测结论)

安装包自带 `resources/bin/ffmpeg` + `ffprobe`(后端有 63 处 ffprobe 探测,两者必须同包),来源由
`desktop/scripts/prepare-resources.sh` 决定,**两个平台的 build 不一样**:

| 平台 | 随包来源 | 有没有 `libx264` |
|---|---|---|
| **Windows x64** | BtbN `ffmpeg-master-latest-win64-lgpl`(默认,`FFB_URL` 可覆盖) | **没有**:LGPL 包以 `--disable-libx264` 编译,为的是能随包分发 |
| **macOS** | 构建机 `PATH` 上的 ffmpeg(通常是 Homebrew 版)或 `FFMPEG_BIN=` | 一般有(Homebrew build 带 x264),但脚本会警告其可能依赖系统 dylib |

因此装机版的实测结论是:

- **链路里已经没有写死 `libx264` 的地方,装机版不再报 `Unknown encoder 'libx264'`**。实测随包二进制：`-c:v libx264` 退出码 **8**（`Encoder not found`），`-c:v h264_mf` 与 `-c:v libopenh264` 都是 **0**。原先 **7 个文件 20 处**把 `libx264` 当字面量塞进 argv（`freezone/jobs.py` 9 处、`task_backend/runners/video.py` 4 处、`generators/video_composer.py` 3 处、`freezone/whiteboard.py`、`freezone/narration_edit.py`、`freezone/explainer_motion.py`，外加 mock 后端 1 处），现在这些位置的编码器参数统一由上面那个解析器的 `video_encode_args()` 铺出来。另外整集合成里还有 4 处是把 `config.VIDEO_CODEC`（默认值就是 `libx264`）直接塞进 argv、不做探测的，现在 `VideoComposer.codec` 也取自解析器，`VIDEO_CODEC` 于是对所有链路都算数——但只在「本机这台 build 真有它」的前提下。两条保证：本机**有** x264 时，铺出来的 argv 与收口前逐字节相同（`-preset`、`-crf` 原样保留），容器/镜像出片质量不会被顺手改掉；本机**没有** x264 时，摘掉 x264 私有选项、补一条 `-b:v`——老链路取 `VIDEO_BITRATE`，MG 按 分辨率 × 帧率 × 0.1 bit/像素 计算并夹在 2–12 Mbit/s。探测本身跑不起来时（`PATH`/`FFMPEG_PATH` 上找不到 ffmpeg，或 `-encoders` 失败），解析器原样返回老的 `libx264` 写法，不多造一类新错误。另有静态守卫 [tests/test_no_hardcoded_h264_encoder.py](../../../tests/test_no_hardcoded_h264_encoder.py)：`src/novelvideo` 下再出现 `libx264` 字面量（允许清单只有解析器与 `config.py` 的默认值）就直接失败——链路测试钉的是命令形状，不是编码器名，少了这条守卫，「某处又写回 x264」不会有任何测试变红。
- **动效大片(MG)渲染用的是同一个解析器**：它原来自己抄了一份阶梯（`libx264 → h264_videotoolbox → h264_mf → libopenh264`），现在改为委托解析器，本地只保留自己的每像素码率预算。四个候选都没有才失败，且说明原因。交付给剪辑软件/平台的仍然是 H.264。
- **MG 的声音那半也吃这条二进制**:两遍 `loudnorm` 归一到 **-14 LUFS / 真峰值 ≤ -1 dBTP**
  (渲染契约第 6 条),合轨 `-c:v copy -c:a aac`。LGPL 构建自带 `loudnorm`/`alimiter`/`afade`
  与原生 `aac`,这几个滤镜与编码器没有 x264 那样的许可牵连,实测(包内 ffmpeg)归一后实测
  `-14.05 LUFS / -1.00 dBTP`。

## 许可义务(请自查)

ffmpeg 的实际许可证由其 build flag 决定:启用 `--enable-gpl` 或 `x264` 等会落入 **GPL**。你需要:

- 为自己的环境和分发场景选择**合规的 ffmpeg build**;
- 自行承担相应许可义务(LGPL/GPL 的署名、源码提供等)。

CE 既不替你选择 build,也不对其许可状态作担保。

## 相关

- [安装指南](../getting-started/installation.md) ｜ [自托管手册](self-hosting.md) ｜ [排错](troubleshooting.md)
