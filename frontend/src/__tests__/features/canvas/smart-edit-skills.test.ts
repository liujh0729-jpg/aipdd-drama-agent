// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
// [aipdd fork delta] 智能剪辑节点的技能目录与建边规则回归。
//
// 建边那组断言是命门：结果边由组件程序生成（smartEdit → 结果节点），若白名单
// 漏登记，store 的 addEdge 规范化会静默丢边 —— 用户看到结果节点冒出来却和剪辑
// 节点没有任何连线，而且不报任何错。
import { describe, expect, it } from "vitest";

import {
  CANVAS_NODE_TYPES,
  type CanvasNode,
} from "@/features/canvas/domain/canvasNodes";
import {
  canvasNodeDefinitions,
  getMenuNodeDefinitions,
  getDownstreamSpawnTypes,
  getUpstreamSpawnTypes,
  isManualConnectionAllowed,
  isUpstreamConnectionAllowed,
} from "@/features/canvas/domain/nodeRegistry";
import {
  collectUpstreamMediaByKind,
  detectInputKind,
  resolveNodeMediaUrl,
  resolveSkillSource,
  skillsForKind,
  SMART_EDIT_SKILLS,
} from "@/features/canvas/domain/smartEditSkills";

function node(id: string, type: string, data: Record<string, unknown>): CanvasNode {
  return {
    id,
    type,
    position: { x: 0, y: 0 },
    data,
  } as unknown as CanvasNode;
}

const VIDEO = node("v1", CANVAS_NODE_TYPES.video, {
  videoUrl: "https://cdn/in.mp4",
});
const VIDEO_EMPTY = node("v2", CANVAS_NODE_TYPES.video, { videoUrl: null });
const AUDIO = node("a1", CANVAS_NODE_TYPES.audio, {
  audioUrl: "https://cdn/in.mp3",
});
const AUDIO_EMPTY = node("a2", CANVAS_NODE_TYPES.audio, { audioUrl: "" });
const IMAGE = node("i1", CANVAS_NODE_TYPES.upload, {
  imageUrl: "https://cdn/in.png",
});
const TEXT = node("t1", CANVAS_NODE_TYPES.textAnnotation, { content: "hi" });

describe("detectInputKind", () => {
  it("按内容类型识别上游主素材", () => {
    expect(detectInputKind([VIDEO])).toBe("video");
    expect(detectInputKind([AUDIO])).toBe("audio");
    expect(detectInputKind([IMAGE])).toBe("image");
  });

  it("没有上游或上游没有可用 URL 时返回 null", () => {
    expect(detectInputKind([])).toBe(null);
    expect(detectInputKind([TEXT])).toBe(null);
    expect(detectInputKind([VIDEO_EMPTY, AUDIO_EMPTY])).toBe(null);
  });

  it("多种素材并存时按 视频 > 图片 > 音频 取主素材", () => {
    expect(detectInputKind([AUDIO, IMAGE, VIDEO])).toBe("video");
    expect(detectInputKind([AUDIO, IMAGE])).toBe("image");
    expect(detectInputKind([AUDIO, VIDEO_EMPTY, IMAGE])).toBe("image");
  });
});

