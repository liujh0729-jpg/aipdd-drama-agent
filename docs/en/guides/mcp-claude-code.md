<!-- lang-switch -->
**English** · [简体中文](../../zh/guides/mcp-claude-code.md)

# Driving DramaClaw from an AI agent (MCP)

DramaClaw ships an [MCP](https://modelcontextprotocol.io) server that exposes the
whole drama pipeline — ingest, characters, script, storyboards, first frames,
video, audio, compose, export, Jianying/CapCut draft editing, the tools-page
batch suite (video remake / short-video mixing / whiteboard animation /
explainer motion video /
idea-to-video / voice synth / lip-sync / person replace / watermark removal /
Douyin copy extract), one-click
automation (story→storyboard step driver, batch shot videos by beat range), and the
canvas (freezone) image / image-edit / video / audio / text-script / resource
catalog verbs — as ~97
tools. Any MCP-speaking agent (**Claude Code**, Codex, etc.) can use it to build
a drama end to end.

The server (`src/novelvideo/chat/dramaclaw_mcp.py`) is a thin stdio bridge over
the DramaClaw REST API: each tool is an HTTP call to your running instance, so it
inherits the same auth, project guards, and task queue as the web UI.

## Prerequisites

- A running DramaClaw instance (`docker compose up -d`, REST API on `:8780`).
- A configured, funded model gateway (Settings → Model Config) — the agent can
  plan/script without it, but image/video/audio generation needs working models.
- Local checkout with [`uv`](https://docs.astral.sh/uv/) synced (`uv sync`) so the
  `novelvideo` package is importable.

## Auth: two modes

The server authenticates to the API with two environment variables:

| Variable | Purpose |
|---|---|
| `DRAMACLAW_API_URL` | Base URL of your instance, e.g. `http://localhost:8780` |
| `DRAMACLAW_CE_OWNER` | Set to `1` for **CE** (self-host): calls are made as the local owner, no token needed. The target **must be loopback** (`localhost`, `127.0.0.1`, `::1`, `[::1]`) |
| `DRAMACLAW_CE_OWNER_ALLOW_REMOTE` | Unsafe override: set to `1` to allow tokenless CE-owner mode against a **non-loopback** `DRAMACLAW_API_URL` (see the warning below) |
| `DRAMACLAW_AGENT_TOKEN` | Bearer credential for token mode: a `st-…` **unified API key** (Settings → API keys; the same key the in-app MCP page hands you and the Model Plaza OpenAI-compatible endpoint accepts) or an EE scoped agent token |
| `DRAMACLAW_PROJECT_ID` | Optional: default project so tools can omit `project_id` |

**Community Edition** trusts local requests as the owner, so `DRAMACLAW_CE_OWNER=1`
is all you need — no token to mint. When `DRAMACLAW_AGENT_TOKEN` is set it always
takes precedence: the server sends `Authorization: Bearer <token>` instead of
calling in as the local owner. The simplest token is the `st-…` unified API key
generated under **Settings → API keys** — the same key the in-app MCP page
tells you to paste and the Model Plaza endpoint accepts. On EE, use a scoped
agent token instead; the server enforces its project boundary and write scopes.
Unified keys deliberately cannot touch admin endpoints (e.g. key management) —
that stays reserved for interactive sessions.

**Loopback is enforced.** Because tokenless owner mode sends *unauthenticated,
owner-level* requests, the bridge refuses any `DRAMACLAW_API_URL` whose host is
not a loopback address (`localhost`, `127.0.0.1`, `::1`, `[::1]`) with a clear
error. If you genuinely must reach a remote CE without a token — e.g. an SSH
tunnel you fully control — opt in explicitly with
`DRAMACLAW_CE_OWNER_ALLOW_REMOTE=1`. This is deliberately a separate variable so
it can never be enabled by accident; prefer a scoped `DRAMACLAW_AGENT_TOKEN` for
any non-local target.

## Connect Claude Code

This repo ships a project [`.mcp.json`](../../../.mcp.json). Open the repo in
Claude Code and approve the `dramaclaw` server when prompted — that's it.

Or add it explicitly:

```bash
claude mcp add dramaclaw \
  --env DRAMACLAW_API_URL=http://localhost:8780 \
  --env DRAMACLAW_CE_OWNER=1 \
  -- uv run python -m novelvideo.chat.dramaclaw_mcp
```

Verify the tools are live:

```bash
claude mcp list        # dramaclaw → ✓ connected
```

> **Running via Docker only?** Launch the bridge inside the container instead:
> set the `.mcp.json` command to
> `docker compose exec -T -e DRAMACLAW_API_URL=http://localhost:8780 -e DRAMACLAW_CE_OWNER=1 api python -m novelvideo.chat.dramaclaw_mcp`.

## What the agent can do

The tools cover the full pipeline. A typical end-to-end run:

1. `dramaclaw_post` → `/projects/{p}/ingest/upload` + `/ingest/start` — ingest a manuscript
2. `dramaclaw_build_characters` → extract the cast
3. `dramaclaw_plan_episodes` → segment into episodes
4. `dramaclaw_plan_identities` → per-episode identities
5. `dramaclaw_get_episode_story` / `dramaclaw_optimize_episode_story` /
   `dramaclaw_save_episode_story` → read, AI-optimize and save an episode's story;
   `dramaclaw_story_to_script` → adapt the story into a script, auto-imported into
   the script template (starts `script_writer` — same as script generation)
6. `dramaclaw_generate_script` → episode script (direct-generation entry when no story)
7. `dramaclaw_generate_portrait` / `dramaclaw_generate_scene_master` → key art
8. `dramaclaw_generate_sketches` → storyboards
9. `dramaclaw_render_first_frames` → first frames
10. `dramaclaw_start_single_video` → shot video
11. `dramaclaw_generate_audio` → voice-over
12. `dramaclaw_compose_episode` → assemble
13. `dramaclaw_get_final_video` → the finished episode (returns a servable URL)
14. `vectcut_*` → assemble storyboards / voice-over / subtitles into a
    Jianying/CapCut draft (needs the VectCut editing service:
    `scripts/start-vectcut.sh`, see `services/vectcut/`)
15. Tools-page batch tools: `dramaclaw_video_clone` (video clone: optional prompt
    plus up to 4 reference-content images act together on the remake),
    `dramaclaw_video_mix`
    (mixes a pool of library images/videos into one narrated film with
    burned-in subtitles + BGM), `dramaclaw_video_idea` (one-line idea → story /
    characters / storyboard → per-shot generation → stitched film),
    `dramaclaw_voice_clone`, `dramaclaw_lip_sync`,
    `dramaclaw_replace_person`, `dramaclaw_video_watermark`,
    `dramaclaw_image_watermark` — outputs are archived into the asset library
    (「待分类资产」/ uncategorized) by default; `dramaclaw_douyin_copywrite`
    (Douyin copy extract: share link → parsed video detail → narration
    transcribed with the ASR model you pick from
    `/freezone/douyin-copywrite/models`) returns the full script in the task
    result and does not archive its txt — the asset library has no text carrier
16. Canvas generation tools (fully node-equivalent to the web freezone; each call
    returns a `job_id` and its description names the task type to poll):
    **image** `dramaclaw_image_generate` (text-to-image / references),
    `dramaclaw_image_edit` (instructional edit with optional extra references, output
    archived by default), `dramaclaw_image_upscale`, `dramaclaw_image_outpaint`,
    `dramaclaw_image_relight`, `dramaclaw_image_multi_view`,
    `dramaclaw_image_template_edit` (`mode` required), `dramaclaw_image_redraw`
    (optionally with a mask), `dramaclaw_image_reverse_prompt`;
    **video** `dramaclaw_video_generate` (text-to-video), `dramaclaw_video_i2v`
    (image-to-video, `gen_mode` is imageToVideo / imageReference),
    `dramaclaw_video_omni_gen` (all-reference omni-gen), `dramaclaw_video_upscale`,
    `dramaclaw_video_edit`, `dramaclaw_video_extend`;
    **audio** `dramaclaw_audio_separate` (vocal / accompaniment split),
    `dramaclaw_audio_music` (ElevenLabs music);
    **text/script** `dramaclaw_text_generate`, `dramaclaw_text_translate`,
    `dramaclaw_story_script_generate`, `dramaclaw_video_script_reverse`,
    `dramaclaw_media_copywrite` — the last two produce txt, are deliberately NOT in
    the `/result` whitelist, read them via `dramaclaw_get_task` (`task.result`);
    **resource catalog reads** `dramaclaw_list_assets`,
    `dramaclaw_get_character_library`, `dramaclaw_get_audio_references`,
    `dramaclaw_get_models` (maps `kind` to the image / video / audio / text / vision
    catalogs).
17. One-click automation: `dramaclaw_auto_storyboard` (automated storyboard step
    driver: after the story is uploaded it auto-detects the style → screenplay →
    episodes → storyboard script → sketches → shot first frames; each call advances
    exactly one stage and returns `stage` / `next_action` — keep calling until
    `done`. Optional model / style / aspect ratio / episode count / total &
    per-episode durations; shot counts are derived from the screenplay. It NEVER
    generates videos). Both this tool and `dramaclaw_run_pipeline` below are **thin
    shells**: the stage state machine is unified in the authoritative backend planner
    `POST /api/v1/projects/{project}/pipeline/plan` (default `execute=false` decides
    without firing; `execute=true` fires the next stage's actions in-process), so MCP
    no longer keeps its own copy of the logic. Also `dramaclaw_video_range` (batch-generate shot videos from
    beat numbers or a range like `1-8` with the video model you pick; beats without
    a first frame are skipped and reported in `skipped`; poll each submission via
    `dramaclaw_get_task(task_type="single_video", beat_num=…)`), `dramaclaw_regen_sketches`
    (batch-regenerate shot sketches by beat numbers or a range like `1-8`; omit beats to cover
    the whole episode; requires the storyboard script first; poll
    `dramaclaw_get_task(task_type="sketch_regen", episode=…)`), and
    `dramaclaw_generate_video_prompts` (batch-generate shot video prompts one by one from beat
    numbers or a range; beats missing prerequisites are reported in `skipped`; poll
    `dramaclaw_get_task(task_type="beat_video_prompt", beat_num=…)`).
18. Orchestration layer (production line, giving the user the choice):
    `dramaclaw_run_pipeline` wraps the same backend planner in three modes — `manual` advances one
    stage; `checkpoint` (default) decides without firing and returns a `planned_requests`
    gate that only submits when re-called with `approve=true` (system-led with per-stage
    confirmation); `auto` advances to **shot first-frame completion** within `budget_s` and
    **never submits video** (use `dramaclaw_video_range` for videos); `use_suggested=true` fills
    model / aspect / style from smart defaults. Also
    `dramaclaw_get_run_status` (aggregate pipeline progress with running / failed / completed
    tasks and per-episode shot counts), `dramaclaw_cancel_run` (stop an episode's in-flight
    tasks), and `dramaclaw_suggest_config` (recommend a generation config with rationale).

Generation is **asynchronous**: a tool call starts a task, then the agent polls
`dramaclaw_pipeline_status` / `dramaclaw_list_tasks` / `dramaclaw_get_task` until
it completes. Each tool's description names the exact task to poll. The generic
`dramaclaw_get` / `dramaclaw_post` / `dramaclaw_patch` / `dramaclaw_delete` tools
are an escape hatch for any endpoint not covered by a curated verb.

> **Canvas (freezone).** The common generation / edit / resource actions are now
> distilled into the curated verbs above, one-to-one with the web nodes. Canvas-
> internal operations (canvases CRUD / restore / projections / 3D / screenshot /
> marks / detect) have no dedicated verb; reach them through the generic ones:
> list canvases with
> `dramaclaw_get('/projects/{p}/freezone/canvases')`, inspect one with
> `…/freezone/canvases/{canvas_id}`, and trigger the node-equivalent image
> generation with `dramaclaw_post('/projects/{p}/freezone/gen', {…})` — exactly
> what the web canvas does.

## Security notes

- CE-owner mode grants **full owner access** to the instance it points at — only
  use it against a **local** CE you control. Never point it at a shared instance.
- The bridge enforces this: tokenless CE-owner mode only accepts a **loopback**
  `DRAMACLAW_API_URL` and errors out on any remote host unless you set the
  explicit `DRAMACLAW_CE_OWNER_ALLOW_REMOTE=1` override.
- On EE, use a scoped `DRAMACLAW_AGENT_TOKEN`; the server enforces the token's
  project boundary and write scopes.
