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

**Not every chain reads it.** Only the motion-feature-film chain (`freezone/motion_render` + `freezone/motion_audio`: frame encoding, two-pass loudnorm, muxing) and the shared encoder resolver resolve `FFMPEG_PATH` first and fall back to PATH. Every other chain — `generators/video_composer.py` and the legacy `freezone/jobs.py` task paths — execs a bare `ffmpeg`, so it is the PATH one and nothing else. When `FFMPEG_PATH` is set but does not resolve, the MG chain reports "ffmpeg not found" instead of quietly using another binary — the list of available encoders is probed from that exact binary.

The consequence of centralizing the encoder choice: the resolver probes encoders from `FFMPEG_PATH` → PATH, but the non-MG chains exec a bare `ffmpeg`, i.e. the one on PATH. Pointing `FFMPEG_PATH` at a binary that is *not* on PATH means "encoders I probed" and "encoder I actually run" can be two different builds there. The resolver logs a one-time warning when the two disagree. The desktop installer does not hit this — it prepends `resources/bin` to PATH, so both are the same binary. To give the whole system an x264-capable ffmpeg, put it **first on PATH** (which moves the GPL obligations to you — see below), rather than only setting `FFMPEG_PATH`.

## Verification

```bash
ffmpeg -version      # printing a version means it is on PATH
ffprobe -version
```

Final cuts are encoded as **H.264**, defaulting to `libx264` (the `VIDEO_CODEC` default value). Which encoder actually runs is decided per call by one resolver — [src/novelvideo/video_encoder.py](../../../src/novelvideo/video_encoder.py) — which runs `ffmpeg -encoders` on the binary it resolves (`FFMPEG_PATH` → PATH) and takes the first H.264 implementation that is really present. So an ffmpeg build **without** libx264 no longer fails the pipeline; it silently uses `h264_mf` / `libopenh264` instead. Set `VIDEO_CODEC` to pin a specific encoder (it wins when the build has it, and is ignored when it does not). It may only name one of the four H.264 implementations in that ladder: a value that exists locally but is *not* H.264 (`mpeg4`, `libvpx`, `aac`) is ignored with a one-time warning and the ladder picks an H.264 encoder instead, because cuts delivered to editors and platforms have to be H.264. See [Environment Variable Reference](../reference/environment-variables.md).

## The ffmpeg bundled with the desktop app (measured)

The installer ships `resources/bin/ffmpeg` + `ffprobe` (the backend makes 63 ffprobe calls, so both must travel together). `desktop/scripts/prepare-resources.sh` picks the build, **and the two platforms differ**:

| Platform | Bundled source | Has `libx264`? |
|---|---|---|
| **Windows x64** | BtbN `ffmpeg-master-latest-win64-lgpl` (default, overridable with `FFB_URL`) | **No** — the LGPL package is compiled with `--disable-libx264`, which is what makes bundling it lawful |
| **macOS** | ffmpeg found on the build machine's `PATH` (usually Homebrew) or `FFMPEG_BIN=` | Usually yes (Homebrew includes x264), but the script warns that such a build may depend on system dylibs |

Probing the bundled Windows build shows no `libx264`, but `h264_mf`, `libopenh264`, native `aac`, and the `loudnorm` / `alimiter` / `afade` filters are all present. So:

- **No chain hardcodes `libx264` any more, so the installer does not fail with `Unknown encoder 'libx264'`.** Measured with the bundled binary: `-c:v libx264` exits **8** (`Encoder not found`), `-c:v h264_mf` and `-c:v libopenh264` both exit **0**. Every encode site now builds its argv from `video_encode_args()` in the resolver above — that replaced **20 literals in 7 files** (`freezone/jobs.py` ×9, `task_backend/runners/video.py` ×4, `generators/video_composer.py` ×3, `freezone/whiteboard.py`, `freezone/narration_edit.py`, `freezone/explainer_motion.py`, plus the mock backend). Four more sites inside the episode composer passed `config.VIDEO_CODEC` (whose default *is* `libx264`) straight into argv without probing anything; `VideoComposer.codec` now comes from the resolver too, which is what makes `VIDEO_CODEC` count for every chain — on the condition that the local build actually contains it. Two guarantees: when x264 *is* present the argv comes out byte-identical to the old hand-written one (same `-preset`, same `-crf`), so container/image output quality does not change; when it is absent, the x264-private options are dropped and a `-b:v` is supplied instead — from `VIDEO_BITRATE` on the legacy chains, and derived from resolution × fps × 0.1 bit/pixel (clamped to 2–12 Mbit/s) on MG. If the probe cannot run at all — no ffmpeg on `PATH`/`FFMPEG_PATH`, or `-encoders` failing — the resolver returns the old `libx264` argv unchanged rather than inventing a new failure mode. A static guard, [tests/test_no_hardcoded_h264_encoder.py](../../../tests/test_no_hardcoded_h264_encoder.py), fails the suite if the literal `libx264` reappears anywhere under `src/novelvideo` outside the resolver and the `config.py` default: chain tests assert command *shape*, not encoder names, so without it "someone hardcoded x264 again" would be invisible to the test suite.
- **The motion feature film (MG) renderer uses the same resolver.** It used to carry its own copy of the ladder; it now delegates, and the only policy it keeps locally is its bit-per-pixel budget. The ladder is `libx264 → h264_videotoolbox → h264_mf → libopenh264`, and only if none of them exist does it fail — with the reason. What it hands to an editor or platform is still H.264.
- **MG's audio half uses the same binary**: two-pass `loudnorm` to **-14 LUFS / true peak ≤ -1 dBTP** (render-contract item 6), then `-c:v copy -c:a aac`. Measured with the bundled build: after normalization the muxed film re-measures `-14.05 LUFS / -1.00 dBTP`. None of these filters or codecs carry x264's licensing entanglement.

## Licensing obligations (please check for yourself)

ffmpeg's actual license is determined by its build flags: enabling `--enable-gpl`, `x264`, and the like puts it under **GPL**. You need to:

- choose a **compliant ffmpeg build** for your environment and distribution scenario;
- take on the corresponding licensing obligations yourself (LGPL/GPL attribution, source provision, etc.).

CE neither chooses the build for you nor guarantees its license status.

## Related

- [Installation Guide](../getting-started/installation.md) ｜ [Self-Hosting Handbook](self-hosting.md) ｜ [Troubleshooting](troubleshooting.md)