describe("skillsForKind / 技能目录", () => {
  it("每种内容类型列举各自的技能集", () => {
    expect(skillsForKind("video").map((s) => s.id)).toEqual([
      "extract-copy",
      "reverse-script",
      "audio-separate",
      "video-upscale",
      "lip-sync",
      "replace-person",
      "replace-product",
      "video-clone",
    ]);
    expect(skillsForKind("audio").map((s) => s.id)).toEqual(["audio-extract"]);
    expect(skillsForKind("image").map((s) => s.id)).toEqual([
      "image-upscale",
      "reverse-prompt",
      "image-watermark",
    ]);
  });

  it("技能 id 全局唯一，且元数据齐备（title/desc/icon/参数）", () => {
    const ids = SMART_EDIT_SKILLS.map((s) => s.id);
    expect(new Set(ids).size).toBe(ids.length);
    for (const skill of SMART_EDIT_SKILLS) {
      expect(skill.titleKey.startsWith("smartEdit.skills.")).toBe(true);
      expect(skill.descKey.startsWith("smartEdit.skills.")).toBe(true);
      expect(skill.icon).toBeTruthy();
      expect(["text", "video", "audio", "image", "video+audio"]).toContain(
        skill.resultKind,
      );
      for (const extra of skill.extraInputs ?? []) {
        expect(["video", "image", "audio"]).toContain(extra.kind);
        expect(typeof extra.required).toBe("boolean");
        expect(extra.hintKey.startsWith("smartEdit.inputs.")).toBe(true);
      }
    }
  });

  it("多输入视频技能声明第二路素材（对口型要音频、换人/换品要图）", () => {
    const byId = (id: string) =>
      SMART_EDIT_SKILLS.find((s) => s.id === id);
    expect(byId("lip-sync")?.extraInputs).toEqual([
      {
        kind: "audio",
        required: true,
        max: 1,
        hintKey: "smartEdit.inputs.needDriverAudio",
      },
    ]);
    expect(byId("replace-person")?.extraInputs?.[0]).toMatchObject({
      kind: "image",
      required: true,
    });
    expect(byId("replace-product")?.extraInputs?.[0]).toMatchObject({
      kind: "image",
      required: true,
    });
    expect(byId("video-clone")?.extraInputs?.[0]).toMatchObject({
      kind: "image",
      required: false,
      max: 4,
    });
  });
});

describe("collectUpstreamMediaByKind", () => {
  it("按内容类型收集全部有 URL 的上游并保序", () => {
    const image2 = node("i2", CANVAS_NODE_TYPES.upload, {
      imageUrl: "https://cdn/in2.png",
    });
    const collected = collectUpstreamMediaByKind([
      VIDEO,
      AUDIO,
      IMAGE,
      image2,
      VIDEO_EMPTY,
      TEXT,
    ]);
    expect(collected.video).toEqual(["https://cdn/in.mp4"]);
    expect(collected.audio).toEqual(["https://cdn/in.mp3"]);
    expect(collected.image).toEqual(["https://cdn/in.png", "https://cdn/in2.png"]);
  });

  it("没有可收集素材时各类为空数组", () => {
    expect(collectUpstreamMediaByKind([TEXT, VIDEO_EMPTY])).toEqual({
      video: [],
      image: [],
      audio: [],
    });
  });
});

describe("resolveSkillSource / resolveNodeMediaUrl", () => {
  it("为技能找到匹配输入类型的上游 URL，类型不匹配返回 null", () => {
    expect(resolveSkillSource("video", [TEXT, VIDEO, AUDIO])).toBe(
      "https://cdn/in.mp4",
    );
    expect(resolveSkillSource("audio", [VIDEO, AUDIO])).toBe(
      "https://cdn/in.mp3",
    );
    expect(resolveSkillSource("image", [VIDEO, AUDIO])).toBe(null);
  });

  it("空 URL 的节点不算有效素材", () => {
    expect(resolveNodeMediaUrl(VIDEO_EMPTY)).toBe(null);
    expect(resolveNodeMediaUrl(AUDIO_EMPTY)).toBe(null);
    expect(resolveNodeMediaUrl(TEXT)).toBe(null);
    expect(resolveNodeMediaUrl(null)).toBe(null);
  });
});

