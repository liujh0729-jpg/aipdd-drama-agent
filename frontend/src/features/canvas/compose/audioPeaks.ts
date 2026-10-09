// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
/**
 * 音频波形峰值解码 + 模块级缓存。
 *
 * 给「视频合成」时间线上的音频片段画波形用：按固定密度（{@link PEAK_BUCKETS_PER_SEC}）
 * 把整段源音频降采样成归一化峰值数组，时间线上再按片段的裁剪窗口切片绘制。
 * decodeAudioData 开销不小，同一 src 只解一次并 LRU 缓存；并发请求复用同一 promise。
 */

/** 峰值采样密度（每秒桶数）。波形按此密度切片，越大越细。 */
export const PEAK_BUCKETS_PER_SEC = 120;

const CACHE_MAX = 60;
const cache = new Map<string, Float32Array>();
const inflight = new Map<string, Promise<Float32Array>>();

function cachePeaks(src: string, peaks: Float32Array): void {
  cache.delete(src); // 命中即刷新到「最近使用」末尾
  cache.set(src, peaks);
  while (cache.size > CACHE_MAX) {
    const oldest = cache.keys().next().value;
    if (oldest === undefined) break;
    cache.delete(oldest);
  }
}

function computePeaks(buffer: AudioBuffer): Float32Array {
  const channel = buffer.getChannelData(0);
  const bucketCount = Math.max(1, Math.ceil(buffer.duration * PEAK_BUCKETS_PER_SEC));
  const samplesPerBucket = Math.max(1, Math.floor(channel.length / bucketCount));
  const peaks = new Float32Array(bucketCount);
  let max = 0;
  for (let i = 0; i < bucketCount; i += 1) {
    const start = i * samplesPerBucket;
    const end = Math.min(channel.length, start + samplesPerBucket);
    let peak = 0;
    for (let j = start; j < end; j += 1) {
      const v = Math.abs(channel[j]);
      if (v > peak) peak = v;
    }
    peaks[i] = peak;
    if (peak > max) max = peak;
  }
  // 归一化，放大较安静的素材便于观看。
  if (max > 0) {
    for (let i = 0; i < peaks.length; i += 1) peaks[i] /= max;
  }
  return peaks;
}

/** 已解码并缓存的峰值（同步读取，未缓存返回 null）。 */
export function getCachedAudioPeaks(src: string): Float32Array | null {
  return cache.get(src) ?? null;
}

/** 拉取 + 解码 + 降采样音频，返回归一化峰值数组（带缓存 / 并发去重）。 */
export async function loadAudioPeaks(src: string): Promise<Float32Array> {
  const cached = cache.get(src);
  if (cached) {
    cachePeaks(src, cached); // LRU bump
    return cached;
  }
  const existing = inflight.get(src);
  if (existing) return existing;

  const task = (async () => {
    const res = await fetch(src);
    const arrayBuffer = await res.arrayBuffer();
    const Ctx =
      window.AudioContext ||
      (window as unknown as { webkitAudioContext: typeof AudioContext })
        .webkitAudioContext;
    const ctx = new Ctx();
    try {
      const decoded = await ctx.decodeAudioData(arrayBuffer);
      const peaks = computePeaks(decoded);
      cachePeaks(src, peaks);
      return peaks;
    } finally {
      void ctx.close?.();
      inflight.delete(src);
    }
  })();
  inflight.set(src, task);
  return task;
}
