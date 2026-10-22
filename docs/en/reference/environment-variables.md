<!-- lang-switch -->
**English** · [简体中文](../../zh/reference/environment-variables.md)

# Environment Variables Reference

> Configuration is injected through a `.env` file in the project root (copied from `.env.example`). **`.env.example` is the authoritative, complete set with per-item comments**; this page groups the most commonly used variables and their defaults by category for quick reference.

## Runtime Environment and Paths

| Variable | Default | Description |
|---|---|---|
| `ST_EDITION` | `ce` (forced by compose/image) | Edition identifier. CE mode cannot be downgraded. |
| `NOVELVIDEO_DATA_ROOT` | `.` (set to `/data` under Docker) | Data root directory; the three items below derive from it by default. |
| `NOVELVIDEO_OUTPUT_DIR` | `$DATA_ROOT/output` | Output directory for finished videos and artifacts. Docker Compose pins it to `/data/output`, which is persisted in the `ce-data` volume. |
| `NOVELVIDEO_STATE_DIR` | `$DATA_ROOT/state` | Local state. |
| `NOVELVIDEO_RUNTIME_DIR` | `$DATA_ROOT/runtime` | Runtime temporary directory. |
| `NOVELVIDEO_PROJECT_ROOT` | auto-detected (each consumer walks up to the layer holding the bundled resource it needs) | Project root. Used by the face-swap sidecar, the 3D world installer and the Jianying 10.2 draft skeleton to locate the bundled `services/`; set it explicitly for non-standard deployments. |
| `ST_CONTROL_PLANE_DSN` / `ST_REDIS_URL` / `ST_CELERY_BROKER_URL` / `ST_CELERY_RESULT_BACKEND` | Empty (forced empty in CE) | Used only by EE/distributed; CE runs tasks inline in-process, so leave empty. |

## Model Gateway

In CE, the selected channel, gateway address, and token are written by
**Settings → Models & Channels** to the local `settings.db`; they are not
configured through environment variables. The official channel has a fixed
address, while the local channel uses the bundled LocalAPI.

| Variable | Default | Description |
|---|---|---|
| `NEWAPI_TEXT_TIMEOUT_SECONDS` | `120` | HTTP timeout for text models (seconds). |
| `NEWAPI_TEXT_TRUST_ENV` | `true` | Whether the text client reads the system proxy; set `false` when an internal gateway is being blocked by a proxy. |

Model names are **not configured through environment variables**: which model each feature
slot binds to (general text generation, cast planning, image understanding, style analysis,
and so on) is chosen only in the web "Settings → Models & Channels" page, and the bindable list comes
from the channel catalog. The former `*_MODEL=DC-*-LLM` entries plus
`TEXT_MODEL_FALLBACK` / `VISION_MODEL_FALLBACK` have all left the resolution chain.
The reason: env used to win over the settings page, so when the panel switched models while
the environment still pointed at an old name, users saw one model while another one actually
ran—and a delisted name turns into a gateway 503 dead link. When nothing resolves, tasks are
rejected before submission with a Chinese message pointing back to the settings page; the
system never falls back to a preset model name. See
[Configuring Model Providers](../getting-started/configuring-models.md).

### Exception: direct-egress side paths (inactive in the default deployment)

A few side paths talk to the vendor directly and never go through the channel catalog, so they
still carry their own env keys and a factory default name:

| Variable | Purpose | Requires |
|---|---|---|
| `SCENE_SPATIAL_CONTRACT_MODEL` (falls back to `OPENROUTER_VISION_MODEL`, factory name `openai/gpt-5.5`) | Scene-360 spatial contract / overlap VLM | `OPENROUTER_API_KEY` set, otherwise the whole step is skipped |
| `VOXEL_VLM_MODEL` / `VOXEL_VLM_PROVIDER` | Scene voxelization codegen | same as above |
| `SKETCH_GATE_MODEL` / `SKETCH_GATE_PROVIDER` / `SKETCH_GATE_API_KEY` | Sketch visual quality gate | both provider and key present, otherwise no gating |
| `BLOCK_WORLD_MODEL` (falls back to `MODEL_PROVIDER` / `MODEL_NAME`) | Director-world block builder (host codex CLI) | the CLI installed on the host |

Keeping these out of the "Settings → Models & Channels" list is deliberate: their model name is decoupled
from endpoint and credentials—name resolution through the gateway yields a *gateway-routable*
real name, which turned against a direct endpoint only produces 404/400—and none of these vendor
keys are set in the default deployment, so not a single one of them runs. Under an organization
(multi-user) deployment these direct children are denied by the egress boundary regardless of
this section.

To actually enable one in production, the order matters: first add that vendor as an
OpenAI-compatible channel under "Settings → Models & Channels", then move the call site onto the
gateway as a whole (name / base URL / key switched as a pair in one commit)—do not feed a
settings-page name to a direct endpoint. The constraint, the field evidence and the trigger
conditions are recorded in the wave3 archive note inside `tests/test_no_hardcoded_model_names.py`.

## Reference Media Relay (optional)

| Variable | Description |
|---|---|
| `OSS_RELAY_AK` / `OSS_RELAY_SK` | Object storage credentials required by the reference-image feature. A pure-text → video flow can skip these. |
| `OSS_RELAY_ENDPOINT` / `OSS_RELAY_BUCKET` | Relay endpoint and bucket. |

## Video / Image Parameters

