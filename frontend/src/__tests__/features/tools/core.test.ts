// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { describe, expect, it } from "vitest";

import {
  firstMediaUrlInResult,
  kindFromFile,
  MIN_BOX_SIZE,
  rectFromCorners,
  resizeRectTo,
  runBatch,
  shiftRect,
  TOOL_GROUPS,
  TOOL_NAV,
  TOOLS,
  toolByKind,
  toolGroupMembers,
  toolGroupOf,
  wasArchivedToLibrary,
} from "@/features/tools/core";

describe("tools core TOOLS", () => {
  it("orders the batch tools with content creation first and music creation last", () => {
    expect(TOOLS.map((t) => t.kind)).toEqual([
      // [aipdd fork delta] 内容创作组置顶（菜单首项），成员 article-video → channel-short-video。
      "article-video",
      "channel-short-video",
      // [aipdd fork delta] 音视频提取文案：菜单第二项。
      "media-copywrite",
      "video-clone",
      "video-mix",
      // [aipdd fork delta] 口播智能剪辑：独立条目，紧邻混剪之后，不并入分组。
      "narration-smart-edit",
      "video-idea",
      "voice-clone",
      // [aipdd fork delta] 视频配音同属音频族，紧跟人声复刻之后。
      "video-dubbing",
      "lip-sync",
      "replace-person",
      "replace-product",
      "video-watermark",
      "image-watermark",
      "video-reverse-script",
      "whiteboard-animation",
      "explainer-video",
      // [aipdd fork delta req46] 音乐创作：菜单最后一项。
      "music-create",
    ]);
  });

  it("collapses the explainer class into one nav entry and puts content creation first", () => {
    // 菜单入口由 TOOLS 现算：同组类型只占一条；内容创作组占菜单首项，音乐创作收尾。
    expect(TOOL_NAV.map((entry) => (entry.type === "tool" ? entry.kind : entry.id))).toEqual([
      "content-creation",
      "media-copywrite",
      "video-clone",
      "video-mix",
      // [aipdd fork delta] 口播智能剪辑：独立入口，紧随混剪。
      "narration-smart-edit",
      "video-idea",
      "voice-clone",
      // [aipdd fork delta] 视频配音同属音频族，紧跟人声复刻之后。
      "video-dubbing",
      "lip-sync",
      "replace-person",
      "replace-product",
      "video-watermark",
      "image-watermark",
      "video-reverse-script",
      "explainer-class",
      // [aipdd fork delta req46] 音乐创作收尾。
      "music-create",
    ]);
    const firstEntry = TOOL_NAV[0];
    expect(firstEntry.type).toBe("group");
    expect(firstEntry.type === "group" ? firstEntry.id : null).toBe("content-creation");
    const lastEntry = TOOL_NAV[TOOL_NAV.length - 1];
    expect(lastEntry.type).toBe("tool");
    expect(lastEntry.type === "tool" ? lastEntry.kind : null).toBe("music-create");
    const explainer = TOOL_NAV.find(
      (entry) => entry.type === "group" && entry.id === "explainer-class",
    );
    expect(explainer?.type).toBe("group");
    expect(explainer && explainer.type === "group" ? explainer.titleKey : null).toBe(
      "tools.explainerClass.title",
    );
    expect(toolGroupMembers("explainer-class")).toEqual(["whiteboard-animation", "explainer-video"]);
    expect(toolGroupMembers("content-creation")).toEqual([
      "article-video",
      "channel-short-video",
    ]);
  });

  it("keeps merged entry's members owning their own task types", () => {
    // 入口合一不等于任务合一：kind / taskTypes / sourceTool 各自独立，
    // 否则历史任务的归属过滤（后端 metadata.source_tool）与命名会一起断掉。
    expect(toolGroupOf("whiteboard-animation")?.id).toBe("explainer-class");
    expect(toolGroupOf("explainer-video")?.id).toBe("explainer-class");
    expect(toolGroupOf("video-mix")).toBeNull();
    expect(TOOL_GROUPS["explainer-class"].defaultKind).toBe("whiteboard-animation");
    for (const kind of toolGroupMembers("explainer-class")) {
      expect(toolByKind(kind).group).toBe("explainer-class");
      expect(toolByKind(kind).modelKind).toBe("audio");
      expect(toolByKind(kind).accept).toBeNull();
    }
    expect(toolByKind("whiteboard-animation").taskTypes).toEqual(["freezone_whiteboard_animation"]);
    expect(toolByKind("explainer-video").taskTypes).toEqual(["freezone_explainer_video"]);
    // 分组标题/说明/切换器标签都有自己的词条位置（不能借用成员的名字）。
    expect(TOOL_GROUPS["explainer-class"].descKey).toBe("tools.explainerClass.desc");
    expect(TOOL_GROUPS["explainer-class"].typeLabelKey).toBe("tools.explainerClass.typeLabel");
  });

  it("each tool declares the task types it produces", () => {
    for (const tool of TOOLS) {
      expect(tool.taskTypes.length).toBeGreaterThan(0);
    }
    // 视频复刻：全自动成片专用任务类型（freezone_video_clone）。
    expect(toolByKind("video-clone").modelKind).toBe("video");
    expect(toolByKind("video-clone").accept).toBe("video");
    expect(toolByKind("video-clone").taskTypes).toEqual(["freezone_video_clone"]);
    // 混剪短视频：素材池（图/视频）一次产出一条成片；模型仅配音用（audio 目录）。
    expect(toolByKind("video-mix").modelKind).toBe("audio");
    expect(toolByKind("video-mix").accept).toBe("video-or-image");
    expect(toolByKind("video-mix").taskTypes).toEqual(["freezone_video_mix"]);
    // [aipdd fork delta] 口播智能剪辑：独立条目，源为单条视频（面板自管上传/项目视频），
    // 无共享批量素材入口（accept=video 仅作源提示），任务类型独立，不并入分组。
    expect(toolByKind("narration-smart-edit").modelKind).toBe("video");
    expect(toolByKind("narration-smart-edit").accept).toBe("video");
    expect(toolByKind("narration-smart-edit").taskTypes).toEqual(["freezone_narration_smart_edit"]);
    expect(toolByKind("narration-smart-edit").titleKey).toBe("tools.narrationSmartEdit.title");
    expect(toolGroupOf("narration-smart-edit")).toBeNull();
    // 白板动画 / 科普讲解两个类型的归属断言在上面的分组用例里（它们共用一个菜单入口）。
    // [aipdd fork delta req46] 音乐创作：模型不经网关（候选来自「音乐」目录），
    // 任务类型独立，参考音频在面板内自管 → 不占批量素材入口。
    expect(toolByKind("music-create").modelKind).toBe("music");
    expect(toolByKind("music-create").accept).toBeNull();
    expect(toolByKind("music-create").taskTypes).toEqual(["freezone_music_generate"]);
    expect(toolByKind("music-create").titleKey).toBe("tools.musicCreate.title");
    // [aipdd fork delta] 视频配音：与 voice-clone 同口径读 audio 目录，源视频/参考
    // 音频在面板内自管 → 不占批量素材入口，任务类型独立。
    expect(toolByKind("video-dubbing").modelKind).toBe("audio");
    expect(toolByKind("video-dubbing").accept).toBeNull();
    expect(toolByKind("video-dubbing").taskTypes).toEqual(["freezone_video_dubbing"]);
    expect(toolByKind("video-dubbing").titleKey).toBe("tools.videoDubbing.title");
    // 一句话生成短视频：无媒体批量输入（纯创意文本）；肖像/帧与逐镜双模型在面板内选。
    expect(toolByKind("video-idea").modelKind).toBe("video");
    expect(toolByKind("video-idea").accept).toBeNull();
    expect(toolByKind("video-idea").taskTypes).toEqual(["freezone_video_idea"]);
    expect(toolByKind("replace-person").modelKind).toBe("video");
    expect(toolByKind("replace-product").modelKind).toBe("video");
    expect(toolByKind("voice-clone").modelKind).toBe("audio");
    expect(toolByKind("image-watermark").modelKind).toBe("image");
    // 视频去水印双路径：默认引擎本地擦除，选定模型走 AI 视频编辑（仍读 video 目录）。
    expect(toolByKind("video-watermark").modelKind).toBe("video");
    expect(toolByKind("video-watermark").accept).toBe("video");
    expect(toolByKind("video-watermark").taskTypes).toEqual([
      "freezone_video_erase",
      "freezone_video_gen",
    ]);
    // 后端实际 task_type：omni 路由产出 freezone_video_gen、图片工具产出
    // freezone_edit。幽灵类型会让任务记录过滤不到，这里钉住。
    expect(toolByKind("lip-sync").taskTypes).toEqual(["freezone_video_gen"]);
    expect(toolByKind("replace-person").taskTypes).toEqual(["freezone_video_gen"]);
    expect(toolByKind("replace-product").taskTypes).toEqual(["freezone_video_gen"]);
    expect(toolByKind("image-watermark").taskTypes).toEqual(["freezone_edit"]);
    expect(toolByKind("voice-clone").taskTypes).toEqual(["freezone_audio_speech"]);
    // 音视频提取文案（req21 由抖音文案提取泛化）：输入是链接 / 上传媒体而非
    // 批量素材（无共享批量输入，accept=null），ASR 模型来自专用候选端点（网关
    // 模型 list + 内置 FunASR / 豆包），由用户显式指定。taskTypes 同时包含新旧两
    // 个任务类型，保证旧抖音历史任务仍能在本卡过滤到。
    expect(toolByKind("media-copywrite").modelKind).toBe("audio");
    expect(toolByKind("media-copywrite").accept).toBeNull();
    expect(toolByKind("media-copywrite").taskTypes).toEqual([
      "freezone_media_copywrite",
      "freezone_douyin_copywrite",
    ]);
    // 视频反推剧本：视觉候选来自 /model-gateway/vision-models（modelKind=vision，
    // 面板自管下拉、留空 = 后端默认视觉链）；无共享批量输入（accept=null），
    // 产物 txt 走独立任务类型，不覆盖故事脚本 JSON。
    expect(toolByKind("video-reverse-script").modelKind).toBe("vision");
    expect(toolByKind("video-reverse-script").accept).toBeNull();
    expect(toolByKind("video-reverse-script").taskTypes).toEqual([
      "freezone_video_script_reverse",
    ]);
  });
});

