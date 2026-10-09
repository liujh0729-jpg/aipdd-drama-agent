"""
CapCut Live Web Preview Player & SSE Sync Server
Provides real-time 9:16 canvas rendering, interactive timeline scrubbing,
and instant hot-reloading reflection for CapCut projects.
"""

import os
import json
import time
import queue
import urllib.parse
from flask import Blueprint, request, jsonify, Response, send_file, render_template_string
from desktop_companion import reload_capcut_desktop, get_capcut_windows

preview_bp = Blueprint("preview", __name__)

CAPCUT_DRAFTS_DIR = os.path.expandvars(r"%LOCALAPPDATA%\CapCut\User Data\Projects\com.lveditor.draft")

# Global subscriber list for Server-Sent Events
_sse_subscribers = set()


def broadcast_draft_update(draft_id=None, action="update", project_name=None):
    """Notify all connected preview browser tabs that the draft was updated."""
    msg = {
        "event": "draft_updated",
        "action": action,
        "draft_id": draft_id,
        "project_name": project_name,
        "timestamp": time.time()
    }
    dead_subs = set()
    for q in _sse_subscribers:
        try:
            q.put_nowait(msg)
        except Exception:
            dead_subs.add(q)
    for q in dead_subs:
        _sse_subscribers.discard(q)


def get_latest_project_path():
    """Finds the most recently updated CapCut project on disk."""
    if not os.path.exists(CAPCUT_DRAFTS_DIR):
        return None
    dirs = [
        os.path.join(CAPCUT_DRAFTS_DIR, d)
        for d in os.listdir(CAPCUT_DRAFTS_DIR)
        if os.path.isdir(os.path.join(CAPCUT_DRAFTS_DIR, d))
    ]
    if not dirs:
        return None
    dirs.sort(key=lambda p: os.path.getmtime(p), reverse=True)
    return dirs[0]


def parse_draft_json(draft_content):
    """Parses CapCut draft_content.json into a clean structure for web preview."""
    canvas_cfg = draft_content.get("canvas_config", {"width": 1080, "height": 1920, "ratio": "9:16"})
    materials = draft_content.get("materials", {})
    tracks = draft_content.get("tracks", [])

    # Index materials by ID
    text_mats = {m["id"]: m for m in materials.get("texts", [])}
    video_mats = {m["id"]: m for m in materials.get("videos", [])}
    audio_mats = {m["id"]: m for m in materials.get("audios", [])}

    video_clips = []
    text_clips = []
    audio_clips = []
    max_duration_sec = 0.0

    for track in tracks:
        track_type = track.get("type", "")
        segments = track.get("segments", [])

        for seg in segments:
            mat_id = seg.get("material_id")
            timerange = seg.get("target_timerange", {})
            start_us = timerange.get("start", 0)
            dur_us = timerange.get("duration", 0)
            start_sec = round(start_us / 1_000_000, 3)
            dur_sec = round(dur_us / 1_000_000, 3)
            end_sec = round(start_sec + dur_sec, 3)
            if end_sec > max_duration_sec:
                max_duration_sec = end_sec

            clip_info = seg.get("clip") or {}
            transform = (clip_info.get("transform") or {"x": 0.0, "y": 0.0}) if isinstance(clip_info, dict) else {"x": 0.0, "y": 0.0}
            scale = (clip_info.get("scale") or {"x": 1.0, "y": 1.0}) if isinstance(clip_info, dict) else {"x": 1.0, "y": 1.0}

            if track_type == "video" and mat_id in video_mats:
                vmat = video_mats[mat_id]
                video_clips.append({
                    "id": seg.get("id"),
                    "material_id": mat_id,
                    "name": vmat.get("material_name", "Video Clip"),
                    "path": vmat.get("path", ""),
                    "start_sec": start_sec,
                    "duration_sec": dur_sec,
                    "end_sec": end_sec,
                    "transform": transform,
                    "scale": scale,
                    "speed": seg.get("speed", 1.0),
                    "volume": seg.get("volume", 1.0)
                })

            elif track_type == "text" and mat_id in text_mats:
                tmat = text_mats[mat_id]
                raw_content = tmat.get("content", "")
                text_str = ""
                font_size = tmat.get("font_size", 12.0)
                bold = False
                italic = False
                underline = False
                text_color = "#FFFFFF"

                if isinstance(raw_content, str):
                    try:
                        parsed_content = json.loads(raw_content)
                        text_str = parsed_content.get("text", "")
                        styles = parsed_content.get("styles", [])
                        if styles:
                            st = styles[0]
                            bold = st.get("bold", False)
                            italic = st.get("italic", False)
                            underline = st.get("underline", False)
                            fill_color = st.get("fill", {}).get("content", {}).get("solid", {}).get("color", [])
                            if len(fill_color) >= 3:
                                r = int(fill_color[0] * 255)
                                g = int(fill_color[1] * 255)
                                b = int(fill_color[2] * 255)
                                text_color = f"#{r:02x}{g:02x}{b:02x}"
                    except Exception:
                        text_str = raw_content

                text_clips.append({
                    "id": seg.get("id"),
                    "material_id": mat_id,
                    "text": text_str,
                    "start_sec": start_sec,
                    "duration_sec": dur_sec,
                    "end_sec": end_sec,
                    "font_size": font_size,
                    "bold": bold,
                    "italic": italic,
                    "underline": underline,
                    "color": text_color,
                    "align": tmat.get("alignment", 1),  # 0=left, 1=center, 2=right
                    "line_spacing": tmat.get("line_spacing", 0.0),
                    "letter_spacing": tmat.get("letter_spacing", 0.0),
                    "transform": transform,
                    "scale": scale
                })

            elif track_type == "audio" and mat_id in audio_mats:
                amat = audio_mats[mat_id]
                audio_clips.append({
                    "id": seg.get("id"),
                    "material_id": mat_id,
                    "name": amat.get("name", "Audio Track"),
                    "path": amat.get("path", ""),
                    "start_sec": start_sec,
                    "duration_sec": dur_sec,
                    "end_sec": end_sec,
                    "volume": seg.get("volume", 1.0)
                })

    return {
        "canvas": canvas_cfg,
        "total_duration_sec": max(max_duration_sec, 5.0),
        "video_clips": video_clips,
        "text_clips": text_clips,
        "audio_clips": audio_clips
    }


