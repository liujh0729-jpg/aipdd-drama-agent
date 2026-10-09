/**
 * ffmpeg.wasm 单线程转码兜底 —— 只在浏览器解不了源编码（典型：Edge 没装
 * HEVC 扩展遇到 HEVC）时由 videoTranscode.ts 动态 import。独立成模块是为了
 * 让 @ffmpeg/* 和 ~31MB 的 wasm 核心留在懒加载 chunk 里，不进主包。
 *
 * 页面没开 COEP（无 SharedArrayBuffer），只能用单线程核心 @ffmpeg/core；
 * 速度约 0.2~0.5x 实时，调用方需要用进度 UI 兜住等待体验。
 */

import { FFmpeg } from "@ffmpeg/ffmpeg";
import { fetchFile } from "@ffmpeg/util";
import coreJsUrl from "@ffmpeg/core?url";
import coreWasmUrl from "@ffmpeg/core/wasm?url";

let ffmpegSingleton: Promise<FFmpeg> | null = null;

function loadFfmpeg(): Promise<FFmpeg> {
  if (!ffmpegSingleton) {
    ffmpegSingleton = (async () => {
      const ffmpeg = new FFmpeg();
      await ffmpeg.load({ coreURL: coreJsUrl, wasmURL: coreWasmUrl });
      return ffmpeg;
    })();
    // 加载失败（网络中断等）不缓存失败态，下次重试。
    ffmpegSingleton.catch(() => {
      ffmpegSingleton = null;
    });
  }
  return ffmpegSingleton;
}

export async function transcodeWithFfmpeg(
  file: File,
  onProgress?: (progress: number) => void,
): Promise<Blob> {
  const ffmpeg = await loadFfmpeg();
  const ext = /\.[^.]+$/.exec(file.name)?.[0] ?? ".mp4";
  const inputName = `input${ext}`;
  const outputName = "output.mp4";

  const progressHandler = ({ progress }: { progress: number }) => {
    // ffmpeg 偶尔会报出 >1 的瞬时值，夹一下。
    onProgress?.(Math.min(1, Math.max(0, progress)));
  };
  ffmpeg.on("progress", progressHandler);
  try {
    await ffmpeg.writeFile(inputName, await fetchFile(file));
    const code = await ffmpeg.exec([
      "-i", inputName,
      "-c:v", "libx264",
      "-preset", "veryfast",
      "-crf", "23",
      "-pix_fmt", "yuv420p",
      // H.264 4:2:0 要求偶数宽高，奇数尺寸的源向下取偶。
      "-vf", "scale=trunc(iw/2)*2:trunc(ih/2)*2",
      "-c:a", "aac",
      "-b:a", "128k",
      "-movflags", "+faststart",
      outputName,
    ]);
    if (code !== 0) {
      throw new Error(`ffmpeg exited with code ${code}`);
    }
    const data = await ffmpeg.readFile(outputName);
    if (typeof data === "string" || data.byteLength === 0) {
      throw new Error("ffmpeg produced empty output");
    }
    return new Blob([data.slice()], { type: "video/mp4" });
  } finally {
    ffmpeg.off("progress", progressHandler);
    // 清掉 MEMFS 里的中间文件，转长视频时别把内存越堆越高。
    await ffmpeg.deleteFile(inputName).catch(() => {});
    await ffmpeg.deleteFile(outputName).catch(() => {});
  }
}
