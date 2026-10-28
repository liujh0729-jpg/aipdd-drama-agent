<!-- lang-switch -->
**English** · [简体中文](../../zh/guides/mcp-claude-code.md)

# Driving AIPDD-Agent-Claw from an AI agent (MCP)

AIPDD-Agent-Claw ships an [MCP](https://modelcontextprotocol.io) server that exposes the
whole drama pipeline — ingest, characters, script, storyboards, first frames,
video, audio, compose, export, Jianying/CapCut draft editing, the tools-page
batch suite (video remake / short-video mixing / whiteboard animation /
explainer motion video / channel short video / WeChat Official Account article writing & publish /
idea-to-video / narration smart edit / voice synth / lip-sync / person replace / watermark removal /
Douyin copy extract), one-click
automation (story→storyboard step driver, batch shot videos by beat range), the
canvas (freezone) image / image-edit / video / audio / text-script / resource
catalog verbs, environment auto-install (`aipdd_drama_env_setup`: probe runtime
dependencies → action plan → per-item install), environment and code self-heal
(`aipdd_drama_self_heal`: on failure, the default text model analyzes the
symptom; `diagnose` is read-only, `repair` applies whitelisted fixes only),
executable work plans (`aipdd_drama_get_plan` /
`aipdd_drama_update_plan` / `aipdd_drama_run_plan`), novel story-source read/save,
chapter read/save and ingest, the motion-graphic (MG) catalog / render / stills /
Jianying draft chain, and final-video / episode-pack export — as 122
tools. Any MCP-speaking agent (**Claude Code**, Codex, etc.) can use it to build
a drama end to end.

The server (`src/novelvideo/chat/dramaclaw_mcp.py`) is a thin stdio bridge over
the AIPDD-Agent-Claw REST API: each tool is an HTTP call to your running instance, so it
inherits the same auth, project guards, and task queue as the web UI.

## Prerequisites

- A running AIPDD-Agent-Claw instance (`docker compose up -d`, REST API on `:8780`).
- A configured, funded model gateway (Settings → Models & Channels) — the agent can
  plan/script without it, but image/video/audio generation needs working models.
  Generation can ride **AIPDD's low-cost idle compute**, any **LocalAPI** channel you
  register, or **local ComfyUI** on your own GPU — see the
  [model guide](../../../README.md#supported-models).
- Local checkout with [`uv`](https://docs.astral.sh/uv/) synced (`uv sync`) so the
  `novelvideo` package is importable.

## Auth: two modes

The server authenticates to the API with two environment variables:

| Variable | Purpose |
|---|---|
| `DRAMACLAW_API_URL` | Base URL of your instance, e.g. `http://localhost:8780` |
| `DRAMACLAW_CE_OWNER` | Set to `1` for **CE** (self-host): calls are made as the local owner, no token needed. The target **must be loopback** (`localhost`, `127.0.0.1`, `::1`, `[::1]`) |
| `DRAMACLAW_CE_OWNER_ALLOW_REMOTE` | Unsafe override: set to `1` to allow tokenless CE-owner mode against a **non-loopback** `DRAMACLAW_API_URL` (see the warning below) |
| `DRAMACLAW_AGENT_TOKEN` | Bearer credential for token mode: a `st-…` **unified API key** (Settings → API Keys; the same key the in-app MCP page hands you and the Model Plaza OpenAI-compatible endpoint accepts) or an EE scoped agent token |
| `DRAMACLAW_PROJECT_ID` | Optional: default project so tools can omit `project_id` |

**Community Edition** trusts local requests as the owner, so `DRAMACLAW_CE_OWNER=1`
is all you need — no token to mint. When `DRAMACLAW_AGENT_TOKEN` is set it always
takes precedence: the server sends `Authorization: Bearer <token>` instead of
calling in as the local owner. The simplest token is the `st-…` unified API key
generated under **Settings → API Keys** — the same key the in-app MCP page
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

### In-app MCP page: config generated for your runtime

**MCP → Local agent tools** connects Workbuddy / Claude Code / Cursor / Codex to this
instance in one click. The entry it writes depends on how the app runs:

- **Docker deployment**: `docker exec -i <container> python -m novelvideo.chat.aipdd_drama_mcp`,
  where the container name comes from `AIPDD_DRAMA_MCP_CONTAINER` (default
  `aipdd-drama-api`, matching the release compose files);
- **Desktop app (exe / dmg)**: the bundled Python runs the server directly
  (`sys.executable -m …`) with the token and project in the entry's `env` — no Docker
  or uv required.

Built-in servers (Chrome DevTools / Blender / ComfyUI) are marked available or not for
the current machine: if `chrome-devtools-mcp` or `uvx` is missing, the page shows
"unavailable" with the install command, and agent turns stop trying to spawn it.
Installing the command restores it on the next request — no config edit needed.

## What the agent can do

The tools cover the full pipeline. A typical end-to-end run:

1. `aipdd_drama_start_ingest` → `/projects/{p}/ingest/start` — ingest an uploaded manuscript
   (the multipart upload itself stays in the web/client; `aipdd_drama_post` can still call the endpoint)
2. `aipdd_drama_build_characters` → extract the cast
3. `aipdd_drama_plan_episodes` → segment into episodes
4. `aipdd_drama_plan_identities` → per-episode identities
5. `aipdd_drama_get_episode_story` / `aipdd_drama_optimize_episode_story` /
   `aipdd_drama_save_episode_story` → read, AI-optimize and save an episode's story;
   `aipdd_drama_story_to_script` → adapt the story into a script, auto-imported into
   the script template (starts `script_writer` — same as script generation)
6. `aipdd_drama_generate_script` → episode script (direct-generation entry when no story)
7. `aipdd_drama_generate_portrait` / `aipdd_drama_generate_scene_master` → key art
8. `aipdd_drama_generate_sketches` → storyboards
9. `aipdd_drama_render_first_frames` → first frames
10. `aipdd_drama_start_single_video` → shot video
11. `aipdd_drama_generate_audio` → voice-over
12. `aipdd_drama_compose_episode` → assemble
13. `aipdd_drama_get_final_video` → the finished episode (returns a servable URL)
14. `vectcut_*` → assemble storyboards / voice-over / subtitles into a
    Jianying/CapCut draft (needs the VectCut editing service:
    `scripts/start-vectcut.sh`, see `services/vectcut/`)
15. Tools-page batch tools: `aipdd_drama_video_clone` (video clone: optional prompt
    plus up to 4 reference-content images act together on the remake),
    `aipdd_drama_video_mix`
    (mixes a pool of library images/videos into one narrated film with
    burned-in subtitles + BGM), `aipdd_drama_video_idea` (one-line idea → story /
    characters / storyboard → per-shot generation → stitched film),
    `aipdd_drama_narration_smart_edit` (narration smart edit: one talking-head
    video → local silence cut + gateway ASR/text defect cut & storyboard/B-roll
    planning + image/BGM generation → a direct MP4 or a VectCut Jianying draft,
    or both; each stage's model can be left blank to use the default),
    `aipdd_drama_voice_clone`, `aipdd_drama_lip_sync`,
    `aipdd_drama_replace_person`, `aipdd_drama_video_watermark`,
    `aipdd_drama_image_watermark` — generated outputs are archived into the asset
    library (「待分类资产」/ uncategorized) by default — "generate, then archive"
    — and pass `archive_to_library=false` to opt out; `aipdd_drama_douyin_copywrite`
    (Douyin copy extract: share link → parsed video detail → narration
    transcribed with the ASR model you pick from
    `/freezone/douyin-copywrite/models`) returns the full script in the task
    result and archives its txt into the text carrier the same way (opt-out applies too)
16. Canvas generation tools (fully node-equivalent to the web freezone; each call
    returns a `job_id` and its description names the task type to poll):
    **image** `aipdd_drama_image_generate` (text-to-image / references),
    `aipdd_drama_image_edit` (instructional edit with optional extra references, output
    archived by default), `aipdd_drama_image_upscale`, `aipdd_drama_image_outpaint`,
    `aipdd_drama_image_relight`, `aipdd_drama_image_multi_view`,
    `aipdd_drama_image_template_edit` (`mode` required), `aipdd_drama_image_redraw`
    (optionally with a mask), `aipdd_drama_image_reverse_prompt`;
    **video** `aipdd_drama_video_generate` (text-to-video), `aipdd_drama_video_i2v`
    (image-to-video, `gen_mode` is imageToVideo / imageReference),
    `aipdd_drama_video_omni_gen` (all-reference omni-gen), `aipdd_drama_video_upscale`,
    `aipdd_drama_video_edit`, `aipdd_drama_video_extend`;
    canvas generation nodes follow the same "generate, then archive" rule — products
    land in 「待分类资产」/ uncategorized automatically, including processing/transform
    results (only intermediates such as frame extraction or analysis stay out);
    **audio** `aipdd_drama_audio_separate` (vocal / accompaniment split),
    `aipdd_drama_audio_music` (ElevenLabs music);
    **text/script** `aipdd_drama_text_generate`, `aipdd_drama_text_translate`,
    `aipdd_drama_story_script_generate`, `aipdd_drama_video_script_reverse`,
    `aipdd_drama_media_copywrite` — inline plain text does not get its own entry;
    txt files (reverse script / media copy) are archived into the text carrier and
    are deliberately NOT in the `/result` whitelist — read them via
    `aipdd_drama_get_task` (`task.result`);
    **resource catalog reads** `aipdd_drama_list_assets`,
    `aipdd_drama_get_character_library`, `aipdd_drama_get_audio_references`,
    `aipdd_drama_get_models` (maps `kind` to the image / video / audio / text / vision
    catalogs).
17. One-click automation: `aipdd_drama_auto_storyboard` (automated storyboard step
    driver: after the story is uploaded it auto-detects the style → screenplay →
    episodes → storyboard script → sketches → shot first frames; each call advances
    exactly one stage and returns `stage` / `next_action` — keep calling until
    `done`. Optional model / style / aspect ratio / episode count / total &
    per-episode durations; shot counts are derived from the screenplay. It NEVER
    generates videos). Both this tool and `aipdd_drama_run_pipeline` below are **thin
    shells**: the stage state machine is unified in the authoritative backend planner
    `POST /api/v1/projects/{project}/pipeline/plan` (default `execute=false` decides
    without firing; `execute=true` fires the next stage's actions in-process), so MCP
    no longer keeps its own copy of the logic. Also `aipdd_drama_video_range` (batch-generate shot videos from
    beat numbers or a range like `1-8` with the video model you pick; beats without
    a first frame are skipped and reported in `skipped`; poll each submission via
    `aipdd_drama_get_task(task_type="single_video", beat_num=…)`), `aipdd_drama_regen_sketches`
    (batch-regenerate shot sketches by beat numbers or a range like `1-8`; omit beats to cover
    the whole episode; requires the storyboard script first; poll
    `aipdd_drama_get_task(task_type="sketch_regen", episode=…)`), and
    `aipdd_drama_generate_video_prompts` (batch-generate shot video prompts one by one from beat
    numbers or a range; beats missing prerequisites are reported in `skipped`; poll
    `aipdd_drama_get_task(task_type="beat_video_prompt", beat_num=…)`).
18. Orchestration layer (production line, giving the user the choice):
    `aipdd_drama_run_pipeline` wraps the same backend planner in three modes — `manual` advances one
    stage; `checkpoint` (default) decides without firing and returns a `planned_requests`
    gate that only submits when re-called with `approve=true` (system-led with per-stage
    confirmation); `auto` advances to **shot first-frame completion** within `budget_s` and
    **never submits video** (use `aipdd_drama_video_range` for videos); `use_suggested=true` fills
    model / aspect / style from smart defaults. Also
    `aipdd_drama_get_run_status` (aggregate pipeline progress with running / failed / completed
    tasks and per-episode shot counts), `aipdd_drama_cancel_run` (stop an episode's in-flight
    tasks), and `aipdd_drama_suggest_config` (recommend a generation config with rationale).
19. Executable work plans (user writes the plan → the agent does the work):
    `aipdd_drama_update_plan` (add / update / complete / remove; an item with a `stage`
    milestone — story / screenplay / ingest / characters / episodes / identities / script /
    scenes / props / sketches / frames — and an optional `episode` becomes executable;
    items without a stage stay manual to-dos), `aipdd_drama_get_plan` (read the plan, open
    count and executable items, so the agent confirms before acting), and
    `aipdd_drama_run_plan` (`mode=auto` keeps advancing within `budget_s`; `manual` advances
    one step; `checkpoint` previews only). The runner reuses the same backend planner and
    ticks items off real artifacts (story text, characters, episodes, identities, shot
    script, scenes/props, sketches, first frames), so repeated calls are idempotent and
    completed items never re-run. It also stops at shot first frames and **never generates
    video**. The HTTP side is `GET/POST/PATCH/DELETE /api/v1/agent/plan*` plus
    `POST /api/v1/agent/plan/run`; the Agent page's plan panel shows stages and can run it.
20. Novel story source / chapters / motion / exports: `aipdd_drama_get_story_source` /
    `aipdd_drama_save_story_source` (the entry point when the user pastes a whole novel
    into the Agent; validated the same way as the web import), `aipdd_drama_get_chapters`
    (chapter structure; metadata only by default, `chapter=N` returns one body),
    `aipdd_drama_update_chapter` (save a chapter body; the title line is preserved),
    `aipdd_drama_start_ingest` (start
    `ingest_fast` for a file already in `uploads/`; pair with
    `aipdd_drama_list_ingest_uploads`). Motion graphics: `aipdd_drama_motion_cases` /
    `aipdd_drama_motion_styles` (case and style catalogs; pass an id for the full prompt),
    `aipdd_drama_motion_renders` (finished clips), `aipdd_drama_motion_render` (one MG clip,
    `freezone_motion_render`), `aipdd_drama_motion_stills` (beat stills preflight,
    `freezone_motion_stills`), `aipdd_drama_motion_draft` (clips → Jianying draft,
    `freezone_motion_draft`). Exports: `aipdd_drama_get_subtitles` (one episode's SRT) and
    `aipdd_drama_export_episode` (`kind=video` saves the final mp4, `kind=zip` saves the
    episode resource pack; streamed to disk and returns the local path).

Generation is **asynchronous**: a tool call starts a task, then the agent polls
`aipdd_drama_pipeline_status` / `aipdd_drama_list_tasks` / `aipdd_drama_get_task` until
it completes. Each tool's description names the exact task to poll. The generic
`aipdd_drama_get` / `aipdd_drama_post` / `aipdd_drama_patch` / `aipdd_drama_delete` tools
are an escape hatch for any endpoint not covered by a curated verb.

> **Canvas (freezone).** The common generation / edit / resource actions are now
> distilled into the curated verbs above, one-to-one with the web nodes. Canvas-
> internal operations (canvases CRUD / restore / projections / 3D / screenshot /
> marks / detect) have no dedicated verb; reach them through the generic ones:
> list canvases with
> `aipdd_drama_get('/projects/{p}/freezone/canvases')`, inspect one with
> `…/freezone/canvases/{canvas_id}`, and trigger the node-equivalent image
> generation with `aipdd_drama_post('/projects/{p}/freezone/gen', {…})` — exactly
> what the web canvas does.

> **Motion feature film (MG)** has dedicated verbs: `aipdd_drama_motion_cases` /
> `aipdd_drama_motion_styles` read the case and style catalogs (pass `case_id` /
> `style_id` for the full prompt), `aipdd_drama_motion_render` starts a render
> (paste `html`, or pass `case_id`/`style_id` + `subject` and let the text model
> write the page), `aipdd_drama_motion_stills` captures beat stills as a
> preflight, `aipdd_drama_motion_renders` lists finished films (the source of
> truth is the on-disk `*.motion.json` sidecars, not the task records), and
> `aipdd_drama_motion_draft` exports a Jianying / CapCut 10.2 draft. Task types
> are `freezone_motion_render` / `freezone_motion_stills` / `freezone_motion_draft`;
> poll with `aipdd_drama_get_task(task_type=..., episode=0, scope=<job_id>)`.
> This chain needs a local Chrome and ffmpeg — see the
> [MG guide](motion-feature-film.md).

## Security notes

- CE-owner mode grants **full owner access** to the instance it points at — only
  use it against a **local** CE you control. Never point it at a shared instance.
- The bridge enforces this: tokenless CE-owner mode only accepts a **loopback**
  `DRAMACLAW_API_URL` and errors out on any remote host unless you set the
  explicit `DRAMACLAW_CE_OWNER_ALLOW_REMOTE=1` override.
- On EE, use a scoped `DRAMACLAW_AGENT_TOKEN`; the server enforces the token's
  project boundary and write scopes.
