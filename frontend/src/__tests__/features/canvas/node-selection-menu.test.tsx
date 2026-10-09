// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import {
  NodeSelectionMenu,
  buildReferenceGenerateItems,
} from "@/features/canvas/NodeSelectionMenu";
import { CANVAS_NODE_TYPES, type CanvasNodeType } from "@/features/canvas/domain/canvasNodes";
import {
  getNodeDefinition,
  getDownstreamSpawnTypes,
  getUpstreamSpawnTypes,
} from "@/features/canvas/domain/nodeRegistry";

const translations: Record<string, string> = {
  "node.menu.sectionAddNode": "添加节点",
  "node.menu.sectionAddResource": "添加资源",
  "node.menu.sectionSkillNode": "技能节点",
  "node.menu.uploadImage": "上传资源",
  "node.menu.image": "图片",
  "node.menu.aiImageGeneration": "AI 图片",
  "node.menu.storyboard": "分格抽取结果",
  "node.menu.storyboardGen": "多版本宫格",
  "node.menu.beatContext": "镜头上下文",
  "node.menu.textAnnotation": "文本",
  "node.menu.video": "视频",
  "node.menu.audio": "音频",
  "node.menu.videoStory": "视频故事",
  "node.menu.videoCompose": "视频合成",
  "node.menu.script": "脚本",
  "node.menu.screenplay": "剧本",
  "node.menu.topDownView": "俯视图",
  "node.menu.smartEdit": "智能剪辑",
  "node.menu.whiteModelVideo": "白模视频",
  "node.menu.pano360Viewer": "360° 全景",
  "node.menu.threeDWorld": "导演世界",
  "node.displayName.threeDWorldNode": "3D 世界",
};

vi.mock("react-i18next", () => ({
  useTranslation: () => ({
    t: (key: string) => translations[key] ?? key,
  }),
}));

// 图片类候选在网格里合并成一枚「图片」按钮（创建时 imageGen → imageEdit → upload 落位）。
const IMAGE_FAMILY = new Set<CanvasNodeType>([
  CANVAS_NODE_TYPES.imageGen,
  CANVAS_NODE_TYPES.imageEdit,
  CANVAS_NODE_TYPES.upload,
  CANVAS_NODE_TYPES.exportImage,
]);

const identity = (key: string) => key;

function assertItemsCoverWhitelist(
  origin: CanvasNodeType,
  direction: "downstream" | "upstream",
) {
  const allowed =
    direction === "downstream" ? getDownstreamSpawnTypes(origin) : getUpstreamSpawnTypes(origin);
  const items = buildReferenceGenerateItems(identity, allowed);
  const created = items.map((item) => item.type).filter(Boolean) as CanvasNodeType[];

  // 白名单给了、且允许进创建菜单的类型，必须一枚不落都有按钮。
  const missing = allowed.filter((type) => {
    if (IMAGE_FAMILY.has(type)) return false;
    return getNodeDefinition(type).visibleInMenu && !created.includes(type);
  });
  expect(missing, `${origin}(${direction}) 白名单成员在菜单里缺席`).toEqual([]);

  // 反过来：菜单也不该给出白名单之外的按钮。
  expect(created.filter((type) => !allowed.includes(type))).toEqual([]);

  if (allowed.some((type) => IMAGE_FAMILY.has(type))) {
    expect(created.some((type) => IMAGE_FAMILY.has(type))).toBe(true);
  }
}

describe("NodeSelectionMenu", () => {
  it("shows standalone shot context in the add-node menu", () => {
    render(
      <NodeSelectionMenu
        position={{ x: 12, y: 16 }}
        onSelect={vi.fn()}
        onClose={vi.fn()}
      />,
    );

    expect(screen.getByText("镜头上下文")).toBeInTheDocument();
  });

  it("每种节点的「引用该节点生成」都与 nodeRegistry 白名单同源（防菜单漂移）", () => {
    // 这张网格原先是一份手工列举的固定数组：注册表加了智能剪辑、视频合成之后它没跟上，
    // 用户看到的就是「功能凭空消失」；未声明下游白名单的源类型（文本等）更是只露出七项，
    // 而白名单其实给了十几项。现在网格直接由 allowedTypes 构建，两个方向一起锁死。
    for (const origin of Object.values(CANVAS_NODE_TYPES) as CanvasNodeType[]) {
      assertItemsCoverWhitelist(origin, "downstream");
      assertItemsCoverWhitelist(origin, "upstream");
    }
  });

  it("渲染出的按钮与白名单构建的条目一一对应", () => {
    for (const origin of [
      CANVAS_NODE_TYPES.video,
      CANVAS_NODE_TYPES.audio,
      CANVAS_NODE_TYPES.imageGen,
      CANVAS_NODE_TYPES.textAnnotation,
    ]) {
      const allowed = getDownstreamSpawnTypes(origin);
      const items = buildReferenceGenerateItems((key) => translations[key] ?? key, allowed);
      const { unmount } = render(
        <NodeSelectionMenu
          position={{ x: 12, y: 16 }}
          allowedTypes={allowed}
          onSelect={vi.fn()}
          onClose={vi.fn()}
        />,
      );
      for (const item of items) {
        expect(screen.getByRole("button", { name: item.label })).toBeEnabled();
      }
      expect(screen.getAllByRole("button")).toHaveLength(items.length);
      unmount();
    }
  });

  it("图片 / 视频 / 音频 / 文本节点的菜单里都能直接创建智能剪辑", () => {
    // 智能剪辑吃这三类素材（ASR / 提取文案 / 图像处理），入口必须都在；文本节点是
    // 「白名单给了但网格漏了」那一类，同源之后一并出现。
    for (const origin of [
      CANVAS_NODE_TYPES.imageGen,
      CANVAS_NODE_TYPES.video,
      CANVAS_NODE_TYPES.audio,
      CANVAS_NODE_TYPES.textAnnotation,
    ]) {
      const { unmount } = render(
        <NodeSelectionMenu
          position={{ x: 12, y: 16 }}
          allowedTypes={getDownstreamSpawnTypes(origin)}
          onSelect={vi.fn()}
          onClose={vi.fn()}
        />,
      );
      expect(screen.getByRole("button", { name: "智能剪辑" })).toBeEnabled();
      unmount();
    }
  });

  it("白名单没给的类型不会多出按钮", () => {
    // 音频节点不该出现「多版本宫格」（不在音频下游白名单里）——同源不等于无脑放开。
    const { unmount } = render(
      <NodeSelectionMenu
        position={{ x: 12, y: 16 }}
        allowedTypes={getDownstreamSpawnTypes(CANVAS_NODE_TYPES.audio)}
        onSelect={vi.fn()}
        onClose={vi.fn()}
      />,
    );
    expect(screen.queryByRole("button", { name: "多版本宫格" })).not.toBeInTheDocument();
    unmount();
  });
});
