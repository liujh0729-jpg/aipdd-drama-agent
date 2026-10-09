// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
// [aipdd fork delta] 视频去水印：在视频预览上拖拽归一化矩形框选区域。
// 交互：空白处拖拽画新框；框内拖拽整体移动；右下角手柄缩放；误点（过小）回退。
// 视频下方自带播放控件（播放/暂停、进度、时间）便于定位水印；selectionEnabled=false
// 时退化为纯预览播放器（AI 视频编辑路径不需要框选）。
import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { Pause, Play } from "lucide-react";

import {
  MIN_BOX_SIZE,
  clamp01,
  rectFromCorners,
  resizeRectTo,
  shiftRect,
  type NormalizedBox,
} from "@/features/tools/core";

type Point = { x: number; y: number };

type Drag =
  | { mode: "new"; start: Point; origin: NormalizedBox }
  | { mode: "move"; start: Point; origin: NormalizedBox }
  | { mode: "resize"; start: Point; origin: NormalizedBox };

/** 播放时间显示 mm:ss；NaN / 负值兜底 0:00。 */
function formatSeconds(value: number): string {
  if (!Number.isFinite(value) || value < 0) return "0:00";
  const total = Math.floor(value);
  const minutes = Math.floor(total / 60);
  const seconds = total % 60;
  return `${minutes}:${String(seconds).padStart(2, "0")}`;
}