describe("kindFromFile", () => {
  it("maps MIME to media kind", () => {
    expect(kindFromFile(new File(["x"], "a.png", { type: "image/png" }))).toBe("image");
    expect(kindFromFile(new File(["x"], "a.mp4", { type: "video/mp4" }))).toBe("video");
    expect(kindFromFile(new File(["x"], "a.mp3", { type: "audio/mpeg" }))).toBe("audio");
    expect(kindFromFile(new File(["x"], "notes.txt", { type: "text/plain" }))).toBeNull();
  });
});

describe("box geometry (video watermark drag-select)", () => {
  it("rectFromCorners builds a normalized bbox from any two corners", () => {
    const box = rectFromCorners({ x: 0.8, y: 0.9 }, { x: 0.2, y: 0.3 });
    expect(box.x).toBeCloseTo(0.2);
    expect(box.y).toBeCloseTo(0.3);
    expect(box.width).toBeCloseTo(0.6);
    expect(box.height).toBeCloseTo(0.6);
  });

  it("rectFromCorners clamps out-of-range points to 0-1", () => {
    const box = rectFromCorners({ x: -0.2, y: 0.5 }, { x: 1.4, y: 2 });
    expect(box.x).toBe(0);
    expect(box.y).toBe(0.5);
    expect(box.width).toBe(1);
    expect(box.height).toBe(0.5);
  });

  it("shiftRect keeps the whole box inside 0-1", () => {
    const moved = shiftRect({ x: 0.9, y: 0.9, width: 0.2, height: 0.2 }, 0.5, 0.5);
    expect(moved.x).toBeCloseTo(0.8);
    expect(moved.y).toBeCloseTo(0.8);
    const clampedTopLeft = shiftRect({ x: 0.1, y: 0.1, width: 0.2, height: 0.2 }, -0.9, -0.9);
    expect(clampedTopLeft.x).toBe(0);
    expect(clampedTopLeft.y).toBe(0);
  });

  it("resizeRectTo clamps to canvas and enforces the minimum size", () => {
    const grown = resizeRectTo({ x: 0.5, y: 0.5, width: 0.2, height: 0.2 }, 3, 4);
    expect(grown.width).toBeCloseTo(0.5);
    expect(grown.height).toBeCloseTo(0.5);
    const shrunk = resizeRectTo({ x: 0.5, y: 0.5, width: 0.2, height: 0.2 }, 0.5, 0.5);
    expect(shrunk.width).toBe(MIN_BOX_SIZE);
    expect(shrunk.height).toBe(MIN_BOX_SIZE);
  });
});