| Variable | Default | Description |
|---|---|---|
| `VIDEO_FPS` | `30` | Frame rate. |
| `VIDEO_WIDTH` / `VIDEO_HEIGHT` | `1080` / `1920` | Portrait resolution. |
| `VIDEO_CODEC` | `libx264` | Video codec (H.264). Honored by **every** encode chain now — all of them pick the encoder through `src/novelvideo/video_encoder.py`, which reads this variable before falling back to `libx264 → h264_videotoolbox → h264_mf → libopenh264`. Two conditions: the local ffmpeg build must actually contain it, and it must be one of those four H.264 implementations; anything else (e.g. `mpeg4`, `aac`) is ignored with a one-time warning and the ladder is used instead. See the [ffmpeg guide](../guides/ffmpeg.md). |
| `VIDEO_AUDIO_CODEC` | `aac` | Audio codec. |
| `VIDEO_BITRATE` | `4M` | Bitrate. |
| `IMAGE_DEFAULT_WIDTH` / `IMAGE_DEFAULT_HEIGHT` / `IMAGE_DEFAULT_STYLE` | `1440` / `2560` / `chinese_period_drama` | Default image dimensions and style. |

## Media Tools

| Variable | Default | Description |
|---|---|---|
| `FFMPEG_PATH` | `ffmpeg` (from PATH) | Path to the ffmpeg executable; specify explicitly when installed in a non-standard location. **Only the motion-feature-film chain (frame encoding + loudness normalization) and the shared encoder resolver read it**; `generators/video_composer.py` and the legacy `freezone/jobs.py` paths exec a bare `ffmpeg`, so they only ever use PATH. Pointing it at a binary off PATH makes the encoder list get probed from one build while those chains run another — the resolver logs a one-time warning; see the [ffmpeg guide](../guides/ffmpeg.md). |

## Motion feature film (MG)

Gallery → model writes a single-file page against the render contract → bundled Chrome renders it frame by frame → code-arranged score → Jianying draft. Out of the box this chain needs no configuration and makes no outbound calls; every variable below is an optional override.

| Variable | Default | Description |
|---|---|---|
| `MOTION_GALLERY_MANIFEST` | bundled `freezone/motion_templates.json` | Point at an external case catalog to swap the gallery. An unreadable/broken external file falls back to the bundled one with a warning; only a missing bundled catalog is an error (that means a broken package). |
| `MOTION_STYLE_MANIFEST` | bundled `freezone/motion_styles.json` | The art-style catalog (66 entries) is a second, independent manifest: it changes the look, not the case shot structure. Same fallback rule as above — a broken external file falls back to the bundled one with a warning; only a missing bundled manifest is an error. |
| `MOTION_GALLERY_ASSET_BASE` | `/api/v1/motion-gallery` | Address prefix for posters and original clips. Put the 37 originals (~0.7 GB, not bundled) on OSS/CDN and point here; the frontend resolves `<base>/<path-relative-to-manifest>`. **Bundled preview clips ignore this prefix**: they always come from the same-origin `/api/v1/motion-gallery/<case>/preview.mp4`, so an external CDN only needs posters and originals. |
| `MOTION_GALLERY_MIRROR` | empty | On-demand original fetch: when the local cache misses, pull once from `<mirror>/<relative path>`, verify sha256, store in the state volume, then serve. Unset means un-cached originals simply don't play — prompts still work. |
| `ST_CHROME_CDP_URL` | empty | Attach to an existing debugging instance instead of launching one. When unset the default follows the deployment: `http://127.0.0.1:9222` natively/desktop, `http://host.docker.internal:9222` in containers. Shared with the Douyin detail capture and the built-in Chrome MCP. |
| `ST_CHROME_PATH` | empty | Path to a Chrome/Chromium executable to launch as a temporary headless instance. A path that does not exist is reported loudly rather than silently treated as "no browser". |
| `MOTION_DRAFT_TEMPLATE_DIR` | bundled `services/vectcut/template_jianying_10_2` | Jianying 10.2 draft skeleton (root `draft_content.json` plus `Timelines/`). The skeleton location is derived by walking up to the layer that contains it; when your layout differs, set `NOVELVIDEO_PROJECT_ROOT` or point at this directory directly. |

> The desktop build needs none of these: `env.rs` prepends `resources/bin` (with `ffmpeg.exe` / `ffprobe.exe` / `chrome/`) to `PATH` and injects `APP_RESOURCE_PATH`, which is where the browser and the draft skeleton are discovered. See the [desktop environment contract](../../../desktop/README.md).

## Security

| Variable | Default | Description |
|---|---|---|
| `PROMPT_EXPORT_PASSWORD` | `change_me` | Prompt-export password; **always override it for deployment**. |
| `ST_COOKIE_SECURE` | `true` | Whether the admin cookie is Secure. Local HTTP development needs `0`, otherwise the browser drops the cookie. |

## Release Notifications

| Variable | Default | Description |
|---|---|---|
| `RELEASE_NOTIFICATIONS_ENABLED` | `true` | Set `false` to fully disable the release feed, including packaged notes parsing and GitHub checks. |
| `RELEASE_NOTIFICATIONS_GITHUB_TOKEN` | Empty | Optional GitHub token for a higher `releases/latest` rate limit. Anonymous requests are used when empty. |

## Observability Tracing (optional, off by default)

| Variable | Description |
|---|---|
| `NOVELVIDEO_ENABLE_LOGFIRE` | Enables PydanticAI tracing instrumentation. |
| `OTEL_EXPORTER_OTLP_TRACES_ENDPOINT` | Export traces to your own OTLP/Jaeger. |
| `LOGFIRE_TOKEN` | The only switch that sends data to the Logfire SaaS. |

See the [telemetry notes](../guides/telemetry.md) for details. By default none of the three are set, and no data is sent.

## Related

- Full list with comments: `.env.example` in the repo root
- [Quickstart](../getting-started/quickstart.md) ｜ [Configuring Model Providers](../getting-started/configuring-models.md) ｜ [Self-Hosting Manual](../guides/self-hosting.md)
