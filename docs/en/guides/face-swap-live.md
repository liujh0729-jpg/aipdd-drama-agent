<!-- lang-switch -->
**English** · [简体中文](../../zh/guides/face-swap-live.md)

# Face Swap Live Guide

> DramaClaw's **Face Swap Live** uses a local engine to replace the face seen by your
> **physical camera** in real time with a target face you upload, and outputs the result to a
> **system-level virtual camera** that OBS / streaming apps read directly. The browser is only a
> **control panel** — upload a face, tweak parameters, start / stop. **Video never passes through
> the browser.**

## Data flow

```
physical camera → [local sidecar engine] capture → detect/align + inswapper face swap → write system virtual camera
                                                                              → OBS / streaming app reads it
                            ▲
browser control panel ── localhost HTTP: upload face / change params / start-stop ──┘  (video never touches the browser / main backend)
```

## Runtime form restriction (important)

This feature needs a **physical camera + a system virtual-camera device**, so it is available only
on the **desktop / local runtime** (Tauri on the host, with network egress). A **Docker web
container has no host camera and cannot register a virtual camera**; there the capability is greyed
out on the Settings page and in the tool panel, with status viewing only.

## Per-platform virtual-camera prerequisites

The engine writes to a system virtual camera via `pyvirtualcam`; that device must exist first:

| Platform | Prerequisite |
|---|---|
| **Windows** | Install OBS Studio (ships the "OBS Virtual Camera" driver) |
| **macOS** | Install OBS Studio (provides the `obs-mac-virtualcam` device) |
| **Linux** | Load the `v4l2loopback` kernel module, e.g. `sudo modprobe v4l2loopback` |

The Settings page probes whether the virtual camera is available; when it is missing it shows a
hint and disables the "Start" button.

## 1. Deploy the engine under Settings → Environment (self-download, like the 3D world)

The engine's dependencies and models are **not part of the main dependency tree and not bundled in
the installer**; like the 3D world, they are downloaded on demand:

1. Open **Settings → Environment → Face Swap Live**.
2. (Optional) Configure download sources: pip index / HuggingFace endpoint (default `hf-mirror`) / proxy.
3. Toggle **Enable**, then click **Download & Deploy** — in the background it installs permissive
   dependencies (insightface / onnxruntime / opencv / pyvirtualcam / fastapi) into an **isolated
   venv** (`<state dir>/faceswap/venv`) and downloads the swap models (`inswapper_128_fp16` + the
   `buffalo_l` detection pack) from HuggingFace into `faceswap/models`. Progress and the log tail
   appear in the card; **Re-deploy** and **Uninstall** (drop the venv + models to free disk) are supported.
4. The page shows a hardware-probed recommended execution provider (CPU / CoreML / CUDA).

## 2. Operate the Tools → Face Swap Live panel

The **Face Swap Live** tool entry is actionable once deployed, on a local runtime, and with the
virtual camera available:

1. Click **Start** — the main backend launches the sidecar (capture camera → swap → write virtual camera).
2. **Upload face** — pick one clear frontal face image as the swap source (stored locally only, used
   as the inswapper source face).
3. Toggle parameters as needed: `swap all faces` / `keep original mouth` / `NSFW filter` /
   `face enhancer` / `live mirror` / detection size / provider. The panel shows run status and live FPS.
4. Click **Stop** to shut the engine down.

## 3. Read it in OBS

After start, in OBS choose **Sources → Video Capture Device → OBS Virtual Camera** (or the
pyvirtualcam device) to get the swapped feed. **Do not** pick the physical camera in OBS — it is
held exclusively by the local engine (expected behavior).

## License & compliance (read this)

- The local sidecar engine is DramaClaw-**authored**, licensed **Elastic-2.0**, and uses only
  permissively-licensed dependencies (insightface=Apache-2.0, onnxruntime=MIT, opencv=Apache-2.0,
  pyvirtualcam=MIT, fastapi/uvicorn=MIT).
- The upstream [Deep-Live-Cam](https://github.com/hacksider/Deep-Live-Cam) project is **AGPL-3.0**.
  This project does **not link, vendor, or copy** any of its source — it served only as behavioral
  reference. The main backend controls the sidecar over **127.0.0.1 HTTP with zero imports**, so
  AGPL propagation is blocked at the process boundary and does not reach the Elastic-2.0
  distribution (see `services/faceswap/NOTICE` and the "FaceSwap Live" section of the root `NOTICE`).
- Swap **model weights** are downloaded by the user at runtime and are not bundled; insightface /
  inswapper weights are largely **research / non-commercial** licensed — confirm and accept those
  obligations yourself.
- Face-swapping / deep-synthesis must comply with local law and must **not** be used to deceive or
  infringe others' likeness and privacy; obtain informed consent from anyone captured.

## Troubleshooting

- **Cannot open camera**: the device may be occupied by another app or lack permission; close the
  occupant or grant access in your OS privacy settings.
- **Virtual camera unavailable**: install the OBS virtual camera / `v4l2loopback` per the table and
  retry; the Settings probe will update.
- **Low FPS**: CPU inference is slow — prefer GPU (CoreML / CUDA) and lower the detection size (e.g. 128/160).
- **Dependency / model download failed**: network egress is required; offline environments surface
  the failure reason in the status card (same constraint as the 3D world).
