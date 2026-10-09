// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { useCallback, useEffect, useRef, useState } from "react";

/**
 * 时间线预览的「主时钟」。
 *
 * 用 requestAnimationFrame 推进 `playheadMs`，与具体媒体元素解耦 —— 视频 / 音频
 * 元素由组件侧根据 {@link activeClipAt} 自行把 src + currentTime 对齐到这个播放头。
 * 时钟独立推进（不读 video.currentTime），所以跨片段切换时画面 src 交换造成的轻微
 * 抖动不会拖累整体进度，符合「预览非像素级精确、后端渲染才是最终结果」的取舍。
 */
export interface ComposePlayback {
  playheadMs: number;
  isPlaying: boolean;
  play: () => void;
  pause: () => void;
  toggle: () => void;
  seek: (ms: number) => void;
}

/**
 * React state 节流间隔（毫秒）。播放头的「竖线移动」走 onFrame 直接改 DOM，
 * 不受此限；这里节流的只是 React state（驱动时间读数 + 暂停态媒体镜像 + 整条
 * 时间轴重渲染）。60fps 全量重渲染那条又重又长的时间轴正是卡帧根因，降到 ~5/s
 * 即可，竖线本身仍然每帧丝滑。
 */
const STATE_THROTTLE_MS = 200;

export function useComposePlayback(
  durationMs: number,
  /** 每帧（及 seek/play）以最新播放头位置回调，供消费方命令式更新竖线 DOM。 */
  onFrame?: (ms: number) => void,
  /**
   * 播放态的「媒体主时钟」：返回当前正在播放的视频元素换算到时间线的位置（ms），
   * 取不到（无视频 / 缓冲 / 片段边界）时返回 null。提供它时，竖线跟随真实解码帧推进，
   * 与画面严丝合缝（libtv 效果）；返回 null 时回落到 performance.now 墙钟，保证连续。
   */
  clockMs?: () => number | null,
): ComposePlayback {
  const [playheadMs, setPlayheadMs] = useState(0);
  const [isPlaying, setIsPlaying] = useState(false);

  const playheadRef = useRef(0);
  const isPlayingRef = useRef(false);
  const startWallRef = useRef(0);
  const rafRef = useRef<number | null>(null);
  const durationRef = useRef(durationMs);
  durationRef.current = durationMs;
  const onFrameRef = useRef(onFrame);
  onFrameRef.current = onFrame;
  const clockMsRef = useRef(clockMs);
  clockMsRef.current = clockMs;
  const lastStateMsRef = useRef(0);

  // force=true：立即同步 React state（seek / play / pause / 到末尾）。
  // force=false：仅在与上次 state 值相差超过节流阈值时才 setState（rAF 推进用）。
  const setPlayhead = useCallback((ms: number, force = true) => {
    playheadRef.current = ms;
    // 竖线位置每帧命令式更新，绕开 React 重渲染。
    onFrameRef.current?.(ms);
    if (force || Math.abs(ms - lastStateMsRef.current) >= STATE_THROTTLE_MS) {
      lastStateMsRef.current = ms;
      setPlayheadMs(ms);
    }
  }, []);

  const stopRaf = useCallback(() => {
    if (rafRef.current != null) {
      cancelAnimationFrame(rafRef.current);
      rafRef.current = null;
    }
  }, []);

  const setPlaying = useCallback((value: boolean) => {
    isPlayingRef.current = value;
    setIsPlaying(value);
  }, []);

  const tick = useCallback(() => {
    const dur = durationRef.current;
    // 优先用媒体主时钟（竖线跟真实解码帧走）；取不到时回落墙钟，并把墙钟基准对齐
    // 到当前位置，保证下一帧若仍无媒体时钟也能从正确位置无缝接着推进。
    const fromMedia = clockMsRef.current?.();
    let elapsed: number;
    if (typeof fromMedia === "number" && Number.isFinite(fromMedia)) {
      elapsed = fromMedia;
      startWallRef.current = performance.now() - elapsed;
    } else {
      elapsed = performance.now() - startWallRef.current;
    }
    if (dur <= 0 || elapsed >= dur) {
      setPlayhead(dur > 0 ? dur : 0);
      setPlaying(false);
      stopRaf();
      return;
    }
    setPlayhead(elapsed, false);
    rafRef.current = requestAnimationFrame(tick);
  }, [setPlayhead, setPlaying, stopRaf]);

  const play = useCallback(() => {
    const dur = durationRef.current;
    if (dur <= 0) return;
    const from = playheadRef.current >= dur ? 0 : playheadRef.current;
    setPlayhead(from);
    startWallRef.current = performance.now() - from;
    setPlaying(true);
    stopRaf();
    rafRef.current = requestAnimationFrame(tick);
  }, [setPlayhead, setPlaying, stopRaf, tick]);

  const pause = useCallback(() => {
    setPlaying(false);
    stopRaf();
    // 暂停时把 React state 对齐到真实播放头：播放期间 state 是节流的，可能落后于
    // 竖线/画面；不对齐会出现「竖线停在 A 段、预览却是 B 段」之类的错位。
    lastStateMsRef.current = playheadRef.current;
    setPlayheadMs(playheadRef.current);
  }, [setPlaying, stopRaf]);

  const toggle = useCallback(() => {
    if (isPlayingRef.current) pause();
    else play();
  }, [pause, play]);

  const seek = useCallback(
    (ms: number) => {
      const dur = durationRef.current;
      const clamped = Math.max(0, Math.min(ms, dur));
      setPlayhead(clamped);
      if (isPlayingRef.current) startWallRef.current = performance.now() - clamped;
    },
    [setPlayhead],
  );

  // 编辑导致总时长缩短到当前播放头之后时，把播放头收回末尾。
  useEffect(() => {
    if (playheadRef.current > durationMs) seek(durationMs);
  }, [durationMs, seek]);

  useEffect(() => stopRaf, [stopRaf]);

  return { playheadMs, isPlaying, play, pause, toggle, seek };
}
