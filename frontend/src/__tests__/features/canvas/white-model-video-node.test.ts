// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
// [aipdd fork delta] 白模视频节点（需求 #29）注册 + 画布「添加节点」菜单可见性。
//
// 这条测试盯的是用户报的「俯视图、智能剪辑、白模视频看不到」——真正驱动添加节点
// 网格的是 CANVAS_ADD_NODE_TYPES 这份数组（不是 registry 的 visibleInMenu），漏登记
// 就会让节点在源码里存在却在菜单里消失。所以直接对数组断言。
import { describe, expect, it } from "vitest";

import { CANVAS_NODE_TYPES } from "@/features/canvas/domain/canvasNodes";
import {
  canvasNodeDefinitions,
  getMenuNodeDefinitions,
  getDownstreamSpawnTypes,
  isManualConnectionAllowed,
  isUpstreamConnectionAllowed,
} from "@/features/canvas/domain/nodeRegistry";
import { CANVAS_ADD_NODE_TYPES } from "@/features/canvas/ui/canvas-node-menu-shared";

describe("白模视频节点注册", () => {
  it("进了节点总表、可见于创建菜单，且图标 key 一致", () => {
    const definition = canvasNodeDefinitions[CANVAS_NODE_TYPES.whiteModelVideo];
    expect(definition).toBeTruthy();
    expect(definition.visibleInMenu).toBe(true);
    expect(definition.menuIcon).toBe("whiteModelVideo");
    expect(definition.menuLabelKey).toBe("node.menu.whiteModelVideo");
    expect(getMenuNodeDefinitions().map((item) => item.type)).toContain(
      CANVAS_NODE_TYPES.whiteModelVideo,
    );
  });

  it("createDefaultData 是一张空的白模视频卡（无 URL、未上传）", () => {
    const data = canvasNodeDefinitions[
      CANVAS_NODE_TYPES.whiteModelVideo
    ].createDefaultData() as Record<string, unknown>;
    expect(data.videoUrl).toBe(null);
    expect(data.isUploading).toBe(false);
    expect(data.aspectRatio).toBe("16:9");
  });

  it("作为素材源可以往下游视频节点连（供参考创作成片）", () => {
    expect(
      isUpstreamConnectionAllowed(
        CANVAS_NODE_TYPES.whiteModelVideo,
        CANVAS_NODE_TYPES.video,
      ),
    ).toBe(true);
    expect(
      isManualConnectionAllowed(
        CANVAS_NODE_TYPES.whiteModelVideo,
        CANVAS_NODE_TYPES.video,
      ),
    ).toBe(true);
    expect(getDownstreamSpawnTypes(CANVAS_NODE_TYPES.whiteModelVideo)).toContain(
      CANVAS_NODE_TYPES.video,
    );
  });
});

describe("画布「添加节点」菜单可见性", () => {
  it("俯视图 / 智能剪辑 / 白模视频都在添加节点菜单里", () => {
    // 用户明确要能在这三个入口看到它们。
    for (const type of [
      CANVAS_NODE_TYPES.topDownView,
      CANVAS_NODE_TYPES.smartEdit,
      CANVAS_NODE_TYPES.whiteModelVideo,
    ]) {
      expect(CANVAS_ADD_NODE_TYPES).toContain(type);
    }
  });

  it("菜单里列出的每个类型都能在 registry 里取到定义（否则网格渲染成 null）", () => {
    for (const type of CANVAS_ADD_NODE_TYPES) {
      expect(canvasNodeDefinitions[type]).toBeTruthy();
    }
  });
});
