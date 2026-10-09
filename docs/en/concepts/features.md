<!-- lang-switch -->
**English** · [简体中文](../../zh/concepts/features.md)

# Feature Overview

What DramaClaw CE can do — a complete pipeline that turns a **novel manuscript** into a **finished video**, running entirely on your own machine, BYO model key, no PostgreSQL / Redis required.

## Creative Flow

### 📥 Import and Story Graph
Import a novel manuscript; the `cognee` knowledge graph **parses the source text** and builds queryable **characters, relationships, and a timeline** that ground the planning that follows. You can also arrange assets and ideas directly on the **Freezone free-form canvas** as a starting point.

### 📖 Script Generation and Episodes
Turn the novel into a **structured script**, automatically **split into episodes and scenes**, organized in a unified script format (episode-scene, characters, shot description, dialogue, sound effects / VFX / system audio). Supports adaptation / direct-translation / storyboard modes, with a review-and-repair loop.

### 🎭 Characters and Props
AI generates **character designs** and **three-view prop sheets**, building reusable identity assets; identity schemes keep characters / props **visually consistent** across different shots, and per-episode variants can be generated.

### 🏞️ Scenes, Sketches, and First Frames
Build a **scene library** and generate shot **sketches** and **first-frame images**; the optional `director_world` (3GS / world) feature enhances scenes. Each shot sets its visual tone before moving into production.

### 🎬 Final Composition and Export
Shot-by-shot **video generation** plus emotion-aware **voiceover (TTS)**, then **compose, edit, and export an MP4** (including subtitles and a full asset bundle).

## Capabilities Throughout

- **Task center**: progress tracking for long tasks, **resume from checkpoint**, and cancellation (in-process execution, with an experience matching the Enterprise Edition).
- **Freezone (infinite canvas)**: a node-based visual workbench — drag in project assets to generate images / video / audio, open Director World, and promote satisfying candidates back to the main pipeline; main pipeline and free exploration run as dual tracks.
- **Visual Style (style templates)**: set a project-wide style template (style prompts, avoid-instructions, style tags), upload a reference image to auto-extract parameters, and apply it across the whole project.
- **Xia Director (Chat / Agent)**: a conversational creative assistant that runs through every stage — script, assets, and scenes.
- **Multi-model BYO**: all text / image / video / audio models are reached through a single OpenAI-compatible gateway; use an official key or [bring your own gateway](../getting-started/configuring-models.md).
- **Single-machine self-hosting**: local auth, no-login single user, inline execution, up and running with `docker compose up`; all data stays local.

## Boundaries

CE = Community Edition (source-available, ELv2), covering the **full set of individual creative capabilities**. Multi-tenancy, team collaboration, billing quotas, scaled-out execution, and other capabilities aimed at "managing others / charging others / operating on others' behalf" belong to the Enterprise Edition and are not in this repo.

---

> To understand how it works, see [Architecture](architecture.md); to get it running first, see [Quickstart](../getting-started/quickstart.md).
