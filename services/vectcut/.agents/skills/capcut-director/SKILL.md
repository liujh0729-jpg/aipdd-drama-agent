---
name: capcut-director
description: Expert skill for programmatically creating, editing, styling, and rendering cinematic video projects via CapCut MCP and direct timeline engineering. Covers multi-timeline CapCut 9.x+ architecture, 9:16 vertical canvas configuration, professional typography/subtitling, audio mastering, process locking, and companion rendering.
---

# CapCut Video Director & Timeline Engineering Skill

This skill guides AI agents in programmatically directing, assembling, and polishing video projects using the CapCut MCP server and direct CapCut Desktop timeline engineering.

---

## 1. CapCut Desktop Architecture & File System

### Project Locations
- **Windows**: `C:\Users\<Username>\AppData\Local\CapCut\User Data\Projects\com.lveditor.draft\<ProjectName>`
- **macOS**: `~/Library/Containers/com.lemon.lvpro/Data/Documents/JianyingPro/User Data/Projects/com.lveditor.draft/<ProjectName>`

### The Multi-Timeline Architecture (CapCut 9.x+)
Modern CapCut Desktop (v9.0+) uses a nested multi-timeline system:
```
com.lveditor.draft/<ProjectName>/
├── draft_content.json             <-- Root compatibility state
├── draft_meta_info.json           <-- Project name, thumbnail, edit time
├── draft_cover.jpg                <-- Cover thumbnail
├── .locked                        <-- Lock file present while project is open in CapCut
└── Timelines/
    └── <TIMELINE-UUID>/
        ├── draft_content.json     <-- ACTIVE TIMELINE STATE (Read by CapCut 9.x+!)
        ├── draft_content.json.bak <-- Autosave backup
        └── template.tmp           <-- Working buffer
```

> [!IMPORTANT]
> **CapCut 9.x+ reads `Timelines/<UUID>/draft_content.json`**, NOT the root file alone! If you only write to the root `draft_content.json` (as standard legacy MCP libraries do), CapCut will open an empty timeline. Always write to both the root and the active timeline directory.

---

## 2. In-Memory Process Lifecycle & The '.locked' Rule

CapCut Desktop loads project files into C++ RAM upon opening and **does not feature a live file watcher**.

1. **Avoid Overwrites**: While CapCut is open, its background autosave thread will overwrite any external disk changes with its in-memory state.
2. **Workflow for External Updates**:
   ```powershell
   # 1. Close CapCut before modifying draft files
   Get-Process -Name 'CapCut' -ErrorAction SilentlyContinue | Stop-Process -Force
   Start-Sleep -Seconds 1

   # 2. Write updated draft_content.json
   # ... (write files)

   # 3. Remove stale lock files
   Get-ChildItem -Path "$env:LOCALAPPDATA\CapCut\User Data\Projects\com.lveditor.draft" -Recurse -Filter '.locked' | Remove-Item -Force
   ```
3. **Reopening**: Users must return to the CapCut Home screen or relaunch CapCut to reload the updated project from disk.

---

## 3. Canvas & Aspect Ratio Configuration

When creating a vertical project (e.g. 1080x1920 9:16), CapCut requires explicit `canvas_config` settings in `draft_content.json`. Otherwise, the preview player defaults to widescreen `[16:9]`:

```json
{
  "canvas_config": {
    "height": 1920,
    "width": 1080,
    "ratio": "9:16"
  }
}
```

---

## 4. Professional Typography & Subtitle Standards

### Mobile (9:16) Sizing Guidelines
Text must be legible on small mobile viewports. Avoid small desktop font defaults:

