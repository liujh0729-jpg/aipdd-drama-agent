<!-- lang-switch -->
**English** · [简体中文](../../zh/guides/ffmpeg.md)

# ffmpeg Guide

> DramaClaw uses `ffmpeg` / `ffprobe` for media probing, extraction, composition, transcoding, and final-cut validation. This page covers how to install it, how to let the program find it, and your licensing obligations.

## Why you install it yourself

CE **does not distribute the ffmpeg/ffprobe binaries** — their license (LGPL or GPL) depends on the codecs and build flags enabled at compile time, and bundling them would require a separate source/build/attribution audit. CE therefore treats ffmpeg as a **system dependency**, provided by your operating system, package manager, or deployment image (for background, see [ADR-0002](../../adr/0002-ffmpeg-system-dependency.md)).

- **Docker**: the image already runs `apt-get install ffmpeg`, so nothing extra is needed.
- **Local development**: you install it yourself, see below.

## Installation

| Platform | Command |
|---|---|
| **macOS** | `brew install ffmpeg` |
| **Windows** | `winget install Gyan.FFmpeg` (or follow the Linux flow inside WSL2) |
| **Linux (Debian/Ubuntu)** | `sudo apt install ffmpeg` |
| **Linux (Fedora/RHEL)** | `sudo dnf install ffmpeg` (the appropriate repository must be enabled) |

A single install provides both `ffmpeg` and `ffprobe` (same project).

## Letting the program find it

By default `ffmpeg` is resolved from `PATH`. If it is installed in a non-standard location, specify it explicitly with an environment variable:

```bash
FFMPEG_PATH=/usr/local/bin/ffmpeg     # defaults to "ffmpeg" (found via PATH)
```

## Verification

```bash
ffmpeg -version      # printing a version means it is on PATH
ffprobe -version
```

Final cuts are encoded as **H.264 / `libx264`** by default (the `VIDEO_CODEC` default value). Make sure your ffmpeg build **includes libx264**; otherwise composition will fail, or change `VIDEO_CODEC` to an encoder your build supports (for the relevant parameters, see [Environment Variable Reference](../reference/environment-variables.md)).

## Licensing obligations (please check for yourself)

ffmpeg's actual license is determined by its build flags: enabling `--enable-gpl`, `x264`, and the like puts it under **GPL**. You need to:

- choose a **compliant ffmpeg build** for your environment and distribution scenario;
- take on the corresponding licensing obligations yourself (LGPL/GPL attribution, source provision, etc.).

CE neither chooses the build for you nor guarantees its license status.

## Related

- [Installation Guide](../getting-started/installation.md) ｜ [Self-Hosting Handbook](self-hosting.md) ｜ [Troubleshooting](troubleshooting.md)