describe("runBatch", () => {
  it("runs every item and keeps input order", async () => {
    const seen: number[] = [];
    const out = await runBatch([10, 20, 30, 40], 2, async (v, i) => {
      seen.push(i);
      return v * 2;
    });
    expect(out).toEqual([20, 40, 60, 80]);
    expect(seen.sort()).toEqual([0, 1, 2, 3]);
  });

  it("caps concurrency at the limit", async () => {
    let active = 0;
    let peak = 0;
    await runBatch(Array.from({ length: 6 }, (_, i) => i), 2, async () => {
      active += 1;
      peak = Math.max(peak, active);
      await new Promise((r) => setTimeout(r, 1));
      active -= 1;
    });
    expect(peak).toBeLessThanOrEqual(2);
  });
});

describe("firstMediaUrlInResult", () => {
  it("finds a media url nested in a free-form result", () => {
    expect(
      firstMediaUrlInResult({
        ok: true,
        data: { artifacts: [{ path: "/tmp/x.txt" }, { url: "/static/freezone/a.mp4?v=1" }] },
      }),
    ).toBe("/static/freezone/a.mp4?v=1");
  });

  it("returns null when nothing media-like exists", () => {
    expect(firstMediaUrlInResult({ message: "done", count: 3 })).toBeNull();
  });
});

describe("wasArchivedToLibrary", () => {
  it("recognises the backend archive marker", () => {
    expect(
      wasArchivedToLibrary({ archived_to_library: true, library_item_id: "tool-job-1" }),
    ).toBe(true);
  });

  it("stays false for canvas-style results and junk", () => {
    expect(wasArchivedToLibrary({ output_url: "/static/a.mp4" })).toBe(false);
    expect(wasArchivedToLibrary(null)).toBe(false);
    expect(wasArchivedToLibrary("done")).toBe(false);
    expect(wasArchivedToLibrary(["archived_to_library"])).toBe(false);
  });
});