describe("智能剪辑的建边规则", () => {
  it("结果边 smartEdit → audio 在白名单内（否则会被规范化静默丢弃）", () => {
    expect(
      isUpstreamConnectionAllowed(CANVAS_NODE_TYPES.smartEdit, CANVAS_NODE_TYPES.audio),
    ).toBe(true);
    // 分离技能还会产无声视频。
    expect(
      isUpstreamConnectionAllowed(CANVAS_NODE_TYPES.smartEdit, CANVAS_NODE_TYPES.video),
    ).toBe(true);
  });

  it("用户可以把素材手工连进 smartEdit 作输入", () => {
    for (const source of [
      CANVAS_NODE_TYPES.video,
      CANVAS_NODE_TYPES.audio,
      CANVAS_NODE_TYPES.imageGen,
      CANVAS_NODE_TYPES.upload,
    ] as const) {
      expect(
        isManualConnectionAllowed(source, CANVAS_NODE_TYPES.smartEdit),
      ).toBe(true);
    }
  });

  it("左「+」菜单可创建的素材类型与建边规则一致", () => {
    const spawn = getUpstreamSpawnTypes(CANVAS_NODE_TYPES.smartEdit);
    expect([...spawn].sort()).toEqual(
      [
        CANVAS_NODE_TYPES.audio,
        CANVAS_NODE_TYPES.imageGen,
        CANVAS_NODE_TYPES.upload,
        CANVAS_NODE_TYPES.video,
      ].sort(),
    );
    // 候选集里的每种类型都必须真能连进来，否则菜单给了根连不上的线。
    for (const type of spawn) {
      expect(isUpstreamConnectionAllowed(type, CANVAS_NODE_TYPES.smartEdit)).toBe(
        true,
      );
    }
  });
});

describe("smartEdit 注册", () => {
  it("进了节点总表且出现在添加菜单里", () => {
    const definition = canvasNodeDefinitions[CANVAS_NODE_TYPES.smartEdit];
    expect(definition).toBeTruthy();
    expect(definition.visibleInMenu).toBe(true);
    expect(definition.menuIcon).toBe("smartEdit");
    expect(getMenuNodeDefinitions().map((item) => item.type)).toContain(
      CANVAS_NODE_TYPES.smartEdit,
    );
  });

  it("createDefaultData 是无上游、未选技能的空态", () => {
    const data = canvasNodeDefinitions[
      CANVAS_NODE_TYPES.smartEdit
    ].createDefaultData() as Record<string, unknown>;
    expect(data.activeSkillId).toBe(null);
    expect(data.isProcessing).toBeFalsy();
  });

  it("开放下游创建菜单（fromSource），素材节点可就地引用生成智能剪辑", () => {
    // [aipdd fork delta] 需求：图片/视频/音频节点的「引用该节点生成」要出现智能剪辑，
    // 把该素材连进来作输入。故 smartEdit 的 fromSource 打开、并进入图片类/视频/音频
    // 三张下游白名单。
    const definition = canvasNodeDefinitions[CANVAS_NODE_TYPES.smartEdit];
    expect(definition.connectivity.connectMenu.fromSource).toBe(true);
    expect(definition.connectivity.connectMenu.fromTarget).toBe(true);
    expect(getDownstreamSpawnTypes(CANVAS_NODE_TYPES.video)).toContain(CANVAS_NODE_TYPES.smartEdit);
    expect(getDownstreamSpawnTypes(CANVAS_NODE_TYPES.imageGen)).toContain(CANVAS_NODE_TYPES.smartEdit);
    expect(getDownstreamSpawnTypes(CANVAS_NODE_TYPES.upload)).toContain(CANVAS_NODE_TYPES.smartEdit);
    // 音频：建边规则与产品白名单两边都要给，否则菜单里点不到、线又连得上。
    expect(isManualConnectionAllowed(CANVAS_NODE_TYPES.audio, CANVAS_NODE_TYPES.smartEdit)).toBe(true);
    expect(getDownstreamSpawnTypes(CANVAS_NODE_TYPES.audio)).toContain(CANVAS_NODE_TYPES.smartEdit);
  });

  it("smartEdit 自身产出的音频结果边仍是系统专用，不进菜单", () => {
    // 结果边 smartEdit→audio 由程序建，不出现在 smartEdit 的下游菜单里。
    expect(isUpstreamConnectionAllowed(CANVAS_NODE_TYPES.smartEdit, CANVAS_NODE_TYPES.audio)).toBe(true);
    expect(isManualConnectionAllowed(CANVAS_NODE_TYPES.smartEdit, CANVAS_NODE_TYPES.audio)).toBe(false);
    expect(getDownstreamSpawnTypes(CANVAS_NODE_TYPES.smartEdit)).not.toContain(CANVAS_NODE_TYPES.audio);
  });
});