@preview_bp.route("/preview/media")
def serve_media():
    """Serves a local media file for the preview canvas player."""
    raw_path = request.args.get("path")
    if not raw_path:
        return "Missing path parameter", 400
    path = urllib.parse.unquote(raw_path)
    if not os.path.exists(path):
        return f"File not found: {path}", 404
    return send_file(path)


@preview_bp.route("/api/active_draft")
def api_active_draft():
    """Returns the parsed timeline and materials for the requested or latest project."""
    project_name = request.args.get("project_name")
    draft_folder = request.args.get("draft_folder")

    target_dir = None
    if project_name and os.path.exists(os.path.join(CAPCUT_DRAFTS_DIR, project_name)):
        target_dir = os.path.join(CAPCUT_DRAFTS_DIR, project_name)
    elif draft_folder and os.path.exists(draft_folder):
        target_dir = draft_folder
    else:
        target_dir = get_latest_project_path()

    if not target_dir or not os.path.exists(target_dir):
        return jsonify({"success": False, "error": "No draft found."})

    draft_file = os.path.join(target_dir, "draft_content.json")
    if not os.path.exists(draft_file):
        return jsonify({"success": False, "error": f"draft_content.json missing in {target_dir}"})

    try:
        with open(draft_file, "r", encoding="utf-8") as f:
            raw = json.load(f)
        parsed = parse_draft_json(raw)
        parsed["project_name"] = os.path.basename(target_dir)
        parsed["project_path"] = target_dir
        return jsonify({"success": True, "data": parsed})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})


@preview_bp.route("/api/preview_update", methods=["POST"])
def api_preview_update():
    """Applies quick text tweaks to draft_content.json directly from the web inspector."""
    data = request.get_json() or {}
    project_name = data.get("project_name")
    material_id = data.get("material_id")
    new_text = data.get("text")
    transform_x = data.get("transform_x")
    transform_y = data.get("transform_y")
    font_size = data.get("font_size")
    color = data.get("color")

    target_dir = os.path.join(CAPCUT_DRAFTS_DIR, project_name) if project_name else get_latest_project_path()
    if not target_dir or not os.path.exists(target_dir):
        return jsonify({"success": False, "error": "Target project not found."})

    draft_file = os.path.join(target_dir, "draft_content.json")
    try:
        with open(draft_file, "r", encoding="utf-8") as f:
            content = json.load(f)

        # Update text material
        for tmat in content.get("materials", {}).get("texts", []):
            if tmat.get("id") == material_id:
                if font_size is not None:
                    tmat["font_size"] = float(font_size)
                
                raw = tmat.get("content", "")
                try:
                    c_obj = json.loads(raw)
                    if new_text is not None:
                        c_obj["text"] = new_text
                    if color and c_obj.get("styles"):
                        # Parse hex to rgb
                        h = color.lstrip("#")
                        rgb = [int(h[i:i+2], 16) / 255.0 for i in (0, 2, 4)]
                        c_obj["styles"][0]["fill"]["content"]["solid"]["color"] = rgb
                    tmat["content"] = json.dumps(c_obj, ensure_ascii=False)
                except Exception:
                    if new_text is not None:
                        tmat["content"] = new_text

        # Update transform if provided
        if transform_x is not None or transform_y is not None:
            for tr in content.get("tracks", []):
                for seg in tr.get("segments", []):
                    if seg.get("material_id") == material_id:
                        clip = seg.setdefault("clip", {}).setdefault("transform", {})
                        if transform_x is not None:
                            clip["x"] = float(transform_x)
                        if transform_y is not None:
                            clip["y"] = float(transform_y)

        with open(draft_file, "w", encoding="utf-8") as f:
            json.dump(content, f, ensure_ascii=False, indent=2)

        # Broadcast update
        broadcast_draft_update(project_name=os.path.basename(target_dir), action="edit")

        return jsonify({"success": True, "message": "Updated draft successfully."})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})


