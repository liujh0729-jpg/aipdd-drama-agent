// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { activeClipAt, type ComposeTimelineState } from "./timelineModel";

/**
 * 封面选帧的纯逻辑工具（无 React）。把「时间线某 ms」解析成可截帧的源地址 + 源内
 * 时间，并把一个已 seek 到目标帧的 <video> 元素画成 JPEG Blob。截帧元素的装载 /
 * seek 留给调用方（封面编辑器持有一个可见的预览 <video>）。
 */

export interface CoverFrameSource {
  sourceUrl: string;
  /** 该时间线时刻对应的源媒体内时间（ms，已含 trim/变速换算）。 */
  sourceMs: number;
}

/**
 * 在时间线某 ms 处，取「最上层命中」的视频片段的源地址与源内时间（与预览舞台同款
 * 取轨规则：数组靠后的视频轨在上层）。落在空隙 / 无视频时返回 null。
 */
export function coverFrameSourceAt(
  timeline: ComposeTimelineState,
  ms: number,
): CoverFrameSource | null {
  const videos = timeline.tracks.filter((track) => track.kind === "video");
  for (let i = videos.length - 1; i >= 0; i -= 1) {
    const active = activeClipAt(videos[i], ms);
    if (active) {
      return { sourceUrl: active.laid.clip.sourceUrl, sourceMs: active.sourceMs };
    }
  }
  return null;
}

/** 时间线是否含任意可截帧的视频片段（用于禁用「选帧」tab）。 */
export function hasCoverableVideo(timeline: ComposeTimelineState): boolean {
  return timeline.tracks.some(
    (track) => track.kind === "video" && track.clips.length > 0,
  );
}

/**
 * 等待 <video> 元素的待决 seek 落定、当前帧可绘制（readyState ≥ HAVE_CURRENT_DATA）。
 * 截帧必须先等这个 —— 用户拖完选帧滑块立刻点确定时元素往往还在 seeking，直接
 * drawImage 画到的是上一个已解码帧而非所选帧。超时兜底返回（按当前帧截，不无限等）。
 */
export function waitForVideoFrameReady(
  video: HTMLVideoElement,
  timeoutMs = 3000,
): Promise<void> {
  if (!video.seeking && video.readyState >= 2) return Promise.resolve();
  return new Promise((resolve) => {
    let settled = false;
    const finish = () => {
      if (settled) return;
      settled = true;
      video.removeEventListener("seeked", check);
      video.removeEventListener("canplay", check);
      clearTimeout(timer);
      resolve();
    };
    const check = () => {
      if (!video.seeking && video.readyState >= 2) finish();
    };
    const timer = setTimeout(finish, timeoutMs);
    video.addEventListener("seeked", check);
    video.addEventListener("canplay", check);
  });
}

/**
 * 把一个已 seek 到目标帧的 <video> 元素画到离屏 canvas，导出 JPEG Blob。
 * 源经 Vite 代理 / Worker 反代后与页面同源，canvas 不会被污染。无尺寸 / 无 2d
 * 上下文 / 编码失败时返回 null。
 */
export function captureVideoFrame(
  video: HTMLVideoElement,
  quality = 0.9,
): Promise<Blob | null> {
  const w = video.videoWidth;
  const h = video.videoHeight;
  if (!w || !h) return Promise.resolve(null);
  const canvas = document.createElement("canvas");
  canvas.width = w;
  canvas.height = h;
  const ctx = canvas.getContext("2d");
  if (!ctx) return Promise.resolve(null);
  ctx.drawImage(video, 0, 0, w, h);
  return new Promise((resolve) =>
    canvas.toBlob((blob) => resolve(blob), "image/jpeg", quality),
  );
}
