<!-- lang-switch -->
**English** · [简体中文](../../zh/guides/troubleshooting.md)

# Troubleshooting

> Common failures and how to diagnose them when self-hosting DramaClaw CE. Check the logs first: `docker compose logs -f api`, or the terminal output of `novelvideo api` during local development.

## Startup

| Symptom | Diagnosis |
|---|---|
| **Container won't start / exits immediately** | Check `docker compose logs api` and follow the startup error to the configuration, port, or data-volume problem. |
| **Port `8780` already in use** | Change the left-hand value of `ports` in compose, e.g. `8888:8780`; or stop the process holding it (`lsof -i :8780`). |
| **Health check stays unhealthy** | The probe hits `/api/v1/config`; if the API itself errors, check the startup logs to pinpoint the real exception. |
| **Local dev won't start: Python version** | Requires **3.11–3.12** (`>=3.11,<3.13`). Run `uv python pin 3.12` or install the matching version, then `uv sync`. |

## Model / gateway

| Symptom | Diagnosis |
|---|---|
| **Every model call errors** | Under Settings → Models & Channels, confirm the active channel is configured. Check the DC key for the official channel, or the service, runtime token, and upstream channels for LocalAPI. |
| **A stage reports "model does not exist"** | LocalAPI is missing the corresponding logical model mapping, or the target channel is disabled. See [Configuring model providers](../getting-started/configuring-models.md). |
| **Structured steps fail with `Exceeded maximum output retries`** (character extraction, script planning…) while plain text works | The upstream did not return a function/tool call. The task log (v2.0.3+) shows the retry prompt and cause. Relays that convert Chat Completions themselves (Codex2API and similar Codex-backed proxies) are known to drop `tool_calls` on `/v1/chat/completions`: in the LocalAPI admin, enable **ChatCompletions → Responses Compatibility** (`chat_completions_to_responses_policy`) for that channel so LocalAPI sends `/v1/responses` upstream. See #490. |
| **Text model times out** | Increase `LOCALAPI_TEXT_TIMEOUT_SECONDS` (default 120); if a system proxy is intercepting an internal gateway, set `LOCALAPI_TEXT_TRUST_ENV=false`. |
| **Reference-image feature unavailable** | Requires `OSS_RELAY_AK/SK`; the plain text→video pipeline can run without it. |

## Media / ffmpeg

| Symptom | Diagnosis |
|---|---|
| **Compositing stage reports ffmpeg not found** | The legacy chains exec a bare `ffmpeg` from `PATH` (Docker images ship it; local development means installing it yourself); only the motion-graphics chain and the shared resolver read `FFMPEG_PATH`. Desktop builds put `resources/bin` first on `PATH`. See the [ffmpeg guide](ffmpeg.md). |
| **Compositing fails with encoder unavailable** | Should not happen any more: `src/novelvideo/video_encoder.py` picks the encoder from what `ffmpeg -encoders` actually reports, so `Unknown encoder 'libx264'` is closed out. A "no H.264 encoder available" error means this build has none of the four candidates (x264 / videotoolbox / h264_mf / libopenh264). `VIDEO_CODEC` pins an implementation, but only one the build really has and only from that H.264 family. |
| **Output is black / duration is wrong** | Usually upstream image/audio artifacts are missing; review the logs of preceding stages to confirm the assets were generated. |

## Data / upgrades

| Symptom | Diagnosis |
|---|---|
| **Data gone after a rebuild** | Data lives in the named volume `ce-data` (`/data` inside the container). `docker compose down` keeps the volume—**do not add `-v`** (it deletes the volume). For backups see the [self-hosting handbook](self-hosting.md#5-where-the-data-lives--backups). |
| **`unable to prepare context: path ".../dramaclaw-gateway" not found`** | The gateway builds from the vendored `third_party/dramaclaw-gateway`, so this only happens if you overrode `DRAMACLAW_GATEWAY_SRC` to a path that doesn't exist. Unset it to use the vendored copy, or point `DRAMACLAW_GATEWAY_SRC` in `.env` at a valid gateway checkout path. |
| **Config error after an upgrade** | Everything is source-built now (`docker-compose.yml` and `docker-compose.release.yml`): `git pull && docker compose up -d --build`. The old image-pull mode is retired. See the self-hosting guide §6. |

## world features (3DGS/SHARP)

| Symptom | Diagnosis |
|---|---|
| **`FileNotFoundError` pointing at `BuilderGPT/...`** | These heavyweight feature scripts are not in the slim CE package; the plain text→video pipeline doesn't need them. They're only required for the 3D/voxel pipeline. |
| **`uv sync --extra world` install fails** | Use uv (not pip) so the dependency overrides take effect; GPU acceleration needs a CUDA environment, while slim/CPU environments only support the CPU path. |

## Self-heal (MCP agents)

| Symptom | Diagnosis |
|---|---|
| **An agent tool call failed and the agent doesn't know how to continue** | Failures that aren't caller-fixable (server 5xx, rate-limit 429, or a status-less `ok:false`) carry a `self_heal` hint; fix 4xx parameter/path errors directly from the error message. Have the agent call `aipdd_drama_self_heal` once (default `mode="diagnose"`): it collects diagnostics, pending environment items, log tails, and the failing tool's source, then asks the configured default text model for a categorized diagnosis and next steps. |
| **You want it to fix the problem** | Use `mode="repair"` only when the user explicitly asked for a fix. `diagnose` returns repairs with status `proposed` (suggestions only, nothing is executed); `repair` applies whitelisted actions only: install auto-installable runtime deps (e.g. world, requires `allow_heavy=true`; a `started` result means the async install has begun — confirm with `check` later, `needs_manual` means it cannot be auto-installed here, follow the returned `guide`), make a unique-match replacement in a `.py` file under `src/novelvideo/` or `.hermes/plugins/`, and create missing state/runtime/output directories. It never runs a shell, installs pip/npm packages, or edits secrets and auth files; patches are backed up, syntax-checked, and reverted on failure. |
| **A tool reports `network_error` / the service is unreachable** | Do not call self-heal: its endpoint lives on the same service and will fail too. Confirm AIPDD is running and check the host/port in `DRAMACLAW_API_URL`, then retry once. |
| **See what self-heal did** | The audit log is `self_heal/audit.jsonl` under the data directory, and patch backups are in `self_heal/backups/<UTC timestamp>/`. A source patch requires a service restart (`restart_required` is true); code patches inside a macOS `.app` are refused to preserve the signature. |
| **Self-heal itself says the model is not configured** | The response has `degraded=true` and a `degraded_reason` pointing at the unconfigured gateway. Configure the default text model under Settings → Models & Channels and retry. |

## Still stuck?

- Usage / ideas → [GitHub Discussions](https://github.com/liujh0729-jpg/aipdd-drama-agent/discussions)
- Confirmed a bug → [File a bug](https://github.com/liujh0729-jpg/aipdd-drama-agent/issues/new?template=bug_report.yml) (attach logs, reproduction steps, environment)
- Security issue → do not use a public issue; see [SECURITY](../../../SECURITY.md)

## Related

- [Installation guide](../getting-started/installation.md) ｜ [Quickstart](../getting-started/quickstart.md) ｜ [Self-hosting handbook](self-hosting.md)