@preview_bp.route("/api/trigger_desktop_reload", methods=["POST"])
def api_trigger_desktop_reload():
    """Triggers the desktop companion to hot-reload CapCut Desktop."""
    data = request.get_json() or {}
    project_name = data.get("project_name")
    res = reload_capcut_desktop(project_name)
    return jsonify(res)


@preview_bp.route("/api/preview_events")
def api_preview_events():
    """Server-Sent Events endpoint for real-time live preview synchronization."""
    def event_stream():
        q = queue.Queue()
        _sse_subscribers.add(q)
        try:
            # Initial ping
            yield "data: {\"type\": \"connected\"}\n\n"
            while True:
                try:
                    msg = q.get(timeout=20.0)
                    yield f"data: {json.dumps(msg)}\n\n"
                except queue.Empty:
                    # Keep-alive heartbeat
                    yield ": heartbeat\n\n"
        finally:
            _sse_subscribers.discard(q)

    return Response(event_stream(), mimetype="text/event-stream")


HTML_PREVIEW_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Antigravity CapCut Studio & Live Preview</title>
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link href="https://fonts.googleapis.com/css2?family=Cinzel:wght@500;700;900&family=Montserrat:wght@400;600;700&family=Noto+Sans+Devanagari:wght@400;600;700&display=swap" rel="stylesheet">
  <style>
    :root {
      --bg-dark: #0a0c10;
      --panel-bg: #12151c;
      --card-bg: #181d26;
      --border-color: #242c3b;
      --accent-gold: #ffd700;
      --accent-cyan: #00e5ff;
      --accent-green: #00e676;
      --text-main: #f0f3f8;
      --text-muted: #8a99ad;
      --phone-w: 360px;
      --phone-h: 640px;
    }
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body {
      background: var(--bg-dark);
      color: var(--text-main);
      font-family: 'Montserrat', system-ui, -apple-system, sans-serif;
      height: 100vh;
      display: flex;
      flex-direction: column;
      overflow: hidden;
    }
    /* Header */
    header {
      background: var(--panel-bg);
      border-bottom: 1px solid var(--border-color);
      padding: 12px 24px;
      display: flex;
      align-items: center;
      justify-content: space-between;
      height: 60px;
    }
    .brand {
      display: flex;
      align-items: center;
      gap: 12px;
    }
    .brand-badge {
      background: linear-gradient(135deg, #ff9900, #ff5500);
      color: #fff;
      font-weight: 800;
      font-size: 11px;
      padding: 3px 8px;
      border-radius: 4px;
      letter-spacing: 1px;
    }
    .project-selector {
      background: var(--card-bg);
      border: 1px solid var(--border-color);
      color: var(--text-main);
      padding: 6px 12px;
      border-radius: 6px;
      font-weight: 600;
      font-size: 13px;
      cursor: pointer;
    }
    .status-badge {
      display: inline-flex;
      align-items: center;
      gap: 6px;
      font-size: 12px;
      font-weight: 600;
      padding: 4px 10px;
      border-radius: 12px;
      background: rgba(0, 230, 118, 0.1);
      color: var(--accent-green);
      border: 1px solid rgba(0, 230, 118, 0.2);
    }
    .status-dot {
      width: 7px;
      height: 7px;
      background: var(--accent-green);
      border-radius: 50%;
      animation: pulse 1.5s infinite;
    }
    @keyframes pulse {
      0%, 100% { opacity: 1; transform: scale(1); }
      50% { opacity: 0.4; transform: scale(1.2); }
    }
    .header-actions {
      display: flex;
      align-items: center;
      gap: 12px;
    }
    button.btn {
      display: inline-flex;
      align-items: center;
      gap: 6px;
      background: var(--card-bg);
      border: 1px solid var(--border-color);
      color: var(--text-main);
      padding: 7px 14px;
      border-radius: 6px;
      font-size: 12px;
      font-weight: 600;
      cursor: pointer;
      transition: all 0.2s ease;
    }
    button.btn:hover {
      background: var(--border-color);
      transform: translateY(-1px);
    }
    button.btn-primary {
      background: linear-gradient(135deg, #ffd700, #ffaa00);
      color: #000;
      border: none;
      font-weight: 700;
    }
    button.btn-primary:hover {
      filter: brightness(1.1);
      box-shadow: 0 4px 12px rgba(255, 215, 0, 0.3);
    }
    button.btn-cyan {
      background: linear-gradient(135deg, #00e5ff, #0099ff);
      color: #000;
      border: none;
      font-weight: 700;
    }
    /* Main Layout */
    .workspace {
      flex: 1;
      display: flex;
      overflow: hidden;
    }
    /* Viewport Area */
    .viewport-container {
      flex: 1;
      background: radial-gradient(circle at center, #151a24 0%, #080a0e 100%);
      display: flex;
      flex-direction: column;
      align-items: center;
      justify-content: center;
      position: relative;
      padding: 20px;
    }
    .phone-mockup {
      width: var(--phone-w);
      height: var(--phone-h);
      background: #000;
      border-radius: 36px;
      border: 4px solid #2d3546;
      box-shadow: 0 20px 60px rgba(0, 0, 0, 0.8), 0 0 40px rgba(0, 229, 255, 0.1);
      position: relative;
      overflow: hidden;
      display: flex;
      align-items: center;
      justify-content: center;
    }
    .phone-notch {
      position: absolute;
      top: 10px;
      width: 90px;
      height: 18px;
      background: #181d26;
      border-radius: 12px;
      z-index: 10;
    }
    canvas#stage {
      width: 100%;
      height: 100%;
      display: block;
      background: #000;
    }
    /* Hidden media elements for canvas sampling */
    video.media-source {
      display: none;
    }
    /* Sidebar Inspector */
    .sidebar {
      width: 380px;
      background: var(--panel-bg);
      border-left: 1px solid var(--border-color);
      display: flex;
      flex-direction: column;
      overflow-y: auto;
    }
    .sidebar-header {
      padding: 16px;
      border-bottom: 1px solid var(--border-color);
      font-size: 13px;
      font-weight: 700;
      text-transform: uppercase;
      letter-spacing: 0.5px;
      color: var(--accent-cyan);
      display: flex;
      justify-content: space-between;
      align-items: center;
    }
    .sidebar-content {
      padding: 16px;
      display: flex;
      flex-direction: column;
      gap: 16px;
    }
    .card {
      background: var(--card-bg);
      border: 1px solid var(--border-color);
      border-radius: 8px;
      padding: 14px;
    }
    .card-title {
      font-size: 12px;
      font-weight: 700;
      color: var(--accent-gold);
      margin-bottom: 10px;
      display: flex;
      justify-content: space-between;
    }
    .form-group {
      margin-bottom: 12px;
    }
    .form-group label {
      display: block;
      font-size: 11px;
      font-weight: 600;
      color: var(--text-muted);
      margin-bottom: 4px;
    }
    .form-control {
      width: 100%;
      background: #0d1017;
      border: 1px solid var(--border-color);
      color: #fff;
      padding: 8px 10px;
      border-radius: 6px;
      font-size: 12px;
      font-family: inherit;
    }
    .form-control:focus {
      outline: none;
      border-color: var(--accent-cyan);
    }
    textarea.form-control {
      resize: vertical;
      min-height: 54px;
    }
    .range-row {
      display: flex;
      align-items: center;
      gap: 8px;
    }
    .range-val {
      font-size: 11px;
      font-weight: 700;
      color: var(--accent-cyan);
      min-width: 36px;
      text-align: right;
    }
    /* Timeline Controls */
    .timeline-panel {
      height: 170px;
      background: var(--panel-bg);
      border-top: 1px solid var(--border-color);
      display: flex;
      flex-direction: column;
    }
    .timeline-controls {
      height: 44px;
      padding: 0 16px;
      display: flex;
      align-items: center;
      justify-content: space-between;
      border-bottom: 1px solid var(--border-color);
    }
    .transport-btns {
      display: flex;
      align-items: center;
      gap: 8px;
    }
    .btn-icon {
      background: var(--card-bg);
      border: 1px solid var(--border-color);
      color: #fff;
      width: 30px;
      height: 30px;
      border-radius: 6px;
      display: flex;
      align-items: center;
      justify-content: center;
      cursor: pointer;
      font-size: 13px;
    }
    .btn-icon:hover { background: var(--border-color); }
    .timecode {
      font-family: 'Courier New', monospace;
      font-size: 13px;
      font-weight: 700;
      color: var(--accent-gold);
    }
    .timeline-body {
      flex: 1;
      padding: 8px 16px;
      display: flex;
      flex-direction: column;
      gap: 6px;
      position: relative;
    }
    .scrubber-bar {
      position: relative;
      height: 20px;
      cursor: pointer;
      display: flex;
      align-items: center;
    }
    .scrubber-track {
      width: 100%;
      height: 4px;
      background: var(--border-color);
      border-radius: 2px;
      position: relative;
    }
    .scrubber-progress {
      position: absolute;
      left: 0;
      top: 0;
      height: 100%;
      background: var(--accent-gold);
      border-radius: 2px;
      width: 0%;
    }
    .scrubber-needle {
      position: absolute;
      top: -6px;
      left: 0%;
      width: 12px;
      height: 16px;
      background: #fff;
      border-radius: 2px;
      box-shadow: 0 2px 6px rgba(0,0,0,0.5);
      transform: translateX(-50%);
      pointer-events: none;
    }
    .track-lanes {
      flex: 1;
      display: flex;
      flex-direction: column;
      gap: 4px;
    }
    .lane {
      height: 24px;
      background: rgba(255, 255, 255, 0.02);
      border-radius: 4px;
      position: relative;
      overflow: hidden;
    }
    .segment-block {
      position: absolute;
      top: 2px;
      height: 20px;
      border-radius: 3px;
      font-size: 10px;
      padding: 2px 6px;
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
      cursor: pointer;
      font-weight: 600;
      color: #000;
      display: flex;
      align-items: center;
    }
    .block-video { background: #4fc3f7; }
    .block-text { background: #ffd54f; }
    .block-audio { background: #81c784; }
    .segment-block.selected {
      outline: 2px solid #fff;
      box-shadow: 0 0 8px #fff;
    }
  </style>
</head>
<body>

  <!-- Top Navigation Header -->
  <header>
    <div class="brand">
      <span class="brand-badge">CAPCUT STUDIO</span>
      <select id="projectSelect" class="project-selector" onchange="loadProject(this.value)">
        <option value="">Loading drafts...</option>
      </select>
      <div class="status-badge">
        <span class="status-dot"></span>
        <span id="sseStatus">Live Sync Active</span>
      </div>
    </div>
    <div class="header-actions">
      <button class="btn btn-cyan" onclick="triggerDesktopReload()">
        🔄 Reload Desktop
      </button>
      <button class="btn btn-primary" onclick="deployToCapCut()">
        ⚡ Sync to CapCut
      </button>
    </div>
  </header>

  <!-- Workspace: Canvas & Inspector -->
  <div class="workspace">
    
    <!-- Central 9:16 Canvas Phone Viewport -->
    <div class="viewport-container">
      <div class="phone-mockup">
        <div class="phone-notch"></div>
        <canvas id="stage" width="1080" height="1920"></canvas>
      </div>
      <!-- Hidden Video Element for Video Clips -->
      <video id="activeVideoPlayer" class="media-source" playsinline muted></video>
    </div>

    <!-- Right Sidebar Inspector -->
    <div class="sidebar">
      <div class="sidebar-header">
        <span>Live Element Inspector</span>
        <span id="selectedBadge" style="font-size: 10px; color: var(--text-muted);">None</span>
      </div>
      <div class="sidebar-content" id="inspectorContent">
        <div class="card" style="text-align: center; color: var(--text-muted); font-size: 12px;">
          Click any text segment in the timeline or preview to tweak styling and coordinates in real-time.
        </div>
      </div>
    </div>

  </div>

  <!-- Bottom Timeline & Scrubber Panel -->
  <div class="timeline-panel">
    <div class="timeline-controls">
      <div class="transport-btns">
        <button class="btn-icon" onclick="stepFrame(-1)">⏮</button>
        <button class="btn-icon" id="playBtn" onclick="togglePlay()" style="width: 44px; font-weight: bold;">▶</button>
        <button class="btn-icon" onclick="stepFrame(1)">⏭</button>
        <button class="btn-icon" onclick="toggleLoop()" id="loopBtn" title="Toggle Loop">🔁</button>
      </div>
      <div class="timecode">
        <span id="curTime">00:00.00</span> / <span id="totalTime">00:00.00</span>
      </div>
      <div style="font-size: 11px; color: var(--text-muted);">
        9:16 Vertical Reel (1080x1920)
      </div>
    </div>
    
    <div class="timeline-body">
      <!-- Scrubber Track -->
      <div class="scrubber-bar" id="scrubberBar" onclick="onScrubClick(event)">
        <div class="scrubber-track">
          <div class="scrubber-progress" id="scrubProgress"></div>
          <div class="scrubber-needle" id="scrubNeedle"></div>
        </div>
      </div>

      <!-- Multi-Track Lanes -->
      <div class="track-lanes" id="trackLanes">
        <div class="lane" id="laneVideo" title="Video Track"></div>
        <div class="lane" id="laneText" title="Text / Subtitle Track"></div>
        <div class="lane" id="laneAudio" title="Audio Track"></div>
      </div>
    </div>
  </div>

  <script>
    let draftData = null;
    let currentTime = 0.0;
    let isPlaying = false;
    let isLooping = true;
    let selectedSegment = null;
    let animationFrameId = null;
    let lastTimestamp = 0;

    const canvas = document.getElementById("stage");
    const ctx = canvas.getContext("2d");
    const videoPlayer = document.getElementById("activeVideoPlayer");

    // Initialize Project List and SSE
    async function init() {
      await fetchProjects();
      await loadProject();
      setupSSE();
      requestAnimationFrame(renderLoop);
    }

    async function fetchProjects() {
      try {
        const res = await fetch("/list_projects");
        const json = await res.json();
        const select = document.getElementById("projectSelect");
        select.innerHTML = "";
        if (json.success && json.output.projects) {
          json.output.projects.forEach(p => {
            const opt = document.createElement("option");
            opt.value = p.name;
            opt.textContent = `${p.name} (${p.ratio || '9:16'})`;
            select.appendChild(opt);
          });
        }
      } catch (e) {
        console.error("Failed to list projects:", e);
      }
    }

    async function loadProject(projectName = "", preserveTime = false) {
      try {
        const url = projectName ? `/api/active_draft?project_name=${encodeURIComponent(projectName)}` : "/api/active_draft";
        const res = await fetch(url);
        const json = await res.json();
        if (json.success && json.data) {
          draftData = json.data;
          document.getElementById("projectSelect").value = draftData.project_name;
          document.getElementById("totalTime").textContent = formatTime(draftData.total_duration_sec);
          renderTimelineLanes();
          seek(preserveTime ? currentTime : 0);
        }
      } catch (e) {
        console.error("Failed to load draft:", e);
      }
    }

    function setupSSE() {
      const sse = new EventSource("/api/preview_events");
      const badge = document.getElementById("sseStatus");
      sse.onmessage = (e) => {
        try {
          const msg = JSON.parse(e.data);
          if (msg.event === "draft_updated") {
            badge.textContent = "Live Update Reflected!";
            loadProject(msg.project_name || draftData?.project_name, true);
            setTimeout(() => { badge.textContent = "Live Sync Active"; }, 2000);
          }
        } catch (err) {}
      };
      sse.onerror = () => {
        badge.textContent = "Reconnecting...";
      };
    }

    function togglePlay() {
      isPlaying = !isPlaying;
      document.getElementById("playBtn").textContent = isPlaying ? "⏸" : "▶";
      lastTimestamp = performance.now();
    }

    function toggleLoop() {
      isLooping = !isLooping;
      document.getElementById("loopBtn").style.borderColor = isLooping ? "var(--accent-gold)" : "var(--border-color)";
    }

    function seek(sec) {
      if (!draftData) return;
      currentTime = Math.max(0, Math.min(sec, draftData.total_duration_sec));
      updateScrubberUI();
      updateActiveVideo();
    }

    function stepFrame(frames) {
      seek(currentTime + frames * (1.0 / 30.0));
    }

    function onScrubClick(e) {
      const rect = document.getElementById("scrubberBar").getBoundingClientRect();
      const pct = Math.max(0, Math.min((e.clientX - rect.left) / rect.width, 1));
      if (draftData) {
        seek(pct * draftData.total_duration_sec);
      }
    }

    function formatTime(sec) {
      const m = Math.floor(sec / 60);
      const s = Math.floor(sec % 60);
      const ms = Math.floor((sec % 1) * 100);
      return `${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}.${String(ms).padStart(2, '0')}`;
    }

    function updateScrubberUI() {
      if (!draftData) return;
      const pct = (currentTime / draftData.total_duration_sec) * 100;
      document.getElementById("scrubProgress").style.width = pct + "%";
      document.getElementById("scrubNeedle").style.left = pct + "%";
      document.getElementById("curTime").textContent = formatTime(currentTime);
    }

    function updateActiveVideo() {
      if (!draftData) return;
      const curClip = draftData.video_clips.find(c => currentTime >= c.start_sec && currentTime < c.end_sec);
      if (curClip && curClip.path) {
        const expectedSrc = `/preview/media?path=${encodeURIComponent(curClip.path)}`;
        if (!videoPlayer.src.includes(encodeURIComponent(curClip.path))) {
          videoPlayer.src = expectedSrc;
        }
        const clipOffset = (currentTime - curClip.start_sec) * curClip.speed;
        if (Math.abs(videoPlayer.currentTime - clipOffset) > 0.3) {
          videoPlayer.currentTime = clipOffset;
        }
        if (isPlaying && videoPlayer.paused) {
          videoPlayer.play().catch(() => {});
        } else if (!isPlaying && !videoPlayer.paused) {
          videoPlayer.pause();
        }
      } else {
        if (!videoPlayer.paused) videoPlayer.pause();
      }
    }

    function renderLoop(timestamp) {
      if (isPlaying && draftData) {
        const dt = (timestamp - lastTimestamp) / 1000;
        currentTime += dt;
        if (currentTime >= draftData.total_duration_sec) {
          if (isLooping) {
            currentTime = 0;
          } else {
            currentTime = draftData.total_duration_sec;
            togglePlay();
          }
        }
        updateScrubberUI();
        updateActiveVideo();
      }
      lastTimestamp = timestamp;
      drawCanvasFrame();
      requestAnimationFrame(renderLoop);
    }

    function drawCanvasFrame() {
      const W = canvas.width;
      const H = canvas.height;
      ctx.clearRect(0, 0, W, H);

      if (!draftData) {
        ctx.fillStyle = "#111";
        ctx.fillRect(0, 0, W, H);
        return;
      }

      // 1. Draw Video / Background Layer
      const curVideo = draftData.video_clips.find(c => currentTime >= c.start_sec && currentTime < c.end_sec);
      if (curVideo && videoPlayer.readyState >= 2) {
        ctx.save();
        const tx = (curVideo.transform.x * 0.5 + 0.5) * W;
        const ty = (0.5 - curVideo.transform.y * 0.5) * H;
        ctx.translate(tx, ty);
        ctx.scale(curVideo.scale.x, curVideo.scale.y);
        ctx.drawImage(videoPlayer, -W / 2, -H / 2, W, H);
        ctx.restore();
      } else {
        // Aesthetic Dark Cinematic Gradient Placeholder
        const grad = ctx.createLinearGradient(0, 0, 0, H);
        grad.addColorStop(0, "#141721");
        grad.addColorStop(0.5, "#0b0d14");
        grad.addColorStop(1, "#18140c");
        ctx.fillStyle = grad;
        ctx.fillRect(0, 0, W, H);

        if (curVideo) {
          ctx.fillStyle = "rgba(255, 215, 0, 0.4)";
          ctx.font = "bold 42px Montserrat";
          ctx.textAlign = "center";
          ctx.fillText(curVideo.name || "Video Scene", W / 2, H / 2 - 40);
          ctx.fillStyle = "rgba(255, 255, 255, 0.3)";
          ctx.font = "28px Montserrat";
          ctx.fillText(`${curVideo.start_sec}s - ${curVideo.end_sec}s`, W / 2, H / 2 + 20);
        }
      }

      // 2. Draw Text & Subtitle Layers
      const activeTexts = draftData.text_clips.filter(t => currentTime >= t.start_sec && currentTime < t.end_sec);
      for (const t of activeTexts) {
        ctx.save();
        
        // Map normalized coordinates: (0,0) is center
        const tx = (t.transform.x * 0.5 + 0.5) * W;
        const ty = (0.5 - t.transform.y * 0.5) * H;

        ctx.translate(tx, ty);
        ctx.scale(t.scale.x, t.scale.y);

        // Styling
        const weight = t.bold ? "bold " : "";
        const italic = t.italic ? "italic " : "";
        // Convert CapCut font_size (points) to canvas px (standard scaling ~ 3.2x for 1080p canvas)
        const pxSize = Math.round(t.font_size * 3.4);
        ctx.font = `${italic}${weight}${pxSize}px 'Cinzel', 'Noto Sans Devanagari', 'Montserrat', sans-serif`;

        // Alignment
        if (t.align === 0) ctx.textAlign = "left";
        else if (t.align === 2) ctx.textAlign = "right";
        else ctx.textAlign = "center";

        ctx.textBaseline = "middle";

        // Multi-line rendering with line-spacing
        const lines = t.text.split("\\n");
        const lineHeight = pxSize * (1.25 + (t.line_spacing || 0));
        const totalBlockHeight = lines.length * lineHeight;
        let startY = -totalBlockHeight / 2 + lineHeight / 2;

        for (let i = 0; i < lines.length; i++) {
          const lineY = startY + i * lineHeight;
          
          // Subtle drop shadow for premium legibility
          ctx.shadowColor = "rgba(0, 0, 0, 0.8)";
          ctx.shadowBlur = 12;
          ctx.shadowOffsetX = 0;
          ctx.shadowOffsetY = 4;

          // Fill Text
          ctx.fillStyle = t.color || "#FFFFFF";
          ctx.fillText(lines[i], 0, lineY);
        }

        // Highlight if selected in inspector
        if (selectedSegment && selectedSegment.material_id === t.material_id) {
          ctx.strokeStyle = "#00e5ff";
          ctx.lineWidth = 4;
          ctx.strokeRect(-W * 0.42, -totalBlockHeight / 2 - 16, W * 0.84, totalBlockHeight + 32);
        }

        ctx.restore();
      }
    }

    function renderTimelineLanes() {
      if (!draftData) return;
      const dur = draftData.total_duration_sec;

      // Video Lane
      const vLane = document.getElementById("laneVideo");
      vLane.innerHTML = "";
      draftData.video_clips.forEach(c => {
        const left = (c.start_sec / dur) * 100;
        const width = (c.duration_sec / dur) * 100;
        const el = document.createElement("div");
        el.className = "segment-block block-video";
        el.style.left = left + "%";
        el.style.width = width + "%";
        el.textContent = `🎬 ${c.name}`;
        el.onclick = () => { seek(c.start_sec); };
        vLane.appendChild(el);
      });

      // Text Lane
      const tLane = document.getElementById("laneText");
      tLane.innerHTML = "";
      draftData.text_clips.forEach(t => {
        const left = (t.start_sec / dur) * 100;
        const width = (t.duration_sec / dur) * 100;
        const el = document.createElement("div");
        el.className = "segment-block block-text";
        el.style.left = left + "%";
        el.style.width = width + "%";
        el.textContent = `✍️ ${t.text.replace(/\\n/g, ' ')}`;
        el.onclick = (e) => {
          e.stopPropagation();
          selectTextSegment(t);
          seek(t.start_sec);
        };
        tLane.appendChild(el);
      });

      // Audio Lane
      const aLane = document.getElementById("laneAudio");
      aLane.innerHTML = "";
      draftData.audio_clips.forEach(a => {
        const left = (a.start_sec / dur) * 100;
        const width = (a.duration_sec / dur) * 100;
        const el = document.createElement("div");
        el.className = "segment-block block-audio";
        el.style.left = left + "%";
        el.style.width = width + "%";
        el.textContent = `🎵 ${a.name}`;
        el.onclick = () => { seek(a.start_sec); };
        aLane.appendChild(el);
      });
    }

    function selectTextSegment(seg) {
      selectedSegment = seg;
      document.getElementById("selectedBadge").textContent = `ID: ${seg.material_id.substring(0, 8)}...`;
      
      const insp = document.getElementById("inspectorContent");
      insp.innerHTML = `
        <div class="card">
          <div class="card-title">
            <span>Edit Text Layer</span>
            <span style="color: var(--accent-cyan); font-size: 11px;">${seg.start_sec}s - ${seg.end_sec}s</span>
          </div>
          <div class="form-group">
            <label>Text Content (supports newlines)</label>
            <textarea id="inpText" class="form-control" rows="3">${seg.text}</textarea>
          </div>
          <div class="form-group">
            <label>Font Size</label>
            <div class="range-row">
              <input type="range" id="inpFontSize" class="form-control" min="8" max="40" step="0.5" value="${seg.font_size}" oninput="document.getElementById('lblSize').textContent = this.value">
              <span class="range-val" id="lblSize">${seg.font_size}</span>
            </div>
          </div>
          <div class="form-group">
            <label>Color</label>
            <input type="color" id="inpColor" class="form-control" value="${seg.color}" style="height: 36px; padding: 2px;">
          </div>
          <div class="form-group">
            <label>Vertical Position Y (-1.0 to 1.0)</label>
            <div class="range-row">
              <input type="range" id="inpPosY" class="form-control" min="-1" max="1" step="0.02" value="${seg.transform.y}" oninput="document.getElementById('lblPosY').textContent = this.value">
              <span class="range-val" id="lblPosY">${seg.transform.y}</span>
            </div>
          </div>
          <div class="form-group">
            <label>Horizontal Position X (-1.0 to 1.0)</label>
            <div class="range-row">
              <input type="range" id="inpPosX" class="form-control" min="-1" max="1" step="0.02" value="${seg.transform.x}" oninput="document.getElementById('lblPosX').textContent = this.value">
              <span class="range-val" id="lblPosX">${seg.transform.x}</span>
            </div>
          </div>
          <button class="btn btn-primary" style="width: 100%; justify-content: center; margin-top: 8px;" onclick="applyTextUpdate()">
            Apply Live Changes
          </button>
        </div>
      `;
    }

    async function applyTextUpdate() {
      if (!selectedSegment || !draftData) return;
      const text = document.getElementById("inpText").value;
      const fontSize = parseFloat(document.getElementById("inpFontSize").value);
      const color = document.getElementById("inpColor").value;
      const transformY = parseFloat(document.getElementById("inpPosY").value);
      const transformX = parseFloat(document.getElementById("inpPosX").value);

      // Optimistic local update
      selectedSegment.text = text;
      selectedSegment.font_size = fontSize;
      selectedSegment.color = color;
      selectedSegment.transform.y = transformY;
      selectedSegment.transform.x = transformX;

      try {
        const res = await fetch("/api/preview_update", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            project_name: draftData.project_name,
            material_id: selectedSegment.material_id,
            text: text,
            font_size: fontSize,
            color: color,
            transform_x: transformX,
            transform_y: transformY
          })
        });
        const json = await res.json();
        if (json.success) {
          await loadProject(draftData.project_name, true);
        }
      } catch (err) {
        console.error("Failed to apply update:", err);
      }
    }

    async function triggerDesktopReload() {
      try {
        const res = await fetch("/api/trigger_desktop_reload", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ project_name: draftData?.project_name })
        });
        const json = await res.json();
        alert(json.message || "Desktop reload triggered!");
      } catch (err) {
        alert("Desktop reload failed: " + err);
      }
    }

    async function deployToCapCut() {
      if (!draftData) return;
      try {
        const res = await fetch("/save_draft", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            draft_id: draftData.project_name,
            project_name: draftData.project_name,
            auto_deploy: true,
            auto_reload: true
          })
        });
        const json = await res.json();
        alert(json.success ? "Draft successfully saved & auto-reloaded in CapCut Desktop!" : "Failed to save draft.");
      } catch (err) {
        alert("Deploy failed: " + err);
      }
    }

    // Keyboard Shortcuts
    window.addEventListener("keydown", (e) => {
      if (e.target.tagName === "INPUT" || e.target.tagName === "TEXTAREA") return;
      if (e.code === "Space") {
        e.preventDefault();
        togglePlay();
      } else if (e.code === "ArrowLeft") {
        stepFrame(-1);
      } else if (e.code === "ArrowRight") {
        stepFrame(1);
      }
    });

    // Start on load
    window.onload = init;
  </script>
</body>
</html>
"""


@preview_bp.route("/preview")
def preview_page():
    """Serves the full web live preview application."""
    return render_template_string(HTML_PREVIEW_TEMPLATE)