export function BoxRegionEditor({
  videoUrl,
  box,
  onBoxChange,
  selectionEnabled = true,
}: {
  videoUrl: string;
  box: NormalizedBox;
  onBoxChange: (next: NormalizedBox) => void;
  /** false = 只保留预览播放，不渲染框选层（AI 视频编辑路径不需要框选）。 */
  selectionEnabled?: boolean;
}) {
  const { t } = useTranslation();
  const layerRef = useRef<HTMLDivElement>(null);
  const dragRef = useRef<Drag | null>(null);
  const boxRef = useRef(box);
  boxRef.current = box;
  const videoRef = useRef<HTMLVideoElement>(null);
  const [playing, setPlaying] = useState(false);
  const [currentSeconds, setCurrentSeconds] = useState(0);
  const [durationSeconds, setDurationSeconds] = useState(0);

  // 换源复位播放状态，避免沿用上一个视频的进度。
  useEffect(() => {
    setPlaying(false);
    setCurrentSeconds(0);
    setDurationSeconds(0);
  }, [videoUrl]);

  const togglePlay = () => {
    const el = videoRef.current;
    if (!el) return;
    if (el.paused) {
      const started = el.play();
      // jsdom 等无媒体实现的环境 play() 可能返回 undefined，做防御式兜底。
      if (started && typeof started.catch === "function") started.catch(() => {});
    } else {
      el.pause();
    }
  };

  const seekTo = (value: number) => {
    const el = videoRef.current;
    if (!el || !Number.isFinite(value)) return;
    el.currentTime = value;
    setCurrentSeconds(value);
  };

  const toNorm = (clientX: number, clientY: number): Point => {
    const rect = layerRef.current?.getBoundingClientRect();
    if (!rect || rect.width === 0 || rect.height === 0) return { x: 0, y: 0 };
    return {
      x: clamp01((clientX - rect.left) / rect.width),
      y: clamp01((clientY - rect.top) / rect.height),
    };
  };

  const onPointerDown = (e: React.PointerEvent<HTMLDivElement>) => {
    if (e.button !== 0) return;
    e.currentTarget.setPointerCapture(e.pointerId);
    const start = toNorm(e.clientX, e.clientY);
    const role = (e.target as HTMLElement).dataset.role;
    if (role === "handle") {
      dragRef.current = { mode: "resize", start, origin: box };
    } else if (role === "box") {
      dragRef.current = { mode: "move", start, origin: box };
    } else {
      dragRef.current = { mode: "new", start, origin: box };
      onBoxChange({ x: start.x, y: start.y, width: 0, height: 0 });
    }
  };

  const onPointerMove = (e: React.PointerEvent<HTMLDivElement>) => {
    const drag = dragRef.current;
    if (!drag) return;
    const cur = toNorm(e.clientX, e.clientY);
    if (drag.mode === "new") {
      onBoxChange(rectFromCorners(drag.start, cur));
    } else if (drag.mode === "move") {
      onBoxChange(shiftRect(drag.origin, cur.x - drag.start.x, cur.y - drag.start.y));
    } else {
      onBoxChange(resizeRectTo(drag.origin, cur.x, cur.y));
    }
  };

  const endDrag = () => {
    const drag = dragRef.current;
    dragRef.current = null;
    // 只是点了一下、没画出有效大小时回退到原框，避免误点吞掉选择。
    if (drag?.mode === "new") {
      const current = boxRef.current;
      if (current.width < MIN_BOX_SIZE || current.height < MIN_BOX_SIZE) {
        onBoxChange(drag.origin);
      }
    }
  };

  const seekMax = durationSeconds > 0 ? durationSeconds : 0;

  return (
    <div className="mx-auto inline-block max-w-full space-y-2">
      <div className="relative max-w-full overflow-hidden rounded-lg border border-border bg-black">
        <video
          ref={videoRef}
          src={videoUrl}
          muted
          playsInline
          preload="metadata"
          draggable={false}
          className="block max-h-[320px] max-w-full"
          onPlay={() => setPlaying(true)}
          onPause={() => setPlaying(false)}
          onEnded={() => setPlaying(false)}
          onTimeUpdate={(e) => setCurrentSeconds(e.currentTarget.currentTime)}
          onLoadedMetadata={(e) => setDurationSeconds(e.currentTarget.duration || 0)}
        />
        {selectionEnabled ? (
          <>
            <div
              ref={layerRef}
              onPointerDown={onPointerDown}
              onPointerMove={onPointerMove}
              onPointerUp={endDrag}
              onPointerCancel={endDrag}
              className="absolute inset-0 touch-none cursor-crosshair select-none"
            >
              <div
                data-role="box"
                className="absolute cursor-move border border-primary bg-primary/20 shadow-[0_0_0_9999px_rgba(0,0,0,0.45)]"
                style={{
                  left: `${box.x * 100}%`,
                  top: `${box.y * 100}%`,
                  width: `${box.width * 100}%`,
                  height: `${box.height * 100}%`,
                }}
              >
                <div
                  data-role="handle"
                  className="absolute -bottom-1.5 -right-1.5 size-3 rounded-sm border border-primary bg-background"
                />
              </div>
            </div>
            <p className="pointer-events-none absolute bottom-2 left-2 rounded-md bg-black/60 px-2 py-1 text-[11px] text-white/80">
              {t("tools.videoWatermark.dragHint")}
            </p>
          </>
        ) : null}
      </div>
      <div className="flex items-center gap-2">
        <button
          type="button"
          onClick={togglePlay}
          aria-label={t(playing ? "tools.videoWatermark.pause" : "tools.videoWatermark.play")}
          className="flex size-7 shrink-0 items-center justify-center rounded-md border border-border text-muted-foreground transition-colors hover:text-foreground"
        >
          {playing ? <Pause className="size-3.5" /> : <Play className="size-3.5" />}
        </button>
        <input
          type="range"
          min={0}
          max={seekMax}
          step={0.05}
          value={Math.min(currentSeconds, seekMax)}
          onChange={(e) => seekTo(Number(e.target.value))}
          aria-label={t("tools.videoWatermark.seek")}
          className="h-1 flex-1 cursor-pointer accent-primary"
        />
        <span className="shrink-0 text-xs tabular-nums text-muted-foreground">
          {formatSeconds(currentSeconds)} / {formatSeconds(seekMax)}
        </span>
      </div>
    </div>
  );
}