| Text Type | Font Size (FFmpeg / pt) | CapCut Size Value | Purpose |
|---|---|---|---|
| **Main Title / Hero Hook** | 80pt – 135pt | 9.0 – 12.0 | Strong focal impact, title cards |
| **Secondary Sub-headline** | 68pt – 85pt | 8.0 – 9.5 | Key concepts, etymology breakdown |
| **Subtitles / Explanations** | 56pt – 64pt | 6.5 – 7.5 | Minimum size for effortless mobile reading |
| **Micro Labels / Badges** | 42pt – 48pt | 5.0 – 5.5 | Metadata, subtle tags only |

### Line Spacing & Vertical Breathing Room
- **Default CapCut line spacing (`0.05`) is too cramped** for multi-line titles, causing text lines to collide.
- Always set `"line_spacing": 0.25` to `0.32` for multi-line text blocks.
- Separate distinct text layers by at least `115px – 140px` vertically in 1080x1920 space.

### Contrast & Legibility Over Dynamic Footage
Never render plain text over variable video backgrounds:
1. **Multi-layer Drop Shadows**:
   - Primary shadow: `angle: -45.0, distance: 4.0 - 6.0, alpha: 0.95, diffuse: 0.08`
2. **Backdrop Box / Gradient Plate**:
   - Semi-transparent dark backplate: `box=1:boxcolor=black@0.35:boxborderw=16`
3. **Safe Zones**:
   - Avoid placing subtitles at `y < 15%` (header UI/notch) or `y > 85%` (caption/bottom bar UI).
   - Ideal lower-third subtitle placement: `y = 75% - 80%`.
   - Upper-third placement (for scenes with bottom subjects): `y = 20% - 25%`.

---

## 5. Audio Mastering & Sidechain Ducking

Cinematic videos require layered sound design:

```
Timeline:
Track 1: Ambient / Cinematic Music (-13 dB during speech, -5.5 dB in pauses)
Track 2: Voiceover (Loudness normalized, +1.25 volume, 80Hz high-pass filter)
Track 3: SFX Accents (Chimes, whooshes, risers, time-aligned to visual cuts)
```

- Apply **dynamic sidechain ducking** (`-7.5 dB` to `-10 dB` reduction) on background music whenever the voiceover track is active.
- Ensure audio ends with a `0.8s – 1.2s` smooth fade-out.

---

## 6. End-to-End Execution Workflow

1. **Initialize Project**: Create the draft and import curated media via CapCut MCP or direct Python script.
2. **Inject Multi-Timeline Structure**: Write `Timelines/<UUID>/draft_content.json` with 9:16 canvas, scaled typography, and generous line spacing.
3. **Master Render**: Render an exact 30fps H.264 master video with normalized SAR (`setsar=1`) and AAC audio for instant preview.
4. **Inspect Keyframes**: Extract single-frame JPGs across all scene boundaries (`ffmpeg -ss ... -vframes 1`) to verify text centering and legibility before delivering.

---

## 7. Live Editing Reflection: Web Preview & Desktop Hot-Reload

To eliminate manual reopening and delays, use the dual live-editing system:

### 1. Web Live-Preview Player (0ms Feedback)
- **URL**: `http://127.0.0.1:9001/preview`
- Real-time HTML5 9:16 canvas player with interactive multi-track timeline scrubber.
- **Server-Sent Events (SSE)**: Automatically wakes up and reflects edits made via MCP or API without browser refresh.
- **Live Element Inspector**: Allows instant in-browser tweaking of text strings, font sizes, colors, and coordinates with the "Apply Live Changes" button.
- **Direct Actions**:
  - `⚡ Sync to CapCut`: Saves and deploys draft directly to CapCut Desktop.
  - `🔄 Reload Desktop`: Triggers automated CapCut desktop hot-reload.

### 2. Desktop Auto-Reload Companion
- **MCP Tool**: `capcut_reload_desktop(project_name=...)`
- **Auto-Reload on Save**: Pass `auto_reload: true` to `capcut_save_draft` to automatically refresh CapCut Desktop immediately after saving.
- Automates project cycling (Home -> Reopen) in under 1 second without manual mouse intervention.