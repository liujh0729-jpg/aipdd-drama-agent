# SPDX-License-Identifier: Elastic-2.0
# Copyright (c) 2026 ClaymoreLab
"""[aipdd fork delta] 科普讲解视频的 HyperFrames 帧生成层（引擎即服务）。

策划层（``explainer.py``）算好的 ``ExplainerTimeline`` 在这里翻成一个
**自包含的 HyperFrames 项目目录**（``index.html`` + ``assets/gsap.min.js``），
交给 ``renderer.client.render_project`` 无头渲染成 MP4；音频按 Spike B 口径
「渲染静音画面 + api 端 ffmpeg 混网关 TTS/BGM」在本地拼接，渲染与配音解耦。

口径：
- 只产出**受限 HTML/CSS 版式 + GSAP 时间轴**，映射既有 8 图元 / 5 动效，不引入
  外部模板拉取、运行期零外联（GSAP 随项目 assets 落盘）。
- 帧号 / 时长 / 字幕块全部来自 ``timeline``（确定性），模型不参与对位。
- composition 契约取自 HyperFrames producer 加载器：根元素带
  ``data-composition-id`` / ``data-start`` / ``data-duration`` / ``data-width`` /
  ``data-height``，页面把一条 paused 的 ``gsap.timeline()`` 注册到
  ``window.__timelines[id]``，producer 按帧 seek 抓取。
- 画面由固定 builder JS 依 ``#explainer-spec`` 的 JSON 生成（Python 只发数据，
  产物可读、可单测）。
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .explainer import (
    ENTRY_FRAMES,
    EXIT_FRAMES,
    ExplainerTimeline,
)

# 引擎开关：默认走 HyperFrames，异常可回退 Pillow（保留原逐帧绘制链路）。
ENGINE_ENV = "DRAMACLAW_EXPLAINER_ENGINE"
ENGINE_HYPERFRAMES = "hyperframes"
ENGINE_PILLOW = "pillow"
DEFAULT_ENGINE = ENGINE_HYPERFRAMES

# 帧生成层的版式画布（渲染像素尺寸由调用方按画幅传入，这里是 HTML 逻辑尺寸基准）。
ASSETS_DIR = Path(__file__).resolve().parent / "assets"
_GSAP_ASSET = ASSETS_DIR / "gsap.min.js"

# 内置设计预设：CSS 变量集，套到根 composition 上；取自 HyperFrames frame-presets
# 的几套常用配色，保持「黑底 MG」为默认（与原 Pillow 视觉一致）。
STYLE_PRESETS: dict[str, dict[str, str]] = {
    "ink": {
        "bg": "#0b0d12",
        "fg": "#f5f7fa",
        "muted": "#9aa4b2",
        "accent": "#ffb703",
        "accent2": "#4cc9f0",
        "card": "rgba(255,255,255,0.06)",
        "line": "rgba(255,255,255,0.14)",
        "font": "'Noto Sans CJK SC', 'PingFang SC', 'Microsoft YaHei', system-ui, sans-serif",
        "bgfx": "dots",
    },
    "ocean": {
        "bg": "#041526",
        "fg": "#eaf6ff",
        "muted": "#7fa6c4",
        "accent": "#4cc9f0",
        "accent2": "#80ffdb",
        "card": "rgba(76,201,240,0.10)",
        "line": "rgba(128,255,219,0.18)",
        "font": "'Noto Sans CJK SC', 'PingFang SC', 'Microsoft YaHei', system-ui, sans-serif",
        "bgfx": "grid",
    },
    "sunset": {
        "bg": "#160a12",
        "fg": "#fff2e8",
        "muted": "#c99a8f",
        "accent": "#ff6b35",
        "accent2": "#ffb703",
        "card": "rgba(255,107,53,0.10)",
        "line": "rgba(255,183,3,0.20)",
        "font": "'Noto Sans CJK SC', 'PingFang SC', 'Microsoft YaHei', system-ui, sans-serif",
        "bgfx": "dots",
    },
    "paper": {
        "bg": "#f7f4ec",
        "fg": "#1a1a1a",
        "muted": "#6b6b6b",
        "accent": "#c1121f",
        "accent2": "#005f73",
        "card": "rgba(0,0,0,0.05)",
        "line": "rgba(0,0,0,0.14)",
        "font": "'Noto Sans CJK SC', 'PingFang SC', 'Microsoft YaHei', system-ui, sans-serif",
        "bgfx": "plain",
    },
    "mono": {
        "bg": "#000000",
        "fg": "#ffffff",
        "muted": "#8c8c8c",
        "accent": "#d4d4d4",
        "accent2": "#737373",
        "card": "rgba(255,255,255,0.06)",
        "line": "rgba(255,255,255,0.18)",
        "font": "'Noto Sans CJK SC', 'PingFang SC', 'Microsoft YaHei', system-ui, sans-serif",
        "bgfx": "grid",
    },
}
DEFAULT_STYLE_PRESET = "ink"

# 画幅预设 → 逻辑像素尺寸（与 renderer.client 的 outputResolution 名一致）。
CANVAS_FORMATS: dict[str, tuple[int, int]] = {
    "landscape": (1920, 1080),
    "portrait": (1080, 1920),
    "square": (1080, 1080),
}

KNOWN_PRIMITIVES = frozenset(
    {"term", "flow", "compare", "bars", "bignumber", "list", "cycle", "timeline"}
)
KNOWN_MOTIONS = frozenset({"softin", "typewriter", "lightup", "travel", "countup"})


def explainer_engine() -> str:
    """当前讲解视频渲染引擎（pillow|hyperframes），默认 hyperframes。"""

    value = (os.environ.get(ENGINE_ENV) or "").strip().lower()
    if value == ENGINE_PILLOW:
        return ENGINE_PILLOW
    if value == ENGINE_HYPERFRAMES:
        return ENGINE_HYPERFRAMES
    return DEFAULT_ENGINE


def normalize_style_preset(value: object) -> str:
    preset = str(value or "").strip().lower()
    return preset if preset in STYLE_PRESETS else DEFAULT_STYLE_PRESET


def normalize_canvas_format(value: object) -> str | None:
    fmt = str(value or "").strip().lower()
    return fmt if fmt in CANVAS_FORMATS else None


@dataclass(frozen=True, slots=True)
class ExplainerProject:
    """产出的 HyperFrames 项目目录（渲染服务按 projectDir 读取）。"""

    project_dir: Path
    entry_file: str
    width: int
    height: int
    fps: int
    duration_seconds: float
    style_preset: str
    canvas_format: str | None
    scene_count: int


def _seconds(frame: int, fps: int) -> float:
    return round(frame / float(fps or 1), 3)


def _scene_numbers(numbers: tuple[str, ...]) -> list[float]:
    """把 shot.numbers 里的字符串解析成可用于柱状/大数字的数值（非数即忽略）。"""

    out: list[float] = []
    for raw in numbers:
        token = str(raw).strip().replace(",", "").replace("%", "")
        try:
            out.append(float(token))
        except ValueError:
            # 保留 0 让版式对齐，但不参与数值动画。
            out.append(0.0)
    return out


def _scene_to_spec(scene: Any, fps: int) -> dict[str, Any]:
    shot = scene.shot
    start = _seconds(scene.start_frame, fps)
    dur = _seconds(max(1, scene.duration_frames), fps)
    entry = _seconds(min(ENTRY_FRAMES, max(1, scene.duration_frames // 3)), fps)
    exit_ = _seconds(min(EXIT_FRAMES, max(1, scene.duration_frames // 6)), fps)
    motion = shot.motion if shot.motion in KNOWN_MOTIONS else "softin"
    primitive = shot.primitive if shot.primitive in KNOWN_PRIMITIVES else "term"
    numbers = _scene_numbers(tuple(shot.numbers))
    return {
        "id": f"scene-{scene.start_frame}",
        "primitive": primitive,
        "motion": motion,
        "chapterIndex": int(shot.chapter_index),
        "headline": str(shot.headline or "").strip(),
        "sentences": [str(s).strip() for s in shot.sentences if str(s).strip()],
        "items": [str(i).strip() for i in shot.items if str(i).strip()],
        "numbers": [str(n).strip() for n in shot.numbers if str(n).strip()],
        "values": numbers,
        "emphasis": int(shot.emphasis),
        "start": start,
        "duration": dur,
        "entry": entry,
        "exit": exit_,
        "cues": [
            {
                "text": str(cue.text).strip(),
                "start": _seconds(cue.start_frame, fps),
                "duration": _seconds(max(1, cue.end_frame - cue.start_frame), fps),
            }
            for cue in scene.cues
            if str(cue.text).strip()
        ],
    }


def build_spec(
    timeline: ExplainerTimeline,
    *,
    width: int,
    height: int,
    style_preset: str,
    captions: bool,
    canvas_format: str | None,
) -> dict[str, Any]:
    """ExplainerTimeline → 渲染项目 spec（纯数据，可单测）。"""

    fps = max(1, int(timeline.fps or 30))
    total_seconds = _seconds(max(fps, timeline.total_frames), fps)
    title = timeline.title
    scenes = [_scene_to_spec(scene, fps) for scene in timeline.scenes]
    # 全局字幕块（跨镜头）：按镜头顺序展平，便于 builder 单带渲染。
    captions_flat: list[dict[str, Any]] = []
    for scene in scenes:
        captions_flat.extend(scene["cues"])
    chapter_names = list(timeline.chapter_names or ())
    return {
        "meta": {
            "width": int(width),
            "height": int(height),
            "fps": fps,
            "duration": total_seconds,
            "preset": style_preset,
            "captions": bool(captions),
            "canvasFormat": canvas_format,
            "bgfx": STYLE_PRESETS[style_preset]["bgfx"],
        },
        "title": {
            "big": str(title.big or "").strip(),
            "rest": str(title.rest or "").strip(),
            "en": str(title.en or "").strip(),
            "tagline": str(title.tagline or "").strip(),
            "duration": round(min(2.6, total_seconds), 3),
        },
        "chapters": chapter_names,
        "scenes": scenes,
        "captionBlocks": captions_flat if captions else [],
    }


def _escape_script_json(payload: dict[str, Any]) -> str:
    """JSON 内联进 <script>：非 ASCII 保留中文，转义 '<' 避免提前闭合 script。"""

    raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    return raw.replace("</", "<\\/")


def _preset_css_vars(preset: str) -> str:
    p = STYLE_PRESETS[preset]
    return ";".join(
        [
            f"--bg:{p['bg']}",
            f"--fg:{p['fg']}",
            f"--muted:{p['muted']}",
            f"--accent:{p['accent']}",
            f"--accent2:{p['accent2']}",
            f"--card:{p['card']}",
            f"--line:{p['line']}",
            f"--font:{p['font']}",
        ]
    )


def render_index_html(spec: dict[str, Any]) -> str:
    """把 spec 套进固定模板，产出自包含 index.html（GSAP 走 assets 相对路径）。"""

    meta = spec["meta"]
    width = int(meta["width"])
    height = int(meta["height"])
    duration = float(meta["duration"])
    vars_ = _preset_css_vars(meta["preset"])
    spec_json = _escape_script_json(spec)
    return (
        "<!doctype html>\n"
        '<html lang="zh-CN">\n'
        "  <head>\n"
        '    <meta charset="utf-8" />\n'
        f'    <meta name="viewport" content="width={width}, height={height}" />\n'
        "    <title>Explainer</title>\n"
        '    <script src="assets/gsap.min.js"></script>\n'
        f"    <style>{_CSS}</style>\n"
        "  </head>\n"
        "  <body>\n"
        '    <div id="comp" data-composition-id="main" data-start="0" '
        f'data-duration="{duration}" data-width="{width}" data-height="{height}" '
        f'style="{vars_}">\n'
        '      <div id="bg" aria-hidden="true"></div>\n'
        '      <div id="hud" aria-hidden="true"><span id="hud-chapter"></span>'
        '<span id="hud-total"></span></div>\n'
        '      <div id="stage" aria-hidden="true"></div>\n'
        '      <div id="captions" aria-hidden="true"></div>\n'
        '      <div id="progress" aria-hidden="true"><i id="progress-fill"></i></div>\n'
        "    </div>\n"
        f'    <script id="explainer-spec" type="application/json">{spec_json}</script>\n'
        f"    <script>{_BUILDER_JS}</script>\n"
        "  </body>\n"
        "</html>\n"
    )


def build_explainer_project(
    timeline: ExplainerTimeline,
    *,
    project_dir: Path | str,
    width: int,
    height: int,
    style_preset: object = DEFAULT_STYLE_PRESET,
    captions: bool = True,
    canvas_format: object = None,
) -> ExplainerProject:
    """产出 HyperFrames 项目目录：``index.html`` + ``assets/gsap.min.js``。

    ``project_dir`` 必须是渲染服务能读到的绝对路径（共享卷 / 宿主 FS）。返回
    一个 :class:`ExplainerProject`，调用方拿 ``project_dir`` 交给
    ``renderer.client.render_project``。
    """

    if timeline.total_frames <= 0:
        raise ValueError("explainer timeline is empty")
    preset = normalize_style_preset(style_preset)
    fmt = normalize_canvas_format(canvas_format)
    fps = max(1, int(timeline.fps or 30))
    width = max(2, int(width))
    height = max(2, int(height))

    root = Path(project_dir)
    root.mkdir(parents=True, exist_ok=True)
    assets = root / "assets"
    assets.mkdir(parents=True, exist_ok=True)

    spec = build_spec(
        timeline,
        width=width,
        height=height,
        style_preset=preset,
        captions=captions,
        canvas_format=fmt,
    )
    (root / "index.html").write_text(render_index_html(spec), encoding="utf-8")
    shutil.copyfile(_GSAP_ASSET, assets / "gsap.min.js")

    return ExplainerProject(
        project_dir=root,
        entry_file="index.html",
        width=width,
        height=height,
        fps=fps,
        duration_seconds=_seconds(max(fps, timeline.total_frames), fps),
        style_preset=preset,
        canvas_format=fmt,
        scene_count=len(timeline.scenes),
    )


def mux_audio(
    silent_video: Path | str,
    *,
    out_path: Path | str,
    narration_path: str | Path | None = None,
    bgm_path: str | Path | None = None,
    bgm_volume: float = 0.2,
    duration_seconds: float | None = None,
) -> Path:
    """把静音画面与配音 / BGM 混流成片（Spike B：渲染与配音解耦）。

    画面已由渲染服务编好，这里只做容器级复用（``-c:v copy``）+ 音频 amix，不再
    重编码视频，快且不损画质。BGM 无限循环裁到片长。
    """

    src = Path(silent_video)
    if not src.exists():
        raise FileNotFoundError(f"silent video not found: {src}")
    target = Path(out_path)
    target.parent.mkdir(parents=True, exist_ok=True)

    command: list[str] = ["ffmpeg", "-y", "-i", str(src)]
    stream = 1
    labels: list[str] = []
    filters: list[str] = []
    if narration_path:
        command += ["-i", str(narration_path)]
        filters.append(
            f"[{stream}:a]aformat=sample_rates=44100:channel_layouts=stereo,"
            "asetpts=PTS-STARTPTS[va]"
        )
        labels.append("[va]")
        stream += 1
    if bgm_path:
        command += ["-stream_loop", "-1", "-i", str(bgm_path)]
        volume = max(0.02, min(0.9, float(bgm_volume)))
        filters.append(
            f"[{stream}:a]aformat=sample_rates=44100:channel_layouts=stereo,"
            f"asetpts=PTS-STARTPTS,volume={volume:.3f}[vb]"
        )
        labels.append("[vb]")
        stream += 1

    command += ["-map", "0:v:0", "-c:v", "copy"]
    if len(labels) == 2:
        graph = ";".join(filters) + f";{labels[0]}{labels[1]}amix=inputs=2:duration=first:normalize=0[aout]"
        command += ["-filter_complex", graph, "-map", "[aout]", "-c:a", "aac", "-b:a", "160k"]
    elif labels:
        graph = ";".join(filters) + f";{labels[0]}anull[aout]"
        command += ["-filter_complex", graph, "-map", "[aout]", "-c:a", "aac", "-b:a", "160k"]
    else:
        command += ["-an"]
    command += ["-movflags", "+faststart"]
    if duration_seconds and duration_seconds > 0:
        command += ["-t", f"{float(duration_seconds) + 0.04:.3f}"]
    command += [str(target)]

    proc = subprocess.run(command, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(
            f"ffmpeg 混音失败（{proc.returncode}）：{proc.stderr[-400:]}"
        )
    return target


# --- 模板：静态 CSS + builder JS -------------------------------------------
# CSS 变量套在 #comp 上；版式全部用变量着色，一份 CSS 通吃所有预设。
_CSS = r"""
*{box-sizing:border-box;margin:0;padding:0}
html,body{background:#000;color:#fff;font-family:var(--font)}
#comp{position:relative;width:100%;height:100%;overflow:hidden;background:var(--bg);color:var(--fg)}
body{margin:0;width:100vw;height:100vh}
#bg{position:absolute;inset:0;opacity:.5}
#bg[data-fx="dots"]{background-image:radial-gradient(var(--line) 1.4px,transparent 1.4px);background-size:34px 34px}
#bg[data-fx="grid"]{background-image:linear-gradient(var(--line) 1px,transparent 1px),linear-gradient(90deg,var(--line) 1px,transparent 1px);background-size:64px 64px}
#bg[data-fx="plain"]{background:transparent}
#hud{position:absolute;top:3%;left:4%;right:4%;display:flex;justify-content:space-between;font-size:2.2vh;color:var(--muted);letter-spacing:.06em;z-index:5}
#hud-chapter{background:var(--card);padding:.5em 1em;border-radius:999px;border:1px solid var(--line);color:var(--accent)}
#stage{position:absolute;inset:0;display:flex;align-items:center;justify-content:center;z-index:2}
.scene{position:absolute;inset:0;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:3vh;padding:9vh 7vw;visibility:hidden;opacity:0;text-align:center}
.scene .headline{font-size:6.4vh;font-weight:800;line-height:1.15;color:var(--fg);max-width:84vw}
.scene .body{font-size:3.4vh;line-height:1.5;color:var(--muted);max-width:80vw}
.scene .accent{color:var(--accent)}
#title{position:absolute;inset:0;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:2.2vh;text-align:center;z-index:6;visibility:hidden;opacity:0;padding:8vh 8vw}
#title .big{font-size:9vh;font-weight:900;color:var(--accent);letter-spacing:.04em}
#title .rest{font-size:5vh;font-weight:800;color:var(--fg)}
#title .en{font-size:2.8vh;color:var(--muted);letter-spacing:.28em;text-transform:uppercase}
#title .tagline{font-size:3vh;color:var(--fg);opacity:.8;max-width:78vw}
/* 图元版式 */
.flow{display:flex;align-items:stretch;gap:2vw;flex-wrap:wrap;justify-content:center}
.flow .step{background:var(--card);border:1px solid var(--line);border-radius:1.4vh;padding:2.4vh 2vw;font-size:3vh;min-width:16vw}
.flow .arrow{align-self:center;color:var(--accent);font-size:4vh}
.compare{display:flex;gap:3vw;align-items:stretch;justify-content:center}
.compare .col{background:var(--card);border:1px solid var(--line);border-radius:1.6vh;padding:3vh 3vw;font-size:3.2vh;min-width:30vw}
.compare .col:first-child{border-left:.6vh solid var(--accent)}
.compare .col:last-child{border-left:.6vh solid var(--accent2)}
.bars{display:flex;align-items:flex-end;gap:2.4vw;height:44vh}
.bars .bar{display:flex;flex-direction:column;align-items:center;gap:1vh}
.bars .bar .fill{width:9vw;background:linear-gradient(180deg,var(--accent),var(--accent2));border-radius:1vh 1vh 0 0;transform-origin:bottom;height:0}
.bars .bar .val{font-size:2.8vh;color:var(--fg)}
.bars .bar .lab{font-size:2.2vh;color:var(--muted)}
.bignum{display:flex;flex-direction:column;align-items:center;gap:1.5vh}
.bignum .num{font-size:20vh;font-weight:900;color:var(--accent);line-height:1}
.bignum .lab{font-size:3.4vh;color:var(--muted)}
.list{display:flex;flex-direction:column;gap:2.2vh;align-items:flex-start;max-width:80vw}
.list .li{display:flex;gap:1.4vw;align-items:baseline;font-size:3.6vh;color:var(--fg)}
.list .li .dot{color:var(--accent);font-size:3.6vh}
.cycle{display:flex;flex-wrap:wrap;gap:2vw;justify-content:center;max-width:80vw}
.cycle .node{background:var(--card);border:1px solid var(--accent);border-radius:999px;padding:2vh 2.4vw;font-size:3vh;color:var(--fg)}
.cycle .sep{align-self:center;color:var(--accent2);font-size:3.4vh}
.timeline{display:flex;align-items:center;gap:0;width:86vw;justify-content:space-between;position:relative}
.timeline:before{content:"";position:absolute;left:0;right:0;top:50%;height:.5vh;background:var(--line)}
.timeline .tick{position:relative;display:flex;flex-direction:column;align-items:center;gap:1.2vh}
.timeline .tick .pt{width:2.4vh;height:2.4vh;border-radius:50%;background:var(--accent);border:.6vh solid var(--bg)}
.timeline .tick .lab{font-size:2.4vh;color:var(--muted)}
#captions{position:absolute;left:6%;right:6%;bottom:10%;text-align:center;z-index:4;font-size:4.2vh;line-height:1.4;color:var(--fg);min-height:6vh}
#captions .cue{position:absolute;left:0;right:0;bottom:0;visibility:hidden;opacity:0;text-shadow:0 2px 10px rgba(0,0,0,.6)}
#progress{position:absolute;left:0;right:0;bottom:0;height:.7vh;background:var(--line);z-index:5}
#progress-fill{display:block;height:100%;width:100%;background:linear-gradient(90deg,var(--accent),var(--accent2));transform:scaleX(0);transform-origin:left center}
""".strip()

# builder JS：读 #explainer-spec → 建 DOM + GSAP 时间轴 → 注册 window.__timelines。
# 不用模板字符串/反引号，避免与 Python 字符串冲突；全是普通拼接。
_BUILDER_JS = r"""
(function(){
  "use strict";
  var root=document.getElementById("comp");
  var spec=JSON.parse(document.getElementById("explainer-spec").textContent);
  var meta=spec.meta;
  document.getElementById("bg").setAttribute("data-fx",meta.bgfx||"dots");
  var stage=document.getElementById("stage");
  var capWrap=document.getElementById("captions");
  var hudCh=document.getElementById("hud-chapter");
  var hudTotal=document.getElementById("hud-total");
  var fill=document.getElementById("progress-fill");
  var total=(spec.scenes.length);
  hudTotal.textContent=total?("/ "+total):"";
  function el(tag,cls,txt){var n=document.createElement(tag);if(cls)n.className=cls;if(txt!=null)n.textContent=txt;return n;}

  function buildScene(scene){
    var box=el("div","scene p-"+scene.primitive);
    box.id=scene.id;
    var head=scene.headline||scene.sentences[0]||"";
    if(scene.primitive==="term"){
      if(head)box.appendChild(el("div","headline",head));
      var def=scene.sentences.filter(function(s){return s!==head;}).join(" ");
      if(def)box.appendChild(el("div","body",def));
    }else if(scene.primitive==="flow"){
      var row=el("div","flow");var steps=scene.items.length?scene.items:(scene.sentences.length>1?scene.sentences:[head].concat(scene.sentences.slice(1)));
      steps.forEach(function(s,i){if(i)row.appendChild(el("div","arrow","→"));row.appendChild(el("div","step",s));});
      box.appendChild(el("div","headline",head));box.appendChild(row);
    }else if(scene.primitive==="compare"){
      var cols=scene.items.length>=2?scene.items.slice(0,2):(scene.sentences.length>=2?scene.sentences.slice(0,2):[head,scene.sentences[1]||""]);
      var cmp=el("div","compare");cols.forEach(function(c){cmp.appendChild(el("div","col",c));});
      box.appendChild(el("div","headline",head));box.appendChild(cmp);
    }else if(scene.primitive==="bars"){
      var bars=el("div","bars");var vals=scene.values&&scene.values.length?scene.values:[1];
      var maxv=Math.max.apply(null,vals.concat([1]));
      vals.forEach(function(v,i){var b=el("div","bar");var f=el("div","fill");f.setAttribute("data-h",Math.round(100*(v/maxv)));
        var num=el("div","val",(scene.numbers&&scene.numbers[i])||String(v));num.setAttribute("data-count",v);
        b.appendChild(f);b.appendChild(num);if(scene.items&&scene.items[i])b.appendChild(el("div","lab",scene.items[i]));bars.appendChild(b);});
      if(head)box.appendChild(el("div","headline",head));box.appendChild(bars);
    }else if(scene.primitive==="bignumber"){
      var bn=el("div","bignum");var raw=(scene.numbers&&scene.numbers[0])||(scene.values[0]!=null?scene.values[0]:"");
      var n=el("div","num",String(raw));var mv=(scene.values&&scene.values[0])||0;n.setAttribute("data-count",mv);
      bn.appendChild(n);if(head)bn.appendChild(el("div","lab",head));box.appendChild(bn);
    }else if(scene.primitive==="list"){
      var lst=el("div","list");var its=scene.items.length?scene.items:scene.sentences;
      its.forEach(function(t){var li=el("div","li");li.appendChild(el("span","dot","•"));li.appendChild(el("span",null,t));lst.appendChild(li);});
      if(head)box.appendChild(el("div","headline",head));box.appendChild(lst);
    }else if(scene.primitive==="cycle"){
      var cyc=el("div","cycle");var nodes=scene.items.length?scene.items:scene.sentences;
      nodes.forEach(function(t,i){if(i)cyc.appendChild(el("span","sep","↻"));cyc.appendChild(el("div","node",t));});
      if(head)box.appendChild(el("div","headline",head));box.appendChild(cyc);
    }else if(scene.primitive==="timeline"){
      var tl=el("div","timeline");var ticks=scene.items.length?scene.items:scene.sentences;
      ticks.forEach(function(t){var k=el("div","tick");k.appendChild(el("div","pt"));k.appendChild(el("div","lab",t));tl.appendChild(k);});
      if(head)box.appendChild(el("div","headline",head));box.appendChild(tl);
    }else{
      if(head)box.appendChild(el("div","headline",head));
      box.appendChild(el("div","body",scene.sentences.join(" ")));
    }
    stage.appendChild(box);
    return box;
  }

  var sceneEls=spec.scenes.map(buildScene);

  var capEls=(spec.captionBlocks||[]).map(function(c){var n=el("div","cue",c.text);capWrap.appendChild(n);return {el:n,start:c.start,dur:c.duration};});

  // 片头卡
  var titleEl=null;
  if(spec.title&&(spec.title.big||spec.title.rest)){
    titleEl=el("div",null);titleEl.id="title";
    if(spec.title.big)titleEl.appendChild(el("div","big",spec.title.big));
    if(spec.title.rest)titleEl.appendChild(el("div","rest",spec.title.rest));
    if(spec.title.en)titleEl.appendChild(el("div","en",spec.title.en));
    if(spec.title.tagline)titleEl.appendChild(el("div","tagline",spec.title.tagline));
    stage.appendChild(titleEl);
  }

  var tl=gsap.timeline({paused:true});
  tl.set(root,{opacity:1},0);
  if(titleEl){
    tl.set(titleEl,{visibility:"visible",autoAlpha:1},0);
    tl.fromTo(titleEl,{autoAlpha:0,scale:0.96},{autoAlpha:1,scale:1,duration:0.6,ease:"power2.out"},0);
    tl.to(titleEl,{autoAlpha:0,duration:0.5,ease:"power1.in"},spec.title.duration);
  }
  // 底部进度条随全片线性推进
  tl.fromTo(fill,{scaleX:0},{scaleX:1,duration:meta.duration,ease:"none"},0);

  spec.scenes.forEach(function(s,i){
    var node=sceneEls[i];
    var end=s.start+s.duration;
    // HUD 章节标签：镜头开始即切到该章名
    if(s.chapterIndex>=0 && spec.chapters && spec.chapters[s.chapterIndex]){
      tl.set(hudCh,{textContent:(s.chapterIndex+1)+". "+spec.chapters[s.chapterIndex]},s.start);
    }
    tl.set(node,{visibility:"visible"},s.start);
    tl.set(node,{autoAlpha:1},s.start);
    tl.set(node,{autoAlpha:0},end);
    tl.set(node,{visibility:"hidden"},end);
    var kids=node.querySelectorAll(".headline,.body,.flow,.compare,.bars,.bignum,.list,.cycle,.timeline");
    // 入场动效
    if(s.motion==="travel"){
      tl.fromTo(node,{x:-48,autoAlpha:0},{x:0,autoAlpha:1,duration:s.entry,ease:"power2.out"},s.start);
    }else if(s.motion==="lightup"){
      tl.fromTo(node,{autoAlpha:0},{autoAlpha:1,duration:s.entry,ease:"power1.out"},s.start);
      if(kids[0])tl.fromTo(kids[0],{textShadow:"0 0 0 rgba(0,0,0,0)"},{textShadow:"0 0 24px var(--accent)",duration:s.entry,yoyo:true,repeat:1},s.start);
    }else if(s.motion==="typewriter"){
      tl.set(node,{autoAlpha:1},s.start);
      if(kids[0])tl.fromTo(kids[0],{clipPath:"inset(0 100% 0 0)"},{clipPath:"inset(0 0% 0 0)",duration:Math.max(0.6,s.entry),ease:"steps(24)"},s.start);
    }else if(s.motion==="countup"){
      tl.fromTo(node,{autoAlpha:0,y:16},{autoAlpha:1,y:0,duration:s.entry,ease:"power2.out"},s.start);
      var counters=node.querySelectorAll("[data-count]");
      Array.prototype.forEach.call(counters,function(c){var to=parseFloat(c.getAttribute("data-count"))||0;var o={v:0};
        tl.to(o,{v:to,duration:Math.max(0.8,s.duration-0.4),ease:"power1.out",onUpdate:function(){c.textContent=fmtNum(o,o.v,c);}},s.start);});
    }else{ // softin
      tl.fromTo(node,{autoAlpha:0,y:24},{autoAlpha:1,y:0,duration:s.entry,ease:"power2.out"},s.start);
    }
    // 柱状生长（bars / bignumber 的填充高度）
    var fills=node.querySelectorAll(".fill[data-h]");
    Array.prototype.forEach.call(fills,function(f){var h=parseFloat(f.getAttribute("data-h"))||0;
      tl.fromTo(f,{height:"0%"},{height:h+"%",duration:Math.max(0.6,s.duration-0.6),ease:"power2.out"},s.start+s.entry);});
    // 子元素错峰入场（list / flow / cycle / timeline / compare）
    if(s.motion!=="countup"){
      var items=node.querySelectorAll(".li,.step,.node,.tick,.col");
      if(items.length>1){
        tl.set(items,{autoAlpha:0,y:14},s.start);
        Array.prototype.forEach.call(items,function(it,k){tl.to(it,{autoAlpha:1,y:0,duration:0.35,ease:"power2.out"},s.start+s.entry*0.5+k*0.22);});
      }
    }
    // 离场淡出
    if(s.exit>0){tl.to(node,{autoAlpha:0,duration:s.exit,ease:"power1.in"},end-s.exit);}
  });

  capEls.forEach(function(c){
    tl.set(c.el,{visibility:"visible",autoAlpha:1},c.start);
    tl.set(c.el,{autoAlpha:0,visibility:"hidden"},c.start+c.dur);
  });

  function fmtNum(o,cur,el){var raw=el.getAttribute("data-count");var target=parseFloat(raw)||0;
    if(target>=1000)return Math.round(cur).toLocaleString("en-US");
    if(target%1!==0)return cur.toFixed(1);
    return String(Math.round(cur));}
  function v(c){return c.v!=null?c.v:0;}

  tl.duration(meta.duration);
  tl.progress(0);
  window.__timelines=window.__timelines||{};
  window.__timelines["main"]=tl;
})();
""".strip()
