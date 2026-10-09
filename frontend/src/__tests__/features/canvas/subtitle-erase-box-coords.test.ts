// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { describe, expect, it } from "vitest";

import { clientPointToDisplayedNormalized } from "@/features/canvas/nodes/VideoNode";

/**
 * 回归：智能去字幕「框选擦除」在画布 zoom ≠ 1 时，框选框与鼠标位置错位。
 *
 * 旧实现把 `getBoundingClientRect()`（视觉像素，随画布 zoom 缩放）与
 * ResizeObserver 量出的布局尺寸 / displayed 画面矩形（布局像素，未缩放）
 * 直接混算，漏了视觉→布局的比例换算：
 *   zoom 0.5 时框选范围只有鼠标实际划过的 1/2；zoom 2 时被提前钳到边缘。
 */
describe("clientPointToDisplayedNormalized — 去字幕框选坐标换算", () => {
  it("zoom = 1 基座：指针落在容器 25% / 75% 处，归一化坐标一致", () => {
    const layoutSize = { w: 400, h: 300 };
    const containerRect = { left: 1000, top: 500, width: 400, height: 300 };
    const displayed = { left: 0, top: 0, width: 400, height: 300 };

    const point = clientPointToDisplayedNormalized(
      1000 + 100,
      500 + 225,
      containerRect,
      layoutSize,
      displayed,
    );

    expect(point.nx).toBeCloseTo(0.25, 6);
    expect(point.ny).toBeCloseTo(0.75, 6);
  });

  it("画布缩小（zoom 0.5）：指针在视觉中心 → 0.5 / 0.5（不再只算到 0.25）", () => {
    const layoutSize = { w: 400, h: 300 };
    // zoom 0.5 下 getBoundingClientRect 的视觉尺寸是布局尺寸的一半。
    const containerRect = { left: 1000, top: 500, width: 200, height: 150 };
    const displayed = { left: 0, top: 0, width: 400, height: 300 };

    const point = clientPointToDisplayedNormalized(
      1000 + 100,
      500 + 75,
      containerRect,
      layoutSize,
      displayed,
    );

    expect(point.nx).toBeCloseTo(0.5, 6);
    expect(point.ny).toBeCloseTo(0.5, 6);
  });

  it("画布放大（zoom 2）：指针在视觉中心 → 0.5 / 0.5（不再被提前钳到 1）", () => {
    const layoutSize = { w: 400, h: 300 };
    const containerRect = { left: 0, top: 0, width: 800, height: 600 };
    const displayed = { left: 0, top: 0, width: 400, height: 300 };

    const point = clientPointToDisplayedNormalized(400, 300, containerRect, layoutSize, displayed);

    expect(point.nx).toBeCloseTo(0.5, 6);
    expect(point.ny).toBeCloseTo(0.5, 6);
  });

  it("竖屏视频有左右黑边（displayed 带偏移）且 zoom 0.5：画面中心 / 左上角仍对齐", () => {
    const layoutSize = { w: 400, h: 300 };
    const displayed = { left: 100, top: 0, width: 200, height: 300 };
    const containerRect = { left: 50, top: 20, width: 200, height: 150 };

    // 画面矩形中心：布局 (200, 150) → 视觉 (50 + 100, 20 + 75)。
    expect(clientPointToDisplayedNormalized(150, 95, containerRect, layoutSize, displayed)).toEqual({
      nx: 0.5,
      ny: 0.5,
    });

    // 画面矩形左上角：布局 (100, 0) → 视觉 (50 + 50, 20)。
    expect(clientPointToDisplayedNormalized(100, 20, containerRect, layoutSize, displayed)).toEqual({
      nx: 0,
      ny: 0,
    });
  });

  it("指针划出容器时钳制在 [0, 1]", () => {
    const layoutSize = { w: 400, h: 300 };
    const containerRect = { left: 0, top: 0, width: 200, height: 150 };
    const displayed = { left: 0, top: 0, width: 400, height: 300 };

    const outside = clientPointToDisplayedNormalized(-80, 999, containerRect, layoutSize, displayed);

    expect(outside.nx).toBe(0);
    expect(outside.ny).toBe(1);
  });

  it("ResizeObserver 尚未量出尺寸时返回 0，不产生 NaN", () => {
    const point = clientPointToDisplayedNormalized(
      120,
      90,
      { left: 0, top: 0, width: 0, height: 0 },
      { w: 0, h: 0 },
      { left: 0, top: 0, width: 0, height: 0 },
    );

    expect(point).toEqual({ nx: 0, ny: 0 });
  });
});